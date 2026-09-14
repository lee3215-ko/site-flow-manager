import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from app import PublisherApp, Project
from site_builder import write_seo_files


class DeployOnlyTests(unittest.TestCase):
    def test_without_naver_then_normal_registration_redeploys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'site'
            root.mkdir()
            (root / 'index.html').write_text('<html><head></head><body>site</body></html>')
            write_seo_files(root, 'https://sample.pages.dev')
            archive = Path(directory) / 'sample.zip'
            archive.touch()
            project = Project('sample', str(archive), str(root), 'https://sample.pages.dev')
            app = Mock()
            app.deploy_only_var.get.return_value = True
            app._delay_range.return_value = (0, 0)
            app._selected_many.return_value = [project]
            app.cancel_event = threading.Event()
            client = Mock()
            client.deploy.return_value = ('https://sample.pages.dev', '')
            pool = Mock()
            pool.statuses.return_value = []
            pool.project_for.return_value = (client, {'account_id': 'a'*32, 'name': 'test'}, project.url)
            verification = Path(directory) / 'naver123.html'
            verification.write_text('naver-site-verification: naver123.html')
            browser = Mock()
            browser.register_and_download.return_value = verification
            app._get_naver_browser.return_value = browser
            with patch('app.messagebox.askokcancel', return_value=True), patch('deployment_runtime.preflight'), patch('app.CloudflareAccountPool', return_value=pool), patch('app.verify_public_deployment') as verify, patch('app.move_zip_to_success', return_value=archive):
                PublisherApp.run_registration_batch(app)
                result = app._background.call_args.args[1]()
                self.assertEqual(result[1], [])
                app._get_naver_browser.assert_not_called()
                self.assertEqual(project.registration, '대기')
                self.assertEqual(project.ownership, '대기')
                self.assertEqual(project.deployment_verified, '완료')
                self.assertIsNone(verify.call_args.args[1])
                self.assertEqual(client.deploy.call_count, 1)
                app.deploy_only_var.get.return_value = False
                PublisherApp.run_registration_batch(app)
                result = app._background.call_args.args[1]()
                self.assertEqual(result[1], [])
                browser.register_and_download.assert_called_once()
                self.assertEqual(client.deploy.call_count, 2)
                self.assertTrue((root / verification.name).is_file())
