"""单门考试的学习任务排程（只读，不写数据库）。

职责：
    给一个用户 + 一场考试，算出「考前还能在哪些时间段复习什么」，
    并如实报告排不下的分钟数。它只产生**候选安排**，不修改正式计划——
    真正写入要等后续的预览/确认流程。

一、时间窗口从哪来（关键设计，需要团队确认）
    V4 没有单独存"空闲时间表"，本项目也**不能改 schema**。因此这里采用：
    候选窗口 = 当前正式计划里"可移动的学习块"（task / rest / custom 类型，
               未锁定、未开始、关联任务未完成）。
    然后扣除：课程、考试、锁定/已完成/已开始的时间块，
    再截断到考试开始之前。

    为什么这样定：演示数据里 11-30 19:00、12-01 19:00、12-02 15:00 这三段
    正是计划 v1 的三个学习块，也就是学生"原本打算学习"的时间。重排就是把
    这三段按新约束重新分配，这样算出的 150 / 80 才和 V4 第 7 节对得上。

    如果以后要支持"自由找空档"（从早到晚自动找空），需要先决定一个
    "允许学习时段"的数据来源（偏好表或配置），那属于另一个迭代。

二、规则
    1. 考试开始之后的时间一律不用（哪怕还空着）
    2. 必做任务优先，选做任务只用剩余时间
    3. 每天安排的学习分钟数不超过 goals.daily_limit_minutes
    4. 已完成 / 已开始 / 锁定的时间块不动，也不当作可用时间
    5. 任务排不下就报缺口，不缩短必做任务的时长
    6. 同样输入必然得到同样输出（所有排序都有明确 key，不依赖字典顺序）

三、用法
    from services.scheduler import generate_schedule

    result = generate_schedule("us_demo_a", "ex_demo_ml_final")
    result["required_gap_minutes"]   # 必做还差多少分钟
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time as dt_time, timedelta
from typing import Iterator

from database.connection import get_connection
from services import clock

# 拆分任务时，单个片段的最小长度（分钟）。
# 小于这个数就不值得单独占一段，除非它正好能把任务排完。
MIN_BLOCK_MINUTES = 15

# 时间块类型：只有这三类算"可移动的学习时间"
_MOVABLE_ITEM_TYPES = ("task", "rest", "custom")
# 这两类是固定占用（真实课表和考试）
_FIXED_ITEM_TYPES = ("course", "exam")


class SchedulerError(Exception):
    """排程无法进行（考试不存在、时间非法、已过期等）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@contextmanager
def _connection(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if conn is not None:
        yield conn
        return
    own = get_connection()
    try:
        yield own
    finally:
        own.close()


# ---------------------------------------------------------------- 读取数据
def _load_user(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, name, timezone FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None:
        raise SchedulerError("USER_NOT_FOUND", f"用户 {user_id!r} 不存在")
    return row


def _load_exam(conn: sqlite3.Connection, user_id: str, exam_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT id, user_id, course_id, name, exam_date, start_time, end_time,
               location, status, version
        FROM exams
        WHERE id = ? AND user_id = ?
        """,
        (exam_id, user_id),
    ).fetchone()
    if row is None:
        raise SchedulerError(
            "EXAM_NOT_FOUND", f"用户 {user_id!r} 名下没有考试 {exam_id!r}"
        )
    return row


def _load_goal(conn: sqlite3.Connection, user_id: str, exam_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT id, exam_id, title, daily_limit_minutes, status, version
        FROM goals
        WHERE user_id = ? AND exam_id = ? AND status = 'active'
        ORDER BY version DESC, created_at DESC
        LIMIT 1
        """,
        (user_id, exam_id),
    ).fetchone()
    if row is None:
        raise SchedulerError(
            "GOAL_NOT_FOUND", f"考试 {exam_id!r} 还没有进行中的学习目标"
        )
    return row


def _load_tasks(conn: sqlite3.Connection, user_id: str, goal_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, title, due_at, estimated_minutes, remaining_minutes,
               priority, is_optional, splittable, status
        FROM tasks
        WHERE user_id = ? AND goal_id = ? AND status <> 'done'
        ORDER BY id
        """,
        (user_id, goal_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_plan(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, version, status, goal_id
        FROM plans
        WHERE user_id = ? AND status = 'confirmed'
        ORDER BY version DESC, updated_at DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()


def _load_plan_items(conn: sqlite3.Connection, user_id: str, plan_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT pi.id, pi.start_at, pi.end_at, pi.title, pi.item_type,
               pi.task_id, pi.is_locked, t.status AS task_status
        FROM plan_items pi
        JOIN plans p ON p.id = pi.plan_id AND p.user_id = ?
        LEFT JOIN tasks t ON t.id = pi.task_id AND t.user_id = p.user_id
        WHERE pi.plan_id = ?
        ORDER BY pi.start_at, pi.id
        """,
        (user_id, plan_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_busy_sessions(
    conn: sqlite3.Connection, user_id: str, until: date
) -> list[tuple[datetime, datetime]]:
    rows = conn.execute(
        """
        SELECT session_date, start_time, end_time
        FROM class_sessions
        WHERE user_id = ? AND status <> 'cancelled' AND session_date <= ?
        """,
        (user_id, until.isoformat()),
    ).fetchall()
    intervals = []
    for row in rows:
        start = _combine(row["session_date"], row["start_time"])
        end = _combine(row["session_date"], row["end_time"])
        if start and end and end > start:
            intervals.append((start, end))
    return intervals


def _load_busy_exams(
    conn: sqlite3.Connection, user_id: str, until: date
) -> list[tuple[datetime, datetime]]:
    rows = conn.execute(
        """
        SELECT exam_date, start_time, end_time
        FROM exams
        WHERE user_id = ? AND status <> 'cancelled' AND exam_date <= ?
        """,
        (user_id, until.isoformat()),
    ).fetchall()
    intervals = []
    for row in rows:
        start = _combine(row["exam_date"], row["start_time"])
        end = _combine(row["exam_date"], row["end_time"] or row["start_time"])
        if start and end:
            if end <= start:
                end = start + timedelta(minutes=1)
            intervals.append((start, end))
    return intervals


# ---------------------------------------------------------------- 时间工具
def _combine(day: str, clock_time: str | None) -> datetime | None:
    if not clock_time:
        return None
    try:
        return datetime.fromisoformat(f"{day}T{clock_time}").replace(
            tzinfo=clock.get_timezone()
        )
    except ValueError:
        return None


def _parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=clock.get_timezone())
    return parsed


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _minutes(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def _exam_start(exam: sqlite3.Row) -> datetime:
    start = _combine(exam["exam_date"], exam["start_time"])
    if start is None:
        raise SchedulerError(
            "EXAM_TIME_INVALID",
            f"考试 {exam['name']!r} 缺少可用的开始时间（exam_date={exam['exam_date']!r}, "
            f"start_time={exam['start_time']!r}），无法计算考前时间",
        )
    return start


def _exam_end_with_override(
    exam: sqlite3.Row, start: datetime, override: datetime | None
) -> datetime:
    """没有 override 时用库里的结束时间；有 override 时保持原考试时长。"""
    if override is None:
        end = _combine(exam["exam_date"], exam["end_time"])
        return end if end and end > start else start
    original_start = _combine(exam["exam_date"], exam["start_time"])
    original_end = _combine(exam["exam_date"], exam["end_time"])
    if original_start and original_end and original_end > original_start:
        return start + (original_end - original_start)
    return start


def _merge(
    intervals: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """合并重叠或首尾相接的区间。"""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda pair: (pair[0], pair[1]))
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:          # 重叠或正好相接（end == start）
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _subtract(
    windows: list[tuple[datetime, datetime]],
    busy: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """从 windows 里扣掉 busy。"""
    merged_busy = _merge(busy)
    result: list[tuple[datetime, datetime]] = []
    for win_start, win_end in windows:
        cursor = win_start
        for busy_start, busy_end in merged_busy:
            if busy_end <= cursor or busy_start >= win_end:
                continue
            if busy_start > cursor:
                result.append((cursor, min(busy_start, win_end)))
            cursor = max(cursor, busy_end)
            if cursor >= win_end:
                break
        if cursor < win_end:
            result.append((cursor, win_end))
    return result


def _split_at_midnight(
    intervals: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """把跨零点的区间切开，保证每段都属于同一天（每天的时长上限才好算）。"""
    result: list[tuple[datetime, datetime]] = []
    for start, end in intervals:
        cursor = start
        while cursor < end:
            midnight = datetime.combine(
                cursor.date() + timedelta(days=1), dt_time.min, tzinfo=cursor.tzinfo
            )
            segment_end = min(end, midnight)
            result.append((cursor, segment_end))
            cursor = segment_end
    return result


def _clip(intervals: list[tuple[datetime, datetime]], cutoff: datetime):
    """截断到考试开始之前，并返回被完全排除的段数。"""
    kept = []
    dropped = 0
    for start, end in intervals:
        if start >= cutoff:
            dropped += 1
            continue
        kept.append((start, min(end, cutoff)))
    return kept, dropped


# ---------------------------------------------------------------- 窗口构建
def _build_windows(
    now: datetime,
    cutoff: datetime,
    plan_items: list[dict],
    busy_sessions: list[tuple[datetime, datetime]],
    busy_exams: list[tuple[datetime, datetime]],
) -> tuple[list[tuple[datetime, datetime]], int, list[dict]]:
    """返回 (可用窗口, 落在考试之后的段数, 警告)。"""
    movable: list[tuple[datetime, datetime]] = []
    busy: list[tuple[datetime, datetime]] = list(busy_sessions) + list(busy_exams)
    warnings: list[dict] = []

    for item in plan_items:
        start = _parse_dt(item["start_at"])
        end = _parse_dt(item["end_at"])
        if start is None or end is None or end <= start:
            warnings.append(
                {
                    "code": "SKIP_INVALID_PLAN_ITEM",
                    "message": f"时间块 {item['id']} 时间格式无效，已跳过",
                }
            )
            continue

        is_fixed = (
            item["item_type"] in _FIXED_ITEM_TYPES
            or bool(item["is_locked"])
            or item["task_status"] == "done"
            or start <= now
        )
        if is_fixed:
            busy.append((start, end))
        elif item["item_type"] in _MOVABLE_ITEM_TYPES:
            movable.append((start, end))
        else:
            busy.append((start, end))

    merged = _merge(movable)
    free = _subtract(merged, busy)
    free = _merge(free)
    # 先合并再按零点切开，且切完不再合并——否则又会被接回去
    free = _split_at_midnight(free)
    free, dropped = _clip(free, cutoff)

    if dropped:
        warnings.append(
            {
                "code": "WINDOWS_AFTER_EXAM",
                "message": f"有 {dropped} 段可移动学习时间落在考试开始之后，已排除",
            }
        )
    return free, dropped, warnings


# ---------------------------------------------------------------- 任务排序
def _task_need(task: dict) -> int:
    for key in ("remaining_minutes", "estimated_minutes"):
        value = task.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 0


def _task_sort_key(task: dict, exam_start: datetime):
    """排序规则（越靠前越先排）：

        1. 必做在前，选做在后
        2. 截止时间早的在前；没有截止时间按考试时间算
        3. 优先级数字小的（更紧急）在前
        4. 同时长按"大的先排"（First-Fit Decreasing），减少窗口碎片
        5. 最后用 ID 兜底，保证同样输入输出一致
    """
    due = _parse_dt(task.get("due_at")) or exam_start
    is_optional = 1 if task.get("is_optional") else 0
    return (
        is_optional,
        due,
        task.get("priority") or 2,
        -_task_need(task),
        task.get("id") or "",
    )


# ---------------------------------------------------------------- 排程核心
def _unplaced_reason(
    windows: list[dict],
    used_by_day: dict[date, int],
    daily_limit: int,
    blocked_by_contiguity: bool,
) -> str:
    """任务没排完时，给一个能解释得清的原因。"""
    has_capacity_left = False
    limit_exhausted = False
    for window in windows:
        if window["start"] >= window["end"]:
            continue
        has_capacity_left = True
        day = window["start"].date()
        if used_by_day.get(day, 0) >= daily_limit:
            limit_exhausted = True

    if limit_exhausted:
        return "DAILY_LIMIT_REACHED"
    if blocked_by_contiguity:
        return "NOT_SPLITTABLE"
    return "NO_FREE_SLOT"


def _place_tasks(
    tasks: list[dict],
    windows: list[dict],
    daily_limit: int,
    exam_start: datetime,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """把任务放进窗口。返回 (排好的时间块, 未排入明细)。"""
    used_by_day: dict[date, int] = {}
    placed: list[dict] = []
    unscheduled: dict[str, list[dict]] = {}

    ordered = sorted(tasks, key=lambda t: _task_sort_key(t, exam_start))

    for task in ordered:
        need = _task_need(task)
        if need <= 0:
            unscheduled.setdefault(task["id"], []).append(
                {"reason": "NO_DURATION", "minutes": 0}
            )
            continue

        remaining = need
        blocked_by_contiguity = False

        for window in windows:
            if remaining <= 0:
                break
            if window["start"] >= window["end"]:
                continue

            day = window["start"].date()
            day_left = daily_limit - used_by_day.get(day, 0)
            if day_left <= 0:
                continue

            capacity = _minutes(window["start"], window["end"])
            take = min(capacity, remaining, day_left)
            if take <= 0:
                continue

            splittable = bool(task.get("splittable"))
            if not splittable:
                # 不拆分的任务必须在一个窗口里一次性放下
                if take < remaining:
                    blocked_by_contiguity = True
                    continue
            elif take < MIN_BLOCK_MINUTES and take < remaining:
                # 太碎，不值得拆
                continue

            start = window["start"]
            end = start + timedelta(minutes=take)
            placed.append(
                {
                    "task_id": task["id"],
                    "title": task["title"],
                    "start_at": _iso(start),
                    "end_at": _iso(end),
                    "date": start.date().isoformat(),
                    "duration_minutes": take,
                    "priority": task.get("priority"),
                    "is_optional": bool(task.get("is_optional")),
                }
            )
            window["start"] = end
            used_by_day[day] = used_by_day.get(day, 0) + take
            remaining -= take

        if remaining > 0:
            reason = _unplaced_reason(
                windows, used_by_day, daily_limit, blocked_by_contiguity
            )
            unscheduled.setdefault(task["id"], []).append(
                {"reason": reason, "minutes": remaining}
            )

    placed.sort(key=lambda item: (item["start_at"], item["task_id"]))
    return placed, unscheduled


# ---------------------------------------------------------------- 对外入口
def generate_schedule(
    user_id: str,
    exam_id: str,
    *,
    conn: sqlite3.Connection | None = None,
    exam_start_override: datetime | None = None,
) -> dict:
    """为指定用户的指定考试生成学习安排（只读，不写库）。

    返回结构见模块文档与 docs/SCHEDULER.md。所有数字都是现场算的。
    """
    now = clock.get_now()

    with _connection(conn) as db:
        user = _load_user(db, user_id)
        exam = _load_exam(db, user_id, exam_id)
        exam_start = exam_start_override or _exam_start(exam)
        if exam_start <= now:
            raise SchedulerError(
                "EXAM_IN_PAST",
                f"考试 {exam['name']!r} 的时间 {exam_start.isoformat()} 已经过去，无法据此排程",
            )

        goal = _load_goal(db, user_id, exam_id)
        tasks = _load_tasks(db, user_id, goal["id"])
        plan = _load_plan(db, user_id)
        plan_items = _load_plan_items(db, user_id, plan["id"]) if plan else []
        busy_sessions = _load_busy_sessions(db, user_id, exam_start.date())
        busy_exams = _load_busy_exams(db, user_id, exam_start.date())

    warnings: list[dict] = []
    if plan is None:
        warnings.append(
            {
                "code": "NO_CONFIRMED_PLAN",
                "message": "还没有已确认的计划，没有可移动的学习时间块，本次无法安排",
            }
        )
    if not tasks:
        warnings.append({"code": "NO_OPEN_TASK", "message": "没有待安排的未完成任务"})

    windows, _dropped, window_warnings = _build_windows(
        now, exam_start, plan_items, busy_sessions, busy_exams
    )
    warnings.extend(window_warnings)

    if not windows and tasks:
        warnings.append(
            {
                "code": "NO_FREE_WINDOW",
                "message": "考试之前没有可用的学习时间窗口",
            }
        )

    window_views = [
        {
            "start_at": _iso(start),
            "end_at": _iso(end),
            "date": start.date().isoformat(),
            "minutes": _minutes(start, end),
        }
        for start, end in windows
    ]
    # 排程期间会移动窗口起点，这里用副本操作，保留原始窗口用于返回
    working = [{"start": start, "end": end} for start, end in windows]

    daily_limit = int(goal["daily_limit_minutes"] or 0)
    schedule_items, unscheduled_map = _place_tasks(
        tasks, working, daily_limit, exam_start
    )

    required_tasks = [t for t in tasks if not t.get("is_optional")]
    optional_tasks = [t for t in tasks if t.get("is_optional")]

    required_minutes = sum(_task_need(t) for t in required_tasks)
    optional_minutes = sum(_task_need(t) for t in optional_tasks)
    scheduled_required = sum(
        item["duration_minutes"] for item in schedule_items if not item["is_optional"]
    )
    scheduled_optional = sum(
        item["duration_minutes"] for item in schedule_items if item["is_optional"]
    )

    unscheduled = []
    for task in sorted(tasks, key=lambda t: _task_sort_key(t, exam_start)):
        for entry in unscheduled_map.get(task["id"], []):
            if entry["minutes"] <= 0 and entry["reason"] == "NO_DURATION":
                warnings.append(
                    {
                        "code": "TASK_WITHOUT_DURATION",
                        "message": f"任务「{task['title']}」没有剩余时长，已跳过",
                    }
                )
                continue
            unscheduled.append(
                {
                    "task_id": task["id"],
                    "title": task["title"],
                    "minutes": entry["minutes"],
                    "is_optional": bool(task.get("is_optional")),
                    "reason": entry["reason"],
                }
            )
            if entry["reason"] == "DAILY_LIMIT_REACHED":
                warnings.append(
                    {
                        "code": "DAILY_LIMIT_REACHED",
                        "message": (
                            f"任务「{task['title']}」因每日上限 {daily_limit} 分钟未排完，"
                            f"还差 {entry['minutes']} 分钟"
                        ),
                    }
                )

    required_gap = max(0, required_minutes - scheduled_required)
    optional_gap = max(0, optional_minutes - scheduled_optional)

    explanation = (
        f"考试「{exam['name']}」在 {exam_start:%Y-%m-%d %H:%M} 开始；"
        f"考前可用 {sum(w['minutes'] for w in window_views)} 分钟（{len(window_views)} 段）。"
        f"必做需求 {required_minutes} 分钟，已排 {scheduled_required} 分钟，"
        f"缺口 {required_gap} 分钟；"
        f"选做需求 {optional_minutes} 分钟，已排 {scheduled_optional} 分钟，"
        f"未排 {optional_gap} 分钟。"
    )
    if required_gap:
        explanation += " 必做任务没有排完，需要补充可用时间或由用户确认调整范围。"

    return {
        "user_id": user["id"],
        "generated_at": now.isoformat(),
        "time_mode": clock.get_mode(),
        "exam": {
            "id": exam["id"],
            "name": exam["name"],
            "exam_date": exam["exam_date"],
            "start_at": _iso(exam_start),
            "end_at": _iso(_exam_end_with_override(exam, exam_start, exam_start_override)),
            "cutoff_at": _iso(exam_start),
        },
        "goal_id": goal["id"],
        "daily_limit_minutes": daily_limit,
        "plan": {"id": plan["id"], "version": plan["version"]} if plan else None,
        "windows": window_views,
        "schedule_items": schedule_items,
        "required_minutes": required_minutes,
        "scheduled_required_minutes": scheduled_required,
        "required_gap_minutes": required_gap,
        "optional_minutes": optional_minutes,
        "scheduled_optional_minutes": scheduled_optional,
        "unscheduled_optional_minutes": optional_gap,
        "total_requested_minutes": required_minutes + optional_minutes,
        "unscheduled": unscheduled,
        "warnings": warnings,
        "explanation": explanation,
    }
