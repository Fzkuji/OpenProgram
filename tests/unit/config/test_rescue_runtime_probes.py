"""``openprogram rescue`` checks what this install actually runs.

An installed runtime (terminal release or App) ships Node.js, the terminal UI
and the web export, so rescue must not tell its users to install Node.js or
run npm builds; only a source checkout gets those hints.
"""
from __future__ import annotations

import sys
from pathlib import Path

from openprogram.interfaces.cli.commands.maintenance import rescue
from openprogram.interfaces.server import frontend


def _runtime_probes() -> list[rescue.Finding]:
    probes = (rescue._probe_node, rescue._probe_npm, rescue._probe_tui_bundle, rescue._probe_web_bundle)
    return [finding for finding in (probe() for probe in probes) if finding is not None]


def _installed_runtime(monkeypatch, tmp_path: Path, *, complete: bool) -> Path:
    runtime = tmp_path / "runtime"
    python = runtime / "py" / "python"
    files = [python]
    if complete:
        files += [runtime / "bin" / "node", runtime / "assets" / "tui" / "index.cjs",
                  tmp_path / "_frontend" / "index.html"]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    (runtime / "runtime-manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(rescue, "_source_root", lambda: None)
    monkeypatch.setattr(frontend, "out_dir", lambda: tmp_path / "_frontend")
    return runtime


def test_an_installed_runtime_reports_what_it_ships(monkeypatch, tmp_path):
    runtime = _installed_runtime(monkeypatch, tmp_path, complete=True)
    findings = _runtime_probes()
    assert [f.level for f in findings] == ["OK", "OK", "OK"]
    assert [f.label for f in findings] == ["Node.js", "Terminal UI (Ink)", "Web UI"]
    assert str(runtime / "bin" / "node") in findings[0].detail
    assert all(f.fix is None for f in findings)


def test_a_damaged_install_is_told_to_reinstall_not_to_build(monkeypatch, tmp_path):
    _installed_runtime(monkeypatch, tmp_path, complete=False)
    findings = _runtime_probes()
    assert [f.level for f in findings] == ["FAIL", "FAIL", "FAIL"]
    assert str(frontend.packaged_out_dir()) in findings[2].detail
    for finding in findings:
        assert finding.fix == rescue._REINSTALL
        assert "npm" not in finding.fix and "nodejs.org" not in finding.fix


def test_a_source_checkout_keeps_the_build_hints(monkeypatch, tmp_path):
    root = tmp_path / "checkout"
    (root / "apps" / "web").mkdir(parents=True)
    monkeypatch.setattr(rescue, "_source_root", lambda: root)
    monkeypatch.setattr("openprogram.interfaces.cli.ink.packaged_tui", lambda: None)
    monkeypatch.setattr(frontend, "out_dir", lambda: root / "apps" / "web" / "out")
    monkeypatch.setattr(rescue.shutil, "which", lambda _name: None)
    by_label = {f.label: f for f in _runtime_probes()}
    assert set(by_label) == {"Node.js", "npm on PATH", "Terminal UI (Ink)", "Web UI"}
    assert all(f.level == "WARN" for f in by_label.values())
    assert "nodejs.org" in by_label["Node.js"].fix
    assert "npm run build --workspace apps/cli" in by_label["Terminal UI (Ink)"].fix
    assert "npm run build --workspace apps/web" in by_label["Web UI"].fix
