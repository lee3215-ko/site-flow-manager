"""Windows local setup/launcher. Secrets never appear in shell arguments."""
import getpass
import json
import os
from pathlib import Path
import secrets
import sys

from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash
from secret_store import load_secret, save_secret
from admin_server.server import create_app


def main():
    root = Path(os.environ['LOCALAPPDATA']) / 'SiteFlowAdmin'
    root.mkdir(exist_ok=True)
    config_file = root / 'server.bin'
    if not config_file.exists():
        password = getpass.getpass('New administrator password (at least 14 characters): ')
        if len(password) < 14 or password != getpass.getpass('Repeat password: '):
            raise SystemExit('Passwords must match and contain at least 14 characters.')
        config = dict(SECRET_KEY=secrets.token_urlsafe(48), DEVICE_KEY=secrets.token_urlsafe(48),
                      PASSWORD_HASH=generate_password_hash(password), VAULT_KEY=Fernet.generate_key().decode())
        save_secret(config_file, json.dumps(config))
    config = json.loads(load_secret(config_file))
    if '--connection-key' in sys.argv:
        import tkinter as tk
        from tkinter import ttk
        window = tk.Tk()
        window.title('SiteFlow device connection key')
        tk.Label(window, text='Paste into SiteFlow > 중앙 관리자 연동. Keep this key private.').pack(padx=20, pady=10)
        field = ttk.Entry(window, width=75, show='*')
        field.insert(0, config['DEVICE_KEY'])
        field.pack(padx=20, pady=10)
        def copy():
            window.clipboard_clear()
            window.clipboard_append(config['DEVICE_KEY'])
        ttk.Button(window, text='Copy connection key', command=copy).pack(pady=10)
        window.mainloop()
        return
    config.update(DATA_DIR=str(root), SESSION_COOKIE_SECURE=False)
    from waitress import serve
    print('Local administrator: http://127.0.0.1:8787 (Ctrl+C to stop)')
    serve(create_app(config), host='127.0.0.1', port=8787)


if __name__ == '__main__':
    main()
