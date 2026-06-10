import os
import json
import requests
import feedparser

DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"]
RSS_URL = os.environ["RSS_URL"]

STATE_FILE = "state.json"

def load_last_id():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r") as f:
        return json.load(f).get("last_id")

def save_last_id(entry_id):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_id": entry_id}, f)

def send_to_discord(entry):
    title = entry.get("title", "New post")
    link = entry.get("link", "")
    content = f"🚨 New X Post\n\n{title}\n\n{link}"
    requests.post(DISCORD_WEBHOOK, json={"content": content}).raise_for_status()

def main():
    feed = feedparser.parse(RSS_URL)

    if not feed.entries:
        raise Exception("No RSS entries found. RSS_URL may be invalid or unavailable.")

    last_id = load_last_id()
    newest_id = feed.entries[0].get("id") or feed.entries[0].get("link")

    if last_id is None:
        save_last_id(newest_id)
        print("Initialized RSS state. No message sent.")
        return

    new_entries = []
    for entry in feed.entries:
        entry_id = entry.get("id") or entry.get("link")
        if entry_id == last_id:
            break
        new_entries.append(entry)

    for entry in reversed(new_entries):
        send_to_discord(entry)

    save_last_id(newest_id)
    print(f"Sent {len(new_entries)} new RSS item(s).")

if __name__ == "__main__":
    main()
