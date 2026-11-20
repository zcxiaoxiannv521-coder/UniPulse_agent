"""数据库连接工具。

所有模块（services / api / tests）都通过这里拿数据库连接，
不要在别处直接写 sqlite3.connect(...)，方便以后统一换数据库。

用法：
    from database.connection import get_connection, init_db

    conn = get_connection()      # 拿一个连接
    rows = conn.execute("SELECT * FROM tasks").fetchall()
    conn.close()
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# 项目根目录 = 本文件所在目录(database) 的上一级
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "unipulse.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_connection(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    """返回一个 SQLite 连接。

    - row_factory = sqlite3.Row：查询结果可以像字典一样用 row["name"] 取值
    - 打开外键约束，保证 plan_items 不会挂在不存在的 plan 上
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path = DB_PATH) -> Path:
    """执行 schema.sql，建好所有表（已存在则跳过）。返回数据库文件路径。"""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    finally:
        conn.close()
    return Path(db_path)


if __name__ == "__main__":
    # 直接运行本文件即可建库：python -m database.connection
    path = init_db()
    print(f"✅ 数据库已初始化：{path}")
