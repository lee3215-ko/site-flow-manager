import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from site_builder import (
    SiteBuildError,
    archived_zip_path,
    move_zip_to_success,
    prepare_zip,
    project_name_from_zip,
    safe_extract,
)


class SiteBuilderTests(unittest.TestCase):
    def test_project_name_comes_from_zip_filename(self):
        self.assertEqual(project_name_from_zip(Path("tiket159-sk.pages.dev.zip")), "tiket159-sk")
        self.assertEqual(project_name_from_zip(Path("My Site.zip")), "my-site")

    def test_prepare_zip_generates_search_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "site.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("website/index.html", "<h1>home</h1>")
                output.writestr("website/about.html", "<h1>about</h1>")
                output.writestr("website/blog/index.html", "<h1>blog</h1>")
            prepared = prepare_zip(archive, root / "project", "sample-site")
            self.assertEqual(prepared.page_count, 3)
            self.assertTrue((prepared.root / "robots.txt").is_file())
            self.assertTrue((prepared.root / "sitemap.xml").is_file())
            self.assertIn("https://sample-site.pages.dev/blog/", (prepared.root / "sitemap.xml").read_text(encoding="utf-8"))

    def test_prepare_zip_accepts_cloudflare_assigned_url(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "tiket168.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("index.html", "<html><body>site</body></html>")

            prepared = prepare_zip(
                archive,
                root / "project",
                "tiket168",
                base_url="https://tiket168-7rw.pages.dev",
            )

            manifest = json.loads(
                (prepared.root.parent / "publish-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                manifest["base_url"], "https://tiket168-7rw.pages.dev"
            )
            self.assertIn(
                "https://tiket168-7rw.pages.dev/sitemap.xml",
                (prepared.root / "robots.txt").read_text(encoding="utf-8"),
            )

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../outside.txt", "bad")
            with self.assertRaises(SiteBuildError):
                safe_extract(archive, root / "output")

    def test_successful_zip_is_moved_to_sibling_success_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "site.zip"
            archive.write_bytes(b"zip")

            moved = move_zip_to_success(archive)

            self.assertEqual(moved, root / "성공" / "site.zip")
            self.assertTrue(moved.is_file())
            self.assertFalse(archive.exists())
            self.assertEqual(archived_zip_path(archive), moved)

    def test_zip_already_in_success_folder_is_not_moved_again(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "성공" / "site.zip"
            archive.parent.mkdir()
            archive.write_bytes(b"zip")

            self.assertEqual(move_zip_to_success(archive), archive)


if __name__ == "__main__":
    unittest.main()
