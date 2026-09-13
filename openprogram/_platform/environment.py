"""Platform environment."""
from __future__ import annotations
from .. import _compat as state


def can_open_browser() -> bool:
    """Whether an automatic browser launch is meaningful on this host.

    Linux servers and SSH sessions commonly have ``xdg-open`` installed but
    no graphical session.  Calling :mod:`webbrowser` there either produces a
    misleading success or starts a helper that cannot display anything.  WSLg
    and desktop Linux expose DISPLAY/WAYLAND_DISPLAY and remain supported.
    """

    if not state._sys.platform.startswith("linux"):
        return True
    return bool(
        state._os.environ.get("DISPLAY") or state._os.environ.get("WAYLAND_DISPLAY")
    )


def open_browser_url(url: str, *, new: int = 2) -> bool:
    """Best-effort open ``url`` without launching dead helpers headlessly."""

    if not state.can_open_browser():
        return False
    try:
        import webbrowser

        return bool(webbrowser.open(url, new=new))
    except Exception:
        return False


def tui_child_requires_direct_stdio_inheritance() -> bool:
    """Whether an interactive Node TUI must inherit the console directly.

    Windows exposes console streams as native handles behind CRT file
    descriptors.  Passing descriptors saved with :func:`os.dup` through
    ``subprocess.Popen(stdout=..., stderr=...)`` is not stable across the
    detached worker bootstrap and can fail with ``WinError 6``.  Direct
    inheritance also preserves Node's ``isTTY``/raw-mode detection.

    POSIX descriptors remain safe to duplicate, which lets the Python startup
    phase write to a log before the Ink child takes over the real terminal.
    Keep this platform distinction in the compatibility seam rather than in
    the CLI launcher.
    """

    return state._sys.platform == "win32"


def tui_worker_ready_timeout_seconds() -> float:
    """Upper bound for the TUI's detached-worker cold start.

    A complete Windows runtime can spend over a minute in Defender's first
    scan. POSIX has no equivalent reason to leave an apparently blank terminal
    waiting for two minutes after a worker startup failure.
    """

    return 120.0 if state._sys.platform == "win32" else 30.0


@state._functools.cache
def _windows_default_wsl2_distribution() -> tuple[str | None, str | None]:
    """Return the default WSL2 distribution and an actionable failure.

    Reading the per-user WSL registry avoids launching ``wsl.exe`` on hosts
    where it is installed as a Windows feature but has no distribution.  On
    those machines the launcher can wait for interactive setup indefinitely.
    """

    if state._sys.platform != "win32":
        return None, "WSL2 delegation is only available on Windows"
    try:
        import winreg

        root_path = r"Software\Microsoft\Windows\CurrentVersion\Lxss"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root_path) as root:
            default_id, _ = winreg.QueryValueEx(root, "DefaultDistribution")
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            root_path + "\\" + str(default_id),
        ) as distro:
            name, _ = winreg.QueryValueEx(distro, "DistributionName")
            version, _ = winreg.QueryValueEx(distro, "Version")
    except (OSError, ValueError, TypeError):
        return None, (
            "Windows sandbox needs a default WSL2 distribution with "
            "bubblewrap installed"
        )
    if int(version) != 2:
        return None, f"default WSL distribution {name!r} is not WSL2"
    if not str(name).strip():
        return None, "default WSL2 distribution has no name"
    return str(name), None


def _windows_wsl_executable() -> str | None:
    import shutil

    return shutil.which("wsl.exe") if state._sys.platform == "win32" else None


def windows_wsl_exec_prefix() -> list[str]:
    """Return a stable argv prefix for the default WSL2 distribution."""

    executable = state._windows_wsl_executable()
    distribution, reason = state._windows_default_wsl2_distribution()
    if executable is None:
        raise RuntimeError("Windows sandbox needs wsl.exe")
    if reason is not None or distribution is None:
        raise RuntimeError(reason or "Windows sandbox needs a default WSL2 distribution")
    return [executable, "--distribution", distribution, "--exec"]


@state._functools.cache
def windows_wsl_sandbox_reason() -> str | None:
    """Why WSL2+bubblewrap cannot enforce the Windows sandbox, if any."""

    try:
        prefix = state.windows_wsl_exec_prefix()
    except RuntimeError as exc:
        return str(exc)
    probe = (
        "command -v bwrap >/dev/null 2>&1 || exit 21; "
        "command -v bash >/dev/null 2>&1 || exit 22; "
        "exec bwrap --new-session --die-with-parent --unshare-pid "
        "--unshare-ipc --unshare-uts --unshare-net --cap-drop ALL "
        "--ro-bind / / --proc /proc --dev /dev -- /bin/true"
    )
    flags = getattr(state._subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = state._subprocess.run(
            [*prefix, "sh", "-c", probe],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=flags,
        )
    except state._subprocess.TimeoutExpired:
        return "Windows WSL2 sandbox probe timed out"
    except OSError as exc:
        return f"Windows WSL2 sandbox probe failed: {exc}"
    if result.returncode == 0:
        return None
    if result.returncode == 21:
        return "Windows sandbox needs bubblewrap in the default WSL2 distribution"
    if result.returncode == 22:
        return "Windows sandbox needs /bin/bash in the default WSL2 distribution"
    lines = (result.stderr or result.stdout).strip().splitlines()
    detail = f": {lines[-1]}" if lines else ""
    return "WSL2 bubblewrap cannot create the required namespaces" + detail


@state._functools.lru_cache(maxsize=512)
def windows_path_to_wsl(path: str) -> str:
    """Translate one absolute Windows path through the selected WSL distro."""

    absolute = state._os.path.abspath(state._os.fspath(path))
    prefix = state.windows_wsl_exec_prefix()
    flags = getattr(state._subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = state._subprocess.run(
            [*prefix, "wslpath", "-a", "-u", absolute],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=flags,
        )
    except (state._subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"could not translate Windows path for WSL2: {exc}") from exc
    translated = result.stdout.strip()
    if result.returncode != 0 or not translated.startswith("/"):
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"could not translate Windows path for WSL2: {absolute}"
            + (f" ({detail})" if detail else "")
        )
    return translated


def managed_release_target(
    system: str | None = None,
    machine: str | None = None,
) -> tuple[str, str, str, str] | None:
    """Return the formal runtime platform, arch, suffix and installer.

    This is the single platform seam used by the managed updater. Archive
    format is part of the target contract: POSIX runtimes use ``tar.gz`` and
    Windows runtimes use a ZIP that needs no Unix compatibility layer.
    """

    import platform as _platform

    system = system or _platform.system()
    machine = (machine or _platform.machine()).lower()
    platform_name = {
        "Darwin": "macos",
        "Linux": "linux",
        "Windows": "windows",
    }.get(system)
    arch = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x86_64",
        "amd64": "x86_64",
    }.get(machine)
    if platform_name is None or arch is None:
        return None
    if platform_name == "windows":
        return platform_name, arch, ".zip", "install-release.ps1"
    return platform_name, arch, ".tar.gz", "install-release.sh"


def release_installer_command(path) -> list[str]:
    """Build the native command for one downloaded release installer."""

    import shutil

    value = state._os.fspath(path)
    if value.lower().endswith(".ps1"):
        executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if executable is None:
            raise OSError("PowerShell is required to run the release installer")
        return [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            value,
        ]
    return ["sh", value]


def release_installer_fallback_command(path) -> list[str] | None:
    """Return a Git-for-Windows fallback for a POSIX installer, if present.

    Formal Windows releases use PowerShell. This fallback only preserves the
    ability to exercise or recover an older POSIX-tagged managed installer on
    a Windows host where ``sh`` is not on PATH.
    """

    import shutil

    value = state._os.fspath(path)
    if state._sys.platform != "win32" or not value.lower().endswith(".sh"):
        return None
    git = shutil.which("git.exe")
    if not git:
        return None
    candidate = state._os.path.abspath(
        state._os.path.join(state._os.path.dirname(git), "..", "bin", "sh.exe")
    )
    return [candidate, value] if state._os.path.isfile(candidate) else None


def platform_environment_advisories(state_dir) -> list[tuple[bool, str, str]]:
    """Return non-blocking host advice without changing system settings.

    Rows are marked successful because these are optional platform capability
    and performance notes rather than requirements for running OpenProgram.
    No probe changes service state, security policy, ACLs, or file modes.
    """

    if state._sys.platform.startswith("linux"):
        from openprogram.sandbox import unavailable_reason

        sandbox_reason = unavailable_reason()
        sandbox_detail = (
            "available (bubblewrap namespaces verified)"
            if sandbox_reason is None
            else f"optional isolation unavailable: {sandbox_reason}"
        )
        service_reason = state._linux_systemd_user_reason()
        service_detail = (
            "available"
            if service_reason is None
            else f"optional login service unavailable: {service_reason}; "
                 "use `openprogram worker start`"
        )
        return [
            (True, "linux sandbox", sandbox_detail),
            (True, "systemd user service", service_detail),
        ]

    if state._sys.platform != "win32":
        return []

    long_paths = state._windows_powershell(
        "$v=(Get-ItemProperty "
        "'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem' "
        "-Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled;"
        "if($null -eq $v){'unknown'}elseif($v -eq 1){'enabled'}else{'disabled'}"
    ).strip().lower()
    if long_paths == "enabled":
        long_path_detail = "enabled"
    elif long_paths == "disabled":
        long_path_detail = (
            "disabled — enable Win32 long paths if deeply nested Programs fail"
        )
    else:
        long_path_detail = "status unavailable; no setting was changed"

    import json

    defender_output = state._windows_powershell(
        "$s=Get-MpComputerStatus -ErrorAction Stop;"
        "$p=Get-MpPreference -ErrorAction Stop;"
        "[pscustomobject]@{realTime=[bool]$s.RealTimeProtectionEnabled;"
        "exclusions=@($p.ExclusionPath)}|ConvertTo-Json -Compress",
        timeout=8.0,
    ).strip()
    runtime_dir = state._os.path.join(state._os.fspath(state_dir), "runtime")
    defender_detail = "status unavailable; no setting was changed"
    if defender_output:
        try:
            payload = json.loads(defender_output)
            exclusions = payload.get("exclusions") or []
            if isinstance(exclusions, str):
                exclusions = [exclusions]
            normalized_runtime = state._os.path.normcase(state._os.path.abspath(runtime_dir))
            excluded = any(
                state._os.path.normcase(state._os.path.abspath(str(value))).rstrip("\\/")
                == normalized_runtime.rstrip("\\/")
                for value in exclusions
                if value
            )
            if excluded:
                defender_detail = f"runtime exclusion present: {runtime_dir}"
            elif payload.get("realTime"):
                defender_detail = (
                    "real-time scanning active; if startup is slow, consider "
                    f"excluding only {runtime_dir}"
                )
            else:
                defender_detail = "real-time scanning is not active"
        except (TypeError, ValueError, OSError):
            pass

    return [
        (True, "windows long paths", long_path_detail),
        (True, "windows defender", defender_detail),
    ]


def _linux_systemd_user_reason() -> str | None:
    """Why a Linux systemd user manager cannot be reached, if any."""

    import shutil

    executable = shutil.which("systemctl")
    if executable is None:
        return "systemctl is not installed"
    try:
        result = state._subprocess.run(
            [executable, "--user", "show-environment"],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except state._subprocess.TimeoutExpired:
        return "systemctl --user timed out"
    except OSError as exc:
        return f"systemctl --user probe failed: {exc}"
    if result.returncode == 0:
        return None
    lines = (result.stderr or result.stdout).strip().splitlines()
    detail = lines[-1] if lines else f"exit status {result.returncode}"
    container = (
        state._os.path.exists("/.dockerenv")
        or state._os.path.exists("/run/.containerenv")
        or bool(state._os.environ.get("container"))
    )
    if container:
        return f"container has no reachable user manager ({detail})"
    return detail


def conversational_update_backend() -> str | None:
    """Installed source-update controller adapter, not the release updater.

    A worker service adapter alone is insufficient: packaging, activation,
    recovery and native verification must all implement the same transaction.
    Only the macOS controller currently supplies that complete adapter.
    """
    return "launchd" if state._sys.platform == "darwin" else None


def worker_service_backend() -> str | None:
    """Name of the per-user worker service adapter for this host."""

    if state._sys.platform == "darwin":
        return "launchd"
    if state._sys.platform == "linux":
        return "systemd"
    if state._sys.platform == "win32":
        return "windows"
    return None
