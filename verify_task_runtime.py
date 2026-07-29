#!/usr/bin/env python3
"""Unprivileged end-to-end canary for Cara's one-way worker spool."""
import hashlib
import json
import time
from types import SimpleNamespace

import tool_broker
import worker_client


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def main():
    tool_broker.assert_registry()
    search = tool_broker.get_spec("web.search")
    if (search is None or search.risk != "network_read"
            or search.writes_state or not search.external_network):
        raise RuntimeError("web.search broker contract is not active")
    value = {"text": "cara-worker-live-canary"}
    digest = hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
    cfg = SimpleNamespace(task_worker_spool="/var/lib/cara-worker/spool")
    binding = worker_client.submit(
        cfg, task_id=2147483001, step_id=2147483001, tool="worker.echo",
        input_value=value, input_hash=digest,
        policy_version=tool_broker.POLICY_VERSION,
        implementation_version=tool_broker.IMPLEMENTATION_VERSION,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = worker_client.poll(
                cfg, job_id=binding["job_id"], nonce=binding["nonce"],
                task_id=2147483001, step_id=2147483001, tool="worker.echo",
                input_hash=digest,
                policy_version=tool_broker.POLICY_VERSION,
                implementation_version=tool_broker.IMPLEMENTATION_VERSION,
            )
            if result is not None:
                if result != {
                    "schema": "worker.echo/v1",
                    "echo": "cara-worker-live-canary",
                }:
                    raise RuntimeError("worker canary returned wrong content")
                print("worker-spool-canary: ok")
                return 0
            time.sleep(0.1)
        raise RuntimeError("worker canary timed out")
    finally:
        worker_client.acknowledge(cfg, binding["job_id"])


if __name__ == "__main__":
    raise SystemExit(main())
