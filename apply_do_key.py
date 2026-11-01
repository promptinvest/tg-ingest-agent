#!/usr/bin/env python3
"""One-shot: apply a staged DO_MODEL_ACCESS_KEY to /etc/tg-ingest-agent.env.

The key is read from a file, never from argv, and is VALIDATED against the DO
inference API (GET /v1/models — a free call) BEFORE the env file is touched:
the old order wrote first and verified after, so a bad key replaced a working
one. A missing `DO_MODEL_ACCESS_KEY=` line used to make the rewrite a silent
no-op that still printed success; the line is now appended when absent. The env
write is atomic with a .bak, and the staged secret is deleted only on success —
its fate is always printed.
"""
import env_file

import json
import os as os
import re as re
from pathlib import Path
from urllib.request import Request, urlopen

STAGED = Path("/root/tg-ingest-agent-stage/.dokey.env")
ENV = Path("/etc/tg-ingest-agent.env")
KEY = "DO_MODEL_ACCESS_KEY"
MODELS_URL = "https://inference.do-ai.run/v1/models"


def read_staged(path, key):
    value = ""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            value = line.split("=", 1)[1].strip()
    if not value:
        raise SystemExit(f"no {key} in staged file {path}")
    return value


def verify_key(key, opener=urlopen):
    """List models with the STAGED key; an empty/failed listing aborts the write."""
    request = Request(MODELS_URL,
                      headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    with opener(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    models = [m.get("id") for m in payload.get("data") or []]
    if not models:
        raise SystemExit("staged DO key lists no models — refusing to write it")
    return models


def set_env_value(content, key, value):
    return env_file.set_env_value(content, key, value)


def write_env_atomically(path, content):
    return env_file.write_env_atomically(path, content, sync_directory=False)


def main(staged=STAGED, env=ENV, opener=urlopen):
    staged, env = Path(staged), Path(env)
    removed = wrote = False
    try:
        key = read_staged(staged, KEY)
        models = verify_key(key, opener)
        print(f"key valid; {len(models)} models visible")
        relevant = [m for m in models if m and ("claude" in m or "whisper" in m or "gpt-4o" in m)]
        print("relevant:", json.dumps(sorted(relevant), ensure_ascii=False))
        try:
            updated = set_env_value(env.read_text(encoding="utf-8"), KEY, key)
        except ValueError as exc:               # duplicated KEY= — say so, don't trace
            raise SystemExit(str(exc)) from exc
        write_env_atomically(env, updated)
        wrote = True
        print(f"DO key applied to {env} (previous contents kept as {env}.bak)")
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
