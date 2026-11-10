"""Orchestrate the optional native window around the Cojam backend."""
import ctypes
import inspect
import os
import subprocess
import sys
import webbrowser

from . import backend

TITLE = "合拍 Cojam"
BUSY_TEXT = "还有任务正在运行或排队。现在关闭窗口，如果这是最后一个合拍页面，任务会中断。确定关闭吗？"


def base_url():
    return "http://127.0.0.1:%s" % os.environ.get("ROUNDTABLE_PORT", "8765")


def _message_box(text, flags):
    if sys.platform == "darwin":
        script = 'on run argv\n display dialog (item 1 of argv) with title "合拍 Cojam" buttons {"取消", "确定"} default button "确定"\nend run' if flags & 0x04 else 'on run argv\n display alert "合拍 Cojam" message (item 1 of argv)\nend run'
        result = subprocess.run(["/usr/bin/osascript", "-e", script, text], capture_output=True)
        return 6 if result.returncode == 0 else 2
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


def _system_dark():
    """Windows 应用主题是否为深色；页面的 prefers-color-scheme 跟随同一个设置。"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        try:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


def _colorref(hex_color):
    """'#1b1b1e' → COLORREF（DWM 要 BGR 字节序）。"""
    text = str(hex_color).lstrip("#")
    r, g, b = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    return ctypes.c_uint((b << 16) | (g << 8) | r)


def _window_hwnd():
    """找本进程里标题为主窗口的那个句柄；找不到返回 None。"""
    import ctypes.wintypes as wt
    user32 = ctypes.windll.user32
    found = []
    pid = os.getpid()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def collect(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            owner = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if title.value == TITLE and owner.value == pid:
                found.append(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    return found[0] if found else None


def tint_title_bar(_window=None):
    """Windows 11：标题栏 / 边框 / 标题文字染成与应用一致的深浅主题色。

    与 index.html 的 --bar/--fg/--line 一一对应；旧系统不支持这些属性时静默忽略。
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = _window_hwnd()
        if not hwnd:
            return
        dark = _system_dark()
        dwm = ctypes.windll.dwmapi

        def paint(attribute, value):
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), 4)

        paint(20, ctypes.c_uint(1 if dark else 0))             # 深色标题按钮
        paint(35, _colorref("#1b1b1e" if dark else "#ffffff"))  # 标题栏 = --bar
        paint(36, _colorref("#ececf1" if dark else "#18181b"))  # 标题文字 = --fg
        paint(34, _colorref("#2c2c31" if dark else "#e7e7ea"))  # 窗口边框 = --line
    except Exception:
        pass


class DesktopApi:
    """Native actions exposed only to the desktop window."""
    def __init__(self, webview):
        # pywebview recursively inspects public attributes when exposing its JS API.
        # Keep implementation objects private so it only discovers our method.
        self._webview = webview
        self._window = None

    def choose_workdir(self):
        if self._window is None:
            raise RuntimeError("桌面窗口尚未准备好")
        kinds = getattr(self._webview, "FileDialog", None)
        folder = kinds.FOLDER if kinds else self._webview.FOLDER_DIALOG
        result = self._window.create_file_dialog(folder, allow_multiple=False)
        return str(result[0]) if result else None


def open_window(url, on_closing, module=None):
    webview = module or __import__("webview")
    settings = getattr(webview, "settings", None)
    if isinstance(settings, dict) and "OPEN_EXTERNAL_LINKS_IN_BROWSER" in settings:
        settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    api = DesktopApi(webview)
    window = webview.create_window(TITLE, url, width=1280, height=860, min_size=(960, 640),
                                   text_select=True, js_api=api)
    api._window = window
    window.events.closing += on_closing
    window.events.shown += tint_title_bar
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
        show_error("找不到合拍后台。请确认应用包完整；Windows 版需要把 Cojam.exe 和 CojamCore.exe 放在同一个文件夹里。")
        return 2
    state, info = backend.probe(base)
    if state in ("foreign", "timeout"):
        show_error("端口 %s 被其他程序占用，合拍无法启动。" % os.environ.get("ROUNDTABLE_PORT", "8765"))
        return 2
    if state == "cojam" and not backend.ui_ready(base):
        # A half-extracted backend answers /api/* but serves no CSS/JS. Ask the
        # core to replace it instead of opening an unstyled, scriptless page.
        state = "free"
    proc = None
    if state != "cojam":
        command, cwd = core
        try:
            proc = backend.spawn_core(command, cwd)
        except OSError as error:
            show_error("合拍后台没有启动成功。\n%s" % error)
            return 2
        info = backend.wait_ready(base, proc)
        if info is None or not backend.ui_ready(base):
            backend.stop_process(proc)
            show_error("合拍后台的界面文件不完整，无法打开。请完全退出合拍 Cojam 后重新打开；"
                       "如果仍然失败，请重新安装应用。日志：%s" % backend.log_path(cwd))
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
        show_error("没能打开桌面窗口。已改用浏览器打开合拍。")
        return 0
    heartbeat.stop()
    return 0
