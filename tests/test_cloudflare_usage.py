import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import PublisherApp
from cloudflare_client import CloudflareAccountPool


class CloudflareUsageTests(unittest.TestCase):
    def setUp(self):
        self.accounts = [
            {'name': 'Full', 'account_id': 'a' * 32, 'api_token': 'test-one'},
            {'name': 'Available', 'account_id': 'b' * 32, 'api_token': 'test-two'},
        ]
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for name, path in [('DATA_FILE', root / 'projects.json'),
                           ('SETTINGS_FILE', root / 'settings.json'),
                           ('TOKEN_FILE', root / 'tokens.bin')]:
            patcher = patch(f'app.{name}', path)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('app.save_secret')
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('app.load_secret', return_value=json.dumps({
            item['account_id']: item['api_token'] for item in self.accounts
        }))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = SimpleNamespace(
            settings={'cloudflare_accounts': self.accounts}, projects=[],
            cloudflare_statuses=[], naver_site_count=None, naver_count_error='',
            _append_log=Mock(), _update_service_labels=Mock(),
        )
        self.app._cloudflare_accounts = lambda: PublisherApp._cloudflare_accounts(self.app)
        self.app._save = lambda: PublisherApp._save(self.app)
        self.app._background = lambda _message, work, done: done(work())

    def pool(self):
        pool = CloudflareAccountPool(self.accounts)
        for client in pool.clients:
            client.list_projects = Mock(return_value=[])
        return pool

    def refresh(self, pool):
        with patch('app.CloudflareAccountPool', return_value=pool):
            PublisherApp.refresh_service_counts(self.app)

    def test_first_lookup_is_saved_and_full_account_is_skipped_after_restart(self):
        first = self.pool()
        first.clients[0].list_projects.return_value = [{'name': str(i)} for i in range(100)]
        first.clients[1].list_projects.return_value = [{'name': 'available'}]
        self.refresh(first)
        first.clients[0].list_projects.assert_called_once_with(refresh=True)
        self.app.settings = PublisherApp._load_settings(self.app)
        self.app.cloudflare_statuses = []

        second = self.pool()
        self.refresh(second)
        second.clients[0].list_projects.assert_not_called()
        second.clients[1].list_projects.assert_called_once_with(refresh=True)
        status = self.app.cloudflare_statuses[0]
        self.assertEqual(status['count'], 100)
        self.assertTrue(status['recorded'])
        self.assertTrue(any('조회 생략' in call.args[0] for call in self.app._append_log.call_args_list))

    def test_all_full_accounts_need_no_api_lookup(self):
        pool = self.pool()
        statuses = pool.refresh(recorded_counts={'a' * 32: 100, 'b' * 32: 101})
        for client in pool.clients:
            client.list_projects.assert_not_called()
        self.assertEqual([s['count'] for s in statuses], [100, 101])
        self.assertTrue(all(s['recorded'] and not s['error'] for s in statuses))

    def test_recording_100th_project_is_persisted_for_next_startup(self):
        pool = self.pool()
        pool.projects = [[{'name': str(i)} for i in range(99)], []]
        pool.record_project('a' * 32, 'hundredth')
        self.app.cloudflare_statuses = pool.statuses()
        self.app._save()
        self.app.settings = PublisherApp._load_settings(self.app)
        next_pool = self.pool()
        self.refresh(next_pool)
        next_pool.clients[0].list_projects.assert_not_called()
        self.assertEqual(self.app.cloudflare_statuses[0]['count'], 100)

    def test_below_limit_and_invalid_records_are_still_queried(self):
        for recorded in (99, '100', True, None):
            with self.subTest(recorded=recorded):
                pool = self.pool()
                pool.refresh(recorded_counts={'a' * 32: recorded})
                for client in pool.clients:
                    client.list_projects.assert_called_once_with(refresh=True)

    def test_explicit_lookup_can_reenable_an_account_after_deletion(self):
        self.app.settings['cloudflare_counts'] = {'a' * 32: 100}
        pool = self.pool()
        pool.clients[0].list_projects.return_value = [{'name': str(i)} for i in range(90)]
        self.app.cloudflare_statuses = pool.refresh()
        self.app._save()
        self.assertEqual(self.app.settings['cloudflare_counts']['a' * 32], 90)
        self.app.settings = PublisherApp._load_settings(self.app)
        self.refresh(self.pool())
        self.assertNotIn('recorded', self.app.cloudflare_statuses[0])

    def test_query_failure_does_not_erase_last_known_count(self):
        self.app.settings['cloudflare_counts'] = {'a' * 32: 100}
        self.app.cloudflare_statuses = [{
            'account_id': 'a' * 32, 'count': None, 'error': 'connection failed',
        }]
        self.app._save()
        loaded = PublisherApp._load_settings(self.app)
        self.assertEqual(loaded['cloudflare_counts']['a' * 32], 100)


if __name__ == '__main__':
    unittest.main()
