# web/ —— 成员 C 的工作区（桌面个人看板）

> **状态：** 前端演示已实现；真实 Agent / B 业务接口尚未联调。  
> **范围：** 首页、课表、计划、任务；左侧导航；最终结果优先，处理记录默认收起。  
> **依据：** V4 方案，以及用户后续确认的“电脑端优先、手机端暂缓”。

---

## 1. 当前完成了什么

| 页面 | 功能 |
|---|---|
| 首页 | 本周课表、最近课程/截止、当前计划、任务概览、待确认与最近修改结果 |
| 课表 | 周课表与按日查看、具体日期课程录入、冲突校验 |
| 计划 | 正式/旧计划区分、来源证据、改期预览、确认/取消、备用目标与对话入口 |
| 任务 | 筛选、仅记录用时、明确进度反馈；不把时长推断为完成比例 |

默认使用 `fixtures/workspace.json`，只修改本次网页演示会话，不访问 B 的数据库，不启动机器人，不虚构模型/工具调用或消息送达。

---

## 2. 目录结构

```text
web/
├── app.py                 # Streamlit 界面
├── client.py              # DemoClient + 历史 ApiClient 草案
├── view_model.py          # 展示组织、有效版本判断、有限样例核对
├── fixtures/workspace.json# C 的独立脱敏样例，不替代 B 的 demo/seed
├── tests/                 # 网页功能检查，与 B 的根 tests 分开
├── .streamlit/config.toml # 中大深绿白底主题
├── requirements.txt       # C 的依赖，不改 B 的根 requirements
├── .env.example
├── 启动网页.command       # Mac 启动入口
├── API_CONTRACT.md        # C 独立接口草案，不是仓库统一协议
├── docs/
│   ├── DESKTOP_HANDOFF.md  # 启动、数据准备和人工稳定性检查
│   ├── INTEGRATION_NOTES.md# 与 B 契约的差异，待协商
│   ├── FILE_MANIFEST.md   # 本次提交资料索引与哈希
│   └── plans/             # V2/V3/V4 方案参考
└── archive/
    ├── prototypes/        # 历史 HTML 原型，交互是本地模拟
    ├── mockups/           # 历史排版效果图，不是运行截图
    └── packages/          # 历史独立交付包，不是当前仓库运行入口
```

---

## 3. 启动

从**仓库根目录**运行：

```bash
python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
python -m pip install -r web/requirements.txt   # 等价于根目录的 requirements-web.txt
cd web
python -m streamlit run app.py
```

进入 web 启动可读到 `web/.streamlit/config.toml` 的主题。Mac 创建环境后也可双击 `web/启动网页.command`。不需要额外导入资料、模型密钥或企微凭据。

使用 Python 3.11+，已在 Python 3.12 / Streamlit 1.67.0 验证。此处不提交 `.venv`，换机器需按依赖文件安装。

---

## 4. 测试

从**仓库根目录**运行：

```bash
python -m pip install -r requirements.txt -r web/requirements.txt
python -m pytest -q
```

仅检查 C 的网页：

```bash
python -m unittest discover -s web/tests -v
```

本次导入后完整运行 `pytest -q`：**39 passed，另有 7 个子检查通过**，包含 B 的 3 项原有冒烟测试和 C 的 36 项功能检查。第三方 TestClient 有 1 条弃用提示，未造成测试失败。真实浏览器视觉、长时间稳定性和正式跨用户隔离仍不能只靠这些检查证明。

---

## 5. 与 A/B 怎么对接

统一契约参考 [`../docs/API_CONTRACT.md`](../docs/API_CONTRACT.md)，不能用 C 的旧草案替换 B 的文档。

B 已实现 `/v1/*` 兼容接口（`GET /v1/workspace`、通知、预览、确认、取消、任务进度/用时、目标与课表录入），
C 的 `ApiClient` 已与这些接口对齐；`api/compat.py` 负责把 `/v1` 的 `{ok:true,data}` 信封映射到 B 的服务层。

联调模式：

```bash
export UNIPULSE_API_BASE=http://127.0.0.1:8000
export UNIPULSE_LOCAL_TEST=1
export UNIPULSE_API_TOKEN=<B 签发的测试 Token>
```

不设 `UNIPULSE_API_BASE` 时仍是本地演示模式（`fixtures/workspace.json`）。
两条链路共用同一份页面代码，页面顶部会明确标注当前是"演示数据"还是"共享后端模式"。
接口契约见 [`../docs/API_CONTRACT.md`](../docs/API_CONTRACT.md)（第 5.12 节为 `/v1` 兼容层）。

---

## 6. 用户接下来要做什么

1. **先体验内置样例。** 不需要导入，先检查四页和改期取消/确认。
2. **人工检查电脑浏览器。** 连续切换/保存、重复点击、放置后继续、窗口与缩放；问题记录页面、步骤、预期/实际结果与浏览器。
3. **准备脱敏资料。** 一周具体日期课表、明确考试通知、必做/选做任务、剩余估时与允许时段。当前没有 Excel/PDF 批量导入。
4. **等 B 提供业务接口。** 获取可访问地址、测试账号与返回样例，先对齐契约，再接同一个真实 Agent；手机联调后置。

完整步骤及已知边界见 [`docs/DESKTOP_HANDOFF.md`](docs/DESKTOP_HANDOFF.md)。历史 HTML、效果图和 zip 仅用于追溯设计，当前实现看 `web/app.py`。
