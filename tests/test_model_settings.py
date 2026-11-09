"""Exercise model switching and save lifecycle in the actual frontend code."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node is needed for frontend tests')
class ModelSettings(unittest.TestCase):
    def test_rescan_keeps_selected_agent_and_unsaved_form(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('static/connections.js','utf8');
const begin=source.indexOf("document.getElementById('connRescan').onclick = async () => {");
const end=source.indexOf('    const selected =',begin);
let busy=[],renders=0;
const nodes={connRescan:{},connProgramHelp:{}};
const w={id:'qodercli',saved:true,localChoice:'qodercli',form:{},controller:{signal:{}},busy:false};
const ctx=vm.createContext({w,document:{getElementById:id=>nodes[id]},agCat:{connections:{qodercli:{state:'ready'}}},
 setConnectionBusy:(flow,value)=>{flow.busy=value;busy.push(value)},captureConnectionForm:flow=>flow.form.command='unsaved-command',
 refreshContacts:async()=>{},wizardCurrent:()=>true,renderConnectionWizard:()=>renders++,wizardError:()=>{}});
vm.runInContext(source.slice(begin,end),ctx);
(async()=>{await nodes.connRescan.onclick();assert.equal(w.localChoice,'qodercli');assert.equal(w.id,'qodercli');
 assert.equal(w.saved,true);assert.equal(w.form.command,'unsaved-command');assert.equal(w.status.state,'ready');
 assert.deepEqual(busy,[true,false]);assert.equal(renders,1);assert.match(nodes.connProgramHelp.textContent,/检索完成/);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_model_switch_save_failure_retry_and_room_switch(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('static/model-settings.js','utf8');
const boxes=Object.fromEntries(['modelSettings','mModel','mModelChoice','mCustomModel','mEffort','mSave','mReset','mHint','mCurrent','mEffortHint'].map(id=>[id,{value:'',textContent:'',disabled:false,innerHTML:'',focus(){}}]));
let calls=[],pending=[],reloads=0;
const ctx=vm.createContext({document:{getElementById:id=>boxes[id]},RID:'room-a',S:{ais:{}},esc:String,
 api:()=>'/api/r/room-a/models',post:(url,body)=>{calls.push([url,body]);return new Promise(resolve=>pending.push(resolve))},loadState:async()=>{reloads++}});
vm.runInContext(source,ctx);
const info={model:'reasoning',effort:'high',default_model:'reasoning',default_effort:'high',models:['fast','reasoning'],efforts:['low','high'],
 model_supported:true,effort_supported:true,efforts_by_model:{fast:[],reasoning:['low','high']}};
boxes.mModel.value='reasoning';ctx.bindModelSettings('agent',info,false);
assert.equal(boxes.mEffort.value,'high');assert.equal(boxes.mSave.disabled,true);
boxes.mModelChoice.value='fast';boxes.mModelChoice.onchange();assert.equal(boxes.mModel.value,'fast');assert.equal(boxes.mCustomModel.hidden,true);assert.equal(boxes.mEffort.value,'');assert.equal(boxes.mEffort.disabled,true);
assert.equal(boxes.mSave.disabled,false);assert.match(boxes.mHint.textContent,/不适用/);
(async()=>{
 const failed=boxes.mSave.onclick();await boxes.mSave.onclick();assert.equal(calls.length,1);assert.equal(boxes.mModel.disabled,true);
 pending.shift()([false,{error:'后台暂不可用'}]);await failed;assert.match(boxes.mHint.textContent,/后台暂不可用/);assert.equal(boxes.mSave.disabled,false);
 const retry=boxes.mSave.onclick();assert.deepEqual(JSON.parse(JSON.stringify(calls[1][1])),{ai:'agent',model:'fast',effort:''});
 pending.shift()([true,{}]);await retry;assert.equal(reloads,1);assert.match(boxes.mHint.textContent,/已保存/);assert.equal(boxes.mSave.disabled,true);
 boxes.mReset.onclick();assert.equal(boxes.mModel.value,'');assert.equal(boxes.mEffort.disabled,false);assert.match(boxes.mEffort.innerHTML,/会话默认/);
 const late=boxes.mSave.onclick();ctx.RID='room-b';boxes.mHint={textContent:'新会话'};
 pending.shift()([true,{}]);await late;assert.equal(boxes.mHint.textContent,'新会话');assert.equal(reloads,1);
 const markup=ctx.modelSettingsMarkup({model_supported:false,effort_supported:false,note:'Agent 自己管理'},false);
 assert.doesNotMatch(markup,/id="mModel"|id="mSave"/);assert.match(markup,/Agent 自己管理/);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
