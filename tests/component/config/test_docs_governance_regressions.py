"""Faults a change can introduce, each seeded once: the check must name them."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.docs_site import build, check as entry, checklang, hook_status, nav
from scripts.docs_site.governance import acknowledgements, content_checks, corpus, format_checks, policy
from scripts.docs_site.governance.model import ERROR, WARNING, Finding, Scope
from scripts.testing import affected_tests

ROOT = Path(__file__).resolve().parents[3]
HEAD = '<title>T</title>\n<meta name="description" content="d">\n<meta name="covers" content="none">\n<h1>T</h1>\n'
ZH = '<title>甲</title>\n<meta name="description" content="乙">\n<h1>甲</h1>\n'
BASELINE = 'scripts/docs_site/governance/baseline.json'
POLICY = 'scripts/docs_site/governance/policy.py'
SETTINGS = 'openprogram/state/settings.py'
SERVER = 'openprogram/interfaces/server/routes/x.py'


def git(root: Path, *args: str) -> str:
    done = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=root, check=True,
                          capture_output=True, text=True)
    return done.stdout.strip()


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding='utf-8')


def commit(root: Path, message: str = 'x') -> str:
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', message, '--allow-empty')
    return git(root, 'rev-parse', 'HEAD')


def page(body: str = '<p>x</p>\n') -> str:
    return HEAD + body


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'dev')
    git(root, 'config', 'user.email', 't@t')
    git(root, 'config', 'user.name', 't')
    write(root, {'docs/start/a.html': page(), 'docs/start/a.zh.html': ZH, 'docs/start/b.html': page(),
                 'docs/start/b.zh.html': ZH, 'code.py': 'x = 1\n'})
    commit(root)
    monkeypatch.setattr(nav, 'TAB_SECTIONS', {'start': [('S', '甲', [
        'start/a.html', 'start/b.html', 'start/c.html', 'start/n.html'])]})
    monkeypatch.setattr(policy, 'PROTOTYPE_PAGES', frozenset())
    monkeypatch.setattr(hook_status, 'stale_hooks', lambda _root: [])
    monkeypatch.chdir(root)
    return root


def linked(root: Path) -> Path:
    tree = root.parent / 'linked'
    git(root, 'worktree', 'add', '-q', '-b', 'side', str(tree))
    return tree


def run(root: Path, mode='all', changed=(), base=None, **kwargs):
    return entry.run(Scope(mode, root, tuple(changed), base, **kwargs))


def rules(report, severity=ERROR) -> set[str]:
    return {f.rule for f in report.findings if f.severity == severity}


def pages_of(report) -> list[str]:
    return [item.page for item in report.impact]


# ------------------------------------------------------------ one language deleted


@pytest.mark.parametrize('deleted, rule', [('docs/start/a.zh.html', 'format.missing-pair'),
                                           ('docs/start/a.html', 'format.orphan-zh')])
def test_deleting_one_language_is_found(repo, capsys, deleted, rule):
    tree = linked(repo)
    (tree / deleted).unlink()
    assert entry.main([], root=tree) == 1
    assert rule in capsys.readouterr().out
    assert entry.main([deleted], root=tree) == 1
    assert rule in capsys.readouterr().out


# ----------------------------------------------------------------------- moves


def test_a_staged_move_lists_both_paths(repo):
    git(repo, 'mv', 'code.py', 'moved.py')
    assert set(affected_tests.changed_files(repo)) == {'code.py', 'moved.py'}


def test_a_committed_move_under_base_lists_both_paths(repo):
    tree = linked(repo)
    base = git(tree, 'rev-parse', 'HEAD')
    git(tree, 'mv', 'code.py', 'moved.py')
    commit(tree)
    assert set(affected_tests.changed_files(tree, base)) == {'code.py', 'moved.py'}


# -------------------------------------------------------------------- baseline


def _big(words: int) -> str:
    return page(''.join(f'<p>{" ".join(f"w{i}x{j}" for j in range(30))}</p>\n' for i in range(words // 30)))


def _entry(path: str, ceiling: int, **more) -> dict:
    subject = Finding('content.page-prose', ERROR, path, 0, '', '', 'page-prose').key[2]
    return {'rule': 'content.page-prose', 'path': path, 'subject': subject, 'max': ceiling,
            'reason': 'a long enough reason for the entry', **more}


def test_growing_a_baselined_page_fails(repo):
    write(repo, {'docs/start/a.html': _big(2100), BASELINE: json.dumps([_entry('docs/start/a.html', 2100)])})
    commit(repo)
    assert 'content.page-prose' not in rules(run(repo))
    write(repo, {'docs/start/a.html': _big(2400)})
    found = rules(run(repo, 'paths', ['docs/start/a.html']))
    assert 'content.page-prose' in found


def test_raising_a_ceiling_is_refused(repo):
    write(repo, {'docs/start/a.html': _big(2100), BASELINE: json.dumps([_entry('docs/start/a.html', 2100)])})
    commit(repo)
    write(repo, {'docs/start/a.html': _big(2400), BASELINE: json.dumps([_entry('docs/start/a.html', 2400)])})
    assert 'baseline.invalid' in rules(run(repo, 'paths', ['docs/start/a.html']))


def test_a_new_entry_never_hides_a_finding_even_with_named_paths(repo):
    write(repo, {BASELINE: '[]'})
    commit(repo)
    write(repo, {'docs/start/a.html': _big(2100), BASELINE: json.dumps([_entry('docs/start/a.html', 2100)])})
    found = rules(run(repo, 'paths', ['docs/start/a.html']))
    assert {'content.page-prose', 'baseline.invalid'} <= found


def test_committing_the_baseline_alone_does_not_launder_it(repo):
    write(repo, {BASELINE: '[]'})
    published = commit(repo)
    git(repo, 'update-ref', 'refs/publish/last', published)
    write(repo, {'docs/start/a.html': _big(2100)})
    commit(repo)
    write(repo, {BASELINE: json.dumps([_entry('docs/start/a.html', 2100)])})
    commit(repo, 'smuggle')
    assert {'content.page-prose', 'baseline.invalid'} <= rules(run(repo))


# ---------------------------------------------------------------------- policy


def _policy(repo: Path, text: str) -> None:
    write(repo, {POLICY: text})


def test_a_new_prototype_page_needs_a_reason(repo):
    _policy(repo, 'PROTOTYPE_PAGES = frozenset({"a.html"})\nPARAGRAPH_WORDS = (80, 60)\n')
    commit(repo)
    _policy(repo, 'PROTOTYPE_PAGES = frozenset({"a.html", "b.html"})\nPARAGRAPH_WORDS = (80, 60)\n')
    assert 'policy.loosened' in rules(run(repo, 'paths', [POLICY]))
    _policy(repo, 'PROTOTYPE_PAGES = frozenset({"a.html", "b.html"})\nPARAGRAPH_WORDS = (80, 60)\n'
                  'LOOSENED_BECAUSE = {"b.html": "the interaction is the whole content here"}\n')
    assert 'policy.loosened' not in rules(run(repo, 'paths', [POLICY]))


def test_a_looser_threshold_is_refused_and_a_tighter_one_is_not(repo):
    _policy(repo, 'PARAGRAPH_WORDS = (80, 60)\n')
    commit(repo)
    _policy(repo, 'PARAGRAPH_WORDS = (90, 60)\n')
    assert 'policy.loosened' in rules(run(repo))
    _policy(repo, 'PARAGRAPH_WORDS = (70, 50)\n')
    assert 'policy.loosened' not in rules(run(repo))


# ------------------------------------------------------------------- content


@pytest.mark.parametrize('body', [
    '<div>{}</div>\n', '{}\n', '<figure><img src="x.png"><figcaption>{}</figcaption></figure>\n',
])
def test_long_text_counts_whatever_tag_holds_it(repo, body):
    text = ' '.join(f'word{i}' for i in range(300))
    write(repo, {'docs/start/a.html': page(body.format(text))})
    assert 'content.paragraph' in rules(run(repo, 'paths', ['docs/start/a.html']))


def test_a_long_table_cell_is_reported(repo):
    text = ' '.join(f'word{i}' for i in range(300))
    write(repo, {'docs/start/a.html': page(f'<table><tr><td>{text}</td></tr></table>\n')})
    assert 'content.table-cell' in rules(run(repo, 'paths', ['docs/start/a.html']))


def test_a_line_break_separates_sentences(repo):
    sentence = 'Requests travel through the gateway before reaching the worker pool quickly.'
    write(repo, {'docs/start/a.html': page(f'<p>{sentence}<br>{sentence}</p>\n')})
    assert 'content.duplicate-sentence' in rules(run(repo, 'paths', ['docs/start/a.html']))


def test_bullets_wrapped_in_a_container_are_still_bullets(repo):
    items = ''.join(f'<li>item {i}</li>' for i in range(8))
    write(repo, {'docs/start/a.html': page(f'<div><h2>Section</h2><ul>{items}</ul></div>\n')})
    assert 'content.bullet-section' in rules(run(repo, 'paths', ['docs/start/a.html']))


TABLE = ('<table><thead><tr><th>Mode</th><th>Commands run</th><th>Files read written</th></tr></thead><tbody>'
         '<tr><td>Docker</td><td>container conversation</td><td>computer stay</td></tr>'
         '<tr><td>Local</td><td>this machine</td><td>project folder</td></tr>'
         '<tr><td>Remote</td><td>server node</td><td>synced copy</td></tr>'
         '<tr><td>Extra</td><td>one</td><td>two</td></tr><tr><td>More</td><td>three</td><td>four</td></tr>'
         '</tbody></table>\n')


def test_a_sentence_using_the_column_headers_repeats_the_row(repo):
    sentence = 'Docker commands run in a container for this conversation, and files read and written stay on this computer.'
    write(repo, {'docs/start/a.html': page(TABLE + f'<p>{sentence}</p>\n')})
    assert 'content.repeats-figure' in rules(run(repo, 'paths', ['docs/start/a.html']))


def test_a_sentence_summarising_a_small_table_repeats_it(repo):
    table = ('<table><tr><th>Yellow</th><td>connecting paused</td></tr>'
             '<tr><th>Red</th><td>disconnected</td></tr><tr><th>Green</th><td>online</td></tr></table>\n')
    sentence = 'Yellow means connecting or paused, red means disconnected.'
    write(repo, {'docs/start/a.html': page(table + f'<p>{sentence}</p>\n')})
    assert 'content.repeats-figure' in rules(run(repo, 'paths', ['docs/start/a.html']))


def test_a_warning_names_its_band():
    assert 'warning above 60, error above 80' in next(content_checks._length(
        type('P', (), {'path': 'docs/x.html', 'tree': None})(), [(1, ' '.join(['a'] * 70))])).message


# -------------------------------------------------------------------- format


@pytest.mark.parametrize('body, rule', [
    ('<p onclick="x()">y</p>', 'format.event-handler'),
    ('<a href="javascript:x()">y</a>', 'format.javascript-url'),
    ('<iframe src="x"></iframe>', 'format.embed'),
    ('<object data="x"></object>', 'format.embed'),
])
def test_page_code_is_refused(repo, body, rule):
    write(repo, {'docs/start/a.html': page(body + '\n')})
    assert rule in rules(run(repo, 'paths', ['docs/start/a.html']))


def test_chinese_in_the_english_description_is_found():
    assert checklang.prose_violations('<meta name="description" content="中文说明">', markdown=False)


def test_dropping_a_page_from_nav_is_found_on_that_page(repo, monkeypatch):
    monkeypatch.setattr(nav, 'TAB_SECTIONS', {'start': [('S', '甲', ['start/a.html'])]})
    found = run(repo, 'paths', ['scripts/docs_site/nav.py']).findings
    assert any(f.rule == 'format.unregistered' and f.path == 'docs/start/b.html' for f in found)


def test_english_edit_without_chinese_warns(repo):
    write(repo, {'docs/start/a.html': page('<p>new text</p>\n')})
    assert 'format.zh-stale' in rules(run(repo, 'paths', ['docs/start/a.html']), WARNING)


def test_quarantine_is_only_for_unsafe_markup_in_that_language(monkeypatch):
    made = [Finding('format.script-element', ERROR, 'docs/x.zh.html', 1, 'm', 'f'),
            Finding('format.missing-pair', ERROR, 'docs/y.html', 1, 'm', 'f'),
            Finding('format.cjk-in-english', ERROR, 'docs/z.html', 1, 'm', 'f')]
    monkeypatch.setattr(build.format_checks, 'check_docs_root', lambda _root: made)
    monkeypatch.setenv('OPENPROGRAM_DOCS_FORMAT', 'quarantine')
    assert list(build.format_gate()) == ['docs/x.zh.html']


# --------------------------------------------------------------------- links


def test_losing_an_id_reports_the_inbound_link(repo):
    write(repo, {'docs/start/a.html': page('<h2 id="kept">Kept</h2>\n'),
                 'docs/start/b.html': page('<p><a href="a.html#kept">x</a></p>\n')})
    commit(repo)
    write(repo, {'docs/start/a.html': page('<h2 id="other">Other</h2>\n')})
    found = run(repo, 'paths', ['docs/start/a.html']).findings
    assert any(f.rule == 'links.fragment' and f.path == 'docs/start/b.html' for f in found)


def test_a_moved_page_needs_a_redirect(repo):
    write(repo, {'docs/start/c.html': page('<p>body text here</p>\n'), 'docs/start/c.zh.html': ZH})
    commit(repo)
    git(repo, 'mv', 'docs/start/c.html', 'docs/start/n.html')
    git(repo, 'mv', 'docs/start/c.zh.html', 'docs/start/n.zh.html')
    report = run(repo, 'paths', ['docs/start/c.html', 'docs/start/n.html'])
    assert 'links.no-redirect' in rules(report)
    write(repo, {'docs/redirects.json': json.dumps({'start/c.html': 'start/n.html'})})
    assert 'links.no-redirect' not in rules(run(repo, 'paths', ['docs/start/c.html', 'docs/start/n.html']))


def test_a_removed_page_with_no_successor_is_only_reported(repo):
    (repo / 'docs/start/b.html').unlink()
    (repo / 'docs/start/b.zh.html').unlink()
    report = run(repo, 'paths', ['docs/start/b.html', 'docs/start/b.zh.html'])
    assert 'links.no-redirect' in rules(report, WARNING) and 'links.no-redirect' not in rules(report)


# -------------------------------------------------------------------- claims


def code_page(body: str, covers='none') -> str:
    return HEAD.replace('content="none"', f'content="{covers}"') + body + '\n'


def test_unresolved_partial_and_bare_fail_on_product_pages(repo):
    write(repo, {'openprogram/runtime/agent/real.py': '', 'docs/start/a.html': code_page(
        '<code>runtime/agent/nonexistent_x.py</code> <code>nonexistent_helper.py</code> <code>real.py</code>')})
    found = run(repo, 'paths', ['docs/start/a.html']).findings
    assert {f.subject for f in found if f.rule in {'claims.partial', 'claims.bare'}} == {
        'runtime/agent/nonexistent_x.py', 'nonexistent_helper.py'}


def test_engineering_records_may_cite_peer_files(repo):
    write(repo, {'docs/reference/design/x.html': code_page('<code>peer_file.ts</code>')})
    assert not {'claims.bare'} & rules(run(repo, 'paths', ['docs/reference/design/x.html']))


def test_a_move_reaches_partial_and_bare_references_on_unchanged_pages(repo):
    write(repo, {'openprogram/runtime/context/model.py': 'x = 1\n', 'openprogram/unique_name.txt': '',
                 'docs/start/a.html': code_page('<code>context/model.py</code>'),
                 'docs/reference/design/y.html': code_page('<code>model.py</code>')})
    commit(repo)
    git(repo, 'mv', 'openprogram/runtime/context/model.py', 'openprogram/runtime/context/shape.py')
    found = [f for f in run(repo, 'staged', ['openprogram/runtime/context/model.py',
                                             'openprogram/runtime/context/shape.py']).findings if f.severity == ERROR]
    assert {(f.path, f.rule) for f in found} >= {('docs/start/a.html', 'claims.partial'),
                                                 ('docs/reference/design/y.html', 'claims.bare')}
    assert any('moved it to openprogram/runtime/context/shape.py' in f.fix for f in found)


def test_a_moved_module_with_an_attribute_is_found(repo):
    write(repo, {'openprogram/host/resources/session.py': 'def resource_use(): ...\n', 'openprogram/host/__init__.py': '',
                 'docs/start/a.html': code_page('<code>openprogram.host.resources.session.resource_use</code>')})
    commit(repo)
    git(repo, 'mv', 'openprogram/host/resources/session.py', 'openprogram/host/resources/other.py')
    changed = ['openprogram/host/resources/session.py', 'openprogram/host/resources/other.py']
    assert 'claims.module' in rules(run(repo, 'staged', changed))


def test_a_renamed_attribute_is_found(repo):
    write(repo, {'openprogram/thing.py': 'def before(): ...\n', 'docs/start/a.html': code_page('<code>openprogram.thing.before</code>')})
    commit(repo)
    write(repo, {'openprogram/thing.py': 'def after(): ...\n'})
    assert 'claims.module' in rules(run(repo, 'paths', ['openprogram/thing.py']))


def test_a_moved_script_module_is_found(repo):
    write(repo, {'scripts/tool.py': '', 'docs/start/a.html': code_page('<code>python -m scripts.tool</code>')})
    commit(repo)
    git(repo, 'mv', 'scripts/tool.py', 'scripts/other.py')
    assert 'claims.module' in rules(run(repo, 'staged', ['scripts/tool.py', 'scripts/other.py']))


def test_a_moved_file_named_by_covers_is_found(repo):
    write(repo, {'scripts/tool.py': '', 'docs/start/a.html': code_page('', covers='scripts/tool.py')})
    commit(repo)
    git(repo, 'mv', 'scripts/tool.py', 'scripts/other.py')
    assert 'claims.covers' in rules(run(repo, 'staged', ['scripts/tool.py', 'scripts/other.py']))


def test_a_moved_docs_file_cited_in_code_is_found(repo):
    write(repo, {'docs/data/x.json': '{}', 'docs/start/a.html': code_page('<code>docs/data/x.json</code>')})
    commit(repo)
    git(repo, 'mv', 'docs/data/x.json', 'docs/data/y.json')
    assert 'claims.path' in rules(run(repo, 'staged', ['docs/data/x.json', 'docs/data/y.json']))


def test_removing_a_literal_route_is_not_saved_by_a_parameter(repo):
    write(repo, {SERVER: 'a("/api/apps/{id}")\nb("/api/apps/install")\n', 'docs/start/a.html': code_page('<code>/api/apps/install</code>')})
    commit(repo)
    write(repo, {SERVER: 'a("/api/apps/{id}")\nb("/api/apps/setup")\n'})
    assert 'claims.route' in rules(run(repo, 'paths', [SERVER]))


def test_removing_a_route_is_not_saved_by_a_sub_route(repo):
    write(repo, {SERVER: 'a("/api/sys/access")\nb("/api/sys/access/waits")\n', 'docs/start/a.html': code_page('<code>/api/sys/access</code>')})
    commit(repo)
    write(repo, {SERVER: 'b("/api/sys/access/waits")\n'})
    assert 'claims.route' in rules(run(repo, 'paths', [SERVER]))


def test_a_route_written_as_a_prefix_fits_what_is_beneath(repo):
    write(repo, {SERVER: 'b("/api/sys/access/waits")\n', 'docs/start/a.html': code_page('<code>/api/sys/access/*</code>')})
    assert 'claims.route' not in rules(run(repo, 'paths', ['docs/start/a.html']))


@pytest.mark.parametrize('prefix', ['sudo ', 'uv run ', '.venv/bin/'])
def test_prefixed_commands_are_verified(repo, prefix):
    write(repo, {'docs/start/a.html': code_page(f'<code>{prefix}openprogram nosuchverb</code>')})
    assert 'claims.command' in rules(run(repo, 'paths', ['docs/start/a.html']))


def test_a_vanished_setting_reports_its_real_line_and_whole_tokens_only(repo):
    spec = 'SettingSpec(key="agent.limit", default=1)\nSettingSpec(key="hooks", default=1)\n'
    named = ''.join(['\n', '<p><code>agent.limit</code></p>\n', '<p><code>.git/hooks</code></p>\n'])
    write(repo, {SETTINGS: spec, 'docs/start/a.html': code_page(named)})
    commit(repo)
    write(repo, {SETTINGS: 'SettingSpec(key="other.key", default=1)\n'})
    found = [f for f in run(repo, 'paths', [SETTINGS]).findings if f.rule == 'claims.setting']
    assert [(f.subject, f.line) for f in found] == [('agent.limit', 6)]


def test_a_real_command_cannot_be_an_example(repo):
    write(repo, {'docs/start/a.html': code_page('<code data-claim="example">openprogram nosuch</code>')})
    assert 'claims.example-command' in rules(run(repo, 'paths', ['docs/start/a.html']))


def test_adding_an_exemption_past_the_ceiling_is_refused(repo, monkeypatch):
    monkeypatch.setattr(policy, 'CLAIM_EXEMPTIONS', (0, 99))
    write(repo, {'docs/start/a.html': code_page('<code data-claim="example">x/y.py</code>')})
    assert 'claims.exemption-ceiling' in rules(run(repo, 'paths', ['docs/start/a.html']))


def test_claim_errors_do_not_advertise_the_marker_for_commands(repo):
    write(repo, {'docs/start/a.html': code_page('<code>openprogram nosuch</code>')})
    found = [f for f in run(repo, 'paths', ['docs/start/a.html']).findings if f.rule == 'claims.command']
    assert found and 'data-claim' not in found[0].fix


def test_suggestions_name_only_something_close(repo):
    write(repo, {'openprogram/setup.py': 'OPENPROGRAM_NODE_PTY = 1\n',
                 'docs/start/a.html': code_page('<code>openprogram/no/such.py</code> <code>OPENPROGRAM_NOPE_X</code>')})
    found = run(repo, 'paths', ['docs/start/a.html']).findings
    assert not [f for f in found if f.rule.startswith('claims.') and 'Did you mean' in f.fix]


def test_drift_this_change_did_not_cause_only_warns(repo):
    from openprogram.interfaces.cli import parser  # the real parser; the page's verb never existed
    assert parser
    cli = 'openprogram/interfaces/cli/parser_notes.py'
    write(repo, {cli: '# one\n', 'docs/start/b.html': code_page('<code>openprogram nosuchverb</code>')})
    commit(repo)
    write(repo, {cli: '# two\n'})
    report = run(repo, 'paths', [cli])
    assert 'claims.command' in rules(report, WARNING) and 'claims.command' not in rules(report)


def test_drift_this_change_caused_blocks(repo):
    cli = 'openprogram/interfaces/cli/parser_notes.py'
    write(repo, {cli: 'add_parser("nosuchverb")\n', 'docs/start/b.html': code_page('<code>openprogram nosuchverb</code>')})
    commit(repo)
    write(repo, {cli: '# gone\n'})
    assert 'claims.command' in rules(run(repo, 'paths', [cli]))


# -------------------------------------------------------------------- impact


def test_changing_a_default_lists_the_page_naming_that_setting(repo):
    settings = 'SettingSpec(\n    key="agent.fanout",\n    default=1,\n)\nSettingSpec(\n    key="other.one",\n    default=2,\n)\n'
    filler = {f'docs/start/f{i}.html': code_page(f'<code>{SETTINGS}</code>') for i in range(4)}
    write(repo, {SETTINGS: settings, 'docs/start/a.html': code_page('<code>agent.fanout</code>'), **filler})
    commit(repo)
    write(repo, {SETTINGS: settings.replace('default=1', 'default=9')})
    assert 'docs/start/a.html' in pages_of(run(repo, 'paths', [SETTINGS]))


def test_the_filter_says_what_it_dropped(repo, capsys):
    target = 'openprogram/area/deep/file.py'
    filler = {f'docs/start/f{i}.html': code_page(f'<code>{target}</code>') for i in range(5)}
    write(repo, {target: 'a = 1\n', **filler})
    commit(repo)
    write(repo, {target: 'a = 1\nnewconstant = 2\n'})
    assert entry.main([target], root=repo) == 0
    text = capsys.readouterr().out
    assert '5 pages cite this file; 0 share a changed word' in text
    entry.main([target, '--unfiltered'], root=repo)
    assert 'Affected pages, unchanged by this change (5)' in capsys.readouterr().out


def test_an_edit_that_changes_no_visible_text_still_asks(repo):
    target = 'openprogram/area/deep/file.py'
    text = code_page(f'<code>{target}</code>')
    write(repo, {target: 'a = 1\n', 'docs/start/a.html': text})
    commit(repo)
    write(repo, {target: 'a = 2\n', 'docs/start/a.html': text + '\n\n'})
    items = run(repo, 'paths', [target, 'docs/start/a.html']).impact
    assert [i.page for i in items] == ['docs/start/a.html'] and items[0].edited


# ------------------------------------------------------------- acknowledgements


TARGET = 'openprogram/area/deep/file.py'


def _impact_repo(repo: Path) -> None:
    write(repo, {TARGET: 'a = 1\n', 'openprogram/area/deep/other.py': 'b = 1\n', 'docs/start/a.html': code_page(f'<code>{TARGET}</code>')})
    commit(repo)


def test_an_ack_records_only_the_files_pointing_at_the_page(repo):
    _impact_repo(repo)
    tree = linked(repo)
    write(tree, {TARGET: 'a = 2\n', 'openprogram/area/deep/other.py': 'b = 2\n'})
    assert entry.main(['--ack', 'docs/start/a.html', '--reason', 'wording is still true'], root=tree) == 0
    lines = [json.loads(l) for l in acknowledgements.store_path(tree).read_text().splitlines()]
    assert [line['changed'] for line in lines] == [TARGET]


def test_an_ack_refuses_a_page_the_report_does_not_list(repo, capsys):
    _impact_repo(repo)
    tree = linked(repo)
    write(tree, {TARGET: 'a = 2\n'})
    with pytest.raises(SystemExit) as stop:
        entry.main(['--ack', 'docs/start/b.html', '--reason', 'wording is still true'], root=tree)
    err = capsys.readouterr().err
    assert stop.value.code == 2 and 'impact report' in err and 'ValueError' not in err


def test_an_ack_in_the_shared_checkout_needs_paths(repo, capsys):
    _impact_repo(repo)
    write(repo, {TARGET: 'a = 2\n'})
    with pytest.raises(SystemExit):
        entry.main(['--ack', 'docs/start/a.html', '--reason', 'wording is still true'], root=repo)
    assert 'name your changed files' in capsys.readouterr().err
    assert entry.main([TARGET, '--ack', 'docs/start/a.html', '--reason', 'wording is still true'], root=repo) == 0


def test_a_short_reason_is_a_usage_error(repo, capsys):
    _impact_repo(repo)
    write(repo, {TARGET: 'a = 2\n'})
    with pytest.raises(SystemExit) as stop:
        entry.main([TARGET, '--ack', 'docs/start/a.html', '--reason', 'short'], root=repo)
    assert stop.value.code == 2 and 'ValueError' not in capsys.readouterr().err


def test_an_ack_of_a_small_last_edit_does_not_cover_the_big_one(repo):
    _impact_repo(repo)
    tree = linked(repo)
    base = git(tree, 'rev-parse', 'HEAD')
    write(tree, {TARGET: 'a = "big behaviour change"\n'})
    commit(tree)
    write(tree, {TARGET: 'a = "big behaviour change"  # note\n'})
    entry.main([TARGET, '--ack', 'docs/start/a.html', '--reason', 'only a comment here'], root=tree)
    commit(tree)
    item = next(i for i in run(tree, 'range', [TARGET], base).impact if i.page == 'docs/start/a.html')
    assert not item.acknowledged


def test_chained_acks_cover_the_range(repo):
    _impact_repo(repo)
    tree = linked(repo)
    base = git(tree, 'rev-parse', 'HEAD')
    for text in ('a = 2\n', 'a = 3\n'):
        write(tree, {TARGET: text})
        entry.main([TARGET, '--ack', 'docs/start/a.html', '--reason', 'checked this step too'], root=tree)
        commit(tree)
    item = next(i for i in run(tree, 'range', [TARGET], base).impact if i.page == 'docs/start/a.html')
    assert item.acknowledged


# ----------------------------------------------------------- whose tree is it


def test_base_in_the_main_checkout_ignores_other_sessions_files(repo):
    base = git(repo, 'rev-parse', 'HEAD')
    write(repo, {'docs/start/a.html': page('<p style="x">bad</p>\n'), 'docs/start/new.html': '<p style="x">x</p>'})
    assert entry.main(['--base', base], root=repo) == 0
    assert affected_tests.changed_files(repo, base) == []


def test_base_in_the_main_checkout_reads_the_committed_pages(repo):
    base = git(repo, 'rev-parse', 'HEAD')
    write(repo, {'docs/start/b.html': page('<p style="x">bad</p>\n')})
    commit(repo)
    write(repo, {'docs/start/b.html': page()})  # someone's dirty fix does not excuse the commit
    assert entry.main(['--base', base], root=repo) == 1


def test_a_named_page_does_not_block_on_another_sessions_dirty_page(repo):
    sentence = 'Requests travel through the gateway before reaching the worker pool quickly.'
    write(repo, {'docs/start/a.html': page(f'<p>{sentence}</p>\n')})
    commit(repo)
    write(repo, {'docs/start/b.html': page(f'<p>{sentence}</p>\n')})
    report = entry.run(Scope('paths', repo, ('docs/start/a.html',), shared_tree=True))
    assert 'content.duplicate-across-pages' in rules(report, WARNING)
    assert 'content.duplicate-across-pages' not in rules(report)


def test_staged_mode_reads_the_staged_navigation(repo, monkeypatch):
    monkeypatch.undo()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(hook_status, 'stale_hooks', lambda _root: [])
    real = (ROOT / 'scripts/docs_site/nav.py').read_text(encoding='utf-8')
    titles = (ROOT / 'scripts/docs_site/page_titles.json').read_text(encoding='utf-8')
    write(repo, {'scripts/docs_site/nav.py': real, 'scripts/docs_site/page_titles.json': titles})
    commit(repo)
    write(repo, {'docs/start/zz-new.html': page(), 'docs/start/zz-new.zh.html': ZH})
    git(repo, 'add', 'docs')
    # Another session's unsaved nav.py lists the page; the commit does not.
    write(repo, {'scripts/docs_site/nav.py': real.replace('"start/', '"start/zz-new.html", "start/', 1)})
    loaded = corpus.load(Scope('staged', repo, ('docs/start/zz-new.html',)))
    assert loaded.nav is not nav
    found = list(format_checks.check(loaded))
    assert any(f.rule == 'format.unregistered' for f in found)


def test_staged_mode_greps_the_index_for_environment_variables(repo):
    write(repo, {'code.py': 'OPENPROGRAM_ONLY_ON_DISK = 1\n', 'docs/start/a.html': code_page('<code>OPENPROGRAM_ONLY_ON_DISK</code>')})
    git(repo, 'add', 'docs')
    assert 'claims.env' in rules(run(repo, 'staged', ['docs/start/a.html']))


# ----------------------------------------------------------------- the selector


def test_a_clean_worktree_is_checked_against_dev(repo):
    tree = linked(repo)
    assert affected_tests.branch_point(tree) is None
    point = git(tree, 'rev-parse', 'HEAD')
    write(tree, {'code.py': 'x = 2\n'})
    commit(tree)
    assert affected_tests.branch_point(tree) == point


def test_docs_tool_failure_is_labelled_and_need_not_block(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(affected_tests, '_stream', lambda _command, _where: (2, []))
    planned = [('.', ['python', '-m', 'scripts.docs_site.check'])]
    assert affected_tests.run_planned(planned, tmp_path, docs_tool_blocks=False) == 0
    assert 'docs check could not run' in capsys.readouterr().err
    assert affected_tests.run_planned(planned, tmp_path) == 1


# ---------------------------------------------------------------------- hooks


def _hooks_repo(repo: Path) -> Path:
    for name in ('pre-commit', 'pre-push', 'post-merge', 'install.sh'):
        (repo / 'scripts/git-hooks').mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'scripts/git-hooks' / name, repo / 'scripts/git-hooks' / name)
    commit(repo)
    subprocess.run(['sh', 'scripts/git-hooks/install.sh'], cwd=repo, check=True, capture_output=True)
    return repo / '.git/hooks'


def test_an_older_checkout_does_not_downgrade_the_hooks(repo):
    hooks = _hooks_repo(repo)
    old = git(repo, 'rev-parse', 'HEAD')
    write(repo, {'scripts/git-hooks/pre-push': '#!/bin/sh\n# newer\n'})
    commit(repo)
    subprocess.run(['sh', 'scripts/git-hooks/install.sh'], cwd=repo, check=True, capture_output=True)
    assert '# newer' in (hooks / 'pre-push').read_text()
    git(repo, 'checkout', '-q', old)
    subprocess.run(['sh', 'scripts/git-hooks/install.sh'], cwd=repo, check=True, capture_output=True)
    assert '# newer' in (hooks / 'pre-push').read_text()
    assert (hooks / 'post-merge').is_file()


def test_the_hook_fires_for_a_baseline_only_commit(repo):
    hooks = _hooks_repo(repo)
    write(repo, {'.venv/bin/.keep': '', BASELINE: '[]', 'scripts/docs_site/check.py': 'if __name__ == "__main__":\n    raise SystemExit(1)\n'})
    (repo / '.venv/bin/python').symlink_to(sys.executable)
    git(repo, 'add', BASELINE, 'scripts/docs_site/check.py')
    done = subprocess.run(['sh', str(hooks / 'pre-commit')], cwd=repo, capture_output=True, text=True)
    assert done.returncode != 0 and 'commit refused' in done.stderr


def test_stale_hooks_are_quiet_in_a_worktree(repo, monkeypatch):
    tree = linked(repo)
    monkeypatch.setattr(hook_status, 'stale_hooks', lambda _root: ['pre-commit'])
    scope = type('C', (), {'scope': Scope('all', tree)})
    assert list(hook_status.check(scope)) == []
    assert list(hook_status.check(type('C', (), {'scope': Scope('all', repo)})))
