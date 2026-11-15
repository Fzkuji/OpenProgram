"""HTML document sources render inside the site chrome with every site feature."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.docs_site import build, generate_reference, html_source, nav
from scripts.docs_site.governance import policy

PAGE = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Update flow</title>
<meta name="description" content="How a commit reaches the stable instance.">
</head><body><a class="skip" href="#main">Skip</a><main id="main">
<h1>Update flow</h1>
<figure>
<svg class="fig-wide" viewBox="0 0 852 100" role="img" aria-label="Commit, then deploy."><text>Wide label</text></svg>
<ol class="flow fig-narrow"><li><strong>Commit</strong></li><li class="via"><code translate="no">dev deploy</code></li></ol>
</figure>
<h2>Commands</h2>
<table class="stack fixed"><thead class="sr"><tr><th scope="col">Command</th><th scope="col">Does</th></tr></thead>
<tbody><tr><th scope="row"><code>status</code><span class="arg"><code>--json</code></span></th><td>See <a href="other.html#part">other</a></td></tr>
<tr><td colspan="2">Both | columns</td></tr></tbody></table>
<details><summary>Rare</summary><h2>Hidden heading</h2><pre><code>a &lt; b
  c</code></pre></details>
<p class="next"><a href="other.html">Other</a></p>
</main></body></html>
'''


@pytest.fixture
def site(tmp_path: Path, monkeypatch):
    start = tmp_path / 'start'
    start.mkdir()
    head = '<title>{0}</title><meta name="description" content="{0}">'
    (start / 'flow.html').write_text(PAGE, encoding='utf-8')
    (start / 'flow.zh.html').write_text(PAGE.replace('Update flow', '更新流程'), encoding='utf-8')
    (start / 'other.html').write_text(head.format('Other') + '<h1>Other</h1><h2 id="part">Part</h2>', encoding='utf-8')
    (start / 'other.zh.html').write_text(head.format('其他') + '<h1>其他</h1><h2 id="part">部分</h2>', encoding='utf-8')
    legacy = '<html><head>' + head.format('{0}') + '<script>1</script></head><body><h1>{0}</h1><p>Old</p></body></html>'
    (start / 'legacy.html').write_text(legacy.format('Legacy'), encoding='utf-8')
    (start / 'legacy.zh.html').write_text(legacy.format('旧'), encoding='utf-8')
    reference = tmp_path / 'reference'
    reference.mkdir()
    (reference / 'config-keys.md').write_text('# Written\n\nBody\n', encoding='utf-8')
    monkeypatch.setattr(policy, 'PROTOTYPE_PAGES', frozenset({'start/legacy.html'}))
    monkeypatch.setattr(nav, 'TAB_SECTIONS', {
        'start': [('S', '甲', ['start/flow.html', 'start/other.html', 'start/legacy.html'])]})
    monkeypatch.setattr(build, 'DOCS_ROOT', tmp_path)
    monkeypatch.setattr(build, 'OUT_ROOT', tmp_path / '_site')
    monkeypatch.setattr(generate_reference, 'generate_all', lambda: None)
    assert build.build() == 0
    return tmp_path / '_site'


def test_html_document_renders_in_the_chrome_with_contents_and_links(site):
    page = (site / 'start/flow.html').read_text(encoding='utf-8')
    assert '<article class="doc">' in page
    assert 'viz-frame' not in page
    assert not (site / 'start/flow.raw.html').exists()
    assert 'class="skip" href="#main"' not in page and '<!doctype html>\n<html lang="en"><head>' not in page
    assert '<title>Update flow · OpenProgram Docs</title>' in page
    assert 'content="How a commit reaches the stable instance."' in page
    assert '<h2 id="commands">' in page and 'href="#commands"' in page
    assert 'href="#hidden-heading"' not in page
    assert 'href="/docs/start/other.html#part"' in page
    assert 'data-alt-lang-url="/docs/start/flow.zh.html"' in page
    assert 'class="prevnext"' in page


def test_chinese_document_and_catalogs(site):
    zh = (site / 'start/flow.zh.html').read_text(encoding='utf-8')
    assert '<article class="doc">' in zh and 'data-page-lang="zh"' in zh
    assert 'start/flow.zh.html' in (site / 'sitemap.xml').read_text(encoding='utf-8')
    records = {r['url']: r for r in json.loads((site / 'search-index.json').read_text(encoding='utf-8'))}
    assert 'dev deploy' in records['start/flow.html']['text']
    assert 'Wide label' not in records['start/flow.html']['text']
    assert records['start/flow.zh.html']['lang'] == 'zh'


def test_a_declared_prototype_keeps_its_rendering_and_generated_markdown_its_own(site):
    assert 'viz-frame' in (site / 'start/legacy.html').read_text(encoding='utf-8')
    assert (site / 'start/legacy.raw.html').exists()
    assert not (site / 'start/legacy.html.md').exists()
    written = (site / 'reference/config-keys.html').read_text(encoding='utf-8')
    assert '<article>' in written
    assert (site / 'reference/config-keys.html.md').read_text(encoding='utf-8') == '# Written\n\nBody\n'


def test_markdown_rendition_keeps_content_and_counts_figures_once():
    build._SLUG_DEDUP = {}
    body = html_source.article_markup(PAGE).replace('href="other.html"', 'href="/docs/o.html"')
    text = html_source.to_markdown(
        body, link_base='/docs/', public_base='https://example.org/site/docs/')
    assert text.startswith('# Update flow\n')
    assert 'Wide label' not in text and 'Commit, then deploy' not in text
    assert '`dev deploy`' in text
    assert '| Command | Does |' in text
    assert '| `status` (`--json`) |' in text
    assert '| Both \\| columns | Both \\| columns |' in text
    assert '**Rare**' in text and '```\na < b\n  c\n```' in text
    assert '[Other](https://example.org/site/docs/o.html)' in text


def test_figure_without_a_narrow_twin_is_read_through_its_description():
    text = html_source.to_markdown('<figure><svg aria-label="Two boxes joined."><text>x</text></svg></figure>')
    assert text == 'Two boxes joined.\n'


def test_link_syntax_shown_as_code_is_not_rewritten():
    assert build.relink_internal('<code>href="a.html"</code>', Path('x')) == '<code>href="a.html"</code>'


@pytest.mark.parametrize('source', [
    '<style>p{}</style><h1>A</h1>',
    '<h1>A</h1><script>1</script>',
    '<link rel="stylesheet" href="a.css"><h1>A</h1>',
    '<h1 style="color:red">A</h1>',
])
def test_page_styling_is_not_a_plain_document(source):
    assert not html_source.is_html_document(source)


def test_style_shown_as_code_is_still_a_document():
    assert html_source.is_html_document('<h1>A</h1><pre>&lt;style&gt; style="x"</pre>')


def test_author_heading_ids_are_kept_and_duplicates_differ():
    build._SLUG_DEDUP = {}
    body = html_source.add_heading_ids(
        '<h2 id="kept">A</h2><h2>Same</h2><h3 class="x">Same</h3>', build._make_unique_slug)
    assert body == '<h2 id="kept">A</h2><h2 id="same">Same</h2><h3 id="same-1" class="x">Same</h3>'


def test_moved_page_keeps_its_registered_title_section_and_order(tmp_path, monkeypatch):
    (tmp_path / 'install').mkdir()
    for name in ('install.html', 'upgrade.html'):
        (tmp_path / 'install' / name).write_text('<h1>Long source heading</h1>', encoding='utf-8')
    (tmp_path / 'README.html').write_text('<h1>Home</h1>', encoding='utf-8')
    monkeypatch.setattr(nav, 'ROOT_PAGE_GROUPS', {'README.md': ('Overview', 'start')})
    monkeypatch.setattr(nav, 'ROOT_PAGE_TAB', {'README.md': 'start'})
    monkeypatch.setattr(nav, 'TAB_SECTIONS', {'install': [
        ('Install', '安装', ['install/install.md']),
        ('Maintenance', '维护', ['install/upgrade.md']),
    ]})
    monkeypatch.setattr(nav, 'PAGE_ORDER', {'install/install.md': 0, 'install/upgrade.md': 1})
    monkeypatch.setattr(nav, 'PAGE_TITLES', {})
    pages = nav.discover(tmp_path)
    assert {p.key for p in pages} == {'install/install.md', 'install/upgrade.md', 'README.md'}
    readme = next(p for p in pages if p.is_readme)
    assert (readme.title, nav.tab_of(readme)) == ('Overview', 'start')
    install = next(tab for tab in nav.build_tabs(tmp_path, pages) if tab.key == 'install')
    assert [(s.title, [p.rel.as_posix() for p in s.pages]) for s in install.sections] == [
        ('Install', ['install/install.html']), ('Maintenance', ['install/upgrade.html']),
    ]


def test_markdown_beside_an_html_companion_keeps_its_own_registration(tmp_path):
    (tmp_path / 'install').mkdir()
    (tmp_path / 'install/install.md').write_text('# Text\n', encoding='utf-8')
    (tmp_path / 'install/install.html').write_text('<h1>Diagram</h1>', encoding='utf-8')
    keys = {p.rel.as_posix(): p.key for p in nav.discover(tmp_path)}
    assert keys == {'install/install.md': 'install/install.md', 'install/install.html': 'install/install.html'}
