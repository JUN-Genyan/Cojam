# -*- coding: utf-8 -*-
"""连接与心跳设置：统一在 Cojam 上配置，再下发给成员连接端、MCP 待命端和本机常驻进程。

存在 <数据目录>/connection_settings.json：
    {"rev": 3, "global": {...}, "contacts": {"<联系人 id>": {...只写要覆盖的项}}}

读的时候一律经过 effective(pid)：全局默认 ← 全局设置 ← 联系人覆盖，每项都做范围校验，
文件坏了或缺项就用默认值，不会因为一份坏配置把连接全断掉。
"""
import os
import threading
import time

from .common import LOG_DIR, read_json, write_json

# 名称: (默认, 最小, 最大)。单位都是秒，standby_max / idle_reap 是分钟。
FIELDS = {
    "heartbeat_interval": (10, 3, 120),      # 连接端多久发一次心跳
    "offline_after": (60, 15, 600),          # 多久没收到任何请求就算掉线
    "long_poll": (20, 5, 55),                # 成员连接端单次领取任务最长等多久
    "standby_poll": (55, 5, 55),             # MCP 待命单次领取最长等多久（每次返回 = agent 一轮调用）
    "task_timeout_discuss": (1800, 30, 14400),   # 讨论模式一轮总上限
    "task_timeout_work": (7200, 30, 14400),      # 干活模式一轮总上限
    "reconnect": (1, 0, 1),                  # 掉线后自动重连：1 开 / 0 关
    "reconnect_max": (60, 2, 600),           # 重连退避上限
    "standby_max": (120, 5, 1440),           # MCP 待命最长多少分钟后自动结束
    "idle_reap": (15, 1, 240),               # 本机常驻进程（ACP 等）空闲多少分钟后回收
}
LABELS = {
    "heartbeat_interval": "心跳间隔（秒）",
    "offline_after": "多久算掉线（秒）",
    "long_poll": "连接端单次领取等待（秒）",
    "standby_poll": "MCP 待命单次等待（秒）",
    "task_timeout_discuss": "讨论模式任务超时（秒）",
    "task_timeout_work": "干活模式任务超时（秒）",
    "reconnect": "自动重连",
    "reconnect_max": "重连最长间隔（秒）",
    "standby_max": "待命最长时长（分钟）",
    "idle_reap": "常驻进程空闲回收（分钟）",
}
DEFAULTS = {k: v[0] for k, v in FIELDS.items()}

_lock = threading.RLock()
_cache = {"mtime": None, "data": None, "path": None}
_stat = {"path": None, "mtime": None, "checked": 0.0}
STAT_TTL = 1.0   # 一秒内重复读不重复 stat：status() 会被调用得很频繁


def _path():
    return os.path.join(LOG_DIR, "connection_settings.json")


def _clean(values, partial=False):
    """只保留认识的键，数值截到范围内；partial=True 时没给的键不补默认。"""
    out = {}
    if not isinstance(values, dict):
        return out if partial else dict(DEFAULTS)
    for key, (default, low, high) in FIELDS.items():
        if key not in values:
            if not partial:
                out[key] = default
            continue
        raw = values[key]
        if isinstance(raw, bool):
            raw = int(raw)
        try:
            num = int(float(raw))
        except (TypeError, ValueError):
            if not partial:
                out[key] = default
            continue
        out[key] = max(low, min(high, num))
    return out


def _load():
    path = _path()
    with _lock:
        now = time.monotonic()
        if _stat["path"] == path and now - _stat["checked"] < STAT_TTL:
            mtime = _stat["mtime"]
        else:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = None
            _stat.update(path=path, mtime=mtime, checked=now)
        if _cache["path"] == path and _cache["mtime"] == mtime and _cache["data"] is not None:
            return _cache["data"]
        raw = read_json(path, {}) if mtime is not None else {}
        if not isinstance(raw, dict):
            raw = {}
        contacts = raw.get("contacts") if isinstance(raw.get("contacts"), dict) else {}
        data = {"rev": int(raw.get("rev") or 0) if str(raw.get("rev") or "0").isdigit() else 0,
                "global": _clean(raw.get("global") or {}, partial=True),
                "contacts": {str(k): _clean(v, partial=True) for k, v in contacts.items() if isinstance(v, dict)}}
        _cache.update(path=path, mtime=mtime, data=data)
        return data


def rev():
    return _load()["rev"]


def effective(pid=None):
    """某个联系人实际生效的设置（没给 pid 就是全局）。"""
    data = _load()
    out = dict(DEFAULTS)
    out.update(data["global"])
    if pid and pid in data["contacts"]:
        out.update(data["contacts"][pid])
    return out


def get(key, pid=None):
    return effective(pid)[key]


def snapshot():
    """给界面看的：字段范围、全局值、每个联系人的覆盖。"""
    data = _load()
    return {"rev": data["rev"], "defaults": dict(DEFAULTS), "global": effective(),
            "contacts": {k: dict(v) for k, v in data["contacts"].items()},
            "fields": {k: {"default": d, "min": lo, "max": hi, "label": LABELS[k]} for k, (d, lo, hi) in FIELDS.items()}}


def save(global_values=None, pid=None, contact_values=None, clear_contact=False):
    """保存设置并把 rev 加一（连接端看到 rev 变了就更新参数）。返回新的 snapshot。"""
    with _lock:
        data = _load()
        new = {"rev": data["rev"] + 1, "global": dict(data["global"]),
               "contacts": {k: dict(v) for k, v in data["contacts"].items()}}
        if global_values is not None:
            new["global"] = _clean(global_values, partial=True)
        if pid:
            if clear_contact:
                new["contacts"].pop(pid, None)
            elif contact_values is not None:
                cleaned = _clean(contact_values, partial=True)
                if cleaned:
                    new["contacts"][pid] = cleaned
                else:
                    new["contacts"].pop(pid, None)
        os.makedirs(LOG_DIR, exist_ok=True)
        write_json(_path(), new)
        _cache.update(mtime=None, data=None)
        _stat.update(checked=0.0)   # 下次读立刻重新 stat，别把刚保存的值挡住
    return snapshot()


def member_config(pid):
    """下发给成员连接端 / 待命端的那份。"""
    s = effective(pid)
    return {"rev": rev(), "heartbeat_interval": s["heartbeat_interval"], "offline_after": s["offline_after"],
            "long_poll": s["long_poll"], "standby_poll": s["standby_poll"],
            "reconnect": bool(s["reconnect"]), "reconnect_max": s["reconnect_max"],
            "standby_max": s["standby_max"]}
