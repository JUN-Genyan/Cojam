"""Run either desktop shell against the same isolated source backend."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import json

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop_shell import backend

PORT = 18765
BASE = "http://127.0.0.1:%d" % PORT
DATA = ROOT / ".test-tmp" / "tauri-experiment-data"


def child_environment():
    env = dict(os.environ)
    env.pop("ROUNDTABLE_AGENTS", None)
    env.update(ROUNDTABLE_PORT=str(PORT), ROUNDTABLE_LOG_DIR=str(DATA),
               ROUNDTABLE_DEFAULT_DIR=str(DATA / "workspace"),
               ROUNDTABLE_NO_BROWSER="1", ROUNDTABLE_OWNER_LIFECYCLE="1")
    return env


def serve():
    # The experiment listens on loopback only, never on the LAN or Tailscale.
    from bridge import web
    web.start_lan_listener = lambda *args, **kwargs: (None, None)
    web.start_tailscale_listener = lambda *args, **kwargs: (None, None)
    web.main()


def port_available():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("127.0.0.1", PORT))
        return True
    except OSError:
        return False


def matching_backend():
    try:
        token = backend.fetch_token(BASE)
        request = urllib.request.Request(BASE + "/api/server/runtime", headers={"X-RT-Token": token})
        with backend._opener().open(request, timeout=3) as response:
            runtime = json.load(response)
        same = lambda a, b: os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
        return same(runtime.get("install", ""), ROOT) and same(runtime.get("data", ""), DATA)
    except (OSError, ValueError, KeyError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--shell", choices=("tauri", "pywebview"), default="tauri")
    args = parser.parse_args()
    if args.serve:
        serve()
        return 0
    executable = ROOT / "experiments" / "tauri" / "src-tauri" / "target" / "release" / (
        "cojam-tauri-experiment.exe" if os.name == "nt" else "cojam-tauri-experiment")
    if args.shell == "tauri" and not executable.is_file():
        print("尚未构建 Tauri 实验窗口：%s" % executable, file=sys.stderr)
        return 2
    if not port_available():
        print("实验端口 %s 已被占用；为避免混用数据，请先释放该端口。" % PORT, file=sys.stderr)
        return 2
    (DATA / "workspace").mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve"],
                            cwd=ROOT, env=child_environment(), creationflags=flags)
    info = backend.wait_ready(BASE, proc)
    if not info or not matching_backend() or proc.poll() is not None:
        backend.stop_process(proc)
        print("隔离后台未能启动或身份不匹配。", file=sys.stderr)
        return 2
    print("隔离后台就绪：%.2f 秒；数据目录：%s" % (time.monotonic() - started, DATA), flush=True)
    try:
        if args.shell == "tauri":
            return subprocess.call([str(executable)], cwd=ROOT)
        from desktop_shell.main import confirm_close, open_window
        token = backend.fetch_token(BASE)
        open_window(BASE + "/", lambda: confirm_close(BASE, token))
        return 0
    except Exception as error:
        backend.stop_process(proc)
        print("无法打开实验窗口：%s" % error, file=sys.stderr)
        return 2
    # The existing owner lifecycle stops the isolated server after its last
    # window closes; do not kill a backend which may still have active work.


if __name__ == "__main__":
    raise SystemExit(main())
