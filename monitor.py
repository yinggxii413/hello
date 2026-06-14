#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X(Twitter) -> 中文翻译 -> Discord  常驻轮询优化版
==================================================
为按量付费的 X API 优化：
  • 用 since_id：每次只取"比上次更新的"新推，没新推=0条读=0花费。
  • 缓存 user_id：账号 ID 永不变，只查一次，省掉重复 user 查询。
  • 常驻 while 循环：每 POLL_INTERVAL 秒轮询一次(默认120s)，准实时。
  • 429 限流自动退避；任何异常都不退出循环(适合 systemd 守护)。

运行模式：
  默认常驻循环；设 ONESHOT=1 则只跑一轮就退出(兼容 GitHub Actions 旧用法)。
  ⚠️ 一旦在 VM 上常驻运行，请关闭/删除 GitHub 的「X to Discord」定时任务，避免重复推送。

必填环境变量：
  X_BEARER_TOKEN        X API Bearer Token
  OPENAI_API_KEY        OpenAI key(翻译用)
各账号的 Discord Webhook(缺则该账号跳过)：
  DISCORD_WEBHOOK            -> Serenity
  TRUMP_WEBHOOK             -> Trump Truth
  FINANCIAL_JUICE_WEBHOOK   -> Financial Juice(当前已停用)
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

X_BEARER_TOKEN = os.environ["X_BEARER_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "120"))
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
ONESHOT = os.environ.get("ONESHOT", "").strip() == "1"
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

X_API = "https://api.x.com/2"
HTTP_TIMEOUT = 30

# 监控账号。新增账号：加一项并为它配一个 webhook 环境变量即可。
ACCOUNTS = [
    {
        "username": "aleabitoreddit",
        "display_name": "Serenity",
        "webhook": os.environ.get("DISCORD_WEBHOOK"),
    },
    {
        "username": "TrumpDailyPosts",
        "display_name": "Trump Truth",
        "webhook": os.environ.get("TRUMP_WEBHOOK"),
    },
    # 已停用(高频号，省钱)。要恢复取消下面注释即可：
    # {
    #     "username": "financialjuice",
    #     "display_name": "Financial Juice",
    #     "webhook": os.environ.get("FINANCIAL_JUICE_WEBHOOK"),
    # },
]

client = OpenAI(api_key=OPENAI_API_KEY)
HEADERS = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}


# ---------------- 状态 ----------------
# 结构: {"user_ids": {username: id}, "since_id": {username: tweet_id}}
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"user_ids": {}, "since_id": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        return {"user_ids": {}, "since_id": {}}
    # 兼容旧格式 {username: newest_id}
    if "since_id" not in s and "user_ids" not in s:
        s = {"user_ids": {}, "since_id": {k: v for k, v in s.items()}}
    s.setdefault("user_ids", {})
    s.setdefault("since_id", {})
    return s


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


# ---------------- X API(带 429 退避) ----------------
def x_get(url, params=None):
    for attempt in range(4):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=HTTP_TIMEOUT)
        except Exception as e:
            print(f"[WARN] X 请求异常: {e}"); time.sleep(3); continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:  # 限流：等到 reset 再试
            reset = r.headers.get("x-rate-limit-reset")
            now = int(time.time())
            wait = max(5, (int(reset) - now) if reset and reset.isdigit() else 30)
            wait = min(wait, 900)  # 最多等15分钟
            print(f"[WARN] X 限流(429)，等待 {wait}s 后重试")
            time.sleep(wait + 1); continue
        print(f"[WARN] X HTTP {r.status_code}: {r.text[:160]}")
        return None
    return None


def get_user_id(username, state):
    """优先用缓存，缓存没有才查一次并写回 state。"""
    cached = state["user_ids"].get(username)
    if cached:
        return cached
    data = x_get(f"{X_API}/users/by/username/{username}")
    if not data or "data" not in data:
        return None
    uid = data["data"]["id"]
    state["user_ids"][username] = uid
    save_state(state)
    return uid


def get_new_posts(user_id, since_id):
    """只取比 since_id 更新的推文(没有则少量取最新用于初始化)。返回(列表, 是否首次)。"""
    params = {"tweet.fields": "created_at"}
    if since_id:
        params["since_id"] = since_id
        params["max_results"] = 100   # 用 since_id 时只返回新推，按返回条数计费
        first = False
    else:
        params["max_results"] = 5     # 首次仅取少量，确定起点，不回灌历史
        first = True
    data = x_get(f"{X_API}/users/{user_id}/tweets", params)
    if not data:
        return None, first
    return data.get("data", []) or [], first


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
    chinese = translate_to_chinese(original)
    payload = {"embeds": [{
        "title": f"📰 {account['display_name']} 新推文",
        "url": tweet_url,
        "description": f"**原文**\n{original[:1500]}\n\n**中文翻译**\n{chinese[:1500]}",
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
        uid = get_user_id(username, state)
        if not uid:
            print(f"[WARN] {username}: 取 user_id 失败"); return
        since = state["since_id"].get(username)
        posts, first = get_new_posts(uid, since)
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
            print(f"[INFO] {username}: 初始化起点 {newest_id}(不回灌历史)")
            return
        # 按时间正序逐条发送(API 返回为最新在前)
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
            print(f"[WARN] {username}: 有发送失败，起点不更新(下轮重试)")
    except Exception as e:
        print(f"[WARN] {username}: 异常 -> {e}")


# ---------------- 主循环 ----------------
def one_pass(state):
    for acc in ACCOUNTS:
        process_account(acc, state)
        time.sleep(2)  # 账号间错峰，减轻限流


def main():
    print(f"[INFO] 启动 | 账号 {len(ACCOUNTS)} 个 | 间隔 {POLL_INTERVAL}s | "
          f"模型 {OPENAI_MODEL} | {'单次' if ONESHOT else '常驻循环'}")
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
