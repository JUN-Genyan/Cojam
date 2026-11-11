# -*- coding: utf-8 -*-
r"""通用驱动：把"叫一个 AI"这件事从具体厂商里拆出来。

agents.call_ai 只管内置的两个（claude / codex，各有专门的一套）。这里管另外两种，都靠注册表里的配置驱动：

    driver = "cli"   起一个命令行程序，把这一轮的话交给它，读它的输出
    driver = "http"  把这一轮 POST 给自己的服务，拿它的回复（自己的 harness 接会议室走这条）

两种都返回跟 agents.call_ai 一样的东西：(ok, text, session_id, edits)。

命令行的拼法（顺序固定，配置里怎么写就怎么排）：
    bin + args.base + (干活 ? args.work : args.read) + (有会话 ? args.resume : args.new) + 每个图片的 images
占位符：{prompt} {session} {model} {effort} {cwd} {room} {room_title} {goal} {bridge_url} {bridge_key} {path}
args 里出现了 {prompt} 就走参数；没出现时看 prompt 字段："stdin" 写进标准输入，"arg" 接在最后。
"""
import json, os, re, urllib.error, urllib.request

from . import agents, registry
from .agents import bridge_url
from .common import run_stream, timeout_for

PH = re.compile(r"\{([a-z_]+)\}")


def _fmt(s, ctx):
    """把 {xxx} 换成值；不认识的占位符原样留着（免得用户写错就崩）。"""
    return PH.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), s)


def _get_path(obj, path):
    """{"a": {"b": 1}} 取 "a.b"；取不到返回 None。"""
    cur = obj
    for k in [x for x in str(path or "").split(".") if x]:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and k.isdigit() and int(k) < len(cur):
            cur = cur[int(k)]
        else:
            return None
    return cur


def _ctx(pid, spec, room, mode, session_id):
    key = room.keys.get(pid, "") if room is not None else ""
    wd = room.workdir() if room is not None else os.getcwd()
    return {"cwd": wd, "session": session_id or "",
            "room": room.id if room is not None else "", "room_title": room.title() if room is not None else "",
            "goal": room.goal() if room is not None else "", "sid": session_id or "",
            "bridge_url": bridge_url("/mcp"), "bridge_key": key, "speaker": pid, "mode": mode,
            "title": room.title() if room is not None else ""}


# ===================== 命令行 =====================
def _compose(spec, base_ctx, session_id, images, mode, prompt):
    a = spec["args"]
    seq = list(a["base"])
    seq += list(a["work"] if mode == "work" else a["read"])
    if base_ctx.get("model"):
        seq += list(a.get("model") or [])
    if base_ctx.get("effort"):
        seq += list(a.get("effort") or [])
    seq += list(a["resume"] if session_id else a["new"])
    out = []
    if images and spec["images"]:
        ictx = dict(base_ctx)
        ictx["path"] = ""
        for p in images:
            ictx["path"] = p
            out += [_fmt(x, ictx) for x in spec["images"]]
    out = [_fmt(x, dict(base_ctx, prompt=prompt)) for x in seq] + out
    has_prompt = any("{prompt}" in x for x in seq)
    if not has_prompt and spec["prompt"] == "arg":
        out.append(prompt)
    return out, has_prompt


def _parse_ndjson(p, stdout):
    """一行一个 JSON（很多 CLI 的 --format json 是这样）：文本拼起来，会话 id 取第一个非空的。"""
    parts, sess = [], None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if not sess and p["session"]:
            v = _get_path(obj, p["session"])
            if isinstance(v, (str, int)) and str(v):
                sess = str(v)
        if p["filter"] and any(_get_path(obj, k) != v for k, v in p["filter"].items()):
            continue
        if p["text"]:
            v = _get_path(obj, p["text"])
            if isinstance(v, str) and v:
                parts.append(v)
    return (p["join"].join(parts) if parts else ""), sess


def _parse(spec, stdout):
    """按配置从输出里取回复文本和会话 id。返回 (text, session)。"""
    p = spec["parse"]
    text, sess = "", None
    if p["type"] == "ndjson":
        text, sess = _parse_ndjson(p, stdout)
    elif p["type"] == "json":
        data = None
        try:
            data = json.loads(stdout)
        except Exception:
            for line in reversed(stdout.splitlines()):   # 有的 CLI 打一行 JSON，前后还夹着别的输出
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    data = json.loads(line)
                    break
                except Exception:
                    continue
        if isinstance(data, dict) or isinstance(data, list):
            if p["text"]:
                got = _get_path(data, p["text"])
                text = got if isinstance(got, str) else ("" if got is None else json.dumps(got, ensure_ascii=False))
            else:
                text = stdout
            if p["session"]:
                got = _get_path(data, p["session"])
                sess = str(got) if isinstance(got, (str, int)) else None
        else:
            text = stdout
    else:
        text = stdout
    if not sess and p["session_regex"]:
        m = re.search(p["session_regex"], stdout, re.M)
        if m:
            sess = m.group(1) if m.groups() else m.group(0)
    return (text or "").strip(), sess


def call_cli(pid, spec, prompt, session_id, images, mode, room):
    from . import room as _room  # 只为了拿房间类型判断，避免顶层循环 import
    exe = registry.exe_of(spec)
    if not exe:
        return False, "找不到 %s 命令（要装：%s）" % (pid, "、".join(spec["bin"])), session_id, []
    ctx = _ctx(pid, spec, room, mode, session_id)
    extra = room.effective_models(pid) if room is not None else tuple(spec["default"])
    ctx["model"], ctx["effort"] = (extra[0] or ""), (extra[1] or "")
    args, has_prompt = _compose(spec, ctx, session_id, images, mode, prompt)
    cmd = list(exe) + args
    stdin_text = "" if has_prompt else (prompt if spec["prompt"] == "stdin" else "")
    env = dict(spec.get("env") or {})
    buf = []

    def on_line(line):
        buf.append(line)
        if room is not None:
            room.push_live(pid, {"live": line})   # 中途说的话给页面状态行看

    rc, so, se, to = run_stream(cmd, stdin_text, on_line, timeout_for(mode, pid), cwd=ctx["cwd"], env=env,
                                reg=(lambda p: room.procs.__setitem__(pid, p)) if room is not None else None)
    stdout = "\n".join(buf) if buf else (so or "")
    if to:
        from .common import timeout_text
        return False, timeout_text(pid, to, mode), session_id, []
    text, sess = _parse(spec, stdout)
    if rc != 0 and not text:
        tail = "\n".join((se or "").strip().splitlines()[-6:])
        return False, "%s 出错了（退出码 %s）\n%s" % (pid, rc, tail), session_id, []
    if not text:
        return False, "%s 没有返回内容" % pid, session_id, []
    return True, text, (sess or session_id), []


# ===================== HTTP =====================
def call_http(pid, spec, prompt, session_id, images, mode, room):
    """把这一轮 POST 给用户自己的服务。请求 / 回复的格式见 docs/PROVIDERS.md。

    请求：{"room","room_title","speaker","mode","prompt","session","workdir","goal","images","bridge_url","bridge_key"}
    回复：{"text": "...", "session": "可选的新会话 id"}
    """
    idle, total = timeout_for(mode, pid)
    body = {"room": room.id if room is not None else "", "room_title": room.title() if room is not None else "",
            "speaker": pid, "mode": mode, "prompt": prompt, "session": session_id or "",
            "workdir": room.workdir() if room is not None else os.getcwd(),
            "goal": room.goal() if room is not None else "", "images": list(images or ()),
            "bridge_url": bridge_url("/mcp"), "bridge_key": (room.keys.get(pid, "") if room is not None else "")}
    if room is not None:
        from .access import permissions
        body["permissions"] = permissions(room, pid)
    model, effort = room.effective_models(pid) if room is not None else tuple(spec["default"])
    body["model"], body["effort"] = model, effort
    headers = {"Content-Type": "application/json"}
    headers.update(spec.get("headers") or {})
    if spec.get("token"):
        headers["Authorization"] = "Bearer " + spec["token"]
    req = urllib.request.Request(spec["endpoint"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=total) as r:
            raw = r.read().decode("utf-8", "replace")
            ctype = (r.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as e:
        tail = e.read().decode("utf-8", "replace")[:400] if hasattr(e, "read") else ""
        return False, "%s 的服务返回 %s\n%s" % (pid, e.code, tail), session_id, []
    except Exception as e:
        return False, "连不上 %s 的服务（%s）：%s" % (pid, spec["endpoint"], e), session_id, []
    text, sess = raw.strip(), None
    if "json" in ctype or raw.lstrip().startswith("{"):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                text = str(data.get("text") or data.get("message") or "").strip()
                sess = str(data["session"]) if data.get("session") else None
        except Exception:
            pass
    if not text:
        return False, "%s 没有返回内容" % pid, session_id, []
    return True, text, (sess or session_id), []


# ===================== 分派 =====================
def standby_worker(pid):
    """这个联系人现在是不是有桌面端在 MCP 待命。

    有的话优先派给它：桌面软件里的 agent 自己会执行并回复，比再起一个 CLI 更贴近用户看到的东西。
    待命端掉线就自然回落到本地 CLI，不需要用户改配置。
    """
    try:
        from .members import broker
        st = broker.status(pid)
    except Exception:
        return None
    return st if (st.get("automatic") and st.get("transport") == "standby") else None


def call(pid, prompt, session_id=None, images=(), mode=None, room=None):
    """叫一个 AI。跟 agents.call_ai 同签名，room.ai_caller 指的就是这里。"""
    spec = registry.get(pid)
    if not spec:                       # 没在注册表里（理论上不会）：退回内置处理
        return agents.call_ai(pid, prompt, session_id, images, mode, room)
    driver = spec["driver"]
    if mode is None:
        mode = room.mode() if room is not None else "discuss"
    if room is not None and standby_worker(pid):
        from .members import broker
        return broker.call(pid, prompt, session_id, mode, room)
    if driver in ("claude", "codex"):
        return agents.call_ai(pid, prompt, session_id, images, mode, room)
    try:
        if driver == "acp":
            from . import acp
            return acp.call(pid, spec, prompt, session_id, images, mode, room)
        if driver == "external":
            from .members import broker
            if room is None:
                return False, "请先加入会话并连接成员执行端", session_id, []
            return broker.call(pid, prompt, session_id, mode, room)
        if driver == "cli":
            return call_cli(pid, spec, prompt, session_id, images, mode, room)
        if driver == "http":
            return call_http(pid, spec, prompt, session_id, images, mode, room)
    except Exception as e:
        return False, "%s 调用失败：%s" % (pid, e), session_id, []
    return False, "%s 的 driver %r 不认识" % (pid, driver), session_id, []
