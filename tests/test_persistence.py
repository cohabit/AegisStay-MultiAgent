"""SQLite restart and session recovery checks for the small-hotel deployment."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime import SessionManager


class PersistenceTests(unittest.TestCase):
    def test_business_and_session_state_survive_process_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "aegis.sqlite3")
            first = SessionManager(db_path=db_path)
            try:
                first.chat_result("guest-1", "1128 送两条毛巾", force_rules=True)
                self.assertTrue(first.persistence_enabled())
                self.assertEqual(first.shared_tickets[0]["inventory_item"], "毛巾")
            finally:
                first.close()

            second = SessionManager(db_path=db_path)
            try:
                restored = second.get_or_create("guest-1", force_rules=True)
                self.assertEqual(restored.state.room_no, "1128")
                self.assertEqual(len(second.shared_tickets), 1)
                self.assertEqual(second.shared_tickets[0]["status"], "处理中")
                self.assertEqual(second.housekeeping.inventory["毛巾"], 22)
                self.assertEqual(restored.state.tasks[next(iter(restored.state.tasks))]["status"], "submitted")
            finally:
                second.close()

    def test_restart_marks_inflight_task_as_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "aegis.sqlite3")
            first = SessionManager(db_path=db_path)
            try:
                flow = first.get_or_create("guest-2", force_rules=True)
                flow.state.tasks["TASK-restart"] = {"task_id": "TASK-restart", "status": "running"}
            finally:
                first.close()
            second = SessionManager(db_path=db_path)
            try:
                flow = second.get_or_create("guest-2", force_rules=True)
                self.assertEqual(flow.state.tasks["TASK-restart"]["status"], "interrupted")
            finally:
                second.close()


if __name__ == "__main__":
    unittest.main()
