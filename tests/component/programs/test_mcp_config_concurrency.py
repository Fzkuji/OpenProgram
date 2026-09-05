"""Cross-process MCP config mutations use exact revisions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _wait_for(paths: list[Path]) -> None:
    deadline = time.monotonic() + (30 if os.name == "nt" else 5)
    while not all(path.exists() for path in paths):
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {paths}")
        time.sleep(0.01)


def _server(command: str, *, enabled: bool = True) -> dict:
    return {
        "type": "local",
        "command": [command],
        "env": {},
        "enabled": enabled,
        "timeout_seconds": 30.0,
        "always_load": False,
    }


@pytest.mark.parametrize(
    ("operation", "initial"),
    [
        ("add", {}),
        ("patch", {"first": _server("one"), "second": _server("two")}),
        (
            "delete",
            {
                "first": _server("one"),
                "second": _server("two"),
                "keep": _server("keep"),
            },
        ),
    ],
)
def test_two_process_mcp_routes_do_not_enforce_revision_conflicts(
    tmp_path: Path, operation: str, initial: dict
) -> None:
    home = tmp_path / "home"
    state = home / ".openprogram"
    state.mkdir(parents=True, mode=0o700)
    config_path = state / "mcp_servers.json"
    config_path.write_text(json.dumps({"servers": initial}), encoding="utf-8")
    config_path.chmod(0o600)
    release = tmp_path / "release"
    ready = [tmp_path / "ready-first", tmp_path / "ready-second"]
    env = {
        **os.environ,
        "HOME": os.fspath(home),
        "USERPROFILE": os.fspath(home),
    }
    script = """
import sys, time
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openprogram.webui.routes import mcp
operation, name = sys.argv[1:3]
ready, release = map(Path, sys.argv[3:5])
real_load = mcp.load_configs_with_revision
def paused_load(**kwargs):
    loaded = real_load(**kwargs)
    ready.write_text('ready')
    while not release.exists():
        time.sleep(0.01)
    return loaded
mcp.load_configs_with_revision = paused_load
async def runtime_ok(*args, **kwargs):
    return {'name': name, 'ready': True}
mcp.add_server = runtime_ok
mcp.restart_server = runtime_ok
mcp.remove_server = runtime_ok
app = FastAPI()
mcp.register(app)
client = TestClient(app)
if operation == 'add':
    response = client.post('/api/mcp/servers', json={
        'name': name, 'type': 'local', 'command': [name],
    })
elif operation == 'patch':
    response = client.patch(f'/api/mcp/servers/{name}', json={'enabled': False})
elif operation == 'delete':
    response = client.delete(f'/api/mcp/servers/{name}')
if response.status_code == 409:
    raise SystemExit(3)
if response.status_code not in (200, 201):
    raise SystemExit(4)
"""
    processes = []
    try:
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    operation,
                    name,
                    os.fspath(marker),
                    os.fspath(release),
                ],
                cwd=Path(__file__).parents[3],
                env=env,
                start_new_session=os.name != "nt",
            )
            for name, marker in zip(("first", "second"), ready, strict=True)
        ]
        _wait_for(ready)
        release.write_text("go", encoding="utf-8")
        wait_timeout = 30 if os.name == "nt" else 10
        codes = sorted(process.wait(timeout=wait_timeout) for process in processes)
    finally:
        release.touch(exist_ok=True)
        from openprogram._compat import kill_process_tree

        for process in processes:
            if process.poll() is None:
                kill_process_tree(process.pid)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

    assert codes == [0, 0]
    stored = json.loads(config_path.read_text(encoding="utf-8"))["servers"]
    if operation == "add":
        assert set(stored) in ({"first"}, {"second"})
    elif operation == "patch":
        assert sum(not stored[name]["enabled"] for name in ("first", "second")) == 1
    else:
        assert "keep" in stored
        assert len({"first", "second"} & stored.keys()) == 1


def test_mcp_revision_does_not_reject_external_exact_byte_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.mcp import config

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    path = root / "mcp_servers.json"
    path.write_text(json.dumps({"servers": {"one": _server("one")}}), encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr(config._paths, "get_state_dir", lambda: root)
    configs, revision = config.load_configs_with_revision(include_disabled=True)
    path.write_bytes(path.read_bytes() + b"\n")

    config.save_configs_revision(configs, expected_revision=revision)
    assert json.loads(path.read_text())["servers"]["one"]["command"] == ["one"]
