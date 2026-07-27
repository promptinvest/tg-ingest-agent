#!/usr/bin/env python3
"""Calendar skill: .ics export (zero-setup) + Google Calendar API sync.

Two layers:
- make_ics() builds an RFC 5545 file the bot sends as a document — tapping it
  on a phone adds the event to any calendar app. Works with no Google setup.
- insert_event() pushes the event into Google Calendar via a service account.
  Dormant until GCAL_CALENDAR_ID is set and the key file exists; RS256 JWT
  signing is delegated to the system openssl binary to stay stdlib-only.

Activation (one-time, ~5 min):
  1. Google Cloud console -> create project -> enable "Google Calendar API".
  2. Create a service account, download its JSON key.
  3. In Google Calendar -> calendar settings -> share the calendar with the
     service account's client_email (permission: make changes to events).
  4. Put the JSON key at /etc/tg-ingest-agent/gcal-sa.json, `chown tg-ingest:`
     and chmod 0600 — the service reads it as the `tg-ingest` user, so a
     root-owned 0600 key passes configured() but fails every read — and set
     GCAL_CALENDAR_ID=<your calendar id / gmail> in the env; restart.

Every failure path raises CalendarError (key unreadable/malformed, bare socket
timeouts — which escape urlopen as OSError, not URLError) so the caller's
.ics fallback always engages instead of the error skipping its except clause.
"""
import base64
import http.client
import json
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import store
from common import log, scrub_secrets

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"

TOKEN_KV_KEYS = ("gcal_token", "gcal_token_exp")
# How much of Google's error body is carried into the message the boss/journal
# sees. Enough to name the cause ("Request had insufficient authentication
# scopes"), never the whole document.
ERROR_BODY_CHARS = 200
# Calendar v3 answers 403 for TWO unrelated things: "your credentials are not
# accepted" and "you are sending too much" (rateLimitExceeded,
# userRateLimitExceeded, dailyLimitExceeded, quotaExceeded — messages like
# «Rate Limit Exceeded», «Calendar usage limits exceeded»). Only the first is a
# token problem. Dropping a perfectly good cached token on a quota 403 would
# re-mint, retry with no backoff, fail again and drop it a SECOND time: four
# calls where there was one, the cache destroyed for the rest of the quota
# window, and two extra 30 s urlopen timeouts inside a single update — all aimed
# at a service that just asked for less traffic. Google's own wording is already
# parsed by `_error_detail`, so use it.
RATE_LIMITED_HINT = re.compile(r"rate limit|quota|usage limit|too many|daily limit",
                               re.IGNORECASE)


class CalendarError(Exception):
    pass


class _AuthRejected(CalendarError):
    """Google refused the ACCESS TOKEN itself (401/403) — retryable exactly once,
    with a freshly minted token. A CalendarError subclass, so it still lands in
    every caller's `except CalendarError` .ics fallback if it escapes."""


def _error_detail(exc):
    """A bounded, secret-scrubbed slice of an HTTPError body. Google puts the
    real cause in `error.message` ("Not Found", "The caller does not have
    permission"); reporting only the numeric status threw it away and left the
    boss — and the issue row — with an HTTP code and nothing to act on.

    Two body shapes, because two endpoints: the Calendar API answers
    `{"error": {"message": …}}`, the OAuth token endpoint
    `{"error": "invalid_grant", "error_description": …}` — where `error` is a
    plain string and the sentence worth reading is the description. Anything
    else (or an unparseable body) falls back to the raw text.
    """
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — a diagnostic must never mask the failure
        return ""
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        err = payload.get("error")
        parts = [(err.get("message") if isinstance(err, dict) else err) or "",
                 payload.get("error_description") or ""]
        detail = ": ".join(str(p) for p in parts if p)
    else:
        detail = raw
    return scrub_secrets(" ".join(str(detail).split()))[:ERROR_BODY_CHARS]


def _suffix(detail):
    return f": {detail}" if detail else ""


def invalidate_token(conn):
    """Drop the cached access token so the next call mints a fresh one."""
    store.kv_delete(conn, *TOKEN_KV_KEYS)


def configured(cfg):
    return bool(cfg.gcal_calendar_id) and Path(cfg.gcal_key_file).exists()


# -- .ics generation -----------------------------------------------------------

def _ics_escape(text):
    return (str(text or "")
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\n", "\\n"))


def _ics_dt(iso_utc):
    parsed = datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00")).astimezone(timezone.utc)
    return parsed.strftime("%Y%m%dT%H%M%SZ")


RRULES = {"daily": "RRULE:FREQ=DAILY", "weekly": "RRULE:FREQ=WEEKLY"}


def make_ics(events, now_utc=None):
    """events: [{uid, title, start_utc, duration_minutes, recurrence}]."""
    now_utc = now_utc or datetime.now(timezone.utc)
    stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//tg-ingest-agent//EN"]
    for event in events:
        start = datetime.fromisoformat(str(event["start_utc"]).replace("Z", "+00:00"))
        end = start + timedelta(minutes=int(event.get("duration_minutes") or 30))
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{event['uid']}@tg-ingest-agent",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_ics_dt(event['start_utc'])}",
            f"DTEND:{_ics_dt(end.isoformat())}",
            f"SUMMARY:{_ics_escape(event['title'])}",
        ])
        rrule = RRULES.get(str(event.get("recurrence") or "none"))
        if rrule:
            lines.append(rrule)
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def event_from_reminder(row, duration_minutes=30):
    return {
        "uid": f"reminder-{row['id']}",
        "title": row["title"],
        "start_utc": row["due_utc"],
        "duration_minutes": duration_minutes,
        "recurrence": row["recurrence"] if "recurrence" in row.keys() else "none",
    }


# -- Google service-account auth (RS256 via openssl) ---------------------------

def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def build_jwt_unsigned(client_email, now_epoch):
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": client_email,
        "scope": CALENDAR_SCOPE,
        "aud": GOOGLE_TOKEN_URI,
        "iat": now_epoch,
        "exp": now_epoch + 3600,
    }
    return (_b64url(json.dumps(header).encode()) + b"." +
            _b64url(json.dumps(claims).encode()))


def _sign_rs256(signing_input, private_key_pem):
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as handle:
        handle.write(private_key_pem)
        key_path = handle.name
    try:
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_path],
            input=signing_input, capture_output=True, check=True,
        )
        return result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise CalendarError(f"openssl signing failed: {exc}") from exc
    finally:
        Path(key_path).unlink(missing_ok=True)


def get_access_token(cfg, conn):
    cached = store.kv_get(conn, "gcal_token")
    expires = store.kv_get(conn, "gcal_token_exp")
    now = datetime.now(timezone.utc)
    if cached and expires and expires > now.isoformat():
        return cached
    try:
        sa = json.loads(Path(cfg.gcal_key_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CalendarError(f"service-account key unreadable: {exc!r}") from exc
    if not sa.get("client_email") or not sa.get("private_key"):
        raise CalendarError("service-account key missing client_email/private_key")
    signing_input = build_jwt_unsigned(sa["client_email"], int(now.timestamp()))
    assertion = signing_input + b"." + _b64url(_sign_rs256(signing_input, sa["private_key"]))
    body = urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion.decode("ascii"),
    }).encode("utf-8")
    request = Request(GOOGLE_TOKEN_URI, data=body, method="POST",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise CalendarError(f"google token request failed with HTTP {exc.code}"
                            f"{_suffix(_error_detail(exc))}") from exc
    except URLError as exc:
        raise CalendarError(f"google token request failed: {exc.reason}") from exc
    except (TimeoutError, http.client.HTTPException, OSError, ValueError) as exc:
        raise CalendarError(f"google token request failed: {exc!r}") from exc
    token = payload.get("access_token")
    if not token:
        raise CalendarError("google token response had no access_token")
    lifetime = int(payload.get("expires_in") or 3600)
    store.kv_set(conn, "gcal_token", token)
    store.kv_set(conn, "gcal_token_exp",
                 (now + timedelta(seconds=lifetime - 120)).isoformat())
    return token


def build_event_payload(event):
    start = datetime.fromisoformat(str(event["start_utc"]).replace("Z", "+00:00"))
    end = start + timedelta(minutes=int(event.get("duration_minutes") or 30))
    payload = {
        "summary": event["title"],
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    rrule = RRULES.get(str(event.get("recurrence") or "none"))
    if rrule:
        payload["recurrence"] = [rrule]
    return payload


def _insert_once(cfg, token, event):
    from urllib.parse import quote
    url = (f"https://www.googleapis.com/calendar/v3/calendars/"
           f"{quote(cfg.gcal_calendar_id)}/events")
    request = Request(
        url,
        data=json.dumps(build_event_payload(event)).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = _error_detail(exc)
        message = f"calendar insert failed with HTTP {exc.code}{_suffix(detail)}"
        # 401 is always the token. 403 is the token UNLESS Google says the
        # problem is volume (see RATE_LIMITED_HINT) — throwing away a valid
        # cached token there costs four calls instead of one and re-mints for
        # every later call in the same quota window.
        if exc.code == 401 or (exc.code == 403 and not RATE_LIMITED_HINT.search(detail)):
            raise _AuthRejected(message) from exc
        raise CalendarError(message) from exc
    except URLError as exc:
        raise CalendarError(f"calendar insert failed: {exc.reason}") from exc
    except (TimeoutError, http.client.HTTPException, OSError, ValueError) as exc:
        raise CalendarError(f"calendar insert failed: {exc!r}") from exc
    log(f"calendar event created: {payload.get('id')}")
    return payload.get("htmlLink") or ""


def insert_event(cfg, conn, event):
    """Insert into Google Calendar; returns the event htmlLink.

    The access token is cached in kv for its full lifetime (~58 min). When
    Google stops accepting it — the service-account key was rotated, the
    account disabled, the calendar unshared — a 401/403 came back, the cached
    token was kept, and EVERY calendar_add for the rest of that hour failed the
    same way while the .ics fallback quietly covered it. Drop the cached token
    and retry ONCE with a freshly minted one; if that is refused too, the
    problem is permissions, not the token, and Google's own description is
    carried out with the error.
    """
    for attempt in (0, 1):
        token = get_access_token(cfg, conn)
        try:
            return _insert_once(cfg, token, event)
        except _AuthRejected as exc:
            invalidate_token(conn)
            if attempt:
                raise CalendarError(f"{exc} (after re-minting the access token)") from exc
            log(f"calendar token rejected ({exc}); re-minting and retrying once")
