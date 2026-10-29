# Exact environment transformation

`transform_config.py` is a pure helper for the October 29 model-retirement
release. It is not a deployment controller and has no authority to install,
restart, contact models or Telegram, change budgets, or rewrite history.

Import `transform_environment(environment)` with a `dict[str, str]` to receive
`(new_environment, plan)`. Caller input is never mutated. The plan contains
SHA-256 bindings and exact changed paths, without configuration values.

The allowed substitutions are:

| Location | Exact old value | New value |
|---|---|---|
| `DO_CHAT_MODEL`, `ROUTER_MODEL`, profile `primary` and `fallbacks[]` | `deepseek-4-flash` | `deepseek-v4.1-flash-1017` |
| Same locations | `openai-gpt-oss-20b` | `openai-gpt-oss-120b` |
| `VISION_MODEL` only | `llama-4-maverick` | `deepseek-v4.1-flash` |

Profiles are the direct objects in `LLM_PROFILES_JSON`; nested metadata, prompts,
other leaves, list order and unlisted models stay unchanged. In particular,
active commercial/DeepSeek Pro/Kimi primaries are preserved. Matching is exact:
whitespace, case variants and suffixes are not normalized into substitutions.

`PRICING_JSON` gains explicit input/output USD-per-million rates for both selected
DeepSeek V4.1 variants (`[0.30, 1.20]`) and GPT-OSS120B (`[0.10, 0.70]`). Every
existing override, including old model rates and commercial context ceilings,
is retained. An existing numerically equal selected rate is retained as written;
a conflicting rate raises `ConfigError` rather than overwriting custom policy.
Duplicate keys, invalid JSON, invalid profile/model-list shapes, nonfinite
numbers and malformed or negative pricing pairs fail closed. No rejected input
values are included in error messages.

Unchanged environment strings retain their exact bytes when UTF-8 encoded.
Changed embedded JSON is compactly serialized, retaining unrelated semantic
values and object/list order. Paths are JSON Pointers through a virtual document
that decodes `LLM_PROFILES_JSON` and `PRICING_JSON` into objects. Adding a missing
price map records each inserted model leaf.

Hash format is **sorted compact UTF-8 JSON of the entire environment string
map**, not native `.env` bytes. A release operator must separately bind original
and rendered file bytes and preserve native comments, quoting, newlines,
ownership, permissions and unrelated entries. Do not publish a transformed
environment or a private input file as review evidence. The plan alone does not
prove account availability, inference quality, installed state or acceptance.

The CLI reads a JSON string map from stdin (at most 4 MiB), outputs only the
redacted plan, and writes no files:

```text
python -B ops/model-retirement/transform_config.py < sanitized-environment.json
python -B -m unittest discover -s ops/model-retirement -p test_transform_config.py
```

It never reads the process environment or accepts credentials as command-line
arguments. Keep any real environment input within the existing protected
operator boundary. The pure helper checks complement the normal remote
release and installed-layout gates; they do not replace them.
