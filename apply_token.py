#!/usr/bin/env python3
"""One-shot: apply a staged TELEGRAM_BOT_TOKEN to /etc/tg-ingest-agent.env.

The token is read from a file, never from argv, and is VALIDATED against getMe
BEFORE the env file is touched — the old order wrote first and verified after,
so a typo replaced a working token. A missing `TELEGRAM_BOT_TOKEN=` line used
to make the rewrite a silent no-op that still printed success; the line is now
appended when absent. The env write is atomic with a .bak, and the staged
secret is deleted only on success — its fate is always printed.
"""
import env_file

import json
import os as os
import re as re
from pathlib import Path
from urllib.request import urlopen

STAGED = Path("/root/tg-ingest-agent-stage/.token.env")
ENV = Path("/etc/tg-ingest-agent.env")
KEY = "TELEGRAM_BOT_TOKEN"


def read_staged(path, key):
    value = ""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            value = line.split("=", 1)[1].strip()
    if not value:
        raise SystemExit(f"no {key} in staged file {path}")
    return value


def verify_token(token, opener=urlopen):
    """getMe with the STAGED token; anything but ok:true aborts before a write."""
    with opener(f"https://api.telegram.org/bot{token}/getMe", timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok") or not payload.get("result"):
        raise SystemExit(f"staged token rejected by getMe: {payload.get('description')}")
    return payload["result"]


def set_env_value(content, key, value):
    return env_file.set_env_value(content, key, value)


def write_env_atomically(path, content):
    return env_file.write_env_atomically(path, content, sync_directory=False)


def main(staged=STAGED, env=ENV, opener=urlopen):
    staged, env = Path(staged), Path(env)
    removed = wrote = False
    try:
        token = read_staged(staged, KEY)
        print(json.dumps(verify_token(token, opener), ensure_ascii=False))
        try:
            updated = set_env_value(env.read_text(encoding="utf-8"), KEY, token)
        except ValueError as exc:               # duplicated KEY= — say so, don't trace
            raise SystemExit(str(exc)) from exc
        write_env_atomically(env, updated)
        wrote = True
        print(f"token applied to {env} (previous contents kept as {env}.bak)")
        staged.unlink()
        removed = True
    finally:
        if removed:
            print(f"staged secret removed: {staged}")
        elif staged.exists():
            state = "the env file WAS updated" if wrote else "the env file was NOT changed"
            print(f"staged secret KEPT at {staged} — {state}; fix the problem and re-run, "
                  "or delete the staged file by hand.")


if __name__ == "__main__":
    main()
