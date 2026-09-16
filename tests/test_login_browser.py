import json
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from naver_browser import NAVER_DASHBOARD, NaverAutomationError, NaverBrowser


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
        target = self.helper._site_console_url('summary', 'https://example.pages.dev')
        settings = self.helper._settings_url('https://example.pages.dev')
        self.context.route(NAVER_DASHBOARD, lambda route: route.fulfill(
            content_type='text/html', body=f'<a href="{target}">Site</a><script>fetch("/api-board/list/current")</script>'))
        visits = []
        def site_route(route):
            visits.append(route.request.url)
            if len(visits) == 1:
                route.fulfill(content_type='text/html', body="<script>setTimeout(() => location.href='/auth/callback?code=test-only', 100)</script>")
            else:
                route.fulfill(content_type='text/html', body=f'<a href="{settings}">Settings</a>')
        self.context.route('**/console/site/summary*', site_route)
        self.context.route('**/auth/callback*', lambda route: route.fulfill(
            content_type='text/html', body="<script>setTimeout(() => location.href='/console/board', 1800)</script>"))
        self.helper._open_site('https://example.pages.dev')
        self.assertEqual(len(visits), 2)

    def test_delayed_registered_link_with_different_encoding_uses_spa_navigation(self):
        self.context.route(NAVER_DASHBOARD, lambda route: route.fulfill(
            content_type='text/html', body='''<script>
            window.authMarker = 'keep';
            fetch('/api-board/list/current');
            setTimeout(() => {
              const a = document.createElement('a');
              a.href = '/console/site/summary?site=https%3a%2f%2fexample.pages.dev%2f';
              a.textContent = 'Registered site';
              a.onclick = e => {
                e.preventDefault(); history.pushState({}, '', a.href);
                const settings = document.createElement('a');
                settings.href = '/console/site/option?site=https%3A%2F%2Fexample.pages.dev%2F';
                settings.textContent = 'Settings'; document.body.appendChild(settings);
              };
              document.body.appendChild(a);
            }, 1200);
            </script>'''))
        self.helper._open_site('https://example.pages.dev')
        self.assertEqual(self.helper.page.evaluate('window.authMarker'), 'keep')

    def test_missing_site_does_not_open_guessed_url_or_login(self):
        self.helper._wait_dashboard(timeout_seconds=5, require_input=False)
        with self.assertRaisesRegex(NaverAutomationError, '관리 링크'):
            self.helper._click_registered_site('https://missing.pages.dev', timeout_seconds=0.3)
        self.assertEqual(self.helper.page.url, NAVER_DASHBOARD)
        self.assertEqual(self.login_visits, 0)

    def test_registered_site_matching_keeps_protocol_and_host_distinct(self):
        self.helper.page.goto(NAVER_DASHBOARD)
        self.helper.page.evaluate('''() => {
            document.body.innerHTML += '<a href="/console/site/summary?site=http%3A%2F%2Fexample.pages.dev">HTTP</a>';
        }''')
        self.assertIsNone(self.helper._console_link(
            self.helper._site_console_url('summary', 'https://example.pages.dev')))
        self.assertIsNone(self.helper._console_link(
            self.helper._site_console_url('summary', 'http://other.pages.dev')))

    def test_registered_row_click_handler_without_href(self):
        self.context.route(NAVER_DASHBOARD, lambda route: route.fulfill(
            content_type='text/html', body='''
            <input value="https://example.pages.dev">
            <table><tbody><tr><td><a id="site">https://example.pages.dev</a></td><td>26.09.15</td></tr></tbody></table>
            <script>
            window.authMarker = 'keep'; fetch('/api-board/list/current');
            document.getElementById('site').onclick = () => {
                history.pushState({}, '', '/console/site/summary?site=https%3A%2F%2Fexample.pages.dev');
                document.body.innerHTML = '<a href="/console/site/option?site=https%3A%2F%2Fexample.pages.dev">Settings</a>';
            };
            </script>'''))
        self.helper._open_site('https://example.pages.dev')
        self.assertEqual(self.helper.page.evaluate('window.authMarker'), 'keep')

    def test_row_match_ignores_input_and_similar_addresses(self):
        self.helper.page.goto(NAVER_DASHBOARD)
        self.helper.page.evaluate('''() => {
            document.body.innerHTML = '<input value="https://example.pages.dev">'
              + '<table><tbody><tr><td><a>https://example.pages.dev.evil.test</a></td></tr>'
              + '<tr><td><a>http://example.pages.dev</a></td></tr></tbody></table>';
        }''')
        self.assertIsNone(self.helper._registered_site_text('https://example.pages.dev'))
