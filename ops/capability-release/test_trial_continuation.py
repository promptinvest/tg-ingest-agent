import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import trial_continuation as t


def fixture():
    identity = {"sha256": "a" * 64, "deployment_id": "b" * 24,
                "source_revision": t.OLD_SOURCE, "build_version": "c" * 12}
    runtime = {name: "d" * 64 for name in ("agent.py", "llm.py", "common.py", "verify-cara-runtime.sh")}
    runtime.update({f"module_{i}.py": "e" * 64 for i in range(87)})
    protected = {f"/etc/example/pin-{i}": {"sha256": "f" * 64, "uid": 0, "gid": 0, "mode": 0o600} for i in range(77)}
    protected[t.ENV] = {"sha256": t.TRIAL_ENV_SHA256, "uid": 0, "gid": 0, "mode": 0o600}
    start = "2026-10-25T13:36:17.924552+00:00"
    accepted = {"schema": "cara.model-trial-acceptance/v1", "status": "accepted", "at": start,
                "observation_until": "2026-10-28T13:36:17.924552+00:00", "identity": identity,
                "before_env_sha256": t.OLD_ENV_SHA256, "candidate_env_sha256": t.TRIAL_ENV_SHA256,
                "budget_limits_usd": [2.0, 12.0], "code_installed": False, "database_restored": False,
                "operator_messages_sent": 0, "runtime_hashes": runtime, "stopped_tables_preserved": 56}
    prepared = {"schema": "cara.model-trial-prepared/v1", "identity": identity,
                "before_env_sha256": t.OLD_ENV_SHA256, "candidate_env_sha256": t.TRIAL_ENV_SHA256,
                "budget_limits_usd": [2.0, 12.0], "runtime_hashes": runtime,
                "tables": {f"table_{i}": {} for i in range(56)}, "database_path": t.DATABASE}
    boundary = {"schema": "cara.model-trial-runtime-boundary/v1", "at": "2026-10-25T15:00:00+00:00",
                "trial_acceptance_sha256": t.TRIAL_ACCEPTANCE_SHA256, "old_identity": identity,
                "old_runtime_sha256": t.digest(runtime), "main_stopped": True,
                "snapshot_sha256": "a" * 64, "original_timer": t.ORIGINAL_TIMER}
    new = {"schema": "cara.capability-trial-runtime/v1", "status": "accepted",
           "at": "2026-10-25T15:10:00+00:00", "segment_started_at": "2026-10-25T15:05:00+00:00",
           "release_acceptance_sha256": "1" * 64, "release_authorization_sha256": "2" * 64,
           "identity": {"sha256": "3" * 64, "source_revision": "4" * 40,
                        "deployment_id": "5" * 24, "build_version": "6" * 12},
           "runtime_hashes": runtime, "protected": protected,
           "model_key_sha256s": {key: "7" * 64 for key in t.MODEL_KEYS}}
    return accepted, prepared, boundary, new


def plan():
    values = fixture()
    raw = [t.canonical(value) for value in values]
    return t.make_continuation(*raw, expected_acceptance_sha256=t.sha(raw[0]))


class ContinuationContracts(unittest.TestCase):
    def test_disjoint_runtime_windows_and_original_end(self):
        value = plan()
        self.assertEqual(t.segment_window(value, "2026-10-26T00:00:00+00:00"),
                         ("2026-10-25T15:05:00+00:00", "2026-10-26T00:00:00+00:00", False))
        self.assertEqual(value["trial_end"], "2026-10-28T13:36:17.924552+00:00")
        self.assertLess(t.utc(value["old_segment_until"]), t.utc(value["new_segment_from"]))

    def test_end_clamps_late_report(self):
        value = plan()
        self.assertEqual(t.segment_window(value, "2026-10-29T00:00:00+00:00")[1:], (value["trial_end"], True))

    def test_fractional_final_calendar_is_ceiled(self):
        dates = t.remaining_dates(plan(), "2026-10-25T15:10:00+00:00")
        self.assertEqual(len(dates), 12)
        self.assertEqual(dates[-1], "2026-10-28 13:36:18 UTC")

    def test_remaining_dates_do_not_restart_72h(self):
        dates = t.remaining_dates(plan(), "2026-10-27T15:10:00+00:00")
        self.assertEqual(len(dates), 4)
        self.assertEqual(dates[0], "2026-10-27 19:36:18 UTC")

    def test_install_after_deadline_rejected(self):
        with self.assertRaises(ValueError):
            t.remaining_dates(plan(), "2026-10-29T00:00:00+00:00")

    def test_extended_deadline_rejected(self):
        value = plan(); value["trial_end"] = "2026-10-29T13:36:17.924552+00:00"
        with self.assertRaises(ValueError):
            t.validate_continuation(value)

    def test_arbitrary_units_rejected(self):
        for field in ("original_timer", "service", "timer"):
            value = plan(); value[field] = "tg-ingest-agent.service"
            with self.assertRaises(ValueError):
                t.validate_continuation(value)

    def test_original_hash_is_not_selected_by_production_default(self):
        values = fixture(); raw = [t.canonical(value) for value in values]
        with self.assertRaisesRegex(ValueError, "original trial acceptance changed"):
            t.make_continuation(*raw)

    def test_unstopped_boundary_rejected(self):
        values = fixture(); values[2]["main_stopped"] = False
        raw = [t.canonical(value) for value in values]
        with self.assertRaises(ValueError):
            t.make_continuation(*raw, expected_acceptance_sha256=t.sha(raw[0]))

    def test_reuse_old_runtime_rejected(self):
        values = fixture(); values[3]["identity"] = values[0]["identity"]
        raw = [t.canonical(value) for value in values]
        with self.assertRaises(ValueError):
            t.make_continuation(*raw, expected_acceptance_sha256=t.sha(raw[0]))

    def test_new_runtime_cannot_change_model_config(self):
        values = fixture(); values[3]["protected"][t.ENV]["sha256"] = "0" * 64
        raw = [t.canonical(value) for value in values]
        with self.assertRaises(ValueError):
            t.make_continuation(*raw, expected_acceptance_sha256=t.sha(raw[0]))

    def test_all_runtime_counterparts_required(self):
        value = fixture()[0]["runtime_hashes"]
        value.pop("verify-cara-runtime.sh")
        with self.assertRaises(ValueError):
            t.runtime_map(value)

    def test_duplicate_receipt_keys_rejected(self):
        with self.assertRaises(ValueError):
            t.decode(b'{"at":1,"at":2}')

    def test_frozen_revert_preserves_unrelated_bytes(self):
        old = b'DO_CHAT_MODEL=old\nROUTER_MODEL=old\nOTHER=original\n'
        candidate = b'DO_CHAT_MODEL=new\nROUTER_MODEL=new\nOTHER=original\n'
        current = b'# keep this comment\r\nDO_CHAT_MODEL=new\r\nROUTER_MODEL=new\r\nOTHER=later\r\nNEW_KEY="a b"\r\n'
        result = t.merge_revert(current, old, candidate)
        self.assertIn(b'OTHER=later\r\nNEW_KEY="a b"\r\n', result)
        self.assertTrue(result.startswith(b'# keep this comment\r\n'))
        self.assertEqual(t.load_reference("trial_config.py").parse_env_bytes(result)["DO_CHAT_MODEL"], "old")

    def test_conflicting_later_model_change_rejected(self):
        with self.assertRaises(ValueError):
            t.merge_revert(b'DO_CHAT_MODEL=third\nOTHER=later\n', b'DO_CHAT_MODEL=old\nOTHER=x\n', b'DO_CHAT_MODEL=new\nOTHER=x\n')

    def test_reference_byte_binding(self):
        self.assertEqual(t.load_reference("trial_config.py").MODEL_KEYS, t.MODEL_KEYS)
        with self.assertRaises(ValueError):
            t.load_reference("../trial_config.py")

    def preflight(self):
        value = plan(); new = value["new_runtime"]
        return value, {"schema": "cara.model-trial-new-runtime-revert-preflight/v1",
                       "at": "2026-10-27T15:00:00+00:00", "until": "2026-10-27T15:35:00+00:00",
                       "continuation_sha256": t.digest(value), "identity": new["identity"],
                       "runtime_hashes": new["runtime_hashes"], "protected": copy.deepcopy(new["protected"]),
                       "main_process": {"pid": 123, "start_ticks": 456, "uid": 997, "active_enter": "2026-10-25T15:07:00+00:00"},
                       "peers": {}, "environment_sha256": t.TRIAL_ENV_SHA256,
                       "model_key_sha256s": new["model_key_sha256s"], "table_schema_sha256": "a" * 64,
                       "tables": 56, "idle": True}

    def test_new_runtime_revert_accepts_fresh_unrelated_env_change(self):
        value, fresh = self.preflight()
        fresh["protected"][t.ENV]["sha256"] = fresh["environment_sha256"] = "b" * 64
        t.verify_revert_preflight(fresh, value, now="2026-10-27T15:10:00+00:00")

    def test_revert_rejects_unrelated_protected_drift(self):
        value, fresh = self.preflight(); fresh["protected"]["/etc/example/pin-0"]["sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            t.verify_revert_preflight(fresh, value, now="2026-10-27T15:10:00+00:00")

    def test_revert_rejects_changed_runtime_pid_root_or_expired_window(self):
        for mutate in (lambda v: v["runtime_hashes"].update({"llm.py": "b" * 64}),
                       lambda v: v["main_process"].update(uid=0),
                       lambda v: v.update(until="2026-10-27T16:00:00+00:00"),
                       lambda v: v.update(idle=False)):
            value, fresh = self.preflight(); fresh = copy.deepcopy(fresh); mutate(fresh)
            with self.assertRaises(ValueError):
                t.verify_revert_preflight(fresh, value, now="2026-10-27T15:10:00+00:00")


class RecordedPauseContracts(unittest.TestCase):
    def test_recorded_pauses_round_trip_without_resetting_epoch_or_trial_end(self):
        value = plan()
        first, end = value["new_segment_from"], value["trial_end"]
        pauses = [{"from_inclusive": "2026-10-25T15:06:00+00:00",
                   "until_exclusive": "2026-10-25T15:07:00+00:00",
                   "reason": "forward_paused_after_runtime_verifier_path_failure"},
                  {"from_inclusive": "2026-10-25T15:08:00+00:00",
                   "until_exclusive": "2026-10-25T15:09:00+00:00",
                   "reason": "forward_paused_after_runtime_verifier_path_failure"}]
        value["recorded_paused_intervals"] = copy.deepcopy(pauses)
        original = copy.deepcopy(value)
        accepted = t.decode(t.canonical(value))
        t.validate_continuation(accepted)
        self.assertEqual(accepted["recorded_paused_intervals"], pauses)
        self.assertEqual(accepted["new_segment_from"], first)
        self.assertEqual(accepted["trial_end"], end)
        self.assertEqual(t.segment_window(accepted, "2026-10-29T00:00:00+00:00"),
                         (first, end, True))
        self.assertEqual(t.remaining_dates(accepted, "2026-10-25T15:10:00+00:00")[-1],
                         "2026-10-28 13:36:18 UTC")
        self.assertEqual(value, original)

    def test_malformed_out_of_epoch_or_overlapping_recorded_pauses_are_rejected(self):
        valid = {"from_inclusive": "2026-10-25T15:06:00+00:00",
                 "until_exclusive": "2026-10-25T15:07:00+00:00",
                 "reason": "forward_paused_after_runtime_verifier_path_failure"}
        invalid = [None, {}, [valid] * 5,
                   [{"from_inclusive": valid["from_inclusive"], "reason": valid["reason"]}],
                   [{**valid, "extra": "unbound"}],
                   [{**valid, "reason": "unbound"}],
                   [{**valid, "from_inclusive": "2026-10-25T15:04:00+00:00"}],
                   [{**valid, "until_exclusive": valid["from_inclusive"]}],
                   [{**valid, "until_exclusive": "2026-10-25T15:05:30+00:00"}],
                   [{**valid, "until_exclusive": "2026-10-28T13:36:17.924552+00:00"}],
                   [{**valid, "until_exclusive": "2026-10-29T00:00:00+00:00"}],
                   [{**valid, "from_inclusive": "2026-10-25T16:06:00+01:00"}],
                   [valid, {**valid, "from_inclusive": "2026-10-25T15:06:30+00:00",
                            "until_exclusive": "2026-10-25T15:08:00+00:00"}]]
        for pauses in invalid:
            with self.subTest(pauses=pauses):
                value = plan()
                value["recorded_paused_intervals"] = copy.deepcopy(pauses)
                before = copy.deepcopy(value)
                with self.assertRaises(ValueError):
                    t.validate_continuation(value)
                self.assertEqual(value, before)


if __name__ == "__main__":
    unittest.main()
