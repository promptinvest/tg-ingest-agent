#!/usr/bin/env python3
"""Bilingual (ru/en) user-facing templates — Cara's transactional voice.

Every fixed/system reply comes from here (suggestions, reminder drafts,
confirmations, errors): a scoped router can be sweet-talked, a template cannot.
Free-form conversation and grounded answers are LLM-generated elsewhere
(converse.py / hermes.py) under their own guardrails; templates carry the
deterministic side of her voice. Warm and personal, but structurally fixed.
"""

TEXTS = {
    "start": {
        "ru": ("Привет, {name}! Я Кара — твой личный ассистент 🤗\n"
               "Пересылай мне посты, пиши или наговаривай: сохраню и разложу по полочкам, "
               "напомню о важном, добавлю в календарь, посчитаю расходы на AI. "
               "Категории предлагаю сама — ты только подтверждаешь."),
        "en": ("Hi {name}! I'm Cara — your personal assistant 🤗\n"
               "Forward me posts, write or speak: I'll save and organize things, remind you "
               "of what matters, add events to your calendar, track AI spend. "
               "I suggest categories myself — you just confirm."),
    },
    # NB: the smalltalk_* / persona_* / out_of_scope reply templates were REMOVED (2026-07-02):
    # every one of those router actions dispatches to `_dispatch_default → do_converse` (warm
    # free-form Cara), so they were never rendered — dead templates that also gave false test
    # coverage. Cara's human-emulation + "who are you" behavior lives in `converse.CHARACTER`
    # (the live prompt); `out_of_scope` requests get a warm converse reply, not a bounded card.
    "clarify": {
        "ru": "Хм, не совсем уловила 🤔 Скажи чуть конкретнее — и я подхвачу.",
        "en": "Hmm, I didn't quite catch that 🤔 Tell me a touch more and I've got it.",
    },
    # Sent AFTER the note commit when the reminder draft can't take the single
    # pending slot (another confirmation is mid-flight) — never stacks pendings.
    "capture_reminder_slot_busy": {
        "ru": ("Заметку сохранила ✔️ А напоминание предложу, как закончим с текущим "
               "вопросом — сначала ответь на него, пожалуйста 🙂"),
        "en": ("The note is saved ✔️ I'll offer the reminder once we finish the "
               "question that's already open — answer that one first, please 🙂"),
    },
    "overview_notes": {
        "ru": ("🗂 Заметки: {active} активных · {inbox} во входящих · "
               "{due} на пересмотр · {archived} в архиве"),
        "en": ("🗂 Notes: {active} active · {inbox} in the inbox · "
               "{due} due for review · {archived} archived"),
    },
    # -- note review + resurfacing (suggestion-only) --------------------------
    "note_review_header": {
        "ru": "🔎 Нашла {n} — стоит принять решение:",
        "en": "🔎 Found {n} worth a quick decision:",
    },
    "note_review_footer": {
        "ru": ("Можно так: «второе в архив», «оставь первое», «пересмотрю #N через "
               "месяц» — или просто открой #N."),
        "en": ("You can say: \"archive the second\", \"keep the first\", "
               "\"review #N next month\" — or just open #N."),
    },
    "note_review_empty": {
        "ru": "Сейчас пересматривать нечего — всё разобрано 🌿",
        "en": "Nothing needs a review right now — all sorted 🌿",
    },
    # NB: no final verbs (action_truth catalogue guard) — an invitation, no state change.
    "nudge_note_review": {
        "ru": "Нашла {n} сохранёнки(ок), по которым стоит принять решение — показать?",
        "en": "I've got {n} note(s) worth a quick decision — want to see them?",
    },
    "related_note_hint": {
        "ru": "К слову, у тебя есть ещё #{row_id} ({category}) по этой теме — открыть?",
        "en": "By the way, you also have #{row_id} ({category}) on this — open it?",
    },
    "archive_empty": {
        "ru": "Архив пуст — ничего не убирала.",
        "en": "The archive is empty — nothing has been put away.",
    },
    # -- note lifecycle (reversible triage; never deletes) --------------------
    "note_archived": {
        "ru": "Убрала #{row_id} в архив 🗂 Из поиска она не пропадёт; вернуть — «восстанови #{row_id}».",
        "en": "Note #{row_id} is in the archive now 🗂 Still searchable; say \"restore #{row_id}\" to bring it back.",
    },
    "note_archived_multi": {
        "ru": "Убрала в архив {n} заметок 🗂 Вернуть любую — «восстанови #N».",
        "en": "Moved {n} notes to the archive 🗂 Say \"restore #N\" to bring any back.",
    },
    "note_archive_confirm_multi": {
        "ru": "Убрать в архив {n} заметок ({ids})? Это обратимо — скажи «да» или «нет».",
        "en": "Archive {n} notes ({ids})? It's reversible — say \"yes\" or \"no\".",
    },
    "note_restored": {
        "ru": "Вернула #{row_id} из архива — снова в активных ✨",
        "en": "Note #{row_id} is back from the archive — active again ✨",
    },
    "note_kept": {
        "ru": "Хорошо, #{row_id} остаётся в активных 👌",
        "en": "Okay, #{row_id} stays active 👌",
    },
    "note_purpose_set": {
        "ru": "Пометила #{row_id} как «{purpose}».",
        "en": "Marked #{row_id} as \"{purpose}\".",
    },
    "note_review_set": {
        "ru": "Пометила #{row_id} на пересмотр — {when_local}. Будильника не будет: "
              "она просто всплывёт в обзоре заметок.",
        "en": "Flagged #{row_id} for review around {when_local}. No alarm — "
              "it'll surface in the notes review.",
    },
    "note_temporary_set": {
        "ru": "#{row_id} теперь временная — примерно до {when_local}. Сама ничего "
              "не удалю, просто предложу решить её судьбу.",
        "en": "#{row_id} is temporary now — until about {when_local}. I won't "
              "delete anything myself, just nudge you to decide.",
    },
    "note_lifecycle_na": {
        "ru": "#{row_id} — это запись журнала, у неё нет статусов заметок.",
        "en": "#{row_id} is a journal entry — note lifecycle doesn't apply to it.",
    },
    # NB: no final verbs (action_truth catalogue guard) — a failure, nothing stored.
    "album_failed": {
        "ru": "Не получилось сохранить этот альбом 😔 Перешли его ещё раз, пожалуйста.",
        "en": "I couldn't save that album 😔 Please forward it again.",
    },
    # His OWN album — «перешли ещё раз» would be wrong copy for something he sent
    # himself. Same honesty: the parts are dead-lettered, so silence would mean he
    # never learns they were dropped.
    "own_album_failed": {
        "ru": "Не получилось разобрать твой альбом 😔 Отправь его ещё раз, пожалуйста.",
        "en": "I couldn't handle your album 😔 Please send it again.",
    },
    # NB: no final verbs — the update dead-lettered, nothing was processed.
    "update_dead_letter": {
        "ru": "Не смогла обработать это сообщение 😔 Оно лежит во входящих сбоях — "
              "пришли его ещё раз, пожалуйста.",
        "en": "I couldn't process that message 😔 It's sitting in the failed inbox — "
              "please send it again.",
    },
    # NB: no final verbs here (action_truth catalogue guard) — it's a decline,
    # nothing was stored.
    "own_photo_not_stored": {
        "ru": ("Твои собственные фото я в заметки не складываю — это наш разговор 🙂 "
               "Если нужно сохранить суть, пришли её текстом или файлом, "
               "либо перешли пост — тогда запишу."),
        "en": ("I don't file your own photos as notes — that's just us talking 🙂 "
               "If you want to keep the gist, send it as text or a file, "
               "or forward the post — I'll write it down."),
    },
    # Same decline, for a MIXED own album (photos + a real document): the
    # picture parts are dropped, so he is told which half is missing instead of
    # reading «фото: 0» on the card and guessing.
    # NB: it says only what is NOT kept. The earlier wording («в заметку пойдут
    # только файлы») was a claim about what the note WILL contain, sent before
    # the suggestion card he still has to confirm — it passed action_truth only
    # because «пойдут» is absent from FINAL_VERBS, not because it made no
    # forward claim. The counts card does the claiming.
    "own_photo_not_stored_partial": {
        "ru": ("Свои фото ({n}) я в заметки не складываю — они остаются здесь, "
               "в разговоре 🙂"),
        "en": ("I don't file your own photos ({n}) as notes — they stay here "
               "in the chat 🙂"),
    },
    # -- media capture: photos of movies/books -> confirmed catalog notes ------
    # The card is PRE-confirmation (state=waiting_confirmation): no final verbs —
    # «Сохранить?» is an offer, nothing is stored until his yes.
    "media_card_header": {
        "ru": "📸 Вот что я вижу на фото ({n}):",
        "en": "📸 Here's what I can see in the photo ({n}):",
    },
    "media_kind_movie": {"ru": "фильм", "en": "movie"},
    "media_kind_book": {"ru": "книга", "en": "book"},
    # Provenance label for visible free context read OFF the photo.
    "media_from_photo": {"ru": "на фото: {comment}", "en": "in the photo: {comment}"},
    "media_aliases": {
        "ru": "другое название: {aliases}",
        "en": "alternate title: {aliases}",
    },
    "media_card_footer": {
        "ru": ("Сохранить в каталог? Если что-то не так — ответь, например: "
               "«№2 — книга, не фильм» или «убери №3» 🙂"),
        "en": ("Save these to the catalog? If something's off, reply e.g. "
               "\"#2 is a book, not a movie\" or \"remove #3\" 🙂"),
    },
    # Footer variant for when ANOTHER confirmation holds the single pending
    # slot: a text reply would resolve against THAT pending, so this one must
    # not promise reply-corrections — the buttons (stash-backed) always work.
    "media_card_footer_buttons": {
        "ru": "Сохранить в каталог? Жми кнопку под карточкой 🙂",
        "en": "Save these to the catalog? Use the buttons below 🙂",
    },
    "media_card_cap_note": {
        "ru": "Разобрала первые {cap} фото из {total} — больше за один раз не беру.",
        "en": "I went through the first {cap} of {total} photos — that's my per-batch cap.",
    },
    # States the STORAGE consequence, not a display nicety — the surplus is
    # dropped from the staged set (confirm covers exactly what is shown). The
    # wording is count-free on purpose: it stays true after «убери №3» re-shows
    # the card with fewer entries.
    "media_card_truncated": {
        "ru": "Разобрала больше, чем влезает в карточку, — сохраню только то, что показано здесь.",
        "en": "I read more than fits the card — I'll only save what's shown here.",
    },
    # A partially unread album is DISCLOSED — the card must never quietly imply
    # it covers photos she couldn't read.
    "media_card_photo_unread": {
        "ru": "Не смогла прочитать {n} фото — в карточке только то, что разобрала.",
        "en": "I couldn't read {n} of the photos — the card has only what I could make out.",
    },
    # Media captions outside the small recognized kind/identify contract are not
    # routed while the card is offered, but must not vanish silently either.
    "media_card_caption_note": {
        "ru": ("Подпись «{caption}» вижу, но как команду её тут не выполняла — "
               "если что-то нужно, напиши отдельным сообщением."),
        "en": ("I can see the caption \"{caption}\", but I didn't run it as a "
               "command — if you need something, send it as its own message."),
    },
    "media_card_identified": {
        "ru": "Подобрала наиболее вероятное совпадение по фото и видимому контексту.",
        "en": "I matched the most likely work using the photo and its visible context.",
    },
    # A caption that NAMED the kind was acted on, not merely noticed: she says
    # what she DID with it (2026-07-28 — the old note claimed the caption was
    # not executed while it was in fact being used as a hint).
    # NB: no final verbs — the card is still an OFFER (state waiting_confirmation),
    # so she says how she is READING the photo, never that she filed anything.
    # NB2: {kind} is the CANONICAL kind word, not his caption («Кино», «Найди
    # этот фильм» both arrive as «фильм»), so the wording must not quote it back
    # as if it were what he typed — she never misquotes him (review fix).
    "media_card_kind_forced": {
        "ru": "Ты сказал, что это {kind} — так и считаю, даже если обложка выглядит иначе.",
        "en": "You told me it's a {kind} — so that's how I'm taking it, even if the cover reads otherwise.",
    },
    # The capture lands on a note that already exists: the card must SAY so and
    # name it, because the confirm updates THAT row, not a new one.
    "media_card_merge": {
        "ru": "уже есть в каталоге: {title} (#{row_id}) — обновлю эту запись",
        "en": "already in the catalog: {title} (#{row_id}) — I'll update that entry",
    },
    # The catalog moved under an open card (another confirm, an edit, a delete),
    # so the approved card no longer describes the result: she re-draws it
    # instead of storing something he never saw.
    "media_card_recheck": {
        "ru": ("Пока карточка висела, каталог изменился — вот как выйдет теперь. "
               "Проверь и подтверди ещё раз."),
        "en": ("The catalog changed while this card was open — here's how it comes "
               "out now. Have a look and confirm again."),
    },
    # NB: no final verbs — nothing was stored, and she says what she could NOT do.
    "media_nothing_extracted": {
        "ru": ("Похоже, тут фильм или книга, но названия разобрать не смогла 😔 "
               "Пришли кадр почётче или напиши название текстом."),
        "en": ("Looks like a movie or a book, but I couldn't make out the titles 😔 "
               "Send a clearer shot or just type the title."),
    },
    "media_btn_save": {"ru": "✅ Сохранить", "en": "✅ Save"},
    "media_btn_cancel": {"ru": "✖️ Отмена", "en": "✖️ Cancel"},
    "media_saved": {
        "ru": "Готово, сохранила 🤍\n{lines}",
        "en": "Done, saved 🤍\n{lines}",
    },
    # {title} arrives ALREADY quoted (media.quoted_title) — a photo-read title
    # often carries its own guillemets and must not be wrapped twice.
    "media_line_merged": {
        "ru": "{emoji} {title} — уже есть в {category} (#{row_id}), освежила заметку",
        "en": "{emoji} {title} — already in {category} (#{row_id}), refreshed it",
    },
    "media_correction_unclear": {
        "ru": "Не поняла правку 🤔 Скажи, например: «№2 — книга» или «убери №3».",
        "en": "I didn't catch the correction 🤔 Say e.g. \"#2 is a book\" or \"remove #3\".",
    },
    # He named the kind the card ALREADY shows. Answered deterministically: a
    # re-drawn identical card would eat the turn, and routing a message that is
    # explicitly about the open card would expose a write-capable pending to the
    # model (2026-07-28).
    "media_correction_noop": {
        "ru": "Так и есть — {kind} 🙂 Карточку не меняю.",
        "en": "That's what it says — {kind} 🙂 Leaving the card as is.",
    },
    # -- B2 enrichment: per-field provenance on the card -----------------------
    # «на фото» = visible structured evidence; «(нашла)» = a keyless lookup
    # (OpenLibrary/Wikipedia); «(по памяти)» = model knowledge. A field NO source
    # yielded is listed under «не нашла: …» — honestly missing, never invented.
    "media_field_creator_book": {"ru": "автор: {value}", "en": "author: {value}"},
    "media_field_creator_movie": {"ru": "режиссёр: {value}", "en": "director: {value}"},
    "media_field_year": {"ru": "год: {value}", "en": "year: {value}"},
    "media_field_genre": {"ru": "жанр: {value}", "en": "genre: {value}"},
    "media_src_photo": {"ru": "на фото", "en": "in the photo"},
    "media_src_lookup": {"ru": "нашла", "en": "found"},
    "media_src_model": {"ru": "по памяти", "en": "from memory"},
    # A value the merge target ALREADY holds, which this capture did not find.
    # It needs its own label: «на фото» would claim this photo shows it and
    # «нашла» would claim a lookup ran this turn — neither is true (2026-07-28).
    "media_src_note": {"ru": "уже в записи", "en": "already on file"},
    # A value ANOTHER entry on this same card brings to the note both of them
    # merge into. It is not «уже в записи» (nothing is stored before his yes)
    # and this entry did not find it either (2026-07-28).
    "media_src_card": {"ru": "из этой же карточки", "en": "from this card"},
    "media_fields_missing": {"ru": "не нашла: {fields}", "en": "couldn't find: {fields}"},
    # Field names as they read inside the missing note (RU accusative).
    "media_fname_creator_book": {"ru": "автора", "en": "author"},
    "media_fname_creator_movie": {"ru": "режиссёра", "en": "director"},
    "media_fname_year": {"ru": "год", "en": "year"},
    "media_fname_genre": {"ru": "жанр", "en": "genre"},
    # -- category md export («дай md по Movies» — works for ANY category) ------
    "export_category_which": {
        "ru": "Какую категорию выгрузить? Сейчас есть: {names}",
        "en": "Which category should I export? Right now: {names}",
    },
    "export_category_unknown": {
        "ru": ("Категории «{name}» у меня нет 🤔 Назови точное имя — "
               "или скажи «категории», покажу список."),
        "en": ("I don't have a “{name}” category 🤔 Give me the exact name — "
               "or say “categories” and I'll list them."),
    },
    "export_category_empty": {
        "ru": "В категории «{category}» пока пусто — выгружать нечего.",
        "en": "The “{category}” category is empty — nothing to export yet.",
    },
    # Variant family (pre-confirmation, state=suggested → no final verbs).
    "suggestion": {
        "ru": [
            "Я бы отнесла это к «{category}» 💡\nКоротко: {summary}\n{counts}\n"
            "Согласен? Ответь или нажми кнопку — либо назови свою категорию.",
            "Поймала, босс. Похоже на «{category}» 💡\nКоротко: {summary}\n{counts}\n"
            "Подтвердить? Или назови свою категорию.",
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
    "category_correction_needed": {
        "ru": "Поняла — эту категорию не подтверждаю. Напиши «Категория — …», и я исправлю.",
        "en": "Got it — I won't confirm that category. Send “Category — …” and I'll correct it.",
    },
    "duplicate": {
        "ru": "Это у нас уже есть — #{original_id} ({detail}).",
        "en": "We already have this one — #{original_id} ({detail}).",
    },
    "dup_confirmed": {"ru": "категория: {category}", "en": "category: {category}"},
    "dup_suggested": {
        "ru": "я предложила «{category}», ждёт твоего слова",
        "en": "I suggested \"{category}\", awaiting your word",
    },
    "dup_pending": {"ru": "ещё разбираюсь", "en": "still working on it"},
    "stored_retry": {
        "ru": "Сохранила #{row_id}, но разобрать с первого раза не вышло — попробую ещё раз чуть позже 🙏",
        "en": "Saved #{row_id}, but I couldn't sort it on the first try — I'll retry a bit later 🙏",
    },
    # Fix 6: truthful failure copy. Cara does NOT keep the voice file and has no
    # STT retry queue, so she must not claim "saved / I'll retry" — she asks to
    # resend. (state=failed_final, no false final verbs.)
    "stt_failed": {
        "ru": [
            "Не расслышала голосовое 😔 Пришли ещё раз или текстом, пожалуйста.",
            "Не разобрала голосовое в этот раз, босс. Повтори голосом или текстом?",
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
        "ru": "⏰ Напомнить: {title}\nКогда: {when_local} (твоё время)\nПовтор: {recurrence}\nСтавлю?",
        "en": "⏰ Remind you: {title}\nWhen: {when_local} (your time)\nRepeat: {recurrence}\nShall I?",
    },
    "reminder_set": {
        "ru": "Поставила! #{rid}: {title} — {when_local} 👌",
        "en": "Set! #{rid}: {title} — {when_local} 👌",
    },
    "reminder_fired": {
        "ru": "⏰ {name}, напоминаю: {title}\nОтветь «готово» — или «через 30 минут», если отложить.",
        "en": "⏰ {name}, reminder: {title}\nReply \"done\" — or \"in 30 minutes\" to snooze.",
    },
    "reminder_done": {"ru": "Отлично, закрыла ✅", "en": "Great, closed ✅"},
    "reminder_skipped": {
        "ru": "Хорошо, на сегодня пропускаем 👌",
        "en": "Okay, skipping it for today 👌",
    },
    "reminder_snoozed": {
        "ru": "Хорошо, напомню снова в {when_local} 😉",
        "en": "Okay, I'll nudge you again at {when_local} 😉",
    },
    "reminder_list_empty": {
        "ru": "Активных напоминаний нет — всё спокойно 🌿",
        "en": "No active reminders — all clear 🌿",
    },
    "reminder_list_header": {"ru": "Твои напоминания:", "en": "Your reminders:"},
    "reminder_mark_fired": {
        "ru": "сработало, ждёт «готово»",
        "en": "fired, awaiting \"done\"",
    },
    "reminder_mark_overdue": {"ru": "просрочено", "en": "overdue"},
    "reminder_mark_rescheduled": {"ru": "перенесено", "en": "rescheduled"},
    "reminders_rescheduled_multi": {
        "ru": "Готово 🔄 перенесла {n} напоминания на {when_local}.",
        "en": "Done 🔄 moved {n} reminders to {when_local}.",
    },
    "reminder_cancelled": {
        "ru": "Отменила #{rid}: {title}.",
        "en": "Cancelled #{rid}: {title}.",
    },
    "reminder_not_found": {
        "ru": "Хм, не нашла такого напоминания 🤔",
        "en": "Hmm, I couldn't find that reminder 🤔",
    },
    "reminder_already_closed": {
        "ru": "Это напоминание уже закрыто — трогать его не буду. Скажи, если нужно новое 🙂",
        "en": "That reminder is already closed — I won't touch it. Say the word if you want a new one 🙂",
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
               "Не волнуйся — я всё сохраняю и обработаю, как только бюджет обновится."),
        "en": ("⛔ The AI budget for {period} is used up (${spent:.2f} of ${limit:.2f}). "
               "No worries — I keep saving everything and will catch up once it resets."),
    },
    "period_day": {"ru": "сегодня", "en": "today"},
    "period_week": {"ru": "неделю", "en": "this week"},
    "period_month": {"ru": "месяц", "en": "month"},
    "boss_profile_header": {
        "ru": "Босс, вот что я о тебе знаю — отдельно по уверенности.",
        "en": "Boss, here's what I know about you — split by confidence.",
    },
    "boss_confirmed": {"ru": "Подтверждено:", "en": "Confirmed:"},
    "boss_inferred": {"ru": "Похоже, но ещё не подтверждено:", "en": "Inferred, not confirmed yet:"},
    "boss_edit_hint": {
        "ru": "Можно сказать: «забудь #id», «подтверди #id» или «запомни про меня …».",
        "en": "You can say: \"forget #id\", \"confirm #id\", or \"remember about me …\".",
    },
    "boss_remembered": {"ru": "Запомнила про тебя: {value} 📝", "en": "Remembered about you: {value} 📝"},
    "boss_sensitive_confirm": {
        "ru": ("Это похоже на личное ({s}). Сохранить в профиль? "
               "Я буду держать это закрытым и не выгружать без отдельной просьбы. (да/нет)"),
        "en": ("This looks personal ({s}). Save it to your profile? "
               "I'll keep it private and won't export it without an explicit request. (yes/no)"),
    },
    "boss_forgotten": {"ru": "Забыла: {value} 🙈", "en": "Forgotten: {value} 🙈"},
    "boss_confirmed_ok": {"ru": "Подтвердила: {value} ✅", "en": "Confirmed: {value} ✅"},
    "boss_not_found": {"ru": "Не нашла такой записи о тебе.", "en": "I couldn't find that about you."},
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
    "memory_candidate_kept": {"ru": "Запомнила ✅", "en": "Remembered ✅"},
    "memory_candidate_skipped": {"ru": "Пропустила.", "en": "Skipped."},
    "mc_remember": {"ru": "✅ Запомнить", "en": "✅ Remember"},
    "mc_skip": {"ru": "✖️ Пропустить", "en": "✖️ Skip"},
    "working_history_header": {
        "ru": "Босс, вот как я тебе помогала за {days} дн.:",
        "en": "Boss, here's how I've helped you over {days} days:",
    },
    "working_history_moments": {"ru": "Заметные моменты:", "en": "Notable moments:"},
    "working_history_empty": {
        "ru": "Мы только начали работать вместе — пока истории мало 😊",
        "en": "We've only just started working together — not much history yet 😊",
    },
    "memory_empty": {
        "ru": "Я пока только знакомлюсь с тобой — запомнить ничего не успела 😊",
        "en": "I'm still getting to know you — nothing remembered yet 😊",
    },
    "memory_header": {"ru": "Вот что я о тебе помню:", "en": "Here's what I remember about you:"},
    "remember_saved": {"ru": "Запомнила: {value} 📝", "en": "Got it, remembered: {value} 📝"},
    "forgotten": {"ru": "Забыла: {value} 🙈", "en": "Forgotten: {value} 🙈"},
    "forget_not_found": {
        "ru": "Не нашла такой записи у себя в памяти.",
        "en": "I couldn't find that in my memory.",
    },
    "habit_proposal": {
        "ru": ("Я заметила: последние {n} постов из «{source}» ты относишь к «{category}». "
               "Давай я буду подтверждать их сама?"),
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
        "ru": "Категорий пока нет — они появятся, когда ты подтвердишь первые предложения 🌱",
        "en": "No categories yet — they'll grow as you confirm my first suggestions 🌱",
    },
    "categories_header": {
        "ru": "Наши категории (подтверждённых сообщений):",
        "en": "Our categories (confirmed messages):",
    },
    "stats_empty": {"ru": "Пока ничего не сохраняли.", "en": "Nothing saved yet."},
    "capabilities": {
        "ru": ("Вот чем я могу помочь 💛\n"
               "• Сохранять и раскладывать сообщения — пересылай посты, фото, ссылки; "
               "предложу категорию и краткое содержание, ты подтверждаешь\n"
               "• Напоминания — «напомни завтра в 10 позвонить в банк», разово или регулярно\n"
               "• Календарь — «добавь в календарь...» (пришлю .ics или запишу в Google Calendar)\n"
               "• Расходы на AI — «сколько потратили за месяц?», слежу за бюджетом\n"
               "• Память — «запомни: ...», «что ты обо мне знаешь?», «забудь...»\n"
               "• Обзор — «что у тебя есть?», «покажи сохранённое про X», «что в категории Y?»\n"
               "Пиши или говори голосом — по-русски или по-английски."),
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
    "items_filter_category": {"ru": " (категория: {category})", "en": " (category: {category})"},
    "items_filter_query": {"ru": " (по запросу: {query})", "en": " (matching: {query})"},
    "notes_page_header": {
        "ru": "🗂 Заметки{filter} — {start}–{end} из {total}",
        "en": "🗂 Notes{filter} — {start}–{end} of {total}",
    },
    "page_prev": {"ru": "◀ Назад", "en": "◀ Back"},
    "page_next": {"ru": "Вперёд ▶", "en": "Next ▶"},
    "list_view_stale": {
        "ru": "Этот список устарел — открой его заново 🙂",
        "en": "This list is stale — open it again 🙂",
    },
    "items_empty": {
        "ru": "По этому запросу ничего не нашла 🤷‍♀️",
        "en": "I found nothing for that 🤷‍♀️",
    },
    "no_media": {
        "ru": "У #{row_id} нет сохранённых фото.",
        "en": "#{row_id} has no stored photos.",
    },
    "read_media_none": {
        "ru": "Не вижу пересланного голосового или файла, чтобы разобрать. Перешли — и я гляну, что внутри.",
        "en": "I don't see a forwarded voice note or file to open. Forward one and I'll tell you what's inside.",
    },
    "read_media_none_note": {
        "ru": "У #{row_id} нет голосового или файла, который я могу открыть.",
        "en": "#{row_id} has no voice note or file I can open.",
    },
    # The router garbled which note he named («первая», not a number): ask,
    # never read the newest UNRELATED file as if it were the answer.
    "read_media_which": {
        "ru": "Не поняла, из какой именно записи открыть вложение 🤔 Назови её номер — «#N».",
        "en": "I'm not sure which note's attachment you mean 🤔 Give me its number — \"#N\".",
    },
    "read_media_fail": {
        "ru": "Не получилось открыть это — что-то пошло не так при загрузке. Попробуй ещё раз?",
        "en": "Couldn't open that — something went wrong fetching it. Try again?",
    },
    "read_media_unsupported": {
        "ru": "«{name}» я не умею прочитать (это не голос, не PDF и не текст). Могу переслать тебе сам файл.",
        "en": "I can't read \"{name}\" (it's not a voice note, PDF, or text). I can send you the file itself.",
    },
    "read_media_empty": {
        "ru": "Открыла «{name}», но содержимого не разобрать (пусто или не распознаётся).",
        "en": "I opened \"{name}\" but couldn't make out any content (empty or unreadable).",
    },
    "read_media_result": {
        "ru": "📄 Вот что в «{name}»:\n\n{content}",
        "en": "📄 Here's what's in \"{name}\":\n\n{content}",
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
    # NB: no final verbs (action_truth catalogue guard) — the page was read but
    # nothing landed in the inbox.
    "fetch_store_failed": {
        "ru": "Страницу прочитала, но во входящие она не легла 😔 Попробуй ещё раз, пожалуйста.",
        "en": "I read the page, but it didn't make it into the inbox 😔 Please try again.",
    },
    "fetch_disabled": {
        "ru": "Чтение ссылок сейчас отключено.",
        "en": "Link reading is currently disabled.",
    },
    "fetch_no_url": {
        "ru": "Пришли ссылку, которую нужно прочитать 🙂",
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
               "Сохраню: твои настройки и историю расходов AI.\n"
               "Если уверен — пришли ровно эту фразу:\n«{phrase}»"),
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
    "purge_phrase_journal": {"ru": "да, очистить дневник {category}",
                             "en": "yes, clear the {category} journal"},
    "calendar_added": {
        "ru": "Записала в Google Calendar: {title} 📅\n{link}",
        "en": "Added to Google Calendar: {title} 📅\n{link}",
    },
    "calendar_ics": {
        "ru": ("Google Calendar пока не подключён — вот файл .ics: открой его, "
               "и «{title}» появится в твоём календаре 📅"),
        "en": ("Google Calendar isn't connected yet — here's an .ics file: open it "
               "and \"{title}\" lands in your calendar 📅"),
    },
    "calendar_failed": {
        "ru": "Не получилось с календарём: {error}",
        "en": "Calendar trouble: {error}",
    },
    "calendar_not_found": {
        "ru": "Не поняла, какое событие добавить — назови напоминание или время 🤔",
        "en": "Not sure which event to add — name a reminder or give me a time 🤔",
    },
    "delete_confirm": {
        "ru": "Удалить #{row_id} [{category}] «{snippet}»? Это насовсем — скажи «да», и я удалю.",
        "en": "Delete #{row_id} [{category}] \"{snippet}\"? This is permanent — say \"yes\" and I'll remove it.",
    },
    "deleted": {
        "ru": "Удалила #{row_id} — и записи, и файлы 🗑",
        "en": "Deleted #{row_id} — records and files 🗑",
    },
    "delete_confirm_multi": {
        "ru": "Удалить {n} записей ({ids})? Это насовсем — скажи «да», и я удалю.",
        "en": "Delete {n} items ({ids})? This is permanent — say \"yes\" and I'll remove them.",
    },
    "deleted_multi": {
        "ru": "Удалила {n} записей — и тексты, и файлы 🗑",
        "en": "Deleted {n} items — text and files 🗑",
    },
    "llm_error": {
        "ru": "Ой, у меня тут заминка — не получилось ответить с первого раза 😔 Повтори чуть позже, я ничего не потеряла.",
        "en": "Oops, I hit a little snag and couldn't answer just now 😔 Try me again in a bit — nothing is lost.",
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
    "issue_kind_correction": {
        "ru": "замечания, по которым я скорректировалась",
        "en": "corrections learned from your feedback",
    },
    "issue_kind_correction_unresolved": {
        "ru": "повторяющиеся замечания, которым нужна правка кода",
        "en": "recurring corrections that need a code fix",
    },
    "issue_kind_converse_action_claim": {
        "ru": "безопасно заблокированные ложные подтверждения действий",
        "en": "safely blocked false action confirmations",
    },
    "issue_kind_converse_artifact_claim": {
        "ru": "заблокированные выдуманные файлы",
        "en": "blocked fabricated file claims",
    },
    "review_weekly_intro": {
        "ru": "📊 {name}, моя еженедельная сводка — как я поработала:",
        "en": "📊 {name}, my weekly check-in — how I did:",
    },
    "review_file_caption": {
        "ru": "Отчёт готов, босс — можно отдать его в VS Code 📎",
        "en": "Report ready, boss — feed it to VS Code 📎",
    },
    "artifact_not_sent": {
        "ru": ("Не буду делать вид, что файл прикреплён: в этом ответе его нет. "
               "Скажи «сделай отчёт файлом» — пришлю настоящий .md."),
        "en": ("I won't pretend a file is attached when it isn't. "
               "Say “export the review as md” and I'll send the real .md."),
    },
    "action_not_done": {
        "ru": ("Я не выполнила это действие: тот ответ был только текстом, состояние "
               "не изменено. Повтори команду конкретно — обработаю её через нужный раздел."),
        "en": ("I didn't perform that action: the previous reply was only text, and no "
               "state changed. Repeat the specific command and I'll route it correctly."),
    },
    "reminder_snooze_past": {
        "ru": ("{time} сегодня уже прошло. Скажи «отложи на завтра в {time}» "
               "или укажи другое будущее время."),
        "en": ("{time} has already passed today. Say “snooze until tomorrow at {time}” "
               "or give me another future time."),
    },
    "review_schedule": {
        "ru": "Наш performance review — еженедельно, в {weekday}. Следующий: {date} в {time}. Хочешь, проведу прямо сейчас?",
        "en": "Our performance review is weekly, on {weekday}. Next one: {date} at {time}. Want me to run it now?",
    },
    # Proactive nudges (suggestion-only; Cara never acts on them herself).
    "nudge_candidates": {
        "ru": "Босс, у меня накопилось {n} предложений в память. Скажешь «обзор памяти» — покажу, и решишь.",
        "en": "Boss, I've got {n} memory suggestions waiting. Say \"memory review\" and you decide.",
    },
    # NB: nudge_unsorted was REMOVED 2026-07-17 — the generic unsorted-pile nudge
    # became the note-review invitation (nudge_note_review); untriaged items
    # surface there as one of the deterministic review reasons.
    "nudge_overdue": {
        "ru": "Просрочено напоминаний: {n}. Перенести их или отметить выполненными?",
        "en": "{n} reminder(s) are overdue. Reschedule them, or mark them done?",
    },
    # Correction handling (auto-applied, but she tells him).
    "correction_learned": {
        "ru": "Поняла, босс — буду так делать. Запомнила: {items} ✍️",
        "en": "Got it, boss — I'll do that from now on. Noted: {items} ✍️",
    },
    # Confirm-FIRST: the rule is a candidate, not yet in force — so no «Запомнила»
    # here (see memory_curator.curate_conversation's learned/proposed split).
    # It NAMES the route instead of asking a bare yes/no: nothing is staged in the
    # pending slot and there are no buttons on this line, so «да» would land on
    # `resolve_pending` and get «нечего подтверждать». The answer path is
    # memory_review, which renders each candidate with its own ✅/✖️.
    "correction_proposed": {
        "ru": ("Поняла, босс. Это личное — сама такое в правила не беру: "
               "положила в предложения, скажи «что ты хочешь запомнить» — "
               "там кнопки. {items} 🤍"),
        "en": ("Got it, boss. This one's personal — I don't turn that into a rule "
               "on my own: it's in my suggestions, say \"what do you want to "
               "remember\" and you'll get the buttons. {items} 🤍"),
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
    "journal_capture_card": {
        "ru": "📔 Добавить в «{category}» — запись за {date}?\n📝 {summary}",
        "en": "📔 Add to «{category}» — an entry for {date}?\n📝 {summary}",
    },
    "journal_edit_slot_busy": {
        "ru": "Сначала закончим текущее подтверждение, босс — потом поправлю запись.",
        "en": "Let's finish the confirmation in flight first, boss — then I'll fix the entry.",
    },
    "journal_edit_prompt": {
        "ru": ("Что поправить, босс? Напиши, как должно быть — например: "
               "«Кому: Вера, за что: помогла с презентацией». Или «отмена»."),
        "en": ("What should I fix, boss? Tell me how it should read — e.g. "
               "\"To: Vera, for: helped with the deck\". Or say \"cancel\"."),
    },
    "journal_stats_header": {
        "ru": "📔 «{category}» — кто чаще всего появляется в записях {period}:",
        "en": "📔 «{category}» — who appears most in the entries {period}:",
    },
    "journal_stats_empty": {
        "ru": "В записях «{category}» пока нет отмеченных людей за этот период.",
        "en": "No people recorded in «{category}» entries for that period yet.",
    },
    "journal_prompt_confirm": {
        "ru": ("Включить приглашение к дневнику «{category}» около {hour}:00? "
               "Не чаще раза в день, тихие часы уважаю. Скажи «да» — включу."),
        "en": ("Turn on a journal invitation for «{category}» around {hour}:00? "
               "At most once a day, quiet hours respected. Say \"yes\" and I'll enable it."),
    },
    "journal_prompt_enabled": {
        "ru": ("Готово — буду предлагать запись в «{category}» около {hour}:00 🙂 "
               "Скажи «не предлагай больше» — выключу."),
        "en": ("Done — I'll suggest a «{category}» entry around {hour}:00 🙂 "
               "Say \"stop prompting\" to turn it off."),
    },
    "journal_prompt_disabled": {
        "ru": "Хорошо — больше не предлагаю записи в «{category}».",
        "en": "Okay — no more «{category}» journal prompts from me.",
    },
    "journal_prompt_go": {
        "ru": "Слушаю 🙂 За что сегодня благодарность, что записать в «{category}»?",
        "en": "Listening 🙂 What should today's «{category}» entry say?",
    },
    "nudge_journal": {
        "ru": ("📔 Хочешь записать что-нибудь в «{category}» за сегодня? "
               "Если не сейчас — просто пропусти."),
        "en": ("📔 Want to add something to «{category}» for today? "
               "If not now — just skip it."),
    },
    "model_down": {
        "ru": "⚠️ Босс, модель «{model}» сейчас недоступна ({reason}). Держусь на запасной, но загляни в доступ к моделям, когда сможешь.",
        "en": "⚠️ Boss, the «{model}» model just became unavailable ({reason}). I'm holding on a backup, but check the model access when you can.",
    },
    "model_down_transient": {
        "ru": "⚠️ Босс, у провайдера модели «{model}» затянулась временная перегрузка ({reason}). Пока держусь на запасной; ничего проверять не нужно.",
        "en": "⚠️ Boss, the provider for «{model}» has a sustained temporary overload ({reason}). I'm using a backup; you don't need to check anything yet.",
    },
    "model_back": {
        "ru": "✓ Модель «{model}» снова на связи, босс.",
        "en": "✓ The «{model}» model is reachable again, boss.",
    },
    # The speech backend is an ON-BOX systemd unit, not a provider: the remedy is
    # `systemctl restart whisper-server`, not a look at the model access. Two
    # variants because the cold whisper-cli fallback exists only when the binary
    # and the model file are both on disk — claiming a backup that isn't there
    # would be a fabricated fact.
    "speech_down": {
        "ru": ("⚠️ Босс, распознавание речи на сервере ({model}) не отвечает ({reason}). "
               "Голосовые пока веду через запасной whisper-cli — медленнее, но работает. "
               "Когда дойдут руки: systemctl restart whisper-server."),
        "en": ("⚠️ Boss, on-box speech recognition ({model}) is not answering ({reason}). "
               "I'm running voice notes through the backup whisper-cli meanwhile — slower, "
               "but it works. When you get a chance: systemctl restart whisper-server."),
    },
    "speech_down_no_fallback": {
        "ru": ("⚠️ Босс, распознавание речи на сервере ({model}) не отвечает ({reason}), "
               "и запасного whisper-cli на боксе нет — голосовые я сейчас разобрать не могу. "
               "Нужен systemctl restart whisper-server."),
        "en": ("⚠️ Boss, on-box speech recognition ({model}) is not answering ({reason}), "
               "and there is no backup whisper-cli on the box — I can't make out voice notes "
               "right now. It needs systemctl restart whisper-server."),
    },
    "speech_back": {
        "ru": "✓ Распознавание речи на сервере ({model}) снова работает, босс.",
        "en": "✓ On-box speech recognition ({model}) is working again, boss.",
    },
    "disk_low": {
        "ru": ("⚠️ Босс, на диске сервера осталось {free} из {total} ({pct}%). "
               "Когда он забьётся, я перестану записывать вообще всё — "
               "посмотри, пожалуйста, что можно почистить."),
        "en": ("⚠️ Boss, the server disk is down to {free} of {total} ({pct}%). "
               "Once it fills I stop being able to save anything at all — "
               "please take a look at what can be cleared."),
    },
    "disk_ok": {
        "ru": "✓ С диском снова порядок, босс — свободно {free} ({pct}%).",
        "en": "✓ Disk space is healthy again, boss — {free} free ({pct}%).",
    },
    "backup_failed": {
        "ru": ("⚠️ Босс, сегодняшний бэкап базы не сделался ({reason}). "
               "Копия вне сервера не обновилась — посмотри, когда сможешь."),
        "en": ("⚠️ Boss, today's database backup did not go through ({reason}). "
               "The off-box copy is stale — please check when you can."),
    },
    # Sent from the disk-full backstop in main(): no DB is reachable at that
    # point, so this is the only thing the process can still say.
    "db_full_fatal": {
        "ru": ("🛑 Босс, на сервере кончилось место — база больше ничего не принимает, "
               "и мне придётся остановиться. Освободи, пожалуйста, диск: пока он полный, "
               "я не смогу ни отвечать, ни вести записи."),
        "en": ("🛑 Boss, the server is out of disk space — the database can't accept "
               "anything and I have to stop. Please free some room: while it's full "
               "I can neither reply nor keep records."),
    },
    # Sent from the inbound-path containment guard when the database keeps
    # refusing writes for a reason that is NOT a full disk (read-only remount,
    # lost permissions, a corrupted file). The process is alive and can still
    # send — but it can neither record nor answer, so it says so instead of
    # looking healthy to systemd while being deaf.
    "db_stalled": {
        "ru": ("🛑 Босс, база перестала принимать записи — твои сообщения доходят, "
               "но я не могу их обработать и не починю это сама. Посмотри, "
               "пожалуйста, сервер: диск и файл базы. Всё, что ты пишешь, "
               "подхвачу, как только она снова заработает."),
        "en": ("🛑 Boss, the database stopped accepting writes — your messages keep "
               "arriving but I can't process any of them, and I can't fix this "
               "myself. Please check the server: the disk and the database file. "
               "Everything you write gets picked up as soon as it works again."),
    },
    "problem_logged": {
        "ru": "Записала в проблемы, босс — разберёмся 📝",
        "en": "Logged it as a problem, boss — we'll sort it out 📝",
    },
    "one_at_a_time": {
        "ru": "Давай по одному, босс — что сделать первым?",
        "en": "Let's do one at a time, boss — which first?",
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
    "categories_merged": {
        "ru": "Объединила «{src}» в «{dst}» — перенесла {n} и убрала дубликат ✅",
        "en": "Merged «{src}» into «{dst}» — moved {n} and removed the duplicate ✅",
    },
    "memory_cleaned": {
        "ru": "Почистила память, босс — объединила {n} повторяющихся записей 🧹",
        "en": "Tidied my memory, boss — merged {n} duplicate item(s) 🧹",
    },
    "memory_clean_none": {
        "ru": "Память и так в порядке, босс — повторов не нашла 🧹",
        "en": "Memory's already tidy, boss — no duplicates to merge 🧹",
    },
    "merge_which": {
        "ru": "Какие категории объединить, босс? Скажи «объедини X в Y».",
        "en": "Which categories should I merge, boss? Say \"merge X into Y\".",
    },
    "category_not_found": {
        "ru": "Не нашла категорию «{category}», босс.",
        "en": "I don't see a category «{category}», boss.",
    },
    "budget_set_done": {
        "ru": "Готово, босс — лимит AI на {period}: ${amount} ✅",
        "en": "Done, boss — AI budget for the {period}: ${amount} ✅",
    },
    "budget_set_off": {
        "ru": "Готово, босс — лимит AI на {period} снят (0 = без ограничения) ✅",
        "en": "Done, boss — the AI cap for the {period} is off (0 = no limit) ✅",
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
    "note_edited": {
        "ru": "Поправила заметку #{row_id} 🤍\n📝 {summary}",
        "en": "Updated note #{row_id} 🤍\n📝 {summary}",
    },
    "note_edit_unclear": {
        "ru": "На что поменять краткое? Напиши новый текст — и я исправлю.",
        "en": "What should the summary say? Give me the new text and I'll fix it.",
    },
    # -- he EDITED the Telegram message a saved note was made from ------------
    # Honest, never silent: the old version is the one she is holding, and the
    # new text is applied only after he says yes. («сохранила» is a completed
    # action here — the note really is saved; see action_truth.TEMPLATE_STATES.)
    "note_edit_offer": {
        "ru": ("Босс, ты поправил то сообщение — а я уже сохранила старую версию. "
               "Обновить заметку #{row_id}?"),
        "en": ("Boss, you edited that message — but I'd already saved the older "
               "version. Update note #{row_id}?"),
    },
    "note_edit_yes": {"ru": "✅ Обновить", "en": "✅ Update"},
    "note_edit_no": {"ru": "✖️ Оставить", "en": "✖️ Keep it"},
    "note_edit_applied": {
        "ru": "Готово — заметка #{row_id} теперь с новым текстом 🤍",
        "en": "Done — note #{row_id} now holds the new text 🤍",
    },
    # An EDITED message whose original turn produced a reminder / remembered
    # fact (no note row): the dialogue record follows his chat, the artifact
    # does NOT — one honest line instead of a silent divergence (2026-07-27).
    # NB: no final verbs (action_truth) — «обновила» describes the record,
    # the artifact wording claims no action.
    "edited_turn_reminder": {
        "ru": ("Текст в нашей переписке обновила 🙂 Напоминание из того сообщения "
               "осталось со старыми деталями — скажи, если его тоже поправить."),
        "en": ("I've updated the text in our chat history 🙂 The reminder from that "
               "message still has the old details — tell me if you want it changed too."),
    },
    "edited_turn_memory": {
        "ru": ("Текст в нашей переписке обновила 🙂 Но у меня в памяти осталась "
               "прежняя версия из того сообщения — скажи, если её тоже поправить."),
        "en": ("I've updated the text in our chat history 🙂 But my memory still holds "
               "the earlier version from that message — tell me if you want it "
               "changed too."),
    },
    "note_edit_kept": {
        "ru": "Хорошо, заметка #{row_id} остаётся как есть.",
        "en": "Okay, note #{row_id} stays as it is.",
    },
    # The ✅ button outlives the pending slot on purpose (the offer must stay
    # answerable) — so an old card carries no visible age, and a tap on it is not
    # consent to text staged days ago. She says so instead of applying it.
    "note_edit_stale": {
        "ru": ("Босс, это предложение уже старое — не буду переписывать заметку "
               "#{row_id} вслепую. Поправь сообщение ещё раз, и я спрошу заново."),
        "en": ("Boss, that offer has gone stale — I won't rewrite note #{row_id} "
               "blind. Edit the message once more and I'll ask again."),
    },
    # The caption changed but the note's text does not come from the caption.
    # Never silent: he sees the 'edited' mark and would otherwise get nothing.
    "note_edit_from_file": {
        "ru": ("Босс, подпись поменялась — но заметку #{row_id} я так не обновлю: "
               "текст заметки с документом я беру из самого файла. Если изменился "
               "он — пришли файл заново."),
        "en": ("Boss, the caption changed — but I can't update note #{row_id} that "
               "way: for a note with a document I take the text from the file "
               "itself. If the file changed, send it again."),
    },
    "note_edit_from_album": {
        "ru": ("Босс, подпись поменялась — но заметку #{row_id} я так не обновлю: "
               "она собрана из всего альбома, а не из одной подписи."),
        "en": ("Boss, the caption changed — but I can't update note #{row_id} that "
               "way: it was built from the whole album, not from one caption."),
    },
    "journal_open_hint": {
        "ru": "\nОткрой любую запись целиком: «покажи #N».",
        "en": "\nOpen any entry in full: \"show #N\".",
    },
    "recall_conversation_empty": {
        "ru": "Не нашла этого в нашей переписке 🤍 Подскажи, когда это было (вечер, утро, какой день) — посмотрю дальше.",
        "en": "I can't find that in our conversation 🤍 Tell me roughly when it was (evening, morning, which day) and I'll look further back.",
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
    # Lazy import avoids a module cycle while making the action-truth check a
    # production invariant instead of a pair of hand-picked unit tests. Check
    # before interpolation: nested values (for example the independently
    # checked `counts` template inside `suggestion`) retain their own lifecycle.
    from action_truth import assert_template_key_allowed
    assert_template_key_allowed(key, template)
    return template.format(**kwargs) if kwargs else template
