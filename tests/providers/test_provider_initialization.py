from __future__ import annotations

import json
import importlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch: pytest.MonkeyPatch):
    from openprogram.providers import api_registry
    from openprogram.providers import initialization

    monkeypatch.setattr(api_registry, "_registry", {})
    monkeypatch.setattr(api_registry, "_original_registry", {})
    monkeypatch.setattr(api_registry, "_provider_transform", None)
    importlib.reload(initialization)


def _run_python(source: str, *, home: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
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


def test_failed_initialization_is_stable_and_never_exposes_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.providers import api_registry
    from openprogram.providers import initialization

    provider = object()
    api_registry.register_api_provider("test-api", provider)
    calls = 0

    def fail() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("invalid replay file")

    monkeypatch.setattr(initialization, "_register_builtins", lambda: None)
    monkeypatch.setattr(initialization, "_activate_record_replay", fail)

    errors = []
    for _ in range(2):
        with pytest.raises(initialization.ProviderRuntimeInitializationError) as captured:
            api_registry.get_api_provider("test-api")
        errors.append(captured.value)

    assert calls == 1
    assert errors[0] is not errors[1]
    assert errors[0].stage == errors[1].stage == "record_replay"
    assert errors[0].cause_type == errors[1].cause_type == "ValueError"
    assert str(errors[0]) == str(errors[1])


def test_builtin_registration_can_retry_after_loading_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.providers import api_registry
    from openprogram.providers import register

    importlib.reload(register)
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
