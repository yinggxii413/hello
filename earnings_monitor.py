import os
import json
import requests
from datetime import date, timedelta
from openai import OpenAI

FINNHUB_API_KEY = os.environ["FINNHUB_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
EARNINGS_WEBHOOK = os.environ["EARNINGS_WEBHOOK"]

STATE_FILE = "earnings_state.json"

WATCHLIST = [
    "NVDA", "AMD", "AVGO", "MRVL",
    "MU", "SNDK", "WDC", "STX",
    "ANET", "COHR", "LITE",
    "MSFT", "AMZN", "GOOGL", "META", "AAPL", "TSLA",
    "VRT", "ETN",
    "RKLB", "ASTS",
]

client = OpenAI(api_key=OPENAI_API_KEY)


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def fetch_earnings():
    today = date.today()
    to_day = today + timedelta(days=14)

    url = "https://finnhub.io/api/v1/calendar/earnings"

    params = {
        "from": today.isoformat(),
        "to": to_day.isoformat(),
        "token": FINNHUB_API_KEY,
    }

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()

    return r.json().get("earningsCalendar", [])


def analyze_earnings(item):
    prompt = f"""
请用中文分析这家公司即将发布的财报，面向AI产业链投资者。

公司：{item.get("symbol")}
日期：{item.get("date")}
季度：{item.get("quarter")}
年份：{item.get("year")}
EPS预期：{item.get("epsEstimate")}
营收预期：{item.get("revenueEstimate")}

请输出：
1. 为什么这家公司值得关注
2. 财报重点看什么
3. 对AI产业链的影响
4. 可能利多因素
5. 可能风险
6. 一句话结论
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "你是专业美股AI产业链财报分析师，语言简洁、直接、适合发到Discord。",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
    )

    return response.choices[0].message.content.strip()


def send_to_discord(item, analysis):
    symbol = item.get("symbol")
    earnings_date = item.get("date")

    content = f"""
📊 **{symbol} 财报提醒**

日期：{earnings_date}
季度：Q{item.get("quarter")} {item.get("year")}

EPS预期：{item.get("epsEstimate")}
营收预期：{item.get("revenueEstimate")}

{analysis}
"""

    r = requests.post(
        EARNINGS_WEBHOOK,
        json={"content": content[:1900]},
        timeout=30,
    )

    print(f"Discord status for {symbol}: {r.status_code}")
    r.raise_for_status()


def main():
    state = load_state()
    earnings = fetch_earnings()

    print(f"Fetched {len(earnings)} earnings events")

    for item in earnings:
        symbol = item.get("symbol")

        if symbol not in WATCHLIST:
            continue

        key = f"{symbol}-{item.get('date')}-{item.get('quarter')}-{item.get('year')}"

        if state.get(key):
            print(f"Already sent: {key}")
            continue

        print(f"New earnings event: {key}")

        analysis = analyze_earnings(item)
        send_to_discord(item, analysis)

        state[key] = True

    save_state(state)


if __name__ == "__main__":
    main()
