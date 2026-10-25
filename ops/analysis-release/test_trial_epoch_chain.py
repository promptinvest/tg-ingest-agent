"""Synthetic public chronology contracts; no private fixture or host dependency."""
import copy
import unittest

import trial_epoch_chain as chain


def native(letter):
    return {"sha256": letter * 64, "deployment_id": letter * 24, "source_revision": letter * 40, "build_version": letter * 12}


def prior():
    return {"schema": "cara.model-trial-continuation/v1", "trial_start": "2026-01-01T00:00:00.500000+00:00", "trial_end": "2026-01-04T00:00:00.500000+00:00", "old_runtime": native("a"), "old_segment_until": "2026-01-01T01:00:00+00:00", "new_segment_from": "2026-01-01T01:00:02+00:00", "new_runtime": {"identity": native("b")}, "recorded_paused_intervals": [{"from_inclusive": "2026-01-01T01:10:00+00:00", "until_exclusive": "2026-01-01T01:20:00+00:00", "reason": "forward_paused_after_runtime_verifier_path_failure"}]}


class PublicEpochChain(unittest.TestCase):
    def test_append_preserves_every_prior_timestamp_and_pause_without_mutation(self):
        before = prior(); original = copy.deepcopy(before)
        result = chain.append_third_epoch(before, "2026-01-02T00:00:00+00:00", native("c"), "2026-01-02T00:00:02+00:00")
        self.assertEqual(before, original); self.assertEqual(len(result["epochs"]), 3)
        self.assertEqual(result["epochs"][1]["from_inclusive"], before["new_segment_from"])
        self.assertEqual(result["epochs"][1]["recorded_paused_intervals"], before["recorded_paused_intervals"])
        result["epochs"][1]["recorded_paused_intervals"].clear()
        self.assertEqual(before, original)

    def test_reused_source_or_deployment_is_refused(self):
        for changed in (native("b"), {**native("c"), "deployment_id": "a" * 24}):
            with self.assertRaises(ValueError): chain.append_third_epoch(prior(), "2026-01-02T00:00:00+00:00", changed, "2026-01-02T00:00:02+00:00")

    def test_overlap_deadline_extension_and_reversed_transition_are_refused(self):
        for boundary, started in (("2026-01-01T00:30:00+00:00", "2026-01-02T00:00:00+00:00"), ("2026-01-02T00:00:00+00:00", "2026-01-01T23:59:59+00:00"), ("2026-01-04T00:00:00.500000+00:00", "2026-01-04T00:00:02+00:00")):
            with self.assertRaises(ValueError): chain.append_third_epoch(prior(), boundary, native("c"), started)
        changed = prior(); changed["trial_end"] = "2026-01-05T00:00:00.500000+00:00"
        with self.assertRaises(ValueError): chain.append_third_epoch(changed, "2026-01-02T00:00:00+00:00", native("c"), "2026-01-02T00:00:02+00:00")

    def test_out_of_epoch_unknown_or_overlapping_pause_is_refused(self):
        for change in (lambda p: p["recorded_paused_intervals"][0].update(reason="unreviewed"), lambda p: p["recorded_paused_intervals"][0].update(until_exclusive="2026-01-03T00:00:00+00:00"), lambda p: p["recorded_paused_intervals"].append(dict(p["recorded_paused_intervals"][0]))):
            value = prior(); change(value)
            with self.assertRaises(ValueError): chain.append_third_epoch(value, "2026-01-02T00:00:00+00:00", native("c"), "2026-01-02T00:00:02+00:00")

    def test_remaining_dates_are_original_suffix_with_terminal_second_ceiled(self):
        value = prior(); dates = chain.remaining_original_dates(value["trial_start"], value["trial_end"], "2026-01-02T00:00:00+00:00")
        self.assertEqual(dates[0], "2026-01-02 00:00:01 UTC")
        self.assertEqual(dates[-1], "2026-01-04 00:00:01 UTC")
        self.assertEqual(len(dates), 9)

    def test_no_calendar_installation_after_original_deadline(self):
        value = prior()
        with self.assertRaises(ValueError): chain.remaining_original_dates(value["trial_start"], value["trial_end"], value["trial_end"])


if __name__ == "__main__":
    unittest.main()
