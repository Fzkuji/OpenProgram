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


def _check_skills_watcher() -> tuple[bool, str, str]:
    try:
        import openprogram.skills.watcher as w
        running = w._thread is not None and w._thread.is_alive()
        if running:
            return True, "skills watcher", "running"
        return False, "skills watcher", "not started (server hasn't booted?)"
    except Exception as e:
        return False, "skills watcher", f"{type(e).__name__}: {e}"


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
    try:
        from openprogram.providers.registry import check_providers
        info = check_providers()
        ok_count = sum(1 for v in info.values() if v.get("ok"))
        total = len(info)
        names = ", ".join(sorted(info.keys())[:5])
        if total > 5:
            names += "…"
        return ok_count > 0, "providers", f"{ok_count}/{total} authed: {names}"
    except Exception as e:
        return False, "providers", f"{type(e).__name__}: {e}"


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
        s.connect(("127.0.0.1", 8109))
        s.close()
        return True, "worker on :8109", "reachable"
    except OSError:
        return False, "worker on :8109", "not running — `openprogram worker run`"


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
    _check_skills_watcher,
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
