# 排程算法说明（SCHEDULER）

> **模块：** `services/scheduler.py`
> **状态：** 已完成，只读，不写数据库
> **维护：** 成员 B｜2026-11-20
> **测试：** `tests/test_scheduler.py`（23 项）

---

## 1. 它做什么 / 不做什么

| 做 | 不做 |
|---|---|
| 算出考前每个学习时段放哪个任务 | ❌ 不写数据库（计划、任务一律不动） |
| 报出必做 / 选做各缺多少分钟 | ❌ 不生成"预览"记录、不弹确认 |
| 遵守每日上限、考试截止、固定占用 | ❌ 不调模型、不做全局最优搜索 |
| 同样的输入给出同样的输出 | ❌ 不主动去找"计划之外"的空档 |

它的产出是**候选安排**，给后续的预览确认流程用（`previews` 表还没接）。

---

## 2. 时间窗口从哪来（重要设计假设）

V4 要求"先算可用时段"，但现有数据库没有单独存"空闲时间表"，本轮又不能改 schema。
所以采用：

> **候选窗口 = 当前正式计划里「可移动的学习块」**
> （`plan_items` 中 `item_type` 为 `task`/`rest`/`custom`，且未锁定、未开始、关联任务未完成）

然后依次处理：

1. **扣除固定占用**：课程（`class_sessions`，已停课的除外）、考试（`exams`）、
   锁定的时间块、已完成任务的时间块、已经开始的时间块
2. **合并**重叠或首尾相接的窗口（避免容量重复计算）
3. **按零点切开**跨午夜的窗口（每天的上限才好算）
4. **截断到考试开始之前**（考试之后的时间一律不用，哪怕还空着）

**为什么这样定：** 演示数据里 11-30 19:00、12-01 19:00、12-02 15:00 这三段，
正是计划 v1 的三个学习块——也就是学生"原本打算学习"的时间。重排就是把这三段
按新约束重新分配，这样算出的 150 / 80 才和 V4 第 7 节对得上。

⚠️ **这个假设需要团队确认**（已登记在 `docs/OPEN_QUESTIONS.md` T11）。
如果以后要支持"从早到晚自由找空档"，需要先决定"允许学习时段"存在哪
（偏好表 `user_preferences` 里加一个 `study_window`，或者做配置项），
那属于另一个迭代，本轮不擅自改表。

---

## 3. 算法流程

```
读数据（全部按 user_id 过滤）
  ├─ 用户、考试（必须属于该用户）
  ├─ 目标（该考试下 active 的那条，提供每日上限）
  ├─ 未完成任务（status ≠ done）
  ├─ 当前正式计划（status = confirmed，取版本最高）
  └─ 计划时间块 + 课程 + 其他考试

建窗口
  ├─ 可移动块 → 候选
  ├─ 固定块 / 课程 / 考试 → busy
  ├─ 候选 − busy → 合并 → 按零点切 → 截断到考试前
  └─ 得到 windows（带日期、分钟数）

排序任务（确定性）
  ├─ 必做在前、选做在后
  ├─ 截止早的在前（没截止按考试时间算）
  ├─ 优先级数字小的在前
  ├─ 同时长"大的先排"（减少窗口碎片）
  └─ ID 兜底

逐个放任务
  ├─ 每日已排分钟 ≤ 每日上限
  ├─ 可拆分任务可以跨窗口/跨天，单段 ≥ 15 分钟
  ├─ 不可拆分任务必须在一个窗口里一次放下
  └─ 放不下 → 记录剩余分钟 + 原因

汇总
  ├─ 必做需求 / 已排 / 缺口
  ├─ 选做需求 / 已排 / 未排
  └─ warnings + 中文解释
```

### 排序为什么是"大的先排"

最初按"截止→优先级→ID"排，测试里出现了一个真实问题：90 分钟窗口里先塞了 60 分钟任务，
剩下 30 分钟装不下 90 分钟任务，白白多出 90 分钟缺口。改成同优先级下"大的先排"后，
90 分钟任务进 90 分钟窗口、60 分钟任务进 60 分钟窗口，缺口消失。

这不是最优算法（那需要背包/整数规划），但简单、可解释、可测试。

---

## 4. 返回结构

```python
{
  "user_id": "us_demo_a",
  "generated_at": "2026-11-30T09:00:00+08:00",
  "time_mode": "demo",
  "exam": {
    "id": "ex_demo_ml_final",
    "name": "机器学习期末考试",
    "exam_date": "2026-12-04",
    "start_at": "2026-12-04T14:00:00+08:00",
    "end_at": "2026-12-04T15:30:00+08:00",
    "cutoff_at": "2026-12-04T14:00:00+08:00"
  },
  "goal_id": "gl_demo_ml",
  "daily_limit_minutes": 90,
  "plan": {"id": "pl_demo_01", "version": 1},
  "windows": [
    {"start_at": "...", "end_at": "...", "date": "2026-11-30", "minutes": 60}
  ],
  "schedule_items": [
    {
      "task_id": "tk_demo_ch3",
      "title": "复习第三章",
      "start_at": "2026-11-30T19:00:00+08:00",
      "end_at": "2026-11-30T20:00:00+08:00",
      "date": "2026-11-30",
      "duration_minutes": 60,
      "priority": 1,
      "is_optional": false
    }
  ],
  "required_minutes": 150,
  "scheduled_required_minutes": 150,
  "required_gap_minutes": 0,
  "optional_minutes": 80,
  "scheduled_optional_minutes": 80,
  "unscheduled_optional_minutes": 0,
  "total_requested_minutes": 230,
  "unscheduled": [
    {"task_id": "...", "title": "...", "minutes": 80,
     "is_optional": true, "reason": "NO_FREE_SLOT"}
  ],
  "warnings": [{"code": "WINDOWS_AFTER_EXAM", "message": "..."}],
  "explanation": "考试「机器学习期末考试」在 2026-12-04 14:00 开始；……"
}
```

**必做缺口和选做缺口分开统计**，不混在一起。

### 未排入原因

| reason | 含义 |
|---|---|
| `NO_FREE_SLOT` | 窗口不够（总时长或连续时长不足） |
| `NOT_SPLITTABLE` | 任务不允许拆分，找不到足够长的连续窗口 |
| `DAILY_LIMIT_REACHED` | 当天已达每日上限 |
| `NO_DURATION` | 任务没有剩余时长/预计时长，无法排 |

### 警告

| code | 含义 |
|---|---|
| `WINDOWS_AFTER_EXAM` | N 段可移动学习时间落在考试开始之后，已排除 |
| `NO_CONFIRMED_PLAN` | 没有已确认计划，没有可移动时间块 |
| `NO_OPEN_TASK` | 没有未完成任务 |
| `NO_FREE_WINDOW` | 考前没有任何可用窗口 |
| `DAILY_LIMIT_REACHED` | 某任务因每日上限没排完 |
| `TASK_WITHOUT_DURATION` | 任务缺时长 |
| `SKIP_INVALID_PLAN_ITEM` | 时间块格式异常，已跳过 |

---

## 5. 错误

| code | 触发条件 |
|---|---|
| `USER_NOT_FOUND` | 用户不存在 |
| `EXAM_NOT_FOUND` | 考试不存在，或不属于该用户（隔离） |
| `EXAM_TIME_INVALID` | 考试没有可用的开始时间 |
| `EXAM_IN_PAST` | 考试开始时间已过 |
| `GOAL_NOT_FOUND` | 该考试没有进行中的学习目标 |

错误用 `SchedulerError`（带 `.code`）抛出，调用方（未来的预览模块）据此决定回什么话术。

---

## 5.1 目标字段的使用现状（2026-11-26 核对）

| 目标数据 | 是否参与排程 |
|---|---|
| `goals.id` / `exam_id` | ✅ 选目标与任务 |
| `goals.daily_limit_minutes` | ✅ 每日上限 |
| `goals.version` | ✅ 预览记录并在确认时校验（变了就不能确认） |
| `goals.title` | 🟡 仅展示 |
| `scope`（复习范围） | ❌ 只存储与展示，**当前不影响排程** |
| `availability`（允许时段） | ❌ 只存储与展示，**当前不影响排程**（候选窗口仍来自计划的可移动块，T11） |

**预览与确认：** 预览生成时把 `goal_id` + `goal_version` 写入 `capacity_json`；
确认时在事务内重新读取 active goal，版本不一致返回 409 `GOAL_VERSION_CONFLICT`，
不会写入计划、考试或提醒。已确认预览重复确认仍是幂等成功。

## 6. 边界情况处理

| 情况 | 行为 |
|---|---|
| 考试早于当前时间 | 抛 `EXAM_IN_PAST` |
| 考试时间为空/无效 | 抛 `EXAM_TIME_INVALID` |
| 没有待安排任务 | 返回全 0 + `NO_OPEN_TASK` |
| 没有可用窗口 | 返回缺口 + `NO_FREE_WINDOW` |
| 必做超过可用时间 | `required_gap_minutes > 0`，不缩短必做时长 |
| 选做超过剩余时间 | `unscheduled_optional_minutes > 0`，单独统计 |
| 每日达到上限 | 当天不再排，原因 `DAILY_LIMIT_REACHED` |
| 同一天多段窗口 | 依次填，不跨段拼接（可拆分任务除外） |
| 课程与窗口重叠 | 重叠部分被扣掉，窗口可能被切成两段 |
| 已完成的时间块 | 不计入可用时间，任务也不重排 |
| 已经开始的时间块 | 不计入可用时间；任务本身仍可改排到别处 |
| 锁定的时间块 | 不计入可用时间 |
| 其他用户的数据 | 全部按 `user_id` 过滤；考别人的考试抛 `EXAM_NOT_FOUND` |
| 考试时间变化 | 每次调用重新读库，结果随考试时间变化 |
| 跨午夜窗口 | 按零点切成两段，分属两天，各算当日上限 |
| 两段首尾相接 | 合并成一段（60+60 可以放 120 分钟任务） |
| 两段部分重叠 | 合并，容量不重复计算 |

---

## 7. 已知限制

1. **不会找计划之外的空档**：只有计划里已有的可移动学习块算候选时间。
2. **不是全局最优**：贪心 + 大任务优先，极端情况下可能仍有可优化空间。
3. **没有"学习中"状态**：`tasks.status` 只有 `todo/doing/done`；`doing` 的任务
   仍会被重排（只有关联时间块"已开始"才保护）。
4. **拆分粒度 15 分钟**：小于 15 分钟的碎片不单独占一段（除非正好排完）。
5. **新增可用时段的入口还没做**：后续 `capacity_choice(add_windows)` 需要给
   `generate_schedule` 加一个 `extra_windows` 参数（本轮没加，避免提前设计接口）。

---

## 8. 怎么运行

```bash
cd UniPulse_agent
source .venv/bin/activate

export UNIPULSE_TIME_MODE=demo
export UNIPULSE_DEMO_NOW=2026-11-30T09:00:00+08:00

python -m demo.seed
python - <<'PY'
from services.scheduler import generate_schedule

r = generate_schedule("us_demo_a", "ex_demo_ml_final")
print(r["required_minutes"], r["scheduled_required_minutes"], r["required_gap_minutes"])
print(r["optional_minutes"], r["scheduled_optional_minutes"], r["unscheduled_optional_minutes"])
print(r["explanation"])
PY
```

期望输出（考试 12-04）：

```
150 150 0
80 80 0
考试「机器学习期末考试」在 2026-12-04 14:00 开始；……
```

把考试改成 12-02 14:00 后再跑，应变成：

```
150 150 0
80 0 80
```

---

## 9. 下一步

1. `services/previews.py`：把排程结果写进 `previews`（草稿），确认后单事务提交
2. `capacity_choice`：支持"增加可用时段 / 移出选做"两种选择后重算
3. 预览模块需要 `services/scheduler.py` 提供 `extra_windows` 参数（增加时段用）

这些都要等 API 契约里的预览接口落地，本轮不做。
