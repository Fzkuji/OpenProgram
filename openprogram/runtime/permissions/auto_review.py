"""Prepare one exact Auto operation before publishing dispatch or approval."""
from __future__ import annotations

from dataclasses import dataclass
from openprogram.runtime.permissions.policy import permission_decision
from openprogram.runtime.permissions.classifier import ReviewResult
from openprogram.runtime.permissions.auto_history import AutoHistory
from openprogram.runtime.permissions.file_state import capture, validate


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
    from openprogram.state.worktree.context import current_worktree_path
    from openprogram.runtime.permissions.classifier import auto_classify_tool, ReviewResult
    from openprogram.runtime.permissions.auto_history import for_operation
    working_dir = current_worktree_path() or os.getcwd()
    reviewed_args = copy.deepcopy(args)
    decision = permission_decision(agent_tool, req, reviewed_args).outcome
    if req.permission_mode != "auto" or decision not in {"auto", "allow"}:
        return None
    history = None
    record = None
    files = {}
    reviewed_screen = False
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
            }, session_id=getattr(req, "session_id", None))
            blocked, reason = verdict
            verdict = ReviewResult(blocked, reason, getattr(verdict, "code",
                "AUTO_CLASSIFIER_DENY" if blocked else "AUTO_CLASSIFIER_ALLOW"))
            reviewed_screen = verdict.code == "AUTO_CLASSIFIER_ALLOW"
        from openprogram.runtime.permissions.lifecycle import current_permission_request, policy_changed
        current = current_permission_request(req)
        if working_dir != (current_worktree_path() or os.getcwd()) or policy_changed(req, current, name):
            verdict = ReviewResult(True, "permission changed during review", "PERMISSION_REVIEW_STALE")
        try:
            validate(files)
        except (OSError, ValueError):
            verdict = ReviewResult(True, "file changed during review", "PERMISSION_REVIEW_STALE")
        if record is None and history and verdict.code in {"AUTO_CLASSIFIER_ALLOW", "AUTO_CLASSIFIER_DENY"}:
            record = history.record(blocked=verdict.blocked, reason=verdict.reason, code=verdict.code)
            verdict = ReviewResult(record["blocked"], record["reason"], record["code"])
        if reviewed_screen and not verdict.blocked:
            from openprogram.runtime.permissions.policy import keeps_screen_review
            if keeps_screen_review(name):
                from openprogram.runtime.permissions import app_grants
                app_grants.grant(req, name, reviewed_args)
    except Exception:
        verdict = ReviewResult(True, "automatic permission history is unavailable", "AUTO_HISTORY_UNAVAILABLE")
    return PreparedReview(str(call_id), reviewed_args, verdict, record, history, files, working_dir)

