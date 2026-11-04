"""桌面快捷方式的入口：会议室没开就在后台开起来，然后用浏览器打开。不弹黑窗口。"""
import os, subprocess, sys

URL = "http://127.0.0.1:8765/"
HERE = os.path.dirname(os.path.abspath(__file__))


# Always let the current backend validate/reuse/upgrade the existing process.
# A successful GET / alone cannot distinguish an old backend from the new one.
env = dict(os.environ, PYTHONIOENCODING="utf-8")
env.pop("ROUNDTABLE_NO_BROWSER", None)
exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
if not os.path.exists(exe):
    exe = sys.executable
subprocess.Popen([exe, os.path.join(HERE, "roundtable.py")], cwd=HERE, env=env,
                 creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_NO_WINDOW, close_fds=True)
