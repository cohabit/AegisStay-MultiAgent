import unittest
import threading
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import app, session_manager
from hotel_core import EngineeringAgent, HousekeepingAgent


class FastAPISessionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        session_manager.reset_all()

    def test_async_submit_accepts_replacement_and_poll_enforces_session(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = HousekeepingAgent.dispatch

        def dispatch(agent, room, detail, state):
            if "毛巾" in detail:
                started.set()
                if not release.wait(4):
                    raise RuntimeError("old API worker was not released")
            return original(agent, room, detail, state)

        with patch.object(HousekeepingAgent, "dispatch", dispatch):
            first = self.client.post("/api/chat/submit", json={
                "session_id": "async", "message": "1128 送两条毛巾", "force_rules": True,
            })
            self.assertEqual(first.status_code, 202)
            old_id = first.json()["run_id"]
            self.assertTrue(started.wait(2))
            poll = self.client.get(f"/api/tasks/{old_id}", params={"session_id": "async"})
            self.assertEqual(poll.json()["status"], "running")
            self.assertNotIn("result", poll.json())
            payload = {"session_id": "async", "message": "不要毛巾了，我要浴巾",
                       "force_rules": True, "request_id": "change-1"}
            second = self.client.post("/api/chat/submit", json=payload)
            self.assertEqual(second.status_code, 202)
            latest = second.json()
            session_manager.wait("async", latest["run_id"], timeout=2)
            result = self.client.get(f"/api/tasks/{latest['run_id']}", params={"session_id": "async"}).json()
            self.assertEqual(result["result"]["version"], 2)
            self.assertEqual(result["result"]["run_status"], "completed")
            self.assertEqual(result["task_id"], first.json()["task_id"])
            self.assertEqual(self.client.get(f"/api/tasks/{old_id}", params={"session_id": "other"}).status_code, 404)
            self.assertEqual(self.client.post("/api/chat/submit", json=payload).json()["run_id"], latest["run_id"])
            payload["message"] = "改成矿泉水"
            self.assertEqual(self.client.post("/api/chat/submit", json=payload).status_code, 409)
            self.assertEqual(session_manager.shared_tickets[0]["inventory_item"], "浴巾")
            self.assertEqual(len(session_manager.shared_tickets), 1)
            release.set()

    def test_dispatched_change_confirmation_is_idempotent(self):
        self.client.post("/api/chat", json={"session_id": "change", "message": "1128 送两条毛巾", "force_rules": True})
        ticket_id = session_manager.shared_tickets[0]["ticket_id"]
        self.assertEqual(self.client.post(f"/api/tickets/{ticket_id}/dispatch").status_code, 200)
        response = self.client.post("/api/chat", json={"session_id": "change", "message": "不要毛巾了，改成浴巾", "force_rules": True})
        self.assertIn("尚未确认取消成功", response.json()["reply"])
        confirmation = self.client.post(f"/api/tickets/{ticket_id}/change/confirm", json={"approved": True})
        self.assertEqual(confirmation.status_code, 200)
        self.assertEqual(confirmation.json()["ticket"]["inventory_item"], "浴巾")
        self.client.post(f"/api/tickets/{ticket_id}/change/confirm", json={"approved": True})
        self.assertEqual(session_manager.housekeeping.inventory["毛巾"], 24)
        self.assertEqual(session_manager.housekeeping.inventory["浴巾"], 22)

    def test_cancelled_delivery_cannot_be_completed(self):
        self.client.post("/api/chat", json={"session_id": "cancel", "message": "1128 送毛巾", "force_rules": True})
        ticket_id = session_manager.shared_tickets[0]["ticket_id"]
        self.client.post("/api/chat", json={"session_id": "cancel", "message": "不用了", "force_rules": True})
        response = self.client.post(f"/api/tickets/{ticket_id}/complete")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(session_manager.shared_tickets[0]["status"], "已取消")

    def test_out_of_stock_ticket_cannot_be_dispatched_or_marked_delivered(self):
        response = self.client.post("/api/chat", json={"session_id": "shortage", "message": "1128 送100条毛巾", "force_rules": True})
        self.assertIn("库存不足", response.json()["reply"])
        ticket = session_manager.shared_tickets[0]
        self.assertEqual(self.client.post(f"/api/tickets/{ticket['ticket_id']}/dispatch").status_code, 409)
        self.assertEqual(self.client.post(f"/api/tickets/{ticket['ticket_id']}/complete").status_code, 409)
        self.assertEqual(ticket["status"], "处理中")
        self.assertEqual(session_manager.housekeeping.inventory["毛巾"], 24)
        receipt = self.client.post("/api/chat", json={"session_id": "shortage", "message": "毛巾送到了", "force_rules": True})
        self.assertIn("暂未标记完成", receipt.json()["reply"])
        self.assertEqual(ticket["status"], "处理中")

    def test_conflicting_child_is_visible_as_review_without_order(self):
        original = EngineeringAgent.diagnose

        def wrong_room(agent, *args, **kwargs):
            ticket = original(agent, *args, **kwargs)
            ticket["room_no"] = "808"
            return ticket

        with patch.object(EngineeringAgent, "diagnose", wrong_room):
            response = self.client.post("/api/chat", json={"session_id": "conflict", "message": "1128 空调坏了", "force_rules": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run_status"], "needs_review")
        detail = self.client.get("/api/session/conflict").json()
        self.assertEqual(detail["tickets"], [])
        self.assertEqual(len(detail["agent_conflicts"]), 1)
        self.assertEqual(detail["agent_conflicts"][0]["status"], "需人工核验")

    def test_multi_session_isolation(self):
        response_a = self.client.post(
            "/api/chat",
            json={"session_id": "sess_A", "message": "1106 送毛巾"},
        )
        self.assertEqual(response_a.status_code, 200)
        data_a = response_a.json()
        self.assertEqual(data_a["room_no"], "1106")
        self.assertEqual(data_a["active_agent"], "HousekeepingAgent")

        response_b = self.client.post(
            "/api/chat",
            json={"session_id": "sess_B", "message": "1128 房间空调坏了"},
        )
        self.assertEqual(response_b.status_code, 200)
        data_b = response_b.json()
        self.assertEqual(data_b["room_no"], "1128")
        self.assertEqual(data_b["active_agent"], "EngineeringAgent")

        detail_a = self.client.get("/api/session/sess_A")
        self.assertEqual(detail_a.status_code, 200)
        detail_data_a = detail_a.json()
        self.assertEqual(detail_data_a["room_no"], "1106")
        self.assertTrue(all("1128" not in m["content"] for m in detail_data_a["messages"]))

        detail_b = self.client.get("/api/session/sess_B")
        self.assertEqual(detail_b.status_code, 200)
        self.assertTrue(all("1106" not in m["content"] for m in detail_b.json()["messages"]))

    def test_cross_room_api_intercept(self):
        self.client.post("/api/chat", json={"session_id": "sess_safe", "message": "我是 1102"})
        response = self.client.post(
            "/api/chat",
            json={"session_id": "sess_safe", "message": "帮 1208 房间送水"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("拦截跨房", response.json()["reply"])

    def test_session_reset(self):
        self.client.post("/api/chat", json={"session_id": "sess_reset", "message": "1106 送毛巾"})
        reset_response = self.client.post(
            "/api/session/reset", json={"session_id": "sess_reset"}
        )
        self.assertEqual(reset_response.status_code, 200)
        self.assertEqual(reset_response.json()["status"], "success")
        detail_response = self.client.get("/api/session/sess_reset")
        self.assertEqual(detail_response.status_code, 404)

    def test_shared_pms_overview_and_ticket_flow(self):
        self.client.post("/api/chat", json={"session_id": "sess_1", "message": "1106 卫生间漏水"})
        self.client.post("/api/chat", json={"session_id": "sess_2", "message": "1128 送两条毛巾"})

        overview = self.client.get("/api/pms/overview")
        self.assertEqual(overview.status_code, 200)
        overview_data = overview.json()
        self.assertEqual(overview_data["pending_tickets"], 2)
        self.assertEqual(overview_data["department_agents"]["EngineeringAgent"]["active_tasks"], 1)
        self.assertEqual(overview_data["department_agents"]["HousekeepingAgent"]["active_tasks"], 1)

        tickets = self.client.get("/api/tickets?status=处理中")
        self.assertEqual(tickets.status_code, 200)
        self.assertEqual(tickets.json()["total"], 2)
        ticket_id = tickets.json()["tickets"][0]["ticket_id"]
        complete_response = self.client.post(f"/api/tickets/{ticket_id}/complete")
        self.assertEqual(complete_response.status_code, 200)

        new_overview = self.client.get("/api/pms/overview").json()
        self.assertEqual(new_overview["pending_tickets"], 1)


if __name__ == "__main__":
    unittest.main()
