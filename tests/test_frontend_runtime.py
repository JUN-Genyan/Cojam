"""The original failure was hidden by the browser's non-JSON error handling."""
from pathlib import Path
import shutil
import subprocess
import unittest


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
