# Cara — Capabilities, Features & Architecture

**Cara** (`@cara_assist_bot`) is a personal, conversational AI assistant that lives
in Telegram and is self-hosted on the **PD‑VPS** (`174.138.108.85`, a DigitalOcean
droplet repurposed after the PD platform retired). The former Pilot‑VPS was retired
in 2026‑06; there is no active standby there.
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
| **Inference** | DigitalOcean Gradient (chat default/current `deepseek-4-flash`, default fallback `openai-gpt-oss-20b`, embeddings `BGE‑M3`); STT local `whisper.cpp` |
| **Storage** | SQLite (WAL) + local media dir; optional DO Spaces (dormant) |
| **Persona** | a warm, loyal human assistant persona with her own (fictional) life (the in-person companion/relationship side was split off to Nikki, 2026-07-03); **open and personal by the boss's wish** — shares her inner life and talks frankly about any personal matter, no "professional distance"; never breaks character; matches the boss's language |
| **Safety spine** | owner‑only access · permission manifest · confirm‑before‑state‑change · budget caps · SSRF guard · action‑truth · full tracing |

---

## 2. How she decides what to do (request flow)

```
Telegram update (owner-only: chat AND sender must be on the allowlist)
   │
   ├─ durable inbox → retry unexpected failures 3x; retain terminal payload as a dead letter
   │                  (and tell the boss it was dead-lettered; a DB failure pauses the
   │                   batch without advancing the offset instead of exiting — 2026-07-25)
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
  repeating the same beat ("a bad joke"). That rebalance is a ONE‑TIME migration, marker‑
  guarded since 2026‑07‑25 (documented here 2026‑07‑26): it used to re‑insert its three
  life rows on every start, so a life fact the boss had deliberately removed (memory
  consolidation or a purge) came back at the next restart. A life fact he deletes now
  stays deleted. **2026‑07‑26:** consolidation no longer DELETES a duplicate beat at
  all — it demotes it (`cara_life.status='merged'`, hidden from every reader, reversible
  by hand), so a wrong grouping call costs nothing permanent. `life_count` still counts
  folded rows on purpose: it is the "was this DB ever seeded?" marker read on every
  start, and an active‑only count would re‑attempt the whole seed insert at each startup.
  A folded beat is deliberately **not re‑learned** from conversation either (its text
  still occupies the UNIQUE key): re‑adding it would put a second live copy of the beat
  next to the one that was kept, i.e. re‑create exactly the over‑growth the fold exists
  to remove. Undoing a fold is therefore a manual `status='active'`, by design.
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
  fabricated confirmation. This is enforced in code as well as the prompt: a free-form reply
  that claims a current close/move/save/delete or says a queue is clean is blocked, logged as
  `converse_action_claim`, and replaced with an honest no-state-changed response.

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
  photo via vision, e.g. "одобряешь мой выбор?" → an opinion in her voice). Your own
  **photos are never stored** (retired 2026‑07‑16): even an explicit «сохрани эти фото»
  gets an honest decline with a hint (send it as text/a file or forward the post). Your
  own **text/PDF documents** with a save caption still file normally (the .md/.txt→KB
  flow). She also understands what you're **replying to or quoting** (TG reply/quote)
  as context for "this" — **first‑class since 2026‑07‑22**: the replied‑to/quoted
  text (up to 600 chars; a partial quote is used verbatim) reaches **both the
  router and converse**, labeled with **who said it** (her own earlier message ·
  your earlier message · a forwarded post) and fenced as DATA, so «поставь это
  на завтра», «что ты имела в виду?» or a quote‑reply resolve against the exact
  message you mean — even one far older than the rolling history window. A
  reply‑shaped **«сохрани это»** treats the replied‑to message as the note's
  subject (see referential saves below). Her conversation memory in free‑form
  chat now spans the last **20 turns** (was 12); deeper reads stay behind
  `recall_conversation` («перечитай наш разговор за вчера»).
- **You edit a message, she follows (2026‑07‑26).** Telegram edits are now requested
  (`allowed_updates` included `edited_message` for the first time) and handled:
  · the **dialogue record** is rewritten, so a verbatim readback matches what your chat
  shows (the whole promise of `recall_conversation`) — except from a caption on a **voice
  note**, whose turn holds the transcript of what you actually said, and except a caption
  you **delete**, which would blank the turn rather than correct it (§10);
  · a note still **in the inbox** (pending/suggested/failed) is re‑ingested — new text,
  re‑derived links, a fresh summary and new embeddings, with the old vectors dropped
  first, the stale card's buttons retired, and **everything the old text produced cleared
  with it** (a reminder candidate it carried, a journal draft it filled — otherwise a
  «созвон в 15:00» you replaced could still schedule 15:00 from the new card);
  · a note she has already **saved** is never rewritten behind your back: «я уже
  сохранила старую версию — обновить заметку #N?» with ✅/✖️ buttons, and only your yes
  applies the text, its links and a re‑embed (the outdated summary and key facts are
  dropped, so lists show the edited text itself; the category you filed it under does not
  change). The offer never takes the pending slot from a confirmation you are already
  mid‑way through — and because its buttons deliberately outlive that slot, a tap on a
  **stale** offer (older than an hour) is refused out loud instead of applying words
  staged days ago;
  · an edit that changes **nothing** is a no‑op — no model call, no new card;
  · edits from any other chat are ignored before anything is read or written;
  · **(2026‑07‑27)** an edit of a message that was a routed **command** — «напомни
  завтра в 15:00…», «запомни: …» — rewrites the dialogue record like any turn, and she
  adds ONE honest line: the reminder/remembered fact made from that message keeps the
  old details until you say to change it (she never re‑derives an alarm or a memory
  item from edited prose behind your back). The line fires only when the artifact
  **actually exists** — the pointer is written when the reminder is created at your
  confirm / the fact is stored, so a draft or a flagged fact you declined (or let
  expire) earns plain silence, a purge that deleted the reminders drops the pointer
  with the rows, and a replayed edit carrying identical text does not repeat the line.
  **Honest about the outage case:** the re‑embed is best‑effort. If the embedder is down
  in that moment the note keeps its text and stays in your lists but is briefly out of
  `ask` — the sweep that retries pending notes now also re‑indexes any visible note left
  without vectors, so it comes back on its own (it used to be permanent and silent).
  What is NOT applied at all is in §10.
- **Ingest forwards/notes:** forwarded posts and typed notes (text, URLs, photos;
  an album = one item) are saved with forward origin, t.me source link, post date.
  Forwarded albums are **crash-safe** (2026‑07‑17): buffered parts stay pending in
  the durable update inbox until the album is filed, a restart replays them, and a
  filing error gets an honest «перешли ещё раз» instead of a silent loss.
  **2026‑07‑25:** your OWN album saved with a caption («сохрани») now stores **every
  document part** — it used to keep part 1 and lose 2..N unrecoverably behind a normal
  confirmation card. A **mixed** album (photos + a real file) still files only the
  files: own photos stay unstored (retired 2026‑07‑16), and the counts line says so
  («фото: 0») — **and since 2026‑07‑26 she says it in words too**, naming how many
  picture parts stay in the chat instead of leaving him to read a zero off a card.
  That line describes only what is NOT kept: it is sent before the confirmation
  card, so it must make no promise about what the note will end up holding.
  Own‑media album parts are durable/deferred like forwarded ones (a crash
  in the settle window no longer drops the album), and if their filing fails you get
  an honest «отправь ещё раз» + an incident row — before, only a forwarded album's
  failure was ever mentioned. A **shutdown** leaves a half‑arrived album to the startup
  replay instead of filing the half it holds (the late parts used to become a second
  note); and a **forwarded sticker** is a sticker, not a "(no analyzable content)" note.
  A vision LLM suggests a **category** (from your taxonomy), a **summary**, and up to
  5 **key facts** — strictly in the source language. Duplicates are detected.
  A **referential save** ("сохрани заметку про этот фильм") with no subject of its
  own resolves the subject from the recent conversation — so the note captures the
  actual film/topic discussed, not the bare command; a save sent as a **reply**
  («сохрани это» on a specific message, 2026‑07‑22) resolves against exactly the
  replied‑to/quoted message as the primary referent, ahead of the rolling history. If the model's reply won't parse,
  she **never stores raw JSON** as the summary — she salvages the fields, else leaves it
  empty so the note shows its real text. Long note/journal listings are **paginated**,
  not cut off at Telegram's length limit. Gratitude (and any **journal** entry) lands in
  the right journal even when the model writes a singular/variant of its name; a
  referential save with no resolvable subject keeps its real text instead of a blank note.
- **Habit auto‑confirm (opt‑in, documented 2026‑07‑26 — it shipped undocumented):** when
  you have filed the last `HABIT_THRESHOLD` (default 10) forwards from the **same source
  channel** into the same category, she **asks once** — «я заметила: последние 10 постов
  из «X» ты относишь к «Y». Давай я буду подтверждать их сама?». Only your yes turns it
  on (stored as the `auto_cat:{source}` preference); after that, posts from **that one
  source** are filed under **that one category** without a card, and she says she did it
  («сама записала в «Y» (#N)»). A no is remembered too (`auto_cat_declined:{source}`), so
  she never asks about that source again, and the proposal is skipped whenever another
  confirmation is already pending. It changes only WHO presses confirm — it never widens
  what may be saved, and it never applies to a source you have not agreed to.
- **Link‑aware ingest (2026‑07‑06):** a **link‑centric** note (short text + a URL) has
  its first URL **fetched** through the SSRF‑guarded reader — the summary describes the
  ACTUAL page (no more "вероятно, содержит…") and the page text is **indexed**, so `ask`
  answers from what the link really says. Rich forwarded posts aren't delayed by a fetch
  (only raw text < 400 chars triggers it); a failed fetch degrades to today's behavior.
  Toggle: `INGEST_READ_LINKS` (on), prompt cap `INGEST_FETCH_CHARS` (3500).
  **2026‑07‑25:** every fetch has a **total wall‑clock budget** (2 × `FETCH_TIMEOUT_SECONDS`,
  shared across redirect hops, and the per‑hop socket timeout is clamped to what's left)
  — a server that drips bytes used to hold the single thread for hours and freeze the
  whole bot, reminders included, from one forwarded link. **What the budget really
  bounds (stated honestly 2026‑07‑26):** it is checked at every hop boundary and before
  every body chunk, so the BODY transfer and the hop chain are genuinely capped. Inside
  one hop, `urlopen()` — DNS, connect, TLS and the response headers — is a single opaque
  blocking call this code cannot interrupt; there the only bound is the socket timeout,
  already clamped to the remaining budget, applied PER socket operation. A server that
  dribbles header bytes just under that timeout can therefore still outlive the budget
  within one hop. The body is read one socket
  read at a time so the budget is actually reachable; an unknown/quoted page charset no
  longer loses the page; the SSRF filter also
  blocks 100.64.0.0/10 (CGN/Tailscale); a bare‑domain link entity («example.com/x») is
  fetchable; and two fetches in the same second are two notes (the second used to store
  nothing, silently — now it's stored, or she says it didn't land). A
  **meta‑summary** ("Пользователь просит записать…") is dropped in code, not just
  forbidden in the prompt — the note falls back to its real text. **Category near‑
  variants are snapped** to the canonical existing name at suggestion time ("AI tools"
  reuses "AI Tools & Resources" instead of coining a duplicate), and the weekly review
  lists remaining look‑alike pairs with a merge hint. **List cosmetics:** previews cut on
  a word boundary with «…», and URLs show as host+path (no tracking params) in lists —
  the full URL stays in the detail card.
- **Forward-to-reminder handoff (2026-07-15):** a standalone forward remains
  untrusted inbox content and can never execute its wording. The narrow exception is
  when the boss has just opened a half-specified reminder that already has a time and
  is explicitly waiting for its title: the next single forwarded text supplies that
  title as **data**, Cara shows the normal reminder draft, and nothing is scheduled
  until the boss confirms. Common “напомни пожалуйста вечером…” framing is stripped
  cosmetically from the title; media albums keep the ordinary ingest path.
  A journal also owns its common Russian singular/plural stem at the **write boundary**:
  a manual correction to «Благодарности» reuses an existing «Благодарность» journal instead
  of creating a parallel inbox category.
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
  never break. (Owner decision, 2026‑06‑29 — gaps accepted for stability.) Since
  **2026‑07‑25** that promise is enforced for every number issued from then on: the
  number comes from a durable per‑chat counter, not `MAX(note_no)+1` over the *live*
  rows, which handed the newest deleted note's number straight back to the next save
  (and made the outcome ledger swallow the new note's `captured` row, quietly shrinking
  the saved‑to‑used KPI). One bounded exception remains on the upgrade boundary: a
  number issued *before* the counter existed and deleted while the note was still merely
  «suggested» left no trace at all, so the counter's one‑time seed can hand it out once
  more. Only «удали всё» — which wipes the ledger too — restarts numbering at #1.
  **«Покажи файлы» no longer mints numbers (2026‑07‑27):** the files listing lazily
  assigned a permanent #N to *failed/duplicate* rows — invisible in every note list, so
  the numbering jumped (#56 → #58) and «убери #57» answered «вне жизненного цикла».
  Such a file now lists honestly without a number; visible notes keep their lazy #N.
  **Reminder
  numbers are different** — a contiguous 1…N position in the active list (soonest‑due
  first) that **compacts** as reminders fire/cancel; "#N" in reschedule/cancel/undo
  resolves to that position, and Cara re‑shows the refreshed list after a cancel so a
  captured number never goes stale.
  An explicit note delete in either common word order — **«Удали #N»** or
  **«#N — удали»** — is a deterministic closed-world command and never depends on
  an LLM route. The recent-reminder-list stamp remains the deliberate exception: after
  Cara has shown reminders, the same `#N` form cancels that displayed reminder.
- **Journals (long‑term areas):** mark a category as a journal — "веди Благодарности
  как дневник" / "сделай X журналом" — and it becomes append‑only: each note acks as a
  dated entry ("запись за 18.06, всего N"), "покажи дневник благодарности [за неделю/
  месяц]" — or just a bare **"покажи благодарности"** — replays it as a **day‑grouped
  series** (deterministic `#N • snippet`, never free‑texted by the model). The show handler
  **resolves a loosely‑typed name** (`_match_journal_category`) so an inflection ("благодарности"
  vs the stored "Благодарность") hits the real journal instead of spawning a phantom empty
  one. Recall is a real **5-entry inline pager** (◀ · X/Y · ▶) that edits the same Telegram
  message; the category + period (+ person/tag filter) live in the `list_views` token, so pages
  do not repeat or silently truncate. A "📔 Дневники" digest appears in the weekly review and
  morning brief, and a "clear all notes" purge **spares it**. Turn it back off with "X больше
  не дневник". One‑time notes behave exactly as before.
- **Structured journals + Gratitude built‑in (2026‑07‑17, Batch 3 JRN‑001…006):**
  journals are now first‑class semantic entities: `journal_definitions` (slug ·
  entry type · linked category · sensitivity · per‑journal prompt opt‑in) +
  `journal_entries` (one row per source message: `occurred_at`, validated
  `payload_json`, `extraction_status`). The **entry‑type registry is closed, in
  code** (`journals.py`: gratitude · win · lesson · decision · memorable_moment ·
  mood · health · mistake · idea · generic_event — only **gratitude** is active;
  the rest are the extension contract). The LLM can never invent schemas or
  validators. **Gratitude capture:** «запиши в благодарности: …» / «я благодарен
  Вере за …» → the card shows the extracted core fields («Кому/чему · За что …»)
  with «📔 Добавить / ✏️ Изменить / ✖️ Отмена»; every non‑null field needs
  **lexical support** in the source text (invented names are rejected), «Изменить»
  edits the pending **draft** (his correction re‑extracts; nothing is written
  until confirm), a failed extraction still saves the raw entry honestly
  (`extraction_status='failed'` — no invented structure), and the source text is
  never rewritten. Entries display as **J#N** — the linked message's stable note
  number, no second counter; `J#41` and legacy `#41` both resolve. Legacy
  gratitude history was **migrated deterministically** (no bulk LLM): the
  canonical «Благодарности» journal got one `legacy_unstructured` entry per
  confirmed message; the built‑in definition binds to an existing gratitude
  category at migration (fresh DBs bind the moment one is made a journal).
  **Recall extras:** filter by person/tag («покажи благодарности про Веру»),
  deterministic person counts with citations («кому я чаще всего был
  благодарен?» → «Вера — 3 (J#41, J#43…)» — from validated fields only, never a
  diagnosis), per‑journal **Markdown export** («выгрузи дневник благодарности в
  md»), and a journal‑specific **typed purge phrase** («да, очистить дневник X» —
  entries go, the diary + definition survive; a category purge aimed at a journal
  automatically switches to the journal phrase). **Opt‑in prompts (off by
  default):** «предлагай мне вечером записывать благодарность» — enabling needs
  an explicit «да» (pending confirm), the invitation fires at most once a day
  past the configured hour, only when today has no entry, honors quiet
  hours/days/the daily proactive cap, and «выключи приглашения» turns it off
  instantly. «X больше не дневник» also deactivates the structured definition —
  the boss's decision wins; existing entries stay readable.
  **Diary membership follows the category (2026‑07‑27):** «перенеси J#12 в Идеи»
  now removes the entry row (the note used to stay in the diary forever — listed,
  counted in the person stats, exported, and even surviving the diary's own purge),
  moving between two journals moves the entry, and folding a plain category INTO a
  journal (`merge_categories`) creates the missing entry rows immediately instead of
  waiting for the next restart's backfill. A confirmed edit of a journal note resets
  its extracted fields to unstructured (see §10) — «Аня» no longer answers the stats
  after the text says «Борис».
- **Overview & stats:** "что у тебя есть?" → a digest (counts, reminders, memory,
  spend); per‑status/category **stats** (`stats`) and the **category list**
  (`categories`).
  **Three hidden slash aliases (documented 2026‑07‑26 — they shipped, and the only
  place they were written down was the README section that WP11 replaced):**
  `/start` → her greeting by name, `/stats` → exactly the same text as the `stats`
  capability above, `/categories` → the same category list. They bypass the router
  (no model call, no tokens) and exist for debugging and for the very first message
  to a fresh bot; nothing else needs a slash command.
- **Re‑categorize** (`recategorize`): "поменяй категорию #2 на Документы", "переложи
  это в Чеки" (most recent), "переложи всё из crypto в news" (bulk — moves the WHOLE
  set, reporting the real count). Logged as a correction so it feeds learning.
  Generic rejection while a category suggestion is pending (for example
  «Неправильно!» / “wrong category”) never becomes an LLM-invented category: the
  suggestion stays unconfirmed and Cara asks for an explicit «Категория — …».
  **A REPLY to a suggestion card is only read as a category when it plausibly is
  one (2026‑07‑25):** an explicit phrase («категория: планы», «смени категорию на
  …») always counts, whatever its length; a bare word counts when it is short, not
  a question, and a near‑variant of a category that already exists («финансы» →
  «Финансы»). **Tightened 2026‑07‑26:** "near‑variant" now means every significant
  word he wrote belongs to the existing name («ai tools» → «AI Tools & Resources»)
  **and that it is more than one word** — a lone «позже» is a subset of a category
  called «Прочитать позже», and he was saying "later", not choosing a shelf; a
  single‑word reply therefore only matches a single‑word category.
  The other direction — an existing name contained in a LONGER reply — is what made
  «это точно не финансы» file the note into «Финансы»: short enough, no «?», and
  the fuzzy matcher accepted a subset either way. (The ingest snap still matches
  both ways; there the candidate is model‑written, not a sentence he typed.)
  Anything else — «а зачем это сохранять?» — routes on as ordinary
  conversation and the card stays pending; it used to be taken wholesale as the
  note's new category and confirmed into it. **Narrowed on purpose:** a bare reply
  naming a category that does NOT exist yet («Крипта» when there is no such
  category) no longer creates it — say «категория: Крипта» and it does. And a reply
  always acts on the card you replied to: only one confirmation is pending at a
  time, so answering an OLDER card used to file the newest one instead.
  Merging categories **never strips journal protection** (2026‑07‑17): folding a
  journal into another name carries the journal kind to the destination.
- **Edit a note's summary** (`note_edit`): "исправь заметку #11 на …", "поменяй краткое
  #3 на …" — fixes the LLM‑written summary shown in lists/detail **in place**; the
  original message text (`raw_text`, the KB‑search source) is preserved. Distinct from
  re‑categorize (category) and reminder rename (a reminder's title).
- **Delete / discard / purge:** delete by id/ids/count/query; decline a fresh
  suggestion; bulk purge by scope (all / category / stats / reminders / messages /
  issues / journal) behind a typed phrase. The preview lists **exactly** what the
  execute deletes: `stats` never touches our conversation history, and `all` — the
  only scope that does — discloses the conversation‑turn count before you type the
  phrase (2026‑07‑16). A journal purge has its **own** phrase («да, очистить
  дневник X», 2026‑07‑17) and leaves the diary itself (category + definition) in
  place. **2026‑07‑25:** `stats` keeps journal categories (a stats reset used to
  strip the journal mark from every diary, so its entries flooded the #N lists and
  the next notes purge deleted them), and `all` additionally **scrubs the verbatim
  copies** Telegram delivery left in the durable inbox — disclosed in the preview
  as «служебных копий входящих сообщений». **2026‑07‑26:** that scrub now also
  clears `telegram_updates.last_error`, which kept up to 1000 chars of the failing
  text (an exception repr routinely quotes the message), and drops the kv pointers
  that hold raw row ids — the note‑review snapshot, the resurfacing pointer, the
  fired‑notification map and «последнее напоминание».
- **One-card capture (2026‑07‑17, NTE‑003):** the suggestion card now carries the
  **why** — a source‑grounded `saved_reason` + proposed purpose (📌 line; the
  meta‑copy guard drops a reason that describes the request instead of the
  content) — and conditional buttons: every card gets «🕒 Временно (30 дней)»
  (advisory expiry) and «🗑 Не сохранять» beside the category confirm; when the
  content itself carries a concrete FUTURE date the model may propose an
  `action_candidate` (⏰ line, validated by deterministic date code — the model
  is never the authority on dates) and the card adds «✅⏰ Сохранить +
  напоминание». That button commits the note **first**, then stages a normal
  reminder **draft** in the now‑free single pending slot (your «да» confirms it
  through the ordinary reminder flow — nothing is ever scheduled by the button
  alone, and a mid‑flight confirmation is never clobbered: the note saves and
  Cara says she'll offer the reminder after the open question).
- **Notes review (`note_review`, 2026‑07‑17 — suggestion‑only):** «покажи, что
  стоит пересмотреть» → at most **three** items, each with a deterministic
  reason (пора пересмотреть — ты просил · временная, срок подходит · требовала
  действия, движения нет · не разобрана · давно лежит без дела), selected by a
  fixed priority order, never re‑shown the same day. The shown batch is
  **snapshotted**, so a follow‑up «второе в архив» / «оставь первое» / «все в
  архив» acts on exactly what you saw (24 h window; 15 min after a proactive
  invitation) — never a recomputed list. **Ordinals stay positional
  (2026‑07‑25):** if one of the shown items was deleted meanwhile, «третье»
  still means the third item you were shown, and naming the deleted one gets a
  not‑found — the list is no longer compacted, which used to shift every ordinal
  after the gap. Naming a position that was never shown («четвёртое» after a
  three‑item review), or one whose item is gone, is likewise a not‑found instead
  of the newest note. State views: «покажи архив» /
  «покажи входящие» open the exact lifecycle view with pagination, and «что у
  тебя есть?» now leads with the notes overview (активные · входящие · на
  пересмотр · архив).
- **Related‑note resurfacing (2026‑07‑17 — one, or nothing):** after a
  delivered KB answer Cara may add at most ONE compact hint («К слову, у тебя
  есть ещё #N по этой теме — открыть?») drawn from the real retrieval ranking —
  never during personal conversation (business path only), never free‑texted.
  Opening the hinted note within 15 minutes counts as an accepted suggestion;
  ranking alone still counts nothing.
- **Note lifecycle (`note_lifecycle`, 2026‑07‑17 — reversible triage, never
  deletes):** beside its category (what it's about) every note carries a
  **knowledge state** (inbox → active → archived) and a **purpose** (справка /
  источник / идея / решение / временная / требует действия). «Убери #5 в архив»
  — the note leaves the default lists but stays searchable and comes back with
  «восстанови #5»; «оставь #3 в активных»; «пометь #3 как идею»; «поставь #4 на
  пересмотр через месяц» (no alarm fires — it will surface in the notes review);
  «сделай #4 временной на 30 дней» (advisory expiry — nothing is EVER deleted
  automatically). **Explicit `#N` targets fail CLOSED (2026‑07‑25):** «убери #7 в
  архив» or «в архив #7 и #9» when those numbers no longer exist replies not‑found
  and touches nothing — it no longer falls back to the newest note (lifecycle ops
  skip confirmation, so that archived an unrelated note instantly); the same holds
  for delete‑by‑ids, for a single stale `#N` on re‑categorize, and for an unusable
  count. **Extended to every single‑note handler (2026‑07‑26):** the SINGULAR
  resolver used by «покажи #7», «покажи фото из #7», «исправь заметку #7 на …» and
  «напомни по заметке 7» fell through to the newest note in exactly the same way —
  so a stale number could edit the wrong note's summary, send another note's
  photos, show another note's card, or tie the reminder to a note he never named
  (none of these ask for confirmation, and none of them name the substitute back
  to him). All of them now answer «ничего не нашла». An id‑LESS request (a query,
  a category, or nothing) is unchanged: best match, else the most recent. Two
  boundaries, pinned the same day: a router id arriving as «#7» / «J#7» / «7.» is
  normalized rather than refused (the router is a model reading prose full of «#»,
  and a hard not‑found on a note that exists is its own bug), while an id that is
  present but unusable («», «abc») counts as a router artefact — it may fall back
  to a text SEARCH, which can only return a real match, never to "the most recent".
  **Closed the residual asymmetries (2026‑07‑27):** the PLURAL resolver (delete /
  lifecycle / recategorize) now has the same artefact‑id search escape the singular
  one got — «удали заметку про крипту» arriving with a garbled id finds the note by
  the query instead of a false not‑found; an explicit `#N` beats a stray `count`
  («убери #7 в архив» routed with `count: 1` no longer archives the newest note);
  «удали 3 из crypto» is bounded by the named category/query instead of taking the
  three newest notes overall; «расшифруй голосовое из #12» normalizes «#12» like
  every other note path (it used to read the newest UNRELATED file as the answer —
  a garbled non‑empty id now asks which note, only a truly id‑less request keeps
  the recent‑file fallback); «второе удали» right after a review card resolves
  against the SNAPSHOT on the delete path exactly as on the archive path; and the
  snapshot's collective forms now include «всё»/«эти»/«их»/"them" — «всё в архив»
  used to fall through and archive the newest note in the inbox, unconfirmed.
  **Accepted residue:** a targetless lifecycle wording the resolver does not
  recognise («те два в архив», «первые две удали») still falls through to the
  newest note even while a review card is live — the snapshot lives 24 h, and
  during all of it «убери последнюю в архив» must keep meaning the newest note,
  so a blanket "clarify while a snapshot exists" would refuse legitimate commands
  far more often than it would catch this phrasing.
  A **bulk** archive asks for confirmation first. New notes enter
  as `inbox` on suggestion and become `active`/`reference` on confirm; journal
  entries stay outside note lifecycle. **Real‑use accounting:** opening a note's
  detail card or having it cited in a *delivered* KB answer bumps its use count
  (retrieval/ranking alone never counts) — the basis for the upcoming
  review/resurfacing features.

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
    "готово" — she never auto‑closes it on a misread. Common follow-ups ("закрой",
    "готово", "сегодня пропускаем", "через 30 минут", "до завтра") resolve before the
    probabilistic router. An explicit close/skip/snooze still binds to the last fired active
    reminder after the short pending window expires; a bare «да» requires live pending context.
    **A follow-up never introduces its own subject (2026‑07‑22 fix):** a message that
    carries a new subject beyond the follow-up scaffold (defer/ack verbs, reminder
    references, time words) or the fired reminder's own title — e.g. «Поставь
    напоминание на завтра 10:30 — Эрика» — is a NEW command and goes through the
    normal router; it is never eaten as a snooze (the live incident silently
    dropped «Эрика» and echoed the gratitude daily at 10:30 instead). And the
    late binding (after the pending expires) applies to a **recurring** reminder
    only within a ~3‑hour recency window after it fired — its series has already
    advanced, so hours later «завтра в 10» belongs to the router; a fired
    **one‑shot** genuinely stays open and remains closable/snoozable any time.
    **A TG Reply to a specific alarm names THAT reminder (2026‑07‑23 fix):** every
    delivered fired‑notification's Telegram message id is remembered (bounded map),
    and a reply to one of them binds the follow‑up («готово», «отложи на завтра»)
    to **exactly that reminder** — the strongest binding, overriding both the live
    pending and the last‑fired recency rule, with no time window (replying IS
    explicit). The live incident: «Отложи на завтра» sent as a Reply on the
    «заметка #9» alarm used to snooze the just‑fired gratitude daily instead.
    Acting on a replied‑to/last‑fired alarm also **never wipes an unrelated
    open confirmation** any more (his journal capture card survived intact).
    **A reply to an ALREADY‑CLOSED alarm is refused, never redirected
    (2026‑07‑25):** if that reminder is closed/expired/gone Cara says so plainly
    and touches nothing — she no longer falls through to the live pending or the
    last‑touched reminder (the same incident class as above). Substantive content
    replied to a closed alarm still routes normally. **Scope, made exact
    2026‑07‑26:** that guard only sees follow‑up wordings the deterministic parser
    recognises; anything else reaches the router, where a targetless
    reschedule/rename used to bind to the last‑touched reminder. So the same rule
    now also sits in `_resolve_reminder_target` **and in the undo handler, which
    resolves its own target** (a Reply saying «верни как было» on an acked alarm
    used to restore an unrelated reminder's previous time): when a message is a
    Reply to a fired notification and names no target of its own, that reminder IS
    the target if it is still active, and a not‑found refusal if it is closed —
    never a different one. An explicit `#N`/title in the same message still wins,
    and so does a positional «второе».
    **Deterministic follow‑up parsing fixes (2026‑07‑25):** «отложи на
    послезавтра» now moves it **two** days (a substring test read «завтра» inside
    it and re‑armed the alarm a full day early; "day after tomorrow" works too),
    and «отложи на 2 часа» is read as
    the duration idiom **+2 hours** (it used to full‑match the absolute‑clock
    branch and try to snooze to 02:00, then ask to clarify); «отложи на 2» / «до 2
    часов» stay absolute‑clock as before. During a «какое из них?»
    disambiguation, a message carrying a fresh **time** («давай лучше в 2 часа»,
    «через 2 дня») is no longer read as picking reminder #2 — only a bare or
    `#`‑prefixed number is a pick, and a time re‑routes as a new reschedule. A
    one‑element `#N` target («перенеси #2») now counts as an explicit target
    instead of being dropped onto the last‑touched reminder, and a list where only
    some numbers still exist («#1 и #99») is a not‑found with the active list, not
    a silent move of something else. And «пока» / «давай» are no longer acks: ack
    words are matched at word boundaries, so a goodbye can't close a fired alarm.
    «Сегодня пропустим» / "skip today"
    counts as that ack (deterministic — today's instance closes; a recurring one still
    fires tomorrow on schedule). **Snooze** by minutes, hours, or an
    absolute time ("через полчаса", "отложи на час", "до завтра в 9") **re‑arms the same
    ONE‑SHOT reminder** (keeps its id and history — no orphaned new row); on a
    **recurring** reminder a snooze is a **one‑time deferral**: a one‑shot echo fires at
    the snoozed time and the daily/weekly schedule stays exactly where you set it
    (2026‑07‑06 fix — snoozes used to shift the daily anchor: благодарности drifted
    22:00 → 23:33 over two snoozes). Bare local-clock language such as
    **«Отложи на 12»** means 12:00 today
    when that time is still ahead. If it already passed, Cara asks for «завтра
    в 12» or another future time and leaves the reminder open; she never silently
    rolls it to tomorrow or treats a following bare «да» as completion.
    The reminder
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
    different one); "верни предыдущее время" / "отмени перенос" undoes the last move
    **you** made — a recurring reminder's automatic advance to its next occurrence is
    not a "move" and can't be undone into the past (2026‑07‑16: that used to silently
    kill the series).
  - **Fired‑reminder replies parse absolutely:** «давай завтра в 10 часов» after an
    alarm re‑arms it for **tomorrow 10:00**, not a "10‑hour snooze" (2026‑07‑16).
    **The meridiem is read too (2026‑07‑27):** «tomorrow at 5 pm» / «завтра в 5
    вечера» re‑arms at **17:00**, not 05:00 — am/pm and «утра/дня/вечера/ночи» right
    after the clock time are applied instead of dropped.
  - **"Это напоминание"** binds to the one you were just dealing with; if it's genuinely
    ambiguous she asks which and **remembers what you wanted** — your "второе" / "#2" /
    "про банк" then completes the move/rename on the right one (never a stray close).
    **Your pick means the card you were shown (2026‑07‑27):** the positions are pinned
    to the exact ordered list the «какое?» card rendered, like the note‑review snapshot
    — if a daily fires‑and‑advances (or one expires) between the question and your
    answer, «первое» still means the first item of that card, and picking one that has
    since closed is answered «уже закрыто», never applied to a shifted neighbour.
    A reminder id arriving as «#2» / «2.» is normalized like the note path — «перенеси
    #2 на 17:00» no longer answers not‑found on a reminder that is right there
    (2026‑07‑27).
  - **Complete a half‑specified reminder:** an unmistakable time-only command such as
    "напомни в 17:00" is recognized deterministically (no router-confidence gamble) →
    she asks the subject, stitches your typed answer or the next single forwarded text
    in as untrusted title data, then confirms — the partial isn't lost and the forward
    alone never acts. A time that resolves to the **past** never enters the draft, and
    a fresh valid time **replaces** a stored one (2026‑07‑16: a past‑parsed «в 9» used
    to wedge the draft into an endless "а во сколько?" loop).
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
  gated. "Забудь/подтверди …" targets an item by **explicit** `#N` (or a bare number)
  or by text match — a digit inside the phrase («забудь, что я встаю в 6 утра») is
  part of the fact, never item #6 (2026‑07‑16).
- **Memory candidates:** she proposes durable memories from evidence; "обзор памяти"
  lists them with confirm/skip buttons. Durable memory only after a yes; benign facts
  learned from chat are stored as correctable "inferred" items — **but a fact that
  contradicts something you already confirmed is proposed for confirmation, not
  silently auto‑stored**. **Speaker-bound evidence (2026‑07‑13):** every LLM-extracted
  boss fact or correction must carry an exact quote from a genuine, non-forwarded boss
  turn and share meaningful words with the normalized memory; Cara-life facts require
  a Cara quote. Missing, wrong-speaker, or unrelated evidence is rejected in code, so
  Cara's own reply can never round-trip into a made-up boss preference. Evidence now
  survives the complete `conversation → candidate → confirmation → boss profile` path;
  candidates also retain their source trace, first/last-seen times and recurrence count.
  Near-identical pending candidates are folded deterministically (shared meaningful-token
  containment), so a short fact and a longer restatement no longer produce two approval
  prompts. A candidate the
  consolidation already folded (`merged`/
  `superseded`) is **never re‑proposed** (2026‑07‑02) — the curator's dedup no longer
  churns the same text through propose→fold every pass.
- **Memory provenance** (`memory_why`): "откуда ты это знаешь?" / "почему ты это
  помнишь?" → she cites *how* she learned it, in character ("ты сам мне это сказал",
  "ты меня поправил", "заметила из наших разговоров", with the date).
- **Corrections that stick:** when you correct her behavior she **says** she learned
  it, **applies** it (injected into her prompt), and **reports** it in the review. If
  the same correction recurs she flags it as **needing a code fix** instead of
  pretending to fix it. She says “Запомнила” only after the evidence checks above pass.
  **2026‑07‑26:** …and only for corrections that are actually IN FORCE. A *sensitive*
  correction is a confirm‑first candidate, not a standing rule — it used to be counted
  as learned anyway, so she claimed «Запомнила: …» for a rule she was not following.
  Those now get their own confirm‑first line, which **names the route that exists**
  («положила в предложения — скажи «что ты хочешь запомнить»») rather than asking a bare
  yes/no: nothing is staged in the pending slot for it, so a «да» would have reached
  «нечего подтверждать». And it is said only when a proposal was really queued — a rule
  you already **refused**, or one the consolidation folded, creates no candidate, so
  there is nothing to announce and nothing to confirm.
- **Standing rules reach the prompt even as the profile grows (2026‑07‑26).** The
  tone/workflow/avoidance/quality rules were selected by fetching the top‑20 profile
  items and filtering the kinds in Python: past ~20 higher‑confidence facts of other
  kinds, the guidance list came back EMPTY and Cara silently stopped honoring every
  standing correction, with nothing in the logs to say so. The kind filter is now part
  of the SQL, before the LIMIT (same for the descriptive operating model, limit 80).
- **Working history:** "как ты мне помогала?" → a grounded summary of real actions
  (saves, corrections, reminders, reviews, exports) — never fabricated.
- **Settings memory** (`memory`): "запомни: отвечай по‑английски", "что ты помнишь из
  настроек?" — language, timezone, auto‑calendar, named notes.

### Reporting & ops
- **Morning brief (opt‑in, documented 2026‑07‑26 — it shipped undocumented):** OFF until
  you ask for it — «делай мне утреннюю сводку» / «присылай утренний бриф» (and
  «не нужна утренняя сводка» turns it back off; it is stored as the `morning_brief`
  preference, so it survives restarts). When on, it fires **once a day at or after
  `MORNING_BRIEF_HOUR`** (default 9, your local time) and only if the proactive
  heartbeat is enabled and you are outside quiet hours — a brief is a nudge, not an
  alarm. Contents, all deterministic (no model call): today's reminders with their
  times, what is **overdue**, one‑shots that fired and still wait for «готово», the
  open threads from your working history, and the **📔 Дневники** journal rollup.
  When there is genuinely nothing worth a ping she sends nothing and marks the day
  done. **Delivery‑gated (2026‑07‑06):** the day is stamped done only after Telegram
  confirms the send — a transient failure backs off 15 minutes and retries up to 3
  times, then logs a `sched_send_failed` issue and gives up for that day rather than
  wedging the schedule forever.
- **Weekly performance review:** runs on a fixed schedule (default **Monday 10:00
  local**); "когда следующий review?" tells you the date; "как ты поработала?" runs it
  on demand. **Saved‑to‑used outcomes lead (2026‑07‑17, MET‑001):** the user‑facing
  review opens with what the saved material actually DID — saved · actually used
  (opened/cited) · turned into reminders · archived/restored · awaiting triage ·
  review‑due · upcoming reviews/temporary expiring — plus the **📔 Дневники**
  journal‑activity rollup; operational metrics (issues, spend, model fallbacks,
  first‑guess accuracy, memory counts) move to the **«⚙️ Как я работала» Cara‑health
  tail**. The engineering Markdown adds a **"Notes outcomes"** section with the KPI
  `capture_to_use_rate` (distinct notes used / distinct notes confirmed — never
  optimized toward more saves/nudges/entries), median capture→first‑use
  (durable all‑time milestones; an upgrade with no retained first-use event is
  honestly labelled as a legacy last-use approximation), % archived unused, inbox age, review‑batch and
  resurfacing acceptance counts, and journal entries per journal. A reminder
  created from a note (capture card «Сохранить + напоминание» or «напомни по
  заметке N») records `note_reminder_proposed`/`note_reminder_created` outcome
  events — the link survives draft amends. Markdown exports for VS Code:
  review, self, boss profile, working history, memory candidates, trace summary.
  **Real-file boundary (2026‑07‑13):** the short follow-up `Давай md` / `send the md`
  is resolved deterministically to `review(export=true)` without an LLM router call;
  the existing handler writes the report and uploads it through Telegram `sendDocument`.
  Free-form `converse` cannot create attachments: a bare `[Review.md]` or “here's the
  file” claim is blocked and logged instead of being delivered as a fake link.
  **Truthful review semantics (2026‑07‑13):** saved knowledge items and conversation
  turns are separate; extracted document facts are not described as personal facts;
  reminder outcomes separate created/completed/cancelled/skipped/expired,
  fired-awaiting-ack, and genuinely overdue-unfired states. Communication incidents are
  immutable observations, while normalized issue patterns have open/resolved lifecycle
  state and contextual turns; the backlog lists only open patterns and records resolutions.
  Behavioral instructions are distinct from categorization feedback, carry evidence or an
  explicit `legacy-unverified` label, and count as recurring only after two occurrences.
  Proactive sends are broken down by check, `ok` is the single successful trace status,
  and fallback output is structured/scrubbed rather than dumping provider response bodies.
  **Review-accuracy release (2026‑07‑20):** reminder results now show the complete
  period lifecycle (completed/cancelled/skipped/expired/snoozed) separately from the
  current overdue and fired-awaiting-ack snapshot. AI traffic separates functional
  calls from model-health probes and their cost. A low-level `llm.fallback` row means
  only that one model attempt failed; the report calls a backup successful only when
  the same trace has `llm.failover_served`, calls the whole chain failed only on
  `llm.failover_failed`, and labels older traces without either outcome as unknown.
  Correction/action-claim issue kinds are rendered as human Russian/English labels,
  never raw internal keys.
  **Durable outcomes + latency (2026‑07‑20):** a separate, content-free
  `note_outcomes` ledger records only chat id, stable note number, closed event
  label, time and provenance — never note text/summary/category. Confirmation,
  first real use, review/triage/resurfacing/reminder outcomes and used/unused
  deletion all write it. It is not telemetry and is not retention-pruned, so
  deleting an unused note cannot improve `capture_to_use_rate`; the denominator,
  deleted outcome and capture→first-use interval remain. A one-time migration
  backfills surviving notes and still-retained events. Explicit `stats`/`all`
  purge clears the ledger and its preview discloses the row count; the migration
  marker prevents an intentional reset being silently reconstructed next boot.
  Chat/embed request duration now populates `llm_usage.seconds`; reports show
  functional p50/p95 latency (and engineering exports show per-skill and health-
  probe latency separately). STT audio duration is not mixed into model latency.
  **Delivered‑or‑retried (2026‑07‑06):** the weekly review and the morning brief mark
  their slot done only **after a successful send** — a transient Telegram failure backs
  off 15 min and retries (up to 3 attempts, then a `sched_send_failed` issue), instead
  of silently skipping the week/day.
- **Daily DB backup (hardened 2026‑07‑10):** once per UTC day a durable job (`maintenance`/
  `db_backup`) snapshots `ingest.db` consistently (sqlite3 online‑backup API), keeps the
  newest `BACKUP_KEEP` (7) gzipped copies under `/var/lib/tg-ingest-agent/backups`, and
  encrypts every **off‑box** copy with AES‑256‑CBC/PBKDF2 (200,000 iterations) using
  `BACKUP_ENCRYPTION_KEY_FILE` before sending it to Spaces or the fleet notify chat.
  A missing key or failed transfer makes the durable job retry; plaintext is refused.
  With no target, the snapshot stays local and logs a WARNING. The passphrase recovery
  copy must live outside the VPS and repo. `BACKUP_ENABLED=false` disables.
  **No-leak / no-silence hardening (2026‑07‑25):** the raw `.db` snapshot and the
  half‑written archive are always removed (a failed gzip used to leave a full DB copy
  rotation could not see), the archive gets its rotation‑visible name only after a
  complete write (`.gz.tmp` → `os.replace`), and `rotate()` sweeps the stray files this
  module itself makes — `ingest-<stamp>.db`, `ingest-<stamp>.db.gz.tmp` and
  `ingest-<stamp>.db.gz.enc.tmp` (2026‑07‑26 wording fix: the sweep is scoped to that
  exact machine‑generated name form, never a hand‑made copy or its `-wal`/`-shm`
  companions). **Rotation now runs before encryption**, so a missing key file can no
  longer skip local retention forever, **and it also runs when the snapshot itself fails**
  (2026‑07‑26) — a nearly full disk is exactly what makes `conn.backup`/gzip raise, i.e.
  retention was skipped precisely when it was needed. Retention likewise counts and prunes
  ONLY `ingest-<stamp>.db.gz`: on the live box two hand‑made pre‑change copies were
  occupying 2 of the 7 slots, so five automated snapshots survived instead of seven. An off‑box copy blocked by the Telegram 45 MB cap logs a
  `backup_offbox_blocked` issue and reports the blocked state in the job result instead
  of looking green (plus a one‑time `backup_offbox_near_limit` warning past ~35 MB), and
  a **terminally failed backup tells the boss** once a day, then holds the retry for an
  hour (only TODAY's failures count — failed job rows live 90 days — and the "told him"
  stamp lands only after Telegram confirms delivery, so a blip doesn't swallow the notice
  and a permanent cause doesn't repeat it hourly). The UTC day is stamped only after a
  **successful** run — a failed morning is retried the same day instead of being marked done.
- **Monthly restore self‑check (2026‑07‑26):** nothing ever proved a snapshot could be
  turned back INTO a database — the job was green once the file was written and sent. A
  second durable job (`maintenance`/`backup_verify`, one per calendar month, tick
  `check_backup_verify`) now takes the newest snapshot through the real recovery path:
  decrypt with `BACKUP_ENCRYPTION_KEY_FILE`, gunzip, open read‑only, `PRAGMA
  integrity_check`, and confirm it is Cara's schema — in a `backups/restore-check/`
  scratch dir removed on every path (the daily sweep also clears one left by a killed
  run, since it holds a DECRYPTED copy). "Newest" is by stamp across both forms, taking
  the encrypted copy of THAT stamp. ANY failure — including an out-of-disk error while
  copying — logs a `backup_restore_failed` issue and holds the retry a day; the month is
  stamped only on success. It refuses to start when free disk cannot hold the expansion,
  and the gunzip is capped at that same budget — what a restore can legitimately produce,
  not what the disk could absorb — so the check can never be what fills the disk.
  **Honest limit:** the key file lives on the same droplet as the backups, so a green
  check means "archive and key agree here", not "the off‑box copy is openable after this
  box is gone" — that needs an operator‑held OFF‑BOX copy of the key (never automated,
  never printed by the code). The restore one‑liner is in SOLUTION.md §9.
- **Low‑disk alert (2026‑07‑25):** a `check_disk_space` scheduler tick (every 30 min)
  reads free space on the DB filesystem and tells the boss ONCE when it drops below
  `DISK_ALERT_MIN_FREE_PCT` (default 10%), with a `disk_low` issue row, and once again
  when it recovers past threshold + 2 pct. Same debounced state‑change shape as the
  model‑health monitor — a full disk breaks every write at once, so the warning has to
  arrive while there is still room to act. `DISK_ALERT_MIN_FREE_PCT=0` disables.
- **Proactive heartbeat:** gentle, suggestion‑only nudges — overdue reminders, memory
  candidates waiting, items needing a category — throttled (≤1 non‑urgent/day),
  quiet‑hours‑aware (22:00–08:00), fully audited; never acts. **A "sent" is recorded only
  on real delivery** (2026‑07‑02), so a transient Telegram error doesn't mark the day's
  nudge delivered and lose it. **One calendar (2026‑07‑26):** the daily cap and the
  "same nudge once a day" dedup bucket by YOUR local day, like quiet hours and off‑days
  always did — they used the UTC day, so with the +3 default the allowance rolled at
  03:00 local (nudges spent in the evening freed up in the middle of the night). **A persistent overdue reminder no
  longer starves the other nudges** — an already‑sent‑today hit is skipped, not treated
  as fatal, so a waiting candidate/uncategorized item still gets its turn.
  A delivered nudge snapshots its type + row ids for 15 minutes; a short «Давай»/«Да»/
  “show them” opens that exact memory/review/reminder queue deterministically.
  **The generic "unsorted pile" nudge was replaced (2026‑07‑17)** by the **note‑review
  invitation** — «Нашла N сохранёнок, по которым стоит принять решение — показать?» —
  which opens the exact snapshotted ≤3‑item review batch (untriaged items surface there
  as one of the deterministic review reasons); chat can never answer about an unrelated
  item or falsely say the queue is clean.
  A one-shot that already fired and is waiting for “готово” is not overdue and cannot
  generate another urgent overdue nudge.
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
  recovers. **Debounced:** hard access failures use `MODEL_HEALTH_CONFIRM_CHECKS` (default 2),
  while transient 429/overload/timeout failures require
  `MODEL_HEALTH_TRANSIENT_CONFIRM_CHECKS` (default 4). Provider response bodies never reach
  Telegram; reasons are reduced to bounded labels such as `temporary provider overload
  (HTTP 429)`, and transient copy explicitly says no operator action is needed. "Back" only
  fires if she actually announced "down". **2026‑07‑25:** each probe is capped at 10 s
  (independent of `LLM_TIMEOUT_SECONDS`) — the sweep runs inline on the one thread, so
  three models × 90 s used to freeze the bot for 4.5 minutes during exactly the outage it
  reports — and the warm `whisper-server` is probed alongside the chat models when
  `STT_MODE=local_server`, under its **own** alert wording: it is an on‑box unit, so the
  remedy she names is `systemctl restart whisper-server`, and she only claims to be
  "holding on a backup" when the `whisper-cli` binary AND its model file are really on
  disk. Because that probe is free, it keeps running while the budget is stopped (the
  paid model probes are skipped then, since they would all "fail" for a spend reason).
- **Systemd watchdog (2026‑07‑25):** the agent sends `READY=1` at startup and `WATCHDOG=1`
  as it works; the unit sets `NotifyAccess=main` + `WatchdogSec=900` on a `Type=simple`
  service. A **wedged** poll loop used to report `active (running)` forever — silence was
  the only symptom. The pings are at two levels: coarse ones at the loop top, each
  scheduler tick and each update, and — the ones that make the budget a real number —
  **fine ones inside the long primitives**: every `llm.chat`/`llm.embed`/`llm.transcribe`,
  each whisper‑server attempt and its CLI fallback, and each job inside `runtime.drain`.
  So the budget has to exceed the longest **un‑pinged span**, which is ONE bounded wait
  (the largest being a cold transcription: `STT_LOCAL_TIMEOUT_SECONDS` + ffmpeg) — not a
  whole update or scheduler tick, which nobody can put a number on (a routed turn is
  router + converse + embed, each with primary+fallback × 2 attempts × `LLM_TIMEOUT`, and
  one drain runs up to 5 durable jobs). **Honest limits:** raising
  `STT_LOCAL_TIMEOUT_SECONDS` above ~780 breaks the arithmetic — she logs a startup
  WARNING naming both numbers when you do, and since 2026‑07‑26 the same check covers
  `LLM_TIMEOUT_SECONDS` **and `FETCH_TIMEOUT_SECONDS`** (an inline link fetch carries no
  progress ping at all and spans 2× that knob), naming EVERY knob that is over budget
  rather than only the largest — and a kill mid‑update would still be a
  SIGABRT, i.e. no dead‑letter (the same update replays after restart). Outside systemd
  the notify helper is a silent no‑op.

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
- **No side conversations** (owner decision 2026‑07‑06): warmth lives in *how* she
  responds — she never asks unprompted questions about your day/life/plans/feelings
  («как день прошёл?») and never opens topics you didn't bring up; an instruction or a
  close gets a warm confirmation and a full stop. You start personal conversations;
  she meets them.
- **One address form:** every template speaks to the boss on **«ты»** (2026‑07‑06 sweep,
  test‑guarded — mixed «вы» in system templates broke immersion), and mid‑conversation
  failure copy carries no tech‑speak («модель…» removed from `llm_error`/`stored_retry`;
  the model‑health/budget alerts stay technical by design — owner‑requested ops notices).
- **Never fabricates specifics** — IDs, numbers, trace codes, prices, dates, model
  names; if unsure she says so.
- **What you confirmed outranks what she inferred (2026‑07‑26).** Weekly consolidation
  pools confirmed and inferred facts and asks a fast model which of a duplicate group to
  keep — and that model judges by richness, so it regularly kept the long *inferred*
  paraphrase and demoted the fact you had confirmed to `merged`. After that every
  “confirmed wins” guard downstream defended the guess instead. Now a confirmed item is
  only ever folded into another CONFIRMED item; if the model picks an unconfirmed keeper
  for a group that holds confirmed facts, the highest‑confidence confirmed one takes over.
  The same reply is also made self‑consistent before it can touch anything: an id another
  group is keeping is never dropped (`{keep:5,drop:[6]}` + `{keep:6,drop:[7]}` used to
  fold 6 while 7 was being folded into it — i.e. every richer copy gone at once).
  **Rotating batches (2026‑07‑26):** that pass groups in 40‑item batches (the fast model
  misses duplicates in a 120‑item wall), and the cuts used to fall on the same indexes
  every run — so a duplicate pair either side of a boundary was re‑separated week after
  week and could never fold. The batch offset now rotates with the run date, so a pair a
  boundary split this week is compared next week; it stays deterministic (same date →
  same batches), never randomized. **The flip side, said plainly:** «почисти память» run
  again on the SAME day reproduces that day's cuts — the next day, or the weekly pass,
  is what moves them.
- **She never rewrites a saved note behind your back (2026‑07‑26).** If you EDIT a
  message she has already saved, she says which version she is holding and asks before
  applying the new text (see §10).
- **Action‑truth:** she won't claim a real task was done unless the code did it; every
  rendered template is checked in production and a catalogue‑wide test requires every
  "done/saved/scheduled" template to declare its lifecycle state.
- **Persona sits below the hard rules (structurally):** the live prompts that actually
  reach the model (`converse.CHARACTER`, the router/ingest system prompts) write the
  security, no‑fabrication, no‑fake‑action and no‑invented‑specifics rules **at the top**,
  above the persona voice and her changeable life — so charm can never precede or override
  safety, confirmation, or truth. (The old `persona.py` layer‑order *table* was inert —
  nothing assembled prompts from it — and was removed 2026‑07‑02; the enforcement was
  always the prompt content itself.) **The operative persona is code (2026‑07‑26):**
  `converse.CHARACTER` (warm) + `hermes.PERSONA` (business) are what reach the model;
  `prompts/cara_persona.md` is descriptive copy that nothing loads at runtime, and now
  says so in its header — change the code first and mirror the wording there.
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
   ├─ hermes.py        her business register: ACTIONS domain + Hermes PERSONA + HermesMixin
   │                   (KB ask/fetch, budget_set, review, export)
   ├─ notes_svc.py     NotesMixin: notes/inbox handlers — lists, detail, show media,
   │                   discard/recategorize/merge, purge (typed phrase), journals, problems
   ├─ reminders_svc.py ReminderMixin: create/list/cancel/reschedule/rename/undo, partial
   │                   drafts, fired follow-ups, the fire/expiry sweeps
   ├─ ingest.py        parsing, UTF-16-safe URL extraction, category+facts+summary
   ├─ journals.py      structured journals: closed entry-type registry (gratitude active),
   │                   payload validation (lexical support), extraction, stats, md export
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
   ├─ backup.py        daily consistent DB snapshot: local rotation + encrypted off-box
   │                   copy (Spaces or fleet notify chat), as a durable daily job;
   │                   monthly restore self-check (decrypt → gunzip → integrity_check)
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
  `memory_curator`, `memory_consolidate` (the dead `review_balanced` was removed
  2026‑07‑25 — the weekly review is deterministic and nothing requested it).
  Failover to a fallback model on error/
  invalid‑JSON, with per‑model cooldowns. The default fallback is an **accessible
  open‑weight slug** (`openai-gpt-oss-20b`), not the tier‑403 `openai-gpt-4o` that used
  to be a dead fallback on a fresh deploy; a profile added via `LLM_PROFILES_JSON`
  without a `primary` is backfilled with the configured chat model so it can't crash a turn.
  A malformed `fallbacks` is coerced the same way (2026‑07‑27): a bare string is wrapped
  into a one‑element list (it used to iterate per CHARACTER — one doomed call, cooldown row
  and unpriced warning per letter), any other scalar is dropped to `[]` — both logged.
  Every failover chain records its terminal result: `llm.failover_served` only after a
  fallback produces a usable response, and `llm.failover_failed` when no configured
  model does. `llm.fallback` remains the per-attempt failure signal. **2026‑07‑25:**
  the profile and pricing tables are parsed once per process (memoized on the config),
  and a **truncated/unparsable response body is treated as transient** — one retry of
  the preferred model and a short bench, instead of the full cooldown a hard 403 gets.
- **Budget‑guarded:** every chat/STT/embedding call is priced and logged to
  `llm_usage`; daily/monthly caps warn at 80% and **hard‑stop** at 100% (above
  failover). Caps are overridable at runtime via `budget_set` — **including
  `0`, which disables that cap** (2026‑07‑25: a numeric 0 used to be swallowed and
  answered "I couldn't read the amount"). **A response missing its
  `usage` block is metered from text length** rather than logged as $0,
  and a billed‑but‑empty response is metered before it errors — so an under‑reporting
  model can't quietly slip the meter past the "enforced" cap. The estimate is
  **script‑aware** (≈2 chars/token for Cyrillic, ≈4 for Latin; 2026‑07‑25) and is
  recorded on the trace as `llm.usage_estimated` so a guessed row is visibly a guess.
  Successful/billed chat and embedding responses also store measured wall-clock
  request duration in `seconds`; latency summaries use chat/embed only and keep
  model-health probes separate from functional calls.
- **Unpriced model slugs are loud (2026‑07‑25).** A slug missing from
  `DEFAULT_PRICING`/`PRICING_JSON` bills at the punitive $3/$15 default — that is what
  budget‑locked Cara on 2026‑06‑19 (phantom dollars, then a refusal to work), and
  nothing detected it. Now: a **startup warning** naming every configured slug that is
  missing (`DO_CHAT_MODEL`, `ROUTER_MODEL`, `VISION_MODEL` and every
  `LLM_PROFILES_JSON` primary/fallback), a **`llm.unpriced_model` trace event + log on
  first billing** of such a slug per process, and a **`(default-priced!)` flag** beside
  that model in the spend report (chat rows only — STT is per audio minute and
  embeddings have their own rate).

### Voice (STT)
- DO has no transcription model, so Cara runs **whisper.cpp locally**: a warm
  `whisper-server` (`STT_MODE=local_server`, OpenBLAS, `ggml-small-q5_1`) keeps the
  model resident (~12 s/note on 1 vCPU). Language is **pinned to Russian**
  (`STT_LANGUAGE=ru`) to avoid wrong‑language hallucinations. (These two are set in
  the box env; the code defaults are `remote` / `auto`.) a non‑speech
  hallucination filter ("[Subscribe]", "[Music]", "Спасибо за просмотр"…) and a
  too‑big (>20 MB) message keep garbage out of dispatch.
- **STT_MODE is validated at startup (2026‑07‑25)**: only `local` / `local_server` /
  `remote`: an unknown value used to fall through to `remote`, so a typo would have
  shipped the boss's private voice audio to an off‑box endpoint. **Outage resilience:**
  an unreachable `whisper-server` is retried once (~3 s — its unit restarts in 5 s) and
  then served by the co‑installed cold `whisper-cli`, and the server itself is now part
  of the model‑health sweep (same debounced down/recovered alerts as a chat model).
  **2026‑07‑26:** a server that ACCEPTS the connection and then never answers (OOM
  thrash on a 4 GB box) now falls back to the CLI as well — that timeout raises
  `socket.timeout`, an `OSError` rather than a `URLError`, and used to be terminal, so
  the voice note was refused while a working transcriber sat next to it. It is not
  retried first: one full `STT_LOCAL_TIMEOUT_SECONDS` has already elapsed.
- **Stored recordings are metered with their real length (2026‑07‑25):** `files` keeps
  Telegram's `duration`, and `read_media` passes it to `transcribe`. It used to pass 0,
  which bills any recording as a single second in remote mode. Where the duration is
  unknown (legacy rows, and any audio sent as a *document* — Telegram attaches no
  duration to those) the size estimate applies **only to OGG/Opus voice** (~3.5 KB/s);
  a `.wav`/`.mp3` returns 0 rather than being over‑billed 5–50× by a bitrate that isn't
  its own.
- Only the **boss's own voice notes** are transcribed on arrival (commands/questions);
  forwarded voice/audio/files are stored unparsed — **but on request** ("что в этом голосовом?",
  "разбери файл", "read this file") the **`read_media`** action fetches the most recent
  forwarded voice/file and shows its **content**: a voice/audio note is transcribed (whisper),
  a PDF/text file's text is extracted — never metadata or trace ids. **Naming a
  note pins the target (2026‑07‑25):** «что в файле из #12» reads a file on #12 or
  says that note has none — it no longer falls back to the recent‑files list and
  read an unrelated file as if it answered the question. Only an id‑less request
  (or one whose note number is unreadable) uses the most recent file.

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
- **Crash‑loop containment (2026‑07‑25):** a SQLite failure — a full disk is the
  realistic case — anywhere in the inbound path used to leave the poll loop, and
  systemd's 10 s restart hit the same failing write forever: Cara was permanently
  and *silently* dead. Now the whole per‑update body (durable‑inbox bookkeeping,
  trace start, AND the dead‑letter ledger writes) runs under a `sqlite3.Error`
  guard that logs, pauses 5 s, and stops the batch **without advancing the offset**
  — at‑least‑once redelivery is preserved and the process survives. The offset
  write is guarded the same way (a lost offset costs one redelivery; the durable
  inbox dedupes). As a last resort `main()` catches a "disk is full" error, sends
  ONE Telegram alert (`db_full_fatal` — sending needs no disk, so a dying process
  can still be honest), waits 5 min so restarts stay paced, and exits. A disk‑full
  error raised by the *handler* takes the containment route as well, so a full disk
  cannot burn an update's retry budget and dead‑letter a perfectly good message;
  every other SQLite error **raised by the handler** still dead‑letters, so a poison
  update can't wedge her. An error raised by the *bookkeeping* has no dead‑letter
  route by definition — the ledger is what broke — so after 12 consecutive
  containment breaks (≈1 min, `DB_STALL_ALERT_AFTER`) Cara sends ONE direct
  `db_stalled` alert (no `reply()`, no `lang()` — both touch the DB) and latches it
  until an update goes through again. Without that, a *persistent* non‑disk‑full
  failure — a read‑only remount, lost file permissions, a corrupted image — left
  `systemctl is-active` reporting `active (running)` while she was permanently deaf
  and completely silent.
- **Startup writes nothing at steady state (2026‑07‑25):** `open_db` used to rewrite
  every `memory_candidates` row and the gratitude category row on EVERY start, and
  `Agent.__init__` then re‑stamped every seeded self‑fact — so a full disk blocked
  STARTUP too and the crash loop could never limp back up. Both backfills are now
  condition‑guarded (`WHERE …IS NULL`; the journal self‑heal reads before writing)
  and `self_fact_set` reads before writing, so a repeat start performs zero writes.
  Editing `SEED_FACTS` still overwrites, and `self_facts.updated_at` now means "when
  the fact actually changed".
- **Atomic migrations (2026‑07‑25):** `_migrate` runs inside one
  `BEGIN IMMEDIATE`/`commit`. Python's legacy transaction control autocommitted DDL
  while paired backfills waited for the end‑of‑open commit, so a crash between an
  `ALTER TABLE` and its backfill left a column that existed but was never filled —
  and its `if "x" not in columns` guard then skipped that backfill forever. Now the
  step is all‑or‑nothing and the next start retries it cleanly.
- **A dead‑lettered message is announced (2026‑07‑25):** when an update exhausts
  `UPDATE_MAX_ATTEMPTS` its payload is kept and an issue logged — and Cara now also
  says so (`update_dead_letter`), instead of the message just vanishing from the
  boss's side. Best‑effort: a failed notice never changes the dead‑letter outcome. It
  is sent after the turn is already over, so it speaks the language of the message that
  failed, not the stored default. **Allowlist‑gated (2026‑07‑25, documented 2026‑07‑26):**
  the owner check lives inside `handle_update`, i.e. after the raw chat id was captured,
  so an update that failed BEFORE that gate used to draw a reply in Cara's voice into a
  stranger's chat; the notice now goes only to allowed chats (the update is dead‑lettered
  either way). The disk‑full alert's send loop likewise survives a non‑JSON HTTP reply
  (captive portal / proxy error page) instead of replacing the honest disk‑full exit with
  a traceback.
- **A redelivered save repairs itself (2026‑07‑25):** filing a forward writes the
  message row first and downloads its media afterwards, so a crash in between left a
  text‑only note — and the redelivery hit `ON CONFLICT DO NOTHING`, was logged as
  "skipping redelivered message", and lost every attachment and URL while the boss saw
  a normal confirmation. The redelivery now **adopts** the existing row and backfills
  whatever is missing (idempotent on Telegram's `file_unique_id` / the URL), then
  resumes the suggestion pipeline if the note never got that far — without re‑logging
  what the crashed pass already recorded. A note that already reached a
  suggestion/confirmation only gets its missing media back; it is never re‑suggested —
  and since 2026‑07‑26 those backfilled pictures also get their durable off‑box copy
  (that branch returned before the offload). **A picture whose FIRST download failed is
  recovered too (2026‑07‑26):** the failure stored the image row with no local file, and
  a row that exists counted as "already there", so no redelivery ever fetched it; the
  repair pass now re‑downloads exactly those rows and updates them in place (never a
  second row — duplicating on every redelivery is the one thing this path must not do).
  The saved counts also describe the note itself now, so an uncompressed image sent as a
  **document** is finally reported («фото: 1») instead of as nothing at all.
- **Performance & small‑correctness sweep (2026‑07‑26):** eleven small things that only
  bite once the numbers grow or a dependency misbehaves. A «#N» lookup has its own
  `note_no` index (the composite one leads with `chat_id`, so every note reference was a
  full table scan — and a bulk «удали #3 #7 #12» ran one per id). Journal sizes are
  COUNTed in SQL: `len()` of the row helper capped at 200, so past that every digest and
  page header said exactly «200» forever — and opening a journal now fetches its page
  ONCE instead of twice (the render still costs that one Python scan of the confirmed
  notes; only the duplicates were removed). Candidate dedup gained an indexed `norm_text`
  fast path, so re‑proposing something already stored tokenizes nothing; a genuinely new
  proposal still walks the rows of its own `kind` (memory is never pruned, by policy).
  A `message_reaction` update now records its chat id (its chat sits
  at the top level, so those inbox/event rows were written with `chat_id` NULL). A
  calendar token Google refuses is dropped and re‑minted ONCE instead of
  failing from cache for the rest of its ~58‑minute life — a 401 always, a 403 only when
  Google's own words do not say the problem is volume, because throwing away a good token
  on a rate‑limit 403 would quadruple the traffic aimed at a service that just asked for
  less. Calendar errors now carry Google's own (secret‑scrubbed, bounded) description.
  `events.py` gained jobs.py's startup reclaim, an error recorded on `fail()`, a retry
  backoff so one blip cannot burn both attempts at once, and a claimed‑row dict that
  describes the claim — before Stage C moves live dispatch onto it; `jobs.claim_next`
  got the same corrected dict. The optional Space upload
  wraps bare `TimeoutError`/`ConnectionResetError`/`IncompleteRead` (they are not
  `URLError`, and this sits on the LIVE save path), and `offload` now logs and files
  ONE issue per failing save (not one per photo — a 10‑photo album would have flushed
  the bounded recent‑issues list) instead of costing the boss his save — while still
  re‑raising a `sqlite3` failure, which belongs to the crash‑loop containment guard.
  `sendDocument`/`sendPhoto` — and the file DOWNLOAD on the ingest path, where «file is
  too big» is the line that matters — report Telegram's `description` and `retry_after`
  like every other call. The weekly review's "exact median" is a real median (it took the
  upper‑middle of an even sample). The retired 1..N numbering helper is deleted, and so
  is the capped journal row helper that nothing but a test still called. And the
  409/rate‑limit poll backoffs wait in ≤1 s slices that check the stop flag, so a SIGTERM
  during a Telegram incident is noticed at once instead of up to two minutes later.
- **Per‑turn context dies with its turn (2026‑07‑25):** the quoted/replied‑to message,
  the reply‑bound reminder and the reply language are cleared in a `finally` at the end
  of every update. They used to survive until the *next* inbound message, so a
  background retry sweep re‑ingested an old note against a quote the boss never attached
  to it, and the language of one update leaked into the next one's replies (a voice note
  was even echoed back with the previous turn's language header).

---

## 6. Data model (SQLite, WAL)

Core inbox: `messages` (ingest lifecycle `pending → suggested → confirmed`,
`failed`/`duplicate`; forward origin, dates; plus the separate **knowledge
lifecycle** 2026‑07‑17: `knowledge_state` inbox/active/archived, `note_purpose`,
`saved_reason`, `review_at`, `expires_at` advisory, `use_count`/`last_used_at`
real-use counters, `archived_at`/`archive_reason`) · `urls` · `images` · `files` (any attachment by
file_id) · `facts` · `chunks` (BGE‑M3 embeddings) · `categories` (Cyrillic‑safe;
`kind` = `inbox`|`journal`) · `journal_definitions` (2026‑07‑17: slug‑stable
structured‑journal entities — entry type from the closed code registry, linked
category, sensitivity, per‑journal prompt opt‑in + validated `prompt_config_json`)
· `journal_entries` (one per source message — UNIQUE `message_id`, `occurred_at`,
validated `payload_json`, `extraction_status`; deletion cascades **manually**
through `delete_message`/purge, never FK pragmas) · `note_outcomes` (content-free,
durable capture/use/triage/delete ledger keyed by stable note number; deliberately
no message FK, never retention-pruned) · `reminders` (incl.
`prev_due_utc`, `closed_at`, `close_reason`) + `reminder_events` lifecycle log ·
`feedback` · `preferences` (identity/config + budget overrides) ·
`pending_actions` (TTL) · `conversation` (every turn, never pruned) · `kv`.

**Conversation turns are stored to 4096 characters (2026‑07‑26)** — Telegram's own
message maximum, so for anything he TYPES a stored turn is the WHOLE turn and the
verbatim readback `recall_conversation` promises is literally true. The cap was 1000: a
long pasted spec or forwarded post was clipped on the way in and then read back as his
words, silently. An `edited_message` rewrite uses the same cap, or editing a typo in a
long message would have truncated the turn that was already stored in full. It applies
to new writes only: **turns already clipped at 1000 stay clipped** — the rest of those
messages was never stored and cannot be recovered.

**The one thing 4096 still clips: a long VOICE note.** Text and captions cannot exceed
Telegram's own limit, but a transcript can — `handle_update` replaces the message text
with what Whisper returned, and nothing on that path caps its length (a dictation may
run for minutes; ~5 minutes of Russian speech already passes 4096 characters). Such a
transcript is stored up to the cap and the tail is lost, so "the whole turn" is a
promise about typed messages, not about a long dictation.

The honest cost of the change: every prompt that REPLAYS this table pays it — the
router (14 turns), warm conversation (20), the referential context prepended to each
ingest (8) and the daily memory curator (12). None of them clips a row, so the replayed
history is bounded by turn COUNT only and can be up to four times larger than the old
1000‑char clip produced. That is not a rare case: **forwarded posts are her ordinary
input and every forward's text is stored as a turn**, so a forward‑heavy day is exactly
the long‑turn run. At the far end of that range it is a context‑window risk, not only
input tokens against the daily budget. Deliberate: storage stays verbatim because the
readback is what this table is for; if the cost ever shows up, the surgical fix is a
per‑row clip at the router/converse call sites, not a smaller cap here.
`recall_conversation` was already bounded separately (7000 characters of rendered
transcript, most‑recent‑first) and that bound is unchanged — but it now buys **fewer
turns**: under two maximum‑length turns where the old cap fitted six, with the oldest
surviving turn sliced mid‑word and unmarked. The readback got truer per turn and
shorter per conversation.

Spend & reliability: `llm_usage` · `model_cooldowns`.

Personality & memory: `self_facts` · `boss_profile_items` (status + sensitivity + evidence) ·
`memory_candidates` (evidence/source trace/recurrence/first+last seen) ·
`relationship_events` (title + trace) · `cara_life`.

Observability: `traces` · `trace_events` · `issues` (immutable incident observations) ·
`issue_patterns` (normalized open/resolved/legacy lifecycle, counts, resolution + context) ·
`events` · `jobs` ·
`proactive_log`.

Cascade deletes + purge scopes keep rows and media consistent. **`llm_usage` (spend
history) and `preferences` (identity) are never purged.** The user-facing note number
is a **stable `messages.note_no`** — assigned once, monotonic, never reused, with
permanent gaps on deletion (it never alters the internal row id that attachments/
embeddings/memory reference). **Reminder numbers** are different: a **contiguous 1…N
display position** in the active list (due order, from the stable `reminders.id`) that
compacts on fire/cancel.
Normal note/category/message deletion preserves `note_outcomes` so historical KPIs
cannot be gamed by deletion; explicit `stats` and `all` purge preview and clear it.

**Nothing identity‑bearing is recycled (2026‑07‑25).** `note_no` comes from a durable
per‑chat kv counter (`note_no_next:{chat}`), seeded once from live rows *and* the outcome
ledger, so from the first claim onward a deleted number is never handed out again (the
single residual hole is the seed itself, on a database whose numbers pre‑date the
counter — see §2). `delete_message` also drops that
message's id‑keyed kv state (`capture_action:{id}`, `journal_draft:{id}`,
`note_edit:{id}` — the text staged by an unanswered edit offer) — SQLite reuses
the highest rowid, so a new note used to inherit a deleted note's reminder draft or
journal payload. **kv VALUES carrying row ids got the same treatment (2026‑07‑26):** the
note‑review snapshot and the resurfacing pointer store the stable `#N` beside each
`messages.id` and re‑check it on resolve, so a reused rowid can no longer answer for a
note he was shown (an ordinal that resolves to a mismatch is a not‑found); the whole‑table
purges drop those pointers outright, because scope `all` restarts the rowids AND the `#N`
counter, and a reminders purge drops `fired_reminder_msgs`/`last_reminder_id` so a reply
to an old alarm cannot bind to whatever new reminder inherited its id. The kv sweeps also
escape `_` (a single‑character wildcard in `LIKE`) so a prefix matches only itself. The
snapshot is also written from what the review card actually RENDERED: accepting a
proactive nudge («Давай») used to rebuild it from the queued ids instead, so a note the
card had dropped — or every note, when the card was never delivered — could still be hit
by «второе в архив». **The shown‑today ledger got the same pinning (2026‑07‑27):**
`note_review_shown:{day}` held bare rowids, so «delete the highest‑rowid shown note,
save a new one» silently excluded the brand‑new note from every review batch for the
rest of the day — it now stores `{id, no}` pairs and drops an entry whose `#N` no
longer matches. Every write to `chunks` bumps a `vec_gen` counter and drops the decoded
vector cache: the old `(count, max_id, sum_id)` fingerprint collided under rowid reuse,
and retrieval kept grounding answers in a DELETED note's chunks while the new note stayed
invisible. (The legacy JSON→blob embedding conversion in `_migrate` bumps it too — it
rewrites rows without changing an id.) **And since 2026‑07‑27 a category rewrite bumps
it as well** (`confirm_category`, `merge_categories`): the cached rows carry each
chunk's category beside the vector, and a messages‑only UPDATE was invisible to the
chunks‑derived fingerprint — after «перенеси #12 в Крипту», `ask` kept rendering the
citation head and the grounding block with the OLD category until the next chunk write
anywhere in the DB. Inbound `conversation` rows carry their Telegram
`update_id` under a partial unique index, so an at‑least‑once redelivery cannot make the
boss repeat himself in the history or in prompts — **and since 2026‑07‑26 their
`tg_message_id` too**, which is what lets an `edited_message` rewrite that exact turn.
`cara_life` gained a `status` column the same day: consolidation folds a duplicate beat
to `merged` instead of DELETEing it (all readers filter `status='active'`; the seed
marker `life_count` deliberately still counts every row).

**Two lookup indexes and one derived column (2026‑07‑26).** `idx_messages_note_no_only`
serves the owner‑global «#N» lookup (`idx_messages_note_no` leads with `chat_id` and could
not); `memory_candidates.norm_text` (casefolded/stripped copy of `proposed_text`, backfilled
once on upgrade, indexed) is the dedup fast path. Both are additive — no data moves, and a
row written without `norm_text` stays visible to dedup through the scanned arm — which is
also why the fast path is only that: an EXACT‑duplicate hit that lets the scan stop early.
Journal sizes now come from `COUNT(*)` helpers (`journal_count`,
`journal_entries_count_for` — which keeps the row helper's `JOIN messages` so an orphaned
entry could never make the header out‑count the listing) rather than `len()` of a capped
row fetch. Memory rows are still **never pruned**, by policy — the fix here is the cost per
lookup, not the size of the table.

**A purge deletes what it says, and only that (2026‑07‑25).** `categories.kind` is the
single source of truth for journal protection, so scope `stats` now deletes only
`kind != 'journal'` rows — «сбросить всю статистику» no longer silently demotes a diary
to an ordinary category (only the built‑in gratitude journal used to self‑heal at the
next start; any other diary lost its protection permanently). Scope `all` scrubs
`telegram_updates.payload` on every non‑pending row (`'{}'`, row and `update_id` kept as
the redelivery dedupe key): the durable inbox held a verbatim copy of every message, and
only `done` rows are retention‑pruned, so a dead‑lettered one outlived «удали всё» and
rode along in the off‑box backups. **The same statement covers `last_error` since
2026‑07‑26** — a failed row keeps up to 1000 chars of the exception, which is routinely
the offending text quoted back, so the scrub sets it to NULL and the WHERE clause picks up
a row that has only the error left. Still‑`pending` rows are the one exception — they are
unprocessed work the startup replay must read, and the turn that types the confirmation
phrase is itself `pending`, so its own copy survives the purge it triggers (a later purge
scrubs it, once it reaches a terminal state). `events.payload` / `trace_events.data` carry
ids, stage names and counts, never message text; the free‑text `trace_events.message` /
`traces.summary` can hold an exception repr that quotes the offending text, but those are
truncated (500/200 chars) and retention‑pruned — accepted residue, not a second archive.
And the fast whole‑table note wipe now writes the same `deleted_used`/`deleted_unused`
ledger rows as the per‑id path, so the saved‑to‑used KPI no longer depends on whether a
journal happens to exist. The emptiness guard in `do_purge` counts the scrub too: a
database whose only remaining content is dead‑lettered inbox rows must not answer
«удалять нечего». **(2026‑07‑27)** Scopes `all` and `reminders` now delete **every**
reminder row, not only the active ones — a closed reminder keeps its verbatim title
forever (closing only flips status; no user‑facing path deletes the row), and
`reminder_events.detail` carries titles too, so «удали всё» left both in the DB and in
every off‑box backup. The preview discloses the closed count as its own line, the
emptiness guard counts it too (a DB whose only reminder content is closed rows gets the
offer, not «удалять нечего» — the exact state the fix targets), the ON
DELETE CASCADE takes `reminder_events` with the rows, `reminder_events` older than
`TELEMETRY_RETENTION_DAYS` are now retention‑pruned like the other telemetry (the
weekly review reads at most a month of them), and the edited‑command pointers of kind
'reminder' (`turn_artifact_msgs`) are dropped with the rows they describe — an edit of
the old «напомни…» message after the purge must not claim the deleted reminder kept
its details.

---

## 7. Security & safety

- **Owner‑only** access on both chat and sender id, for messages, reactions, buttons.
- Closed router action set; JSON‑only router output; untrusted‑content delimiters for
  forwarded/quoted text and stored notes (prompt‑injection defense); confidence gate.
  **Forwarded content in the conversation log is fenced too** (2026‑07‑02): a forward
  is stored `source='forward'` and replayed into the router/converse prompts as DATA,
  never as the boss's own instruction — so a forwarded post can't inject via history.
- **The fences are unforgeable (2026‑07‑25, review WP8).** A fence only holds if the
  content can't write one itself. Two shared sanitizers in `common.py` now defang every
  untrusted string before it reaches a prompt: `neutralize_fences` (keeps line structure,
  collapses a forged `=== … ===` delimiter line to `—` and drops literal
  `<message>`/`<entry>`/`<user_request>` tags) and `neutralize_untrusted` (flattens to one
  line with ` · `, strips leading `user:`/`Босс:`‑style role labels). Applied to saved
  notes in the **ask** prompt, the converse grounding block, the router's recent‑
  conversation rows and `<user_request>` fence, the replied‑to/quoted message, replayed
  forwarded turns (`store.convo_replay_text`), the memory‑curator transcript, and the
  ingest/journal `<message>`/`<entry>` payloads. **Saved notes also left the system role**:
  the ask prompt now carries them in their own user‑role DATA turn, so even a successful
  escape lands in data, not in system‑role authority. Nothing is censored — the words
  survive verbatim, they just can't impersonate a delimiter or a turn.
- **Sanitizer edges closed (2026‑07‑27 review).** The fence‑tag strip is a fixpoint loop
  (a nested `</mes</message>sage>` used to reconstitute an intact terminator in one pass);
  the invisible‑character set covers the bidi isolates U+2066–69 plus U+061C/U+180E — and,
  since the same‑day finalize pass, the rest of the default‑ignorable block: U+2065, the
  deprecated format controls U+206A–6F, the interlinear annotation controls U+FFF9–FFFB and
  the invisible tag characters U+E0000–E007F (the classic hidden‑instruction smuggling
  channel; known trade‑off — a subdivision‑flag emoji loses its tags and renders as a plain
  black flag, while ZWNJ/ZWJ and the emoji variation selectors deliberately survive); every
  rendered‑equals look‑alike (U+FF1D/U+FE66/U+A78A/U+2550) folds to `=` before the fence
  rules. Inside an **ask** note body a dashes‑only line and a `[#…`‑shaped line start are
  defanged, so a saved note can't forge a sibling note block or steal another note's `#N`.
  Stored boss‑memory/life texts (standing guidance, operating model, life facts, the
  preference hint) are flattened to one line per fact before they reach the converse/ask
  SYSTEM prompts — the one region with no fence at all; one over‑long profile row is now
  skipped (logged) instead of silently dropping every standing rule (`standing_guidance`
  also honours its `max_items=8`). The router history keeps the boss's own «…»: the
  guillemet rewrite applies only to the forwarded row the prompt itself wraps in «…».
- **Fetch SSRF guard:** http/https only, no URL creds, every URL + redirect hop
  rejected if it resolves to a private/loopback/link‑local/reserved IP or the cloud
  metadata endpoint — and the socket is **pinned to the validated IP** (2026‑07‑02) so
  a rebinding host can't flip to a private address between the check and the connect.
- **Bulk purge** requires a typed confirmation phrase (handled before the router, so a
  stray "да" can't wipe data); pending actions carry a TTL and are swept when abandoned.
- **Truthfulness:** action‑truth guard + no‑fabrication persona rule. Free-form output
  is also fail-closed for claimed artifacts: conversation cannot name/present a file as
  attached because only deterministic document handlers own Telegram `sendDocument`.
- Secrets in `/etc/tg-ingest-agent.env` (0600), staged via files (never argv/journal);
  access keys redacted from logged HTTP errors. Dedicated bot token + DO key.
- systemd hardening: non‑root user, `NoNewPrivileges`, `ProtectSystem=strict`,
  `PrivateTmp`, writable only in `/var/lib/tg-ingest-agent`. **Tightened 2026‑07‑26:**
  empty `CapabilityBoundingSet`, `PrivateDevices`, `ProtectKernelTunables`/
  `ProtectKernelModules`/`ProtectControlGroups`, `RestrictNamespaces`,
  `RestrictSUIDSGID`, `LockPersonality`, `RestrictAddressFamilies=AF_INET AF_INET6
  AF_UNIX`, `SystemCallArchitectures=native` and `SystemCallFilter=@system-service`
  with `SystemCallErrorNumber=EPERM`. The filter is the only directive no test can prove:
  she forks ffmpeg/whisper‑cli (voice) and openssl (backup encryption), and a missing
  syscall fails the CHILD, not the service — so every deploy that changes it must be
  followed by a real voice note and a real backup, with the drop‑in escape hatch
  documented in the unit file itself. `EPERM` is there because the default action is
  SIGSYS: with `Restart=always`/`RestartSec=10` a killed main process would never trip
  systemd's start limiter, i.e. an incomplete filter meant an endless 10‑second crash
  loop rather than a unit that stops and stays visibly failed.
- **Operator scripts hardened (2026‑07‑26).** `bootstrap_chat_id.py` now REQUIRES the
  expected chat id (a sole pending private chat used to be enough, so whoever `/start`‑ed
  first could become the owner); with no argument it only lists candidates. It refuses to
  run while the service is polling and rewrites the env file atomically with a `.bak`. It
  reads the queue with a plain NO-offset `getUpdates` — the only form the Bot API promises
  consumes nothing — and reports honestly when the queue is deeper than that one page;
  the opt-in `--deep-read` reaches the end of a flooded queue with negative offsets and
  warns that, per the API, everything older is then forgotten.
  `apply_token.py`/`apply_do_key.py` validate the staged secret against the API BEFORE
  touching the env, append the line when it is missing (the old `re.sub` was a silent
  no‑op that still reported success), and keep the staged file when anything fails.
  The whisper STT server no longer runs as root (`DynamicUser=yes`), and its build and
  model can be pinned by ref + sha256 — both pins default to EMPTY (an unpinned build with
  a loud warning) until the live ref/digest are captured from the box and recorded in the
  PD‑VPS KB. The armed 2026‑07‑03 Cara/Nikki split one‑shot moved to
  `archive/2026-07-03-cara-nikki-split/` (executed once; must never run again); the staged
  copy at `/root/cara-nikki-split/` on the PD box is a pending box action, not yet removed.
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
`BUDGET_MONTHLY_USD=15.0` (runtime‑overridable) · `DO_CHAT_MODEL=deepseek-4-flash`
· `ROUTER_MODEL` · `DO_EMBEDDING_MODEL=BGE-M3` · `ROUTER_CONFIDENCE_THRESHOLD=0.6`
· `ASK_MIN_SCORE=0.25` · `UPDATE_MAX_ATTEMPTS=3`.

STT (code defaults shown; the box overrides the first two): `STT_MODE` (default
`remote`, box `local_server`) · `STT_LANGUAGE` (default `auto`, box `ru`) ·
`WHISPER_SERVER_URL` · `WHISPER_MODEL` · `STT_ENABLED=true`.

Schedules & proactivity: `REVIEW_WEEKDAY=0` (Mon) / `REVIEW_HOUR=10` ·
`MORNING_BRIEF_HOUR=9` (the opt‑in morning brief's earliest local hour; the brief
itself is off until he asks for it — §3) · `PROACTIVE_ENABLED=true` ·
`QUIET_HOURS_START=22` / `QUIET_HOURS_END=8` · `PROACTIVE_MAX_PER_DAY=1` ·
`PROACTIVE_INTERVAL_SECONDS=3600`.

Learning: `HABIT_THRESHOLD=10` — how many consecutive same‑category filings from one
source before she offers to auto‑confirm that source (§3; the offer still needs a yes).

Backup: `BACKUP_ENABLED=true` · `BACKUP_KEEP=7` ·
`BACKUP_ENCRYPTION_KEY_FILE=/etc/tg-ingest-agent-backup.key` (local gzip plus encrypted
off‑box copy; recovery key retained separately from the VPS/repo) ·
`DISK_ALERT_MIN_FREE_PCT=10` (low‑disk alert, 0 disables; added 2026‑07‑25).

Optional integrations (dormant until configured): `GCAL_CALENDAR_ID` /
`GCAL_SA_KEY_FILE` (Calendar) · `STORAGE_BACKEND=spaces` + `SPACES_*` (DO Spaces) ·
`FETCH_ENABLED` · `CATEGORIES`/`CATEGORIES_FILE`.

**The complete catalogue is `tg-ingest-agent.env.example`, and it is now enforced
(2026‑07‑26).** That file both documents every knob and *is* `/etc/tg-ingest-agent.env`
on a fresh box, so an undocumented key is a real capability loss on a rebuild rather
than a documentation nit: 23 keys `common.load_config` reads were missing from it,
including `VISION_MODEL` — empty means photos are handled text‑only from the caption,
so a host rebuilt from the example lost photo description with nothing said. The
"vision‑capable model id" comment also sat over `DO_CHAT_MODEL`, i.e. following the
example verbatim swapped Cara's MAIN chat model for a vision model. A guard test now
diffs the keys `common.py`'s config loaders read against the keys the example carries
(commented or active) and fails the suite in **both** directions — the same mechanical
shape as the installer `MODULES` guard, which has never drifted since it got one. It
also checks every documented **value**: each template line is loaded through
`load_config` (a documented `QUIET_HOURS_START=22:00` would otherwise sit there until
someone uncommented it and the service crash‑looped on `int()`), compared against the
code default unless it is on a named list of deliberate examples, and rejected if a key
is documented twice. The lists above stay as the short tour; they are not the catalogue.

---

## 9. Operations

- **Host:** PD‑VPS (`174.138.108.85`, SSH key‑only; connection details in the PD‑VPS
  KB). Service `tg-ingest-agent`; app `/opt/tg-ingest-agent/`; state
  `/var/lib/tg-ingest-agent/`. The former Pilot‑VPS is retired.
- **Deploy:** single‑connection `deploy.sh` (tar → test → install → verify) with an
  idempotent installer (backs up, preserves env, `py_compile` gate, restarts only when
  secrets are complete); `--pull` / `--rollback <sha>` supported. The installer stamps
  a content‑hash `VERSION` so Cara announces real code changes (not reboots). The
  remote scripts run with `pipefail` (2026‑07‑02), so a FAILED test run or a mid‑way
  installer abort fails the deploy instead of being masked by the `| tail` pipes.
  **Single source of truth (2026‑07‑26):** the tracked `tg-ingest-agent.service` is the
  unit the installer installs (`install -m 0644`) and the tracked
  `tg-ingest-agent.env.example` is what seeds a MISSING `/etc/tg-ingest-agent.env` —
  the installer's own copies of both are gone, so the unit can no longer drift from the
  file a human reads and the env template can no longer drift from the documented one.
  A reinstall still never touches a populated env file, and a freshly seeded one still
  trips the `=REPLACE_ME` guard that keeps the service stopped until the secrets are in.
  `--rollback` now rejects option‑shaped refs (`--rollback --pull` used to reach
  `git checkout --pull`) and verifies the ref resolves to a commit before checking out.
  The deploy payload NAMES the scripts it carries (`deploy.sh` + the two installers)
  rather than globbing `*.sh`, so a one‑shot left at the repo root can never ride onto the
  live box; `migrate-cara-to-pd.sh` is deliberately excluded and checked in a checkout.
  The stage dir is never wiped, so both one‑shots had already reached
  `/root/tg-ingest-agent-stage/` while the glob was in place — a real deploy (not
  `--test`) now deletes those two copies by name before installing.
- **Repo:** `git@github.com:promptinvest/tg-ingest-agent.git` (own deploy key); pushed
  after every commit.
- **Tests:** the full offline suite (no network; temp SQLite) — deliberately not a
  number here, because every count these specs have carried went stale within days
  (2026‑07‑26). Run on
  the box as part of every deploy and in GitHub Actions — including a
  **golden‑transcript harness** that replays end‑to‑end
  scenarios through `handle_update` (LLM scripted per skill, Telegram captured) and
  asserts replies, DB writes, and **no state change before confirmation**; an
  un‑scripted LLM call fails the scenario.
  **Suite hygiene (2026‑07‑26):** three tests asserted less than they claimed and were
  fixed — the "overdue reminder bypasses the daily proactive cap" regression planted its
  spent‑cap row without a day, so the cap was never actually spent and the test passed
  even with the bypass deleted (it now pins the day and asserts the inverse: a non‑urgent
  nudge in the same DB IS suppressed with the daily‑cap reason); the `ask`‑prompt test's
  honesty check was an expression that asserted nothing and now pins the refuse‑if‑absent
  wording the prompt really uses; and the event/job tests snapshot‑and‑restore the
  process‑global job‑handler registry instead of clearing it (clearing erased the handlers
  every constructed Agent registers, so later tests depended on test order). Three new
  standing guards, each against a failure that is silent by construction: no duplicate
  method name may shadow an earlier one inside a class (`setUp`/helpers included, not
  just `test*` — a shadowed fixture changes every test in the class); no conditional
  expression may pose as an assertion (`self.assertIn(…) if cond else None` checks
  nothing when `cond` is false — that was the `ask`‑prompt bug); and the CI unit job
  must stay time‑bounded (`timeout-minutes: 10`, asserted inside that job's own block
  so a second job's timeout can never stand in for it). GitHub Actions is **not** the
  whole gate — it runs one pinned CPython with no optional system packages, so the
  pdfminer path and parity with the box's distro `python3` are exercised only by the
  on‑VPS run in `deploy.sh`.
- **Observability:** journald (routing decisions with risk + confidence, per‑row
  lifecycle), `traces`/`trace_events`, `llm_usage` (spend), `issues` + `proactive_log`
  (behavior), weekly digest + trace‑summary export.
- **Footprint:** tens of MB RSS; disk a small fraction of the 48 GB volume. Free space is
  now monitored (2026‑07‑25): below `DISK_ALERT_MIN_FREE_PCT` Cara says so once and
  logs a `disk_low` issue; install‑time backups under `/root/codex-hardening-backups`
  are pruned to the newest 10 (each holds an env/secrets copy) and the root is `chmod 700`.

---

## 10. Known limits & roadmap

- **PDF text** uses pdfminer.six (apt `python3-pdfminer`, kept current by the nightly
  updater) with a stdlib regex fallback. **Scanned / no‑ToUnicode (glyph‑coded) PDFs**
  still yield no text layer — reading them needs **OCR**, out of scope here; such files
  are stored and re‑sendable. **Decompression bombs (2026‑07‑25):** a forwarded PDF is
  attacker‑supplied, and a few KB of crafted FlateDecode inflating to gigabytes would
  OOM‑kill this single‑process service (systemd then restarts it straight back into the
  retry). Two guards: the stdlib fallback inflates each stream to at most 4 × the char
  cap and reads at most 200 streams, and — because **pdfminer runs FIRST and inflates
  unbounded** — a pre‑scan refuses the whole document before pdfminer sees it if its
  streams inflate past **128 MB** in total (the scan only counts bytes, never keeps
  them). **2026‑07‑26:** the pre‑scan keeps measuring PAST the `endstream` marker when
  zlib says the stream did not end there — that regex is non‑greedy, so a payload
  CONTAINING the literal bytes «endstream» (trivial: a stored deflate block copies its
  input verbatim) made the scan measure a harmless prefix while pdfminer, which takes the
  length from the object dictionary, still inflated the whole bomb. **The same day's
  review corrected how:** the first version handed zlib «payload start → end of file» for
  every stream, and zlib copies back whatever it was given but did not consume, so the
  scan cost one copy of the rest of the document PER STREAM — a 20 MB forward of ~800 000
  tiny valid streams would have frozen the single thread for tens of minutes and then been
  killed by the watchdog. Input now reaches zlib in 64 KB windows, the past‑marker read
  only happens when a stream really does run on (a well‑formed PDF never does), and that
  read has its own 16 MB per‑document allowance. Honest limits: the pre‑scan reads the
  streams the stdlib regex can find, so a
  PDF that hides them from it still reaches pdfminer unbounded; a document that exhausts
  the past‑marker allowance is unverifiable and refused; and a genuinely huge PDF
  may be refused («не смогла прочитать») or read only up to the cap.
- **Voice transcripts** are discarded as Whisper noise only when the known hallucination
  phrases are essentially the WHOLE transcript (2026‑07‑25) — strip every phrase that
  matched and ≤ 15 characters may remain — so a real dictation that just mentions
  «спасибо за просмотр» is kept. Two trades, both deliberate: a long hallucinated credit
  line with extra words around it can now pass through as text, and a very SHORT genuine
  dictation wrapped in a hallucinated outro («спасибо за просмотр, купи молоко») is still
  discarded, because 13 characters of remainder are indistinguishable from credit‑line glue.
- **Voice is RUSSIAN‑pinned on the live box — English voice notes degrade (recorded
  honestly 2026‑07‑26).** `STT_LANGUAGE` defaults to `auto` in code, but the live box sets
  it to `ru`. That is a deliberate trade, not an oversight: with `auto`, Whisper guesses
  from the first moments of audio and a wrong guess is what produces the YouTube‑style
  hallucinations («[Subscribe]», «Спасибо за просмотр») the noise filter above then has to
  throw away — pinning one language removes that failure for a single‑language speaker.
  The price is that an ENGLISH voice note on this box is transcribed as if it were
  Russian and comes back mangled or empty. **Text stays fully bilingual** (`detect_lang`
  per message, ru/en templates, replies in the language he wrote in) — only the audio
  path is pinned. To take English voice notes, set `STT_LANGUAGE=auto` and accept the
  hallucination rate back.
- **Edited messages (2026‑07‑26) — what is handled and what is not.** Handled: the
  dialogue record, a note still in the inbox, and an ask‑first update of a note she
  already saved (§3). Deliberately NOT applied to the note — because the note's text was
  probably never derived from that message's caption, so writing the caption over it
  would destroy content rather than correct it. In both cases she now **says so** (one
  line naming the note) instead of staying silent:
  · a **note with a document** (PDF/.md/.txt): `finalize` stores the document's text
  layer and discards the caption, so a whole PDF body would become one line. This is
  decided by the FILE KIND, not by whether a text layer was really extracted — a
  **scanned** PDF has no text layer, so that note's text genuinely IS the caption and the
  edit is refused anyway. She cannot tell the two apart after the fact (a forwarded
  document keeps the forward's origin type, so there is no marker to read), and
  overwriting a contract with one line is the worse mistake. The workaround is to send
  the file again.
  · an **album**: its note was built from every part's text and links, and an edit
  carries exactly one part.
  · **the other parts of an album** (2..N) have no note row of their own, so editing
  those captions touches only the dialogue record.
  Not applied to the **dialogue record** either: a caption edit on a **voice/audio**
  message (its turn holds the transcript — a caption would put words there you never
  said), and **removing** the text/caption entirely (the turn keeps the last real words
  rather than going blank; the note is untouched too).
  Also unhandled: turns from **before this change** carry no `tg_message_id`, so their
  edits find no row to rewrite (nothing is invented — the edit is a silent no‑op); a
  **journal entry's extracted fields** (e.g. gratitude items) are **reset to
  unstructured, not re‑derived**, when a confirmed edit lands (2026‑07‑27 — they were
  extracted from the replaced text, and keeping them meant the stats and person filters
  kept answering with the old names; re‑extraction would be a model call inside a
  confirm path), and a journal row also records no entry in the note‑outcome ledger
  (journal entries are outside note lifecycle by design); a confirmed note's **key facts
  and summary are dropped rather than re‑derived** (same rule — renderers fall back to
  the note's own edited text); an edited **command** turn gets the honest one‑line
  notice above only for reminders and remembered facts (2026‑07‑27) — a calendar add or
  a category correction derived from an edited message still keeps its old details
  silently; and Telegram itself
  only delivers edits within its own edit window, so a very old message cannot be
  corrected at all.
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
