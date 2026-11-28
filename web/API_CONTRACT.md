# UniPulse 成员 C 独立接口草案 v0.2（待与仓库对齐）

> 本文件保留 C 独立开发时的字段设计，不覆盖仓库 `docs/API_CONTRACT.md`。当前客户端的 `/v1/workspace` 与 B 草案 `/api/v1/dashboard` 不兼容；联调差异见 [docs/INTEGRATION_NOTES.md](docs/INTEGRATION_NOTES.md)。目前 B 仅实现健康检查，不能宣称已接通。

本轮按用户确认优先交付电脑网页，采用“首页、课表、计划、任务”左侧导航；默认展示最终结果，处理记录收起。手机企微打开与移动端外观验收后置。以下接口继续沿用 v0.2，没有因导航调整新增协议。

依据本次 V4 文件修订。**这是前端待联调协议，目前没有对应正式后端服务实现。** 与 v0.1 不完全兼容：新增 plans、来源字段、时间块所属计划、通用差异；预览请求必须关联通知证据。A、B 需先对齐再接入。

## 入口和身份

- 企微是主入口，网页主要 GET 查询本人工作台；网页对话/表单是备用入口，复用同一 Agent 和数据。
- `/v1`，JSON，带时区 ISO 8601 日期。看板统一按北京时间显示。
- 后端从可信登录会话或有效的个人凭据确定用户及企微绑定，不能信任 URL user_id、正文中的姓名、模型参数或通知原文。
- 当前前端支持本次 Streamlit 会话测试凭据；`UNIPULSE_LOCAL_TEST=1` 时允许环境固定测试凭据。均不等于完成正式企微身份绑定，不支持直接公开成多用户生产服务。
- 企微回复配置的手机可访问个人看板地址。地址本身不授予读取权限，不在 URL 中暴露长期凭据；重新打开须由后端校验访问者。登录/绑定流程由 A/B 提供，C 再接入其会话协议。

## 共同约定

成功：`{"ok":true,"data":{...}}`。写入通常返回 message、version；错误使用 401/403/409/422/5xx，不用成功结果包裹失败。网页不会在错误时退回演示。

写入包含 `expected_version`，第一版采用整个工作台版本。服务端必须同时核验对象所属、依赖版本、确认有效期、约束及幂等。真实计划/提醒更新应原子提交，提交后查询核验。模型只生成候选，不能自行审批。

## 路径

| 路径 | 请求 | data |
|---|---|---|
| GET /workspace | 当前可信会话 | 完整工作台；只查询，不触发重排 |
| POST /agent/messages | message | reply、run_id；交给同一共享 Agent |
| POST /notices | text、expected_version | message、version；不等于已解析或已修改正式数据 |
| POST /previews | event_id、new_start、expected_version、source_notice_ids | 通用预览；当前备用表单仅能人工校对考试改期 |
| POST /actions/{id}/confirm | {}，当前可信用户的明确点击 | message、version；只有实际提交后返回成功 |
| POST /actions/{id}/cancel | {} | message；只取消草稿，不回滚旧的已确认事实 |
| POST /goals | payload、expected_version | message、version；备用目标输入，不等于已规划 |
| POST /classes | payload、expected_version | message、version；具体日期课程，不扩散至每周 |
| POST /tasks/{id}/progress | payload、expected_version | message、version；明确反馈状态/剩余工作 |
| POST /tasks/{id}/time | actual_minutes、expected_version | message、version；仅替换累计用时，不改变任务进度、剩余工作或启动重排 |

payload 沿用源码中方法字段。真实主流程应由企微消息调用同一业务服务，不要求学生在网页重复填写。查询看板链接不调用模型，不重新排程。

## 工作台

完整脱敏实例见 `fixtures/workspace.json`。必需列表：goals、events、plans、classes、tasks、study_blocks、reminders、notices、pending、activity；根字段另有 version 和 profile.display_name。schema_version=2 与 demo_now 仅用于本地样例恢复/演示，不参与真实服务当前时间判断。

| 对象 | 关键字段 |
|---|---|
| goals | id、title、event_id、scope、daily_max_minutes、availability、status、version |
| events | id、title、start_at、location、source、version；source_notice_id 关联事件证据 |
| plans | id、goal_id、version、status、event_version、source_notice_ids、summary；可含 capacity |
| classes | id、course、start_at、end_at、location |
| tasks | id、goal_id、title、remaining_minutes、actual_minutes、status、version；required 区分必做/选做，included 表示是否仍在范围内；可有 due_at/feedback |
| study_blocks | id、task_id、plan_id、plan_version、start_at、end_at、status；可扩展 locked 等保护标识 |
| notices | id、text、channel、received_at、status；published_at 可为 null（未知不猜） |
| reminders | id、event_id、at、status；可加 plan_id，用于关联计划提醒失效；入队、发送失败、实际送达分别记录 |
| activity | at、name、detail、可选 kind；简短实际调用/状态摘要，不含内部思考、密钥或完整私聊 |

当前 scope 为单门考试。不同目标必须分别关联计划与事件。前端将对字段类型、关联编号及缺口数字做基础检查；这不能代替 B 的数据约束与身份校验。

正式计划 status=confirmed 且其 event_version 等于当前考试版本，时间块的 plan_version 也匹配时才标为已确认有效。计划待调整、旧版本块或草稿应明确标记，不能混入正式学习安排。

确认考试事实后若没有经过复核/确认的新计划：保留上次确认的计划版本，status=needs_adjustment，相关旧提醒失效；不能把事件版本递增说成新计划版本递增。真实重排确认后由 B 提供新的正式计划和时间块。

## 通用预览

```json
{
  "id": "T03",
  "kind": "exam_change",
  "expected_version": 1,
  "status": "pending",
  "expires_at": "2026-11-30T10:30:00+08:00",
  "source_notice_ids": ["n_change"],
  "changes": [
    {"label":"考试时间","before":"12月4日14:00","after":"12月2日14:00"},
    {"label":"学习计划","before":"已确认 v1","after":"仍有80分钟未排入，需学生明确取舍"}
  ],
  "preserved": ["具体日期课表", "已完成任务"],
  "capacity": {
    "required_minutes":230,
    "kept_minutes":150,
    "unplaced_minutes":80,
    "basis":"只开放三段已确认学习时段，最后80分钟晚于新考试。"
  }
}
```

changes 是后端提供的实际差异，before/after 为可直接展示的字符串。这样单次调课、仅换教室、作业截止也能用同一看板展示，不套用“考试前一天提醒”的固定说法。C 当前演示适配器只支持考试事实改期，不代表已实现这些解析和执行服务。

capacity 可选；若提供，须是非负整数且 required=kept+unplaced。basis 解释阻塞和计算范围。真实结果由 B 求解返回，C 不以“总分钟够”推断连续空档够，也不硬编码80。候选变更可包含计划和任务，但确认必须由后端绑定同一份候选与依赖版本。

本地演示容量核对的能力有限，只检查已有块，不求解替代空档；不会自动移出选做任务，也不会假称完成重排。完整协商需由 A/B 通过真实 Agent 返回新草稿，明确展示被移出的指定选做内容，再由用户确认。

## 联调待验收

1. 企微私聊返回手机可访问链接，A、B、C 指向同一后端及测试用户。
2. 用户甲的链接被乙打开后，后端拒绝读取甲的数据；网页不能仅靠 user_id 筛选证明隔离。
3. 通知来源、草稿、依赖版本一致；未确认不修改正式安排，过时/过期拒绝，重复确认不重复写入。
4. 确认后重查同一份数据，观察真实新版本/待调整状态和提醒失效；失败无半套更新。
5. 真实时间缺口与协商随截止、允许时段、必做/选做范围变化，不使用固定演示台词。
6. 异步 run 协议、登录绑定方式和部署地址由 A/B 确定后再接；当前不伪造运行过程或手机联调结果。
