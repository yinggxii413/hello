name: X to Discord

on:
  schedule:
    - cron: "*/10 * * * *"   # 每 10 分钟一次(GitHub 可能漂到 15-20 分钟，属正常)
  workflow_dispatch:

permissions:
  contents: write

# 避免上一轮没结束又触发下一轮(导致 state.json 提交打架)
concurrency:
  group: x-to-discord
  cancel-in-progress: false

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - run: pip install -r requirements.txt

      - name: Run X monitor (single pass)
        env:
          X_BEARER_TOKEN: ${{ secrets.X_BEARER_TOKEN }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          DISCORD_WEBHOOK: ${{ secrets.DISCORD_WEBHOOK }}      # Serenity
          TRUMP_WEBHOOK:   ${{ secrets.TRUMP_WEBHOOK }}        # Trump Truth
          # FINANCIAL_JUICE_WEBHOOK: ${{ secrets.FINANCIAL_JUICE_WEBHOOK }}  # 已停用,恢复时取消注释
          ONESHOT: "1"          # ★关键：单次模式，跑一轮就退出(否则会死循环占满 Actions)
        run: python monitor.py

      - name: Save state
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git add state.json
          git commit -m "update x state" || echo "No changes"
          git push
