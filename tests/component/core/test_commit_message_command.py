from __future__ import annotations

import subprocess

import pytest
from rich.console import Console

from openprogram._cli_chat import handlers
from openprogram.commands import registry
from openprogram.commands.dispatch import invoke


@pytest.fixture()
def isolated_commands(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        registry,
        "_buckets",
        {source: {} for source in registry.SOURCE_ORDER},
    )
    monkeypatch.setattr(
        registry,
        "_aliases",
        {source: {} for source in registry.SOURCE_ORDER},
    )
    monkeypatch.setattr(registry, "_loaded", False)
    monkeypatch.setattr(registry, "load_user", lambda: [])
    monkeypatch.setattr(registry, "load_project", lambda cwd=None: [])
    import openprogram.commands._plugin_adapter as plugin_adapter
    import openprogram.commands._skill_adapter as skill_adapter

    monkeypatch.setattr(plugin_adapter, "sync_into_registry", lambda: None)
    monkeypatch.setattr(skill_adapter, "sync_into_registry", lambda: None)
    registry.reload()
    yield


def test_commit_message_is_a_local_command(isolated_commands):
    result = invoke("/commit-message")

    assert result.ok is True
    assert result.kind == "local"
    assert result.command_name == "commit-message"
    assert callable(result.local_handler)


def test_commit_message_reads_diff_without_changing_repository(
    isolated_commands,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    target = repo / "settings.toml"
    target.write_text("enabled = true\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "settings.toml"], check=True)
    before = subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    seen: dict[str, str] = {}

    def fake_llm(system: str, user: str) -> str:
        seen["system"] = system
        seen["user"] = user
        return "Add project settings"

    monkeypatch.setattr(
        "openprogram.providers.default_llm.build_default_llm",
        lambda: fake_llm,
    )
    import openprogram.commands.commit_message as commit_message

    real_run = commit_message.subprocess.run
    git_envs: list[dict[str, str] | None] = []

    def recording_run(*args, **kwargs):
        if (
            args
            and isinstance(args[0], list)
            and "core.fsmonitor=false" in args[0]
        ):
            git_envs.append(kwargs.get("env"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(commit_message.subprocess, "run", recording_run)

    result = invoke("/commit-message")
    output = result.local_handler({"session_id": "", "cwd": str(repo)}, "")
    after = subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert output == {"text": "Add project settings"}
    assert "+enabled = true" in seen["user"]
    assert "Do not modify" in seen["system"]
    assert git_envs and all(env and env.get("GIT_OPTIONAL_LOCKS") == "0" for env in git_envs)
    assert after == before

    monkeypatch.chdir(repo)
    console = Console(record=True, width=120, force_terminal=False)
    assert handlers._handle_slash(
        "/commit-message", console, None, session_id="cli-session",
    ) is False
    assert "Add project settings" in console.export_text()


def test_shared_registration_keeps_host_builtin(isolated_commands):
    registry.register_builtin("commit-message", handler="host-owned")

    registry.register_shared_builtins()

    assert registry.get("commit-message").builtin_handler == "host-owned"


def test_commit_message_bounds_combined_status_and_diff_context(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    import openprogram.commands.commit_message as commit_message

    seen: dict[str, str] = {}

    def fake_git(_cwd, *args: str) -> str:
        if args[0] == "status":
            return "".join(f"?? generated/file-{i:05}.txt\n" for i in range(10_000))
        return "diff --git a/a b/a\n" + ("+changed\n" * 20_000)

    def fake_llm(system: str, user: str) -> str:
        seen["system"] = system
        seen["user"] = user
        return "Bound generated commit context"

    monkeypatch.setattr(commit_message, "_git", fake_git)
    monkeypatch.setattr(
        "openprogram.providers.default_llm.build_default_llm",
        lambda: fake_llm,
    )

    result = commit_message.commit_message_builtin_handler(
        {"cwd": str(tmp_path)}, "",
    )

    assert result == {"text": "Bound generated commit context"}
    assert len(seen["user"]) <= 80_000
    assert "[status truncated]" in seen["user"]
    assert "Diff:\n" in seen["user"]
    assert seen["user"].endswith("[change context truncated]")
