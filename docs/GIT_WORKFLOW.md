# Git 速查卡

> 完整流程（环境、分支、提交、合并、冲突）见 [`DEVELOPMENT_GUIDE.md`](DEVELOPMENT_GUIDE.md)。
> 这份只做速查，两处不一致时**以 DEVELOPMENT_GUIDE 为准**。

---

## 仓库信息

| 项 | 值 |
|---|---|
| 地址 | `https://github.com/zcxiaoxiannv521-coder/UniPulse_agent`（**公开**，直接 clone，无需邀请） |
| 克隆后的文件夹 | `UniPulse_agent` |

---

## 分支对照

| 分支 | 用途 | 谁合并 |
|---|---|---|
| `main` | 稳定版，演示/提交用 | B，只在里程碑 |
| `dev` | 日常整合 | 三个人每天合一次 |
| `feature/agent` | A：企微与 Agent | A |
| `feature/backend` | B：数据与执行 | B |
| `feature/web` | C：网页看板 | C |

---

## 每天三步

```bash
git checkout feature/backend
git fetch origin && git merge origin/dev     # 先吸收别人的改动
python -m pytest -q                          # 提交前必须全绿
git add -A && git commit -m "feat: 做了什么"
git push -u origin feature/backend
```

合并回 `dev`：

```bash
git checkout dev && git pull origin dev
git merge --no-ff feature/backend
python -m pytest -q && git push origin dev
```

---

## 三条铁律

1. **不直接改 `main`**
2. **不提交密钥**（`.env`、token、企微 Secret）
3. **不 `git push -f`**

---

## 提交前检查

- [ ] `git status` 里没有 `.env` / `data/*.db` / `.venv`
- [ ] `python -m pytest -q` 全绿
- [ ] 提交信息能看懂（`feat:` / `fix:` / `docs:` / `test:` / `chore:`）

---

## 危险操作清单（做了会很难救）

| 命令 | 后果 |
|---|---|
| `git push -f` | 覆盖别人的提交 |
| `git reset --hard` | 丢掉本地未提交的改动 |
| `git clean -fd` | 删掉所有未跟踪文件 |
| `git checkout <文件>` | 丢掉该文件的本地修改 |

真要用，先 `git stash` 或先在群里问一句。
