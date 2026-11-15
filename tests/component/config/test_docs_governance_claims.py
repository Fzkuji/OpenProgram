"""Claims on documentation pages are checked against the parser and the repository."""
from __future__ import annotations

from pathlib import Path
import subprocess

from scripts.docs_site import html_source
from scripts.docs_site.governance import claims
from scripts.docs_site.governance.model import Corpus, Page, Scope


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@t", *arguments],
        cwd=root, capture_output=True, text=True, check=True).stdout


def repo(tmp_path: Path, files: dict[str, str]) -> Path:
    git(tmp_path, "init", "-q")
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "seed", "--allow-empty")
    return tmp_path


def page(path: str, body: str, zh: str | None = None) -> Page:
    full = '<title>T</title>\n<meta name="covers" content="none">\n<h1>T</h1>\n' + body
    return Page(path[len("docs/"):], path, path[:-5] + ".zh.html" if zh is not None else "", full, zh or "",
                html_source.parse(full), html_source.parse(zh) if zh is not None else None, False)


def run(root: Path, *pages: Page, mode: str = "all", changed: tuple[str, ...] = (), base: str | None = None):
    return list(claims.check(Corpus(Scope(mode, root, changed, base), list(pages))))


def rules(findings) -> list[str]:
    return [finding.rule for finding in findings]


def test_unknown_flag_names_file_line_and_a_fix(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a.txt": ""})
    found = run(root, page("docs/x.html", "<p>\n<code>openprogram web --no-such-flag</code></p>"))
    assert rules(found) == ["claims.flag"]
    assert found[0].path == "docs/x.html"
    assert found[0].line == 5
    assert "--no-such-flag" in found[0].message
    assert "Update" in found[0].fix or "Did you mean" in found[0].fix


def test_unknown_subcommand_is_caught_and_real_ones_pass(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a.txt": ""})
    assert rules(run(root, page("docs/x.html", "<code>openprogram sessionz list</code>"))) == ["claims.command"]
    assert run(root, page("docs/x.html", "<code>openprogram config list</code>")) == []


def test_placeholders_and_passthrough_are_not_flagged(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a.txt": ""})
    body = ("<code>openprogram config set &lt;key&gt; &lt;value&gt;</code>"
            "<code>openprogram mcp add drawio -- npx -y @drawio/mcp</code>"
            "<code>openprogram ...</code>")
    assert run(root, page("docs/x.html", body)) == []


def test_a_command_argparse_would_reject_is_caught(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a.txt": ""})
    found = run(root, page("docs/x.html", "<pre><code>openprogram mcp add drawio npx -y @drawio/mcp\n</code></pre>"))
    assert rules(found) == ["claims.flag"]


def test_commands_in_blocks_keep_their_own_line(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a.txt": ""})
    found = run(root, page("docs/x.html", "<pre>$ echo hi\n$ openprogram nope\n</pre>"))
    assert [(finding.rule, finding.line) for finding in found] == [("claims.command", 5)]


def test_moved_file_fails_and_existing_file_passes(tmp_path: Path) -> None:
    root = repo(tmp_path, {"openprogram/a/here.py": ""})
    assert run(root, page("docs/x.html", "<code>openprogram/a/here.py</code>")) == []
    found = run(root, page("docs/x.html", "<code>openprogram/a/gone.py</code>"))
    assert rules(found) == ["claims.path"]


def test_build_output_patterns_and_peer_clones_are_not_flagged(tmp_path: Path) -> None:
    root = repo(tmp_path, {"docs/.gitignore": "_site/\n", "docs/y.html": "", "references/keep": ""})
    body = ("<code>docs/_site/</code><code>docs/*.html</code><code>docs/&lt;name&gt;.html</code>"
            "<code>references/never-cloned/a.py</code>")
    assert run(root, page("docs/x.html", body)) == []


def test_example_marker_exempts_code_and_block(tmp_path: Path) -> None:
    root = repo(tmp_path, {"openprogram/a.py": ""})
    body = ('<code data-claim="example">openprogram/gone.py</code>'
            '<pre data-claim="invalid">openprogram nope</pre>'
            '<pre><code data-claim="invalid">openprogram nope2</code></pre>')
    assert run(root, page("docs/x.html", body)) == []


def test_github_link_is_a_path_claim(tmp_path: Path) -> None:
    root = repo(tmp_path, {"openprogram/a.py": ""})
    url = "https://github.com/FuzzyKuji/OpenProgram/blob/main/openprogram/gone.py"
    assert rules(run(root, page("docs/x.html", f'<a href="{url}">source</a>'))) == ["claims.path"]


def test_module_claims(tmp_path: Path) -> None:
    root = repo(tmp_path, {"openprogram/__init__.py": "", "openprogram/thing.py": "def real(): ...\n"})
    good = "<code>openprogram.thing</code><code>openprogram.thing.real</code><code>openprogram.io</code>"
    assert run(root, page("docs/x.html", good)) == []
    assert rules(run(root, page("docs/x.html", "<code>openprogram.thing.fake</code>"))) == ["claims.module"]
    assert rules(run(root, page("docs/x.html", "<code>openprogram.other</code>"))) == ["claims.module"]


def test_route_claims(tmp_path: Path) -> None:
    server = 'app.get("/api/agents/{agent_id}")\napp.post("/api/agents/{agent_id}/start")\n'
    root = repo(tmp_path, {"openprogram/interfaces/server/server.py": server})
    good = "<code>GET /api/agents/{id}</code><code>/api/agents/general</code><code>/api/agents/*</code>"
    assert run(root, page("docs/x.html", good)) == []
    assert rules(run(root, page("docs/x.html", "<code>/api/nope</code>"))) == ["claims.route"]
    assert rules(run(root, page("docs/x.html", "<code>/api/agents</code>"))) == ["claims.route"]


def test_environment_variable_claims(tmp_path: Path) -> None:
    root = repo(tmp_path, {"openprogram/a.py": "OPENPROGRAM_REAL = 1\n"})
    assert run(root, page("docs/x.html", "<code>OPENPROGRAM_REAL</code>")) == []
    assert rules(run(root, page("docs/x.html", "<code>OPENPROGRAM_GONE</code>"))) == ["claims.env"]


def test_config_argument_must_be_a_setting(tmp_path: Path) -> None:
    settings = 'SettingSpec(\n    key="ui.web_port", path=("ui",),\n)\n'
    root = repo(tmp_path, {"openprogram/state/settings.py": settings})
    assert run(root, page("docs/x.html", "<code>openprogram config get ui.web_port</code>")) == []
    found = run(root, page("docs/x.html", "<code>openprogram config set ui.renamed 1</code>"))
    assert rules(found) == ["claims.setting"]


def test_chinese_page_is_checked_too(tmp_path: Path) -> None:
    root = repo(tmp_path, {"openprogram/a.py": ""})
    found = run(root, page("docs/x.html", "<p>ok</p>", zh="<h1>T</h1><code>openprogram/gone.py</code>"))
    assert [(finding.rule, finding.path) for finding in found] == [("claims.path", "docs/x.zh.html")]


def test_page_outside_scope_is_failed_only_by_this_change(tmp_path: Path) -> None:
    root = repo(tmp_path, {"openprogram/a/old.py": "", "openprogram/a/keep.py": ""})
    stale = page("docs/x.html", "<code>openprogram/a/old.py</code>")
    unrelated_broken = page("docs/y.html", "<code>openprogram/a/never.py</code>")
    (root / "openprogram/a/old.py").unlink()
    found = run(root, stale, unrelated_broken, mode="worktree", changed=("openprogram/a/old.py",))
    assert [(finding.rule, finding.path) for finding in found] == [("claims.path", "docs/x.html")]


def test_cli_change_rechecks_commands_on_every_page(tmp_path: Path) -> None:
    root = repo(tmp_path, {"openprogram/interfaces/cli/parser.py": "x\n"})
    stale = page("docs/x.html", "<code>openprogram renamed-away</code>")
    assert run(root, stale, mode="worktree", changed=("openprogram/a.py",)) == []
    found = run(root, stale, mode="worktree", changed=("openprogram/interfaces/cli/parser.py",))
    assert rules(found) == ["claims.command"]


def test_server_change_rechecks_routes_on_every_page(tmp_path: Path) -> None:
    root = repo(tmp_path, {"openprogram/interfaces/server/routes.py": 'x = "/api/now"\n'})
    stale = page("docs/x.html", "<code>/api/before</code>")
    found = run(root, stale, mode="worktree", changed=("openprogram/interfaces/server/routes.py",))
    assert rules(found) == ["claims.route"]


def test_removed_environment_variable_rechecks_other_pages(tmp_path: Path) -> None:
    root = repo(tmp_path, {"openprogram/a.py": "OPENPROGRAM_OLD_NAME = 1\n"})
    (root / "openprogram/a.py").write_text("OPENPROGRAM_NEW_NAME = 1\n", encoding="utf-8")
    stale = page("docs/x.html", "<code>OPENPROGRAM_OLD_NAME</code>")
    assert rules(run(root, stale, mode="worktree", changed=("openprogram/a.py",))) == ["claims.env"]


def test_setting_removed_in_the_range_fails_a_page_still_naming_it(tmp_path: Path) -> None:
    before = 'SettingSpec(\n    key="ui.old_key", path=("ui",),\n)\nSettingSpec(key="ui.kept", path=())\n'
    root = repo(tmp_path, {"openprogram/state/settings.py": before})
    base = git(root, "rev-parse", "HEAD").strip()
    (root / "openprogram/state/settings.py").write_text('SettingSpec(key="ui.kept", path=())\n', encoding="utf-8")
    git(root, "commit", "-qam", "drop")
    stale = page("docs/x.html", "<p><code>ui.old_key</code></p>")
    fine = page("docs/y.html", "<p><code>ui.kept</code></p>")
    found = run(root, stale, fine, mode="range", changed=("openprogram/state/settings.py",), base=base)
    assert [(finding.rule, finding.path) for finding in found] == [("claims.setting", "docs/x.html")]


def test_page_without_any_tie_to_code_must_declare_one(tmp_path: Path) -> None:
    root = repo(tmp_path, {"openprogram/a/b/c.py": ""})
    bare = Page("x.html", "docs/x.html", "", "", "", html_source.parse("<h1>T</h1><p>words</p>"), None, False)
    assert rules(run(root, bare)) == ["claims.no-covers"]
    assert rules(run(root, page("docs/x.html", "<p>words</p>"))) == []
    declared = '<meta name="covers" content="openprogram/a/b/">'
    tied = Page("x.html", "docs/x.html", "", "", "", html_source.parse(declared + "<h1>T</h1>"), None, False)
    assert run(root, tied) == []


def test_covers_naming_a_missing_path_fails(tmp_path: Path) -> None:
    root = repo(tmp_path, {"openprogram/a.py": ""})
    source = '<meta name="covers" content="openprogram/moved/">\n<h1>T</h1>'
    bad = Page("x.html", "docs/x.html", "", "", "", html_source.parse(source), None, False)
    found = run(root, bad)
    assert rules(found) == ["claims.covers"]
    assert found[0].line == 1
