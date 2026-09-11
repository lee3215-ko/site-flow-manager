from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile

import requests

from version import APP_VERSION, ASSET_NAME, GITHUB_REPOSITORY

RELEASES_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases/latest"
MANAGED = ("SiteFlow.exe", "browser", "README.md", "version.json")
MAX_ZIP = 600 * 1024 * 1024


def version_tuple(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"v?\d+\.\d+(?:\.\d+)?", value):
        raise ValueError("정식 버전 번호가 올바르지 않습니다.")
    parts = tuple(int(part) for part in value.removeprefix("v").split("."))
    return (parts + (0,))[:3]


def latest_release(current: str = APP_VERSION) -> dict | None:
    response = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "SiteFlow-Updater"},
        timeout=20,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    release = response.json()
    if release.get("draft") or release.get("prerelease"):
        return None
    if version_tuple(release["tag_name"]) <= version_tuple(current):
        return None
    asset = next((a for a in release.get("assets", []) if a["name"] == ASSET_NAME), None)
    if not asset or not re.fullmatch(r"sha256:[a-f0-9]{64}", asset.get("digest") or ""):
        raise ValueError("새 버전의 배포 파일 또는 SHA-256 검증 정보가 아직 준비되지 않았습니다.")
    expected = f"https://github.com/{GITHUB_REPOSITORY}/releases/download/{release['tag_name']}/{ASSET_NAME}"
    if asset["browser_download_url"] != expected:
        raise ValueError("공식 배포 파일 주소가 일치하지 않습니다.")
    return {"version": release["tag_name"].removeprefix("v"), "url": expected,
            "sha256": asset["digest"].split(":")[1], "size": asset["size"]}


def unpack_verified(archive: Path, destination: Path, expected_hash: str, version: str) -> Path:
    with archive.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != expected_hash:
        raise ValueError("업데이트 파일 검증에 실패했습니다. 기존 버전은 유지됩니다.")
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 5000 or sum(e.file_size for e in entries) > 2 * 1024**3:
            raise ValueError("업데이트 압축 파일이 허용 크기를 초과합니다.")
        names = set()
        for entry in entries:
            parts = PurePosixPath(entry.filename.replace("\\", "/")).parts
            if (len(parts) < 2 or parts[0] != "SiteFlow" or parts[1] not in MANAGED
                    or any(p in {"..", "."} or ":" in p or p.endswith((".", " ")) for p in parts)
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError("업데이트 압축 경로가 올바르지 않습니다.")
            key = "/".join(parts).lower()
            if key in names:
                raise ValueError("업데이트 압축에 중복 경로가 있습니다.")
            names.add(key)
        bundle.extractall(destination)
    root = destination / "SiteFlow"
    if not (root / "SiteFlow.exe").is_file() or not (root / "browser/chrome.exe").is_file():
        raise ValueError("업데이트에 실행 파일이 없습니다.")
    manifest = json.loads((root / "version.json").read_text(encoding="utf-8"))
    if manifest.get("version") != version:
        raise ValueError("다운로드한 프로그램의 버전이 일치하지 않습니다.")
    return root


def prepare_update(release: dict) -> Path:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("자동 설치는 배포용 SiteFlow.exe에서 사용할 수 있습니다.")
    install = Path(sys.executable).resolve().parent
    stage = Path(tempfile.mkdtemp(prefix=".siteflow-update-", dir=install.parent))
    archive = stage / "download.zip"
    try:
        if not 0 < release["size"] <= MAX_ZIP:
            raise ValueError("업데이트 크기가 올바르지 않습니다.")
        with requests.get(release["url"], stream=True, timeout=(15, 60)) as response:
            response.raise_for_status()
            size = 0
            with archive.open("wb") as out:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ZIP:
                        raise ValueError("업데이트 다운로드 크기 초과")
                    out.write(chunk)
        if size != release["size"]:
            raise ValueError("업데이트 다운로드가 완료되지 않았습니다.")
        payload = unpack_verified(archive, stage / "payload", release["sha256"], release["version"])
        helper = stage / "UpdateHelper.exe"
        shutil.copy2(sys.executable, helper)
        plan = stage / "plan.json"
        plan.write_text(json.dumps({"install": str(install), "payload": str(payload),
                                   "pid": os.getpid()}, ensure_ascii=False), encoding="utf-8")
        return plan
    except Exception:
        shutil.rmtree(stage)
        raise


def start_helper(plan: Path) -> None:
    subprocess.Popen([str(plan.parent / "UpdateHelper.exe"), "--apply-update", str(plan)],
                     creationflags=subprocess.CREATE_NO_WINDOW)


def replace_files(install: Path, payload: Path, backup: Path) -> None:
    backup.mkdir()
    moved, installed = [], []
    try:
        for name in MANAGED:
            target = install / name
            if target.exists():
                target.rename(backup / name)
                moved.append(name)
            (payload / name).rename(target)
            installed.append(name)
    except Exception:
        for name in reversed(installed):
            (install / name).rename(payload / name)
        for name in reversed(moved):
            (backup / name).rename(install / name)
        backup.rmdir()
        raise


def apply_update(plan_path: Path) -> None:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    stage = plan_path.resolve().parent
    install = Path(plan["install"]).resolve()
    payload = Path(plan["payload"]).resolve()
    if (not stage.name.startswith(".siteflow-update-") or stage.parent != install.parent
            or payload != stage / "payload/SiteFlow" or stage == install
            or install.is_symlink()):
        raise ValueError("업데이트 작업 경로가 올바르지 않습니다.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, int(plan["pid"]))
    if handle:
        try:
            if kernel.WaitForSingleObject(handle, 120_000) != 0:
                raise RuntimeError("프로그램 종료 대기 시간이 초과되어 업데이트를 취소했습니다.")
        finally:
            kernel.CloseHandle(handle)
    elif ctypes.get_last_error() != 87:
        raise ctypes.WinError(ctypes.get_last_error())
    deadline = time.monotonic() + 30
    while True:
        try:
            replace_files(install, payload, stage / "backup")
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    subprocess.Popen([str(install / "SiteFlow.exe")], cwd=install)
    # Keep the previous files in stage/backup for manual recovery.
