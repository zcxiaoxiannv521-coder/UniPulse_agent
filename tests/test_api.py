"""api/app.py 的接口测试：认证、隔离、参数校验、只读。"""

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
    """独立的演示数据库 + 固定演示时间。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T09:00:00+08:00")
    monkeypatch.delenv(auth.ENV_AUTH_MODE, raising=False)
    monkeypatch.delenv(auth.ENV_TOKEN_SECRET, raising=False)

    path = tmp_path / "api.db"
    init_db(path)
    conn = get_connection(path)
    seed_module.seed(conn)
    conn.close()
    return path


@pytest.fixture()
def client(db_path, monkeypatch):
    """把 app 指向临时数据库，避免动到 data/unipulse.db。"""
    monkeypatch.setattr(queries, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(preview_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(scheduler_module, "get_connection", lambda: get_connection(db_path))
    monkeypatch.setattr(app_module, "init_db", lambda *args, **kwargs: db_path)
    monkeypatch.setattr(app_module, "DB_PATH", db_path)

    with TestClient(app_module.app) as test_client:
        yield test_client


def _bearer(user_id: str = seed_module.DEMO_USER_ID, **kwargs) -> dict:
    return {"Authorization": f"Bearer {auth.create_token(user_id, **kwargs)}"}


def test_health_without_auth(client):
    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["auth_mode"] == "demo"
    assert body["time_mode"] == "demo"


def test_dashboard_requires_token(client):
    resp = client.get("/api/v1/dashboard")

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
    assert resp.json()["error"]["details"]["reason"] == "MISSING_TOKEN"


def test_bad_token_rejected(client):
    resp = client.get("/api/v1/dashboard", headers={"Authorization": "Bearer abc.def"})

    assert resp.status_code == 401
    assert resp.json()["error"]["details"]["reason"] == "BAD_SIGNATURE"


def test_wrong_scheme_rejected(client):
    resp = client.get("/api/v1/dashboard", headers={"Authorization": "Token xyz"})

    assert resp.status_code == 401
    assert resp.json()["error"]["details"]["reason"] == "MALFORMED_TOKEN"


def test_expired_token_rejected(client):
    headers = _bearer(ttl_minutes=-1)
    resp = client.get("/api/v1/dashboard", headers=headers)

    assert resp.status_code == 401
    assert resp.json()["error"]["details"]["reason"] == "TOKEN_EXPIRED"


def test_dashboard_with_valid_token(client):
    resp = client.get("/api/v1/dashboard", headers=_bearer())

    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["id"] == seed_module.DEMO_USER_ID
    assert body["today"] == "2026-11-30"
    assert body["plan"]["version"] == 1
    assert len(body["days"]) == 7


def test_token_in_query_string(client):
    """看板链接形式：?token=...（C 的页面用这种）。"""
    token = auth.create_token(seed_module.DEMO_USER_ID)
    resp = client.get("/api/v1/dashboard", params={"token": token})

    assert resp.status_code == 200
    assert resp.json()["user"]["id"] == seed_module.DEMO_USER_ID


def test_cannot_read_other_user_by_changing_params(client):
    """带上自己的 Token，再往 URL 里塞别人的 user_id 也没用。"""
    resp = client.get(
        "/api/v1/tasks",
        params={"user_id": "us_demo_b", "token": auth.create_token(seed_module.DEMO_USER_ID)},
    )

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 3
    assert all(item["id"].startswith("tk_demo_") for item in items)


def test_unknown_user_with_valid_token(client):
    """签名合法但用户不存在：404，不是 200 空数据。"""
    resp = client.get("/api/v1/dashboard", headers=_bearer("us_not_exist"))

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
    assert resp.json()["error"]["details"]["reason"] == "USER_NOT_FOUND"


def test_courses_endpoint(client):
    resp = client.get("/api/v1/courses", headers=_bearer())

    assert resp.status_code == 200
    body = resp.json()
    assert body["date_from"] == "2026-11-30"
    assert body["date_to"] == "2026-12-06"
    assert len(body["items"]) == 3
    dates = [(item["session_date"], item["start_time"]) for item in body["items"]]
    assert dates == sorted(dates)


def test_exams_endpoint(client):
    resp = client.get("/api/v1/exams", headers=_bearer())

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["exam_date"] == "2026-12-04"


def test_tasks_endpoint_and_filter(client):
    all_tasks = client.get("/api/v1/tasks", headers=_bearer()).json()["items"]
    assert len(all_tasks) == 3

    done = client.get("/api/v1/tasks", params={"status": "done"}, headers=_bearer())
    assert done.status_code == 200
    assert done.json()["items"] == []


def test_tasks_invalid_status_rejected(client):
    resp = client.get("/api/v1/tasks", params={"status": "whatever"}, headers=_bearer())

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_bad_date_param_rejected(client):
    resp = client.get(
        "/api/v1/courses", params={"date_from": "2026/11/30"}, headers=_bearer()
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_current_plan_endpoint(client):
    resp = client.get("/api/v1/plans/current", headers=_bearer())

    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"]["id"] == seed_module.PLAN_ID
    assert body["plan"]["status"] == "confirmed"
    assert len(body["items"]) == 3


def test_endpoints_are_read_only(client, db_path):
    """跑一遍所有接口，数据库内容不能有任何变化。"""
    tables = [
        "users", "user_preferences", "courses", "class_sessions", "exams",
        "goals", "tasks", "plans", "plan_items", "previews", "notifications", "reminders",
    ]

    def snapshot():
        conn = get_connection(db_path)
        try:
            return {
                table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
                for table in tables
            }
        finally:
            conn.close()

    before = snapshot()
    headers = _bearer()
    for path in (
        "/api/v1/dashboard",
        "/api/v1/courses",
        "/api/v1/exams",
        "/api/v1/tasks",
        "/api/v1/plans/current",
    ):
        assert client.get(path, headers=headers).status_code == 200
    after = snapshot()

    assert before == after


def test_access_log_redacts_token():
    """访问日志不能把完整 Token 打出来。"""
    import logging

    from api.app import RedactTokenFilter

    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1", "GET", "/api/v1/dashboard?token=SECRET123&x=1", "1.1", 200),
        exc_info=None,
    )
    RedactTokenFilter().filter(record)

    message = record.getMessage()
    assert "SECRET123" not in message
    assert "token=<redacted>" in message
    assert "x=1" in message


# ---------------------------------------------------------------- 预览与确认接口
def _make_preview(db_path):
    conn = get_connection(db_path)
    try:
        return preview_module.create_preview(
            seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
        )
    finally:
        conn.close()


def test_get_preview_via_api(client, db_path):
    p = _make_preview(db_path)
    resp = client.get(f"/api/v1/previews/{p['preview_id']}", headers=_bearer())

    assert resp.status_code == 200
    body = resp.json()
    assert body["preview_id"] == p["preview_id"]
    assert body["status"] == "pending"
    assert "diff" in body and "summary" in body


def test_preview_endpoints_require_auth(client, db_path):
    p = _make_preview(db_path)

    assert client.get(f"/api/v1/previews/{p['preview_id']}").status_code == 401
    assert client.post(f"/api/v1/previews/{p['preview_id']}/confirm",
                       json={"confirm_code": p["confirm_code"]}).status_code == 401
    assert client.post(f"/api/v1/previews/{p['preview_id']}/cancel").status_code == 401


def test_get_preview_other_user_404(client, db_path):
    p = _make_preview(db_path)
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")
        token = auth.create_token("us_other")
    finally:
        conn.close()

    resp = client.get(
        f"/api/v1/previews/{p['preview_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PREVIEW_NOT_FOUND"


def test_confirm_via_api(client, db_path):
    p = _make_preview(db_path)
    resp = client.post(
        f"/api/v1/previews/{p['preview_id']}/confirm",
        json={"confirm_code": p["confirm_code"], "expected_version": 1},
        headers=_bearer(),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["applied"]["plan_version"] == 2


def test_confirm_wrong_code_422(client, db_path):
    p = _make_preview(db_path)
    resp = client.post(
        f"/api/v1/previews/{p['preview_id']}/confirm",
        json={"confirm_code": "T99"},
        headers=_bearer(),
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "CONFIRM_CODE_MISMATCH"


def test_confirm_missing_code_422(client, db_path):
    p = _make_preview(db_path)
    resp = client.post(
        f"/api/v1/previews/{p['preview_id']}/confirm", json={}, headers=_bearer()
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "MISSING_CONFIRM_CODE"


def test_confirm_expired_409(client, db_path):
    p = _make_preview(db_path)
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE previews SET expires_at = '2026-11-30T08:00:00+08:00' WHERE id = ?",
                (p["preview_id"],),
            )
    finally:
        conn.close()

    resp = client.post(
        f"/api/v1/previews/{p['preview_id']}/confirm",
        json={"confirm_code": p["confirm_code"]},
        headers=_bearer(),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "PREVIEW_EXPIRED"


def test_cancel_then_confirm_409(client, db_path):
    p = _make_preview(db_path)
    cancelled = client.post(
        f"/api/v1/previews/{p['preview_id']}/cancel", headers=_bearer()
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    resp = client.post(
        f"/api/v1/previews/{p['preview_id']}/confirm",
        json={"confirm_code": p["confirm_code"]},
        headers=_bearer(),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "PREVIEW_NOT_PENDING"


def test_other_user_cannot_confirm(client, db_path):
    p = _make_preview(db_path)
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")
        token = auth.create_token("us_other")
    finally:
        conn.close()

    resp = client.post(
        f"/api/v1/previews/{p['preview_id']}/confirm",
        json={"confirm_code": p["confirm_code"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
