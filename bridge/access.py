"""Application contacts, revocable credentials and conversation permissions."""
import hashlib
import os
import secrets
import threading

from . import registry
from .common import LOG_DIR, read_json, write_json

LOCK = threading.RLock()
PERMISSIONS = {"read": True, "post": True, "trigger": True, "board": False, "files": False, "work": False}


def permissions(room, pid):
    defaults = dict(PERMISSIONS)
    if "permissions" not in room.meta:  # preserve existing local rooms
        defaults.update(board=True, files=True, work=True)
    return dict(defaults, **room.meta.get("permissions", {}).get(pid, {}))


def allowed(room, pid, action="read"):
    spec = registry.get(pid)
    if not room or not spec or not spec["enabled"]:
        return False
    with room.lock:
        return bool(pid in room.ais and permissions(room, pid).get(action))


def require(room, pid, action="read"):
    if not allowed(room, pid, action):
        raise PermissionError("未加入此会话或没有%s权限" % action)


def credential(pid, revoke=False):
    spec = registry.get(pid)
    if not spec or not spec["enabled"]:
        raise ValueError("联系人不存在或未启用")
    path = os.path.join(LOG_DIR, "contact_keys.json")
    with LOCK:
        keys = read_json(path) or {}
        token = "" if revoke else secrets.token_urlsafe(32)
        keys.pop(pid, None)
        if token:
            keys[pid] = hashlib.sha256(token.encode()).hexdigest()
        os.makedirs(LOG_DIR, exist_ok=True)
        write_json(path, keys)
        from . import connections
        connections.forget(pid)
    return token


def identify(token):
    if not token:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with LOCK:
        keys = read_json(os.path.join(LOG_DIR, "contact_keys.json")) or {}
        for pid, saved in keys.items():
            if secrets.compare_digest(saved, digest):
                spec = registry.get(pid)
                return pid if spec and spec["enabled"] else None
    return None


def visible_rooms(pid):
    from .room import rooms
    return [x for x in rooms.listing() if pid in x.get("participants", []) and allowed(rooms.get(x["id"]), pid)]
