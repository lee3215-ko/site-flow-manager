"""Local administrator lifecycle owned by the desktop application."""
import json
import secrets
import threading
from pathlib import Path

from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash
from waitress import create_server
from secret_store import load_secret, save_secret
from admin_server.server import create_app


class LocalAdmin:
    url = 'http://127.0.0.1:8787'

    def __init__(self, directory):
        self.directory = Path(directory)
        self.config_path = self.directory / 'server.bin'
        self.server = None
        self.config = None

    @property
    def needs_setup(self):
        return not self.config_path.exists()

    def start(self, password=None):
        if self.server is not None:
            return self.config
        if self.needs_setup:
            if not password or len(password) < 14:
                raise ValueError('관리자 비밀번호는 14자 이상으로 설정해 주세요.')
            config = dict(SECRET_KEY=secrets.token_urlsafe(48), DEVICE_KEY=secrets.token_urlsafe(48),
                          PASSWORD_HASH=generate_password_hash(password), VAULT_KEY=Fernet.generate_key().decode())
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.config_path.with_suffix('.tmp')
            save_secret(temporary, json.dumps(config))
            temporary.replace(self.config_path)
        config = json.loads(load_secret(self.config_path))
        config.update(DATA_DIR=str(self.directory), SESSION_COOKIE_SECURE=False,
                      SESSION_COOKIE_NAME='siteflow_admin', TRUSTED_HOSTS=['127.0.0.1', 'localhost'])
        try:
            server = create_server(create_app(config), host='127.0.0.1', port=8787)
        except OSError as exc:
            raise RuntimeError('관리자 서버 포트가 사용 중입니다. 이전 admin-start 실행 창 또는 다른 SiteFlow를 닫고 다시 눌러 주세요.') from exc
        self.config = config
        self.server = server
        threading.Thread(target=server.run, daemon=True, name='local-admin').start()
        return config

    def close(self):
        if self.server:
            self.server.close()
            self.server = None
