"""
Context compaction for long agent sessions.

Pure algorithm code — the session manager / tree navigator that calls these
functions is an OpenProgram-specific concern handled by the Runtime layer.
"""

from .compaction import compact_context, should_compact
from .branch_summarization import (
    BranchPreparation,
    BranchSummaryDetails,
    BranchSummaryResult,
    CollectEntriesResult,
    collect_entries_for_branch_summary,
    generate_branch_summary,
    prepare_branch_entries,
)
from .utils import (
    FileOperations,
    SUMMARIZATION_SYSTEM_PROMPT,
    compute_file_lists,
    create_file_ops,
    extract_file_ops_from_message,
    format_file_operations,
    serialize_conversation,
)

__all__ = [
    "BranchPreparation",
    "compact_context",
    "should_compact",
    "BranchSummaryDetails",
    "BranchSummaryResult",
    "CollectEntriesResult",
    "FileOperations",
    "SUMMARIZATION_SYSTEM_PROMPT",
    "collect_entries_for_branch_summary",
    "compute_file_lists",
    "create_file_ops",
    "extract_file_ops_from_message",
    "format_file_operations",
    "generate_branch_summary",
    "prepare_branch_entries",
    "serialize_conversation",
]
