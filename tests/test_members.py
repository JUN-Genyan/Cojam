"""Shared-room scheduling and the portable connector, without real AI calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import test_contacts_access as support
from bridge import access, drivers, members, registry, room as R


def wait_until(predicate, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('condition did not become true')


class Members(unittest.TestCase):
    def setUp(self):
        self.original = R.ai_caller
        R.ai_caller = drivers.call
        members.broker = members.Broker()
        for pid in ['member-a','member-b']:
            registry.save_agent({'id':pid,'driver':'external','enabled':True})
        self.keys = {p:access.credential(p) for p in ['member-a','member-b']}
        self.rooms = []

    def tearDown(self):
        for r in self.rooms:
            r.stop_ais()
            wait_until(lambda: not r.busy_for())
        R.ai_caller = self.original

    def room(self, ids=None):
        r = R.rooms.create('共享会议', '', support.DATA, participants=ids or ['member-a','member-b'])
        self.rooms.append(r)
        return r

    def op(self, pid, action, body=None):
        return support.call('/api/external/member/' + action, body or {}, self.keys[pid])

    def connect(self, pid, worker='worker', **extra):
        status, data = self.op(pid, 'connect', dict(worker=worker,device='Mac test device',workdir='/members/project',**extra))
        self.assertEqual(status, 200, data)

    def receive(self, pid, worker='worker'):
        found = []
        def poll():
            status, data = self.op(pid, 'receive', {'worker':worker,'timeout':0})
            self.assertEqual(status, 200, data)
            if data.get('task'):
                found.append(data['task'])
            return bool(found)
        wait_until(poll)
        return found[0]

    def reply(self, pid, task, text, worker='worker', **extra):
        return self.op(pid, 'reply', dict(worker=worker,task=task['id'],lease=task['lease'],ok=True,text=text,session='member-session',**extra))

    def test_remote_members_can_mention_each_other_in_the_same_scheduler(self):
        r = self.room()
        self.connect('member-a')
        self.connect('member-b')
        r.handle_message('你','@member-a 请讨论')
        a = self.receive('member-a')
        self.assertEqual(a['room'], r.id)
        self.assertEqual(self.reply('member-a',a,'@member-b 请检查')[0], 200)
        b = self.receive('member-b')
        self.assertIn('请检查', b['prompt'])
        self.assertEqual(self.reply('member-b',b,'@member-a 已检查')[0], 200)
        a2 = self.receive('member-a')
        self.assertEqual(a2['session'], 'member-session')
        self.reply('member-a',a2,'@用户 讨论完成')
        wait_until(lambda: not r.busy_for())
        self.assertEqual([m['speaker'] for m in r.messages if m['speaker'] != '系统'],['你','member-a','member-b','member-a'])

    def test_duplicate_results_do_not_duplicate_messages(self):
        r = self.room(['member-a'])
        self.connect('member-a')
        r.handle_message('你','@member-a hi')
        task = self.receive('member-a')
        self.reply('member-a',task,'one reply')
        self.assertTrue(self.reply('member-a',task,'one reply')[1]['duplicate'])
        self.assertEqual(self.reply('member-a',task,'different reply')[0], 409)
        wait_until(lambda:not r.busy_for())
        self.assertEqual(len([m for m in r.messages if m['text']=='one reply']),1)

    def test_no_identity_spoofing_or_foreign_lease(self):
        r = self.room()
        self.connect('member-a')
        self.connect('member-b')
        r.handle_message('你','@member-a hi')
        task = self.receive('member-a')
        self.assertEqual(self.reply('member-b',task,'spoof')[0],403)
        bad = dict(task, lease='wrong')
        self.assertEqual(self.reply('member-a',bad,'spoof')[0],403)
        self.reply('member-a',task,'valid')
        wait_until(lambda:not r.busy_for())

    def test_stopped_tasks_reject_late_replies_and_mentions(self):
        r = self.room()
        self.connect('member-a')
        self.connect('member-b')
        r.handle_message('你','@member-a go')
        task = self.receive('member-a')
        r.stop_ais(['member-a'])
        self.assertEqual(self.reply('member-a',task,'@member-b late')[0],410)
        wait_until(lambda:not r.busy_for())
        self.assertFalse(any(m['text']=='@member-b late' for m in r.messages))
        self.assertIsNone(self.op('member-b','receive',{'worker':'worker','timeout':0})[1]['task'])

    def test_readonly_members_never_receive_tasks(self):
        r = self.room()
        r.set_members(['member-a','member-b'],{'member-a':{'post':False}})
        self.connect('member-a')
        r.handle_message('你','@member-a hi')
        self.assertIsNone(self.op('member-a','receive',{'worker':'worker','timeout':0})[1]['task'])
        self.assertFalse(r.busy_for())

    def test_offline_and_revoked_members_do_not_stall_the_room(self):
        r = self.room(['member-a'])
        r.handle_message('你','@member-a hi')
        wait_until(lambda:not r.busy_for())
        self.assertTrue(any('尚未开启自动响应' in m['text'] for m in r.messages))
        self.connect('member-a')
        r.handle_message('你','@member-a hi again')
        task=self.receive('member-a')
        access.credential('member-a',revoke=True)
        self.assertEqual(self.reply('member-a',task,'late')[0],401)
        wait_until(lambda:not r.busy_for())

    def test_remote_work_uses_remote_folder_and_no_host_snapshot(self):
        r=self.room(['member-a'])
        r.set_members(['member-a'],{'member-a':{'work':True}})
        r.set_mode('work')
        self.connect('member-a',allow_work=True)
        with patch.object(R.ws,'snapshot_tree',side_effect=AssertionError('host snapshot must not run')):
            r.handle_message('你','@member-a go')
            task=self.receive('member-a')
            self.assertEqual(task['mode'],'work')
            self.assertIn('/members/project',task['prompt'])
            self.reply('member-a',task,'done')
            wait_until(lambda:not r.busy_for())

    def test_local_policy_can_restrict_remote_work(self):
        r=self.room(['member-a'])
        r.set_members(['member-a'],{'member-a':{'work':True}})
        r.set_mode('work')
        self.connect('member-a',allow_work=False)
        r.handle_message('你','@member-a go')
        task=self.receive('member-a')
        self.assertEqual(task['mode'],'discuss')
        self.reply('member-a',task,'done')
        wait_until(lambda:not r.busy_for())

    def test_one_receiver_per_identity_and_disconnect(self):
        self.connect('member-a')
        self.assertEqual(self.op('member-a','connect',{'worker':'other'})[0],409)
        self.assertTrue(members.broker.status('member-a')['automatic'])
        self.assertEqual(self.op('member-a','disconnect',{'worker':'worker'})[0],200)
        self.assertFalse(members.broker.status('member-a')['automatic'])

    def test_progress_does_not_trigger_other_members(self):
        r=self.room()
        self.connect('member-a')
        self.connect('member-b')
        r.handle_message('你','@member-a go')
        task=self.receive('member-a')
        body={'worker':'worker','task':task['id'],'lease':task['lease'],'text':'@member-b 中途进度'}
        self.assertEqual(self.op('member-a','progress',body)[0],200)
        self.assertIsNone(self.op('member-b','receive',{'worker':'worker','timeout':0})[1]['task'])
        self.reply('member-a',task,'done')
        wait_until(lambda:not r.busy_for())

    def test_lost_receiver_cancels_without_replaying(self):
        r=self.room(['member-a'])
        self.connect('member-a')
        r.handle_message('你','@member-a go')
        task=self.receive('member-a')
        members.broker.workers['member-a']['seen']=time.time()-members.WORKER_TTL-1
        wait_until(lambda:not r.busy_for())
        self.assertIsNone(self.op('member-a','receive',{'worker':'worker','timeout':0})[1]['task'])
        self.assertEqual(self.reply('member-a',task,'too late')[0],410)

    def test_remote_member_cannot_interpret_host_file_commands(self):
        r=self.room(['member-a'])
        r.set_members(['member-a'],{'member-a':{'files':True}})
        self.connect('member-a')
        r.handle_message('你','@member-a go')
        task=self.receive('member-a')
        with patch.object(r,'apply_send_cmds',side_effect=AssertionError('must not read host files')):
            self.reply('member-a',task,'some remote file command')
            wait_until(lambda:not r.busy_for())
        self.assertTrue(any(m['text']=='some remote file command' for m in r.messages))

    def test_mcp_can_receive_and_reply_with_the_same_member_protocol(self):
        r=self.room(['member-a'])
        def rpc(name,args):
            return support.call('/mcp',{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}},self.keys['member-a'])[1]['result']
        empty=rpc('receive_task',{'worker':'mcp-worker','timeout':0})
        self.assertFalse(empty['isError'])
        r.handle_message('你','@member-a MCP task')
        wait_until(lambda:bool(members.broker.tasks))
        response=rpc('receive_task',{'worker':'mcp-worker','timeout':1})
        task=json.loads(response['content'][0]['text'])['task']
        self.assertEqual(task['member'],'member-a')
        result=rpc('reply_task',{'worker':'mcp-worker','task':task['id'],'lease':task['lease'],'ok':True,'text':'MCP reply'})
        self.assertFalse(result['isError'])
        wait_until(lambda:not r.busy_for())
        self.assertTrue(any(m['text']=='MCP reply' for m in r.messages))

    def test_board_is_scoped_to_task_and_permission(self):
        r=self.room(['member-a'])
        self.connect('member-a')
        r.handle_message('你','@member-a go')
        task=self.receive('member-a')
        data={'worker':'worker','task':task['id'],'lease':task['lease'],'op':'add','kind':'todo','text':'not allowed'}
        self.assertEqual(self.op('member-a','board',data)[0],403)
        self.reply('member-a',task,'done')
        wait_until(lambda:not r.busy_for())
        r.set_members(['member-a'],{'member-a':{'board':True}})
        r.handle_message('你','@member-a board')
        task=self.receive('member-a')
        data.update(task=task['id'],lease=task['lease'],text='shared board')
        self.assertEqual(self.op('member-a','board',data)[0],200)
        self.assertEqual(r.meta['board'][-1]['by'],'member-a')
        self.reply('member-a',task,'done')
        wait_until(lambda:not r.busy_for())

    def test_connector_subprocess_executes_in_its_own_folder_and_keeps_session(self):
        r=self.room(['member-a'])
        root=Path(tempfile.mkdtemp(prefix='member-node-'))
        state=root/'state'
        configs=state/'runtime'/'agents.d'
        configs.mkdir(parents=True)
        wd=root/'project'
        wd.mkdir()
        fake=root/'fake.py'
        fake.write_text("import json,sys,os,time\nfrom pathlib import Path\np=Path('member-marker.txt');p.write_text(p.read_text()+'x' if p.exists() else 'x')\nPath('prompt.txt').write_text(sys.argv[1],encoding='utf-8')\nPath('credential.txt').write_text(os.environ.get('AI_BRIDGE_TOKEN',''))\nif 'stop-long' in sys.argv[1] and not Path('slept.once').exists():\n Path('slept.once').write_text('started');time.sleep(20)\nprint(json.dumps({'reply':'@用户 worker replied','sid':'local-session'}))\n",encoding='utf-8')
        (configs/'fake.json').write_text(json.dumps({'id':'fake','driver':'cli','bin':[sys.executable],'args':{'base':[str(fake),'{prompt}']},'parse':{'type':'json','text':'reply','session':'sid'}}),encoding='utf-8')
        env=dict(os.environ,AI_BRIDGE_TOKEN=self.keys['member-a'],ROUNDTABLE_NO_PRESETS='1')
        log=open(root/'connector.log','wb')
        process=subprocess.Popen([sys.executable,'-m','bridge.member','--server',support.BASE,'--agent','fake','--workdir',str(wd),'--state-dir',str(state)],env=env,stdout=log,stderr=log)
        try:
            wait_until(lambda:members.broker.status('member-a')['automatic'],timeout=12)
            for n in range(2):
                r.handle_message('你','@member-a task %s' % n)
                wait_until(lambda:not r.busy_for(),timeout=15)
            self.assertEqual((wd/'member-marker.txt').read_text(),'xx')
            self.assertFalse((Path(support.DATA)/'member-marker.txt').exists())
            self.assertEqual((wd/'credential.txt').read_text(),'')
            self.assertNotIn(self.keys['member-a'],(wd/'prompt.txt').read_text(encoding='utf-8'))
            self.assertEqual(len([m for m in r.messages if m['text']=='@用户 worker replied']),2)
            self.assertEqual(r.meta['member-a_session'],'local-session')
            self.assertNotIn(self.keys['member-a'],(root/'connector.log').read_text(encoding='utf-8',errors='replace'))
            self.assertNotIn('prompt',Path(support.DATA,'member_tasks.json').read_text())
            # Cancellation must terminate the local call and free the connector
            # for another task, without recording the stopped task's reply.
            r.handle_message('你','@member-a stop-long')
            wait_until(lambda:(wd/'slept.once').exists(),timeout=10)
            r.stop_ais(['member-a'])
            wait_until(lambda:not r.busy_for())
            r.handle_message('你','@member-a after stop')
            wait_until(lambda:not r.busy_for(),timeout=12)
            self.assertEqual(len([m for m in r.messages if m['text']=='@用户 worker replied']),3)
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill()
            log.close()


if __name__=='__main__':
    unittest.main()
