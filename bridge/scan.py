# -*- coding: utf-8 -*-
r"""本机 Harness 扫描快照：一次扫描、分阶段发布、按「能不能用」给界面一张候选表。

和 registry 的分工：registry.load() 负责读配置、找程序、决定 enabled（联系人能不能用）；
scan 在它之上把结果整理成候选（candidate），带状态、安装列表、版本和能做的动作，
界面按状态渲染每一行，不再自己拼「检测到 / 没检测到」。
旧接口（/api/agents、/api/agents/restore…）不动；本模块只服务新的
GET/POST /api/agents/scan 和 POST /api/agents/connect。

候选状态（界面只按状态决定文案和按钮）：
    ready            找到可执行文件，可以直接连接（选安装 + 加入会话）
    attention        找到文件，但版本命令失败或超时
    connected        已经是联系人，且已在至少一场会话里
    desktop_cli      桌面应用装着，且找到它自带的 CLI（合并成一行，连的是 CLI）
    desktop_external 桌面应用，只能作为外部成员接入（支持 MCP / HTTP）
    desktop_only     桌面应用，没有可用接口
    missing          之前接入过（内置或自己加的），现在程序没了
"""
import hashlib, os, subprocess, threading, time, uuid

from . import discovery, registry, room as R
from .common import NO_WINDOW, LOG_DIR, read_json

PROBE_TIMEOUT = 8      # 版本命令最多等几秒
SCAN_TTL = 60          # 扫完的快照放几秒；期间再点「扫描」直接回上一份，不重扫
VERSION_TTL = 300      # 版本探测按（路径 + 改动时间 + 命令）缓存几分钟

ACTIONS = {            # 每个状态默认能做的动作；多个安装时再补 choose_installation
    "ready": ["connect", "diagnose"],
    "attention": ["connect", "diagnose"],
    "connected": ["open"],
    "desktop_cli": ["connect_cli"],
    "desktop_external": ["connect_external", "how"],
    "desktop_only": ["how"],
    "missing": ["rescan", "specify"],
}


class ScanError(Exception):
    """connect 的某一步失败；code 是回给界面的 HTTP 状态码。"""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


_lock = threading.RLock()
_versions = {}
_versions_lock = threading.Lock()
_thread = None
_scan = None


def _blank():
    return {"scan_id": "", "state": "idle", "phase": "", "progress": 0.0,
            "started_at": "", "finished_at": None, "candidates": [],
            "counts": {}, "errors": [], "log": [], "_finished": 0.0}


_scan = _blank()


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _set(phase, progress):
    with _lock:
        _scan["phase"] = phase
        _scan["progress"] = round(min(progress, 1.0), 3)


def _log(phase, text):
    with _lock:
        log = _scan["log"]
        log.append({"time": _now(), "phase": phase, "text": text})
        del log[:-200]                        # 日志区常驻显示，最多留 200 条


def snapshot():
    """当前快照；随时可取，不用等扫描结束。"""
    with _lock:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in _scan.items() if not k.startswith("_")}


def start(force=False):
    """开始一次新扫描；正在扫或快照还新鲜时直接回当前快照，不并行起线程。"""
    global _thread, _scan
    with _lock:
        fresh = _scan["state"] in ("done", "partial") and time.monotonic() - _scan["_finished"] < SCAN_TTL
        if (_thread is not None and _thread.is_alive()) or (fresh and not force):
            return snapshot()
        _scan = _blank()
        _scan.update(scan_id="scan-" + uuid.uuid4().hex[:12], state="scanning",
                     phase="contacts", started_at=_now())
        _log("contacts", "开始扫描本机程序")
        _thread = threading.Thread(target=_run, name="cojam-agent-scan", daemon=True)
        _thread.start()
        return snapshot()


def _run():
    errors = []
    try:
        _set("contacts", 0.1)
        registry.load()                       # 全量：bin、包入口、开始菜单快捷方式都在这一步
        rooms = _rooms_of()
        specs = registry.all_specs()
        rows, total = [], max(len(specs) + 1, 1)
        _set("installs", 0.15)
        for i, spec in enumerate(specs):
            try:
                row = _cli_candidate(spec, rooms) or _desktop_candidate(spec, rooms)
            except Exception as error:        # 单个程序探测失败不拖垮整个扫描
                errors.append("%s：%s" % (spec.get("id") or "?", error))
                _log("installs", "%s 检测失败：%s" % (spec.get("label") or spec.get("id"), error))
                row = None
            if row:
                rows.append(row)
                _log("installs", _row_note(row))
            _set("installs", 0.15 + 0.6 * (i + 1) / total)
        for spec in registry._discovered_hidden:   # 被隐藏的预置又检测到了：给「恢复」的机会
            try:
                row = _cli_candidate(spec, rooms, preset=True) or _desktop_candidate(spec, rooms)
            except Exception as error:
                errors.append("%s：%s" % (spec.get("id") or "?", error))
                row = None
            if row:
                rows.append(row)
                _log("installs", _row_note(row))
        _set("links", 0.85)
        rows = _merge_links(rows)
        counts = {"ready": 0, "attention": 0, "desktop": 0, "connected": 0, "missing": 0}
        for row in rows:
            key = "desktop" if row["kind"] == "desktop" else row["status"]
            counts[key] = counts.get(key, 0) + 1
        with _lock:
            _scan.update(candidates=rows, counts=counts, errors=errors, phase="done", progress=1.0,
                         state="partial" if errors else "done", finished_at=_now(), _finished=time.monotonic())
        _log("done", "扫描完成：" + ("、".join("%s %d" % (k, v) for k, v in sorted(counts.items()) if v) or "没有检测到程序"))
    except Exception as error:
        with _lock:
            _scan.update(state="error", errors=errors + [str(error)], phase="error", progress=1.0,
                         finished_at=_now(), _finished=time.monotonic())
        _log("error", "扫描失败：%s" % error)


def _rooms_of():
    """谁已经在哪些会话里（决定 ready 还是 connected）。"""
    out = {}
    for summary in R.rooms.listing():
        for pid in summary.get("participants") or []:
            out.setdefault(pid, []).append({"id": summary["id"], "title": summary.get("title") or ""})
    return out


def _row(spec, status, installs, rooms, preset=False, version=None, note="", kind="cli"):
    row = {
        "id": spec["id"], "label": spec["label"], "kind": kind, "status": status,
        "version": version, "source": spec.get("source") or "",
        "installations": [dict(r) for r in installs],
        "linked_cli": spec.get("linked_cli") or "",
        "cli": None,
        "rooms": list(rooms.get(spec["id"]) or []),
        "evidence": {"checked_at": _now()},
        "actions": list(ACTIONS.get(status, [])),
        "note": note,
        "preset": bool(preset),
    }
    if len(row["installations"]) > 1:
        row["actions"].append("choose_installation")
    return row


def _cli_candidate(spec, rooms, preset=False):
    """CLI / 内置驱动 → 一行候选；没装且不是用户接过的返回 None（不占地方）。"""
    if spec.get("kind") == "user" or spec["driver"] not in ("claude", "codex", "cli"):
        return None
    enabled = bool(spec.get("enabled"))
    try:
        installs = registry.installations_of(spec)
    except Exception:
        installs = []
    if not installs and not enabled:
        if spec.get("builtin") or spec.get("user"):
            return _row(spec, "missing", [], rooms, preset=preset,
                        note="之前接入过，现在没找到程序文件，可能已卸载")
        return None
    status = "connected" if (enabled and rooms.get(spec["id"])) else "ready"
    version, note = None, ""
    command = (spec.get("discovery") or {}).get("version_command") or []
    if installs and command:
        chosen = next((r for r in installs if r.get("selected")), installs[0])
        version, error = _probe(chosen["path"], command)
        if error:
            status = "attention"
            note = "版本读取失败：%s" % error
    return _row(spec, status, installs, rooms, preset=preset, version=version, note=note)


def _desktop_candidate(spec, rooms):
    """桌面应用（external 驱动）：只报告「装没装、能怎么接」，绝不说成能直接调用。"""
    if spec["driver"] != "external" or spec.get("kind") == "user":
        return None
    paths = _desktop_paths(spec)
    if not paths:
        return None
    installs = [{"id": hashlib.sha256(os.path.normcase(p).encode()).hexdigest()[:20], "path": p} for p in paths]
    linked = str(spec.get("linked_cli") or "")
    cli_row = _cli_candidate(registry.get(linked), rooms) if linked and registry.get(linked) else None
    if cli_row:
        status = "desktop_cli"       # 同一设备先只算一个：主 action 是连接自带的 CLI
    elif spec.get("endpoint"):
        status = "desktop_external"
    else:
        status = "desktop_only"
    row = _row(spec, status, installs, rooms, kind="desktop")
    row["cli"] = cli_row
    return row


def _merge_links(rows):
    """桌面应用行已经把同品牌 CLI 挂进去了，CLI 不再单独占一行。"""
    absorbed = {row["cli"]["id"] for row in rows if row["cli"]}
    return [row for row in rows if not (row["kind"] == "cli" and row["id"] in absorbed)]


def _row_note(row):
    where = ("，已在 %d 场会话" % len(row["rooms"])) if row["rooms"] else ""
    installs = ("，%d 个安装" % len(row["installations"])) if len(row["installations"]) > 1 else ""
    if row["kind"] == "desktop":
        words = {"desktop_cli": "自带 CLI 已找到", "desktop_external": "可作为外部成员接入",
                 "desktop_only": "没有可直接调用的接口"}
        return "%s（桌面应用 · %s%s）" % (row["label"], words.get(row["status"], row["status"]), where)
    words = {"ready": "可直接连接", "attention": row["note"] or "需要确认",
             "connected": "已加入会话", "missing": "没找到程序文件"}
    return "%s（%s%s%s）" % (row["label"], words.get(row["status"], row["status"]), installs, where)


def _desktop_paths(spec):
    """桌面应用的安装位置：detect 里真实存在的路径 + 开始菜单快捷方式指向的目标。"""
    out = []
    for pattern in spec.get("detect") or []:
        try:
            path = os.path.normpath(os.path.expandvars(os.path.expanduser(str(pattern))))
        except Exception:
            continue
        if os.path.isfile(path):
            out.append(path)
    for name in spec.get("shortcuts") or []:
        target = _shortcut_target_by_name(str(name))
        if target:
            out.append(target)
    return list(dict.fromkeys(out))


def _shortcut_target_by_name(name):
    wanted = (name.strip() + ".lnk").lower()
    for base in registry._start_menu_dirs():
        for root, _, files in os.walk(base):
            for filename in files:
                if filename.lower() != wanted:
                    continue
                path = os.path.join(root, filename)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                target = registry._shortcut_target(path, stat.st_mtime_ns, stat.st_size)
                if target and os.path.isfile(os.path.expandvars(target)):
                    return os.path.normpath(os.path.expandvars(target))
    return ""


def _probe(path, argv):
    """跑一次版本命令；结果按（路径 + 改动时间 + 命令）缓存。返回 (版本, 错误)。"""
    try:
        key = (os.path.normcase(path), os.stat(path).st_mtime_ns, tuple(str(a) for a in argv))
    except OSError:
        return None, "程序文件不存在"
    with _versions_lock:
        hit = _versions.get(key)
        if hit and time.monotonic() - hit[0] < VERSION_TTL:
            return hit[1], hit[2]
    with discovery.PROBE_LIMIT:
        try:
            result = subprocess.run([path] + [str(a) for a in argv], capture_output=True,
                                    timeout=PROBE_TIMEOUT, **NO_WINDOW)
        except subprocess.TimeoutExpired:
            out = (None, "版本命令 %d 秒没响应" % PROBE_TIMEOUT)
        except OSError as error:
            out = (None, "启动失败：%s" % error)
        else:
            text = result.stdout.decode("utf-8", "replace").strip()
            out = (text.splitlines()[0][:80], "") if text else (None, "版本命令没有输出（退出码 %d）" % result.returncode)
    with _versions_lock:
        if len(_versions) >= 128:
            _versions.clear()
        _versions[key] = (time.monotonic(), out[0], out[1])
    return out


def connect(candidate, installation=None, label="", room="", rooms=None):
    """原子连接：校验候选 →（恢复预置）→ 选安装 → 改名 → 加入会话。
    任何一步失败抛 ScanError，前端保留已填的名称和选择，只重试失败的一步。"""
    with _lock:
        row = next((c for c in _scan["candidates"] if c["id"] == candidate), None)
    if row is None:
        raise ScanError(404, "请先扫描本机程序，再连接")
    pid = row["id"]
    if row["kind"] == "desktop" and row.get("cli"):
        pid = row["cli"]["id"]            # 桌面应用真正能连的是它自带的 CLI
    if row.get("preset") and registry.get(pid) is None:
        if not registry.restore_preset(pid):
            raise ScanError(404, "没找到可以恢复的预置联系人")
    spec = registry.get(pid)
    if spec is None:
        raise ScanError(404, "联系人不存在")
    chosen = None
    if installation:
        try:
            chosen = registry.select_installation(pid, installation)
        except ValueError as error:
            raise ScanError(400, str(error))
        registry.load()
        spec = registry.get(pid)
        if spec is None or not spec["enabled"]:
            raise ScanError(409, "选中的安装现在用不了，请重新扫描")
    elif not spec["enabled"]:
        available = row.get("installations") or []
        if not available:
            raise ScanError(409, "没找到可用的安装，请重新扫描")
        pick = next((r for r in available if r.get("selected")), available[0])
        try:
            registry.select_installation(pid, pick["id"])
        except ValueError as error:
            raise ScanError(409, str(error))
        registry.load()
        spec = registry.get(pid)
        if spec is None or not spec["enabled"]:
            raise ScanError(409, "程序现在用不了，请重新扫描")
    _apply_label(spec, label)
    joined = None
    if room:                              # 会话必须显式选；不给 room 就只是接入，不进会话
        target = (rooms or R.rooms).get(room)
        if target is None:
            raise ScanError(404, "会话不存在，请重新选择")
        want = list(target.ais)
        if pid not in want:
            ok, msg = target.set_participants(want + [pid])
            if not ok:
                raise ScanError(409, msg)
        joined = {"id": target.id, "title": target.title(), "participants": list(target.ais)}
    return {"ok": True, "candidate": row["id"], "agent": registry.public(registry.get(pid)),
            "installation": chosen, "room": joined}


def _apply_label(spec, label):
    """换个显示名；内置的改不了，预置 / agents.d 的把原始配置复制一份到 agents.d 覆盖。"""
    label = str(label or "").strip()[:40]
    if not label or label == spec["label"] or spec.get("builtin"):
        return
    raw = None
    source = str(spec.get("source") or "")
    for base in (os.path.join(LOG_DIR, "agents.d"), registry.PRESET_DIR):
        path = os.path.join(base, source)
        if source and os.path.isfile(path):
            raw = read_json(path)
            break
    raw = raw if isinstance(raw, dict) else {}
    raw.update({"id": spec["id"], "label": label})
    ok, msg = registry.save_agent(raw)
    if not ok:
        raise ScanError(400, "改名没有生效：%s" % msg)


def _reset():
    """测试用：把扫描状态清回 idle，清掉版本缓存。"""
    global _thread, _scan
    with _lock:
        _thread = None
        _scan = _blank()
    with _versions_lock:
        _versions.clear()
