"""``openprogram install-skills``: a checkout's skills/ folder is copied, nothing is fetched.

The guard replaces ``subprocess.run`` so a command the installer would start
fails the test; that dependency keeps these out of the unit layer.
"""
from __future__ import annotations

from pathlib import Path


def _write_skill(dir_path: Path, slug: str, name: str, desc: str) -> None:
    sk = dir_path / slug
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\nbody\n",
        encoding="utf-8",
    )


def _legacy_install(tmp_path, monkeypatch, *, with_skills: bool):
    import shutil
    import subprocess

    import openprogram
    from openprogram.interfaces.cli.commands.capabilities.skills import _cmd_install_skills

    checkout = tmp_path / "checkout"
    (checkout / "openprogram").mkdir(parents=True)
    if with_skills:
        _write_skill(checkout / "skills", "echo", "echo-tool", "makes echoes")
    monkeypatch.setattr(openprogram, "__file__", str(checkout / "openprogram" / "__init__.py"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/claude" if name == "claude" else None)

    def no_network(*args, **kwargs):
        raise AssertionError(f"install --target ran a command: {args!r}")

    monkeypatch.setattr(subprocess, "run", no_network)
    return _cmd_install_skills


def test_legacy_install_without_a_skills_folder_says_so_and_fetches_nothing(tmp_path, monkeypatch, capsys):
    install = _legacy_install(tmp_path, monkeypatch, with_skills=False)
    try:
        install("claude")
    except SystemExit as stop:
        assert stop.code == 1
    else:
        raise AssertionError("expected the command to stop")
    out = capsys.readouterr().out
    assert "ships no bundled skills" in out
    assert "openprogram skills install <slug>" in out
    assert "http" not in out
    assert not (tmp_path / "home" / ".claude" / "skills").exists()


def test_legacy_install_copies_a_checkouts_skills_folder(tmp_path, monkeypatch, capsys):
    install = _legacy_install(tmp_path, monkeypatch, with_skills=True)
    install("claude")
    assert (tmp_path / "home" / ".claude" / "skills" / "echo" / "SKILL.md").is_file()
    assert "Installed 1 skills" in capsys.readouterr().out
