"""Adversarial evidence-contract checks; never recursively run release acceptance."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import verify_release as verification
from scripts.verify_blackboard import (collect_scenarios, scenario_assertions,
                                       validate_blackboard_report)


class ReleaseVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def unittest_report(self, count=1):
        return verification.envelope(
            "unittest", "current-run", status="PASS", tests_run=count, collected=count,
            test_ids=[f"tests.test_app.MultiAgentFlowTests.test_example_{i}" for i in range(count)],
            successful=True, failures=0, errors=0, skipped=0, expected_failures=0,
            unexpected_successes=0,
        )

    def harness_report(self):
        return verification.envelope(
            "harness", "current-run", status="PASS", total=20, passed=20,
            rows=[{"id": f"H{i:02d}", "status": "PASS", "model_mode": "规则降级"}
                  for i in range(1, 21)],
            intent_accuracy=1.0, slot_filling_rate=1.0, ticket_precision=1.0,
            ticket_recall=1.0, eval_mode="⚡ 离线规则快速回归",
            model_name="确定性规则引擎 (离线保底)",
        )

    def traceability_mapping(self, method=None):
        name = method or "tests.test_release_verification.ReleaseVerificationTests.test_existing_output_directory_is_rejected"
        return {"schema_version": 1, "requirements": [
            {"id": f"REQ-{i:02d}", "acceptance_id": f"AC-{i:02d}", "status": "partial",
             "tests": [name], "experiment_ids": [f"EXP-{i:02d}"], "evidence": ["full-tests.json"]}
            for i in range(1, 17)
        ], "future_requirements": [{"id": "FUT-01", "status": "planned"}]}

    def test_unittest_exit_zero_cannot_mask_failed_result(self):
        # This reproduces a harness/adapter returning 0 while recording failure.
        report = self.unittest_report()
        report.update(successful=False, failures=1)
        completed = subprocess.CompletedProcess(["python", "-m", "unittest"], 0)
        self.assertEqual(completed.returncode, 0)
        with self.assertRaises(verification.VerificationError):
            verification.validate_unittest_report(report, minimum=1)

    def test_unittest_zero_tests_is_failure(self):
        with self.assertRaises(verification.VerificationError):
            verification.validate_unittest_report(self.unittest_report(0), minimum=0)

    def test_unittest_skips_are_failure(self):
        report = self.unittest_report()
        report["skipped"] = 1
        with self.assertRaisesRegex(verification.VerificationError, "skipped"):
            verification.validate_unittest_report(report, minimum=1)

    def test_unittest_expected_failure_is_not_a_full_pass(self):
        report = self.unittest_report()
        report["expected_failures"] = 1
        with self.assertRaisesRegex(verification.VerificationError, "expected_failures"):
            verification.validate_unittest_report(report, minimum=1)

    def test_unittest_forged_count_cannot_replace_executed_identities(self):
        report = self.unittest_report(2)
        report["test_ids"] = report["test_ids"][:1]
        with self.assertRaisesRegex(verification.VerificationError, "identities"):
            verification.validate_unittest_report(report, minimum=2)

    def test_core_suite_rejects_tests_from_another_module(self):
        report = self.unittest_report()
        report["test_ids"] = ["tests.test_api.ApiTests.test_fake"]
        with self.assertRaisesRegex(verification.VerificationError, "outside tests/test_app.py"):
            verification.validate_unittest_report(report, minimum=1, core=True)

    def test_same_test_count_cannot_replace_a_committed_baseline_identity(self):
        ids = [f"tests.test_app.MultiAgentFlowTests.test_baseline_{i}" for i in range(127)]
        contract = {"schema_version": 1, "baseline_commit": "a" * 40,
                    "expected_count": 127, "test_ids": ids}
        executed = set(ids)
        executed.remove(ids[-1])
        executed.add("tests.test_release_verification.ReleaseVerificationTests.test_replacement")
        self.assertEqual(len(executed), 127)
        with self.assertRaisesRegex(verification.VerificationError, "baseline tests did not execute"):
            verification.validate_baseline_test_ids(contract, executed)

    def test_harness_failed_row_is_failure(self):
        report = self.harness_report()
        report["rows"][0]["status"] = "FAIL"
        with self.assertRaisesRegex(verification.VerificationError, "failed"):
            verification.validate_harness_report(report)

    def test_harness_missing_baseline_case_is_failure(self):
        report = self.harness_report()
        # Still 20 PASS rows and 100% metrics, but H20 has been silently replaced.
        report["rows"][-1]["id"] = "H99"
        with self.assertRaisesRegex(verification.VerificationError, "H01..H20"):
            verification.validate_harness_report(report)

    def test_harness_duplicate_case_is_failure(self):
        report = self.harness_report()
        report["rows"][-1]["id"] = "H01"
        with self.assertRaises(verification.VerificationError):
            verification.validate_harness_report(report)

    def test_harness_extra_passing_case_is_failure(self):
        for extra in ("H21", "H100"):
            with self.subTest(extra=extra):
                report = self.harness_report()
                report["rows"].append({"id": extra, "status": "PASS", "model_mode": "规则降级"})
                report.update(total=21, passed=21)
                with self.assertRaisesRegex(verification.VerificationError, "20"):
                    verification.validate_harness_report(report)

    def test_harness_each_metric_is_required_and_exact(self):
        for metric in verification.METRICS:
            for value in (None, 0.999, True, float("nan")):
                with self.subTest(metric=metric, value=value):
                    report = self.harness_report()
                    report[metric] = value
                    with self.assertRaises(verification.VerificationError):
                        verification.validate_harness_report(report)

    def test_harness_cloud_mode_is_not_offline_evidence(self):
        report = self.harness_report()
        report["rows"][0]["model_mode"] = "云端模型"
        with self.assertRaisesRegex(verification.VerificationError, "offline"):
            verification.validate_harness_report(report)

    def test_harness_cli_exit_zero_does_not_mask_fail_rows(self):
        log = self.root / "harness.log"
        log.write_text("\n".join(f"H{i:02d} {'FAIL' if i == 7 else 'PASS'} intent room dept" for i in range(1, 21)), encoding="utf-8")
        with self.assertRaisesRegex(verification.VerificationError, "exit 0 is insufficient"):
            verification.validate_harness_cli(log)

    def test_harness_cli_missing_case_does_not_pass_on_pass_substrings(self):
        log = self.root / "harness.log"
        log.write_text("PASS PASS PASS\n" + "\n".join(f"H{i:02d} PASS intent room dept" for i in range(1, 20)), encoding="utf-8")
        with self.assertRaises(verification.VerificationError):
            verification.validate_harness_cli(log)

    def test_harness_cli_extra_passing_case_is_failure(self):
        log = self.root / "harness.log"
        for extra in ("H21", "H100"):
            with self.subTest(extra=extra):
                rows = [f"H{i:02d} PASS intent room dept" for i in range(1, 21)]
                log.write_text("\n".join([*rows, f"{extra} PASS intent room dept"]), encoding="utf-8")
                with self.assertRaisesRegex(verification.VerificationError, "20"):
                    verification.validate_harness_cli(log)

    def test_harness_cli_unknown_status_rows_are_not_ignored(self):
        log = self.root / "harness.log"
        for case_id, status in (("H21", "SKIP"), ("H100", "ERROR"), ("H01", "SKIP")):
            with self.subTest(case_id=case_id, status=status):
                rows = [f"H{i:02d} PASS intent room dept" for i in range(1, 21)]
                log.write_text("\n".join([*rows, f"{case_id} {status} intent room dept"]), encoding="utf-8")
                with self.assertRaises(verification.VerificationError):
                    verification.validate_harness_cli(log)

    def test_source_capture_hashes_release_control_files_and_detects_drift(self):
        controls = ("Dockerfile", ".dockerignore", ".gitignore")
        (self.root / "app.py").write_text("# source fixture\n", encoding="utf-8")
        for name in controls:
            (self.root / name).write_bytes(b"original control\n")
        # Git metadata is a fixture; the production file selection and hashing run.
        def git_metadata(command, **kwargs):
            args = command[1:]
            stdout = ("\0".join((*controls, "app.py")) + "\0" if args[0] == "ls-files" else
                      "a" * 40 if args[0] == "rev-parse" else "")
            return subprocess.CompletedProcess(command, 0, stdout.encode("utf-8"), b"")
        with patch.object(verification, "ROOT", self.root), patch.object(verification.subprocess, "run", side_effect=git_metadata):
            before = verification.capture_source()["sha256_files"]
            self.assertEqual(set(before), {*controls, "app.py"})
            for name in controls:
                with self.subTest(name=name):
                    self.assertEqual(before[name], hashlib.sha256(b"original control\n").hexdigest())
                    (self.root / name).write_bytes(b"changed control\n")
                    after = verification.capture_source()["sha256_files"]
                    self.assertNotEqual(before[name], after[name])

    def test_child_guard_records_actual_isolated_dependency_versions(self):
        code = "\n".join([
            "import importlib.metadata, json, sys",
            "versions = {}",
            "for name in ('streamlit', 'fastapi', 'httpx', 'openai', 'typing_extensions'):",
            "    try: versions[name] = {'status': 'installed', 'version': importlib.metadata.version(name)}",
            "    except importlib.metadata.PackageNotFoundError: versions[name] = {'status': 'not-installed', 'version': None}",
            "assert 'hotel_core' not in sys.modules",
            "print(json.dumps(versions, sort_keys=True))",
        ])
        with verification.isolated_runtime("versions-actual") as (env, temporary, _cleanup):
            step = verification.run_logged_step(
                "versions-actual", [sys.executable, "-B", "-X", "utf8", "-c", code],
                self.root, "versions.log", env, temporary, kind="fixture")
            receipt = verification.load_report(temporary / "versions-actual.guard.json", kind="safety-guard",
                                               run_id="versions-actual", not_before=step["started_at_utc"])
        self.assertEqual(step["exit_code"], 0, (self.root / "versions.log").read_text(encoding="utf-8"))
        actual = json.loads((self.root / "versions.log").read_text(encoding="utf-8"))
        self.assertEqual(receipt.get("dependencies"), actual)
        self.assertEqual(step.get("dependencies"), actual)

    def test_child_guard_marks_missing_dependency_without_fabricating_version(self):
        # -S permits injecting an unavailable distribution before the real guard
        # runs; only importlib.metadata is replaced, never the guard or receipt.
        code = "\n".join([
            "import importlib.metadata",
            "def version(name):",
            "    if name == 'openai': raise importlib.metadata.PackageNotFoundError(name)",
            "    return 'fixture-1.2'",
            "importlib.metadata.version = version",
            "from scripts.verify_release import install_child_guards",
            "install_child_guards()",
        ])
        with verification.isolated_runtime("versions-missing") as (env, temporary, _cleanup):
            step = verification.run_logged_step(
                "versions-missing", [sys.executable, "-S", "-B", "-X", "utf8", "-c", code],
                self.root, "missing-version.log", env, temporary, kind="fixture")
            receipt = verification.load_report(temporary / "versions-missing.guard.json", kind="safety-guard",
                                               run_id="versions-missing", not_before=step["started_at_utc"])
        self.assertEqual(step["exit_code"], 0, (self.root / "missing-version.log").read_text(encoding="utf-8"))
        self.assertEqual(receipt.get("dependencies", {}).get("openai"), {"status": "not-installed", "version": None})
        self.assertEqual(receipt.get("dependencies", {}).get("fastapi"), {"status": "installed", "version": "fixture-1.2"})

    def test_manifest_uses_child_versions_and_rejects_inconsistent_receipts(self):
        dependencies = {name: {"status": "installed", "version": "fixture-child-1.2"}
                        for name in ("streamlit", "fastapi", "httpx", "openai", "typing_extensions")}
        def child_step(name, command, output, log_name, env, temporary, **kwargs):
            (output / log_name).write_text("fixture child output\n", encoding="utf-8")
            versions = {key: dict(value) for key, value in dependencies.items()}
            if name == "core-tests":
                versions["fastapi"]["version"] = "fixture-different-2.0"
            return {"name": name, "command": command, "started_at_utc": verification.utc_now(),
                    "exit_code": 0, "status": "FAIL", "metrics": {}, "log": log_name,
                    "errors": [], "duration_seconds": 0, "safety_guard_verified": True,
                    "dependencies": versions}
        output = self.root / "child-version-manifest"
        with patch.object(verification, "run_logged_step", side_effect=child_step):
            self.assertEqual(verification.verify_release(output), 1)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("dependencies"), dependencies)
        self.assertTrue(any("dependency versions changed" in error for error in manifest["steps"][1]["errors"]))

    def test_missing_json_is_failure(self):
        with self.assertRaisesRegex(verification.VerificationError, "missing or invalid JSON"):
            verification.load_report(self.root / "missing.json", kind="harness", run_id="current-run")

    def test_json_from_another_run_is_rejected(self):
        path = self.root / "harness.json"
        verification.write_json(path, self.harness_report())
        with self.assertRaisesRegex(verification.VerificationError, "run_id"):
            verification.load_report(path, kind="harness", run_id="new-run")

    def test_stale_json_timestamp_is_rejected(self):
        path = self.root / "harness.json"
        report = self.harness_report()
        report["generated_at_utc"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        verification.write_json(path, report)
        with self.assertRaisesRegex(verification.VerificationError, "stale"):
            verification.load_report(path, kind="harness", run_id="current-run", not_before=verification.utc_now())

    def test_existing_output_directory_is_rejected(self):
        output = self.root / "earlier-evidence"
        output.mkdir()
        old = output / "manifest.json"
        old.write_text('{"status":"PASS","run_id":"old"}', encoding="utf-8")
        before = old.read_bytes()
        self.assertEqual(verification.main(["--output-dir", str(output)]), 2)
        self.assertEqual(old.read_bytes(), before)

    def test_failure_still_writes_manifest_and_returns_nonzero(self):
        output = self.root / "failed-new-run"
        with patch.object(verification, "capture_source", side_effect=verification.VerificationError("fixture source failure")):
            self.assertEqual(verification.verify_release(output), 1)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "FAIL")
        self.assertEqual(manifest["scope"], "offline-code-baseline")
        self.assertEqual(manifest["field_readiness"], "NOT_VERIFIED")
        self.assertIn("finished_at_utc", manifest)
        self.assertTrue(manifest["errors"])

    def test_blackboard_broken_trace_is_failure(self):
        with patch.dict(os.environ, verification.SAFE_SETTINGS):
            scenarios = collect_scenarios()
        report = {"scenarios": scenarios, "assertions": scenario_assertions(scenarios)}
        self.assertEqual(validate_blackboard_report(report)["scenario_count"], 3)
        scenarios["manual_checkout_approval"]["after"]["trace"] = []
        # Keep the originally passing assertion flags: the snapshot must win.
        with self.assertRaisesRegex(verification.VerificationError, "manual_approval_trace"):
            validate_blackboard_report(report)

    def test_blackboard_manual_change_requires_real_before_and_after_states(self):
        with patch.dict(os.environ, verification.SAFE_SETTINGS):
            scenarios = collect_scenarios()
        scenarios["manual_checkout_approval"]["before"]["checkout_time"] = "14:00"
        report = {"scenarios": scenarios, "assertions": scenario_assertions(scenarios)}
        with self.assertRaisesRegex(verification.VerificationError, "manual_before_stays_at_noon"):
            validate_blackboard_report(report)

    def test_traceability_rejects_unloadable_test(self):
        method = "tests.test_release_verification.ReleaseVerificationTests.test_method_that_does_not_exist"
        with self.assertRaisesRegex(verification.VerificationError, "unloadable"):
            verification.validate_traceability(self.traceability_mapping(method), {method})

    def test_traceability_rejects_loadable_but_unexecuted_test(self):
        with self.assertRaisesRegex(verification.VerificationError, "did not execute"):
            verification.validate_traceability(self.traceability_mapping(), set())

    def test_traceability_requires_all_sixteen_requirements(self):
        mapping = self.traceability_mapping()
        mapping["requirements"].pop()
        with self.assertRaisesRegex(verification.VerificationError, "REQ-01..REQ-16"):
            verification.validate_traceability(mapping, set())

    def test_traceability_accepts_partial_without_claiming_field_readiness(self):
        mapping = self.traceability_mapping()
        method = mapping["requirements"][0]["tests"][0]
        metrics = verification.validate_traceability(mapping, {method})
        self.assertEqual(metrics["requirement_count"], 16)
        self.assertEqual(set(metrics["requirements_status"].values()), {"partial"})
        self.assertEqual(metrics["future_requirements"], ["FUT-01"])

    def test_safe_environment_removes_real_data_and_model_configuration(self):
        env = verification.safe_environment({
            "AEGIS_DB_PATH": "real-guest-database.sqlite3", "AEGIS_API_URL": "https://live-hotel.invalid",
            "AEGIS_PMS_FILE": "real-pms.json", "AEGIS_KNOWLEDGE_FILE": "private-policy.json",
            "AEGIS_BUSINESS_DATE": "2099-01-01", "OPENAI_API_KEY": "private-value",
            "AEGIS_STAFF_TOKEN": "private-value", "AEGIS_SIGNING_KEY": "private-value",
            "HTTPS_PROXY": "https://private-proxy.invalid", "PATH": "preserved",
        }, self.root)
        self.assertEqual(env["PATH"], "preserved")
        for key, value in verification.SAFE_SETTINGS.items():
            self.assertEqual(env[key], value)
        self.assertEqual(env["AEGIS_PMS_FILE"], "")
        self.assertEqual(env["AEGIS_KNOWLEDGE_FILE"], "")
        self.assertNotIn("private-value", env.values())
        self.assertNotIn("HTTPS_PROXY", env)

    def run_tiny_unittest(self, source, label):
        fixtures = self.root / label
        fixtures.mkdir()
        (fixtures / "test_fixture.py").write_text(source, encoding="utf-8")
        with verification.isolated_runtime(label) as (env, temporary, _cleanup):
            step = verification.run_logged_step(
                label, [sys.executable, "-B", "-X", "utf8", "-m", "unittest", "discover",
                        "-s", str(fixtures), "-p", "test_fixture.py", "-v"],
                self.root, label + ".log", env, temporary, kind="unittest", result_name=label + ".json")
            report = verification.load_report(self.root / (label + ".json"), kind="unittest", run_id=label,
                                               not_before=step["started_at_utc"])
        self.assertTrue(step["safety_guard_verified"])
        return step, report

    def test_real_unittest_skip_exit_zero_is_rejected(self):
        source = "import unittest\nclass Tiny(unittest.TestCase):\n    @unittest.skip('fixture')\n    def test_skip(self): pass\n"
        step, report = self.run_tiny_unittest(source, "tiny-skip")
        self.assertEqual(step["exit_code"], 0)
        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["status"], "FAIL")
        with self.assertRaises(verification.VerificationError):
            verification.validate_unittest_report(report, minimum=1)

    def test_real_unittest_zero_test_report_is_rejected(self):
        step, report = self.run_tiny_unittest("# deliberately no tests\n", "tiny-empty")
        # Python builds differ: some return 0, newer unittest versions return 5.
        self.assertIn(step["exit_code"], (0, 5))
        self.assertEqual(report["tests_run"], 0)
        self.assertEqual(report["status"], "FAIL")
        with self.assertRaises(verification.VerificationError):
            verification.validate_unittest_report(report, minimum=0)

    def test_real_unittest_failure_preserves_nonzero_and_structured_failure(self):
        source = "import unittest\nclass Tiny(unittest.TestCase):\n    def test_failure(self): self.fail('deliberate tiny fixture failure')\n"
        step, report = self.run_tiny_unittest(source, "tiny-fail")
        self.assertEqual(step["exit_code"], 1)
        self.assertEqual(report["failures"], 1)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("deliberate tiny fixture failure", (self.root / "tiny-fail.log").read_text(encoding="utf-8"))

    def test_runner_preserves_raw_logs_and_exit_code_on_failure(self):
        with verification.isolated_runtime("tiny-failure") as (env, temporary, _cleanup):
            step = verification.run_logged_step(
                "tiny-failure", [sys.executable, "-B", "-X", "utf8", "-c",
                                 "import sys; print('raw stdout'); print('raw stderr', file=sys.stderr); sys.exit(7)"],
                self.root, "failure.log", env, temporary, kind="fixture")
        self.assertEqual(step["exit_code"], 7)
        self.assertEqual(step["status"], "FAIL")
        self.assertTrue(step["safety_guard_verified"])
        self.assertGreaterEqual(step["duration_seconds"], 0)
        log = (self.root / "failure.log").read_text(encoding="utf-8")
        self.assertIn("raw stdout", log)
        self.assertIn("raw stderr", log)

    def test_safety_guards_block_external_network_and_non_temporary_sqlite(self):
        # A tiny subprocess exercises the actual startup bootstrap, not a full suite.
        code = "\n".join([
            "import pathlib, socket, sqlite3",
            "def denied(call):",
            "    try: call()",
            "    except PermissionError: return",
            "    raise AssertionError('unsafe operation was permitted')",
            "denied(lambda: socket.socket().connect(('192.0.2.1', 443)))",
            "denied(lambda: sqlite3.connect('outside-verification.sqlite3'))",
            "assert not pathlib.Path('config.json').exists()",
            "assert not pathlib.Path('.env').exists()",
            "print('guards verified')",
        ])
        with verification.isolated_runtime("tiny-guard") as (env, temporary, _cleanup):
            step = verification.run_logged_step(
                "tiny-guard", [sys.executable, "-B", "-X", "utf8", "-c", code],
                self.root, "guard.log", env, temporary, kind="fixture")
        self.assertEqual(step["exit_code"], 0, (self.root / "guard.log").read_text(encoding="utf-8"))
        self.assertTrue(step["safety_guard_verified"])
        self.assertEqual(step["errors"], [])


if __name__ == "__main__":
    unittest.main()
