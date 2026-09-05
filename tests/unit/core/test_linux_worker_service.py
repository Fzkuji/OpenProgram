from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openprogram.worker.services import systemd


def test_systemd_unit_identity_isolated_by_profile(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("OPENPROGRAM_PROFILE", raising=False)
    default_name = systemd._unit_name()
    default_path = systemd._unit_path()

    monkeypatch.setenv("OPENPROGRAM_PROFILE", "dev / 中文")
    profile_name = systemd._unit_name()
    profile_path = systemd._unit_path()

    assert default_name == systemd.UNIT_NAME
    assert profile_name.startswith("openprogram-worker-profile-")
    assert profile_name.endswith(".service")
    assert profile_name != default_name
    assert profile_path != default_path
    assert profile_path.parent == default_path.parent
    assert "dev" not in profile_name
    assert "/" not in profile_name


def test_systemd_profile_stop_never_targets_default_unit(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("OPENPROGRAM_PROFILE", raising=False)
    default_path = systemd._unit_path()
    default_path.parent.mkdir(parents=True)
    default_path.write_text("[Service]\nExecStart=/default\n", encoding="utf-8")

    monkeypatch.setenv("OPENPROGRAM_PROFILE", "dev")
    profile_name = systemd._unit_name()
    profile_path = systemd._unit_path()
    profile_path.write_text("[Service]\nExecStart=/dev\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        systemd,
        "_systemctl",
        lambda *args: calls.append(args) or (0, ""),
    )
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.current_worker_pid", lambda: None,
    )

    assert systemd.stop_if_installed() == 0
    assert calls == [("stop", profile_name)]
    assert default_path.is_file()
    assert profile_path.is_file()


def test_systemd_unit_quotes_paths_flags_and_packaged_environment(
    tmp_path: Path, monkeypatch,
) -> None:
    python = "/opt/Open Program/50%/$runtime/python"
    log = tmp_path / "state 50%" / "worker.log"
    monkeypatch.setattr(
        "openprogram.worker.lifecycle._detached_worker_command",
        lambda: [python, "-I", "-B", "-u", "-m", "openprogram", "worker", "run"],
    )
    monkeypatch.setattr(systemd.worker_paths, "log_path", lambda: log)
    monkeypatch.setenv("PATH", "/home/me/.local/bin:/usr/bin")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/Open Program/assets/playwright")
    monkeypatch.setenv("GPA_MODEL_PATH", "/opt/Open Program/assets/gpa/model.pt")
    monkeypatch.setenv("OPENPROGRAM_IMMUTABLE_RUNTIME", "1")
    monkeypatch.setenv("OPENPROGRAM_STATE_DIR", "/srv/Open Program/state")
    monkeypatch.setenv("OPENPROGRAM_BIN_DIR", "/srv/Open Program/bin")

    unit = systemd._build_unit()

    assert 'ExecStart="/opt/Open Program/50%%/$$runtime/python" "-I" "-B"' in unit
    assert "WorkingDirectory=%h\n" in unit
    assert f"StandardOutput=append:{str(log).replace('%', '%%')}\n" in unit
    assert f"StandardError=append:{str(log).replace('%', '%%')}\n" in unit
    assert 'Environment="PLAYWRIGHT_BROWSERS_PATH=/opt/Open Program/assets/playwright"' in unit
    assert 'Environment="GPA_MODEL_PATH=/opt/Open Program/assets/gpa/model.pt"' in unit
    assert 'Environment="OPENPROGRAM_IMMUTABLE_RUNTIME=1"' in unit
    assert 'Environment="OPENPROGRAM_STATE_DIR=/srv/Open Program/state"' in unit
    assert 'Environment="OPENPROGRAM_BIN_DIR=/srv/Open Program/bin"' in unit
    assert "OPENAI_API_KEY" not in unit


def test_systemd_install_checks_user_bus_before_stopping_worker(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    unit_file = tmp_path / systemd.UNIT_NAME
    monkeypatch.setattr(systemd, "_unit_path", lambda: unit_file)
    monkeypatch.setattr(
        systemd,
        "_systemctl",
        lambda *args: (1, "Failed to connect to bus: No medium found"),
    )
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.current_worker_pid",
        lambda: (_ for _ in ()).throw(AssertionError("worker was inspected")),
    )

    assert systemd.install() == 1
    assert not unit_file.exists()
    output = capsys.readouterr().out
    assert "No systemd user bus" in output
    assert "openprogram worker start" in output


@pytest.mark.parametrize("failed_probe", ["is-enabled", "is-active"])
def test_systemd_install_rejects_manager_probe_error_before_mutation(
    failed_probe: str, tmp_path: Path, monkeypatch,
) -> None:
    unit_file = tmp_path / systemd.UNIT_NAME
    original = b"[Service]\nExecStart=/healthy\n"
    unit_file.write_bytes(original)
    calls: list[tuple[str, ...]] = []

    def fake_systemctl(*args: str) -> tuple[int, str]:
        calls.append(args)
        if args == ("show-environment",):
            return 0, ""
        if args[0] == failed_probe:
            return 124, "systemctl --user timed out"
        if args == ("is-enabled", systemd.UNIT_NAME):
            return 0, "enabled"
        raise AssertionError(f"unexpected systemctl mutation: {args!r}")

    monkeypatch.setattr(systemd, "_unit_path", lambda: unit_file)
    monkeypatch.setattr(systemd, "_systemctl", fake_systemctl)
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.current_worker_pid",
        lambda: (_ for _ in ()).throw(AssertionError("worker was inspected")),
    )

    assert systemd.install() == 124
    assert unit_file.read_bytes() == original
    assert not any(call[0] in {"stop", "enable", "disable"} for call in calls)


@pytest.mark.parametrize("argv", [["status"], ["worker", "status"]])
def test_cli_status_propagates_service_manager_failure(
    argv: list[str], monkeypatch,
) -> None:
    import openprogram.cli as application
    from openprogram import worker
    from openprogram.worker import services

    calls: list[bool] = []
    monkeypatch.setattr(sys, "argv", ["openprogram", *argv])
    monkeypatch.setattr(worker, "print_status", lambda: 0)
    monkeypatch.setattr(services, "is_supported", lambda: True)
    monkeypatch.setattr(
        services,
        "status",
        lambda: calls.append(True) or 124,
    )

    with pytest.raises(SystemExit) as exc_info:
        application.main()

    assert exc_info.value.code == 124
    assert calls == [True]


def test_systemd_install_restores_unit_and_detached_worker_on_reload_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    unit_file = tmp_path / systemd.UNIT_NAME
    original = b"[Service]\nExecStart=/old\n"
    unit_file.write_bytes(original)
    calls: list[tuple[str, ...]] = []

    def fake_systemctl(*args: str) -> tuple[int, str]:
        calls.append(args)
        if args == ("show-environment",):
            return 0, ""
        if args == ("is-enabled", systemd.UNIT_NAME):
            return 1, "disabled"
        if args == ("is-active", systemd.UNIT_NAME):
            return 3, "inactive"
        if args == ("daemon-reload",) and calls.count(args) == 1:
            return 5, "reload failed"
        return 0, ""

    restarted: list[bool] = []
    monkeypatch.setattr(systemd, "_unit_path", lambda: unit_file)
    monkeypatch.setattr(systemd, "_systemctl", fake_systemctl)
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.current_worker_pid", lambda: 42,
    )
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.stop_worker", lambda **_kwargs: 0,
    )
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.spawn_detached",
        lambda **_kwargs: restarted.append(True) or 0,
    )

    assert systemd.install() == 5
    assert unit_file.read_bytes() == original
    assert restarted == [True]
    assert ("disable", "--now", systemd.UNIT_NAME) in calls


def test_systemd_install_removes_new_unit_when_enable_fails(
    tmp_path: Path, monkeypatch,
) -> None:
    unit_file = tmp_path / systemd.UNIT_NAME

    def fake_systemctl(*args: str) -> tuple[int, str]:
        if args in {
            ("show-environment",),
            ("daemon-reload",),
            ("disable", "--now", systemd.UNIT_NAME),
        }:
            return 0, ""
        if args == ("enable", "--now", systemd.UNIT_NAME):
            return 6, "start failed"
        if args == ("is-enabled", systemd.UNIT_NAME):
            return 1, "disabled"
        if args == ("is-active", systemd.UNIT_NAME):
            return 3, "inactive"
        return 0, ""

    monkeypatch.setattr(systemd, "_unit_path", lambda: unit_file)
    monkeypatch.setattr(systemd, "_systemctl", fake_systemctl)
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.current_worker_pid", lambda: None,
    )

    assert systemd.install() == 6
    assert not unit_file.exists()


def test_systemd_write_failure_restarts_previous_active_service(
    tmp_path: Path, monkeypatch,
) -> None:
    unit_file = tmp_path / systemd.UNIT_NAME
    original = b"[Service]\nExecStart=/old\n"
    unit_file.write_bytes(original)
    calls: list[tuple[str, ...]] = []

    def fake_systemctl(*args: str) -> tuple[int, str]:
        calls.append(args)
        if args == ("is-enabled", systemd.UNIT_NAME):
            return 0, "enabled"
        if args == ("is-active", systemd.UNIT_NAME):
            return 0, "active"
        return 0, ""

    monkeypatch.setattr(systemd, "_unit_path", lambda: unit_file)
    monkeypatch.setattr(systemd, "_systemctl", fake_systemctl)
    monkeypatch.setattr(
        systemd,
        "_write_unit",
        lambda *_args: (_ for _ in ()).throw(OSError("read-only filesystem")),
    )
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.current_worker_pid", lambda: 42,
    )
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.stop_worker", lambda **_kwargs: 0,
    )

    assert systemd.install() == 1
    assert unit_file.read_bytes() == original
    assert ("start", systemd.UNIT_NAME) in calls


def test_systemd_uninstall_keeps_unit_when_stop_disable_fails(
    tmp_path: Path, monkeypatch,
) -> None:
    unit_file = tmp_path / systemd.UNIT_NAME
    unit_file.write_text("[Service]\nExecStart=/old\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def fake_systemctl(*args: str) -> tuple[int, str]:
        calls.append(args)
        return 7, "user bus unavailable"

    monkeypatch.setattr(systemd, "_unit_path", lambda: unit_file)
    monkeypatch.setattr(systemd, "_systemctl", fake_systemctl)

    assert systemd.uninstall() == 7
    assert unit_file.exists()
    assert calls == [("disable", "--now", systemd.UNIT_NAME)]


def test_systemd_install_stops_active_unit_even_before_pid_lock_appears(
    tmp_path: Path, monkeypatch,
) -> None:
    unit_file = tmp_path / systemd.UNIT_NAME
    unit_file.write_text("[Service]\nExecStart=/old\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def fake_systemctl(*args: str) -> tuple[int, str]:
        calls.append(args)
        if args == ("is-enabled", systemd.UNIT_NAME):
            return 0, "enabled"
        if args == ("is-active", systemd.UNIT_NAME):
            return 0, "active"
        return 0, ""

    monkeypatch.setattr(systemd, "_unit_path", lambda: unit_file)
    monkeypatch.setattr(systemd, "_systemctl", fake_systemctl)
    monkeypatch.setattr(systemd, "_linger_enabled", lambda: True)
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.current_worker_pid", lambda: None,
    )
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.stop_worker",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("detached stop used")
        ),
    )

    assert systemd.install() == 0
    assert calls.index(("stop", systemd.UNIT_NAME)) < calls.index(("daemon-reload",))
    assert ("enable", "--now", systemd.UNIT_NAME) in calls


def test_systemd_install_preserves_unit_when_active_service_will_not_stop(
    tmp_path: Path, monkeypatch,
) -> None:
    unit_file = tmp_path / systemd.UNIT_NAME
    original = "[Service]\nExecStart=/old\n"
    unit_file.write_text(original, encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def fake_systemctl(*args: str) -> tuple[int, str]:
        calls.append(args)
        if args == ("is-enabled", systemd.UNIT_NAME):
            return 0, "enabled"
        if args == ("is-active", systemd.UNIT_NAME):
            return 0, "active"
        if args == ("stop", systemd.UNIT_NAME):
            return 4, "stop timed out"
        return 0, ""

    monkeypatch.setattr(systemd, "_unit_path", lambda: unit_file)
    monkeypatch.setattr(systemd, "_systemctl", fake_systemctl)
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.current_worker_pid", lambda: None,
    )

    assert systemd.install() == 4
    assert unit_file.read_text(encoding="utf-8") == original
    assert ("daemon-reload",) not in calls


def test_systemd_start_and_restart_refresh_an_installed_unit(
    tmp_path: Path, monkeypatch,
) -> None:
    unit_file = tmp_path / systemd.UNIT_NAME
    unit_file.write_text("[Service]\nExecStart=/old\n", encoding="utf-8")
    installs: list[bool] = []
    monkeypatch.setattr(systemd, "_unit_path", lambda: unit_file)
    monkeypatch.setattr(systemd, "install", lambda: installs.append(True) or 0)

    assert systemd.start_if_installed() == 0
    assert systemd.restart_if_installed() == 0
    assert installs == [True, True]

    unit_file.unlink()
    assert systemd.start_if_installed() is None
    assert systemd.restart_if_installed() is None


def test_lifecycle_service_failure_never_falls_back_to_detached(
    monkeypatch,
) -> None:
    from openprogram.worker import lifecycle, services

    monkeypatch.setattr(services, "start_if_installed", lambda: 9)
    monkeypatch.setattr(
        lifecycle,
        "current_worker_pid",
        lambda: (_ for _ in ()).throw(AssertionError("detached path used")),
    )

    assert lifecycle.spawn_detached() == 9


def test_lifecycle_restart_delegates_to_installed_service(monkeypatch) -> None:
    from openprogram.worker import lifecycle, services

    monkeypatch.setattr(services, "restart_if_installed", lambda: 0)
    monkeypatch.setattr(
        lifecycle,
        "current_worker_pid",
        lambda: (_ for _ in ()).throw(AssertionError("detached path used")),
    )

    assert lifecycle.restart_worker() == 0


def test_systemd_stop_keeps_service_ownership_and_cleans_legacy_detached_worker(
    tmp_path: Path, monkeypatch,
) -> None:
    unit_file = tmp_path / systemd.UNIT_NAME
    unit_file.write_text("[Service]\nExecStart=/old\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    raw_stops: list[bool] = []
    monkeypatch.setattr(systemd, "_unit_path", lambda: unit_file)
    monkeypatch.setattr(
        systemd,
        "_systemctl",
        lambda *args: calls.append(args) or (0, ""),
    )
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.current_worker_pid", lambda: 321,
    )
    monkeypatch.setattr(
        "openprogram.worker.lifecycle.stop_worker",
        lambda **kwargs: raw_stops.append(kwargs["prefer_service"]) or 0,
    )

    assert systemd.stop_if_installed() == 0
    assert calls == [("stop", systemd.UNIT_NAME)]
    assert raw_stops == [False]
