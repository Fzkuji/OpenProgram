"""Which shell a command runs in, decided in one place.

:func:`resolve_shell` answers it for an execution environment and the
sandbox in force, and every place that runs or describes a command reads
its answer: the command tool's invocation (``host.backend.local``, which
also runs a command sandboxed exactly when this says so), a user's hook on
Windows, the environment block the model reads, permission rule matching
and ``openprogram doctor``. Nothing else probes for a shell. On macOS and
Linux a hook keeps running in ``/bin/sh``, as hooks are documented to.

The host part is a file probe that does not change while the process
runs, so it is cached; whether the sandbox is in force is read on every
call, because a setting can change between two commands.

On Windows the host shell is Git Bash when one can be found and otherwise
PowerShell (``pwsh`` 7 before the built-in Windows PowerShell 5.1), the
order Claude Code uses. Bash is looked for in this order: the path the
owner named (``OPENPROGRAM_GIT_BASH_PATH``, then the setting
``shell.windows_bash_path``), Git's install locations, the bash inside the
Git the runtime ships (its base's ``base.json`` names it as ``gitBash``),
the bash beside every ``git.exe`` on ``PATH`` (``<root>/bin`` then
``<root>/usr/bin``, as Kimi Code derives it), and a ``bash.exe`` on
``PATH`` that is not the WSL launcher. A named path that is not a file is
passed over and reported by ``openprogram doctor``; it never stops a
command. Only absolute ``PATH`` entries are searched, never the current
directory that Windows otherwise searches first: a repository's own
``bash.exe`` must not become everyone's shell, and the answer must not
depend on where the process started.
With the sandbox in force a command runs in ``/bin/bash`` (inside the
default WSL2 distribution on Windows), and the host shell is where a
command approved to leave the sandbox runs.

Each profile states the promises its shell keeps (:data:`PROMISES`), so a
missing one is reported, never silently absent.
"""
from __future__ import annotations

import ntpath
import os
import shutil
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

SUPPORTED = "supported"
PARTIAL = "partial"
UNSUPPORTED = "unsupported"

#: What a command's shell may or may not guarantee, in report order.
PROMISES = (
    "recoverable_delete",   # rm, rmdir and unlink move files to the run's trash
    "utf8_file_output",     # text redirected to a file is UTF-8
    "exit_code",            # the command's own exit code reaches the caller
    "process_tree",         # stopping a command stops everything it started
    "rule_parsing",         # permission rules can read the command
)

_POWERSHELL_KINDS = frozenset({"pwsh7", "powershell51"})

_PROMISES_BY_KIND: Mapping[str, Mapping[str, str]] = {
    "bash": dict.fromkeys(PROMISES, SUPPORTED),
    "sh": dict.fromkeys(PROMISES, SUPPORTED),
    "git-bash": dict.fromkeys(PROMISES, SUPPORTED),
    # Remove-Item and its aliases (rm, del) bypass the delete shims; prefix
    # rules read PowerShell with a POSIX tokenizer and decline what it
    # cannot read safely.
    "pwsh7": {**dict.fromkeys(PROMISES, SUPPORTED),
              "recoverable_delete": UNSUPPORTED, "rule_parsing": PARTIAL},
    # Windows PowerShell 5.1 writes `>` and Out-File as UTF-16 by default;
    # the command prelude makes them UTF-8 with a byte order mark, which
    # read and grep decode. Set-Content keeps its own default.
    "powershell51": {**dict.fromkeys(PROMISES, SUPPORTED),
                     "recoverable_delete": UNSUPPORTED, "utf8_file_output": PARTIAL,
                     "rule_parsing": PARTIAL},
    # The delete shims are Windows files the Linux side does not run, and
    # ending wsl.exe does not reach every process a command left behind.
    "wsl-bash": {**dict.fromkeys(PROMISES, SUPPORTED),
                 "recoverable_delete": UNSUPPORTED, "process_tree": PARTIAL},
    # A remote machine has no delete shims, and ending the local client does
    # not reach every process a command started there.
    "remote": {**dict.fromkeys(PROMISES, SUPPORTED),
               "recoverable_delete": UNSUPPORTED, "process_tree": PARTIAL},
}

_NAMES = {
    "git-bash": "Git Bash",
    "pwsh7": "PowerShell",
    "powershell51": "PowerShell",
    "bash": "bash",
    "sh": "sh",
    "wsl-bash": "bash in WSL",
    "remote": "remote shell",
}


#: The safety rules Codex gives a model on Windows (``windows_shell_guidance``),
#: for both PowerShell editions.
_POWERSHELL_SAFETY = (
    "never pass an unquoted path with spaces to a native program; "
    "use -LiteralPath for paths that may contain [ ] or *; "
    "do not delete or overwrite files outside the working directory without being asked"
)

#: What the model is told to write for a shell whose syntax it may not guess,
#: by ``(kind, edition)``. 5.1 rejects ``&&``, ``||``, ``?:`` and ``??`` with
#: a ParserError, and ``2>&1`` on a native program turns its stderr into
#: error records.
_SYNTAX = {
    ("pwsh7", "7"): "write PowerShell 7 syntax, not cmd or bash; " + _POWERSHELL_SAFETY,
    ("powershell51", "5.1"): (
        "write Windows PowerShell 5.1 syntax, not cmd or bash: there is no &&, ||, ?: or ??, "
        "so chain with ; and test with if ($?) { ... }; do not add 2>&1 to native programs; "
        "write files with the edit tools or Set-Content -Encoding utf8; " + _POWERSHELL_SAFETY
    ),
    ("git-bash", ""): "write bash syntax; Windows paths need quotes or forward slashes",
}


@dataclass(frozen=True)
class ShellProfile:
    """The shell a command runs in.

    ``kind`` is one of ``git-bash``, ``pwsh7``, ``powershell51``, ``bash``,
    ``sh``, ``wsl-bash`` or ``remote``. ``source`` says how it was found
    (``override`` for the path the owner named, ``install`` for a known
    install location, ``bundled`` for the Git the runtime ships, ``git``
    for the bash beside a ``git.exe`` on ``PATH``, ``path`` for ``PATH``,
    ``system`` for the operating system's own, ``sandbox``,
    ``environment``). ``edition`` is the PowerShell edition, read from the
    executable's name. A sandboxed profile carries the ``host`` profile a
    command approved to leave the sandbox runs in. ``override_unused`` is a
    path the owner named that is not a file, kept so ``doctor`` can say so.
    """

    kind: str
    path: str
    source: str
    edition: str = ""
    sandboxed: bool = False
    host: Optional["ShellProfile"] = None
    override_unused: str = ""

    @property
    def name(self) -> str:
        return _NAMES[self.kind]

    @property
    def syntax(self) -> str:
        return _SYNTAX.get((self.kind, self.edition), "")

    @property
    def is_powershell(self) -> bool:
        return self.kind in _POWERSHELL_KINDS

    @property
    def promises(self) -> dict[str, str]:
        return dict(_PROMISES_BY_KIND[self.kind])

    def as_dict(self) -> dict[str, Any]:
        row = {"kind": self.kind, "name": self.name, "path": self.path,
               "source": self.source, "edition": self.edition,
               "sandboxed": self.sandboxed, "promises": self.promises}
        if self.host is not None:
            row["host"] = self.host.as_dict()
        if self.override_unused:
            row["override_unused"] = self.override_unused
        return row


_POSIX_BASH_CANDIDATES = ("/bin/bash", "/usr/bin/bash", "/usr/local/bin/bash")


def _probe_posix(*, isfile: Optional[Callable[[str], bool]] = None,
                 executable: Optional[Callable[[str], bool]] = None,
                 which: Optional[Callable[[str], Optional[str]]] = None) -> ShellProfile:
    isfile = isfile or os.path.isfile
    executable = executable or (lambda p: os.access(p, os.X_OK))
    which = which or shutil.which
    # /bin/sh is dash on Debian and Ubuntu and rejects `[[`, `source` and
    # arrays, so it is the shell only on a host with no bash at all.
    for path in _POSIX_BASH_CANDIDATES:
        if isfile(path) and executable(path):
            return ShellProfile("bash", path, "system")
    found = which("bash")
    if found:
        return ShellProfile("bash", found, "path")
    return ShellProfile("sh", "/bin/sh", "system")


def _rooted(path: str) -> bool:
    # ntpath.isabs no longer counts a path without a drive as absolute.
    return bool(ntpath.splitdrive(path)[0]) or path.startswith(("\\", "/"))


def _absolute(folder: str) -> bool:
    # ``C:\tools`` and ``\\server\share`` name one folder; ``tools``, ``.``,
    # ``\tools`` and ``C:tools`` are read against the current directory or drive.
    drive, rest = ntpath.splitdrive(folder)
    if len(drive) == 2:
        return rest.startswith(("\\", "/"))
    return bool(drive)


def _on_path(name: str, env: Mapping[str, str],
             isfile: Callable[[str], bool]) -> list[str]:
    """Every file ``name`` names in the absolute directories on ``PATH``.

    ``shutil.which`` on Windows looks in the current directory first, and a
    relative ``PATH`` entry is read against it; neither is searched here, so
    the answer is the same whichever directory the process started in.
    """
    path = env.get("PATH") or env.get("Path") or ""
    exts = [e for e in (env.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(";") if e]
    names = ([name] if any(name.lower().endswith(e.lower()) for e in exts)
             else [name + e.lower() for e in exts])
    found = []
    for folder in path.split(";"):
        folder = folder.strip().strip('"')
        if not _absolute(folder):
            continue
        for candidate in names:
            full = ntpath.join(folder, candidate)
            if isfile(full):
                found.append(full)
    return found


def _is_wsl_launcher(path: str) -> bool:
    # System32\bash.exe and the WindowsApps execution alias exec into the
    # Linux subsystem, where the Windows working directory does not apply.
    lowered = path.lower().replace("/", "\\")
    return "\\system32\\" in lowered or "\\windowsapps\\" in lowered


def _powershell_profile(path: str, source: str) -> ShellProfile:
    if ntpath.basename(path).lower().startswith("pwsh"):
        return ShellProfile("pwsh7", path, source, edition="7")
    return ShellProfile("powershell51", path, source, edition="5.1")


def _exists(path: str) -> bool:
    # The test shutil.which makes, so an App Execution Alias such as the
    # Store's WindowsApps\pwsh.exe (a reparse point) counts as found.
    return os.path.exists(path) and not os.path.isdir(path)


def _bash_beside_git(env: Mapping[str, str], isfile: Callable[[str], bool]) -> list[str]:
    """The bash of every Git for Windows whose ``git.exe`` is on ``PATH``.

    ``git.exe`` sits in ``<root>/cmd`` (or ``<root>/bin``, or
    ``<root>/mingw64/bin``); bash is ``<root>/bin/bash.exe`` or
    ``<root>/usr/bin/bash.exe``. This finds scoop, portable and custom
    installs, as Claude Code, OpenCode and Kimi Code do.
    """
    found: list[str] = []
    for git in _on_path("git.exe", env, isfile):
        folder = ntpath.dirname(git)
        root = ntpath.dirname(folder)
        if ntpath.basename(folder).lower() == "bin" and ntpath.basename(root).lower() in ("mingw64", "mingw32", "clangarm64"):
            root = ntpath.dirname(root)
        for candidate in (ntpath.join(root, "bin", "bash.exe"), ntpath.join(root, "usr", "bin", "bash.exe")):
            if candidate not in found and isfile(candidate) and not _is_wsl_launcher(candidate):
                found.append(candidate)
    return found


def bash_override(environ: Mapping[str, str] | None = None) -> str:
    """The bash the owner named: ``OPENPROGRAM_GIT_BASH_PATH``, then ``shell.windows_bash_path``."""
    env = os.environ if environ is None else environ
    named = (env.get("OPENPROGRAM_GIT_BASH_PATH") or "").strip().strip('"')
    if named:
        return named
    try:
        from openprogram.state.settings import setting_value
        return str(setting_value("shell.windows_bash_path") or "").strip().strip('"')
    except Exception:  # noqa: BLE001 - an unreadable config names no bash
        return ""


def bundled_git_bash() -> str:
    """The bash inside the Git the running base ships, when its ``base.json`` names one."""
    try:
        import json
        from pathlib import Path
        from openprogram.operations.self_update.versions.layout import current_runtime
        runtime = current_runtime()
        if runtime is None:
            return ""
        version = json.loads((Path(sys.prefix).parent / "version.json").read_text(encoding="utf-8"))
        base = runtime.layout.base(str(version["base"]))
        relative = json.loads((base / "base.json").read_text(encoding="utf-8")).get("gitBash")
    except Exception:  # noqa: BLE001 - no manifest means no bundled bash
        return ""
    if not isinstance(relative, str) or not relative:
        return ""
    return str(base / "git" / relative)


def _powershell_on(env: Mapping[str, str], isfile: Callable[[str], bool]) -> ShellProfile:
    for name in ("pwsh.exe", "powershell.exe"):
        for found in _on_path(name, env, isfile):
            return _powershell_profile(found, "path")
    system_root = env.get("SystemRoot", r"C:\Windows")
    return _powershell_profile(
        ntpath.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
        "system",
    )


def windows_powershell(environ: Mapping[str, str] | None = None) -> ShellProfile:
    """The PowerShell a hook declared for PowerShell runs in, whatever the host shell is."""
    return _powershell_on(os.environ if environ is None else environ, _exists)


def _probe_windows(*, environ: Mapping[str, str] | None = None,
                   isfile: Optional[Callable[[str], bool]] = None,
                   override: str | None = None,
                   bundled: str | None = None) -> ShellProfile:
    env = os.environ if environ is None else environ
    isfile = isfile or _exists
    override = bash_override(env) if override is None else override
    unused = ""
    if override:
        if _rooted(override) and isfile(override):
            return ShellProfile("git-bash", override, "override")
        unused = override

    def found(kind: str, path: str, source: str) -> ShellProfile:
        return ShellProfile(kind, path, source, override_unused=unused)

    for candidate in (
        ntpath.join(env.get("ProgramFiles", r"C:\Program Files"), "Git", "bin", "bash.exe"),
        ntpath.join(env.get("ProgramW6432", r"C:\Program Files"), "Git", "bin", "bash.exe"),
        ntpath.join(env.get("LOCALAPPDATA", ""), "Programs", "Git", "bin", "bash.exe"),
    ):
        if _rooted(candidate) and isfile(candidate):
            return found("git-bash", candidate, "install")
    bundled = bundled_git_bash() if bundled is None else bundled
    if bundled and _rooted(bundled) and isfile(bundled):
        return found("git-bash", bundled, "bundled")
    for path in _bash_beside_git(env, isfile):
        return found("git-bash", path, "git")
    for path in _on_path("bash", env, isfile):
        if not _is_wsl_launcher(path):
            return found("git-bash", path, "path")
    shell = _powershell_on(env, isfile)
    return found(shell.kind, shell.path, shell.source) if unused else shell


_HOST_CACHE: dict[tuple[str, str], ShellProfile] = {}


def host_shell(platform: str | None = None) -> ShellProfile:
    """The shell an unsandboxed local command runs in on this machine.

    The probe is cached per named bash path, so changing the setting takes
    effect on the next command without a restart.
    """
    platform = platform or sys.platform
    override = bash_override() if platform == "win32" else ""
    key = (platform, override)
    profile = _HOST_CACHE.get(key)
    if profile is None:
        profile = (_probe_windows(override=override) if platform == "win32"
                   else _probe_posix())
        _HOST_CACHE[key] = profile
    return profile


def _reset_host_probe() -> None:
    _HOST_CACHE.clear()


def _sandbox_in_force(policy: Any = None) -> bool:
    from openprogram.host import sandbox
    if policy is None:
        policy = sandbox.resolve_policy()
    return policy is not None and sandbox.unavailable_reason() is None


def resolve_shell(environment: Any = None, *, sandboxed: bool | None = None,
                  policy: Any = None, platform: str | None = None) -> ShellProfile:
    """The shell a command runs in now.

    ``environment`` is the execution environment (``host.environments``);
    an SSH host or a Docker container runs commands in its own shell.
    ``policy`` is the sandbox policy a caller runs the command under, as
    ``host.backend.local`` takes it; without one it is read from the
    configuration. The command is sandboxed when there is a policy and the
    sandbox can run here. ``sandboxed`` states the answer outright.
    """
    if environment is not None and getattr(environment, "kind", "local") in ("ssh", "docker"):
        return ShellProfile("remote", "", "environment")
    platform = platform or sys.platform
    host = host_shell(platform)
    if sandboxed is None:
        sandboxed = _sandbox_in_force(policy)
    if not sandboxed:
        return host
    kind = "wsl-bash" if platform == "win32" else "bash"
    return ShellProfile(kind, "/bin/bash", "sandbox", sandboxed=True, host=host)


def msys_path(path: str) -> str:
    """A Windows path as Git Bash and MSYS2 spell it, without ``cygpath``.

    ``C:\\Users\\me`` becomes ``/c/Users/me`` and ``\\\\server\\share\\x``
    becomes ``//server/share/x``; a path already in POSIX form is kept.
    """
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    text = path.replace("\\", "/")
    if text.startswith("//"):
        return text
    drive, rest = ntpath.splitdrive(path)
    if len(drive) == 2 and drive[1] == ":":
        rest = rest.replace("\\", "/")
        return f"/{drive[0].lower()}" + (rest if rest.startswith("/") else "/" + rest)
    return text


__all__ = [
    "PARTIAL", "PROMISES", "SUPPORTED", "UNSUPPORTED", "ShellProfile",
    "bash_override", "bundled_git_bash", "host_shell", "msys_path", "resolve_shell",
    "windows_powershell",
]
