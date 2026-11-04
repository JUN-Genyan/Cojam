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
使用联系人接入密钥，只能访问受邀会话。

改别人的配置文件之前一律先备份成 <文件>.bak-<时间>；文件里有别的东西就合并，不覆盖。
"""
import json, os, re, shutil, subprocess, sys, time

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
    token = host.get("token") or os.environ.get("AI_BRIDGE_TOKEN")
    if token:
        servers[SERVER_NAME]["headers"] = {"Authorization": "Bearer " + token}
    data["mcpServers"] = servers
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return True, "写好了：%s%s" % (path, ("（原文件备份在 %s）" % os.path.basename(bak)) if bak else "")


def install_claude(host):
    exe = shutil.which("claude")
    if not exe:
        return False, "找不到 claude 命令。手动接的话：\n  claude mcp add --transport http %s %s" % (SERVER_NAME, URL)
    cmd = [exe, "mcp", "add", "--transport", "http", SERVER_NAME, URL]
    token = host.get("token") or os.environ.get("AI_BRIDGE_TOKEN")
    if token and installed(host):
        return False, "Claude 已有 ai-bridge 配置，请在 Claude 的 MCP 设置中更新 Authorization 密钥；或移除该项后重新配置"
    if token:
        cmd += ["--header", "Authorization: Bearer " + token]
    r = subprocess.run(cmd,
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
    token = host.get("token") or os.environ.get("AI_BRIDGE_TOKEN")
    if token:
        block += 'http_headers = { Authorization = %s }\n' % json.dumps("Bearer " + token)
    txt = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    if "[mcp_servers.%s]" % SERVER_NAME in txt:
        if not token:
            return True, "已经接过了（%s 里有 [mcp_servers.%s]）；请在联系人接入页复制含密钥的配置" % (path, SERVER_NAME)
        if "[mcp_servers.%s." % SERVER_NAME in txt:
            return False, "已有嵌套配置，请在工具中手动更新 Authorization 密钥"
        bak = _backup(path)
        pattern = r'(?ms)^\[mcp_servers\.ai-bridge\][^\n]*\n.*?(?=^\[|\Z)'
        section = re.search(pattern, txt)
        if not section:
            return False, "无法识别已有的 ai-bridge 配置，请手动更新"
        old = section.group(0)
        header = re.search(r'(?m)^http_headers\s*=\s*(.*)$', old)
        if header and not header.group(1).rstrip().endswith("}"):
            return False, "已有多行认证配置，请手动更新 Authorization"
        rest = re.sub(r'(?m)^(?:url|http_headers|bearer_token_env_var)\s*=.*\n?', '', old.split('\n', 1)[1])
        replacement = block.lstrip() + rest + ('\n' if rest and not rest.endswith('\n') else '')
        txt = txt[:section.start()] + replacement + txt[section.end():]
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt)
        return True, "已更新接入密钥，原配置已备份：%s" % bak
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
    """Print authenticated external connection instructions without exposing credentials."""
    base = "http://127.0.0.1:%d" % PORT
    alive = False
    try:
        import urllib.request
        with urllib.request.urlopen(base + "/join", timeout=2) as response:
            alive = response.status == 200
    except Exception:
        pass
    print("AI Bridge %s: %s" % ("正在运行" if alive else "尚未启动", base))
    print("1. 本机首页：参与人 ＋ → 添加联系人 → 接入 → 生成接入密钥。")
    print("2. 在会话的成员与权限中邀请该联系人并设置权限。")
    print("3. 其他用户打开 %s/join 并输入密钥。" % base)
    print("4. Harness 使用下面的 MCP 配置（将占位符替换为密钥）：")
    print(json.dumps({"mcpServers": {SERVER_NAME: {"url": URL,
          "headers": {"Authorization": "Bearer <联系人接入密钥>"}}}}, ensure_ascii=False, indent=2))
    print("HTTP: Authorization: Bearer <联系人接入密钥>")
    print("GET  %s/api/external/rooms" % base)
    print('POST %s/api/post {"room":"受邀会话 ID","text":"消息"}' % base)
    print("身份由密钥决定，不能自行加入会话或冒用其他联系人。")
    print("CLI 安装前可先设置 AI_BRIDGE_TOKEN 环境变量；或在接入页一键配置。")
    print("完整说明：docs/CONTACTS.md；示例客户端：examples/contact_client.py")
    return 0 if alive else 1


def hosts_status():
    """界面上"一键接入"那一栏用：每个宿主装没装、接没接。"""
    return [{"id": h["id"], "label": h["label"], "present": present(h), "installed": bool(installed(h))}
            for h in HOSTS]


def host_install(hid, token=None):
    """界面点一下就走这里：不让人碰命令行。返回 (ok, 说明)。"""
    host = next((h for h in HOSTS if h["id"] == hid), None)
    if not host:
        return False, "不认识这个工具：%s" % hid
    if not present(host):
        return False, "这台机器上没装 %s" % host["label"]
    ok, detail = INSTALLERS[host["kind"]](dict(host, token=token))
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
