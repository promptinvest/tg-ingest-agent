#!/usr/bin/env python3
"""Cara's self-knowledge: grounded answers to "who are you / what can you do /
what are your limits", from seeded self_facts + the skill manifest + live
config — never improvised mythology. Deterministic, no LLM.
"""
import skill_manifest
import store

# Stable, deterministic self facts (spec §6). Seeded idempotently at startup.
SEED_FACTS = {
    "name": "Cara",
    "role": "private conversational aide for the boss",
    "telegram": "@cara_assist_bot",
    "languages": "Russian and English; replies in the boss's language",
    "hosting": "self-hosted on the Pilot-VPS as one stdlib-only Python systemd service",
    "inference": "DigitalOcean Gradient serverless inference; local whisper.cpp for voice",
    "storage": "SQLite (WAL) + local media; optional DO Spaces for durability",
    "router": "closed-world router with no generic chat action",
    "free_text_rule": "the only free-form answers are grounded KB Q&A from the boss's own notes",
    "safety_rule": "suggest, then confirm before any state change",
}


def seed(conn):
    for key, value in SEED_FACTS.items():
        store.self_fact_set(conn, key, value, scope="core", source="seed")


def answer_self_query(conn, lang, cfg=None):
    facts = {row["key"]: row["value"] for row in store.self_facts(conn)}
    caps = " · ".join(skill_manifest.capability_titles(lang))
    dormant = []
    if cfg is not None:
        if not cfg.gcal_calendar_id:
            dormant.append("Google Calendar sync" if lang != "ru" else "синхронизация с Google Calendar")
        if getattr(cfg, "storage_backend", "local") != "spaces":
            dormant.append("DO Spaces upload" if lang != "ru" else "выгрузка в DO Spaces")
    if lang == "ru":
        lines = [
            f"Босс, я {facts.get('name', 'Cara')} — {('ваша личная помощница в Telegram')} 🦊",
            "",
            "Что я умею: " + caps,
            "",
            "Как я устроена: один процесс на Pilot-VPS, SQLite, длинный поллинг, "
            "без обычного чат-бота. Свободный текст — только ответы по вашим заметкам.",
            "Правило безопасности: предлагаю и жду подтверждения перед любыми изменениями.",
        ]
        if dormant:
            lines.append("Пока не подключено: " + ", ".join(dormant) + ".")
        lines.append("Моя память сейчас: ваши заметки, подтверждённые категории, напоминания, "
                     "правки и рабочие правила, что вы мне дали.")
        return "\n".join(lines)
    lines = [
        f"Boss, I'm {facts.get('name', 'Cara')} — your private Telegram aide 🦊",
        "",
        "What I can do: " + caps,
        "",
        "How I work: one process on the Pilot-VPS, SQLite, long polling, no generic chat. "
        "The only free-form answers I give are grounded in your own saved notes.",
        "Safety rule: I suggest, then confirm before changing anything.",
    ]
    if dormant:
        lines.append("Not yet connected: " + ", ".join(dormant) + ".")
    lines.append("My memory right now: your notes, confirmed categories, reminders, your "
                 "corrections, and the working rules you gave me.")
    return "\n".join(lines)
