# UniPulse 数据结构说明（DATA_SCHEMA）

> **版本：** v0.2（草案，待团队确认）
> **维护人：** 成员 B（数据与执行）
> **对应建表脚本：** `database/schema.sql`
> **数据库：** SQLite 3（Python 自带 `sqlite3`，零安装）
> **日期：** 2026-11-20

---

## 1. 为什么这样设计

V4 方案第 3.3 节列出四类必须保存的数据，本文件逐条对应：

| V4 要求保存的内容 | 落在哪些表 |
|---|---|
| 用户与渠道：内部用户标识、企微绑定、时区、确认过的偏好及作用范围 | `users`、`user_preferences` |
| 通知与事件：原文证据、接收时间、发布时间、所属课程、具体事件时间和版本 | `notifications`、`courses`、`class_sessions`、`exams` |
| 目标、待办与时间块：考试关联、范围、剩余估时、进度；区分「要做什么」和「什么时候做」 | `goals`、`tasks`、`plan_items` |
| 计划、预览与运行记录：当前生效版本、修改前后差异、依赖版本、确认有效期；调用摘要、消息去重、提醒状态 | `plans`、`previews`、`reminders`（去重靠 `notifications.wecom_msg_id` 唯一约束） |

### 三条贯穿全库的规则

1. **用户隔离**：每张业务表都有 `user_id`，查询必须带它。没有「公共表」。
2. **考试日期单一来源**：日期只在 `exams.exam_date`。其他表通过 `exam_id` 引用，不复制日期，避免两处不同步。
3. **草稿与正式分离**：`previews` 存「打算怎么改」，`plans` 存「已经生效」。用户确认前只动 `previews`。

---

## 2. 表关系（文字版）

```
users ─┬─ user_preferences
       ├─ courses ─┬─ class_sessions
       │           ├─ exams
       │           └─ tasks
       ├─ exams ──── goals ─┬─ tasks
       │                    └─ plans ─── plan_items
       ├─ previews（草稿，引用 goal / base plan）
       ├─ notifications（原文证据）
       └─ reminders ─── plan_items
```

读法：
- 一个用户有多门课（`courses`），每门课在具体日期上有多次上课（`class_sessions`）
- 一个用户有若干考试（`exams`），每场考试可以有**一个**学习目标（`goals`，首版只做单门考试）
- 一个目标下有若干任务（`tasks`），任务被排进计划的若干时间块（`plan_items`）
- 一份计划（`plans`）属于一个目标，包含多个时间块
- 预览（`previews`）记录「基于哪一版计划、准备改什么」，确认后才会产生新版本

---

## 3. 表定义

字段类型说明：SQLite 是弱类型，`TEXT` 存字符串、`INTEGER` 存整数、`0/1` 表示布尔。

### 3.1 `users` 用户

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | TEXT | 主键 | 内部 ID，`us_` + 12 位十六进制 |
| `name` | TEXT | 非空 | 显示名 |
| `wecom_userid` | TEXT | 唯一，可空 | 企微 UserID，A 接入后回填 |
| `timezone` | TEXT | 默认 `Asia/Shanghai` | 统一时区 |
| `created_at` | TEXT | 默认当前时间 | 创建时间 |

为什么内部 ID 和企微 UserID 分开：换渠道（网页、以后的小程序）时不用改主键，也避免把企微 ID 暴露在网页链接里。

### 3.2 `user_preferences` 用户偏好

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 主键，`up_` 前缀 |
| `user_id` | TEXT | 外键 → `users.id` |
| `key` | TEXT | 例如 `daily_limit_minutes`、`study_window` |
| `value_json` | TEXT | JSON 字符串，可存数字、数组、对象 |
| `scope` | TEXT | `global` 或 `goal:<goal_id>`，表示这条偏好对谁生效 |
| `confirmed_at` | TEXT | 用户明确确认的时间（V4 要求只存「明确确认过的」） |

`scope` 是 V4「偏好及作用范围」的直接实现：同一个用户，对某一场考试可以设「每天 90 分钟」，对另一场设「每天 60 分钟」。

**当前用到的 key（2026-11-25）：**

| key | scope | 含义 |
|---|---|---|
| `daily_limit_minutes` | `goal:<id>` | 每日学习上限（演示数据用） |
| `goal_scope` | `goal:<id>` | 用户在网页填写的「复习范围」 |
| `goal_availability` | `goal:<id>` | 用户填写的「允许时段与约束」 |

`goals` 表没有这两列，所以放在偏好表里，避免为两个文本字段改表。

### 3.3 `courses` 课程档案

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 主键，`cr_` 前缀 |
| `user_id` | TEXT | 外键 → `users.id` |
| `name` | TEXT | 课程名，如「机器学习」 |
| `teacher` | TEXT | 任课教师，可空 |
| `location` | TEXT | 默认上课地点，可空 |
| `created_at` | TEXT | — |

一门课只建一条，不存「星期几」，因为具体时间在 `class_sessions`。

### 3.4 `class_sessions` 某一次上课

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 主键，`cs_` 前缀 |
| `user_id` | TEXT | 外键 → `users.id` |
| `course_id` | TEXT | 外键 → `courses.id` |
| `session_date` | TEXT | `YYYY-MM-DD`，具体日期 |
| `start_time` | TEXT | `HH:MM` |
| `end_time` | TEXT | `HH:MM` |
| `location` | TEXT | 本次地点（可与课程默认地点不同） |
| `status` | TEXT | `normal` / `moved` / `cancelled` |
| `source_notice_id` | TEXT | 哪条通知造成的变更，可空 |
| `version` | INTEGER | 每次改动 +1 |
| `created_at` / `updated_at` | TEXT | — |

**关键设计：** 「12 月 1 日 14:00 的机器学习课调到 12 月 2 日 10:00」只改这一条记录，其他周的课不受影响。这直接对应 V4「不把某一次调课改成每周调课」。

### 3.5 `exams` 考试

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 主键，`ex_` 前缀 |
| `user_id` | TEXT | 外键 |
| `course_id` | TEXT | 可空（四六级等非课程考试） |
| `name` | TEXT | 考试名 |
| `exam_date` | TEXT | `YYYY-MM-DD`，**全系统唯一来源** |
| `start_time` / `end_time` | TEXT | `HH:MM` |
| `location` | TEXT | 可空 |
| `status` | TEXT | `scheduled` / `moved` / `cancelled` |
| `source_notice_id` | TEXT | 来源通知 |
| `version` | INTEGER | 每次改动 +1 |

### 3.6 `goals` 学习目标

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 主键，`gl_` 前缀 |
| `user_id` | TEXT | 外键 |
| `exam_id` | TEXT | 外键 → `exams.id`，首版一门考试一个目标 |
| `title` | TEXT | 如「准备机器学习期末考试」 |
| `daily_limit_minutes` | INTEGER | 每日学习上限，默认 90 |
| `status` | TEXT | `active` / `paused` / `completed` / `abandoned` |
| `version` | INTEGER | — |

V4 说「初始目标、调整草稿和当前正式计划分开」：目标本身只有一条 `active`，它的**调整**通过 `previews` 走，不直接改 `goals`。

### 3.7 `tasks` 待办

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 主键，`tk_` 前缀 |
| `user_id` | TEXT | 外键 |
| `goal_id` | TEXT | 可空，关联学习目标 |
| `course_id` | TEXT | 可空 |
| `title` | TEXT | 「复习第三章」 |
| `detail` | TEXT | 具体要求，保留作业原文要点 |
| `due_at` | TEXT | 截止时间 ISO 格式，可空 |
| `estimated_minutes` | INTEGER | 预计总耗时 |
| `remaining_minutes` | INTEGER | 剩余耗时（部分完成后更新） |
| `priority` | INTEGER | 1 高 / 2 中 / 3 低 |
| `is_optional` | INTEGER | 1 = 选做，时间不足可移出 |
| `splittable` | INTEGER | 1 = 允许拆块 |
| `status` | TEXT | `todo` / `doing` / `done` |
| `source` | TEXT | `manual` / `wecom` |

**`estimated_minutes` 与 `remaining_minutes` 分开的理由：** V4 明确「学 30 分钟」不等于「完成了 30 分钟工作量」。只报时长时只记时间，不改剩余工作量；只有学生说「做了 8/20 题」这类明确进度，才扣减 `remaining_minutes`。

### 3.7.1 `task_logs` 任务日志（2026-11-24 新增）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 主键，`tl_` 前缀 |
| `user_id` | TEXT | 外键 → `users.id`，用于隔离 |
| `task_id` | TEXT | 外键 → `tasks.id` |
| `kind` | TEXT | `time` 只记用时 / `progress` 进度反馈 |
| `minutes` | INTEGER | **本次增量**（不是累计值），累计用时 = `SUM(minutes)` |
| `feedback` | TEXT | 进度说明，仅 `progress` 使用 |
| `created_at` | TEXT | 写入时间 |

**为什么新增这张表：** `tasks` 只有 `estimated_minutes` / `remaining_minutes`，
没有"实际用时"和"反馈"字段。把学习时长写进 `remaining_minutes` 会把"投入时间"
误当成"完成进度"；改 `estimated_minutes` 又会破坏原计划。

**迁移影响：** 只做 `CREATE TABLE IF NOT EXISTS` + 索引，老库启动时由 `init_db()`
自动补齐，**不需要数据迁移**，也不改动任何既有列。

**幂等策略：** 客户端上报的是"累计值"，服务端用「新累计 − 已有 SUM」得到增量：
增量为 0 不写新行；为负则拒绝（历史记录不可抹掉）。

### 3.8 `plans` 计划

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 主键，`pl_` 前缀 |
| `user_id` | TEXT | 外键 |
| `goal_id` | TEXT | 可空 |
| `status` | TEXT | `draft` / `confirmed` / `superseded` |
| `version` | INTEGER | 从 1 开始，每确认一次 +1 |
| `based_on_plan_id` | TEXT | 上一版计划 ID |
| `note` | TEXT | 例如「计划待调整」 |
| `created_at` / `updated_at` | TEXT | — |

**不变式：** 同一个 `goal_id` 同时最多只有一条 `status='confirmed'`。旧的置为 `superseded`，不删除（保留历史，答辩时要展示「前后对比」）。

### 3.9 `plan_items` 时间块

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 主键，`pi_` 前缀 |
| `plan_id` | TEXT | 外键 → `plans.id`，级联删除 |
| `start_at` / `end_at` | TEXT | ISO 时间，含时区 |
| `title` | TEXT | 显示标题 |
| `item_type` | TEXT | `course` / `exam` / `task` / `rest` / `custom` |
| `task_id` / `session_id` / `exam_id` | TEXT | 可空，指向来源对象 |
| `is_locked` | INTEGER | 1 = 锁定，重排时不动 |

`item_type='course'` 的块在网页上通常标灰、不可拖动，因为它们来自真实课表。重排时靠 `is_locked` 和 `item_type` 双重保护。

### 3.10 `previews` 待确认草稿（核心表）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 主键，`pv_` 前缀 |
| `user_id` | TEXT | 外键 |
| `goal_id` | TEXT | 可空 |
| `kind` | TEXT | `initial_plan` / `reschedule` / `progress` / `class_change` / `capacity_choice` |
| `confirm_code` | TEXT | 人工确认编号，如 `T03` |
| `status` | TEXT | `pending` / `confirmed` / `cancelled` / `expired` |
| `base_plan_id` | TEXT | 基于哪份正式计划 |
| `base_version` | INTEGER | 基于第几版 |
| `summary` | TEXT | 给用户看的一句话摘要 |
| `diff_json` | TEXT | 变更差异（added/changed/removed/kept） |
| `capacity_json` | TEXT | 缺口信息（required/scheduled/gap/options） |
| `expires_at` | TEXT | 确认有效期 |
| `created_at` / `confirmed_at` | TEXT | — |

**这张表是「确认前不改正式数据」的技术保证。** 预览生成时只 INSERT 到这里；`confirm_preview` 才在事务里写正式表。

`diff_json` 结构：

```json
{
  "added":   [ { "object_type": "plan_item", "object_id": "pi_new1", "after": {} } ],
  "changed": [ { "object_type": "exam", "object_id": "ex_...", "before": {}, "after": {} } ],
  "removed": [ { "object_type": "plan_item", "object_id": "pi_old9", "before": {} } ],
  "kept":    [ { "object_type": "plan_item", "object_id": "pi_keep1", "reason": "必做，未受影响" } ]
}
```

网页要展示「前后差异」，直接消费这个 JSON，不需要另调接口。

### 3.11 `notifications` 通知原文

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 主键，`nt_` 前缀 |
| `user_id` | TEXT | 外键 |
| `wecom_msg_id` | TEXT | **唯一**，幂等去重靠它 |
| `raw_text` | TEXT | 原文，只当数据 |
| `received_at` | TEXT | 接收时间 |
| `published_at` | TEXT | 可得的发布时间，可空 |
| `parse_json` | TEXT | A 解析出的候选事项 |
| `status` | TEXT | `parsed` / `needs_clarification` / `applied` / `duplicate` / `ignored` |

`wecom_msg_id` 的唯一约束是「同一条消息再送达不重复建事项」的实现方式。

### 3.12 `reminders` 提醒队列

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT | 主键，`rm_` 前缀 |
| `user_id` | TEXT | 外键 |
| `plan_item_id` | TEXT | 外键，可空 |
| `remind_at` | TEXT | 提醒时间 |
| `status` | TEXT | `queued` / `sent` / `failed` / `cancelled` |
| `attempts` | INTEGER | 尝试次数 |
| `last_error` | TEXT | 失败原因 |

V4 要求「入队、发送失败、真实送达分别记录」，所以状态不能只用一个 `sent` 布尔值。首版提醒是 P1，表先建好，逻辑后补。

---

## 4. 枚举值汇总

| 枚举 | 取值 |
|---|---|
| `class_sessions.status` | `normal` / `moved` / `cancelled` |
| `exams.status` | `scheduled` / `moved` / `cancelled` |
| `goals.status` | `active` / `paused` / `completed` / `abandoned` |
| `tasks.status` | `todo` / `doing` / `done` |
| `tasks.priority` | 1 / 2 / 3 |
| `plans.status` | `draft` / `confirmed` / `superseded` |
| `plan_items.item_type` | `course` / `exam` / `task` / `rest` / `custom` |
| `previews.kind` | `initial_plan` / `reschedule` / `progress` / `class_change` / `capacity_choice` |
| `previews.status` | `pending` / `confirmed` / `cancelled` / `expired` |
| `notifications.status` | `parsed` / `needs_clarification` / `applied` / `duplicate` / `ignored` |
| `reminders.status` | `queued` / `sent` / `failed` / `cancelled` |

---

## 5. 关键约束（代码里也要检查）

数据库能挡一部分，业务逻辑还要再挡一层：

1. 同一 `goal_id` 最多一条 `confirmed` 计划
2. `plan_items` 时间不能重叠（同一计划内）
3. 学习块不能与 `class_sessions` 的课、`exams` 的考试重叠
4. 每天学习总分钟数 ≤ `goals.daily_limit_minutes`
5. 排程不越过 `exams.exam_date` 和 `tasks.due_at`
6. `previews.base_version` 必须等于当前正式计划的 `version` 才能确认
7. 确认后 `previews.status` 必须变为 `confirmed`，且 `confirmed_at` 非空

第 2–5 条在 `services/scheduler.py` 里算，第 6–7 条在 `services/confirm.py` 里校验。

---

## 6. 演示数据约定（12/6 答辩固定场景）

V4 第 7 节要求「一套可复现的数据」：

| 项目 | 值 |
|---|---|
| 固定「今天」 | 2026-11-30 上午 |
| 考试 | 机器学习期末考试，原定 2026-12-04 14:00 |
| 每日上限 | 90 分钟 |
| 空档 1 | 11-30 19:00–20:00（60 分钟）→ 复习第三章（必做） |
| 空档 2 | 12-01 19:00–20:30（90 分钟）→ 复习第四章（必做） |
| 空档 3 | 12-02 15:00–16:20（80 分钟）→ 附加模拟练习（选做） |
| 改期通知 | 「机器学习期末考试提前至 12 月 2 日 14:00，复习范围不变」 |
| 期望计算 | 剩余 230 分钟 → 能排 150 分钟 → 缺 80 分钟 |

**关键：** 空档 3 在新考试时间（12-02 14:00）之后，所以它排不进去。这三个数字必须由排程代码现场算出，不能出现在任何硬编码的回复里。

演示数据放 `demo/`，由 B 写一个 `seed.py` 一键重置；C 只读，不改数据。

---

## 7. 初始化与查看

```bash
cd unipulse

# 建库（已存在则跳过建表）
.venv/bin/python -m database.connection

# 查看有哪些表
sqlite3 data/unipulse.db ".tables"

# 查看某张表的结构
sqlite3 data/unipulse.db ".schema plans"
```

`data/unipulse.db` 已被 `.gitignore` 忽略，不进仓库；队友拉到代码后自己跑一次建库即可。

---

## 8. 待确认事项

| 问题 | 影响 | B 的默认建议 |
|---|---|---|
| 课表按「具体日期」还是「每周重复 + 例外」存 | 直接决定 `class_sessions` 设计与录入方式 | 用具体日期（首版只有一周数据，最简单，也最贴合演示） |
| 是否需要多用户同时在线 | 影响并发与版本控制复杂度 | 首版按单用户会话设计，但所有表已带 `user_id` |
| 是否需要保存每次工具调用摘要 | V4 提到「保留调用摘要」但未细化 | 首版只在日志里留 `request_id`，不建表 |
| 提醒功能是否进入 12/6 演示 | P1 项，影响工期 | 表先建，逻辑放最后；演示不承诺送达 |

---

## 9. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-11-20 | 初稿：6 张基础表 |
| v0.2 | 2026-11-20 | 按 V4 第 3.3 节扩展为 12 张表：新增 `class_sessions`（单次调课）、`goals`、`previews`、`notifications`、`reminders`、`user_preferences`；补充枚举、约束与演示数据 |
