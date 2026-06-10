import os
import json
import requests
from openai import OpenAI

X_BEARER_TOKEN = os.environ["X_BEARER_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

STATE_FILE = "state.json"

ACCOUNTS = [
    {
        "username": "aleabitoreddit",
        "display_name": "Serenity",
        "webhook": os.environ.get("DISCORD_WEBHOOK"),
    },
    {
        "username": "TrumpDailyPosts",
        "display_name": "Trump Truth",
        "webhook": os.environ.get("TRUMP_WEBHOOK"),
    },
    {
        "username": "financialjuice",
        "display_name": "Financial Juice",
        "webhook": os.environ.get("FINANCIAL_JUICE_WEBHOOK"),
    },
]

client = OpenAI(api_key=OPENAI_API_KEY)

headers = {
    "Authorization": f"Bearer {X_BEARER_TOKEN}"
}


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    with open(STATE_FILE, "r") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def get_user_id(username):
    url = f"https://api.x.com/2/users/by/username/{username}"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    return r.json()["data"]["id"]


def get_latest_posts(user_id):
    url = f"https://api.x.com/2/users/{user_id}/tweets"

    params = {
        "max_results": 5,
        "tweet.fields": "created_at",
        "exclude": "replies,retweets"
    }

    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()

    return r.json().get("data", [])


def translate_to_chinese(text):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "你是专业财经翻译助手，请翻译成自然流畅中文。"
                },
                {
                    "role": "user",
                    "content": text
                }
            ]
        )

        return response.choices[0].message.content.strip()

    except Exception:
        return "翻译失败"


def send_to_discord(account, tweet):

    tweet_url = (
        f"https://x.com/{account['username']}/status/{tweet['id']}"
    )

    original_text = tweet["text"]
    chinese_text = translate_to_chinese(original_text)

    payload = {
        "embeds": [
            {
                "title": f"📰 {account['display_name']} 新推文",
                "url": tweet_url,
                "description":
                    f"**原文**\n{original_text}\n\n"
                    f"**中文翻译**\n{chinese_text}",
                "color": 3447003
            }
        ]
    }

    requests.post(
        account["webhook"],
        json=payload
    ).raise_for_status()


def process_account(account, state):

    username = account["username"]

    user_id = get_user_id(username)

    posts = get_latest_posts(user_id)

    if not posts:
        print(f"No posts found for {username}")
        return

    last_id = state.get(username)

    newest_id = posts[0]["id"]

    if last_id is None:
        state[username] = newest_id
        print(
            f"Initialized state for {username}. No message sent."
        )
        return

    new_posts = []

    for post in posts:

        if post["id"] == last_id:
            break

        new_posts.append(post)

    for post in reversed(new_posts):
        send_to_discord(account, post)

    state[username] = newest_id

    print(
        f"Sent {len(new_posts)} new post(s) for {username}"
    )


def main():

    state = load_state()

    for account in ACCOUNTS:
        process_account(account, state)

    save_state(state)


if __name__ == "__main__":
    main()
