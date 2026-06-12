#!/usr/bin/env python3
"""One-shot: read the bot token from /etc/tg-ingest-agent.env, fetch pending
updates, and write the sender's chat id into ALLOWED_CHAT_IDS.

Run ONLY while the service is stopped (one poller per token). Safe to re-run.
"""
import json
import os
import re
from pathlib import Path
from urllib.request import urlopen

ENV = Path("/etc/tg-ingest-agent.env")

token = ""
for line in ENV.read_text(encoding="utf-8").splitlines():
    if line.startswith("TELEGRAM_BOT_TOKEN="):
        token = line.split("=", 1)[1].strip()
assert token and token != "REPLACE_ME", "token not configured"

with urlopen(f"https://api.telegram.org/bot{token}/getUpdates?timeout=5", timeout=30) as response:
    payload = json.loads(response.read().decode("utf-8"))

chats = {}
for update in payload.get("result") or []:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    if chat.get("id") is not None:
        chats[chat["id"]] = (
            chat.get("username") or chat.get("first_name") or chat.get("title") or "?"
        )

if not chats:
    print("no pending updates — message the bot first, then re-run")
    raise SystemExit(1)

print("seen chats:", json.dumps(chats, ensure_ascii=False))
ids = ",".join(str(chat_id) for chat_id in sorted(chats))
content = ENV.read_text(encoding="utf-8")
content = re.sub(r"(?m)^ALLOWED_CHAT_IDS=.*$", "ALLOWED_CHAT_IDS=" + ids, content)
ENV.write_text(content, encoding="utf-8")
os.chmod(ENV, 0o600)
print(f"ALLOWED_CHAT_IDS={ids} written to {ENV}")
