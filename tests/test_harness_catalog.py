"""Portable installation discovery and real adapter execution, without AI calls."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import io
from types import SimpleNamespace
from unittest.mock import patch

DATA = tempfile.mkdtemp(prefix='harness_catalog_')
os.environ['ROUNDTABLE_LOG_DIR'] = DATA
os.environ['ROUNDTABLE_NO_PRESETS'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import agents, discovery, drivers, model_catalog as catalogs, registry


class Discovery(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='安装 space_'))

    def command(self, directory, name='sample-harness'):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (name + ('.cmd' if os.name == 'nt' else ''))
        path.write_text('@exit /b 0\n' if os.name == 'nt' else '#!/bin/sh\nexit 0\n', encoding='utf-8')
        path.chmod(0o755)
        return path

    def test_custom_npm_prefix_and_platform_home_paths(self):
        prefix = self.root / 'custom disk' / 'npm'
        (self.root / '.npmrc').write_text('prefix="%s"\n' % prefix.as_posix(), encoding='utf-8')
        for platform in ('win32', 'darwin', 'linux'):
            with self.subTest(platform=platform):
                paths = discovery.search_paths({'PATH':'', 'LOCALAPPDATA':str(self.root / 'local'), 'APPDATA':str(self.root / 'roaming')}, str(self.root), platform)
                self.assertIn(str(prefix), paths)
                self.assertIn(str(self.root / '.local' / 'bin'), paths)

    def test_multiple_installs_runtime_install_and_uninstall(self):
        first = self.command(self.root / 'first')
        second = self.command(self.root / 'second')
        paths = [str(first.parent), str(second.parent)]
        self.assertEqual(discovery.executables('sample-harness', paths), [str(first.resolve()), str(second.resolve())])
        first.unlink()
        self.assertEqual(discovery.executables('sample-harness', paths), [str(second.resolve())])
        first = self.command(first.parent)
        self.assertEqual(discovery.executables('sample-harness', paths)[0], str(first.resolve()))

    def test_stale_package_shim_and_symlink(self):
        shim = self.root / 'sample.cmd'
        shim.write_text('"%dp0%\\node_modules\\tool\\cli.js" %*', encoding='utf-8')
        self.assertIs(discovery.shim_target(str(shim)), False)
        target = self.root / 'node_modules' / 'tool' / 'cli.js'
        target.parent.mkdir(parents=True)
        target.write_text('// installed', encoding='utf-8')
        self.assertEqual(discovery.shim_target(str(shim)), str(target.resolve()))
        if os.name != 'nt':
            executable = self.command(self.root / 'bin')
            link = self.root / 'link'
            link.symlink_to(executable)
            self.assertEqual(discovery.executables(str(link)), [str(executable.resolve())])
            executable.unlink()
            self.assertEqual(discovery.executables(str(link)), [])

    def test_native_drivers_use_the_selected_installation(self):
        executable = self.command(self.root / 'native', 'codex')
        with patch.object(registry,'_selected_installations',return_value={'codex':str(executable)}),patch.object(agents,'resolve_cmd',return_value=['selected-codex']) as resolve:
            self.assertEqual(agents._command('codex'),['selected-codex'])
            resolve.assert_called_once_with(str(executable))


class Catalogs(unittest.TestCase):
    def setUp(self):
        self.cwd = tempfile.mkdtemp(prefix='catalog-project_')
        self.spec = registry._norm({'id':'sample', 'driver':'cli', 'bin':[sys.executable],
                                   'args':{'new':['{prompt}'], 'model':['--model','{model}']}})
        self.lookup = patch.object(registry, 'get', side_effect=lambda pid:self.spec if pid == 'sample' else None)
        self.lookup.start(); self.addCleanup(self.lookup.stop)
        with catalogs.LOCK:
            catalogs.CACHE.clear(); catalogs.LATEST.clear()

    def test_versioned_fixture_shapes_and_reasoning_types(self):
        fixture = Path(__file__).parent / 'fixtures' / 'harness-catalogs.json'
        cases = json.loads(fixture.read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(adapter=case['adapter'], version=case['version']):
                rows = catalogs.parse_models(case['stdout'], case['adapter'])
                self.assertEqual([row['id'] for row in rows], case['models'])
                if 'kind' in case:
                    self.assertEqual(rows[0]['reasoning']['kind'], case['kind'])

    def test_program_help_on_stderr_is_usable(self):
        output=catalogs.probe([sys.executable,'-c','import sys; print("--model --effort",file=sys.stderr)'],self.cwd)
        self.assertIn('--model',output)

    def test_configuration_jsonc_and_no_secret_leaks(self):
        config = Path(self.cwd) / 'opencode.jsonc'
        config.write_text('{// comment\n"provider":{"private":{"apiKey":"SECRET","models":{"m":{"name":"Named", "variants":{"deep":{}}}}}},}', encoding='utf-8')
        rows = catalogs._configured(self.spec, self.cwd)
        self.assertEqual(rows[0]['id'], 'private/m')
        self.assertEqual(rows[0]['reasoning']['options'], ['deep'])
        safe = catalogs.sanitize({'entries':rows, 'token':'SECRET', 'headers':{'Authorization':'SECRET'}, 'detail':'SECRET','model_supported':True})
        self.assertNotIn('SECRET', json.dumps(safe))

    def test_http_capability_query_is_opt_in_and_discards_secrets(self):
        spec=registry._norm({'id':'sample','driver':'http','endpoint':'http://service/bridge','token':'private-token'})
        with patch('urllib.request.build_opener') as opener:
            catalogs._discover(spec,self.cwd,None)
            opener.assert_not_called()
            spec['capabilities_endpoint']='http://service/capabilities'
            response=io.BytesIO(json.dumps({'model_supported':True,'entries':[{'id':'service-model'}],'token':'do-not-publish'}).encode())
            opener.return_value.open.return_value=response
            result=catalogs._discover(spec,self.cwd,None)
            request=opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'),'Bearer private-token')
            self.assertEqual(result['entries'][0]['id'],'service-model')
            self.assertNotIn('do-not-publish',json.dumps(result))

    def test_installation_rescan_returns_without_blocking_registry_reads(self):
        gate=threading.Event()
        with patch.object(registry,'load',side_effect=lambda:gate.wait(3)):
            start=time.monotonic();registry.request_scan()
            self.assertLess(time.monotonic()-start,.5)
            self.assertTrue(registry.scanning())
            self.assertIs(registry.get('sample'),self.spec)
            gate.set()
            until=time.monotonic()+3
            while registry.scanning() and time.monotonic()<until:time.sleep(.01)
            self.assertFalse(registry.scanning())

    def test_nonblocking_refresh_retains_cache_on_failure(self):
        gate = threading.Event()
        def discover(*args):
            gate.wait(5)
            return dict(catalogs.baseline(self.spec), entries=[catalogs.model('new-model')], status='ready')
        with patch.object(catalogs, '_discover', side_effect=discover):
            start = time.monotonic()
            result = catalogs.get('sample', self.cwd)
            self.assertLess(time.monotonic() - start, 1)
            self.assertTrue(result['refreshing'])
            gate.set()
            self.wait_ready()
        with patch.object(catalogs, '_discover', side_effect=PermissionError('private raw error')):
            catalogs.get('sample', self.cwd, refresh=True)
            self.wait_ready()
        cached = catalogs.snapshot('sample', self.cwd)
        self.assertEqual(cached['entries'][0]['id'], 'new-model')
        self.assertEqual(cached['status'], 'login_required')
        self.assertTrue(cached['stale'])
        self.assertNotIn('private raw error', json.dumps(cached))

    def test_background_discovery_never_exceeds_two_workers(self):
        gate, lock = threading.Event(), threading.Lock()
        active, maximum = [0], [0]
        def discover(spec,*args):
            with lock:
                active[0]+=1; maximum[0]=max(maximum[0],active[0])
            gate.wait(3)
            with lock:active[0]-=1
            return catalogs.baseline(spec)
        with patch.object(catalogs,'_discover',side_effect=discover):
            for index in range(4):
                cwd=Path(self.cwd)/str(index);cwd.mkdir()
                catalogs.get('sample',str(cwd))
            time.sleep(.05);gate.set();self.wait_ready()
        self.assertLessEqual(maximum[0],2)

    def test_qwen_same_model_name_routes_to_the_selected_provider(self):
        self.spec['adapter']='qwen'
        path=Path(self.cwd)/'.qwen'/'settings.json';path.parent.mkdir()
        path.write_text(json.dumps({'modelProviders':{'openai':[{'id':'same'}],'anthropic':[{'id':'same'}]}}),encoding='utf-8')
        rows=catalogs._configured(self.spec,self.cwd)
        self.assertEqual({row['id'] for row in rows},{'openai/same','anthropic/same'})
        ctx={'model':'anthropic/same','effort':''}
        updated=drivers._prepare_model(self.spec,ctx,{}, {'version':'fixture','flags':{'--model':True,'--auth-type':True},'entries':rows})
        self.assertEqual(ctx['model'],'same')
        self.assertEqual(updated['args']['base'],['--auth-type','anthropic'])

    def wait_ready(self):
        until = time.monotonic() + 6
        while time.monotonic() < until:
            with catalogs.LOCK:
                pending = bool(catalogs.PENDING)
            if not pending:
                return
            time.sleep(.01)
        self.fail('model discovery did not finish')

    def test_custom_cli_execution_new_and_resume_flags(self):
        for name in ('qodercli','cursor','antigravity','gemini','qwen','aider','crush','devin_cli','opencode'):
            raw = json.loads((Path(__file__).resolve().parents[1] / 'bridge' / 'presets' / (name + '.json')).read_text(encoding='utf-8'))
            with patch.object(registry, 'find_executable', return_value=sys.executable):
                spec = registry._norm(raw)
            for session in (None, 'session-one'):
                args, _ = drivers._compose(spec, {'model':'provider/chosen','effort':'','session':session or ''}, session, [], 'discuss', 'question')
                self.assertIn('--model', args)
                self.assertEqual(args[args.index('--model')+1], 'provider/chosen')
                if name in ('opencode','crush'):
                    self.assertLess(args.index('run'), args.index('--model'))

    def test_old_and_new_opencode_variants(self):
        spec = registry._norm({'id':'sample','adapter':'opencode','driver':'cli','bin':[sys.executable], 'args':{'model':['--model','{model}'],'effort':['--variant','{effort}'],'new':['run','{prompt}']},'options_position':'after_command'})
        for separate in (True, False):
            ctx = {'model':'provider/model','effort':'deep'}
            updated = drivers._prepare_model(spec, ctx, {}, {'version':'fixture', 'flags':{'--model':True,'--variant':separate}})
            args, _ = drivers._compose(updated,ctx,None,[],'discuss','test')
            self.assertIn('deep' if separate else 'provider/model#deep', args)

    def test_actual_cli_process_receives_selected_model_and_variant(self):
        script = Path(self.cwd) / 'echo_cli.py'
        record = Path(self.cwd) / 'arguments.json'
        script.write_text("import sys,json\nfrom pathlib import Path\nPath(%r).write_text(json.dumps(sys.argv[1:]),encoding='utf-8')\nprint(json.dumps({'type':'text','part':{'text':'ok'},'sessionID':'session-one'}))\n" % str(record), encoding='utf-8')
        spec = registry._norm({'id':'sample','adapter':'opencode','driver':'cli','bin':[sys.executable],
                               'options_position':'after_command', 'args':{'new':['run','{prompt}'],'resume':['run','--session','{session}','{prompt}'],
                                   'model':['--model','{model}'],'effort':['--variant','{effort}']},
                               'parse':{'type':'ndjson','text':'part.text','session':'sessionID','filter':{'type':'text'}}})
        room = SimpleNamespace(id='one',keys={},meta={'models':{'sample':{'model':'p/m','effort':'deep'}}},procs={},
                               title=lambda:'Test',goal=lambda:'',workdir=lambda:self.cwd,effective_models=lambda pid:('p/m','deep'),push_live=lambda *args:None)
        catalog = {'version':'2.2.5','flags':{'--model':True,'--variant':False},'entries':[catalogs.model('p/m',controls=catalogs.reasoning({'kind':'variant','options':['deep']}))]}
        with patch.object(registry,'exe_of',return_value=[sys.executable,str(script)]), patch.object(catalogs,'snapshot',return_value=catalog):
            for session in (None,'session-one'):
                result = drivers.call_cli('sample',spec,'hello',session,[],'discuss',room)
                self.assertTrue(result[0],result)
                args=json.loads(record.read_text(encoding='utf-8'))
                self.assertEqual(args[0],'run')
                self.assertIn('p/m#deep',args)
                self.assertNotIn('--variant',args)
                if session:self.assertIn('--session',args)

    def test_temporary_gemini_config_is_per_invocation_and_preserves_system_policy(self):
        script = Path(self.cwd) / 'settings_cli.py'
        record = Path(self.cwd) / 'seen.json'
        original = Path(self.cwd) / 'system.json'
        original.write_text(json.dumps({'security':{'strict':True}}),encoding='utf-8')
        script.write_text("import os,json\nfrom pathlib import Path\np=Path(os.environ['GEMINI_CLI_SYSTEM_SETTINGS_PATH'])\nPath(%r).write_text(json.dumps({'path':str(p),'settings':json.loads(p.read_text(encoding='utf-8'))}),encoding='utf-8')\nprint(json.dumps({'response':'ok'}))\n" % str(record),encoding='utf-8')
        spec=registry._norm({'id':'sample','adapter':'gemini','driver':'cli','bin':[sys.executable],
                             'args':{'model':['--model','{model}'],'new':['-p','{prompt}']},'parse':{'type':'json','text':'response'}})
        room=SimpleNamespace(id='one',keys={},meta={'models':{'sample':{'model':'configured','reasoning':{'kind':'budget','value':1024}}}},procs={},
                             title=lambda:'Test',goal=lambda:'',workdir=lambda:self.cwd,effective_models=lambda pid:('configured',''),push_live=lambda *args:None)
        catalog={'version':'fixture','flags':{'--model':True},'entries':[]}
        with patch.object(registry,'exe_of',return_value=[sys.executable,str(script)]),patch.object(catalogs,'snapshot',return_value=catalog),patch.dict(os.environ,{'GEMINI_CLI_SYSTEM_SETTINGS_PATH':str(original)}):
            self.assertTrue(drivers.call_cli('sample',spec,'hello',None,[],'discuss',room)[0])
        seen=json.loads(record.read_text(encoding='utf-8'))
        self.assertTrue(seen['settings']['security']['strict'])
        self.assertEqual(seen['settings']['modelConfigs']['overrides'][0]['modelConfig']['generateContentConfig']['thinkingConfig']['thinkingBudget'],1024)
        self.assertFalse(Path(seen['path']).exists())
        self.assertEqual(json.loads(original.read_text(encoding='utf-8')),{'security':{'strict':True}})

    def test_crush_rooms_have_independent_state_and_follow_account_changes(self):
        source=Path(self.cwd)/'original-state';source.mkdir()
        auth=source/'auth.json';auth.write_text('{"credential":"first"}',encoding='utf-8')
        script=Path(self.cwd)/'crush_cli.py';record=Path(self.cwd)/'state-seen.json'
        script.write_text("import os,json\nfrom pathlib import Path\np=Path(os.environ['CRUSH_GLOBAL_DATA'])\n(p/'chosen.json').write_text('{}')\nPath(%r).write_text(json.dumps({'root':str(p),'auth':json.loads((p/'auth.json').read_text(encoding='utf-8'))}),encoding='utf-8')\nprint('OK')\n" % str(record),encoding='utf-8')
        spec=registry._norm({'id':'sample','adapter':'crush','driver':'cli','bin':[sys.executable],
                             'options_position':'after_command','args':{'model':['--model','{model}'],'new':['run','{prompt}']}})
        roots=[]
        with patch.object(registry,'exe_of',return_value=[sys.executable,str(script)]),patch.object(catalogs,'snapshot',return_value={'version':'fixture','flags':{'--model':True}}),patch.dict(os.environ,{'CRUSH_GLOBAL_DATA':str(source)}):
            for name in ('one','two'):
                room=SimpleNamespace(id=name,keys={},meta={'models':{}},procs={},title=lambda:'',goal=lambda:'',workdir=lambda:self.cwd,effective_models=lambda pid:('chosen',''),push_live=lambda *args:None)
                self.assertTrue(drivers.call_cli('sample',spec,'hello',None,[],'discuss',room)[0])
                seen=json.loads(record.read_text(encoding='utf-8'));roots.append(seen['root'])
                self.assertEqual(seen['auth']['credential'],'first' if name=='one' else 'second')
                auth.write_text('{"credential":"second"}',encoding='utf-8')
        self.assertNotEqual(roots[0],roots[1])
        self.assertFalse((source/'chosen.json').exists())
        self.assertEqual(json.loads(auth.read_text(encoding='utf-8')),{'credential':'second'})

    def test_reasoning_validation_and_unknown_models(self):
        for controls, value in (({'kind':'budget','min':0,'max':4096}, 100), ({'kind':'toggle'}, False), ({'kind':'variant','options':['deep']}, 'deep')):
            catalog = {'model_supported':True,'entries':[catalogs.model('m',controls=catalogs.reasoning(controls))]}
            self.assertEqual(catalogs.validate(catalog,'m',controls={'kind':controls['kind'],'value':value}), '')
            self.assertTrue(catalogs.validate(catalog,'m',controls={'kind':'unknown','value':'oops'}))
        self.assertTrue(catalogs.validate({'model_supported':False},'m'))


if __name__ == '__main__':
    unittest.main()
