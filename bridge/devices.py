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


def enroll(token, label="", client_id=""):
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
        cid = _clean(client_id, "", limit=64)
        current = _devices()
        # 同一台设备（接入页存在浏览器里的 ID）重复接入：覆盖原记录换新密钥，不新增一行
        row = None
        if cid:
            row = next((r for r in current if r.get("client_id") == cid and not r.get("revoked")), None)
            if row is None:
                row = next((r for r in current if r.get("client_id") == cid), None)
        if row is None:
            row = {"id": "dev_" + secrets.token_hex(12), "label": _clean(label or record.get("label"), "我的设备"),
                   "secret_hash": _digest(device_token), "account_id": account()["id"],
                   "created": now, "last_seen": now, "revoked": False}
            current.append(row)
        else:
            row.update({"label": _clean(label or row.get("label"), str(row.get("label") or "我的设备")),
                        "secret_hash": _digest(device_token), "last_seen": now, "revoked": False})
        if cid:
            row["client_id"] = cid
        _save("devices.json", {"devices": current})
        record["used"] = True
        _save("device_codes.json", {"codes": rows})
        return {"token": device_token, "device": public(row)}


def code_status(code_id):
    """Status for the authenticated issuing device; never return a secret."""
    with LOCK:
        record = next((row for row in _codes() if row.get("id") == code_id), None)
        if record is None:
            return None
        return {"used": bool(record.get("used")), "expired": float(record.get("expires") or 0) < time.time()}


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
