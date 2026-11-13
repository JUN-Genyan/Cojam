"""Read-only native-session adapters. Never invent a replacement conversation."""
import glob
import json
import os
import re
import threading

from . import registry

SID = re.compile(r"^[a-zA-Z0-9_-]{1,120}$")
LOCK = threading.RLock()
PATHS = {}
OBSERVED = {}


def provider(pid):
    spec = registry.get(pid) or {}
    return spec.get("driver") if spec.get("driver") in ("claude", "codex") else ""


def locate(pid, sid):
    if not SID.fullmatch(str(sid or "")):
        raise ValueError("原生会话 ID 格式不正确")
    from . import agents
    kind = provider(pid)
    root = agents.CLAUDE_HOME if kind == "claude" else agents.CODEX_HOME
    cache_key = (kind, root, sid)
    with LOCK:
        cached = PATHS.get(cache_key)
    if cached and os.path.isfile(cached):
        return cached
    if kind == "claude":
        candidates = glob.glob(os.path.join(agents.CLAUDE_HOME, "projects", "*", sid + ".jsonl"))
    elif kind == "codex":
        candidates = []
        for name in ("sessions", "archived_sessions"):
            candidates += glob.glob(os.path.join(agents.CODEX_HOME, name, "**", "*" + sid + ".jsonl"), recursive=True)
    else:
        raise ValueError("此 Agent 尚无已验证的原生会话共享适配，只能使用当前执行端")
    for path in candidates:
        if os.path.isfile(path):
            with LOCK:
                if len(PATHS) > 512:
                    PATHS.clear()
                PATHS[cache_key] = path
            return path
    raise ValueError("本机未找到该原生会话；请在同一台设备、同一会话存储中打开，不能用另一条对话代替")


def tail(path):
    with open(path, "rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - 2 * 1024 * 1024))
        lines = stream.read().decode("utf-8", "replace").splitlines()
    rows = []
    for line in lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        except ValueError:
            continue
    return rows


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def prove(pid, sid, challenge, cwd):
    """A nonce in an actual native tool-call log proves which session called MCP."""
    path = locate(pid, sid)
    matched_cwd = False
    # Metadata is at the start even when a long conversation's tail no longer includes it.
    with open(path, "rb") as stream:
        first = stream.read(65536).decode("utf-8", "replace").splitlines()
    rows = tail(path)
    for line in first:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    for root in rows:
        if not isinstance(root, dict):
            continue
        value = root.get("cwd")
        if root.get("type") in ("session_meta", "turn_context"):
            value = (root.get("payload") or {}).get("cwd")
        if isinstance(value, str) and os.path.normcase(os.path.realpath(value)) == os.path.normcase(os.path.realpath(cwd)):
            matched_cwd = True
    if not matched_cwd:
        raise ValueError("原生会话的工作目录与会议不一致，无法接管")
    for root in rows:
        if not isinstance(root, dict):
            continue
        calls = []
        payload = root.get("payload") or {}
        if root.get("type") == "response_item" and isinstance(payload, dict) and payload.get("type") in ("function_call", "custom_tool_call"):
            calls.append(payload)
        message = root.get("message") or {}
        if isinstance(message, dict) and message.get("role") == "assistant":
            calls += [item for item in message.get("content", []) if isinstance(item, dict) and item.get("type") == "tool_use"] if isinstance(message.get("content"), list) else []
        for item in calls:
            name = str(item.get("name") or "")
            args = item.get("input", item.get("arguments"))
            if name in ("exec", "functions.exec") and isinstance(args, str):
                # Codex's tool orchestration can log the JS invocation rather than a direct MCP item.
                pattern = r"[\"']?binding_challenge[\"']?\s*:\s*([\"'])" + re.escape(challenge) + r"\1"
                if re.search(r"cojam_standby[^\w]{0,5}\(", args) and re.search(pattern, args):
                    return observed(pid, sid)
            if "cojam_standby" not in name:
                continue
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    continue
            if isinstance(args, dict) and args.get("binding_challenge") == challenge:
                return observed(pid, sid)
    raise ValueError("尚未在该原生会话的工具记录中确认接管凭证，请从指定原生会话调用待命工具后重试")


def observed(pid, sid):
    try:
        path = locate(pid, sid)
        stat = os.stat(path)
        signature = (stat.st_mtime_ns, stat.st_size)
        with LOCK:
            cached = OBSERVED.get(path)
        if cached and cached[0] == signature:
            return dict(cached[1])
        rows = tail(path)
    except (ValueError, OSError):
        return {"model": "", "effort": "", "source": "未回报"}
    model = effort = ""
    for row in rows:
        if row.get("type") == "turn_context":
            item = row.get("payload") or {}
            model = item.get("model") or model
            effort = item.get("effort") or item.get("reasoning_effort") or effort
        message = row.get("message") or {}
        if isinstance(message, dict) and message.get("role") == "assistant":
            model = message.get("model") or model
    result = {"model": model, "effort": effort, "source": "原生会话记录" if model else "未回报"}
    with LOCK:
        if len(OBSERVED) > 512:
            OBSERVED.clear()
        OBSERVED[path] = (signature, result)
    return dict(result)
