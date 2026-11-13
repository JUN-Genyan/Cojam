"""Owner-facing Agent forms persist and reach runtime; tests use isolated fake agents."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_contacts_access as support
from bridge import access, agent_settings, agents, connections, install, registry, room as R

DATA = support.DATA
call = support.call


class AgentSettings(unittest.TestCase):
    def setUp(self):
        self.settings_path = Path(DATA, 'agent_settings.json')
        if self.settings_path.exists():
            self.settings_path.unlink()
        self.spec = {'id':'editable', 'driver':'cli', 'bin':[sys.executable], 'label':'Editable',
                     'models':['fast','reasoning'], 'efforts':['low','high'], 'default':['reasoning','high'],
                     'model_efforts':{'fast':[], 'reasoning':['low','high']},
                     'env':{'PRIVATE_VARIABLE':'secret-env'}, 'parse':{'type':'json','text':'text'},
                     'args':{'new':['fake.py','{prompt}'], 'model':['--model','{model}'], 'effort':['--effort','{effort}']}}
        registry.save_agent(self.spec)
        registry.load()
        for room in R.rooms.all():
            room.state['running'].clear()
            room.state['queued'].clear() if isinstance(room.state.get('queued'), list) else None

    def config(self, pid='editable'):
        status, data = call('/api/agents/config?id='+pid, owner=True)
        self.assertEqual(status, 200, data)
        return data

    def save(self, changes, pid='editable', revision=None):
        return call('/api/agents/config', {'id':pid, 'revision':revision or self.config(pid)['revision'],
                                         'changes':changes}, owner=True)

    def test_owner_only_and_no_credential_or_environment_in_view(self):
        registry.save_agent({'id':'viewer','driver':'external','enabled':True})
        key = access.credential('viewer')
        self.assertEqual(call('/api/agents/config?id=editable', key=key)[0], 403)
        self.assertEqual(call('/api/agents/config', {'id':'editable','changes':{}}, key=key)[0], 403)
        text = json.dumps(self.config())
        self.assertNotIn('secret-env', text)
        self.assertNotIn(key, text)
        self.assertNotIn('token', self.config()['values'])

    def test_reopen_edit_preserves_driver_parser_env_and_session(self):
        room = R.rooms.create('settings session', '', DATA, participants=['editable'])
        room.meta['editable_session'] = 'native-session'
        status, result = self.save({'label':'Changed', 'args':dict(self.spec['args'], base=['--profile','has spaces'])})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.config()['values']['label'], 'Changed')
        self.assertEqual(self.save({'label':'Changed again'})[0], 200)
        registry.load()
        self.assertEqual(registry.get('editable')['label'], 'Changed again')
        self.assertEqual(registry.get('editable')['env']['PRIVATE_VARIABLE'], 'secret-env')
        self.assertEqual(registry.get('editable')['parse']['text'], 'text')
        self.assertEqual(registry.get('editable')['driver'], 'cli')
        self.assertEqual(room.meta['editable_session'], 'native-session')

    def test_builtin_path_and_defaults_reach_actual_command(self):
        status, result = self.save({'bin':[sys.executable], 'default':['custom-model','high'],
                                    'args':{'base':['--profile','my profile']}}, 'claude')
        self.assertEqual(status, 200, result)
        self.assertEqual(agents.agent_executable('claude'), [sys.executable])
        self.assertEqual(agents.ROOM_DEFAULTS['claude'], ('custom-model','high'))
        room = R.rooms.create('builtin preference', '', DATA, participants=['claude'])
        self.assertEqual(room.effective_models('claude'), ('custom-model','high'))
        room.meta['models']['claude'] = {'model':'room-model','effort':'low'}
        self.assertEqual(room.effective_models('claude'), ('room-model','low'))
        command = agents.build_cmd('claude','discuss','native',[sys.executable],model='custom-model',effort='high')
        self.assertEqual(command[:3], [sys.executable,'--profile','my profile'])
        self.assertIn('custom-model', command)
        self.assertIn('native', command)

    def test_codex_launch_uses_configured_executable_and_arguments(self):
        self.assertEqual(self.save({'bin':[sys.executable], 'args':{'base':['--profile','changed']}},'codex')[0],200)
        with patch.object(agents,'codex_mcp_names',return_value=[]), patch.object(agents,'codex_plugins',return_value=[{'id':'plugins'}]), patch.object(agents,'plugin_on',return_value=True):
            command, _ = agents.codex_launch(None,'discuss')
        self.assertEqual(command[:3], [sys.executable,'--profile','changed'])
        self.assertIn('app-server',command)

    def test_missing_program_settings_remain_editable(self):
        missing = str(Path(DATA,'not installed.exe'))
        self.assertEqual(self.save({'bin':[missing]})[0],200)
        self.assertFalse(self.config()['enabled'])
        self.assertEqual(self.save({'bin':[sys.executable]})[0],200)
        self.assertTrue(self.config()['enabled'])

    def test_stale_revision_and_busy_room_cannot_overwrite(self):
        old = self.config()['revision']
        self.assertEqual(self.save({'label':'first'})[0],200)
        self.assertEqual(self.save({'label':'stale'},revision=old)[0],409)
        room = R.rooms.create('busy settings', '', DATA, participants=['editable'])
        room.state['running'].append('editable')
        try:
            self.assertEqual(self.save({'label':'busy'})[0],409)
        finally:
            room.state['running'].clear()
        self.assertEqual(self.config()['values']['label'],'first')

    def test_invalid_changes_do_not_write(self):
        for change in [{'driver':'external'}, {'id':'other'}, {'args':{'new':'not a list'}},
                       {'default':['fast','high']}, {'default_permissions':{'admin':True}},
                       {'desktop':{'path':'relative.exe'}}, {'model_efforts':{'reasoning':['bad effort']}}]:
            with self.subTest(change=change):
                self.assertEqual(self.save(change)[0],400)
                self.assertFalse(self.settings_path.exists())

    def test_write_failure_retains_previous_settings(self):
        self.assertEqual(self.save({'label':'kept'})[0],200)
        original = self.settings_path.read_bytes()
        with patch.object(agent_settings,'write_json',side_effect=PermissionError('locked')):
            self.assertEqual(self.save({'label':'lost'})[0],500)
        self.assertEqual(self.settings_path.read_bytes(),original)
        self.assertEqual(self.config()['values']['label'],'kept')

    def test_default_join_and_permissions_only_affect_new_memberships(self):
        old = R.rooms.create('old settings', '', DATA, participants=['editable'])
        before = copy.deepcopy(old.meta['permissions'])
        changes = {'default_join':True,'default_permissions':{'read':True,'post':True,'work':True,'trigger':False}}
        self.assertEqual(self.save(changes)[0],200)
        self.assertIn('editable', registry.default_participants())
        new = R.rooms.create('new settings', '', DATA, participants=['editable'])
        self.assertTrue(access.permissions(new,'editable')['work'])
        self.assertFalse(access.permissions(new,'editable')['trigger'])
        self.assertEqual(old.meta['permissions'],before)
        self.assertEqual(self.save({'default_join':False})[0],200)
        self.assertNotIn('editable',registry.default_participants())

    def test_membership_edit_and_leave_preserve_other_members(self):
        registry.save_agent({'id':'another','driver':'external','enabled':True})
        room = R.rooms.create('member preferences', '', DATA, participants=['editable','another'])
        room.set_members(['editable','another'], {'editable':{},'another':{'post':False,'work':True}})
        before = access.permissions(room,'another')
        path = '/api/r/'+support.quote(room.id)+'/invite'
        self.assertEqual(call(path,{'contact':'editable','joined':True,'permissions':{'post':False}},owner=True)[0],200)
        self.assertFalse(access.permissions(room,'editable')['post'])
        self.assertEqual(access.permissions(room,'another'),before)
        self.assertEqual(call(path,{'contact':'editable','joined':False,'permissions':{}},owner=True)[0],200)
        self.assertEqual(room.ais,['another'])
        self.assertEqual(access.permissions(room,'another'),before)

    def test_cli_change_invalidates_verification_but_rename_does_not(self):
        self.assertEqual(connections.check('editable')['state'],'ready')
        self.assertEqual(self.save({'label':'Renamed'})[0],200)
        self.assertEqual(connections.status('editable')['state'],'ready')
        self.assertEqual(self.save({'args':dict(self.spec['args'],base=['--different'])})[0],200)
        self.assertEqual(connections.status('editable')['state'],'unchecked')

    def test_mcp_requests_and_cli_validation_keep_separate_evidence(self):
        key=access.credential('editable')
        self.assertEqual(connections.check('editable')['state'],'ready')
        connections.seen('editable',key,'mcp','desktop')
        self.assertEqual(connections.status('editable')['state'],'ready')
        connections.check('editable')
        evidence=json.loads(Path(DATA,'connections.json').read_text(encoding='utf-8'))['editable']
        self.assertIn('digest',evidence);self.assertIn('signature',evidence)

    def test_mcp_install_success_and_failure_preserve_identity_and_old_key(self):
        host = {'id':'test-ui-host','label':'Test Desktop','kind':'json','detect':[sys.executable],
                'path':str(Path(DATA,'test-mcp.json'))}
        install.HOSTS.append(host)
        self.addCleanup(lambda:install.HOSTS.remove(host))
        self.assertEqual(self.save({'desktop':{'host':host['id'],'path':sys.executable,'mcp_path':host['path']}})[0],200)
        Path(host['path']).write_text('{"other":"keep"}',encoding='utf-8')
        oldkey = access.credential('editable')
        revision = self.config()['revision']
        with self.assertRaises(ValueError):
            agent_settings.install_mcp('editable',False,revision)
        before = Path(host['path']).read_bytes()
        def broken(value):
            Path(value['path']).write_text('partial write',encoding='utf-8')
            return False,'fake failure'
        with patch.dict(install.INSTALLERS,{'json':broken}):
            with self.assertRaisesRegex(ValueError,'fake failure'):
                agent_settings.install_mcp('editable',True,revision)
        self.assertEqual(Path(host['path']).read_bytes(),before)
        self.assertEqual(access.identify(oldkey),'editable')
        message = agent_settings.install_mcp('editable',True,revision)
        data = json.loads(Path(host['path']).read_text(encoding='utf-8'))
        self.assertEqual(data['other'],'keep')
        token = data['mcpServers']['ai-bridge']['headers']['Authorization'].split(' ',1)[1]
        self.assertEqual(access.identify(token),'editable')
        self.assertIsNone(access.identify(oldkey))
        self.assertNotIn(token,message)
        self.assertNotIn(token,Path(DATA,'contact_keys.json').read_text(encoding='utf-8'))
        self.assertEqual(registry.get('editable')['driver'],'cli')


@unittest.skipUnless(shutil.which('node'),'Node is needed for form regression')
class AgentSettingsForm(unittest.TestCase):
    def test_collect_unchanged_and_structured_edits(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const ctx=vm.createContext({esc:String,AG_DRV:{cli:'CLI'},PERM_LABELS:{read:'Read',post:'Post',trigger:'Trigger',board:'Board',files:'Files',work:'Work'}});
vm.runInContext(fs.readFileSync('static/agent-settings.js','utf8'),ctx);
const v={label:'Agent',bin:['my agent.exe'],args:{new:['-p','{prompt}']},prompt:'arg',context:'full',models:['reasoning'],efforts:['low','high'],model_efforts:{},default:['reasoning','high'],desktop:{host:'cursor',mcp_path:'C:/mcp.json',path:'C:/Cursor.exe'},default_join:null,default_permissions:{}};
const input=value=>({value}),boxes={agentLabel:input(v.label),agentDesktopPath:input(v.desktop.path),agentDesktopHost:input(v.desktop.host),agentMcpPath:input(v.desktop.mcp_path),agentDefaultModel:input('reasoning'),agentDefaultEffort:input('high'),agentDefaultJoin:input('auto'),agentPrompt:input('arg'),agentContext:input('full')};
const permissions=Object.keys(ctx.PERM_LABELS).map((key,i)=>({dataset:{agentDefaultPermission:key},checked:i<3}));
const modelRow={querySelector:()=>input('reasoning'),querySelectorAll:()=>['low','high'].map(level=>({dataset:{agentModelEffort:level}}))};
const lists=[['bin',v.bin],['arg-new',v.args.new]].map(([key,values])=>({dataset:{agentList:key},querySelectorAll:()=>values.map(input)}));
const root={querySelector:id=>boxes[id.slice(1)],querySelectorAll:selector=>selector==='[data-agent-list]'?lists:selector==='[data-agent-default-permission]'?permissions:selector==='.agent-model-row'?[modelRow]:[]};
const config={values:v,driver:'cli',connection:{},capabilities:{},hosts:[]};
assert.deepEqual(JSON.parse(JSON.stringify(ctx.collectAgentSettings(root,config))),{});
boxes.agentLabel.value='Renamed';boxes.agentDefaultEffort.value='low';permissions[5].checked=true;boxes.agentDefaultJoin.value='yes';
const changes=JSON.parse(JSON.stringify(ctx.collectAgentSettings(root,config)));
assert.equal(changes.label,'Renamed');assert.deepEqual(changes.default,['reasoning','low']);assert.equal(changes.default_join,true);assert.equal(changes.default_permissions.work,true);assert.equal(changes.driver,undefined);assert.equal(changes.model_efforts,undefined);
const html=ctx.agentSettingsMarkup(config);
assert.match(html,/data-agent-panel="cli"/);assert.match(html,/data-agent-panel="desktop"/);assert.match(html,/data-agent-panel="joining"/);assert.doesNotMatch(html,/<textarea|调用参数（JSON/);
'''
        result = subprocess.run(['node','-e',script],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main()
