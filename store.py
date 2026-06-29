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
from array import array
from datetime import datetime, timedelta, timezone
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
  thumb_file_id TEXT,
  description TEXT,
  added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cara_photos (
  id INTEGER PRIMARY KEY,
  file_id TEXT NOT NULL,
  file_unique_id TEXT UNIQUE,
  added_at TEXT NOT NULL
);

-- Cara's wardrobe: curated, persona-true outfits she picks from for in-person
-- meetings. The intimate tier is tasteful/suggestive only (gated by closeness +
-- occasion). List fields (season/colors/pieces) are stored as JSON text.
CREATE TABLE IF NOT EXISTS cara_wardrobe (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  family TEXT NOT NULL,
  season TEXT,
  intimacy INTEGER NOT NULL DEFAULT 0,
  colors TEXT,
  pieces TEXT,
  footwear TEXT,
  signature INTEGER NOT NULL DEFAULT 0,
  surprise INTEGER NOT NULL DEFAULT 0,
  last_worn_at TEXT,
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
  status TEXT NOT NULL DEFAULT 'active', -- scheduled|active|ended
  started_at TEXT NOT NULL,
  scheduled_for TEXT,                   -- planned time (status='scheduled' future meeting)
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

-- Live physical scene snapshot for an active meeting (placement, postures, state of
-- dress, props). One row per meeting; carried forward turn to turn so Cara stays
-- physically consistent, cleared when the meeting ends.
CREATE TABLE IF NOT EXISTS meeting_scene (
  meeting_id INTEGER PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,
  state TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Durable WORLD MODEL beyond facts-about-him: the cast of people (real acquaintances and
-- recurring roleplay characters, with relationships/bonding/background), promises to keep,
-- relationship milestones, and recurring owned items/props. Injected (compact) into context
-- so Cara remembers who's who, what was promised, and where the relationship is going.
CREATE TABLE IF NOT EXISTS world_facts (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                     -- person|promise|milestone|item
  name TEXT,                              -- canonical name (person/item); NULL for promise/milestone
  text TEXT NOT NULL,                     -- role/relationship (person) or the promise/milestone text
  status TEXT NOT NULL DEFAULT 'active',  -- active|kept|inactive|superseded
  happened_at TEXT,                       -- when a promise was made / a milestone occurred
  created_at TEXT NOT NULL,
  last_seen_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_world_kind ON world_facts(kind, status);

-- Long-term BODY memory for Cara: durable changes to her body that persist ACROSS dates and
-- into everyday talk — marks he left (hickeys/bruises, which fade), add-ons she now wears
-- (a collar, jewelry), and permanent adjustments (a piercing, a tattoo). Distinct from the
-- ephemeral meeting_scene: this is who her body IS over time, not the live pose.
CREATE TABLE IF NOT EXISTS body_state (
  id INTEGER PRIMARY KEY,
  feature TEXT NOT NULL,                   -- "след на шее", "ошейник", "пирсинг в пупке"
  permanence TEXT NOT NULL DEFAULT 'lasting',  -- mark (fades) | lasting (worn) | permanent
  note TEXT,                               -- context (who/when/why)
  added_at TEXT NOT NULL,
  fades_at TEXT,                           -- when a temporary mark should be gone (marks only)
  status TEXT NOT NULL DEFAULT 'active'    -- active | faded | removed
);
CREATE INDEX IF NOT EXISTS idx_body_status ON body_state(status);

-- Paginated list views: a short-lived snapshot of the FILTER behind a notes list message, so
-- its inline ◀/▶ buttons can recompute pages by token (the filter can exceed Telegram's 64-byte
-- callback_data). Pruned after a day.
CREATE TABLE IF NOT EXISTS list_views (
  id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  filter TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Preparation agreed for an UPCOMING meeting: logistical details/agreements (what
-- she'll wear, what he brings, the plan) and emotional beats (her anticipation,
-- longing). Accumulated during the lead-up, surfaced while planning AND carried into
-- the live meeting so she stays consistent ("she's in that dress") and never surprised.
-- The shared intimate LANGUAGE that lands between them: pet-names, terms of endearment,
-- favoured playful/euphemistic phrasings (NON-explicit — style only). Injected so her
-- teasing and hints feel personal and consistent over time.
CREATE TABLE IF NOT EXISTS intimacy_style (
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL UNIQUE,
  added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_prep (
  id INTEGER PRIMARY KEY,
  meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'detail',   -- agreement|detail|feeling
  detail TEXT NOT NULL,
  added_at TEXT NOT NULL,
  UNIQUE(meeting_id, detail)
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


# -- embeddings: packed float32 (compact + fast to load) ---------------------
# Stored as a BLOB of little-endian float32 (4 bytes/dim) instead of JSON text
# (~5x smaller than the old JSON-array form and far cheaper to decode). The
# unpacker also accepts the legacy JSON-text form so old rows and hand-built
# test rows still decode; legacy rows are migrated to BLOB in `_migrate`.

def pack_embedding(vec):
    """list[float] -> float32 BLOB (None stays None)."""
    if vec is None:
        return None
    return array("f", (float(x) for x in vec)).tobytes()


def unpack_embedding(value):
    """float32 BLOB | legacy JSON text | None -> list[float] | None.
    Returns None on anything undecodable (caller skips it)."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        a = array("f")
        try:
            a.frombytes(bytes(value))
        except ValueError:
            return None
        return a.tolist()
    if isinstance(value, str):  # legacy JSON text (and test rows)
        try:
            out = json.loads(value)
        except (TypeError, ValueError):
            return None
        return out if isinstance(out, list) else None
    return None


# -- decoded-vector cache ----------------------------------------------------
# The retrieval hot path (converse grounding, ask, meeting recall) ranks every
# embedded chunk on each turn. Re-reading + re-decoding all embeddings every
# time is the part that grows with the corpus, so we cache the DECODED vectors
# per connection and reuse them until the underlying table changes. The cache
# key is a cheap (count, max_id, sum_id) fingerprint over the id column — it
# changes on any insert/delete/re-index, so the cache is never stale. Keyed by
# id(conn) so multiple test DBs in one process never collide.
_VEC_CACHE = {}


def _fingerprint(conn, table):
    row = conn.execute(
        f"SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(SUM(id),0)"
        f" FROM {table} WHERE embedding IS NOT NULL").fetchone()
    return (table, row[0], row[1], row[2])


def _cached_vectors(conn, table, load_sql, meta_keys):
    fp = _fingerprint(conn, table)
    slot = _VEC_CACHE.setdefault(id(conn), {})
    cached = slot.get(table)
    if cached is not None and cached[0] == fp:
        return cached[1]
    rows = []
    for r in conn.execute(load_sql):
        d = {k: r[k] for k in meta_keys}
        d["vec"] = unpack_embedding(r["embedding"])
        if d["vec"] is None:
            continue  # corrupt/undecodable — skip rather than poison the cache
        rows.append(d)
    slot[table] = (fp, rows)
    return rows


def invalidate_vector_cache(conn=None):
    """Drop cached decoded vectors (all, or one connection). The fingerprint
    already keeps the cache honest; this is a belt-and-suspenders hook."""
    if conn is None:
        _VEC_CACHE.clear()
    else:
        _VEC_CACHE.pop(id(conn), None)


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
    # RANDOM (not ORDER BY id) so no single trait is pinned into EVERY prompt — the
    # old fixed slice made her over-index on the same details (the 'tea' problem).
    return conn.execute(
        "SELECT kind, text FROM cara_life ORDER BY RANDOM() LIMIT ?", (limit,)
    ).fetchall()


def life_all(conn):
    """Every life fact with its id (for consolidation/dedup)."""
    return conn.execute("SELECT id, text FROM cara_life ORDER BY id").fetchall()


def life_delete(conn, life_id):
    cur = conn.execute("DELETE FROM cara_life WHERE id = ?", (life_id,))
    conn.commit()
    return cur.rowcount > 0


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


def meeting_schedule(conn, chat_id, scheduled_for, kind="other", setting=None, title=None):
    """Create a FUTURE (status='scheduled') meeting agreed in conversation, so
    Cara remembers the appointment. started_at mirrors scheduled_for so it dates
    correctly when it later goes live."""
    cur = conn.execute(
        "INSERT INTO meetings (chat_id, kind, setting, title, status, started_at,"
        " scheduled_for, trace_id) VALUES (?, ?, ?, ?, 'scheduled', ?, ?, ?)",
        (chat_id, kind, setting, title, scheduled_for, scheduled_for, _trace_id()),
    )
    conn.commit()
    return cur.lastrowid


def meetings_upcoming(conn, chat_id, limit=10):
    return conn.execute(
        "SELECT * FROM meetings WHERE chat_id = ? AND status = 'scheduled'"
        " ORDER BY scheduled_for LIMIT ?", (chat_id, limit),
    ).fetchall()


def intimacy_style_add(conn, text):
    """Remember one shared pet-name / endearment / favoured playful phrasing (style only,
    non-explicit). Idempotent via UNIQUE(text); capped softly. Returns the id or None."""
    text = str(text or "").strip()[:120]
    if not text:
        return None
    try:
        cur = conn.execute(
            "INSERT INTO intimacy_style (text, added_at) VALUES (?, ?)", (text, _now()))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def intimacy_style_list(conn, limit=12):
    return [r["text"] for r in conn.execute(
        "SELECT text FROM intimacy_style ORDER BY id DESC LIMIT ?", (limit,))]


def meeting_prep_add(conn, meeting_id, detail, kind="detail"):
    """Note one agreed prep detail / emotional beat for an upcoming meeting.
    Idempotent via UNIQUE(meeting_id, detail). Returns the row id or None."""
    detail = str(detail or "").strip()[:300]
    if not detail:
        return None
    try:
        cur = conn.execute(
            "INSERT INTO meeting_prep (meeting_id, kind, detail, added_at) VALUES (?, ?, ?, ?)",
            (meeting_id, kind, detail, _now()))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:  # already noted
        return None


def meeting_prep_list(conn, meeting_id, limit=40):
    return conn.execute(
        "SELECT kind, detail FROM meeting_prep WHERE meeting_id = ? ORDER BY id LIMIT ?",
        (meeting_id, limit)).fetchall()


def meetings_due_scheduled(conn, now_iso):
    """Scheduled meetings whose time has arrived (for proactive go-live)."""
    return conn.execute(
        "SELECT * FROM meetings WHERE status = 'scheduled' AND scheduled_for <= ?"
        " ORDER BY scheduled_for", (now_iso,),
    ).fetchall()


def meeting_activate(conn, meeting_id):
    """Move a scheduled meeting into the live (active) state when it begins."""
    conn.execute(
        "UPDATE meetings SET status = 'active', last_turn_at = ? WHERE id = ?",
        (_now(), meeting_id),
    )
    conn.commit()


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


def scene_get(conn, meeting_id):
    """The active meeting's physical scene snapshot as a dict ({} if none/unparseable)."""
    row = conn.execute(
        "SELECT state FROM meeting_scene WHERE meeting_id = ?", (meeting_id,)).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row["state"])
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def scene_set(conn, meeting_id, state):
    conn.execute(
        "INSERT INTO meeting_scene (meeting_id, state, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(meeting_id) DO UPDATE SET state = excluded.state,"
        " updated_at = excluded.updated_at",
        (meeting_id, json.dumps(state, ensure_ascii=False), _now()))
    conn.commit()


def scene_clear(conn, meeting_id):
    conn.execute("DELETE FROM meeting_scene WHERE meeting_id = ?", (meeting_id,))
    conn.commit()


# -- world model (people / promises / milestones / owned items) ---------------

def world_upsert_person(conn, name, role, kind="person"):
    """Add or update a person (or named item) by canonical name, case-insensitively — so a
    role/relationship gets refreshed and the name is never duplicated. Returns the id.
    (Matching is done in Python: SQLite's lower() doesn't fold Cyrillic.)"""
    name = (name or "").strip()
    if not name:
        return None
    role = (role or "").strip()
    now = _now()
    key = name.casefold()
    for row in conn.execute("SELECT id, name FROM world_facts WHERE kind=?", (kind,)).fetchall():
        if (row["name"] or "").casefold() == key:
            conn.execute(
                "UPDATE world_facts SET text=?, status='active', last_seen_at=? WHERE id=?",
                (role or "", now, row["id"]))
            conn.commit()
            return row["id"]
    cur = conn.execute(
        "INSERT INTO world_facts (kind, name, text, status, created_at, last_seen_at)"
        " VALUES (?, ?, ?, 'active', ?, ?)", (kind, name, role, now, now))
    conn.commit()
    return cur.lastrowid


def world_add(conn, kind, text, happened_at=None):
    """Add a nameless world fact (promise/milestone), deduped by (kind, casefold(text)).
    Returns the id, or None if it duplicates an existing one. (Python casefold — Cyrillic-safe.)"""
    text = (text or "").strip()
    if not text:
        return None
    key = text.casefold()
    for row in conn.execute("SELECT text FROM world_facts WHERE kind=?", (kind,)).fetchall():
        if (row["text"] or "").casefold() == key:
            return None
    now = _now()
    cur = conn.execute(
        "INSERT INTO world_facts (kind, text, status, happened_at, created_at, last_seen_at)"
        " VALUES (?, ?, 'active', ?, ?, ?)", (kind, text, happened_at, now, now))
    conn.commit()
    return cur.lastrowid


def world_active(conn, kind, limit=20):
    return conn.execute(
        "SELECT * FROM world_facts WHERE kind=? AND status='active'"
        " ORDER BY last_seen_at DESC, id DESC LIMIT ?", (kind, limit)).fetchall()


def world_find_person(conn, name, kind="person"):
    key = (name or "").strip().casefold()   # Python casefold — Cyrillic-safe
    for row in conn.execute("SELECT * FROM world_facts WHERE kind=?", (kind,)).fetchall():
        if (row["name"] or "").casefold() == key:
            return row
    return None


def world_set_status(conn, fact_id, status):
    conn.execute("UPDATE world_facts SET status=? WHERE id=?", (status, fact_id))
    conn.commit()


# -- long-term body memory (marks / add-ons / adjustments) --------------------

def body_add(conn, feature, permanence="lasting", note=None, fade_days=0):
    """Record a durable body change, deduped by casefold(feature) among active ones (refreshes
    its note/timestamp). A 'mark' with fade_days>0 gets a fades_at so it auto-fades. Returns id."""
    feature = (feature or "").strip()
    if not feature:
        return None
    permanence = permanence if permanence in ("mark", "lasting", "permanent") else "lasting"
    note = (note or "").strip() or None
    now = _now()
    fades_at = None
    if permanence == "mark" and fade_days > 0:
        fades_at = (datetime.now(timezone.utc) + timedelta(days=fade_days)).isoformat()
    key = feature.casefold()
    for row in conn.execute("SELECT id, feature FROM body_state WHERE status='active'").fetchall():
        if (row["feature"] or "").casefold() == key:
            conn.execute(
                "UPDATE body_state SET permanence=?, note=?, added_at=?, fades_at=? WHERE id=?",
                (permanence, note, now, fades_at, row["id"]))
            conn.commit()
            return row["id"]
    cur = conn.execute(
        "INSERT INTO body_state (feature, permanence, note, added_at, fades_at, status)"
        " VALUES (?, ?, ?, ?, ?, 'active')", (feature, permanence, note, now, fades_at))
    conn.commit()
    return cur.lastrowid


def body_active(conn, now=None, limit=20):
    """Active body features, after auto-fading any temporary mark past its fades_at."""
    now = now or _now()
    conn.execute(
        "UPDATE body_state SET status='faded' WHERE status='active' AND permanence='mark'"
        " AND fades_at IS NOT NULL AND fades_at < ?", (now,))
    conn.commit()
    return conn.execute(
        "SELECT * FROM body_state WHERE status='active' ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def body_set_status(conn, body_id, status):
    conn.execute("UPDATE body_state SET status=? WHERE id=?", (status, body_id))
    conn.commit()


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
    """Replace a meeting's embedding chunks. chunks: list of (text, vec_or_None).
    Embeddings stored as packed float32 BLOBs."""
    conn.execute("DELETE FROM meeting_chunks WHERE meeting_id = ?", (meeting_id,))
    for i, (text, embedding) in enumerate(chunks):
        conn.execute(
            "INSERT INTO meeting_chunks (meeting_id, chunk_index, text, embedding)"
            " VALUES (?, ?, ?, ?)",
            (meeting_id, i, text, pack_embedding(embedding)),
        )
    conn.commit()


_MEETING_CHUNK_SQL = (
    "SELECT mc.meeting_id AS meeting_id, mc.text AS text, mc.embedding AS embedding,"
    " m.kind AS kind, m.setting AS setting, m.title AS title, m.started_at AS started_at"
    " FROM meeting_chunks mc JOIN meetings m ON m.id = mc.meeting_id"
    " WHERE mc.embedding IS NOT NULL")
_MEETING_CHUNK_KEYS = ("meeting_id", "text", "kind", "setting", "title", "started_at")


def all_meeting_chunks(conn, chat_id=None):
    """Every embedded meeting chunk with its meeting's kind/setting/title/date,
    DECODED (vector in `vec`). The common (whole-corpus) path is cached; the rare
    per-chat path decodes directly."""
    if chat_id is None:
        return _cached_vectors(conn, "meeting_chunks", _MEETING_CHUNK_SQL, _MEETING_CHUNK_KEYS)
    rows = []
    for r in conn.execute(_MEETING_CHUNK_SQL + " AND m.chat_id = ?", (chat_id,)):
        d = {k: r[k] for k in _MEETING_CHUNK_KEYS}
        d["vec"] = unpack_embedding(r["embedding"])
        if d["vec"] is not None:
            rows.append(d)
    return rows


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


def proactive_key_sent_count(conn, day, check_name):
    """How many times a specific proactive check actually sent on a given UTC day."""
    return conn.execute(
        "SELECT COUNT(*) AS n FROM proactive_log WHERE day = ? AND check_name = ?"
        " AND sent_message = 1", (day, check_name),
    ).fetchone()["n"]


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
    try:
        stk_columns = {row["name"] for row in conn.execute("PRAGMA table_info(stickers)")}
        if stk_columns and "description" not in stk_columns:
            conn.execute("ALTER TABLE stickers ADD COLUMN description TEXT")
        if stk_columns and "thumb_file_id" not in stk_columns:
            conn.execute("ALTER TABLE stickers ADD COLUMN thumb_file_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        mtg_columns = {row["name"] for row in conn.execute("PRAGMA table_info(meetings)")}
        if mtg_columns and "scheduled_for" not in mtg_columns:
            conn.execute("ALTER TABLE meetings ADD COLUMN scheduled_for TEXT")
    except sqlite3.OperationalError:
        pass
    # One-time tea de-emphasis (the original seed life over-indexed on tea — 'a bad
    # joke'). Rebalance the two emphatic tea seed rows on an ALREADY-seeded DB and add a
    # few varied facts. Skip a fresh/empty DB entirely: seed_life plants the full (already
    # de-tea'd) LIFE_SEED there, and inserting a few facts now would make it think it's
    # seeded and skip the rest. Idempotent: UPDATEs match the old text once, INSERT OR
    # IGNORE is a no-op when present.
    try:
        seeded = conn.execute("SELECT COUNT(*) FROM cara_life").fetchone()[0]
    except sqlite3.OperationalError:
        seeded = 0
    if seeded:
        conn.execute(
            "UPDATE cara_life SET text = ? WHERE text = ?",
            ("Ты снимаешь маленькую квартиру у реки; на подоконнике — стопка недочитанных "
             "книг и пара открыток с прошлых поездок.",
             "Ты снимаешь маленькую квартиру у реки; на подоконнике — чайник и стопка "
             "недочитанных книг."))
        conn.execute(
            "UPDATE cara_life SET text = ? WHERE text = ?",
            ("Собираешь маленькие радости дня — удачный кадр, строчку из книги, песню, "
             "что зацепила.",
             "Завариваешь крепкий чёрный чай и почти никогда не пьёшь кофе."))
        for kind, txt in (
            ("music", "Под настроение ставишь старый джаз или что-нибудь тихое и тёплое."),
            ("season", "Любишь дождь за окном и первый снег; от хорошей погоды у тебя "
                       "сразу планы на прогулку."),
            ("food", "Готовишь редко, но с удовольствием; обожаешь рынок выходного дня "
                     "и свежий хлеб."),
        ):
            conn.execute(
                "INSERT OR IGNORE INTO cara_life (kind, text, created_at) VALUES (?, ?, ?)",
                (kind, txt, _now()))
    # Convert legacy JSON-text embeddings to packed float32 BLOBs (one-time;
    # idempotent — after conversion typeof()='blob' so the scan finds nothing).
    for tbl in ("chunks", "meeting_chunks"):
        try:
            legacy = conn.execute(
                f"SELECT id, embedding FROM {tbl}"
                f" WHERE embedding IS NOT NULL AND typeof(embedding) = 'text'").fetchall()
        except sqlite3.OperationalError:
            continue  # table not present on a very old db (SCHEMA creates it anyway)
        for r in legacy:
            try:
                vec = json.loads(r["embedding"])
            except (TypeError, ValueError):
                continue
            conn.execute(f"UPDATE {tbl} SET embedding = ? WHERE id = ?",
                         (pack_embedding(vec), r["id"]))


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


def merge_categories(conn, src, dst):
    """Fold a duplicate category `src` into `dst`: move every message (confirmed AND
    still-suggested) from src to dst, then delete the now-empty src category. Returns
    (moved_count, dst_canonical_name), or (0, None) if src doesn't exist; (0, dst) if
    src == dst. Preserves message ids/embeddings (only the category string changes)."""
    src_row = conn.execute("SELECT name FROM categories WHERE norm_key = ?",
                           (str(src or "").casefold(),)).fetchone()
    if not src_row:
        return 0, None
    src_name = src_row["name"]
    dst_name = ensure_category(conn, dst)
    if src_name.casefold() == dst_name.casefold():
        return 0, dst_name
    moved = conn.execute("UPDATE messages SET category = ? WHERE category = ?",
                         (dst_name, src_name)).rowcount
    conn.execute("UPDATE messages SET suggested_category = ? WHERE suggested_category = ?",
                 (dst_name, src_name))
    conn.execute("DELETE FROM categories WHERE norm_key = ?", (src_name.casefold(),))
    conn.commit()
    return moved, dst_name


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


def files_recent_full(conn, chat_id, limit=5):
    """Recent stored files (full rows incl. tg_file_id) for a chat, newest first — so a
    forwarded voice/document can be re-fetched and read on demand."""
    return conn.execute(
        "SELECT f.* FROM files f JOIN messages m ON m.id = f.message_id"
        " WHERE m.chat_id = ? ORDER BY f.id DESC LIMIT ?", (chat_id, limit),
    ).fetchall()


def set_chunks(conn, message_id, chunks):
    """Replace a message's embedding chunks (idempotent for re-indexing).
    chunks: list of (text, embedding_list_or_None). Embeddings are stored as
    packed float32 BLOBs."""
    conn.execute("DELETE FROM chunks WHERE message_id = ?", (message_id,))
    for i, (text, embedding) in enumerate(chunks):
        conn.execute(
            "INSERT INTO chunks (message_id, chunk_index, text, embedding) VALUES (?, ?, ?, ?)",
            (message_id, i, text, pack_embedding(embedding)),
        )
    conn.commit()


_NOTE_CHUNK_SQL = (
    "SELECT c.message_id AS message_id, c.text AS text, c.embedding AS embedding,"
    " m.category AS category, m.suggested_category AS suggested_category,"
    " m.forward_origin_title AS title, m.received_at AS received_at"
    " FROM chunks c JOIN messages m ON m.id = c.message_id"
    " WHERE c.embedding IS NOT NULL")


def all_embedded_chunks(conn):
    """Every embedded note chunk with its message's category/title, DECODED and
    CACHED (the vector is in `vec`; re-decoded only when the table changes)."""
    return _cached_vectors(conn, "chunks", _NOTE_CHUNK_SQL,
                           ("message_id", "text", "category", "suggested_category",
                            "title", "received_at"))


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
    # The general notes list hides CONFIRMED journal entries (they have their own dated
    # journal); an explicit category filter still shows everything in that category.
    journals = {n.casefold() for n in journal_categories(conn)} if category is None else set()
    result = []
    for row in rows:
        row_category = row["category"] or row["suggested_category"] or ""
        if category and row_category.casefold() != str(category).casefold():
            continue
        if journals and (row["category"] or "").casefold() in journals:
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


def list_messages_filtered(conn, category=None, query=None):
    """ALL visible notes matching the filter, newest-first (Python-filtered for Cyrillic
    casefold). Returns the full list; callers slice for pagination. Confirmed journal entries
    are hidden from the general list (they have their own dated journal) unless a category
    filter is given."""
    rows = conn.execute(
        "SELECT * FROM messages WHERE status IN ('confirmed', 'suggested') ORDER BY id DESC"
    ).fetchall()
    facts_by_message = {}
    if query:
        for row in conn.execute(
            "SELECT message_id, GROUP_CONCAT(fact, ' ') AS f FROM facts GROUP BY message_id"
        ):
            facts_by_message[row["message_id"]] = row["f"]
    journals = {n.casefold() for n in journal_categories(conn)} if category is None else set()
    out = []
    for row in rows:
        row_category = row["category"] or row["suggested_category"] or ""
        if category and row_category.casefold() != str(category).casefold():
            continue
        if journals and (row["category"] or "").casefold() in journals:
            continue
        if query:
            haystack = " ".join(filter(None, [
                row["raw_text"], row["summary"], row_category, row["forward_origin_title"],
                facts_by_message.get(row["id"]),
            ])).casefold()
            if str(query).casefold() not in haystack:
                continue
        out.append(row)
    return out


def list_messages_page(conn, category=None, query=None, offset=0, limit=8):
    """A page of filtered notes plus the TOTAL match count: (rows, total)."""
    allrows = list_messages_filtered(conn, category, query)
    offset = max(0, offset)
    return allrows[offset:offset + limit], len(allrows)


def list_view_add(conn, chat_id, filter_dict):
    """Persist the filter behind a paginated list; returns a token (its id) for callback_data."""
    cur = conn.execute(
        "INSERT INTO list_views (chat_id, filter, created_at) VALUES (?, ?, ?)",
        (chat_id, json.dumps(filter_dict, ensure_ascii=False), _now()))
    conn.commit()
    return cur.lastrowid


def list_view_get(conn, token):
    """The stored filter dict for a list token, or None if unknown/expired."""
    try:
        token = int(token)
    except (TypeError, ValueError):
        return None
    row = conn.execute("SELECT filter FROM list_views WHERE id = ?", (token,)).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["filter"])
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def list_views_prune(conn, cutoff_iso):
    cur = conn.execute("DELETE FROM list_views WHERE created_at < ?", (cutoff_iso,))
    conn.commit()
    return cur.rowcount


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
    """Visible-note ids in display order (oldest first); position = number. A CONFIRMED
    journal entry lives in its dated journal (journal_show), so it's excluded from the
    #N notes list/numbering; a still-suggested one (category not yet set) stays for its card."""
    journals = {n.casefold() for n in journal_categories(conn)}
    out = []
    for r in conn.execute(
            "SELECT id, category FROM messages WHERE status IN ('confirmed', 'suggested')"
            " ORDER BY id ASC"):
        if journals and (r["category"] or "").casefold() in journals:
            continue
        out.append(r["id"])
    return out


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


def reminders_expire_stale(conn, cutoff_iso):
    """Auto-close one-shot reminders that fired but were never acked and whose fire time is
    older than the cutoff — so the 'ждёт готово' list doesn't grow forever. Returns count."""
    cur = conn.execute(
        "UPDATE reminders SET status = 'expired' WHERE status = 'active'"
        " AND recurrence = 'none' AND last_fired_at IS NOT NULL AND last_fired_at < ?",
        (cutoff_iso,),
    )
    conn.commit()
    return cur.rowcount


def reminder_display_no(conn, chat_id, rid):
    """1..N position of an active reminder in the boss-facing (due-ordered) list,
    or None if it isn't active. The number compacts as reminders fire/cancel; the
    immutable id stays the key for fired-pending payloads, calendar, and history."""
    for i, row in enumerate(reminders_active(conn, chat_id), start=1):
        if row["id"] == rid:
            return i
    return None


def reminders_due(conn, now_iso):
    # A one-shot that already fired stays 'active' (visible/snoozable until the boss
    # closes it) — exclude it from re-firing via last_fired_at. A reschedule/snooze
    # moves due_utc into the future (> last_fired_at), which re-arms it automatically.
    return conn.execute(
        "SELECT * FROM reminders WHERE status = 'active' AND due_utc <= ?"
        " AND (last_fired_at IS NULL OR last_fired_at < due_utc) ORDER BY due_utc",
        (now_iso,),
    ).fetchall()


def reminder_touch_fired(conn, rid, when=None):
    """Stamp a reminder as fired NOW (stops a one-shot re-firing; never closes it)."""
    conn.execute("UPDATE reminders SET last_fired_at = ? WHERE id = ?",
                 (when or _now(), rid))
    conn.commit()


def reminder_update_due(conn, rid, due_utc):
    """Move a reminder, remembering its current time in prev_due_utc so a reschedule can
    be undone ('верни предыдущее время'). Clears last_fired_at — a reschedule/snooze
    RE-ARMS the reminder, so it's a fresh future reminder, not one still 'сработало, ждёт
    готово' (the marker must not linger after it's moved to a new time)."""
    conn.execute(
        "UPDATE reminders SET prev_due_utc = due_utc, due_utc = ?, last_fired_at = NULL"
        " WHERE id = ?",
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


def reminder_rename(conn, rid, title):
    """Retitle an existing reminder in place (keeps id, time, recurrence, history)."""
    conn.execute("UPDATE reminders SET title = ? WHERE id = ?", (title, rid))
    conn.commit()


def reminder_close(conn, rid, status="done"):
    conn.execute(
        "UPDATE reminders SET status = ?, last_fired_at = ? WHERE id = ?",
        (status, _now(), rid),
    )
    conn.commit()


# -- Stickers (packs Cara may use in conversation) ---------------------------

def stickers_add(conn, set_name, stickers):
    """Insert each sticker of a set (dicts with file_id/file_unique_id/emoji and,
    optionally, thumb_file_id/description). Idempotent via UNIQUE(file_unique_id):
    a re-save updates a now-known thumbnail/description for an existing row.
    Returns how many are now saved for the set."""
    for s in stickers:
        fid = s.get("file_id")
        if not fid:
            continue
        thumb = (s.get("thumbnail") or s.get("thumb") or {})
        thumb_id = s.get("thumb_file_id") or thumb.get("file_id")
        conn.execute(
            "INSERT INTO stickers (set_name, file_id, file_unique_id, emoji, thumb_file_id,"
            " description, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(file_unique_id) DO UPDATE SET"
            "   thumb_file_id = COALESCE(excluded.thumb_file_id, thumb_file_id),"
            "   description   = COALESCE(excluded.description, description)",
            (set_name, fid, s.get("file_unique_id"), s.get("emoji"), thumb_id,
             s.get("description"), _now()),
        )
    conn.commit()
    return conn.execute(
        "SELECT COUNT(*) FROM stickers WHERE set_name = ?", (set_name,)
    ).fetchone()[0]


def sticker_count(conn):
    return conn.execute("SELECT COUNT(*) FROM stickers").fetchone()[0]


def sticker_random_row(conn, exclude_uid=None):
    """A random saved sticker as {file_id, file_unique_id}, avoiding `exclude_uid`
    when another is available (so the same one isn't sent twice in a row)."""
    row = conn.execute(
        "SELECT file_id, file_unique_id FROM stickers WHERE file_unique_id IS NOT ?"
        " ORDER BY RANDOM() LIMIT 1", (exclude_uid,)
    ).fetchone()
    if not row:  # only the excluded one exists -> allow it
        row = conn.execute(
            "SELECT file_id, file_unique_id FROM stickers ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def sticker_random(conn):  # back-compat: file_id only
    row = sticker_random_row(conn)
    return row["file_id"] if row else None


def sticker_pick(conn, emoji, exclude_uid=None):
    """A saved sticker {file_id, file_unique_id} whose emoji matches `emoji` (exact
    first, then any that contains it), preferring one that isn't `exclude_uid` so the
    identical sticker isn't repeated back-to-back. None if none match."""
    emoji = (emoji or "").strip()
    if not emoji:
        return None
    for clause, arg in (("emoji = ?", emoji), ("emoji LIKE ?", f"%{emoji}%")):
        row = conn.execute(
            f"SELECT file_id, file_unique_id FROM stickers WHERE {clause}"
            " AND file_unique_id IS NOT ? ORDER BY RANDOM() LIMIT 1", (arg, exclude_uid)
        ).fetchone()
        if row:
            return dict(row)
        row = conn.execute(  # fall back to the excluded one if it's the only match
            f"SELECT file_id, file_unique_id FROM stickers WHERE {clause}"
            " ORDER BY RANDOM() LIMIT 1", (arg,)
        ).fetchone()
        if row:
            return dict(row)
    return None


def sticker_for_emoji(conn, emoji):  # back-compat: file_id only
    row = sticker_pick(conn, emoji)
    return row["file_id"] if row else None


def stickers_described(conn, limit=40):
    """Saved stickers that HAVE a real image description, as (emoji, description), for
    the converse prompt — so she picks one whose actual picture fits the moment."""
    return conn.execute(
        "SELECT emoji, description FROM stickers"
        " WHERE description IS NOT NULL AND description != ''"
        " ORDER BY RANDOM() LIMIT ?", (limit,)
    ).fetchall()


def stickers_undescribed(conn, limit=12):
    """Stickers not yet ATTEMPTED for description (description IS NULL). A failed attempt
    stores '' so it isn't retried forever; a successful one stores the text."""
    return conn.execute(
        "SELECT id, set_name, file_id, file_unique_id, thumb_file_id, emoji FROM stickers"
        " WHERE description IS NULL LIMIT ?", (limit,)
    ).fetchall()


def sticker_set_description(conn, file_unique_id, description):
    conn.execute("UPDATE stickers SET description = ? WHERE file_unique_id = ?",
                 (description, file_unique_id))
    conn.commit()


def sticker_set_thumb(conn, file_unique_id, thumb_file_id):
    conn.execute("UPDATE stickers SET thumb_file_id = ? WHERE file_unique_id = ?",
                 (thumb_file_id, file_unique_id))
    conn.commit()


# -- Cara's wardrobe / style -------------------------------------------------

def cara_style_get(conn):
    return kv_get(conn, "cara_style")


def cara_style_set(conn, text):
    kv_set(conn, "cara_style", text)


def _wardrobe_row(row):
    """Decode a cara_wardrobe row into a dict with JSON list fields parsed."""
    def _list(v):
        try:
            return json.loads(v) if v else []
        except (TypeError, ValueError):
            return []
    return {
        "id": row["id"], "name": row["name"], "family": row["family"],
        "season": _list(row["season"]), "intimacy": row["intimacy"],
        "colors": _list(row["colors"]), "pieces": _list(row["pieces"]),
        "footwear": row["footwear"], "signature": bool(row["signature"]),
        "surprise": bool(row["surprise"]), "last_worn_at": row["last_worn_at"] or "",
    }


def wardrobe_add(conn, o):
    """Insert one outfit (dict from wardrobe.WARDROBE_SEED). Idempotent on id."""
    conn.execute(
        "INSERT OR IGNORE INTO cara_wardrobe (id, name, family, season, intimacy, colors,"
        " pieces, footwear, signature, surprise, added_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (o["id"], o["name"], o["family"], json.dumps(o.get("season") or []),
         int(o.get("intimacy") or 0), json.dumps(o.get("colors") or []),
         json.dumps(o.get("pieces") or []), o.get("footwear"),
         1 if o.get("signature") else 0, 1 if o.get("surprise") else 0, _now()),
    )
    conn.commit()


def wardrobe_count(conn):
    return conn.execute("SELECT COUNT(*) FROM cara_wardrobe").fetchone()[0]


def wardrobe_candidates(conn, families, max_intimacy):
    """Outfits in any of `families` with intimacy <= max_intimacy, least-recently-worn
    first (never-worn first). Returns decoded dicts."""
    if not families:
        return []
    marks = ",".join("?" for _ in families)
    rows = conn.execute(
        f"SELECT * FROM cara_wardrobe WHERE family IN ({marks}) AND intimacy <= ?"
        " ORDER BY (last_worn_at IS NULL) DESC, last_worn_at ASC",
        (*families, int(max_intimacy)),
    ).fetchall()
    return [_wardrobe_row(r) for r in rows]


def wardrobe_get(conn, outfit_id):
    row = conn.execute("SELECT * FROM cara_wardrobe WHERE id = ?", (outfit_id,)).fetchone()
    return _wardrobe_row(row) if row else None


def wardrobe_mark_worn(conn, outfit_id):
    conn.execute("UPDATE cara_wardrobe SET last_worn_at = ? WHERE id = ?", (_now(), outfit_id))
    conn.commit()


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
