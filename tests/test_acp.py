# -*- coding: utf-8 -*-
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_BOOT_TMP = tempfile.mkdtemp(prefix="rt_acp_")
os.environ["ROUNDTABLE_LOG_DIR"] = _BOOT_TMP

from bridge import acp, common, drivers, guard, registry

FAKE_ACP_SCRIPT = r'''# -*- coding: utf-8 -*-
import json, sys

req_seq = 100

def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def call_client(method, params):
    global req_seq
    req_seq += 1
    rid = req_seq
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    for raw in sys.stdin:
        msg = json.loads(raw.strip())
        if msg.get("id") == rid:
            return msg

for raw in sys.stdin:
    line = raw.strip()
    if not line:
        continue
    msg = json.loads(line)
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}})
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "acp-sess-1"}})
    elif method == "session/load":
        send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": params.get("sessionId")}})
    elif method == "session/prompt":
        sid = params.get("sessionId")
        blocks = params.get("prompt") or []
        text = blocks[0].get("text", "") if blocks else ""
        if "TRY_WRITE" in text:
            perm = call_client("session/request_permission", {
                "sessionId": sid,
                "toolCall": {"kind": "edit", "title": "Write demo.txt"},
                "options": [
                    {"optionId": "allow-1", "kind": "allow_once"},
                    {"optionId": "reject-1", "kind": "reject_once"},
                ],
            })
            outcome = ((perm.get("result") or {}).get("outcome") or {})
            if outcome.get("optionId") == "allow-1":
                w = call_client("fs/write_text_file", {"sessionId": sid, "path": "demo.txt", "content": "written by acp"})
                if "error" in w:
                    chunk = "WRITE_ERR:" + w["error"]["message"]
                else:
                    chunk = "WRITE_OK"
            else:
                chunk = "PERM_DENIED"
        else:
            chunk = "ACP回复(" + sid + "):" + text
        send({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": sid, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": chunk}}},
        })
        send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
'''


class TestAcpDriver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old_cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.old_log = common.LOG_DIR
        common.LOG_DIR = registry.LOG_DIR = guard.LOG_DIR = self.tmp.name
        os.makedirs(os.path.join(self.tmp.name, "agents.d"), exist_ok=True)
        self.fake_script = os.path.join(self.tmp.name, "fake_acp.py")
        Path(self.fake_script).write_text(FAKE_ACP_SCRIPT, encoding="utf-8")
        self.spec = {
            "id": "fakeacp",
            "label": "Fake ACP",
            "driver": "acp",
            "bin": [sys.executable],
            "args": {"acp": [self.fake_script]},
        }
        registry.save_agent(self.spec)

    def tearDown(self):
        acp.stop()
        os.chdir(self.old_cwd)
        common.LOG_DIR = registry.LOG_DIR = guard.LOG_DIR = self.old_log
        self.tmp.cleanup()

    def test_streaming_prompt_and_session_reuse(self):
        ok, text, sid, edits = drivers.call("fakeacp", "你好", None, (), "discuss", None)
        self.assertTrue(ok, text)
        self.assertEqual(sid, "acp-sess-1")
        self.assertEqual(text, "ACP回复(acp-sess-1):你好")
        self.assertEqual(edits, [])

        ok2, text2, sid2, _ = drivers.call("fakeacp", "第二轮", sid, (), "discuss", None)
        self.assertTrue(ok2, text2)
        self.assertEqual(sid2, "acp-sess-1")
        self.assertIn("第二轮", text2)

    def test_permission_and_write_guarded_by_mode(self):
        ok, text, sid, edits = drivers.call("fakeacp", "TRY_WRITE", None, (), "discuss", None)
        self.assertTrue(ok, text)
        self.assertEqual(text, "PERM_DENIED")
        self.assertEqual(edits, [])

        ok2, text2, sid2, edits2 = drivers.call("fakeacp", "TRY_WRITE", sid, (), "work", None)
        self.assertTrue(ok2, text2)
        self.assertEqual(text2, "WRITE_OK")
        self.assertEqual(len(edits2), 1)
        self.assertEqual(Path(edits2[0]).read_text(encoding="utf-8"), "written by acp")


if __name__ == "__main__":
    unittest.main()
