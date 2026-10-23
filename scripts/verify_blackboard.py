"""Execute three real, synthetic Blackboard scenarios and assert their state/Trace."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_release import (VerificationError, envelope, isolated_runtime, is_offline_mode,
                                    load_report, utc_now, write_json)


def _snapshot(flow, reply: str) -> dict:
    state = flow.state
    return deepcopy({"reply": reply, "room_no": state.room_no, "intent": state.intent,
                     "sentiment": state.sentiment, "active_agent": state.active_agent,
                     "model_mode": state.model_mode, "guest_profile": state.guest_profile,
                     "tickets": state.tickets, "late_checkout": state.late_checkout,
                     "checkout_requests": state.checkout_requests, "trace": state.last_trace,
                     "checkout_time": state.pms_rooms.get(state.room_no, {}).get("checkout_time")})


def collect_scenarios() -> dict:
    # Imported after the child guard hides local configuration and isolates SQLite.
    from hotel_core import HotelOrchestrator, HotelState

    compound = HotelOrchestrator(HotelState(), force_rules=True)
    reply = compound.process("1128 你们早餐几点结束？另外我是携程黑金，能帮我两点退房吗？")
    compound_result = _snapshot(compound, reply)
    angry = HotelOrchestrator(HotelState(), force_rules=True)
    angry_reply = angry.process("1128 房间太差了，我非常生气，要投诉，马上让经理处理")
    angry_result = _snapshot(angry, angry_reply)
    manual = HotelOrchestrator(HotelState(), force_rules=True)
    manual_reply = manual.process("808 能两点退房吗？")
    before = _snapshot(manual, manual_reply)
    manual.review_checkout("808", "14:00", True)
    after = _snapshot(manual, manual.state.messages[-1]["content"])
    return {"compound_faq_checkout": compound_result, "angry_escalation": angry_result,
            "manual_checkout_approval": {"before": before, "after": after}}


def scenario_assertions(scenarios: dict) -> dict[str, bool]:
    """Recompute gates from snapshots; a forged assertions=true flag is ignored."""
    try:
        compound = scenarios["compound_faq_checkout"]
        angry = scenarios["angry_escalation"]
        before = scenarios["manual_checkout_approval"]["before"]
        after = scenarios["manual_checkout_approval"]["after"]

        def trace_has(snapshot, agent, event, detail=""):
            return any(row.get("agent") == agent and row.get("event") == event
                       and isinstance(row.get("detail"), str) and row["detail"]
                       and detail in row["detail"] for row in snapshot["trace"])

        return {
            "all_scenarios_offline": all(is_offline_mode(s["model_mode"]) for s in (compound, angry, before, after)),
            "compound_faq_preserved": "07:00–10:00" in compound["reply"] and "二楼餐厅" in compound["reply"],
            "compound_room_bound": compound["room_no"] == "1128",
            "compound_service_preserved": compound["intent"] == "延迟退房" and "已批准" in compound["reply"],
            "compound_pms_updated": compound["checkout_time"] == "14:00" and compound["guest_profile"]["checkout_time"] == "14:00",
            "compound_request_approved": compound["late_checkout"]["status"] == "已批准" and compound["late_checkout"]["updated_pms"] is True,
            "compound_faq_trace": trace_has(compound, "ReceptionAgent", "知识检索", "hotel_knowledge.json"),
            "compound_checkout_trace": trace_has(compound, "ReceptionAgent", "apply_late_checkout", "14:00 → 已批准"),
            "angry_sentiment": angry["sentiment"] == "愤怒",
            "angry_manager": angry["intent"] == "客诉紧急" and angry["active_agent"] == "ManagerEscalationAgent",
            "angry_one_high_red_ticket": len(angry["tickets"]) == 1 and angry["tickets"][0]["priority"] == "高" and angry["tickets"][0]["risk_level"] == "红色",
            "angry_manager_department": angry["tickets"][0]["assigned_dept"] == "大堂经理" and angry["tickets"][0]["room_no"] == "1128",
            "angry_validation_trace": trace_has(angry, "Supervisor", "proposal_validated"),
            "angry_dispatch_trace": trace_has(angry, "ManagerEscalationAgent", "vertical_agent_dispatch", "大堂经理 / 高"),
            "manual_before_stays_at_noon": before["room_no"] == "808" and before["checkout_time"] == "12:00" and before["guest_profile"]["checkout_time"] == "12:00",
            "manual_before_requires_review": before["late_checkout"]["status"] == "需前台审核" and before["late_checkout"]["updated_pms"] is False and "人工审核" in before["reply"],
            "manual_pending_trace": trace_has(before, "ReceptionAgent", "apply_late_checkout", "14:00 → 需前台审核"),
            "manual_after_pms_updated": after["room_no"] == "808" and after["checkout_time"] == "14:00" and after["guest_profile"]["checkout_time"] == "14:00",
            "manual_after_approved": after["late_checkout"]["status"] == "已批准" and after["late_checkout"]["updated_pms"] is True and "前台通知" in after["reply"],
            "manual_approval_trace": trace_has(after, "PMS", "人工审批", "房间 808 / 14:00 / 已批准"),
            "manual_request_identity_preserved": bool(before["late_checkout"]["request_id"]) and before["late_checkout"]["request_id"] == after["late_checkout"]["request_id"],
        }
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise VerificationError("Blackboard scenario snapshots are incomplete") from error


def validate_blackboard_report(report: dict) -> dict:
    if set(report.get("scenarios", {})) != {"compound_faq_checkout", "angry_escalation", "manual_checkout_approval"}:
        raise VerificationError("Blackboard must execute all three required scenarios")
    checks = scenario_assertions(report["scenarios"])
    failed = [name for name, passed in checks.items() if passed is not True]
    if failed:
        raise VerificationError("Blackboard state/Trace assertions failed: " + ", ".join(failed))
    if report.get("assertions") != checks:
        raise VerificationError("Blackboard assertion flags disagree with its snapshots")
    return {"scenario_count": 3, "assertions": len(checks), "passed": len(checks), "failed": []}


def worker(output: Path, run_id: str) -> int:
    if os.environ.get("AEGIS_VERIFY_GUARD_INSTALLED") != "1":
        raise VerificationError("Blackboard worker requires isolated startup guard")
    report = envelope("blackboard", run_id, status="FAIL")
    try:
        report["scenarios"] = collect_scenarios()
        report["assertions"] = scenario_assertions(report["scenarios"])
        report["metrics"] = validate_blackboard_report(report)
        report["status"] = "PASS"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()
    write_json(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["status"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new JSON output file")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    run_id = args.run_id or uuid.uuid4().hex
    output = args.output.resolve()
    try:
        if output.exists():
            raise VerificationError("Blackboard output already exists; refusing stale evidence")
        output.parent.mkdir(parents=True, exist_ok=True)
        if args._worker:
            return worker(output, run_id)
        with isolated_runtime(run_id) as (env, temporary, _cleanup):
            receipt = temporary / "blackboard.guard.json"
            env.update(AEGIS_VERIFY_KIND="blackboard", AEGIS_VERIFY_GUARD_PATH=str(receipt))
            started = utc_now()
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()),
                                     "--_worker", "--output", str(output), "--run-id", run_id],
                                    cwd=ROOT, env=env, stdin=subprocess.DEVNULL, check=False)
            load_report(receipt, kind="safety-guard", run_id=run_id, not_before=started)
            report = load_report(output, kind="blackboard", run_id=run_id, not_before=started)
            validate_blackboard_report(report)
            return result.returncode if report.get("status") == "PASS" else 1
    except (OSError, VerificationError) as error:
        print(f"Blackboard verification refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
