"""Decide how much of the installed App a candidate has to rebuild and restart.

The installed App records the commit it was built from in
``Contents/Resources/openprogram-source-revision``. Every path that differs
between that commit and the candidate maps to one component. A component
names what has to be rebuilt and the least disruptive way to bring it live:

* ``hot``: files the running processes read from disk per request (the Web
  export, the docs site, the terminal UI bundle). The bundle is swapped while
  the App and the worker keep running; open pages reload themselves.
* ``worker``: Python code. The worker restarts on the new bundle; the App
  window stays open and shows the updating page meanwhile.
* ``app``: the Electron shell. The App quits and reopens.

Anything the incremental build cannot reproduce exactly (dependency locks,
packaging scripts, icons, unknown paths) needs the complete package build,
which also restarts the App.
"""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import json
import re
from pathlib import Path
import subprocess


RESTART_LEVELS = ("hot", "worker", "app")
COMPONENTS = ("web", "docs", "tui", "python", "desktop")

# First match wins. ``None`` means the path does not ship in the App.
_RULES: tuple[tuple[str, str | None], ...] = (
    # Dependency inputs and packaging code change what a complete build makes.
    ("uv.lock", "full"),
    ("pyproject.toml", "full"),
    ("package.json", "full"),
    ("package-lock.json", "full"),
    ("apps/*/package.json", "full"),
    ("scripts/release/*", "full"),
    ("apps/desktop/scripts/*", "full"),
    ("apps/desktop/build/*", "full"),
    # Tests and repository metadata never ship.
    ("tests/*", None),
    ("apps/*/tests/*", None),
    ("references/*", None),
    ("examples/*", None),
    ("website/*", None),
    (".github/*", None),
    (".claude/*", None),
    (".gitignore", None),
    (".gitattributes", None),
    ("AGENTS.md", None),
    ("CITATION.cff", None),
    ("LICENSE", None),
    ("README.md", None),
    ("README.zh.md", None),
    ("apps/*/README.md", None),
    # Docs pages and the generator that renders them.
    ("docs/*", "docs"),
    ("scripts/docs_site/*", "docs"),
    # Shipped code.
    ("apps/web/*", "web"),
    ("apps/cli/*", "tui"),
    ("openprogram/*", "python"),
    ("setup.py", "python"),
    ("apps/desktop/*", "desktop"),
    # Other repository scripts are developer tools.
    ("scripts/*", None),
)

_RESTART_OF = {"web": "hot", "docs": "hot", "tui": "hot", "python": "worker", "desktop": "app"}
_REVISION = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class ActivationPlan:
    """What to rebuild, and the restart that brings it live."""

    scope: str  # "incremental" or "full"
    restart: str  # one of RESTART_LEVELS
    components: tuple[str, ...]
    installed_revision: str | None
    reason: str

    def to_dict(self) -> dict:
        return {
            "schema": 1,
            "scope": self.scope,
            "restart": self.restart,
            "components": list(self.components),
            "installed_revision": self.installed_revision,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ActivationPlan":
        if not isinstance(data, dict) or set(data) != {
            "schema", "scope", "restart", "components", "installed_revision", "reason",
        } or data["schema"] != 1:
            raise ValueError("malformed activation plan")
        plan = cls(
            scope=data["scope"],
            restart=data["restart"],
            components=tuple(data["components"]),
            installed_revision=data["installed_revision"],
            reason=data["reason"],
        )
        if (
            plan.scope not in ("incremental", "full")
            or plan.restart not in RESTART_LEVELS
            or any(item not in COMPONENTS for item in plan.components)
            or (plan.scope == "full" and plan.restart != "app")
            or (plan.scope == "incremental" and not plan.components)
            or not isinstance(plan.reason, str)
        ):
            raise ValueError("malformed activation plan")
        return plan


PLAN_FILE = "activation.json"


def load_plan(update_dir: Path) -> ActivationPlan | None:
    """The plan the supervisor recorded for this update, if it built one."""
    try:
        document = json.loads((update_dir / PLAN_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return ActivationPlan.from_dict(document)


def full_plan(reason: str, installed_revision: str | None = None) -> ActivationPlan:
    return ActivationPlan("full", "app", (), installed_revision, reason)


def component_of(path: str) -> str | None:
    """Return the component a repository path belongs to, ``"full"`` or ``None``."""
    for pattern, component in _RULES:
        if path == pattern or fnmatch.fnmatchcase(path, pattern):
            return component
    return "full"


def classify(paths: tuple[str, ...] | list[str], installed_revision: str | None) -> ActivationPlan:
    """Plan the activation for ``paths`` changed since ``installed_revision``."""
    if installed_revision is None:
        return full_plan("the installed App does not record a clean source revision")
    components: set[str] = set()
    for path in paths:
        component = component_of(path)
        if component == "full":
            return full_plan(f"{path} needs a complete package build", installed_revision)
        if component is not None:
            components.add(component)
    if "python" in components:
        # Reference pages are generated from the CLI parser and settings code.
        components.add("docs")
    if not components:
        # Nothing shipped changed; a hot swap still records the new revision.
        components.add("docs")
    ordered = tuple(name for name in COMPONENTS if name in components)
    restart = max((_RESTART_OF[name] for name in ordered), key=RESTART_LEVELS.index)
    return ActivationPlan("incremental", restart, ordered, installed_revision,
                          "only " + ", ".join(ordered) + " changed")


def installed_revision(app: Path) -> str | None:
    """The clean commit the installed App was built from, if it says."""
    marker = app / "Contents/Resources/openprogram-source-revision"
    try:
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 128:
            return None
        value = marker.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value if _REVISION.fullmatch(value) else None


def predict(app: Path, repo: Path, candidate_sha: str) -> ActivationPlan:
    """The plan the supervisor will choose, as far as the installed App says.

    The supervisor recomputes it at build time against the installer it froze.
    """
    installer = app / "Contents/Resources/update/install-app.sh"
    try:
        supports = b"--restart=" in installer.read_bytes()
    except OSError:
        supports = False
    if not supports:
        return full_plan("the installed App's installer predates partial restarts", installed_revision(app))
    return plan_for(app, repo, candidate_sha)


def plan_for(app: Path, repo: Path, candidate_sha: str, *, timeout: float = 10) -> ActivationPlan:
    """Diff the installed revision against the candidate in ``repo``."""
    revision = installed_revision(app)
    if revision is None:
        return classify((), None)
    if revision == candidate_sha:
        return full_plan("the installed App already runs this candidate", revision)
    environment = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
             "diff", "--name-only", "--no-renames", "-z", f"{revision}..{candidate_sha}"],
            cwd=str(repo), env=environment, capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return full_plan("the installed revision could not be compared", revision)
    if result.returncode != 0:
        return full_plan("the installed revision is not in this repository", revision)
    paths = [path for path in result.stdout.decode("utf-8", "surrogateescape").split("\0") if path]
    if any(any(ord(char) < 32 for char in path) for path in paths):
        return full_plan("a changed path has control characters", revision)
    return classify(paths, revision)
