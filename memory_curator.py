#!/usr/bin/env python3
"""Memory curator: proposes durable-memory candidates from evidence (repeated
corrections, learned habits, explicit patterns). Confirmed durable memory needs
the boss's yes (memory_review); BENIGN facts learned from conversation are
auto-stored as correctable *inferred* items (curate_conversation) — sensitive
facts, and anything contradicting a confirmed fact, become confirm-first
candidates, never auto-stored. Runs as a background job (no proactive nudges;
the boss pulls candidates via the memory_review action).

Deterministic by default (spec §21: deterministic candidates first). An
optional LLM extraction pass is gated behind MEMORY_CURATOR_LLM.
"""
import re

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
    '{"cara_life": [{"kind": "...", "text": "...", "evidence": "exact Cara quote"}], '
    '"boss_facts": [{"kind": "...", "text": "...", "evidence": "exact Boss quote"}], '
    '"corrections": [{"kind": "...", "text": "...", "evidence": "exact Boss quote"}]}\n'
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
    "EVIDENCE IS REQUIRED for every item. Copy one exact, verbatim quote from the "
    "speaker named above; never cite the other speaker or forwarded data. The quote "
    "must directly support the normalized text, not merely occur nearby. An item with "
    "no exact supporting quote must be omitted. Never invent — only what the text "
    "plainly supports. Use empty arrays when nothing durable is new."
)


_EVIDENCE_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_EVIDENCE_STOP = {
    "and", "the", "that", "this", "with", "from", "your", "you", "his", "her",
    "для", "как", "что", "это", "его", "она", "они", "тебе", "тебя", "мне", "меня",
}


def _normalized_evidence(value):
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _evidence_stems(value):
    words = [w for w in _EVIDENCE_WORD_RE.findall(_normalized_evidence(value))
             if len(w) >= 3 and w not in _EVIDENCE_STOP]
    return {w[:5] for w in words}


def _supported_item(item, source_texts):
    """Require a verbatim source quote plus lexical support for the normalized item.

    The exact-quote check enforces speaker attribution. Stem overlap prevents the
    model from attaching an unrelated real quote (for example ``Давай md``) to an
    invented weekly-summary preference.
    """
    evidence = str(item.get("evidence") or "").strip()
    text = str(item.get("text") or "").strip()
    if not evidence or not text:
        return False
    needle = _normalized_evidence(evidence)
    if len(needle) < 3 or not any(needle in _normalized_evidence(src) for src in source_texts):
        return False
    return bool(_evidence_stems(evidence) & _evidence_stems(text))


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
    # Fence forwarded turns (convo_replay_text): a forwarded channel post must not be
    # mined as the boss's OWN fact/correction — otherwise a single forward could poison
    # inferred memory (benign learned facts are auto-stored, not confirm-gated).
    transcript = "\n".join(
        f"{'Boss' if r['role'] == 'user' else 'Cara'}: {store.convo_replay_text(r)}"
        for r in turns)
    boss_sources = [r["text"] for r in turns
                    if r["role"] == "user" and store.convo_row_source(r) != "forward"]
    cara_sources = [r["text"] for r in turns if r["role"] != "user"]
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
        if not isinstance(item, dict) or not _supported_item(item, cara_sources):
            continue
        text = str(item.get("text") or "").strip()
        kind = str(item.get("kind") or "moment").strip().lower()
        if kind not in LIFE_KINDS:
            kind = "moment"
        if text and store.life_add(conn, kind, text):
            life_added += 1

    boss_added = 0
    for item in (parsed.get("boss_facts") or [])[:5]:
        if not isinstance(item, dict) or not _supported_item(item, boss_sources):
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
                           sensitivity=sens, source_table="conversation",
                           evidence=str(item.get("evidence") or "").strip())
            boss_added += 1
        elif store.candidate_add(conn, kind, text, reason="from conversation",
                                 sensitivity=sens, confidence=0.7,
                                 source_table="conversation",
                                 evidence=str(item.get("evidence") or "").strip()):
            boss_added += 1  # sensitive or conflicting -> propose, never auto-store

    # Behavioral corrections: store as standing guidance Cara honors next turn,
    # log each new one as an issue, and escalate ones that recur despite being
    # learned (they likely need a code change, not more "trying").
    corrections_added = 0
    learned, unresolved = [], []
    for item in (parsed.get("corrections") or [])[:5]:
        if not isinstance(item, dict) or not _supported_item(item, boss_sources):
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
                           sensitivity=sens, source_table="correction",
                           evidence=str(item.get("evidence") or "").strip())
        else:
            store.candidate_add(conn, kind, text, reason="correction",
                                sensitivity=sens, confidence=0.8, source_table="correction",
                                evidence=str(item.get("evidence") or "").strip())
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
                       source_table="memory_candidate", source_id=candidate_id,
                       evidence=cand["evidence"])
        store.candidate_set_status(conn, candidate_id, "confirmed")
        store.rel_add(conn, "memory_confirmed", f"confirmed: {cand['proposed_text']}",
                      importance=2, source_table="memory_candidates", source_id=candidate_id,
                      title="learned about you")
        return cand["proposed_text"], True
    store.candidate_set_status(conn, candidate_id, "rejected")
    return cand["proposed_text"], False


# -- memory consolidation (dedup) --------------------------------------------

_CONSOLIDATE_SYSTEM = (
    "You de-duplicate Cara's memory of her boss. You are given remembered items as "
    "'<id>: <text>'. Group items that capture the SAME underlying fact, preference or "
    "standing rule — INCLUDING shorter restatements and paraphrases that only add a small "
    "detail. For example 'Начинает день рано', 'Начинает день рано, уже в работе с утра' and "
    "'Начинает день рано и сразу в работу, есть дисциплина' are ONE fact; 'Его зовут Олег' "
    "and 'Зовут Олег' are one. For each group of 2+, KEEP the single most complete/clear id "
    "and DROP the rest (no information is lost — the kept item is the richest). Keep items "
    "SEPARATE only when they are genuinely DIFFERENT facts (a different topic or a materially "
    "different claim) — never merge two distinct facts. Never invent text — only reference the "
    "given ids. Return STRICT JSON only: "
    '{"groups": [{"keep": <id>, "drop": [<id>, ...]}]}. Empty groups if nothing duplicates.'
)


def _merge_groups(conn, cfg, items, max_items=120):
    """Ask the model to GROUP duplicate items (a list of (id, text)). Returns a list of
    (keep_id, [drop_ids]) for genuine duplicates only. [] when <8 items or on failure.
    Never invents — only references the given ids; tolerates int OR string ids."""
    import llm
    if len(items) < 8:
        return []
    listing = "\n".join(f"{i}: {v}" for i, v in items[:max_items])
    try:
        reply = llm.chat_profile(
            cfg, conn, "memory_curator",
            [{"role": "system", "content": _CONSOLIDATE_SYSTEM},
             {"role": "user", "content": listing}],
            profile="memory_consolidate")
    except (llm.BudgetExceeded, llm.LLMError):
        return []
    parsed = llm.parse_llm_json(reply) or {}
    valid = {i for i, _ in items}

    def _id(x):  # the model may emit ids as ints OR strings ("12") — accept both
        try:
            return int(x)
        except (TypeError, ValueError):
            return None
    groups = []
    for g in (parsed.get("groups") or []):
        if not isinstance(g, dict):
            continue
        keep = _id(g.get("keep"))
        if keep not in valid:
            continue
        drops = [d for d in (_id(x) for x in (g.get("drop") or []))
                 if d in valid and d != keep]
        if drops:
            groups.append((keep, drops))
    return groups


def consolidate(conn, cfg, max_items=120):
    """De-duplicate Cara's memory: an LLM groups genuine duplicate items and we KEEP the
    richest. Boss facts: the rest are marked 'merged' (reversible). Her life-flavour facts
    (cara_life, no status column): the redundant copies are deleted, keeping one of each
    distinct beat — this is what folds the over-grown 'tea' duplicates. Pending memory
    candidates are also tidied: duplicates folded ('merged') and any that CONTRADICT a fact
    he already confirmed dropped ('superseded') — a sensed guess never overrides confirmed
    truth (the кофе-vs-confirmed-чай case). Returns total folded."""
    # Group in SMALL batches — the fast model reliably spots duplicates in ~40 items but
    # misses them in a 120-item wall. (Re-running across weeks folds anything a batch split.)
    def _batches(seq, size=40):
        for i in range(0, len(seq), size):
            yield seq[i:i + size]
    merged = 0
    # 1) boss profile items -> demote duplicates to 'merged' (reversible)
    items, seen = [], set()
    for status in ("confirmed", "inferred"):
        for row in store.boss_items(conn, status, limit=max_items):
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            val = (row["value"] or "").strip()
            if val:
                items.append((row["id"], val))
    for batch in _batches(items):
        for _keep, drops in _merge_groups(conn, cfg, batch):
            for d in drops:
                if store.boss_set_status(conn, d, "merged"):
                    merged += 1
    # 2) Cara's life flavour -> delete redundant duplicates, keep the richest of each
    life = [(r["id"], r["text"]) for r in store.life_all(conn) if (r["text"] or "").strip()]
    for batch in _batches(life):
        for _keep, drops in _merge_groups(conn, cfg, batch):
            for d in drops:
                if store.life_delete(conn, d):
                    merged += 1
    # 3) Pending candidates: an LLM judges each against the CONFIRMED facts — dropping ones
    #    that CONTRADICT a confirmed fact ('superseded') and ones that merely DUPLICATE another
    #    ('merged'). Semantic, not token-overlap: catches "пьёт кофе, а не чай" vs a verbose
    #    confirmed "пьёт чай…" and folds the "Иван Доронин ×N" restatements.
    merged += _tidy_candidates(conn, cfg, max_items)
    # 4) Stored INFERRED facts that CONTRADICT a confirmed fact -> demote to 'merged' (reversible).
    #    An auto-learned guess must not coexist with confirmed truth (inferred "пьёт кофе" next to
    #    confirmed "пьёт чай"); the confirmed fact wins.
    merged += _tidy_inferred(conn, cfg, max_items)
    return merged


_CANDIDATE_HYGIENE_SYSTEM = (
    "You tidy a queue of UNCONFIRMED memory candidates about the boss against facts he has "
    "ALREADY CONFIRMED. You are given CONFIRMED facts (text only) and CANDIDATES as "
    "'<id>: <text>'. Return STRICT JSON only: "
    '{"contradicts": [<id>, ...], "duplicates": [<id>, ...]}.\n'
    "- contradicts: candidate ids stating something that CONTRADICTS a confirmed fact (e.g. "
    "confirmed 'пьёт чай' vs candidate 'пьёт кофе, а не чай') — a wrong/outdated guess.\n"
    "- duplicates: candidate ids that merely RESTATE another candidate OR a confirmed fact "
    "(same underlying fact/paraphrase, nothing new). Of a duplicate set keep ONE — list only "
    "the redundant ids.\n"
    "Leave OUT any candidate that is a genuinely new, non-conflicting fact. Reference only the "
    "given candidate ids; never invent. Empty arrays if nothing to drop."
)


def _tidy_candidates(conn, cfg, max_items=120):
    """LLM hygiene over pending candidates: drop contradictions-with-confirmed ('superseded')
    and duplicates ('merged'). Returns count folded. Best-effort: 0 on <2 candidates or failure."""
    import llm
    pending = [c for c in store.candidates_pending(conn, limit=max_items)
               if (c["proposed_text"] or "").strip()]
    if len(pending) < 2:
        return 0
    confirmed = [r["value"] for r in store.boss_items(conn, "confirmed", limit=80)
                 if (r["value"] or "").strip()]
    conf_listing = "\n".join(f"- {v}" for v in confirmed) or "(none)"
    cand_listing = "\n".join(f"{c['id']}: {c['proposed_text']}" for c in pending)
    try:
        reply = llm.chat_profile(
            cfg, conn, "memory_curator",
            [{"role": "system", "content": _CANDIDATE_HYGIENE_SYSTEM},
             {"role": "user",
              "content": f"CONFIRMED facts:\n{conf_listing}\n\nCANDIDATES:\n{cand_listing}"}],
            profile="memory_consolidate")
    except (llm.BudgetExceeded, llm.LLMError):
        return 0
    parsed = llm.parse_llm_json(reply) or {}
    valid = {c["id"] for c in pending}

    def _ids(key):
        out = []
        for x in (parsed.get(key) or []):
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if i in valid:
                out.append(i)
        return out

    folded, contradicts = 0, set(_ids("contradicts"))
    for i in contradicts:
        store.candidate_set_status(conn, i, "superseded")
        folded += 1
    for i in _ids("duplicates"):
        if i not in contradicts:
            store.candidate_set_status(conn, i, "merged")
            folded += 1
    return folded


_INFERRED_HYGIENE_SYSTEM = (
    "You reconcile Cara's AUTO-LEARNED (unconfirmed) facts about the boss against facts he has "
    "CONFIRMED. You are given CONFIRMED facts (text only) and INFERRED items as '<id>: <text>'. "
    'Return STRICT JSON only: {"contradicts": [<id>, ...]}. List the INFERRED ids that state '
    "something CONTRADICTING a confirmed fact (e.g. confirmed 'пьёт чай' vs inferred 'пьёт кофе') "
    "— the confirmed fact wins, so the inferred one is wrong/outdated. Leave OUT anything that is "
    "consistent with, or merely additional to, the confirmed facts. Reference only the given "
    "inferred ids; never invent. Empty array if none contradict."
)


def _tidy_inferred(conn, cfg, max_items=120):
    """Demote stored INFERRED facts that CONTRADICT a confirmed fact to 'merged' (reversible).
    Conservative: only direct contradictions with a CONFIRMED fact (confirmed wins). 0 on
    nothing-to-do or failure."""
    import llm
    inferred = [(r["id"], r["value"]) for r in store.boss_items(conn, "inferred", limit=max_items)
                if (r["value"] or "").strip()]
    confirmed = [r["value"] for r in store.boss_items(conn, "confirmed", limit=80)
                 if (r["value"] or "").strip()]
    if not inferred or not confirmed:
        return 0
    listing = "\n".join(f"{i}: {v}" for i, v in inferred)
    conf = "\n".join(f"- {v}" for v in confirmed)
    try:
        reply = llm.chat_profile(
            cfg, conn, "memory_curator",
            [{"role": "system", "content": _INFERRED_HYGIENE_SYSTEM},
             {"role": "user", "content": f"CONFIRMED facts:\n{conf}\n\nINFERRED:\n{listing}"}],
            profile="memory_consolidate")
    except (llm.BudgetExceeded, llm.LLMError):
        return 0
    parsed = llm.parse_llm_json(reply) or {}
    valid = {i for i, _ in inferred}
    folded = 0
    for x in (parsed.get("contradicts") or []):
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i in valid and store.boss_set_status(conn, i, "merged"):
            folded += 1
    return folded
