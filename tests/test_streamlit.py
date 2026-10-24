"""Headless interaction checks for an editable chat while a department is busy."""
from __future__ import annotations

import threading
import unittest
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from hotel_core import HousekeepingAgent


STREAMLIT_VERSION = tuple(int(part) for part in version("streamlit").split(".")[:2])


@unittest.skipIf(STREAMLIT_VERSION < (1, 57), "Use project .venv with Streamlit >= 1.57")
class GuestInteractionTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"AEGIS_FORCE_RULES": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15).run()
        self.runtime = self.app.session_state["agent_runtime"]
        self.addCleanup(self.runtime.close)
        self.assertEqual(list(self.app.exception), [])

    def send(self, message):
        self.app.chat_input(key="guest-prompt").set_value(message).run()
        self.assertEqual(list(self.app.exception), [])
        flow = self.runtime.get_session("streamlit-guest")
        run_id = list(flow.state.tasks.values())[-1]["run_id"]
        return self.runtime.get_run("streamlit-guest", run_id)

    def test_user_can_replace_while_old_department_worker_is_blocked(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = HousekeepingAgent.dispatch

        def dispatch(agent, room, detail, state):
            if "毛巾" in detail:
                started.set()
                if not release.wait(5):
                    raise RuntimeError("UI worker was not released")
            return original(agent, room, detail, state)

        with patch.object(HousekeepingAgent, "dispatch", dispatch):
            first = self.send("1128 给我送两块毛巾")
            self.assertTrue(started.wait(2))
            self.assertFalse(first.result.done())
            self.assertEqual(len(self.app.chat_input), 1)
            self.assertTrue(any("可以继续输入" in info.value for info in self.app.info))
            second = self.send("我不要毛巾了，我要浴巾")
            outcome = self.runtime.wait("streamlit-guest", second.run_id, timeout=2)
            self.assertEqual(outcome.version, 2)
            self.assertEqual(first.status, "superseded")
            release.set()
        self.app.run()
        self.assertEqual(list(self.app.exception), [])
        self.assertEqual(len(self.runtime.shared_tickets), 1)
        ticket = self.runtime.shared_tickets[0]
        self.assertEqual((ticket["inventory_item"], ticket["quantity"]), ("浴巾", 2))

    def test_cancel_button_stops_pending_delivery(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = HousekeepingAgent.dispatch

        def dispatch(agent, *args):
            started.set()
            if not release.wait(5):
                raise RuntimeError("UI worker was not released")
            return original(agent, *args)

        with patch.object(HousekeepingAgent, "dispatch", dispatch):
            first = self.send("1128 送两条毛巾")
            self.assertTrue(started.wait(2))
            self.app.button(key="cancel-current-task").click().run()
            task = list(self.runtime.get_session("streamlit-guest").state.tasks.values())[-1]
            self.runtime.wait("streamlit-guest", task["run_id"], timeout=2)
            self.assertTrue(first.cancelled.is_set())
            release.set()
        self.app.run()
        self.assertEqual(list(self.app.exception), [])
        self.assertEqual(self.runtime.shared_tickets, [])
        self.assertEqual(self.runtime.housekeeping.inventory["毛巾"], 24)

    def test_staff_can_review_conflict_without_bypassing_validation(self):
        original = HousekeepingAgent.dispatch

        def wrong_item(agent, *args):
            ticket = original(agent, *args)
            ticket["inventory_item"] = "矿泉水"
            return ticket

        with patch.object(HousekeepingAgent, "dispatch", wrong_item):
            run = self.send("1128 送两条浴巾")
            outcome = self.runtime.wait("streamlit-guest", run.run_id)
        self.assertEqual(outcome.status, "needs_review")
        self.app.sidebar.radio(key="workspace-page").set_value("运营中枢").run()
        conflict = self.runtime.get_session("streamlit-guest").state.agent_conflicts[0]
        self.app.button(key=f"resolve-{conflict['conflict_id']}").click().run()
        self.assertEqual(list(self.app.exception), [])
        self.assertEqual(conflict["status"], "已核验")
        self.assertEqual(self.runtime.shared_tickets, [])


if __name__ == "__main__":
    unittest.main()
