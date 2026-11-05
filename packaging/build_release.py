"""Create explicit release artifacts; never include data or machine credentials."""
from pathlib import Path
import os
import shutil
import subprocess
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
DIST = ROOT/'dist'
DIST.mkdir(exist_ok=True)
if os.environ.get('COJAM_REQUIRE_SIGNATURE') == '1':
    if os.name != 'nt':
        raise RuntimeError('必须在 Windows 上验证正式发行包的签名')
    shell = shutil.which('pwsh') or 'powershell.exe'
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command',
        "$ErrorActionPreference='Stop'; $s=Get-AuthenticodeSignature -LiteralPath 'dist/Cojam.exe'; if ($s.Status -ne 'Valid' -or -not $s.TimeStamperCertificate) { exit 1 }"], cwd=ROOT)
    if result.returncode:
        raise RuntimeError('正式发行包需要有效的受信任签名及时间戳')
connector = DIST/('Cojam-Member-%s.zip' % VERSION)
shutil.copyfile(ROOT/'static'/'member-connector.zip', connector)
target = DIST/('Cojam-%s-win64.zip' % VERSION)
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
    for source, name in [('dist/Cojam.exe','Cojam.exe'),('LICENSE','LICENSE'),('packaging/README.txt','README.txt'),
                         ('packaging/connect-member.bat','连接其他会议室.bat'),('docs/CONTACTS.md','CONTACTS.md'),
                         ('docs/MEMBERS.md','MEMBERS.md'),('docs/PROVIDERS.md','PROVIDERS.md'),
                         ('examples/contact_client.py','contact_client.py')]:
        archive.write(ROOT/source, name)
with zipfile.ZipFile(target) as archive:
    assert archive.testzip() is None
print(target)
print(connector)
desktop_exe = DIST/'CojamDesktop.exe'
if desktop_exe.is_file():
    core_exe = DIST/'Cojam.exe'
    if not core_exe.is_file():
        raise RuntimeError('桌面版需要先构建 dist/Cojam.exe')
    desktop = DIST/('Cojam-Desktop-%s-win64.zip' % VERSION)
    with zipfile.ZipFile(desktop, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(desktop_exe, 'Cojam.exe')
        archive.write(core_exe, 'CojamCore.exe')
        archive.write(ROOT/'packaging'/'desktop-readme.txt', 'README.txt')
        archive.write(ROOT/'LICENSE', 'LICENSE')
        archive.write(ROOT/'packaging'/'connect-member.bat', '连接其他会议室.bat')
        for source, name in [('docs/CONTACTS.md','CONTACTS.md'), ('docs/MEMBERS.md','MEMBERS.md'),
                             ('docs/PROVIDERS.md','PROVIDERS.md'), ('examples/contact_client.py','contact_client.py')]:
            archive.write(ROOT/source, name)
    with zipfile.ZipFile(desktop) as archive:
        assert archive.testzip() is None
    print(desktop)
