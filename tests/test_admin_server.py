import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import zipfile
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash
from admin_server.server import create_app
from admin_sync import AdminSync, validate_url


class AdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = 'test-password-at-least-14'
        cls.password_hash = generate_password_hash(cls.password)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_app(dict(TESTING=True, SECRET_KEY='s'*40, DEVICE_KEY='d'*40,
                                  PASSWORD_HASH=self.password_hash, VAULT_KEY=Fernet.generate_key(),
                                  DATA_DIR=self.tmp.name, SESSION_COOKIE_SECURE=False))
        self.client = self.app.test_client()
        self.account = 'a'*32
        self.token = 'private-cloudflare-test-token'
        self.payload = dict(device='device-1', name='Office', version='test',
                            projects=[dict(name='site', url='https://site.pages.dev', last_error='secret', zip_path='private')],
                            accounts=[dict(account_id=self.account, name='Account', api_token=self.token)])

    def sync(self):
        return self.client.post('/api/sync', json=self.payload, headers={'Authorization': 'Bearer '+'d'*40})

    def login(self):
        response = self.client.post('/api/login', json={'password': self.password})
        self.assertEqual(response.status_code, 200)
        return {'X-CSRF-Token': response.json['csrf']}

    def test_auth_and_device_scope(self):
        self.assertEqual(self.client.get('/api/state').status_code, 401)
        self.assertEqual(self.client.post('/api/sync', json=self.payload).status_code, 401)
        self.assertEqual(self.client.get('/api/state', headers={'Authorization':'Bearer '+'d'*40}).status_code, 401)
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_snapshot_dedup_and_secrets_encrypted(self):
        self.assertEqual(self.sync().status_code, 200)
        self.sync()
        self.login()
        data = self.client.get('/api/state').json
        self.assertEqual(len(data['records']), 1)
        self.assertNotIn('last_error', data['records'][0]['payload'])
        self.assertNotIn('zip_path', data['records'][0]['payload'])
        self.assertNotIn(self.token, json.dumps(data))
        self.assertNotIn(self.token.encode(), (Path(self.tmp.name)/'admin.sqlite3').read_bytes())
        self.payload['projects'][0]['crawl'] = 'complete'
        self.sync()
        self.assertEqual(len(self.client.get('/api/state').json['records']), 2)

    def test_reveal_requires_csrf_and_password(self):
        self.sync()
        headers = self.login()
        body = dict(account=self.account, password=self.password)
        self.assertEqual(self.client.post('/api/token', json=body).status_code, 403)
        self.assertEqual(self.client.post('/api/token', json={**body, 'password':'wrong'}, headers=headers).status_code, 401)
        result = self.client.post('/api/token', json=body, headers=headers)
        self.assertEqual(result.json['token'], self.token)
        self.assertEqual(result.headers['Cache-Control'], 'no-store')

    def test_logout_revokes_session(self):
        headers = self.login()
        self.client.post('/api/logout', json={}, headers=headers)
        self.assertEqual(self.client.get('/api/state').status_code, 401)

    def test_login_throttle(self):
        for _ in range(10):
            self.assertEqual(self.client.post('/api/login', json={'password':'bad'}).status_code, 401)
        self.assertEqual(self.client.post('/api/login', json={'password':'bad'}).status_code, 429)

    def deploy(self, headers, extra=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            z.writestr('index.html', '<html><head><title>Test</title></head><body>OK</body></html>')
            for name, text in (extra or {}).items():
                z.writestr(name, text)
        stream.seek(0)
        return self.client.post('/api/redeploy', headers=headers, data={
            'account':self.account, 'project':'site', 'zip':(stream, 'site.zip')})

    @patch('admin_server.server.CloudflareClient')
    def test_existing_project_redeploy_uses_actual_domain(self, factory):
        self.sync()
        client = factory.return_value
        client.list_projects.return_value = [{'name':'site', 'subdomain':'site-abc.pages.dev'}]
        def check(name, root):
            self.assertEqual(name, 'site')
            self.assertIn('https://site-abc.pages.dev', (root/'sitemap.xml').read_text())
            self.assertIn('https://site-abc.pages.dev/sitemap.xml', (root/'robots.txt').read_text())
            return '', ''
        client.deploy.side_effect = check
        self.assertEqual(self.deploy(self.login()).status_code, 200)
        client.ensure_project.assert_not_called()

    @patch('admin_server.server.CloudflareClient')
    def test_missing_project_does_not_create(self, factory):
        self.sync()
        factory.return_value.list_projects.return_value = []
        self.assertEqual(self.deploy(self.login()).status_code, 400)
        factory.return_value.deploy.assert_not_called()

    @patch('admin_server.server.CloudflareClient')
    def test_unsafe_zip_and_runtime_config_rejected(self, factory):
        self.sync()
        factory.return_value.list_projects.return_value = [{'name':'site', 'subdomain':'site.pages.dev'}]
        headers = self.login()
        for path in ('../escape.txt', '.npmrc', 'wrangler.toml', '_worker.js', 'functions/api.js'):
            self.assertEqual(self.deploy(headers, {path:'bad'}).status_code, 400, path)
        factory.return_value.deploy.assert_not_called()

    @patch('admin_server.server.CloudflareClient')
    def test_provider_error_is_not_returned(self, factory):
        self.sync()
        factory.return_value.list_projects.side_effect = RuntimeError(self.token)
        result = self.deploy(self.login())
        self.assertNotIn(self.token, result.get_data(as_text=True))
        self.assertNotIn(self.token, self.client.get('/api/state').get_data(as_text=True))


class SyncTests(unittest.TestCase):
    def test_https_required(self):
        for url in ('http://example.com', 'https://user:pass@example.com', 'https://example.com/path', 'file:///tmp'):
            with self.assertRaises(ValueError):
                validate_url(url)
        self.assertEqual(validate_url('http://127.0.0.1:8787/'), 'http://127.0.0.1:8787')

    @patch.object(AdminSync, 'flush')
    @patch('admin_sync.requests.post')
    def test_encrypted_outbox_survives_failure_and_restart(self, post, flush):
        with tempfile.TemporaryDirectory() as folder:
            sync = AdminSync(folder)
            sync.configure('http://127.0.0.1:8787', 'd'*40, 'test')
            sync.enqueue([{'name':'test', 'last_error':'secret'}], [], 'test')
            file = next(sync.outbox.glob('*.bin'))
            self.assertNotIn(b'"projects"', file.read_bytes())
            post.side_effect = OSError('offline')
            sync._send()
            self.assertTrue(file.exists())
            again = AdminSync(folder)
            post.side_effect = None
            post.return_value = Mock(status_code=200, json=lambda: {'ok':True})
            again._send()
            self.assertFalse(file.exists())
            self.assertNotIn('last_error', post.call_args.kwargs['json']['projects'][0])
            self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as folder:
            sync = AdminSync(folder)
            sync.enqueue([], [], 'test')
            self.assertFalse(sync.outbox.exists())


if __name__ == '__main__':
    unittest.main()
