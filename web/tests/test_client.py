from copy import deepcopy
from datetime import date, datetime, timedelta
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from web.client import (ApiClient, ClientError, DemoClient, TZ, initial_workspace,
                        week_bounds, workspace_now)


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


def _http_error(code, payload):
    return HTTPError("http://127.0.0.1:8000/v1/x", code, "err", {},
                     io.BytesIO(json.dumps(payload).encode()))


class TimeBaseTests(unittest.TestCase):
    """时间基准：API 模式用后端 generated_at，演示模式用 demo_now。"""

    def test_api_mode_uses_generated_at(self):
        state = initial_workspace()
        state["generated_at"] = "2026-11-30T09:00:00+08:00"

        value = workspace_now(state, False)

        self.assertEqual(value.date(), date(2026, 11, 30))
        self.assertEqual(value.utcoffset(), timedelta(hours=8))

    def test_api_mode_ignores_demo_now(self):
        """API 模式即使 fixture 里带 demo_now，也必须用 generated_at。"""
        state = initial_workspace()
        state["generated_at"] = "2026-12-02T08:00:00+08:00"

        self.assertEqual(workspace_now(state, False).date(), date(2026, 12, 2))

    def test_demo_mode_still_uses_demo_now(self):
        state = initial_workspace()
        state["generated_at"] = "2030-01-01T00:00:00+08:00"

        self.assertEqual(workspace_now(state, True).date(), date(2026, 11, 30))

    def test_generated_at_timezone_is_converted(self):
        state = initial_workspace()
        state["generated_at"] = "2026-11-30T23:30:00-05:00"

        value = workspace_now(state, False)

        # 23:30 -05:00 = 次日 12:30 +08:00
        self.assertEqual(value.date(), date(2026, 12, 1))
        self.assertEqual(value.hour, 12)

    def test_generated_at_missing_is_explicit(self):
        state = initial_workspace()
        state.pop("generated_at", None)

        with self.assertRaisesRegex(ClientError, "无法确定本周日期范围"):
            workspace_now(state, False)

    def test_generated_at_invalid_is_explicit(self):
        state = initial_workspace()
        state["generated_at"] = "不是时间"

        with self.assertRaisesRegex(ClientError, "格式不正确"):
            workspace_now(state, False)

    def test_week_bounds(self):
        monday, sunday = week_bounds(date(2026, 11, 30))     # 周一
        self.assertEqual((monday, sunday), (date(2026, 11, 30), date(2026, 12, 6)))

        monday, sunday = week_bounds(date(2026, 12, 6))      # 周日
        self.assertEqual((monday, sunday), (date(2026, 11, 30), date(2026, 12, 6)))

    def test_week_bounds_cross_year(self):
        monday, sunday = week_bounds(date(2027, 1, 1))       # 周五，跨年
        self.assertEqual(monday, date(2026, 12, 28))
        self.assertEqual(sunday, date(2027, 1, 3))

    def test_week_bounds_cross_month(self):
        monday, sunday = week_bounds(date(2026, 3, 1))       # 周日，跨月
        self.assertEqual(monday, date(2026, 2, 23))
        self.assertEqual(sunday, date(2026, 3, 1))


class ApiTests(unittest.TestCase):
    def test_conflict_codes_are_distinguished(self):
        """409 不能一律提示"数据已变化"：课表冲突要用后端的具体说明。"""
        client = ApiClient("http://127.0.0.1:8000")
        client.opener = Mock()
        client.opener.open.side_effect = _http_error(409, {
            "ok": False,
            "error": {"code": "CLASS_TIME_CONFLICT",
                      "message": "与已有课程「数据结构」（08:00–09:35）时间冲突"},
        })

        with self.assertRaises(ClientError) as ctx:
            client.add_class({}, 1)

        self.assertEqual(ctx.exception.code, "CLASS_TIME_CONFLICT")
        self.assertIn("已有课程", str(ctx.exception))

    def test_version_conflict_uses_friendly_message(self):
        client = ApiClient("http://127.0.0.1:8000")
        client.opener = Mock()
        client.opener.open.side_effect = _http_error(409, {
            "ok": False,
            "error": {"code": "VERSION_CONFLICT", "message": "正式计划已变化"},
        })

        with self.assertRaises(ClientError) as ctx:
            client.confirm_action("pv_1", "T03")

        self.assertEqual(ctx.exception.code, "VERSION_CONFLICT")
        self.assertIn("数据已变化", str(ctx.exception))

    def test_confirm_sends_confirm_code(self):
        client = ApiClient("http://127.0.0.1:8000")
        with patch.object(client, "_request", return_value={"ok": True}) as mocked:
            client.confirm_action("pv_1", "T03")

        mocked.assert_called_once_with(
            "POST", "/actions/pv_1/confirm", {"confirm_code": "T03"}
        )

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
