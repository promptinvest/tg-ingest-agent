#!/usr/bin/env python3
"""Persona context assembly: a compact, budgeted snippet of the boss's confirmed
preferences, for prompts that benefit from personalization. Kept short on purpose.

Persona-below-rules is enforced STRUCTURALLY, not by an abstract ordering table:
the security/no-fabrication/no-fake-action/no-invented-specifics rules are written
at the TOP of the system prompts that actually run (`converse.CHARACTER`, the
router/ingest system prompts), above the persona voice and life; the router is
closed-world; and transactional/system replies are deterministic `texts.py`
templates the model never free-writes. So charm can't override safety because the
rules physically precede the persona in the one prompt that reaches the model.
"""
import boss_model


def boss_preference_hint(conn, max_chars=600):
    """Confirmed boss preferences as a short instruction block, or '' when
    none. Safe to prepend to grounded-answer / summary prompts so Cara honors
    standing preferences (e.g. 'short answers')."""
    prefs = boss_model.confirmed_context(conn, max_chars=max_chars)
    if not prefs:
        return ""
    return ("The boss's standing preferences (honor them in tone/format, not as "
            "factual content):\n" + "\n".join(prefs))
