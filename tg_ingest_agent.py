#!/usr/bin/env python3
"""tg-ingest-agent: Telegram message ingest + LLM categorization.

Receives messages (text, photos, forwarded channel posts) from allowed chats
via long polling, stores them in SQLite, downloads photos, classifies each
message against a fixed category list using DigitalOcean Gradient serverless
inference, and replies in the chat with the category and a short summary.

Stdlib-only. Deployed on Pilot-VPS as /opt/tg-ingest-agent/agent.py.
"""
import base64
import json
import os
import re
import signal
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def log(message):
    print(f"{datetime.now(timezone.utc).isoformat()} {message}", flush=True)


# ---------------------------------------------------------------------------
# Configuration


class Config:
    pass


def config_list(value):
    normalized = str(value or "").replace("\n", ",").replace(";", ",").replace("|", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def parse_chat_ids(value):
    ids = set()
    for part in config_list(value):
        try:
            ids.add(int(part))
        except ValueError:
            raise SystemExit(f"ALLOWED_CHAT_IDS contains a non-numeric entry: {part!r}")
    return ids


def load_categories(env):
    file_path = (env.get("CATEGORIES_FILE") or "").strip()
    if file_path:
        lines = Path(file_path).read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    return config_list(env.get("CATEGORIES", ""))


def load_config(env=None):
    env = os.environ if env is None else env
    cfg = Config()
    cfg.token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not cfg.token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    cfg.allowed_chat_ids = parse_chat_ids(env.get("ALLOWED_CHAT_IDS", ""))
    if not cfg.allowed_chat_ids:
        raise SystemExit("ALLOWED_CHAT_IDS is required")
    cfg.do_key = (env.get("DO_MODEL_ACCESS_KEY") or "").strip()
    if not cfg.do_key:
        raise SystemExit("DO_MODEL_ACCESS_KEY is required")
    cfg.categories = load_categories(env)
    if not cfg.categories:
        raise SystemExit("CATEGORIES or CATEGORIES_FILE must provide at least one category")
    cfg.fallback_category = (env.get("FALLBACK_CATEGORY") or "uncategorized").strip()
    if validate_category(cfg.fallback_category, cfg.categories) is None:
        cfg.categories.append(cfg.fallback_category)
    cfg.do_model = (env.get("DO_CHAT_MODEL") or "anthropic-claude-haiku-4.5").strip()
    cfg.do_base_url = (env.get("DO_INFERENCE_BASE_URL") or "https://inference.do-ai.run/v1").strip()
    cfg.db_path = Path(env.get("DB_PATH") or "/var/lib/tg-ingest-agent/ingest.db")
    cfg.media_dir = Path(env.get("MEDIA_DIR") or "/var/lib/tg-ingest-agent/media")
    cfg.poll_timeout = int(env.get("POLL_TIMEOUT_SECONDS") or "50")
    cfg.album_settle = float(env.get("ALBUM_SETTLE_SECONDS") or "3")
    cfg.max_llm_images = int(env.get("MAX_LLM_IMAGES") or "4")
    cfg.llm_timeout = int(env.get("LLM_TIMEOUT_SECONDS") or "90")
    cfg.llm_max_attempts = int(env.get("LLM_MAX_ATTEMPTS") or "5")
    cfg.retry_interval = int(env.get("RETRY_INTERVAL_SECONDS") or "300")
    return cfg


# ---------------------------------------------------------------------------
# SQLite storage


SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  tg_message_id INTEGER NOT NULL,
  media_group_id TEXT,
  from_user_id INTEGER,
  forward_origin_type TEXT,
  forward_origin_chat_id INTEGER,
  forward_origin_title TEXT,
  forward_origin_message_id INTEGER,
  forward_date INTEGER,
  received_at TEXT NOT NULL,
  tg_date INTEGER,
  raw_text TEXT,
  category TEXT,
  summary TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  llm_model TEXT,
  llm_attempts INTEGER NOT NULL DEFAULT 0,
  duplicate_of INTEGER REFERENCES messages(id),
  UNIQUE (chat_id, tg_message_id)
);

CREATE TABLE IF NOT EXISTS urls (
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  url TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS images (
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  tg_message_id INTEGER NOT NULL,
  tg_file_id TEXT NOT NULL,
  tg_file_unique_id TEXT NOT NULL,
  local_path TEXT,
  width INTEGER,
  height INTEGER,
  file_size INTEGER
);

CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
CREATE INDEX IF NOT EXISTS idx_messages_fwd
  ON messages(forward_origin_chat_id, forward_origin_message_id);
"""


def open_db(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def kv_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()


def insert_message(conn, fields):
    """Insert a message row; returns its id, or None when the (chat_id,
    tg_message_id) pair was already stored (update redelivery)."""
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    cur = conn.execute(
        f"INSERT INTO messages ({columns}) VALUES ({placeholders}) "
        "ON CONFLICT(chat_id, tg_message_id) DO NOTHING",
        tuple(fields.values()),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def insert_url(conn, message_id, url):
    conn.execute("INSERT INTO urls (message_id, url) VALUES (?, ?)", (message_id, url))
    conn.commit()


def insert_image(conn, message_id, tg_message_id, photo, local_path):
    conn.execute(
        "INSERT INTO images (message_id, tg_message_id, tg_file_id, tg_file_unique_id,"
        " local_path, width, height, file_size) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            message_id,
            tg_message_id,
            photo.get("file_id"),
            photo.get("file_unique_id"),
            local_path,
            photo.get("width"),
            photo.get("height"),
            photo.get("file_size"),
        ),
    )
    conn.commit()


def get_message(conn, message_id):
    return conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()


def message_urls(conn, message_id):
    return conn.execute("SELECT * FROM urls WHERE message_id = ? ORDER BY id", (message_id,)).fetchall()


def message_images(conn, message_id):
    return conn.execute("SELECT * FROM images WHERE message_id = ? ORDER BY id", (message_id,)).fetchall()


def find_forward_duplicate(conn, fwd_chat_id, fwd_message_id, exclude_id):
    return conn.execute(
        "SELECT * FROM messages WHERE forward_origin_chat_id = ? AND forward_origin_message_id = ?"
        " AND id != ? AND duplicate_of IS NULL ORDER BY id LIMIT 1",
        (fwd_chat_id, fwd_message_id, exclude_id),
    ).fetchone()


def mark_duplicate(conn, message_id, original):
    conn.execute(
        "UPDATE messages SET duplicate_of = ?, category = ?, summary = ?, llm_model = ?,"
        " status = CASE WHEN ? IS NOT NULL THEN 'classified' ELSE status END WHERE id = ?",
        (
            original["id"],
            original["category"],
            original["summary"],
            original["llm_model"],
            original["category"],
            message_id,
        ),
    )
    conn.commit()


def set_classification(conn, message_id, category, summary, model):
    conn.execute(
        "UPDATE messages SET category = ?, summary = ?, llm_model = ?, status = 'classified' WHERE id = ?",
        (category, summary, model, message_id),
    )
    conn.commit()


def bump_attempts(conn, message_id):
    conn.execute("UPDATE messages SET llm_attempts = llm_attempts + 1 WHERE id = ?", (message_id,))
    conn.commit()
    return conn.execute(
        "SELECT llm_attempts FROM messages WHERE id = ?", (message_id,)
    ).fetchone()["llm_attempts"]


def mark_failed(conn, message_id):
    conn.execute("UPDATE messages SET status = 'failed' WHERE id = ?", (message_id,))
    conn.commit()


def pending_messages(conn, max_attempts, limit=5):
    return conn.execute(
        "SELECT * FROM messages WHERE status = 'pending' AND llm_attempts < ?"
        " AND duplicate_of IS NULL ORDER BY id LIMIT ?",
        (max_attempts, limit),
    ).fetchall()


def stats_text(conn):
    lines = ["By status:"]
    for row in conn.execute(
        "SELECT status, COUNT(*) AS n FROM messages GROUP BY status ORDER BY status"
    ):
        lines.append(f"  {row['status']}: {row['n']}")
    lines.append("By category:")
    for row in conn.execute(
        "SELECT COALESCE(category, '(none)') AS cat, COUNT(*) AS n FROM messages"
        " GROUP BY cat ORDER BY n DESC"
    ):
        lines.append(f"  {row['cat']}: {row['n']}")
    return "\n".join(lines) if len(lines) > 2 else "No messages stored yet."


# ---------------------------------------------------------------------------
# Telegram Bot API


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


# ---------------------------------------------------------------------------
# URL extraction (Telegram entity offsets are UTF-16 code units)


URL_RE = re.compile(r"https?://[^\s<>()\"']+")


def utf16_slice(text, offset, length):
    encoded = text.encode("utf-16-le")
    return encoded[offset * 2:(offset + length) * 2].decode("utf-16-le", errors="ignore")


def extract_urls(text, entities):
    text = text or ""
    urls = []
    for entity in entities or []:
        etype = entity.get("type")
        if etype == "url":
            urls.append(utf16_slice(text, entity.get("offset", 0), entity.get("length", 0)))
        elif etype == "text_link" and entity.get("url"):
            urls.append(entity["url"])
    for match in URL_RE.findall(text):
        urls.append(match.rstrip(".,;"))
    seen = set()
    result = []
    for url in urls:
        url = url.strip()
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


# ---------------------------------------------------------------------------
# LLM classification (DigitalOcean Gradient serverless inference)


class LLMError(Exception):
    pass


_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+")


def _redacted_http_error(exc, access_key):
    try:
        body = exc.read().decode("utf-8")
    except Exception:
        body = ""
    if access_key:
        body = body.replace(access_key, "<redacted>")
    body = _BEARER_PATTERN.sub(r"\1<redacted>", body)
    suffix = f": {body[:500]}" if body else "."
    return f"inference request failed with HTTP {exc.code}{suffix}"


def do_chat(cfg, messages):
    base = cfg.do_base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    payload = {
        "model": cfg.do_model,
        "messages": messages,
        "max_tokens": 300,
        "temperature": 0,
        "stream": False,
    }
    request = Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg.do_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=cfg.llm_timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise LLMError(_redacted_http_error(exc, cfg.do_key)) from exc
    except URLError as exc:
        raise LLMError(f"inference request failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError("inference response was not valid JSON") from exc
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("inference response had no choices")
    return str((choices[0].get("message") or {}).get("content") or "")


def parse_llm_json(text):
    if not text:
        return None
    candidates = [text.strip()]
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        candidates.append(fence.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return None


def validate_category(value, categories):
    value = str(value or "").strip()
    for category in categories:
        if value.casefold() == category.casefold():
            return category
    return None


def build_text_block(raw_text, forward_type, forward_title, urls):
    lines = []
    if forward_title:
        lines.append(f"Forwarded from {forward_type or 'unknown'}: {forward_title}")
    lines.append("Message text:")
    lines.append(raw_text or "(no text)")
    if urls:
        lines.append("URLs:")
        lines.extend(f"- {url}" for url in urls)
    return "\n".join(lines)


MAX_LLM_IMAGE_BYTES = 5 * 1024 * 1024


def build_llm_messages(cfg, text_block, image_paths):
    system = (
        "You categorize messages forwarded into a personal Telegram inbox.\n"
        f"Allowed categories (choose exactly one): {', '.join(cfg.categories)}\n"
        "Reply with ONLY a JSON object: "
        '{"category": "<one of the allowed categories>", '
        '"summary": "<short summary of the message, at most 2 sentences>"}'
    )
    content = [{"type": "text", "text": text_block}]
    used = 0
    for path in image_paths:
        if used >= cfg.max_llm_images:
            break
        try:
            data = Path(path).read_bytes()
        except OSError:
            continue
        if len(data) > MAX_LLM_IMAGE_BYTES:
            continue
        encoded = base64.b64encode(data).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}
        )
        used += 1
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


def classify(cfg, text_block, image_paths):
    messages = build_llm_messages(cfg, text_block, image_paths)
    reply = do_chat(cfg, messages)
    parsed = parse_llm_json(reply)
    category = validate_category((parsed or {}).get("category"), cfg.categories)
    if parsed is None or category is None:
        messages.append({"role": "assistant", "content": reply})
        messages.append({
            "role": "user",
            "content": (
                'Reply with ONLY the JSON object {"category": ..., "summary": ...}. '
                f"The category must be exactly one of: {', '.join(cfg.categories)}"
            ),
        })
        reply = do_chat(cfg, messages)
        parsed = parse_llm_json(reply)
        category = validate_category((parsed or {}).get("category"), cfg.categories)
    if parsed is None:
        summary = (reply or "").strip()[:500] or "(unparseable model reply)"
        return cfg.fallback_category, summary
    summary = str(parsed.get("summary") or "").strip() or "(no summary)"
    return category or cfg.fallback_category, summary


# ---------------------------------------------------------------------------
# Message parsing helpers


def parse_forward_origin(origin):
    if not origin:
        return {}
    otype = origin.get("type")
    info = {"type": otype, "date": origin.get("date")}
    if otype == "channel":
        chat = origin.get("chat") or {}
        info["chat_id"] = chat.get("id")
        info["title"] = chat.get("title") or chat.get("username")
        info["message_id"] = origin.get("message_id")
    elif otype == "user":
        user = origin.get("sender_user") or {}
        name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))
        info["chat_id"] = user.get("id")
        info["title"] = name or user.get("username")
    elif otype == "hidden_user":
        info["title"] = origin.get("sender_user_name")
    elif otype == "chat":
        chat = origin.get("sender_chat") or {}
        info["chat_id"] = chat.get("id")
        info["title"] = chat.get("title")
    return info


def first_text(parts):
    for part in parts:
        text = (part.get("text") or "").strip()
        if text:
            return text
    for part in parts:
        caption = (part.get("caption") or "").strip()
        if caption:
            return caption
    return None


def collect_urls(parts):
    urls = []
    seen = set()
    for part in parts:
        for url in extract_urls(part.get("text"), part.get("entities")) + extract_urls(
            part.get("caption"), part.get("caption_entities")
        ):
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


# ---------------------------------------------------------------------------
# Agent


class Agent:
    def __init__(self, cfg):
        self.cfg = cfg
        self.conn = open_db(cfg.db_path)
        cfg.media_dir.mkdir(parents=True, exist_ok=True)
        self.albums = {}  # media_group_id -> {"parts": [...], "deadline": float}
        self.stop = False
        self.last_sweep = 0.0

    def request_stop(self, signum, _frame):
        log(f"received signal {signum}, shutting down")
        self.stop = True

    # -- Telegram helpers

    def reply(self, chat_id, text, reply_to=None):
        try:
            tg_call(
                self.cfg.token,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text[:4000],
                    "reply_to_message_id": reply_to,
                    "allow_sending_without_reply": True,
                },
            )
        except TelegramError as exc:
            log(f"sendMessage failed: {exc}")

    def download_photo(self, photo):
        unique_id = photo.get("file_unique_id")
        existing = list(self.cfg.media_dir.glob(f"{unique_id}.*"))
        if existing:
            return str(existing[0])
        info = tg_call(self.cfg.token, "getFile", {"file_id": photo.get("file_id")})
        file_path = info.get("file_path") or ""
        ext = Path(file_path).suffix or ".jpg"
        dest = self.cfg.media_dir / f"{unique_id}{ext}"
        tg_download(self.cfg.token, file_path, dest)
        return str(dest)

    # -- Main loop

    def run(self):
        try:
            tg_call(self.cfg.token, "deleteWebhook", {"drop_pending_updates": False})
        except TelegramError as exc:
            log(f"deleteWebhook failed (continuing): {exc}")
        offset = int(kv_get(self.conn, "offset", "0") or 0)
        errors = 0
        log(
            f"polling started (model={self.cfg.do_model}, categories={len(self.cfg.categories)}, "
            f"allowed_chats={len(self.cfg.allowed_chat_ids)}, offset={offset})"
        )
        while not self.stop:
            now = time.time()
            self.flush_albums(now)
            if now - self.last_sweep >= self.cfg.retry_interval:
                self.last_sweep = now
                self.retry_sweep()
            poll_timeout = 2 if self.albums else self.cfg.poll_timeout
            try:
                updates = tg_call(
                    self.cfg.token,
                    "getUpdates",
                    {"offset": offset, "timeout": poll_timeout, "allowed_updates": ["message"]},
                    timeout=poll_timeout + 15,
                )
                errors = 0
            except TelegramError as exc:
                if exc.status == 409:
                    log(f"getUpdates conflict (another poller or webhook active): {exc}")
                    time.sleep(30)
                    continue
                if exc.retry_after:
                    log(f"rate limited, sleeping {exc.retry_after}s")
                    time.sleep(min(int(exc.retry_after), 120))
                    continue
                errors += 1
                delay = min(60, 5 * (2 ** min(errors - 1, 4)))
                log(f"getUpdates failed ({exc}), retrying in {delay}s")
                time.sleep(delay)
                continue
            for update in updates or []:
                try:
                    self.handle_update(update)
                except Exception as exc:  # never let one bad update kill the loop
                    log(f"error handling update {update.get('update_id')}: {exc!r}")
            if updates:
                offset = max(u["update_id"] for u in updates) + 1
                kv_set(self.conn, "offset", offset)
        self.flush_albums(time.time(), force=True)
        log("stopped")

    # -- Update handling

    def handle_update(self, update):
        msg = update.get("message")
        if not msg:
            return
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id not in self.cfg.allowed_chat_ids:
            from_id = (msg.get("from") or {}).get("id")
            log(f"ignored message from chat_id={chat_id} user_id={from_id}")
            return
        text = (msg.get("text") or "").strip()
        if text in ("/start", "/stats") and not msg.get("forward_origin"):
            self.handle_command(chat_id, text)
            return
        group_id = msg.get("media_group_id")
        if group_id:
            buffer = self.albums.setdefault(str(group_id), {"parts": []})
            buffer["parts"].append(msg)
            buffer["deadline"] = time.time() + self.cfg.album_settle
            return
        self.finalize([msg])

    def handle_command(self, chat_id, text):
        if text == "/start":
            self.reply(
                chat_id,
                "tg-ingest-agent: send or forward messages (text, links, photos) and I will "
                "categorize, summarize and store them. /stats shows stored counts.",
            )
        else:
            self.reply(chat_id, stats_text(self.conn))

    def flush_albums(self, now, force=False):
        for group_id in list(self.albums):
            buffer = self.albums[group_id]
            if force or buffer.get("deadline", 0) <= now:
                del self.albums[group_id]
                parts = sorted(buffer["parts"], key=lambda m: m.get("message_id", 0))
                try:
                    self.finalize(parts)
                except Exception as exc:
                    log(f"error finalizing album {group_id}: {exc!r}")

    def finalize(self, parts):
        first = parts[0]
        chat_id = first["chat"]["id"]
        reply_to = first.get("message_id")
        raw_text = first_text(parts)
        urls = collect_urls(parts)
        forward = parse_forward_origin(first.get("forward_origin"))
        row_id = insert_message(
            self.conn,
            {
                "chat_id": chat_id,
                "tg_message_id": first.get("message_id"),
                "media_group_id": first.get("media_group_id"),
                "from_user_id": (first.get("from") or {}).get("id"),
                "forward_origin_type": forward.get("type"),
                "forward_origin_chat_id": forward.get("chat_id"),
                "forward_origin_title": forward.get("title"),
                "forward_origin_message_id": forward.get("message_id"),
                "forward_date": forward.get("date"),
                "received_at": datetime.now(timezone.utc).isoformat(),
                "tg_date": first.get("date"),
                "raw_text": raw_text,
            },
        )
        if row_id is None:
            log(f"skipping redelivered message chat_id={chat_id} message_id={first.get('message_id')}")
            return
        for url in urls:
            insert_url(self.conn, row_id, url)
        image_count = 0
        for part in parts:
            photo_sizes = part.get("photo") or []
            if photo_sizes:
                largest = photo_sizes[-1]  # Telegram orders PhotoSize ascending
                try:
                    local_path = self.download_photo(largest)
                except TelegramError as exc:
                    log(f"photo download failed for message #{row_id}: {exc}")
                    local_path = None
                insert_image(self.conn, row_id, part.get("message_id"), largest, local_path)
                image_count += 1
                continue
            document = part.get("document") or {}
            if str(document.get("mime_type") or "").startswith("image/"):
                # v1 limitation: uncompressed image documents are stored as
                # metadata only and not sent to the LLM.
                log(f"image document stored metadata-only for message #{row_id}")
                insert_image(self.conn, row_id, part.get("message_id"), document, None)
        log(
            f"stored message #{row_id} (chat={chat_id}, images={image_count}, urls={len(urls)}, "
            f"forward={forward.get('title') or '-'})"
        )
        if forward.get("chat_id") is not None and forward.get("message_id") is not None:
            original = find_forward_duplicate(
                self.conn, forward["chat_id"], forward["message_id"], row_id
            )
            if original:
                mark_duplicate(self.conn, row_id, original)
                log(f"message #{row_id} is a duplicate of #{original['id']}, skipping LLM")
                if original["category"]:
                    self.reply(
                        chat_id,
                        f"Duplicate of #{original['id']}.\nCategory: {original['category']}\n"
                        f"Summary: {original['summary']}",
                        reply_to,
                    )
                else:
                    self.reply(
                        chat_id,
                        f"Duplicate of #{original['id']} (classification still pending).",
                        reply_to,
                    )
                return
        result = self.classify_row(get_message(self.conn, row_id))
        if result:
            category, summary = result
            self.reply(
                chat_id,
                f"Category: {category}\nSummary: {summary[:500]}\n"
                f"(saved #{row_id}, {image_count} images, {len(urls)} URLs)",
                reply_to,
            )
        else:
            self.reply(
                chat_id,
                f"Stored #{row_id}. Classification failed, will retry.",
                reply_to,
            )

    # -- Classification

    def classify_row(self, row):
        row_id = row["id"]
        urls = [r["url"] for r in message_urls(self.conn, row_id)]
        image_paths = [r["local_path"] for r in message_images(self.conn, row_id) if r["local_path"]]
        if not (row["raw_text"] or urls or image_paths):
            set_classification(
                self.conn, row_id, self.cfg.fallback_category, "(no analyzable content)", None
            )
            return self.cfg.fallback_category, "(no analyzable content)"
        text_block = build_text_block(
            row["raw_text"], row["forward_origin_type"], row["forward_origin_title"], urls
        )
        try:
            category, summary = classify(self.cfg, text_block, image_paths)
        except LLMError as exc:
            attempts = bump_attempts(self.conn, row_id)
            if attempts >= self.cfg.llm_max_attempts:
                mark_failed(self.conn, row_id)
                log(f"message #{row_id} marked failed after {attempts} attempts: {exc}")
            else:
                log(f"classification failed for message #{row_id} (attempt {attempts}): {exc}")
            return None
        set_classification(self.conn, row_id, category, summary, self.cfg.do_model)
        log(f"classified message #{row_id} as {category}")
        return category, summary

    def retry_sweep(self):
        rows = pending_messages(self.conn, self.cfg.llm_max_attempts)
        for row in rows:
            if self.stop:
                return
            result = self.classify_row(row)
            if result:
                category, summary = result
                self.reply(
                    row["chat_id"],
                    f"(retry) Category: {category}\nSummary: {summary[:500]}\n(saved #{row['id']})",
                    row["tg_message_id"],
                )


def main():
    cfg = load_config()
    agent = Agent(cfg)
    signal.signal(signal.SIGTERM, agent.request_stop)
    signal.signal(signal.SIGINT, agent.request_stop)
    agent.run()


if __name__ == "__main__":
    main()
