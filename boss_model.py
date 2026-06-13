#!/usr/bin/env python3
"""Structured model of the boss: confirmed facts vs inferred patterns, with
edit/forget/confirm. Explicit "remember this about me" lands as confirmed;
the curator (Phase C) proposes inferred candidates. Sensitive/secret items
are never surfaced casually.
"""
import re

import store
from texts import T

# kinds the operator can teach explicitly (spec §8)
KINDS = ("identity", "language", "tone", "workflow", "quality_bar", "project",
         "category_preference", "reminder_preference", "personal_fact", "avoidance",
         "relationship_note")

_SENSITIVE_HINTS = re.compile(
    r"(здоров|болезн|диагноз|финанс|деньг|зарплат|кредит|пароль|карт|адрес|"
    r"health|finance|salary|password|credit|address|medical|legal)", re.I)


def classify_sensitivity(text):
    return "sensitive" if _SENSITIVE_HINTS.search(text or "") else "normal"


def remember_explicit(conn, value, kind="workflow"):
    """Operator said 'remember X about me' -> a confirmed profile item."""
    value = re.sub(r"\s+", " ", str(value or "")).strip()[:300]
    if not value:
        return None
    return store.boss_add(conn, kind, value, status="confirmed", confidence=1.0,
                          sensitivity=classify_sensitivity(value), source_table="explicit")


def forget(conn, query):
    """Deprecate a profile item by #id or substring; returns the value or None."""
    item = None
    m = re.search(r"#?(\d+)", str(query or ""))
    if m:
        item = store.boss_get(conn, int(m.group(1)))
    if item is None:
        item = store.boss_find(conn, query)
    if item is None:
        return None
    store.boss_set_status(conn, item["id"], "deprecated")
    return item["value"]


def confirm(conn, query):
    m = re.search(r"#?(\d+)", str(query or ""))
    item = store.boss_get(conn, int(m.group(1))) if m else store.boss_find(conn, query)
    if item is None:
        return None
    store.boss_set_status(conn, item["id"], "confirmed")
    return item["value"]


def render_profile(conn, lang, include_inferred=True):
    confirmed = store.boss_items(conn, "confirmed")
    inferred = store.boss_items(conn, "inferred", sensitivities=("normal", "private")) \
        if include_inferred else []
    if lang == "ru":
        lines = [T(lang, "boss_profile_header"), "", T(lang, "boss_confirmed")]
        lines += [f"  #{r['id']} {r['value']}" for r in confirmed] or ["  — пока нет"]
        if include_inferred:
            lines += ["", T(lang, "boss_inferred")]
            lines += [f"  #{r['id']} {r['value']}" for r in inferred] or ["  — пока нет"]
        lines += ["", T(lang, "boss_edit_hint")]
        return "\n".join(lines)
    lines = [T(lang, "boss_profile_header"), "", T(lang, "boss_confirmed")]
    lines += [f"  #{r['id']} {r['value']}" for r in confirmed] or ["  — none yet"]
    if include_inferred:
        lines += ["", T(lang, "boss_inferred")]
        lines += [f"  #{r['id']} {r['value']}" for r in inferred] or ["  — none yet"]
    lines += ["", T(lang, "boss_edit_hint")]
    return "\n".join(lines)


def confirmed_context(conn, max_items=8, max_chars=600):
    """Compact confirmed-preferences snippet for prompt personalization
    (skips sensitive). Used by persona.py."""
    out = []
    used = 0
    for row in store.boss_items(conn, "confirmed", sensitivities=("normal",), limit=max_items):
        line = f"- {row['value']}"
        if used + len(line) > max_chars:
            break
        out.append(line)
        used += len(line)
    return out
