"""Public API for the snapshot subsystem.

把核心入口都从这里 re-export, 调用方只 import 这一个包就够了。
"""
from .types import ContextItem, Snapshot, CURRENT_RULES_VERSION
from .store import (
    init_schema,
    save_snapshot,
    load_snapshot,
    load_latest_snapshot,
    list_snapshots,
)
from .generator import generate_snapshot
from .ensure import ensure_latest_snapshot
from .views import (
    render_snapshot,
    snapshot_token_total,
    snapshot_state_counts,
)

__all__ = [
    "ContextItem",
    "Snapshot",
    "CURRENT_RULES_VERSION",
    "init_schema",
    "save_snapshot",
    "load_snapshot",
    "load_latest_snapshot",
    "list_snapshots",
    "generate_snapshot",
    "ensure_latest_snapshot",
    "render_snapshot",
    "snapshot_token_total",
    "snapshot_state_counts",
]
