from __future__ import annotations

import json
import importlib
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch: pytest.MonkeyPatch):
    from openprogram.providers import api_registry
    from openprogram.providers import initialization
    from openprogram.providers import register

    monkeypatch.setattr(api_registry, "_registry", {})
    monkeypatch.setattr(api_registry, "_original_registry", {})
    monkeypatch.setattr(api_registry, "_provider_transform", None)
    monkeypatch.setattr(register, "_registered", False)
    importlib.reload(initialization)


def _run_python(source: str, *, home: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = os.getcwd()
    env.pop("_OPENPROGRAM_RECORDINGS_MANAGEMENT", None)
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_importing_provider_package_does_not_activate_invalid_replay(tmp_path: Path) -> None:
    state = tmp_path / ".openprogram"
    state.mkdir()
    (state / "config.json").write_text(
        json.dumps({"record_replay": {"mode": "replay", "file": "/missing/calls.jsonl"}}),
        encoding="utf-8",
    )
    (state / "config.json").chmod(0o600)

    result = _run_python(
        """
import json
import sys
import openprogram.providers
from openprogram.providers import api_registry
from openprogram.auth import credential_provider
print(json.dumps({
    "apis": sorted(api_registry._registry),
    "auth": sorted(credential_provider._provider_configs),
    "recording_loaded": "openprogram.providers.recording" in sys.modules,
}))
""",
        home=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "apis": [],
        "auth": [],
        "recording_loaded": False,
    }


def test_first_public_lookup_initializes_builtins_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.providers import api_registry
    from openprogram.providers import initialization

    calls = 0

    def register() -> None:
        nonlocal calls
        calls += 1
        api_registry.register_api_provider("test-api", object())

    monkeypatch.setattr(initialization, "_register_builtins", register)
    monkeypatch.setattr(initialization, "_activate_record_replay", lambda: None)

    first = api_registry.get_api_provider("test-api")
    second = api_registry.get_api_provider("test-api")

    assert first is second
    assert first is not None
    assert calls == 1


def test_concurrent_first_lookups_wait_for_one_initializer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.providers import api_registry
    from openprogram.providers import initialization

    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def register() -> None:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(5)
        api_registry.register_api_provider("test-api", object())

    monkeypatch.setattr(initialization, "_register_builtins", register)
    monkeypatch.setattr(initialization, "_activate_record_replay", lambda: None)
    results: list[object | None] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(api_registry.get_api_provider("test-api"))
        )
        for _ in range(4)
    ]

    for thread in threads:
        thread.start()
    assert entered.wait(5)
    time.sleep(0.05)
    assert results == []
    release.set()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()

    assert calls == 1
    assert len(results) == 4
    assert all(provider is results[0] for provider in results)


def test_record_replay_activation_failure_installs_blocked_provider_and_stays_ready(
    tmp_path: Path,
) -> None:
    """A corrupt record_replay config must not brick every provider call.

    The runtime transitions to READY with a fail-closed provider installed; the
    recordings management commands can then recover the config. Only builtin
    registration failure keeps the runtime FAILED.
    """
    state = tmp_path / ".openprogram"
    state.mkdir()
    config_path = state / "config.json"
    config_path.write_text(
        json.dumps(
            {"record_replay": {"mode": "replay", "file": str(tmp_path / "missing.jsonl")}}
        ),
        encoding="utf-8",
    )
    config_path.chmod(0o600)

    result = _run_python(
        """
import asyncio
import json
from openprogram.providers import api_registry, initialization
from openprogram.providers.types import (
    Context,
    Model,
    SimpleStreamOptions,
    UserMessage,
)
from openprogram.providers.stream import stream_simple

api_registry.register_api_provider("blocked-api", object())
snapshot = initialization.initialize_provider_runtime()
provider = api_registry.get_api_provider("blocked-api")

async def drain():
    model = Model(id="x", name="x", api="blocked-api", provider="blocked", base_url="https://n/a")
    ctx = Context(messages=[UserMessage(content="hi", timestamp=0)])
    try:
        async for _ in stream_simple(model, ctx, SimpleStreamOptions()):
            pass
    except RuntimeError as exc:
        return str(exc)
    return None

diagnostic = asyncio.run(drain())
print(json.dumps({
    "mode": snapshot.mode,
    "requires_credentials": provider.requires_credentials,
    "diagnostic": diagnostic,
}))
""",
        home=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "blocked"
    assert payload["requires_credentials"] is False
    assert "record/replay configuration" in payload["diagnostic"]


def test_recordings_off_recovers_after_activation_failure(tmp_path: Path) -> None:
    """`openprogram recordings off` stays reachable when replay activation fails."""
    state = tmp_path / ".openprogram"
    state.mkdir()
    config_path = state / "config.json"
    config_path.write_text(
        json.dumps(
            {"record_replay": {"mode": "replay", "file": str(tmp_path / "missing.jsonl")}}
        ),
        encoding="utf-8",
    )
    config_path.chmod(0o600)

    result = _run_python(
        """
import json
from openprogram.providers.recording import (
    activate_record_replay_safely,
    set_record_replay_off,
)
from openprogram.providers.api_registry import _provider_transform

mode = activate_record_replay_safely()
from openprogram.providers import api_registry
had_transform = api_registry._provider_transform is not None
set_record_replay_off()
print(json.dumps({"mode": mode, "had_transform": had_transform}))
""",
        home=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"mode": "blocked", "had_transform": True}


def test_failed_initialization_is_stable_and_never_exposes_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Builtin registration failure keeps the runtime FAILED across callers."""
    from openprogram.providers import api_registry
    from openprogram.providers import initialization

    provider = object()
    api_registry.register_api_provider("test-api", provider)
    calls = 0

    def fail() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("broken builtin registration")

    monkeypatch.setattr(initialization, "_register_builtins", fail)
    monkeypatch.setattr(initialization, "_activate_record_replay", lambda: "off")

    errors = []
    for _ in range(2):
        with pytest.raises(initialization.ProviderRuntimeInitializationError) as captured:
            api_registry.get_api_provider("test-api")
        errors.append(captured.value)

    assert calls == 1
    assert errors[0] is not errors[1]
    assert errors[0].stage == errors[1].stage == "builtins"
    assert errors[0].cause_type == errors[1].cause_type == "ValueError"
    assert str(errors[0]) == str(errors[1])


def test_failed_initialization_does_not_retain_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.providers import initialization, recording

    monkeypatch.setattr(
        initialization,
        "_register_builtins",
        lambda: (_ for _ in ()).throw(ValueError("SECRET-PROBE")),
    )
    monkeypatch.setattr(recording, "remove_secret_values", lambda _message: "[redacted]")

    with pytest.raises(initialization.ProviderRuntimeInitializationError) as captured:
        initialization.initialize_provider_runtime()

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "SECRET-PROBE" not in "".join(
        traceback.format_exception(captured.value)
    )


def test_failure_description_cannot_leave_runtime_initializing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.providers import initialization

    class BrokenMessageError(Exception):
        def __str__(self) -> str:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        initialization,
        "_register_builtins",
        lambda: (_ for _ in ()).throw(BrokenMessageError()),
    )

    with pytest.raises(KeyboardInterrupt):
        initialization.initialize_provider_runtime()

    completed = threading.Event()

    def retry() -> None:
        with pytest.raises(initialization.ProviderRuntimeInitializationError):
            initialization.initialize_provider_runtime()
        completed.set()

    thread = threading.Thread(target=retry)
    thread.start()
    thread.join(1)
    assert completed.is_set()


def test_worker_releases_lock_when_provider_initialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.providers import initialization
    from openprogram.worker import runner

    class FakeLock:
        holder_pid = None

        def __init__(self) -> None:
            self.released = False

        def try_acquire(self) -> bool:
            return True

        def release(self) -> None:
            self.released = True

    lock = FakeLock()
    monkeypatch.setattr(runner, "WorkerLock", lambda: lock)
    monkeypatch.setattr(
        initialization,
        "initialize_provider_runtime",
        lambda: (_ for _ in ()).throw(RuntimeError("invalid replay")),
    )

    with pytest.raises(RuntimeError, match="invalid replay"):
        runner.run_foreground()

    assert lock.released is True


def test_auth_adapter_registration_failure_rolls_back_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.auth import credential_provider
    from openprogram.providers import register

    monkeypatch.setattr(credential_provider, "_provider_configs", {})
    monkeypatch.setattr(credential_provider, "_PROVIDER_PLUGINS_LOADED", False)
    attempts = 0

    def register_adapters() -> None:
        nonlocal attempts
        attempts += 1
        credential_provider.register_provider_config(
            credential_provider.ProviderAuthConfig(provider_id="partial")
        )
        if attempts == 1:
            raise ImportError("broken auth adapter")
        credential_provider.register_provider_config(
            credential_provider.ProviderAuthConfig(provider_id="target")
        )

    monkeypatch.setattr(register, "register_auth_adapters", register_adapters)

    with pytest.raises(ImportError, match="broken auth adapter"):
        credential_provider.get_provider_config("target")
    assert credential_provider._provider_configs == {}

    assert credential_provider.get_provider_config("target").provider_id == "target"
    assert attempts == 2


def test_concurrent_builtin_and_auth_registration_do_not_deadlock(
    tmp_path: Path,
) -> None:
    result = _run_python(
        """
import json
import threading
import time
from openprogram.auth import credential_provider
from openprogram.providers import register

credential_provider._provider_configs.clear()
credential_provider._PROVIDER_PLUGINS_LOADED = False
register._registered = False
entered = threading.Event()
release = threading.Event()

def load_builtins():
    entered.set()
    release.wait(2)
    return {}

def register_auth():
    with register._lock:
        credential_provider.register_provider_config(
            credential_provider.ProviderAuthConfig(provider_id="target")
        )

register._load_builtin_providers = load_builtins
register.register_auth_adapters = register_auth
builtins = threading.Thread(target=register.register_builtins, daemon=True)
auth = threading.Thread(
    target=lambda: credential_provider.get_provider_config("target"), daemon=True
)
builtins.start()
assert entered.wait(2)
auth.start()
time.sleep(0.05)
release.set()
builtins.join(1)
auth.join(1)
print(json.dumps({"builtins": builtins.is_alive(), "auth": auth.is_alive()}))
""",
        home=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"builtins": False, "auth": False}


def test_builtin_registration_can_retry_after_loading_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.providers import api_registry
    from openprogram.providers import register

    importlib.reload(register)
    monkeypatch.setattr(api_registry, "_registry", {})
    monkeypatch.setattr(api_registry, "_original_registry", {})
    monkeypatch.setattr(register, "_registered", False)
    provider = object()
    attempts = 0

    def load():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ImportError("provider module is broken")
        return {"test-api": provider}

    monkeypatch.setattr(register, "_load_builtin_providers", load)
    monkeypatch.setattr(register, "register_auth_adapters", lambda: None)

    with pytest.raises(ImportError, match="provider module is broken"):
        register.register_builtins()
    register.register_builtins()

    assert attempts == 2
    assert api_registry._registry == {"test-api": provider}


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("anthropic", "claude-sonnet-4-6", "anthropic:claude-sonnet-4-6"),
        ("claude-code", "claude-sonnet-4", "anthropic:claude-sonnet-4"),
        ("openai-codex", "gpt-5.5", "openai-codex:gpt-5.5"),
        ("gemini-cli", "gemini-2.5-flash", "gemini-subscription:gemini-2.5-flash"),
    ],
)
def test_replay_runtime_factory_never_constructs_credentials_runtime(
    provider: str,
    model: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.agentic_programming.runtime import Runtime
    from openprogram.providers import initialization, registry
    from openprogram.providers import models as provider_models
    from openprogram.providers.types import Model

    namespace, model_id = expected.split(":", 1)
    api = {
        "anthropic": "anthropic-messages",
        "openai-codex": "openai-codex",
        "gemini-subscription": "gemini-subscription",
    }[namespace]
    wire_model = Model(
        id=model_id,
        name=model_id,
        api=api,
        provider=namespace,
        base_url="https://offline.invalid",
    )
    monkeypatch.setattr(
        provider_models,
        "get_model",
        lambda candidate_provider, candidate_model: (
            wire_model
            if (candidate_provider, candidate_model) == (namespace, model_id)
            else None
        ),
    )

    monkeypatch.setattr(
        initialization,
        "initialize_provider_runtime",
        lambda: initialization.ProviderRuntimeSnapshot(mode="replay"),
    )
    monkeypatch.setattr(
        registry,
        "_http_api_key_for",
        lambda entry: (_ for _ in ()).throw(AssertionError("credential lookup")),
    )
    real_import = importlib.import_module

    def no_runtime_import(name: str, package: str | None = None):
        if name.endswith(".runtime") or "direct_runtime" in name:
            raise AssertionError("subscription runtime import")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", no_runtime_import)

    runtime = registry.create_runtime(provider=provider, model=model)

    assert type(runtime) is Runtime
    assert runtime.model == expected


def test_web_start_initializes_before_background_threads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from openprogram.webui import server
    from openprogram.webui import owner_auth
    from openprogram.providers import initialization

    order: list[str] = []

    class NoopAuth:
        bind_host = "127.0.0.1"
        effective_origins = ()
        fingerprint = "sha256:test"

        def close(self) -> None:
            pass

    class NoopThread:
        def __init__(self, *args, **kwargs) -> None:
            order.append("thread")

        def start(self) -> None:
            pass

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(server, "_server_thread", None)
    monkeypatch.setattr(server, "_owner_auth_state", None)
    monkeypatch.setattr(owner_auth.OwnerAuthState, "start", lambda **kwargs: NoopAuth())
    monkeypatch.setattr(server.threading, "Thread", NoopThread)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        initialization,
        "initialize_provider_runtime",
        lambda: order.append("initialize"),
    )

    server.start_server(port=18199, open_browser=False)

    assert order[0] == "initialize"


def test_replay_claude_code_uses_enabled_model_without_anthropic_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.agentic_programming.runtime import Runtime
    from openprogram.providers import enabled_models, initialization, registry
    from openprogram.providers.types import Model

    model = Model(
        id="claude-sonnet-4-6",
        name="Claude Sonnet 4.6",
        api="anthropic-messages",
        provider="claude-code",
        base_url="https://api.anthropic.com",
    )
    monkeypatch.setattr(
        enabled_models,
        "ENABLED_MODELS",
        {"claude-code/claude-sonnet-4-6": model},
    )
    monkeypatch.setattr("openprogram.providers.models.ENABLED_MODELS", enabled_models.ENABLED_MODELS)
    monkeypatch.setattr(
        initialization,
        "initialize_provider_runtime",
        lambda: initialization.ProviderRuntimeSnapshot(mode="replay"),
    )

    runtime = registry.create_runtime(
        provider="claude-code", model="claude-sonnet-4-6"
    )

    assert type(runtime) is Runtime
    assert runtime.model == "anthropic:claude-sonnet-4-6"
    assert runtime.provider_id == "claude-code"
    assert runtime.api_model.provider == "anthropic"
    assert enabled_models.ENABLED_MODELS == {
        "claude-code/claude-sonnet-4-6": model
    }


def test_replay_factory_does_not_enable_disabled_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.providers import enabled_models, initialization, registry

    monkeypatch.setattr(enabled_models, "ENABLED_MODELS", {})
    monkeypatch.setattr("openprogram.providers.models.ENABLED_MODELS", enabled_models.ENABLED_MODELS)
    monkeypatch.setattr(
        initialization,
        "initialize_provider_runtime",
        lambda: initialization.ProviderRuntimeSnapshot(mode="replay"),
    )
    monkeypatch.setattr(
        "openprogram.providers.enabled_models.register_model_from_config",
        lambda provider, model: enabled_models.ENABLED_MODELS.update(
            {f"{provider}/{model}": object()}
        ) or True,
    )

    with pytest.raises(ValueError, match="Unknown model"):
        registry.create_runtime(provider="anthropic", model="disabled-model")

    assert enabled_models.ENABLED_MODELS == {}


def test_replay_factory_does_not_register_disabled_community_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.providers import enabled_models, initialization, registry

    monkeypatch.setattr(enabled_models, "ENABLED_MODELS", {})
    monkeypatch.setattr("openprogram.providers.models.ENABLED_MODELS", enabled_models.ENABLED_MODELS)
    monkeypatch.setattr(
        initialization,
        "initialize_provider_runtime",
        lambda: initialization.ProviderRuntimeSnapshot(mode="replay"),
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        enabled_models,
        "register_model_from_config",
        lambda provider, model: calls.append((provider, model)) or False,
    )

    with pytest.raises(ValueError, match="Unknown model"):
        registry.create_runtime(
            provider="community-provider", model="disabled-model"
        )

    assert enabled_models.ENABLED_MODELS == {}
    assert calls == []
