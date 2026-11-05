"""Find, start and keep the Cojam backend alive for the desktop shell."""
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

APP_NAME = "AI Bridge"
TAB_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


def find_core(frozen=None, executable=None):
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = sys.executable if executable is None else executable
    exe_dir = Path(executable).resolve().parent
    if frozen:
        core = exe_dir / "Cojam.exe"
        return ([str(core)], str(exe_dir)) if core.is_file() else None
    repo = Path(__file__).resolve().parents[1]
    script = repo / "roundtable.py"
    if not script.is_file():
        return None
    python = Path(executable)
    if python.name.lower() == "python.exe":
        windowless = python.with_name("pythonw.exe")
        if windowless.is_file():
            python = windowless
    return [str(python), str(script)], str(repo)


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _bindable(base):
    # Some Windows setups time out instead of refusing a closed local port.
    parts = urllib.parse.urlsplit(base)
    if not parts.hostname or not parts.port:
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((parts.hostname, parts.port))
        return True
    except OSError:
        return False


def probe(base, timeout=.5):
    try:
        with _opener().open(base.rstrip("/") + "/api/server/info", timeout=timeout) as response:
            info = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return "foreign", None
    except urllib.error.URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            return "free", None
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            return ("free", None) if _bindable(base) else ("timeout", None)
        return "foreign", None
    except (TimeoutError, socket.timeout):
        return ("free", None) if _bindable(base) else ("timeout", None)
    except (OSError, ValueError):
        return "foreign", None
    if isinstance(info, dict) and info.get("app") == APP_NAME:
        return "cojam", info
    return "foreign", None


def spawn_core(command, cwd):
    env = dict(os.environ)
    env["ROUNDTABLE_NO_BROWSER"] = "1"
    env["ROUNDTABLE_OWNER_LIFECYCLE"] = "1"
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    return subprocess.Popen(command, cwd=cwd, env=env, creationflags=flags, close_fds=True)


def wait_ready(base, proc=None, timeout=30, interval=.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state, info = probe(base)
        if state == "cojam":
            return info
        if proc is not None and proc.poll() is not None:
            for _ in range(5):
                state, info = probe(base)
                if state == "cojam":
                    return info
                time.sleep(interval)
            return None
        time.sleep(interval)
    return None


def fetch_token(base, timeout=3):
    with _opener().open(base.rstrip("/") + "/", timeout=timeout) as response:
        html = response.read().decode("utf-8")
    match = re.search(r'name="rt-token" content="([^"]+)"', html)
    if not match:
        raise RuntimeError("已运行的合拍后台版本不兼容，请先退出旧版再打开桌面版。")
    return match.group(1)


def post_session(base, token, tab, action, instance, timeout=3):
    if not TAB_RE.fullmatch(str(tab)) or not TAB_RE.fullmatch(str(instance)):
        raise ValueError("无效页面状态")
    body = json.dumps({"tab": tab, "action": action, "instance": instance}).encode("utf-8")
    request = urllib.request.Request(
        base.rstrip("/") + "/api/ui/session", data=body,
        headers={"Content-Type": "application/json", "X-RT-Token": token})
    with _opener().open(request, timeout=timeout):
        pass


def busy(base, token, timeout=3):
    request = urllib.request.Request(base.rstrip("/") + "/api/rooms",
                                     headers={"X-RT-Token": token})
    try:
        with _opener().open(request, timeout=timeout) as response:
            rows = json.loads(response.read().decode("utf-8")).get("rooms") or []
    except Exception:
        return False
    return any(bool(row.get("running")) or bool(row.get("queued")) for row in rows if isinstance(row, dict))


def new_tab_id():
    return secrets.token_urlsafe(24)


def stop_process(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def log_path(core_dir):
    directory = os.environ.get("ROUNDTABLE_LOG_DIR") or os.path.join(core_dir, "data")
    return os.path.join(directory, "cojam.log")


class TabHeartbeat:
    def __init__(self, base, token, tab, interval=30):
        if not TAB_RE.fullmatch(str(tab)):
            raise ValueError("无效页面状态")
        self.base = base
        self.token = token
        self.tab = tab
        self.interval = interval
        self.failures = 0
        self.last_error = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = None

    def _send(self, action):
        try:
            post_session(self.base, self.token, self.tab, action, self.tab)
            return True
        except Exception as error:
            self.failures += 1
            self.last_error = error
            return False

    def start(self):
        self._send("open")
        self._thread = threading.Thread(target=self._run, daemon=True, name="cojam-desktop-heartbeat")
        self._thread.start()

    def _run(self):
        while not self._stop.wait(self.interval):
            self._send("ping")

    def stop(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._stop.set()
        self._send("close")
