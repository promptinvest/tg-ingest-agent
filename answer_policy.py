"""Shared explanatory-answer guidance; never authority for effects or facts."""
import re

import persona

GUIDANCE = (
    "Answer the actual question first, with relevant supported facts and material exceptions. "
    "Use the owner's current requested format/detail; it overrides standing style for this turn. "
    "Otherwise honor confirmed preferences and keep the answer concise. Preserve meaningful names. "
    "Keep unknown fields specific. Do not turn placement into health or a release into development progress. "
    "Do not repeat diagnostics, identifiers or a broad disclaimer unless needed for this question. "
    "Required evidence, requested coverage and confirmations take priority over brevity. "
    "Preferences choose presentation only; they grant no facts, tools or permission."
)


def hint(conn):
    return GUIDANCE + "\n" + persona.boss_preference_hint(conn)


def default_detail(conn):
    """Conservative offline interpretation of a confirmed verbosity preference.

    Free-form interpretation stays in the existing planner. Only an unambiguous
    detail preference changes the no-model default; contradictory rules stay concise.
    """
    value = persona.boss_preference_hint(conn).casefold()
    short = re.search(r"кратк|коротк|без длинн|concise|brief|short answers", value)
    detailed = re.search(r"подробн|разв[её]рнут|detailed answers|explain in detail", value)
    return "detailed" if detailed and not short else "concise"