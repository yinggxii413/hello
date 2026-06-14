#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财报实际结果 -> 中文数据分析 -> 按板块分发到 9 个 Discord 频道  (v2)
====================================================================
在 GitHub Actions 上每日多次运行(见 earnings.yml 的 cron)。
检测观察清单(9 类 71 只)内"已经披露财报"的公司，抓实际营收/EPS(vs 预期)+关键指标，
用 OpenAI 生成中文财报数据分析(Discord 友好排版，不用 Markdown 表格)，
**按公司所属板块，分发到对应板块的 Discord 频道**。用 earnings_state.json 去重。
本版只做财报硬数据；电话会与市场/X 分析待后续加。

必填环境变量(GitHub repo Secrets)：
  FINNHUB_API_KEY
  OPENAI_API_KEY
  9 个频道 Webhook(缺哪个，该板块就跳过并告警，不影响其他)：
    WEBHOOK_EQUIP      封装设备
    WEBHOOK_STORAGE    存储
    WEBHOOK_COMPUTE    算力芯片
    WEBHOOK_OPTICAL    光模块
    WEBHOOK_NEOCLOUD   Neocloud
    WEBHOOK_MAG7       Mag 7
    WEBHOOK_SPACE      航天机器人
    WEBHOOK_ENERGY     储能
    WEBHOOK_QUANTUM    量子
可选：
  OPENAI_MODEL      默认 gpt-4o-mini
  MODE              post(默认) | preview | both
  REPORTED_LOOKBACK 盘后回看天数，默认 3(测试可临时设 30)
  PREVIEW_AHEAD     前瞻向前看天数，默认 7
"""

import os
import json
import time
import requests
from datetime import date, timedelta
from openai import OpenAI

FINNHUB_API_KEY = os.environ["FINNHUB_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
MODE = os.environ.get("MODE", "post").strip().lower()
REPORTED_LOOKBACK = int(os.environ.get("REPORTED_LOOKBACK", "3"))
PREVIEW_AHEAD = int(os.environ.get("PREVIEW_AHEAD", "7"))

STATE_FILE = "earnings_state.json"
FINNHUB = "https://finnhub.io/api/v1"
HTTP_TIMEOUT = 30

client = OpenAI(api_key=OPENAI_API_KEY)

# ---------------- 9 大类(产业链上游->下游) ----------------
# 每类: 显示名、对应频道的 webhook 环境变量名、成分股
CATEGORIES = [
    {"name": "封装设备", "env": "WEBHOOK_EQUIP",
     "tickers": ["AMAT", "ONTO", "KLAC", "CAMT", "FORM", "AMKR", "AEHR", "ASML"]},
    {"name": "存储", "env": "WEBHOOK_STORAGE",
     "tickers": ["MU", "SNDK", "KIOXIA", "STX", "WDC", "005930.KS", "000660.KS"]},
    {"name": "算力芯片", "env": "WEBHOOK_COMPUTE",
     "tickers": ["TSM", "AVGO", "AMD", "ARM", "NOK", "INTC", "MRVL", "QCOM", "ALAB", "VSH"]},
    {"name": "光模块", "env": "WEBHOOK_OPTICAL",
     "tickers": ["POET", "COHR", "CRDO", "AAOI", "AXTI", "LITE", "FOTO", "SIVEF", "FN"]},
    {"name": "Neocloud", "env": "WEBHOOK_NEOCLOUD",
     "tickers": ["IREN", "NBIS", "HUT", "ORCL", "WULF", "NVTS", "CRWV"]},
    {"name": "Mag 7", "env": "WEBHOOK_MAG7",
     "tickers": ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"]},
    {"name": "航天机器人", "env": "WEBHOOK_SPACE",
     "tickers": ["DXYZ", "SATS", "RKLB", "ASTS", "FLY", "LUNR", "PATH", "SIDE", "RR", "SPCE", "SPCX"]},
    {"name": "储能", "env": "WEBHOOK_ENERGY",
     "tickers": ["BE", "CEG", "ENPH", "NEE", "FLNC", "CSIQ", "EOSE"]},
    {"name": "量子", "env": "WEBHOOK_QUANTUM",
     "tickers": ["IONQ", "QUBT", "QBTS", "LAES", "QTUM"]},
]

CATEGORY_BY_TICKER = {t: c["name"] for c in CATEGORIES for t in c["tickers"]}
ENV_BY_CATEGORY = {c["name"]: c["env"] for c in CATEGORIES}
WATCHLIST = set(CATEGORY_BY_TICKER.keys())
# 启动时读取各频道 webhook(缺失为空字符串)
WEBHOOK_BY_CATEGORY = {c["name"]: os.environ.get(c["env"], "").strip() for c in CATEGORIES}


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
    return item.get("epsActual") is not None or item.get("revenueActual") is not None


def fetch_metrics(symbol):
    data = finnhub_get("/stock/metric", symbol=symbol, metric="all")
    if isinstance(data, dict):
        return data.get("metric", {}) or {}
    return {}


def fetch_company_name(symbol):
    """取真实公司名，避免模型从代码瞎猜公司名。取不到则返回代码本身。"""
    data = finnhub_get("/stock/profile2", symbol=symbol)
    if isinstance(data, dict) and data.get("name"):
        return data["name"]
    return symbol


def fetch_peers_pe(symbol, limit=4):
    """取同业公司的 PE，用于同业估值对比(免费)。返回 [{'ticker','peTTM'}...]"""
    peers = finnhub_get("/stock/peers", symbol=symbol)
    if not isinstance(peers, list):
        return []
    out = []
    for p in peers:
        if p == symbol or len(out) >= limit:
            continue
        m = fetch_metrics(p)
        pe = m.get("peTTM")
        if isinstance(pe, (int, float)):
            out.append({"ticker": p, "peTTM": round(pe, 1)})
    return out


# ---------------- OpenAI ----------------
SYS_POST = """你是专业的美股财报分析师。基于给定的【已披露财报实际数据】和关键指标，
生成一份【中文】的财报数据分析，发到 Discord。

⚠️ 极重要：Discord 不支持 Markdown 表格！**绝对不要用 | 和 --- 画表格**。
一律用"加粗标签：数值"的逐行排版。严格按下面这个格式输出(示例排版，数字用真实数据替换)：

# 📊 {公司中文名}（{代码}）· {板块}
**财报实际结果**

**一、财报重点数据**
📈 **实际营收**：$X亿（预期 $Y亿 → 超预期✅ +N% / 不及❌ -N%）
💰 **实际 EPS**：$X（预期 $Y → 超预期✅ +N% / 不及❌ -N%）
📊 **同比**：营收同比 X% ｜ EPS同比 X%（用 revenueGrowthQuarterlyYoy / epsGrowthQuarterlyYoy，没有就用 TTM 口径并标注）

**二、盈利与估值**
**毛利率**：X% ｜ **营业利润率**：X% ｜ **净利率**：X%
**PE**：X ｜ **PS**：X ｜ **PB**：X ｜ **ROE**：X%
**市值**：$X亿 ｜ **52周区间**：$低 ~ $高

**三、同业估值对比**
本司 PE X ｜ vs 同业：AAA X、BBB X、CCC X（用 peers_pe；据此点评本司估值偏高/偏低/合理）

🔑 **最该关注**：一句话点出本季最关键的数字或意外。

**四、速览**
营收 🟢超预期/🟡符合/🔴不及 — 一句话点评
EPS 🟢超预期/🟡符合/🔴不及 — 一句话点评
估值 🟢偏低/🟡合理/🔴偏高（相对同业）
⚠️ **关注**：需留意的点 / 风险

*本报告为自动生成的信息整理，非投资建议。*

硬性要求：
- **公司名必须用提供的 company_name 字段**（可译成中文，并在括号里保留英文原名），
  **严禁自己从股票代码猜测或编造公司名**。例如代码 PATH 对应 UiPath，不要写成别的公司。
- **绝不用 Markdown 表格(| 和 ---)**；只用上面的逐行加粗格式。
- 只用提供的数据，**绝不编造任何数字或管理层表态**；某项数据缺失就**整行不写**(不要写"暂无数据"占行)。
- 数字带单位($/%/亿)，差异%自己算清。
- 本版只聚焦财报硬数据，简洁、适合手机看。"""

SYS_PREVIEW = """你是专业美股AI产业链财报分析师，语言简洁直接，适合发到 Discord。
不要用 Markdown 表格。基于即将发布财报公司的预期数据，用中文输出前瞻：
1)为什么值得关注 2)财报重点看什么 3)对AI产业链影响 4)可能利多 5)可能风险 6)一句话结论。
不要编造尚未公布的实际数字。"""


def gen_post_report(item, metrics, company_name, peers_pe=None):
    cat = CATEGORY_BY_TICKER.get(item.get("symbol"), "")
    payload = {
        "symbol": item.get("symbol"),
        "company_name": company_name,
        "category": cat,
        "date": item.get("date"),
        "quarter": item.get("quarter"),
        "year": item.get("year"),
        "epsActual": item.get("epsActual"),
        "epsEstimate": item.get("epsEstimate"),
        "revenueActual": item.get("revenueActual"),
        "revenueEstimate": item.get("revenueEstimate"),
        "key_metrics": {k: metrics.get(k) for k in (
            # 利润率
            "grossMarginTTM", "operatingMarginTTM", "netProfitMarginTTM",
            # 同比增长(YoY)
            "revenueGrowthTTMYoy", "epsGrowthTTMYoy",
            "revenueGrowthQuarterlyYoy", "epsGrowthQuarterlyYoy",
            # 估值
            "peTTM", "psTTM", "pbTTM", "roeTTM",
            # 规模/区间
            "marketCapitalization", "52WeekHigh", "52WeekLow",
            "dividendYieldIndicatedAnnual") if k in metrics},
        "peers_pe": peers_pe or [],   # 同业 PE 对比
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


# ---------------- Discord(指定频道，分段发送) ----------------
def discord_send(webhook, text):
    if not webhook:
        return False
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

    ok = True
    for ch in chunks:
        sent = False
        for _ in range(3):
            try:
                r = requests.post(webhook, json={"content": ch}, timeout=HTTP_TIMEOUT)
            except Exception as e:
                print(f"[WARN] Discord 异常: {e}"); time.sleep(2); continue
            if r.status_code in (200, 204):
                sent = True; break
            if r.status_code == 429:
                wait = 1.5
                try:
                    wait = float(r.json().get("retry_after", 1.5))
                except Exception:
                    pass
                time.sleep(wait + 0.3); continue
            print(f"[WARN] Discord HTTP {r.status_code}: {r.text[:140]}"); break
        ok = ok and sent
        time.sleep(0.8)
    return ok


# ---------------- 主流程 ----------------
def run_post(state):
    today = date.today()
    frm = today - timedelta(days=REPORTED_LOOKBACK)
    cal = fetch_calendar(frm, today)
    reported = [it for it in cal if it.get("symbol") in WATCHLIST and is_reported(it)]
    print(f"[INFO] 盘后模式：窗口 {frm}~{today}，清单内已披露 {len(reported)} 家")

    # 按板块分组新增项
    by_cat = {}
    for it in reported:
        key = f"post-{it.get('symbol')}-{it.get('year')}Q{it.get('quarter')}"
        if state.get(key):
            continue
        cat = CATEGORY_BY_TICKER.get(it.get("symbol"))
        by_cat.setdefault(cat, []).append((key, it))

    if not by_cat:
        print("[INFO] 无新披露财报，跳过。")
        return

    for cat, items in by_cat.items():
        webhook = WEBHOOK_BY_CATEGORY.get(cat, "")
        syms = [it.get("symbol") for _, it in items]
        if not webhook:
            print(f"[WARN] 板块「{cat}」未配置 webhook({ENV_BY_CATEGORY.get(cat)})，跳过 {syms}(state 不记，待配置后补发)")
            continue
        print(f"[INFO] 板块「{cat}」→ {len(items)} 家：{syms}")
        discord_send(webhook, f"🔔 **{cat} · 财报实际结果 · {today.isoformat()}**\n"
                              f"本板块 **{len(items)}** 家已披露：{', '.join('`'+s+'`' for s in syms)}")
        for key, it in items:
            sym = it.get("symbol")
            metrics = fetch_metrics(sym)
            name = fetch_company_name(sym)
            peers_pe = fetch_peers_pe(sym)
            report = gen_post_report(it, metrics, name, peers_pe)
            if not report:
                discord_send(webhook, f"⚠️ `{sym}` 报告生成失败，已跳过。")
                continue
            discord_send(webhook, report)
            state[key] = True
            save_state(state)
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
        cat = CATEGORY_BY_TICKER.get(sym)
        webhook = WEBHOOK_BY_CATEGORY.get(cat, "")
        if not webhook:
            print(f"[WARN] 板块「{cat}」未配置 webhook，跳过前瞻 {sym}")
            continue
        report = gen_preview_report(it)
        if not report:
            continue
        head = f"📅 **{sym} 财报前瞻** | {it.get('date')} Q{it.get('quarter')} {it.get('year')}\n"
        discord_send(webhook, head + report)
        state[key] = True
        save_state(state)
        time.sleep(1)


def main():
    configured = [c for c, w in WEBHOOK_BY_CATEGORY.items() if w]
    print(f"[INFO] MODE={MODE} | 模型={OPENAI_MODEL} | 清单{len(WATCHLIST)}只 | "
          f"已配置频道 {len(configured)}/9: {configured}")
    state = load_state()
    if MODE in ("post", "both"):
        run_post(state)
    if MODE in ("preview", "both"):
        run_preview(state)
    save_state(state)
    print("[INFO] 完成。")


if __name__ == "__main__":
    main()
