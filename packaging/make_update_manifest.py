"""Generate the official update manifest from the built Windows installer."""
import argparse
import hashlib
import json
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--notes', default='修复问题并改进使用体验。')
    args = parser.parse_args()
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    installer = ROOT / 'dist' / ('Cojam-Setup-%s.exe' % version)
    data = installer.read_bytes()
    result = {'app':'Cojam', 'version':version,
              'url':'https://jun-genyan.github.io/Cojam/downloads/' + installer.name,
              'sha256':hashlib.sha256(data).hexdigest(), 'size':len(data), 'notes':args.notes[:1000]}
    output = ROOT / 'website' / 'update.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
