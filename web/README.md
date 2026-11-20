# web/ —— 成员 C 的工作区（个人看板）

手机可读的个人看板，用 **Streamlit** 实现（V4 第 3.1 节指定）。

## 启动

```bash
cd unipulse
source .venv/bin/activate
pip install streamlit          # 依赖由 C 自己加进 requirements.txt
streamlit run web/app.py
```

## 数据从哪来

**只通过 HTTP 调后端**，不直接连数据库：

```python
import httpx

resp = httpx.get("http://127.0.0.1:8000/api/v1/dashboard", params={"token": token})
data = resp.json()
```

接口清单、字段、错误结构见 `docs/API_CONTRACT.md` 第 3、5 节。
后端启动：`python -m uvicorn api.app:app --port 8000`。

## token 从哪来

用户从企微点开的链接形如：

```
http://<看板地址>/?token=eyJ...
```

Streamlit 用 `st.query_params["token"]` 取到，原样转发给后端。
**不要**自己解析 token，也不要接受任何 `user_id` 参数——身份判断只在后端做。

## 页面结构（V4 第 2 节）

1. 顶部：下一节课、最近截止、待确认提示（黄色提示条）
2. 中间：按日期分组的课程 / 考试 / 学习时间块
3. 下方：待办与进度
4. 展开区：通知来源、计划改动前后对比

## 两条硬要求

- **未确认的草稿单独标记**，不能渲染成正式安排（后端已经把 `pending_changes` 和 `plan` 分开）
- 链接里不能出现明文 `user_id`，页面不能因为拿到别人的链接就显示别人的数据

## 演示

12/6 用固定的 11-30 场景（见 `demo/README.md`）。数据由 B 的 `demo/seed.py` 重置，C 只读不改。
