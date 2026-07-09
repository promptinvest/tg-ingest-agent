#!/usr/bin/env python3
"""Safely bind Cara to one private Telegram owner.

Run only while the service is stopped. Pass the expected numeric chat id when
possible; without it, bootstrap succeeds only when exactly one eligible private
chat is pending. It never allowlists every sender in the update queue.
"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.request import urlopen

ENV = Path("/etc/tg-ingest-agent.env")


def private_chat_ids(payload):
    """Return unique private chats whose sender id matches the chat id."""
    found = set()
    for update in payload.get("result") or []:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        if chat.get("type") == "private" and chat_id is not None and sender.get("id") == chat_id:
            found.add(int(chat_id))
    return found


def select_owner(payload, expected=None):
    eligible = private_chat_ids(payload)
    if expected is not None:
        expected = int(expected)
        if expected not in eligible:
            raise ValueError("the expected owner has no pending private message")
        return expected
    if len(eligible) != 1:
        raise ValueError(
            "bootstrap requires exactly one pending private chat; pass the expected numeric chat id")
    return next(iter(eligible))


def read_token(content):
    match = re.search(r"(?m)^TELEGRAM_BOT_TOKEN=(.*)$", content)
    token = match.group(1).strip() if match else ""
    if not token or token == "REPLACE_ME":
        raise ValueError("token not configured")
    return token


def write_owner(content, chat_id):
    replacement = f"ALLOWED_CHAT_IDS={int(chat_id)}"
    if re.search(r"(?m)^ALLOWED_CHAT_IDS=.*$", content):
        return re.sub(r"(?m)^ALLOWED_CHAT_IDS=.*$", replacement, content)
    return content.rstrip() + "\n" + replacement + "\n"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 1 or (argv and not re.fullmatch(r"-?\d+", argv[0])):
        raise SystemExit("usage: bootstrap_chat_id.py [expected_numeric_chat_id]")
    content = ENV.read_text(encoding="utf-8")
    token = read_token(content)
    with urlopen(
            f"https://api.telegram.org/bot{token}/getUpdates?timeout=5", timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    try:
        owner = select_owner(payload, argv[0] if argv else None)
    except ValueError as exc:
        print(f"bootstrap refused: {exc}")
        raise SystemExit(1) from exc
    ENV.write_text(write_owner(content, owner), encoding="utf-8")
    os.chmod(ENV, 0o600)
    print(f"one private owner was written to {ENV}")


if __name__ == "__main__":
    main()
