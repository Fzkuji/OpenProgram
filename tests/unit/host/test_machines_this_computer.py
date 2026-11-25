"""Never this computer: a machine that is this computer under another name is refused when it is saved.

This computer is built in as ``local``. An SSH target that resolves here, a
screen address another machine already has and a method's own check at save
(``Kind.admit``) are refused, each with one sentence. The resolver is faked:
nothing is looked up.
"""
from __future__ import annotations

import json
import os

import pytest

from openprogram.host import machines
from openprogram.host.machines import MachineError, here, kinds
from openprogram.host.machines.kinds import KindError

OWN_NAMES = {"studio.example.lan", "studio", "studio.local"}
RESOLVER = {
    "studio.example.lan": {"192.168.1.20"},
    "studio": {"192.168.1.20"},
    "studio.local": {"192.168.1.20", "fe80::1"},
    "loop.example": {"127.0.1.1"},
    "myself.example": {"192.168.1.20"},
    "devbox": {"192.168.1.40"},
}


@pytest.fixture(autouse=True)
def resolver(monkeypatch):
    monkeypatch.setattr(here, "own_names", lambda: set(OWN_NAMES))
    monkeypatch.setattr(here, "_resolve", lambda host: set(RESOLVER.get(host, set())))


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    os.chmod(p, 0o600)
    monkeypatch.setattr("openprogram.state.paths.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.config.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(machines, "_remove_instances_later", lambda machine_id: None)
    return p


@pytest.fixture
def addressed(monkeypatch):
    """A connection method with an address, and a record of what its admit was given."""
    admitted: list[dict] = []

    def admit(settings: dict) -> None:
        admitted.append(dict(settings))
        if settings.get("address") == "refuse-me":
            raise KindError("Refused: this test screen is this computer.")

    kinds.get("")
    monkeypatch.setitem(kinds._kinds, "test_addressed", kinds.Kind(
        name="test_addressed", label="Test screen",
        settings={"type": "object", "properties": {"address": {"type": "string"},
                                                   "password": {"type": "string", "secret": True}}},
        address=lambda settings: str(settings.get("address") or ""), admit=admit))
    return admitted


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST.", "app.localhost", "127.0.0.1", "127.4.5.6", "::1",
                                  "[::1]", "0.0.0.0", "::ffff:127.0.0.1"])
def test_loopback_is_read_without_resolving(host, monkeypatch):
    monkeypatch.setattr(here, "_resolve", lambda name: pytest.fail("nothing is resolved"))
    assert here.is_loopback(host)


@pytest.mark.parametrize("host", ["devbox", "10.0.0.5", "", None, "localhost.example.com"])
def test_other_hosts_are_not_loopback(host):
    assert not here.is_loopback(host)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "studio", "STUDIO.local", "studio.example.lan",
                                  "loop.example", "myself.example", "192.168.1.20"])
def test_this_computer_by_name_loopback_or_its_own_address(host):
    assert here.is_this_computer(host)


@pytest.mark.parametrize("host", ["devbox", "192.168.1.40", "no-such-host", ""])
def test_another_host_or_a_name_that_does_not_resolve_is_not_this_computer(host):
    assert not here.is_this_computer(host)


@pytest.mark.parametrize("target", ["localhost", "me@localhost", "me@127.0.0.1", "ssh://me@[::1]",
                                    "ssh://me@127.0.0.1:22", "me@studio", "me@loop.example"])
def test_an_ssh_target_that_is_this_computer_is_refused(cfg_path, target):
    with pytest.raises(MachineError) as caught:
        machines.save_machine({"id": "self", "connection": {"kind": "ssh", "target": target}})
    message = str(caught.value)
    assert message.startswith(f"Refused: the SSH target {target} is this computer")
    assert message.count(". ") == 0 and message.endswith(".")
    assert machines.get_machine("self") is None


# Another port on a loopback address is another sshd: a virtual machine or a container forwarded here.
@pytest.mark.parametrize("target", ["me@devbox", "me@no-such-host", "build-alias", "ssh://me@127.0.0.1:2222",
                                    "ssh://vagrant@[::1]:2200"])
def test_another_ssh_target_is_saved(cfg_path, target):
    assert machines.save_machine({"id": "box", "connection": {"kind": "ssh", "target": target}}).id == "box"


def test_two_machines_may_not_share_one_screen_address(cfg_path, addressed):
    machines.save_machine({"id": "lab", "connection": {"kind": "test_addressed", "address": "http://10.0.0.5:5000/"}})
    with pytest.raises(MachineError) as caught:
        machines.save_machine({"id": "lab-2",
                               "connection": {"kind": "test_addressed", "address": "HTTP://10.0.0.5:5000"}})
    assert str(caught.value) == ("Refused: machine 'lab' already has the screen address 10.0.0.5:5000, and two "
                                 "machines may not share one screen address.")
    # Another port is another screen, and a machine keeps its own address when it is edited.
    machines.save_machine({"id": "lab-2", "connection": {"kind": "test_addressed", "address": "http://10.0.0.5:5001"}})
    machines.save_machine({"id": "lab", "name": "Lab", "connection": {"kind": "test_addressed",
                                                                  "address": "http://10.0.0.5:5000"}})


def test_a_machine_saved_again_keeps_its_own_address(cfg_path, addressed):
    server = {"kind": "test_addressed", "address": "http://10.0.0.6:7000"}
    machines.save_machine({"id": "devbox", "connection": dict(server)})
    machines.save_machine({"id": "devbox", "name": "Dev box", "connection": dict(server)})
    with pytest.raises(MachineError, match="^Refused: machine 'devbox' already has the screen address "
                                           "10.0.0.6:7000"):
        machines.save_machine({"id": "devbox-2", "connection": dict(server)})


def test_loopback_spellings_are_one_address(cfg_path, addressed):
    machines.save_machine({"id": "vm", "connection": {"kind": "test_addressed", "address": "http://localhost:5000"}})
    with pytest.raises(MachineError, match="already has the screen address localhost:5000"):
        machines.save_machine({"id": "vm-2", "connection": {"kind": "test_addressed",
                                                        "address": "http://127.0.0.1:5000"}})




def test_a_kind_admits_its_connection_with_its_secret(cfg_path, addressed):
    machines.save_machine({"id": "pc", "connection": {"kind": "test_addressed", "address": "10.0.0.7",
                                                  "password": "hunter2"}})
    assert addressed[-1] == {"address": "10.0.0.7", "password": "hunter2"}
    # Sent back with the marker, the stored secret is what the kind checks.
    machines.save_machine({"id": "pc", "connection": {"kind": "test_addressed", "address": "10.0.0.7",
                                                  "password": dict(machines.SECRET_MARKER)}})
    assert addressed[-1]["password"] == "hunter2"
    with pytest.raises(MachineError, match=r"^Refused: this test screen is this computer\.$"):
        machines.save_machine({"id": "me", "connection": {"kind": "test_addressed", "address": "refuse-me"}})
    assert machines.get_machine("me") is None


def test_this_computer_is_built_in_and_never_stored(cfg_path):
    here_now = machines.local_machine()
    assert here_now.id == "local" and here_now.method == "local" and here_now.has_shell
    assert machines.list_machines()[0].is_local
    with pytest.raises(MachineError):
        machines.save_machine({"id": "local", "connection": {"kind": "ssh", "target": "me@devbox"}})
    assert "machines" not in json.loads(cfg_path.read_text())


def test_releasers_run_before_instances_are_removed(cfg_path, monkeypatch):
    # A switched-off machine of the kind: a conversation has instances only on a kind a machine uses.
    cfg_path.write_text(json.dumps({"machines": {"items": [
        {"id": "lab", "name": "Lab", "enabled": False, "connection": {"kind": "test_scoped"}}]}}), encoding="utf-8")
    order: list[tuple] = []
    monkeypatch.setattr(machines, "releasers", [lambda **who: order.append(("release", who))])
    from openprogram.host import backend
    monkeypatch.setitem(backend.SHELLS, "test_scoped", backend.Shell(
        lambda machine, session: None,
        remove_instances=lambda machine, conversation: order.append(("remove", machine, conversation))))
    machines.remove_instances(machine="lab")
    machines.remove_instances(conversation="c1")
    assert order == [("release", {"machine": "lab", "conversation": None}), ("remove", "lab", None),
                     ("release", {"machine": None, "conversation": "c1"}), ("remove", None, "c1")]


def test_a_failing_releaser_does_not_stop_the_removal(cfg_path, monkeypatch):
    removed: list = []

    def broken(**who):
        raise RuntimeError("boom")

    monkeypatch.setattr(machines, "releasers", [broken])
    from openprogram.host import backend
    monkeypatch.setitem(backend.SHELLS, "test_scoped", backend.Shell(
        lambda machine, session: None, remove_instances=lambda machine, conversation: removed.append(machine)))
    machines.remove_instances(machine="lab")
    assert removed == ["lab"]
