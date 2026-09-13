"""Conservative URL normalization for multi-page static HTML sites."""
import json
import re
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urljoin, unquote, quote


def verification_page(path):
    return bool(re.fullmatch(r'(?:naver|google)[\w-]+\.html', path.name, re.I))


def public_path(relative):
    path = '/' + relative
    if path.endswith('/index.html'):
        path = path[:-10]
    elif path.endswith('.html'):
        path = path[:-5]
    return quote(path, safe='/')


class Tags(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def normalize_site(root: Path, base: str):
    root = root.resolve()
    base = base.rstrip('/')
    parts = urlsplit(base)
    if parts.scheme != 'https' or not parts.netloc or parts.path or parts.query or parts.fragment or parts.username:
        raise ValueError('배포 주소는 https://도메인 형식이어야 합니다.')
    files = [p for p in root.rglob('*.html') if not verification_page(p)]
    origins = set()
    for path in files:
        for tag, attrs in Tags(path.read_text(encoding='utf-8-sig')).tags:
            if tag == 'link' and attrs.get('rel', '').lower() == 'canonical':
                old = urlsplit(attrs.get('href', ''))
                if old.scheme in {'http', 'https'} and old.netloc:
                    origins.add(f'{old.scheme}://{old.netloc}')
    if len({urlsplit(o).netloc for o in origins} - {parts.netloc}) > 1:
        raise ValueError('여러 이전 대표 도메인이 발견되었습니다. 원본 canonical을 확인해 주세요.')
    origins.add(base)
    urls = {}
    errors = []

    for path in files:
        relative = path.relative_to(root).as_posix()
        canonical = base + public_path(relative)
        text = path.read_text(encoding='utf-8-sig')
        tags = Tags(text).tags
        excluded = path.name in {'404.html', '500.html'} or any(
            tag == 'meta' and attrs.get('name', '').lower() in {'robots', 'googlebot'}
            and 'noindex' in attrs.get('content', '').lower() for tag, attrs in tags)
        if not excluded:
            urls[public_path(relative)] = relative

        def rewrite(value, check=False):
            parsed = urlsplit(value)
            if parsed.scheme and parsed.scheme not in {'http', 'https'}:
                return value
            if value.startswith('#'):
                return value
            absolute = urlsplit(urljoin(base + '/' + relative, value))
            origin = f'{absolute.scheme}://{absolute.netloc}'
            if origin not in origins:
                return value
            decoded = unquote(absolute.path).lstrip('/')
            target = (root / decoded).resolve()
            if not target.is_relative_to(root.resolve()):
                raise ValueError('사이트 외부 경로: ' + value)
            candidates = [target, target / 'index.html', Path(str(target) + '.html')]
            found = next((p for p in candidates if p.is_file()), None)
            if check and not found:
                errors.append(f'{relative}: 파일 없음 {value}')
            route = absolute.path
            if found and found.suffix == '.html' and not verification_page(found):
                route = public_path(found.relative_to(root).as_posix())
            return urlunsplit((parts.scheme, parts.netloc, route, absolute.query, absolute.fragment))

        def transform_json(value):
            if isinstance(value, dict):
                return {k: transform_json(v) for k, v in value.items()}
            if isinstance(value, list):
                return [transform_json(v) for v in value]
            if isinstance(value, str) and value.startswith(('https://', 'http://', '//')):
                return rewrite(value)
            return value

        class Rewriter(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=False)
                self.output = []
                self.json_script = False
                self.canonical_written = False

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'link' and attrs.get('rel', '').lower() == 'canonical':
                    if not excluded and not self.canonical_written:
                        self.output.append(f'<link rel="canonical" href="{escape(canonical, quote=True)}">')
                        self.canonical_written = True
                    return
                self.json_script = tag == 'script' and attrs.get('type') == 'application/ld+json'
                for key in ('href', 'src', 'action', 'poster'):
                    if attrs.get(key):
                        attrs[key] = rewrite(attrs[key], check=key in {'href', 'src', 'poster'})
                if tag == 'meta' and attrs.get('property', attrs.get('name', '')) in {'og:url', 'og:image', 'twitter:image'}:
                    attrs['content'] = canonical if attrs.get('property') == 'og:url' else rewrite(attrs.get('content', ''), check=True)
                self.output.append('<' + tag + ''.join(' ' + k + ('' if v is None else '="' + escape(v, quote=True) + '"') for k, v in attrs.items()) + '>')

            def handle_endtag(self, tag):
                if tag == 'head' and not excluded and not self.canonical_written:
                    self.output.append(f'<link rel="canonical" href="{escape(canonical, quote=True)}">')
                    self.canonical_written = True
                self.output.append(f'</{tag}>')
                if tag == 'script':
                    self.json_script = False

            def handle_data(self, data):
                self.output.append(json.dumps(transform_json(json.loads(data)), ensure_ascii=False).replace('</', '<\\/') if self.json_script and data.strip() else data)

            def handle_entityref(self, name): self.output.append('&' + name + ';')
            def handle_charref(self, name): self.output.append('&#' + name + ';')
            def handle_comment(self, data): self.output.append('<!--' + data + '-->')
            def handle_decl(self, data): self.output.append('<!' + data + '>')

        parser = Rewriter()
        parser.feed(text)
        result = ''.join(parser.output)
        if not excluded and not any(tag == 'head' for tag, _ in tags):
            result = f'<head><link rel="canonical" href="{escape(canonical, quote=True)}"></head>' + result
        for old in origins - {base}:
            if old in result:
                errors.append(f'{relative}: 자동 수정하지 못한 이전 주소 {old}')
        path.write_text(result, encoding='utf-8')
    if errors:
        raise ValueError('배포 전 검사 실패:\n' + '\n'.join(errors[:20]))
    if not (root / '404.html').exists():
        (root / '404.html').write_text('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="robots" content="noindex"><title>404 Not Found</title></head><body><h1>404 Not Found</h1><a href="/">Home</a></body></html>', encoding='utf-8')
    return urls
