import unittest
from unittest.mock import Mock, patch

from app import Project, PublisherApp


class ManualPostTests(unittest.TestCase):
    def helper(self):
        app = Mock(busy=False)
        self.project = Project('test', 'site.zip', 'root', 'https://example.pages.dev', last_error='old')
        app._selected_many.return_value = [self.project]
        return app

    def test_confirmed_manual_completion_does_not_change_deployment(self):
        app = self.helper()
        with patch('app.messagebox.askyesno', return_value=True):
            PublisherApp.mark_post_complete(app)
        self.assertEqual(self.project.crawl, '완료 (수동)')
        self.assertEqual(self.project.ownership, '완료')
        self.assertEqual(self.project.cloudflare, '준비')
        self.assertEqual(self.project.deployment_verified, '대기')
        self.assertTrue(self.project.crawl_completed_at)
        self.assertEqual(self.project.last_error, '')
        app._save.assert_called_once()
        app._get_naver_browser.assert_not_called()

    def test_declined_confirmation_does_not_modify_record(self):
        app = self.helper()
        with patch('app.messagebox.askyesno', return_value=False):
            PublisherApp.mark_post_complete(app)
        self.assertEqual(self.project.crawl, '대기')
        app._save.assert_not_called()

    def test_busy_blocks_changes(self):
        app = self.helper()
        app.busy = True
        with patch('app.messagebox.showinfo'):
            PublisherApp.mark_post_complete(app)
        app._selected_many.assert_not_called()

    def test_completed_record_keeps_original_date(self):
        app = self.helper()
        self.project.crawl = '완료 9개'
        self.project.crawl_completed_at = '2026-09-19 10:00:00'
        with patch('app.messagebox.showinfo'):
            PublisherApp.mark_post_complete(app)
        self.assertEqual(self.project.crawl_completed_at, '2026-09-19 10:00:00')
        app._save.assert_not_called()
