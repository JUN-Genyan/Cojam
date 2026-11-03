# -*- coding: utf-8 -*-
r"""验一个 agent 能不能接上：拿一句无伤大雅的问话真跑一次，看回复和会话 id 对不对。

    python -m bridge.doctor              列出读到的 agent 和状态
    python -m bridge.doctor gemini       真跑一次（会花掉一点额度）
    python -m bridge.doctor gemini "你好"  换个问法

自己加一个 agent 之后先用它验一遍：命令行拼错、输出格式写错，在这里就能看出来，
不用开会、不用占着会议室。所有配置（bin / args / parse）的写法见 docs/PROVIDERS.md。
"""
import sys, time

from . import drivers, registry
from .common import LOG_DIR


def _show(spec):
    bits = [spec["id"], "（%s）" % spec["label"] if spec["label"] != spec["id"] else "",
            "driver=%s" % spec["driver"], "启用" if spec["enabled"] else "没启用",
            "实测过" if spec["verified"] else "没实测"]
    print("  " + "  ".join(x for x in bits if x))
    if spec["bin"]:
        exe = registry.exe_of(spec)
        print("    命令：%s" % (" ".join(exe) if exe else "找不到（PATH 里没有：%s）" % "、".join(spec["bin"])))
    if spec["driver"] == "http":
        print("    服务：%s" % spec["endpoint"])
    if spec["note"]:
        print("    说明：%s" % spec["note"])
    if spec["source"]:
        print("    来自：%s" % spec["source"])


def main(argv):
    registry.load()
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        print("读到的 agent（数据目录：%s）\n" % LOG_DIR)
        for s in registry.all_specs():
            _show(s)
        if registry.ERRORS:
            print("\n有配置读不了（不影响别的）：")
            for e in registry.ERRORS:
                print("  " + e)
        print("\n验一个：python -m bridge.doctor <id>")
        return 0
    pid = args[0].lower()
    spec = registry.get(pid)
    if not spec:
        print("没有这个 agent：%s。能用的：%s" % (pid, "、".join(s["id"] for s in registry.all_specs())))
        return 2
    print("验 %s：\n" % pid)
    _show(spec)
    if not spec["enabled"]:
        print("\n它现在是关的（命令找不到，或者配置里写了 enabled=false），不跑。")
        return 1
    if spec["driver"] in ("claude", "codex"):
        print("\n这是内置驱动，会议室里直接用；这里不单独跑（它要会议室给钥匙、要会话目录）。")
        return 0
    prompt = args[1] if len(args) > 1 else "用一句话说你好，并说明你是哪个模型。"
    print("\n发出去的问题：%s" % prompt)
    t0 = time.time()
    ok, text, sid, _edits = drivers.call(pid, prompt, None, (), "discuss", None)
    print("用时 %.1fs" % (time.time() - t0))
    print("结果：%s" % ("成功" if ok else "失败"))
    if sid:
        print("会话 id：%s" % sid)
    print("回复：\n%s" % (text or "（空）"))
    if not ok:
        print("\n看看 docs/PROVIDERS.md 里 args / parse 那一节，把 %s 调一下。" % os.path.basename(spec["source"] or "配置"))
        return 1
    return 0


def cli():
    """pip 装完之后的控制台命令：ai-bridge-doctor"""
    raise SystemExit(main(sys.argv[1:]))


if __name__ == "__main__":
    import os
    sys.exit(main(sys.argv[1:]))
