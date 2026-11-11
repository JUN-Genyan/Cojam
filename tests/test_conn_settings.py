# -*- coding: utf-8 -*-
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bridge.common as common
import bridge.conn_settings as conn_settings
import bridge.members as members


class TestConnSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_log = common.LOG_DIR
        common.LOG_DIR = self.tmp.name
        conn_settings.LOG_DIR = self.tmp.name
        conn_settings._cache.update(mtime=None, data=None, path=None)

    def tearDown(self):
        common.LOG_DIR = self.old_log
        conn_settings.LOG_DIR = self.old_log
        conn_settings._cache.update(mtime=None, data=None, path=None)
        self.tmp.cleanup()

    def test_defaults_and_clamping(self):
        snap = conn_settings.snapshot()
        self.assertEqual(snap["rev"], 0)
        self.assertEqual(snap["global"]["heartbeat_interval"], 10)
        self.assertEqual(snap["global"]["offline_after"], 60)

        saved = conn_settings.save(global_values={"heartbeat_interval": 1, "offline_after": 9999, "reconnect": False})
        self.assertEqual(saved["rev"], 1)
        self.assertEqual(saved["global"]["heartbeat_interval"], 3)
        self.assertEqual(saved["global"]["offline_after"], 600)
        self.assertEqual(saved["global"]["reconnect"], 0)

    def test_per_contact_override_and_clear(self):
        conn_settings.save(global_values={"heartbeat_interval": 15, "offline_after": 90})
        conn_settings.save(pid="cursor", contact_values={"heartbeat_interval": 5, "task_timeout_discuss": 120})
        self.assertEqual(conn_settings.get("heartbeat_interval", "cursor"), 5)
        self.assertEqual(conn_settings.get("offline_after", "cursor"), 90)
        self.assertEqual(conn_settings.get("heartbeat_interval", "claude"), 15)
        self.assertEqual(common.timeout_for("discuss", "cursor")[1], 120)

        conn_settings.save(pid="cursor", clear_contact=True)
        self.assertEqual(conn_settings.get("heartbeat_interval", "cursor"), 15)

    def test_member_config_and_ttl(self):
        conn_settings.save(pid="antigravity", contact_values={"offline_after": 25, "heartbeat_interval": 6})
        cfg = conn_settings.member_config("antigravity")
        self.assertEqual(cfg["heartbeat_interval"], 6)
        self.assertEqual(cfg["offline_after"], 25)
        self.assertEqual(members.ttl("antigravity"), 25)


if __name__ == "__main__":
    unittest.main()
