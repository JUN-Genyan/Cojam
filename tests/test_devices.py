"""Own-device account, one-time codes, revocable credentials and routing."""
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

DATA = tempfile.mkdtemp(prefix="cojam_devices_")
os.environ["ROUNDTABLE_LOG_DIR"] = DATA
os.environ["ROUNDTABLE_NO_PRESETS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bridge import common, devices, registry, web

registry.load()

srv = web.ExclusiveServer(("127.0.0.1", 0), web.Handler)
srv.lan_url = None
srv.tailscale_url = None
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%s" % srv.server_address[1]


def call(path, obj=None, token=None, cookie=None, headers=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["X-RT-Token"] = token
    if cookie:
        h["Cookie"] = cookie
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode() if obj is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


def body_json(body):
    return json.loads(body.decode("utf-8"))


def page(headers=None):
    request = urllib.request.Request(BASE + "/", headers=headers or {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")


def injected_token(html):
    match = re.search(r'name="rt-token" content="([^"]+)"', html)
    return match.group(1) if match else ""


class DeviceCredentials(unittest.TestCase):
    def setUp(self):
        for name in ("account.json", "devices.json", "device_codes.json"):
            try:
                os.remove(os.path.join(DATA, name))
            except FileNotFoundError:
                pass

    def test_account_is_created_once(self):
        first = devices.account()
        second = devices.account()
        self.assertTrue(first["id"].startswith("acct_"))
        self.assertEqual(first, second)
        saved = json.loads(Path(DATA, "account.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["id"], first["id"])

    def test_code_is_one_time_and_creates_a_device(self):
        code = devices.create_code("我的手机")
        self.assertRegex(code["token"], r"^code_[0-9a-f]{24}\.")
        enrolled = devices.enroll(code["token"], "我的手机")
        self.assertTrue(enrolled["token"].startswith("dev_"))
        self.assertEqual(enrolled["device"]["label"], "我的手机")
        self.assertIsNotNone(devices.verify(enrolled["token"]))
        with self.assertRaises(devices.DeviceError) as caught:
            devices.enroll(code["token"], "再来一次")
        self.assertEqual(caught.exception.code, 410)
        saved = json.loads(Path(DATA, "devices.json").read_text(encoding="utf-8"))
        self.assertNotIn(enrolled["token"], json.dumps(saved))
        self.assertEqual(len(saved["devices"]), 1)

    def test_unknown_and_expired_codes(self):
        with self.assertRaises(devices.DeviceError) as caught:
            devices.enroll("code_ffffffffffffffffffffffff.nope", "x")
        self.assertEqual(caught.exception.code, 401)
        code = devices.create_code("旧手机")
        path = Path(DATA, "device_codes.json")
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["codes"][0]["expires"] = int(time.time()) - 1
        path.write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaises(devices.DeviceError) as caught:
            devices.enroll(code["token"], "旧手机")
        self.assertEqual(caught.exception.code, 410)

    def test_revoke_invalidates_the_token(self):
        code = devices.create_code("平板")
        enrolled = devices.enroll(code["token"], "平板")
        devices.revoke(enrolled["device"]["id"])
        self.assertIsNone(devices.verify(enrolled["token"]))
        self.assertEqual(devices.listing(), [])


class DeviceHTTP(unittest.TestCase):
    def make_device(self, label="我的手机"):
        status, body, _ = call("/api/devices/code", {"label": label, "base": "http://192.168.1.9:8766"}, token=web.RT_TOKEN)
        self.assertEqual(status, 200)
        code = body_json(body)["code"]
        status, body, headers = call("/api/devices/enroll", {"code": code, "label": label})
        self.assertEqual(status, 200)
        return body_json(body)["token"], body_json(body)["device"], headers

    def test_code_and_device_routes(self):
        token, device, headers = self.make_device()
        self.assertIn("cojam_device=" + token, headers.get("Set-Cookie", ""))
        status, body, _ = call("/api/devices", token=token)
        self.assertEqual(status, 200)
        data = body_json(body)
        self.assertTrue(data["account"]["id"].startswith("acct_"))
        self.assertEqual([row["id"] for row in data["devices"]], [device["id"]])
        self.assertTrue(data["devices"][0]["current"])
        self.assertEqual(call("/api/rooms", token=token)[0], 200)
        status, catalog_body, _ = call("/api/agents", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(body_json(catalog_body)["owner_devices"][0]["id"], device["id"])
        self.assertNotIn(token, catalog_body.decode('utf-8'))
        self.assertEqual(call("/api/agents")[0], 403)

    def test_revoked_device_cannot_call_admin_api(self):
        token, device, _ = self.make_device("旧手机")
        self.assertEqual(call("/api/devices/revoke", {"id": device["id"]}, token=web.RT_TOKEN)[0], 200)
        self.assertEqual(call("/api/rooms", token=token)[0], 403)
        self.assertIsNone(devices.verify(token))

    def test_root_routing_uses_credentials(self):
        owner = page()
        self.assertEqual(injected_token(owner), web.RT_TOKEN)
        remote = page({"Host": "192.168.1.9"})
        self.assertIn("联系人接入密钥", remote)
        self.assertEqual(injected_token(remote), "")
        token, device, _ = self.make_device("手机")
        with_cookie = page({"Host": "192.168.1.9", "Cookie": "cojam_device=" + token})
        self.assertEqual(injected_token(with_cookie), token)
        forwarded = page({"Host": "192.168.1.9", "X-Forwarded-For": "1.2.3.4",
                          "Cookie": "cojam_device=" + token})
        self.assertEqual(injected_token(forwarded), token)
        revoked = call("/api/devices/revoke", {"id": device["id"]}, token=web.RT_TOKEN)
        self.assertEqual(revoked[0], 200)
        after = page({"Host": "192.168.1.9", "Cookie": "cojam_device=" + token})
        self.assertIn("联系人接入密钥", after)

    def test_cookie_is_only_for_pages_and_files(self):
        token, _, _ = self.make_device("手机")
        self.assertEqual(call("/api/devices/code", {"label": "x"}, cookie="cojam_device=" + token)[0], 403)
        files = Path(DATA, "meetings", "room-1", "files")
        files.mkdir(parents=True, exist_ok=True)
        (files / "note.txt").write_text("hello", encoding="utf-8")
        status, body, _ = call("/files/room-1/files/note.txt", cookie="cojam_device=" + token)
        self.assertEqual((status, body), (200, b"hello"))

    def test_device_session_does_not_drive_owner_lifecycle(self):
        token, _, _ = self.make_device("手机")
        status, body, _ = call("/api/ui/session", {"tab": "t", "action": "open"}, token=token)
        self.assertEqual(status, 200)
        self.assertEqual(body_json(body), {"ok": True, "tracked": False})

    def test_info_reports_device_support(self):
        status, body, _ = call("/api/server/info")
        data = body_json(body)
        self.assertEqual(status, 200)
        self.assertTrue(data["owner_devices_supported"])
        self.assertIn("tailscale_url", data)

    def test_enroll_page_is_public_and_does_not_store_tokens(self):
        request = urllib.request.Request(BASE + "/enroll", headers={"Host": "192.168.1.9"})
        with urllib.request.urlopen(request, timeout=5) as response:
            html = response.read().decode("utf-8")
        self.assertIn("接入我的设备", html)
        self.assertIn("/api/devices/enroll", html)
        self.assertIn("location.replace('/')", html)
        self.assertNotIn("localStorage", html)


if __name__ == "__main__":
    unittest.main()
