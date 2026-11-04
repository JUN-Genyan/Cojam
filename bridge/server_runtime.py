"""Reuse a matching backend; upgrade an idle backend from this installation only."""
import json
import os
import re
import sys
import time
import urllib.request

from .version import VERSION, API_REVISION


def installation():
    from .common import HERE
    return os.path.realpath(sys.executable if getattr(sys, 'frozen', False) else HERE)


def reuse_or_stop(url):
    from .common import HERE, LOG_DIR
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path, token=None, body=None):
        headers = {"X-RT-Token": token} if token else {}
        req = urllib.request.Request(url.rstrip("/") + path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        with opener.open(req, timeout=3) as response:
            return response.read()
    try:
        info = json.loads(request("/api/server/info"))
        if info.get("app") != "AI Bridge":
            raise ValueError("another application")
        html = request("/").decode("utf-8")
        match = re.search(r'name="rt-token" content="([^"]+)"', html)
        if not match:
            raise ValueError("owner page unavailable")
        token = match.group(1)
        runtime = json.loads(request("/api/server/runtime", token))
        same_path = lambda a, b: os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
        if not same_path(runtime.get("install", ""), installation()) or not same_path(runtime.get("data", ""), LOG_DIR):
            return "error", "端口上已有另一份合拍 Cojam。请先退出原来的程序，再打开这一份，以免混用会话数据。"
        if info.get("version") == VERSION and info.get("api_revision") == API_REVISION:
            return "reuse", ""
        request("/api/server/stop", token, {})
        for _ in range(30):
            time.sleep(.1)
            try:
                request("/api/server/info")
            except OSError:
                return "stopped", ""
        return "error", "旧后台尚未退出，请稍后重新打开合拍 Cojam。"
    except Exception as e:
        if getattr(e, "code", None) == 409:
            return "error", "旧后台还有任务运行或排队。请等待任务结束，再重新打开合拍 Cojam 更新。"
        return "error", "端口上仍有旧后台或其他程序，当前版本无法启动。请先退出原来运行的合拍 Cojam（关闭网页不会退出后台），再重新打开。不要继续在旧网页里配置接入。"


def show_startup_error(message):
    print(message)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("合拍 Cojam 启动未完成", message, parent=root)
        root.destroy()
    except Exception:
        pass
