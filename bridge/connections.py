"""Connection evidence for the onboarding UI; owner polling never counts as a connection."""
import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import access, registry
from .common import LOG_DIR, NO_WINDOW, read_json, write_json

LOCK = threading.RLock()
FRESH_SECONDS = 90   # 默认值；实际 = 1.5 × Cojam「连接与心跳」里的掉线时间


def fresh_seconds(pid=None):
    try:
        from . import conn_settings
        return int(conn_settings.get("offline_after", pid) * 1.5)
    except Exception:
        return FRESH_SECONDS


def _path():
    return os.path.join(LOG_DIR, "connections.json")


def _signature(spec):
    fields = {k: spec.get(k) for k in ("driver", "bin", "args", "endpoint", "token", "headers", "enabled")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def forget(pid):
    with LOCK:
        data = read_json(_path()) or {}
        data.pop(pid, None)
        os.makedirs(LOG_DIR, exist_ok=True)
        write_json(_path(), data)


def seen(pid, key, channel, client=""):
    """Record evidence only from a currently authenticated external request."""
    # Holding the credential lock prevents a reset racing with this write.
    with access.LOCK:
        if access.identify(key) != pid:
            return
        with LOCK:
            data = read_json(_path()) or {}
            digest = hashlib.sha256(key.encode()).hexdigest()
            old = data.get(pid, {})
            same = old.get("digest") == digest
            if same and not client and channel == "http" and time.time() - old.get("last_seen", 0) < 3:
                return
            data[pid] = {"digest": digest, "last_seen": time.time(),
                         "channel": old.get("channel", channel) if same and channel == "http" else channel,
                         "client": str(client or (old.get("client") if same else "") or "")[:60]}
            os.makedirs(LOG_DIR, exist_ok=True)
            write_json(_path(), data)


def status(pid):
    spec = registry.get(pid)
    if not spec:
        return {"state": "missing", "label": "联系人不存在", "has_key": False}
    with access.LOCK:
        keys = read_json(os.path.join(LOG_DIR, "contact_keys.json")) or {}
        digest = keys.get(pid)
        with LOCK:
            evidence = (read_json(_path()) or {}).get(pid, {})
    if spec["driver"] == "external":
        valid = bool(digest and evidence.get("digest") == digest)
        age = max(0, time.time() - evidence.get("last_seen", 0)) if valid else None
        connected = age is not None and age <= fresh_seconds(pid)
        state = "connected" if connected else "offline" if valid else "waiting" if digest else "unconfigured"
        return {"state": state, "label": {"connected": "已连接", "offline": "暂未收到新连接", "waiting": "等待对方连接", "unconfigured": "尚未配置连接"}[state],
                "has_key": bool(digest), "last_seen": evidence.get("last_seen") if valid else None,
                "channel": evidence.get("channel", "") if valid else "", "client": evidence.get("client", "") if valid else ""}
    valid = evidence.get("signature") == _signature(spec)
    ready = valid and evidence.get("ok") is True
    return {"state": "ready" if ready else "failed" if valid else "unchecked", "has_key": bool(digest),
            "label": "回调已验证" if ready and spec["driver"] == "http" else "程序可启动" if ready else "验证失败" if valid else "未验证",
            "checked_at": evidence.get("checked_at") if valid else None, "detail": evidence.get("detail", "") if valid else ""}


def catalog():
    return {s["id"]: status(s["id"]) for s in registry.all_specs()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_check(spec):
    url = urllib.parse.urlsplit(spec["endpoint"])
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("请填写 HTTP / HTTPS 服务地址，不要在地址中放用户名或密码")
    body = {"type": "connection_test", "probe": True, "room": "", "room_title": "连接验证", "speaker": spec["id"],
            "mode": "discuss", "prompt": "这是 AI Bridge 的连接验证。请仅回复：连接成功。不要执行工具或修改文件。",
            "session": "", "workdir": "", "goal": "验证连接", "images": [], "permissions": dict(access.PERMISSIONS, trigger=False),
            "bridge_url": "", "bridge_key": ""}
    headers = dict(spec.get("headers") or {}, **{"Content-Type": "application/json"})
    if spec.get("token"):
        headers["Authorization"] = "Bearer " + spec["token"]
    req = urllib.request.Request(spec["endpoint"], data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as response:
            raw = response.read(65537)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ValueError("服务拒绝认证，请检查服务密钥（HTTP %d）" % e.code) from e
        if 300 <= e.code < 400:
            raise ValueError("服务发生重定向，请填写最终的回调地址") from e
        raise ValueError("服务返回 HTTP %d，请检查回调接口" % e.code) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ValueError("无法连接服务或等待超时，请确认地址、端口和服务是否启动") from e
    if len(raw) > 65536:
        raise ValueError("回复过大，验证接口应返回简短 JSON")
    try:
        reply = json.loads(raw)
    except (ValueError, UnicodeError) as e:
        raise ValueError('服务能访问，但回复不符合协议；应返回 {"text":"连接成功"}') from e
    if not isinstance(reply, dict) or not isinstance(reply.get("text") or reply.get("message"), str) or not (reply.get("text") or reply.get("message")).strip():
        raise ValueError('回复缺少非空 text 字段；请检查 Harness 回调协议')
    return "已收到符合协议的回复。服务密钥可用；这次未授予任何工具或文件权限。"


def _local_check(spec):
    cmd = registry.exe_of(spec)
    if not cmd:
        raise ValueError("找不到程序。请先安装并登录该 Agent，再重新检测；也可选择自定义命令。")
    try:
        result = subprocess.run(cmd + ["--version"], capture_output=True, timeout=8, **NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        raise ValueError("程序启动超时，请在终端确认该命令能正常运行") from e
    except OSError as e:
        raise ValueError("程序无法启动，请检查安装路径") from e
    if result.returncode:
        raise ValueError("程序未通过 --version 验证，请在终端检查安装；自定义程序需支持 --version")
    return "程序可以正常启动。不需要接入密钥；登录与模型额度会在首次会话回复时验证。"


def check(pid):
    registry.load()
    spec = registry.get(pid)
    if not spec:
        raise ValueError("联系人不存在")
    if spec["driver"] == "external":
        return status(pid)  # cannot fabricate a remote connection from an owner request
    ok = False
    try:
        if spec["driver"] == "http":
            detail = _http_check(spec)
        elif spec["driver"] == "acp":
            # ACP 不能只看 --version：要真握手 + 开一个会话，才算「能连」
            from . import acp
            detail = acp.probe(spec)
        else:
            detail = _local_check(spec)
        ok = True
    except ValueError as e:
        detail = str(e)
    with LOCK:
        data = read_json(_path()) or {}
        data[pid] = {"signature": _signature(spec), "checked_at": time.time(), "ok": ok, "detail": detail}
        os.makedirs(LOG_DIR, exist_ok=True)
        write_json(_path(), data)
    return status(pid)
