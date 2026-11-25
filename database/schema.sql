-- ============================================================
-- UniPulse 数据库建表脚本
-- 版本：v0.2（依据 V4 方案 3.3「业务工具和数据」整理，待团队确认）
-- 数据库：SQLite（单文件 data/unipulse.db，便于演示与备份）
--
-- 三条硬规则（与 V4 一致）：
--   1. 每张业务表都有 user_id，任何查询都必须带 user_id 条件。
--   2. 考试日期只存在 exams 表，别的表只存 exam_id，不复制日期。
--   3. 草稿（previews）与正式计划（plans.status='confirmed'）分开，
--      用户确认前不修改正式数据。
-- ============================================================

-- ---------- 用户与偏好 ----------
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,            -- 内部用户 ID，如 us_9f2c1a7b3d4e
    name          TEXT NOT NULL,               -- 显示名
    wecom_userid  TEXT UNIQUE,                 -- 企微 UserID，未绑定时为 NULL
    timezone      TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 用户明确确认过的偏好，带作用范围（全局 or 某个目标）
CREATE TABLE IF NOT EXISTS user_preferences (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    key         TEXT NOT NULL,                 -- 如 daily_limit_minutes / study_window
    value_json  TEXT NOT NULL,                 -- JSON 字符串，便于存列表/对象
    scope       TEXT NOT NULL DEFAULT 'global',-- global 或 goal:<goal_id>
    confirmed_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ---------- 课表 ----------
-- 课程档案：一门课只建一条
CREATE TABLE IF NOT EXISTS courses (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    teacher     TEXT,
    location    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 具体某一次上课：调课/停课只改这一条，不动其他周
CREATE TABLE IF NOT EXISTS class_sessions (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    course_id        TEXT NOT NULL,
    session_date     TEXT NOT NULL,            -- YYYY-MM-DD
    start_time       TEXT NOT NULL,            -- HH:MM
    end_time         TEXT NOT NULL,            -- HH:MM
    location         TEXT,
    status           TEXT NOT NULL DEFAULT 'normal'
                     CHECK (status IN ('normal', 'moved', 'cancelled')),
    source_notice_id TEXT,                     -- 哪条通知导致的变更
    version          INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (course_id) REFERENCES courses(id)
);

-- ---------- 考试 ----------
-- 考试日期的唯一来源
CREATE TABLE IF NOT EXISTS exams (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    course_id        TEXT,
    name             TEXT NOT NULL,
    exam_date        TEXT NOT NULL,            -- YYYY-MM-DD
    start_time       TEXT,                     -- HH:MM
    end_time         TEXT,                     -- HH:MM
    location         TEXT,
    status           TEXT NOT NULL DEFAULT 'scheduled'
                     CHECK (status IN ('scheduled', 'moved', 'cancelled')),
    source_notice_id TEXT,
    version          INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (course_id) REFERENCES courses(id)
);

-- ---------- 目标、任务 ----------
-- 学习目标：首版是“一门考试 + 每天最多多少分钟”
CREATE TABLE IF NOT EXISTS goals (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    exam_id             TEXT NOT NULL,
    title               TEXT NOT NULL,
    daily_limit_minutes INTEGER NOT NULL DEFAULT 90,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'paused', 'completed', 'abandoned')),
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (exam_id) REFERENCES exams(id)
);

-- 待办：要做什么；排到哪个时间块由 plan_items 负责
CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    goal_id           TEXT,
    course_id         TEXT,
    title             TEXT NOT NULL,
    detail            TEXT,
    due_at            TEXT,                    -- ISO 时间，可空
    estimated_minutes INTEGER,                 -- 预计总耗时
    remaining_minutes INTEGER,                 -- 剩余耗时（部分完成后更新）
    priority          INTEGER NOT NULL DEFAULT 2 CHECK (priority IN (1, 2, 3)),
    is_optional       INTEGER NOT NULL DEFAULT 0,  -- 1=选做，时间不足时可移出
    splittable        INTEGER NOT NULL DEFAULT 1,  -- 1=允许拆成多个时间块
    status            TEXT NOT NULL DEFAULT 'todo'
                      CHECK (status IN ('todo', 'doing', 'done')),
    source            TEXT NOT NULL DEFAULT 'manual', -- manual / wecom
    created_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (goal_id) REFERENCES goals(id),
    FOREIGN KEY (course_id) REFERENCES courses(id)
);

-- 任务日志：实际用时（增量）与进度反馈备注
-- 设计原因：tasks 表没有"实际用时/反馈"字段，而"学习时长"不能塞进 remaining_minutes。
-- 累计用时 = SUM(minutes)；每条记录只存本次增量，避免覆盖历史。
CREATE TABLE IF NOT EXISTS task_logs (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    task_id    TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('time', 'progress')),
    minutes    INTEGER NOT NULL DEFAULT 0,     -- 本次新增的累计用时（增量，可为 0）
    feedback   TEXT,                           -- 进度反馈备注（仅 kind='progress'）
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- ---------- 计划与时间块 ----------
-- 计划：同一目标同一时间只有一条 confirmed（正式），其余为 superseded
CREATE TABLE IF NOT EXISTS plans (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    goal_id         TEXT,
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'confirmed', 'superseded')),
    version         INTEGER NOT NULL DEFAULT 1,
    based_on_plan_id TEXT,
    note            TEXT,                      -- 如“计划待调整”
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (goal_id) REFERENCES goals(id)
);

-- 时间块：计划里每一个具体时间段
CREATE TABLE IF NOT EXISTS plan_items (
    id          TEXT PRIMARY KEY,
    plan_id     TEXT NOT NULL,
    start_at    TEXT NOT NULL,                 -- ISO 时间，含时区
    end_at      TEXT NOT NULL,
    title       TEXT NOT NULL,
    item_type   TEXT NOT NULL
                CHECK (item_type IN ('course', 'exam', 'task', 'rest', 'custom')),
    task_id     TEXT,
    session_id  TEXT,
    exam_id     TEXT,
    is_locked   INTEGER NOT NULL DEFAULT 0,    -- 1=锁定，重排时不动
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (session_id) REFERENCES class_sessions(id),
    FOREIGN KEY (exam_id) REFERENCES exams(id)
);

-- ---------- 预览与确认 ----------
-- 草稿变更：确认前所有改动只写这里，不碰 plans / tasks / exams
CREATE TABLE IF NOT EXISTS previews (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    goal_id        TEXT,
    kind           TEXT NOT NULL
                   CHECK (kind IN ('initial_plan', 'reschedule', 'progress', 'class_change', 'capacity_choice')),
    confirm_code   TEXT NOT NULL,              -- 人工确认编号，如 T03
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'confirmed', 'cancelled', 'expired')),
    base_plan_id   TEXT,                       -- 预览基于哪个正式计划
    base_version   INTEGER NOT NULL DEFAULT 0, -- 预览基于第几版
    summary        TEXT NOT NULL,              -- 给人看的一句话摘要
    diff_json      TEXT NOT NULL,              -- 变更前后差异（JSON）
    capacity_json  TEXT,                       -- 排程缺口信息（JSON）
    expires_at     TEXT NOT NULL,              -- 确认有效期，过期作废
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    confirmed_at   TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (goal_id) REFERENCES goals(id)
);

-- ---------- 通知与提醒 ----------
-- 通知原文证据：用于去重、回溯“这条安排从哪来”
CREATE TABLE IF NOT EXISTS notifications (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    wecom_msg_id  TEXT UNIQUE,                 -- 企微消息 ID，用于幂等去重
    raw_text      TEXT NOT NULL,               -- 原文，只当数据，不当指令
    received_at   TEXT NOT NULL,
    published_at  TEXT,
    parse_json    TEXT,                        -- A 解析出的候选事项
    status        TEXT NOT NULL DEFAULT 'parsed'
                  CHECK (status IN ('parsed', 'needs_clarification', 'applied', 'duplicate', 'ignored')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 提醒队列：计划变化时同步失效旧提醒
CREATE TABLE IF NOT EXISTS reminders (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    plan_item_id TEXT,
    remind_at    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'sent', 'failed', 'cancelled')),
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (plan_item_id) REFERENCES plan_items(id) ON DELETE CASCADE
);

-- ---------- 索引 ----------
CREATE INDEX IF NOT EXISTS idx_pref_user        ON user_preferences(user_id, scope);
CREATE INDEX IF NOT EXISTS idx_courses_user     ON courses(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user    ON class_sessions(user_id, session_date);
CREATE INDEX IF NOT EXISTS idx_exams_user       ON exams(user_id, exam_date);
CREATE INDEX IF NOT EXISTS idx_goals_user       ON goals(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_user       ON tasks(user_id, due_at);
CREATE INDEX IF NOT EXISTS idx_task_logs_task   ON task_logs(user_id, task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_plans_user       ON plans(user_id, goal_id, status);
CREATE INDEX IF NOT EXISTS idx_plan_items_plan  ON plan_items(plan_id);
CREATE INDEX IF NOT EXISTS idx_plan_items_task  ON plan_items(task_id);
CREATE INDEX IF NOT EXISTS idx_previews_user    ON previews(user_id, status);
CREATE INDEX IF NOT EXISTS idx_previews_code    ON previews(user_id, confirm_code);
CREATE INDEX IF NOT EXISTS idx_notes_user       ON notifications(user_id, status);
CREATE INDEX IF NOT EXISTS idx_reminders_due    ON reminders(status, remind_at);
