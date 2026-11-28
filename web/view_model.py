"""看板只组织展示数据，不触发 Agent 或寻找新空档。"""
from web.client import parse_time, TZ


def plan_is_current(workspace, plan):
    goal = next((g for g in workspace["goals"] if g["id"] == plan["goal_id"]), None)
    event = next((e for e in workspace["events"] if goal and e["id"] == goal["event_id"]), None)
    return bool(event and plan["status"] == "confirmed" and plan["event_version"] == event["version"])


def agenda_for_day(workspace, day):
    rows = []
    for c in workspace["classes"]:
        rows.append({"kind": "课程", "title": c["course"], "start_at": c["start_at"], "end_at": c["end_at"], "detail": c["location"], "status": "固定课表"})
    for e in workspace["events"]:
        rows.append({"kind": "考试", "title": e["title"], "start_at": e["start_at"], "end_at": None, "detail": e["location"], "status": "已核对事件"})
    plans = {p["id"]: p for p in workspace["plans"]}
    tasks = {t["id"]: t for t in workspace["tasks"]}
    for b in workspace["study_blocks"]:
        plan, task = plans[b["plan_id"]], tasks[b["task_id"]]
        if b["status"] in {"retired", "completed", "cancelled"} or task["status"] == "done" or not task.get("included", True):
            continue
        valid = plan_is_current(workspace, plan) and b["status"] == "confirmed" and b["plan_version"] == plan["version"]
        rows.append({"kind": "学习" if valid else "旧学习块", "title": task["title"], "start_at": b["start_at"], "end_at": b["end_at"],
                     "detail": f"计划 v{b['plan_version']}", "status": "已确认" if valid else "待检查，不能视为有效计划"})
    return sorted([r for r in rows if parse_time(r["start_at"]).astimezone(TZ).date() == day], key=lambda r: parse_time(r["start_at"]))


def assess_demo_blocks(workspace, event_id, new_start):
    """只核对已存在的示例学习块，不重新排程，也不替代 B 的可行性求解。"""
    cutoff, now = parse_time(new_start), parse_time(workspace["demo_now"])
    goals = {g["id"]: g for g in workspace["goals"] if g["event_id"] == event_id}
    tasks = {t["id"]: t for t in workspace["tasks"] if t["goal_id"] in goals and t["status"] != "done" and t.get("included", True)}
    required = sum(t["remaining_minutes"] for t in tasks.values())
    used, per_day = {}, {}
    for b in sorted(workspace["study_blocks"], key=lambda b: parse_time(b["start_at"])):
        if b["task_id"] not in tasks or b["status"] != "confirmed":
            continue
        start, end = parse_time(b["start_at"]), parse_time(b["end_at"])
        if start < now or end > cutoff:
            continue
        if not any(parse_time(a["start_at"]) <= start and end <= parse_time(a["end_at"]) for a in workspace.get("availability", [])):
            continue
        if any(start < parse_time(c["end_at"]) and parse_time(c["start_at"]) < end for c in workspace["classes"]):
            continue
        task = tasks[b["task_id"]]
        day = start.astimezone(TZ).date()
        minutes = int((end-start).total_seconds()/60)
        remaining = max(0, task["remaining_minutes"]-used.get(task["id"], 0))
        budget = max(0, goals[task["goal_id"]]["daily_max_minutes"]-per_day.get(day, 0))
        # 不把不能容纳整块的情况假称可拆分或已重新安排。
        kept = minutes if minutes <= remaining and minutes <= budget else 0
        used[task["id"]] = used.get(task["id"], 0)+kept
        per_day[day] = per_day.get(day, 0)+kept
    kept = sum(used.values())
    return {"required_minutes": required, "kept_minutes": kept, "unplaced_minutes": max(0, required-kept),
            "basis": "仅核对现有示例时间块；没有寻找新空档，不是完整排程器的可行性结论。"}
