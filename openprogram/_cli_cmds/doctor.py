"""``openprogram doctor`` — diagnose installation health.

Runs the same probes the LLM agent might want to surface as a /doctor
slash command. Each check returns ``(ok, label, detail)``. We print a
plain-text table and exit non-zero when any blocking check fails.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Iterable


def _check_python_version() -> tuple[bool, str, str]:
    ok = sys.version_info >= (3, 11)
    return ok, "python ≥ 3.11", f"running {sys.version.split()[0]}"


def _check_node() -> tuple[bool, str, str]:
    n = shutil.which("node")
    return (n is not None), "node available", n or "not on PATH"


def _check_npm() -> tuple[bool, str, str]:
    n = shutil.which("npm")
    return (n is not None), "npm available", n or "not on PATH"


def _check_git() -> tuple[bool, str, str]:
    n = shutil.which("git")
    return (n is not None), "git available", n or "not on PATH"


def _check_skills_loader() -> tuple[bool, str, str]:
    try:
        from openprogram.skills.loader import list_skills
        skills = list_skills()
        return True, "skills load", f"{len(skills)} skill(s) discovered"
    except Exception as e:
        return False, "skills load", f"{type(e).__name__}: {e}"


def _check_plugin_loader() -> tuple[bool, str, str]:
    try:
        from openprogram.plugins.loader import list_plugins
        plugins = list_plugins()
        errs = [p for p in plugins if p.error]
        if errs:
            return False, "plugins load", f"{len(errs)} error(s)"
        return True, "plugins load", f"{len(plugins)} plugin(s)"
    except Exception as e:
        return False, "plugins load", f"{type(e).__name__}: {e}"


def _check_providers() -> tuple[bool, str, str]:
    """Real provider status — what credentials does ``AuthManager`` actually
    have on disk?

    Earlier versions of this check called ``check_providers()`` from the
    provider registry, which inspects env vars + binary presence (not
    the auth store), and then summed a field that doesn't exist on the
    returned dict (``v.get("ok")`` vs the real key ``"available"``).
    Both bugs combined into a permanent "0/5 authed: anthropic, gemini,
    gemini-cli, openai, openai-codex" no matter what the user had
    actually logged into. Query the auth store directly instead.
    """
    try:
        from openprogram.auth.store import get_store
        pools = get_store().list_pools()
    except Exception as e:  # noqa: BLE001
        return False, "providers", f"{type(e).__name__}: {e}"

    if not pools:
        return False, "providers", (
            "0 configured — run `openprogram providers setup` or "
            "`openprogram providers login <name>`"
        )

    names_seen: list[str] = []
    for p in pools:
        # Pools with at least one credential count as "have something".
        if not p.credentials:
            continue
        label = p.provider_id if p.profile_id == "default" else f"{p.provider_id}/{p.profile_id}"
        if label not in names_seen:
            names_seen.append(label)

    if not names_seen:
        return False, "providers", "pools exist but no credentials inside"

    head = ", ".join(names_seen[:5])
    if len(names_seen) > 5:
        head += "…"
    return True, "providers", f"{len(names_seen)} authed: {head}"


def _check_mcp() -> tuple[bool, str, str]:
    try:
        from openprogram.mcp.config import load_configs
        all_servers = load_configs(include_disabled=True)
        enabled = load_configs(include_disabled=False)
        return True, "mcp servers", f"{len(enabled)}/{len(all_servers)} enabled"
    except Exception as e:
        return False, "mcp servers", f"{type(e).__name__}: {e}"


def _check_disk_cache() -> tuple[bool, str, str]:
    home = Path.home()
    sizes: dict[str, int] = {}
    for sub in ("skills", "plugins", "cache"):
        p = home / ".openprogram" / sub
        if not p.exists():
            continue
        total = 0
        for f in p.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                continue
        sizes[sub] = total
    if not sizes:
        return True, "disk cache", "no cache yet"
    total = sum(sizes.values())
    by_kind = ", ".join(f"{k}={_human(v)}" for k, v in sizes.items() if v > 0)
    return True, "disk cache", f"total {_human(total)} ({by_kind})"


def _check_backend_port() -> tuple[bool, str, str]:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", 18109))
        s.close()
        return True, "worker on :18109", "reachable"
    except OSError:
        return False, "worker on :18109", "not running — `openprogram worker run`"


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if isinstance(n, float) else f"{n}{unit}"
        n = n / 1024  # type: ignore[assignment]
    return f"{n:.1f}TB"


CHECKS: tuple[Callable[[], tuple[bool, str, str]], ...] = (
    _check_python_version,
    _check_node,
    _check_npm,
    _check_git,
    _check_skills_loader,
    # ``_check_skills_watcher`` removed: the watcher only runs inside
    # the webui server process, so a CLI ``openprogram doctor`` always
    # saw it as "not started" and reported FAIL — actionable for
    # nobody. The watcher's health is now visible inside the webui
    # itself; the CLI doctor doesn't pretend to see into a different
    # process.
    _check_plugin_loader,
    _check_providers,
    _check_mcp,
    _check_disk_cache,
    _check_backend_port,
)


def run_checks() -> list[dict]:
    """Run every check and return JSON-serialisable result list."""
    results: list[dict] = []
    for fn in CHECKS:
        try:
            ok, label, detail = fn()
        except Exception as e:  # never let a buggy check kill /doctor
            ok, label, detail = False, fn.__name__, f"{type(e).__name__}: {e}"
        results.append({"ok": ok, "label": label, "detail": detail})
    return results


def _cmd_doctor(as_json: bool = False) -> int:
    results = run_checks()
    if as_json:
        print(json.dumps(results, indent=2))
        return 0 if all(r["ok"] for r in results) else 1
    width = max(len(r["label"]) for r in results) + 2
    fail_count = 0
    for r in results:
        mark = "OK  " if r["ok"] else "FAIL"
        label = r["label"].ljust(width)
        print(f"  [{mark}] {label}{r['detail']}")
        if not r["ok"]:
            fail_count += 1
    print()
    if fail_count == 0:
        print("All checks passed.")
        return 0
    print(f"{fail_count} check(s) failed.")
    return 1
