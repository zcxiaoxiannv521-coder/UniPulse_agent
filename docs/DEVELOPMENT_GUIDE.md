# 团队开发指南（DEVELOPMENT_GUIDE）

> **适用对象：** A（企微与 Agent）、B（数据与执行）、C（网页与演示）
> **目标：** 任何人照着这份文档，从零把项目跑起来，并且知道自己的代码该放哪、怎么提交、怎么合并。
> **更新：** 2026-11-20 · 维护人 B

---

## 0. 先搞清楚三件事

### 0.1 谁负责哪块

| 成员 | 职责 | 只改这些目录 |
|---|---|---|
| **A** | 企微接入、LLM API、意图识别、工具调用、多轮对话 | `agent/`、`integrations/` |
| **B** | 数据库、通知匹配、排程、预览与确认、版本、看板接口 | `database/`、`services/`、`api/`、`tests/`、`demo/` |
| **C** | Streamlit 看板、数据展示、通知来源与计划对比 | `web/` |

### 0.2 公共文件（改动前必须先说）

下面这些文件三个人都会碰到，**改之前先在群里说一句「我要动 xxx」**，避免两人同时改一个文件：

| 文件 | 为什么会冲突 | 建议 |
|---|---|---|
| `README.md` | 三个人都会想补充说明 | 各自加自己那节，别重写别人写的 |
| `requirements.txt` | 各自加依赖 | 按字母顺序追加，合并前先 pull |
| `.env.example` | 新增配置项 | 只加空值样例，永不写真实值 |
| `.gitignore` | 偶尔要加忽略规则 | 加之前确认不会误伤别人的文件 |
| `docs/*.md` | 契约类文档 | 改契约必须三方确认 |
| `database/schema.sql` | 建表脚本 | B 负责，A/C 只读 |
| `api/app.py` | 接口入口 | B 负责，C 只调用不修改 |

### 0.3 三条铁律

1. **不直接改 `main`**。`main` 永远是能跑起来的稳定版本。
2. **不提交密钥**。`.env`、token、企微 Secret 一律不进仓库。
3. **不 `git push -f`**。强推会覆盖别人的提交，出了事很难恢复。

---

## 1. 克隆仓库

仓库是**公开**的，不需要邀请，直接克隆：

```bash
git clone https://github.com/zcxiaoxiannv521-coder/UniPulse_agent.git
cd UniPulse_agent
```

> 注意：克隆下来的文件夹叫 `UniPulse_agent`，不是 `unipulse`。
> 如果你改过文件夹名，后面的命令记得换成你自己的名字。

确认克隆成功：

```bash
git log --oneline -3      # 应该能看到最近的提交
ls                        # 应该看到 agent/ api/ database/ docs/ services/ tests/ web/ 等
```

---

## 2. Python 环境

### 2.1 版本选择：用 3.12，不用 3.14

| 版本 | 状态 | 说明 |
|---|---|---|
| Python 3.12 | ✅ **团队统一用这个** | Streamlit、FastAPI、pyarrow 生态支持最完整 |
| Python 3.13 | ⚠️ 可用但没验证 | 遇到怪问题先怀疑版本 |
| Python 3.14 | ⚠️ 本机现状，仅 B 已验证核心依赖 | 见 2.4 的实测结论 |

**为什么统一 3.12：** 三个人的环境要一致，否则会出现「你那边能跑我这边报错」。Streamlit 的依赖链（pyarrow / pandas / numpy）在 3.12 上是最稳的。

### 2.2 安装 Python 3.12（如果本机没有）

先检查：

```bash
python3.12 -V
```

有输出（如 `Python 3.12.10`）就跳过本节。

没有的话，两种方式任选：

**方式一：官网安装包（推荐，最简单）**

1. 打开 <https://www.python.org/downloads/>
2. 下载 macOS 版 **Python 3.12.x**
3. 双击安装，一路下一步
4. 新开一个终端，`python3.12 -V` 应该能看到版本号

**方式二：conda（如果你已经在用 Anaconda）**

```bash
conda create -n unipulse python=3.12 -y
conda activate unipulse
```

> conda 方式下不需要再建 `venv`，后面第 2.3 节可以跳过。

### 2.3 建虚拟环境并安装依赖

在项目根目录（`UniPulse_agent/`）执行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

说明：

- `python3.12 -m venv .venv`：在项目里建一个独立的 Python 环境，装在 `.venv/` 目录
- `source .venv/bin/activate`：**激活**它。激活后命令行的 `python` 指的是这个环境
- 每次新开终端都要重新 `source .venv/bin/activate`，看到行首出现 `(.venv)` 就说明激活了

验证：

```bash
python -V                 # 应显示 Python 3.12.x
python -c "import fastapi, pydantic; print('依赖正常')"
```

**C 需要额外装网页依赖**（Streamlit 比较重，不放进公共 `requirements.txt`，避免 A、B 也装一遍）：

```bash
python -m pip install -r requirements-web.txt
```

这个文件里是 `-r requirements.txt` 加一行 `streamlit`，装完 C 就能跑 `streamlit run web/app.py`。

### 2.4 实测记录：Python 3.14 能不能用

B 在 2026-11-20 用本机 Python 3.14.6 做了实测（结论见下，供参考，不代表团队应该用 3.14）：

| 检查项 | 结果 |
|---|---|
| FastAPI 0.165.0 | ✅ 正常安装、`/health` 能访问 |
| Pydantic 2.13.4 | ✅ 正常 |
| pytest 9.0.3 | ✅ 3 项测试通过 |
| Streamlit 依赖（pyarrow 26 / pandas 3.1 / numpy 2.5） | ✅ 有 macOS cp314 轮子 |
| Streamlit 1.67.0 本体 | ✅ 临时环境安装成功，`import streamlit` 正常 |

**结论：** 3.14 目前跑得通，但团队仍然统一 3.12，原因不是「跑不起来」，而是：

1. **三个人环境要一致**，否则出现「你那边能跑我这边报错」时，排查成本远高于换版本
2. A 的企微 SDK 和 LLM 客户端在 3.12 上验证过的人更多，出问题好搜
3. 3.14 是 2025 年才发布的版本，少数第三方库还在跟进，没必要在比赛前冒这个险

换句话说：**3.14 不是不能跑，是不值得赌。**

**迁移到 3.12 的操作（不删除现有环境）：**

```bash
# 1. 保留现有 .venv（不删），新建一个 3.12 环境
python3.12 -m venv .venv312

# 2. 用新环境装依赖
source .venv312/bin/activate
python -m pip install -r requirements.txt

# 3. 验证
python -V
python -m pytest -q
```

两个环境可以共存。确认 `.venv312` 一切正常后，再决定要不要把旧的 `.venv` 删掉（**不要现在删**，保留回退余地）。

要换回旧环境，直接 `source .venv/bin/activate` 即可。

### 2.5 依赖装不上怎么办

| 现象 | 原因 | 解决 |
|---|---|---|
| `python3.12: command not found` | 没装 3.12 | 回到 2.2 装 |
| `No module named venv` | 发行版精简版 Python | 换官网安装包 |
| 下载超时 | 网络 | 换镜像：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` |
| `error: externally-managed-environment` | 没用虚拟环境 | 先 `source .venv/bin/activate` |

---

## 3. 启动 FastAPI

**第一次**先建数据库：

```bash
python -m database.connection
```

看到 `✅ 数据库已初始化：.../data/unipulse.db` 就成功了。这个命令可以重复执行，已存在的表不会重建。

启动服务：

```bash
python -m uvicorn api.app:app --reload --port 8000
```

- `--reload`：改代码自动重启（开发时用）
- `--port 8000`：端口，被占用就换 8010

验证：

```bash
curl http://127.0.0.1:8000/health
```

应返回：

```json
{"status":"ok","version":"0.1.0","database":".../data/unipulse.db","database_exists":true}
```

浏览器打开 <http://127.0.0.1:8000/docs> 能看到自动生成的接口文档。

> 现在只有 `/health` 和 `/` 两个接口是真的，其余接口在 `docs/API_CONTRACT.md` 里只是设计，尚未实现。

停止服务：在终端按 `Ctrl + C`。

---

### 3.1 演示模式与演示数据（演示前必看）

演示要求「今天」固定为 2026-11-30。时间由 `services/clock.py` 统一提供，切换靠环境变量：

```bash
export UNIPULSE_TIME_MODE=demo
export UNIPULSE_DEMO_NOW=2026-11-30T09:00:00+08:00

python -m services.clock          # 自查：应显示 mode=demo、2026-11-30T09:00:00+08:00
```

写入演示数据（幂等，可重复执行）：

```bash
python -m demo.seed
```

只想重来（会丢弃对演示数据的修改）：

```bash
python -m demo.seed --reset
```

**三条纪律：**

1. 业务代码取时间一律用 `from services.clock import get_now`，不要写 `datetime.now()`
2. 演示时间只放在环境变量里，不能写进 Python 代码
3. 配置写错会报 `ClockConfigError`，这是故意的——不要改成"出错就用真实时间"

### 3.2 认证与 Token（调接口前必看）

所有 `/api/v1/*` 接口都要 Token。演示环境这样拿：

```bash
python -m api.auth --status          # 看当前模式
python -m api.auth --user us_demo_a  # 签发 2 小时有效的 Token
```

用法二选一：

```bash
# 请求头
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/api/v1/dashboard

# 查询参数（看板链接）
curl "http://127.0.0.1:8000/api/v1/dashboard?token=<token>"
```

两种模式：

| 模式 | 何时用 | 密钥 |
|---|---|---|
| `demo`（默认） | 本地开发、演示 | 内置演示密钥，免配置 |
| `production` | 正式部署 | 必须设 `UNIPULSE_TOKEN_SECRET`（≥32 位），否则启动失败 |

**不要**把 `user_id` 传给接口——身份只从 Token 里取。

---

## 4. 运行测试

```bash
python -m pytest -q
```

应看到：

```
3 passed
```

`pytest` 会执行 `tests/` 下所有 `test_*.py`。改了 `services/` 或 `api/` 的代码后，**提交前必须跑一遍**。

常用参数：

```bash
python -m pytest -q tests/test_smoke.py     # 只跑某个文件
python -m pytest -q -k health              # 只跑名字含 health 的用例
python -m pytest -q -v                     # 显示每个用例名字
```

---

## 5. 分支模型

### 5.1 三条分支，各管一段

```
main      ← 稳定版，只有里程碑才合并（演示前、提交前）
 │
dev       ← 日常整合，三个人每天往这里合
 │
 ├── feature/agent      A 的开发分支
 ├── feature/backend    B 的开发分支
 └── feature/web        C 的开发分支
```

| 分支 | 谁往里合 | 什么时候 | 规则 |
|---|---|---|---|
| `feature/agent` | A | 随时 | 只改 `agent/`、`integrations/` |
| `feature/backend` | B | 随时 | 只改 B 的目录 |
| `feature/web` | C | 随时 | 只改 `web/` |
| `dev` | 三个人 | **每天至少一次** | 合并前必须 `pytest` 通过 |
| `main` | 由 B 统一操作 | 里程碑（11/23、11/26、11/29、12/1） | 只能从 `dev` 合，合完打 tag |

### 5.2 第一次拿到仓库后

```bash
git checkout dev
git pull origin dev                 # 如果远程还没有 dev，见下方说明
git checkout -b feature/backend     # 换成你自己的分支名
```

> 远程 `dev` 分支要等第一次推送后才存在。如果 `git pull origin dev` 报 `couldn't find remote ref`，说明还没人推过，直接用 `git checkout -b dev` 从 `main` 拉一条即可。

---

## 6. 提交代码

### 6.1 每天的标准动作

```bash
# 1. 先切到自己的分支
git checkout feature/backend

# 2. 把 dev 的最新改动吸收进来（避免越拖越难合）
git fetch origin
git merge origin/dev

# 3. 写代码……

# 4. 看看自己改了什么
git status
git diff

# 5. 跑测试
python -m pytest -q

# 6. 提交
git add -A
git commit -m "feat: 实现七天排程与缺口计算"

# 7. 推送
git push -u origin feature/backend
```

### 6.2 提交信息怎么写

格式：`类型: 一句话说明`

| 类型 | 用于 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修 bug |
| `docs` | 只改文档 |
| `test` | 只改测试 |
| `refactor` | 重构，行为不变 |
| `chore` | 杂项（配置、依赖） |

例子：

```
feat: 实现按用户隔离的课表查询
fix: 修复过期预览仍能确认的问题
docs: 补充 A 的接口调用示例
```

**不要**写 `update`、`修改`、`111` 这种看不出内容的提交信息。

### 6.3 提交前检查清单

- [ ] `git status` 里没有 `.env`、`data/*.db`、`.venv`
- [ ] `python -m pytest -q` 全绿
- [ ] 没有把调试用的 `print` 留下的密码、token
- [ ] 提交信息能看懂

---

## 7. 合并代码

### 7.1 自己的分支 → `dev`（每天）

```bash
git checkout dev
git pull origin dev
git merge --no-ff feature/backend -m "merge: B 的排程与预览"
python -m pytest -q            # 合并后再跑一次测试
git push origin dev
```

`--no-ff` 的作用：保留「这里合并过一次」的记录，出问题时容易回退。

**合并前在群里说一声**，避免两个人同时推 `dev` 产生冲突。

### 7.2 `dev` → `main`（里程碑）

只有 B 操作，并且要满足：

1. `python -m pytest -q` 全绿
2. 手动跑通一次完整流程（建库 → 启服务 → 访问 `/health`）
3. 群里通知过

```bash
git checkout main
git pull origin main
git merge --no-ff dev -m "release: 11/23 里程碑"
git push origin main
git tag -a v0.2-1123 -m "第二次交付：通道跑通"
git push origin v0.2-1123
```

### 7.3 关于 Pull Request

3 人小团队不强制用 PR。想用的话：在 GitHub 上从 `feature/xxx` 向 `dev` 开 PR，另一个人点 Approve 后合并。好处是改动有记录、能评论；坏处是多两步操作。

**建议：** 涉及公共文件（README、requirements、docs）的改动走 PR，其他直接合并。

---

## 8. 遇到冲突怎么办

冲突的意思是：你和别人改了**同一个文件的同一处**，Git 不知道该听谁的。

### 8.1 长什么样

打开冲突文件，会看到：

```
<<<<<<< HEAD
你写的内容
=======
别人写的内容
>>>>>>> feature/agent
```

### 8.2 处理步骤

```bash
git status                        # 看哪些文件冲突（Unmerged paths）
# 打开冲突文件，手工改成最终想要的样子，删掉 <<<<<<< ======= >>>>>>> 三行标记
git add <改好的文件>
git commit                        # 完成合并
python -m pytest -q               # 一定要再测一次
```

### 8.3 处理原则

| 情况 | 做法 |
|---|---|
| 只是两个人都加了新内容 | 两段都留 |
| 同一行改成了不同值 | 想清楚哪边对，或两边都不对，重写 |
| 完全看不懂 | **别硬改**，群里问改这块的人 |
| 想放弃这次合并 | `git merge --abort`，回到合并前 |

### 8.4 预防冲突的办法

- 每天开工先 `git fetch origin && git merge origin/dev`
- 公共文件改动前先说
- 不要长时间不合分支（拖三天以上必然冲突）
- 一个提交只做一件事，别一次改五个文件

---

## 9. 常见错误速查

| 报错 / 现象 | 原因 | 解决 |
|---|---|---|
| `ModuleNotFoundError: No module named 'database'` | 不在项目根目录，或没激活环境 | `cd UniPulse_agent && source .venv/bin/activate` |
| `pytest: command not found` | 没激活虚拟环境 | 用 `python -m pytest`，或先 activate |
| `Address already in use`（端口被占用） | 8000 被别的程序占了 | `--port 8010` |
| `fatal: not a git repository` | 不在仓库目录里 | `cd` 到 `UniPulse_agent` |
| `Permission denied (publickey)` | 用了 SSH 地址但没配 key | 改用 https 地址克隆 |
| `Updates were rejected` | 远程有你本地没有的提交 | `git pull --rebase` 后再 push |
| `error: Your local changes would be overwritten` | 本地有未提交改动 | 先 `git stash`，操作完再 `git stash pop` |

---

## 10. 每天的节奏

| 时间 | 做什么 |
|---|---|
| 开工前 | `git fetch origin && git merge origin/dev`，跑一遍 `pytest` |
| 开发中 | 只改自己的目录；公共文件先沟通 |
| 收工前 | 提交 + 推到自己的 `feature/xxx`，并在群里说「今天做了什么、卡在哪」 |
| 21:00 | 三个人一起跑 `demo` 场景，确认三条链路还在 |

对应 V4 的里程碑：

| 日期 | 要达成的 |
|---|---|
| 11/21 | 通道跑通：手机能打开自己的看板，另一账号看不到 |
| 11/23 | 数据跑通：通知 → 预览 → 确认 → 看板 |
| 11/26 | 改期影响、部分进度、缺口协商 |
| 11/29 | 隔离、过期、重复、回滚检查 |
| 12/1 | **功能冻结**，只修阻断问题 |
| 12/6 | 提交 |

---

## 11. 安全问题（务必看）

- `.env` 里有企微 Secret 和 LLM API Key，**永远不提交**
- 一旦发现密钥进了仓库：立刻去后台**重置密钥**，再处理代码。只删文件没用，历史记录里还在
- 演示数据必须脱敏：用 `us_demo_a` 这种假 ID，不写真实姓名、学号、聊天记录
- 仓库目前是**公开**的，任何人能看到代码，别放任何真实凭据
