"""What every computer_use backend shares: its result types, its errors and
the small argument helpers, so the tool reads the same on each platform."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from . import audio
from .tree import Node

MAX_TEXT_CHARS = 5000
MAX_COPY_CHARS = 20_000
MAX_HOLD_SECONDS = 10.0
HOVER_SECONDS = 0.8  # long enough for tooltips and hover menus to open
CUE_LEAD_SECONDS = 0.15  # time for the App's agent cursor to reach a step's point
# A window whose tree has fewer elements than this shows little beyond its
# frame and title bar; its screenshot is read with OCR as well.
OCR_SPARSE_NODES = 12
KEEP_RECORDINGS = 20

INCLUDE = ("all", "tree", "screenshot")


class ComputerUseError(RuntimeError):
    """An action that cannot run; the message is shown to the model."""


class NotDispatched(ComputerUseError):
    """An action stopped before any input reached the app: its target was not found or not reachable.

    The app is as it was, so the operation's outcome is known: nothing
    happened. Other errors may come after input was posted.
    """


class StaleElement(NotDispatched):
    """A previously observed control vanished; return a fresh observation."""


class NotAnswering(NotDispatched):
    """The application answered nothing for a while: it is busy, so nothing more was sent to it.

    An app that is carrying out a long step, or one that has stopped
    responding, leaves every request waiting. Its windows read as missing
    and a step sent now would run whenever it comes back.
    """

    def __init__(self, app: str, seconds: float) -> None:
        self.app, self.seconds = app, seconds
        super().__init__(
            f"{app} has not answered for {seconds:.0f} s: it is busy (still carrying out its last step, or not "
            "responding). Nothing was sent, and its windows are still open. Wait (action wait), then try again.")


class Report(str):
    """An action result that read something and changed nothing, so no new state follows it."""


@dataclass
class Observation:
    text: str
    image: bytes | None
    app: str


@dataclass
class AudioClip:
    """A finished recording: the WAV on disk and what it holds."""
    app: str
    path: Path
    data: bytes
    seconds: float
    peak: float

    @property
    def silent(self) -> bool:
        return self.peak < audio.SILENCE_PEAK


class RefusedApps:
    """Which applications this call may not touch.

    The owner lists any in ``computer_use.refused_apps``. OpenProgram's own
    windows (``own``) are refused in a mode that shows approval cards, where
    the Agent could answer its own card.
    """

    def __init__(self, own: frozenset[str]) -> None:
        from openprogram.runtime.agent.turn_request import get_turn_request
        from openprogram.state.config import read_config
        listed = (read_config().get("computer_use") or {}).get("refused_apps")
        self.listed = {str(item).strip().lower() for item in listed} if isinstance(listed, list) else set()
        self.own = own
        self.mode = getattr(get_turn_request(), "permission_mode", None)

    def _own(self, keys: set[str]) -> bool:
        return bool(keys & self.own) and self.mode != "bypass"

    def hits(self, *names: str) -> bool:
        keys = {str(name).lower() for name in names if name}
        return bool(keys & self.listed) or self._own(keys)

    def refuse(self, label: str, *names: str) -> None:
        """Raise the standard refusal if any of ``names`` is off limits."""
        from openprogram.runtime.permissions import refusal
        keys = {str(name).lower() for name in names if name}
        if keys & self.listed:
            raise refusal.computer_use_refused_app(label)
        if self._own(keys):
            raise refusal.computer_use_own_window(self.mode or "this mode")


def refuse_password_field(what: str) -> None:
    """Raise the standard refusal when the owner has turned password fields off."""
    from openprogram.runtime.permissions import refusal
    from openprogram.state.config import read_config
    if (read_config().get("computer_use") or {}).get("refuse_password_fields") is True:
        raise refusal.computer_use_password_field(what)


def check_cancelled() -> None:
    try:
        from openprogram.runtime.conversation.run_control import check_cancelled as check
    except ImportError:
        return
    check()


def save_recording(app: str, data: bytes) -> Path:
    """Write a recording under the state directory, keeping the newest KEEP_RECORDINGS."""
    from openprogram.state.paths import get_state_dir

    folder = get_state_dir() / "computer-use" / "audio"
    folder.mkdir(parents=True, exist_ok=True)
    stem = "".join(c if c.isalnum() else "-" for c in app).strip("-").lower() or "app"
    path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}.wav"
    suffix = 1
    while path.exists():
        suffix += 1
        path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}-{suffix}.wav"
    path.write_bytes(data)
    for old in sorted(folder.glob("*.wav"), key=lambda p: p.stat().st_mtime)[:-KEEP_RECORDINGS]:
        old.unlink(missing_ok=True)
    return path


def window_node_count(nodes: list[Node]) -> int:
    """Elements of the window itself: the first walk, up to the next top-level node."""
    return next((i for i, node in enumerate(nodes[1:], 1) if node.depth == 0), len(nodes))


def seconds_arg(value, name: str) -> float:
    try:
        seconds = float(value or 0)
    except (TypeError, ValueError) as exc:
        raise ComputerUseError(f"{name} must be a number of seconds") from exc
    if not 0 <= seconds <= MAX_HOLD_SECONDS:
        raise ComputerUseError(f"{name} must be between 0 and {MAX_HOLD_SECONDS:g} seconds")
    return seconds


def menu_key(title: str) -> str:
    """Menu titles compare case-insensitively and without a trailing ellipsis."""
    title = " ".join(str(title).split()).lower()
    for tail in ("…", "..."):
        if title.endswith(tail):
            title = title[: -len(tail)].rstrip()
    return title


def click_words(button: str, count: int) -> str:
    verb = {"left": "clicked", "right": "right-clicked", "middle": "middle-clicked"}[button]
    if count == 1:
        return verb
    if button == "left":
        return {2: "double-clicked", 3: "triple-clicked"}[count]
    return f"{verb} {count} times"


def find_text(value: str, text: str, prefix: str = "", suffix: str = "") -> int | None:
    """Index of the first ``text`` in ``value`` preceded by ``prefix`` and followed by ``suffix``."""
    start = value.find(text)
    while start != -1:
        before = value[max(0, start - len(prefix)):start]
        after = value[start + len(text):start + len(text) + len(suffix)]
        if (not prefix or before == prefix) and (not suffix or after == suffix):
            return start
        start = value.find(text, start + 1)
    return None


def scroll_args(args: dict) -> tuple[str, float, float]:
    """``(direction, pages, pixels)`` of a scroll step, checked."""
    direction = str(args.get("direction") or "down").lower()
    direction = {"u": "up", "d": "down", "l": "left", "r": "right"}.get(direction, direction)
    if direction not in ("up", "down", "left", "right"):
        raise ComputerUseError("direction must be up, down, left or right")
    try:
        pages = float(args.get("pages") or 1)
    except (TypeError, ValueError) as exc:
        raise ComputerUseError("pages must be a number between 0 and 20") from exc
    if not 0 < pages <= 20:
        raise ComputerUseError("pages must be between 0 and 20")
    try:
        pixels = float(args.get("pixels") or 0)
    except (TypeError, ValueError) as exc:
        raise ComputerUseError("pixels must be a number") from exc
    if not 0 <= pixels <= 20_000:
        raise ComputerUseError("pixels must be between 0 and 20000")
    return direction, pages, pixels


def click_args(args: dict) -> tuple[str, int]:
    """``(button, count)`` of a click step, checked."""
    button = str(args.get("mouse_button") or "left").lower()
    button = {"l": "left", "r": "right", "m": "middle"}.get(button, button)
    if button not in ("left", "right", "middle"):
        raise ComputerUseError("mouse_button must be left, right or middle")
    try:
        count = int(args.get("click_count") or 1)
    except (TypeError, ValueError) as exc:
        raise ComputerUseError("click_count must be 1, 2 or 3") from exc
    if not 1 <= count <= 3:
        raise ComputerUseError("click_count must be 1, 2 or 3")
    return button, count


def menu_path(args: dict) -> list[str]:
    path = args.get("menu_path")
    if isinstance(path, str):
        path = [part for part in (p.strip() for p in path.replace("›", ">").split(">")) if part]
    return [str(p) for p in (path or [])]
