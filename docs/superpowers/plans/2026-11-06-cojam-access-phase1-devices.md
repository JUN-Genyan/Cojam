# Cojam 接入整合（第 1 期：自有设备凭证）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让屋主用自己的其他设备通过局域网、Tailscale 或自定义地址打开 Cojam 的完整界面，并以「我」的身份参与会话；凭证长期有效、可撤销；同时修掉远程访问被当成游客的回归。

**Architecture:** 新增本机账户与设备凭证（`data/account.json`、`data/devices.json`、`data/device_codes.json`）。`GET /` 改为按凭证分流：带有效 `cojam_device` cookie 或本机请求给完整界面，其余给游客页。管理 API 仍只认 `X-RT-Token`，但 `_from_browser()` 同时接受未撤销的设备凭证。新增 `GET /enroll` 一次性接入页和 Tailscale IP 监听（默认 8767）。设备页不使用 localStorage，凭证只放在 HttpOnly cookie 里，由 `GET /` 注入 `meta[name=rt-token]`。

**Tech Stack:** Python 3.11+ 标准库、`ThreadingHTTPServer`、`unittest`、原生 HTML/CSS/JS、现有 `connections.js` 向导、`qrcode.min.js`。

**Spec:** `docs/superpowers/specs/2026-11-06-cojam-access-integration-design.md`（第 5、6、11、12、13、14、15 节中属于第 1 期的部分）

## Global Constraints

- 核心 `Cojam.exe` 保持零第三方依赖；不新增 `pyproject.toml` 运行时依赖。
- 所有持久化文件放在 `LOG_DIR`，用 `common.read_json` / `common.write_json` 原子写入，模块级 `threading.RLock` 保护。
- 设备凭证与接入码只在签发时返回明文，磁盘只存 SHA-256；日志不得输出明文凭证、接入码或联系人密钥。
- 管理接口只认请求头 `X-RT-Token`；`cojam_device` cookie 只用于 `GET /` 分流和只读资源（`/files/`、`static/member-connector.zip`），不能让仅带 cookie 的请求调用管理 API。
- 设备 cookie 属性固定为 `HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000`；HTTPS（`X-Forwarded-Proto: https`）时追加 `Secure`。
- 不内置 Tailscale、不代管隧道、不建中继、不做 WebRTC。
- 不改 MCP 服务器名 `ai-bridge`，不改现有联系人字段和 `data/agents.d` 格式。
- 版本统一升到 `2.6.0`，`API_REVISION = 2`；`static/connections.js` 版本守卫、`static/sw.js` 缓存名、`pyproject.toml` 同步。
- 本仓库当前规则：只有用户明确要求时才提交。本计划中的「提交」步骤一律先停在第 5 步，等用户确认后再执行。
- 每个测试文件可单独运行：`python tests/test_xxx.py`；全量用 `python tests/run_all.py`。

---

## 文件结构

- 新建 `bridge/devices.py`：本机账户、一次性接入码、设备凭证、撤销、在线时间。只依赖 `common`，不依赖 `web`。
- 新建 `static/enroll.html`：一次性接入页；读 `#code=`，填设备名，`POST /api/devices/enroll`，跳 `/`。
- 修改 `bridge/common.py`：新增 `tailscale_ipv4()`；保留 `pick_lan_ip` 对 `100.64.0.0/10` 的排除。
- 修改 `bridge/web.py`：凭证分流、公开接入路由、设备管理 API、Tailscale 监听、生命周期分流。
- 修改 `static/connections.js`：向导增加「我的设备」，管理设备列表、生成接入码、二维码、撤销。
- 修改 `bridge/version.py`、`pyproject.toml`、`static/sw.js`、`tests/test_frontend_runtime.py`。
- 新建 `tests/test_devices.py`；扩展 `tests/test_lan.py`。

---

### Task 1: 版本号与前端守卫对齐

**Files:**
- Modify: `bridge/version.py`
- Modify: `pyproject.toml`
- Modify: `static/sw.js`
- Modify: `static/connections.js:10-11`
- Test: `tests/test_frontend_runtime.py:193-224`

**Interfaces:**
- Produces: `bridge.version.VERSION == "2.6.0"`、`bridge.version.API_REVISION == 2`；`checkConnectionBackend()` 只接受这一组值。

- [ ] **Step 1: 先改测试，让它期待 2.6.0 / 2**

在 `tests/test_frontend_runtime.py` 的 `BrowserErrors.test_legacy_backend_and_http_errors_are_actionable` 中，把 `api_revision:1` 改成从 `bridge/version.py` 读取，避免以后再手工改：

```python
        const version = fs.readFileSync('pyproject.toml','utf8').match(/^version = "([^"]+)"/m)[1];
        const revision = Number(fs.readFileSync('bridge/version.py','utf8').match(/^API_REVISION = (\d+)/m)[1]);
        context.fetch = async () => ({ok:true,json:async()=>({app:'AI Bridge',version,api_revision:revision})});
        await context.checkConnectionBackend();
        context.fetch = async () => ({ok:true,json:async()=>({app:'AI Bridge',version,api_revision:revision+1})});
        await assert.rejects(context.checkConnectionBackend(), /版本不一致/);
```

把前一行旧的 `api_revision:1` 的「old version」用例保留：

```python
 context.fetch = async () => ({ok:true,json:async()=>({app:'AI Bridge',version:'old',api_revision:revision})});
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python tests/test_frontend_runtime.py`
Expected: FAIL，`checkConnectionBackend` 仍然接受旧版本号，或守卫报「版本不一致」。

- [ ] **Step 3: 升版本并改守卫**

`bridge/version.py`：

```python
VERSION = "2.6.0"
API_REVISION = 2
```

`pyproject.toml`：

```toml
version = "2.6.0"
```

`static/sw.js`：

```js
const V = 'ai-bridge-2.6.0', OFF = '/static/offline.html';
```

`static/connections.js`：

```js
  if (info.app !== 'AI Bridge' || info.version !== '2.6.0' || info.api_revision !== 2)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python tests/test_frontend_runtime.py`
Expected: PASS（所有用例）。

Run: `python tests/test_server_runtime.py`
Expected: PASS。

- [ ] **Step 5: 暂缓提交**

Run: `git status --short`
Expected: 只看到上述文件。等用户明确要求后再提交，例如：

```bash
git add bridge/version.py pyproject.toml static/sw.js static/connections.js tests/test_frontend_runtime.py
git commit -m "chore: bump Cojam to 2.6.0 and API revision 2"
```

---

### Task 2: Tailscale 地址检测

**Files:**
- Modify: `bridge/common.py`（在 `NO_WINDOW` 之后新增函数）
- Test: `tests/test_lan.py`

**Interfaces:**
- Produces: `common.tailscale_ipv4() -> str | None`。只返回 `100.64.0.0/10` 内的第一个 IPv4；命令不存在、超时、输出为空都返回 `None`，不抛异常。

- [ ] **Step 1: 写失败测试**

在 `tests/test_lan.py` 的 `LanAddress` 类里追加：

```python
    def test_tailscale_address_is_detected_separately(self):
        from unittest.mock import patch
        import subprocess
        completed = subprocess.CompletedProcess([], 0, stdout='100.64.0.5\nfd7a:115c:a1e0::1\n', stderr='')
        with patch('shutil.which', return_value=r'C:\Program Files\Tailscale\tailscale.exe'), \
             patch.object(common.subprocess, 'run', return_value=completed):
            self.assertEqual(common.tailscale_ipv4(), '100.64.0.5')

    def test_tailscale_address_is_none_when_unavailable(self):
        from unittest.mock import patch
        import subprocess
        with patch('shutil.which', return_value=None), \
             patch.object(common.os, 'name', 'nt'), \
             patch.object(common.os.path, 'isfile', return_value=False):
            self.assertIsNone(common.tailscale_ipv4())
        with patch('shutil.which', return_value='/usr/bin/tailscale'), \
             patch.object(common.subprocess, 'run', side_effect=OSError):
            self.assertIsNone(common.tailscale_ipv4())
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python tests/test_lan.py`
Expected: FAIL，`AttributeError: module 'bridge.common' has no attribute 'tailscale_ipv4'`。

- [ ] **Step 3: 实现**

在 `bridge/common.py` 的 `NO_WINDOW` 定义之后追加：

```python
# ===== Tailscale =====
_TAILSCALE_NET = ipaddress.IPv4Network("100.64.0.0/10")


def _tailscale_ip(candidates):
    for ip in candidates:
        try:
            addr = ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        if addr in _TAILSCALE_NET:
            return str(addr)
    return None


def tailscale_ipv4():
    """这台电脑的 Tailscale IPv4；没装、没登录或命令失败都返回 None。"""
    import shutil
    exe = shutil.which("tailscale")
    if not exe and os.name == "nt":
        candidate = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tailscale", "tailscale.exe")
        if os.path.isfile(candidate):
            exe = candidate
    if not exe:
        return None
    try:
        result = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=2, **NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return _tailscale_ip((result.stdout or "").split())
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python tests/test_lan.py`
Expected: PASS。

- [ ] **Step 5: 暂缓提交**

Run: `git status --short`，等用户确认后再提交。

---

### Task 3: 设备凭证模块

**Files:**
- Create: `bridge/devices.py`
- Test: `tests/test_devices.py`（本任务先写模块级测试；Task 4 再追加 HTTP 测试）

**Interfaces:**
- Produces:
  - `devices.account() -> {"id": "acct_...", "label": "...", "created": int}`，首次调用自动生成并写 `data/account.json`。
  - `devices.create_code(label="新设备", ttl=600) -> {"id": "code_...", "token": "code_<24hex>.<secret>", "expires": int, "label": str}`。
  - `devices.enroll(token, label="") -> {"token": "dev_...", "device": public}`；无效 401、已用/过期 410；抛出 `devices.DeviceError(code, message)`。
  - `devices.verify(token) -> public | None`；只接受未撤销设备；成功时最多每 30 秒更新一次 `last_seen`。
  - `devices.listing(current="") -> [{"id","label","created","last_seen","online","current"}]`，不含摘要。
  - `devices.revoke(id) -> public`；不存在抛 `DeviceError(404, ...)`。
- `public(row)` 只返回 `id`、`label`、`created`、`last_seen`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_devices.py`：

```python
"""Own-device account, one-time codes, revocable credentials and routing."""
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

DATA = tempfile.mkdtemp(prefix="cojam_devices_")
os.environ["ROUNDTABLE_LOG_DIR"] = DATA
os.environ["ROUNDTABLE_NO_PRESETS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bridge import common, devices, registry, web

registry.load()


class DeviceCredentials(unittest.TestCase):
    def setUp(self):
        for name in ("account.json", "devices.json", "device_codes.json"):
            try:
                os.remove(os.path.join(DATA, name))
            except FileNotFoundError:
                pass

    def test_account_is_created_once(self):
        first = devices.account()
        second = devices.account()
        self.assertTrue(first["id"].startswith("acct_"))
        self.assertEqual(first, second)
        saved = json.loads(Path(DATA, "account.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["id"], first["id"])

    def test_code_is_one_time_and_creates_a_device(self):
        code = devices.create_code("我的手机")
        self.assertRegex(code["token"], r"^code_[0-9a-f]{24}\.")
        enrolled = devices.enroll(code["token"], "我的手机")
        self.assertTrue(enrolled["token"].startswith("dev_"))
        self.assertEqual(enrolled["device"]["label"], "我的手机")
        self.assertIsNotNone(devices.verify(enrolled["token"]))
        with self.assertRaises(devices.DeviceError) as caught:
            devices.enroll(code["token"], "再来一次")
        self.assertEqual(caught.exception.code, 410)
        saved = json.loads(Path(DATA, "devices.json").read_text(encoding="utf-8"))
        self.assertNotIn(enrolled["token"], json.dumps(saved))
        self.assertEqual(len(saved["devices"]), 1)

    def test_unknown_and_expired_codes(self):
        with self.assertRaises(devices.DeviceError) as caught:
            devices.enroll("code_ffffffffffffffffffffffff.nope", "x")
        self.assertEqual(caught.exception.code, 401)
        code = devices.create_code("旧手机")
        path = Path(DATA, "device_codes.json")
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["codes"][0]["expires"] = int(time.time()) - 1
        path.write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaises(devices.DeviceError) as caught:
            devices.enroll(code["token"], "旧手机")
        self.assertEqual(caught.exception.code, 410)

    def test_revoke_invalidates_the_token(self):
        code = devices.create_code("平板")
        enrolled = devices.enroll(code["token"], "平板")
        devices.revoke(enrolled["device"]["id"])
        self.assertIsNone(devices.verify(enrolled["token"]))
        self.assertEqual(devices.listing(), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python tests/test_devices.py`
Expected: FAIL，`ModuleNotFoundError: No module named 'bridge.devices'`。

- [ ] **Step 3: 实现 `bridge/devices.py`**

```python
"""本机账户与自有设备凭证。磁盘只存 SHA-256，明文只在签发时返回。"""
import hashlib
import os
import secrets
import threading
import time

from .common import LOG_DIR, read_json, write_json

LOCK = threading.RLock()
CODE_TTL = 600
ONLINE_WRITE_INTERVAL = 30


class DeviceError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _path(name):
    return os.path.join(LOG_DIR, name)


def _save(name, data):
    os.makedirs(LOG_DIR, exist_ok=True)
    write_json(_path(name), data)


def _digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _clean(value, fallback, limit=40):
    text = "".join(ch for ch in str(value or "") if ch >= " " and ch != "\x7f").strip()
    return (text or fallback)[:limit]


def _rows(name, key):
    data = read_json(_path(name)) or {}
    rows = data.get(key) if isinstance(data, dict) else []
    return [row for row in rows if isinstance(row, dict) and row.get("id")] if isinstance(rows, list) else []


def _devices():
    return _rows("devices.json", "devices")


def _codes():
    return _rows("device_codes.json", "codes")


def public(row):
    return {"id": str(row.get("id") or ""), "label": str(row.get("label") or "我的设备"),
            "created": int(row.get("created") or 0), "last_seen": int(row.get("last_seen") or 0)}


def account():
    with LOCK:
        data = read_json(_path("account.json"))
        if isinstance(data, dict) and data.get("id"):
            return {"id": str(data["id"]), "label": str(data.get("label") or "我的 Cojam"),
                    "created": int(data.get("created") or 0)}
        row = {"id": "acct_" + secrets.token_hex(12), "label": "我的 Cojam", "created": int(time.time())}
        _save("account.json", row)
        return dict(row)


def create_code(label="新设备", ttl=CODE_TTL):
    with LOCK:
        now = time.time()
        rows = [row for row in _codes() if not row.get("used") and float(row.get("expires") or 0) > now]
        record_id = "code_" + secrets.token_hex(12)
        secret = secrets.token_urlsafe(24)
        expires = int(now + max(60, int(ttl)))
        clean_label = _clean(label, "新设备")
        rows.append({"id": record_id, "hash": _digest(secret), "label": clean_label,
                     "created": int(now), "expires": expires, "used": False})
        _save("device_codes.json", {"codes": rows})
        return {"id": record_id, "token": record_id + "." + secret, "expires": expires, "label": clean_label}


def enroll(token, label=""):
    record_id, _, secret = str(token or "").partition(".")
    with LOCK:
        rows = _codes()
        record = next((row for row in rows if row.get("id") == record_id), None)
        if record is None or not secret:
            raise DeviceError(401, "接入码无效，请让主机重新生成")
        if record.get("used"):
            raise DeviceError(410, "接入码已经用过，请让主机重新生成")
        if float(record.get("expires") or 0) < time.time():
            raise DeviceError(410, "接入码已过期，请让主机重新生成")
        if not secrets.compare_digest(str(record.get("hash") or ""), _digest(secret)):
            raise DeviceError(401, "接入码无效，请让主机重新生成")
        device_token = "dev_" + secrets.token_urlsafe(32)
        now = int(time.time())
        row = {"id": "dev_" + secrets.token_hex(12), "label": _clean(label or record.get("label"), "我的设备"),
               "secret_hash": _digest(device_token), "account_id": account()["id"],
               "created": now, "last_seen": now, "revoked": False}
        current = _devices()
        current.append(row)
        _save("devices.json", {"devices": current})
        record["used"] = True
        _save("device_codes.json", {"codes": rows})
        return {"token": device_token, "device": public(row)}


def verify(token):
    text = str(token or "")
    if not text.startswith("dev_"):
        return None
    digest = _digest(text)
    with LOCK:
        rows = _devices()
        row = None
        for item in rows:
            saved = str(item.get("secret_hash") or "")
            if saved and not item.get("revoked") and secrets.compare_digest(saved, digest):
                row = item
                break
        if row is None:
            return None
        now = int(time.time())
        if now - int(row.get("last_seen") or 0) >= ONLINE_WRITE_INTERVAL:
            row["last_seen"] = now
            _save("devices.json", {"devices": rows})
        return public(row)


def listing(current=""):
    with LOCK:
        rows = [row for row in _devices() if not row.get("revoked")]
        now = time.time()
        out = []
        for row in sorted(rows, key=lambda item: item.get("created") or 0, reverse=True):
            item = public(row)
            item["online"] = now - float(row.get("last_seen") or 0) <= 90
            item["current"] = str(row.get("id")) == str(current or "")
            out.append(item)
        return out


def revoke(device_id):
    with LOCK:
        rows = _devices()
        row = next((item for item in rows if item.get("id") == device_id), None)
        if row is None:
            raise DeviceError(404, "没有这台设备")
        row["revoked"] = True
        _save("devices.json", {"devices": rows})
        return public(row)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python tests/test_devices.py`
Expected: PASS（4 个用例）。

- [ ] **Step 5: 暂缓提交**

Run: `git status --short`，等用户确认后再提交。

---

### Task 4: 凭证分流、设备 API 与 Tailscale 监听

**Files:**
- Modify: `bridge/web.py`
- Test: `tests/test_devices.py`（追加 HTTP 测试）
- Test: `tests/test_lan.py`

**Interfaces:**
- Consumes: `devices.account/create_code/enroll/verify/listing/revoke`、`common.tailscale_ipv4`。
- Produces:
  - `GET /api/server/info` 增加 `tailscale_url` 与 `owner_devices_supported: true`。
  - `GET /enroll` 返回 `static/enroll.html`（Task 5 创建文件；本任务先只加路由）。
  - `POST /api/devices/enroll`（公开）返回 `{"ok":true,"token":"dev_...","device":{...}}` 并设置 `cojam_device` cookie。
  - `POST /api/devices/code`、`GET /api/devices`、`POST /api/devices/revoke`（屋主）。
  - `web.start_tailscale_listener(port=None) -> (server|None, url|None)`。
  - `GET /`：有效设备 cookie → 完整界面并注入设备凭证；本机无凭证 → 完整界面并注入 `RT_TOKEN`；其余 → `join.html`。

- [ ] **Step 1: 追加 HTTP 测试**

在 `tests/test_devices.py` 的 `registry.load()` 之后启动服务器：

```python
srv = web.ExclusiveServer(("127.0.0.1", 0), web.Handler)
srv.lan_url = None
srv.tailscale_url = None
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%s" % srv.server_address[1]


def call(path, obj=None, token=None, cookie=None, headers=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["X-RT-Token"] = token
    if cookie:
        h["Cookie"] = cookie
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode() if obj is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


def body_json(body):
    return json.loads(body.decode("utf-8"))


def page(headers=None):
    request = urllib.request.Request(BASE + "/", headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")


def injected_token(html):
    match = re.search(r'name="rt-token" content="([^"]+)"', html)
    return match.group(1) if match else ""
```

在 `DeviceCredentials` 之后追加：

```python
class DeviceHTTP(unittest.TestCase):
    def make_device(self, label="我的手机"):
        status, body, _ = call("/api/devices/code", {"label": label, "base": "http://192.168.1.9:8766"}, token=web.RT_TOKEN)
        self.assertEqual(status, 200)
        code = body_json(body)["code"]
        status, body, headers = call("/api/devices/enroll", {"code": code, "label": label})
        self.assertEqual(status, 200)
        return body_json(body)["token"], body_json(body)["device"], headers

    def test_code_and_device_routes(self):
        token, device, headers = self.make_device()
        self.assertIn("cojam_device=" + token, headers.get("Set-Cookie", ""))
        status, body, _ = call("/api/devices", token=token)
        self.assertEqual(status, 200)
        data = body_json(body)
        self.assertTrue(data["account"]["id"].startswith("acct_"))
        self.assertEqual([row["id"] for row in data["devices"]], [device["id"]])
        self.assertTrue(data["devices"][0]["current"])
        self.assertEqual(call("/api/rooms", token=token)[0], 200)

    def test_revoked_device_cannot_call_admin_api(self):
        token, device, _ = self.make_device("旧手机")
        self.assertEqual(call("/api/devices/revoke", {"id": device["id"]}, token=web.RT_TOKEN)[0], 200)
        self.assertEqual(call("/api/rooms", token=token)[0], 403)
        self.assertIsNone(devices.verify(token))

    def test_root_routing_uses_credentials(self):
        owner = page()
        self.assertEqual(injected_token(owner), web.RT_TOKEN)
        remote = page({"Host": "192.168.1.9"})
        self.assertIn("联系人接入密钥", remote)
        self.assertEqual(injected_token(remote), "")
        token, device, _ = self.make_device("手机")
        with_cookie = page({"Host": "192.168.1.9", "Cookie": "cojam_device=" + token})
        self.assertEqual(injected_token(with_cookie), token)
        forwarded = page({"Host": "192.168.1.9", "X-Forwarded-For": "1.2.3.4",
                          "Cookie": "cojam_device=" + token})
        self.assertEqual(injected_token(forwarded), token)
        revoked = call("/api/devices/revoke", {"id": device["id"]}, token=web.RT_TOKEN)
        self.assertEqual(revoked[0], 200)
        after = page({"Host": "192.168.1.9", "Cookie": "cojam_device=" + token})
        self.assertIn("联系人接入密钥", after)

    def test_cookie_is_only_for_pages_and_files(self):
        token, _, _ = self.make_device("手机")
        self.assertEqual(call("/api/devices/code", {"label": "x"}, cookie="cojam_device=" + token)[0], 403)
        files = Path(DATA, "meetings", "room-1", "files")
        files.mkdir(parents=True, exist_ok=True)
        (files / "note.txt").write_text("hello", encoding="utf-8")
        status, body, _ = call("/files/room-1/files/note.txt", cookie="cojam_device=" + token)
        self.assertEqual((status, body), (200, b"hello"))

    def test_device_session_does_not_drive_owner_lifecycle(self):
        token, _, _ = self.make_device("手机")
        status, body, _ = call("/api/ui/session", {"tab": "t", "action": "open"}, token=token)
        self.assertEqual(status, 200)
        self.assertEqual(body_json(body), {"ok": True, "tracked": False})

    def test_info_reports_device_support(self):
        status, body, _ = call("/api/server/info")
        data = body_json(body)
        self.assertEqual(status, 200)
        self.assertTrue(data["owner_devices_supported"])
        self.assertIn("tailscale_url", data)
```

`test_lan.py` 追加 Tailscale 监听测试：

```python
class TailscaleListener(unittest.TestCase):
    def test_tailscale_listener_reports_its_own_url(self):
        with patch.object(common, 'tailscale_ipv4', return_value='127.0.0.1'):
            server, url = web.start_tailscale_listener(0)
        self.assertIsNotNone(server)
        try:
            self.assertEqual(url, 'http://127.0.0.1:%d' % server.server_address[1])
            self.assertEqual(server.tailscale_url, url)
            with urllib.request.urlopen(url + '/api/server/info', timeout=5) as response:
                self.assertEqual(__import__('json').loads(response.read())['tailscale_url'], url)
            request = urllib.request.Request(url + '/', headers={'Host': '100.64.0.5:8767'})
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertIn('联系人接入密钥', response.read().decode('utf-8'))
        finally:
            server.shutdown()
            server.server_close()

    def test_no_tailscale_address_means_no_listener(self):
        with patch.object(common, 'tailscale_ipv4', return_value=None):
            self.assertEqual(web.start_tailscale_listener(0), (None, None))
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python tests/test_devices.py`
Expected: FAIL，`/api/devices` 返回 403 或 404；`start_tailscale_listener` 不存在。

- [ ] **Step 3: 修改 `bridge/web.py`**

更新 import：

```python
from urllib.parse import parse_qs, quote, unquote, urlsplit
from . import access, agents, common, connections, devices, install, mcp, members, projects, registry, room as R
```

把 `_from_browser` 替换为：

```python
    def _from_browser(self):
        """管理员能力：进程内令牌，或请求头里的未撤销设备凭证。cookie 不在这里放行。"""
        tok = self.headers.get("X-RT-Token") or ""
        if not tok:
            return False
        if hmac.compare_digest(tok.encode("utf-8"), RT_TOKEN.encode("utf-8")):
            self._device_id = ""
            return True
        device = devices.verify(tok)
        if not device:
            return False
        self._device_id = device["id"]
        return True
```

在 `_bearer` 之前追加：

```python
    def _cookies(self):
        out = {}
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name:
                out[name] = value
        return out

    def _owner_cookie(self):
        return self._cookies().get("rt_owner") == RT_TOKEN

    def _device_cookie(self):
        token = self._cookies().get("cojam_device") or ""
        return token if token and devices.verify(token) else ""

    def _is_local_owner(self):
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        forwarded = any(self.headers.get(h) for h in ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Real-IP"))
        return (not forwarded and self.client_address[0] in ("127.0.0.1", "::1")
                and host in ("localhost", "127.0.0.1"))

    def _https(self):
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def _device_cookie_header(self, token):
        parts = ["cojam_device=" + token, "HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=31536000"]
        if self._https():
            parts.append("Secure")
        return "; ".join(parts)

    @staticmethod
    def _base_url(value):
        try:
            url = urlsplit(str(value or "").strip())
        except ValueError:
            return ""
        if url.scheme not in ("http", "https") or not url.netloc:
            return ""
        if url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
            return ""
        return "%s://%s" % (url.scheme, url.netloc)

    def _owner_page(self, token, extra=None):
        html = getattr(self.server, "ui_index", None)
        if html is None:
            with open(INDEX, "rb") as f:
                html = f.read()
        html = html.replace(b"__RT_TOKEN__", token.encode("ascii"))
        return self._send(200, html, "text/html; charset=utf-8", extra)
```

`_static` 中 member zip 检查改为：

```python
            if not self._from_browser() and not self._owner_cookie() and not self._device_cookie():
                return self._json(403, {"error": USER_ONLY_MSG})
```

`do_GET` 的 `/api/server/info` 改为：

```python
        if path == "/api/server/info":
            return self._json(200, {"app": "AI Bridge", "version": VERSION, "api_revision": API_REVISION,
                                    "lan_url": getattr(self.server, "lan_url", None),
                                    "tailscale_url": getattr(self.server, "tailscale_url", None),
                                    "owner_devices_supported": True})
```

在 `/join` 之前加：

```python
        if path == "/enroll":
            return self._static("enroll.html", "text/html; charset=utf-8")
```

把 `/` 分支替换为：

```python
        if path in ("/", "/index.html"):
            device_token = self._device_cookie()
            if device_token:
                return self._owner_page(device_token)
            if self._is_local_owner():
                return self._owner_page(RT_TOKEN, {"Set-Cookie": "rt_owner=" + RT_TOKEN + "; HttpOnly; SameSite=Strict; Path=/"})
            return self._static("join.html", "text/html; charset=utf-8")
```

`/files/` 检查改为：

```python
            if not self._from_browser() and not self._owner_cookie() and not self._device_cookie():
                return self._json(403, {"error": USER_ONLY_MSG})
```

在管理员 GET 路由区（`/api/trash` 附近）加：

```python
        if path == "/api/devices":
            return self._json(200, {"account": devices.account(),
                                    "devices": devices.listing(getattr(self, "_device_id", ""))})
```

`do_POST` 在 `/api/external/hello` 之后、`/api/agents/add` 之前加公开路由：

```python
        if path == "/api/devices/enroll":
            data = self._read_json()
            if data is None:
                return
            try:
                result = devices.enroll(str(data.get("code") or ""), str(data.get("label") or ""))
            except devices.DeviceError as error:
                return self._json(error.code, {"error": str(error)})
            return self._json(200, {"ok": True, "token": result["token"], "device": result["device"]},
                              {"Set-Cookie": self._device_cookie_header(result["token"])})
```

在 `/api/contacts/key` 之前加管理员路由（此时已过 `_from_browser` 检查）：

```python
        if path == "/api/devices/code":
            base = self._base_url(data.get("base")) or getattr(self.server, "lan_url", None) or ""
            result = devices.create_code(str(data.get("label") or ""))
            return self._json(200, {"ok": True, "code": result["token"], "expires": result["expires"],
                                    "url": (base.rstrip("/") + "/enroll#code=" + quote(result["token"])) if base else ""})
        if path == "/api/devices/revoke":
            try:
                devices.revoke(str(data.get("id") or ""))
            except devices.DeviceError as error:
                return self._json(error.code, {"error": str(error)})
            return self._json(200, {"ok": True})
```

`/api/ui/session` 改为：

```python
        if path == "/api/ui/session":
            if getattr(self, "_device_id", ""):
                return self._json(200, {"ok": True, "tracked": False})
            lifecycle = getattr(self.server, "owner_lifecycle", None)
            if lifecycle is not None:
                try:
                    lifecycle.update(data.get("tab"), data.get("action"), data.get("instance"))
                except ValueError as error:
                    return self._json(400, {"error": str(error)})
            return self._json(200, {"ok": True})
```

在 `start_lan_listener` 之后加：

```python
def start_tailscale_listener(port=None):
    """在 Tailscale 地址上再开一个入口；没有 Tailscale 或端口被占就跳过，不影响其他入口。"""
    ip = common.tailscale_ipv4()
    if not ip:
        return None, None
    port = int(os.environ.get("ROUNDTABLE_TAILSCALE_PORT", "8767")) if port is None else port
    try:
        srv = ExclusiveServer((ip, port), Handler)
    except OSError as error:
        print("Tailscale 入口没开成（%s:%s）：%s" % (ip, port, error), file=sys.stderr)
        return None, None
    srv.tailscale_url = "http://%s:%d" % (ip, srv.server_address[1])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.tailscale_url
```

`main()` 改为：

```python
    lan_srv, lan_url = start_lan_listener()
    ts_srv, tailscale_url = start_tailscale_listener()
    for extra in (srv, lan_srv, ts_srv):
        if extra is not None:
            extra.lan_url = lan_url
            extra.tailscale_url = tailscale_url
    if owner_lifecycle_enabled():
        from .owner_lifecycle import OwnerLifecycle
        srv.owner_lifecycle = OwnerLifecycle(lambda: stop_application(srv))
        for extra in (lan_srv, ts_srv):
            if extra is not None:
                extra.owner_lifecycle = srv.owner_lifecycle
    agents.start_reaper()
    print("会议室已启动：" + url)
    if lan_url:
        print("同一局域网（同一个 WiFi）可扫码或打开：" + lan_url)
    if tailscale_url:
        print("Tailscale 网络可打开：" + tailscale_url)
    if os.environ.get("ROUNDTABLE_NO_BROWSER") != "1":
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        for extra in (lan_srv, ts_srv):
            if extra is not None:
                extra.shutdown()
                extra.server_close()
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python tests/test_devices.py`
Expected: PASS（模块级 + HTTP 全部用例）。

Run: `python tests/test_lan.py`
Expected: PASS（原有 LAN 用例 + Tailscale 监听用例）。

Run: `python tests/test_contacts_access.py`
Expected: PASS（旧管理员令牌路径不回归）。

- [ ] **Step 5: 暂缓提交**

Run: `git status --short`，等用户确认后再提交。

---

### Task 5: 一次性接入页

**Files:**
- Create: `static/enroll.html`
- Test: `tests/test_devices.py`（追加页面可访问与内容检查）

**Interfaces:**
- Consumes: `POST /api/devices/enroll`，请求 `{"code":"code_...","label":"我的 iPhone"}`。
- Produces: 成功后 `location.replace('/')`；不写 localStorage；地址栏清掉 `#code=`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_devices.py` 的 `DeviceHTTP` 里追加：

```python
    def test_enroll_page_is_public_and_does_not_store_tokens(self):
        request = urllib.request.Request(BASE + "/enroll", headers={"Host": "192.168.1.9"})
        with urllib.request.urlopen(request, timeout=5) as response:
            html = response.read().decode("utf-8")
        self.assertIn("接入我的设备", html)
        self.assertIn("/api/devices/enroll", html)
        self.assertIn("location.replace('/')", html)
        self.assertNotIn("localStorage", html)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python tests/test_devices.py`
Expected: FAIL，`/enroll` 返回 404。

- [ ] **Step 3: 创建 `static/enroll.html`**

```html
<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>接入我的设备 · 合拍 Cojam</title>
<link rel="icon" href="/static/icon-192.png"><link rel="apple-touch-icon" href="/static/icon-192.png">
<meta name="theme-color" content="#6022e8">
<style>
:root{font-family:system-ui,"Microsoft YaHei",sans-serif;color-scheme:light dark;background:#151517;color:#ececf1}
*{box-sizing:border-box}body{margin:0;min-height:100dvh;display:grid;place-items:center;padding:24px}
main{width:min(440px,100%);background:#1b1b1e;border:1px solid #333;border-radius:16px;padding:24px}
h1{font-size:22px;margin:0 0 8px}p{color:#aaa;line-height:1.6}label{display:block;margin:18px 0 6px;font-size:14px}
input{width:100%;font:inherit;padding:12px;border:1px solid #444;border-radius:8px;background:#232328;color:inherit}
button{width:100%;margin-top:16px;font:inherit;padding:12px;border:0;border-radius:8px;background:#6022e8;color:#fff;cursor:pointer}
button:disabled{opacity:.5;cursor:not-allowed}#error{color:#fca5a5;min-height:1.5em}#address{font-size:13px}
</style>
<main>
  <h1>接入我的设备</h1>
  <p>这台设备将获得与你相同的完整界面和「我」的身份。只接入你信任的设备。</p>
  <label for="name">设备名称</label>
  <input id="name" maxlength="40" autocomplete="off">
  <button id="join">接入</button>
  <p id="error" role="alert"></p>
  <p id="address" class="hint"></p>
</main>
<script>
const params = new URLSearchParams(location.hash.slice(1));
const code = params.get('code') || '';
const el = id => document.getElementById(id);
function defaultName() {
  const ua = navigator.userAgent;
  const device = /iPhone/i.test(ua) ? 'iPhone' : /iPad/i.test(ua) ? 'iPad' : /Android/i.test(ua) ? 'Android'
    : /Macintosh|Mac OS X/i.test(ua) ? 'Mac' : /Windows/i.test(ua) ? 'Windows' : '新设备';
  const browser = /Edg\//i.test(ua) ? 'Edge' : /Chrome\//i.test(ua) ? 'Chrome' : /Safari\//i.test(ua) ? 'Safari' : '浏览器';
  return device + ' · ' + browser;
}
el('name').value = defaultName();
el('address').textContent = '当前地址：' + location.origin + '。以后请固定用这个地址打开；换地址需要重新生成接入码。';
if (!code) {
  el('join').disabled = true;
  el('error').textContent = '接入码无效，请让主机重新生成。';
}
history.replaceState(null, '', location.pathname + location.search);
el('join').onclick = async () => {
  const label = el('name').value.trim() || defaultName();
  el('join').disabled = true;
  el('error').textContent = '';
  try {
    const response = await fetch('/api/devices/enroll', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code, label})
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || '接入失败');
    location.replace('/');
  } catch (error) {
    el('error').textContent = error.message;
    el('join').disabled = false;
  }
};
</script></html>
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python tests/test_devices.py`
Expected: PASS。

- [ ] **Step 5: 暂缓提交**

Run: `git status --short`，等用户确认后再提交。

---

### Task 6: 屋主界面「我的设备」

**Files:**
- Modify: `static/connections.js`
- Modify: `tests/test_frontend_runtime.py`
- Test: `tests/test_frontend_runtime.py`

**Interfaces:**
- Consumes: `GET /api/devices`、`POST /api/devices/code`、`POST /api/devices/revoke`、`/api/server/info` 的 `tailscale_url`。
- Produces:
  - `connectionDefaultBase('mydevice', info, origin)` 优先 `info.tailscale_url`，其次 `info.lan_url`，最后 `origin`。
  - 向导新增 `mydevice` 类型；第二步生成接入码与二维码，列出设备并可撤销；不进入会话权限步骤。

- [ ] **Step 1: 写失败测试**

在 `tests/test_frontend_runtime.py` 的 `LanInvite.test_lan_base_and_loopback_warning` 脚本里，`connectionDefaultBase` 断言后追加：

```js
assert.equal(context.connectionDefaultBase('mydevice',
  {tailscale_url: 'http://100.64.0.5:8767', lan_url: lan}, origin),
  'http://100.64.0.5:8767');
assert.equal(context.connectionDefaultBase('mydevice', {lan_url: lan}, origin), lan);
assert.equal(context.connectionDefaultBase('mydevice', {}, origin), origin);
assert.ok(context.CONNECTION_TYPES.mydevice);
```

再追加一个 `DeviceWizard` 测试类（只验证纯函数，避免依赖完整 DOM）：

```python
class DeviceWizard(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_device_code_url_and_name(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const js = fs.readFileSync('static/connections.js', 'utf8');
const context = vm.createContext({uiIcon: () => '', URL});
vm.runInContext(js, context);
assert.equal(context.connectionDeviceName('  '), '我的设备');
assert.equal(context.connectionDeviceName('  我的 iPhone  '), '我的 iPhone');
assert.equal(context.connectionDeviceUrl('http://192.168.1.9:8766', 'code_abc.def'),
             'http://192.168.1.9:8766/enroll#code=code_abc.def');
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python tests/test_frontend_runtime.py`
Expected: FAIL，`mydevice` 不存在，`connectionDeviceName` / `connectionDeviceUrl` 未定义。

- [ ] **Step 3: 修改 `static/connections.js`**

在 `connectionDefaultBase` 中加入：

```js
function connectionDefaultBase(type, info, origin) {
  if (type === 'mydevice') return (info && info.tailscale_url) || (info && info.lan_url) || origin;
  return ['user', 'external'].includes(type) && info && info.lan_url ? info.lan_url : origin;
}
```

在 `CONNECTION_TYPES` 中加入：

```js
  mydevice: ['我的设备', '手机、平板或另一台电脑获得完整界面，并以「我」的身份参与。', uiIcon('device')],
```

在 `connectionInvitation` 之前加入纯函数：

```js
function connectionDeviceName(value) { return (value || '').trim() || '我的设备'; }
function connectionDeviceUrl(base, code) { return base.replace(/\/+$/, '') + '/enroll#code=' + encodeURIComponent(code); }
```

`renderConnectionSetup` 在 `if (w.type === 'local')` 之前加：

```js
  if (w.type === 'mydevice') {
    content += '<p class="hint">接入码 10 分钟内有效，只能使用一次。设备拿到长期凭证后，重启应用也不用重新接入。</p>'
      + '<label>设备名称<input id="connDeviceName" maxlength="40" placeholder="例如：我的 iPhone" value="' + esc(w.form.deviceName || '') + '"></label>'
      + '<label>接入地址<select id="connDeviceBase"></select></label>'
      + '<p class="hint" id="connDeviceBaseHint"></p>'
      + '<button id="connDeviceCreate" class="pri">生成设备接入码</button>'
      + '<div id="connDeviceResult"></div>'
      + '<h4>已接入的设备</h4><div id="connDeviceList">正在读取…</div>';
  } else if (w.type === 'local') {
```

注意把原来的 `if (w.type === 'local') {` 改成 `} else if (w.type === 'local') {` 并去掉多余的重复左花括号，保持语法正确。

在 `renderConnectionSetup` 末尾、`if (w.type === 'local') {` 之前插入：

```js
  if (w.type === 'mydevice') {
    renderDeviceSetup(w);
  }
```

在 `connectionModalChanged` 之后加入：

```js
async function connectionGet(url) {
  const response = await fetch(url);
  let data = {};
  try { data = await response.json(); } catch (e) {}
  return [response.ok, data];
}
async function renderDeviceSetup(w) {
  const select = document.getElementById('connDeviceBase');
  const hint = document.getElementById('connDeviceBaseHint');
  const options = [];
  if (w.info && w.info.tailscale_url) options.push([w.info.tailscale_url, 'Tailscale（完整界面）']);
  if (w.info && w.info.lan_url) options.push([w.info.lan_url, '局域网']);
  options.push([location.origin, '当前地址']);
  select.innerHTML = options.map(([value, label]) => '<option value="' + esc(value) + '">' + esc(label) + '</option>').join('');
  select.value = w.base;
  const showHint = () => {
    const issue = connectionBaseWarning(w.base);
    hint.textContent = issue || '用要接入的设备扫描二维码或打开链接。只发给可信设备。';
    hint.classList.toggle('warn', !!issue);
  };
  select.onchange = () => { w.base = select.value; showHint(); };
  showHint();
  document.getElementById('connDeviceCreate').onclick = () => generateDeviceCode(w);
  try {
    const [ok, data] = await connectionGet('/api/devices');
    if (!ok) throw Error(data.error || '读取设备失败');
    w.account = data.account;
    w.devices = data.devices || [];
    renderDeviceList(w);
  } catch (error) {
    document.getElementById('connDeviceList').textContent = error.message;
  }
}
function renderDeviceList(w) {
  const box = document.getElementById('connDeviceList');
  if (!box) return;
  box.innerHTML = (w.devices || []).map(device =>
    '<div class="connection-device"><span><b>' + esc(device.label) + '</b><small>'
    + (device.online ? '在线' : '最近在线 ' + new Date((device.last_seen || device.created) * 1000).toLocaleString())
    + ' · @' + esc(device.id) + '</small></span>'
    + (device.current ? '<small>当前设备</small>' : '<button data-device-revoke="' + esc(device.id) + '">撤销</button>')
    + '</div>').join('') || '<p class="hint">还没有接入其他设备。</p>';
  box.querySelectorAll('[data-device-revoke]').forEach(button => {
    button.onclick = () => revokeDevice(w, button.dataset.deviceRevoke);
  });
}
async function revokeDevice(w, id) {
  if (!confirm('撤销后，这台设备下次请求就会失去完整界面。继续？')) return;
  const [ok, data] = await post('/api/devices/revoke', {id});
  if (!ok) return wizardError(data.error || '撤销失败');
  const [listOk, listing] = await connectionGet('/api/devices');
  if (listOk) { w.devices = listing.devices || []; renderDeviceList(w); }
}
async function generateDeviceCode(w) {
  if (w.busy) return;
  wizardError('');
  const nameInput = document.getElementById('connDeviceName');
  const baseSelect = document.getElementById('connDeviceBase');
  if (nameInput) w.form.deviceName = nameInput.value;
  if (baseSelect) w.base = baseSelect.value;
  setConnectionBusy(w, true);
  try {
    const [ok, data] = await post('/api/devices/code', {
      label: connectionDeviceName(w.form.deviceName), base: w.base
    });
    if (!ok) throw Error(data.error || '生成失败');
    const issue = connectionBaseWarning(w.base);
    const box = document.getElementById('connDeviceResult');
    box.innerHTML = '<div class="connection-evidence success"><b>✓ 接入码已生成</b>'
      + '<p>10 分钟内有效，只能使用一次。用要接入的设备扫描二维码或打开链接。</p></div>'
      + (issue ? '<p class="hint connection-base-warning">' + esc(issue) + ' 先换一个设备能打开的地址。</p>'
               : '<div id="connQr" class="connection-qr" aria-label="设备接入二维码"></div>')
      + '<input id="connDeviceUrl" readonly aria-label="设备接入链接"><button id="connDeviceCopy">复制接入链接</button>';
    document.getElementById('connDeviceUrl').value = data.url || '';
    document.getElementById('connDeviceCopy').onclick = async event => {
      await copyText(data.url || ''); event.target.textContent = '已复制';
    };
    if (!issue) renderConnectionQrUrl('connQr', data.url || connectionDeviceUrl(w.base, data.code));
  } catch (error) {
    wizardError(error.message);
  } finally {
    if (wizardCurrent(w)) setConnectionBusy(w, false);
  }
}
function renderConnectionQrUrl(id, text) {
  const box = document.getElementById(id);
  if (!box || typeof QRCode !== 'function') return;
  try {
    new QRCode(box, {text, width: 168, height: 168, correctLevel: QRCode.CorrectLevel.M});
  } catch (error) { box.textContent = '二维码没生成出来，请直接用链接。'; }
}
```

把现有 `renderConnectionQr` 替换为：

```js
function renderConnectionQr(w) { renderConnectionQrUrl('connQr', connectionInvitation(w)); }
```

`renderConnectionSetup` 末尾的 `if (w.type === 'local')` 代码保持不动；`mydevice` 不生成 `connCheck`，所以不会绑定错误。

`advanceConnection` 开头加：

```js
  if (w.type === 'mydevice') {
    wizardError('');
    w.step = 4;
    renderConnectionWizard();
    return;
  }
```

`updateConnectionEvidence` 末尾的下一步按钮改为：

```js
  const next = document.getElementById('connNext');
  if (next) next.disabled = w.busy || (!['user', 'mydevice'].includes(w.type) && !good);
```

`renderConnectionComplete` 在计算 `contact` 之前加设备分支：

```js
  if (w.type === 'mydevice') {
    wizardFrame(w, '<div class="connection-complete"><span class="connection-symbol">✓</span><h3>我的设备</h3>'
      + '<p class="hint">在要接入的设备上打开接入码链接。接入后，这台设备会获得完整界面，并以「我」的身份参与会话。</p>'
      + ((w.devices || []).length ? '<p class="hint">已接入 ' + w.devices.length + ' 台设备。可随时在接入页撤销。</p>' : '')
      + '</div><div class="btns"><button id="connDone">完成</button></div>');
    document.getElementById('connDone').onclick = () => { hideModal(); refreshContacts(); loadState(); };
    return;
  }
```

`captureConnectionForm` 增加设备名称字段：

```js
  for (const [field, id] of Object.entries({label:'connName', id:'connId', command:'connCommand', args:'connArgs',
      endpoint:'connEndpoint', serviceKey:'connServiceKey', deviceName:'connDeviceName'})) {
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python tests/test_frontend_runtime.py`
Expected: PASS（原有用例 + mydevice 用例）。

Run: `python tests/test_devices.py`
Expected: PASS。

- [ ] **Step 5: 暂缓提交**

Run: `git status --short`，等用户确认后再提交。

---

### Task 7: 第 1 期全量回归

**Files:**
- Test: `tests/run_all.py`
- Test: `tests/test_contacts_access.py`
- Test: `tests/test_lan.py`
- Test: `tests/test_devices.py`

**Interfaces:**
- Confirms: 旧管理员令牌、LAN 入口、外部联系人认证、MCP、桌面壳页签生命周期全部不回归。

- [ ] **Step 1: 跑全量测试**

Run: `python tests/run_all.py`
Expected: 每个 `test_*.py` 都 PASS，最后一行 `N tests; 0 failing modules`。

- [ ] **Step 2: 手工验证本机路径**

Run: `python -m bridge.web`
Expected:
- 控制台出现 `会议室已启动：http://127.0.0.1:8765/`；
- 如果装了 Tailscale，再出现 `Tailscale 网络可打开：http://100.x.x.x:8767`；
- 本机浏览器打开 `http://127.0.0.1:8765/` 仍是完整界面；
- 打开 `http://127.0.0.1:8765/join` 仍是游客页。

- [ ] **Step 3: 手工验证设备接入**

1. 本机完整界面 → 左侧「＋ 接入」→「我的设备」。
2. 选一个手机能打开的地址，生成接入码。
3. 手机打开链接 → 填设备名 → 接入 → 应进入完整界面。
4. 刷新手机页面，仍是完整界面；关掉浏览器再打开同一地址，仍是完整界面。
5. 本机设备列表出现手机，状态为在线；点撤销后，手机下一次请求回到游客页。
6. 用 Tailscale Serve 地址重复第 2–4 步；如果 Tailscale 监听成功，也测试 `http://100.x.x.x:8767` 直连。

- [ ] **Step 4: 更新任务状态**

在任务列表里把第 1 期任务标为 completed，并记录手工验证结果（通过 / 未通过的具体步骤）。

- [ ] **Step 5: 暂缓提交**

Run: `git status --short`，等用户确认后再提交。

---

## 第 1 期完成标准

- 手机/另一台电脑通过设备接入码打开后是完整界面，不是游客页。
- 刷新、关页、重启服务后仍是完整界面；撤销后立即失效。
- 设备凭证只存在于 HttpOnly cookie 和服务端摘要；localStorage 中没有设备凭证。
- 仅带 cookie 的请求不能调用管理 API；`/files/` 可以用 cookie 读取。
- 本机 `127.0.0.1` 体验不变；桌面壳生命周期不受远程设备标签影响。
- 有 Tailscale 时自动出现 8767 监听和 `tailscale_url`；没有时静默跳过。
- 全量测试通过。

## 后续计划

第 2 期（游客邀请票、懒创建、保活）、第 3 期（接入界面整合 + Harness 扫描/MCP）、第 4 期（跨网文档与发布）在第 1 期验收后分别写实施计划，避免在身份模型未定时反复改 UI。
