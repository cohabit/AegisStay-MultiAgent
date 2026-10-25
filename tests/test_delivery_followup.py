"""Synthetic regressions for drinking-water boundaries and order reminders."""
from __future__ import annotations

import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from agent_protocol import normalize_delivery_text, parse_delivery_command
from agent_runtime import SessionManager


class DeliveryFollowupTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1", "AEGIS_SITE_PROFILE": "academic",
                                     "AEGIS_DB_PATH": "", "AEGIS_PMS_FILE": "", "AEGIS_KNOWLEDGE_FILE": ""})
        env.start()
        self.addCleanup(env.stop)

    def manager(self, **kwargs):
        manager = SessionManager(db_path=kwargs.pop("db_path", ""), **kwargs)
        self.addCleanup(manager.close)
        return manager

    def chat(self, manager, text, session="guest", **kwargs):
        return manager.chat_result(session, text, force_rules=True, **kwargs)

    def test_user_compound_reports_only_the_real_unknown_item(self):
        text = "我还要两瓶水两桶泡面，很饿，快点送"
        command = parse_delivery_command(text)
        self.assertIn("矿泉水", command.references)
        self.assertEqual(command.unknown_items, ("泡面",))
        manager = self.manager()
        self.chat(manager, "1128")
        before = deepcopy(manager.housekeeping.inventory)
        result = self.chat(manager, text)
        self.assertIn("未找到泡面", result.reply)
        self.assertNotIn("水、", result.reply)
        self.assertNotIn("快点送", result.reply)
        self.assertEqual(manager.shared_tickets, [])
        self.assertEqual(manager.housekeeping.inventory, before)

    def test_adjacent_quantities_and_conjunctions_normalize_water(self):
        for text in ("两瓶水两桶泡面", "给我两瓶水再拿一桶泡面", "我要两瓶水和两桶泡面"):
            with self.subTest(text=text):
                command = parse_delivery_command(text)
                self.assertIn("矿泉水", command.references)
                self.assertEqual(command.unknown_items, ("泡面",))

    def test_pacing_and_hunger_are_not_stock_items(self):
        for text in ("送两瓶水，快点送", "给我送两条毛巾，很饿，尽快送来",
                     "送两条浴巾，麻烦快一点送过来", "送两瓶水，很口渴，送快点", "送两瓶水，不用催"):
            with self.subTest(text=text):
                self.assertEqual(parse_delivery_command(text).unknown_items, ())

    def test_fast_delivery_does_not_hide_unknown_products(self):
        for text in ("快点送两罐红牛", "送两瓶水，快点送两桶泡面", "麻烦尽快送一盒饼干"):
            with self.subTest(text=text):
                command = parse_delivery_command(text)
                expected = "红牛" if "红牛" in text else "泡面" if "泡面" in text else "饼干"
                self.assertEqual(command.unknown_items, (expected,))

    def test_plumbing_and_toiletry_water_is_not_drinking_water(self):
        for text in ("快点把热水修好", "快点把漏水处理好", "快点把水管修好", "送两瓶洗发水", "洗澡的水不用了"):
            with self.subTest(text=text):
                self.assertNotIn("矿泉水", normalize_delivery_text(text))
                self.assertNotIn("矿泉水", parse_delivery_command(text).references)

    def test_ba_water_reminder_is_a_known_noun_not_an_unknown_phrase(self):
        command = parse_delivery_command("快点把水送来，我很口渴")
        self.assertEqual(command.item, "矿泉水")
        self.assertEqual(command.unknown_items, ())

    def test_reminder_targets_water_preserving_other_tickets_and_stock(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        self.chat(manager, "送两条毛巾")
        water, towels = manager.shared_tickets
        towels_before = deepcopy(towels)
        inventory = deepcopy(manager.housekeeping.inventory)
        result = self.chat(manager, "快点把水送来，我很口渴")
        self.assertIn("催送", result.reply)
        self.assertEqual(len(manager.shared_tickets), 2)
        self.assertEqual(water.get("reminder_count"), 1)
        self.assertTrue(water.get("last_reminded_at"))
        self.assertEqual(water["priority"], "中")
        self.assertEqual(towels, towels_before)
        self.assertEqual(manager.housekeeping.inventory, inventory)
        self.assertNotRegex(result.reply, r"已(?:出发|派出|送达)|工作人员已收到")

    def test_repeat_reminder_is_recorded_without_duplicate_reservation(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        ticket_id = manager.shared_tickets[0]["ticket_id"]
        for text in ("快点把水送来，我很口渴", "矿泉水怎么还没送到"):
            self.chat(manager, text)
        self.assertEqual(len(manager.shared_tickets), 1)
        self.assertEqual(manager.shared_tickets[0]["ticket_id"], ticket_id)
        self.assertEqual(manager.shared_tickets[0].get("reminder_count"), 2)
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 34)

    def test_more_water_with_quantity_remains_a_new_request(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        self.chat(manager, "我还要两瓶水，很口渴，快点送")
        self.assertEqual(len(manager.shared_tickets), 2)
        self.assertEqual([ticket["quantity"] for ticket in manager.shared_tickets], [2, 2])
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 32)
        self.assertTrue(all(not ticket.get("reminder_count") for ticket in manager.shared_tickets))

    def test_new_quantity_with_cui_phrase_is_not_swallowed_by_legacy_pacing(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        self.chat(manager, "再送两瓶水，催一下")
        self.assertEqual(len(manager.shared_tickets), 2)
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 32)

    def test_unknown_addition_with_existing_order_progress_is_not_dropped(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        before = deepcopy(manager.shared_tickets), deepcopy(manager.housekeeping.inventory)
        result = self.chat(manager, "矿泉水还没送到，快点送两罐红牛")
        self.assertIn("未找到红牛", result.reply)
        # CR-01 v2 allows a source-grounded reminder while rejecting the separate
        # unknown item; it must still never create a second water delivery.
        before[0][0]["reminder_count"] = 1
        before[0][0]["last_reminded_at"] = manager.shared_tickets[0]["last_reminded_at"]
        self.assertEqual((manager.shared_tickets, manager.housekeeping.inventory), before)

    def test_named_progress_question_does_not_choose_latest_towel_order(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        self.chat(manager, "送两条毛巾")
        result = self.chat(manager, "水还要多久")
        self.assertIn("矿泉水", result.reply)
        self.assertNotIn("毛巾", result.reply)
        self.assertEqual(manager.shared_tickets[0].get("reminder_count"), 1)
        self.assertIsNone(manager.shared_tickets[1].get("reminder_count"))

    def test_negated_reminder_does_not_change_order_or_inventory(self):
        for text in ("矿泉水不用催", "水不需要催一下", "矿泉水别催了"):
            with self.subTest(text=text):
                manager = self.manager()
                self.chat(manager, "1128 送两瓶水")
                before = deepcopy(manager.shared_tickets), deepcopy(manager.housekeeping.inventory)
                self.chat(manager, text)
                self.assertEqual((manager.shared_tickets, manager.housekeeping.inventory), before)

    def test_quantityless_cui_targets_only_own_order_even_if_other_room_exists(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水", session="one")
        self.chat(manager, "1102 送两条毛巾", session="two")
        other = deepcopy(manager.shared_tickets[1])
        result = self.chat(manager, "催一下", session="one")
        self.assertIn("催送", result.reply)
        self.assertEqual(manager.shared_tickets[0].get("reminder_count"), 1)
        self.assertEqual(manager.shared_tickets[1], other)

    def test_negative_speed_modifier_keeps_new_order_at_normal_priority(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水，不用快点送")
        self.assertEqual(len(manager.shared_tickets), 1)
        self.assertEqual(manager.shared_tickets[0]["priority"], "低")

    def test_two_water_orders_require_clarification(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        self.chat(manager, "再送一瓶水")
        tickets, stock = deepcopy(manager.shared_tickets), deepcopy(manager.housekeeping.inventory)
        result = self.chat(manager, "快点把水送来，我很口渴")
        self.assertIn("多张", result.reply)
        self.assertEqual(manager.shared_tickets, tickets)
        self.assertEqual(manager.housekeeping.inventory, stock)

    def test_reminder_without_order_does_not_create_phantom_order(self):
        manager = self.manager()
        self.chat(manager, "1128")
        result = self.chat(manager, "快点把水送来，我很口渴")
        self.assertIn("未找到", result.reply)
        self.assertIn("数量", result.reply)
        self.assertEqual(manager.shared_tickets, [])
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 36)

    def test_cancelled_order_cannot_be_revived_by_reminder(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        self.chat(manager, "水不用了")
        before = deepcopy(manager.shared_tickets)
        result = self.chat(manager, "快点把水送来，我很口渴")
        self.assertIn("未找到", result.reply)
        self.assertEqual(manager.shared_tickets, before)
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 36)

    def test_completed_order_cannot_be_revived_by_reminder(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        manager.complete_ticket(manager.shared_tickets[0]["ticket_id"])
        before = deepcopy(manager.shared_tickets)
        result = self.chat(manager, "快点把水送来，我很口渴")
        self.assertIn("未找到", result.reply)
        self.assertEqual(manager.shared_tickets, before)
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 34)

    def test_reminder_never_reduces_existing_high_priority(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        manager.shared_tickets[0]["priority"] = "高"
        self.chat(manager, "快点把水送来，我很口渴")
        self.assertEqual(manager.shared_tickets[0]["priority"], "高")
        self.assertEqual(manager.shared_tickets[0].get("reminder_count"), 1)

    def test_shortage_reminder_keeps_unconfirmed_delivery_status(self):
        manager = self.manager()
        self.chat(manager, "1128 送7个吹风机")
        before = deepcopy(manager.housekeeping.inventory)
        result = self.chat(manager, "快点把吹风机送来")
        self.assertIn("库存", result.reply)
        self.assertIn("未确认派送", result.reply)
        self.assertEqual(len(manager.shared_tickets), 1)
        self.assertEqual(manager.shared_tickets[0]["reserved_quantity"], 0)
        self.assertEqual(manager.shared_tickets[0]["delivery_status"], "待库存协调")
        self.assertEqual(manager.housekeeping.inventory, before)

    def test_delivery_followup_cannot_swallow_smoke_emergency(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        self.chat(manager, "快点把水送来，房间空调冒烟了")
        self.assertEqual(len(manager.shared_tickets), 2)
        self.assertEqual(manager.shared_tickets[-1]["category"], "工程报修")
        self.assertEqual(manager.shared_tickets[-1]["priority"], "高")
        self.assertFalse(manager.shared_tickets[0].get("reminder_count"))

    def test_followup_keeps_facility_answer_in_same_turn(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        result = self.chat(manager, "水还要多久，健身房几点关门")
        self.assertIn("催送", result.reply)
        self.assertIn("健身房", result.reply)
        self.assertIn("核验", result.reply)
        self.assertEqual(len(manager.shared_tickets), 1)

    def test_user_sequence_keeps_staff_confirmed_replacement_and_single_water_order(self):
        manager = self.manager()
        self.assertIn("房间号", self.chat(manager, "送一块浴巾给我").reply)
        self.chat(manager, "1128")
        original = manager.shared_tickets[0]["ticket_id"]
        manager.dispatch_delivery(original)
        reply = self.chat(manager, "算了我不要浴巾了，你给我送两块毛巾吧").reply
        self.assertIn("尚未确认取消成功", reply)
        manager.confirm_delivery_change(original, approved=True)
        self.assertEqual(manager.shared_tickets[0]["inventory_item"], "毛巾")
        self.assertEqual(manager.shared_tickets[0]["quantity"], 2)
        self.chat(manager, "我还要两瓶水两桶泡面，很饿，快点送")
        self.assertEqual(len(manager.shared_tickets), 1)
        self.chat(manager, "我还要两瓶水")
        self.chat(manager, "我还要两桶泡面")
        result = self.chat(manager, "快点把水送来，我很口渴")
        self.assertIn("催送", result.reply)
        self.assertEqual(len(manager.shared_tickets), 2)
        self.assertEqual(manager.shared_tickets[0]["ticket_id"], original)
        self.assertEqual(manager.shared_tickets[1].get("reminder_count"), 1)
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 34)
        self.assertEqual(manager.housekeeping.inventory["浴巾"], 24)
        self.assertEqual(manager.housekeeping.inventory["毛巾"], 22)

    def test_same_room_different_session_cannot_remind_someone_elses_order(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水", session="one")
        self.chat(manager, "1128", session="two")
        before = deepcopy(manager.shared_tickets)
        result = self.chat(manager, "快点把水送来，我很口渴", session="two")
        self.assertIn("未找到", result.reply)
        self.assertEqual(manager.shared_tickets, before)

    def test_reminder_request_id_replay_is_idempotent(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水")
        first = self.chat(manager, "快点把水送来", request_id="reminder-once")
        second = self.chat(manager, "快点把水送来", request_id="reminder-once")
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(manager.shared_tickets[0].get("reminder_count"), 1)

    def test_reminder_record_survives_sqlite_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "reminder.sqlite3")
            first = SessionManager(db_path=path)
            try:
                self.chat(first, "1128 送两瓶水")
                self.chat(first, "快点把水送来，我很口渴")
            finally:
                first.close()
            second = self.manager(db_path=path)
            try:
                self.assertEqual(second.shared_tickets[0].get("reminder_count"), 1)
                self.assertEqual(second.shared_tickets[0]["priority"], "中")
                self.chat(second, "快点把水送来，我很口渴")
                self.assertEqual(second.shared_tickets[0].get("reminder_count"), 2)
                self.assertEqual(second.housekeeping.inventory["矿泉水"], 34)
            finally:
                second.close()


if __name__ == "__main__":
    unittest.main()
