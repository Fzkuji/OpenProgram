"""Saving a key in the system's credential store, for an agent that reads its
key from there and not from a file (Zed).

After yetone/magpie ``internal/agent/zed_credential_other.go`` and
``zed_credential_windows.go`` (MIT License, Copyright (c) yetone):

* macOS: an Internet Password whose server is the URL and whose account is
  ``Bearer`` (``security add-internet-password -U``), the app's bundle and
  ``/usr/bin/security`` trusted so it can be updated on later picks;
* Linux: an item in the Secret Service's default collection with the
  attributes ``url`` and ``username`` and the label GPUI finds it by
  (``secret-tool store``);
* Windows: a generic credential ``zed:url=<url>`` for ``Bearer``, its blob
  UTF-8 (``CredWriteW``).

Under pytest nothing is saved unless a test asks (:data:`ALLOW_IN_TESTS`):
a test never writes the developer's own keychain.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Sequence

ALLOW_IN_TESTS = False


class CredentialError(Exception):
    """The credential store refused or isn't there."""


def _testing() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and not ALLOW_IN_TESTS


def save_url_password(url: str, account: str, secret: str, *, label: str = "",
                      trusted: Sequence[str] = (), windows_target: str = "") -> None:
    """Save ``secret`` for ``account`` at ``url``; :class:`CredentialError` when it can't be."""
    if _testing():
        return
    if sys.platform == "darwin":
        args = ["security", "add-internet-password", "-U"]
        for app in trusted:
            args += ["-T", app]
        args += ["-s", url, "-a", account, "-w", secret]
        _run(args)
    elif sys.platform == "win32":
        _cred_write(windows_target or url, account, secret)
    elif sys.platform.startswith(("linux", "freebsd")):
        args = ["secret-tool", "store", f"--label={label or url}", "url", url, "username", account]
        _run(args, stdin=secret)
    else:
        raise CredentialError(f"unsupported system credential store on {sys.platform}")


def _run(args: list[str], stdin: str | None = None) -> None:
    try:
        done = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CredentialError(f"{args[0]}: {exc}") from exc
    if done.returncode != 0:
        raise CredentialError(f"{args[0]}: {(done.stderr or done.stdout).strip() or done.returncode}")


def _cred_write(target: str, user: str, secret: str) -> None:
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]

    blob = secret.encode("utf-8")  # Zed's blob is UTF-8, not UTF-16
    buffer = ctypes.create_string_buffer(blob, len(blob))
    credential = CREDENTIAL(Type=1, TargetName=target, UserName=user,  # CRED_TYPE_GENERIC
                            CredentialBlobSize=len(blob),
                            CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)),
                            Persist=2)  # CRED_PERSIST_LOCAL_MACHINE
    if not ctypes.windll.advapi32.CredWriteW(ctypes.byref(credential), 0):  # type: ignore[attr-defined]
        raise CredentialError(f"CredWriteW: {ctypes.WinError()}")  # type: ignore[attr-defined]
