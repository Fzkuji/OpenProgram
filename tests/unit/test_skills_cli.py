"""Tests for ``openprogram skills list`` / ``skills doctor``."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openprogram.cli import _cmd_skills_doctor, _cmd_skills_list


def _write_skill(dir_path: Path, slug: str, name: str, desc: str) -> None:
    sk = dir_path / slug
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\nbody\n",
        encoding="utf-8",
    )


def test_list_renders_discovered_skills(tmp_path, capsys):
    _write_skill(tmp_path, "echo", "echo-tool", "makes echoes for things")
    _write_skill(tmp_path, "ping", "ping-tool", "ICMP on demand")

    rc = _cmd_skills_list([str(tmp_path)], as_json=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "echo-tool" in out
    assert "ping-tool" in out
    assert "makes echoes" in out


def test_list_json_mode(tmp_path, capsys):
    _write_skill(tmp_path, "echo", "echo-tool", "makes echoes")
    rc = _cmd_skills_list([str(tmp_path)], as_json=True)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["name"] == "echo-tool"
    assert data[0]["slug"] == "echo"


def test_list_empty_when_no_dirs_exist(tmp_path, capsys):
    rc = _cmd_skills_list([str(tmp_path / "nope")], as_json=False)
    assert rc == 0
    assert "no skills discovered" in capsys.readouterr().out.lower()


def test_doctor_clean_passes(tmp_path, capsys):
    _write_skill(tmp_path, "good", "good-tool", "works fine")
    rc = _cmd_skills_doctor([str(tmp_path)])
    assert rc == 0
    assert "OK" in capsys.readouterr().out


def test_doctor_flags_missing_skill_md(tmp_path, capsys):
    (tmp_path / "broken").mkdir()
    rc = _cmd_skills_doctor([str(tmp_path)])
    assert rc == 1
    assert "missing SKILL.md" in capsys.readouterr().out


def test_doctor_flags_missing_front_matter(tmp_path, capsys):
    slug = tmp_path / "noyaml"
    slug.mkdir()
    (slug / "SKILL.md").write_text("just body, no front matter\n", encoding="utf-8")
    rc = _cmd_skills_doctor([str(tmp_path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "front matter" in out


def test_doctor_flags_missing_name_or_description(tmp_path, capsys):
    slug = tmp_path / "partial"
    slug.mkdir()
    (slug / "SKILL.md").write_text(
        "---\nname: has-only-name\n---\nbody\n", encoding="utf-8",
    )
    rc = _cmd_skills_doctor([str(tmp_path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "description" in out


def test_doctor_flags_duplicate_names(tmp_path, capsys):
    d1 = tmp_path / "a"
    d2 = tmp_path / "b"
    _write_skill(d1, "sk", "dup-name", "first")
    _write_skill(d2, "sk2", "dup-name", "second")
    rc = _cmd_skills_doctor([str(d1), str(d2)])
    assert rc == 1
    assert "duplicate name" in capsys.readouterr().out
