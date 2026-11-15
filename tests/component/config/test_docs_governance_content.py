"""The content rules catch what they name, at their thresholds."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.docs_site import nav
from scripts.docs_site.governance import baseline, content_checks, corpus, links, policy
from scripts.docs_site.governance.model import ERROR, WARNING, Finding, Scope

HEAD = '<title>T</title><meta name="description" content="d"><h1>T</h1>\n'
SENTENCE = 'Requests travel through the gateway before reaching the worker pool quickly.'


def write(root: Path, rel: str, text: str) -> None:
    path = root / 'docs' / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def page(body: str) -> str:
    return HEAD + body


def findings(root: Path, rule: str, mode: str = 'all', changed: tuple = ()) -> list[Finding]:
    scope = Scope(mode, root, changed)
    return [f for f in content_checks.check(corpus.load(scope)) if f.rule == rule]


def words(count: int) -> str:
    return ' '.join(f'word{i}' for i in range(count))


@pytest.mark.parametrize('count, severity', [(81, ERROR), (61, WARNING)])
def test_long_paragraph(tmp_path, count, severity):
    write(tmp_path, 'a.html', page(f'<p>{words(count)}</p>'))
    assert findings(tmp_path, 'content.paragraph')[0].severity == severity


def test_paragraph_at_the_warning_line_passes(tmp_path):
    write(tmp_path, 'a.html', page(f'<p>{words(60)}</p>'))
    assert findings(tmp_path, 'content.paragraph') == []


def test_table_text_is_not_prose(tmp_path):
    write(tmp_path, 'a.html', page(f'<table><tr><td>{words(200)}</td></tr></table>'))
    assert findings(tmp_path, 'content.paragraph') == []


def test_page_prose_and_share(tmp_path):
    write(tmp_path, 'a.html', page(''.join(f'<p>{words(50)}</p>' for _ in range(41))))
    assert findings(tmp_path, 'content.page-prose')[0].severity == ERROR
    assert findings(tmp_path, 'content.prose-share')[0].severity == ERROR


def test_prose_share_ignores_a_short_page(tmp_path):
    write(tmp_path, 'a.html', page(f'<p>{words(100)}</p>'))
    assert findings(tmp_path, 'content.prose-share') == []


def test_same_sentence_twice_on_a_page(tmp_path):
    write(tmp_path, 'a.html', page(f'<p>{SENTENCE}</p><p>Other words.</p><p>{SENTENCE}</p>'))
    assert len(findings(tmp_path, 'content.duplicate-sentence')) == 1


def test_same_sentence_on_another_page_names_it_only_when_the_page_is_in_scope(tmp_path):
    write(tmp_path, 'a.html', page(f'<p>{SENTENCE}</p>'))
    write(tmp_path, 'b.html', page(f'<p>{SENTENCE}</p>'))
    found = findings(tmp_path, 'content.duplicate-across-pages', 'paths', ('docs/a.html',))
    assert [f.path for f in found] == ['docs/a.html'] and 'docs/b.html' in found[0].message
    assert findings(tmp_path, 'content.duplicate-across-pages', 'paths', ('docs/c.html',)) == []


def test_near_duplicate_is_found_but_a_different_sentence_is_not(tmp_path):
    write(tmp_path, 'a.html', page(f'<p>{SENTENCE}</p>'))
    write(tmp_path, 'b.html', page('<p>Requests travel through the gateway before reaching the worker pool.</p>'))
    write(tmp_path, 'c.html', page('<p>Colours follow theme tokens across every generated chart surface.</p>'))
    paths = {f.path for f in findings(tmp_path, 'content.duplicate-across-pages')}
    assert paths == {'docs/a.html', 'docs/b.html'}


def test_full_sentence_table_cell_repeats_across_pages(tmp_path):
    write(tmp_path, 'a.html', page(f'<table><tr><td>{SENTENCE}</td></tr></table>'))
    write(tmp_path, 'b.html', page(f'<p>{SENTENCE}</p>'))
    assert {f.path for f in findings(tmp_path, 'content.duplicate-across-pages')} == {'docs/a.html', 'docs/b.html'}


def test_sentence_a_table_already_says(tmp_path):
    write(tmp_path, 'a.html', page(
        '<table><tr><th>Gateway</th><td>requests travel through before reaching worker pool quickly</td></tr></table>'
        f'<p>{SENTENCE}</p>'))
    assert findings(tmp_path, 'content.repeats-figure')[0].severity == ERROR


def test_definition_list_entry_is_not_a_sentence_repeating_a_table(tmp_path):
    write(tmp_path, 'a.html', page(
        '<table><tr><th>Gateway</th><td>requests travel through before reaching worker pool quickly</td></tr></table>\n'
        f'<dl><dt>Route</dt><dd>{SENTENCE}</dd></dl>'))
    assert not findings(tmp_path, 'content.repeats-figure')


def test_sentence_a_figure_already_says(tmp_path):
    write(tmp_path, 'a.html', page(
        '<figure><svg aria-label="x"><text>requests travel through gateway before reaching worker pool quickly</text></svg></figure>'
        f'<p>{SENTENCE}</p>'))
    assert findings(tmp_path, 'content.repeats-figure')


def test_bare_bullet_section(tmp_path):
    items = ''.join(f'<li>{i}</li>' for i in range(8))
    write(tmp_path, 'a.html', page(f'<h2>Things</h2><ul>{items}</ul><p class="note">n</p>'))
    found = findings(tmp_path, 'content.bullet-section')
    assert found[0].severity == ERROR and 'Things' in found[0].message


def test_bullets_beside_a_table_are_a_form_of_their_own(tmp_path):
    items = ''.join(f'<li>{i}</li>' for i in range(12))
    write(tmp_path, 'a.html', page(f'<h2>Things</h2><table></table><ul>{items}</ul>'))
    assert findings(tmp_path, 'content.bullet-section') == []


def test_every_page_of_a_tab_in_one_form_only_in_the_full_sweep(tmp_path):
    for index in range(12):
        write(tmp_path, f'capabilities/p{index}.html', page('<table></table><p>x</p>'))
    found = findings(tmp_path, 'content.uniform-tab')
    assert found[0].severity == ERROR and found[0].path == 'docs/capabilities'
    assert findings(tmp_path, 'content.uniform-tab', 'paths', ('docs/capabilities/p0.html',)) == []


def git(root: Path, *args: str) -> None:
    subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.email', 't@t')
    git(tmp_path, 'config', 'user.name', 't')
    write(tmp_path, 'a.html', page(f'<p>{words(90)}</p>'))
    return tmp_path


def entry(rule: str = 'content.paragraph', reason: str = 'A reason that is long enough here.', subject=None) -> dict:
    from scripts.docs_site.governance.model import subject_hash
    return {'rule': rule, 'path': 'docs/a.html', 'subject': subject_hash(subject if subject is not None else words(90)),
            'reason': reason}


def apply(repo: Path, entries: list[dict], commit: bool = False, finding: bool = True) -> list[Finding]:
    target = repo / baseline.PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(entries), encoding='utf-8')
    if commit:
        git(repo, 'add', '-A')
        git(repo, 'commit', '-qm', 'x')
    scope = Scope('all', repo)
    found = [f for f in content_checks.check(corpus.load(scope))] if finding else []
    return baseline.apply(found, corpus.load(scope))


def test_baseline_suppresses_only_its_entry(repo):
    assert apply(repo, [entry()], commit=True) == []


def test_baseline_only_shrinks(repo):
    apply(repo, [], commit=True)
    rules = [f.message for f in apply(repo, [entry()])]
    assert any('only shrinks' in message for message in rules)


def test_baseline_entry_that_no_longer_matches_must_go(repo):
    apply(repo, [entry()], commit=True)
    write(repo, 'a.html', page('<p>short</p>'))
    assert any('no longer matches' in f.message for f in apply(repo, [entry()]))


def test_editing_the_text_brings_the_finding_back(repo):
    apply(repo, [entry()], commit=True)
    write(repo, 'a.html', page(f'<p>{words(95)}</p>'))
    found = apply(repo, [entry()])
    assert any(f.rule == 'content.paragraph' and f.severity == ERROR for f in found)


def test_baseline_needs_a_reason_and_a_content_rule(repo):
    found = apply(repo, [entry(reason='short'), entry(rule='format.style-element')], commit=True)
    messages = ' '.join(f.message for f in found)
    assert 'no real reason' in messages and 'cannot be baselined' in messages


def test_format_findings_are_never_suppressed(repo):
    entries = [entry(rule='format.style-element', subject='')]
    apply(repo, entries, commit=True)
    finding = Finding('format.style-element', ERROR, 'docs/a.html', 1, 'm', 'f')
    assert finding in baseline.apply([finding], corpus.load(Scope('all', repo)))


def test_shipped_baseline_is_short_and_reasoned():
    root = Path(__file__).resolve().parents[3]
    entries = json.loads((root / baseline.PATH).read_text(encoding='utf-8'))
    assert len(entries) <= 10
    assert all(e['rule'].startswith('content.') and len(e['reason']) >= policy.BASELINE_REASON_MIN for e in entries)


# links: the build's relative rules, checked in the sources


@pytest.fixture
def linked(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, 'PROTOTYPE_PAGES', frozenset())
    write(tmp_path, 'start/a.html', page('<h2>Part one</h2><p>x</p>'))
    write(tmp_path, 'start/b.html', page('<p><a href="a.html#part-one">ok</a> <a href="/docs/start/a.html">ok</a></p>'))
    return tmp_path


def link_rules(root: Path, changed=None):
    scope = Scope('paths', root, changed) if changed else Scope('all', root)
    return sorted((f.rule, f.path, f.line) for f in links.check(corpus.load(scope)))


def test_valid_links_pass(linked):
    assert link_rules(linked) == []


def test_missing_page_directory_and_fragment_are_caught(linked):
    write(linked, 'start/c.html', page('<p><a href="gone.html">x</a></p>\n<p><a href="a.html#nope">y</a></p>\n'
                                       '<p><a href="../start/">z</a></p>'))
    assert link_rules(linked) == [('links.broken', 'docs/start/c.html', 2), ('links.broken', 'docs/start/c.html', 4),
                                  ('links.fragment', 'docs/start/c.html', 3)]


def test_link_shown_as_code_is_not_a_link(linked):
    write(linked, 'start/c.html', page('<code>href="gone.html"</code>'))
    assert link_rules(linked) == []


def test_deleting_a_page_reports_every_page_still_linking_to_it(linked):
    (linked / 'docs/start/a.html').unlink()
    assert link_rules(linked, ('docs/start/a.html',)) == [
        ('links.broken', 'docs/start/b.html', 2), ('links.broken', 'docs/start/b.html', 2)]


def test_another_pages_broken_link_is_not_this_changes_problem(linked):
    write(linked, 'start/c.html', page('<a href="gone.html">x</a>'))
    assert link_rules(linked, ('docs/start/a.html',)) == []


def test_redirects_and_generated_reference_pages_are_valid_targets(linked):
    (linked / 'docs/redirects.json').write_text(json.dumps({'old.html': 'start/a.html'}), encoding='utf-8')
    write(linked, 'start/c.html', page('<a href="../old.html">x</a> <a href="../reference/cli/run.html">y</a>'))
    assert link_rules(linked) == []
