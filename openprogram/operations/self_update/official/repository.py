"""The installation's Git repository and the official line in it.

``repo/`` holds two lines: ``refs/heads/release``, the official code, and
``refs/heads/local``, what runs here, which is the official code plus the
owner's own commits. An installer seeds both with GitHub's ``main``.

An update does not ship Git history. Its code layer carries the commit's
whole source tree as objects, each with its Git blob id, and
:func:`import_official` rebuilds that tree here (only the blobs this
repository lacks are written), checks that it is exactly the published tree,
and commits it on top of ``release`` with fixed metadata. So the official line
here is the seed's commits followed by one local commit per update taken; its
ids differ from GitHub's, and ``refs/openprogram/official/<GitHub commit>``
says which local commit holds which published one. Replaying the owner's
commits only needs ``release`` and ``local`` to share history, which this
keeps, whichever updates a machine skipped.
"""
from __future__ import annotations

from collections.abc import Iterable
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from openprogram.host._platform import runtime_layout as paths
from openprogram.host._platform.architecture import interpreter_architecture
from openprogram.host._platform.runtime_layout import Layout

LOCAL_REF = "refs/heads/local"
RELEASE_REF = "refs/heads/release"
OFFICIAL_REFS = "refs/openprogram/official/"
GIT_TIMEOUT = 600
COMMITTER = ("OpenProgram", "updates@openprogram.invalid")
_COMMIT = re.compile(r"[0-9a-f]{40}")


class ReleaseError(RuntimeError):
    """An update cannot be used; the message says why, and nothing changed."""


def current_platform() -> str:
    """This machine, in the release matrix's ``<platform>-<arch>`` words."""
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
    # The running interpreter's architecture: an x64 install on Windows on Arm
    # updates to the x64 runtime, not to an Arm one it never installed.
    return f"{system}-{interpreter_architecture()}"


def git(
    repository: Path, *arguments: str, timeout: float = GIT_TIMEOUT, raw: bool = False,
    input: bytes | str | None = None, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run Git with no hook, no user config and a fixed committer."""
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_COMMITTER_NAME": COMMITTER[0],
        "GIT_COMMITTER_EMAIL": COMMITTER[1],
        "GIT_EDITOR": "true",
    }
    for name in ("SYSTEMROOT", "TMPDIR", "TEMP", "TMP", "HOME"):
        if name in os.environ:
            environment[name] = os.environ[name]
    environment.update(env or {})
    command = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
               "-c", "commit.gpgsign=false", *arguments]
    decoding = {} if raw else {"text": True, "errors": "replace"}
    if input is not None and not raw and isinstance(input, bytes):
        input = input.decode("utf-8")
    try:
        return subprocess.run(
            command, cwd=str(repository), env=environment, input=input,
            stdin=None if input is not None else subprocess.DEVNULL,
            capture_output=True, timeout=timeout, **decoding,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(f"git {arguments[0]} could not run: {exc}") from exc


def git_output(repository: Path, *arguments: str, **options) -> str:
    result = git(repository, *arguments, **options)
    if result.returncode != 0:
        raise ReleaseError(f"git {arguments[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def contains(layout: Layout, commit: str, ref: str) -> bool:
    return git(layout.repo, "merge-base", "--is-ancestor", commit, ref).returncode == 0


def local_official(layout: Layout, published: str) -> str | None:
    """The local commit holding GitHub's commit ``published``, if this repository has it.

    A seed's commits are GitHub's own; an update's are recorded under
    ``refs/openprogram/official/``.
    """
    if _COMMIT.fullmatch(published) is None:
        return None
    mapped = git(layout.repo, "rev-parse", "--verify", "--quiet", f"{OFFICIAL_REFS}{published}^{{commit}}")
    if mapped.returncode == 0:
        return mapped.stdout.strip()
    own = git(layout.repo, "cat-file", "-e", f"{published}^{{commit}}")
    return published if own.returncode == 0 else None


def has_official(layout: Layout, published: str) -> bool:
    """Whether what runs here already contains the published commit."""
    found = local_official(layout, published)
    return found is not None and contains(layout, found, LOCAL_REF)


def missing_blobs(layout: Layout, blobs: Iterable[str]) -> set[str]:
    """The Git blob ids this repository lacks."""
    wanted = sorted(set(blobs))
    if not wanted:
        return set()
    answer = git_output(layout.repo, "cat-file", "--batch-check=%(objectname) %(objecttype)",
                        input="\n".join(wanted) + "\n")
    missing = set()
    for line in answer.splitlines():
        name, _, kind = line.partition(" ")
        if kind.strip() != "blob":
            missing.add(name)
    return missing


def write_blobs(layout: Layout, files: dict[str, Path]) -> None:
    """Write blob ``id -> file`` into the repository, checking each id."""
    if not files:
        return
    ids = list(files)
    written = git_output(layout.repo, "hash-object", "-w", "--no-filters", "--stdin-paths",
                         input="".join(f"{files[name]}\n" for name in ids)).split()
    for expected, actual in zip(ids, written, strict=True):
        if expected != actual:
            raise ReleaseError(f"a source file does not hash to its Git blob {expected[:12]}")


def write_link_blob(layout: Layout, target: str, expected: str) -> None:
    actual = git_output(layout.repo, "hash-object", "-w", "--stdin", input=target)
    if actual != expected:
        raise ReleaseError(f"a source link does not hash to its Git blob {expected[:12]}")


def import_official(
    layout: Layout, *, published: str, tree: str, entries: Iterable[tuple[str, str, str]],
    message: str, when: str,
) -> str:
    """Commit the published tree on top of ``release`` and move ``release`` to it.

    ``entries`` are ``(path, git mode, blob id)``; every blob must be present.
    Returns the local commit. Raises :class:`ReleaseError` when the rebuilt
    tree is not ``tree``.
    """
    existing = local_official(layout, published)
    if existing is not None:
        if not contains(layout, existing, RELEASE_REF):
            git_output(layout.repo, "update-ref", RELEASE_REF, existing)
        return existing
    with tempfile.TemporaryDirectory(prefix="openprogram-index-") as scratch:
        index = {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
        lines = "".join(f"{mode} {blob}\t{path}\n" for path, mode, blob in entries)
        result = git(layout.repo, "update-index", "--add", "--index-info", input=lines, env=index)
        if result.returncode != 0:
            raise ReleaseError(f"the source tree could not be rebuilt: {result.stderr.strip()}")
        built = git_output(layout.repo, "write-tree", env=index)
    if built != tree:
        raise ReleaseError(f"the rebuilt source tree is {built[:12]}, and the update names {tree[:12]}")
    identity = {
        "GIT_AUTHOR_NAME": COMMITTER[0], "GIT_AUTHOR_EMAIL": COMMITTER[1],
        "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when,
    }
    parent = git(layout.repo, "rev-parse", "--verify", "--quiet", f"{RELEASE_REF}^{{commit}}")
    parents = ["-p", parent.stdout.strip()] if parent.returncode == 0 else []
    commit = git_output(layout.repo, "commit-tree", tree, *parents,
                        "-m", f"{message}\n\nOfficial-Commit: {published}", env=identity)
    git_output(layout.repo, "update-ref", f"{OFFICIAL_REFS}{published}", commit)
    git_output(layout.repo, "update-ref", RELEASE_REF, commit)
    return commit


def needs_new_shell(layout: Layout, name: str, shell_compat: object, base: str) -> str | None:
    """Why the installed shell cannot host an update, if so; a shell staged for the next restart does not count."""
    if paths.shell_hosts(layout, shell_compat, base, staged=False):
        return None
    if paths.shell_hosts(layout, shell_compat, staged=False):
        return f"{name} moves Python to {paths.base_minor(base)}, and the installed launcher is built for another line"
    return f"{name} needs a newer signed shell"
