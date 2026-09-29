import unittest

from fastapi.testclient import TestClient

from server import app, session_manager


class FastAPISessionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        session_manager.reset_all()

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
