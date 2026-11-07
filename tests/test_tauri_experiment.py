"""Safety properties of the isolated desktop experiment."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tauri_experiment", ROOT / "experiments/tauri/run_experiment.py")
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class Isolation(unittest.TestCase):
    def test_environment_does_not_use_regular_data_or_agent_override(self):
        with patch.dict(os.environ, {"ROUNDTABLE_LOG_DIR": "existing-data", "ROUNDTABLE_AGENTS": "existing-agents"}):
            env = experiment.child_environment()
        self.assertEqual(env["ROUNDTABLE_PORT"], "18765")
        self.assertEqual(env["ROUNDTABLE_LOG_DIR"], str(experiment.DATA))
        self.assertEqual(env["ROUNDTABLE_DEFAULT_DIR"], str(experiment.DATA / "workspace"))
        self.assertNotIn("ROUNDTABLE_AGENTS", env)
        self.assertEqual(env["ROUNDTABLE_OWNER_LIFECYCLE"], "1")

    def test_missing_shell_does_not_start_backend(self):
        with patch("sys.argv", ["run_experiment.py", "--shell", "tauri"]), \
             patch.object(Path, "is_file", return_value=False), \
             patch.object(experiment.subprocess, "Popen") as spawn:
            self.assertEqual(experiment.main(), 2)
        spawn.assert_not_called()

    def test_occupied_port_does_not_start_backend(self):
        with patch("sys.argv", ["run_experiment.py", "--shell", "pywebview"]), \
             patch.object(experiment, "port_available", return_value=False), \
             patch.object(experiment.subprocess, "Popen") as spawn:
            self.assertEqual(experiment.main(), 2)
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
