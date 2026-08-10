"""``openprogram upgrade`` — gated self-update (phase 2 of
``docs/reference/design/runtime/self-update.md``).

Replaces a bare ``restart`` for code updates. The step chain is fixed:

    preflight → checkout → deps → build → probe → restart → verify

Every step records ``{name, ok, detail, duration_s}``; the first failure
aborts the chain. Any failure before ``restart`` leaves the running
instance completely untouched — nothing has been activated yet, only the
working tree moved (which the running process does not re-read until it
restarts).

Automatic rollback on a failed verify is phase 3; today a verify failure
prints the exact manual rollback command and exits non-zero.

The channel table and the resolve → materialize → verify → activate
shape are the extension points from §4.4: adding ``beta`` is one table
entry, and a future non-git distribution method reimplements only the
first two steps.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------- channels

# channel name -> (git remote, ref). One built-in channel today; adding
# another is a line here plus nothing else (see §4.4).
CHANNELS: dict[str, tuple[str, str]] = {
    "stable": ("origin", "main"),
}

DEFAULT_CHANNEL = "stable"
CONFIG_KEY = "update.channel"


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


def _poll_healthz(port: int, timeout: float,
                  want_sha: Optional[str] = None) -> tuple[bool, str]:
    """Poll ``/healthz`` until it answers (and matches ``want_sha`` when
    given), or ``timeout`` seconds elapse. Returns ``(ok, detail)``."""
    import urllib.request
    deadline = time.monotonic() + timeout
    last = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/healthz", timeout=2.0
            ) as resp:
                body = json.loads(resp.read(65536))
        except Exception as e:  # noqa: BLE001 — the server may not be up yet
            last = f"{type(e).__name__}"
            time.sleep(0.5)
            continue
        if want_sha is None:
            return True, "healthy"
        got = (body or {}).get("sha") or ""
        if got == want_sha:
            return True, f"serving {want_sha[:12]}"
        last = f"serving {got[:12] or '(unknown)'}, expected {want_sha[:12]}"
        time.sleep(1.0)
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
        ok, detail = _poll_healthz(port, timeout=60.0)
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
         "import json;from openprogram._cli_cmds.doctor import run_checks;"
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
        if "web/package-lock.json" in changed:
            _run_or_fail(["npm", "ci"], root / "web", "deps-failed")
            done.append("npm ci")
        steps.record("deps", True, ", ".join(done) or "unchanged", started)

        # 4. build — only when something under web/ moved.
        started = time.monotonic()
        if any(f.startswith("web/") for f in changed):
            _run_or_fail(["npx", "next", "build"], root / "web", "build-failed")
            detail = "next build"
        else:
            detail = "unchanged"
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
        ok, detail = _poll_healthz(port, timeout=90.0, want_sha=target)
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


def _run_or_fail(cmd: list[str], cwd: Path, reason: str) -> None:
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
    channel = getattr(args, "channel", None)
    if channel:
        # `--channel` both selects and persists (§4.4), so the next bare
        # `upgrade` follows the same line without repeating the flag.
        try:
            persist_channel(channel)
        except UpgradeError as e:
            print(f"{e.reason}: {e.detail}")
            return 1
    if getattr(args, "upgrade_verb", None) == "status":
        return run_status(channel=channel,
                          as_json=getattr(args, "json", False))
    return run_upgrade(
        channel=channel,
        dry_run=getattr(args, "dry_run", False),
        as_json=getattr(args, "json", False),
        no_restart=getattr(args, "no_restart", False),
        yes=getattr(args, "yes", False),
    )
