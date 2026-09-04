"""Validate packaged reopen declarations without executing candidate code."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from .reopen import REOPEN_PROTOCOL

_ROLES = {"desktop", "installer", "runtime_manifest", "backend", "routes", "frontend"}


def _file(root: Path, relative: str) -> Path:
    if (not isinstance(relative, str) or not relative or len(relative) > 512
            or "\\" in relative or PurePosixPath(relative).is_absolute()
            or str(PurePosixPath(relative)) != relative or ".." in PurePosixPath(relative).parts):
        raise ValueError("invalid reopen protocol path")
    current = root
    for part in ("", *PurePosixPath(relative).parts):
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink in reopen protocol path")
    return current


def _read_or_hash(path: Path, *, limit: int, read: bool = False):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("invalid reopen protocol file")
        if read:
            result = handle.read(limit + 1)
            if len(result) > limit:
                raise ValueError("reopen protocol file exceeds size bound")
            return result
        digest = hashlib.sha256()
        size = 0
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            if size > limit:
                raise ValueError("reopen protocol file exceeds size bound")
            digest.update(chunk)
        return digest.hexdigest()


def validate_reopen_package(app: Path) -> dict:
    """A matching declaration permits preflight, not an installation verdict."""
    try:
        resources = _file(app, "Contents/Resources")
        data = json.loads(_read_or_hash(_file(resources, "update/reopen-protocol.json"), limit=16384, read=True))
        if (not isinstance(data, dict) or set(data) != {"schema", "protocol", "bindings"}
                or type(data["schema"]) is not int or data["schema"] != 1
                or type(data["protocol"]) is not int or data["protocol"] != REOPEN_PROTOCOL
                or not isinstance(data["bindings"], dict) or set(data["bindings"]) != _ROLES):
            raise ValueError("unsupported reopen protocol declaration")
        bindings = data["bindings"]
        for value in bindings.values():
            if (not isinstance(value, dict) or set(value) != {"path", "sha256"}
                    or not isinstance(value["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])):
                raise ValueError("invalid reopen protocol binding")
            actual = _read_or_hash(_file(resources, value["path"]), limit=512 * 1024 * 1024)
            if actual != value["sha256"]:
                raise ValueError("reopen protocol file changed after packaging")
        if any(bindings[role]["path"] != path for role, path in {
            "desktop": "app.asar", "installer": "update/install-app.sh",
            "runtime_manifest": "runtime/runtime-manifest.json",
        }.items()):
            raise ValueError("reopen protocol has unexpected fixed paths")
        backend = bindings["backend"]["path"]
        match = re.fullmatch(r"(runtime/(?:[A-Za-z0-9._-]+/)*lib/python\d+\.\d+/site-packages/)openprogram/self_update/reopen.py", backend)
        if not match:
            raise ValueError("invalid reopen backend package location")
        site = match[1]
        manifest = json.loads(_read_or_hash(_file(resources, "runtime/runtime-manifest.json"), limit=16384, read=True))
        if (not isinstance(manifest, dict) or manifest.get("schema") != 2
                or not isinstance(manifest.get("python"), str)):
            raise ValueError("invalid reopen runtime manifest")
        executable = "runtime/" + manifest["python"]
        _file(resources, executable)
        prefix = str(PurePosixPath(executable).parent.parent)
        if not site.startswith(prefix + "/lib/"):
            raise ValueError("reopen protocol does not describe the active Python prefix")
        if (bindings["routes"]["path"] != site + "openprogram_server/_webui/routes/self_updates.py"
                or not re.fullmatch(re.escape(site + "openprogram_server/_webui/_frontend/_next/static/chunks/")
                                    + r"[A-Za-z0-9._-]+\.js", bindings["frontend"]["path"])):
            raise ValueError("reopen protocol packages do not share a runtime")
        return data
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("App reopen protocol is missing, incompatible, or changed") from exc


def validate_ui_package(app: Path) -> dict:
    """Require packaged capture, backend and compiled renderer bindings."""
    reopen = validate_reopen_package(app)
    resources = _file(app, "Contents/Resources")
    try:
        data = json.loads(_read_or_hash(_file(resources, "update/ui-verification-protocol.json"), limit=16384, read=True))
        if (not isinstance(data, dict) or set(data) != {"schema", "protocol", "bindings"}
                or type(data["schema"]) is not int or data["schema"] != 1
                or type(data["protocol"]) is not int or data["protocol"] not in (1, 2)
                or not isinstance(data["bindings"], dict)
                or set(data["bindings"]) != ({"desktop", "backend", "routes", "frontend", "runtime_manifest"}
                    | ({"server", "owner_auth", "scroll_frontend"} if data["protocol"] == 2 else set()))):
            raise ValueError
        bindings = data["bindings"]
        for role in ("desktop", "routes", "runtime_manifest"):
            if bindings[role] != reopen["bindings"][role]:
                raise ValueError
        backend = reopen["bindings"]["backend"]["path"].removesuffix("reopen.py") + "ui_checks.py"
        prefix = backend.removesuffix("openprogram/self_update/ui_checks.py")
        if data["protocol"] == 2:
            if (bindings["server"]["path"] != prefix + "openprogram_server/server.py"
                    or bindings["owner_auth"]["path"] != prefix + "openprogram_server/_webui/owner_auth.py"
                    or not re.fullmatch(re.escape(prefix) + r"openprogram_server/_webui/_frontend/_next/static/chunks/[A-Za-z0-9._-]+\.js",
                                        bindings["scroll_frontend"]["path"])):
                raise ValueError
        if (bindings["backend"]["path"] != backend or not re.fullmatch(
                re.escape(prefix) + r"openprogram_server/_webui/_frontend/_next/static/chunks/[A-Za-z0-9._-]+\.js",
                bindings["frontend"]["path"])):
            raise ValueError
        for value in bindings.values():
            if (not isinstance(value, dict) or set(value) != {"path", "sha256"}
                    or _read_or_hash(_file(resources, value["path"]), limit=512 * 1024 * 1024) != value["sha256"]):
                raise ValueError
        return data
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("App UI verification protocol is missing, incompatible, or changed") from exc
