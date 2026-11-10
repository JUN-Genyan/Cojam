"""The original failure was hidden by the browser's non-JSON error handling."""
from pathlib import Path
import shutil
import subprocess
import unittest


class ContactRoutes(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to test contact refresh')
    def test_contact_refresh_ignores_stale_responses_and_preserves_list(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('static/workspace.js','utf8');
const part=source.slice(source.indexOf('let contactsRequestSequence'),source.indexOf('function drawRoomIdentity'));
let pending=[],writes=0,markup='';
const list={scrollTop:37,set innerHTML(v){writes++;markup=v},get innerHTML(){return markup}};
const context=vm.createContext({fetch:()=>new Promise(resolve=>pending.push(resolve)),
 document:{activeElement:null,getElementById:id=>id==='contactList'?list:{}},
 esc:String,CONTACTS:[],agCat:{},drawQuick:()=>{}});
vm.runInContext(part,context);
const response=label=>({ok:true,json:async()=>({agents:[{id:'a',label,letter:'A',color:'#fff',driver:'cli',enabled:true,user:true}]})});
(async()=>{const old=context.refreshContacts(),fresh=context.refreshContacts();pending[1](response('新'));await fresh;
 pending[0](response('旧'));await old;assert.match(markup,/新/);assert.doesNotMatch(markup,/旧/);assert.equal(writes,1);
 const same=context.refreshContacts();pending[2](response('新'));await same;assert.equal(writes,1);assert.equal(list.scrollTop,37);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to test contact routing UI')
    def test_same_brand_contacts_keep_separate_mentions_and_channels(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const ws=fs.readFileSync('static/workspace.js','utf8'), html=fs.readFileSync('index.html','utf8');
const part=ws.slice(ws.indexOf('function contactStatusLabel'),ws.indexOf('function drawRoomIdentity'));
const quick=html.match(/function drawQuick\(\)\{[\s\S]*?\n\}/)[0];
const boxes={contactList:{innerHTML:''},contactCount:{},quick:{innerHTML:'',dataset:{},querySelectorAll(){return []}}};
const contacts=[{id:'codex',label:'我的 Codex',kind:'agent',driver:'codex',enabled:true,builtin:true,color:'#fff',letter:'C'},
 {id:'friend-codex',label:'朋友的 Codex',kind:'agent',driver:'external',enabled:true,user:true,color:'#fff',letter:'C'}];
const ctx=vm.createContext({document:{getElementById:id=>boxes[id]},esc:s=>String(s),CONTACTS:contacts,
 agCat:{owner_devices:[{label:'我的手机',online:true}],connections:{'friend-codex':{state:'connected',label:'已连接'}},members:{'friend-codex':{automatic:true,device:'朋友的 Mac'}}},
 AG_DRV:{},S:{participants:['codex','friend-codex']},quickEl:boxes.quick});
vm.runInContext(part+'\n'+quick,ctx);ctx.drawContacts();ctx.drawQuick();
assert.match(boxes.contactList.innerHTML,/我的手机/);assert.match(boxes.contactList.innerHTML,/朋友的 Mac/);assert.match(boxes.contactList.innerHTML,/@friend-codex/);
assert.match(boxes.quick.innerHTML,/data-ins="@codex "[^>]*>@我的 Codex/);
assert.match(boxes.quick.innerHTML,/data-ins="@friend-codex "[^>]*>@朋友的 Codex/);
'''
        result=subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to test message rendering')
    def test_history_render_yields_and_stops_after_room_switch(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert/strict');
const html=fs.readFileSync('index.html','utf8');
const source=html.match(/async function poll\(\)\{[\s\S]*?\n\}/)[0];
let rendered=[], yielded=0;
const context=vm.createContext({RID:'first', roomViewGeneration:0, polling:null, S:{}, since:0, curMeeting:'first',
 api:()=>'', fetch:async()=>({status:200,json:async()=>({meeting:'first',messages:Array.from({length:45},(_,id)=>({id})),next:45})}),
 feed:{scrollHeight:0,scrollTop:0,clientHeight:0}, add:m=>rendered.push(m.id), syncUndo:()=>{},
 setTimeout:f=>{yielded++; if(yielded===1)context.roomViewGeneration++; f()},
 document:{hidden:true}, loadState:async()=>{}, drawStatus:()=>{}, markSeen:()=>{}});
vm.runInContext(source,context);
(async()=>{await context.poll(); assert.equal(yielded,1); assert.equal(rendered.length,20); assert.equal(context.since,0)})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


class WorkspaceFolderPicker(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to test the desktop bridge')
    def test_selection_and_cancel_keep_the_expected_path(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('static/workspace.js','utf8');
const helper=source.match(/function attachWorkspaceFolderPicker\(inputId\)\{[\s\S]*?\n\}/)[0];
let button, next='D:\\Project';
const parent={append(b){button=b}}, input={value:'old',parentElement:parent,dispatchEvent(){}};
const context=vm.createContext({document:{getElementById:()=>input,createElement:()=>({})},
 window:{pywebview:{api:{choose_workdir:async()=>next}},addEventListener(){}},Event:function(){},alert:()=>{throw Error('unexpected alert')}});
vm.runInContext(helper,context);context.attachWorkspaceFolderPicker('pWd');
(async()=>{assert.equal(button.hidden,false);await button.onclick();assert.equal(input.value,'D:\\Project');
 next=null;await button.onclick();assert.equal(input.value,'D:\\Project');assert.equal(button.disabled,false)})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node','-e',script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


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
    if (url === '/') return {text: async () => 'guest page'};
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
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to test guest reconnection')
    def test_temporary_unauthorized_response_preserves_credential_and_recovers(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('static/join.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const storage={cojam_guest_token:'guest-token',cojam_guest_last_room:'room-2'},elements={},timers=[];
const node=id=>elements[id]||(elements[id]={value:'',textContent:'',disabled:false,hidden:false,
 children:[],replaceChildren(){this.children=[]},append(v){this.children.push(v)},
 classList:{toggle(){}},scrollTop:0,scrollHeight:0});
let available=false;
const ctx=vm.createContext({URL,URLSearchParams,document:{getElementById:node,createElement:()=>node('child'+Math.random())},
 location:{hash:'',pathname:'/join',search:''},history:{replaceState(){}},setInterval(fn){timers.push(fn)},
 localStorage:{getItem:k=>storage[k]||null,setItem:(k,v)=>storage[k]=v,removeItem:k=>delete storage[k]},
 fetch:async(url)=>{if(url==='/')return {text:async()=> 'guest page'};
 if(url==='/api/external/rooms')return available?{ok:true,json:async()=>({contact:{label:'访客'},rooms:[{id:'room-2',title:'会话'}]})}:{ok:false,status:401,json:async()=>({error:'暂时无法验证'})};
 if(url.startsWith('/api/external/messages'))return {ok:true,json:async()=>({messages:[],next:0,permissions:{post:true}})};
 throw Error('unexpected '+url)}});
vm.runInContext(source,ctx);
setTimeout(()=>{try{assert.equal(storage.cojam_guest_token,'guest-token');assert.match(node('error').textContent,/无法验证/);
 available=true;timers[1]();setTimeout(()=>{try{assert.equal(node('title').textContent,'会话');assert.equal(storage.cojam_guest_token,'guest-token')}
 catch(e){console.error(e);process.exitCode=1}},30)}catch(e){console.error(e);process.exitCode=1}},30);
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to test restoring a guest room')
    def test_reopening_remembered_guest_restores_last_room(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('static/join.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const storage={cojam_guest_token:'guest-token',cojam_guest_last_room:'room-2'},elements={};
const node=id=>elements[id]||(elements[id]={value:'',textContent:'',disabled:false,hidden:false,
 children:[],replaceChildren(){this.children=[]},append(v){this.children.push(v)},
 classList:{toggle(){}},scrollTop:0,scrollHeight:0});
const calls=[];
const ctx=vm.createContext({URL,URLSearchParams,document:{getElementById:node,createElement:()=>node('child'+Math.random())},
 location:{hash:'',pathname:'/join',search:''},history:{replaceState(){}},setInterval(){},
 localStorage:{getItem:k=>storage[k]||null,setItem:(k,v)=>storage[k]=v,removeItem:k=>delete storage[k]},
 fetch:async(url,opts)=>{calls.push(url);if(url==='/')return {text:async()=> 'guest page'};
 if(url==='/api/external/rooms')return {ok:true,json:async()=>({contact:{label:'访客'},rooms:[{id:'room-1',title:'一'},{id:'room-2',title:'二'}]})};
 if(url.startsWith('/api/external/messages'))return {ok:true,json:async()=>({messages:[],next:0,permissions:{post:true}})};
 throw Error('unexpected '+url)}});
vm.runInContext(source,ctx);
setTimeout(()=>{try{assert.equal(node('title').textContent,'二');assert.ok(calls.some(x=>x.includes('room=room-2')));
 assert.equal(storage.cojam_guest_token,'guest-token');assert.equal(storage.cojam_guest_last_room,'room-2');
 }catch(e){console.error(e);process.exitCode=1}},30);
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
    if (url === '/') return {text: async () => 'guest page'};
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
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to test device restoration')
    def test_remembered_device_restores_cookie_and_handles_revocation(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync('static/join.html','utf8');
const source=html.match(/async function restoreOwnDevice\(\)\{[\s\S]*?\n  \}/)[0];
async function run(revoked, acceptsCookie, hash='', remembered=true) {
 let removed=false, redirected=false, destination='', requests=[]; const own='dev_abc';
 const ctx=vm.createContext({URLSearchParams,saved:()=>remembered?own:'',save:(key,value)=>{if(!value)removed=true},location:{hash,replace:value=>{redirected=true;destination=value}},
  fetch:async (url,options)=>{requests.push(url); if(url==='/api/devices/restore')return {status:revoked?401:200,ok:!revoked};
   if(url==='/api/invites/resolve-owner')return {ok:true,json:async()=>({room:'room-1'})};
   return {text:async()=>acceptsCookie?'<meta name="rt-token" content="dev_abc">':'join page'};}});
 vm.runInContext(source,ctx); const result=await ctx.restoreOwnDevice(); return {result,removed,redirected,destination,requests};
}
(async()=>{let a=await run(false,true);assert.equal(a.redirected,true);assert.equal(a.result,true);
 a=await run(false,true,'#invite=inv_test');assert.equal(a.destination,'/?r=room-1');assert.ok(a.requests.includes('/api/invites/resolve-owner'));
 a=await run(false,true,'#invite=inv_test',false);assert.equal(a.destination,'/?r=room-1');
 a=await run(true,false);assert.equal(a.removed,true);assert.equal(a.redirected,false);
 a=await run(false,false);assert.equal(a.redirected,false);assert.equal(a.removed,false);})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to test reusable links')
    def test_reusable_device_link_is_exchanged_and_removed_from_address(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('static/enroll.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
let saved={},calls=[],redirect='',clean='';const els={};
const el=id=>els[id]||(els[id]={value:'',textContent:'',disabled:false,hidden:false});
const ctx=vm.createContext({URLSearchParams,navigator:{userAgent:'iPhone'},document:{getElementById:el},
 location:{hash:'#device=dev_test',pathname:'/enroll',search:'',origin:'http://example',replace:x=>redirect=x},
 history:{replaceState:(a,b,c)=>clean=c},localStorage:{getItem:k=>saved[k]||'',setItem:(k,v)=>saved[k]=v,removeItem:k=>delete saved[k]},
 fetch:async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return {ok:true,status:200}}});
vm.runInContext(source,ctx);
setTimeout(()=>{try{assert.equal(clean,'/enroll');assert.equal(calls[0].url,'/api/devices/restore');
 assert.equal(calls[0].body.token,'dev_test');assert.equal(saved.cojam_owner_device_token,'dev_test');assert.equal(redirect,'/');
 }catch(e){console.error(e);process.exitCode=1}},20);
'''
        result = subprocess.run(['node','-e',script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to test the device wizard')
    def test_enrollment_completion_closes_wizard_after_refresh(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const js=fs.readFileSync('static/connections.js','utf8');
const fn=js.match(/async function pollDeviceEnrollment\(w, id\) \{[\s\S]*?\n\}/)[0];
let closed=0, refreshed=0, cleared=0, status={devices:[]}, message={textContent:''};
const ctx=vm.createContext({wizardCurrent:()=>true, clearInterval:()=>cleared++,
 connectionGet:async()=>[true,status], refreshContacts:async()=>{refreshed++},hideModal:()=>closed++,document:{getElementById:()=>message}});
vm.runInContext(fn,ctx);
(async()=>{const w={type:'mydevice',step:2,timer:1};await ctx.pollDeviceEnrollment(w,'dev_x');
 assert.equal(closed,0);status={devices:[{id:'dev_x',last_seen:123}]};await ctx.pollDeviceEnrollment(w,'dev_x');
 assert.equal(closed,0);assert.match(message.textContent,/复制/);w.deviceCopied=true;await ctx.pollDeviceEnrollment(w,'dev_x');
 assert.equal(closed,1);assert.equal(refreshed,1);assert.equal(cleared,1)})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute browser helper functions')
    def test_my_device_can_enter_custom_tailscale_serve_address(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const elements = {};
function node(id) { return elements[id] ||= {value:'', hidden:false, innerHTML:'', textContent:'',
  classList:{toggle(){}}, querySelectorAll(){return []}}; }
const context = vm.createContext({URL, uiIcon:()=>'', esc:s=>s,
  location:{origin:'http://127.0.0.1:8765'}, document:{getElementById:node},
  fetch:async()=>({ok:true,json:async()=>({account:{},devices:[]})})});
vm.runInContext(fs.readFileSync('static/connections.js','utf8'), context);
(async()=>{
  const w={base:'http://100.64.0.5:8767',info:{lan_url:'http://192.168.1.5:8766',tailscale_url:'http://100.64.0.5:8767'},form:{}};
  await context.renderDeviceSetup(w);
  const select=node('connDeviceBase'), custom=node('connDeviceCustomBase');
  assert.match(select.innerHTML, /自定义 HTTPS 地址/);
  assert.ok(select.innerHTML.indexOf('192.168.1.5:8766') < select.innerHTML.indexOf('100.64.0.5:8767'));
  assert.match(node('connDeviceBaseHint').textContent, /只有这台电脑能打开/);
  select.value='__custom__'; select.onchange();
  assert.equal(custom.hidden,false);
  custom.value='https://host.example.ts.net/'; custom.oninput();
  assert.equal(w.base,'https://host.example.ts.net');
  select.value='http://100.64.0.5:8767'; select.onchange();
  assert.equal(custom.hidden,true);
  assert.equal(w.base,'http://100.64.0.5:8767');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], cwd=root, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
  {tailscale_url: 'http://100.64.0.5:8767', lan_url: lan}, origin), lan);   // 同一 WiFi 默认局域网
assert.equal(context.connectionDefaultBase('mydevice',
  {tailscale_url: 'http://100.64.0.5:8767'}, origin), 'http://100.64.0.5:8767');
assert.equal(context.connectionDefaultBase('mydevice', {lan_url: lan}, origin), lan);
assert.equal(context.connectionDefaultBase('mydevice', {}, origin), origin);
assert.equal(context.connectionDefaultBase('mydevice',
  {lan_url: lan, tailscale_url: 'http://100.64.0.5:8767'}, 'http://100.64.0.5:8767'), 'http://100.64.0.5:8767');
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
  RID: null, roomViewGeneration: 0, resetRoomView: () => {}, post: () => {}, api: () => '', drawRooms: () => {},
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
vm.runInContext(js.match(/async function checkConnectionBackend\(signal\) \{[\s\S]*?\n\}/)[0], context);
(async () => {
 context.fetch = async () => ({ok:false,status:404,json:async()=>{throw Error('HTML response')}});
 let result = await context.post('/api/contacts/check', {});
 assert.equal(result[0], false); assert.match(result[1].error, /旧版本/);
 await assert.rejects(context.checkConnectionBackend(), /无法连接/);
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


class MessageActions(unittest.TestCase):
    def test_desktop_keeps_reply_and_copy_controls_visible(self):
        html = (Path(__file__).resolve().parents[1] / 'index.html').read_text(encoding='utf-8')
        self.assertIn('class="cp"', html)
        self.assertIn('.who .rp,.who .cp', html)
        self.assertNotIn('@media(hover:hover) and (pointer:fine){.who .rp{display:none}}', html)


if __name__ == '__main__':
    unittest.main()
