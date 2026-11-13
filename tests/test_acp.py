# -*- coding: utf-8 -*-
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_BOOT_TMP = tempfile.mkdtemp(prefix="rt_acp_")
os.environ["ROUNDTABLE_LOG_DIR"] = _BOOT_TMP

from bridge import acp, common, drivers, guard, registry
from bridge import room as R

FAKE_ACP_SCRIPT = r'''# -*- coding: utf-8 -*-
import json, os, sys

req_seq = 100
CALL_LOG = os.path.join(os.getcwd(), "acp_calls.jsonl")
CONFIG_OPTIONS = [
    {"id": "model", "category": "model", "options": [{"value": "zhipu/glm-5.3"}, {"value": "zhipu/glm-5.3-flash"}]},
    {"id": "effort", "category": "thought_level", "options": [{"value": "low"}, {"value": "medium"}, {"value": "high"}, {"value": "default"}]},
    {"id": "mode", "category": "mode", "options": [{"value": "build"}, {"value": "plan"}]},
]

def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def log(obj):
    try:
        with open(CALL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        pass

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
    log(msg)
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}})
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "acp-sess-1", "configOptions": CONFIG_OPTIONS}})
    elif method == "session/load":
        send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": params.get("sessionId"), "configOptions": CONFIG_OPTIONS}})
    elif method == "session/set_config_option":
        cid = params.get("configId")
        if cid in ("model", "effort", "mode"):
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Invalid params: unknown config option: " + str(cid)}})
    elif method == "session/prompt":
        sid = params.get("sessionId")
        blocks = params.get("prompt") or []
        text = blocks[0].get("text", "") if blocks else ""
        if "LOSE_SESSION" in text:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Invalid params: session not found: " + str(sid)}})
            continue
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


class FakeAcpRoom:
    """够 acp.call / prompt 用的最小会议室。"""
    id = "fake-acp-room"
    keys = {}

    def __init__(self, wd):
        self.wd = wd

    def workdir(self):
        return self.wd

    def effective_models(self, pid):
        return ("zhipu/glm-5.3", "high")

    def tool_key(self, pid):
        return ""

    def push_live(self, pid, payload):
        pass


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

    # ---------- 小工具 ----------
    def calls(self):
        path = os.path.join(self.tmp.name, "acp_calls.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def config_calls(self):
        return [c for c in self.calls() if c.get("method") == "session/set_config_option"]

    def wait(self, r, t=8):
        end = time.time() + t
        while time.time() < end:
            if not r.busy_for():
                return True
            time.sleep(0.02)
        return False

    def make_room(self, agent):
        r = R.rooms.create("acp-" + agent, "", self.tmp.name)
        self.addCleanup(R.rooms.unload, r.id)
        self.assertTrue(r.set_participants([agent])[0])
        return r

    # ---------- 配置下发 ----------
    def test_config_option_uses_declared_id_not_category_name(self):
        room = FakeAcpRoom(self.tmp.name)
        ok, text, sid, _ = drivers.call("fakeacp", "你好", None, (), "discuss", room)
        self.assertTrue(ok, text)
        sent = {c["params"]["configId"]: c["params"]["value"] for c in self.config_calls()}
        self.assertEqual(sent.get("model"), "zhipu/glm-5.3")
        self.assertEqual(sent.get("effort"), "high")   # category=thought_level，真实 id 是 effort
        self.assertNotIn("thought_level", sent)

    def test_unchanged_model_and_effort_are_not_sent_again(self):
        room = FakeAcpRoom(self.tmp.name)
        ok, text, sid, _ = drivers.call("fakeacp", "第一轮", None, (), "discuss", room)
        self.assertTrue(ok, text)
        self.assertEqual(len(self.config_calls()), 2)
        ok2, text2, sid2, _ = drivers.call("fakeacp", "第二轮", sid, (), "discuss", room)
        self.assertTrue(ok2, text2)
        self.assertEqual(sid2, sid)
        self.assertEqual(len(self.config_calls()), 2)   # 没变就不再发

    def test_rejected_config_is_not_cached_as_applied(self):
        proc = object.__new__(acp.AcpProcess)
        proc.applied = ('native', 'old-model', 'low')
        proc.config_options = [{'id':'model','category':'model'}, {'id':'effort','category':'thought_level'}]
        proc.request = Mock(side_effect=[{}, acp.AcpError('not supported')])
        with self.assertRaisesRegex(acp.AcpError, 'ACP 拒绝'):
            proc.apply_config('native','new-model','high')
        self.assertIsNone(proc.applied)
        proc.request = Mock(return_value={})
        proc.apply_config('native','old-model','low')
        self.assertEqual(proc.request.call_count,2)

    def test_explicit_setting_requires_a_real_config_option(self):
        proc = object.__new__(acp.AcpProcess)
        proc.applied = None
        proc.config_options = []
        proc.request = Mock()
        with self.assertRaisesRegex(acp.AcpError,'未提供'):
            proc.apply_config('native','requested-model','')
        proc.request.assert_not_called()
        self.assertIsNone(proc.applied)

    def test_acp_capabilities_require_declared_models_and_efforts(self):
        from bridge import agents
        caps = agents.model_capabilities("fakeacp")
        self.assertFalse(caps["model_supported"])
        self.assertFalse(caps["effort_supported"])
        self.assertEqual(caps["models"], [])
        registry.save_agent(dict(self.spec, models=["zhipu/glm-5.3"], efforts=["low", "medium", "high"]))
        caps = agents.model_capabilities("fakeacp")
        self.assertTrue(caps["model_supported"])
        self.assertTrue(caps["effort_supported"])
        self.assertIn("zhipu/glm-5.3", caps["models"])
        self.assertEqual(caps["efforts_by_model"]["zhipu/glm-5.3"], ["low", "medium", "high"])

    # ---------- 会话失效 ----------
    def test_lost_session_returns_none_session_id(self):
        ok, text, sid, edits = drivers.call("fakeacp", "LOSE_SESSION", None, (), "discuss", None)
        self.assertFalse(ok)
        self.assertIsNone(sid)
        self.assertEqual(edits, [])
        self.assertIn("会话已失效", text)

    def test_config_is_reapplied_after_session_loss(self):
        room = FakeAcpRoom(self.tmp.name)
        ok, text, sid, _ = drivers.call("fakeacp", "LOSE_SESSION", None, (), "discuss", room)
        self.assertFalse(ok)
        self.assertIsNone(sid)
        self.assertEqual(len(self.config_calls()), 2)      # 旧会话下过一次 model + effort
        ok2, text2, sid2, _ = drivers.call("fakeacp", "重试", None, (), "discuss", room)
        self.assertTrue(ok2, text2)
        self.assertEqual(len(self.config_calls()), 4)      # 新会话会重新下发，不会因为 applied 缓存漏掉 effort
        sent = {c["params"]["configId"]: c["params"]["value"] for c in self.config_calls()[2:]}
        self.assertEqual(sent, {"model": "zhipu/glm-5.3", "effort": "high"})

    def test_room_retries_lost_acp_session_with_full_first_prompt(self):
        r = self.make_room("fakeacp")
        prompts = []

        def fake(ai, prompt, sid, imgs, mode=None, room=None):
            prompts.append((prompt, sid))
            if len(prompts) == 1:
                return True, "@用户 第一轮", "s-1", []
            if len(prompts) == 2:
                return False, "fakeacp (ACP) 会话已失效，正在换新会话重试", None, []
            return True, "@用户 重试成功", "s-2", []

        old = R.ai_caller
        R.ai_caller = fake
        self.addCleanup(setattr, R, "ai_caller", old)
        r.handle_message("你", "@fakeacp 第一件事")
        self.assertTrue(self.wait(r))
        self.assertEqual(r.meta.get("fakeacp_session"), "s-1")
        r.handle_message("你", "@fakeacp 第二件事")
        self.assertTrue(self.wait(r))
        self.assertEqual(len(prompts), 3)          # 只重试一次
        retry_prompt, retry_sid = prompts[2]
        self.assertIsNone(retry_sid)
        self.assertIn("这场会议的目标", retry_prompt)
        self.assertIn("看板", retry_prompt)
        self.assertIn("第一件事", retry_prompt)    # 旧会话丢了，历史要重发
        self.assertIn("第二件事", retry_prompt)
        self.assertEqual(r.meta.get("fakeacp_session"), "s-2")

    def test_non_acp_error_text_does_not_drop_session(self):
        registry.save_agent({"id": "fakecli", "label": "Fake CLI", "driver": "cli", "bin": [sys.executable],
                             "args": {"new": [sys.executable, "-c", "print(1)", "{prompt}"]}})
        r = self.make_room("fakecli")
        prompts = []

        def fake(ai, prompt, sid, imgs, mode=None, room=None):
            prompts.append(sid)
            if len(prompts) == 1:
                return True, "@用户 好", "c-1", []
            return False, "CLI 报错：Session not found", "c-2", []

        old = R.ai_caller
        R.ai_caller = fake
        self.addCleanup(setattr, R, "ai_caller", old)
        r.handle_message("你", "@fakecli 第一件事")
        self.assertTrue(self.wait(r))
        r.handle_message("你", "@fakecli 第二件事")
        self.assertTrue(self.wait(r))
        self.assertEqual(len(prompts), 2)          # 非 ACP 不按结构化信号重试
        self.assertEqual(r.meta.get("fakecli_session"), "c-2")   # 保留新会话 id

    # ---------- 原有行为 ----------
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
