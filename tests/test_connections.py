"""Onboarding verifies real evidence, separates channels, and invites without replacing members."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import test_contacts_access as support
from bridge import access, connections, registry, room as R

call = support.call
DATA = support.DATA


class Callback(BaseHTTPRequestHandler):
    last_body = None
    last_auth = None
    redirected = False

    def log_message(self, *args):
        pass

    def do_POST(self):
        Callback.last_body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        Callback.last_auth = self.headers.get('Authorization')
        status = 401 if self.path == '/unauthorized' else 302 if self.path == '/redirect' else 200
        self.send_response(status)
        if status == 302:
            self.send_header('Location', '/target')
        self.end_headers()
        if self.path == '/target':
            Callback.redirected = True
        self.wfile.write(b'<html>OK</html>' if self.path == '/html' else b'{}' if self.path == '/empty' else b'x' * 70000 if self.path == '/large' else b'{"text":"connected"}')


callback = ThreadingHTTPServer(('127.0.0.1', 0), Callback)
threading.Thread(target=callback.serve_forever, daemon=True).start()


class Connections(unittest.TestCase):
    def setUp(self):
        registry.save_agent({'id': 'visitor', 'kind': 'user', 'driver': 'external', 'enabled': True})
        registry.save_agent({'id': 'remote-bot', 'driver': 'external', 'enabled': True})
        self.key = access.credential('visitor')
        self.bot_key = access.credential('remote-bot')

    def test_backend_version_and_private_runtime(self):
        from bridge.version import VERSION
        code, info = call('/api/server/info')
        self.assertEqual((code, info['app'], info['version']), (200, 'AI Bridge', VERSION))
        self.assertNotIn('data', info)
        self.assertEqual(call('/api/server/runtime')[0], 403)
        self.assertEqual(call('/api/server/runtime', owner=True)[1]['data'], os.path.realpath(DATA))
        self.assertEqual(call('/api/server/stop', {})[0], 403)

    def test_contact_write_failure_is_actionable_json(self):
        with patch.object(registry, 'save_agent', side_effect=PermissionError('locked')):
            code, result = call('/api/agents/add', {'spec':{'id':'new-person','driver':'external'}}, owner=True)
        self.assertEqual(code, 500)
        self.assertIn('写入权限', result['error'])

    def test_stop_refuses_busy_rooms(self):
        with patch.object(R.rooms, 'listing', return_value=[{'running':['claude'],'queued':0}]):
            code, result = call('/api/server/stop', {}, owner=True)
        self.assertEqual(code, 409)
        self.assertIn('任务', result['error'])

    def test_owner_polling_does_not_prove_connection(self):
        self.assertEqual(connections.status('visitor')['state'], 'waiting')
        for path, body in [('/api/contacts/status', {'id':'visitor'}), ('/api/contacts/check', {'id':'visitor'})]:
            self.assertEqual(call(path, body, owner=True)[0], 200)
        call('/api/agents', owner=True)
        self.assertEqual(connections.status('visitor')['state'], 'waiting')

    def test_web_handshake_without_any_invitation(self):
        self.assertEqual(call('/api/external/hello', {'client':'AI Bridge 网页'}, self.key)[0], 200)
        status = connections.status('visitor')
        self.assertEqual((status['state'], status['channel']), ('connected', 'web'))
        # The regular poll keeps the web identity rather than changing it to generic HTTP.
        call('/api/external/rooms', key=self.key)
        self.assertEqual(connections.status('visitor')['channel'], 'web')

    def test_mcp_records_the_authenticated_client(self):
        msg = {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','clientInfo':{'name':'Test Harness','version':'1'}}}
        self.assertEqual(call('/mcp', msg, self.bot_key)[0], 200)
        state = connections.status('remote-bot')
        self.assertEqual((state['state'], state['channel'], state['client']), ('connected','mcp','Test Harness'))
        self.assertNotIn(self.bot_key, Path(DATA, 'connections.json').read_text())
        self.assertNotIn('digest', state)

    def test_invalid_key_does_not_change_state(self):
        self.assertEqual(call('/api/external/hello', {'client':'imposter'}, 'invalid')[0], 401)
        self.assertEqual(connections.status('visitor')['state'], 'waiting')
        self.assertEqual(call('/api/contacts/check', {'id':'visitor'}, self.key)[0], 403)

    def test_reset_revocation_and_stale_connections(self):
        call('/api/external/hello', {'client':'web'}, self.key)
        last = connections.status('visitor')['last_seen']
        with patch.object(connections.time, 'time', return_value=last + connections.FRESH_SECONDS + 1):
            self.assertEqual(connections.status('visitor')['state'], 'offline')
        replacement = access.credential('visitor')
        self.assertEqual(connections.status('visitor')['state'], 'waiting')
        connections.seen('visitor', self.key, 'http')
        self.assertEqual(connections.status('visitor')['state'], 'waiting')
        call('/api/external/hello', {}, replacement)
        access.credential('visitor', revoke=True)
        self.assertEqual(connections.status('visitor')['state'], 'unconfigured')

    def test_local_validation_does_not_call_model_or_issue_key(self):
        registry.save_agent({'id':'version-test','driver':'cli','bin':[sys.executable]})
        result = connections.check('version-test')
        self.assertEqual(result['state'], 'ready')
        self.assertFalse(result['has_key'])
        self.assertIn('首次', result['detail'])

    def test_missing_and_timed_out_local_programs(self):
        registry.save_agent({'id':'missing-test','driver':'cli','bin':['certainly-no-such-program-xx']})
        self.assertEqual(connections.check('missing-test')['state'], 'failed')
        registry.save_agent({'id':'timeout-test','driver':'cli','bin':[sys.executable]})
        with patch.object(connections.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fake', 8)):
            self.assertEqual(connections.check('timeout-test')['state'], 'failed')

    def http_spec(self, path):
        registry.save_agent({'id':'http-test','driver':'http','endpoint':'http://127.0.0.1:%d%s' % (callback.server_address[1], path), 'token':'private-test-secret'})

    def test_http_protocol_probe(self):
        self.http_spec('/ok')
        self.assertEqual(connections.check('http-test')['state'], 'ready')
        self.assertEqual(Callback.last_auth, 'Bearer private-test-secret')
        self.assertEqual(Callback.last_body['type'], 'connection_test')
        self.assertEqual(Callback.last_body['mode'], 'discuss')
        self.assertEqual(Callback.last_body['bridge_key'], '')
        self.assertFalse(Callback.last_body['permissions']['work'])
        self.assertFalse(Callback.last_body['permissions']['files'])
        # A changed endpoint invalidates earlier evidence.
        self.http_spec('/empty')
        self.assertEqual(connections.status('http-test')['state'], 'unchecked')

    def test_http_rejects_html_empty_and_oversized_replies(self):
        for path in ['/html','/empty','/large']:
            with self.subTest(path=path):
                self.http_spec(path)
                self.assertEqual(connections.check('http-test')['state'], 'failed')

    def test_http_auth_and_redirect_failures_are_clear(self):
        for path in ['/unauthorized','/redirect']:
            self.http_spec(path)
            result = connections.check('http-test')
            self.assertEqual(result['state'], 'failed')
            self.assertNotIn('private-test-secret', json.dumps(result))
        self.assertFalse(Callback.redirected)

    def test_inviting_one_contact_preserves_other_members_and_permissions(self):
        room = R.rooms.create('邀请不替换', '', DATA, participants=['remote-bot'])
        room.set_members(['remote-bot'], {'remote-bot':{'post':False}})
        body = {'contact':'visitor','permissions':{'read':True,'post':True,'work':False}}
        status, result = call('/api/r/' + support.quote(room.id) + '/invite', body, owner=True)
        self.assertEqual(status, 200)
        self.assertEqual(room.ais, ['remote-bot','visitor'])
        self.assertFalse(access.permissions(room,'remote-bot')['post'])
        self.assertFalse(access.permissions(room,'visitor')['work'])
        # Repeated submission is idempotent, and external credentials cannot invite.
        call('/api/r/' + support.quote(room.id) + '/invite', body, owner=True)
        self.assertEqual(room.ais.count('visitor'), 1)
        self.assertEqual(call('/api/r/' + support.quote(room.id) + '/invite', body, self.key)[0], 403)

    def test_failed_invites_leave_members_unchanged(self):
        room = R.rooms.create('不能加入', '', DATA, participants=['remote-bot'])
        path = '/api/r/' + support.quote(room.id) + '/invite'
        self.assertEqual(call(path, {'contact':'unknown','permissions':{}}, owner=True)[0], 409)
        self.assertEqual(room.ais, ['remote-bot'])
        self.assertEqual(call(path, {'contact':'visitor','permissions':{'admin':True}}, owner=True)[0], 409)
        self.assertEqual(room.ais, ['remote-bot'])
        room.state['running'].append('remote-bot')
        try:
            self.assertEqual(call(path, {'contact':'visitor','permissions':{}}, owner=True)[0], 409)
            self.assertEqual(room.ais, ['remote-bot'])
        finally:
            room.state['running'].clear()

    def test_save_failure_does_not_grant_membership_or_permissions(self):
        room = R.rooms.create('保存失败', '', DATA, participants=['remote-bot'])
        original = json.loads(json.dumps(room.meta))
        with patch.object(room, 'save_meta', side_effect=PermissionError('locked')):
            status, response = call('/api/r/' + support.quote(room.id) + '/invite',
                                    {'contact':'visitor','permissions':{'read':True,'work':True}}, owner=True)
        self.assertEqual(status, 500)
        self.assertIn('重试', response['error'])
        self.assertEqual(room.meta, original)
        self.assertFalse(access.allowed(room,'visitor'))


if __name__ == '__main__':
    unittest.main()
