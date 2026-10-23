"""Create fresh, offline release evidence; exit nonzero on incomplete evidence.

The public command deliberately runs the real unittest and Harness CLIs. A
private, per-run sitecustomize installs guards before any application import
and captures unittest's TestResult without interpreting its human-readable log.
Only this run's JSON files, not earlier logs, can satisfy the gates.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_VERSION = 1
BASELINE_CASES = frozenset(f"H{number:02d}" for number in range(1, 21))
REQUIREMENTS = frozenset(f"REQ-{number:02d}" for number in range(1, 17))
METRICS = ("intent_accuracy", "slot_filling_rate", "ticket_precision", "ticket_recall")
DEPENDENCY_PACKAGES = ("streamlit", "fastapi", "httpx", "openai", "typing_extensions")
SAFE_SETTINGS = {
    "AEGIS_FORCE_RULES": "1", "AEGIS_AUTH_ENABLED": "0", "AEGIS_DEPLOYED": "0",
    "AEGIS_DB_PATH": "", "AEGIS_SITE_PROFILE": "standard",
    "AEGIS_API_URL": "", "AEGIS_BUSINESS_DATE": "",
    "AEGIS_PMS_FILE": "", "AEGIS_KNOWLEDGE_FILE": "",
}
REQUIRED_FILES = (
    "full-tests.log", "full-tests.json", "core-tests.log", "core-tests.json",
    "harness.log", "harness-structured.log", "harness.json", "blackboard.log",
    "blackboard.json", "validation.log", "traceability.json",
)


class VerificationError(ValueError):
    """Evidence is absent, inconsistent, stale or below its declared gate."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any], *, exclusive: bool = True) -> None:
    with path.open("x" if exclusive else "w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def envelope(kind: str, run_id: str, **payload: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "kind": kind, "run_id": run_id,
            "generated_at_utc": utc_now(), **payload}


def reserve_output_directory(path: Path) -> Path:
    """Even an empty existing directory is earlier evidence and is rejected."""
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def safe_environment(base: dict[str, str], temporary: Path) -> dict[str, str]:
    env = dict(base)
    # Values from the caller are never printed or copied into the manifest.
    for key in list(env):
        if (key.startswith("AEGIS_") or key.startswith(("OPENAI_", "SILICONFLOW_"))
                or key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}):
            env.pop(key, None)
    env.update(SAFE_SETTINGS)
    env.update({
        "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1", "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
    })
    return env


@contextmanager
def isolated_runtime(run_id: str):
    """Keep test databases outside the evidence directory and never share them."""
    parent = ROOT / (".venv" if (ROOT / ".venv").is_dir() else ".artifacts") / "task-temp"
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="aegis-verification-", dir=parent)).resolve()
    bootstrap = temporary / "bootstrap"
    bootstrap.mkdir()
    # If sitecustomize fails, Python normally continues unsafely. Exit before
    # the requested module can execute instead; the parent also checks a receipt.
    (bootstrap / "sitecustomize.py").write_text(
        "import os, sys, traceback\n"
        "try:\n"
        f"    sys.path.insert(0, {str(ROOT)!r})\n"
        "    from scripts.verify_release import install_child_guards\n"
        "    install_child_guards()\n"
        "except BaseException:\n"
        "    traceback.print_exc()\n"
        "    sys.stderr.flush()\n"
        "    os._exit(86)\n", encoding="utf-8")
    env = safe_environment(os.environ, temporary)
    env.update({"PYTHONPATH": os.pathsep.join((str(bootstrap), str(ROOT))),
                "AEGIS_VERIFY_TEMP": str(temporary), "AEGIS_VERIFY_RUN_ID": run_id})
    cleanup = {"status": "PENDING"}
    try:
        yield env, temporary, cleanup
    finally:
        try:
            # The resolved target is a newly allocated child of our fixed root.
            if temporary.parent != parent.resolve():
                raise VerificationError("unsafe temporary-directory cleanup target")
            shutil.rmtree(temporary)
            cleanup["status"] = "PASS"
        except OSError as error:
            cleanup.update(status="RETAINED", reason=type(error).__name__)


def _canonical_test_id(name: str) -> str:
    return "tests." + name if name.startswith("test_") else name


def _flatten_tests(suite):
    import unittest
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _flatten_tests(test)
        else:
            yield test


def _install_unittest_reporting() -> None:
    import unittest
    original = unittest.TextTestRunner
    result_path = Path(os.environ["AEGIS_VERIFY_RESULT"])
    run_id = os.environ["AEGIS_VERIFY_RUN_ID"]

    class EvidenceRunner(original):
        def run(self, test):
            test_ids = [_canonical_test_id(case.id()) for case in _flatten_tests(test)]
            result = super().run(test)
            payload = envelope(
                "unittest", run_id, status="FAIL", test_ids=test_ids,
                collected=len(test_ids), tests_run=result.testsRun,
                successful=result.wasSuccessful(), failures=len(result.failures),
                errors=len(result.errors), skipped=len(result.skipped),
                expected_failures=len(result.expectedFailures),
                unexpected_successes=len(result.unexpectedSuccesses),
            )
            try:
                validate_unittest_report(payload, minimum=1)
                payload["status"] = "PASS"
            except VerificationError as error:
                payload["validation_error"] = str(error)
            write_json(result_path, payload)
            return result

    unittest.TextTestRunner = EvidenceRunner
    unittest.runner.TextTestRunner = EvidenceRunner


def collect_dependency_versions() -> dict[str, dict[str, Any]]:
    """Read distribution metadata in the guarded child, without package imports."""
    from importlib.metadata import PackageNotFoundError, version

    dependencies = {}
    for name in DEPENDENCY_PACKAGES:
        try:
            dependencies[name] = {"status": "installed", "version": version(name)}
        except PackageNotFoundError:
            dependencies[name] = {"status": "not-installed", "version": None}
    return dependencies


def install_child_guards() -> None:
    """Called only in disposable subprocesses, before importing business code."""
    import ipaddress
    import socket
    import sqlite3
    from urllib.parse import unquote, urlsplit

    for key, value in SAFE_SETTINGS.items():
        if os.environ.get(key) != value:
            raise VerificationError(f"unsafe child setting: {key}")
    temporary = Path(os.environ["AEGIS_VERIFY_TEMP"]).resolve()
    if not temporary.is_dir():
        raise VerificationError("isolated temporary directory is absent")
    # hotel_core auto-loads these files at import time. Conceal only these two
    # optional configuration files inside this child, rather than reading keys.
    concealed = {os.path.normcase(str(ROOT / name)) for name in (".env", "config.json")}
    exists = Path.exists

    def safe_exists(path):
        if os.path.normcase(os.path.abspath(os.fspath(path))) in concealed:
            return False
        return exists(path)

    Path.exists = safe_exists
    connect = sqlite3.connect

    def isolated_connect(database, *args, **kwargs):
        raw = os.fsdecode(database)
        if raw != ":memory:":
            if raw.startswith("file:"):
                parsed = urlsplit(raw)
                raw = unquote(parsed.path)
                if os.name == "nt" and re.match(r"^/[A-Za-z]:", raw):
                    raw = raw[1:]
            target = Path(raw).resolve()
            if not target.is_relative_to(temporary):
                raise PermissionError("verification SQLite access outside its temporary directory")
        return connect(database, *args, **kwargs)

    sqlite3.connect = isolated_connect
    socket_connect, socket_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def local_address(address) -> bool:
        if not isinstance(address, tuple) or not address:
            return False
        host = address[0]
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def offline_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not local_address(address):
            raise PermissionError("external network disabled during offline verification")
        return socket_connect(sock, address)

    def offline_connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not local_address(address):
            raise PermissionError("external network disabled during offline verification")
        return socket_connect_ex(sock, address)

    socket.socket.connect, socket.socket.connect_ex = offline_connect, offline_connect_ex
    tempfile.tempdir = str(temporary)
    if os.environ.get("AEGIS_VERIFY_KIND") == "unittest":
        _install_unittest_reporting()
    os.environ["AEGIS_VERIFY_GUARD_INSTALLED"] = "1"
    receipt = Path(os.environ["AEGIS_VERIFY_GUARD_PATH"]).resolve()
    if not receipt.is_relative_to(temporary):
        raise VerificationError("guard receipt is outside temporary directory")
    write_json(receipt, envelope("safety-guard", os.environ["AEGIS_VERIFY_RUN_ID"], status="PASS",
                               config_files_hidden=True, sqlite_isolated=True,
                               external_network_blocked=True, settings=SAFE_SETTINGS,
                               dependencies=collect_dependency_versions()))


def load_report(path: Path, *, kind: str, run_id: str,
                not_before: str | None = None) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise VerificationError(f"missing or invalid JSON evidence: {path.name}") from error
    if (not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION
            or report.get("kind") != kind or report.get("run_id") != run_id):
        raise VerificationError(f"wrong schema, kind or run_id: {path.name}")
    try:
        generated = datetime.fromisoformat(report["generated_at_utc"].replace("Z", "+00:00"))
        lower = datetime.fromisoformat(not_before.replace("Z", "+00:00")) if not_before else None
        if generated.tzinfo is None or generated > datetime.now(timezone.utc) or (lower and generated < lower):
            raise ValueError("stale timestamp")
    except (KeyError, TypeError, ValueError) as error:
        raise VerificationError(f"missing, invalid or stale evidence timestamp: {path.name}") from error
    return report


def _count(report: dict[str, Any], key: str) -> int:
    value = report.get(key)
    if type(value) is not int or value < 0:
        raise VerificationError(f"invalid counter: {key}")
    return value


def validate_unittest_report(report: dict[str, Any], *, minimum: int,
                             core: bool = False) -> dict[str, Any]:
    count = _count(report, "tests_run")
    if count < minimum or count == 0:
        raise VerificationError(f"only {count} tests ran; at least {minimum} required")
    for key in ("failures", "errors", "skipped", "expected_failures", "unexpected_successes"):
        if _count(report, key):
            raise VerificationError(f"unittest {key} must be zero")
    if report.get("successful") is not True:
        raise VerificationError("unittest result is unsuccessful despite its process exit code")
    ids = report.get("test_ids")
    if (not isinstance(ids, list) or any(not isinstance(name, str) for name in ids)
            or len(set(ids)) != count or len(ids) != count or _count(report, "collected") != count):
        raise VerificationError("collected and executed test identities are inconsistent")
    if any(not name.startswith("tests.test_") for name in ids):
        raise VerificationError("a collected identity is outside the test suite")
    if core and any(not name.startswith("tests.test_app.") for name in ids):
        raise VerificationError("core evidence contains tests outside tests/test_app.py")
    return {key: report[key] for key in ("tests_run", "collected", "failures", "errors", "skipped",
                                       "expected_failures", "unexpected_successes")}


def validate_harness_report(report: dict[str, Any]) -> dict[str, Any]:
    total, passed = _count(report, "total"), _count(report, "passed")
    rows = report.get("rows")
    if total != 20 or not isinstance(rows, list) or len(rows) != total or passed != 20:
        raise VerificationError("Harness totals must show exactly 20 complete passing cases")
    if any(not isinstance(row, dict) or row.get("status") != "PASS" for row in rows):
        raise VerificationError("Harness contains a failed or malformed row")
    ids = [row.get("id") for row in rows]
    if any(not isinstance(name, str) for name in ids) or len(set(ids)) != total or set(ids) != BASELINE_CASES:
        raise VerificationError("Harness must contain unique H01..H20 baseline cases")
    for key in METRICS:
        value = report.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value != 1.0:
            raise VerificationError(f"Harness {key} must equal 1.0")
    if (report.get("model_name") != "确定性规则引擎 (离线保底)"
            or report.get("eval_mode") != "⚡ 离线规则快速回归"
            or any(not is_offline_mode(row.get("model_mode")) for row in rows)):
        raise VerificationError("Harness evidence is not the deterministic offline baseline")
    return {"total": total, "passed": passed, "baseline_ids": sorted(BASELINE_CASES),
            **{key: report[key] for key in METRICS}}


def is_offline_mode(mode: Any) -> bool:
    return mode == "规则降级" or mode in {
        "规则降级 / HousekeepingAgent", "规则降级 / EngineeringAgent",
        "规则降级 / ManagerEscalationAgent", "规则降级 / ConciergeAgent",
        "规则降级 / DispatchAgent", "规则降级 / 重复申请复用",
    }


def validate_harness_cli(path: Path) -> dict[str, Any]:
    # Collect case rows before validating status, so SKIP/ERROR cannot vanish.
    rows = re.findall(r"^(H\d+)(?:[ \t]+(\S+))?", path.read_text(encoding="utf-8"), re.MULTILINE)
    ids = [row[0] for row in rows]
    if (len(rows) != 20 or len(set(ids)) != len(ids) or set(ids) != BASELINE_CASES
            or any(status != "PASS" for _, status in rows)):
        raise VerificationError("Harness CLI did not emit unique H01..H20 passing rows (exit 0 is insufficient)")
    return {"total": len(rows), "passed": len(rows), "baseline_ids": sorted(BASELINE_CASES)}


def validate_baseline_test_ids(contract: dict[str, Any], executed_ids: set[str], *, core: bool = False) -> dict[str, Any]:
    """New tests cannot make up for deleting/replacing a committed baseline test."""
    ids = contract.get("test_ids") if isinstance(contract, dict) else None
    if (not isinstance(contract, dict) or contract.get("schema_version") != 1
            or contract.get("expected_count") != 127 or not isinstance(ids, list)
            or len(ids) != 127 or any(not isinstance(name, str) for name in ids)
            or len(set(ids)) != 127
            or not re.fullmatch(r"[0-9a-f]{40}", str(contract.get("baseline_commit", "")))):
        raise VerificationError("invalid committed 127-test baseline contract")
    required = {name for name in ids if not core or name.startswith("tests.test_app.")}
    if core and len(required) != 40:
        raise VerificationError("committed core baseline must contain 40 tests")
    missing = sorted(required - executed_ids)
    if missing:
        raise VerificationError("committed baseline tests did not execute: " + ", ".join(missing))
    return {"baseline_commit": contract["baseline_commit"], "expected_count": len(required),
            "executed_count": len(required), "missing": []}


def baseline_metrics(executed_ids: set[str], *, core: bool = False) -> dict[str, Any]:
    path = ROOT / "spec" / "baseline_test_ids.json"
    payload = path.read_bytes()
    contract = json.loads(payload)
    return {**validate_baseline_test_ids(contract, executed_ids, core=core),
            "source": "spec/baseline_test_ids.json", "sha256": hashlib.sha256(payload).hexdigest()}


def validate_traceability(mapping: dict[str, Any], executed_ids: set[str]) -> dict[str, Any]:
    import unittest
    if not isinstance(mapping, dict) or mapping.get("schema_version") != 1:
        raise VerificationError("traceability source schema_version must be 1")
    requirements = mapping.get("requirements")
    if not isinstance(requirements, list) or len(requirements) != 16:
        raise VerificationError("traceability must contain exactly REQ-01..REQ-16")
    ids = [item.get("id") if isinstance(item, dict) else None for item in requirements]
    if any(not isinstance(name, str) for name in ids) or set(ids) != REQUIREMENTS or len(set(ids)) != 16:
        raise VerificationError("traceability requirement identities are missing or duplicated")
    all_tests: set[str] = set()
    for item in requirements:
        identifier = item["id"]
        if item.get("acceptance_id") != identifier.replace("REQ-", "AC-"):
            raise VerificationError(f"wrong acceptance mapping: {identifier}")
        if item.get("status") not in {"implemented", "partial", "planned"}:
            raise VerificationError(f"invalid requirement status: {identifier}")
        for key in ("tests", "experiment_ids", "evidence"):
            values = item.get(key)
            if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
                raise VerificationError(f"nonempty {key} required: {identifier}")
        for filename in item["evidence"]:
            if filename not in {*REQUIRED_FILES, "manifest.json"}:
                raise VerificationError(f"unknown evidence filename for {identifier}: {filename}")
        for name in item["tests"]:
            if not re.fullmatch(r"tests\.test_\w+\.\w+\.test_\w+", name):
                raise VerificationError(f"test must be a fully qualified unittest method: {name}")
            loader = unittest.TestLoader()
            cases = list(_flatten_tests(loader.loadTestsFromName(name)))
            if loader.errors or len(cases) != 1 or cases[0].id() != name:
                raise VerificationError(f"unloadable unittest mapping: {name}")
            if name not in executed_ids:
                raise VerificationError(f"mapped unittest did not execute in this run: {name}")
            all_tests.add(name)
    future = mapping.get("future_requirements", [])
    if not isinstance(future, list) or any(not isinstance(item, dict) or not str(item.get("id", "")).startswith("FUT-") for item in future):
        raise VerificationError("future requirements must use FUT- identities")
    return {"requirement_count": 16, "mapped_test_count": len(all_tests), "executed_test_count": len(all_tests),
            "requirements_status": {item["id"]: item["status"] for item in requirements},
            "test_ids": sorted(all_tests), "future_requirements": [item["id"] for item in future]}


def capture_source() -> dict[str, Any]:
    def git(*arguments):
        completed = subprocess.run(["git", *arguments], cwd=ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, check=False)
        if completed.returncode:
            raise VerificationError("Git source identity could not be captured")
        return completed.stdout.decode("utf-8", errors="strict")

    names = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    blocked = {".git", ".venv", ".artifacts", "__pycache__", "var", "backups", ".agents", ".codex"}
    hashes = {}
    for name in sorted(set(names)):
        path = Path(name)
        if (not name or any(part in blocked for part in path.parts)
                or path.name in {"config.json", ".env", "deploy.env"}
                or (path.name not in {"Dockerfile", ".dockerignore", ".gitignore"}
                    and path.suffix.lower() not in {".py", ".json", ".md", ".txt", ".toml", ".yml", ".yaml", ".example"})
                or not (ROOT / path).is_file()):
            continue
        hashes[path.as_posix()] = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    if not hashes:
        raise VerificationError("no source files were hashed")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    return {"git_commit": git("rev-parse", "HEAD").strip(), "git_dirty": bool(status.strip()),
            "git_status": status.splitlines(), "sha256_files": hashes}


def run_logged_step(name: str, command: list[str], output: Path, log_name: str,
                    env: dict[str, str], temporary: Path, *, kind: str,
                    result_name: str | None = None, timeout: float = 300) -> dict[str, Any]:
    started, clock_start = utc_now(), time.perf_counter()
    child_env = dict(env)
    receipt = temporary / f"{name}.guard.json"
    child_env.update(AEGIS_VERIFY_KIND=kind, AEGIS_VERIFY_GUARD_PATH=str(receipt))
    if result_name:
        child_env["AEGIS_VERIFY_RESULT"] = str(output / result_name)
    record = {"name": name, "command": command, "started_at_utc": started, "exit_code": None,
              "status": "FAIL", "metrics": {}, "log": log_name, "errors": []}
    if result_name:
        record["report"] = result_name
    with (output / log_name).open("xb") as log:
        try:
            completed = subprocess.run(command, cwd=ROOT, env=child_env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
            record["exit_code"] = completed.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            message = f"\nVerification runner: {type(error).__name__}: {error}\n"
            log.write(message.encode("utf-8"))
            record["errors"].append(str(error))
    record.update(finished_at_utc=utc_now(), duration_seconds=round(time.perf_counter() - clock_start, 6))
    try:
        guard = load_report(receipt, kind="safety-guard", run_id=env["AEGIS_VERIFY_RUN_ID"], not_before=started)
        if guard.get("status") != "PASS" or guard.get("settings") != SAFE_SETTINGS:
            raise VerificationError("child safety guard was not verified")
        dependencies = guard.get("dependencies")
        if not isinstance(dependencies, dict) or set(dependencies) != set(DEPENDENCY_PACKAGES):
            raise VerificationError("child dependency metadata is incomplete")
        for package in DEPENDENCY_PACKAGES:
            item = dependencies[package]
            if (not isinstance(item, dict) or not (
                    (item.get("status") == "installed" and isinstance(item.get("version"), str) and item["version"])
                    or (item.get("status") == "not-installed" and "version" in item and item["version"] is None))):
                raise VerificationError(f"invalid child dependency metadata: {package}")
        record["dependencies"] = dependencies
        record["safety_guard_verified"] = True
    except VerificationError as error:
        record["errors"].append(str(error))
    if record["exit_code"] != 0:
        record["errors"].append(f"process exit code is {record['exit_code']}")
    return record


def _worker(kind: str, output: Path, run_id: str, full_tests: Path | None) -> int:
    if os.environ.get("AEGIS_VERIFY_GUARD_INSTALLED") != "1":
        raise VerificationError("private worker requires the isolated startup guard")
    report = envelope(kind, run_id, status="FAIL")
    try:
        if kind == "harness":
            from harness.eval_runner import print_report, run_evaluation
            report.update(run_evaluation(use_cloud_model=False))
            print_report(report)
            validate_harness_report(report)
        elif kind == "traceability":
            if full_tests is None:
                raise VerificationError("traceability validation requires this run's full-test evidence")
            full = load_report(full_tests, kind="unittest", run_id=run_id)
            validate_unittest_report(full, minimum=127)
            mapping_file = ROOT / "spec" / "traceability.json"
            mapping = json.loads(mapping_file.read_text(encoding="utf-8"))
            report.update(validate_traceability(mapping, set(full["test_ids"])))
            report["source_sha256"] = hashlib.sha256(mapping_file.read_bytes()).hexdigest()
            print(f"Validated {report['requirement_count']} requirements and {report['mapped_test_count']} actual executed tests.")
        else:
            raise VerificationError("unknown worker kind")
        report["status"] = "PASS"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
    write_json(output, report)
    return 0 if report["status"] == "PASS" else 1


def verify_release(output_dir: Path) -> int:
    output = reserve_output_directory(output_dir)
    run_id, started, clock_start = uuid.uuid4().hex, utc_now(), time.perf_counter()
    manifest = {"schema_version": 1, "run_id": run_id, "status": "FAIL",
                "scope": "offline-code-baseline", "field_readiness": "NOT_VERIFIED",
                "started_at_utc": started, "python": {"executable": sys.executable, "version": sys.version},
                "platform": {"sys_platform": sys.platform, "os_name": os.name,
                             "platform": platform.platform(), "machine": platform.machine()}, "steps": [], "errors": [],
                "safety": {"settings": SAFE_SETTINGS, "synthetic_data_only": True,
                           "external_network_blocked": True, "local_config_files_hidden": True,
                           "databases": "per-run temporary directory outside evidence"}}
    write_json(output / "manifest.json", manifest)
    try:
        manifest["source"] = capture_source()
        with isolated_runtime(run_id) as (env, temporary, cleanup):
            python = [sys.executable, "-B", "-X", "utf8"]
            jobs = [
                ("full-tests", python + ["-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
                 "full-tests.log", "unittest", "full-tests.json"),
                ("core-tests", python + ["-m", "unittest", "tests/test_app.py", "-v"],
                 "core-tests.log", "unittest", "core-tests.json"),
                ("harness-cli", python + ["-m", "harness.eval_runner"], "harness.log", "cli", None),
                ("harness", python + [str(Path(__file__).resolve()), "--_worker", "harness", "--json-output",
                                      str(output / "harness.json"), "--run-id", run_id],
                 "harness-structured.log", "harness", "harness.json"),
                ("blackboard", python + [str(ROOT / "scripts" / "verify_blackboard.py"), "--_worker",
                                         "--output", str(output / "blackboard.json"), "--run-id", run_id],
                 "blackboard.log", "blackboard", "blackboard.json"),
                ("traceability", python + [str(Path(__file__).resolve()), "--_worker", "traceability", "--json-output",
                                           str(output / "traceability.json"), "--run-id", run_id, "--full-tests-json",
                                           str(output / "full-tests.json")],
                 "validation.log", "traceability", "traceability.json"),
            ]
            for name, command, log_name, kind, result_name in jobs:
                step = run_logged_step(name, command, output, log_name, env, temporary,
                                       kind=kind, result_name=result_name)
                try:
                    if step.get("safety_guard_verified") is True:
                        if "dependencies" not in manifest:
                            manifest["dependencies"] = step["dependencies"]
                        elif manifest["dependencies"] != step["dependencies"]:
                            raise VerificationError("child dependency versions changed between verification steps")
                    if kind == "cli":
                        step["metrics"] = validate_harness_cli(output / log_name)
                    else:
                        report = load_report(output / result_name, kind=kind, run_id=run_id,
                                             not_before=step["started_at_utc"])
                        if kind == "unittest":
                            step["metrics"] = validate_unittest_report(report, minimum=40 if name == "core-tests" else 127,
                                                                       core=name == "core-tests")
                            step["metrics"]["baseline"] = baseline_metrics(set(report["test_ids"]), core=name == "core-tests")
                            if name == "full-tests":
                                manifest["baseline"] = step["metrics"]["baseline"]
                        elif kind == "harness":
                            step["metrics"] = validate_harness_report(report)
                        elif kind == "blackboard":
                            from scripts.verify_blackboard import validate_blackboard_report
                            step["metrics"] = validate_blackboard_report(report)
                        elif kind == "traceability":
                            if report.get("requirement_count") != 16 or report.get("mapped_test_count", 0) <= 0:
                                raise VerificationError("traceability validation is incomplete")
                            step["metrics"] = {key: report[key] for key in ("requirement_count", "mapped_test_count", "executed_test_count", "requirements_status")}
                        if report.get("status") != "PASS":
                            raise VerificationError(f"{kind} report status is not PASS")
                except (OSError, ValueError, KeyError, TypeError) as error:
                    step["errors"].append(str(error))
                if not step["errors"]:
                    step["status"] = "PASS"
                manifest["steps"].append(step)
                print(f"{name}: {step['status']} (exit={step['exit_code']}, {step['duration_seconds']:.3f}s)", flush=True)
                write_json(output / "manifest.json", manifest, exclusive=False)
        manifest["temporary_cleanup"] = cleanup
        manifest["source_end"] = capture_source()
        if (manifest["source"]["git_commit"] != manifest["source_end"]["git_commit"]
                or manifest["source"]["sha256_files"] != manifest["source_end"]["sha256_files"]):
            manifest["errors"].append("source changed while verification was running; rerun against stable sources")
        missing = [name for name in REQUIRED_FILES if not (output / name).is_file()]
        if missing:
            manifest["errors"].append("missing required evidence: " + ", ".join(missing))
        if not manifest["errors"] and all(step["status"] == "PASS" for step in manifest["steps"]) and len(manifest["steps"]) == 6:
            manifest["status"] = "PASS"
    except Exception as error:
        manifest["errors"].append(f"{type(error).__name__}: {error}")
        traceback.print_exc()
    finally:
        manifest.update(finished_at_utc=utc_now(), duration_seconds=round(time.perf_counter() - clock_start, 6))
        write_json(output / "manifest.json", manifest, exclusive=False)
    print(f"Offline code baseline: {manifest['status']} | field readiness: NOT_VERIFIED | {output / 'manifest.json'}")
    return 0 if manifest["status"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--_worker", choices=("harness", "traceability"), help=argparse.SUPPRESS)
    parser.add_argument("--json-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    parser.add_argument("--full-tests-json", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args._worker:
            if not args.json_output or not args.run_id:
                parser.error("private worker arguments are incomplete")
            return _worker(args._worker, args.json_output, args.run_id, args.full_tests_json)
        if not args.output_dir:
            parser.error("--output-dir is required and must name a new directory")
        return verify_release(args.output_dir)
    except (OSError, VerificationError) as error:
        print(f"Verification refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
