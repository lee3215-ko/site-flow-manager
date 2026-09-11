import unittest

from secret_store import protect, unprotect


class SecretStoreTests(unittest.TestCase):
    def test_dpapi_round_trip(self):
        payload = protect("test-token-123")
        self.assertNotIn(b"test-token-123", payload)
        self.assertEqual(unprotect(payload), "test-token-123")


if __name__ == "__main__":
    unittest.main()
