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


# Conversational learning -------------------------------------------------------
# Cara grows from free chat: her own (fictional) life fills in automatically, and
# benign facts the boss reveals are learned as correctable "inferred" items. Real
# personal/sensitive data is never auto-stored — it becomes a candidate he must
# confirm, preserving the spec §21 boundary for anything that matters.

LIFE_KINDS = {"hobby", "friend", "place", "habit", "plan", "mood", "moment",
              "home", "dream", "work", "taste"}

# Behavioral-correction kinds — standing guidance Cara must follow going forward.
GUIDANCE_KINDS = {"tone", "workflow", "avoidance", "quality_bar"}

_EXTRACT_SYSTEM = (
    "You extract durable memory from a chat between Cara (a warm assistant who has "
    "her own life) and her boss. Return STRICT JSON only, no prose:\n"
    '{"cara_life": [{"kind": "...", "text": "..."}], '
    '"boss_facts": [{"kind": "...", "text": "..."}], '
    '"corrections": [{"kind": "...", "text": "..."}]}\n'
    "cara_life: NEW, lasting details Cara revealed about HER OWN life (a hobby, a "
    "friend, a place, a plan, a taste). Each a short statement addressed to Cara in "
    "her language, e.g. 'Ты любишь джаз.' / 'You're learning to bake.' Skip anything "
    "already listed as known, and anything merely momentary.\n"
    "boss_facts: NEW, lasting facts the BOSS stated about HIMSELF (a preference, a "
    "project, a habit, a personal fact). Short third-person in his language, e.g. "
    "'Любит короткие ответы.' kind in: tone, workflow, quality_bar, avoidance, "
    "project, personal_fact, category_preference, identity.\n"
    "corrections: standing instructions the BOSS gave about HOW CARA SHOULD BEHAVE "
    "going forward — something to do or to stop doing, or a mistake not to repeat "
    "(e.g. reply in the language he writes in; be shorter; don't switch languages). "
    "Write each as a short imperative IN HIS LANGUAGE addressed to Cara, e.g. "
    "'Отвечай на том языке, на котором он пишет.' kind in: tone, workflow, "
    "avoidance, quality_bar. Capture it even if he complained only once. Do NOT "
    "capture insults, venting, emotions, or one-off task content — only durable "
    "behavioral rules Cara can actually follow.\n"
    "Never invent — only what the text plainly supports. Use empty arrays when "
    "nothing durable is new."
)


def curate_conversation(conn, cfg, chat_id, limit=12, correction_mode=False):
    """One small LLM pass over recent free chat. Returns counts plus the lists of
    newly-`learned` corrections and `unresolved` ones (a correction raised again
    despite being learned → likely needs a code fix). Dedup (UNIQUE life text,
    boss substring match, candidate text) makes overlapping windows safe to re-run.
    `correction_mode` (set when the boss just corrected her) enables the
    recurrence → needs-code escalation."""
    import llm  # lazy: keeps the deterministic curator import-light
    turns = [r for r in store.convo_recent(conn, chat_id, limit=limit)
             if (r["text"] or "").strip()]
    if len(turns) < 2:
        return {"life": 0, "boss": 0}
    transcript = "\n".join(
        f"{'Boss' if r['role'] == 'user' else 'Cara'}: {r['text']}" for r in turns)
    known = [r["text"] for r in store.life_facts(conn, limit=40)]
    known_block = "\n".join(f"- {t}" for t in known[-24:]) or "(none yet)"
    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user",
         "content": f"Known about Cara's life:\n{known_block}\n\nConversation:\n{transcript}"},
    ]
    try:
        reply = llm.chat_profile(cfg, conn, "memory_curator", messages,
                                 profile="memory_curator")
    except (llm.BudgetExceeded, llm.LLMError):
        return {"life": 0, "boss": 0}
    parsed = llm.parse_llm_json(reply) or {}

    life_added = 0
    for item in (parsed.get("cara_life") or [])[:5]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        kind = str(item.get("kind") or "moment").strip().lower()
        if kind not in LIFE_KINDS:
            kind = "moment"
        if text and store.life_add(conn, kind, text):
            life_added += 1

    boss_added = 0
    for item in (parsed.get("boss_facts") or [])[:5]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        kind = str(item.get("kind") or "personal_fact").strip().lower()
        if not text or boss_model.is_duplicate(conn, text):  # skip reworded repeats
            continue
        sens = boss_model.effective_sensitivity(kind, text)
        # Auto-learn only benign facts that don't clash with something he already
        # confirmed. Sensitive OR contradicting facts are proposed for confirmation
        # — never silently auto-stored (avoids "confidently wrong intimacy").
        benign = boss_model.SENS_ORDER[sens] <= boss_model.SENS_ORDER["normal"]
        if benign and not boss_model.conflicts_with_confirmed(conn, text):
            store.boss_add(conn, kind, text, status="inferred", confidence=0.7,
                           sensitivity=sens, source_table="conversation")
            boss_added += 1
        elif store.candidate_add(conn, kind, text, reason="from conversation",
                                 sensitivity=sens, confidence=0.7,
                                 source_table="conversation"):
            boss_added += 1  # sensitive or conflicting -> propose, never auto-store

    # Behavioral corrections: store as standing guidance Cara honors next turn,
    # log each new one as an issue, and escalate ones that recur despite being
    # learned (they likely need a code change, not more "trying").
    corrections_added = 0
    learned, unresolved = [], []
    for item in (parsed.get("corrections") or [])[:5]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        kind = str(item.get("kind") or "workflow").strip().lower()
        if kind not in GUIDANCE_KINDS:
            kind = "workflow"
        if not text:
            continue
        if boss_model.is_duplicate(conn, text):
            # already learned, yet he's correcting it again -> auto-apply isn't
            # enough; flag for a code fix. Only when he actively corrected this
            # turn (not on background re-processing of an old window).
            if correction_mode:
                store.issue_add(conn, chat_id, "correction_unresolved", text)
                unresolved.append(text)
            continue
        store.issue_add(conn, chat_id, "correction", text)
        sens = boss_model.effective_sensitivity(kind, text)
        if boss_model.SENS_ORDER[sens] <= boss_model.SENS_ORDER["normal"]:
            store.boss_add(conn, kind, text, status="inferred", confidence=0.8,
                           sensitivity=sens, source_table="correction")
        else:
            store.candidate_add(conn, kind, text, reason="correction",
                                sensitivity=sens, confidence=0.8, source_table="correction")
        learned.append(text)
        corrections_added += 1
    return {"life": life_added, "boss": boss_added, "corrections": corrections_added,
            "learned": learned, "unresolved": unresolved}


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
                      importance=2, source_table="memory_candidates", source_id=candidate_id,
                      title="learned about you")
        return cand["proposed_text"], True
    store.candidate_set_status(conn, candidate_id, "rejected")
    return cand["proposed_text"], False
