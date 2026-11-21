"""services/preview.py 的测试。

覆盖：创建预览、正式数据不变、版本记录、差异（新增/移除/调整/保护）、
过期、重复创建、跨用户隔离、缺口、事务回滚、确定性。
"""

from __future__ import annotations

import pytest

from database.connection import get_connection, init_db
from demo import seed as seed_module
from services import clock, preview, scheduler
from services.preview import PreviewError


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T09:00:00+08:00")

    path = tmp_path / "preview.db"
    init_db(path)
    connection = get_connection(path)
    seed_module.seed(connection)
    yield connection
    connection.close()


def _scenario(conn, *, user="us_t", exam_date="2026-12-04", daily_limit=90,
              tasks=(), items=()):
    goal_id, plan_id = f"gl_{user}", f"pl_{user}"
    with conn:
        conn.execute("INSERT INTO users (id, name) VALUES (?, ?)", (user, user))
        conn.execute(
            "INSERT INTO exams (id, user_id, name, exam_date, start_time, end_time) "
            "VALUES (?, ?, '测试考试', ?, '14:00', '15:30')",
            (f"ex_{user}", user, exam_date),
        )
        conn.execute(
            "INSERT INTO goals (id, user_id, exam_id, title, daily_limit_minutes) "
            "VALUES (?, ?, ?, '测试目标', ?)",
            (goal_id, user, f"ex_{user}", daily_limit),
        )
        conn.execute(
            "INSERT INTO plans (id, user_id, goal_id, status, version) "
            "VALUES (?, ?, ?, 'confirmed', 1)",
            (plan_id, user, goal_id),
        )
        for task in tasks:
            conn.execute(
                """
                INSERT INTO tasks (id, user_id, goal_id, title, estimated_minutes,
                                   remaining_minutes, priority, is_optional,
                                   splittable, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'todo')
                """,
                (task["id"], user, goal_id, task["title"], task["minutes"],
                 task["minutes"], task.get("priority", 2),
                 1 if task.get("optional") else 0,
                 1 if task.get("splittable") else 0),
            )
        for item in items:
            conn.execute(
                """
                INSERT INTO plan_items (id, plan_id, start_at, end_at, title,
                                        item_type, task_id, is_locked)
                VALUES (?, ?, ?, ?, ?, 'task', ?, ?)
                """,
                (item["id"], plan_id, item["start"], item["end"],
                 item.get("title", "学习"), item.get("task_id"),
                 1 if item.get("locked") else 0),
            )
    return user, f"ex_{user}"


def _iso(day: str, hm: str) -> str:
    return f"{day}T{hm}:00+08:00"


# ---------------------------------------------------------------- 创建
def test_create_preview(conn):
    result = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    assert result["status"] == "pending"
    assert result["confirm_code"].startswith("T")
    assert result["base_version"] == 1
    assert result["base_plan_id"] == seed_module.PLAN_ID
    assert result["old_plan"]["id"] == seed_module.PLAN_ID
    assert len(result["new_schedule"]["schedule_items"]) == 3
    assert result["summary"]["required_minutes"] == 150
    assert result["exam"]["exam_date"] == "2026-12-04"


def test_formal_plan_unchanged(conn):
    """创建预览不能改动任何正式数据。"""
    tables = ["plans", "plan_items", "tasks", "exams", "goals", "class_sessions"]

    def snapshot():
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in tables
        }

    before = snapshot()
    preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    after = snapshot()

    assert before == after


def test_base_version_recorded(conn):
    result = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    row = conn.execute(
        "SELECT base_plan_id, base_version, status FROM previews WHERE id = ?",
        (result["preview_id"],),
    ).fetchone()

    assert row["base_plan_id"] == seed_module.PLAN_ID
    assert row["base_version"] == 1
    assert row["status"] == "pending"


# ---------------------------------------------------------------- 差异
def test_diff_added_and_changed(conn):
    """旧计划只有一个大块，重排后一分为二：一个调整、一个新增。"""
    user, exam = _scenario(
        conn,
        daily_limit=180,
        tasks=[
            {"id": "tk_a", "title": "任务A", "minutes": 60},
            {"id": "tk_b", "title": "任务B", "minutes": 60},
        ],
        items=[{"id": "pi_old", "start": _iso("2026-12-01", "19:00"),
                "end": _iso("2026-12-01", "21:00"), "task_id": "tk_a"}],
    )

    result = preview.create_preview(user, exam, conn=conn)

    assert [item["task_id"] for item in result["diff"]["changed"]] == ["tk_a"]
    assert result["diff"]["changed"][0]["after"][0]["start_at"] == _iso("2026-12-01", "19:00")
    assert result["diff"]["changed"][0]["after"][0]["end_at"] == _iso("2026-12-01", "20:00")
    assert [item["task_id"] for item in result["diff"]["added"]] == ["tk_b"]


def test_diff_removed(conn):
    """考试提前后，12-02 的选做块被移出。"""
    with conn:
        conn.execute(
            "UPDATE exams SET exam_date = '2026-12-02', start_time = '14:00' "
            "WHERE id = ?",
            (seed_module.EXAM_ID,),
        )

    result = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    assert len(result["diff"]["removed"]) == 1
    assert result["diff"]["removed"][0]["task_id"] == "tk_demo_mock"
    assert result["diff"]["removed"][0]["blocks"][0]["start_at"] == _iso("2026-12-02", "15:00")
    assert len(result["diff"]["kept"]) == 2
    assert result["diff"]["added"] == []
    assert result["diff"]["changed"] == []


def test_completed_block_protected(conn):
    with conn:
        conn.execute("UPDATE tasks SET status = 'done' WHERE id = 'tk_demo_ch3'")

    result = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    kept = {item["task_id"]: item["reason"] for item in result["diff"]["kept"]}
    assert kept["tk_demo_ch3"] == "task_done"
    assert all(item["task_id"] != "tk_demo_ch3" for item in result["diff"]["removed"])


def test_locked_block_protected(conn):
    with conn:
        conn.execute("UPDATE plan_items SET is_locked = 1 WHERE id = 'pi_demo_01'")

    result = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    kept = {item["task_id"]: item["reason"] for item in result["diff"]["kept"]}
    assert kept["tk_demo_ch3"] == "locked"


def test_started_block_protected(conn):
    with conn:
        conn.execute(
            "UPDATE plan_items SET start_at = ?, end_at = ? WHERE id = 'pi_demo_01'",
            ("2026-11-30T08:00:00+08:00", "2026-11-30T09:00:00+08:00"),
        )

    result = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    kept = {item["task_id"]: item["reason"] for item in result["diff"]["kept"]}
    assert kept["tk_demo_ch3"] == "started"


# ---------------------------------------------------------------- 有效期
def test_expired_preview(conn):
    result = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn, ttl_minutes=5
    )
    with conn:
        conn.execute(
            "UPDATE previews SET expires_at = '2026-11-30T08:00:00+08:00' WHERE id = ?",
            (result["preview_id"],),
        )

    shown = preview.get_preview(
        seed_module.DEMO_USER_ID, result["preview_id"], conn=conn
    )
    assert shown["status"] == "expired"

    with pytest.raises(PreviewError) as excinfo:
        preview.assert_confirmable(
            seed_module.DEMO_USER_ID, result["preview_id"], conn=conn
        )
    assert excinfo.value.code == "PREVIEW_EXPIRED"

    with pytest.raises(PreviewError) as excinfo:
        preview.cancel_preview(
            seed_module.DEMO_USER_ID, result["preview_id"], conn=conn
        )
    assert excinfo.value.code == "PREVIEW_NOT_PENDING"


def test_cancel_then_confirm_rejected(conn):
    result = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )
    cancelled = preview.cancel_preview(
        seed_module.DEMO_USER_ID, result["preview_id"], conn=conn
    )
    assert cancelled["status"] == "cancelled"

    with pytest.raises(PreviewError) as excinfo:
        preview.assert_confirmable(
            seed_module.DEMO_USER_ID, result["preview_id"], conn=conn
        )
    assert excinfo.value.code == "PREVIEW_NOT_PENDING"


def test_repeat_create_reuses_pending(conn):
    first = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )
    second = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    assert second["reused"] is True
    assert second["preview_id"] == first["preview_id"]
    count = conn.execute("SELECT COUNT(*) AS n FROM previews").fetchone()["n"]
    assert count == 1


# ---------------------------------------------------------------- 隔离与错误
def test_other_user_cannot_read(conn):
    result = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )
    with conn:
        conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")

    with pytest.raises(PreviewError) as excinfo:
        preview.get_preview("us_other", result["preview_id"], conn=conn)
    assert excinfo.value.code == "PREVIEW_NOT_FOUND"

    with pytest.raises(PreviewError):
        preview.assert_confirmable("us_other", result["preview_id"], conn=conn)

    with pytest.raises(PreviewError):
        preview.cancel_preview("us_other", result["preview_id"], conn=conn)


def test_confirm_by_code_is_user_scoped(conn):
    result = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )
    with conn:
        conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")

    ok = preview.assert_confirmable(
        seed_module.DEMO_USER_ID, confirm_code=result["confirm_code"], conn=conn
    )
    assert ok["confirmable"] is True

    with pytest.raises(PreviewError) as excinfo:
        preview.assert_confirmable(
            "us_other", confirm_code=result["confirm_code"], conn=conn
        )
    assert excinfo.value.code == "PREVIEW_NOT_FOUND"


def test_exam_not_found(conn):
    with pytest.raises(scheduler.SchedulerError) as excinfo:
        preview.create_preview(
            seed_module.DEMO_USER_ID, "ex_not_exist", conn=conn
        )
    assert excinfo.value.code == "EXAM_NOT_FOUND"


# ---------------------------------------------------------------- 缺口
def test_required_gap_shown(conn):
    user, exam = _scenario(
        conn,
        tasks=[
            {"id": "tk_1", "title": "1", "minutes": 90},
            {"id": "tk_2", "title": "2", "minutes": 60},
        ],
        items=[{"id": "pi_1", "start": _iso("2026-12-01", "19:00"),
                "end": _iso("2026-12-01", "20:30"), "task_id": "tk_1"}],
    )

    result = preview.create_preview(user, exam, conn=conn)

    assert result["summary"]["required_minutes"] == 150
    assert result["summary"]["required_gap_minutes"] == 60
    assert any("还差 60 分钟" in note for note in result["notes"])


def test_optional_gap_separate(conn):
    with conn:
        conn.execute(
            "UPDATE exams SET exam_date = '2026-12-02', start_time = '14:00' "
            "WHERE id = ?",
            (seed_module.EXAM_ID,),
        )

    result = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    assert result["summary"]["required_gap_minutes"] == 0
    assert result["summary"]["unscheduled_optional_minutes"] == 80
    assert result["summary"]["scheduled_optional_minutes"] == 0


# ---------------------------------------------------------------- 事务与确定性
def test_rollback_on_failure(conn, monkeypatch):
    """写入后回读失败时，整条预览必须回滚。"""

    def boom(*args, **kwargs):
        raise RuntimeError("模拟回读失败")

    monkeypatch.setattr(preview, "_load_preview_row", boom)

    with pytest.raises(RuntimeError):
        preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)

    count = conn.execute("SELECT COUNT(*) AS n FROM previews").fetchone()["n"]
    assert count == 0


def test_diff_deterministic(conn):
    """取消后重新生成，差异内容应完全一致。"""
    first = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )
    preview.cancel_preview(seed_module.DEMO_USER_ID, first["preview_id"], conn=conn)

    second = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    assert second["preview_id"] != first["preview_id"]
    assert second["diff"] == first["diff"]
    assert second["summary"] == first["summary"]


# ---------------------------------------------------------------- 版本冲突
def test_version_conflict_detected(conn):
    result = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    # 模拟别人确认了新计划：版本 +1
    with conn:
        conn.execute(
            "UPDATE plans SET version = version + 1 WHERE id = ?",
            (seed_module.PLAN_ID,),
        )

    with pytest.raises(PreviewError) as excinfo:
        preview.assert_confirmable(
            seed_module.DEMO_USER_ID, result["preview_id"], conn=conn
        )
    assert excinfo.value.code == "VERSION_CONFLICT"
