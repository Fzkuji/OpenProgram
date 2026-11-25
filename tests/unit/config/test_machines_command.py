"""``openprogram machines``: the parser names no method; the method's schema checks the settings."""
from __future__ import annotations

import json
import os

import pytest

from openprogram.host import machines
from openprogram.host.machines import here
from openprogram.interfaces.cli.commands.conversations import machines as command
from openprogram.interfaces.cli.parser import build_parser


@pytest.fixture(autouse=True)
def _no_resolver(monkeypatch):
    """Nothing is looked up: this computer has one fixed name, and a name the tests save is never this computer."""
    monkeypatch.setattr(here, "own_names", lambda: {"studio", "studio.local"})
    monkeypatch.setattr(here, "_resolve", lambda host: set())


@pytest.fixture
def cfg_path(tmp_path, monkeypatch, removed):
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    os.chmod(p, 0o600)
    monkeypatch.setattr("openprogram.state.paths.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.config.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(machines, "_remove_instances_later",
                        lambda machine_id: pytest.fail("the CLI exits before a background removal ends"))
    monkeypatch.setattr(machines, "remove_instances", lambda **kw: removed.append(kw))
    monkeypatch.setattr(command, "_load_kinds", lambda: None)
    return p


@pytest.fixture
def removed():
    """Machines whose instances were removed before the command returned."""
    return []


def _run(*argv):
    return command.dispatch(build_parser().parse_args(["machines", *argv]))


def test_the_parser_names_no_method():
    parser = build_parser()
    args = parser.parse_args(["machines", "add", "devbox", "--name", "Dev box",
                              "ssh", "target=me@devbox", "workdir=/srv/app"])
    assert args.machines_verb == "add" and args.id == "devbox" and args.name == "Dev box"
    assert args.connection == ["ssh", "target=me@devbox", "workdir=/srv/app"]
    assert parser.parse_args(["machines", "add", "lab", "anything", "url=http://lab:5000"]).connection == [
        "anything", "url=http://lab:5000"]
    for verb in ("list", "remove x", "enable x", "disable x", "test x"):
        assert parser.parse_args(["machines", *verb.split()]).machines_verb == verb.split()[0]
    # A project decides where its conversations run: there is no default machine.
    for argv in (["environments", "list"], ["machines", "default", "x"], ["machines", "add", "x", "--default"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def test_add_list_disable_remove(cfg_path, removed, capsys):
    assert _run("add", "devbox", "--name", "Dev box", "ssh", "target=me@devbox", "workdir=/srv/app") == 0
    assert "Saved machine 'devbox': SSH host me@devbox:/srv/app" in capsys.readouterr().out
    stored = json.loads(cfg_path.read_text())["machines"]
    assert "default_environment" not in stored
    assert stored["items"] == [{"id": "devbox", "name": "Dev box",
                                "connection": {"kind": "ssh", "target": "me@devbox", "workdir": "/srv/app"}}]

    assert _run("list") == 0
    out = capsys.readouterr().out
    assert "devbox" in out and "SSH host me@devbox" in out and "local" in out and "*" not in out

    assert _run("disable", "devbox") == 0
    # Its instances are gone when the command returns: the process exits right after.
    assert removed == [{"machine": "devbox"}]
    assert _run("enable", "devbox") == 0
    assert machines.get_machine("devbox").enabled
    assert _run("remove", "devbox") == 0
    assert machines.get_machine("devbox") is None
    assert removed == [{"machine": "devbox"}, {"machine": "devbox"}]


def test_settings_are_checked_by_the_method(cfg_path, capsys):
    assert _run("add", "x", "ssh", "workdir=relative") == 1
    assert "[error]" in capsys.readouterr().out
    assert _run("add", "x", "telnet") == 1
    assert "unknown connection method 'telnet'" in capsys.readouterr().out
    assert _run("add", "x", "ssh", "novalue") == 1
    assert "KEY=VALUE" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        build_parser().parse_args(["machines", "add", "x"])
    assert machines.list_machines()[1:] == []


def test_a_typed_setting_is_read_as_its_type():
    from openprogram.host.machines import kinds
    kinds.register(kinds.Kind(name="test_typed", label="Typed",
                              settings={"type": "object", "properties": {"port": {"type": "integer"},
                                                                         "name": {"type": "string"}}}))
    try:
        built = command.connection_from_args(["test_typed", "port=5900", "name=5900"])
        assert built == {"kind": "test_typed", "port": 5900, "name": "5900"}
    finally:
        kinds._kinds.pop("test_typed", None)


def test_unknown_machines_are_reported(cfg_path, capsys):
    for verb in ("remove", "enable", "disable", "test"):
        assert _run(verb, "ghost") == 1
        assert "unknown machine 'ghost'" in capsys.readouterr().out


def test_the_setup_wizard_offers_an_id_and_takes_it_on_enter(cfg_path):
    from openprogram.interfaces.cli.setup_sections.backend import _add_machine
    asked = {}

    def text(prompt, default=""):
        asked[prompt] = default
        return "me@devbox" if prompt.startswith("SSH target") else ""

    assert _add_machine(text, lambda prompt, choices, default: "SSH host") == "ssh"
    assert asked["Machine id (lowercase, e.g. devbox):"] == "ssh"
    assert machines.get_machine("ssh").connection.get("target") == "me@devbox"
