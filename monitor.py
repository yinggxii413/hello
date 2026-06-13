#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财报实际结果 -> 中文数据分析 -> Discord  (v1：仅免费财报硬数据)
================================================================
在 GitHub Actions 上每日多次运行(见 earnings.yml 的 cron)。
检测观察清单内"已经披露财报"的公司，抓实际营收/EPS(vs 预期)+关键指标，
用 OpenAI 生成中文财报数据分析(两段式：重点数据 + 速览卡)，
分段发到 Discord(修复原来 [:1900] 截断问题)。用 earnings_state.json 去重。
本版【不含】电话会与 X 情绪分析，待效果验证后再加。

环境变量(GitHub repo Secrets)：
  FINNHUB_API_KEY   必填  Finnhub API key
  OPENAI_API_KEY    必填  OpenAI API key
  EARNINGS_WEBHOOK  必填  Discord 频道 Webhook URL
可选：
  OPENAI_MODEL      默认 gpt-4o-mini(想更深度可设 gpt-4o)
  MODE              post(默认,盘后实际) | preview(财报前瞻) | both
  REPORTED_LOOKBACK 盘后模式回看天数，默认 3(覆盖周末/补漏)
  PREVIEW_AHEAD     前瞻模式向前看天数，默认 7
"""

import os
import json
import time
import requests
from datetime import date, timedelta
from openai import OpenAI

FINNHUB_API_KEY = os.environ["FINNHUB_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
EARNINGS_WEBHOOK = os.environ["EARNINGS_WEBHOOK"]

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
MODE = os.environ.get("MODE", "post").strip().lower()
REPORTED_LOOKBACK = int(os.environ.get("REPORTED_LOOKBACK", "3"))
PREVIEW_AHEAD = int(os.environ.get("PREVIEW_AHEAD", "7"))

STATE_FILE = "earnings_state.json"
FINNHUB = "https://finnhub.io/api/v1"
HTTP_TIMEOUT = 30

client = OpenAI(api_key=OPENAI_API_KEY)

# ---------------- 观察清单：6 大类(50 只 + 合并你原21只里的额外标的) ----------------
WATCHLIST_GROUPS = {
    "芯片设计·算力·CPU·内存": [
        "NVDA", "AVGO", "MRVL", "ARM", "ALAB", "CRDO", "INTC", "QCOM", "AMD",
        "MU", "SNDK", "WDC", "STX", "000660.KS", "005930.KS",
    ],
    "半导体设备·代工·封测": [
        "ASML", "AMAT", "KLAC", "ONTO", "TSM", "AMKR", "FN",
    ],
    "光通信·功率·化合物半导体": [
        "AAOI", "LITE", "COHR", "SIVEF", "WOLF", "NVTS", "VSH", "AXTI",
    ],
    "大型科技·AI云·数据中心": [
        "AAPL", "META", "GOOG", "GOOGL", "AMZN", "MSFT", "TSLA",
        "CRWV", "NBIS", "IREN", "SMCI", "ANET", "VRT", "ETN",
    ],
    "储能·清洁能源": [
        "FLNC", "EOSE", "GWH", "STEM", "FCEL",
    ],
    "加密·金融科技·航天·其他": [
        "COIN", "HOOD", "CRCL", "IBIT", "RKLB", "SPCX", "ASTS", "NOK", "KORU",
    ],
}
# 反查：ticker -> 板块
CATEGORY_BY_TICKER = {t: cat for cat, lst in WATCHLIST_GROUPS.items() for t in lst}
WATCHLIST = set(CATEGORY_BY_TICKER.keys())


# ---------------- 状态(去重) ----------------
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=0)


# ---------------- Finnhub ----------------
def finnhub_get(path, **params):
    params["token"] = FINNHUB_API_KEY
    try:
        r = requests.get(f"{FINNHUB}{path}", params=params, timeout=HTTP_TIMEOUT)
    except Exception as e:
        print(f"[WARN] Finnhub 异常 {path}: {e}")
        return None
    if r.status_code != 200:
        print(f"[WARN] Finnhub HTTP {r.status_code} {path}: {r.text[:140]}")
        return None
    try:
        return r.json()
    except Exception:
        return None


def fetch_calendar(from_day, to_day):
    data = finnhub_get("/calendar/earnings",
                       **{"from": from_day.isoformat(), "to": to_day.isoformat()})
    if not data:
        return []
    return data.get("earningsCalendar", []) or []


def is_reported(item):
    """财报是否已实际披露：有 epsActual 或 revenueActual。"""
    return item.get("epsActual") is not None or item.get("revenueActual") is not None


def fetch_metrics(symbol):
    data = finnhub_get("/stock/metric", symbol=symbol, metric="all")
    if isinstance(data, dict):
        return data.get("metric", {}) or {}
    return {}


# ---------------- OpenAI ----------------
SYS_POST = """你是专业的美股财报分析师。基于给定的【已披露财报实际数据】和关键指标，
生成一份【中文】的财报数据分析，发到 Discord。严格按以下结构输出 Markdown：

# 📊 {公司}（{代码}）{板块} — 财报实际结果

## 一、财报重点数据
Markdown 表格列出：实际营收、实际 EPS、以及与预期的对比(超预期✅/不及❌，并算出差异%)。
若关键指标里有：毛利率、净利率、营收同比、估值(PE)、市值、52周高低，也择要列出，无则写"暂无数据"。
随后一句"🔑 最该关注的一条"点出本季最关键的数字或意外。

## 二、本期速览卡
紧凑表格：实际 vs 预期(🟢超预期 / 🟡基本符合 / 🔴不及)、一句话点评、需关注的点。

硬性要求：
- 只用提供的数据，**绝不编造任何数字或管理层表态**；缺失就写"暂无数据"。
- 数字带单位($/%/亿)，同比/环比标清。
- 本版不做电话会与情绪分析，只聚焦财报硬数据，简洁、适合手机看。
- 结尾一行小字："本报告为自动生成的信息整理，非投资建议。"
"""

SYS_PREVIEW = """你是专业美股AI产业链财报分析师，语言简洁直接，适合发到 Discord。
基于即将发布财报公司的预期数据，用中文输出前瞻：1)为什么值得关注 2)财报重点看什么
3)对AI产业链影响 4)可能利多 5)可能风险 6)一句话结论。不要编造尚未公布的实际数字。"""


def gen_post_report(item, metrics):
    cat = CATEGORY_BY_TICKER.get(item.get("symbol"), "")
    payload = {
        "symbol": item.get("symbol"),
        "category": cat,
        "date": item.get("date"),
        "quarter": item.get("quarter"),
        "year": item.get("year"),
        "epsActual": item.get("epsActual"),
        "epsEstimate": item.get("epsEstimate"),
        "revenueActual": item.get("revenueActual"),
        "revenueEstimate": item.get("revenueEstimate"),
        "key_metrics": {k: metrics.get(k) for k in (
            "grossMarginTTM", "netProfitMarginTTM", "revenueGrowthTTMYoy",
            "peTTM", "marketCapitalization", "52WeekHigh", "52WeekLow") if k in metrics},
    }
    return _chat(SYS_POST, "以下是该公司已披露财报的实际数据(JSON)，据此生成报告：\n\n"
                 + json.dumps(payload, ensure_ascii=False))


def gen_preview_report(item):
    payload = {k: item.get(k) for k in
               ("symbol", "date", "quarter", "year", "epsEstimate", "revenueEstimate")}
    return _chat(SYS_PREVIEW, "即将发布财报的公司预期数据(JSON)：\n\n"
                 + json.dumps(payload, ensure_ascii=False))


def _chat(system, user):
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.3,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[WARN] OpenAI 失败: {e}")
        return None


# ---------------- Discord(分段发送，修复截断) ----------------
def discord_send(text):
    chunks, buf = [], ""
    for line in text.split("\n"):
        while len(line) > 1900:
            if buf:
                chunks.append(buf); buf = ""
            chunks.append(line[:1900]); line = line[1900:]
        if len(buf) + len(line) + 1 > 1900:
            chunks.append(buf); buf = line
        else:
            buf = (buf + "\n" + line) if buf else line
    if buf:
        chunks.append(buf)

    for ch in chunks:
        for _ in range(3):
            try:
                r = requests.post(EARNINGS_WEBHOOK, json={"content": ch}, timeout=HTTP_TIMEOUT)
            except Exception as e:
                print(f"[WARN] Discord 异常: {e}"); time.sleep(2); continue
            if r.status_code in (200, 204):
                break
            if r.status_code == 429:
                wait = 1.5
                try:
                    wait = float(r.json().get("retry_after", 1.5))
                except Exception:
                    pass
                time.sleep(wait + 0.3); continue
            print(f"[WARN] Discord HTTP {r.status_code}: {r.text[:140]}"); break
        time.sleep(0.8)


# ---------------- 主流程 ----------------
def run_post(state):
    today = date.today()
    frm = today - timedelta(days=REPORTED_LOOKBACK)
    cal = fetch_calendar(frm, today)
    reported = [it for it in cal
                if it.get("symbol") in WATCHLIST and is_reported(it)]
    print(f"[INFO] 盘后模式：窗口 {frm}~{today}，清单内已披露 {len(reported)} 家")

    new_items = []
    for it in reported:
        key = f"post-{it.get('symbol')}-{it.get('year')}Q{it.get('quarter')}"
        if not state.get(key):
            new_items.append((key, it))

    if not new_items:
        print("[INFO] 无新披露财报，跳过。")
        return

    syms = ", ".join("`" + k.split("-")[1] + "`" for k, _ in new_items)
    discord_send(f"🔔 **财报实际结果 · {today.isoformat()}**\n清单内 **{len(new_items)}** 家已披露：{syms}")

    for key, it in new_items:
        sym = it.get("symbol")
        print(f"[INFO] 处理 {sym} ...")
        metrics = fetch_metrics(sym)
        report = gen_post_report(it, metrics)
        if not report:
            discord_send(f"⚠️ `{sym}` 报告生成失败，已跳过。")
            continue
        discord_send(report)
        state[key] = True
        save_state(state)   # 逐个落盘，避免中途失败丢状态
        time.sleep(1)


def run_preview(state):
    today = date.today()
    cal = fetch_calendar(today, today + timedelta(days=PREVIEW_AHEAD))
    upcoming = [it for it in cal if it.get("symbol") in WATCHLIST and not is_reported(it)]
    print(f"[INFO] 前瞻模式：未来{PREVIEW_AHEAD}天清单内 {len(upcoming)} 家将发财报")
    for it in upcoming:
        sym = it.get("symbol")
        key = f"prev-{sym}-{it.get('year')}Q{it.get('quarter')}"
        if state.get(key):
            continue
        report = gen_preview_report(it)
        if not report:
            continue
        head = f"📅 **{sym} 财报前瞻** | {it.get('date')} Q{it.get('quarter')} {it.get('year')}\n"
        discord_send(head + report)
        state[key] = True
        save_state(state)
        time.sleep(1)


def main():
    print(f"[INFO] MODE={MODE} | 模型={OPENAI_MODEL} | 清单{len(WATCHLIST)}只")
    state = load_state()
    if MODE in ("post", "both"):
        run_post(state)
    if MODE in ("preview", "both"):
        run_preview(state)
    save_state(state)
    print("[INFO] 完成。")


if __name__ == "__main__":
    main()
