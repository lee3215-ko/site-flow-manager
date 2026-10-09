import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from app import PublisherApp
from naver_browser import NaverBrowser, NaverSessionError, session_failure
from playwright.sync_api import sync_playwright


class BrowserRecoveryTests(unittest.TestCase):
    def test_plain_driver_eof_is_a_session_failure(self):
        self.assertTrue(session_failure(Exception('Browser.new_browser_cdp_session: Connection closed while reading from the driver')))
        self.assertFalse(session_failure(RuntimeError('unexpected application error')))

    def test_unknown_probe_error_is_not_silently_reconnected(self):
        helper = NaverBrowser(Path('.'))
        helper.browser = Mock()
        helper.browser.new_browser_cdp_session.side_effect = RuntimeError('unexpected application error')
        helper._save_connection_diagnostic = Mock()
        helper.reconnect = Mock()
        app = Mock()
        app.naver_browser = helper
        with self.assertRaisesRegex(NaverSessionError, 'unexpected application error'):
            PublisherApp._get_naver_browser(app)
        helper.reconnect.assert_not_called()
        self.assertIs(app.naver_browser, helper)

    def test_probe_cleanup_error_does_not_discard_successful_connection(self):
        helper = NaverBrowser(Path('.'))
        helper.browser = Mock()
        helper.browser.is_connected.return_value = True
        session = helper.browser.new_browser_cdp_session.return_value
        session.detach.side_effect = RuntimeError('temporary cleanup failure')
        helper._save_connection_diagnostic = Mock()
        self.assertTrue(helper.connection_is_alive())
        helper._save_connection_diagnostic.assert_called_once()

    def test_confirmed_exit_recreates_browser_without_overwriting_saved_state(self):
        app = Mock()
        app.settings = {'last_naver_account': 'saved-account'}
        old = app.naver_browser
        old.browser_process.poll.return_value = 0
        old.connection_is_alive.return_value = False
        old.context.pages = []
        new = MagicMock()
        with patch('app.NaverBrowser', return_value=new):
            self.assertIs(PublisherApp._get_naver_browser(app), new)
        old._close_browser.assert_called_once_with(save_state=False)
        new.__enter__.assert_called_once()
        self.assertEqual(new.login_account, 'saved-account')

    def test_live_browser_is_reused(self):
        app = Mock()
        browser = app.naver_browser
        browser.browser_process.poll.return_value = None
        browser.connection_is_alive.return_value = True
        page = Mock()
        page.is_closed.return_value = False
        browser.context.pages = [page]
        self.assertIs(PublisherApp._get_naver_browser(app), browser)
        browser._close_browser.assert_not_called()

    def test_empty_tabs_do_not_close_connected_browser(self):
        app = Mock()
        old = app.naver_browser
        old.connection_is_alive.return_value = True
        old.context.pages = []
        self.assertIs(PublisherApp._get_naver_browser(app), old)
        old.select_live_page.assert_called_once()
        old._close_browser.assert_not_called()

    def test_launcher_exit_does_not_override_live_connection(self):
        app = Mock()
        old = app.naver_browser
        old.connection_is_alive.return_value = True
        old.browser_process.poll.return_value = 0
        self.assertIs(PublisherApp._get_naver_browser(app), old)
        old._close_browser.assert_not_called()

    def test_tab_selection_error_preserves_login_window(self):
        app = Mock()
        old = app.naver_browser
        old.connection_is_alive.return_value = True
        old.select_live_page.side_effect = RuntimeError('temporary navigation')
        with self.assertRaises(NaverSessionError):
            PublisherApp._get_naver_browser(app)
        old._close_browser.assert_not_called()
        old.playwright.stop.assert_not_called()

    def test_disconnect_reconnects_without_terminating_live_process(self):
        app = Mock()
        old = app.naver_browser
        old.connection_is_alive.return_value = False
        old.browser_process.poll.return_value = None
        self.assertIs(PublisherApp._get_naver_browser(app), old)
        old.reconnect.assert_called_once()
        old._close_browser.assert_not_called()
        self.assertIs(app.naver_browser, old)

    def test_reconnect_failure_preserves_live_process_and_helper(self):
        app = Mock()
        old = app.naver_browser
        old.connection_is_alive.return_value = False
        old.browser_process.poll.return_value = None
        old.reconnect.side_effect = NaverSessionError('connection unavailable')
        with self.assertRaises(NaverSessionError):
            PublisherApp._get_naver_browser(app)
        old._close_browser.assert_not_called()
        self.assertIs(app.naver_browser, old)

    def test_two_stages_keep_actual_chromium_page_and_document(self):
        # Synthetic page only: no Naver login, cookies or remote requests.
        with sync_playwright() as playwright:
            chromium = playwright.chromium.launch(headless=True)
            try:
                context = chromium.new_context()
                page = context.new_page()
                page.set_content('<p id="state">stage-one-session</p>')
                browser = NaverBrowser(Path('.'))
                browser.browser = chromium
                browser.context = context
                browser.page = page
                browser.browser_process = Mock()
                browser.browser_process.poll.return_value = 0
                app = Mock()
                app.naver_browser = browser
                with patch.object(browser, '_close_browser') as close:
                    self.assertIs(PublisherApp._get_naver_browser(app), browser)
                    self.assertIs(PublisherApp._get_naver_browser(app), browser)
                    self.assertEqual(page.locator('#state').inner_text(), 'stage-one-session')
                    self.assertFalse(page.is_closed())
                    close.assert_not_called()
                page.close()
                self.assertIs(PublisherApp._get_naver_browser(app), browser)
                self.assertFalse(browser.page.is_closed())
                self.assertIs(browser.context, context)
            finally:
                chromium.close()
