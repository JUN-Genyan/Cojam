"""Editable contact preferences. Only the owner-facing form exposes these values."""
import copy
import hashlib
import json
import os
import re
import threading

from . import registry
from .common import read_json, write_json

LOCK = threading.RLock()
ARG_KEYS = ("base", "new", "resume", "read", "work", "model", "effort", "acp")
PERMISSIONS = {"read": True, "post": True, "trigger": True, "board": False, "files": False, "work": False}
EDITABLE = {"label", "bin", "args", "prompt", "context", "models", "efforts", "model_efforts",
            "default", "desktop", "default_join", "default_permissions", "endpoint"}


def _path():
    return os.path.join(registry.LOG_DIR, "agent_settings.json")


def patches():
    data = read_json(_path(), {})
    return data if isinstance(data, dict) else {}


def overlay(raw, saved):
    """Apply preferences last, including to built-ins, without changing identity or driver."""
    result = dict(raw)
    patch = saved.get(raw.get("id"), {})
    if isinstance(patch, dict):
        result.update({key: copy.deepcopy(value) for key, value in patch.items() if key in EDITABLE})
        result["settings_fields"] = sorted(key for key in patch if key in EDITABLE)
    return result


def _revision(spec):
    values = {key: spec.get(key) for key in EDITABLE}
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def view(pid):
    from . import agents, connections, discovery, install
    spec = registry.get(pid)
    if not spec or spec["kind"] == "user":
        raise ValueError("Agent 不存在")
    values = {key: copy.deepcopy(spec.get(key)) for key in EDITABLE}
    desktop = values["desktop"] or {}
    entry = next((row for row in discovery.load_catalog() if row["id"] == pid), {})
    # Old separate desktop presets are distinct contacts, but can use the same host format.
    host_id = desktop.get("host", (entry.get("mcp_desktop") or entry.get("mcp") or {}).get("id", ""))
    if not host_id and pid.endswith("_desktop"):
        host_id = pid[:-8]
    host = next((row for row in install.HOSTS if row["id"] == host_id), {})
    desktop = dict(desktop, host=host_id, mcp_path=desktop.get("mcp_path", host.get("path", "")))
    if "path" not in desktop:
        candidates = (entry.get("desktop") or {}).get("detect", []) or spec.get("detect", [])
        desktop["path"] = next((os.path.expandvars(os.path.expanduser(p)) for p in candidates
                                if os.path.exists(os.path.expandvars(os.path.expanduser(p)))), "")
    values["desktop"] = desktop
    capabilities = agents.model_capabilities(pid)
    return {"id": pid, "driver": spec["driver"], "builtin": spec["builtin"], "enabled": spec["enabled"],
            "revision": _revision(spec), "values": values, "capabilities": capabilities,
            "connection": connections.status(pid),
            "hosts": [{"id": h["id"], "label": h["label"], "kind": h["kind"], "path": h.get("path", "")}
                      for h in install.HOSTS]}


def _strings(value, label, limit=100):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(v, str) or len(v) > 2000 or "\x00" in v for v in value):
        raise ValueError(label + "格式不正确")
    return list(value)


def validate(changes):
    if not isinstance(changes, dict) or set(changes) - EDITABLE:
        raise ValueError("包含不可修改的 Agent 字段")
    out = copy.deepcopy(changes)
    for key in ("label", "endpoint"):
        if key in out and (not isinstance(out[key], str) or "\x00" in out[key] or len(out[key]) > (40 if key == "label" else 2000)):
            raise ValueError("名称或服务地址格式不正确")
    if "label" in out and not out["label"].strip():
        raise ValueError("请填写 Agent 名称")
    if "endpoint" in out and out["endpoint"]:
        from urllib.parse import urlsplit
        url = urlsplit(out["endpoint"])
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("请填写有效的 HTTP / HTTPS 服务地址")
    for key in ("bin", "models", "efforts", "default"):
        if key in out:
            out[key] = _strings(out[key], key)
    if "default" in out and len(out["default"]) != 2:
        raise ValueError("默认设置需要模型与 effort")
    for key in ("models", "efforts", "default"):
        if key in out and any(v and not re.fullmatch(r"[A-Za-z0-9][\w.\-:/\[\]]{0,79}", v) for v in out[key]):
            raise ValueError("模型或 effort ID 格式不正确")
    if "args" in out:
        if not isinstance(out["args"], dict) or set(out["args"]) - set(ARG_KEYS):
            raise ValueError("启动参数格式不正确")
        out["args"] = {key: _strings(value, "启动参数") for key, value in out["args"].items()}
    if "model_efforts" in out:
        if not isinstance(out["model_efforts"], dict) or len(out["model_efforts"]) > 100:
            raise ValueError("模型思考强度格式不正确")
        out["model_efforts"] = {key: _strings(value, "思考强度") for key, value in out["model_efforts"].items()}
        if any(not re.fullmatch(r"[A-Za-z0-9][\w.\-:/\[\]]{0,79}", key) for key in out["model_efforts"]):
            raise ValueError("模型 ID 格式不正确")
        if any(not re.fullmatch(r"[A-Za-z0-9_\-]{1,20}", level)
               for levels in out["model_efforts"].values() for level in levels):
            raise ValueError("effort ID 格式不正确")
    for key, allowed in (("prompt", ("arg", "stdin")), ("context", ("delta", "full"))):
        if key in out and out[key] not in allowed:
            raise ValueError("提示词或上下文传递方式不正确")
    if "default_join" in out and out["default_join"] is not None and not isinstance(out["default_join"], bool):
        raise ValueError("默认加入设置不正确")
    if "default_permissions" in out:
        perms = out["default_permissions"]
        if not isinstance(perms, dict) or set(perms) - set(PERMISSIONS) or any(not isinstance(v, bool) for v in perms.values()):
            raise ValueError("默认权限格式不正确")
        out["default_permissions"] = dict(PERMISSIONS, **perms)
    if "desktop" in out:
        desktop = out["desktop"]
        if not isinstance(desktop, dict) or set(desktop) - {"path", "host", "mcp_path"}:
            raise ValueError("桌面设置格式不正确")
        if any(not isinstance(v, str) or len(v) > 2000 or "\x00" in v for v in desktop.values()):
            raise ValueError("桌面程序路径格式不正确")
        from . import install
        if desktop.get("host") and desktop["host"] not in {h["id"] for h in install.HOSTS}:
            raise ValueError("请选择受支持的桌面软件")
        for field in ("path", "mcp_path"):
            path = desktop.get(field)
            if path and not os.path.isabs(os.path.expandvars(os.path.expanduser(path))):
                raise ValueError("桌面程序和 MCP 保存位置需要完整路径")
    return out


def save(pid, changes, revision):
    with LOCK:
        with registry._locker():
            registry.load()
            spec = registry.get(pid)
            if not spec or spec["kind"] == "user":
                raise ValueError("Agent 不存在")
            if revision != _revision(spec):
                raise ValueError("配置已在其他窗口更新，请重新打开设置")
            patch = validate(changes)
            saved = patches()
            previous = saved.get(pid, {})
            merged = dict(previous, **patch)
            candidate = registry._norm(overlay(spec, {pid: merged}))
            model, effort = candidate["default"]
            choices = candidate["model_efforts"].get(model, candidate["efforts"])
            if pid == "codex" and model not in candidate["model_efforts"]:
                from . import agents
                choices = dict(agents.codex_catalog()).get(model, choices)
            if effort and any(key in patch for key in ("default", "efforts", "model_efforts")) and effort not in choices:
                raise ValueError("默认 effort 不在此模型的可选范围内")
            saved[pid] = merged
            write_json(_path(), saved)
            registry.load()
        return view(pid)


def install_mcp(pid, replace_key, revision):
    """Stage a host write before replacing the contact credential."""
    import secrets
    from . import access, install, members
    with LOCK:
        config = view(pid)
        if config["revision"] != revision:
            raise ValueError("配置已更新，请重新打开设置")
        desktop = config["values"]["desktop"]
        host = next((dict(h) for h in install.HOSTS if h["id"] == desktop.get("host")), None)
        if not host:
            raise ValueError("请先选择桌面软件并保存设置")
        if members.broker.status(pid).get("automatic"):
            raise ValueError("请先停止此 Agent 的待命或成员连接，再应用 MCP 配置")
        if config["connection"].get("has_key") and not replace_key:
            raise ValueError("应用 MCP 需要更换已有接入密钥，请先确认")
        if desktop.get("path"):
            host["detect"] = [os.path.expandvars(os.path.expanduser(desktop["path"]))] + host.get("detect", [])
        if desktop.get("mcp_path"):
            host["path"] = os.path.expandvars(os.path.expanduser(desktop["mcp_path"]))
        if not install.present(host):
            raise ValueError("未找到桌面程序，请检查程序位置")
        path = host.get("path")
        original = None
        existed = bool(path and os.path.isfile(path))
        if existed:
            with open(path, "rb") as stream:
                original = stream.read()
        token = secrets.token_urlsafe(32)
        # Credential resets and incoming authentication cannot race with this commit.
        with access.LOCK:
            key_path = os.path.join(registry.LOG_DIR, "contact_keys.json")
            keys = read_json(key_path, {}) or {}
            if keys.get(pid) and not replace_key:
                raise ValueError("接入密钥已更新，请重新打开设置并确认")
            try:
                ok, message = install.INSTALLERS[host["kind"]](dict(host, token=token))
                if not ok:
                    raise ValueError(message)
                updated = dict(keys, **{pid: hashlib.sha256(token.encode()).hexdigest()})
                write_json(key_path, updated)
            except Exception:
                if path and existed:
                    with open(path, "wb") as stream:
                        stream.write(original)
                elif path and os.path.isfile(path):
                    os.remove(path)
                raise
        return message + "。请重启桌面软件，并在要接入的会话中开启 /cojam_standby。"
