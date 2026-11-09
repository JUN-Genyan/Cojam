"""Portable member connector. Network credentials never go to the local model.

python3 -m bridge.member --server URL --agent codex --workdir /my/project
The key is read from AI_BRIDGE_TOKEN or a password prompt. GUI: member_ui.
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class ConnectionError(Exception):
    def __init__(self, message, code=0):
        super().__init__(message)
        self.code = code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def connection_problem(error):
    """Useful network diagnostics without echoing URLs, credentials or proxy output."""
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "HTTPS 证书验证失败，请检查服务地址和证书；不要关闭证书验证"
    if isinstance(reason, socket.gaierror):
        return "找不到会议室的域名，请检查地址或 DNS"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "连接会议室超时，请检查网络、防火墙及服务地址是否可达"
    if isinstance(reason, ConnectionRefusedError):
        return "会议室拒绝连接，请确认主机已启动且地址和端口正确"
    return "无法连接会议室，请检查网络和服务地址（127.0.0.1 只代表当前设备）"


class Client:
    def __init__(self, server, key, worker=None, device=None, workdir="", allow_work=False, emit=print):
        url = urllib.parse.urlsplit(server)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            raise ValueError("请填写会议室根地址，例如 https://bridge.example.com")
        if not key.strip():
            raise ValueError("请填写成员接入密钥")
        self.server, self.key = server.rstrip("/"), key.strip()
        self.worker, self.device = worker or uuid.uuid4().hex, device or socket.gethostname()
        self.workdir, self.allow_work, self.emit = workdir, allow_work, emit
        self.stop_event = threading.Event()
        self.active = None

    def request(self, action, body=None, timeout=30):
        body = dict(body or {}, worker=self.worker)
        req = urllib.request.Request(self.server + "/api/external/member/" + action,
                                     data=json.dumps(body).encode(), headers={"Content-Type":"application/json", "Authorization":"Bearer " + self.key})
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ConnectionError("会议室返回的任务过大")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except urllib.error.HTTPError as e:
            # Do not print a proxy's response, request headers or credentials.
            message = {401:"密钥已失效，请重新配对", 403:"此成员没有访问权限", 409:"连接或任务状态冲突，请检查是否已有同一成员的执行端", 410:"任务已取消或会议室已重启"}.get(e.code, "会议室请求失败（HTTP %s）" % e.code)
            raise ConnectionError(message, e.code) from e
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise ConnectionError(connection_problem(e)) from e

    def task_request(self, action, task, timeout=10, **body):
        return self.request(action, dict(body, task=task["id"], lease=task["lease"]), timeout=timeout)

    def stop(self):
        self.stop_event.set()
        def disconnect():
            try:
                self.request("disconnect", timeout=5)
            except ConnectionError:
                pass
        threading.Thread(target=disconnect, daemon=True).start()

    def run(self, executor):
        connected = False
        try:
            while not self.stop_event.is_set():
                try:
                    if not connected:
                        capability = executor.catalog() if hasattr(executor, 'catalog') else None
                        info = self.request("connect", {"device":self.device, "workdir":self.workdir, "allow_work":self.allow_work, 'capabilities':capability})
                        self.member = info["member"]
                        connected = True
                        self.emit("已加入会议室服务，等待分配给 %s 的任务。" % self.member)
                    result = self.request("receive", {"timeout":5}, timeout=10)
                    if hasattr(executor, 'catalog'):
                        capability = executor.catalog(refresh=result.get('refresh_catalog') is True)
                        self.request('heartbeat', {'capabilities':capability}, timeout=10)
                    task = result.get("task")
                    if task:
                        self._execute(task, executor)
                except ConnectionError as e:
                    self.emit(str(e))
                    if e.code in (401, 409) or (e.code == 403 and not connected):
                        break
                    connected = False
                    self.stop_event.wait(3)
        finally:
            self.active = None
            if connected:
                try:
                    self.request("disconnect", timeout=5)
                except ConnectionError:
                    pass
            executor.close()
            self.emit("成员连接已停止。")

    def _execute(self, task, executor):
        if not isinstance(task, dict) or not all(isinstance(task.get(k), str) and task[k] for k in ("id", "lease", "room", "prompt", "member")):
            raise ConnectionError("会议室任务格式无效")
        if task["member"] != self.member or task.get("protocol") != 1:
            raise ConnectionError("任务身份或成员协议不匹配")
        self.active = task
        self.emit("正在处理「%s」的任务…" % task.get("title", task["room"]))
        answer, done = {}, threading.Event()

        def work():
            try:
                answer.update(executor(task))
            except Exception:
                answer.update(ok=False, text="成员执行失败，请在执行设备查看程序与登录状态", session="")
            finally:
                done.set()

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        last_ok = time.monotonic()
        cancelled = False
        while not done.wait(2):
            if self.stop_event.is_set():
                cancelled = True
                break
            try:
                status = self.task_request("heartbeat", task)
                if status.get("state") not in ("claimed",):
                    cancelled = True
                    break
                last_ok = time.monotonic()
            except ConnectionError as e:
                if e.code in (401, 403, 410) or time.monotonic() - last_ok > 40:
                    cancelled = True
                    break
        if cancelled or self.stop_event.is_set():
            executor.cancel()
            thread.join(5)
            if thread.is_alive():
                self.stop_event.set()  # Never start another task alongside an uninterruptible old one.
            self.emit("任务已停止；已经发生的文件修改或外部服务操作不会自动回滚。")
            self.active = None
            return
        # Retrying a lost acknowledgement submits the cached result, never reruns a model.
        while not self.stop_event.is_set():
            try:
                self.task_request("reply", task, **answer)
                self.emit("回复已进入原会话。")
                break
            except ConnectionError as e:
                if e.code in (401, 403, 409, 410, 413):
                    self.emit(str(e))
                    break
                self.stop_event.wait(3)
        self.active = None


def configure_state(directory):
    root = Path(directory or Path.home() / ".ai-bridge" / "member").expanduser().resolve()
    runtime = str(root / "runtime")
    old = sys.modules.get("bridge.common")
    if old is not None and os.path.normcase(os.path.abspath(old.LOG_DIR)) != os.path.normcase(runtime):
        raise ValueError("请在独立进程启动成员连接端，避免与本机会议室混用数据")
    os.environ["ROUNDTABLE_LOG_DIR"] = runtime
    os.environ["ROUNDTABLE_NO_BROWSER"] = "1"
    root.mkdir(parents=True, exist_ok=True)
    return root


class LocalExecutor:
    def __init__(self, client, agent, workdir, state_dir):
        # Model subprocesses inherit the environment. Remove the room credential
        # after the connector captures it; only local scoped tool keys are used.
        os.environ.pop("AI_BRIDGE_TOKEN", None)
        self.root = configure_state(state_dir)
        from . import agents, registry, room as R, web
        registry.load()
        spec = registry.get(agent)
        if not spec or not spec["enabled"] or spec["driver"] == "external":
            raise ValueError("本机没有可调用的 %s，请先安装并登录，或导入 CLI / HTTP 驱动配置" % agent)
        self.workdir = os.path.realpath(os.path.expanduser(workdir))
        if not os.path.isdir(self.workdir):
            raise ValueError("本机工作文件夹不存在")
        self.client, self.agent, self.rooms, self.current = client, agent, {}, None
        self.RoomClass = _member_room_class()
        self.receipts = self.root / "receipts"
        self.receipts.mkdir(exist_ok=True)
        # Persistent worker ID allows recovery of a lost response after a process restart.
        identity = hashlib.sha256((client.server + client.key + agent + self.workdir).encode()).hexdigest()
        from .common import read_json, write_json
        identity_path = str(self.root / (identity + ".json"))
        saved = read_json(identity_path) or {}
        client.worker = saved.get("worker") or uuid.uuid4().hex
        write_json(identity_path, {"worker":client.worker})
        client.workdir = self.workdir
        # One process per local connector identity. No firewall ports are opened.
        self.exclusive = socket.socket()
        try:
            self.exclusive.bind(("127.0.0.1", 32000 + int(identity[:8], 16) % 20000))
            self.exclusive.listen(1)
        except OSError:
            self.exclusive.close()
            raise ValueError("已有同一配置的成员连接端在运行，请先关闭它")
        self.server = web.ExclusiveServer(("127.0.0.1", 0), web.Handler, cache_ui=False)
        agents.PORT = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def catalog(self, refresh=False):
        from . import model_catalog
        return model_catalog.get(self.agent, self.workdir, refresh=refresh)

    def _room(self, task):
        from . import agents, registry, room as R
        from .common import read_json, write_json
        rid = "member-" + hashlib.sha256((self.client.server + task["member"] + task["room"] + self.agent + self.workdir).encode()).hexdigest()[:24]
        r = self.rooms.get(rid)
        if r is None:
            directory = self.root / "runtime" / "meetings" / rid
            (directory / "files").mkdir(parents=True, exist_ok=True)
            meta = read_json(str(directory / "meeting.json")) or R.default_meta(task.get("title", "成员会话"), task.get("goal", ""), self.workdir)
            r = self.RoomClass(rid, str(directory), R.fix_meta(meta), [], client=self.client)
            R.rooms.loaded[rid] = r
            self.rooms[rid] = r
        r.upstream_task = task
        permissions = dict(task.get("permissions") or {})
        permissions["files"] = False  # Remote file transfer is explicit; never reinterpret host file paths.
        permissions["work"] = permissions.get("work") is True and self.client.allow_work
        r.meta.update(title=task.get("title") or "成员会话", announcement=task.get("goal") or "", workdir=self.workdir,
                      participants=[self.agent], permissions={self.agent:permissions}, board=task.get("board") or [],
                      mode="work" if task.get("mode") == "work" and permissions["work"] else "discuss")
        settings = task.get('model_settings')
        if isinstance(settings, dict):
            # Empty values explicitly clear the previous task's overrides.
            r.meta['models'][self.agent] = {'model':str(settings.get('model') or ''),
                                           'effort':str(settings.get('effort') or ''),
                                           'reasoning':settings.get('reasoning') or {}}
        else:
            for field in ("model", "effort"):
                if task.get(field):
                    r.meta["models"][self.agent][field] = task[field]
        r.save_meta()
        return r

    def __call__(self, task):
        from . import drivers
        from .common import read_json, write_json
        receipt = str(self.receipts / (hashlib.sha256(task["id"].encode()).hexdigest() + ".json"))
        saved = read_json(receipt)
        if saved:
            return saved.get("result") or {"ok":False, "text":"执行端上次在此任务中断，可能已经产生修改，因此不自动重复执行", "session":""}
        write_json(receipt, {"state":"running"})
        r = self._room(task)
        self.current = r
        r.state["running"] = [self.agent]
        r.live[self.agent] = {"text":"", "time":"", "since":time.time()}
        try:
            prompt = ("你是共享会议室中的成员 %s。你只在本设备工作文件夹 %s 中执行，不能访问或假定其他设备的文件。"
                      "跨设备附件未自动同步。不要把发给你的任务再用 post_to_room 重发；直接返回最终回复，由会议室统一记录和处理 @。\n\n" % (task["member"], self.workdir)) + task["prompt"]
            result = drivers.call(self.agent, prompt, r.meta.get(self.agent + "_session"), (), r.mode(), r)
            text = str(result[1] or "成员没有返回内容")
            if len(text.encode()) > 47000:
                text = text.encode()[:46500].decode("utf-8", "ignore") + "\n[回复过长，已截断]"
            answer = {"ok":bool(result[0]), "text":text, "session":str(result[2] or "")}
            if (r.meta.get('model_execution') or {}).get(self.agent):
                answer['model_execution'] = r.meta['model_execution'][self.agent]
            if result[0]:
                r.meta[self.agent + "_session"] = result[2]
                r.save_meta()
            write_json(receipt, {"state":"finished", "result":answer})
            return answer
        finally:
            from .guard import guard
            r.state["running"] = []
            r.live.clear()
            guard.release_turn((r.id, self.agent))
            self.current = None

    def cancel(self):
        if self.current:
            self.current.stop_ais([self.agent])

    def close(self):
        self.cancel()
        for r in self.rooms.values():
            r.close()
        self.server.shutdown()
        self.server.server_close()
        self.exclusive.close()


# Imported after state directory selection; importing this module alone has no side effects.
def _member_room_class():
    from .room import Room

    class MemberRoom(Room):
        def __init__(self, *args, client, **kwargs):
            self.client, self.upstream_task = client, None
            super().__init__(*args, **kwargs)

        def send_file(self, path):
            raise ValueError("跨设备附件尚未启用；请在执行设备查看文件")

        def post_progress(self, ai, text, atts):
            if atts:
                raise ValueError("跨设备附件尚未启用")
            if not self.upstream_task:
                return None
            self.client.task_request("progress", self.upstream_task, text=str(text))
            return {"id":0}

        def board_op(self, by, op, kind=None, text="", ids=()):
            result = self.client.task_request("board", self.upstream_task, op=op, kind=kind, text=text, ids=list(ids))
            self.meta["board"] = result.get("board") or []
            return result.get("result")

        def push_live(self, ai, res):
            super().push_live(ai, res)
            now = time.monotonic()
            text = (self.live.get(ai) or {}).get("text")
            if self.upstream_task and text and now - getattr(self, "last_forward", 0) > 3:
                self.last_forward = now
                try:
                    self.client.task_request("live", self.upstream_task, text=text, timeout=2)
                except ConnectionError:
                    pass  # Heartbeat owns disconnection / cancellation handling.

    return MemberRoom


def main(argv=None):
    # Redirected output uses the Windows locale by default. Chinese connection
    # messages must not terminate the worker on an English-language machine.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="把本设备的 Harness 加入共享会议室，自动接收任务并回复")
    parser.add_argument("--server", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--state-dir")
    parser.add_argument("--allow-work", action="store_true", help="允许执行会话授予的干活权限（默认仅讨论）")
    args = parser.parse_args(argv)
    key = os.environ.get("AI_BRIDGE_TOKEN") or getpass.getpass("成员接入密钥：")
    root = configure_state(args.state_dir)
    client = Client(args.server, key, workdir=args.workdir, allow_work=args.allow_work)
    executor = LocalExecutor(client, args.agent, args.workdir, root)
    try:
        client.run(executor)
    except KeyboardInterrupt:
        client.stop()
        executor.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
