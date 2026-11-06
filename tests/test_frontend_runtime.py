"""The original failure was hidden by the browser's non-JSON error handling."""
from pathlib import Path
import shutil
import subprocess
import unittest


class GuestInvitation(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_full_invitation_url_can_be_pasted_as_key(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const html = fs.readFileSync('static/join.html', 'utf8');
const match = html.match(/function invitationKey\(value\) \{[\s\S]*?\n\}/);
assert.ok(match, 'invitationKey missing');
const context = vm.createContext({URL, URLSearchParams});
vm.runInContext(match[0], context);
const key = 'VOTx5vAXUUhog34gr3_xomX_RhsF4KLFcpklNsDCVrA';
assert.equal(context.invitationKey('http://192.168.1.5:8766/join#key=' + key), key);
assert.equal(context.invitationKey('https://example.test/join#key=' + encodeURIComponent(key)), key);
assert.equal(context.invitationKey('  ' + key + '  '), key);
assert.equal(context.invitationKey(''), '');
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_join_page_script_runs_and_autoconnects_from_hash(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const html = fs.readFileSync('static/join.html', 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const key = 'VOTx5vAXUUhog34gr3_xomX_RhsF4KLFcpklNsDCVrA';
const elements = {};
function node(id) {
  if (!elements[id]) elements[id] = {
    id, value: '', textContent: '', disabled: false, onclick: null, onsubmit: null,
    children: [], replaceChildren() { this.children = []; },
    append(child) { this.children.push(child); },
    classList: { toggle() {} }, scrollTop: 0, scrollHeight: 0
  };
  return elements[id];
}
const calls = [], historyCalls = [];
const context = vm.createContext({
  URL, URLSearchParams,
  document: { getElementById: node, createElement: () => node('created-' + Math.random()) },
  location: { hash: '#key=' + key, pathname: '/join', search: '' },
  history: { replaceState: (state, title, url) => historyCalls.push(url) },
  setInterval: () => 0,
  fetch: async (url, options) => {
    calls.push({ url, auth: (options.headers || {}).Authorization, body: options.body });
    if (url === '/api/external/hello') return { ok: true, json: async () => ({ contact: { label: '测试联系人', kind: 'user' } }) };
    if (url === '/api/external/rooms') return { ok: true, json: async () => ({
      contact: { label: '测试联系人', kind: 'user' }, rooms: [{ id: 'room-1', title: '测试会议' }]
    }) };
    if (url.startsWith('/api/external/messages')) return { ok: true, json: async () => ({ messages: [], next: 0, permissions: { post: false } }) };
    throw Error('unexpected ' + url);
  }
});
vm.runInContext(source, context);
setTimeout(() => {
  try {
    assert.equal(calls[0].url, '/api/external/hello');
    assert.equal(calls[0].auth, 'Bearer ' + key);
    assert.equal(historyCalls.length, 1);
    assert.equal(historyCalls[0], '/join');
    assert.equal(node('key').value, '');
    assert.match(node('identity').textContent, /测试联系人/);
    assert.equal(node('list').children.length, 1);
    assert.equal(node('title').textContent, '测试会议');
    assert.ok(calls.some(call => call.url.startsWith('/api/external/messages?room=room-1')),
              'a single invited room should open automatically');
  } catch (error) { console.error(error); process.exitCode = 1; }
}, 20);
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class GuestIdentity(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_helpers_and_page_redeem_flow(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const html = fs.readFileSync('static/join.html', 'utf8');
const match = html.match(/function invitationValue\(value\) \{[\s\S]*?\n\}/);
assert.ok(match, 'invitationValue missing');
const helpers = vm.createContext({URL, URLSearchParams});
vm.runInContext(match[0], helpers);
assert.equal(helpers.invitationValue('http://192.168.1.5:8766/join#invite=inv_abc.def'), 'inv_abc.def');
assert.equal(helpers.invitationValue('  inv_abc.def  '), 'inv_abc.def');
assert.equal(helpers.invitationValue('http://192.168.1.5:8766/join#key=keyvalue'), '');

const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = {};
function node(id) {
  if (!elements[id]) elements[id] = {
    id, value: '', textContent: '', hidden: false, disabled: false, onclick: null, onsubmit: null,
    children: [], replaceChildren() { this.children = []; }, append(c) { this.children.push(c); },
    classList: { toggle() {}, add() {}, remove() {} }, scrollTop: 0, scrollHeight: 0, focus() {}
  };
  return elements[id];
}
const calls = [], historyCalls = [], saved = {};
const store = {getItem: k => (k in saved ? saved[k] : null), setItem: (k, v) => { saved[k] = String(v); },
               removeItem: k => { delete saved[k]; }};
const context = vm.createContext({
  URL, URLSearchParams,
  document: { getElementById: node, createElement: () => node('created-' + Math.random()) },
  location: { hash: '#invite=inv_abc.def', pathname: '/join', search: '' },
  history: { replaceState: (state, title, url) => historyCalls.push(url) },
  localStorage: store,
  setInterval: () => 0,
  navigator: { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1' },
  crypto: { getRandomValues: array => { array.fill(7); return array; } },
  fetch: async (url, options) => {
    calls.push({ url, auth: (options.headers || {}).Authorization, body: options.body ? JSON.parse(options.body) : null });
    if (url === '/api/guest/redeem') return { ok: true, json: async () => ({
      ok: true, token: 'guest-secret', contact: { id: 'guest-11111111', label: '访客' },
      room: 'room-1', rooms: [{ id: 'room-1', title: '测试会话' }], created: true
    }) };
    if (url === '/api/external/hello') return { ok: true, json: async () => ({ contact: { label: '访客', kind: 'user' } }) };
    if (url === '/api/external/rooms') return { ok: true, json: async () => ({
      contact: { label: '访客', kind: 'user' }, rooms: [{ id: 'room-1', title: '测试会话' }]
    }) };
    if (url.startsWith('/api/external/messages')) return { ok: true, json: async () => ({ messages: [], next: 0, permissions: { post: true } }) };
    throw Error('unexpected ' + url);
  }
});
node('namePrompt').hidden = true;
vm.runInContext(source, context);
setTimeout(() => {
  try {
    assert.equal(node('namePrompt').hidden, false, 'name prompt should be shown before redeeming');
    assert.equal(calls.length, 0, 'redeem must wait for the name');
    node('nameConfirm').onclick();
    setTimeout(() => {
      try {
        assert.ok(calls.some(c => c.url === '/api/guest/redeem'));
        const redeem = calls.find(c => c.url === '/api/guest/redeem');
        assert.equal(redeem.body.invite, 'inv_abc.def');
        assert.match(redeem.body.device_key, /^[0-9a-f]{64}$/);
        assert.equal(saved.cojam_guest_token, 'guest-secret');
        assert.equal(historyCalls[0], '/join');
        assert.ok(calls.some(c => c.url === '/api/external/rooms'));
        assert.equal(node('title').textContent, '测试会话');
        assert.equal(node('text').disabled, false, 'invited guest can send messages');
        node('key').value = 'http://192.168.1.5:8766/join#invite=inv_second.ticket';
        node('connect').onclick();
        setTimeout(() => {
          try {
            assert.ok(calls.some(c => c.url === '/api/guest/redeem' && c.body.invite === 'inv_second.ticket'));
          } catch (error) { console.error(error); process.exitCode = 1; }
        }, 20);
      } catch (error) { console.error(error); process.exitCode = 1; }
    }, 20);
  } catch (error) { console.error(error); process.exitCode = 1; }
}, 20);
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class GuestWizard(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_invite_url_and_status_labels(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const js = fs.readFileSync('static/connections.js', 'utf8');
const context = vm.createContext({uiIcon: () => '', URL, Date});
vm.runInContext(js, context);
assert.equal(context.connectionGuestUrl('http://192.168.1.9:8766/', 'inv_abc.def'),
             'http://192.168.1.9:8766/join#invite=inv_abc.def');
assert.equal(context.connectionInviteStatus({revoked: true}), '已撤销');
assert.equal(context.connectionInviteStatus({expires: 1}), '已过期');
assert.equal(context.connectionInviteStatus({expires: 9999999999, guest_count: 2}), '已加入 2 位游客');
assert.equal(context.connectionInviteStatus({expires: 9999999999, guest_count: 0}), '等待加入');
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class LanInvite(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_lan_base_and_loopback_warning(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const js = fs.readFileSync('static/connections.js', 'utf8');
const context = vm.createContext({uiIcon: () => '', URL});
vm.runInContext(js, context);
const origin = 'http://127.0.0.1:8765', lan = 'http://192.168.1.5:8766';
assert.equal(context.connectionDefaultBase('user', {lan_url: lan}, origin), lan);
assert.equal(context.connectionDefaultBase('external', {lan_url: lan}, origin), lan);
assert.equal(context.connectionDefaultBase('http', {lan_url: lan}, origin), origin);
assert.equal(context.connectionDefaultBase('user', {}, origin), origin);
assert.equal(context.connectionDefaultBase('mydevice',
  {tailscale_url: 'http://100.64.0.5:8767', lan_url: lan}, origin),
  'http://100.64.0.5:8767');
assert.equal(context.connectionDefaultBase('mydevice', {lan_url: lan}, origin), lan);
assert.equal(context.connectionDefaultBase('mydevice', {}, origin), origin);
const remote = 'https://bridge.example.test';
for (const type of ['user', 'external', 'mydevice'])
  assert.equal(context.connectionDefaultBase(type, {lan_url: lan, tailscale_url: 'http://100.64.0.5:8767'}, remote), remote);
assert.ok(vm.runInContext('CONNECTION_TYPES.mydevice', context));
assert.match(context.connectionBaseWarning('http://127.0.0.1:8765'), /只有这台电脑/);
assert.match(context.connectionBaseWarning('http://localhost:8765'), /只有这台电脑/);
assert.match(context.connectionBaseWarning('http://[::1]:8765'), /只有这台电脑/);
assert.equal(context.connectionBaseWarning(lan), '');
assert.match(context.connectionBaseWarning('not a url'), /格式/);
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_qr_is_generated_locally(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue(Path(root, 'static', 'qrcode.min.js').is_file())
        html = Path(root, 'index.html').read_text(encoding='utf-8')
        self.assertIn('/static/qrcode.min.js', html)
        self.assertLess(html.index('/static/qrcode.min.js'), html.index('/static/connections.js'))
        self.assertNotIn('qrserver', html + Path(root, 'static', 'connections.js').read_text(encoding='utf-8'))


class DesktopOwnerTab(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_owner_tab_validation_and_shared_instance(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const html = fs.readFileSync('index.html', 'utf8');
const valid = 'A'.repeat(32);
const idMatch = html.match(/function ownerTabId\(search\)\{[\s\S]*?\n\}/);
assert.ok(idMatch, 'ownerTabId missing');
const idContext = vm.createContext({URLSearchParams});
vm.runInContext(idMatch[0], idContext);
assert.equal(idContext.ownerTabId('?owner_tab=' + valid), valid);
assert.equal(idContext.ownerTabId('?owner_tab=short'), '');
assert.equal(idContext.ownerTabId('?owner_tab=' + 'B'.repeat(81)), '');
assert.equal(idContext.ownerTabId('?owner_tab=' + encodeURIComponent('bad/id')), '');
assert.equal(idContext.ownerTabId(''), '');

function runOwner(search, reloaded, stored) {
  const marker = '// Each owner tab registers separately.';
  const begin = html.indexOf('(function(){', html.indexOf(marker));
  const end = html.indexOf('})();', begin) + 5;
  const source = html.slice(begin, end);
  const calls = [], listeners = [], storedWrites = [];
  const context = vm.createContext({
    URLSearchParams,
    crypto: {randomUUID: () => '11111111-2222-3333-4444-555555555555'},
    performance: {getEntriesByType: () => [{type: reloaded ? 'reload' : 'navigate'}]},
    sessionStorage: {getItem: () => stored, setItem: (key, value) => storedWrites.push([key, value])},
    fetch: (url, options) => { calls.push({url, body: JSON.parse(options.body)}); return Promise.resolve(); },
    setInterval: () => 1,
    addEventListener: (type, handler) => listeners.push({type, handler}),
    document: {getElementById: () => ({onclick: null})},
    location: {search},
    confirm: () => false, alert: () => {}
  });
  const instanceMatch = html.match(/function ownerInstance\(\)\{[\s\S]*?\n\}/);
  assert.ok(instanceMatch, 'ownerInstance missing');
  vm.runInContext(idMatch[0], context);
  vm.runInContext(instanceMatch[0], context);
  vm.runInContext(source, context);
  return {calls, listeners, storedWrites};
}

let result = runOwner('?owner_tab=' + valid, false, null);
assert.equal(result.calls[0].body.tab, valid);
assert.equal(result.calls[0].body.instance, valid);
assert.equal(result.storedWrites.length, 0);
assert.equal(result.listeners.some(item => item.type === 'beforeunload'), false);
assert.equal(result.listeners.some(item => item.type === 'pagehide'), true);

result = runOwner('', true, 'stored-tab-value');
assert.equal(result.calls[0].body.tab, 'stored-tab-value');
assert.equal(result.calls[0].body.instance, '11111111222233334444555555555555');
assert.deepEqual(result.storedWrites, [['cojam_owner_tab', 'stored-tab-value']]);
assert.equal(result.listeners.some(item => item.type === 'beforeunload'), true);

result = runOwner('?owner_tab=short');
assert.equal(result.calls[0].body.tab, '11111111222233334444555555555555');
assert.equal(result.storedWrites.length, 1);

const roomMatch = html.match(/function switchRoom\(id,replace\)\{[\s\S]*?\n\}/);
assert.ok(roomMatch, 'switchRoom missing');
let pushed = null;
const roomContext = vm.createContext({
  RID: null, resetRoomView: () => {}, post: () => {}, api: () => '', drawRooms: () => {},
  poll: () => Promise.resolve(), feed: {scrollTop: 0, scrollHeight: 0}, queueJump: () => {},
  location: {search: '?owner_tab=' + valid},
  history: {replaceState: (state, title, url) => { pushed = url; },
            pushState: (state, title, url) => { pushed = url; }},
  encodeURIComponent, URLSearchParams
});
vm.runInContext(roomMatch[0], roomContext);
roomContext.switchRoom('room-1', true);
assert.ok(pushed.includes('owner_tab=' + valid), pushed);
assert.ok(pushed.includes('r=room-1'), pushed);
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class InsecureContext(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_owner_tab_registers_without_random_uuid(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const html = fs.readFileSync('index.html', 'utf8');
const marker = '// Each owner tab registers separately.';
const begin = html.indexOf('(function(){', html.indexOf(marker));
const end = html.indexOf('})();', begin) + 5;
const source = html.slice(begin, end);
const calls = [], storedWrites = [];
const context = vm.createContext({
  URLSearchParams,
  crypto: {},
  performance: {getEntriesByType: () => [{type: 'navigate'}]},
  sessionStorage: {getItem: () => null, setItem: (key, value) => storedWrites.push([key, value])},
  fetch: (url, options) => { calls.push({url, body: JSON.parse(options.body)}); return Promise.resolve(); },
  setInterval: () => 1,
  addEventListener: () => {},
  document: {getElementById: () => ({onclick: null})},
  location: {search: ''}, confirm: () => false, alert: () => {}
});
const idMatch = html.match(/function ownerTabId\(search\)\{[\s\S]*?\n\}/);
assert.ok(idMatch, 'ownerTabId missing');
const instanceMatch = html.match(/function ownerInstance\(\)\{[\s\S]*?\n\}/);
assert.ok(instanceMatch, 'ownerInstance missing');
vm.runInContext(idMatch[0], context);
vm.runInContext(instanceMatch[0], context);
vm.runInContext(source, context);
assert.equal(calls.length, 1);
const id = calls[0].body.instance;
assert.match(id, /^[A-Za-z0-9_-]{16,80}$/);
assert.equal(calls[0].body.tab, id);
assert.deepEqual(storedWrites, [['cojam_owner_tab', id]]);
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class DeviceWizard(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_device_code_url_and_name(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const js = fs.readFileSync('static/connections.js', 'utf8');
const context = vm.createContext({uiIcon: () => '', URL});
vm.runInContext(js, context);
assert.equal(context.connectionDeviceName('  '), '我的设备');
assert.equal(context.connectionDeviceName('  我的 iPhone  '), '我的 iPhone');
assert.equal(context.connectionDeviceUrl('http://192.168.1.9:8766', 'code_abc.def'),
             'http://192.168.1.9:8766/enroll#code=code_abc.def');
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class BrowserErrors(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_legacy_backend_and_http_errors_are_actionable(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const html = fs.readFileSync('index.html', 'utf8');
const js = fs.readFileSync('static/connections.js', 'utf8');
const context = vm.createContext({});
vm.runInContext(html.match(/^function apiError.*$/m)[0], context);
vm.runInContext(html.match(/^async function post.*$/m)[0], context);
vm.runInContext(js.match(/async function checkConnectionBackend\(\) \{[\s\S]*?\n\}/)[0], context);
(async () => {
 context.fetch = async () => ({ok:false,status:404,json:async()=>{throw Error('HTML response')}});
 let result = await context.post('/api/contacts/check', {});
 assert.equal(result[0], false); assert.match(result[1].error, /旧版本/);
 await assert.rejects(context.checkConnectionBackend(), /旧版后台/);
 context.fetch = async () => ({ok:false,status:403,json:async()=>({error:'owner only'})});
 result = await context.post('/api/agents/add', {});
 assert.equal(result[0], false); assert.match(result[1].error, /刷新/);
 context.fetch = async () => {throw Error('connection refused')};
 result = await context.post('/api/agents/add', {});
 assert.equal(result[0], false); assert.match(result[1].error, /无法连接/);
 const version = fs.readFileSync('pyproject.toml','utf8').match(/^version = "([^"]+)"/m)[1];
 const revision = Number(fs.readFileSync('bridge/version.py','utf8').match(/^API_REVISION = (\d+)/m)[1]);
 context.fetch = async () => ({ok:true,json:async()=>({app:'AI Bridge',version:'old',api_revision:revision})});
 await assert.rejects(context.checkConnectionBackend(), /版本不一致/);
 context.fetch = async () => ({ok:true,json:async()=>({app:'AI Bridge',version,api_revision:revision})});
 await context.checkConnectionBackend();
 context.fetch = async () => ({ok:true,json:async()=>({app:'AI Bridge',version,api_revision:revision+1})});
 await assert.rejects(context.checkConnectionBackend(), /版本不一致/);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result = subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
