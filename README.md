# UniPulse 校园智能助手

把散在聊天里的校园通知，变成可查看、可更新的个人安排。

- **主入口：** 企业微信私聊——贴通知、确认变更、说「发一下计划表」
- **看板：** 手机可读的个人网页——看当前课表、任务和已确认的学习计划
- **主场景：** 单门考试备考（考试改期 → 局部重排 → 时间不足协商）

> 方案依据：《UniPulse 校园智能助手 Agent 开发方案 V4》。
> 比赛时间：2026-11-20 ~ 12-05 开发，**12-06 提交**。功能 12-01 冻结。

---

## 当前进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| 第一阶段 | 项目初始化、目录结构、Git、建库脚本、冒烟测试 | ✅ 完成 |
| 第二阶段 | 接口契约、数据表设计、协商清单 | ✅ 完成（文档，待三方确认） |
| 第三阶段 | 时间管理（`services/clock.py`）、演示数据（`demo/seed.py`）、只读查询（`services/queries.py`） | ✅ 完成 |
| 第三阶段·接口 | 认证（`api/auth.py`）+ 5 个只读 HTTP 接口 | ✅ 完成 |
| 第三阶段·排程 | 单门考试排程 `services/scheduler.py` | ✅ 完成（已接入预览） |
| 第三阶段·预览 | 预览草稿 `services/preview.py` | ✅ 完成（已接 HTTP） |
| 第三阶段·确认 | 确认提交 `confirm_preview`（单事务、幂等、保护旧块） | ✅ 完成 |
| 第三阶段·改期 | 改期预览 + 确认时同事务更新考试 | ✅ 完成 |
| 第三阶段·兼容 | `/v1/*` 兼容接口（workspace/预览/确认） | ✅ 完成 |
| 第三阶段·待做 | 提醒排期、写接口（goals/classes/progress） | ⬜ 未开始 |
| 第四阶段 | 企微接入（A）、看板（C）、联调 | ⬜ 未开始 |

**现在仓库里没有排程算法、没有 Agent、没有企微代码。** 这是故意的——先把时间基准、演示数据、只读接口对齐，再写会改数据的逻辑。

---

## 目录结构

```
unipulse/
├── agent/               # A：意图判断、工具调度、运行状态
├── integrations/        # A：企微接入、消息去重、身份注入
├── api/                 # B：HTTP 接口层（供 C 的网页调用）
│   ├── app.py           #   FastAPI 入口 + /api/v1/*
│   └── compat.py        #   /v1/* 兼容层（对齐 C 现有页面）
├── database/            # B：数据库
│   ├── connection.py    #   连接工具 + 一键建库
│   └── schema.sql       #   12 张表的建表脚本
├── services/            # B：业务逻辑
│   ├── clock.py         #   统一时间入口（real / demo 两种模式）
│   ├── queries.py       #   只读查询（上下文 / 看板）
│   ├── scheduler.py     #   单门考试排程（只读，不写库）
│   ├── preview.py       #   排程预览草稿与确认提交（单事务）
│   └── workspace.py     #   给 C 的 workspace 聚合（只读）
├── tests/               # B：测试
│   ├── test_smoke.py    #   冒烟测试：建库、接口、连接
│   ├── test_clock.py    #   时间模式、时区、错误配置、跨天
│   └── test_seed.py     #   演示数据、幂等、关联、回滚
├── demo/                # B：脱敏演示数据（固定 11-30 场景）
│   └── seed.py          #   一键初始化演示数据
├── web/                 # C：Streamlit 个人看板
├── docs/
│   ├── API_CONTRACT.md  #   ★ 接口契约：A/B/C 怎么对接
│   ├── DATA_SCHEMA.md   #   ★ 数据表设计
│   ├── DEVELOPMENT_GUIDE.md # ★ 协作流程：环境、分支、提交、合并、冲突
│   ├── SCHEDULER.md     #   排程算法说明（规则、返回结构、边界）
│   ├── PREVIEW.md       #   预览服务说明（差异、有效期、版本检查）
│   ├── C_INTEGRATION_GAPS.md # C 网页与 B 后端的接口缺口
│   ├── INTERFACE_REVIEW.md  # ★ 给 A / C 的接口审核说明
│   ├── GIT_WORKFLOW.md  #   分支与安全速查（详见 DEVELOPMENT_GUIDE）
│   └── OPEN_QUESTIONS.md#   ★ 待三方确认的问题
├── data/                # SQLite 数据库文件（不入库）
├── requirements.txt
├── .env.example
└── README.md
```

`agent/`、`integrations/`、`web/` 现在只有 README，**空目录占位是为了让三个人一拉代码就知道自己该在哪干活**，里面没有实现代码。

---

## 快速开始

### 0. 克隆仓库

```bash
git clone https://github.com/zcxiaoxiannv521-coder/UniPulse_agent.git
cd UniPulse_agent
```

> 克隆下来的文件夹叫 `UniPulse_agent`，不是 `unipulse`。后面所有命令都在这个文件夹里执行。

### 1. 建虚拟环境并装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

> 已经建好 `.venv` 的话，只需要每次 `source .venv/bin/activate`。
> 团队协作的分支、提交、合并规则见 [`docs/DEVELOPMENT_GUIDE.md`](docs/DEVELOPMENT_GUIDE.md)。

### 2. 建数据库 + 演示数据

```bash
python -m database.connection     # 建表
python -m demo.seed               # 写入演示数据（可重复执行）
```

看到 `✅ 数据库已初始化：.../data/unipulse.db` 和 `✅ 演示数据已写入：...` 就成功了。
演示数据是 11-30 的固定场景，详见 [`demo/README.md`](demo/README.md)。

### 3. 启动后端

```bash
python -m uvicorn api.app:app --reload --port 8000
```

浏览器打开：

- <http://127.0.0.1:8000/health> —— 应返回 `{"status":"ok",...}`
- <http://127.0.0.1:8000/docs> —— 自动生成的接口文档

### 4. 跑测试

```bash
python -m pytest -q
```

应看到 `24 passed`。

### 5. 查看数据库

```bash
sqlite3 data/unipulse.db ".tables"
sqlite3 data/unipulse.db "SELECT id, title FROM tasks;"
```

---

## 时间模式：real 与 demo

演示要求「今天」固定在 2026-11-30 上午。所有业务时间都必须从 `services/clock.py` 取，**不要用 `datetime.now()`**，否则 12 月 6 日答辩时「今天」会变成真实日期，缺口全算错。

| 模式 | 含义 | 什么时候用 |
|---|---|---|
| `real` | 真实当前时间（**默认**） | 平时开发 |
| `demo` | 固定在 `UNIPULSE_DEMO_NOW` | 跑演示、录视频、联调固定场景 |

切换方式（只影响当前终端）：

```bash
# 演示模式
export UNIPULSE_TIME_MODE=demo
export UNIPULSE_DEMO_NOW=2026-11-30T09:00:00+08:00

# 改回真实时间
export UNIPULSE_TIME_MODE=real
```

自查当前用的是哪种时间：

```bash
python -m services.clock
```

代码里这样用：

```python
from services.clock import get_now, get_today, get_mode

now = get_now()        # 带 +08:00 时区的 datetime
today = get_today()    # date
```

配置写错（模式拼错、时间不带时区、时区名不存在）会直接抛 `ClockConfigError`，不会悄悄退回真实时间。

---

## 接口与认证

### 两种认证模式

| 模式 | 触发方式 | 说明 |
|---|---|---|
| `demo` | 默认 | 用固定的演示密钥，可直接签发 Token，适合本地开发和演示 |
| `production` | `UNIPULSE_AUTH_MODE=production` | 必须设置 `UNIPULSE_TOKEN_SECRET`（≥32 位），否则服务**启动失败** |

演示模式签的 Token 带 `mode` 标记，拿到正式模式下会被拒绝（反过来也一样）。这是为了防止"演示凭据混进正式环境"。

### 生成演示 Token

```bash
python -m api.auth --status          # 看当前模式与密钥是否就绪
python -m api.auth --user us_demo_a  # 给演示用户签一个 2 小时的 Token
```

### 只读接口

| 方法 | 路径 | 说明 | 认证 |
|---|---|---|---|
| GET | `/health` | 健康检查 | 不需要 |
| GET | `/api/v1/dashboard` | 看板数据（7 天时间轴、下一节课、待确认） | 需要 |
| GET | `/api/v1/courses` | 课表，可带 `date_from` / `date_to` | 需要 |
| GET | `/api/v1/exams` | 今天及以后的考试 | 需要 |
| GET | `/api/v1/tasks` | 待办，可带 `status=todo\|doing\|done` | 需要 |
| GET | `/api/v1/plans/current` | 当前正式计划与时间块 | 需要 |

Token 两种传法（二者任选）：

```bash
# 1) 请求头（A 的脚本、curl 用这种）
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/api/v1/dashboard

# 2) 查询参数（C 的看板链接用这种）
curl "http://127.0.0.1:8000/api/v1/dashboard?token=<token>"
```

**不要把 `user_id` 当参数传**——接口只认 Token 里的身份，传了也会被忽略。

### Swagger 里怎么测

本项目用的是自定义 `authorization` 头参数，所以 Swagger 里**没有**右上角的 Authorize 按钮，直接填参数即可：

1. 启动服务后打开 <http://127.0.0.1:8000/docs>
2. 展开任意 `/api/v1/*` 接口，点 **Try it out**
3. 在 `authorization` 一栏填：`Bearer <token>`（`Bearer` 和 Token 之间有一个空格）
4. `token` 一栏留空（它是给看板链接用的备选方式）
5. 点 **Execute**，看下方的 Response body

先拿 Token：

```bash
python -m api.auth --user us_demo_a
```

---

## 三人分工（V4 第 4 节）

| 成员 | 负责 | 主要开发目录 |
|---|---|---|
| **A：企微与 Agent** | 私聊收发、意图判断、工具选择、确认回复、计划表链接 | `agent/`、`integrations/` |
| **B：数据与执行**（我） | 课表/任务/目标、规则排程、预览、版本确认、提醒队列 | `database/`、`services/`、`api/` |
| **C：网页与演示** | 手机看板、通知证据与前后差异、脱敏样例、验收答辩 | `web/`、页面相关文件 |

> 目录归属约定：A 只改 `agent/`、`integrations/`；B 只改 `database/`、`services/`、`api/`、`tests/`、`demo/`；C 只改 `web/`。跨目录改动先在群里说一声，避免两个人同时改一个文件。

---

## 三条链路的对接方式

```
A（企微+Agent） ── 同进程 import ──► B（services + SQLite）
C（Streamlit）  ── HTTP JSON ─────► B（api + services + SQLite）
```

详细字段、错误码、确认流程见 [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md)。

**三条不能破的规矩：**

1. **用户隔离**：`user_id` 只能来自可信身份（企微回调 / 签名 token），绝不来自通知原文或模型输出。
2. **确认前不改正式数据**：预览只写 `previews` 表，`plans` / `tasks` / `exams` 在确认后一个事务里改。
3. **不猜、不硬编码**：缺信息就追问；缺口数字（230 / 150 / 80）必须由代码算。

---

## 开发约定

- **时间格式**：日期 `2026-12-02`，时间 `14:00`，日期时间 `2026-12-02T14:00:00+08:00`
- **时长**：单位一律分钟，整数
- **金额**：无（本项目不涉及）
- **ID**：带前缀字符串，`us_` / `cr_` / `ex_` / `tk_` / `pl_` / `pv_` …
- **密钥**：只放 `.env`，`.env` 不进仓库
- **提交前**：跑一遍 `pytest -q`

---

## 常见问题

**Q：`python -m database.connection` 报 `ModuleNotFoundError: No module named 'database'`？**
A：确认当前目录是 `unipulse/`（不是它的上一级），并且虚拟环境已激活。

**Q：`pytest` 找不到命令？**
A：用 `.venv/bin/python -m pytest`，或先 `source .venv/bin/activate`。

**Q：`data/unipulse.db` 能提交吗？**
A：不能。已被 `.gitignore` 忽略；队友自己跑一次建库即可。

**Q：端口 8000 被占用？**
A：换端口，例如 `--port 8010`。

**Q：手机打不开 127.0.0.1？**
A：`127.0.0.1` 只在本机有效。手机访问要用电脑局域网 IP（`ipconfig getifaddr en0`），或内网穿透。这是已知风险，见 `docs/OPEN_QUESTIONS.md` C7。
