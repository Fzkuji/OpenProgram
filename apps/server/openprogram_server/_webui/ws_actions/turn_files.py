"""Bounded Review scopes, exact journal diffs, Undo and Reapply actions."""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


_MAX_SCOPE_FILES = 10_000
_SCOPE_PAGE_SIZE = 100
_MAX_DIFF_BYTES = 512 * 1024
_MAX_DIFF_PAGE_BYTES = 256 * 1024
_MAX_DIFF_LINES = 200
_MAX_DIFF_LINE_BYTES = 64 * 1024
_REVIEW_CATEGORIES = {"All", "Code", "Tests", "Docs", "Large"}
_REVIEW_SORTS = {"path", "alpha", "category", "recent"}
_REVIEW_SCOPES = {"turn", "branch", "workspace"}
_MAX_REVIEW_SNAPSHOTS = 256
_MAX_REVIEW_SNAPSHOT_BYTES = 16 * 1024 * 1024
_MAX_REVIEW_SNAPSHOT_ITEMS = _MAX_SCOPE_FILES
_REVIEW_SNAPSHOT_TTL = 5 * 60
_MAX_REVIEW_CURSORS = 1024
_MAX_REVIEW_SNAPSHOT_TOMBSTONES = _MAX_REVIEW_SNAPSHOTS + _MAX_REVIEW_CURSORS
_MAX_REVIEW_TEXT_BYTES = 4096

# Review snapshots are deliberately separate from the read cache.  They hold
# the exact candidate set used by a page/diff request until its content is
# invalidated by a fresh workspace comparison.
_REVIEW_SNAPSHOTS: dict[str, dict] = {}
_REVIEW_CURSORS: dict[str, dict] = {}
_REVIEW_SNAPSHOT_EPOCHS: dict[str, int] = {}
_REVIEW_SNAPSHOT_NONCE = 0
_REVIEW_REGISTRY_LOCK = threading.RLock()


# Scope, diff, and history own their implementations in dedicated modules;
# this module remains the stable public action namespace and shared limits.
from .turn_files_scope import *  # noqa: E402,F403
from .turn_files_diff import *  # noqa: E402,F403
from .turn_files_history import (  # noqa: E402
    ACTIONS, handle_reapply_turn, handle_review_file_diff,
    handle_review_scope, handle_revert_turn, handle_turn_history_state,
    handle_turn_operation_status, _stable_file_result,
)
