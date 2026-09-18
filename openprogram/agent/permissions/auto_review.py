"""Prepare one exact Auto operation before publishing dispatch or approval."""
from __future__ import annotations

from dataclasses import dataclass
from .policy import permission_decision
from .classifier import ReviewResult
from .auto_history import AutoHistory
from .file_state import capture, validate


@dataclass(frozen=True)
class PreparedReview:
    call_id: str
    args: dict
    verdict: ReviewResult
    record: dict | None
    history: AutoHistory | None
    files: dict
    working_dir: str

    def matches(self, call_id, args):
        return self.call_id == str(call_id) and self.args == args



async def prepare_review(agent_tool, req, call_id, args, *, operation_id=None):
    name = agent_tool.name
    import copy
    import os
    from openprogram.worktree.context import current_worktree_path
    from .classifier import auto_classify_tool, ReviewResult
    from .auto_history import for_operation
    working_dir = current_worktree_path() or os.getcwd()
    reviewed_args = copy.deepcopy(args)
    decision = permission_decision(agent_tool, req, reviewed_args)[0]
    if req.permission_mode != "auto" or decision not in {"auto", "allow"}:
        return None
    history = None
    record = None
    files = {}
    try:
        files = capture(name, reviewed_args)
        history = for_operation(req, name, call_id, reviewed_args, operation_id, files=files)
        record = history.get() if history else None
        if record is not None:
            verdict = ReviewResult(record["blocked"], record["reason"], record["code"])
        elif decision == "allow":
            verdict = ReviewResult(False, "allowed by policy", "AUTO_CLASSIFIER_ALLOW")
        else:
            verdict = await auto_classify_tool(name, copy.deepcopy(reviewed_args), context={
                "working_directory": working_dir,
                "user_request": getattr(req, "user_text", ""),
                "permission_mode": req.permission_mode,
                "additional_working_directories": getattr(req, "additional_working_dirs", []),
                "authority_tier": getattr(req, "authority_tier", None),
                "file_preconditions": files,
            })
            blocked, reason = verdict
            verdict = ReviewResult(blocked, reason, getattr(verdict, "code",
                "AUTO_CLASSIFIER_DENY" if blocked else "AUTO_CLASSIFIER_ALLOW"))
        from .lifecycle import current_permission_request
        current = current_permission_request(req)
        if working_dir != (current_worktree_path() or os.getcwd()) or any(getattr(current, key, None) != getattr(req, key, None)
               for key in ("permission_mode", "permission_rules", "_permission_version")):
            verdict = ReviewResult(True, "permission changed during review", "PERMISSION_REVIEW_STALE")
        try:
            validate(files)
        except (OSError, ValueError):
            verdict = ReviewResult(True, "file changed during review", "PERMISSION_REVIEW_STALE")
        if record is None and history and verdict.code in {"AUTO_CLASSIFIER_ALLOW", "AUTO_CLASSIFIER_DENY"}:
            record = history.record(blocked=verdict.blocked, reason=verdict.reason, code=verdict.code)
            verdict = ReviewResult(record["blocked"], record["reason"], record["code"])
    except Exception:
        verdict = ReviewResult(True, "automatic permission history is unavailable", "AUTO_HISTORY_UNAVAILABLE")
    return PreparedReview(str(call_id), reviewed_args, verdict, record, history, files, working_dir)

