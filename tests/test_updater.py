import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_contacts_access as support
from bridge import updater


class Updates(unittest.TestCase):
    def test_macos_does_not_offer_windows_installer(self):
        with patch.object(updater.sys, 'platform', 'darwin'), patch.object(updater, '_open') as request:
            result = updater.check()
        self.assertEqual(result['state'], 'error')
        self.assertIn('macOS', result['error'])
        request.assert_not_called()

    def manifest(self, payload=b'installer'):
        return {'app':'Cojam', 'version':'99.0.0',
                'url':'https://jun-genyan.github.io/Cojam/downloads/Cojam-Setup-99.0.0.exe',
                'sha256':hashlib.sha256(payload).hexdigest(), 'size':len(payload)}

    def test_rejects_untrusted_urls_and_bad_manifest(self):
        for url in ['http://jun-genyan.github.io/Cojam/downloads/Cojam-Setup-99.0.0.exe',
                    'https://evil.test/Cojam/downloads/Cojam-Setup-99.0.0.exe',
                    'https://jun-genyan.github.io/Cojam/downloads/../Cojam-Setup-99.0.0.exe',
                    'https://jun-genyan.github.io/Cojam/downloads/Cojam-Setup-99.0.0.exe?x=y']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                updater.parse_manifest(dict(self.manifest(),url=url))
        with self.assertRaises(ValueError):
            updater.parse_manifest(dict(self.manifest(), sha256='bad'))

    def test_verified_download_never_runs_and_never_overwrites(self):
        release=updater.parse_manifest(self.manifest())
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp,'Cojam-Setup-99.0.0.exe');old.write_bytes(b'old')
            with patch.object(updater,'_open',return_value=io.BytesIO(b'installer')):
                updater._download(release,tmp)
            result=updater.status()
            self.assertEqual(result['state'],'downloaded')
            self.assertEqual(old.read_bytes(),b'old')
            self.assertEqual(Path(result['path']).read_bytes(),b'installer')
            self.assertFalse(list(Path(tmp).glob('*.tmp')))

    def test_corrupt_download_is_not_saved(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(updater,'_open',return_value=io.BytesIO(b'corrupt!!')):
            updater._download(updater.parse_manifest(self.manifest()),tmp)
            self.assertEqual(updater.status()['state'],'error')
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_only_authenticated_local_owner_can_start_download(self):
        self.assertEqual(support.call('/api/update/download', {})[0],403)
        self.assertEqual(support.call('/api/update/download', {}, owner=True,
                                      headers={'X-Forwarded-For':'10.0.0.2'})[0],403)
        with patch.object(updater,'start_download',return_value={'state':'downloading'}):
            self.assertEqual(support.call('/api/update/download', {}, owner=True)[0],200)


if __name__ == '__main__':
    unittest.main()
