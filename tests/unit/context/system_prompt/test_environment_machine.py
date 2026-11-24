"""The <environment> block names the machine a conversation's shell and files are on.

When that machine is not this computer, one sentence says that the chat
and OpenProgram still run here, and, with computer_use offered, that
a target without a machine acts on this computer.
"""
from __future__ import annotations

import types

import pytest

from openprogram.host import machines
from openprogram.host.machines import Connection, Machine
from openprogram.runtime.context.prompt_assembly import components


def _block(monkeypatch, tmp_path, machine, tools=None):
    monkeypatch.setenv("OPENPROGRAM_WORKDIR", str(tmp_path))
    monkeypatch.setattr(machines, "current_environment", lambda: machine)
    return components.environment_block(tools)


def test_this_computer_gets_no_machine_line(monkeypatch, tmp_path):
    block = _block(monkeypatch, tmp_path, machines.local_machine({}))
    assert "Environment:" not in block and "not on" not in block


def test_a_remote_shell_with_remote_files(monkeypatch, tmp_path):
    devbox = Machine("devbox", "Dev box", shell=Connection("ssh", {"target": "me@devbox"}))
    block = _block(monkeypatch, tmp_path, devbox)
    assert ("Environment: machine devbox (Dev box; SSH host me@devbox). This conversation's shell and file "
            "tools act on that machine, and paths are its paths.") in block
    assert "The chat itself and OpenProgram run on this computer, not on devbox." in block
    assert "computer_use" not in block
    assert "Shell:" not in block


def test_a_shell_that_mounts_the_working_directories(monkeypatch, tmp_path):
    box = Machine("builder", "Builder", shell=Connection("docker", {"image": "me/desktop:24.04"}))
    block = _block(monkeypatch, tmp_path, box)
    assert ("Environment: machine builder (Builder; Docker container me/desktop:24.04). This "
            "conversation's shell commands run there; its working directories are mounted there at the same "
            "paths.") in block


@pytest.mark.parametrize("tool", ["computer_use", types.SimpleNamespace(name="computer_use")])
def test_with_computer_use_a_call_without_device_is_this_computer(monkeypatch, tmp_path, tool):
    devbox = Machine("devbox", "Dev box", shell=Connection("ssh", {"target": "me@devbox"}))
    block = _block(monkeypatch, tmp_path, devbox, tools=[tool])
    # Named outright: "it" would read as devbox, the nearest noun.
    assert ("The chat itself and OpenProgram run on this computer, not on devbox; computer_use calls "
            "without device act on this computer.") in block
    # The browser line of the tool's description says where its localhost is, only when there is a browser.
    assert "localhost" not in block
