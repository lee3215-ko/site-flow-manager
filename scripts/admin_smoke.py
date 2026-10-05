"""Exercise the local administrator UI using synthetic data only."""
import sys
from pathlib import Path
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright, expect
from admin_server.server import create_app


def main():
    with tempfile.TemporaryDirectory() as folder:
        app = create_app(dict(SECRET_KEY='s'*40, DEVICE_KEY='d'*40,
                              VAULT_KEY=Fernet.generate_key(), DATA_DIR=folder,
                              PASSWORD_HASH=generate_password_hash('smoke-only-password'),
                              SESSION_COOKIE_SECURE=False))
        app.test_client().post('/api/sync', headers={'Authorization':'Bearer '+'d'*40}, json={
            'device':'test-pc', 'name':'테스트 컴퓨터', 'version':'4.5.0',
            'projects':[{'name':'sample', 'title':'테스트 사이트', 'url':'https://sample.pages.dev',
                         'cloudflare':'완료', 'ownership':'완료', 'crawl':'완료 9개'}],
            'accounts':[{'account_id':'a'*32, 'api_token':'fake-test-token', 'name':'테스트 계정'}]})
        server = make_server('127.0.0.1', 0, app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page(viewport={'width':1400, 'height':1100})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    page.locator('#password').fill('smoke-only-password')
                    page.locator('#loginButton').click()
                    page.locator('#records tr').wait_for()
                    assert 'sample.pages.dev' in page.locator('#records').inner_text()
                    page.locator('#reauth').fill('smoke-only-password')
                    page.locator('#reveal').click()
                    expect(page.locator('#token')).to_have_value('fake-test-token')
                    page.locator('#hide').click()
                    assert page.locator('#token').input_value() == ''
                    page.locator('#search').fill('missing')
                    assert page.locator('#records tr').count() == 0
                    page.locator('#search').fill('')
                    output = Path('.dev-build/admin-preview.png')
                    output.parent.mkdir(exist_ok=True)
                    page.screenshot(path=str(output), full_page=True)
                    page.locator('#logout').click()
                    page.locator('#loginButton').wait_for()
                    assert not errors, errors
                    print('Admin browser smoke passed; synthetic data only.')
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()


if __name__ == '__main__':
    main()
