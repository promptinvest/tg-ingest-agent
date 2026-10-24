"""Requested-answer relevance and full-evidence preservation across presentation."""
import copy
import unittest
from unittest import mock

import answer_policy
import fleet_answers
import fleet_protocol as protocol
import fleet_report


def report(focus=None):
    inventory = dict(version=protocol.VERSION,
                     hosts={identity: dict(label=label, role="Synthetic", aliases=[], restart_services=[])
                            for identity, label in (("host-a", "Example A"), ("host-b", "Example B"))},
                     projects={"project-alpha": dict(label="Project Alpha", hosts=["host-a"],
                                                       recorded_at="2026-10-20", notes="Recorded placement only.")})
    plan = dict(hosts=list(inventory["hosts"]), facets=["resources", "services", "projects"],
                focus=focus or ["overview"])
    response = dict(version=protocol.VERSION, observed_at=protocol.now(), hosts={})
    for identity in inventory["hosts"]:
        response["hosts"][identity] = dict(host=identity, observed_at=protocol.now(),
            resources=dict(cpu_percent=12, cpu_sample_seconds=.2, memory_used_percent=45, disk_used_percent=30,
                           top_memory=[dict(name="example-app", pid=4321, rss_bytes=100*1048576, cpu_one_core_percent=2)]),
            services={"example.service": dict(LoadState="loaded", ActiveState="active", SubState="running")}, projects={})
    return fleet_report.build(response, inventory, plan), plan


class RequestedAnswers(unittest.TestCase):
    def test_cpu_answer_has_requested_measurement_and_window_without_unrelated_diagnostics(self):
        evidence, plan = report(["cpu"])
        before = copy.deepcopy(evidence)
        answer = "\n".join(fleet_answers.render(evidence, None, plan))
        for value in ("Example A", "Example B", "CPU 12% (0.2s)"):
            self.assertIn(value, answer)
        self.assertNotIn("RAM", answer)
        self.assertNotIn("example.service", answer)
        self.assertNotIn("Project Alpha", answer)
        self.assertEqual(evidence, before)
        self.assertGreater(len(evidence["observations"]), 10)

    def test_requested_unknown_and_missing_host_remain_visible(self):
        evidence, plan = report(["memory"])
        for key, observation in evidence["observations"].items():
            if observation["host"] == "host-b" and observation["field"] == "resources.memory_used_percent":
                observation.update(status="unavailable", value=None)
                evidence["findings"][key].update(status="unavailable", en="RAM unknown")
        answer = "\n".join(fleet_answers.render(evidence, None, plan))
        self.assertIn("Example B: RAM unknown", answer)
        self.assertIn("100.0 MiB RSS", answer)
        self.assertNotIn("CPU 12%", answer)

    def test_explicit_detail_overrides_confirmed_short_style_and_preserves_coverage(self):
        evidence, plan = report()
        with mock.patch.object(answer_policy.persona, "boss_preference_hint", return_value="Short answers"):
            concise = "\n".join(fleet_answers.render(evidence, None, plan, conn=object()))
            detailed = "\n".join(fleet_answers.render(evidence, None, dict(plan, detail="detailed"), conn=object()))
        self.assertLess(len(concise), len(detailed))
        for host in evidence["hosts"]:
            self.assertIn(evidence["coverage"][host]["label"], detailed)
        self.assertIn("These are configured observations", detailed)
        self.assertNotIn("Highlights:", detailed)

    def test_table_retains_all_requested_hosts_and_specific_capability_gaps(self):
        evidence, plan = report(["progress"])
        answer = "\n".join(fleet_answers.render(evidence, None, dict(plan, presentation="table")))
        self.assertIn("| Server | Answer |", answer)
        self.assertIn("Example A", answer)
        self.assertIn("Example B", answer)
        self.assertIn("development progress is unknown", answer)
        self.assertNotIn("CPU", answer)

    def test_unsupported_backup_conclusion_cannot_enter_through_selection(self):
        evidence, plan = report(["backups"])
        with self.assertRaises(ValueError):
            fleet_answers.render(evidence, dict(highlights=[], hypotheses=[], next_checks=[],
                                                answer="Backups are healthy because CPU is low"), plan)
        answer = "\n".join(fleet_answers.render(evidence, None, plan))
        self.assertIn("fresh backup results are not connected", answer)
        self.assertNotIn("CPU", answer)
        self.assertNotIn("Backups are healthy", answer)

    def test_highlights_do_not_repeat_a_finding_and_irrelevant_hypotheses_are_not_displayed(self):
        evidence, plan = report()
        key = evidence["coverage"]["host-a"]["findings"][0]
        decision = dict(highlights=[key], hypotheses=[], next_checks=[])
        answer = "\n".join(fleet_answers.render(evidence, decision, plan))
        self.assertEqual(answer.count("Example A:"), 1)
        self.assertNotIn("Highlights:", answer)

    def test_shared_guidance_does_not_promote_style_into_authority(self):
        with mock.patch.object(answer_policy.persona, "boss_preference_hint", return_value="Short answers"):
            value = answer_policy.hint(object())
        for instruction in ("actual question first", "requested format/detail", "Short answers",
                            "grant no facts, tools or permission", "meaningful names"):
            self.assertIn(instruction, value)
        with mock.patch.object(answer_policy.persona, "boss_preference_hint", return_value="Detailed answers"):
            self.assertEqual(answer_policy.default_detail(object()), "detailed")
        with mock.patch.object(answer_policy.persona, "boss_preference_hint", return_value="Short answers; detailed answers"):
            self.assertEqual(answer_policy.default_detail(object()), "concise")


    def test_explicit_detailed_table_keeps_every_coverage_finding_and_dates(self):
        evidence, plan = report(["memory"])
        before = copy.deepcopy(evidence)
        pages = fleet_answers.render(evidence, None, dict(plan, presentation="table", detail="detailed"))
        answer = "\n".join(pages)
        self.assertIn("| Server | Evidence |", answer)
        for host in evidence["hosts"]:
            label = evidence["coverage"][host]["label"]
            self.assertIn("| " + label + " |", answer)
            for key in evidence["coverage"][host]["findings"]:
                self.assertIn(evidence["findings"][key]["en"].replace("|", "/").replace("\n", " "), answer)
        self.assertIn(fleet_report.stamp(evidence["observed_at"]), answer)
        self.assertIn("These are configured observations", answer)
        self.assertEqual(evidence, before)
        self.assertTrue(all(len(page) <= 3700 for page in pages))

    def test_detailed_table_does_not_change_original_detailed_prose(self):
        evidence, plan = report()
        detailed = fleet_answers.render(evidence, None, dict(plan, detail="detailed"))
        self.assertEqual(detailed, fleet_report.render(evidence, dict(highlights=[], hypotheses=[], next_checks=[])))
        self.assertNotIn("| Server | Evidence |", "\n".join(detailed))

if __name__ == "__main__":
    unittest.main()