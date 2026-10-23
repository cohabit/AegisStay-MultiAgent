"""Offline regression runner for the AegisStay Blackboard workflow."""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "harness" / "eval_dataset.json"
EXTENDED_DATASET = ROOT / "harness" / "eval_extended.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hotel_core import HotelOrchestrator


def intent_matches(expected: str, actual: str) -> bool:
    """Compare intents while treating the two out-of-scope labels as aliases.

    The deterministic fallback calls general topics ``其他`` while a cloud
    model may use the more descriptive ``闲聊`` label.  Both routes use the
    same no-ticket soft-pivot workflow, so the harness should not penalize
    that naming difference when the dataset expects ``其他``.
    """
    if expected == "其他":
        return actual in {"其他", "闲聊"}
    return actual == expected


def load_dataset(path: Path = DATASET) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_extended_dataset(path: Path = EXTENDED_DATASET) -> list[dict[str, Any]]:
    """Load the stratified quality set without changing the H01…H20 contract."""
    return json.loads(path.read_text(encoding="utf-8"))


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Return a reproducible Wilson 95% interval for a binomial rate."""
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def run_evaluation(
    dataset: list[dict[str, Any]] | None = None,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
    use_cloud_model: bool = False,
    include_extended: bool = False,
) -> dict[str, Any]:
    if dataset is not None:
        cases = dataset
        dataset_label = "custom"
    else:
        cases = load_dataset()
        dataset_label = "baseline"
        if include_extended:
            cases = [*cases, *load_extended_dataset()]
            dataset_label = "baseline+extended"
    rows: list[dict[str, Any]] = []
    intent_hits = 0
    slot_hits = 0
    expected_ticket_count = 0
    actual_ticket_count = 0
    correct_ticket_count = 0
    total_ms = 0.0
    group_stats: dict[str, dict[str, Any]] = {}

    eval_model_name = "确定性规则引擎 (离线保底)"
    for index, case in enumerate(cases, start=1):
        started = time.perf_counter()
        previous_profile = os.environ.get("AEGIS_SITE_PROFILE")
        case_profile = case.get("profile")
        if case_profile:
            os.environ["AEGIS_SITE_PROFILE"] = str(case_profile)
        else:
            os.environ.pop("AEGIS_SITE_PROFILE", None)
        flow = HotelOrchestrator(force_rules=not use_cloud_model)
        if use_cloud_model:
            eval_model_name = (
                f"云端模型: {flow.model.model}"
                if flow.model.client
                else f"云端模型不可用 (配置: {flow.model.model})"
            )
        for turn in case["turns"]:
            flow.process(turn)
        if previous_profile is None:
            os.environ.pop("AEGIS_SITE_PROFILE", None)
        else:
            os.environ["AEGIS_SITE_PROFILE"] = previous_profile
        elapsed_ms = (time.perf_counter() - started) * 1000
        state = flow.state
        actual_ticket = state.tickets[-1] if state.tickets else None
        expected_ticket = bool(case["should_create_ticket"])
        slot_complete = (not expected_ticket) or bool(state.room_no and actual_ticket)
        intent_ok = intent_matches(case["expected_intent"], state.intent)
        dept_ok = (not expected_ticket) if not actual_ticket else actual_ticket["assigned_dept"] == case["expected_dept"]
        status_ok = intent_ok and slot_complete and (actual_ticket is not None) == expected_ticket and dept_ok
        intent_hits += int(intent_ok)
        slot_hits += int(expected_ticket and slot_complete)
        expected_ticket_count += int(expected_ticket)
        actual_ticket_count += int(actual_ticket is not None)
        correct_ticket_count += int(expected_ticket and actual_ticket is not None and dept_ok)
        total_ms += elapsed_ms
        row = {
            "id": case["id"],
            "dataset": "baseline" if str(case["id"]).startswith("H") else "extended",
            "group": case.get("group", "baseline"),
            "name": case["name"],
            "status": "PASS" if status_ok else "FAIL",
            "expected_intent": case["expected_intent"],
            "actual_intent": state.intent,
            "room_no": state.room_no or "—",
            "expected_dept": case["expected_dept"] or "—",
            "actual_dept": actual_ticket["assigned_dept"] if actual_ticket else "—",
            "ticket_created": bool(actual_ticket),
            "model_mode": state.model_mode,
            "response_ms": round(elapsed_ms, 2),
        }
        rows.append(row)
        group = str(case.get("group", "baseline"))
        stats = group_stats.setdefault(group, {"group": group, "total": 0, "passed": 0, "intent_hits": 0, "slot_hits": 0, "expected_tickets": 0, "actual_tickets": 0, "correct_tickets": 0, "response_ms": []})
        stats["total"] += 1
        stats["passed"] += int(status_ok)
        stats["intent_hits"] += int(intent_ok)
        stats["slot_hits"] += int(expected_ticket and slot_complete)
        stats["expected_tickets"] += int(expected_ticket)
        stats["actual_tickets"] += int(actual_ticket is not None)
        stats["correct_tickets"] += int(expected_ticket and actual_ticket is not None and dept_ok)
        stats["response_ms"].append(elapsed_ms)
        if progress_callback:
            progress_callback(index, len(cases), row)

    total = len(cases) or 1
    latencies = sorted(row["response_ms"] for row in rows)
    p95_index = max(0, math.ceil(0.95 * len(latencies)) - 1) if latencies else 0
    groups = []
    for group, stats in group_stats.items():
        group_total = stats["total"] or 1
        group_passed = stats["passed"]
        group_expected = stats["expected_tickets"]
        group_actual = stats["actual_tickets"]
        group_correct = stats["correct_tickets"]
        groups.append({
            "group": group,
            "total": stats["total"],
            "passed": group_passed,
            "pass_rate": group_passed / group_total,
            "pass_rate_ci95": wilson_interval(group_passed, group_total),
            "intent_accuracy": stats["intent_hits"] / group_total,
            "slot_filling_rate": stats["slot_hits"] / group_expected if group_expected else 1.0,
            "ticket_precision": group_correct / group_actual if group_actual else 1.0,
            "ticket_recall": group_correct / group_expected if group_expected else 1.0,
            "average_response_ms": sum(stats["response_ms"]) / group_total,
        })
    groups.sort(key=lambda item: item["group"])
    baseline_total = sum(row["dataset"] == "baseline" for row in rows)
    extended_total = sum(row["dataset"] == "extended" for row in rows)
    extended_passed = sum(row["dataset"] == "extended" and row["status"] == "PASS" for row in rows)
    return {
        "rows": rows,
        "total": len(cases),
        "passed": sum(row["status"] == "PASS" for row in rows),
        "dataset": dataset_label,
        "baseline_total": baseline_total,
        "extended_total": extended_total,
        "extended_passed": extended_passed,
        "groups": groups,
        "pass_rate_ci95": wilson_interval(sum(row["status"] == "PASS" for row in rows), len(cases)),
        "response_p95_ms": latencies[p95_index] if latencies else 0.0,
        "intent_accuracy": intent_hits / total,
        "slot_filling_rate": slot_hits / expected_ticket_count if expected_ticket_count else 1.0,
        "ticket_precision": correct_ticket_count / actual_ticket_count if actual_ticket_count else 1.0,
        "ticket_recall": correct_ticket_count / expected_ticket_count if expected_ticket_count else 1.0,
        "average_response_ms": total_ms / total,
        "eval_mode": "🤖 云端大模型真实质检" if use_cloud_model else "⚡ 离线规则快速回归",
        "model_name": eval_model_name,
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"\nAegisStay Harness Evaluation · {report.get('eval_mode', '')}")
    print("=" * 86)
    print(f"Target: {report.get('model_name', 'Default')} | Dataset: {report.get('dataset', 'baseline')} | Cases: {report['total']} | Passed: {report['passed']} | Avg response: {report['average_response_ms']:.2f} ms | P95: {report.get('response_p95_ms', 0.0):.2f} ms")
    print(f"Baseline: {report.get('baseline_total', 0)} | Extended: {report.get('extended_passed', 0)}/{report.get('extended_total', 0)} | Pass-rate 95% CI: {report.get('pass_rate_ci95', (0.0, 0.0))}")
    print(f"Intent Accuracy: {report['intent_accuracy']:.1%}")
    print(f"Slot Filling Rate: {report['slot_filling_rate']:.1%}")
    print(f"Ticket Precision: {report['ticket_precision']:.1%} | Ticket Recall: {report['ticket_recall']:.1%}")
    for group in report.get("groups", []):
        print(f"Group {group['group']}: {group['passed']}/{group['total']} ({group['pass_rate']:.1%}) · intent {group['intent_accuracy']:.1%} · slot {group['slot_filling_rate']:.1%}")
    print("-" * 86)
    print(f"{'ID':<5} {'STATUS':<7} {'INTENT':<8} {'ROOM':<7} {'DEPT':<8} {'TICKET':<7} {'MODE':<25} NAME")
    for row in report["rows"]:
        print(f"{row['id']:<5} {row['status']:<7} {row['actual_intent']:<8} {row['room_no']:<7} {row['actual_dept']:<8} {str(row['ticket_created']):<7} {row.get('model_mode', ''):<25} {row['name']}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    use_cloud = "--cloud" in sys.argv
    include_extended = "--extended" in sys.argv
    print_report(run_evaluation(use_cloud_model=use_cloud, include_extended=include_extended))

