import unittest
from unittest.mock import Mock

from cloudflare_client import CloudflareAccountPool, CloudflareClient, CloudflareError, ProjectLimitError


class CloudflareClientTests(unittest.TestCase):
    def test_actual_limit_response_retries_same_project_on_next_account(self):
        pool = CloudflareAccountPool([{'account_id': 'a'*32}, {'account_id': 'b'*32}])
        pool.projects = [[], []]
        pool.clients[0].project_url = Mock(side_effect=ProjectLimitError('full'))
        pool.clients[1].project_url = Mock(return_value='https://same.pages.dev')
        client, account, url = pool.project_for('same')
        self.assertEqual(account['account_id'], 'b'*32)
        pool.clients[1].project_url.assert_called_once_with('same')
        self.assertIn(0, pool.full_accounts)

    def test_other_api_error_does_not_rotate_accounts(self):
        pool = CloudflareAccountPool([{'account_id': 'a'*32}, {'account_id': 'b'*32}])
        pool.projects = [[], []]
        pool.clients[0].project_url = Mock(side_effect=CloudflareError('rate limited'))
        pool.clients[1].project_url = Mock()
        with self.assertRaises(CloudflareError):
            pool.project_for('same')
        pool.clients[1].project_url.assert_not_called()

    @staticmethod
    def _response(payload, status_code=200):
        response = Mock()
        response.ok = status_code < 400
        response.status_code = status_code
        response.json.return_value = payload
        return response

    def test_verify_checks_pages_access_directly(self):
        client = CloudflareClient("a" * 32, "Bearer test-token")
        client.list_projects = Mock(return_value=[{"name": "one"}])
        self.assertEqual(client.verify(), [{"name": "one"}])
        self.assertEqual(client.api_token, "test-token")
        client.list_projects.assert_called_once_with()

    def test_invalid_account_id_is_explained(self):
        client = CloudflareClient("short", "test-token")
        with self.assertRaisesRegex(CloudflareError, "32자리"):
            client.verify()

    def test_pages_project_limit_is_explained(self):
        response = Mock()
        response.ok = False
        response.status_code = 400
        response.json.return_value = {
            "success": False,
            "errors": [{"message": "You have reached the limit of projects you can have on your account."}],
        }
        client = CloudflareClient("a" * 32, "test-token")
        client.session.request = Mock(return_value=response)
        with self.assertRaisesRegex(CloudflareError, "프로젝트 한도 100개"):
            client.ensure_project("new-project")

    def test_project_url_uses_cloudflare_assigned_subdomain(self):
        client = CloudflareClient("a" * 32, "test-token")
        client.ensure_project = Mock(
            return_value={"name": "tiket168", "subdomain": "tiket168-7rw.pages.dev"}
        )

        self.assertEqual(
            client.project_url("tiket168"), "https://tiket168-7rw.pages.dev"
        )

    def test_list_projects_reads_every_page(self):
        client = CloudflareClient("a" * 32, "test-token")
        client.session.request = Mock(side_effect=[
            self._response({
                "success": True,
                "result": [{"name": "one"}, {"name": "two"}],
                "result_info": {"page": 1, "total_pages": 2, "total_count": 3},
            }),
            self._response({
                "success": True,
                "result": [{"name": "three"}],
                "result_info": {"page": 2, "total_pages": 2, "total_count": 3},
            }),
        ])

        self.assertEqual([p["name"] for p in client.list_projects()], ["one", "two", "three"])
        self.assertEqual(client.session.request.call_count, 2)
        client.list_projects()
        self.assertEqual(client.session.request.call_count, 2)

    def test_account_pool_moves_to_next_account_at_limit(self):
        accounts = [
            {"name": "첫 계정", "account_id": "a" * 32, "api_token": "one"},
            {"name": "둘째 계정", "account_id": "b" * 32, "api_token": "two"},
        ]
        pool = CloudflareAccountPool(accounts)
        pool.projects = [
            [{"name": f"project-{index}"} for index in range(100)],
            [{"name": "another"}],
        ]

        client, account = pool.client_for("new-project")

        self.assertEqual(account["name"], "둘째 계정")
        self.assertEqual(client.account_id, "b" * 32)

    def test_account_pool_keeps_existing_project_on_full_account(self):
        accounts = [
            {"name": "첫 계정", "account_id": "a" * 32, "api_token": "one"},
            {"name": "둘째 계정", "account_id": "b" * 32, "api_token": "two"},
        ]
        pool = CloudflareAccountPool(accounts)
        pool.projects = [
            [{"name": "existing"}] + [{"name": f"full-{index}"} for index in range(99)],
            [],
        ]

        client, account = pool.client_for("existing")

        self.assertEqual(account["name"], "첫 계정")
        self.assertEqual(client.account_id, "a" * 32)

    def test_account_pool_switches_immediately_after_recording_100th_project(self):
        accounts = [
            {"name": "첫 계정", "account_id": "a" * 32, "api_token": "one"},
            {"name": "둘째 계정", "account_id": "b" * 32, "api_token": "two"},
        ]
        pool = CloudflareAccountPool(accounts)
        pool.projects = [
            [{"name": f"project-{index}"} for index in range(99)],
            [],
        ]

        _, first_account = pool.client_for("project-100")
        pool.record_project(first_account["account_id"], "project-100")
        _, next_account = pool.client_for("project-101")

        self.assertEqual(first_account["name"], "첫 계정")
        self.assertEqual(next_account["name"], "둘째 계정")


if __name__ == "__main__":
    unittest.main()
