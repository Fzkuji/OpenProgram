"""What ``openprogram upgrade`` does on a version store: read the pointer, fetch, apply.

The order keeps a bad server harmless:

1. Read ``channel.json`` and its signature (``channel``) and check them in
   memory; refuse a sequence lower than one seen before.
2. Read the layer manifests the pointer names, and work out what this machine
   lacks (``assemble.plan``). Nothing is written but verified objects.
3. Fetch the missing objects as byte ranges, each checked by its name.
4. When the update brings an App shell newer than the running App, assemble
   it beside the App and check its signature (``assemble.stage_shell``);
   Restart to update swaps it in. When that is all it brings (the code is
   here already), the update stops here, prepared. So it does when the code
   needs the new App and the update is not being prepared for a restart
   (``upgrade --prepare``): that code first runs in the new App, after the swap.
5. Hand the plan to the update controller, which assembles, builds, tries and
   switches (``control.official_update``).

A download that stops leaves its verified objects for the next run.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import importlib.metadata
from pathlib import Path

from openprogram.host._platform.runtime_layout import Layout, current_runtime, preparing_for_restart

from . import assemble, replay
from . import channel as channels
from .assemble import Plan
from .channel import Channel, Transport
from .repository import ReleaseError

SHELL_READY = "prepared"  # As ``control.official_update.PREPARED``: ready for Restart to update.
PROXY_HINT = "Check your connection (behind a proxy, set HTTPS_PROXY) and run `openprogram upgrade` again."
_OUTCOMES = {
    assemble.UP_TO_DATE: replay.UP_TO_DATE,
    assemble.WAITING_FOR_SHELL: replay.WAITING_FOR_SHELL,
    assemble.WAITING_FOR_RUNTIME: replay.WAITING_FOR_RUNTIME,
}


def installed_version() -> str | None:
    try:
        version = importlib.metadata.version("openprogram")
    except importlib.metadata.PackageNotFoundError:
        return None
    return version if channels.VERSION.fullmatch(version) else None


def kind(published: Channel) -> str:
    """``stable`` for an announced release, ``daily`` for a quiet publish."""
    return "stable" if published.announce else "daily"


@dataclass(frozen=True)
class Discovery:
    """What ``openprogram upgrade`` would install, found without downloading it.

    ``done`` is set when there is nothing to install now (up to date, or
    waiting for a shell or a runtime); otherwise ``plan`` says what to fetch.
    """

    transport: Transport
    published: Channel | None = None
    plan: Plan | None = None
    done: replay.ReplayResult | None = None

    @property
    def target(self) -> str:
        return self.published.name if self.published else ""

    @property
    def channel(self) -> str:
        return kind(self.published) if self.published else ""

    @property
    def latest_version(self) -> str:
        return self.published.version if self.published else ""

    def about(self) -> dict:
        """``channel``, ``latest_version`` and ``announce`` for the command's report."""
        if self.published is None:
            return {"channel": "", "latest_version": "", "announce": False}
        return {"channel": self.channel, "latest_version": self.latest_version, "announce": self.published.announce}

    def hold(self) -> dict:
        """The marks of an update prepared from this discovery and held for a restart."""
        return {"held": True, "target": self.target, **self.about()}


def _runtime_layout(layout: Layout | None) -> Layout:
    if layout is not None:
        return layout
    runtime = current_runtime()
    if runtime is None:
        raise ReleaseError("this installation does not run from the version store")
    return runtime.layout


def _days_since(moment: str) -> int:
    """How old the published pointer is: information only, an old one is still taken."""
    from datetime import datetime, timezone

    published = datetime.strptime(moment, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - published).days)


def discover(
    *, layout: Layout | None = None, transport: Transport | None = None, platform: str | None = None,
    trusted_keys: Iterable[str] | None = None,
) -> Discovery:
    """The published update and what applying it here takes."""
    layout = _runtime_layout(layout)
    transport = transport or channels.HttpTransport()
    published = channels.read(transport, trusted_keys=trusted_keys)
    if published is None:
        return Discovery(transport, done=replay.ReplayResult(
            outcome=replay.UP_TO_DATE, message="No update is published right now; the next check looks again."))
    channels.note_seen(layout, published)
    update = assemble.plan(layout, published, transport, platform=platform)
    if update.state != assemble.AVAILABLE:
        message = update.detail if update.state == assemble.UP_TO_DATE else f"{update.detail}. Nothing was changed."
        return Discovery(transport, published, update, replay.ReplayResult(
            outcome=_OUTCOMES[update.state], release=published.name, message=message))
    return Discovery(transport, published, update)


def check(
    *, layout: Layout | None = None, transport: Transport | None = None, platform: str | None = None,
    trusted_keys: Iterable[str] | None = None,
) -> dict:
    """What ``openprogram upgrade --check`` reports. Downloads only the pointer and layer manifests."""
    status = {
        "current_version": installed_version(), "latest_version": None, "update_available": False,
        "can_apply": False, "detail": "", "download_bytes": 0, "release_notes": "", "channel": "",
        "announce": False, "found_through": "updates",
    }
    found = discover(layout=layout, transport=transport, platform=platform, trusted_keys=trusted_keys)
    if found.published is None:
        status["detail"] = found.done.message if found.done else ""
        return status
    published = found.published
    status.update({
        "latest_version": published.version, "channel": kind(published), "announce": published.announce,
        "commit": published.build, "published_at": published.published_at, "sequence": published.sequence,
        "published_days_ago": _days_since(published.published_at),
        "release_notes": published.notes if published.announce else "",
    })
    state = found.plan.state if found.plan else assemble.UP_TO_DATE
    status["update_available"] = state != assemble.UP_TO_DATE
    status["can_apply"] = state == assemble.AVAILABLE
    status["detail"] = found.done.message if found.done and state != assemble.UP_TO_DATE else ""
    if found.plan is not None and state == assemble.AVAILABLE:
        status["download_bytes"] = found.plan.download_bytes
    return status


def update(
    *, layout: Layout | None = None, transport: Transport | None = None, platform: str | None = None,
    trusted_keys: Iterable[str] | None = None, say: Callable[[str], None] | None = None,
    found: Discovery | None = None, hold: bool = False,
    apply: Callable[[Plan, dict | None], replay.ReplayResult] | None = None,
) -> replay.ReplayResult:
    """Bring this installation to the published update, if it is behind.

    With ``hold`` the update is prepared and tried, then held in ``ready``
    for a restart instead of switched to (``official_update.activate_held``).
    """
    layout = _runtime_layout(layout)
    if found is None:
        found = discover(layout=layout, transport=transport, platform=platform, trusted_keys=trusted_keys)
    if found.done is not None:
        return found.done
    plan = found.plan
    assert plan is not None
    assemble.check_space(layout, plan)
    try:
        assemble.download(layout, plan, found.transport, say=say)
    except ReleaseError as exc:
        raise ReleaseError(f"The download stopped: {exc}. {PROXY_HINT} It resumes from where it stopped.") from exc
    except OSError as exc:
        raise ReleaseError(f"The download could not be saved: {exc}. Nothing was changed.") from exc
    try:
        assemble.stage_shell(layout, plan, found.transport)
    except OSError as exc:
        raise ReleaseError(f"The new App could not be assembled: {exc}. Nothing was changed.") from exc
    except ReleaseError as exc:
        raise ReleaseError(f"The new App could not be assembled: {exc}. Nothing was changed.") from exc
    if plan.shell_only or (plan.needs_swap and not hold):
        return replay.ReplayResult(outcome=SHELL_READY, release=plan.channel.name,
                                   message=f"The new App of {plan.channel.name} is ready; restart to update.")
    if apply is None:
        from openprogram.operations.self_update.control.official_update import apply_plan

        apply = lambda planned, marks: apply_plan(planned, hold=marks)  # noqa: E731
    if hold:
        with preparing_for_restart():
            result = apply(plan, found.hold())
    else:
        result = apply(plan, None)
    if result.outcome in (replay.ACTIVATED, replay.UP_TO_DATE, "prepared"):
        assemble.object_store(layout).prune(plan.keep())
    return result


def from_directory(directory: Path) -> Transport:
    """A local copy of the update files, for ``openprogram upgrade --from DIR``."""
    return channels.DirectoryTransport(directory)


__all__ = ["Discovery", "check", "discover", "from_directory", "installed_version", "kind", "update"]
