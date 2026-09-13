import json
import tempfile
import unittest
from pathlib import Path
from site_builder import write_seo_files, SiteBuildError


class SeoTests(unittest.TestCase):
    def test_domain_paths_json_and_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'site'
            root.mkdir()
            (root / 'index.html').write_text('''<html><head>
<link rel="canonical" href="https://old.netlify.app/">
<meta property="og:image" content="https://old.netlify.app/a.png">
<script type="application/ld+json">{"url":"https://old.netlify.app/about.html"}</script>
</head><body><a href="/about.html">About</a><a href="https://example.org/">External</a></body></html>''')
            (root / 'about.html').write_text('<html><head></head><body>About</body></html>')
            (root / 'a.png').write_bytes(b'image')
            (root / 'naver123.html').write_bytes(b'naver-site-verification: naver123.html')
            (root / 'google123.html').write_bytes(b'google-site-verification: google123.html')
            first = write_seo_files(root, 'https://new.pages.dev')
            text = (root / 'index.html').read_text(encoding='utf-8')
            self.assertNotIn('old.netlify.app', text)
            self.assertIn('https://new.pages.dev/about', text)
            self.assertIn('https://example.org/', text)
            self.assertEqual(first.page_count, 2)
            self.assertNotIn('google123', (root / 'sitemap.xml').read_text())
            self.assertEqual((root / 'naver123.html').read_bytes(), b'naver-site-verification: naver123.html')
            self.assertTrue((root / '404.html').exists())
            write_seo_files(root, 'https://new.pages.dev', first.indexnow_key)
            self.assertEqual(text, (root / 'index.html').read_text(encoding='utf-8'))

    def test_missing_local_file_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'index.html').write_text('<img src="/missing.png">')
            with self.assertRaisesRegex(SiteBuildError, 'missing.png'):
                write_seo_files(root, 'https://new.pages.dev')

    def test_noindex_excluded_and_robots_rules_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'index.html').write_text('<h1>Home</h1>')
            (root / 'private.html').write_text('<meta name="robots" content="noindex">')
            (root / 'robots.txt').write_text('User-agent: *\nDisallow: /private\nSitemap: https://old.test/sitemap.xml')
            write_seo_files(root, 'https://new.pages.dev')
            self.assertNotIn('private', (root / 'sitemap.xml').read_text())
            self.assertIn('Disallow: /private', (root / 'robots.txt').read_text())
