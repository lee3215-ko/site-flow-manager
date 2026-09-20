from __future__ import annotations

import os
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from playwright.sync_api import (
    Error as PlaywrightError,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


NAVER_DASHBOARD = "https://searchadvisor.naver.com/console/board"


class NaverAutomationError(RuntimeError):
    pass

class NaverSessionError(NaverAutomationError):
    """Authentication or browser failure: stop the batch, not just one site."""

def session_failure(error):
    return isinstance(error, NaverSessionError) or (
        isinstance(error, PlaywrightError) and 'closed' in str(error).lower()
    )


class NaverBrowser:
    def __init__(
        self,
        profile_dir: Path,
        status: Callable[[str], None] | None = None,
        headless: bool = False,
    ) -> None:
        self.profile_dir = profile_dir
        self.state_file = profile_dir / "storage-state.json"
        self.status = status or (lambda _message: None)
        self.headless = headless
        self.playwright = None
        self.browser_process: subprocess.Popen | None = None
        self.runtime_profile: Path | None = None
        self.browser = None
        self.context = None
        self.page: Page | None = None
        self.cancel_event = None
        self.board_payload = None
        self.login_account = ''
        self._pending_login_account = ''
        self._account_input_page = None
        self._saving_state = False
        self._work_page = None

    def _chromium_path(self) -> Path:
        app_dir = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent
        )
        candidates = [app_dir / "browser" / "chrome.exe"]
        if self.playwright:
            candidates.append(Path(self.playwright.chromium.executable_path))
        candidates.extend([
            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ])
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise NaverAutomationError(
            "동봉된 Chromium 실행 파일을 찾지 못했습니다. ZIP의 browser 폴더가 EXE 옆에 있는지 확인해 주세요."
        )

    @staticmethod
    def _available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _launch_browser(self) -> str:
        chromium_path = self._chromium_path()
        port = self._available_port()
        run_root = self.profile_dir / "runs"
        run_root.mkdir(parents=True, exist_ok=True)
        self.runtime_profile = Path(tempfile.mkdtemp(prefix="run-", dir=run_root))
        arguments = [
            str(chromium_path),
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={self.runtime_profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            "--disable-background-mode",
            "--disable-blink-features=AutomationControlled",
        ]
        if self.headless:
            arguments.append("--headless=new")
        arguments.append("about:blank")
        self.browser_process = subprocess.Popen(
            arguments,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        endpoint = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.browser_process.poll() is not None:
                raise NaverAutomationError(
                    "Chromium이 연결 준비 전에 종료되었습니다. "
                    f"종료 코드: {self.browser_process.returncode}"
                )
            try:
                with urllib.request.urlopen(f"{endpoint}/json/version", timeout=0.5) as response:
                    if response.status == 200:
                        return endpoint
            except OSError:
                time.sleep(0.25)
        raise NaverAutomationError("Chromium 자동화 연결 준비 시간이 초과되었습니다.")

    def __enter__(self):
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.playwright = sync_playwright().start()
        try:
            endpoint = self._launch_browser()
            self.browser = self.playwright.chromium.connect_over_cdp(endpoint)
            context_options = {
                "accept_downloads": True,
                "viewport": {"width": 1440, "height": 920},
            }
            if self.state_file.is_file():
                context_options["storage_state"] = str(self.state_file)
            try:
                self.context = self.browser.new_context(**context_options)
            except Exception:
                context_options.pop("storage_state", None)
                self.context = self.browser.new_context(**context_options)
        except Exception as exc:
            self._close_browser(save_state=False)
            self.playwright.stop()
            raise NaverAutomationError(
                "자동화 Chromium을 열 수 없습니다.\n"
                f"실제 원인: {exc}"
            ) from exc
        self.context.on("response", self._capture_board_response)
        self._install_account_capture()
        self.page = self.context.new_page()
        for browser_context in self.browser.contexts:
            if browser_context == self.context:
                continue
            for blank_page in browser_context.pages:
                try:
                    blank_page.close()
                except PlaywrightError:
                    pass
        return self

    def _capture_login_account(self, source, value) -> None:
        if self._saving_state:
            return
        frame = source.get('frame')
        page = source.get('page')
        if not frame or not page or frame != page.main_frame or urlsplit(frame.url).hostname != 'nid.naver.com':
            return
        if not isinstance(value, str) or len(value) > 254:
            return
        value = value.strip()
        if any(char.isspace() for char in value):
            return
        self.login_account = ''
        self._pending_login_account = value
        self._account_input_page = page

    def _install_account_capture(self) -> None:
        self.context.expose_binding('siteflowAccountInput', self._capture_login_account)
        self.context.add_init_script('''(() => {
          if (location.hostname !== 'nid.naver.com' || window !== window.top) return;
          const report = () => {
            const field = document.querySelector('input#id:not([type="password"])');
            window.siteflowAccountInput(field ? field.value : '').catch(() => {});
          };
          report();
          document.addEventListener('DOMContentLoaded', report, {once: true});
          for (const event of ['input', 'change']) {
            document.addEventListener(event, e => {
              if (e.target.matches('input#id:not([type="password"])')) report();
            }, true);
          }
          document.addEventListener('submit', report, true);
        })();''')

    def _save_authenticated_state(self) -> None:
        # Playwright may create temporary origin pages while reading local storage.
        # Their empty login inputs must not replace the user's captured account.
        self._saving_state = True
        try:
            self.context.storage_state(path=str(self.state_file))
        finally:
            self._saving_state = False
        if self._account_input_page == self.page:
            self.login_account = self._pending_login_account

    def _close_browser(self, save_state: bool) -> None:
        if self.context:
            if save_state:
                try:
                    self._save_authenticated_state()
                except Exception as state_exc:
                    self.status(f"네이버 로그인 상태 저장 실패: {state_exc}")
            try:
                self.context.close()
            except Exception:
                pass
        if self.browser:
            try:
                session = self.browser.new_browser_cdp_session()
                session.send("Browser.close")
            except Exception:
                pass
        if self.browser_process:
            try:
                self.browser_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.browser_process.terminate()
                try:
                    self.browser_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.browser_process.kill()
        if self.runtime_profile:
            shutil.rmtree(self.runtime_profile, ignore_errors=True)

    def __exit__(self, exc_type, exc, traceback):
        self._close_browser(save_state=True)
        if self.playwright:
            self.playwright.stop()

    def _visible(self, locator: Locator) -> Locator | None:
        try:
            count = locator.count()
            for index in range(count):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    return candidate
        except PlaywrightError:
            # Login and redirects replace the document while this polling loop is running.
            return None
        return None

    def _click_text(self, *labels: str, timeout_ms: int = 15_000) -> None:
        assert self.page
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            for label in labels:
                candidate = self._visible(self.page.get_by_text(label, exact=True))
                if candidate:
                    candidate.click()
                    return
            self.page.wait_for_timeout(250)
        raise NaverAutomationError(f"네이버 화면에서 메뉴를 찾지 못했습니다: {' / '.join(labels)}")

    def _wait_visible(self, css: str, timeout_ms: int = 15_000) -> Locator:
        assert self.page
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            candidate = self._visible(self.page.locator(css))
            if candidate:
                return candidate
            self.page.wait_for_timeout(250)
        raise NaverAutomationError(f"네이버 화면 요소를 찾지 못했습니다: {css}")

    def _goto_tolerating_login_redirect(self, url: str) -> None:
        assert self.page
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise NaverSessionError('네이버 작업을 중지했습니다.')
        if self.page.is_closed():
            raise NaverSessionError('네이버 창이 닫혀 일괄 작업을 중단합니다.')
        current, target = urlsplit(self.page.url), urlsplit(url)
        if current.hostname == target.hostname == 'searchadvisor.naver.com' and not self._is_login_page(self.page):
            if current.path == target.path and current.query == target.query:
                return
            link = self._console_link(url)
            if not link and self._at_site(parse_qs(target.query).get('site', [''])[0]):
                if target.path == '/console/site/option':
                    link = self._settings_control()
            if not link:
                group = '검증' if '/check/' in target.path else ('요청' if '/request/' in target.path else None)
                if group:
                    button = self._visible(self.page.get_by_role('button').filter(has_text=group))
                    if button and button.get_attribute('aria-expanded') != 'true':
                        button.click()
                        self.page.wait_for_timeout(300)
                        link = self._console_link(url)
            if link:
                self.status(f'네이버 내부 링크로 이동: {target.path}')
                link.click()
                return
        self.status(f'네이버 페이지 열기: {target.hostname}{target.path}')
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightError as exc:
            message = str(exc).lower()
            if "interrupted by another navigation" not in message and "context was destroyed" not in message:
                raise
            # Naver can replace a dashboard navigation with an OAuth login redirect.
            self.page.wait_for_timeout(1_000)

    def _capture_board_response(self, response) -> None:
        if "/api-board/list/" not in response.url:
            return
        try:
            if response.frame.page != self.page:
                return
            payload = response.json()
            if payload.get("code") == 0 and isinstance(payload.get("items"), list):
                self.board_payload = payload
        except (PlaywrightError, ValueError, AttributeError):
            pass

    @staticmethod
    def _is_login_page(page: Page) -> bool:
        url = urlsplit(page.url)
        return url.hostname == "nid.naver.com" or (
            url.hostname == "searchadvisor.naver.com" and url.path.startswith('/auth/')
        )

    def select_live_page(self) -> Page:
        pages = [page for page in self.context.pages if not page.is_closed()]
        login_pages = [page for page in pages if self._is_login_page(page)]
        naver_pages = [page for page in pages if (
            urlsplit(page.url).hostname == "naver.com"
            or (urlsplit(page.url).hostname or "").endswith(".naver.com")
        )]
        if login_pages:
            selected = login_pages[-1]
        elif self.page in naver_pages:
            selected = self.page
        elif naver_pages:
            selected = naver_pages[-1]
        elif self.page in pages:
            selected = self.page
        elif pages:
            selected = pages[-1]
        else:
            selected = self.context.new_page()
        if selected != self.page:
            self.board_payload = None
        self.page = selected
        return selected

    def _login_session(self) -> tuple:
        # Compare session changes in memory only; never log authentication cookies.
        return tuple(sorted(
            (cookie["name"], cookie["value"])
            for cookie in self.context.cookies("https://naver.com")
            if cookie["name"] in {"NID_AUT", "NID_SES"}
        ))

    def _wait_dashboard(self, timeout_seconds: int = 600, require_input: bool = True):
        self.select_live_page()
        # Keep the current dashboard response when no document navigation is needed.
        current = urlsplit(self.page.url)
        if current.hostname != 'searchadvisor.naver.com' or current.path.rstrip('/') != '/console/board':
            self.board_payload = None
        waiting_login = self._is_login_page(self.page)
        if not waiting_login:
            self._goto_tolerating_login_redirect(NAVER_DASHBOARD)
        deadline = time.monotonic() + timeout_seconds
        login_notified = False
        callback_started = None
        navigated_locations = set()
        while time.monotonic() < deadline:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise NaverSessionError("네이버 로그인 대기를 중지했습니다.")
            if not any(not page.is_closed() for page in self.context.pages):
                raise NaverSessionError("네이버 창이 모두 닫혔습니다. 로그인 창을 다시 열어 주세요.")
            previous_page = self.page
            self.select_live_page()
            on_login = self._is_login_page(self.page)
            url = urlsplit(self.page.url)
            on_dashboard = url.hostname == 'searchadvisor.naver.com' and url.path.rstrip('/') == '/console/board'
            if url.hostname == 'searchadvisor.naver.com' and url.path.startswith('/auth/'):
                if callback_started is None:
                    callback_started = time.monotonic()
                    self.status('네이버 인증 완료를 기다립니다. 인증 중에는 페이지를 이동하지 않습니다.')
                elif time.monotonic() - callback_started > 60:
                    raise NaverSessionError('네이버 인증 콜백이 60초 이상 완료되지 않았습니다. 열린 창에서 서치어드바이저 홈으로 이동해 다시 로그인해 주세요. 소유확인 실패로 판정한 것은 아닙니다.')
            else:
                callback_started = None
            if on_login:
                self.board_payload = None
            if not on_login and not on_dashboard and (waiting_login or previous_page != self.page):
                location = (self.page, url.hostname, url.path)
                if location in navigated_locations or len(navigated_locations) >= 2:
                    raise NaverSessionError('네이버 인증 후 같은 화면으로 반복 이동했습니다. 자동 이동을 중단했습니다. 열린 창에서 로그인을 완료한 뒤 다시 시도해 주세요.')
                navigated_locations.add(location)
                self.board_payload = None
                self.status("새 네이버 로그인 상태로 서치어드바이저를 확인합니다.")
                self._goto_tolerating_login_redirect(NAVER_DASHBOARD)
            waiting_login = self._is_login_page(self.page)
            current = urlsplit(self.page.url)
            dashboard_ready = current.hostname == 'searchadvisor.naver.com' and current.path.rstrip('/') == '/console/board'
            if dashboard_ready and not require_input and self.board_payload is not None:
                self._save_authenticated_state()
                return None
            field = self._visible(self.page.locator('input[maxlength="253"][type="text"]')) if dashboard_ready else None
            if require_input and field:
                self._save_authenticated_state()
                return field
            if not login_notified:
                self.status("네이버 로그인이 필요하면 열린 Chromium에서 로그인해 주세요. 최대 10분간 기다립니다.")
                login_notified = True
            try:
                self.page.wait_for_timeout(500)
            except PlaywrightError:
                if not self.page.is_closed():
                    raise
        raise NaverSessionError("네이버 로그인 또는 웹마스터 도구 화면 대기 시간이 초과되었습니다.")

    def register_and_download(self, site_url: str, download_dir: Path) -> Path:
        assert self.page
        self.status(f"네이버에 사이트 주소 등록 중: {site_url}")
        field = self._wait_dashboard()
        field.fill(site_url)
        submit = self._visible(self.page.locator(
            '//button[.//i[contains(normalize-space(.), "exit_to_app")]]'
        ))
        if submit:
            submit.click()
        else:
            field.press("Enter")

        try:
            self.page.get_by_text("HTML 파일 업로드", exact=True).wait_for(
                state="visible", timeout=30_000
            )
        except PlaywrightTimeoutError as exc:
            body = self.page.locator("body").inner_text()[:800]
            raise NaverAutomationError(
                f"사이트 등록 후 소유확인 화면으로 이동하지 못했습니다.\n{body}"
            ) from exc

        self._wait_visible('input[type="radio"][value="file"]').check(force=True)
        link = self._visible(self.page.locator("a.api_link").filter(has_text="HTML 확인 파일"))
        if not link:
            raise NaverAutomationError("HTML 확인 파일 다운로드 링크를 찾지 못했습니다.")

        download_dir.mkdir(parents=True, exist_ok=True)
        self.status(f"네이버 소유확인 HTML 다운로드 중: {site_url}")
        try:
            with self.page.expect_download(timeout=30_000) as download_info:
                link.click()
            download = download_info.value
        except PlaywrightTimeoutError as exc:
            raise NaverAutomationError("네이버 소유확인 HTML 다운로드가 시작되지 않았습니다.") from exc
        filename = download.suggested_filename
        if not re.fullmatch(r"naver[a-zA-Z0-9_-]+\.html", filename):
            filename = f"naver-verification-{int(time.time())}.html"
        target = download_dir / filename
        download.save_as(target)
        return target

    def wait_for_login(self) -> None:
        self.status("열린 Chromium에서 네이버 로그인을 완료해 주세요.")
        self._wait_dashboard(require_input=False)
        self.status("네이버 로그인을 확인했습니다. 로그인 상태를 저장합니다.")

    def count_registered_sites(self) -> tuple[int, int]:
        assert self.page
        self._wait_dashboard(timeout_seconds=120, require_input=False)
        deadline = time.monotonic() + 10
        while self.board_payload is None and time.monotonic() < deadline:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise NaverAutomationError("네이버 사이트 수 확인을 중지했습니다.")
            self.page.wait_for_timeout(250)
        if self.board_payload is None:
            raise NaverAutomationError("로그인 화면은 확인했지만 사이트 목록 응답을 받지 못했습니다. 네이버 등록 수 확인을 다시 눌러 주세요.")
        payload = self.board_payload
        return len(payload["items"]), int((payload.get("meta") or {}).get("max") or 100)


    @staticmethod
    def _site_key(url: str) -> tuple:
        parsed = urlsplit(url)
        return parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip('/'), parsed.query

    def _console_link(self, target_url: str) -> Locator | None:
        """Use rendered links; query escaping and a root trailing slash may differ."""
        target = urlsplit(target_url)
        expected = parse_qs(target.query)
        try:
            links = self.page.locator('a[href]')
            hrefs = links.evaluate_all('(links) => links.map(a => a.href)')
            for index, href in enumerate(hrefs):
                actual = urlsplit(urljoin(self.page.url, href))
                if (actual.scheme, actual.netloc, actual.path) != (target.scheme, target.netloc, target.path):
                    continue
                query = parse_qs(actual.query)
                if 'site' in expected:
                    if len(query.get('site', [])) != 1 or self._site_key(query['site'][0]) != self._site_key(expected['site'][0]):
                        continue
                elif query != expected:
                    continue
                link = links.nth(index)
                if link.is_visible():
                    return link
        except PlaywrightError:
            return None
        return None

    def _click_registered_site(self, site_url: str, timeout_seconds: float = 15) -> None:
        target = self._site_console_url('summary', site_url)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise NaverSessionError('네이버 작업을 중지했습니다.')
            if self.page.is_closed() or self._is_login_page(self.page):
                raise NaverSessionError('사이트 목록 확인 중 인증 상태가 변경되었습니다. 열린 창을 확인해 주세요.')
            link = self._console_link(target)
            if not link:
                link = self._registered_site_text(site_url)
            if link:
                self.status(f'네이버 등록 목록의 사이트 링크 클릭: {site_url}')
                link.click()
                return
            self.page.wait_for_timeout(250)
        raise NaverAutomationError(
            f'로그인은 확인했지만 현재 목록에서 {site_url}의 관리 링크를 찾지 못했습니다. '
            '현재 계정에 등록된 주소(http/https 포함), 소유확인 상태와 목록의 검색·페이지를 확인해 주세요. '
            '주소를 추측해 열거나 재로그인을 요청하지 않았습니다.'
        )

    def _registered_site_text(self, site_url: str) -> Locator | None:
        # Some dashboard entries use click handlers without a navigable href.
        # Restrict matching to list rows, never the URL registration/search input.
        expected = self._site_key(site_url)
        try:
            entries = self.page.locator('tbody tr, [role="row"], [role="listitem"]')
            for index in range(entries.count()):
                row = entries.nth(index)
                if not row.is_visible():
                    continue
                for text in row.inner_text().split():
                    text = text.strip()
                    if not text.startswith(('http://', 'https://')) or self._site_key(text) != expected:
                        continue
                    match = self._visible(row.get_by_text(text, exact=True))
                    if match:
                        return match
        except PlaywrightError:
            return None
        return None

    def _open_site(self, site_url: str) -> None:
        assert self.page
        if self._site_management_ready(site_url):
            self.status(f'이미 열린 사이트 관리 화면 확인: {site_url}')
            return
        previous_work = self._work_page
        self.page = self.context.new_page()
        self._work_page = self.page
        self.board_payload = None
        self.status(f'사이트별 새 작업 탭 준비 (로그인 세션 유지): {site_url}')
        self._goto_tolerating_login_redirect(NAVER_DASHBOARD)
        if previous_work and not previous_work.is_closed():
            previous_work.close()
        self._wait_dashboard(require_input=False)
        self._click_registered_site(site_url)
        deadline = time.monotonic() + 60
        recovered = False
        menu_reloaded = False
        stale_since = None
        while time.monotonic() < deadline:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise NaverAutomationError('네이버 사이트 관리 화면 대기를 중지했습니다.')
            self.select_live_page()
            if self._is_login_page(self.page):
                if recovered:
                    raise NaverSessionError('로그인 후에도 인증 화면으로 돌아갑니다. 현재 로그인 계정과 서치어드바이저 접근 상태를 확인해 주세요.')
                self.status('사이트 관리 화면 진입 중 재인증이 필요합니다. 로그인 완료 후 다시 진입합니다.')
                self._wait_dashboard(require_input=False)
                self._click_registered_site(site_url)
                recovered = True
                deadline = time.monotonic() + 60
                continue
            if self._site_management_ready(site_url):
                self.status(f'사이트 관리 화면 확인: {site_url}')
                return
            stale_site = self._stale_menu_site(site_url)
            if stale_site:
                if stale_since is None:
                    stale_since = time.monotonic()
                elif time.monotonic() - stale_since >= 1:
                    if menu_reloaded:
                        self._save_navigation_diagnostic(site_url)
                        raise NaverSessionError('새로고침 후에도 이전 사이트 메뉴가 남아 있습니다. 다른 사이트에 요청하지 않고 일괄 작업을 중단합니다.')
                    self.status(f'이전 사이트 메뉴 감지: {stale_site} → {site_url}. 현재 화면을 한 번 새로고침합니다.')
                    self.page.reload(wait_until='domcontentloaded', timeout=60_000)
                    menu_reloaded = True
                    stale_since = None
                    deadline = time.monotonic() + 60
                    continue
            else:
                stale_since = None
            self.page.wait_for_timeout(250)
        location = urlsplit(self.page.url)
        self._save_navigation_diagnostic(site_url)
        raise NaverAutomationError(
            f'{site_url}의 사이트 관리 화면 준비를 확인하지 못했습니다. '
            f'요청 사이트 주소 일치: {self._at_site(site_url)}, 설정 메뉴 표시: {bool(self._settings_control())}. '
            f'현재 위치: {location.hostname}{location.path}'
        )

    def _stale_menu_site(self, site_url: str) -> str | None:
        if not self._at_site(site_url):
            return None
        try:
            links = self.page.locator('[role="list"] a[href]')
            for index in range(links.count()):
                link = links.nth(index)
                if not link.is_visible():
                    continue
                target = urlsplit(urljoin(self.page.url, link.get_attribute('href') or ''))
                if target.hostname != 'searchadvisor.naver.com' or target.path != '/console/site/option':
                    continue
                linked_site = parse_qs(target.query).get('site', [''])[0]
                if linked_site and self._site_key(linked_site) != self._site_key(site_url):
                    return linked_site
        except PlaywrightError:
            return None
        return None

    def _save_navigation_diagnostic(self, site_url: str) -> None:
        try:
            data = self.page.locator('body').evaluate('''body => {
                const safeUrl = value => {
                    try {
                        const u = new URL(value, location.href);
                        return {host: u.hostname, path: u.pathname, site: u.searchParams.get('site')};
                    } catch { return null; }
                };
                return {
                    location: safeUrl(location.href),
                    viewport: {width: innerWidth, height: innerHeight},
                    menus: Array.from(body.querySelectorAll('a, [role="listitem"], [role="button"]')).slice(0, 250).map(el => {
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return {text: (el.textContent || '').trim().slice(0, 120),
                            target: el.hasAttribute('href') ? safeUrl(el.getAttribute('href')) : null,
                            tag: el.tagName, role: el.getAttribute('role'),
                            inList: !!el.closest('[role="list"]'),
                            visible: !!(rect.width && rect.height && style.visibility !== 'hidden' && style.display !== 'none'),
                            classes: el.className.toString().slice(0, 180)};
                    })
                };
            }''')
            data['requested_site'] = site_url
            data['conflicting_site_heading'] = self._conflicting_site_heading(site_url)
            data['captured_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
            path = self.profile_dir / 'diagnostics' / 'navigation-latest.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            self.status(f'메뉴 진단 기록 저장: {path}')
        except Exception:
            self.status('메뉴 진단 기록을 저장하지 못했습니다. 열린 화면을 유지해 주세요.')

    def _at_site(self, site_url: str) -> bool:
        if not site_url or not self.page or self.page.is_closed():
            return False
        location = urlsplit(self.page.url)
        selected = parse_qs(location.query).get('site', [''])[0]
        return (location.hostname == 'searchadvisor.naver.com'
                and location.path.startswith('/console/site/')
                and self._site_key(selected) == self._site_key(site_url))

    def _settings_control(self) -> Locator | None:
        # Only the site sidebar, never the global "tool settings" navigation.
        controls = self.page.locator('[role="list"] a, [role="list"] [role="listitem"], [role="list"] [role="button"]')
        try:
            for index in range(controls.count()):
                control = controls.nth(index)
                if not control.is_visible():
                    continue
                label = ' '.join(control.inner_text().split())
                if not re.fullmatch(r'(?:settings\s*)?설정', label):
                    continue
                href = control.get_attribute('href')
                if href is None and control.locator('a').count():
                    continue
                if href and href != '#':
                    target = urlsplit(urljoin(self.page.url, href))
                    site = parse_qs(target.query).get('site', [''])[0]
                    if (target.hostname != 'searchadvisor.naver.com'
                            or target.path != '/console/site/option'
                            or not self._at_site(site)):
                        continue
                return control
        except PlaywrightError:
            return None
        return None

    def _site_management_ready(self, site_url: str) -> bool:
        return self._at_site(site_url) and not self._stale_menu_site(site_url) and not self._conflicting_site_heading(site_url) and bool(
            self._console_link(self._settings_url(site_url)) or self._settings_control()
        )

    def _conflicting_site_heading(self, site_url: str) -> str | None:
        # The selected-site banner is plain URL text, not a sidebar/table link.
        candidates = self.page.get_by_text(re.compile(r'^https?://[^\s]+$'))
        for index in range(candidates.count()):
            item = candidates.nth(index)
            if not item.is_visible() or item.evaluate('(el) => !!el.closest("a, table, [role=list], [role=row], input, textarea")'):
                continue
            value = item.inner_text().strip()
            parsed = urlsplit(value)
            if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
                continue
            if self._site_key(value) != self._site_key(site_url):
                return value
        return None

    def _assert_site_identity(self, site_url: str) -> None:
        if (not self._at_site(site_url) or self._stale_menu_site(site_url)
                or self._conflicting_site_heading(site_url)):
            self._save_navigation_diagnostic(site_url)
            raise NaverSessionError('주소창·메뉴·본문의 사이트가 일치하지 않아 요청을 보내지 않고 중단했습니다.')

    def _section(self, heading: str) -> Locator:
        assert self.page
        title = self._visible(self.page.get_by_text(heading, exact=True))
        if not title:
            raise NaverAutomationError(f"네이버 화면에서 '{heading}' 영역을 찾지 못했습니다.")
        return title.locator(
            'xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " row ")][1]'
        )

    def _open_menu(self, group: str, item: str | None = None) -> None:
        self._click_text(group)
        if item:
            self._click_text(item)

    def _raise_visible_error(self) -> None:
        assert self.page
        alert = self._visible(self.page.locator('[role="alert"].error'))
        if alert:
            message = " ".join(alert.locator(".v-alert__content").inner_text().split())
            if message:
                raise NaverAutomationError(f"네이버 요청 오류: {message}")

    def _type_like_user(self, field: Locator, value: str) -> None:
        assert self.page
        field.click()
        field.press("Control+A")
        field.press("Backspace")
        field.press_sequentially(value, delay=15)
        self.page.wait_for_timeout(300)

    def _click_and_confirm_api(self, button: Locator, endpoint: str, action: str) -> None:
        assert self.page
        try:
            with self.page.expect_response(
                lambda response: endpoint in response.url
                and response.request.method != "GET",
                timeout=30_000,
            ) as response_info:
                button.click()
            response = response_info.value
        except PlaywrightTimeoutError as exc:
            self._raise_visible_error()
            raise NaverAutomationError(
                f"네이버 {action} 요청이 서버로 전송되지 않았습니다."
            ) from exc
        if not 200 <= response.status < 300:
            raise NaverAutomationError(
                f"네이버 {action} 요청 실패: HTTP {response.status}"
            )

    @staticmethod
    def _site_console_url(page_name: str, site_url: str) -> str:
        encoded_site = quote(site_url, safe="")
        return f"https://searchadvisor.naver.com/console/site/{page_name}?site={encoded_site}"

    @classmethod
    def _settings_url(cls, site_url: str) -> str:
        return cls._site_console_url("option", site_url)

    def _open_action_page(
        self,
        route: str,
        site_url: str,
        ready: Callable[[], Locator],
        description: str,
    ) -> Locator:
        assert self.page
        target_url = self._site_console_url(route, site_url)
        for attempt in range(1, 4):
            self._goto_tolerating_login_redirect(target_url)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self.cancel_event is not None and self.cancel_event.is_set():
                    raise NaverSessionError('네이버 작업을 중지했습니다.')
                if self.page.is_closed() or self._is_login_page(self.page):
                    raise NaverSessionError('네이버 인증 또는 브라우저 상태가 변경되어 일괄 작업을 중단합니다. 로그인 상태를 확인해 주세요.')
                candidate = self._visible(ready())
                if candidate and urlsplit(self.page.url).path == f"/console/site/{route}" and self._at_site(site_url):
                    return candidate
                self.page.wait_for_timeout(250)
            if attempt < 3:
                self.status(f"{description} 화면 응답이 늦어 다시 여는 중... ({attempt}/2)")
                self.page.wait_for_timeout(1_500)
        raise NaverAutomationError(
            f"네이버 {description} 화면을 열지 못했습니다. "
            f"현재 화면: {self.page.title()} ({self.page.url})"
        )

    def set_fast_frequency(self, site_url: str) -> None:
        assert self.page
        self.status("수집 주기를 '빠르게'로 설정 중...")
        self._goto_tolerating_login_redirect(self._settings_url(site_url))
        fast = self.page.locator('input[type="radio"][value="fast"]').first
        try:
            fast.wait_for(state="attached", timeout=60_000)
        except PlaywrightTimeoutError as exc:
            raise NaverAutomationError(
                "네이버 설정 화면에서 수집 주기 '빠르게' 항목을 60초 안에 찾지 못했습니다."
            ) from exc
        if not self._at_site(site_url) or urlsplit(self.page.url).path != '/console/site/option':
            raise NaverAutomationError('설정 화면의 사이트 주소가 요청한 사이트와 달라 변경하지 않았습니다.')
        self._assert_site_identity(site_url)
        if fast.is_checked():
            self.status("수집 주기가 이미 '빠르게'로 선택되어 있습니다.")
            return
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise NaverSessionError('네이버 작업을 중지했습니다.')
        # Vuetify overlays the native input. Click its visible label instead.
        group = fast.locator('xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " v-radio ")][1]')
        label = group.locator('label').filter(has_text=re.compile(r'^\s*빠르게\s*$'))
        if label.count():
            label.first.click(timeout=15_000)
        else:
            self.page.get_by_label('빠르게', exact=True).check(timeout=15_000)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise NaverSessionError('네이버 작업을 중지했습니다.')
            if not self._at_site(site_url) or urlsplit(self.page.url).path != '/console/site/option':
                raise NaverSessionError('수집 주기 확인 중 사이트 또는 로그인 화면이 바뀌어 중단했습니다.')
            self._raise_visible_error()
            if fast.is_checked():
                self.page.wait_for_timeout(500)
                self._raise_visible_error()
                if fast.is_checked():
                    return
            self.page.wait_for_timeout(200)
        raise NaverAutomationError("'빠르게' 라벨을 클릭했지만 선택 상태가 유지되지 않았습니다. 완료로 기록하지 않았습니다.")

    def request_robots(self, site_url: str) -> None:
        assert self.page
        self.status("robots.txt 정보 수집 요청 중...")
        request_button = self._open_action_page(
            "check/robots",
            site_url,
            lambda: self.page.get_by_role("button", name="수집요청", exact=True),
            "robots.txt",
        )
        self._assert_site_identity(site_url)
        self._click_and_confirm_api(
            request_button, "/api-console/check/robots", "robots.txt 수집"
        )
        self.page.wait_for_timeout(1_500)
        self._raise_visible_error()

    def submit_sitemap(self, site_url: str) -> None:
        assert self.page
        self.status("sitemap.xml 제출 중...")
        page_field = self._open_action_page(
            "request/sitemap",
            site_url,
            lambda: self.page.locator('input[maxlength="2048"]'),
            "사이트맵 제출",
        )
        sitemap_url = site_url.rstrip("/") + "/sitemap.xml"
        self._assert_site_identity(site_url)
        if self._visible(self.page.locator(f'a[href="{sitemap_url}"]')):
            return
        button = self.page.get_by_role("button", name="확인", exact=True).first
        try:
            button.wait_for(state="visible", timeout=60_000)
        except PlaywrightTimeoutError as exc:
            raise NaverAutomationError(
                "네이버 사이트맵 제출 화면의 확인 버튼을 찾지 못했습니다."
            ) from exc
        self._type_like_user(page_field, sitemap_url)
        self._assert_site_identity(site_url)
        self._click_and_confirm_api(
            button, "/api-console/request/sitemap", "사이트맵 제출"
        )
        self.page.wait_for_timeout(1_500)
        self._raise_visible_error()
        try:
            self.page.locator(f'a[href="{sitemap_url}"]').first.wait_for(
                state="visible", timeout=15_000
            )
        except PlaywrightTimeoutError as exc:
            raise NaverAutomationError(
                "네이버 사이트맵 제출 목록에서 sitemap.xml을 확인하지 못했습니다."
            ) from exc

    def request_pages(
        self, site_url: str, page_urls: list[str], maximum: int = 50
    ) -> int:
        assert self.page
        self.status("웹 페이지 수집 요청 화면으로 이동 중...")
        page_field = self._open_action_page(
            "request/crawl",
            site_url,
            lambda: self.page.locator('input[maxlength="2048"]'),
            "웹 페이지 수집",
        )
        urls = []
        for url in page_urls:
            parsed = urlsplit(url)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                value = url
            else:
                value = site_url.rstrip("/") + "/" + url.lstrip("/")
            if value not in urls:
                urls.append(value)
        requested = 0
        for index, url in enumerate(urls[:maximum], start=1):
            self._assert_site_identity(site_url)
            self.status(f"페이지 수집 요청 {index}/{min(len(urls), maximum)}: {url}")
            existing = self._visible(self.page.locator(f'a[href="{url}"]'))
            if existing:
                requested += 1
                continue
            button = self.page.get_by_role("button", name="확인", exact=True).first
            try:
                button.wait_for(state="visible", timeout=60_000)
            except PlaywrightTimeoutError as exc:
                raise NaverAutomationError(
                    "네이버 페이지 수집 요청 화면의 확인 버튼을 찾지 못했습니다."
                ) from exc
            self._type_like_user(page_field, url)
            self._assert_site_identity(site_url)
            self._click_and_confirm_api(
                button, "/api-console/request/crawl", "웹 페이지 수집"
            )
            self.page.wait_for_timeout(1_200)
            self._raise_visible_error()
            try:
                self.page.locator(f'a[href="{url}"]').first.wait_for(
                    state="visible", timeout=15_000
                )
            except PlaywrightTimeoutError as exc:
                raise NaverAutomationError(
                    f"네이버 수집 요청 내역에서 URL을 확인하지 못했습니다: {url}"
                ) from exc
            requested += 1
        return requested

    def run_after_ownership(
        self,
        site_url: str,
        page_urls: list[str],
        completed_steps: set[str] | None = None,
        on_step: Callable[[str, str], None] | None = None,
    ) -> int:
        completed_steps = completed_steps or set()

        def update(step: str, state: str) -> None:
            if on_step:
                on_step(step, state)

        self.status(f"소유확인 사이트 열기: {site_url}")
        self._open_site(site_url)
        if "frequency" not in completed_steps:
            update("frequency", "진행 중")
            self.set_fast_frequency(site_url)
            update("frequency", "완료 (빠르게)")
        if "robots" not in completed_steps:
            update("robots", "진행 중")
            self.request_robots(site_url)
            update("robots", "완료")
        if "sitemap" not in completed_steps:
            update("sitemap", "진행 중")
            self.submit_sitemap(site_url)
            update("sitemap", "완료")
        if "crawl" in completed_steps:
            return 0
        update("crawl", "진행 중")
        requested = self.request_pages(site_url, page_urls)
        update("crawl", f"완료 {requested}개")
        return requested


def _run_browser_self_test(profile_dir: Path) -> None:
    with NaverBrowser(profile_dir, headless=False) as helper:
        assert helper.page
        helper.page.set_content(
            '<div class="row"><div>수집 요청 URL 입력</div>'
            '<input maxlength="2048" type="text"><button>확인</button></div>'
        )
        section = helper._section("수집 요청 URL 입력")
        if section.locator('input[maxlength="2048"]').count() != 1:
            raise RuntimeError("네이버 입력창 선택자 자체 검사 실패")


def automation_self_test(profile_dir: Path | None = None) -> None:
    if profile_dir is not None:
        _run_browser_self_test(profile_dir)
        return
    with tempfile.TemporaryDirectory(
        prefix="site-publisher-chromium-", ignore_cleanup_errors=True
    ) as temp_dir:
        _run_browser_self_test(Path(temp_dir))


def settings_page_self_test(profile_dir: Path, site_url: str) -> None:
    with NaverBrowser(profile_dir, headless=False) as helper:
        assert helper.page
        helper.page.goto(
            helper._settings_url(site_url), wait_until="domcontentloaded", timeout=60_000
        )
        fast = helper.page.locator('input[type="radio"][value="fast"]').first
        fast.wait_for(state="attached", timeout=60_000)
        if fast.count() != 1:
            raise RuntimeError("네이버 설정 페이지의 빠르게 라디오 감지 실패")
