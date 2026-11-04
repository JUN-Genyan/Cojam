"""Deployment regressions: stale backend reuse and hot-served incompatible assets."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['ROUNDTABLE_LOG_DIR'] = tempfile.mkdtemp(prefix='bridge_runtime_')
from bridge import common, server_runtime, web
from bridge.version import VERSION, API_REVISION


class OldServer(BaseHTTPRequestHandler):
    version = VERSION
    busy = False
    old = False
    other = False
    stops = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/':
            self.send_response(200); self.end_headers()
            self.wfile.write(b'<meta name="rt-token" content="test-key">')
            return
        if self.old:
            self.send_response(404); self.end_headers(); self.wfile.write(b'not found')
            return
        if self.path == '/api/server/info':
            value = {'app':'AI Bridge', 'version':self.version, 'api_revision':API_REVISION}
        elif self.path == '/api/server/runtime' and self.headers.get('X-RT-Token') == 'test-key':
            value = {'install':common.HERE, 'data':'another-directory' if self.other else common.LOG_DIR}
        else:
            self.send_response(403); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(value).encode())

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path != '/api/server/stop' or self.headers.get('X-RT-Token') != 'test-key':
            self.send_response(403); self.end_headers(); return
        if self.busy:
            self.send_response(409); self.end_headers(); return
        type(self).stops += 1
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
        threading.Thread(target=self.server.shutdown, daemon=True).start()


class Runtime(unittest.TestCase):
    def setUp(self):
        OldServer.version = VERSION
        OldServer.busy = OldServer.old = OldServer.other = False
        OldServer.stops = 0
        self.server = ThreadingHTTPServer(('127.0.0.1',0), OldServer)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]

    def serve(self):
        self.server.serve_forever(poll_interval=.05)
        self.server.server_close()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(4)

    def test_reuses_current_backend(self):
        self.assertEqual(server_runtime.reuse_or_stop(self.url)[0], 'reuse')
        self.assertEqual(OldServer.stops, 0)

    def test_legacy_backend_is_not_silently_opened(self):
        OldServer.old = True
        action, message = server_runtime.reuse_or_stop(self.url)
        self.assertEqual(action, 'error')
        self.assertIn('旧后台', message)
        self.assertEqual(OldServer.stops, 0)

    def test_other_data_directory_is_not_stopped(self):
        OldServer.version = 'old'
        OldServer.other = True
        self.assertEqual(server_runtime.reuse_or_stop(self.url)[0], 'error')
        self.assertEqual(OldServer.stops, 0)

    def test_busy_backend_is_not_stopped(self):
        OldServer.version = 'old'
        OldServer.busy = True
        action, message = server_runtime.reuse_or_stop(self.url)
        self.assertEqual(action, 'error')
        self.assertIn('任务', message)
        self.assertEqual(OldServer.stops, 0)

    def test_idle_same_installation_upgrades(self):
        OldServer.version = 'old'
        self.assertEqual(server_runtime.reuse_or_stop(self.url)[0], 'stopped')
        self.assertEqual(OldServer.stops, 1)

    def test_running_backend_keeps_matching_ui_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            index = Path(temp, 'index.html'); index.write_bytes(b'original page')
            script = Path(temp, 'connections.js'); script.write_bytes(b'original script')
            with patch.object(web, 'INDEX', str(index)), patch.object(web, 'STATIC', temp):
                server = web.ExclusiveServer(('127.0.0.1', 0), web.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                index.write_bytes(b'incompatible page'); script.write_bytes(b'incompatible script')
                base = 'http://127.0.0.1:%d' % server.server_address[1]
                try:
                    self.assertEqual(urllib.request.urlopen(base).read(), b'original page')
                    self.assertEqual(urllib.request.urlopen(base+'/static/connections.js').read(), b'original script')
                finally:
                    server.shutdown(); thread.join(); server.server_close()


if __name__ == '__main__':
    unittest.main()
