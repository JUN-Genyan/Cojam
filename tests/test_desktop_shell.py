# -*- coding: utf-8 -*-
"""Desktop shell backend: discovery, token, owner-tab heartbeat and busy detection."""
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_shell import backend
from desktop_shell import main as shell_main


class State:
    app = "AI Bridge"
    token = "test-token"
    rooms = []
    delay = 0
    rooms_error = False
    assets = True
    calls = []


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload=None, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else (
            json.dumps(payload).encode() if payload is not None else b"")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if State.delay:
            time.sleep(State.delay)
        if self.path == "/api/server/info":
            return self._send(200, {"app": State.app, "version": "2.5.1"})
        if self.path == "/":
            html = '<meta name="rt-token" content="%s">' % State.token
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if self.path.startswith("/static/"):
            if State.assets:
                return self._send(200, b"asset", "text/plain")
            return self._send(404, b"not found", "text/plain")
        if self.path == "/api/rooms":
            if self.headers.get("X-RT-Token") != State.token:
                return self._send(403, {"error": "forbidden"})
            if State.rooms_error:
                return self._send(500, {"error": "boom"})
            return self._send(200, {"rooms": State.rooms})
        return self._send(404, {"error": "missing"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        State.calls.append({"path": self.path, "token": self.headers.get("X-RT-Token"),
                            "body": json.loads(body or b"{}")})
        return self._send(200, {"ok": True})


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


class ShellTest(unittest.TestCase):
    def setUp(self):
        State.app = "AI Bridge"
        State.token = "test-token"
        State.rooms = []
        State.delay = 0
        State.rooms_error = False
        State.assets = True
        State.calls = []
        self.server = QuietServer(("127.0.0.1", 0), FakeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_probe_recognizes_cojam(self):
        state, info = backend.probe(self.base)
        self.assertEqual(state, "cojam")
        self.assertEqual(info["app"], "AI Bridge")

    def test_probe_marks_other_app_as_foreign(self):
        State.app = "Other"
        self.assertEqual(backend.probe(self.base), ("foreign", None))

    def test_probe_free_port(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.assertEqual(backend.probe("http://127.0.0.1:%d" % port), ("free", None))

    def test_probe_timeout(self):
        State.delay = .3
        self.assertEqual(backend.probe(self.base, timeout=.05), ("timeout", None))

    def test_fetch_token(self):
        self.assertEqual(backend.fetch_token(self.base), "test-token")
        State.token = ""
        with self.assertRaises(RuntimeError):
            backend.fetch_token(self.base)

    def test_ui_ready_checks_static_bundle(self):
        self.assertTrue(backend.ui_ready(self.base))
        State.assets = False
        self.assertFalse(backend.ui_ready(self.base))

    def test_post_session_body_and_header(self):
        backend.post_session(self.base, "test-token", "a" * 32, "open", "a" * 32)
        call = State.calls[-1]
        self.assertEqual(call["path"], "/api/ui/session")
        self.assertEqual(call["token"], "test-token")
        self.assertEqual(call["body"], {"tab": "a" * 32, "action": "open", "instance": "a" * 32})

    def test_heartbeat_open_ping_close_and_idempotent_stop(self):
        beat = backend.TabHeartbeat(self.base, "test-token", "b" * 32, interval=.02)
        beat.start()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            actions = [call["body"]["action"] for call in State.calls]
            if "ping" in actions:
                break
            time.sleep(.01)
        beat.stop()
        beat.stop()
        actions = [call["body"]["action"] for call in State.calls]
        self.assertEqual(actions[0], "open")
        self.assertIn("ping", actions)
        self.assertEqual(actions.count("close"), 1)
        self.assertEqual(actions[-1], "close")
        self.assertIsNone(beat.last_error)
        self.assertEqual(beat.failures, 0)

    def test_busy(self):
        State.rooms = [{"running": ["task"], "queued": 0}]
        self.assertTrue(backend.busy(self.base, "test-token"))
        State.rooms = [{"running": [], "queued": 2}]
        self.assertTrue(backend.busy(self.base, "test-token"))
        State.rooms = [{"running": [], "queued": 0}]
        self.assertFalse(backend.busy(self.base, "test-token"))
        State.rooms_error = True
        self.assertFalse(backend.busy(self.base, "test-token"))
        self.assertFalse(backend.busy(self.base, "wrong-token"))

    def test_new_tab_id_is_valid_and_unique(self):
        first, second = backend.new_tab_id(), backend.new_tab_id()
        self.assertRegex(first, r"^[A-Za-z0-9_-]{16,80}$")
        self.assertNotEqual(first, second)

    def test_wait_ready_uses_existing_backend(self):
        info = backend.wait_ready(self.base, timeout=.5, interval=.01)
        self.assertEqual(info["app"], "AI Bridge")

    def test_wait_ready_reuses_backend_after_child_exit(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait(timeout=5)
        self.assertIsNotNone(proc.poll())
        info = backend.wait_ready(self.base, proc, timeout=.5, interval=.01)
        self.assertEqual(info["app"], "AI Bridge")

    def test_wait_ready_times_out(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.assertIsNone(backend.wait_ready("http://127.0.0.1:%d" % port, timeout=.15, interval=.01))

    def test_spawn_core_sets_desktop_environment(self):
        with patch.object(backend.subprocess, "Popen") as popen:
            popen.return_value = object()
            backend.spawn_core([sys.executable, "-c", "pass"], str(ROOT))
        args, kwargs = popen.call_args
        self.assertEqual(args[0][0], sys.executable)
        self.assertEqual(kwargs["cwd"], str(ROOT))
        self.assertEqual(kwargs["env"]["ROUNDTABLE_NO_BROWSER"], "1")
        self.assertEqual(kwargs["env"]["ROUNDTABLE_OWNER_LIFECYCLE"], "1")
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("stderr", kwargs)
        if os.name == "nt":
            self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
            self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            self.assertEqual(kwargs["creationflags"], 0)

    def test_stop_process_terminates_running_child(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        backend.stop_process(proc)
        self.assertIsNotNone(proc.poll())

    def test_log_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"ROUNDTABLE_LOG_DIR": tmp}):
                self.assertEqual(backend.log_path("C:/core"), os.path.join(tmp, "cojam.log"))
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(backend.log_path("C:/core"),
                                 os.path.join("C:/core", "data", "cojam.log"))


class FakeEvent:
    def __init__(self):
        self.items = []

    def __iadd__(self, handler):
        self.items.append(handler)
        return self


class FakeEvents:
    def __init__(self):
        self.closing = FakeEvent()
        self.shown = FakeEvent()


class FakeWindow:
    def __init__(self):
        self.events = FakeEvents()


class FakeWebview:
    def __init__(self):
        self.settings = {"OPEN_EXTERNAL_LINKS_IN_BROWSER": False}
        self.created = None
        self.started = False

    def create_window(self, title, url, **kwargs):
        self.created = dict(title=title, url=url, **kwargs)
        self.window = FakeWindow()
        return self.window

    def start(self, **kwargs):
        self.started = kwargs


class WindowAndFlow(unittest.TestCase):
    def setUp(self):
        self.ui_ready = patch.object(shell_main.backend, "ui_ready", return_value=True)
        self.ui_ready.start()
        self.addCleanup(self.ui_ready.stop)

    def test_folder_picker_returns_selected_path_and_cancel(self):
        webview = Mock()
        webview.FileDialog.FOLDER = 20
        api = shell_main.DesktopApi(webview)
        api._window = Mock()
        api._window.create_file_dialog.side_effect = [(r'D:\project',), None]
        self.assertEqual(api.choose_workdir(), r'D:\project')
        self.assertIsNone(api.choose_workdir())
        api._window.create_file_dialog.assert_called_with(20, allow_multiple=False)

    def test_only_folder_picker_is_public_to_js_bridge(self):
        api = shell_main.DesktopApi(Mock())
        api._window = Mock()
        self.assertEqual([name for name in dir(api) if not name.startswith('_')], ['choose_workdir'])

    def test_open_window_uses_native_window_settings(self):
        fake = FakeWebview()
        handler = Mock()
        shell_main.open_window("http://127.0.0.1:8765/?owner_tab=" + "a" * 32, handler, module=fake)
        self.assertEqual(fake.created["title"], "合拍 Cojam")
        self.assertEqual(fake.created["url"], "http://127.0.0.1:8765/?owner_tab=" + "a" * 32)
        self.assertEqual((fake.created["width"], fake.created["height"]), (1280, 860))
        self.assertEqual(fake.created["min_size"], (960, 640))
        self.assertTrue(fake.created["text_select"])
        self.assertEqual(fake.window.events.closing.items, [handler])
        self.assertEqual(fake.window.events.shown.items, [shell_main.tint_title_bar])
        self.assertTrue(fake.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"])
        self.assertEqual(fake.started, {})

    def test_confirm_close_skips_when_idle(self):
        with patch.object(shell_main.backend, "busy", return_value=False), \
                patch.object(shell_main, "show_confirm") as ask:
            self.assertTrue(shell_main.confirm_close("http://base", "token"))
        ask.assert_not_called()

    def test_confirm_close_asks_when_busy(self):
        with patch.object(shell_main.backend, "busy", return_value=True), \
                patch.object(shell_main, "show_confirm", return_value=False) as ask:
            self.assertFalse(shell_main.confirm_close("http://base", "token"))
        ask.assert_called_once()
        with patch.object(shell_main.backend, "busy", return_value=True), \
                patch.object(shell_main, "show_confirm", return_value=True):
            self.assertTrue(shell_main.confirm_close("http://base", "token"))

    def test_missing_core(self):
        with patch.object(shell_main.backend, "find_core", return_value=None), \
                patch.object(shell_main.backend, "probe") as probe, \
                patch.object(shell_main, "show_error") as error:
            self.assertEqual(shell_main.main(), 2)
        probe.assert_not_called()
        self.assertIn("同一个文件夹", error.call_args.args[0])

    def test_foreign_or_timeout_port_is_rejected(self):
        for state in ("foreign", "timeout"):
            with self.subTest(state=state), \
                    patch.object(shell_main.backend, "find_core", return_value=(["core"], "C:/core")), \
                    patch.object(shell_main.backend, "probe", return_value=(state, None)), \
                    patch.object(shell_main.backend, "spawn_core") as spawn, \
                    patch.object(shell_main, "show_error") as error:
                self.assertEqual(shell_main.main(), 2)
                spawn.assert_not_called()
                self.assertIn("占用", error.call_args.args[0])

    def test_free_port_starts_core_and_opens_window(self):
        base = shell_main.base_url()
        heartbeat = Mock()
        with patch.object(shell_main.backend, "find_core", return_value=(["core.exe"], "C:/core")), \
                patch.object(shell_main.backend, "probe", return_value=("free", None)), \
                patch.object(shell_main.backend, "spawn_core", return_value=Mock()) as spawn, \
                patch.object(shell_main.backend, "wait_ready", return_value={"app": "AI Bridge"}), \
                patch.object(shell_main.backend, "fetch_token", return_value="token"), \
                patch.object(shell_main.backend, "new_tab_id", return_value="T" * 32), \
                patch.object(shell_main.backend, "TabHeartbeat", return_value=heartbeat) as beat_class, \
                patch.object(shell_main, "open_window") as window, \
                patch.object(shell_main, "show_error") as error:
            self.assertEqual(shell_main.main(), 0)
        spawn.assert_called_once_with(["core.exe"], "C:/core")
        self.assertEqual(beat_class.call_args.args, (base, "token", "T" * 32))
        heartbeat.start.assert_called_once()
        self.assertEqual(window.call_args.args[0], base + "/?owner_tab=" + "T" * 32)
        self.assertEqual(heartbeat.stop.call_count, 1)
        error.assert_not_called()

    def test_existing_backend_is_reused(self):
        with patch.object(shell_main.backend, "find_core", return_value=(["core.exe"], "C:/core")), \
                patch.object(shell_main.backend, "probe", return_value=("cojam", {"app": "AI Bridge"})), \
                patch.object(shell_main.backend, "spawn_core") as spawn, \
                patch.object(shell_main.backend, "fetch_token", return_value="token"), \
                patch.object(shell_main.backend, "TabHeartbeat", return_value=Mock()), \
                patch.object(shell_main, "open_window"), \
                patch.object(shell_main, "show_error") as error:
            self.assertEqual(shell_main.main(), 0)
        spawn.assert_not_called()
        error.assert_not_called()

    def test_existing_backend_without_ui_assets_is_replaced(self):
        proc = Mock()
        with patch.object(shell_main.backend, "find_core", return_value=(["core.exe"], "C:/core")), \
                patch.object(shell_main.backend, "probe", return_value=("cojam", {"app": "AI Bridge"})), \
                patch.object(shell_main.backend, "ui_ready", side_effect=[False, True]), \
                patch.object(shell_main.backend, "spawn_core", return_value=proc) as spawn, \
                patch.object(shell_main.backend, "wait_ready", return_value={"app": "AI Bridge"}), \
                patch.object(shell_main.backend, "fetch_token", return_value="token"), \
                patch.object(shell_main.backend, "TabHeartbeat", return_value=Mock()), \
                patch.object(shell_main, "open_window"), \
                patch.object(shell_main, "show_error") as error:
            self.assertEqual(shell_main.main(), 0)
        spawn.assert_called_once_with(["core.exe"], "C:/core")
        error.assert_not_called()

    def test_backend_that_stays_without_ui_assets_fails_clearly(self):
        proc = Mock()
        with patch.object(shell_main.backend, "find_core", return_value=(["core.exe"], "C:/core")), \
                patch.object(shell_main.backend, "probe", return_value=("cojam", {"app": "AI Bridge"})), \
                patch.object(shell_main.backend, "ui_ready", return_value=False), \
                patch.object(shell_main.backend, "spawn_core", return_value=proc), \
                patch.object(shell_main.backend, "wait_ready", return_value={"app": "AI Bridge"}), \
                patch.object(shell_main.backend, "stop_process") as stop, \
                patch.object(shell_main.backend, "log_path", return_value="C:/core/data/cojam.log"), \
                patch.object(shell_main, "open_window") as window, \
                patch.object(shell_main, "show_error") as error:
            self.assertEqual(shell_main.main(), 2)
        stop.assert_called_once_with(proc)
        window.assert_not_called()
        self.assertIn("界面文件不完整", error.call_args.args[0])

    def test_backend_start_failure_stops_spawned_process(self):
        proc = Mock()
        with patch.object(shell_main.backend, "find_core", return_value=(["core.exe"], "C:/core")), \
                patch.object(shell_main.backend, "probe", return_value=("free", None)), \
                patch.object(shell_main.backend, "spawn_core", return_value=proc), \
                patch.object(shell_main.backend, "wait_ready", return_value=None), \
                patch.object(shell_main.backend, "stop_process") as stop, \
                patch.object(shell_main.backend, "log_path", return_value="C:/core/data/cojam.log"), \
                patch.object(shell_main, "show_error") as error:
            self.assertEqual(shell_main.main(), 2)
        stop.assert_called_once_with(proc)
        self.assertIn("C:/core/data/cojam.log", error.call_args.args[0])

    def test_token_failure_stops_spawned_process(self):
        proc = Mock()
        with patch.object(shell_main.backend, "find_core", return_value=(["core.exe"], "C:/core")), \
                patch.object(shell_main.backend, "probe", return_value=("free", None)), \
                patch.object(shell_main.backend, "spawn_core", return_value=proc), \
                patch.object(shell_main.backend, "wait_ready", return_value={"app": "AI Bridge"}), \
                patch.object(shell_main.backend, "fetch_token", side_effect=RuntimeError("old")), \
                patch.object(shell_main.backend, "stop_process") as stop, \
                patch.object(shell_main, "show_error") as error:
            self.assertEqual(shell_main.main(), 2)
        stop.assert_called_once_with(proc)
        self.assertIn("版本不兼容", error.call_args.args[0])

    def test_window_failure_falls_back_to_browser(self):
        heartbeat = Mock()
        with patch.object(shell_main.backend, "find_core", return_value=(["core.exe"], "C:/core")), \
                patch.object(shell_main.backend, "probe", return_value=("cojam", {"app": "AI Bridge"})), \
                patch.object(shell_main.backend, "fetch_token", return_value="token"), \
                patch.object(shell_main.backend, "TabHeartbeat", return_value=heartbeat), \
                patch.object(shell_main, "open_window", side_effect=ImportError("no webview")), \
                patch.object(shell_main.webbrowser, "open", return_value=True) as browser, \
                patch.object(shell_main, "show_error") as error:
            self.assertEqual(shell_main.main(), 0)
        browser.assert_called_once()
        heartbeat.stop.assert_not_called()
        self.assertIn("已改用浏览器", error.call_args.args[0])

    def test_window_and_browser_failure_unregisters_tab(self):
        heartbeat = Mock()
        with patch.object(shell_main.backend, "find_core", return_value=(["core.exe"], "C:/core")), \
                patch.object(shell_main.backend, "probe", return_value=("cojam", {"app": "AI Bridge"})), \
                patch.object(shell_main.backend, "fetch_token", return_value="token"), \
                patch.object(shell_main.backend, "TabHeartbeat", return_value=heartbeat), \
                patch.object(shell_main, "open_window", side_effect=RuntimeError("boom")), \
                patch.object(shell_main.webbrowser, "open", return_value=False), \
                patch.object(shell_main, "show_error") as error:
            self.assertEqual(shell_main.main(), 2)
        heartbeat.stop.assert_called_once()
        self.assertIn("浏览器", error.call_args.args[0])


class FindCore(unittest.TestCase):
    def test_source_mode_points_at_repo_script(self):
        found = backend.find_core(frozen=False, executable=sys.executable)
        self.assertIsNotNone(found)
        command, cwd = found
        self.assertEqual(cwd, str(ROOT))
        self.assertEqual(Path(command[-1]), ROOT / "roundtable.py")
        self.assertTrue(Path(command[-1]).is_file())

    def test_packaged_mode_uses_sibling_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            core = Path(tmp) / "Cojam.exe"
            core.write_bytes(b"core")
            found = backend.find_core(frozen=True, executable=str(Path(tmp) / "CojamDesktop.exe"))
            self.assertEqual(found, ([str(core.resolve())], str(Path(tmp).resolve())))

    def test_packaged_mode_prefers_core_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            shell = Path(tmp) / "Cojam.exe"
            core = Path(tmp) / "CojamCore.exe"
            shell.write_bytes(b"shell")
            core.write_bytes(b"core")
            found = backend.find_core(frozen=True, executable=str(shell))
            self.assertEqual(found, ([str(core.resolve())], str(Path(tmp).resolve())))

    def test_packaged_mode_does_not_spawn_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Cojam.exe").write_bytes(b"shell")
            self.assertIsNone(backend.find_core(frozen=True, executable=str(Path(tmp) / "Cojam.exe")))

    def test_packaged_mode_without_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(backend.find_core(frozen=True, executable=str(Path(tmp) / "CojamDesktop.exe")))


class TitleBarTint(unittest.TestCase):
    def test_colorref_uses_bgr_order(self):
        self.assertEqual(shell_main._colorref("#1b1b1e").value, 0x001E1B1B)
        self.assertEqual(shell_main._colorref("#ececf1").value, 0x00F1ECEC)
        self.assertEqual(shell_main._colorref("#ffffff").value, 0x00FFFFFF)

    def test_tint_is_noop_without_window_handle(self):
        with patch.object(shell_main, "_window_hwnd", return_value=None):
            shell_main.tint_title_bar()          # 找不到窗口时静默跳过

    @unittest.skipUnless(sys.platform == "win32", "DWM attributes are Windows only")
    def test_tint_paints_theme_colors(self):
        seen = []

        class FakeDwm:
            def DwmSetWindowAttribute(self, hwnd, attribute, value, size):
                seen.append((attribute, ctypes.cast(value, ctypes.POINTER(ctypes.c_uint)).contents.value))
                return 0

        fake = types.SimpleNamespace(dwmapi=FakeDwm())
        for dark, expect in ((True, {20: 1, 35: 0x001E1B1B, 36: 0x00F1ECEC, 34: 0x00312C2C}),
                             (False, {20: 0, 35: 0x00FFFFFF, 36: 0x001B1818, 34: 0x00EAE7E7})):
            seen[:] = []
            with patch.object(shell_main, "_window_hwnd", return_value=1234), \
                    patch.object(shell_main, "_system_dark", return_value=dark), \
                    patch.object(shell_main.ctypes, "windll", fake):
                shell_main.tint_title_bar()
            self.assertEqual(dict(seen), expect)


if __name__ == "__main__":
    unittest.main()
