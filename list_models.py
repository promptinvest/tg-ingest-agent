#!/usr/bin/env python3
"""List all model ids visible to the configured DO key (reads /etc env)."""
import json
from pathlib import Path
from urllib.request import Request, urlopen

key = ""
for line in Path("/etc/tg-ingest-agent.env").read_text(encoding="utf-8").splitlines():
    if line.startswith("DO_MODEL_ACCESS_KEY="):
        key = line.split("=", 1)[1].strip()
assert key and key != "REPLACE_ME"

request = Request(
    "https://inference.do-ai.run/v1/models",
    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
)
with urlopen(request, timeout=30) as response:
    payload = json.loads(response.read().decode("utf-8"))
for model in sorted(m.get("id") or "" for m in payload.get("data") or []):
    print(model)
