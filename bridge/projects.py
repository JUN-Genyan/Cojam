"""Persisted projects, with stable groups for existing conversations."""
import hashlib
import os
import threading
import uuid

from .common import LOG_DIR, PROJECT, read_json, write_json

LOCK = threading.RLock()


def legacy_id(workdir):
    return "folder-" + hashlib.sha256(os.path.normcase(os.path.abspath(workdir or PROJECT)).encode()).hexdigest()[:16]


def listing(conversations):
    with LOCK:
        saved = read_json(os.path.join(LOG_DIR, "projects.json")) or []
        result = {p["id"]: p for p in saved}
        for r in conversations:
            pid = r.get("project") or legacy_id(r["workdir"])
            if pid not in result:
                wd = r["workdir"]
                result[pid] = {"id": pid, "name": os.path.basename(wd.rstrip("\\/")) or wd, "workdir": wd}
        return list(result.values())


def create(name, workdir):
    name = str(name or "").strip()[:60]
    wd = os.path.normpath(os.path.expandvars(os.path.expanduser(str(workdir or "").strip().strip('"'))))
    if not name or not os.path.isabs(wd) or not os.path.isdir(wd):
        raise ValueError("请填写项目名称和存在的工作文件夹完整路径")
    with LOCK:
        path = os.path.join(LOG_DIR, "projects.json")
        saved = read_json(path) or []
        p = {"id": uuid.uuid4().hex, "name": name, "workdir": wd}
        saved.append(p)
        os.makedirs(LOG_DIR, exist_ok=True)
        write_json(path, saved)
        return p
