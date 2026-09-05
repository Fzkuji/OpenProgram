"""``setup.update_config`` — atomic read-modify-write of config.json.

The race it guards: concurrent writers doing ``_read_config()`` … ``_write_config()``
separately clobber each other (last write wins). ``update_config`` serialises
the whole critical section, so concurrent mutators all land.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from openprogram import setup
from openprogram.config_schema import set_setting


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.01)


def test_update_config_concurrent_mutators_all_land(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    monkeypatch.setattr(setup, "get_config_path", lambda: cfgp)
    setup._write_config({"a": {}})

    def make(key):
        def mutate(cfg):
            sub = dict(cfg.get("a", {}))   # read
            time.sleep(0.02)               # widen the window a bare r/m/w would lose
            sub[key] = key
            cfg["a"] = sub                 # write
        return mutate

    threads = [threading.Thread(target=setup.update_config, args=(make(k),))
               for k in ("x", "y", "z")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Without serialisation each thread reads {} / a stale dict and the last
    # write wins, leaving a single key. The lock means all three land.
    assert setup._read_config()["a"] == {"x": "x", "y": "y", "z": "z"}


def test_update_config_returns_and_mutates_in_place(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    monkeypatch.setattr(setup, "get_config_path", lambda: cfgp)
    setup._write_config({})
    out = setup.update_config(lambda cfg: cfg.setdefault("ui", {}).update({"port": 18109}))
    assert out["ui"]["port"] == 18109
    assert setup._read_config()["ui"]["port"] == 18109


def test_update_config_writes_file(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    monkeypatch.setattr(setup, "get_config_path", lambda: cfgp)
    setup.update_config(lambda cfg: cfg.update({"k": "v"}))
    assert json.loads(cfgp.read_text()) == {"k": "v"}


def test_provider_update_waits_to_read_and_preserves_concurrent_api_key(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    state = home / ".openprogram"
    state.mkdir(parents=True, mode=0o700)
    config = state / "config.json"
    config.write_text("{}\n", encoding="utf-8")
    config.chmod(0o600)
    ready = tmp_path / "ready"
    provider_read = tmp_path / "provider-read"
    provider_update = tmp_path / "provider-update"
    release = tmp_path / "release"
    env = {
        **os.environ,
        "HOME": os.fspath(home),
        "USERPROFILE": os.fspath(home),
    }
    api_key_writer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import sys, time
from pathlib import Path
from openprogram import setup
ready, release = map(Path, sys.argv[1:])
def update(config):
    ready.write_text('ready')
    while not release.exists():
        time.sleep(0.01)
    config.setdefault('api_keys', {})['KEEP'] = 'secret'
setup.update_config(update)
""",
            os.fspath(ready),
            os.fspath(release),
        ],
        cwd=Path(__file__).parents[3],
        env=env,
    )
    _wait_for(ready)
    provider_writer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import sys, time
from pathlib import Path
from openprogram import setup
from openprogram.providers.storage import save_default_model
read_started, update_started, release = map(Path, sys.argv[1:])
real_read = setup._read_config
real_update = setup.update_config
def observed_read():
    value = real_read()
    read_started.write_text('read')
    while not release.exists():
        time.sleep(0.01)
    return value
def observed_update(mutator, **kwargs):
    update_started.write_text('update')
    return real_update(mutator, **kwargs)
setup._read_config = observed_read
setup.update_config = observed_update
save_default_model('openai', 'gpt-4.1')
""",
            os.fspath(provider_read),
            os.fspath(provider_update),
            os.fspath(release),
        ],
        cwd=Path(__file__).parents[3],
        env=env,
    )
    deadline = time.monotonic() + 5
    while not provider_read.exists() and not provider_update.exists():
        if time.monotonic() >= deadline:
            raise AssertionError("provider writer did not begin config mutation")
        time.sleep(0.01)
    release.write_text("go", encoding="utf-8")

    assert api_key_writer.wait(timeout=10) == 0
    assert provider_writer.wait(timeout=10) == 0
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "api_keys": {"KEEP": "secret"},
        "default_provider": "openai",
        "default_model": "gpt-4.1",
    }


def test_interactive_section_does_not_enforce_revision_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.cli.setup_sections import sections
    path = tmp_path / "config.json"
    monkeypatch.setattr(setup, "get_config_path", lambda: path)
    setup._write_config({"memory": {"backend": "local"}})

    def edit_during_prompt(*_args):
        path.write_text('{"api_keys":{"KEEP":"external"}}\n', encoding="utf-8")
        path.chmod(0o600)
        return "none"

    monkeypatch.setattr(setup, "_choose_one", edit_during_prompt)

    sections.run_memory_section()
    assert json.loads(path.read_text(encoding="utf-8"))["memory"] == {
        "backend": "none"
    }


def test_two_process_custom_provider_creates_preserve_both_providers(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    state = home / ".openprogram"
    state.mkdir(parents=True, mode=0o700)
    config = state / "config.json"
    config.write_text('{"api_keys":{"KEEP":"secret"}}\n', encoding="utf-8")
    config.chmod(0o600)
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
from openprogram import setup
from openprogram.providers.storage import create_custom_provider
name = sys.argv[1]
ready, release = map(Path, sys.argv[2:])
real_update = setup.update_config
def paused_update(mutator, **kwargs):
    ready.write_text('ready')
    while not release.exists():
        time.sleep(0.01)
    return real_update(mutator, **kwargs)
setup.update_config = paused_update
result = create_custom_provider(name, name, f'https://{name}.example/v1')
if not result.get('ok'):
    raise SystemExit(4)
"""
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                name,
                os.fspath(marker),
                os.fspath(release),
            ],
            cwd=Path(__file__).parents[3],
            env=env,
        )
        for name, marker in zip(("first", "second"), ready, strict=True)
    ]
    for marker in ready:
        _wait_for(marker)
    release.write_text("go", encoding="utf-8")

    assert [process.wait(timeout=10) for process in processes] == [0, 0]
    stored = json.loads(config.read_text(encoding="utf-8"))
    assert set(stored["providers"]) == {"first", "second"}
    assert stored["api_keys"] == {"KEEP": "secret"}


def test_outbound_url_security_update_preserves_mode_and_unrelated_config(
    tmp_path, monkeypatch
):
    cfgp = tmp_path / "config.json"
    monkeypatch.setattr(setup, "get_config_path", lambda: cfgp)
    setup._write_config({"api_keys": {"OPENAI_API_KEY": "stored-secret"}})

    result = set_setting(
        "security.outbound_url",
        {
            "exceptions": [
                {
                    "consumer": "provider.configured_api",
                    "cidr": "10.20.7.9/16",
                }
            ]
        },
    )

    assert "error" not in result
    assert setup._read_config() == {
        "api_keys": {"OPENAI_API_KEY": "stored-secret"},
        "security": {
            "outbound_url": {
                "exceptions": [
                    {
                        "consumer": "provider.configured_api",
                        "cidr": "10.20.0.0/16",
                    }
                ]
            }
        },
    }
    if os.name != "nt":
        assert (cfgp.stat().st_mode & 0o777) == 0o600


def test_provider_update_waits_to_read_and_preserves_concurrent_api_key(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    state = home / ".openprogram"
    state.mkdir(parents=True, mode=0o700)
    config = state / "config.json"
    config.write_text("{}\n", encoding="utf-8")
    config.chmod(0o600)
    ready = tmp_path / "ready"
    provider_read = tmp_path / "provider-read"
    provider_update = tmp_path / "provider-update"
    release = tmp_path / "release"
    env = {
        **os.environ,
        "HOME": os.fspath(home),
        "USERPROFILE": os.fspath(home),
    }
    api_key_writer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import sys, time
from pathlib import Path
from openprogram import setup
ready, release = map(Path, sys.argv[1:])
def update(config):
    ready.write_text('ready')
    while not release.exists():
        time.sleep(0.01)
    config.setdefault('api_keys', {})['KEEP'] = 'secret'
setup.update_config(update)
""",
            os.fspath(ready),
            os.fspath(release),
        ],
        cwd=Path(__file__).parents[3],
        env=env,
    )
    _wait_for(ready)
    provider_writer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import sys, time
from pathlib import Path
from openprogram import setup
from openprogram.providers.storage import save_default_model
read_started, update_started, release = map(Path, sys.argv[1:])
real_read = setup._read_config
real_update = setup.update_config
def observed_read():
    value = real_read()
    read_started.write_text('read')
    while not release.exists():
        time.sleep(0.01)
    return value
def observed_update(mutator, **kwargs):
    update_started.write_text('update')
    return real_update(mutator, **kwargs)
setup._read_config = observed_read
setup.update_config = observed_update
save_default_model('openai', 'gpt-4.1')
""",
            os.fspath(provider_read),
            os.fspath(provider_update),
            os.fspath(release),
        ],
        cwd=Path(__file__).parents[3],
        env=env,
    )
    deadline = time.monotonic() + 5
    while not provider_read.exists() and not provider_update.exists():
        if time.monotonic() >= deadline:
            raise AssertionError("provider writer did not begin config mutation")
        time.sleep(0.01)
    release.write_text("go", encoding="utf-8")

    assert api_key_writer.wait(timeout=10) == 0
    assert provider_writer.wait(timeout=10) == 0
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "api_keys": {"KEEP": "secret"},
        "default_provider": "openai",
        "default_model": "gpt-4.1",
    }


def test_two_process_custom_provider_creates_preserve_both_providers(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    state = home / ".openprogram"
    state.mkdir(parents=True, mode=0o700)
    config = state / "config.json"
    config.write_text('{"api_keys":{"KEEP":"secret"}}\n', encoding="utf-8")
    config.chmod(0o600)
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
from openprogram import setup
from openprogram.providers.storage import create_custom_provider
name = sys.argv[1]
ready, release = map(Path, sys.argv[2:])
real_update = setup.update_config
def paused_update(mutator, **kwargs):
    ready.write_text('ready')
    while not release.exists():
        time.sleep(0.01)
    return real_update(mutator, **kwargs)
setup.update_config = paused_update
result = create_custom_provider(name, name, f'https://{name}.example/v1')
if not result.get('ok'):
    raise SystemExit(4)
"""
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                name,
                os.fspath(marker),
                os.fspath(release),
            ],
            cwd=Path(__file__).parents[3],
            env=env,
        )
        for name, marker in zip(("first", "second"), ready, strict=True)
    ]
    for marker in ready:
        _wait_for(marker)
    release.write_text("go", encoding="utf-8")

    assert [process.wait(timeout=10) for process in processes] == [0, 0]
    stored = json.loads(config.read_text(encoding="utf-8"))
    assert set(stored["providers"]) == {"first", "second"}
    assert stored["api_keys"] == {"KEEP": "secret"}
