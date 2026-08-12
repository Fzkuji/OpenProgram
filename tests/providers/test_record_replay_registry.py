from __future__ import annotations

import multiprocessing
import asyncio
import importlib
import logging
from pathlib import Path

import pytest

from openprogram.providers import api_registry
from openprogram.providers.api_registry import (
    _register_builtin_api_providers,
    _replace_provider_transform,
    configure_provider_transform,
    get_api_provider,
    register_api_provider,
    register_api_providers,
)
from openprogram.providers.recording import RecordingSink
from openprogram.providers.recording import RecordingProvider, activate_record_replay_from_config
from openprogram.providers.replay import ReplayProvider
from openprogram.providers.replay import read_recording_file
from openprogram.providers.types import Context, Model, SimpleStreamOptions, UserMessage


class Provider:
    def __init__(self, name: str) -> None:
        self.name = name


class WrappedProvider:
    def __init__(self, api: str, inner: Provider) -> None:
        self.api = api
        self.inner = inner


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api_registry, "_registry", {})
    monkeypatch.setattr(api_registry, "_original_registry", {})
    monkeypatch.setattr(api_registry, "_provider_transform", None)
    monkeypatch.setattr(api_registry, "_audited_accounting", {})
    monkeypatch.setattr(api_registry, "_audited_originals", {})


def test_shared_replay_transform_preserves_each_audited_api_identity() -> None:
    _register_builtin_api_providers({
        "first-api": Provider("first"),
        "second-api": Provider("second"),
    })
    replay = Provider("shared-replay")

    configure_provider_transform(lambda _api, _provider: replay)

    assert get_api_provider("first-api") is replay
    assert get_api_provider("second-api") is replay
    assert api_registry.has_audited_accounting(replay, "first-api")
    assert api_registry.has_audited_accounting(replay, "second-api")


@pytest.mark.parametrize("batch", [False, True])
def test_public_override_revokes_original_and_transformed_accounting_identity(
    batch: bool,
) -> None:
    audited = Provider("audited")
    _register_builtin_api_providers({"first-api": audited})
    old_wrapper = WrappedProvider("first-api", audited)
    configure_provider_transform(lambda _api, _provider: old_wrapper)
    custom = Provider("custom")

    if batch:
        register_api_providers({"first-api": custom})
    else:
        register_api_provider("first-api", custom)

    current = get_api_provider("first-api")
    assert not api_registry.has_audited_accounting(audited, "first-api")
    assert not api_registry.has_audited_accounting(old_wrapper, "first-api")
    assert not api_registry.has_audited_accounting(current, "first-api")


def test_replacing_transform_revokes_old_wrapper_identity() -> None:
    audited = Provider("audited")
    _register_builtin_api_providers({"first-api": audited})
    old_wrapper = WrappedProvider("old", audited)
    new_wrapper = WrappedProvider("new", audited)
    configure_provider_transform(lambda _api, _provider: old_wrapper)
    assert api_registry.has_audited_accounting(old_wrapper, "first-api")

    _replace_provider_transform(lambda _api, _provider: new_wrapper)

    assert not api_registry.has_audited_accounting(old_wrapper, "first-api")
    assert api_registry.has_audited_accounting(new_wrapper, "first-api")


def test_transform_wraps_existing_and_future_registry_entries() -> None:
    first = Provider("first")
    second = Provider("second")
    transform = lambda api, provider: WrappedProvider(api, provider)
    register_api_provider("first-api", first)

    configure_provider_transform(transform)
    register_api_provider("second-api", second)

    assert get_api_provider("first-api").inner is first
    assert get_api_provider("second-api").inner is second
    assert get_api_provider("first-api").api == "first-api"


def test_transform_is_idempotent_only_for_the_same_callable() -> None:
    transform = lambda api, provider: WrappedProvider(api, provider)
    configure_provider_transform(transform)
    configure_provider_transform(transform)

    with pytest.raises(RuntimeError, match="already configured"):
        configure_provider_transform(lambda api, provider: WrappedProvider(api, provider))


def test_transform_failure_does_not_partially_replace_registry() -> None:
    first = Provider("first")
    second = Provider("second")
    register_api_provider("first-api", first)
    register_api_provider("second-api", second)

    def fail_on_second(api, provider):
        if api == "second-api":
            raise ValueError("cannot wrap")
        return WrappedProvider(api, provider)

    with pytest.raises(ValueError, match="cannot wrap"):
        configure_provider_transform(fail_on_second)

    assert get_api_provider("first-api") is first
    assert get_api_provider("second-api") is second


def _write_calls(path: str, count: int) -> None:
    sink = RecordingSink(path)
    for marker in range(count):
        call_index = sink.begin_call({
            "model": {"id": f"model-{marker}"},
            "context": {"messages": []},
            "options": None,
        })
        sink.end_call(call_index, 0)


def test_multiprocess_sink_allocates_contiguous_parseable_calls(tmp_path: Path) -> None:
    recording = tmp_path / "concurrent.jsonl"
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_write_calls, args=(str(recording), 5)) for _ in range(3)]

    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0

    calls = read_recording_file(recording)
    assert [call.call_index for call in calls] == list(range(15))


def test_record_activation_uses_one_sink_for_every_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = Provider("first")
    second = Provider("second")
    register_api_provider("first-api", first)
    register_api_provider("second-api", second)
    monkeypatch.setattr("openprogram.paths.get_recordings_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"record_replay": {"mode": "record", "file": "calls"}},
    )

    activate_record_replay_from_config()

    wrapped_first = get_api_provider("first-api")
    wrapped_second = get_api_provider("second-api")
    assert isinstance(wrapped_first, RecordingProvider)
    assert isinstance(wrapped_second, RecordingProvider)
    assert wrapped_first._sink is wrapped_second._sink


def test_replay_activation_uses_one_provider_for_every_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "calls.jsonl"
    RecordingSink(recording)
    register_api_provider("first-api", Provider("first"))
    register_api_provider("second-api", Provider("second"))
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"record_replay": {"mode": "replay", "file": str(recording)}},
    )

    activate_record_replay_from_config()

    replay = get_api_provider("first-api")
    assert isinstance(replay, ReplayProvider)
    assert get_api_provider("second-api") is replay


def test_invalid_startup_config_keeps_import_alive_and_blocks_live_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from openprogram.providers import recording as recording_module

    api = "activation-failure-api"
    register_api_provider(api, Provider("live"))
    missing = tmp_path / "missing.jsonl"
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"record_replay": {"mode": "replay", "file": str(missing)}},
    )
    caplog.set_level(logging.ERROR, logger="openprogram.providers")

    recording_module.activate_record_replay_safely()

    blocked = get_api_provider(api)
    assert blocked is not None
    assert blocked.requires_credentials is False
    stream_module = importlib.import_module("openprogram.providers.stream")
    monkeypatch.setattr(
        stream_module,
        "resolve_provider_key",
        lambda provider: pytest.fail("invalid replay config resolved credentials"),
    )
    model = Model(
        id="blocked",
        name="Blocked",
        api=api,
        provider="blocked",
        base_url="https://network.invalid",
    )

    async def drain() -> None:
        async for _ in stream_module.stream_simple(
            model,
            Context(messages=[UserMessage(content="hello", timestamp=0)]),
            SimpleStreamOptions(),
        ):
            pass

    with pytest.raises(RuntimeError, match="record/replay configuration"):
        asyncio.run(drain())
    assert "provider calls are blocked" in caplog.text


def test_invalid_startup_config_replaces_an_existing_transform_with_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transform = lambda api, provider: WrappedProvider(api, provider)
    configure_provider_transform(transform)
    register_api_provider("existing-api", Provider("existing"))
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {
            "record_replay": {
                "mode": "replay",
                "file": str(tmp_path / "missing.jsonl"),
            }
        },
    )

    from openprogram.providers.recording import activate_record_replay_safely

    activate_record_replay_safely()
    register_api_provider("future-api", Provider("future"))

    async def drain(provider) -> None:
        async for _ in provider.stream(None, None):
            pass

    for api in ("existing-api", "future-api"):
        provider = get_api_provider(api)
        assert provider.requires_credentials is False
        with pytest.raises(RuntimeError, match="record/replay configuration"):
            asyncio.run(drain(provider))
