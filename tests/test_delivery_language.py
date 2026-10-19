"""Offline regressions for colloquial item references and safe order targeting."""
from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from agent_protocol import normalize_delivery_text, parse_delivery_command
from agent_runtime import SessionManager
from hotel_core import HotelOrchestrator, HousekeepingAgent


class DeliveryLanguageTests(unittest.TestCase):
    def test_water_cancel_variants_reference_the_same_item(self):
        for text in (
            "水不用了", "水不需要了", "水不要了", "水不用送了", "水先不用送了",
            "不要水了", "取消水", "那瓶水不用了", "饮用水不用了", "纯净水不需要了",
            "矿泉水不需要了", "矿泉水不用送了", "1128 水不用了",
        ):
            with self.subTest(text=text):
                command = parse_delivery_command(text)
                self.assertEqual(command.action, "cancel")
                self.assertEqual(command.references, ("矿泉水",))
                self.assertFalse(command.unresolved_target)

    def test_water_alias_keeps_quantity_and_replacement_roles(self):
        create = parse_delivery_command("送两瓶水")
        self.assertEqual((create.action, create.item, create.quantity), ("create", "矿泉水", 2))
        replace = parse_delivery_command("不要水了，改成浴巾")
        self.assertEqual((replace.action, replace.item), ("replace", "浴巾"))
        self.assertEqual(set(replace.references), {"矿泉水", "浴巾"})

    def test_negated_temperature_modifier_is_not_a_cancellation(self):
        command = parse_delivery_command("矿泉水不要太凉")
        self.assertEqual(command.action, "create")
        self.assertEqual(command.item, "矿泉水")

    def test_other_water_terms_are_not_normalized_to_drinking_water(self):
        for text in ("热水不用了", "洗澡的水不用了", "水管不用了", "漏水不用了", "洗发水不用了"):
            with self.subTest(text=text):
                self.assertEqual(normalize_delivery_text(text), text)
                self.assertNotIn("矿泉水", parse_delivery_command(text).references)

    def test_screenshot_followup_cancels_only_water_and_preserves_task_id(self):
        manager = SessionManager()
        self.addCleanup(manager.close)
        manager.chat_result("guest", "1128 送两条毛巾", force_rules=True)
        water = manager.chat_result("guest", "送两瓶矿泉水", force_rules=True)
        ambiguous = manager.chat_result("guest", "不用了", force_rules=True)
        self.assertIn("先说明", ambiguous.reply)
        result = manager.chat_result("guest", "水不用了", force_rules=True)
        by_item = {ticket["inventory_item"]: ticket for ticket in manager.shared_tickets}
        self.assertEqual(result.task_id, water.task_id)
        self.assertEqual(result.version, 2)
        self.assertEqual(by_item["矿泉水"]["status"], "已取消")
        self.assertEqual(by_item["毛巾"]["status"], "处理中")
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 36)
        self.assertEqual(manager.housekeeping.inventory["毛巾"], 22)
        self.assertIn("已取消", result.reply)

    def test_alias_cancel_cannot_supersede_an_unrelated_running_delivery(self):
        manager = SessionManager(max_workers=3)
        self.addCleanup(manager.close)
        water = manager.chat_result("guest", "1128 送两瓶矿泉水", force_rules=True)
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = HousekeepingAgent.dispatch

        def dispatch(agent, room, detail, state):
            if "毛巾" in detail:
                started.set()
                if not release.wait(4):
                    raise RuntimeError("test did not release housekeeping")
            return original(agent, room, detail, state)

        with patch.object(HousekeepingAgent, "dispatch", dispatch):
            towels = manager.submit("guest", "送两条毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            cancel = manager.submit("guest", "水不用了", force_rules=True)
            self.assertFalse(towels.cancelled.is_set())
            self.assertEqual(cancel.task_id, water.task_id)
            release.set()
            manager.wait("guest", towels.run_id)
            manager.wait("guest", cancel.run_id)
        by_item = {ticket["inventory_item"]: ticket for ticket in manager.shared_tickets}
        self.assertEqual(by_item["毛巾"]["status"], "处理中")
        self.assertEqual(by_item["矿泉水"]["status"], "已取消")

    def test_unknown_water_reference_does_not_cancel_existing_drinking_water(self):
        for message in ("热水不用了", "洗澡的水不用了", "水管不用了", "洗发水不用了"):
            with self.subTest(message=message):
                manager = SessionManager()
                try:
                    manager.chat_result("guest", "1128 送两瓶矿泉水", force_rules=True)
                    result = manager.chat_result("guest", message, force_rules=True)
                    self.assertEqual(len(manager.shared_tickets), 1)
                    self.assertEqual(manager.shared_tickets[0]["status"], "处理中")
                    self.assertEqual(manager.housekeeping.inventory["矿泉水"], 34)
                    self.assertTrue(any(step["event"] == "delivery_target_required" for step in result.flow.state.last_trace))
                finally:
                    manager.close()

    def test_replacing_water_alias_updates_only_its_original_order(self):
        flow = HotelOrchestrator(force_rules=True)
        flow.process("1128 送两条毛巾")
        flow.process("送两瓶矿泉水")
        water_id = flow.state.tickets[-1]["ticket_id"]
        flow.process("不要水了，改成浴巾")
        self.assertEqual(len(flow.state.tickets), 2)
        self.assertEqual(flow.state.tickets[-1]["ticket_id"], water_id)
        self.assertEqual((flow.state.tickets[-1]["inventory_item"], flow.state.tickets[-1]["quantity"]), ("浴巾", 2))
        self.assertEqual(flow.state.tickets[0]["inventory_item"], "毛巾")
        self.assertEqual(flow.housekeeping.inventory["矿泉水"], 36)
        self.assertEqual(flow.housekeeping.inventory["浴巾"], 22)


if __name__ == "__main__":
    unittest.main()
