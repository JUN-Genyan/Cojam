from pathlib import Path
import shutil
import subprocess
import unittest


class UpdateUi(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the update UI test')
    def test_desktop_requests_automatic_install_after_confirmation(self):
        root=Path(__file__).resolve().parents[1]
        code=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const elements={};function node(id){return elements[id]||=({innerHTML:'',disabled:false,classList:{toggle(){}},insertAdjacentHTML(){}})}
let request=null;const ctx=vm.createContext({document:{getElementById:node},esc:String,showModal(){},hideModal(){},confirm:()=>true,
 setTimeout:()=>1,clearTimeout(){},fetch:async()=>({ok:true,json:async()=>({state:'available',current:'2.6.4',latest:'2.6.5',can_install:true})}),
 post:async(url,body)=>{request={url,body};return [true,{state:'downloading'}]}});
vm.runInContext(fs.readFileSync('static/update.js','utf8'),ctx);
(async()=>{node('updateApp').onclick();await new Promise(r=>global.setTimeout(r,10));
 assert.match(node('updateContent').innerHTML,/下载并更新/);await node('updateDownload').onclick();
 assert.equal(request.url,'/api/update/download');assert.equal(request.body.auto_install,true)})().catch(e=>{console.error(e);process.exitCode=1});
'''
        r=subprocess.run(['node','-e',code],cwd=root,capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the update UI test')
    def test_check_and_download_use_internal_api(self):
        root=Path(__file__).resolve().parents[1]
        code=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const elements={};function node(id){return elements[id]||=( {innerHTML:'',disabled:false,classList:{toggle(){}},insertAdjacentHTML(){}} )}
const calls=[],ctx=vm.createContext({document:{getElementById:node},esc:s=>String(s),showModal(){},hideModal(){},
 setTimeout:()=>1,clearTimeout(){},fetch:async url=>{calls.push(url);return {ok:true,json:async()=>({state:'available',current:'2.6.1',latest:'2.6.2',size:3})}},
 post:async(url)=>{calls.push(url);return [true,{state:'downloading',current:'2.6.1',size:3,downloaded:0}]}});
vm.runInContext(fs.readFileSync('static/update.js','utf8'),ctx);
(async()=>{node('updateApp').onclick();await new Promise(resolve=>global.setTimeout(resolve,10));
assert.match(node('updateContent').innerHTML,/2.6.2/);await node('updateDownload').onclick();
assert.ok(calls.includes('/api/update/check'));assert.ok(calls.includes('/api/update/download'));})();
'''
        r=subprocess.run(['node','-e',code],cwd=root,capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)


if __name__ == '__main__': unittest.main()
