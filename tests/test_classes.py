"""services/classes.py 的测试：课程/课次录入、校验、冲突、隔离、事务。"""

from __future__ import annotations

import pytest

from database.connection import get_connection, init_db
from demo import seed as seed_module
from services import classes, clock, workspace


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T09:00:00+08:00")

    path = tmp_path / "classes.db"
    init_db(path)
    connection = get_connection(path)
    seed_module.seed(connection)
    yield connection
    connection.close()


PAYLOAD = {
    "course": "数据结构",
    "location": "B205",
    "start_at": "2026-12-01T08:00:00+08:00",
    "end_at": "2026-12-01T09:35:00+08:00",
}


def test_add_class_creates_course_and_session(conn):
    result = classes.add_class(seed_module.DEMO_USER_ID, PAYLOAD, conn=conn)

    assert result["class"]["course"] == "数据结构"
    assert result["class"]["start_at"].startswith("2026-12-01T08:00")
    assert result["session_id"].startswith("cs_")
    assert result["course_id"].startswith("cr_")

    row = conn.execute(
        "SELECT * FROM class_sessions WHERE id = ?", (result["session_id"],)
    ).fetchone()
    assert row["session_date"] == "2026-12-01"
    assert (row["start_time"], row["end_time"]) == ("08:00", "09:35")
    assert row["status"] == "normal"


def test_same_course_name_reuses_course_row(conn):
    first = classes.add_class(seed_module.DEMO_USER_ID, PAYLOAD, conn=conn)
    second = classes.add_class(
        seed_module.DEMO_USER_ID,
        {**PAYLOAD, "start_at": "2026-12-02T08:00:00+08:00",
         "end_at": "2026-12-02T09:35:00+08:00"},
        conn=conn,
    )

    assert first["course_id"] == second["course_id"]
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM courses WHERE user_id = ? AND name = '数据结构'",
        (seed_module.DEMO_USER_ID,),
    ).fetchone()["n"]
    assert count == 1


def test_workspace_shows_new_class(conn):
    before = workspace.build_workspace(seed_module.DEMO_USER_ID, conn=conn)
    assert len(before["classes"]) == 3

    classes.add_class(seed_module.DEMO_USER_ID, PAYLOAD, conn=conn)

    after = workspace.build_workspace(seed_module.DEMO_USER_ID, conn=conn)
    assert len(after["classes"]) == 4
    created = [c for c in after["classes"] if c["course"] == "数据结构"]
    assert created[0]["location"] == "B205"


def test_invalid_times(conn):
    cases = [
        ({**PAYLOAD, "end_at": "2026-12-01T07:00:00+08:00"}, "INVALID_TIME"),
        ({**PAYLOAD, "end_at": "2026-12-01T08:00:00+08:00"}, "INVALID_TIME"),
        ({**PAYLOAD, "end_at": "2026-12-02T09:35:00+08:00"}, "CROSS_DAY_NOT_SUPPORTED"),
        ({**PAYLOAD, "start_at": "2026-12-01T08:00:00"}, "INVALID_TIME"),
        ({**PAYLOAD, "start_at": "不是时间"}, "INVALID_TIME"),
    ]
    for payload, code in cases:
        with pytest.raises(classes.ClassError) as excinfo:
            classes.add_class(seed_module.DEMO_USER_ID, payload, conn=conn)
        assert excinfo.value.code == code


def test_invalid_course(conn):
    with pytest.raises(classes.ClassError) as excinfo:
        classes.add_class(
            seed_module.DEMO_USER_ID, {**PAYLOAD, "course": "   "}, conn=conn
        )
    assert excinfo.value.code == "INVALID_COURSE"

    with pytest.raises(classes.ClassError) as excinfo:
        classes.add_class(
            seed_module.DEMO_USER_ID, {**PAYLOAD, "course": "课" * 61}, conn=conn
        )
    assert excinfo.value.code == "INVALID_COURSE"


def test_time_conflict(conn):
    # 演示数据里 11-30 10:00–11:30 已有「机器学习」
    with pytest.raises(classes.ClassError) as excinfo:
        classes.add_class(
            seed_module.DEMO_USER_ID,
            {
                "course": "冲突课程",
                "location": "",
                "start_at": "2026-11-30T10:30:00+08:00",
                "end_at": "2026-11-30T11:00:00+08:00",
            },
            conn=conn,
        )
    assert excinfo.value.code == "CLASS_TIME_CONFLICT"


def test_user_isolation(conn):
    with conn:
        conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")

    classes.add_class("us_other", PAYLOAD, conn=conn)

    mine = workspace.build_workspace(seed_module.DEMO_USER_ID, conn=conn)
    theirs = workspace.build_workspace("us_other", conn=conn)

    assert all(c["course"] != "数据结构" for c in mine["classes"])
    assert [c["course"] for c in theirs["classes"]] == ["数据结构"]


def test_class_does_not_touch_exams_or_plans(conn):
    def snapshot():
        return (
            [tuple(r) for r in conn.execute("SELECT * FROM exams")],
            [tuple(r) for r in conn.execute("SELECT * FROM plans")],
            [tuple(r) for r in conn.execute("SELECT * FROM plan_items")],
        )

    before = snapshot()
    classes.add_class(seed_module.DEMO_USER_ID, PAYLOAD, conn=conn)
    assert snapshot() == before


def test_rollback_when_session_insert_fails(conn, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("模拟写课次失败")

    monkeypatch.setattr(classes, "_insert_session", boom)

    before = conn.execute(
        "SELECT COUNT(*) AS n FROM courses WHERE name = '数据结构'"
    ).fetchone()["n"]

    with pytest.raises(RuntimeError):
        classes.add_class(seed_module.DEMO_USER_ID, PAYLOAD, conn=conn)

    after = conn.execute(
        "SELECT COUNT(*) AS n FROM courses WHERE name = '数据结构'"
    ).fetchone()["n"]
    assert after == before      # 课程没有被单独留下
