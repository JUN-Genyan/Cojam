"""LAN reachability: a private-address listener beside the loopback-only admin one."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['ROUNDTABLE_LOG_DIR'] = tempfile.mkdtemp(prefix='bridge_lan_')
from bridge import common, web


class LanAddress(unittest.TestCase):
    def test_only_private_addresses_are_usable(self):
        self.assertEqual(common.pick_lan_ip(['127.0.0.1', '8.8.8.8', '192.168.1.23']), '192.168.1.23')
        self.assertIsNone(common.pick_lan_ip(['127.0.0.1', '169.254.2.3', '100.64.0.2']))
        self.assertIsNone(common.pick_lan_ip(['not-an-ip', '']))

    def test_order_prefers_the_default_route(self):
        self.assertEqual(common.pick_lan_ip(['10.0.0.7', '192.168.1.23']), '10.0.0.7')
        self.assertEqual(common.pick_lan_ip(['172.16.4.9']), '172.16.4.9')

    def test_tailscale_address_is_detected_separately(self):
        import subprocess
        completed = subprocess.CompletedProcess([], 0, stdout='100.64.0.5\nfd7a:115c:a1e0::1\n', stderr='')
        with patch('shutil.which', return_value=r'C:\Program Files\Tailscale\tailscale.exe'), \
             patch.object(common.subprocess, 'run', return_value=completed):
            self.assertEqual(common.tailscale_ipv4(), '100.64.0.5')

    def test_tailscale_address_is_none_when_unavailable(self):
        with patch('shutil.which', return_value=None), \
             patch.object(common.os, 'name', 'nt'), \
             patch.object(common.os.path, 'isfile', return_value=False):
            self.assertIsNone(common.tailscale_ipv4())
        with patch('shutil.which', return_value='/usr/bin/tailscale'), \
             patch.object(common.subprocess, 'run', side_effect=OSError):
            self.assertIsNone(common.tailscale_ipv4())


class LanListener(unittest.TestCase):
    def test_no_private_address_means_no_listener(self):
        with patch.object(common, 'lan_ipv4', return_value=None):
            self.assertEqual(web.start_lan_listener(0), (None, None))

    def test_second_listener_reports_its_own_address(self):
        with patch.object(common, 'lan_ipv4', return_value='127.0.0.1'):
            server, url = web.start_lan_listener(0)
        self.assertIsNotNone(server)
        try:
            self.assertEqual(url, 'http://127.0.0.1:%d' % server.server_address[1])
            self.assertEqual(server.lan_url, url)
            with urllib.request.urlopen(url + '/api/server/info', timeout=5) as response:
                self.assertEqual(__import__('json').loads(response.read())['lan_url'], url)
            request = urllib.request.Request(url + '/', headers={'Host': '192.168.1.9:' + str(server.server_address[1])})
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertIn('联系人接入密钥', response.read().decode('utf-8'))
        finally:
            server.shutdown()
            server.server_close()

    def test_loopback_listener_keeps_the_admin_page(self):
        server = web.ExclusiveServer(('127.0.0.1', 0), web.Handler)
        server.lan_url = None
        import threading
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = 'http://127.0.0.1:%d' % server.server_address[1]
            with urllib.request.urlopen(url + '/', timeout=5) as response:
                self.assertNotIn('联系人接入密钥', response.read().decode('utf-8'))
        finally:
            server.shutdown()
            thread.join(4)
            server.server_close()


class TailscaleListener(unittest.TestCase):
    def test_tailscale_listener_reports_its_own_url(self):
        with patch.object(common, 'tailscale_ipv4', return_value='127.0.0.1'):
            server, url = web.start_tailscale_listener(0)
        self.assertIsNotNone(server)
        try:
            self.assertEqual(url, 'http://127.0.0.1:%d' % server.server_address[1])
            self.assertEqual(server.tailscale_url, url)
            with urllib.request.urlopen(url + '/api/server/info', timeout=5) as response:
                self.assertEqual(__import__('json').loads(response.read())['tailscale_url'], url)
            request = urllib.request.Request(url + '/', headers={'Host': '100.64.0.5:8767'})
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertIn('联系人接入密钥', response.read().decode('utf-8'))
        finally:
            server.shutdown()
            server.server_close()

    def test_no_tailscale_address_means_no_listener(self):
        with patch.object(common, 'tailscale_ipv4', return_value=None):
            self.assertEqual(web.start_tailscale_listener(0), (None, None))


if __name__ == '__main__':
    unittest.main()
