#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymarket 监控 -> 中文摘要 -> Discord（股票 + 世界杯）
========================================================
用 Polymarket 免费 Gamma API，按话题(tag)拉取热门预测市场，
取成交量最高的 Top N，解析各结果概率 + 成交量，OpenAI 翻译成中文，
按话题分别发到对应 Discord 频道。定时跑(见 polymarket.yml)。

必填环境变量(GitHub Secrets)：
  OPENAI_API_KEY          翻译用(没有则只发英文标题)
  POLY_STOCKS_WEBHOOK     股票频道 Webhook
  POLY_WORLDCUP_WEBHOOK   世界杯频道 Webhook
可选：
  OPENAI_MODEL   默认 gpt-4o-mini
  POLY_TOP_N     每个话题取前几条，默认 10
"""

import os
import json
import time
import datetime as dt
import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
TOP_N = int(os.environ.get("POLY_TOP_N", "10"))

GAMMA = "https://gamma-api.polymarket.com"
HTTP_TIMEOUT = 30

# 话题配置：名称、Polymarket tag、对应频道 webhook、emoji
TOPICS = [
    {"name": "股票", "tag": "stocks", "emoji": "📈",
     "webhook": os.environ.get("POLY_STOCKS_WEBHOOK", "").strip()},
    {"name": "世界杯", "tag": "world-cup", "emoji": "⚽",
     "webhook": os.environ.get("POLY_WORLDCUP_WEBHOOK", "").strip()},
]

client = None
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        print(f"[WARN] OpenAI 初始化失败，将只发英文: {e}")


# ---------------- Polymarket ----------------
def fetch_events(tag, limit):
    url = (f"{GAMMA}/events?closed=false&active=true&archived=false"
           f"&order=volume24hr&ascending=false&limit={limit}&tag_slug={tag}")
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            print(f"[WARN] Gamma HTTP {r.status_code} ({tag})")
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[WARN] Gamma 请求异常 ({tag}): {e}")
        return []


def _loads(s, default):
    try:
        return json.loads(s) if isinstance(s, str) else (s or default)
    except Exception:
        return default


def event_outcomes(ev, top=3):
    """返回 [(标签, 概率0-1)]，按概率降序，最多 top 个。兼容多市场/二元市场。"""
    markets = ev.get("markets") or []
    rows = []
    if len(markets) > 1:
        # 多市场事件：每个 market 是一个候选(如各队/各价格档)，概率取其 "Yes" 价
        for m in markets:
            label = m.get("groupItemTitle") or m.get("question") or "?"
            outs = _loads(m.get("outcomes"), [])
            prices = _loads(m.get("outcomePrices"), [])
            prob = None
            if outs and prices and len(outs) == len(prices):
                if "Yes" in outs:
                    prob = float(prices[outs.index("Yes")])
                else:
                    prob = max(float(p) for p in prices)
            elif prices:
                prob = float(prices[0])
            if prob is not None:
                rows.append((label, prob))
    else:
        # 单一市场：每个 outcome 是一行
        m = markets[0] if markets else ev
        outs = _loads(m.get("outcomes"), [])
        prices = _loads(m.get("outcomePrices"), [])
        for o, p in zip(outs, prices):
            try:
                rows.append((o, float(p)))
            except Exception:
                pass
        # 二元 Yes/No 市场：把 Yes(是) 放前面，更直观
        labels = {r[0] for r in rows}
        if labels == {"Yes", "No"}:
            rows.sort(key=lambda x: 0 if x[0] == "Yes" else 1)
            return rows
    rows.sort(key=lambda x: -x[1])
    return rows[:top]


def zh_label(label):
    return {"Yes": "是", "No": "否"}.get(label, label)


def event_volume(ev):
    for k in ("volume24hr", "volume"):
        v = ev.get(k)
        if v:
            try:
                return float(v)
            except Exception:
                pass
    return 0.0


def fmt_money(v):
    v = float(v)
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    if v >= 1e3:
        return f"${v/1e3:.1f}K"
    return f"${v:.0f}"


# ---------------- 翻译 ----------------
def translate_titles(titles):
    """把英文标题列表批量翻译成中文，返回等长列表。失败则原样返回。"""
    if not client or not titles:
        return titles
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content":
                 "把下列预测市场标题翻译成简洁自然的中文，保留公司/球队/人名和股票代码。"
                 "严格按 '序号. 译文' 每行一条原样返回，不要加任何解释。"},
                {"role": "user", "content": numbered},
            ],
        )
        out = resp.choices[0].message.content.strip().splitlines()
        zh = {}
        for line in out:
            line = line.strip()
            if "." in line:
                num, _, txt = line.partition(".")
                if num.strip().isdigit():
                    zh[int(num.strip())] = txt.strip()
        return [zh.get(i + 1, titles[i]) for i in range(len(titles))]
    except Exception as e:
        print(f"[WARN] 翻译失败，用英文: {e}")
        return titles


# ---------------- Discord ----------------
def discord_send(webhook, text):
    if not webhook:
        return
    chunks, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > 1900:
            chunks.append(buf)
            buf = line
        else:
            buf = (buf + "\n" + line) if buf else line
    if buf:
        chunks.append(buf)
    for ch in chunks:
        for _ in range(3):
            try:
                r = requests.post(webhook, json={"content": ch}, timeout=HTTP_TIMEOUT)
            except Exception as e:
                print(f"[WARN] Discord 异常: {e}"); time.sleep(2); continue
            if r.status_code in (200, 204):
                break
            if r.status_code == 429:
                try:
                    time.sleep(float(r.json().get("retry_after", 1.5)) + 0.3)
                except Exception:
                    time.sleep(2)
                continue
            print(f"[WARN] Discord HTTP {r.status_code}: {r.text[:140]}"); break
        time.sleep(0.8)


# ---------------- 主流程 ----------------
def run_topic(topic):
    name, tag, webhook, emoji = topic["name"], topic["tag"], topic["webhook"], topic["emoji"]
    if not webhook:
        print(f"[WARN] 话题「{name}」未配置 webhook，跳过")
        return
    events = fetch_events(tag, max(TOP_N * 2, 20))
    # 过滤掉没有有效结果的，按 24h 成交量取前 TOP_N
    events = [e for e in events if event_outcomes(e)]
    events = sorted(events, key=event_volume, reverse=True)[:TOP_N]
    if not events:
        print(f"[INFO] 话题「{name}」无可用市场")
        return
    print(f"[INFO] 话题「{name}」→ {len(events)} 条")

    titles_zh = translate_titles([e.get("title") or "?" for e in events])
    now = dt.datetime.utcnow() + dt.timedelta(hours=8)  # 北京时间
    lines = [f"{emoji} **Polymarket · {name}** · 北京时间 {now.strftime('%Y/%m/%d %H:%M')}",
             "_按 24 小时成交量排序_\n"]
    for i, ev in enumerate(events):
        lines.append(f"**{i+1}. {titles_zh[i]}**")
        for label, prob in event_outcomes(ev):
            lines.append(f"　{zh_label(label)}：**{prob*100:.0f}%**")
        url = f"https://polymarket.com/event/{ev.get('slug','')}"
        lines.append(f"　成交量 {fmt_money(event_volume(ev))}　🔗 <{url}>\n")
    discord_send(webhook, "\n".join(lines))


def main():
    configured = [t["name"] for t in TOPICS if t["webhook"]]
    print(f"[INFO] Polymarket 监控 | TOP_N={TOP_N} | 已配置话题 {configured}")
    for topic in TOPICS:
        run_topic(topic)
        time.sleep(1)
    print("[INFO] 完成。")


if __name__ == "__main__":
    main()
