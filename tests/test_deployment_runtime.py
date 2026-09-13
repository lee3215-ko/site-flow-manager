from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from deployment_runtime import runtime_command
from cloudflare_client import CloudflareClient


class RuntimeTests(unittest.TestCase):
    def test_bundled_runtime_without_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / 'browser/siteflow-runtime'
            cli = runtime / 'node_modules/npm/bin/npx-cli.js'
            cli.parent.mkdir(parents=True)
            cli.touch()
            (runtime / 'node.exe').touch()
            with patch('deployment_runtime.shutil.which', return_value=None), patch.dict('os.environ', {}, clear=True):
                self.assertEqual(runtime_command(root), [str(runtime / 'node.exe'), str(cli)])

    def test_missing_runtime_stops_before_project_creation(self):
        client = CloudflareClient('a' * 32, 'token')
        client.ensure_project = Mock()
        with patch('cloudflare_client.preflight', side_effect=RuntimeError('missing')):
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                client.deploy('test', Path('.'))
        client.ensure_project.assert_not_called()
