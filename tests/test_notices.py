"""services/notices.py 的测试：保存、校验、用户隔离、只读副作用。"""

from __future__ import annotations

import pytest

from database.connection import get_connection, init_db
from demo import seed as seed_module
from services import clock, notices


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T09:00:00+08:00")

    path = tmp_path / "notices.db"
    init_db(path)
    connection = get_connection(path)
    seed_module.seed(connection)
    yield connection
    connection.close()


def test_save_notice(conn):
    saved = notices.save_notice(
        seed_module.DEMO_USER_ID, "  教务通知：考试调整至 12 月 2 日  ", conn=conn
    )

    assert saved["id"].startswith("nt_")
    assert saved["text"] == "教务通知：考试调整至 12 月 2 日"   # 去掉首尾空白
    assert saved["status"] == "待解析"
    assert saved["channel"] == notices.CHANNEL_UNKNOWN
    assert saved["received_at"].endswith("+08:00")
    assert saved["message"]

    row = conn.execute(
        "SELECT * FROM notifications WHERE id = ?", (saved["id"],)
    ).fetchone()
    assert row["user_id"] == seed_module.DEMO_USER_ID
    assert row["raw_text"] == saved["text"]
    assert row["status"] == "parsed"


def test_empty_notice_rejected(conn):
    with pytest.raises(notices.NoticeError) as excinfo:
        notices.save_notice(seed_module.DEMO_USER_ID, "   \n  ", conn=conn)
    assert excinfo.value.code == "EMPTY_NOTICE"


def test_too_long_notice_rejected(conn):
    with pytest.raises(notices.NoticeError) as excinfo:
        notices.save_notice(
            seed_module.DEMO_USER_ID, "通" * (notices.MAX_NOTICE_LENGTH + 1), conn=conn
        )
    assert excinfo.value.code == "NOTICE_TOO_LONG"


def test_notice_is_user_scoped(conn):
    saved = notices.save_notice(seed_module.DEMO_USER_ID, "A 的通知", conn=conn)
    with conn:
        conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")

    assert notices.get_notice("us_other", saved["id"], conn=conn) is None
    assert notices.get_notice(seed_module.DEMO_USER_ID, saved["id"], conn=conn)


def test_assert_notices_owned(conn):
    saved = notices.save_notice(seed_module.DEMO_USER_ID, "A 的通知", conn=conn)
    with conn:
        conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")

    assert notices.assert_notices_owned(
        seed_module.DEMO_USER_ID, [saved["id"]], conn=conn
    ) == [saved["id"]]

    with pytest.raises(notices.NoticeError) as excinfo:
        notices.assert_notices_owned("us_other", [saved["id"]], conn=conn)
    assert excinfo.value.code == "NOTICE_NOT_FOUND"


def test_save_does_not_touch_exam_or_plan(conn):
    def snapshot():
        return (
            [tuple(r) for r in conn.execute("SELECT * FROM exams")],
            [tuple(r) for r in conn.execute("SELECT * FROM plans")],
            [tuple(r) for r in conn.execute("SELECT * FROM plan_items")],
        )

    before = snapshot()
    notices.save_notice(seed_module.DEMO_USER_ID, "任意通知原文", conn=conn)
    after = snapshot()

    assert before == after
