"""Regression checks for the meeting toolbar and the formerly unbound details button."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node is required for UI handlers')
class MeetingNavigation(unittest.TestCase):
    def test_toolbar_details_close_and_focus(self):
        script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const html=fs.readFileSync('index.html','utf8');
const source=html.slice(html.indexOf('const meetingToolsButton='),html.indexOf('function fmtK('));
const nodes={},events={};let focused='',details=0;
function node(id){return nodes[id]||=(function(){const classes=new Set(),attrs={};return {
 id,style:{},disabled:false,inert:true,
 classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c),toggle(c,on){if(on===undefined)on=!classes.has(c);on?classes.add(c):classes.delete(c);return on}},
 setAttribute:(k,v)=>attrs[k]=v,getAttribute:k=>attrs[k],focus(){focused=id},
 contains:el=>el===nodes[id],querySelector:()=>node('bList'),querySelectorAll:()=>[],
 getBoundingClientRect:()=>({bottom:60,right:380}),addEventListener:(k,f)=>events[id+':'+k]=f
}})()}
const ctx=vm.createContext({bar:node('bar'),modal:node('modal'),innerHeight:844,innerWidth:390,
 document:{getElementById:node,activeElement:node('menu'),body:{classList:{remove(){}}},addEventListener:(k,f)=>events[k]=f},
 addEventListener:(k,f)=>events['window:'+k]=f,drawMeetingDetails:()=>details++,openAi(){}});
vm.runInContext(source,ctx);
node('menu').onclick();assert.equal(node('menu').getAttribute('aria-expanded'),'true');assert.equal(focused,'bList');
node('bSide').onclick();assert.equal(details,1);assert.equal(node('side').inert,false);assert.ok(node('side').classList.contains('on'));assert.equal(focused,'sideX');
assert.equal(node('bar').classList.contains('show'),false);
node('sideX').onclick();assert.equal(node('side').inert,true);assert.equal(focused,'menu');
node('menu').onclick();events.pointerdown({target:node('outside')});assert.equal(node('bar').classList.contains('show'),false);
node('menu').onclick();events.keydown({key:'Escape',preventDefault(){}});assert.equal(node('menu').getAttribute('aria-expanded'),'false');
node('bSide').onclick();node('sideBg').onclick();assert.equal(node('side').inert,true);
node('bSide').onclick();events.keydown({key:'Escape',preventDefault(){}});assert.equal(node('side').inert,true);
"""
        result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
