# web/ —— 成员 C 的工作区（桌面个人看板）

> **状态：** 桌面网页与 B 后端已整合到 dev，HTTP 业务链路已验证，界面已统一中文并完成本机页面检查；Agent 与企微成果待入库及联调。
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

界面文案、版本、状态、日历与输入提示已统一中文；通知原文和用户输入保留。默认不配置后台时使用 `fixtures/workspace.json`，只修改本次网页演示会话；共享后端模式通过 API 读写 B 的服务。两种模式均不虚构模型/工具调用或消息送达。

---

## 2. 目录结构

```text
web/
├── app.py                 # Streamlit 界面
├── client.py              # DemoClient + 已对齐 B 兼容接口的 ApiClient
├── i18n.py                # 系统文案、版本与状态中文转换
├── assets/zh-CN.js        # 框架控件的中文提示与无障碍标签
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
│   ├── INTEGRATION_NOTES.md# 早期接口差异记录，现状见根 PROJECT_STATUS
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
python -m pip install -r web/requirements.txt   # 独立网页依赖；完整联调使用 requirements-web.txt
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
python -m pytest -q web/tests
```

2026-11-28 中文界面更新后运行 `pytest -q`：**295 passed，另有 7 个子检查通过**，包含 B 的 241 项测试和 C 的 54 项测试。第三方 TestClient 有 1 条弃用提示，未造成测试失败。本机浏览器已核对四页、日历、版本和状态的中文显示；完整人工业务回归、长时间稳定性、多浏览器及正式跨用户隔离仍待验收。

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

## 6. C 的剩余任务

完整三人分工、依赖与完成标准以 [`../PROJECT_STATUS.md`](../PROJECT_STATUS.md) 为准。

| 任务 | 状态 / 完成标准 |
|---|---|
| 电脑浏览器业务验收 | 待完成；在脱敏后端执行录入、错误输入、改期预览/取消/确认、重复确认和任务反馈，留存步骤与结果 |
| 桌面稳定性 | 待完成；检查连续操作、窗口与缩放、放置后继续、到期/断线恢复及问题复测 |
| 同一个 Agent 与身份联调 | 等 A/B 提供可运行服务和可信身份方式；验证企微操作后网页刷新可见同一用户结果，备用对话调用共享 Agent |
| 目标变化后的计划状态 | B/C 共同待办；对齐可靠状态/依赖版本后展示并回归，前端不猜测 |
| 演示资料 | 待完成；按 B 的脚本录制真实运行的备用视频，准备页面截图及答辩材料 |
| 最终交付 | 三人共同完成干净环境复现与提交物检查；稳定版发布由团队安排 |
| 手机端 | 按用户要求暂缓，不阻断当前桌面验收 |

不需要额外搭建独立 Agent。少量课程可手动录入；如需真实资料，先准备脱敏的一周具体日期课表、考试通知、必做/选做任务与剩余估时。当前没有 Excel/PDF 批量导入。

[`docs/DESKTOP_HANDOFF.md`](docs/DESKTOP_HANDOFF.md) 保留早期交接与人工检查方法，其中“后端不能直连”“36 项检查”“浏览器无法读取画面”等是历史状态；当前接口、测试与任务以本页、根 README 和 PROJECT_STATUS 为准。历史 HTML、效果图和 zip 仅用于追溯设计，当前运行入口是 `web/app.py`。
