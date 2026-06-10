name: X to Discord

on:
  schedule:
    - cron: "*/10 * * * *"
  workflow_dispatch:

permissions:
  contents: write

jobs:
  run:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run monitor
        env:
          X_BEARER_TOKEN: ${{ secrets.X_BEARER_TOKEN }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          DISCORD_WEBHOOK: ${{ secrets.DISCORD_WEBHOOK }}
          TRUMP_WEBHOOK: ${{ secrets.TRUMP_WEBHOOK }}
          FINANCIAL_JUICE_WEBHOOK: ${{ secrets.FINANCIAL_JUICE_WEBHOOK }}
        run: python monitor.py

      - name: Save state
        run: |
          git config user.name "github-actions"
          git config user.email "github-actions@github.com"
          git pull --rebase
          git add state.json
          git commit -m "Update state" || echo "No changes"
          git push
