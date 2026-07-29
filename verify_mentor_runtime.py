#!/usr/bin/env python3
"""One paid-call-free, full-suite canary for the live networkless Mentor runner."""
import difflib
import hashlib
import time
from pathlib import Path
from types import SimpleNamespace

import mentor_client
import mentor_protocol as protocol


SOURCE = Path("/opt/cara-mentor-source")


def _diff(name, before, after):
    return (
        f"diff --git a/{name} b/{name}\n"
        + "".join(difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
        ))
    )


def main():
    router = (SOURCE / "router.py").read_text(encoding="utf-8")
    tests = (SOURCE / "test_mentor_candidates.py").read_text(encoding="utf-8")
    router_after = router + "\n# Mentor runner deployment canary.\n"
    tests_after = tests + """

class MentorDeploymentCanaryTests(unittest.TestCase):
    def test_networkless_candidate_suite_can_execute(self):
        self.assertEqual(2 + 2, 4)
"""
    targets = ["router.py", "test_mentor_candidates.py"]
    patch = _diff(targets[0], router, router_after) + _diff(
        targets[1], tests, tests_after)
    patch = protocol.validate_patch(patch, targets)
    cfg = SimpleNamespace(
        mentor_runner_spool=Path("/var/lib/cara-mentor-runner/spool"))
    source_build = (SOURCE / "VERSION").read_text(encoding="utf-8").strip()
    source_hash = (SOURCE / "SOURCE_HASH").read_text(encoding="utf-8").strip()
    cycle_uid = "deploy-canary-" + protocol.digest(patch)[:12]
    change_hash = hashlib.sha256(b"Mentor runner deploy canary").hexdigest()
    binding = mentor_client.submit_runner(
        cfg,
        cycle_uid=cycle_uid,
        patch=patch,
        patch_hash=protocol.digest(patch),
        target_files=targets,
        source_build=source_build,
        source_hash=source_hash,
        proposed_change_hash=change_hash,
    )
    deadline = time.monotonic() + 720
    result = None
    try:
        while time.monotonic() < deadline:
            result = mentor_client.poll_runner(
                cfg,
                job_id=binding["job_id"],
                nonce=binding["nonce"],
                cycle_uid=cycle_uid,
                patch_hash=protocol.digest(patch),
                source_build=source_build,
                source_hash=source_hash,
                proposed_change_hash=change_hash,
            )
            if result is not None:
                break
            time.sleep(0.5)
        if result is None:
            raise RuntimeError("Mentor runner canary timed out")
        if (result["status"] != "passed"
                or "Ran " not in result["tests_summary"]
                or "OK" not in result["tests_summary"]
                or not result["branch"].startswith("mentor/deploy-canary-")):
            raise RuntimeError(
                "Mentor runner canary failed: "
                + str(result["tests_summary"])[:300])
        print("mentor-runner-canary: " + result["tests_summary"])
        return 0
    finally:
        mentor_client.acknowledge(
            cfg.mentor_runner_spool, binding["job_id"])


if __name__ == "__main__":
    raise SystemExit(main())
