"""Run each unittest module in a fresh process, using isolated in-repo scratch data."""
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
scratch = ROOT / ".test-tmp"
scratch.mkdir(exist_ok=True)
env = dict(os.environ, TEMP=str(scratch), TMP=str(scratch), PYTHONWARNINGS="ignore::ResourceWarning",
           GIT_CEILING_DIRECTORIES=str(scratch))
failed = []
total = 0
for file in sorted((ROOT / "tests").glob("test_*.py")):
    try:
        result = subprocess.run([sys.executable, str(file)], cwd=ROOT, env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=120)
        output = result.stdout + result.stderr
        match = re.search(r"Ran (\d+) tests?", output)
        total += int(match[1]) if match else 0
        print(file.name, "PASS" if result.returncode == 0 else "FAIL", flush=True)
        if result.returncode:
            failed.append(file.name)
            print(output, flush=True)
    except subprocess.TimeoutExpired:
        failed.append(file.name)
        print(file.name, "TIMEOUT", flush=True)
print("%d tests; %d failing modules" % (total, len(failed)), flush=True)
sys.exit(bool(failed))
