#!/usr/bin/env python3
"""One-shot: apply staged DO_MODEL_ACCESS_KEY to /etc/tg-ingest-agent.env and
verify it against the DO inference API (GET /v1/models — free call). Secret is
read from a file, never from argv. Deletes the staged secret afterwards."""
import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen

STAGED = Path("/root/tg-ingest-agent-stage/.dokey.env")
ENV = Path("/etc/tg-ingest-agent.env")

key = ""
for line in STAGED.read_text(encoding="utf-8").splitlines():
    if line.startswith("DO_MODEL_ACCESS_KEY="):
        key = line.split("=", 1)[1].strip()
assert key, "no key in staged file"

content = ENV.read_text(encoding="utf-8")
content = re.sub(r"(?m)^DO_MODEL_ACCESS_KEY=.*$", "DO_MODEL_ACCESS_KEY=" + key, content)
ENV.write_text(content, encoding="utf-8")
os.chmod(ENV, 0o600)

request = Request(
    "https://inference.do-ai.run/v1/models",
    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
)
with urlopen(request, timeout=30) as response:
    payload = json.loads(response.read().decode("utf-8"))
models = [m.get("id") for m in payload.get("data") or []]
print(f"key valid; {len(models)} models visible")
interesting = [m for m in models if m and ("claude" in m or "whisper" in m or "gpt-4o" in m)]
print("relevant:", json.dumps(sorted(interesting), ensure_ascii=False))

STAGED.unlink()
print("DO key applied to /etc/tg-ingest-agent.env; staged secret removed")
