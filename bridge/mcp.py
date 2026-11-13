# -*- coding: utf-8 -*-
"""会议室自己的 MCP 服务（Streamable HTTP，只回 JSON，不开 SSE），地址 http://127.0.0.1:<端口>/mcp。

两种来客：
- 会议里的 AI：会议室起 claude / codex 时把这场会议给它的钥匙放进请求头（Authorization: Bearer <钥匙>）。
  它能看目标 / 看板 / 谁占着哪些文件、中途发进度和文件、改看板、占文件 / 放开文件。
- 外部用户 / Harness：带全局联系人接入密钥，仅能列出、读取和参与受邀的会话。
  跟 /api/post 一样（消息带 origin=external），按会话检查权限。"""
import json, os, time

from . import access, connections, members, registry, room as R
from .common import AIS, EXTERNAL_SPEAKERS, MAX_ATTACH, mode_text
from .guard import block_text, guard

VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
from .version import VERSION
SERVER_INFO = {"name": "ai-bridge", "title": "AI Bridge 会议室", "version": VERSION}
INSTRUCTIONS = "AI Bridge 会议室的工具：看会议目标、看板、谁占着哪些文件；中途往会议里发进度和文件；占文件防止两个 AI 同时改。"


def _obj(props, req=()):
    return {"type": "object", "properties": props, "required": list(req), "additionalProperties": False}


ROOM_TOOLS = [
    {"name": "room_info", "title": "这场会议的情况",
     "description": "看这场会议的目标、模式、看板、谁在跑，以及同一个文件夹里谁占着哪些文件。",
     "inputSchema": _obj({}), "annotations": {"readOnlyHint": True}},
    {"name": "post", "title": "中途发一句 / 发文件",
     "description": "在一轮还没说完的时候往会议里发一句进度，或者把本机文件（图片等）发给用户看。不会叫醒别人（里面的 @ 不叫人）。"
                    "最终的回答照常直接回复，不用这个。",
     "inputSchema": _obj({"text": {"type": "string", "description": "要说的话（可空，只发文件时）"},
                          "files": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_ATTACH,
                                    "description": "本机文件路径（完整路径或相对工作文件夹）"}})},
    {"name": "board", "title": "改看板",
     "description": "看板 = 这场会议的公告和待办。op=add 加一条（kind=todo 待办 / note 公告，text 内容）；op=done / undone 勾掉 / 取消勾选（ids）；op=delete 删（ids）。",
     "inputSchema": _obj({"op": {"type": "string", "enum": ["add", "done", "undone", "delete"]},
                          "kind": {"type": "string", "enum": ["todo", "note"]},
                          "text": {"type": "string"},
                          "ids": {"type": "array", "items": {"type": "integer"}}}, ["op"])},
    {"name": "claim_files", "title": "占文件",
     "description": "先占住接下来要连着改的一批文件或文件夹（文件夹会占住里面所有文件），别的 AI（这场会议的另一个、别的会议里的）就改不了。"
                    "用编辑工具改文件时会自动占一轮，只有要跨几轮连着改时才需要明着占。写清原因和大概要多久（分钟，默认 30，最多 240）。",
     "inputSchema": _obj({"paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                          "minutes": {"type": "integer", "minimum": 1, "maximum": 240},
                          "reason": {"type": "string"}}, ["paths"])},
    {"name": "release_files", "title": "放开文件",
     "description": "放开自己占的文件。paths 不填 = 放开自己占的全部。",
     "inputSchema": _obj({"paths": {"type": "array", "items": {"type": "string"}}})},
    {"name": "list_claims", "title": "谁占着哪些文件",
     "description": "列出现在被占着的文件（默认只列这场会议工作文件夹里的；all=true 列全部会议的）。",
     "inputSchema": _obj({"all": {"type": "boolean"}}), "annotations": {"readOnlyHint": True}},
]

EXT_TOOLS = [
    {"name": "list_rooms", "title": "列出会议",
     "description": "列出 AI Bridge 里的会议：会议号、标题、工作文件夹、谁在跑、消息数。",
     "inputSchema": _obj({"include_archived": {"type": "boolean"}}), "annotations": {"readOnlyHint": True}},
    {"name": "read_room", "title": "读会议记录",
     "description": "读受邀会话的目标、看板和最近消息。room 必填，可先 list_rooms 获取。",
     "inputSchema": _obj({"room": {"type": "string"}, "since": {"type": "integer", "minimum": 0},
                          "limit": {"type": "integer", "minimum": 1, "maximum": 200}}), "annotations": {"readOnlyHint": True}},
    {"name": "post_to_room", "title": "往会议里发言",
     "description": "以接入密钥绑定的联系人身份，在已受邀的会话中发言。服务端检查会话内的发言和唤起权限。",
     "inputSchema": _obj({"room": {"type": "string"}, "speaker": {"type": "string", "description": "可省略；提供时必须与接入密钥身份相同"},
                          "text": {"type": "string"}}, ["room", "text"])},
    {"name": "wait_for_message", "title": "等新消息",
     "description": "长轮询：一直等到这场会议有新消息、或者超时（最多 55 秒）。用来等 @自己 的话，然后 read_room 看内容再 post_to_room 回。"
                    "返回新消息和下次用的 since；超时返回空。",
     "inputSchema": _obj({"room": {"type": "string"}, "since": {"type": "integer", "minimum": 0},
                          "timeout": {"type": "integer", "minimum": 1, "maximum": 55}},
                         ["since"]), "annotations": {"readOnlyHint": True}},
]


EXT_TOOLS += [
    {"name": "cojam_standby", "title": "进入合拍待命模式（自动接单与心跳）",
     "description": "给桌面 IDE / 客户端里的 Agent 使用：调用即自动注册、续心跳并长轮询等待 Cojam 派发任务。无需手填 worker ID。收到 status='task' 时执行任务并调用 reply_task 提交回复，随后再次调用 cojam_standby；收到 status='idle' 时立即再次调用 cojam_standby；收到 status='stopped' 或 'expired' 时结束循环。",
     "inputSchema": _obj({"workdir": {"type": "string", "description": "当前工作目录（可选）"},
                          "allow_work": {"type": "boolean", "description": "是否允许在干活模式下修改文件（默认 false：只读参与；需要改文件时显式传 true）"},
                          "timeout": {"type": "integer", "minimum": 0, "maximum": 55, "description": "单次等待秒数（可选，默认按 Cojam 设置）"}}, [])},
    {"name": "receive_task", "title": "接收本成员的任务", "description": "启用自动响应并领取任务。只返回用户已邀请本成员的会话任务。保持稳定的 worker ID；按任务中 member 的身份回复，不冒充本机的其他 AI。",
     "inputSchema": _obj({"worker": {"type": "string"}, "device": {"type": "string"}, "workdir": {"type": "string"},
                          "allow_work": {"type": "boolean"}, "timeout": {"type": "integer", "minimum": 0, "maximum": 55}}, ["worker"])},
    {"name": "reply_task", "title": "回复成员任务", "description": "提交任务结果，会议室自动记录并处理回复中的 @。必须使用 receive_task 或 cojam_standby 返回的 worker、task id 和 lease。重复提交同一结果不会重复发言。",
     "inputSchema": _obj({"worker": {"type": "string"}, "task": {"type": "string"}, "lease": {"type": "string"},
                          "ok": {"type": "boolean"}, "text": {"type": "string"}, "session": {"type": "string"}}, ["worker", "task", "lease", "ok", "text"])},
    {"name": "member_heartbeat", "title": "成员心跳与取消检查", "description": "按 Cojam 心跳间隔调用，保持自动响应连接并检查当前任务是否已取消。任务取消后停止工作，不再提交回复。",
     "inputSchema": _obj({"worker": {"type": "string"}, "task": {"type": "string"}, "lease": {"type": "string"}}, ["worker"])},
    {"name": "task_progress", "title": "汇报成员任务进度", "description": "当前任务中途汇报进度并续心跳，不触发其他成员。",
     "inputSchema": _obj({"worker": {"type": "string"}, "task": {"type": "string"}, "lease": {"type": "string"}, "text": {"type": "string"}}, ["worker", "task", "lease", "text"])}
]

# Scoped desktop sessions use an endpoint credential in addition to the contact key.
ENDPOINT_FIELDS = {"room": {"type": "string"}, "worker": {"type": "string"},
                   "binding_token": {"type": "string"}}
for _tool in EXT_TOOLS:
    if _tool["name"] in ("list_rooms", "read_room", "post_to_room", "wait_for_message", "member_heartbeat", "cojam_standby"):
        _tool["inputSchema"]["properties"].update(ENDPOINT_FIELDS)
    if _tool["name"] == "cojam_standby":
        _tool["inputSchema"]["properties"].update(native_session={"type": "string"}, binding_challenge={"type": "string"})
        _tool["description"] += " 桌面接管需从指定原生会话传入 room/native_session/binding_challenge；后续调用携带返回的 worker/binding_token 以及 room/native_session。status=release 时调用 release_session 并结束循环。"
EXT_TOOLS.append({"name": "release_session", "title": "释放共享原生会话给 CLI",
                  "description": "会议室请求切换 CLI 后停止待命并释放会话，成功后不要再读取或执行。",
                  "inputSchema": _obj(ENDPOINT_FIELDS, ["room", "worker", "binding_token"])})

STANDBY_PROMPTS = [
    {
        "name": "cojam_standby",
        "title": "开启合拍 Cojam 待命模式",
        "description": "让当前 Agent 持续接听 Cojam 会议室里的 @ 和任务，收到任务后自动执行并回复到会议室。",
        "arguments": [],
    }
]


class ToolError(Exception):
    pass


def _text(s, err=False):
    return {"content": [{"type": "text", "text": s}], "isError": bool(err)}


def _room_for(rid):
    rid = rid or R.last_room()
    r = R.rooms.get(rid) if rid else None
    if r is None:
        raise ToolError("没有这场会议：%s" % (rid or "（还没打开过会议）"))
    return r


def _claims_text(items):
    if not items:
        return "（现在没人占着文件）"
    out = []
    for c in items:
        how = "这一轮自动占着" if c["kind"] == "auto" else "明着占着，还有约 %s 分钟%s" % (c["mins_left"], "，原因：" + c["reason"] if c["reason"] else "")
        out.append("- %s%s：%s（%s）" % (c["shown"], "（整个文件夹）" if c["dir"] else "", c["who"], how))
    return "\n".join(out)


def call_room_tool(rid, ai, name, args):
    r = R.rooms.get(rid)
    if r is None:
        raise ToolError("这场会议已经不在了")
    from . import execution
    owner = execution.binding(r, ai)
    if owner["endpoint"] == "desktop" or owner["phase"] != "ready":
        raise ToolError("此原生会话已交给桌面或正在接管，原 CLI 工具凭据不可继续读取和执行")
    try:
        access.require(r, ai)
        needed = {"post": "post", "board": "board", "claim_files": "work", "release_files": "work"}.get(name)
        if needed:
            access.require(r, ai, needed)
        if name == "post" and args.get("files"):
            access.require(r, ai, "files")
    except PermissionError as e:
        raise ToolError(str(e))
    who = (r.id, ai)
    if (registry.get(ai) or {}).get("driver") == "external" and name in ("claim_files", "release_files"):
        raise ToolError("成员的文件护栏由执行设备管理，不能占用会议室宿主文件")
    if (registry.get(ai) or {}).get("driver") == "external" and name == "post" and args.get("files"):
        raise ToolError("跨设备附件尚未启用，不能读取会议室宿主的本机文件")
    if name == "room_info":
        st = r.api_state()
        lines = ["会议：%s（%s）" % (r.title(), "干活模式" if r.mode() == "work" else "讨论模式"),
                 "工作文件夹：%s" % r.workdir(), "目标：\n" + r.goal_text(), "看板：\n" + r.board_text(),
                 "在跑：%s" % ("、".join(st["running"]) or "没有"),
                 "这个文件夹里被占着的文件：\n" + _claims_text(st["claims"])]
        return "\n\n".join(lines)
    if name == "post":
        atts, errs = [], []
        for p in (args.get("files") or [])[:MAX_ATTACH]:
            try:
                atts.append(r.send_file(str(p)))
            except Exception as e:
                errs.append(str(e))
        m = r.post_progress(ai, str(args.get("text") or ""), atts)
        if m is None and not errs:
            raise ToolError("没写内容也没给文件")
        return ("发出去了" + ("（%d 个文件）" % len(atts) if atts else "")) + ("；没发成：" + "；".join(errs) if errs else "")
    if name == "board":
        op = str(args.get("op") or "")
        ids = [int(x) for x in args.get("ids") or [] if isinstance(x, int) or str(x).isdigit()]
        if op == "add":
            res = r.board_op(ai, "add", str(args.get("kind") or "todo"), str(args.get("text") or ""))
        elif op in ("done", "undone", "delete"):
            res = r.board_op(ai, "del" if op == "delete" else op, ids=ids)
        else:
            raise ToolError("op 只能是 add / done / undone / delete")
        if not res:
            raise ToolError("没改成（检查编号、内容；看板最多 100 条）")
        r.board_note(ai, [res])   # 两个 AI 都能看到（它自己下一轮也看到，确认改上了）
        return res
    if name == "claim_files":
        paths = [r.send_path(str(p)) for p in args.get("paths") or [] if str(p).strip()]
        if not paths:
            raise ToolError("paths 不能为空")
        if r.member_mode(ai) != "work":
            raise ToolError("讨论模式下不能改文件，用不着占")
        res = guard.claim(who, paths, args.get("minutes"), str(args.get("reason") or ""), r.title())
        ok = [p for p, good, _ in res if good]
        bad = [(p, hit) for p, good, hit in res if not good]
        out = []
        if ok:
            out.append("占住了：\n" + "\n".join("- " + p for p in ok))
            r.system_note("%s 占住了 %d 个路径：%s" % (ai, len(ok), "、".join(os.path.basename(p.rstrip("\\/")) or p for p in ok[:6])))
        for p, hit in bad:
            out.append(block_text(p, hit))
        return "\n".join(out)
    if name == "release_files":
        paths = [r.send_path(str(p)) for p in args.get("paths") or [] if str(p).strip()]
        n = guard.release(who, paths or None)
        return "放开了 %d 个" % n
    if name == "list_claims":
        items = guard.listing()
        if not args.get("all"):
            from .common import in_dir
            items = [c for c in items if c["room"] == r.id or in_dir(c["shown"], r.workdir())]
        return _claims_text(items)
    raise ToolError("没有这个工具：%s" % name)


def call_ext_tool(name, args, pid=None, key=""):
    if not pid:
        raise ToolError("请使用联系人接入密钥")
    actions = {"receive_task": "receive", "reply_task": "reply", "member_heartbeat": "heartbeat",
               "task_progress": "progress", "cojam_standby": "standby", "release_session": "release"}
    if name in actions:
        try:
            if name == "receive_task":
                members.handle(pid, key, "connect", args)
            return json.dumps(members.handle(pid, key, actions[name], args), ensure_ascii=False)
        except members.MemberError as e:
            raise ToolError(str(e))
    if name != "list_rooms":
        r = _room_for(args.get("room"))
        if not args.get("room"):
            raise ToolError("room 必填，请先 list_rooms")
        if not access.external_allowed(r, pid, str(args.get("worker") or ""), str(args.get("binding_token") or "")):
            raise ToolError("未加入此会话或没有读取权限")
    if name == "list_rooms":
        out = []
        for x in access.visible_rooms(pid, str(args.get("worker") or ""), str(args.get("binding_token") or "")):
            if x["archived"] and not args.get("include_archived"):
                continue
            out.append("- %s：%s（%s%s，%d 条消息%s）" % (x["id"], x["title"], x["workdir"], "，已归档" if x["archived"] else "",
                                                     x["count"], "，在跑：" + "、".join(x["running"]) if x["running"] else ""))
        return "\n".join(out) or "（还没有会议）"
    if name == "read_room":
        r = _room_for(args.get("room"))
        try:
            limit = max(1, min(200, int(args.get("limit") or 30)))
            since = max(0, int(args.get("since") or 0))
        except (TypeError, ValueError):
            raise ToolError("since / limit 要是整数")
        with r.lock:
            msgs = r.messages[since:]
        msgs = [m for m in msgs if not m.get("quiet")][-limit:]
        body = "\n\n".join("#%d %s %s" % (m["id"], m["time"], R.fmt_message(m)) for m in msgs) or "（没有消息）"
        return "会议：%s（%s）\n目标：\n%s\n\n看板：\n%s\n\n消息（共 %d 条，下次从 since=%d 读起）：\n%s" % (
            r.title(), r.id, r.goal_text(), r.board_text(), len(r.messages), len(r.messages), body)
    if name == "post_to_room":
        sp = pid
        if args.get("speaker") and args["speaker"] != pid:
            raise ToolError("密钥不能冒用其他联系人")
        if not access.allowed(r, pid, "post"):
            raise ToolError("没有发言权限")
        if not sp:
            raise ToolError("speaker 必填（你以谁的身份发言）")
        text = str(args.get("text") or "").strip()
        if not text:
            raise ToolError("text 不能为空")
        r = _room_for(args.get("room"))
        r.handle_message(sp, text, origin="external")
        return "发到「%s」了（以 %s 的身份）" % (r.title(), sp)
    if name == "wait_for_message":
        r = _room_for(args.get("room"))
        try:
            since = max(0, int(args.get("since") or 0))
            wait = max(1, min(55, int(args.get("timeout") or 30)))
        except (TypeError, ValueError):
            raise ToolError("since / timeout 要是整数")
        end = time.time() + wait
        while True:
            if not access.external_allowed(r, pid, str(args.get("worker") or ""), str(args.get("binding_token") or "")):
                raise ToolError("会话权限已撤销")
            with r.lock:
                msgs = [m for m in r.messages[since:] if not m.get("quiet")]
            if msgs or time.time() >= end:
                break
            time.sleep(0.5)
        if not msgs:
            return "（%d 秒内没有新消息，下次还是从 since=%d 读）" % (wait, since)
        body = "\n\n".join("#%d %s %s" % (m["id"], m["time"], R.fmt_message(m)) for m in msgs)
        return "新消息 %d 条（下次从 since=%d 读）：\n%s" % (len(msgs), len(r.messages), body)
    raise ToolError("没有这个工具：%s" % name)


def _result(rid, res):
    return {"jsonrpc": "2.0", "id": rid, "result": res}


def _error(rid, code, msg):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}


def handle_one(msg, who, key=""):
    """处理一条 JSON-RPC。who = (会议号, ai) 或 None（外面来的）。返回回应（通知 / 回应类的返回 None）。"""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid request")
    if "method" not in msg:
        return None   # 客户端回给我们的（我们不发请求），忽略
    mid, meth, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None   # 通知：notifications/initialized 之类
    if meth == "initialize":
        want = str(params.get("protocolVersion") or "")
        return _result(mid, {"protocolVersion": want if want in VERSIONS else VERSIONS[0],
                             "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
                             "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
    if meth == "ping":
        return _result(mid, {})
    if meth == "tools/list":
        return _result(mid, {"tools": ROOM_TOOLS if isinstance(who, tuple) else EXT_TOOLS})
    if meth in ("resources/list", "resources/templates/list"):
        return _result(mid, {"resources": []} if meth == "resources/list" else {"resourceTemplates": []})
    if meth == "prompts/list":
        return _result(mid, {"prompts": [] if isinstance(who, tuple) else STANDBY_PROMPTS})
    if meth == "prompts/get":
        pname = str(params.get("name") or "")
        if pname != "cojam_standby":
            return _error(mid, -32602, "未知提示词：%s" % pname)
        return _result(mid, {
            "description": "开启合拍 Cojam 待命模式",
            "messages": [{
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        "请立即调用 ai-bridge 的 cojam_standby 工具进入合拍 Cojam 待命模式，并遵循以下循环：\n"
                        "1. 调用 cojam_standby。\n"
                        "2. 如果返回 status='task'，认真按 task.prompt 执行任务，完成后调用 reply_task（带上返回的 worker、task.id 和 task.lease，ok=true，text=你的回复），然后立即回到第 1 步再次调用 cojam_standby。\n"
                        "3. 如果返回 status='idle'，不要输出冗余解释，立即再次调用 cojam_standby。\n"
                        "4. 如果返回 status='release'，调用 release_session 释放会话并结束循环。\n"
                        "5. 如果返回 status='stopped' 或 'expired'，停止循环并告知我待命已结束。\n"
                        "桌面接管需使用会议室提供的参数，在指定原生会话调用；后续待命和读取一直携带 room、worker、binding_token。"
                    ),
                },
            }],
        })
    if meth == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(mid, -32602, "arguments 要是对象")
        try:
            if isinstance(who, tuple):
                text = call_room_tool(who[0], who[1], name, args)
            else:
                text = call_ext_tool(name, args, who, key)
            return _result(mid, _text(text))
        except ToolError as e:
            return _result(mid, _text(str(e), err=True))
        except Exception as e:
            return _result(mid, _text("会议室内部错误：%s" % e, err=True))
    return _error(mid, -32601, "不支持 %s" % meth)


def handle_http(body, key):
    """POST /mcp 的正文。返回 (状态码, 回应对象或 None)。"""
    try:
        data = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
    except Exception:
        return 400, _error(None, -32700, "parse error")
    who = R.who_by_key(key) or access.identify(key)
    if who is None:
        return 401, _error(None, -32001, "需要有效的联系人接入密钥")
    if isinstance(who, str) and isinstance(data, (dict, list)):
        first = data if isinstance(data, dict) else next((m for m in data if isinstance(m, dict)), {})
        params = first.get("params") or {}
        client = (params.get("clientInfo") or {}).get("name", "") if isinstance(params, dict) and isinstance(params.get("clientInfo") or {}, dict) else ""
        connections.seen(who, key, "mcp", client)
    if isinstance(data, list):   # 老版本允许批量
        out = [x for x in (handle_one(m, who, key) for m in data) if x is not None]
        return (200, out) if out else (202, None)
    resp = handle_one(data, who, key)
    return (200, resp) if resp is not None else (202, None)
