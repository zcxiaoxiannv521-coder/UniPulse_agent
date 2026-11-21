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
            "source_notice_ids": ["nt_demo"],
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
