import json
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from naver_browser import NAVER_DASHBOARD, NaverBrowser


class LoginBrowserTests(unittest.TestCase):
    def test_internal_link_preserves_document_authentication_state(self):
        target = self.helper._site_console_url('summary', 'https://example.pages.dev')
        self.helper.page.goto(NAVER_DASHBOARD)
        self.helper.page.evaluate('window.authMarker = "keep"')
        self.helper.page.evaluate('''target => {
            const a = document.createElement('a'); a.href = target; a.textContent = 'Site';
            a.onclick = e => {e.preventDefault(); history.pushState({}, '', target);};
            document.body.appendChild(a);
        }''', target)
        self.helper._goto_tolerating_login_redirect(target)
        self.assertEqual(self.helper.page.evaluate('window.authMarker'), 'keep')
        self.assertEqual(self.helper.page.url, target)

    def test_rotating_cookies_do_not_reload_dashboard_before_delayed_response(self):
        visits = []
        def dashboard(route):
            visits.append(route.request.url)
            route.fulfill(content_type='text/html', body="""<script>
                setInterval(() => document.cookie='NID_SES='+Date.now()+'; domain=.naver.com; path=/; Secure', 150);
                setTimeout(() => fetch('/api-board/list/current'), 2200);
                </script>""")
        self.context.route(NAVER_DASHBOARD, dashboard)
        self.helper._wait_dashboard(timeout_seconds=7, require_input=False)
        self.assertEqual(len(visits), 1)
        self.assertIsNotNone(self.helper.board_payload)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.chromium.launch(headless=True)
        self.addCleanup(self.browser.close)
        self.context = self.browser.new_context()
        self.context.route('**/*', self.route)
        self.helper = NaverBrowser(Path(self.directory.name))
        self.helper.context = self.context
        self.helper.page = self.context.new_page()
        self.context.on('response', self.helper._capture_board_response)
        self.login_visits = 0

    def route(self, route):
        url = route.request.url
        if '/api-board/list/' in url:
            account = 'second' if 'NID_AUT=second' in route.request.headers.get('cookie', '') else 'first'
            route.fulfill(content_type='application/json', body=json.dumps({
                'code': 0, 'items': [account], 'meta': {'max': 100},
            }))
        elif url == NAVER_DASHBOARD:
            route.fulfill(content_type='text/html', body="""
                <script>fetch('/api-board/list/current')</script>
            """)
        elif 'nid.naver.com' in url:
            self.login_visits += 1
            route.fulfill(content_type='text/html', body="""
                <script>setTimeout(() => {
                    document.cookie='NID_AUT=second; domain=.naver.com; path=/; Secure';
                    location.href='https://www.naver.com/';
                }, 600)</script>
            """)
        else:
            route.fulfill(content_type='text/html', body='Naver home')

    def test_login_home_redirect_and_session_saved(self):
        self.helper.page.goto('https://nid.naver.com/login')
        self.helper._wait_dashboard(timeout_seconds=5, require_input=False)
        self.assertEqual(self.helper.board_payload['items'], ['second'])
        self.assertEqual(self.login_visits, 1)
        state = json.loads(self.helper.state_file.read_text())
        self.assertTrue(any(c['name'] == 'NID_AUT' and c['value'] == 'second'
                            for c in state['cookies']))

    def test_new_login_tab_replaces_closed_original_without_closing_browser(self):
        original = self.helper.page
        login = self.context.new_page()
        login.goto('https://nid.naver.com/login')
        original.close()
        self.helper._wait_dashboard(timeout_seconds=5, require_input=False)
        self.assertEqual(self.helper.page, login)
        self.assertTrue(self.browser.is_connected())
        self.assertEqual(len(self.context.pages), 1)

    def test_account_switch_ignores_previous_account_payload(self):
        self.helper._wait_dashboard(timeout_seconds=5, require_input=False)
        self.assertEqual(self.helper.board_payload['items'], ['first'])
        login = self.context.new_page()
        login.goto('https://nid.naver.com/login')
        self.helper._wait_dashboard(timeout_seconds=5, require_input=False)
        self.assertEqual(self.helper.page, login)
        self.assertEqual(self.helper.board_payload['items'], ['second'])

    def test_other_tab_response_is_ignored(self):
        other = self.context.new_page()
        other.goto(NAVER_DASHBOARD)
        other.wait_for_timeout(200)
        self.assertIsNone(self.helper.board_payload)

    def test_callback_can_finish_without_being_interrupted(self):
        self.context.route('**/auth/callback*', lambda route: route.fulfill(
            content_type='text/html', body="""<script>setTimeout(() => {
                document.cookie='callback_done=yes; path=/; Secure';
                location.href='/console/board';
            }, 1200)</script>"""))
        self.helper.page.goto('https://searchadvisor.naver.com/auth/callback?code=test-only')
        self.helper._wait_dashboard(timeout_seconds=5, require_input=False)
        self.assertTrue(any(c['name'] == 'callback_done' for c in self.context.cookies()))

    def test_site_auth_redirect_recovers_after_callback(self):
        visits = []
        def site_route(route):
            visits.append(route.request.url)
            if len(visits) == 1:
                route.fulfill(content_type='text/html', body="<script>setTimeout(() => location.href='/auth/callback?code=test-only', 100)</script>")
            else:
                route.fulfill(content_type='text/html', body='<a href="/console/site/option?site=test">Settings</a>')
        self.context.route('**/console/site/summary*', site_route)
        self.context.route('**/auth/callback*', lambda route: route.fulfill(
            content_type='text/html', body="<script>setTimeout(() => location.href='/console/board', 1800)</script>"))
        self.helper._open_site('https://example.pages.dev')
        self.assertEqual(len(visits), 2)
