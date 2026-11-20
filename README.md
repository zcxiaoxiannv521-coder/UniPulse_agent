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
| 第三阶段 | 业务代码（课表/任务查询、排程、预览、确认） | ⬜ 未开始 |
| 第四阶段 | 企微接入（A）、看板（C）、联调 | ⬜ 未开始 |

**现在仓库里没有排程算法、没有 Agent、没有企微代码。** 这是故意的——先把接口和数据格式对齐，再各写各的。

---

## 目录结构

```
unipulse/
├── api/                 # HTTP 接口层（供 C 的网页调用）
│   └── app.py           #   FastAPI 入口，目前只有 /health
├── database/            # 数据库
│   ├── connection.py    #   连接工具 + 一键建库
│   └── schema.sql       #   12 张表的建表脚本
├── services/            # 业务逻辑（B 的主要工作区，第二阶段开始写）
├── tests/               # 测试
│   └── test_smoke.py    #   冒烟测试：建库、接口、连接
├── demo/                # 脱敏演示数据（固定 11-30 场景）
├── docs/
│   ├── API_CONTRACT.md  #   ★ 接口契约：A/B/C 怎么对接
│   ├── DATA_SCHEMA.md   #   ★ 数据表设计
│   └── OPEN_QUESTIONS.md#   ★ 待三方确认的问题
├── data/                # SQLite 数据库文件（不入库）
├── requirements.txt
├── .env.example
└── README.md
```

**还没建 `agent/` 和 `integrations/`**：这两块归 A，等 A 开工时自己建，避免出现空目录让人误以为已有实现。

---

## 快速开始

### 1. 进入项目、建虚拟环境

```bash
cd unipulse
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> 已经建好 `.venv` 的话，只需要每次 `source .venv/bin/activate`。

### 2. 建数据库

```bash
python -m database.connection
```

看到 `✅ 数据库已初始化：.../data/unipulse.db` 就成功了。

### 3. 启动后端

```bash
python -m uvicorn api.app:app --reload --port 8000
```

浏览器打开：

- <http://127.0.0.1:8000/health> —— 应返回 `{"status":"ok",...}`
- <http://127.0.0.1:8000/docs> —— 自动生成的接口文档

### 4. 跑测试

```bash
pytest -q
```

应看到 `3 passed`。

### 5. 查看数据库

```bash
sqlite3 data/unipulse.db ".tables"
sqlite3 data/unipulse.db ".schema plans"
```

---

## 三人分工（V4 第 4 节）

| 成员 | 负责 | 本仓库对应的目录 |
|---|---|---|
| **A：企微与 Agent** | 私聊收发、意图判断、工具选择、确认回复、计划表链接 | `agent/`、`integrations/`（A 自己建） |
| **B：数据与执行**（我） | 课表/任务/目标、规则排程、预览、版本确认、提醒队列 | `database/`、`services/`、`api/`、`tests/`、`demo/` |
| **C：网页与演示** | 手机看板、通知证据与前后差异、脱敏样例、验收答辩 | Streamlit 文件（目录待定，见 OPEN_QUESTIONS C1/T2） |

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
