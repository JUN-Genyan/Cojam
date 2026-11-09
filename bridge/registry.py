# -*- coding: utf-8 -*-
r"""参与者注册表：会议室里能有哪些 AI、每一个怎么叫。

内置两套专门的驱动（写在 agents.py 里）：
    claude  driver=claude   每轮起一个 claude -p --resume <会话>，读 stream-json
    codex   driver=codex    每场会议一个常驻 codex app-server，走 JSON-RPC
别的厂商不用改代码，写一段配置就行，驱动两种：
    cli     起一个命令行程序，读它的输出（大多数 CLI 都能这么接）
    http    把这一轮 POST 给你的服务，你回一句（自己的 harness 最省事，见 docs/PROVIDERS.md）

配置从这几个地方读，按顺序，同 id 的后面的覆盖前面的：
    1. bridge\presets\*.json      仓库自带的内置预置
    2. <数据目录>\agents.d\*.json  用户加的，一个 agent 一个文件
    3. <数据目录>\agents.json      {"agents": [...]} 一个文件写几个
    4. 环境变量 ROUNDTABLE_AGENTS 指的目录或文件（放在最后，方便测试和覆盖）

加载后 common.AIS 会被原地改成当前启用的 id 列表（别的模块 import 到的是同一个 list 对象，所以不用改 import）。
"""
import glob, json, os, re, shutil, subprocess, threading

from .common import LOG_DIR, NO_WINDOW, find_executable, read_json, resolve_cmd, write_json

HERE = os.path.dirname(os.path.abspath(__file__))
PRESET_DIR = os.path.join(HERE, "presets")

# 参与者 id：@ 得打出来，别用空格和符号；不能跟会议室自己的身份撞
ID_RE = re.compile(r"^[a-z][a-z0-9_\-]{0,23}$")
RESERVED = {"user", "system", "me", "room", "bridge"}
CANT_MENTION = ("用户", "user")          # @用户 是叫人，不给 agent 用

COLORS = ["#d97757", "#10a37f", "#4285f4", "#a855f7", "#f59e0b", "#06b6d4", "#ec4899", "#84cc16"]

# 每个 driver 支持的能力；配置里没写的按这个来
DRIVER_CAPS = {
    "claude": {"images": True, "native_compact": True, "handoff": True, "tools": True, "edits": True},
    "codex": {"images": True, "native_compact": True, "handoff": True, "tools": True, "edits": True},
    "cli": {"images": False, "native_compact": False, "handoff": False, "tools": False, "edits": False},
    "http": {"images": False, "native_compact": False, "handoff": False, "tools": False, "edits": False},
    # 会外参与者：会议室不叫它，它自己经 /api/post 或 MCP 进来发言（IDE 里的 agent 都是这种）
    "external": {"images": False, "native_compact": False, "handoff": False, "tools": False, "edits": False},
}

SPECS = {}          # id -> 规整过的配置（按加载顺序）
_loaded = False
_lock = threading.RLock()


def _locker():
    return _lock


def builtin():
    """内置的两个：跟原来一样，只是现在也走注册表。"""
    return [
        {"id": "claude", "label": "Claude", "letter": "C", "color": COLORS[0], "driver": "claude",
         "bin": ["claude"], "models": ["opus", "sonnet", "haiku"], "default": ["claude-opus-5-5", "medium"],
         "efforts": ["low", "medium", "high", "xhigh", "max"], "builtin": True},
        {"id": "codex", "label": "Codex", "letter": "X", "color": COLORS[1], "driver": "codex",
         "bin": ["codex"], "models": [], "default": ["gpt-6.1-sol", "medium"],
         "efforts": ["low", "medium", "high"], "builtin": True},
    ]


def _start_menu_dirs():
    """开始菜单的「程序」目录（当前用户 + 所有用户），检测桌面端装没装用。"""
    roots = [os.path.join(os.environ.get(key) or "", "Microsoft", "Windows", "Start Menu", "Programs")
             for key in ("APPDATA", "ProgramData")]
    return [root for root in roots if root and os.path.isdir(root)]


_shortcut_targets = {}
_shortcut_targets_lock = threading.Lock()


def _shortcut_target(path, modified_ns, size):
    """Resolve a Windows shortcut without launching its target; cache only its path."""
    if os.name != "nt":
        return ""
    key = (path, modified_ns, size)
    with _shortcut_targets_lock:
        if key in _shortcut_targets:
            return _shortcut_targets[key]
    quoted = path.replace("'", "''")
    script = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
              "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('" + quoted + "'); "
              "[Console]::Out.Write($s.TargetPath)")
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    try:
        result = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, timeout=4, **NO_WINDOW)
        target = result.stdout.decode("utf-8", "replace").strip() if result.returncode == 0 else ""
        if target:
            with _shortcut_targets_lock:
                if len(_shortcut_targets) >= 128:
                    _shortcut_targets.clear()
                _shortcut_targets[key] = target
        return target
    except (OSError, subprocess.SubprocessError):
        return ""


def _shortcut_target_exists(path):
    try:
        stat = os.stat(path)
        target = _shortcut_target(path, stat.st_mtime_ns, stat.st_size)
        return bool(target and os.path.isfile(os.path.expandvars(target)))
    except OSError:
        return False


def _shortcut_present(name):
    """A shortcut counts only while its target exists, including custom install drives."""
    wanted = (str(name).strip() + ".lnk").lower()
    for base in _start_menu_dirs():
        for root, _, files in os.walk(base):
            for filename in files:
                if filename.lower() == wanted and _shortcut_target_exists(os.path.join(root, filename)):
                    return True
    return False


def _detected(spec):
    """检测实际程序文件或指向现存程序的快捷方式。"""
    for p in spec.get("detect") or []:
        try:
            if os.path.isfile(os.path.expandvars(os.path.expanduser(p))):
                return True
        except Exception:
            pass
    for name in spec.get("shortcuts") or []:
        try:
            if _shortcut_present(name):
                return True
        except Exception:
            pass
    return False


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [str(x) for x in v if isinstance(x, (str, int, float)) and str(x) != ""]
    return []


def _norm(raw, idx=0):
    """把一段配置规整成能用的一版。字段不对就抛 ValueError（调用方决定是跳过还是报错）。"""
    if not isinstance(raw, dict):
        raise ValueError("配置得是一个对象")
    pid = str(raw.get("id") or "").strip().lower()
    if not ID_RE.match(pid):
        raise ValueError("id 只能是 a-z 开头、小写字母数字和 - _，最长 24 个字：%r" % (raw.get("id"),))
    if pid in RESERVED:
        raise ValueError("id %r 是会议室自己用的名字" % pid)
    driver = str(raw.get("driver") or "cli").strip().lower()
    if driver not in DRIVER_CAPS:
        raise ValueError("driver 只能是 claude / codex / cli / http / external，给的是 %r" % driver)
    if raw.get("kind") == "user" and driver != "external":
        raise ValueError("用户联系人应使用 external 接入方式")

    caps = dict(DRIVER_CAPS[driver])
    rc = raw.get("caps")
    if isinstance(rc, dict):
        for k in caps:
            if isinstance(rc.get(k), bool):
                caps[k] = rc[k]
    caps["tools"] = caps["tools"] and driver in ("claude", "codex")
    if caps["handoff"] and driver not in ("claude", "codex") and not str(raw.get("handoff") or ""):
        caps["handoff"] = False      # 非内置的要说清"在终端里怎么接着聊"，说不清就不给这个按钮

    args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
    arg = {}
    for k in ("base", "read", "work", "new", "resume", "model", "effort"):
        arg[k] = _as_list(args.get(k))

    parse = raw.get("parse") if isinstance(raw.get("parse"), dict) else {}
    ptype = str(parse.get("type") or "text").strip().lower()
    if ptype not in ("text", "json", "ndjson"):
        raise ValueError("parse.type 只能是 text / json / ndjson")
    flt = parse.get("filter")
    if not isinstance(flt, dict):
        flt = {}

    spec = {
        "id": pid,
        "label": str(raw.get("label") or pid).strip()[:40] or pid,
        "letter": (str(raw.get("letter") or pid[:1]).strip() or "?")[:2].upper(),
        "color": str(raw.get("color") or COLORS[idx % len(COLORS)]),
        "driver": driver,
        "kind": "user" if raw.get("kind") == "user" else "agent",
        "bin": _as_list(raw.get("bin")),
        "detect": _as_list(raw.get("detect")),
        "shortcuts": _as_list(raw.get("shortcuts")),
        "args": arg,
        "prompt": str(raw.get("prompt") or ("stdin" if driver == "claude" else "arg")).strip().lower(),
        "images": _as_list(raw.get("images")),
        "env": {str(k): str(v) for k, v in (raw.get("env") or {}).items()} if isinstance(raw.get("env"), dict) else {},
        "parse": {"type": ptype,
                  "text": str(parse.get("text") or ""),
                  "session": str(parse.get("session") or ""),
                  "session_regex": str(parse.get("session_regex") or ""),
                  "filter": {str(k): v for k, v in flt.items()},
                  "join": str(parse.get("join") if parse.get("join") is not None else "\n")},
        "context": str(raw.get("context") or "delta").strip().lower(),
        "models": _as_list(raw.get("models")),
        "efforts": _as_list(raw.get("efforts")),
        "model_efforts": {str(k): _as_list(v) for k, v in (raw.get("model_efforts") or {}).items()}
                         if isinstance(raw.get("model_efforts"), dict) else {},
        "default": _as_list(raw.get("default"))[:2] or ["", ""],
        "caps": caps,
        "handoff": str(raw.get("handoff") or ""),
        "endpoint": str(raw.get("endpoint") or "").strip(),
        "token": str(raw.get("token") or ""),
        "headers": {str(k): str(v) for k, v in (raw.get("headers") or {}).items()} if isinstance(raw.get("headers"), dict) else {},
        "builtin": bool(raw.get("builtin")),
        "verified": bool(raw.get("verified", driver in ("claude", "codex"))),
        "user": False,                      # 是不是用户自己在界面上加的（load 里按来源标）
        "note": str(raw.get("note") or ""),
        "enabled": None,      # 下面按 bin 能不能找到决定，配置里写了就听配置的
        "source": str(raw.get("source") or ""),
    }
    while len(spec["default"]) < 2:
        spec["default"].append("")
    if spec["prompt"] not in ("stdin", "arg"):
        spec["prompt"] = "arg"
    if spec["context"] not in ("delta", "full"):
        spec["context"] = "delta"
    if driver == "http" and not spec["endpoint"]:
        raise ValueError("driver=http 得写 endpoint")
    if driver == "cli" and not spec["bin"]:
        raise ValueError("driver=cli 得写 bin")
    for k in ("id", "label"):
        if spec[k].lower() in ("用户", "user", "系统", "system"):
            raise ValueError("%s 不能叫 %r" % (k, spec[k]))

    # 有没有装：配置里写死了 enabled 就听配置的，没写就看命令在不在 PATH 里
    ra = raw.get("enabled")
    if driver in ("cli", "claude", "codex"):
        # A saved contact or pairing is not evidence that its executable is
        # still installed. Keep its configuration, but recheck availability.
        spec["enabled"] = ra is not False and any(find_executable(b) for b in spec["bin"])
    elif isinstance(ra, bool):
        spec["enabled"] = ra
    elif driver == "http":
        spec["enabled"] = True
    elif driver == "external":
        spec["enabled"] = _detected(spec)     # 只有装了这个软件才出现
    else:
        spec["enabled"] = any(shutil.which(b) for b in spec["bin"]) if spec["bin"] else bool(spec["builtin"])
    return spec


def _read_file(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and isinstance(data.get("agents"), list):
        out = data["agents"]
    elif isinstance(data, list):
        out = data
    elif isinstance(data, dict) and data.get("id"):
        out = [data]
    else:
        raise ValueError("看不懂这个文件的结构（要 {\"agents\": [...]} 或 [ ... ] 或单个对象）")
    for i, x in enumerate(out):
        if isinstance(x, dict) and not x.get("source"):
            x["source"] = os.path.basename(path)
    return out


def _sources():
    """(说明, 路径列表)，按优先级从低到高。"""
    out = [("presets", [] if os.environ.get("ROUNDTABLE_NO_PRESETS") else
            sorted(glob.glob(os.path.join(PRESET_DIR, "*.json"))))]
    d = os.path.join(LOG_DIR, "agents.d")
    out.append(("agents.d", sorted(glob.glob(os.path.join(d, "*.json")))))
    one = os.path.join(LOG_DIR, "agents.json")
    out.append(("agents.json", [one] if os.path.isfile(one) else []))
    env = os.environ.get("ROUNDTABLE_AGENTS")
    if env:
        if os.path.isdir(env):
            out.append(("ROUNDTABLE_AGENTS", sorted(glob.glob(os.path.join(env, "*.json")))))
        elif os.path.isfile(env):
            out.append(("ROUNDTABLE_AGENTS", [env]))
    return out


def _hidden_path():
    """用户在界面上删掉的预置联系人名单（想恢复：删掉名单里的 id 或重新接入同名联系人）。"""
    return os.path.join(LOG_DIR, "hidden_presets.json")


def _hidden_ids():
    try:
        data = read_json(_hidden_path())
    except Exception:
        return []
    return sorted(str(x) for x in data if isinstance(x, str)) if isinstance(data, list) else []


def _set_hidden(ids):
    write_json(_hidden_path(), sorted(set(ids)))


ERRORS = []     # 读配置时出的错，界面上能看（"加了一个但没生效"时好查）


def load():
    """重新读一遍所有配置，返回 id -> spec。读坏的跳过并记进 ERRORS，不影响别的。"""
    global _loaded
    with _locker():
        ERRORS[:] = []
        specs = {}
        for i, s in enumerate(builtin()):
            specs[s["id"]] = _norm(s, i)
        hidden = _hidden_ids()
        for where, files in _sources():
            for path in files:
                try:
                    for i, raw in enumerate(_read_file(path)):
                        spec = _norm(raw, len(specs))
                        spec["source"] = raw.get("source") or os.path.basename(path)
                        spec["user"] = (where == "agents.d")
                        if where == "presets" and spec["id"] in hidden:
                            continue    # 用户在界面上删掉的预置联系人，不再出现
                        specs[spec["id"]] = spec
                except Exception as e:
                    ERRORS.append("%s：%s" % (os.path.basename(path), e))
        SPECS.clear()
        SPECS.update(specs)
        _loaded = True
        _sync_common()
        return dict(SPECS)


def _sync_common():
    """把启用列表原地写进 common.AIS（其它模块早就 import 了同一个 list 对象）。"""
    from . import common
    ids = [pid for pid, s in SPECS.items() if s["enabled"]]
    common.AIS[:] = ids or ["claude"]   # 一个都没装时别把会议室弄成空的


def ensure():
    with _locker():
        if not _loaded:
            load()


def get(pid):
    ensure()
    return SPECS.get(pid)


def all_specs():
    ensure()
    return [dict(v) for v in SPECS.values()]


def enabled_ids():
    ensure()
    return [pid for pid, s in SPECS.items() if s["enabled"]]


def default_participants():
    """新会议默认让谁参加：有 claude / codex 就这两个（跟以前一样），都没有就把启用的全放进去。
    别的 agent 装好了也不自动进每场会议：在页面上给某场会议勾上就进。"""
    ids = enabled_ids()
    core = [a for a in ("claude", "codex") if a in ids]
    return core or list(ids)


def public(spec):
    """给界面看的（去掉 token 之类）。"""
    if not spec:
        return None
    return {"id": spec["id"], "label": spec["label"], "letter": spec["letter"], "color": spec["color"], "driver": spec["driver"],
            "kind": spec["kind"],
            "enabled": spec["enabled"], "caps": dict(spec["caps"]), "models": list(spec["models"]),
            "efforts": list(spec["efforts"]), "default": list(spec["default"]),
            "source": spec["source"], "builtin": spec["builtin"],
            "verified": spec["verified"], "note": spec["note"], "user": spec["user"],
            "context": spec["context"]}


def catalog():
    ensure()
    return {"agents": [public(v) for v in SPECS.values()], "errors": list(ERRORS),
            "active": [pid for pid, s in SPECS.items() if s["enabled"]]}


def discover_hidden_presets():
    """Find installed presets hidden from contacts without changing pairings."""
    hidden = set(_hidden_ids())
    found = []
    for where, paths in _sources():
        if where != "presets":
            continue
        for path in paths:
            try:
                for raw in _read_file(path):
                    spec = _norm(raw)
                    if spec["id"] in hidden and spec["id"] not in SPECS and spec["enabled"] and spec["driver"] in ("cli", "claude", "codex", "external"):
                        found.append(public(spec))
            except (OSError, ValueError, TypeError):
                continue
    return found


def restore_preset(pid):
    if pid not in {row["id"] for row in discover_hidden_presets()}:
        return False
    _set_hidden([item for item in _hidden_ids() if item != pid])
    load()
    return True


def save_agent(raw):
    """把界面上填的一份配置存成 data/agents.d/<id>.json 并立刻生效。返回 (ok, 错误)。"""
    try:
        spec = _norm(raw, len(SPECS))
    except Exception as e:
        return False, str(e)
    cur = SPECS.get(spec["id"])
    if cur and cur["builtin"]:
        return False, "%s 是内置的，不能改" % spec["id"]
    d = os.path.join(LOG_DIR, "agents.d")
    os.makedirs(d, exist_ok=True)
    hidden = _hidden_ids()
    if spec["id"] in hidden:      # 重新接入被删掉的预置联系人 = 解除隐藏
        _set_hidden([x for x in hidden if x != spec["id"]])
    write_json(os.path.join(d, spec["id"] + ".json"), raw)
    load()
    if spec["id"] not in SPECS:
        return False, "存是存了，但没读出来"
    return True, ""


def remove_agent(pid):
    """删除联系人：自己加的删掉配置文件；预置的记进隐藏名单（恢复=重新接入或清空名单）；内置的不让删。"""
    cur = SPECS.get(pid)
    if not cur:
        return False, "没有这个 AI"
    if cur["builtin"]:
        return False, "内置的不能删"
    path = os.path.join(LOG_DIR, "agents.d", pid + ".json")
    if os.path.isfile(path):
        os.remove(path)
        load()
        return True, ""
    source = str(cur.get("source") or "")
    if source and os.path.isfile(os.path.join(PRESET_DIR, source)):
        hidden = _hidden_ids()
        if pid not in hidden:
            _set_hidden(hidden + [pid])
        load()
        return True, ""
    return False, "这个不是在这里加的（来自 %s），要改得去改那个文件" % (source or "预置")


def exe_of(spec):
    """这个 agent 的命令行真身（参数列表开头）；找不到返回 None。"""
    for b in spec.get("bin") or []:
        got = resolve_cmd(b)
        if got:
            return got
    return None
