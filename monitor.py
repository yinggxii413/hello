#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X(Twitter) -> 中文翻译 -> Discord  常驻轮询版（TwitterAPI.io 数据源）
=====================================================================
抓取层使用 TwitterAPI.io（第三方），替代 X 官方 API。
翻译 / 去重 / Discord 推送逻辑与官方 API 版完全一致。

去重：state.json 存每账号最后处理的推文 ID（since_id）。
  TwitterAPI.io 的 advanced_search 不支持服务端 since_id 参数，
  因此改为客户端按 tweet_id 过滤（X 推文 ID 单调递增，可直接比较）。
  注意：tweet_id 与官方 API 完全相同，所以旧 state.json 可无缝继承。

运行模式：
  默认常驻循环；设 ONESHOT=1 则只跑一轮就退出（GitHub Actions 用法）。

必填环境变量：
  TWITTERAPI_KEY    TwitterAPI.io 的 API Key（X-API-Key 头）
  OPENAI_API_KEY    OpenAI key（翻译用）
各账号的 Discord Webhook（缺则该账号跳过）：
  DISCORD_WEBHOOK            -> Serenity
  TRUMP_WEBHOOK             -> Trump Truth
  FINANCIAL_JUICE_WEBHOOK   -> Financial Juice（当前已停用）
可选：
  POLL_INTERVAL   轮询间隔秒，默认 120
  OPENAI_MODEL    翻译模型，默认 gpt-4o-mini
  ONESHOT         =1 时只跑一轮
  STATE_FILE      状态文件路径，默认 state.json
"""

import os
import json
import time
import requests
from openai import OpenAI

TWITTERAPI_KEY = os.environ["TWITTERAPI_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "120"))
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
ONESHOT = os.environ.get("ONESHOT", "").strip() == "1"
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

TWITTERAPI_BASE = "https://api.twitterapi.io"
HTTP_TIMEOUT = 30
MAX_PAGES = 10  # 增量轮询时最多翻页数（防某账号一轮发太多导致失控）

# 监控账号。新增账号：加一项并为它配一个 webhook 环境变量即可。
ACCOUNTS = [
    # 已停用(换成华尔街日报)。要恢复取消下面注释即可：
    # {
    #     "username": "aleabitoreddit",
    #     "display_name": "Serenity",
    #     "webhook": os.environ.get("DISCORD_WEBHOOK"),
    #     "translate": True,
    # },
    {
        "username": "TrumpDailyPosts",
        "display_name": "Trump Truth",
        "webhook": os.environ.get("TRUMP_WEBHOOK"),
        "translate": True,
    },
    {
        "username": "ChineseWSJ",
        "display_name": "华尔街日报",
        "webhook": os.environ.get("WSJ_WEBHOOK"),
        "translate": False,   # 本身是中文，直接发原文，不翻译、不花翻译钱
    },
    # 已停用(高频号，省钱)。要恢复取消下面注释即可：
    # {
    #     "username": "financialjuice",
    #     "display_name": "Financial Juice",
    #     "webhook": os.environ.get("FINANCIAL_JUICE_WEBHOOK"),
    #     "translate": True,
    # },
]

client = OpenAI(api_key=OPENAI_API_KEY)
HEADERS = {"X-API-Key": TWITTERAPI_KEY}


# ---------------- 状态 ----------------
# 结构: {"since_id": {username: tweet_id}}
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"since_id": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        return {"since_id": {}}
    # 兼容官方 API 版的旧格式 {"user_ids":..., "since_id":...}
    if isinstance(s, dict) and "since_id" in s:
        return {"since_id": dict(s.get("since_id", {}))}
    # 兼容最早的格式 {username: newest_id}
    if isinstance(s, dict):
        return {"since_id": {k: v for k, v in s.items() if isinstance(v, str)}}
    return {"since_id": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


# ---------------- TwitterAPI.io（带 429 退避） ----------------
def http_get(url, params=None):
    for attempt in range(4):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=HTTP_TIMEOUT)
        except Exception as e:
            print(f"[WARN] 请求异常: {e}"); time.sleep(3); continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:  # TwitterAPI.io 几乎不限流，遇到就短暂退避
            print("[WARN] 限流(429)，等待 10s 后重试")
            time.sleep(10); continue
        print(f"[WARN] HTTP {r.status_code}: {r.text[:160]}")
        return None
    return None


def get_new_posts(username, since_id):
    """用 advanced_search 取该账号推文。
    有 since_id：翻页累积所有 id > since_id 的新推（遇到旧推即停）。
    无 since_id：只取第一页用于确定起点，不回灌历史。
    返回 (列表[最新在前], 是否首次)。失败返回 (None, first)。
    """
    first = since_id is None
    collected = []
    cursor = None
    pages = 1 if first else MAX_PAGES
    for _ in range(pages):
        params = {"query": f"from:{username}", "queryType": "Latest"}
        if cursor:
            params["cursor"] = cursor
        data = http_get(f"{TWITTERAPI_BASE}/twitter/tweet/advanced_search", params)
        if data is None:
            return (collected or None), first
        page = data.get("tweets", []) or []
        if not page:
            break
        if first:
            collected = page
            break
        # 增量：保留比 since_id 新的；遇到 <= since_id 即停止翻页
        stop = False
        for t in page:
            try:
                newer = int(t["id"]) > int(since_id)
            except (ValueError, KeyError):
                newer = False
            if newer:
                collected.append(t)
            else:
                stop = True
                break
        if stop or not data.get("has_next_page"):
            break
        cursor = data.get("next_cursor")
    # 统一保证最新在前（不依赖 API 返回顺序）
    collected.sort(key=lambda t: int(t["id"]), reverse=True)
    return collected, first


# ---------------- 翻译 ----------------
def translate_to_chinese(text):
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system",
                 "content": "你是专业财经翻译助手。请翻译成自然流畅中文，保留股票代码、人名、公司名。"},
                {"role": "user", "content": text},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[WARN] 翻译失败: {e}")
        return "（翻译失败，仅显示原文）"


# ---------------- Discord ----------------
def send_to_discord(account, tweet):
    webhook = account["webhook"]
    if not webhook:
        print(f"[WARN] {account['username']} 缺 webhook，跳过")
        return False
    tweet_url = f"https://x.com/{account['username']}/status/{tweet['id']}"
    original = tweet["text"]

    # translate 默认 True；设为 False 的账号(本身是中文)直接发原文，不翻译
    if account.get("translate", True):
        chinese = translate_to_chinese(original)
        description = f"**原文**\n{original[:1500]}\n\n**中文翻译**\n{chinese[:1500]}"
    else:
        description = original[:3500]

    payload = {"embeds": [{
        "title": f"📰 {account['display_name']} 新推文",
        "url": tweet_url,
        "description": description,
        "color": 3447003,
        "footer": {"text": f"来源：@{account['username']}"},
    }]}
    for _ in range(3):
        try:
            r = requests.post(webhook, json=payload, timeout=HTTP_TIMEOUT)
        except Exception as e:
            print(f"[WARN] Discord 异常: {e}"); time.sleep(2); continue
        if r.status_code in (200, 204):
            return True
        if r.status_code == 429:
            try:
                time.sleep(float(r.json().get("retry_after", 1.5)) + 0.3)
            except Exception:
                time.sleep(2)
            continue
        print(f"[WARN] Discord HTTP {r.status_code}: {r.text[:140]}")
        return False
    return False


# ---------------- 单账号处理 ----------------
def process_account(account, state):
    username = account["username"]
    try:
        since = state["since_id"].get(username)
        posts, first = get_new_posts(username, since)
        if posts is None:
            print(f"[WARN] {username}: 取推文失败"); return
        if not posts:
            print(f"[INFO] {username}: 无新推")
            return
        newest_id = posts[0]["id"]
        if first:
            # 首次：只记起点，不回灌历史，避免刷屏
            state["since_id"][username] = newest_id
            save_state(state)
            print(f"[INFO] {username}: 初始化起点 {newest_id}（不回灌历史）")
            return
        # 按时间正序逐条发送（get_new_posts 已保证最新在前）
        sent_ok = True
        for post in reversed(posts):
            if not send_to_discord(account, post):
                sent_ok = False
                break
        if sent_ok:
            state["since_id"][username] = newest_id
            save_state(state)
            print(f"[INFO] {username}: 推送 {len(posts)} 条，起点更新到 {newest_id}")
        else:
            print(f"[WARN] {username}: 有发送失败，起点不更新（下轮重试）")
    except Exception as e:
        print(f"[WARN] {username}: 异常 -> {e}")


# ---------------- 主循环 ----------------
def one_pass(state):
    for acc in ACCOUNTS:
        process_account(acc, state)
        time.sleep(2)  # 账号间错峰


def main():
    print(f"[INFO] 启动 | 账号 {len(ACCOUNTS)} 个 | 间隔 {POLL_INTERVAL}s | "
          f"模型 {OPENAI_MODEL} | 数据源 TwitterAPI.io | {'单次' if ONESHOT else '常驻循环'}")
    state = load_state()
    if ONESHOT:
        one_pass(state)
        print("[INFO] 单次完成。")
        return
    while True:
        try:
            one_pass(state)
        except Exception as e:
            print(f"[WARN] 本轮异常(已忽略，继续): {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
