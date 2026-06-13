#!/usr/bin/env python3
"""SQLite storage layer for tg-ingest-agent.

messages.status lifecycle:
  pending   -> stored, awaiting an LLM suggestion (retried on failure)
  suggested -> LLM suggestion sent, awaiting operator confirmation
  confirmed -> operator confirmed (category is final)
  failed    -> LLM gave up after LLM_MAX_ATTEMPTS
  duplicate -> re-forward of an already stored channel post
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  norm_key TEXT NOT NULL UNIQUE,
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
  forward_origin_username TEXT,
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
  object_key TEXT,
  width INTEGER,
  height INTEGER,
  file_size INTEGER
);

CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  month TEXT NOT NULL,
  skill TEXT NOT NULL,
  kind TEXT NOT NULL,
  model TEXT NOT NULL,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  seconds REAL,
  cost_usd REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  skill TEXT NOT NULL,
  input_digest TEXT,
  suggested TEXT,
  corrected TEXT
);

CREATE TABLE IF NOT EXISTS preferences (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_actions (
  chat_id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation (
  id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  ts TEXT NOT NULL,
  role TEXT NOT NULL,
  text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  fact TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  embedding TEXT
);

CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  chat_id INTEGER,
  kind TEXT NOT NULL,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  due_utc TEXT NOT NULL,
  recurrence TEXT NOT NULL DEFAULT 'none',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  last_fired_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
CREATE INDEX IF NOT EXISTS idx_messages_fwd
  ON messages(forward_origin_chat_id, forward_origin_message_id);
CREATE INDEX IF NOT EXISTS idx_messages_suggestion
  ON messages(chat_id, suggestion_message_id);
CREATE INDEX IF NOT EXISTS idx_usage_day ON llm_usage(day);
CREATE INDEX IF NOT EXISTS idx_usage_month ON llm_usage(month);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_utc);
CREATE INDEX IF NOT EXISTS idx_issues_ts ON issues(ts);
CREATE INDEX IF NOT EXISTS idx_conversation_chat ON conversation(chat_id, id);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def open_db(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn):
    """Additive migrations for databases created by older versions
    (CREATE IF NOT EXISTS does not alter existing tables)."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    if "forward_origin_username" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN forward_origin_username TEXT")
    image_columns = {row["name"] for row in conn.execute("PRAGMA table_info(images)")}
    if "object_key" not in image_columns:
        conn.execute("ALTER TABLE images ADD COLUMN object_key TEXT")


# -- kv ----------------------------------------------------------------------

def kv_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()


# -- categories (norm_key uses Python casefold: SQLite NOCASE is ASCII-only
#    and would treat 'Крипта' and 'крипта' as different categories) ----------

def ensure_category(conn, name):
    """Insert the category if new (case-insensitive incl. Cyrillic); return
    the canonical stored name."""
    norm = name.casefold()
    row = conn.execute("SELECT name FROM categories WHERE norm_key = ?", (norm,)).fetchone()
    if row:
        return row["name"]
    conn.execute(
        "INSERT INTO categories (name, norm_key, created_at) VALUES (?, ?, ?)",
        (name, norm, _now()),
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


def category_counts(conn):
    return conn.execute(
        "SELECT c.name AS name,"
        " (SELECT COUNT(*) FROM messages m WHERE m.category = c.name AND m.status = 'confirmed') AS n"
        " FROM categories c ORDER BY n DESC, c.name",
    ).fetchall()


# -- messages ----------------------------------------------------------------

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


def set_facts(conn, message_id, facts):
    """Replace the key facts of a message (idempotent for retries)."""
    conn.execute("DELETE FROM facts WHERE message_id = ?", (message_id,))
    for fact in facts:
        conn.execute("INSERT INTO facts (message_id, fact) VALUES (?, ?)", (message_id, fact))
    conn.commit()


def message_facts(conn, message_id):
    return conn.execute(
        "SELECT fact FROM facts WHERE message_id = ? ORDER BY id", (message_id,)
    ).fetchall()


def message_urls(conn, message_id):
    return conn.execute("SELECT * FROM urls WHERE message_id = ? ORDER BY id", (message_id,)).fetchall()


def message_images(conn, message_id):
    return conn.execute("SELECT * FROM images WHERE message_id = ? ORDER BY id", (message_id,)).fetchall()


def set_image_object_key(conn, image_id, object_key):
    conn.execute("UPDATE images SET object_key = ? WHERE id = ?", (object_key, image_id))
    conn.commit()


def set_chunks(conn, message_id, chunks):
    """Replace a message's embedding chunks (idempotent for re-indexing).
    chunks: list of (text, embedding_list_or_None)."""
    import json as _json
    conn.execute("DELETE FROM chunks WHERE message_id = ?", (message_id,))
    for i, (text, embedding) in enumerate(chunks):
        conn.execute(
            "INSERT INTO chunks (message_id, chunk_index, text, embedding) VALUES (?, ?, ?, ?)",
            (message_id, i, text, _json.dumps(embedding) if embedding is not None else None),
        )
    conn.commit()


def all_embedded_chunks(conn):
    """Every chunk that has an embedding, with its message's category/title
    for grounding. Returns rows: message_id, text, embedding(JSON), category,
    suggested_category, forward_origin_title."""
    return conn.execute(
        "SELECT c.message_id AS message_id, c.text AS text, c.embedding AS embedding,"
        " m.category AS category, m.suggested_category AS suggested_category,"
        " m.forward_origin_title AS title"
        " FROM chunks c JOIN messages m ON m.id = c.message_id"
        " WHERE c.embedding IS NOT NULL"
    ).fetchall()


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


def latest_suggested(conn, chat_id):
    return conn.execute(
        "SELECT * FROM messages WHERE chat_id = ? AND status = 'suggested'"
        " ORDER BY id DESC LIMIT 1",
        (chat_id,),
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


def list_messages(conn, category=None, query=None, limit=10):
    """Recent stored messages, optionally filtered by category (exact,
    case-insensitive incl. Cyrillic) or a substring query over text, summary,
    key facts, category, and source. Filtering happens in Python: SQL
    lower()/NOCASE are ASCII-only."""
    rows = conn.execute(
        "SELECT * FROM messages WHERE status IN ('confirmed', 'suggested')"
        " ORDER BY id DESC LIMIT 200"
    ).fetchall()
    facts_by_message = {}
    if query:
        for row in conn.execute(
            "SELECT message_id, GROUP_CONCAT(fact, ' ') AS f FROM facts GROUP BY message_id"
        ):
            facts_by_message[row["message_id"]] = row["f"]
    result = []
    for row in rows:
        row_category = row["category"] or row["suggested_category"] or ""
        if category and row_category.casefold() != str(category).casefold():
            continue
        if query:
            haystack = " ".join(filter(None, [
                row["raw_text"], row["summary"], row_category, row["forward_origin_title"],
                facts_by_message.get(row["id"]),
            ])).casefold()
            if str(query).casefold() not in haystack:
                continue
        result.append(row)
        if len(result) >= limit:
            break
    return result


def status_counts(conn):
    return conn.execute(
        "SELECT status, COUNT(*) AS n FROM messages GROUP BY status ORDER BY status"
    ).fetchall()


def delete_message(conn, message_id):
    """Delete a message row (urls/images cascade); returns media paths to
    unlink. Other rows referencing it as duplicate_of keep their copy."""
    paths = [r["local_path"] for r in message_images(conn, message_id) if r["local_path"]]
    conn.execute("UPDATE messages SET duplicate_of = NULL WHERE duplicate_of = ?", (message_id,))
    conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
    conn.commit()
    return paths


PURGE_SCOPES = ("all", "category", "stats", "reminders")


def _messages_in_category(conn, category):
    """Message ids whose (confirmed or suggested) category matches, Cyrillic
    case-insensitively (SQL lower/NOCASE is ASCII-only)."""
    target = str(category or "").casefold()
    ids = []
    for row in conn.execute("SELECT id, category, suggested_category FROM messages"):
        name = (row["category"] or row["suggested_category"] or "").casefold()
        if name == target:
            ids.append(row["id"])
    return ids


def purge_preview(conn, scope, category=None):
    """Count what a purge would remove, without deleting. llm_usage (spend
    history) and preferences (identity/config) are NEVER purged."""
    def count(sql, args=()):
        return conn.execute(sql, args).fetchone()[0]
    info = {"scope": scope}
    if scope == "all":
        info["messages"] = count("SELECT COUNT(*) FROM messages")
        info["reminders"] = count("SELECT COUNT(*) FROM reminders WHERE status='active'")
        info["categories"] = count("SELECT COUNT(*) FROM categories")
        info["issues"] = count("SELECT COUNT(*) FROM issues")
    elif scope == "category":
        info["messages"] = len(_messages_in_category(conn, category))
        info["category"] = category
    elif scope == "stats":
        info["categories"] = count("SELECT COUNT(*) FROM categories")
        info["issues"] = count("SELECT COUNT(*) FROM issues")
        info["feedback"] = count("SELECT COUNT(*) FROM feedback")
    elif scope == "reminders":
        info["reminders"] = count("SELECT COUNT(*) FROM reminders WHERE status='active'")
    return info


def purge_execute(conn, scope, category=None):
    """Run the purge. Returns (summary_dict, media_paths_to_unlink).
    Preserves llm_usage and preferences in every scope."""
    info = purge_preview(conn, scope, category)
    paths = []
    if scope == "all":
        paths = [r["local_path"] for r in
                 conn.execute("SELECT local_path FROM images WHERE local_path IS NOT NULL")]
        for table in ("facts", "chunks", "urls", "images", "messages", "categories", "issues",
                      "feedback", "conversation"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM reminders WHERE status='active'")
        conn.execute("DELETE FROM pending_actions")
    elif scope == "category":
        ids = _messages_in_category(conn, category)
        for mid in ids:
            paths.extend(delete_message(conn, mid))
        if category:
            conn.execute("DELETE FROM categories WHERE norm_key = ?", (str(category).casefold(),))
    elif scope == "stats":
        for table in ("categories", "issues", "feedback", "conversation"):
            conn.execute(f"DELETE FROM {table}")
    elif scope == "reminders":
        conn.execute("DELETE FROM reminders WHERE status='active'")
    conn.commit()
    return info, [p for p in paths if p]


def habit_streak(conn, fwd_chat_id):
    """Length and category of the current same-category confirmation streak
    for a forward source; (None, 0) when the streak is broken or empty."""
    rows = conn.execute(
        "SELECT category FROM messages WHERE forward_origin_chat_id = ?"
        " AND status = 'confirmed' ORDER BY id DESC LIMIT 50",
        (fwd_chat_id,),
    ).fetchall()
    if not rows:
        return None, 0
    head = rows[0]["category"]
    streak = 0
    for row in rows:
        if row["category"] == head:
            streak += 1
        else:
            break
    return head, streak


# -- llm usage ---------------------------------------------------------------

def usage_add(conn, skill, kind, model, tokens_in=0, tokens_out=0, seconds=None, cost_usd=0.0):
    ts = _now()
    conn.execute(
        "INSERT INTO llm_usage (ts, day, month, skill, kind, model, tokens_in, tokens_out,"
        " seconds, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, ts[:10], ts[:7], skill, kind, model, tokens_in, tokens_out, seconds, cost_usd),
    )
    conn.commit()


def usage_total(conn, period):
    """period: 'day' or 'month' (current)."""
    ts = _now()
    column, value = ("day", ts[:10]) if period == "day" else ("month", ts[:7])
    row = conn.execute(
        f"SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_usage WHERE {column} = ?", (value,)
    ).fetchone()
    return float(row["c"])


def usage_breakdown(conn, period, by="skill"):
    assert by in ("skill", "model")
    ts = _now()
    if period == "day":
        where, value = "day = ?", ts[:10]
    elif period == "week":
        where, value = "day >= ?", _week_start(ts)
    else:
        where, value = "month = ?", ts[:7]
    return conn.execute(
        f"SELECT {by} AS k, COUNT(*) AS calls, SUM(tokens_in) AS tin, SUM(tokens_out) AS tout,"
        f" COALESCE(SUM(cost_usd), 0) AS cost FROM llm_usage WHERE {where}"
        f" GROUP BY {by} ORDER BY cost DESC",
        (value,),
    ).fetchall()


def _week_start(ts):
    day = datetime.fromisoformat(ts).date()
    from datetime import timedelta
    return (day - timedelta(days=day.weekday())).isoformat()


# -- feedback / preferences / pending / conversation -------------------------

def feedback_add(conn, skill, input_digest, suggested, corrected):
    conn.execute(
        "INSERT INTO feedback (ts, skill, input_digest, suggested, corrected) VALUES (?, ?, ?, ?, ?)",
        (_now(), skill, input_digest, suggested, corrected),
    )
    conn.commit()


def feedback_recent(conn, skill, limit=5):
    return conn.execute(
        "SELECT * FROM feedback WHERE skill = ? ORDER BY id DESC LIMIT ?", (skill, limit)
    ).fetchall()


def pref_set(conn, key, value):
    conn.execute(
        "INSERT OR REPLACE INTO preferences (key, value, updated_at) VALUES (?, ?, ?)",
        (key, str(value), _now()),
    )
    conn.commit()


def pref_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM preferences WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def pref_all(conn):
    return conn.execute("SELECT * FROM preferences ORDER BY key").fetchall()


def pref_delete(conn, key):
    cur = conn.execute("DELETE FROM preferences WHERE key = ?", (key,))
    conn.commit()
    return cur.rowcount > 0


def pending_set(conn, chat_id, kind, payload, ttl_seconds=3600):
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT OR REPLACE INTO pending_actions (chat_id, kind, payload, created_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (chat_id, kind, json.dumps(payload, ensure_ascii=False), now.isoformat(),
         (now + timedelta(seconds=ttl_seconds)).isoformat()),
    )
    conn.commit()


def pending_get(conn, chat_id):
    row = conn.execute(
        "SELECT * FROM pending_actions WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    if not row:
        return None
    if row["expires_at"] < _now():
        pending_clear(conn, chat_id)
        return None
    return {"kind": row["kind"], "payload": json.loads(row["payload"])}


def pending_clear(conn, chat_id):
    conn.execute("DELETE FROM pending_actions WHERE chat_id = ?", (chat_id,))
    conn.commit()


def convo_add(conn, chat_id, role, text):
    conn.execute(
        "INSERT INTO conversation (chat_id, ts, role, text) VALUES (?, ?, ?, ?)",
        (chat_id, _now(), role, text[:1000]),
    )
    conn.execute(
        "DELETE FROM conversation WHERE chat_id = ? AND id NOT IN"
        " (SELECT id FROM conversation WHERE chat_id = ? ORDER BY id DESC LIMIT 30)",
        (chat_id, chat_id),
    )
    conn.commit()


def convo_recent(conn, chat_id, limit=10):
    rows = conn.execute(
        "SELECT role, text FROM conversation WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
        (chat_id, limit),
    ).fetchall()
    return list(reversed(rows))


# -- issues (communication problems, for weekly/on-demand summaries) ----------

def issue_add(conn, chat_id, kind, detail=""):
    ts = _now()
    conn.execute(
        "INSERT INTO issues (ts, day, chat_id, kind, detail) VALUES (?, ?, ?, ?, ?)",
        (ts, ts[:10], chat_id, kind, str(detail or "")[:300]),
    )
    conn.commit()


def issue_counts(conn, since_iso):
    return conn.execute(
        "SELECT kind, COUNT(*) AS n FROM issues WHERE ts >= ? GROUP BY kind ORDER BY n DESC",
        (since_iso,),
    ).fetchall()


def issues_recent(conn, since_iso, limit=5):
    return conn.execute(
        "SELECT * FROM issues WHERE ts >= ? ORDER BY id DESC LIMIT ?",
        (since_iso, limit),
    ).fetchall()


# -- reminders ----------------------------------------------------------------

def reminder_add(conn, chat_id, title, due_utc, recurrence="none"):
    cur = conn.execute(
        "INSERT INTO reminders (chat_id, title, due_utc, recurrence, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (chat_id, title, due_utc, recurrence, _now()),
    )
    conn.commit()
    return cur.lastrowid


def reminder_get(conn, rid):
    return conn.execute("SELECT * FROM reminders WHERE id = ?", (rid,)).fetchone()


def reminders_active(conn, chat_id):
    return conn.execute(
        "SELECT * FROM reminders WHERE chat_id = ? AND status = 'active' ORDER BY due_utc",
        (chat_id,),
    ).fetchall()


def reminders_due(conn, now_iso):
    return conn.execute(
        "SELECT * FROM reminders WHERE status = 'active' AND due_utc <= ? ORDER BY due_utc",
        (now_iso,),
    ).fetchall()


def reminder_update_due(conn, rid, due_utc):
    conn.execute(
        "UPDATE reminders SET due_utc = ?, last_fired_at = ? WHERE id = ?",
        (due_utc, _now(), rid),
    )
    conn.commit()


def reminder_close(conn, rid, status="done"):
    conn.execute(
        "UPDATE reminders SET status = ?, last_fired_at = ? WHERE id = ?",
        (status, _now(), rid),
    )
    conn.commit()
