#!/usr/bin/env python3
"""Persona context assembly: a compact, budgeted snippet of Cara's identity +
the boss's confirmed preferences (+ relationship later), for prompts that
benefit from personalization. Kept short on purpose (spec §15.1).
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
