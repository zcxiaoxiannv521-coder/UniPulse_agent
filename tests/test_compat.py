"""api/compat.py（/v1 兼容接口）的测试。

覆盖：workspace 映射、空数据、认证、隔离、预览创建/详情/确认/取消、
版本冲突、统一成功/错误信封、原有 /api/v1 不回归。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import app as app_module
from api import auth
from database.connection import get_connection, init_db
from demo import seed as seed_module
from services import clock, preview as preview_module, queries, scheduler as scheduler_module


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T09:00:00+08:00")
    monkeypatch.delenv(auth.ENV_AUTH_MODE, raising=False)
    monkeypatch.delenv(auth.ENV_TOKEN_SECRET, raising=False)

    path = tmp_path / "compat.db"
    init_db(path)
    conn = get_connection(path)
    seed_module.seed(conn)
    conn.close()
    return path


@pytest.fixture()
def client(db_path, monkeypatch):
    for module in (queries, preview_module, scheduler_module):
        monkeypatch.setattr(module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(app_module, "init_db", lambda *args, **kwargs: db_path)
    monkeypatch.setattr(app_module, "DB_PATH", db_path)

    with TestClient(app_module.app) as test_client:
        yield test_client


def _headers(user_id: str = seed_module.DEMO_USER_ID) -> dict:
    return {"Authorization": f"Bearer {auth.create_token(user_id)}"}


def test_workspace_maps_real_data(client):
    resp = client.get("/v1/workspace", headers=_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True

    data = body["data"]
    for key in (
        "version", "profile", "goals", "events", "classes", "tasks",
        "study_blocks", "plans", "reminders", "notices", "pending", "activity",
    ):
        assert key in data

    assert data["profile"]["display_name"] == seed_module.DEMO_USER_NAME
    assert data["version"] == 1
    assert len(data["goals"]) == 1
    assert data["goals"][0]["event_id"] == seed_module.EXAM_ID
    assert data["goals"][0]["daily_max_minutes"] == 90
    assert len(data["events"]) == 1
    assert data["events"][0]["start_at"] == "2026-12-04T14:00:00+08:00"
    assert len(data["classes"]) == 3
    assert len(data["tasks"]) == 3
    assert len(data["study_blocks"]) == 3
    assert data["study_blocks"][0]["plan_version"] == 1
    assert data["plans"][0]["id"] == seed_module.PLAN_ID
    assert data["plans"][0]["event_version"] == 1
    assert data["activity"] == []


def test_workspace_empty_user(client, db_path):
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO users (id, name) VALUES ('us_empty', '空同学')")
    finally:
        conn.close()

    resp = client.get("/v1/workspace", headers=_headers("us_empty"))

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["version"] == 0
    assert data["plans"] == []
    assert data["tasks"] == []
    assert data["study_blocks"] == []
    assert data["pending"] == []
    assert data["profile"]["display_name"] == "空同学"


def test_workspace_requires_auth(client):
    resp = client.get("/v1/workspace")

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
    assert resp.json()["ok"] is False


def test_workspace_isolation(client, db_path):
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")
            conn.execute(
                "INSERT INTO tasks (id, user_id, title) VALUES ('tk_other', 'us_other', '别人的任务')"
            )
    finally:
        conn.close()

    resp = client.get("/v1/workspace", headers=_headers("us_other"))
    data = resp.json()["data"]

    assert [t["id"] for t in data["tasks"]] == ["tk_other"]
    assert "tk_demo_ch3" not in [t["id"] for t in data["tasks"]]
    assert data["study_blocks"] == []


def test_create_preview_via_compat(client):
    resp = client.post(
        "/v1/previews",
        json={
            "event_id": seed_module.EXAM_ID,
            "new_start": "2026-12-02T14:00:00+08:00",
            "expected_version": 1,
            "source_notice_ids": [seed_module.NOTICE_ID],
        },
        headers=_headers(),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    preview = body["data"]
    assert preview["event_id"] == seed_module.EXAM_ID
    assert preview["status"] == "pending"
    assert preview["expected_version"] == 1
    assert preview["old"] == "2026-12-04T14:00:00+08:00"
    assert preview["new"] == "2026-12-02T14:00:00+08:00"
    assert preview["capacity"]["unplaced_minutes"] == 80
    assert any(change["label"] == "考试时间" for change in preview["changes"])
    assert preview["confirm_code"].startswith("T")


def test_preview_detail_via_compat(client):
    created = client.post(
        "/v1/previews",
        json={"event_id": seed_module.EXAM_ID, "new_start": "2026-12-02T14:00:00+08:00"},
        headers=_headers(),
    ).json()["data"]

    resp = client.get(f"/v1/previews/{created['id']}", headers=_headers())

    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == created["id"]


def test_preview_detail_other_user_404(client, db_path):
    created = client.post(
        "/v1/previews",
        json={"event_id": seed_module.EXAM_ID, "new_start": "2026-12-02T14:00:00+08:00"},
        headers=_headers(),
    ).json()["data"]

    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")
        token = auth.create_token("us_other")
    finally:
        conn.close()

    resp = client.get(
        f"/v1/previews/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404
    assert resp.json()["ok"] is False
    assert resp.json()["error"]["code"] == "PREVIEW_NOT_FOUND"


def test_confirm_action_applies_exam_and_plan(client, db_path):
    created = client.post(
        "/v1/previews",
        json={"event_id": seed_module.EXAM_ID, "new_start": "2026-12-02T14:00:00+08:00"},
        headers=_headers(),
    ).json()["data"]

    # C 的 confirm_action 只发空对象：兼容层读取预览自带的确认码
    resp = client.post(
        f"/v1/actions/{created['id']}/confirm", json={}, headers=_headers()
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["ok"] is True
    assert body["data"]["applied"]["plan_version"] == 2

    conn = get_connection(db_path)
    try:
        exam = conn.execute(
            "SELECT exam_date, start_time, version, status FROM exams WHERE id = ?",
            (seed_module.EXAM_ID,),
        ).fetchone()
        plan = conn.execute(
            "SELECT version, status FROM plans WHERE user_id = ? ORDER BY version DESC LIMIT 1",
            (seed_module.DEMO_USER_ID,),
        ).fetchone()
    finally:
        conn.close()

    assert exam["exam_date"] == "2026-12-02"
    assert exam["version"] == 2
    assert exam["status"] == "moved"
    assert plan["version"] == 2


def test_cancel_then_confirm_conflict(client):
    created = client.post(
        "/v1/previews",
        json={"event_id": seed_module.EXAM_ID, "new_start": "2026-12-02T14:00:00+08:00"},
        headers=_headers(),
    ).json()["data"]

    cancelled = client.post(
        f"/v1/actions/{created['id']}/cancel", headers=_headers()
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["preview"]["status"] == "cancelled"

    resp = client.post(
        f"/v1/actions/{created['id']}/confirm", json={}, headers=_headers()
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "PREVIEW_NOT_PENDING"


def test_create_preview_version_conflict(client):
    resp = client.post(
        "/v1/previews",
        json={
            "event_id": seed_module.EXAM_ID,
            "new_start": "2026-12-02T14:00:00+08:00",
            "expected_version": 99,
        },
        headers=_headers(),
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "VERSION_CONFLICT"


def test_create_preview_unknown_event(client):
    resp = client.post(
        "/v1/previews",
        json={"event_id": "ex_not_exist", "new_start": "2026-12-02T14:00:00+08:00"},
        headers=_headers(),
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "EXAM_NOT_FOUND"


def test_workspace_shows_pending_preview(client):
    created = client.post(
        "/v1/previews",
        json={"event_id": seed_module.EXAM_ID, "new_start": "2026-12-02T14:00:00+08:00"},
        headers=_headers(),
    ).json()["data"]

    data = client.get("/v1/workspace", headers=_headers()).json()["data"]

    assert len(data["pending"]) == 1
    assert data["pending"][0]["id"] == created["id"]
    assert data["pending"][0]["status"] == "pending"


def test_original_api_still_works(client):
    resp = client.get("/api/v1/dashboard", headers=_headers())

    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["id"] == seed_module.DEMO_USER_ID
    assert "ok" not in body          # 原有接口仍是裸对象


def test_error_envelope_shape(client):
    resp = client.get("/v1/previews/pv_not_exist", headers=_headers())

    assert resp.status_code == 404
    body = resp.json()
    assert body["ok"] is False
    assert set(body["error"]) >= {"code", "message"}


# ---------------------------------------------------------------- 通知保存接口
def test_save_notice_via_compat(client):
    resp = client.post(
        "/v1/notices",
        json={"text": "教务通知：考试调整至 12 月 2 日", "expected_version": 1},
        headers=_headers(),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["id"].startswith("nt_")
    assert data["text"] == "教务通知：考试调整至 12 月 2 日"
    assert data["channel"]
    assert data["message"]


def test_saved_notice_appears_in_workspace(client):
    created = client.post(
        "/v1/notices",
        json={"text": "这条通知应该出现在 workspace 里"},
        headers=_headers(),
    ).json()["data"]

    data = client.get("/v1/workspace", headers=_headers()).json()["data"]
    ids = [notice["id"] for notice in data["notices"]]

    assert created["id"] in ids
    notice = next(n for n in data["notices"] if n["id"] == created["id"])
    assert notice["text"] == "这条通知应该出现在 workspace 里"
    assert notice["channel"]  # 不能是 None


def test_empty_notice_422(client):
    resp = client.post("/v1/notices", json={"text": "   "}, headers=_headers())

    assert resp.status_code == 422
    assert resp.json()["ok"] is False
    assert resp.json()["error"]["code"] == "EMPTY_NOTICE"


def test_too_long_notice_422(client):
    resp = client.post(
        "/v1/notices", json={"text": "通" * 20001}, headers=_headers()
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "NOTICE_TOO_LONG"


def test_notice_requires_auth(client):
    resp = client.post("/v1/notices", json={"text": "无 token"})

    assert resp.status_code == 401


def test_body_user_id_cannot_spoof_identity(client, db_path):
    """请求体里塞 user_id 不会改变身份归属。"""
    resp = client.post(
        "/v1/notices",
        json={"text": "伪造身份测试", "user_id": "us_other"},
        headers=_headers(),
    )

    assert resp.status_code == 200
    notice_id = resp.json()["data"]["id"]

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT user_id FROM notifications WHERE id = ?", (notice_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row["user_id"] == seed_module.DEMO_USER_ID


def test_other_user_cannot_see_notice(client, db_path):
    client.post(
        "/v1/notices", json={"text": "A 的私密通知"}, headers=_headers()
    )

    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")
        token = auth.create_token("us_other")
    finally:
        conn.close()

    data = client.get(
        "/v1/workspace", headers={"Authorization": f"Bearer {token}"}
    ).json()["data"]

    assert all("A 的私密通知" != n["text"] for n in data["notices"])


def test_preview_cannot_reference_other_users_notice(client, db_path):
    other = client.post(
        "/v1/notices",
        json={"text": "B 用户的通知"},
        headers=_headers(),
    ).json()["data"]

    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO users (id, name) VALUES ('us_b', '乙')")
        token = auth.create_token("us_b")
    finally:
        conn.close()

    resp = client.post(
        "/v1/previews",
        json={
            "event_id": seed_module.EXAM_ID,
            "new_start": "2026-12-02T14:00:00+08:00",
            "source_notice_ids": [other["id"]],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # 乙既没有这场考试，也没有这条通知；先被通知归属检查拦下
    assert resp.status_code in (403, 404)
    assert resp.json()["ok"] is False


def test_workspace_types_match_client_schema(client):
    """按 C 的 ApiClient.snapshot 校验规则逐项检查类型。"""
    data = client.get("/v1/workspace", headers=_headers()).json()["data"]

    schema = {
        "goals": {"id": str, "title": str, "event_id": str, "scope": str,
                  "daily_max_minutes": int, "availability": str,
                  "status": str, "version": int},
        "events": {"id": str, "title": str, "start_at": str, "location": str,
                   "source": str, "version": int},
        "plans": {"id": str, "goal_id": str, "version": int, "status": str,
                  "event_version": int, "source_notice_ids": list, "summary": str},
        "classes": {"id": str, "course": str, "start_at": str, "end_at": str,
                    "location": str},
        "tasks": {"id": str, "goal_id": str, "title": str,
                  "remaining_minutes": int, "actual_minutes": int,
                  "status": str, "version": int},
        "study_blocks": {"id": str, "task_id": str, "plan_id": str,
                         "plan_version": int, "start_at": str, "end_at": str,
                         "status": str},
        "reminders": {"id": str, "event_id": str, "at": str, "status": str},
        "notices": {"id": str, "text": str, "status": str, "channel": str,
                    "received_at": str},
        "pending": {"id": str, "kind": str, "expected_version": int,
                    "status": str, "expires_at": str, "source_notice_ids": list,
                    "changes": list, "preserved": list},
        "activity": {"at": str, "name": str, "detail": str},
    }

    for table, fields in schema.items():
        for row in data[table]:
            for key, kind in fields.items():
                assert type(row.get(key)) is kind, f"{table}.{key}={row.get(key)!r}"

    notice_ids = {n["id"] for n in data["notices"]}
    for item in data["plans"] + data["pending"]:
        for notice_id in item["source_notice_ids"]:
            assert notice_id in notice_ids


def test_notice_save_does_not_change_exam_or_plan(client, db_path):
    def snapshot():
        conn = get_connection(db_path)
        try:
            return {
                table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")]
                for table in ("exams", "plans", "plan_items")
            }
        finally:
            conn.close()

    before = snapshot()
    client.post("/v1/notices", json={"text": "不会改考试"}, headers=_headers())
    after = snapshot()

    assert before == after


# ---------------------------------------------------------------- 任务进度与用时
TASK_ID = "tk_demo_ch3"


def test_task_progress_via_compat(client):
    resp = client.post(
        f"/v1/tasks/{TASK_ID}/progress",
        json={
            "payload": {
                "status": "doing",
                "remaining_minutes": 45,
                "actual_minutes": 15,
                "feedback": "还剩一半",
            },
            "expected_version": 1,
        },
        headers=_headers(),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["task"]["remaining_minutes"] == 45
    assert body["data"]["actual_minutes"] == 15

    data = client.get("/v1/workspace", headers=_headers()).json()["data"]
    task = next(t for t in data["tasks"] if t["id"] == TASK_ID)
    assert task["remaining_minutes"] == 45
    assert task["actual_minutes"] == 15
    assert task["feedback"] == "还剩一半"
    assert task["status"] == "doing"


def test_task_done_via_compat(client):
    resp = client.post(
        f"/v1/tasks/{TASK_ID}/progress",
        json={"payload": {"status": "done", "remaining_minutes": 0}},
        headers=_headers(),
    )

    assert resp.status_code == 200
    data = client.get("/v1/workspace", headers=_headers()).json()["data"]
    task = next(t for t in data["tasks"] if t["id"] == TASK_ID)
    assert task["status"] == "done"
    assert task["remaining_minutes"] == 0


def test_task_progress_requires_auth(client):
    resp = client.post(
        f"/v1/tasks/{TASK_ID}/progress",
        json={"payload": {"status": "doing", "remaining_minutes": 30}},
    )
    assert resp.status_code == 401


def test_task_progress_other_user_404(client, db_path):
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")
        token = auth.create_token("us_other")
    finally:
        conn.close()

    resp = client.post(
        f"/v1/tasks/{TASK_ID}/progress",
        json={"payload": {"status": "doing", "remaining_minutes": 30}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_task_progress_body_user_id_ignored(client, db_path):
    resp = client.post(
        f"/v1/tasks/{TASK_ID}/progress",
        json={
            "payload": {"status": "doing", "remaining_minutes": 20},
            "user_id": "us_other",
        },
        headers=_headers(),
    )
    assert resp.status_code == 200

    conn = get_connection(db_path)
    try:
        task = conn.execute(
            "SELECT user_id, remaining_minutes FROM tasks WHERE id = ?", (TASK_ID,)
        ).fetchone()
    finally:
        conn.close()

    assert task["user_id"] == seed_module.DEMO_USER_ID
    assert task["remaining_minutes"] == 20


def test_task_progress_invalid_remaining(client):
    resp = client.post(
        f"/v1/tasks/{TASK_ID}/progress",
        json={"payload": {"status": "doing", "remaining_minutes": 0}},
        headers=_headers(),
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] in {"INVALID_REMAINING", "INVALID_ARGUMENT"}


def test_task_cannot_reopen_done(client):
    client.post(
        f"/v1/tasks/{TASK_ID}/progress",
        json={"payload": {"status": "done", "remaining_minutes": 0}},
        headers=_headers(),
    )

    resp = client.post(
        f"/v1/tasks/{TASK_ID}/progress",
        json={"payload": {"status": "doing", "remaining_minutes": 30}},
        headers=_headers(),
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TASK_ALREADY_DONE"


def test_task_time_does_not_change_remaining(client):
    before = client.get("/v1/workspace", headers=_headers()).json()["data"]
    before_task = next(t for t in before["tasks"] if t["id"] == TASK_ID)

    resp = client.post(
        f"/v1/tasks/{TASK_ID}/time",
        json={"actual_minutes": 25, "expected_version": 1},
        headers=_headers(),
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["actual_minutes"] == 25

    after = client.get("/v1/workspace", headers=_headers()).json()["data"]
    after_task = next(t for t in after["tasks"] if t["id"] == TASK_ID)

    assert after_task["actual_minutes"] == 25
    assert after_task["remaining_minutes"] == before_task["remaining_minutes"]
    assert after_task["status"] == before_task["status"]


def test_task_time_other_user_404(client, db_path):
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")
        token = auth.create_token("us_other")
    finally:
        conn.close()

    resp = client.post(
        f"/v1/tasks/{TASK_ID}/time",
        json={"actual_minutes": 10},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_task_time_rejects_negative(client):
    resp = client.post(
        f"/v1/tasks/{TASK_ID}/time",
        json={"actual_minutes": -5},
        headers=_headers(),
    )

    assert resp.status_code == 422


def test_task_progress_does_not_touch_plan(client, db_path):
    def snapshot():
        conn = get_connection(db_path)
        try:
            return {
                table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")]
                for table in ("plans", "plan_items")
            }
        finally:
            conn.close()

    before = snapshot()
    client.post(
        f"/v1/tasks/{TASK_ID}/progress",
        json={"payload": {"status": "done", "remaining_minutes": 0}},
        headers=_headers(),
    )
    client.post(
        f"/v1/tasks/{TASK_ID}/time", json={"actual_minutes": 30}, headers=_headers()
    )

    assert snapshot() == before


def test_started_block_not_touched_by_progress(client, db_path):
    """已开始的时间块不能被进度反馈改动。"""
    started_at = "2026-11-30T08:00:00+08:00"
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE plan_items SET start_at = ?, end_at = ? WHERE id = 'pi_demo_01'",
                (started_at, "2026-11-30T09:00:00+08:00"),
            )
        before = [tuple(r) for r in conn.execute("SELECT * FROM plan_items")]
    finally:
        conn.close()

    client.post(
        f"/v1/tasks/{TASK_ID}/progress",
        json={"payload": {"status": "doing", "remaining_minutes": 30}},
        headers=_headers(),
    )
    client.post(
        f"/v1/tasks/{TASK_ID}/time", json={"actual_minutes": 10}, headers=_headers()
    )

    conn = get_connection(db_path)
    try:
        after = [tuple(r) for r in conn.execute("SELECT * FROM plan_items")]
    finally:
        conn.close()

    assert after == before


# ---------------------------------------------------------------- 目标与课表
def test_save_goal_via_compat(client):
    resp = client.post(
        "/v1/goals",
        json={
            "payload": {
                "title": "准备机器学习期末考试",
                "scope": "第三章、第四章",
                "event_id": seed_module.EXAM_ID,
                "daily_max_minutes": 120,
                "availability": "每天 19:00 后",
            },
            "expected_version": 1,
        },
        headers=_headers(),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["goal"]["daily_max_minutes"] == 120
    assert body["data"]["goal"]["scope"] == "第三章、第四章"

    data = client.get("/v1/workspace", headers=_headers()).json()["data"]
    goal = data["goals"][0]
    assert goal["daily_max_minutes"] == 120
    assert goal["scope"] == "第三章、第四章"
    assert goal["availability"] == "每天 19:00 后"


def test_save_goal_requires_auth(client):
    resp = client.post("/v1/goals", json={"payload": {}})
    assert resp.status_code == 401


def test_save_goal_other_users_exam(client, db_path):
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")
            conn.execute(
                "INSERT INTO exams (id, user_id, name, exam_date) "
                "VALUES ('ex_other', 'us_other', '别人的考试', '2026-12-10')"
            )
    finally:
        conn.close()

    resp = client.post(
        "/v1/goals",
        json={
            "payload": {
                "title": "偷别人的考试",
                "event_id": "ex_other",
                "daily_max_minutes": 90,
            }
        },
        headers=_headers(),
    )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "EXAM_NOT_FOUND"


def test_save_goal_invalid_daily_limit(client):
    resp = client.post(
        "/v1/goals",
        json={
            "payload": {
                "title": "非法上限",
                "event_id": seed_module.EXAM_ID,
                "daily_max_minutes": 999,
            }
        },
        headers=_headers(),
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_DAILY_LIMIT"


def test_save_goal_body_user_id_ignored(client, db_path):
    client.post(
        "/v1/goals",
        json={
            "payload": {
                "title": "伪造身份的目标",
                "event_id": seed_module.EXAM_ID,
                "daily_max_minutes": 60,
            },
            "user_id": "us_other",
        },
        headers=_headers(),
    )

    conn = get_connection(db_path)
    try:
        goal = conn.execute(
            "SELECT user_id FROM goals WHERE id = ?", (seed_module.GOAL_ID,)
        ).fetchone()
    finally:
        conn.close()

    assert goal["user_id"] == seed_module.DEMO_USER_ID


def test_add_class_via_compat(client):
    resp = client.post(
        "/v1/classes",
        json={
            "payload": {
                "course": "数据结构",
                "location": "B205",
                "start_at": "2026-12-01T08:00:00+08:00",
                "end_at": "2026-12-01T09:35:00+08:00",
            },
            "expected_version": 1,
        },
        headers=_headers(),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["class"]["course"] == "数据结构"

    data = client.get("/v1/workspace", headers=_headers()).json()["data"]
    assert any(c["course"] == "数据结构" for c in data["classes"])


def test_add_class_requires_auth(client):
    resp = client.post("/v1/classes", json={"payload": {}})
    assert resp.status_code == 401


def test_add_class_time_conflict(client):
    resp = client.post(
        "/v1/classes",
        json={
            "payload": {
                "course": "冲突课程",
                "location": "",
                "start_at": "2026-11-30T10:30:00+08:00",
                "end_at": "2026-11-30T11:00:00+08:00",
            }
        },
        headers=_headers(),
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CLASS_TIME_CONFLICT"


def test_add_class_invalid_time(client):
    resp = client.post(
        "/v1/classes",
        json={
            "payload": {
                "course": "时间反了",
                "location": "",
                "start_at": "2026-12-01T10:00:00+08:00",
                "end_at": "2026-12-01T09:00:00+08:00",
            }
        },
        headers=_headers(),
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_TIME"


# ---------------------------------------------------------------- 目标版本冲突
def test_confirm_blocked_after_goal_change(client, db_path):
    created = client.post(
        "/v1/previews",
        json={"event_id": seed_module.EXAM_ID, "new_start": "2026-12-02T14:00:00+08:00"},
        headers=_headers(),
    ).json()["data"]

    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE goals SET version = version + 1, updated_at = datetime('now','localtime') "
                "WHERE id = ?",
                (seed_module.GOAL_ID,),
            )
    finally:
        conn.close()

    resp = client.post(
        f"/v1/actions/{created['id']}/confirm",
        json={"confirm_code": created["confirm_code"]},
        headers=_headers(),
    )

    assert resp.status_code == 409
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "GOAL_VERSION_CONFLICT"
    assert "重新生成预览" in body["error"]["message"]

    conn = get_connection(db_path)
    try:
        exam = conn.execute(
            "SELECT exam_date FROM exams WHERE id = ?", (seed_module.EXAM_ID,)
        ).fetchone()
        plan = conn.execute(
            "SELECT version FROM plans WHERE status = 'confirmed'"
        ).fetchone()
        preview = conn.execute(
            "SELECT status FROM previews WHERE id = ?", (created["id"],)
        ).fetchone()
    finally:
        conn.close()

    assert exam["exam_date"] == "2026-12-04"     # 考试没被改
    assert plan["version"] == 1                  # 计划没被改
    assert preview["status"] == "pending"        # 预览没有被误标 confirmed
