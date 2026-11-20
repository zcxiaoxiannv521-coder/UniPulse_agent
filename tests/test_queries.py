"""services/queries.py 的测试。

覆盖：演示用户能查到、跨用户隔离、用户不存在、空数据、排序、
只读（不改变任何记录）、JSON 可序列化、预览不算正式计划。
"""

from __future__ import annotations

import json

import pytest

from database.connection import get_connection, init_db
from demo import seed as seed_module
from services import clock, queries


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    """演示时间固定为 2026-11-30 09:00，数据库里写入演示数据。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T09:00:00+08:00")

    db_file = tmp_path / "queries.db"
    init_db(db_file)
    connection = get_connection(db_file)
    seed_module.seed(connection)
    yield connection
    connection.close()


def _add_user(conn, user_id: str, name: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO users (id, name, timezone) VALUES (?, ?, 'Asia/Shanghai')",
            (user_id, name),
        )


def test_demo_user_context(conn):
    """演示用户能查到自己完整的上下文。"""
    ctx = queries.get_context(seed_module.DEMO_USER_ID, conn=conn)

    assert ctx["user"]["id"] == seed_module.DEMO_USER_ID
    assert ctx["user"]["name"] == seed_module.DEMO_USER_NAME
    assert ctx["time_mode"] == "demo"
    assert ctx["as_of"] == "2026-11-30T09:00:00+08:00"
    assert ctx["range"] == {"from": "2026-11-30", "to": "2026-12-06"}

    assert len(ctx["sessions"]) == 3
    assert len(ctx["exams"]) == 1
    assert ctx["exams"][0]["exam_date"] == "2026-12-04"
    assert len(ctx["tasks"]) == 3
    assert len(ctx["goals"]) == 1
    assert ctx["goals"][0]["daily_limit_minutes"] == 90

    assert ctx["plan"]["id"] == seed_module.PLAN_ID
    assert ctx["plan"]["version"] == 1
    assert ctx["plan"]["status"] == "confirmed"
    assert len(ctx["plan_items"]) == 3
    assert ctx["data_version"] == {
        "plan_id": seed_module.PLAN_ID,
        "plan_version": 1,
    }

    assert ctx["preferences"][0]["key"] == "daily_limit_minutes"
    assert ctx["preferences"][0]["value"] == 90
    assert ctx["preferences"][0]["scope"] == f"goal:{seed_module.GOAL_ID}"

    # 时间统一为 ISO 8601 带时区，不是 SQLite 的 "YYYY-MM-DD HH:MM:SS"
    assert "T" in ctx["plan"]["updated_at"]
    assert ctx["plan"]["updated_at"].endswith("+08:00")
    assert ctx["preferences"][0]["confirmed_at"].endswith("+08:00")


def test_demo_user_dashboard(conn):
    """看板：今天、7 天时间轴、下一节课、计划版本都对。"""
    board = queries.get_dashboard(seed_module.DEMO_USER_ID, conn=conn)

    assert board["today"] == "2026-11-30"
    assert board["time_mode"] == "demo"
    assert len(board["days"]) == 7
    assert board["days"][0]["date"] == "2026-11-30"
    assert board["days"][0]["weekday"] == "周一"

    assert board["plan"]["version"] == 1
    assert board["plan"]["status"] == "confirmed"

    # 11-30 09:00 之后最近的一节课是当天 10:00
    assert board["next_class"]["title"] == "机器学习"
    assert board["next_class"]["start_at"] == "2026-11-30T10:00:00+08:00"

    # 演示任务没有截止时间，所以最近截止为空（不是编造一个）
    assert board["next_deadline"] is None
    assert board["pending_changes"] == []
    assert board["notices"] == []

    # 时间块分别落在三天里
    by_date = {day["date"]: day["items"] for day in board["days"]}
    assert [i["title"] for i in by_date["2026-11-30"]] == ["机器学习", "复习第三章"]
    assert [i["title"] for i in by_date["2026-12-01"]] == ["机器学习", "复习第四章"]
    assert [i["title"] for i in by_date["2026-12-02"]] == ["模拟练习"]


def test_dashboard_json_serializable(conn):
    """返回值必须能直接 json.dumps（C 的接口要用）。"""
    board = queries.get_dashboard(seed_module.DEMO_USER_ID, conn=conn)
    ctx = queries.get_context(seed_module.DEMO_USER_ID, conn=conn)

    dumped_board = json.dumps(board, ensure_ascii=False)
    dumped_ctx = json.dumps(ctx, ensure_ascii=False)

    assert "林小满" in dumped_board
    assert "林小满" in dumped_ctx


def test_user_isolation(conn):
    """B 用户看不到 A 的任何数据，A 也看不到 B 的。"""
    _add_user(conn, "us_demo_b", "周小舟")
    with conn:
        conn.execute(
            "INSERT INTO courses (id, user_id, name) VALUES ('cr_b', 'us_demo_b', '编译原理')"
        )
        conn.execute(
            "INSERT INTO class_sessions (id, user_id, course_id, session_date, start_time, end_time) "
            "VALUES ('cs_b', 'us_demo_b', 'cr_b', '2026-11-30', '08:00', '09:00')"
        )
        conn.execute(
            "INSERT INTO exams (id, user_id, name, exam_date) "
            "VALUES ('ex_b', 'us_demo_b', '编译原理期末考试', '2026-12-03')"
        )
        conn.execute(
            "INSERT INTO tasks (id, user_id, title) VALUES ('tk_b', 'us_demo_b', '写编译器')"
        )
        conn.execute(
            "INSERT INTO plans (id, user_id, status, version) "
            "VALUES ('pl_b', 'us_demo_b', 'confirmed', 7)"
        )
        conn.execute(
            "INSERT INTO plan_items (id, plan_id, start_at, end_at, title, item_type) "
            "VALUES ('pi_b', 'pl_b', '2026-11-30T21:00:00+08:00', '2026-11-30T22:00:00+08:00', "
            "'写编译器', 'task')"
        )

    ctx_b = queries.get_context("us_demo_b", conn=conn)
    assert [s["id"] for s in ctx_b["sessions"]] == ["cs_b"]
    assert [e["id"] for e in ctx_b["exams"]] == ["ex_b"]
    assert [t["id"] for t in ctx_b["tasks"]] == ["tk_b"]
    assert [i["id"] for i in ctx_b["plan_items"]] == ["pi_b"]
    assert ctx_b["plan"]["version"] == 7

    # 反向：A 的数据里不能出现任何 us_demo_b 的东西
    ctx_a = queries.get_context(seed_module.DEMO_USER_ID, conn=conn)
    dumped = json.dumps(ctx_a, ensure_ascii=False)
    for leaked in ("cr_b", "cs_b", "ex_b", "tk_b", "pl_b", "pi_b", "编译原理"):
        assert leaked not in dumped

    # 看板同样隔离
    board_a = queries.get_dashboard(seed_module.DEMO_USER_ID, conn=conn)
    dumped_board = json.dumps(board_a, ensure_ascii=False)
    assert "编译原理" not in dumped_board
    assert board_a["plan"]["id"] == seed_module.PLAN_ID


def test_unknown_user_raises(conn):
    """用户不存在要明确报错，不能返回空壳冒充“这个人没数据”。"""
    with pytest.raises(queries.UserNotFoundError) as excinfo:
        queries.get_context("us_not_exist", conn=conn)
    assert excinfo.value.code == "USER_NOT_FOUND"

    with pytest.raises(queries.UserNotFoundError):
        queries.get_dashboard("us_not_exist", conn=conn)


def test_user_without_data_returns_complete_structure(conn):
    """新用户什么都没有：结构要完整，数组为空，missing 说明原因。"""
    _add_user(conn, "us_empty", "空同学")

    ctx = queries.get_context("us_empty", conn=conn)

    assert ctx["sessions"] == []
    assert ctx["exams"] == []
    assert ctx["tasks"] == []
    assert ctx["goals"] == []
    assert ctx["plan"] is None
    assert ctx["plan_items"] == []
    assert ctx["data_version"] == {"plan_id": None, "plan_version": None}

    reasons = {item["reason"] for item in ctx["missing"]}
    assert {"NO_CLASS_DATA_IN_RANGE", "NO_UPCOMING_EXAM", "NO_OPEN_TASK",
            "NO_CONFIRMED_PLAN", "NO_ACTIVE_GOAL"} <= reasons

    board = queries.get_dashboard("us_empty", conn=conn)
    assert board["plan"] is None
    assert board["next_class"] is None
    assert board["next_deadline"] is None
    assert board["tasks"] == []
    assert len(board["days"]) == 7
    assert all(day["items"] == [] for day in board["days"])


def test_sorting(conn):
    """课表按日期、任务按截止、考试按日期排序。"""
    with conn:
        conn.execute(
            "INSERT INTO tasks (id, user_id, title, due_at, priority) "
            "VALUES ('tk_late', ?, '晚截止', '2026-12-05T23:59:00+08:00', 1)",
            (seed_module.DEMO_USER_ID,),
        )
        conn.execute(
            "INSERT INTO tasks (id, user_id, title, due_at, priority) "
            "VALUES ('tk_early', ?, '早截止', '2026-11-29T23:59:00+08:00', 3)",
            (seed_module.DEMO_USER_ID,),
        )

    ctx = queries.get_context(seed_module.DEMO_USER_ID, conn=conn)

    session_dates = [(s["session_date"], s["start_time"]) for s in ctx["sessions"]]
    assert session_dates == sorted(session_dates)

    due_dates = [t["due_at"] for t in ctx["tasks"] if t["due_at"]]
    assert due_dates == sorted(due_dates)

    # 没有 due_at 的任务排在最后
    assert ctx["tasks"][-1]["due_at"] is None


def test_queries_do_not_modify_database(conn):
    """只读：调用前后所有表的行内容完全一致。"""
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
    queries.get_context(seed_module.DEMO_USER_ID, conn=conn)
    queries.get_dashboard(seed_module.DEMO_USER_ID, conn=conn)
    after = snapshot()

    assert before == after


def test_pending_preview_is_not_treated_as_plan(conn):
    """待确认草稿只出现在 pending_changes，不能影响正式计划。"""
    with conn:
        conn.execute(
            """
            INSERT INTO previews (id, user_id, goal_id, kind, confirm_code, status,
                                  base_plan_id, base_version, summary, diff_json, expires_at)
            VALUES ('pv_demo', ?, ?, 'reschedule', 'T03', 'pending', ?,
                    1, '考试提前到 12-02，缺口 80 分钟', '{}', '2026-11-30T10:00:00+08:00')
            """,
            (seed_module.DEMO_USER_ID, seed_module.GOAL_ID, seed_module.PLAN_ID),
        )

    ctx = queries.get_context(seed_module.DEMO_USER_ID, conn=conn)
    assert ctx["plan"]["id"] == seed_module.PLAN_ID
    assert ctx["plan"]["version"] == 1          # 正式计划没被草稿顶替
    assert len(ctx["plan_items"]) == 3

    board = queries.get_dashboard(seed_module.DEMO_USER_ID, conn=conn)
    assert [p["confirm_code"] for p in board["pending_changes"]] == ["T03"]
    assert board["plan"]["version"] == 1


def test_expired_preview_hidden(conn):
    """过期草稿不出现在看板的待确认里。"""
    with conn:
        conn.execute(
            """
            INSERT INTO previews (id, user_id, goal_id, kind, confirm_code, status,
                                  base_version, summary, diff_json, expires_at)
            VALUES ('pv_old', ?, ?, 'reschedule', 'T01', 'pending',
                    1, '已过期', '{}', '2026-11-30T08:00:00+08:00')
            """,
            (seed_module.DEMO_USER_ID, seed_module.GOAL_ID),
        )

    board = queries.get_dashboard(seed_module.DEMO_USER_ID, conn=conn)
    assert board["pending_changes"] == []


def test_cancelled_session_not_on_dashboard(conn):
    """已停课的记录不在看板时间轴上显示。"""
    with conn:
        conn.execute(
            "UPDATE class_sessions SET status = 'cancelled' "
            "WHERE id = 'cs_demo_01' AND user_id = ?",
            (seed_module.DEMO_USER_ID,),
        )

    board = queries.get_dashboard(seed_module.DEMO_USER_ID, conn=conn)
    titles = [item["title"] for day in board["days"] for item in day["items"]]
    assert titles.count("机器学习") == 2        # 原本 3 次，取消 1 次


def test_bad_date_format_raises(conn):
    """日期格式错误要有明确提示。"""
    with pytest.raises(queries.QueryError, match="不是合法日期"):
        queries.get_context(seed_module.DEMO_USER_ID, "2026/11/30", conn=conn)


def test_date_range_filters_sessions(conn):
    """指定范围时，范围外的课表不返回。"""
    ctx = queries.get_context(
        seed_module.DEMO_USER_ID, "2026-12-10", "2026-12-12", conn=conn
    )
    assert ctx["sessions"] == []
    assert any(m["field"] == "sessions" for m in ctx["missing"])
