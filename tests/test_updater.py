import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from updater import MANAGED, latest_release, unpack_verified, replace_files, version_tuple
from version import ASSET_NAME, GITHUB_REPOSITORY


class UpdaterTests(unittest.TestCase):
    def bundle(self, root, extra=None):
        archive = root / 'download.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('SiteFlow/SiteFlow.exe', b'new exe')
            z.writestr('SiteFlow/browser/chrome.exe', b'new browser')
            z.writestr('SiteFlow/README.md', 'readme')
            z.writestr('SiteFlow/version.json', json.dumps({'version': '4.1.0'}))
            if extra:
                z.writestr(extra, 'invalid')
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    def test_numeric_version_order(self):
        self.assertGreater(version_tuple('v4.10.0'), version_tuple('4.9.0'))
        self.assertEqual(version_tuple('3.8'), (3, 8, 0))
        with self.assertRaises(ValueError):
            version_tuple('4.2.0-beta')

    def test_release_official_asset_and_digest_required(self):
        asset = {'name': ASSET_NAME, 'size': 100, 'digest': 'sha256:' + 'a'*64,
                 'browser_download_url': f'https://github.com/{GITHUB_REPOSITORY}/releases/download/v4.1.0/{ASSET_NAME}'}
        response = Mock()
        response.status_code = 200
        response.json.return_value = {'tag_name': 'v4.1.0', 'assets': [asset]}
        with patch('updater.requests.get', return_value=response):
            self.assertEqual(latest_release('4.0.0')['version'], '4.1.0')
            self.assertIsNone(latest_release('4.1.0'))
            asset['digest'] = None
            with self.assertRaises(ValueError):
                latest_release('4.0.0')

    def test_verified_package_and_invalid_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, digest = self.bundle(root)
            with self.assertRaises(ValueError):
                unpack_verified(archive, root / 'bad', '0'*64, '4.1.0')
            self.assertFalse((root/'bad').exists())
            payload = unpack_verified(archive, root/'good', digest, '4.1.0')
            self.assertEqual((payload/'SiteFlow.exe').read_bytes(), b'new exe')

    def test_zip_traversal_and_user_data_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for extra in ['SiteFlow/browser/../../escape.txt', 'SiteFlow/settings.json',
                          'SiteFlow/browser/C:/evil', '/SiteFlow/SiteFlow.exe']:
                archive, digest = self.bundle(root, extra)
                with self.assertRaises(ValueError):
                    unpack_verified(archive, root/'bad', digest, '4.1.0')

    def test_install_and_rollback_preserve_unmanaged_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            install = root/'app'
            install.mkdir()
            for name in MANAGED:
                if name == 'browser':
                    (install/name).mkdir()
                    (install/name/'chrome.exe').write_bytes(b'old browser')
                else:
                    (install/name).write_bytes(b'old')
            (install/'user.txt').write_bytes(b'keep')
            archive, digest = self.bundle(root)
            payload = unpack_verified(archive, root/'stage', digest, '4.1.0')
            # A missing late file forces rollback after the executable/browser changed.
            version_file = (payload/'version.json').read_bytes()
            (payload/'version.json').unlink()
            with self.assertRaises(FileNotFoundError):
                replace_files(install, payload, root/'backup')
            self.assertEqual((install/'SiteFlow.exe').read_bytes(), b'old')
            self.assertEqual((install/'browser/chrome.exe').read_bytes(), b'old browser')
            self.assertEqual((install/'user.txt').read_bytes(), b'keep')
            (payload/'version.json').write_bytes(version_file)
            replace_files(install, payload, root/'backup')
            self.assertEqual((install/'SiteFlow.exe').read_bytes(), b'new exe')
            self.assertEqual((root/'backup/SiteFlow.exe').read_bytes(), b'old')
            self.assertEqual((install/'user.txt').read_bytes(), b'keep')
