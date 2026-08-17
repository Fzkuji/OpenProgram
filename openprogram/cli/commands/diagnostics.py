"""``openprogram diagnostics`` — build a shareable, redacted support bundle.

Reporting a bug used to mean "go find your logs", which meant nobody
attached them and every report opened with three round-trips of "where
is that file?". This collects what a maintainer would ask for into one
zip: version + platform, a redacted config snapshot, the tail of each
log, and the environment probes ``doctor`` already runs.

Redaction reuses :func:`openprogram.providers.recording.remove_secret_values`,
the recursive scrubber the provider recorder already trusts, so the two
paths cannot drift into disagreeing about what counts as a secret. Two
gaps in it matter here and are closed locally:

* it matches dict keys *exactly* against ``SECRET_FIELD_NAMES``, so a
  config written as ``openai_api_key`` or ``github_token`` slips
  through unless the value happens to look like a key. Config files are
  exactly where such compound names live, so this module matches keys
  by substring as well.
* its value patterns cover bearer / ``sk-`` / URL-embedded credentials,
  which is right for HTTP recordings but misses the GitHub, AWS, Google
  and JWT shapes that turn up in logs and user config.

Credential files themselves never enter the bundle. The auth store is
reported as which providers have credentials and how many — names and
counts only, never the payload.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from openprogram.providers.recording import (
    PLACEHOLDER,
    SECRET_FIELD_NAMES,
    remove_secret_values,
)

# Substring-matched against a lowercased mapping key, to catch the
# compound names (``openai_api_key``, ``github_token``) that the
# recorder's exact-match set does not. Deliberately broad: a false
# positive costs a maintainer one question, a false negative posts a
# live credential to a bug tracker.
SENSITIVE_KEY_PARTS = tuple(SECRET_FIELD_NAMES) + (
    "credential", "passwd", "private_key", "signature", "session_id",
    "access_key", "auth",
)

# Credential shapes the recorder's patterns don't cover.
EXTRA_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),          # GitHub
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),        # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                  # AWS access key id
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}"),              # Google API key
    re.compile(                                            # JWT
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),
)

# ``some_token: <opaque blob>`` in otherwise unstructured text, where
# there is no dict key to inspect because the line is a log message.
_KEYED_VALUE_PATTERN = re.compile(
    r"(?i)([A-Za-z0-9_.-]*(?:%s)[A-Za-z0-9_.-]*[\"']?\s*[:=]\s*[\"']?)"
    r"([A-Za-z0-9._~+/-]{8,}=*)"
    % "|".join(re.escape(part) for part in SENSITIVE_KEY_PARTS)
)


def is_sensitive_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_text(text: str) -> str:
    """Scrub credential-shaped substrings out of free text."""
    text = remove_secret_values(text)
    for pattern in EXTRA_VALUE_PATTERNS:
        text = pattern.sub(PLACEHOLDER, text)
    return _KEYED_VALUE_PATTERN.sub(lambda m: m.group(1) + PLACEHOLDER, text)


def redact_value(value: Any) -> Any:
    """Recursively redact a JSON-like structure.

    A sensitive *key* redacts its whole subtree: an ``auth`` block has
    nothing inside worth keeping, and descending would risk emitting the
    parts that match no pattern.
    """
    if isinstance(value, dict):
        return {
            k: (PLACEHOLDER if is_sensitive_key(k) else redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return remove_secret_values(value)


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
    from openprogram.paths import get_config_path
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
        from openprogram.auth.store import get_store
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
    from openprogram.cli.commands.logs import _log_targets
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
    from openprogram.cli.commands.doctor import run_checks
    from openprogram.paths import get_logs_dir, get_state_dir

    probes: dict[str, Any] = {"doctor_checks": run_checks()}

    web_out = Path(__file__).resolve().parents[2] / "web" / "out"
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
