"""Unit tests for AegisStay 2.0 Self-Evolution, Night Audit, and Atomic Inventory."""

from __future__ import annotations

import unittest
from datetime import datetime

from hotel_core import (
    HotelOrchestrator,
    HotelState,
    HousekeepingAgent,
    apply_late_checkout,
    run_pms_night_audit,
)
from harness.self_evolution import (
    DynamicExemplarCurator,
    EvolutionDeltaRunner,
    HardCaseMiner,
)


class SelfEvolutionAndMemoryTests(unittest.TestCase):
    def test_dynamic_exemplar_curator_retrieves_block(self):
        curator = DynamicExemplarCurator()
        block = curator.retrieve_prompt_block("我是1102不对是1106送三块毛巾")
        self.assertIn("黄金参考示例", block)
        self.assertIn("1106", block)

    def test_hard_case_miner_loads_default_cases(self):
        miner = HardCaseMiner()
        cases = miner.load_cases()
        self.assertGreaterEqual(len(cases), 4)
        categories = {c["category"] for c in cases}
        self.assertIn("口误换房纠错", categories)
        self.assertIn("复合意图分流", categories)

    def test_evolution_delta_runner_cycle(self):
        runner = EvolutionDeltaRunner()
        report = runner.run_evolution_cycle()
        self.assertEqual(report["cycle_status"], "SUCCESS")
        self.assertIn("100.0%", report["baseline_pass_rate"])
        self.assertEqual(report["hard_cases_passed"], report["hard_cases_total"])
        self.assertGreaterEqual(len(report["diagnostics"]), 4)

    def test_night_audit_advances_date_and_marks_overdue(self):
        pms = {
            "1128": {
                "room_no": "1128",
                "guest_name": "张先生",
                "room_status": "在住",
                "checkout_date": "2026-10-01",
                "daily_voucher_used": 2,
            },
            "808": {
                "room_no": "808",
                "guest_name": "李女士",
                "room_status": "空房清洁中",
                "checkout_date": "2026-10-05",
            },
        }
        next_date, actions = run_pms_night_audit(pms, "2026-10-01")
        self.assertEqual(next_date, "2026-10-02")
        self.assertEqual(pms["1128"]["room_status"], "超时待结")
        self.assertEqual(pms["1128"]["daily_voucher_used"], 0)
        self.assertEqual(pms["808"]["room_status"], "空房可用")
        self.assertEqual(len(actions), 2)

    def test_late_checkout_next_booking_conflict_guard(self):
        profile = {
            "room_no": "1128",
            "room_status": "在住",
            "member_level": "携程黑金会员",
            "inventory_status": "充裕",
            "checkout_time": "12:00",
            "next_booking": {"expected_checkin": "14:15", "guest_name": "刘先生"},
        }
        res = apply_late_checkout("1128", "14:00", profile)
        self.assertEqual(res["status"], "需前台审核")
        self.assertIn("消杀保洁排期", res["reason"])
        self.assertFalse(res["updated_pms"])

    def test_atomic_inventory_borrowable_item_tracking(self):
        state = HotelState()
        housekeeping = HousekeepingAgent()
        ticket = housekeeping.dispatch("1106", "借一把雨伞", state)
        self.assertTrue(ticket.get("is_borrowable"))
        self.assertEqual(len(state.borrowed_items), 1)
        self.assertEqual(state.borrowed_items[0]["item"], "雨伞")
        self.assertEqual(state.borrowed_items[0]["status"], "借用在房")

    def test_sliding_window_memory_summary(self):
        flow = HotelOrchestrator(HotelState(), force_rules=True)
        # Push 14 conversation turns
        for i in range(7):
            flow.process(f"请问第 {i} 次早餐是几点？")
        self.assertIsNotNone(flow.state.rolling_summary)
        self.assertLessEqual(len(flow.state.messages), 8)


if __name__ == "__main__":
    unittest.main()
