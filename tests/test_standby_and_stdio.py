# -*- coding: utf-8 -*-
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_BOOT_TMP = tempfile.mkdtemp(prefix="rt_standby_")
os.environ["ROUNDTABLE_LOG_DIR"] = _BOOT_TMP

from bridge import access, common, drivers, mcp, mcp_stdio, members, registry, room as R


class TestStandbyAndPrompts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old_log = common.LOG_DIR
        common.LOG_DIR = self.tmp.name
        os.makedirs(os.path.join(self.tmp.name, "agents.d"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp.name, "meetings"), exist_ok=True)
        members.broker = members.Broker()
        registry.save_agent({"id": "ide-agent", "label": "IDE Agent", "driver": "external", "enabled": True})
        self.key = access.credential("ide-agent")
        self.room = R.rooms.create("待命测试", "", self.tmp.name)
        self.room.set_participants(["ide-agent"])

    def tearDown(self):
        common.LOG_DIR = self.old_log
        self.tmp.cleanup()

    def test_prompts_list_and_get(self):
        code, resp = mcp.handle_http(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"}), self.key)
        self.assertEqual(code, 200)
        prompts = resp["result"]["prompts"]
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["name"], "cojam_standby")

        code2, resp2 = mcp.handle_http(
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "prompts/get", "params": {"name": "cojam_standby"}}),
            self.key,
        )
        self.assertEqual(code2, 200)
        self.assertIn("cojam_standby", resp2["result"]["messages"][0]["content"]["text"])

    def test_standby_task_roundtrip_and_stop(self):
        # 1. Idle poll with timeout=0 registers standby status
        _, idle_resp = mcp.handle_http(
            json.dumps({
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "cojam_standby", "arguments": {"timeout": 0}},
            }),
            self.key,
        )
        payload = json.loads(idle_resp["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "idle")
        st = members.broker.status("ide-agent")
        self.assertTrue(st["automatic"])
        self.assertTrue(st["standby"])
        self.assertEqual(st["label"], "MCP 待命中")

        # 2. Cojam dispatches a task while standby is polling
        result_box = {}

        def caller():
            ok, text, sid, _ = drivers.call("ide-agent", "请检查 README", None, (), "discuss", self.room)
            result_box.update(ok=ok, text=text, sid=sid)

        t = threading.Thread(target=caller, daemon=True)
        t.start()
        time.sleep(0.1)

        _, task_resp = mcp.handle_http(
            json.dumps({
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "cojam_standby", "arguments": {"timeout": 5}},
            }),
            self.key,
        )
        task_data = json.loads(task_resp["result"]["content"][0]["text"])
        self.assertEqual(task_data["status"], "task")
        worker = task_data["worker"]
        task = task_data["task"]

        _, reply_resp = mcp.handle_http(
            json.dumps({
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "reply_task",
                    "arguments": {
                        "worker": worker,
                        "task": task["id"],
                        "lease": task["lease"],
                        "ok": True,
                        "text": "@用户 README 已检查完毕",
                    },
                },
            }),
            self.key,
        )
        self.assertFalse(reply_resp["result"].get("isError"))
        t.join(5)
        self.assertTrue(result_box.get("ok"))
        self.assertEqual(result_box.get("text"), "@用户 README 已检查完毕")

        # 3. Stop standby from Cojam side wakes up next/current standby call
        members.broker.stop_standby("ide-agent")
        self.assertFalse(members.broker.status("ide-agent")["automatic"])
        _, stop_resp = mcp.handle_http(
            json.dumps({
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {"name": "cojam_standby", "arguments": {"timeout": 5}},
            }),
            self.key,
        )
        stop_data = json.loads(stop_resp["result"]["content"][0]["text"])
        self.assertEqual(stop_data["status"], "stopped")

    def _standby(self, key, timeout=0, allow_work=None, reply="收到"):
        """在测试线程里走一次 MCP 待命调用，返回解析后的结果。"""
        args = {"timeout": timeout}
        if allow_work is not None:
            args["allow_work"] = allow_work
        _, resp = mcp.handle_http(
            json.dumps({
                "jsonrpc": "2.0",
                "id": int(time.time() * 1000) % 100000,
                "method": "tools/call",
                "params": {"name": "cojam_standby", "arguments": args},
            }),
            key,
        )
        return json.loads(resp["result"]["content"][0]["text"])

    def _standby_once(self, key, reply="收到", allow_work=None):
        """后台跑一次长轮询：领到任务就回复，把任务内容留给测试检查。"""
        box = {}

        def run():
            data = self._standby(key, timeout=5, allow_work=allow_work)
            box["data"] = data
            if data.get("status") != "task":
                return
            task = data["task"]
            box["task"] = task
            mcp.handle_http(
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": 99,
                    "method": "tools/call",
                    "params": {
                        "name": "reply_task",
                        "arguments": {"worker": data["worker"], "task": task["id"],
                                      "lease": task["lease"], "ok": True, "text": reply},
                    },
                }),
                key,
            )

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t, box

    def test_standby_does_not_steal_a_cli_conversation(self):
        """桌面待命在线本身不能改变会话执行端。"""
        ok, err = registry.save_agent({"id": "claude-ide", "label": "Claude Desktop",
                                       "driver": "claude", "bin": ["python"], "enabled": True})
        self.assertTrue(ok, err)
        key = access.credential("claude-ide")
        self.room.set_participants(["ide-agent", "claude-ide"])

        idle = self._standby(key, timeout=0)
        self.assertEqual(idle["status"], "idle")
        st = members.broker.status("claude-ide")
        self.assertTrue(st["standby"], st)

        with patch.object(drivers.agents, "call_ai", return_value=(True,"@用户 CLI 已处理",None,[])) as cli:
            ok, text, sid, _ = drivers.call("claude-ide", "看一下 README", None, (), "discuss", self.room)
        self.assertTrue(ok, text)
        self.assertEqual(text, "@用户 CLI 已处理")
        cli.assert_called_once()
        self.assertEqual(members.broker.tasks,{})

    def test_standby_work_mode_needs_allow_work(self):
        """会话是干活模式时，待命端没显式允许改文件就只读参与。"""
        ok, err = registry.save_agent({"id": "claude-ide", "label": "Claude Desktop",
                                       "driver": "external", "enabled": True})
        self.assertTrue(ok, err)
        key = access.credential("claude-ide")
        self.room.set_participants(["ide-agent", "claude-ide"])
        ok, err = self.room.set_mode("work")
        self.assertTrue(ok, err)

        self._standby(key, timeout=0, allow_work=False)
        t, box = self._standby_once(key, allow_work=False)
        time.sleep(0.1)
        ok, text, _, _ = drivers.call("claude-ide", "改一下 README", None, (), "work", self.room)
        t.join(5)
        self.assertTrue(ok, text)
        self.assertEqual(box["task"]["mode"], "discuss")
        self.assertFalse(box["task"]["allow_work"])

        self._standby(key, timeout=0, allow_work=True)
        t2, box2 = self._standby_once(key, allow_work=True)
        time.sleep(0.1)
        ok2, text2, _, _ = drivers.call("claude-ide", "改一下 README", None, (), "work", self.room)
        t2.join(5)
        self.assertTrue(ok2, text2)
        self.assertEqual(box2["task"]["mode"], "work")
        self.assertTrue(box2["task"]["allow_work"])

    def test_stop_standby_cooldown_is_sticky(self):
        """点了停止待命后，循环里的 cojam_standby 不能马上把它复活。"""
        members.broker.stop_standby("ide-agent")
        self.assertIn("ide-agent", members.broker.standby_stopped)
        for _ in range(2):
            data = self._standby(self.key, timeout=0)
            self.assertEqual(data["status"], "stopped")
            self.assertGreater(data["resume_after"], 0)
        self.assertIn("ide-agent", members.broker.standby_stopped)
        self.assertFalse(members.broker.status("ide-agent")["automatic"])

        # 冷却结束后允许重新开启
        members.broker.standby_stopped["ide-agent"] -= members.STANDBY_STOP_COOLDOWN + 1
        data = self._standby(self.key, timeout=0)
        self.assertEqual(data["status"], "idle")
        self.assertNotIn("ide-agent", members.broker.standby_stopped)
        self.assertTrue(members.broker.status("ide-agent")["standby"])

    def test_stdio_parse_error(self):
        out = json.loads(mcp_stdio.forward_one("not-json"))
        self.assertEqual(out["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
