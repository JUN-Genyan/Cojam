# -*- coding: utf-8 -*-
"""ACP（Agent Client Protocol）直连驱动：stdio 上的 JSON-RPC 2.0。

由 Cojam 直接拉起支持 ACP 的本机 Agent（如 gemini --experimental-acp、opencode acp、qwen --experimental-acp、
goose acp、kimi acp 等），保持常驻会话、流式接收回复、按会议室讨论/干活模式控制工具权限，
并在写文件前走 Cojam 的文件护栏（guard）。
"""
import base64
import json
import mimetypes
import os
import queue
import subprocess
import threading
import time

from . import conn_settings, guard, registry
from .common import NO_WINDOW, PORT, kill_tree, resolve_cmd, timeout_for, timeout_text
from .version import VERSION


class AcpError(Exception):
    pass


class AcpProcess:
    def __init__(self, pid, cmd, cwd, env=None, init_timeout=20):
        self.pid = pid
        self.cmd = list(cmd)
        self.cwd = cwd
        self.init_timeout = init_timeout
        self.lock = threading.RLock()
        self.req_id = 0
        self.pending = {}
        self.notes = queue.Queue()
        self.last_used = time.time()
        self.session_id = None
        self.active_room = None
        self.active_mode = "discuss"
        self.active_edits = []

        full_env = dict(os.environ, PYTHONIOENCODING="utf-8")
        full_env.update(env or {})
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=full_env,
            **NO_WINDOW,
        )
        self._closed = False
        threading.Thread(target=self._read_stdout, daemon=True, name="acp-out-" + pid).start()
        threading.Thread(target=self._read_stderr, daemon=True, name="acp-err-" + pid).start()
        self._initialize()

    def alive(self):
        return not self._closed and self.proc.poll() is None

    def _send(self, obj):
        if not self.alive():
            raise AcpError("%s 的 ACP 进程已退出" % self.pid)
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        try:
            self.proc.stdin.write(line.encode("utf-8"))
            self.proc.stdin.flush()
        except OSError as e:
            raise AcpError("%s 的 ACP 管道写入失败：%s" % (self.pid, e)) from e

    def _read_stdout(self):
        try:
            for raw in self.proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
                    q = self.pending.get(msg["id"])
                    if q:
                        q.put(msg)
                elif "id" in msg and "method" in msg:
                    self._handle_client_request(msg)
                elif "method" in msg:
                    self.notes.put(msg)
        finally:
            self._closed = True
            for q in list(self.pending.values()):
                q.put({"error": {"message": "ACP 进程已退出"}})

    def _read_stderr(self):
        try:
            self.stderr_tail = self.proc.stderr.read().decode("utf-8", "replace")[-1200:]
        except Exception:
            self.stderr_tail = ""

    def _handle_client_request(self, msg):
        req_id = msg.get("id")
        method = str(msg.get("method") or "")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        try:
            if method == "session/request_permission":
                result = self._on_permission(params)
                self._send({"jsonrpc": "2.0", "id": req_id, "result": result})
                return
            if method == "fs/read_text_file":
                path = os.path.abspath(os.path.join(self.cwd, str(params.get("path") or "")))
                with open(path, encoding="utf-8", errors="replace") as f:
                    content = f.read()
                self._send({"jsonrpc": "2.0", "id": req_id, "result": {"content": content}})
                return
            if method == "fs/write_text_file":
                result = self._on_write_file(params)
                self._send({"jsonrpc": "2.0", "id": req_id, "result": result})
                return
            self._send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not supported: " + method}})
        except AcpError as e:
            self._send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}})
        except Exception as e:
            self._send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}})

    def _on_permission(self, params):
        options = params.get("options") if isinstance(params.get("options"), list) else []
        tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        kind = str(tool_call.get("kind") or params.get("kind") or "").lower()
        allow = self.active_mode == "work" or kind in ("read", "search", "think", "fetch")
        if allow:
            for opt in options:
                if isinstance(opt, dict) and str(opt.get("kind") or "").startswith("allow"):
                    return {"outcome": {"outcome": "selected", "optionId": opt.get("optionId") or opt.get("id")}}
            if options and isinstance(options[0], dict):
                return {"outcome": {"outcome": "selected", "optionId": options[0].get("optionId") or options[0].get("id")}}
            return {"outcome": {"outcome": "selected", "optionId": "allow"}}
        for opt in options:
            if isinstance(opt, dict) and str(opt.get("kind") or "").startswith("reject"):
                return {"outcome": {"outcome": "selected", "optionId": opt.get("optionId") or opt.get("id")}}
        return {"outcome": {"outcome": "cancelled"}}

    def _on_write_file(self, params):
        if self.active_mode != "work":
            raise AcpError("当前是讨论模式，禁止修改文件")
        raw_path = str(params.get("path") or "")
        if not raw_path:
            raise AcpError("缺少文件路径")
        full = os.path.abspath(os.path.join(self.cwd, raw_path))
        if self.active_room is not None:
            ok, msg, _ = guard.claim(self.active_room.id, self.pid, [full], self.cwd, "ACP 写文件")
            if not ok:
                raise AcpError(msg)
        os.makedirs(os.path.dirname(full) or self.cwd, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(str(params.get("content") or ""))
        if full not in self.active_edits:
            self.active_edits.append(full)
        return {}

    def request(self, method, params=None, timeout=30, on_note=None):
        self.req_id += 1
        rid = self.req_id
        q = queue.Queue()
        self.pending[rid] = q
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            end = time.time() + timeout
            while True:
                while not self.notes.empty():
                    try:
                        n = self.notes.get_nowait()
                        if on_note:
                            on_note(n)
                    except queue.Empty:
                        break
                rem = end - time.time()
                if rem <= 0:
                    raise TimeoutError("%s (%s) 超时" % (self.pid, method))
                try:
                    resp = q.get(timeout=min(0.25, rem))
                except queue.Empty:
                    if not self.alive():
                        raise AcpError("%s 的 ACP 进程意外退出" % self.pid)
                    continue
                while not self.notes.empty():
                    try:
                        n = self.notes.get_nowait()
                        if on_note:
                            on_note(n)
                    except queue.Empty:
                        break
                if "error" in resp and resp["error"]:
                    err = resp["error"]
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    raise AcpError(msg or "ACP 请求失败")
                return resp.get("result") or {}
        finally:
            self.pending.pop(rid, None)

    def _initialize(self):
        self.request("initialize", {
            "protocolVersion": 1,
            "clientInfo": {"name": "Cojam", "version": VERSION},
            "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}, "terminal": False},
        }, timeout=self.init_timeout)

    def ensure_session(self, wanted_sid, mcp_servers, timeout=25):
        if wanted_sid and wanted_sid == self.session_id:
            return self.session_id
        if wanted_sid:
            try:
                res = self.request("session/load", {"sessionId": wanted_sid, "cwd": self.cwd, "mcpServers": mcp_servers}, timeout=timeout)
                self.session_id = str(res.get("sessionId") or wanted_sid)
                return self.session_id
            except Exception:
                pass
        res = self.request("session/new", {"cwd": self.cwd, "mcpServers": mcp_servers}, timeout=timeout)
        self.session_id = str(res.get("sessionId") or wanted_sid or ("acp-" + str(int(time.time()))))
        return self.session_id

    def prompt(self, text, images, mode, room, wanted_sid=None):
        with self.lock:
            self.last_used = time.time()
            self.active_room = room
            self.active_mode = mode
            self.active_edits = []
            mcp_servers = []
            if room is not None:
                key = room.tool_key(self.pid) if hasattr(room, "tool_key") else ""
                mcp_servers.append({
                    "name": "bridge",
                    "type": "http",
                    "url": "http://127.0.0.1:%d/mcp" % PORT,
                    "headers": [{"name": "Authorization", "value": "Bearer " + key}] if key else [],
                })
            sid = self.ensure_session(wanted_sid, mcp_servers)
            mo_m, mo_e = ("", "")
            if room is not None:
                mo_m, mo_e = room.effective_models(self.pid)
                if mo_m:
                    try:
                        self.request("session/set_config_option", {"sessionId": sid, "configId": "model", "value": mo_m}, timeout=10)
                    except Exception:
                        pass
                if mo_e:
                    try:
                        self.request("session/set_config_option", {"sessionId": sid, "configId": "thought_level", "value": mo_e}, timeout=10)
                    except Exception:
                        pass
            blocks = [{"type": "text", "text": text}]
            for img in images or ():
                if os.path.isfile(img):
                    try:
                        mime = mimetypes.guess_type(img)[0] or "image/png"
                        with open(img, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("ascii")
                        blocks.append({"type": "image", "mimeType": mime, "data": b64})
                    except OSError:
                        pass

            chunks = []

            def on_note(msg):
                self.last_used = time.time()
                if msg.get("method") != "session/update":
                    return
                params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
                upd = params.get("update") if isinstance(params.get("update"), dict) else params
                kind = str(upd.get("sessionUpdate") or upd.get("type") or "")
                if kind == "agent_message_chunk":
                    content = upd.get("content") if isinstance(upd.get("content"), dict) else upd
                    piece = str(content.get("text") or "")
                    if piece:
                        chunks.append(piece)
                        if room is not None:
                            room.push_live(self.pid, {"live": "".join(chunks)[-300:]})
                elif kind in ("tool_call", "tool_call_update"):
                    title = str(upd.get("title") or upd.get("name") or "")
                    if title and room is not None:
                        room.push_live(self.pid, {"tool": title})

            idle, total = timeout_for(mode, self.pid)
            try:
                res = self.request("session/prompt", {"sessionId": sid, "prompt": blocks}, timeout=total, on_note=on_note)
            except Exception as e:
                err_str = str(e).lower()
                if "session not found" in err_str or "invalid params: session" in err_str:
                    self.session_id = None
                    sid = self.ensure_session(None, mcp_servers)
                    if mo_m:
                        try:
                            self.request("session/set_config_option", {"sessionId": sid, "configId": "model", "value": mo_m}, timeout=10)
                        except Exception:
                            pass
                    res = self.request("session/prompt", {"sessionId": sid, "prompt": blocks}, timeout=total, on_note=on_note)
                else:
                    raise
            if not chunks and isinstance(res.get("text"), str):
                chunks.append(res["text"])
            reply = "".join(chunks).strip()
            return reply, sid, list(self.active_edits)

    def cancel(self):
        if self.session_id and self.alive():
            try:
                self._send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.session_id}})
            except Exception:
                pass

    def close(self):
        self._closed = True
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=1.5)
        except Exception:
            if self.proc.poll() is None:
                kill_tree(self.proc)
        for s in (self.proc.stdout, self.proc.stderr):
            try:
                if s:
                    s.close()
            except Exception:
                pass


_pool_lock = threading.RLock()
_pool = {}   # (room_id, pid) -> AcpProcess


def _reap_idle():
    now = time.time()
    with _pool_lock:
        for key, proc in list(_pool.items()):
            limit = conn_settings.get("idle_reap", key[1]) * 60
            if not proc.alive() or (now - proc.last_used > limit and proc.active_room is None):
                _pool.pop(key, None)
                proc.close()


def _cmd_for(spec):
    exe = None
    for b in spec.get("bin") or []:
        got = resolve_cmd(b)
        if got:
            exe = got
            break
    if not exe:
        return None
    acp_args = list((spec.get("args") or {}).get("acp") or [])
    return exe + acp_args


def probe(spec, cwd=None, init_timeout=12, session_timeout=15):
    """真的握一次手：只跑 --version 通过不算能用，得能 initialize 并开一个会话。

    连不上/没登录/协议不兼容都会在这里暴露，而不是等第一次派任务才失败。
    """
    cmd = _cmd_for(spec)
    if not cmd:
        raise ValueError("找不到可执行程序（%s）" % " / ".join(spec.get("bin") or []))
    proc = None
    try:
        proc = AcpProcess(spec["id"], cmd, cwd or os.getcwd(), env=spec.get("env"), init_timeout=init_timeout)
        proc.ensure_session(None, [], timeout=session_timeout)
        return "ACP 握手成功，可以建立会话。登录与模型额度会在首次回复时验证。"
    except Exception as e:
        raise ValueError("ACP 握手失败：%s" % e) from e
    finally:
        if proc is not None:
            try:
                proc.close()
            except Exception:
                pass


def call(pid, spec, prompt, session_id=None, images=(), mode="discuss", room=None):
    _reap_idle()
    cmd = _cmd_for(spec)
    if not cmd:
        return False, "找不到 %s 的可执行程序（%s）" % (pid, " / ".join(spec.get("bin") or [])), session_id, []
    cwd = room.workdir() if room is not None else os.getcwd()
    key = (room.id if room is not None else "_", pid)
    with _pool_lock:
        proc = _pool.get(key)
        if proc and (not proc.alive() or os.path.normcase(proc.cwd) != os.path.normcase(cwd)):
            _pool.pop(key, None)
            proc.close()
            proc = None
        if proc is None:
            try:
                proc = AcpProcess(pid, cmd, cwd, env=spec.get("env"))
                _pool[key] = proc
            except Exception as e:
                return False, "%s 的 ACP 进程启动失败：%s" % (pid, e), session_id, []
    try:
        reply, sid, edits = proc.prompt(prompt, images, mode, room, wanted_sid=session_id)
    except TimeoutError:
        stop(room, pid)
        return False, timeout_text(pid, "total", mode), session_id, []
    except Exception as e:
        # 模型侧报错（额度不足之类）不代表进程坏了：进程和会话留着下轮接着用，别每次都重开进程再开新会话
        if proc.alive():
            proc.cancel()
        else:
            with _pool_lock:
                if _pool.get(key) is proc:
                    _pool.pop(key, None)
            proc.close()
        err_str = str(e).lower()
        if "session not found" in err_str or "invalid params: session" in err_str:
            proc.session_id = None
            session_id = None
        return False, "%s (ACP) 调用出错：%s" % (pid, e), proc.session_id or session_id, []
    finally:
        proc.active_room = None
    if not reply:
        return False, "%s 没有返回内容" % pid, sid or session_id, edits
    return True, reply, sid or session_id, edits


def stop(room=None, pid=None):
    rid = room.id if hasattr(room, "id") else (str(room) if room else None)
    with _pool_lock:
        for (r_id, a_id), proc in list(_pool.items()):
            if (rid is None or r_id == rid) and (pid is None or a_id == pid):
                proc.cancel()
                _pool.pop((r_id, a_id), None)
                proc.close()
