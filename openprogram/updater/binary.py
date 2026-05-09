"""Binary-distribution updater (placeholder for future Plan D).

When OpenProgram ships standalone PyInstaller / Nuitka binaries hosted
on a CDN, this module will:

  1. Query the manifest URL for the latest version + per-platform asset
  2. Download the asset for the current platform into a staging dir
  3. Verify checksum / signature
  4. Atomically swap the binary on next launch

For now everything is stubbed out so callers can branch on install type
without crashing.
"""
from __future__ import annotations

from typing import Optional


def check_for_update() -> Optional[dict]:
    """Stub. Will hit a manifest URL once binary distribution exists."""
    return None


def apply_update(_info: dict) -> tuple[bool, str]:
    return False, "binary auto-update not implemented yet"
