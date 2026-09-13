from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import zipfile
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from seo_prepare import normalize_site, verification_page, public_path


MAX_FILES = 20_000
MAX_UNPACKED_BYTES = 2 * 1024 * 1024 * 1024


class SiteBuildError(RuntimeError):
    pass


class _TitleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.finished = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "title" and not self.finished:
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title" and self.in_title:
            self.in_title = False
            self.finished = True

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)


def read_site_title(site_root: Path) -> str:
    try:
        raw = (site_root / "index.html").read_bytes()
    except OSError:
        return ""
    try:
        html = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        html = raw.decode("cp949", errors="replace")
    parser = _TitleParser()
    parser.feed(html)
    return " ".join("".join(parser.parts).split())


@dataclass
class PreparedSite:
    root: Path
    page_count: int
    sitemap_url: str
    robots_url: str
    indexnow_key: str


def project_name_from_zip(zip_path: Path) -> str:
    name = zip_path.stem.strip().lower()
    if name.endswith(".pages.dev"):
        name = name[:-10]
    name = re.sub(r"[^a-z0-9-]+", "-", name).strip("-")
    name = re.sub(r"-+", "-", name)[:58].rstrip("-")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,56}[a-z0-9])?", name):
        raise SiteBuildError(
            f"ZIP 파일명으로 사이트 주소를 만들 수 없습니다: {zip_path.name}\n"
            "파일명을 영문 소문자, 숫자, 하이픈으로 바꿔 주세요."
        )
    return name


def safe_extract(zip_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_FILES:
            raise SiteBuildError(f"ZIP 파일이 {MAX_FILES:,}개 파일 제한을 초과합니다.")
        if sum(item.file_size for item in members) > MAX_UNPACKED_BYTES:
            raise SiteBuildError("압축 해제 크기가 2GB를 초과합니다.")

        destination_resolved = destination.resolve()
        for item in members:
            name = item.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise SiteBuildError(f"안전하지 않은 ZIP 경로입니다: {item.filename}")
            target = (destination / Path(*path.parts)).resolve()
            if os.path.commonpath([destination_resolved, target]) != str(destination_resolved):
                raise SiteBuildError(f"ZIP 경로가 작업 폴더를 벗어납니다: {item.filename}")
        archive.extractall(destination)


def locate_site_root(unpacked: Path) -> Path:
    direct = unpacked / "index.html"
    if direct.is_file():
        return unpacked

    candidates = sorted(
        unpacked.rglob("index.html"),
        key=lambda path: (len(path.relative_to(unpacked).parts), str(path).lower()),
    )
    if not candidates:
        raise SiteBuildError(
            "배포할 index.html을 찾지 못했습니다. React/Vue 프로젝트라면 먼저 빌드한 dist 폴더를 ZIP으로 만들어 주세요."
        )
    return candidates[0].parent


def page_path(root: Path, html_path: Path) -> str:
    relative = html_path.relative_to(root).as_posix()
    lowered = relative.lower()
    if lowered == "index.html":
        return "/"
    if lowered.endswith("/index.html"):
        return "/" + quote(relative[:-10].rstrip("/")) + "/"
    return public_path(relative)


def discover_pages(root: Path) -> list[str]:
    ignored_parts = {"node_modules", ".git", ".cache"}
    pages = []
    for path in root.rglob("*.html"):
        relative = path.relative_to(root)
        if any(part.lower() in ignored_parts for part in relative.parts):
            continue
        if path.name.lower() in {"404.html", "500.html"}:
            continue
        if verification_page(path):
            continue
        pages.append(page_path(root, path))
    return sorted(set(pages), key=lambda value: (value != "/", value))


def write_seo_files(root: Path, base_url: str, indexnow_key: str | None = None) -> PreparedSite:
    base_url = base_url.rstrip("/")
    try:
        page_files = normalize_site(root, base_url)
    except (ValueError, UnicodeError) as exc:
        raise SiteBuildError(str(exc)) from exc
    pages = sorted(page_files)
    if not pages:
        raise SiteBuildError("사이트에서 HTML 페이지를 찾지 못했습니다.")

    key = indexnow_key or secrets.token_hex(16)
    sitemap_url = f"{base_url}/sitemap.xml"
    old_robots = (root / "robots.txt").read_text(encoding="utf-8-sig") if (root / "robots.txt").exists() else "User-agent: *\nAllow: /\n"
    robots = '\n'.join(line for line in old_robots.splitlines() if not line.strip().lower().startswith('sitemap:')) + f'\nSitemap: {sitemap_url}\n'
    (root / "robots.txt").write_text(robots, encoding="utf-8")

    today = date.today().isoformat()
    entries = "\n".join(
        f"  <url><loc>{base_url}{path}</loc></url>" for path in pages
    )
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )
    (root / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    (root / f"{key}.txt").write_text(key, encoding="utf-8")

    manifest = {
        "base_url": base_url,
        "pages": [f"{base_url}{path}" for path in pages],
        "indexnow_key": key,
        "page_files": page_files,
        "seo_checked": True,
    }
    (root.parent / "publish-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return PreparedSite(
        root=root,
        page_count=len(pages),
        sitemap_url=sitemap_url,
        robots_url=f"{base_url}/robots.txt",
        indexnow_key=key,
    )


def prepare_zip(
    zip_path: Path,
    project_dir: Path,
    project_name: str,
    base_url: str | None = None,
) -> PreparedSite:
    unpacked = project_dir / "unpacked"
    publish_root = project_dir / "site"
    preserved = {p.name: p.read_bytes() for p in publish_root.glob('*.html') if verification_page(p)}
    if unpacked.exists():
        shutil.rmtree(unpacked)
    if publish_root.exists():
        shutil.rmtree(publish_root)
    unpacked.mkdir(parents=True)
    safe_extract(zip_path, unpacked)
    source_root = locate_site_root(unpacked)
    shutil.copytree(source_root, publish_root)
    for name, content in preserved.items():
        (publish_root / name).write_bytes(content)
    return write_seo_files(
        publish_root, base_url or f"https://{project_name}.pages.dev"
    )


def install_verification_file(site_root: Path, verification_file: Path) -> Path:
    if not verification_file.name.lower().startswith("naver") or verification_file.suffix.lower() != ".html":
        raise SiteBuildError("네이버에서 받은 naver*.html 소유확인 파일을 선택해 주세요.")
    target = site_root / verification_file.name
    shutil.copy2(verification_file, target)
    return target


def archived_zip_path(zip_path: Path) -> Path | None:
    if zip_path.parent.name == "성공":
        return zip_path if zip_path.is_file() else None
    destination = zip_path.parent / "성공" / zip_path.name
    return destination if destination.is_file() else None


def move_zip_to_success(zip_path: Path) -> Path:
    existing = archived_zip_path(zip_path)
    if existing:
        return existing
    if not zip_path.is_file():
        raise SiteBuildError(f"이동할 원본 ZIP 파일을 찾지 못했습니다: {zip_path}")
    destination_dir = zip_path.parent / "성공"
    destination_dir.mkdir(exist_ok=True)
    destination = destination_dir / zip_path.name
    if destination.exists():
        raise SiteBuildError(f"성공 폴더에 같은 이름의 ZIP이 이미 있습니다: {destination}")
    return Path(shutil.move(str(zip_path), str(destination)))
