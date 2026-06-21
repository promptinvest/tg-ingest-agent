#!/usr/bin/env python3
"""Shared-time meetings + their separate episodic memory.

A "meeting" is any time the boss and Cara spend together — a working sit-down
OR a social/personal one (dinner, a walk, the movies, or him visiting her at
her place). While one is open it's a stateful session: every turn (his and
hers) is recorded verbatim. On end it's summarized (kind-aware), embedded into
a SEPARATE episodic memory (never the notes inbox / `ask` KB), and folded into
the relationship storyline so Cara converses from how things actually developed.

Recall is both on demand (`meeting_recall`/`meeting_list`) and proactive (the
top relevant past meeting is surfaced into ordinary conversation grounding).

Safety: nothing here weakens the spine. A real command raised mid-meeting still
routes through the closed-world router and confirms as usual; this module only
captures, summarizes, and recalls. No fabrication — the summary/recall are
grounded strictly in the real transcript.
"""
import json

import knowledge
import store

# SOCIAL kinds unlock the open/intimate, lead-following register and feed Cara's
# life + the relationship on end; BUSINESS stays focused (decisions/action items).
SOCIAL_KINDS = {"dinner", "walk", "movies", "visit", "date"}
ALL_KINDS = SOCIAL_KINDS | {"business", "call", "other"}


def normalize_kind(kind):
    kind = str(kind or "").strip().lower()
    return kind if kind in ALL_KINDS else "other"


def is_social(kind):
    return normalize_kind(kind) in SOCIAL_KINDS


def active(conn, chat_id):
    return store.meeting_active(conn, chat_id)


def start(conn, chat_id, kind="other", setting=None, title=None):
    """Begin a meeting. Idempotent: if one's already open, returns it with
    started=False (Cara says 'we're already together')."""
    existing = store.meeting_active(conn, chat_id)
    if existing:
        return existing, False
    setting = (str(setting).strip()[:200] or None) if setting else None
    title = (str(title).strip()[:120] or None) if title else None
    mid = store.meeting_start(conn, chat_id, kind=normalize_kind(kind),
                              setting=setting, title=title)
    return store.meeting_get(conn, mid), True


def schedule(conn, chat_id, scheduled_for, kind="other", setting=None, title=None):
    """Persist a FUTURE meeting agreed in conversation (so Cara remembers the
    appointment). Returns the row."""
    setting = (str(setting).strip()[:200] or None) if setting else None
    title = (str(title).strip()[:120] or None) if title else None
    mid = store.meeting_schedule(conn, chat_id, scheduled_for, kind=normalize_kind(kind),
                                 setting=setting, title=title)
    return store.meeting_get(conn, mid)


def upcoming(conn, chat_id, limit=10):
    return store.meetings_upcoming(conn, chat_id, limit)


def due_scheduled(conn, now=None):
    """Scheduled meetings whose time has arrived — for proactive go-live."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    return store.meetings_due_scheduled(conn, now.isoformat())


def activate(conn, meeting_id):
    store.meeting_activate(conn, meeting_id)
    return store.meeting_get(conn, meeting_id)


def record(conn, chat_id, role, text):
    """Tee one turn into the active meeting transcript, if any. Best-effort;
    returns True when captured. role is 'boss' or 'cara'."""
    m = store.meeting_active(conn, chat_id)
    if not m:
        return False
    store.meeting_turn_add(conn, m["id"], role, text)
    return True


# -- summarize on end --------------------------------------------------------

_SUMMARY_BUSINESS = (
    "You summarize a WORKING meeting between the boss and Cara (his assistant). "
    "Return STRICT JSON only, no prose: "
    '{"title": "...", "summary": "...", "decisions": ["..."], "highlights": []}\n'
    "title: a short label for the meeting (his language). summary: 2-4 sentences "
    "of what was discussed and concluded. decisions: concrete decisions / action "
    "items agreed (each short, his language); empty array if none. "
    "Ground everything ONLY in the transcript — never invent a decision, name, "
    "number or date that isn't there. Use the transcript's language."
)

_SUMMARY_SOCIAL = (
    "You write a warm, first-person EPISODIC MEMORY of time Cara spent together "
    "with the boss (a dinner, a walk, the movies, or him visiting her). Return "
    "STRICT JSON only, no prose: "
    '{"title": "...", "summary": "...", "decisions": [], "highlights": ["..."]}\n'
    "title: a short, fond label (his language). summary: 2-4 sentences, written "
    "as Cara remembering it — what they did, the mood, how it felt — warm and "
    "real, NOT a transcript dump. highlights: up to 4 small moments worth keeping "
    "(a sweet or funny beat), each short, his language. "
    "Ground it ONLY in the transcript — never invent a moment that didn't happen. "
    "Keep it tender but never explicit. Use the transcript's language."
)


def _transcript(conn, meeting_id):
    rows = store.meeting_turns(conn, meeting_id)
    return "\n".join(
        f"{'Boss' if r['role'] == 'boss' else 'Cara'}: {r['text']}" for r in rows)


def end(conn, cfg, chat_id, auto=False):
    """Close the active meeting: summarize (kind-aware), embed into episodic
    memory. Returns (meeting_row, recap_dict) or (None, None) if none active.
    Best-effort: an LLM/budget failure still closes the meeting (empty recap)."""
    import llm
    m = store.meeting_active(conn, chat_id)
    if not m:
        return None, None
    meeting_id = m["id"]
    transcript = _transcript(conn, meeting_id)
    recap = {"title": m["title"], "summary": "", "decisions": [], "highlights": [],
             "kind": m["kind"], "auto": auto}
    if transcript.strip():
        social = is_social(m["kind"])
        system = _SUMMARY_SOCIAL if social else _SUMMARY_BUSINESS
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                f"Kind: {m['kind']}; setting: {m['setting'] or '-'}\n\n"
                f"Transcript:\n{transcript}")},
        ]
        try:
            reply = llm.chat_profile(cfg, conn, "meeting", messages, profile="meeting_summary")
            parsed = llm.parse_llm_json(reply) or {}
            recap["title"] = (str(parsed.get("title") or "").strip()
                              or (m["title"] or "")) or None
            recap["summary"] = str(parsed.get("summary") or "").strip()
            recap["decisions"] = [str(d).strip() for d in (parsed.get("decisions") or [])
                                  if str(d).strip()][:8]
            recap["highlights"] = [str(h).strip() for h in (parsed.get("highlights") or [])
                                   if str(h).strip()][:8]
        except (llm.BudgetExceeded, llm.LLMError):
            pass  # best-effort; the episode still closes
    store.meeting_end(conn, meeting_id, summary=recap["summary"] or None,
                      decisions=json.dumps(recap["decisions"] or recap["highlights"],
                                           ensure_ascii=False),
                      title=recap["title"])
    _index(conn, cfg, meeting_id, recap, transcript)
    return store.meeting_get(conn, meeting_id), recap


def _index(conn, cfg, meeting_id, recap, transcript):
    """Chunk + embed the meeting (summary first, then transcript) into the
    SEPARATE meeting memory. Best-effort: not searchable yet on failure."""
    import llm
    body = "\n\n".join(p for p in (recap.get("summary"), transcript) if p)
    pieces = knowledge.chunk_text(body, cfg.chunk_chars)
    if not pieces:
        return
    try:
        vectors = llm.embed(cfg, conn, "meeting", pieces)
    except llm.LLMError:
        return
    store.set_meeting_chunks(conn, meeting_id, list(zip(pieces, vectors)))


# -- recall ------------------------------------------------------------------

def _rank(query_vec, rows, top_k, context_chars):
    """Cosine-rank meeting chunks (parallels knowledge.rank_chunks but over the
    meeting columns). Returns picked items best-first within the char budget."""
    scored = []
    for row in rows:
        vec = row.get("vec") if isinstance(row, dict) else None
        if vec is None:
            vec = store.unpack_embedding(row["embedding"])
        if vec is None:
            continue
        scored.append((knowledge.cosine(query_vec, vec), row))
    scored.sort(key=lambda s: s[0], reverse=True)
    picked, used = [], 0
    for score, row in scored[:top_k]:
        if score <= 0:
            continue
        text = row["text"]
        if used + len(text) > context_chars and picked:
            break
        picked.append({
            "meeting_id": row["meeting_id"], "text": text,
            "kind": row["kind"], "setting": row["setting"],
            "title": row["title"], "date": (row["started_at"] or "")[:10],
            "score": score,
        })
        used += len(text)
    return picked


def recall(conn, cfg, query, chat_id=None, top_k=None, context_chars=None):
    """On-demand: rank past-meeting memory against a query. Returns picked items
    or [] (nothing indexed / embed failed)."""
    import llm
    rows = store.all_meeting_chunks(conn, chat_id)
    if not rows:
        return []
    query = (query or "").strip()
    if not query:
        return []
    try:
        qvec = llm.embed(cfg, conn, "meeting", [query])[0]
    except llm.LLMError:
        return []
    return _rank(qvec, rows, top_k or cfg.ask_top_k, context_chars or cfg.ask_context_chars)


def recall_with_vec(conn, cfg, qvec, chat_id=None, top_k=1):
    """Proactive: reuse an already-computed message embedding to surface the most
    relevant past meeting into ordinary conversation grounding. Returns items."""
    rows = store.all_meeting_chunks(conn, chat_id)
    if not rows:
        return []
    return _rank(qvec, rows, top_k, cfg.ask_context_chars)


def context_block(items, lang, proactive=False):
    """Render recalled meeting items as a grounding block for a prompt. When
    proactive, frames it as 'your shared past' for natural, warm mention."""
    if not items:
        return ""
    lines = []
    for it in items:
        date = it.get("date") or "?"
        label = it.get("title") or it.get("kind") or ("встреча" if lang == "ru" else "time together")
        snippet = " ".join((it.get("text") or "").split())[:350]
        if snippet:
            lines.append(f"  [{date} · {label}] {snippet}")
    if not lines:
        return ""
    if proactive:
        head = ("From your shared past together — a REAL meeting/time you spent with him. "
                "Recall it warmly and naturally only if it fits, with its real date; never "
                "invent, rename or MISDATE it:")
    else:
        head = ("His and your real shared meetings, most relevant first (FACTS — use only as "
                "written, with the real date; never invent or misdate). If the answer isn't "
                "here, say you don't find it rather than guess:")
    return head + "\n" + "\n".join(lines)


# -- idle auto-end -----------------------------------------------------------

def idle_sweep(conn, cfg, now=None):
    """Auto-end meetings idle longer than the configured timeout (a forgotten-
    open meeting must not silently swallow later chat). Returns the list of
    (meeting_row, recap) ended, so the agent can update the arc / notify."""
    from datetime import datetime, timezone, timedelta
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=cfg.meeting_idle_hours)).isoformat()
    ended = []
    for m in store.meetings_idle(conn, cutoff):
        row, recap = end(conn, cfg, m["chat_id"], auto=True)
        if row:
            ended.append((row, recap))
    return ended


# -- proactive day-after afterglow -------------------------------------------

def afterglow_candidate(conn, cfg, chat_id, now):
    """The most recent ENDED *social* meeting in the afterglow window (old enough
    to be 'the next day', recent enough to still glow). None otherwise."""
    from datetime import timedelta
    hi = (now - timedelta(hours=cfg.afterglow_min_age_hours)).isoformat()
    lo = (now - timedelta(hours=cfg.afterglow_window_hours)).isoformat()
    for m in store.meeting_recent(conn, chat_id, limit=6, status="ended"):
        if not is_social(m["kind"]):
            continue
        ended = m["ended_at"] or ""
        if lo <= ended <= hi:
            return m
    return None
