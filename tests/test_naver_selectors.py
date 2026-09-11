import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from naver_browser import NaverBrowser


class NaverSelectorTests(unittest.TestCase):
    def test_summary_url_uses_direct_site_route(self):
        self.assertEqual(
            NaverBrowser._site_console_url("summary", "https://tiket160-sk.pages.dev"),
            "https://searchadvisor.naver.com/console/site/summary?site=https%3A%2F%2Ftiket160-sk.pages.dev",
        )

    def test_settings_url_uses_site_option_route(self):
        self.assertEqual(
            NaverBrowser._settings_url("https://tiket160-sk.pages.dev"),
            "https://searchadvisor.naver.com/console/site/option?site=https%3A%2F%2Ftiket160-sk.pages.dev",
        )

    def test_direct_routes_for_all_follow_up_pages(self):
        site = "https://tiket160-sk.pages.dev"
        self.assertEqual(
            NaverBrowser._site_console_url("check/robots", site),
            "https://searchadvisor.naver.com/console/site/check/robots?site=https%3A%2F%2Ftiket160-sk.pages.dev",
        )
        self.assertEqual(
            NaverBrowser._site_console_url("request/sitemap", site),
            "https://searchadvisor.naver.com/console/site/request/sitemap?site=https%3A%2F%2Ftiket160-sk.pages.dev",
        )
        self.assertEqual(
            NaverBrowser._site_console_url("request/crawl", site),
            "https://searchadvisor.naver.com/console/site/request/crawl?site=https%3A%2F%2Ftiket160-sk.pages.dev",
        )

    def test_follow_up_reports_each_completed_step(self):
        helper = NaverBrowser(Path(tempfile.gettempdir()))
        helper._open_site = lambda _site: None
        helper.set_fast_frequency = lambda _site: None
        helper.request_robots = lambda _site: None
        helper.submit_sitemap = lambda _site: None
        helper.request_pages = lambda _site, _pages: 3
        events = []

        count = helper.run_after_ownership(
            "https://example.pages.dev", ["/", "/a"], on_step=lambda *args: events.append(args)
        )

        self.assertEqual(count, 3)
        self.assertEqual(
            events,
            [
                ("frequency", "진행 중"),
                ("frequency", "완료 (빠르게)"),
                ("robots", "진행 중"),
                ("robots", "완료"),
                ("sitemap", "진행 중"),
                ("sitemap", "완료"),
                ("crawl", "진행 중"),
                ("crawl", "완료 3개"),
            ],
        )

    def test_navigation_during_locator_poll_is_retried(self):
        class NavigatingLocator:
            def count(self):
                raise PlaywrightError(
                    "Execution context was destroyed, most likely because of a navigation"
                )

        helper = NaverBrowser(Path(tempfile.gettempdir()))
        self.assertIsNone(helper._visible(NavigatingLocator()))

    def test_dynamic_ids_are_not_required(self):
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True)
            except Exception as exc:
                self.skipTest(f"Chromium을 실행할 수 없음: {exc}")
            page = browser.new_page()
            page.set_content(
                """
                <div class="row">
                  <div>사이트맵 URL 입력</div>
                  <input maxlength="2048" id="input-random-number" type="text">
                  <button type="button">확인</button>
                </div>
                """
            )
            helper = NaverBrowser(Path(tempfile.gettempdir()))
            helper.page = page
            section = helper._section("사이트맵 URL 입력")
            self.assertEqual(section.locator('input[maxlength="2048"]').count(), 1)
            self.assertEqual(section.get_by_role("button", name="확인", exact=True).count(), 1)
            browser.close()


if __name__ == "__main__":
    unittest.main()
