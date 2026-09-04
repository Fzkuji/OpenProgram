"""Recover an existing update from its saved runtime, independently of the App."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import stat
import sys
import tempfile
import time

from .store import SelfUpdateStore
from .types import TERMINAL_PHASES, _validate_update_id
from .verification_channel import _digest


def _agents_directory() -> Path:
    return Path.home() / "Library/LaunchAgents"


def _directory(path: Path, *, create=False, private=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if (path.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & (0o077 if private else 0o022)):
        raise ValueError("recovery directory is not safely owned by this user")


def _existing_state(store, update_id):
    # The general store lock creates/chmods its root; recovery must reject first.
    for path in (store.root, store.root / _validate_update_id(update_id)):
        _directory(path, private=True)


def _bytes(path: Path, mode: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != mode:
            raise ValueError("recovery file has unsafe type, owner or permissions")
        value = handle.read(65_537)
        if len(value) > 65_536:
            raise ValueError("recovery file is too large")
        return value


def _publish(path: Path, value: bytes, mode: int):
    if path.exists() or path.is_symlink():
        if _bytes(path, mode) != value:
            raise ValueError("existing recovery file differs from its binding")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".recovery-") as handle:
        os.fchmod(handle.fileno(), mode)
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
        try:
            os.link(handle.name, path)
        except FileExistsError:
            if _bytes(path, mode) != value:
                raise ValueError("recovery publication conflicts with an existing file") from None
        SelfUpdateStore._fsync_directory(path.parent)


def _expected(store, record, bundle):
    from .controller_bundle import controller_environment
    update_id = record.request.update_id
    directory = store.root / update_id
    script = directory / "recover.sh"
    label = f"ai.openprogram.self-update.recovery.{update_id}"
    plist = _agents_directory() / f"{label}.plist"
    command = ["/usr/bin/env", "-i", *(f"{k}={v}" for k, v in controller_environment().items()),
               str(bundle.python), "-I", "-B", "-m", "openprogram.self_update.bootstrap",
               "--state-root", str(store.root), "--installer-sha256", bundle.installer_sha256,
               update_id, "--mode"]
    script_bytes = (
        '#!/bin/sh\nset -eu\n[ "$#" -le 1 ] || exit 2\nmode=${1:-status}\n'
        'case "$mode" in status|repair|resume) ;; *) exit 2 ;; esac\nexec '
        + " ".join(map(shlex.quote, command)) + ' "$mode"\n'
    ).encode()
    plist_bytes = plistlib.dumps({
        "Label": label, "ProgramArguments": ["/bin/sh", str(script), "resume"],
        "RunAtLoad": True, "ProcessType": "Background",
        "StandardOutPath": str(directory / "bootstrap.log"),
        "StandardErrorPath": str(directory / "bootstrap.log"),
    }, sort_keys=True)
    binding = dict(schema=1, update_id=update_id, request_sha256=_digest(record.request.to_dict()),
                   installer_sha256=bundle.installer_sha256, runtime_sha256=bundle.runtime_sha256,
                   script_sha256=hashlib.sha256(script_bytes).hexdigest(),
                   plist_sha256=hashlib.sha256(plist_bytes).hexdigest(), plist_path=str(plist))
    return script, script_bytes, plist, plist_bytes, binding


def prepare_bootstrap(store, record, bundle):
    """Publish under the existing store lock, before the ordinary controller starts."""
    from .owner_repair import _owner
    from .controller_bundle import _probe_runtime
    _owner(store, record)
    _probe_runtime(store.root / record.request.update_id / "controller/runtime", bundle.python,
                   "openprogram.self_update.bootstrap")
    agents = _agents_directory()
    _directory(agents.parent, create=True)
    _directory(agents, create=True)
    script, body, plist, payload, binding = _expected(store, record, bundle)
    _publish(script, body, 0o700)
    _publish(plist, payload, 0o600)
    _publish(script.parent / "bootstrap.json", (json.dumps(binding, sort_keys=True) + "\n").encode(), 0o600)


def _finished(store, record):
    from .maintenance import load_maintenance
    marker = load_maintenance(store)
    return record.state.phase in TERMINAL_PHASES and (marker is None or marker["update_id"] != record.request.update_id)


def validate_bootstrap(store, record, bundle):
    from .owner_repair import _owner
    _owner(store, record)
    _directory(_agents_directory().parent)
    _directory(_agents_directory())
    script, body, plist, payload, binding = _expected(store, record, bundle)
    saved = json.loads(_bytes(script.parent / "bootstrap.json", 0o600))
    if saved != binding or type(saved.get("schema")) is not int or _bytes(script, 0o700) != body:
        raise ValueError("recovery entry does not match the original update")
    if plist.exists() or plist.is_symlink() or not _finished(store, record):
        if _bytes(plist, 0o600) != payload:
            raise ValueError("login recovery entry changed")
    return plist


def validate_if_present(store, record, bundle):
    directory = store.root / record.request.update_id
    # Older saved controllers predate bootstrap publication and remain resumable.
    if any(path.exists() or path.is_symlink() for path in (directory / "recover.sh", directory / "bootstrap.json")):
        validate_bootstrap(store, record, bundle)


def _error(store, update_id, exc):
    update_id = _validate_update_id(update_id)
    try:
        _existing_state(store, update_id)
        store._write_json(store.root / update_id / "bootstrap-error.json", {
            "schema": 1, "update_id": update_id, "at": time.time(), "error": (str(exc) or type(exc).__name__)[:1000],
        })
    except (OSError, ValueError):
        print("Self-update bootstrap: unable to persist diagnostic", file=sys.stderr)


def cleanup_bootstrap(store, update_id):
    """Remove only the matching completed update's login file, retaining evidence."""
    from .controller_bundle import _load_bundle
    update_id = _validate_update_id(update_id)
    try:
        _existing_state(store, update_id)
        with store._locked():
            directory = store.root / update_id
            binding = directory / "bootstrap.json"
            if not binding.exists() and not binding.is_symlink():
                return
            record = store._load_unlocked(update_id)
            if not _finished(store, record):
                return
            bundle = _load_bundle(directory / "controller")
            plist = validate_bootstrap(store, record, bundle)
            if plist.exists():
                plist.unlink()
                store._fsync_directory(plist.parent)
    except Exception as exc:
        _error(store, update_id, exc)


def main(argv=None) -> int:
    from .controller_bundle import _load_bundle
    from .supervisor import _canonical_store, run_supervisor
    parser = argparse.ArgumentParser(description="Use an update's original App-independent recovery entry")
    parser.add_argument("--state-root", required=True, type=Path)
    parser.add_argument("--installer-sha256", required=True)
    parser.add_argument("--mode", choices=("status", "repair", "resume"), default="status")
    parser.add_argument("update_id")
    args = parser.parse_args(argv)
    store = None
    record = None
    try:
        store = _canonical_store(args.state_root)
        _existing_state(store, args.update_id)
        read_only = args.mode == "status"
        with store._locked(read_only=read_only):
            record = store._load_unlocked(args.update_id, read_only=read_only)
            bundle = _load_bundle(store.root / args.update_id / "controller")
            if bundle.installer_sha256 != args.installer_sha256:
                raise ValueError("recovery installer identity changed")
            validate_bootstrap(store, record, bundle)
            finished = _finished(store, record)
            if read_only:
                from .owner_repair import _status_unlocked
                print(json.dumps(_status_unlocked(store, record), indent=2))
                return 0
        if args.mode == "repair":
            from openprogram.cli.commands.self_update import _cmd_self_update
            result = _cmd_self_update(argparse.Namespace(self_update_verb="repair", update_id=args.update_id))
        else:
            result = 0 if finished else run_supervisor(args.update_id, state_root=store.root,
                                                      installer_sha256=bundle.installer_sha256)
        cleanup_bootstrap(store, args.update_id)
        return result
    except Exception as exc:
        if args.mode != "status" and store is not None and record is not None:
            _error(store, record.request.update_id, exc)
        print(f"Self-update bootstrap: {str(exc) or type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
