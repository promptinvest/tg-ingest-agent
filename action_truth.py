#!/usr/bin/env python3
"""Action-truth guard (spec §0.1.3): persona must never claim a final action
('saved', 'scheduled', 'remembered', …) before the deterministic code path
that performs it has completed. Every rendered template is checked in
production, and the catalogue-wide test requires every final-action template
to declare its lifecycle state here.
"""
import re

FINAL_VERBS = {
    "saved", "filed", "scheduled", "remembered", "deleted", "purged",
    "synced", "fetched", "transcribed", "exported", "sent",
    # Russian finals
    "сохранила", "записала", "поставила", "запомнила", "удалила", "очистила",
    "выгрузила", "отправила",
}

# A free-form conversation turn has no attachment channel. These shapes are
# therefore proof that the model is pretending an artifact exists. The real
# review/export handlers upload through sendDocument and never describe the file
# in an ordinary sendMessage.
_BARE_ARTIFACT_LINK_RE = re.compile(
    r"\[[^\]\n]+\.(?:md|txt|pdf|csv|json|docx?|xlsx?|zip|ics)\](?!\s*\()",
    re.IGNORECASE,
)
_ARTIFACT_CLAIM_RE = re.compile(
    r"(?:\bвот\s+(?:твой\s+)?файл\b|\bфайл\s+готов\b|\bприкрепила\s+файл\b|"
    r"\bотправила\s+(?:тебе\s+)?файл\b|\bhere(?:'s|\s+is)\s+(?:your\s+|the\s+)?file\b|"
    r"\bthe\s+file\s+is\s+ready\b|\bi\s+(?:sent|attached)\s+(?:you\s+)?the\s+file\b)",
    re.IGNORECASE,
)

# A free-form `converse` turn cannot mutate state. These phrases are therefore
# unsafe when the model presents them as the outcome of the current exchange:
# only deterministic skill handlers may close/move/save/schedule something or
# claim that a queue is now clean. This is deliberately narrower than
# FINAL_VERBS (which validates deterministic templates) so ordinary discussion
# of a past action is less likely to be blocked.
_ACTION_CLAIM_RE = re.compile(
    r"(?:^|[.!?]\s*)(?:готово[,!:.\s-]*)?(?:я\s+)?"
    r"(?:#\d+\s+|(?:напоминание|заметк[ау]|запись|задач[ау]|файл)\s+)?"
    r"(?:закрыла|закрыто|перенесла|передвинула|поставила|сохранила|записала|"
    r"удалила|очистила|подтвердила|переименовала|отменила)\b|"
    r"\b(?:всё|очередь|список)\s+(?:чисто|разобран[аоы]?|пуст[ао])\b|"
    r"\b(?:возьму|беру)\s+(?:это\s+)?в\s+работу\b|"
    r"(?:^|[.!?]\s*)(?:done[,!:.\s-]*)?(?:i\s+)?"
    r"(?:closed|moved|scheduled|saved|filed|deleted|cleared|confirmed|renamed)\b|"
    r"\b(?:queue|list|everything)\s+is\s+(?:clear|empty|sorted)\b|"
    r"\bi(?:'ll|\s+will)\s+(?:take|put)\s+(?:this\s+)?(?:into|on)\s+(?:the\s+)?work\b",
    re.IGNORECASE,
)

# Which final verbs are legitimate in each lifecycle state.
STATE_ALLOWED_FINAL_VERBS = {
    "received": set(),
    "stored_original": {"saved", "сохранила"},
    "suggested": set(),
    "waiting_confirmation": set(),
    "confirmed": FINAL_VERBS,
    "failed_retryable": set(),
    "failed_final": {"saved", "сохранила"},  # may say the original was saved
    "done": FINAL_VERBS,                      # read-only/completed ops
}

# A template key is the deterministic action boundary: call sites render it
# only after that named operation. New final-action copy must be reviewed and
# mapped here or both runtime rendering and the catalogue test fail closed.
TEMPLATE_STATES = {
    "counts": "stored_original",
    "already_confirmed": "confirmed",
    "stored_retry": "stored_original",
    "reminder_set": "done",
    "boss_remembered": "done",
    "memory_candidate_kept": "done",
    "memory_empty": "done",
    "remember_saved": "done",
    "auto_confirmed": "done",
    "stats_empty": "done",
    "capabilities": "done",
    "purge_done": "done",
    "calendar_added": "done",
    "deleted": "done",
    "deleted_multi": "done",
    "correction_learned": "done",
    "correction_needs_code": "done",
    "journal_saved": "done",
    "problem_logged": "done",
    "reminder_no_prev": "done",
    "files_empty": "done",
    # Rendered only AFTER apply_category_confirm committed the note.
    "capture_reminder_slot_busy": "confirmed",
    # Rendered only AFTER _media_store_entries committed every entry (the media
    # confirm boundary) — the card itself carries no final verbs.
    "media_saved": "done",
    # "I'd already saved the older version" — the claim is about the note that IS
    # saved; the EDIT itself is explicitly not applied yet, which is what the
    # question in the same sentence asks about.
    "note_edit_offer": "confirmed",
}


def verbs_in(text):
    low = str(text or "").lower()
    return {v for v in FINAL_VERBS
            if re.search(rf"(?<!\w){re.escape(v)}(?!\w)", low)}


def assert_template_allowed(template_key, state, text):
    """Raise ValueError if `text` uses a final verb not allowed in `state`."""
    allowed = STATE_ALLOWED_FINAL_VERBS.get(state, set())
    forbidden = verbs_in(text) - allowed
    if forbidden:
        raise ValueError(
            f"Template {template_key!r} uses final verbs {sorted(forbidden)} in state {state!r}")


def assert_template_key_allowed(template_key, text):
    """Production guard for one rendered template."""
    found = verbs_in(text)
    if not found:
        return
    state = TEMPLATE_STATES.get(template_key)
    if not state:
        raise ValueError(
            f"Template {template_key!r} uses final verbs but declares no lifecycle state")
    assert_template_allowed(template_key, state, text)


def assert_catalogue(catalogue):
    """Validate every language and variant, not a hand-selected sample."""
    for key, entry in catalogue.items():
        for localized in entry.values():
            variants = localized if isinstance(localized, (list, tuple)) else [localized]
            for text in variants:
                assert_template_key_allowed(key, text)


def freeform_claims_artifact(text):
    """True when ordinary chat text falsely presents a file as attached/created."""
    value = str(text or "")
    return bool(_BARE_ARTIFACT_LINK_RE.search(value) or _ARTIFACT_CLAIM_RE.search(value))


def freeform_claims_action(text):
    """True when ordinary chat claims a state change it cannot have performed."""
    return bool(_ACTION_CLAIM_RE.search(str(text or "")))
