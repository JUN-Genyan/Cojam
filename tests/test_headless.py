# -*- coding: utf-8 -*-
"""Windowed builds have no console: keep stdio usable and package as GUI."""
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_roundtable():
    spec = importlib.util.spec_from_file_location("roundtable_headless_test", ROOT / "roundtable.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Stdio(unittest.TestCase):
    def test_keeps_existing_streams(self):
        module = load_roundtable()
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
            module.ensure_stdio()
            self.assertIs(sys.stdout, out)
            self.assertIs(sys.stderr, err)

    def test_without_console_writes_to_log(self):
        module = load_roundtable()
        original_out, original_err = sys.stdout, sys.stderr
        stream = None
        with tempfile.TemporaryDirectory() as tmp:
            try:
                with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None), \
                        patch.object(module, "_attach_console", return_value=False), \
                        patch.dict(os.environ, {"ROUNDTABLE_LOG_DIR": tmp}):
                    module.ensure_stdio()
                    stream = sys.stdout
                    self.assertIsNotNone(stream)
                    self.assertIs(stream, sys.stderr)
                    stream.write("windowed line\n")
                    stream.flush()
                log = Path(tmp) / "cojam.log"
                self.assertTrue(log.exists())
                self.assertIn("windowed line", log.read_text(encoding="utf-8"))
            finally:
                if stream is not None:
                    stream.close()
                sys.stdout, sys.stderr = original_out, original_err


class Packaging(unittest.TestCase):
    def test_windows_build_is_windowed(self):
        build = (ROOT / "build_exe.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("--windowed", build)
        for spec in ("Cojam.spec", "AIBridge.spec"):
            text = (ROOT / spec).read_text(encoding="utf-8")
            self.assertRegex(text, r"console\s*=\s*False", spec)

    def test_source_launcher_is_headless(self):
        starter = (ROOT / "start.bat").read_text(encoding="utf-8-sig").lower()
        self.assertIn("pythonw", starter)
        self.assertNotIn("pause", starter)


if __name__ == "__main__":
    unittest.main()
