# 接入整合第 2 期：游客邀请票与设备身份 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让游客用一张邀请票首次领取身份，同一设备之后复用同一联系人，刷新/换邀请不再新建身份；屋主可查看邀请与游客并移除。

**Architecture:** 新增 `bridge/invites.py` 管理 `invites.json`（邀请票，只存摘要）和 `guests.json`（设备摘要 → 游客联系人）。公开接口 `POST /api/guest/redeem` 在同一把锁内完成“校验邀请 → 找已有身份 → 必要时创建 `guest-*` 联系人 → 加入会话 → 记录邀请”。游客页把 token、`deviceKey`、称呼保存在 localStorage；屋主侧复用现有联系人/权限/会话模型。

**Tech Stack:** Python 3.12 标准库后端；原生 JS 单页（无构建）；现有 `registry` / `access` / `room` / `connections` 模块。

**Spec:** `docs/superpowers/specs/2026-11-06-cojam-access-integration-design.md`（第 5、7、12、13、14 节）

## Global Constraints

- 不租中继、不运营中继；本期不引入 WebRTC、公网隧道或新依赖。
- 核心 `Cojam.exe` 保持零第三方依赖；只做 Windows x64。
- `invites.json` 只存邀请 token 的 SHA-256；`guests.json` 只存 `deviceKey` 的 SHA-256；两人都不落明文密钥。
- 游客联系人复用现有 `agents.d` + `contact_keys.json` + 房间成员模型，不新增鉴权体系。
- 旧 `#key=` 链接必须继续可用。
- **本会话不执行 git commit / push**（用户明确要求除非授权否则不提交）。
- 版本保持 `2.6.0` / `API_REVISION = 2`（第 1 期已升级）。

---

### Task 1: 邀请票存储与屋主管理函数

**Files:**
- Create: `bridge/invites.py`
- Test: `tests/test_invites.py`

**Interfaces:**
- Produces: `invites.InviteError(code, message)`、`invites.create(room, permissions=None, label="游客邀请", expires_days=7)`、`invites.find(token)`、`invites.listing()`、`invites.guest_listing()`、`invites.revoke(invite_id)`、`invites.public(row)`
- Produces: `invites.PERMISSIONS` 复用 `access.PERMISSIONS`；邀请 token 形如 `inv_<24hex>.<secret>`

- [ ] **Step 1: 写失败测试（存储、创建、撤销、过期）**

```python
"""Invitation tickets and guest identities. No real AI calls."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

DATA = tempfile.mkdtemp(prefix="cojam_invites_")
os.environ["ROUNDTABLE_LOG_DIR"] = DATA
os.environ["ROUNDTABLE_NO_PRESETS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bridge import invites, registry, room as R, web

registry.load()


def clear_files():
    for name in ("invites.json", "guests.json", "contact_keys.json"):
        try:
            os.remove(os.path.join(DATA, name))
        except FileNotFoundError:
            pass
    agents = Path(DATA, "agents.d")
    if agents.is_dir():
        for path in agents.glob("guest-*.json"):
            path.unlink()


class InviteStore(unittest.TestCase):
    def setUp(self):
        clear_files()
        self.room = R.rooms.create("游客邀请测试", "", DATA, participants=[])

    def test_create_returns_token_and_stores_only_digest(self):
        issued = invites.create(self.room.id, {"read": True, "post": False}, "朋友", 7)
        self.assertRegex(issued["token"], r"^inv_[0-9a-f]{24}\.")
        self.assertEqual(issued["room"], self.room.id)
        self.assertFalse(issued["permissions"]["post"])
        saved = Path(DATA, "invites.json").read_text(encoding="utf-8")
        self.assertNotIn(issued["token"], saved)
        self.assertIn(issued["id"], saved)
        found = invites.find(issued["token"])
        self.assertEqual(found["id"], issued["id"])

    def test_listing_status_and_revoke(self):
        issued = invites.create(self.room.id)
        rows = invites.listing()
        self.assertEqual(rows[0]["status"], "waiting")
        self.assertEqual(rows[0]["status_label"], "等待加入")
        invites.revoke(issued["id"])
        with self.assertRaises(invites.InviteError) as caught:
            invites.find(issued["token"])
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(invites.listing()[0]["status"], "revoked")

    def test_expired_invite_is_gone(self):
        issued = invites.create(self.room.id)
        path = Path(DATA, "invites.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["invites"][0]["expires"] = int(time.time()) - 1
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(invites.InviteError) as caught:
            invites.find(issued["token"])
        self.assertEqual(caught.exception.code, 410)
        self.assertEqual(invites.listing()[0]["status"], "expired")

    def test_archived_room_rejects_new_invite(self):
        R.set_archived(self.room.id, True)
        with self.assertRaises(invites.InviteError) as caught:
            invites.create(self.room.id)
        self.assertEqual(caught.exception.code, 400)
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m unittest tests.test_invites.InviteStore -v`
Expected: FAIL — `ImportError: cannot import name 'invites'`

- [ ] **Step 3: 实现存储与管理函数**

创建 `bridge/invites.py`：

```python
"""Guest invitation tickets and device-bound guest identities."""
import hashlib
import os
import secrets
import threading
import time

from . import access, registry, room as R
from .common import LOG_DIR, is_archived, read_json, write_json

LOCK = threading.RLock()
DEFAULT_DAYS = 7


class InviteError(Exception):
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


def _clean(value, fallback, limit):
    text = "".join(ch for ch in str(value or "") if ch >= " " and ch != "\x7f").strip()
    return (text or fallback)[:limit]


def _rows():
    data = read_json(_path("invites.json")) or {}
    rows = data.get("invites") if isinstance(data, dict) else []
    return [row for row in rows if isinstance(row, dict) and row.get("id")] if isinstance(rows, list) else []


def _save_rows(rows):
    _save("invites.json", {"invites": rows})


def _guest_rows():
    data = read_json(_path("guests.json")) or {}
    guests = data.get("guests") if isinstance(data, dict) else {}
    return guests if isinstance(guests, dict) else {}


def _save_guests(guests):
    _save("guests.json", {"guests": guests})


def _permissions(value):
    result = dict(access.PERMISSIONS)
    if isinstance(value, dict):
        for key, on in value.items():
            if key in result and isinstance(on, bool):
                result[key] = on
    return result


def public(row):
    return {"id": row["id"], "label": row.get("label") or "游客邀请",
            "room": row.get("room") or "",
            "permissions": dict(row.get("permissions") or access.PERMISSIONS),
            "created": int(row.get("created") or 0),
            "expires": int(row.get("expires") or 0),
            "revoked": bool(row.get("revoked"))}


def create(room, permissions=None, label="游客邀请", expires_days=7):
    with LOCK:
        r = R.rooms.get(str(room or ""))
        if r is None or is_archived(r.meta.get("status")):
            raise InviteError(400, "会话不存在或已归档")
        try:
            days = max(1, min(int(expires_days or DEFAULT_DAYS), 30))
        except (TypeError, ValueError):
            days = DEFAULT_DAYS
        now = int(time.time())
        invite_id = "inv_" + secrets.token_hex(12)
        token = invite_id + "." + secrets.token_urlsafe(32)
        row = {"id": invite_id, "hash": _digest(token), "room": r.id,
               "permissions": _permissions(permissions),
               "label": _clean(label, "游客邀请", 40),
               "created": now, "expires": now + days * 86400, "revoked": False}
        rows = _rows()
        rows.append(row)
        _save_rows(rows)
        return dict(public(row), token=token)


def find(token):
    digest = _digest(token or "")
    with LOCK:
        for row in _rows():
            if secrets.compare_digest(str(row.get("hash") or ""), digest):
                if row.get("revoked"):
                    raise InviteError(403, "邀请已撤销，请让屋主重新生成")
                if int(row.get("expires") or 0) <= time.time():
                    raise InviteError(410, "邀请已过期，请让屋主重新生成")
                return row
    raise InviteError(404, "邀请无效，请检查链接")


def _guest_public(pid, row):
    from . import connections
    spec = registry.get(pid) or {}
    status = connections.status(pid)
    return {"id": pid, "label": spec.get("label") or pid,
            "device": row.get("device") or "",
            "created": int(row.get("created") or 0),
            "invites": list(row.get("invites") or []),
            "online": status.get("state") == "connected",
            "last_seen": status.get("last_seen")}


def listing():
    with LOCK:
        rows, guests, now = _rows(), _guest_rows(), time.time()
        out = []
        for row in rows:
            item = public(row)
            joined = [pid for pid, grow in guests.items()
                      if row["id"] in (grow.get("invites") or [])]
            item["guests"] = [_guest_public(pid, guests[pid]) for pid in joined]
            item["guest_count"] = len(joined)
            if item["revoked"]:
                item["status"], item["status_label"] = "revoked", "已撤销"
            elif item["expires"] <= now:
                item["status"], item["status_label"] = "expired", "已过期"
            elif joined:
                item["status"], item["status_label"] = "joined", "已加入 %d 位游客" % len(joined)
            else:
                item["status"], item["status_label"] = "waiting", "等待加入"
            out.append(item)
        return out


def guest_listing():
    with LOCK:
        guests = _guest_rows()
        return [_guest_public(pid, row) for pid, row in guests.items()]


def revoke(invite_id):
    with LOCK:
        rows = _rows()
        for row in rows:
            if row.get("id") == invite_id:
                row["revoked"] = True
                _save_rows(rows)
                return public(row)
    raise InviteError(404, "邀请不存在")
```

- [ ] **Step 4: 运行，确认通过**

Run: `python -m unittest tests.test_invites.InviteStore -v`
Expected: PASS (4 tests)

---

### Task 2: 兑换邀请票与游客身份

**Files:**
- Modify: `bridge/invites.py`
- Modify: `tests/test_invites.py`

**Interfaces:**
- Consumes: Task 1 的 `_rows` / `_guest_rows` / `_save_guests` / `find`
- Produces: `invites.redeem(invite_token, contact_token="", device_key="", name="", device="") -> {"token","contact","room","rooms","created"}`；`invites.remove_guest(pid)`
- 复用：`registry.save_agent`、`access.credential`、`access.identify`、`access.visible_rooms`、`Room.set_members`

- [ ] **Step 1: 写失败测试（身份复用、设备恢复、权限、移除）**

追加到 `tests/test_invites.py`：

```python
class InviteRedeem(unittest.TestCase):
    def setUp(self):
        clear_files()
        self.room = R.rooms.create("游客兑换测试", "", DATA, participants=[])
        self.invite = invites.create(self.room.id, {"read": True, "post": True, "trigger": False})

    def test_first_redeem_creates_guest_and_joins_room(self):
        result = invites.redeem(self.invite["token"], device_key="device-a", name="小林", device="iPhone · Safari")
        self.assertTrue(result["token"])
        self.assertTrue(result["contact"]["id"].startswith("guest-"))
        self.assertEqual(result["contact"]["label"], "小林")
        self.assertEqual(result["room"], self.room.id)
        self.assertIn(result["contact"]["id"], self.room.ais)
        self.assertTrue(access.allowed(self.room, result["contact"]["id"], "post"))
        self.assertFalse(access.allowed(self.room, result["contact"]["id"], "trigger"))
        saved = Path(DATA, "guests.json").read_text(encoding="utf-8")
        self.assertNotIn("device-a", saved)
        row = invites.listing()[0]
        self.assertEqual(row["status"], "joined")
        self.assertEqual(row["guests"][0]["device"], "iPhone · Safari")
        self.assertEqual(access.identify(result["token"]), result["contact"]["id"])

    def test_same_contact_token_reuses_identity(self):
        first = invites.redeem(self.invite["token"], device_key="device-a", name="小林")
        second = invites.redeem(self.invite["token"], contact_token=first["token"], device_key="device-a")
        self.assertEqual(second["contact"]["id"], first["contact"]["id"])
        self.assertEqual(sum(1 for pid in registry.enabled_ids() if pid.startswith("guest-")), 1)
        self.assertEqual(len(invites.guest_listing()), 1)

    def test_same_device_key_recovers_and_rotates_key(self):
        first = invites.redeem(self.invite["token"], device_key="device-a", name="小林")
        second = invites.redeem(self.invite["token"], device_key="device-a")
        self.assertEqual(second["contact"]["id"], first["contact"]["id"])
        self.assertNotEqual(second["token"], first["token"])
        self.assertIsNone(access.identify(first["token"]))
        self.assertEqual(access.identify(second["token"]), first["contact"]["id"])

    def test_new_invite_on_same_device_reuses_identity(self):
        first = invites.redeem(self.invite["token"], device_key="device-a", name="小林")
        other_room = R.rooms.create("第二个会话", "", DATA, participants=[])
        later = invites.create(other_room.id)
        second = invites.redeem(later["token"], device_key="device-a")
        self.assertEqual(second["contact"]["id"], first["contact"]["id"])
        self.assertIn(first["contact"]["id"], other_room.ais)
        pid = first["contact"]["id"]
        saved = json.loads(Path(DATA, "guests.json").read_text(encoding="utf-8"))["guests"][pid]
        self.assertEqual(saved["invites"], [self.invite["id"], later["id"]])

    def test_agent_token_is_not_reused(self):
        registry.save_agent({"id": "bot", "driver": "external", "enabled": True})
        bot_key = access.credential("bot")
        result = invites.redeem(self.invite["token"], contact_token=bot_key, device_key="device-a")
        self.assertNotEqual(result["contact"]["id"], "bot")
        self.assertTrue(result["contact"]["id"].startswith("guest-"))

    def test_second_redeem_keeps_owner_permission_changes(self):
        first = invites.redeem(self.invite["token"], device_key="device-a")
        pid = first["contact"]["id"]
        ids = list(self.room.ais)
        perms = {a: access.permissions(self.room, a) for a in ids}
        perms[pid] = dict(perms[pid], post=False)
        ok, msg = self.room.set_members(ids, perms)
        self.assertTrue(ok, msg)
        invites.redeem(self.invite["token"], contact_token=first["token"], device_key="device-a")
        self.assertFalse(access.permissions(self.room, pid)["post"])

    def test_archived_room_creates_nothing(self):
        R.set_archived(self.room.id, True)
        before = len(registry.enabled_ids())
        with self.assertRaises(invites.InviteError) as caught:
            invites.redeem(self.invite["token"], device_key="device-a")
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(len(registry.enabled_ids()), before)

    def test_remove_guest_cleans_rooms_key_and_record(self):
        result = invites.redeem(self.invite["token"], device_key="device-a")
        pid = result["contact"]["id"]
        invites.remove_guest(pid)
        self.assertNotIn(pid, self.room.ais)
        self.assertIsNone(access.identify(result["token"]))
        self.assertIsNone(registry.get(pid))
        self.assertEqual(invites.guest_listing(), [])
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m unittest tests.test_invites.InviteRedeem -v`
Expected: FAIL — `AttributeError: module 'bridge.invites' has no attribute 'redeem'`

- [ ] **Step 3: 实现兑换与移除**

追加到 `bridge/invites.py`：

```python
def _new_guest_id(guests):
    for _ in range(20):
        pid = "guest-" + secrets.token_hex(4)
        if pid not in guests and registry.get(pid) is None:
            return pid
    raise InviteError(500, "无法生成游客身份")


def _device_hash(device_key):
    return _digest(device_key) if device_key else ""


def _recover_by_device(guests, device_hash):
    if not device_hash:
        return ""
    for pid, row in guests.items():
        saved = str(row.get("device_hash") or "")
        if saved and secrets.compare_digest(saved, device_hash):
            spec = registry.get(pid)
            if spec and spec.get("enabled"):
                return pid
    return ""


def redeem(invite_token, contact_token="", device_key="", name="", device=""):
    with LOCK:
        row = find(invite_token)
        r = R.rooms.get(row.get("room") or "")
        if r is None or is_archived(r.meta.get("status")):
            raise InviteError(409, "邀请对应的会话不可用，请让屋主重新生成")
        device_hash = _device_hash(device_key)
        guests = _guest_rows()
        pid, token, created = "", "", False

        reused = access.identify(contact_token) if contact_token else None
        if reused:
            spec = registry.get(reused)
            if spec and spec.get("kind") == "user" and spec.get("driver") == "external":
                pid = reused

        if not pid:
            pid = _recover_by_device(guests, device_hash)
            if pid:
                token = access.credential(pid)

        if not pid:
            pid = _new_guest_id(guests)
            ok, msg = registry.save_agent({"id": pid, "label": _clean(name, "访客", 40),
                                           "kind": "user", "driver": "external", "enabled": True})
            if not ok:
                raise InviteError(500, msg or "无法创建游客联系人")
            token = access.credential(pid)
            guests[pid] = {"invites": [], "device": _clean(device, "", 60),
                           "device_hash": device_hash, "created": int(time.time())}
            created = True

        if pid.startswith("guest-") and pid not in guests:
            guests[pid] = {"invites": [], "device": _clean(device, "", 60),
                           "device_hash": device_hash, "created": int(time.time())}

        if pid not in r.ais:
            ids = list(r.ais) + [pid]
            perms = {a: access.permissions(r, a) for a in r.ais}
            perms[pid] = dict(row.get("permissions") or access.PERMISSIONS)
            ok, msg = r.set_members(ids, perms)
            if not ok:
                raise InviteError(409, "会话暂时不可加入，请稍后重试：" + msg)

        grow = guests.get(pid)
        if grow is not None:
            tickets = grow.setdefault("invites", [])
            if row["id"] not in tickets:
                tickets.append(row["id"])
            if device_hash and not grow.get("device_hash"):
                grow["device_hash"] = device_hash
            if not grow.get("device"):
                grow["device"] = _clean(device, "", 60)

        _save_guests(guests)
        return {"token": token, "contact": registry.public(registry.get(pid)),
                "room": r.id, "rooms": access.visible_rooms(pid), "created": created}


def remove_guest(pid):
    with LOCK:
        guests = _guest_rows()
        if pid not in guests:
            raise InviteError(404, "游客不存在")
        for summary in R.rooms.listing():
            if pid not in summary.get("participants", []):
                continue
            r = R.rooms.get(summary["id"])
            if r is None:
                continue
            with r.lock:
                ids = [a for a in r.ais if a != pid]
                perms = {a: access.permissions(r, a) for a in ids}
                ok, msg = r.set_members(ids, perms)
            if not ok:
                raise InviteError(409, "暂时无法移除游客：" + summary.get("title", "") + "（" + msg + "）")
        try:
            access.credential(pid, revoke=True)
        except ValueError:
            pass
        ok, msg = registry.remove_agent(pid)
        if not ok:
            raise InviteError(409, msg or "无法删除游客联系人")
        guests.pop(pid, None)
        _save_guests(guests)
        return {"ok": True, "id": pid}
```

- [ ] **Step 4: 运行，确认通过**

Run: `python -m unittest tests.test_invites.InviteRedeem -v`
Expected: PASS (8 tests)

---

### Task 3: HTTP 接口（屋主邀请管理 + 公开兑换）

**Files:**
- Modify: `bridge/web.py:11`（import）、`bridge/web.py:240-243`（GET 路由）、`bridge/web.py:316-335`（公开 POST 路由）、`bridge/web.py:412-422`（屋主 POST 路由）
- Modify: `tests/test_invites.py`

**Interfaces:**
- Consumes: Task 1/2 的 `invites.create/find/listing/guest_listing/revoke/redeem/remove_guest`
- Produces: `GET /api/invites`、`POST /api/invites`、`POST /api/invites/revoke`、`POST /api/guests/remove`、`POST /api/guest/redeem`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_invites.py`：

```python
srv = web.ExclusiveServer(("127.0.0.1", 0), web.Handler)
srv.lan_url = "http://192.168.1.9:8766"
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%s" % srv.server_address[1]


def call(path, obj=None, key=None, owner=False):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    if owner:
        headers["X-RT-Token"] = web.RT_TOKEN
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode() if obj is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class InviteHTTP(unittest.TestCase):
    def setUp(self):
        clear_files()
        self.room = R.rooms.create("接口游客测试", "", DATA, participants=[])

    def test_owner_routes_and_public_redeem(self):
        status, data = call("/api/invites", {"room": self.room.id, "permissions": {"post": False},
                                             "base": "http://192.168.1.9:8766"})
        self.assertEqual(status, 403)
        status, data = call("/api/invites", {"room": self.room.id, "permissions": {"post": False},
                                             "label": "朋友", "base": "http://192.168.1.9:8766"}, owner=True)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["invite"]["id"].startswith("inv_"))
        self.assertTrue(data["url"].startswith("http://192.168.1.9:8766/join#invite=inv_"))
        token = data["token"]
        status, listing = call("/api/invites", owner=True)
        self.assertEqual(status, 200)
        self.assertEqual(listing["invites"][0]["label"], "朋友")
        status, redeemed = call("/api/guest/redeem", {"invite": token, "device_key": "dev-http",
                                                      "name": "小林", "device": "iPhone · Safari"})
        self.assertEqual(status, 200, redeemed)
        self.assertTrue(redeemed["token"])
        self.assertEqual(redeemed["room"], self.room.id)
        self.assertEqual(redeemed["contact"]["label"], "小林")
        self.assertEqual(len(redeemed["rooms"]), 1)
        status, listing = call("/api/invites", owner=True)
        self.assertEqual(listing["invites"][0]["guest_count"], 1)
        self.assertTrue(listing["invites"][0]["guests"][0]["online"] is False)

    def test_redeem_errors_are_actionable(self):
        self.assertEqual(call("/api/guest/redeem", {"invite": "inv_x.y"})[0], 404)
        status, data = call("/api/invites", {"room": "missing"}, owner=True)
        self.assertEqual(status, 400)
        issued = call("/api/invites", {"room": self.room.id}, owner=True)[1]
        call("/api/invites/revoke", {"id": issued["invite"]["id"]}, owner=True)
        status, data = call("/api/guest/redeem", {"invite": issued["token"], "device_key": "d"})
        self.assertEqual(status, 403)
        self.assertIn("撤销", data["error"])

    def test_guest_cannot_use_owner_api(self):
        issued = call("/api/invites", {"room": self.room.id}, owner=True)[1]
        token = call("/api/guest/redeem", {"invite": issued["token"], "device_key": "d"})[1]["token"]
        self.assertEqual(call("/api/invites", key=token)[0], 403)
        self.assertEqual(call("/api/agents", key=token)[0], 403)
        self.assertEqual(call("/api/external/rooms", key=token)[0], 200)

    def test_remove_guest_route(self):
        issued = call("/api/invites", {"room": self.room.id}, owner=True)[1]
        redeemed = call("/api/guest/redeem", {"invite": issued["token"], "device_key": "d"})[1]
        pid = redeemed["contact"]["id"]
        self.assertEqual(call("/api/guests/remove", {"id": pid}, owner=True)[0], 200)
        self.assertNotIn(pid, self.room.ais)
        self.assertIsNone(registry.get(pid))
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m unittest tests.test_invites.InviteHTTP -v`
Expected: FAIL — POST `/api/invites` 返回 403/404

- [ ] **Step 3: 接线路由**

`bridge/web.py` import 行改为：

```python
from . import access, agents, common, connections, devices, install, invites, mcp, members, projects, registry, room as R
```

`do_GET` 中 `/api/devices` 之后加：

```python
        if path == "/api/invites":
            return self._json(200, {"invites": invites.listing(), "guests": invites.guest_listing()})
```

`do_POST` 中 `/api/external/hello` 分支之后加：

```python
        if path == "/api/guest/redeem":
            data = self._read_json()
            if data is None:
                return
            try:
                result = invites.redeem(str(data.get("invite") or ""),
                                        str(data.get("token") or ""),
                                        str(data.get("device_key") or ""),
                                        str(data.get("name") or ""),
                                        str(data.get("device") or ""))
            except invites.InviteError as error:
                return self._json(error.code, {"error": str(error)})
            return self._json(200, {"ok": True, "token": result["token"],
                                    "contact": result["contact"], "room": result["room"],
                                    "rooms": result["rooms"], "created": result["created"]})
```

`do_POST` 的屋主区（`/api/devices/revoke` 之后）加：

```python
        if path == "/api/invites":
            room = str(data.get("room") or "")
            r = R.rooms.get(room)
            if r is None or common.is_archived(r.meta.get("status")):
                return self._json(400, {"error": "会话不存在或已归档"})
            try:
                issued = invites.create(room, data.get("permissions"), str(data.get("label") or "游客邀请"),
                                        data.get("expires_days") or 7)
            except invites.InviteError as error:
                return self._json(error.code, {"error": str(error)})
            base = self._base_url(data.get("base")) or getattr(self.server, "lan_url", None) or ""
            url = (base.rstrip("/") + "/join#invite=" + quote(issued["token"])) if base else ""
            return self._json(200, {"ok": True, "token": issued["token"],
                                    "invite": invites.public(issued), "url": url,
                                    "expires": issued["expires"]})
        if path == "/api/invites/revoke":
            try:
                invites.revoke(str(data.get("id") or ""))
            except invites.InviteError as error:
                return self._json(error.code, {"error": str(error)})
            return self._json(200, {"ok": True})
        if path == "/api/guests/remove":
            try:
                return self._json(200, invites.remove_guest(str(data.get("id") or "")))
            except invites.InviteError as error:
                return self._json(error.code, {"error": str(error)})
```

注意：`data` 在 `do_POST` 中直到 `if not self._from_browser()` 之后才读取；上述屋主路由必须放在 `data = self._read_json()`（约 line 388）之后、`/api/ui/session` 之前。公开的 redeem 路由在 gate 之前，需自己读取。

- [ ] **Step 4: 运行，确认通过**

Run: `python -m unittest tests.test_invites -v`
Expected: PASS (4 + 8 + 4 tests)

---

### Task 4: 游客页身份持久化与邀请兑换

**Files:**
- Modify: `static/join.html`（整体替换）
- Modify: `tests/test_frontend_runtime.py`

**Interfaces:**
- Consumes: `POST /api/guest/redeem`、`/api/external/hello`、`/api/external/rooms`
- Produces: localStorage 键 `cojam_guest_token`、`cojam_guest_device_key`、`cojam_guest_name`；纯函数 `guestDeviceName()`、`guestDeviceKey()`、`invitationValue(value)`

- [ ] **Step 1: 写失败测试**

在 `tests/test_frontend_runtime.py` 增加：

```python
class GuestIdentity(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_helpers_and_page_redeem_flow(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const html = fs.readFileSync('static/join.html', 'utf8');
const match = html.match(/function invitationValue\(value\) \{[\s\S]*?\n\}/);
assert.ok(match, 'invitationValue missing');
const ctx = vm.createContext({URL, URLSearchParams});
vm.runInContext(match[0], ctx);
assert.equal(ctx.invitationValue('http://192.168.1.5:8766/join#invite=inv_abc.def'), 'inv_abc.def');
assert.equal(ctx.invitationValue('  inv_abc.def  '), 'inv_abc.def');
assert.equal(ctx.invitationValue('http://192.168.1.5:8766/join#key=keyvalue'), '');

const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = {};
function node(id) {
  if (!elements[id]) elements[id] = {
    id, value: '', textContent: '', hidden: false, disabled: false, onclick: null, onsubmit: null,
    children: [], replaceChildren() { this.children = []; }, append(c) { this.children.push(c); },
    classList: { toggle() {}, add() {}, remove() {} }, scrollTop: 0, scrollHeight: 0, focus() {}
  };
  return elements[id];
}
const calls = [], historyCalls = [], saved = {};
const store = {getItem: k => (k in saved ? saved[k] : null), setItem: (k, v) => { saved[k] = String(v); },
               removeItem: k => { delete saved[k]; }};
const context = vm.createContext({
  URL, URLSearchParams,
  document: { getElementById: node, createElement: () => node('created-' + Math.random()) },
  location: { hash: '#invite=inv_abc.def', pathname: '/join', search: '' },
  history: { replaceState: (state, title, url) => historyCalls.push(url) },
  localStorage: store,
  setInterval: () => 0,
  navigator: { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1' },
  crypto: { getRandomValues: array => { array.fill(7); return array; } },
  fetch: async (url, options) => {
    calls.push({ url, auth: (options.headers || {}).Authorization, body: options.body ? JSON.parse(options.body) : null });
    if (url === '/api/guest/redeem') return { ok: true, json: async () => ({
      ok: true, token: 'guest-secret', contact: { id: 'guest-11111111', label: '访客' },
      room: 'room-1', rooms: [{ id: 'room-1', title: '测试会话' }], created: true
    }) };
    if (url === '/api/external/hello') return { ok: true, json: async () => ({ contact: { label: '访客', kind: 'user' } }) };
    if (url === '/api/external/rooms') return { ok: true, json: async () => ({
      contact: { label: '访客', kind: 'user' }, rooms: [{ id: 'room-1', title: '测试会话' }]
    }) };
    if (url.startsWith('/api/external/messages')) return { ok: true, json: async () => ({ messages: [], next: 0, permissions: { post: true } }) };
    throw Error('unexpected ' + url);
  }
});
node('namePrompt').hidden = true;
vm.runInContext(source, context);
setTimeout(() => {
  try {
    assert.equal(node('namePrompt').hidden, false, 'name prompt should be shown before redeeming');
    assert.equal(calls.length, 0, 'redeem must wait for the name');
    node('nameConfirm').onclick();
    setTimeout(() => {
      try {
        assert.ok(calls.some(c => c.url === '/api/guest/redeem'));
        const redeem = calls.find(c => c.url === '/api/guest/redeem');
        assert.equal(redeem.body.invite, 'inv_abc.def');
        assert.match(redeem.body.device_key, /^[0-9a-f]{64}$/);
        assert.equal(saved.cojam_guest_token, 'guest-secret');
        assert.equal(historyCalls[0], '/join');
        assert.ok(calls.some(c => c.url === '/api/external/rooms'));
      } catch (error) { console.error(error); process.exitCode = 1; }
    }, 20);
  } catch (error) { console.error(error); process.exitCode = 1; }
}, 20);
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m unittest tests.test_frontend_runtime.GuestIdentity -v`
Expected: FAIL — `invitationValue missing`

- [ ] **Step 3: 重写 `static/join.html`**

保留现有布局/消息轮询，替换脚本与侧栏：

```html
<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>加入会话 · 合拍 Cojam</title>
<link rel="icon" href="/static/icon-192.png"><link rel="apple-touch-icon" href="/static/icon-192.png">
<meta name="theme-color" content="#6022e8">
<style>
:root{font-family:system-ui,"Microsoft YaHei",sans-serif;color-scheme:light dark;background:#151517;color:#ececf1}*{box-sizing:border-box}body{margin:0;height:100dvh;display:grid;grid-template-columns:260px 1fr}aside{padding:24px 16px;background:#1b1b1e;border-right:1px solid #333;overflow:auto}h2{font-size:18px}small{color:#aaa}button,input,textarea{font:inherit;padding:10px;border:1px solid #444;border-radius:8px;background:#232328;color:inherit}input{width:100%;margin:12px 0}button{cursor:pointer}#list button{display:block;width:100%;text-align:left;margin:8px 0}#list button.on{border-color:#6495ff;background:#263656}main{display:flex;flex-direction:column;min-width:0;padding:24px;overflow:hidden}#feed{flex:1;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}.msg{padding:16px 0;border-bottom:1px solid #333}.who{font-size:12px;color:#aaa;margin-bottom:6px}form{display:flex;gap:8px;padding-top:16px}textarea{flex:1;resize:vertical}#error{color:#fca5a5;white-space:pre-wrap}#status{color:#aaa}#namePrompt{border:1px solid #444;border-radius:12px;padding:16px;margin-top:16px;background:#232328}#namePrompt[hidden]{display:none}@media(max-width:650px){body{grid-template-columns:1fr;grid-template-rows:auto 1fr}aside{padding:12px;max-height:42vh}main{padding:16px}h2{margin:0}#list button{display:inline-block;width:auto}}
</style>
<link rel="stylesheet" href="/static/guest.css">
<aside><h2 class="guest-brand"><img src="/static/logo.svg" width="36" height="36" alt=""><span>cojam<small>合拍 · 一起参与</small></span></h2><small>打开邀请，加入这次合拍。</small><label class="key-label" for="key">联系人接入密钥</label><input id="key" type="password" placeholder="粘贴邀请中的密钥" autocomplete="off"><button id="connect">加入会议室</button> <button id="logout">退出此设备</button><p id="identity"></p><div id="namePrompt" hidden><b>怎么称呼你？</b><p class="hint">只影响显示名称，不改变身份。</p><input id="guestName" maxlength="40" placeholder="访客" autocomplete="nickname"><button id="nameConfirm">继续加入</button></div><div id="list" aria-label="已受邀的会话"></div><p id="error" role="alert"></p></aside>
<main><h2 id="title">选择一个会话</h2><p id="status">请向应用管理员索取联系人密钥。</p><div id="feed" aria-live="polite"></div><form id="send"><textarea id="text" placeholder="输入消息…" disabled></textarea><button id="submit" disabled>发送</button></form></main>
<script>
let token='',rid='',since=0,generation=0,permission=false,busy=false,autoOpen=false,presetRoom='',pendingInvite='';
const el=id=>document.getElementById(id);
const KEYS={token:'cojam_guest_token',deviceKey:'cojam_guest_device_key',name:'cojam_guest_name'};
function saved(key){try{return localStorage.getItem(key)||''}catch(e){return ''}}
function save(key,value){try{if(value)localStorage.setItem(key,value);else localStorage.removeItem(key)}catch(e){}}
function guestDeviceName(){const ua=navigator.userAgent,device=/iPhone/i.test(ua)?'iPhone':/iPad/i.test(ua)?'iPad':/Android/i.test(ua)?'Android':/Macintosh|Mac OS X/i.test(ua)?'Mac':/Windows/i.test(ua)?'Windows':'新设备',browser=/Edg\//i.test(ua)?'Edge':/Chrome\//i.test(ua)?'Chrome':/Safari\//i.test(ua)?'Safari':'浏览器';return device+' · '+browser}
function guestDeviceKey(){let value=saved(KEYS.deviceKey);if(!value){const bytes=crypto.getRandomValues(new Uint8Array(32));value=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');save(KEYS.deviceKey,value)}return value}
function invitationValue(value){const text=(value||'').trim();try{const invite=new URLSearchParams(new URL(text).hash.slice(1)).get('invite');if(invite)return invite}catch(e){}return text.startsWith('inv_')?text:''}
async function request(url,body,auth){const r=await fetch(url,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+(auth===undefined?token:auth),'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const j=await r.json();if(!r.ok){const error=Error(j.error||'请求失败');error.status=r.status;throw error}return j}
function clear(){generation++;rid='';since=0;permission=false;el('feed').replaceChildren();el('text').value='';el('text').disabled=el('submit').disabled=true;el('title').textContent='选择一个会话'}
function openRoom(room){clear();rid=room.id;el('title').textContent=room.title;poll();refresh()}
async function refresh(){if(!token)return;const gen=generation;try{const j=await request('/api/external/rooms');if(gen!==generation)return;el('identity').textContent='当前身份：'+j.contact.label+' · 此设备已记住';el('list').replaceChildren();for(const room of j.rooms){const b=document.createElement('button');b.textContent=room.title;b.classList.toggle('on',room.id===rid);b.onclick=()=>openRoom(room);el('list').append(b)}const first=autoOpen;autoOpen=false;const wanted=presetRoom&&j.rooms.find(r=>r.id===presetRoom);presetRoom='';if(wanted){openRoom(wanted);return}if(first&&!rid&&j.rooms.length===1){openRoom(j.rooms[0]);return}if(rid&&!j.rooms.some(r=>r.id===rid)){clear();el('status').textContent='此会话的访问权限已撤销'}else if(!rid)el('status').textContent=j.rooms.length?'选择左侧会话开始交流':'还没有受邀的会话，请联系管理员。';el('error').textContent=''}catch(e){autoOpen=false;if(e.status===401){token='';save(KEYS.token,'');clear();el('identity').textContent='';el('error').textContent='身份已失效，请重新打开邀请链接。';return}clear();el('list').replaceChildren();el('error').textContent=e.message}}
async function poll(){if(!rid||busy)return;const gen=generation,room=rid;busy=true;try{const j=await request('/api/external/messages?room='+encodeURIComponent(room)+'&since='+since);if(gen!==generation)return;for(const m of j.messages){const box=document.createElement('div');box.className='msg';const who=document.createElement('div');who.className='who';who.textContent=m.speaker+' · '+m.time;const text=document.createElement('div');text.textContent=m.text;box.append(who,text);el('feed').append(box)}if(j.messages.length)el('feed').scrollTop=el('feed').scrollHeight;since=j.next;permission=j.permissions.post;el('text').disabled=el('submit').disabled=!permission;el('status').textContent=permission?'以自己的联系人身份发言':'此会话为只读'}catch(e){if(gen===generation){clear();el('error').textContent=e.message}}finally{busy=false}}
async function redeemInvite(invite){clear();el('error').textContent='';try{const body={invite,device_key:guestDeviceKey(),name:saved(KEYS.name),device:guestDeviceName()};if(token)body.token=token;const j=await request('/api/guest/redeem',body,'');if(j.token){token=j.token;save(KEYS.token,token)}save(KEYS.name,j.contact.label);el('key').value='';history.replaceState(null,'',location.pathname+location.search);presetRoom=j.room||'';autoOpen=!presetRoom;await refresh()}catch(e){el('error').textContent=e.message}}
function askName(invite){pendingInvite=invite;el('namePrompt').hidden=false;el('guestName').value=saved(KEYS.name)||'访客';el('guestName').focus();el('nameConfirm').onclick=()=>{const name=el('guestName').value.trim()||'访客';save(KEYS.name,name);el('namePrompt').hidden=true;const value=pendingInvite;pendingInvite='';redeemInvite(value)}}
async function connectClient(key){clear();token=invitationKey(key);autoOpen=false;el('key').value='';el('error').textContent='';try{await request('/api/external/hello',{client:'Cojam 游客'});save(KEYS.token,token);save(KEYS.deviceKey,'');el('identity').textContent='';await refresh()}catch(e){autoOpen=false;token='';el('error').textContent=e.message}}
function invitationKey(value){const text=(value||'').trim();try{const key=new URLSearchParams(new URL(text).hash.slice(1)).get('key');if(key)return key}catch(e){}return text}
el('connect').onclick=()=>connectClient(el('key').value);
el('logout').onclick=()=>{token='';save(KEYS.token,'');save(KEYS.deviceKey,'');save(KEYS.name,'');clear();el('list').replaceChildren();el('identity').textContent='';el('status').textContent='已退出此设备'};
el('send').onsubmit=async e=>{e.preventDefault();const text=el('text').value.trim(),room=rid,gen=generation;if(!text||!permission)return;el('submit').disabled=true;try{await request('/api/post',{room,text});if(gen===generation){el('text').value='';await poll()}}catch(e){el('error').textContent=e.message}finally{el('submit').disabled=!permission}};
setInterval(poll,1500);setInterval(refresh,5000);
setInterval(()=>{if(token)request('/api/external/hello',{client:'Cojam 游客'}).catch(e=>{if(e.status===401){token='';save(KEYS.token,'');clear();el('identity').textContent='';el('error').textContent='身份已失效，请重新打开邀请链接。'}})},20000);
token=saved(KEYS.token);
const hash=new URLSearchParams(location.hash.slice(1)),invite=hash.get('invite')||'',initialKey=hash.get('key')||'';
if(invite){history.replaceState(null,'',location.pathname+location.search);if(token||saved(KEYS.name))redeemInvite(invite);else askName(invite)}
else if(initialKey){history.replaceState(null,'',location.pathname+location.search);connectClient(initialKey)}
else if(token){refresh()}
</script></html>
```

- [ ] **Step 4: 运行，确认通过**

Run: `python -m unittest tests.test_frontend_runtime.GuestIdentity -v`
Expected: PASS

- [ ] **Step 5: 回归旧 key 流程**

Run: `python -m unittest tests.test_frontend_runtime.GuestInvitation -v`
Expected: PASS（旧 `#key=` 测试仍通过）

---

### Task 5: 屋主「其他用户」改用邀请票 + 旧联系人标签

**Files:**
- Modify: `static/connections.js`
- Modify: `static/workspace.js:7,46`
- Modify: `tests/test_frontend_runtime.py`

**Interfaces:**
- Consumes: `GET/POST /api/invites`、`POST /api/invites/revoke`、`POST /api/guests/remove`、`POST /api/rooms/new`
- Produces: `connectionGuestUrl(base, token)`、`connectionInviteStatus(invite)`、`renderGuestSetup(w)`、`generateGuestInvite(w)`、`pollGuestInvites(w)`

- [ ] **Step 1: 写失败测试（纯函数）**

在 `tests/test_frontend_runtime.py` 增加：

```python
class GuestWizard(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_invite_url_and_status_labels(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const js = fs.readFileSync('static/connections.js', 'utf8');
const context = vm.createContext({uiIcon: () => '', URL, Date});
vm.runInContext(js, context);
assert.equal(context.connectionGuestUrl('http://192.168.1.9:8766/', 'inv_abc.def'),
             'http://192.168.1.9:8766/join#invite=inv_abc.def');
assert.equal(context.connectionInviteStatus({revoked: true}), '已撤销');
assert.equal(context.connectionInviteStatus({expires: 1}), '已过期');
assert.equal(context.connectionInviteStatus({expires: 9999999999, guest_count: 2}), '已加入 2 位游客');
assert.equal(context.connectionInviteStatus({expires: 9999999999, guest_count: 0}), '等待加入');
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m unittest tests.test_frontend_runtime.GuestWizard -v`
Expected: FAIL — `connectionGuestUrl is not a function`

- [ ] **Step 3: 实现纯函数与邀请界面**

`static/connections.js` 追加纯函数：

```js
function connectionGuestUrl(base, token) {
  return base.replace(/\/+$/, '') + '/join#invite=' + encodeURIComponent(token);
}
function connectionInviteStatus(invite) {
  if (!invite || invite.revoked) return '已撤销';
  if (!invite.expires || invite.expires * 1000 < Date.now()) return '已过期';
  if (invite.guest_count) return '已加入 ' + invite.guest_count + ' 位游客';
  return '等待加入';
}
```

`CONNECTION_TYPES.user` 描述改为：

```js
  user: ['其他用户', '生成邀请票；对方打开链接领取身份，只进指定会话。同一设备之后仍是同一个人。', uiIcon('user')]
```

`renderConnectionSetup` 增加 user 分支（放在 mydevice 分支之后）：

```js
  } else if (w.type === 'user') {
    w.target = w.target || ((ROOMS.find(r => !r.archived) || {}).id || '__new__');
    w.permissions = w.permissions || {read:true,post:true,trigger:true,board:false,files:false,work:false};
    content += '<p class="hint">邀请票不会预先创建联系人。对方打开链接后领取身份，只加入你选择的会话。</p>'
      + '<label>会话<select id="connGuestRoom">' + ROOMS.filter(r => !r.archived).map(r => '<option value="' + esc(r.id) + '"' + (w.target === r.id ? ' selected' : '') + '>' + esc(r.title) + '</option>').join('') + '<option value="__new__"' + (w.target === '__new__' ? ' selected' : '') + '>＋ 新建会话</option></select></label>'
      + '<div id="connGuestNew"' + (w.target === '__new__' ? '' : ' hidden') + '><label>新会话标题<input id="connGuestTitle" maxlength="60" placeholder="例如：与朋友协作"></label></div>'
      + '<h4>此会话内的权限</h4><div id="connPermissions" class="perm-grid">' + renderPermissionChoices(w) + '</div>'
      + '<details class="connection-advanced" open><summary>邀请地址（对方能打开的地址）</summary><label>接入地址<input id="connBase" value="' + esc(w.base) + '"></label><p class="hint" id="connBaseHint"></p></details>'
      + '<button id="connGuestGenerate" class="pri">生成邀请</button><div id="connGuestResult"></div>'
      + '<h4>邀请与游客</h4><div id="connInviteList">正在读取…</div>';
```

动作行改为：

```js
  if (w.type === 'mydevice') {
    content += '<div class="btns connection-actions"><button id="connBack">更换接入方式</button><button id="connNext" class="pri">完成</button></div>';
  } else if (w.type === 'user') {
    content += '<div class="btns connection-actions"><button id="connBack">更换接入方式</button><button id="connNext" class="pri">完成</button></div>';
  } else {
    content += '<div id="connEvidence" class="connection-evidence" aria-live="polite"></div><div class="btns connection-actions"><button id="connBack">更换接入方式</button><button id="connLater">稍后验证，先分配会话</button><button id="connNext" class="pri" disabled>下一步：会话与权限</button></div>';
  }
```

在 `renderConnectionSetup` 末尾的初始化逻辑中：

```js
  if (w.type === 'user') {
    renderGuestSetup(w);
  }
```

`advanceConnection` 开头增加：

```js
  if (w.type === 'user') { hideModal(); refreshContacts(); loadState(); return; }
```

新增函数：

```js
async function renderGuestSetup(w) {
  const select = document.getElementById('connGuestRoom');
  const newBox = document.getElementById('connGuestNew');
  if (select) select.onchange = () => {
    w.target = select.value;
    newBox.hidden = w.target !== '__new__';
  };
  const baseInput = document.getElementById('connBase');
  const hint = document.getElementById('connBaseHint');
  const showHint = () => {
    const issue = connectionBaseWarning(w.base);
    hint.textContent = issue || '游客的手机/电脑要能打开这个地址。跨网络需要你提供双方可达的地址。';
    hint.classList.toggle('warn', !!issue);
  };
  if (baseInput) {
    showHint();
    baseInput.oninput = () => { w.base = baseInput.value.trim().replace(/\/+$/, ''); showHint(); };
  }
  const generate = document.getElementById('connGuestGenerate');
  if (generate) generate.onclick = () => generateGuestInvite(w);
  if (w.inviteUrl) renderGuestInviteResult(w);
  await loadGuestInvites(w);
  if (wizardCurrent(w) && w.step === 2) w.timer = setInterval(() => pollGuestInvites(w), 3000);
}
async function loadGuestInvites(w) {
  try {
    const [ok, data] = await connectionGet('/api/invites');
    if (!ok) throw Error(data.error || '读取邀请失败');
    w.invites = data.invites || [];
    renderGuestInviteList(w);
  } catch (error) {
    const box = document.getElementById('connInviteList');
    if (box) box.textContent = error.message;
  }
}
function renderGuestInviteResult(w) {
  const box = document.getElementById('connGuestResult');
  if (!box) return;
  const issue = connectionBaseWarning(w.base);
  box.innerHTML = (issue ? '<p class="hint connection-base-warning">' + esc(issue) + ' 先换一个对方能打开的地址。</p>' : '')
    + '<input id="connGuestUrl" readonly aria-label="邀请链接"><button id="connGuestCopy">复制邀请链接</button>'
    + (issue ? '' : '<div id="connGuestQr" class="connection-qr" aria-label="邀请二维码"></div>');
  document.getElementById('connGuestUrl').value = w.inviteUrl;
  document.getElementById('connGuestCopy').onclick = async event => { await copyText(w.inviteUrl); event.target.textContent = '已复制'; };
  if (!issue) renderConnectionQrUrl('connGuestQr', w.inviteUrl);
}
function renderGuestInviteList(w) {
  const box = document.getElementById('connInviteList');
  if (!box) return;
  box.innerHTML = (w.invites || []).map(invite => {
    const room = ROOMS.find(r => r.id === invite.room);
    return '<div class="connection-invite"><span><b>' + esc((room || {}).title || invite.room) + '</b><small>'
      + esc(connectionInviteStatus(invite)) + ' · 创建于 ' + new Date((invite.created || 0) * 1000).toLocaleString()
      + ' · 有效至 ' + new Date((invite.expires || 0) * 1000).toLocaleString() + '</small>'
      + (invite.guests || []).map(guest => '<small class="connection-guest">' + esc(guest.label) + ' · ' + esc(guest.device || '设备未知') + (guest.online ? ' · 在线' : '') + ' <button data-guest-remove="' + esc(guest.id) + '">移除</button></small>').join('')
      + '</span>' + (invite.revoked ? '' : '<button data-invite-revoke="' + esc(invite.id) + '">撤销邀请</button>') + '</div>';
  }).join('') || '<p class="hint">还没有邀请。生成后会出现在这里。</p>';
  box.querySelectorAll('[data-invite-revoke]').forEach(button => {
    button.onclick = () => revokeGuestInvite(w, button.dataset.inviteRevoke);
  });
  box.querySelectorAll('[data-guest-remove]').forEach(button => {
    button.onclick = () => removeGuest(w, button.dataset.guestRemove);
  });
}
async function pollGuestInvites(w) {
  if (!wizardCurrent(w) || w.step !== 2) { clearInterval(w.timer); return; }
  await loadGuestInvites(w);
}
async function generateGuestInvite(w) {
  if (w.busy) return;
  wizardError('');
  w.permissions = Object.fromEntries([...mbox.querySelectorAll('[data-connection-permission]')].map(i => [i.dataset.connectionPermission, i.checked]));
  const title = document.getElementById('connGuestTitle');
  setConnectionBusy(w, true);
  try {
    if (w.target === '__new__') {
      const [ok, created] = await post('/api/rooms/new', {
        project: (PROJECTS[0] || {}).id || '', title: (title && title.value.trim()) || '与朋友协作', participants: []
      });
      if (!ok) throw Error(created.error || '会话创建失败');
      w.target = created.id;
      await pollRooms();
    }
    const [ok, data] = await post('/api/invites', {
      room: w.target, permissions: w.permissions, label: '游客邀请', expires_days: 7, base: w.base
    });
    if (!ok) throw Error(data.error || '邀请生成失败');
    w.inviteUrl = data.url || connectionGuestUrl(w.base, data.token);
    w.step = 2;
    renderConnectionWizard();
  } catch (error) {
    if (wizardCurrent(w)) wizardError(error.message);
  } finally {
    if (wizardCurrent(w)) setConnectionBusy(w, false);
  }
}
async function revokeGuestInvite(w, id) {
  if (!confirm('撤销后，这张邀请不能再兑换；已加入的游客不受影响。继续？')) return;
  const [ok, data] = await post('/api/invites/revoke', {id});
  if (!ok) return wizardError(data.error || '撤销失败');
  await loadGuestInvites(w);
}
async function removeGuest(w, id) {
  if (!confirm('移除后，这位游客会退出所有会话，密钥立即失效。继续？')) return;
  const [ok, data] = await post('/api/guests/remove', {id});
  if (!ok) return wizardError(data.error || '移除失败');
  await loadGuestInvites(w);
}
```

`setConnectionBusy` 会让整个向导禁用；`generateGuestInvite` 重新渲染后需要恢复按钮，现有 finally 已处理。

- [ ] **Step 4: 运行，确认通过**

Run: `python -m unittest tests.test_frontend_runtime.GuestWizard -v`
Expected: PASS

- [ ] **Step 5: 修改旧联系人状态文案**

`static/workspace.js` 增加：

```js
function contactStatusLabel(a){const c=(agCat.connections||{})[a.id]||{},m=(agCat.members||{})[a.id]||{};if(m.automatic)return '自动响应已连接';if(a.kind==='user'&&c.state==='waiting')return '邀请已生成，从未连接';return c.label||'未验证'}
```

`drawContacts` 与 `openContacts` 中原来重复的状态表达式替换为 `esc(contactStatusLabel(a))`。

- [ ] **Step 6: 前端回归**

Run: `python -m unittest tests.test_frontend_runtime -v`
Expected: PASS（全部前端运行时测试）

---

### Task 6: 全量回归与手工端到端

**Files:** 无新增；验证用临时数据目录。

- [ ] **Step 1: 全量测试**

Run: `python tests/run_all.py`
Expected: `237 tests; 0 failing modules`（以实际新增数为准，0 failing）

- [ ] **Step 2: 启动临时实例**

```bash
ROUNDTABLE_NO_BROWSER=1 ROUNDTABLE_LOG_DIR=.test-tmp/phase2 ROUNDTABLE_PORT=8899 ROUNDTABLE_LAN_PORT=8897 ROUNDTABLE_TAILSCALE_PORT=8898 python -m bridge
```

- [ ] **Step 3: 浏览器手工验证**

1. 屋主页面 → ＋接入 → 其他用户：选择会话与权限 → 生成邀请；确认页面出现链接、二维码和“等待加入”。
2. 新开无痕窗口打开邀请链接：先出现“怎么称呼你？”；填名字确认后进入指定会话；刷新仍显示“此设备已记住”，身份不变。
3. 屋主再生成一张邀请，用同一无痕窗口打开：游客列表仍是同一个 `guest-*`，不新增身份。
4. 屋主撤销第一张邀请：已加入的游客仍能发言。
5. 点击“移除”：游客立即掉线，会话成员与联系人消失。
6. 旧 `#key=` 链接仍能连接；联系人列表对未连接用户显示“邀请已生成，从未连接”。
7. 控制台无报错；`localStorage` 只有 `cojam_guest_*` 三个键。

- [ ] **Step 4: 停掉临时实例并汇报**

不提交 git；汇报改动文件、测试数量、手工验证结果与遗留项。
