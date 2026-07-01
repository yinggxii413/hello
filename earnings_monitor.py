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
import re
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
FOOL_ARCHIVE = "https://www.fool.com/earnings-call-transcripts"
HTTP_TIMEOUT = 30
# 抓 Motley Fool 需要真实 UA，否则可能被拦
WEB_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

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

# 单频道改造：财报硬数据 -> WEBHOOK_EARNINGS；电话会摘要 -> WEBHOOK_CALLS
WEBHOOK_EARNINGS = os.environ.get("WEBHOOK_EARNINGS", "").strip()
WEBHOOK_CALLS = os.environ.get("WEBHOOK_CALLS", "").strip()

# 电话会白名单：默认 = watchlist 里除"抓不到"的标的外的全部。
# 排除：非美上市(韩股/日股)、ETF、封闭式基金(本就没有电话会)。
# Motley Fool 覆盖主流美股；冷门小票若没有转录，脚本会自动跳过、不报错。
# 可用环境变量 TRANSCRIPT_TICKERS 覆盖(逗号分隔)。
TRANSCRIPT_EXCLUDE = {"005930.KS", "000660.KS", "KIOXIA", "QTUM", "DXYZ"}
_default_transcript = ",".join(t for t in sorted(WATCHLIST) if t not in TRANSCRIPT_EXCLUDE)
TRANSCRIPT_TICKERS = {t.strip().upper() for t in
                      os.environ.get("TRANSCRIPT_TICKERS", _default_transcript).split(",") if t.strip()}


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


# ---------------- 电话会转录(免费抓 Motley Fool 归档页，仅白名单) ----------------
def _web_get(url):
    try:
        r = requests.get(url, headers=WEB_UA, timeout=HTTP_TIMEOUT)
    except Exception as e:
        print(f"[WARN] 抓取异常 {url}: {e}")
        return None
    if r.status_code != 200:
        print(f"[WARN] 抓取 HTTP {r.status_code} {url}")
        return None
    return r.text


def find_transcript_url(ticker, max_pages=6):
    """在 Motley Fool 转录归档页里按 ticker 匹配最新转录链接。"""
    tk = ticker.lower()
    for page in range(1, max_pages + 1):
        url = FOOL_ARCHIVE + ("/" if page == 1 else f"/page/{page}/")
        html = _web_get(url)
        if not html:
            continue
        links = re.findall(
            r'(?:https://www\.fool\.com)?/earnings/call-transcripts/\d{4}/\d{2}/\d{2}/[a-z0-9-]+/?',
            html)
        for link in links:
            slug = link.rstrip("/").rsplit("/", 1)[-1]
            # ticker 以 -mu- 或 开头-mu 形式出现在 slug
            if re.search(rf'(^|-){re.escape(tk)}(-|$)', slug):
                return link if link.startswith("http") else "https://www.fool.com" + link
    return None


def fetch_transcript(ticker):
    """返回(转录正文, 来源URL)；找不到返回(None, None)。"""
    url = find_transcript_url(ticker)
    if not url:
        print(f"[INFO] {ticker}: 归档页未找到转录链接")
        return None, None
    html = _web_get(url)
    if not html:
        return None, url
    # 去脚本/样式，剥标签，压空白
    body = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', html)
    text = re.sub(r'(?s)<[^>]+>', ' ', body)
    text = re.sub(r'&[a-z]+;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    # 定位电话会正文起点，截掉前面的导航
    start = len(text)
    for marker in ("Prepared Remarks", "Operator", "Call participants", "Earnings Call"):
        i = text.find(marker)
        if 0 <= i < start:
            start = i
    if start == len(text):
        start = 0
    return text[start:start + 16000], url


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


SYS_CALL = """你是专业的美股财报分析师。下面是某公司最新一季【电话会转录(英文)】。
请用中文提炼【电话会核心】，发到 Discord。不要用 Markdown 表格，用加粗小标题逐条列。
严格按下面结构(示例排版，内容换成真实信息)：

📞 **{公司中文名}（{代码}）· 电话会核心** — FY{年} Q{季}

**📊 业绩与指引**
本季关键数字 + 下季/全年指引。**所有关键数字、超预期/纪录等结论都用粗体突出**。

**🚀 业务亮点**
增长最快/管理层着重强调的业务、产品、客户(每条把核心词加粗)。

**⚠️ 隐忧/逆风**
管理层提到的压力、放缓、供需/成本/需求的不确定性(关键点加粗)。

**🤝 大客户/订单/产能**
提到的大客户、长协、订单、产能/扩产、资本开支(数字加粗)。

**💬 管理层原话**
引用 **2-4 句**最有信息量的管理层原话：中文翻译为主、括号内保留关键英文短语，
并标注是谁说的(CEO/CFO 姓名)。格式如：
> "中文翻译……"（关键英文短语）—— CEO Sanjay Mehrotra

**❓ Q&A 关键点**
分析师最关心的 1-3 个问题 + 管理层回答要点(提问机构名可带上)。

**🔻 风险提示**
主要风险一句话概括。

硬性要求：
- **只基于提供的转录内容**，原话引用必须是转录里真实出现的，绝不编造数字、原话或不存在的表态；转录没提到的就不写。
- **突出重点**：关键数字(营收/EPS/毛利率/指引/同比)、纪录、超预期、重大客户/订单一律**加粗**。
- 简洁、适合手机看；中文，专业术语和关键短语可保留英文。
- 结尾一行小字："以上为电话会要点的自动提炼，可能有遗漏，以公司正式披露为准。" """


def gen_call_summary(item, company_name, transcript):
    payload = {
        "symbol": item.get("symbol"),
        "company_name": company_name,
        "quarter": item.get("quarter"),
        "year": item.get("year"),
        "transcript_excerpt": transcript,
    }
    return _chat(SYS_CALL, "公司电话会转录(JSON)，据此提炼电话会核心：\n\n"
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

    # ---- 1) 数据报告：按板块分组，post-key 去重(每只只发一次) ----
    by_cat = {}
    for it in reported:
        key = f"post-{it.get('symbol')}-{it.get('year')}Q{it.get('quarter')}"
        if state.get(key):
            continue
        cat = CATEGORY_BY_TICKER.get(it.get("symbol"))
        by_cat.setdefault(cat, []).append((key, it))

    for cat, items in by_cat.items():
        webhook = WEBHOOK_EARNINGS
        syms = [it.get("symbol") for _, it in items]
        if not webhook:
            print(f"[WARN] 未配置 WEBHOOK_EARNINGS，跳过 {syms}(state 不记，待配置后补发)")
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

    # ---- 2) 电话会：白名单票，call-key 独立去重 ----
    # 转录可能比财报晚几小时才发布；没抓到就不标记 call-key，后续轮次继续重试(3天窗口内)。
    for it in reported:
        sym = it.get("symbol")
        if sym not in TRANSCRIPT_TICKERS:
            continue
        ckey = f"call-{sym}-{it.get('year')}Q{it.get('quarter')}"
        if state.get(ckey):
            continue
        webhook = WEBHOOK_CALLS
        if not webhook:
            continue
        tx, src = fetch_transcript(sym)
        if not tx:
            print(f"[INFO] {sym}: 转录暂未发布，待下一班重试")
            continue  # 不标记 ckey → 下轮再试
        name = fetch_company_name(sym)
        summary = gen_call_summary(it, name, tx)
        if summary:
            if src:
                summary += f"\n🔗 转录来源：{src}"
            discord_send(webhook, summary)
            state[ckey] = True
            save_state(state)
            print(f"[INFO] {sym}: 已发电话会核心")
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
        webhook = WEBHOOK_EARNINGS
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
