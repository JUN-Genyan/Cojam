"""Check the official release and download its installer without running it."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import urllib.parse
import urllib.request

from .version import VERSION

MANIFEST = 'https://jun-genyan.github.io/Cojam/update.json'
DOWNLOAD_ROOT = 'https://jun-genyan.github.io/Cojam/downloads/'
MAX_INSTALLER = 100 * 1024 * 1024
LOCK = threading.RLock()
STATE = {'state': 'idle', 'current': VERSION}
RELEASE = None


def version(value):
    text = str(value or '')
    if not re.fullmatch(r'(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})', text):
        raise ValueError('版本信息格式不正确')
    return tuple(map(int, text.split('.')))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def _open(url):
    return urllib.request.build_opener(NoRedirect).open(url, timeout=20)


def parse_manifest(data):
    if not isinstance(data, dict) or data.get('app') != 'Cojam':
        raise ValueError('更新信息不完整')
    v = str(data.get('version') or '')
    version(v)
    url = str(data.get('url') or '')
    parsed = urllib.parse.urlsplit(url)
    if (not url.startswith(DOWNLOAD_ROOT) or parsed.query or parsed.fragment or
            not re.fullmatch(r'/Cojam/downloads/Cojam-Setup-[0-9]+\.[0-9]+\.[0-9]+\.exe', parsed.path)):
        raise ValueError('安装包地址无效')
    if not parsed.path.endswith('Cojam-Setup-' + v + '.exe'):
        raise ValueError('安装包版本不匹配')
    sha = str(data.get('sha256') or '').lower()
    size = data.get('size')
    if not re.fullmatch(r'[0-9a-f]{64}', sha) or type(size) is not int or not 0 < size <= MAX_INSTALLER:
        raise ValueError('安装包校验信息无效')
    return {'version': v, 'url': url, 'sha256': sha, 'size': size,
            'notes': str(data.get('notes') or '')[:1000]}


def check():
    global RELEASE
    if sys.platform == 'darwin':
        return {'state': 'error', 'current': VERSION, 'error': 'macOS 版请通过官网获取更新并手动替换应用。'}
    with LOCK:
        if STATE['state'] == 'downloading':
            return dict(STATE)
    try:
        with _open(MANIFEST) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('更新信息过大')
        release = parse_manifest(json.loads(raw))
        available = version(release['version']) > version(VERSION)
        with LOCK:
            RELEASE = release if available else None
            STATE.update(state='available' if available else 'current', current=VERSION,
                         latest=release['version'], notes=release['notes'], error='', path='', downloaded=0,
                         size=release['size'])
            return dict(STATE)
    except Exception:
        with LOCK:
            RELEASE = None
            STATE.update(state='error', current=VERSION, error='无法检查更新，请检查网络后重试', path='')
            return dict(STATE)


def status():
    with LOCK:
        return dict(STATE)


def start_download(directory=None):
    with LOCK:
        if STATE['state'] == 'downloading':
            return dict(STATE)
        if not RELEASE:
            raise ValueError('请先检查可用更新')
        release = dict(RELEASE)
        STATE.update(state='downloading', downloaded=0, path='', error='')
    threading.Thread(target=_download, args=(release, directory), daemon=True).start()
    return status()


def _download(release, directory=None):
    temp = None
    created = None
    try:
        folder = Path(directory) if directory else Path.home() / 'Downloads'
        folder.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.cojam-update-', suffix='.tmp', dir=folder)
        total = 0
        digest = hashlib.sha256()
        with os.fdopen(fd, 'wb') as out, _open(release['url']) as response:
            while True:
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > release['size'] or total > MAX_INSTALLER:
                    raise ValueError('安装包大小不符')
                digest.update(chunk)
                out.write(chunk)
                with LOCK:
                    STATE['downloaded'] = total
        if total != release['size'] or digest.hexdigest() != release['sha256']:
            raise ValueError('安装包校验失败')
        name = 'Cojam-Setup-' + release['version']
        for i in range(1000):
            path = folder / (name + (('-%d' % i) if i else '') + '.exe')
            try:
                # Never overwrite an existing file; keep only a verified installer.
                with open(path, 'xb') as dest, open(temp, 'rb') as source:
                    created = path
                    while chunk := source.read(128 * 1024):
                        dest.write(chunk)
                break
            except FileExistsError:
                continue
        else:
            raise ValueError('下载目录中已有太多同名文件')
        with LOCK:
            STATE.update(state='downloaded', path=str(path), error='')
    except Exception as error:
        if created:
            try:
                created.unlink(missing_ok=True)
            except OSError:
                pass
        with LOCK:
            STATE.update(state='error', error='下载失败或文件校验未通过，请重试', path='')
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)
