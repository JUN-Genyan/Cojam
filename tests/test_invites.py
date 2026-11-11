"""Invitation tickets and guest identities. No real AI calls."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

DATA = tempfile.mkdtemp(prefix="cojam_invites_")
os.environ["ROUNDTABLE_LOG_DIR"] = DATA
os.environ["ROUNDTABLE_NO_PRESETS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bridge import access, invites, registry, room as R, web

registry.load()


def clear_files():
    for name in ("invites.json", "guests.json", "contact_keys.json"):
        try:
            os.remove(os.path.join(DATA, name))
        except FileNotFoundError:
            pass
    agents = Path(DATA, "agents.d")
    if agents.is_dir():
        for path in agents.glob("guest-*.json"):
            path.unlink()


class InviteStore(unittest.TestCase):
    def setUp(self):
        clear_files()
        self.room = R.rooms.create("游客邀请测试", "", DATA, participants=[])

    def test_guest_credential_survives_registry_reload(self):
        issued = invites.create(self.room.id, {"read": True}, "朋友", 7)
        joined = invites.redeem(issued["token"], device_key="phone-key", name="手机", device="iPhone")
        pid = joined["contact"]["id"]
        registry.SPECS.clear()
        registry.load()
        self.assertEqual(access.identify(joined["token"]), pid)
        self.assertIn(self.room.id, [room["id"] for room in access.visible_rooms(pid)])

    def test_create_returns_token_and_stores_only_digest(self):
        issued = invites.create(self.room.id, {"read": True, "post": False}, "朋友", 7)
        self.assertRegex(issued["token"], r"^inv_[0-9a-f]{24}\.")
        self.assertEqual(issued["room"], self.room.id)
        self.assertFalse(issued["permissions"]["post"])
        saved = Path(DATA, "invites.json").read_text(encoding="utf-8")
        self.assertNotIn(issued["token"], saved)
        self.assertIn(issued["id"], saved)
        found = invites.find(issued["token"])
        self.assertEqual(found["id"], issued["id"])

    def test_listing_status_and_revoke(self):
        issued = invites.create(self.room.id)
        rows = invites.listing()
        self.assertEqual(rows[0]["status"], "waiting")
        self.assertEqual(rows[0]["status_label"], "等待加入")
        invites.revoke(issued["id"])
        with self.assertRaises(invites.InviteError) as caught:
            invites.find(issued["token"])
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(invites.listing()[0]["status"], "revoked")

    def test_expired_invite_is_gone(self):
        issued = invites.create(self.room.id)
        path = Path(DATA, "invites.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["invites"][0]["expires"] = int(time.time()) - 1
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(invites.InviteError) as caught:
            invites.find(issued["token"])
        self.assertEqual(caught.exception.code, 410)
        self.assertEqual(invites.listing()[0]["status"], "expired")

    def test_archived_room_rejects_new_invite(self):
        R.set_archived(self.room.id, True)
        with self.assertRaises(invites.InviteError) as caught:
            invites.create(self.room.id)
        self.assertEqual(caught.exception.code, 400)


class InviteRedeem(unittest.TestCase):
    def setUp(self):
        clear_files()
        self.room = R.rooms.create("游客兑换测试", "", DATA, participants=[])
        self.invite = invites.create(self.room.id, {"read": True, "post": True, "trigger": False})

    def test_first_redeem_creates_guest_and_joins_room(self):
        result = invites.redeem(self.invite["token"], device_key="device-a", name="小林", device="iPhone · Safari")
        self.assertTrue(result["token"])
        self.assertTrue(result["contact"]["id"].startswith("guest-"))
        self.assertEqual(result["contact"]["label"], "小林")
        self.assertEqual(result["room"], self.room.id)
        self.assertIn(result["contact"]["id"], self.room.ais)
        self.assertTrue(access.allowed(self.room, result["contact"]["id"], "post"))
        self.assertFalse(access.allowed(self.room, result["contact"]["id"], "trigger"))
        saved = Path(DATA, "guests.json").read_text(encoding="utf-8")
        self.assertNotIn("device-a", saved)
        row = invites.listing()[0]
        self.assertEqual(row["status"], "joined")
        self.assertEqual(row["guests"][0]["device"], "iPhone · Safari")
        self.assertEqual(access.identify(result["token"]), result["contact"]["id"])

    def test_same_contact_token_reuses_identity(self):
        first = invites.redeem(self.invite["token"], device_key="device-a", name="小林")
        second = invites.redeem(self.invite["token"], contact_token=first["token"], device_key="device-a")
        self.assertEqual(second["contact"]["id"], first["contact"]["id"])
        self.assertEqual(sum(1 for pid in registry.enabled_ids() if pid.startswith("guest-")), 1)
        self.assertEqual(len(invites.guest_listing()), 1)

    def test_same_device_key_recovers_and_rotates_key(self):
        first = invites.redeem(self.invite["token"], device_key="device-a", name="小林")
        second = invites.redeem(self.invite["token"], device_key="device-a")
        self.assertEqual(second["contact"]["id"], first["contact"]["id"])
        self.assertNotEqual(second["token"], first["token"])
        self.assertIsNone(access.identify(first["token"]))
        self.assertEqual(access.identify(second["token"]), first["contact"]["id"])

    def test_new_invite_on_same_device_reuses_identity(self):
        first = invites.redeem(self.invite["token"], device_key="device-a", name="小林")
        other_room = R.rooms.create("第二个会话", "", DATA, participants=[])
        later = invites.create(other_room.id)
        second = invites.redeem(later["token"], device_key="device-a")
        self.assertEqual(second["contact"]["id"], first["contact"]["id"])
        self.assertIn(first["contact"]["id"], other_room.ais)
        pid = first["contact"]["id"]
        saved = json.loads(Path(DATA, "guests.json").read_text(encoding="utf-8"))["guests"][pid]
        self.assertEqual(saved["invites"], [self.invite["id"], later["id"]])

    def test_agent_token_is_not_reused(self):
        registry.save_agent({"id": "bot", "driver": "external", "enabled": True})
        bot_key = access.credential("bot")
        result = invites.redeem(self.invite["token"], contact_token=bot_key, device_key="device-a")
        self.assertNotEqual(result["contact"]["id"], "bot")
        self.assertTrue(result["contact"]["id"].startswith("guest-"))

    def test_second_redeem_keeps_owner_permission_changes(self):
        first = invites.redeem(self.invite["token"], device_key="device-a")
        pid = first["contact"]["id"]
        ids = list(self.room.ais)
        perms = {a: access.permissions(self.room, a) for a in ids}
        perms[pid] = dict(perms[pid], post=False)
        ok, msg = self.room.set_members(ids, perms)
        self.assertTrue(ok, msg)
        invites.redeem(self.invite["token"], contact_token=first["token"], device_key="device-a")
        self.assertFalse(access.permissions(self.room, pid)["post"])

    def test_archived_room_creates_nothing(self):
        R.set_archived(self.room.id, True)
        before = len(registry.enabled_ids())
        with self.assertRaises(invites.InviteError) as caught:
            invites.redeem(self.invite["token"], device_key="device-a")
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(len(registry.enabled_ids()), before)

    def test_remove_guest_cleans_rooms_key_and_record(self):
        result = invites.redeem(self.invite["token"], device_key="device-a")
        pid = result["contact"]["id"]
        invites.remove_guest(pid)
        self.assertNotIn(pid, self.room.ais)
        self.assertIsNone(access.identify(result["token"]))
        self.assertIsNone(registry.get(pid))
        self.assertEqual(invites.guest_listing(), [])


srv = web.ExclusiveServer(("127.0.0.1", 0), web.Handler)
srv.lan_url = "http://192.168.1.9:8766"
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%s" % srv.server_address[1]


def call(path, obj=None, key=None, owner=False):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    if owner:
        headers["X-RT-Token"] = web.RT_TOKEN
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode() if obj is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class InviteHTTP(unittest.TestCase):
    def setUp(self):
        clear_files()
        self.room = R.rooms.create("接口游客测试", "", DATA, participants=[])

    def test_owner_routes_and_public_redeem(self):
        status, data = call("/api/invites", {"room": self.room.id, "permissions": {"post": False},
                                             "base": "http://192.168.1.9:8766"})
        self.assertEqual(status, 403)
        status, data = call("/api/invites", {"room": self.room.id, "permissions": {"post": False},
                                             "label": "朋友", "base": "http://192.168.1.9:8766"}, owner=True)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["invite"]["id"].startswith("inv_"))
        self.assertTrue(data["url"].startswith("http://192.168.1.9:8766/join#invite=inv_"))
        token = data["token"]
        status, listing = call("/api/invites", owner=True)
        self.assertEqual(status, 200)
        self.assertEqual(listing["invites"][0]["label"], "朋友")
        status, redeemed = call("/api/guest/redeem", {"invite": token, "device_key": "dev-http",
                                                      "name": "小林", "device": "iPhone · Safari"})
        self.assertEqual(status, 200, redeemed)
        self.assertTrue(redeemed["token"])
        self.assertEqual(redeemed["room"], self.room.id)
        self.assertEqual(redeemed["contact"]["label"], "小林")
        self.assertEqual(len(redeemed["rooms"]), 1)
        status, listing = call("/api/invites", owner=True)
        self.assertEqual(listing["invites"][0]["guest_count"], 1)
        self.assertFalse(listing["invites"][0]["guests"][0]["online"])

    def test_redeem_errors_are_actionable(self):
        self.assertEqual(call("/api/guest/redeem", {"invite": "inv_x.y"})[0], 404)
        status, data = call("/api/invites", {"room": "missing"}, owner=True)
        self.assertEqual(status, 400)
        issued = call("/api/invites", {"room": self.room.id}, owner=True)[1]
        call("/api/invites/revoke", {"id": issued["invite"]["id"]}, owner=True)
        status, data = call("/api/guest/redeem", {"invite": issued["token"], "device_key": "d"})
        self.assertEqual(status, 403)
        self.assertIn("撤销", data["error"])

    def test_guest_cannot_use_owner_api(self):
        issued = call("/api/invites", {"room": self.room.id}, owner=True)[1]
        token = call("/api/guest/redeem", {"invite": issued["token"], "device_key": "d"})[1]["token"]
        self.assertEqual(call("/api/invites", key=token)[0], 403)
        self.assertEqual(call("/api/agents", key=token)[0], 403)
        self.assertEqual(call("/api/external/rooms", key=token)[0], 200)

    def test_remove_guest_route(self):
        issued = call("/api/invites", {"room": self.room.id}, owner=True)[1]
        redeemed = call("/api/guest/redeem", {"invite": issued["token"], "device_key": "d"})[1]
        pid = redeemed["contact"]["id"]
        self.assertEqual(call("/api/guests/remove", {"id": pid}, owner=True)[0], 200)
        self.assertNotIn(pid, self.room.ais)
        self.assertIsNone(registry.get(pid))


if __name__ == '__main__':
    unittest.main()
