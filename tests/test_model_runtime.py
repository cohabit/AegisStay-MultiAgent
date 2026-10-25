"""Selected-model inference evidence, switching lifetime and oral preferences."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import hotel_core
import app
from agent_runtime import RuntimeBusy, SessionManager


class ModelRuntimeTests(unittest.TestCase):
    def client(self, create=None, *, model="Qwen/Qwen3.5-27B", base="https://api.siliconflow.cn/v1"):
        client = hotel_core.OpenAICompatibleClient.__new__(hotel_core.OpenAICompatibleClient)
        client.disabled, client.model, client.base_url = False, model, base
        client.initialization_error, client.last_probe, client.last_call = None, None, None
        client.transport, client.call_count = "httpx", 0
        create = create or Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]))
        client.client = SimpleNamespace(models=SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id=model)])),
                                        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return client

    def test_qwen35_intent_uses_bounded_non_thinking_request(self):
        client = self.client()
        self.assertEqual(client.json_extract("JSON", "hello"), {"ok": True})
        kwargs = client.client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 512)
        self.assertEqual(kwargs["extra_body"], {"enable_thinking": False})
        self.assertTrue(client.last_call["ok"])
        self.assertEqual(client.last_call["model"], client.model)
        self.assertGreaterEqual(client.last_call["elapsed_ms"], 0)

    def test_qwen25_and_other_provider_do_not_receive_siliconflow_options(self):
        for model, base in (("Qwen/Qwen2.5-7B-Instruct", "https://api.siliconflow.cn/v1"),
                            ("Qwen/Qwen3.5-27B", "https://other.invalid/v1")):
            with self.subTest(model=model, base=base):
                client = self.client(model=model, base=base)
                client.json_extract("JSON", "hello")
                self.assertNotIn("extra_body", client.client.chat.completions.create.call_args.kwargs)

    def test_http_transport_flattens_sdk_extra_body(self):
        client = hotel_core._HttpxOpenAICompatClient(api_key="synthetic", base_url="https://api.siliconflow.cn/v1")
        client._request = Mock(return_value={"choices": [{"message": {"content": '{"ok":true}'}}]})
        client.chat.completions.create(model="Qwen/Qwen3.5-27B", extra_body={"enable_thinking": False})
        payload = client._request.call_args.args[2]
        self.assertIs(payload["enable_thinking"], False)
        self.assertNotIn("extra_body", payload)

    def test_http_failure_is_observable_without_exception_body_or_key(self):
        error = RuntimeError("sk-do-not-print request body")
        error.status_code = 401
        client = self.client(Mock(side_effect=error))
        self.assertIsNone(client.json_extract("JSON", "hello"))
        self.assertFalse(client.last_call["ok"])
        self.assertEqual(client.last_call["code"], "http_401")
        self.assertNotIn("sk-do-not-print", json.dumps(client.last_call))

    def test_invalid_json_is_reported_as_format_failure(self):
        client = self.client(Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not JSON"))])))
        self.assertIsNone(client.json_extract("JSON", "hello"))
        self.assertEqual(client.last_call["code"], "invalid_json")

    def test_connection_probe_requires_actual_selected_model_inference(self):
        client = self.client()
        probe = client.probe(inference=True)
        self.assertTrue(probe["ok"])
        self.assertTrue(probe["inference_verified"])
        self.assertEqual(client.client.chat.completions.create.call_count, 1)

    def test_listed_model_with_timed_out_inference_is_not_online(self):
        client = self.client(Mock(side_effect=TimeoutError("private detail")))
        probe = client.probe(inference=True)
        self.assertFalse(probe["ok"])
        self.assertEqual(probe["code"], "timeout")

    def test_probe_rejects_valid_json_without_expected_acknowledgement(self):
        client = self.client(Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":false}'))])))
        self.assertEqual(client.probe(inference=True)["code"], "invalid_response")

    def test_sidebar_distinguishes_configuration_success_and_failure(self):
        client = self.client()
        flow = SimpleNamespace(model=client, state=SimpleNamespace(model_mode="规则护栏 · 身份边界"))
        self.assertEqual(app._model_status(flow)["level"], "info")
        self.assertIn("待实测", app._model_status(flow)["title"])
        client.probe(inference=True)
        self.assertEqual(app._model_status(flow)["level"], "success")
        for code in ("http_401", "http_429", "timeout", "invalid_json"):
            client.last_call = {"ok": False, "code": code, "elapsed_ms": 123, "purpose": "understand"}
            status = app._model_status(flow)
            self.assertEqual(status["level"], "warning")
            self.assertIn(code, status["detail"])
            self.assertIn("123", status["detail"])
            self.assertIn("规则护栏", status["detail"])

    def test_local_identity_turn_does_not_reuse_previous_cloud_mode(self):
        with patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1"}):
            flow = hotel_core.HotelOrchestrator(force_rules=True)
        flow.state.model_mode = "云模型 + 规则校验"
        flow.model.json_extract = Mock(side_effect=AssertionError("public identity stays local"))
        self.assertIn("学术之家", flow.process("你是什么模型"))
        self.assertIn("未调用云端", flow.state.model_mode)

    def test_failed_connection_restores_all_previous_environment_settings(self):
        prior = {"AEGIS_FORCE_RULES": "1", "OPENAI_API_KEY": "old-synthetic", "OPENAI_MODEL": "old-model",
                 "OPENAI_BASE_URL": "https://old.invalid/v1"}
        candidate = SimpleNamespace(model=SimpleNamespace(probe=Mock(return_value={"ok": False, "code": "http_401", "model": "new", "elapsed_ms": 99})))
        with patch.dict(os.environ, prior), patch.object(app, "HotelOrchestrator", return_value=candidate):
            with self.assertRaisesRegex(ValueError, "http_401"):
                app._connect_cloud_model("new-synthetic", "https://new.invalid/v1", "new")
            self.assertEqual({key: os.environ.get(key) for key in prior}, prior)
        candidate.model.probe.assert_called_once_with(inference=True)

    def test_successful_ui_switch_persists_settings_without_resetting_session(self):
        manager = SessionManager(db_path="")
        self.addCleanup(manager.close)
        manager.chat_result(app.GUEST_SESSION, "1128 送两瓶水", force_rules=True)
        flow = manager.get_session(app.GUEST_SESSION)
        before = deepcopy(manager._business_payload()), deepcopy(manager.conversation_history(app.GUEST_SESSION))
        candidate = SimpleNamespace(model=self.client())
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            path = Path(directory) / "config.json"
            app._activate_cloud_model(manager, flow, candidate, "synthetic", "https://api.siliconflow.cn/v1", candidate.model.model, config_path=path)
            self.assertEqual(json.loads(path.read_text())["model"], candidate.model.model)
            self.assertEqual(os.getenv("OPENAI_MODEL"), candidate.model.model)
            self.assertIs(manager.get_session(app.GUEST_SESSION), flow)
            self.assertEqual((manager._business_payload(), manager.conversation_history(app.GUEST_SESSION)), before)

    def test_streamlit_connect_button_retains_history_and_feedback_after_rerun(self):
        from streamlit.testing.v1 import AppTest
        candidate = SimpleNamespace(model=self.client())
        probe = candidate.model.probe(inference=True)
        activate = app._activate_cloud_model
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1", "OPENAI_API_KEY": "synthetic"}):
            ui = AppTest.from_string("import app\napp.main()", default_timeout=15).run()
            manager = ui.session_state["agent_runtime"]
            self.addCleanup(manager.close)
            manager.chat_result(app.GUEST_SESSION, "1128 送两瓶水", force_rules=True)
            before = deepcopy(manager.conversation_history(app.GUEST_SESSION))
            flow = manager.get_session(app.GUEST_SESSION)
            ui.selectbox[0].select("SiliconFlow (硅基流动)").run()
            with patch.object(app, "_connect_cloud_model", return_value=(candidate, probe)), patch.object(app, "_activate_cloud_model", side_effect=lambda *args: activate(*args, config_path=Path(directory) / "config.json")):
                next(button for button in ui.button if button.label == "保存并连接云端大模型").click().run()
            self.assertEqual(list(ui.exception), [])
            self.assertIs(ui.session_state["agent_runtime"], manager)
            self.assertIs(manager.get_session(app.GUEST_SESSION), flow)
            self.assertEqual(manager.conversation_history(app.GUEST_SESSION), before)
            self.assertTrue(any("实际推理通过" in item.value for item in ui.success))
            self.assertTrue(any("最近云端调用成功" in item.value for item in ui.success))

    def test_config_write_failure_keeps_old_client_and_environment(self):
        manager = SessionManager(db_path="")
        self.addCleanup(manager.close)
        manager.chat_result(app.GUEST_SESSION, "1128 送两瓶水", force_rules=True)
        flow = manager.get_session(app.GUEST_SESSION)
        old = flow.model
        candidate = SimpleNamespace(model=self.client())
        with patch.dict(os.environ, {"OPENAI_MODEL": "old"}), patch.object(Path, "write_text", side_effect=OSError("private contents")):
            with self.assertRaisesRegex(ValueError, "原连接保持不变"):
                app._activate_cloud_model(manager, flow, candidate, "synthetic", "https://api.siliconflow.cn/v1", "new", config_path=Path(".artifacts/nonexistent-config-test.json"))
            self.assertIs(flow.model, old)
            self.assertEqual(os.getenv("OPENAI_MODEL"), "old")

    def test_running_task_blocks_model_switch_and_then_finishes_normally(self):
        manager = SessionManager(db_path="")
        self.addCleanup(manager.close)
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = hotel_core.HousekeepingAgent.dispatch
        def delayed(agent, room, detail, state):
            started.set()
            release.wait(3)
            return original(agent, room, detail, state)
        with patch.object(hotel_core.HousekeepingAgent, "dispatch", delayed):
            run = manager.submit("guest", "1128 送两瓶水", force_rules=True)
            self.assertTrue(started.wait(2))
            flow = manager.get_session("guest")
            old = flow.model
            with self.assertRaises(RuntimeBusy):
                manager.configure_model("guest", self.client())
            self.assertIs(flow.model, old)
            release.set()
            self.assertEqual(manager.wait("guest", run.run_id, timeout=2).status, "completed")
        self.assertEqual(len(manager.shared_tickets), 1)

    def test_acceptable_first_json_reply_avoids_second_generation(self):
        with patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1"}):
            flow = hotel_core.HotelOrchestrator(force_rules=True)
        flow.force_rules, flow.model.disabled, flow.model.client = False, False, object()
        flow.model.json_extract = Mock(return_value={"intent": "闲聊", "actions": [], "reply": "您好，欢迎来到学术之家。"})
        flow.model.chat = Mock(side_effect=AssertionError("should reuse first answer"))
        self.assertIn("欢迎", flow.process("你好"))
        self.assertEqual(flow.model.json_extract.call_count, 1)

    def test_failed_or_replyless_understanding_does_not_trigger_second_request(self):
        for response in (None, {"intent": "闲聊", "actions": []}):
            with self.subTest(response=response), patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1"}):
                flow = hotel_core.HotelOrchestrator(force_rules=True)
                flow.model.disabled, flow.model.client = False, object()
                flow.model.json_extract = Mock(return_value=response)
                flow.model.chat = Mock(side_effect=AssertionError("do not retry in reply path"))
                self.assertTrue(flow.process("你好"))
                self.assertEqual(flow.model.json_extract.call_count, 1)
                flow.model.chat.assert_not_called()

    def test_model_parts_are_understood_once_per_compound(self):
        with patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1", "AEGIS_SITE_PROFILE": "academic"}):
            flow = hotel_core.HotelOrchestrator(force_rules=True)
        flow._bind_room("1128")
        flow.force_rules, flow.model.disabled, flow.model.client = False, False, object()
        flow.model.json_extract = Mock(return_value={"intent": "送物", "room_no": "1128", "sentiment": "正常",
            "actions": [{"kind": "delivery", "source": "送两瓶水"}, {"kind": "delivery", "source": "再拿三条毛巾"}]})
        flow.process("送两瓶水再拿三条毛巾")
        self.assertEqual(flow.model.json_extract.call_count, 1)
        self.assertEqual({t["inventory_item"]: t["quantity"] for t in flow.state.tickets}, {"矿泉水": 2, "毛巾": 3})

    def test_preferred_address_is_independent_from_verified_identity(self):
        with patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1"}):
            flow = hotel_core.HotelOrchestrator(force_rules=True)
        flow._bind_room("1128")
        profile = deepcopy(flow.state.guest_profile)
        reply = flow.process("我姓梁，叫我梁老板")
        self.assertIn("梁老板", reply)
        self.assertEqual(flow.state.preferred_address, "梁老板")
        self.assertEqual(flow.state.guest_profile, profile)
        self.assertTrue(flow.process("给我送两瓶水").startswith("梁老板"))
        self.assertEqual(flow.housekeeping.inventory["矿泉水"], 34)

    def test_address_objection_clears_only_preference(self):
        with patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1"}):
            flow = hotel_core.HotelOrchestrator(force_rules=True)
        flow._bind_room("1128")
        flow.process("叫我梁老板")
        reply = flow.process("别叫我梁老板")
        self.assertIsNone(flow.state.preferred_address)
        self.assertFalse(reply.startswith("梁老板"))
        self.assertEqual(flow.state.room_no, "1128")

    def test_water_before_urgency_is_reminder_without_new_reservation(self):
        with patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1"}):
            flow = hotel_core.HotelOrchestrator(force_rules=True)
        flow._bind_room("1128")
        flow.process("给我送两瓶水")
        ticket = flow.state.tickets[0]
        for text in ("水快点送来，我赶时间", "水尽快送来", "矿泉水快一点送来"):
            with self.subTest(text=text):
                reply = flow.process(text)
                self.assertIn("催送", reply)
                self.assertEqual(len(flow.state.tickets), 1)
                self.assertEqual(flow.housekeeping.inventory["矿泉水"], 34)
        self.assertEqual(ticket["reminder_count"], 3)

    def test_address_and_business_in_same_turn_both_apply(self):
        with patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1"}):
            flow = hotel_core.HotelOrchestrator(force_rules=True)
        flow._bind_room("1128")
        reply = flow.process("叫我梁老板，给我送两条毛巾")
        self.assertIn("梁老板", reply)
        self.assertEqual(len(flow.state.tickets), 1)
        self.assertEqual(flow.state.tickets[0]["quantity"], 2)

    def test_unpunctuated_address_does_not_swallow_delivery(self):
        with patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1"}):
            flow = hotel_core.HotelOrchestrator(force_rules=True)
        flow._bind_room("1128")
        flow.process("叫我梁老板顺便送两瓶水")
        self.assertEqual(flow.state.preferred_address, "梁老板")
        self.assertEqual([(t["inventory_item"], t["quantity"]) for t in flow.state.tickets], [("矿泉水", 2)])

    def test_incomplete_model_proposal_cannot_drop_urgent_complaint(self):
        with patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1"}):
            flow = hotel_core.HotelOrchestrator(force_rules=True)
        flow._bind_room("1128")
        flow.model.disabled, flow.model.client = False, object()
        flow.model.json_extract = Mock(return_value={"intent": "送物", "actions": [{"kind": "delivery", "source": "送两瓶水"}]})
        flow.process("送两瓶水，卫生太差")
        self.assertEqual({t["category"] for t in flow.state.tickets}, {"送物", "客诉紧急"})
        self.assertEqual(next(t for t in flow.state.tickets if t["category"] == "客诉紧急")["priority"], "高")

    def test_known_order_reminder_is_local_and_does_not_wait_for_model(self):
        with patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1"}):
            flow = hotel_core.HotelOrchestrator(force_rules=True)
        flow._bind_room("1128")
        flow.process("给我送两瓶水")
        flow.model.disabled, flow.model.client = False, object()
        flow.model.json_extract = Mock(side_effect=AssertionError("known reminder must be local"))
        self.assertIn("催送", flow.process("水快点送来，我赶时间"))
        self.assertEqual(flow.state.model_mode, "规则护栏 · 原单催送")
        self.assertEqual(flow.model.json_extract.call_count, 0)

    def test_verified_model_replacement_preserves_business_and_history(self):
        manager = SessionManager(db_path="")
        self.addCleanup(manager.close)
        manager.chat_result("guest", "1128 送两瓶水", force_rules=True)
        flow = manager.get_session("guest")
        before = deepcopy(manager._business_payload()), deepcopy(manager.conversation_history("guest"))
        candidate = self.client()
        manager.configure_model("guest", candidate)
        self.assertIs(flow.model, candidate)
        self.assertEqual((manager._business_payload(), manager.conversation_history("guest")), before)
        self.assertIs(flow.reception.model, candidate)

    def test_address_survives_sqlite_restart(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"AEGIS_FORCE_RULES": "1"}):
            path = str(Path(directory) / "address.sqlite3")
            first = SessionManager(db_path=path)
            try:
                first.chat_result("guest", "1128", force_rules=True)
                first.chat_result("guest", "我姓梁，叫我梁老板", force_rules=True)
            finally:
                first.close()
            second = SessionManager(db_path=path)
            try:
                self.assertEqual(second.get_or_create("guest", force_rules=True).state.preferred_address, "梁老板")
                self.assertTrue(second.chat_result("guest", "给我送一瓶水", force_rules=True).reply.startswith("梁老板"))
            finally:
                second.close()


if __name__ == "__main__":
    unittest.main()
