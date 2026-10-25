# Cara: temporary model trial

Prepared partial configuration-only variant, pending the owner's scope preference.
This runbook does not claim deployment or acceptance. Trial duration is 72 hours
from `accepted.json.at`.

The accepted application source remains
`ee1a319ce53ddc941ebf4b760c084abd865afd19`; install no code, change no native
deployment marker and perform no database/schema restore. The carried source
must match every accepted installed runtime counterpart, including the existing
`verify-cara-runtime.sh`. Documentation-only checkout revisions may differ.

| Work | Primary |
| --- | --- |
| Routing, ingestion, memory candidates, routine media metadata | `anthropic-claude-5.5-haiku` |
| Grounded KB answers, memory consolidation | `anthropic-claude-sonnet-5.5` |
| Planning, synthesis, configured improvement evaluator | `openai-gpt-6-1-sol` |
| Conversation | Exact original effective profile: DeepSeek primary, original 320-token production cap and original fallback chain |

For switched profiles, preserve every existing fallback in its original order;
append `deepseek-4-flash` only where absent. `converse_warm` is explicitly pinned
to its complete original effective profile, with no added fallback or cap change;
changing the main model to Haiku must not alter conversation through inheritance.
There are no profile-control exceptions. Every cap, timeout, temperature, JSON
validation, source/confirmation rule and history remains.
`VISION_MODEL`, BGE-M3
embeddings and local Whisper remain unchanged. The improvement evaluator has no
production call site; its configuration is not evidence of functional testing.

Production budgets remain **USD2/day and USD12/month**, including authoritative
SQLite preference overrides. Only `DO_CHAT_MODEL`, `ROUTER_MODEL`,
`LLM_PROFILES_JSON` and `PRICING_JSON` change.

| USD per million input/output tokens | Ordinary reference | Trial reservation/accounting ceiling |
| --- | --- | --- |
| Haiku | 0.10 / 0.50 | 0.50 / 2.50 |
| Sonnet | 2 / 10 | 2 / 10 |
| Sol | 2 / 10 | 4 / 15 |

The unchanged flat-rate gateway cannot select context tiers; prompt builders do
not guarantee normal-tier sizes. The active meter therefore conservatively uses
the documented tier ceilings. Its ledger is an upper estimate, not the provider
invoice. Ordinary/tier estimates, when calculated separately, must retain that
label and must never replace unknown reservations or weaken budget guards.

## Verify and qualify

On Linux, outside OneDrive, run helper tests against the carried source:

```sh
CARA_TEST_SOURCE="$SRC" python3 -B -m unittest discover -p 'test_*.py'
```

Record absolute `$TOOLS`, `$SRC`, `$RUN`, `$PINS`, `$PROOF` paths and the selected
canonical `operator-files.json` digest as `$PACKAGE_SHA256` before commands.
The original environment must match
`e1ae7f6c65e4076a1a6fb93a87ecf200eb59745f048183acbbea389cfa64b733`;
the exact accepted 78-file protected map must have canonical digest
`763f79140a92fbc2376bf004f217bfb7aab65d7b9d636500a70cb7ac33aa56da`.
Keep originals, backups, raw answers and failures private and immutable.
Failed Sonnet conversation trials at 320/640 tokens and the failed Sol640 trial,
including all charges/raw responses, remain retained and are not deployment proof.
The partial variant requires a fresh exact candidate environment/hash and receipt,
with the unchanged effective conversation profile's canonical SHA bound explicitly.
Reuse cases only when their complete input/profile bindings are unchanged. Do not
change prompts, action-truth guards or application source to force qualification.

Each following controller/cohort invocation is one-shot. Read existing phase
receipts before acting; never replay a started phase.

```sh
python3 "$TOOLS/trial_operator.py" prepare --root "$RUN" --source "$SRC" --protected-manifest "$PINS"
python3 "$TOOLS/qualify_trial.py" --root "$RUN" --output "$RUN/qualification-baseline" --cohort baseline
python3 "$TOOLS/qualify_trial.py" --root "$RUN" --output "$RUN/qualification-candidate" --cohort candidate
```

Qualification has a **USD5 aggregate ceiling** across cohorts, with a USD0.15
maximum conservative reservation per call; normal production budget checks also
apply. Unknown charges stop continuation. This allowance does not authorize paid
replays or work beyond that ceiling.

The original 24-case corpus covers eight active profiles. A partial qualification
must identify the 21 applicable cases for the seven changed active profiles and
bind the unchanged conversation profile separately. The dormant evaluator is
configured but not functionally qualified. Actual builders and isolated
handler/validator replay are used; external effects and host execution are
mocked. They do not establish all production behavior, screenshot quality,
exhaustive fleet correctness, dormant-evaluator behavior or long-term quality.
Review retained raw RU/EN answers for semantic correctness and tone. The fresh
`cara.model-trial-qualified/v1` proof must bind that review, complete functional
results, accounted usage, exact candidate environment/source/runtime and the
selected operator package. A successful transport is insufficient.

```sh
python3 "$TOOLS/trial_operator.py" activate --root "$RUN" --qualification "$PROOF" --package-sha256 "$PACKAGE_SHA256"
```

Activation requires fresh idle checks, current-inference containment, verified
online/stopped backups, exact stopped-data parity and protected-file preservation.
Only the main service stops/starts; peer services remain intact. Retain acceptance
and three final health checks. Failure containment merges model keys only.

## Observe, review and conditionally revert

Bind the observer to the accepted receipt SHA, recorded database and acceptance
timestamp. Invoke its fixed schedule every six hours, through hour 72:

```sh
python3 "$TOOLS/observe_trial.py" scheduled --db "$DB" --trial-start "$ACCEPTED_AT" --output-dir "$OBSERVATIONS" --binding-sha256 "$ACCEPTANCE_SHA256"
```

Stop the timer when `timer_stop_required` is true. Reports are read-only metadata:
ledger costs, unknown reservations, fallbacks/failures, latency samples, delivery
state and service identity/restarts. They make no model calls, send no messages,
judge no answer quality and never revert automatically. Fewer than 20 ordinary
samples leaves p95 unestablished; low traffic does not count as successful quality.
Review actual conversations and metadata together after several days.

If reversion is needed under the owner's trial instruction:

```sh
python3 "$TOOLS/trial_operator.py" revert --root "$RUN"
```

Reversion is single-use and conditional on exact recorded source/artifact bindings,
unchanged trial model keys and safe idle state. It preserves later unrelated
environment edits, takes a stopped backup and restores only trial-changed model
keys. Never restore an old database, reinstall old code, erase usage/history or
replay messages. A conflicting later model edit requires review, not an overwrite.

## October 25 selected partial cohort and fixed monitor

The original baseline and Sonnet320 candidate ran once in `attempt1`. Sonnet640
failed in `attempt2`, and Sol640 conversation failed in `attempt3`. All original
raw evidence and charges remain; none of those attempts may activate. The selected
fresh root is `/root/cara-model-trial-20261025/attempt4`. It preserves the exact
original effective DeepSeek conversation profile, with no token-control exception.

Qualify all 21 cases for the seven changed active profiles by exact input/profile/
source/raw/ledger carry and new isolated functional replay. This makes no model
calls and does not promote failed conversation answers:

```sh
python3 "$TOOLS/qualify_partial.py" --root "$RUN" --prior-root "$PRIOR" --prior-run "$PRIOR/qualification-candidate" --output "$RUN/qualification-partial"
```

The passed proof must enumerate all 21 case IDs, all seven changed active profiles
and the SHA of the unchanged effective conversation profile. Failed conversation
cases are excluded from this explicitly reduced scope, not counted as passes.

After acceptance, install the fixed root-bound observer:

```sh
python3 "$TOOLS/install_monitor.py" install --root "$RUN" --dry-run
python3 "$TOOLS/install_monitor.py" install --root "$RUN"
systemctl start cara-model-trial-20261025-observer.service
```

Read its receipt and first report to verify actual sandbox access. Twelve absolute
calendar entries run at +6 through +72 hours, rounded upward to the next whole
second. The final wrapper stops only this timer even if observation fails.
Existing application/worker/fleet/Whisper units remain unchanged.
