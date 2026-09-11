import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from naver_browser import NaverBrowser, NaverAutomationError


class LoginDetectionTests(unittest.TestCase):
    def helper(self):
        helper = NaverBrowser(Path(tempfile.gettempdir()))
        helper.page = Mock()
        helper.page.is_closed.return_value = False
        helper.page.url = 'https://searchadvisor.naver.com/console/board'
        helper.context = Mock()
        helper.context.pages = [helper.page]
        helper.context.cookies.return_value = []
        helper._visible = Mock(return_value=None)
        return helper

    def test_login_without_registration_input(self):
        helper = self.helper()
        response = Mock(url='https://searchadvisor.naver.com/api-board/list/test')
        response.frame.page = helper.page
        response.json.return_value = {'code': 0, 'items': [], 'meta': {'max': 100}}
        helper._goto_tolerating_login_redirect = lambda _: helper._capture_board_response(response)
        self.assertEqual(helper.count_registered_sites(), (0, 100))
        helper.context.storage_state.assert_called_once()

    def test_stop_interrupts_login_wait(self):
        helper = self.helper()
        helper._goto_tolerating_login_redirect = Mock()
        helper.cancel_event = threading.Event()
        helper.cancel_event.set()
        with self.assertRaisesRegex(NaverAutomationError, '중지'):
            helper._wait_dashboard(require_input=False)

    def test_failed_response_is_not_login_success(self):
        helper = self.helper()
        response = Mock(url='https://searchadvisor.naver.com/api-board/list/test')
        response.frame.page = helper.page
        response.json.return_value = {'code': 401, 'items': []}
        helper._capture_board_response(response)
        self.assertIsNone(helper.board_payload)
