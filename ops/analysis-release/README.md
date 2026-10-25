# Trial source epoch contracts

This standard-library helper appends one third source epoch to an independently
authenticated two-source trial receipt. It preserves the original 72-hour end,
prior first exposure, transition gaps and recorded pauses. Calendar dates remain
a suffix of the original six-hour absolute schedule, with fractional final seconds
rounded upward.

The helper is pure: it does not read files, grant tools, install observers, change
configuration, send messages or perform model calls. Authentication, release
checks, protected backups, actual namespace readiness, source-specific model-only
reversion and observer installation belong to the separately qualified private
operator. Calling this helper alone does not authorize or accept a deployment.

Run the synthetic contracts from this directory with:

```sh
python3 -B -m unittest discover -p 'test_*.py'
```
