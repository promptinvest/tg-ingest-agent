#!/usr/bin/env python3
"""Action-truth guard (spec §0.1.3): persona must never claim a final action
('saved', 'scheduled', 'remembered', …) before the deterministic code path
that performs it has completed. This is a TEST-ONLY guard — templates carry
`final_verbs` + `allowed_states` metadata, and a test asserts no template uses
a final verb in a state where that action hasn't happened.
"""

FINAL_VERBS = {
    "saved", "filed", "scheduled", "remembered", "deleted", "purged",
    "synced", "fetched", "transcribed", "exported", "sent",
    # Russian finals
    "сохранила", "записала", "поставила", "запомнила", "удалила", "очистила",
    "выгрузила", "отправила",
}

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


def verbs_in(text):
    low = str(text or "").lower()
    return {v for v in FINAL_VERBS if v in low}


def assert_template_allowed(template_key, state, text):
    """Raise ValueError if `text` uses a final verb not allowed in `state`."""
    allowed = STATE_ALLOWED_FINAL_VERBS.get(state, set())
    forbidden = verbs_in(text) - allowed
    if forbidden:
        raise ValueError(
            f"Template {template_key!r} uses final verbs {sorted(forbidden)} in state {state!r}")
