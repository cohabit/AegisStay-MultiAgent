"""Executable delivery details in the real local and API operations views."""
from __future__ import annotations

from copy import deepcopy
import os
import re
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def delivery(**changes):
    ticket = {
        "ticket_id": "OT-water", "room_no": "1128", "category": "送物",
        "item_detail": "请送两瓶水", "assigned_dept": "客房部", "priority": "低",
        "status": "处理中", "delivery_status": "待派送", "agent": "HousekeepingAgent",
        "inventory_item": "矿泉水", "quantity": 2, "reserved_quantity": 2,
        "is_borrowable": False,
    }
    ticket.update(changes)
    return ticket


class SyntheticHotelApi:
    """Read-only synthetic operations endpoint; any accidental mutation fails."""
    def __init__(self, tickets, inventory):
        self.payload = {
            "state": {"current_date": "2026-10-01", "pms_rooms": {},
                      "tickets": deepcopy(tickets), "checkout_requests": [], "agent_conflicts": []},
            "inventory": deepcopy(inventory), "quarantine": {},
        }

    def request(self, method, path, **kwargs):
        if (method, path) != ("GET", "/api/operations/state"):
            raise AssertionError(f"Unexpected external operation: {method} {path}")
        return deepcopy(self.payload)


def local_operations():
    import streamlit as st
    from app import _render_staff, _runtime_for_ui
    from hotel_core import HotelOrchestrator

    if "fixture_orchestrator" not in st.session_state:
        flow = HotelOrchestrator(force_rules=True)
        runtime = _runtime_for_ui(flow)
        runtime.shared_tickets[:] = st.session_state["fixture_tickets"]
        runtime.shared_pms.clear()
        flow.housekeeping.inventory.update(st.session_state["fixture_inventory"])
        st.session_state.fixture_orchestrator = flow
    _render_staff(st.session_state.fixture_orchestrator)


def api_operations():
    import streamlit as st
    from deployed_ui import operations

    operations(st.session_state["fixture_api"], {"role": "housekeeping"})


class OperationsTicketAssertions:
    surface = None

    def setUp(self):
        env = patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1", "AEGIS_API_URL": "",
                                     "AEGIS_DB_PATH": "", "AEGIS_DEPLOYED": "0",
                                     "AEGIS_AUTH_ENABLED": "0", "AEGIS_SITE_PROFILE": "standard",
                                     "AEGIS_PMS_FILE": "", "AEGIS_KNOWLEDGE_FILE": "",
                                     "AEGIS_BUSINESS_DATE": ""})
        env.start()
        self.addCleanup(env.stop)

    def render(self, tickets, inventory):
        script = local_operations if self.surface == "local" else api_operations
        app = AppTest.from_function(script, default_timeout=15)
        app.session_state["fixture_tickets"] = deepcopy(tickets)
        app.session_state["fixture_inventory"] = deepcopy(inventory)
        app.session_state["fixture_api"] = SyntheticHotelApi(tickets, inventory)
        app.run()
        if self.surface == "local":
            self.addCleanup(app.session_state["agent_runtime"].close)
        self.assertEqual(list(app.exception), [])
        self.app = app
        return app

    def text(self):
        values = (str(node.value) for kind in ("markdown", "caption", "text", "warning", "info", "success")
                  for node in self.app.get(kind))
        body = "\n".join(re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "[image]", value) for value in values)
        return body + "\n" + "\n".join(panel.label for panel in self.app.expander)

    def actions(self, tid):
        keys = {f"dispatch-{tid}", f"close-{tid}", f"complete-{tid}"}
        return [button for button in self.app.button if button.key in keys]

    def assert_blocked(self, tid):
        self.assertFalse(any(not button.disabled for button in self.actions(tid)),
                         "Unreserved, historical or closed delivery must not allow dispatch/completion")

    def test_reserved_water_shows_canonical_quantities_even_when_available_zero(self):
        self.render([delivery()], {"矿泉水": 0})
        text = self.text()
        for expected in ("执行清单", "矿泉水", "申请 2 瓶", "已预留 2 瓶", "当前可用 0 瓶", "缺口 0 瓶", "无需补货", "原始需求"):
            self.assertIn(expected, text)
        self.assertEqual(len(self.actions("OT-water")), 2)
        self.assertTrue(all(not button.disabled for button in self.actions("OT-water")))

    def test_shortage_is_coordination_with_gap_and_no_dispatch(self):
        self.render([delivery(ticket_id="OT-short", inventory_item="吹风机", quantity=7,
                              reserved_quantity=0, item_detail="借七个吹风机", is_borrowable=True)], {"吹风机": 6})
        for expected in ("吹风机", "申请 7 个", "已预留 0 个", "当前可用 6 个", "缺口 1 个", "需补货", "待库存协调", "未确认派送"):
            self.assertIn(expected, self.text())
        self.assert_blocked("OT-short")

    def test_zero_stock_and_recovered_but_unreserved_have_distinct_states(self):
        for available, label, shortage in ((0, "待库存协调", "缺口 2 瓶"), (6, "待库存预留", "缺口 0 瓶")):
            with self.subTest(available=available):
                self.render([delivery(reserved_quantity=0)], {"矿泉水": available})
                self.assertIn(label, self.text())
                self.assertIn(shortage, self.text())
                self.assert_blocked("OT-water")

    def test_historical_unknown_and_missing_fields_require_verification(self):
        missing = delivery()
        missing.pop("quantity")
        mixed = delivery(item_detail="送两瓶水和一个没有登记的设备")
        unknown = delivery(inventory_item="未知设备", item_detail="送两个未知设备")
        for ticket in (missing, mixed, unknown):
            with self.subTest(ticket=ticket):
                self.render([ticket], {"矿泉水": 4})
                self.assertIn("待人工核验", self.text())
                self.assert_blocked("OT-water")

    def test_closed_orders_have_no_active_replenishment_request(self):
        for status, progress in (("已取消", "已取消"), ("已结单", "已送达")):
            with self.subTest(status=status):
                app = self.render([delivery(status=status, delivery_status=progress, reserved_quantity=0)], {"矿泉水": 0})
                if self.surface == "api":
                    app.selectbox(key="op-status").select("全部").run()
                self.assertIn("执行清单", self.text())
                self.assertNotIn("需补货：", self.text())
                self.assertNotIn("待库存协调，未确认派送", self.text())
                self.assert_blocked("OT-water")

    def test_pending_change_disables_completion_and_preserves_confirmation(self):
        ticket = delivery(change_request={"status": "待部门确认", "action": "cancel", "change_id": "change-1"})
        self.render([ticket], {"矿泉水": 0})
        self.assertIn("执行清单", self.text())
        self.assert_blocked("OT-water")
        self.assertTrue(any("撤回并" in button.label and not button.disabled for button in self.app.button))

    def test_each_order_keeps_its_own_item_quantity_and_in_transit_completion(self):
        self.render([delivery(), delivery(ticket_id="OT-towel", inventory_item="浴巾", quantity=3,
                                          reserved_quantity=3, item_detail="送三条浴巾", delivery_status="配送中")],
                    {"矿泉水": 4, "浴巾": 0})
        self.assertIn("矿泉水 · 申请 2 瓶", self.text())
        self.assertIn("浴巾 · 申请 3 条", self.text())
        self.assertTrue(any(not button.disabled for button in self.actions("OT-towel")))
        self.assertFalse(any(button.key == "dispatch-OT-towel" and not button.disabled for button in self.app.button))

    def test_engineering_ticket_keeps_ordinary_completion(self):
        ticket = {"ticket_id": "OT-engineer", "room_no": "1128", "category": "工程报修", "item_detail": "灯不亮",
                  "assigned_dept": "工程部", "priority": "中", "status": "处理中", "agent": "EngineeringAgent"}
        self.render([ticket], {})
        self.assertTrue(any(not button.disabled for button in self.actions("OT-engineer")))
        self.assertNotIn("执行清单", self.text())

    def test_revised_order_recomputes_item_and_removes_old_shortage(self):
        old = delivery(inventory_item="吹风机", quantity=7, reserved_quantity=0,
                       item_detail="借七个吹风机", is_borrowable=True)
        self.render([old], {"吹风机": 6, "浴巾": 0})
        self.assertIn("缺口 1 个", self.text())
        revised = delivery(inventory_item="浴巾", quantity=3, reserved_quantity=3, item_detail="送三条浴巾")
        if self.surface == "local":
            self.app.session_state["agent_runtime"].shared_tickets[:] = [revised]
        else:
            self.app.session_state["fixture_api"].payload["state"]["tickets"] = [revised]
        self.app.run()
        self.assertEqual(list(self.app.exception), [])
        self.assertIn("浴巾 · 申请 3 条", self.text())
        self.assertNotIn("缺口 1 个", self.text())
        self.assertTrue(all(not button.disabled for button in self.actions("OT-water")))

    def test_dynamic_ticket_quote_cannot_inject_html(self):
        self.render([delivery(item_detail='<img src=x onerror="alert(1)">送两瓶水',
                              room_no="<b>1128</b>", assigned_dept="<svg onload=alert(1)>")], {"矿泉水": 0})
        for node in self.app.markdown:
            self.assertNotIn("<img src=x", node.value)
            self.assertNotIn("<b>1128</b>", node.value)
            self.assertNotIn("<svg onload=", node.value)
        self.assertIn("原始需求", self.text())


class LocalOperationsTicketUITests(OperationsTicketAssertions, unittest.TestCase):
    surface = "local"

    def test_shortage_coordination_is_not_counted_as_dispatch_queue_or_eta(self):
        self.render([delivery(inventory_item="吹风机", quantity=7, reserved_quantity=0,
                              item_detail="借七个吹风机", is_borrowable=True)], {"吹风机": 6})
        metrics = {metric.label: metric.value for metric in self.app.metric}
        self.assertEqual(metrics["送物队列排队深度"], "0 单待送")
        self.assertEqual(metrics["当前动态推导 ETA"], "—")


class ApiOperationsTicketUITests(OperationsTicketAssertions, unittest.TestCase):
    surface = "api"


if __name__ == "__main__":
    unittest.main()
