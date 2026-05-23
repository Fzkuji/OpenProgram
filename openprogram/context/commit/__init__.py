"""Public API for the context commit subsystem.

把核心入口都从这里 re-export, 调用方只 import 这一个包就够了。
"""
from .types import ContextItem, ContextCommit, CURRENT_RULES_VERSION
from .store import (
    init_schema,
    save_commit,
    load_commit,
    load_latest_commit,
    load_commit_for_head,
    list_commits,
)
from .generator import generate_commit
from .ensure import ensure_latest_commit
from .views import (
    render_commit,
    commit_token_total,
    commit_state_counts,
)

__all__ = [
    "ContextItem",
    "ContextCommit",
    "CURRENT_RULES_VERSION",
    "init_schema",
    "save_commit",
    "load_commit",
    "load_latest_commit",
    "load_commit_for_head",
    "list_commits",
    "generate_commit",
    "ensure_latest_commit",
    "render_commit",
    "commit_token_total",
    "commit_state_counts",
]
