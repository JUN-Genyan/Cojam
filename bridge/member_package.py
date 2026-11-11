"""Build a source-only member bundle, never including runtime data or keys."""
from pathlib import Path
import threading
import zipfile

LOCK = threading.Lock()


def build(root=None):
    root = Path(root or Path(__file__).resolve().parents[1])
    target = root / "static" / "member-connector.zip"
    with LOCK:
        sources = [(p, 'bridge/' + p.relative_to(root/'bridge').as_posix()) for p in (root/'bridge').glob('*.py')]
        sources += [(p, 'bridge/presets/' + p.name) for p in (root/'bridge'/'presets').glob('*.json')]
        sources += [(p, 'bridge/catalog/' + p.name) for p in (root/'bridge'/'catalog').glob('*.json')]
        sources += [(root/'LICENSE','LICENSE'), (root/'packaging'/'member-readme.md','README.md'),
                    (root/'packaging'/'start-member.command','start-member.command'),
                    (root/'packaging'/'start-member.bat','start-member.bat')]
        temp = target.with_suffix('.zip.tmp')
        try:
            with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as archive:
                for source, name in sources:
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = (0o100755 if name.endswith('.command') else 0o100644) << 16
                    archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
            temp.replace(target)
        finally:
            if temp.exists():
                temp.unlink()
    return target


if __name__ == '__main__':
    print(build())
