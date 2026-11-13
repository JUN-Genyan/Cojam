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
    spec = specs.get(pid)
    cli_cfg = entry.get("cli") if isinstance(entry.get("cli"), dict) else None
    acp_cfg = entry.get("acp") if isinstance(entry.get("acp"), dict) else None
    cli_exe = _find_cli(cli_cfg)
    acp_exe = _find_cli({"bin": acp_cfg.get("bin"), "paths": (cli_cfg or {}).get("paths")}) if acp_cfg else None
    desktop_loc = _find_desktop(entry.get("desktop"), installed_names)
    if spec and "bin" in spec.get("settings_fields", []) and spec.get("driver") in ("cli", "acp", "claude", "codex"):
        configured = registry.exe_of(spec)
        cli_exe = configured[0] if configured else None
        acp_exe = cli_exe if spec["driver"] == "acp" else None
    desktop = (spec or {}).get("desktop") or {}
    if desktop.get("path"):
        desktop_loc = _find_desktop({"detect": [desktop["path"]]}, installed_names)
    host_id = desktop.get("host") or _host_for(entry, bool(cli_exe or acp_exe), bool(desktop_loc))
    host_info = host_map.get(host_id) if host_id else None
    mcp_installed = bool(host_info and host_info.get("installed"))
    if desktop.get("mcp_path"):
        host = next((dict(row) for row in install.HOSTS if row["id"] == host_id), None)
        if host:
            host["path"] = os.path.expandvars(os.path.expanduser(desktop["mcp_path"]))
            mcp_installed = install.installed(host)

    has_direct = bool(cli_exe or acp_exe)
    has_desktop = bool(desktop_loc)
    present = has_direct or has_desktop

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
    conn = conn_cat.get(pid) or {}

    if level == "direct":
        connected = bool(spec and spec.get("enabled") and spec.get("driver") in ("claude", "codex", "acp", "cli")
                         and conn.get("state") == "ready")
    elif level == "mcp":
        from . import members
        connected = bool(spec and spec.get("enabled") and members.broker.status(pid)["automatic"])
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
        "mcp_configured": bool(spec and mcp_installed and conn.get("has_key")),
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
        from . import members
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
                "connected": bool(s.get("enabled") and (members.broker.status(pid)["automatic"] if s.get("driver") == "external" else conn.get("state") == "ready")),
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
        pending = [x for x in detected if not x.get("dismissed") and not x.get("connected") and
                   not x.get("mcp_configured") and x.get("level") in ("direct", "mcp")]
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
        preset_name = cli_cfg.get("preset") or pid
        base = _preset_spec(preset_name) if preset_name else None
        return {
            "id": pid,
            "label": entry.get("label") or pid,
            "color": entry.get("color") or "#4285f4",
            "driver": "acp",
            "bin": [exe] if exe else list(acp_cfg.get("bin") or [pid]),
            "args": {"acp": list(acp_cfg.get("args") or ["acp"])},
            "models": list((base or {}).get("models") or []),
            "efforts": list((base or {}).get("efforts") or []),
            "model_efforts": dict((base or {}).get("model_efforts") or {}),
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


def prepare(pid, via=None):
    """Create a candidate once, then use the same editable settings as every other entrypoint."""
    from . import agent_settings
    pid = str(pid or "").strip().lower()
    if not _state["items"]:
        scan()
    entry = next((e for e in load_catalog() if e["id"] == pid), None)
    item = next((x for x in _state["items"] if x["id"] == pid), None)
    if not item or not item.get("present"):
        raise ValueError("未找到已安装的 Agent")
    chosen = str(via or item.get("via") or "cli").lower()
    if chosen not in item.get("options", []) and chosen != item.get("via"):
        raise ValueError("此 Agent 未检测到所选接入方式")
    registry.restore_preset(pid)
    current = registry.get(pid)
    if not current:
        if chosen == "mcp":
            spec = {"id":pid, "label":item["label"], "driver":"external", "enabled":True}
        elif entry:
            spec = _build_direct_spec(entry, chosen, item.get("exe") or "")
        else:
            raise ValueError("请在接入界面添加此程序")
        ok, error = registry.save_agent(spec)
        if not ok:
            raise ValueError(error)
    if chosen == "mcp":
        config = agent_settings.view(pid)
        desktop = dict(config["values"].get("desktop") or {})
        desktop["host"] = desktop.get("host") or item.get("mcp_host") or ""
        if not desktop.get("path") and os.path.isabs(item.get("desktop_path") or ""):
            desktop["path"] = item["desktop_path"]
        if desktop != config["values"].get("desktop"):
            agent_settings.save(pid, {"desktop":desktop}, config["revision"])
    return {"id":pid, "via":chosen, "tab":"desktop" if chosen == "mcp" else "cli"}


def connect(pid, via=None, replace_key=False):
    """Compatibility route; preserve saved drivers, and use the transactional settings installer."""
    from . import agent_settings
    try:
        prepared = prepare(pid, via)
        pid, chosen = prepared["id"], prepared["via"]
        if chosen == "mcp":
            config = agent_settings.view(pid)
            message = agent_settings.install_mcp(pid, replace_key, config["revision"])
            result = {"ok":True,"id":pid,"via":"mcp","msg":message,"standby_prompt":"/cojam_standby",
                      "standby_instruction":"请调用 cojam_standby，按会议室给出的会话接管参数连接。"}
        else:
            connection = connections.check(pid)
            result = {"ok":connection.get("state") == "ready", "id":pid,"via":chosen,
                      "connection":connection,"msg":connection.get("detail") or connection.get("label")}
        scan()
        result["discovery"] = status()
        return result["ok"], result
    except (ValueError, OSError) as error:
        return False, {"ok":False,"error":str(error),"discovery":status()}
