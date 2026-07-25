#!/usr/bin/env python3
"""Shared config and logging for tg-ingest-agent."""
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def log(message):
    print(f"{datetime.now(timezone.utc).isoformat()} {message}", flush=True)


def build_multipart(fields, file_field, filename, file_bytes, content_type):
    """RFC 2046 multipart/form-data body; returns (body, boundary)."""
    import uuid
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8")
        )
    parts.append(
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\";"
         f" filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n").encode("utf-8")
    )
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def detect_lang(text):
    """Reply-language from the message, by WORD not letter count: a long borrowed
    English term inside a Russian sentence ("когда у нас performance review?")
    must stay Russian, so we don't let one long Latin run outvote the Russian
    words around it. Ties and 'no letters' fall to Russian (the uncertain
    fallback) — None means the caller uses the stored preference (also ru)."""
    cyr_words = lat_words = 0
    for token in re.findall(r"[A-Za-zЀ-ӿ]+", str(text or "")):
        if any("Ѐ" <= c <= "ӿ" for c in token):
            cyr_words += 1
        else:
            lat_words += 1
    if cyr_words == 0 and lat_words == 0:
        return None
    return "ru" if cyr_words >= lat_words else "en"


# Whisper's well-known non-speech hallucinations (from YouTube training data) —
# emitted on silence, music, or wrong-language audio. Specific enough not to
# collide with real messages.
_STT_NOISE_PHRASES = (
    "subtitles by", "amara.org", "thanks for watching", "thank you for watching",
    "please subscribe", "продолжение следует", "спасибо за просмотр",
    "подписывайтесь на канал", "субтитры подготовил", "субтитры сделал",
    "редактор субтитров", "dimatorzok",
)
# How much text may remain around the matched noise phrase(s) and still count as
# "the phrase IS the transcript" — punctuation, a stray filler word, the glue of
# a multi-phrase credit line ("Subtitles by the Amara.org community" leaves
# "the  community", 14 chars). Anything longer is real speech that merely
# mentions it. 15 is the approved budget: at 20 a short genuine dictation like
# «Спасибо за просмотр, перезвони Ване» (16 chars of remainder) was still thrown
# away — the very failure this rule exists to stop, just narrowed.
STT_NOISE_REMAINDER = 15


# Emoji Cara may react with (a safe subset of Telegram's allowed reactions). If
# Telegram rejects one anyway, the agent just logs and moves on.
REACTION_PALETTE = frozenset({
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔", "🎉", "🤩", "🙏", "👌",
    "💯", "🤣", "🤝", "✍️", "😢", "👀", "🫡", "😍", "🙈", "🤯", "😎", "🤗",
})
_POSITIVE_REACTIONS = {"👍", "❤️", "❤", "🔥", "🥰", "👏", "😁", "🎉", "🤩", "🙏",
                       "👌", "💯", "🤣", "🤝", "😍", "😎", "🤗"}
_NEGATIVE_REACTIONS = {"👎", "😢", "😭", "💔", "🤬", "😡", "🤨", "😐", "🥱", "💩"}

# Common emoji that AREN'T in Telegram's reaction palette -> the nearest allowed
# one, so an out-of-palette emotion is converted rather than dropped.
REACTION_ALIASES = {
    "😊": "😁", "🙂": "😁", "😄": "😁", "😃": "😁", "😀": "😁", "😅": "😁", "😆": "😁",
    "😉": "😁", "🙃": "😁", "😂": "🤣",
    "💕": "❤️", "💖": "❤️", "💗": "❤️", "💓": "❤️", "💞": "❤️", "💘": "❤️", "💝": "❤️",
    "❤": "❤️", "♥": "❤️", "🧡": "❤️", "💛": "❤️", "💚": "❤️", "💙": "❤️", "💜": "❤️",
    "🤍": "❤️", "🤎": "❤️", "🩷": "❤️", "🫶": "❤️",
    "🥺": "🥰", "🥹": "🥰", "😌": "🥰", "🥲": "🥰",
    "😘": "😍", "😗": "😍", "😚": "😍", "😙": "😍", "😻": "😍", "🤤": "😍",
    "🙌": "👏", "🤲": "🙏", "🥳": "🎉", "🎊": "🎉",
    "✨": "🔥", "⭐": "🔥", "🌟": "🔥", "💫": "🔥", "💪": "🔥", "⚡": "🔥",
    "😭": "😢", "😔": "😢", "😞": "😢", "🙁": "😢", "☹": "😢",
    "😱": "🤯", "😮": "🤯", "😲": "🤯", "🤩": "🤩",
    "🤭": "🙈", "🫣": "🙈", "😬": "🙈",
    "😏": "😎", "🫡": "🫡", "✅": "👍", "☑": "👍", "👌🏻": "👌",
    "🥱": "👎", "😒": "👎", "🙄": "👎",
}


def reaction_sentiment(emoji):
    if emoji in _POSITIVE_REACTIONS:
        return "positive"
    if emoji in _NEGATIVE_REACTIONS:
        return "negative"
    return "neutral"


def to_reaction(s):
    """Map `s` (an emoji, possibly outside Telegram's reaction palette, optionally
    wrapped in surrounding text) to a TG-allowed reaction emoji: the palette emoji
    itself, the nearest mapped equivalent, or None if nothing matches. Tolerates a
    missing/extra VS16 (❤ vs ❤️) and trailing skin-tone modifiers (👍 in 👍🏻)."""
    if not s:
        return None
    for emo in REACTION_PALETTE:
        if emo in s:
            return emo
    bare = s.replace("️", "")
    for emo in REACTION_PALETTE:
        if emo.replace("️", "") in bare:
            return emo
    for src, dst in REACTION_ALIASES.items():
        if src in s or src.replace("️", "") in bare:
            return dst
    return None


def scrub_secrets(text):
    """Mask obvious secrets before a trace is shown or shared (defence in depth —
    trace text is short stage messages, but never leak a token)."""
    t = str(text or "")
    t = re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", t)
    t = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", "***", t)
    return t


def part_of_day(hour, lang="ru"):
    """Coarse part-of-day label for a local hour (0-23)."""
    if 5 <= hour < 12:
        ru, en = "утро", "morning"
    elif 12 <= hour < 18:
        ru, en = "день", "afternoon"
    elif 18 <= hour < 23:
        ru, en = "вечер", "evening"
    else:
        ru, en = "ночь", "night"
    return ru if lang == "ru" else en


def is_stt_noise(text):
    """True if a transcript is a Whisper non-speech hallucination ('[Subscribe]',
    '[Music]', 'Спасибо за просмотр', 'Subtitles by…') or effectively empty — so
    we ask for a resend instead of acting on garbage."""
    t = str(text or "").strip()
    if not t:
        return True
    # nothing but brackets / punctuation / music glyphs -> not speech
    core = re.sub(r"[\[\](){}<>♪♫*~_\-—–.,!?…:;\"'«»\s]", "", t)
    if len(core) <= 1:
        return True
    # the whole transcript is a single bracketed tag: [Subscribe], (applause)
    if re.fullmatch(r"[\[(<][^\])>]{0,40}[\])>]", t):
        return True
    # A noise phrase ANYWHERE used to condemn the whole transcript, so a genuine
    # dictation or forwarded talk that merely CONTAINED «спасибо за просмотр» /
    # «продолжение следует» (real outros — which is exactly why Whisper
    # hallucinates them) was thrown away, deterministically, on every retry.
    # Noise only when the phrases are essentially the ENTIRE transcript: strip
    # every phrase that matched and see whether anything of substance is left.
    low = t.casefold()
    rest = low
    for phrase in _STT_NOISE_PHRASES:
        if phrase in rest:
            rest = rest.replace(phrase, " ")
    return rest != low and len(rest.strip()) <= STT_NOISE_REMAINDER


# Current trace id for the in-flight unit of work (single-threaded poll loop,
# so a module global is safe). store.usage_add/issue_add default to this so
# every model call and issue is trace-linked without threading an arg through
# every call site. Lives here (no store/trace deps) to avoid import cycles.
_CURRENT_TRACE = {"id": None}


def set_current_trace(trace_id):
    _CURRENT_TRACE["id"] = trace_id


def current_trace():
    return _CURRENT_TRACE["id"]


def config_list(value):
    normalized = str(value or "").replace("\n", ",").replace(";", ",").replace("|", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def parse_chat_ids(value):
    ids = set()
    for part in config_list(value):
        try:
            ids.add(int(part))
        except ValueError:
            raise SystemExit(f"ALLOWED_CHAT_IDS contains a non-numeric entry: {part!r}")
    return ids


def parse_int_set(value, default=None):
    """Parse a comma/space list of integers into a set; blank -> default (copied)."""
    if value is None or not str(value).strip():
        return set(default) if default is not None else set()
    out = set()
    for part in config_list(value):
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out or (set(default) if default is not None else set())


def load_categories(env):
    file_path = (env.get("CATEGORIES_FILE") or "").strip()
    if file_path:
        lines = Path(file_path).read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    return config_list(env.get("CATEGORIES", ""))


class ShutdownInterrupt(Exception):
    """Raised when an in-flight update should be left for redelivery because
    the service is shutting down mid-processing (e.g. a deploy restart killed
    the whisper subprocess)."""


class Config:
    pass


def load_config(env=None):
    env = os.environ if env is None else env
    cfg = Config()
    cfg.token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not cfg.token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    cfg.allowed_chat_ids = parse_chat_ids(env.get("ALLOWED_CHAT_IDS", ""))
    if not cfg.allowed_chat_ids:
        raise SystemExit("ALLOWED_CHAT_IDS is required")
    cfg.do_key = (env.get("DO_MODEL_ACCESS_KEY") or "").strip()
    if not cfg.do_key:
        raise SystemExit("DO_MODEL_ACCESS_KEY is required")
    # Optional seed taxonomy; the real category list grows from confirmed
    # suggestions in the categories table.
    cfg.seed_categories = load_categories(env)
    cfg.fallback_category = (env.get("FALLBACK_CATEGORY") or "uncategorized").strip()
    cfg.do_model = (env.get("DO_CHAT_MODEL") or "deepseek-4-flash").strip()
    cfg.router_model = (env.get("ROUTER_MODEL") or cfg.do_model).strip()
    cfg.embedding_model = (env.get("DO_EMBEDDING_MODEL") or "BGE-M3").strip()
    cfg.do_base_url = (env.get("DO_INFERENCE_BASE_URL") or "https://inference.do-ai.run/v1").strip()
    cfg.db_path = Path(env.get("DB_PATH") or "/var/lib/tg-ingest-agent/ingest.db")
    cfg.media_dir = Path(env.get("MEDIA_DIR") or "/var/lib/tg-ingest-agent/media")
    cfg.poll_timeout = int(env.get("POLL_TIMEOUT_SECONDS") or "50")
    cfg.update_max_attempts = max(1, int(env.get("UPDATE_MAX_ATTEMPTS") or "3"))
    cfg.album_settle = float(env.get("ALBUM_SETTLE_SECONDS") or "3")
    cfg.max_llm_images = int(env.get("MAX_LLM_IMAGES") or "4")
    cfg.llm_timeout = int(env.get("LLM_TIMEOUT_SECONDS") or "90")
    cfg.llm_max_attempts = int(env.get("LLM_MAX_ATTEMPTS") or "5")
    cfg.retry_interval = int(env.get("RETRY_INTERVAL_SECONDS") or "300")
    # Conversational assistant settings
    cfg.language = (env.get("BOT_LANGUAGE") or "ru").strip().lower()
    cfg.timezone_offset = int(env.get("TIMEZONE_OFFSET_HOURS") or "3")  # MSK default
    # Cara's own timezone (her fictional life); defaults to the boss's, so she
    # only mentions a separate "her time" if it's explicitly set different.
    cfg.cara_tz_offset = int(env.get("CARA_TIMEZONE_OFFSET_HOURS") or cfg.timezone_offset)
    cfg.confidence_threshold = float(env.get("ROUTER_CONFIDENCE_THRESHOLD") or "0.6")
    # Speech-to-text: mode 'local' = whisper.cpp on this host (free, slower);
    # mode 'remote' = OpenAI-compatible /audio/transcriptions endpoint.
    # STT_MODE: local (cold whisper-cli) | local_server (warm whisper-server,
    # no per-call model reload) | remote (OpenAI-compatible endpoint).
    cfg.stt_enabled = (env.get("STT_ENABLED") or "true").strip().lower() == "true"
    cfg.stt_mode = (env.get("STT_MODE") or "remote").strip().lower()
    cfg.stt_model = (env.get("STT_MODEL") or "whisper-large-v3").strip()
    # Language hint for Whisper. 'auto' lets it detect, but a wrong guess on the
    # first moments yields YouTube-style hallucinations ('[Subscribe]'); pinning
    # a language (e.g. STT_LANGUAGE=ru) cuts those for a single-language speaker.
    cfg.stt_language = (env.get("STT_LANGUAGE") or "auto").strip().lower()
    cfg.whisper_bin = (env.get("WHISPER_BIN") or "/opt/whisper.cpp/build/bin/whisper-cli").strip()
    cfg.whisper_model = (env.get("WHISPER_MODEL")
                         or "/opt/whisper.cpp/models/ggml-small-q5_1.bin").strip()
    cfg.whisper_server_url = (env.get("WHISPER_SERVER_URL") or "http://127.0.0.1:8089").strip()
    cfg.stt_local_timeout = int(env.get("STT_LOCAL_TIMEOUT_SECONDS") or "600")
    # Spend control
    cfg.budget_daily_usd = float(env.get("BUDGET_DAILY_USD") or "1.0")
    cfg.budget_monthly_usd = float(env.get("BUDGET_MONTHLY_USD") or "15.0")
    cfg.pricing_json = (env.get("PRICING_JSON") or "").strip()
    # Learning
    cfg.habit_threshold = int(env.get("HABIT_THRESHOLD") or "10")
    # Weekly performance review schedule (local time). Cara holds it on this
    # weekday/hour and can tell you when the next one is. 0=Monday … 6=Sunday.
    cfg.review_weekday = max(0, min(6, int(env.get("REVIEW_WEEKDAY") or "0")))
    cfg.review_hour = max(0, min(23, int(env.get("REVIEW_HOUR") or "10")))
    # Morning brief: a daily warm digest, OFF by default (opt-in via "делай
    # утреннюю сводку"); fires once a day at/after this local hour.
    cfg.morning_brief_hour = max(0, min(23, int(env.get("MORNING_BRIEF_HOUR") or "9")))
    # Proactive heartbeat: gentle, suggestion-only nudges. Off-hours respected,
    # at most N non-urgent nudges/day; urgent (overdue) may bypass the cap.
    cfg.proactive_enabled = (env.get("PROACTIVE_ENABLED") or "true").strip().lower() == "true"
    cfg.quiet_start = max(0, min(23, int(env.get("QUIET_HOURS_START") or "22")))  # local hour
    cfg.quiet_end = max(0, min(23, int(env.get("QUIET_HOURS_END") or "8")))       # local hour
    cfg.proactive_max_per_day = int(env.get("PROACTIVE_MAX_PER_DAY") or "1")
    cfg.proactive_urgent_bypass_quiet = (
        env.get("PROACTIVE_URGENT_BYPASS_QUIET") or "false").strip().lower() == "true"
    cfg.proactive_interval = int(env.get("PROACTIVE_INTERVAL_SECONDS") or "3600")
    # Model profiles / failover (llm.chat_profile)
    cfg.llm_profiles_json = (env.get("LLM_PROFILES_JSON") or "").strip()
    cfg.llm_fallback_cooldown = int(env.get("LLM_FALLBACK_COOLDOWN_SECONDS") or "300")
    # Vision model for describing photos when the chat model isn't vision-capable
    # (open-weight models). Empty -> photos categorized text-only from the caption.
    cfg.vision_model = (env.get("VISION_MODEL") or "").strip()
    # Model-health monitor: how often to check Cara's models are reachable and
    # alert the boss the moment one becomes inaccessible (0 disables).
    cfg.model_health_interval = int(env.get("MODEL_HEALTH_INTERVAL_SECONDS") or "1800")
    # Debounce: how many CONSECUTIVE failed probes before announcing "model down". A
    # single failed probe is usually a transient 429/overload that clears by the next
    # check; requiring >=2 suppresses the down/back flap noise (only a sustained outage
    # is announced, and "back" only fires if we actually announced "down").
    cfg.model_health_confirm = max(1, int(env.get("MODEL_HEALTH_CONFIRM_CHECKS") or "2"))
    # Transient overloads are common and already fail over automatically. Require
    # a longer sustained sequence before bothering the boss; hard access failures
    # continue to use MODEL_HEALTH_CONFIRM_CHECKS.
    cfg.model_health_transient_confirm = max(
        cfg.model_health_confirm,
        int(env.get("MODEL_HEALTH_TRANSIENT_CONFIRM_CHECKS") or "4"),
    )
    # Housekeeping: how many review .md exports to keep on disk
    cfg.review_keep = int(env.get("REVIEW_KEEP") or "10")
    # A due reminder waits for a ~5-min lull after the boss's last message so it never lands
    # mid-exchange.
    cfg.reminder_quiet_after_msg_minutes = float(env.get("REMINDER_QUIET_AFTER_MSG_MINUTES") or "5")
    # recall_conversation: how far back to read by default when the boss references our past
    # dialogue without a clear time ("посмотри наш разговор"), and the turn cap fed to the model.
    cfg.recall_default_hours = float(env.get("RECALL_DEFAULT_HOURS") or "60")
    cfg.recall_max_turns = int(env.get("RECALL_MAX_TURNS") or "300")
    # Register baseline. Her resting tone is set by whether it's WORK time
    # and whether he's been doing business recently — NOT a rigid day/night gate, and
    # always overridden by how personal his actual message is. After he's been heavy on
    # business she stays mobilized (working style) for this many minutes, then eases
    # back to the time-of-day baseline (relaxed/playful off-hours). Work window is the
    # boss-local hours/days that set the resting tone to professional when business is quiet.
    cfg.work_register_hold_minutes = int(env.get("WORK_REGISTER_HOLD_MINUTES") or "40")
    cfg.work_hours_start = max(0, min(23, int(env.get("WORK_HOURS_START") or "9")))
    cfg.work_hours_end = max(0, min(24, int(env.get("WORK_HOURS_END") or "19")))
    # Work days as weekday numbers 0=Mon..6=Sun (default Mon-Fri). Off these days the
    # resting baseline is the relaxed/playful one all day.
    cfg.work_days = parse_int_set(env.get("WORK_DAYS"), default={0, 1, 2, 3, 4})
    # Max-defer valve: a reminder overdue beyond this fires even mid-exchange, so a
    # long evening can never defer it indefinitely (0 disables the valve).
    cfg.reminder_max_defer_hours = float(env.get("REMINDER_MAX_DEFER_HOURS") or "2")
    # A fired one-shot reminder stays open ('ждёт готово') until acked; if never acked it
    # auto-closes after this many days so the list doesn't pile up forever (0 disables).
    cfg.reminder_fired_expire_days = float(env.get("REMINDER_FIRED_EXPIRE_DAYS") or "3")
    # Telemetry retention (housekeep): traces/trace_events, done/failed events+jobs,
    # the proactive audit log and expired model cooldowns are pruned past this window
    # (0 disables). llm_usage, conversation, issues and memory tables are NEVER pruned.
    cfg.telemetry_retention_days = float(env.get("TELEMETRY_RETENTION_DAYS") or "90")
    # Personality intensity (0 neutral .. 3 max; selects template variants only)
    cfg.personality_intensity = int(env.get("PERSONALITY_INTENSITY") or "2")
    # Knowledge Q&A (ask): semantic retrieval over the KB
    cfg.ask_top_k = int(env.get("ASK_TOP_K") or "6")
    cfg.ask_context_chars = int(env.get("ASK_CONTEXT_CHARS") or "6000")
    cfg.ask_min_score = float(env.get("ASK_MIN_SCORE") or "0.25")
    cfg.chunk_chars = int(env.get("CHUNK_CHARS") or "800")
    # Remote fetch (read a URL the operator sends; SSRF-guarded)
    cfg.fetch_enabled = (env.get("FETCH_ENABLED") or "true").strip().lower() == "true"
    cfg.fetch_timeout = int(env.get("FETCH_TIMEOUT_SECONDS") or "20")
    cfg.fetch_max_bytes = int(env.get("FETCH_MAX_BYTES") or str(2 * 1024 * 1024))
    # Ingest link reading: a LINK-CENTRIC note (short text + a URL) has its first
    # URL fetched so the summary and search index cover the actual page (not a
    # guess). Uses the same SSRF-guarded fetcher and its timeout/size caps.
    cfg.ingest_read_links = (env.get("INGEST_READ_LINKS") or "true").strip().lower() == "true"
    cfg.ingest_fetch_chars = int(env.get("INGEST_FETCH_CHARS") or "3500")
    # Daily DB backup (backup.py): a consistent snapshot, rotated locally and
    # encrypted before any off-box copy (Spaces or fleet notify chat).
    cfg.backup_enabled = (env.get("BACKUP_ENABLED") or "true").strip().lower() == "true"
    cfg.backup_keep = max(1, int(env.get("BACKUP_KEEP") or "7"))
    cfg.backup_encryption_key_file = Path(
        env.get("BACKUP_ENCRYPTION_KEY_FILE") or "/etc/tg-ingest-agent-backup.key")
    # Low-disk alert: warn the boss while there is still room to act. A full disk
    # kills every SQLite write at once (crash loop), and the first symptom used to
    # be the crash itself. Percent of the DB filesystem still free (0 disables).
    cfg.disk_alert_min_free_pct = float(env.get("DISK_ALERT_MIN_FREE_PCT") or "10")
    # Binary storage backend: 'local' (default) or 'spaces' (DO Spaces, S3).
    # Built now, dormant until a Space + keys are configured.
    cfg.storage_backend = (env.get("STORAGE_BACKEND") or "local").strip().lower()
    cfg.spaces_region = (env.get("SPACES_REGION") or "fra1").strip()
    cfg.spaces_bucket = (env.get("SPACES_BUCKET") or "").strip()
    cfg.spaces_endpoint = (env.get("SPACES_ENDPOINT")
                           or f"https://{cfg.spaces_region}.digitaloceanspaces.com").strip()
    cfg.spaces_key = (env.get("SPACES_KEY") or "").strip()
    cfg.spaces_secret = (env.get("SPACES_SECRET") or "").strip()
    cfg.spaces_prefix = (env.get("SPACES_PREFIX") or "media").strip()
    # Google Calendar sync (dormant until the key file + calendar id exist;
    # .ics export works without any of this)
    cfg.gcal_calendar_id = (env.get("GCAL_CALENDAR_ID") or "").strip()
    cfg.gcal_key_file = (env.get("GCAL_SA_KEY_FILE") or "/etc/tg-ingest-agent/gcal-sa.json").strip()
    cfg.event_duration_minutes = int(env.get("EVENT_DURATION_MINUTES") or "30")
    # Build/deploy notices go to the shared FLEET notification bot (the ops channel other
    # VPSes post to), NOT into the boss's conversation with Cara — a code install must never
    # clutter the personal chat. These live in Cara's own env because the tg-ingest user can't
    # read the root-only /etc/codex-auto-update/telegram.env (and its var names would collide
    # with Cara's own token). Empty token/chat -> deploy notices are simply skipped.
    cfg.fleet_notify_token = (env.get("FLEET_NOTIFY_BOT_TOKEN") or "").strip()
    cfg.fleet_notify_chat_id = (env.get("FLEET_NOTIFY_CHAT_ID") or "").strip()
    cfg.fleet_notify_label = (env.get("FLEET_NOTIFY_LABEL") or "tg-ingest-agent (Cara)").strip()
    return cfg
