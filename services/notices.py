"""通知保存（只存原文，不解析、不改考试、不生成预览）。

安全边界：
    通知原文属于不可信外部数据，只作为普通字符串存进 `notifications.raw_text`。
    不执行其中任何文字，也不据此自动改考试或计划。

与 C 的约定：
    C 的 `ApiClient.save_notice(text, expected_version)` 发
        POST /v1/notices  {"text": "...", "expected_version": 1}
    `expected_version` 只是演示端的并发标记；通知是追加写入，不影响计划版本，
    这里接收但不做冲突校验（见 docs/API_CONTRACT.md）。
"""

from __future__ import annotations

import secrets
import sqlite3

from services import clock
from services.queries import _connection, _load_user, _normalize_time

# 与 C 的输入限制保持一致（web/client.py 里也是 20000）
MAX_NOTICE_LENGTH = 20000

# 数据库没有 channel 列，这是**展示用默认值**，不代表真实来源
CHANNEL_UNKNOWN = "未标注"

NOTICE_STATUS_LABELS = {
    "parsed": "已解析",
    "needs_clarification": "待确认",
    "applied": "已应用",
    "duplicate": "重复",
    "ignored": "已忽略",
}


class NoticeError(Exception):
    """通知保存/校验失败。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def render_notice(row: sqlite3.Row) -> dict:
    """把 notifications 行转成 C 需要的结构。"""
    # 复用 parsed 状态表示"已入库"：parse_json 为空说明还没被解析
    if row["status"] == "parsed" and not row["parse_json"]:
        label = "待解析"
    else:
        label = NOTICE_STATUS_LABELS.get(row["status"], row["status"])

    return {
        "id": row["id"],
        "text": row["raw_text"],
        "status": label,
        "channel": CHANNEL_UNKNOWN,
        "received_at": _normalize_time(row["received_at"]),
        "published_at": _normalize_time(row["published_at"]),
    }


def get_notice(
    user_id: str, notice_id: str, *, conn: sqlite3.Connection | None = None
) -> dict | None:
    """按用户读取一条通知；不属于该用户时返回 None。"""
    with _connection(conn) as db:
        row = db.execute(
            "SELECT * FROM notifications WHERE id = ? AND user_id = ?",
            (notice_id, user_id),
        ).fetchone()
    return render_notice(row) if row else None


def assert_notices_owned(
    user_id: str,
    notice_ids: list[str],
    *,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """校验这些通知都属于该用户；有任何一个不属于就报错。"""
    ids = [notice_id for notice_id in dict.fromkeys(notice_ids) if notice_id]
    if not ids:
        return []

    with _connection(conn) as db:
        placeholders = ", ".join("?" for _ in ids)
        rows = db.execute(
            f"SELECT id FROM notifications WHERE user_id = ? AND id IN ({placeholders})",
            (user_id, *ids),
        ).fetchall()

    owned = {row["id"] for row in rows}
    missing = [notice_id for notice_id in ids if notice_id not in owned]
    if missing:
        raise NoticeError(
            "NOTICE_NOT_FOUND",
            f"通知不存在或不属于当前用户：{', '.join(missing)}",
        )
    return ids


def save_notice(
    user_id: str,
    text: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """保存一条通知原文，返回 C 需要的通知信息。"""
    if not isinstance(text, str):
        raise NoticeError("EMPTY_NOTICE", "通知原文必须是文本")

    raw = text.strip()
    if not raw:
        raise NoticeError("EMPTY_NOTICE", "通知原文不能为空")
    if len(raw) > MAX_NOTICE_LENGTH:
        raise NoticeError(
            "NOTICE_TOO_LONG", f"通知原文不能超过 {MAX_NOTICE_LENGTH} 字"
        )

    now = clock.get_now()
    notice_id = f"nt_{secrets.token_hex(6)}"

    with _connection(conn) as db:
        try:
            _load_user(db, user_id)
        except Exception as exc:                      # queries.UserNotFoundError
            raise NoticeError("USER_NOT_FOUND", f"用户 {user_id!r} 不存在") from exc

        with db:
            db.execute(
                """
                INSERT INTO notifications (id, user_id, wecom_msg_id, raw_text,
                                           received_at, published_at, parse_json, status)
                VALUES (?, ?, NULL, ?, ?, NULL, NULL, 'parsed')
                """,
                (notice_id, user_id, raw, now.isoformat()),
            )
        row = db.execute(
            "SELECT * FROM notifications WHERE id = ? AND user_id = ?",
            (notice_id, user_id),
        ).fetchone()

    rendered = render_notice(row)
    rendered["message"] = "原文已保存，等待后续处理。"
    return rendered
