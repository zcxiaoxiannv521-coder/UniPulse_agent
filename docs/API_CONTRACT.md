# UniPulse 接口契约（API_CONTRACT）

> **版本：** v0.1（草案，待 A / C 确认）
> **维护人：** 成员 B（数据与执行）
> **依据：** 《UniPulse 校园智能助手 Agent 开发方案 V4》第 2、3、4 节
> **状态：** 本文件是联调依据，**不是最终定稿**。标了 `⚠️待确认` 的条目需要三方开会拍板。
> **日期：** 2026-11-20

---

## 0. 一句话说明

A（企微 + Agent）在**同一个 Python 进程里**调用 B 写的业务函数；C（网页看板）通过 **HTTP JSON 接口**读同一份数据；三者之间的数据格式，就是本文第 3 节定义的几个对象。

```
                  ┌──────────────────────────────┐
  企微私聊 ──────►│ A：企微接入 + Agent 选工具      │
                  │ 直接 import services 里的函数  │──┐
                  └──────────────────────────────┘  │
                                                     ▼
                                        ┌────────────────────────┐
  手机网页 ◄──── HTTP JSON ────────────►│ B：services + SQLite    │
  （Streamlit，C 负责）                  └────────────────────────┘
```

**为什么 A 不走 HTTP：** A 和 B 在同一个仓库、同一个进程里，`from services import ...` 直接调用最简单，也符合 V4「一个后端、一个 Agent」的设定。B 另外提供一层很薄的 HTTP 接口，是为了 C 的 Streamlit 页面和手机访问。

---

## 1. 三条链路

| 链路 | 谁调谁 | 通信方式 | 本文位置 |
|---|---|---|---|
| 链路 1 | A → B | Python 函数（同进程） | 第 4 节 |
| 链路 2 | C → B | HTTP + JSON | 第 5 节 |
| 链路 3 | B → A（回调） | 无。B 只返回结果，由 A 组织话术 | — |

B **不主动推送**企微消息，也**不生成**面向用户的自然语言。B 只返回结构化数据（摘要句、缺口数字、追问字段），措辞由 A 决定。

---

## 2. 通用约定

### 2.1 时间与日期

| 类型 | 格式 | 示例 |
|---|---|---|
| 日期 | `YYYY-MM-DD` | `2026-12-02` |
| 日期时间 | ISO 8601，**必须带时区** | `2026-12-02T14:00:00+08:00` |
| 钟点（课表） | `HH:MM` | `14:00` |
| 时区 | 全系统统一 `Asia/Shanghai`，数据库存本地时间字符串 | — |

⚠️待确认：A 收到企微消息的时间是 UTC 还是本地？B 建议 A 统一转成 `+08:00` 再传。

### 2.2 ID 规则

所有 ID 都是**带前缀的字符串**，方便日志和演示时一眼看出类型：

| 前缀 | 对象 | 示例 |
|---|---|---|
| `us_` | 用户 | `us_9f2c1a7b3d4e` |
| `cr_` | 课程 | `cr_1a2b3c4d5e6f` |
| `cs_` | 某一次上课 | `cs_7f8e9d0c1b2a` |
| `ex_` | 考试 | `ex_3c4d5e6f7a8b` |
| `gl_` | 目标 | `gl_5e6f7a8b9c0d` |
| `tk_` | 任务 | `tk_9c0d1e2f3a4b` |
| `pl_` | 计划 | `pl_2f3a4b5c6d7e` |
| `pi_` | 计划时间块 | `pi_0a1b2c3d4e5f` |
| `pv_` | 预览（草稿） | `pv_4b5c6d7e8f9a` |
| `nt_` | 通知 | `nt_6d7e8f9a0b1c` |
| `rm_` | 提醒 | `rm_8f9a0b1c2d3e` |

业务上另有**人工确认编号** `confirm_code`，形如 `T03`（T + 两位数字），只在当前用户范围内唯一、短期有效，给用户在企微里回复用。

### 2.3 字段命名

- JSON 的 key 一律**小写蛇形**英文：`start_at`、`exam_date`、`daily_limit_minutes`
- 枚举值一律小写：`"status": "confirmed"`
- 时间字段名以 `_at` 结尾表示完整时间，以 `_date` 结尾表示日期，以 `_time` 结尾表示钟点
- 金额、时长单位统一为**分钟**（整数），字段名带 `_minutes`

### 2.4 版本号

- 每份**正式计划**有 `version`（整数，从 1 开始，每次确认 +1）
- 每个**预览**记录它基于哪一版：`base_plan_id` + `base_version`
- 确认时如果正式计划已经不是 `base_version`，拒绝，要求重新生成预览。

### 2.5 幂等

| 场景 | 规则 |
|---|---|
| 同一条企微消息重复送达 | `wecom_msg_id` 唯一，第二次返回 `duplicate: true`，不新增数据 |
| 同一个预览重复确认 | 不重复写入，返回第一次的 `applied` 结果，`duplicate: true` |
| 确认已过期 / 已取消的预览 | 拒绝（见错误码） |

### 2.6 错误 vs「没有数据」的区别（V4 明确要求）

| 情况 | 表现 |
|---|---|
| 查不到数据（没课表、没任务） | **正常返回**，数组为空 + `missing` 里写原因 |
| 需要用户补充信息 | **正常返回**，`status: "needs_clarification"` + `questions` |
| 时间排不下 | **正常返回**，`capacity.gap_minutes > 0`，不是报错 |
| 用户不存在的对象 / 越权访问 | 抛错 `NOT_FOUND` |
| 版本冲突、预览过期、约束被破坏、内部故障 | 抛错，见第 6 节 |

**A 不要把所有非 200 都回「出错了」。** 缺数据和需要追问是正常业务流程。

---

## 3. 共享对象（三种角色都按这个格式对接）

下面字段是**最小必需集**。加字段可以，删字段必须三方同意。

### 3.1 `User`

```json
{
  "id": "us_9f2c1a7b3d4e",
  "name": "小吴",
  "timezone": "Asia/Shanghai"
}
```

### 3.2 `ClassSession`（某一次具体的课）

```json
{
  "id": "cs_7f8e9d0c1b2a",
  "course_id": "cr_1a2b3c4d5e6f",
  "course_name": "机器学习",
  "session_date": "2026-12-01",
  "start_time": "14:00",
  "end_time": "15:30",
  "location": "教三 302",
  "status": "normal",
  "version": 1
}
```

`status`：`normal` 正常 / `moved` 已调课 / `cancelled` 已停课。

> `course_name` 是后端从 `courses` 表联查带出的**显示字段**，`class_sessions` 表本身不存它（见 `DATA_SCHEMA.md` 3.4 节）。下面的 `Exam` 同理。

> 调课**只改这一条** `class_sessions`，不动每周课表。这是 V4 的硬要求。

### 3.3 `Exam`

```json
{
  "id": "ex_3c4d5e6f7a8b",
  "course_id": "cr_1a2b3c4d5e6f",
  "course_name": "机器学习",
  "name": "机器学习期末考试",
  "exam_date": "2026-12-04",
  "start_time": "14:00",
  "end_time": "15:30",
  "location": "教三 302",
  "status": "scheduled",
  "version": 1
}
```

> 考试日期**只存在这里**。任务、计划、缺口计算都通过 `exam_id` 反查，不复制日期（V4 要求）。

### 3.4 `Task`

```json
{
  "id": "tk_9c0d1e2f3a4b",
  "goal_id": "gl_5e6f7a8b9c0d",
  "course_id": "cr_1a2b3c4d5e6f",
  "title": "复习第三章",
  "detail": "教材第 3 章 + 课后题",
  "due_at": "2026-12-01T23:59:00+08:00",
  "estimated_minutes": 60,
  "remaining_minutes": 60,
  "priority": 1,
  "is_optional": 0,
  "splittable": 0,
  "status": "todo",
  "source": "wecom"
}
```

- `remaining_minutes`：部分完成后更新（“第三章只复习了一半” → 60 里还剩 30）
- `is_optional`：1 = 选做，时间不够时可以移出（演示里的「附加模拟练习」）
- `splittable`：1 = 允许拆成多个时间块

### 3.5 `Goal`

```json
{
  "id": "gl_5e6f7a8b9c0d",
  "exam_id": "ex_3c4d5e6f7a8b",
  "title": "准备机器学习期末考试",
  "daily_limit_minutes": 90,
  "status": "active",
  "version": 1
}
```

### 3.6 `PlanItem`（计划里的一个时间块）

```json
{
  "id": "pi_0a1b2c3d4e5f",
  "start_at": "2026-12-01T19:00:00+08:00",
  "end_at": "2026-12-01T20:30:00+08:00",
  "title": "复习第四章",
  "item_type": "task",
  "task_id": "tk_2b3c4d5e6f7a",
  "session_id": null,
  "exam_id": null,
  "is_locked": 0,
  "status": "planned"
}
```

`item_type`：`course` 课程 / `exam` 考试 / `task` 学习任务 / `rest` 休息 / `custom` 其他。
`status`：`planned` 待做 / `done` 已完成 / `locked` 锁定不动。

### 3.7 `Plan`（一份正式计划）

```json
{
  "id": "pl_2f3a4b5c6d7e",
  "goal_id": "gl_5e6f7a8b9c0d",
  "status": "confirmed",
  "version": 4,
  "note": "计划待调整",
  "updated_at": "2026-11-30T10:12:00+08:00",
  "items": [ /* PlanItem 数组 */ ]
}
```

`status`：`draft` 草稿 / `confirmed` 正式 / `superseded` 已被新版本替代。
`note`：例如考试已改期但新计划还没确认时，写 `计划待调整`。

### 3.8 `ScheduleResult`（排程/缺口结果）

```json
{
  "required_minutes": 230,
  "scheduled_minutes": 150,
  "gap_minutes": 80,
  "blocks": [
    {
      "date": "2026-12-01",
      "start_at": "2026-12-01T19:00:00+08:00",
      "end_at": "2026-12-01T20:30:00+08:00",
      "task_id": "tk_2b3c4d5e6f7a",
      "title": "复习第四章",
      "minutes": 90
    }
  ],
  "unscheduled": [
    { "task_id": "tk_5c6d7e8f9a0b", "title": "附加模拟练习", "minutes": 80, "reason": "AFTER_EXAM_NO_SLOT" }
  ],
  "violations": []
}
```

- `gap_minutes = required_minutes - scheduled_minutes`，这是 V4 要求必须展示的数字
- `unscheduled[].reason` 取值见 6.3
- `violations`：排出来的块里如果有违规（例如挤占了课程），写在这里；正常情况为空数组

### 3.9 `Preview`（待确认草稿）—— 最重要

```json
{
  "preview_id": "pv_4b5c6d7e8f9a",
  "confirm_code": "T03",
  "kind": "reschedule",
  "status": "pending",
  "summary": "考试提前到 12-02 14:00。第三章复习移到 12-01 19:00；选做的模拟练习移出。两项必做任务保留。",
  "base": { "plan_id": "pl_2f3a4b5c6d7e", "version": 3 },
  "expires_at": "2026-11-30T10:42:00+08:00",
  "diff": {
    "added":   [ { "object_type": "plan_item", "object_id": "pi_new1", "after": { "...": "..." } } ],
    "changed": [ { "object_type": "exam", "object_id": "ex_3c4d5e6f7a8b",
                   "before": { "exam_date": "2026-12-04", "start_time": "14:00" },
                   "after":  { "exam_date": "2026-12-02", "start_time": "14:00" } } ],
    "removed": [ { "object_type": "plan_item", "object_id": "pi_old9",
                   "before": { "title": "附加模拟练习" } } ],
    "kept":    [ { "object_type": "plan_item", "object_id": "pi_keep1",
                   "reason": "必做任务，未受影响" } ]
  },
  "capacity": { "required_minutes": 230, "scheduled_minutes": 150, "gap_minutes": 80,
                "options": ["add_windows", "drop_optional"] },
  "requires_user_choice": true
}
```

**预览只写 `previews` 表，绝不改 `plans` / `tasks` / `exams` / `class_sessions`。** 这是「确认前不修改正式计划」的唯一实现方式。

### 3.10 `Dashboard`（C 的看板一次性数据）

```json
{
  "user": { "id": "us_9f2c1a7b3d4e", "name": "小吴" },
  "generated_at": "2026-11-30T09:00:00+08:00",
  "today": "2026-11-30",
  "time_mode": "demo",
  "plan": { "id": "pl_2f3a4b5c6d7e", "goal_id": "gl_...", "version": 4,
            "status": "confirmed", "note": null,
            "updated_at": "2026-11-30T09:00:00+08:00" },
  "next_class": { "title": "机器学习", "start_at": "2026-11-30T10:00:00+08:00",
                  "end_at": "2026-11-30T11:30:00+08:00", "location": "教三 302",
                  "session_id": "cs_..." },
  "next_deadline": { "task_id": "tk_...", "title": "提交实验报告",
                     "due_at": "2026-12-01T23:59:00+08:00", "remaining_minutes": 60 },
  "pending_changes": [
    { "preview_id": "pv_4b5c6d7e8f9a", "confirm_code": "T03", "kind": "reschedule",
      "summary": "考试提前到 12-02，缺口 80 分钟待选择",
      "expires_at": "2026-11-30T10:42:00+08:00" }
  ],
  "days": [
    {
      "date": "2026-11-30",
      "weekday": "周一",
      "items": [
        { "start_at": "2026-11-30T19:00:00+08:00", "end_at": "2026-11-30T20:00:00+08:00",
          "title": "复习第三章", "item_type": "task", "status": "planned",
          "source": "plan", "location": null, "ref_id": "pi_..." }
      ]
    }
  ],
  "tasks": [ /* Task 数组 */ ],
  "exams": [ /* Exam 数组 */ ],
  "notices": [
    { "id": "nt_6d7e8f9a0b1c", "received_at": "2026-11-30T08:55:00+08:00",
      "status": "applied", "summary": "机器学习期末考试提前至 12-02 14:00" }
  ]
}
```

`days[].items[].source`：`plan` 来自计划 / `course` 来自课表 / `exam` 来自考试。
`days[].items[].status`：`planned` 待做 / `done` 已完成 / `locked` 锁定。
`ref_id` 指向来源记录的 ID，C 需要展开详情时用它调对应接口。

> **实现状态（2026-11-20）：** `services/queries.py` 的 `get_dashboard()` 已实现；
> HTTP 接口 `/api/v1/dashboard` 尚未实现。

`days[].items[].source`：`plan` 来自计划 / `course` 来自课表 / `exam` 来自考试。

---

## 4. 链路 1：A 调用 B（Python 函数）

### 4.1 调用方式

A 在 `agent/` 里这样用（**函数名和字段名就是契约，实现由 B 提供**）：

```python
from services import (
    get_context,                # 查上下文
    record_notice,              # 存通知原文（带去重）
    match_notice,               # 匹配已有课程/考试/任务
    preview_change,             # 生成预览（不改正式数据）
    confirm_preview,            # 用户确认后提交
    cancel_preview,             # 取消草稿
    service_errors,             # 异常基类与错误码
)

try:
    ctx = get_context(user_id="us_9f2c1a7b3d4e")
    preview = preview_change(user_id="us_9f2c1a7b3d4e", request={
        "kind": "exam_moved",
        "exam_id": "ex_3c4d5e6f7a8b",
        "new_date": "2026-12-02",
        "new_start": "14:00",
    })
    # preview["status"] == "pending" → 回「确认 T03」
    # preview["status"] == "needs_clarification" → 把 questions 转成追问
except service_errors.ServiceError as e:
    # e.code 见第 6 节
    ...
```

> `user_id` **必须**由 A 从企微回调的可信身份里取，不能从消息文本或模型输出里取。这是全系统安全的前提。

### 4.2 函数清单

| 函数 | 输入 | 输出 | 权限 |
|---|---|---|---|
| `get_context` | user_id, date_from?, date_to? | `ContextResult` | 读 |
| `record_notice` | user_id, wecom_msg_id, raw_text, received_at, parse_json? | `NoticeRecord` | 写 |
| `match_notice` | user_id, candidate | `MatchResult` | 读 |
| `preview_change` | user_id, request | `PreviewResult` | 写草稿 |
| `confirm_preview` | user_id, preview_id 或 confirm_code | `ConfirmResult` | 写正式 |
| `cancel_preview` | user_id, preview_id | `None` | 写草稿 |
| `get_dashboard` | user_id | `Dashboard` | 读 |

### 4.3 `get_context`

**用途：** Agent 在回答或排程前，一次拿到该用户的全部相关数据（V4「上下文查询」）。

```python
get_context(user_id: str, date_from: str | None = None, date_to: str | None = None) -> dict
```

- `date_from` / `date_to`：`YYYY-MM-DD`；不传默认「今天起 7 天」
- 返回：

```json
{
  "user": { "id": "us_...", "name": "小吴", "timezone": "Asia/Shanghai" },
  "range": { "from": "2026-11-30", "to": "2026-12-06" },
  "as_of": "2026-11-30T09:00:00+08:00",
  "time_mode": "demo",
  "data_version": { "plan_id": "pl_2f3a4b5c6d7e", "plan_version": 3 },
  "goals": [ /* Goal */ ],
  "exams": [ /* Exam */ ],
  "sessions": [ /* ClassSession */ ],
  "tasks": [ /* Task */ ],
  "preferences": [ { "key": "daily_limit_minutes", "value": 90,
                     "scope": "goal:gl_5e6f7a8b9c0d",
                     "confirmed_at": "2026-11-20T20:50:44+08:00" } ],
  "plan": { "id": "pl_...", "goal_id": "gl_...", "status": "confirmed",
            "version": 3, "note": null, "updated_at": "2026-11-30T09:00:00+08:00" },
  "plan_items": [ /* PlanItem */ ],
  "missing": [ { "field": "courses", "reason": "NO_COURSE_DATA" } ]
}
```

`time_mode`：当前是 `real` 还是 `demo`，方便 A 在演示时确认时间基准。

`missing` 是「数据不全」的唯一表达方式。例如用户还没录课表，`sessions` 为空且 `missing` 含 `NO_CLASS_DATA_IN_RANGE`，A 应提示补全，而不是当成「今天没课」。

`missing[].reason` 目前有：`NO_CLASS_DATA_IN_RANGE`、`NO_UPCOMING_EXAM`、`NO_OPEN_TASK`、`NO_CONFIRMED_PLAN`、`EMPTY_PLAN`、`NO_ACTIVE_GOAL`。

> **实现状态（2026-11-20）：** `services/queries.py` 中已实现，可直接调用。

### 4.4 `record_notice`

```python
record_notice(user_id, wecom_msg_id, raw_text, received_at, parse_json=None) -> dict
```

返回：

```json
{ "notification_id": "nt_6d7e8f9a0b1c", "duplicate": false, "status": "parsed" }
```

- 同一个 `wecom_msg_id` 第二次调用 → `duplicate: true`，返回原 `notification_id`，不新增记录
- `raw_text` 只当**数据**存，不执行其中任何指令（V4 安全要求）

### 4.5 `match_notice`

**分工依据 V4：A 负责解析，B 负责匹配。** A 把解析结果交来，B 判断它对应哪条已有记录、是否冲突、缺什么。

```python
match_notice(user_id: str, candidate: dict) -> dict
```

`candidate`（A 解析产出）：

```json
{
  "notice_type": "exam_change",
  "course_name": "机器学习",
  "title": "机器学习期末考试",
  "candidate_dates": ["2026-12-02"],
  "candidate_start": "14:00",
  "candidate_end": null,
  "location": null,
  "evidence": "机器学习期末考试提前至 12 月 2 日 14:00，复习范围不变。",
  "missing_fields": []
}
```

`notice_type`：`class_change` 调课/停课 / `exam_change` 考试安排或改期 / `homework` 作业或截止 / `other`。

返回：

```json
{
  "candidate": { "...": "原样带回" },
  "matches": [
    {
      "object_type": "exam",
      "object_id": "ex_3c4d5e6f7a8b",
      "score": 0.95,
      "reasons": ["课程名一致", "考试名一致"],
      "current": { "exam_date": "2026-12-04", "start_time": "14:00" }
    }
  ],
  "needs_choice": false,
  "questions": [],
  "conflicts": [],
  "is_stale": false
}
```

`needs_choice: true` 表示有多个候选（V4：同一课程两次候选要让用户选）。
`questions` 用于 A 生成追问，例如 `[{"field": "time", "reason": "NOTICE_HAS_NO_TIME", "ask": "具体是哪天、几点？"}]`。
`is_stale: true` 表示这条通知比现有记录更旧（旧通知不回退日程）。

### 4.6 `preview_change`（核心）

```python
preview_change(user_id: str, request: dict) -> dict
```

`request.kind` 决定必填字段：

| kind | 含义 | 必填字段 |
|---|---|---|
| `initial_plan` | 为目标首次排程 | `goal_id` |
| `exam_moved` | 考试改期 | `exam_id`, `new_date`, `new_start`（`new_end` 可选） |
| `class_moved` | 单次调课/停课 | `session_id`, `new_date`, `new_start`, `new_end`（停课传 `status:"cancelled"`） |
| `progress` | 记录进度 | `task_id`, `remaining_minutes` |
| `capacity_choice` | 缺口协商 | `base_preview_id`, `action`（`add_windows` / `drop_optional`），`windows` 或 `task_ids` |

`capacity_choice.add_windows` 示例：

```json
{ "kind": "capacity_choice", "base_preview_id": "pv_4b5c6d7e8f9a",
  "action": "add_windows",
  "windows": [ { "date": "2026-12-02", "start_time": "20:00", "end_time": "21:00" } ] }
```

`capacity_choice.drop_optional` 示例：

```json
{ "kind": "capacity_choice", "base_preview_id": "pv_4b5c6d7e8f9a",
  "action": "drop_optional", "task_ids": ["tk_5c6d7e8f9a0b"] }
```

三种返回状态：

**① 生成成功，等待确认**

```json
{ "status": "pending", "preview": { /* Preview 对象 */ } }
```

**② 信息不全，需要追问（不是错误）**

```json
{ "status": "needs_clarification",
  "questions": [ { "field": "new_start", "reason": "MISSING_TIME", "ask": "改到几点？" } ] }
```

**③ 完全不可行（例如考试时间已过），必须说明原因**

```json
{ "status": "rejected", "reason": "EXAM_IN_PAST",
  "message": "这场考试的时间已经过去了，无法据此重排。" }
```

### 4.7 `confirm_preview`

```python
confirm_preview(user_id: str, preview_id: str | None = None,
                confirm_code: str | None = None) -> dict
```

- `preview_id` 和 `confirm_code` 二选一（用户回「确认 T03」时 A 传 `confirm_code`）
- 校验顺序：归属 → 状态 `pending` → 未过期 → `base_version` 仍是最新 → 约束复检
- 成功后单事务写入，然后**重新查库**再返回（V4 要求「写入后重查」）

返回：

```json
{
  "ok": true,
  "duplicate": false,
  "preview_id": "pv_4b5c6d7e8f9a",
  "applied": {
    "plan_id": "pl_8a9b0c1d2e3f",
    "plan_version": 4,
    "changed": [ { "object_type": "exam", "object_id": "ex_3c4d5e6f7a8b",
                   "before": { "exam_date": "2026-12-04" }, "after": { "exam_date": "2026-12-02" } } ],
    "removed": [ { "object_type": "plan_item", "object_id": "pi_old9" } ],
    "kept":    [ { "object_type": "task", "object_id": "tk_9c0d1e2f3a4b" } ]
  },
  "reminders": { "cancelled": 2, "queued": 1 },
  "verified_at": "2026-11-30T10:15:00+08:00"
}
```

### 4.8 `cancel_preview`

```python
cancel_preview(user_id: str, preview_id: str) -> None
```

只把 `previews.status` 改成 `cancelled`，**不回退**任何已确认的事实。

### 4.9 `get_dashboard`

```python
get_dashboard(user_id: str) -> dict   # 返回 Dashboard 对象
```

纯读，**不触发重排**（V4：「链接不触发重排」）。

---

## 5. 链路 2：C 调用 B（HTTP）

### 5.1 启动

```bash
cd unipulse
python -m uvicorn api.app:app --host 0.0.0.0 --port 8000
```

- 接口文档：`http://127.0.0.1:8000/docs`（FastAPI 自动生成，C 可直接对照调试）
- 手机访问需要把 `127.0.0.1` 换成电脑局域网 IP（例如 `http://192.168.1.10:8000`），⚠️待确认

C 的看板在 `web/app.py`，单独启动：

```bash
streamlit run web/app.py
```

看板通过 HTTP 调用上面的后端，**不直接连数据库**。

### 5.2 认证：链接里的 token

V4 明确要求「不能凭链接里的用户编号读取数据」。所以：

- A 回给用户的链接形如：`https://<看板地址>/?token=<签名后的长字符串>`
- token 由 **B 签发**（`create_dashboard_token(user_id)`），内含 `user_id` + 过期时间，用密钥签名
- C 不做身份判断，**原样把 token 转发给 B**，B 校验后返回该用户的数据
- token 内**不含**明文 `user_id`，也不接受任何「传 user_id 就返回数据」的接口

⚠️待确认：token 放 query 参数（手机点链接最省事）还是另外做一次性登录换取 cookie？B 建议先 query + 2 小时有效期。

### 5.3 接口清单

| 方法 | 路径 | 用途 | 认证 |
|---|---|---|---|
| GET | `/health` | 健康检查 | 无 |
| GET | `/api/v1/dashboard` | 看板一次性数据 | token |
| GET | `/api/v1/courses` | 课表（按日期范围） | token |
| GET | `/api/v1/exams` | 考试列表 | token |
| GET | `/api/v1/tasks` | 待办列表 | token |
| GET | `/api/v1/plans/current` | 当前正式计划 | token |
| GET | `/api/v1/previews/{preview_id}` | 某个草稿的前后差异 | token |
| POST | `/api/v1/previews/{preview_id}/confirm` | 网页备用确认入口 | token |
| POST | `/api/v1/previews/{preview_id}/cancel` | 取消草稿 | token |
| GET | `/api/v1/notifications/{notification_id}` | 通知原文与来源 | token |

所有 token 接口都有 `?token=...`，且**只返回 token 所属用户的数据**。

> **实现状态（2026-11-21）：** `/health`、`/dashboard`、`/courses`、`/exams`、`/tasks`、
> `/plans/current`、`/previews/{id}`、`/previews/{id}/confirm`、`/previews/{id}/cancel` 已实现；
> `/notifications/{id}` 待通知模块完成后实现。
> Token 由 `api/auth.py` 签发：`python -m api.auth --user us_demo_a`。

### 5.4 `GET /api/v1/dashboard`

```
GET /api/v1/dashboard?token=eyJ...
```

200 返回第 3.10 节的 `Dashboard`。

### 5.5 `GET /api/v1/courses`

```
GET /api/v1/courses?token=...&date_from=2026-11-30&date_to=2026-12-06
```

```json
{ "date_from": "2026-11-30", "date_to": "2026-12-06", "items": [ /* ClassSession */ ] }
```

### 5.6 `GET /api/v1/exams`

```json
{ "items": [ /* Exam */ ] }
```

### 5.7 `GET /api/v1/tasks`

参数：`status`（可选，`todo|doing|done`）

```json
{ "items": [ /* Task */ ] }
```

### 5.8 `GET /api/v1/plans/current`

```json
{ "plan": { /* Plan，含 items */ } }
```

没有正式计划时返回 `{"plan": null}`，**不是 404**。

### 5.9 `GET /api/v1/previews/{preview_id}`

返回 `Preview`。过期后仍可查看，但 `status` 为 `expired`。

### 5.10 `POST /api/v1/previews/{preview_id}/confirm`

```json
{ "confirm_code": "T03", "expected_version": 3 }
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `confirm_code` | **是** | 预览里显示的确认码；必须与预览一致，防止只凭 ID 跳过确认 |
| `expected_version` | 否 | 调用方看到的正式计划版本；与预览的 `base_version` 不符时拒绝 |

- 成功：返回与 `confirm_preview` 相同结构（`ok` / `applied` / `reminders`）
- 确认码不对：422 `CONFIRM_CODE_MISMATCH`；没给：422 `MISSING_CONFIRM_CODE`
- 过期 / 已取消：409 `PREVIEW_EXPIRED` / `PREVIEW_NOT_PENDING`
- 版本变化：409 `VERSION_CONFLICT`
- 重复确认：返回 200，`duplicate: true`，不会重复建计划

这是**备用入口**（V4：企微不可用时网页保留简版确认）。主路径永远是企微私聊。

### 5.10.1 `POST /api/v1/previews/{preview_id}/cancel`

无请求体。只把预览置为 `cancelled`，不影响正式计划。

- 成功：200，返回预览详情（`status: "cancelled"`）
- 已确认/已过期：409 `PREVIEW_NOT_PENDING`

### 5.11 统一错误响应

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "正式计划已更新到第 4 版，请重新生成预览。",
    "details": { "expected_version": 3, "current_version": 4 },
    "request_id": "req_7c8d9e0f1a2b"
  }
}
```

`UNAUTHORIZED` 会在 `details.reason` 里说明具体原因，C 可以据此决定提示文案：

| reason | 含义 |
|---|---|
| `MISSING_TOKEN` | 没带 Token |
| `MALFORMED_TOKEN` | Token 格式不对（或 Authorization 头不是 `Bearer xxx`） |
| `BAD_SIGNATURE` | 签名不匹配（伪造或密钥变了） |
| `TOKEN_EXPIRED` | 已过期 |
| `NOT_YET_VALID` | 签发时间在未来（时钟不一致） |
| `WRONG_MODE` | 演示 Token 用在正式模式，或反之 |

HTTP 状态码与 `code` 对应关系见第 6 节。

---

### 5.12 `/v1/*` 兼容接口（给成员 C 的网页）

C 的页面按 `GET /v1/workspace` + `{ok:true,data}` 信封实现，为了让**已有页面不改**就能连真实后端，
B 增加一层薄适配。它不重复业务逻辑：全部调用 `services/`，并且与 `/api/v1/*` 共用同一套 Token 与用户隔离。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/workspace` | 工作台数据（version/profile/goals/events/classes/tasks/study_blocks/plans/reminders/notices/pending/activity） |
| POST | `/v1/notices` | 保存通知原文（只入库，不解析、不改考试） |
| POST | `/v1/previews` | 按**新考试时间**生成改期预览 |
| GET | `/v1/previews/{preview_id}` | 预览详情 |
| POST | `/v1/actions/{preview_id}/confirm` | 确认并提交（同一事务更新考试、计划、提醒） |
| POST | `/v1/actions/{preview_id}/cancel` | 取消草稿 |

**语义映射：** C 的 `event` = B 的 `exam`（一场考试事件）；C 的 `action` = B 的 `preview`（同一张 `previews` 表）。

**请求体（保存通知）：**

```json
{ "text": "教务通知：……", "expected_version": 1 }
```

- `text` 必填，去首尾空白后 1—20000 字；为空返回 422 `EMPTY_NOTICE`，超长返回 422 `NOTICE_TOO_LONG`
- `expected_version` 只做记录：通知是追加写入，不参与计划版本冲突判断
- 成功返回 `{id, text, status, channel, received_at, published_at, message}`
- `channel` 数据库没有该列，返回展示用默认值 `"未标注"`（不是伪造的真实渠道）
- `status` 复用 `parsed` 表示"已入库"，`parse_json` 为空时对外显示"待解析"

**请求体（创建预览）：**

```json
{
  "event_id": "ex_demo_ml_final",
  "new_start": "2026-12-02T14:00:00+08:00",
  "expected_version": 1,
  "source_notice_ids": ["nt_demo"]
}
```

**响应信封：**

```json
{ "ok": true, "data": { ... } }
{ "ok": false, "error": { "code": "VERSION_CONFLICT", "message": "..." } }
```

**确认接口的差异（需要注意）：**

- `/v1/actions/{id}/confirm` 允许**不传** `confirm_code`：C 当前只发 `{}`，兼容层会读取预览自带的确认码。
- `/api/v1/previews/{id}/confirm` 仍然**强制要求** `confirm_code`，两套入口的安全等级不同，这是为兼容 C 现状的临时取舍，待 C 改成带上确认码后可统一。

---

## 6. 错误码

### 6.1 错误码总表

| code | HTTP | 含义 | A 应如何回复用户 |
|---|---|---|---|
| `UNAUTHORIZED` | 401 | token 无效/过期 | 「链接已过期，请再发一次『发一下计划表』」 |
| `NOT_FOUND` | 404 | 对象不存在或不属于该用户 | 「没找到这项安排，可能已经变了」 |
| `VALIDATION_ERROR` | 422 | 参数格式不对（日期写成 `12/02`） | 回「日期请用 2026-12-02 这种格式」 |
| `VERSION_CONFLICT` | 409 | 预览基于的版本已过时 | 「计划刚被改过，我重新算一版」并重新预览 |
| `PREVIEW_EXPIRED` | 410 | 预览超过有效期 | 「这条确认已过期，我重新生成」 |
| `PREVIEW_NOT_PENDING` | 409 | 已确认/已取消 | 「这条已经处理过了」 |
| `CONSTRAINT_VIOLATION` | 409 | 会挤占课程/超每日上限/动到锁定块 | 说明冲突对象，让用户决定 |
| `INTERNAL_ERROR` | 500 | 数据库或程序故障 | 「系统出了点问题，稍后再试」，**不能回成功** |

### 6.2 不是错误的三种「正常返回」

| 场景 | 返回 |
|---|---|
| 没有数据 | 空数组 + `missing` |
| 需要补充 | `status: "needs_clarification"` + `questions` |
| 排不下 | `capacity.gap_minutes > 0` + `capacity.options` |

### 6.3 缺口原因枚举 `unscheduled[].reason`

| 值 | 含义 |
|---|---|
| `NO_FREE_SLOT` | 七天内没有空档 |
| `AFTER_EXAM_NO_SLOT` | 时间窗落在考试之后（演示主场景） |
| `DAILY_LIMIT_REACHED` | 当天已达到每日学习上限 |
| `NOT_SPLITTABLE` | 任务不允许拆分，找不到足够连续空档 |
| `DEADLINE_PASSED` | 已过截止时间 |
| `LOCKED_BY_USER` | 该块被用户锁定 |

---

## 7. 用户数据隔离

V4 硬性要求：「网页校验访问者与企微用户的绑定关系，不能凭链接里的用户编号读取数据」。

### 7.1 五条规则

1. **`user_id` 只能来自可信来源**：企微回调的发送者身份（A 侧），或签名 token（C 侧）。**永远不来自**通知原文、模型输出、URL 明文参数。
2. **所有 SQL 都带 `WHERE user_id = ?`**：`services/` 里不允许出现不带用户条件的业务查询。
3. **查不到就是 404**：别人的对象对当前用户等于不存在，不返回 403，避免探测。
4. **写操作前先验归属**：确认预览、取消预览、改任务，都先 `SELECT ... WHERE id=? AND user_id=?`。
5. **链接不含明文 user_id**：token 里塞的是签名后的载荷，C 拿到也用不了别人的。

### 7.2 必须覆盖的测试（下一阶段实现）

- 用户甲拿自己的 token 请求用户乙的 `preview_id` → `NOT_FOUND`
- 用户乙的 token 打开甲的看板 → 看不到甲的任何数据
- 伪造/篡改 token → `UNAUTHORIZED`
- 过期 token → `UNAUTHORIZED`

---

## 8. 确认与版本（「确认前不改正式数据」怎么保证）

### 8.1 状态机

```
                    preview_change
                          │
                          ▼
                    ┌───────────┐
     confirm ──────►│ confirmed │（正式数据已改，版本 +1）
                    └───────────┘
        │
   ┌────┴────┐
   │ pending │── cancel ──► cancelled（正式数据不变）
   └────┬────┘
        └── 超过 expires_at ──► expired（正式数据不变）
```

### 8.2 六条规则

1. 生成预览只写 `previews`，正式表**一个字节都不动**。
2. 确认时依次校验：归属 → `pending` → 未过期 → `base_version` 等于当前正式版本 → 约束复检。
3. 全部通过后，**一个事务**内完成：旧计划 `superseded` → 新计划 `confirmed`（version+1）→ 写 `plan_items` → 更新 `exams`/`class_sessions`/`tasks` 的实际字段 → 失效旧 `reminders` → 插入新 `reminders` → `previews.status='confirmed'`。任何一步失败整体回滚。
4. 提交后**重新查库**，把查到的真实结果作为 `applied` 返回，不返回「我以为写进去的值」。
5. 重复确认返回第一次的结果 + `duplicate: true`，不重复写入。
6. 过期/冲突/越权一律拒绝，正式数据保持原样，并告诉 A 需要重新预览。

### 8.3 演示场景的完整时序（12/6 答辩就用这条）

| 步 | 角色 | 动作 / 数据 |
|---|---|---|
| 1 | 学生 | 「帮我准备机器学习考试，每天最多 90 分钟」 |
| 2 | A | `get_context` → 拿到考试 12-04 14:00、三段空档 60/90/80 |
| 3 | A | `preview_change(kind=initial_plan)` → 预览 T01，210 分钟全排下，`gap=0` |
| 4 | 学生 | 「确认 T01」→ `confirm_preview(confirm_code="T01")` → 计划 v1 |
| 5 | 学生 | 粘贴「考试提前至 12-02 14:00」 |
| 6 | A | 解析 + `record_notice` + `match_notice` → 命中 `ex_...` |
| 7 | A | `preview_change(kind=exam_moved)` → 预览 T03，`required=230, scheduled=150, gap=80`，`requires_user_choice=true` |
| 8 | 学生 | 「减少选做」→ `preview_change(kind=capacity_choice, action=drop_optional)` → 新预览 T04，`gap=0` |
| 9 | 学生 | 「确认 T04」→ 事务提交 → 计划 v2，考试日期 12-02 |
| 10 | 学生 | 「发一下计划表」→ A 用 `create_dashboard_token` 拼链接 |
| 11 | C | 用 token 请求 `/api/v1/dashboard` → 看到 v2、考试 12-02、选做已移出 |

第 7 步的 230 / 150 / 80 **必须由代码算出来**，不能写死在回复里（V4 明确要求）。

---

## 9. 待确认事项

见 `docs/OPEN_QUESTIONS.md`。其中影响本文件的有：

- ⚠️ A 是否同意「同进程 import」而不是 HTTP 调用 B
- ⚠️ 解析字段（3.1 节的 `candidate`）由 B 定义、A 填充，A 是否接受
- ⚠️ `confirm_code` 由 B 生成、A 只回显，A 是否接受
- ⚠️ C 的 Streamlit 是走 HTTP 还是同进程 import（目录已定 `web/`，方式待 C 确认）
- ⚠️ token 放 query 参数是否满足手机端与安全要求
- ⚠️ 课表是「具体日期」还是「每周重复 + 例外」，直接影响 `class_sessions` 表

---

## 10. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-11-20 | 初稿：按 V4 第 3 节定义 A/B/C 三个角色、7 个服务函数、9 个 HTTP 接口、8 个错误码 |
| v0.5 | 2026-11-21 | 新增 `POST /v1/notices`；预览的 `source_notice_ids` 增加归属校验；workspace 字段按 C 的严格类型校验补齐（`channel`、`source`、`tasks.version` 等） |
| v0.4 | 2026-11-21 | 增加 `/v1/*` 兼容接口（workspace、改期预览、确认/取消）；改期预览携带 `proposed_exam`，确认时同事务更新考试时间 |
| v0.3 | 2026-11-21 | 预览确认落地：新增 `/previews/{id}` 读取、`/confirm`、`/cancel`；confirm 必须带 `confirm_code`（服务端强校验）；补充 404/409/422 的细分错误码 |
| v0.2 | 2026-11-20 | 只读查询落地：`get_context` 增加 `time_mode` / `plan` / `plan_items`；`Dashboard` 增加 `today` / `time_mode` / `days[].weekday` / `items[].location` / `items[].ref_id` / `next_class.session_id`；所有时间字段统一为带时区 ISO；`missing.reason` 补全枚举 |
