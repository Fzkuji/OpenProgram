"""Installation health checks.

``openprogram doctor``, the server's ``/api/doctor`` route and the
self-update system probe all run these. Each check returns
``(ok, label, detail)``; :func:`run_checks` collects them, plus the Runtime
HTTP, platform and system-access rows, into JSON-serialisable dicts.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Callable

from openprogram.operations.self_update.versions.layout import immutable_runtime
from openprogram.state.paths import get_state_dir


def _check_python_version() -> tuple[bool, str, str]:
    ok = sys.version_info >= (3, 11)
    return ok, "python ≥ 3.11", f"running {sys.version.split()[0]}"


def _check_node() -> tuple[bool, str, str]:
    if immutable_runtime():
        return True, "node available", "not required in immutable product runtime"
    n = shutil.which("node")
    if n is None and immutable_runtime():
        return True, "node available", "not required by managed runtime"
    return (n is not None), "node available", n or "not on PATH"


def _check_npm() -> tuple[bool, str, str]:
    if immutable_runtime():
        return True, "npm available", "not required in immutable product runtime"
    n = shutil.which("npm")
    if n is None and immutable_runtime():
        return True, "npm available", "not required by managed runtime"
    return (n is not None), "npm available", n or "not on PATH"


def _check_git() -> tuple[bool, str, str]:
    n = shutil.which("git")
    return (n is not None), "git available", n or "not on PATH"


def _check_skills_loader() -> tuple[bool, str, str]:
    try:
        from openprogram.extensions.skills.loader import list_skills
        skills = list_skills()
        return True, "skills load", f"{len(skills)} skill(s) discovered"
    except Exception as e:
        return False, "skills load", f"{type(e).__name__}: {e}"


def _check_plugin_loader() -> tuple[bool, str, str]:
    try:
        from openprogram.extensions.plugins.loader import list_plugins
        plugins = list_plugins()
        errs = [p for p in plugins if p.error]
        if errs:
            return False, "plugins load", f"{len(errs)} error(s)"
        return True, "plugins load", f"{len(plugins)} plugin(s)"
    except Exception as e:
        return False, "plugins load", f"{type(e).__name__}: {e}"


def _check_providers() -> tuple[bool, str, str]:
    """Real provider status — what credentials does ``CredentialProvider`` actually
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
        from openprogram.models.auth.store import get_store
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
        label = p.provider_id if p.account_id == "default" else f"{p.provider_id}/{p.account_id}"
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
        from openprogram.extensions.mcp.config import load_configs
        all_servers = load_configs(include_disabled=True)
        enabled = load_configs(include_disabled=False)
        return True, "mcp servers", f"{len(enabled)}/{len(all_servers)} enabled"
    except Exception as e:
        return False, "mcp servers", f"{type(e).__name__}: {e}"


def _check_disk_cache() -> tuple[bool, str, str]:
    state = get_state_dir()
    sizes: dict[str, int] = {}
    for sub in ("skills", "plugins", "cache"):
        p = state / sub
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
    from openprogram.operations.worker.lifecycle import resolve_worker_port
    from openprogram.operations.worker.ports import port_in_use
    port = resolve_worker_port()
    if port_in_use(port):
        return True, f"worker on :{port}", "reachable"
    return False, f"worker on :{port}", "not running — `openprogram worker run`"


def _check_command_shell() -> tuple[bool, str, str]:
    """The shell commands run in, the promises it keeps, and one real command.

    The command goes through the command tool's own backend, sandbox
    included, and must bring back UTF-8 text and its exit code 3.
    """
    from openprogram.host._platform.command_shell import resolve_shell
    from openprogram.host.backend.local import LocalBackend

    profile = resolve_shell()
    name = f"{profile.name} {profile.edition}" if profile.edition else profile.name
    parts = [f"{name} ({profile.path})"]
    if profile.sandboxed and profile.host is not None:
        host = profile.host
        host_name = f"{host.name} {host.edition}" if host.edition else host.name
        parts.append(f"sandboxed; outside the sandbox {host_name} ({host.path})")
    else:
        parts.append("not sandboxed")
        try:
            from openprogram.host import sandbox
            reason = sandbox.unavailable_reason() if sandbox.resolve_policy() is not None else None
        except Exception:  # noqa: BLE001 - the row still reports the shell
            reason = None
        if reason:
            parts.append(f"the sandbox is on but cannot run here: {reason}")
    if profile.override_unused:
        parts.append(f"the named bash {profile.override_unused} is not a file and is not used "
                     "(OPENPROGRAM_GIT_BASH_PATH or shell.windows_bash_path)")
    parts.append(", ".join(f"{key}={value}" for key, value in profile.promises.items()))
    source = ("Write-Output '你好'; exit 3" if profile.is_powershell
              else "echo '你好'; exit 3")
    result = LocalBackend().run(source, timeout=20)
    ok = result.exit_code == 3 and result.stdout.strip() == "你好"
    if ok:
        parts.append("round trip: UTF-8 text and exit code 3 came back")
    else:
        got = (result.stdout or result.stderr or "").strip()[:120]
        parts.append(f"round trip failed: exit {result.exit_code}, output {got!r}")
    return ok, "command shell", "; ".join(parts)


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if isinstance(n, float) else f"{n}{unit}"
        n = n / 1024  # type: ignore[assignment]
    return f"{n:.1f}TB"


def runtime_http_checks(
    *,
    config: dict | None = None,
    inventory=None,
) -> list[tuple[bool, str, str]]:
    """Return stable, sanitized Runtime HTTP policy diagnostics."""

    from openprogram.state import config as user_config
    from openprogram.state.settings import parse_outbound_url_settings
    from openprogram.host.security.runtime_http_audit import (
        recent_runtime_http_denials,
        scan_runtime_http,
    )

    if config is None:
        config = user_config.read_config()
    if inventory is None:
        import openprogram

        # The Server and CLI applications are inside the package, so one root
        # covers the core and both interfaces.
        inventory = scan_runtime_http(Path(openprogram.__file__).resolve().parent)

    registry_gaps = (
        len(inventory.unregistered)
        + len(inventory.registry_without_consumer)
        + len(inventory.stale_exclusions)
    )
    registry_ok = registry_gaps == 0
    registry_detail = (
        "all Runtime HTTP calls and registry consumers classified"
        if registry_ok
        else (
            f"{len(inventory.unregistered)} raw call(s), "
            f"{len(inventory.registry_without_consumer)} consumer gap(s), "
            f"{len(inventory.stale_exclusions)} stale exclusion(s)"
        )
    )

    raw_security = config.get("security", {}) if isinstance(config, dict) else {}
    raw_outbound = (
        raw_security.get("outbound_url", {})
        if isinstance(raw_security, dict)
        else {}
    )
    try:
        settings = parse_outbound_url_settings(raw_outbound)
    except ValueError:
        settings = None

    if settings is None:
        owner_row = (
            False,
            "runtime-http-owner-exceptions",
            "invalid outbound URL security configuration",
        )
        proxy_row = (
            False,
            "runtime-http-policy-proxy",
            "invalid outbound URL security configuration",
        )
    else:
        exception_details = [
            f"{item.consumer}={item.origin or item.cidr}"
            for item in settings.exceptions
        ]
        owner_row = (
            True,
            "runtime-http-owner-exceptions",
            ", ".join(exception_details) if exception_details else "none",
        )
        proxy = settings.policy_proxy
        proxy_row = (
            True,
            "runtime-http-policy-proxy",
            (
                f"delegated to {proxy.url}; target-policy enforcement asserted"
                if proxy is not None
                else "disabled"
            ),
        )

    denials = recent_runtime_http_denials()
    denial_detail = (
        "; ".join(
            f"{event.consumer}:{event.reason}@{event.safe_origin}"
            for event in denials[-5:]
        )
        if denials
        else "none"
    )
    unmanaged = inventory.active_unmanaged_transports
    return [
        (registry_ok, "runtime-http-registry", registry_detail),
        owner_row,
        proxy_row,
        (True, "runtime-http-recent-denials", denial_detail),
        (
            not unmanaged,
            "runtime-http-unmanaged-transport",
            ", ".join(unmanaged) if unmanaged else "none active",
        ),
    ]


def _runtime_http_check(index: int) -> tuple[bool, str, str]:
    return runtime_http_checks()[index]


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
    checks: list[Callable[[], tuple[bool, str, str]]] = list(CHECKS)
    from openprogram.host._platform import environment

    platform_rows = environment.platform_environment_advisories(get_state_dir())
    for fn in checks:
        try:
            ok, label, detail = fn()
        except Exception as e:  # never let a buggy check kill /doctor
            ok, label, detail = False, fn.__name__, f"{type(e).__name__}: {e}"
        results.append({"id": fn.__name__, "ok": ok, "label": label, "detail": detail})
    # The five Runtime HTTP rows share one source inventory. Compute it once;
    # rescanning the installed tree per row is especially costly on Defender.
    try:
        runtime_rows = runtime_http_checks()
    except Exception as e:  # never let a buggy check kill /doctor
        runtime_rows = [
            (False, "runtime-http-registry", f"{type(e).__name__}: {e}")
        ]
    results.extend(
        {"id": "runtime_http:" + label, "ok": ok, "label": label, "detail": detail}
        for ok, label, detail in runtime_rows
    )
    results.extend(
        {"id": "platform:" + label, "ok": ok, "label": label, "detail": detail}
        for ok, label, detail in platform_rows
    )
    from openprogram.host.system_access import doctor_rows
    results.extend(doctor_rows())
    # Last, after every row that existed before it, so existing ids keep
    # their order. Not in CHECKS: the self-update probe requires every CHECKS id from the
    # worker it verifies, and a worker from before this row has none. Advice,
    # like the system access rows: a failed round trip shows as INFO with its
    # reason and gates neither an upgrade nor a self-update.
    try:
        ok, label, detail = _check_command_shell()
    except Exception as e:  # never let a buggy check kill /doctor
        ok, label, detail = False, "command shell", f"{type(e).__name__}: {e}"
    results.append({"id": "command_shell", "ok": True, "label": label, "detail": detail,
                    "optional": True, "status": "granted" if ok else "failed"})
    return results
