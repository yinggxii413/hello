#!/usr/bin/env python3
"""
每日 AI 基础设施简报：用 Claude API 联网生成中文简报，再发到 Discord。
带去重记忆：记录每天发过的标题，下次生成时排除最近几天发过的事件。

环境变量（在 GitHub Actions 里作为 Secrets 配置）：
    ANTHROPIC_API_KEY     -- 你的 Anthropic API key
    DISCORD_WEBHOOK_URL    -- 你的 Discord webhook 地址（workflow 里映射自 BRIEFING_WEBHOOK_URL）
    BRIEFING_MODEL         -- 可选，默认 claude-sonnet-4-6

依赖：anthropic（见 requirements.txt）
"""
import os
import re
import sys
import json
import time
import datetime
import urllib.request

import anthropic

MODEL = os.environ.get("BRIEFING_MODEL", "claude-sonnet-4-6")
DISCORD_LIMIT = 1900  # 留余量，Discord 单条上限 2000 字符
STATE_FILE = "briefing_state.json"
DEDUP_DAYS = 4   # 排除最近几天发过的事件
KEEP_DAYS = 10   # state 文件最多保留多少天记录

# 用东八区（北京时间）算"今天"，避免 UTC 跨日。如需改时区改 hours=8。
NOW_BJ = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
TODAY = NOW_BJ.strftime("%-m月%-d日")
TODAY_KEY = NOW_BJ.strftime("%Y-%m-%d")
YESTERDAY_KEY = (NOW_BJ - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

# 数字 emoji（1️⃣..9️⃣）后面跟 **标题** 的匹配，用于从简报里抽出当天标题
TITLE_RE = re.compile(r"[1-9]️⃣\s*\*\*(.+?)\*\*")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"entries": []}


def recent_titles(state):
    """取最近 DEDUP_DAYS 天发过的标题，作为本次的'禁止重复'清单。"""
    titles = []
    for e in state.get("entries", [])[-DEDUP_DAYS:]:
        titles.extend(e.get("titles", []))
    return titles


def build_prompt(exclusions):
    exclude_block = ""
    if exclusions:
        listed = "\n".join(f"- {t}" for t in exclusions)
        exclude_block = f"""
【最近几天已经发过的事件 — 今天不要再作为要点重复】
下面这些是最近几天的简报已经报道过的事件。今天**不要**再把它们当成新要点；
**即使措辞、角度不同，只要是同一事件就算重复。** 除非该事件今天有**全新的重大进展**（例如新数据、新交易、新政策），否则一律跳过，换今天真正新发生的新闻。
{listed}
"""

    return f"""请生成一份「AI基础设施每日简报」，中文，今天是 {TODAY}。这份简报会直接发到 Discord 频道，请严格按下面的格式和排版要求输出。

【内容要求】
1. 今天是 {TODAY}（北京时间）。**只保留发布时间在 {YESTERDAY_KEY} 或 {TODAY_KEY} 的新闻**（即过去约 24-36 小时内新发生或新披露的事件）。每条都要先在检索结果里确认它的发布日期；更早的旧闻、或只是最近几天旧事件的延续，一律不选。
2. 范围＝**整个 AI 基础设施建设产业链，不局限于任何固定板块**。下面是产业链环节清单（仅作提醒、不设上限，链条上任何当天重大新闻都要纳入）：
   - 上游设备/材料/先进封装（封装测试设备、CoWoS 产能等）
   - 芯片：NVIDIA 及各家 GPU、自研 ASIC（博通、Google TPU、亚马逊 Trainium、Meta MTIA）、AMD/Intel、CPU
   - 存储：HBM/DRAM/NAND（Micron、SK hynix、Samsung 的财报、产能、价格、诉讼）
   - 网络与光互连：交换机、光模块、CPO、NVLink/以太网
   - 服务器与系统：ODM/OEM（Dell、SMCI、鸿海等）、液冷散热
   - 云与算力运营：超大规模厂商（微软/谷歌/亚马逊/Meta/Oracle）的 AI 资本开支、云业务、自建或出售算力；neocloud（CoreWeave、Nebius 等）；主权 AI；GPU/算力租赁价格
   - 数据中心建设与能源：选址/并网/建设进度、电力/核电/SMR/储能
   - 资本与宏观：融资、IPO、并购、出口管制与政策、SOX、Nasdaq、美债收益率、Fed、KOSPI
3. **检索策略（检索次数有限，务必用在刀刃上）**：第 1 次先做一个宽检索，例如「过去24小时 AI infrastructure / AI compute 重大新闻」，先把当天最重要的跨领域大事件抓出来（例如某大厂宣布进军云、出售富余算力这类）；其余检索再针对上面清单里当天有动静的环节补充。绝不要因为某条新闻不在某个细分环节，就把它漏掉。
4. **新鲜度是硬要求，且同一事件换个说法也算重复**：最近几天已报道过的持续性话题（见下方清单），今天除非有全新重大进展（新数据/新交易/新政策）否则不要再选。
5. 每条要点都必须基于检索到的真实数据，不要编造数字；记不准的数字宁可不写。
6. 选出约 5 条最重要且最新的要点。
{exclude_block}
【格式要求 — 必须严格遵守】
- 这是 Discord 消息，用 Discord 支持的 markdown：**加粗**、`━` 分隔线、emoji。不要用 # 号标题，不要用表格。
- 严禁出现任何网址、超链接、markdown 链接、脚注或「Sources / 来源 / 参考」部分。全文不得有 http 链接。
- 关键数字、公司名、涨跌幅请用 **加粗** 突出。
- 严格按以下模板排版（X月X日替换成今天日期）：

**📡 AI基础设施每日简报｜{TODAY}**
━━━━━━━━━━━━━━━━

1️⃣ **<该条核心事件的小标题>**
<一句话讲清事件，关键数字加粗>
　▫️ **【产业链影响】** <一句话>
　▫️ **【机会 / 风险】** <一句话>

2️⃣ **<小标题>**
<事件>
　▫️ **【产业链影响】** <…>
　▫️ **【机会 / 风险】** <…>

（3️⃣ 4️⃣ 5️⃣ 同上）

━━━━━━━━━━━━━━━━
👀 **今日重点观察**
<一行，列出今天要盯的几个点>

💡 **我的判断**
<一两句简短判断>

【输出要求】
直接输出上面的简报正文本身，不要任何前言、说明或结尾客套。"""


def generate_briefing(exclusions):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        messages=[{"role": "user", "content": build_prompt(exclusions)}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        raise RuntimeError("模型未返回文本内容")
    return text


def extract_titles(text):
    return [m.strip() for m in TITLE_RE.findall(text)]


def chunk(text, limit=DISCORD_LIMIT):
    chunks, buf = [], ""
    for para in text.split("\n\n"):
        piece = (buf + "\n\n" + para).strip() if buf else para
        if len(piece) <= limit:
            buf = piece
        else:
            if buf:
                chunks.append(buf)
            while len(para) > limit:
                chunks.append(para[:limit])
                para = para[limit:]
            buf = para
    if buf:
        chunks.append(buf)
    return chunks


def post_to_discord(text):
    url = os.environ["DISCORD_WEBHOOK_URL"]
    for i, c in enumerate(chunk(text)):
        data = json.dumps({"content": c}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                # Discord/Cloudflare 会拦截默认的 python-urllib UA，必须自带 User-Agent
                "User-Agent": "ai-infra-briefing-bot/1.0 (+github-actions)",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"已发送第 {i+1} 段 -> HTTP {r.status}")
        time.sleep(0.6)  # 避开 Discord 限流


def save_state(state, titles):
    entries = [e for e in state.get("entries", []) if e.get("date") != TODAY_KEY]
    entries.append({"date": TODAY_KEY, "titles": titles})
    entries = entries[-KEEP_DAYS:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f, ensure_ascii=False, indent=2)
    print(f"已记录今日 {len(titles)} 条标题到 {STATE_FILE}")


def main():
    for key in ("ANTHROPIC_API_KEY", "DISCORD_WEBHOOK_URL"):
        if not os.environ.get(key):
            sys.exit(f"ERROR: 缺少环境变量 {key}")

    state = load_state()

    # 幂等保护：今天已经成功发过就直接退出，避免多个触发点重复发送/重复扣费。
    # 想强制重发，给运行设环境变量 FORCE_SEND=1 即可。
    entries = state.get("entries", [])
    last = entries[-1] if entries else None
    if (not os.environ.get("FORCE_SEND")) and last and last.get("date") == TODAY_KEY and last.get("titles"):
        print(f"今天（{TODAY_KEY}）已发送过，跳过本次运行（未调用 API、未重复发送）。")
        return

    exclusions = recent_titles(state)
    print(f"加载到 {len(exclusions)} 条最近已发标题，将避免重复。")

    print("正在生成简报…")
    text = generate_briefing(exclusions)
    print(f"简报生成完成，{len(text)} 字符，开始发送到 Discord…")
    post_to_discord(text)

    # 仅在成功发送后才记录状态，避免失败时误标记为已发
    titles = extract_titles(text)
    save_state(state, titles)
    print("完成。")


if __name__ == "__main__":
    main()
