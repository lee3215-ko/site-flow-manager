import unittest
from unittest.mock import Mock, patch
from app import Project, PublisherApp


class AccountTests(unittest.TestCase):
    def test_ownership_preserves_each_registration_account_without_prompt(self):
        projects = [Project(str(i), '', '', 'https://example.pages.dev',
                            naver_account=name, deployment_verified='완료')
                    for i, name in enumerate(['first', 'second', ''])]
        app = Mock(busy=False)
        app.settings = {'last_naver_account': 'current'}
        app._selected_many.return_value = projects
        with patch('app.simpledialog.askstring') as prompt, patch('app.messagebox.askyesno', return_value=True):
            PublisherApp.mark_verified(app)
        prompt.assert_not_called()
        self.assertEqual([p.naver_account for p in projects], ['first', 'second', 'current'])
        self.assertTrue(all(p.ownership == '완료' for p in projects))

    def test_manual_account_edit_is_remembered_without_overwriting_projects(self):
        app = Mock(busy=False)
        app.settings = {'last_naver_account': 'old'}
        app.projects = [Project('site', '', '', '', naver_account='original')]
        with patch('app.simpledialog.askstring', return_value=' new-user '):
            PublisherApp.edit_naver_account(app)
        self.assertEqual(app.settings['last_naver_account'], 'new-user')
        self.assertEqual(app.naver_browser.login_account, 'new-user')
        self.assertEqual(app.projects[0].naver_account, 'original')
        app._save.assert_called_once()
