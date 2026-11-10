# -*- coding: utf-8 -*-
"""本机扫描快照 /api/agents/scan 和原子连接 /api/agents/connect。不起真 AI。"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_contacts_access as support
from bridge import registry, room as R, scan


def wait_scan(timeout=30):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        status, data = support.call("/api/agents/scan", owner=True)
        assert status == 200, data
        if data["state"] in ("done", "partial", "error"):
            return data
        time.sleep(0.05)
    raise AssertionError("扫描没有在 %d 秒内结束" % timeout)


def save(raw):
    ok, msg = registry.save_agent(raw)
    assert ok, msg


class ScanFlow(unittest.TestCase):
    def setUp(self):
        while any(t.name == "cojam-agent-scan" and t.is_alive() for t in threading.enumerate()):
            time.sleep(0.02)
        scan._reset()
        self.addCleanup(scan._reset)

    def test_scan_requires_browser_and_reports_lifecycle(self):
        self.assertEqual(support.call("/api/agents/scan")[0], 403)                    # GET 要凭证
        self.assertEqual(support.call("/api/agents/scan", {})[0], 403)                # POST 也要
        status, data = support.call("/api/agents/scan", {}, owner=True)
        self.assertEqual((status, data["state"]), (200, "scanning"), data)
        data = wait_scan()
        self.assertEqual(data["state"], "done", data["errors"])
        self.assertTrue(data["scan_id"])
        self.assertEqual(data["progress"], 1.0)
        self.assertTrue(data["log"])                                                  # 日志区始终有内容
        # 快照缓存：TTL 内再点「扫描」直接回上一份；force 才重扫
        _, again = support.call("/api/agents/scan", {}, owner=True)
        self.assertEqual(again["scan_id"], data["scan_id"])
        _, forced = support.call("/api/agents/scan", {"force": True}, owner=True)
        self.assertNotEqual(forced["scan_id"], data["scan_id"])
        self.assertEqual(wait_scan()["state"], "done")

    def test_ready_candidate_and_atomic_connect(self):
        save({"id": "scancli", "label": "Scan CLI", "driver": "cli", "bin": [sys.executable],
              "discovery": {"version_command": ["--version"]}})
        room = R.rooms.create("扫描连接", "", support.DATA, participants=[])
        support.call("/api/agents/scan", {"force": True}, owner=True)
        snap = wait_scan()
        row = next((c for c in snap["candidates"] if c["id"] == "scancli"), None)
        self.assertIsNotNone(row, snap["candidates"])
        self.assertEqual(row["status"], "ready")
        self.assertIn("connect", row["actions"])
        self.assertTrue(str(row["version"]).startswith("Python"), row)
        self.assertTrue(row["installations"])
        # 会话必须显式选：不给 room 就只接入，不进会话
        status, result = support.call("/api/agents/connect", {"candidate": "scancli"}, owner=True)
        self.assertEqual((status, result.get("ok")), (200, True), result)
        self.assertIsNone(result["room"])
        self.assertNotIn("scancli", room.ais)
        # 显式选择会话后一次完成：接入 + 加入
        status, result = support.call("/api/agents/connect", {"candidate": "scancli", "room": room.id}, owner=True)
        self.assertEqual((status, result.get("ok")), (200, True), result)
        self.assertEqual(result["room"]["id"], room.id)
        self.assertIn("scancli", room.ais)
        # 再扫：已经在会话里 → connected，行上带着所在会话
        wait_scan()
        _, snap = support.call("/api/agents/scan", {"force": True}, owner=True)
        row = next(c for c in wait_scan()["candidates"] if c["id"] == "scancli")
        self.assertEqual(row["status"], "connected")
        self.assertEqual([r["id"] for r in row["rooms"]], [room.id])
        # 重复连接同一会话是幂等的
        status, result = support.call("/api/agents/connect", {"candidate": "scancli", "room": room.id}, owner=True)
        self.assertEqual((status, result.get("ok")), (200, True), result)
        # 坏参数各自有明确的错
        self.assertEqual(support.call("/api/agents/connect", {"candidate": "nope"}, owner=True)[0], 404)
        self.assertEqual(support.call("/api/agents/connect", {"candidate": "scancli", "room": "nope"}, owner=True)[0], 404)
        self.assertEqual(support.call("/api/agents/connect", {"candidate": "scancli", "installation": "missing"}, owner=True)[0], 400)
        self.assertEqual(support.call("/api/agents/connect", {"candidate": "scancli"})[0], 403)

    def test_probe_failure_marks_attention_but_still_connectable(self):
        save({"id": "scanbad", "driver": "cli", "bin": [sys.executable],
              "discovery": {"version_command": ["--zzz-not-a-flag"]}})
        support.call("/api/agents/scan", {"force": True}, owner=True)
        snap = wait_scan()
        row = next((c for c in snap["candidates"] if c["id"] == "scanbad"), None)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "attention")
        self.assertTrue(row["note"])
        self.assertIn("diagnose", row["actions"])
        status, result = support.call("/api/agents/connect", {"candidate": "scanbad"}, owner=True)
        self.assertEqual((status, result.get("ok")), (200, True), result)             # 仍然连接

    def test_missing_user_contact(self):
        save({"id": "gonecli", "label": "Gone", "driver": "cli", "bin": ["definitely-missing-xyz-9"]})
        support.call("/api/agents/scan", {"force": True}, owner=True)
        row = next((c for c in wait_scan()["candidates"] if c["id"] == "gonecli"), None)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "missing")
        self.assertIn("rescan", row["actions"])

    def test_desktop_merge_and_external_statuses(self):
        save({"id": "deskcli", "label": "Desk CLI", "driver": "cli", "bin": [sys.executable]})
        save({"id": "scandesk", "label": "Scan Desk", "driver": "external",
              "detect": [sys.executable], "linked_cli": "deskcli"})
        save({"id": "deskonly", "label": "Desk Only", "driver": "external", "detect": [sys.executable]})
        save({"id": "deskext", "label": "Desk Ext", "driver": "external", "detect": [sys.executable],
              "endpoint": "http://127.0.0.1:9/mcp"})
        support.call("/api/agents/scan", {"force": True}, owner=True)
        snap = wait_scan()
        rows = {c["id"]: c for c in snap["candidates"]}
        desk = rows.get("scandesk")
        self.assertIsNotNone(desk, snap["candidates"])
        self.assertEqual(desk["status"], "desktop_cli")
        self.assertEqual(desk["cli"]["id"], "deskcli")            # 同一设备只算一行，CLI 挂在里面
        self.assertNotIn("deskcli", rows)
        self.assertEqual(desk["actions"], ["connect_cli"])
        self.assertEqual(rows["deskonly"]["status"], "desktop_only")
        self.assertEqual(rows["deskonly"]["actions"], ["how"])
        self.assertEqual(rows["deskext"]["status"], "desktop_external")
        self.assertEqual(snap["counts"]["desktop"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
