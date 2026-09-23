import unittest
from unittest.mock import MagicMock, Mock, patch
from app import PublisherApp


class BrowserRecoveryTests(unittest.TestCase):
    def test_all_closed_tabs_recreates_browser_without_overwriting_saved_state(self):
        app = Mock()
        app.settings = {'last_naver_account': 'saved-account'}
        old = app.naver_browser
        old.browser_process.poll.return_value = None
        old.browser.is_connected.return_value = True
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
        browser.browser.is_connected.return_value = True
        page = Mock()
        page.is_closed.return_value = False
        browser.context.pages = [page]
        self.assertIs(PublisherApp._get_naver_browser(app), browser)
        browser._close_browser.assert_not_called()
