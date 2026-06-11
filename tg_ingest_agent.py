#!/usr/bin/env python3
"""tg-ingest-agent: Telegram message ingest + LLM categorization.

Receives messages (text, photos, forwarded channel posts) from allowed chats
via long polling, stores them in SQLite, downloads photos, and asks a
vision-capable LLM on DigitalOcean Gradient serverless inference to suggest a
category (reusing the taxonomy built up from previously confirmed categories)
and a short summary. The suggestion is sent back with inline buttons; the
operator confirms it, picks an alternative, or replies with a custom category.
Only confirmed categories enter the taxonomy.

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
    # Optional seed taxonomy; the real category list grows from confirmed
    # suggestions in the categories table.
    cfg.seed_categories = load_categories(env)
    cfg.fallback_category = (env.get("FALLBACK_CATEGORY") or "uncategorized").strip()
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
#
# messages.status lifecycle:
#   pending   -> stored, awaiting an LLM suggestion (retried on failure)
#   suggested -> LLM suggestion sent, awaiting operator confirmation
#   confirmed -> operator confirmed (category is final)
#   failed    -> LLM gave up after LLM_MAX_ATTEMPTS
#   duplicate -> re-forward of an already stored channel post


SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  created_at TEXT NOT NULL
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
  suggested_category TEXT,
  category TEXT,
  summary TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  llm_model TEXT,
  llm_attempts INTEGER NOT NULL DEFAULT 0,
  suggestion_message_id INTEGER,
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
CREATE INDEX IF NOT EXISTS idx_messages_suggestion
  ON messages(chat_id, suggestion_message_id);
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


def ensure_category(conn, name):
    """Insert the category if new (case-insensitive); return canonical name."""
    row = conn.execute(
        "SELECT name FROM categories WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        return row["name"]
    conn.execute(
        "INSERT INTO categories (name, created_at) VALUES (?, ?)",
        (name, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return name


def known_categories(conn, limit=50):
    rows = conn.execute(
        "SELECT c.name AS name,"
        " (SELECT COUNT(*) FROM messages m WHERE m.category = c.name AND m.status = 'confirmed') AS n"
        " FROM categories c ORDER BY n DESC, c.name LIMIT ?",
        (limit,),
    ).fetchall()
    return [r["name"] for r in rows]


def categories_text(conn):
    rows = conn.execute(
        "SELECT c.name AS name,"
        " (SELECT COUNT(*) FROM messages m WHERE m.category = c.name AND m.status = 'confirmed') AS n"
        " FROM categories c ORDER BY n DESC, c.name",
    ).fetchall()
    if not rows:
        return "No categories yet. They are created when you confirm suggestions."
    return "Categories (confirmed messages):\n" + "\n".join(
        f"  {r['name']}: {r['n']}" for r in rows
    )


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
        " AND id != ? AND status != 'duplicate' ORDER BY id LIMIT 1",
        (fwd_chat_id, fwd_message_id, exclude_id),
    ).fetchone()


def mark_duplicate(conn, message_id, original):
    conn.execute(
        "UPDATE messages SET duplicate_of = ?, suggested_category = ?, category = ?,"
        " summary = ?, llm_model = ?, status = 'duplicate' WHERE id = ?",
        (
            original["id"],
            original["suggested_category"],
            original["category"],
            original["summary"],
            original["llm_model"],
            message_id,
        ),
    )
    conn.commit()


def set_suggestion(conn, message_id, suggested_category, summary, model):
    conn.execute(
        "UPDATE messages SET suggested_category = ?, summary = ?, llm_model = ?,"
        " status = 'suggested' WHERE id = ?",
        (suggested_category, summary, model, message_id),
    )
    conn.commit()


def set_suggestion_message(conn, message_id, tg_suggestion_message_id):
    conn.execute(
        "UPDATE messages SET suggestion_message_id = ? WHERE id = ?",
        (tg_suggestion_message_id, message_id),
    )
    conn.commit()


def confirm_category(conn, message_id, category):
    conn.execute(
        "UPDATE messages SET category = ?, status = 'confirmed' WHERE id = ?",
        (category, message_id),
    )
    conn.commit()


def find_by_suggestion_message(conn, chat_id, suggestion_message_id):
    if not suggestion_message_id:
        return None
    return conn.execute(
        "SELECT * FROM messages WHERE chat_id = ? AND suggestion_message_id = ? LIMIT 1",
        (chat_id, suggestion_message_id),
    ).fetchone()


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
        " ORDER BY id LIMIT ?",
        (max_attempts, limit),
    ).fetchall()


def stats_text(conn):
    status_rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM messages GROUP BY status ORDER BY status"
    ).fetchall()
    if not status_rows:
        return "No messages stored yet."
    lines = ["By status:"]
    lines.extend(f"  {row['status']}: {row['n']}" for row in status_rows)
    category_rows = conn.execute(
        "SELECT category AS cat, COUNT(*) AS n FROM messages"
        " WHERE status = 'confirmed' GROUP BY cat ORDER BY n DESC"
    ).fetchall()
    if category_rows:
        lines.append("Confirmed by category:")
        lines.extend(f"  {row['cat']}: {row['n']}" for row in category_rows)
    return "\n".join(lines)


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
# LLM suggestion (DigitalOcean Gradient serverless inference)


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


MAX_CATEGORY_CHARS = 40


def normalize_category(value):
    """Collapse whitespace, cap length; None when nothing usable remains."""
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:MAX_CATEGORY_CHARS].strip() or None


def match_category(value, categories):
    """Return the canonical (existing) spelling of value, or None."""
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


def build_llm_messages(cfg, known, text_block, image_paths):
    if known:
        taxonomy = (
            "Categories used so far: " + ", ".join(known) + "\n"
            "Prefer one of these when it fits; propose a new short category only when none fits."
        )
    else:
        taxonomy = "There are no categories yet; propose a short (1-3 word) category."
    system = (
        "You categorize messages forwarded into a personal Telegram inbox.\n"
        f"{taxonomy}\n"
        "Reply with ONLY a JSON object: "
        '{"category": "<best category>", '
        '"alternatives": ["<up to 2 other plausible categories>"], '
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


def suggest(cfg, known, text_block, image_paths):
    """Ask the LLM for a category suggestion.

    Returns (category, alternatives, summary); never raises on bad model
    output (falls back to cfg.fallback_category), only on transport errors.
    """
    messages = build_llm_messages(cfg, known, text_block, image_paths)
    reply = do_chat(cfg, messages)
    parsed = parse_llm_json(reply)
    category = normalize_category((parsed or {}).get("category"))
    if parsed is None or category is None:
        messages.append({"role": "assistant", "content": reply})
        messages.append({
            "role": "user",
            "content": (
                'Reply with ONLY the JSON object '
                '{"category": ..., "alternatives": [...], "summary": ...}.'
            ),
        })
        reply = do_chat(cfg, messages)
        parsed = parse_llm_json(reply)
        category = normalize_category((parsed or {}).get("category"))
    if parsed is None or category is None:
        summary = (reply or "").strip()[:500] or "(unparseable model reply)"
        return cfg.fallback_category, [], summary
    category = match_category(category, known) or category
    alternatives = []
    raw_alternatives = parsed.get("alternatives")
    if isinstance(raw_alternatives, list):
        for alt in raw_alternatives[:5]:
            alt = normalize_category(alt)
            if not alt:
                continue
            alt = match_category(alt, known) or alt
            taken = [category.casefold()] + [a.casefold() for a in alternatives]
            if alt.casefold() not in taken:
                alternatives.append(alt)
    summary = str(parsed.get("summary") or "").strip() or "(no summary)"
    return category, alternatives[:3], summary


# ---------------------------------------------------------------------------
# Confirmation keyboard (callback_data is capped at 64 bytes by Telegram)


CALLBACK_BYTE_LIMIT = 64


def build_suggestion_keyboard(row_id, category, alternatives):
    keyboard = [[{"text": f"✅ {category}", "callback_data": f"s|{row_id}"}]]
    alt_row = []
    for alt in alternatives:
        if alt.casefold() == category.casefold():
            continue
        data = f"a|{row_id}|{alt}"
        if len(data.encode("utf-8")) > CALLBACK_BYTE_LIMIT:
            continue
        alt_row.append({"text": alt, "callback_data": data})
    if alt_row:
        keyboard.append(alt_row[:3])
    return keyboard


def parse_callback_data(data):
    """Parse 's|<row_id>' or 'a|<row_id>|<category>'; None when malformed."""
    parts = str(data or "").split("|", 2)
    if len(parts) < 2:
        return None
    try:
        row_id = int(parts[1])
    except ValueError:
        return None
    if parts[0] == "s" and len(parts) == 2:
        return ("suggested", row_id, None)
    if parts[0] == "a" and len(parts) == 3 and parts[2].strip():
        return ("named", row_id, parts[2].strip())
    return None


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
        for name in cfg.seed_categories:
            normalized = normalize_category(name)
            if normalized:
                ensure_category(self.conn, normalized)
        self.albums = {}  # media_group_id -> {"parts": [...], "deadline": float}
        self.stop = False
        self.last_sweep = 0.0

    def request_stop(self, signum, _frame):
        log(f"received signal {signum}, shutting down")
        self.stop = True

    # -- Telegram helpers

    def reply(self, chat_id, text, reply_to=None, reply_markup=None):
        try:
            return tg_call(
                self.cfg.token,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text[:4000],
                    "reply_to_message_id": reply_to,
                    "allow_sending_without_reply": True,
                    "reply_markup": reply_markup,
                },
            )
        except TelegramError as exc:
            log(f"sendMessage failed: {exc}")
            return None

    def answer_callback(self, callback_id, text):
        try:
            tg_call(
                self.cfg.token,
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": text[:200]},
            )
        except TelegramError as exc:
            log(f"answerCallbackQuery failed: {exc}")

    def edit_suggestion_message(self, chat_id, message_id, row):
        if not message_id:
            return
        summary = (row["summary"] or "")[:500]
        try:
            tg_call(
                self.cfg.token,
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"Category: {row['category']} ✅\nSummary: {summary}\n(#{row['id']})",
                },
            )
        except TelegramError as exc:
            log(f"editMessageText failed: {exc}")

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
            f"polling started (model={self.cfg.do_model}, "
            f"known_categories={len(known_categories(self.conn))}, "
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
                    {
                        "offset": offset,
                        "timeout": poll_timeout,
                        "allowed_updates": ["message", "callback_query"],
                    },
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
        callback = update.get("callback_query")
        if callback:
            self.handle_callback(callback)
            return
        msg = update.get("message")
        if not msg:
            return
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id not in self.cfg.allowed_chat_ids:
            from_id = (msg.get("from") or {}).get("id")
            log(f"ignored message from chat_id={chat_id} user_id={from_id}")
            return
        text = (msg.get("text") or "").strip()
        if text in ("/start", "/stats", "/categories") and not msg.get("forward_origin"):
            self.handle_command(chat_id, text)
            return
        reply_to_msg = msg.get("reply_to_message")
        if reply_to_msg and text and not msg.get("forward_origin"):
            row = find_by_suggestion_message(self.conn, chat_id, reply_to_msg.get("message_id"))
            if row:
                self.handle_correction(row, chat_id, text, msg.get("message_id"))
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
                "tg-ingest-agent: send or forward messages (text, links, photos). I store "
                "them, suggest a category and summary, and you confirm with the buttons or "
                "by replying with your own category. /stats shows counts, /categories the "
                "taxonomy.",
            )
        elif text == "/stats":
            self.reply(chat_id, stats_text(self.conn))
        else:
            self.reply(chat_id, categories_text(self.conn))

    def handle_callback(self, callback):
        callback_id = callback.get("id")
        msg = callback.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        from_id = (callback.get("from") or {}).get("id")
        if chat_id not in self.cfg.allowed_chat_ids and from_id not in self.cfg.allowed_chat_ids:
            self.answer_callback(callback_id, "Not allowed.")
            return
        parsed = parse_callback_data(callback.get("data"))
        if not parsed:
            self.answer_callback(callback_id, "Unknown action.")
            return
        kind, row_id, name = parsed
        row = get_message(self.conn, row_id)
        if not row:
            self.answer_callback(callback_id, "Unknown message.")
            return
        if row["status"] == "confirmed":
            self.answer_callback(callback_id, f"Already confirmed: {row['category']}")
            return
        category = name if kind == "named" else (row["suggested_category"] or self.cfg.fallback_category)
        canonical = ensure_category(self.conn, category)
        confirm_category(self.conn, row_id, canonical)
        log(f"message #{row_id} confirmed as {canonical} (via button)")
        self.answer_callback(callback_id, f"Saved: {canonical}")
        self.edit_suggestion_message(
            chat_id, msg.get("message_id") or row["suggestion_message_id"], get_message(self.conn, row_id)
        )

    def handle_correction(self, row, chat_id, text, reply_to):
        if row["status"] == "confirmed":
            self.reply(chat_id, f"#{row['id']} is already confirmed as {row['category']}.", reply_to)
            return
        category = normalize_category(text)
        if not category:
            return
        canonical = ensure_category(self.conn, category)
        confirm_category(self.conn, row["id"], canonical)
        log(f"message #{row['id']} confirmed as {canonical} (via reply)")
        self.reply(chat_id, f"Saved: {canonical} (#{row['id']})", reply_to)
        self.edit_suggestion_message(chat_id, row["suggestion_message_id"], get_message(self.conn, row["id"]))

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
                if original["status"] == "confirmed":
                    detail = f"category: {original['category']}"
                elif original["suggested_category"]:
                    detail = f"suggested {original['suggested_category']}, awaiting confirmation"
                else:
                    detail = "classification still pending"
                self.reply(chat_id, f"Duplicate of #{original['id']} ({detail}).", reply_to)
                return
        suggestion = self.suggest_row(get_message(self.conn, row_id))
        if suggestion:
            category, alternatives, summary = suggestion
            self.send_suggestion(
                row_id, chat_id, reply_to, category, alternatives, summary,
                f"(saved #{row_id}, {image_count} images, {len(urls)} URLs)",
            )
        else:
            self.reply(
                chat_id,
                f"Stored #{row_id}. Could not get a suggestion, will retry.",
                reply_to,
            )

    # -- Suggestion flow

    def suggest_row(self, row):
        """Get an LLM suggestion for a stored row; returns (category,
        alternatives, summary) or None when the LLM call failed."""
        row_id = row["id"]
        urls = [r["url"] for r in message_urls(self.conn, row_id)]
        image_paths = [r["local_path"] for r in message_images(self.conn, row_id) if r["local_path"]]
        known = known_categories(self.conn)
        if not (row["raw_text"] or urls or image_paths):
            category, alternatives, summary = self.cfg.fallback_category, [], "(no analyzable content)"
        else:
            text_block = build_text_block(
                row["raw_text"], row["forward_origin_type"], row["forward_origin_title"], urls
            )
            try:
                category, alternatives, summary = suggest(self.cfg, known, text_block, image_paths)
            except LLMError as exc:
                attempts = bump_attempts(self.conn, row_id)
                if attempts >= self.cfg.llm_max_attempts:
                    mark_failed(self.conn, row_id)
                    log(f"message #{row_id} marked failed after {attempts} attempts: {exc}")
                else:
                    log(f"suggestion failed for message #{row_id} (attempt {attempts}): {exc}")
                return None
        set_suggestion(self.conn, row_id, category, summary, self.cfg.do_model)
        log(f"suggested {category} for message #{row_id}")
        return category, alternatives, summary

    def send_suggestion(self, row_id, chat_id, reply_to, category, alternatives, summary, counts_line):
        keyboard = build_suggestion_keyboard(row_id, category, alternatives)
        result = self.reply(
            chat_id,
            f"Suggested category: {category}\nSummary: {summary[:500]}\n{counts_line}\n"
            "Tap a button to confirm, or reply to this message with a different category.",
            reply_to,
            reply_markup={"inline_keyboard": keyboard},
        )
        if result and result.get("message_id"):
            set_suggestion_message(self.conn, row_id, result["message_id"])

    def retry_sweep(self):
        rows = pending_messages(self.conn, self.cfg.llm_max_attempts)
        for row in rows:
            if self.stop:
                return
            suggestion = self.suggest_row(row)
            if suggestion:
                category, alternatives, summary = suggestion
                self.send_suggestion(
                    row["id"], row["chat_id"], row["tg_message_id"],
                    category, alternatives, summary, f"(saved #{row['id']}, retried)",
                )


def main():
    cfg = load_config()
    agent = Agent(cfg)
    signal.signal(signal.SIGTERM, agent.request_stop)
    signal.signal(signal.SIGINT, agent.request_stop)
    agent.run()


if __name__ == "__main__":
    main()
