#!/usr/bin/env python3
"""DigitalOcean Gradient inference gateway.

Single control point for ALL model calls: every chat/STT request is priced
and logged to llm_usage, and refused when the daily/monthly budget is
exhausted. Skills never talk to the API directly.
"""
import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import store
from common import log


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    def __init__(self, period, spent, limit):
        super().__init__(f"AI budget exhausted ({period}: ${spent:.2f} of ${limit:.2f})")
        self.period = period
        self.spent = spent
        self.limit = limit


# USD per 1M tokens (input, output); STT priced per audio minute.
# Override via PRICING_JSON env, e.g. {"openai-gpt-4o": [2.5, 10.0]}.
DEFAULT_PRICING = {
    "anthropic-claude-haiku-4.5": (1.0, 5.0),
    "anthropic-claude-4.6-sonnet": (3.0, 15.0),
    "anthropic-claude-4.5-sonnet": (3.0, 15.0),
    "openai-gpt-4o": (2.5, 10.0),
    "openai-gpt-4o-mini": (0.15, 0.6),
}
DEFAULT_CHAT_PRICE = (3.0, 15.0)  # unknown models priced conservatively
STT_PRICE_PER_MINUTE = 0.006
EMBED_PRICE_PER_1M = 0.02  # BGE-M3-class embedding, USD per 1M tokens


def pricing_table(cfg):
    table = dict(DEFAULT_PRICING)
    if cfg.pricing_json:
        try:
            for model, pair in json.loads(cfg.pricing_json).items():
                table[model] = (float(pair[0]), float(pair[1]))
        except Exception as exc:
            log(f"PRICING_JSON ignored (invalid): {exc!r}")
    return table


def chat_cost(model, tokens_in, tokens_out, table):
    price_in, price_out = table.get(model, DEFAULT_CHAT_PRICE)
    return (tokens_in * price_in + tokens_out * price_out) / 1_000_000


def budget_state(cfg, conn):
    """Returns (state, period, spent, limit); state in ok|warn|stop."""
    for period, limit in (("day", cfg.budget_daily_usd), ("month", cfg.budget_monthly_usd)):
        spent = store.usage_total(conn, period)
        if limit > 0 and spent >= limit:
            return "stop", period, spent, limit
    for period, limit in (("day", cfg.budget_daily_usd), ("month", cfg.budget_monthly_usd)):
        spent = store.usage_total(conn, period)
        if limit > 0 and spent >= 0.8 * limit:
            return "warn", period, spent, limit
    return "ok", "day", store.usage_total(conn, "day"), cfg.budget_daily_usd


def _check_budget(cfg, conn):
    state, period, spent, limit = budget_state(cfg, conn)
    if state == "stop":
        raise BudgetExceeded(period, spent, limit)


_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+")


def _redacted_http_error(exc, access_key):
    try:
        body = exc.read().decode("utf-8")
    except Exception:
        body = ""
    if access_key:
        body = body.replace(access_key, "<redacted>")
    body = _BEARER_PATTERN.sub(r"\1<redacted>", body)
    suffix = f": {body[:500]}" if body else "."
    return f"inference request failed with HTTP {exc.code}{suffix}"


def _base_url(cfg):
    base = cfg.do_base_url.rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def chat(cfg, conn, skill, messages, max_tokens=300, model=None, temperature=0):
    """Budget-guarded chat completion; logs usage; returns content string."""
    _check_budget(cfg, conn)
    model = model or cfg.do_model
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    request = Request(
        f"{_base_url(cfg)}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg.do_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=cfg.llm_timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise LLMError(_redacted_http_error(exc, cfg.do_key)) from exc
    except URLError as exc:
        raise LLMError(f"inference request failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError("inference response was not valid JSON") from exc
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("inference response had no choices")
    usage = data.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    store.usage_add(
        conn, skill, "chat", model, tokens_in, tokens_out,
        cost_usd=chat_cost(model, tokens_in, tokens_out, pricing_table(cfg)),
    )
    return str((choices[0].get("message") or {}).get("content") or "")


def default_profiles(cfg):
    """Named profiles → model + fallbacks. Primary defaults to the configured
    chat model; fallback is a different-family model so a provider hiccup on
    one route can still be served. Override wholesale via LLM_PROFILES_JSON."""
    primary = cfg.do_model
    router = cfg.router_model
    fb = ["openai-gpt-4o"]  # confirmed in DO catalog; different family
    return {
        "router_fast": {"primary": router, "fallbacks": fb, "max_tokens": 200, "json_required": True},
        "ingest_balanced": {"primary": primary, "fallbacks": fb, "max_tokens": 600, "json_required": True},
        "ask_grounded": {"primary": primary, "fallbacks": [], "max_tokens": 500, "json_required": False},
        # Warm free-form conversation as Cara. A little temperature so she sounds
        # alive rather than canned; a fallback so a chat never dead-ends.
        "converse_warm": {"primary": primary, "fallbacks": fb, "max_tokens": 320,
                          "json_required": False, "temperature": 0.7},
        "memory_curator": {"primary": primary, "fallbacks": fb, "max_tokens": 700, "json_required": True},
        "review_balanced": {"primary": primary, "fallbacks": [], "max_tokens": 900, "json_required": False},
    }


def profiles(cfg):
    table = default_profiles(cfg)
    if cfg.llm_profiles_json:
        try:
            for name, prof in json.loads(cfg.llm_profiles_json).items():
                table.setdefault(name, {}).update(prof)
        except Exception as exc:
            log(f"LLM_PROFILES_JSON ignored (invalid): {exc!r}")
    return table


def chat_profile(cfg, conn, skill, messages, *, profile, max_tokens=None, json_required=None):
    """Budget-guarded chat with model failover and cooldowns. Tries the
    profile's primary then fallbacks, skipping models on active cooldown.
    Budget is authoritative — BudgetExceeded never triggers a fallback."""
    from common import current_trace
    prof = profiles(cfg).get(profile) or {
        "primary": cfg.do_model, "fallbacks": [], "max_tokens": 300, "json_required": False}
    mt = max_tokens or prof.get("max_tokens", 300)
    jr = prof.get("json_required", False) if json_required is None else json_required
    temp = prof.get("temperature", 0)
    models = [prof["primary"]] + list(prof.get("fallbacks") or [])
    active = [m for m in models if not store.cooldown_active(conn, profile, m)]
    if not active:
        active = models  # everything is cooling down — try anyway rather than fail outright
    last_exc = None
    last_content = None
    for model in active:
        try:
            content = chat(cfg, conn, skill, messages, max_tokens=mt, model=model,
                           temperature=temp)
        except BudgetExceeded:
            raise  # budget hard-stop sits above failover
        except LLMError as exc:
            last_exc = exc
            store.cooldown_set(conn, profile, model, cfg.llm_fallback_cooldown, str(exc)[:200])
            if current_trace():
                store.trace_event(conn, current_trace(), "llm.fallback",
                                  f"{profile}:{model} failed: {exc}", level="warn", skill=skill)
            log(f"profile {profile} model {model} failed ({exc}); trying next")
            continue
        if jr and parse_llm_json(content) is None:
            last_content = content
            if current_trace():
                store.trace_event(conn, current_trace(), "llm.fallback",
                                  f"{profile}:{model} returned non-JSON", level="warn", skill=skill)
            continue  # try a fallback model for a clean JSON object
        return content
    if last_content is not None:
        return last_content  # let the caller's defensive parsing handle it
    raise last_exc or LLMError(f"profile {profile}: all models failed")


def embed(cfg, conn, skill, texts):
    """Budget-guarded embeddings (BGE-M3); logs usage; returns list of
    float vectors aligned with `texts`."""
    if not texts:
        return []
    _check_budget(cfg, conn)
    payload = {"model": cfg.embedding_model, "input": list(texts)}
    request = Request(
        f"{_base_url(cfg)}/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg.do_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=cfg.llm_timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise LLMError(_redacted_http_error(exc, cfg.do_key)) from exc
    except URLError as exc:
        raise LLMError(f"embeddings request failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError("embeddings response was not valid JSON") from exc
    rows = sorted(data.get("data") or [], key=lambda r: r.get("index", 0))
    vectors = [[float(x) for x in r.get("embedding") or []] for r in rows]
    if len(vectors) != len(texts):
        raise LLMError("embeddings response count mismatch")
    tokens = int((data.get("usage") or {}).get("prompt_tokens")
                 or sum(len(t) for t in texts) // 4)
    store.usage_add(conn, skill, "embed", cfg.embedding_model, tokens, 0,
                    cost_usd=tokens / 1_000_000 * EMBED_PRICE_PER_1M)
    return vectors


# -- speech-to-text -----------------------------------------------------------

from common import build_multipart  # noqa: E402 (shared with tg_api/gcal)


def transcribe(cfg, conn, skill, audio_path, duration_seconds):
    """Voice -> text. local = cold whisper-cli; local_server = warm
    whisper-server on localhost (no per-call model reload); remote = an
    OpenAI-compatible transcriptions endpoint."""
    if cfg.stt_mode == "local":
        return _transcribe_local(cfg, conn, skill, audio_path, duration_seconds)
    if cfg.stt_mode == "local_server":
        return _transcribe_local_server(cfg, conn, skill, audio_path, duration_seconds)
    return _transcribe_remote(cfg, conn, skill, audio_path, duration_seconds)


def _transcribe_local_server(cfg, conn, skill, audio_path, duration_seconds):
    """POST the audio to the warm whisper-server (/inference). The server is
    launched with --convert, so it ffmpegs the OGG itself; the 190 MB model
    stays loaded between calls. Free, on-box, no model reload."""
    audio_path = Path(audio_path)
    body, boundary = build_multipart(
        {"response_format": "json", "temperature": "0"},
        "file", audio_path.name, audio_path.read_bytes(), "audio/ogg",
    )
    request = Request(
        cfg.whisper_server_url.rstrip("/") + "/inference",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=cfg.stt_local_timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise LLMError(f"whisper-server HTTP {exc.code}") from exc
    except URLError as exc:
        raise LLMError(f"whisper-server unreachable: {exc.reason}") from exc
    try:
        text = str((json.loads(raw) or {}).get("text") or "").strip()
    except (ValueError, AttributeError):
        text = raw.strip()
    store.usage_add(conn, skill, "stt", "whisper.cpp-server",
                    seconds=duration_seconds, cost_usd=0.0)
    return text


def _transcribe_local(cfg, conn, skill, audio_path, duration_seconds):
    """whisper.cpp on this host: ffmpeg OGG->16k mono WAV, then whisper-cli.

    Runs niced so a long note does not starve the poll loop's CPU; zero cost,
    still logged to llm_usage for visibility.
    """
    import subprocess
    import tempfile
    wav_path = Path(tempfile.gettempdir()) / (Path(audio_path).stem + ".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(audio_path), "-ar", "16000", "-ac", "1", str(wav_path)],
            capture_output=True, check=True, timeout=120,
        )
        result = subprocess.run(
            ["nice", "-n", "10", cfg.whisper_bin, "-m", cfg.whisper_model,
             "-f", str(wav_path), "-l", "auto", "-np", "-nt"],
            capture_output=True, check=True, timeout=cfg.stt_local_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise LLMError("local transcription timed out") from exc
    except FileNotFoundError as exc:
        raise LLMError(f"local transcription tool missing: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace")[:200]
        raise LLMError(f"local transcription failed: {stderr}") from exc
    finally:
        wav_path.unlink(missing_ok=True)
    text = result.stdout.decode("utf-8", errors="replace").strip()
    store.usage_add(conn, skill, "stt", "whisper.cpp-local",
                    seconds=duration_seconds, cost_usd=0.0)
    return text


def _transcribe_remote(cfg, conn, skill, audio_path, duration_seconds):
    _check_budget(cfg, conn)
    audio_path = Path(audio_path)
    body, boundary = build_multipart(
        {"model": cfg.stt_model},
        "file",
        audio_path.name,
        audio_path.read_bytes(),
        "audio/ogg",
    )
    request = Request(
        f"{_base_url(cfg)}/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {cfg.do_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=cfg.llm_timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise LLMError(_redacted_http_error(exc, cfg.do_key)) from exc
    except URLError as exc:
        raise LLMError(f"transcription request failed: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError("transcription response was not valid JSON") from exc
    text = str(data.get("text") or "").strip()
    cost = (max(duration_seconds, 1) / 60.0) * STT_PRICE_PER_MINUTE
    store.usage_add(conn, skill, "stt", cfg.stt_model, seconds=duration_seconds, cost_usd=cost)
    return text


# -- model output parsing helpers ----------------------------------------------

def parse_llm_json(text):
    if not text:
        return None
    candidates = [text.strip()]
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        candidates.append(fence.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return None


MAX_CATEGORY_CHARS = 40


def normalize_category(value):
    """Collapse whitespace, cap length; None when nothing usable remains."""
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:MAX_CATEGORY_CHARS].strip() or None


def match_category(value, categories):
    """Return the canonical (existing) spelling of value, or None."""
    value = str(value or "").strip()
    for category in categories:
        if value.casefold() == category.casefold():
            return category
    return None
