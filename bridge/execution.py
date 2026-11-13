"""Per-conversation endpoint ownership and authenticated native-session handoff."""
import copy
import hashlib
import json
import os
import secrets
import threading
import time

from . import registry

LOCK = threading.RLock()
HANDOFF_SECONDS = 300


def binding(room, pid):
    spec = registry.get(pid) or {}
    default = "member" if spec.get("driver") == "external" else "http" if spec.get("driver") == "http" else "cli"
    row = getattr(room, "meta", {}).get("execution", {}).get(pid)
    if not row:
        return {"endpoint": default, "phase": "ready", "version": 0, "worker": ""}
    # Expiry restores the previous owner, never changes endpoints by availability.
    if row.get("phase") != "ready" and row.get("expires", 0) <= time.time():
        with room.lock:
            previous = copy.deepcopy(row["previous"])
            previous["version"] = row["version"] + 1
            room.meta["execution"][pid] = previous
            room.save_meta()
            return dict(previous)
    return dict(row)


def version(room, pid):
    return binding(room, pid)["version"]


def local(room, pid):
    row = binding(room, pid)
    return row["phase"] == "ready" and row["endpoint"] == "cli"


def remote(room, pid):
    return binding(room, pid)["endpoint"] in ("desktop", "member")


def describe(room, pid):
    from . import members, native_session
    row = binding(room, pid)
    result = {key: row.get(key) for key in ("endpoint", "phase", "version", "worker", "target", "expires")}
    result["native_session"] = room.meta.get(pid + "_session") or ""
    result["handoff_supported"] = bool(native_session.provider(pid))
    if row["endpoint"] == "desktop":
        status = members.broker.status(pid, room.id)
        result.update(online=status["automatic"], label="桌面 · 待命在线" if status["automatic"] else "桌面 · 离线")
        result["observed"] = native_session.observed(pid, result["native_session"])
    else:
        result["label"] = {"cli": "CLI 直连", "member": "外部成员", "http": "HTTP 服务"}.get(row["endpoint"], row["endpoint"])
    if row["phase"] != "ready":
        result["label"] = "等待桌面接管" if row.get("target") == "desktop" else "等待桌面释放"
    return result


def authorize(room, pid, worker="", token="", lifecycle=False):
    row = binding(room, pid)
    if row["endpoint"] in ("member", "http") and row["phase"] == "ready":
        return True  # Legacy external principals keep their existing permissions.
    valid_phase = row["phase"] == "ready" or (lifecycle and (row.get("target") == "cli" or row["phase"] == "registering"))
    if row["endpoint"] != "desktop" or not valid_phase or not token or row.get("worker") != worker:
        return False
    matched = secrets.compare_digest(row.get("token_digest", "!"), hashlib.sha256(token.encode()).hexdigest())
    if matched and not lifecycle:
        from . import members
        return members.broker.status(pid, room.id)["automatic"]
    return matched


def _check_unique(room, pid, sid):
    if not sid:
        return
    from . import room as R, native_session
    kind = native_session.provider(pid)
    # Include unloaded rooms without loading their message histories or taking other room locks.
    metas = {r.id: r.meta for r in R.rooms.all()}
    if os.path.isdir(R.MEET_DIR):
        for name in os.listdir(R.MEET_DIR):
            if name not in metas:
                try:
                    with open(os.path.join(R.MEET_DIR, name, "meeting.json"), encoding="utf-8") as stream:
                        metas[name] = json.load(stream)
                except (OSError, ValueError):
                    continue
    for rid, meta in metas.items():
        for other in meta.get("participants", []):
            if (rid, other) != (room.id, pid) and native_session.provider(other) == kind and meta.get(other + "_session") == sid:
                raise ValueError("该原生会话已绑定其他会议或联系人，不能重复接管")


def prepare(room, pid, endpoint, expected):
    from . import access, acp, agents, members, native_session
    if not native_session.provider(pid):
        raise ValueError("此 Agent 尚无已验证的原生会话共享适配")
    with LOCK, room.lock:
        if pid not in room.ais or not access.allowed(room, pid):
            raise ValueError("此 Agent 不在当前会话或没有读取权限")
        row = binding(room, pid)
        if row["version"] != expected:
            raise ValueError("执行端已变更，请刷新后重试")
        if room.busy_for() or room.meta["handed_off"].get(pid):
            raise ValueError("会话正在运行、排队、压缩或已交给终端，请结束后再切换")
        with members.broker.condition:
            if any(task["room"] == room.id and task["member"] == pid and task["state"] in ("queued", "claimed")
                   for task in members.broker.tasks.values()):
                raise ValueError("此原生会话仍有待处理任务，不能切换")
        if endpoint not in ("cli", "desktop"):
            raise ValueError("执行端只能选择 CLI 或桌面")
        if row["phase"] != "ready":
            raise ValueError("已有接管正在进行，请先取消或完成")
        if endpoint == row["endpoint"]:
            return {"execution": describe(room, pid)}
        if endpoint == "cli" and not agents.agent_executable(pid):
            raise ValueError("找不到配置的 CLI，请先修正程序位置")
        if endpoint == "desktop":
            from . import connections
            if not connections.status(pid).get("has_key"):
                raise ValueError("请先在 Agent 设置中应用桌面 MCP 配置，再接管会话")
        sid = room.meta.get(pid + "_session") or ""
        if sid:
            native_session.locate(pid, sid)
            _check_unique(room, pid, sid)
        selected_model, selected_effort = room.own_models(pid)
        if endpoint == "desktop" and (selected_model or selected_effort):
            raise ValueError("此桌面接入没有模型写入接口，请先明确恢复会议模型默认值，再接管；不会静默丢弃当前选择")
        previous = copy.deepcopy(row)
        pending = dict(row, phase="preparing" if endpoint == "desktop" else "releasing", target=endpoint,
                       version=row["version"] + 1, previous=previous, expires=time.time() + HANDOFF_SECONDS)
        challenge = secrets.token_urlsafe(24) if endpoint == "desktop" else ""
        if challenge:
            pending["challenge_digest"] = hashlib.sha256(challenge.encode()).hexdigest()
        room.meta.setdefault("execution", {})[pid] = pending
        try:
            room.save_meta()
        except OSError:
            room.meta["execution"][pid] = previous
            raise
        if endpoint == "desktop":
            agents.stop_codex(room)
            acp.stop(room, pid)
        elif not members.broker.status(pid, room.id)["automatic"]:
            return _finish_cli(room, pid, pending)
        result = {"execution": describe(room, pid)}
        if challenge:
            args = {"room": room.id, "native_session": sid or "当前原生会话 ID", "binding_challenge": challenge}
            result["instruction"] = "在同一条原生会话中调用 ai-bridge 的 cojam_standby，参数：" + json.dumps(args, ensure_ascii=False)
            command = agents.agent_executable(pid)
            if native_session.provider(pid) == "claude" and sid and command:
                import shlex
                command += list(((registry.get(pid) or {}).get("args") or {}).get("base") or []) + ["--desktop", "--resume", sid]
                result["native_command"] = "& " + " ".join("'" + value.replace("'", "''") + "'" for value in command) if os.name == "nt" else shlex.join(command)
            else:
                result["native_command"] = ""
        return result


def cancel(room, pid, expected):
    with LOCK, room.lock:
        row = binding(room, pid)
        if row["version"] != expected or row["phase"] == "ready":
            raise ValueError("接管已变更或已完成，请刷新")
        previous = copy.deepcopy(row["previous"])
        previous["version"] = row["version"] + 1
        room.meta["execution"][pid] = previous
        try:
            room.save_meta()
        except OSError:
            room.meta["execution"][pid] = row
            raise
        return {"execution": describe(room, pid)}


def claim(room, pid, worker, sid, challenge="", token=""):
    from . import native_session
    with LOCK, room.lock:
        row = binding(room, pid)
        if authorize(room, pid, worker, token, lifecycle=True):
            if sid != room.meta.get(pid + "_session"):
                raise ValueError("待命端不能改变原生会话")
            return {"binding_token": token, "binding_version": row["version"]}
        if row["phase"] != "preparing" or row.get("target") != "desktop" or not challenge:
            raise ValueError("请先在会议室选择桌面接管，此端不能自行抢占会话")
        if not secrets.compare_digest(row.get("challenge_digest", "!"), hashlib.sha256(challenge.encode()).hexdigest()):
            raise ValueError("接管凭证不正确或已失效")
        original = room.meta.get(pid + "_session")
        if original and original != sid:
            raise ValueError("必须续接同一原生会话，不能新开或替换")
        _check_unique(room, pid, sid)
        native_session.prove(pid, sid, challenge, room.workdir())
        endpoint_token = secrets.token_urlsafe(32)
        updated = {"endpoint": "desktop", "phase": "registering", "target": "desktop", "worker": worker,
                   "version": row["version"] + 1, "previous": row["previous"], "expires": row["expires"],
                   "token_digest": hashlib.sha256(endpoint_token.encode()).hexdigest()}
        room.meta["execution"][pid] = updated
        room.meta[pid + "_session"] = sid
        try:
            room.save_meta()
        except OSError:
            room.meta["execution"][pid] = row
            if original is None:
                room.meta.pop(pid + "_session", None)
            else:
                room.meta[pid + "_session"] = original
            raise
        return {"binding_token": endpoint_token, "binding_version": updated["version"]}


def activate(room, pid, worker, token):
    """Publish readiness only after the scoped worker exists; no dispatch gap during registration."""
    with LOCK, room.lock:
        row = binding(room, pid)
        if not authorize(room, pid, worker, token, lifecycle=True):
            raise ValueError("执行端凭证已失效")
        if row["phase"] == "registering":
            updated = {key: row[key] for key in ("endpoint", "worker", "token_digest")}
            updated.update(phase="ready", version=row["version"] + 1)
            room.meta["execution"][pid] = updated
            try:
                room.save_meta()
            except OSError:
                room.meta["execution"][pid] = row
                raise
            row = updated
        return row["version"]


def _finish_cli(room, pid, row):
    from . import agents
    if not agents.agent_executable(pid):
        raise ValueError("找不到配置的 CLI，请先修正程序位置")
    room.meta["execution"][pid] = {"endpoint": "cli", "phase": "ready", "worker": "", "version": row["version"] + 1}
    try:
        room.save_meta()
    except OSError:
        room.meta["execution"][pid] = row
        raise
    return {"execution": describe(room, pid)}


def release(room, pid, worker, token):
    with LOCK, room.lock:
        row = binding(room, pid)
        if row.get("target") != "cli" or not authorize(room, pid, worker, token, lifecycle=True):
            raise ValueError("没有此原生会话的释放权限或请求已失效")
        if room.busy_for():
            raise ValueError("会话仍有任务，不能释放")
        return _finish_cli(room, pid, row)
