# -*- coding: utf-8 -*-
"""接更多 agent：注册表读配置、通用 cli 驱动、http 驱动、每场会议各自的参会者、没有会话的 AI 拿整份记录。"""
import json, os, sys, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = tempfile.mkdtemp(prefix="rt_reg_")
os.environ["ROUNDTABLE_LOG_DIR"] = DATA
os.environ["ROUNDTABLE_NO_PRESETS"] = "1"      # 别让这台机器上装了哪些 CLI 影响测试
AGD = os.path.join(DATA, "agents.d")
os.makedirs(AGD, exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bridge import common, drivers, registry, room as R

FAKE = os.path.join(DATA, "fake_agent.py")
REC = os.path.join(DATA, "calls.jsonl")
open(FAKE, "w", encoding="utf-8").write(
    "import json, sys\n"
    "open(%r, 'a', encoding='utf-8').write(json.dumps(sys.argv[1:], ensure_ascii=False) + chr(10))\n"
    "print(json.dumps({'reply': 'fake 说：收到', 'sid': 'sid-fake-1'}, ensure_ascii=False))\n" % REC)


def write_cfg(name, obj):
    with open(os.path.join(AGD, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def calls():
    if not os.path.exists(REC):
        return []
    with open(REC, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


CLI_CFG = {"id": "fake", "label": "假 CLI", "driver": "cli", "bin": ["python"],
           "args": {"new": [FAKE, "{prompt}"], "resume": [FAKE, "--resume", "{session}", "{prompt}"]},
           "prompt": "arg", "parse": {"type": "json", "text": "reply", "session": "sid"}}


class Base(unittest.TestCase):
    def setUp(self):
        # These tests exercise room behavior, not what happens to be installed
        # on the machine running them. Keep the built-in agents deterministic.
        from unittest.mock import patch
        real_find = registry.find_executable
        detector = patch.object(registry, 'find_executable',
            side_effect=lambda name: ('test-' + name) if name in ('claude', 'codex') else real_find(name))
        detector.start()
        self.addCleanup(detector.stop)
        for f in os.listdir(AGD):
            os.remove(os.path.join(AGD, f))
        if os.path.exists(REC):
            os.remove(REC)


class Registry(Base):
    def test_bad_config_is_reported_not_fatal(self):
        write_cfg("good.json", CLI_CFG)
        write_cfg("bad.json", {"id": "Bad Id!", "driver": "cli", "bin": ["x"]})
        registry.load()
        self.assertIn("fake", registry.SPECS)
        self.assertTrue(any("bad.json" in e for e in registry.ERRORS))
        self.assertTrue(registry.get("fake")["enabled"])

    def test_disabled_when_bin_missing(self):
        write_cfg("nobin.json", {"id": "nobin", "driver": "cli", "bin": ["肯定没有这个命令-xyz"]})
        registry.load()
        self.assertFalse(registry.get("nobin")["enabled"])
        self.assertNotIn("nobin", common.AIS)

    def test_saved_enabled_cli_disappears_when_executable_is_removed(self):
        from unittest.mock import patch
        config = {"id": "savedcli", "driver": "cli", "bin": ["savedcli"], "enabled": True}
        with patch.object(registry, "find_executable", return_value="/installed/savedcli"):
            self.assertTrue(registry._norm(config)["enabled"])
        with patch.object(registry, "find_executable", return_value=None):
            self.assertFalse(registry._norm(config)["enabled"])

    def test_rescan_sees_a_command_after_path_changes(self):
        from unittest.mock import patch
        directory = tempfile.mkdtemp(prefix="cojam_scan_")
        executable = os.path.join(directory, "cojam-scan-test" + (".cmd" if os.name == "nt" else ""))
        with open(executable, "w", encoding="utf-8") as f:
            f.write("@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n")
        os.chmod(executable, 0o755)
        with patch.dict(os.environ, {"PATH": directory + os.pathsep + os.environ.get("PATH", "")}):
            self.assertIsNotNone(common.find_executable("cojam-scan-test"))
        os.remove(executable)
        with patch.dict(os.environ, {"PATH": directory + os.pathsep + os.environ.get("PATH", "")}):
            self.assertIsNone(common.find_executable("cojam-scan-test"))

    def test_reserved_and_bad_driver(self):
        write_cfg("a.json", {"id": "user", "driver": "cli", "bin": ["python"]})
        write_cfg("b.json", {"id": "x", "driver": "telepathy", "bin": ["python"]})
        registry.load()
        self.assertIsNone(registry.get("user"))
        self.assertIsNone(registry.get("x"))
        self.assertEqual(len(registry.ERRORS), 2)

    def test_mention_picks_up_new_agent(self):
        write_cfg("good.json", CLI_CFG)
        registry.load()
        self.assertEqual(common.mentions("@fake 你看 @claude"), ["fake", "claude"])
        self.assertEqual(common.mentions("@用户 好"), ["用户"])


class CliDriver(Base):
    def test_roundtrip_and_session_reuse(self):
        write_cfg("good.json", CLI_CFG)
        registry.load()
        ok, text, sid, edits = drivers.call("fake", "第一句", None, (), "discuss", None)
        self.assertTrue(ok, text)
        self.assertEqual(text, "fake 说：收到")
        self.assertEqual(sid, "sid-fake-1")
        ok2, text2, sid2, _ = drivers.call("fake", "第二句", sid, (), "discuss", None)
        self.assertTrue(ok2, text2)
        got = calls()
        self.assertEqual(got[0][-1], "第一句")
        self.assertIn("--resume", got[1])          # 第二轮带着会话 id 重开
        self.assertIn("sid-fake-1", got[1])

    def test_missing_binary_message(self):
        write_cfg("nb.json", {"id": "nb", "driver": "cli", "bin": ["肯定没有这个命令-xyz"],
                              "args": {"new": ["{prompt}"]}})
        registry.load()
        ok, text, sid, _ = drivers.call("nb", "hi", None, (), "discuss", None)
        self.assertFalse(ok)
        self.assertIn("找不到", text)


class HttpDriver(Base):
    def test_http_roundtrip(self):
        got_body = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                got_body.update(json.loads(self.rfile.read(n).decode("utf-8")))
                out = json.dumps({"text": "http 那边的回复", "session": "h-9"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            write_cfg("h.json", {"id": "h", "driver": "http", "label": "H",
                                 "endpoint": "http://127.0.0.1:%d/bridge" % srv.server_address[1]})
            registry.load()
            ok, text, sid, _ = drivers.call("h", "你好", "h-1", (), "discuss", None)
            self.assertTrue(ok, text)
            self.assertEqual(text, "http 那边的回复")
            self.assertEqual(sid, "h-9")
            self.assertEqual(got_body["speaker"], "h")
            self.assertEqual(got_body["session"], "h-1")
            self.assertIn("prompt", got_body)
        finally:
            srv.shutdown()


class Participants(Base):
    def setUp(self):
        super().setUp()
        write_cfg("good.json", CLI_CFG)
        registry.load()
        self.seen = []
        self._orig = R.ai_caller
        R.ai_caller = lambda ai, prompt, sid, imgs, mode=None, room=None: (
            self.seen.append(ai), (True, "@用户 好", sid or "s-" + ai, []))[1]
        self.wd = tempfile.mkdtemp(prefix="rt_reg_wd_")

    def tearDown(self):
        R.ai_caller = self._orig

    def wait(self, r, t=8):
        end = time.time() + t
        while time.time() < end:
            if not r.busy_for():
                return True
            time.sleep(0.05)
        return False

    def test_new_room_defaults_to_claude_codex(self):
        r = R.rooms.create("新会", "", self.wd)
        self.assertEqual(r.ais, ["claude", "codex"])

    def test_only_participants_get_called(self):
        r = R.rooms.create("选人", "", self.wd)
        self.assertTrue(r.set_participants(["claude", "fake"])[0])
        self.assertEqual(r.ais, ["claude", "fake"])
        r.handle_message("你", "大家好")          # 没 @：叫会里的全部
        self.assertTrue(self.wait(r))
        self.assertEqual(sorted(self.seen), ["claude", "fake"])
        self.assertNotIn("codex", r.ais)

    def test_mention_of_non_participant_is_ignored(self):
        r = R.rooms.create("不叫人", "", self.wd)
        r.set_participants(["claude"])
        r.handle_message("你", "@codex 你好")
        time.sleep(0.4)
        self.assertEqual(self.seen, [])

    def test_empty_conversation_and_unknown_contact(self):
        r = R.rooms.create("边界", "", self.wd)
        self.assertTrue(r.set_participants([])[0])
        self.assertEqual(r.ais, [])
        self.assertFalse(r.set_participants(["不存在的"])[0])

    def test_agent_installed_after_room_created(self):
        for f in os.listdir(AGD):
            os.remove(os.path.join(AGD, f))
        registry.load()                      # 这会儿还没装 fake
        r = R.rooms.create("后来的", "", self.wd)
        self.assertNotIn("fake", r.ais)
        write_cfg("good.json", CLI_CFG)
        registry.load()                      # 会开完之后才装上
        self.assertTrue(r.set_participants(["claude", "fake"])[0])
        r.handle_message("你", "@fake 你好")
        self.assertTrue(self.wait(r))
        self.assertIn("fake", self.seen)     # 会后加的 agent 也能叫（锁、per-AI 字段都按需建）

    def test_guest_joins_and_is_never_called(self):
        r = R.rooms.create("外来", "", self.wd)
        ok, err = r.join_external("ghost")
        self.assertTrue(ok, err)
        self.assertIn("ghost", r.ais)
        self.assertTrue(r.is_guest("ghost"))
        r.handle_message("ghost", "我经 /api/post 进来了")
        time.sleep(0.4)
        self.assertEqual(self.seen, [])           # 会外参与者不会被会议室叫
        self.assertEqual(r.messages[-1]["speaker"], "ghost")

    def test_bad_guest_name_rejected(self):
        r = R.rooms.create("外来2", "", self.wd)
        self.assertFalse(r.join_external("Bad Name")[0])
        self.assertFalse(r.join_external("user")[0])

    def test_context_full_sends_whole_transcript(self):
        write_cfg("full.json", dict(CLI_CFG, id="full1", context="full"))
        registry.load()
        prompts = []
        R.ai_caller = lambda ai, prompt, sid, imgs, mode=None, room=None: (
            prompts.append(prompt), (True, "@用户 好", sid, []))[1]
        r = R.rooms.create("整份", "", self.wd)
        r.set_participants(["full1"])
        r.handle_message("你", "@full1 第一件事")
        self.assertTrue(self.wait(r))
        r.handle_message("你", "@full1 第二件事")
        self.assertTrue(self.wait(r))
        self.assertIn("第一件事", prompts[1])     # 没有会话的 AI，第二轮也要看得到第一轮
        self.assertIn("第二件事", prompts[1])


class PresetHiding(Base):
    """预置联系人可以在界面上删除（记进隐藏名单），重新接入同名联系人会解除隐藏。"""

    def setUp(self):
        super().setUp()
        self.saved_dir = registry.PRESET_DIR
        self.preset_dir = os.path.join(DATA, "presets")
        os.makedirs(self.preset_dir, exist_ok=True)
        for f in os.listdir(self.preset_dir):
            os.remove(os.path.join(self.preset_dir, f))
        self.hidden = os.path.join(DATA, "hidden_presets.json")
        if os.path.exists(self.hidden):
            os.remove(self.hidden)
        os.environ.pop("ROUNDTABLE_NO_PRESETS", None)
        registry.PRESET_DIR = self.preset_dir

    def tearDown(self):
        registry.PRESET_DIR = self.saved_dir
        os.environ["ROUNDTABLE_NO_PRESETS"] = "1"
        if os.path.exists(self.hidden):
            os.remove(self.hidden)
        super().tearDown()

    @staticmethod
    def _write_preset(name, obj):
        with open(os.path.join(DATA, "presets", name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def test_preset_removable_and_hidden(self):
        self._write_preset("cli1.json", dict(CLI_CFG, id="cli1", label="预置 CLI"))
        registry.load()
        self.assertIn("cli1", registry.SPECS)
        ok, msg = registry.remove_agent("cli1")
        self.assertTrue(ok, msg)
        self.assertNotIn("cli1", registry.SPECS)
        self.assertNotIn("cli1", [a["id"] for a in registry.catalog()["agents"]])
        self.assertIn("cli1", json.load(open(self.hidden, encoding="utf-8")))
        self.assertIn("cli1", [a["id"] for a in registry.discover_hidden_presets()])
        self.assertTrue(registry.restore_preset("cli1"))
        self.assertIn("cli1", registry.SPECS)

    def test_readding_same_id_unhides_preset(self):
        self._write_preset("cli1.json", dict(CLI_CFG, id="cli1", label="预置 CLI"))
        registry.load()
        registry.remove_agent("cli1")
        ok, msg = registry.save_agent(dict(CLI_CFG, id="cli1", label="我的预置"))
        self.assertTrue(ok, msg)
        self.assertIn("cli1", registry.SPECS)
        self.assertTrue(registry.get("cli1")["user"])
        self.assertNotIn("cli1", json.load(open(self.hidden, encoding="utf-8")))

    def test_agents_d_override_wins_over_hidden_preset(self):
        self._write_preset("cli1.json", dict(CLI_CFG, id="cli1", label="预置 CLI"))
        write_cfg("cli1.json", dict(CLI_CFG, id="cli1", label="我自己的"))
        json.dump(["cli1"], open(self.hidden, "w", encoding="utf-8"))
        registry.load()
        spec = registry.get("cli1")
        self.assertTrue(spec["user"])            # agents.d 的同名配置盖过被隐藏的预置
        self.assertEqual(spec["label"], "我自己的")

    def test_builtin_still_not_removable(self):
        ok, _ = registry.remove_agent("claude")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
