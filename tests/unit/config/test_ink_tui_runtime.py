from __future__ import annotations

import sys
from pathlib import Path


def test_windows_tui_invocation_is_not_disabled_by_platform(
    monkeypatch,
) -> None:
    from openprogram.cli import _looks_like_tui_invocation

    monkeypatch.setattr(sys, "platform", "win32")
    assert _looks_like_tui_invocation([])
    assert _looks_like_tui_invocation(["tui"])
    assert not _looks_like_tui_invocation(["--print", "hello"])
    assert not _looks_like_tui_invocation(["web"])


def test_tui_detection_does_not_treat_profile_values_as_subcommands() -> None:
    from openprogram.cli import _looks_like_tui_invocation

    assert _looks_like_tui_invocation(["--profile", "web", "tui"])
    assert _looks_like_tui_invocation(["--profile=doctor", "tui"])
    assert _looks_like_tui_invocation(["--resume", "web"])


def test_tui_startup_log_uses_cli_profile_from_argv(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from openprogram.cli import application
    from openprogram import paths

    # Seed the default-profile value through monkeypatch so teardown records
    # and removes the profile that _tui_startup_log_path() sets directly.
    monkeypatch.setenv("OPENPROGRAM_PROFILE", "")
    monkeypatch.setattr(paths, "get_logs_dir", lambda: tmp_path / "logs")

    path = application._tui_startup_log_path(["--profile", "linux-check", "tui"])

    assert path == tmp_path / "logs" / "ink-startup.log"
    assert paths.get_active_profile() == "linux-check"


def test_argparse_errors_are_visible_before_tui_redirect(
    monkeypatch,
    capsys,
) -> None:
    import pytest

    from openprogram.cli import application

    redirects: list[bool] = []
    monkeypatch.setattr(
        application,
        "_maybe_redirect_for_tui",
        lambda: redirects.append(True),
    )

    for argv, expected in (
        (["openprogram", "--bogus"], "unrecognized arguments: --bogus"),
        (["openprogram", "--profile"], "argument --profile: expected one argument"),
    ):
        monkeypatch.setattr(application.sys, "argv", argv)
        with pytest.raises(SystemExit) as caught:
            application.main()
        assert caught.value.code == 2
        assert expected in capsys.readouterr().err

    assert redirects == []


def test_windows_tui_keeps_native_console_stdio(
    monkeypatch,
) -> None:
    from openprogram.cli import application

    monkeypatch.setattr(
        application,
        "tui_child_requires_direct_stdio_inheritance",
        lambda: True,
    )
    monkeypatch.setattr(
        application.os,
        "dup",
        lambda _fd: (_ for _ in ()).throw(
            AssertionError("Windows TUI must not duplicate console stdio")
        ),
    )
    monkeypatch.setattr(application.sys, "argv", ["openprogram", "tui"])
    monkeypatch.setattr(application.sys.stdout, "isatty", lambda: True)
    application._TUI_TTY_OUT = None
    application._TUI_TTY_ERR = None

    application._maybe_redirect_for_tui()

    assert application._TUI_TTY_OUT is None
    assert application._TUI_TTY_ERR is None


def test_ink_tui_does_not_eagerly_initialise_provider_runtime(
    monkeypatch,
) -> None:
    from openprogram.cli import chat, ink

    launched: list[dict[str, object]] = []
    monkeypatch.setattr(
        chat,
        "_get_chat_runtime",
        lambda: (_ for _ in ()).throw(
            AssertionError("Ink must let the worker initialise providers")
        ),
    )
    monkeypatch.setattr(
        ink,
        "run_ink_tui",
        lambda **kwargs: launched.append(kwargs),
    )

    chat.run_cli_chat(tui=True)

    assert len(launched) == 1
    assert str(launched[0]["session_id"]).startswith("local_")
    assert "agent" not in launched[0]
    assert "rt" not in launched[0]


def test_tui_accessibility_options_reach_ink_launcher(monkeypatch) -> None:
    from openprogram.cli import chat, ink

    launched: list[dict[str, object]] = []
    monkeypatch.setattr(ink, "run_ink_tui", lambda **kwargs: launched.append(kwargs))

    chat.run_cli_chat(tui=True, no_alt_screen=True, screen_reader=True)

    assert launched[0]["no_alt_screen"] is True
    assert launched[0]["screen_reader"] is True


def test_chat_command_forwards_tui_display_options(monkeypatch) -> None:
    from openprogram.cli import chat
    from openprogram.cli.commands import chat as command_chat

    launched: list[dict[str, object]] = []
    monkeypatch.setattr(chat, "run_cli_chat", lambda **kwargs: launched.append(kwargs))

    command_chat._cmd_cli_chat(no_alt_screen=True, screen_reader=True)

    assert launched[0]["no_alt_screen"] is True
    assert launched[0]["screen_reader"] is True


def test_worker_cold_start_timeout_is_platform_specific(monkeypatch) -> None:
    from openprogram import _compat

    monkeypatch.setattr(_compat._sys, "platform", "win32")
    assert _compat.tui_worker_ready_timeout_seconds() >= 120.0

    monkeypatch.setattr(_compat._sys, "platform", "linux")
    assert _compat.tui_worker_ready_timeout_seconds() == 30.0


def test_worker_wait_stops_when_observed_pid_disappears(monkeypatch) -> None:
    from openprogram import worker
    from openprogram.cli import ink
    from openprogram.worker import lifecycle

    pids = iter([4321, None])
    monkeypatch.setattr(worker, "spawn_detached", lambda: 0)
    monkeypatch.setattr(worker, "current_worker_pid", lambda: next(pids))
    monkeypatch.setattr(
        lifecycle,
        "find_running_webui",
        lambda: (None, None, "none"),
    )
    monkeypatch.setattr(ink.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(ink.time, "sleep", lambda _seconds: None)

    assert ink._resolve_worker_port(autostart=True) is None


def test_tui_requires_terminal_input_and_output(monkeypatch) -> None:
    from openprogram import cli as cli_module
    from openprogram.cli import ink

    monkeypatch.setattr(ink.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(ink.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli_module, "_TUI_TTY_OUT", None)
    assert ink._has_interactive_tui_stdio() is False

    monkeypatch.setattr(ink.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(ink.sys.stdout, "isatty", lambda: False)
    assert ink._has_interactive_tui_stdio() is False


def test_non_tty_tui_falls_back_before_runtime_or_worker_start(monkeypatch) -> None:
    import pytest

    from openprogram.cli import ink

    monkeypatch.setattr(ink, "_has_interactive_tui_stdio", lambda: False)
    monkeypatch.setattr(
        ink,
        "_resolve_node",
        lambda: (_ for _ in ()).throw(AssertionError("runtime lookup must not run")),
    )

    with pytest.raises(RuntimeError, match="requires terminal stdin and stdout"):
        ink.run_ink_tui()


def test_posix_saved_terminal_stdout_satisfies_tui_preflight(monkeypatch) -> None:
    from openprogram import cli as cli_module
    from openprogram.cli import ink

    monkeypatch.setattr(ink.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(ink.sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(cli_module, "_TUI_TTY_OUT", 99)
    monkeypatch.setattr(ink.os, "isatty", lambda fd: fd == 99)

    assert ink._has_interactive_tui_stdio() is True


def test_first_frame_handshake_classifies_startup_failure_without_timing() -> None:
    from openprogram.cli import ink

    assert ink._is_tui_startup_failure(
        first_frame_ready=False,
        user_interrupted=False,
    )
    assert not ink._is_tui_startup_failure(
        first_frame_ready=True,
        user_interrupted=False,
    )
    assert not ink._is_tui_startup_failure(
        first_frame_ready=False,
        user_interrupted=True,
    )


def test_first_frame_handshake_requires_exact_marker(tmp_path: Path) -> None:
    from openprogram.cli import ink

    marker = tmp_path / "first-frame"
    assert not ink._tui_first_frame_ready(marker)

    marker.write_text("not ready\n", encoding="utf-8")
    assert not ink._tui_first_frame_ready(marker)

    marker.write_text(ink._TUI_READY_MARKER, encoding="utf-8")
    assert ink._tui_first_frame_ready(marker)


def test_tui_child_environment_drops_inherited_handshake_path(monkeypatch) -> None:
    from openprogram.cli import ink

    monkeypatch.setenv(ink._TUI_READY_ENV, "caller-controlled")

    assert ink._TUI_READY_ENV not in ink._tui_child_environment()


def test_tui_child_receives_authoritative_profile_state_dir(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from openprogram.cli import ink
    from openprogram import paths

    profile = tmp_path / ".openprogram-linux-check"
    monkeypatch.setattr(paths, "get_state_dir", lambda: profile)

    assert ink._tui_child_environment()["OPENPROGRAM_STATE_DIR"] == str(profile)


def test_launcher_closes_owned_tty_fds_when_child_start_raises(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import pytest

    from openprogram import cli as cli_module
    from openprogram import paths
    from openprogram.cli import ink
    from openprogram.worker import lifecycle

    monkeypatch.setattr(ink, "_has_interactive_tui_stdio", lambda: True)
    monkeypatch.setattr(ink, "_resolve_node", lambda: "node")
    monkeypatch.setattr(ink, "_resolve_cli_entry", lambda: tmp_path / "index.cjs")
    monkeypatch.setattr(ink, "_resolve_worker_port", lambda **_kwargs: 18100)
    monkeypatch.setattr(
        lifecycle,
        "find_running_webui",
        lambda: (18100, 123, "managed"),
    )
    monkeypatch.setattr(
        ink,
        "tui_child_requires_direct_stdio_inheritance",
        lambda: False,
    )
    monkeypatch.setattr(paths, "get_logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(cli_module, "_TUI_TTY_OUT", None)
    monkeypatch.setattr(cli_module, "_TUI_TTY_ERR", None)

    duplicates = iter([71, 72])
    dup2_calls: list[tuple[int, int]] = []
    closed: list[int] = []
    monkeypatch.setattr(ink.os, "dup", lambda _fd: next(duplicates))
    monkeypatch.setattr(ink.os, "open", lambda *_args: 73)
    monkeypatch.setattr(ink.os, "dup2", lambda source, target: dup2_calls.append((source, target)))
    monkeypatch.setattr(ink.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(
        ink,
        "_run_ink_child",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )

    with pytest.raises(OSError, match="spawn failed"):
        ink.run_ink_tui()

    assert dup2_calls[-2:] == [(71, 1), (72, 2)]
    assert closed == [73, 71, 72]


def test_launcher_never_closes_cli_owned_tty_fds_on_child_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import pytest

    from openprogram import cli as cli_module
    from openprogram.cli import ink
    from openprogram.worker import lifecycle

    monkeypatch.setattr(ink, "_has_interactive_tui_stdio", lambda: True)
    monkeypatch.setattr(ink, "_resolve_node", lambda: "node")
    monkeypatch.setattr(ink, "_resolve_cli_entry", lambda: tmp_path / "index.cjs")
    monkeypatch.setattr(ink, "_resolve_worker_port", lambda **_kwargs: 18100)
    monkeypatch.setattr(
        lifecycle,
        "find_running_webui",
        lambda: (18100, 123, "managed"),
    )
    monkeypatch.setattr(
        ink,
        "tui_child_requires_direct_stdio_inheritance",
        lambda: False,
    )
    monkeypatch.setattr(cli_module, "_TUI_TTY_OUT", 81)
    monkeypatch.setattr(cli_module, "_TUI_TTY_ERR", 82)
    closed: list[int] = []
    monkeypatch.setattr(ink.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(
        ink,
        "_run_ink_child",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("render failed")),
    )

    with pytest.raises(OSError, match="render failed"):
        ink.run_ink_tui()

    assert closed == []


def test_partial_tty_dup_failure_does_not_close_cli_owned_descriptor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import pytest

    from openprogram import cli as cli_module
    from openprogram import paths
    from openprogram.cli import ink
    from openprogram.worker import lifecycle

    monkeypatch.setattr(ink, "_has_interactive_tui_stdio", lambda: True)
    monkeypatch.setattr(ink, "_resolve_node", lambda: "node")
    monkeypatch.setattr(ink, "_resolve_cli_entry", lambda: tmp_path / "index.cjs")
    monkeypatch.setattr(ink, "_resolve_worker_port", lambda **_kwargs: 18100)
    monkeypatch.setattr(
        lifecycle,
        "find_running_webui",
        lambda: (18100, 123, "managed"),
    )
    monkeypatch.setattr(
        ink,
        "tui_child_requires_direct_stdio_inheritance",
        lambda: False,
    )
    monkeypatch.setattr(paths, "get_logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(cli_module, "_TUI_TTY_OUT", None)
    monkeypatch.setattr(cli_module, "_TUI_TTY_ERR", 82)

    duplicates = iter([71, OSError("fd exhausted")])
    closed: list[int] = []

    def duplicate(_fd: int) -> int:
        result = next(duplicates)
        if isinstance(result, OSError):
            raise result
        return result

    monkeypatch.setattr(ink.os, "dup", duplicate)
    monkeypatch.setattr(ink.os, "dup2", lambda *_args: None)
    monkeypatch.setattr(ink.os, "close", lambda fd: closed.append(fd))

    with pytest.raises(OSError, match="fd exhausted"):
        ink.run_ink_tui()

    assert closed == [71]
    assert 82 not in closed


def test_packaged_tui_resolves_its_own_node_and_bundle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from openprogram.cli import ink

    runtime = tmp_path / "runtime"
    python = runtime / "py" / "python.exe"
    node = runtime / "bin" / "node.exe"
    entry = runtime / "assets" / "tui" / "index.cjs"
    for path in (python, node, entry):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    (runtime / "runtime-manifest.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", "")

    assert ink._managed_runtime_root() == runtime
    assert ink._resolve_node() == str(node)
    assert ink._resolve_cli_entry() == entry


def test_incomplete_packaged_tui_requests_complete_reinstall(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import pytest

    from openprogram.cli import ink

    runtime = tmp_path / "runtime"
    python = runtime / "py" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (runtime / "runtime-manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(python))

    with pytest.raises(FileNotFoundError, match="complete OpenProgram release"):
        ink._resolve_cli_entry()
    with pytest.raises(RuntimeError, match="complete OpenProgram release"):
        ink._resolve_node()
