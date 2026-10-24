"""Catalog facts, language/risk boundaries and multiple authenticated guests."""
from __future__ import annotations

import json
import os
import threading
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from agent_protocol import parse_delivery_command
from agent_runtime import SessionManager
from hotel_core import HotelOrchestrator, HousekeepingAgent


class HotelReadinessTests(unittest.TestCase):
    def test_regular_catalog_has_at_least_24_stocked_items_and_safe_metadata(self):
        from hotel_catalog import ITEM_CATALOG
        stock = HousekeepingAgent().inventory
        self.assertGreaterEqual(len(stock), 24)
        self.assertEqual(set(stock), set(ITEM_CATALOG))
        self.assertTrue(all(type(n) is int and n >= 0 for n in stock.values()))
        for name in ("纸巾", "卷纸", "牙膏", "浴帽", "棉签", "香皂", "耳塞", "洗衣袋", "衣架"):
            self.assertIn(name, stock)
            self.assertEqual(parse_delivery_command(f"送1件{name}").item, name)
            self.assertIn("unit", ITEM_CATALOG[name])
            self.assertIn("group", ITEM_CATALOG[name])
        self.assertNotIn("房卡", stock, "Room access requires front-desk identity verification")

    def test_stock_question_uses_current_inventory_without_model_or_ticket(self):
        flow = HotelOrchestrator(force_rules=True)
        flow.housekeeping.inventory["纸巾"] = 0
        with patch.object(flow.reception, "understand", side_effect=AssertionError("Catalog facts must not be guessed")):
            reply = flow.process("现在还有纸巾吗？")
        self.assertIn("暂时缺货", reply)
        self.assertEqual(flow.state.tickets, [])
        self.assertEqual(flow.housekeeping.inventory["纸巾"], 0)

    def test_service_three_states_and_review_counts_do_not_invent_availability(self):
        for status, expected in (("available", "可提供"), ("unavailable", "不提供"), ("unconfirmed", "尚未核验")):
            with self.subTest(status=status):
                flow = HotelOrchestrator(force_rules=True)
                catalog = {"services": [{"name": "按摩服务", "aliases": ["按摩"], "status": status,
                                          "source": "synthetic-approved-config"}]}
                with patch("hotel_core.load_service_catalog", return_value=catalog), patch.object(flow.reception, "understand", side_effect=AssertionError("Do not call model for configured facts")):
                    reply = flow.process("酒店能提供按摩吗")
                    flow.process("想问一下有没有按摩")
                self.assertIn(expected, reply)
                self.assertEqual(flow.state.tickets, [])
                feedback = getattr(flow.state, "service_feedback", [])
                if status != "available":
                    self.assertEqual(len(feedback), 1)
                    self.assertEqual(feedback[0]["count"], 2)
                    self.assertEqual(feedback[0]["status"], "待酒店核验")
                    self.assertNotIn("input", feedback[0])

    def test_broken_bath_is_repair_not_a_service_catalog_question(self):
        flow = HotelOrchestrator(force_rules=True)
        flow.process("1128 浴缸漏水，马上来修")
        self.assertEqual(flow.state.tickets[0]["category"], "工程报修")

    def test_catalog_question_does_not_swallow_delivery_or_cancellation(self):
        flow = HotelOrchestrator(force_rules=True)
        reply = flow.process("1128 送2条毛巾，顺便问一下有没有按摩")
        self.assertIn("尚未核验", reply)
        self.assertEqual(len(flow.state.tickets), 1)
        self.assertEqual(flow.housekeeping.inventory["毛巾"], 22)
        reply = flow.process("毛巾全部取消，酒店有没有按摩")
        self.assertIn("已取消", reply)
        self.assertIn("尚未核验", reply)
        self.assertEqual(flow.state.tickets[0]["status"], "已取消")
        self.assertEqual(flow.housekeeping.inventory["毛巾"], 24)
        reply = flow.process("房间断电了，还有矿泉水吗？")
        self.assertIn("矿泉水", reply)
        self.assertEqual(flow.state.tickets[-1]["category"], "工程报修")
        self.assertLess(reply.index("请先远离"), reply.index("矿泉水"))

    def test_room_access_and_academic_vouchers_queries_require_verification(self):
        flow = HotelOrchestrator(force_rules=True)
        with patch.dict(os.environ, {"AEGIS_SITE_PROFILE": "academic"}), patch.object(flow.reception, "understand", side_effect=AssertionError("Policy query must be local")):
            for text in ("酒店有房卡吗？", "现在还有早餐券吗？"):
                reply = flow.process(text)
                self.assertIn("前台核验", reply)
                self.assertNotIn("目前可申请", reply)
                self.assertNotIn("暂时缺货", reply)
        self.assertEqual(flow.state.tickets, [])
        self.assertEqual(flow.housekeeping.inventory["早餐券"], 40)

    def test_spoken_delivery_fillers_are_not_unknown_products(self):
        flow = HotelOrchestrator(force_rules=True)
        flow.process("1128 水，两瓶，给我再来一下，不用催")
        self.assertEqual(len(flow.state.tickets), 1)
        self.assertEqual(flow.state.tickets[0]["inventory_item"], "矿泉水")
        self.assertEqual(flow.state.tickets[0]["quantity"], 2)
        self.assertEqual(flow.housekeeping.inventory["矿泉水"], 34)

    def test_smoke_or_electric_leak_is_high_risk_even_if_model_says_chitchat(self):
        for text in ("走廊插座冒烟还漏电，我已经离远了", "灯突然冒烟了，请来看看"):
            with self.subTest(text=text):
                flow = HotelOrchestrator(force_rules=True)
                flow._bind_room("1128")
                flow.reception.model.json_extract = lambda *_: {"intent": "闲聊", "sentiment": "正常"}
                flow.process(text)
                self.assertEqual(flow.state.tickets[0]["priority"], "高")
                self.assertEqual(flow.state.tickets[0]["risk_level"], "红色")
                self.assertEqual(flow.state.tickets[0]["category"], "工程报修")

    def test_delivery_urgency_is_medium_but_explicitly_not_urgent_remains_low(self):
        for text, priority in (("1128 送1条毛巾，急用，麻烦尽快", "中"), ("1128 送1条毛巾，不急，不用马上送", "低"),
                               ("1128 送1条毛巾，不用赶时间", "低"), ("1128 送1条毛巾，我很急", "中"),
                               ("1128 送1条毛巾，不着急", "低")):
            with self.subTest(text=text):
                flow = HotelOrchestrator(force_rules=True)
                flow.process(text)
                self.assertEqual(flow.state.tickets[0]["priority"], priority)

    def test_new_regular_item_cancellation_restores_only_reserved_stock(self):
        manager = SessionManager(db_path="")
        self.addCleanup(manager.close)
        initial = manager.housekeeping.inventory.get("耳塞")
        self.assertIsNotNone(initial)
        manager.chat_result("guest", "1128 送2个耳塞", force_rules=True)
        manager.chat_result("guest", "全部取消", force_rules=True)
        manager.chat_result("guest", "全部取消", force_rules=True)
        self.assertEqual(manager.housekeeping.inventory["耳塞"], initial)
        self.assertEqual(len(manager.shared_tickets), 1)
        self.assertEqual(manager.shared_tickets[0]["status"], "已取消")

    def test_catalog_expansion_does_not_invent_stock_in_existing_database(self):
        path = Path(tempfile.gettempdir()) / ("aegis-catalog-" + uuid.uuid4().hex + ".sqlite3")
        manager = SessionManager(db_path=str(path))
        try:
            manager.housekeeping.inventory.pop("耳塞")
            manager.chat_result("guest", "酒店有按摩吗", force_rules=True)
            manager.persist()
            manager.close()
            manager = SessionManager(db_path=str(path))
            self.assertEqual(manager.housekeeping.inventory["耳塞"], 0)
            self.assertEqual(manager.get_session("guest").state.service_feedback[0]["count"], 1)
            manager.chat_result("guest", "还有按摩服务吗", force_rules=True)
            self.assertEqual(manager.get_session("guest").state.service_feedback[0]["count"], 2)
        finally:
            manager.close()
            for suffix in ("", "-wal", "-shm", ".writer.lock"):
                Path(str(path) + suffix).unlink(missing_ok=True)

    def test_explicit_denial_of_smoke_does_not_raise_an_ordinary_light_repair(self):
        for text in ("1128 灯不亮，没有冒烟也没有漏电", "1128 灯不亮，没有冒烟和漏电", "1128 灯不亮，不存在漏电"):
            with self.subTest(text=text):
                flow = HotelOrchestrator(force_rules=True)
                flow.process(text)
                self.assertEqual(flow.state.tickets[0]["priority"], "中")


class ConcurrentGuestApiTests(unittest.TestCase):
    def setUp(self):
        import server
        from fastapi.testclient import TestClient
        self.server, self.TestClient = server, TestClient
        self.admin = "readiness-admin-" + "a" * 40
        self.env = patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1", "AEGIS_AUTH_ENABLED": "1", "AEGIS_DEPLOYED": "0",
                         "AEGIS_DB_PATH": "", "AEGIS_SITE_PROFILE": "academic", "AEGIS_STAFF_TOKEN": self.admin,
                         "AEGIS_SIGNING_KEY": "readiness-signing-" + "b" * 40, "AEGIS_STAFF_KEYS_JSON": "{}"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.manager = SessionManager(db_path="", max_workers=8)
        self.addCleanup(self.manager.close)
        self.binding = patch.object(server, "session_manager", self.manager)
        self.binding.start()
        self.addCleanup(self.binding.stop)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        self.staff = {"Authorization": "Bearer " + self.admin}

    def grant(self, room):
        response = self.client.post("/api/guest-access", headers=self.staff,
                                    json={"room_no": room, "identity_checked": True})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_eight_guest_clients_share_stock_and_cancel_only_their_own_reservation(self):
        rooms = [n for n, r in self.manager.shared_pms.items() if r["room_status"] == "在住"][:8]
        guests = [self.grant(room) for room in rooms]
        barrier = threading.Barrier(8)
        def send(index):
            guest = guests[index]
            # A single shared application owns the manager. Starting/stopping
            # eight ASGI lifespans would close it while other clients run.
            client = self.TestClient(self.server.app)
            try:
                barrier.wait(timeout=5)
                response = client.post("/api/chat", headers={"Authorization": "Bearer " + guest["access_token"]},
                                       json={"session_id": guest["session_id"], "message": "送6条毛巾", "request_id": f"stock-{index}"})
                return response.status_code, response.json()
            finally:
                client.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(send, range(8)))
        self.assertTrue(all(code == 200 for code, _ in responses))
        self.assertTrue(all(row["run_status"] == "completed" for _, row in responses), responses)
        self.assertEqual(len(self.manager.shared_tickets), 8)
        reserved = sum(t["reserved_quantity"] for t in self.manager.shared_tickets)
        self.assertEqual(reserved, 24)
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 0)
        ticket = next(t for t in self.manager.shared_tickets if t["reserved_quantity"])
        guest = next(g for g in guests if g["session_id"] == ticket["owner_session_id"])
        other_before = deepcopy([t for t in self.manager.shared_tickets if t is not ticket])
        for _ in range(2):
            result = self.client.post("/api/chat", headers={"Authorization": "Bearer " + guest["access_token"]},
                                      json={"session_id": guest["session_id"], "message": "全部取消"})
            self.assertEqual(result.status_code, 200)
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 6)
        self.assertEqual([t for t in self.manager.shared_tickets if t is not ticket], other_before)
        foreign = next(g for g in guests if g is not guest)
        self.assertEqual(self.client.get(f"/api/session/{foreign['session_id']}/messages",
                    headers={"Authorization": "Bearer " + guest["access_token"]}).status_code, 403)

    def test_new_stay_has_new_history_and_preferences_old_pass_is_invalid(self):
        old = self.grant("1128")
        flow = self.manager.get_session(old["session_id"])
        self.manager.chat_result(old["session_id"], "酒店有按摩吗", force_rules=True)
        flow.state.neutral_address = True
        new_stay = self.manager.register_stay("1128", "合成新住客", (date.today()+timedelta(days=2)).isoformat(), "synthetic-new-order", True)
        new = self.grant("1128")
        self.assertNotEqual(old["stay_id"], new["stay_id"])
        self.assertEqual(self.manager.conversation_history(new["session_id"]), [])
        current = self.manager.get_session(new["session_id"]).state
        self.assertFalse(current.neutral_address)
        self.assertEqual(getattr(current, "service_feedback", []), [])
        self.assertEqual(self.client.get("/api/me", headers={"Authorization": "Bearer " + old["access_token"]}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
