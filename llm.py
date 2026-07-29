#!/usr/bin/env python3
"""DigitalOcean Gradient inference gateway.

Single control point for ALL model calls: every chat/STT request is priced
and logged to llm_usage, and refused when the daily/monthly budget is
exhausted. Skills never talk to the API directly.
"""
import base64
import http.client
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import common
import store
from common import log


class LLMError(Exception):
    def __init__(self, message, *, transient=False):
        super().__init__(message)
        self.transient = bool(transient)


class BudgetExceeded(LLMError):
    def __init__(self, period, spent, limit):
        super().__init__(f"AI budget exhausted ({period}: ${spent:.2f} of ${limit:.2f})")
        self.period = period
        self.spent = spent
        self.limit = limit


# USD per 1M tokens (input, output); STT priced per audio minute.
# Override via PRICING_JSON env, e.g. {"openai-gpt-4o": [2.5, 10.0]}.
# (USD per 1M input, per 1M output). DO Gradient serverless rates, 2026-06
# (docs.digitalocean.com/products/inference/details/pricing). Keep slugs in sync
# with DO_CHAT_MODEL / VISION_MODEL / LLM_PROFILES_JSON — a model MISSING here is
# billed at DEFAULT_CHAT_PRICE ($3/$15), which silently 3-10x's the meter and
# trips the daily budget on phantom dollars (see the 2026-06-19 spike).
DEFAULT_PRICING = {
    # Anthropic (most EOL'd on DO; kept for historical rows)
    "anthropic-claude-haiku-4.5": (1.0, 5.0),
    "anthropic-claude-4.6-sonnet": (3.0, 15.0),
    "anthropic-claude-4.5-sonnet": (3.0, 15.0),
    "openai-gpt-4o": (2.5, 10.0),
    "openai-gpt-4o-mini": (0.15, 0.6),
    # Open-weight models Cara actually runs today
    "deepseek-v4-pro": (1.392, 2.784),
    "deepseek-4-flash": (0.112, 0.224),
    "deepseek-3.2": (0.425, 1.36),
    "nemotron-3-nano-omni": (0.50, 0.90),
    "nemotron-nano-12b-v2-vl": (0.20, 0.60),
    # Vision model (own-photo reactions): open Llama-4 multimodal.
    "llama-4-maverick": (0.25, 0.87),
    "openai-gpt-oss-20b": (0.05, 0.45),
    "openai-gpt-oss-120b": (0.10, 0.70),
    "kimi-k3": (3.0, 15.0),
    "kimi-k2.6": (0.76, 3.20),
    "kimi-k2.5": (0.375, 2.025),
    # router:cara was a DO Inference Router (meta-endpoint) that dispatched to a
    # cheap open-weight model; price it at the deepseek-flash rate it targeted.
    "router:cara": (0.14, 0.28),
}
DEFAULT_CHAT_PRICE = (3.0, 15.0)  # unknown models priced conservatively
STT_PRICE_PER_MINUTE = 0.006
EMBED_PRICE_PER_1M = 0.02  # BGE-M3-class embedding, USD per 1M tokens
# Health probes must not hold the single thread for the full LLM_TIMEOUT: three
# models x 90 s is 4.5 minutes of a frozen bot during exactly the outage the
# monitor exists to report. Reachability needs seconds, not a full generation.
HEALTH_PROBE_TIMEOUT_SECONDS = 10


def pricing_table(cfg):
    """Effective prices (defaults + PRICING_JSON overrides).

    Memoized on the cfg and keyed by the env string, so the JSON is parsed — and an
    invalid-JSON warning logged — once per process instead of on every model call."""
    key = cfg.pricing_json
    cached = getattr(cfg, "_pricing_cache", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    table = dict(DEFAULT_PRICING)
    if key:
        try:
            for model, pair in json.loads(key).items():
                table[model] = (float(pair[0]), float(pair[1]))
        except Exception as exc:
            log(f"PRICING_JSON ignored (invalid): {exc!r}")
    try:
        cfg._pricing_cache = (key, table)
    except AttributeError:      # an exotic cfg object — correctness first, cache optional
        pass
    return table


def chat_cost(model, tokens_in, tokens_out, table):
    price_in, price_out = table.get(model, DEFAULT_CHAT_PRICE)
    return (tokens_in * price_in + tokens_out * price_out) / 1_000_000


def configured_models(cfg, profile_table=None):
    """Every chat-model slug this process may call: the configured chat/router/vision
    models plus each profile's primary and fallbacks."""
    slugs = [getattr(cfg, "do_model", ""), getattr(cfg, "router_model", ""),
             getattr(cfg, "vision_model", "")]
    table = profiles(cfg) if profile_table is None else profile_table
    for prof in table.values():
        slugs.append(prof.get("primary"))
        slugs.extend(prof.get("fallbacks") or [])
    out = []
    for slug in slugs:
        slug = str(slug or "").strip()
        if slug and slug not in out:
            out.append(slug)
    return out


def unpriced_models(cfg, profile_table=None):
    """Configured slugs MISSING from the pricing table. Every one of them bills at
    DEFAULT_CHAT_PRICE ($3/$15) — a typo in DO_CHAT_MODEL/ROUTER_MODEL/VISION_MODEL/
    LLM_PROFILES_JSON therefore inflates the meter 3-10x in complete silence and
    trips the budget on phantom dollars (the 2026-06-19 lockout)."""
    table = pricing_table(cfg)
    return [m for m in configured_models(cfg, profile_table) if m not in table]


_UNPRICED_SEEN = set()


def note_unpriced_model(conn, model, skill=None):
    """First time this process BILLS an unpriced slug: log it and stamp the trace.
    Returns True when it was the first sighting. Nothing used to notice at all."""
    if not model or model in _UNPRICED_SEEN:
        return False
    _UNPRICED_SEEN.add(model)
    message = (f"model {model} not in pricing table — billed at default "
               f"${DEFAULT_CHAT_PRICE[0]}/${DEFAULT_CHAT_PRICE[1]} per 1M tokens")
    log(message)
    from common import current_trace
    if current_trace():
        store.trace_event(conn, current_trace(), "llm.unpriced_model", message,
                          level="warn", skill=skill, data={"model": model})
    return True


def budget_limits(cfg, conn):
    """Effective (daily, monthly) caps: a runtime override the boss set, else the
    configured defaults."""
    def _eff(key, default):
        try:
            return float(store.pref_get(conn, key) or default)
        except (TypeError, ValueError):
            return default
    return _eff("budget_daily_usd", cfg.budget_daily_usd), \
        _eff("budget_monthly_usd", cfg.budget_monthly_usd)


def budget_state(cfg, conn):
    """Returns (state, period, spent, limit); state in ok|warn|stop."""
    daily, monthly = budget_limits(cfg, conn)
    for period, limit in (("day", daily), ("month", monthly)):
        spent = store.usage_total(conn, period)
        if limit > 0 and spent >= limit:
            return "stop", period, spent, limit
    for period, limit in (("day", daily), ("month", monthly)):
        spent = store.usage_total(conn, period)
        if limit > 0 and spent >= 0.8 * limit:
            return "warn", period, spent, limit
    return "ok", "day", store.usage_total(conn, "day"), daily


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


def _fallback_summary(profile, model, exc):
    """Bounded structured fallback text for durable traces/reviews.

    Provider response bodies belong in neither: they are noisy, unstable and may contain
    request details. Runtime logs still identify the profile/model and HTTP class.
    """
    text = str(exc or "")
    match = re.search(r"\bHTTP\s+(\d{3})\b", text, flags=re.IGNORECASE)
    reason = f"HTTP {match.group(1)}" if match else type(exc).__name__
    return f"{profile}:{model} failed: {reason}", {
        "profile": profile,
        "model": model,
        "error_type": type(exc).__name__,
        "http_status": int(match.group(1)) if match else None,
    }


def _base_url(cfg):
    base = cfg.do_base_url.rstrip("/")
    return base if base.endswith("/v1") else base + "/v1"


def _estimate_tokens(text):
    """Rough token count for when the provider omits a usage block. ~4 chars/token
    holds for Latin text; Cyrillic costs roughly 2-3 tokens per character on these
    tokenizers, so a flat //4 undercounts a Russian conversation 2-3x — and this
    estimate IS the metering backstop that keeps the budget honest."""
    text = str(text or "")
    if not text:
        return 0
    cyrillic = sum(1 for c in text if "Ѐ" <= c <= "ӿ")
    return len(text) // (2 if cyrillic * 2 > len(text) else 4)


def _estimate_prompt_tokens(messages):
    """Prompt-token estimate for when the provider omits a usage block. Counts only
    TEXT — a multimodal message's content is a list of parts, and stringifying it
    would fold the base64 image blob into the count and massively over-bill (the
    mirror of the $0-undercount this fallback fixes)."""
    parts = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
    # Per PART, not on the joined string: Cara's prompts routinely mix an English
    # system/schema block with Russian content, and one script decision for the
    # whole thing would score a majority-Latin prompt at //4 and reinstate the
    # Cyrillic undercount for exactly the mixed case that is most common.
    return sum(_estimate_tokens(p) for p in parts)


def chat(cfg, conn, skill, messages, max_tokens=300, model=None, temperature=0,
         timeout=None):
    """Budget-guarded chat completion; logs usage; returns content string."""
    # The single longest thing this process does is wait on a model. Mark progress
    # HERE rather than only between updates: with failover a routed turn is
    # 2 models x 2 attempts x LLM_TIMEOUT, and no watchdog budget can cover that.
    common.watchdog_ping()
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
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout or cfg.llm_timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise LLMError(
            _redacted_http_error(exc, cfg.do_key),
            transient=exc.code in {408, 425, 429} or exc.code >= 500) from exc
    except URLError as exc:
        raise LLMError(
            f"inference request failed: {exc.reason}", transient=True) from exc
    except OSError as exc:
        # A read-timeout during response.read() raises a bare socket.timeout/
        # TimeoutError (NOT a URLError) — wrap it so callers' except LLMError sees it.
        raise LLMError(
            f"inference request failed: {exc}", transient=True) from exc
    except (http.client.HTTPException, UnicodeDecodeError) as exc:
        # A connection cut mid-body raises IncompleteRead (an HTTPException, NOT an
        # OSError); a body truncated inside a multibyte char (Cyrillic!) raises
        # UnicodeDecodeError. Unwrapped, either bypasses failover/cooldown entirely
        # and re-runs the whole update's side effects on retry.
        raise LLMError(
            f"inference response truncated/malformed: {exc!r}",
            transient=True) from exc
    except json.JSONDecodeError as exc:
        raise LLMError(
            "inference response was not valid JSON", transient=True) from exc
    elapsed = max(0.0, time.monotonic() - started)
    choices = data.get("choices") or []
    content = str((choices[0].get("message") or {}).get("content") or "") if choices else ""
    # Meter the call BEFORE the no-choices guard so a billed-but-empty response is still
    # counted. If the provider omits a usage block (some meta-routes do), estimate from
    # text length (script-aware, see _estimate_tokens) instead of logging $0 — an
    # unmetered model silently under-counts the budget and the real DO bill can then
    # blow past the "enforced" cap.
    usage = data.get("usage") or {}
    # EITHER side may be missing on its own (providers that report prompt_tokens and
    # omit completion_tokens are common), and a half-guessed row must not look
    # provider-reported.
    guessed = [side for side, key in (("in", "prompt_tokens"), ("out", "completion_tokens"))
               if not usage.get(key)]
    estimated = bool(guessed)
    tokens_in = int(usage.get("prompt_tokens") or _estimate_prompt_tokens(messages))
    tokens_out = int(usage.get("completion_tokens") or _estimate_tokens(content))
    table = pricing_table(cfg)
    store.usage_add(
        conn, skill, "chat", model, tokens_in, tokens_out,
        seconds=elapsed,
        cost_usd=chat_cost(model, tokens_in, tokens_out, table),
    )
    # Metered first (a billed call is always counted), THEN the two loudness
    # signals: an unpriced slug is billing at the punitive default, and an
    # estimated usage row is a guess rather than the provider's own count.
    if model not in table:
        note_unpriced_model(conn, model, skill)
    if estimated:
        from common import current_trace
        if current_trace():
            store.trace_event(
                conn, current_trace(), "llm.usage_estimated",
                f"{model}: provider omitted {'/'.join(guessed)} usage — "
                f"metered from text length",
                level="warn", skill=skill,
                data={"model": model, "tokens_in": tokens_in, "tokens_out": tokens_out,
                      "guessed": guessed})
    if not choices:
        raise LLMError("inference response had no choices")
    return content


def model_ok(cfg, conn, model, timeout=None):
    """Lightweight reachability check for a chat model: (True, '') if a tiny
    call succeeds, else (False, short_reason). Used by the model-health monitor.

    The probe timeout is DELIBERATELY short (and independent of LLM_TIMEOUT): the
    monitor runs inline on the only thread, so a dead provider must cost seconds
    per model, not a 90 s hang per model during the very outage it is reporting."""
    try:
        chat(cfg, conn, "healthcheck", [{"role": "user", "content": "ping"}],
             max_tokens=5, model=model, temperature=0,
             timeout=timeout or HEALTH_PROBE_TIMEOUT_SECONDS)
        return True, ""
    except LLMError as exc:
        return False, model_health_reason(str(exc))


def whisper_server_ok(cfg, timeout=None):
    """Reachability of the warm whisper-server (STT_MODE=local_server). ANY HTTP
    answer proves the process is up (its root path has no handler), so only a
    connection-level failure or a timeout counts as down. Without this the STT
    backend was the one model dependency the health monitor never watched.

    The reason is bounded here rather than through `model_health_reason` — that
    helper classifies PROVIDER errors and would flatten every on-box failure to
    "model health check failed", which is exactly the detail the boss needs to
    know whether to restart the unit."""
    url = cfg.whisper_server_url.rstrip("/") + "/"
    try:
        with urlopen(Request(url, method="GET"),
                     timeout=timeout or HEALTH_PROBE_TIMEOUT_SECONDS):
            return True, ""
    except HTTPError:
        return True, ""          # answered (404/405) — the server is alive
    except URLError as exc:
        return False, f"speech server unreachable ({_short_reason(exc.reason)})"
    except OSError as exc:
        return False, f"speech server timed out ({_short_reason(exc)})"
    except Exception as exc:  # noqa: BLE001 — a probe must never raise into the loop
        return False, f"speech server check failed ({type(exc).__name__})"


def _short_reason(value):
    """One bounded line — a health reason goes straight to Telegram."""
    return " ".join(str(value or "").split())[:80]


def model_health_reason(error):
    """Bounded, user-safe health reason; provider response bodies never escape."""
    text = str(error or "")
    low = text.casefold()
    match = re.search(r"http\s+(\d{3})", low)
    code = match.group(1) if match else None
    if _is_transient_llm_error(low):
        if code == "429" or "rate_limit" in low or "overload" in low:
            return "temporary provider overload (HTTP 429)"
        if code:
            return f"temporary provider error (HTTP {code})"
        return "temporary provider timeout"
    if code in ("401", "403"):
        return f"model access denied (HTTP {code})"
    if code == "404":
        return "model unavailable (HTTP 404)"
    return f"model health check failed{f' (HTTP {code})' if code else ''}"


def model_health_reason_is_transient(reason):
    return str(reason or "").startswith("temporary provider ")


def _sniff_image_mime(data):
    """Best-effort image MIME from magic bytes — so a WEBP sticker isn't mislabeled
    as JPEG (vision models reject a wrong content-type). Defaults to image/jpeg."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def _vision_text_is_garbled(text):
    """A vision description we must NOT trust and should treat as 'no usable read'.
    On this DO tier the open vision models (e.g. llama-4-maverick) sometimes leak a
    reply in the WRONG script — a whole Chinese sentence for a ru/en task — or return
    a degenerate stub. Folding that into the conversation makes Cara talk gibberish, so
    a garbled read is downgraded to empty and the caller falls back to a warm 'couldn't
    make it out' acknowledgment instead of inventing content."""
    t = (text or "").strip()
    if len(t) < 4:
        return True
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return True  # digits/punctuation only, e.g. "Article 1(5)(1)(32)(11)"
    def _cjk(c):
        o = ord(c)
        return (0x3040 <= o <= 0x9FFF) or (0xAC00 <= o <= 0xD7A3) or (0xF900 <= o <= 0xFAFF)
    def _latcyr(c):
        lo = c.lower()
        return ("a" <= lo <= "z") or ("Ѐ" <= c <= "ӿ")
    cjk = sum(1 for c in letters if _cjk(c))
    if cjk / len(letters) > 0.1:          # any real CJK on a ru/en task = a leak
        return True
    if sum(1 for c in letters if _latcyr(c)) / len(letters) < 0.6:
        return True                        # mostly some other script → untrusted
    return False


def vision_chat(cfg, conn, skill, model, image_path, prompt, max_tokens=400):
    """One budget-guarded vision call: the image + a purpose-built prompt, raw
    model content back (media.py's JSON-strict classify/extract prompts go
    through here). '' when the image file can't be read; transport failures
    raise LLMError like any chat call. Same plumbing as describe_image, without
    its describe-specific prompt/garbled-read policy — callers own their own
    output parsing."""
    try:
        data = Path(image_path).read_bytes()
    except OSError:
        return ""
    b64 = base64.b64encode(data).decode("ascii")
    mime = _sniff_image_mime(data)
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]}]
    return chat(cfg, conn, skill, messages, max_tokens=max_tokens, model=model)


def describe_image(cfg, conn, skill, model, image_path, lang="ru", prompt=None):
    """Use a vision-capable model to produce a short text description of an image,
    so a non-vision text model can still categorize a photo post (or know what a
    sticker depicts). '' on failure OR on a garbled/wrong-script read."""
    try:
        data = Path(image_path).read_bytes()
    except OSError:
        return ""
    b64 = base64.b64encode(data).decode("ascii")
    mime = _sniff_image_mime(data)
    prompt = prompt or (
        "Опиши коротко и ТОЛЬКО на русском языке, что на изображении: текст, объекты, "
        "суть. Никогда не отвечай на других языках."
        if lang == "ru" else
        "Briefly describe this image in ENGLISH ONLY: any text, objects, and the gist. "
        "Never answer in any other language.")
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]}]
    try:
        out = (chat(cfg, conn, skill, messages, max_tokens=400, model=model) or "").strip()
    except LLMError as exc:
        log(f"image describe ({model}) failed: {exc}")
        return ""
    if _vision_text_is_garbled(out):
        log(f"image describe ({model}) discarded as garbled/wrong-script: {out[:60]!r}")
        return ""
    return out


def default_profiles(cfg):
    """Named profiles → model + fallbacks. Primary defaults to the configured
    chat model; fallback is a different-family model so a provider hiccup on
    one route can still be served. Override wholesale via LLM_PROFILES_JSON."""
    primary = cfg.do_model
    router = cfg.router_model
    # Default fallback: an open-weight model that's ACTUALLY reachable on this DO tier.
    # (openai-gpt-4o was the old default but it 403s for the subscription tier — a dead
    # fallback on any fresh deploy that hasn't set LLM_PROFILES_JSON. gpt-oss-20b is the
    # slug the live box overrides to, and it's priced in DEFAULT_PRICING.)
    fb = ["openai-gpt-oss-20b"]
    return {
        "router_fast": {"primary": router, "fallbacks": fb, "max_tokens": 200, "json_required": True},
        "ingest_balanced": {"primary": primary, "fallbacks": fb, "max_tokens": 600, "json_required": True},
        "ask_grounded": {"primary": primary, "fallbacks": [], "max_tokens": 500, "json_required": False},
        # Warm free-form conversation as Cara. A little temperature so she sounds
        # alive rather than canned; a fallback so a chat never dead-ends.
        "converse_warm": {"primary": primary, "fallbacks": fb, "max_tokens": 320,
                          "json_required": False, "temperature": 0.7},
        "memory_curator": {"primary": primary, "fallbacks": fb, "max_tokens": 700, "json_required": True},
        # Weekly memory consolidation (dedup + contradiction judgment) — infrequent but needs
        # real semantic judgment, so it gets a stronger model than the fast curator.
        "memory_consolidate": {"primary": primary, "fallbacks": fb, "max_tokens": 700, "json_required": True},
        # Compound work is infrequent and high-leverage. Keep the fast model for
        # routing; use the stronger open model for bounded plans/synthesis, with
        # Flash as the independent-family availability backstop.
        "task_planner": {"primary": "deepseek-v4-pro",
                         "fallbacks": ["deepseek-4-flash"],
                         "max_tokens": 1600, "json_required": True},
        "task_synthesis": {"primary": "deepseek-v4-pro",
                           "fallbacks": ["deepseek-4-flash"],
                           "max_tokens": 1200, "json_required": True},
        "improvement_evaluator": {"primary": "deepseek-v4-pro",
                                  "fallbacks": ["deepseek-4-flash"],
                                  "max_tokens": 1000, "json_required": True},
        # (review_balanced retired 2026-07-25: the weekly review is deterministic,
        # nothing requested the profile — a dead entry only invites stale config.)
    }


def profiles(cfg):
    """Named model profiles.

    Memoized on the cfg and keyed by the strings it is built from, so the env JSON is
    parsed — and its warnings logged — once per process rather than on every routed
    turn (profiles() is called several times per update)."""
    key = (cfg.llm_profiles_json, cfg.do_model, cfg.router_model)
    cached = getattr(cfg, "_profiles_cache", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    table = default_profiles(cfg)
    if cfg.llm_profiles_json:
        try:
            for name, prof in json.loads(cfg.llm_profiles_json).items():
                table.setdefault(name, {}).update(prof)
        except Exception as exc:
            log(f"LLM_PROFILES_JSON ignored (invalid): {exc!r}")
    # A brand-new profile added via LLM_PROFILES_JSON without a "primary" would
    # KeyError in chat_profile (not an LLMError, so it escapes every skill's
    # except LLMError and dies to the loop). Backfill a primary from the configured
    # chat model so a partial override can't crash a turn.
    for name, prof in table.items():
        if not prof.get("primary"):
            prof["primary"] = cfg.do_model
            log(f"profile {name!r} had no primary; defaulted to {cfg.do_model}")
        fb = prof.get("fallbacks")
        if isinstance(fb, str):
            # `"fallbacks": "slug"` (an easy typo for the documented list) would
            # iterate per CHARACTER downstream — one doomed API call, cooldown
            # row and unpriced-model warning per letter (2026-07-27 review).
            prof["fallbacks"] = [fb.strip()] if fb.strip() else []
            log(f"profile {name!r} fallbacks was a string; wrapped as {prof['fallbacks']}")
        elif fb is not None and not isinstance(fb, (list, tuple)):
            # Any other scalar would TypeError out of chat_profile — not an
            # LLMError, so it would escape every skill's handler and kill the turn.
            prof["fallbacks"] = []
            log(f"profile {name!r} fallbacks was {type(fb).__name__}, not a list; ignored")
    missing = unpriced_models(cfg, table)
    if missing:
        log(f"WARNING: model slug(s) missing from the pricing table, so every call "
            f"bills at the ${DEFAULT_CHAT_PRICE[0]}/${DEFAULT_CHAT_PRICE[1]} default: "
            f"{', '.join(missing)}")
    try:
        cfg._profiles_cache = (key, table)
    except AttributeError:      # an exotic cfg object — correctness first, cache optional
        pass
    return table


def _is_transient_llm_error(msg):
    """A TRANSIENT provider hiccup (DO 'Platform overloaded' 429s are frequent), as
    opposed to a hard error (403 tier-lock, 401 auth, 404). A transient error should
    be retried and must NOT bench the model for the full cooldown — otherwise one 429
    on the fast router strands every request on a weaker fallback for minutes (the
    recorded bug: 'опиши фото' mis-routed to converse and Cara hallucinated)."""
    m = (msg or "").lower()
    if "http 429" in m or "rate_limit" in m or "overload" in m:
        return True
    if "http 500" in m or "http 502" in m or "http 503" in m or "http 504" in m:
        return True
    # A body cut mid-flight (IncompleteRead / mid-multibyte decode) or a garbage
    # body is a CONNECTION accident, not a broken model: retry the preferred model
    # once and bench it only briefly, instead of stranding every later request on
    # a weaker fallback for the full cooldown.
    if "truncated/malformed" in m or "was not valid json" in m:
        return True
    return "timeout" in m or "timed out" in m


def chat_profile(cfg, conn, skill, messages, *, profile, max_tokens=None, json_required=None):
    """Budget-guarded chat with model failover and cooldowns. Tries the
    profile's primary then fallbacks, skipping models on active cooldown. A transient
    overload (429/5xx/timeout) gets ONE quick same-model retry before failover and only
    a brief cooldown, so the preferred model isn't benched for minutes on a blip.
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
    failed_models = []
    primary_model = prof["primary"]
    for model in active:
        content = None
        for attempt in range(2):
            try:
                content = chat(cfg, conn, skill, messages, max_tokens=mt, model=model,
                               temperature=temp)
                break
            except BudgetExceeded:
                raise  # budget hard-stop sits above failover
            except LLMError as exc:
                last_exc = exc
                transient = _is_transient_llm_error(str(exc))
                if transient and attempt == 0:
                    time.sleep(0.8)      # a 'Platform overloaded' 429 usually clears in ~1s
                    continue             # retry the SAME (preferred) model once
                # Bench only briefly for a transient blip; a hard error gets the full cooldown.
                cd = min(cfg.llm_fallback_cooldown, 20) if transient else cfg.llm_fallback_cooldown
                store.cooldown_set(conn, profile, model, cd, str(exc)[:200])
                fallback_message, fallback_data = _fallback_summary(profile, model, exc)
                if current_trace():
                    store.trace_event(conn, current_trace(), "llm.fallback",
                                      fallback_message, level="warn", skill=skill,
                                      data=fallback_data)
                failed_models.append(model)
                log(f"{fallback_message}; trying next")
                break
        if content is None:
            continue  # move to the next fallback model
        if jr and parse_llm_json(content) is None:
            last_content = content
            if current_trace():
                store.trace_event(conn, current_trace(), "llm.fallback",
                                  f"{profile}:{model} returned non-JSON", level="warn", skill=skill)
            failed_models.append(model)
            continue  # try a fallback model for a clean JSON object
        if current_trace() and (failed_models or model != primary_model):
            store.trace_event(
                conn, current_trace(), "llm.failover_served",
                f"{profile}: served by {model} after failover", level="info", skill=skill,
                data={"profile": profile, "served_by": model,
                      "failed_models": list(dict.fromkeys(failed_models))},
            )
        return content
    if last_content is not None:
        if current_trace() and failed_models:
            store.trace_event(
                conn, current_trace(), "llm.failover_failed",
                f"{profile}: no model returned valid structured output",
                level="warn", skill=skill,
                data={"profile": profile,
                      "failed_models": list(dict.fromkeys(failed_models))},
            )
        return last_content  # let the caller's defensive parsing handle it
    if current_trace() and failed_models:
        store.trace_event(
            conn, current_trace(), "llm.failover_failed",
            f"{profile}: all available models failed", level="warn", skill=skill,
            data={"profile": profile,
                  "failed_models": list(dict.fromkeys(failed_models))},
        )
    raise last_exc or LLMError(f"profile {profile}: all models failed")


def embed(cfg, conn, skill, texts):
    """Budget-guarded embeddings (BGE-M3); logs usage; returns list of
    float vectors aligned with `texts`."""
    if not texts:
        return []
    common.watchdog_ping()      # another bounded network wait — see chat()
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
    started = time.monotonic()
    try:
        with urlopen(request, timeout=cfg.llm_timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise LLMError(
            _redacted_http_error(exc, cfg.do_key),
            transient=exc.code in {408, 425, 429} or exc.code >= 500) from exc
    except URLError as exc:
        raise LLMError(
            f"embeddings request failed: {exc.reason}", transient=True) from exc
    except OSError as exc:
        # Bare socket read-timeout (not a URLError) — wrap so index_message's
        # except LLMError catches it instead of crashing the update handler.
        raise LLMError(
            f"embeddings request failed: {exc}", transient=True) from exc
    except (http.client.HTTPException, UnicodeDecodeError) as exc:
        # Truncated body (IncompleteRead) / mid-multibyte cut — same class as chat().
        raise LLMError(
            f"embeddings response truncated/malformed: {exc!r}",
            transient=True) from exc
    except json.JSONDecodeError as exc:
        raise LLMError(
            "embeddings response was not valid JSON", transient=True) from exc
    elapsed = max(0.0, time.monotonic() - started)
    rows = sorted(data.get("data") or [], key=lambda r: r.get("index", 0))
    vectors = [[float(x) for x in r.get("embedding") or []] for r in rows]
    if len(vectors) != len(texts):
        raise LLMError("embeddings response count mismatch")
    tokens = int((data.get("usage") or {}).get("prompt_tokens")
                 or sum(_estimate_tokens(t) for t in texts))
    store.usage_add(conn, skill, "embed", cfg.embedding_model, tokens, 0,
                    seconds=elapsed,
                    cost_usd=tokens / 1_000_000 * EMBED_PRICE_PER_1M)
    return vectors


# -- speech-to-text -----------------------------------------------------------

from common import build_multipart  # noqa: E402 (shared with tg_api/gcal)


def transcribe(cfg, conn, skill, audio_path, duration_seconds):
    """Voice -> text. local = cold whisper-cli; local_server = warm
    whisper-server on localhost (no per-call model reload); remote = an
    OpenAI-compatible transcriptions endpoint."""
    # The longest single bounded step on this thread (ffmpeg + STT_LOCAL_TIMEOUT).
    # The watchdog budget is sized against exactly this, so mark it started.
    common.watchdog_ping()
    if cfg.stt_mode == "local":
        return _transcribe_local(cfg, conn, skill, audio_path, duration_seconds)
    if cfg.stt_mode == "local_server":
        return _transcribe_local_server(cfg, conn, skill, audio_path, duration_seconds)
    return _transcribe_remote(cfg, conn, skill, audio_path, duration_seconds)


WHISPER_SERVER_RETRY_SECONDS = 3.0


def _transcribe_local_server(cfg, conn, skill, audio_path, duration_seconds):
    """POST the audio to the warm whisper-server (/inference). The server is
    launched with --convert, so it ffmpegs the OGG itself; the 190 MB model
    stays loaded between calls. Free, on-box, no model reload.

    Its unit restarts in 5 s, so a connection-level outage is almost always a few
    seconds long — one retry, then the co-installed cold whisper-cli, before the
    boss's voice note is refused. Which path served is logged."""
    audio_path = Path(audio_path)
    fields = {"response_format": "json", "temperature": "0"}
    if cfg.stt_language and cfg.stt_language != "auto":
        fields["language"] = cfg.stt_language  # pin language -> fewer hallucinations
    body, boundary = build_multipart(
        fields, "file", audio_path.name, audio_path.read_bytes(), "audio/ogg",
    )
    url = cfg.whisper_server_url.rstrip("/") + "/inference"
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}",
               "Accept": "application/json"}
    raw = None
    last_exc = None
    for attempt in range(2):
        common.watchdog_ping()   # each attempt is its own STT_LOCAL_TIMEOUT wait
        try:
            with urlopen(Request(url, data=body, headers=headers, method="POST"),
                         timeout=cfg.stt_local_timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
            if attempt:
                log("whisper-server answered on retry")
            break
        except HTTPError as exc:      # a subclass of URLError: the server DID answer
            raise LLMError(
                f"whisper-server HTTP {exc.code}",
                transient=exc.code in {408, 425, 429} or exc.code >= 500) from exc
        except URLError as exc:
            # Connection-level failure = the process is down/restarting, not
            # refusing this request. Retry once, then fall back to the CLI.
            last_exc = LLMError(f"whisper-server unreachable: {exc.reason}")
            if attempt == 0:
                log(f"whisper-server unreachable ({exc.reason}); retrying in"
                    f" {WHISPER_SERVER_RETRY_SECONDS}s")
                time.sleep(WHISPER_SERVER_RETRY_SECONDS)
        except OSError as exc:
            # A TIMEOUT (socket.timeout is an OSError, not a URLError) means the
            # server accepted the connection and never answered — OOM thrash on a
            # 4 GB box looks exactly like this. That was terminal: the boss's
            # voice note was refused although the co-installed cold CLI could
            # still transcribe it. Fall back instead. No second attempt: the full
            # STT_LOCAL_TIMEOUT already elapsed, and repeating it against a
            # thrashing server only doubles his wait.
            last_exc = LLMError(f"whisper-server timed out: {exc}")
            log(f"whisper-server timed out after {cfg.stt_local_timeout}s ({exc})")
            break
        except http.client.HTTPException as exc:
            raise LLMError(
                f"whisper-server response truncated: {exc!r}",
                transient=True) from exc
    if raw is None:
        # The CLI needs BOTH halves: with the binary present but the model file
        # missing/renamed, falling through would answer "local transcription
        # failed: …" and hide the outage that actually happened.
        if (cfg.whisper_bin and Path(cfg.whisper_bin).exists()
                and cfg.whisper_model and Path(cfg.whisper_model).exists()):
            log("whisper-server still unreachable — transcribing with whisper-cli")
            common.watchdog_ping()   # a cold CLI run is a fresh long step
            try:
                return _transcribe_local(cfg, conn, skill, audio_path, duration_seconds)
            except LLMError as exc:
                # Chain the real cause: without it the reply and the log blame the
                # CLI for an outage that started at the server.
                raise LLMError(f"{exc} (after {last_exc or 'whisper-server unreachable'})") \
                    from last_exc
        raise last_exc or LLMError("whisper-server unreachable")
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
             "-f", str(wav_path), "-l", (cfg.stt_language or "auto"), "-np", "-nt"],
            capture_output=True, check=True, timeout=cfg.stt_local_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise LLMError("local transcription timed out", transient=True) from exc
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
        raise LLMError(
            _redacted_http_error(exc, cfg.do_key),
            transient=exc.code in {408, 425, 429} or exc.code >= 500) from exc
    except URLError as exc:
        raise LLMError(
            f"transcription request failed: {exc.reason}", transient=True) from exc
    except OSError as exc:
        raise LLMError(
            f"transcription request failed: {exc}", transient=True) from exc
    except (http.client.HTTPException, UnicodeDecodeError) as exc:
        raise LLMError(
            f"transcription response truncated/malformed: {exc!r}",
            transient=True) from exc
    except json.JSONDecodeError as exc:
        raise LLMError(
            "transcription response was not valid JSON", transient=True) from exc
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


def match_category_fuzzy(value, categories, value_subset_only=False):
    """The existing category `value` is a near-variant of, or None.

    Beyond the exact casefold match: when the significant-token set of one name
    is a SUBSET of the other's ("AI tools" vs "AI Tools & Resources"), it's the
    same category — the model keeps coining such variants next to an existing
    one and the operator keeps merging them by hand. Used to SNAP a fresh
    suggestion to the canonical existing name; never renames stored data.

    `value_subset_only` drops the other direction (an existing name contained in
    a LONGER `value`). It is right when `value` is a model-proposed category, and
    wrong when `value` is a sentence the boss typed: «это точно не финансы»
    contains «Финансы», so a reply that REJECTS the category matched it. Callers
    that feed free text pass True — then every significant word he wrote has to
    belong to the existing name.

    Free text also needs MORE than a lone word for that remaining direction:
    `toks()` filters no stopwords, so a bare «позже» is a subset of «Прочитать
    позже» and would file — and CONFIRM — a note he was only saying "later"
    about. A single-token reply therefore matches only a single-token category,
    which is what the exact-match path above already handles."""
    exact = match_category(value, categories)
    if exact:
        return exact

    def toks(s):
        return {t for t in re.split(r"[^\w]+", str(s or "").casefold()) if len(t) >= 2}

    vt = toks(value)
    if not vt:
        return None
    lone = value_subset_only and len(vt) == 1
    for category in categories:
        ct = toks(category)
        if not ct:
            continue
        if vt <= ct and not (lone and len(ct) > 1):
            return category
        if ct <= vt and not value_subset_only:
            return category
    return None
