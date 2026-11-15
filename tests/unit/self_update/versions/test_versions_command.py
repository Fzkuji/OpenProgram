"""`openprogram versions` and how a managed worker is started."""
from types import SimpleNamespace
import sys

from openprogram.interfaces.cli.application import build_parser
from openprogram.interfaces.cli.commands import versions as command
from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.worker import lifecycle


def test_the_parser_has_the_verbs():
    parsed = build_parser().parse_args(["versions", "use", "v1"])
    assert (parsed.command, parsed.versions_verb, parsed.version) == ("versions", "use", "v1")
    assert build_parser().parse_args(["versions", "restore-data", "pre-switch-u"]).backup == "pre-switch-u"


def test_an_install_without_versions_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    assert command._cmd_versions(SimpleNamespace(versions_verb="list")) == 1
    assert "does not keep separate versions" in capsys.readouterr().err


def test_it_runs_the_out_of_version_tool(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    (root / "bootstrap").mkdir(parents=True)
    (root / "bootstrap" / "versions.py").write_text("")
    (root / "current").write_text("")
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    seen = []
    monkeypatch.setattr(command.subprocess, "call", lambda argv: seen.append(argv) or 0)
    assert command._cmd_versions(SimpleNamespace(versions_verb="use", version="v1")) == 0
    assert seen[0][1:] == ["-I", "-B", str(root / "bootstrap/versions.py"), "--root", str(root), "use", "v1"]


def test_inside_a_version_it_uses_that_versions_root(tmp_path, monkeypatch):
    root = tmp_path / "elsewhere"
    venv = root / "versions" / "v1" / "venv"
    venv.mkdir(parents=True)
    (root / "bootstrap").mkdir()
    (root / "bootstrap" / "versions.py").write_text("")
    (root / "current").write_text("")
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "other")
    seen = []
    monkeypatch.setattr(command.subprocess, "call", lambda argv: seen.append(argv) or 0)
    assert command._cmd_versions(SimpleNamespace(versions_verb="list")) == 0
    assert str(root) in seen[0]


def test_a_sealed_version_finds_its_own_terminal_ui_and_the_bases_node(tmp_path, monkeypatch):
    from openprogram.interfaces.cli import ink
    from tests.support.sealed_versions import BASE, make_layout, make_version, point_at

    layout = make_layout(tmp_path / "runtime")
    version = make_version(layout, "v1")
    point_at(layout, "v1")
    bundle = paths.frontend_bundle(paths.site_packages(version, BASE)) / "tui" / "index.cjs"
    bundle.parent.mkdir()
    bundle.write_text("// tui")
    node = layout.base(BASE) / "bin" / "node"
    node.parent.mkdir()
    node.write_text("")
    monkeypatch.setattr(sys, "prefix", str(version / "venv"))
    assert ink._resolve_cli_entry() == bundle
    assert ink._resolve_node() == str(node)


def _managed(tmp_path, monkeypatch):
    venv = tmp_path / "versions" / "v1" / "venv"
    venv.mkdir(parents=True)
    (tmp_path / "current").write_text("")
    monkeypatch.setattr(sys, "prefix", str(venv))
    return paths.Layout(tmp_path)


def test_a_managed_worker_starts_through_the_launcher(tmp_path, monkeypatch):
    layout = _managed(tmp_path, monkeypatch)
    assert lifecycle._detached_worker_command() == [
        str(layout.launcher()), "-u", "-m", "openprogram", "worker", "run",
    ]


def test_a_managed_cli_stays_on_its_own_python(tmp_path, monkeypatch):
    _managed(tmp_path, monkeypatch)
    assert lifecycle.worker_executable() == sys.executable


def test_an_unmanaged_worker_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert lifecycle._detached_worker_command()[-4:] == ["-m", "openprogram", "worker", "run"]
    assert "openprogram-launcher" not in lifecycle._detached_worker_command()[0]
