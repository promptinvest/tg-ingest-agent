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


def tg_send_document(token, chat_id, filename, content_bytes, caption=None,
                     content_type="text/calendar"):
    from common import build_multipart
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption[:1000]
    body, boundary = build_multipart(fields, "document", filename, content_bytes, content_type)
    request = Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise TelegramError(f"sendDocument failed with HTTP {exc.code}", status=exc.code) from exc
    except URLError as exc:
        raise TelegramError(f"sendDocument failed: {exc.reason}") from exc
    if not payload.get("ok"):
        raise TelegramError(f"sendDocument returned ok=false: {payload.get('description')}")
    return payload.get("result")


def tg_send_document_file_id(token, chat_id, file_id, caption=None):
    """Re-send a previously received document by its file_id — no upload, free,
    and not subject to download-link expiry."""
    params = {"chat_id": chat_id, "document": file_id}
    if caption:
        params["caption"] = caption[:1000]
    return tg_call(token, "sendDocument", params)


def tg_send_photo(token, chat_id, photo, caption=None, by_file_id=True):
    """Send a photo. by_file_id=True re-sends a stored file_id (no upload,
    free); otherwise `photo` is (filename, bytes) uploaded via multipart."""
    if by_file_id:
        params = {"chat_id": chat_id, "photo": photo}
        if caption:
            params["caption"] = caption[:1000]
        return tg_call(token, "sendPhoto", params)
    from common import build_multipart
    filename, content_bytes = photo
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption[:1000]
    body, boundary = build_multipart(fields, "photo", filename, content_bytes, "image/jpeg")
    request = Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise TelegramError(f"sendPhoto failed with HTTP {exc.code}", status=exc.code) from exc
    except URLError as exc:
        raise TelegramError(f"sendPhoto failed: {exc.reason}") from exc
    if not payload.get("ok"):
        raise TelegramError(f"sendPhoto returned ok=false: {payload.get('description')}")
    return payload.get("result")


def tg_download(token, file_path, dest):
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    try:
        with urlopen(Request(url), timeout=120) as response:
            Path(dest).write_bytes(response.read())
    except (HTTPError, URLError) as exc:
        reason = getattr(exc, "code", None) or getattr(exc, "reason", exc)
        raise TelegramError(f"file download failed: {reason}") from exc
