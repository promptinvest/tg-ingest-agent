#!/usr/bin/env python3
"""Action-truth guard (spec §0.1.3): persona must never claim a final action
('saved', 'scheduled', 'remembered', …) before the deterministic code path
that performs it has completed. Every rendered template is checked in
production, and the catalogue-wide test requires every final-action template
to declare its lifecycle state here.
"""
import re

FINAL_VERBS = {
    "saved", "filed", "scheduled", "remembered", "deleted", "purged",
    "synced", "fetched", "transcribed", "exported", "sent",
    # Russian finals
    "сохранила", "записала", "поставила", "запомнила", "удалила", "очистила",
    "выгрузила", "отправила",
}

# A free-form conversation turn has no attachment channel. These shapes are
# therefore proof that the model is pretending an artifact exists. The real
# review/export handlers upload through sendDocument and never describe the file
# in an ordinary sendMessage.
_BARE_ARTIFACT_LINK_RE = re.compile(
    r"\[[^\]\n]+\.(?:md|txt|pdf|csv|json|docx?|xlsx?|zip|ics)\](?!\s*\()",
    re.IGNORECASE,
)
_ARTIFACT_CLAIM_RE = re.compile(
    r"(?:\bвот\s+(?:твой\s+)?файл\b|\bфайл\s+готов\b|\bприкрепила\s+файл\b|"
    r"\bотправила\s+(?:тебе\s+)?файл\b|\bhere(?:'s|\s+is)\s+(?:your\s+|the\s+)?file\b|"
    r"\bthe\s+file\s+is\s+ready\b|\bi\s+(?:sent|attached)\s+(?:you\s+)?the\s+file\b)",
    re.IGNORECASE,
)

# -- free-form completion claims ----------------------------------------------
# A free-form `converse` turn cannot mutate state. These phrases are therefore
# unsafe when the model presents them as the outcome of the current exchange:
# only deterministic skill handlers may close/move/save/schedule something or
# claim that a queue is now clean. This is deliberately narrower than
# FINAL_VERBS (which validates deterministic templates): a QUESTION, a FUTURE
# offer («могу добавить», «давай добавлю?») and an honest denial («я её не
# добавила») must survive, so each of those is carved out explicitly below.
#
# PRODUCTION 2026-07-28 — why this is no longer one regex. Four fabricated
# confirmations reached the boss in one exchange and every one of them PASSED
# the old pattern:
#   1. «Готово — сохранила …» — the optional prefix was `(?:готово[,!:.\s-]*)?`
#      whose character class holds an ASCII hyphen; she wrote an EM DASH.
#   2. «#51 теперь «X» вместо «Y»» — a completed replacement with NO verb at all.
#   3. «- #51 — «…» — восстановила / - #52 — «…» — добавила» — the verb sits at
#      the END of a bullet line, and neither verb was in the alternation.
# So: punctuation is normalised BEFORE matching, the verb set covers the
# completed forms she actually writes, and verb-less completion SHAPES are
# matched in their own right.

# Typographic folding. Every dash variant becomes an ASCII hyphen, every exotic
# space becomes a plain space, zero-width joiners disappear — a guard that can
# be defeated by punctuation is not a guard.
# (escapes, never literals: an ASCII hyphen, a figure dash and an em dash are
# indistinguishable in a diff — mistaking one for another is the whole bug.)
_DASH_CHARS = ("\u2010\u2011\u2012\u2013\u2014\u2015\u2043\u2212"
               "\ufe58\ufe63\uff0d")
_SPACE_CHARS = ("\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
                "\u2007\u2008\u2009\u200a\u202f\u205f\u3000")
_ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\ufeff"
# Markdown emphasis. '_' is a WORD character, so «_сохранила_» defeated every
# `\b`-anchored verb while «**сохранила**» did not — Telegram markdown is a live
# formatting path for these replies, so italics cannot be a way through. '*' is
# deliberately NOT folded: it is also the bullet marker the outcome-list shapes
# anchor on, and being a non-word character it never breaks a boundary anyway.
_MARKDOWN_CHARS = "_~`"
_CLAIM_TRANSLATION = {ord(c): "-" for c in _DASH_CHARS}
_CLAIM_TRANSLATION.update({ord(c): " " for c in _SPACE_CHARS})
_CLAIM_TRANSLATION.update({ord(c): " " for c in _MARKDOWN_CHARS})
_CLAIM_TRANSLATION.update({ord(c): None for c in _ZERO_WIDTH_CHARS})


def normalize_claim_text(text):
    """Fold dashes/odd spaces/markdown emphasis so «Готово — сохранила» and
    «Готово — _сохранила_» both read exactly like «Готово, сохранила». Newlines are
    preserved — a per-item outcome LIST is one of the shapes the guard has to see."""
    value = str(text or "").translate(_CLAIM_TRANSLATION)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[^\S\n]+", " ", value)


# COMPLETED forms only — first-person feminine past (how Cara writes about
# herself) and the SHORT PASSIVES in every gender. The passives matter as much as
# the active forms: Russian short participles agree with their subject, and the
# subjects this app talks about — «запись», «заметка», «задача», «категория» — are
# FEMININE, so «Запись #51 обновлена» is a likelier shape than «обновлено».
# Deliberately ABSENT:
#   * future and infinitive forms («добавлю», «могу добавить», «заменю») — an OFFER
#     to do the thing is exactly the reply we want instead of a fabrication;
#   * masculine PAST ACTIVE forms («добавил», «сохранил») — those are HIS actions,
#     and «Ты сам добавил #51 вчера» is a true sentence.
#
# The set is split in two because position alone cannot tell a report from a story.
# DATA verbs have no other ordinary meaning in this assistant's voice, so they fire
# wherever they appear near the start of a sentence. NARRATIVE-CAPABLE verbs
# («поставила чайник», «вернула книгу на полку», «обновила плейлист», «отметила для
# себя») fire only when the sentence also names one of HIS data objects (a #N, a
# note/task/reminder/file/category, «Movies», «Books») or sits under a «Готово»
# completion header. That is what stops the guard from eating her shared-world
# voice — and a false positive is no longer cheap: it now costs a second model
# call, writes an issue and closes the memory paths for a whole curation window.
_RU_DATA_VERBS = (
    "сохранила|сохранено|сохранена|сохранён|сохранен|"
    "записала|записано|записана|записан|"
    "добавила|добавлено|добавлена|добавлен|"
    "удалила|удалено|удалена|удалён|удален|"
    "стёрла|стерла|стёрто|стерто|стёрта|стерта|"
    "очистила|очищено|очищена|очищен|"
    "восстановила|восстановлено|восстановлена|восстановлен|"
    "заменила|заменено|заменена|заменён|заменен|"
    "переименовала|переименовано|переименована|переименован|"
    "отменила|отменено|отменена|отменён|отменен|"
    "подтвердила|подтверждено|подтверждена|подтверждён|подтвержден"
)
_EN_DATA_VERBS = (
    "saved|filed|deleted|cleared|renamed|added|restored|replaced|confirmed|"
    "scheduled|exported|purged|synced"
)
_RU_SOFT_VERBS = (
    "закрыла|закрыто|закрыта|закрыт|"
    "перенесла|перенесено|перенесена|перенесён|перенесен|"
    "передвинула|передвинуто|передвинута|"
    "поставила|поставлено|поставлена|поставлен|"
    "вернула|возвращено|возвращена|возвращён|возвращен|"
    "обновила|обновлено|обновлена|обновлён|обновлен|"
    "исправила|исправлено|исправлена|исправлен|"
    "переместила|перемещено|перемещена|перемещён|перемещен|"
    "привязала|привязано|привязана|привязан|"
    "назначила|назначено|назначена|назначен|"
    "отметила|отмечено|отмечена|отмечен|"
    "отправила|отправлено|отправлена|отправлен|"
    "выгрузила|выгружено|выгружена"
)
_EN_SOFT_VERBS = "closed|moved|updated|fixed|marked|linked|assigned|sent"

_DATA_VERBS = f"{_RU_DATA_VERBS}|{_EN_DATA_VERBS}"
_DONE_VERBS = f"{_DATA_VERBS}|{_RU_SOFT_VERBS}|{_EN_SOFT_VERBS}"

# HIS data objects. A narrative-capable verb becomes a state-change claim the
# moment one of these is in the same sentence — a kettle, a book on a shelf and a
# playlist carry none of them. Russian category ALIASES («книги», «фильмы») are
# deliberately not here: «вернула книгу на полку» must stay a story.
_DATA_CUE = (
    r"#\s*\d+|\bзаметк\w*|\bзапис(?:ь|и|ью|ей|ям|ями|ях)\b|\bзадач\w*|"
    r"\bнапомина\w*|\bфайл\w*|\bкатегори(?:я|и|ю|ей|ям|ями|ях)\b|\bархив\w*|"
    r"\bсписок\b|\bсписк\w*|\bкаталог\w*|\bmovies\b|\bbooks\b|"
    r"\bnotes?\b|\breminders?\b|\btasks?\b|\bfiles?\b|\bcategor(?:y|ies)\b|"
    r"\bentr(?:y|ies)\b|\barchive\b|\bcatalog\w*|\blists?\b"
)
# The completion announcement itself. «Готово — вернула» is a report no matter
# what the verb usually means.
_COMPLETION_HEAD = r"(?:готово|done)"
# Any enumeration marker, not just a dash: rendering the SAME outcome list with
# digits used to defeat both list shapes at once.
_ITEM_MARKER = r"(?:[-*•·]|\d+[.)]|[✅✔☑])"

_CLAIM_PATTERNS = (
    # 1) A DATA verb near the start of a sentence or LINE. The old pattern allowed
    #    only «готово»/«я»/a note-noun in front of the verb, so ANY other lead-in
    #    («Ок, сохранила», «Поняла! Уже добавила его в Movies», «Не бойся, я всё
    #    сохранила») walked through. MULTILINE because her confirmations are
    #    multi-line and only the second line carries the claim.
    re.compile(rf"(?:^|[.!?]\s*){_ITEM_MARKER}?\s*[^\n]{{0,30}}?"
               rf"\b(?P<verb>{_DATA_VERBS})\b",
               re.IGNORECASE | re.MULTILINE),
    # 2) ANY completed verb announced under a «Готово»/«Done» header.
    re.compile(rf"(?:^|[.!?]\s*){_ITEM_MARKER}?\s*{_COMPLETION_HEAD}\b[^\n]{{0,40}}?"
               rf"\b(?P<verb>{_DONE_VERBS})\b",
               re.IGNORECASE | re.MULTILINE),
    # 3) ANY completed verb in the same SENTENCE as one of his data objects, either
    #    order: «- #51 - «Я устал от тебя» (2011) - восстановила», «Задача перенесена
    #    на завтра», «Запись #51 обновлена». Sentence-bounded ([^\n.!?]) so a note
    #    number in one sentence cannot arm a narrative verb in the next one.
    re.compile(rf"(?:{_DATA_CUE})[^\n.!?]{{0,120}}?\b(?P<verb>{_DONE_VERBS})\b",
               re.IGNORECASE),
    re.compile(rf"\b(?P<verb>{_DONE_VERBS})\b[^\n.!?]{{0,80}}?(?:{_DATA_CUE})",
               re.IGNORECASE),
    # 4) VERB-LESS completed state assertion over a NUMBER — the shape that walked
    #    through untouched: «#51 теперь «Всё везде и сразу» (2022) вместо «Я устал от
    #    тебя»». The «вместо»/«instead of» half was never the claim: «#N теперь …»,
    #    «теперь под #N …», «#N is now …», «уже лежит в Movies под #52» all assert a
    #    completed change, and an ordinary chat turn has no authority to assert what a
    #    number NOW holds. «уже есть #N»/«now no #N» is existence, not change, and is
    #    carved out — the catalog-grounding block is the authority for what exists.
    re.compile(r"#\s*\d+[^\n]{0,60}?\b(?:теперь|уже|now)\b", re.IGNORECASE),
    re.compile(r"\b(?:теперь|уже|now)\b(?!\s+(?:есть|нет|no|not)\b)[^\n]{0,40}?#\s*\d+",
               re.IGNORECASE),
    # 5) «Готово:» introducing a per-item outcome list — the completion word plus a
    #    note number is a claim even when no verb follows, with ANY item marker and
    #    however many blank lines sit between.
    re.compile(rf"\b(?:готово|done)\b\s*[:\-]\s*(?:\n\s*)*{_ITEM_MARKER}?\s*#\s*\d+",
               re.IGNORECASE),
    # 6) An enumerated line whose outcome verb sits at the end («- «X» (2022) - добавила»,
    #    «2. #52 — добавила»). A bulleted/numbered outcome line is a report, never a story.
    re.compile(rf"^\s*{_ITEM_MARKER}\s+[^\n]{{0,160}}?\b(?P<verb>{_DONE_VERBS})\b",
               re.IGNORECASE | re.MULTILINE),
    # 7) A COMPLETED auxiliary plus an infinitive. Infinitives are excluded above to
    #    protect offers, but «успела/удалось/смогла + добавить» is not an offer, it is
    #    a completion — and «могу добавить»/«давай добавлю» still get through.
    re.compile(
        r"\b(?P<verb>успела|успел|удалось|смогла|смог|получилось|managed\s+to|"
        r"was\s+able\s+to)\b[^\n]{0,25}?\b(?:добавить|сохранить|записать|"
        r"восстановить|заменить|удалить|стереть|очистить|перенести|переместить|"
        r"обновить|исправить|закрыть|отменить|переименовать|привязать|назначить|"
        r"add|save|restore|replace|delete|remove|move|update|fix|clear|close|rename)\b",
        re.IGNORECASE),
    # 8) "the queue is clean" / "I'm taking this on" — unchanged from the original.
    re.compile(
        r"\b(?:всё|очередь|список)\s+(?:чисто|разобран[аоы]?|пуст[ао])\b|"
        r"\b(?:возьму|беру)\s+(?:это\s+)?в\s+работу\b|"
        r"\b(?:queue|list|everything)\s+is\s+(?:clear|empty|sorted)\b|"
        r"\bi(?:'ll|\s+will)\s+(?:take|put)\s+(?:this\s+)?(?:into|on)\s+(?:the\s+)?work\b",
        re.IGNORECASE),
)

# A completed verb behind a NEGATION is an honest denial, not a claim («я её не
# добавила», "I haven't added it"). The negation must be ADJACENT to the verb —
# at most one pronoun/particle between them. The old window allowed twelve word
# characters, which swallowed reassurance idioms that precede a real claim: «Не
# волнуйся я добавила #52» and «Я ничего не забыла и сохранила #51» were both
# read as denials.
_NEGATION_TAIL_RE = re.compile(
    r"(?:\bне|\bни|\bnot|\bnever|n['’]t)\s+"
    r"(?:я\s+|i\s+|же\s+|ещё\s+|еще\s+|это\s+|их\s+|её\s+|ее\s+|его\s+|ничего\s+)?$",
    re.IGNORECASE)
_NEGATION_WINDOW = 40

# Explicit references to an EARLIER exchange. Discussion of a real past save —
# «Да, я сохранила его вчера в Movies под #51» — is truthful and must survive, or
# the repair pass turns it into a confident false DENIAL of something that really
# happened (the same honesty failure with the sign flipped). A «Готово» header on
# the same line overrides this: that is a report about NOW.
# «тогда» is deliberately NOT here: in Russian it is far more often «in that
# case» than «back then», and it is exactly the filler in «Окей, тогда добавила
# её отдельной записью» — a fabrication, not a memory.
_PAST_MARKER_RE = re.compile(
    r"\b(?:вчера|позавчера|недавно|раньше|ранее|утром|днём|днем|вечером|"
    r"ночью|на\s+днях|на\s+прошлой\s+неделе|на\s+той\s+неделе|"
    r"в\s+прошлом\s+месяце|в\s+прошлый\s+раз|в\s+тот\s+раз|"
    r"yesterday|last\s+week|last\s+month|last\s+time|earlier|the\s+other\s+day|"
    r"back\s+then|this\s+morning)\b",
    re.IGNORECASE)

_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?\n]")
# A clause boundary, not just a sentence one — the dash is already folded to '-'.
_CLAUSE_BOUNDARY_RE = re.compile(r"[.!?\n,;:-]")
_COMPLETION_HEAD_RE = re.compile(rf"\b{_COMPLETION_HEAD}\b", re.IGNORECASE)
_FIRST_PERSON_RE = re.compile(r"\b(?:я|i)\b", re.IGNORECASE)
# What an actual QUESTION about a completed action opens with.
_QUESTION_CUE_RE = re.compile(
    r"^\s*(?:[-*•·]\s*)?(?:а|ты|вы|тебе|разве|неужели|правда|скажи|"
    r"что|чего|где|куда|когда|почему|зачем|сколько|как|какая|какой|какие|"
    r"did|do|does|have|has|had|can|could|are|is|was|were|will|would|you|"
    r"why|what|where|when|how|which)\b",
    re.IGNORECASE)


def _negated(value, pos):
    return bool(_NEGATION_TAIL_RE.search(value[max(0, pos - _NEGATION_WINDOW):pos]))


def _line_around(value, start, end):
    line_start = value.rfind("\n", 0, start) + 1
    line_end = value.find("\n", end)
    return value[line_start:line_end if line_end != -1 else len(value)]


def _past_reference(value, start, end):
    """True when the line explicitly places the action in an EARLIER exchange."""
    line = _line_around(value, start, end)
    if _COMPLETION_HEAD_RE.search(line):
        return False
    return bool(_PAST_MARKER_RE.search(line))


def _inside_question(value, start, end):
    """True when the match sits in a QUESTION rather than in a report.

    NOT «the next 120 characters end in ?». Cara's house style closes a
    confirmation with an offer question, so that test disarmed the guard for the
    production line itself the moment the offer was joined with a comma instead of
    a newline («Готово — сохранила в Movies под #51, хочешь, поищу режиссёра?»).
    The question has to be about the CLAUSE the match lives in:
      * the sentence must really end in «?»; AND
      * that clause must carry no completion evidence before the match — no
        «готово/done», no first-person «я/I» subject; AND
      * either the «?» closes that very clause («А #51 ты сохранила?»), or the
        clause opens with an interrogative cue («Ты сохранила это, или нет?»).
    A trailing tag question after a first-person completed verb («Я же сохранила
    #51, помнишь?») is a claim, and stays blocked.
    """
    stop = _SENTENCE_BOUNDARY_RE.search(value[end:end + 200])
    if not (stop and stop.group() == "?"):
        return False
    left = max((m.end() for m in _CLAUSE_BOUNDARY_RE.finditer(value, 0, start)),
               default=0)
    right_m = _CLAUSE_BOUNDARY_RE.search(value, end)
    right = right_m.start() if right_m else len(value)
    prefix = value[left:start]
    if _COMPLETION_HEAD_RE.search(prefix) or _FIRST_PERSON_RE.search(prefix):
        return False
    if right_m and right_m.group() == "?":
        return True
    return bool(_QUESTION_CUE_RE.match(value[left:right]))

# Which final verbs are legitimate in each lifecycle state.
STATE_ALLOWED_FINAL_VERBS = {
    "received": set(),
    "stored_original": {"saved", "сохранила"},
    "suggested": set(),
    "waiting_confirmation": set(),
    "confirmed": FINAL_VERBS,
    "failed_retryable": set(),
    "failed_final": {"saved", "сохранила"},  # may say the original was saved
    "done": FINAL_VERBS,                      # read-only/completed ops
}

# A template key is the deterministic action boundary: call sites render it
# only after that named operation. New final-action copy must be reviewed and
# mapped here or both runtime rendering and the catalogue test fail closed.
TEMPLATE_STATES = {
    "counts": "stored_original",
    "already_confirmed": "confirmed",
    "stored_retry": "stored_original",
    "reminder_set": "done",
    "boss_remembered": "done",
    "memory_candidate_kept": "done",
    "memory_empty": "done",
    "remember_saved": "done",
    "auto_confirmed": "done",
    "stats_empty": "done",
    "capabilities": "done",
    "purge_done": "done",
    "calendar_added": "done",
    "deleted": "done",
    "deleted_multi": "done",
    "correction_learned": "done",
    "correction_needs_code": "done",
    "journal_saved": "done",
    "problem_logged": "done",
    "reminder_no_prev": "done",
    "files_empty": "done",
    # Rendered only AFTER apply_category_confirm committed the note.
    "capture_reminder_slot_busy": "confirmed",
    # Rendered only AFTER _media_store_entries committed every entry (the media
    # confirm boundary) — the card itself carries no final verbs.
    "media_saved": "done",
    # "I'd already saved the older version" — the claim is about the note that IS
    # saved; the EDIT itself is explicitly not applied yet, which is what the
    # question in the same sentence asks about.
    "note_edit_offer": "confirmed",
}


def verbs_in(text):
    low = str(text or "").lower()
    return {v for v in FINAL_VERBS
            if re.search(rf"(?<!\w){re.escape(v)}(?!\w)", low)}


def assert_template_allowed(template_key, state, text):
    """Raise ValueError if `text` uses a final verb not allowed in `state`."""
    allowed = STATE_ALLOWED_FINAL_VERBS.get(state, set())
    forbidden = verbs_in(text) - allowed
    if forbidden:
        raise ValueError(
            f"Template {template_key!r} uses final verbs {sorted(forbidden)} in state {state!r}")


def assert_template_key_allowed(template_key, text):
    """Production guard for one rendered template."""
    found = verbs_in(text)
    if not found:
        return
    state = TEMPLATE_STATES.get(template_key)
    if not state:
        raise ValueError(
            f"Template {template_key!r} uses final verbs but declares no lifecycle state")
    assert_template_allowed(template_key, state, text)


def assert_catalogue(catalogue):
    """Validate every language and variant, not a hand-selected sample."""
    for key, entry in catalogue.items():
        for localized in entry.values():
            variants = localized if isinstance(localized, (list, tuple)) else [localized]
            for text in variants:
                assert_template_key_allowed(key, text)


def freeform_claims_artifact(text):
    """True when ordinary chat text falsely presents a file as attached/created."""
    value = str(text or "")
    return bool(_BARE_ARTIFACT_LINK_RE.search(value) or _ARTIFACT_CLAIM_RE.search(value))


def action_claim_match(text):
    """The exact fragment where ordinary chat claims a completed state change, or
    None. Returned (not just a bool) so the blocked reply can be logged with the
    phrase that tripped the guard — the four production fabrications were only
    diagnosable because the raw lines were kept."""
    value = normalize_claim_text(text)
    for pattern in _CLAIM_PATTERNS:
        for m in pattern.finditer(value):
            pos = m.start("verb") if "verb" in pattern.groupindex else m.start()
            if _negated(value, pos) or _inside_question(value, pos, m.end()) \
                    or _past_reference(value, pos, m.end()):
                continue
            return " ".join(m.group(0).split())
    return None


def freeform_claims_action(text):
    """True when ordinary chat claims a state change it cannot have performed."""
    return action_claim_match(text) is not None
