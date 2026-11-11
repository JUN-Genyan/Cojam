# -*- coding: utf-8 -*-
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_BOOT_TMP = tempfile.mkdtemp(prefix="rt_disc_")
os.environ["ROUNDTABLE_LOG_DIR"] = _BOOT_TMP

from bridge import access, common, connections, discovery, install, registry


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old_log = common.LOG_DIR
        common.LOG_DIR = discovery.LOG_DIR = connections.LOG_DIR = registry.LOG_DIR = access.LOG_DIR = self.tmp.name
        os.makedirs(os.path.join(self.tmp.name, "agents.d"), exist_ok=True)
        discovery._state.update(scanning=False, last_scan=0.0, items=[])

    def tearDown(self):
        common.LOG_DIR = discovery.LOG_DIR = connections.LOG_DIR = registry.LOG_DIR = access.LOG_DIR = self.old_log
        discovery._state.update(scanning=False, last_scan=0.0, items=[])
        self.tmp.cleanup()

    def test_scan_classifies_direct_and_mcp_agents(self):
        fake_bin = os.path.join(self.tmp.name, "claude.exe")
        Path(fake_bin).write_text("echo 1\n", encoding="utf-8")
        fake_desk = os.path.join(self.tmp.name, "Antigravity.exe")
        Path(fake_desk).write_text("desk\n", encoding="utf-8")

        fake_catalog = [
            {
                "id": "claude",
                "label": "Claude Code",
                "cli": {"driver": "claude", "bin": ["claude"], "paths": [fake_bin], "verified": True},
            },
            {
                "id": "antigravity",
                "label": "Google Antigravity",
                "desktop": {"detect": [fake_desk]},
                "mcp": {"id": "antigravity", "label": "Antigravity", "kind": "json", "path": os.path.join(self.tmp.name, "mcp.json")},
            },
            {
                "id": "missing-one",
                "label": "Missing Agent",
                "cli": {"driver": "cli", "bin": ["not-installed-xyz"]},
            },
        ]
        with patch.object(discovery, "load_catalog", return_value=fake_catalog), \
             patch.object(discovery, "_uninstall_names", return_value=set()), \
             patch.object(common, "find_executable", side_effect=lambda name, extra_paths=(): fake_bin if name == "claude" else None):
            st = discovery.scan()

        by_id = {x["id"]: x for x in st["items"]}
        self.assertTrue(by_id["claude"]["present"])
        self.assertEqual(by_id["claude"]["level"], "direct")
        self.assertTrue(by_id["antigravity"]["present"])
        self.assertEqual(by_id["antigravity"]["level"], "mcp")
        self.assertFalse(by_id["missing-one"]["present"])
        self.assertEqual(st["pending_count"], 2)

        discovery.dismiss("antigravity")
        st2 = discovery.status()
        self.assertEqual(st2["pending_count"], 1)

    def test_present_falls_back_to_uninstall_registry(self):
        """装在 D:\\cursor 这类非默认目录时，扫描和一键接入要得出同一个结论。"""
        host = {"id": "cursor", "label": "Cursor", "bin": [], "detect": [], "uninstall": ["cursor"]}
        with patch.object(install, "app_installed", return_value=True):
            self.assertTrue(install.present(host))
        with patch.object(install, "app_installed", return_value=False):
            self.assertFalse(install.present(host))

    def test_connect_mcp_writes_config_and_returns_standby_prompt(self):
        mcp_path = os.path.join(self.tmp.name, "windsurf_mcp.json")
        fake_desk = os.path.join(self.tmp.name, "Windsurf.exe")
        Path(fake_desk).write_text("desk\n", encoding="utf-8")
        host = {"id": "windsurf-test", "label": "Windsurf", "kind": "json-serverurl",
                "path": mcp_path, "detect": [fake_desk], "restart": "重启 Windsurf"}
        fake_catalog = [{
            "id": "windsurf",
            "label": "Windsurf",
            "desktop": {"detect": [fake_desk]},
            "mcp": host,
        }]
        install.HOSTS.append(host)
        try:
            with patch.object(discovery, "load_catalog", return_value=fake_catalog), \
                 patch.object(discovery, "_uninstall_names", return_value=set()):
                discovery.scan()
                ok, res = discovery.connect("windsurf", via="mcp")
            self.assertTrue(ok, res)
            self.assertEqual(res["via"], "mcp")
            self.assertEqual(res["standby_prompt"], "/cojam_standby")
            written = json.loads(Path(mcp_path).read_text(encoding="utf-8"))
            self.assertIn("ai-bridge", written["mcpServers"])
            self.assertIn("serverUrl", written["mcpServers"]["ai-bridge"])
            self.assertIn("Authorization", written["mcpServers"]["ai-bridge"]["headers"])
        finally:
            install.HOSTS[:] = [h for h in install.HOSTS if h["id"] != "windsurf-test"]


if __name__ == "__main__":
    unittest.main()
