"""Version 2: independently identified requests receive independent outcomes."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from agent_protocol import AgentConflict
from agent_runtime import SessionManager
from hotel_core import HotelOrchestrator, HousekeepingAgent


class CompoundRequestTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1", "AEGIS_SITE_PROFILE": "academic",
                                     "AEGIS_DB_PATH": "", "AEGIS_PMS_FILE": "", "AEGIS_KNOWLEDGE_FILE": ""})
        env.start()
        self.addCleanup(env.stop)

    def manager(self, **kwargs):
        manager = SessionManager(db_path=kwargs.pop("db_path", ""), **kwargs)
        self.addCleanup(manager.close)
        return manager

    def chat(self, manager, text, **kwargs):
        return manager.chat_result("guest", text, force_rules=True, **kwargs)

    def assert_three_outcomes(self, result):
        for term in ("矿泉水", "泡面", "空调"):
            self.assertIn(term, result.reply)
        self.assertIn("1瓶", result.reply.replace(" ", ""))
        self.assertIn("暂不支持", result.reply)
        self.assertNotRegex(result.reply, r"已(?:经)?(?:将空调|调低|调整温度)|温度合适")

    def test_user_water_noodles_temperature_has_three_explicit_outcomes(self):
        manager = self.manager()
        self.chat(manager, "1128")
        text = "再给我送点水吧，还要泡面，还要把空调温度调低一点"
        result = self.chat(manager, text)
        self.assert_three_outcomes(result)
        self.assertEqual(len(manager.shared_tickets), 1)
        ticket = manager.shared_tickets[0]
        self.assertEqual((ticket["inventory_item"], ticket["quantity"]), ("矿泉水", 1))
        self.assertEqual(ticket.get("source_request"), text)
        self.assertNotIn("泡面", ticket["item_detail"])
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 35)
        self.assertEqual(result.reply.count("张先生"), 1)

    def test_user_mineral_water_variant_does_not_lose_unknown_or_device(self):
        manager = self.manager()
        self.chat(manager, "1128")
        self.assert_three_outcomes(self.chat(manager, "再给我送点矿泉水吧，还要泡面，还要把空调温度调低一点"))
        self.assertEqual(len(manager.shared_tickets), 1)
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 35)

    def test_two_known_items_keep_separate_quantities(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水，再拿三条毛巾")
        self.assertEqual({t["inventory_item"]: t["quantity"] for t in manager.shared_tickets}, {"矿泉水": 2, "毛巾": 3})
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 34)
        self.assertEqual(manager.housekeeping.inventory["毛巾"], 21)

    def test_distinct_unknown_part_does_not_block_verified_water(self):
        manager = self.manager()
        result = self.chat(manager, "1128 送两瓶矿泉水，还要两桶泡面")
        self.assertIn("矿泉水", result.reply)
        self.assertIn("泡面", result.reply)
        self.assertEqual(len(manager.shared_tickets), 1)
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 34)

    def test_room_slot_keeps_all_three_requests(self):
        manager = self.manager()
        text = "给我送两瓶水，还要泡面，还要把空调温度调低一点"
        first = self.chat(manager, text)
        self.assertIn("房间号", first.reply)
        self.assertEqual(manager.shared_tickets, [])
        result = self.chat(manager, "1128")
        for term in ("矿泉水", "泡面", "空调"):
            self.assertIn(term, result.reply)
        self.assertEqual(manager.shared_tickets[0]["quantity"], 2)

    def test_split_execution_archives_only_one_assistant_reply(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水，还要泡面，还要调低空调温度")
        messages = manager.conversation_history("guest", limit=100)
        self.assertEqual(len(messages), 2)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertIn("泡面", messages[1]["content"])

    def test_device_control_cannot_be_invented_by_a_cloud_label(self):
        flow = HotelOrchestrator(force_rules=True)
        with patch.object(flow.reception, "understand", side_effect=AssertionError("Unsupported device operation is local")):
            reply = flow.process("帮我把空调温度调低一点")
        self.assertIn("暂不支持", reply)
        self.assertEqual(flow.state.tickets, [])

    def test_fault_is_not_dismissed_as_unsupported_remote_control(self):
        manager = self.manager()
        self.chat(manager, "1128 空调温度降不下来，而且冒烟了")
        self.assertEqual(manager.shared_tickets[-1]["category"], "工程报修")
        self.assertEqual(manager.shared_tickets[-1]["priority"], "高")

    def test_blowdryer_reminder_with_two_orders_never_creates_third(self):
        manager = self.manager()
        self.chat(manager, "1128需要一个吹风机")
        self.chat(manager, "我需要六个吹风机")
        before = deepcopy(manager.shared_tickets), deepcopy(manager.housekeeping.inventory)
        result = self.chat(manager, "吹风机快点，我有点着急赶时间")
        self.assertIn("多张", result.reply)
        self.assertEqual((manager.shared_tickets, manager.housekeeping.inventory), before)

    def test_blowdryer_reminder_with_one_order_records_only_reminder(self):
        manager = self.manager()
        self.chat(manager, "1128需要一个吹风机")
        self.chat(manager, "吹风机快点，我有点着急赶时间")
        self.assertEqual(len(manager.shared_tickets), 1)
        self.assertEqual(manager.shared_tickets[0].get("reminder_count"), 1)
        self.assertEqual(manager.housekeeping.inventory["吹风机"], 5)

    def test_child_item_change_inherits_its_own_quantity(self):
        manager = self.manager()
        self.chat(manager, "1128 送一瓶水，再拿三条毛巾")
        self.chat(manager, "不要水了，改成浴巾")
        self.assertEqual({t["inventory_item"]: t["quantity"] for t in manager.shared_tickets}, {"浴巾": 1, "毛巾": 3})
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 36)

    def test_cancel_one_child_keeps_the_other_reserved(self):
        manager = self.manager()
        self.chat(manager, "1128 送两瓶水，再拿三条毛巾")
        self.chat(manager, "水不用了")
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 36)
        self.assertEqual(manager.housekeeping.inventory["毛巾"], 21)
        self.assertEqual(manager.shared_tickets[1]["status"], "处理中")

    def test_compound_request_id_replay_reserves_once(self):
        manager = self.manager()
        text = "1128 送两瓶水，再拿三条毛巾"
        first = self.chat(manager, text, request_id="compound-once")
        second = self.chat(manager, text, request_id="compound-once")
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(len(manager.shared_tickets), 2)
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 34)

    def test_new_compounds_with_same_canonical_sku_are_not_false_success_replays(self):
        manager = self.manager()
        self.chat(manager, "1128")
        for text in ("再给我送点水吧，还要泡面，还要把空调温度调低一点",
                     "再给我送点矿泉水吧，还要泡面，还要把空调温度调低一点"):
            self.chat(manager, text)
        self.assertEqual(len(manager.shared_tickets), 2)
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 34)
        self.assertTrue(all(t["quantity"] == t["reserved_quantity"] == 1 for t in manager.shared_tickets))

    def test_blocked_compound_is_fenced_by_cancel_all(self):
        manager = self.manager(max_workers=3)
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = HousekeepingAgent.dispatch
        def dispatch(agent, room, detail, state):
            if "矿泉水" in detail:
                started.set()
                if not release.wait(4):
                    raise RuntimeError("test did not release compound")
            return original(agent, room, detail, state)
        with patch.object(HousekeepingAgent, "dispatch", dispatch):
            run = manager.submit("guest", "1128 送两瓶矿泉水，再拿三条毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            self.chat(manager, "全部都不要了")
            release.set()
            manager.wait("guest", run.run_id)
        self.assertEqual(manager.shared_tickets, [])
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 36)
        self.assertEqual(manager.housekeeping.inventory["毛巾"], 24)

    def test_compound_results_and_original_request_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "compound.sqlite3")
            first = SessionManager(db_path=db)
            try:
                self.chat(first, "1128 送两瓶水，还要泡面，还要调低空调温度")
            finally:
                first.close()
            second = SessionManager(db_path=db)
            try:
                self.assertEqual(len(second.shared_tickets), 1)
                self.assertIn("泡面", second.shared_tickets[0]["source_request"])
                self.assertEqual(second.housekeeping.inventory["矿泉水"], 34)
                self.assertEqual(len(second.conversation_history("guest")), 2)
            finally:
                second.close()

    def test_direct_department_still_rejects_unseparated_unknown_product(self):
        flow = HotelOrchestrator(force_rules=True)
        before = deepcopy(flow.housekeeping.inventory)
        with self.assertRaises(AgentConflict):
            flow.housekeeping.dispatch("1128", "送两瓶矿泉水和两桶泡面", flow.state)
        self.assertEqual(flow.housekeeping.inventory, before)

    def test_single_unknown_item_is_specific_even_if_model_says_other(self):
        flow = HotelOrchestrator(force_rules=True)
        flow._bind_room("1128")
        analysis, mode = flow.reception.understand("还要两桶泡面")
        analysis["intent"], analysis["service_intent"] = "其他", None
        with patch.object(flow.reception, "understand", return_value=(analysis, mode)):
            reply = flow.process("还要两桶泡面")
        self.assertIn("泡面", reply)
        self.assertIn("未找到", reply)
        self.assertEqual(flow.state.tickets, [])

    def test_shared_quantity_still_requires_clarification(self):
        manager = self.manager()
        self.chat(manager, "1128 矿泉水和毛巾各两份")
        self.assertEqual(manager.shared_tickets, [])
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 36)

    def test_multiple_room_targets_cannot_be_merged_into_one_room(self):
        manager = self.manager()
        result = self.chat(manager, "1128送两瓶矿泉水，1102送三条毛巾")
        self.assertEqual(manager.shared_tickets, [])
        self.assertIn("房间", result.reply)
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 36)

    def test_explicit_room_and_quantity_correction_is_not_a_second_request(self):
        manager = self.manager()
        self.chat(manager, "我是1102……不对我记错了，其实我是1106，送两块毛巾，不不不，三块毛巾")
        self.assertEqual(len(manager.shared_tickets), 1)
        self.assertEqual((manager.shared_tickets[0]["room_no"], manager.shared_tickets[0]["quantity"]), ("1106", 3))
        self.assertEqual(manager.housekeeping.inventory["毛巾"], 21)

    def test_pending_compound_is_not_replayed_over_cancellation_with_room(self):
        manager = self.manager()
        self.chat(manager, "送两瓶水，还要泡面")
        self.chat(manager, "1128 不要水了")
        self.assertEqual(manager.shared_tickets, [])
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 36)

    def test_pending_compound_is_replaced_by_explicit_new_room_request(self):
        manager = self.manager()
        self.chat(manager, "送两瓶水，还要泡面")
        self.chat(manager, "1128 送三条毛巾")
        self.assertEqual([(t["inventory_item"], t["quantity"]) for t in manager.shared_tickets], [("毛巾", 3)])
        self.assertEqual(manager.housekeeping.inventory["矿泉水"], 36)

    def test_power_and_lock_incidents_are_not_dropped_from_compound(self):
        for incident in ("停电了", "门锁打不开"):
            with self.subTest(incident=incident):
                manager = self.manager()
                result = self.chat(manager, "1128 送两瓶水，还要泡面，" + incident)
                repairs = [t for t in manager.shared_tickets if t["category"] == "工程报修"]
                self.assertEqual(len(repairs), 1)
                self.assertIn(incident, repairs[0]["item_detail"])
                self.assertEqual(repairs[0]["priority"], "高")
                self.assertIn("泡面", result.reply)

    def test_mixed_new_delivery_and_repair_cancel_requires_clarification(self):
        manager = self.manager()
        result = self.chat(manager, "1128 送两瓶水，取消空调报修")
        self.assertEqual(manager.shared_tickets, [])
        self.assertIn("确认", result.reply)

    def test_reminder_preserves_original_task_ownership(self):
        manager = self.manager()
        first = self.chat(manager, "1128 送两瓶水")
        self.chat(manager, "水还要多久，还要泡面")
        self.assertEqual(manager.shared_tickets[0]["task_id"], first.task_id)
        self.assertEqual(manager.shared_tickets[0].get("reminder_count"), 1)

    def test_ambiguous_items_are_not_silently_replaced_by_device_only_reply(self):
        manager = self.manager()
        result = self.chat(manager, "1128 我要矿泉水和毛巾，再把空调温度调低一点")
        self.assertIn("分别", result.reply)
        self.assertEqual(manager.shared_tickets, [])

    def test_surname_correction_does_not_delegate_verified_identity_to_cloud(self):
        manager = self.manager()
        self.chat(manager, "1128")
        self.chat(manager, "我不是张先生")
        flow = manager.get_session("guest")
        before = deepcopy(flow.state.guest_profile)
        with patch.object(flow.reception, "understand", side_effect=AssertionError("Name correction stays local")):
            reply = flow.process("我的意思是我姓高")
        self.assertIn("核验", reply)
        self.assertNotIn("您好，高先生", reply)
        self.assertEqual(flow.state.guest_profile, before)
        self.assertTrue(flow.state.neutral_address)

    def test_harmless_out_of_scope_reply_does_not_label_topic_sensitive(self):
        flow = HotelOrchestrator(force_rules=True)
        flow.reception.model.disabled = False
        flow.reception.model.client = object()
        flow.reception.model.chat = lambda *_args, **_kwargs: "这是敏感话题，我们不讨论敏感话题。"
        reply = flow.reception.generate_chitchat_reply("谈谈你对俄乌局势的看法")
        self.assertNotIn("敏感话题", reply)
        self.assertIn("酒店", reply)


if __name__ == "__main__":
    unittest.main()
