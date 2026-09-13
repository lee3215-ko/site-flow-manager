"""Find and package the Node/npm runtime without depending on user PATH."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def runtime_command(app_dir=None):
    app_dir = Path(app_dir) if app_dir else (Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent)
    candidates = [app_dir / 'browser' / 'siteflow-runtime' / 'node.exe']
    system = shutil.which('node.exe') or shutil.which('node')
    if system:
        candidates.append(Path(system))
    for variable in ('PROGRAMFILES', 'LOCALAPPDATA'):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / 'nodejs' / 'node.exe')
    for node in candidates:
        cli = node.parent / 'node_modules/npm/bin/npx-cli.js'
        if node.is_file() and cli.is_file():
            return [str(node), str(cli)]
    raise RuntimeError('배포용 Node.js/npm을 찾지 못했습니다. 최신 ZIP 전체를 압축 해제해 browser 폴더까지 함께 사용해 주세요. 사이트 등록은 시작하지 않습니다.')


def preflight():
    command = runtime_command()
    result = subprocess.run(command + ['--version'], capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('배포용 Node.js/npm 실행 확인에 실패했습니다. 사이트 등록을 중단합니다.')
    return command


def bundle_runtime(package):
    node = shutil.which('node.exe') or shutil.which('node')
    if not node:
        raise RuntimeError('Build requires Node.js with npm')
    node = Path(node)
    npm = node.parent / 'node_modules/npm'
    if not (npm / 'bin/npx-cli.js').is_file():
        raise RuntimeError('Build requires npm beside node.exe')
    target = Path(package) / 'browser/siteflow-runtime'
    target.mkdir(parents=True)
    shutil.copy2(node, target / 'node.exe')
    shutil.copytree(npm, target / 'node_modules/npm')
    license_file = node.parent / 'LICENSE'
    if license_file.exists():
        shutil.copy2(license_file, target / 'LICENSE')
    # Exercise the bundled runtime with no globally installed Node on PATH.
    env = dict(os.environ, PATH=str(target))
    subprocess.run(runtime_command(package) + ['--version'], env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
