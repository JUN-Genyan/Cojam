"""Native conversation handoff, endpoint isolation, and scoped desktop task leases."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

import test_contacts_access as support
from bridge import access, agent_settings, agents, drivers, execution, members, mcp, native_session, registry, room as R


class Execution(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.home = Path(self.temp.name)
        self.patch = patch.object(agents, 'CLAUDE_HOME', str(self.home))
        self.patch.start()
        self.old_broker = members.broker
        members.broker = members.Broker()
        preference = Path(support.DATA, 'agent_settings.json')
        if preference.exists():
            preference.unlink()
        registry.load()
        agent_settings.save('claude', {'bin':[sys.executable]}, agent_settings.view('claude')['revision'])
        self.key = access.credential('claude')
        self.rooms = []
        self.r, self.sid, self.log = self.new_room()

    def tearDown(self):
        for room in self.rooms:
            R.rooms.unload(room.id)
        members.broker = self.old_broker
        self.patch.stop()
        self.temp.cleanup()

    def new_room(self):
        r = R.rooms.create('native handoff', '', support.DATA, participants=['claude'])
        sid = str(uuid.uuid4())
        r.meta['claude_session'] = sid
        r.save_meta()
        log = self.home / 'projects' / 'project' / (sid+'.jsonl')
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(json.dumps({'sessionId':sid,'cwd':r.workdir()})+'\n'+json.dumps({'message':{'role':'assistant','model':'observed-model'}})+'\n',encoding='utf-8')
        self.rooms.append(r)
        return r,sid,log

    def prepare(self, room=None):
        room = room or self.r
        result = execution.prepare(room,'claude','desktop',execution.version(room,'claude'))
        return json.loads(result['instruction'].split('参数：',1)[1])

    def claim(self, room=None, sid=None, log=None):
        room, sid, log = room or self.r, sid or self.sid, log or self.log
        args = self.prepare(room)
        with log.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'message':{'role':'assistant','content':[{'type':'tool_use','name':'mcp__ai-bridge__cojam_standby','input':args}]}})+'\n')
        reply = members.handle('claude',self.key,'standby',dict(args,timeout=0))
        return dict(room=room.id,native_session=sid,worker=reply['worker'],binding_token=reply['binding_token'],timeout=0)

    def test_online_standby_does_not_implicitly_take_over_cli(self):
        members.broker.register('claude',self.key,{'worker':'legacy','transport':'standby'})
        with patch.object(drivers.agents,'call_ai',return_value=(True,'CLI',self.sid,[])) as cli:
            result = drivers.call('claude','hello',self.sid,room=self.r)
        self.assertTrue(result[0]);cli.assert_called_once()
        self.assertFalse(access.external_allowed(self.r,'claude'))

    def test_busy_pending_and_stale_revision_are_rejected(self):
        self.r.state['running'].append('claude')
        with self.assertRaisesRegex(ValueError,'正在运行'):
            self.prepare()
        self.r.state['running'].clear()
        self.prepare()
        with patch.object(drivers.agents,'call_ai') as cli:
            result = drivers.call('claude','hello',self.sid,room=self.r)
        self.assertFalse(result[0]);cli.assert_not_called()
        with self.assertRaisesRegex(ValueError,'已变更'):
            execution.cancel(self.r,'claude',0)

    def test_native_proof_must_be_in_the_exact_tool_call_and_session(self):
        args = self.prepare()
        with self.log.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'message':{'role':'user','text':args}})+'\n')
            stream.write(json.dumps({'message':{'role':'user','content':[{'type':'tool_use','name':'cojam_standby','input':args}]}})+'\n')
        with self.assertRaisesRegex(members.MemberError,'工具记录'):
            members.handle('claude',self.key,'standby',dict(args,timeout=0))
        self.assertEqual(execution.binding(self.r,'claude')['phase'],'preparing')
        with self.assertRaisesRegex(ValueError,'同一原生会话'):
            execution.claim(self.r,'claude','fake','different',args['binding_challenge'])

    def test_claim_is_scoped_and_secret_not_in_state(self):
        data = self.claim()
        self.assertEqual(self.r.meta['claude_session'],self.sid)
        self.assertEqual(execution.binding(self.r,'claude')['endpoint'],'desktop')
        self.assertFalse(access.external_allowed(self.r,'claude'))
        self.assertTrue(access.external_allowed(self.r,'claude',data['worker'],data['binding_token']))
        self.assertFalse(access.external_allowed(self.r,'claude','wrong',data['binding_token']))
        state = json.dumps(self.r.api_state())
        self.assertNotIn(data['binding_token'],state)
        self.assertNotIn('token_digest',state)
        with self.assertRaisesRegex(mcp.ToolError,'原 CLI'):
            mcp.call_room_tool(self.r.id,'claude','room_info',{})
        path='/api/external/messages?room='+support.quote(self.r.id)
        self.assertEqual(support.call(path,key=self.key)[0],403)
        headers={'X-Cojam-Worker':data['worker'],'X-Cojam-Execution':data['binding_token']}
        self.assertEqual(support.call(path,key=self.key,headers=headers)[0],200)

    def test_two_rooms_have_independent_workers_and_task_leases(self):
        first = self.claim()
        other, sid, log = self.new_room()
        second = self.claim(other,sid,log)
        self.assertNotEqual(first['worker'],second['worker'])
        received = {}
        def call():
            received['result'] = drivers.call('claude','native task',self.sid,room=self.r)
        thread = threading.Thread(target=call,daemon=True);thread.start()
        end=time.time()+4
        while not members.broker.tasks and time.time()<end:time.sleep(.01)
        self.assertIsNone(members.broker.receive('claude',self.key,second)['task'])
        task=members.broker.receive('claude',self.key,first)['task']
        reply={'worker':first['worker'],'task':task['id'],'lease':task['lease'],'ok':True,'text':'same native session'}
        with self.assertRaisesRegex(members.MemberError,'不能替换'):
            members.broker.complete('claude',self.key,dict(reply,session='wrong-native'))
        members.broker.complete('claude',self.key,reply)
        thread.join(5)
        self.assertTrue(received['result'][0]);self.assertEqual(received['result'][2],self.sid)
        self.assertEqual(task['execution']['location'],'desktop')

    def test_release_acknowledgement_returns_the_same_native_session_to_cli(self):
        data=self.claim()
        pending=execution.prepare(self.r,'claude','cli',execution.version(self.r,'claude'))
        self.assertEqual(pending['execution']['phase'],'releasing')
        self.assertFalse(access.external_allowed(self.r,'claude',data['worker'],data['binding_token']))
        self.assertEqual(members.handle('claude',self.key,'standby',data)['status'],'release')
        members.handle('claude',self.key,'release',data)
        self.assertEqual(execution.binding(self.r,'claude')['endpoint'],'cli')
        self.assertEqual(self.r.meta['claude_session'],self.sid)
        with patch.object(drivers.agents,'call_ai',return_value=(True,'CLI',self.sid,[])) as cli:
            drivers.call('claude','next',self.sid,room=self.r)
        self.assertEqual(cli.call_args.args[2],self.sid)
        with self.assertRaises(members.MemberError):
            members.handle('claude',self.key,'standby',data)

    def test_offline_desktop_never_falls_back_and_can_reconnect(self):
        data=self.claim()
        members.broker.workers[('claude',self.r.id)]['seen']=0
        with patch.object(drivers.agents,'call_ai') as cli:
            result=drivers.call('claude','next',self.sid,room=self.r)
        self.assertFalse(result[0]);cli.assert_not_called()
        self.assertFalse(access.external_allowed(self.r,'claude',data['worker'],data['binding_token']))
        members.handle('claude',self.key,'standby',data)
        self.assertTrue(members.broker.status('claude',self.r.id)['automatic'])

    def test_cancel_and_expiry_restore_owner_without_changing_session(self):
        self.prepare()
        execution.cancel(self.r,'claude',execution.version(self.r,'claude'))
        self.assertEqual(execution.binding(self.r,'claude')['endpoint'],'cli')
        self.prepare()
        self.r.meta['execution']['claude']['expires']=0
        self.assertEqual(execution.binding(self.r,'claude')['phase'],'ready')
        self.assertEqual(self.r.meta['claude_session'],self.sid)

    def test_cannot_duplicate_native_session_or_drop_model_overrides(self):
        self.r.meta['models']['claude']={'model':'sonnet','effort':'high'}
        with self.assertRaisesRegex(ValueError,'默认值'):
            self.prepare()
        self.r.meta['models']['claude']={'model':'','effort':''}
        other,_,_=self.new_room()
        other.meta['claude_session']=self.sid
        with self.assertRaisesRegex(ValueError,'重复接管'):
            self.prepare()

    def test_desktop_models_are_reported_read_only(self):
        self.claim()
        info=self.r.model_info('claude')
        self.assertFalse(info['model_supported']);self.assertFalse(info['effort_supported'])
        self.assertEqual(info['observed']['model'],'observed-model')
        self.assertEqual(info['apply_status'],'read_only')
        self.assertFalse(self.r.set_models('claude','other','')[0])

    def test_failed_binding_save_restores_the_previous_owner(self):
        with patch.object(self.r,'save_meta',side_effect=PermissionError('locked')):
            with self.assertRaises(OSError):self.prepare()
        self.assertTrue(execution.local(self.r,'claude'))

    def test_codex_native_adapter_checks_real_call_arguments_and_observes_effort(self):
        sid=str(uuid.uuid4());challenge='unique-native-challenge'
        folder=self.home/'sessions'/'2026'/'11'/'13';folder.mkdir(parents=True)
        log=folder/('rollout-'+sid+'.jsonl')
        rows=[{'type':'session_meta','payload':{'id':sid,'cwd':self.r.workdir()}},
              {'type':'turn_context','payload':{'model':'test-model','effort':'high'}},
              {'type':'response_item','payload':{'type':'function_call','name':'mcp__ai_bridge__cojam_standby','arguments':json.dumps({'binding_challenge':challenge})}}]
        log.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
        with patch.object(agents,'CODEX_HOME',str(self.home)):
            observed=native_session.prove('codex',sid,challenge,self.r.workdir())
        self.assertEqual(observed['model'],'test-model');self.assertEqual(observed['effort'],'high')

    def test_codex_orchestrated_mcp_call_is_native_proof(self):
        sid=str(uuid.uuid4());challenge='wrapped-challenge'
        folder=self.home/'sessions';folder.mkdir(parents=True)
        log=folder/('rollout-'+sid+'.jsonl')
        rows=[{'type':'session_meta','payload':{'id':sid,'cwd':self.r.workdir()}},
              {'type':'response_item','payload':{'type':'function_call','name':'exec','namespace':'functions',
               'arguments':"await tools.mcp__cojam__cojam_standby({binding_challenge: 'wrapped-challenge'});"}}]
        log.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
        with patch.object(agents,'CODEX_HOME',str(self.home)):
            native_session.prove('codex',sid,challenge,self.r.workdir())


if __name__=='__main__':unittest.main()
