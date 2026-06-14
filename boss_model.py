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
    r"(здоров|болезн|диагноз|аллерг|лекарств|финанс|деньг|зарплат|кредит|пароль|"
    r"карт|адрес|телефон|паспорт|религ|политик|"
    r"счёт|реквизит|"
    r"health|illness|medical|allerg|diagnos|finance|salary|credit|password|"
    r"address|phone|passport|religion|politics|legal|iban|\bbank\b|account)", re.I)

# Sensitivity is a safety boundary, so it must not depend on a keyword denylist
# alone (those fail open). The item's KIND gives a deterministic floor that the
# regex can only raise, never lower.
SENS_ORDER = {"normal": 0, "private": 1, "sensitive": 2, "secret": 3}
KIND_FLOOR = {"personal_fact": "sensitive", "identity": "private"}


def classify_sensitivity(text):
    return "sensitive" if _SENSITIVE_HINTS.search(text or "") else "normal"


# Fix 7: resolve how to address the boss from stored preferences, with a safe
# fallback — never a hard-coded name, never empty.
DEFAULT_ADDRESS = {"ru": "босс", "en": "boss"}


def get_address(conn, lang, allow_name=True):
    if allow_name:
        name = (store.pref_get(conn, f"owner_name_{lang}")
                or store.pref_get(conn, "owner_name"))
        if name:
            return name
    return store.pref_get(conn, f"preferred_address_{lang}") or DEFAULT_ADDRESS.get(lang, "boss")


def effective_sensitivity(kind, text):
    """Max of the keyword guess and the kind's floor — so a personal_fact is
    never stored as 'normal' even if no keyword matched."""
    guess = classify_sensitivity(text)
    floor = KIND_FLOOR.get(kind, "normal")
    return guess if SENS_ORDER[guess] >= SENS_ORDER[floor] else floor


def remember_explicit(conn, value, kind="workflow"):
    """Operator said 'remember X about me' -> a confirmed profile item."""
    value = re.sub(r"\s+", " ", str(value or "")).strip()[:300]
    if not value:
        return None
    return store.boss_add(conn, kind, value, status="confirmed", confidence=1.0,
                          sensitivity=effective_sensitivity(kind, value), source_table="explicit")


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


def _name_line(conn, lang):
    """A 'your name is …' line for the profile view, drawn from the stored name
    (which 'как меня зовут' otherwise couldn't surface). '' when unknown."""
    name_ru = store.pref_get(conn, "owner_name_ru")
    name_en = store.pref_get(conn, "owner_name_en")
    name = (store.pref_get(conn, f"owner_name_{lang}")
            or store.pref_get(conn, "owner_name"))
    if not (name or name_ru or name_en):
        return ""
    both = " / ".join(p for p in dict.fromkeys([name_ru, name_en]) if p) or name
    return (f"Вас зовут {both}." if lang == "ru" else f"Your name is {both}.")


def render_profile(conn, lang, include_inferred=True):
    confirmed = store.boss_items(conn, "confirmed")
    inferred = store.boss_items(conn, "inferred", sensitivities=("normal", "private")) \
        if include_inferred else []
    name_line = _name_line(conn, lang)
    name_block = [name_line, ""] if name_line else []
    if lang == "ru":
        lines = [T(lang, "boss_profile_header"), ""] + name_block + [T(lang, "boss_confirmed")]
        lines += [f"  #{r['id']} {r['value']}" for r in confirmed] or ["  — пока нет"]
        if include_inferred:
            lines += ["", T(lang, "boss_inferred")]
            lines += [f"  #{r['id']} {r['value']}" for r in inferred] or ["  — пока нет"]
        lines += ["", T(lang, "boss_edit_hint")]
        return "\n".join(lines)
    lines = [T(lang, "boss_profile_header"), ""] + name_block + [T(lang, "boss_confirmed")]
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


# Behavioral guidance: rules the boss set or corrected (how Cara should act).
# Both confirmed AND inferred — a correction learned from conversation lands as
# 'inferred' and must reach the prompt so Cara honors it next turn.
GUIDANCE_KINDS = ("tone", "workflow", "avoidance", "quality_bar")


def standing_guidance(conn, max_items=8, max_chars=600):
    out, used, seen = [], 0, set()
    for status in ("confirmed", "inferred"):
        for row in store.boss_items(conn, status, sensitivities=("normal",), limit=20):
            if row["kind"] not in GUIDANCE_KINDS:
                continue
            value = (row["value"] or "").strip()
            if not value or value in seen:
                continue
            line = f"- {value}"
            if used + len(line) > max_chars:
                return out
            out.append(line)
            used += len(line)
            seen.add(value)
    return out
