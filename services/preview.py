"""排程预览（草稿）服务。

作用：
    把排程结果变成一条「待用户确认的草稿」写进 previews 表，
    同时给出新旧计划差异。**不修改正式计划**——plans / plan_items / tasks / exams
    一个字节都不动，真正提交留给后续的确认流程。

四条硬规则：
    1. 只写 previews 表；正式数据只读
    2. 每个预览记录 base_plan_id / base_version；正式计划版本变了，这个预览就不能再确认
    3. 预览绑定用户；别人的预览 id 一律当作不存在
    4. 过期 / 已确认 / 已取消的预览都不能再确认

已知限制（对应 OPEN_QUESTIONS T11）：
    候选时间窗口只来自当前正式计划里的「可移动学习块」，
    不会自动发现计划之外的空闲时间。预览的 notes 里会写明这一点。

用法：
    from services.preview import create_preview, get_preview, assert_confirmable

    preview = create_preview("us_demo_a", "ex_demo_ml_final")
    preview["confirm_code"]    # 给用户回的编号，如 T01
    preview["diff"]["removed"] # 这次会移出哪些学习块
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Iterator

from database.connection import get_connection
from services import clock
from services import scheduler

ENV_PREVIEW_TTL = "UNIPULSE_PREVIEW_TTL_MINUTES"
DEFAULT_TTL_MINUTES = 30

# 提示文案：让 A/C 知道这次预览的能力边界
NOTE_WINDOW_SOURCE = (
    "候选时间窗口仅来自当前正式计划中的可移动学习块；"
    "不会自动寻找计划之外的空闲时间（T11 待确认）。"
)
NOTE_NOT_COMMITTED = "这是待确认草稿，正式计划没有被修改；确认后才会生效。"


class PreviewError(Exception):
    """预览相关错误。"""

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


# ---------------------------------------------------------------- 工具
def get_ttl_minutes(ttl_minutes: int | None = None) -> int:
    if ttl_minutes is not None:
        if ttl_minutes <= 0:
            raise PreviewError("INVALID_TTL", "预览有效期必须大于 0 分钟")
        return ttl_minutes

    import os

    raw = (os.environ.get(ENV_PREVIEW_TTL) or "").strip()
    if not raw:
        return DEFAULT_TTL_MINUTES
    try:
        value = int(raw)
    except ValueError as exc:
        raise PreviewError(
            "INVALID_TTL", f"{ENV_PREVIEW_TTL}={raw!r} 必须是整数"
        ) from exc
    if value <= 0:
        raise PreviewError("INVALID_TTL", f"{ENV_PREVIEW_TTL} 必须大于 0")
    return value


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


def _blocks(items: list[dict]) -> list[dict]:
    """把时间块规整成可比较的形式（按开始时间排序）。"""
    return sorted(
        (
            {"start_at": item["start_at"], "end_at": item["end_at"]}
            for item in items
        ),
        key=lambda block: (block["start_at"], block["end_at"]),
    )


# ---------------------------------------------------------------- 读取正式数据
def _load_user(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, name FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None:
        raise PreviewError("USER_NOT_FOUND", f"用户 {user_id!r} 不存在")
    return row


def _load_current_plan(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, goal_id, status, version, note, updated_at
        FROM plans
        WHERE user_id = ? AND status = 'confirmed'
        ORDER BY version DESC, updated_at DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()


def _load_plan_items(
    conn: sqlite3.Connection, user_id: str, plan_id: str
) -> list[dict]:
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


def _old_plan_snapshot(plan: sqlite3.Row | None, items: list[dict]) -> dict | None:
    if plan is None:
        return None
    return {
        "id": plan["id"],
        "version": plan["version"],
        "status": plan["status"],
        "note": plan["note"],
        "items": [
            {
                "id": item["id"],
                "start_at": item["start_at"],
                "end_at": item["end_at"],
                "title": item["title"],
                "item_type": item["item_type"],
                "task_id": item["task_id"],
                "is_locked": bool(item["is_locked"]),
            }
            for item in items
        ],
    }


# ---------------------------------------------------------------- 差异计算
def _protection_of(item: dict, now: datetime) -> str | None:
    """返回保护原因；None 表示这块可以参与重排。"""
    if item["item_type"] in ("course", "exam"):
        return "fixed_event"
    if item["is_locked"]:
        return "locked"
    if item["task_status"] == "done":
        return "task_done"
    start = _parse_dt(item["start_at"])
    if start is not None and start <= now:
        return "started"
    return None


def build_diff(
    old_items: list[dict],
    new_items: list[dict],
    now: datetime,
) -> dict:
    """比较正式计划与候选排程。

    匹配键优先用 task_id（稳定），不用标题——标题可能重复或改写。
    没有 task_id 的时间块（课程、考试、自定义）不算本次重排范围，原样保留。
    """
    diff: dict[str, list[dict]] = {
        "added": [],
        "removed": [],
        "changed": [],
        "kept": [],
    }

    old_by_task: dict[str, list[dict]] = {}
    for item in old_items:
        task_id = item.get("task_id")
        if task_id:
            old_by_task.setdefault(task_id, []).append(item)
        else:
            diff["kept"].append(
                {
                    "task_id": None,
                    "title": item["title"],
                    "item_type": item["item_type"],
                    "reason": "not_in_scope",
                    "blocks": _blocks([item]),
                }
            )

    new_by_task: dict[str, list[dict]] = {}
    for item in new_items:
        new_by_task.setdefault(item["task_id"], []).append(
            {"start_at": item["start_at"], "end_at": item["end_at"]}
        )

    for task_id in sorted(old_by_task):
        items = old_by_task[task_id]
        title = items[0]["title"]

        reasons = [_protection_of(item, now) for item in items]
        blocking = next((reason for reason in reasons if reason), None)
        if blocking:
            diff["kept"].append(
                {
                    "task_id": task_id,
                    "title": title,
                    "reason": blocking,
                    "blocks": _blocks(items),
                }
            )
            continue

        if task_id not in new_by_task:
            diff["removed"].append(
                {
                    "task_id": task_id,
                    "title": title,
                    "blocks": _blocks(items),
                }
            )
            continue

        before = _blocks(items)
        after = sorted(
            new_by_task[task_id], key=lambda b: (b["start_at"], b["end_at"])
        )
        if before == after:
            diff["kept"].append(
                {"task_id": task_id, "title": title, "reason": "unchanged",
                 "blocks": before}
            )
        else:
            diff["changed"].append(
                {
                    "task_id": task_id,
                    "title": title,
                    "before": before,
                    "after": after,
                }
            )

    for task_id in sorted(new_by_task):
        if task_id in old_by_task:
            continue
        diff["added"].append(
            {
                "task_id": task_id,
                "title": next(
                    (
                        item["title"]
                        for item in new_items
                        if item["task_id"] == task_id
                    ),
                    task_id,
                ),
                "blocks": sorted(
                    new_by_task[task_id],
                    key=lambda b: (b["start_at"], b["end_at"]),
                ),
            }
        )

    return diff


# ---------------------------------------------------------------- 预览存取
def _next_confirm_code(conn: sqlite3.Connection, user_id: str) -> str:
    rows = conn.execute(
        "SELECT confirm_code FROM previews WHERE user_id = ?", (user_id,)
    ).fetchall()
    highest = 0
    for row in rows:
        code = (row["confirm_code"] or "").lstrip("T")
        if code.isdigit():
            highest = max(highest, int(code))
    return f"T{highest + 1:02d}"


def _load_preview_row(
    conn: sqlite3.Connection, user_id: str, preview_id: str
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM previews WHERE id = ? AND user_id = ?",
        (preview_id, user_id),
    ).fetchone()
    if row is None:
        raise PreviewError("PREVIEW_NOT_FOUND", "预览不存在或不属于当前用户")
    return row


def _find_reusable(
    conn: sqlite3.Connection,
    user_id: str,
    goal_id: str,
    base_plan_id: str | None,
    base_version: int,
    diff_json: str,
    now: datetime,
) -> sqlite3.Row | None:
    """同样的基础版本 + 同样的差异 → 复用已有草稿，避免重复刷编号。"""
    rows = conn.execute(
        """
        SELECT * FROM previews
        WHERE user_id = ? AND goal_id = ? AND status = 'pending'
          AND base_version = ? AND base_plan_id IS ?
        ORDER BY created_at DESC
        """,
        (user_id, goal_id, base_version, base_plan_id),
    ).fetchall()
    for row in rows:
        expires = _parse_dt(row["expires_at"])
        if expires is not None and expires <= now:
            continue
        if row["diff_json"] == diff_json:
            return row
    return None


def _effective_status(row: sqlite3.Row, now: datetime) -> str:
    """把"已过期但还没被标记"的 pending 显示成 expired（不改库）。"""
    if row["status"] != "pending":
        return row["status"]
    expires = _parse_dt(row["expires_at"])
    if expires is not None and expires <= now:
        return "expired"
    return "pending"


def _render(row: sqlite3.Row, now: datetime, reused: bool = False) -> dict:
    capacity = json.loads(row["capacity_json"] or "{}")
    diff = json.loads(row["diff_json"] or "{}")
    return {
        "preview_id": row["id"],
        "confirm_code": row["confirm_code"],
        "kind": row["kind"],
        "status": _effective_status(row, now),
        "base_plan_id": row["base_plan_id"],
        "base_version": row["base_version"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "confirmed_at": row["confirmed_at"],
        "exam": capacity.get("exam"),
        "old_plan": capacity.get("old_plan"),
        "new_schedule": {
            "windows": capacity.get("windows", []),
            "schedule_items": capacity.get("schedule_items", []),
        },
        "diff": diff,
        "summary": capacity.get("summary", {}),
        "warnings": capacity.get("warnings", []),
        "notes": capacity.get("notes", []),
        "explanation": capacity.get("explanation", ""),
        "applied": capacity.get("applied"),
        "proposed_exam": capacity.get("proposed_exam"),
        "reused": reused,
    }


def _build_summary_text(diff: dict, summary: dict, exam: dict) -> str:
    counts = (
        f"新增 {len(diff.get('added', []))} 块、"
        f"移除 {len(diff.get('removed', []))} 块、"
        f"调整 {len(diff.get('changed', []))} 块、"
        f"保留 {len(diff.get('kept', []))} 块"
    )
    gaps = (
        f"必做缺口 {summary.get('required_gap_minutes', 0)} 分钟，"
        f"选做未排 {summary.get('unscheduled_optional_minutes', 0)} 分钟"
    )
    return f"考试「{exam.get('name', '')}」调整后的计划：{counts}；{gaps}。"


def _store_preview(
    db: sqlite3.Connection,
    *,
    user_id: str,
    goal_id: str | None,
    kind: str,
    base_plan_id: str | None,
    base_version: int,
    summary_text: str,
    diff_json: str,
    capacity_json: str,
    expires_at: str,
) -> sqlite3.Row:
    """分配确认码并写入预览；写入后回读，失败则整体回滚。"""
    preview_id = f"pv_{secrets.token_hex(6)}"
    confirm_code = _next_confirm_code(db, user_id)
    with db:
        db.execute(
            """
            INSERT INTO previews (id, user_id, goal_id, kind, confirm_code,
                                  status, base_plan_id, base_version, summary,
                                  diff_json, capacity_json, expires_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (
                preview_id,
                user_id,
                goal_id,
                kind,
                confirm_code,
                base_plan_id,
                base_version,
                summary_text,
                diff_json,
                capacity_json,
                expires_at,
            ),
        )
        return _load_preview_row(db, user_id, preview_id)


# ---------------------------------------------------------------- 对外：创建
def create_preview(
    user_id: str,
    exam_id: str,
    *,
    conn: sqlite3.Connection | None = None,
    ttl_minutes: int | None = None,
) -> dict:
    """生成（或复用）一条待确认的排程预览。

    参数：
        user_id      可信身份里的用户 ID
        exam_id      要重排的考试（必须属于该用户）
        ttl_minutes  有效期，默认读 UNIPULSE_PREVIEW_TTL_MINUTES，再默认 30 分钟

    返回：预览详情（见 docs/PREVIEW.md）
    """
    now = clock.get_now()
    ttl = get_ttl_minutes(ttl_minutes)

    with _connection(conn) as db:
        _load_user(db, user_id)

        # 排程本身只读；这里复用同一个连接
        schedule = scheduler.generate_schedule(user_id, exam_id, conn=db)

        plan = _load_current_plan(db, user_id)
        old_items = _load_plan_items(db, user_id, plan["id"]) if plan else []
        base_plan_id = plan["id"] if plan else None
        base_version = int(plan["version"]) if plan else 0

        diff = build_diff(old_items, schedule["schedule_items"], now)
        summary_numbers = {
            "required_minutes": schedule["required_minutes"],
            "scheduled_required_minutes": schedule["scheduled_required_minutes"],
            "required_gap_minutes": schedule["required_gap_minutes"],
            "optional_minutes": schedule["optional_minutes"],
            "scheduled_optional_minutes": schedule["scheduled_optional_minutes"],
            "unscheduled_optional_minutes": schedule["unscheduled_optional_minutes"],
            "total_requested_minutes": schedule["total_requested_minutes"],
        }

        notes = [NOTE_WINDOW_SOURCE, NOTE_NOT_COMMITTED]
        if schedule["required_gap_minutes"] > 0:
            notes.append(
                f"必做任务还差 {schedule['required_gap_minutes']} 分钟，"
                "需要用户补充可用时间或调整复习范围。"
            )

        capacity_json = json.dumps(
            {
                "exam": schedule["exam"],
                "old_plan": _old_plan_snapshot(plan, old_items),
                "windows": schedule["windows"],
                "schedule_items": schedule["schedule_items"],
                "summary": summary_numbers,
                "warnings": schedule["warnings"],
                "explanation": schedule["explanation"],
                "notes": notes,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        diff_json = json.dumps(diff, ensure_ascii=False, sort_keys=True)

        reusable = _find_reusable(
            db, user_id, schedule["goal_id"], base_plan_id, base_version,
            diff_json, now,
        )
        if reusable is not None:
            return _render(reusable, now, reused=True)

        summary_text = _build_summary_text(diff, summary_numbers, schedule["exam"])
        stored = _store_preview(
            db,
            user_id=user_id,
            goal_id=schedule["goal_id"],
            kind="initial_plan" if plan is None else "reschedule",
            base_plan_id=base_plan_id,
            base_version=base_version,
            summary_text=summary_text,
            diff_json=diff_json,
            capacity_json=capacity_json,
            expires_at=(now + timedelta(minutes=ttl)).isoformat(),
        )
        return _render(stored, now)


def _exam_start_of(exam: sqlite3.Row) -> datetime | None:
    if not exam["start_time"]:
        return None
    try:
        return datetime.fromisoformat(
            f"{exam['exam_date']}T{exam['start_time']}"
        ).replace(tzinfo=clock.get_timezone())
    except ValueError:
        return None


def create_exam_change_preview(
    user_id: str,
    exam_id: str,
    new_start: str | datetime,
    *,
    expected_version: int | None = None,
    notice_id: str | None = None,
    conn: sqlite3.Connection | None = None,
    ttl_minutes: int | None = None,
) -> dict:
    """按「拟议的新考试时间」生成预览（只读，改考试时间要等确认）。

    这是给网页的"改期预览"用的：排程按新时间计算，但 exams 表不动。
    确认时（confirm_preview）才会在同一个事务里更新考试时间。
    """
    now = clock.get_now()
    ttl = get_ttl_minutes(ttl_minutes)

    proposed_start = _parse_dt(new_start) if isinstance(new_start, str) else new_start
    if proposed_start is None:
        raise PreviewError("INVALID_EXAM_TIME", "新的考试时间格式不正确，需要带时区的 ISO 8601")
    if proposed_start.tzinfo is None:
        raise PreviewError("INVALID_EXAM_TIME", "新的考试时间必须带时区")
    if proposed_start <= now:
        raise PreviewError("INVALID_EXAM_TIME", "新的考试时间必须晚于当前时间")

    with _connection(conn) as db:
        _load_user(db, user_id)
        exam = db.execute(
            "SELECT id, user_id, name, exam_date, start_time, end_time, version, "
            "source_notice_id FROM exams WHERE id = ? AND user_id = ?",
            (exam_id, user_id),
        ).fetchone()
        if exam is None:
            raise scheduler.SchedulerError(
                "EXAM_NOT_FOUND", f"用户 {user_id!r} 名下没有考试 {exam_id!r}"
            )

        current_start = _exam_start_of(exam)
        if current_start is not None and proposed_start == current_start:
            raise PreviewError("EXAM_TIME_UNCHANGED", "考试时间没有变化，不需要生成预览")

        plan = _load_current_plan(db, user_id)
        base_version = int(plan["version"]) if plan else 0
        if expected_version is not None and int(expected_version) != base_version:
            raise PreviewError(
                "VERSION_CONFLICT",
                f"你看到的版本是 {expected_version}，当前正式计划是 v{base_version}，请刷新",
            )

        schedule = scheduler.generate_schedule(
            user_id, exam_id, conn=db, exam_start_override=proposed_start
        )
        old_items = _load_plan_items(db, user_id, plan["id"]) if plan else []
        diff = build_diff(old_items, schedule["schedule_items"], now)

        summary_numbers = {
            "required_minutes": schedule["required_minutes"],
            "scheduled_required_minutes": schedule["scheduled_required_minutes"],
            "required_gap_minutes": schedule["required_gap_minutes"],
            "optional_minutes": schedule["optional_minutes"],
            "scheduled_optional_minutes": schedule["scheduled_optional_minutes"],
            "unscheduled_optional_minutes": schedule["unscheduled_optional_minutes"],
            "total_requested_minutes": schedule["total_requested_minutes"],
        }

        proposed = {
            "exam_id": exam_id,
            "exam_version": int(exam["version"]),
            "old_start_at": current_start.isoformat() if current_start else None,
            "new_start_at": proposed_start.isoformat(),
            "new_end_at": schedule["exam"]["end_at"],
            "source_notice_id": notice_id,
        }

        notes = [
            NOTE_WINDOW_SOURCE,
            "这是待确认草稿：考试时间与正式计划都还没改，确认后才会一起生效。",
        ]
        if schedule["required_gap_minutes"] > 0:
            notes.append(
                f"必做任务还差 {schedule['required_gap_minutes']} 分钟，"
                "需要用户补充可用时间或调整复习范围。"
            )

        capacity_json = json.dumps(
            {
                "exam": schedule["exam"],
                "proposed_exam": proposed,
                "old_plan": _old_plan_snapshot(plan, old_items),
                "windows": schedule["windows"],
                "schedule_items": schedule["schedule_items"],
                "summary": summary_numbers,
                "warnings": schedule["warnings"],
                "explanation": schedule["explanation"],
                "notes": notes,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        diff_json = json.dumps(diff, ensure_ascii=False, sort_keys=True)

        counts = (
            f"新增 {len(diff['added'])} 块、移除 {len(diff['removed'])} 块、"
            f"调整 {len(diff['changed'])} 块、保留 {len(diff['kept'])} 块"
        )
        summary_text = (
            f"考试「{exam['name']}」改期预览：{counts}；"
            f"必做缺口 {summary_numbers['required_gap_minutes']} 分钟，"
            f"选做未排 {summary_numbers['unscheduled_optional_minutes']} 分钟。"
        )

        stored = _store_preview(
            db,
            user_id=user_id,
            goal_id=schedule["goal_id"],
            kind="reschedule",
            base_plan_id=plan["id"] if plan else None,
            base_version=base_version,
            summary_text=summary_text,
            diff_json=diff_json,
            capacity_json=capacity_json,
            expires_at=(now + timedelta(minutes=ttl)).isoformat(),
        )
        return _render(stored, now)


# ---------------------------------------------------------------- 对外：读取
def get_preview(user_id: str, preview_id: str, *, conn=None) -> dict:
    """读取一条预览（只读）。过期会显示为 expired，但不改库。"""
    now = clock.get_now()
    with _connection(conn) as db:
        row = _load_preview_row(db, user_id, preview_id)
    return _render(row, now)


def cancel_preview(user_id: str, preview_id: str, *, conn=None) -> dict:
    """取消一条待确认预览。只能取消 pending 的。"""
    now = clock.get_now()
    with _connection(conn) as db:
        row = _load_preview_row(db, user_id, preview_id)
        status = _effective_status(row, now)
        if status != "pending":
            raise PreviewError(
                "PREVIEW_NOT_PENDING", f"预览当前状态是 {status}，不能取消"
            )
        with db:
            db.execute(
                "UPDATE previews SET status = 'cancelled' "
                "WHERE id = ? AND user_id = ? AND status = 'pending'",
                (preview_id, user_id),
            )
        row = _load_preview_row(db, user_id, preview_id)
    return _render(row, now)


# ---------------------------------------------------------------- 对外：确认前校验
def assert_confirmable(
    user_id: str,
    preview_id: str | None = None,
    confirm_code: str | None = None,
    *,
    conn=None,
) -> dict:
    """确认流程在写入之前调用它：检查归属、状态、有效期、版本。

    本轮只做校验，不写正式计划。返回可直接展示的预览详情。
    """
    if not preview_id and not confirm_code:
        raise PreviewError("INVALID_ARGUMENT", "必须提供 preview_id 或 confirm_code")

    now = clock.get_now()
    with _connection(conn) as db:
        if preview_id:
            row = _load_preview_row(db, user_id, preview_id)
        else:
            row = db.execute(
                """
                SELECT * FROM previews
                WHERE user_id = ? AND confirm_code = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, confirm_code),
            ).fetchone()
            if row is None:
                raise PreviewError("PREVIEW_NOT_FOUND", "预览不存在或不属于当前用户")

        status = _effective_status(row, now)
        if status == "expired":
            raise PreviewError("PREVIEW_EXPIRED", "预览已过期，请重新生成")
        if status != "pending":
            raise PreviewError(
                "PREVIEW_NOT_PENDING", f"预览当前状态是 {status}，不能确认"
            )

        plan = _load_current_plan(db, user_id)
        current_version = int(plan["version"]) if plan else 0
        current_id = plan["id"] if plan else None
        if current_id != row["base_plan_id"] or current_version != row["base_version"]:
            raise PreviewError(
                "VERSION_CONFLICT",
                "正式计划已经变化（当前版本 "
                f"{current_version}，预览基于 {row['base_version']}），请重新生成预览",
            )

        rendered = _render(row, now)
    rendered["confirmable"] = True
    return rendered


# ---------------------------------------------------------------- 确认并提交
CONFIRM_CODE_PATTERN = re.compile(r"^T\d{2,}$")

# 这些原因的时间块在提交时必须原样保留（不能删了重建）
PROTECTED_REASONS = {"task_done", "locked", "started", "fixed_event", "not_in_scope"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _validate_confirm_code(row: sqlite3.Row, confirm_code: str | None) -> None:
    """确认码必须存在、格式正确、并且与这条预览一致。"""
    if not confirm_code or not confirm_code.strip():
        raise PreviewError(
            "MISSING_CONFIRM_CODE", "确认必须提供预览里显示的确认码（例如 T03）"
        )
    code = confirm_code.strip().upper()
    if not CONFIRM_CODE_PATTERN.match(code):
        raise PreviewError(
            "INVALID_CONFIRM_CODE", "确认码格式应为 T + 两位数字，例如 T03"
        )
    if code != (row["confirm_code"] or "").upper():
        raise PreviewError("CONFIRM_CODE_MISMATCH", "确认码与这条预览不匹配")


def _ensure_version(db: sqlite3.Connection, user_id: str, row: sqlite3.Row) -> sqlite3.Row | None:
    """确认时正式计划仍必须是预览基于的那一版。"""
    plan = _load_current_plan(db, user_id)
    current_id = plan["id"] if plan else None
    current_version = int(plan["version"]) if plan else 0
    if current_id != row["base_plan_id"] or current_version != int(row["base_version"]):
        raise PreviewError(
            "VERSION_CONFLICT",
            f"正式计划已变化（当前 v{current_version}，预览基于 v{row['base_version']}），请重新生成预览",
        )
    return plan


def _insert_plan(
    db: sqlite3.Connection,
    *,
    plan_id: str,
    user_id: str,
    goal_id: str | None,
    version: int,
    based_on_plan_id: str | None,
) -> None:
    db.execute(
        """
        INSERT INTO plans (id, user_id, goal_id, status, version,
                           based_on_plan_id, note, updated_at)
        VALUES (?, ?, ?, 'confirmed', ?, ?, NULL, datetime('now', 'localtime'))
        """,
        (plan_id, user_id, goal_id, version, based_on_plan_id),
    )


def _supersede_plan(db: sqlite3.Connection, user_id: str, plan_id: str) -> None:
    db.execute(
        "UPDATE plans SET status = 'superseded', "
        "updated_at = datetime('now', 'localtime') "
        "WHERE id = ? AND user_id = ? AND status = 'confirmed'",
        (plan_id, user_id),
    )


def _insert_plan_items(
    db: sqlite3.Connection,
    *,
    plan_id: str,
    schedule_items: list[dict],
    protected_items: list[dict],
) -> int:
    """写入新计划的时间块：新排的 + 必须保留的旧块。"""
    rows: list[tuple] = []
    for item in schedule_items:
        rows.append(
            (
                _new_id("pi"),
                plan_id,
                item["start_at"],
                item["end_at"],
                item["title"],
                "task",
                item.get("task_id"),
                None,
                None,
                0,
            )
        )
    for item in protected_items:
        rows.append(
            (
                _new_id("pi"),
                plan_id,
                item["start_at"],
                item["end_at"],
                item["title"],
                item["item_type"],
                item.get("task_id"),
                item.get("session_id"),
                item.get("exam_id"),
                1 if item.get("is_locked") else 0,
            )
        )
    db.executemany(
        """
        INSERT INTO plan_items (id, plan_id, start_at, end_at, title, item_type,
                                task_id, session_id, exam_id, is_locked)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _cancel_reminders(db: sqlite3.Connection, user_id: str, plan_id: str) -> int:
    """只失效"属于将被替换的那份计划"的待发提醒，别的计划不动。"""
    cursor = db.execute(
        """
        UPDATE reminders SET status = 'cancelled'
        WHERE user_id = ? AND status IN ('queued', 'failed')
          AND plan_item_id IN (SELECT id FROM plan_items WHERE plan_id = ?)
        """,
        (user_id, plan_id),
    )
    return cursor.rowcount or 0


def _mark_confirmed(
    db: sqlite3.Connection,
    *,
    user_id: str,
    preview_id: str,
    confirmed_at: str,
    applied: dict,
) -> None:
    row = db.execute(
        "SELECT capacity_json FROM previews WHERE id = ? AND user_id = ?",
        (preview_id, user_id),
    ).fetchone()
    capacity = json.loads(row["capacity_json"] or "{}") if row else {}
    capacity["applied"] = applied
    cursor = db.execute(
        """
        UPDATE previews
        SET status = 'confirmed', confirmed_at = ?, capacity_json = ?
        WHERE id = ? AND user_id = ? AND status = 'pending'
        """,
        (confirmed_at, json.dumps(capacity, ensure_ascii=False, sort_keys=True),
         preview_id, user_id),
    )
    if cursor.rowcount != 1:
        raise PreviewError("PREVIEW_NOT_PENDING", "预览状态已变化，确认未生效")


def _duplicate_result(db: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> dict:
    """重复确认：返回第一次的结果，不写任何东西。"""
    capacity = json.loads(row["capacity_json"] or "{}")
    applied = capacity.get("applied") or {}
    diff = json.loads(row["diff_json"] or "{}")
    plan = db.execute(
        "SELECT id, version, status FROM plans WHERE id = ? AND user_id = ?",
        (applied.get("plan_id"), row["user_id"]),
    ).fetchone()
    return {
        "ok": True,
        "duplicate": True,
        "preview_id": row["id"],
        "applied": {
            "plan_id": applied.get("plan_id"),
            "plan_version": plan["version"] if plan else applied.get("plan_version"),
            "plan_status": plan["status"] if plan else None,
            "changed": diff.get("changed", []),
            "removed": diff.get("removed", []),
            "kept": diff.get("kept", []),
            "added": diff.get("added", []),
            "items_written": applied.get("items_written"),
            "confirmed_at": row["confirmed_at"],
        },
        "reminders": {"cancelled": 0, "queued": 0},
        "verified_at": now.isoformat(),
    }


def confirm_preview(
    user_id: str,
    preview_id: str,
    confirm_code: str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    expected_version: int | None = None,
) -> dict:
    """把预览提交为新的正式计划（单事务）。

    参数：
        user_id          可信身份里的用户 ID
        preview_id       预览 ID
        confirm_code     用户在企微里回填的确认码（如 T03），必须与预览一致
        expected_version 可选，前端/调用方认为的当前计划版本（用于冲突检测）

    返回：
        {"ok", "duplicate", "preview_id", "applied": {...}, "reminders": {...}, "verified_at"}

    幂等：重复确认返回第一次的结果，不会重复建计划、不会重复递增版本。
    """
    now = clock.get_now()

    with _connection(conn) as db:
        _load_user(db, user_id)
        row = _load_preview_row(db, user_id, preview_id)
        _validate_confirm_code(row, confirm_code)

        status = _effective_status(row, now)
        if status == "confirmed":
            return _duplicate_result(db, row, now)
        if status == "expired":
            raise PreviewError("PREVIEW_EXPIRED", "预览已过期，请重新生成")
        if status != "pending":
            raise PreviewError(
                "PREVIEW_NOT_PENDING", f"预览当前状态是 {status}，不能确认"
            )

        if expected_version is not None and int(expected_version) != int(row["base_version"]):
            raise PreviewError(
                "VERSION_CONFLICT",
                f"你看到的版本是 {expected_version}，预览基于 {row['base_version']}，请刷新后重试",
            )

        _ensure_version(db, user_id, row)

        capacity = json.loads(row["capacity_json"] or "{}")
        schedule_items = capacity.get("schedule_items") or []
        old_items = (
            _load_plan_items(db, user_id, row["base_plan_id"])
            if row["base_plan_id"]
            else []
        )
        protected_items = [
            item for item in old_items if _protection_of(item, now)
        ]
        diff = json.loads(row["diff_json"] or "{}")
        goal_id = row["goal_id"]
        created_at = now.isoformat()

        with db:                                  # ---- 单事务开始 ----
            fresh = _load_preview_row(db, user_id, preview_id)
            if _effective_status(fresh, now) != "pending":
                raise PreviewError("PREVIEW_NOT_PENDING", "预览状态已变化，确认未生效")
            current_plan = _ensure_version(db, user_id, fresh)

            proposed = capacity.get("proposed_exam")
            exam_change = None
            if proposed:
                fresh_exam = db.execute(
                    "SELECT id, version FROM exams WHERE id = ? AND user_id = ?",
                    (proposed.get("exam_id"), user_id),
                ).fetchone()
                if fresh_exam is None:
                    raise PreviewError("EXAM_NOT_FOUND", "预览对应的考试已不存在")
                if int(fresh_exam["version"]) != int(proposed.get("exam_version", -1)):
                    raise PreviewError(
                        "EXAM_VERSION_CONFLICT",
                        "考试信息已被修改，请重新生成预览",
                    )
                new_start = _parse_dt(proposed.get("new_start_at"))
                new_end = _parse_dt(proposed.get("new_end_at"))
                if new_start is None:
                    raise PreviewError("INVALID_EXAM_TIME", "预览里的新考试时间无效")
                if new_start <= now:
                    raise PreviewError("PREVIEW_EXPIRED", "新的考试时间已经过去，请重新生成预览")
                db.execute(
                    """
                    UPDATE exams
                    SET exam_date = ?, start_time = ?, end_time = ?,
                        version = version + 1, status = 'moved',
                        source_notice_id = COALESCE(?, source_notice_id),
                        updated_at = datetime('now', 'localtime')
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        new_start.date().isoformat(),
                        new_start.strftime("%H:%M"),
                        new_end.strftime("%H:%M") if new_end else None,
                        proposed.get("source_notice_id"),
                        proposed["exam_id"],
                        user_id,
                    ),
                )
                exam_change = {
                    "exam_id": proposed["exam_id"],
                    "old_start_at": proposed.get("old_start_at"),
                    "new_start_at": proposed["new_start_at"],
                    "exam_version": int(proposed.get("exam_version", 0)) + 1,
                }

            new_version = (int(current_plan["version"]) if current_plan else 0) + 1
            new_plan_id = _new_id("pl")

            _insert_plan(
                db,
                plan_id=new_plan_id,
                user_id=user_id,
                goal_id=goal_id,
                version=new_version,
                based_on_plan_id=row["base_plan_id"],
            )
            if current_plan is not None:
                _supersede_plan(db, user_id, current_plan["id"])

            items_written = _insert_plan_items(
                db,
                plan_id=new_plan_id,
                schedule_items=schedule_items,
                protected_items=protected_items,
            )
            reminders_cancelled = (
                _cancel_reminders(db, user_id, row["base_plan_id"])
                if row["base_plan_id"]
                else 0
            )

            applied = {
                "plan_id": new_plan_id,
                "plan_version": new_version,
                "items_written": items_written,
                "reminders_cancelled": reminders_cancelled,
                "exam": exam_change,
                "confirmed_at": created_at,
            }
            _mark_confirmed(
                db,
                user_id=user_id,
                preview_id=preview_id,
                confirmed_at=created_at,
                applied=applied,
            )
        # ---- 事务提交 ----

        # 写入后重查：确认落库的是我们以为的东西
        verified = db.execute(
            "SELECT id, version, status FROM plans WHERE id = ? AND user_id = ?",
            (new_plan_id, user_id),
        ).fetchone()
        if verified is None or verified["status"] != "confirmed":
            raise PreviewError("INTERNAL_ERROR", "计划写入后校验失败")
        item_count = db.execute(
            "SELECT COUNT(*) AS n FROM plan_items WHERE plan_id = ?", (new_plan_id,)
        ).fetchone()["n"]
        if item_count != items_written:
            raise PreviewError("INTERNAL_ERROR", "计划时间块数量校验失败")

        return {
            "ok": True,
            "duplicate": False,
            "preview_id": preview_id,
            "applied": {
                "plan_id": new_plan_id,
                "plan_version": int(verified["version"]),
                "plan_status": verified["status"],
                "based_on_plan_id": row["base_plan_id"],
                "changed": diff.get("changed", []),
                "removed": diff.get("removed", []),
                "kept": diff.get("kept", []),
                "added": diff.get("added", []),
                "items_written": items_written,
                "exam": exam_change,
                "confirmed_at": created_at,
            },
            "reminders": {"cancelled": reminders_cancelled, "queued": 0},
            "verified_at": now.isoformat(),
        }
