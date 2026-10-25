"""Executable staff delivery details, read-only projection and write guards."""
from __future__ import annotations

from copy import deepcopy
import importlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_runtime import SessionManager
import server


def delivery(**updates):
    row = {"ticket_id": "HT-fixture", "room_no": "1128", "category": "送物",
           "assigned_dept": "楼层保洁", "priority": "低", "status": "处理中",
           "item_detail": "送2瓶矿泉水", "inventory_item": "矿泉水", "quantity": 2,
           "reserved_quantity": 2, "delivery_status": "待派送"}
    row.update(updates)
    return row


class DeliveryFulfillmentProjectionTests(unittest.TestCase):
    def details(self, ticket, stock):
        self.assertIsNotNone(importlib.util.find_spec("ticket_details"), "missing shared fulfillment projection")
        return importlib.import_module("ticket_details").delivery_fulfillment(ticket, stock)

    def test_ready_line_has_canonical_item_unit_and_all_hand_checked_quantities(self):
        ticket, stock = delivery(), {"矿泉水": 34}
        before = deepcopy((ticket, stock))
        result = self.details(ticket, stock)
        self.assertEqual(result["items"], [{"name": "矿泉水", "unit": "瓶", "requested_quantity": 2,
                         "reserved_quantity": 2, "available_quantity": 34, "shortage_quantity": 0,
                         "needs_restock": False}])
        self.assertEqual(result["state"], "ready")
        self.assertTrue(result["can_dispatch"])
        self.assertTrue(result["can_complete"])
        self.assertEqual((ticket, stock), before)

    def test_fully_reserved_delivery_remains_ready_when_unreserved_stock_is_zero(self):
        result = self.details(delivery(), {"矿泉水": 0})
        self.assertTrue(result["can_dispatch"])
        self.assertFalse(result["needs_restock"])

    def test_shortage_line_shows_seven_requested_six_available_one_missing(self):
        result = self.details(delivery(item_detail="给我送7个吹风机", inventory_item="吹风机",
                                      quantity=7, reserved_quantity=0), {"吹风机": 6})
        self.assertEqual(result["state"], "awaiting_stock")
        self.assertIn("库存协调", result["label"])
        self.assertEqual(result["items"][0], {"name": "吹风机", "unit": "个", "requested_quantity": 7,
                         "reserved_quantity": 0, "available_quantity": 6, "shortage_quantity": 1,
                         "needs_restock": True})
        self.assertFalse(result["can_dispatch"])
        self.assertFalse(result["can_complete"])

    def test_replenished_stock_is_not_automatically_reserved_or_dispatched(self):
        result = self.details(delivery(quantity=7, reserved_quantity=0, item_detail="送7瓶矿泉水",
                                      delivery_status="待库存协调"), {"矿泉水": 10})
        self.assertEqual(result["state"], "awaiting_reservation")
        self.assertFalse(result["needs_restock"])
        self.assertFalse(result["can_dispatch"])
        self.assertIn("预留", result["message"])

    def test_cancelled_shortage_does_not_request_restock(self):
        result = self.details(delivery(status="已取消", reserved_quantity=0, delivery_status="已取消"), {"矿泉水": 0})
        self.assertEqual(result["state"], "cancelled")
        self.assertFalse(result["needs_restock"])
        self.assertFalse(result["items"][0]["needs_restock"])
        self.assertFalse(result["can_dispatch"])
        self.assertFalse(result["can_complete"])

    def test_in_transit_delivered_and_change_pending_are_distinct(self):
        for updates, state, dispatchable, completable in [
            ({"delivery_status": "配送中"}, "in_transit", False, True),
            ({"delivery_status": "已送达", "status": "已结单"}, "completed", False, False),
            ({"change_request": {"status": "待部门确认"}}, "change_pending", False, False),
        ]:
            with self.subTest(state=state):
                result = self.details(delivery(**updates), {"矿泉水": 34})
                self.assertEqual((result["state"], result["can_dispatch"], result["can_complete"]),
                                 (state, dispatchable, completable))

    def test_old_unknown_or_incomplete_details_are_reviewed_without_guessing(self):
        cases = [
            delivery(item_detail="我要两瓶水，两桶泡面，一桶泡面里装水"),
            delivery(item_detail="送2瓶矿泉水2条毛巾"),
            delivery(item_detail="送3瓶矿泉水"),
            delivery(inventory_item="未核准物料"),
            {"category": "送物", "status": "处理中", "item_detail": "送水"},
            delivery(quantity=True), delivery(reserved_quantity=-1), delivery(quantity=0),
        ]
        for ticket in cases:
            with self.subTest(ticket=ticket):
                result = self.details(ticket, {"矿泉水": 34})
                self.assertTrue(result["needs_verification"])
                self.assertEqual(result["state"], "needs_review")
                self.assertFalse(result["can_dispatch"])
                self.assertFalse(result["can_complete"])

    def test_non_delivery_has_no_inventory_fulfillment(self):
        self.assertIsNone(self.details({"category": "工程报修", "item_detail": "空调坏了"}, {}))

    def test_malformed_legacy_field_types_are_reviewed_instead_of_crashing(self):
        for ticket, stock in [(delivery(inventory_item=[]), {}),
                              (delivery(inventory_item={"name": "矿泉水"}), {}),
                              (delivery(reserved_quantity=True), {"矿泉水": 34}),
                              (delivery(), {"矿泉水": "34"}),
                              (delivery(), {})]:
            with self.subTest(ticket=ticket, stock=stock):
                result = self.details(ticket, stock)
                self.assertTrue(result["needs_verification"])
                self.assertFalse(result["can_dispatch"])


class TicketFulfillmentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1", "AEGIS_AUTH_ENABLED": "0",
            "AEGIS_DEPLOYED": "0", "AEGIS_DB_PATH": "", "AEGIS_SITE_PROFILE": "academic",
            "AEGIS_PMS_FILE": "", "AEGIS_KNOWLEDGE_FILE": "", "AEGIS_API_URL": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.manager = SessionManager()
        self.addCleanup(self.manager.close)
        self.server_manager = patch.object(server, "session_manager", self.manager)
        self.server_manager.start()
        self.addCleanup(self.server_manager.stop)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)

    def chat(self, text):
        return self.manager.chat_result("fulfillment-guest", text, force_rules=True)

    def test_new_shortage_is_persisted_as_coordination_and_rejected_by_both_actions(self):
        reply = self.chat("1128 给我送7个吹风机").reply
        self.assertIn("库存不足", reply)
        ticket = self.manager.shared_tickets[0]
        self.assertEqual(ticket["delivery_status"], "待库存协调")
        self.assertEqual((ticket["quantity"], ticket["reserved_quantity"]), (7, 0))
        for action in ("dispatch", "complete"):
            response = self.client.post(f"/api/tickets/{ticket['ticket_id']}/{action}")
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.manager.housekeeping.inventory["吹风机"], 6)

    def test_staff_endpoints_project_same_details_without_changing_stored_ticket(self):
        self.chat("1128 给我送7个吹风机")
        original = deepcopy(self.manager.shared_tickets[0])
        tickets = self.client.get("/api/tickets").json()["tickets"]
        state = self.client.get("/api/operations/state").json()["state"]
        self.assertIn("fulfillment", tickets[0])
        self.assertEqual(tickets[0]["fulfillment"], state["tickets"][0]["fulfillment"])
        self.assertEqual(tickets[0]["fulfillment"]["items"][0]["shortage_quantity"], 1)
        self.assertEqual(self.manager.shared_tickets[0], original)
        self.assertNotIn("fulfillment", self.manager.shared_tickets[0])

    def test_legacy_erroneous_mixed_ticket_cannot_dispatch_or_complete(self):
        self.chat("1128 给我送两瓶水")
        ticket = self.manager.shared_tickets[0]
        ticket["item_detail"] = "我要两瓶水，两桶泡面，一桶泡面里装水"
        before = deepcopy(self.manager.housekeeping.inventory)
        for action in ("dispatch", "complete"):
            response = self.client.post(f"/api/tickets/{ticket['ticket_id']}/{action}")
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(ticket["delivery_status"], "待派送")
        self.assertEqual(self.manager.housekeeping.inventory, before)

    def test_pending_change_cannot_be_dispatched_even_if_stock_is_reserved(self):
        self.chat("1128 送两条毛巾")
        ticket = self.manager.shared_tickets[0]
        ticket["change_request"] = {"status": "待部门确认", "action": "cancel"}
        response = self.client.post(f"/api/tickets/{ticket['ticket_id']}/dispatch")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(ticket["delivery_status"], "待派送")

    def test_direct_completion_and_guest_confirmation_do_not_bypass_legacy_review(self):
        self.chat("1128 送两瓶水")
        ticket = self.manager.shared_tickets[0]
        ticket["item_detail"] = "我要两瓶水，两桶泡面，一桶泡面里装水"
        flow = self.manager.get_session("fulfillment-guest")
        with self.assertRaises(ValueError):
            flow.complete_ticket(ticket["ticket_id"])
        reply = self.chat("送到了，谢谢").reply
        self.assertIn("核验", reply)
        self.assertEqual(ticket["status"], "处理中")
        self.assertEqual(self.manager.housekeeping.inventory["矿泉水"], 34)

    def test_guest_cannot_read_staff_fulfillment_or_global_inventory(self):
        admin = "fulfillment-admin-" + "a" * 40
        signing = "fulfillment-sign-" + "b" * 40
        with patch.dict(os.environ, {"AEGIS_AUTH_ENABLED": "1", "AEGIS_STAFF_TOKEN": admin,
                                    "AEGIS_SIGNING_KEY": signing, "AEGIS_STAFF_KEYS_JSON": "{}"}):
            grant = self.client.post("/api/guest-access", headers={"Authorization": "Bearer " + admin},
                                     json={"room_no": "1128", "identity_checked": True}).json()
            guest = {"Authorization": "Bearer " + grant["access_token"]}
            response = self.client.post("/api/chat", headers=guest,
                json={"session_id": grant["session_id"], "message": "给我送7个吹风机", "force_rules": True})
            self.assertEqual(response.status_code, 200)
            for endpoint in ("/api/tickets", "/api/operations/state"):
                self.assertEqual(self.client.get(endpoint, headers=guest).status_code, 403)
            history = self.client.get("/api/session/" + grant["session_id"], headers=guest)
            self.assertEqual(history.status_code, 200)
            self.assertNotIn('"fulfillment"', history.text)
            self.assertNotIn('"available_quantity"', history.text)

    def test_valid_dispatch_and_completion_never_deduct_stock_twice(self):
        self.chat("1128 送两瓶水")
        ticket = self.manager.shared_tickets[0]
        self.assertEqual(self.client.post(f"/api/tickets/{ticket['ticket_id']}/dispatch").status_code, 200)
        self.assertEqual(self.client.post(f"/api/tickets/{ticket['ticket_id']}/dispatch").status_code, 200)
        self.assertEqual(self.client.post(f"/api/tickets/{ticket['ticket_id']}/complete").status_code, 200)
        self.assertEqual(self.manager.housekeeping.inventory["矿泉水"], 34)

    def test_shortage_replaced_with_available_quantity_becomes_ready(self):
        self.chat("1128 给我送7个吹风机")
        self.chat("吹风机改成3个")
        ticket = self.manager.shared_tickets[0]
        self.assertEqual((ticket["quantity"], ticket["reserved_quantity"], ticket["delivery_status"]), (3, 3, "待派送"))
        result = self.client.get("/api/tickets").json()["tickets"][0]
        self.assertEqual(result.get("fulfillment", {}).get("state"), "ready")

    def test_named_quantity_change_targets_shortage_among_multiple_active_orders(self):
        self.chat("1128 送两瓶水")
        self.chat("给我送7个吹风机")
        reply = self.chat("吹风机改成3个").reply
        self.assertIn("改为", reply)
        water, dryer = self.manager.shared_tickets
        self.assertEqual((water["quantity"], water["reserved_quantity"]), (2, 2))
        self.assertEqual((dryer["quantity"], dryer["reserved_quantity"], dryer["delivery_status"]), (3, 3, "待派送"))
        self.assertEqual((self.manager.housekeeping.inventory["矿泉水"], self.manager.housekeeping.inventory["吹风机"]), (34, 3))

    def test_water_alias_before_change_marker_identifies_its_own_order(self):
        self.chat("1128 送两瓶水")
        self.chat("送两条毛巾")
        self.assertIn("改为", self.chat("水改成1瓶").reply)
        self.assertEqual([(t["inventory_item"], t["quantity"]) for t in self.manager.shared_tickets],
                         [("矿泉水", 1), ("毛巾", 2)])
        self.assertEqual((self.manager.housekeeping.inventory["矿泉水"], self.manager.housekeeping.inventory["毛巾"]), (35, 22))

    def test_new_item_only_or_duplicate_target_orders_still_require_clarification(self):
        self.chat("1128 送两瓶水")
        self.chat("给我送7个吹风机")
        before = deepcopy(self.manager.shared_tickets)
        self.assertIn("说明", self.chat("改成3个吹风机").reply)
        self.assertEqual(self.manager.shared_tickets, before)
        self.chat("送1个吹风机")
        before = deepcopy(self.manager.shared_tickets)
        self.assertIn("哪一单", self.chat("吹风机改成3个").reply)
        self.assertEqual(self.manager.shared_tickets, before)

    def test_shortage_state_and_derived_details_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "hotel.sqlite3")
            first = SessionManager(db_path=path)
            try:
                first.chat_result("restart-guest", "1128 送7个吹风机", force_rules=True)
            finally:
                first.close()
            restored = SessionManager(db_path=path)
            try:
                ticket = restored.shared_tickets[0]
                self.assertEqual(ticket["delivery_status"], "待库存协调")
                module = importlib.import_module("ticket_details")
                details = module.delivery_fulfillment(ticket, restored.housekeeping.inventory)
                self.assertEqual(details["items"][0]["shortage_quantity"], 1)
                self.assertFalse(details["can_dispatch"])
            finally:
                restored.close()


if __name__ == "__main__":
    unittest.main()
