"""Window and screen geometry: screenshot pixels to screen points, and which windows cover which."""
from __future__ import annotations


def _intersect(a: tuple[float, float, float, float],
               b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """The overlap of two x, y, width, height rectangles (zero size when they do not meet)."""
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return left, top, max(0.0, right - left), max(0.0, bottom - top)


def screen_point(px: float, py: float, *, origin: tuple[float, float], scale: float) -> tuple[float, float]:
    """A pixel of a window's screenshot as a screen point.

    ``scale`` is the screenshot's pixels per point (2 on a Retina display,
    less when a large window was captured smaller); ``origin`` is the
    window's top left in screen points, top-left origin of the main display.
    """
    return origin[0] + px / scale, origin[1] + py / scale


def window_location(point: tuple[float, float], origin: tuple[float, float]) -> tuple[float, float]:
    """A screen point inside the window, measured from its top left, in points.

    A pointer event posted to one window carries this beside its screen
    location (``CGEventSetWindowLocation``), as KWWK computer-use-core and
    cua-driver set it. AppKit places the click from the screen location: a
    probe window took the click at the same point with either value here.
    """
    return point[0] - origin[0], point[1] - origin[1]


def live_frame(rows: list[dict], window_id: int) -> tuple[float, float, float, float] | None:
    """The window's frame now (x, y, width, height in points), from the window server's rows, if listed."""
    for row in rows:
        if int(row.get("kCGWindowNumber", 0)) == window_id:
            b = row.get("kCGWindowBounds") or {}
            if float(b.get("Width", 0)) > 0 and float(b.get("Height", 0)) > 0:
                return float(b.get("X", 0)), float(b.get("Y", 0)), float(b.get("Width", 0)), float(b.get("Height", 0))
    return None


def _bounds(row: dict) -> tuple[float, float, float, float]:
    b = row.get("kCGWindowBounds") or {}
    x, y = float(b.get("X", 0)), float(b.get("Y", 0))
    return x, y, x + float(b.get("Width", 0)), y + float(b.get("Height", 0))


def covering_rows(rows: list[dict], window_id: int, front_pid: int) -> list[dict]:
    """The windows above ``window_id`` that overlap it, deepest first, without the front app's.

    ``rows`` is the window server's on-screen list, front to back.
    """
    rows = [r for r in rows if r.get("kCGWindowLayer") == 0]
    ids = [int(r.get("kCGWindowNumber", 0)) for r in rows]
    if not window_id or window_id not in ids:
        return []
    target = _bounds(rows[ids.index(window_id)])
    above = [r for r in rows[:ids.index(window_id)] if int(r.get("kCGWindowOwnerPID", -1)) != front_pid]
    return [r for r in reversed(above) if (lambda b: b[0] < target[2] and target[0] < b[2]
                                           and b[1] < target[3] and target[1] < b[3])(_bounds(r))]


def _same_frame(a, b, tolerance: float = 3.0) -> bool:
    return all(abs(float(x) - float(y)) < tolerance for x, y in zip(a, b))


def panel_hosts(rows: list[dict], pid: int, services: set[int]) -> list[tuple[int, int, int, tuple]]:
    """The out-of-process file dialogs on screen for ``pid``, as (service pid,
    service window id, app window id, bounds as x, y, width, height).

    Such a dialog is two layer-0 windows with the same bounds: the panel
    service's, which holds its Accessibility tree and stays off screen, and
    the app's, which shows it on screen.
    """
    def frame(row):
        b = row.get("kCGWindowBounds") or {}
        return float(b.get("X", 0)), float(b.get("Y", 0)), float(b.get("Width", 0)), float(b.get("Height", 0))

    def sized(row):
        f = frame(row)
        return row.get("kCGWindowLayer") == 0 and f[2] >= 80 and f[3] >= 60

    hosts = [r for r in rows
             if int(r.get("kCGWindowOwnerPID", -1)) == pid and sized(r) and r.get("kCGWindowIsOnscreen")]
    found = []
    for row in rows:
        owner = int(row.get("kCGWindowOwnerPID", -1))
        if owner not in services or not sized(row):
            continue
        host = next((h for h in hosts if _same_frame(frame(h), frame(row))), None)
        if host is not None:
            found.append((owner, int(row.get("kCGWindowNumber", 0)), int(host.get("kCGWindowNumber", 0)), frame(row)))
    return found
