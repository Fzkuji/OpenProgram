"""Resolve a one-shot shell location without changing worktree or process state."""
from __future__ import annotations

import ntpath
import os
import posixpath
import shlex
import stat


def prepare_command(
    command: str,
    workdir: str | None,
    *,
    backend_id: str,
    worktree: str | None,
) -> tuple[str, str | None, str | None]:
    """Return command, backend cwd, and reported starting directory.

    Omission preserves the backend's existing default. Explicit local paths
    are checked against the original workspace, never authorized by themselves.
    Remote paths are interpreted by the remote shell, not the host filesystem.
    """
    from openprogram import sandbox

    if workdir is None:
        reported = worktree or (os.getcwd() if backend_id == "local" else None)
        return command, worktree, reported
    if not isinstance(workdir, str) or not workdir.strip() or "\x00" in workdir:
        raise ValueError("workdir must be a non-empty path without NUL characters")

    drive, _ = ntpath.splitdrive(workdir)
    if backend_id == "local":
        # Reject drive-relative/root-relative Windows paths: their base depends
        # on ambient per-drive state rather than the bound workspace.
        if os.name == "nt" and (drive or workdir.startswith(("/", "\\"))):
            if not drive or not ntpath.isabs(workdir):
                raise ValueError("workdir must be workspace-relative or a full absolute path")
        base = os.path.realpath(worktree or os.getcwd())
        target = os.path.realpath(os.path.join(base, workdir))
        # Check before stat, and keep policy anchored to the original workspace.
        # In particular, passing workdir as cwd to validate_write_path would
        # grant the requested directory just because the model named it.
        violation = sandbox.validate_read_path(target)
        if violation:
            raise PermissionError(f"sandbox policy: {violation}")
        violation = sandbox.validate_write_path(target, cwd=base)
        if violation:
            raise PermissionError(f"sandbox policy: {violation}")
        if not stat.S_ISDIR(os.stat(target).st_mode):
            raise NotADirectoryError(f"workdir is not a directory: {target}")
        if sandbox.resolve_policy() is not None:
            # The sandbox grants its starting cwd. Launch under the ORIGINAL
            # root and change directory INSIDE it, so even a symlink swap after
            # validation cannot turn the requested workdir into a new grant.
            reason = sandbox.unavailable_reason()
            if reason is not None:
                raise PermissionError(f"explicit workdir requires the configured sandbox: {reason}")
            shell_target = target
            if os.name == "nt":
                from openprogram._compat import windows_path_to_wsl
                shell_target = windows_path_to_wsl(target)
            return f"cd {shlex.quote(shell_target)} || exit\n{command}", base, target
        return command, target, target

    if backend_id not in {"ssh", "docker"}:
        raise ValueError(f"backend {backend_id!r} does not support explicit workdir")
    # A host policy cannot authorize paths in another filesystem. Do not use
    # host realpath/stat, or silently disable sandboxing for a remote override.
    if sandbox.resolve_policy() is not None:
        raise PermissionError("explicit remote workdir requires backend-native sandbox validation")
    if drive or "\\" in workdir:
        raise ValueError("SSH/Docker workdir must use a POSIX path")
    if posixpath.isabs(workdir):
        target = workdir
    elif worktree and posixpath.isabs(worktree) and "\\" not in worktree:
        # Preserve symlink/.. semantics for the remote filesystem.
        target = posixpath.join(worktree, workdir)
    else:
        raise ValueError("relative remote workdir requires an absolute POSIX worktree binding")
    # Run from the backend default and explicitly cd inside its filesystem.
    # This avoids Docker -w creating a missing directory. A failed cd stops ALL
    # user commands, even a list such as 'first; second'. Quote only the path;
    # the original command remains a shell command, not a path or host probe.
    guarded = f"cd {shlex.quote(target)} || exit\n{command}"
    return guarded, None, target
