"""把 B 的后端数据整理成 C 网页实际使用的 workspace 结构。

C 的页面（`web/client.py` 的 `ApiClient.snapshot`）要求这些键：
    version, profile, plans, goals, events, classes, tasks,
    study_blocks, reminders, notices, pending, activity

本模块只读：从现有表里取值并做**字段映射**，不写库、不重复业务逻辑。

映射原则：
    - 有真实字段的，直接映射（如 classes ← class_sessions）
    - 没有数据来源的，返回空数组 / 明确空值，不编造
    - `tasks.actual_minutes` 用「预计 - 剩余」推导（两者都存在时），并注明
    - `study_blocks.status` 固定为 confirmed：只输出当前正式计划里的块

已知缺口（对应 docs/C_INTEGRATION_GAPS.md / T11）：
    - goals.scope / availability 没有字段，返回空字符串
    - plans.event_version 用考试当前 version 代替（计划随考试版本失效）
    - reminders.event_id 由 plan_item → plan → goal → exam 反查，可能为空
    - activity 无表，返回空数组
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from services import clock
from services.notices import CHANNEL_UNKNOWN, render_notice
from services.queries import (
    _combine,
    _connection,
    _latest_confirmed_plan,
    _load_user,
    _normalize_time,
    _parse_iso,
    _plan_items,
)

# 通知状态 → C 页面显示的标签（C 的 STATUS 表里没有这几个英文值）
NOTICE_STATUS_LABELS = {
    "parsed": "已解析",
    "needs_clarification": "待确认",
    "applied": "已应用",
    "duplicate": "重复",
    "ignored": "已忽略",
}

# 保留原因 → 给用户看的中文
KEPT_REASON_LABELS = {
    "task_done": "已完成任务",
    "locked": "锁定时间块",
    "started": "已开始时间块",
    "fixed_event": "固定课表/考试",
    "not_in_scope": "不属于重排范围",
    "unchanged": "未受影响",
}


def _iso_or_none(value: Any) -> str | None:
    return _normalize_time(value) if isinstance(value, str) else None


def _blocks_text(blocks: list[dict]) -> str:
    if not blocks:
        return "—"
    parts = []
    for block in blocks:
        start = _parse_iso(block.get("start_at"))
        end = _parse_iso(block.get("end_at"))
        if start and end:
            parts.append(f"{start:%m-%d %H:%M}–{end:%H:%M}")
        elif start:
            parts.append(f"{start:%m-%d %H:%M}")
    return "、".join(parts) or "—"


def capacity_of(preview_row: dict) -> dict:
    """把排程/预览的缺口翻译成 C 的 capacity 三项。"""
    summary = preview_row.get("summary") or {}
    required = int(summary.get("total_requested_minutes") or 0)
    kept = int(summary.get("scheduled_required_minutes") or 0) + int(
        summary.get("scheduled_optional_minutes") or 0
    )
    unplaced = max(0, required - kept)
    return {
        "required_minutes": required,
        "kept_minutes": kept,
        "unplaced_minutes": unplaced,
        "basis": (
            "基于当前正式计划中的可移动时间块计算；没有寻找计划之外的空档，"
            "也不是全局最优排程。"
        ),
    }


def to_client_preview(detail: dict) -> dict:
    """把 B 的预览详情转成 C 的 action（pending / preview）结构。"""
    exam = detail.get("exam") or {}
    proposed = detail.get("proposed_exam") or {}
    diff = detail.get("diff") or {}

    old_start = proposed.get("old_start_at") or exam.get("start_at")
    new_start = proposed.get("new_start_at") or exam.get("start_at")

    changes: list[dict] = []
    if proposed:
        changes.append(
            {"label": "考试时间", "before": old_start, "after": new_start}
        )
    for item in diff.get("changed", []):
        changes.append(
            {
                "label": f"学习块调整：{item.get('title', '')}",
                "before": _blocks_text(item.get("before", [])),
                "after": _blocks_text(item.get("after", [])),
            }
        )
    for item in diff.get("removed", []):
        changes.append(
            {
                "label": f"移出：{item.get('title', '')}",
                "before": _blocks_text(item.get("blocks", [])),
                "after": "不再安排",
            }
        )
    for item in diff.get("added", []):
        changes.append(
            {
                "label": f"新增：{item.get('title', '')}",
                "before": "—",
                "after": _blocks_text(item.get("blocks", [])),
            }
        )

    preserved = [
        KEPT_REASON_LABELS.get(item.get("reason"), item.get("reason"))
        for item in diff.get("kept", [])
        if item.get("reason") in KEPT_REASON_LABELS
    ]
    # 去重但保持顺序
    preserved = list(dict.fromkeys(preserved))

    status = detail.get("status")
    preview = {
        "id": detail.get("preview_id"),
        "confirm_code": detail.get("confirm_code"),
        "kind": "exam_change",
        "event_id": exam.get("id") or proposed.get("exam_id"),
        "expected_version": detail.get("base_version"),
        "event_version": proposed.get("exam_version"),
        "old": old_start or "",
        "new": new_start or "",
        "status": "applied" if status == "confirmed" else status,
        "expires_at": detail.get("expires_at"),
        "source_notice_ids": (
            [proposed["source_notice_id"]] if proposed.get("source_notice_id") else []
        ),
        "changes": changes,
        "preserved": preserved,
        "capacity": capacity_of(detail),
    }
    # 提醒排期还没做：没有真实 remind_at 时就不输出这个键，
    # 避免 C 的客户端把它当成日期去解析。
    return preview


def _row_to_detail(row: sqlite3.Row) -> dict:
    capacity = json.loads(row["capacity_json"] or "{}")
    return {
        "preview_id": row["id"],
        "confirm_code": row["confirm_code"],
        "status": row["status"],
        "base_version": row["base_version"],
        "expires_at": row["expires_at"],
        "exam": capacity.get("exam"),
        "proposed_exam": capacity.get("proposed_exam"),
        "summary": capacity.get("summary"),
        "diff": json.loads(row["diff_json"] or "{}"),
    }


def build_workspace(user_id: str, *, conn: sqlite3.Connection | None = None) -> dict:
    """组装 C 需要的 workspace（只读）。"""
    now = clock.get_now()
    today = now.date()

    with _connection(conn) as db:
        user = _load_user(db, user_id)
        plan = _latest_confirmed_plan(db, user_id)
        plan_items = _plan_items(db, user_id, plan["id"]) if plan else []

        goal_rows = db.execute(
            """
            SELECT id, exam_id, title, daily_limit_minutes, status, version
            FROM goals WHERE user_id = ? ORDER BY created_at
            """,
            (user_id,),
        ).fetchall()
        goal_by_id = {row["id"]: row for row in goal_rows}

        exam_rows = db.execute(
            """
            SELECT e.id, e.course_id, c.name AS course_name, e.name, e.exam_date,
                   e.start_time, e.end_time, e.location, e.status, e.version,
                   e.source_notice_id
            FROM exams e
            LEFT JOIN courses c ON c.id = e.course_id AND c.user_id = e.user_id
            WHERE e.user_id = ? AND e.status <> 'cancelled'
            ORDER BY e.exam_date, COALESCE(e.start_time, '00:00')
            """,
            (user_id,),
        ).fetchall()

        session_rows = db.execute(
            """
            SELECT s.id, s.course_id, c.name AS course_name, s.session_date,
                   s.start_time, s.end_time, s.location, s.status
            FROM class_sessions s
            JOIN courses c ON c.id = s.course_id AND c.user_id = s.user_id
            WHERE s.user_id = ? AND s.status <> 'cancelled'
            ORDER BY s.session_date, s.start_time
            """,
            (user_id,),
        ).fetchall()

        task_rows = db.execute(
            """
            SELECT id, goal_id, course_id, title, detail, due_at, estimated_minutes,
                   remaining_minutes, priority, is_optional, splittable, status,
                   created_at, updated_at
            FROM tasks WHERE user_id = ?
            ORDER BY due_at IS NULL, due_at, priority, title
            """,
            (user_id,),
        ).fetchall()

        notice_rows = db.execute(
            """
            SELECT * FROM notifications WHERE user_id = ?
            ORDER BY received_at DESC LIMIT 50
            """,
            (user_id,),
        ).fetchall()

        reminder_rows = db.execute(
            """
            SELECT r.id, r.remind_at, r.status, pi.plan_id
            FROM reminders r
            LEFT JOIN plan_items pi ON pi.id = r.plan_item_id
            WHERE r.user_id = ?
            ORDER BY r.remind_at
            """,
            (user_id,),
        ).fetchall()

        preview_rows = db.execute(
            """
            SELECT id, confirm_code, status, base_version, expires_at,
                   diff_json, capacity_json
            FROM previews WHERE user_id = ? AND status = 'pending'
            ORDER BY created_at
            """,
            (user_id,),
        ).fetchall()

    # ---- 映射 ----
    events = []
    exam_by_id = {}
    for exam in exam_rows:
        start_at = _combine(exam["exam_date"], exam["start_time"] or "00:00")
        event = {
            "id": exam["id"],
            "title": exam["name"],
            "start_at": start_at.isoformat() if start_at else None,
            "location": exam["location"] or "",
            "version": int(exam["version"]),
            # 数据库没有 source 列；C 要求 str，给空串而不是 null
            "source": "",
            "source_notice_id": exam["source_notice_id"],
        }
        events.append(event)
        exam_by_id[exam["id"]] = event

    classes = []
    for session in session_rows:
        start_at = _combine(session["session_date"], session["start_time"])
        end_at = _combine(session["session_date"], session["end_time"])
        classes.append(
            {
                "id": session["id"],
                "course": session["course_name"],
                "start_at": start_at.isoformat() if start_at else None,
                "end_at": end_at.isoformat() if end_at else None,
                "location": session["location"] or "",
            }
        )

    tasks = []
    for task in task_rows:
        estimated = task["estimated_minutes"]
        remaining = task["remaining_minutes"]
        actual = 0
        if isinstance(estimated, int) and isinstance(remaining, int):
            actual = max(0, estimated - remaining)
        tasks.append(
            {
                "id": task["id"],
                "goal_id": task["goal_id"] or "",
                "title": task["title"],
                "remaining_minutes": remaining or 0,
                "status": task["status"],
                # tasks 表没有 version 列；C 的客户端要求 int，这里用 1 占位
                "version": 1,
                "actual_minutes": actual,  # 由「预计 - 剩余」推导
                "feedback": task["detail"] or "",
                "required": not bool(task["is_optional"]),
                "included": True,          # 没有"移出范围"的字段
            }
        )

    study_blocks = [
        {
            "id": item["id"],
            "task_id": item["task_id"],
            "plan_id": item["plan_id"],
            "plan_version": int(plan["version"]) if plan else 0,
            "start_at": item["start_at"],
            "end_at": item["end_at"],
            "status": "confirmed",
            "locked": bool(item["is_locked"]),
        }
        for item in plan_items
        if item.get("task_id")
    ]

    goals = []
    for goal in goal_rows:
        goals.append(
            {
                "id": goal["id"],
                "title": goal["title"],
                "event_id": goal["exam_id"],
                "daily_max_minutes": int(goal["daily_limit_minutes"]),
                "scope": "",          # 没有字段
                "availability": "",    # 没有字段（T11）
                "status": goal["status"],
                "version": int(goal["version"]),
            }
        )

    plans = []
    if plan is not None:
        goal = goal_by_id.get(plan["goal_id"])
        exam = exam_by_id.get(goal["exam_id"]) if goal else None
        plans.append(
            {
                "id": plan["id"],
                "goal_id": plan["goal_id"],
                "version": int(plan["version"]),
                "status": plan["status"],
                # 计划跟着考试版本失效：用考试当前 version 代替
                "event_version": exam["version"] if exam else 0,
                "source_notice_ids": [],
                "summary": plan["note"] or "",
            }
        )

    notices = [render_notice(row) for row in notice_rows]

    reminders = []
    for row in reminder_rows:
        reminders.append(
            {
                "id": row["id"],
                "event_id": "",     # 没有直接字段；C 要求 str，用空串
                "at": _normalize_time(row["remind_at"]),
                "status": row["status"],
            }
        )

    pending = []
    for row in preview_rows:
        expires = _parse_iso(row["expires_at"])
        if expires is not None and expires <= now:
            continue
        pending.append(to_client_preview(_row_to_detail(row)))

    return {
        "version": int(plan["version"]) if plan else 0,
        "profile": {"display_name": user["name"], "timezone": user["timezone"]},
        "goals": goals,
        "events": events,
        "classes": classes,
        "tasks": tasks,
        "study_blocks": study_blocks,
        "plans": plans,
        "reminders": reminders,
        "notices": notices,
        "pending": pending,
        "activity": [],
        "availability": [],
        "preferences": {},
        "schema_version": 2,
        "generated_at": now.isoformat(),
        "time_mode": clock.get_mode(),
    }
