# Git 协作说明

> 三个人一起改一个仓库，最容易出事的是「两个人改同一个文件」和「把密钥提交上去」。
> 这份文档只讲这两件事和日常操作，5 分钟能看完。

---

## 1. 仓库信息

| 项 | 值 |
|---|---|
| 地址 | `https://github.com/zcxiaoxiannv521-coder/UniPulse_agent`（**私有**，需登录 GitHub 账号才能访问） |
| 主分支 | `main`，任何时候都应该能跑起来 |
| 本地目录 | 每个人自己电脑上的 `unipulse/` |

---

## 2. 队友第一次接入

1. 仓库主人在 GitHub 页面点 **Settings → Collaborators → Add people**，输入队友的 GitHub 用户名
2. 队友在邮箱里点接受邀请
3. 队友在终端执行：

```bash
git clone https://github.com/zcxiaoxiannv521-coder/UniPulse_agent.git
cd unipulse
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m database.connection
pytest -q
```

看到 `3 passed` 就说明环境没问题。

---

## 3. 分支怎么用

| 谁 | 分支名 | 改哪里 |
|---|---|---|
| A | `feat/a-wecom` | `agent/`、`integrations/` |
| B | `feat/b-data` | `database/`、`services/`、`api/`、`tests/`、`demo/` |
| C | `feat/c-web` | `web/` |

日常流程：

```bash
git checkout main
git pull                      # 先同步别人的改动
git checkout -b feat/b-data   # 第一次建分支，之后不用再建
# ……写代码……
git add -A
git commit -m "feat: 实现七天排程与缺口计算"
git push -u origin feat/b-data
```

**每天晚上**把分支合并回 `main`（三个人都在 `main` 上工作，冲突早发现早解决）：

```bash
git checkout main
git pull
git merge feat/b-data
git push
```

3 人小团队可以不用 Pull Request，但**合并前必须在群里说一声**，避免同时往 `main` 推。

---

## 4. 提交信息怎么写

一句话说清「做了什么」，中文即可：

```
feat: 实现七天排程与缺口计算
fix: 修复过期预览仍能确认的问题
docs: 补充确认流程时序
chore: 初始化项目骨架
```

前缀：`feat` 新功能 / `fix` 修 bug / `docs` 文档 / `test` 测试 / `chore` 杂项。

---

## 5. 绝对不能提交的东西

| 文件 | 原因 |
|---|---|
| `.env` | 含企微机器人密钥 |
| `data/*.db` | 本地数据库，队友自己建 |
| `.venv/` | 虚拟环境，几百 MB |
| `.streamlit/secrets.toml` | 可能含真实凭据 |
| 真实学生姓名、完整聊天记录 | 隐私 |

`.gitignore` 已经覆盖了这些。**提交前扫一眼 `git status`**，确认没有红色告警里的敏感文件。

如果不小心提交了密钥：立刻去企微后台重置密钥，然后告诉我，不要只删文件了事（历史记录里还在）。

---

## 6. 冲突了怎么办

```bash
git pull
# 提示 CONFLICT，打开冲突文件，会看到 <<<<<<< ======= >>>>>>>
# 改成你想要的样子，删掉这些标记
git add <冲突文件>
git commit
```

**不要**用 `git push -f` 覆盖别人的提交。真搞不定就在群里喊，别硬来。

---

## 7. 演示前的检查

```bash
git checkout main
git pull
pytest -q
python -m database.connection
python -m uvicorn api.app:app --port 8000
```

全过了再打标签，方便回滚：

```bash
git tag demo-v1
git push --tags
```

---

## 8. 每天 21:00 联调

用同一套数据跑三条链路，记录「今天通了什么、卡在哪」。
数据由 B 的 `python -m demo.seed --reset` 重置，C 只读不改，避免两个人同时改数据把演示搞坏。
