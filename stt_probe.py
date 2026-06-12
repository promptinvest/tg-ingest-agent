#!/usr/bin/env python3
"""Probe STT options on DO serverless inference using a real stored voice
file. Reads the key from /etc/tg-ingest-agent.env; secrets never in argv."""
import base64
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

key = ""
for line in Path("/etc/tg-ingest-agent.env").read_text(encoding="utf-8").splitlines():
    if line.startswith("DO_MODEL_ACCESS_KEY="):
        key = line.split("=", 1)[1].strip()
assert key and key != "REPLACE_ME"

media = sorted(Path("/var/lib/tg-ingest-agent/media").glob("*.oga"))
if not media:
    print("NO VOICE FILE in media/ — send a voice note first")
    raise SystemExit(0)
audio = media[-1].read_bytes()
print(f"using {media[-1].name} ({len(audio)} bytes)")


def show(label, fn):
    try:
        print(f"--- {label}: OK ->", fn()[:400])
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")[:300]
        except Exception:
            pass
        print(f"--- {label}: HTTP {exc.code} -> {body.replace(key, '<redacted>')}")
    except (URLError, Exception) as exc:
        print(f"--- {label}: FAIL -> {exc!r}")


def try_transcriptions(model):
    import uuid
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n".encode(),
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\";"
         f" filename=\"voice.oga\"\r\nContent-Type: audio/ogg\r\n\r\n").encode(),
        audio,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    request = Request(
        "https://inference.do-ai.run/v1/audio/transcriptions",
        data=b"".join(parts),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8")


def try_chat_audio(model):
    payload = {
        "model": model,
        "max_tokens": 200,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Transcribe this audio verbatim. Reply with only the transcript."},
                {"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(audio).decode("ascii"), "format": "ogg"}},
            ],
        }],
    }
    request = Request(
        "https://inference.do-ai.run/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=90) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str((data.get("choices") or [{}])[0].get("message", {}).get("content") or "")


show("transcriptions whisper-large-v3", lambda: try_transcriptions("whisper-large-v3"))
show("transcriptions openai-whisper-1", lambda: try_transcriptions("whisper-1"))
show("chat-audio nemotron-3-nano-omni", lambda: try_chat_audio("nemotron-3-nano-omni"))
show("chat-audio openai-gpt-4o", lambda: try_chat_audio("openai-gpt-4o"))
