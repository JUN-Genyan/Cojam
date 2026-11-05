# -*- coding: utf-8 -*-
"""Desktop shell backend: discovery, token, owner-tab heartbeat and busy detection."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_shell import backend


class State:
    app = "AI Bridge"
    token = "test-token"
    rooms = []
    delay = 0
    rooms_error = False
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
            self.assertEqual(found, ([str(core)], tmp))

    def test_packaged_mode_without_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(backend.find_core(frozen=True, executable=str(Path(tmp) / "CojamDesktop.exe")))


if __name__ == "__main__":
    unittest.main()
