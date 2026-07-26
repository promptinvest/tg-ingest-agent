#!/usr/bin/env python3
"""Structured model of the boss: confirmed facts vs inferred patterns, with
edit/forget/confirm. Explicit "remember this about me" lands as confirmed;
the curator (Phase C) proposes inferred candidates. Sensitive/secret items
are never surfaced casually.
"""
import re
from datetime import datetime

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


def _explicit_item_id(query):
    """An item id only when the boss actually POINTED at one: '#12' anywhere, or the
    whole query being a bare number. A digit inside an ordinary phrase («забудь, что
    я встаю в 6 утра») is part of the fact, not an id — grabbing it deprecated an
    unrelated item #6."""
    q = str(query or "")
    m = re.search(r"#(\d+)", q) or re.fullmatch(r"\s*(\d+)\s*", q)
    return int(m.group(1)) if m else None


def forget(conn, query):
    """Deprecate a profile item by explicit #id/bare number or substring; returns
    the value or None."""
    iid = _explicit_item_id(query)
    item = store.boss_get(conn, iid) if iid is not None else None
    if item is None:
        item = store.boss_find(conn, query)
    if item is None:
        return None
    store.boss_set_status(conn, item["id"], "deprecated")
    return item["value"]


def confirm(conn, query):
    iid = _explicit_item_id(query)
    item = store.boss_get(conn, iid) if iid is not None else store.boss_find(conn, query)
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
    return (f"Тебя зовут {both}." if lang == "ru" else f"Your name is {both}.")


def _norm(s):
    return re.sub(r"[^\w]+", " ", str(s or "").casefold(), flags=re.UNICODE).strip()


def _similar(a, b, thresh=0.6):
    """Rough near-duplicate check by shared-word ratio — catches reworded repeats
    ('надёжный, держит слово, люди доверяют' vs '…и люди ему доверяют')."""
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta or not tb:
        return False
    return len(ta & tb) / max(len(ta), len(tb)) >= thresh


def _dedup(values, thresh=0.7):
    out = []
    for v in values:
        if v and not any(_similar(v, kept, thresh) for kept in out):
            out.append(v)
    return out


def is_duplicate(conn, text, thresh=0.7):
    """True if a near-identical fact is already in the profile (so the curator
    doesn't pile up reworded copies)."""
    existing = [r["value"] for r in store.boss_items(conn, "confirmed", limit=60)] \
        + [r["value"] for r in store.boss_items(conn, "inferred", limit=60)]
    return any(_similar(text, e, thresh) for e in existing)


# Provenance: when asked "откуда ты это знаешь?", Cara cites how she learned it —
# in character (no DB/table jargon), drawn from the stored source.
_STOP = {"и", "в", "на", "что", "ты", "я", "он", "она", "это", "эту", "этот", "меня",
         "мне", "о", "про", "у", "тебя", "как", "знаешь", "помнишь", "откуда", "почему",
         "the", "a", "an", "is", "to", "of", "that", "you", "i", "it", "do", "does",
         "know", "why", "remember", "about", "where", "learn", "this", "from"}


def _provenance(row, lang):
    value = row["value"]
    src = row["source_table"] or ""
    when = (row["first_seen_at"] or row["created_at"] or "")[:10]
    date = ""
    if when:
        try:
            d = datetime.fromisoformat(when)
            date = (f" (ещё {d:%d.%m})" if lang == "ru" else f" (since {d:%d.%m})")
        except ValueError:
            pass
    if lang == "ru":
        lead = {"explicit": "Ты сам мне это сказал",
                "correction": "Ты меня на этом поправил",
                "memory_candidate": "Ты подтвердил это, когда я предложила запомнить",
                }.get(src, "Заметила из наших разговоров")
        return f"{lead}: «{value}»{date}. Если что-то не так — поправь."
    lead = {"explicit": "You told me yourself",
            "correction": "You corrected me on it",
            "memory_candidate": "You confirmed it when I offered to remember",
            }.get(src, "I picked it up from our chats")
    return f"{lead}: «{value}»{date}. Correct me if it's off."


def explain(conn, lang, query):
    """In-character provenance for the stored fact most related to the question,
    or None when nothing clearly matches (caller falls back to warm chat)."""
    qt = {t for t in _norm(query).split() if t not in _STOP and len(t) > 2}
    if not qt:
        return None
    best, score = None, 0
    for status in ("confirmed", "inferred"):
        for row in store.boss_items(conn, status, limit=60):
            vt = {t for t in _norm(row["value"]).split() if t not in _STOP and len(t) > 2}
            overlap = len(qt & vt)
            if overlap > score:
                best, score = row, overlap
    return _provenance(best, lang) if best and score >= 1 else None


def conflicts_with_confirmed(conn, text, low=0.4):
    """True if a CONFIRMED fact is on the same topic but says something different
    — likely an update/contradiction, so it should be confirmed, not auto-stored."""
    for row in store.boss_items(conn, "confirmed", limit=60):
        if _similar(text, row["value"], low) and not _similar(text, row["value"], 0.75):
            return True
    return False


# A grouped "operating model" of the boss for prompt assembly — the descriptive
# context (who he is, projects, routines, how he files), kept separate from the
# behavioral rules in standing_guidance (tone/workflow/avoidance/quality_bar).
_OPERATING_GROUPS = (
    ("projects", ("проекты", "projects"), ("project",)),
    ("about", ("о нём", "about him"), ("personal_fact", "identity", "relationship_note")),
    ("routines", ("распорядок", "routines"), ("reminder_preference",)),
    ("filing", ("как он раскладывает", "how he files"), ("category_preference",)),
)

# The union of the group kinds — the SQL filter, so the LIMIT is spent on rows
# this view can actually show instead of on guidance/other kinds it discards.
_OPERATING_KINDS = tuple(dict.fromkeys(k for _key, _labels, kinds in _OPERATING_GROUPS
                                       for k in kinds))


def operating_model(conn, lang):
    """Grouped, deduped, non-sensitive facts about the boss for the prompt:
    list of (label, [values]). Confirmed + inferred."""
    rows = []
    for status in ("confirmed", "inferred"):
        rows += store.boss_items(conn, status, sensitivities=("normal",), limit=80,
                                 kinds=_OPERATING_KINDS)
    out = []
    for _key, (ru, en), kinds in _OPERATING_GROUPS:
        vals = _dedup([r["value"] for r in rows if r["kind"] in kinds])
        if vals:
            out.append((ru if lang == "ru" else en, vals[:6]))
    return out


def profile_facts(conn, lang):
    """Deduped (name, confirmed, inferred) for a warm spoken summary. Inferred
    items that merely restate a confirmed one are dropped."""
    name_ru = store.pref_get(conn, "owner_name_ru")
    name_en = store.pref_get(conn, "owner_name_en")
    name = " / ".join(dict.fromkeys(n for n in (name_ru, name_en) if n)) \
        or store.pref_get(conn, "owner_name") or None
    confirmed = _dedup([r["value"] for r in store.boss_items(conn, "confirmed", limit=40)])
    inferred = [v for v in _dedup(
        [r["value"] for r in store.boss_items(conn, "inferred",
                                              sensitivities=("normal", "private"), limit=40)])
        if not any(_similar(v, c, 0.6) for c in confirmed)]
    return name, confirmed, inferred


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
    """The standing behavioral rules for the prompt.

    The kind filter lives in SQL, before the LIMIT: filtering the top-20 rows in
    Python meant a profile with 20+ higher-confidence facts of OTHER kinds
    returned no guidance at all — Cara silently stopped honoring every standing
    correction he had taught her, with nothing in the logs to say so.
    """
    out, used, seen = [], 0, set()
    for status in ("confirmed", "inferred"):
        for row in store.boss_items(conn, status, sensitivities=("normal",), limit=20,
                                    kinds=GUIDANCE_KINDS):
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
