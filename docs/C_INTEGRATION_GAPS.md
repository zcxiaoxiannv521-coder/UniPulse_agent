# C 网页与 B 后端的接口缺口（C_INTEGRATION_GAPS）

> **整理：** 成员 B｜2026-11-21
> **依据：** `origin/feat/c-web`（提交 `69fdead`）与本地 `feature/backend`（`b34c37c`）
> **状态：** 只记录，不改 C 的代码，也不改现有契约。结论待三方确认。

---

## 1. 事实

| 项 | 情况 |
|---|---|
| C 的分支 | `feat/c-web`（不是约定的 `feature/web`） |
| 提交 | `69fdead feat: 导入成员 C 桌面网页、测试及方案归档` |
| 基于 | `main`（`14384bc`），**不含** `dev` 上的 `DEVELOPMENT_GUIDE`、`INTERFACE_REVIEW` 等文档 |
| 新增文件 | `web/` 下 32 个文件：`app.py`、`client.py`、`view_model.py`、`tests/`、`fixtures/`、`docs/`、原型 HTML、归档 zip |
| 公共文件 | 只改了 `web/README.md`（C 自己的目录），**没有**动 `api/`、`database/`、`docs/`、`README.md` |
| 默认行为 | 本地演示客户端（`DemoClient`），界面上标注为演示数据 |
| 真实后端模式 | 设置 `UNIPULSE_API_BASE` 后启用 `ApiClient`，用 Bearer Token |

C 自己的 `web/docs/INTEGRATION_NOTES.md` 已经列了差异，并且明确写了"不代表已达成共识或实现后台"。这份文档是对它的事实核对与补充。

---

## 2. C 实际调用的接口（`web/client.py`）

| 方法 | 路径 | 请求体 | B 现状 |
|---|---|---|---|
| GET | `/v1/workspace` | — | ❌ 无（B 是 `/api/v1/dashboard`） |
| POST | `/v1/goals` | `{payload, expected_version}` | ❌ 无 |
| POST | `/v1/classes` | `{payload, expected_version}` | ❌ 无 |
| POST | `/v1/tasks/{id}/progress` | `{payload, expected_version}` | ❌ 无 |
| POST | `/v1/tasks/{id}/time` | `{actual_minutes, expected_version}` | ❌ 无 |
| POST | `/v1/notices` | `{text, expected_version}` | ❌ 无 |
| POST | `/v1/previews` | `{event_id, new_start, expected_version, source_notice_ids}` | 🟡 只有 Python 函数，无 HTTP |
| POST | `/v1/actions/{id}/cancel` | `{}` | 🟡 `cancel_preview` 有，无 HTTP |
| POST | `/v1/actions/{id}/confirm` | `{}` | ❌ 未实现（B 本轮只做校验） |
| POST | `/v1/agent/messages` | `{message}` | ❌ 无（A 的职责） |

### 响应格式

C 的 `ApiClient._request()` 要求：

```json
{"ok": true, "data": { ... }}
```

不是这个结构就报"后端没有返回符合约定的成功结果"。

B 现在返回的是**裸对象**（`Dashboard` 直接作为 body），错误是：

```json
{"error": {"code": "...", "message": "...", "details": {}}}
```

### 错误码映射

C 只按 HTTP 状态码提示（401 凭据失效 / 403 无权限 / 409 数据变化 / 422 输入被拒），
B 的 401 会带 `details.reason`（`TOKEN_EXPIRED` 等），C 目前没有读取。

---

## 3. `workspace` 与 `Dashboard` 的字段差异

C 的 `snapshot()` 强制要求这 12 个 key：

| C 期望 | B 现有字段 | 能否直接映射 | 说明 |
|---|---|---|---|
| `version` | `plan.version`（可能为 null） | ✅ | 需要约定：无计划时是 0 还是 null |
| `profile` | `user`（id/name） | ✅ | `display_name` ← `user.name` |
| `plans` | `/api/v1/plans/current` 的 `plan` | 🟡 | 结构不同，C 是数组 |
| `goals` | 只在 `get_context()` 里，无 HTTP | ❌ | 需要新接口 |
| `events` | 无（课表在 `courses`，考试在 `exams`） | ❌ | 需要合并视图 |
| `classes` | `/api/v1/courses` 的 `items` | 🟡 | 字段名不同（`session_date` vs C 的日期字段） |
| `tasks` | `/api/v1/tasks` 的 `items` | ✅ | 大部分字段同名 |
| `study_blocks` | `/api/v1/plans/current` 的 `items` | 🟡 | C 叫 `study_blocks`，B 叫 `plan_items` |
| `reminders` | 表存在，无接口 | ❌ | P2 才有 |
| `notices` | `dashboard.notices`（只有 40 字摘要） | 🟡 | 无原文详情接口 |
| `pending` | `dashboard.pending_changes`（摘要） | 🟡 | 无 diff 详情 HTTP |
| `activity` | 无 | ❌ | 运行记录表还没做 |

---

## 4. 缺口分级

### P0：演示必需的"看板能显示真实数据"

| 需求 | B 侧现状 | 建议 |
|---|---|---|
| 拿到课表、任务、正式计划 | ✅ 三个接口都有 | C 写适配层即可 |
| 拿到"下一节课/最近截止/待确认" | ✅ `dashboard` 有 | 同上 |
| 版本号展示 | 🟡 在 `plan.version` 里 | C 映射 |

**结论：P0 只靠现有接口 + C 的适配层就能做，不需要 B 改契约。**

### P1：预览与确认（"改期 → 缺口 → 确认"这条主线）

| 需求 | B 侧现状 |
|---|---|
| 生成预览 | 🟡 `create_preview()` 已实现，**仍无 HTTP**（B3 未要求） |
| 查看预览差异 | ✅ `GET /api/v1/previews/{id}`（2026-11-21） |
| 提交确认 | ✅ `POST /api/v1/previews/{id}/confirm`（需带 `confirm_code`） |
| 取消预览 | ✅ `POST /api/v1/previews/{id}/cancel` |

### P2：网页写入与 Agent

`goals`、`classes`、`progress`、`time`、`notices`、`agent/messages` 都属于"写操作/Agent 交互"，
V4 的首版主线是**企微里确认、网页只读展示**，这些不在演示必需范围内。

---

## 5. 两种对接方案

### 方案 A（推荐）：C 写适配层，B 不改契约

C 的 `ApiClient.snapshot()` 内部把 `/api/v1/*` 的结果拼成 `workspace` 结构：

```python
# 示意：C 侧适配，不是 B 侧改动
dash = GET /api/v1/dashboard
tasks = GET /api/v1/tasks
plan = GET /api/v1/plans/current
workspace = {
    "version": (plan["plan"] or {}).get("version", 0),
    "profile": {"display_name": dash["user"]["name"]},
    "tasks": tasks["items"],
    "study_blocks": plan["items"],
    "classes": GET /api/v1/courses["items"],
    ...
}
```

- 优点：B 的契约不动；C 的页面逻辑不受影响；两边可以并行
- 缺点：C 要写 100 行左右的映射；`goals`/`events`/`reminders`/`activity` 仍然缺数据，C 要接受"空数组"或继续用演示值

### 方案 B：B 增加 `/v1/workspace` 兼容端点

- 优点：C 一行不用改就能连上
- 缺点：等于**新增一套并行契约**，以后要维护两套；`{ok,data}` 信封与现有错误格式不一致；**需要三方同意**，B 不擅自做

### 建议

先走 A：**11/23 前让看板显示真实课表、任务、计划**，演示主线不受影响。
等确认流程（`confirm`）做完，再一起决定是否统一成一套 URL/信封。
如果团队决定选 B，B 可以在 1 天内加一个薄的适配端点，但要写进契约并标"过渡层"。

---

## 6. 字段映射建议（给 C，P0 范围）

| C 的 workspace 字段 | 用 B 的哪个接口 |
|---|---|
| `version` | `GET /api/v1/plans/current` → `plan.version`（无计划填 0） |
| `profile.display_name` | `GET /api/v1/dashboard` → `user.name` |
| `classes` | `GET /api/v1/courses` → `items` |
| `tasks` | `GET /api/v1/tasks` → `items` |
| `study_blocks` | `GET /api/v1/plans/current` → `items` |
| `events` | `GET /api/v1/exams` → `items`（考试）；课表用 `classes` |
| `notices` | `dashboard.notices` → `id / received_at / status / summary` |
| `pending` | `dashboard.pending_changes` → `preview_id / confirm_code / summary / expires_at` |
| `plans` | `dashboard.plan` 包成单元素数组 |
| `goals` | 暂无接口，先给 `[]` |
| `reminders` | 暂无接口，先给 `[]` |
| `activity` | 暂无接口，先给 `[]` |

---

## 7. 待确认问题

| # | 问题 | 谁决定 | 影响 |
|---|---|---|---|
| Q1 | 契约以 `/api/v1/*` 还是 `/v1/*` 为准 | 三人 | 决定谁改 URL |
| Q2 | 成功响应用裸对象还是 `{ok, data}` | 三人 | 决定是否加统一信封（改动面大） |
| Q3 | "版本号"指计划版本还是全局数据版本 | B + C | C 的 `expected_version` 语义 |
| Q4 | 预览接口用 `exam_id` 还是 `event_id`；`action_id` 还是 `preview_id` | B + C | 决定预览 HTTP 形态 |
| Q5 | 写接口（goals/classes/progress/notices）是否进首版 | 三人 | 决定 B 的工作量 |
| Q6 | C 的分支是否改名为 `feature/web` 并先合到 `dev` | C | 影响联调与合并顺序 |
| Q7 | 身份方式：链接 token（query）是否足够，还是网页会话登录 | 三人 | C 现在用"会话输入 token"，B 有签名 token |

---

## 8. B 这边的结论

1. **不改 C 的代码**（本轮只读检查）。
2. **不改现有契约**：`/api/v1/*` 与裸对象格式保持不动，等 Q1/Q2 有结论。
3. **P0 不需要 B 加班**：现有接口足够 C 拼出看板。
4. **P1 的缺口是 HTTP 层**：预览的三个函数已经能用，接路由是下一步（约半天）。
5. A 的 Agent 不在本表范围内，`/agent/messages` 归 A。

---

## 9. 下一步（建议顺序）

1. 群里过 Q1–Q7，先定 Q1/Q2/Q4
2. C 按第 6 节映射先接只读看板（不依赖 B 改代码）
3. B 接 `GET /api/v1/previews/{id}` + `POST /api/v1/previews/{id}/confirm`（依赖 `confirm_preview`）
4. 确认流程跑通后，再讨论要不要统一信封
