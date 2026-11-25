"""The driver of this computer's display, the screenshots a call takes, and which display a screenshot shows."""
from __future__ import annotations

from dataclasses import dataclass
import platform
import threading
from typing import Any


MAX_IMAGE_SIDE = 1568


@dataclass(frozen=True)
class Shot:
    """The last screenshot an agent took of one display: what its pixels map to."""
    display: Any
    width: int
    height: int

    @property
    def scale(self) -> float:
        """Screenshot pixels per screen point."""
        return self.width / self.display.size[0]

    def point(self, x: float, y: float) -> tuple[float, float]:
        """A pixel of the screenshot as a point on screen, top-left origin of the main display."""
        return (self.display.origin[0] + x * self.display.size[0] / self.width,
                self.display.origin[1] + y * self.display.size[1] / self.height)


# The last screenshot of each display, per agent: (agent, display id) -> Shot.
_shots: dict[tuple[str, int], Shot] = {}
_shots_lock = threading.Lock()


def _scale(display) -> float:
    """Pixels per point of a screenshot: the display's own density, smaller when its long side would pass the limit."""
    return min(display.backing, MAX_IMAGE_SIDE / max(display.size[0], display.size[1], 1.0))


def _driver_for(system: str):
    """The screen driver of this platform, or None where there is none."""
    if system == "Darwin":
        from ...desktop.macos import screen as screen_macos
        if not screen_macos.MacScreen.support()["available"]:
            return None
        return screen_macos.backend
    return None


# Thumbnails compared to tell whether a screenshot is of this computer's own display: small enough that
# scaling, compression and a moved pointer vanish, large enough that two different desktops differ.
_THUMB = (32, 20)
_SAME_DISPLAY = 12.0


def _gray(png: bytes) -> bytes | None:
    """A PNG scaled to a grey thumbnail, one byte a pixel; None where this computer cannot decode it."""
    try:
        import Quartz
        from Foundation import NSData
    except ImportError:
        return None
    width, height = _THUMB
    try:
        source = Quartz.CGImageSourceCreateWithData(NSData.dataWithBytes_length_(png, len(png)), None)
        image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None) if source is not None else None
        if image is None:
            return None
        context = Quartz.CGBitmapContextCreate(None, width, height, 8, width, Quartz.CGColorSpaceCreateDeviceGray(),
                                               Quartz.kCGImageAlphaNone)
        Quartz.CGContextSetInterpolationQuality(context, Quartz.kCGInterpolationHigh)
        Quartz.CGContextDrawImage(context, Quartz.CGRectMake(0, 0, width, height), image)
        return bytes(Quartz.CGBitmapContextGetData(context).as_buffer(width * height))
    except Exception:
        return None


def _alike(first: bytes, second: bytes) -> bool:
    return len(first) == len(second) and sum(abs(a - b) for a, b in zip(first, second)) / len(first) <= _SAME_DISPLAY


def shows_this_display(png: bytes) -> bool | None:
    """Whether a screenshot is of one of this computer's own displays.

    None when this computer's displays cannot be read: no driver here, no
    Screen Recording grant (nothing asks for one), or an image that cannot
    be decoded. Each display is captured once, at thumbnail size.
    """
    factory = _driver_for(platform.system())
    if factory is None:
        return None
    try:
        driver = factory()
        if not driver.grants().get("screen_recording"):
            return None
        theirs = _gray(png)
        if theirs is None:
            return None
        for display in driver.displays():
            ours = _gray(driver.capture(display, _THUMB[0] * 2 / max(display.size[0], 1.0)))
            if ours is None:
                return None
            if _alike(theirs, ours):
                return True
    except Exception:
        return None
    return False
