"""Every module that reaches an App window says what it does when there is none.

A scheduled run, a check-in, a Goal's own turn and a chat-app message have no
window and often nobody watching; the App may not be running at all. Code that
needs a window was written for a chat a person opened, and a run with no
window used to find that out by failing. There are two ways to a window: the
desktop link (``host/resources/desktop_link``) and the interface-command
registry (``programs/_host/framework/interface``). The scan finds every module
that imports either, and each one is listed here with what a run with no
window gets from it:

``fallback``  the work is done another way
``refuses``   an answer at once that says what to use instead
``window``    the module only carries or answers what a window itself sent

A new module that reaches a window fails this test until it is listed, which
is the moment to decide. The same question for a person is one rule in one
place: a turn nobody answers is not given the question tool
(``runtime/permissions/policy.py::nobody_answers``).
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "openprogram"
GATEWAYS = ("openprogram.host.resources.desktop_link", "openprogram.programs._host.framework.interface")

WINDOW_USERS: dict[str, tuple[str, str]] = {
    "openprogram/host/resources/terminal.py":
        ("refuses", "a terminal pane lives in a window; the refusal names bash"),
    "openprogram/programs/applications/browser/tabs.py":
        ("fallback", "with no window to open a page in, the driver starts a browser of its own"),
    "openprogram/programs/applications/browser/service.py":
        ("fallback", "reads the App's tab when there is one; the driver's own tabs need no window"),
    "openprogram/programs/applications/browser/pages.py":
        ("window", "tells the windows showing a page that its record changed; no window, nothing to tell"),
    "openprogram/programs/applications/browser/video.py":
        ("refuses", "the App records its own window; the refusal says a window must be open"),
    "openprogram/runtime/conversation/surface_context.py":
        ("fallback", "browser_pages_available counts the driver's own browser; open_page reports "
                     "desktop_unavailable at once and the driver falls back"),
    "openprogram/runtime/conversation/process_runner.py":
        ("window", "bridges a Program's child process to the window its turn came from"),
    "openprogram/programs/tools/jobs/cron/worker.py":
        ("window", "names an open window to a scheduled run and bridges its child process to it; "
                   "with none open the run goes on without one"),
    "openprogram/interfaces/server/ws_actions/webtab.py":
        ("window", "the windows' own end of the link"),
    "openprogram/interfaces/server/ws_actions/interface.py":
        ("window", "the windows' own end of interface commands"),
    "openprogram/interfaces/server/server_runtime/websocket_dispatch.py":
        ("window", "forgets a window when its connection closes"),
    "openprogram/interfaces/server/routes/control/framework.py":
        ("refuses", "an interface command names one connected window or is refused; a run with no window "
                    "is refused before the request (programs/_host/framework/client.py)"),
    "openprogram/operations/self_update/verification/ui_checks.py":
        ("refuses", "an update's screen check needs the one main window and raises without it"),
}


def _module(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _imports(path: Path) -> set[str]:
    """Absolute names this module imports, at any depth, relative ones resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = _module(path).split(".")[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package[:len(package) - node.level + 1]
                base = ".".join([*parent, base] if base else parent)
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def _users() -> set[str]:
    users = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if _module(path) in GATEWAYS:
            continue
        if _imports(path) & set(GATEWAYS):
            users.add(relative)
    return users


def test_every_module_reaching_a_window_is_listed() -> None:
    users = _users()
    unlisted = sorted(users - set(WINDOW_USERS))
    assert not unlisted, (
        f"{unlisted} reach an App window. Decide what a run with no window gets from each "
        "(fallback, refuses, or window) and list it in WINDOW_USERS.")
    gone = sorted(set(WINDOW_USERS) - users)
    assert not gone, f"{gone} no longer reach an App window; remove them from WINDOW_USERS"


def test_each_entry_says_what_happens() -> None:
    for path, (kind, note) in WINDOW_USERS.items():
        assert kind in {"fallback", "refuses", "window"}, path
        assert len(note) > 20, path
