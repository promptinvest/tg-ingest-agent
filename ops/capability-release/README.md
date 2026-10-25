# Capability release and ongoing model trial

The public helpers here are pure, inert receipt/continuation contracts. Importing
them or running their tests performs no host transition, model call or message.
They do not constitute a deployment authorization or acceptance.

ADR-0035 changes application context/evidence handling while retaining the
October25 model configuration. The original model trial remains immutable and
its original revert correctly refuses a different native runtime. A separately
qualified private release package must bind the exact old and new runtimes,
stopped backup/schema/data parity, loaded models, protected files and fresh
acceptance before installing a companion observer or issuing a new revert.

The continuation retains the original October28 13:36:17.924552UTC deadline,
separates old/new source windows and records downtime explicitly. Only the fixed
old observer timer stops at the quiescent handoff; its files and receipts remain.
The companion makes no paid calls or messages and stops at the original end.
Any model reversion restores only the four originally changed model keys after
fresh checks/backups, retaining later unrelated configuration and all data.
Never replay a consumed release/trial controller or restore an old database.

Run the pure tests outside OneDrive, preferably in the isolated Linux gate:

```sh
cd ops/capability-release
python3 -B -m unittest discover -p 'test_*.py'
```

`reference/` retains exact-hash copies of the existing model environment parser
and metadata observer for compatibility; the continuation verifies these hashes
before import. These copies are not replacement live files.
