#!/usr/bin/env python3
"""Binary object storage backend.

Default backend is 'local' (files live under MEDIA_DIR, as today). When
STORAGE_BACKEND=spaces and credentials are present, binaries are also
uploaded to a DigitalOcean Space (S3-compatible) for durability — the
droplet was wiped once before, so local media would not survive a rebuild.

S3 Signature V4 is implemented in pure stdlib (hmac/hashlib). This module
is wired in now but stays dormant until the Space + keys are configured;
with backend=local every call is a no-op.
"""
import hashlib
import hmac
import http.client
import sqlite3
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from common import log

# The same taxonomy tg_api.py and llm.py already encode, for the same reason: a
# bare socket read-timeout escapes urlopen as TimeoutError, a reset mid-body as
# ConnectionResetError, a truncated chunked body as http.client.IncompleteRead —
# none of them URLError, so none of them caught by a caller's `except
# StorageError`. `offload` runs INSIDE the live message path, so an uncaught one
# would surface as "error handling update", cost the boss his save and put the
# update on the dead-letter road. Order matters at the except sites:
# HTTPError/URLError first (both are OSError subclasses), then this catch-all.
TRANSPORT_ERRORS = (TimeoutError, http.client.HTTPException, OSError, ValueError)


class StorageError(Exception):
    pass


def backend(cfg):
    if cfg.storage_backend == "spaces" and cfg.spaces_key and cfg.spaces_secret and cfg.spaces_bucket:
        return "spaces"
    return "local"


# -- S3 Signature V4 ----------------------------------------------------------

def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _hmac(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret, date_stamp, region, service):
    k_date = _hmac(("AWS4" + secret).encode("utf-8"), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


def canonical_request(method, uri, query, headers, signed_headers, payload_hash):
    canonical_headers = "".join(
        f"{h}:{headers[h]}\n" for h in signed_headers.split(";")
    )
    return "\n".join([method, uri, query, canonical_headers, signed_headers, payload_hash])


def authorization_header(method, uri, host, payload, access_key, secret, region, service,
                         amz_date, extra_headers=None):
    """Build (authorization, headers) for an S3 request. amz_date is
    YYYYMMDDTHHMMSSZ."""
    date_stamp = amz_date[:8]
    payload_hash = _sha256_hex(payload)
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    if extra_headers:
        headers.update({k.lower(): v for k, v in extra_headers.items()})
    signed_headers = ";".join(sorted(headers))
    creq = canonical_request(method, uri, "", headers, signed_headers, payload_hash)
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope, _sha256_hex(creq.encode("utf-8")),
    ])
    signature = hmac.new(signing_key(secret, date_stamp, region, service),
                         string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return authorization, headers, signature


# -- object store -------------------------------------------------------------

def object_key(cfg, filename):
    prefix = cfg.spaces_prefix.strip("/")
    return f"{prefix}/{filename}" if prefix else filename


def put_object(cfg, key, data, content_type="application/octet-stream"):
    """Upload bytes to the Space via a signed PUT. Returns the object key."""
    host = cfg.spaces_endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    uri = "/" + cfg.spaces_bucket + "/" + quote(key)
    amz_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    authorization, headers, _sig = authorization_header(
        "PUT", uri, host, data, cfg.spaces_key, cfg.spaces_secret,
        cfg.spaces_region, "s3", amz_date,
        extra_headers={"content-type": content_type},
    )
    request = Request(f"https://{host}{uri}", data=data, method="PUT")
    request.add_header("Authorization", authorization)
    request.add_header("Content-Type", content_type)
    request.add_header("x-amz-content-sha256", headers["x-amz-content-sha256"])
    request.add_header("x-amz-date", amz_date)
    try:
        with urlopen(request, timeout=60) as response:
            response.read()
    except HTTPError as exc:
        raise StorageError(f"Spaces PUT failed HTTP {exc.code}") from exc
    except URLError as exc:
        raise StorageError(f"Spaces PUT failed: {exc.reason}") from exc
    except TRANSPORT_ERRORS as exc:
        raise StorageError(f"Spaces PUT failed: {exc!r}") from exc
    return key


def offload(cfg, conn, row_id):
    """Durably copy a message's local media to the Space (when enabled).
    Keeps local files; records the object key. No-op on the local backend.

    A durability EXTRA sitting on the live save path: whatever goes wrong with
    the SPACE, the note itself is already stored and the boss must still get his
    confirmation. So every such failure is caught — not just StorageError but an
    unreadable file or anything unforeseen — logged, and recorded as an issue so
    a Space that has been failing all week is visible instead of silent.
    (`except Exception` never catches KeyboardInterrupt/SystemExit, so shutdown
    still works.)

    A `sqlite3.Error` is deliberately NOT swallowed: a database that cannot take
    the object key is not the Space misbehaving, it is the condition WP2's
    containment guard exists for — pause, do not advance the offset, let Telegram
    redeliver. Hiding it here would make this function the one place on the
    inbound path where a full disk passes silently.

    ONE issue row per call, not per image: a 10-photo album saved while the Space
    is down is ONE event, and `recent_issues` is a bounded LIMIT list that a
    single bad album would otherwise flush clean of every other real problem
    (`issues` is never pruned by policy, so nothing else trims it either).
    Failures are collected and filed once, the way the sentence above already
    described them.
    """
    if backend(cfg) != "spaces":
        return 0
    import store
    from pathlib import Path
    uploaded = 0
    attempted = 0
    failures = []
    for img in store.message_images(conn, row_id):
        if img["object_key"] or not img["local_path"]:
            continue
        path = Path(img["local_path"])
        if not path.exists():
            continue
        attempted += 1
        try:
            key = put_object(cfg, object_key(cfg, path.name), path.read_bytes(), "image/jpeg")
            store.set_image_object_key(conn, img["id"], key)
            uploaded += 1
        except sqlite3.Error:
            raise            # the database, not the Space — see the docstring
        except Exception as exc:  # noqa: BLE001 — a durability extra must not cost the save
            # Keep going: one unreadable file must not abandon the album's rest.
            log(f"spaces offload failed for image {img['id']}: {exc!r}")
            failures.append((img["id"], exc))
    if failures:
        try:
            store.issue_add(conn, None, "storage_offload_failed",
                            f"{len(failures)} of {attempted} object(s) for #{row_id}"
                            f" failed, first image {failures[0][0]}: {failures[0][1]!r}")
        except Exception as issue_exc:  # noqa: BLE001 — reporting must not raise either
            log(f"could not record the offload failure: {issue_exc!r}")
    if uploaded:
        log(f"offloaded {uploaded} media object(s) for #{row_id} to Spaces")
    return uploaded
