"""成员 C：桌面网页。四项左侧导航，共享演示/API 数据，只展示真实返回结果。"""
from datetime import datetime, time, timedelta
from html import escape
import json
import os
import sys
from pathlib import Path

# 同时支持仓库根目录启动及 cd web 后启动。
repo_root = str(Path(__file__).resolve().parents[1])
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import streamlit as st

from web.client import ClientError, TZ, initial_workspace, make_client, parse_time
from web.view_model import agenda_for_day, plan_is_current

st.set_page_config(page_title="UniPulse · 中大校园工作台", page_icon="🎓", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
.block-container{max-width:1480px;padding-top:1.8rem;padding-bottom:2.5rem}
[data-testid="stSidebar"]{background:#edf3ef;border-right:1px solid #d9e5dd}
h1,h2,h3{color:#164b35}h1{font-size:2rem!important}
.school-head{display:flex;justify-content:space-between;align-items:center;gap:1rem;border-bottom:1px solid #d9e5dd;padding-bottom:1rem;margin-bottom:1rem;flex-wrap:wrap}
.school-name{font:600 1.9rem 'Songti SC','STSong',serif;letter-spacing:.2rem;color:#006b48}.school-en{font:12px Georgia,serif;letter-spacing:.1rem;color:#526d5f;margin-top:.45rem}.motto{font:14px 'Songti SC',serif;letter-spacing:.08rem;color:#526d5f}
.hero{background:#006b48;color:white;padding:1.4rem 1.6rem;border-radius:10px;margin:.5rem 0 1.3rem}.hero strong{font-size:1.65rem;font-weight:600}.hero p{margin:.5rem 0 0;color:#e4f1e9}
.summary-card{border:1px solid #d9e5dd;border-radius:9px;background:white;padding:1rem 1.2rem;min-height:123px;margin-bottom:1rem;overflow-wrap:anywhere}.summary-card .label{font-size:14px;color:#526d5f;margin-bottom:.5rem}.summary-card strong{font-size:18px;color:#164b35}.summary-card p{font-size:14px;margin:.45rem 0 0;color:#526d5f}
.agenda-card{border:1px solid #d9e5dd;border-left:4px solid #006b48;border-radius:7px;background:white;padding:.85rem 1rem;margin:.6rem 0;overflow-wrap:anywhere}.agenda-card.review{border-left-color:#a96a16;background:#fff9ec}.agenda-card strong{font-size:16px}.agenda-card p{margin:.4rem 0 0;font-size:14px}.meta{font-size:14px;color:#526d5f}
.week-wrap{overflow-x:auto;border:1px solid #d9e5dd;border-radius:9px;background:white;margin-bottom:1rem}.week{width:100%;min-width:700px;border-collapse:collapse;table-layout:fixed}.week th{background:#e7f1e9;color:#315c47;padding:13px 7px;font-size:14px;font-weight:600}.week td{border-top:1px solid #e4ece6;border-right:1px solid #edf2ee;padding:7px;vertical-align:top;height:84px;font-size:14px}.week th:first-child,.week td:first-child{width:70px}.week .today{background:#d6e8dc}.week .course{background:#e7f1e9;border-radius:5px;padding:8px;margin-bottom:5px;color:#164b35;overflow-wrap:anywhere}.week small{display:block;font-size:13px;color:#526d5f;margin-top:4px}.week .empty{color:#8b9d92}.week .slot{background:#f8faf8;color:#526d5f;font-size:13px}
[data-testid="stMetric"]{background:white;border:1px solid #d9e5dd;border-radius:9px;padding:1rem 1.2rem}
@media(max-width:900px){.block-container{padding:1rem}.school-name{font-size:1.5rem}}
</style>""", unsafe_allow_html=True)

STATUS = {"todo":"待开始","doing":"进行中","partial":"部分完成","done":"已完成","active":"执行中","needs_review":"待检查","needs_adjustment":"计划待调整","confirmed":"已确认","completed":"已完成","pending":"待处理","invalidated":"已失效","cancelled":"已取消","applied":"已应用"}
WEEKDAYS = ["周一","周二","周三","周四","周五","周六","周日"]


def human(value):
    return parse_time(value).astimezone(TZ).strftime("%m 月 %d 日 %H:%M")


def run_action(fn, *args):
    try:
        result = fn(*args)
        st.session_state["flash"] = result.get("message", "操作已完成。")
        st.rerun()
    except ClientError as exc:
        st.error(str(exc))


def reset_demo():
    st.session_state["demo_workspace"] = initial_workspace()
    st.session_state.pop("chat_history", None)
    st.session_state["flash"] = "已恢复初始脱敏示例。"
    st.rerun()


def change_page(name):
    st.session_state["page"] = name


st.markdown('<div class="school-head"><div><div class="school-name">中山大学 · UniPulse</div><div class="school-en">SUN YAT-SEN UNIVERSITY · CAMPUS WORKSPACE</div></div><div class="motto">博学 · 审问 · 慎思 · 明辨 · 笃行</div></div>', unsafe_allow_html=True)
api_mode = bool(os.getenv("UNIPULSE_API_BASE", "").strip())
if api_mode and not st.session_state.get("api_token") and not (os.getenv("UNIPULSE_LOCAL_TEST") == "1" and os.getenv("UNIPULSE_API_TOKEN")):
    st.info("后端联调模式：请提供本次会话的测试访问凭据。")
    with st.form("access_form"):
        credential = st.text_input("本次会话的测试访问凭据", type="password")
        if st.form_submit_button("读取我的工作台"):
            if credential.strip():
                st.session_state["api_token"] = credential.strip()
                st.rerun()
            st.error("请填写访问凭据。")
    st.stop()
try:
    client = make_client(st.session_state)
    workspace = client.snapshot()
except ClientError as exc:
    st.error(str(exc))
    if not api_mode:
        if st.button("恢复 V4 示例"):
            reset_demo()
    else:
        st.caption("未使用演示数据替代后端结果。")
        if st.button("清除本次会话凭据"):
            st.session_state.pop("api_token", None)
            st.rerun()
    st.stop()

now = parse_time(workspace["demo_now"]) if client.is_demo else datetime.now(TZ)
today, version = now.astimezone(TZ).date(), workspace["version"]
events = {e["id"]: e for e in workspace["events"]}
notices = {n["id"]: n for n in workspace["notices"]}
tasks, plans = workspace["tasks"], workspace["plans"]
goals_by_id = {g["id"]: g for g in workspace["goals"]}
goal = next(iter(goals_by_id.values())) if goals_by_id else None
pending = [a for a in workspace["pending"] if a["status"] == "pending"]
active_tasks = [t for t in tasks if t["status"] != "done" and t.get("included", True)]
with st.sidebar:
    st.title("UniPulse")
    st.caption("中大校园 · 智能相伴")
    page = st.radio("工作台导航", ["首页", "课表", "计划", "任务"], key="page")
    if len(goals_by_id) > 1:
        chosen = st.selectbox(
            "当前目标", list(goals_by_id),
            format_func=lambda key: goals_by_id[key]["title"], key="goal_select",
        )
        goal = goals_by_id[chosen]
    st.divider()
    st.write(workspace["profile"]["display_name"])
    st.caption("演示模式 · Agent 未连接" if client.is_demo else "共享后端模式")
    if client.is_demo:
        st.caption("示例时间："+human(workspace["demo_now"]))
        with st.expander("演示数据与恢复"):
            st.download_button("下载当前数据快照", json.dumps(workspace, ensure_ascii=False, indent=2), "unipulse-demo.json", "application/json")
            st.caption("下载仅用于留存，本版不支持快照导入。")
            agreed = st.checkbox("清除本次演示操作，恢复初始样例")
            if st.button("恢复示例", disabled=not agreed):
                reset_demo()
    st.caption("学生项目 · 非学校官方服务")
if client.is_demo:
    st.caption("演示数据 · 尚未连接真实 Agent / 企微 / 自动排程；页面操作只影响本次演示会话。")
else:
    st.caption("共享后端模式 · 仅展示后端返回的数据与结果")
    if os.getenv("UNIPULSE_LOCAL_TEST") == "1":
        st.warning("固定账号本机联调模式，不用于多用户部署。")
if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))


def summary_card(label, title, detail):
    st.markdown(f'<div class="summary-card"><div class="label">{escape(label)}</div><strong>{escape(title)}</strong><p>{escape(detail)}</p></div>', unsafe_allow_html=True)


def sources(source_ids):
    if not source_ids:
        st.caption("尚无关联通知证据，请先核对来源。")
    for source_id in source_ids:
        n = notices.get(source_id)
        if n:
            st.text(n["text"])
            published = human(n["published_at"]) if n.get("published_at") else "未知"
            st.caption(f"{n['channel']} · 接收 {human(n['received_at'])} · 发布 {published} · {n['status']}")


def capacity_card(capacity):
    if capacity:
        st.write(f"剩余 **{capacity['required_minutes']} 分钟** · 可保留 **{capacity['kept_minutes']} 分钟** · 未排入 **{capacity['unplaced_minutes']} 分钟**")
        if capacity["unplaced_minutes"]:
            st.warning("仍有工作未排入。需要明确补充允许时段，或减少指定选做内容；当前未自动删减范围。")
        with st.expander("查看时间核对说明"):
            st.caption(capacity["basis"])


def plan_result(compact=False):
    if not plans:
        st.info("尚无已确认计划。连接 Agent 后将在这里展示结果。")
    for p in plans:
        if plan_is_current(workspace, p):
            st.write(f"**正式计划 v{p['version']} · 已确认**")
        else:
            st.warning(f"计划待调整 · 上次确认版本 v{p['version']}，尚无有效新版。")
        plan_goal = goals_by_id.get(p.get("goal_id")) or goal
        if plan_goal:
            st.write(plan_goal["title"])
            st.caption(f"每天最多 {plan_goal['daily_max_minutes']} 分钟 · 目标版本 {plan_goal['version']}")
        capacity_card(p.get("capacity"))
        if not compact:
            blocks = [b for b in workspace["study_blocks"] if b["plan_id"] == p["id"]]
            rows = []
            for b in blocks:
                task = next(t for t in tasks if t["id"] == b["task_id"])
                effective = plan_is_current(workspace, p) and b["status"] == "confirmed" and b["plan_version"] == p["version"] and task["status"] != "done" and task.get("included", True)
                state = "已完成" if b["status"] == "completed" or task["status"] == "done" else "已移出" if b["status"] in {"retired", "cancelled"} or not task.get("included", True) else "已确认" if effective else "旧安排 · 待检查"
                rows.append({"任务":task["title"],"开始":human(b["start_at"]),"结束":human(b["end_at"]),"版本":f"v{b['plan_version']}","状态":state})
            if rows:
                st.table(rows)


def render_agenda(day):
    rows = agenda_for_day(workspace, day)
    if not rows:
        st.info("这天暂无已录入安排。未录入不代表有可用学习空档。")
    for r in rows:
        end = "—"+parse_time(r["end_at"]).astimezone(TZ).strftime("%H:%M") if r["end_at"] else ""
        css = " review" if r["kind"] == "旧学习块" else ""
        st.markdown(f'<div class="agenda-card{css}"><div class="meta">{parse_time(r["start_at"]).astimezone(TZ):%H:%M}{end} · {escape(r["kind"])}</div><strong>{escape(r["title"])}</strong><p>{escape(r["detail"])} · {escape(r["status"])}</p></div>', unsafe_allow_html=True)


def week_table():
    monday = today-timedelta(days=today.weekday())
    days = [monday+timedelta(days=i) for i in range(7)]
    st.caption(f"{days[0]:%m 月 %d 日} 至 {days[-1]:%m 月 %d 日} · 具体日期课程")
    heads = '<th>时间</th>'+''.join(f'<th class="{"today" if d==today else ""}">{WEEKDAYS[d.weekday()]}<br>{d:%m.%d}</th>' for d in days)
    rows = ""
    slots = [("上午",8,10),("上午",10,12),("下午",12,16),("下午",16,19),("晚间",19,24),("其他",0,8)]
    for label, start_hour, end_hour in slots:
        matches = [c for c in workspace["classes"] if parse_time(c["start_at"]).astimezone(TZ).date() in days and start_hour <= parse_time(c["start_at"]).astimezone(TZ).hour < end_hour]
        if label == "其他" and not matches:
            continue
        row = f'<td class="slot">{label}<br>{start_hour:02d}—{end_hour:02d}时</td>'
        for d in days:
            courses = sorted([c for c in matches if parse_time(c["start_at"]).astimezone(TZ).date()==d], key=lambda c:parse_time(c["start_at"]))
            content = ""
            for c in courses:
                content += f'<div class="course">{escape(c["course"])}<small>{parse_time(c["start_at"]).astimezone(TZ):%H:%M}—{parse_time(c["end_at"]).astimezone(TZ):%H:%M}<br>{escape(c["location"])}</small></div>'
            row += '<td>'+ (content or '<span class="empty">—</span>')+'</td>'
        rows += '<tr>'+row+'</tr>'
    st.markdown('<div class="week-wrap"><table class="week"><thead><tr>'+heads+'</tr></thead><tbody>'+rows+'</tbody></table></div>', unsafe_allow_html=True)
    st.caption("空白仅表示尚无录入课程，不代表可以安排学习；学习时间块在“计划”查看。")


def preview_card(action):
    with st.container(border=True):
        st.subheader("待确认调整 · 尚未生效")
        st.caption(f"编号 {action['id']} · 有效至 {human(action['expires_at'])}")
        st.table([{"事项":c["label"],"原安排":c["before"],"调整后":c["after"]} for c in action["changes"]])
        if action["preserved"]:
            st.write("保留："+"；".join(action["preserved"]))
        capacity_card(action.get("capacity"))
        with st.expander("核对通知原文"):
            sources(action["source_notice_ids"])
        stale = action["expected_version"] != version
        expired = datetime.now(TZ) >= parse_time(action["expires_at"])
        if stale or expired:
            st.warning("此预览已过时或过期，请重新生成。")
        if st.button("确认本次调整", key="apply_"+action["id"], disabled=stale or expired, type="primary"):
            run_action(client.confirm_action, action["id"], action.get("confirm_code"))
        if st.button("取消草稿", key="cancel_"+action["id"]):
            run_action(client.cancel_action, action["id"])
        st.caption("当前演示仅确认考试事实及提醒队列变化，不代表 Agent 已生成新计划或消息已送达。" if client.is_demo else "仅提交本次预览。执行结果以刷新后的后端数据为准。")


def task_card(task):
    with st.container(border=True):
        st.write(("✓ " if task["status"]=="done" else "□ ")+task["title"])
        st.caption(f"{STATUS.get(task['status'],task['status'])} · {'必做' if task.get('required',True) else '选做'} · {'当前范围内' if task.get('included',True) else '已移出范围'}")
        st.write(f"剩余估时 {task['remaining_minutes']} 分钟 · 累计学习 {task['actual_minutes']} 分钟")
        if task.get("feedback"):
            st.write(task["feedback"])


def edit_goal_form():
    if not goal:
        st.info("暂无目标，等待后端提供当前目标。")
        return
    with st.form("goal_form"):
        title = st.text_input("目标名称", goal["title"])
        scope = st.text_area("复习范围", goal["scope"])
        daily = st.number_input("每日上限 / 分钟",15,480,goal["daily_max_minutes"],step=15)
        availability = st.text_area("允许时段与约束",goal["availability"])
        if st.form_submit_button("保存目标输入"):
            run_action(client.save_goal,{"title":title,"scope":scope,"event_id":goal["event_id"],"daily_max_minutes":daily,"availability":availability},version)


def change_form():
    st.caption("人工校对用于前端演示；不会自动解析通知、匹配对象或完成重排。" if client.is_demo else "备用人工校对入口，与共享后端使用同一份数据。")
    with st.form("notice_form"):
        text = st.text_area("通知原文","机器学习期末考试提前至12月2日14:00，复习范围不变。")
        if st.form_submit_button("保留通知原文"):
            run_action(client.save_notice,text,version)
    if events and notices:
        with st.form("change_form"):
            event_id = st.selectbox("要修改的考试",list(events),format_func=lambda k:events[k]["title"])
            notice_id = st.selectbox("对应的通知证据",list(notices),index=list(notices).index("n_change") if "n_change" in notices else len(notices)-1,format_func=lambda k:notices[k]["text"][:45])
            day = st.date_input("校对后的新日期",today+timedelta(days=2))
            at = st.time_input("校对后的新时间",time(14))
            if st.form_submit_button("生成修改预览",type="primary"):
                run_action(client.preview_exam_change,event_id,datetime.combine(day,at,TZ).isoformat(),version,notice_id)


def feedback_form():
    if not tasks:
        st.info("暂无任务可反馈。")
        return
    task_id = st.selectbox("选择任务",[t["id"] for t in tasks],format_func=lambda k:next(t["title"] for t in tasks if t["id"]==k),key="feedback_task")
    current = next(t for t in tasks if t["id"]==task_id)
    mode = st.radio("这次反馈什么",["只记录学习时长","报告明确进度"],key="progress_mode")
    if mode == "只记录学习时长":
        with st.form("time_"+task_id):
            actual = st.number_input("累计实际学习 / 分钟（替换原累计值）",0,10080,current["actual_minutes"],key="time_"+task_id)
            st.caption("只保存用时，不改变任务完成比例、剩余估时或学习安排。")
            if st.form_submit_button("保存学习时长"):
                run_action(client.record_time,task_id,actual,version)
    else:
        with st.form("progress_"+task_id):
            status = st.selectbox("本次明确状态",["partial","doing","done","todo"],format_func=lambda k:STATUS[k])
            remaining = st.number_input("明确剩余估时 / 分钟（完成时记为 0）",0,10080,current["remaining_minutes"],key="remain_"+task_id)
            actual = st.number_input("累计实际学习 / 分钟（替换原累计值）",0,10080,current["actual_minutes"],key="actual_"+task_id)
            feedback = st.text_area("进度说明",current.get("feedback",""))
            if st.form_submit_button("保存明确反馈"):
                run_action(client.record_progress,task_id,{"status":status,"remaining_minutes":remaining,"actual_minutes":actual,"feedback":feedback},version)


def result_records():
    with st.expander("通知证据与处理记录",expanded=False):
        st.caption("默认只展示最终结果。这里保留实际页面操作或后端返回的摘要；不生成模型思考或虚构工具调用。")
        sources(list(notices))
        for item in reversed(workspace["activity"][-20:]):
            st.write(item["name"]+" · "+item["detail"])
            st.caption(item.get("kind","后端记录")+" · "+human(item["at"]))
        if not workspace["activity"]:
            st.caption("还没有处理记录。")
        for p in plans:
            st.caption(p["summary"])
        st.write("提醒结果")
        for r in workspace["reminders"]:
            st.write(human(r["at"])+" · "+STATUS.get(r["status"],r["status"]))
        st.caption("本地演示不会发消息，入队不表示已经送达。")


if page == "首页":
    period = "上午好" if now.hour<12 else "下午好" if now.hour<18 else "晚上好"
    st.markdown(f'<div class="hero"><strong>{period}，{escape(workspace["profile"]["display_name"])}</strong><p>课表、目标和待办，尽在你的校园工作台。</p></div>',unsafe_allow_html=True)
    cols = st.columns(3)
    cols[0].metric("未完成任务",len(active_tasks))
    cols[1].metric("剩余工作",f"{sum(t['remaining_minutes'] for t in active_tasks)} 分钟")
    cols[2].metric("待确认调整",len(pending))
    st.write("")
    left,right = st.columns(2)
    future = sorted([c for c in workspace["classes"] if parse_time(c["start_at"])>=now],key=lambda c:parse_time(c["start_at"]))
    with left:
        summary_card("下一节课",future[0]["course"] if future else "暂无已录入课程",human(future[0]["start_at"])+" · "+future[0]["location"] if future else "未录入不代表没有课程安排")
    deadlines = [(e["start_at"],e["title"]) for e in workspace["events"] if parse_time(e["start_at"])>=now]
    deadlines += [(t["due_at"],t["title"]) for t in active_tasks if t.get("due_at") and parse_time(t["due_at"])>=now]
    with right:
        due = min(deadlines,key=lambda p:parse_time(p[0])) if deadlines else None
        summary_card("最近考试 / 截止",due[1] if due else "暂无已核对截止",human(due[0]) if due else "等待明确日期后再安排")
    main,aside = st.columns([2.3,1])
    with main:
        st.subheader("本周课表")
        week_table()
    with aside,st.container(border=True):
        st.subheader("当前计划")
        plan_result(compact=True)
        st.button("查看完整计划",on_click=change_page,args=("计划",))
        st.divider()
        st.subheader("接下来要做")
        if not active_tasks:
            st.write("暂无未完成任务。")
        for t in active_tasks[:3]:
            st.write("□ "+t["title"])
            st.caption(f"剩余 {t['remaining_minutes']} 分钟")
        st.button("查看全部任务",on_click=change_page,args=("任务",))
    if pending:
        for action in pending:
            preview_card(action)
    else:
        applied = [a for a in workspace["pending"] if a["status"]=="applied"]
        if applied:
            with st.container(border=True):
                st.subheader("最近修改结果")
                st.table([{"事项":c["label"],"结果":c["after"]} for c in applied[-1]["changes"]])
                st.caption("已确认的修改结果；新学习计划是否有效以上方当前计划状态为准。")
    result_records()

elif page == "课表":
    st.title("我的课表")
    st.caption("具体日期课表与当天安排，课程不会自动扩散为每周重复。")
    week_table()
    left,right = st.columns([1.4,1])
    with left:
        st.subheader("当天安排")
        selected_day = st.date_input("按日期查看",today,key="course_day")
        render_agenda(selected_day)
    with right:
        with st.expander("录入一节具体日期课程",expanded=False):
            with st.form("class_form"):
                course = st.text_input("课程名称")
                location = st.text_input("教室或地点")
                day = st.date_input("上课日期",today)
                start = st.time_input("开始时间",time(8))
                end = st.time_input("结束时间",time(9,35))
                if st.form_submit_button("保存课程",type="primary"):
                    run_action(client.add_class,{"course":course,"location":location,"start_at":datetime.combine(day,start,TZ).isoformat(),"end_at":datetime.combine(day,end,TZ).isoformat()},version)
        st.caption("新增课程后，关联计划会标为待检查；本地模式不会自动重排。")
    result_records()

elif page == "计划":
    st.title("我的计划")
    st.caption("正式计划、待确认调整与修改结果分开查看。")
    plan_result()
    if goal:
        with st.expander("目标范围与允许时段"):
            st.write(goal["scope"])
            st.write(goal["availability"])
    for action in pending:
        preview_card(action)
    with st.expander("录入通知与校对改期（备用入口）",expanded=False):
        change_form()
    with st.expander("编辑目标输入",expanded=False):
        edit_goal_form()
    with st.expander("网页备用对话",expanded=False):
        st.caption("接入后复用同一 Agent；当前演示没有连接模型。" if client.is_demo else "此入口使用共享后端 Agent。")
        for m in st.session_state.get("chat_history",[]):
            with st.chat_message(m["role"]):
                st.write(m["content"])
        message = st.chat_input("给共享 Agent 发消息")
        if message:
            try:
                result = client.chat(message)
                if not isinstance(result.get("reply"),str):
                    raise ClientError("后端未返回有效回复。")
                history = st.session_state.get("chat_history",[])
                st.session_state["chat_history"] = (history+[{"role":"user","content":message},{"role":"assistant","content":result["reply"]}])[-20:]
                st.rerun()
            except ClientError as exc:
                st.error(str(exc))
    result_records()

elif page == "任务":
    st.title("我的任务")
    st.caption("明确的工作量与累计学习时长分别记录。")
    left,right = st.columns([1.5,1])
    with left:
        selected = st.selectbox("筛选任务",["全部","未完成","已完成"],key="task_filter")
        filtered = [t for t in tasks if selected=="全部" or (t["status"]=="done")== (selected=="已完成")]
        if not filtered:
            st.info("这个筛选下暂无任务。")
        for t in filtered:
            task_card(t)
    with right,st.container(border=True):
        st.subheader("反馈学习情况")
        feedback_form()
    result_records()

st.divider()
if st.button("刷新当前数据"):
    st.rerun()
st.caption("UniPulse · 成员 C 桌面网页 · 学生项目，非学校官方服务")
