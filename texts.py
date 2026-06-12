#!/usr/bin/env python3
"""Bilingual (ru/en) user-facing templates.

All fixed bot replies come from here — the LLM never free-writes to the user
(its output only fills validated slots like summaries). This is a guardrail:
a scoped router can be sweet-talked, a template cannot.
"""

TEXTS = {
    "start": {
        "ru": ("Я ваш ассистент-инбокс. Пересылайте посты или пишите/наговаривайте запросы: "
               "сохранить сообщение, поставить напоминание, спросить про расходы на AI. "
               "Категории я предлагаю сам — вы подтверждаете ответом или кнопкой."),
        "en": ("I am your inbox assistant. Forward posts or send text/voice requests: "
               "save a message, set a reminder, ask about AI spend. "
               "I suggest categories myself — you confirm by reply or button."),
    },
    "out_of_scope": {
        "ru": ("Это вне моих задач. Я умею: сохранять и категоризировать сообщения, "
               "напоминания, статистика расходов на AI, память о ваших предпочтениях."),
        "en": ("That is outside my scope. I can: save and categorize messages, "
               "reminders, AI spend stats, remember your preferences."),
    },
    "clarify": {
        "ru": "Уточните, что сделать: сохранить это, поставить напоминание или показать статистику?",
        "en": "Please clarify: save this, set a reminder, or show stats?",
    },
    "suggestion": {
        "ru": ("Предлагаю категорию: {category}\nКратко: {summary}\n{counts}\n"
               "Подтвердите ответом (или кнопкой), либо назовите свою категорию."),
        "en": ("Suggested category: {category}\nSummary: {summary}\n{counts}\n"
               "Confirm by reply (or button), or name your own category."),
    },
    "counts": {
        "ru": "(сохранено #{row_id}, фото: {images}, ссылок: {urls})",
        "en": "(saved #{row_id}, {images} images, {urls} URLs)",
    },
    "confirmed": {
        "ru": "Сохранил: {category} (#{row_id})",
        "en": "Saved: {category} (#{row_id})",
    },
    "already_confirmed": {
        "ru": "#{row_id} уже подтверждено: {category}.",
        "en": "#{row_id} is already confirmed as {category}.",
    },
    "duplicate": {
        "ru": "Дубликат #{original_id} ({detail}).",
        "en": "Duplicate of #{original_id} ({detail}).",
    },
    "dup_confirmed": {"ru": "категория: {category}", "en": "category: {category}"},
    "dup_suggested": {
        "ru": "предложено {category}, ждёт подтверждения",
        "en": "suggested {category}, awaiting confirmation",
    },
    "dup_pending": {"ru": "ещё обрабатывается", "en": "classification still pending"},
    "stored_retry": {
        "ru": "Сохранил #{row_id}. Не получил ответ от модели — попробую ещё раз позже.",
        "en": "Stored #{row_id}. Could not get a suggestion, will retry.",
    },
    "stt_failed": {
        "ru": "Не смог распознать голосовое. Отправьте текстом, пожалуйста.",
        "en": "Could not transcribe the voice message. Please send it as text.",
    },
    "voice_quote": {
        "ru": "🎤 Услышал: «{transcript}»",
        "en": "🎤 Heard: \"{transcript}\"",
    },
    "reminder_draft": {
        "ru": "⏰ Напоминание: {title}\nКогда: {when_local} (ваше время)\nПовтор: {recurrence}\nПодтвердить?",
        "en": "⏰ Reminder: {title}\nWhen: {when_local} (your time)\nRepeat: {recurrence}\nConfirm?",
    },
    "reminder_set": {
        "ru": "Поставил напоминание #{rid}: {title} — {when_local}.",
        "en": "Reminder #{rid} set: {title} — {when_local}.",
    },
    "reminder_fired": {
        "ru": "⏰ Напоминание: {title}\nОтветьте «готово» или «через 30 минут», чтобы отложить.",
        "en": "⏰ Reminder: {title}\nReply \"done\" or \"in 30 minutes\" to snooze.",
    },
    "reminder_done": {"ru": "Готово, напоминание закрыто.", "en": "Done, reminder closed."},
    "reminder_snoozed": {
        "ru": "Отложил до {when_local}.",
        "en": "Snoozed until {when_local}.",
    },
    "reminder_list_empty": {"ru": "Активных напоминаний нет.", "en": "No active reminders."},
    "reminder_list_header": {"ru": "Активные напоминания:", "en": "Active reminders:"},
    "reminder_cancelled": {
        "ru": "Отменил напоминание #{rid}: {title}.",
        "en": "Cancelled reminder #{rid}: {title}.",
    },
    "reminder_not_found": {
        "ru": "Не нашёл такое напоминание.",
        "en": "Could not find that reminder.",
    },
    "recurrence_none": {"ru": "нет", "en": "none"},
    "recurrence_daily": {"ru": "ежедневно", "en": "daily"},
    "recurrence_weekly": {"ru": "еженедельно", "en": "weekly"},
    "cancelled": {"ru": "Отменил.", "en": "Cancelled."},
    "nothing_pending": {
        "ru": "Сейчас нечего подтверждать.",
        "en": "There is nothing awaiting confirmation.",
    },
    "budget_warn": {
        "ru": "⚠️ Расходы на AI достигли 80% бюджета ({spent:.2f}$ из {limit:.2f}$ за {period}).",
        "en": "⚠️ AI spend reached 80% of budget (${spent:.2f} of ${limit:.2f} for {period}).",
    },
    "budget_stop": {
        "ru": ("⛔ Бюджет AI исчерпан ({spent:.2f}$ из {limit:.2f}$ за {period}). "
               "Сообщения сохраняются и будут обработаны после сброса бюджета."),
        "en": ("⛔ AI budget exhausted (${spent:.2f} of ${limit:.2f} for {period}). "
               "Messages are stored and will be processed after the budget resets."),
    },
    "period_day": {"ru": "сегодня", "en": "today"},
    "period_month": {"ru": "месяц", "en": "month"},
    "memory_empty": {
        "ru": "Пока ничего о вас не запомнил.",
        "en": "I have not remembered anything about you yet.",
    },
    "memory_header": {"ru": "Что я помню:", "en": "What I remember:"},
    "remember_saved": {"ru": "Запомнил: {value}", "en": "Remembered: {value}"},
    "forgotten": {"ru": "Забыл: {value}", "en": "Forgot: {value}"},
    "forget_not_found": {"ru": "Не нашёл такую запись.", "en": "Could not find that entry."},
    "habit_proposal": {
        "ru": ("Последние {n} постов из «{source}» вы подтверждали как «{category}». "
               "Подтверждать их автоматически?"),
        "en": ("The last {n} posts from \"{source}\" were all confirmed as \"{category}\". "
               "Auto-confirm them from now on?"),
    },
    "habit_enabled": {
        "ru": "Хорошо, посты из «{source}» теперь автоматически идут в «{category}».",
        "en": "OK, posts from \"{source}\" now auto-confirm as \"{category}\".",
    },
    "auto_confirmed": {
        "ru": "Авто: {category} (#{row_id}). Кратко: {summary}",
        "en": "Auto: {category} (#{row_id}). Summary: {summary}",
    },
    "no_categories": {
        "ru": "Категорий пока нет — они появятся после ваших подтверждений.",
        "en": "No categories yet. They are created when you confirm suggestions.",
    },
    "categories_header": {
        "ru": "Категории (подтверждённых сообщений):",
        "en": "Categories (confirmed messages):",
    },
    "stats_empty": {"ru": "Сообщений пока нет.", "en": "No messages stored yet."},
    "llm_error": {
        "ru": "Не получилось обработать запрос (модель недоступна). Попробуйте позже — сообщение не потеряно.",
        "en": "Could not process the request (model unavailable). Try again later — nothing is lost.",
    },
    "stats_status": {"ru": "По статусам:", "en": "By status:"},
    "stats_categories": {"ru": "Подтверждено по категориям:", "en": "Confirmed by category:"},
}


def T(lang, key, **kwargs):
    entry = TEXTS[key]
    template = entry.get(lang) or entry["en"]
    return template.format(**kwargs) if kwargs else template
