from __future__ import annotations

import json
import time
import webbrowser
from pathlib import Path

import requests


NAVER_DASHBOARD = "https://searchadvisor.naver.com/console/board"
INDEXNOW_ENDPOINT = "https://searchadvisor.naver.com/indexnow"


class NaverToolError(RuntimeError):
    pass


def open_dashboard() -> None:
    webbrowser.open(NAVER_DASHBOARD)


def load_manifest(site_root: Path) -> dict:
    manifest = site_root.parent / "publish-manifest.json"
    if not manifest.is_file():
        raise NaverToolError("사이트 배포 정보를 찾지 못했습니다.")
    return json.loads(manifest.read_text(encoding="utf-8"))


def check_public_files(site_root: Path) -> dict[str, str]:
    data = load_manifest(site_root)
    base_url = data["base_url"].rstrip("/")
    targets = {
        "홈페이지": base_url + "/",
        "robots.txt": base_url + "/robots.txt",
        "sitemap.xml": base_url + "/sitemap.xml",
        "IndexNow 키": f"{base_url}/{data['indexnow_key']}.txt",
    }
    result = {}
    for label, url in targets.items():
        try:
            response = requests.get(url, timeout=20, allow_redirects=True)
            result[label] = f"{response.status_code} {response.headers.get('content-type', '')}".strip()
        except requests.RequestException as exc:
            result[label] = f"접속 실패: {exc}"
    return result


def verify_public_deployment(
    site_root: Path,
    verification_file: Path,
    timeout_seconds: int = 120,
    interval_seconds: float = 3,
) -> dict[str, str]:
    data = load_manifest(site_root)
    base_url = data["base_url"].rstrip("/")
    verification = site_root / verification_file.name
    expected = {
        verification.name: verification,
        "robots.txt": site_root / "robots.txt",
        "sitemap.xml": site_root / "sitemap.xml",
        f"{data['indexnow_key']}.txt": site_root / f"{data['indexnow_key']}.txt",
    }
    missing = [name for name, path in expected.items() if not path.is_file()]
    if missing:
        raise NaverToolError(f"배포 작업본에 필수 파일이 없습니다: {', '.join(missing)}")

    deadline = time.monotonic() + timeout_seconds
    last_results: dict[str, str] = {}
    while True:
        nonce = int(time.time() * 1000)
        all_match = True
        last_results = {}
        for name, local_path in expected.items():
            try:
                response = requests.get(
                    f"{base_url}/{name}?codex_verify={nonce}",
                    timeout=20,
                    allow_redirects=True,
                    headers={"Cache-Control": "no-cache"},
                )
                if response.status_code == 200 and response.content == local_path.read_bytes():
                    last_results[name] = "일치"
                else:
                    all_match = False
                    last_results[name] = (
                        f"불일치 (HTTP {response.status_code}, "
                        f"공개 {len(response.content)}바이트/로컬 {local_path.stat().st_size}바이트)"
                    )
            except requests.RequestException as exc:
                all_match = False
                last_results[name] = f"접속 실패: {exc}"
        if all_match:
            return last_results
        if time.monotonic() >= deadline:
            details = "; ".join(
                f"{name}: {result}" for name, result in last_results.items()
            )
            raise NaverToolError(
                "Cloudflare 배포 명령은 끝났지만 공개 파일 검증에 실패했습니다. "
                f"완료 처리 및 ZIP 이동을 중단했습니다. {details}"
            )
        time.sleep(interval_seconds)


def submit_indexnow(site_root: Path) -> tuple[int, str]:
    data = load_manifest(site_root)
    urls = data.get("pages") or []
    if not urls:
        raise NaverToolError("요청할 페이지가 없습니다.")
    base_url = data["base_url"].rstrip("/")
    payload = {
        "host": base_url.removeprefix("https://").removeprefix("http://"),
        "key": data["indexnow_key"],
        "keyLocation": f"{base_url}/{data['indexnow_key']}.txt",
        "urlList": urls[:10_000],
    }
    response = requests.post(INDEXNOW_ENDPOINT, json=payload, timeout=30)
    if response.status_code not in {200, 202}:
        raise NaverToolError(f"IndexNow 요청 실패 ({response.status_code}): {response.text[:300]}")
    return len(payload["urlList"]), response.text[:300]


def copy_naver_values(root, site_root: Path) -> None:
    data = load_manifest(site_root)
    text = "\n".join(
        (
            data["base_url"],
            data["base_url"].rstrip("/") + "/sitemap.xml",
            data["base_url"].rstrip("/") + "/robots.txt",
        )
    )
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()
