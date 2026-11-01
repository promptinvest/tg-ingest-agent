"""Stable legacy task JSON encoding, shared by the agent, worker and canary.

These existing task envelopes permit JSON's non-finite float spellings. Fleet
protocols have a stricter, separate contract; do not use this encoder for them.
"""
import json


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
