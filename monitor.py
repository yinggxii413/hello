import os
import json
import requests

X_BEARER_TOKEN = os.environ["X_BEARER_TOKEN"]
DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"]
X_USERNAME = os.environ["X_USERNAME"]

STATE_FILE = "state.json"

headers = {
    "Authorization": f"Bearer {X_BEARER_TOKEN}"
}

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

def load_last_id():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r") as f:
        return json.load(f).get("last_id")

def save_last_id(tweet_id):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_id": tweet_id}, f)

def send_to_discord(tweet):
    url = f"https://x.com/{X_USERNAME}/status/{tweet['id']}"
    content = f"🚨 {X_USERNAME} 新帖\n\n{tweet['text']}\n\n{url}"
    r = requests.post(DISCORD_WEBHOOK, json={"content": content})
    r.raise_for_status()

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
