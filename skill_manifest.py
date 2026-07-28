#!/usr/bin/env python3
"""Canonical permission registry for Cara's skills/actions.

Single source of truth for what each router action may do. Used by the
dispatcher (gate state changes), the future proactive heartbeat (only
read-only/suggestion actions), the capabilities answer (generated from here,
never stale prose), and tests (no action ships without a declared policy).

risk: read_only | read_only_suggestion | draft_write | state_write |
      network_read | external_write | destructive | meta
requires_confirmation: False | True | "typed_phrase"
"""

_DEFAULT = {
    "risk": "read_only", "uses_llm": False, "writes_state": False,
    "destructive": False, "requires_confirmation": False,
    "allowed_proactive": False, "persona_context": False,
    "title": {"en": "", "ru": ""},
}

SKILLS = {
    # -- ingest / knowledge
    "ingest": {"risk": "state_write", "uses_llm": True, "writes_state": True,
               "requires_confirmation": True, "persona_context": True,
               "title": {"en": "Save & categorize", "ru": "Сохранение и разбор"}},
    "fetch": {"risk": "network_read", "uses_llm": True, "writes_state": True,
              "requires_confirmation": True, "persona_context": True,
              "title": {"en": "Read a link", "ru": "Чтение ссылок"}},
    "ask": {"risk": "read_only", "uses_llm": True, "persona_context": True,
            "title": {"en": "Knowledge-base Q&A", "ru": "Вопросы по базе"}},
    "list_items": {"risk": "read_only", "title": {"en": "Browse saved", "ru": "Просмотр сохранённого"}},
    "item_detail": {"risk": "read_only", "title": {"en": "Item detail", "ru": "Детали записи"}},
    "show_media": {"risk": "read_only", "title": {"en": "Show photos", "ru": "Показ фото"}},
    "read_media": {"risk": "read_only", "title": {"en": "Read a voice/file", "ru": "Прочитать голос/файл"}},
    "categories": {"risk": "read_only", "title": {"en": "Categories", "ru": "Категории"}},
    "discard": {"risk": "state_write", "writes_state": True,
                "title": {"en": "Discard suggestion", "ru": "Отклонить запись"}},
    "item_delete": {"risk": "state_write", "writes_state": True, "requires_confirmation": True,
                    "title": {"en": "Delete item", "ru": "Удалить запись"}},
    # Replacing a saved note's text is not additive and not undoable, so it is
    # confirmation-gated in the handler (a pending that NAMES the note and shows
    # both versions) — C2, 2026-07-28.
    "note_edit": {"risk": "state_write", "writes_state": True, "persona_context": True,
                  "requires_confirmation": True,
                  "title": {"en": "Edit a note", "ru": "Исправить заметку"}},
    # A movie/book added to the catalog from TEXT. It stages the SAME
    # confirmation card the photo flow uses and writes only on his ✅, so it is a
    # draft_write like reminder_create — and it spends model calls (the lookup /
    # enrichment pass that gives the entry its director, year and genre).
    "catalog_add": {"risk": "draft_write", "uses_llm": True, "writes_state": True,
                    "requires_confirmation": True, "persona_context": True,
                    "title": {"en": "Add to the catalog", "ru": "Добавить в каталог"}},
    "list_field": {"risk": "read_only",
                   "title": {"en": "One field across a list",
                             "ru": "Одно поле по всему списку"}},
    "recategorize": {"risk": "state_write", "writes_state": True, "persona_context": True,
                     "title": {"en": "Re-categorize item", "ru": "Сменить категорию"}},
    # Reversible single ops run directly (undo = the inverse op); a BULK archive
    # is confirmation-gated in the handler via a pending, like item_delete.
    "note_lifecycle": {"risk": "state_write", "writes_state": True, "persona_context": True,
                       "title": {"en": "Note lifecycle", "ru": "Статус заметки"}},
    "note_review": {"risk": "read_only", "persona_context": True,
                    "title": {"en": "Notes review", "ru": "Обзор заметок"}},
    "merge_categories": {"risk": "state_write", "writes_state": True,
                         "title": {"en": "Merge categories", "ru": "Объединить категории"}},
    "purge": {"risk": "destructive", "writes_state": True, "destructive": True,
              "requires_confirmation": "typed_phrase",
              "title": {"en": "Purge", "ru": "Очистка"}},
    # -- reminders / calendar
    "reminder_create": {"risk": "draft_write", "uses_llm": False, "writes_state": True,
                        "requires_confirmation": True, "allowed_proactive": True,
                        "persona_context": True, "title": {"en": "Reminders", "ru": "Напоминания"}},
    "reminder_list": {"risk": "read_only", "title": {"en": "List reminders", "ru": "Список напоминаний"}},
    "reminder_cancel": {"risk": "state_write", "writes_state": True,
                        "title": {"en": "Cancel reminder", "ru": "Отмена напоминания"}},
    "reminder_reschedule": {"risk": "state_write", "writes_state": True,
                            "title": {"en": "Reschedule reminder", "ru": "Перенести напоминание"}},
    "reminder_rename": {"risk": "state_write", "writes_state": True,
                        "title": {"en": "Rename reminder", "ru": "Переименовать напоминание"}},
    "reminder_undo": {"risk": "state_write", "writes_state": True,
                      "title": {"en": "Undo reschedule", "ru": "Вернуть прежнее время"}},
    "list_files": {"risk": "read_only", "title": {"en": "List files", "ru": "Список файлов"}},
    "calendar_add": {"risk": "external_write", "external_network": True, "writes_state": True,
                     "requires_confirmation": True, "title": {"en": "Calendar", "ru": "Календарь"}},
    # -- spend / stats / introspection
    "spend": {"risk": "read_only", "allowed_proactive": True,
              "title": {"en": "AI spend", "ru": "Расходы AI"}},
    "budget_set": {"risk": "state_write", "writes_state": True, "persona_context": True,
                   "title": {"en": "Set AI budget", "ru": "Изменить бюджет AI"}},
    "stats": {"risk": "read_only", "title": {"en": "Stats", "ru": "Статистика"}},
    "vps_stats": {"risk": "read_only", "allowed_proactive": True,
                  "title": {"en": "Server status", "ru": "Статус сервера"}},
    "overview": {"risk": "read_only", "persona_context": True,
                 "title": {"en": "Overview", "ru": "Обзор"}},
    "help": {"risk": "meta", "persona_context": True, "title": {"en": "Help", "ru": "Помощь"}},
    "issues_report": {"risk": "read_only", "title": {"en": "Issues report", "ru": "Отчёт о проблемах"}},
    "report_problem": {"risk": "state_write", "writes_state": True,
                       "title": {"en": "Report a problem", "ru": "Записать проблему"}},
    "multi_action": {"risk": "meta", "title": {"en": "One at a time", "ru": "По одному"}},
    "set_journal": {"risk": "state_write", "writes_state": True,
                    "title": {"en": "Set journal", "ru": "Сделать дневником"}},
    "journal_show": {"risk": "read_only", "persona_context": True,
                     "title": {"en": "Show journal", "ru": "Показать дневник"}},
    # Enabling a scheduled journal invitation is a PROACTIVE preference —
    # explicit confirmation before it turns on (plan v1.1 §11); disable is direct.
    "journal_prompt": {"risk": "state_write", "writes_state": True,
                       "requires_confirmation": True, "persona_context": True,
                       "title": {"en": "Journal prompts", "ru": "Приглашения к дневнику"}},
    "review": {"risk": "read_only", "uses_llm": False, "allowed_proactive": True,
               "persona_context": True, "title": {"en": "Performance review", "ru": "Сводка работы"}},
    # -- memory / personality
    "memory": {"risk": "read_only", "persona_context": True,
               "title": {"en": "Memory", "ru": "Память"}},
    "remember": {"risk": "state_write", "writes_state": True, "requires_confirmation": True,
                 "persona_context": True, "title": {"en": "Remember", "ru": "Запомнить"}},
    "forget": {"risk": "state_write", "writes_state": True,
               "title": {"en": "Forget", "ru": "Забыть"}},
    "self_query": {"risk": "read_only", "uses_llm": False, "persona_context": True,
                   "title": {"en": "What I can do", "ru": "Что я умею"}},
    "persona": {"risk": "read_only", "uses_llm": False, "persona_context": True,
                "title": {"en": "About me", "ru": "Какая я"}},
    "boss_query": {"risk": "read_only", "persona_context": True,
                   "title": {"en": "About you", "ru": "О вас"}},
    "memory_why": {"risk": "read_only", "persona_context": True,
                   "title": {"en": "Why you remember", "ru": "Откуда знаешь"}},
    "proactive_prefs": {"risk": "state_write", "writes_state": True, "persona_context": True,
                        "title": {"en": "Tune check-ins", "ru": "Настроить напоминания"}},
    "boss_memory_update": {"risk": "state_write", "writes_state": True, "requires_confirmation": True,
                           "persona_context": True, "title": {"en": "Update profile", "ru": "Обновить профиль"}},
    "style_update": {"risk": "state_write", "writes_state": True, "requires_confirmation": True,
                     "persona_context": True, "title": {"en": "Adjust tone", "ru": "Настроить тон"}},
    "working_history": {"risk": "read_only", "persona_context": True,
                        "title": {"en": "Working history", "ru": "История работы"}},
    "trace_query": {"risk": "read_only", "title": {"en": "Why did you do that", "ru": "Почему так"}},
    "export": {"risk": "read_only", "title": {"en": "Export to Markdown", "ru": "Экспорт в Markdown"}},
    "memory_review": {"risk": "read_only_suggestion", "persona_context": True,
                      "title": {"en": "Memory review", "ru": "Обзор памяти"}},
    "memory_cleanup": {"risk": "state_write", "writes_state": True, "uses_llm": True,
                       "title": {"en": "Consolidate memory", "ru": "Почистить память"}},
    "memory_curator": {"risk": "read_only_suggestion", "uses_llm": True, "writes_state": True,
                       "requires_confirmation": True, "allowed_proactive": True, "persona_context": True,
                       "internal": True, "title": {"en": "Memory curator", "ru": "Куратор памяти"}},
    "proactive_heartbeat": {"risk": "read_only_suggestion", "allowed_proactive": True,
                            "persona_context": True, "internal": True,
                            "title": {"en": "Proactive check", "ru": "Проактивная проверка"}},
    # -- free-form conversation as Cara (warm chat; reads context, never writes state)
    "converse": {"risk": "read_only", "uses_llm": True, "persona_context": True,
                 "title": {"en": "Conversation", "ru": "Общение"}},
    "recall_conversation": {"risk": "read_only", "uses_llm": True, "persona_context": True,
                            "title": {"en": "Read back our conversation",
                                      "ru": "Перечитать наш разговор"}},
    # -- conversational glue (no capability surface)
    "smalltalk": {"risk": "meta"}, "confirm": {"risk": "meta"}, "amend": {"risk": "meta"},
    "cancel": {"risk": "meta"}, "clarify": {"risk": "meta"}, "out_of_scope": {"risk": "meta"},
    "router": {"risk": "meta", "uses_llm": True},
}


class SkillPolicyError(Exception):
    pass


def get_policy(action):
    base = dict(_DEFAULT)
    if action not in SKILLS:
        raise SkillPolicyError(f"Unknown skill/action: {action}")
    base.update(SKILLS[action])
    return base


def known(action):
    return action in SKILLS


def assert_covers(actions):
    """Every router action MUST have a declared policy. Called at startup so
    drift (a new router action with no manifest entry) crashes immediately
    instead of shipping un-permissioned."""
    missing = sorted(a for a in actions if a not in SKILLS)
    if missing:
        raise SkillPolicyError(f"router actions missing a manifest policy: {missing}")


def assert_proactive_allowed(action):
    if not get_policy(action).get("allowed_proactive"):
        raise SkillPolicyError(f"{action} is not allowed in proactive mode")


def capability_titles(lang):
    """Skills worth surfacing to the operator (excludes meta glue), best for
    generating the capabilities answer from a single source of truth."""
    out = []
    seen = set()
    for action, policy in SKILLS.items():
        if policy.get("internal"):
            continue
        title = (policy.get("title") or {})
        label = title.get(lang) or title.get("en")
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out
