"""``openprogram diagnostics`` — build a shareable, redacted support bundle.

Reporting a bug used to mean "go find your logs", which meant nobody
attached them and every report opened with three round-trips of "where
is that file?". This collects what a maintainer would ask for into one
zip: version + platform, a redacted config snapshot, the tail of each
log, and the environment probes ``doctor`` already runs.

Redaction is :mod:`openprogram.operations.diagnostics.redaction`, the same
scrubber credential-helper errors go through, so the two paths cannot
disagree about what counts as a secret.

Credential files themselves never enter the bundle. The auth store is
reported as which providers have credentials and how many — names and
counts only, never the payload.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from openprogram.models.providers.recording.recording import PLACEHOLDER
from openprogram.operations.diagnostics.redaction import redact_text, redact_value

def _version_info() -> dict[str, Any]:
    try:
        from importlib.metadata import version
        installed = version("openprogram")
    except Exception:
        installed = None
    return {
        "openprogram": installed or "unknown",
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def _config_snapshot() -> dict[str, Any]:
    """The user's config.json with every credential-shaped value removed."""
    from openprogram.state.paths import get_config_path
    path = get_config_path()
    if not path.exists():
        return {"_note": f"no config at {path}"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"_error": f"could not parse {path}: {type(e).__name__}: {e}"}
    return redact_value(raw)


def _credentials_report() -> dict[str, Any]:
    """Which providers have credentials — names and counts, no payloads.

    Walks the auth directory rather than calling ``list_pools()``, which
    parses every credential file: nothing here needs the contents, so
    nothing here reads them.
    """
    try:
        from openprogram.models.auth.store import get_store
        base = get_store().base_dir()
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}

    providers: dict[str, int] = {}
    if base.is_dir():
        for provider_dir in sorted(base.iterdir()):
            if provider_dir.is_dir():
                providers[provider_dir.name] = len(list(provider_dir.glob("*.json")))
    return {
        "note": "provider names and account counts only; no credential contents",
        "auth_dir": str(base),
        "providers": providers,
    }


def _log_excerpts(max_lines: int) -> list[tuple[str, str]]:
    """Return ``[(archive_name, redacted_tail), ...]`` for each known log."""
    from openprogram.interfaces.cli.commands.logs import _log_targets
    out: list[tuple[str, str]] = []
    for name, path in _log_targets():
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            out.append((f"logs/{name}.log", f"<unreadable: {e}>"))
            continue
        out.append((f"logs/{name}.log", redact_text("\n".join(lines[-max_lines:]))))
    return out


def _environment_probes() -> dict[str, Any]:
    """Port / worker / build-artifact / directory-permission facts.

    Reuses ``doctor``'s check list so the bundle and ``openprogram
    doctor`` can never disagree about what healthy means, then adds the
    filesystem details doctor has no reason to print.
    """
    from openprogram.operations.diagnostics.checks import run_checks
    from openprogram.state.paths import get_logs_dir, get_state_dir

    probes: dict[str, Any] = {"doctor_checks": run_checks()}

    import openprogram

    web_out = Path(openprogram.__file__).resolve().parents[1] / "apps" / "web" / "out"
    probes["web_build"] = {
        "path": str(web_out),
        "exists": web_out.is_dir(),
        "file_count": sum(1 for _ in web_out.rglob("*")) if web_out.is_dir() else 0,
    }

    dirs: dict[str, Any] = {}
    for label, path in (("state", get_state_dir()), ("logs", get_logs_dir())):
        if path.exists():
            dirs[label] = {
                "path": str(path),
                "mode": oct(path.stat().st_mode & 0o777),
                "writable": os.access(path, os.W_OK),
            }
        else:
            dirs[label] = {"path": str(path), "exists": False}
    probes["directories"] = dirs
    return probes


def build_bundle(output: Path, max_log_lines: int = 2000) -> list[str]:
    """Write the diagnostics zip; return the archive names inside it."""
    entries: list[tuple[str, str, str]] = []  # (arcname, content, source)

    entries.append((
        "version.json",
        json.dumps(_version_info(), indent=2),
        "importlib.metadata + platform",
    ))
    entries.append((
        "config.json",
        json.dumps(_config_snapshot(), indent=2),
        "config.json, credential values replaced",
    ))
    entries.append((
        "credentials.json",
        json.dumps(_credentials_report(), indent=2),
        "auth directory listing, no credential contents",
    ))
    entries.append((
        "environment.json",
        json.dumps(_environment_probes(), indent=2, default=str),
        "doctor checks + filesystem probes",
    ))
    for arcname, text in _log_excerpts(max_log_lines):
        entries.append((arcname, text, f"last {max_log_lines} lines, redacted"))

    manifest = {
        "generated": date.today().isoformat(),
        "redaction": (
            f"Credential-shaped values are replaced with {PLACEHOLDER!r}. "
            "Credential files are never included."
        ),
        "files": [
            {"name": name, "source": source, "bytes": len(content.encode("utf-8"))}
            for name, content, source in entries
        ],
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content, _ in entries:
            zf.writestr(name, content)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    return [name for name, _, _ in entries] + ["manifest.json"]


def _cmd_diagnostics(output: str | None = None) -> int:
    target = Path(output) if output else Path.cwd() / (
        f"openprogram-diagnostics-{date.today().isoformat()}.zip"
    )
    try:
        names = build_bundle(target)
    except Exception as e:  # noqa: BLE001
        print(f"Failed to build diagnostics bundle: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1

    print(f"Wrote {target} ({target.stat().st_size / 1024:.1f} KB)\n")
    print("Contents:")
    for name in names:
        print(f"  {name}")
    print(
        f"\nCredential values were replaced with {PLACEHOLDER} and "
        "credential files were not collected."
        "\nReview the contents yourself before sharing this file."
    )
    return 0
