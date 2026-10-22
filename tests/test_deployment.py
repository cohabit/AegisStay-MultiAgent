"""Authenticated academic-hotel flows, storage failures and restart boundaries."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from agent_runtime import SessionManager
from api_client import HotelApi
from auth import validate_configuration
from persistence import StorageConflict
from scripts.manage import backup, initialize
from hotel_core import HotelKnowledge, HousekeepingAgent

ADMIN = "admin-test-" + "a" * 40
SIGNING = "signing-test-" + "b" * 40
ENGINEER = "engineer-test-" + "c" * 40


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        import json
        self.env = patch.dict(os.environ, {
            "AEGIS_FORCE_RULES": "1", "AEGIS_AUTH_ENABLED": "1", "AEGIS_DEPLOYED": "0",
            "AEGIS_DB_PATH": "", "AEGIS_SITE_PROFILE": "academic",
            "AEGIS_STAFF_TOKEN": ADMIN, "AEGIS_SIGNING_KEY": SIGNING,
            "AEGIS_STAFF_KEYS_JSON": json.dumps({ENGINEER: {"subject": "engineer-1", "role": "engineering"}}),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "hotel.sqlite3")
        self.manager = SessionManager(db_path=self.db)
        self.addCleanup(lambda: self.manager.close())
        self.binding = patch.object(server, "session_manager", self.manager)
        self.binding.start()
        self.addCleanup(self.binding.stop)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        self.staff = {"Authorization": "Bearer " + ADMIN}

    def grant(self, room="1128"):
        response = self.client.post("/api/guest-access", headers=self.staff,
                                    json={"room_no": room, "identity_checked": True, "ttl_seconds": 86400})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def chat(self, guest, message, request_id=None):
        return self.client.post("/api/chat", headers={"Authorization": "Bearer " + guest["access_token"]},
                                json={"session_id": guest["session_id"], "message": message, "request_id": request_id})

    def restart(self):
        self.manager.close()
        self.manager = SessionManager(db_path=self.db)
        server.session_manager = self.manager

    def test_missing_auth_and_shared_guest_token_cannot_access(self):
        self.assertEqual(self.client.get("/api/tickets").status_code, 401)
        self.assertEqual(self.client.get("/api/me", headers={"Authorization": "Bearer old-shared-token"}).status_code, 401)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_guest_is_bound_to_own_session_room_and_stay(self):
        first, other = self.grant(), self.grant("808")
        token = {"Authorization": "Bearer " + first["access_token"]}
        self.assertEqual(self.client.get("/api/session/" + other["session_id"], headers=token).status_code, 403)
        self.assertEqual(self.client.get("/api/operations/state", headers=token).status_code, 403)
        self.assertEqual(self.chat(first, "808 送水").status_code, 403)
        self.assertEqual(self.chat(first, "送两条毛巾").status_code, 200)
        self.assertEqual(len(self.manager.shared_tickets), 1)
        self.assertEqual(self.manager.shared_tickets[0]["room_no"], "1128")
        self.manager.shared_pms["1128"]["stay_id"] = "another-stay"
        self.assertEqual(self.client.get("/api/me", headers=token).status_code, 401)

    def test_revocation_and_expiry_invalidate_pass(self):
        guest = self.grant()
        token = {"Authorization": "Bearer " + guest["access_token"]}
        with patch("auth.time.time", return_value=guest["expires_at"] + 1):
            self.assertEqual(self.client.get("/api/me", headers=token).status_code, 401)
        self.client.post(f"/api/guest-access/{guest['grant_id']}/revoke", headers=self.staff)
        self.assertEqual(self.client.get("/api/me", headers=token).status_code, 401)

    def test_new_guest_does_not_read_previous_room_orders(self):
        previous = self.grant()
        self.assertEqual(self.chat(previous, "送两条毛巾").status_code, 200)
        current = self.grant()
        detail = self.client.get("/api/session/" + current["session_id"], headers={"Authorization": "Bearer " + current["access_token"]}).json()
        self.assertEqual(detail["tickets"], [])
        self.assertEqual(detail["messages"], [])
        self.assertEqual(detail["progress_items"], [])
        response = self.chat(current, "毛巾不用了")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.manager.get_run(current["session_id"], response.json()["run_id"]).target_ticket_id)
        self.assertEqual(self.manager.shared_tickets[0]["status"], "处理中")
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 22)

    def test_department_cannot_complete_other_department_ticket(self):
        guest = self.grant()
        self.chat(guest, "送两条毛巾")
        tid = self.manager.shared_tickets[0]["ticket_id"]
        response = self.client.post(f"/api/tickets/{tid}/complete", headers={"Authorization": "Bearer " + ENGINEER})
        self.assertEqual(response.status_code, 403)
        board = self.client.get("/api/operations/state", headers={"Authorization": "Bearer " + ENGINEER}).json()
        self.assertEqual(board["state"]["tickets"], [])
        self.assertNotIn("guest_name", board["state"]["pms_rooms"]["1128"])
        self.assertNotIn("stay_id", board["state"]["pms_rooms"]["1128"])
        self.assertEqual(self.client.get("/api/session/" + guest["session_id"], headers={"Authorization": "Bearer " + ENGINEER}).status_code, 403)
        self.assertEqual(self.client.get("/api/session/" + guest["session_id"] + "/messages", headers={"Authorization": "Bearer " + ENGINEER}).status_code, 403)

    def test_academic_meeting_and_checkout_need_real_confirmation(self):
        guest = self.grant()
        response = self.chat(guest, "会议签到在哪里？下午两点能退房吗？").json()
        self.assertIn("会务", response["reply"])
        self.assertIn("审核", response["reply"])
        self.assertEqual(self.manager.shared_pms["1128"]["checkout_time"], "12:00")
        request_id = self.manager.shared_checkout_requests[-1]["request_id"]
        result = self.client.post("/api/checkout/1128/review", headers=self.staff,
                                   json={"requested_time": "14:00", "approved": True, "expected_request_id": request_id})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn("费用按接待订单", self.manager.get_session(guest["session_id"]).state.messages[-1]["content"])
        self.restart()
        self.assertEqual(self.manager.shared_pms["1128"]["checkout_time"], "14:00")
        events = self.client.get("/api/audit", headers=self.staff).json()["events"]
        self.assertTrue(any(e["action"] == "checkout.review" and e["actor"] == "hotel-admin" for e in events))

    def test_academic_breakfast_is_not_an_automatic_entitlement(self):
        guest = self.grant()
        response = self.chat(guest, "给我两张早餐券")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("前台核验", response.json()["reply"])
        voucher = self.manager.shared_voucher_ledger[-1]
        self.assertEqual(voucher["status"], "需前台核验")
        self.assertEqual(voucher["owner_session_id"], guest["session_id"])
        self.assertEqual(voucher["stay_id"], guest["stay_id"])
        self.assertNotIn("已发放", response.json()["reply"])

    def test_request_retry_survives_restart_without_second_order(self):
        guest = self.grant()
        first = self.chat(guest, "送两条毛巾", "retry-same").json()
        self.restart()
        second = self.chat(guest, "送两条毛巾", "retry-same")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["run_id"], first["run_id"])
        self.assertEqual(len(self.manager.shared_tickets), 1)
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 22)
        self.assertEqual(self.chat(guest, "改成浴巾", "retry-same").status_code, 409)

    def test_write_failure_rolls_back_stock_and_does_not_ack_success(self):
        guest = self.grant()
        checkpoint = self.manager.store.checkpoint

        def failing(*args, **kwargs):
            event = kwargs.get("event") or (args[4] if len(args) > 4 else None)
            if event and event["action"] == "task.commit":
                raise sqlite3.OperationalError("injected disk failure")
            return checkpoint(*args, **kwargs)

        with patch.object(self.manager.store, "checkpoint", side_effect=failing):
            response = self.chat(guest, "送两条毛巾").json()
        self.assertEqual(response["run_status"], "failed")
        self.assertNotIn("已为您创建", response["reply"])
        self.assertEqual(self.manager.shared_tickets, [])
        self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)
        self.restart()
        self.assertEqual(self.manager.shared_tickets, [])

    def test_second_writer_is_rejected_instead_of_overwriting(self):
        with self.assertRaises(StorageConflict):
            SessionManager(db_path=self.db)

    def test_stale_change_confirmation_and_pending_completion_are_rejected(self):
        guest = self.grant()
        self.chat(guest, "送两条毛巾")
        tid = self.manager.shared_tickets[0]["ticket_id"]
        self.client.post(f"/api/tickets/{tid}/dispatch", headers=self.staff)
        self.chat(guest, "不要毛巾，改成浴巾")
        change = self.manager.shared_tickets[0]["change_request"]["change_id"]
        self.assertEqual(self.client.post(f"/api/tickets/{tid}/complete", headers=self.staff).status_code, 409)
        self.chat(guest, "不要毛巾，改成矿泉水")
        result = self.client.post(f"/api/tickets/{tid}/change/confirm", headers=self.staff,
                                  json={"approved": True, "expected_change_id": change})
        self.assertEqual(result.status_code, 409)

    def test_history_survives_window_compaction_and_can_be_paged(self):
        guest = self.grant()
        for index in range(8):
            self.chat(guest, f"第 {index} 次请问会议签到在哪里")
        sid = guest["session_id"]
        self.assertLess(len(self.manager.get_session(sid).state.messages), 16)
        self.restart()
        api = HotelApi("http://testserver", guest["access_token"], client=self.client)
        messages = api.request("GET", f"/api/session/{sid}/messages")["messages"]
        self.assertEqual(len(messages), 16)
        older = api.request("GET", f"/api/session/{sid}/messages", params={"before": messages[8]["sequence"], "limit": 8})["messages"]
        self.assertEqual(len(older), 8)

    def test_night_audit_cannot_be_repeated_with_stale_date(self):
        date = self.manager.current_date
        self.assertEqual(self.client.post("/api/pms/night-audit", headers=self.staff, json={"expected_date": date}).status_code, 200)
        self.assertEqual(self.client.post("/api/pms/night-audit", headers=self.staff, json={"expected_date": date}).status_code, 409)
        expected = self.manager.current_date
        self.restart()
        self.assertEqual(self.manager.current_date, expected)

    def test_validated_backup_includes_latest_committed_order(self):
        guest = self.grant()
        self.chat(guest, "送两条毛巾")
        destination = Path(self.temp.name) / "copy.sqlite3"
        backup(Path(self.db), destination)
        restored = SessionManager(db_path=str(destination))
        try:
            self.assertEqual(len(restored.shared_tickets), 1)
            self.assertEqual(restored.housekeeping.inventory["毛巾"], 22)
        finally:
            restored.close()

    def test_secret_generation_never_overwrites_deployment(self):
        output = Path(self.temp.name) / "deploy.env"
        initialize(output)
        original = output.read_text()
        with self.assertRaises(FileExistsError):
            initialize(output)
        self.assertEqual(output.read_text(), original)

    def test_deployed_mode_rejects_missing_credentials(self):
        with patch.dict(os.environ, {"AEGIS_DEPLOYED": "1", "AEGIS_DB_PATH": self.db, "AEGIS_STAFF_TOKEN": "replace-with-token"}):
            with self.assertRaises(RuntimeError):
                validate_configuration()

    def test_turnover_revokes_previous_occupant(self):
        old = self.grant()
        result = self.client.post("/api/rooms/1128/stay", headers=self.staff, json={
            "guest_alias": "来访教师", "checkout_date": "2030-10-30", "order_reference": "VISIT-001",
            "identity_checked": True, "replace_existing": True,
        })
        self.assertEqual(result.status_code, 200, result.text)
        self.assertNotEqual(result.json()["stay_id"], old["stay_id"])
        self.assertEqual(self.client.get("/api/me", headers={"Authorization": "Bearer " + old["access_token"]}).status_code, 401)
        self.assertEqual(self.grant()["stay_id"], result.json()["stay_id"])

    def test_revoked_inflight_request_cannot_commit(self):
        guest = self.grant()
        started, release = threading.Event(), threading.Event()
        original = HousekeepingAgent.dispatch

        def blocked(agent, *args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test worker was not released")
            return original(agent, *args, **kwargs)

        try:
            with patch.object(HousekeepingAgent, "dispatch", blocked):
                response = self.client.post("/api/chat/submit", headers={"Authorization": "Bearer " + guest["access_token"]},
                                            json={"session_id": guest["session_id"], "message": "送两条毛巾"})
                self.assertEqual(response.status_code, 202)
                self.assertTrue(started.wait(2))
                self.manager.revoke_guest_access(guest["grant_id"])
                release.set()
                result = self.manager.wait(guest["session_id"], response.json()["run_id"], timeout=3)
                self.assertEqual(result.status, "superseded")
            self.assertEqual(self.manager.shared_tickets, [])
            self.assertEqual(self.manager.housekeeping.inventory["毛巾"], 24)
        finally:
            release.set()

    def test_atomic_checkpoint_rolls_back_if_session_write_fails(self):
        store = self.manager.store
        before = store.load_business()
        with store._connection:
            store._connection.execute("CREATE TRIGGER abort_session BEFORE INSERT ON aegis_sessions BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        payload = self.manager._business_payload().copy()
        payload["current_date"] = "2030-01-01"
        with self.assertRaises(sqlite3.IntegrityError):
            store.checkpoint(payload, [{"session_id": "fail", "state": {"messages": []}, "client_type": "guest", "ttl_seconds": 60, "updated_at": time.time()}], [])
        self.assertEqual(store.load_business(), before)
        with store._connection:
            store._connection.execute("DROP TRIGGER abort_session")
        self.manager.persist()

    def test_pruned_run_keeps_durable_idempotency_record(self):
        guest = self.grant()
        first = self.chat(guest, "送两条毛巾", "retained-id").json()
        sid = guest["session_id"]
        with self.manager.lock:
            self.manager._runs.pop(first["run_id"])
            self.manager._requests.pop((sid, "retained-id"))
        response = self.chat(guest, "送两条毛巾", "retained-id")
        self.assertEqual(response.json()["run_id"], first["run_id"])
        self.assertEqual(len(self.manager.shared_tickets), 1)

    def test_guest_submission_rate_is_bounded(self):
        guest = self.grant()
        with server._rate_lock:
            server._recent_requests[guest["grant_id"]] = __import__("collections").deque([time.monotonic()] * 20)
        self.assertEqual(self.chat(guest, "送毛巾").status_code, 429)

    def test_checkout_approval_is_bound_to_current_occupant_request(self):
        previous = self.grant()
        self.chat(previous, "下午两点退房")
        old_id = self.manager.shared_checkout_requests[-1]["request_id"]
        result = self.client.post("/api/rooms/1128/stay", headers=self.staff, json={
            "guest_alias": "参会教师", "checkout_date": "2030-10-30", "order_reference": "MEETING-002",
            "identity_checked": True, "replace_existing": True,
        })
        self.assertEqual(result.status_code, 200)
        current = self.grant()
        self.chat(current, "下午两点退房")
        new_id = self.manager.shared_checkout_requests[-1]["request_id"]
        self.assertNotEqual(old_id, new_id)
        self.assertEqual(len(self.manager.shared_checkout_requests), 2)
        body = {"requested_time": "14:00", "approved": True}
        self.assertEqual(self.client.post("/api/checkout/1128/review", headers=self.staff, json=body).status_code, 400)
        self.assertEqual(self.client.post("/api/checkout/1128/review", headers=self.staff,
                                         json={**body, "expected_request_id": old_id}).status_code, 409)
        self.assertEqual(self.client.post("/api/checkout/1128/review", headers=self.staff,
                                         json={**body, "expected_request_id": new_id}).status_code, 200)
        self.assertEqual(self.manager.shared_checkout_requests[0]["status"], "需前台审核")
        self.assertEqual(self.manager.shared_checkout_requests[1]["status"], "已批准")

    def test_staff_storage_failure_is_unavailable_and_rolls_back(self):
        guest = self.grant()
        self.chat(guest, "送两条毛巾")
        tid = self.manager.shared_tickets[0]["ticket_id"]
        with patch.object(self.manager.store, "checkpoint", side_effect=sqlite3.OperationalError("injected failure")):
            response = self.client.post(f"/api/tickets/{tid}/dispatch", headers=self.staff)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.manager.shared_tickets[0]["delivery_status"], "待派送")
        self.assertEqual(self.client.get("/ready").status_code, 503)

    def test_invalid_staff_mapping_fails_configuration_validation(self):
        for value in ("[]", '{"short":{"role":"engineering","subject":"e1"}}', '{"bad":null}'):
            with self.subTest(value=value), patch.dict(os.environ, {"AEGIS_STAFF_KEYS_JSON": value}):
                with self.assertRaises(RuntimeError):
                    validate_configuration()

    def test_invalid_knowledge_file_is_rejected(self):
        path = Path(self.temp.name) / "bad-policy.json"
        path.write_text('{"fallback":"请联系前台","entries":[{"id":"same","answer":"test","keywords":"会务"}]}', encoding="utf-8")
        with self.assertRaises(ValueError):
            HotelKnowledge(path)

    def test_initial_business_date_is_configurable_and_retained(self):
        path = str(Path(self.temp.name) / "date.sqlite3")
        with patch.dict(os.environ, {"AEGIS_BUSINESS_DATE": "2026-10-23"}):
            first = SessionManager(db_path=path)
        try:
            self.assertEqual(first.current_date, "2026-10-23")
        finally:
            first.close()
        with patch.dict(os.environ, {"AEGIS_BUSINESS_DATE": "2026-10-24"}):
            recovered = SessionManager(db_path=path)
        try:
            self.assertEqual(recovered.current_date, "2026-10-23")
        finally:
            recovered.close()


class DeployedUIBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"AEGIS_API_URL": "http://hotel-api", "AEGIS_DEPLOYED": "0",
                         "AEGIS_AUTH_ENABLED": "1", "AEGIS_FORCE_RULES": "1", "AEGIS_SITE_PROFILE": "academic",
                         "AEGIS_STAFF_TOKEN": ADMIN, "AEGIS_SIGNING_KEY": SIGNING, "AEGIS_DB_PATH": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.manager = SessionManager(db_path=str(Path(self.temp.name) / "ui.sqlite3"))
        self.addCleanup(self.manager.close)
        self.binding = patch.object(server, "session_manager", self.manager)
        self.binding.start()
        self.addCleanup(self.binding.stop)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        from urllib.parse import urlsplit

        def route(method, url, **kwargs):
            kwargs.pop("timeout", None)
            return self.client.request(method, urlsplit(url).path, **kwargs)
        self.http = patch("api_client.httpx.request", side_effect=route)
        self.http.start()
        self.addCleanup(self.http.stop)

    def app(self, token=""):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15)
        app.session_state["access_token"] = token
        return app.run()

    def test_no_credential_does_not_create_unprotected_business_ui(self):
        app = self.app()
        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.chat_input), 0)
        self.assertEqual(self.manager.sessions, {})

    def test_guest_ui_only_receives_own_service_view(self):
        record = self.client.post("/api/guest-access", headers={"Authorization": "Bearer " + ADMIN},
                                   json={"room_no": "1128", "identity_checked": True}).json()
        app = self.app(record["access_token"])
        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.chat_input), 1)
        self.assertFalse(any("签发" in b.label for b in app.button))
        self.assertFalse(any("运营工作台" == tab.label for tab in app.tabs))

    def test_staff_ui_shares_api_without_a_second_writer(self):
        app = self.app(ADMIN)
        self.assertEqual(list(app.exception), [])
        self.assertTrue(any("签发" in b.label for b in app.button))
        self.assertFalse("agent_runtime" in app.session_state)
