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


def main():
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
    from bridge.web import main as run
    run()


if __name__ == "__main__":
    main()
