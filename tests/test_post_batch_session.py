import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import PublisherApp
from naver_browser import NaverSessionError
from playwright.sync_api import Error


class PostBatchSessionTests(unittest.TestCase):
    def run_batch(self, error=None, cancel_during_check=False):
        with tempfile.TemporaryDirectory() as directory:
            verification = Path(directory) / 'naver-test.html'
            verification.touch()
            projects = [SimpleNamespace(
                name=str(index), url=f'https://site{index}.pages.dev',
                ownership='완료', crawl='', frequency='', robots='', sitemap='',
                verification_file=str(verification), site_root=directory,
                last_error='', crawl_completed_at='',
            ) for index in range(2)]
            app = Mock()
            app.cancel_event = threading.Event()
            app._selected_many.return_value = projects
            browser = app._get_naver_browser.return_value
            browser.run_after_ownership.side_effect = error
            app._background.side_effect = lambda label, run, done: run()
            def verify(*args, **kwargs):
                if cancel_during_check:
                    app.cancel_event.set()
            with patch('app.messagebox.askokcancel', return_value=True), \
                    patch('app.verify_public_deployment', side_effect=verify), \
                    patch('app.load_manifest', return_value={'pages': []}):
                PublisherApp.run_post_batch(app)
            return browser, projects

    def test_auth_failure_stops_before_next_site(self):
        browser, projects = self.run_batch(NaverSessionError('Authentication failed'))
        self.assertEqual(browser.run_after_ownership.call_count, 1)
        self.assertEqual(projects[1].last_error, '')

    def test_closed_browser_stops_before_next_site(self):
        browser, projects = self.run_batch(Error('Target page, context or browser has been closed'))
        self.assertEqual(browser.run_after_ownership.call_count, 1)
        self.assertEqual(projects[1].last_error, '')

    def test_cancel_during_public_check_does_not_start_browser_work(self):
        browser, projects = self.run_batch(cancel_during_check=True)
        browser.run_after_ownership.assert_not_called()

