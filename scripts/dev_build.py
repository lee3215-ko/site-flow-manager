"""Local developer build watcher and stable desktop launcher (Windows)."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.dev-build'
PYTHON = Path(sys.executable).with_name('python.exe')


def fingerprint():
    paths = list(ROOT.glob('*.py')) + list(ROOT.glob('requirements*.txt'))
    paths += [ROOT / 'SiteFlow.spec', ROOT / 'README.md']
    paths += list((ROOT / 'scripts').glob('*.py')) + list((ROOT / 'tests').glob('test_*.py'))
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@contextmanager
def lock(name, wait=True):
    STATE.mkdir(exist_ok=True)
    with (STATE / name).open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if not wait:
                    raise RuntimeError('Developer watcher already running')
                time.sleep(1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def current():
    try:
        return json.loads((STATE / 'current.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def build():
    with lock('build.lock'):
        signature = fingerprint()
        previous = current()
        if previous.get('fingerprint') == signature and Path(previous['exe']).is_file():
            return Path(previous['exe'])
        output = STATE / 'builds' / f'{time.time_ns()}-{signature[:10]}'
        output.mkdir(parents=True)
        with (STATE / 'build.log').open('a', encoding='utf-8') as log:
            log.write(f'\n{time.ctime()} Building {signature}\n')
            log.flush()
            for args in [
                ['-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                ['-m', 'PyInstaller', '--noconfirm', '--distpath', str(output),
                 '--workpath', str(STATE / 'work'), 'SiteFlow.spec'],
            ]:
                subprocess.run([str(PYTHON), *args], cwd=ROOT, check=True,
                               stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        sys.path.insert(0, str(ROOT))
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = Path(playwright.chromium.executable_path).parent
        shutil.copytree(browser, output / 'browser', ignore=shutil.ignore_patterns('debug.log', 'First Run'))
        from deployment_runtime import bundle_runtime
        bundle_runtime(output)
        if fingerprint() != signature:
            raise RuntimeError('Source changed during build; waiting for a stable revision')
        exe = output / 'SiteFlow.exe'
        temporary = STATE / 'current.tmp'
        temporary.write_text(json.dumps({'fingerprint': signature, 'exe': str(exe)}), encoding='utf-8')
        os.replace(temporary, STATE / 'current.json')
        return exe


def watch():
    with lock('watch.lock', wait=False):
        last = None
        attempted = None
        while True:
            signature = fingerprint()
            if signature == last and signature != attempted:
                attempted = signature
                try:
                    build()
                except Exception as exc:
                    with (STATE / 'build.log').open('a', encoding='utf-8') as log:
                        log.write(f'\n{time.ctime()} FAILED: {exc}\n')
                    # Retry transient failures without a tight loop.
                    time.sleep(60)
                    attempted = None
            last = signature
            time.sleep(5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['build', 'launch', 'watch'])
    mode = parser.parse_args().mode
    if mode == 'watch':
        watch()
        return
    try:
        exe = build()
    except Exception:
        if mode != 'launch' or not Path(current().get('exe', '')).is_file():
            raise
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, 'Build failed. Opening the last successful build. See .dev-build/build.log.', 'SiteFlow Developer', 48)
        exe = Path(current()['exe'])
    if mode == 'launch':
        environment = dict(os.environ, SITEFLOW_DEV='1')
        subprocess.Popen([str(exe)], cwd=exe.parent, env=environment)
    else:
        print(exe)


if __name__ == '__main__':
    main()
