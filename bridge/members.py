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

from . import access, conn_settings, registry
from .common import LOG_DIR, read_json, write_json, timeout_for

WORKER_TTL = 60   # 仅作默认值；实际按 conn_settings 的 offline_after（可按联系人设置）
STANDBY_STOP_COOLDOWN = 600   # 点了「停止待命」后，这段时间内再调用也不会自动复活


def ttl(pid):
    try:
        return conn_settings.get("offline_after", pid)
    except Exception:
        return WORKER_TTL
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
        self.standby_stopped = {}   # pid -> 点「停止待命」的时间；冷却期内不自动复活

    def _journal(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        rows = [{k: t[k] for k in ("id", "room", "member", "state", "created", "updated", "reason")}
                for t in list(self.tasks.values())[-100:]]
        write_json(os.path.join(LOG_DIR, "member_tasks.json"), rows)

    def _state(self, task, state, reason=""):
        task.update(state=state, reason=reason, updated=time.time())
        self.condition.notify_all()
        self._journal()

    def _registered(self, pid, worker, key, room="", token="", leased=False):
        w = self.workers.get((pid, room)) if room else self.workers.get(pid)
        if not w or w["id"] != worker or w["digest"] != fingerprint(key):
            raise MemberError("成员连接已失效，请重新连接", 403)
        if room and not leased:
            from . import execution, room as R
            r = R.rooms.get(room)
            if not r or not execution.authorize(r, pid, worker, token, lifecycle=True):
                raise MemberError("会话不属于此执行端或执行端凭证已失效", 403)
        return w

    def register(self, pid, key, data):
        spec = registry.get(pid)
        if not spec or spec.get("kind") == "user":
            raise MemberError("只有成员可以领取自动响应任务", 403)
        worker = str(data.get("worker") or "")
        if not ID.fullmatch(worker):
            raise MemberError("worker 必须是 1–80 位字母数字、横线或下划线", 400)
        scope = str(data.get("room") or "")
        registration = {}
        if scope:
            if data.get("transport") != "standby":
                raise MemberError("会话独占接入需要使用 cojam_standby", 400)
            from . import execution, room as R
            room = R.rooms.get(scope)
            if not room or not access.allowed(room, pid):
                raise MemberError("未加入此会话或没有读取权限", 403)
            try:
                registration = execution.claim(room, pid, worker, str(data.get("native_session") or ""),
                                               str(data.get("binding_challenge") or ""), str(data.get("binding_token") or ""))
            except (ValueError, OSError) as error:
                raise MemberError(str(error), 409) from error
        slot = (pid, scope) if scope else pid
        with self.condition:
            old = self.workers.get(slot)
            transport = str(data.get("transport") or (old.get("transport") if old and old["id"] == worker else "") or "connector")
            # 待命通道任何驱动都能用（Claude/Codex 桌面端、ACP、external）；
            # 普通「自动响应端」仍然只给 Harness 成员，避免把本机 CLI 误当成远程执行端。
            if spec["driver"] != "external" and transport != "standby":
                raise MemberError("只有 Harness 成员可以领取自动响应任务", 403)
            if old and old["digest"] == fingerprint(key) and old["id"] != worker and time.time() - old["seen"] < ttl(pid):
                raise MemberError("此联系人已有自动响应端；不同 Harness 请分别添加为成员")
            if old and (old["id"] != worker or old["digest"] != fingerprint(key)):
                for task in self.tasks.values():
                    if task["member"] == pid and (not scope or task["room"] == scope) and task["state"] in ("queued", "claimed"):
                        self._state(task, "cancelled", "执行端已更换，旧任务不会重复执行")
            now = time.time()
            started = old.get("started", now) if (old and old["id"] == worker and old["digest"] == fingerprint(key)) else now
            self.workers[slot] = {"id": worker, "member": pid, "room": scope,
                                 "native_session": str(data.get("native_session") or ""),
                                 "digest": fingerprint(key), "seen": now, "started": started,
                                 "transport": transport,
                                 "device": str(data.get("device") or ("本机 MCP 待命" if transport == "standby" else "未命名设备"))[:80],
                                 "workdir": str(data.get("workdir") or "成员自己的工作文件夹")[:500],
                                 "allow_work": data.get("allow_work") is True}
            self.condition.notify_all()
        if scope:
            try:
                registration["binding_version"] = execution.activate(room, pid, worker, registration["binding_token"])
            except (ValueError, OSError) as error:
                with self.condition:
                    self.workers.pop(slot, None)
                raise MemberError(str(error), 409) from error
        cfg = conn_settings.member_config(pid)
        return {"ok": True, "member": pid, "protocol": 1, "heartbeat_interval": cfg["heartbeat_interval"],
                "settings": cfg, "settings_rev": cfg["rev"], **registration}

    def status(self, pid, room=None):
        digest = current_digest(pid)
        with self.condition:
            w = self.workers.get((pid, room)) if room else self.workers.get(pid)
            if not room and (not w or w["digest"] != digest or time.time() - w["seen"] >= ttl(pid)):
                w = next((value for slot, value in self.workers.items() if isinstance(slot, tuple) and slot[0] == pid
                          and value["digest"] == digest and time.time() - value["seen"] < ttl(pid)), None)
            live = bool(w and digest == w["digest"] and time.time() - w["seen"] < ttl(pid))
            transport = w.get("transport", "connector") if live else ""
            standby = bool(live and transport == "standby")
            return {"automatic": live, "standby": standby, "transport": transport,
                    "device": w["device"] if live else "", "workdir": w["workdir"] if live else "",
                    "allow_work": bool(live and w["allow_work"]),
                    "label": "MCP 待命中" if standby else ("自动响应已连接" if live else "等待成员开启自动响应")}

    def _valid(self, task):
        # Do not acquire a room lock while holding the broker lock.
        from . import room as R
        r = R.rooms.get(task["room"])
        from . import execution
        return bool(r and not r.closed and access.allowed(r, task["member"], "read") and access.allowed(r, task["member"], "post")
                    and (task.get("binding_version") is None or (execution.version(r, task["member"]) == task["binding_version"]
                         and execution.binding(r, task["member"])["phase"] == "ready"))
                    and r.job_tag(task["member"]) == task["epoch"] and current_digest(task["member"]) == task["digest"])

    def call(self, pid, prompt, session, mode, room):
        if not access.allowed(room, pid, "read") or not access.allowed(room, pid, "post"):
            return False, "此成员未加入会话或没有读取、发言权限", session, []
        from . import execution
        bound = execution.binding(room, pid)
        scope = room.id if bound["endpoint"] == "desktop" else ""
        status = self.status(pid, scope or None)
        if not status["automatic"]:
            return False, "%s 尚未开启自动响应；请在该软件里输入 /cojam_standby（或调用 ai-bridge 的 cojam_standby 工具）开启待命，或运行成员连接端。" % pid, session, []
        mode = room.member_mode(pid, mode)
        # 待命端默认只读：会话是干活模式，也要这个联系人自己允许改文件才真的进 work。
        if mode == "work" and not status.get("allow_work"):
            mode = "discuss"
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
                                "permissions": access.permissions(room, pid), "model": room.effective_models(pid)[0] if not scope else "",
                                "effort": room.effective_models(pid)[1] if not scope else "", "allow_work": bool(status.get("allow_work")),
                                "execution": {"location": "member", "workdir": status["workdir"]}}}
            if scope:
                task["binding_version"] = bound["version"]
                task["payload"]["execution"].update(location="desktop", binding_version=bound["version"], worker=bound["worker"])
            self.tasks[tid] = task
            self._state(task, "queued")
        end = now + timeout_for(mode, pid)[1]
        while True:
            valid = self._valid(task)
            online = self.status(pid, scope or None)["automatic"]
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
            wait = max(0, min(55, int(data.get("timeout", conn_settings.get("long_poll", pid)))))
        except (TypeError, ValueError):
            raise MemberError("timeout 必须是整数", 400)
        end = time.time() + wait
        scope = str(data.get("room") or "")
        while True:
            if access.identify(key) != pid:
                raise MemberError("接入密钥已失效", 401)
            with self.condition:
                if pid in self.standby_stopped:
                    return {"task": None, "stopped": True, "settings_rev": conn_settings.rev()}
                # Authorization can acquire a room lock, so perform it outside the broker lock.
                w = self.workers.get((pid, scope) if scope else pid)
            w = self._registered(pid, data.get("worker"), key, scope, str(data.get("binding_token") or ""))
            if scope:
                from . import execution, room as R
                r = R.rooms.get(scope)
                row = execution.binding(r, pid)
                if row.get("target") == "cli":
                    return {"task": None, "release": True, "settings_rev": conn_settings.rev()}
            with self.condition:
                w["seen"] = time.time()
                candidates = [t for t in self.tasks.values() if t["member"] == pid and
                              (t["room"] == scope if scope else t.get("binding_version") is None) and t["state"] in ("queued", "claimed")]
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
                    return {"task": dict(task["payload"], lease=task["lease"]), "settings_rev": conn_settings.rev()}
            with self.condition:
                remaining = end - time.time()
                if remaining <= 0:
                    return {"task": None, "settings_rev": conn_settings.rev()}
                self.condition.wait(min(.5, remaining))

    def heartbeat(self, pid, key, data):
        task = self._task(pid, key, data) if data.get("task") else None
        scope = task["room"] if task and task.get("binding_version") is not None else str(data.get("room") or "")
        w = self._registered(pid, data.get("worker"), key, scope, str(data.get("binding_token") or ""), leased=task is not None)
        with self.condition:
            w["seen"] = time.time()
        if data.get("task"):
            task = self._task(pid, key, data)
            return {"ok": True, "state": task["state"], "settings_rev": conn_settings.rev()}
        return {"ok": True, "settings_rev": conn_settings.rev()}

    def complete(self, pid, key, data):
        task = self._task(pid, key, data)
        if not isinstance(data.get("ok"), bool) or not isinstance(data.get("text"), str) or not data["text"].strip():
            raise MemberError("ok 必须是布尔值，text 必须为非空回复", 400)
        if len(data["text"].encode()) > 48 * 1024 or len(str(data.get("session") or "")) > 200:
            raise MemberError("回复或 session 过长", 413)
        result = {"ok": data["ok"], "text": data["text"], "session": str(data.get("session") or "")}
        if task.get("binding_version") is not None:
            expected = task["payload"]["session"]
            if result["session"] and result["session"] != expected:
                raise MemberError("桌面端不能替换共享的原生会话", 409)
            result["session"] = expected
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
            scope = task["room"] if task.get("binding_version") is not None else ""
            self.workers[(pid, scope) if scope else pid]["seen"] = time.time()
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
        scope = str(data.get("room") or "")
        self._registered(pid, data.get("worker"), key, scope, str(data.get("binding_token") or ""))
        with self.condition:
            self.workers.pop((pid, scope) if scope else pid, None)
            for task in self.tasks.values():
                if task["member"] == pid and (not scope or task["room"] == scope) and task["state"] in ("queued", "claimed"):
                    self._state(task, "cancelled", "成员已断开连接")
        return {"ok": True}

    def stop_standby(self, pid):
        now = time.time()
        with self.condition:
            self.standby_stopped[pid] = now
            # 顺手清掉已经过期的标记，避免这个集合无限长
            for old, at in list(self.standby_stopped.items()):
                if now - at >= STANDBY_STOP_COOLDOWN:
                    self.standby_stopped.pop(old, None)
            self.workers.pop(pid, None)
            for slot in list(self.workers):
                if isinstance(slot, tuple) and slot[0] == pid:
                    self.workers.pop(slot, None)
            for task in self.tasks.values():
                if task["member"] == pid and task["state"] in ("queued", "claimed"):
                    self._state(task, "cancelled", "Cojam 端已停止此成员的待命状态")
            self.condition.notify_all()
        return {"ok": True, "member": pid, "stopped": True, "resume_after": STANDBY_STOP_COOLDOWN}

    def standby(self, pid, key, data):
        scope = str(data.get("room") or "")
        worker = str(data.get("worker") or ("standby-" + fingerprint(key + scope)[:16]))
        with self.condition:
            stopped_at = self.standby_stopped.get(pid)
            if stopped_at is not None:
                left = int(STANDBY_STOP_COOLDOWN - (time.time() - stopped_at))
                if left > 0:
                    self.workers.pop(pid, None)
                    return {"status": "stopped", "member": pid, "worker": worker, "resume_after": left,
                            "next_step": "Cojam 端已停止待命（%d 分钟内不会恢复）。请停止循环调用 cojam_standby 并告知用户待命已结束。" % max(1, left // 60)}
                self.standby_stopped.pop(pid, None)
        reg_data = {
            "worker": worker,
            "transport": "standby",
            "device": str(data.get("device") or "本机 MCP 待命"),
            "workdir": str(data.get("workdir") or os.getcwd()),
            "allow_work": data.get("allow_work") is True,
            "room": scope, "native_session": data.get("native_session"),
            "binding_challenge": data.get("binding_challenge"), "binding_token": data.get("binding_token"),
        }
        # A release request must be acknowledged without trying to register a new owner.
        if scope:
            from . import execution, room as R
            r = R.rooms.get(scope)
            if r and execution.binding(r, pid).get("target") == "cli" and execution.authorize(r, pid, worker, str(data.get("binding_token") or ""), lifecycle=True):
                return {"status": "release", "room": scope, "worker": worker,
                        "next_step": "会议室请求释放此会话。停止执行任务，调用 release_session(room, worker, binding_token)，之后结束待命循环。"}
        registration = self.register(pid, key, reg_data)
        scoped_fields = {k: registration[k] for k in ("binding_token", "binding_version") if k in registration}
        with self.condition:
            w = self.workers.get((pid, scope) if scope else pid)
            max_sec = conn_settings.get("standby_max", pid) * 60
            if w and time.time() - w.get("started", time.time()) > max_sec:
                self.workers.pop((pid, scope) if scope else pid, None)
                return {"status": "expired", "member": pid, "worker": worker,
                        "next_step": "已达到 Cojam 设定的最长待命时长（%d 分钟），请结束待命并告知用户。" % conn_settings.get("standby_max", pid)}
        poll = data.get("timeout", conn_settings.get("standby_poll", pid))
        res = self.receive(pid, key, {"worker": worker, "timeout": poll, "room": scope,
                                    "binding_token": registration.get("binding_token") or data.get("binding_token")})
        if res.get("release"):
            return {"status": "release", "room": scope, "worker": worker, **scoped_fields,
                    "next_step": "停止接单，调用 release_session(room, worker, binding_token) 释放原生会话，然后结束待命。"}
        if res.get("stopped"):
            with self.condition:
                self.workers.pop((pid, scope) if scope else pid, None)
            left = int(STANDBY_STOP_COOLDOWN - (time.time() - self.standby_stopped.get(pid, 0)))
            return {"status": "stopped", "member": pid, "worker": worker, "resume_after": max(0, left),
                    "next_step": "Cojam 端已停止待命，请停止循环调用 cojam_standby 并告知用户待命已结束。"}
        task = res.get("task")
        if task:
            return {
                "status": "task",
                "member": pid,
                "worker": worker,
                "task": task,
                "room": scope, **scoped_fields,
                "settings_rev": res.get("settings_rev"),
                "next_step": ("收到 Cojam 会话「%s」的任务！请按 prompt 要求处理，完成后调用 "
                              "reply_task(worker=%r, task=%r, lease=%r, ok=true, text='你的回复')，"
                              "提交后再调用 cojam_standby 继续待命。") % (task.get("title") or task.get("room"), worker, task["id"], task["lease"]),
            }
        return {
            "status": "idle",
            "member": pid,
            "worker": worker,
            "room": scope, **scoped_fields,
            "settings_rev": res.get("settings_rev"),
            "next_step": "暂无数新任务；请不要输出多余文字，立即再次调用 cojam_standby 继续待命。",
        }

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
    if action == "standby":
        return broker.standby(pid, key, data)
    if action == "release":
        from . import execution, room as R
        r = R.rooms.get(str(data.get("room") or ""))
        if not r or not access.allowed(r, pid):
            raise MemberError("无此会话权限", 403)
        try:
            result = execution.release(r, pid, str(data.get("worker") or ""), str(data.get("binding_token") or ""))
        except (ValueError, OSError) as error:
            raise MemberError(str(error), 409) from error
        with broker.condition:
            broker.workers.pop((pid, r.id), None)
            broker.condition.notify_all()
        return dict(result, status="stopped", next_step="原生会话已释放给 CLI，请结束此待命循环。")
    if action == "settings":
        cfg = conn_settings.member_config(pid)
        return {"ok": True, "settings": cfg, "settings_rev": cfg["rev"]}
    raise MemberError("没有此成员操作", 404)
