"""PyPI / pip-wheel install update path.

Mirrors ``updater/git.py`` for the pip-installed case. Queries PyPI's
JSON endpoint for the latest version, compares against the locally
installed metadata, and shells out to ``pip install --upgrade
openprogram`` to apply.

The HTTP query uses stdlib ``urllib`` so the updater stays import-free
of optional third-party packages — auto-update must keep working even
when ``[all]`` extras aren't installed.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Optional

from openprogram.security import safe_http


PYPI_URL = "https://pypi.org/pypi/openprogram/json"
HTTP_TIMEOUT = 5.0  # seconds — auto-update must not hang the worker


def installed_version() -> Optional[str]:
    """Return the currently installed openprogram version, or None.

    Reads via ``importlib.metadata`` so it matches whatever pip / the
    wheel actually registered, not whatever pyproject.toml claims.
    """
    try:
        from importlib.metadata import version, PackageNotFoundError
    except ImportError:
        return None
    try:
        return version("openprogram")
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def latest_pypi_version() -> Optional[str]:
    """Query PyPI's JSON API for the latest non-yanked release.

    Returns None on any error (network, parse, missing field). Callers
    treat None as "no update info available", not as "we're up to date".
    """
    try:
        with safe_http.safe_client("updater.pip") as client:
            response = client.get(
                PYPI_URL,
                headers={"User-Agent": "openprogram-updater"},
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return None
    info = payload.get("info") or {}
    ver = info.get("version")
    if isinstance(ver, str) and ver:
        return ver
    return None


def _parse_version(v: str) -> tuple[int, ...]:
    """Coerce a version string into a comparable tuple.

    Best-effort PEP 440 subset: splits on ``.`` and parses leading
    digits per segment. ``1.2.3a1`` → ``(1, 2, 3)``. Pre-release ordering
    isn't a concern for our use case — auto-update only fires when a
    real release is published.
    """
    out: list[int] = []
    for part in v.split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def is_newer(current: str, target: str) -> bool:
    """True iff ``target`` is strictly newer than ``current``."""
    try:
        return _parse_version(target) > _parse_version(current)
    except Exception:
        return False


def apply() -> tuple[bool, str]:
    """Run ``pip install --upgrade openprogram`` against the running
    Python's interpreter. Returns ``(ok, message)``.

    Using ``sys.executable`` matters when the user installed via a
    Python that isn't the one currently on PATH (e.g. pipx-managed env,
    venv-shadowed system Python).
    """
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "openprogram",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "pip install timed out after 180s"
    except Exception as e:  # noqa: BLE001
        return False, f"pip install failed to start: {type(e).__name__}: {e}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").splitlines()[-3:]
        return False, "pip install failed: " + " | ".join(tail)
    return True, "pip install --upgrade openprogram succeeded"
