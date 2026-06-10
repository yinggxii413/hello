import os
import json
import requests
from openai import OpenAI

X_BEARER_TOKEN = os.environ["X_BEARER_TOKEN"]
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"]
X_USERNAME = os.environ["X_USERNAME"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

STATE_FILE = "state.json"

client = OpenAI(api_key=OPENAI_API_KEY)

headers = {
    "Authorization": f"Bearer {X_BEARER_TOKEN}"
}

def get_user_id(username):
    url = f"https://api.x.com/2/users/by/username/{username}"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()["data"]["id"]

def get_latest_posts(user_id):
    url = f"https://api.x.com/2/users/{user_id}/tweets"
    params = {
        "max_results": 5,
        "tweet.fields": "created_at",
        "exclude": "replies,retweets"
    }
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json().get("data", [])

def load_last_id():
    if not os.path.exists(STATE_FILE):
        return None

    with open(STATE_FILE, "r") as file:
        return json.load(file).get("last_id")

def save_last_id(tweet_id):
    with open(STATE_FILE, "w") as file:
        json.dump({"last_id": tweet_id}, file)

def translate_to_chinese(text):
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "你是专业财经和AI产业链翻译助手。请把英文推文翻译成自然、准确、适合中文投资者阅读的中文。保留股票代码、公司名、专业术语。不要添加原文没有的信息。"
            },
            {
                "role": "user",
                "content": text
            }
        ]
    )

    return response.choices[0].message.content.strip()

def send_to_discord(tweet):
    tweet_url = f"https://x.com/{X_USERNAME}/status/{tweet['id']}"
    original_text = tweet["text"]
    chinese_text = translate_to_chinese(original_text)

    payload = {
        "embeds": [
            {
                "title": "🚨 Serenity 新推文",
                "url": tweet_url,
                "description": f"**【原文】**\n{original_text}\n\n**【中文翻译】**\n{chinese_text}",
                "color": 3447003,
                "footer": {
                    "text": f"来源：@{X_USERNAME}"
                }
            }
        ]
    }

    requests.post(
        DISCORD_WEBHOOK,
        json=payload
    ).raise_for_status()

def main():
    user_id = get_user_id(X_USERNAME)
    posts = get_latest_posts(user_id)

    if not posts:
        print("No posts found.")
        return

    last_id = load_last_id()
    newest_id = posts[0]["id"]

    if last_id is None:
        save_last_id(newest_id)
        print("Initialized state. No message sent.")
        return

    new_posts = []

    for post in posts:
        if post["id"] == last_id:
            break
        new_posts.append(post)

    for post in reversed(new_posts):
        send_to_discord(post)

    save_last_id(newest_id)
    print(f"Sent {len(new_posts)} new post(s).")

if __name__ == "__main__":
    main()
