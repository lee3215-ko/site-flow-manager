import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from werkzeug.security import check_password_hash
from admin_server.embedded import LocalAdmin


class EmbeddedTests(unittest.TestCase):
    @patch('admin_server.embedded.threading.Thread')
    @patch('admin_server.embedded.create_server')
    def test_setup_persists_and_reuses_config(self, create, thread):
        with tempfile.TemporaryDirectory() as directory:
            admin = LocalAdmin(directory)
            self.assertTrue(admin.needs_setup)
            config = admin.start('long-local-test-password')
            self.assertTrue(check_password_hash(config['PASSWORD_HASH'], 'long-local-test-password'))
            self.assertNotIn(config['DEVICE_KEY'].encode(), (Path(directory)/'server.bin').read_bytes())
            self.assertEqual(admin.start(), config)
            create.assert_called_once()
            self.assertEqual(create.call_args.kwargs, {'host':'127.0.0.1', 'port':8787})
            admin.close()
            create.return_value.close.assert_called_once()
            restarted = LocalAdmin(directory)
            self.assertFalse(restarted.needs_setup)
            self.assertEqual(restarted.start()['DEVICE_KEY'], config['DEVICE_KEY'])
            restarted.close()

    def test_short_password_does_not_create_config(self):
        with tempfile.TemporaryDirectory() as directory:
            admin = LocalAdmin(directory)
            with self.assertRaises(ValueError):
                admin.start('short')
            self.assertTrue(admin.needs_setup)

    @patch('admin_server.embedded.create_server', side_effect=OSError('port busy'))
    def test_port_conflict_does_not_attach_to_unknown_service(self, create):
        with tempfile.TemporaryDirectory() as directory:
            admin = LocalAdmin(directory)
            with self.assertRaisesRegex(RuntimeError, '포트'):
                admin.start('long-local-test-password')
            self.assertIsNone(admin.server)
