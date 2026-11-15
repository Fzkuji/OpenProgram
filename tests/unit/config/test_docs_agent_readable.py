from html.parser import HTMLParser

from scripts.docs_site import build
from scripts.docs_site.template import render_page


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == 'link':
            self.links.append(dict(attrs))


def test_shell_advertises_source_and_guide_without_corrupting_urls():
    links = Links(render_page(
        title='Example', body_html='', nav_html='', toc_html='', base='/docs/',
        markdown_url='https://example.org/docs/a.html.md?a=1&b=2',
        llms_url='https://example.org/llms.txt',
    )).links
    assert {'rel': 'alternate', 'type': 'text/markdown',
            'href': 'https://example.org/docs/a.html.md?a=1&b=2'} in links
    assert {'rel': 'describedby', 'href': 'https://example.org/llms.txt'} in links


def test_html_shell_does_not_invent_markdown_source():
    links = Links(render_page(
        title='Example', body_html='', nav_html='', toc_html='', base='/docs/',
    )).links
    assert not any(link.get('type') == 'text/markdown' for link in links)


def test_raw_visualization_uses_configured_origin_and_retains_readability(monkeypatch):
    monkeypatch.setattr(build, 'SITE_URL', 'https://example.org/')
    text = build.add_raw_readability('<html><HEAD></HEAD><body>Figure</body></html>')
    assert {'rel': 'describedby', 'href': 'https://example.org/llms.txt'} in Links(text).links
    assert 'raw-readability.css' in text
    assert 'raw-readability.js' in text
    assert '<body>Figure</body>' in text


def test_html_documents_publish_a_markdown_rendition_and_advertise_it(tmp_path, monkeypatch):
    from scripts.docs_site import generate_reference

    (tmp_path / 'start').mkdir()
    (tmp_path / 'start/topic.html').write_text(
        '<title>Topic</title><h1>Topic</h1><p>Body</p>', encoding='utf-8')
    (tmp_path / 'start/topic.zh.html').write_text(
        '<title>主题</title><h1>主题</h1><p>正文</p>', encoding='utf-8')
    monkeypatch.setattr(build, 'DOCS_ROOT', tmp_path)
    monkeypatch.setattr(build, 'OUT_ROOT', tmp_path / '_site')
    monkeypatch.setattr(build, 'SITE_URL', 'https://example.org/')
    monkeypatch.setattr(generate_reference, 'generate_all', lambda: None)
    monkeypatch.setattr(build, 'format_gate', dict)  # the pages here are minimal on purpose
    assert build.build() == 0
    for name, text in (('topic.html', '# Topic\n\nBody\n'), ('topic.zh.html', '# 主题\n\n正文\n')):
        assert (tmp_path / '_site/start' / f'{name}.md').read_text(encoding='utf-8') == text
        links = Links((tmp_path / '_site/start' / name).read_text(encoding='utf-8')).links
        assert {'rel': 'alternate', 'type': 'text/markdown',
                'href': f'https://example.org/docs/start/{name}.md'} in links
