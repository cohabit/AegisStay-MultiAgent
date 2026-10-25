"""Guest transcript retention, catalog validation and explicit batch cancellation."""
from __future__ import annotations

import threading
import tempfile
import unittest
import uuid
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from agent_runtime import SessionManager
from hotel_core import EngineeringAgent, HotelOrchestrator, HousekeepingAgent, OpenAICompatibleClient


class TranscriptRetentionTests(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager(db_path="")
        self.addCleanup(self.manager.close)

    def test_model_compaction_does_not_delete_guest_transcript(self):
        for i in range(8):
            self.manager.chat_result("guest", f"记录{i}，你是谁？", force_rules=True)
        self.assertLess(len(self.manager.get_session("guest").state.messages), 16)
        history = self.manager.conversation_history("guest", limit=100)
        self.assertEqual(len(history), 16)
        self.assertEqual(history[0]["content"], "记录0，你是谁？")
        self.assertEqual(len({m["message_id"] for m in history}), 16)

    def test_memory_archive_pages_are_exclusive_ordered_and_private(self):
        for i in range(56):
            self.manager.chat_result("guest", f"记录{i}，你是谁？", force_rules=True)
        self.manager.chat_result("other", "另一位住客的私聊，你是谁？", force_rules=True)
        pages, before = [], None
        while True:
            page = self.manager.conversation_history("guest", before=before, limit=30)
            if not page:
                break
            self.assertTrue(all("sequence" in m and "message_id" in m for m in page), "Archive needs stable cursors and identities")
            pages = page + pages
            before = page[0]["sequence"]
        self.assertEqual(len(pages), 112)
        self.assertEqual(len({m["message_id"] for m in pages}), 112)
        self.assertEqual([m["sequence"] for m in pages], sorted(m["sequence"] for m in pages))
        self.assertEqual(pages[0]["content"], "记录0，你是谁？")
        self.assertNotIn("另一位住客", str(pages))
        pages[-1]["content"] = "不能改写归档"
        self.assertNotEqual(self.manager.conversation_history("guest")[-1]["content"], "不能改写归档")
        self.assertEqual(self.manager.conversation_history("guest", before=0), [])

    def test_reset_removes_only_the_current_sessions_archive(self):
        self.manager.chat_result("guest", "你的身份？", force_rules=True)
        self.manager.chat_result("other", "你是谁？", force_rules=True)
        self.manager.reset("guest")
        self.assertEqual(self.manager.conversation_history("guest"), [])
        self.assertEqual(len(self.manager.conversation_history("other")), 2)

    def test_durable_archive_and_neutral_address_survive_restart(self):
        path = Path(tempfile.gettempdir()) / ("aegis-transcript-" + uuid.uuid4().hex + ".sqlite3")
        manager = SessionManager(db_path=str(path))
        try:
            for i in range(56):
                manager.chat_result("guest", f"记录{i}，你是谁？", force_rules=True)
            manager.chat_result("guest", "1128 送1瓶矿泉水", force_rules=True)
            manager.chat_result("guest", "我不是张先生", force_rules=True)
            manager.close()
            manager = SessionManager(db_path=str(path))
            newest = manager.conversation_history("guest", limit=100)
            older = manager.conversation_history("guest", before=newest[0]["sequence"], limit=100)
            self.assertEqual(len(older + newest), 116)
            self.assertEqual((older + newest)[0]["content"], "记录0，你是谁？")
            result = manager.chat_result("guest", "你是谁？", force_rules=True)
            self.assertFalse(result.reply.startswith("张先生"))
            self.assertEqual(len(manager.shared_tickets), 1)
        finally:
            manager.close()
            for suffix in ("", "-wal", "-shm", ".writer.lock"):
                Path(str(path) + suffix).unlink(missing_ok=True)


class DeliveryReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager(db_path="", max_workers=4)
        self.addCleanup(self.manager.close)

    def chat(self, message, session="guest", **kwargs):
        return self.manager.chat_result(session, message, force_rules=True, **kwargs)

    def test_explicit_all_cancel_after_faq_releases_every_own_order_once(self):
        for text in ("全部都不要了", "全部取消", "所有送物都取消", "全都不要了"):
            with self.subTest(text=text):
                manager = SessionManager(db_path="")
                try:
                    orders = [manager.chat_result("guest", message, force_rules=True) for message in
                              ("1128 送两条毛巾", "送1瓶矿泉水", "送3个吹风机")]
                    original_tasks = [t["task_id"] for t in manager.shared_tickets]
                    manager.chat_result("guest", "早餐几点？", force_rules=True)
                    result = manager.chat_result("guest", text, force_rules=True)
                    self.assertTrue(all(t["status"] == "已取消" for t in manager.shared_tickets))
                    self.assertEqual([t["task_id"] for t in manager.shared_tickets], original_tasks)
                    self.assertEqual([manager.housekeeping.inventory[k] for k in ("毛巾", "矿泉水", "吹风机")], [24, 36, 6])
                    self.assertIn("取消", result.reply)
                    self.assertTrue(all(manager.get_session("guest").state.tasks[o.task_id]["status"] == "cancelled" for o in orders))
                    manager.chat_result("guest", text, force_rules=True)
                    self.assertEqual([manager.housekeeping.inventory[k] for k in ("毛巾", "矿泉水", "吹风机")], [24, 36, 6])
                finally:
                    manager.close()

    def test_all_cancel_stops_running_and_queued_delivery_before_old_worker_returns(self):
        # A cancel must remain usable even when both the worker and queue are full.
        self.manager.close()
        self.manager = SessionManager(db_path="", max_workers=1, max_pending=2)
        self.addCleanup(self.manager.close)
        self.chat("1128 送1瓶矿泉水")
        self.chat("1106 送1瓶矿泉水", session="other")
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = HousekeepingAgent.dispatch

        def slow(agent, room, detail, state):
            if "毛巾" in detail:
                started.set()
                if not release.wait(5):
                    raise RuntimeError("unreleased worker")
            return original(agent, room, detail, state)

        with patch.object(HousekeepingAgent, "dispatch", slow):
            running = self.manager.submit("guest", "送2条毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            queued = self.manager.submit("guest", "送3个吹风机", force_rules=True)
            cancel = self.manager.submit("guest", "全部都不要了", force_rules=True)
            result = self.manager.wait("guest", cancel.run_id, timeout=2)
            self.assertTrue(running.cancelled.is_set())
            self.assertTrue(queued.cancelled.is_set())
            self.assertIn("取消", result.reply)
            release.set()
            self.manager.wait("guest", running.run_id)
            self.manager.wait("guest", queued.run_id)
        self.assertEqual(len(self.manager.shared_tickets), 2)
        own = [t for t in self.manager.shared_tickets if t["owner_session_id"] == "guest"]
        other = [t for t in self.manager.shared_tickets if t["owner_session_id"] == "other"]
        self.assertEqual(own[0]["status"], "已取消")
        self.assertEqual(other[0]["status"], "处理中")
        self.assertEqual([self.manager.housekeeping.inventory[k] for k in ("毛巾", "矿泉水", "吹风机")], [24, 35, 6])

    def test_batch_cancel_includes_delivery_identified_by_model_after_queuing(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = HousekeepingAgent.dispatch

        def slow(agent, room, detail, state):
            if "矿泉水" in detail:
                started.set()
                release.wait(4)
            return original(agent, room, detail, state)

        cloud = {"intent": "送物", "room_no": None, "sentiment": "正常"}
        with patch.object(OpenAICompatibleClient, "json_extract", return_value=cloud), patch.object(HousekeepingAgent, "dispatch", slow):
            running = self.manager.submit("guest", "1128 送1瓶矿泉水", force_rules=True)
            self.assertTrue(started.wait(2))
            queued = self.manager.submit("guest", "两条毛巾", force_rules=True)
            cancel = self.manager.submit("guest", "全部都不要了", force_rules=True)
            self.manager.wait("guest", cancel.run_id, timeout=2)
            try:
                self.assertTrue(queued.cancelled.is_set(), "An initial non-service label cannot exclude an explicit delivery target")
            finally:
                release.set()
            self.manager.wait("guest", running.run_id)
            self.manager.wait("guest", queued.run_id)
        self.assertEqual(self.manager.shared_tickets, [])
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)
        self.assertEqual(self.manager.housekeeping.inventory["矿泉水"], 36)

    def test_batch_cancel_bypasses_blocked_unrelated_business_worker(self):
        self.manager.close()
        self.manager = SessionManager(db_path="", max_workers=1, max_pending=1)
        self.addCleanup(self.manager.close)
        self.chat("1128 送1瓶矿泉水")
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = EngineeringAgent.diagnose

        def slow(agent, room, detail, state, sentiment="正常"):
            started.set()
            release.wait(4)
            return original(agent, room, detail, state, sentiment)

        with patch.object(EngineeringAgent, "diagnose", slow):
            repair = self.manager.submit("guest", "空调坏了", force_rules=True)
            self.assertTrue(started.wait(2))
            cancel = self.manager.submit("guest", "全部取消", force_rules=True)
            try:
                self.assertTrue(cancel.result.done(), "Delivery control cannot wait for unrelated work")
                self.assertFalse(repair.cancelled.is_set())
                self.assertEqual(self.manager.shared_tickets[0]["status"], "已取消")
            finally:
                release.set()
            self.manager.wait("guest", repair.run_id)
            self.manager.wait("guest", cancel.run_id)
        self.assertEqual(len(self.manager.shared_tickets), 2)
        self.assertEqual(self.manager.shared_tickets[-1]["category"], "工程报修")

    def test_late_delivery_from_unclassified_slot_reply_cannot_commit_after_batch(self):
        self.chat("送2条毛巾")
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = HousekeepingAgent.dispatch

        def slow(agent, room, detail, state):
            started.set()
            release.wait(4)
            return original(agent, room, detail, state)

        with patch.object(HousekeepingAgent, "dispatch", slow):
            old = self.manager.submit("guest", "1128", force_rules=True)
            self.assertTrue(started.wait(2))
            cancel = self.manager.submit("guest", "全部取消", force_rules=True)
            try:
                self.assertTrue(cancel.result.done())
            finally:
                release.set()
            self.manager.wait("guest", old.run_id)
        self.assertEqual(self.manager.shared_tickets, [])
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)

    def test_batch_cancellation_leaves_dispatched_stock_until_staff_confirmation(self):
        self.chat("1128 送2条毛巾")
        self.chat("送1瓶矿泉水")
        water = self.manager.shared_tickets[-1]
        self.manager.dispatch_delivery(water["ticket_id"])
        result = self.chat("全部都不要了")
        self.assertEqual(self.manager.shared_tickets[0]["status"], "已取消")
        self.assertEqual(water["delivery_status"], "配送中")
        self.assertEqual(water.get("change_request", {}).get("status"), "待部门确认")
        self.assertEqual(self.manager.housekeeping.inventory["矿泉水"], 35)
        self.assertIn("确认", result.reply)

    def test_batch_cancellation_requests_staff_pickup_for_completed_delivery(self):
        delivery = self.chat("1128 送1个吹风机")
        ticket = self.manager.shared_tickets[0]
        self.manager.complete_ticket(ticket["ticket_id"])
        result = self.chat("全部都不要了")
        self.assertEqual(ticket["status"], "已结单")
        self.assertEqual(ticket.get("change_request", {}).get("status"), "待部门确认")
        self.assertEqual(self.manager.housekeeping.inventory["吹风机"], 5)
        self.assertEqual(self.manager.get_session("guest").state.tasks[delivery.task_id]["status"], "awaiting_confirmation")
        self.assertIn("确认", result.reply)

    def test_batch_cancel_does_not_touch_another_session_in_same_room(self):
        self.chat("1128 送2条毛巾")
        self.chat("1128 送1瓶矿泉水", session="other")
        self.chat("全部取消")
        self.assertEqual([t["status"] for t in self.manager.shared_tickets], ["已取消", "处理中"])

    def test_item_scoped_batch_status_and_audit_ignore_unrelated_cancellations(self):
        first = self.chat("1128 送1条浴巾")
        prior_cancel = self.chat("全部取消浴巾")
        self.chat("送1瓶矿泉水")
        water = self.manager.shared_tickets[-1]
        self.manager.dispatch_delivery(water["ticket_id"])
        self.chat("取消矿泉水")
        towel = self.chat("送2条毛巾")
        tasks = self.manager.get_session("guest").state.tasks
        earlier_audit = deepcopy(tasks[first.task_id])
        water_change = deepcopy(water["change_request"])
        result = self.chat("全部取消毛巾")
        self.assertEqual(tasks[result.task_id]["status"], "cancelled")
        self.assertEqual(tasks[towel.task_id]["status"], "cancelled")
        self.assertEqual(tasks[first.task_id], earlier_audit)
        self.assertEqual(tasks[first.task_id]["cancelled_by_run"], prior_cancel.run_id)
        self.assertEqual(water["change_request"], water_change)

    def test_negated_batch_command_is_not_executed(self):
        self.chat("1128 送2条毛巾")
        self.chat("送1瓶矿泉水")
        before = deepcopy(self.manager._business_payload())
        for text in ("不要全部取消", "我没说要全部取消，只是不需要那么快", "我不是要取消全部，只是问问"):
            with self.subTest(text=text):
                self.chat(text)
                self.assertEqual(self.manager._business_payload(), before)

    def test_unknown_and_mixed_catalog_requests_never_create_delivery_ticket(self):
        for text in ("1128 送红牛", "1128 送红牛和1瓶矿泉水", "1128 红牛和水都只要1瓶", "1128 送1瓶可乐和1条毛巾"):
            with self.subTest(text=text):
                flow = HotelOrchestrator(force_rules=True)
                # Even a cloud intent of 送物 must not authorize an unknown SKU.
                flow.reception.model.json_extract = lambda *_args: {"intent": "送物", "room_no": "1128", "sentiment": "正常"}
                stock = deepcopy(flow.housekeeping.inventory)
                reply = flow.process(text)
                self.assertEqual(flow.state.tickets, [])
                self.assertEqual(flow.housekeeping.inventory, stock)
                self.assertNotIn("已为您创建送物服务单", reply)

    def test_unknown_items_in_additions_conjunctions_and_polite_requests_are_not_dropped(self):
        for text in ("1128 送1瓶矿泉水，再加1罐可乐", "1128 送1瓶可乐及1条毛巾", "1128 送矿泉水和红牛好吗"):
            with self.subTest(text=text):
                flow = HotelOrchestrator(force_rules=True)
                before = deepcopy(flow.housekeeping.inventory)
                reply = flow.process(text)
                # CR-01 v2: the comma-separated, independently quantified water
                # clause is executable; conjunction/shared targets stay blocked.
                if "，再加" in text:
                    self.assertEqual(len(flow.state.tickets), 1)
                    self.assertEqual((flow.state.tickets[0]["inventory_item"], flow.state.tickets[0]["quantity"]), ("矿泉水", 1))
                    before["矿泉水"] -= 1
                    self.assertIn("可乐", reply)
                else:
                    self.assertEqual(flow.state.tickets, [])
                self.assertEqual(flow.housekeeping.inventory, before)
                self.assertIn("尚未创建", reply)

    def test_batch_cancel_with_new_or_replacement_request_requires_clarification(self):
        self.chat("1128 送2条毛巾")
        self.chat("送1瓶矿泉水")
        before = deepcopy(self.manager._business_payload())
        for text in ("全部取消，改成1条毛巾", "全部取消，再送1条毛巾", "全部都不要了，只要1个吹风机"):
            with self.subTest(text=text):
                result = self.chat(text)
                self.assertEqual(self.manager._business_payload(), before)
                self.assertIn("确认", result.reply)

    def test_catalog_validation_also_applies_to_direct_department_dispatch(self):
        flow = HotelOrchestrator(force_rules=True)
        before = deepcopy(flow.housekeeping.inventory)
        from agent_protocol import AgentConflict
        with self.assertRaises(AgentConflict):
            flow.housekeeping.dispatch("1128", "送红牛和1瓶矿泉水", flow.state)
        self.assertEqual(flow.housekeeping.inventory, before)

    def test_supervisor_rejects_child_that_silently_drops_unknown_item(self):
        flow = HotelOrchestrator(force_rules=True)
        flow._bind_room("1128")
        original = HousekeepingAgent.dispatch

        def omit_unknown(agent, room, detail, state):
            return original(agent, room, "送1瓶矿泉水", state)

        from agent_protocol import AgentConflict
        with patch.object(HousekeepingAgent, "dispatch", omit_unknown), self.assertRaises(AgentConflict):
            flow._verified_department_ticket("送物", "送红牛和1瓶矿泉水")
        self.assertEqual(flow.housekeeping.inventory["矿泉水"], 36)

    def test_cross_room_batch_command_cannot_interrupt_current_request(self):
        self.chat("1128 送1瓶矿泉水")
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = HousekeepingAgent.dispatch

        def slow(agent, room, detail, state):
            if "毛巾" in detail:
                started.set()
                release.wait(4)
            return original(agent, room, detail, state)

        with patch.object(HousekeepingAgent, "dispatch", slow):
            run = self.manager.submit("guest", "送1条毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            wrong = self.manager.submit("guest", "808 全部取消", force_rules=True)
            self.assertFalse(run.cancelled.is_set())
            release.set()
            self.manager.wait("guest", run.run_id)
            self.manager.wait("guest", wrong.run_id)
        self.assertTrue(all(t["status"] == "处理中" for t in self.manager.shared_tickets))

    def test_cancel_all_checkout_is_not_interpreted_as_delivery_cancellation(self):
        self.chat("1128 送2条毛巾")
        self.chat("把所有退房申请全部取消")
        self.assertEqual(self.manager.shared_tickets[0]["status"], "处理中")
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 22)

    def test_delivery_speed_negation_is_not_an_order_replacement(self):
        self.chat("1128 送1条毛巾")
        self.chat("送1个吹风机")
        result = self.chat("我很冷静，想再来两瓶水，不需要很快但一定要急")
        self.assertEqual(len(self.manager.shared_tickets), 3)
        self.assertEqual((self.manager.shared_tickets[-1]["inventory_item"], self.manager.shared_tickets[-1]["quantity"]), ("矿泉水", 2))
        self.assertNotIn("先说明", result.reply)

    def test_emotion_alone_cannot_authorize_cloud_invented_service(self):
        for intent in ("送物", "客诉紧急", "工程报修", "续住申请", "票券服务", "闲聊"):
            with self.subTest(intent=intent):
                flow = HotelOrchestrator(force_rules=True)
                flow._bind_room("1128")
                flow.reception.model.json_extract = lambda *_args: {"intent": intent, "sentiment": "愤怒", "room_no": None}
                before = deepcopy(flow.housekeeping.inventory)
                flow.process("我不要帮助，我就只是很急")
                self.assertEqual(flow.state.tickets, [])
                self.assertEqual(flow.state.checkout_requests, [])
                self.assertEqual(flow.state.voucher_ledger, [])
                self.assertEqual(flow.housekeeping.inventory, before)

    def test_emotion_or_denied_manager_is_not_a_complaint_under_rules_or_model(self):
        for text in ("我很生气", "我非常愤怒，但不需要帮助", "我不需要经理介入，只是很着急"):
            for cloud in (None, {"intent": "客诉紧急", "sentiment": "愤怒", "room_no": None}):
                with self.subTest(text=text, cloud=cloud):
                    flow = HotelOrchestrator(force_rules=True)
                    flow._bind_room("1128")
                    flow.reception.model.json_extract = lambda *_args: cloud
                    flow.process(text)
                    self.assertEqual(flow.state.tickets, [])
                    self.assertIsNone(flow.state.pending)

    def test_denied_guest_name_switches_to_neutral_address_without_rebinding(self):
        self.chat("1128 送1瓶矿泉水")
        before = deepcopy(self.manager._business_payload())
        correction = self.chat("我不是张先生")
        self.assertFalse(correction.reply.startswith("张先生"))
        self.assertIn("前台", correction.reply)
        following = self.chat("你是谁？")
        self.assertFalse(following.reply.startswith("张先生"))
        self.assertEqual(following.flow.state.room_no, "1128")
        self.assertEqual(following.flow.state.guest_profile["guest_name"], "张先生")
        self.assertEqual(self.manager._business_payload(), before)


if __name__ == "__main__":
    unittest.main()
