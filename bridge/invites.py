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
