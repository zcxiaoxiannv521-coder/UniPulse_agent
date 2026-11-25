"""成员 C 的接口适配层。DemoClient 只用于网页交互验收，不是 Agent 后台。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

FIXTURE = Path(__file__).parent / "fixtures" / "workspace.json"
TZ = timezone(timedelta(hours=8))


class ClientError(Exception):
    """可以向用户展示的失败，不将失败返回成成功。"""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 不将认证头跟随重定向带到另一个地址。
        return None


def parse_time(value):
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            raise ValueError("missing timezone")
        return dt
    except (TypeError, ValueError) as exc:
        raise ClientError("日期必须完整并包含时区。") from exc


def initial_workspace():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class DemoClient:
    """只修改传入的演示会话，重规划/模型/真实推送留给 A、B。"""
    is_demo = True

    def __init__(self, state):
        self.state = state

    def snapshot(self):
        return deepcopy(self.state)

    def _check(self, expected_version):
        if expected_version != self.state["version"]:
            raise ClientError("数据已变化，请刷新并重新预览。")

    def _commit(self, name, detail):
        self.state["version"] += 1
        self.state["activity"].append({"at": datetime.now(TZ).isoformat(), "name": name, "detail": detail, "kind": "页面演示操作"})
        return {"ok": True, "version": self.state["version"], "message": detail}

    def _one(self, table, object_id):
        item = next((x for x in self.state[table] if x["id"] == object_id), None)
        if item is None:
            raise ClientError("记录不存在，请刷新页面。")
        return item

    def _mark_plan_review(self, goal_ids):
        for plan in self.state["plans"]:
            if plan["goal_id"] in goal_ids:
                plan["status"] = "needs_adjustment"

    def save_goal(self, payload, expected_version):
        self._check(expected_version)
        if not payload["title"].strip() or not payload["scope"].strip():
            raise ClientError("请填写目标名称和复习范围。")
        self._one("events", payload["event_id"])
        if not 15 <= payload["daily_max_minutes"] <= 480:
            raise ClientError("每日上限应在 15—480 分钟之间。")
        goal = self.state["goals"][0]
        goal.update(payload)
        goal["version"] += 1
        goal["status"] = "needs_review"
        self._mark_plan_review({goal["id"]})
        return self._commit("目标录入", "目标已保存在演示会话，等待 Agent 生成或检查计划。")

    def add_class(self, payload, expected_version):
        self._check(expected_version)
        start, end = parse_time(payload["start_at"]), parse_time(payload["end_at"])
        if end <= start:
            raise ClientError("结束时间必须晚于开始时间。")
        if not payload["course"].strip():
            raise ClientError("请填写课程名称。")
        if any(start < parse_time(c["end_at"]) and parse_time(c["start_at"]) < end for c in self.state["classes"]):
            raise ClientError("这段时间已有课程，请先核对时间。")
        self.state["classes"].append({**payload, "id": uuid4().hex})
        for goal in self.state["goals"]:
            goal["status"] = "needs_review"
        self._mark_plan_review({g["id"] for g in self.state["goals"]})
        return self._commit("课表录入", "课程已加入演示课表；关联计划待 Agent 检查。")

    def record_progress(self, task_id, payload, expected_version):
        self._check(expected_version)
        task = self._one("tasks", task_id)
        status = payload["status"]
        if status not in {"todo", "doing", "partial", "done"}:
            raise ClientError("不支持的进度状态。")
        if not 0 <= payload["remaining_minutes"] <= 10080 or not 0 <= payload["actual_minutes"] <= 10080:
            raise ClientError("请填写合理的剩余估时与累计耗时。")
        if status != "done" and payload["remaining_minutes"] <= 0:
            raise ClientError("未完成任务请明确剩余估时；已完成请选择完成。")
        if not payload["feedback"].strip():
            raise ClientError("请补充进度说明。")
        task.update(payload)
        if status == "done":
            task["remaining_minutes"] = 0
        task["version"] += 1
        self._mark_plan_review({task["goal_id"]})
        for goal in self.state["goals"]:
            if goal["id"] == task["goal_id"]:
                goal["status"] = "needs_review"
        for block in self.state["study_blocks"]:
            if block["task_id"] == task_id:
                block["status"] = "completed" if status == "done" else "needs_review"
        return self._commit("进度反馈", "明确进度已保存；累计学习时长不会自动换算成完成比例。重规划尚待 Agent 接入。")

    def record_time(self, task_id, actual_minutes, expected_version):
        self._check(expected_version)
        if type(actual_minutes) is not int or not 0 <= actual_minutes <= 10080:
            raise ClientError("请填写合理的累计学习时长。")
        task = self._one("tasks", task_id)
        task["actual_minutes"] = actual_minutes
        task["version"] += 1
        return self._commit("用时记录", "仅保存累计学习时长；任务状态、剩余工作和计划安排没有据此修改。")

    def save_notice(self, text, expected_version):
        self._check(expected_version)
        if not text.strip() or len(text) > 20000:
            raise ClientError("请填写 1—20000 字的通知。")
        if any(n["text"] == text for n in self.state["notices"]):
            raise ClientError("这条通知已经保存，无需重复导入。")
        self.state["notices"].append({"id": uuid4().hex, "text": text, "status": "待解析", "channel": "网页备用录入", "received_at": datetime.now(TZ).isoformat(), "published_at": None})
        return self._commit("原文录入", "原文已保留。演示模式不自动解析，请在下方校对变更字段。")

    def preview_exam_change(self, event_id, new_start, expected_version, notice_id):
        self._check(expected_version)
        event = self._one("events", event_id)
        notice = self._one("notices", notice_id)
        new = parse_time(new_start)
        if new <= parse_time(self.state["demo_now"]):
            raise ClientError("示例考试时间须晚于演示当前时间。")
        if new == parse_time(event["start_at"]):
            raise ClientError("时间未变化，无需生成修改预览。")
        from web.view_model import assess_demo_blocks
        old_reminders = [r for r in self.state["reminders"] if r.get("event_id") == event_id and r["status"] == "pending"]
        preview = {"id": uuid4().hex, "kind": "exam_change", "event_id": event_id, "expected_version": expected_version,
                   "event_version": event["version"], "old": event["start_at"], "new": new.isoformat(),
                   "remind_at": (new-timedelta(days=1)).isoformat(), "status": "pending",
                   "expires_at": (datetime.now(TZ)+timedelta(minutes=30)).isoformat(),
                   "source_notice_ids": [notice["id"]],
                   "changes": [
                       {"label": "考试时间", "before": event["start_at"], "after": new.isoformat()},
                       {"label": "原待发送提醒", "before": "、".join(r["at"] for r in old_reminders) or "无", "after": "旧提醒停用；新考试提醒入队，不代表实际送达"},
                       {"label": "备考计划", "before": "当前参考计划", "after": "计划待调整，尚未生成或确认新版"}],
                   "preserved": ["具体日期课表", "已完成任务", "必做/选做范围（没有自动删除选做项）"],
                   "capacity": assess_demo_blocks(self.state, event_id, new.isoformat())}
        self.state["pending"] = [preview]
        return deepcopy(preview)

    def cancel_action(self, action_id):
        action = self._one("pending", action_id)
        if action["status"] == "applied":
            raise ClientError("此修改已应用，不能通过取消预览撤回。")
        if action["status"] == "pending":
            action["status"] = "cancelled"
        return {"ok": True, "message": "已取消；考试、任务和提醒没有修改。"}

    def confirm_action(self, action_id, confirm_code=None):
        action = self._one("pending", action_id)
        if action["status"] == "applied":
            return {"ok": True, "message": "此操作已应用，没有重复修改。"}
        if action["status"] != "pending":
            raise ClientError("此预览已取消，不能再确认。")
        if datetime.now(TZ) >= parse_time(action["expires_at"]):
            raise ClientError("预览已过期，请重新生成。")
        self._check(action["expected_version"])
        event = self._one("events", action["event_id"])
        if event["version"] != action["event_version"]:
            raise ClientError("考试版本已变化，请重新生成。")
        event["start_at"] = action["new"]
        event["version"] += 1
        event["source_notice_id"] = action["source_notice_ids"][0]
        affected_goal_ids = {g["id"] for g in self.state["goals"] if g["event_id"] == event["id"]}
        self._mark_plan_review(affected_goal_ids)
        for r in self.state["reminders"]:
            if r.get("event_id") == event["id"] or r.get("plan_id") in {p["id"] for p in self.state["plans"] if p["goal_id"] in affected_goal_ids}:
                r["status"] = "invalidated"
        self.state["reminders"].append({"id": uuid4().hex, "event_id": event["id"], "at": action["remind_at"], "status": "pending"})
        for goal in self.state["goals"]:
            if goal["event_id"] == event["id"]:
                goal["status"] = "needs_review"
                for t in self.state["tasks"]:
                    if t["goal_id"] == goal["id"] and t["status"] != "done":
                        for block in self.state["study_blocks"]:
                            if block["task_id"] == t["id"] and block["status"] == "confirmed" and not block.get("locked", False) and parse_time(block["start_at"]) > parse_time(self.state["demo_now"]):
                                block["status"] = "needs_review"
        action["status"] = "applied"
        for plan in self.state["plans"]:
            if plan["goal_id"] in affected_goal_ids:
                plan["capacity"] = action["capacity"]
        for n in self.state["notices"]:
            if n["id"] in action["source_notice_ids"]:
                n["status"] = "已人工确认"
        return self._commit("确认修改", "演示考试与提醒队列已同步，固定课表未修改，个人计划标记待检查。没有发送真实消息。")

    def chat(self, message):
        if not message.strip():
            raise ClientError("请填写消息。")
        return {"reply": "此入口已准备好。当前是页面演示模式，未连接模型，也未自动执行规划；成员 A 接入后，消息会发送给共享 Agent。", "run_id": None}


class ApiClient:
    """真实模式只展示后端返回，不在错误时静默降级到 DemoClient。"""
    is_demo = False

    def __init__(self, base, token=""):
        parts = urlparse(base)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ClientError("后端地址应为不含账号、查询参数或片段的 HTTP(S) 地址。")
        if parts.scheme != "https" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ClientError("远程后端须使用 HTTPS；仅本机开发允许 HTTP。")
        self.base, self.token = base.rstrip("/"), token
        self.opener = build_opener(NoRedirect())

    def _request(self, method, path, payload=None):
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = None if payload is None else json.dumps(payload).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + "/v1" + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=20) as response:
                body = json.load(response)
        except HTTPError as exc:
            # 后端错误体是 {"ok": false, "error": {"code", "message"}}，
            # 409 既可能是版本冲突，也可能是课表时间冲突，必须按 code 区分。
            body = {}
            try:
                raw = exc.read()
                if raw:
                    body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError, OSError):
                body = {}
            error = body.get("error") if isinstance(body, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            server_message = error.get("message") if isinstance(error, dict) else None

            if code in {"VERSION_CONFLICT", "EXAM_VERSION_CONFLICT"}:
                message = "数据已变化，请刷新后重新预览。"
            elif code == "PREVIEW_EXPIRED":
                message = "预览已过期，请重新生成。"
            elif code == "PREVIEW_NOT_PENDING":
                message = "此预览已处理过，不能重复确认。"
            elif code in {"CLASS_TIME_CONFLICT", "TASK_ALREADY_DONE"} and server_message:
                message = server_message      # 用后端的具体说明（哪门课、什么状态）
            elif server_message:
                message = server_message
            else:
                messages = {401: "登录或测试凭据已失效。", 403: "没有权限访问这项数据。",
                            404: "没有找到这条记录。", 409: "数据已变化，请刷新后重新预览。",
                            422: "后端拒绝了输入，请检查表单。"}
                message = messages.get(exc.code, "后端暂时无法完成请求，请稍后重试。")
            raise ClientError(message, code=code) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ClientError("无法连接后端。请确认地址和服务状态；页面未切换为模拟成功。") from exc
        except (ValueError, TypeError) as exc:
            raise ClientError("后端返回格式不正确。") from exc
        if not isinstance(body, dict) or body.get("ok") is not True or not isinstance(body.get("data"), dict):
            raise ClientError("后端没有返回符合约定的成功结果。")
        return body["data"]

    def snapshot(self):
        data = self._request("GET", "/workspace")
        for key in ["version", "profile", "plans", "goals", "events", "classes", "tasks", "study_blocks", "reminders", "notices", "pending", "activity"]:
            if key not in data:
                raise ClientError("后端工作台数据缺少字段：" + key)
        if not isinstance(data["version"], int) or not isinstance(data["profile"], dict):
            raise ClientError("后端版本或用户资料的格式不正确。")
        for key in ["plans", "goals", "events", "classes", "tasks", "study_blocks", "reminders", "notices", "pending", "activity"]:
            if not isinstance(data[key], list):
                raise ClientError("后端列表字段格式不正确：" + key)
        schema = {
            "goals": {"id": str, "title": str, "event_id": str, "scope": str, "daily_max_minutes": int, "availability": str, "status": str, "version": int},
            "events": {"id": str, "title": str, "start_at": str, "location": str, "source": str, "version": int},
            "plans": {"id": str, "goal_id": str, "version": int, "status": str, "event_version": int, "source_notice_ids": list, "summary": str},
            "classes": {"id": str, "course": str, "start_at": str, "end_at": str, "location": str},
            "tasks": {"id": str, "goal_id": str, "title": str, "remaining_minutes": int, "actual_minutes": int, "status": str, "version": int},
            "study_blocks": {"id": str, "task_id": str, "plan_id": str, "plan_version": int, "start_at": str, "end_at": str, "status": str},
            "reminders": {"id": str, "event_id": str, "at": str, "status": str},
            "notices": {"id": str, "text": str, "status": str, "channel": str, "received_at": str},
            "pending": {"id": str, "kind": str, "expected_version": int, "status": str, "expires_at": str, "source_notice_ids": list, "changes": list, "preserved": list},
            "activity": {"at": str, "name": str, "detail": str},
        }
        if not isinstance(data["profile"].get("display_name"), str):
            raise ClientError("后端用户资料缺少显示名称。")
        for table, fields in schema.items():
            ids = set()
            for row in data[table]:
                if not isinstance(row, dict) or any(type(row.get(key)) is not kind for key, kind in fields.items()):
                    raise ClientError("后端记录字段格式不正确：" + table)
                if "id" in row:
                    if not row["id"] or row["id"] in ids:
                        raise ClientError("后端记录编号为空或重复：" + table)
                    ids.add(row["id"])
                for key in ("start_at", "end_at", "at", "old", "new", "remind_at", "expires_at"):
                    if key in row:
                        parse_time(row[key])
                if "daily_max_minutes" in row and not 15 <= row["daily_max_minutes"] <= 480:
                    raise ClientError("后端目标每日上限超出约定范围。")
                if any(row.get(key, 0) < 0 for key in ("remaining_minutes", "actual_minutes")):
                    raise ClientError("后端任务时长不能为负数。")
        plan_ids = {p["id"] for p in data["plans"]}
        task_ids = {t["id"] for t in data["tasks"]}
        notice_ids = {n["id"] for n in data["notices"]}
        for block in data["study_blocks"]:
            if block["plan_id"] not in plan_ids or block["task_id"] not in task_ids:
                raise ClientError("后端学习块关联的计划或任务不存在。")
        for item in data["plans"] + data["pending"]:
            if any(not isinstance(n, str) or n not in notice_ids for n in item["source_notice_ids"]):
                raise ClientError("后端通知来源引用不完整。")
        for action in data["pending"]:
            if any(not isinstance(c, dict) or any(not isinstance(c.get(k), str) for k in ("label", "before", "after")) for c in action["changes"]) or any(not isinstance(p, str) for p in action["preserved"]):
                raise ClientError("后端预览差异字段不完整。")
        goal_ids = {g["id"] for g in data["goals"]}
        event_ids = {e["id"] for e in data["events"]}
        if any(g["event_id"] not in event_ids for g in data["goals"]) or any(p["goal_id"] not in goal_ids for p in data["plans"]):
            raise ClientError("后端目标、考试或计划引用不完整。")
        for item in data["plans"] + data["pending"]:
            cap = item.get("capacity")
            if cap is not None:
                if not isinstance(cap, dict) or not isinstance(cap.get("basis"), str) or any(type(cap.get(k)) is not int or cap[k] < 0 for k in ("required_minutes", "kept_minutes", "unplaced_minutes")):
                    raise ClientError("后端时间缺口字段不完整。")
                if cap["required_minutes"] != cap["kept_minutes"]+cap["unplaced_minutes"]:
                    raise ClientError("后端时间缺口数字不一致。")
        return data

    def save_goal(self, payload, expected_version):
        return self._request("POST", "/goals", {"payload": payload, "expected_version": expected_version})

    def add_class(self, payload, expected_version):
        return self._request("POST", "/classes", {"payload": payload, "expected_version": expected_version})

    def record_progress(self, task_id, payload, expected_version):
        return self._request("POST", "/tasks/" + quote(task_id, safe="") + "/progress", {"payload": payload, "expected_version": expected_version})

    def record_time(self, task_id, actual_minutes, expected_version):
        return self._request("POST", "/tasks/" + quote(task_id, safe="") + "/time", {"actual_minutes": actual_minutes, "expected_version": expected_version})

    def save_notice(self, text, expected_version):
        return self._request("POST", "/notices", {"text": text, "expected_version": expected_version})

    def preview_exam_change(self, event_id, new_start, expected_version, notice_id):
        return self._request("POST", "/previews", {"event_id": event_id, "new_start": new_start, "expected_version": expected_version, "source_notice_ids": [notice_id]})

    def cancel_action(self, action_id):
        return self._request("POST", "/actions/" + quote(action_id, safe="") + "/cancel", {})

    def confirm_action(self, action_id, confirm_code=None):
        body = {"confirm_code": confirm_code} if confirm_code else {}
        return self._request("POST", "/actions/" + quote(action_id, safe="") + "/confirm", body)

    def chat(self, message):
        return self._request("POST", "/agent/messages", {"message": message})


def make_client(session):
    base = os.getenv("UNIPULSE_API_BASE", "").strip()
    if base:
        token = session.get("api_token", "")
        if not token and os.getenv("UNIPULSE_LOCAL_TEST") == "1":
            token = os.getenv("UNIPULSE_API_TOKEN", "")
        if not token:
            raise ClientError("请先提供本次会话的测试访问凭据；企微身份绑定尚待后端联调。")
        return ApiClient(base, token)
    if "demo_workspace" not in session:
        session["demo_workspace"] = initial_workspace()
    if session["demo_workspace"].get("schema_version") != 2:
        raise ClientError("演示数据结构已更新，请恢复示例后再体验。旧数据未被自动覆盖。")
    return DemoClient(session["demo_workspace"])
