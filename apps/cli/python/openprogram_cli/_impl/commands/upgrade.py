"""Explicit stable-Release or source-checkout upgrades."""
from __future__ import annotations

import importlib.metadata
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Optional


# ---------------------------------------------------------------- channels

# channel name -> (git remote, ref). One built-in channel today; adding
# another is a line here plus nothing else (see §4.4).
CHANNELS: dict[str, tuple[str, str]] = {
    "stable": ("origin", "main"),
}

DEFAULT_CHANNEL = "stable"
CONFIG_KEY = "update.channel"
PRODUCT_REPOSITORY = "Fzkuji/OpenProgram"
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class UpgradeError(Exception):
    """Aborts the step chain. ``reason`` is the structured failure code."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def resolve_channel(name: Optional[str] = None) -> tuple[str, str, str]:
    """Resolve a channel to ``(channel_name, remote, ref)``.

    ``name`` wins; otherwise the persisted ``update.channel`` setting;
    otherwise the default. An unknown name is an error naming the known
    ones — never a silent fallback to stable.
    """
    if name is None:
        name = _configured_channel()
    if name not in CHANNELS:
        known = ", ".join(sorted(CHANNELS))
        raise UpgradeError(
            "unknown-channel",
            f"unknown channel {name!r} — known channels: {known}",
        )
    remote, ref = CHANNELS[name]
    return name, remote, ref


def _configured_channel() -> str:
    try:
        from openprogram.setup import _read_config
        value = ((_read_config().get("update") or {}).get("channel"))
    except Exception:
        return DEFAULT_CHANNEL
    return value if isinstance(value, str) and value else DEFAULT_CHANNEL


def _version_parts(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(value)
    if match is None:
        raise UpgradeError("invalid-version", f"invalid release version: {value!r}")
    return tuple(int(part) for part in match.groups())


def _installed_version() -> str:
    try:
        return importlib.metadata.version("openprogram")
    except importlib.metadata.PackageNotFoundError as exc:
        raise UpgradeError(
            "unknown-version",
            "cannot read the installed OpenProgram version",
        ) from exc


def _platform_runtime_names(version: str) -> tuple[str, str]:
    from openprogram import _compat

    target = _compat.managed_release_target()
    if target is None:
        import platform

        raise UpgradeError(
            "unsupported-platform",
            "managed releases do not support "
            f"{platform.system()} {platform.machine()}",
        )
    platform_name, arch, suffix, _installer_name = target
    archive = f"OpenProgram-{version}-runtime-{platform_name}-{arch}{suffix}"
    return archive, f"{archive}.sha256"


def _managed_release_status() -> dict[str, Any]:
    from openprogram.updater import github

    current = _installed_version()
    _version_parts(current)
    release = github.latest_release()
    if release is None:
        raise UpgradeError(
            "release-unavailable",
            "cannot read the latest stable GitHub Release",
        )
    target = release["tag_name"][1:]
    _version_parts(target)
    archive, checksum = _platform_runtime_names(target)
    assets: dict[str, dict] = {}
    for asset in release["assets"]:
        if (
            not isinstance(asset, dict)
            or not isinstance(asset.get("name"), str)
            or not isinstance(asset.get("size"), int)
            or asset["size"] < 0
            or asset["name"] in assets
        ):
            raise UpgradeError("incomplete-release", "latest Release asset metadata is invalid")
        assets[asset["name"]] = asset
    required = ("release-manifest.json", archive, checksum)
    missing = [name for name in required if name not in assets]
    if missing:
        raise UpgradeError(
            "incomplete-release",
            "latest Release lacks the complete runtime asset(s): "
            + ", ".join(missing),
        )
    manifest = github.release_manifest(target)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != 1
        or manifest.get("version") != target
        or not isinstance(manifest.get("files"), list)
    ):
        raise UpgradeError("incomplete-release", "latest Release manifest is invalid")
    files: dict[str, dict] = {}
    for item in manifest["files"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise UpgradeError("incomplete-release", "latest Release manifest entry is invalid")
        name = PurePosixPath(item["path"].replace("\\", "/")).name
        if (
            not name
            or name in files
            or not isinstance(item.get("bytes"), int)
            or item["bytes"] < 0
            or re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", ""))) is None
        ):
            raise UpgradeError("incomplete-release", "latest Release manifest entry is invalid")
        files[name] = item
    for name in (archive, checksum):
        if name not in files or files[name]["bytes"] != assets[name]["size"]:
            raise UpgradeError(
                "incomplete-release",
                f"latest Release manifest does not match {name}",
            )
    return {
        "current_version": current,
        "latest_version": target,
        "update_available": _version_parts(target) > _version_parts(current),
        "archive": archive,
    }


def run_managed_release_upgrade(
    *,
    check_only: bool,
    as_json: bool,
    dry_run: bool = False,
) -> int:
    from openprogram.updater import github

    try:
        status = _managed_release_status()
        if check_only or not status["update_available"]:
            if as_json:
                print(json.dumps({"ok": True, **status, "dry_run": dry_run}, indent=2))
            else:
                print(f"  current        {status['current_version']}")
                print(f"  latest         {status['latest_version']}")
                print(
                    "  update         "
                    + ("available" if status["update_available"] else "up to date")
                )
            return 0

        if dry_run:
            payload = {
                "ok": True,
                **status,
                "dry_run": True,
                "planned": [
                    "download-versioned-installer",
                    "verify-complete-runtime",
                    "activate-current",
                ],
            }
            if as_json:
                print(json.dumps(payload, indent=2))
            else:
                print(f"  current        {status['current_version']}")
                print(f"  latest         {status['latest_version']}")
                print("  dry run        no files or processes changed")
            return 0

        target = status["latest_version"]
        from openprogram import _compat

        installer_name = (
            "install-release.ps1"
            if status["archive"].endswith(".zip")
            else "install-release.sh"
        )
        installer = (
            github.release_installer(target, script_name=installer_name)
            if installer_name.endswith(".ps1")
            else github.release_installer(target)
        )
        if installer is None:
            raise UpgradeError(
                "installer-unavailable",
                f"cannot read the versioned installer for v{target}",
            )
        installer_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f"openprogram-{target}-installer-",
                suffix=Path(installer_name).suffix,
                delete=False,
            ) as temporary:
                temporary.write(installer)
                installer_path = temporary.name
            if installer_name.endswith(".sh"):
                os.chmod(installer_path, 0o700)
            allowed_environment = {
                "PATH", "HOME", "TMPDIR", "TMP", "TEMP",
                "USERPROFILE", "LOCALAPPDATA", "APPDATA", "SystemRoot",
                "SYSTEMROOT", "WINDIR", "ComSpec", "COMSPEC", "PATHEXT",
                "LANG", "LC_ALL", "LC_CTYPE",
                "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
                "https_proxy", "http_proxy", "all_proxy", "no_proxy",
                "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
                "XDG_STATE_HOME", "OPENPROGRAM_STATE_DIR", "OPENPROGRAM_BIN_DIR",
            }
            env = {
                key: value
                for key, value in os.environ.items()
                if key in allowed_environment
            }
            env["OPENPROGRAM_VERSION"] = target
            env["OPENPROGRAM_REPOSITORY"] = PRODUCT_REPOSITORY
            command = _compat.release_installer_command(installer_path)
            try:
                completed = subprocess.run(
                    command,
                    env=env,
                    capture_output=as_json,
                    text=as_json,
                )
            except FileNotFoundError:
                fallback = _compat.release_installer_fallback_command(
                    installer_path
                )
                if fallback is None:
                    raise
                completed = subprocess.run(
                    fallback,
                    env=env,
                    capture_output=as_json,
                    text=as_json,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            raise UpgradeError(
                "installer-execution-failed",
                f"cannot execute the versioned installer: {type(exc).__name__}",
            ) from exc
        finally:
            if installer_path:
                try:
                    Path(installer_path).unlink(missing_ok=True)
                except OSError:
                    pass
        if completed.returncode != 0:
            raise UpgradeError(
                "installer-failed",
                f"versioned installer exited {completed.returncode}; "
                "current remains unchanged"
                + (
                    f": {(completed.stderr or completed.stdout or '').strip()[-1000:]}"
                    if as_json and (completed.stderr or completed.stdout)
                    else ""
                ),
            )
        if as_json:
            print(json.dumps({"ok": True, **status, "activated": target}, indent=2))
        else:
            print(f"\nOpenProgram {target} is now the active CLI runtime.")
            print("The existing worker is still running its previous version.")
            print("Restart it when ready:")
            print("  openprogram worker restart")
        return 0
    except UpgradeError as exc:
        if as_json:
            print(json.dumps({
                "ok": False,
                "reason": exc.reason,
                "detail": exc.detail,
            }))
        else:
            print(f"{exc.reason}: {exc.detail}")
        return 1


def persist_channel(name: str) -> None:
    """Save ``--channel`` as the new default. Validates first so a typo
    can never persist a channel that `upgrade` would then refuse."""
    resolve_channel(name)
    from openprogram.config_schema import set_setting
    set_setting(CONFIG_KEY, name)


# --------------------------------------------------------------- sentinel

# One file, overwritten at every step. The worker reads it on boot so the
# first chat turn after an upgrade can report what happened (phase 3);
# today it is the record you check when an upgrade dies mid-flight.
def _sentinel_path() -> Path:
    from openprogram.paths import ensure_state_dir
    return ensure_state_dir() / "upgrade-state.json"


def _write_sentinel(payload: dict) -> None:
    try:
        path = _sentinel_path()
        payload = {**payload, "updated_at": time.time()}
        path.write_text(json.dumps(payload, indent=2))
    except Exception:
        pass  # ponytail: progress reporting must never fail an upgrade


# ------------------------------------------------------------------- git


def repo_root() -> Path:
    """The git checkout containing the *installed* package.

    For the editable install that OpenProgram ships as, ``openprogram/``
    and ``.git`` are siblings. A non-checkout install cannot self-update
    through git.
    """
    import openprogram
    root = Path(openprogram.__file__).resolve().parents[1]
    if not (root / ".git").exists():
        raise UpgradeError(
            "not-a-checkout",
            f"{root} is not a git checkout — `upgrade` needs a source install",
        )
    return root


def _git(root: Path, *args: str, check: bool = True) -> str:
    res = subprocess.run(
        ["git", *args], cwd=str(root),
        capture_output=True, text=True,
    )
    if check and res.returncode != 0:
        raise UpgradeError(
            "git-failed",
            f"git {' '.join(args)} failed: {(res.stderr or res.stdout).strip()}",
        )
    return (res.stdout or "").strip()


def _head_sha(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD")


def _is_ancestor(root: Path, older: str, newer: str) -> bool:
    res = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer],
        cwd=str(root), capture_output=True, text=True,
    )
    return res.returncode == 0


def _changed_files(root: Path, old: str, new: str) -> list[str]:
    return [ln for ln in _git(root, "diff", "--name-only", old, new).splitlines() if ln]


# ------------------------------------------------------------- step chain


class _Steps:
    """Collects ``{name, ok, detail, duration_s}`` in order, printing live."""

    def __init__(self, quiet: bool, sentinel: Optional[dict] = None):
        self.rows: list[dict] = []
        self.quiet = quiet
        self.sentinel = sentinel

    def record(self, name: str, ok: bool, detail: str, started: float) -> None:
        row = {
            "name": name,
            "ok": ok,
            "detail": detail,
            "duration_s": round(time.monotonic() - started, 3),
        }
        self.rows.append(row)
        if not self.quiet:
            mark = "OK  " if ok else "FAIL"
            print(f"  [{mark}] {name:<10} {detail}")
        if self.sentinel is not None:
            _write_sentinel({**self.sentinel, "steps": self.rows,
                             "current_step": name})


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _poll_backend_identity(
    port: int,
    timeout: float,
    expected_revision: Optional[str] = None,
) -> tuple[bool, str]:
    """Wait for a token-HMAC listener proof and optional revision match."""
    from openprogram._ports import backend_is_ours

    deadline = time.monotonic() + timeout
    last = "no response"
    while time.monotonic() < deadline:
        identified = backend_is_ours(
            port,
            expected_revision=expected_revision,
        )
        if identified is True and expected_revision is None:
            return True, "healthy"
        if identified is True:
            return True, f"serving {expected_revision[:12]}"
        last = "listener ownership or revision proof not ready"
        time.sleep(0.5)
    return False, f"timed out after {timeout:.0f}s — {last}"


def _cold_start_probe(root: Path, target_sha: str) -> str:
    """Boot the new code in an isolated profile on a scratch port.

    Catches import errors, config-schema breaks and port-binding failures
    before the real instance is touched. Also runs the doctor checks in a
    *subprocess* — the running CLI already imported the old modules, so
    an in-process run would grade the wrong code.
    """
    env = dict(os.environ)
    port = _free_port()
    env["OPENPROGRAM_PROFILE"] = "upgrade-probe"
    env["OPENPROGRAM_WEB_PORT"] = str(port)

    child = subprocess.Popen(
        [sys.executable, "-m", "openprogram.cli", "worker", "run"],
        cwd=str(root), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        ok, detail = _poll_backend_identity(port, timeout=60.0)
        if not ok:
            raise UpgradeError("probe-failed", f"cold start on :{port} {detail}")
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

    doctor = subprocess.run(
        [sys.executable, "-c",
         "import json;from openprogram.cli.commands.doctor import run_checks;"
         "print(json.dumps(run_checks()))"],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=120,
    )
    if doctor.returncode != 0:
        raise UpgradeError(
            "probe-failed",
            f"doctor checks could not run: {(doctor.stderr or '').strip()[:300]}",
        )
    try:
        results = json.loads(doctor.stdout)
    except ValueError:
        raise UpgradeError("probe-failed", "doctor produced no parseable output")
    # The worker-port check legitimately fails inside the probe profile
    # (the probe worker is already stopped), so it is not a gate.
    blocking = [r for r in results
                if not r["ok"] and not r["label"].startswith("worker on")]
    if blocking:
        names = ", ".join(r["label"] for r in blocking)
        raise UpgradeError("probe-failed", f"doctor check(s) failed: {names}")
    return f"cold start ok on :{port}, {len(results)} doctor check(s)"


# ------------------------------------------------------------------- run


def run_upgrade(*, channel: Optional[str] = None, dry_run: bool = False,
                as_json: bool = False, no_restart: bool = False,
                yes: bool = False) -> int:
    result: dict[str, Any] = {
        "ok": False, "steps": [],
        "from_sha": "", "to_sha": "", "reason": "",
    }
    # A dry run reports a plan; it writes nothing anywhere, sentinel included.
    steps = _Steps(quiet=as_json, sentinel=None if dry_run else result)
    result["steps"] = steps.rows

    def finish(ok: bool, reason: str = "") -> int:
        result["ok"] = ok
        result["reason"] = reason
        if not dry_run:
            _write_sentinel({**result, "current_step": "done"})
        if as_json:
            print(json.dumps(result, indent=2))
        elif reason and not ok:
            print(f"\nupgrade failed: {reason}")
        return 0 if ok else 1

    try:
        name, remote, ref = resolve_channel(channel)
        root = repo_root()

        # 1. preflight — resolve target, refuse to touch a dirty tree.
        started = time.monotonic()
        if _git(root, "status", "--porcelain"):
            raise UpgradeError(
                "dirty-worktree",
                f"{root} has uncommitted changes — commit or stash them first",
            )
        current = _head_sha(root)
        result["from_sha"] = current
        _git(root, "fetch", remote, ref)
        target = _git(root, "rev-parse", "FETCH_HEAD")
        result["to_sha"] = target
        if target == current:
            steps.record("preflight", True, f"already at {current[:12]}", started)
            return finish(True, "already-up-to-date")
        if _is_ancestor(root, target, current) and not yes:
            raise UpgradeError(
                "downgrade-needs-confirmation",
                f"{target[:12]} is older than HEAD {current[:12]} — pass --yes",
            )
        steps.record("preflight", True,
                     f"{name} → {remote}/{ref}: {current[:12]} → {target[:12]}",
                     started)

        if dry_run:
            planned = ["checkout", "deps", "build", "probe"]
            if not no_restart:
                planned += ["restart", "verify"]
            for step in planned:
                steps.record(step, True, "planned (dry run)", time.monotonic())
            return finish(True, "dry-run")

        # 2. checkout — fast-forward only; never force.
        started = time.monotonic()
        res = subprocess.run(
            ["git", "merge", "--ff-only", "FETCH_HEAD"],
            cwd=str(root), capture_output=True, text=True,
        )
        if res.returncode != 0:
            # Detached HEAD or a diverged branch: a plain checkout of the
            # target sha is still correct and still non-destructive.
            checkout = subprocess.run(
                ["git", "checkout", target],
                cwd=str(root), capture_output=True, text=True,
            )
            if checkout.returncode != 0:
                raise UpgradeError(
                    "checkout-failed",
                    f"cannot fast-forward and cannot check out {target[:12]}: "
                    f"{(res.stderr or '').strip()}",
                )
        steps.record("checkout", True, f"at {target[:12]}", started)

        # 3. deps — only when the manifests actually moved.
        started = time.monotonic()
        changed = _changed_files(root, current, target)
        done: list[str] = []
        if any(f in ("pyproject.toml", "setup.py") for f in changed):
            _run_or_fail([sys.executable, "-m", "pip", "install", "-e", "."],
                         root, "deps-failed")
            done.append("pip install -e .")
        if "package-lock.json" in changed:
            _run_or_fail(
                [
                    "npm", "ci", "--include-workspace-root", "--ignore-scripts",
                ],
                root,
                "deps-failed",
                node_tool=True,
            )
            done.append("npm ci (frontend workspaces)")
        steps.record("deps", True, ", ".join(done) or "unchanged", started)

        # 4. build — only the frontend workspaces that changed.
        started = time.monotonic()
        builds: list[str] = []
        if "package-lock.json" in changed or any(
            f.startswith("apps/web/") for f in changed
        ):
            _run_or_fail(
                ["npm", "run", "build", "--workspace", "apps/web"],
                root,
                "build-failed",
                node_tool=True,
            )
            builds.append("apps/web")
        if "package-lock.json" in changed or any(
            f.startswith("apps/cli/") for f in changed
        ):
            _run_or_fail(
                ["npm", "run", "build", "--workspace", "apps/cli"],
                root,
                "build-failed",
                node_tool=True,
            )
            builds.append("apps/cli")
        detail = f"npm run build ({', '.join(builds)})" if builds else "unchanged"
        steps.record("build", True, detail, started)

        # 5. probe — cold-start the new code in an isolated profile.
        started = time.monotonic()
        steps.record("probe", True, _cold_start_probe(root, target), started)

        if no_restart:
            return finish(True, "restart-skipped")

        # 6. restart — the existing worker restart, unchanged.
        started = time.monotonic()
        from openprogram import worker as _worker
        rc = _worker.restart_worker()
        if rc != 0:
            raise UpgradeError("restart-failed", f"restart exited {rc}")
        steps.record("restart", True, "worker restarted", started)

        # 7. verify — the real instance must report the new sha.
        started = time.monotonic()
        from openprogram.worker.lifecycle import resolve_worker_port
        port = resolve_worker_port()
        ok, detail = _poll_backend_identity(
            port,
            timeout=90.0,
            expected_revision=target,
        )
        steps.record("verify", ok, detail, started)
        if not ok:
            if not as_json:
                print("\nThe restarted instance is not serving the new code. "
                      "Roll back manually with:")
                print(f"  git -C {root} checkout {current} && openprogram restart")
            result["reason"] = "verify-failed"
            return finish(False, "verify-failed")

        if not as_json:
            print(f"\nupgraded to {target[:12]} ({name}).")
        return finish(True, "")

    except UpgradeError as e:
        if not as_json:
            print(f"  [FAIL] {e.reason}: {e.detail}")
        return finish(False, e.reason)


def _run_or_fail(
    cmd: list[str], cwd: Path, reason: str, *, node_tool: bool = False
) -> None:
    if node_tool:
        from openprogram._compat import node_tool_cmd

        cmd = node_tool_cmd(cmd)
    res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if res.returncode != 0:
        tail = (res.stderr or res.stdout or "").strip()[-500:]
        raise UpgradeError(reason, f"{' '.join(cmd)} failed: {tail}")


def run_status(*, channel: Optional[str] = None, as_json: bool = False) -> int:
    try:
        name, remote, ref = resolve_channel(channel)
        root = repo_root()
        current = _head_sha(root)
        _git(root, "fetch", remote, ref)
        target = _git(root, "rev-parse", "FETCH_HEAD")
    except UpgradeError as e:
        if as_json:
            print(json.dumps({"ok": False, "reason": e.reason, "detail": e.detail}))
        else:
            print(f"{e.reason}: {e.detail}")
        return 1

    available = target != current
    if as_json:
        print(json.dumps({
            "ok": True, "channel": name, "remote": remote, "ref": ref,
            "head_sha": current, "target_sha": target,
            "update_available": available,
        }, indent=2))
        return 0
    print(f"  channel        {name} ({remote}/{ref})")
    print(f"  head           {current}")
    print(f"  target         {target}")
    print(f"  update         {'available' if available else 'up to date'}")
    return 0


def _cmd_upgrade(args) -> int:
    from openprogram.updater.detect import InstallMethod, detect_install_method

    channel = getattr(args, "channel", None)
    as_json = bool(getattr(args, "json", False))
    method = detect_install_method()
    check_only = bool(
        getattr(args, "check", False)
        or getattr(args, "upgrade_verb", None) == "status"
    )
    if method is InstallMethod.MANAGED_RELEASE:
        if channel not in (None, "stable"):
            detail = f"managed releases only support 'stable', not {channel!r}"
            print(json.dumps({"ok": False, "reason": "unknown-channel", "detail": detail}) if as_json else f"unknown-channel: {detail}")
            return 1
        return run_managed_release_upgrade(
            check_only=check_only,
            as_json=as_json,
            dry_run=getattr(args, "dry_run", False),
        )
    if method is InstallMethod.UNKNOWN:
        detail = "no supported product update path for this installation"
        print(json.dumps({"ok": False, "reason": "unknown-install", "detail": detail}) if as_json else f"unknown-install: {detail}")
        return 1

    if channel:
        # `--channel` both selects and persists (§4.4), so the next bare
        # `upgrade` follows the same line without repeating the flag.
        try:
            persist_channel(channel)
        except UpgradeError as e:
            print(json.dumps({"ok": False, "reason": e.reason, "detail": e.detail}) if as_json else f"{e.reason}: {e.detail}")
            return 1
    if check_only:
        return run_status(channel=channel,
                          as_json=getattr(args, "json", False))
    return run_upgrade(
        channel=channel,
        dry_run=getattr(args, "dry_run", False),
        as_json=getattr(args, "json", False),
        no_restart=getattr(args, "no_restart", False),
        yes=getattr(args, "yes", False),
    )
