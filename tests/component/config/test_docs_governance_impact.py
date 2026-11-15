"""A code change is told which pages it may have outdated, and can record that one is fine."""
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from scripts.docs_site import html_source
from scripts.docs_site.governance import acknowledgements, impact
from scripts.docs_site.governance.model import Corpus, Page, Scope


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
        cwd=root, capture_output=True, text=True, check=True).stdout


def repo(tmp_path: Path, files: dict[str, str]) -> Path:
    git(tmp_path, "init", "-q")
    write(tmp_path, files)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "seed", "--allow-empty")
    return tmp_path


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def page(path: str, body: str, zh: bool = False, root: Path | None = None) -> Page:
    full = "<title>T</title>\n<h1>T</h1>\n" + body
    if root is not None:
        write(root, {path: full})
    return Page(path[len("docs/"):], path, path[:-5] + ".zh.html" if zh else "", full, full if zh else "",
                html_source.parse(full), html_source.parse(full) if zh else None, False)


def report(root: Path, *pages: Page, mode: str = "worktree", changed: tuple[str, ...] = (), base: str | None = None):
    return impact.report(Corpus(Scope(mode, root, changed, base), list(pages)))


def names(items) -> list[str]:
    return [item.page for item in items]


TARGET = "openprogram/area/deep/thing.py"


def test_page_naming_a_changed_file_is_reported(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "first = 1\n"})
    write(root, {TARGET: "second = 2\n"})
    items = report(root, page("docs/x.html", f"<code>{TARGET}</code>"), page("docs/y.html", "<p>none</p>"),
                   changed=(TARGET,))
    assert names(items) == ["docs/x.html"]
    assert items[0].changed == TARGET
    assert TARGET in items[0].matched
    assert not items[0].acknowledged


def test_docs_change_itself_reports_nothing(tmp_path: Path) -> None:
    root = repo(tmp_path, {"docs/x.html": ""})
    assert report(root, page("docs/x.html", "<code>docs/x.html</code>"), changed=("docs/x.html",)) == []


def test_updating_the_english_text_clears_the_page(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "first = 1\n"})
    write(root, {TARGET: "second = 2\n"})
    items = report(root, page("docs/x.html", f"<code>{TARGET}</code>", zh=True), changed=(TARGET, "docs/x.html"))
    assert items == []


def test_editing_only_the_chinese_page_still_asks(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "first = 1\n"})
    write(root, {TARGET: "second = 2\n"})
    items = report(root, page("docs/x.html", f"<code>{TARGET}</code>", zh=True), changed=(TARGET, "docs/x.zh.html"))
    assert names(items) == ["docs/x.html"] and items[0].edited


def test_deep_directory_counts_and_shallow_directory_does_not(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "first = 1\n", "openprogram/top.py": "x = 1\n"})
    write(root, {TARGET: "second = 2\n"})
    deep = page("docs/deep.html", "<code>openprogram/area/deep/</code>")
    shallow = page("docs/shallow.html", "<code>openprogram/area/</code>")
    assert names(report(root, deep, shallow, changed=(TARGET,))) == ["docs/deep.html"]


def test_covers_counts_at_any_depth_and_ranks_first(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "first = 1\n"})
    write(root, {TARGET: "second = 2\n"})
    named = page("docs/a-named.html", f"<code>{TARGET}</code>")
    declared = page("docs/z-declared.html", '<meta name="covers" content="openprogram/">')
    items = report(root, named, declared, changed=(TARGET,))
    assert names(items) == ["docs/z-declared.html", "docs/a-named.html"]
    assert items[0].declared and not items[1].declared


def test_unique_bare_name_and_partial_path_resolve(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "first = 1\n", "openprogram/other/shared.py": "", "openprogram/more/shared.py": ""})
    write(root, {TARGET: "second = 2\n"})
    bare = page("docs/bare.html", "<code>thing.py</code>")
    partial = page("docs/partial.html", "<code>area/deep/thing.py</code>")
    ambiguous = page("docs/amb.html", "<code>shared.py</code>")
    assert sorted(names(report(root, bare, partial, ambiguous, changed=(TARGET,)))) == ["docs/bare.html", "docs/partial.html"]


def test_module_route_and_command_map_to_their_source(tmp_path: Path) -> None:
    server = "openprogram/interfaces/server/routes/agents.py"
    parser = "openprogram/interfaces/cli/commands/doctor.py"
    root = repo(tmp_path, {TARGET: "a = 1\n", server: 'app.get("/api/agents")\n', parser: "b = 1\n"})
    write(root, {TARGET: "a = 2\n", server: 'app.get("/api/agents")\nchanged_line = 1\n', parser: "b = 2\n"})
    module = page("docs/module.html", "<code>openprogram.area.deep.thing</code>")
    route = page("docs/route.html", "<code>/api/agents</code>")
    command = page("docs/command.html", "<code>openprogram doctor</code>")
    assert names(report(root, module, changed=(TARGET,))) == ["docs/module.html"]
    assert names(report(root, route, changed=(server,))) == ["docs/route.html"]
    assert names(report(root, command, changed=(parser,))) == ["docs/command.html"]


def test_filter_above_three_pages_keeps_only_pages_sharing_a_changed_word(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "plain = 1\n"})
    write(root, {TARGET: "renamed_option = 1\n"})
    silent = [page(f"docs/s{i}.html", f"<code>{TARGET}</code>") for i in range(4)]
    speaking = page("docs/speaks.html", f"<code>{TARGET}</code><code>renamed_option</code>")
    items = report(root, *silent, speaking, changed=(TARGET,))
    assert names(items) == ["docs/speaks.html"]
    assert "renamed_option" in items[0].matched


def test_at_or_below_three_pages_no_filter_applies(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "plain = 1\n"})
    write(root, {TARGET: "renamed_option = 1\n"})
    pages = [page(f"docs/s{i}.html", f"<code>{TARGET}</code>") for i in range(3)]
    assert len(report(root, *pages, changed=(TARGET,))) == 3


def test_declared_page_survives_the_filter(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "plain = 1\n"})
    write(root, {TARGET: "renamed_option = 1\n"})
    silent = [page(f"docs/s{i}.html", f"<code>{TARGET}</code>") for i in range(4)]
    declared = page("docs/d.html", f'<meta name="covers" content="{TARGET}">')
    assert names(report(root, *silent, declared, changed=(TARGET,))) == ["docs/d.html"]


def test_range_mode_reads_the_committed_change(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "plain = 1\n"})
    base = git(root, "rev-parse", "HEAD").strip()
    write(root, {TARGET: "renamed_option = 1\n"})
    git(root, "commit", "-qam", "change")
    silent = [page(f"docs/s{i}.html", f"<code>{TARGET}</code>") for i in range(4)]
    speaking = page("docs/speaks.html", f"<code>{TARGET}</code><code>renamed_option</code>")
    assert names(report(root, *silent, speaking, mode="range", changed=(TARGET,), base=base)) == ["docs/speaks.html"]


def test_acknowledged_page_is_not_nagged_and_a_further_edit_asks_again(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "first = 1\n"})
    write(root, {TARGET: "second = 2\n"})
    x = page("docs/x.html", f"<code>{TARGET}</code>", root=root)
    scope = Scope("worktree", root, (TARGET,))
    assert acknowledgements.record(scope, ["docs/x.html"], "renamed a local only") == 1
    assert report(root, x, changed=(TARGET,))[0].acknowledged
    assert acknowledgements.record(scope, ["docs/x.html"], "renamed a local only") == 0
    write(root, {TARGET: "third = 3\n"})
    assert not report(root, x, changed=(TARGET,))[0].acknowledged


def test_acknowledgement_survives_the_commit(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "first = 1\n"})
    base = git(root, "rev-parse", "HEAD").strip()
    write(root, {TARGET: "second = 2\n"})
    x = page("docs/x.html", f"<code>{TARGET}</code>", root=root)
    acknowledgements.record(Scope("worktree", root, (TARGET,)), ["docs/x.html"], "checked, wording still true")
    git(root, "add", TARGET)
    git(root, "commit", "-qm", "change", "--", TARGET)
    assert report(root, x, mode="range", changed=(TARGET,), base=base)[0].acknowledged


def test_staged_mode_uses_the_index_blob(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "first = 1\n"})
    write(root, {TARGET: "staged = 2\n"})
    git(root, "add", TARGET)
    write(root, {TARGET: "unstaged = 3\n"})
    x = page("docs/x.html", f"<code>{TARGET}</code>", root=root)
    scope = Scope("staged", root, (TARGET,))
    expected = git(root, "ls-files", "-s", "--", TARGET).split()[1]
    assert acknowledgements.blobs(scope) == {TARGET: expected}
    acknowledgements.record(scope, ["docs/x.html"], "index version is fine")
    assert report(root, x, mode="staged", changed=(TARGET,))[0].acknowledged


def test_acknowledging_a_chinese_name_records_the_english_page(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "first = 1\n"})
    write(root, {TARGET: "second = 2\n"})
    x = page("docs/x.html", f"<code>{TARGET}</code>", root=root)
    write(root, {"docs/x.zh.html": ""})
    acknowledgements.record(Scope("worktree", root, (TARGET,)), ["docs/x.zh.html"], "checked the pair of pages")
    assert report(root, x, changed=(TARGET,))[0].acknowledged


def test_partial_acknowledgement_keeps_the_page_open_and_names_the_other_file(tmp_path: Path) -> None:
    other = "openprogram/area/deep/other.py"
    root = repo(tmp_path, {TARGET: "a = 1\n", other: "b = 1\n"})
    write(root, {TARGET: "a = 2\n"})
    x = page("docs/x.html", f"<code>{TARGET}</code><code>{other}</code>", root=root)
    acknowledgements.record(Scope("worktree", root, (TARGET,)), ["docs/x.html"], "first file is fine here")
    write(root, {other: "b = 2\n"})
    items = report(root, x, changed=(TARGET, other))
    assert not items[0].acknowledged
    assert items[0].changed == other


def test_acknowledged_pages_sort_after_open_ones(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "first = 1\n"})
    write(root, {TARGET: "second = 2\n"})
    a = page("docs/a.html", f"<code>{TARGET}</code>", root=root)
    b = page("docs/b.html", f"<code>{TARGET}</code>", root=root)
    acknowledgements.record(Scope("worktree", root, (TARGET,)), ["docs/a.html"], "checked, no change needed")
    assert names(report(root, a, b, changed=(TARGET,))) == ["docs/b.html", "docs/a.html"]


def test_store_is_shared_with_worktrees_and_outside_the_tree(tmp_path: Path) -> None:
    (tmp_path / "main").mkdir()
    root = repo(tmp_path / "main", {TARGET: "first = 1\n"})
    other = tmp_path / "wt"
    git(root, "worktree", "add", "-q", str(other))
    assert acknowledgements.store_path(other).resolve() == acknowledgements.store_path(root).resolve()
    assert acknowledgements.store_path(root).resolve().is_relative_to((root / ".git").resolve())


@pytest.mark.parametrize("pages,reason,message", [
    ([], "a long enough reason", "name each page"),
    (["docs/x.html"], "short", "reason"),
    (["docs/missing.html"], "a long enough reason", "not a documentation page"),
])
def test_record_refuses_blanket_or_empty_acknowledgements(tmp_path: Path, pages, reason, message) -> None:
    root = repo(tmp_path, {TARGET: "a = 1\n", "docs/x.html": ""})
    with pytest.raises(ValueError, match=message):
        acknowledgements.record(Scope("worktree", root, (TARGET,)), pages, reason)


def test_record_refuses_when_nothing_changed(tmp_path: Path) -> None:
    root = repo(tmp_path, {"docs/x.html": ""})
    with pytest.raises(ValueError, match="no code file"):
        acknowledgements.record(Scope("worktree", root, ()), ["docs/x.html"], "a long enough reason")


def test_a_damaged_store_loses_entries_but_does_not_break(tmp_path: Path) -> None:
    root = repo(tmp_path, {TARGET: "a = 1\n"})
    store = acknowledgements.store_path(root)
    store.parent.mkdir(parents=True)
    store.write_text("not json\n", encoding="utf-8")
    assert acknowledgements.acknowledged(Scope("worktree", root, (TARGET,))) == set()
