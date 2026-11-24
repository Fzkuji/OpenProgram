"""The user's browsers OpenProgram can reach, and where each keeps its program and its profiles.

Every browser has one lowercase name, the ``app`` of
``computer_use(type="browser", app=...)``, and an engine that says how it is
reached:

- ``chromium``: through the Playwright Extension and Playwright MCP in
  extension mode (``extension.py``). Chrome and Edge, with their beta, dev
  and canary channels, go by the Playwright channel; Brave, Vivaldi, Opera,
  Arc and Chromium by their executable and their user-data folder.
- ``safari``: through AppleScript (``safari.py``), macOS only.
- ``firefox``: through Firefox's WebDriver BiDi remote agent (``firefox.py``).

Only files are read here; no browser is started or asked anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import glob
import os
from pathlib import Path
import shutil
import sys


@dataclass(frozen=True)
class Browser:
    """One browser: its name, what people call it, its engine and where it lives on each platform."""
    name: str
    label: str
    engine: str
    # The Playwright channel that finds the executable and the profile itself (chromium only), or "".
    channel: str = ""
    # The user-data folder by platform ("darwin", "win32", "linux"): relative to the home folder on macOS and
    # Linux, to %LOCALAPPDATA% on Windows unless it starts with "%APPDATA%". A pattern may hold a glob.
    data: dict[str, str] = field(default_factory=dict)
    # The executable by platform, the first that exists; a bare name is looked up on PATH.
    executables: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # How its windows are told apart: bundle identifiers on macOS, process names elsewhere.
    bundles: tuple[str, ...] = ()
    programs: tuple[str, ...] = ()


def _platform() -> str:
    return "win32" if sys.platform.startswith("win") else "darwin" if sys.platform == "darwin" else "linux"


def _mac(app: str, binary: str) -> tuple[str, ...]:
    return (f"/Applications/{app}.app/Contents/MacOS/{binary}", f"~/Applications/{app}.app/Contents/MacOS/{binary}")


_PROGRAM_FILES = ("%PROGRAMFILES%", "%PROGRAMFILES(X86)%", "%LOCALAPPDATA%")


def _win(relative: str) -> tuple[str, ...]:
    return tuple(f"{root}\\{relative}" for root in _PROGRAM_FILES)


BROWSERS: tuple[Browser, ...] = (
    Browser("chrome", "Google Chrome", "chromium", channel="chrome",
            data={"darwin": "Library/Application Support/Google/Chrome", "win32": "Google/Chrome/User Data",
                  "linux": ".config/google-chrome"},
            bundles=("com.google.Chrome",), programs=("chrome", "google-chrome", "google-chrome-stable", "google chrome")),
    Browser("chrome-beta", "Google Chrome Beta", "chromium", channel="chrome-beta",
            data={"darwin": "Library/Application Support/Google/Chrome Beta", "win32": "Google/Chrome Beta/User Data",
                  "linux": ".config/google-chrome-beta"},
            bundles=("com.google.Chrome.beta",), programs=("chrome", "google-chrome-beta", "google chrome beta")),
    Browser("chrome-dev", "Google Chrome Dev", "chromium", channel="chrome-dev",
            data={"darwin": "Library/Application Support/Google/Chrome Dev", "win32": "Google/Chrome Dev/User Data",
                  "linux": ".config/google-chrome-unstable"},
            bundles=("com.google.Chrome.dev",), programs=("chrome", "google-chrome-unstable", "google chrome dev")),
    Browser("chrome-canary", "Google Chrome Canary", "chromium", channel="chrome-canary",
            data={"darwin": "Library/Application Support/Google/Chrome Canary", "win32": "Google/Chrome SxS/User Data",
                  "linux": ".config/google-chrome-canary"},
            bundles=("com.google.Chrome.canary",), programs=("chrome", "google-chrome-canary", "google chrome canary")),
    Browser("edge", "Microsoft Edge", "chromium", channel="msedge",
            data={"darwin": "Library/Application Support/Microsoft Edge", "win32": "Microsoft/Edge/User Data",
                  "linux": ".config/microsoft-edge"},
            bundles=("com.microsoft.edgemac",), programs=("msedge", "microsoft-edge", "microsoft-edge-stable", "microsoft edge")),
    Browser("edge-beta", "Microsoft Edge Beta", "chromium", channel="msedge-beta",
            data={"darwin": "Library/Application Support/Microsoft Edge Beta", "win32": "Microsoft/Edge Beta/User Data",
                  "linux": ".config/microsoft-edge-beta"},
            bundles=("com.microsoft.edgemac.Beta",), programs=("msedge", "microsoft-edge-beta", "microsoft edge beta")),
    Browser("edge-dev", "Microsoft Edge Dev", "chromium", channel="msedge-dev",
            data={"darwin": "Library/Application Support/Microsoft Edge Dev", "win32": "Microsoft/Edge Dev/User Data",
                  "linux": ".config/microsoft-edge-dev"},
            bundles=("com.microsoft.edgemac.Dev",), programs=("msedge", "microsoft-edge-dev", "microsoft edge dev")),
    Browser("edge-canary", "Microsoft Edge Canary", "chromium", channel="msedge-canary",
            data={"darwin": "Library/Application Support/Microsoft Edge Canary", "win32": "Microsoft/Edge SxS/User Data",
                  "linux": ".config/microsoft-edge-canary"},
            bundles=("com.microsoft.edgemac.Canary",), programs=("msedge", "microsoft-edge-canary", "microsoft edge canary")),
    Browser("brave", "Brave", "chromium",
            data={"darwin": "Library/Application Support/BraveSoftware/Brave-Browser",
                  "win32": "BraveSoftware/Brave-Browser/User Data", "linux": ".config/BraveSoftware/Brave-Browser"},
            executables={"darwin": _mac("Brave Browser", "Brave Browser"),
                         "win32": _win("BraveSoftware\\Brave-Browser\\Application\\brave.exe"),
                         "linux": ("brave-browser", "brave", "/opt/brave.com/brave/brave")},
            bundles=("com.brave.Browser",), programs=("brave", "brave-browser", "brave browser")),
    Browser("vivaldi", "Vivaldi", "chromium",
            data={"darwin": "Library/Application Support/Vivaldi", "win32": "Vivaldi/User Data",
                  "linux": ".config/vivaldi"},
            executables={"darwin": _mac("Vivaldi", "Vivaldi"), "win32": _win("Vivaldi\\Application\\vivaldi.exe"),
                         "linux": ("vivaldi-stable", "vivaldi", "/opt/vivaldi/vivaldi")},
            bundles=("com.vivaldi.Vivaldi",), programs=("vivaldi", "vivaldi-bin", "vivaldi-stable")),
    # Opera keeps one profile in the user-data folder itself, with no Default folder.
    Browser("opera", "Opera", "chromium",
            data={"darwin": "Library/Application Support/com.operasoftware.Opera",
                  "win32": "%APPDATA%/Opera Software/Opera Stable", "linux": ".config/opera"},
            executables={"darwin": _mac("Opera", "Opera"),
                         "win32": _win("Programs\\Opera\\opera.exe") + _win("Opera\\opera.exe"),
                         "linux": ("opera", "/usr/lib/x86_64-linux-gnu/opera/opera")},
            bundles=("com.operasoftware.Opera",), programs=("opera",)),
    Browser("arc", "Arc", "chromium",
            data={"darwin": "Library/Application Support/Arc/User Data",
                  "win32": "Packages/TheBrowserCompany.Arc_*/LocalCache/Local/Arc/User Data"},
            executables={"darwin": _mac("Arc", "Arc"),
                         "win32": ("%LOCALAPPDATA%\\Microsoft\\WindowsApps\\Arc.exe",)},
            bundles=("company.thebrowser.Browser",), programs=("arc",)),
    Browser("chromium", "Chromium", "chromium",
            data={"darwin": "Library/Application Support/Chromium", "win32": "Chromium/User Data",
                  "linux": ".config/chromium"},
            executables={"darwin": _mac("Chromium", "Chromium"), "win32": _win("Chromium\\Application\\chrome.exe"),
                         "linux": ("chromium", "chromium-browser", "/snap/bin/chromium")},
            bundles=("org.chromium.Chromium",), programs=("chromium", "chromium-browser")),
    Browser("safari", "Safari", "safari",
            executables={"darwin": ("/Applications/Safari.app/Contents/MacOS/Safari",)},
            bundles=("com.apple.Safari",), programs=("safari",)),
    Browser("firefox", "Firefox", "firefox",
            executables={"darwin": _mac("Firefox", "firefox"), "win32": _win("Mozilla Firefox\\firefox.exe"),
                         "linux": ("firefox", "firefox-esr", "/usr/lib/firefox/firefox", "/snap/bin/firefox")},
            bundles=("org.mozilla.firefox",), programs=("firefox", "firefox-bin", "firefox-esr")),
)

BY_NAME: dict[str, Browser] = {browser.name: browser for browser in BROWSERS}
NAMES: tuple[str, ...] = tuple(BY_NAME)


def get(name: str) -> Browser:
    try:
        return BY_NAME[name]
    except KeyError:
        raise ValueError(f"browser is one of {', '.join(NAMES)}, not {name!r}") from None


def _expand(pattern: str) -> str:
    return os.path.expanduser(os.path.expandvars(pattern))


def data_dir(name: str) -> Path | None:
    """The user-data folder of ``name`` on this platform, or None where it keeps none."""
    relative = get(name).data.get(_platform())
    if not relative:
        return None
    if _platform() == "win32":
        if relative.startswith("%APPDATA%"):
            root = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
            relative = relative.removeprefix("%APPDATA%").lstrip("/\\")
        else:
            root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        path = root / relative
    else:
        path = Path.home() / relative
    if glob.has_magic(str(path)):
        found = sorted(glob.glob(str(path)))
        return Path(found[0]) if found else None
    return path


def executable(name: str) -> str:
    """The executable of ``name`` on this computer, or ""."""
    for candidate in get(name).executables.get(_platform(), ()):
        expanded = _expand(candidate)
        if os.sep not in expanded and "/" not in expanded:
            found = shutil.which(expanded)
            if found:
                return found
            continue
        if "%" in expanded:
            # An environment variable this computer does not set.
            continue
        if os.path.isfile(expanded):
            return expanded
    return ""


def profiles(root: Path) -> list[Path]:
    """The profile folders of a Chromium user-data folder: its sub-folders, and the folder itself (Opera)."""
    try:
        return [root, *(entry for entry in root.iterdir() if entry.is_dir())]
    except OSError:
        return []


__all__ = ["BROWSERS", "BY_NAME", "Browser", "NAMES", "data_dir", "executable", "get", "profiles"]
