"""Browser and installed-app icons must reference the current Cojam artwork."""
import json
from pathlib import Path
import unittest
import urllib.request

import test_contacts_access as support


class CojamIcons(unittest.TestCase):
    def test_versioned_icons_are_served_with_current_artwork(self):
        root = Path(__file__).resolve().parents[1]
        url = support.BASE + '/static/icon-192.png?v=cojam-262'
        with urllib.request.urlopen(url) as response:
            self.assertEqual(response.headers.get_content_type(), 'image/png')
            self.assertEqual(response.read(), (root / 'static/icon-192.png').read_bytes())
        for name in ('index.html', 'static/join.html', 'static/enroll.html'):
            self.assertIn('/static/icon-192.png?v=cojam-262', (root / name).read_text(encoding='utf-8'))
        manifest = json.loads((root / 'static/manifest.webmanifest').read_text(encoding='utf-8'))
        self.assertTrue(all(icon['src'].endswith('?v=cojam-262') for icon in manifest['icons']))


if __name__ == '__main__':
    unittest.main()
