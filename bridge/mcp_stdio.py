# -*- coding: utf-8 -*-
r"""stdio ↔ Cojam HTTP MCP 转发桥：给只支持 stdio MCP 的桌面软件（如 Claude Desktop）用。

启动方式：
    python -m bridge.mcp_stdio
    python roundtable.py mcp-stdio
    Cojam.exe mcp-stdio

环境变量：
    AI_BRIDGE_URL    默认 http://127.0.0.1:<PORT>/mcp
    AI_BRIDGE_TOKEN  联系人接入密钥（Bearer token）
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request

from .common import PORT


def forward_one(line, url=None, token=None):
    raw = (line or "").strip()
    if not raw:
        return None
    try:
        msg = json.loads(raw)
    except Exception:
        return json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
    target = url or os.environ.get("AI_BRIDGE_URL") or ("http://127.0.0.1:%d/mcp" % PORT)
    tok = token if token is not None else os.environ.get("AI_BRIDGE_TOKEN", "")
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(target, data=raw.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=65) as resp:
            body = resp.read().decode("utf-8", "replace").strip()
            return body if body else None
    except urllib.error.HTTPError as e:
        if isinstance(msg, dict) and msg.get("id") is not None:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            return json.dumps({"jsonrpc": "2.0", "id": msg.get("id"),
                               "error": {"code": -32000, "message": "Cojam HTTP %s: %s" % (e.code, detail)}},
                              ensure_ascii=False)
        return None
    except Exception as e:
        if isinstance(msg, dict) and msg.get("id") is not None:
            return json.dumps({"jsonrpc": "2.0", "id": msg.get("id"),
                               "error": {"code": -32000, "message": "连不上 Cojam (%s): %s" % (target, e)}},
                              ensure_ascii=False)
        return None


def main(argv=None):
    # MCP 的 stdio 一律是 UTF-8；中文 Windows 默认 cp936，不重设会把中文和 emoji 弄乱。
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    write_lock = threading.Lock()
    slots = threading.Semaphore(8)

    def emit(resp):
        if not resp:
            return
        with write_lock:          # 多行响应不能交错
            sys.stdout.write(resp + "\n")
            sys.stdout.flush()

    def work(line):
        with slots:
            try:
                emit(forward_one(line))
            except Exception:
                pass

    # 一条 55 秒的待命调用不能把 ping / tools/list 堵在后面，所以每条请求单独跑。
    for raw in sys.stdin:
        threading.Thread(target=work, args=(raw,), daemon=True).start()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
