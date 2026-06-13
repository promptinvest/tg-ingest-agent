#!/usr/bin/env python3
"""Memory curator: proposes durable-memory candidates from evidence (repeated
corrections, learned habits, explicit patterns) — never writes durable memory
itself; the boss confirms. Runs as a background job (no proactive nudges; the
boss pulls candidates via the memory_review action).

Deterministic by default (spec §21: deterministic candidates first). An
optional LLM extraction pass is gated behind MEMORY_CURATOR_LLM.
"""
import boss_model
import store
from texts import T

# score gate (spec §9.1): only confident, low-sensitivity, useful kinds surface
USEFUL_KINDS = {"workflow", "tone", "quality_bar", "avoidance", "category_preference"}


def score(kind, sensitivity, confidence, text):
    s = 0
    if confidence >= 0.8:
        s += 2
    if kind in USEFUL_KINDS:
        s += 2
    if sensitivity == "normal":
        s += 1
    if sensitivity in ("sensitive", "secret"):
        s -= 5
    if len(text) > 180:
        s -= 1
    return s


def run_daily(conn):
    """Build pending candidates from evidence. Returns the count created.
    Idempotent: candidate_add dedupes by text."""
    created = 0
    # Repeated category corrections -> a category-preference candidate.
    rows = conn.execute(
        "SELECT corrected, COUNT(*) AS n FROM feedback WHERE corrected IS NOT NULL"
        " GROUP BY corrected HAVING n >= 2 ORDER BY n DESC LIMIT 10"
    ).fetchall()
    for row in rows:
        text = (f"часто переносите сообщения в категорию «{row['corrected']}»"
                f" (замечено {row['n']} раз)")
        sens = boss_model.classify_sensitivity(row["corrected"])
        conf = 0.85
        if score("category_preference", sens, conf, text) >= 2:
            if store.candidate_add(conn, "category_preference", text, reason="repeated corrections",
                                   sensitivity=sens, confidence=conf, source_table="feedback"):
                created += 1
    # Source habits the boss enabled -> a workflow candidate (so it's in the
    # boss profile too, not only a hidden auto_cat pref).
    for pref in store.pref_all(conn):
        if pref["key"].startswith("auto_cat:") and pref["value"]:
            text = f"посты из источника {pref['key'].split(':',1)[1]} → «{pref['value']}» автоматически"
            if score("workflow", "normal", 0.9, text) >= 2:
                if store.candidate_add(conn, "workflow", text, reason="confirmed auto-habit",
                                       sensitivity="normal", confidence=0.9, source_table="preferences"):
                    created += 1
    return created


def render_review(conn, lang, limit=8):
    pending = store.candidates_pending(conn, limit)
    if not pending:
        return T(lang, "memory_review_empty")
    lines = [T(lang, "memory_review_header")]
    for c in pending:
        lines.append(f"#{c['id']} {c['proposed_text']}")
    lines.append(T(lang, "memory_review_hint"))
    return "\n".join(lines)


def confirm_candidate(conn, candidate_id, accept):
    """Promote a candidate to a confirmed boss-profile item, or reject it.
    Returns (value, accepted) or (None, None) if the candidate is gone."""
    cand = store.candidate_get(conn, candidate_id)
    if cand is None or cand["status"] != "pending":
        return None, None
    if accept:
        sensitivity = boss_model.effective_sensitivity(cand["kind"], cand["proposed_text"])
        store.boss_add(conn, cand["kind"], cand["proposed_text"], status="confirmed",
                       confidence=1.0, sensitivity=sensitivity,
                       source_table="memory_candidate", source_id=candidate_id)
        store.candidate_set_status(conn, candidate_id, "confirmed")
        store.rel_add(conn, "memory_confirmed", f"confirmed: {cand['proposed_text']}",
                      importance=2, source_table="memory_candidates", source_id=candidate_id)
        return cand["proposed_text"], True
    store.candidate_set_status(conn, candidate_id, "rejected")
    return cand["proposed_text"], False
