#!/usr/bin/env python3
"""One-shot: apply staged TELEGRAM_BOT_TOKEN to /etc/tg-ingest-agent.env and
verify it via getMe. Token is read from a file, never from argv. Deletes the
staged secret afterwards."""
import json
import os
import re
from pathlib import Path
from urllib.request import urlopen

STAGED = Path("/root/tg-ingest-agent-stage/.token.env")
ENV = Path("/etc/tg-ingest-agent.env")

token = ""
for line in STAGED.read_text(encoding="utf-8").splitlines():
    if line.startswith("TELEGRAM_BOT_TOKEN="):
        token = line.split("=", 1)[1].strip()
assert token, "no token in staged file"

content = ENV.read_text(encoding="utf-8")
content = re.sub(r"(?m)^TELEGRAM_BOT_TOKEN=.*$", "TELEGRAM_BOT_TOKEN=" + token, content)
ENV.write_text(content, encoding="utf-8")
os.chmod(ENV, 0o600)

with urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=20) as response:
    result = json.loads(response.read().decode("utf-8"))
print(json.dumps(result.get("result"), ensure_ascii=False))

STAGED.unlink()
print("token applied to /etc/tg-ingest-agent.env; staged secret removed")
