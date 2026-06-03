"""``openprogram rescue`` — deterministic repair helper.

Crestodian-style "what's broken and how do I fix it?" diagnostic that
works WITHOUT an LLM — every probe is a plain Python check against
local state (config files, ports, binaries, credential pools, build
artefacts). The output is a flat list:

    [OK]   foo                  one-line detail
    [WARN] bar                  one-line detail
                                ↳ fix: openprogram providers setup

Designed to be the first thing a user runs when bare ``openprogram``
errors out, or when something doesn't work and they don't know where
to start. Doesn't change any state by itself — it only suggests
commands the user runs separately. That way an LLM-broken install
(no provider, bad token, etc.) can still walk through the report;
nothing here depends on AI being reachable.

Mirrors openclaw's ``crestodian`` in spirit (configless-safe, plain
planner) but scoped tighter — we only check the few things that
actually break for OpenProgram users on a normal install.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Finding:
    """One probe result."""
    level: str  # "OK" | "WARN" | "FAIL"
    label: str
    detail: str
    fix: Optional[str] = None  # exact command the user should run


# ---------------------------------------------------------------------------
# Probes — each returns one Finding. Order matters: dependencies first,
# then config, then state.
# ---------------------------------------------------------------------------

def _probe_python() -> Finding:
    ok = sys.version_info >= (3, 11)
    detail = f"running {sys.version.split()[0]}"
    if ok:
        return Finding("OK", "Python ≥ 3.11", detail)
    return Finding(
        "FAIL", "Python ≥ 3.11", detail,
        fix="Install Python 3.11 or newer and reinstall openprogram into it.",
    )


def _probe_node() -> Finding:
    n = shutil.which("node")
    if not n:
        return Finding(
            "WARN", "node on PATH", "not found",
            fix="Install Node.js 20+ from https://nodejs.org/ "
                "(needed for the Ink TUI and Next.js web UI).",
        )
    return Finding("OK", "node on PATH", n)


def _probe_npm() -> Finding:
    n = shutil.which("npm")
    if not n:
        return Finding(
            "WARN", "npm on PATH", "not found",
            fix="Install Node.js (npm ships with it).",
        )
    return Finding("OK", "npm on PATH", n)


def _probe_git() -> Finding:
    n = shutil.which("git")
    if not n:
        return Finding(
            "WARN", "git on PATH", "not found",
            fix="Install git — needed for session persistence "
                "(every conversation is a tiny git repo).",
        )
    return Finding("OK", "git on PATH", n)


def _probe_tui_bundle() -> Finding:
    """``cli/dist/index.js`` — the Ink TUI's pre-built Node bundle."""
    try:
        import openprogram
        root = Path(openprogram.__file__).resolve().parent.parent
    except Exception as e:  # noqa: BLE001
        return Finding("FAIL", "TUI bundle",
                       f"can't locate repo root: {e}",
                       fix=None)
    bundle = root / "cli" / "dist" / "index.js"
    if bundle.exists():
        return Finding("OK", "TUI bundle (Ink)", str(bundle))
    # Auto-built on first launch, so missing is not fatal — just informative.
    return Finding(
        "WARN", "TUI bundle (Ink)", f"not built yet ({bundle})",
        fix="Auto-built on first `openprogram` launch on POSIX. "
            "Or build manually: cd cli && npm install && npm run build",
    )


def _probe_web_bundle() -> Finding:
    """``web/.next/BUILD_ID`` — the Next.js webui production build."""
    try:
        import openprogram
        root = Path(openprogram.__file__).resolve().parent.parent
    except Exception:  # noqa: BLE001
        return Finding("FAIL", "Web bundle", "can't locate repo root", fix=None)
    build_id = root / "web" / ".next" / "BUILD_ID"
    if build_id.exists():
        return Finding("OK", "Web bundle (Next.js)",
                       f"built at {build_id.parent}")
    return Finding(
        "WARN", "Web bundle (Next.js)", f"not built ({root / 'web' / '.next'})",
        fix="Auto-built on first `openprogram web` launch. Or manually: "
            "cd web && npm install && npm run build",
    )


def _probe_providers() -> Finding:
    """Any credentials in the auth store?"""
    try:
        from openprogram.auth.store import get_store
        pools = get_store().list_pools()
    except Exception as e:  # noqa: BLE001
        return Finding("FAIL", "Provider credentials",
                       f"auth store error: {e}", fix=None)
    if not pools:
        return Finding(
            "FAIL", "Provider credentials", "0 configured",
            fix="openprogram providers setup    "
                "# (or: providers login <name>)",
        )
    names = sorted({p.provider_id for p in pools if p.credentials})
    return Finding("OK", "Provider credentials",
                   f"{len(names)} configured: {', '.join(names[:5])}")


def _probe_default_agent() -> Finding:
    """Does a default agent exist with a pinned model?"""
    try:
        from openprogram.agents import manager as _A
        agent = _A.get_default()
    except Exception as e:  # noqa: BLE001
        return Finding("FAIL", "Default agent",
                       f"agents manager error: {e}", fix=None)
    if agent is None:
        return Finding(
            "WARN", "Default agent", "none — will be auto-created on chat",
            fix=None,
        )
    if not (agent.model and (agent.model.provider or "").strip()):
        return Finding(
            "WARN", "Default agent", f"{agent.id} (no provider pinned)",
            fix=f"openprogram agents add {agent.id} --provider <name> --model <id>",
        )
    return Finding(
        "OK", "Default agent",
        f"{agent.id} → {agent.model.provider}/{agent.model.id}",
    )


def _probe_worker_port() -> Finding:
    """Backend port 18109 — listening, free, or held by someone unrelated?"""
    port = 18109
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        # Something's listening — check if it's our webui
        try:
            from openprogram.worker.lifecycle import find_running_webui
            _, _, source = find_running_webui()
        except Exception:  # noqa: BLE001
            source = "unknown"
        if source == "managed":
            return Finding("OK", f"Worker on :{port}", "managed (lock file present)")
        if source == "unmanaged":
            return Finding(
                "WARN", f"Worker on :{port}", "running but unmanaged",
                fix="openprogram worker restart    "
                    "# converts to managed; preserves session state",
            )
        return Finding(
            "WARN", f"Port :{port}", "in use by an unknown process",
            fix=f"Stop whatever owns :{port}, or change "
                "OPENPROGRAM_BACKEND_PORT.",
        )
    except OSError:
        return Finding(
            "OK", f"Worker on :{port}", "free (start with `openprogram worker start`)",
        )
    finally:
        try:
            s.close()
        except OSError:
            pass


def _probe_credential_freshness() -> Finding:
    """OAuth credentials with refresh registered and time left?"""
    try:
        from openprogram.auth.store import get_store
        from openprogram.auth.manager import get_provider_config
        from openprogram.auth.types import OAuthPayload
        import time as _time
        pools = get_store().list_pools()
    except Exception as e:  # noqa: BLE001
        return Finding("OK", "OAuth freshness", f"(skipped: {e})", fix=None)

    expiring: list[str] = []
    no_refresh: list[str] = []
    for pool in pools:
        cfg = get_provider_config(pool.provider_id)
        has_refresh = bool(cfg.refresh or cfg.async_refresh)
        for c in pool.credentials:
            if c.kind != "oauth":
                continue
            if not has_refresh and not c.read_only:
                no_refresh.append(pool.provider_id)
            payload = c.payload
            if isinstance(payload, OAuthPayload) and payload.expires_at_ms:
                left_s = payload.expires_at_ms / 1000 - _time.time()
                if 0 < left_s < 7 * 86400:  # < 1 week
                    expiring.append(f"{pool.provider_id} ({int(left_s // 86400)}d)")

    if not expiring and not no_refresh:
        return Finding("OK", "OAuth freshness", "all tokens healthy")
    bits = []
    if expiring:
        bits.append(f"expiring soon: {', '.join(expiring)}")
    if no_refresh:
        bits.append(f"no refresh callback: {', '.join(no_refresh)}")
    return Finding(
        "WARN", "OAuth freshness", "; ".join(bits),
        fix="For expiring: openprogram providers login <name> (re-auth). "
            "For no-refresh: usually a code wiring issue — file an issue.",
    )


def _probe_disk_state_dir() -> Finding:
    """Can we write to the state dir (~/.openprogram)?"""
    try:
        from openprogram.paths import get_state_dir
        p = get_state_dir()
    except Exception:
        p = Path.home() / ".openprogram"
    try:
        p.mkdir(parents=True, exist_ok=True)
        test = p / ".write-test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
    except OSError as e:
        return Finding(
            "FAIL", "State dir writable", f"{p} — {e}",
            fix=f"Fix permissions on {p}, or set OPENPROGRAM_STATE_DIR "
                "to a writable path.",
        )
    return Finding("OK", "State dir writable", str(p))


PROBES = (
    _probe_python,
    _probe_node,
    _probe_npm,
    _probe_git,
    _probe_disk_state_dir,
    _probe_providers,
    _probe_default_agent,
    _probe_credential_freshness,
    _probe_worker_port,
    _probe_tui_bundle,
    _probe_web_bundle,
)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _cmd_rescue() -> int:
    findings: list[Finding] = []
    for probe in PROBES:
        try:
            findings.append(probe())
        except Exception as e:  # noqa: BLE001 — probe must never throw
            findings.append(Finding(
                "FAIL", probe.__name__,
                f"probe crashed: {type(e).__name__}: {e}",
                fix=None,
            ))

    width = max(len(f.label) for f in findings) + 2

    fix_count = sum(1 for f in findings if f.fix)
    fail_count = sum(1 for f in findings if f.level == "FAIL")
    warn_count = sum(1 for f in findings if f.level == "WARN")

    print()
    print("openprogram rescue — what's broken, how to fix")
    print("=" * 70)
    for f in findings:
        print(f"  [{f.level:<4}] {f.label.ljust(width)}{f.detail}")
        if f.fix:
            for line in f.fix.splitlines():
                print(f"          ↳ {line}")
    print("=" * 70)
    if fail_count == 0 and warn_count == 0:
        print("All probes passed. Everything looks healthy.")
        return 0
    summary = []
    if fail_count:
        summary.append(f"{fail_count} fail(s)")
    if warn_count:
        summary.append(f"{warn_count} warning(s)")
    print(f"{', '.join(summary)} — {fix_count} fix command(s) above.")
    return 1 if fail_count else 0


__all__ = ["_cmd_rescue"]
