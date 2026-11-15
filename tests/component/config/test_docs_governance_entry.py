"""One command, with the promised scopes, exit codes and output."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.docs_site import check as entry
from scripts.docs_site import nav
from scripts.docs_site.governance import format_checks, policy
from scripts.docs_site.governance.model import ERROR, Finding, Scope

GOOD = '<title>T</title>\n<meta name="description" content="d">\n<h1>T</h1>\n<p>x</p>\n'
ZH = '<title>甲</title>\n<meta name="description" content="乙">\n<h1>甲</h1>\n<p>丙</p>\n'
BAD = GOOD + '<p style="x">y</p>\n'


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding='utf-8')


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'dev')
    git(root, 'config', 'user.email', 't@t')
    git(root, 'config', 'user.name', 't')
    write(root, 'docs/start/a.html', GOOD)
    write(root, 'docs/start/a.zh.html', ZH)
    write(root, 'docs/start/b.html', GOOD)
    write(root, 'docs/start/b.zh.html', ZH)
    write(root, 'code.py', '')
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'x')
    monkeypatch.setattr(nav, 'TAB_SECTIONS', {'start': [('S', '甲', ['start/a.html', 'start/b.html'])]})
    monkeypatch.setattr(policy, 'PROTOTYPE_PAGES', frozenset())
    monkeypatch.setattr(entry, 'default_checks',
                        lambda: [format_checks.check])
    monkeypatch.chdir(root)
    return root


def out(capsys) -> str:
    return capsys.readouterr().out


def test_named_path_blocks_and_prints_file_line_rule_and_fix(repo, capsys):
    write(repo, 'docs/start/a.html', BAD)
    assert entry.main(['docs/start/a.html'], root=repo) == 1
    assert 'docs/start/a.html:5: error format.style-attribute: a style attribute. Fix: use a site class.' in out(capsys)


def test_report_only_never_blocks(repo):
    write(repo, 'docs/start/a.html', BAD)
    assert entry.main(['docs/start/a.html', '--report-only'], root=repo) == 0


def test_shared_checkout_reports_but_does_not_block(repo, capsys):
    write(repo, 'docs/start/a.html', BAD)
    assert entry.main([], root=repo) == 0
    text = out(capsys)
    assert 'docs check (shared)' in text and 'format.style-attribute' in text and 'Not blocking' in text


def test_worktree_blocks_on_its_own_change(repo, tmp_path):
    other = tmp_path / 'wt'
    git(repo, 'worktree', 'add', '-q', str(other), '-b', 'w')
    write(other, 'docs/start/a.html', BAD)
    assert entry.main([], root=other) == 1


def test_worktree_ignores_a_page_it_did_not_change(repo, tmp_path):
    write(repo, 'docs/start/b.html', BAD)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'bad')
    other = tmp_path / 'wt2'
    git(repo, 'worktree', 'add', '-q', str(other), '-b', 'w2')
    write(other, 'docs/start/a.html', GOOD + '<p>z</p>\n')
    assert entry.main([], root=other) == 0


def test_staged_reads_the_index_not_the_working_tree(repo):
    write(repo, 'docs/start/a.html', BAD)
    git(repo, 'add', 'docs/start/a.html')
    write(repo, 'docs/start/a.html', GOOD)
    assert entry.main(['--staged'], root=repo) == 1
    write(repo, 'docs/start/a.html', BAD)
    git(repo, 'reset', '-q')
    assert entry.main(['--staged'], root=repo) == 0


def test_range_covers_commits(repo):
    start = git(repo, 'rev-parse', 'HEAD')
    write(repo, 'docs/start/a.html', BAD)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'bad')
    assert entry.main(['--base', start], root=repo) == 1
    assert entry.main(['--base', 'HEAD'], root=repo) == 0


def test_all_sees_a_page_nothing_changed(repo):
    write(repo, 'docs/start/b.html', BAD)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'bad')
    assert entry.main([], root=repo) == 0
    assert entry.main(['--all'], root=repo) == 1


def test_json_output(repo, capsys):
    write(repo, 'docs/start/a.html', BAD)
    assert entry.main(['docs/start/a.html', '--json'], root=repo) == 1
    data = json.loads(out(capsys))
    assert data['errors'] == 1 and data['findings'][0]['rule'] == 'format.style-attribute'


def test_a_broken_revision_is_a_tool_failure_not_a_page_failure(repo, capsys):
    assert entry.main(['--base', 'no-such-rev'], root=repo) == 2
    assert 'could not run' in capsys.readouterr().err


def test_a_crashing_check_is_exit_two(repo, monkeypatch):
    def boom(_corpus):
        raise RuntimeError('broken check')
    monkeypatch.setattr(entry, 'default_checks', lambda: [boom])
    assert entry.main(['--all'], root=repo) == 2


def test_run_accepts_replacement_checks(repo):
    finding = Finding('x.y', ERROR, 'docs/start/a.html', 1, 'm', 'f')
    report = entry.run(Scope('all', repo), [lambda _c: [finding]])
    assert report.findings == [finding] and report.blocks


def test_ack_needs_a_reason(repo):
    with pytest.raises(SystemExit):
        entry.main(['--ack', 'docs/start/a.html'], root=repo)


def test_impact_is_printed_with_a_way_to_answer(repo, capsys, monkeypatch):
    from types import SimpleNamespace
    item = SimpleNamespace(page='docs/start/a.html', changed='code.py', matched=('code.py',),
                           declared=False, acknowledged=False, edited=False)
    entry._print_impact([item] * 9, [], Scope('worktree', repo))
    text = out(capsys)
    assert 'Affected pages' in text and text.count('docs/start/a.html') == 9 and '--ack' in text
    assert 'more' not in text


def test_the_check_never_needs_markdown_or_a_build():
    done = subprocess.run(
        ['python', '-c', 'import sys, scripts.docs_site.check; sys.exit("markdown_it" in sys.modules)'],
        cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
