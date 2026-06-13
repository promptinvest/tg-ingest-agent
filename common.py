#!/usr/bin/env python3
"""Shared config and logging for tg-ingest-agent."""
import os
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
    cfg.do_model = (env.get("DO_CHAT_MODEL") or "anthropic-claude-haiku-4.5").strip()
    cfg.router_model = (env.get("ROUTER_MODEL") or cfg.do_model).strip()
    cfg.embedding_model = (env.get("DO_EMBEDDING_MODEL") or "BGE-M3").strip()
    cfg.do_base_url = (env.get("DO_INFERENCE_BASE_URL") or "https://inference.do-ai.run/v1").strip()
    cfg.db_path = Path(env.get("DB_PATH") or "/var/lib/tg-ingest-agent/ingest.db")
    cfg.media_dir = Path(env.get("MEDIA_DIR") or "/var/lib/tg-ingest-agent/media")
    cfg.poll_timeout = int(env.get("POLL_TIMEOUT_SECONDS") or "50")
    cfg.album_settle = float(env.get("ALBUM_SETTLE_SECONDS") or "3")
    cfg.max_llm_images = int(env.get("MAX_LLM_IMAGES") or "4")
    cfg.llm_timeout = int(env.get("LLM_TIMEOUT_SECONDS") or "90")
    cfg.llm_max_attempts = int(env.get("LLM_MAX_ATTEMPTS") or "5")
    cfg.retry_interval = int(env.get("RETRY_INTERVAL_SECONDS") or "300")
    # Conversational assistant settings
    cfg.language = (env.get("BOT_LANGUAGE") or "ru").strip().lower()
    cfg.timezone_offset = int(env.get("TIMEZONE_OFFSET_HOURS") or "3")  # MSK default
    cfg.confidence_threshold = float(env.get("ROUTER_CONFIDENCE_THRESHOLD") or "0.6")
    # Speech-to-text: mode 'local' = whisper.cpp on this host (free, slower);
    # mode 'remote' = OpenAI-compatible /audio/transcriptions endpoint.
    cfg.stt_enabled = (env.get("STT_ENABLED") or "true").strip().lower() == "true"
    cfg.stt_mode = (env.get("STT_MODE") or "remote").strip().lower()
    cfg.stt_model = (env.get("STT_MODEL") or "whisper-large-v3").strip()
    cfg.whisper_bin = (env.get("WHISPER_BIN") or "/opt/whisper.cpp/build/bin/whisper-cli").strip()
    cfg.whisper_model = (env.get("WHISPER_MODEL")
                         or "/opt/whisper.cpp/models/ggml-small-q5_1.bin").strip()
    cfg.stt_local_timeout = int(env.get("STT_LOCAL_TIMEOUT_SECONDS") or "600")
    # Spend control
    cfg.budget_daily_usd = float(env.get("BUDGET_DAILY_USD") or "1.0")
    cfg.budget_monthly_usd = float(env.get("BUDGET_MONTHLY_USD") or "15.0")
    cfg.pricing_json = (env.get("PRICING_JSON") or "").strip()
    # Learning
    cfg.habit_threshold = int(env.get("HABIT_THRESHOLD") or "10")
    # Housekeeping: how many review .md exports to keep on disk
    cfg.review_keep = int(env.get("REVIEW_KEEP") or "10")
    # Knowledge Q&A (ask): semantic retrieval over the KB
    cfg.ask_top_k = int(env.get("ASK_TOP_K") or "6")
    cfg.ask_context_chars = int(env.get("ASK_CONTEXT_CHARS") or "6000")
    cfg.chunk_chars = int(env.get("CHUNK_CHARS") or "800")
    # Remote fetch (read a URL the operator sends; SSRF-guarded)
    cfg.fetch_enabled = (env.get("FETCH_ENABLED") or "true").strip().lower() == "true"
    cfg.fetch_timeout = int(env.get("FETCH_TIMEOUT_SECONDS") or "20")
    cfg.fetch_max_bytes = int(env.get("FETCH_MAX_BYTES") or str(2 * 1024 * 1024))
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
    return cfg
