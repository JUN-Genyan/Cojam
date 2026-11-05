"""Orchestrate the optional native window around the Cojam backend."""
import ctypes
import inspect
import os
import sys
import webbrowser

from . import backend

TITLE = "合拍 Cojam"
BUSY_TEXT = "还有任务正在运行或排队。现在关闭窗口，如果这是最后一个合拍页面，任务会中断。确定关闭吗？"


def base_url():
    return "http://127.0.0.1:%s" % os.environ.get("ROUNDTABLE_PORT", "8765")


def _message_box(text, flags):
    return ctypes.windll.user32.MessageBoxW(None, text, TITLE, flags)


def show_error(text):
    try:
        _message_box(text, 0x10)
    except Exception:
        print(text, file=sys.stderr)


def show_confirm(text):
    try:
        return _message_box(text, 0x04 | 0x30 | 0x100) == 6
    except Exception:
        return True


def confirm_close(base, token, ask=None):
    if not backend.busy(base, token):
        return True
    return (ask or show_confirm)(BUSY_TEXT)


def open_window(url, on_closing, module=None):
    webview = module or __import__("webview")
    settings = getattr(webview, "settings", None)
    if isinstance(settings, dict) and "OPEN_EXTERNAL_LINKS_IN_BROWSER" in settings:
        settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    window = webview.create_window(TITLE, url, width=1280, height=860, min_size=(960, 640))
    window.events.closing += on_closing
    kwargs = {}
    try:
        if "open_external_links_in_browser" in inspect.signature(webview.start).parameters:
            kwargs["open_external_links_in_browser"] = True
    except (TypeError, ValueError):
        pass
    webview.start(**kwargs)


def main():
    base = base_url()
    core = backend.find_core()
    if core is None:
        show_error("找不到 Cojam.exe。请把 CojamDesktop.exe 和 Cojam.exe 放在同一个文件夹里。")
        return 2
    state, info = backend.probe(base)
    if state in ("foreign", "timeout"):
        show_error("端口 %s 被其他程序占用，合拍无法启动。" % os.environ.get("ROUNDTABLE_PORT", "8765"))
        return 2
    proc = None
    if state != "cojam":
        command, cwd = core
        try:
            proc = backend.spawn_core(command, cwd)
        except OSError as error:
            show_error("合拍后台没有启动成功。\n%s" % error)
            return 2
        info = backend.wait_ready(base, proc)
        if info is None:
            backend.stop_process(proc)
            show_error("合拍后台没有启动成功。日志：%s" % backend.log_path(cwd))
            return 2
    try:
        token = backend.fetch_token(base)
    except Exception:
        backend.stop_process(proc)
        show_error("已运行的合拍后台版本不兼容，请先退出旧版再打开桌面版。")
        return 2
    tab = backend.new_tab_id()
    heartbeat = backend.TabHeartbeat(base, token, tab)
    heartbeat.start()
    url = base + "/?owner_tab=" + tab

    def on_closing():
        if not confirm_close(base, token):
            return False
        heartbeat.stop()
        return True

    try:
        open_window(url, on_closing)
    except Exception:
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False
        if not opened:
            heartbeat.stop()
            show_error("没能打开桌面窗口，也没能打开浏览器。")
            return 2
        show_error("没能打开桌面窗口（可能缺少 Microsoft Edge WebView2）。已改用浏览器打开合拍。")
        return 0
    heartbeat.stop()
    return 0
