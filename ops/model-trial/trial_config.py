"""Pure, reversible Cara trial configuration helpers; this module performs no I/O.

The installed llm module is an explicit argument. Parsing rejects ambiguous input
instead of silently changing the interpretation of the production environment.
Only single-line EnvironmentFile assignments are supported; unrelated bytes are
preserved by update_env_bytes. Never print an entire environment or its values.
"""
from copy import deepcopy
import json
import math
import re
from types import SimpleNamespace


HAIKU = "anthropic-claude-5.5-haiku"
SONNET = "anthropic-claude-sonnet-5.5"
SOL = "openai-gpt-6-1-sol"
FLASH = "deepseek-4-flash"
# Scoped compatibility correction: Sonnet's measured 320/640-token conversation
# attempts failed through the existing repair flow. Qualify Sol conversation at
# exactly 640, preserving all other controls and the original cap for reversion.
TRIAL_CONVERSE_MAX_TOKENS = 640
# USD per million input/output tokens. Reference rates are not the active meter.
NORMAL_REFERENCE_PRICES = {HAIKU: (0.10, 0.50), SONNET: (2.0, 10.0), SOL: (2.0, 10.0)}
# Existing flat-rate gateway cannot select context tiers. Use documented maximum
# tier prices for reservations AND accounting throughout this temporary trial.
# This is a conservative upper meter, not an assertion about provider invoices.
ACCOUNTING_UPPER_PRICES = {HAIKU: (0.50, 2.50), SONNET: (2.0, 10.0), SOL: (4.0, 15.0)}
PRIMARY_BY_PROFILE = {
    "router_fast": HAIKU,
    "ingest_balanced": HAIKU,
    "ask_grounded": SONNET,
    "converse_warm": SOL,
    "memory_curator": HAIKU,
    "memory_consolidate": SONNET,
    "task_planner": SOL,
    "task_synthesis": SOL,
    "improvement_evaluator": SOL,
}
MODEL_KEYS = frozenset({"DO_CHAT_MODEL", "ROUTER_MODEL", "LLM_PROFILES_JSON", "PRICING_JSON"})
_ASSIGN = re.compile(r"^([ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*)(.*)$")
_MISSING = object()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def strict_json(value):
    """JSON object with duplicate members and nonfinite numbers rejected."""
    def constant(_value):
        raise ValueError("nonfinite JSON number")

    try:
        result = json.loads(value, object_pairs_hook=_unique_object, parse_constant=constant)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON configuration") from exc
    if not isinstance(result, dict):
        raise ValueError("JSON configuration must be an object")
    _finite_json(result)
    return result


def _finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite JSON number")
    if isinstance(value, dict):
        for entry in value.values():
            _finite_json(entry)
    elif isinstance(value, list):
        for entry in value:
            _finite_json(entry)


def _decode_value(raw):
    raw = raw.strip(" \t")
    if not raw:
        return ""
    if "\x00" in raw or "\r" in raw or "\n" in raw:
        raise ValueError("multiline or NUL environment value")
    if raw.startswith("'"):
        if not raw.endswith("'") or "'" in raw[1:-1]:
            raise ValueError("invalid single-quoted environment value")
        return raw[1:-1]
    if raw.startswith('"'):
        if len(raw) < 2 or not raw.endswith('"'):
            raise ValueError("invalid double-quoted environment value")
        text = raw[1:-1]
        result = []
        index = 0
        while index < len(text):
            char = text[index]
            if char == '"':
                raise ValueError("unescaped quote in environment value")
            if char == "\\" and index + 1 < len(text) and text[index + 1] in '\\"`$':
                index += 1
                char = text[index]
            elif char == "\\" and index + 1 == len(text):
                raise ValueError("trailing environment escape")
            result.append(char)
            index += 1
        return "".join(result)
    result = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\":
            index += 1
            if index == len(raw):
                raise ValueError("continued environment assignments are unsupported")
            char = raw[index]
        result.append(char)
        index += 1
    return "".join(result)


def parse_env_bytes(raw):
    """Parse a strict single-line systemd EnvironmentFile without filesystem I/O."""
    if not isinstance(raw, bytes):
        raise TypeError("environment input must be bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("environment is not UTF-8") from exc
    result = {}
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        if not line.strip() or line.lstrip().startswith(("#", ";")):
            continue
        match = _ASSIGN.fullmatch(line)
        if not match:
            raise ValueError("unsupported environment assignment")
        key = match.group(2)
        if key in result:
            raise ValueError("duplicate environment key: " + key)
        result[key] = _decode_value(match.group(3))
    return result


def _encode_value(value):
    if not isinstance(value, str) or any(char in value for char in ("\x00", "\r", "\n")):
        raise ValueError("environment updates must be single-line strings")
    if "'" not in value:
        return "'" + value + "'"
    return '"' + re.sub(r'([\\"`$])', r'\\\1', value) + '"'


def update_env_bytes(raw, updates):
    """Replace/remove named assignments, preserving every unrelated line byte-for-byte.

    A None update removes that assignment. New assignments use the file's first
    newline style. Existing assignment indentation/export/key spacing survive.
    """
    parsed = parse_env_bytes(raw)
    for key, value in updates.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError("invalid environment update key")
        if value is not None:
            _encode_value(value)
    chunks = raw.decode("utf-8").split("\n")
    lines = [chunk + "\n" for chunk in chunks[:-1]]
    if chunks[-1]:
        lines.append(chunks[-1])
    newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
    result = []
    seen = set()
    for line in lines:
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        match = _ASSIGN.fullmatch(content)
        key = match.group(2) if match else None
        if key not in updates:
            result.append(line)
            continue
        seen.add(key)
        if updates[key] is not None:
            # Do not rewrite a value that already has the requested meaning.
            result.append(line if parsed[key] == updates[key] else
                          match.group(1) + _encode_value(updates[key]) + ending)
    additions = [key for key in updates if key not in seen and updates[key] is not None]
    if additions and result and not result[-1].endswith(("\n", "\r")):
        result.append(newline)
    for key in additions:
        result.append(key + "=" + _encode_value(updates[key]) + newline)
    output = "".join(result).encode("utf-8")
    expected = dict(parsed)
    for key, value in updates.items():
        if value is None:
            expected.pop(key, None)
        else:
            expected[key] = value
    if parse_env_bytes(output) != expected:
        raise ValueError("environment update did not round-trip")
    return output


def _config(env):
    main = (env.get("DO_CHAT_MODEL") or FLASH).strip()
    return SimpleNamespace(do_model=main,
                           router_model=(env.get("ROUTER_MODEL") or main).strip(),
                           llm_profiles_json=(env.get("LLM_PROFILES_JSON") or "").strip(),
                           pricing_json=(env.get("PRICING_JSON") or "").strip())


def _overrides(env):
    value = env.get("LLM_PROFILES_JSON") or ""
    return strict_json(value) if value.strip() else {}


def _positive_number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value > 0)


def effective_profiles(env, llm_module):
    """Installed defaults plus strictly validated overrides; no llm module import."""
    defaults = deepcopy(llm_module.default_profiles(_config(env)))
    if set(defaults) != set(PRIMARY_BY_PROFILE):
        raise ValueError("installed profile catalogue changed")
    for name, value in _overrides(env).items():
        if name not in defaults or not isinstance(value, dict):
            raise ValueError("unknown or malformed model profile")
        defaults[name].update(value)
    for profile in defaults.values():
        if not isinstance(profile.get("primary"), str) or not profile["primary"].strip():
            raise ValueError("missing profile primary")
        if not isinstance(profile.get("fallbacks"), list) or any(
                not isinstance(value, str) or not value.strip() for value in profile["fallbacks"]):
            raise ValueError("invalid profile fallbacks")
        if type(profile.get("max_tokens")) is not int or profile["max_tokens"] <= 0:
            raise ValueError("invalid profile token cap")
        if type(profile.get("json_required")) is not bool:
            raise ValueError("invalid profile JSON requirement")
        if not _positive_number(profile.get("timeout")) or profile["timeout"] > 90:
            raise ValueError("invalid profile timeout")
        temperature = profile.get("temperature", 0)
        if isinstance(temperature, bool) or not isinstance(temperature, (float, int)) or not math.isfinite(temperature):
            raise ValueError("invalid profile temperature")
    return defaults


def _prices(env):
    raw = env.get("PRICING_JSON") or ""
    prices = strict_json(raw) if raw.strip() else {}
    for key, pair in prices.items():
        if not key or not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("invalid price override")
        for value in pair:
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError("invalid price override")
    return prices


def build_candidate(old_env, llm_module):
    """Return an independent environment containing exactly the approved model delta."""
    old_profiles = effective_profiles(old_env, llm_module)
    overrides = deepcopy(_overrides(old_env))
    for name, primary in PRIMARY_BY_PROFILE.items():
        fallbacks = list(old_profiles[name]["fallbacks"])
        if FLASH not in fallbacks:
            fallbacks.append(FLASH)
        # Preserve every old backup, its order, and all controls except the
        # explicitly approved conversation model/output-cap correction.
        overrides.setdefault(name, {}).update(primary=primary, fallbacks=fallbacks)
        if name == "converse_warm" and primary == SOL:
            overrides[name]["max_tokens"] = TRIAL_CONVERSE_MAX_TOKENS
    prices = _prices(old_env)
    for model, pair in ACCOUNTING_UPPER_PRICES.items():
        if model in prices and tuple(prices[model]) != pair:
            raise ValueError("existing trial-model price conflicts with conservative upper meter")
        prices.setdefault(model, list(pair))
    candidate = dict(old_env)
    candidate.update(DO_CHAT_MODEL=HAIKU, ROUTER_MODEL=HAIKU,
                     LLM_PROFILES_JSON=json.dumps(overrides, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                     PRICING_JSON=json.dumps(prices, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    return candidate


def changed_model_keys(old_env, candidate_env):
    changed = {key for key in set(old_env) | set(candidate_env)
               if old_env.get(key, _MISSING) != candidate_env.get(key, _MISSING)}
    if not changed <= MODEL_KEYS:
        raise ValueError("trial changes unrelated environment keys")
    return changed


def validate_candidate(old_env, candidate_env, llm_module, effective_budgets=(2.0, 12.0)):
    """Validate exact expected delta and budgets supplied from the live SQLite view.

    Caller must read effective budgets with installed llm.budget_limits on a
    read-only connection; environment defaults alone are not authoritative.
    Active prices are documented context-tier upper bounds. The ordinary-rate
    reference metadata must never be confused with settled trial accounting.
    """
    changed_model_keys(old_env, candidate_env)
    if (not isinstance(effective_budgets, (tuple, list)) or len(effective_budgets) != 2
            or any(not _positive_number(value) for value in effective_budgets)
            or tuple(effective_budgets) != (2.0, 12.0)):
        raise ValueError("effective production budgets must remain USD2/day and USD12/month")
    expected = build_candidate(old_env, llm_module)
    if candidate_env != expected:
        raise ValueError("candidate does not match the approved exact model delta")
    before = effective_profiles(old_env, llm_module)
    after = effective_profiles(candidate_env, llm_module)
    for name, profile in before.items():
        before_controls = {key: value for key, value in profile.items() if key not in {"primary", "fallbacks"}}
        after_controls = {key: value for key, value in after[name].items() if key not in {"primary", "fallbacks"}}
        expected_controls = dict(before_controls)
        if name == "converse_warm" and after[name]["primary"] == SOL:
            expected_controls["max_tokens"] = TRIAL_CONVERSE_MAX_TOKENS
        if expected_controls != after_controls:
            raise ValueError("trial changes profile controls")
        expected_backups = list(profile["fallbacks"])
        if FLASH not in expected_backups:
            expected_backups.append(FLASH)
        if after[name]["primary"] != PRIMARY_BY_PROFILE[name] or after[name]["fallbacks"] != expected_backups:
            raise ValueError("trial profile mapping or fallback preservation failed")
    return {"changed_keys": sorted(changed_model_keys(old_env, candidate_env)),
            "profiles": len(after), "budgets_usd": [2.0, 12.0],
            "profile_control_exceptions": {"converse_warm": {
                "model": SOL, "max_tokens_before": before["converse_warm"]["max_tokens"],
                "max_tokens_after": TRIAL_CONVERSE_MAX_TOKENS}},
            "pricing_mode": "conservative_context_tier_upper_bound",
            "normal_reference_rates": {model: list(pair) for model, pair in NORMAL_REFERENCE_PRICES.items()},
            "accounting_upper_rates": {model: list(pair) for model, pair in ACCOUNTING_UPPER_PRICES.items()}}


def candidate_env_bytes(old_raw, llm_module, effective_budgets=(2.0, 12.0)):
    old_env = parse_env_bytes(old_raw)
    candidate = build_candidate(old_env, llm_module)
    validate_candidate(old_env, candidate, llm_module, effective_budgets)
    updates = {key: candidate[key] for key in sorted(changed_model_keys(old_env, candidate))}
    return update_env_bytes(old_raw, updates)


def revert_model_keys(current_env, old_env, candidate_env):
    """CAS-merge only trial-changed model keys, preserving later unrelated edits."""
    changed = changed_model_keys(old_env, candidate_env)
    if any(current_env.get(key, _MISSING) != candidate_env.get(key, _MISSING) for key in changed):
        raise ValueError("trial model keys changed since activation; manual review required")
    result = dict(current_env)
    for key in changed:
        if key in old_env:
            result[key] = old_env[key]
        else:
            result.pop(key, None)
    return result


def revert_env_bytes(current_raw, old_env, candidate_env):
    current = parse_env_bytes(current_raw)
    reverted = revert_model_keys(current, old_env, candidate_env)
    updates = {key: reverted.get(key) for key in sorted(changed_model_keys(old_env, candidate_env))}
    return update_env_bytes(current_raw, updates)
