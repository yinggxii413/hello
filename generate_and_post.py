#!/usr/bin/env python3
"""
每日 AI 基础设施简报：用 Claude API 联网生成中文简报，再发到 Discord。

环境变量（在 GitHub Actions 里作为 Secrets 配置）：
    ANTHROPIC_API_KEY     -- 你的 Anthropic API key
    DISCORD_WEBHOOK_URL   -- 你的 Discord webhook 地址
    BRIEFING_MODEL        -- 可选，默认 claude-sonnet-4-6

依赖：anthropic（见 requirements.txt）
"""
import os
import sys
import json
import time
import datetime
import urllib.request

import anthropic

MODEL = os.environ.get("BRIEFING_MODEL", "claude-sonnet-4-6")
DISCORD_LIMIT = 1900  # 留余量，Discord 单条上限 2000 字符

# 用东八区（北京时间）算"今天"，避免 UTC 跨日。如需改时区改 hours=8。
TODAY = (
    datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)
).strftime("%-m月%-d日")

PROMPT = f"""请生成一份「AI基础设施每日简报」，中文，今天是 {TODAY}。供直接发到 Discord 频道。

要求：
1. 先联网检索过去24小时内的最新真实新闻，覆盖：(a) 存储/HBM/DRAM/NAND（Micron、SK hynix、Samsung 的财报与产能）；(b) NVIDIA 及 AI 算力平台（如 Vera Rubin、主权AI、数据中心部署）；(c) 数据中心电力/并网/液冷/核电等能源瓶颈；(d) 半导体与科技股宏观面（SOX、Nasdaq、美债收益率、Fed、KOSPI 等）。
2. 每条要点都必须基于检索到的真实数据，不要编造数字；记不准的数字宁可不写。
3. 固定结构：
   - 标题：「AI基础设施每日简报｜{TODAY}」
   - 约5条要点，每条 = 一句话核心事件 +「产业链影响：」+「机会/风险：」
   - 结尾「今日重点观察」一行 +「我的判断」一句
4. 风格简洁、信息密度高，中文研报口吻。
5. 末尾附「Sources:」，列出用到的新闻链接。
6. 直接输出简报正文本身，不要任何前言或额外说明。"""


def generate_briefing():
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
        messages=[{"role": "user", "content": PROMPT}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        raise RuntimeError("模型未返回文本内容")
    return text


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


def main():
    for key in ("ANTHROPIC_API_KEY", "DISCORD_WEBHOOK_URL"):
        if not os.environ.get(key):
            sys.exit(f"ERROR: 缺少环境变量 {key}")
    print("正在生成简报…")
    text = generate_briefing()
    print(f"简报生成完成，{len(text)} 字符，开始发送到 Discord…")
    post_to_discord(text)
    print("完成。")


if __name__ == "__main__":
    main()
