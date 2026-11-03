# -*- coding: utf-8 -*-
r"""最小的 harness 桥：让你的 AI 进程以 driver=http 坐进会议室。

跑起来：
    python examples/harness_stub.py            # 默认 127.0.0.1:9100

然后在 <数据目录>\agents.d\myharness.json 里放配置（照 examples/myharness.json 抄），
打开会议室点"参会者"勾上它，@ 它一句就通了。

会议室每轮会 POST 一个 JSON 过来，你回 {"text": "..."} 就行：
    {room, room_title, speaker, mode, prompt, session, workdir, goal, images, bridge_url, bridge_key}

**接你自己的东西只有一处要改**：下面 answer() 里的 TODO。
"""
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:                     # 边跑边打印，别等缓冲满了才看见
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

HOST, PORT = "127.0.0.1", int(os.environ.get("HARNESS_PORT", "9100"))
LOG = os.environ.get("HARNESS_LOG", "")     # 设了就把每轮请求存下来，方便看会议室到底发了什么
SESSIONS = {}                               # room -> 会话 id（真正用的时候换成你自己的会话表）


# ======================= 这里换成你的 harness =======================
def answer(req):
    """收到会议室的一轮请求，返回 (回复文本, 会话 id 或 None)。

    req["prompt"] 是完整的一段话：会议室规矩 + 目标 + 这一轮的新消息，最后的
    "现在轮到你（xxx）回复" 后面就是该你回的内容。
    """
    room = req.get("room") or ""
    sid = req.get("session") or ""
    if not sid:                              # 第一轮：建个会话
        sid = "h-" + (room[-8:] or "local")
        SESSIONS[room] = sid

    # TODO: 换成你自己的：
    #   reply = my_harness.run(req["prompt"])                     # 一次性调用
    #   reply = my_harness.chat(sid, req["prompt"])               # 有会话的
    #   reply = my_harness.run(req["prompt"], mode=req["mode"],
    #                          workdir=req["workdir"])            # 要它干活
    n = len([x for x in req["prompt"].splitlines() if x.strip().startswith("[")])
    reply = ("（harness 桩）收到，%s模式，工作目录 %s，这条提示词里带了 %d 条会议记录。"
             "把 examples/harness_stub.py 里 answer() 换成我自己的就行。@用户"
             % ("干活" if req.get("mode") == "work" else "讨论", req.get("workdir", "?"), n))
    return reply, sid
# =====================================================================


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        try:
            req = json.loads(raw or "{}")
        except Exception:
            return self._out(400, {"text": "", "error": "body 不是 JSON"})
        if LOG:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(req, ensure_ascii=False) + "\n")
        print("---- %s / %s / %s" % (req.get("room"), req.get("speaker"), req.get("mode")))
        print("     session=%r  session_id=%r  images=%s" % (req.get("session"), SESSIONS.get(req.get("room")), req.get("images")))
        print("     prompt 前 200 字：%s" % (req.get("prompt") or "")[:200].replace("\n", " ⏎ "))
        try:
            text, sid = answer(req)
        except Exception as e:
            return self._out(500, {"text": "", "error": "answer() 抛了：%s" % e})
        print("     回：%s" % text[:120].replace("\n", " ⏎ "))
        self._out(200, {"text": text, "session": sid})

    def _out(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("harness 桩在 http://%s:%d/bridge ，Ctrl+C 停" % (HOST, PORT))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
