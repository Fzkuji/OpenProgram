"""Metadata-only permission-check events; no tool argument text is collected.

Policy checks also run while constructing manifests and reconciling waits.
These events describe checks, not completed execution, and may repeat.
The existing event bus supplies timestamps, best-effort delivery and retention.
"""
from __future__ import annotations


def log_delegated_risky_tool(
    session_id: str,
    tool_name: str,
    source: str,
    authority_tier: str,
) -> None:
    """Record a delegated risky-tool check allowed by bypass or an allow rule."""
    from openprogram.runtime.events import emit_safe

    emit_safe(
        "security.risky_tool_delegated",
        "system",
        {
            "session_id": session_id,
            "tool": tool_name,
            "source": source,
            "authority": authority_tier,
        },
        metadata={"session": session_id} if session_id else None,
    )


def log_path_safety_violation(
    session_id: str,
    tool_name: str,
    violation_reason: str,
) -> None:
    """Record a fixed policy reason without the path or other arguments."""
    from openprogram.runtime.events import emit_safe

    emit_safe(
        "security.path_violation",
        "system",
        {
            "session_id": session_id,
            "tool": tool_name,
            "reason": violation_reason,
        },
        metadata={"session": session_id} if session_id else None,
    )
