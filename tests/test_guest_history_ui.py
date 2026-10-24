"""Guest archive navigation through the real local and authenticated API UIs."""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
ADMIN = "history-ui-admin-" + "a" * 40
SIGNING = "history-ui-signing-" + "b" * 40


class GuestHistoryAssertions:
    def assert_healthy(self, app):
        self.assertEqual(list(app.exception), [])

    def user_messages(self, app):
        return [message.markdown[0].value for message in app.chat_message if message.name == "user"]

    def history_button(self, app, label):
        matches = [button for button in app.button if button.label == label]
        self.assertEqual(len(matches), 1, f"Guest history needs one {label!r} control")
        return matches[0]

    def assert_all_pages_reachable(self, app, prefix):
        seen = []
        for _ in range(10):
            self.assert_healthy(app)
            seen.extend(self.user_messages(app))
            older = self.history_button(app, "加载更早记录")
            if older.disabled:
                break
            older.click().run()
        else:
            self.fail("History paging did not reach the beginning")
        self.assertEqual(len(seen), 56)
        self.assertEqual(len(set(seen)), 56)
        self.assertEqual(set(seen), {f"{prefix} 记录 {index}：请问会议签到在哪里" for index in range(56)})

    def populate(self, manager, sid, count, prefix):
        for index in range(count):
            manager.chat_result(sid, f"{prefix} 记录 {index}：请问会议签到在哪里", force_rules=True)


class LocalGuestHistoryUITests(GuestHistoryAssertions, unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AEGIS_FORCE_RULES": "1", "AEGIS_API_URL": "", "AEGIS_DEPLOYED": "0",
            "AEGIS_DB_PATH": "", "AEGIS_AUTH_ENABLED": "0", "AEGIS_SITE_PROFILE": "standard",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=15).run()
        self.assert_healthy(self.app)
        self.runtime = self.app.session_state["agent_runtime"]
        self.addCleanup(self.runtime.close)

    def test_local_main_chat_reads_archive_after_compaction(self):
        self.populate(self.runtime, "streamlit-guest", 8, "本地")
        self.app.run()
        self.assert_healthy(self.app)
        self.assertLess(len(self.runtime.get_session("streamlit-guest").state.messages), 16)
        self.assertEqual(len(self.app.chat_message), 16)
        self.assertIn("本地 记录 0：请问会议签到在哪里", self.user_messages(self.app))
        self.assertEqual(len(self.app.chat_input), 1)

    def test_local_can_reach_messages_beyond_one_hundred(self):
        self.populate(self.runtime, "streamlit-guest", 56, "长对话")
        self.app.run()
        self.assert_all_pages_reachable(self.app, "长对话")
        panel = self.app.container(key="client-history-panel")
        self.assertEqual(panel.proto.height_config.pixel_height, 300)
        self.assertEqual({button.label for button in panel.button}, {"加载更早记录", "回到最新消息"})
        self.assertEqual(len(self.app.chat_input), 1)

    def test_polling_preserves_old_page_until_return_to_latest(self):
        self.populate(self.runtime, "streamlit-guest", 20, "阅读")
        self.app.run()
        self.history_button(self.app, "加载更早记录").click().run()
        self.assert_healthy(self.app)
        old_page = self.user_messages(self.app)
        self.assertIn("阅读 记录 0：请问会议签到在哪里", old_page)
        self.runtime.chat_result("streamlit-guest", "阅读 新回复：请问早餐几点", force_rules=True)
        for _ in range(2):
            self.app.run()
            self.assert_healthy(self.app)
            self.assertEqual(self.user_messages(self.app), old_page)
            self.assertFalse(self.app.container(key="client-history-panel").proto.autoscroll)
        self.history_button(self.app, "回到最新消息").click().run()
        self.assert_healthy(self.app)
        self.assertIn("阅读 新回复：请问早餐几点", self.user_messages(self.app))
        self.assertEqual(len(self.user_messages(self.app)), len(set(self.user_messages(self.app))))
        self.assertTrue(self.app.container(key="client-history-panel").proto.autoscroll)

    def test_replacement_local_runtime_cannot_reuse_old_history_page(self):
        self.populate(self.runtime, "streamlit-guest", 20, "旧会话")
        self.app.run()
        self.history_button(self.app, "加载更早记录").click().run()
        from agent_runtime import SessionManager

        replacement = SessionManager(db_path="")
        self.addCleanup(replacement.close)
        self.populate(replacement, "streamlit-guest", 1, "新会话")
        self.app.session_state["agent_runtime"] = replacement
        self.app.session_state["orchestrator"] = replacement.get_session("streamlit-guest")
        self.app.run()
        self.assert_healthy(self.app)
        self.assertEqual(self.user_messages(self.app), ["新会话 记录 0：请问会议签到在哪里"])


class DeployedGuestHistoryUITests(GuestHistoryAssertions, unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AEGIS_FORCE_RULES": "1", "AEGIS_API_URL": "http://history-ui-api", "AEGIS_DEPLOYED": "0",
            "AEGIS_DB_PATH": "", "AEGIS_AUTH_ENABLED": "1", "AEGIS_SITE_PROFILE": "academic",
            "AEGIS_STAFF_TOKEN": ADMIN, "AEGIS_SIGNING_KEY": SIGNING,
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        import server
        from agent_runtime import SessionManager
        from fastapi.testclient import TestClient

        # Flat unique files avoid Windows ACL inheritance problems in newly
        # created temporary subdirectories. No existing database is opened.
        self.db = Path(tempfile.gettempdir()) / ("aegis-history-ui-" + uuid.uuid4().hex + ".sqlite3")
        self.addCleanup(self.remove_database)
        self.manager = SessionManager(db_path=str(self.db))
        self.addCleanup(self.manager.close)
        binding = patch.object(server, "session_manager", self.manager)
        binding.start()
        self.addCleanup(binding.stop)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)

        def request(method, url, **kwargs):
            kwargs.pop("timeout", None)
            return self.client.request(method, urlsplit(url).path, **kwargs)

        transport = patch("api_client.httpx.request", side_effect=request)
        transport.start()
        self.addCleanup(transport.stop)

    def remove_database(self):
        for path in (self.db, Path(str(self.db) + "-wal"), Path(str(self.db) + "-shm"), Path(str(self.db) + ".writer.lock")):
            path.unlink(missing_ok=True)

    def guest(self, room="1128"):
        response = self.client.post("/api/guest-access", headers={"Authorization": "Bearer " + ADMIN},
                                    json={"room_no": room, "identity_checked": True})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def app(self, guest):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=15)
        app.session_state["access_token"] = guest["access_token"]
        app.run()
        self.assert_healthy(app)
        return app

    def test_deployed_main_chat_reads_authorized_archive(self):
        guest = self.guest()
        self.populate(self.manager, guest["session_id"], 8, "已授权")
        app = self.app(guest)
        self.assertLess(len(self.manager.get_session(guest["session_id"]).state.messages), 16)
        self.assertEqual(len(app.chat_message), 16)
        self.assertIn("已授权 记录 0：请问会议签到在哪里", self.user_messages(app))

    def test_deployed_can_reach_messages_beyond_one_hundred(self):
        guest = self.guest()
        self.populate(self.manager, guest["session_id"], 56, "接口历史")
        app = self.app(guest)
        self.assert_all_pages_reachable(app, "接口历史")
        self.assertEqual(len(app.chat_input), 1)

    def test_changed_guest_pass_discards_previous_history_page(self):
        first = self.guest()
        self.populate(self.manager, first["session_id"], 20, "前一住客")
        app = self.app(first)
        self.history_button(app, "加载更早记录").click().run()
        second = self.guest("808")
        self.populate(self.manager, second["session_id"], 1, "当前住客")
        app.text_input(key="access_token").set_value(second["access_token"]).run()
        self.assert_healthy(app)
        self.assertEqual(self.user_messages(app), ["当前住客 记录 0：请问会议签到在哪里"])
        self.assertFalse("agent_runtime" in app.session_state)


if __name__ == "__main__":
    unittest.main()
