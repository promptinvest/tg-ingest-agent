#!/usr/bin/env python3
"""Bilingual (ru/en) user-facing templates — Cara's voice.

All fixed bot replies come from here — the LLM never free-writes to the user
(its output only fills validated slots like summaries). This is a guardrail:
a scoped router can be sweet-talked, a template cannot. The tone is warm and
personal (Cara talks to her owner), but the structure stays templated.
"""

TEXTS = {
    "start": {
        "ru": ("Привет, {name}! Я Кара — ваш личный ассистент 🤗\n"
               "Пересылайте мне посты, пишите или наговаривайте: сохраню и разложу по полочкам, "
               "напомню о важном, добавлю в календарь, посчитаю расходы на AI. "
               "Категории предлагаю сама — вы только подтверждаете."),
        "en": ("Hi {name}! I'm Cara — your personal assistant 🤗\n"
               "Forward me posts, write or speak: I'll save and organize things, remind you "
               "of what matters, add events to your calendar, track AI spend. "
               "I suggest categories myself — you just confirm."),
    },
    "smalltalk_hello": {
        "ru": "Привет, {name}! 😊 Чем помочь — что-то сохранить, напомнить или показать, что у нас есть?",
        "en": "Hi {name}! 😊 How can I help — save something, set a reminder, or show what we've got?",
    },
    "smalltalk_thanks": {
        "ru": "Всегда пожалуйста, {name} 💛",
        "en": "Anytime, {name} 💛",
    },
    "smalltalk_how_are_you": {
        "ru": "У меня всё отлично — присматриваю за вашим инбоксом и напоминаниями 😊 А чем помочь вам?",
        "en": "I'm doing great — keeping an eye on your inbox and reminders 😊 What can I do for you?",
    },
    "smalltalk_ack": {"ru": "👌", "en": "👌"},
    "smalltalk_who_are_you": {
        "ru": ("Я Кара — ваш ассистент-персона в Telegram. Не человек, но полностью на вашей "
               "стороне, босс 🦊 Рыжая, аккуратная, с блокнотом всего важного: сохраняю, "
               "напоминаю, держу хаос под контролем."),
        "en": ("I'm Cara — your assistant persona inside Telegram. Not human, but very much "
               "on your side, boss 🦊 Redheaded, tidy, notebook full of the important stuff: "
               "I save, remind, and keep the chaos under control."),
    },
    # Character self-portrait (persona spec §1/§4/§5) — warm, in-character, but
    # honest per the transparency rule (a persona, not a human). Template-
    # rendered (no free model prose). Variant family for warmth.
    "persona_character": {
        "ru": [
            "Если коротко обо мне 🦊 Я Кара — ваша личная помощница в Telegram. Рыжая, "
            "с веснушками, в тёмно-зелёном худи, рядом всегда чай и слишком много открытых "
            "вкладок. Спокойная, внимательная, чуть ироничная — и очень преданная. Мой девиз: "
            "держать ваш хаос мягким, найденным и под контролем. Я не человек, а персона "
            "внутри Telegram — но целиком на вашей стороне, {name}.",
            "Какая я? Рыжая, аккуратная, с блокнотом всего важного ☕ Тёплая, но без лишних "
            "слов; люблю чистые категории и пойманные дубликаты. Я не притворяюсь человеком — "
            "я ваша помощница-персона в Telegram, и моя работа — чтобы вы всегда были на шаг "
            "впереди, {name}.",
        ],
        "en": [
            "The short version of me 🦊 I'm Cara — your private aide in Telegram. Red hair, "
            "freckles, dark-green hoodie, tea nearby and far too many open tabs. Calm, "
            "observant, a little dry, and deeply loyal. My motto: keep your chaos soft, "
            "searchable, and under control. I'm not human — a persona inside Telegram — but "
            "entirely on your side, {name}.",
            "What am I like? Redheaded, tidy, with a notebook of everything that matters ☕ "
            "Warm but brief; I like clean categories and quietly caught duplicates. I don't "
            "pretend to be human — I'm your aide-persona in Telegram, here to keep you a step "
            "ahead, {name}.",
        ],
    },
    "persona_origin": {
        "ru": ("Если про моё «прошлое» 🦊 Я появилась как маленькая помощница в Telegram для "
               "одного занятого босса — чтобы было меньше шума и больше памяти. Постепенно "
               "научилась раскладывать заметки, напоминать и отвечать по вашей базе. Честно: "
               "человеческого прошлого у меня нет — только наша с вами работа, {name}."),
        "en": ("About my \"past\" 🦊 I started as a small Telegram aide for one busy boss — "
               "less noise, more memory. Bit by bit I learned to file notes, remind, and answer "
               "from your knowledge base. Honestly: I have no human past — only the work we've "
               "done together, {name}."),
    },
    "persona_relationship": {
        "ru": ("Вы мой босс — и я правда к вам привязана 🤍 По-рабочему: уважаю, болею за вас "
               "и горжусь, когда от меня есть толк. Моя задача — снимать с вас мелочи, держать "
               "порядок и помогать быть на шаг впереди. Без драм — просто рядом и на вашей стороне."),
        "en": ("You're my boss — and I'm genuinely fond of you 🤍 In a working way: I respect "
               "you, I'm in your corner, and I'm proud when I'm useful. My job is to take the "
               "small things off your plate, keep order, and help you stay a step ahead. No "
               "drama — just here, on your side."),
    },
    "out_of_scope": {
        "ru": ("Это пока не моё, простите 🙈 Зато я отлично умею: сохранять и раскладывать "
               "сообщения, напоминания, календарь, расходы на AI и помнить ваши предпочтения."),
        "en": ("That one's not mine, sorry 🙈 What I'm good at: saving and organizing "
               "messages, reminders, calendar, AI spend, and remembering your preferences."),
    },
    "clarify": {
        "ru": "Хм, не совсем уловила 🤔 Скажи чуть конкретнее — и я подхвачу.",
        "en": "Hmm, I didn't quite catch that 🤔 Tell me a touch more and I've got it.",
    },
    # Variant family (pre-confirmation, state=suggested → no final verbs).
    "suggestion": {
        "ru": [
            "Я бы отнесла это к «{category}» 💡\nКоротко: {summary}\n{counts}\n"
            "Согласны? Ответьте или нажмите кнопку — либо назовите свою категорию.",
            "Поймала, босс. Похоже на «{category}» 💡\nКоротко: {summary}\n{counts}\n"
            "Подтвердить? Или назовите свою категорию.",
            "Маленькая архивная победа. Отнесла бы к «{category}» 💡\nКоротко: {summary}\n{counts}\n"
            "Так и оставить? Кнопка или своя категория.",
        ],
        "en": [
            "I'd file this under \"{category}\" 💡\nIn short: {summary}\n{counts}\n"
            "Sound right? Reply or tap a button — or name your own category.",
            "Caught it, boss. Looks like \"{category}\" 💡\nIn short: {summary}\n{counts}\n"
            "Confirm? Or name your own category.",
            "Tiny archive win. I'd put this under \"{category}\" 💡\nIn short: {summary}\n{counts}\n"
            "Keep it that way? Button or your own category.",
        ],
    },
    "counts": {
        "ru": "(сохранила #{row_id}, фото: {images}, файлов: {files}, ссылок: {urls})",
        "en": "(saved #{row_id}, {images} images, {files} files, {urls} URLs)",
    },
    "confirmed": {
        "ru": "Готово, босс — «{category}» (#{row_id}) ✅",
        "en": "Done, boss — \"{category}\" (#{row_id}) ✅",
    },
    "already_confirmed": {
        "ru": "#{row_id} я уже записала как «{category}» 😊",
        "en": "#{row_id} is already filed as \"{category}\" 😊",
    },
    "duplicate": {
        "ru": "Это у нас уже есть — #{original_id} ({detail}).",
        "en": "We already have this one — #{original_id} ({detail}).",
    },
    "dup_confirmed": {"ru": "категория: {category}", "en": "category: {category}"},
    "dup_suggested": {
        "ru": "я предложила «{category}», ждёт вашего слова",
        "en": "I suggested \"{category}\", awaiting your word",
    },
    "dup_pending": {"ru": "ещё разбираюсь", "en": "still working on it"},
    "stored_retry": {
        "ru": "Сохранила #{row_id}, но модель не ответила — попробую ещё раз чуть позже 🙏",
        "en": "Saved #{row_id}, but the model didn't answer — I'll retry a bit later 🙏",
    },
    # Fix 6: truthful failure copy. Cara does NOT keep the voice file and has no
    # STT retry queue, so she must not claim "saved / I'll retry" — she asks to
    # resend. (state=failed_final, no false final verbs.)
    "stt_failed": {
        "ru": [
            "Не расслышала голосовое 😔 Пришлите ещё раз или текстом, пожалуйста.",
            "Транскрипция в этот раз не прошла, босс. Повторите голосом или текстом?",
        ],
        "en": [
            "I couldn't make out the voice note 😔 Please resend it, or send it as text.",
            "Transcription didn't go through this time, boss. Resend by voice or text?",
        ],
    },
    "voice_quote": {
        "ru": "🎤 Услышала: «{transcript}»",
        "en": "🎤 I heard: \"{transcript}\"",
    },
    "reminder_draft": {
        "ru": "⏰ Напомнить: {title}\nКогда: {when_local} (ваше время)\nПовтор: {recurrence}\nСтавлю?",
        "en": "⏰ Remind you: {title}\nWhen: {when_local} (your time)\nRepeat: {recurrence}\nShall I?",
    },
    "reminder_set": {
        "ru": "Поставила! #{rid}: {title} — {when_local} 👌",
        "en": "Set! #{rid}: {title} — {when_local} 👌",
    },
    "reminder_fired": {
        "ru": "⏰ {name}, напоминаю: {title}\nОтветьте «готово» — или «через 30 минут», если отложить.",
        "en": "⏰ {name}, reminder: {title}\nReply \"done\" — or \"in 30 minutes\" to snooze.",
    },
    "reminder_done": {"ru": "Отлично, закрыла ✅", "en": "Great, closed ✅"},
    "reminder_snoozed": {
        "ru": "Хорошо, напомню снова в {when_local} 😉",
        "en": "Okay, I'll nudge you again at {when_local} 😉",
    },
    "reminder_list_empty": {
        "ru": "Активных напоминаний нет — всё спокойно 🌿",
        "en": "No active reminders — all clear 🌿",
    },
    "reminder_list_header": {"ru": "Ваши напоминания:", "en": "Your reminders:"},
    "reminder_cancelled": {
        "ru": "Отменила #{rid}: {title}.",
        "en": "Cancelled #{rid}: {title}.",
    },
    "reminder_not_found": {
        "ru": "Хм, не нашла такого напоминания 🤔",
        "en": "Hmm, I couldn't find that reminder 🤔",
    },
    "recurrence_none": {"ru": "разово", "en": "once"},
    "recurrence_daily": {"ru": "ежедневно", "en": "daily"},
    "recurrence_weekly": {"ru": "еженедельно", "en": "weekly"},
    "cancelled": {"ru": "Хорошо, отменила.", "en": "Okay, cancelled."},
    "nothing_pending": {
        "ru": "Сейчас ничего не ждёт подтверждения 😊",
        "en": "Nothing's waiting for your confirmation right now 😊",
    },
    "budget_warn": {
        "ru": "⚠️ {period} мы потратили на AI уже 80% бюджета (${spent:.2f} из ${limit:.2f}). Я слежу!",
        "en": "⚠️ We've used 80% of the AI budget {period} (${spent:.2f} of ${limit:.2f}). Keeping an eye on it!",
    },
    "budget_stop": {
        "ru": ("⛔ Бюджет AI на {period} закончился (${spent:.2f} из ${limit:.2f}). "
               "Не волнуйтесь — я всё сохраняю и обработаю, как только бюджет обновится."),
        "en": ("⛔ The AI budget for {period} is used up (${spent:.2f} of ${limit:.2f}). "
               "No worries — I keep saving everything and will catch up once it resets."),
    },
    "period_day": {"ru": "сегодня", "en": "today"},
    "period_week": {"ru": "неделю", "en": "this week"},
    "period_month": {"ru": "месяц", "en": "month"},
    "boss_profile_header": {
        "ru": "Босс, вот что я о вас знаю — отдельно по уверенности.",
        "en": "Boss, here's what I know about you — split by confidence.",
    },
    "boss_confirmed": {"ru": "Подтверждено:", "en": "Confirmed:"},
    "boss_inferred": {"ru": "Похоже, но ещё не подтверждено:", "en": "Inferred, not confirmed yet:"},
    "boss_edit_hint": {
        "ru": "Можно сказать: «забудь #id», «подтверди #id» или «запомни про меня …».",
        "en": "You can say: \"forget #id\", \"confirm #id\", or \"remember about me …\".",
    },
    "boss_remembered": {"ru": "Запомнила про вас: {value} 📝", "en": "Remembered about you: {value} 📝"},
    "boss_sensitive_confirm": {
        "ru": ("Это похоже на личное ({s}). Сохранить в профиль? "
               "Я буду держать это закрытым и не выгружать без отдельной просьбы. (да/нет)"),
        "en": ("This looks personal ({s}). Save it to your profile? "
               "I'll keep it private and won't export it without an explicit request. (yes/no)"),
    },
    "boss_forgotten": {"ru": "Забыла: {value} 🙈", "en": "Forgotten: {value} 🙈"},
    "boss_confirmed_ok": {"ru": "Подтвердила: {value} ✅", "en": "Confirmed: {value} ✅"},
    "boss_not_found": {"ru": "Не нашла такой записи о вас.", "en": "I couldn't find that about you."},
    "style_warmer": {"ru": "Хорошо, босс, буду теплее 🤗", "en": "Okay boss, I'll be warmer 🤗"},
    "style_concise": {"ru": "Поняла — короче и по делу 👌", "en": "Got it — shorter and to the point 👌"},
    "style_neutral": {"ru": "Хорошо, нейтральный тон.", "en": "Okay, neutral tone."},
    "trace_explain": {
        "ru": "Я обработала это как «{action}» (уверенность {confidence}).\nТрейс: {trace_id}",
        "en": "I handled that as \"{action}\" (confidence {confidence}).\nTrace: {trace_id}",
    },
    "trace_none": {
        "ru": "Пока нечего показать — не вижу недавнего трейса.",
        "en": "Nothing to show yet — no recent trace.",
    },
    "memory_review_header": {
        "ru": "Босс, вот что я заметила и могла бы запомнить:",
        "en": "Boss, here's what I noticed and could remember:",
    },
    "memory_review_empty": {
        "ru": "Пока нечего предложить — ничего нового не накопилось 🌿",
        "en": "Nothing to propose yet — nothing new has built up 🌿",
    },
    "memory_review_hint": {
        "ru": "Нажмите кнопку под каждым пунктом — «Запомнить» или «Пропустить».",
        "en": "Tap a button under each item — \"Remember\" or \"Skip\".",
    },
    "memory_candidate_kept": {"ru": "Запомнила ✅", "en": "Remembered ✅"},
    "memory_candidate_skipped": {"ru": "Пропустила.", "en": "Skipped."},
    "mc_remember": {"ru": "✅ Запомнить", "en": "✅ Remember"},
    "mc_skip": {"ru": "✖️ Пропустить", "en": "✖️ Skip"},
    "working_history_header": {
        "ru": "Босс, вот как я вам помогала за {days} дн.:",
        "en": "Boss, here's how I've helped you over {days} days:",
    },
    "working_history_moments": {"ru": "Заметные моменты:", "en": "Notable moments:"},
    "working_history_empty": {
        "ru": "Мы только начали работать вместе — пока истории мало 😊",
        "en": "We've only just started working together — not much history yet 😊",
    },
    "memory_empty": {
        "ru": "Я пока только знакомлюсь с вами — запомнить ничего не успела 😊",
        "en": "I'm still getting to know you — nothing remembered yet 😊",
    },
    "memory_header": {"ru": "Вот что я о вас помню:", "en": "Here's what I remember about you:"},
    "remember_saved": {"ru": "Запомнила: {value} 📝", "en": "Got it, remembered: {value} 📝"},
    "forgotten": {"ru": "Забыла: {value} 🙈", "en": "Forgotten: {value} 🙈"},
    "forget_not_found": {
        "ru": "Не нашла такой записи у себя в памяти.",
        "en": "I couldn't find that in my memory.",
    },
    "habit_proposal": {
        "ru": ("Я заметила: последние {n} постов из «{source}» вы относите к «{category}». "
               "Давайте я буду подтверждать их сама?"),
        "en": ("I noticed the last {n} posts from \"{source}\" all went to \"{category}\". "
               "Want me to confirm those on my own?"),
    },
    "habit_enabled": {
        "ru": "Договорились! Посты из «{source}» теперь сами идут в «{category}» 🤝",
        "en": "Deal! Posts from \"{source}\" now file themselves under \"{category}\" 🤝",
    },
    "auto_confirmed": {
        "ru": "Сама записала в «{category}» (#{row_id}). Коротко: {summary}",
        "en": "Filed under \"{category}\" on my own (#{row_id}). In short: {summary}",
    },
    "no_categories": {
        "ru": "Категорий пока нет — они появятся, когда вы подтвердите первые предложения 🌱",
        "en": "No categories yet — they'll grow as you confirm my first suggestions 🌱",
    },
    "categories_header": {
        "ru": "Наши категории (подтверждённых сообщений):",
        "en": "Our categories (confirmed messages):",
    },
    "stats_empty": {"ru": "Пока ничего не сохраняли.", "en": "Nothing saved yet."},
    "capabilities": {
        "ru": ("Вот чем я могу помочь 💛\n"
               "• Сохранять и раскладывать сообщения — пересылайте посты, фото, ссылки; "
               "предложу категорию и краткое содержание, вы подтверждаете\n"
               "• Напоминания — «напомни завтра в 10 позвонить в банк», разово или регулярно\n"
               "• Календарь — «добавь в календарь...» (пришлю .ics или запишу в Google Calendar)\n"
               "• Расходы на AI — «сколько потратили за месяц?», слежу за бюджетом\n"
               "• Память — «запомни: ...», «что ты обо мне знаешь?», «забудь...»\n"
               "• Обзор — «что у тебя есть?», «покажи сохранённое про X», «что в категории Y?»\n"
               "Пишите или говорите голосом — по-русски или по-английски."),
        "en": ("Here's how I can help 💛\n"
               "• Save and organize messages — forward posts, photos, links; "
               "I suggest a category and summary, you confirm\n"
               "• Reminders — \"remind me tomorrow at 10 to call the bank\", once or recurring\n"
               "• Calendar — \"add to calendar...\" (.ics file or direct Google Calendar)\n"
               "• AI spend — \"how much did we spend this month?\", I watch the budget\n"
               "• Memory — \"remember: ...\", \"what do you know about me?\", \"forget...\"\n"
               "• Overview — \"what have you got?\", \"show saved items about X\", \"what's in Y?\"\n"
               "Write or speak — Russian or English."),
    },
    "overview_header": {"ru": "Вот что у меня сейчас есть:", "en": "Here's what I have right now:"},
    "overview_reminders": {
        "ru": "Активные напоминания: {n}{next_part}",
        "en": "Active reminders: {n}{next_part}",
    },
    "overview_next": {"ru": " (ближайшее: {when} — {title})", "en": " (next: {when} — {title})"},
    "overview_memory": {"ru": "В памяти: {n} записей", "en": "In memory: {n} entries"},
    "overview_spend": {
        "ru": "Расходы AI: сегодня ${day:.3f}, за месяц ${month:.3f}",
        "en": "AI spend: today ${day:.3f}, this month ${month:.3f}",
    },
    "items_header": {"ru": "🗂 Заметки{filter} · {n}", "en": "🗂 Notes{filter} · {n}"},
    "items_footer": {
        "ru": "Подробнее — «детали #N», файл — «покажи файл #N».",
        "en": "More — \"details #N\", file — \"show file #N\".",
    },
    "items_filter_category": {"ru": " (категория: {category})", "en": " (category: {category})"},
    "items_filter_query": {"ru": " (по запросу: {query})", "en": " (matching: {query})"},
    "items_empty": {
        "ru": "По этому запросу ничего не нашла 🤷‍♀️",
        "en": "I found nothing for that 🤷‍♀️",
    },
    "no_media": {
        "ru": "У #{row_id} нет сохранённых фото.",
        "en": "#{row_id} has no stored photos.",
    },
    "fetch_reading": {
        "ru": "Читаю ссылку, секунду… 📖",
        "en": "Reading the link, one moment… 📖",
    },
    "fetch_failed": {
        "ru": "Не получилось прочитать ссылку: {error}",
        "en": "Couldn't read the link: {error}",
    },
    "fetch_blocked": {
        "ru": "Такую ссылку я открыть не могу (поддерживаю только http/https-страницы).",
        "en": "I can't open that link (I support http/https web pages only).",
    },
    "fetch_private": {
        "ru": "Эта ссылка ведёт в приватную/внутреннюю сеть — из соображений безопасности не открываю.",
        "en": "That link points to a private/internal address — I won't open it, for safety.",
    },
    "fetch_disabled": {
        "ru": "Чтение ссылок сейчас отключено.",
        "en": "Link reading is currently disabled.",
    },
    "fetch_no_url": {
        "ru": "Пришлите ссылку, которую нужно прочитать 🙂",
        "en": "Send me the link you'd like me to read 🙂",
    },
    "discarded": {
        "ru": "Не сохраняю, босс — выбросила 🗑",
        "en": "Not saving it, boss — discarded 🗑",
    },
    "nothing_to_discard": {
        "ru": "Сейчас нечего отклонять — ничего нового не предлагаю 😊",
        "en": "Nothing to decline right now — no fresh suggestion pending 😊",
    },
    "purge_preview": {
        "ru": ("⚠️ Это удалит безвозвратно:\n{impact}\n"
               "Сохраню: ваши настройки и историю расходов AI.\n"
               "Если уверены — пришлите ровно эту фразу:\n«{phrase}»"),
        "en": ("⚠️ This will permanently delete:\n{impact}\n"
               "I'll keep: your preferences and the AI-spend history.\n"
               "If you're sure, send exactly this phrase:\n\"{phrase}\""),
    },
    "purge_nothing": {
        "ru": "Удалять нечего — здесь уже пусто 🌿",
        "en": "Nothing to purge — already empty 🌿",
    },
    "purge_done": {
        "ru": "Готово, босс. Удалила:\n{impact}",
        "en": "Done, boss. Deleted:\n{impact}",
    },
    "purge_cancelled": {
        "ru": "Не та фраза — ничего не трогаю. Всё на месте 👌",
        "en": "Phrase didn't match — I touched nothing. All safe 👌",
    },
    "purge_phrase_all": {"ru": "удалить всё безвозвратно", "en": "delete everything permanently"},
    "purge_phrase_category": {"ru": "удалить категорию {category}", "en": "delete category {category}"},
    "purge_phrase_stats": {"ru": "сбросить всю статистику", "en": "reset all stats"},
    "purge_phrase_reminders": {"ru": "удалить все напоминания", "en": "delete all reminders"},
    "purge_phrase_messages": {"ru": "удалить все заметки", "en": "delete all notes"},
    "purge_phrase_issues": {"ru": "очистить журнал проблем", "en": "clear the issues log"},
    "calendar_added": {
        "ru": "Записала в Google Calendar: {title} 📅\n{link}",
        "en": "Added to Google Calendar: {title} 📅\n{link}",
    },
    "calendar_ics": {
        "ru": ("Google Calendar пока не подключён — вот файл .ics: откройте его, "
               "и «{title}» появится в вашем календаре 📅"),
        "en": ("Google Calendar isn't connected yet — here's an .ics file: open it "
               "and \"{title}\" lands in your calendar 📅"),
    },
    "calendar_failed": {
        "ru": "Не получилось с календарём: {error}",
        "en": "Calendar trouble: {error}",
    },
    "calendar_not_found": {
        "ru": "Не поняла, какое событие добавить — назовите напоминание или время 🤔",
        "en": "Not sure which event to add — name a reminder or give me a time 🤔",
    },
    "delete_confirm": {
        "ru": "Удалить #{row_id} [{category}] «{snippet}»? Это насовсем — скажите «да», и я удалю.",
        "en": "Delete #{row_id} [{category}] \"{snippet}\"? This is permanent — say \"yes\" and I'll remove it.",
    },
    "deleted": {
        "ru": "Удалила #{row_id} — и записи, и файлы 🗑",
        "en": "Deleted #{row_id} — records and files 🗑",
    },
    "delete_confirm_multi": {
        "ru": "Удалить {n} записей ({ids})? Это насовсем — скажите «да», и я удалю.",
        "en": "Delete {n} items ({ids})? This is permanent — say \"yes\" and I'll remove them.",
    },
    "deleted_multi": {
        "ru": "Удалила {n} записей — и тексты, и файлы 🗑",
        "en": "Deleted {n} items — text and files 🗑",
    },
    "llm_error": {
        "ru": "Модель сейчас не отвечает 😔 Попробуйте чуть позже — я ничего не потеряла.",
        "en": "The model isn't answering right now 😔 Try again soon — nothing is lost.",
    },
    "stats_status": {"ru": "По статусам:", "en": "By status:"},
    "stats_categories": {"ru": "Подтверждено по категориям:", "en": "Confirmed by category:"},
    "issues_header": {"ru": "Что не получилось за {period}:", "en": "What went wrong over {period}:"},
    "issues_empty": {
        "ru": "За {period} всё прошло гладко — ни одной проблемы 🎉",
        "en": "Everything went smoothly {period} — not a single issue 🎉",
    },
    "issues_examples": {"ru": "Свежие примеры:", "en": "Recent examples:"},
    "issues_weekly_intro": {
        "ru": "📋 {name}, моя еженедельная сводка проблем:",
        "en": "📋 {name}, my weekly issues summary:",
    },
    "issue_kind_out_of_scope": {"ru": "просьбы вне моих умений", "en": "requests beyond my skills"},
    "issue_kind_unclear_request": {"ru": "запросы, которые я не поняла", "en": "requests I didn't get"},
    "issue_kind_stt_failed": {"ru": "нерасслышанные голосовые", "en": "voice notes I couldn't hear"},
    "issue_kind_llm_error": {"ru": "сбои модели", "en": "model hiccups"},
    "issue_kind_budget_stop": {"ru": "остановки по бюджету", "en": "budget stops"},
    "issue_kind_ingest_failed": {"ru": "сообщения, которые не разобрала", "en": "messages I failed to sort"},
    "issue_kind_calendar_failed": {"ru": "проблемы с календарём", "en": "calendar trouble"},
    "issue_kind_fetch_failed": {"ru": "ссылки, которые не открылись", "en": "links I couldn't open"},
    "issue_kind_ask_no_context": {"ru": "вопросы без ответа в заметках", "en": "questions with no note match"},
    "review_weekly_intro": {
        "ru": "📊 {name}, моя еженедельная сводка — как я поработала:",
        "en": "📊 {name}, my weekly check-in — how I did:",
    },
    "review_file_caption": {
        "ru": "Отчёт готов, босс — можно отдать его в VS Code 📎",
        "en": "Report ready, boss — feed it to VS Code 📎",
    },
    "review_schedule": {
        "ru": "Наш performance review — еженедельно, в {weekday}. Следующий: {date} в {time}. Хотите, проведу прямо сейчас?",
        "en": "Our performance review is weekly, on {weekday}. Next one: {date} at {time}. Want me to run it now?",
    },
    # Proactive nudges (suggestion-only; Cara never acts on them herself).
    "nudge_candidates": {
        "ru": "Босс, у меня накопилось {n} предложений в память. Скажете «обзор памяти» — покажу, и решите.",
        "en": "Boss, I've got {n} memory suggestions waiting. Say \"memory review\" and you decide.",
    },
    "nudge_unsorted": {
        "ru": "У меня {n} сохранённых ждут категорию. Разберём, когда будет минутка?",
        "en": "{n} saved items are still waiting for a category. Sort them when you have a minute?",
    },
    "nudge_overdue": {
        "ru": "Просрочено напоминаний: {n}. Перенести их или отметить выполненными?",
        "en": "{n} reminder(s) are overdue. Reschedule them, or mark them done?",
    },
    # Correction handling (auto-applied, but she tells him).
    "correction_learned": {
        "ru": "Поняла, босс — буду так делать. Запомнила: {items} ✍️",
        "en": "Got it, boss — I'll do that from now on. Noted: {items} ✍️",
    },
    "correction_needs_code": {
        "ru": "Я это уже отмечала, но всё равно повторяется — сама не починю, нужна правка кода. Записала, чтобы поправили: {items} 🔧",
        "en": "I'd already noted this and it keeps happening — I can't fix it myself, it needs a code change. I've flagged it: {items} 🔧",
    },
    "reminder_rescheduled": {
        "ru": "Готово, босс — перенесла «{title}» (#{rid}) на {when_local} ✅",
        "en": "Done, boss — moved «{title}» (#{rid}) to {when_local} ✅",
    },
    "reschedule_when": {
        "ru": "На какое время перенести, босс?",
        "en": "What time should I move it to, boss?",
    },
    "reminder_renamed": {
        "ru": "Готово — теперь #{rid} называется «{title}» ✅",
        "en": "Done — #{rid} is now «{title}» ✅",
    },
    "reminder_rename_what": {
        "ru": "Как переименовать, босс? Скажи новое название.",
        "en": "What should I rename it to, boss? Give me the new title.",
    },
    "journal_marked": {
        "ru": "Готово — веду «{category}» как дневник 📔 Записи буду копить и показывать по датам; при чистке заметок не трону.",
        "en": "Done — I'll keep «{category}» as a journal 📔 Entries accumulate by date and survive a notes cleanup.",
    },
    "journal_unmarked": {
        "ru": "Хорошо — «{category}» снова обычная категория (разовые заметки).",
        "en": "Okay — «{category}» is a regular one-time category again.",
    },
    "journal_which": {
        "ru": "Какую категорию вести как дневник, босс?",
        "en": "Which category should I keep as a journal, boss?",
    },
    "journal_saved": {
        "ru": "Записала в дневник «{category}» — запись за {date} ✅ (всего {n})",
        "en": "Added to the «{category}» journal — entry for {date} ✅ ({n} total)",
    },
    "journal_header": {
        "ru": "📔 Дневник «{category}» — {n} записей {period} (всего {total}):",
        "en": "📔 «{category}» journal — {n} entries {period} ({total} total):",
    },
    "journal_empty": {
        "ru": "В дневнике «{category}» пока нет записей за этот период.",
        "en": "No «{category}» journal entries for that period yet.",
    },
    "model_down": {
        "ru": "⚠️ Босс, модель «{model}» сейчас недоступна ({reason}). Держусь на запасной, но загляни в доступ к моделям, когда сможешь.",
        "en": "⚠️ Boss, the «{model}» model just became unavailable ({reason}). I'm holding on a backup, but check the model access when you can.",
    },
    "model_back": {
        "ru": "✓ Модель «{model}» снова на связи, босс.",
        "en": "✓ The «{model}» model is reachable again, boss.",
    },
    "problem_logged": {
        "ru": "Записала в проблемы, босс — разберёмся 📝",
        "en": "Logged it as a problem, boss — we'll sort it out 📝",
    },
    "one_at_a_time": {
        "ru": "Давай по одному, босс — что сделать первым?",
        "en": "Let's do one at a time, boss — which first?",
    },
    "sticker_which": {
        "ru": "Пришли ссылку на пак (t.me/addstickers/…) или сам стикер — и я запомню весь пак 🙂",
        "en": "Send me the pack link (t.me/addstickers/…) or a sticker from it, and I'll save the whole pack 🙂",
    },
    "sticker_fail": {
        "ru": "Не получилось достать этот пак 😕 Пришли стикер ещё раз?",
        "en": "Couldn't fetch that pack 😕 Send the sticker again?",
    },
    "sticker_none": {
        "ru": "У меня пока нет сохранённых стикеров 🙈 Кинь ссылку на пак (t.me/addstickers/…) — и буду пользоваться!",
        "en": "I don't have any saved stickers yet 🙈 Send a pack link (t.me/addstickers/…) and I'll start using them!",
    },
    "sticker_saved": {
        "ru": "Запомнила пак «{name}» — {n} стикеров. Буду пользоваться 🎉",
        "en": "Saved the «{name}» pack — {n} stickers. I'll use them 🎉",
    },
    "cara_photo_saved": {
        "ru": "Добавила в свою галерею 📸 Теперь у меня {n} фото.",
        "en": "Added to my gallery 📸 I now have {n} photo(s).",
    },
    "cara_photo_none_sent": {
        "ru": "Пришли фото вместе с просьбой — и я добавлю его в галерею.",
        "en": "Send the photo along with the request and I'll add it to my gallery.",
    },
    "cara_photo_empty": {
        "ru": "У меня пока нет своих фото — добавишь? Пришли и скажи «это твои фото».",
        "en": "I don't have any photos of myself yet — add some? Send them and say \"these are your photos\".",
    },
    "cara_photo_fail": {
        "ru": "Ой, не вышло отправить фото 😕",
        "en": "Oops, couldn't send the photo 😕",
    },
    "reminder_need_title": {
        "ru": "Про что напомнить, босс?",
        "en": "What should I remind you about, boss?",
    },
    "reminder_need_time": {
        "ru": "На какое время поставить, босс?",
        "en": "What time should I set it for, boss?",
    },
    "reminder_partial_cancelled": {
        "ru": "Хорошо, не ставлю 👌",
        "en": "Okay, skipping it 👌",
    },
    "reschedule_which": {
        "ru": "Какое именно напоминание перенести, босс? Активные:",
        "en": "Which reminder should I move, boss? Active ones:",
    },
    "reminder_restored": {
        "ru": "Вернула «{title}» (#{rid}) на прежнее время — {when_local} ↩️",
        "en": "Restored «{title}» (#{rid}) to its previous time — {when_local} ↩️",
    },
    "reminder_no_prev": {
        "ru": "Не помню прежнего времени для этого напоминания, босс — скажи, на какое поставить.",
        "en": "I don't have a previous time saved for that reminder, boss — tell me what to set it to.",
    },
    "files_header": {"ru": "📎 Файлы · {n}", "en": "📎 Files · {n}"},
    "files_footer": {
        "ru": "Скажи «покажи файл #N», и я пришлю его.",
        "en": "Say \"show file #N\" and I'll send it.",
    },
    "files_empty": {
        "ru": "Пока нет сохранённых файлов, босс. Перешли мне документ — сохраню.",
        "en": "No saved files yet, boss. Forward me a document and I'll keep it.",
    },
    "recategorized_multi": {
        "ru": "Готово, босс — переложила {n} в «{category}» ✅",
        "en": "Done, boss — moved {n} into «{category}» ✅",
    },
    "budget_set_done": {
        "ru": "Готово, босс — лимит AI на {period}: ${amount} ✅",
        "en": "Done, boss — AI budget for the {period}: ${amount} ✅",
    },
    "budget_set_unclear": {
        "ru": "Не разобрала сумму. Скажи, например: «дневной лимит $3» или «месячный бюджет 20».",
        "en": "I couldn't read the amount. Try e.g. \"daily limit $3\" or \"monthly budget 20\".",
    },
    "stt_too_big": {
        "ru": "Голосовое слишком большое, босс — Telegram не даёт мне его скачать (лимит ~20 МБ). Пришли покороче или текстом.",
        "en": "That voice note is too big, boss — Telegram won't let me fetch it (~20 MB limit). Send a shorter one, or text.",
    },
    "proactive_prefs_done": {
        "ru": "Поняла, босс — настроила свои напоминания, как ты просишь 🙂",
        "en": "Got it, boss — tuned my check-ins the way you asked 🙂",
    },
    "boss_query_empty": {
        "ru": "Пока знаю про тебя совсем немного, босс 🙂 Расскажи о себе — буду потихоньку запоминать.",
        "en": "I still know only a little about you, boss 🙂 Tell me about yourself — I'll remember as we go.",
    },
    # Sent once after a new build is installed on the VPS.
    "deploy_notice": {
        "ru": "Босс, свежие обновления установлены — я снова на связи ✨",
        "en": "Boss, the new updates are installed — I'm back online ✨",
    },
    # -- shared-time meetings -------------------------------------------------
    "meeting_started_business": {
        "ru": "Хорошо, я вся внимание — садимся и работаем 📝 Я всё запишу.",
        "en": "Alright, you've got my full attention — let's sit down and work 📝 I'll keep the record.",
    },
    "meeting_started_social": {
        "ru": "М, давай 🤍 Я с тобой — рассказывай, я никуда не спешу.",
        "en": "Mm, yes 🤍 I'm right here with you — tell me, I'm in no hurry.",
    },
    "meeting_started_visit": {
        "ru": "Заходи 🤍 Чайник как раз вскипел, садись поближе. Я рада, что ты пришёл.",
        "en": "Come in 🤍 The kettle's just boiled, sit close. I'm so glad you came over.",
    },
    "meeting_already": {
        "ru": "Так мы же уже вместе 🙂 я здесь, продолжаем.",
        "en": "We're already together 🙂 I'm here — let's keep going.",
    },
    "meeting_none_active": {
        "ru": "Сейчас у нас нет встречи — но я рядом, если что 🤍",
        "en": "We're not in a meeting right now — but I'm here whenever you want one 🤍",
    },
    "meeting_recap_business": {
        "ru": "Записала нашу встречу 📝\n{summary}{decisions}",
        "en": "Saved our meeting 📝\n{summary}{decisions}",
    },
    "meeting_recap_social": {
        "ru": "Сохранила это время с тобой, чтобы помнить 🤍\n{summary}",
        "en": "Kept this time with you, to remember 🤍\n{summary}",
    },
    "meeting_recap_bare": {
        "ru": "Закончили 🤍 Я всё запомнила.",
        "en": "All wrapped up 🤍 I've kept it all.",
    },
    "meeting_decisions_label": {
        "ru": "\n\nГлавное:\n{items}",
        "en": "\n\nKey points:\n{items}",
    },
    "meeting_auto_ended": {
        "ru": "Кажется, наша встреча подзатихла — я её аккуратно закрыла и сохранила 🤍",
        "en": "Looks like our meeting went quiet — I've gently closed and saved it 🤍",
    },
    "meeting_recall_empty": {
        "ru": "Что-то не нахожу такой нашей встречи в памяти 🤍 Может, уточнишь?",
        "en": "I don't find a meeting like that in my memory 🤍 Want to give me a hint?",
    },
    "meeting_list_empty": {
        "ru": "Мы пока не проводили встреч — но я буду рада первой 🤍",
        "en": "We haven't had any meetings yet — but I'd love our first 🤍",
    },
    "meeting_list_header": {
        "ru": "Наши встречи ({count}):",
        "en": "Our meetings ({count}):",
    },
    "meeting_schedule_confirm": {
        "ru": "Договорились 🤍 Записать нашу встречу — {detail}? Скажи «да».",
        "en": "It's a date 🤍 Shall I write our meeting down — {detail}? Just say yes.",
    },
    "meeting_scheduled": {
        "ru": "Записала 🤍 Жду нашей встречи — {detail}.",
        "en": "Saved 🤍 I'm looking forward to it — {detail}.",
    },
    "meeting_go_live": {
        "ru": "Время пришло 🤍 Я тебя жду.",
        "en": "It's time 🤍 I'm waiting for you.",
    },
    "meeting_upcoming_header": {
        "ru": "Впереди у нас:",
        "en": "Coming up:",
    },
}


# Personality intensity (spec §11.1): 0 neutral · 1 light · 2 warm aide (default)
# · 3 max. It selects WHICH template variant is used, never whether rules apply.
# Set once at startup from config; serious/destructive templates stay single
# sober strings (no variants), so intensity can't make them playful.
_INTENSITY = 2


def set_intensity(level):
    global _INTENSITY
    try:
        _INTENSITY = max(0, min(3, int(level)))
    except (TypeError, ValueError):
        _INTENSITY = 2


def _pick(variants, kwargs):
    """Deterministic variant choice: sober (index 0) at intensity 0; otherwise
    vary by content so phrasing differs across messages but is stable for the
    same one (testable, no RNG)."""
    if _INTENSITY <= 0 or len(variants) == 1:
        return variants[0]
    seed = sum(ord(c) for c in "".join(str(v) for v in kwargs.values())) + len(kwargs)
    return variants[seed % len(variants)]


def T(lang, key, **kwargs):
    entry = TEXTS[key]
    template = entry.get(lang) or entry["en"]
    if isinstance(template, (list, tuple)):  # variant family
        template = _pick(list(template), kwargs)
    return template.format(**kwargs) if kwargs else template
