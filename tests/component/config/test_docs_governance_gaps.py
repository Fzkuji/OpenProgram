"""What blind acceptance found a change could slip past: each case is one way a doc went stale silently."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import pytest

from scripts.docs_site import check as entry
from scripts.docs_site import commit_gate, html_source, nav
from scripts.docs_site.governance import content_checks, format_checks, impact, policy, references
from scripts.docs_site.governance.claims import _removed_cli_tokens
from scripts.docs_site.governance.model import Corpus, Page, Scope
from scripts.docs_site.governance.references import ParserChange, parser_change, read_page, verify_command

TOPS = frozenset({"openprogram", "scripts"})
HEAD = '<title>T</title>\n<meta name="description" content="d">\n<h1>T</h1>\n'
ZH = '<title>甲</title>\n<meta name="description" content="乙">\n<h1>甲</h1>\n'


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "dev")
    write(root, {"docs/start/a.html": HEAD + "<p>x</p>\n", "docs/start/a.zh.html": ZH + "<p>丙</p>\n", "code.py": ""})
    git(root, "add", "-A")
    git(root, "commit", "-qm", "x")
    monkeypatch.setattr(nav, "TAB_SECTIONS", {"start": [("S", "甲", ["start/a.html"])]})
    monkeypatch.setattr(policy, "PROTOTYPE_PAGES", frozenset())
    monkeypatch.setattr(entry, "default_checks", lambda: [format_checks.check])
    monkeypatch.chdir(root)
    return root


def pair(path: str, english: str, chinese: str | None) -> Page:
    en, zh = HEAD + english, ZH + chinese if chinese is not None else ""
    return Page(path[5:], path, path[:-5] + ".zh.html" if chinese is not None else "", en, zh,
                html_source.parse(en), html_source.parse(zh) if chinese is not None else None, False)


def cli(rename: bool = False, flag: str = "-y") -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    verbs = root.add_subparsers(dest="c")
    dev = verbs.add_parser("dev").add_subparsers(dest="d")
    dev.add_parser("status")
    dev.add_parser("clone-models" if rename else "copy-models")
    backup = verbs.add_parser("backup").add_subparsers(dest="b")
    restore = backup.add_parser("restore")
    restore.add_argument(flag, action="store_true")
    verbs.add_parser("other").add_argument("-y", action="store_true")
    return root


# ---- a path that names nothing

@pytest.mark.parametrize("arguments", [["docs/nope.html"], ["docs/start/a.html docs/start/b.html code.py"]])
def test_a_path_that_exists_nowhere_cannot_pass(repo, capsys, arguments):
    assert entry.main(arguments, root=repo) == 2
    assert arguments[0] in capsys.readouterr().err


def test_a_deleted_file_is_still_a_valid_path(repo):
    (repo / "code.py").unlink()
    assert entry.main(["code.py"], root=repo) == 0


def test_a_directory_with_no_files_cannot_pass(repo, capsys):
    (repo / "empty").mkdir()
    assert entry.main(["empty"], root=repo) == 2
    assert "nothing to check" in capsys.readouterr().err


# ---- alternation

def test_alternation_is_one_command_each_and_each_is_verified():
    found = read_page(html_source.parse('<code translate="no">openprogram dev status|copy-models</code>'), TOPS)
    commands = [r.value for r in found.references if r.kind == "command"]
    assert commands == ["openprogram dev status", "openprogram dev copy-models"]
    assert not verify_command(cli(), commands[1], set())
    assert [p.token for p in verify_command(cli(rename=True), commands[1], set())] == ["copy-models"]


def test_a_bar_inside_brackets_stays_a_placeholder():
    assert not verify_command(cli(), "openprogram dev <status|nope>", set())
    assert not verify_command(cli(), "openprogram backup restore [--a|--b]", set())


# ---- bare verbs and flags

class FakeRepository:
    def __init__(self, change: ParserChange) -> None:
        self.change = change

    def parser_change(self) -> ParserChange:
        return self.change


def read(html: str):
    return read_page(html_source.parse(html), TOPS)


def test_a_removed_verb_as_a_whole_span_is_an_error():
    change = parser_change(cli(), cli(rename=True))
    found = list(_removed_cli_tokens(FakeRepository(change), "docs/x.html", read('<code>copy-models</code>')))
    assert [f.rule for f in found] == ["claims.command"] and found[0].severity == "error"


def test_a_removed_top_level_verb_counts_only_where_it_is_written_as_a_command():
    change = ParserChange(frozenset(), frozenset(), frozenset({((), "browser")}), frozenset({"browser"}),
                          frozenset({"programs", "mcp"}))

    def lines(html):
        return [f.line for f in _removed_cli_tokens(FakeRepository(change), "docs/x.html", read(html))]
    # The word names an application or a target here, not the command.
    assert lines('<p>target <code>app</code>, <code>browser</code> or <code>vm</code></p>') == []
    assert lines('<p>sources <code>mcp</code> and <code>browser</code></p>') == []
    # In a list of top-level verbs, or written out as a command elsewhere on the page.
    assert lines('<p><code>programs</code>, <code>mcp</code>, <code>browser</code></p>') == [1]
    assert lines('<p><code>browser</code></p>\n<pre><code>openprogram browser status</code></pre>') == [1]


def test_a_removed_flag_on_a_page_about_that_command_is_an_error():
    change = parser_change(cli(), cli(flag="-Y"))
    page = read('<code>openprogram backup restore x</code><code>-y</code>')
    assert [f.subject for f in _removed_cli_tokens(FakeRepository(change), "docs/x.html", page)] == ["-y"]


def test_a_flag_other_commands_keep_does_not_flag_unrelated_pages():
    change = parser_change(cli(), cli(flag="-Y"))
    assert not list(_removed_cli_tokens(FakeRepository(change), "docs/x.html", read('<code>-y</code>')))


# ---- impact

TARGET = "openprogram/operations/backup.py"


def page_on_disk(root: Path, path: str, body: str) -> Page:
    write(root, {path: HEAD + body})
    return pair(path, body, None)


def seeded(tmp_path: Path, files: dict[str, str]) -> Path:
    git(tmp_path, "init", "-q")
    write(tmp_path, files)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "seed")
    return tmp_path


def test_a_source_file_naming_a_page_declares_it(tmp_path):
    root = seeded(tmp_path, {TARGET: 'INCLUDED = ("a",)  # keep in sync with docs/server/backup.html\n'})
    write(root, {TARGET: 'INCLUDED = ("a", "b",)  # keep in sync with docs/server/backup.html\n'})
    pages = [page_on_disk(root, "docs/server/backup.html", "<p>x</p>"), page_on_disk(root, "docs/y.html", "<p>y</p>")]
    items = impact.report(Corpus(Scope("worktree", root, (TARGET,)), pages))
    assert [(i.page, i.declared) for i in items] == [("docs/server/backup.html", True)]


def test_a_command_reaches_what_its_module_imports(tmp_path):
    command = "openprogram/interfaces/cli/commands/backup.py"
    root = seeded(tmp_path, {command: "def run():\n    from openprogram.operations.backup import go\n",
                             TARGET: "def go():\n    return 1\n"})
    write(root, {TARGET: "def go():\n    return 2\n"})
    pages = [page_on_disk(root, "docs/b.html", "<code>openprogram backup list</code>")]
    items = impact.report(Corpus(Scope("worktree", root, (TARGET,)), pages))
    assert [i.page for i in items] == ["docs/b.html"]


def test_strings_comments_and_keywords_are_not_identifiers():
    assert references.identifiers_in('parser.add_argument("--records", action="store_true", help="backups")  # prompt', ".py") \
        == {"parser", "add_argument"}


def test_a_changed_string_leaves_a_crowded_file_filtered(tmp_path):
    root = seeded(tmp_path, {TARGET: 'name = "records"\n'})
    write(root, {TARGET: 'name = "action"\n'})
    pages = [page_on_disk(root, f"docs/p{n}.html", f"<code>{TARGET}</code><code>action</code>") for n in range(5)]
    assert impact.report(Corpus(Scope("worktree", root, (TARGET,)), pages)) == []


def test_only_commands_under_a_changed_path_are_affected():
    change = parser_change(cli(), cli(flag="-Y"))
    assert change.paths == frozenset({("backup", "restore")})
    assert ("backup", "restore") in change.paths and ("dev",) not in change.paths


def test_every_pending_page_is_printed_and_the_old_flag_is_gone(repo, capsys):
    with pytest.raises(SystemExit):
        entry.main(["--every-impact"], root=repo)
    capsys.readouterr()
    assert entry.main(["--all", "--unfiltered"], root=repo) == 0


# ---- both languages

def test_a_stale_chinese_command_is_an_error():
    finding = list(format_checks._code_spans_differ(pair(
        "docs/a.html", '<code translate="no">clone-models</code>', '<code translate="no">copy-models</code>')))
    assert finding[0].rule == "format.code-span-mismatch" and finding[0].severity == "error"


def test_a_translated_placeholder_is_not_a_difference():
    assert not list(format_checks._code_spans_differ(pair(
        "docs/a.html", '<code translate="no">/goal &lt;objective&gt;</code><code translate="no">min(1, normal)</code>',
        '<code translate="no">/goal &lt;目标&gt;</code><code translate="no">min(1, 普通)</code>')))


def test_changing_an_english_span_without_the_chinese_page_is_an_error(repo):
    write(repo, {"docs/start/a.html": HEAD + '<p><code translate="no">new</code></p>\n'})
    report = entry.run(entry.resolve_scope(entry._parser().parse_args(["docs/start/a.html"]), repo))
    stale = [f for f in report.findings if f.rule == "format.zh-stale"]
    assert stale and stale[0].severity == "error"


def test_changing_only_english_prose_stays_a_warning(repo):
    write(repo, {"docs/start/a.html": HEAD + "<p>changed</p>\n"})
    report = entry.run(entry.resolve_scope(entry._parser().parse_args(["docs/start/a.html"]), repo))
    assert [f.severity for f in report.findings if f.rule == "format.zh-stale"] == ["warning"]


# ---- warnings the change did not cause

LONG = " ".join(["word"] * 65)


def test_warnings_on_untouched_lines_become_one_count(repo, capsys, monkeypatch):
    monkeypatch.setattr(entry, "default_checks", lambda: [content_checks.check])
    write(repo, {"docs/start/a.html": HEAD + f"<p>{LONG}</p>\n<p>x</p>\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "long")
    write(repo, {"docs/start/a.html": HEAD + f"<p>{LONG}</p>\n<p>y</p>\n"})
    assert entry.main(["docs/start/a.html"], root=repo) == 0
    text = capsys.readouterr().out
    assert "content.paragraph" not in text and "1 more warning(s) on lines this change did not touch" in text
    entry.main(["--all"], root=repo)
    assert "content.paragraph" in capsys.readouterr().out


# ---- a fold that says nothing

def test_a_fold_repeating_its_summary_as_a_link_warns():
    page = pair("docs/a.html", '<details><summary>Moving to a new computer</summary><p><a href="x.html">Move to a new computer</a></p></details>', None)
    assert [f.rule for f in content_checks._hollow_folds(page)] == ["content.hollow-fold"]


def test_a_fold_with_its_own_content_passes():
    page = pair("docs/a.html", '<details><summary>Moving</summary><p>Copy the folder, then <a href="x.html">Move</a>.</p></details>', None)
    assert not list(content_checks._hollow_folds(page))


# ---- state directory names

def test_a_state_path_is_a_reference():
    found = read('<code>~/.openprogram/programz/x</code><code>&lt;state&gt;/schedulerr</code><code>~/.openprogram/&lt;name&gt;</code>')
    assert [r.value for r in found.references if r.kind == "state"] == ["programz", "schedulerr"]


# ---- the commit gate

@pytest.mark.parametrize(("command", "expected"), [
    ("git commit -m x", "/w"),
    ('cd "/a b" && git commit -- x', "/a b"),
    ("git -C /other merge feature", "/other"),
    ("git merge --abort", None),
    ("git status && git log", None),
    ("echo git commit", None),
])
def test_the_gate_finds_commits_and_merges(command, expected):
    found = commit_gate.gated_directory(command, "/w")
    assert (str(found) if found else None) == expected


def linked(tmp_path: Path, page: str) -> Path:
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "dev")
    write(main, {"seed": ""})
    git(main, "add", "-A")
    git(main, "commit", "-qm", "seed")
    tree = tmp_path / "tree"
    git(main, "worktree", "add", "-q", "-b", "work", str(tree))
    write(tree, {"scripts/docs_site/check.py": page})
    return tree


class Done:
    def __init__(self, code: int, report: dict) -> None:
        self.returncode, self.stdout, self.stderr = code, json.dumps(report), ""


def gate_with(monkeypatch, tree: Path, code: int, report: dict) -> int:
    real = subprocess.run

    def fake(command, *args, **kwargs):
        return Done(code, report) if "scripts.docs_site.check" in command else real(command, *args, **kwargs)

    monkeypatch.setattr(commit_gate.subprocess, "run", fake)
    return commit_gate.run_gate(tree)


ERROR_REPORT = {"errors": 1, "impact": [], "findings": [
    {"severity": "error", "path": "docs/a.html", "line": 3, "rule": "r", "message": "m", "fix": "f"}]}


def test_the_gate_blocks_on_errors_in_a_linked_worktree(tmp_path, monkeypatch, capsys):
    assert gate_with(monkeypatch, linked(tmp_path, ""), 1, ERROR_REPORT) == commit_gate.EXIT_BLOCK
    assert "docs/a.html:3" in capsys.readouterr().err


def test_affected_pages_print_a_count_and_never_block(tmp_path, monkeypatch, capsys):
    report = {"errors": 0, "findings": [], "impact": [{"acknowledged": False}, {"acknowledged": True}]}
    assert gate_with(monkeypatch, linked(tmp_path, ""), 0, report) == 0
    assert "1 affected page" in capsys.readouterr().out


def test_a_broken_tool_does_not_jam_commits(tmp_path, monkeypatch):
    assert gate_with(monkeypatch, linked(tmp_path, ""), 2, {}) == 0


def test_the_main_checkout_passes_straight_through(repo):
    assert commit_gate.run_gate(repo) == 0
