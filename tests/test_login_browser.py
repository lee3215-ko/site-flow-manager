import json
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from naver_browser import NAVER_DASHBOARD, NaverAutomationError, NaverBrowser, NaverSessionError


class LoginBrowserTests(unittest.TestCase):
    def test_each_site_uses_fresh_document_and_session_storage_but_same_login_context(self):
        sites = ['https://first.pages.dev', 'https://second.pages.dev', 'https://third.pages.dev']
        original = self.helper.page
        self.context.add_cookies([{'name': 'test_login', 'value': 'shared', 'url': NAVER_DASHBOARD}])
        body = '<meta charset="utf-8"><script>fetch("/api-board/list/current");</script><table><tbody>'
        for site in sites:
            body += f'<tr><td><a onclick="openSite(\'{site}\')">{site}</a></td></tr>'
        body += '''</tbody></table><script>
          function openSite(site) {
            const cached = sessionStorage.getItem('selectedSite') || site;
            sessionStorage.setItem('selectedSite', cached);
            history.pushState({}, '', '/console/site/summary?site=' + encodeURIComponent(site));
            document.body.innerHTML = '<div>' + cached + '</div><div role="list"><a href="/console/site/option?site=' + encodeURIComponent(cached) + '">settings 設定</a></div>';
          }
        </script>'''
        self.context.route(NAVER_DASHBOARD, lambda route: route.fulfill(content_type='text/html', body=body))
        previous = None
        for site in sites:
            self.helper._open_site(site)
            self.assertEqual(self.helper.page.evaluate('sessionStorage.getItem("selectedSite")'), site)
            self.assertIn('test_login=shared', self.helper.page.evaluate('document.cookie'))
            self.assertFalse(original.is_closed())
            self.assertEqual(len(self.context.pages), 2)
            if previous:
                self.assertTrue(previous.is_closed())
            previous = self.helper.page

    def test_matching_url_and_menu_with_wrong_body_blocks_radio_change(self):
        site = 'https://example.pages.dev'
        self.helper.page.goto(self.helper._settings_url(site))
        self.helper.page.set_content(f'<div>https://previous.pages.dev</div><div role="list"><a href="{self.helper._settings_url(site)}">Settings</a></div><input type="radio" value="fast">')
        self.assertFalse(self.helper._site_management_ready(site))
        with self.assertRaisesRegex(NaverSessionError, '본문'):
            self.helper.set_fast_frequency(site)
        self.assertFalse(self.helper.page.locator('input').is_checked())

    def test_previous_site_sidebar_is_reloaded_once_for_each_new_site(self):
        sites = ['https://first.pages.dev', 'https://second.pages.dev', 'https://third.pages.dev']
        board = '<script>fetch("/api-board/list/current")</script>' + ''.join(
            f'<a href="{self.helper._site_console_url("summary", site)}">{site}</a>' for site in sites)
        self.context.route(NAVER_DASHBOARD, lambda route: route.fulfill(content_type='text/html', body=board))
        counts = {site: 0 for site in sites}
        from urllib.parse import parse_qs, urlsplit
        def summary(route):
            site = parse_qs(urlsplit(route.request.url).query)['site'][0]
            counts[site] += 1
            menu_site = 'https://previous.pages.dev' if counts[site] == 1 else site
            href = self.helper._settings_url(menu_site)
            route.fulfill(content_type='text/html', body=f'<meta charset="utf-8"><div role="list"><a href="{href}">settings 設定</a></div>')
        self.context.route('**/console/site/summary*', summary)
        for site in sites:
            self.helper._open_site(site)
            self.assertEqual(counts[site], 2)
            self.assertTrue(self.helper._site_management_ready(site))
            self.assertIn('/console/site/summary?', self.helper.page.url)

    def test_persistently_stale_sidebar_stops_after_one_reload(self):
        site = 'https://example.pages.dev'
        target = self.helper._site_console_url('summary', site)
        self.context.route(NAVER_DASHBOARD, lambda route: route.fulfill(content_type='text/html',
            body=f'<a href="{target}">Site</a><script>fetch("/api-board/list/current")</script>'))
        visits = []
        def summary(route):
            visits.append(route.request.url)
            route.fulfill(content_type='text/html', body=f'<div role="list"><a href="{self.helper._settings_url("https://previous.pages.dev")}">Settings</a></div>')
        self.context.route(target, summary)
        with self.assertRaisesRegex(NaverSessionError, '이전 사이트 메뉴'):
            self.helper._open_site(site)
        self.assertEqual(len(visits), 2)
        self.assertTrue((Path(self.directory.name) / 'diagnostics/navigation-latest.json').is_file())

    def test_navigation_diagnostic_redacts_auth_queries_and_inputs(self):
        self.helper.page.goto(self.helper._site_console_url('summary', 'https://example.pages.dev'))
        self.helper.page.set_content('''<input type="password" value="secret-password">
            <a href="/auth/callback?code=secret-code&state=secret-state">Login</a>
            <div role="list" style="display:none"><a href="/console/site/option?site=https%3A%2F%2Fexample.pages.dev">Settings</a></div>''')
        self.helper._save_navigation_diagnostic('https://example.pages.dev')
        raw = (Path(self.directory.name) / 'diagnostics' / 'navigation-latest.json').read_text(encoding='utf-8')
        for secret in ('secret-password', 'secret-code', 'secret-state'):
            self.assertNotIn(secret, raw)
        data = json.loads(raw)
        self.assertFalse(data['menus'][1]['visible'])
        self.assertEqual(data['menus'][1]['target']['path'], '/console/site/option')

    def test_fast_frequency_clicks_label_with_overlaid_native_input(self):
        site = 'https://example.pages.dev'
        target = self.helper._settings_url(site)
        self.context.route(target, lambda route: route.fulfill(content_type='text/html', body='''
            <meta charset="utf-8"><div class="v-radio">
            <input type="radio" value="fast" id="dynamic-fast" style="position:absolute;opacity:0;pointer-events:none">
            <label for="dynamic-fast">빠르게</label></div>
            <script>window.clicks=0;
            document.querySelector('label').onclick=e=>{
              e.preventDefault(); window.clicks++;
              setTimeout(()=>{
                const old=document.querySelector('input');
                const replacement=old.cloneNode(); replacement.checked=true;
                old.replaceWith(replacement);
              },300);
            };</script>'''))
        self.helper.set_fast_frequency(site)
        self.assertTrue(self.helper.page.locator('input').is_checked())
        self.assertEqual(self.helper.page.evaluate('window.clicks'), 1)
        self.helper.set_fast_frequency(site)
        self.assertEqual(self.helper.page.evaluate('window.clicks'), 1)

    def test_frequency_server_error_is_not_reported_as_success(self):
        site = 'https://example.pages.dev'
        self.context.route(self.helper._settings_url(site), lambda route: route.fulfill(content_type='text/html', body='''
            <meta charset="utf-8"><div class="v-radio"><input id="fast" type="radio" value="fast">
            <label for="fast">빠르게</label></div><div class="error" role="alert" hidden>
            <div class="v-alert__content">저장 실패</div></div>
            <script>document.querySelector('label').onclick=()=>{
              document.querySelector('[role=alert]').hidden=false;
            };</script>'''))
        with self.assertRaisesRegex(NaverAutomationError, '저장 실패'):
            self.helper.set_fast_frequency(site)

    def test_typed_account_is_promoted_only_after_login_and_switches(self):
        self.helper._install_account_capture()
        self.context.route('https://nid.naver.com/account-test', lambda route: route.fulfill(
            content_type='text/html', body='<input id="id"><input id="pw" type="password">'))
        for account in ['first-user', 'second-user']:
            self.helper.page.goto('https://nid.naver.com/account-test')
            self.helper.page.locator('#id').fill(account)
            self.helper.page.locator('#pw').fill('not-an-account')
            self.helper.page.wait_for_timeout(100)
            self.assertEqual(self.helper.login_account, '')
            self.assertEqual(self.helper._pending_login_account, account)
            self.helper.page.goto(NAVER_DASHBOARD)
            self.helper._wait_dashboard(timeout_seconds=5, require_input=False)
            self.assertEqual(self.helper.login_account, account)

    def test_account_capture_rejects_other_origins(self):
        self.helper.page.goto(NAVER_DASHBOARD)
        self.helper._capture_login_account({'page': self.helper.page, 'frame': self.helper.page.main_frame}, 'wrong')
        self.assertEqual(self.helper.login_account, '')
        self.assertEqual(self.helper._pending_login_account, '')
    def test_three_sites_and_retry_with_handler_only_settings_menu(self):
        self.helper._wait_dashboard = lambda **kw: NaverBrowser._wait_dashboard(self.helper, timeout_seconds=5, **kw)
        sites = [f'https://site{i}.pages.dev' for i in range(3)]
        body = '''<meta charset="utf-8"><script>
        fetch('/api-board/list/current');
        function openSite(site) {
          history.pushState({}, '', '/console/site/summary?site=' + encodeURIComponent(site));
          document.body.innerHTML = '<h1>요약</h1><a href="/console/setting/alarm">도구 설정</a><div role="list"><a id="settings"><i>settings</i> 설정</a></div>';
          document.getElementById('settings').onclick = () => {
            history.pushState({}, '', '/console/site/option?site=' + encodeURIComponent(site));
            document.body.innerHTML += '<input type="radio" value="fast" checked>';
          };
        }
        </script><table><tbody>'''
        for site in sites:
            body += f'<tr><td><a onclick="openSite(\'{site}\')">{site}</a></td></tr>'
        body += '</tbody></table>'
        self.context.route(NAVER_DASHBOARD, lambda route: route.fulfill(content_type='text/html', body=body))
        for site in [*sites, sites[1]]:
            self.helper._open_site(site)
            self.assertTrue(self.helper._site_management_ready(site))
            self.helper.set_fast_frequency(site)
            self.assertTrue(self.helper._at_site(site))
        # An already open target must not be reset to the board on retry.
        current = self.helper.page.url
        self.helper._open_site(sites[1])
        self.assertEqual(self.helper.page.url, current)

    def test_settings_menu_for_other_site_is_not_ready(self):
        self.helper.page.goto(self.helper._site_console_url('summary', 'https://other.pages.dev'))
        self.helper.page.set_content('<a>설정</a>')
        self.assertFalse(self.helper._site_management_ready('https://example.pages.dev'))

    def test_global_alarm_settings_is_never_a_site_settings_control(self):
        site = 'https://example.pages.dev'
        self.helper.page.goto(self.helper._site_console_url('summary', site))
        self.helper.page.set_content('''<a href="/console/setting/alarm">도구 설정</a>
            <div role="list"><a href="/console/setting/alarm">설정</a>
            <a href="/console/site/option?site=https%3A%2F%2Fother.pages.dev">설정</a></div>''')
        self.assertIsNone(self.helper._settings_control())
        self.assertFalse(self.helper._site_management_ready(site))

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
