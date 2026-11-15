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


def _source_root() -> Path | None:
    """The repository root when this runs from a source checkout, else ``None``.

    Only a checkout has the ``apps/`` workspaces; an installed runtime (a
    terminal release or the App) ships Node.js, the terminal UI and the web
    export itself, so its users are never told to install Node or run npm.
    """
    import openprogram
    root = Path(openprogram.__file__).resolve().parent.parent
    return root if (root / "apps" / "web" / "package.json").is_file() else None


_REINSTALL = "Reinstall the complete OpenProgram release."


def _probe_node() -> Finding:
    from openprogram.interfaces.cli.ink import packaged_tui
    packaged = packaged_tui()
    if packaged is not None:
        if packaged[1].is_file():
            return Finding("OK", "Node.js", f"bundled: {packaged[1]}")
        return Finding("FAIL", "Node.js", f"bundled runtime missing ({packaged[1]})", fix=_REINSTALL)
    if _source_root() is None:
        return Finding("OK", "Node.js", "not needed by this install")
    n = shutil.which("node")
    if not n:
        return Finding(
            "WARN", "Node.js", "not on PATH",
            fix="Install Node.js 22.12 or newer from https://nodejs.org/ "
                "(this source checkout builds the terminal and web UI with it).",
        )
    return Finding("OK", "Node.js", n)


def _probe_npm() -> Finding | None:
    """Only a source checkout builds anything with npm."""
    if _source_root() is None:
        return None
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
    """The Ink terminal UI the ``openprogram`` command launches."""
    from openprogram.interfaces.cli.ink import packaged_tui
    packaged = packaged_tui()
    if packaged is not None:
        if packaged[0].is_file():
            return Finding("OK", "Terminal UI (Ink)", str(packaged[0]))
        return Finding("FAIL", "Terminal UI (Ink)", f"missing ({packaged[0]})", fix=_REINSTALL)
    root = _source_root()
    if root is None:
        return Finding("FAIL", "Terminal UI (Ink)", "this install has none", fix=_REINSTALL)
    bundle = root / "apps" / "cli" / "dist" / "index.js"
    if bundle.exists():
        return Finding("OK", "Terminal UI (Ink)", str(bundle))
    # The first `openprogram` launch builds it, so missing is not fatal.
    return Finding(
        "WARN", "Terminal UI (Ink)", f"not built yet ({bundle})",
        fix="Built on the first `openprogram` launch. "
            "Or build it now: npm install && npm run build --workspace apps/cli",
    )


def _probe_web_bundle() -> Finding:
    """The web export the worker serves (the same file it checks)."""
    from openprogram.interfaces.server.frontend import out_dir, packaged_out_dir
    marker = out_dir() / "index.html"
    if marker.is_file():
        return Finding("OK", "Web UI", str(marker.parent))
    if _source_root() is None:
        # Name the export the install ships, not the source-checkout path
        # out_dir() falls back to when it is missing.
        return Finding("FAIL", "Web UI", f"missing ({packaged_out_dir()})", fix=_REINSTALL)
    return Finding(
        "WARN", "Web UI", f"not built yet ({marker.parent})",
        fix="Built when the worker starts. "
            "Or build it now: npm install && npm run build --workspace apps/web",
    )


def _probe_providers() -> Finding:
    """Any credentials in the auth store?"""
    try:
        from openprogram.models.auth.store import get_store
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
        from openprogram.runtime.agent_registry import manager as _A
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
    """Worker port — listening, free, or held by someone unrelated?"""
    from openprogram.operations.worker.lifecycle import resolve_worker_port
    port = resolve_worker_port()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        # Something's listening — check if it's our webui
        try:
            from openprogram.operations.worker.lifecycle import find_running_webui
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
                "OPENPROGRAM_WEB_PORT.",
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
        from openprogram.models.auth.store import get_store
        from openprogram.models.auth.credential_provider import get_provider_config
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
            expires_at_ms = payload.data.get("expires_at_ms") if payload.kind == "oauth" else None
            if expires_at_ms:
                left_s = expires_at_ms / 1000 - _time.time()
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
        from openprogram.state.paths import get_state_dir
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


def _probe_proxy_env() -> Finding:
    """Outbound proxy: report what will be used; fail on socks w/o socksio."""
    try:
        from openprogram.models.providers.utils.http_proxy import get_proxy_mounts
        mounts = get_proxy_mounts()
    except Exception as e:  # noqa: BLE001
        return Finding("WARN", "Outbound proxy", f"resolution failed: {e}")
    if not mounts:
        return Finding("OK", "Outbound proxy", "none configured (direct)")
    proxies = sorted({u for u in mounts.values() if u})
    detail = ", ".join(proxies) or "bypass-only NO_PROXY entries"
    if any(u.startswith("socks") for u in proxies):
        try:
            import socksio  # noqa: F401
        except ImportError:
            return Finding(
                "FAIL", "Outbound proxy",
                f"{detail} — socks proxy configured but 'socksio' is missing; "
                "every HTTP provider call will fail at client construction",
                fix="reinstall the complete OpenProgram release",
            )
    return Finding("OK", "Outbound proxy", detail)


PROBES = (
    _probe_python,
    _probe_node,
    _probe_npm,
    _probe_git,
    _probe_proxy_env,
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
            finding = probe()
            if finding is not None:
                findings.append(finding)
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
