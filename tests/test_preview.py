"""services/preview.py 的测试。

覆盖：创建预览、正式数据不变、版本记录、差异（新增/移除/调整/保护）、
过期、重复创建、跨用户隔离、缺口、事务回滚、确定性。
"""

from __future__ import annotations

import json

import pytest

from database.connection import get_connection, init_db
from demo import seed as seed_module
from services import clock, goals as goals_service, preview, scheduler
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


# ---------------------------------------------------------------- 确认提交
def _confirmed_plan(conn, user_id):
    return conn.execute(
        "SELECT * FROM plans WHERE user_id = ? AND status = 'confirmed' "
        "ORDER BY version DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def _plan_items(conn, plan_id):
    return conn.execute(
        "SELECT * FROM plan_items WHERE plan_id = ? ORDER BY start_at", (plan_id,)
    ).fetchall()


def test_confirm_success(conn):
    """正常确认：新计划 v2，旧计划 superseded，预览 confirmed。"""
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    result = preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )

    assert result["ok"] is True
    assert result["duplicate"] is False
    assert result["applied"]["plan_version"] == 2
    assert result["applied"]["plan_status"] == "confirmed"

    plan = _confirmed_plan(conn, seed_module.DEMO_USER_ID)
    assert plan["version"] == 2
    assert plan["based_on_plan_id"] == seed_module.PLAN_ID

    old = conn.execute(
        "SELECT status FROM plans WHERE id = ?", (seed_module.PLAN_ID,)
    ).fetchone()
    assert old["status"] == "superseded"

    row = conn.execute(
        "SELECT status, confirmed_at FROM previews WHERE id = ?", (p["preview_id"],)
    ).fetchone()
    assert row["status"] == "confirmed"
    assert row["confirmed_at"] is not None


def test_confirm_writes_new_schedule(conn):
    """考试提前后确认：新计划只剩两个必做块。"""
    with conn:
        conn.execute(
            "UPDATE exams SET exam_date = '2026-12-02', start_time = '14:00' "
            "WHERE id = ?",
            (seed_module.EXAM_ID,),
        )

    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )

    plan = _confirmed_plan(conn, seed_module.DEMO_USER_ID)
    items = _plan_items(conn, plan["id"])

    assert [item["title"] for item in items] == ["复习第三章", "复习第四章"]
    assert items[0]["start_at"] == "2026-11-30T19:00:00+08:00"
    assert items[1]["end_at"] == "2026-12-01T20:30:00+08:00"


def test_confirm_is_idempotent(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    first = preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )
    second = preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )

    assert second["duplicate"] is True
    assert second["applied"]["plan_version"] == first["applied"]["plan_version"]
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM plans WHERE user_id = ?",
        (seed_module.DEMO_USER_ID,),
    ).fetchone()["n"]
    assert count == 2          # 只有 v1 和 v2，没有重复建计划


def test_confirm_expired_rejected(conn):
    p = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn, ttl_minutes=5
    )
    with conn:
        conn.execute(
            "UPDATE previews SET expires_at = '2026-11-30T08:00:00+08:00' WHERE id = ?",
            (p["preview_id"],),
        )

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
        )
    assert excinfo.value.code == "PREVIEW_EXPIRED"
    assert _confirmed_plan(conn, seed_module.DEMO_USER_ID)["version"] == 1


def test_confirm_cancelled_rejected(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    preview.cancel_preview(seed_module.DEMO_USER_ID, p["preview_id"], conn=conn)

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
        )
    assert excinfo.value.code == "PREVIEW_NOT_PENDING"


def test_confirm_version_conflict(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    with conn:
        conn.execute(
            "UPDATE plans SET version = version + 1 WHERE id = ?",
            (seed_module.PLAN_ID,),
        )

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
        )
    assert excinfo.value.code == "VERSION_CONFLICT"


def test_confirm_expected_version_conflict(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"],
            conn=conn, expected_version=99,
        )
    assert excinfo.value.code == "VERSION_CONFLICT"


def test_confirm_wrong_code(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, p["preview_id"], "T99", conn=conn
        )
    assert excinfo.value.code == "CONFIRM_CODE_MISMATCH"


def test_confirm_missing_code(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(seed_module.DEMO_USER_ID, p["preview_id"], None, conn=conn)
    assert excinfo.value.code == "MISSING_CONFIRM_CODE"


def test_confirm_invalid_code_format(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, p["preview_id"], "T", conn=conn
        )
    assert excinfo.value.code == "INVALID_CONFIRM_CODE"


def test_confirm_other_user(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    with conn:
        conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(
            "us_other", p["preview_id"], p["confirm_code"], conn=conn
        )
    assert excinfo.value.code == "PREVIEW_NOT_FOUND"


def test_confirm_protects_completed_block(conn):
    with conn:
        conn.execute("UPDATE tasks SET status = 'done' WHERE id = 'tk_demo_ch3'")

    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )

    plan = _confirmed_plan(conn, seed_module.DEMO_USER_ID)
    items = _plan_items(conn, plan["id"])
    ch3 = [item for item in items if item["task_id"] == "tk_demo_ch3"]
    assert len(ch3) == 1
    assert ch3[0]["start_at"] == "2026-11-30T19:00:00+08:00"


def test_confirm_protects_locked_block(conn):
    with conn:
        conn.execute("UPDATE plan_items SET is_locked = 1 WHERE id = 'pi_demo_01'")

    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )

    plan = _confirmed_plan(conn, seed_module.DEMO_USER_ID)
    locked = [
        item for item in _plan_items(conn, plan["id"])
        if item["task_id"] == "tk_demo_ch3" and item["is_locked"] == 1
    ]
    assert len(locked) == 1
    assert locked[0]["start_at"] == "2026-11-30T19:00:00+08:00"


def test_confirm_protects_started_block(conn):
    with conn:
        conn.execute(
            "UPDATE plan_items SET start_at = ?, end_at = ? WHERE id = 'pi_demo_01'",
            ("2026-11-30T08:00:00+08:00", "2026-11-30T09:00:00+08:00"),
        )

    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )

    plan = _confirmed_plan(conn, seed_module.DEMO_USER_ID)
    started = [
        item for item in _plan_items(conn, plan["id"])
        if item["start_at"] == "2026-11-30T08:00:00+08:00"
    ]
    assert len(started) == 1


def test_confirm_keeps_unaffected_blocks(conn):
    """考试改期但前三章/四章的时间没变，确认后仍在。"""
    with conn:
        conn.execute(
            "UPDATE exams SET exam_date = '2026-12-02', start_time = '14:00' "
            "WHERE id = ?",
            (seed_module.EXAM_ID,),
        )

    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )

    plan = _confirmed_plan(conn, seed_module.DEMO_USER_ID)
    items = {item["task_id"]: item for item in _plan_items(conn, plan["id"])}
    assert items["tk_demo_ch3"]["start_at"] == "2026-11-30T19:00:00+08:00"
    assert items["tk_demo_ch4"]["start_at"] == "2026-12-01T19:00:00+08:00"


def test_confirm_rolls_back_on_failure(conn, monkeypatch):
    """写到一半失败：旧计划仍是 confirmed，预览仍是 pending。"""

    def boom(*args, **kwargs):
        raise RuntimeError("模拟写时间块失败")

    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    monkeypatch.setattr(preview, "_insert_plan_items", boom)

    with pytest.raises(RuntimeError):
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
        )

    plan = _confirmed_plan(conn, seed_module.DEMO_USER_ID)
    assert plan["id"] == seed_module.PLAN_ID
    assert plan["version"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM plans").fetchone()["n"] == 1
    row = conn.execute(
        "SELECT status FROM previews WHERE id = ?", (p["preview_id"],)
    ).fetchone()
    assert row["status"] == "pending"


def test_confirm_cancels_related_reminders_only(conn):
    """只失效被替换计划里的提醒，别的提醒不动。"""
    with conn:
        conn.execute(
            "INSERT INTO reminders (id, user_id, plan_item_id, remind_at, status) "
            "VALUES ('rm_old', ?, 'pi_demo_01', '2026-11-30T18:50:00+08:00', 'queued')",
            (seed_module.DEMO_USER_ID,),
        )
        conn.execute(
            "INSERT INTO reminders (id, user_id, plan_item_id, remind_at, status) "
            "VALUES ('rm_other', ?, NULL, '2026-12-05T08:00:00+08:00', 'queued')",
            (seed_module.DEMO_USER_ID,),
        )

    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    result = preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )

    assert result["reminders"]["cancelled"] == 1
    rows = {row["id"]: row["status"] for row in conn.execute("SELECT id, status FROM reminders")}
    assert rows["rm_old"] == "cancelled"
    assert rows["rm_other"] == "queued"


# ---------------------------------------------------------------- 改期预览与确认
def test_exam_change_preview_does_not_write_exam(conn):
    detail = preview.create_exam_change_preview(
        seed_module.DEMO_USER_ID,
        seed_module.EXAM_ID,
        "2026-12-02T14:00:00+08:00",
        conn=conn,
    )

    assert detail["proposed_exam"]["new_start_at"] == "2026-12-02T14:00:00+08:00"
    assert detail["proposed_exam"]["old_start_at"] == "2026-12-04T14:00:00+08:00"
    # 只生成草稿：考试和计划都没变
    exam = conn.execute(
        "SELECT exam_date, version FROM exams WHERE id = ?", (seed_module.EXAM_ID,)
    ).fetchone()
    assert exam["exam_date"] == "2026-12-04"
    assert exam["version"] == 1
    assert _confirmed_plan(conn, seed_module.DEMO_USER_ID)["version"] == 1


def test_exam_change_confirm_applies_exam_and_plan(conn):
    detail = preview.create_exam_change_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, "2026-12-02T14:00:00+08:00",
        conn=conn,
    )
    result = preview.confirm_preview(
        seed_module.DEMO_USER_ID, detail["preview_id"], detail["confirm_code"], conn=conn
    )

    assert result["applied"]["exam"]["new_start_at"] == "2026-12-02T14:00:00+08:00"
    exam = conn.execute(
        "SELECT exam_date, start_time, version, status FROM exams WHERE id = ?",
        (seed_module.EXAM_ID,),
    ).fetchone()
    assert (exam["exam_date"], exam["start_time"]) == ("2026-12-02", "14:00")
    assert exam["version"] == 2
    assert exam["status"] == "moved"

    plan = _confirmed_plan(conn, seed_module.DEMO_USER_ID)
    assert plan["version"] == 2
    assert len(_plan_items(conn, plan["id"])) == 2


def test_exam_change_confirm_is_idempotent(conn):
    detail = preview.create_exam_change_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, "2026-12-02T14:00:00+08:00",
        conn=conn,
    )
    preview.confirm_preview(
        seed_module.DEMO_USER_ID, detail["preview_id"], detail["confirm_code"], conn=conn
    )
    again = preview.confirm_preview(
        seed_module.DEMO_USER_ID, detail["preview_id"], detail["confirm_code"], conn=conn
    )

    assert again["duplicate"] is True
    exam = conn.execute(
        "SELECT version FROM exams WHERE id = ?", (seed_module.EXAM_ID,)
    ).fetchone()
    assert exam["version"] == 2          # 没有重复 +1
    assert len(conn.execute("SELECT id FROM plans").fetchall()) == 2


def test_exam_change_conflicts(conn):
    detail = preview.create_exam_change_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, "2026-12-02T14:00:00+08:00",
        conn=conn,
    )
    # 预览生成后考试被别人改了
    with conn:
        conn.execute(
            "UPDATE exams SET version = version + 1 WHERE id = ?", (seed_module.EXAM_ID,)
        )

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, detail["preview_id"], detail["confirm_code"],
            conn=conn,
        )
    assert excinfo.value.code == "EXAM_VERSION_CONFLICT"


def test_exam_change_rejects_same_or_past_time(conn):
    with pytest.raises(PreviewError) as excinfo:
        preview.create_exam_change_preview(
            seed_module.DEMO_USER_ID, seed_module.EXAM_ID, "2026-12-04T14:00:00+08:00",
            conn=conn,
        )
    assert excinfo.value.code == "EXAM_TIME_UNCHANGED"

    with pytest.raises(PreviewError) as excinfo:
        preview.create_exam_change_preview(
            seed_module.DEMO_USER_ID, seed_module.EXAM_ID, "2026-11-01T14:00:00+08:00",
            conn=conn,
        )
    assert excinfo.value.code == "INVALID_EXAM_TIME"


# ---------------------------------------------------------------- 目标版本一致性（B8）
def _goal_payload(conn):
    exam = conn.execute(
        "SELECT id FROM exams WHERE user_id = ? LIMIT 1",
        (seed_module.DEMO_USER_ID,),
    ).fetchone()
    return {
        "title": "准备机器学习期末考试",
        "scope": "第三章、第四章",
        "event_id": exam["id"],
        "daily_max_minutes": 60,
        "availability": "每天 19:00 后",
    }


def _capacity(conn, preview_id):
    row = conn.execute(
        "SELECT capacity_json FROM previews WHERE id = ?", (preview_id,)
    ).fetchone()
    return json.loads(row["capacity_json"])


def test_preview_stores_goal_version(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    capacity = _capacity(conn, p["preview_id"])

    goal = conn.execute(
        "SELECT id, version FROM goals WHERE id = ?", (seed_module.GOAL_ID,)
    ).fetchone()
    assert capacity["goal_id"] == goal["id"]
    assert capacity["goal_version"] == goal["version"]
    assert p["goal_version"] == goal["version"]


def test_exam_change_preview_stores_goal_version(conn):
    detail = preview.create_exam_change_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, "2026-12-02T14:00:00+08:00",
        conn=conn,
    )
    capacity = _capacity(conn, detail["preview_id"])
    assert capacity["goal_id"] == seed_module.GOAL_ID
    assert capacity["goal_version"] == 1


def test_confirm_ok_when_goal_unchanged(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    result = preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )
    assert result["ok"] is True


def test_goal_change_blocks_old_preview(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    goals_service.save_goal(
        seed_module.DEMO_USER_ID, _goal_payload(conn), conn=conn
    )

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
        )
    assert excinfo.value.code == "GOAL_VERSION_CONFLICT"

    # 预览仍是 pending，正式数据没有任何变化
    row = conn.execute(
        "SELECT status FROM previews WHERE id = ?", (p["preview_id"],)
    ).fetchone()
    assert row["status"] == "pending"
    plan = _confirmed_plan(conn, seed_module.DEMO_USER_ID)
    assert plan["version"] == 1


def test_goal_change_blocks_without_plan_version_change(conn):
    """计划版本没变，也不能绕过目标版本校验。"""
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    goals_service.save_goal(seed_module.DEMO_USER_ID, _goal_payload(conn), conn=conn)

    # 计划版本仍是 1
    assert _confirmed_plan(conn, seed_module.DEMO_USER_ID)["version"] == 1

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
        )
    assert excinfo.value.code == "GOAL_VERSION_CONFLICT"


def test_goal_no_longer_active_blocks_preview(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    with conn:
        conn.execute(
            "UPDATE goals SET status = 'paused' WHERE id = ?", (seed_module.GOAL_ID,)
        )

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
        )
    assert excinfo.value.code == "GOAL_VERSION_CONFLICT"


def test_legacy_preview_without_goal_version_rejected(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    capacity = _capacity(conn, p["preview_id"])
    capacity.pop("goal_version", None)
    with conn:
        conn.execute(
            "UPDATE previews SET capacity_json = ? WHERE id = ?",
            (json.dumps(capacity), p["preview_id"]),
        )

    with pytest.raises(PreviewError) as excinfo:
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
        )
    assert excinfo.value.code == "GOAL_VERSION_CONFLICT"


def test_new_preview_uses_latest_goal(conn):
    goals_service.save_goal(seed_module.DEMO_USER_ID, _goal_payload(conn), conn=conn)
    goal = conn.execute(
        "SELECT version FROM goals WHERE id = ?", (seed_module.GOAL_ID,)
    ).fetchone()

    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    assert p["goal_version"] == goal["version"] == 2

    result = preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )
    assert result["ok"] is True


def test_goal_change_prevents_reuse(conn):
    first = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )
    goals_service.save_goal(seed_module.DEMO_USER_ID, _goal_payload(conn), conn=conn)

    second = preview.create_preview(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    assert second["preview_id"] != first["preview_id"]
    assert second["reused"] is False
    assert second["goal_version"] == 2


def test_two_exams_goal_versions_do_not_mix(conn):
    with conn:
        conn.execute(
            "INSERT INTO exams (id, user_id, name, exam_date, start_time) "
            "VALUES ('ex_two', ?, '另一场考试', '2026-12-09', '09:00')",
            (seed_module.DEMO_USER_ID,),
        )
        conn.execute(
            "INSERT INTO goals (id, user_id, exam_id, title, daily_limit_minutes, "
            "status, version) VALUES ('gl_two', ?, 'ex_two', '另一目标', 60, 'active', 1)",
            (seed_module.DEMO_USER_ID,),
        )

    p1 = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)

    # 改另一场考试的目标，不应影响第一场考试的预览
    goals_service.save_goal(
        seed_module.DEMO_USER_ID,
        {"title": "另一目标", "scope": "", "event_id": "ex_two",
         "daily_max_minutes": 30, "availability": ""},
        conn=conn,
    )

    result = preview.confirm_preview(
        seed_module.DEMO_USER_ID, p1["preview_id"], p1["confirm_code"], conn=conn
    )
    assert result["ok"] is True


def test_goal_conflict_leaves_all_tables_untouched(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    goals_service.save_goal(seed_module.DEMO_USER_ID, _goal_payload(conn), conn=conn)

    tables = ["plans", "plan_items", "exams", "reminders", "previews"]
    before = {
        table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")]
        for table in tables
    }

    with pytest.raises(PreviewError):
        preview.confirm_preview(
            seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
        )

    after = {
        table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")]
        for table in tables
    }
    assert before == after


def test_repeat_confirm_after_goal_change_is_still_idempotent(conn):
    p = preview.create_preview(seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn)
    first = preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )
    goals_service.save_goal(seed_module.DEMO_USER_ID, _goal_payload(conn), conn=conn)

    again = preview.confirm_preview(
        seed_module.DEMO_USER_ID, p["preview_id"], p["confirm_code"], conn=conn
    )

    assert again["duplicate"] is True
    assert again["applied"]["plan_version"] == first["applied"]["plan_version"]
