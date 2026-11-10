"""Bounded, installation-independent executable discovery. Never launches a shell."""
import glob
import hashlib
import os
import re
import shutil
import sys
import threading
import time

PROBE_LIMIT = threading.BoundedSemaphore(2)
_PATH_CACHE = {}
_PATH_LOCK = threading.Lock()


def clear_path_cache():
    with _PATH_LOCK:
        _PATH_CACHE.clear()


def expand(value, env=None, home=None):
    env = os.environ if env is None else env
    home = os.path.expanduser('~') if home is None else home
    value = str(value).replace('~', home, 1) if str(value).startswith('~') else str(value)
    folded = {key.lower():item for key, item in env.items()} if os.name == 'nt' else {}
    return re.sub(r'%([^%]+)%|\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)',
                  lambda m: env.get(next(g for g in m.groups() if g), folded.get(next(g for g in m.groups() if g).lower(), m.group(0))), value)


def search_paths(env=None, home=None, platform=None):
    if env is not None or home is not None or platform is not None:
        return _search_paths(env, home, platform)
    signature = tuple(os.environ.get(key, '') for key in ('PATH','USERPROFILE','HOME','APPDATA','LOCALAPPDATA','NPM_CONFIG_USERCONFIG','NPM_CONFIG_PREFIX','npm_config_prefix','PNPM_HOME','BUN_INSTALL','VOLTA_HOME','CONDA_PREFIX','PIPX_HOME','UV_TOOL_DIR'))
    with _PATH_LOCK:
        cached = _PATH_CACHE.get(signature)
        if cached and time.monotonic() - cached[0] < 2:
            return list(cached[1])
    result = _search_paths()
    with _PATH_LOCK:
        _PATH_CACHE.clear()
        _PATH_CACHE[signature] = (time.monotonic(), result)
    return list(result)


def _search_paths(env=None, home=None, platform=None):
    use_registry = env is None
    env = dict(os.environ) if env is None else env
    home = os.path.expanduser('~') if home is None else home
    platform = sys.platform if platform is None else platform
    windows = platform == 'win32'
    separator = ';' if windows else ':'
    paths = env.get('PATH', '').split(separator)
    if windows and use_registry:
        try:
            import winreg
            for root, key in ((winreg.HKEY_CURRENT_USER, 'Environment'),
                              (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment')):
                try:
                    with winreg.OpenKey(root, key) as handle:
                        index = 0
                        while True:
                            try:
                                variable, value, value_type = winreg.EnumValue(handle, index)
                            except OSError:
                                break
                            if variable.lower() != 'path' and isinstance(value, str) and variable.lower() not in {name.lower() for name in env}:
                                env[variable] = value
                            index += 1
                        paths.extend(expand(winreg.QueryValueEx(handle, 'Path')[0], env, home).split(';'))
                except OSError:
                    pass
        except ImportError:
            pass
    paths += [os.path.join(home, part) for part in (
        '.local/bin', '.cargo/bin', '.bun/bin', '.opencode/bin', '.cursor/bin', '.gemini/bin',
        '.qoder/bin', '.qoder/bin/qodercli', '.devin/bin', '.npm-global/bin',
        '.local/share/pnpm', '.volta/bin', '.asdf/shims', '.pyenv/shims', 'scoop/shims')]
    paths += [env.get(key, '') for key in ('PNPM_HOME', 'BUN_INSTALL', 'NPM_CONFIG_PREFIX', 'npm_config_prefix', 'VOLTA_HOME')]
    for key in ('BUN_INSTALL', 'VOLTA_HOME', 'NPM_CONFIG_PREFIX', 'npm_config_prefix'):
        if env.get(key):
            paths.append(os.path.join(env[key], 'bin'))
    # npm's prefix may be on a custom disk and need not be present in PATH.
    for config in (env.get('NPM_CONFIG_USERCONFIG'), os.path.join(home, '.npmrc')):
        if not config:
            continue
        try:
            with open(config, encoding='utf-8') as handle:
                text = handle.read(65536)
            for prefix in re.findall(r'^\s*prefix\s*=\s*(.+?)\s*$', text, re.M):
                prefix = expand(prefix.strip('"\''), env, home)
                paths += [prefix, os.path.join(prefix, 'bin')]
        except OSError:
            pass
    if windows:
        local, roaming = env.get('LOCALAPPDATA', ''), env.get('APPDATA', '')
        if roaming:
            paths.append(os.path.join(roaming, 'npm'))
        paths += [os.path.join(local, 'pnpm'),
                  os.path.join(local, 'Microsoft', 'WinGet', 'Links'), os.path.join(local, 'agy', 'bin'),
                  os.path.join(local, 'Programs', 'Python', 'Scripts')] if local else []
        patterns = ([os.path.join(local, 'Programs', 'Python', 'Python*', 'Scripts')] if local else []) + [os.path.join(home, '.local', 'pipx', 'venvs', '*', 'Scripts'),
                    os.path.join(home, 'pipx', 'venvs', '*', 'Scripts'),
                    os.path.join(home, '.local', 'share', 'uv', 'tools', '*', 'Scripts')]
    else:
        paths += ['/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin', '/snap/bin']
        patterns = [os.path.join(home, '.nvm', 'versions', 'node', '*', 'bin'),
                    os.path.join(home, '.local', 'share', 'pipx', 'venvs', '*', 'bin'),
                    os.path.join(home, '.local', 'pipx', 'venvs', '*', 'bin'),
                    os.path.join(home, '.local', 'share', 'uv', 'tools', '*', 'bin')]
    executable_folder = 'Scripts' if windows else 'bin'
    if env.get('CONDA_PREFIX'):
        paths += [env['CONDA_PREFIX'], os.path.join(env['CONDA_PREFIX'], executable_folder)]
    for prefix in (os.path.join(home, '.conda', 'envs'), os.path.join(home, 'miniconda3', 'envs'), os.path.join(home, 'anaconda3', 'envs')):
        patterns.append(os.path.join(prefix, '*', executable_folder))
    if env.get('PIPX_HOME'):
        patterns.append(os.path.join(env['PIPX_HOME'], 'venvs', '*', executable_folder))
    if env.get('UV_TOOL_DIR'):
        patterns.append(os.path.join(env['UV_TOOL_DIR'], '*', executable_folder))
    for pattern in patterns:
        paths.extend(sorted(glob.glob(pattern))[:64])
    return list(dict.fromkeys(os.path.normpath(expand(path.strip('"'), env, home)) for path in paths if path))


def _app_paths(name):
    if os.name != 'nt':
        return []
    import winreg
    found = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for suffix in ('', '.exe'):
            try:
                with winreg.OpenKey(root, 'Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\' + name + suffix) as handle:
                    found.append(str(winreg.QueryValue(handle, None)).strip('"'))
            except OSError:
                pass
    return found


def executables(name, paths=None):
    """Return all real installations in resolution order, not stale shims."""
    name = expand(str(name or '').strip())
    if not name:
        return []
    paths = search_paths() if paths is None else paths
    candidates = [name] if os.path.dirname(name) else []
    if not candidates:
        for directory in paths:
            hit = shutil.which(name, path=directory)
            if hit:
                candidates.append(hit)
        candidates += _app_paths(name)
    result, seen = [], set()
    for path in candidates:
        real = os.path.realpath(path)
        if not os.path.isfile(real) or (os.name != 'nt' and not os.access(real, os.X_OK) and not real.endswith(('.js','.cjs','.mjs'))):
            continue
        target = shim_target(real)
        if target is False:
            continue
        identity = os.path.normcase(real)
        if identity not in seen:
            seen.add(identity)
            result.append(real)
    return result


def shim_target(path):
    """Resolve common package-manager shims without evaluating their contents."""
    if os.path.splitext(path)[1].lower() not in ('.cmd', '.bat', '.ps1'):
        return None
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            text = handle.read(65536)
    except OSError:
        return False
    hits = re.finditer(r'(?:%dp0%[\\/]|\$basedir[\\/])([^"\r\n]+?\.(?:[cm]?js|exe))', text, re.I)
    targets = [os.path.join(os.path.dirname(path), m[1].replace('\\', os.sep).replace('/', os.sep)) for m in hits]
    if not targets:
        return None
    # npm 9+ 的模板先写 IF EXIST "%dp0%\node.exe" 探测 node 在不在 shim 目录里，
    # 真正的程序在最后一行（codex.CMD 实测）。逐个找，挑第一个真实存在的；
    # 一处都不存在才是真坏掉的 shim。
    for target in targets:
        real = os.path.realpath(target)
        if os.path.isfile(real):
            return real
    return False


def installations(spec):
    paths, found, seen = search_paths(), [], set()
    descriptor = spec.get('discovery') or {}
    names = list(spec.get('bin') or []) + list(descriptor.get('commands') or [])
    names += list((descriptor.get('paths') or {}).get(sys.platform, []))
    if os.name == 'nt' and descriptor.get('app_names'):
        from . import registry
        wanted = {str(value).lower()+'.lnk' for value in descriptor['app_names']}
        for base in registry._start_menu_dirs():
            for directory, subdirs, files in os.walk(base):
                if os.path.relpath(directory, base).count(os.sep) >= 4:
                    subdirs[:] = []
                for filename in files:
                    if filename.lower() not in wanted:
                        continue
                    shortcut = os.path.join(directory, filename)
                    try:
                        stat = os.stat(shortcut)
                        target = registry._shortcut_target(shortcut, stat.st_mtime_ns, stat.st_size)
                    except OSError:
                        continue
                    if not target or not os.path.isfile(target):
                        continue
                    root = os.path.dirname(target)
                    for command in descriptor.get('bundled_commands') or []:
                        for relative in ('bin', os.path.join('resources','app','bin'), os.path.join('resources','bin')):
                            names.append(os.path.join(root, relative, command + '.exe'))
    # Package metadata supplies executable entrypoints even when the manager
    # forgot to expose a shim in PATH (custom npm/pnpm prefixes included).
    import json
    for package in descriptor.get('packages') or []:
        if not isinstance(package, str) or '..' in package or not re.fullmatch(r'@?[\w.-]+(?:/[\w.-]+)?', package):
            continue
        for base in paths:
            for root in (os.path.join(base, 'node_modules', package), os.path.join(os.path.dirname(base), 'lib', 'node_modules', package)):
                try:
                    with open(os.path.join(root, 'package.json'), encoding='utf-8') as handle:
                        metadata = json.load(handle)
                    bins = metadata.get('bin') or {}
                    entries = bins.values() if isinstance(bins, dict) else [bins] if isinstance(bins, str) else []
                    for relative in entries:
                        candidate = os.path.realpath(os.path.join(root, str(relative)))
                        if os.path.commonpath([candidate, os.path.realpath(root)]) == os.path.realpath(root) and os.path.isfile(candidate):
                            names.append(candidate)
                except (OSError, ValueError):
                    pass
    for name in names:
        for path in executables(name, paths):
            # npm's .cmd and extensionless shims are one installation.
            identity = os.path.normcase(shim_target(path) or os.path.realpath(path))
            if os.name == 'nt' and os.path.splitext(path)[1].lower() in ('.cmd', '.ps1'):
                identity = os.path.splitext(identity)[0]
            if identity in seen:
                continue
            seen.add(identity)
            found.append({'id': hashlib.sha256(identity.encode()).hexdigest()[:20], 'path': path})
    return found
