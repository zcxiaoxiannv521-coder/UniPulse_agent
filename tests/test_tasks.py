"""services/tasks.py 的测试：进度反馈、用时记录、隔离、计划保护。"""

from __future__ import annotations

import pytest

from database.connection import get_connection, init_db
from demo import seed as seed_module
from services import clock, tasks


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T09:00:00+08:00")

    path = tmp_path / "tasks.db"
    init_db(path)
    connection = get_connection(path)
    seed_module.seed(connection)
    yield connection
    connection.close()


TASK = "tk_demo_ch3"


def test_record_progress(conn):
    result = tasks.record_progress(
        seed_module.DEMO_USER_ID,
        TASK,
        {"status": "doing", "remaining_minutes": 45, "feedback": "还剩一半"},
        conn=conn,
    )

    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (TASK,)).fetchone()
    assert task["status"] == "doing"
    assert task["remaining_minutes"] == 45
    assert result["task"]["feedback"] == "还剩一半"
    assert result["message"]


def test_partial_maps_to_doing(conn):
    """C 会发 partial，数据库只接受 todo/doing/done。"""
    tasks.record_progress(
        seed_module.DEMO_USER_ID, TASK, {"status": "partial", "remaining_minutes": 30},
        conn=conn,
    )
    task = conn.execute("SELECT status FROM tasks WHERE id = ?", (TASK,)).fetchone()
    assert task["status"] == "doing"


def test_mark_done_forces_zero_remaining(conn):
    result = tasks.record_progress(
        seed_module.DEMO_USER_ID, TASK,
        {"status": "done", "remaining_minutes": 10},
        conn=conn,
    )

    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (TASK,)).fetchone()
    assert task["status"] == "done"
    assert task["remaining_minutes"] == 0
    assert "完成" in result["message"]


def test_cannot_reopen_done_task(conn):
    tasks.record_progress(
        seed_module.DEMO_USER_ID, TASK, {"status": "done", "remaining_minutes": 0},
        conn=conn,
    )

    with pytest.raises(tasks.TaskError) as excinfo:
        tasks.record_progress(
            seed_module.DEMO_USER_ID, TASK,
            {"status": "doing", "remaining_minutes": 30},
            conn=conn,
        )
    assert excinfo.value.code == "TASK_ALREADY_DONE"


def test_invalid_remaining_values(conn):
    for value in (0, -5, 99999):
        with pytest.raises(tasks.TaskError) as excinfo:
            tasks.record_progress(
                seed_module.DEMO_USER_ID, TASK,
                {"status": "doing", "remaining_minutes": value},
                conn=conn,
            )
        assert excinfo.value.code in {"INVALID_REMAINING", "INVALID_ARGUMENT"}

    with pytest.raises(tasks.TaskError) as excinfo:
        tasks.record_progress(
            seed_module.DEMO_USER_ID, TASK,
            {"status": "doing", "remaining_minutes": "30"},
            conn=conn,
        )
    assert excinfo.value.code == "INVALID_ARGUMENT"


def test_invalid_status(conn):
    with pytest.raises(tasks.TaskError) as excinfo:
        tasks.record_progress(
            seed_module.DEMO_USER_ID, TASK,
            {"status": "finished", "remaining_minutes": 30},
            conn=conn,
        )
    assert excinfo.value.code == "INVALID_STATUS"


def test_task_not_found_and_isolation(conn):
    with conn:
        conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")

    with pytest.raises(tasks.TaskError) as excinfo:
        tasks.record_progress(
            "us_other", TASK, {"status": "doing", "remaining_minutes": 30}, conn=conn
        )
    assert excinfo.value.code == "TASK_NOT_FOUND"

    with pytest.raises(tasks.TaskError):
        tasks.record_time("us_other", TASK, 10, conn=conn)


def test_actual_minutes_is_cumulative(conn):
    first = tasks.record_progress(
        seed_module.DEMO_USER_ID, TASK,
        {"status": "doing", "remaining_minutes": 45, "actual_minutes": 30},
        conn=conn,
    )
    assert first["actual_minutes"] == 30

    second = tasks.record_progress(
        seed_module.DEMO_USER_ID, TASK,
        {"status": "doing", "remaining_minutes": 40, "actual_minutes": 50},
        conn=conn,
    )
    assert second["actual_minutes"] == 50

    logs = tasks.list_logs(seed_module.DEMO_USER_ID, TASK, conn=conn)
    assert [log["minutes"] for log in logs] == [30, 20]        # 只存增量


def test_actual_minutes_cannot_decrease(conn):
    tasks.record_progress(
        seed_module.DEMO_USER_ID, TASK,
        {"status": "doing", "remaining_minutes": 45, "actual_minutes": 30},
        conn=conn,
    )

    with pytest.raises(tasks.TaskError) as excinfo:
        tasks.record_progress(
            seed_module.DEMO_USER_ID, TASK,
            {"status": "doing", "remaining_minutes": 45, "actual_minutes": 10},
            conn=conn,
        )
    assert excinfo.value.code == "INVALID_ACTUAL"


def test_record_time_does_not_change_remaining(conn):
    before = conn.execute("SELECT * FROM tasks WHERE id = ?", (TASK,)).fetchone()

    result = tasks.record_time(seed_module.DEMO_USER_ID, TASK, 25, conn=conn)

    after = conn.execute("SELECT * FROM tasks WHERE id = ?", (TASK,)).fetchone()
    assert after["remaining_minutes"] == before["remaining_minutes"]
    assert after["status"] == before["status"]
    assert result["actual_minutes"] == 25
    assert "没有变化" in result["message"]


def test_record_time_is_incremental_and_idempotent(conn):
    tasks.record_time(seed_module.DEMO_USER_ID, TASK, 25, conn=conn)
    second = tasks.record_time(seed_module.DEMO_USER_ID, TASK, 40, conn=conn)

    assert second["actual_minutes"] == 40
    logs = tasks.list_logs(seed_module.DEMO_USER_ID, TASK, conn=conn)
    assert [log["minutes"] for log in logs] == [25, 15]

    # 重复上报同一个累计值不新增记录
    tasks.record_time(seed_module.DEMO_USER_ID, TASK, 40, conn=conn)
    assert len(tasks.list_logs(seed_module.DEMO_USER_ID, TASK, conn=conn)) == 2


def test_record_time_rejects_negative(conn):
    with pytest.raises(tasks.TaskError) as excinfo:
        tasks.record_time(seed_module.DEMO_USER_ID, TASK, -1, conn=conn)
    assert excinfo.value.code == "INVALID_ARGUMENT"


def test_progress_does_not_touch_plan_or_blocks(conn):
    def snapshot():
        return (
            [tuple(r) for r in conn.execute("SELECT * FROM plans")],
            [tuple(r) for r in conn.execute("SELECT * FROM plan_items")],
        )

    before = snapshot()
    tasks.record_progress(
        seed_module.DEMO_USER_ID, TASK,
        {"status": "done", "remaining_minutes": 0},
        conn=conn,
    )
    tasks.record_time(seed_module.DEMO_USER_ID, TASK, 30, conn=conn)

    assert snapshot() == before


def test_scheduler_uses_updated_remaining(conn):
    """改剩余工作量后，重新排程用的是最新值（不写库）。"""
    from services import scheduler

    before = scheduler.generate_schedule(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )
    assert before["required_minutes"] == 150        # 60 + 90（必做）
    assert before["total_requested_minutes"] == 230

    tasks.record_progress(
        seed_module.DEMO_USER_ID, TASK,
        {"status": "doing", "remaining_minutes": 30},
        conn=conn,
    )

    after = scheduler.generate_schedule(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )
    assert after["required_minutes"] == 30 + 90     # 用最新的 30
    assert after["total_requested_minutes"] == 30 + 90 + 80
