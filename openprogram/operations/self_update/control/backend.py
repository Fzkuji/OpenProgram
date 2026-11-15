"""Which way this installation activates a self-update.

``select`` is the one place that decides. The ``self_update`` tool asks it and
then calls the matching backend through this module, so the tool does not know
what a stable branch or a packaged App is.

| Backend      | When                                                  |
| ------------ | ----------------------------------------------------- |
| ``checkout`` | ``<state>/runtime/developer.json`` exists             |
| ``versions`` | this interpreter is inside ``<state>/runtime/versions`` |
| ``dev_stable`` | the source repository has a ``stable`` branch       |
| ``legacy_app`` | a packaged runtime and no such source repository    |
| ``checkout`` | a source checkout otherwise                           |
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

CHECKOUT = "checkout"
VERSIONS = "versions"
DEV_STABLE = "dev_stable"
LEGACY_APP = "legacy_app"
# Building a new base and running the trial can each take most of an hour.
VERSIONS_TIMEOUT_SECONDS = 6 * 3600


def packaged_runtime() -> bool:
    """Whether this worker runs from a packaged App or a release install."""
    from openprogram.operations.self_update.github_release.detect import managed_runtime_root

    return managed_runtime_root() is not None


class BackendUnavailable(RuntimeError):
    """This installation cannot activate a self-update; the message says why."""


def runtime_root() -> Path:
    from openprogram.state.paths import get_state_dir

    return Path(get_state_dir()) / "runtime"


def _known_source() -> Path | None:
    from openprogram.operations.worker import dev_instance

    try:
        return dev_instance.source_repo()
    except dev_instance.InstanceError:
        return None


def _has_stable_branch(source: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--verify", "--quiet", "refs/heads/stable^{commit}"],
        capture_output=True, text=True, timeout=10)
    return result.returncode == 0


def select(source: Path | None = None) -> str:
    """The backend for a candidate from ``source``, or the install's own source."""
    root = runtime_root()
    if (root / "developer.json").exists():
        return CHECKOUT
    if Path(sys.prefix).resolve().is_relative_to((root / "versions").resolve()):
        return VERSIONS
    repository = source if source is not None else _known_source()
    if repository is not None and _has_stable_branch(repository):
        return DEV_STABLE
    if packaged_runtime():
        return LEGACY_APP
    return CHECKOUT


def checkout_result() -> dict:
    """The tool's answer in a developer checkout: nothing is activated."""
    return {
        "backend": CHECKOUT,
        "activated": False,
        "message": (
            "This OpenProgram runs from a developer checkout, so self_update activates nothing. "
            "Commit the change in the checkout and restart the worker to run it."
        ),
    }


def _installed_app() -> Path:
    """The bundle this runtime came from, else where the App normally is."""
    from openprogram.operations.self_update.github_release.detect import managed_runtime_root

    root = managed_runtime_root()
    for parent in (root.parents if root is not None else ()):
        if parent.suffix == ".app":
            return parent
    return Path("/Applications/OpenProgram.app")


def _developer_id_signed(app: Path) -> bool:
    """Whether the App is the published one, which this machine cannot re-sign."""
    try:
        shown = subprocess.run(["/usr/bin/codesign", "--display", "--verbose=2", str(app)],
                               capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.startswith("Authority=Developer ID Application") for line in shown.stderr.splitlines())


def require_host(backend: str) -> None:
    """Refuse a backend this host cannot run; the macOS limit lives in legacy_app."""
    if backend == LEGACY_APP:
        from openprogram.host._platform.environment import conversational_update_backend

        if conversational_update_backend() is None:
            raise BackendUnavailable(
                "Conversational source self-update is not available on this host; "
                "its packaging, activation and recovery controller currently requires macOS. "
                "No update request was created. For a published version, use "
                "`openprogram upgrade` for the CLI or the Desktop release updater. "
                "Existing self-update status and cancellation remain available."
            )
        if sys.platform == "darwin" and _developer_id_signed(_installed_app()):
            raise BackendUnavailable(
                "This is the published OpenProgram App, and the Agent cannot rebuild and re-sign it. "
                "No update request was created. To get a new version, open Settings in the App and "
                "use Check for updates."
            )


def timeout_seconds(backend: str) -> int:
    """How long one update may take, end to end."""
    if backend == DEV_STABLE:
        from .dev_stable import TIMEOUT_SECONDS
        return TIMEOUT_SECONDS
    if backend == VERSIONS:
        return VERSIONS_TIMEOUT_SECONDS
    return 1800


def evidence(backend: str) -> tuple[str, ...]:
    """Lines of the immutable request that name its backend, when it matters."""
    if backend == DEV_STABLE:
        from .dev_stable import EVIDENCE
        return (EVIDENCE,)
    return ()


def launch(backend: str, store, update_id: str, *, worktree: Path) -> None:
    """Start the out-of-process controller for a saved request."""
    if backend == DEV_STABLE:
        from . import dev_stable
        dev_stable.submit(store, update_id, worktree=worktree)
        return
    if backend == VERSIONS:
        from .switch import launch as launch_version_controller
        launch_version_controller(update_id, store=store)
        return
    from openprogram.operations.self_update.delivery.launcher import launch_supervisor
    launch_supervisor(update_id)


__all__ = [
    "BackendUnavailable", "CHECKOUT", "DEV_STABLE", "LEGACY_APP", "VERSIONS",
    "checkout_result", "evidence", "launch", "packaged_runtime", "require_host",
    "runtime_root", "select", "timeout_seconds",
]
