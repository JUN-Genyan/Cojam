# -*- coding: utf-8 -*-
"""本机 Agent 自动发现与一键连接。

启动 Cojam 时（桌面客户端与同机浏览器共用同一个后台）扫描这台设备上的所有 Agent：
1. 命令行 / ACP 程序（PATH、npm 全局目录、WinGet、Scoop、各软件自带 CLI 目录）
2. 桌面软件（常见安装路径 + Windows 卸载注册表 DisplayName）
3. 已有的 MCP 配置文件

按三层连接策略归并：
- level="direct"：找到可执行程序，Cojam 直接启动并驱动（优先 ACP / 内置驱动，其次无头 CLI）
- level="mcp"：只找到桌面软件，一键写入 MCP 配置 + 生成联系人密钥，再由待命模式（cojam_standby）接单
- level="unsupported"：既无 CLI/ACP，又不支持 MCP
"""
import glob
import json
import os
import threading
import time

from . import access, common, connections, install, registry
from .common import LOG_DIR, read_json, write_json

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG_DIR = os.path.join(HERE, "catalog")
PRESET_DIR = os.path.join(HERE, "presets")

_lock = threading.RLock()
_state = {"scanning": False, "last_scan": 0.0, "items": []}
SCAN_TTL = 60.0   # 60 秒内重复打开「接入」不重复扫盘；手动 scan=1 立即重扫


def _seen_path():
    return os.path.join(LOG_DIR, "discovery_seen.json")


def _cache_path():
    return os.path.join(LOG_DIR, "discovery.json")


def seen_ids():
    raw = read_json(_seen_path(), [])
    return set(str(x) for x in raw if isinstance(x, str)) if isinstance(raw, list) else set()


def dismiss(pid):
    with _lock:
        s = seen_ids()
        if pid == "all":
            for item in _state["items"]:
                if item.get("present"):
                    s.add(item["id"])
        elif pid:
            s.add(str(pid))
        os.makedirs(LOG_DIR, exist_ok=True)
        write_json(_seen_path(), sorted(s))
        for item in _state["items"]:
            item["dismissed"] = item["id"] in s
    return status()


def load_catalog():
    entries = []
    if not os.path.isdir(CATALOG_DIR):
        return entries
    for path in sorted(glob.glob(os.path.join(CATALOG_DIR, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        rows = data.get("agents") if isinstance(data, dict) else (data if isinstance(data, list) else [])
        for row in rows or []:
            if isinstance(row, dict) and row.get("id"):
                entries.append(row)
    return entries


def _uninstall_names():
    """Windows 卸载注册表里的软件名（小写）；和 install.present() 用同一份数据。"""
    return set(common.installed_app_names())


def _find_cli(cli_cfg):
    if not isinstance(cli_cfg, dict):
        return None
    extra = cli_cfg.get("paths") or []
    for p in extra:
        exp = os.path.expandvars(os.path.expanduser(str(p)))
        if os.path.isfile(exp):
            return exp
    for b in cli_cfg.get("bin") or []:
        got = common.find_executable(b, extra_paths=extra)
        if got:
            return got
    return None


def _find_desktop(desktop_cfg, installed_names):
    if not isinstance(desktop_cfg, dict):
        return None
    for p in desktop_cfg.get("detect") or []:
        exp = os.path.expandvars(os.path.expanduser(str(p)))
        if os.path.exists(exp):
            return exp
    for token in desktop_cfg.get("uninstall") or []:
        low = str(token).lower()
        if any(low in n for n in installed_names):
            return "已安装（系统应用列表）"
    return None


def _host_for(entry, has_cli, has_desktop):
    mcp_cfg = entry.get("mcp") if isinstance(entry.get("mcp"), dict) else None
    mcp_desk = entry.get("mcp_desktop") if isinstance(entry.get("mcp_desktop"), dict) else None
    if has_desktop and mcp_desk:
        return mcp_desk.get("id")
    if mcp_cfg:
        return mcp_cfg.get("id")
    if mcp_desk:
        return mcp_desk.get("id")
    return None


def _inspect_entry(entry, installed_names, specs, conn_cat, host_map, dismissed):
    pid = entry["id"]
    cli_cfg = entry.get("cli") if isinstance(entry.get("cli"), dict) else None
    acp_cfg = entry.get("acp") if isinstance(entry.get("acp"), dict) else None
    cli_exe = _find_cli(cli_cfg)
    acp_exe = _find_cli({"bin": acp_cfg.get("bin"), "paths": (cli_cfg or {}).get("paths")}) if acp_cfg else None
    desktop_loc = _find_desktop(entry.get("desktop"), installed_names)
    host_id = _host_for(entry, bool(cli_exe or acp_exe), bool(desktop_loc))
    host_info = host_map.get(host_id) if host_id else None
    mcp_installed = bool(host_info and host_info.get("installed"))

    has_direct = bool(cli_exe or acp_exe)
    has_desktop = bool(desktop_loc)
    present = has_direct or has_desktop or mcp_installed

    options = []
    if cli_cfg and cli_cfg.get("driver") in ("claude", "codex") and cli_exe:
        options.append(cli_cfg["driver"])
    else:
        if acp_exe:
            options.append("acp")
        if cli_exe:
            options.append("cli")
    if host_id and (has_desktop or has_direct or mcp_installed):
        options.append("mcp")

    if has_direct:
        level = "direct"
        via = options[0]
    elif has_desktop and host_id:
        level = "mcp"
        via = "mcp"
    elif has_desktop:
        level = "unsupported"
        via = "none"
    else:
        level = "direct" if (cli_cfg or acp_cfg) else "mcp"
        via = options[0] if options else "none"

    kind = "both" if (has_direct and has_desktop) else ("desktop" if has_desktop else "cli")
    spec = specs.get(pid)
    conn = conn_cat.get(pid) or {}

    if level == "direct":
        connected = bool(spec and spec.get("enabled") and spec.get("driver") in ("claude", "codex", "acp", "cli")
                         and (conn.get("state") == "ready" or spec.get("user")))
    elif level == "mcp":
        connected = bool(spec and spec.get("enabled") and mcp_installed and conn.get("has_key"))
    else:
        connected = False

    verified = bool((cli_cfg or {}).get("verified") or (acp_cfg or {}).get("verified") or (spec and spec.get("verified")))
    reason = ""
    if level == "direct":
        reason = "已找到命令行程序，可由 Cojam 直接调用"
    elif level == "mcp":
        reason = "已检测到桌面软件，可一键写入 MCP 插件并在软件内开启待命模式"
    elif level == "unsupported":
        reason = "仅检测到图形界面且该软件暂不支持 CLI / ACP / MCP"

    return {
        "id": pid,
        "label": entry.get("label") or pid,
        "color": entry.get("color") or "#4285f4",
        "present": present,
        "kind": kind,
        "level": level,
        "via": via,
        "options": options,
        "exe": acp_exe or cli_exe or "",
        "desktop_path": desktop_loc or "",
        "mcp_host": host_id or "",
        "mcp_installed": mcp_installed,
        "connected": connected,
        "verified": verified,
        "dismissed": pid in dismissed,
        "reason": reason,
        "install_hint": entry.get("install_hint") or "",
    }


def scan():
    """立即扫描本机所有 Agent，更新缓存并返回完整状态。"""
    with _lock:
        _state["scanning"] = True
    try:
        registry.load()
        specs = {s["id"]: s for s in registry.all_specs()}
        conn_cat = connections.catalog()
        host_map = {h["id"]: h for h in install.hosts_status()}
        dismissed = seen_ids()
        installed_names = _uninstall_names()
        items = []
        catalog_ids = set()
        for entry in load_catalog():
            catalog_ids.add(entry["id"])
            items.append(_inspect_entry(entry, installed_names, specs, conn_cat, host_map, dismissed))
        # 用户自己在 agents.d 里加的自定义 Agent 也列进发现结果
        for pid, s in specs.items():
            if pid in catalog_ids or s.get("kind") == "user" or not s.get("user"):
                continue
            conn = conn_cat.get(pid) or {}
            items.append({
                "id": pid,
                "label": s.get("label") or pid,
                "color": s.get("color") or "#4285f4",
                "present": bool(s.get("enabled")),
                "kind": "cli" if s.get("driver") in ("cli", "acp", "claude", "codex") else "desktop",
                "level": "direct" if s.get("driver") in ("cli", "acp", "claude", "codex", "http") else "mcp",
                "via": s.get("driver"),
                "options": [s.get("driver")],
                "exe": registry.exe_of(s)[0] if registry.exe_of(s) else "",
                "desktop_path": "",
                "mcp_host": "",
                "mcp_installed": False,
                "connected": bool(s.get("enabled") and (conn.get("state") in ("ready", "connected") or s.get("user"))),
                "verified": bool(s.get("verified")),
                "dismissed": pid in dismissed,
                "reason": "自定义配置的 Agent",
                "install_hint": "",
            })
        now = time.time()
        with _lock:
            _state.update(scanning=False, last_scan=now, items=items)
            try:
                os.makedirs(LOG_DIR, exist_ok=True)
                write_json(_cache_path(), {"last_scan": now, "items": items})
            except OSError:
                pass
        return status()
    finally:
        with _lock:
            _state["scanning"] = False


def ensure_scanned(async_scan=False):
    with _lock:
        fresh = bool(_state["last_scan"]) and time.time() - _state["last_scan"] < SCAN_TTL
        if fresh or _state["scanning"]:
            return
        if async_scan:
            _state["scanning"] = True
            threading.Thread(target=scan, daemon=True, name="cojam-discovery").start()
            return
    scan()


def status():
    with _lock:
        items = [dict(x) for x in _state["items"]]
        detected = [x for x in items if x.get("present")]
        pending = [x for x in detected if not x.get("connected") and not x.get("dismissed") and x.get("level") in ("direct", "mcp")]
        return {
            "scanning": _state["scanning"],
            "last_scan": _state["last_scan"],
            "items": items,
            "detected": detected,
            "pending": pending,
            "pending_count": len(pending),
        }


def _preset_spec(name):
    path = os.path.join(PRESET_DIR, name + ".json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _build_direct_spec(entry, via, exe):
    pid = entry["id"]
    cli_cfg = entry.get("cli") or {}
    acp_cfg = entry.get("acp") or {}
    if via == "acp" and acp_cfg:
        return {
            "id": pid,
            "label": entry.get("label") or pid,
            "color": entry.get("color") or "#4285f4",
            "driver": "acp",
            "bin": [exe] if exe else list(acp_cfg.get("bin") or [pid]),
            "args": {"acp": list(acp_cfg.get("args") or ["acp"])},
            "verified": bool(acp_cfg.get("verified")),
        }
    preset_name = cli_cfg.get("preset")
    base = _preset_spec(preset_name) if preset_name else None
    if base:
        spec = dict(base)
        if exe:
            spec["bin"] = [exe]
        return spec
    return {
        "id": pid,
        "label": entry.get("label") or pid,
        "color": entry.get("color") or "#4285f4",
        "driver": cli_cfg.get("driver") or "cli",
        "bin": [exe] if exe else list(cli_cfg.get("bin") or [pid]),
        "args": dict(cli_cfg.get("args") or {"new": ["-p", "{prompt}"]}),
        "parse": dict(cli_cfg.get("parse") or {"type": "text"}),
        "context": cli_cfg.get("context") or "full",
        "verified": bool(cli_cfg.get("verified")),
    }


def connect(pid, via=None):
    """一键配置并连接某个发现的 Agent。"""
    pid = str(pid or "").strip().lower()
    if not _state["items"]:
        scan()
    entries = {e["id"]: e for e in load_catalog()}
    entry = entries.get(pid)
    item = next((x for x in _state["items"] if x["id"] == pid), None)
    if not entry and not item:
        return False, {"error": "未找到此 Agent：%s" % pid}

    chosen = (via or (item or {}).get("via") or "cli").strip().lower()
    if chosen == "mcp":
        host_id = (item or {}).get("mcp_host") or (entry.get("mcp") or {}).get("id") or (entry.get("mcp_desktop") or {}).get("id") or pid
        cur = registry.get(pid)
        if not cur or ( not cur.get("builtin") and cur.get("driver") != "external"):
            detect_paths = list(((entry or {}).get("desktop") or {}).get("detect") or [])
            spec = {
                "id": pid,
                "label": (entry or item or {}).get("label") or pid,
                "color": (entry or item or {}).get("color") or "#34a853",
                "driver": "external",
                "enabled": True,
                "detect": detect_paths,
                "note": "通过 MCP 插件接入；可在该软件内运行 /cojam_standby 进入待命模式，由 Cojam 直接派发任务。",
            }
            if not (cur and cur.get("builtin")):
                ok, err = registry.save_agent(spec)
                if not ok:
                    return False, {"error": err}
        token = access.credential(pid)
        ok, msg = install.host_install(host_id, token)
        scan()
        standby_cmd = "请调用 ai-bridge 的 cojam_standby 工具进入合拍待命模式：收到任务后执行并用 reply_task 提交结果，再继续调用 cojam_standby。"
        return ok, {
            "ok": ok,
            "id": pid,
            "via": "mcp",
            "host": host_id,
            "msg": msg,
            "token": token,
            "standby_prompt": "/cojam_standby",
            "standby_instruction": standby_cmd,
            "discovery": status(),
        }

    # 直连（claude / codex / acp / cli）
    registry.restore_preset(pid)
    cur = registry.get(pid)
    exe = (item or {}).get("exe") or ""
    if not (cur and cur.get("builtin")):
        if entry:
            spec = _build_direct_spec(entry, chosen, exe)
            ok, err = registry.save_agent(spec)
            if not ok:
                return False, {"error": err}
    conn = connections.check(pid)
    scan()
    ok = conn.get("state") == "ready"
    return ok, {
        "ok": ok,
        "id": pid,
        "via": chosen,
        "connection": conn,
        "msg": conn.get("detail") or conn.get("label") or ("已连接" if ok else "连接验证失败"),
        "discovery": status(),
    }
