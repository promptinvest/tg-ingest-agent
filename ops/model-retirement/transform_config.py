#!/usr/bin/env python3
"""Pure, exact Cara model-retirement transform and redacted dry-run CLI.

The CLI reads a JSON object of environment *strings* from stdin and prints only
the plan. It never loads process environment, writes files, calls providers, or
emits the transformed environment. Import transform_environment for a separate
reviewed installer; this module supplies no installer or deployment authority.
"""
import hashlib
import json
import math
import sys
from decimal import Decimal, InvalidOperation


MODEL_REPLACEMENTS = {
    "deepseek-4-flash": "deepseek-v4.1-flash-1017",
    "openai-gpt-oss-20b": "openai-gpt-oss-120b",
}
# Selected roles use a specific replacement for each retiring fallback.
# Other roles and fresh defaults retain the general retirement mapping above.
ROLE_FALLBACK_REPLACEMENTS = {
    "ingest_balanced": {"deepseek-4-flash": "openai-gpt-oss-120b"},
    "ask_grounded": {"deepseek-4-flash": "openai-gpt-oss-120b"},
    "memory_curator": {"openai-gpt-oss-20b": "deepseek-v4.1-flash-1017"},
}
SELECTED_PRICES = {
    "deepseek-v4.1-flash": [0.30, 1.20],
    "deepseek-v4.1-flash-1017": [0.30, 1.20],
    "openai-gpt-oss-120b": [0.10, 0.70],
}
MAX_CLI_BYTES = 4 * 1024 * 1024


class ConfigError(ValueError):
    """A non-secret diagnostic: input values must never appear in messages."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ConfigError("non-finite JSON number")


def _finite_tree(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigError("non-finite JSON number")
    if isinstance(value, dict):
        for item in value.values():
            _finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item)


def parse_json_object(raw):
    """Decode a strict JSON object without reflecting invalid input in errors."""
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        _finite_tree(value)
    except ConfigError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise ConfigError("invalid JSON object") from None
    if not isinstance(value, dict):
        raise ConfigError("expected JSON object")
    return value


def _dump(value, *, sort_keys=False):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"), sort_keys=sort_keys)


def environment_hash(environment):
    """SHA-256 of sorted compact UTF-8 JSON of the complete string map.

    This is a semantic environment-map binding, not a native .env file hash.
    A native installer must bind the original/rendered file bytes separately.
    """
    try:
        return hashlib.sha256(_dump(environment, sort_keys=True).encode("utf-8")).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ConfigError("environment cannot be canonically encoded") from None


def _pointer(*parts):
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1")
                           for part in parts)


def _record(changes, operation, *parts):
    changes.append({"path": _pointer(*parts), "operation": operation})


def _transform_fallbacks(name, fallbacks, changes):
    replacements = {**MODEL_REPLACEMENTS, **ROLE_FALLBACK_REPLACEMENTS.get(name, {})}
    selected_role = name in ROLE_FALLBACK_REPLACEMENTS
    # Keep existing targets at their existing positions. Repeated entries of
    # one original model remain repeated; only newly converging models coalesce.
    owners = {model: model for model in fallbacks if model not in replacements}
    result = []
    for index, model in enumerate(fallbacks):
        target = replacements.get(model, model)
        if selected_role and target != model and target in owners and owners[target] != model:
            _record(changes, "remove", "LLM_PROFILES_JSON", name, "fallbacks", index)
            continue
        owners.setdefault(target, model)
        if target != model:
            _record(changes, "replace", "LLM_PROFILES_JSON", name, "fallbacks", index)
        result.append(target)
    return result


def _transform_profiles(raw, changes):
    profiles = parse_json_object(raw) if raw.strip() else {}
    changed = False
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            raise ConfigError("LLM_PROFILES_JSON profile must be an object")
        if "primary" in profile:
            primary = profile["primary"]
            if not isinstance(primary, str):
                raise ConfigError("LLM_PROFILES_JSON primary must be a string")
            if primary in MODEL_REPLACEMENTS:
                profile["primary"] = MODEL_REPLACEMENTS[primary]
                _record(changes, "replace", "LLM_PROFILES_JSON", name, "primary")
                changed = True
        if "fallbacks" in profile:
            fallbacks = profile["fallbacks"]
            if not isinstance(fallbacks, list) or not all(isinstance(v, str) for v in fallbacks):
                raise ConfigError("LLM_PROFILES_JSON fallbacks must be a string list")
            updated = _transform_fallbacks(name, fallbacks, changes)
            if updated != fallbacks:
                profile["fallbacks"] = updated
                changed = True
    return _dump(profiles) if changed else raw


def _price_pair(pair):
    if not isinstance(pair, list) or len(pair) != 2:
        raise ConfigError("PRICING_JSON rates must be two-element lists")
    result = []
    for value in pair:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ConfigError("PRICING_JSON rates must be finite nonnegative numbers")
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            raise ConfigError("PRICING_JSON rates must be finite nonnegative numbers") from None
        if not number.is_finite() or number < 0:
            raise ConfigError("PRICING_JSON rates must be finite nonnegative numbers")
        result.append(number)
    return result


def _transform_prices(raw, changes, vision_model=None):
    prices = parse_json_object(raw) if raw.strip() else {}
    # Validate every override before returning a new environment. Invalid existing
    # pricing needs operator attention, not an implicit change to runtime defaults.
    parsed = {model: _price_pair(pair) for model, pair in prices.items()}
    changed = False
    selected_prices = dict(SELECTED_PRICES)
    if vision_model == "mistral-3-14B":
        selected_prices[vision_model] = [0.20, 0.20]
    for model, pair in selected_prices.items():
        if model in parsed:
            if parsed[model] != _price_pair(pair):
                raise ConfigError("selected model already has a conflicting PRICING_JSON override")
        else:
            prices[model] = list(pair)
            _record(changes, "add", "PRICING_JSON", model)
            changed = True
    return _dump(prices) if changed else raw


def transform_environment(environment):
    """Return (new string map, redacted plan), without mutating caller input.

    Changed paths are JSON Pointers through a virtual document in which the two
    JSON-valued environment keys are decoded objects. No arbitrary nested
    ``primary`` keys, prompts, old pricing rows, or unrelated values are rewritten.
    Existing custom prices for selected replacements fail closed on disagreement.
    """
    if (not isinstance(environment, dict)
            or not all(isinstance(k, str) and isinstance(v, str)
                       for k, v in environment.items())):
        raise ConfigError("environment must be an object of string keys and values")
    before_hash = environment_hash(environment)
    updated = dict(environment)
    changes = []
    for key in ("DO_CHAT_MODEL", "ROUTER_MODEL"):
        if updated.get(key) in MODEL_REPLACEMENTS:
            updated[key] = MODEL_REPLACEMENTS[updated[key]]
            _record(changes, "replace", key)
    if updated.get("VISION_MODEL") == "llama-4-maverick":
        updated["VISION_MODEL"] = "mistral-3-14B"
        _record(changes, "replace", "VISION_MODEL")
    if "LLM_PROFILES_JSON" in updated:
        updated["LLM_PROFILES_JSON"] = _transform_profiles(updated["LLM_PROFILES_JSON"], changes)
    updated["PRICING_JSON"] = _transform_prices(
        updated.get("PRICING_JSON", ""), changes, updated.get("VISION_MODEL"))
    plan = {
        "schema": "cara-model-retirement-plan-v1",
        "hash_format": "sha256:sorted-compact-utf8-json-string-map",
        "before_sha256": before_hash,
        "after_sha256": environment_hash(updated),
        "changes": sorted(changes, key=lambda item: item["path"]),
    }
    return updated, plan


def main():
    if len(sys.argv) != 1:
        print("Read a JSON environment map from stdin; no command-line values accepted.",
              file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.buffer.read(MAX_CLI_BYTES + 1)
        if len(raw) > MAX_CLI_BYTES:
            raise ConfigError("input exceeds size limit")
        environment = parse_json_object(raw)
        _, plan = transform_environment(environment)
    except ConfigError as exc:
        print("Configuration rejected: " + str(exc), file=sys.stderr)
        return 2
    print(_dump(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
