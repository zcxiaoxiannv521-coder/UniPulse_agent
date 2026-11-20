from copy import deepcopy
from datetime import datetime, timedelta
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from web.client import ApiClient, ClientError, DemoClient, TZ, initial_workspace


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.state = initial_workspace()
        self.client = DemoClient(self.state)

    def preview(self):
        return self.client.preview_exam_change("exam_ml", "2026-12-02T14:00:00+08:00", self.state["version"], "n_change")

    def test_cancel_preserves_schedule_and_reminders(self):
        before = deepcopy(self.state)
        action = self.preview()
        self.client.cancel_action(action["id"])
        for key in ("events", "tasks", "classes", "study_blocks", "reminders", "version"):
            self.assertEqual(before[key], self.state[key])
        with self.assertRaises(ClientError):
            self.client.confirm_action(action["id"])

    def test_confirm_updates_reminder_once_preserves_completed_and_classes(self):
        before = deepcopy(self.state)
        action = self.preview()
        self.client.confirm_action(action["id"])
        self.assertEqual(self.state["events"][0]["start_at"], action["new"])
        self.assertEqual(self.state["reminders"][0]["status"], "invalidated")
        self.assertEqual(self.state["reminders"][-1]["at"], "2026-12-01T14:00:00+08:00")
        self.assertEqual(before["classes"], self.state["classes"])
        self.assertEqual(before["tasks"][2], self.state["tasks"][2])
        self.assertTrue(all(b["status"] == "needs_review" for b in self.state["study_blocks"]))
        self.assertEqual(self.state["plans"][0]["version"], 1)
        self.assertEqual(self.state["plans"][0]["status"], "needs_adjustment")
        self.assertEqual(self.state["events"][0]["source_notice_id"], "n_change")
        applied = deepcopy(self.state)
        self.client.confirm_action(action["id"])
        self.assertEqual(applied, self.state)
        with self.assertRaisesRegex(ClientError, "已应用"):
            self.client.cancel_action(action["id"])

    def test_stale_preview_cannot_commit(self):
        action = self.preview()
        self.client.save_notice("临时补充通知", self.state["version"])
        before = deepcopy(self.state)
        with self.assertRaisesRegex(ClientError, "已变化"):
            self.client.confirm_action(action["id"])
        self.assertEqual(before, self.state)

    def test_expired_preview_cannot_commit(self):
        action = self.preview()
        self.state["pending"][0]["expires_at"] = (datetime.now(TZ)-timedelta(minutes=1)).isoformat()
        with self.assertRaisesRegex(ClientError, "过期"):
            self.client.confirm_action(action["id"])
        self.assertEqual(self.state["events"][0]["version"], 1)

    def test_confirmation_preserves_locked_block(self):
        self.state['study_blocks'][0]['locked'] = True
        before = deepcopy(self.state['study_blocks'][0])
        self.client.confirm_action(self.preview()['id'])
        self.assertEqual(before, self.state['study_blocks'][0])

    def test_partial_feedback_separates_time_from_work_remaining(self):
        self.client.record_progress("t1", {"status": "partial", "remaining_minutes": 45, "actual_minutes": 120, "feedback": "还剩一半"}, 1)
        task = self.state["tasks"][0]
        self.assertEqual((task["status"], task["remaining_minutes"], task["actual_minutes"]), ("partial", 45, 120))
        self.assertEqual(self.state["tasks"][2]["status"], "done")
        self.assertEqual(self.state["goals"][0]["status"], "needs_review")

    def test_invalid_feedback_does_not_mutate(self):
        before = deepcopy(self.state)
        with self.assertRaises(ClientError):
            self.client.record_progress("t1", {"status": "partial", "remaining_minutes": 0, "actual_minutes": 120, "feedback": "还有工作"}, 1)
        self.assertEqual(before, self.state)

    def test_course_overlap_rejected_and_adjacent_allowed(self):
        payload = {"course": "测试课程", "location": "B205", "start_at": "2026-11-30T09:00:00+08:00", "end_at": "2026-11-30T10:00:00+08:00"}
        with self.assertRaisesRegex(ClientError, "已有课程"):
            self.client.add_class(payload, 1)
        payload["start_at"] = "2026-11-30T09:35:00+08:00"
        self.client.add_class(payload, 1)
        self.assertEqual(len(self.state["classes"]), 6)

    def test_duplicate_notice_does_not_increment_version(self):
        self.client.save_notice("相同原文", 1)
        with self.assertRaisesRegex(ClientError, "已经保存"):
            self.client.save_notice("相同原文", 2)
        self.assertEqual(self.state["version"], 2)


class ApiTests(unittest.TestCase):
    def test_failed_backend_does_not_fall_back(self):
        client = ApiClient("http://127.0.0.1:8000")
        client.opener = Mock()
        client.opener.open.side_effect = URLError("unavailable")
        with self.assertRaisesRegex(ClientError, "未切换"):
            client.snapshot()

    def test_invalid_success_envelope_is_rejected(self):
        client = ApiClient("http://127.0.0.1:8000")
        client.opener = Mock()
        client.opener.open.return_value = io.BytesIO(json.dumps({"ok": False, "data": {}}).encode())
        with self.assertRaises(ClientError):
            client.snapshot()

    def test_remote_http_and_credentials_in_url_rejected(self):
        for url in ("http://example.com", "https://person:secret@example.com", "https://example.com?token=secret"):
            with self.subTest(url=url), self.assertRaises(ClientError):
                ApiClient(url)

    def test_missing_workspace_field_rejected(self):
        client = ApiClient("http://127.0.0.1:8000")
        with patch.object(client, "_request", return_value={"version": 1}):
            with self.assertRaisesRegex(ClientError, "缺少字段"):
                client.snapshot()

    def test_nested_fields_checked_before_render(self):
        state = initial_workspace()
        state["tasks"][0]["remaining_minutes"] = "45"
        client = ApiClient("http://127.0.0.1:8000")
        with patch.object(client, "_request", return_value=state):
            with self.assertRaisesRegex(ClientError, "记录字段"):
                client.snapshot()

    def test_valid_workspace_accepted(self):
        state = initial_workspace()
        client = ApiClient("http://127.0.0.1:8000")
        with patch.object(client, "_request", return_value=state):
            self.assertEqual(client.snapshot(), state)


if __name__ == "__main__":
    unittest.main()
