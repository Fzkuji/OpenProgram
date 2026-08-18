#!/usr/bin/env python3
"""Fail CI when Runtime-owned network access escapes the URL registry."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openprogram.security.runtime_http_audit import scan_runtime_http


def main() -> int:
    root = ROOT / "packages/core/src/openprogram"
    result = scan_runtime_http(
        root,
        additional_roots={
            "apps/server/openprogram_server": (
                ROOT / "apps/server/openprogram_server"
            ),
            "apps/cli/python/openprogram_cli": (
                ROOT / "apps/cli/python/openprogram_cli"
            ),
        },
    )
    print(
        "runtime-http inventory: "
        f"unregistered={len(result.unregistered)} "
        f"active_unmanaged={len(result.active_unmanaged_transports)} "
        f"registry_without_consumer={len(result.registry_without_consumer)} "
        f"stale_exclusions={len(result.stale_exclusions)}"
    )
    for issue in result.unregistered:
        print(f"UNREGISTERED {issue.path}:{issue.line} {issue.kind}")
    for consumer in result.active_unmanaged_transports:
        print(f"ACTIVE_UNMANAGED {consumer}")
    for consumer in result.registry_without_consumer:
        print(f"REGISTRY_WITHOUT_CONSUMER {consumer}")
    for path in result.stale_exclusions:
        print(f"STALE_EXCLUSION {path}")
    return int(
        bool(
            result.unregistered
            or result.active_unmanaged_transports
            or result.registry_without_consumer
            or result.stale_exclusions
        )
    )


if __name__ == "__main__":
    sys.exit(main())
