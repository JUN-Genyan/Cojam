# -*- coding: utf-8 -*-
r"""把会议室接进别人家的工具里（一键，不用改它们的代码）。

    python -m bridge.install              # 看这台机器上哪些能接、接了没
    python -m bridge.install antigravity  # 接 Antigravity
    python -m bridge.install cursor       # 接 Cursor
    python -m bridge.install claude       # 接 Claude Code（走 claude mcp add）
    python -m bridge.install --all        # 装了的一个不落

装的是"会议室自己的 MCP 服务"（HTTP，Streamable HTTP）：
    地址 http://127.0.0.1:<端口>/mcp
接上之后，那边的 agent 就有了 list_rooms / read_room / post_to_room / wait_for_message 四个工具，
能看会议、能以注册表里的 id 发言。

改别人的配置文件之前一律先备份成 <文件>.bak-<时间>；文件里有别的东西就合并，不覆盖。
"""
import json, os, shutil, subprocess, sys, time

from .common import PORT

URL = "http://127.0.0.1:%d/mcp" % PORT
SERVER_NAME = "ai-bridge"
SAMPLE_CLI = """    {
      "id": "myharness",
      "label": "My Harness",
      "driver": "cli",
      "bin": ["myharness"],
      "args": { "new": ["-p", "{prompt}"],
                "resume": ["-p", "--resume", "{session}", "{prompt}"] },
      "parse": { "type": "json", "text": "reply", "session": "session_id" }
    }"""

FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    ME = "AIBridge.exe install"
elif (sys.argv and sys.argv[0].replace("\\", "/").endswith("roundtable.py")):
    ME = "python roundtable.py install"
else:
    ME = "python -m bridge.install"
HOME = os.path.expanduser("~")

# kind:
#   json  —— 往这个 json 文件的 mcpServers 里加一项（Antigravity / Cursor / Gemini CLI 都是这个形状）
#   claude —— 用 claude mcp add 命令（Claude Code 自己的配置别手改）
#   toml  —— 往 ~/.codex/config.toml 里加一段 [mcp_servers.ai-bridge]
HOSTS = [
    {"id": "antigravity", "label": "Google Antigravity", "kind": "json",
     "path": os.path.join(HOME, ".gemini", "config", "mcp_config.json"),
     "detect": [os.path.join(HOME, "AppData", "Local", "Programs", "antigravity", "resources", "bin", "language_server.exe"),
                os.path.join(HOME, "AppData", "Local", "Programs", "Antigravity", "resources", "bin", "language_server.exe")],
     "restart": "重启 Antigravity（完全退出再开）"},
    {"id": "cursor", "label": "Cursor", "kind": "json",
     "path": os.path.join(HOME, ".cursor", "mcp.json"),
     "detect": [os.path.join(HOME, "AppData", "Local", "Programs", "cursor", "Cursor.exe"),
                os.path.join(HOME, ".cursor")],
     "restart": "重启 Cursor"},
    {"id": "gemini", "label": "Gemini CLI", "kind": "json",
     "path": os.path.join(HOME, ".gemini", "settings.json"),
     "bin": ["gemini"], "detect": [],
     "restart": "重开 gemini"},
    {"id": "claude", "label": "Claude Code", "kind": "claude", "bin": ["claude"],
     "restart": "重开 claude（/mcp 里能看到 ai-bridge）"},
    {"id": "codex", "label": "Codex", "kind": "toml", "bin": ["codex"],
     "path": os.path.join(HOME, ".codex", "config.toml"),
     "restart": "重开 codex"},
]


def _backup(path):
    if os.path.exists(path):
        dst = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, dst)
        return dst
    return None


def _read_json(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def install_json(host):
    path = host["path"]
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    try:
        data = _read_json(path)
    except Exception as e:
        return False, "%s 现在不是合法 JSON（%s），不敢动它。手动加吧：\n%s" % (path, e, _sample())
    if not isinstance(data, dict):
        return False, "%s 顶层不是对象，不敢动它。手动加吧：\n%s" % (path, _sample())
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    bak = _backup(path)
    servers[SERVER_NAME] = {"url": URL}
    data["mcpServers"] = servers
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return True, "写好了：%s%s" % (path, ("（原文件备份在 %s）" % os.path.basename(bak)) if bak else "")


def install_claude(host):
    exe = shutil.which("claude")
    if not exe:
        return False, "找不到 claude 命令。手动接的话：\n  claude mcp add --transport http %s %s" % (SERVER_NAME, URL)
    r = subprocess.run([exe, "mcp", "add", "--transport", "http", SERVER_NAME, URL],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    ok = r.returncode == 0
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    msg = tail[-1] if tail else ""
    if not ok and "already" in msg.lower():
        return True, "已经接过了（claude mcp list 能看到 %s）" % SERVER_NAME
    return ok, msg or ("claude mcp add 退出码 %s" % r.returncode)


def install_toml(host):
    path = host["path"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    block = '\n[mcp_servers.%s]\nurl = "%s"\n' % (SERVER_NAME, URL)
    txt = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    if "[mcp_servers.%s]" % SERVER_NAME in txt:
        return True, "已经接过了（%s 里有 [mcp_servers.%s]）" % (path, SERVER_NAME)
    bak = _backup(path)
    with open(path, "a", encoding="utf-8") as f:
        if txt and not txt.endswith("\n"):
            f.write("\n")
        f.write(block)
    return True, "写好了：%s%s" % (path, ("（备份在 %s）" % os.path.basename(bak)) if bak else "")


def _sample():
    return json.dumps({"mcpServers": {SERVER_NAME: {"url": URL}}}, ensure_ascii=False, indent=2)


INSTALLERS = {"json": install_json, "claude": install_claude, "toml": install_toml}


def _claude_has_it():
    """Claude Code 的 MCP 存在 ~/.claude.json 里（按项目分）。不用起 claude 去问，读文件就行。"""
    p = os.path.join(HOME, ".claude.json")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    if SERVER_NAME in (data.get("mcpServers") or {}):
        return True
    for proj in (data.get("projects") or {}).values():
        if isinstance(proj, dict) and SERVER_NAME in (proj.get("mcpServers") or {}):
            return True
    return False


def installed(host):
    """看这个宿主那边是不是已经有 ai-bridge 了。"""
    if host["kind"] == "claude":
        return _claude_has_it()
    path = host.get("path") or ""
    try:
        with open(path, encoding="utf-8") as f:
            return SERVER_NAME in f.read()
    except Exception:
        return False


def present(host):
    for b in host.get("bin") or []:
        if shutil.which(b):
            return True
    for p in host.get("detect") or []:
        if os.path.exists(os.path.expandvars(os.path.expanduser(p))):
            return True
    return False


def join(argv):
    """把"别人怎么接"打出来：地址、能直接贴的配置、能直接跑的一行命令。"""
    base = "http://127.0.0.1:%d" % PORT
    alive = False
    try:
        import urllib.request
        with urllib.request.urlopen(base + "/api/agents", timeout=2) as r:
            alive = r.status == 200
    except Exception:
        alive = False
    print("AI Bridge 会议室 %s：%s\n" % ("正在跑" if alive else "好像没开", base + "/"))
    if not alive:
        print("（先双击 AIBridge.exe 把会议室开起来，再来接。下面这些照抄就对了。）\n")

    print("一、支持 MCP 的工具 —— 把这段贴进它的 MCP 配置")
    print(json.dumps({"mcpServers": {SERVER_NAME: {"url": URL}}}, ensure_ascii=False, indent=2))
    print("   Claude Code：claude mcp add --transport http %s %s" % (SERVER_NAME, URL))
    print("   Codex：~/.codex/config.toml 里加")
    print("          [mcp_servers.%s]" % SERVER_NAME)
    print('          url = "%s"' % URL)
    print("   Cursor / Windsurf / Cline / Roo / Continue / Antigravity：配置里加上面那段 JSON")
    print("   点了 install 的：%s antigravity / cursor / claude / codex" % ME)

    print("\n二、任何能发 HTTP 的程序 —— 不用装任何东西，第一次带 join 就进会")
    print("   PowerShell：")
    print("     Invoke-RestMethod -Method Post -ContentType application/json "
          "-Body '{\"speaker\":\"myharness\",\"text\":\"大家好\",\"join\":true}' %s/api/post" % base)
    print("   Python：")
    print("     import requests; requests.post('%s/api/post', json={\"speaker\":\"myharness\",\"text\":\"大家好\",\"join\":true})" % base)
    print("   speaker 随便起（小写字母开头、最长 24），第一次带 join:true，之后不用带。")

    print("\n三、命令行 AI —— 写一份 json 让会议室每轮主动叫它")
    print("   放到 data\\agents.d\\myharness.json：")
    print(SAMPLE_CLI)
    print("   改完在会议室的\"参会者\"里勾上，不用重启。字段说明见 docs\\PROVIDERS.md。")

    print("\nMCP 工具：list_rooms / read_room / post_to_room / wait_for_message")
    print("完整说明：docs\\PROVIDERS.md")
    return 0 if alive else 1


def hosts_status():
    """界面上"一键接入"那一栏用：每个宿主装没装、接没接。"""
    return [{"id": h["id"], "label": h["label"], "present": present(h), "installed": bool(installed(h))}
            for h in HOSTS]


def host_install(hid):
    """界面点一下就走这里：不让人碰命令行。返回 (ok, 说明)。"""
    host = next((h for h in HOSTS if h["id"] == hid), None)
    if not host:
        return False, "不认识这个工具：%s" % hid
    if not present(host):
        return False, "这台机器上没装 %s" % host["label"]
    ok, detail = INSTALLERS[host["kind"]](host)
    if not ok:
        return False, detail
    return True, "%s：接好了。\n%s\n%s" % (host["label"], host["restart"], detail)


def main(argv):
    args = [a for a in argv if not a.startswith("-")]
    all_flag = "--all" in argv
    if not args and not all_flag:
        print("会议室 MCP 地址：%s\n" % URL)
        print("这台机器上能接的：")
        for h in HOSTS:
            state = ("已接" if installed(h) else "没接") if present(h) else "没装"
            print("  %-12s %-20s %s" % (h["id"], h["label"], state))
        print("\n接一个：%s antigravity" % ME)
        return 0
    ids = [h["id"] for h in HOSTS] if all_flag else args
    bad = [i for i in ids if i not in [h["id"] for h in HOSTS]]
    if bad:
        print("不认识：%s。能用的：%s" % ("、".join(bad), "、".join(h["id"] for h in HOSTS)))
        return 2
    rc = 0
    for hid in ids:
        host = next(h for h in HOSTS if h["id"] == hid)
        print("\n== %s ==" % host["label"])
        if not present(host) and not all_flag:
            print("  这台机器上好像没装（没找到 %s）" % ((host.get("bin") or host.get("detect") or ["-"])[0]))
            rc = 1
            continue
        ok, msg = INSTALLERS[host["kind"]](host)
        print(("  OK " if ok else "  失败 ") + msg)
        if ok:
            print("  下一步：%s" % host["restart"])
            print("  然后在会议室的\"参会者\"里勾上它（Antigravity 这种会外参与者，会议室不主动叫，")
            print("  是你在 Antigravity 里让它用 read_room / post_to_room 发言）。")
        else:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
