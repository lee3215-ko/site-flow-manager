"""Opt-in durable, DPAPI-encrypted device outbox. No raw application logs."""
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit
import uuid

import requests
from secret_store import load_secret, save_secret


def validate_url(value):
    parts = urlsplit(value)
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
        raise ValueError('관리 서버 주소는 경로 없는 주소를 입력하세요.')
    if not parts.hostname or (parts.scheme != 'https' and not (
            parts.scheme == 'http' and parts.hostname in {'localhost', '127.0.0.1'})):
        raise ValueError('외부 서버는 HTTPS가 필요합니다. 로컬은 http://127.0.0.1:8787 사용 가능')
    return value.rstrip('/')


class AdminSync:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'admin-sync.bin'
        self.outbox = self.directory / 'admin-outbox'
        self.lock = threading.Lock()
        self.running = False
        self.status = '중앙 연동 꺼짐'
        try:
            self.config = json.loads(load_secret(self.path) or '{}')
        except ValueError:
            self.config = {}

    def configure(self, url, key, name):
        url = validate_url(url)
        if len(key) < 32:
            raise ValueError('기기 연결 키는 32자 이상이어야 합니다.')
        with self.lock:
            if self.config.get('url') and self.config['url'] != url and self.outbox.exists() and any(self.outbox.iterdir()):
                raise ValueError('기존 서버 미전송 기록이 있습니다. 기존 동기화를 완료한 후 변경하세요.')
            self.config = dict(url=url, key=key, name=name, device=self.config.get('device') or str(uuid.uuid4()), enabled=True)
            save_secret(self.path, json.dumps(self.config))

    def disable(self):
        with self.lock:
            self.config['enabled'] = False
            save_secret(self.path, json.dumps(self.config))
            self.status = '중앙 연동 꺼짐 (진행 중 요청은 완료될 수 있음)'

    def enqueue(self, projects, accounts, version):
        with self.lock:
            if not self.config.get('enabled'):
                return
            allowed = {'name', 'url', 'title', 'cloudflare', 'cloudflare_account', 'registration',
                       'ownership', 'naver_account', 'frequency', 'robots', 'sitemap', 'crawl',
                       'updated_at', 'crawl_completed_at', 'deployment_verified'}
            payload = dict(device=self.config['device'], name=self.config['name'], version=version,
                           projects=[{k: v for k, v in p.items() if k in allowed} for p in projects],
                           accounts=accounts)
            self.outbox.mkdir(exist_ok=True)
            # Unique encrypted snapshots retain offline state transitions across restarts.
            import time
            path = self.outbox / (str(time.time_ns()) + '-' + uuid.uuid4().hex + '.bin')
            temporary = path.with_suffix('.tmp')
            save_secret(temporary, json.dumps(payload, ensure_ascii=False))
            temporary.replace(path)
        self.flush()

    def flush(self):
        with self.lock:
            if self.running or not self.config.get('enabled'):
                return
            self.running = True
        threading.Thread(target=self._send, daemon=True, name='admin-sync').start()

    def _send(self):
        try:
            for path in sorted(self.outbox.glob('*.bin')):
                with self.lock:
                    config = dict(self.config)
                if not config.get('enabled'):
                    return
                payload = json.loads(load_secret(path))
                response = requests.post(config['url'] + '/api/sync', json=payload,
                                         headers={'Authorization': 'Bearer ' + config['key']},
                                         timeout=20, allow_redirects=False)
                if response.status_code != 200 or response.json().get('ok') is not True:
                    raise ValueError('sync rejected')
                path.unlink()
            self.status = '중앙 동기화 완료'
        except Exception:
            self.status = '중앙 서버 연결 실패: 기록 보관 중, 30초 후 재시도'
        finally:
            with self.lock:
                self.running = False
