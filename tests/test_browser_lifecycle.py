import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from playwright.sync_api import Error as PlaywrightError

from app import PublisherApp
from naver_browser import NaverBrowser, NaverSessionError


ORIGIN = 'https://siteflow.test'


class BrowserWorkerLifecycleTests(unittest.TestCase):
    def test_unexpected_driver_exit_between_tasks_recovers_same_window_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            with ThreadPoolExecutor(max_workers=1) as worker:
                def stage_one():
                    helper = NaverBrowser(Path(directory), headless=True)
                    helper.__enter__()
                    helper.page.set_content('<p id="manual">ownership-completed</p>')
                    return helper
                helper = worker.submit(stage_one).result(timeout=30)
                try:
                    app = Mock()
                    app.naver_browser = helper
                    browser_pid = helper.browser_process.pid
                    for _ in range(2):
                        def stop_test_driver():
                            helper.playwright._impl_obj._connection._transport._proc.kill()
                        worker.submit(stop_test_driver).result(timeout=10)
                        time.sleep(0.1)
                        self.assertIs(worker.submit(PublisherApp._get_naver_browser, app).result(timeout=20), helper)
                        self.assertEqual(worker.submit(lambda: helper.page.locator('#manual').inner_text()).result(timeout=10), 'ownership-completed')
                        self.assertEqual(helper.browser_process.pid, browser_pid)
                        self.assertIsNone(helper.browser_process.poll())
                finally:
                    worker.submit(helper.__exit__, None, None, None).result(timeout=30)

    def test_separate_stage_tasks_reuse_worker_and_manual_document(self):
        with tempfile.TemporaryDirectory() as directory:
            with ThreadPoolExecutor(max_workers=1) as worker:
                def stage_one():
                    helper = NaverBrowser(Path(directory), headless=True)
                    helper.__enter__()
                    helper.page.set_content('<p id="manual">ownership-completed</p>')
                    return helper
                helper = worker.submit(stage_one).result(timeout=30)
                try:
                    app = Mock()
                    app.naver_browser = helper
                    helper.login_account = 'synthetic-manual-account'
                    for _ in range(3):
                        time.sleep(0.2)
                        self.assertIs(worker.submit(PublisherApp._get_naver_browser, app).result(timeout=20), helper)
                        self.assertEqual(worker.submit(lambda: helper.page.locator('#manual').inner_text()).result(timeout=10), 'ownership-completed')
                        self.assertEqual(worker.submit(threading.get_ident).result(timeout=10), helper._owner_thread)
                    self.assertEqual(helper.login_account, 'synthetic-manual-account')
                finally:
                    worker.submit(helper.__exit__, None, None, None).result(timeout=30)


class BrowserLifecycleTests(unittest.TestCase):
    """Use the real launch/CDP path; only synthetic pages and login data."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.helper = NaverBrowser(Path(self.temporary.name), headless=True)
        self.helper.__enter__()
        self.addCleanup(self.helper.__exit__, None, None, None)
        self.assertIs(self.helper.context, self.helper.browser.contexts[0])
        self.helper.context.route(ORIGIN + '/**', lambda route: route.fulfill(
            body='<p id="state">ownership-completed</p>', content_type='text/html'))
        self.helper.page.goto(ORIGIN + '/verification', wait_until='domcontentloaded')

    def download_html(self, filename):
        self.helper.page.evaluate('''name => {
          const a = document.createElement('a');
          a.textContent = 'Download';
          a.id = 'download';
          a.href = URL.createObjectURL(new Blob(['synthetic verification HTML'],
            {type: 'text/html'}));
          a.download = name;
          document.body.append(a);
        }''', filename)
        with self.helper.page.expect_download(timeout=10000) as downloaded:
            self.helper.page.locator('#download').click()
        target = Path(self.temporary.name) / filename
        downloaded.value.save_as(target)
        self.assertEqual(target.read_text(), 'synthetic verification HTML')
        self.helper.page.locator('#download').evaluate('(a) => a.remove()')

    def test_stage_transition_survives_repeated_driver_disconnects(self):
        helper = self.helper
        helper.context.add_cookies([{'name': 'synthetic_login', 'value': 'logged-in',
                                    'url': ORIGIN}])
        helper.page.evaluate("localStorage.setItem('login', 'synthetic'); sessionStorage.setItem('site', 'selected'); window.stage = 'one'")
        self.download_html('naver-synthetic-before.html')
        app = Mock()
        app.naver_browser = helper
        for _ in range(3):
            helper.playwright.stop()
            self.assertFalse(helper.connection_is_alive())
            self.assertIsNone(helper.browser_process.poll())
            with urllib.request.urlopen(helper._cdp_endpoint + '/json/list', timeout=5) as response:
                targets = json.load(response)
            self.assertEqual(len([target for target in targets if target['type'] == 'page']), 1)
            self.assertIs(PublisherApp._get_naver_browser(app), helper)
            self.assertEqual(helper.page.locator('#state').inner_text(), 'ownership-completed')
            self.assertEqual(helper.page.evaluate('window.stage'), 'one')
            self.assertEqual(helper.page.evaluate("localStorage.getItem('login')"), 'synthetic')
            self.assertEqual(helper.page.evaluate("sessionStorage.getItem('site')"), 'selected')
            self.assertEqual(helper.context.cookies(ORIGIN)[0]['value'], 'logged-in')
            self.assertEqual(len(helper.context.pages), 1)
        self.download_html('naver-synthetic-after.html')

    def test_failed_reconnect_does_not_close_window(self):
        helper = self.helper
        helper.playwright.stop()
        with patch.object(helper, '_connect_browser', side_effect=RuntimeError('unavailable')):
            with self.assertRaises(NaverSessionError):
                helper.reconnect()
        self.assertIsNone(helper.browser_process.poll())
        helper.reconnect()
        self.assertEqual(helper.page.locator('#state').inner_text(), 'ownership-completed')

    def test_protocol_probe_error_reconnects_once_without_navigation(self):
        helper = self.helper
        app = Mock()
        app.naver_browser = helper
        with patch.object(helper.browser, 'new_browser_cdp_session', side_effect=PlaywrightError(
                'Protocol error (Target.attachToBrowserTarget): No session with given id')):
            with patch.object(helper, 'reconnect', wraps=helper.reconnect) as reconnect:
                self.assertIs(PublisherApp._get_naver_browser(app), helper)
                reconnect.assert_called_once()
        self.assertEqual(helper.page.locator('#state').inner_text(), 'ownership-completed')
        self.assertEqual(len(helper.context.pages), 1)
        diagnostic = json.loads((helper.profile_dir / 'diagnostics/connection-latest.json').read_text(encoding='utf-8'))
        self.assertEqual(diagnostic['stage'], 'health-check')
        self.assertIn('Protocol error', diagnostic['error'])
        self.assertEqual(diagnostic['owner_thread'], diagnostic['current_thread'])

    def test_unexpected_driver_exit_recovers_selected_page_without_navigation(self):
        helper = self.helper
        helper.page.evaluate("window.manualOwnership = 'completed'; sessionStorage.setItem('site', 'selected')")
        helper.context.add_cookies([{'name': 'synthetic_login', 'value': 'logged-in', 'url': ORIGIN}])
        browser_pid = helper.browser_process.pid
        selected_url = helper.page.url
        # Terminate only this test's automation driver, not Chromium or a user process.
        driver = helper.playwright._impl_obj._connection._transport._proc
        driver.kill()
        app = Mock()
        app.naver_browser = helper
        with patch.object(helper, 'reconnect', wraps=helper.reconnect) as reconnect:
            self.assertIs(PublisherApp._get_naver_browser(app), helper)
            reconnect.assert_called_once()
        self.assertEqual(helper.browser_process.pid, browser_pid)
        self.assertIsNone(helper.browser_process.poll())
        self.assertEqual(helper.page.url, selected_url)
        self.assertEqual(helper.page.evaluate('window.manualOwnership'), 'completed')
        self.assertEqual(helper.page.evaluate("sessionStorage.getItem('site')"), 'selected')
        self.assertEqual(helper.context.cookies(ORIGIN)[0]['value'], 'logged-in')
        self.assertEqual(len(helper.context.pages), 1)
        self.download_html('naver-synthetic-after-driver-exit.html')

    def test_connection_error_diagnostic_redacts_authentication_values(self):
        error = RuntimeError('failed https://searchadvisor.naver.com/auth/callback?code=secret-one&state=secret-two token=secret-three cookie=secret-four')
        self.helper._save_connection_diagnostic('synthetic', error)
        raw = (self.helper.profile_dir / 'diagnostics/connection-latest.json').read_text(encoding='utf-8')
        for secret in ('secret-one', 'secret-two', 'secret-three', 'secret-four'):
            self.assertNotIn(secret, raw)

    def test_saved_state_restores_without_reapplying_after_logout(self):
        helper = self.helper
        helper.state_file.write_text(json.dumps({
            'cookies': [{'name': 'saved_login', 'value': 'synthetic', 'domain': 'siteflow.test',
                         'path': '/', 'expires': -1, 'httpOnly': False, 'secure': False,
                         'sameSite': 'Lax'}],
            'origins': [{'origin': ORIGIN, 'localStorage': [{'name': 'login', 'value': 'saved'}]}],
        }), encoding='utf-8')
        helper._restore_saved_state()
        helper.page.reload()
        self.assertEqual(helper.page.evaluate("localStorage.getItem('login')"), 'saved')
        self.assertEqual(helper.context.cookies(ORIGIN)[0]['value'], 'synthetic')
        helper.page.evaluate("localStorage.removeItem('login')")
        helper.page.reload()
        self.assertIsNone(helper.page.evaluate("localStorage.getItem('login')"))

    def test_account_input_capture_works_on_preserved_login_document(self):
        helper = self.helper
        helper.context.route('https://nid.naver.com/**', lambda route: route.fulfill(
            body='<input id="id">', content_type='text/html'))
        helper.page.goto('https://nid.naver.com/synthetic-login')
        helper.page.locator('#id').fill('first-synthetic-account')
        helper.page.wait_for_timeout(100)
        self.assertEqual(helper._pending_login_account, 'first-synthetic-account')
        helper.playwright.stop()
        helper.reconnect()
        helper.page.locator('#id').fill('second-synthetic-account')
        helper.page.wait_for_timeout(100)
        self.assertEqual(helper._pending_login_account, 'second-synthetic-account')
        self.assertEqual(len(helper.context.pages), 1)
