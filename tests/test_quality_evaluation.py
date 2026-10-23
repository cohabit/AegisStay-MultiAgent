"""Quality-set contracts that sit beside the fixed H01-H20 compatibility gate."""

from __future__ import annotations

import unittest

from harness.eval_runner import load_dataset, load_extended_dataset, run_evaluation


class StratifiedQualityEvaluationTests(unittest.TestCase):
    def test_extended_dataset_is_stratified_and_does_not_replace_baseline(self):
        baseline = load_dataset()
        extended = load_extended_dataset()
        self.assertEqual(len(baseline), 20)
        self.assertGreaterEqual(len(extended), 40)
        self.assertEqual({case["id"] for case in baseline} & {case["id"] for case in extended}, set())
        groups = {}
        for case in extended:
            groups.setdefault(case["group"], []).append(case)
        self.assertGreaterEqual(len(groups), 5)
        self.assertTrue(all(len(cases) >= 8 for cases in groups.values()))

    def test_extended_evaluation_reports_per_group_metrics_and_passes(self):
        report = run_evaluation(use_cloud_model=False, include_extended=True)
        self.assertEqual(report["baseline_total"], 20)
        self.assertEqual(report["extended_total"], 40)
        self.assertEqual(report["total"], 60)
        self.assertEqual(report["passed"], 60)
        self.assertEqual(report["extended_passed"], 40)
        self.assertEqual(report["intent_accuracy"], 1.0)
        self.assertEqual(report["ticket_precision"], 1.0)
        self.assertEqual(report["ticket_recall"], 1.0)
        quality_groups = [group for group in report["groups"] if group["group"] != "baseline"]
        self.assertEqual(len(quality_groups), 5)
        self.assertTrue(all(group["passed"] == group["total"] for group in quality_groups))
        self.assertEqual(len(report["pass_rate_ci95"]), 2)


if __name__ == "__main__":
    unittest.main()

