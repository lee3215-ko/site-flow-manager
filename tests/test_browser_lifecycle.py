import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from app import PublisherApp
from naver_browser import NaverBrowser, NaverSessionError


ORIGIN = 'http://siteflow.test'


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
        self.helper.page.goto(ORIGIN + '/verification')

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
