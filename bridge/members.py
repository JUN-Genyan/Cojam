"""One conversation scheduler, with leased tasks for members on other devices.

Tasks are never replayed after a server restart or an expired lease: executing a
coding task twice can have side effects. Only a live, authenticated receiver can
advertise automatic replies. The journal contains metadata, never task prompts.
"""
import hashlib
import os
import re
import secrets
import threading
import time

from . import access, registry
from .common import LOG_DIR, read_json, write_json, timeout_for

WORKER_TTL = 60
ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")


class MemberError(Exception):
    def __init__(self, message, code=409):
        super().__init__(message)
        self.code = code


def fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest()


def current_digest(pid):
    with access.LOCK:
        return (read_json(os.path.join(LOG_DIR, "contact_keys.json")) or {}).get(pid)


class Broker:
    def __init__(self):
        self.condition = threading.Condition(threading.RLock())
        self.workers = {}
        self.tasks = {}

    def _journal(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        rows = [{k: t[k] for k in ("id", "room", "member", "state", "created", "updated", "reason")}
                for t in list(self.tasks.values())[-100:]]
        write_json(os.path.join(LOG_DIR, "member_tasks.json"), rows)

    def _state(self, task, state, reason=""):
        task.update(state=state, reason=reason, updated=time.time())
        self.condition.notify_all()
        self._journal()

    def _registered(self, pid, worker, key):
        w = self.workers.get(pid)
        if not w or w["id"] != worker or w["digest"] != fingerprint(key):
            raise MemberError("成员连接已失效，请重新连接", 403)
        return w

    def register(self, pid, key, data):
        spec = registry.get(pid)
        if not spec or spec["driver"] != "external" or spec.get("kind") == "user":
            raise MemberError("只有 Harness 成员可以领取自动响应任务", 403)
        worker = str(data.get("worker") or "")
        if not ID.fullmatch(worker):
            raise MemberError("worker 必须是 1–80 位字母数字、横线或下划线", 400)
        with self.condition:
            old = self.workers.get(pid)
            if old and old["digest"] == fingerprint(key) and old["id"] != worker and time.time() - old["seen"] < WORKER_TTL:
                raise MemberError("此联系人已有自动响应端；不同 Harness 请分别添加为成员")
            if old and (old["id"] != worker or old["digest"] != fingerprint(key)):
                for task in self.tasks.values():
                    if task["member"] == pid and task["state"] in ("queued", "claimed"):
                        self._state(task, "cancelled", "执行端已更换，旧任务不会重复执行")
            self.workers[pid] = {"id": worker, "digest": fingerprint(key), "seen": time.time(),
                                 "device": str(data.get("device") or "未命名设备")[:80],
                                 "workdir": str(data.get("workdir") or "成员自己的工作文件夹")[:500],
                                 "allow_work": data.get("allow_work") is True}
            self.condition.notify_all()
        return {"ok": True, "member": pid, "protocol": 1, "heartbeat_interval": 10}

    def status(self, pid):
        digest = current_digest(pid)
        with self.condition:
            w = self.workers.get(pid)
            live = bool(w and digest == w["digest"] and time.time() - w["seen"] < WORKER_TTL)
            return {"automatic": live, "device": w["device"] if live else "", "workdir": w["workdir"] if live else "",
                    "allow_work": bool(live and w["allow_work"]),
                    "label": "自动响应已连接" if live else "等待成员开启自动响应"}

    def _valid(self, task):
        # Do not acquire a room lock while holding the broker lock.
        from . import room as R
        r = R.rooms.get(task["room"])
        return bool(r and not r.closed and access.allowed(r, task["member"], "read") and access.allowed(r, task["member"], "post")
                    and r.job_tag(task["member"]) == task["epoch"] and current_digest(task["member"]) == task["digest"])

    def call(self, pid, prompt, session, mode, room):
        if not access.allowed(room, pid, "read") or not access.allowed(room, pid, "post"):
            return False, "此成员未加入会话或没有读取、发言权限", session, []
        mode = room.member_mode(pid, mode)
        status = self.status(pid)
        if not status["automatic"]:
            return False, "%s 尚未开启自动响应；请在它所在设备运行成员连接端，或通过 MCP 领取任务。" % pid, session, []
        digest = current_digest(pid)
        now = time.time()
        with self.condition:
            # Bound both active jobs and retained completions.
            active = sum(t["state"] in ("queued", "claimed") for t in self.tasks.values())
            if active >= 100:
                return False, "会议室待处理任务过多，请稍后重试", session, []
            for tid, old in list(self.tasks.items()):
                if len(self.tasks) <= 200:
                    break
                if old["state"] not in ("queued", "claimed"):
                    self.tasks.pop(tid)
            tid = secrets.token_urlsafe(18)
            task = {"id": tid, "room": room.id, "member": pid, "state": "queued", "created": now, "updated": now,
                    "reason": "", "digest": digest, "epoch": room.job_tag(pid), "worker": None,
                    "payload": {"protocol": 1, "id": tid, "room": room.id, "member": pid, "title": room.title(),
                                "goal": room.goal(), "prompt": prompt, "session": session or "", "mode": mode,
                                "board": [dict(b) for b in room.meta.get("board", [])],
                                "permissions": access.permissions(room, pid), "model": room.own_models(pid)[0],
                                "effort": room.own_models(pid)[1], "execution": {"location": "member", "workdir": status["workdir"]}}}
            self.tasks[tid] = task
            self._state(task, "queued")
        end = now + timeout_for(mode)[1]
        while True:
            valid = self._valid(task)
            online = self.status(pid)["automatic"]
            with self.condition:
                if task["state"] == "completed":
                    result = task["result"]
                    if not valid:
                        return False, "成员权限或任务已变更，已丢弃迟到的回复", session, []
                    return result["ok"], result["text"], result.get("session") or session, []
                if task["state"] in ("cancelled", "failed"):
                    return False, task["reason"] or "成员任务已取消", session, []
                if not valid or not online or time.time() >= end:
                    reason = "成员权限或会话状态已变更" if not valid else "成员连接中断，任务已停止；不会自动重复执行" if not online else "成员任务超时"
                    self._state(task, "cancelled", reason)
                    return False, reason, session, []
                self.condition.wait(.2)

    def _task(self, pid, key, data):
        with self.condition:
            task = self.tasks.get(str(data.get("task") or ""))
            if not task:
                raise MemberError("任务已过期或会议室已重启，不要重复执行", 410)
            if task["member"] != pid or task["digest"] != fingerprint(key) or task.get("worker") != data.get("worker"):
                raise MemberError("不能访问其他成员或连接端的任务", 403)
            if not secrets.compare_digest(task.get("lease", "!"), str(data.get("lease") or "")):
                raise MemberError("任务租约不匹配", 403)
        if not self._valid(task):
            with self.condition:
                if task["state"] != "completed":
                    self._state(task, "cancelled", "成员权限或会话状态已变更")
            raise MemberError("任务已取消，禁止继续执行", 410)
        return task

    def receive(self, pid, key, data):
        try:
            wait = max(0, min(20, int(data.get("timeout", 20))))
        except (TypeError, ValueError):
            raise MemberError("timeout 必须是整数", 400)
        end = time.time() + wait
        while True:
            if access.identify(key) != pid:
                raise MemberError("接入密钥已失效", 401)
            with self.condition:
                w = self._registered(pid, data.get("worker"), key)
                w["seen"] = time.time()
                candidates = [t for t in self.tasks.values() if t["member"] == pid and t["state"] in ("queued", "claimed")]
            for task in candidates:
                if not self._valid(task):
                    with self.condition:
                        self._state(task, "cancelled", "成员权限或会话状态已变更")
                    continue
                with self.condition:
                    if task["state"] not in ("queued", "claimed"):
                        continue
                    if task["state"] == "claimed" and task["worker"] != w["id"]:
                        continue
                    if task["state"] == "queued":
                        task.update(worker=w["id"], lease=secrets.token_urlsafe(24))
                        self._state(task, "claimed")
                    return {"task": dict(task["payload"], lease=task["lease"])}
            with self.condition:
                remaining = end - time.time()
                if remaining <= 0:
                    return {"task": None}
                self.condition.wait(min(.5, remaining))

    def heartbeat(self, pid, key, data):
        with self.condition:
            w = self._registered(pid, data.get("worker"), key)
            w["seen"] = time.time()
        if data.get("task"):
            task = self._task(pid, key, data)
            return {"ok": True, "state": task["state"]}
        return {"ok": True}

    def complete(self, pid, key, data):
        task = self._task(pid, key, data)
        if not isinstance(data.get("ok"), bool) or not isinstance(data.get("text"), str) or not data["text"].strip():
            raise MemberError("ok 必须是布尔值，text 必须为非空回复", 400)
        if len(data["text"].encode()) > 48 * 1024 or len(str(data.get("session") or "")) > 200:
            raise MemberError("回复或 session 过长", 413)
        result = {"ok": data["ok"], "text": data["text"], "session": str(data.get("session") or "")}
        with self.condition:
            if task["state"] == "completed":
                if task["result"] != result:
                    raise MemberError("任务已完成，不能用不同结果覆盖")
                return {"ok": True, "duplicate": True}
            if task["state"] != "claimed":
                raise MemberError("任务已经停止，迟到的回复不会进入会话", 410)
            task["result"] = result
            self._state(task, "completed")
        return {"ok": True, "duplicate": False}

    def progress(self, pid, key, data):
        task = self._task(pid, key, data)
        with self.condition:
            if task["state"] != "claimed":
                raise MemberError("任务已经停止", 410)
            self._registered(pid, data.get("worker"), key)["seen"] = time.time()
        text = str(data.get("text") or "").strip()
        if not text or len(text.encode()) > 16000:
            raise MemberError("进度文字为空或过长", 400)
        from .room import rooms
        r = rooms.get(task["room"])
        r.post_progress(pid, text, [])
        return {"ok": True}

    def live(self, pid, key, data):
        task = self._task(pid, key, data)
        if task["state"] != "claimed":
            raise MemberError("任务已经停止", 410)
        from .room import rooms
        rooms.get(task["room"]).push_live(pid, {"live":str(data.get("text") or "")[:400]})
        return {"ok":True}

    def cancel(self, room, members):
        with self.condition:
            for task in self.tasks.values():
                if task["room"] == room and task["member"] in members and task["state"] in ("queued", "claimed"):
                    self._state(task, "cancelled", "用户已停止此成员的任务")

    def disconnect(self, pid, key, data):
        with self.condition:
            self._registered(pid, data.get("worker"), key)
            self.workers.pop(pid, None)
            for task in self.tasks.values():
                if task["member"] == pid and task["state"] in ("queued", "claimed"):
                    self._state(task, "cancelled", "成员已断开连接")
        return {"ok": True}

    def board(self, pid, key, data):
        task = self._task(pid, key, data)
        from .room import rooms
        r = rooms.get(task["room"])
        if not access.allowed(r, pid, "board"):
            raise MemberError("此成员没有编辑看板权限", 403)
        if task["state"] != "claimed":
            raise MemberError("任务已经结束", 410)
        op = str(data.get("op") or "")
        ids = data.get("ids") or []
        if not isinstance(ids, list) or not all(isinstance(x, int) for x in ids) or len(ids) > 100:
            raise MemberError("看板编号必须是整数列表", 400)
        result = r.board_op(pid, op, str(data.get("kind") or "todo"), str(data.get("text") or ""), ids)
        if not result:
            raise MemberError("看板操作无效", 400)
        r.board_note(pid, [result])
        return {"result": result, "board": r.meta["board"]}


broker = Broker()


def handle(pid, key, action, data):
    if access.identify(key) != pid:
        raise MemberError("接入密钥无效", 401)
    if action == "connect":
        return broker.register(pid, key, data)
    if action == "receive":
        return broker.receive(pid, key, data)
    if action == "heartbeat":
        return broker.heartbeat(pid, key, data)
    if action == "reply":
        return broker.complete(pid, key, data)
    if action == "progress":
        return broker.progress(pid, key, data)
    if action == "live":
        return broker.live(pid, key, data)
    if action == "board":
        return broker.board(pid, key, data)
    if action == "disconnect":
        return broker.disconnect(pid, key, data)
    raise MemberError("没有此成员操作", 404)
