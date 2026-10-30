"""Measured cache counters and dated DigitalOcean tariff estimates.

These estimates are separate from Cara's existing budget ledger. Capture only
numeric usage metadata, freeze the tariff and money at response time, and never
reprice historical rows when rendering a report. Missing counters are unknown,
not zero. No prompts, response text, credentials or provider payloads are kept.
"""
from urllib.parse import urlsplit


TARIFF_DATE = "2026-10-29"
TARIFF_SOURCE = "https://docs.digitalocean.com/products/inference/details/pricing/"
MAX_TOKENS = 10_000_000

# USD per million input/output/cache-read tokens. These are public standard
# rates, not the application guard rates or account-specific invoice discounts.
_HOSTED_RATES = {
    "deepseek-v4-pro": (1.74, 3.48, .348),
    "deepseek-v4.1-flash-1017": (.30, 1.20, .006),
    "deepseek-v4.1-flash": (.30, 1.20, .006),
    "deepseek-4-flash": (.14, .28, .028),
    "deepseek-3.2": (.5, 1.6, .15),
    "kimi-k2.6": (.95, 4, .19),
    "llama-4-maverick": (.25, .87, .05),
    "nemotron-nano-12b-v2-vl": (.2, .6, .04),
    "kimi-k3": (3, 15, .3),
    # Current published cached rates were not established for these old slugs.
    "openai-gpt-oss-20b": (.05, .45, None),
    "openai-gpt-oss-120b": (.1, .7, None),
}
_ANTHROPIC = {"anthropic-claude-5.5-haiku", "anthropic-claude-sonnet-5.5"}
_SOL = "openai-gpt-6-1-sol"
_LUNA = "openai-gpt-6-luna"
_MODELS = set(_HOSTED_RATES) | _ANTHROPIC | {_SOL, _LUNA}


class _InvalidUsage(ValueError):
    """A fixed reason code, never provider content."""


def _record(model):
    return {
        "version": 1,
        "status": "missing",
        "reason": "usage_missing",
        "model": model if isinstance(model, str) and model in _MODELS else None,
        "input_tokens": None,
        "output_tokens": None,
        "cached_read_tokens": None,
        "cached_write_tokens": None,
        "cached_write_5m_tokens": None,
        "cached_write_1h_tokens": None,
        "tariff_date": None,
        "tariff_source": None,
        "tariff": None,
        "provider_cost_usd": None,
        "uncached_cost_usd": None,
        "savings_usd": None,
    }


def _do_endpoint(value):
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == "https" and url.hostname == "inference.do-ai.run"
                and url.port in (None, 443) and url.username is None
                and url.password is None and not url.query and not url.fragment
                and url.path.rstrip("/") == "/v1")
    except (ValueError, TypeError):
        return False


def _count(mapping, key):
    if key not in mapping:
        return None
    value = mapping[key]
    if type(value) is not int or not 0 <= value <= MAX_TOKENS:
        raise _InvalidUsage("invalid_token_counter")
    return value


def _nested(usage, key):
    if key not in usage:
        return {}
    value = usage[key]
    if not isinstance(value, dict):
        raise _InvalidUsage("invalid_usage_details")
    return value


def _cache_counts(usage):
    details = _nested(usage, "prompt_tokens_details")
    read = _count(usage, "cache_read_input_tokens")
    read_alias = _count(details, "cached_tokens")
    if read is not None and read_alias is not None and read != read_alias:
        raise _InvalidUsage("conflicting_cache_read_counters")
    if read is None:
        read = read_alias
    write = _count(usage, "cache_created_input_tokens")
    creation = _nested(usage, "cache_creation")
    write_5m = _count(creation, "ephemeral_5m_input_tokens")
    write_1h = _count(creation, "ephemeral_1h_input_tokens")
    if write_5m is not None and write_1h is not None:
        subtotal = write_5m + write_1h
        if subtotal > MAX_TOKENS:
            raise _InvalidUsage("invalid_cache_write_total")
        if write is not None and subtotal != write:
            raise _InvalidUsage("conflicting_cache_write_counters")
        if write is None:
            write = subtotal
    if write is not None:
        for part in (write_5m, write_1h):
            if part is not None and part > write:
                raise _InvalidUsage("conflicting_cache_write_counters")
    return read, write, write_5m, write_1h


def _normalize(model, usage, result):
    prompt = _count(usage, "prompt_tokens")
    output = _count(usage, "completion_tokens")
    read, write, write_5m, write_1h = _cache_counts(usage)
    result.update(output_tokens=output, cached_read_tokens=read,
                  cached_write_tokens=write, cached_write_5m_tokens=write_5m,
                  cached_write_1h_tokens=write_1h)
    if model in _ANTHROPIC:
        # DO's Anthropic prompt counter covers uncached input only. Read/write
        # fields are separate billable categories and must not be omitted.
        total = None if None in (prompt, read, write) else prompt + read + write
    else:
        total = prompt
        if total is not None:
            if read is not None and read > total:
                raise _InvalidUsage("cache_read_exceeds_input")
            if write is not None and write > total:
                raise _InvalidUsage("cache_write_exceeds_input")
            if read is not None and write is not None and read + write > total:
                raise _InvalidUsage("cache_categories_exceed_input")
    if total is not None and total > MAX_TOKENS:
        raise _InvalidUsage("invalid_input_total")
    result["input_tokens"] = total
    if None in (total, output, read) or (model not in _HOSTED_RATES and write is None):
        result["reason"] = "usage_counters_missing"
        return False
    result.update(status="known", reason=None)
    return True


def _rates(model, total):
    if model in _HOSTED_RATES:
        rate_in, rate_out, read = _HOSTED_RATES[model]
        return {"input_per_million": rate_in, "output_per_million": rate_out,
                "cache_read_per_million": read, "cache_write_per_million": None,
                "cache_write_5m_per_million": None, "cache_write_1h_per_million": None,
                "input_semantics": "inclusive", "cache_write_semantics": "included_free"}
    if model == "anthropic-claude-5.5-haiku":
        rates = (.1, .5, .01, .125, .2) if total <= 100_000 else (.5, 2.5, .05, .625, 1)
    elif model == "anthropic-claude-sonnet-5.5":
        rates = (2, 10, .2, 2.5, 4)
    else:
        if model == _LUNA:
            rate_in, rate_out, read, write = (.1, .5, .01, .125) if total <= 272_000 else (.2, .75, .02, .25)
        else:
            rate_in, rate_out, read, write = (2, 10, .1, 2.5) if total <= 272_000 else (4, 15, .2, 5)
        return {"input_per_million": rate_in, "output_per_million": rate_out,
                "cache_read_per_million": read, "cache_write_per_million": write,
                "cache_write_5m_per_million": None, "cache_write_1h_per_million": None,
                "input_semantics": "inclusive", "cache_write_semantics": "charged"}
    rate_in, rate_out, read, write_5m, write_1h = rates
    return {"input_per_million": rate_in, "output_per_million": rate_out,
            "cache_read_per_million": read, "cache_write_per_million": None,
            "cache_write_5m_per_million": write_5m, "cache_write_1h_per_million": write_1h,
            "input_semantics": "exclusive", "cache_write_semantics": "charged_by_ttl"}


def _price(result, model):
    total, output = result["input_tokens"], result["output_tokens"]
    read, write = result["cached_read_tokens"], result["cached_write_tokens"]
    rates = _rates(model, total)
    result["tariff"] = rates
    if read and rates["cache_read_per_million"] is None:
        result["reason"] = "cache_read_rate_unavailable"
        return
    read_cost = read * (rates["cache_read_per_million"] or 0)
    output_cost = output * rates["output_per_million"]
    if model in _HOSTED_RATES:
        input_cost = (total - read) * rates["input_per_million"]
        write_cost = 0
    else:
        input_cost = (total - read - write) * rates["input_per_million"]
        if model in _ANTHROPIC:
            write_5m = result["cached_write_5m_tokens"]
            write_1h = result["cached_write_1h_tokens"]
            if write and None in (write_5m, write_1h):
                result["reason"] = "cache_write_ttl_unknown"
                return
            write_cost = ((write_5m or 0) * rates["cache_write_5m_per_million"]
                          + (write_1h or 0) * rates["cache_write_1h_per_million"])
        else:
            write_cost = write * rates["cache_write_per_million"]
    baseline = (total * rates["input_per_million"] + output_cost) / 1_000_000
    actual = (input_cost + read_cost + write_cost + output_cost) / 1_000_000
    result.update(provider_cost_usd=round(actual, 12),
                  uncached_cost_usd=round(baseline, 12),
                  savings_usd=round(baseline - actual, 12))


def capture(model, usage, *, provider_url, response_model=None):
    """Return bounded, JSON-safe measurements; prices are estimates, never bills.

    A missing returned model uses the explicitly requested model. A different
    returned identity, unknown model or custom endpoint has no assumed tariff.
    Counts are only known when the selected provider's required categories are
    present and consistent; monetary coverage can be narrower than token coverage.
    """
    result = _record(model)
    if not _do_endpoint(provider_url):
        result.update(status="unsupported", reason="provider_endpoint_unsupported")
        return result
    if not isinstance(model, str) or model not in _MODELS:
        result.update(status="unsupported", reason="model_unsupported")
        return result
    if response_model is not None and response_model != model:
        result.update(status="unsupported", reason="response_model_mismatch")
        return result
    result.update(tariff_date="2026-10-30" if model == _LUNA else TARIFF_DATE,
                  tariff_source=TARIFF_SOURCE)
    if usage is None:
        return result
    if not isinstance(usage, dict):
        result.update(status="invalid", reason="invalid_usage_object")
        return result
    try:
        if _normalize(model, usage, result):
            _price(result, model)
    except _InvalidUsage as exc:
        # Contradictory input invalidates the measurement as a whole; retain no
        # partial counters that a future report might accidentally count as known.
        result = _record(model)
        result.update(status="invalid", reason=str(exc),
                      tariff_date="2026-10-30" if model == _LUNA else TARIFF_DATE,
                      tariff_source=TARIFF_SOURCE)
    return result
