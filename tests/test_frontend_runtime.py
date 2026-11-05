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
  vm.runInContext(idMatch[0], context);
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
 context.fetch = async () => ({ok:true,json:async()=>({app:'AI Bridge',version:'old',api_revision:1})});
 await assert.rejects(context.checkConnectionBackend(), /版本不一致/);
 const version = fs.readFileSync('pyproject.toml','utf8').match(/^version = "([^"]+)"/m)[1];
 context.fetch = async () => ({ok:true,json:async()=>({app:'AI Bridge',version,api_revision:1})});
 await context.checkConnectionBackend();
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        result = subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
