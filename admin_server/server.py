"""Private administrator service; run behind HTTPS outside localhost."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager

from cryptography.fernet import Fernet
from flask import Flask, abort, jsonify, render_template, request, session
from werkzeug.security import check_password_hash
from cloudflare_client import CloudflareClient
from site_builder import safe_extract, locate_site_root, write_seo_files


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get('SITEFLOW_SESSION_KEY'),
        PASSWORD_HASH=os.environ.get('SITEFLOW_PASSWORD_HASH'),
        DEVICE_KEY=os.environ.get('SITEFLOW_DEVICE_KEY', ''),
        VAULT_KEY=os.environ.get('SITEFLOW_VAULT_KEY'),
        DATA_DIR=os.environ.get('SITEFLOW_ADMIN_DATA', 'admin-data'),
        MAX_CONTENT_LENGTH=100 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
        SESSION_COOKIE_SECURE=True, PERMANENT_SESSION_LIFETIME=1800,
    )
    if config:
        app.config.update(config)
    for key in ('SECRET_KEY', 'DEVICE_KEY'):
        if len(app.config.get(key) or '') < 32:
            raise ValueError(f'{key} must contain at least 32 random characters')
    if not app.config['PASSWORD_HASH']:
        raise ValueError('PASSWORD_HASH required')
    vault = Fernet(app.config['VAULT_KEY'])
    data = Path(app.config['DATA_DIR'])
    data.mkdir(parents=True, exist_ok=True)
    database = data / 'admin.sqlite3'
    deploy_lock = threading.Lock()
    login_lock = threading.Lock()
    failures = []

    @contextmanager
    def db():
        connection = sqlite3.connect(database, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    with db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, name TEXT, seen REAL, version TEXT);
            CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, name TEXT, token BLOB);
            CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY, device TEXT, at REAL, digest TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL, action TEXT, target TEXT, result TEXT);
        ''')

    def audit(action, target, result):
        with db() as conn:
            conn.execute('INSERT INTO audit(at,action,target,result) VALUES(?,?,?,?)',
                         (time.time(), action, target, result))

    def password_ok():
        with login_lock:
            now = time.time()
            failures[:] = [t for t in failures if now - t < 300]
            if len(failures) >= 10:
                abort(429)
            if not check_password_hash(app.config['PASSWORD_HASH'], request.json.get('password', '')):
                failures.append(now)
                abort(401)

    @app.before_request
    def authorize():
        if request.path == '/api/sync':
            if not hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + app.config['DEVICE_KEY']):
                abort(401)
            return
        if request.path in ('/', '/api/login'):
            if request.path == '/api/login' and not request.is_json:
                abort(415)
            return
        if not session.get('admin'):
            abort(401)
        if request.method != 'GET' and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session.get('csrf', 'missing')):
            abort(403)

    @app.after_request
    def security(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'"
        return response

    @app.get('/')
    def home():
        return render_template('index.html')

    @app.post('/api/login')
    def login():
        password_ok()
        session.clear()
        session.update(admin=True, csrf=secrets.token_urlsafe(32))
        session.permanent = True
        return jsonify(csrf=session['csrf'])

    @app.post('/api/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.post('/api/sync')
    def sync():
        body = request.get_json()
        device = str(body.get('device', ''))
        if not re.fullmatch(r'[a-zA-Z0-9-]{1,64}', device):
            abort(400)
        projects, accounts = body.get('projects', []), body.get('accounts', [])
        if not isinstance(projects, list) or len(projects) > 10000 or not isinstance(accounts, list) or len(accounts) > 1000:
            abort(400)
        allowed = {'name', 'url', 'title', 'cloudflare', 'cloudflare_account', 'registration',
                   'ownership', 'naver_account', 'frequency', 'robots', 'sitemap', 'crawl',
                   'updated_at', 'crawl_completed_at', 'deployment_verified'}
        clean = [{k: str(v)[:1000] for k, v in p.items() if k in allowed} for p in projects]
        payload = json.dumps(clean, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        validated = []
        for account in accounts:
            aid, token = str(account.get('account_id', '')), str(account.get('api_token', ''))
            if not re.fullmatch(r'[a-fA-F0-9]{32}', aid) or not 1 <= len(token) <= 1024:
                abort(400)
            validated.append((aid, str(account.get('name', ''))[:100], vault.encrypt(token.encode())))
        with db() as conn:
            conn.execute('INSERT OR REPLACE INTO devices VALUES(?,?,?,?)',
                         (device, str(body.get('name', ''))[:100], time.time(), str(body.get('version', ''))[:30]))
            previous = conn.execute('SELECT digest FROM records WHERE device=? ORDER BY id DESC LIMIT 1', (device,)).fetchone()
            if previous is None or previous['digest'] != digest:
                conn.execute('INSERT INTO records(device,at,digest,payload) VALUES(?,?,?,?)',
                             (device, time.time(), digest, payload))
            conn.executemany('INSERT OR REPLACE INTO accounts VALUES(?,?,?)', validated)
        return jsonify(ok=True)

    @app.get('/api/state')
    def state():
        page = max(0, min(1000000, request.args.get('page', 0, type=int)))
        with db() as conn:
            devices = [dict(r) for r in conn.execute('SELECT * FROM devices ORDER BY seen DESC')]
            accounts = [dict(r) for r in conn.execute('SELECT id,name FROM accounts')]
            records = [dict(r) for r in conn.execute('SELECT * FROM records ORDER BY id DESC LIMIT 50 OFFSET ?', (page * 50,))]
            events = [dict(r) for r in conn.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 50')]
        return jsonify(devices=devices, accounts=accounts, records=records, audit=events)

    @app.post('/api/token')
    def token():
        password_ok()
        aid = request.json.get('account', '')
        with db() as conn:
            account = conn.execute('SELECT token FROM accounts WHERE id=?', (aid,)).fetchone()
        if not account:
            abort(404)
        audit('token-reveal', aid, 'ok')
        return jsonify(token=vault.decrypt(account['token']).decode())

    @app.post('/api/redeploy')
    def redeploy():
        aid, project = request.form.get('account', ''), request.form.get('project', '')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,57}', project):
            abort(400)
        upload = request.files.get('zip')
        if not upload:
            abort(400)
        with db() as conn:
            account = conn.execute('SELECT token FROM accounts WHERE id=?', (aid,)).fetchone()
        if not account:
            abort(404)
        if not deploy_lock.acquire(blocking=False):
            abort(409)
        audit('redeploy', aid + '/' + project, 'started')
        try:
            client = CloudflareClient(aid, vault.decrypt(account['token']).decode())
            existing = next((p for p in client.list_projects() if p.get('name') == project), None)
            if not existing:
                raise ValueError('Existing project required')
            host = existing.get('subdomain', '')
            if not re.fullmatch(r'[a-z0-9-]+\.pages\.dev', host):
                raise ValueError('Invalid production domain')
            with tempfile.TemporaryDirectory(prefix='siteflow-admin-') as folder:
                root = Path(folder)
                archive = root / 'site.zip'
                upload.save(archive)
                safe_extract(archive, root / 'unpacked')
                site = locate_site_root(root / 'unpacked')
                forbidden = {'_worker.js', 'functions', '.git', 'node_modules', '.npmrc',
                             'wrangler.toml', 'wrangler.json', 'wrangler.jsonc', '.dev.vars'}
                if any(p.name.lower() in forbidden or p.name.lower().startswith('.env') for p in (root / 'unpacked').rglob('*')):
                    raise ValueError('Static assets only')
                write_seo_files(site, 'https://' + host)
                client.deploy(project, site)
            audit('redeploy', aid + '/' + project, 'success')
            return jsonify(ok=True, url='https://' + host)
        except Exception:
            audit('redeploy', aid + '/' + project, 'failed')
            return jsonify(error='재배포 실패: 계정 권한, 프로젝트명, ZIP 구성을 확인하세요.'), 400
        finally:
            deploy_lock.release()

    return app


if __name__ == '__main__':
    from waitress import serve
    application = create_app({'SESSION_COOKIE_SECURE': os.environ.get('SITEFLOW_LOCAL_HTTP') != '1'})
    serve(application, host='127.0.0.1', port=8787)
