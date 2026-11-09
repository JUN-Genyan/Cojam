# -*- coding: utf-8 -*-
"""AI Bridge 会议室的启动入口（桌面快捷方式 open.pyw、start.bat、打出来的 AIBridge.exe 都跑它）。

    roundtable.py                 开会话室（默认）
    roundtable.py install [id]    把会议室接进别的工具（Antigravity / Cursor / Claude Code / Codex / Gemini CLI）
    roundtable.py join            把"别人怎么接进来"打出来（能直接贴的配置、能直接跑的命令）
    roundtable.py doctor [id]     验一个 agent 接没接上
    roundtable.py agents          列出读到的所有 agent

代码在 bridge 包里，见 README 的"代码结构"。"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _attach_console():
    """GUI 程序从终端启动时接回父控制台，命令行子命令才有输出。"""
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):
            return False
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        return True
    except (AttributeError, OSError):
        return False


def ensure_stdio():
    """没控制台时（双击打包好的应用）：先接父控制台，接不上就把输出写进 data/cojam.log。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    if _attach_console():
        return
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.environ.get("ROUNDTABLE_LOG_DIR") or os.path.join(base, "data")
    try:
        os.makedirs(log_dir, exist_ok=True)
        stream = open(os.path.join(log_dir, "cojam.log"), "a", encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stderr = stream


def main():
    ensure_stdio()
    args = sys.argv[1:]
    cmd = args[0].lower() if args else ""
    if cmd == "member":
        from bridge.member import main as member_main
        raise SystemExit(member_main(args[1:]))
    if cmd == "member-ui":
        from bridge.member_ui import main as member_ui
        raise SystemExit(member_ui())
    if cmd == "hook-codex":
        from bridge.hook_codex import main as hook
        return hook()
    if cmd in ("install", "join", "doctor", "agents"):
        from bridge import install, doctor, registry
        if cmd == "install":
            raise SystemExit(install.main(args[1:]))
        if cmd == "join":
            raise SystemExit(install.join(args[1:]))
        if cmd == "doctor":
            raise SystemExit(doctor.main(args[1:]))
        registry.load()
        for s in registry.all_specs():
            print("%-12s %-8s %s" % (s["id"], s["driver"], "启用" if s["enabled"] else "没启用"))
        raise SystemExit(0)
    # Publish the PATH-visible agents straight away.  The detailed discovery
    # (Start Menu shortcuts, package managers and alternate install roots) is
    # intentionally asynchronous so opening the desktop app cannot hang.
    from bridge import registry
    registry.load(fast=True)
    registry.request_scan()
    from bridge.web import main as run
    run()


if __name__ == "__main__":
    main()
