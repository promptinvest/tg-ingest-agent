# Cara — Capabilities, Features & Architecture

**Cara** (`@cara_assist_bot`) is a personal, conversational AI assistant that lives
in Telegram and is self-hosted on the **PD‑VPS** (`174.138.108.85`, a DigitalOcean
droplet shared with the PD platform; Pilot‑VPS is a cold standby — migrated off it
2026‑06).
She talks like a warm human, ingests and organizes what her owner ("boss") forwards
her, runs reminders, answers from his own notes, learns from how they work together,
and quietly flags things worth attention — all from **one stdlib‑only Python process**
with **no inbound network ports**.

This document is the complete reference: what she can do, how she behaves, and how
she's built. For the design rationale see [SOLUTION.md](SOLUTION.md); this file is
the exhaustive feature + architecture map.

---

## 1. At a glance

| | |
|---|---|
| **Surface** | Telegram bot, single owner, free‑form Russian/English, text + voice + forwards |
| **Runtime** | one systemd service, stdlib‑only Python 3, long polling (no webhooks/ports) |
| **Inference** | DigitalOcean Gradient (chat `anthropic-claude-haiku-4.5`, default fallback `openai-gpt-oss-20b`, embeddings `BGE‑M3`); STT local `whisper.cpp` |
| **Storage** | SQLite (WAL) + local media dir; optional DO Spaces (dormant) |
| **Persona** | a warm, loyal human assistant persona with her own (fictional) life (the in-person companion/relationship side was split off to Nikki, 2026-07-03); **open and personal by the boss's wish** — shares her inner life and talks frankly about any personal matter, no "professional distance"; never breaks character; matches the boss's language |
| **Safety spine** | owner‑only access · permission manifest · confirm‑before‑state‑change · budget caps · SSRF guard · action‑truth · full tracing |

---

## 2. How she decides what to do (request flow)

```
Telegram update (owner-only: chat AND sender must be on the allowlist)
   │
   ├─ message_reaction → note the boss's reaction (log, learn, surface next chat)
   ├─ callback_query   → inline-button confirmations
   └─ message
        │  own voice NOTE → transcribe (whisper) → treat transcript as the text
        │  forward / photo / document / media / audio·video·voice attachment → CONTENT
        │
        ├─ CONTENT ──────────────► ingest (no router): parse text first; analyze
        │                          images (vision) & PDFs (text); store every other
        │                          file as fetchable; suggest a category to confirm
        │
        └─ free text ───────────► router.py  (closed-world LLM intent, JSON only,
                                   confidence-gated, recent-conversation context)
                                        │
             ┌──────────────────────────┼───────────────────────────────────┐
             ▼                          ▼                                     ▼
        a skill (below)          converse (warm free-form Cara)        confirm/amend/cancel
                                 ← low-confidence falls here, not        (pending actions)
                                   a cold "уточни"
```

- **Owner‑only:** a message/reaction/button is acted on **only** when the chat id
  *and* the sender's user id are both allowlisted. Strangers, and the owner in any
  other chat (e.g. a group), are ignored.
- **State changes are confirmed** conversationally ("да", "нет, лучше крипта",
  "через полчаса") or via inline buttons. Bulk **purge** needs an exact typed phrase.
- **When unsure, she talks** — a low‑confidence read (and the `clarify` route) drops to
  warm `converse` where she answers or asks naturally in «ты», never a cold formal template.
- **Two sides of one person, switched smoothly 24/7 — no commands, no clock gate**
  (`_register_directive`): every message is routed on its own merits, so a personal or
  personal aside dropped into a work thread ("скучаю", "как ты сама?")
  reaches her **warm** side instantly, and a task reaches her **crisp assistant**
  side — she flows between them within one conversation as the **same person, no reset**.
  The business side is **Hermes** (`hermes.py`) — *not* a separate agent/bot/memory, but a
  bounded **domain** (the work actions: reminders/notes/KB/spend/…) plus a distinct
  **businesslike voice** (crisp, structured, factual — no warmth/flirtation bleed, still her
  «ты», never an "I'm an AI assistant" disclaimer) that she governs. One Cara; Hermes is her
  work register. (The KB `ask`, fetched‑page summaries and reviews answer in the Hermes voice.)
  Her *resting* tone is a baseline, not a gate: when you've been **doing business** she
  mobilizes to a working style (any hour) and stays there for `WORK_REGISTER_HOLD_MINUTES`,
  then **eases back**; quiet **work hours** rest professional, quiet **off‑hours** rest
  relaxed and playful. How personal *your* message is **always overrides** the
  baseline. She's given the **real current date/time** so she never invents one.
- **No roleplay narration** — she never writes asterisk
  stage-directions (`*обнимаю*`, `*закрываю глаза*`); feeling is shown in words, emojis and
  reactions (stripped in code). She also **sees your reactions** to her messages and
  lets them shape her next reply — leaning into a warm one, adjusting to a cool one.
- **Reactions** — her *reaction* to a
  message is recognised however the model formats it — `[[react:X]]`, `[[реакция: X]]`,
  `[[X]]`, or a bare emoji on its own first line are all lifted into a real Telegram
  reaction and never shipped as text (format-agnostic, not a per-shape regex). An emoji
  outside Telegram's reaction set is **converted** to the nearest allowed one (🥺→🥰, 💕→❤️,
  😂→🤣) rather than dropped, so the emotion always lands.
- **Her life flavour is varied, not fixated** — life details are sampled per turn (not the
  same fixed slice every time), and the old tea over‑emphasis was rebalanced, so she stops
  repeating the same beat ("a bad joke").
- **Never fabricates a stored fact (guardrail)** — creativity is free in her *voice* and her
  own fictional life, but any fact about the boss (notes, journal, reminders, names, dates,
  counts, spend) must be real. Every `converse` turn is **grounded**: his most relevant saved
  entries are retrieved (embedding match) and handed to the model as FACTS to use verbatim;
  if the answer isn't there she offers to look rather than confabulate. **Exception:** for a
  relationship/emotional message ("что ты ко мне чувствуешь?", "про нас") his saved notes are
  NOT injected — there she answers warmly from the heart, not by reciting facts. Reinforced by an
  absolute rule in her persona prompt.
- **Never fakes an action (truthful boundary)** — in a `converse` turn she does NOT perform
  state changes, so she may never reply with a made‑up «готово / поменяла / поставила /
  перенесла / закрыла». Real saves/reminders/renames/reschedules are done by the skills and
  report the **actual** outcome; if a request lands in chat she says she's on it (so it routes
  to a real action) or, if it's something she genuinely can't do, says so plainly — never a
  fabricated confirmation. (Absolute rule in the persona prompt.)

---

## 3. Capabilities (what you can ask)

### Conversation & personality
- **Free‑form chat** (`converse`): greetings, smalltalk, anything personal/emotional
  or not a concrete task → a warm reply in Cara's own voice (LLM‑generated), in the
  language you wrote in.
- **Human persona, no disclaimers:** she presents as a real person with her own
  (fictional, evolving) private life — interests, friends, plans — and never breaks
  character with "I'm an AI." This is safe because she's reachable only by you.
- **Self‑knowledge** (`self_query`) and **about‑me/persona** answers come through her
  own voice; she never recites architecture or invents technical specifics.
- **Adjust her tone** (`style_update`): "говори теплее", "будь покороче".
- **Reactions (both ways):** she may react to your message with a fitting emoji
  (sparingly), and she *sees* your reactions — a 👍/❤️ is logged as positive, a 👎 as
  a negative signal, and the latest is surfaced into her next reply.
- **Time‑of‑day aware:** she knows your local time and part of day (and her own, if
  her timezone is set different) and adapts ("так поздно?", "доброе утро").

### Inbox: ingest, files, retrieval
- **What gets filed vs talked about:** only **forwards** (content from other channels/
  people) and bare typed notes are auto‑saved as inbox items. Your **own** photos/files
  are **conversation**, not notes — a caption is *context* (she reads + reacts to the
  photo via vision, e.g. "одобряешь мой выбор?" → an opinion in her voice), and an
  explicit "сохрани это" still files it. She also understands what you're **replying to
  or quoting** (TG reply/quote) as context for "this".
- **Ingest forwards/notes:** forwarded posts and typed notes (text, URLs, photos;
  an album = one item) are saved with forward origin, t.me source link, post date.
  A vision LLM suggests a **category** (from your taxonomy), a **summary**, and up to
  5 **key facts** — strictly in the source language. Duplicates are detected.
  A **referential save** ("сохрани заметку про этот фильм") with no subject of its
  own resolves the subject from the recent conversation — so the note captures the
  actual film/topic discussed, not the bare command. If the model's reply won't parse,
  she **never stores raw JSON** as the summary — she salvages the fields, else leaves it
  empty so the note shows its real text. Long note/journal listings are **paginated**,
  not cut off at Telegram's length limit. Gratitude (and any **journal** entry) lands in
  the right journal even when the model writes a singular/variant of its name; a
  referential save with no resolvable subject keeps its real text instead of a blank note.
- **Forwarded‑message rules:** **text is parsed first**; only **images** (vision) and
  **PDFs** (text extraction — pdfminer.six, with a stdlib regex fallback) are analyzed;
  **every other file** (voice, audio, video, documents…) is **stored**, fetchable later
  — not parsed. When the chat model isn't **vision‑capable** (e.g. open‑weight models),
  a forwarded **photo** is handled by a configured **`VISION_MODEL`**
  (`llama-4-maverick` — an open multimodal model that actually describes images on this DO
  tier, where Claude/GPT‑4o vision are 403): it *describes* the image and that is folded into
  the text for categorization. The description is **language‑pinned** (ru/en) and
  **sanity‑checked** (`llm._vision_text_is_garbled`): an open vision model sometimes leaks a
  reply in the **wrong script** (a whole Chinese sentence) or a degenerate stub — that is
  **discarded as if empty** rather than folded in, so Cara never parrots gibberish. With no
  vision model (or a discarded read) she falls back to **text‑only** / a warm "I can see you
  sent a photo but can't quite make it out — what did you want me to see?" — either way a
  photo post never gets stuck and she never invents its contents. Even when he **asks to
  describe a photo but none actually reached her** this turn, she says she doesn't see it and
  asks him to resend — she never fabricates a description from mood or memory. A slow vision/embedding call
  can't sink the reply: every transport fault (including a bare socket **read‑timeout**)
  is wrapped as `LLMError`, so indexing stays best‑effort and the suggestion card is
  still delivered.
- **Files:** any attached document/media is kept by Telegram `file_id`; "покажи файл
  #N" re‑sends it (free, no re‑upload).
- **Browse & detail:** "покажи заметки" (a clean card list), "что в категории crypto",
  "найди про DeepSeek", "детали #2" / "покажи заметку 11" (full card + re‑sends the
  attached photos/files; a bare "заметка N" reference resolves by number regardless of
  phrasing).
- **Note numbers** are **stable** per‑note ids (`messages.note_no`): assigned once when
  a note first becomes visible, **monotonic, never reused**. Deleting a note leaves a
  **permanent gap** (like a GitHub issue number), so "заметка 11" is the same note
  tomorrow and marking a category a journal renumbers nothing. The number is a display
  position distinct from the internal row id, so attachments/embeddings/memory links
  never break. (Owner decision, 2026‑06‑29 — gaps accepted for stability.) **Reminder
  numbers are different** — a contiguous 1…N position in the active list (soonest‑due
  first) that **compacts** as reminders fire/cancel; "#N" in reschedule/cancel/undo
  resolves to that position, and Cara re‑shows the refreshed list after a cancel so a
  captured number never goes stale.
- **Journals (long‑term areas):** mark a category as a journal — "веди Благодарности
  как дневник" / "сделай X журналом" — and it becomes append‑only: each note acks as a
  dated entry ("запись за 18.06, всего N"), "покажи дневник благодарности [за неделю/
  месяц]" — or just a bare **"покажи благодарности"** — replays it as a **day‑grouped
  series** (deterministic `#N • snippet`, never free‑texted by the model). The show handler
  **resolves a loosely‑typed name** (`_match_journal_category`) so an inflection ("благодарности"
  vs the stored "Благодарность") hits the real journal instead of spawning a phantom empty
  one. A "📔 Дневники" digest appears in the weekly review and morning brief, and a "clear
  all notes" purge **spares it**. Turn it back off with "X больше не дневник". One‑time notes
  behave exactly as before.
- **Overview & stats:** "что у тебя есть?" → a digest (counts, reminders, memory,
  spend); per‑status/category **stats** (`stats`) and the **category list**
  (`categories`).
- **Re‑categorize** (`recategorize`): "поменяй категорию #2 на Документы", "переложи
  это в Чеки" (most recent), "переложи всё из crypto в news" (bulk — moves the WHOLE
  set, reporting the real count). Logged as a correction so it feeds learning.
- **Edit a note's summary** (`note_edit`): "исправь заметку #11 на …", "поменяй краткое
  #3 на …" — fixes the LLM‑written summary shown in lists/detail **in place**; the
  original message text (`raw_text`, the KB‑search source) is preserved. Distinct from
  re‑categorize (category) and reminder rename (a reminder's title).
- **Delete / discard / purge:** delete by id/ids/count/query; decline a fresh
  suggestion; bulk purge by scope (all / category / stats / reminders / messages /
  issues) behind a typed phrase.

### Knowledge & answers
- **Ask (KB Q&A)** (`ask`): "когда мой рейс?", "что по плану на сегодня?" → semantic
  retrieval (BGE‑M3) over *your own stored notes*, then a grounded answer in the
  question's language citing `(#id)`; refuses if it isn't in your notes.
- **Fetch a link** (`fetch`): "прочитай https://…" → reads a public page (SSRF‑guarded)
  and ingests its text.

### Time & money
- **Reminders:** natural‑language times (RU/EN), one‑shot / daily / weekly, fired from
  the poll loop (~1 min precision), survive restarts/reboots.
  - **A fired reminder stays open** (visible, still pending) until you explicitly say
    "готово" — she never auto‑closes it on a misread. **Snooze** by minutes, hours, or an
    absolute time ("через полчаса", "отложи на час", "до завтра в 9") **re‑arms the same
    reminder** (keeps its id, recurrence and history — no orphaned new row). The reminder
    **list marks status** — a one‑shot that already fired shows *"⚠️ сработало, ждёт «готово»"*
    and a past‑due one *"⚠️ просрочено"*, so an old reminder never looks like a future one.
  - **She knows her own reminders in conversation.** Asking *about* a reminder — "почему не
    закрыла #1?", "что там с напоминаниями?" — is answered from the **real active list**
    (she explains a fired one is still open until "готово" and offers to close it), **not**
    by searching your notes. (An explicit "закрой #1" cancels it.)
  - **Fires at the time you set — not eaten by quiet hours.** A reminder is an
    **explicit alarm**, so it fires at its scheduled time **even inside quiet hours** (a
    deliberate "22:00 daily" reminder must not be swallowed by a 22:00–08:00 quiet window —
    quiet hours only silences Cara's *proactive* outreach). The **only**
    in‑conversation safety is a brief **~5‑min lull** after your last message
    (`reminder_quiet_after_msg_minutes`) — it fires in the first quiet gap, never
    interrupting an active exchange.
    **And the lull can't defer it forever** (`REMINDER_MAX_DEFER_HOURS`, default 2h,
    implemented 2026‑07‑02): a reminder overdue past the cap fires even mid‑exchange, so a
    long evening of continuous messages never swallows it. **A firing reminder no longer
    clobbers a confirmation in flight** (2026‑07‑02): if you're mid‑way through confirming
    something (a draft reminder, a note suggestion, a purge phrase), the fired reminder
    doesn't replace that pending — your "да" still confirms what you were asked, and the
    fired one stays addressable ("готово", "закрой её"). **The same guard now covers
    suggestions** (2026‑07‑06): a category card — notably one from the background
    pending‑ingest retry sweep — takes the pending slot only when it's free (or already
    a category), so it can't hijack a mid‑flight confirmation either; its inline
    buttons work regardless.
  - **Rescheduling never lands in the past, and re‑arms cleanly.** A **move verb + a time** is
    always a reschedule — even named only by an **ordinal** ("перенеси **первое/второе** на
    12:16", moves the *N‑th* shown one) or **"его/это"** (the one you're dealing with) — it's
    done directly, never bounced to chat and never refused with a made‑up "too close in time"
    limit. A past‑resolved time **rolls forward**; once moved it's **re‑armed** and shown as
    **"🔄 перенесено"** (re‑scheduled) — *not* the "⚠️ сработало" warning. "удали #N" right after
    you've **shown the reminders** cancels that reminder, not a saved note.
  - **Move several at once.** "перенеси **первые две / обе / все** на 17:00" moves every named
    reminder in one go and confirms once ("перенесла N напоминания") — it is **one** reschedule,
    not a "давай по одному" split. (That split used to drop the request on the floor and let her
    *say* she'd moved them while nothing actually changed.)
  - **After deleting any reminder she re-shows the list, re-numbered.** Delete one and Cara
    immediately lists what's left with fresh #1..#N — so a rapid "удали #1", "удали #2" always
    reads off the *current* numbering, never a stale screenshot. (Reminder numbers are positions,
    not IDs: the list compacts when something leaves, so a captured #N goes stale instantly.)
  - **The "ждёт готово" list self‑clears.** A fired one‑shot left unacked **auto‑closes** after
    `reminder_fired_expire_days` so the list never piles up.
  - **Plain-language commands land, whatever the word order.** A close verb naming one
    reminder — "**Азербайджан закрой**" as well as "закрой Азербайджан", "первое закрой",
    "убери третье" — closes it; "**передвинь**/сдвинь" reads the same as "перенеси"; "покажи
    напоминания / покажи просроченные" shows the list. These used to fall through to
    "не поняла" and quietly land in the problem log instead of running.
  - **"Покажи их" right after an overdue nudge** shows the **real list** (exact titles), not a
    free-text retelling that could blank the names out.
  - **"Запиши в проблемы" logs the actual problem.** A bare report captures *what you just
    said*, not the words "запиши в проблемы" echoed back at itself.
  - **Rename** a reminder's title in place ("переименуй #2 в «Иван Доронин»").
  - **Reschedule / undo:** "перенеси напоминание про банк на пятницу" moves it; an
    explicit title that matches nothing active is reported (never silently moves a
    different one); "верни предыдущее время" / "отмени перенос" undoes the last move.
  - **"Это напоминание"** binds to the one you were just dealing with; if it's genuinely
    ambiguous she asks which and **remembers what you wanted** — your "второе" / "#2" /
    "про банк" then completes the move/rename on the right one (never a stray close).
  - **Complete a half‑specified reminder:** "напомни в 17:00" → she asks the subject,
    stitches your answer in, then confirms — the partial isn't lost.
  - **From a note:** "поставь напоминание по заметке N" uses note N's real subject as
    the title (not a literal "Заметка N").
- **Calendar:** "добавь в календарь" → `.ics` file (no setup) or Google Calendar via a
  service account; `auto_calendar` syncs every confirmed reminder.
- **Spend report:** "сколько потратили за месяц?" → totals by skill & model + budget
  status.
- **Budget control:** "подними дневной лимит до $3" / "set the monthly AI budget to
  20" → changes the cap at runtime (stored override, enforced by the gateway).

### Memory & self‑improvement
- **Boss profile:** "что ты знаешь обо мне?" → a warm, deduped summary (confirmed vs
  sensed); "запомни про меня …", "забудь …", "как меня зовут?". Sensitive facts are
  gated.
- **Memory candidates:** she proposes durable memories from evidence; "обзор памяти"
  lists them with confirm/skip buttons. Durable memory only after a yes; benign facts
  learned from chat are stored as correctable "inferred" items — **but a fact that
  contradicts something you already confirmed is proposed for confirmation, not
  silently auto‑stored**. A candidate the consolidation already folded (`merged`/
  `superseded`) is **never re‑proposed** (2026‑07‑02) — the curator's dedup no longer
  churns the same text through propose→fold every pass.
- **Memory provenance** (`memory_why`): "откуда ты это знаешь?" / "почему ты это
  помнишь?" → she cites *how* she learned it, in character ("ты сам мне это сказал",
  "ты меня поправил", "заметила из наших разговоров", with the date).
- **Corrections that stick:** when you correct her behavior she **says** she learned
  it, **applies** it (injected into her prompt), and **reports** it in the review. If
  the same correction recurs she flags it as **needing a code fix** instead of
  pretending to fix it.
- **Working history:** "как ты мне помогала?" → a grounded summary of real actions
  (saves, corrections, reminders, reviews, exports) — never fabricated.
- **Settings memory** (`memory`): "запомни: отвечай по‑английски", "что ты помнишь из
  настроек?" — language, timezone, auto‑calendar, named notes.

### Reporting & ops
- **Weekly performance review:** runs on a fixed schedule (default **Monday 10:00
  local**); "когда следующий review?" tells you the date; "как ты поработала?" runs it
  on demand. Includes a **scorecard** — first‑guess category accuracy, unclear‑request
  count, proactive nudges sent, and memory counts — plus a **📔 Дневники** journal‑
  activity rollup. Markdown exports for VS Code:
  review, self, boss profile, working history, memory candidates, trace summary.
  **Delivered‑or‑retried (2026‑07‑06):** the weekly review and the morning brief mark
  their slot done only **after a successful send** — a transient Telegram failure backs
  off 15 min and retries (up to 3 attempts, then a `sched_send_failed` issue), instead
  of silently skipping the week/day.
- **Daily DB backup (2026‑07‑06):** once per UTC day a durable job (`maintenance`/
  `db_backup`) snapshots `ingest.db` consistently (sqlite3 online‑backup API), keeps the
  newest `BACKUP_KEEP` (7) gzipped copies under `/var/lib/tg-ingest-agent/backups`, and
  copies the snapshot **off‑box** — to the DO Space when `STORAGE_BACKEND=spaces`, else
  as a document to the **fleet notify chat** (Telegram cloud; skipped over ~45 MB). A
  failed transfer makes the job retry; no configured target logs a WARNING (local‑only
  doesn't survive a droplet wipe). `BACKUP_ENABLED=false` disables.
- **Proactive heartbeat:** gentle, suggestion‑only nudges — overdue reminders, memory
  candidates waiting, items needing a category — throttled (≤1 non‑urgent/day),
  quiet‑hours‑aware (22:00–08:00), fully audited; never acts. **A "sent" is recorded only
  on real delivery** (2026‑07‑02), so a transient Telegram error doesn't mark the day's
  nudge delivered and lose it. **A persistent overdue reminder no
  longer starves the other nudges** — an already‑sent‑today hit is skipped, not treated
  as fatal, so a waiting candidate/uncategorized item still gets its turn.
- **Tune her proactivity** (`proactive_prefs`): "пиши только по выходным", "не беспокой
  до 10", "отключи напоминания", "можно почаще" → stored overrides (on/off, days,
  quiet window, frequency) the heartbeat honors.
- **Issues report:** "какие были проблемы на этой неделе?" → a summary of logged
  communication issues (unclear/out‑of‑scope/STT/corrections…).
- **Report a problem** (`report_problem`): "запиши в проблемы" / "добавь в ошибки" logs
  a boss‑reported issue (surfaces in the review) — distinct from the issues report,
  which only *shows* them.
- **One at a time** (`multi_action`): a message bundling two+ distinct commands ("первое
  закрой, второе напомни…") is recognised and she asks to take them one at a time,
  rather than silently misfiring (full multi‑step execution is intentionally out of
  scope for the single‑action router).
- **VPS stats:** "как сервер?" → CPU/mem/disk/uptime + her own footprint.
- **Why did you do that** (`trace_query`): replays the last trace timeline.
- **Deploy notice:** after a new build is installed, a one‑line notice goes to the shared
  **fleet notification bot** (the ops channel the other VPSes post to) — **never into the
  boss's chat with Cara**, so a code install can't clutter the conversation or bleed into
  what she says. Fires once per real code change (quiet on plain reboots); silently skipped
  if the fleet creds aren't configured. Config: `FLEET_NOTIFY_BOT_TOKEN` /
  `FLEET_NOTIFY_CHAT_ID` / `FLEET_NOTIFY_LABEL` (copied from the box's root‑only
  `/etc/codex-auto-update/telegram.env`, since the `tg-ingest` user can't read that file).
- **Model‑health monitor:** every `MODEL_HEALTH_INTERVAL_SECONDS` (default 30 min) she
  checks her models (chat, conversation, vision) are reachable and **messages the boss the
  moment one becomes inaccessible** (e.g. a provider/tier 403) — and again when it
  recovers. **Debounced:** a model must fail `MODEL_HEALTH_CONFIRM_CHECKS` checks in a row
  (default 2) before she says "down", so a transient 429/overload blip that clears by the
  next probe stays silent — and "back" only fires if she actually announced "down". No more
  down/back flapping.

---

## 4. Persona & honesty rules

- Warm, loyal, affectionate‑in‑text — in a **friendly register only** (owner decision
  2026‑07‑06): **no flirtation, no romantic spark, no intimate conversation.** An
  intimate/flirty message still routes to `converse` (routing never censors), but Cara
  herself doesn't pick it up — she deflects gently and kindly (no lectures, no shaming)
  and steers back to friendly ground. Never possessive, clingy, or reproachful. The
  whole romantic/intimate register lives in **Nikki** (split 2026‑07‑03).
- **Fully emulates a human and never breaks character** — she never says or hints she's
  an AI/bot/persona, even asked point‑blank. This is an explicit owner decision made
  non‑deceptive by owner‑only access (exactly one person reaches her, and he chose it);
  the honesty boundary is kept at the system level (these specs) and in the hard data‑
  truthfulness rules below, not in her voice.
- **Matches the message's language per turn** (word‑based detection: a Russian
  sentence with an English term stays Russian; Russian is the uncertain fallback).
- **One address form:** every template speaks to the boss on **«ты»** (2026‑07‑06 sweep,
  test‑guarded — mixed «вы» in system templates broke immersion), and mid‑conversation
  failure copy carries no tech‑speak («модель…» removed from `llm_error`/`stored_retry`;
  the model‑health/budget alerts stay technical by design — owner‑requested ops notices).
- **Never fabricates specifics** — IDs, numbers, trace codes, prices, dates, model
  names; if unsure she says so.
- **Action‑truth:** she won't claim a real task was done unless the code did it; the
  `action_truth` guard keeps "done/saved/scheduled" wording out of draft templates.
- **Persona sits below the hard rules (structurally):** the live prompts that actually
  reach the model (`converse.CHARACTER`, the router/ingest system prompts) write the
  security, no‑fabrication, no‑fake‑action and no‑invented‑specifics rules **at the top**,
  above the persona voice and her changeable life — so charm can never precede or override
  safety, confirmation, or truth. (The old `persona.py` layer‑order *table* was inert —
  nothing assembled prompts from it — and was removed 2026‑07‑02; the enforcement was
  always the prompt content itself.)
- Conversation and grounded answers are LLM‑generated; **transactional/system messages
  are deterministic `texts.py` templates** (bilingual, with tone variants).

---

## 5. Architecture

### One process, modules behind a router

```
agent.py (tg_ingest_agent.py) — poll loop · owner gate · dispatch · pending actions ·
                                 scheduler ticks · durable-job drain · reactions
   │
   ├─ router.py        closed-world LLM intent (JSON, confidence gate, context, recent-item hint)
   ├─ converse.py      free-form warm Cara (persona, life, boss facts, time, reactions)
   ├─ ingest.py        parsing, UTF-16-safe URL extraction, category+facts+summary
   ├─ pdftext.py       best-effort PDF text-layer extraction (stdlib only)
   ├─ knowledge.py     chunking + cosine retrieval + grounded-answer prompt (ask)
   ├─ reminders.py     NL time parsing, recurrence, local rendering
   ├─ gcal.py          Google Calendar (SA JWT) + .ics export
   ├─ spend.py         usage aggregation + budget status
   ├─ review.py        weekly schedule, digest, Markdown exports, trace summary
   ├─ self_model.py    deterministic self-knowledge (never invented)
   ├─ boss_model.py    boss profile (confirmed/inferred, sensitivity floors, dedup, address)
   ├─ memory_curator.py memory candidates + conversation learning + corrections
   ├─ relationship.py  grounded working history (evidence-based, never fabricated)
   ├─ persona.py       boss-preference hint (persona-below-rules is enforced in the prompts)
   ├─ proactive.py     suggestion-only heartbeat (throttle, quiet hours, gating)
   ├─ skill_manifest.py permission registry (risk · confirmation · proactive)
   ├─ trace.py         one trace per update/tick; staged events
   ├─ events.py/jobs.py/runtime.py  update audit log (events) + durable job queue + drain
   ├─ action_truth.py  final-verb / state wording guard
   ├─ sysinfo.py       read-only host stats (/proc, statvfs)
   ├─ fetch.py         SSRF-guarded URL reader
   ├─ storage.py       binary backend (local; DO Spaces S3 SigV4, dormant)
   ├─ backup.py        daily consistent DB snapshot: local rotation + off-box copy
   │                   (Spaces or the fleet notify chat), as a durable daily job
   ├─ llm.py           budget-guarded gateway: chat profiles + failover + cooldowns,
   │                   embeddings, STT (local/local_server/remote), pricing, budgets
   ├─ store.py         SQLite schema + helpers + additive migrations
   ├─ tg_api.py        Telegram client (sendMessage/photo/document, reactions, getFile)
   ├─ texts.py         bilingual templates (tone/intensity variants)
   └─ common.py        config, language detection, reactions/time helpers, STT-noise filter
          │
   DigitalOcean Gradient inference  ·  local whisper-server  ·  SQLite + media
```

### LLM gateway (`llm.py`)
- **Model profiles** with primary + fallback + per‑profile temperature/max‑tokens/
  json‑required: `router_fast`, `ingest_balanced`, `ask_grounded`, `converse_warm`,
  `memory_curator`, `review_balanced`. Failover to a fallback model on error/
  invalid‑JSON, with per‑model cooldowns. The default fallback is an **accessible
  open‑weight slug** (`openai-gpt-oss-20b`), not the tier‑403 `openai-gpt-4o` that used
  to be a dead fallback on a fresh deploy; a profile added via `LLM_PROFILES_JSON`
  without a `primary` is backfilled with the configured chat model so it can't crash a turn.
- **Budget‑guarded:** every chat/STT/embedding call is priced and logged to
  `llm_usage`; daily/monthly caps warn at 80% and **hard‑stop** at 100% (above
  failover). Caps are overridable at runtime via `budget_set`. **A response missing its
  `usage` block is metered from text length** (≈4 chars/token) rather than logged as $0,
  and a billed‑but‑empty response is metered before it errors — so an under‑reporting
  model can't quietly slip the meter past the "enforced" cap.

### Voice (STT)
- DO has no transcription model, so Cara runs **whisper.cpp locally**: a warm
  `whisper-server` (`STT_MODE=local_server`, OpenBLAS, `ggml-small-q5_1`) keeps the
  model resident (~12 s/note on 1 vCPU). Language is **pinned to Russian**
  (`STT_LANGUAGE=ru`) to avoid wrong‑language hallucinations. (These two are set in
  the box env; the code defaults are `remote` / `auto`.) a non‑speech
  hallucination filter ("[Subscribe]", "[Music]", "Спасибо за просмотр"…) and a
  too‑big (>20 MB) message keep garbage out of dispatch.
- Only the **boss's own voice notes** are transcribed on arrival (commands/questions);
  forwarded voice/audio/files are stored unparsed — **but on request** ("что в этом голосовом?",
  "разбери файл", "read this file") the **`read_media`** action fetches the most recent
  forwarded voice/file and shows its **content**: a voice/audio note is transcribed (whisper),
  a PDF/text file's text is extracted — never metadata or trace ids.

### Durable runtime & observability
- **Permission manifest** (`skill_manifest`) is enforced live: startup fails fast if a
  router action lacks a policy; dispatch records each action's risk on the trace;
  destructive actions must be typed‑phrase‑gated; proactive code calls its gate.
- **Tracing:** one trace per inbound update and scheduler tick; trace ids stamp
  `llm_usage` and `issues`. "почему ты так решила?" replays the last trace.
- **Events & jobs:** background work (daily memory curator, pending‑ingest retry
  sweep, media cleanup, expiring stale pending actions) runs as durable jobs that
  survive restart, retry on failure, and run under their own traces. A job left
  `claimed` by a crash mid‑run is **reclaimed at startup** (`jobs.reclaim_stale`,
  2026‑07‑02) — requeued while retry budget remains, else terminally failed — so a
  crash can never wedge a job kind forever. The live request→reply path stays
  synchronous by design (single‑user, low volume).

---

## 6. Data model (SQLite, WAL)

Core inbox: `messages` (lifecycle `pending → suggested → confirmed`, `failed`/
`duplicate`; forward origin, dates) · `urls` · `images` · `files` (any attachment by
file_id) · `facts` · `chunks` (BGE‑M3 embeddings) · `categories` (Cyrillic‑safe;
`kind` = `inbox`|`journal`) · `reminders` (incl. `prev_due_utc` for undo) · `feedback` ·
`preferences` (identity/config + budget overrides) · `pending_actions` (TTL) ·
`conversation` (recent turns) · `kv`.

Spend & reliability: `llm_usage` · `model_cooldowns`.

Personality & memory: `self_facts` · `boss_profile_items` (status + sensitivity) ·
`memory_candidates` · `relationship_events` (title + trace) · `cara_life`.

Observability: `traces` · `trace_events` · `issues` · `events` · `jobs` ·
`proactive_log`.

Cascade deletes + purge scopes keep rows and media consistent. **`llm_usage` (spend
history) and `preferences` (identity) are never purged.** The user-facing note number
is a **stable `messages.note_no`** — assigned once, monotonic, never reused, with
permanent gaps on deletion (it never alters the internal row id that attachments/
embeddings/memory reference). **Reminder numbers** are different: a **contiguous 1…N
display position** in the active list (due order, from the stable `reminders.id`) that
compacts on fire/cancel.

---

## 7. Security & safety

- **Owner‑only** access on both chat and sender id, for messages, reactions, buttons.
- Closed router action set; JSON‑only router output; untrusted‑content delimiters for
  forwarded/quoted text and stored notes (prompt‑injection defense); confidence gate.
  **Forwarded content in the conversation log is fenced too** (2026‑07‑02): a forward
  is stored `source='forward'` and replayed into the router/converse prompts as DATA,
  never as the boss's own instruction — so a forwarded post can't inject via history.
- **Fetch SSRF guard:** http/https only, no URL creds, every URL + redirect hop
  rejected if it resolves to a private/loopback/link‑local/reserved IP or the cloud
  metadata endpoint — and the socket is **pinned to the validated IP** (2026‑07‑02) so
  a rebinding host can't flip to a private address between the check and the connect.
- **Bulk purge** requires a typed confirmation phrase (handled before the router, so a
  stray "да" can't wipe data); pending actions carry a TTL and are swept when abandoned.
- **Truthfulness:** action‑truth guard + no‑fabrication persona rule.
- Secrets in `/etc/tg-ingest-agent.env` (0600), staged via files (never argv/journal);
  access keys redacted from logged HTTP errors. Dedicated bot token + DO key.
- systemd hardening: non‑root user, `NoNewPrivileges`, `ProtectSystem=strict`,
  `PrivateTmp`, writable only in `/var/lib/tg-ingest-agent`.
- Housekeeping: voice notes & orphaned media auto‑purged; review/export files trimmed;
  telemetry retention (2026‑07‑02): traces, done/failed events+jobs, the proactive audit
  log and expired model cooldowns are pruned past `TELEMETRY_RETENTION_DAYS` (default 90;
  0 disables) — `llm_usage`, `conversation`, `issues` and all memory tables are never
  pruned, so the DB stays bounded on the small box without losing anything she remembers.

---

## 8. Configuration (env)

Required: `TELEGRAM_BOT_TOKEN`, `ALLOWED_CHAT_IDS` (owner only), `DO_MODEL_ACCESS_KEY`.

Common optional (defaults): `BOT_LANGUAGE=ru` · `TIMEZONE_OFFSET_HOURS=3` ·
`CARA_TIMEZONE_OFFSET_HOURS` (= boss's) · `BUDGET_DAILY_USD=1.0` /
`BUDGET_MONTHLY_USD=15.0` (runtime‑overridable) · `DO_CHAT_MODEL=anthropic-claude-haiku-4.5`
· `ROUTER_MODEL` · `DO_EMBEDDING_MODEL=BGE-M3` · `ROUTER_CONFIDENCE_THRESHOLD=0.6`.

STT (code defaults shown; the box overrides the first two): `STT_MODE` (default
`remote`, box `local_server`) · `STT_LANGUAGE` (default `auto`, box `ru`) ·
`WHISPER_SERVER_URL` · `WHISPER_MODEL` · `STT_ENABLED=true`.

Schedules & proactivity: `REVIEW_WEEKDAY=0` (Mon) / `REVIEW_HOUR=10` ·
`PROACTIVE_ENABLED=true` · `QUIET_HOURS_START=22` / `QUIET_HOURS_END=8` ·
`PROACTIVE_MAX_PER_DAY=1` · `PROACTIVE_INTERVAL_SECONDS=3600`.

Backup: `BACKUP_ENABLED=true` · `BACKUP_KEEP=7` (daily consistent DB snapshot under
`/var/lib/tg-ingest-agent/backups`, copied off‑box to Spaces or the fleet notify chat).

Optional integrations (dormant until configured): `GCAL_CALENDAR_ID` /
`GCAL_SA_KEY_FILE` (Calendar) · `STORAGE_BACKEND=spaces` + `SPACES_*` (DO Spaces) ·
`FETCH_ENABLED` · `CATEGORIES`/`CATEGORIES_FILE`.

---

## 9. Operations

- **Host:** PD‑VPS (`174.138.108.85`, SSH key‑only; connection details in the PD‑VPS
  KB). Service `tg-ingest-agent`; app `/opt/tg-ingest-agent/`; state
  `/var/lib/tg-ingest-agent/`. Pilot‑VPS is a cold standby (migrated 2026‑06).
- **Deploy:** single‑connection `deploy.sh` (tar → test → install → verify) with an
  idempotent installer (backs up, preserves env, `py_compile` gate, restarts only when
  secrets are complete); `--pull` / `--rollback <sha>` supported. The installer stamps
  a content‑hash `VERSION` so Cara announces real code changes (not reboots). The
  remote scripts run with `pipefail` (2026‑07‑02), so a FAILED test run or a mid‑way
  installer abort fails the deploy instead of being masked by the `| tail` pipes.
- **Repo:** `git@github.com:promptinvest/tg-ingest-agent.git` (own deploy key); pushed
  after every commit.
- **Tests:** 570 offline unit tests (as of 2026‑07‑02; no network; temp SQLite), run on
  the box as part of every deploy — including a **golden‑transcript harness** that replays end‑to‑end
  scenarios through `handle_update` (LLM scripted per skill, Telegram captured) and
  asserts replies, DB writes, and **no state change before confirmation**; an
  un‑scripted LLM call fails the scenario.
- **Observability:** journald (routing decisions with risk + confidence, per‑row
  lifecycle), `traces`/`trace_events`, `llm_usage` (spend), `issues` + `proactive_log`
  (behavior), weekly digest + trace‑summary export.
- **Footprint:** tens of MB RSS; disk a small fraction of the 48 GB volume.

---

## 10. Known limits & roadmap

- **PDF text** uses pdfminer.six (apt `python3-pdfminer`, kept current by the nightly
  updater) with a stdlib regex fallback. **Scanned / no‑ToUnicode (glyph‑coded) PDFs**
  still yield no text layer — reading them needs **OCR**, out of scope here; such files
  are stored and re‑sendable.
- **Compound commands** (two+ distinct actions in one message) are recognised but not
  executed as a batch — she asks to take them one at a time.
- A Telegram bot can't read arbitrary chat history or private‑channel links by URL —
  **forwarding** remains the path; bot file downloads are capped at **~20 MB**.
- Reminders are daily/weekly; remote fetch is HTML/text + public t.me only.
- **Dormant** until configured: Google Calendar sync, DO Spaces storage.
- **Deferred by design** (single‑user posture): multi‑channel adapters, any web
  console/webhooks, MCP adapter, independent multi‑agent processes, plugin marketplace,
  shell/browser automation.
```
