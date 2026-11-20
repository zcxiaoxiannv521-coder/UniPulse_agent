# 接口审核说明（给 A 和 C）

> **写给：** 成员 A（企微与 Agent）、成员 C（网页看板）
> **整理：** 成员 B｜2026-11-20
> **配套阅读：** [`API_CONTRACT.md`](API_CONTRACT.md)（完整契约）、[`DATA_SCHEMA.md`](DATA_SCHEMA.md)（数据表）、[`OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md)（待确认清单）
> **目的：** 一句话讲清「你要调什么、传什么、拿到什么、哪些还没有、哪些要你拍板」。

---

## 0. 先看这张表：现在到底实现了什么

| 东西 | 状态 | 你能用吗 |
|---|---|---|
| `GET /health` | ✅ 已实现 | 能 |
| `GET /docs` | ✅ 已实现（FastAPI 自动生成） | 能 |
| 其余 8 个 HTTP 接口 | 📝 只有契约，没有代码 | **不能**，调用会 404 |
| A 要调的 7 个 Python 函数 | 🟡 已实现 2 个（`get_context`、`get_dashboard`），其余 5 个待实现 | 这 2 个能用了，其余不行 |

**这意味着：** A 和 C 现在可以先照契约搭骨架、写界面、定数据结构，但**别指望能跑通**。第三阶段 B 才会把这些填上。

下面所有接口的**字段名已经定稿**，改字段要三方同意；**实现时间**由 B 控制。

---

## 一、给 A（企微与 Agent）

### 1.1 你要调用的 7 个函数

全部从 `services` 包导入，**同进程直接调用**，不走 HTTP：

```python
from services import (
    get_context, record_notice, match_notice,
    preview_change, confirm_preview, cancel_preview,
    get_dashboard,
)
```

| 函数 | 你什么时候调 | 必传入参 | 返回里你最需要的字段 |
|---|---|---|---|
| `get_context` | 用户提新目标、或需要判断冲突前 | `user_id` | `goals` `exams` `sessions` `tasks` `data_version` `missing` |
| `record_notice` | 收到任何一条新消息，先存原文 | `user_id` `wecom_msg_id` `raw_text` `received_at` | `duplicate`（true 就别继续处理） |
| `match_notice` | 你解析完通知，要确认它对应哪条已有记录 | `user_id` `candidate` | `matches` `needs_choice` `questions` `is_stale` |
| `preview_change` | 任何要改数据的动作，**先出预览** | `user_id` `request` | `status`（pending / needs_clarification / rejected）、`preview.confirm_code`、`preview.capacity` |
| `confirm_preview` | 用户回「确认 T03」 | `user_id` + `confirm_code` 或 `preview_id` | `ok` `duplicate` `applied` |
| `cancel_preview` | 用户说「算了」 | `user_id` `preview_id` | 无（成功即返回） |
| `get_dashboard` | 用户要链接时，取数据（其实 C 也会用） | `user_id` | 整个 `Dashboard` |

### 1.2 四种典型场景，你要按这个顺序调

**场景 1：用户第一次说「帮我准备机器学习考试，每天最多 90 分钟」**

```
get_context(user_id)
  → 有 exam、有课表 → preview_change(kind="initial_plan", goal_id=...)
    → status="pending" → 把 summary 和 confirm_code 回给用户
```

**场景 2：用户粘贴一条调课通知**

```
record_notice(user_id, wecom_msg_id, raw_text, received_at)   ← 先存原文
  → duplicate? 是 → 回「这条已经处理过了」
你解析出 candidate
match_notice(user_id, candidate)
  → needs_choice / questions 非空 → 追问，**不要自己猜**
  → matches 命中 → 继续
preview_change(kind="class_moved" 或 "exam_moved", ...)
  → 把「原安排 → 新安排」和 confirm_code 回给用户
```

**场景 3：用户说「确认 T03」**

```
confirm_preview(user_id, confirm_code="T03")
  → ok=true → 回「已更新」
  → 抛 VERSION_CONFLICT / PREVIEW_EXPIRED → 回「计划刚变过，我重新算一版」并重新 preview
```

**场景 4：用户说「发一下计划表」**

```
token = create_dashboard_token(user_id)      ← B 提供，签发短链
回：https://<看板地址>/?token=xxx
```

### 1.3 你不用做的事（避免重复劳动）

| 你以为要自己算的 | 实际由 B 负责 |
|---|---|
| 哪段时间有空、排不排得下 | `preview_change` 内部算，你只读 `capacity` |
| 冲突判定（撞课、撞考试） | `match_notice` / `preview_change` 返回 `conflicts` |
| 缺口数字（230/150/80） | `capacity.required_minutes / scheduled_minutes / gap_minutes` |
| 确认后写哪些表 | `confirm_preview` 事务里做，你只管回话 |
| 用户身份从哪来 | 你从企微回调取，**不能**从消息文本推断 |

### 1.4 你需要确认的问题（详细版见 OPEN_QUESTIONS A1–A8）

| # | 问题 | B 的默认建议 |
|---|---|---|
| A1 | 同进程 `import services` 可以吗？ | 可以，别走 HTTP |
| A2 | 解析字段用 B 定义的 `candidate` 结构吗？ | 用，字段名别自己改 |
| A3 | `confirm_code` 由 B 生成、你只回显？ | 是 |
| A4 | B 返回 `questions[]`，你负责措辞？ | 是，B 给建议问法但不写死话术 |
| A5 | 企微重试时 `wecom_msg_id` 不变吗？ | 必须不变，否则去重失效 |
| A6 | 看板链接谁拼？token 有效期多久？ | B 签发 token（默认 120 分钟），你拼 URL |
| A7 | 消息时间传 UTC 还是 `+08:00`？ | 统一 `+08:00` |
| A8 | 哪些语句走规则、哪些走模型？ | 建议「发计划表」「确认 Txx」走规则 |

---

## 二、给 C（网页看板）

### 2.1 你要调的 9 个接口

基地址：`http://127.0.0.1:8000`（手机访问换局域网 IP，见 C7）

| 方法 | 路径 | 用途 | 状态 |
|---|---|---|---|
| GET | `/health` | 探活，页面右上角显示「后端正常/离线」 | ✅ 已实现 |
| GET | `/api/v1/dashboard` | **主接口**，一次拿全看板数据 | ❌ 未实现 |
| GET | `/api/v1/courses?date_from&date_to` | 单独查课表 | ❌ 未实现 |
| GET | `/api/v1/exams` | 单独查考试 | ❌ 未实现 |
| GET | `/api/v1/tasks?status=` | 单独查待办 | ❌ 未实现 |
| GET | `/api/v1/plans/current` | 当前正式计划（含时间块） | ❌ 未实现 |
| GET | `/api/v1/previews/{preview_id}` | 某个待确认草稿的前后差异 | ❌ 未实现 |
| POST | `/api/v1/previews/{preview_id}/confirm` | 网页备用确认入口 | ❌ 未实现 |
| GET | `/api/v1/notifications/{id}` | 通知原文与来源 | ❌ 未实现 |

**建议：页面只调 `/api/v1/dashboard` 就够了**，其他接口是给「展开详情」用的，第一版可以不做。

### 2.2 页面元素 ↔ 数据字段对照

V4 要求的单页结构，直接对应 `Dashboard` 对象：

| 页面位置 | 用哪个字段 |
|---|---|
| 顶部「下一节课」 | `next_class.title` / `start_at` / `location` |
| 顶部「最近截止」 | `next_deadline.title` / `due_at` / `remaining_minutes` |
| 顶部「待确认」黄条 | `pending_changes[]`（`summary` + `confirm_code` + `expires_at`） |
| 中间按日期分组 | `days[].date` + `days[].items[]` |
| 时间块显示 | `items[].start_at` / `end_at` / `title` / `item_type` / `status` |
| 下方待办 | `tasks[]`（`title` / `due_at` / `status` / `remaining_minutes`） |
| 考试 | `exams[]`（`name` / `exam_date` / `start_time`） |
| 「计划版本」角标 | `plan.version` |
| 「计划待调整」提示 | `plan.note == "计划待调整"` |
| 展开「改了什么」 | `/api/v1/previews/{id}` 的 `diff`（added/changed/removed/kept） |
| 展开「通知来源」 | `notices[]`，或 `/api/v1/notifications/{id}` 看原文 |

`item_type` 的显示建议：`course` 灰底（真实课表，不可动）、`exam` 红边、`task` 蓝底、`rest` 浅色。

### 2.3 token 怎么用

用户从企微点开的链接是：

```
http://<看板地址>/?token=eyJhbGciOi...
```

Streamlit 里这样取：

```python
token = st.query_params.get("token", "")
```

然后**原样**作为查询参数发给后端：

```python
httpx.get(f"{API_BASE}/api/v1/dashboard", params={"token": token})
```

三条规则：

1. **不要自己解析 token**，也不要相信链接里其他参数
2. **不要**加 `user_id` 参数——后端只认 token
3. token 过期（2 小时后）后端返回 `UNAUTHORIZED`，页面提示「链接已过期，请在企微里再发一次『发一下计划表』」

### 2.4 错误怎么显示

后端统一返回：

```json
{"error": {"code": "UNAUTHORIZED", "message": "链接已过期", "details": {}, "request_id": "..."}}
```

| code | 页面怎么处理 |
|---|---|
| `UNAUTHORIZED` | 整页替换成「链接失效」提示，不显示任何数据 |
| `NOT_FOUND` | 该模块显示「没有找到」，不要整页报错 |
| `INTERNAL_ERROR` | 显示「加载失败，请刷新重试」，可重试 |

**没数据不是错误**：`days: []` 表示没有安排，正常渲染空状态，不要弹错误。

### 2.5 你需要确认的问题（详细版见 OPEN_QUESTIONS C1–C7）

| # | 问题 | B 的默认建议 |
|---|---|---|
| C1 | Streamlit 走 HTTP 还是同进程 import？ | 走 HTTP，手机才能验证 |
| C2 | 只调 `/dashboard` 一个接口够不够？ | 够，第一版别拆太细 |
| C3 | 待确认草稿怎么展示？ | 顶部黄条 + 点开看 diff；正式时间轴只渲染 confirmed |
| C4 | diff 的四种分类怎么显示？ | 分组标题：新增 / 修改 / 移除 / 保留 |
| C5 | 刷新方式？ | 手动按钮，演示更可控 |
| C6 | 演示数据谁维护？ | B 提供 `demo/seed.py`，你只读 |
| C7 | 手机怎么访问？ | 局域网 IP 或内网穿透，**这是演示风险点** |

---

## 三、三方一致性检查结果（B 这轮核对的）

| # | 发现 | 影响 | 处理 |
|---|---|---|---|
| 1 | README 写 `cd unipulse`，但克隆后目录叫 `UniPulse_agent` | 新人第一条命令就失败 | ✅ 已改，补了克隆步骤 |
| 2 | `GIT_WORKFLOW` 用旧分支名 `feat/a-wecom`，与新的 `feature/agent` 不一致 | 三个人分支名对不上 | ✅ 已改为速查卡，分支名统一 |
| 3 | 契约里的 `course_name` 在数据库表中不存在 | C 可能以为要自己拼 | ✅ 已在契约注明「联表带出的显示字段」 |
| 4 | `README` 缺「如何克隆」 | 新人不知道从哪开始 | ✅ 已补 |
| 5 | 契约有 9 个 HTTP 接口，代码只有 1 个 | A/C 容易误以为能调 | ✅ 本文件第 0 节写明状态 |

**没有改动的部分：** 数据表结构、接口字段名、错误码、确认流程。这些属于设计，改动要三方确认。

---

## 四、接口要改怎么办

1. 在群里说清楚「改哪个字段、为什么、影响谁」
2. 三方同意后，B 改 `API_CONTRACT.md` 并在文末记一条变更记录
3. 通知 A、C 同步改代码
4. **不允许**其中一方偷偷改字段再让对方适配

新增接口可以，但必须先在 `API_CONTRACT.md` 写清楚入参、返回、错误码，再写代码。
