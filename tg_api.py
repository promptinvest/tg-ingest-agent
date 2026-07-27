#!/usr/bin/env python3
"""Minimal Telegram Bot API client (stdlib urllib).

Every network/parse fault is wrapped as TelegramError: a bare socket
read-timeout escapes urlopen as TimeoutError (not URLError), a reset mid-body
as ConnectionResetError, a truncated chunked body as http.client.IncompleteRead,
and a truncated JSON body as ValueError — none of which the callers' `except
TelegramError` would catch. Unwrapped, any of those kills the poll loop / a
scheduler tick outright (the exact lesson llm.py already encodes for the LLM
gateway; see SOLUTION.md §1.2).
"""
import http.client
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Transport/parse faults that must surface as TelegramError, not crash the
# process. Order matters at the except sites: HTTPError/URLError first (both
# are OSError subclasses), then this catch-all. ValueError covers json/unicode
# decode errors on a truncated body.
TRANSPORT_ERRORS = (TimeoutError, http.client.HTTPException, OSError, ValueError)


class TelegramError(Exception):
    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def http_error(method, exc):
    """One HTTPError → TelegramError translation for EVERY sender.

    Telegram puts the actual cause in the JSON body (`description`: "file is too
    big", "wrong file identifier") and the wait in `parameters.retry_after`. The
    multipart senders parsed neither, so a failed .ics or photo send logged a
    bare "HTTP 400" — the one line that says what went wrong was thrown away,
    and a 429 from them carried no retry hint although the caller knows how to
    read one.
    """
    description = ""
    retry_after = None
    try:
        body = json.loads(exc.read().decode("utf-8"))
        description = body.get("description") or ""
        retry_after = (body.get("parameters") or {}).get("retry_after")
    except Exception:  # noqa: BLE001 — the body is a bonus, never a second failure
        pass
    return TelegramError(
        f"{method} failed with HTTP {exc.code}: {description}",
        status=exc.code,
        retry_after=retry_after,
    )


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
        raise http_error(method, exc) from exc
    except URLError as exc:
        raise TelegramError(f"{method} failed: {exc.reason}") from exc
    except TRANSPORT_ERRORS as exc:
        raise TelegramError(f"{method} failed: {exc!r}") from exc
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
        raise http_error("sendDocument", exc) from exc
    except URLError as exc:
        raise TelegramError(f"sendDocument failed: {exc.reason}") from exc
    except TRANSPORT_ERRORS as exc:
        raise TelegramError(f"sendDocument failed: {exc!r}") from exc
    if not payload.get("ok"):
        raise TelegramError(f"sendDocument returned ok=false: {payload.get('description')}")
    return payload.get("result")


def tg_set_reaction(token, chat_id, message_id, emoji, big=False):
    """React to a message with one emoji (empty emoji clears the reaction)."""
    reaction = [{"type": "emoji", "emoji": emoji}] if emoji else []
    return tg_call(token, "setMessageReaction", {
        "chat_id": chat_id, "message_id": message_id, "reaction": reaction, "is_big": big})


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
        raise http_error("sendPhoto", exc) from exc
    except URLError as exc:
        raise TelegramError(f"sendPhoto failed: {exc.reason}") from exc
    except TRANSPORT_ERRORS as exc:
        raise TelegramError(f"sendPhoto failed: {exc!r}") from exc
    if not payload.get("ok"):
        raise TelegramError(f"sendPhoto returned ok=false: {payload.get('description')}")
    return payload.get("result")


def tg_download(token, file_path, dest):
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    try:
        with urlopen(Request(url), timeout=120) as response:
            Path(dest).write_bytes(response.read())
    except HTTPError as exc:
        # The last sender that reported a bare status. It is on the LIVE ingest
        # path, and "file is too big" — the 20 MB cap the voice reply text
        # exists for — is exactly what Telegram puts in `description`; the
        # status alone made the boss's voice note fail with «не получилось»
        # instead of the sentence that says why.
        raise http_error("file download", exc) from exc
    except URLError as exc:
        raise TelegramError(f"file download failed: {exc.reason}") from exc
    except TRANSPORT_ERRORS as exc:
        raise TelegramError(f"file download failed: {exc!r}") from exc
