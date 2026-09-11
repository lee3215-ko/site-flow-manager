import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from naver_tools import NaverToolError, verify_public_deployment


class PublicVerificationTests(unittest.TestCase):
    def _site(self, root: Path) -> tuple[Path, Path]:
        site = root / "site"
        site.mkdir()
        verification = site / "naver-test.html"
        verification.write_bytes(b"naver-site-verification: naver-test.html")
        (site / "robots.txt").write_bytes(b"User-agent: *\nAllow: /\n")
        (site / "sitemap.xml").write_bytes(b"<urlset></urlset>\n")
        (site / "abc.txt").write_bytes(b"abc")
        (root / "publish-manifest.json").write_text(
            json.dumps(
                {
                    "base_url": "https://example.pages.dev",
                    "pages": ["https://example.pages.dev/"],
                    "indexnow_key": "abc",
                }
            ),
            encoding="utf-8",
        )
        return site, verification

    def test_verification_requires_exact_public_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            site, verification = self._site(Path(temp))

            def matching_response(url, **_kwargs):
                name = url.split("/")[-1].split("?")[0]
                response = Mock(status_code=200)
                response.content = (site / name).read_bytes()
                return response

            with patch("naver_tools.requests.get", side_effect=matching_response):
                result = verify_public_deployment(
                    site, verification, timeout_seconds=0
                )

            self.assertTrue(all(value == "일치" for value in result.values()))

    def test_homepage_fallback_is_rejected_even_with_http_200(self):
        with tempfile.TemporaryDirectory() as temp:
            site, verification = self._site(Path(temp))
            response = Mock(status_code=200, content=b"<html>homepage</html>")

            with patch("naver_tools.requests.get", return_value=response):
                with self.assertRaisesRegex(NaverToolError, "공개 파일 검증에 실패"):
                    verify_public_deployment(site, verification, timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
