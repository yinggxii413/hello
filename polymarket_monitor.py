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
import re
import json
import time
import datetime as dt
import requests

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
TOP_N = int(os.environ.get("POLY_TOP_N", "10"))

GAMMA = "https://gamma-api.polymarket.com"
HTTP_TIMEOUT = 30

# 话题配置：名称、tag、webhook、emoji、卡片颜色、是否只保留 watchlist 相关
TOPICS = [
    {"name": "股票", "tag": "stocks", "emoji": "📈", "color": 0x2ECC71, "watchlist_only": True,
     "webhook": os.environ.get("POLY_STOCKS_WEBHOOK", "").strip()},
    {"name": "世界杯", "tag": "world-cup", "emoji": "⚽", "color": 0x3498DB, "watchlist_only": False,
     "webhook": os.environ.get("POLY_WORLDCUP_WEBHOOK", "").strip()},
    {"name": "特朗普", "tag": "trump", "emoji": "🇺🇸", "color": 0xE74C3C, "watchlist_only": False,
     "webhook": os.environ.get("POLY_TRUMP_WEBHOOK", "").strip()},
]

# ---- 股票频道：只保留涉及 watchlist 的市场 ----
# 代码(区分大小写整词匹配) + 主要公司名(忽略大小写)
WATCHLIST_TICKERS = {
    "AMAT", "ONTO", "KLAC", "CAMT", "FORM", "AMKR", "AEHR", "ASML", "MU", "SNDK", "STX", "WDC",
    "TSM", "AVGO", "AMD", "ARM", "NOK", "INTC", "MRVL", "QCOM", "ALAB", "VSH", "POET", "COHR",
    "CRDO", "AAOI", "AXTI", "LITE", "SIVEF", "FN", "IREN", "NBIS", "HUT", "ORCL", "WULF", "NVTS",
    "CRWV", "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA", "SATS", "RKLB",
    "ASTS", "LUNR", "PATH", "SPCE", "SPCX", "CEG", "ENPH", "FLNC", "CSIQ", "EOSE",
    "IONQ", "QUBT", "QBTS", "LAES",
}
WATCHLIST_NAMES = [
    "Nvidia", "Tesla", "Apple", "Microsoft", "Amazon", "Meta", "Google", "Alphabet", "Broadcom",
    "Micron", "Intel", "Qualcomm", "Taiwan Semiconductor", "TSMC", "Oracle", "CoreWeave",
    "Arm Holdings", "AMD", "Marvell", "Applied Materials", "ASML", "Rocket Lab", "IonQ",
]


def title_matches_watchlist(title):
    if not title:
        return False
    for tk in WATCHLIST_TICKERS:  # 代码：区分大小写、整词
        if re.search(rf'(?<![A-Za-z0-9]){re.escape(tk)}(?![A-Za-z0-9])', title):
            return True
    low = title.lower()
    for nm in WATCHLIST_NAMES:    # 公司名：忽略大小写
        if nm.lower() in low:
            return True
    return False


# ---- 世界杯：国家 -> 国旗 emoji ----
COUNTRY_FLAG = {
    "France": "🇫🇷", "Spain": "🇪🇸", "Brazil": "🇧🇷", "Argentina": "🇦🇷", "Germany": "🇩🇪",
    "Portugal": "🇵🇹", "Netherlands": "🇳🇱", "Italy": "🇮🇹", "Belgium": "🇧🇪", "Croatia": "🇭🇷",
    "Mexico": "🇲🇽", "USA": "🇺🇸", "United States": "🇺🇸", "Canada": "🇨🇦", "Uruguay": "🇺🇾",
    "Colombia": "🇨🇴", "Morocco": "🇲🇦", "Japan": "🇯🇵", "South Korea": "🇰🇷", "Korea": "🇰🇷",
    "Senegal": "🇸🇳", "Norway": "🇳🇴", "Saudi Arabia": "🇸🇦", "Czechia": "🇨🇿",
    "Czech Republic": "🇨🇿", "Switzerland": "🇨🇭", "Denmark": "🇩🇰", "Poland": "🇵🇱",
    "Austria": "🇦🇹", "Ukraine": "🇺🇦", "Turkey": "🇹🇷", "Serbia": "🇷🇸", "Ecuador": "🇪🇨",
    "Peru": "🇵🇪", "Chile": "🇨🇱", "Australia": "🇦🇺", "Iran": "🇮🇷", "Nigeria": "🇳🇬",
    "Ghana": "🇬🇭", "Egypt": "🇪🇬", "Ivory Coast": "🇨🇮", "Cameroon": "🇨🇲", "Algeria": "🇩🇿",
    "Tunisia": "🇹🇳", "Qatar": "🇶🇦", "Greece": "🇬🇷", "Sweden": "🇸🇪", "Hungary": "🇭🇺",
    "Romania": "🇷🇴", "Ireland": "🇮🇪", "Costa Rica": "🇨🇷", "Panama": "🇵🇦", "Jamaica": "🇯🇲",
    "Paraguay": "🇵🇾", "Venezuela": "🇻🇪", "Bolivia": "🇧🇴", "New Zealand": "🇳🇿",
    "South Africa": "🇿🇦", "Bosnia and Herzegovina": "🇧🇦", "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "Scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Wales": "🏴󠁧󠁢󠁷󠁬󠁳󠁿",
}

RANK_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def outcome_emoji(label):
    if label in COUNTRY_FLAG:
        return COUNTRY_FLAG[label]
    if label in ("Yes", "是"):
        return "✅"
    if label in ("No", "否"):
        return "❌"
    return "▸"

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


# ---------------- Discord(embed 卡片) ----------------
def discord_send_embed(webhook, title, description, color, footer):
    if not webhook:
        return
    # embed 描述上限 4096，超长按行切成多张卡片
    chunks, buf = [], ""
    for line in description.split("\n"):
        if len(buf) + len(line) + 1 > 3900:
            chunks.append(buf); buf = line
        else:
            buf = (buf + "\n" + line) if buf else line
    if buf:
        chunks.append(buf)
    for i, ch in enumerate(chunks):
        embed = {"title": title if i == 0 else f"{title}（续 {i+1}）",
                 "description": ch, "color": color}
        if i == len(chunks) - 1 and footer:
            embed["footer"] = {"text": footer}
        for _ in range(3):
            try:
                r = requests.post(webhook, json={"embeds": [embed]}, timeout=HTTP_TIMEOUT)
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
    name, tag, webhook = topic["name"], topic["tag"], topic["webhook"]
    emoji, color, wl_only = topic["emoji"], topic["color"], topic.get("watchlist_only", False)
    if not webhook:
        print(f"[WARN] 话题「{name}」未配置 webhook，跳过")
        return
    # 要做 watchlist 过滤时多抓一些，保证过滤后仍有 Top N
    raw = fetch_events(tag, 100 if wl_only else max(TOP_N * 2, 20))
    events = [e for e in raw if event_outcomes(e)]
    if wl_only:
        events = [e for e in events if title_matches_watchlist(e.get("title"))]
    events = sorted(events, key=event_volume, reverse=True)[:TOP_N]
    if not events:
        print(f"[INFO] 话题「{name}」无可用市场（watchlist 过滤后可能为空）")
        return
    print(f"[INFO] 话题「{name}」→ {len(events)} 条")

    titles_zh = translate_titles([e.get("title") or "?" for e in events])
    body = []
    for i, ev in enumerate(events):
        rank = RANK_EMOJI[i] if i < len(RANK_EMOJI) else f"{i+1}."
        body.append(f"**{rank} {titles_zh[i]}**")
        parts = []
        for l, p in event_outcomes(ev):
            flag = COUNTRY_FLAG.get(l, "")   # 仅世界杯国家有旗，其余不加 emoji
            lab = (flag + " " if flag else "") + zh_label(l)
            parts.append(f"{lab} **{p*100:.0f}%**")
        body.append("　".join(parts))
        body.append(f"成交量 {fmt_money(event_volume(ev))}")
        url = f"https://polymarket.com/event/{ev.get('slug', '')}"
        body.append(f"[查看市场]({url})\n")

    now = dt.datetime.utcnow() + dt.timedelta(hours=8)  # 北京时间
    title = f"{emoji} Polymarket · {name}"
    footer = f"北京 {now.strftime('%Y/%m/%d %H:%M')} · 按 24h 成交量 · Top {len(events)}"
    discord_send_embed(webhook, title, "\n".join(body), color, footer)


def main():
    configured = [t["name"] for t in TOPICS if t["webhook"]]
    print(f"[INFO] Polymarket 监控 | TOP_N={TOP_N} | 已配置话题 {configured}")
    for topic in TOPICS:
        run_topic(topic)
        time.sleep(1)
    print("[INFO] 完成。")


if __name__ == "__main__":
    main()
