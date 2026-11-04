"""A transient Windows replacement failure must not break saving a conversation."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import common


class AtomicJson(unittest.TestCase):
    def test_transient_reader_contention_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'meeting.json')
            common.write_json(path, {'members': []})
            replace = os.replace
            calls = []

            def briefly_locked(source, target):
                calls.append(target)
                if len(calls) < 3:
                    raise PermissionError('temporarily open for reading')
                return replace(source, target)

            with patch.object(common.os, 'replace', side_effect=briefly_locked):
                common.write_json(path, {'members': ['alice']})
            self.assertEqual(common.read_json(path), {'members':['alice']})
            self.assertEqual(os.listdir(directory), ['meeting.json'])

    def test_permanent_failure_keeps_original_and_cleans_tempfile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'meeting.json')
            common.write_json(path, {'original': True})
            with patch.object(common.os, 'replace', side_effect=PermissionError('denied')), patch.object(common.time, 'sleep'):
                with self.assertRaises(PermissionError):
                    common.write_json(path, {'original': False})
            self.assertEqual(common.read_json(path), {'original':True})
            self.assertEqual(os.listdir(directory), ['meeting.json'])


if __name__ == '__main__':
    unittest.main()
