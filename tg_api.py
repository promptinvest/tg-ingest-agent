#!/usr/bin/env python3
"""Minimal Telegram Bot API client (stdlib urllib)."""
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TelegramError(Exception):
    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def tg_call(token, method, params=None, timeout=35):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        data[key] = value
    request = Request(
        url,
        data=urlencode(data).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        description = ""
        retry_after = None
        try:
            body = json.loads(exc.read().decode("utf-8"))
            description = body.get("description") or ""
            retry_after = (body.get("parameters") or {}).get("retry_after")
        except Exception:
            pass
        raise TelegramError(
            f"{method} failed with HTTP {exc.code}: {description}",
            status=exc.code,
            retry_after=retry_after,
        ) from exc
    except URLError as exc:
        raise TelegramError(f"{method} failed: {exc.reason}") from exc
    if not payload.get("ok"):
        raise TelegramError(f"{method} returned ok=false: {payload.get('description')}")
    return payload.get("result")


def tg_download(token, file_path, dest):
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    try:
        with urlopen(Request(url), timeout=120) as response:
            Path(dest).write_bytes(response.read())
    except (HTTPError, URLError) as exc:
        reason = getattr(exc, "code", None) or getattr(exc, "reason", exc)
        raise TelegramError(f"file download failed: {reason}") from exc
