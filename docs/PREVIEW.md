# 排程预览服务说明（PREVIEW）

> **模块：** `services/preview.py`
> **状态：** 已完成（只写 `previews` 表，不改正式计划）
> **维护：** 成员 B｜2026-11-21
> **测试：** `tests/test_preview.py`（19 项）

---

## 1. 它做什么 / 不做什么

| 做 | 不做 |
|---|---|
| 调用排程算出候选安排 | ❌ 不修改 `plans` / `plan_items` / `tasks` / `exams` |
| 与当前正式计划对比，给出差异 | ❌ 不实现"确认后提交"（下一轮） |
| 把草稿写进 `previews` 表 | ❌ 不增加可用时段、不删除选做任务 |
| 记录 `base_version`、有效期、确认码 | ❌ 不接 HTTP、不接企微、不调模型 |
| 提供确认前的校验函数 | ❌ 不做用户容量协商 |

**一句话：** 生成"打算怎么改"的草稿，并保证确认前一切正式数据原封不动。

---

## 2. 数据从哪来

| 数据 | 来源 |
|---|---|
| 候选排程 | `services/scheduler.generate_schedule(user_id, exam_id)` |
| 旧计划 | `plans` 里 `status='confirmed'` 且版本最高的那条，及其 `plan_items` |
| 草稿 | 写入 `previews` 表 |
| 有效期 | `UNIPULSE_PREVIEW_TTL_MINUTES`，默认 30 分钟 |

`previews` 表字段直接复用，没有改 schema：

| 字段 | 用法 |
|---|---|
| `kind` | 有正式计划时写 `reschedule`，没有则写 `initial_plan` |
| `confirm_code` | 用户范围内递增，`T01`、`T02`… |
| `base_plan_id` / `base_version` | 预览基于哪一版正式计划 |
| `diff_json` | 新增/移除/调整/保留 |
| `capacity_json` | 缺口数字、窗口、考试、旧计划快照、提示 |
| `expires_at` | 过期时间 |
| `summary` | 一句话摘要（给 A 直接回用户） |

---

## 3. 对外函数

### 3.1 `create_preview(user_id, exam_id, *, conn=None, ttl_minutes=None)`

生成（或复用）一条待确认预览。

```python
from services.preview import create_preview

p = create_preview("us_demo_a", "ex_demo_ml_final")
p["confirm_code"]          # "T01"
p["status"]                # "pending"
p["diff"]["removed"]       # 这次会移出哪些块
```

**复用规则：** 同一用户、同一目标、同一 `base_version`，且差异完全相同时，
直接返回已有草稿（`reused: True`），不会刷出一堆编号。

### 3.2 `get_preview(user_id, preview_id, *, conn=None)`

只读。过期但还没被标记的预览会显示 `status="expired"`，**不改库**。

### 3.3 `cancel_preview(user_id, preview_id, *, conn=None)`

只能取消 `pending` 的；已过期/已确认/已取消会报 `PREVIEW_NOT_PENDING`。

### 3.4 `assert_confirmable(user_id, preview_id=None, confirm_code=None, *, conn=None)`

**确认流程在写正式数据之前必须调用它。** 依次检查：

1. 归属（别人的预览一律 `PREVIEW_NOT_FOUND`）
2. 状态必须是 `pending`
3. 未过期
4. `base_plan_id` / `base_version` 仍等于当前正式计划

通过后返回预览详情并带 `confirmable: True`。本轮不实现后续的提交。

**确认码不是身份凭据**：用 `confirm_code` 查也一定带 `user_id` 条件，拿别人的编号查不到。

---

## 4. 返回结构

```json
{
  "preview_id": "pv_5e2c957a390a",
  "confirm_code": "T02",
  "kind": "reschedule",
  "status": "pending",
  "base_plan_id": "pl_demo_01",
  "base_version": 1,
  "created_at": "2026-11-20 21:40:12",
  "expires_at": "2026-11-30T09:30:00+08:00",
  "confirmed_at": null,
  "exam": {
    "id": "ex_demo_ml_final",
    "name": "机器学习期末考试",
    "exam_date": "2026-12-02",
    "start_at": "2026-12-02T14:00:00+08:00",
    "end_at": "2026-12-02T15:30:00+08:00",
    "cutoff_at": "2026-12-02T14:00:00+08:00"
  },
  "old_plan": {
    "id": "pl_demo_01",
    "version": 1,
    "items": [
      {"id": "pi_demo_01", "start_at": "2026-11-30T19:00:00+08:00",
       "end_at": "2026-11-30T20:00:00+08:00", "title": "复习第三章",
       "item_type": "task", "task_id": "tk_demo_ch3", "is_locked": false}
    ]
  },
  "new_schedule": {
    "windows": [{"start_at": "...", "end_at": "...", "date": "...", "minutes": 60}],
    "schedule_items": [{"task_id": "tk_demo_ch3", "title": "复习第三章",
                        "start_at": "...", "end_at": "...", "date": "...",
                        "duration_minutes": 60, "priority": 1, "is_optional": false}]
  },
  "diff": {
    "added": [],
    "removed": [
      {"task_id": "tk_demo_mock", "title": "模拟练习",
       "blocks": [{"start_at": "2026-12-02T15:00:00+08:00",
                   "end_at": "2026-12-02T16:20:00+08:00"}]}
    ],
    "changed": [],
    "kept": [
      {"task_id": "tk_demo_ch3", "title": "复习第三章",
       "reason": "unchanged", "blocks": [{"start_at": "...", "end_at": "..."}]}
    ]
  },
  "summary": {
    "required_minutes": 150,
    "scheduled_required_minutes": 150,
    "required_gap_minutes": 0,
    "optional_minutes": 80,
    "scheduled_optional_minutes": 0,
    "unscheduled_optional_minutes": 80,
    "total_requested_minutes": 230
  },
  "warnings": [{"code": "WINDOWS_AFTER_EXAM", "message": "..."}],
  "notes": [
    "候选时间窗口仅来自当前正式计划中的可移动学习块；不会自动寻找计划之外的空闲时间（T11 待确认）。",
    "这是待确认草稿，正式计划没有被修改；确认后才会生效。"
  ],
  "explanation": "考试「机器学习期末考试」在 2026-12-02 14:00 开始；……",
  "reused": false
}
```

---

## 5. 差异怎么算

**匹配键用 `task_id`，不用标题**（标题可能重复或改写）。

| 分类 | 含义 |
|---|---|
| `added` | 新排出来的任务块（旧计划里没有这个任务） |
| `removed` | 旧计划有、新安排没有的任务块 |
| `changed` | 同一任务，时间块位置/时长变了（`before` / `after`） |
| `kept` | 没变，或被保护了 |

`kept[].reason` 取值：

| reason | 含义 |
|---|---|
| `unchanged` | 时间完全一样 |
| `task_done` | 任务已完成，不重排 |
| `locked` | 时间块被锁定 |
| `started` | 时间块已经开始 |
| `fixed_event` | 课程/考试块，不属于重排范围 |
| `not_in_scope` | 没有 `task_id` 的自定义块 |

**保护优先于差异**：已完成、已开始、锁定的块只会出现在 `kept`，绝不会进 `removed`，
也不会被新排程覆盖。

---

## 6. 有效期与版本

| 规则 | 实现 |
|---|---|
| 过期不能确认 | `assert_confirmable` 抛 `PREVIEW_EXPIRED` |
| 已确认/已取消不能再确认 | 抛 `PREVIEW_NOT_PENDING` |
| 别人的预览不能操作 | 所有查询带 `user_id`，一律 `PREVIEW_NOT_FOUND` |
| 版本变化后旧预览作废 | `base_plan_id` / `base_version` 与当前正式计划不一致 → `VERSION_CONFLICT` |
| 重复确认 | 本轮不实现提交；预留的是 `assert_confirmable` + 未来事务 |

`status` 的"过期"是**读时计算**的：`previews.status` 仍是 `pending`，
但对外返回 `expired`。这样避免在读接口里写库。

---

## 7. 错误码

| code | 场景 | 来源 |
|---|---|---|
| `PREVIEW_NOT_FOUND` | 预览不存在 / 不属于该用户 | `PreviewError` |
| `PREVIEW_EXPIRED` | 已过期 | `PreviewError` |
| `PREVIEW_NOT_PENDING` | 已确认 / 已取消 / 已过期后取消 | `PreviewError` |
| `VERSION_CONFLICT` | 正式计划版本变了 | `PreviewError` |
| `INVALID_TTL` | 有效期配置非法 | `PreviewError` |
| `INVALID_ARGUMENT` | 既没给 preview_id 也没给 confirm_code | `PreviewError` |
| `EXAM_NOT_FOUND` | 考试不存在或不属于该用户 | `SchedulerError`（透传） |
| `GOAL_NOT_FOUND` | 考试没有进行中的目标 | `SchedulerError`（透传） |
| `EXAM_IN_PAST` | 考试时间已过 | `SchedulerError`（透传） |
| `EXAM_TIME_INVALID` | 考试缺开始时间 | `SchedulerError`（透传） |

---

## 8. 已知限制（本轮不做）

1. **不会创造新的学习时间**：窗口只来自正式计划的可移动块（T11）。
2. **不做容量协商**：必做有缺口时只报告，不自动加时段、不自动删选做。
   后续 A 负责聊天端协商，B 再加 `extra_windows`。
3. **不提交正式计划**：确认后的写入（新版本 + 时间块 + 提醒失效）是下一轮。
4. **不提供 HTTP 接口**：C 暂时拿不到预览数据，等 API 层接上。

---

## 9. 怎么运行

```bash
export UNIPULSE_TIME_MODE=demo
export UNIPULSE_DEMO_NOW=2026-11-30T09:00:00+08:00

python - <<'PY'
from services.preview import create_preview, assert_confirmable

p = create_preview("us_demo_a", "ex_demo_ml_final")
print(p["confirm_code"], p["status"], p["base_version"])
print("diff:", {k: len(v) for k, v in p["diff"].items()})
print("summary:", p["summary"])
print(assert_confirmable("us_demo_a", p["preview_id"])["confirmable"])
PY
```

---

## 10. 确认提交（confirm_preview）

```python
confirm_preview(user_id, preview_id, confirm_code, *, conn=None, expected_version=None)
```

### 10.1 校验顺序

1. 用户存在、预览属于该用户（否则 `PREVIEW_NOT_FOUND`）
2. 确认码：缺失 → `MISSING_CONFIRM_CODE`；格式错 → `INVALID_CONFIRM_CODE`；
   与预览不一致 → `CONFIRM_CODE_MISMATCH`
3. 状态：`confirmed` → **幂等返回第一次结果**；`expired` → `PREVIEW_EXPIRED`；
   `cancelled` → `PREVIEW_NOT_PENDING`
4. `expected_version`（如果传了）与预览 `base_version` 不一致 → `VERSION_CONFLICT`
5. 当前正式计划的 id/version 必须仍等于预览的 `base_plan_id/base_version`，否则 `VERSION_CONFLICT`

### 10.2 单事务写入

```
BEGIN
  ① 重新读预览，确认仍是 pending
  ② 重新校验正式计划版本（防止两次检查之间被别人改）
  ③ INSERT 新 plans（status=confirmed, version=旧版本+1, based_on_plan_id=旧计划）
  ④ UPDATE 旧计划 → superseded
  ⑤ INSERT 新 plan_items（新排的块 + 必须保留的旧块）
  ⑥ UPDATE 旧计划相关 reminders → cancelled
  ⑦ UPDATE 预览 → confirmed，confirmed_at，并在 capacity_json.applied 记录结果
COMMIT
```

任何一步抛异常都整体 ROLLBACK：不会出现"新计划建了一半"或"预览已确认但计划没变"。

提交后还会**回读校验**：新计划状态必须是 confirmed，时间块数量必须等于写入数量。

### 10.3 哪些旧时间块会保留

| 状态 | 处理 |
|---|---|
| 任务已完成（`tasks.status='done'`） | 原样复制到新计划，`is_locked` 保持不变 |
| 时间块已开始（`start_at <= now`） | 原样复制 |
| 时间块锁定（`is_locked=1`） | 原样复制，并保留 `is_locked=1` |
| 课程 / 考试块 | 原样复制（不属于重排范围） |
| 没有 `task_id` 的自定义块 | 原样复制 |
| 时间没变的学习块 | 用排程结果重新写入（内容一致） |
| 被移出的块 | 只留在旧计划里（旧计划变 superseded，历史可查） |

匹配全部基于 `task_id`、`item_type`、时间，不用标题。

### 10.4 幂等

- 第一次确认：`duplicate: false`，写新计划、版本 +1
- 第二次确认：`duplicate: true`，返回第一次的 `applied`，**不建计划、不递增版本**

### 10.5 提醒

`reminders` 表通过 `plan_item_id` 关联时间块，可以判断归属：

- 只失效「被替换的那份计划」里时间块关联的 `queued/failed` 提醒
- 其它计划、`plan_item_id` 为空的提醒不动
- **不新建提醒**（提醒排期逻辑还没做，P1）
- 失效与计划写入在同一个事务里，不会出现"提醒取消了但计划没更新"

### 10.6 改期预览（exam_update）

```python
create_exam_change_preview(user_id, exam_id, new_start, *,
                           expected_version=None, notice_id=None,
                           conn=None, ttl_minutes=None)
```

- 排程按**拟议的新考试时间**计算（`scheduler.generate_schedule(exam_start_override=...)`）
- 预览里存 `proposed_exam`：旧时间、新时间、新结束时间、考试当时的 version、来源通知
- **此时考试时间与正式计划都没变**

确认时在同一事务里先做：

1. 校验考试 `version` 仍等于预览记录的 `exam_version`（否则 `EXAM_VERSION_CONFLICT`）
2. 校验新时间仍晚于当前时间（否则 `PREVIEW_EXPIRED`）
3. `UPDATE exams`：日期/起止时间、`version+1`、`status='moved'`、来源通知
4. 再建新计划、写时间块、失效提醒、标记预览已确认

重复确认返回第一次结果，**不会**再次递增考试版本。

### 10.7 限制

1. **已开始的时间块会被保留，但对应任务可能被重新排到别处**：`tasks` 表没有"进行中"状态，
   无法判断任务已完成多少。这是 schema 限制，没有擅自推断。
2. **不新建提醒**：`reminders` 只有失效逻辑。
3. 改期预览的窗口仍只来自当前计划的可移动块（T11），不会寻找计划外空档。

## 11. 下一步

1. 预览携带 `exam_update`（改期与计划一起确认）
2. `capacity_choice`：用户选择加时段/移选做后重算
3. 提醒排期（P1）
