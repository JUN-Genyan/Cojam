"""Asynchronous model discovery and version-aware invocation capabilities.

Only named fields leave the adapter. Credentials and raw diagnostic output never
enter the catalog. Reading room state never launches a process or does network IO.
"""
import concurrent.futures
import copy
import hashlib
import glob
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
import urllib.error

from . import registry
from .common import NO_WINDOW, kill_tree, read_json

TTL = 300
LIMIT = 1024 * 1024
LOCK = threading.RLock()
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='cojam-models')
CACHE, PENDING, LATEST = {}, set(), {}
ID = re.compile(r'^[A-Za-z0-9][\w.\-:/\[\]@+]{0,255}$')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
KINDS = {'none', 'effort', 'variant', 'toggle', 'budget'}


def read_config(path):
    try:
        with open(path, encoding='utf-8-sig') as handle:
            text = handle.read(LIMIT + 1)
        if len(text) > LIMIT:
            return {}
        text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*[\s\S]*?\*/', lambda match:match[1] or '', text)
        text = re.sub(r'("(?:\\.|[^"\\])*")|,\s*(?=[}\]])', lambda match:match[1] or '', text)
        return json.loads(text)
    except (OSError, ValueError):
        return {}


def reasoning(raw=None, kind='effort'):
    raw = raw if isinstance(raw, dict) else {}
    kind = str(raw.get('kind') or kind)
    if kind not in KINDS:
        kind = 'none'
    values = raw.get('options') or raw.get('effort') or raw.get('effort_levels') or []
    if not isinstance(values, list):
        values = []
    values = list(dict.fromkeys(str(v) for v in values if isinstance(v, (str, int)) and re.fullmatch(r'[\w.-]{1,40}', str(v))))[:32]
    result = {'kind': kind if values or kind in ('budget', 'toggle') else 'none', 'options': values}
    if kind == 'budget':
        for key, default in (('min', 0), ('max', 1000000)):
            value = raw.get(key, default)
            result[key] = max(0, min(1000000, value)) if type(value) is int else default
        result['max'] = max(result['min'], result['max'])
    return result


def model(model_id, label=None, controls=None):
    if not isinstance(model_id, str) or not ID.fullmatch(model_id):
        return None
    return {'id': model_id, 'label': str(label or model_id).replace('\n', ' ')[:160],
            'reasoning': controls or reasoning({}, 'none')}


def sanitize(raw):
    """Also validates untrusted remote/HTTP capability advertisements."""
    if not isinstance(raw, dict):
        return None
    rows = []
    for item in (raw.get('entries') or [])[:512] if isinstance(raw.get('entries'), list) else []:
        if not isinstance(item, dict):
            continue
        row = model(item.get('id'), item.get('label'), reasoning(item.get('reasoning'), 'none'))
        if row and row['id'] not in {v['id'] for v in rows}:
            rows.append(row)
    return {'entries': rows, 'model_supported': raw.get('model_supported') is True,
            'source': str(raw.get('source') or 'member')[:40],
            'status': raw.get('status') if raw.get('status') in ('ready', 'candidate', 'login_required', 'unsupported', 'error', 'missing', 'loading') else 'candidate',
            'version': str(raw.get('version') or '')[:80], 'installation': str(raw.get('installation') or '')[:500],
            'updated_at': time.time(), 'stale': raw.get('stale') is True,
            'detail': {'ready':'模型目录已读取，账户权限以实际回复为准。', 'login_required':'请先在原程序登录，再刷新模型。',
                       'unsupported':'此版本未提供可用的模型控制接口。'}.get(raw.get('status'), '模型能力由执行端提供。')}


def config_paths(spec, cwd):
    adapter = spec.get('adapter') or spec['id']
    home = os.path.expanduser('~')
    folders = {'opencode':['.config/opencode', '.opencode'], 'qodercli':['.qoder'],
               'cursor':['.cursor'], 'antigravity':['.gemini/antigravity-cli'],
               'gemini':['.gemini'], 'qwen':['.qwen'], 'crush':['.config/crush'],
               'devin_cli':['.config/devin', '.devin'], 'aider':['.aider']}.get(adapter, [])
    paths = []
    if adapter in ('claude','codex'):
        from . import agents
        paths += [agents.CLAUDE_SETTINGS] if adapter == 'claude' else [agents.CODEX_CONFIG, os.path.join(os.path.dirname(agents.CODEX_CONFIG), 'models_cache.json')]
    for folder in folders:
        for name in ('settings.json', 'config.json', 'opencode.json', 'opencode.jsonc', 'crush.json', 'cli-config.json', 'oauth_creds.json', 'credentials.json', '.env'):
            paths.append(os.path.join(home, folder, name))
    if adapter == 'qwen' and os.environ.get('QWEN_HOME'):
        paths.append(os.path.join(os.path.expanduser(os.environ['QWEN_HOME']), 'settings.json'))
    paths += [os.path.join(cwd, name) for name in ('opencode.json', 'opencode.jsonc', '.crush.json', 'crush.json', '.aider.model.settings.yml', '.env')]
    paths += [os.path.join(cwd, folder, 'settings.json') for folder in ('.gemini', '.qwen', '.qoder', '.devin')]
    if adapter == 'aider':
        paths += [os.path.join(home, '.aider.model.settings.yml')]
        exe = registry.exe_of(spec) or []
        for executable in exe:
            root = os.path.dirname(os.path.dirname(executable))
            patterns = [os.path.join(root, 'Lib', 'site-packages', 'aider', 'resources', 'model-settings.yml'),
                        os.path.join(root, 'lib', 'python*', 'site-packages', 'aider', 'resources', 'model-settings.yml')]
            for pattern in patterns:
                paths.extend(glob.glob(pattern)[:16])
    return paths


def _key(spec, cwd):
    exe = registry.exe_of(spec) if spec['driver'] in ('cli', 'claude', 'codex') else []
    stamps = []
    for path in list(exe or []) + config_paths(spec, cwd):
        try:
            stat = os.stat(path)
            stamps.append((path, stat.st_mtime_ns, stat.st_size))
        except OSError:
            pass
    # Config changes, including authentication, invalidate without recording secrets.
    for path in (os.path.expanduser('~/.local/share/opencode/auth.json'), os.path.expanduser('~/.qoder/credentials.json')):
        try:
            stat = os.stat(path)
            stamps.append((path, stat.st_mtime_ns, stat.st_size))
        except OSError:
            pass
    credentials = {key:value for key,value in os.environ.items() if key.endswith(('_API_KEY','_TOKEN','_BASE_URL'))}
    credential_digest = hashlib.sha256(json.dumps(credentials,sort_keys=True).encode()).hexdigest()
    digest = hashlib.sha256(json.dumps([spec, stamps, credential_digest], sort_keys=True, default=str).encode()).hexdigest()
    return spec['id'], os.path.realpath(cwd), digest


def probe(command, cwd, env=None):
    """Bound runtime, output, and process-tree lifetime. Do not run a model."""
    from .discovery import PROBE_LIMIT
    with PROBE_LIMIT:
        return _probe_command(command, cwd, env)


def _probe_command(command, cwd, env=None):
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   cwd=cwd, env={**os.environ, **(env or {}), 'PYTHONIOENCODING':'utf-8'},
                                   **(NO_WINDOW if os.name == 'nt' else {'start_new_session':True}))
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                kill_tree(process)
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
            raise ValueError('程序检测超时，请确认安装和登录状态后重试')
        out.seek(0); err.seek(0)
        stdout, stderr = out.read(LIMIT + 1), err.read(LIMIT + 1)
        if len(stdout) > LIMIT or len(stderr) > LIMIT:
            raise ValueError('模型目录输出过大，已停止读取')
        stdout = ANSI.sub('', stdout.decode('utf-8', 'replace'))
        stderr = ANSI.sub('', stderr.decode('utf-8', 'replace'))
        if process.returncode:
            if re.search(r'not logged|unauthorized|authentication required|login required|please.*log.?in|未登录|请.*登录', stdout + stderr, re.I):
                raise PermissionError('请先在原程序登录，再刷新模型')
            raise ValueError('当前程序不支持该目录命令，或尚未配置模型服务')
        return stdout if stdout.strip() else stderr


def parse_models(text, adapter='', prefix=''):
    """Parse named JSON schemas first; text fallback accepts only model-like rows."""
    try:
        data = json.loads(text)
    except ValueError:
        # OpenCode --verbose versions output provider/model followed by JSON metadata.
        rows, table_header = [], None
        # Old OpenCode emits an ID line followed by a pretty-printed JSON object.
        if adapter == 'opencode':
            for hit in re.finditer(r'(?m)^([A-Za-z0-9][\w./:@+-]+)\r?\n\s*(?=\{)', text):
                try:
                    metadata, _ = json.JSONDecoder().raw_decode(text[hit.end():])
                    if isinstance(metadata, dict):
                        metadata['id'] = hit[1]
                        rows += parse_models(json.dumps([metadata]), adapter)
                except ValueError:
                    pass
        known = {row['id'] for row in rows}
        for line in text.splitlines():
            line = re.sub(r'^\s*[-*•]\s+', '', line).strip()
            if '|' in line or '│' in line:
                cells = [value.strip(' `*') for value in re.split(r'[|│]', line.strip('|│'))]
                if any(value.lower() in ('id','model id','identifier','slug') for value in cells):
                    table_header = [value.lower() for value in cells]
                    continue
                if table_header and len(cells) == len(table_header):
                    index = next(i for i,value in enumerate(table_header) if value in ('id','model id','identifier','slug'))
                    row = model(cells[index])
                    if row:
                        efforts = next((cells[i] for i,value in enumerate(table_header) if 'effort' in value), '')
                        levels = re.findall(r'\b(?:none|minimal|low|medium|high|xhigh|max)\b', efforts)
                        row['reasoning'] = reasoning({'options':levels})
                        rows.append(row)
                        continue
            if ID.fullmatch(line) and ('/' in line or adapter in ('cursor', 'antigravity', 'qodercli', 'devin_cli')):
                row = model(line)
                if row and row['id'] not in known:
                    rows.append(row)
                    known.add(row['id'])
            elif adapter in ('cursor', 'antigravity', 'qodercli', 'devin_cli'):
                match = re.match(r'^\s*([A-Za-z0-9][\w./:@+-]+)(?:\t+| {2,})(.+)$', line)
                if match and not match[1].startswith(('Usage', 'Options', 'Commands', 'Model', 'Available')):
                    rows.append(model(match[1], match[2]))
        return [row for row in rows if row][:512]
    if isinstance(data, dict):
        data = data.get('models', data.get('data', data.get('families', data)))
    if isinstance(data, dict) and any(key in data for key in ('default_models','frontier_models','custom_models','groups')):
        return [row for key in ('default_models','frontier_models','custom_models','groups') if isinstance(data.get(key),(list,dict))
                for row in parse_models(json.dumps(data[key]),adapter,prefix)][:512]
    if isinstance(data, dict):
        data = [{'models':value} if isinstance(value,list) else dict(value, id=key) if isinstance(value, dict) else {'id':key} for key, value in data.items()]
    rows = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, str):
            item = {'id':item}
        if not isinstance(item, dict):
            continue
        if isinstance(item.get('models'), (dict,list)) and not any(item.get(key) for key in ('id','slug','model')):
            rows += parse_models(json.dumps(item['models']),adapter,prefix)
            continue
        if item.get('enabled') is False or item.get('disabled') is True:
            continue
        mid = item.get('id') or item.get('slug') or item.get('model')
        if not isinstance(mid, str):
            continue
        provider = item.get('providerID')
        if adapter == 'opencode' and isinstance(provider, str) and not mid.startswith(provider + '/'):
            mid = provider + '/' + str(item.get('modelID') or mid)
        capabilities = item.get('capabilities') or {}
        controls = item.get('reasoning') or (capabilities.get('reasoning') if isinstance(capabilities, dict) else {}) or {}
        if isinstance(controls, bool):
            controls = {}
        levels = item.get('supported_reasoning_levels') or item.get('reasoning_efforts') or []
        if levels:
            controls = {'kind':'effort', 'options':[v.get('effort') if isinstance(v, dict) else v for v in levels]}
        variants = item.get('variants')
        if variants:
            values = list(variants) if isinstance(variants, dict) else [v.get('id') if isinstance(v, dict) else v for v in variants]
            controls = {'kind':'variant', 'options':values}
        body = (item.get('generationConfig') or {}).get('extra_body') or {}
        if not controls and isinstance(body, dict):
            if type(body.get('enable_thinking')) is bool:
                controls = {'kind':'toggle'}
            elif type(body.get('thinking_budget')) is int:
                controls = {'kind':'budget'}
            elif isinstance(body.get('reasoning_effort'), str):
                controls = {'kind':'effort', 'options':[body['reasoning_effort']]}
        row = model(prefix + mid, item.get('name') or item.get('label'), reasoning(controls))
        if row:
            rows.append(row)
    return rows[:512]


def _configured(spec, cwd):
    rows = []
    for path in config_paths(spec, cwd):
        if path.endswith('.yml'):
            try:
                with open(path, encoding='utf-8') as handle:
                    yaml = handle.read(LIMIT)
                for block in re.split(r'(?m)^\s*-\s+name:\s*', yaml)[1:]:
                    name = block.splitlines()[0].strip(' \"\'')
                    settings = re.search(r'accepts_settings:[ \t]*(\[[^]]*\]|(?:\r?\n[ \t]+-[^\r\n]+)+)', block)
                    controls = {'kind':'budget'} if settings and 'thinking_tokens' in settings[1] else {'options':['low','medium','high']} if settings and 'reasoning_effort' in settings[1] else {}
                    rows.append(model(name, controls=reasoning(controls)))
            except OSError:
                pass
            continue
        data = read_config(path) or {}
        if not isinstance(data, dict):
            continue
        for provider, entries in (data.get('modelProviders') or {}).items() if isinstance(data.get('modelProviders'), dict) else []:
            provider_rows = parse_models(json.dumps(entries))
            if spec.get('adapter') == 'qwen':
                for row in provider_rows:
                    actual = row['id']
                    row.update(id=provider+'/'+actual, label=provider+' · '+row['label'], execution={'model':actual,'provider':provider})
                    protocol = (data.get('providerProtocol') or {}).get(provider, provider)
                    if protocol not in ('openai','openai-responses','qwen-oauth'):
                        row['reasoning'] = reasoning({}, 'none')
            rows += provider_rows
        providers = data.get('provider') or data.get('providers') or {}
        for provider, value in providers.items() if isinstance(providers, dict) else []:
            if isinstance(value, dict) and isinstance(value.get('models'), (dict, list)):
                rows += parse_models(json.dumps(value['models']), prefix=provider + '/')
        if isinstance(data.get('models'), (dict, list)):
            selected_models = data['models']
            if spec.get('adapter') == 'crush' and isinstance(selected_models, dict):
                for selected in selected_models.values():
                    if isinstance(selected, dict) and selected.get('model'):
                        rows.append(model(str(selected.get('provider') or '') + '/' + selected['model']))
            else:
                rows += parse_models(json.dumps(selected_models))
        aliases = (data.get('modelConfigs') or {}).get('aliases') or {}
        for name, config in aliases.items() if isinstance(aliases, dict) else []:
            settings = config.get('modelConfig') if isinstance(config, dict) else None
            if not isinstance(settings, dict) or not settings.get('model'):
                continue
            generation = settings.get('generateContentConfig') or {}
            thinking = generation.get('thinkingConfig') or {}
            controls = {'kind':'budget'} if 'thinkingBudget' in thinking else {}
            rows.append(model(settings['model'], name, reasoning(controls, 'none')))
        selected = data.get('model')
        if isinstance(selected, dict):
            selected = selected.get('name')
        if isinstance(selected, str) and ID.fullmatch(selected):
            rows.append(model(selected))
    return [row for row in rows if row]


def baseline(spec):
    from . import agents
    caps = agents.model_capabilities(spec['id'])
    entries = [model(mid, controls=reasoning({'options':caps['efforts_by_model'].get(mid, [])})) for mid in caps['models']]
    explicit = spec.get('reasoning') or {}
    for row in entries:
        if row and row['id'] in explicit:
            row['reasoning'] = reasoning(explicit[row['id']])
    return {'entries':[v for v in entries if v], 'model_supported':caps['model_supported'],
            'source':'cache' if spec['id'] == 'codex' else 'preset' if spec.get('builtin') else 'configuration',
            'status':'candidate' if caps['model_supported'] else 'unsupported', 'version':'', 'installation':'',
            'updated_at':0, 'stale':False, 'detail':caps['note'], 'flags':{}}


def _discover(spec, cwd, previous):
    result = baseline(spec)
    adapter = spec.get('adapter') or spec['id']
    if spec['driver'] == 'http':
        endpoint = spec.get('capabilities_endpoint')
        if not endpoint:
            return result
        from .connections import NoRedirect
        headers = dict(spec.get('headers') or {})
        if spec.get('token'):
            headers['Authorization'] = 'Bearer ' + spec['token']
        if not endpoint.startswith(('https://', 'http://')):
            raise ValueError('能力查询地址必须是 HTTP 或 HTTPS')
        deadline = time.monotonic() + 8
        try:
            with urllib.request.build_opener(NoRedirect).open(urllib.request.Request(endpoint, headers=headers), timeout=8) as response:
                chunks, length = [], 0
                while length <= LIMIT:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                    if sock:
                        sock.settimeout(remaining)
                    chunk = response.read1(min(65536, LIMIT + 1 - length))
                    if not chunk:
                        break
                    chunks.append(chunk); length += len(chunk)
                raw = b''.join(chunks)
        except urllib.error.HTTPError as error:
            if error.code in (401,403):
                raise PermissionError() from error
            raise
        if len(raw) > LIMIT:
            raise ValueError('能力目录过大')
        result = sanitize(json.loads(raw))
        if not result:
            raise ValueError('服务返回的能力目录格式无效')
        result['source'] = 'http'
        return result
    if spec['driver'] == 'external':
        return result
    exe = registry.exe_of(spec)
    if not exe:
        result.update(status='missing', model_supported=False, detail='未找到可调用程序；配对配置仍保留。')
        return result
    result['installation'] = exe[-1]
    result['version'] = probe(exe + (spec.get('discovery', {}).get('version_command') or ['--version']), cwd, spec.get('env')).strip().split('\n')[0][:80]
    if spec['driver'] in ('claude', 'codex'):
        result.update(updated_at=time.time(), status='ready' if spec['id'] == 'codex' and result['entries'] else 'candidate')
        return result
    subcommand = ['run'] if adapter in ('opencode', 'crush') else []
    help_text = probe(exe + (spec.get('discovery', {}).get('help_command') or subcommand + ['--help']), cwd, spec.get('env'))
    flags = {flag:bool(re.search(r'(?<![\w-])' + re.escape(flag) + r'(?![\w-])', help_text))
             for flag in ('--model','--effort','--reasoning-effort','--variant','--thinking-tokens','--models','--list-models','--auth-type')}
    result['flags'] = flags
    templates = [v for values in spec['args'].values() for v in values]
    known_adapter = adapter in ('opencode','qodercli','cursor','antigravity','devin_cli','aider','crush','gemini','qwen')
    result['model_supported'] = flags['--model'] if known_adapter else any('{model}' in v for v in templates)
    commands = {'opencode':['models','--verbose'], 'qodercli':['--list-models'], 'cursor':['--list-models'],
                'antigravity':['models'], 'devin_cli':['models','list','--format','json'],
                'aider':['--list-models',''], 'crush':['models']}
    rows = _configured(spec, cwd)
    command = spec.get('catalog_command') or commands.get(adapter)
    if adapter == 'opencode' and re.search(r'\bv?2\.', result['version']) and not spec.get('catalog_command'):
        command = ['api','GET','/api/model']
    if command:
        try:
            try:
                output = probe(exe + command, cwd, spec.get('env'))
            except ValueError:
                if adapter != 'opencode' or spec.get('catalog_command'):
                    raise
                output = probe(exe + ['models'], cwd, spec.get('env'))
            listed = parse_models(output, adapter)
            if listed:
                if adapter == 'aider':
                    configured = {row['id']:row for row in rows}
                    for row in listed:
                        if row['id'] in configured and row['reasoning']['kind'] == 'none':
                            row['reasoning'] = configured[row['id']]['reasoning']
                rows = listed + rows
                result['source'] = 'account' if adapter in ('qodercli','cursor','antigravity','devin_cli') else 'cli_candidates'
        except PermissionError:
            raise
        except ValueError:
            if not rows:
                result['detail'] = '此版本未提供可读取的目录；可填写模型 ID，账户权限以实际回复为准。'
    if rows:
        result['entries'] = list({row['id']:row for row in reversed(rows)}.values())[::-1]
        if result['source'] not in ('account', 'cli_candidates'):
            result['source'] = 'configuration'
    for row in result['entries']:
        declared = (spec.get('reasoning') or {}).get(row['id'])
        if declared and row['reasoning']['kind'] == 'none':
            row['reasoning'] = reasoning(declared)
    if adapter == 'antigravity' and flags['--effort']:
        # The CLI's text catalog explicitly encodes effort in both slug and
        # label. Derive each family's choices from listed entries, not from
        # a universal low/medium/high list or a model-name heuristic alone.
        families = {}
        for row in result['entries']:
            match = re.fullmatch(r'(.+)-(low|medium|high|xhigh|max)', row['id'])
            if match and row['label'].lower().endswith('('+match[2]+')'):
                families.setdefault(match[1], []).append(match[2])
        for row in result['entries']:
            match = re.fullmatch(r'(.+)-(low|medium|high|xhigh|max)', row['id'])
            if match and match[1] in families:
                row['reasoning'] = reasoning({'options':families[match[1]]})
    # Metadata is useful only when this installed driver can transmit it.
    for row in result['entries']:
        kind = row['reasoning']['kind']
        supported = (kind == 'none' or (kind == 'variant' and adapter == 'opencode' and flags['--model'])
                     or (kind == 'effort' and (flags['--effort'] or flags['--reasoning-effort']))
                     or (kind == 'budget' and flags['--thinking-tokens'])
                     or (adapter in ('gemini','qwen') and kind in ('effort','budget','toggle'))
                     or any('{' + key + '}' in value for key in ('thinking','thinking_budget') for value in templates))
        if not supported:
            row['reasoning'] = reasoning({}, 'none')
    if not result['model_supported']:
        result.update(status='unsupported', detail='当前安装版本未提供可用的模型参数，请在原程序设置。')
    else:
        result['status'] = 'ready' if rows else 'candidate'
    return result


def get(pid, cwd, refresh=False):
    spec = registry.get(pid)
    if not spec:
        raise ValueError('联系人不存在')
    if spec['driver'] == 'external':
        from .members import broker
        remote = broker.capabilities(pid)
        if remote:
            if refresh:
                broker.request_catalog_refresh(pid)
            return remote
        result = baseline(spec)
        result.update(status='unsupported', detail='执行端尚未上报模型能力；手动参与时请在原软件切换。')
        return result
    key = _key(spec, cwd)
    with LOCK:
        if len(LATEST) >= 256 and (pid, os.path.realpath(cwd)) not in LATEST:
            LATEST.pop(next(iter(LATEST)))
        LATEST[(pid, os.path.realpath(cwd))] = key
        old = CACHE.get(key)
        result = copy.deepcopy(old or baseline(spec))
        expired = not old or time.time() - old.get('updated_at', 0) >= TTL
        if (refresh or expired) and key not in PENDING and len(PENDING) < 32:
            PENDING.add(key)
            POOL.submit(_worker, key, copy.deepcopy(spec), cwd, old)
        if key in PENDING:
            result['refreshing'] = True
            if not old:
                result['status'] = 'loading'
        result['stale'] = bool(old and (expired or old.get('stale')))
        return result


def snapshot(pid, cwd):
    """Fast read for room polling. No directory scans, subprocesses or HTTP."""
    spec = registry.get(pid)
    if not spec:
        return {'entries':[], 'model_supported':False, 'status':'missing'}
    if spec['driver'] == 'external':
        from .members import broker
        remote = broker.capabilities(pid)
        if remote:
            return remote
    with LOCK:
        key = LATEST.get((pid, os.path.realpath(cwd)))
        result = copy.deepcopy(CACHE.get(key)) if key else None
        if result:
            result['refreshing'] = key in PENDING
            result['stale'] = result.get('stale', False) or time.time() - result['updated_at'] >= TTL
            return result
    return baseline(spec)


def legacy(catalog):
    return {'model_supported':catalog.get('model_supported', False),
            'effort_supported':any(row['reasoning']['kind'] in ('effort','variant') for row in catalog.get('entries', [])),
            'models':[row['id'] for row in catalog.get('entries', [])],
            'efforts_by_model':{row['id']:row['reasoning']['options'] if row['reasoning']['kind'] in ('effort','variant') else [] for row in catalog.get('entries', [])},
            'note':catalog.get('detail', ''), 'catalog':catalog}


def _worker(key, spec, cwd, previous):
    try:
        result = _discover(spec, cwd, previous)
        result['updated_at'] = time.time()
    except Exception as error:
        result = copy.deepcopy(previous or baseline(spec))
        result.update(status='login_required' if isinstance(error, PermissionError) else 'error', stale=bool(previous),
                      detail='请在原程序登录后刷新。' if isinstance(error, PermissionError) else '读取模型目录失败，请检查程序版本、网络及模型服务配置。',
                      updated_at=time.time())
    with LOCK:
        if len(CACHE) >= 128:
            CACHE.pop(next(iter(CACHE)))
        CACHE[key] = result
        PENDING.discard(key)


def entry(catalog, selected):
    return next((row for row in catalog.get('entries', []) if row['id'] == selected), None)


def validate(catalog, selected, effort='', controls=None):
    if selected and not ID.fullmatch(selected):
        return '模型 ID 格式无效'
    if selected and not catalog.get('model_supported'):
        return '此执行端或版本尚未提供模型控制能力'
    row = entry(catalog, selected)
    supported = row['reasoning'] if row else reasoning({}, 'none')
    if effort and (supported['kind'] not in ('effort','variant') or effort not in supported['options']):
        return '此模型不支持所选思考强度，请刷新目录后重新选择'
    if controls:
        if not isinstance(controls, dict) or controls.get('kind') != supported['kind']:
            return '思考参数类型与模型不匹配'
        value = controls.get('value')
        if effort and controls.get('kind') in ('effort','variant') and value != effort:
            return '旧思考强度与新参数冲突，请重新选择'
        if supported['kind'] == 'budget' and (type(value) is not int or not supported['min'] <= value <= supported['max']):
            return '思考预算超出模型允许范围'
        if supported['kind'] == 'toggle' and type(value) is not bool:
            return '思考开关必须是布尔值'
        if supported['kind'] in ('effort','variant') and value not in supported['options']:
            return '所选思考参数不可用'
        if supported['kind'] == 'none':
            return '此模型不提供可配置的思考参数'
    return ''
