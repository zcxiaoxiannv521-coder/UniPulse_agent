"""services/scheduler.py 的测试。

覆盖：V4 固定案例、必做优先、考试提前、每日上限、课程冲突、
固定块保护、缺口、空数据、隔离、时间边界、确定性、只读。
"""

from __future__ import annotations

import pytest

from database.connection import get_connection, init_db
from demo import seed as seed_module
from services import clock, scheduler
from services.queries import UserNotFoundError


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    """演示时间固定为 2026-11-30 09:00，数据库写入演示数据。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T09:00:00+08:00")

    path = tmp_path / "sched.db"
    init_db(path)
    connection = get_connection(path)
    seed_module.seed(connection)
    yield connection
    connection.close()


def _scenario(
    conn,
    *,
    user="us_t",
    exam_date="2026-12-04",
    exam_start="14:00",
    daily_limit=90,
    tasks=(),
    items=(),
    sessions=(),
    with_plan=True,
):
    """快速造一个独立用户 + 考试 + 目标 + 计划。"""
    goal_id = f"gl_{user}"
    plan_id = f"pl_{user}"
    with conn:
        conn.execute(
            "INSERT INTO users (id, name) VALUES (?, ?)", (user, user)
        )
        conn.execute(
            "INSERT INTO exams (id, user_id, name, exam_date, start_time, end_time) "
            "VALUES (?, ?, '测试考试', ?, ?, '15:30')",
            (f"ex_{user}", user, exam_date, exam_start),
        )
        conn.execute(
            "INSERT INTO goals (id, user_id, exam_id, title, daily_limit_minutes) "
            "VALUES (?, ?, ?, '测试目标', ?)",
            (goal_id, user, f"ex_{user}", daily_limit),
        )
        conn.execute(
            "INSERT INTO courses (id, user_id, name) VALUES (?, ?, '测试课')",
            (f"cr_{user}", user),
        )
        if with_plan:
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
                                   splittable, status, due_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'todo', ?)
                """,
                (
                    task["id"], user, goal_id, task["title"],
                    task["minutes"], task["minutes"],
                    task.get("priority", 2), 1 if task.get("optional") else 0,
                    1 if task.get("splittable", 0) else 0,
                    task.get("due_at"),
                ),
            )
        for item in items:
            conn.execute(
                """
                INSERT INTO plan_items (id, plan_id, start_at, end_at, title,
                                        item_type, task_id, is_locked)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"], plan_id, item["start"], item["end"],
                    item.get("title", "学习"), item.get("type", "task"),
                    item.get("task_id"), 1 if item.get("locked") else 0,
                ),
            )
        for session in sessions:
            conn.execute(
                """
                INSERT INTO class_sessions (id, user_id, course_id, session_date,
                                            start_time, end_time)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session["id"], user, f"cr_{user}", session["date"],
                 session["start"], session["end"]),
            )
    return user, f"ex_{user}"


def _iso(day: str, hm: str) -> str:
    return f"{day}T{hm}:00+08:00"


# ---------------------------------------------------------------- 固定案例
def test_demo_case_initial(conn):
    """考试 12-04：三段窗口共 230 分钟，全部排下。"""
    result = scheduler.generate_schedule(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    assert result["required_minutes"] == 150
    assert result["scheduled_required_minutes"] == 150
    assert result["required_gap_minutes"] == 0
    assert result["optional_minutes"] == 80
    assert result["scheduled_optional_minutes"] == 80
    assert result["unscheduled_optional_minutes"] == 0
    assert result["total_requested_minutes"] == 230

    assert len(result["schedule_items"]) == 3
    assert [item["task_id"] for item in result["schedule_items"]] == list(
        seed_module.TASK_IDS
    )
    assert result["schedule_items"][2]["duration_minutes"] == 80


def test_demo_case_exam_moved_earlier(conn):
    """考试提前到 12-02 14:00：只剩 150 分钟，选做排不进。"""
    with conn:
        conn.execute(
            "UPDATE exams SET exam_date = '2026-12-02', start_time = '14:00' "
            "WHERE id = ?",
            (seed_module.EXAM_ID,),
        )

    result = scheduler.generate_schedule(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    assert result["required_minutes"] == 150
    assert result["scheduled_required_minutes"] == 150
    assert result["required_gap_minutes"] == 0
    assert result["optional_minutes"] == 80
    assert result["scheduled_optional_minutes"] == 0
    assert result["unscheduled_optional_minutes"] == 80
    assert result["total_requested_minutes"] == 230

    assert len(result["windows"]) == 2
    assert all(item["start_at"] < "2026-12-02T14:00:00+08:00"
               for item in result["schedule_items"])
    assert any(w["code"] == "WINDOWS_AFTER_EXAM" for w in result["warnings"])


def test_numbers_are_not_hardcoded(conn):
    """改任务时长，结果必须跟着变。"""
    with conn:
        conn.execute("UPDATE tasks SET remaining_minutes = 30 WHERE id = 'tk_demo_ch4'")

    result = scheduler.generate_schedule(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    assert result["required_minutes"] == 90
    assert result["scheduled_required_minutes"] == 90
    required_items = [i for i in result["schedule_items"] if not i["is_optional"]]
    assert [i["duration_minutes"] for i in required_items] == [60, 30]


# ---------------------------------------------------------------- 优先级
def test_required_before_optional(conn):
    """只有 60 分钟容量时，必做优先，选做排不进。"""
    user, exam = _scenario(
        conn,
        tasks=[
            {"id": "tk_req", "title": "必做", "minutes": 60},
            {"id": "tk_opt", "title": "选做", "minutes": 60, "optional": True},
        ],
        items=[{"id": "pi_1", "start": _iso("2026-12-01", "19:00"),
                "end": _iso("2026-12-01", "20:00"), "task_id": "tk_req"}],
    )

    result = scheduler.generate_schedule(user, exam, conn=conn)

    assert result["scheduled_required_minutes"] == 60
    assert result["scheduled_optional_minutes"] == 0
    assert result["unscheduled_optional_minutes"] == 60


def test_required_task_ordering_by_priority(conn):
    """两个必做任务抢一个窗口时，优先级高的先排。"""
    user, exam = _scenario(
        conn,
        tasks=[
            {"id": "tk_low", "title": "低", "minutes": 60, "priority": 3},
            {"id": "tk_high", "title": "高", "minutes": 60, "priority": 1},
        ],
        items=[{"id": "pi_1", "start": _iso("2026-12-01", "19:00"),
                "end": _iso("2026-12-01", "20:00"), "task_id": "tk_low"}],
    )

    result = scheduler.generate_schedule(user, exam, conn=conn)

    assert [item["task_id"] for item in result["schedule_items"]] == ["tk_high"]
    assert result["required_gap_minutes"] == 60


# ---------------------------------------------------------------- 每日上限
def test_daily_limit(conn):
    """同一天最多排 90 分钟。"""
    user, exam = _scenario(
        conn,
        tasks=[{"id": "tk_big", "title": "大任务", "minutes": 120, "splittable": True}],
        items=[{"id": "pi_1", "start": _iso("2026-12-01", "18:00"),
                "end": _iso("2026-12-01", "22:00"), "task_id": "tk_big"}],
    )

    result = scheduler.generate_schedule(user, exam, conn=conn)

    assert result["scheduled_required_minutes"] == 90
    assert result["required_gap_minutes"] == 30
    assert any(w["code"] == "DAILY_LIMIT_REACHED" for w in result["warnings"])


# ---------------------------------------------------------------- 冲突与保护
def test_course_blocks_window(conn):
    """课程占掉时间块中间一段，窗口被切成两段。"""
    user, exam = _scenario(
        conn,
        tasks=[{"id": "tk_a", "title": "A", "minutes": 60}],
        items=[{"id": "pi_1", "start": _iso("2026-12-01", "19:00"),
                "end": _iso("2026-12-01", "21:00"), "task_id": "tk_a"}],
        sessions=[{"id": "cs_1", "date": "2026-12-01", "start": "19:30", "end": "20:30"}],
    )

    result = scheduler.generate_schedule(user, exam, conn=conn)

    assert [w["minutes"] for w in result["windows"]] == [30, 30]
    # 60 分钟任务不能拆，两个 30 分钟窗口都放不下
    assert result["required_gap_minutes"] == 60


def test_locked_block_protected(conn):
    """锁定的时间块不当作可用时间。"""
    user, exam = _scenario(
        conn,
        tasks=[{"id": "tk_a", "title": "A", "minutes": 60}],
        items=[
            {"id": "pi_locked", "start": _iso("2026-12-01", "19:00"),
             "end": _iso("2026-12-01", "20:00"), "locked": True},
        ],
    )

    result = scheduler.generate_schedule(user, exam, conn=conn)

    assert result["windows"] == []
    assert result["required_gap_minutes"] == 60


def test_completed_task_protected(conn):
    """已完成任务不重排，它占的时间也不复用。"""
    with conn:
        conn.execute(
            "UPDATE tasks SET status = 'done' WHERE id = 'tk_demo_ch3'"
        )

    result = scheduler.generate_schedule(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    task_ids = [item["task_id"] for item in result["schedule_items"]]
    assert "tk_demo_ch3" not in task_ids
    assert result["required_minutes"] == 90          # 只剩第四章
    assert result["scheduled_required_minutes"] == 90


def test_started_block_protected(conn):
    """已经开始的时间块不移动、不复用；对应任务仍然算缺口。"""
    with conn:
        conn.execute(
            "UPDATE plan_items SET start_at = ?, end_at = ? WHERE id = 'pi_demo_01'",
            ("2026-11-30T08:00:00+08:00", "2026-11-30T09:00:00+08:00"),
        )

    result = scheduler.generate_schedule(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    # 08:00-09:00 这段不进入候选窗口
    assert all("T08:00" not in window["start_at"] for window in result["windows"])
    # 但任务本身没完成，可以被重新安排到别的时间
    assert any(item["task_id"] == "tk_demo_ch3" for item in result["schedule_items"])
    assert all(item["start_at"] != "2026-11-30T08:00:00+08:00"
               for item in result["schedule_items"])
    assert result["required_gap_minutes"] == 0


# ---------------------------------------------------------------- 缺口
def test_required_gap(conn):
    """可用 90 分钟、必做 150 分钟：先排 90，还剩 60 排不下。"""
    user, exam = _scenario(
        conn,
        tasks=[
            {"id": "tk_1", "title": "1", "minutes": 60},
            {"id": "tk_2", "title": "2", "minutes": 90},
        ],
        items=[{"id": "pi_1", "start": _iso("2026-12-01", "19:00"),
                "end": _iso("2026-12-01", "20:30"), "task_id": "tk_1"}],
    )

    result = scheduler.generate_schedule(user, exam, conn=conn)

    assert result["required_minutes"] == 150
    assert result["scheduled_required_minutes"] == 90
    assert result["required_gap_minutes"] == 60
    assert result["unscheduled"][0]["reason"] == "NO_FREE_SLOT"


def test_optional_gap_reported_separately(conn):
    """选做缺口单独统计，不混进必做缺口。"""
    user, exam = _scenario(
        conn,
        tasks=[
            {"id": "tk_req", "title": "必做", "minutes": 60},
            {"id": "tk_opt", "title": "选做", "minutes": 40, "optional": True},
        ],
        items=[{"id": "pi_1", "start": _iso("2026-12-01", "19:00"),
                "end": _iso("2026-12-01", "20:00"), "task_id": "tk_req"}],
    )

    result = scheduler.generate_schedule(user, exam, conn=conn)

    assert result["required_gap_minutes"] == 0
    assert result["unscheduled_optional_minutes"] == 40


# ---------------------------------------------------------------- 空数据
def test_no_tasks(conn):
    with conn:
        conn.execute("DELETE FROM plan_items")
        conn.execute("DELETE FROM tasks")

    result = scheduler.generate_schedule(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    assert result["required_minutes"] == 0
    assert result["optional_minutes"] == 0
    assert result["schedule_items"] == []
    assert any(w["code"] == "NO_OPEN_TASK" for w in result["warnings"])


def test_no_windows(conn):
    """没有正式计划 = 没有可移动时间块。"""
    with conn:
        conn.execute("DELETE FROM plan_items")

    result = scheduler.generate_schedule(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )

    assert result["windows"] == []
    assert result["required_gap_minutes"] == 150
    assert any(w["code"] == "NO_FREE_WINDOW" for w in result["warnings"])


# ---------------------------------------------------------------- 错误与隔离
def test_exam_in_past(conn):
    with conn:
        conn.execute(
            "UPDATE exams SET exam_date = '2026-11-01' WHERE id = ?",
            (seed_module.EXAM_ID,),
        )

    with pytest.raises(scheduler.SchedulerError) as excinfo:
        scheduler.generate_schedule(
            seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
        )
    assert excinfo.value.code == "EXAM_IN_PAST"


def test_exam_time_missing(conn):
    with conn:
        conn.execute(
            "UPDATE exams SET start_time = NULL WHERE id = ?",
            (seed_module.EXAM_ID,),
        )

    with pytest.raises(scheduler.SchedulerError) as excinfo:
        scheduler.generate_schedule(
            seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
        )
    assert excinfo.value.code == "EXAM_TIME_INVALID"


def test_unknown_user(conn):
    with pytest.raises(scheduler.SchedulerError) as excinfo:
        scheduler.generate_schedule("us_nobody", seed_module.EXAM_ID, conn=conn)
    assert excinfo.value.code == "USER_NOT_FOUND"


def test_user_isolation(conn):
    """拿别人的考试 ID 排不了程；也不会把别人的任务算进来。"""
    user_b, exam_b = _scenario(
        conn,
        user="us_b",
        tasks=[{"id": "tk_b", "title": "B 的任务", "minutes": 30}],
        items=[{"id": "pi_b", "start": _iso("2026-12-01", "21:00"),
                "end": _iso("2026-12-01", "22:00"), "task_id": "tk_b"}],
    )

    with pytest.raises(scheduler.SchedulerError) as excinfo:
        scheduler.generate_schedule(
            seed_module.DEMO_USER_ID, exam_b, conn=conn
        )
    assert excinfo.value.code == "EXAM_NOT_FOUND"

    result_b = scheduler.generate_schedule(user_b, exam_b, conn=conn)
    assert [item["task_id"] for item in result_b["schedule_items"]] == ["tk_b"]
    assert "tk_demo_ch3" not in [item["task_id"] for item in result_b["schedule_items"]]


# ---------------------------------------------------------------- 时间边界
def test_adjacent_windows_merged(conn):
    """首尾相接的两段能合起来放一个 60 分钟不拆分的任务。"""
    user, exam = _scenario(
        conn,
        tasks=[{"id": "tk_a", "title": "A", "minutes": 60}],
        items=[
            {"id": "pi_1", "start": _iso("2026-12-01", "19:00"),
             "end": _iso("2026-12-01", "19:30"), "task_id": "tk_a"},
            {"id": "pi_2", "start": _iso("2026-12-01", "19:30"),
             "end": _iso("2026-12-01", "20:00"), "task_id": "tk_a"},
        ],
    )

    result = scheduler.generate_schedule(user, exam, conn=conn)

    assert len(result["schedule_items"]) == 1
    assert result["schedule_items"][0]["duration_minutes"] == 60


def test_overlapping_windows_merged(conn):
    """部分重叠的两段不能重复计算容量。"""
    user, exam = _scenario(
        conn,
        tasks=[{"id": "tk_a", "title": "A", "minutes": 90}],
        items=[
            {"id": "pi_1", "start": _iso("2026-12-01", "19:00"),
             "end": _iso("2026-12-01", "20:00"), "task_id": "tk_a"},
            {"id": "pi_2", "start": _iso("2026-12-01", "19:30"),
             "end": _iso("2026-12-01", "20:30"), "task_id": "tk_a"},
        ],
    )

    result = scheduler.generate_schedule(user, exam, conn=conn)

    assert [w["minutes"] for w in result["windows"]] == [90]
    assert result["scheduled_required_minutes"] == 90


def test_cross_midnight_split(conn):
    """跨零点的窗口要切成两天，并遵守当天上限。"""
    user, exam = _scenario(
        conn,
        exam_date="2026-12-05",
        tasks=[{"id": "tk_a", "title": "A", "minutes": 30}],
        items=[{"id": "pi_1", "start": _iso("2026-12-01", "23:30"),
                "end": "2026-12-02T00:30:00+08:00", "task_id": "tk_a"}],
    )

    result = scheduler.generate_schedule(user, exam, conn=conn)

    assert [w["minutes"] for w in result["windows"]] == [30, 30]
    assert result["schedule_items"][0]["end_at"] == "2026-12-02T00:00:00+08:00"
    assert result["schedule_items"][0]["date"] == "2026-12-01"


# ---------------------------------------------------------------- 确定性与只读
def test_deterministic(conn):
    first = scheduler.generate_schedule(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )
    second = scheduler.generate_schedule(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )
    assert first == second


def test_read_only(conn):
    """排程前后所有表内容完全一致。"""
    tables = [
        "users", "user_preferences", "courses", "class_sessions", "exams",
        "goals", "tasks", "plans", "plan_items", "previews", "notifications", "reminders",
    ]

    def snapshot():
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in tables
        }

    before = snapshot()
    scheduler.generate_schedule(
        seed_module.DEMO_USER_ID, seed_module.EXAM_ID, conn=conn
    )
    after = snapshot()

    assert before == after
