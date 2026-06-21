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
  created_at TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'inbox'  -- 'inbox' (one-time) | 'journal' (long-term, append-only)
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
  cost_usd REAL NOT NULL DEFAULT 0,
  trace_id TEXT
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
  detail TEXT,
  trace_id TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  due_utc TEXT NOT NULL,
  recurrence TEXT NOT NULL DEFAULT 'none',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  last_fired_at TEXT,
  prev_due_utc TEXT
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

CREATE TABLE IF NOT EXISTS traces (
  trace_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  chat_id INTEGER,
  status TEXT NOT NULL DEFAULT 'running',
  started_at TEXT NOT NULL,
  finished_at TEXT,
  summary TEXT
);

CREATE TABLE IF NOT EXISTS trace_events (
  id INTEGER PRIMARY KEY,
  trace_id TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  stage TEXT NOT NULL,
  level TEXT NOT NULL DEFAULT 'info',
  skill TEXT,
  message TEXT NOT NULL,
  data TEXT
);

CREATE INDEX IF NOT EXISTS idx_trace_events_trace ON trace_events(trace_id, id);
CREATE INDEX IF NOT EXISTS idx_traces_recent ON traces(started_at, status);

CREATE TABLE IF NOT EXISTS model_cooldowns (
  id INTEGER PRIMARY KEY,
  profile TEXT NOT NULL,
  model TEXT NOT NULL,
  reason TEXT NOT NULL,
  until_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cooldowns_active ON model_cooldowns(profile, model, until_at);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  trace_id TEXT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  priority INTEGER NOT NULL DEFAULT 100,
  available_at TEXT NOT NULL,
  claimed_at TEXT,
  finished_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  chat_id INTEGER,
  payload TEXT,
  error TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  trace_id TEXT,
  event_id INTEGER,
  skill TEXT NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  priority INTEGER NOT NULL DEFAULT 100,
  available_at TEXT NOT NULL,
  claimed_at TEXT,
  finished_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 2,
  chat_id INTEGER,
  payload TEXT,
  result TEXT,
  error TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_due ON events(status, available_at, priority);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, available_at, priority);
CREATE INDEX IF NOT EXISTS idx_jobs_skill_status ON jobs(skill, status);

CREATE TABLE IF NOT EXISTS self_facts (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'core',
  source TEXT NOT NULL DEFAULT 'seed',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS boss_profile_items (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  confidence REAL NOT NULL DEFAULT 0.5,
  sensitivity TEXT NOT NULL DEFAULT 'normal',
  source_table TEXT,
  source_id INTEGER,
  evidence TEXT,
  recurrence_count INTEGER NOT NULL DEFAULT 1,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_boss_status_kind ON boss_profile_items(status, kind);

CREATE TABLE IF NOT EXISTS memory_candidates (
  id INTEGER PRIMARY KEY,
  target TEXT NOT NULL DEFAULT 'boss_profile',
  kind TEXT NOT NULL,
  proposed_text TEXT NOT NULL,
  reason TEXT,
  sensitivity TEXT NOT NULL DEFAULT 'normal',
  confidence REAL NOT NULL DEFAULT 0.5,
  source_table TEXT,
  source_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  decided_at TEXT
);

CREATE TABLE IF NOT EXISTS relationship_events (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT,
  summary TEXT NOT NULL,
  importance INTEGER NOT NULL DEFAULT 1,
  source_table TEXT,
  source_id INTEGER,
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidates_status ON memory_candidates(status, created_at);
CREATE INDEX IF NOT EXISTS idx_rel_recent ON relationship_events(created_at, importance);

CREATE TABLE IF NOT EXISTS cara_life (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  text TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  tg_message_id INTEGER,
  tg_file_id TEXT NOT NULL,
  tg_file_unique_id TEXT,
  file_name TEXT,
  mime_type TEXT,
  file_size INTEGER,
  local_path TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proactive_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  day TEXT NOT NULL,
  trace_id TEXT,
  check_name TEXT NOT NULL,
  result TEXT NOT NULL,
  sent_message INTEGER NOT NULL DEFAULT 0,
  reason TEXT
);

CREATE TABLE IF NOT EXISTS stickers (
  id INTEGER PRIMARY KEY,
  set_name TEXT,
  file_id TEXT NOT NULL,
  file_unique_id TEXT UNIQUE,
  emoji TEXT,
  added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cara_photos (
  id INTEGER PRIMARY KEY,
  file_id TEXT NOT NULL,
  file_unique_id TEXT UNIQUE,
  added_at TEXT NOT NULL
);

-- Shared-time meetings (business OR social/personal) + their separate episodic
-- memory. A meeting is a stateful session: while active, every turn is captured
-- verbatim in meeting_turns; on end it is summarized and embedded into
-- meeting_chunks (a SEPARATE recall index, never mixed into notes/`chunks`).
CREATE TABLE IF NOT EXISTS meetings (
  id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'other',   -- business|dinner|walk|movies|visit|call|other
  setting TEXT,                         -- the scene/place (grounds recall)
  title TEXT,
  status TEXT NOT NULL DEFAULT 'active', -- active|ended
  started_at TEXT NOT NULL,
  last_turn_at TEXT,                    -- for idle auto-end
  ended_at TEXT,
  summary TEXT,                         -- recap written at end
  decisions TEXT,                       -- JSON: action items (business) / highlights (social)
  trace_id TEXT
);

CREATE TABLE IF NOT EXISTS meeting_turns (
  id INTEGER PRIMARY KEY,
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  role TEXT NOT NULL,                   -- boss|cara
  text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_chunks (
  id INTEGER PRIMARY KEY,
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  embedding TEXT
);

-- The relationship storyline: an evolving, synthesized narrative of "us",
-- versioned so Cara can speak to how things changed. The latest row is the
-- current arc, injected into every conversation so her attitude tracks the
-- relationship's development. Grown by meetings + a daily reflection.
CREATE TABLE IF NOT EXISTS relationship_arc (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  arc_text TEXT NOT NULL,
  meeting_id INTEGER,
  source TEXT                           -- meeting|daily
);

CREATE INDEX IF NOT EXISTS idx_meetings_active ON meetings(chat_id, status, id);
CREATE INDEX IF NOT EXISTS idx_meeting_turns_m ON meeting_turns(meeting_id, id);
CREATE INDEX IF NOT EXISTS idx_meeting_chunks_m ON meeting_chunks(meeting_id);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _trace_id():
    """Current in-flight trace id (set by trace.py), or None. Imported lazily
    to avoid a common<->store import-order surprise."""
    try:
        from common import current_trace
        return current_trace()
    except Exception:
        return None


# -- traces ------------------------------------------------------------------

def trace_start(conn, trace_id, kind, chat_id=None):
    conn.execute(
        "INSERT OR REPLACE INTO traces (trace_id, kind, chat_id, status, started_at)"
        " VALUES (?, ?, ?, 'running', ?)",
        (trace_id, kind, chat_id, _now()),
    )
    conn.commit()


def trace_event(conn, trace_id, stage, message, level="info", skill=None, data=None):
    conn.execute(
        "INSERT INTO trace_events (trace_id, ts, stage, level, skill, message, data)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (trace_id, _now(), stage, level, skill, str(message)[:500],
         json.dumps(data, ensure_ascii=False) if data is not None else None),
    )
    conn.commit()


def trace_finish(conn, trace_id, status, summary=None):
    conn.execute(
        "UPDATE traces SET status = ?, finished_at = ?, summary = ? WHERE trace_id = ?",
        (status, _now(), summary, trace_id),
    )
    conn.commit()


def trace_get(conn, trace_id):
    return conn.execute("SELECT * FROM traces WHERE trace_id = ?", (trace_id,)).fetchone()


def trace_events(conn, trace_id):
    return conn.execute(
        "SELECT * FROM trace_events WHERE trace_id = ? ORDER BY id", (trace_id,)
    ).fetchall()


def latest_trace(conn, chat_id, kind="inbound"):
    return conn.execute(
        "SELECT * FROM traces WHERE chat_id = ? AND kind = ? ORDER BY started_at DESC LIMIT 1",
        (chat_id, kind),
    ).fetchone()


# -- self facts (Cara's self-knowledge) --------------------------------------

def self_fact_set(conn, key, value, scope="core", source="seed"):
    now = _now()
    conn.execute(
        "INSERT INTO self_facts (key, value, scope, source, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'active', ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value, scope=excluded.scope,"
        " updated_at=excluded.updated_at, status='active'",
        (key, value, scope, source, now, now),
    )
    conn.commit()


def self_facts(conn, scope=None):
    if scope:
        return conn.execute(
            "SELECT * FROM self_facts WHERE status='active' AND scope=? ORDER BY key", (scope,)
        ).fetchall()
    return conn.execute("SELECT * FROM self_facts WHERE status='active' ORDER BY key").fetchall()


# -- boss profile model ------------------------------------------------------

def boss_add(conn, kind, value, *, status="pending", confidence=0.5, sensitivity="normal",
             source_table=None, source_id=None, evidence=None):
    now = _now()
    cur = conn.execute(
        "INSERT INTO boss_profile_items (kind, value, status, confidence, sensitivity,"
        " source_table, source_id, evidence, recurrence_count, first_seen_at, last_seen_at,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
        (kind, value, status, confidence, sensitivity, source_table, source_id, evidence,
         now, now, now, now),
    )
    conn.commit()
    return cur.lastrowid


def boss_items(conn, status, sensitivities=None, limit=30):
    if sensitivities:
        marks = ",".join("?" for _ in sensitivities)
        return conn.execute(
            f"SELECT * FROM boss_profile_items WHERE status=? AND sensitivity IN ({marks})"
            " ORDER BY confidence DESC, last_seen_at DESC LIMIT ?",
            (status, *sensitivities, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM boss_profile_items WHERE status=? ORDER BY confidence DESC,"
        " last_seen_at DESC LIMIT ?",
        (status, limit),
    ).fetchall()


def boss_get(conn, item_id):
    return conn.execute("SELECT * FROM boss_profile_items WHERE id=?", (item_id,)).fetchone()


def boss_set_status(conn, item_id, status):
    cur = conn.execute(
        "UPDATE boss_profile_items SET status=?, updated_at=? WHERE id=?",
        (status, _now(), item_id),
    )
    conn.commit()
    return cur.rowcount > 0


def boss_find(conn, query):
    """Find a confirmed/inferred item by substring (Cyrillic-safe, Python-side)."""
    q = str(query or "").casefold()
    if not q:
        return None
    for row in conn.execute(
        "SELECT * FROM boss_profile_items WHERE status IN ('confirmed','inferred') ORDER BY id DESC"
    ):
        if q in (row["value"] or "").casefold():
            return row
    return None


# -- memory candidates (curator) ---------------------------------------------

def candidate_exists(conn, text):
    """True if a same-text candidate is already pending/confirmed/rejected
    (Cyrillic-safe), so the curator doesn't re-propose it."""
    t = str(text or "").casefold()
    for row in conn.execute(
        "SELECT proposed_text FROM memory_candidates WHERE status IN"
        " ('pending','confirmed','rejected')"
    ):
        if t == (row["proposed_text"] or "").casefold():
            return True
    return False


def candidate_add(conn, kind, text, *, reason=None, sensitivity="normal", confidence=0.6,
                  target="boss_profile", source_table=None, source_id=None):
    if candidate_exists(conn, text):
        return None
    cur = conn.execute(
        "INSERT INTO memory_candidates (target, kind, proposed_text, reason, sensitivity,"
        " confidence, source_table, source_id, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (target, kind, text, reason, sensitivity, confidence, source_table, source_id, _now()),
    )
    conn.commit()
    return cur.lastrowid


def candidates_pending(conn, limit=8):
    return conn.execute(
        "SELECT * FROM memory_candidates WHERE status='pending'"
        " ORDER BY confidence DESC, id LIMIT ?",
        (limit,),
    ).fetchall()


def candidate_get(conn, candidate_id):
    return conn.execute("SELECT * FROM memory_candidates WHERE id=?", (candidate_id,)).fetchone()


def candidate_set_status(conn, candidate_id, status):
    conn.execute(
        "UPDATE memory_candidates SET status=?, decided_at=? WHERE id=?",
        (status, _now(), candidate_id),
    )
    conn.commit()


# -- relationship events -----------------------------------------------------

def rel_add(conn, kind, summary, importance=1, source_table=None, source_id=None, title=None):
    conn.execute(
        "INSERT INTO relationship_events (kind, title, summary, importance, source_table,"
        " source_id, trace_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, title, str(summary)[:300], importance, source_table, source_id,
         _trace_id(), _now()),
    )
    conn.commit()


def rel_recent(conn, since_iso, limit=8):
    return conn.execute(
        "SELECT * FROM relationship_events WHERE created_at >= ?"
        " ORDER BY importance DESC, id DESC LIMIT ?",
        (since_iso, limit),
    ).fetchall()


# -- Cara's (fictional) private life: persisted so she stays consistent -------

def life_add(conn, kind, text):
    text = str(text or "").strip()[:300]
    if not text:
        return None
    try:
        cur = conn.execute(
            "INSERT INTO cara_life (kind, text, created_at) VALUES (?, ?, ?)",
            (kind, text, _now()),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:  # UNIQUE(text) — already known
        return None


def life_facts(conn, limit=40):
    return conn.execute(
        "SELECT kind, text FROM cara_life ORDER BY id LIMIT ?", (limit,)
    ).fetchall()


def life_count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM cara_life").fetchone()["n"]


# -- meetings (shared-time sessions) + episodic memory -----------------------

def meeting_active(conn, chat_id):
    """The open meeting for this chat, or None. One active meeting at a time."""
    return conn.execute(
        "SELECT * FROM meetings WHERE chat_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
        (chat_id,),
    ).fetchone()


def meeting_start(conn, chat_id, kind="other", setting=None, title=None):
    now = _now()
    cur = conn.execute(
        "INSERT INTO meetings (chat_id, kind, setting, title, status, started_at,"
        " last_turn_at, trace_id) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
        (chat_id, kind, setting, title, now, now, _trace_id()),
    )
    conn.commit()
    return cur.lastrowid


def meeting_get(conn, meeting_id):
    return conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()


def meeting_turn_add(conn, meeting_id, role, text):
    text = str(text or "").strip()
    if not text:
        return
    now = _now()
    conn.execute(
        "INSERT INTO meeting_turns (meeting_id, ts, role, text) VALUES (?, ?, ?, ?)",
        (meeting_id, now, role, text[:2000]),
    )
    conn.execute("UPDATE meetings SET last_turn_at = ? WHERE id = ?", (now, meeting_id))
    conn.commit()


def meeting_turns(conn, meeting_id, limit=500):
    return conn.execute(
        "SELECT role, text, ts FROM meeting_turns WHERE meeting_id = ? ORDER BY id LIMIT ?",
        (meeting_id, limit),
    ).fetchall()


def meeting_turn_count(conn, meeting_id):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM meeting_turns WHERE meeting_id = ?", (meeting_id,)
    ).fetchone()["n"]


def meeting_end(conn, meeting_id, summary=None, decisions=None, title=None):
    conn.execute(
        "UPDATE meetings SET status = 'ended', ended_at = ?,"
        " summary = COALESCE(?, summary), decisions = COALESCE(?, decisions),"
        " title = COALESCE(?, title) WHERE id = ?",
        (_now(), summary, decisions, title, meeting_id),
    )
    conn.commit()


def meeting_recent(conn, chat_id, limit=10, status="ended"):
    return conn.execute(
        "SELECT * FROM meetings WHERE chat_id = ? AND status = ? ORDER BY id DESC LIMIT ?",
        (chat_id, status, limit),
    ).fetchall()


def meeting_count(conn, chat_id, status="ended"):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM meetings WHERE chat_id = ? AND status = ?",
        (chat_id, status),
    ).fetchone()["n"]


def meeting_first(conn, chat_id):
    return conn.execute(
        "SELECT * FROM meetings WHERE chat_id = ? AND status = 'ended' ORDER BY id LIMIT 1",
        (chat_id,),
    ).fetchone()


def meeting_last(conn, chat_id):
    return conn.execute(
        "SELECT * FROM meetings WHERE chat_id = ? AND status = 'ended' ORDER BY id DESC LIMIT 1",
        (chat_id,),
    ).fetchone()


def meetings_idle(conn, cutoff_iso):
    """Active meetings whose last turn is older than cutoff (idle auto-end)."""
    return conn.execute(
        "SELECT * FROM meetings WHERE status = 'active' AND COALESCE(last_turn_at, started_at) < ?",
        (cutoff_iso,),
    ).fetchall()


def set_meeting_chunks(conn, meeting_id, chunks):
    """Replace a meeting's embedding chunks. chunks: list of (text, vec_or_None)."""
    import json as _json
    conn.execute("DELETE FROM meeting_chunks WHERE meeting_id = ?", (meeting_id,))
    for i, (text, embedding) in enumerate(chunks):
        conn.execute(
            "INSERT INTO meeting_chunks (meeting_id, chunk_index, text, embedding)"
            " VALUES (?, ?, ?, ?)",
            (meeting_id, i, text, _json.dumps(embedding) if embedding is not None else None),
        )
    conn.commit()


def all_meeting_chunks(conn, chat_id=None):
    """Every embedded meeting chunk (optionally for one chat), with its meeting's
    kind/setting/title/date for warm grounded recall."""
    q = ("SELECT mc.meeting_id AS meeting_id, mc.text AS text, mc.embedding AS embedding,"
         " m.kind AS kind, m.setting AS setting, m.title AS title, m.started_at AS started_at"
         " FROM meeting_chunks mc JOIN meetings m ON m.id = mc.meeting_id"
         " WHERE mc.embedding IS NOT NULL")
    params = ()
    if chat_id is not None:
        q += " AND m.chat_id = ?"
        params = (chat_id,)
    return conn.execute(q, params).fetchall()


# -- relationship storyline arc (versioned; latest row = current) ------------

def arc_set(conn, arc_text, meeting_id=None, source=None):
    arc_text = str(arc_text or "").strip()
    if not arc_text:
        return None
    cur = conn.execute(
        "INSERT INTO relationship_arc (ts, arc_text, meeting_id, source) VALUES (?, ?, ?, ?)",
        (_now(), arc_text[:4000], meeting_id, source),
    )
    conn.commit()
    return cur.lastrowid


def arc_current(conn):
    return conn.execute(
        "SELECT * FROM relationship_arc ORDER BY id DESC LIMIT 1"
    ).fetchone()


def arc_history(conn, limit=10):
    return conn.execute(
        "SELECT * FROM relationship_arc ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


# -- proactive heartbeat audit log -------------------------------------------

def proactive_log_add(conn, check_name, result, sent=False, reason=None, day=None):
    ts = _now()
    conn.execute(
        "INSERT INTO proactive_log (ts, day, trace_id, check_name, result, sent_message, reason)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts, day or ts[:10], _trace_id(), check_name, result, 1 if sent else 0, reason),
    )
    conn.commit()


def proactive_sent_count(conn, day):
    """How many proactive nudges were actually sent on a given UTC day."""
    return conn.execute(
        "SELECT COUNT(*) AS n FROM proactive_log WHERE day = ? AND sent_message = 1", (day,)
    ).fetchone()["n"]


def proactive_key_sent_today(conn, day, check_name):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM proactive_log WHERE day = ? AND check_name = ?"
        " AND sent_message = 1", (day, check_name),
    ).fetchone()["n"] > 0


# -- model cooldowns (failover) ----------------------------------------------

def cooldown_set(conn, profile, model, seconds, reason):
    from datetime import timedelta
    until = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
    conn.execute(
        "INSERT INTO model_cooldowns (profile, model, reason, until_at, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (profile, model, str(reason)[:200], until, _now()),
    )
    conn.commit()


def cooldown_active(conn, profile, model):
    row = conn.execute(
        "SELECT 1 FROM model_cooldowns WHERE profile = ? AND model = ? AND until_at > ?"
        " LIMIT 1",
        (profile, model, _now()),
    ).fetchone()
    return row is not None


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
    usage_columns = {row["name"] for row in conn.execute("PRAGMA table_info(llm_usage)")}
    if "trace_id" not in usage_columns:
        conn.execute("ALTER TABLE llm_usage ADD COLUMN trace_id TEXT")
    issue_columns = {row["name"] for row in conn.execute("PRAGMA table_info(issues)")}
    if "trace_id" not in issue_columns:
        conn.execute("ALTER TABLE issues ADD COLUMN trace_id TEXT")
    rel_columns = {row["name"] for row in conn.execute("PRAGMA table_info(relationship_events)")}
    if "title" not in rel_columns:
        conn.execute("ALTER TABLE relationship_events ADD COLUMN title TEXT")
    if "trace_id" not in rel_columns:
        conn.execute("ALTER TABLE relationship_events ADD COLUMN trace_id TEXT")
    rem_columns = {row["name"] for row in conn.execute("PRAGMA table_info(reminders)")}
    if "prev_due_utc" not in rem_columns:
        conn.execute("ALTER TABLE reminders ADD COLUMN prev_due_utc TEXT")
    cat_columns = {row["name"] for row in conn.execute("PRAGMA table_info(categories)")}
    if "kind" not in cat_columns:
        conn.execute("ALTER TABLE categories ADD COLUMN kind TEXT NOT NULL DEFAULT 'inbox'")


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
        "SELECT c.name AS name, c.kind AS kind,"
        " (SELECT COUNT(*) FROM messages m WHERE m.category = c.name AND m.status = 'confirmed') AS n"
        " FROM categories c ORDER BY n DESC, c.name",
    ).fetchall()


# -- journals (categories marked long-term / append-only) --------------------

def set_category_kind(conn, name, kind):
    """Mark a category 'journal' (long-term, append-only) or 'inbox' (one-time).
    Creates the category if new; returns the canonical name."""
    canonical = ensure_category(conn, name)
    conn.execute("UPDATE categories SET kind = ? WHERE norm_key = ?",
                 (kind, canonical.casefold()))
    conn.commit()
    return canonical


def category_kind(conn, name):
    row = conn.execute("SELECT kind FROM categories WHERE norm_key = ?",
                       (str(name or "").casefold(),)).fetchone()
    return (row["kind"] if row and row["kind"] else "inbox")


def is_journal(conn, name):
    return category_kind(conn, name) == "journal"


def journal_categories(conn):
    """Canonical names of all journal categories (casefold-keyed set is the
    source of truth for retention/recall decisions)."""
    return [r["name"] for r in conn.execute(
        "SELECT name FROM categories WHERE kind = 'journal' ORDER BY name")]


def journal_entries(conn, category, since_iso=None, limit=200):
    """Confirmed entries in a journal category, oldest→newest (a diary reads
    forward), optionally only those received since since_iso. Filtered in
    Python because category matching must be Cyrillic-casefold-aware."""
    target = str(category or "").casefold()
    entries = []
    for row in conn.execute(
        "SELECT id, received_at, tg_date, forward_date, raw_text, summary, category"
        " FROM messages WHERE status = 'confirmed' AND category IS NOT NULL ORDER BY id ASC"
    ):
        if row["category"].casefold() != target:
            continue
        if since_iso and (row["received_at"] or "") < since_iso:
            continue
        entries.append(row)
        if len(entries) >= limit:
            break
    return entries


def journal_count(conn, category):
    target = str(category or "").casefold()
    n = 0
    for row in conn.execute(
        "SELECT category FROM messages WHERE status = 'confirmed' AND category IS NOT NULL"):
        if row["category"].casefold() == target:
            n += 1
    return n


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


def insert_file(conn, message_id, tg_message_id, document, local_path=None):
    """Store a non-image document attachment (PDF, doc, sheet, text…). We keep
    its tg_file_id so it can be re-sent later for free, no download needed."""
    conn.execute(
        "INSERT INTO files (message_id, tg_message_id, tg_file_id, tg_file_unique_id,"
        " file_name, mime_type, file_size, local_path, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            message_id,
            tg_message_id,
            document.get("file_id"),
            document.get("file_unique_id"),
            document.get("file_name"),
            document.get("mime_type"),
            document.get("file_size"),
            local_path,
            _now(),
        ),
    )
    conn.commit()


def message_files(conn, message_id):
    return conn.execute(
        "SELECT * FROM files WHERE message_id = ? ORDER BY id", (message_id,)
    ).fetchall()


def recent_files(conn, limit=20):
    """All stored files (newest first) with their item's id/category, for the
    'show my files' listing."""
    return conn.execute(
        "SELECT f.file_name, f.mime_type, f.message_id, m.category, m.suggested_category"
        " FROM files f JOIN messages m ON m.id = f.message_id"
        " ORDER BY f.id DESC LIMIT ?", (limit,),
    ).fetchall()


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
        " m.forward_origin_title AS title, m.received_at AS received_at"
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


# -- display numbering -------------------------------------------------------
# The user-facing note number is a contiguous 1..N position (oldest first) over
# the *visible* notes — NOT the immutable `id` (which stays the stable key for
# every attachment/embedding/memory FK). It compacts automatically on deletion,
# so the numbers the boss sees always start at 1 with no gaps.

def display_ids(conn):
    """Visible-note ids in display order (oldest first); position = number."""
    return [r["id"] for r in conn.execute(
        "SELECT id FROM messages WHERE status IN ('confirmed', 'suggested') ORDER BY id ASC")]


def display_map(conn):
    """{message_id: display_no} for all visible notes."""
    return {mid: i for i, mid in enumerate(display_ids(conn), start=1)}


def display_no(conn, message_id):
    """The note's 1..N display number, or None if it isn't a visible note."""
    ids = display_ids(conn)
    return ids.index(message_id) + 1 if message_id in ids else None


def message_by_display_no(conn, n):
    """Resolve a user-typed note number (1..N) to its row, or None."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    ids = display_ids(conn)
    return get_message(conn, ids[n - 1]) if 1 <= n <= len(ids) else None


def delete_message(conn, message_id):
    """Delete a message row (urls/images cascade); returns media paths to
    unlink. Other rows referencing it as duplicate_of keep their copy."""
    paths = [r["local_path"] for r in message_images(conn, message_id) if r["local_path"]]
    paths += [r["local_path"] for r in message_files(conn, message_id) if r["local_path"]]
    conn.execute("UPDATE messages SET duplicate_of = NULL WHERE duplicate_of = ?", (message_id,))
    conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
    conn.commit()
    return paths


PURGE_SCOPES = ("all", "category", "stats", "reminders", "messages", "issues")


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


def _non_journal_message_ids(conn):
    """Message ids whose category is NOT a journal — the set a 'clear all notes'
    purge may delete (journals are long-term and protected)."""
    journals = {n.casefold() for n in journal_categories(conn)}
    if not journals:
        return None  # nothing protected -> caller can use the fast whole-table path
    ids = []
    for row in conn.execute("SELECT id, category, suggested_category FROM messages"):
        name = (row["category"] or row["suggested_category"] or "").casefold()
        if name not in journals:
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
    elif scope == "messages":  # all saved notes/messages, keep categories/reminders/settings
        protected = _non_journal_message_ids(conn)  # journals are spared
        info["messages"] = (count("SELECT COUNT(*) FROM messages")
                            if protected is None else len(protected))
        kept = count("SELECT COUNT(*) FROM messages") - info["messages"]
        if kept:
            info["kept_journal"] = kept
    elif scope == "issues":  # only the failure/issue log
        info["issues"] = count("SELECT COUNT(*) FROM issues")
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
    elif scope == "messages":
        protected = _non_journal_message_ids(conn)  # journals are spared
        if protected is None:  # no journals -> fast whole-table clear
            paths = [r["local_path"] for r in
                     conn.execute("SELECT local_path FROM images WHERE local_path IS NOT NULL")]
            for table in ("facts", "chunks", "urls", "images", "messages"):
                conn.execute(f"DELETE FROM {table}")
        else:
            for mid in protected:
                paths.extend(delete_message(conn, mid))
    elif scope == "issues":  # only the issue/failure log; nothing else
        conn.execute("DELETE FROM issues")
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
        " seconds, cost_usd, trace_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, ts[:10], ts[:7], skill, kind, model, tokens_in, tokens_out, seconds, cost_usd,
         _trace_id()),
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


def pending_expire(conn):
    """Proactively drop pending actions past their expires_at (pending_get only
    expires the one chat it reads — this sweeps abandoned ones). Returns count."""
    cur = conn.execute("DELETE FROM pending_actions WHERE expires_at < ?", (_now(),))
    conn.commit()
    return cur.rowcount


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
        "INSERT INTO issues (ts, day, chat_id, kind, detail, trace_id) VALUES (?, ?, ?, ?, ?, ?)",
        (ts, ts[:10], chat_id, kind, str(detail or "")[:300], _trace_id()),
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
        "SELECT * FROM reminders WHERE chat_id = ? AND status = 'active'"
        " ORDER BY due_utc, id",  # id tiebreak keeps display numbering deterministic
        (chat_id,),
    ).fetchall()


def reminder_display_no(conn, chat_id, rid):
    """1..N position of an active reminder in the boss-facing (due-ordered) list,
    or None if it isn't active. The number compacts as reminders fire/cancel; the
    immutable id stays the key for fired-pending payloads, calendar, and history."""
    for i, row in enumerate(reminders_active(conn, chat_id), start=1):
        if row["id"] == rid:
            return i
    return None


def reminders_due(conn, now_iso):
    return conn.execute(
        "SELECT * FROM reminders WHERE status = 'active' AND due_utc <= ? ORDER BY due_utc",
        (now_iso,),
    ).fetchall()


def reminder_update_due(conn, rid, due_utc):
    """Move a reminder, remembering its current time in prev_due_utc so a
    reschedule can be undone ('верни предыдущее время')."""
    conn.execute(
        "UPDATE reminders SET prev_due_utc = due_utc, due_utc = ? WHERE id = ?",
        (due_utc, rid),
    )
    conn.commit()


def reminder_restore_due(conn, rid):
    """Swap due_utc with prev_due_utc (undo the last reschedule). Returns the
    restored time, or None if there is no remembered previous time."""
    cur = conn.execute(
        "SELECT due_utc, prev_due_utc FROM reminders WHERE id = ?", (rid,)
    ).fetchone()
    if not cur or not cur["prev_due_utc"]:
        return None
    conn.execute(
        "UPDATE reminders SET due_utc = ?, prev_due_utc = ? WHERE id = ?",
        (cur["prev_due_utc"], cur["due_utc"], rid),
    )
    conn.commit()
    return cur["prev_due_utc"]


def reminder_close(conn, rid, status="done"):
    conn.execute(
        "UPDATE reminders SET status = ?, last_fired_at = ? WHERE id = ?",
        (status, _now(), rid),
    )
    conn.commit()


# -- Stickers (packs Cara may use in conversation) ---------------------------

def stickers_add(conn, set_name, stickers):
    """Insert each sticker of a set (dicts with file_id/file_unique_id/emoji).
    Idempotent via UNIQUE(file_unique_id). Returns how many are now saved for it."""
    for s in stickers:
        fid = s.get("file_id")
        if not fid:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO stickers (set_name, file_id, file_unique_id, emoji,"
            " added_at) VALUES (?, ?, ?, ?, ?)",
            (set_name, fid, s.get("file_unique_id"), s.get("emoji"), _now()),
        )
    conn.commit()
    return conn.execute(
        "SELECT COUNT(*) FROM stickers WHERE set_name = ?", (set_name,)
    ).fetchone()[0]


def sticker_count(conn):
    return conn.execute("SELECT COUNT(*) FROM stickers").fetchone()[0]


def sticker_random(conn):
    row = conn.execute("SELECT file_id FROM stickers ORDER BY RANDOM() LIMIT 1").fetchone()
    return row["file_id"] if row else None


def sticker_for_emoji(conn, emoji):
    """A saved sticker file_id whose emoji matches `emoji` (exact first, then any
    that contains it); None if none saved match."""
    emoji = (emoji or "").strip()
    if not emoji:
        return None
    row = conn.execute(
        "SELECT file_id FROM stickers WHERE emoji = ? ORDER BY RANDOM() LIMIT 1", (emoji,)
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT file_id FROM stickers WHERE emoji LIKE ? ORDER BY RANDOM() LIMIT 1",
            (f"%{emoji}%",),
        ).fetchone()
    return row["file_id"] if row else None


# -- Cara's photo library (her own pictures to send in chat) -----------------

def cara_photo_add(conn, photos):
    """Store photos (dicts with file_id/file_unique_id) as Cara's own gallery.
    Idempotent via UNIQUE(file_unique_id). Returns total saved now."""
    for p in photos:
        fid = p.get("file_id")
        if not fid:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO cara_photos (file_id, file_unique_id, added_at)"
            " VALUES (?, ?, ?)",
            (fid, p.get("file_unique_id"), _now()),
        )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM cara_photos").fetchone()[0]


def cara_photo_count(conn):
    return conn.execute("SELECT COUNT(*) FROM cara_photos").fetchone()[0]


def cara_photo_random(conn):
    row = conn.execute(
        "SELECT file_id FROM cara_photos ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    return row["file_id"] if row else None
