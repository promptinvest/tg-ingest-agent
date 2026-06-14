#!/usr/bin/env python3
"""Persona context assembly: a compact, budgeted snippet of Cara's identity +
the boss's confirmed preferences (+ relationship later), for prompts that
benefit from personalization. Kept short on purpose (spec §15.1).
"""
import boss_model

# Fix 1 (spec §0.1.1): the persona is a STYLE layer, applied strictly below the
# operational rules. This documents and pins the required assembly order; the
# router never receives full persona prose (only a one-line identity hint), and
# the persona may never change confirmation/risk/state decisions.
PROMPT_LAYER_ORDER = [
    "system_security",
    "tool_permissions",
    "router_schema",
    "confirmation_rules",
    "memory_storage_rules",
    "budget_rules",
    "human_like_persona",
    "runtime_context",
    "user_message",
]


def persona_below_rules():
    """True iff the persona layer sits below every operational-rule layer and
    above only runtime context + the user message (invariant for tests)."""
    i = PROMPT_LAYER_ORDER.index("human_like_persona")
    rules = {"system_security", "tool_permissions", "router_schema",
             "confirmation_rules", "memory_storage_rules", "budget_rules"}
    above = set(PROMPT_LAYER_ORDER[:i])
    below = set(PROMPT_LAYER_ORDER[i + 1:])
    return rules.issubset(above) and below == {"runtime_context", "user_message"}


def boss_preference_hint(conn, max_chars=600):
    """Confirmed boss preferences as a short instruction block, or '' when
    none. Safe to prepend to grounded-answer / summary prompts so Cara honors
    standing preferences (e.g. 'short answers')."""
    prefs = boss_model.confirmed_context(conn, max_chars=max_chars)
    if not prefs:
        return ""
    return ("The boss's standing preferences (honor them in tone/format, not as "
            "factual content):\n" + "\n".join(prefs))
