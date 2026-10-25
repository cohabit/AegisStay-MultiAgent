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


class ArchiveFixture:
    """Deterministic archive with the same exclusive cursor as both backends."""
    def __init__(self, count):
        self.records = []
        self.fetches = []
        self.append(count)

    def append(self, count):
        for sequence in range(len(self.records) + 1, len(self.records) + count + 1):
            self.records.append({"message_id": f"message-{sequence}", "sequence": sequence,
                                 "role": "user" if sequence % 2 else "assistant", "content": "重复的正常对话内容"})

    def __call__(self, *, before, limit):
        if not 1 <= limit <= 100:
            raise AssertionError("Archive reads must honor the existing 100-message API limit")
        self.fetches.append((before, limit))
        rows = [record.copy() for record in self.records if before is None or record["sequence"] < before]
        return rows[-limit:]


class CumulativeHistoryStateTests(unittest.TestCase):
    def setUp(self):
        import deployed_ui

        self.ui = deployed_ui
        self.state = {}
        session = patch.object(deployed_ui.st, "session_state", self.state)
        session.start()
        self.addCleanup(session.stop)
        self.scope = ("http://synthetic-api", "session-a", "stay-a", "grant-a")
        self.archive = ArchiveFixture(170)

    def page(self, action=None, scope=None):
        active_scope = self.scope if scope is None else scope
        if action:
            self.ui._navigate_guest_history("history", active_scope, action)
        return self.ui.guest_history_page(self.archive, scope=active_scope, key="history")

    def assert_sequences(self, page, first, last):
        self.assertEqual([record["sequence"] for record in page["messages"]], list(range(first, last + 1)))
        self.assertEqual(len({record["message_id"] for record in page["messages"]}), last - first + 1)

    def test_initial_and_older_reads_accumulate_sixty_without_dropping_newer_records(self):
        page = self.page()
        self.assert_sequences(page, 111, 170)
        self.assertEqual(self.archive.fetches, [(None, 61)])
        self.assertTrue(page["has_older"])
        page = self.page("older")
        self.assert_sequences(page, 51, 170)
        self.assertEqual(self.archive.fetches[-1], (111, 61))
        self.assertFalse(page["latest"])
        page = self.page("older")
        self.assert_sequences(page, 1, 170)
        self.assertFalse(page["has_older"])

    def test_poll_and_return_latest_deduplicate_without_losing_loaded_beginning(self):
        self.page()
        self.page("older")
        self.page("older")
        self.archive.append(12)
        for _ in range(2):
            page = self.page()
            self.assert_sequences(page, 1, 182)
            self.assertFalse(page["latest"])
            self.assertFalse(page["autoscroll"])
        page = self.page("latest")
        self.assert_sequences(page, 1, 182)
        self.assertTrue(page["autoscroll"])
        self.assertTrue(self.page()["autoscroll"])

    def test_latest_keeps_native_scroll_observer_enabled_until_reading_older(self):
        self.page()
        self.page("older")
        for _ in range(2):
            self.assertFalse(self.page()["autoscroll"])
        page = self.page("latest")
        revision = page["scroll_revision"]
        for _ in range(3):
            page = self.page()
            self.assert_sequences(page, 51, 170)
            self.assertTrue(page["autoscroll"])
            self.assertEqual(page["scroll_revision"], revision)
        page = self.page("older")
        self.assert_sequences(page, 1, 170)
        self.assertFalse(page["autoscroll"])
        self.assertFalse(self.page()["autoscroll"])

    def test_poll_fills_the_gap_when_more_than_sixty_new_messages_arrive(self):
        self.page()
        self.page("older")
        self.archive.append(160)
        page = self.page()
        self.assert_sequences(page, 51, 330)
        self.assertTrue(page["has_older"])
        self.assert_sequences(self.page("older"), 1, 330)

    def test_runtime_api_session_stay_and_grant_changes_reset_cumulative_cache(self):
        for changed_scope in (
            ("http://different-api", "session-a", "stay-a", "grant-a"),
            ("http://synthetic-api", "session-b", "stay-a", "grant-a"),
            ("http://synthetic-api", "session-a", "stay-b", "grant-a"),
            ("http://synthetic-api", "session-a", "stay-a", "grant-b"),
            (12345, "streamlit-guest", "stay-local"),
        ):
            with self.subTest(scope=changed_scope):
                self.archive = ArchiveFixture(170)
                self.page()
                self.page("older")
                self.page("older")
                self.archive = ArchiveFixture(4)
                page = self.page(scope=changed_scope)
                self.assert_sequences(page, 1, 4)
                self.assertFalse(page["has_older"])


class GuestHistoryAssertions:
    def assert_healthy(self, app):
        self.assertEqual(list(app.exception), [])

    def user_messages(self, app):
        return [message.markdown[0].value for message in app.chat_message if message.name == "user"]

    def history_button(self, app, label):
        matches = [button for button in app.button if button.label == label]
        self.assertEqual(len(matches), 1, f"Guest history needs one {label!r} control")
        return matches[0]

    def history_panel(self, app, prefix):
        matches = [panel for panel in app.container
                   if panel.key == prefix or (panel.key and panel.key.startswith(prefix + "-jump-"))]
        self.assertEqual(len(matches), 1, "Guest history needs exactly one native scrolling viewport")
        return matches[0]

    def assert_latest_recreates_native_viewport(self, app, prefix):
        messages = [message.markdown[0].value for message in app.chat_message]
        panel = self.history_panel(app, prefix)
        previous_id = panel.proto.id
        previous_height = panel.proto.height_config.pixel_height
        control_id = self.history_button(app, "回到最新消息").proto.id
        for _ in range(2):
            self.history_button(app, "回到最新消息").click().run()
            self.assert_healthy(app)
            self.assertEqual([message.markdown[0].value for message in app.chat_message], messages)
            panel = self.history_panel(app, prefix)
            # A True flag on the same mounted viewport did not jump when its
            # messages were unchanged. The explicit action must recreate the
            # native viewport; a normal poll must then keep that identity and
            # keep its observer enabled until the browser finishes scrolling.
            self.assertNotEqual(panel.proto.id, previous_id)
            self.assertEqual(panel.proto.height_config.pixel_height, previous_height)
            self.assertTrue(panel.proto.autoscroll)
            self.assertEqual(self.history_button(app, "回到最新消息").proto.id, control_id)
            previous_id = panel.proto.id
            app.run()
            self.assert_healthy(app)
            self.assertEqual(self.history_panel(app, prefix).proto.id, previous_id)
            self.assertTrue(self.history_panel(app, prefix).proto.autoscroll)
        self.assertEqual(len(app.chat_input), 1)

    def assert_all_pages_reachable(self, app, prefix):
        previous = []
        self.assertEqual(len(app.chat_message), 60)
        for _ in range(10):
            self.assert_healthy(app)
            visible = self.user_messages(app)
            self.assertEqual(len(visible), len(set(visible)))
            if previous:
                self.assertEqual(visible[-len(previous):], previous)
                self.assertLessEqual(len(app.chat_message) - 2 * len(previous), 60)
            previous = visible
            older = self.history_button(app, "加载更早记录")
            if older.disabled:
                break
            older.click().run()
        else:
            self.fail("History paging did not reach the beginning")
        self.assertEqual(len(app.chat_message), 170)
        self.assertEqual(visible, [f"{prefix} 记录 {index}：请问会议签到在哪里" for index in range(85)])

    def assert_controls_outside_history(self, app, panel_key):
        panel = self.history_panel(app, panel_key)
        self.assertEqual(list(panel.button), [])
        older = self.history_button(app, "加载更早记录")
        latest = self.history_button(app, "回到最新消息")
        # AppTest's element tree preserves render order, so both controls
        # must precede the fixed history viewport rather than scroll in it.
        ordered = list(app)
        self.assertLess(ordered.index(older), ordered.index(panel))
        self.assertLess(ordered.index(latest), ordered.index(panel))

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
        self.populate(self.runtime, "streamlit-guest", 85, "长对话")
        self.app.run()
        self.assert_all_pages_reachable(self.app, "长对话")
        panel = self.history_panel(self.app, "client-history-panel")
        self.assertEqual(panel.proto.height_config.pixel_height, 300)
        self.assert_controls_outside_history(self.app, "client-history-panel")
        self.assertEqual(len(self.app.chat_input), 1)

    def test_polling_preserves_old_page_until_return_to_latest(self):
        self.populate(self.runtime, "streamlit-guest", 85, "阅读")
        self.app.run()
        self.history_button(self.app, "加载更早记录").click().run()
        self.history_button(self.app, "加载更早记录").click().run()
        self.assert_healthy(self.app)
        old_page = self.user_messages(self.app)
        self.assertIn("阅读 记录 0：请问会议签到在哪里", old_page)
        self.runtime.chat_result("streamlit-guest", "阅读 新回复：请问早餐几点", force_rules=True)
        for _ in range(2):
            self.app.run()
            self.assert_healthy(self.app)
            self.assertEqual(self.user_messages(self.app), old_page + ["阅读 新回复：请问早餐几点"])
            self.assertFalse(self.history_panel(self.app, "client-history-panel").proto.autoscroll)
        self.history_button(self.app, "回到最新消息").click().run()
        self.assert_healthy(self.app)
        self.assertIn("阅读 新回复：请问早餐几点", self.user_messages(self.app))
        self.assertEqual(self.user_messages(self.app)[:-1], old_page)
        self.assertEqual(len(self.user_messages(self.app)), len(set(self.user_messages(self.app))))
        self.assertTrue(self.history_panel(self.app, "client-history-panel").proto.autoscroll)
        self.app.run()
        self.assert_healthy(self.app)
        self.assertTrue(self.history_panel(self.app, "client-history-panel").proto.autoscroll)

    def test_local_return_latest_recreates_viewport_with_unchanged_messages(self):
        self.populate(self.runtime, "streamlit-guest", 85, "原生滚动")
        self.app.run()
        self.history_button(self.app, "加载更早记录").click().run()
        self.history_button(self.app, "加载更早记录").click().run()
        self.assertEqual(len(self.app.chat_message), 170)
        self.assert_latest_recreates_native_viewport(self.app, "client-history-panel")

    def test_replacement_local_runtime_cannot_reuse_old_history_page(self):
        self.populate(self.runtime, "streamlit-guest", 40, "旧会话")
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

    def test_missing_local_profile_uses_unverified_academic_facilities(self):
        os.environ.pop("AEGIS_SITE_PROFILE", None)
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=15).run()
        self.assert_healthy(app)
        runtime = app.session_state["agent_runtime"]
        self.addCleanup(runtime.close)
        runtime.chat_result("streamlit-guest", "健身房在哪里，几点开放", force_rules=True)
        app.run()
        self.assert_healthy(app)
        reply = app.chat_message[-1].markdown[0].value
        self.assertEqual(runtime.site_profile, "academic")
        self.assertIn("前台", reply)
        self.assertNotIn("三楼", reply)
        self.assertNotIn("06:00", reply)
        self.assertNotIn("23:00", reply)

    def test_explicit_standard_profile_keeps_demo_facility_fixture(self):
        self.runtime.chat_result("streamlit-guest", "健身房在哪里，几点开放", force_rules=True)
        self.app.run()
        self.assert_healthy(self.app)
        self.assertEqual(self.runtime.site_profile, "standard")
        reply = self.app.chat_message[-1].markdown[0].value
        self.assertIn("三楼", reply)
        self.assertIn("06:00", reply)


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
        self.populate(self.manager, guest["session_id"], 85, "接口历史")
        app = self.app(guest)
        self.assert_all_pages_reachable(app, "接口历史")
        self.assert_controls_outside_history(app, "deployed-history-panel")
        self.assertEqual(len(app.chat_input), 1)

    def test_deployed_polling_and_latest_keep_accumulated_archive(self):
        guest = self.guest()
        sid = guest["session_id"]
        self.populate(self.manager, sid, 85, "接口阅读")
        app = self.app(guest)
        self.history_button(app, "加载更早记录").click().run()
        self.history_button(app, "加载更早记录").click().run()
        old_messages = self.user_messages(app)
        self.manager.chat_result(sid, "接口阅读 新回复：请问会议签到在哪里", force_rules=True)
        for _ in range(2):
            app.run()
            self.assert_healthy(app)
            self.assertEqual(self.user_messages(app), old_messages + ["接口阅读 新回复：请问会议签到在哪里"])
            self.assertFalse(self.history_panel(app, "deployed-history-panel").proto.autoscroll)
        self.history_button(app, "回到最新消息").click().run()
        self.assert_healthy(app)
        self.assertEqual(self.user_messages(app), old_messages + ["接口阅读 新回复：请问会议签到在哪里"])
        self.assertTrue(self.history_panel(app, "deployed-history-panel").proto.autoscroll)

    def test_deployed_return_latest_recreates_viewport_with_unchanged_messages(self):
        guest = self.guest()
        self.populate(self.manager, guest["session_id"], 85, "接口原生滚动")
        app = self.app(guest)
        self.history_button(app, "加载更早记录").click().run()
        self.history_button(app, "加载更早记录").click().run()
        self.assertEqual(len(app.chat_message), 170)
        self.assert_latest_recreates_native_viewport(app, "deployed-history-panel")

    def test_changed_guest_pass_discards_previous_history_page(self):
        first = self.guest()
        self.populate(self.manager, first["session_id"], 40, "前一住客")
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
