#!/usr/bin/env python3
"""
转发器：读取 routine 生成并 commit 的简报文件，分段发到 Discord。
不调用任何 LLM —— 纯文本转发，免费。

由 GitHub Actions 在 latest_briefing.md 被 push 时触发。

环境变量：
    DISCORD_WEBHOOK_URL   -- Discord webhook（workflow 里映射自 BRIEFING_WEBHOOK_URL）
    BRIEFING_FILE         -- 可选，默认 latest_briefing.md
"""
import os
import sys
import json
import time
import urllib.request

DISCORD_LIMIT = 1900  # 留余量，Discord 单条上限 2000
BRIEFING_FILE = os.environ.get("BRIEFING_FILE", "latest_briefing.md")


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
                "User-Agent": "ai-infra-briefing-bot/1.0 (+github-actions)",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"已发送第 {i+1} 段 -> HTTP {r.status}")
        time.sleep(0.6)


def main():
    if not os.environ.get("DISCORD_WEBHOOK_URL"):
        sys.exit("ERROR: 缺少环境变量 DISCORD_WEBHOOK_URL")
    if not os.path.exists(BRIEFING_FILE):
        sys.exit(f"ERROR: 找不到简报文件 {BRIEFING_FILE}")
    with open(BRIEFING_FILE, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        sys.exit("ERROR: 简报文件为空")
    print(f"读取 {BRIEFING_FILE}，{len(text)} 字符，开始转发到 Discord…")
    post_to_discord(text)
    print("转发完成。")


if __name__ == "__main__":
    main()
