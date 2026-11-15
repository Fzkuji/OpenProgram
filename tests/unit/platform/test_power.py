"""Idle sleep assertions are optional and owned by individual executions."""
from unittest.mock import Mock

import pytest

from openprogram.host._platform import power


def test_disabled_does_not_acquire(monkeypatch):
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {})
    acquire = Mock()
    monkeypatch.setattr(power, "_acquire_assertion", acquire)
    with power.prevent_idle_sleep():
        pass
    acquire.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_enabled_releases_on_exit(monkeypatch, fails):
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {"execution": {"prevent_idle_sleep": True}})
    release = Mock()
    monkeypatch.setattr(power, "_acquire_assertion", lambda: release)
    try:
        with power.prevent_idle_sleep():
            release.assert_not_called()
            if fails:
                raise RuntimeError("turn failed")
    except RuntimeError:
        assert fails
    release.assert_called_once_with()


def test_nested_executions_own_separate_assertions(monkeypatch):
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {"execution": {"prevent_idle_sleep": True}})
    first, second = Mock(), Mock()
    monkeypatch.setattr(power, "_acquire_assertion", Mock(side_effect=[first, second]))
    with power.prevent_idle_sleep():
        with power.prevent_idle_sleep():
            pass
        second.assert_called_once_with()
        first.assert_not_called()
    first.assert_called_once_with()


def test_native_failure_does_not_fail_execution(monkeypatch, caplog):
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {"execution": {"prevent_idle_sleep": True}})
    monkeypatch.setattr(power, "_acquire_assertion", Mock(side_effect=OSError("unsupported")))
    with power.prevent_idle_sleep():
        pass
    assert "Unable to prevent idle system sleep" in caplog.text


def test_release_failure_preserves_turn_error(monkeypatch):
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {"execution": {"prevent_idle_sleep": True}})
    monkeypatch.setattr(power, "_acquire_assertion", lambda: Mock(side_effect=OSError("release")))
    with pytest.raises(ValueError, match="turn error"):
        with power.prevent_idle_sleep():
            raise ValueError("turn error")


def test_native_boundary_uses_enabled_level_and_releases_id(monkeypatch):
    import ctypes

    cf, io = Mock(), Mock()
    cf.CFStringCreateWithCString.side_effect = [101, 102]

    def create(assertion_type, level, reason, output):
        assert (assertion_type, level, reason) == (101, 255, 102)
        ctypes.cast(output, ctypes.POINTER(ctypes.c_uint32))[0] = 73
        return 0

    io.IOPMAssertionCreateWithName.side_effect = create
    io.IOPMAssertionRelease.return_value = 0
    monkeypatch.setattr(power.sys, "platform", "darwin")
    monkeypatch.setattr(power.ctypes, "CDLL", Mock(side_effect=[cf, io]))
    release = power._acquire_assertion()
    assert [call.args[0] for call in cf.CFRelease.call_args_list] == [101, 102]
    io.IOPMAssertionRelease.assert_not_called()
    release()
    assert io.IOPMAssertionRelease.call_args.args[0].value == 73


def _windows_kernel(handle=0x1F4, set_ok=1, clear_ok=1):
    kernel = Mock()
    kernel.PowerCreateRequest.return_value = handle
    kernel.PowerSetRequest.return_value = set_ok
    kernel.PowerClearRequest.return_value = clear_ok
    return kernel


def test_windows_holds_a_system_required_power_request_until_release():
    """Not run on Windows: kernel32 is a stand-in at the ctypes boundary."""
    import ctypes

    kernel = _windows_kernel()
    seen = {}

    def create(reference):
        context = ctypes.cast(reference, ctypes.POINTER(power._ReasonContext)).contents
        seen.update(version=context.Version, flags=context.Flags, reason=context.Reason.SimpleReasonString)
        return 0x1F4

    kernel.PowerCreateRequest.side_effect = create
    release = power._acquire_windows(kernel)
    assert seen == {"version": 0, "flags": 1, "reason": "OpenProgram active agent execution"}
    kernel.PowerSetRequest.assert_called_once_with(0x1F4, 1)
    kernel.PowerClearRequest.assert_not_called()
    kernel.CloseHandle.assert_not_called()
    release()
    kernel.PowerClearRequest.assert_called_once_with(0x1F4, 1)
    kernel.CloseHandle.assert_called_once_with(0x1F4)


@pytest.mark.parametrize("handle", [None, 0, 2**64 - 1])
def test_windows_refused_request_raises_and_leaves_no_handle(handle):
    kernel = _windows_kernel(handle=handle)
    with pytest.raises(OSError, match="PowerCreateRequest failed"):
        power._acquire_windows(kernel)
    kernel.PowerSetRequest.assert_not_called()
    kernel.CloseHandle.assert_not_called()


def test_windows_closes_the_handle_when_the_request_cannot_be_set():
    kernel = _windows_kernel(set_ok=0)
    with pytest.raises(OSError, match="PowerSetRequest failed"):
        power._acquire_windows(kernel)
    kernel.CloseHandle.assert_called_once_with(0x1F4)


def test_windows_reason_context_matches_the_native_layout():
    import ctypes

    pointer = ctypes.sizeof(ctypes.c_void_p)
    # ULONG Version, ULONG Flags, then the union whose larger member is
    # {HMODULE, ULONG, ULONG, LPWSTR*}.
    assert ctypes.sizeof(power._ReasonContext) == 8 + pointer + 8 + pointer
    assert power._ReasonContext.Reason.offset == 8


class _Inhibitor:
    def __init__(self, exits=None, hangs=False):
        self.stdin = Mock()
        self.exits, self.hangs, self.killed = exits, hangs, False

    def poll(self):
        return self.exits

    def wait(self, timeout=None):
        if self.hangs and not self.killed:
            raise power.subprocess.TimeoutExpired("systemd-inhibit", timeout)
        return self.exits or 0

    def kill(self):
        self.killed = True


def test_linux_holds_a_logind_lock_that_ends_with_the_pipe():
    """Not run on Linux: systemd-inhibit is a stand-in process."""
    started = []
    child = _Inhibitor()

    def popen(argv, **options):
        started.append((argv, options))
        return child

    release = power._acquire_linux(which=lambda name: f"/usr/bin/{name}", popen=popen)
    argv, options = started[0]
    assert argv == ["/usr/bin/systemd-inhibit", "--what=idle:sleep", "--who=OpenProgram",
                    "--why=OpenProgram active agent execution", "--mode=block", "cat"]
    # The lock is the child's lifetime, and the child lives while this pipe is open.
    assert options["stdin"] == power.subprocess.PIPE
    child.stdin.close.assert_not_called()
    release()
    child.stdin.close.assert_called_once_with()
    assert child.killed is False


def test_linux_kills_an_inhibitor_that_does_not_end():
    child = _Inhibitor(hangs=True)
    power._acquire_linux(which=lambda name: name, popen=lambda *_a, **_k: child)()
    assert child.killed is True


def test_linux_reports_a_lock_logind_refused():
    child = _Inhibitor(exits=1)
    release = power._acquire_linux(which=lambda name: name, popen=lambda *_a, **_k: child)
    with pytest.raises(OSError, match="could not hold the idle sleep lock"):
        release()


def test_linux_without_systemd_inhibit_says_what_is_missing():
    with pytest.raises(OSError, match="needs systemd-inhibit"):
        power._acquire_linux(which=lambda _name: None, popen=Mock(side_effect=AssertionError("started")))


@pytest.mark.parametrize("platform, backend", [("darwin", "_acquire_macos"), ("win32", "_acquire_windows"),
                                               ("linux", "_acquire_linux")])
def test_every_desktop_platform_has_a_backend(monkeypatch, platform, backend):
    release = Mock()
    monkeypatch.setattr(power.sys, "platform", platform)
    monkeypatch.setattr(power, backend, lambda: release)
    assert power._acquire_assertion() is release


def test_support_names_what_each_platform_needs():
    assert power.support("darwin") == {"available": True, "reason": ""}
    assert power.support("win32") == {"available": True, "reason": ""}
    assert power.support("linux", which=lambda name: name)["available"] is True
    assert "systemd-inhibit" in power.support("linux", which=lambda _name: None)["reason"]
    assert power.support("freebsd14") == {"available": False,
                                          "reason": "Preventing idle sleep is not supported on freebsd14."}


def test_a_host_refusal_is_one_line_once(monkeypatch, caplog):
    monkeypatch.setattr("openprogram.state.config.read_config", lambda: {"execution": {"prevent_idle_sleep": True}})
    monkeypatch.setattr(power, "_acquire_assertion", Mock(side_effect=OSError("no logind here")))
    monkeypatch.setattr(power, "_reported", set())
    for _ in range(3):
        with power.prevent_idle_sleep():
            pass
    lines = [record for record in caplog.records if "no logind here" in record.getMessage()]
    assert len(lines) == 1 and lines[0].exc_info is None
