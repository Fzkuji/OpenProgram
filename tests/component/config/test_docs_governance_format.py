"""A page that breaks the format is caught, named, and cannot reach the site."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.docs_site import build, generate_reference, nav
from scripts.docs_site.governance import corpus, format_checks, policy
from scripts.docs_site.governance.format_checks import DocsFormatError
from scripts.docs_site.governance.model import Scope

GOOD = ('<title>Topic</title>\n<meta name="description" content="A topic.">\n'
        '<h1>Topic</h1>\n<h2 id="a">A</h2>\n<p>Words.</p>\n')
ZH = '<title>主题</title>\n<meta name="description" content="主题。">\n<h1>主题</h1>\n<h2 id="a">甲</h2>\n<p>文字。</p>\n'


def write(docs: Path, rel: str, text: str) -> None:
    (docs / rel).parent.mkdir(parents=True, exist_ok=True)
    (docs / rel).write_text(text, encoding='utf-8')


@pytest.fixture
def docs(tmp_path, monkeypatch):
    root = tmp_path / 'docs'
    write(root, 'start/topic.html', GOOD)
    write(root, 'start/topic.zh.html', ZH)
    monkeypatch.setattr(nav, 'TAB_SECTIONS', {'start': [('S', '甲', ['start/topic.html', 'start/other.html'])]})
    monkeypatch.setattr(policy, 'PROTOTYPE_PAGES', frozenset())
    return root


def rules(docs: Path) -> dict[str, list]:
    found: dict[str, list] = {}
    for finding in format_checks.check_docs_root(docs):
        found.setdefault(finding.rule, []).append(finding)
    return found


def test_a_clean_pair_passes(docs):
    assert rules(docs) == {}


@pytest.mark.parametrize('markup, rule, line', [
    ('<style>p{}</style>', 'format.style-element', 6),
    ('<script>1</script>', 'format.script-element', 6),
    ('<link rel="stylesheet" href="a.css">', 'format.stylesheet-link', 6),
    ('<p style="color:red">x</p>', 'format.style-attribute', 6),
    ('<h1>Again</h1>', 'format.multiple-h1', 6),
    ('<p>中文段落</p>', 'format.cjk-in-english', 6),
])
def test_each_markup_violation_names_its_line_and_fix(docs, markup, rule, line):
    write(docs, 'start/topic.html', GOOD + markup + '\n')
    finding = rules(docs)[rule][0]
    assert (finding.path, finding.line) == ('docs/start/topic.html', line)
    assert 'Fix:' in finding.render() and finding.fix


@pytest.mark.parametrize('remove, rule', [
    ('<title>Topic</title>\n', 'format.missing-title'),
    ('<meta name="description" content="A topic.">\n', 'format.missing-description'),
    ('<h1>Topic</h1>\n', 'format.missing-h1'),
])
def test_required_parts(docs, remove, rule):
    write(docs, 'start/topic.html', GOOD.replace(remove, ''))
    assert rule in rules(docs)


def test_chinese_page_is_held_to_the_same_markup_rules(docs):
    write(docs, 'start/topic.zh.html', ZH + '<p style="x">甲</p>\n')
    assert rules(docs)['format.style-attribute'][0].path == 'docs/start/topic.zh.html'


def test_missing_pair_and_orphan(docs):
    (docs / 'start/topic.zh.html').unlink()
    write(docs, 'start/other.zh.html', ZH)
    found = rules(docs)
    assert 'format.missing-pair' in found
    assert found['format.orphan-zh'][0].path == 'docs/start/other.zh.html'


def test_different_structure_between_languages(docs):
    write(docs, 'start/topic.zh.html', ZH + '<ul><li>多</li></ul>\n')
    finding = rules(docs)['format.structure-mismatch'][0]
    assert finding.path == 'docs/start/topic.html' and 'topic.zh.html' in finding.message


def test_a_translated_paragraph_may_differ_without_a_mismatch(docs):
    write(docs, 'start/topic.zh.html', ZH + '<p>第二段。</p>\n')
    assert rules(docs) == {}


def test_unregistered_page(docs):
    write(docs, 'capabilities/new.html', GOOD)
    write(docs, 'capabilities/new.zh.html', ZH)
    finding = rules(docs)['format.unregistered'][0]
    assert 'capabilities/new.html' in finding.fix


def test_handwritten_markdown_is_refused_but_generated_reference_is_not(docs):
    write(docs, 'start/notes.md', '# Notes\n')
    write(docs, 'reference/config-keys.md', '# Keys\n')
    found = rules(docs)['format.markdown-source']
    assert [f.path for f in found] == ['docs/start/notes.md']


def test_prototype_is_exempt_only_while_it_has_a_script(docs, monkeypatch):
    monkeypatch.setattr(policy, 'PROTOTYPE_PAGES', frozenset({'start/topic.html'}))
    write(docs, 'start/topic.html', GOOD + '<script>1</script><style>p{}</style>\n')
    assert rules(docs) == {}
    write(docs, 'start/topic.html', GOOD)
    assert 'format.prototype-stale' in rules(docs)


def test_only_pages_in_scope_are_judged(docs):
    write(docs, 'start/other.html', '<style>p{}</style>')
    write(docs, 'start/other.zh.html', ZH)
    scope = Scope('paths', docs.parent, ('docs/start/topic.html',))
    assert list(format_checks.check(corpus.load(scope))) == []


@pytest.fixture
def built(docs, monkeypatch):
    monkeypatch.setattr(build, 'DOCS_ROOT', docs)
    monkeypatch.setattr(build, 'OUT_ROOT', docs / '_site')
    monkeypatch.setattr(generate_reference, 'generate_all', lambda: None)
    monkeypatch.delenv('OPENPROGRAM_DOCS_FORMAT', raising=False)
    return docs


def test_build_refuses_a_bad_page_and_keeps_the_published_site(built):
    assert build.build() == 0
    published = (built / '_site/start/topic.html').read_bytes()
    write(built, 'start/topic.html', GOOD + '<style>p{}</style>\n')
    with pytest.raises(DocsFormatError) as caught:
        build.build()
    assert 'docs/start/topic.html:6: error format.style-element' in str(caught.value)
    assert (built / '_site/start/topic.html').read_bytes() == published
    assert not list(built.glob('_site/**/*.raw.html'))


def test_quarantine_publishes_a_visible_error_page_and_succeeds(built, monkeypatch):
    write(built, 'start/topic.html', GOOD + '<style>p{}</style>\n')
    monkeypatch.setenv('OPENPROGRAM_DOCS_FORMAT', 'quarantine')
    assert build.build() == 0
    page = (built / '_site/start/topic.html').read_text(encoding='utf-8')
    assert 'failed the docs format check' in page and 'format.style-element' in page
    assert 'viz-frame' not in page


def test_a_declared_prototype_keeps_the_iframe_path(built, monkeypatch):
    monkeypatch.setattr(policy, 'PROTOTYPE_PAGES', frozenset({'start/topic.html'}))
    write(built, 'start/topic.html', '<html><head><title>T</title><meta name="description" content="d"></head>'
                                     '<body><h1>T</h1><script>1</script></body></html>')
    write(built, 'start/topic.zh.html', '<html><head><title>甲</title><meta name="description" content="乙"></head>'
                                        '<body><h1>甲</h1><script>1</script></body></html>')
    assert build.build() == 0
    assert 'viz-frame' in (built / '_site/start/topic.html').read_text(encoding='utf-8')
