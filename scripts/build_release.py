"""Build a clean public package from an explicit asset list."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version import APP_VERSION, ASSET_NAME
from playwright.sync_api import sync_playwright


def main():
    tag = os.environ.get("GITHUB_REF_NAME", "")
    if tag.startswith("v") and tag != f"v{APP_VERSION}":
        raise RuntimeError("Git tag and version.py must match")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "SiteFlow.spec"],
                   cwd=ROOT, check=True)
    with sync_playwright() as playwright:
        browser = Path(playwright.chromium.executable_path).parent
    if not (browser / "chrome.exe").is_file():
        raise RuntimeError("Run python -m playwright install chromium first")
    output = ROOT / "release" / f"v{APP_VERSION}"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="siteflow-package-") as temporary:
        package = Path(temporary) / "SiteFlow"
        package.mkdir()
        shutil.copy2(ROOT / "dist/SiteFlow.exe", package / "SiteFlow.exe")
        shutil.copy2(ROOT / "README.md", package / "README.md")
        shutil.copytree(browser, package / "browser", ignore=shutil.ignore_patterns("debug.log", "First Run"))
        (package / "version.json").write_text(json.dumps({"version": APP_VERSION}), encoding="utf-8")
        archive = output / ASSET_NAME
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for source in sorted(package.rglob("*")):
                if source.is_file():
                    bundle.write(source, source.relative_to(package.parent).as_posix())
        with archive.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        (output / "SHA256SUMS.txt").write_text(f"{digest}  {ASSET_NAME}\n", encoding="ascii")
        print(archive)


if __name__ == "__main__":
    main()
