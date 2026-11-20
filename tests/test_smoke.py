"""冒烟测试：确认项目基础环境是通的。

运行（在 unipulse 目录下）：pytest -q
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from api.app import app
from database.connection import get_connection, init_db


def test_init_db_creates_tables(tmp_path):
    """建库后，六张核心表都应该存在。"""
    db_file = tmp_path / "test.db"
    init_db(db_file)

    conn = sqlite3.connect(db_file)
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    expected = {
        "users",
        "user_preferences",
        "courses",
        "class_sessions",
        "exams",
        "goals",
        "tasks",
        "plans",
        "plan_items",
        "previews",
        "notifications",
        "reminders",
    }
    assert expected.issubset(names), f"缺少表：{expected - names}"


def test_health_endpoint(tmp_path, monkeypatch):
    """服务能启动，/health 返回 ok。"""
    db_file = tmp_path / "health.db"
    init_db(db_file)

    with TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_connection_returns_rows_like_dict(tmp_path):
    """连接工具返回的行可以按列名取值，方便写业务代码。"""
    db_file = tmp_path / "rows.db"
    init_db(db_file)

    conn = get_connection(db_file)
    try:
        with conn:
            conn.execute("INSERT INTO users (id, name) VALUES (?, ?)", ("u1", "小吴"))
        row = conn.execute("SELECT id, name FROM users WHERE id = ?", ("u1",)).fetchone()
    finally:
        conn.close()

    assert row["name"] == "小吴"
