"""Offline regression runner for the AegisStay Blackboard workflow."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "harness" / "eval_dataset.json"
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


def run_evaluation(
    dataset: list[dict[str, Any]] | None = None,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
    use_cloud_model: bool = False,
) -> dict[str, Any]:
    cases = dataset or load_dataset()
    rows: list[dict[str, Any]] = []
    intent_hits = 0
    slot_hits = 0
    expected_ticket_count = 0
    actual_ticket_count = 0
    correct_ticket_count = 0
    total_ms = 0.0

    eval_model_name = "确定性规则引擎 (离线保底)"
    for index, case in enumerate(cases, start=1):
        started = time.perf_counter()
        flow = HotelOrchestrator(force_rules=not use_cloud_model)
        if use_cloud_model:
            eval_model_name = (
                f"云端模型: {flow.model.model}"
                if flow.model.client
                else f"云端模型不可用 (配置: {flow.model.model})"
            )
        for turn in case["turns"]:
            flow.process(turn)
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
        if progress_callback:
            progress_callback(index, len(cases), row)

    total = len(cases) or 1
    return {
        "rows": rows,
        "total": len(cases),
        "passed": sum(row["status"] == "PASS" for row in rows),
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
    print(f"Target: {report.get('model_name', 'Default')} | Cases: {report['total']} | Passed: {report['passed']} | Avg response: {report['average_response_ms']:.2f} ms")
    print(f"Intent Accuracy: {report['intent_accuracy']:.1%}")
    print(f"Slot Filling Rate: {report['slot_filling_rate']:.1%}")
    print(f"Ticket Precision: {report['ticket_precision']:.1%} | Ticket Recall: {report['ticket_recall']:.1%}")
    print("-" * 86)
    print(f"{'ID':<5} {'STATUS':<7} {'INTENT':<8} {'ROOM':<7} {'DEPT':<8} {'TICKET':<7} {'MODE':<25} NAME")
    for row in report["rows"]:
        print(f"{row['id']:<5} {row['status']:<7} {row['actual_intent']:<8} {row['room_no']:<7} {row['actual_dept']:<8} {str(row['ticket_created']):<7} {row.get('model_mode', ''):<25} {row['name']}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    use_cloud = "--cloud" in sys.argv
    print_report(run_evaluation(use_cloud_model=use_cloud))
