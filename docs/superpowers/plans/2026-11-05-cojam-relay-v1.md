# 合拍 Cojam：中继扫码接入 v1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让任何用户在自己的电脑上开合拍，另一台设备扫码后在任意网络用浏览器进入这间会议室；不装软件、不配 VPN、不开放主机端口。

**Architecture:** 产品方运营一个零依赖的 Python 中继（`relay/`）。主机进程只做出站连接：注册房间、长轮询拉任务、在本地执行白名单里的四条接口、回传结果。访客浏览器访问 `https://<中继>/j/<房间号>#key=<邀请码>`，中继托管访客页并转发白名单请求。管理员能力（`X-RT-Token`、`Cookie`、`/api/rooms` 等）在主机和中继两侧都被挡住。

**Tech Stack:** Python 3.11 标准库（`http.server`、`urllib`、`threading`、`base64`）；现有 `unittest` 测试体系；前端为原生 JS + 仓库内 vendored 的 MIT 二维码库（仅静态资源，不引入 Python 依赖）。

**Spec:** `docs/superpowers/specs/2026-11-05-cojam-relay-design.md`

## Global Constraints

- `pyproject.toml` 的 `dependencies = []` 保持不变；Python 侧不引入任何第三方包。
- 主机仍然只监听 `127.0.0.1`；不修改防火墙、不开公网端口、不自动申请证书。
- 转发白名单只有四对「方法 + 路径」，中继和主机各校验一次：`GET /api/external/rooms`、`GET /api/external/messages`、`POST /api/external/hello`、`POST /api/post`。
- 主机适配器只从任务里取 `Authorization` 和 `Content-Type`；`X-RT-Token`、`Cookie`、`X-Forwarded-*` 永不转发、永不注入。
- 邀请码只放在 URL fragment（`#key=...`）；中继不打印、不落盘 `Authorization` 和请求/响应正文。
- 房间在主机停止轮询 60 秒后作废；主机重启会拿到新房间号，旧二维码立即失效。
- 扫码设备默认只读；发言、唤起、看板、文件、干活由主机在现有「成员与权限」里逐项授予。
- 中继不可达时，本机会议、本机 AI、局域网访客页全部照常工作。
- 所有界面文案用简体中文，风格与现有页面一致（短句、大白话）。
- 每个 `tests/test_*.py` 必须能单独用 `python tests/test_xxx.py` 跑，也能被 `python tests/run_all.py` 收集。
- `relay/` 不加入 `pyproject.toml` 的 `packages`；它是服务端程序，从仓库检出运行 `python -m relay.main`。
- 每个任务结束都提交一次，提交信息沿用仓库现有中文风格。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `relay/__init__.py` | 新建 | 包标记 |
| `relay/store.py` | 新建 | 房间、令牌、任务队列、过期、限速 |
| `relay/server.py` | 新建 | 中继 HTTP：主机接口 + 访客转发 + 访客页静态资源 |
| `relay/main.py` | 新建 | `python -m relay.main` 启动入口 |
| `bridge/relay.py` | 新建 | 主机适配器：注册、拉取、本地执行、回传 |
| `bridge/web.py` | 修改 | 启动时接上适配器；新增 `GET /api/relay/status` |
| `static/join.html` | 修改 | 识别 `/j/<room>` 前缀；断线不退出登录 |
| `static/relay.js` | 新建 | 「邀请设备」弹窗、二维码、允许加入当前会话 |
| `static/qrcode.min.js` | 新建 | vendored qrcodejs 1.0.0（MIT），仅浏览器使用 |
| `static/qrcode.LICENSE.txt` | 新建 | qrcodejs 的 MIT 许可证 |
| `static/connections.js` | 修改 | 中继在线时，网页邀请链接改走中继 |
| `index.html` | 修改 | 顶部加「邀请设备」按钮，加载两个新脚本 |
| `tests/test_relay_store.py` | 新建 | 房间状态单测 |
| `tests/test_relay_host_api.py` | 新建 | 注册/长轮询/回传/令牌/过期 |
| `tests/test_relay_guest.py` | 新建 | 转发、白名单、访客页、静态资源 |
| `tests/test_relay_adapter.py` | 新建 | 主机侧白名单与请求头剥离 |
| `tests/test_relay_status.py` | 新建 | `/api/relay/status` 只给管理员 |
| `tests/test_join_page.py` | 新建 | 访客页前缀与重试逻辑的静态守卫 |
| `tests/test_ui_relay.py` | 新建 | 入口按钮和脚本引用的静态守卫 |
| `tests/test_relay_e2e.py` | 新建 | 中继 + 主机 + 访客全链路 |
| `docs/RELAY.md` | 新建 | 本地调试、部署、运维、安全说明 |

---

### Task 1: 中继房间状态（store）

**Files:**
- Create: `relay/__init__.py`
- Create: `relay/store.py`
- Test: `tests/test_relay_store.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `relay.store.RelayError(code: int, message: str)`，异常对象带 `.code` 和 `.message`。
  - `Store(room_idle=60.0)`：`.register(name="") -> Room`、`.get(room_id) -> Room`、`.get_host(room_id, token) -> Room`、`.sweep() -> None`。
  - `Room`：`.id`（`token_urlsafe(16)`）、`.token`、`.submit(job: dict, timeout=30.0) -> result dict`、`.take(wait: float) -> job | None`、`.finish(job_id, result) -> bool`、`.touch()`。
  - 模块常量 `MAX_PENDING`、`MAX_ROOMS`、`RATE_MAX`、`RATE_WINDOW`，测试会临时改它们。

- [ ] **Step 1: 写失败测试**

Create `tests/test_relay_store.py`:

```python
"""中继房间状态：注册、令牌、排队、过期、限速。"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from relay import store
from relay.store import RelayError, Store


class StoreTest(unittest.TestCase):
    def test_register_and_host_auth(self):
        s = Store()
        room = s.register("笔记本")
        self.assertGreaterEqual(len(room.id), 16)
        self.assertIs(s.get(room.id), room)
        self.assertIs(s.get_host(room.id, room.token), room)
        with self.assertRaises(RelayError) as e:
            s.get_host(room.id, "wrong")
        self.assertEqual(e.exception.code, 401)

    def test_unknown_room(self):
        with self.assertRaises(RelayError) as e:
            Store().get("nope")
        self.assertEqual(e.exception.code, 404)

    def test_device_limit(self):
        room = Store().register()
        for i in range(store.DEVICE_LIMIT):
            self.assertTrue(room.allow_device("10.0.0.%d" % i))
        self.assertFalse(room.allow_device("10.0.0.99"))
        self.assertTrue(room.allow_device("10.0.0.0"))   # 已见过的设备不受影响

    def test_expiry_after_host_silence(self):
        s = Store(room_idle=0.05)
        room = s.register()
        time.sleep(0.08)
        with self.assertRaises(RelayError) as e:
            s.get(room.id)
        self.assertEqual(e.exception.code, 404)
        s.sweep()
        self.assertNotIn(room.id, s.rooms)

    def test_job_round_trip(self):
        s = Store()
        room = s.register()
        out = []

        def guest():
            out.append(room.submit({"id": "j1", "method": "GET", "path": "/api/external/rooms"}, 2))

        t = threading.Thread(target=guest)
        t.start()
        job = room.take(2)
        self.assertEqual(job["id"], "j1")
        self.assertTrue(room.finish("j1", {"status": 200, "body": "", "content_type": "text/plain"}))
        t.join(2)
        self.assertEqual(out[0]["status"], 200)

    def test_guest_timeout_and_late_result(self):
        room = Store().register()
        with self.assertRaises(RelayError) as e:
            room.submit({"id": "j2"}, 0.01)
        self.assertEqual(e.exception.code, 504)
        self.assertFalse(room.finish("j2", {}))

    def test_rate_limit(self):
        room = Store().register()
        old = store.RATE_MAX
        store.RATE_MAX = 1
        try:
            with self.assertRaises(RelayError) as first:
                room.submit({"id": "a"}, 0.01)
            self.assertEqual(first.exception.code, 504)
            with self.assertRaises(RelayError) as second:
                room.submit({"id": "b"}, 0.01)
            self.assertEqual(second.exception.code, 429)
        finally:
            store.RATE_MAX = old

    def test_pending_limit(self):
        room = Store().register()
        old_pending, old_rate = store.MAX_PENDING, store.RATE_MAX
        store.MAX_PENDING, store.RATE_MAX = 1, 100
        try:
            def guest():
                try:
                    room.submit({"id": "a"}, 1)
                except RelayError:
                    pass

            t = threading.Thread(target=guest)
            t.start()
            for _ in range(100):
                if room.pending:
                    break
                time.sleep(0.01)
            with self.assertRaises(RelayError) as e:
                room.submit({"id": "b"}, 0.01)
            self.assertEqual(e.exception.code, 429)
            t.join(2)
        finally:
            store.MAX_PENDING, store.RATE_MAX = old_pending, old_rate


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_relay_store.py`
Expected: `ModuleNotFoundError: No module named 'relay'`

- [ ] **Step 3: 写实现**

Create `relay/__init__.py`（空文件）和 `relay/store.py`:

```python
# -*- coding: utf-8 -*-
"""中继的房间状态：注册、排队、回传、过期。只放内存，不落盘。"""
import collections
import secrets
import threading
import time

ROOM_IDLE = 60.0      # 主机这么久没有 register / pull，房间作废
JOB_TTL = 30.0        # 访客请求最多等这么久
RATE_WINDOW = 10.0
RATE_MAX = 30
DEVICE_LIMIT = 5        # 一个房间同时最多几台设备（按来源 IP 算，10 分钟窗口）
DEVICE_WINDOW = 600.0
MAX_PENDING = 50
MAX_ROOMS = 500


class RelayError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class Room:
    def __init__(self, name="", now=None):
        self.id = secrets.token_urlsafe(16)
        self.token = secrets.token_urlsafe(32)
        self.name = str(name or "")[:40]
        self.created = now if now is not None else time.time()
        self.last_seen = self.created
        self.pending = collections.deque()
        self.waiting = {}      # job_id -> threading.Event
        self.results = {}      # job_id -> 结果
        self.recent = collections.deque()
        self.devices = {}      # 来源 IP -> 最后出现时间
        self.condition = threading.Condition()

    def touch(self):
        self.last_seen = time.time()

    def allow_device(self, ip):
        now = time.time()
        with self.condition:
            for addr, seen in list(self.devices.items()):
                if now - seen > DEVICE_WINDOW:
                    del self.devices[addr]
            if ip not in self.devices and len(self.devices) >= DEVICE_LIMIT:
                return False
            self.devices[ip] = now
            return True

    def submit(self, job, timeout=JOB_TTL):
        now = time.time()
        with self.condition:
            while self.recent and now - self.recent[0] > RATE_WINDOW:
                self.recent.popleft()
            if len(self.recent) >= RATE_MAX:
                raise RelayError(429, "请求太频繁，请稍后再试")
            if len(self.pending) >= MAX_PENDING:
                raise RelayError(429, "主机忙不过来，请稍后再试")
            self.recent.append(now)
            event = threading.Event()
            self.waiting[job["id"]] = event
            self.pending.append(job)
            self.condition.notify_all()
        if not event.wait(timeout):
            with self.condition:
                self.waiting.pop(job["id"], None)
                self.results.pop(job["id"], None)
                try:
                    self.pending.remove(job)
                except ValueError:
                    pass
            raise RelayError(504, "主机没有响应")
        with self.condition:
            return self.results.pop(job["id"], None)

    def take(self, wait):
        with self.condition:
            if not self.pending:
                self.condition.wait(wait)
            return self.pending.popleft() if self.pending else None

    def finish(self, job_id, result):
        with self.condition:
            event = self.waiting.pop(job_id, None)
            if event is None:
                return False      # 访客已经超时走了，结果丢掉
            self.results[job_id] = result
            event.set()
            return True


class Store:
    def __init__(self, room_idle=ROOM_IDLE):
        self.lock = threading.RLock()
        self.rooms = {}
        self.room_idle = room_idle

    def register(self, name=""):
        with self.lock:
            self._sweep_locked()
            if len(self.rooms) >= MAX_ROOMS:
                raise RelayError(429, "中继房间已满")
            room = Room(name)
            self.rooms[room.id] = room
            return room

    def get(self, room_id):
        with self.lock:
            room = self.rooms.get(room_id)
            if room is None or time.time() - room.last_seen > self.room_idle:
                raise RelayError(404, "房间不存在或已过期")
            return room

    def get_host(self, room_id, token):
        room = self.get(room_id)
        if not token or not secrets.compare_digest(room.token, token):
            raise RelayError(401, "主机令牌无效")
        room.touch()
        return room

    def sweep(self):
        with self.lock:
            self._sweep_locked()

    def _sweep_locked(self):
        now = time.time()
        for rid, room in list(self.rooms.items()):
            if now - room.last_seen > self.room_idle:
                del self.rooms[rid]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_relay_store.py`
Expected: `Ran 8 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add relay/__init__.py relay/store.py tests/test_relay_store.py
git commit -m "中继（1/8）：房间状态、任务队列、过期与限速"
```

---

### Task 2: 中继主机接口（注册 / 长轮询 / 回传）

**Files:**
- Create: `relay/server.py`
- Create: `relay/main.py`
- Test: `tests/test_relay_host_api.py`

**Interfaces:**
- Consumes: `relay.store.Store`、`relay.store.RelayError`。
- Produces:
  - `relay.server.RelayServer(address, static_dir)`，实例带 `.store`、`.static`（文件名 → bytes）。
  - `relay.server.Handler`。
  - HTTP：`POST /v1/host/register` → `{"room_id", "host_token"}`；`GET /v1/host/pull?room=` → `{"job": {...}}` 或 `204`；`POST /v1/host/result` → `{"ok": true}`。
  - `relay.main.main(argv=None)`，可 `python -m relay.main --host 127.0.0.1 --port 8787`。

- [ ] **Step 1: 写失败测试**

Create `tests/test_relay_host_api.py`:

```python
"""中继主机接口：注册、长轮询、回传、令牌、过期。"""
import base64
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from relay import server
from relay.server import RelayServer

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def call(base, path, obj=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(obj).encode() if obj is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, json.loads(raw) if raw else None


class HostApiTest(unittest.TestCase):
    def setUp(self):
        server.PULL_WAIT = 0.2
        self.srv = RelayServer(("127.0.0.1", 0), STATIC)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def register(self):
        status, data = call(self.base, "/v1/host/register", {"name": "测试主机"})
        self.assertEqual(status, 200)
        return data["room_id"], data["host_token"]

    def test_healthz(self):
        self.assertEqual(call(self.base, "/healthz")[0], 200)

    def test_pull_requires_host_token(self):
        room_id, _ = self.register()
        self.assertEqual(call(self.base, "/v1/host/pull?room=" + room_id)[0], 401)
        self.assertEqual(call(self.base, "/v1/host/pull?room=" + room_id, token="wrong")[0], 401)

    def test_unknown_room(self):
        self.assertEqual(call(self.base, "/v1/host/pull?room=nope", token="x")[0], 404)

    def test_pull_times_out_empty(self):
        room_id, token = self.register()
        self.assertEqual(call(self.base, "/v1/host/pull?room=" + room_id, token=token), (204, None))

    def test_job_round_trip(self):
        room_id, token = self.register()
        room = self.srv.store.get(room_id)
        out = []

        def guest():
            out.append(room.submit({"id": "j1", "method": "GET", "path": "/api/external/rooms"}, 5))

        t = threading.Thread(target=guest)
        t.start()
        time.sleep(0.05)
        status, data = call(self.base, "/v1/host/pull?room=" + room_id, token=token)
        self.assertEqual((status, data["job"]["id"]), (200, "j1"))
        payload = base64.b64encode(b'{"ok":true}').decode()
        status, data = call(self.base, "/v1/host/result", {
            "room": room_id, "job_id": "j1", "status": 200,
            "content_type": "application/json", "body": payload}, token=token)
        self.assertEqual((status, data["ok"]), (200, True))
        t.join(2)
        self.assertEqual(base64.b64decode(out[0]["body"]), b'{"ok":true}')

    def test_late_result_is_rejected(self):
        room_id, token = self.register()
        status, data = call(self.base, "/v1/host/result", {
            "room": room_id, "job_id": "gone", "status": 200, "body": ""}, token=token)
        self.assertEqual((status, data["ok"]), (410, False))

    def test_room_expires_without_polls(self):
        room_id, token = self.register()
        self.srv.store.room_idle = 0.05
        time.sleep(0.08)
        self.assertEqual(call(self.base, "/v1/host/pull?room=" + room_id, token=token)[0], 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_relay_host_api.py`
Expected: `ModuleNotFoundError: No module named 'relay.server'`

- [ ] **Step 3: 写实现**

Create `relay/server.py`:

```python
# -*- coding: utf-8 -*-
"""公网中继：主机出站长轮询，访客浏览器打进来。只用标准库。"""
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .store import RelayError, Store

MAX_BODY = 256 * 1024
MAX_RESULT = 2 * 1024 * 1024
PULL_WAIT = 25
REGISTER_PER_HOUR = 20
IP_RE = re.compile(r"^[0-9A-Fa-f:.]{2,45}$")


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, static_dir):
        self.store = Store()
        self.static = {}
        for name in ("join.html", "guest.css", "logo.svg", "icon-192.png"):
            with open(os.path.join(static_dir, name), "rb") as f:
                self.static[name] = f.read()
        self.registrations = {}
        self.limit_lock = threading.Lock()
        super().__init__(address, Handler)
        threading.Thread(target=self._sweep, daemon=True).start()

    def _sweep(self):
        while True:
            time.sleep(5)
            self.store.sweep()

    def allow(self, table, ip, limit, window):
        now = time.time()
        with self.limit_lock:
            hits = [t for t in table.get(ip, []) if now - t < window]
            if len(hits) >= limit:
                return False
            hits.append(now)
            table[ip] = hits
            return True


class Handler(BaseHTTPRequestHandler):
    server_version = "CojamRelay"
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", extra)

    def _length(self):
        try:
            return int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return -1

    def _read_json(self, limit=MAX_BODY):
        n = self._length()
        if n < 0 or n > limit:
            return None
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return None

    def _bearer(self):
        value = self.headers.get("Authorization") or ""
        return value[7:].strip() if value.lower().startswith("bearer ") else ""

    def _client_ip(self):
        peer = self.client_address[0]
        if peer in ("127.0.0.1", "::1"):
            forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if forwarded and IP_RE.match(forwarded):
                return forwarded
        return peer

    def do_GET(self):
        parts = urlsplit(self.path)
        if parts.path == "/healthz":
            return self._json(200, {"ok": True})
        if parts.path == "/v1/host/pull":
            return self._pull(parse_qs(parts.query))
        return self._send(404, b"not found", "text/plain")

    do_HEAD = do_GET

    def do_POST(self):
        parts = urlsplit(self.path)
        if parts.path == "/v1/host/register":
            return self._register()
        if parts.path == "/v1/host/result":
            return self._result()
        self.close_connection = True      # 没读正文，别让残留数据污染下一个请求
        return self._send(404, b"not found", "text/plain")

    def _register(self):
        if not self.server.allow(self.server.registrations, self._client_ip(), REGISTER_PER_HOUR, 3600):
            return self._json(429, {"error": "注册太频繁"})
        data = self._read_json()
        if data is None:
            return self._json(400, {"error": "bad json"})
        try:
            room = self.server.store.register(str(data.get("name") or ""))
        except RelayError as e:
            return self._json(e.code, {"error": e.message})
        return self._json(200, {"room_id": room.id, "host_token": room.token})

    def _pull(self, q):
        rid = (q.get("room") or [""])[0]
        try:
            room = self.server.store.get_host(rid, self._bearer())
        except RelayError as e:
            return self._json(e.code, {"error": e.message})
        job = room.take(PULL_WAIT)
        room.touch()
        if job is None:
            return self._send(204, b"", "text/plain")
        return self._json(200, {"job": job})

    def _result(self):
        data = self._read_json(MAX_RESULT)
        if data is None:
            return self._json(400, {"error": "bad json"})
        try:
            room = self.server.store.get_host(str(data.get("room") or ""), self._bearer())
        except RelayError as e:
            return self._json(e.code, {"error": e.message})
        ok = room.finish(str(data.get("job_id") or ""), {
            "status": int(data.get("status") or 502),
            "content_type": str(data.get("content_type") or "application/json; charset=utf-8"),
            "body": str(data.get("body") or ""),
        })
        return self._json(200 if ok else 410, {"ok": ok})
```

Create `relay/main.py`:

```python
# -*- coding: utf-8 -*-
"""python -m relay.main 启动中继。"""
import argparse
import os

from .server import RelayServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(os.path.dirname(HERE), "static")


def main(argv=None):
    ap = argparse.ArgumentParser(description="合拍 Cojam 中继")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--static", default=STATIC)
    args = ap.parse_args(argv)
    srv = RelayServer((args.host, args.port), args.static)
    print("中继已启动：http://%s:%d/" % (args.host, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_relay_host_api.py`
Expected: `Ran 7 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add relay/server.py relay/main.py tests/test_relay_host_api.py
git commit -m "中继（2/8）：主机注册、长轮询、结果回传"
```

---

### Task 3: 访客转发、白名单、访客页与静态资源

**Files:**
- Modify: `relay/server.py`
- Test: `tests/test_relay_guest.py`

**Interfaces:**
- Consumes: Task 1 的 `Room.submit`、Task 2 的 `RelayServer`。
- Produces:
  - `GET /j/<room_id>` → `join.html`。
  - `GET /static/<name>` → 仅 `guest.css`、`logo.svg`、`icon-192.png`。
  - `GET|POST /j/<room_id>/api/...` → 只放行四对白名单；任务体字段为 `id / method / path / query / body(base64) / authorization / content_type`。
  - 未知路径一律 404，且不进入队列。

- [ ] **Step 1: 写失败测试**

Create `tests/test_relay_guest.py`:

```python
"""访客经中继访问：白名单、转发内容、访客页和静态资源。"""
import base64
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from relay import server
from relay.server import RelayServer

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def call(base, path, obj=None, token=None, headers=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    h.update(headers or {})
    data = json.dumps(obj).encode() if obj is not None else None
    req = urllib.request.Request(base + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


class FakeHost(threading.Thread):
    def __init__(self, base, room_id, token):
        super().__init__(daemon=True)
        self.base, self.room_id, self.token = base, room_id, token
        self.jobs = []
        self.stopping = threading.Event()

    def run(self):
        while not self.stopping.is_set():
            status, raw, _ = call(self.base, "/v1/host/pull?room=" + self.room_id, token=self.token)
            if status != 200:
                continue
            job = json.loads(raw)["job"]
            self.jobs.append(job)
            body = base64.b64encode(b'{"ok":true}').decode()
            call(self.base, "/v1/host/result", {
                "room": self.room_id, "job_id": job["id"], "status": 200,
                "content_type": "application/json", "body": body}, token=self.token)


class GuestTest(unittest.TestCase):
    def setUp(self):
        server.PULL_WAIT = 0.2
        self.srv = RelayServer(("127.0.0.1", 0), STATIC)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        status, raw, _ = call(self.base, "/v1/host/register", {"name": "测试"})
        data = json.loads(raw)
        self.room_id, self.token = data["room_id"], data["host_token"]
        self.host = FakeHost(self.base, self.room_id, self.token)
        self.host.start()

    def tearDown(self):
        self.host.stopping.set()
        self.host.join(2)
        self.srv.shutdown()
        self.srv.server_close()

    def guest(self, path, obj=None, token="invite-key"):
        return call(self.base, "/j/" + self.room_id + path, obj, token=token)

    def test_guest_page_and_static(self):
        status, raw, headers = call(self.base, "/j/" + self.room_id)
        self.assertEqual(status, 200)
        self.assertIn("<title>加入会话", raw.decode("utf-8"))
        status, raw, headers = call(self.base, "/static/guest.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers.get("Content-Type", ""))

    def test_whitelisted_get_is_forwarded(self):
        status, raw, _ = self.guest("/api/external/rooms")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"ok": True})
        job = self.host.jobs[-1]
        self.assertEqual((job["method"], job["path"], job["authorization"]),
                         ("GET", "/api/external/rooms", "Bearer invite-key"))

    def test_post_body_and_type_reach_host(self):
        status, raw, _ = self.guest("/api/external/hello", {"client": "AI Bridge 网页"})
        self.assertEqual(status, 200)
        job = self.host.jobs[-1]
        self.assertEqual(job["method"], "POST")
        self.assertEqual(base64.b64decode(job["body"]), b'{"client": "AI Bridge \\u7f51\\u9875"}')

    def test_query_string_is_forwarded(self):
        status, raw, _ = self.guest("/api/external/messages?room=r1&since=7")
        self.assertEqual(status, 200)
        self.assertEqual(self.host.jobs[-1]["query"], "room=r1&since=7")

    def test_whitelist_blocks_everything_else(self):
        before = len(self.host.jobs)
        for path in ("/api/rooms", "/api/r/x/state", "/static/../server.py", "/mcp", "/files/x"):
            status, _, _ = self.guest(path)
            self.assertEqual(status, 404, path)
        time.sleep(0.3)
        self.assertEqual(len(self.host.jobs), before)

    def test_unknown_room(self):
        status, _, _ = call(self.base, "/j/" + "a" * 22 + "/api/external/rooms", token="k")
        self.assertEqual(status, 404)

    def test_bad_query_is_rejected(self):
        status, _, _ = self.guest("/api/external/messages?x=%0d%0aInjected: 1")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_relay_guest.py`
Expected: 访客页和转发断言失败（当前 404）

- [ ] **Step 3: 写实现**

Modify `relay/server.py`:

3a. 在 import 区加 `base64`、`secrets`（`re` 在任务 2 已经加了）：

```python
import base64
import json
import os
import re
import secrets
import threading
import time
```

3b. 在常量区加：

```python
JOB_WAIT = 30
STATIC_TYPES = {"guest.css": "text/css; charset=utf-8", "logo.svg": "image/svg+xml",
                "icon-192.png": "image/png"}
WHITELIST = {("GET", "/api/external/rooms"), ("GET", "/api/external/messages"),
             ("POST", "/api/external/hello"), ("POST", "/api/post")}
JOB_PATH = re.compile(r"^/j/([A-Za-z0-9_\-]{16,40})(/api/[A-Za-z0-9_/\-]+)$")
PAGE_PATH = re.compile(r"^/j/[A-Za-z0-9_\-]{16,40}$")
```

3c. 在 `RelayServer.__init__` 里加 `self.guest_hits = {}`（放在 `self.registrations` 后面）。

3d. 把 `do_GET` 改成：

```python
    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/healthz":
            return self._json(200, {"ok": True})
        if path.startswith("/static/") and path[8:] in STATIC_TYPES:
            name = path[8:]
            return self._send(200, self.server.static[name], STATIC_TYPES[name],
                              {"X-Content-Type-Options": "nosniff"})
        if path == "/v1/host/pull":
            return self._pull(parse_qs(parts.query))
        m = JOB_PATH.match(path)
        if m:
            return self._guest(m.group(1), "GET", m.group(2), parts.query, b"")
        if PAGE_PATH.match(path):
            return self._send(200, self.server.static["join.html"], "text/html; charset=utf-8")
        return self._send(404, b"not found", "text/plain")
```

3e. 在 `do_POST` 的 `_result` 分支后加：

```python
        m = JOB_PATH.match(parts.path)
        if m:
            n = self._length()
            if n < 0 or n > MAX_BODY:
                return self._json(413, {"error": "too large"})
            body = self.rfile.read(n) if n else b""
            return self._guest(m.group(1), "POST", m.group(2), parts.query, body)
```

3f. 在 `_result` 方法后加：

```python
    def _guest(self, rid, method, path, query, body):
        if (method, path) not in WHITELIST:
            return self._send(404, b"not found", "text/plain")
        ip = self._client_ip()
        if not self.server.allow(self.server.guest_hits, ip, 120, 60):
            return self._json(429, {"error": "请求太频繁"})
        if len(query) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in query):
            return self._json(400, {"error": "bad query"})
        try:
            room = self.server.store.get(rid)
        except RelayError as e:
            return self._json(e.code, {"error": e.message})
        if not room.allow_device(ip):
            return self._json(429, {"error": "这间房间连接的设备太多"})
        job = {"id": secrets.token_urlsafe(12), "method": method, "path": path,
               "query": query, "body": base64.b64encode(body).decode("ascii"),
               "authorization": self.headers.get("Authorization") or "",
               "content_type": self.headers.get("Content-Type") or "application/json"}
        try:
            result = room.submit(job, JOB_WAIT)
        except RelayError as e:
            return self._json(e.code, {"error": e.message})
        if result is None:
            return self._json(502, {"error": "主机没有返回结果"})
        try:
            raw = base64.b64decode(result.get("body") or "", validate=True)
        except Exception:
            raw = b""
        return self._send(int(result.get("status") or 502), raw,
                          result.get("content_type") or "application/json; charset=utf-8")
```

注意：`_guest` 只把 `Authorization` 和 `Content-Type` 放进任务；`Cookie`、`X-RT-Token`、`X-Forwarded-*` 从不出现。

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_relay_guest.py`
Expected: `Ran 7 tests ... OK`

- [ ] **Step 5: 回归相邻测试并提交**

Run: `python tests/test_relay_store.py && python tests/test_relay_host_api.py`
Expected: 两个都 `OK`

```bash
git add relay/server.py tests/test_relay_guest.py
git commit -m "中继（3/8）：访客页、静态资源、四条白名单转发"
```

---

### Task 4: 主机适配器（本地执行 + 请求头剥离）

**Files:**
- Create: `bridge/relay.py`
- Test: `tests/test_relay_adapter.py`

**Interfaces:**
- Consumes: 中继协议（Task 2/3 的 JSON 字段）。
- Produces:
  - `bridge.relay.RelayError(code, message)`。
  - `local_request(job: dict, port: int) -> (status: int, content_type: str, body: bytes)`。
  - `RelayClient(base, local_port, name="")`：`.start()`、`.stop()`、`.status() -> dict`、`.invite_base() -> str`。
  - `status()` 字段固定为 `enabled / relay / room_id / invite_base / online / error / guests`；`guests` 是 `[{"contact", "first", "last"}]`。

- [ ] **Step 1: 写失败测试**

Create `tests/test_relay_adapter.py`:

```python
"""主机适配器：路径白名单、请求头剥离、查询串校验。"""
import base64
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bridge import relay


class Echo(BaseHTTPRequestHandler):
    def _reply(self):
        raw = json.dumps({"path": self.path, "headers": dict(self.headers)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._reply()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._reply()

    def log_message(self, *a):
        pass


class AdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def job(self, method, path, query="", body=b"", **extra):
        job = {"id": "j1", "method": method, "path": path, "query": query,
               "body": base64.b64encode(body).decode(), "authorization": "Bearer invite-key",
               "content_type": "application/json"}
        job.update(extra)
        return job

    def test_only_whitelisted_paths_execute(self):
        for method, path in (("GET", "/api/rooms"), ("POST", "/api/external/rooms"),
                             ("GET", "/mcp"), ("GET", "/files/x"), ("POST", "/api/r/x/state")):
            with self.assertRaises(relay.RelayError) as e:
                relay.local_request(self.job(method, path), self.port)
            self.assertEqual(e.exception.code, 404, (method, path))

    def test_admin_headers_are_not_forwarded(self):
        job = self.job("GET", "/api/external/rooms", query="room=r1",
                       headers={"X-RT-Token": "secret", "Cookie": "rt_owner=secret"},
                       xtra="ignored")
        status, ctype, body = relay.local_request(job, self.port)
        self.assertEqual(status, 200)
        seen = json.loads(body)
        self.assertEqual(seen["path"], "/api/external/rooms?room=r1")
        self.assertEqual(seen["headers"].get("Authorization"), "Bearer invite-key")
        self.assertNotIn("X-Rt-Token", seen["headers"])
        self.assertNotIn("Cookie", seen["headers"])

    def test_bad_query_and_body_are_rejected(self):
        with self.assertRaises(relay.RelayError) as e:
            relay.local_request(self.job("GET", "/api/external/messages", query="x=%0d%0aInjected: 1"), self.port)
        self.assertEqual(e.exception.code, 400)
        bad = dict(self.job("POST", "/api/post"), body="not base64!")
        with self.assertRaises(relay.RelayError) as e:
            relay.local_request(bad, self.port)
        self.assertEqual(e.exception.code, 400)

    def test_post_body_reaches_local_server(self):
        status, ctype, body = relay.local_request(
            self.job("POST", "/api/post", body=b'{"room":"r1","text":"hi"}'), self.port)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["path"], "/api/post")

    def test_http_relay_is_loopback_only(self):
        self.assertTrue(relay.RelayClient("http://127.0.0.1:8787", 8765))
        with self.assertRaises(ValueError):
            relay.RelayClient("http://relay.example.com", 8765)
        with self.assertRaises(ValueError):
            relay.RelayClient("ftp://127.0.0.1", 8765)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_relay_adapter.py`
Expected: `ImportError: cannot import name 'relay' from 'bridge'`

- [ ] **Step 3: 写实现**

Create `bridge/relay.py`:

```python
# -*- coding: utf-8 -*-
"""主机侧中继适配器：只出站注册和拉取，在本地执行白名单接口后回传。"""
import base64
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

DEFAULT_RELAY = ""      # 正式域名确定后填 https://relay.example.com；环境变量 COJAM_RELAY 优先
WHITELIST = {("GET", "/api/external/rooms"), ("GET", "/api/external/messages"),
             ("POST", "/api/external/hello"), ("POST", "/api/post")}
MAX_BODY = 256 * 1024
MAX_RESULT = 1024 * 1024
MAX_QUERY = 512


class RelayError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def local_request(job, port):
    """执行一条中继任务。白名单之外一律拒绝，管理员请求头在这里被丢掉。"""
    method = str(job.get("method") or "")
    path = str(job.get("path") or "")
    if (method, path) not in WHITELIST:
        raise RelayError(404, "path not allowed")
    query = str(job.get("query") or "")
    if len(query) > MAX_QUERY or any(ord(c) < 33 or ord(c) > 126 for c in query):
        raise RelayError(400, "bad query")
    try:
        body = base64.b64decode(job.get("body") or "", validate=True)
    except Exception:
        raise RelayError(400, "bad body")
    if len(body) > MAX_BODY:
        raise RelayError(413, "too large")
    headers = {}
    auth = str(job.get("authorization") or "")
    if auth.startswith("Bearer "):
        headers["Authorization"] = auth
    ctype = str(job.get("content_type") or "application/json")
    if "\r" not in ctype and "\n" not in ctype:
        headers["Content-Type"] = ctype
    url = "http://127.0.0.1:%d%s" % (port, path)
    if query:
        url += "?" + query
    req = urllib.request.Request(url, data=body if method == "POST" else None,
                                 headers=headers, method=method)
    try:
        with OPENER.open(req, timeout=20) as r:
            raw = r.read(MAX_RESULT + 1)
            return r.status, r.headers.get("Content-Type") or "application/json; charset=utf-8", raw[:MAX_RESULT]
    except urllib.error.HTTPError as e:
        raw = e.read(MAX_RESULT + 1)
        return e.code, e.headers.get("Content-Type") or "application/json; charset=utf-8", raw[:MAX_RESULT]


class RelayClient:
    def __init__(self, base, local_port, name=""):
        parts = urlsplit(str(base or "").strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("COJAM_RELAY 必须是 http:// 或 https:// 地址")
        if parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("中继必须用 https（本机调试除外）")
        self.base = str(base).strip().rstrip("/")
        self.local_port = local_port
        self.name = name
        self.room_id = ""
        self.host_token = ""
        self.online = False
        self.last_error = ""
        self.guests = {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()

    def invite_base(self):
        with self.lock:
            return self.base + "/j/" + self.room_id if self.room_id else ""

    def status(self):
        with self.lock:
            return {"enabled": True, "relay": self.base, "room_id": self.room_id,
                    "invite_base": self.base + "/j/" + self.room_id if self.room_id else "",
                    "online": self.online, "error": self.last_error,
                    "guests": [dict(contact=k, **v) for k, v in self.guests.items()]}

    def _set(self, online, error=""):
        with self.lock:
            self.online = online
            self.last_error = error

    def _post(self, path, obj, token=""):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode("utf-8"),
                                     headers=headers, method="POST")
        with OPENER.open(req, timeout=35) as r:
            return json.loads(r.read() or b"{}")

    def _register(self):
        data = self._post("/v1/host/register", {"name": self.name})
        with self.lock:
            self.room_id = str(data.get("room_id") or "")
            self.host_token = str(data.get("host_token") or "")
        if not self.room_id or not self.host_token:
            raise ValueError("中继没有返回房间号")

    def _pull(self):
        req = urllib.request.Request(self.base + "/v1/host/pull?room=" + self.room_id,
                                     headers={"Authorization": "Bearer " + self.host_token})
        with OPENER.open(req, timeout=35) as r:
            if r.status == 204:
                return None
            return (json.loads(r.read() or b"{}") or {}).get("job")

    def _run(self):
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                if not self.room_id:
                    self._register()
                    self._set(True)      # 注册成功就算在线，别等第一次长轮询返回
                job = self._pull()
                if job:
                    self._finish(job)
                self._set(True)
                backoff = 1.0
            except urllib.error.HTTPError as e:
                if e.code in (401, 404, 410):
                    with self.lock:
                        self.room_id = ""
                        self.host_token = ""
                self._set(False, "中继返回 %d" % e.code)
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 30)
            except Exception as e:
                self._set(False, str(e)[:200] or e.__class__.__name__)
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 30)

    def _finish(self, job):
        try:
            status, ctype, body = local_request(job, self.local_port)
        except RelayError as e:
            status = e.code
            ctype = "application/json; charset=utf-8"
            body = json.dumps({"error": e.message}, ensure_ascii=False).encode("utf-8")
        if job.get("path") == "/api/external/hello" and status == 200:
            try:
                pid = str((json.loads(body) or {}).get("contact", {}).get("id") or "")
                if pid:
                    now = time.time()
                    with self.lock:
                        item = self.guests.get(pid) or {"first": now}
                        item["last"] = now
                        self.guests[pid] = item
            except Exception:
                pass
        self._post("/v1/host/result", {
            "room": self.room_id, "job_id": job.get("id"), "status": status,
            "content_type": ctype, "body": base64.b64encode(body).decode("ascii")},
            self.host_token)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_relay_adapter.py`
Expected: `Ran 5 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add bridge/relay.py tests/test_relay_adapter.py
git commit -m "中继（4/8）：主机适配器，双重白名单与请求头剥离"
```

---

### Task 5: 应用启动接入 + 只读状态接口

**Files:**
- Modify: `bridge/web.py`
- Test: `tests/test_relay_status.py`

**Interfaces:**
- Consumes: `bridge.relay.RelayClient`（Task 4）。
- Produces:
  - `GET /api/relay/status`（管理员）→ `RelayClient.status()`；未启用时 `{"enabled": false}`；非管理员 403。
  - 启动时读环境变量 `COJAM_RELAY`、可选 `COJAM_RELAY_NAME`。

- [ ] **Step 1: 写失败测试**

Create `tests/test_relay_status.py`:

```python
"""中继状态接口只给管理员，未配置时明确报 disabled。"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

os.environ["ROUNDTABLE_LOG_DIR"] = tempfile.mkdtemp(prefix="bridge_relay_status_")
os.environ["ROUNDTABLE_NO_PRESETS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bridge import agents, web

agents.prewarm = lambda *a: None
srv = web.ExclusiveServer(("127.0.0.1", 0), web.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%s" % srv.server_address[1]


def get(owner=False):
    headers = {"X-RT-Token": web.RT_TOKEN} if owner else {}
    req = urllib.request.Request(BASE + "/api/relay/status", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class FakeRelay:
    def status(self):
        return {"enabled": True, "online": True, "room_id": "room-1",
                "invite_base": "https://relay.test/j/room-1", "guests": []}


class RelayStatusTest(unittest.TestCase):
    def test_owner_only_and_disabled_without_relay(self):
        self.assertEqual(get()[0], 403)
        status, data = get(owner=True)
        self.assertEqual(status, 200)
        self.assertEqual(data, {"enabled": False})

    def test_configured_relay_reports_status(self):
        srv.relay = FakeRelay()
        try:
            status, data = get(owner=True)
            self.assertEqual(status, 200)
            self.assertTrue(data["online"])
            self.assertEqual(data["invite_base"], "https://relay.test/j/room-1")
        finally:
            del srv.relay


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_relay_status.py`
Expected: 403 通过，但管理员请求返回 404

- [ ] **Step 3: 改 `bridge/web.py`**

3a. 在 `do_GET` 的管理员门禁后加状态接口（就在 `if path.startswith("/api/") and not self._from_browser():` 这三行之后）：

```python
        if path == "/api/relay/status":
            client = getattr(self.server, "relay", None)
            return self._json(200, client.status() if client else {"enabled": False})
```

3b. 在 `main()` 里，`srv` 绑定成功、`agents.start_reaper()` 之前启动适配器：

```python
    relay_client = None
    from . import relay as relay_module
    relay_url = (os.environ.get("COJAM_RELAY") or relay_module.DEFAULT_RELAY).strip()
    if relay_url:
        try:
            relay_client = relay_module.RelayClient(
                relay_url, PORT, os.environ.get("COJAM_RELAY_NAME") or socket.gethostname()).start()
            srv.relay = relay_client
        except ValueError as error:
            print("中继没有启动：" + str(error), file=sys.stderr)
```

3c. 在 `main()` 的 `finally` 里补一行：

```python
    finally:
        if relay_client:
            relay_client.stop()
        srv.server_close()
```

注意：`relay_client` 必须在 `try` 之前定义（3b 的位置在 `srv.serve_forever()` 之前），否则 `finally` 会 `NameError`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_relay_status.py`
Expected: `Ran 2 tests ... OK`

再跑联系人回归：

Run: `python tests/test_contacts_access.py`
Expected: `OK`

- [ ] **Step 5: 提交**

```bash
git add bridge/web.py tests/test_relay_status.py
git commit -m "中继（5/8）：启动时接上中继，加只读状态接口"
```

---

### Task 6: 访客页识别 `/j/<room>` 前缀

**Files:**
- Modify: `static/join.html`
- Test: `tests/test_join_page.py`

**Interfaces:**
- Consumes: 中继在 `/j/<room>` 提供同一份 `join.html`。
- Produces: 页面在 `location.pathname` 以 `/j/` 开头时，所有 API 请求自动加前缀；本机 `/join` 保持不变。断线时保留登录和当前会话，只提示重试。

- [ ] **Step 1: 写失败测试**

Create `tests/test_join_page.py`:

```python
"""访客页：中继路径前缀和断线重试的静态守卫。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class JoinPageTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "static", "join.html"), encoding="utf-8") as f:
            self.page = f.read()

    def test_api_base_prefix(self):
        self.assertIn("const API_BASE=location.pathname.startsWith('/j/')", self.page)
        self.assertIn("fetch(API_BASE+url", self.page)

    def test_poll_keeps_session_on_network_error(self):
        self.assertIn("连接中断，正在重试", self.page)
        self.assertNotIn("catch(e){if(gen===generation){clear();el('error')", self.page)

    def test_waiting_for_host_text(self):
        self.assertIn("等待主机授权", self.page)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_join_page.py`
Expected: 三个断言至少两个失败

- [ ] **Step 3: 改 `static/join.html`**

3a. 第 15 行改成：

```js
const API_BASE=location.pathname.startsWith('/j/')?location.pathname.replace(/\/+$/,''):'';
async function request(url,body){const r=await fetch(API_BASE+url,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const j=await r.json();if(!r.ok)throw Error(j.error||'请求失败');return j}
```

3b. `refresh()` 里的空列表文案改成：

```js
else if(!rid)el('status').textContent=j.rooms.length?'选择左侧会话开始交流':(API_BASE?'已连接，等待主机授权。':'还没有受邀的会话，请联系管理员。');
```

3c. `poll()` 的 catch 改成：

```js
catch(e){if(gen===generation){el('error').textContent=e.message;el('status').textContent='连接中断，正在重试…'}}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_join_page.py`
Expected: `Ran 3 tests ... OK`

- [ ] **Step 5: 提交**

```bash
git add static/join.html tests/test_join_page.py
git commit -m "中继（6/8）：访客页支持 /j/<房间> 前缀，断线不退登"
```

---

### Task 7: 「邀请设备」入口、二维码与授权

**Files:**
- Create: `static/relay.js`
- Create: `static/qrcode.min.js`
- Create: `static/qrcode.LICENSE.txt`
- Modify: `index.html`
- Modify: `static/connections.js`
- Test: `tests/test_ui_relay.py`

**Interfaces:**
- Consumes: `GET /api/relay/status`（Task 5）、现有 `/api/agents`、`/api/agents/add`、`/api/contacts/key`、`/api/r/<room>/invite`。
- Produces: 顶部按钮 `#bRelay`；全局 `window.CojamRelay.inviteBase()`；`connections.js` 的网页邀请优先使用中继地址。

- [ ] **Step 1: 写失败测试**

Create `tests/test_ui_relay.py`:

```python
"""界面入口和二维码资源的静态守卫。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class UiRelayTest(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_entry_point_and_scripts(self):
        page = self.read("index.html")
        self.assertIn('id="bRelay"', page)
        self.assertIn("/static/relay.js", page)
        self.assertIn("/static/qrcode.min.js", page)

    def test_relay_js_calls_the_right_endpoints(self):
        js = self.read("static", "relay.js")
        for needle in ("/api/relay/status", "/api/contacts/key", "/api/agents/add",
                       "new QRCode", "window.CojamRelay"):
            self.assertIn(needle, js)

    def test_qrcode_is_vendored(self):
        js = self.read("static", "qrcode.min.js")
        self.assertIn("QRCode", js)
        self.assertGreater(len(js), 15000)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", "qrcode.LICENSE.txt")))

    def test_connections_prefer_relay_link(self):
        js = self.read("static", "connections.js")
        self.assertIn("window.CojamRelay", js)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_ui_relay.py`
Expected: `AssertionError`（缺 `bRelay`、`relay.js`）

- [ ] **Step 3: 引入二维码库（vendored，MIT）**

```bash
curl -L --fail -o /tmp/qrcode.min.js https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js
sha256sum /tmp/qrcode.min.js
```

Expected: `c541ef06327885a8415bca8df6071e14189b4855336def4f36db54bde8484f36`

确认后把许可证头补到第一行再写入仓库：

```bash
{ printf '%s\n' '/*! qrcodejs | (c) davidshimjs | MIT License | https://github.com/davidshimjs/qrcodejs */'; cat /tmp/qrcode.min.js; } > static/qrcode.min.js
```

Create `static/qrcode.LICENSE.txt`:

```text
The MIT License (MIT)

Copyright (c) 2012 davidshimjs

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

- [ ] **Step 4: 写 `static/relay.js`**

```javascript
/* 中继扫码邀请：生成邀请链接和二维码，一键把设备加进当前会议。 */
(function(){
  const CONTACT='scan-guest';
  let state={enabled:false}, token='', timer=null, shownBase='';

  const style=document.createElement('style');
  style.textContent='.relay-qr{display:flex;justify-content:center;padding:14px;background:#fff;border-radius:12px;margin:12px 0}.relay-qr canvas,.relay-qr img{display:block}#relayLink{width:100%;font-family:ui-monospace,monospace;font-size:12px}';
  document.head.appendChild(style);

  function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
  async function fetchStatus(){try{const r=await fetch('/api/relay/status',{cache:'no-store'});return await r.json()}catch(e){return {enabled:false,error:'无法连接后台'}}}
  function inviteUrl(){return state.invite_base&&token?state.invite_base+'#key='+encodeURIComponent(token):''}

  async function ensureContact(){
    const cat=await (await fetch('/api/agents',{cache:'no-store'})).json();
    if((cat.agents||[]).some(a=>a.id===CONTACT))return;
    const [ok,j]=await post('/api/agents/add',{spec:{id:CONTACT,label:'扫码访客',kind:'user',driver:'external',enabled:true}});
    if(!ok)throw Error(j.error||'无法创建扫码联系人');
  }

  function render(){
    const body=document.getElementById('relayBody');if(!body)return;
    shownBase=state.invite_base||'';
    if(!state.enabled){
      body.innerHTML='<p class="hint">这台设备还没有连接中继，扫码加入暂时不可用。</p>'
        +'<p class="hint">本机自用照常。要在别的网络扫码进入，启动合拍前设置环境变量 <code>COJAM_RELAY</code>，例如 <code>COJAM_RELAY=https://relay.example.com</code>。</p>';
      return;
    }
    let html='<p class="hint" id="relayNet">'+(state.online?'中继已连接。':'中继暂时断开，正在重试；恢复后才能扫码。')+'</p>';
    if(!token){
      html+='<button type="button" id="relayMake" class="pri"'+(state.online?'':' disabled')+'>生成邀请二维码</button>'
        +'<p class="hint">每次生成一个席位。对方扫码后只能看，发言和其他权限要在这里放开。</p>';
    }else{
      html+='<div class="relay-qr" id="relayQr"></div>'
        +'<input id="relayLink" readonly value="'+esc(inviteUrl())+'" aria-label="邀请链接">'
        +'<div class="btns"><button type="button" id="relayCopy">复制链接</button><button type="button" id="relayAgain">换个邀请</button></div>'
        +'<p class="hint">链接含这把钥匙，只发给要加入的设备；换个邀请会让旧二维码立即失效。</p>';
      if(typeof RID!=='undefined'&&RID)html+='<button type="button" id="relayAllow" class="pri">允许它查看「'+esc((typeof S!=='undefined'&&S&&(S.title||S.id))||'当前会话')+'」</button>';
      html+='<p class="hint" id="relayState">'+(guestOnline()?'设备已连接，可以允许加入了。':'等待设备扫码…')+'</p>';
      if(typeof RID!=='undefined'&&RID)html+='<p class="hint">要允许发言或干活，去「成员与权限」里勾选；扫码设备默认只读。</p>';
    }
    body.innerHTML=html;
    const make=document.getElementById('relayMake');
    if(make)make.onclick=generate;
    if(token){
      const box=document.getElementById('relayQr');
      box.replaceChildren();
      new QRCode(box,{text:inviteUrl(),width:220,height:220,correctLevel:QRCode.CorrectLevel.M});
      document.getElementById('relayCopy').onclick=async e=>{await copyText(inviteUrl());e.target.textContent='已复制'};
      document.getElementById('relayAgain').onclick=generate;
      const allow=document.getElementById('relayAllow');
      if(allow)allow.onclick=allowRoom;
    }
  }

  function guestOnline(){
    return (state.guests||[]).some(g=>g.contact===CONTACT);
  }

  async function generate(){
    const button=document.getElementById('relayMake');
    if(button)button.disabled=true;
    try{
      await ensureContact();
      const [ok,j]=await post('/api/contacts/key',{id:CONTACT});
      if(!ok)throw Error(j.error||'生成失败');
      token=j.token;
      state=await fetchStatus();
      render();
    }catch(e){alert(e.message);render()}
  }

  async function allowRoom(){
    if(typeof RID==='undefined'||!RID)return;
    const permissions={read:true,post:false,trigger:false,board:false,files:false,work:false};
    const [ok,j]=await post('/api/r/'+encodeURIComponent(RID)+'/invite',{contact:CONTACT,permissions});
    if(!ok){alert(j.error||'加入失败');return}
    const allow=document.getElementById('relayAllow');
    if(allow){allow.disabled=true;allow.textContent='已允许（只读）'}
    const line=document.getElementById('relayState');
    if(line)line.textContent='已加入当前会话。要允许发言或干活，去「成员与权限」里勾选。';
  }

  async function tick(){
    if(!document.getElementById('relayBody')){clearInterval(timer);timer=null;return}
    state=await fetchStatus();
    const net=document.getElementById('relayNet');
    if(net)net.textContent=state.online?'中继已连接。':'中继暂时断开，正在重试；恢复后才能扫码。';
    const line=document.getElementById('relayState');
    if(line)line.textContent=guestOnline()?'设备已连接，可以允许加入了。':'等待设备扫码…';
    if(!token)render();
    else if(state.invite_base&&state.invite_base!==shownBase)render();  // 中继重启换了房间号，二维码要跟着换
  }

  async function open(){
    state=await fetchStatus();
    token='';
    showModal('<h3>邀请设备扫码进入</h3><div id="relayBody"></div>'
      +'<div class="btns"><button type="button" id="relayClose">关闭</button></div>');
    document.getElementById('relayClose').onclick=hideModal;
    render();
    if(timer)clearInterval(timer);
    timer=setInterval(tick,2000);
  }

  window.CojamRelay={inviteBase:()=>state.invite_base||''};
  const button=document.getElementById('bRelay');
  if(button)button.onclick=open;
})();
```

- [ ] **Step 5: 改 `index.html` 和 `static/connections.js`**

5a. `index.html` 顶栏：把

```html
<button type="button" id="bAgents">成员与权限</button>
```

改成

```html
<button type="button" id="bAgents">成员与权限</button><button type="button" id="bRelay">邀请设备</button>
```

5b. `index.html` 脚本区，在

```html
<script src="/static/connections.js"></script>
```

后面加：

```html
<script src="/static/qrcode.min.js"></script>
<script src="/static/relay.js"></script>
```

5c. `static/connections.js` 里把

```js
function connectionInvitation(w) { return w.base + '/join#key=' + encodeURIComponent(w.token); }
```

改成：

```js
function connectionInvitation(w) { const relay = window.CojamRelay && window.CojamRelay.inviteBase(); return (relay || w.base + '/join') + '#key=' + encodeURIComponent(w.token); }
```

- [ ] **Step 6: 跑测试并做浏览器手测**

Run: `python tests/test_ui_relay.py`
Expected: `Ran 4 tests ... OK`

手测（本机两个终端）：

```bash
python -m relay.main --host 127.0.0.1 --port 8787
COJAM_RELAY=http://127.0.0.1:8787 ROUNDTABLE_PORT=8765 python -m bridge
```

在桌面合拍里点「邀请设备」→ 生成二维码。本机联调时用另一个浏览器窗口（或隐私窗口）打开二维码里的链接，确认出现「已连接，等待主机授权」→ 点「允许它查看当前会话」→ 5 秒内出现会议列表。真手机扫码要等中继用 https 部署好（见 `docs/RELAY.md`）：主机适配器只允许回环地址用 `http://`，邀请链接里的域名必须能被手机访问。

- [ ] **Step 7: 提交**

```bash
git add static/relay.js static/qrcode.min.js static/qrcode.LICENSE.txt index.html static/connections.js tests/test_ui_relay.py
git commit -m "中继（7/8）：邀请设备入口、二维码、默认只读授权"
```

---

### Task 8: 全链路测试与部署文档

**Files:**
- Create: `tests/test_relay_e2e.py`
- Create: `docs/RELAY.md`

**Interfaces:**
- Consumes: 全部前置任务。
- Produces: 一条自动化的端到端验证；一份能照着上线中文文档。

- [ ] **Step 1: 写端到端测试**

Create `tests/test_relay_e2e.py`:

```python
"""全链路：真中继 + 真主机 + 访客凭据。不调用真实 AI。"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

DATA = tempfile.mkdtemp(prefix="bridge_relay_e2e_")
os.environ["ROUNDTABLE_LOG_DIR"] = DATA
os.environ["ROUNDTABLE_NO_PRESETS"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bridge import access, agents, registry, room as R, web
from bridge import relay as host_relay
from relay import server as relay_server

agents.prewarm = lambda *a: None
registry.load()
STATIC = os.path.join(ROOT, "static")


def call(base, path, obj=None, token=None, headers=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    h.update(headers or {})
    data = json.dumps(obj).encode() if obj is not None else None
    req = urllib.request.Request(base + path, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wait_for(fn, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


class RelayE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        relay_server.PULL_WAIT = 0.5
        cls.web = web.ExclusiveServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=cls.web.serve_forever, daemon=True).start()
        cls.web_port = cls.web.server_address[1]
        cls.web_base = "http://127.0.0.1:%d" % cls.web_port

        registry.save_agent({"id": "phone", "label": "手机", "kind": "user",
                             "driver": "external", "enabled": True})
        cls.key = access.credential("phone")
        cls.room = R.rooms.create("扫码测试", "验证扫码接入", DATA, participants=["phone"])
        cls.room.set_members(["phone"], {"phone": {"read": True, "post": False, "trigger": False,
                                                   "board": False, "files": False, "work": False}})

        cls.relay = relay_server.RelayServer(("127.0.0.1", 0), STATIC)
        threading.Thread(target=cls.relay.serve_forever, daemon=True).start()
        cls.relay_base = "http://127.0.0.1:%d" % cls.relay.server_address[1]

        cls.client = host_relay.RelayClient(cls.relay_base, cls.web_port, "e2e").start()
        assert wait_for(lambda: cls.client.room_id and cls.client.online), "中继没有连上"

    @classmethod
    def tearDownClass(cls):
        cls.client.stop()
        for srv in (cls.relay, cls.web):
            srv.shutdown()
            srv.server_close()

    def guest(self, path, obj=None, headers=None):
        return call(self.relay_base + "/j/" + self.client.room_id + path, obj,
                    token=self.key, headers=headers)

    def test_page_and_static(self):
        status, raw = call(self.relay_base, "/j/" + self.client.room_id)
        self.assertEqual(status, 200)
        self.assertIn("API_BASE", raw.decode("utf-8"))
        status, raw = call(self.relay_base, "/static/guest.css")
        self.assertEqual(status, 200)

    def test_rooms_and_messages(self):
        status, raw = self.guest("/api/external/rooms")
        self.assertEqual(status, 200)
        self.assertIn(self.room.id, [x["id"] for x in json.loads(raw)["rooms"]])
        status, raw = self.guest("/api/external/messages?room=" + self.room.id + "&since=0")
        self.assertEqual(status, 200)
        self.assertIn("messages", json.loads(raw))

    def test_hello_marks_guest_online(self):
        status, raw = self.guest("/api/external/hello", {"client": "AI Bridge 网页"})
        self.assertEqual(status, 200)
        self.assertTrue(wait_for(lambda: self.client.status()["guests"]))

    def test_read_only_then_grant_post(self):
        status, _ = self.guest("/api/post", {"room": self.room.id, "text": "hi"})
        self.assertEqual(status, 403)
        self.room.set_members(["phone"], {"phone": {"post": True}})
        status, _ = self.guest("/api/post", {"room": self.room.id, "text": "hi"})
        self.assertEqual(status, 200)

    def test_admin_paths_stay_blocked_even_with_owner_header(self):
        for path in ("/api/rooms", "/api/agents", "/mcp", "/files/x"):
            status, _ = self.guest(path, headers={"X-RT-Token": web.RT_TOKEN, "Cookie": "rt_owner=" + web.RT_TOKEN})
            self.assertEqual(status, 404, path)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认先失败再通过**

如果是按任务顺序做的，这一步应直接通过。先确认它确实在测试真实链路（把 `RelayClient` 的 URL 临时改错会失败），然后：

Run: `python tests/test_relay_e2e.py`
Expected: `Ran 5 tests ... OK`（几秒内完成）

- [ ] **Step 3: 写部署文档**

Create `docs/RELAY.md`:

```markdown
# 中继部署与运维

中继让访客在任意网络用浏览器进入主机上的合拍，不开放主机端口。中继只转发四条白名单接口，不存会议内容。

## 本机联调

```bash
# 终端 1：中继
python -m relay.main --host 127.0.0.1 --port 8787

# 终端 2：合拍主机
COJAM_RELAY=http://127.0.0.1:8787 python -m bridge
```

Windows PowerShell：

```powershell
$env:COJAM_RELAY = "http://127.0.0.1:8787"
python -m bridge
```

`http://` 只允许回环地址，远程中继必须是 `https://`。可选 `COJAM_RELAY_NAME` 设置主机显示名。

本机联调用另一个浏览器窗口打开邀请链接即可；手机会扫到的地址必须是它网络能访问的 `https://` 域名，所以真机扫码要先把中继部署到正式域名（见下文）。

正式发行时把域名写进 `bridge/relay.py` 的 `DEFAULT_RELAY`，用户不用设置任何环境变量；`COJAM_RELAY` 仍然优先，方便临时切换。

## 正式部署（一台 1C1G VPS）

1. 域名解析到服务器，备案按境内要求办理。
2. 安装 Python 3.11+，把仓库放到 `/opt/cojam`。
3. 中继只监听回环，由 Caddy 终止 TLS：

```
relay.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

4. systemd：

```ini
[Unit]
Description=Cojam relay
After=network.target

[Service]
WorkingDirectory=/opt/cojam
ExecStart=/usr/bin/python3 -m relay.main --host 127.0.0.1 --port 8787
Restart=always
RestartSec=3
User=cojam

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now cojam-relay
```

5. 防火墙只放 80/443；8787 不要对公网开放。中继只在回环地址上信任 `X-Forwarded-For`（Caddy 会自动带上），所以限流和「单房间 5 台设备」按真实来源 IP 计算。

## 主机端

打包版和源码版都在启动前设置环境变量：

```bash
COJAM_RELAY=https://relay.example.com python -m bridge
```

界面顶部「邀请设备」生成二维码。主机停止运行 60 秒后房间作废，旧二维码失效；重启会生成新房间号。

## 限额

- 单房间排队 50 条，10 秒 30 次请求
- 单房间最多 5 个不同来源 IP（10 分钟窗口）
- 单 IP 每分钟 120 次访客请求
- 单 IP 每小时 20 次注册
- 单进程 500 个房间；房间 60 秒无主机轮询即清理
- 上量后再按监控调大：在线房间数、每房间请求速率、出站带宽、被限流 IP 数

## 安全边界

- 中继只接受四对「方法 + 路径」，其余 404，不排队
- 中继只转发 `Authorization` 和 `Content-Type`；不转发 `Cookie`、`X-RT-Token`、`X-Forwarded-*`
- 主机适配器再校验一次白名单，并自行构造请求头；中继被攻破也拿不到管理员能力
- 中继日志不记录 `Authorization`、请求体和响应体
- 邀请码在 URL fragment，浏览器不会把它发给中继
- 主机仍然只监听 `127.0.0.1`

## 排障

- 合拍顶部「邀请设备」显示「这台设备还没有连接中继」：启动时没读到 `COJAM_RELAY`，或地址不是 https
- 一直「中继暂时断开」：看 `journalctl -u cojam-relay`；主机侧错误在 `bridge.relay.RelayClient.status()["error"]`
- 二维码能扫开但一直等待授权：主机上点「邀请设备」→「允许它查看当前会话」，或去「成员与权限」勾选
- 中继重启后旧二维码失效：主机自动重新注册新房间，重新生成邀请即可
```

- [ ] **Step 4: 跑完整测试套件**

Run: `python tests/run_all.py`
Expected: 所有模块 `PASS`，最后一行形如 `N tests; 0 failing modules`

- [ ] **Step 5: 提交**

```bash
git add tests/test_relay_e2e.py docs/RELAY.md
git commit -m "中继（8/8）：全链路测试与部署文档"
```

---

## Self-Review

**Spec coverage**

| 规格要求 | 对应任务 |
|---|---|
| 主机自动注册房间、显示二维码 | 2、4、5、7 |
| 访客任意网络用浏览器加入 | 3、6、8 |
| 主机授权后才看到会议 | 7（默认只读 + 允许加入当前会话）、8 |
| 中继不可用不影响本机 | 5（未配置即不启动）、4（退避重试）、8（本机回环） |
| 四条路径白名单、两侧强制 | 3（中继）、4（主机）、8（组合验证） |
| 管理能力不可达、请求头剥离 | 3、4、8 |
| 邀请码不经过中继存储 | 3（只收 Authorization 与正文，不记录）、7（fragment） |
| 房间 60 秒作废、房间隔离 | 1、2、8 |
| 限设备（默认 5）、单房间/单 IP 限速 | 1、3、8 |
| 默认最小权限、现有「成员与权限」授权 | 7、8 |
| 零依赖 | 全局约束；仅 vendored 浏览器端二维码 |
| 部署与成本、监控指标 | 8 |
| 局域网兜底 | 按规格明确不在 v1；本计划不涉及 |

**Placeholder scan:** 无 TBD/TODO；每个代码步骤都给了完整实现；测试都给了断言。

**Type consistency:** `RelayClient(base, local_port, name="")`、`status()` 字段、`local_request(job, port)` 的三元返回、`RelayServer(address, static_dir)` 在任务 2/3/4/5/8 中一致；`invite_base` 在中继状态、`window.CojamRelay.inviteBase()` 和 `connectionInvitation` 中同名同义。
