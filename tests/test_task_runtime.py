"""Business races, interrupted workers and supervisor decisions; always offline."""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from agent_runtime import RuntimeBusy, SessionManager
from hotel_core import EngineeringAgent, HotelOrchestrator, HousekeepingAgent, ReceptionAgent


class TaskRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager(max_workers=4)
        self.addCleanup(self.manager.close)

    def chat(self, message, session="guest"):
        return self.manager.chat_result(session, message, force_rules=True)

    def wait_finished(self, run):
        deadline = time.monotonic() + 3
        while not run.worker_finished and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(run.worker_finished, "background worker did not finish")

    def slow_towels(self):
        started, release = threading.Event(), threading.Event()
        original = HousekeepingAgent.dispatch

        def dispatch(agent, room, detail, state):
            if "毛巾" in detail and "浴巾" not in detail:
                started.set()
                if not release.wait(4):
                    raise RuntimeError("test did not release the old worker")
            return original(agent, room, detail, state)

        self.addCleanup(release.set)
        return patch.object(HousekeepingAgent, "dispatch", dispatch), started, release

    def test_replace_running_delivery_commits_only_latest_version(self):
        delayed, started, release = self.slow_towels()
        with delayed:
            old = self.manager.submit("guest", "1128 给我送两块毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            new = self.manager.submit("guest", "我不要毛巾了，我要浴巾", force_rules=True)
            outcome = self.manager.wait("guest", new.run_id, timeout=2)
            self.assertEqual(outcome.status, "completed")
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.task_id, new.task_id)
            self.assertEqual(new.version, 2)
            self.assertEqual(len(self.manager.shared_tickets), 1)
            ticket = self.manager.shared_tickets[0]
            self.assertEqual((ticket["inventory_item"], ticket["quantity"]), ("浴巾", 2))
            self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)
            self.assertEqual(self.manager.housekeeping.inventory["浴巾"], 22)
            release.set()
            self.wait_finished(old)
        self.assertEqual(len(self.manager.shared_tickets), 1)
        self.assertEqual(self.manager.shared_tickets[0]["inventory_item"], "浴巾")
        replies = [m["content"] for m in self.manager.get_session("guest").state.messages if m["role"] == "assistant"]
        self.assertEqual(len(replies), 1)

    def test_cancel_running_delivery_never_creates_ticket_or_reserves_stock(self):
        delayed, started, release = self.slow_towels()
        with delayed:
            old = self.manager.submit("guest", "1128 送两条毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            outcome = self.chat("不用了")
            self.assertIn("取消", outcome.reply)
            release.set()
            self.wait_finished(old)
        self.assertEqual(self.manager.shared_tickets, [])
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)

    def test_three_rapid_versions_keep_quantity_and_only_publish_latest(self):
        started = {item: threading.Event() for item in ("毛巾", "浴巾")}
        release = threading.Event()
        self.addCleanup(release.set)
        original = HousekeepingAgent.dispatch

        def dispatch(agent, room, detail, state):
            for item, event in started.items():
                if item in detail:
                    event.set()
                    if not release.wait(4):
                        raise RuntimeError("worker was not released")
            return original(agent, room, detail, state)

        with patch.object(HousekeepingAgent, "dispatch", dispatch):
            first = self.manager.submit("guest", "1128 送两条毛巾", force_rules=True)
            self.assertTrue(started["毛巾"].wait(2))
            second = self.manager.submit("guest", "改成浴巾", force_rules=True)
            self.assertTrue(started["浴巾"].wait(2))
            third = self.manager.submit("guest", "改成矿泉水", force_rules=True)
            outcome = self.manager.wait("guest", third.run_id, timeout=2)
            self.assertEqual(outcome.version, 3)
            self.assertEqual(len(self.manager.shared_tickets), 1)
            ticket = self.manager.shared_tickets[0]
            self.assertEqual((ticket["inventory_item"], ticket["quantity"]), ("矿泉水", 2))
            release.set()
            self.wait_finished(first)
            self.wait_finished(second)
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)
        self.assertEqual(self.manager.housekeeping.inventory["浴巾"], 24)
        self.assertEqual(self.manager.housekeeping.inventory["矿泉水"], 34)

    def test_other_guest_is_not_blocked_by_slow_department(self):
        delayed, started, release = self.slow_towels()
        with delayed:
            old = self.manager.submit("slow", "1128 送两条毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            quick = self.manager.submit("quick", "1106 早餐几点开始？", force_rules=True)
            result = self.manager.wait("quick", quick.run_id, timeout=2)
            self.assertIn("07:00", result.reply)
            self.assertFalse(old.result.done())
            release.set()
            self.wait_finished(old)
        self.assertEqual(self.manager.get_session("quick").state.room_no, "1106")

    def test_unrelated_faq_queues_without_cancelling_delivery(self):
        delayed, started, release = self.slow_towels()
        with delayed:
            old = self.manager.submit("guest", "1128 送两条毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            faq = self.manager.submit("guest", "早餐几点？", force_rules=True)
            self.assertEqual(faq.status, "queued")
            self.assertFalse(old.cancelled.is_set())
            release.set()
            self.assertEqual(self.manager.wait("guest", old.run_id).status, "completed")
            self.assertIn("07:00", self.manager.wait("guest", faq.run_id).reply)
        self.assertEqual(len(self.manager.shared_tickets), 1)

    def test_explicit_delivery_cancel_does_not_discard_active_faq(self):
        self.chat("1128 送两条毛巾")
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = ReceptionAgent.understand

        def understand(agent, text):
            if text == "早餐几点？":
                started.set()
                if not release.wait(4):
                    raise RuntimeError("FAQ was not released")
            return original(agent, text)

        with patch.object(ReceptionAgent, "understand", understand):
            faq = self.manager.submit("guest", "早餐几点？", force_rules=True)
            self.assertTrue(started.wait(2))
            cancel = self.manager.submit("guest", "取消毛巾", force_rules=True)
            self.assertEqual(cancel.status, "queued")
            self.assertFalse(faq.cancelled.is_set())
            release.set()
            self.assertIn("07:00", self.manager.wait("guest", faq.run_id).reply)
            self.assertIn("已取消", self.manager.wait("guest", cancel.run_id).reply)
        self.assertEqual(self.manager.shared_tickets[0]["status"], "已取消")
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)

    def test_reset_cancels_inflight_worker_without_resurrecting_session(self):
        delayed, started, release = self.slow_towels()
        with delayed:
            old = self.manager.submit("guest", "1128 送两条毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            self.manager.reset("guest")
            release.set()
            self.wait_finished(old)
        self.assertIsNone(self.manager.get_session("guest"))
        self.assertEqual(self.manager.shared_tickets, [])

    def test_request_id_retry_does_not_duplicate_delivery(self):
        a = self.manager.submit("guest", "1128 送两条毛巾", force_rules=True, request_id="retry-1")
        b = self.manager.submit("guest", "1128 送两条毛巾", force_rules=True, request_id="retry-1")
        self.assertIs(a, b)
        self.manager.wait("guest", a.run_id)
        self.assertEqual(len(self.manager.shared_tickets), 1)
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 22)
        with self.assertRaises(ValueError):
            self.manager.submit("guest", "1128 送浴巾", force_rules=True, request_id="retry-1")

    def test_task_polling_cannot_use_another_session(self):
        run = self.manager.submit("guest", "早餐几点？", force_rules=True)
        self.manager.wait("guest", run.run_id)
        with self.assertRaises(KeyError):
            self.manager.get_run("other", run.run_id)

    def test_completed_compute_can_amend_unshipped_ticket_without_duplicate(self):
        self.chat("1128 送两条毛巾")
        original_id = self.manager.shared_tickets[0]["ticket_id"]
        outcome = self.chat("不要毛巾了，改成浴巾")
        self.assertIn("改为", outcome.reply)
        self.assertEqual(len(self.manager.shared_tickets), 1)
        ticket = self.manager.shared_tickets[0]
        self.assertEqual(ticket["ticket_id"], original_id)
        self.assertEqual((ticket["inventory_item"], ticket["quantity"]), ("浴巾", 2))
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)
        self.assertEqual(self.manager.housekeeping.inventory["浴巾"], 22)

    def test_cancel_existing_unshipped_ticket_releases_stock_once(self):
        self.chat("1128 送两条毛巾")
        self.chat("不用了")
        self.chat("不用了")
        self.assertEqual(self.manager.shared_tickets[0]["status"], "已取消")
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)

    def test_dispatched_delivery_waits_for_staff_acknowledgement(self):
        self.chat("1128 送两条毛巾")
        ticket = self.manager.shared_tickets[0]
        self.manager.dispatch_delivery(ticket["ticket_id"])
        outcome = self.chat("不要毛巾了，改成浴巾")
        self.assertIn("尚未确认取消成功", outcome.reply)
        self.assertEqual(ticket["inventory_item"], "毛巾")
        self.assertEqual(ticket["change_request"]["status"], "待部门确认")
        self.manager.confirm_delivery_change(ticket["ticket_id"], approved=True)
        self.assertEqual(ticket["inventory_item"], "浴巾")
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)
        self.assertEqual(self.manager.housekeeping.inventory["浴巾"], 22)
        self.manager.confirm_delivery_change(ticket["ticket_id"], approved=True)
        self.assertEqual(self.manager.housekeeping.inventory["浴巾"], 22)

    def test_dispatch_during_amendment_rechecks_actual_delivery_state(self):
        self.chat("1128 送两条毛巾")
        ticket = self.manager.shared_tickets[0]
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = HousekeepingAgent.revise
        first_call = True

        def revise(agent, *args, **kwargs):
            nonlocal first_call
            if first_call:
                first_call = False
                started.set()
                if not release.wait(4):
                    raise RuntimeError("amendment was not released")
            return original(agent, *args, **kwargs)

        with patch.object(HousekeepingAgent, "revise", revise):
            run = self.manager.submit("guest", "不要毛巾了，改成浴巾", force_rules=True)
            self.assertTrue(started.wait(2))
            self.manager.dispatch_delivery(ticket["ticket_id"])
            release.set()
            result = self.manager.wait("guest", run.run_id)
        self.assertIn("尚未确认取消成功", result.reply)
        self.assertEqual(ticket["delivery_status"], "配送中")
        self.assertEqual(ticket["change_request"]["status"], "待部门确认")
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 22)
        self.assertEqual(self.manager.housekeeping.inventory["浴巾"], 24)

    def test_concurrent_guests_cannot_oversell_shared_inventory(self):
        barrier = threading.Barrier(2)
        call_lock = threading.Lock()
        calls = 0
        original = HousekeepingAgent.dispatch

        def dispatch(agent, *args):
            nonlocal calls
            with call_lock:
                calls += 1
                synchronize = calls <= 2
            if synchronize:
                barrier.wait(timeout=3)
            return original(agent, *args)

        with patch.object(HousekeepingAgent, "dispatch", dispatch):
            a = self.manager.submit("a", "1128 送20条毛巾", force_rules=True)
            b = self.manager.submit("b", "1106 送20条毛巾", force_rules=True)
            outcomes = [self.manager.wait("a", a.run_id), self.manager.wait("b", b.run_id)]
        self.assertTrue(all(outcome.status == "completed" for outcome in outcomes))
        self.assertEqual(len(self.manager.shared_tickets), 2)
        self.assertEqual(sorted(t["reserved_quantity"] for t in self.manager.shared_tickets), [0, 20])
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 4)
        self.assertEqual(sum("库存不足" in outcome.reply for outcome in outcomes), 1)

    def test_ambiguous_cancel_does_not_cancel_arbitrary_ticket(self):
        self.chat("1128 送两条毛巾")
        self.chat("送两瓶矿泉水")
        result = self.chat("不用了")
        self.assertIn("先说明", result.reply)
        self.assertTrue(all(t["status"] == "处理中" for t in self.manager.shared_tickets))
        self.assertEqual(result.flow.state.tasks[result.task_id]["status"], "awaiting_input")

    def test_targeted_cancel_preserves_each_orders_task_ownership(self):
        towels = self.chat("1128 送两条毛巾")
        water = self.chat("送两瓶矿泉水")
        result = self.chat("取消毛巾")
        self.assertEqual(result.task_id, towels.task_id)
        self.assertEqual(result.version, 2)
        by_item = {t["inventory_item"]: t for t in self.manager.shared_tickets}
        self.assertEqual(by_item["毛巾"]["status"], "已取消")
        self.assertEqual(by_item["矿泉水"]["status"], "处理中")
        self.assertEqual(by_item["矿泉水"]["task_id"], water.task_id)
        self.assertEqual(self.manager.get_session("guest").state.tasks[water.task_id]["status"], "submitted")

    def test_amending_inflight_delivery_does_not_replace_other_open_order(self):
        water = self.chat("1128 送两瓶矿泉水")
        delayed, started, release = self.slow_towels()
        with delayed:
            old = self.manager.submit("guest", "送两条毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            latest = self.chat("改成浴巾")
            self.assertEqual(latest.task_id, old.task_id)
            release.set()
            self.wait_finished(old)
        by_item = {t["inventory_item"]: t for t in self.manager.shared_tickets}
        self.assertEqual(set(by_item), {"矿泉水", "浴巾"})
        self.assertEqual(by_item["矿泉水"]["task_id"], water.task_id)
        self.assertEqual(by_item["浴巾"]["quantity"], 2)

    def test_cancel_other_order_keeps_inflight_delivery_running(self):
        water = self.chat("1128 送两瓶矿泉水")
        delayed, started, release = self.slow_towels()
        with delayed:
            towels = self.manager.submit("guest", "送两条毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            cancel = self.manager.submit("guest", "取消矿泉水", force_rules=True)
            self.assertFalse(towels.cancelled.is_set())
            self.assertEqual(cancel.task_id, water.task_id)
            release.set()
            self.manager.wait("guest", towels.run_id)
            self.manager.wait("guest", cancel.run_id)
        by_item = {t["inventory_item"]: t for t in self.manager.shared_tickets}
        self.assertEqual(by_item["毛巾"]["status"], "处理中")
        self.assertEqual(by_item["矿泉水"]["status"], "已取消")

    def test_delivered_item_cannot_be_restocked_without_return_confirmation(self):
        self.chat("1128 送两条毛巾")
        ticket = self.manager.shared_tickets[0]
        ticket.update(status="已结单", delivery_status="已送达")
        self.chat("不要毛巾了，改成浴巾")
        with self.assertRaises(ValueError):
            self.manager.confirm_delivery_change(ticket["ticket_id"], approved=True)
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 22)
        self.manager.confirm_delivery_change(ticket["ticket_id"], approved=True, returned=True)
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)

    def test_staff_confirmation_updates_borrowed_record_and_notifies_guest(self):
        self.chat("1128 送一把雨伞")
        ticket = self.manager.shared_tickets[0]
        self.manager.dispatch_delivery(ticket["ticket_id"])
        self.chat("改成吹风机")
        self.manager.confirm_delivery_change(ticket["ticket_id"], approved=True)
        state = self.manager.get_session("guest").state
        self.assertEqual(state.borrowed_items[0]["item"], "吹风机")
        self.assertEqual(self.manager.housekeeping.inventory["雨伞"], 10)
        self.assertEqual(self.manager.housekeeping.inventory["吹风机"], 5)
        self.assertIn("保洁确认", state.messages[-1]["content"])

    def test_rejected_change_keeps_stock_and_clears_waiting_status(self):
        self.chat("1128 送两条毛巾")
        ticket = self.manager.shared_tickets[0]
        self.manager.dispatch_delivery(ticket["ticket_id"])
        change = self.chat("改成浴巾")
        self.manager.confirm_delivery_change(ticket["ticket_id"], approved=False)
        state = self.manager.get_session("guest").state
        self.assertEqual(state.tasks[change.task_id]["status"], "submitted")
        self.assertEqual(ticket["change_request"]["status"], "已拒绝")
        self.assertEqual(ticket["inventory_item"], "毛巾")
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 22)
        self.assertEqual(self.manager.housekeeping.inventory["浴巾"], 24)
        self.assertIn("无法撤回", state.messages[-1]["content"])

    def test_night_audit_cannot_be_overwritten_by_older_snapshot(self):
        delayed, started, release = self.slow_towels()
        with delayed:
            run = self.manager.submit("guest", "1128 送两条毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            audit = self.manager.night_audit()
            release.set()
            result = self.manager.wait("guest", run.run_id)
        self.assertEqual(result.status, "completed")
        state = self.manager.get_session("guest").state
        self.assertEqual(state.current_date, audit["date"])
        self.assertEqual(len(state.audit_logs), 1)
        self.assertEqual(self.manager.get_or_create("new", force_rules=True).state.current_date, audit["date"])

    def test_conflicting_child_item_is_rejected_without_stock_commit(self):
        original = HousekeepingAgent.dispatch

        def wrong_item(agent, room, detail, state):
            proposal = original(agent, room, detail, state)
            proposal["inventory_item"] = "毛巾"
            return proposal

        with patch.object(HousekeepingAgent, "dispatch", wrong_item):
            outcome = self.chat("1128 送两条浴巾")
        self.assertEqual(outcome.status, "needs_review")
        self.assertIn("暂未派单", outcome.reply)
        self.assertEqual(self.manager.shared_tickets, [])
        self.assertEqual(self.manager.housekeeping.inventory["浴巾"], 24)
        self.assertEqual(len(self.manager.get_session("guest").state.agent_conflicts), 1)

    def test_supervisor_cannot_accept_invented_inventory(self):
        original = HousekeepingAgent.dispatch

        def invent_stock(agent, room, detail, state):
            proposal = original(agent, room, detail, state)
            proposal["inventory_remaining"] = 999
            return proposal

        with patch.object(HousekeepingAgent, "dispatch", invent_stock):
            outcome = self.chat("1128 送两条毛巾")
        self.assertEqual(outcome.status, "needs_review")
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)

    def test_child_cannot_downgrade_safety_priority(self):
        original = EngineeringAgent.diagnose

        def downgrade(agent, *args, **kwargs):
            proposal = original(agent, *args, **kwargs)
            proposal.update(priority="低", risk_level="普通")
            return proposal

        with patch.object(EngineeringAgent, "diagnose", downgrade):
            outcome = self.chat("1128 卫生间严重漏水")
        self.assertEqual(outcome.status, "needs_review")
        self.assertEqual(self.manager.shared_tickets, [])

    def test_queued_capacity_has_backpressure(self):
        manager = SessionManager(max_workers=1, max_pending=1)
        self.addCleanup(manager.close)
        delayed, started, release = self.slow_towels()
        with delayed:
            run = manager.submit("guest", "1128 送两条毛巾", force_rules=True)
            self.assertTrue(started.wait(2))
            with self.assertRaises(RuntimeBusy):
                manager.submit("other", "早餐几点？", force_rules=True)
            release.set()
            manager.wait("guest", run.run_id)


class DirectOrchestratorChangeTests(unittest.TestCase):
    def test_ambiguous_items_and_zero_quantity_never_dispatch(self):
        for message in ("1128 送毛巾和浴巾", "1128 送0条毛巾"):
            with self.subTest(message=message):
                flow = HotelOrchestrator(force_rules=True)
                self.assertIn("尚未派单", flow.process(message))
                self.assertEqual(flow.state.tickets, [])
                self.assertEqual(flow.housekeeping.inventory["毛巾"], 24)

    def test_negated_item_is_not_selected_as_new_delivery(self):
        flow = HotelOrchestrator(force_rules=True)
        flow.process("1128 送两条毛巾")
        flow.process("我不要毛巾了，我要浴巾")
        self.assertEqual(len(flow.state.tickets), 1)
        self.assertEqual(flow.state.tickets[0]["inventory_item"], "浴巾")
        self.assertEqual(flow.state.tickets[0]["quantity"], 2)

    def test_change_before_room_slot_keeps_original_quantity(self):
        flow = HotelOrchestrator(force_rules=True)
        flow.process("送两条毛巾")
        flow.process("不要毛巾了，换成浴巾")
        flow.process("1128")
        self.assertEqual(len(flow.state.tickets), 1)
        self.assertEqual((flow.state.tickets[0]["inventory_item"], flow.state.tickets[0]["quantity"]), ("浴巾", 2))


if __name__ == "__main__":
    unittest.main()
