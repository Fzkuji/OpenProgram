"""agy started on the gateway: the function in the user's shell profiles,
``openprogram agy run|env``, agy's own effort and fast-tools settings, and
Devin's effort, after yetone/magpie's tests of the same
(``internal/update/shellfn_test.go``, ``internal/agent/agy_test.go``,
``agy_effort_test.go``, ``internal/provider/agenteffort_test.go``,
``devin_test.go``)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openprogram.models.gateway.agents import shellfn
from openprogram.models.gateway.agents.shellfn import Profile


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("OPENPROGRAM_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    from openprogram.models.gateway import address, conversion
    from openprogram.models.gateway.agents import base
    monkeypatch.setattr(address, "worker_base_url", lambda stable=False: "http://127.0.0.1:18100")
    monkeypatch.setattr(base, "_tool_dirs", lambda: [])
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [
        {"id": "deepseek/deepseek-chat", "name": "DS", "provider": "deepseek", "thinking_levels": [],
         "reasoning": False},
        {"id": "openai/gpt-5", "name": "GPT-5", "provider": "openai", "reasoning": True,
         "thinking_levels": ["minimal", "low", "medium", "high"]},
    ])
    monkeypatch.setattr(shellfn, "shell_profiles", lambda: [Profile("posix", tmp_path / ".zshrc", True),
                                                            Profile("fish", tmp_path / ".config" / "fish" / "config.fish", False)])
    return tmp_path


# The block in a profile (magpie TestWithFnBlock, TestShellFunction)

@pytest.mark.parametrize("text,body,want", [
    ("", "x", "# >>> openprogram agy >>>\nx\n# <<< openprogram agy <<<\n"),
    ("a\n", "x", "a\n\n# >>> openprogram agy >>>\nx\n# <<< openprogram agy <<<\n"),
    ("a\n\n# >>> openprogram agy >>>\nx\n# <<< openprogram agy <<<\n", "", "a\n"),
    ("a\n\n# >>> openprogram agy >>>\nx\n# <<< openprogram agy <<<\nb\n", "", "a\nb\n"),
    ("a\n# >>> openprogram agy >>>\nb\n", "", "a\nb\n"),  # its close line cut
    ("# >>> conda initialize >>>\nc\n# <<< conda initialize <<<\n", "",
     "# >>> conda initialize >>>\nc\n# <<< conda initialize <<<\n"),
    ("a", "", "a"),
])
def test_with_block(text, body, want) -> None:
    assert shellfn.with_fn_block(text, "agy", body) == want


def test_shell_function_in_and_out(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    rc = tmp_path / ".zshrc"
    fish = tmp_path / ".config" / "fish" / "config.fish"
    ps = tmp_path / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
    broken = tmp_path / ".bashrc"
    outside = tmp_path.parent / f"{tmp_path.name}-outside" / ".zshrc"
    profiles = [Profile("posix", rc, False), Profile("fish", fish, False), Profile("powershell", ps, False),
                Profile("posix", broken, False), Profile("posix", outside, True)]
    monkeypatch.setattr(shellfn, "shell_profiles", lambda: profiles)
    mine = "export PATH=$HOME/bin:$PATH\nalias ll='ls -l'"  # no newline at the end
    rc.write_text(mine)
    ps.parent.mkdir(parents=True)
    ps.write_bytes(b"\xef\xbb\xbfSet-Alias ll Get-ChildItem\r\n")
    broken.mkdir()  # its read fails: what it holds is unknown, and it is left
    exe = str(tmp_path / "Applications" / "openprogram")
    monkeypatch.setattr(shellfn, "cli_program", lambda: [exe])
    body = lambda kind, prog: f"agy() {{ # {kind}\n  '{prog[0]}' agy run \"$@\"\n}}"  # noqa: E731

    assert shellfn.shell_function("agy", body) == [str(rc), str(ps)]
    want = mine + f"\n\n# >>> openprogram agy >>>\nagy() {{ # posix\n  '{exe}' agy run \"$@\"\n}}\n# <<< openprogram agy <<<\n"
    assert rc.read_text() == want
    raw = ps.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbfSet-Alias ll Get-ChildItem\r\n\r\n# >>> openprogram agy >>>\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert not fish.exists() and not outside.exists()
    assert shellfn.shell_function_in("agy") == [str(rc), str(ps)]

    # again: the same block, the file not written
    old = rc.stat().st_mtime - 3600
    os.utime(rc, (old, old))
    shellfn.shell_function("agy", body)
    assert rc.read_text() == want and rc.stat().st_mtime == old

    # the user adds lines after it; the block is rewritten where it is
    rc.write_text(want + "export EDITOR=vim\n")
    moved = str(tmp_path / "moved" / "openprogram")
    monkeypatch.setattr(shellfn, "cli_program", lambda: [moved])
    shellfn.shell_function("agy", body)
    assert rc.read_text() == want.replace(exe, moved) + "export EDITOR=vim\n"

    # out: the user's lines as they were
    shellfn.drop_shell_function("agy")
    assert rc.read_text() == mine + "\nexport EDITOR=vim\n"
    assert ps.read_bytes() == b"\xef\xbb\xbfSet-Alias ll Get-ChildItem\r\n"
    assert shellfn.shell_function_in("agy") == []

    # the shell a terminal opens gets a profile made for it
    profiles[1] = Profile("fish", fish, True)
    shellfn.shell_function("agy", body)
    assert fish.read_text() == f"# >>> openprogram agy >>>\nagy() {{ # fish\n  '{moved}' agy run \"$@\"\n}}\n# <<< openprogram agy <<<\n"


def test_a_linked_profile_stays_a_link(tmp_path, monkeypatch) -> None:
    """magpie edit.Target, hardLinked: a profile linked into a dotfiles folder
    keeps its link, the file it names written; one with other hard links is
    written in place, every name getting the block."""
    monkeypatch.setenv("HOME", str(tmp_path))
    real = tmp_path / "dotfiles" / "zshrc"
    real.parent.mkdir()
    real.write_text("export A=1\n")
    link = tmp_path / ".zshrc"
    link.symlink_to(real)
    other = tmp_path / "dotfiles" / "bashrc"
    other.write_text("export B=1\n")
    hard = tmp_path / ".bashrc"
    os.link(other, hard)
    profiles = [Profile("posix", link, True), Profile("posix", hard, True)]
    shellfn.shell_function_at(profiles, "agy", lambda kind: "agy() { :; }")
    assert link.is_symlink() and "# >>> openprogram agy >>>" in real.read_text()
    assert os.path.samefile(hard, other) and "# >>> openprogram agy >>>" in other.read_text()
    shellfn.drop_shell_function_at(profiles, "agy")
    assert link.is_symlink() and real.read_text() == "export A=1\n"
    assert os.path.samefile(hard, other) and other.read_text() == "export B=1\n"
    assert sorted(p.name for p in real.parent.iterdir()) == ["bashrc", "zshrc"]  # no temp file left


def test_every_editor_keeps_links(tmp_path) -> None:
    """edit.write_atomic, which every agent's config goes through, writes a
    symlink's file and a hard-linked file in place too."""
    from openprogram.models.gateway import edit
    real = tmp_path / "real.json"
    real.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    edit.write_atomic(link, '{"a": 1}')
    assert link.is_symlink() and real.read_text() == '{"a": 1}'
    hard = tmp_path / "hard.json"
    os.link(real, hard)
    edit.write_atomic(hard, '{"b": 2}')
    assert os.path.samefile(hard, real) and real.read_text() == '{"b": 2}'


def test_the_program_never_runs_code_from_the_cwd(tmp_path, monkeypatch) -> None:
    """``python -m`` puts the cwd first on sys.path: typing agy in a cloned
    repo must not run its json.py or openprogram/ (``-I``, as the App starts)."""
    import subprocess
    import sys
    repo = tmp_path / "cloned"
    (repo / "openprogram").mkdir(parents=True)
    marker = tmp_path / "ran"
    plant = f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(7)\n"
    (repo / "json.py").write_text(plant)
    (repo / "openprogram" / "__init__.py").write_text(plant)
    (repo / "openprogram" / "__main__.py").write_text(plant)
    monkeypatch.delenv("OPENPROGRAM_PROFILE", raising=False)
    prog = shellfn.cli_program()
    assert prog[:3] == [sys.executable, "-I", "-m"]
    env = dict(os.environ, HOME=str(tmp_path), OPENPROGRAM_DATA_DIR=str(tmp_path / "state"))
    done = subprocess.run([*prog, "agy", "env"], cwd=repo, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert not marker.exists()
    # the profile still goes with it: -I drops only Python's own variables
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "dev")
    assert shellfn.cli_program()[-2:] == ["--profile", "dev"]


# agy on the gateway

def _agy(home: Path) -> Path:
    folder = home / ".gemini" / "antigravity-cli"
    folder.mkdir(parents=True)
    (folder / "settings.json").write_text(json.dumps({"model": "mine"}))
    return folder


def test_agy_gets_its_function_on_connect_and_loses_it_on_disconnect(home) -> None:
    from openprogram.models.gateway.agents import agy, engine
    _agy(home)
    rc = home / ".zshrc"
    rc.write_text("# mine\n")
    engine.connect("agy", {"model": "openai/gpt-5"})
    text = rc.read_text()
    assert text.startswith("# mine\n\n# >>> openprogram agy >>>\nunalias agy 2>/dev/null || true\nagy() {\n")
    assert "'-m' 'openprogram' agy run \"$@\"" in text and "command agy \"$@\"" in text
    status = engine.status("agy")
    assert status["notice"].startswith("Type agy in a new terminal: OpenProgram added it to ~/.zshrc")
    assert status["launch"] in status["notice"]
    assert not (home / ".config" / "fish" / "config.fish").exists()

    engine.disconnect("agy")
    assert rc.read_text() == "# mine\n"


def test_agy_functions_for_each_shell() -> None:
    from openprogram.models.gateway.agents.agy import function
    prog = ["/a b/python", "-m", "openprogram"]
    posix = function("posix", prog)
    assert "if [ -x '/a b/python' ]; then '/a b/python' '-m' 'openprogram' agy run \"$@\"" in posix
    assert "function agy\n    if test -x '/a b/python'\n" in function("fish", prog)
    ps = function("powershell", prog)
    assert "& '/a b/python' '-m' 'openprogram' agy env" in ps and "--settings $start['settings']" in ps


def test_agy_env_and_run(home, monkeypatch) -> None:
    from openprogram.models.gateway.agents import agy, engine
    from openprogram.models.gateway.keys import token_for
    assert agy.env_lines() == []
    folder = _agy(home)
    engine.connect("agy", {"model": "openai/gpt-5"})
    over = folder / "openprogram-settings.json"
    assert agy.env_lines() == [f"GEMINI_API_KEY={token_for('agy')}",
                               "GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:18100/gateway/gemini",
                               f"settings={over}", "model=openprogram/openai/gpt-5"]

    bin_dir = home / "bin"
    bin_dir.mkdir()
    (bin_dir / "agy").write_text("#!/bin/sh\n")
    (bin_dir / "agy").chmod(0o755)
    started = []
    monkeypatch.setattr(os, "execve", lambda path, argv, env: started.append((argv, env)))
    monkeypatch.setenv("gemini_api_key", "mine")
    agy.run(["-p", "hi"])
    argv, env = started[-1]
    assert argv == ["agy", "--settings", str(over), "--model", "openprogram/openai/gpt-5", "-p", "hi"]
    assert env["GEMINI_API_KEY"] == token_for("agy") and "gemini_api_key" not in env
    agy.run(["--model=openprogram/deepseek/deepseek-chat"])
    assert started[-1][0] == ["agy", "--settings", str(over), "--model=openprogram/deepseek/deepseek-chat"]
    agy.run(["remote-control"])
    assert started[-1][0] == ["agy", "remote-control"] and "GOOGLE_GEMINI_BASE_URL" not in started[-1][1]


def test_agy_model_picked_in_agy_moves_into_openprograms_file(home) -> None:
    """magpie agy_test.go migrate: agy's /model writes its pick into settings.json."""
    from openprogram.models.gateway.agents import agy, engine
    folder = _agy(home)
    engine.connect("agy", {"model": "openai/gpt-5"})
    (folder / "settings.json").write_text(json.dumps({"model": "openprogram/openai/gpt-5:high"}))
    engine.sync("agy")
    assert json.loads((folder / "settings.json").read_text()) == {"model": "mine"}
    assert agy.override_model() == "openprogram/openai/gpt-5:high"


def test_agy_sync_keeps_the_model_picked_in_agy(home, monkeypatch) -> None:
    """A sync writes the model list, never agy's pick of a model in it."""
    from openprogram.models.gateway import conversion
    from openprogram.models.gateway.agents import agy, engine
    folder = _agy(home)
    engine.connect("agy", {"model": "openai/gpt-5"})
    (folder / "settings.json").write_text(json.dumps({"model": "openprogram/openai/gpt-5:high"}))
    engine.sync("agy")  # migrate moves it into OpenProgram's file
    monkeypatch.setattr(conversion, "enabled_model_refs", lambda: [
        {"id": "openai/gpt-5", "name": "GPT-5", "provider": "openai", "reasoning": True,
         "thinking_levels": ["low", "medium", "high"]}])
    engine.sync("agy")
    assert agy.override_model() == "openprogram/openai/gpt-5:high"
    over = json.loads((folder / "openprogram-settings.json").read_text())
    assert "openprogram/deepseek/deepseek-chat" not in over["customModelsConfig"]["customModels"]
    # a choice changed in the row keeps it too; another model picked there replaces it
    engine.connect("agy", {"model": "openai/gpt-5", "effort": "low"})
    assert agy.override_model() == "openprogram/openai/gpt-5:high"
    engine.connect("agy", {"model": "deepseek/deepseek-chat"})
    assert agy.override_model() == "openprogram/deepseek/deepseek-chat"


def test_agy_disconnect_takes_its_model_pick_out_of_settings(home) -> None:
    """magpie Disconnect runs migrate first: a /model pick agy wrote into
    settings.json goes, and the user's own model is back."""
    from openprogram.models.gateway.agents import agy, engine
    folder = _agy(home)
    engine.connect("agy", {"model": "deepseek/deepseek-chat"})
    (folder / "settings.json").write_text(json.dumps({"model": "openprogram/openai/gpt-5"}))
    engine.disconnect("agy")
    assert json.loads((folder / "settings.json").read_text()) == {"model": "mine"}
    assert not agy.override_path().exists()


def test_agy_row_pick_wins_over_an_earlier_model_pick_in_agy(home) -> None:
    """magpie's Set drops migrate's pick: another model picked on the row is
    the one agy starts on, even with a /model pick still in settings.json."""
    from openprogram.models.gateway.agents import agy, engine
    folder = _agy(home)
    engine.connect("agy", {"model": "deepseek/deepseek-chat"})
    (folder / "settings.json").write_text(json.dumps({"model": "openprogram/openai/gpt-5"}))  # agy's /model
    status = engine.connect("agy", {"model": "openai/gpt-5"})
    assert agy.override_model() == "openprogram/openai/gpt-5"
    (folder / "settings.json").write_text(json.dumps({"model": "openprogram/openai/gpt-5:high"}))
    status = engine.connect("agy", {"model": "deepseek/deepseek-chat"})
    assert agy.override_model() == "openprogram/deepseek/deepseek-chat"
    assert status["launch"].endswith("--model 'openprogram/deepseek/deepseek-chat'") or os.name == "nt"
    assert json.loads((folder / "settings.json").read_text()) == {"model": "mine"}
    # the same model kept on the row: agy's pick stays
    (folder / "settings.json").write_text(json.dumps({"model": "openprogram/openai/gpt-5:high"}))
    engine.connect("agy", {"model": "deepseek/deepseek-chat"})
    assert agy.override_model() == "openprogram/openai/gpt-5:high"


def test_cli_passthrough(home, monkeypatch, capsys) -> None:
    from openprogram.interfaces.cli.application import _agent_passthrough
    from openprogram.models.gateway.agents import fasttools
    assert _agent_passthrough(["web"]) is None
    assert _agent_passthrough(["agy", "env"]) == 0 and capsys.readouterr().out == ""
    assert _agent_passthrough(["agy"]) == 2 and "openprogram agy run" in capsys.readouterr().err
    seen = []
    monkeypatch.setattr(fasttools, "run", lambda args: seen.append(args) or 0)
    assert _agent_passthrough(["fast-tools", "mcp"]) == 0 and seen == [["mcp"]]


# agy's own settings

def test_agy_effort_is_a_choice_and_the_gateway_applies_it(home) -> None:
    """magpie agenteffort_test.go: a pick replaces the request's level unless agy's request named one."""
    from openprogram.models.gateway import agent_effort
    from openprogram.models.gateway.agents import engine
    from openprogram.models.providers.types import Model
    _agy(home)
    assert not any(f["key"] == "effort" for f in engine.status("agy")["fields"])  # nothing to pick while off
    engine.connect("agy", {"model": "openai/gpt-5"})
    effort = next(f for f in engine.status("agy")["fields"] if f["key"] == "effort")
    assert [o["value"] for o in effort["options"]] == ["minimal", "low", "medium", "high"]

    engine.connect("agy", {"model": "openai/gpt-5", "effort": "high"})
    assert agent_effort.agent_effort("agy") == "high"
    model = Model(id="gpt-5", name="GPT-5", api="openai-responses", provider="openai", base_url="", reasoning=True)
    assert agent_effort.applied("agy", model, None) == "high"
    assert agent_effort.applied("agy", model, "low", level_asked=True) == "low"
    assert agent_effort.applied("codex", model, None) is None
    plain = Model(id="x", name="x", api="openai-responses", provider="openai", base_url="", reasoning=False)
    assert agent_effort.applied("agy", plain, None) is None
    # the user's own setting (magpie Field.Own): it outlives disconnecting and comes back with the next connect
    engine.disconnect("agy")
    assert agent_effort.agent_effort("agy") == "high"
    assert engine.status("agy")["values"].get("effort") == "high"
    engine.connect("agy", {"model": "openai/gpt-5", "effort": "high"})
    assert agent_effort.agent_effort("agy") == "high"
    engine.connect("agy", {"model": "openai/gpt-5"})
    assert agent_effort.agent_effort("agy") == ""


def test_agy_level_asked() -> None:
    from openprogram.models.gateway.gemini import level_asked
    assert level_asked({"generationConfig": {"thinkingConfig": {"thinkingLevel": "high"}}})
    assert level_asked({"generation_config": {"thinking_config": {"thinking_level": "low"}}})
    assert not level_asked({"generationConfig": {"thinkingConfig": {"thinkingBudget": 100}}})


def test_agy_fast_tools_choice(home) -> None:
    from openprogram.models.gateway.agents import engine, fasttools
    _agy(home)
    engine.connect("agy", {"model": "openai/gpt-5"})
    row = next(f for f in engine.status("agy")["fields"] if f["key"] == "fasttools")
    assert [o["value"] for o in row["options"]] == ["on"] and not fasttools.is_on(home)
    # only the choice changes, not agy's files: the plugin still follows it
    engine.connect("agy", {"model": "openai/gpt-5", "fasttools": "on"})
    assert fasttools.is_on(home)
    engine.connect("agy", {"model": "openai/gpt-5"})
    assert not fasttools.is_on(home)
    engine.connect("agy", {"model": "openai/gpt-5", "fasttools": "on"})
    engine.disconnect("agy")
    assert fasttools.is_on(home)  # the user's own setting, left by a disconnect
    assert engine.status("agy")["values"].get("fasttools") == "on"


def test_kept_picks_outlive_and_clear(home) -> None:
    from openprogram.models.gateway import kept_picks
    assert kept_picks.kept("agy") == {}
    kept_picks.keep("agy", {"effort": "high", "fasttools": "on"})
    kept_picks.keep("AGY", {"fasttools": ""})
    assert kept_picks.kept("agy") == {"effort": "high"}
    kept_picks.keep("agy", {"effort": ""})
    assert kept_picks.kept("agy") == {}


def test_route_takes_the_choices(home, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes.settings import harness_models
    from openprogram.models.gateway.agents import devin
    app = FastAPI()
    harness_models.register(app)
    client = TestClient(app)
    _agy(home)
    answer = client.put("/api/harnesses/models", json={"harness": "agy", "model": "openai/gpt-5",
                                                       "tiers": {"effort": "medium"}})
    assert answer.status_code == 200 and answer.json()["values"] == {"model": "openai/gpt-5", "effort": "medium"}
    assert answer.json()["notice"].startswith("Type agy in a new terminal")

    families = devin.parse_models(json.dumps(DEVIN_LIST))
    monkeypatch.setattr(devin, "families", lambda wait=False: families)
    path = home / ".config" / "devin" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"agent": {"model": "swe-2-max"}}')
    answer = client.put("/api/harnesses/models", json={"harness": "devin", "model": "swe-2", "tiers": {"effort": "medium"}})
    assert answer.status_code == 200 and answer.json()["values"] == {"model": "swe-2", "effort": "medium"}
    assert json.loads(path.read_text())["agent"]["model"] == "swe-2-medium"
    # the model changes; the effort picked goes with it, as near as it has it
    client.put("/api/harnesses/models", json={"harness": "devin", "model": "swe-2-fast"})
    assert json.loads(path.read_text())["agent"]["model"] == "swe-2-high-fast"
    effort = next(f for f in client.get("/api/harnesses/models").json()["harnesses"] if f["id"] == "devin")
    assert [o["value"] for o in next(f for f in effort["fields"] if f["key"] == "effort")["options"]] == ["low", "high"]


def test_fast_tools_goes_off_after_a_disconnect(home) -> None:
    """magpie Field.Own: the user's own setting is set while agy is off
    OpenProgram too, and the row offers it then."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from openprogram.interfaces.server.routes.settings import harness_models
    from openprogram.models.gateway import kept_picks
    from openprogram.models.gateway.agents import fasttools
    app = FastAPI()
    harness_models.register(app)
    client = TestClient(app)
    _agy(home)
    client.put("/api/harnesses/models", json={"harness": "agy", "model": "openai/gpt-5", "tiers": {"fasttools": "on"}})
    client.put("/api/harnesses/models", json={"harness": "agy", "model": ""})
    assert fasttools.is_on(home)
    row = next(r for r in client.get("/api/harnesses/models").json()["harnesses"] if r["id"] == "agy")
    field = next(f for f in row["fields"] if f["key"] == "fasttools")
    assert field["kept"] and row["model"] == "" and row["tiers"]["fasttools"] == "on"
    answer = client.put("/api/harnesses/models", json={"harness": "agy", "tiers": {"fasttools": ""}})
    assert answer.status_code == 200 and answer.json()["tiers"]["fasttools"] == ""
    assert not fasttools.is_on(home) and kept_picks.kept("agy") == {}
    assert answer.json()["config"]["applied"] is False
    client.put("/api/harnesses/models", json={"harness": "agy", "tiers": {"fasttools": "on"}})
    assert fasttools.is_on(home)


# Devin's effort

DEVIN_LIST = {"families": [
    {"family_uid": "swe-2", "family_label": "SWE-2", "variants": [
        {"model_uid": "swe-2-high", "label": "SWE-2 High"}, {"model_uid": "swe-2-medium", "label": "SWE-2 Medium"},
        {"model_uid": "swe-2-max", "label": "SWE-2 Max"},
        {"model_uid": "swe-2-low-fast", "label": "SWE-2 Low Fast"},
        {"model_uid": "swe-2-high-fast", "label": "SWE-2 High Fast"}]},
    {"family_uid": "glm-5.2", "family_label": "GLM-5.2", "variants": [
        {"model_uid": "glm-5-2-1m", "label": "GLM-5.2 1M"}]},
]}


def test_devin_effort_options_follow_the_model(monkeypatch) -> None:
    from openprogram.models.gateway.agents import devin, engine
    families = devin.parse_models(json.dumps(DEVIN_LIST))
    assert devin.efforts_of(families, "swe-2") == ["medium", "high", "max"]
    assert devin.efforts_of(families, "swe-2-fast") == ["low", "high"]
    assert devin.efforts_of(families, "glm-5-2-1m") == [] and devin.efforts_of(families, "gone") == []
    monkeypatch.setattr(devin, "families", lambda wait=False: families)
    monkeypatch.setattr(devin, "picked", lambda agent_id: {"model": "swe-2-fast"})
    assert devin._effort_options() == [("low", "low"), ("high", "high")]
