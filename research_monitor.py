#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
研报解读 -> Discord（只推与 watchlist 相关的投行研报/评级）
=============================================================
数据源(免费、已实测可服务器端抓)：
  1. 华尔街见闻 7x24 快讯 API（含正文 + 关联个股）
  2. 每日经济新闻 首页 HTML（标题 + 链接）
  3. 21世纪经济 首页 HTML（标题 + 链接）
  4. 东方财富研报 API（A股券商研报，结构化；美股清单一般匹配不到，作补充）

逻辑：拉各源 → 命中「投行/研报关键词 + watchlist 个股」→ 去重 → 抓正文 →
OpenAI 整理成中文「研报解读」卡片 → 推到 Discord。每天定时跑(见 research.yml)。

必填环境变量(GitHub Secrets)：
  OPENAI_API_KEY
  RESEARCH_WEBHOOK     研报频道 Webhook
可选：
  OPENAI_MODEL         默认 gpt-4o-mini
  RESEARCH_MAX         每次最多推几条，默认 8
"""

import os
import re
import json
import time
import requests
from openai import OpenAI

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
RESEARCH_WEBHOOK = os.environ["RESEARCH_WEBHOOK"]
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
RESEARCH_MAX = int(os.environ.get("RESEARCH_MAX", "8"))

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()  # 复用财报的 key；无则跳过该源
RESEARCH_LOOKBACK_DAYS = int(os.environ.get("RESEARCH_LOOKBACK_DAYS", "3"))

STATE_FILE = "research_state.json"
HTTP_TIMEOUT = 30

# Finnhub 评级源用：美股代码 -> 展示名
TICKER_TO_NAME = {
    "NVDA": "英伟达 (NVDA)", "AVGO": "博通 (AVGO)", "AMD": "AMD", "INTC": "英特尔 (INTC)",
    "QCOM": "高通 (QCOM)", "ARM": "Arm (ARM)", "MRVL": "Marvell (MRVL)", "TSM": "台积电 (TSM)",
    "ASML": "ASML", "AMAT": "应用材料 (AMAT)", "KLAC": "KLA (KLAC)", "MU": "美光 (MU)",
    "WDC": "西部数据 (WDC)", "AAPL": "苹果 (AAPL)", "MSFT": "微软 (MSFT)", "GOOGL": "谷歌 (GOOGL)",
    "AMZN": "亚马逊 (AMZN)", "META": "Meta (META)", "TSLA": "特斯拉 (TSLA)", "ORCL": "甲骨文 (ORCL)",
    "CRWV": "CoreWeave (CRWV)", "SMCI": "超微电脑 (SMCI)", "COHR": "Coherent (COHR)",
    "LITE": "Lumentum (LITE)", "RKLB": "Rocket Lab (RKLB)", "COIN": "Coinbase (COIN)", "NOK": "诺基亚 (NOK)",
}
ACTION_ZH = {"up": "上调", "down": "下调", "init": "首次覆盖", "main": "维持", "reit": "重申"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

client = OpenAI(api_key=OPENAI_API_KEY)

# ---- watchlist：每只票的匹配词（中文名/英文名/代码），命中其一即算相关 ----
WATCHLIST = {
    "英伟达 (NVDA)": ["英伟达", "NVDA", "Nvidia"],
    "博通 (AVGO)": ["博通", "AVGO", "Broadcom"],
    "AMD": ["AMD", "超威"],
    "英特尔 (INTC)": ["英特尔", "INTC", "Intel"],
    "高通 (QCOM)": ["高通", "QCOM", "Qualcomm"],
    "Arm (ARM)": ["Arm", "ARM", "安谋"],
    "Marvell (MRVL)": ["Marvell", "MRVL", "美满"],
    "台积电 (TSM)": ["台积电", "TSM", "TSMC"],
    "ASML": ["ASML", "阿斯麦"],
    "应用材料 (AMAT)": ["应用材料", "AMAT"],
    "KLA (KLAC)": ["科磊", "KLAC", "KLA"],
    "美光 (MU)": ["美光", "MU", "Micron"],
    "西部数据 (WDC)": ["西部数据", "WDC"],
    "苹果 (AAPL)": ["苹果", "AAPL", "Apple"],
    "微软 (MSFT)": ["微软", "MSFT", "Microsoft"],
    "谷歌 (GOOGL)": ["谷歌", "GOOGL", "GOOG", "Alphabet"],
    "亚马逊 (AMZN)": ["亚马逊", "AMZN", "Amazon"],
    "Meta (META)": ["Meta", "META", "脸书"],
    "特斯拉 (TSLA)": ["特斯拉", "TSLA", "Tesla"],
    "甲骨文 (ORCL)": ["甲骨文", "ORCL", "Oracle"],
    "CoreWeave (CRWV)": ["CoreWeave", "CRWV"],
    "超微电脑 (SMCI)": ["超微电脑", "美超微", "SMCI"],
    "Coherent (COHR)": ["Coherent", "COHR", "高意"],
    "Lumentum (LITE)": ["Lumentum", "LITE"],
    "Rocket Lab (RKLB)": ["Rocket Lab", "RKLB", "火箭实验室"],
    "Coinbase (COIN)": ["Coinbase", "COIN"],
    "诺基亚 (NOK)": ["诺基亚", "NOK", "Nokia"],
}

# ---- 投行 / 研报关键词（命中其一才算"研报/评级"类） ----
IB_KEYWORDS = [
    "大摩", "摩根士丹利", "摩根大通", "小摩", "高盛", "美银", "美国银行", "花旗",
    "瑞银", "巴克莱", "杰富瑞", "伯恩斯坦", "富国", "德银", "麦格理", "汇丰",
    "中金", "中信证券", "投行", "分析师",
    "评级", "目标价", "上调", "下调", "重申", "维持", "首予", "首次覆盖",
    "买入", "增持", "减持", "看多", "看好", "唱多", "研报",
]


def match_watchlist(text):
    if not text:
        return None
    for name, terms in WATCHLIST.items():
        for t in terms:
            if t in text:
                return name
    return None


def is_research(text):
    return any(k in (text or "") for k in IB_KEYWORDS)


# ---- 状态(去重，按链接) ----
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"seen": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
            s.setdefault("seen", [])
            return s
    except Exception:
        return {"seen": []}


def save_state(state):
    state["seen"] = state["seen"][-800:]  # 只留最近 800 条，防膨胀
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


# ---- 抓取工具 ----
def _get(url, as_json=False):
    try:
        r = requests.get(url, headers=UA, timeout=HTTP_TIMEOUT)
    except Exception as e:
        print(f"[WARN] 抓取异常 {url[:60]}: {e}")
        return None
    if r.status_code != 200:
        print(f"[WARN] HTTP {r.status_code} {url[:60]}")
        return None
    try:
        return r.json() if as_json else r.text
    except Exception:
        return None


def html_to_text(html, limit=4000):
    body = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', html or "")
    text = re.sub(r'(?s)<[^>]+>', ' ', body)
    text = re.sub(r'&[a-z]+;', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()[:limit]


# ---- 各数据源：返回 [{title, text, url, source, stock}] ----
def src_finnhub():
    """Finnhub 分析师评级变动（付费档接口；无权限自动跳过）。"""
    if not FINNHUB_API_KEY:
        return []
    out = []
    cutoff = time.time() - RESEARCH_LOOKBACK_DAYS * 86400
    for tk, name in TICKER_TO_NAME.items():
        url = f"https://finnhub.io/api/v1/stock/upgrade-downgrade?symbol={tk}&token={FINNHUB_API_KEY}"
        try:
            r = requests.get(url, timeout=HTTP_TIMEOUT)
        except Exception as e:
            print(f"[WARN] Finnhub 异常: {e}"); continue
        if r.status_code in (401, 403):
            print("[WARN] Finnhub 评级接口无权限(可能需付费档)，跳过该源")
            return out
        if r.status_code != 200:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for d in data[:10]:
            gt = d.get("gradeTime", 0) or 0
            if gt < cutoff:
                continue
            firm = d.get("company", "") or "某机构"
            frm, to = d.get("fromGrade") or "", d.get("toGrade") or ""
            act = ACTION_ZH.get(d.get("action", ""), d.get("action", ""))
            out.append({"title": f"{firm} {act} {name} 评级至 {to}",
                        "text": f"{firm} {act} {name} 评级：{frm} → {to}",
                        "url": f"https://stockanalysis.com/stocks/{tk.lower()}/",
                        "source": f"Finnhub·{firm}", "stock": name,
                        "uid": f"fh-{tk}-{int(gt)}-{firm}"})
        time.sleep(0.15)
    return out


def src_wallstreetcn():
    out = []
    data = _get("https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&client=pc&limit=60", as_json=True)
    items = (((data or {}).get("data") or {}).get("items")) or []
    for it in items:
        title = it.get("title") or ""
        text = it.get("content_text") or ""
        blob = title + " " + text
        stock = match_watchlist(blob)
        if stock and is_research(blob):
            out.append({"title": title or text[:40], "text": text,
                        "url": it.get("uri", ""), "source": "华尔街见闻", "stock": stock})
    return out


def src_eastmoney():
    out = []
    data = _get("https://reportapi.eastmoney.com/report/list?pageSize=50&pageNo=1&qType=0", as_json=True)
    for it in (data or {}).get("data", []) or []:
        blob = (it.get("title") or "") + " " + (it.get("stockName") or "")
        stock = match_watchlist(blob)
        if stock:  # 东方财富本身就是研报，匹配到清单即可
            org = it.get("orgSName") or it.get("orgName") or ""
            rating = it.get("emRatingName") or ""
            text = f"{org} {rating}研报：{it.get('title','')}"
            out.append({"title": it.get("title", ""), "text": text,
                        "url": "https://data.eastmoney.com/report/", "source": f"东方财富·{org}",
                        "stock": stock})
    return out


def _src_homepage(home_url, source_name):
    out = []
    html = _get(home_url)
    if not html:
        return out
    # 抓 文章链接 + 标题
    links = re.findall(r'href="(https?://[^"]*?/article/[^"]+\.html)"[^>]*title="([^"]+)"', html)
    seen_local = set()
    for url, title in links:
        if url in seen_local:
            continue
        seen_local.add(url)
        stock = match_watchlist(title)
        if stock and is_research(title):
            out.append({"title": title, "text": "", "url": url, "source": source_name, "stock": stock})
    return out


def src_nbd():
    return _src_homepage("https://www.nbd.com.cn/", "每日经济新闻")


def src_21jingji():
    return _src_homepage("https://www.21jingji.com/", "21世纪经济")


# ---- OpenAI 整理成卡片 ----
SYS = """你是财经研报编辑。下面是一条关于某只股票的"投行研报/评级"相关资讯(来自财经媒体)。
请用中文整理成简洁的「研报解读」，发到 Discord。不要用 Markdown 表格。按这个结构：

**{标题(精炼,可改写更清晰)}**
来源：{媒体}｜机构：{识别到的投行/券商，没有则写"—"}
（若提到）评级/目标价：{内容}

**解读**
- 2-4 条核心要点(投行观点、逻辑、对该股影响)

硬性要求：
- 只基于提供的内容，**绝不编造数字、评级或不存在的表态**；信息少就只写确定的。
- 简洁、突出重点，适合手机看。"""


def make_card(item):
    body_text = item.get("text") or ""
    if not body_text and item.get("url"):     # 每经/21世纪只有标题 → 抓正文补充
        page = _get(item["url"])
        if page:
            body_text = html_to_text(page)
    payload = {
        "涉及个股": item["stock"], "来源": item["source"],
        "标题": item["title"], "正文": body_text[:4000],
    }
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL, temperature=0.3,
            messages=[{"role": "system", "content": SYS},
                      {"role": "user", "content": "资讯(JSON)：\n" + json.dumps(payload, ensure_ascii=False)}],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[WARN] OpenAI 失败: {e}")
        return None


# ---- Discord(embed，多卡一条消息) ----
def discord_send(embeds):
    if not embeds:
        return
    for i in range(0, len(embeds), 10):
        payload = {"content": "📑 **研报解读** · watchlist 相关投行观点", "embeds": embeds[i:i+10]}
        for _ in range(3):
            try:
                r = requests.post(RESEARCH_WEBHOOK, json=payload, timeout=HTTP_TIMEOUT)
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


def main():
    state = load_state()
    seen = set(state["seen"])
    items = []
    for fn in (src_finnhub, src_wallstreetcn, src_eastmoney, src_nbd, src_21jingji):
        try:
            items += fn()
        except Exception as e:
            print(f"[WARN] 源 {fn.__name__} 异常: {e}")
    print(f"[INFO] 命中 watchlist 研报候选 {len(items)} 条")

    # 去重(按 url，无 url 用 标题)
    new_items, used = [], set()
    for it in items:
        key = it.get("uid") or it.get("url") or it.get("title")
        if not key or key in seen or key in used:
            continue
        used.add(key)
        new_items.append(it)
    new_items = new_items[:RESEARCH_MAX]
    if not new_items:
        print("[INFO] 无新研报，跳过。")
        save_state(state)
        return

    embeds = []
    for it in new_items:
        card = make_card(it)
        if not card:
            continue
        embeds.append({"title": f"📈 {it['stock']}", "description": card[:4000],
                       "color": 0xF1C40F,
                       "footer": {"text": it["source"]}})
        state["seen"].append(it.get("uid") or it.get("url") or it.get("title"))
        time.sleep(1)

    discord_send(embeds)
    save_state(state)
    print(f"[INFO] 已推送 {len(embeds)} 条研报解读。")


if __name__ == "__main__":
    main()
