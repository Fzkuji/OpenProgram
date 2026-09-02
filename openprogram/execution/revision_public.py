"""Strict, transport-neutral public authority for revision drafts.

The revision service owns its durable state.  This module owns the public
envelope, server-derived binding, authorization, audit record, and debugger
projection so REST and WebSocket cannot acquire separate mutation semantics.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Mapping

from openprogram.agent.authority import normalize_authority

from .authorization import (
    POLICY_VERSION,
    ExecutionAuthorizationError,
    authorize_execution_action,
)
from .revisions import (
    RevisionApproval,
    RevisionControlService,
    RevisionDraft,
    RevisionManifest,
    RevisionValidation,
)
from .store import ExecutionConflict, ExecutionStore


_TYPE = "revision.draft"
_ACTIONS = frozenset(
    {
        "revision.draft.create",
        "revision.draft.get",
        "revision.draft.replace",
        "revision.draft.discard",
        "revision.validate",
        "revision.approve",
        "revision.publish",
    }
)
_MUTATIONS = _ACTIONS - {"revision.draft.get"}


class RevisionPublicError(RuntimeError):
    """A stable public rejection; transports must not expose internal data."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _subject(actor: Mapping[str, Any]) -> dict[str, str]:
    value = normalize_authority(actor)
    if not value:
        raise RevisionPublicError("not_found")
    return {"subject": value["speaker_id"]}


def _git_head(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and len(value) == 40 else None


def server_project_binding(store: ExecutionStore, execution: Any) -> dict[str, str]:
    """Build the exact immutable revision binding; client fields never enter."""
    from .public import project_id_for_session

    project_id = project_id_for_session(execution.session_id)
    project_path: Path | None = None
    try:
        from openprogram.store.project import project_for_session

        project = project_for_session(execution.session_id)
        if project is not None and project.path:
            project_path = Path(project.path).expanduser().resolve()
    except Exception:
        project_path = None
    if project_path is None:
        project_path = Path.cwd().resolve()
    worktree_id = "project"
    try:
        payload = store.get_job_agent_input(execution.execution_id)
        value = (payload or {}).get("job_context", {}).get("worktree_id")
        if isinstance(value, str) and value:
            worktree_id = value
    except Exception:
        worktree_id = "project"
    root_identity = hashlib.sha256(str(project_path).encode("utf-8")).hexdigest()
    source_head = _git_head(project_path)
    if source_head is not None:
        source_commit = hashlib.sha256(source_head.encode("ascii")).hexdigest()
    else:
        revision = store.get_revision(execution.revision_id)
        source_commit = revision.content_hash if revision is not None else root_identity
    return {
        "project_id": project_id,
        "worktree_id": worktree_id,
        "root_identity": root_identity,
        "source_commit": source_commit,
    }


def _draft_dict(value: RevisionDraft) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "draft_id": value.draft_id,
        "project_id": value.project_binding["project_id"],
        "source_execution_id": value.source_execution_id,
        "base_revision_id": value.base_revision_id,
        "base_revision_hash": value.base_revision_hash,
        "source_checkpoint_id": value.source_checkpoint_id,
        "project_binding": dict(value.project_binding),
        "changes": [dict(item) for item in value.changes],
        "frontier_mapping": [dict(item) for item in value.frontier_mapping],
        "requested_by": dict(value.requested_by),
        "draft_version": value.draft_version,
        "status": value.status,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "published_manifest_id": value.published_manifest_id,
    }


def _validation_dict(value: RevisionValidation | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "validation_id": value.validation_id,
        "draft_id": value.draft_id,
        "draft_version": value.draft_version,
        "report_hash": value.report_hash,
        "report": dict(value.report),
        "compatible_checkpoint_id": value.compatible_checkpoint_id,
        "proof_hash": value.proof_hash,
        "created_at": value.created_at,
    }


def _approval_dict(value: RevisionApproval | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "approval_id": value.approval_id,
        "draft_id": value.draft_id,
        "draft_version": value.draft_version,
        "validation_id": value.validation_id,
        "validation_report_hash": value.validation_report_hash,
        "project_binding": dict(value.project_binding),
        "policy_version": value.policy_version,
        "status": value.status,
        "actor": dict(value.actor),
        "created_at": value.created_at,
    }


def _manifest_dict(value: RevisionManifest | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "schema_version": 1,
        "manifest_id": value.manifest_id,
        "revision_id": value.revision_id,
        "source_execution_id": value.source_execution_id,
        "source_checkpoint_id": value.source_checkpoint_id,
        "compatible_checkpoint_id": value.compatible_checkpoint_id,
        "parent_revision_id": value.parent_revision_id,
        "content_hash": value.content_hash,
        "manifest": dict(value.manifest),
        "validation_id": value.validation_id,
        "approval_id": value.approval_id,
        "proof_hash": value.proof_hash,
        "created_by": dict(value.created_by),
        "published_at": value.published_at,
    }


def project_draft_state(
    service: RevisionControlService, draft_id: str
) -> dict[str, Any]:
    state = service.draft_state(draft_id)
    if state is None:
        raise RevisionPublicError("not_found")
    draft, validation, approval, manifest = state
    return {
        "type": "revision.draft.state",
        "draft": _draft_dict(draft),
        "validation": _validation_dict(validation),
        "approval": _approval_dict(approval),
        "manifest": _manifest_dict(manifest),
    }


def validate_revision_request(command: Mapping[str, Any], action: str) -> str | None:
    """Validate the sole public revision command envelope before lookup."""
    if action not in _ACTIONS or not isinstance(command, Mapping):
        return "invalid_command"
    if command.get("type") != _TYPE or command.get("action") != action:
        return "invalid_command"
    execution_id = command.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id or len(execution_id) > 256:
        return "invalid_command"
    if action == "revision.draft.get":
        return (
            None
            if set(command) == {"type", "action", "execution_id", "draft_id"}
            and isinstance(command.get("draft_id"), str)
            and command["draft_id"]
            else "invalid_command"
        )
    allowed = {"type", "action", "execution_id", "expected_draft_version", "payload"}
    if action != "revision.draft.create":
        allowed.add("draft_id")
    if set(command) != allowed:
        return "invalid_command"
    version = command.get("expected_draft_version")
    if (
        type(version) is not int
        or version < 0
        or (action == "revision.draft.create" and version != 0)
        or (action != "revision.draft.create" and version == 0)
    ):
        return "invalid_draft_version"
    if action != "revision.draft.create" and (
        not isinstance(command.get("draft_id"), str) or not command["draft_id"]
    ):
        return "invalid_command"
    payload = command.get("payload")
    if not isinstance(payload, Mapping):
        return "invalid_payload"
    required = {
        "revision.draft.create": {
            "source_checkpoint_id",
            "changes",
            "frontier_mapping",
        },
        "revision.draft.replace": {"changes", "frontier_mapping"},
        "revision.draft.discard": set(),
        "revision.validate": set(),
        "revision.approve": {"validation_id"},
        "revision.publish": {"validation_id", "approval_id"},
    }[action]
    if set(payload) != required:
        return "invalid_payload"
    if action == "revision.draft.create" and (
        not isinstance(payload["source_checkpoint_id"], str)
        or not payload["source_checkpoint_id"]
        or not isinstance(payload["changes"], list)
        or not isinstance(payload["frontier_mapping"], list)
    ):
        return "invalid_payload"
    if action == "revision.draft.replace" and (
        not isinstance(payload["changes"], list)
        or not isinstance(payload["frontier_mapping"], list)
    ):
        return "invalid_payload"
    if action in {"revision.approve", "revision.publish"} and not (
        isinstance(payload["validation_id"], str) and payload["validation_id"]
    ):
        return "invalid_payload"
    if action == "revision.publish" and not (
        isinstance(payload["approval_id"], str) and payload["approval_id"]
    ):
        return "invalid_payload"
    return None


def _authorize(
    store: ExecutionStore,
    actor: Mapping[str, Any],
    execution_id: str,
    *,
    action: str,
    bound_session: str | None,
) -> Any:
    execution = store.get_execution(execution_id)
    if execution is None or (
        bound_session is not None and bound_session != execution.session_id
    ):
        raise RevisionPublicError("not_found")
    binding = server_project_binding(store, execution)
    try:
        authorize_execution_action(
            actor,
            action,
            execution,
            {"project_id": binding["project_id"], "session_id": execution.session_id},
        )
    except ExecutionAuthorizationError as exc:
        raise RevisionPublicError("not_found") from exc
    return execution, binding


def submit_revision_request(
    store: ExecutionStore,
    command: Mapping[str, Any],
    action: str,
    *,
    actor: Mapping[str, Any] | Any,
    bound_session: str | None = None,
    surface: str,
) -> dict[str, Any]:
    """Apply exactly one authorized revision action and write its audit event."""
    invalid = validate_revision_request(command, action)
    if invalid is not None:
        raise RevisionPublicError(invalid)
    trusted_actor = normalize_authority(actor)
    if not trusted_actor:
        raise RevisionPublicError("not_found")
    execution_id = str(command["execution_id"])
    service = RevisionControlService(store)
    draft_id = command.get("draft_id")
    if action != "revision.draft.create":
        draft = service.get_draft(str(draft_id))
        if draft is None or draft.source_execution_id != execution_id:
            raise RevisionPublicError("not_found")
    execution, binding = _authorize(
        store,
        trusted_actor,
        execution_id,
        action=action,
        bound_session=bound_session,
    )
    if action != "revision.draft.create" and draft.project_binding != binding:
        raise RevisionPublicError("revision_binding_mismatch")
    payload = dict(command.get("payload") or {})
    version = int(command.get("expected_draft_version", 0))
    try:
        if action == "revision.draft.create":
            draft = service.create_draft(
                project_binding=binding,
                source_execution_id=execution.execution_id,
                base_revision_id=execution.revision_id,
                source_checkpoint_id=payload["source_checkpoint_id"],
                changes=payload["changes"],
                frontier_mapping=payload["frontier_mapping"],
                requested_by=_subject(trusted_actor),
            )
        elif action == "revision.draft.replace":
            draft = service.replace_draft(
                draft_id=str(draft_id),
                expected_draft_version=version,
                changes=payload["changes"],
                frontier_mapping=payload["frontier_mapping"],
                actor=_subject(trusted_actor),
            )
        elif action == "revision.draft.discard":
            draft = service.discard_draft(
                draft_id=str(draft_id),
                expected_draft_version=version,
                actor=_subject(trusted_actor),
            )
        elif action == "revision.validate":
            service.validate_draft(
                draft_id=str(draft_id), expected_draft_version=version
            )
        elif action == "revision.approve":
            service.approve_draft(
                draft_id=str(draft_id),
                expected_draft_version=version,
                validation_id=payload["validation_id"],
                actor=_subject(trusted_actor),
                policy_version=POLICY_VERSION,
            )
        elif action == "revision.publish":
            service.publish_draft(
                draft_id=str(draft_id),
                expected_draft_version=version,
                validation_id=payload["validation_id"],
                approval_id=payload["approval_id"],
                actor=_subject(trusted_actor),
            )
        else:  # revision.draft.get
            pass
        state = project_draft_state(
            service,
            draft.draft_id if action == "revision.draft.create" else str(draft_id),
        )
        result = "allowed"
        reason_code = None
    except ExecutionConflict as exc:
        result, reason_code = "rejected", exc.code
        try:
            store.append_audit_event(
                execution_id=execution.execution_id,
                actor=trusted_actor,
                action=action,
                result=result,
                surface=surface,
                payload=payload,
                draft_id=str(draft_id) if draft_id else None,
                source_version=execution.status_version,
                checkpoint_id=payload.get("source_checkpoint_id"),
                reason_code=reason_code,
                project_binding=binding,
            )
        finally:
            raise RevisionPublicError(reason_code) from exc
    store.append_audit_event(
        execution_id=execution.execution_id,
        actor=trusted_actor,
        action=action,
        result=result,
        surface=surface,
        payload=payload,
        draft_id=state["draft"]["draft_id"],
        source_version=execution.status_version,
        checkpoint_id=state["draft"]["source_checkpoint_id"],
        reason_code=reason_code,
        evidence_refs=tuple(
            item
            for item in (
                (state["validation"] or {}).get("validation_id"),
                (state["approval"] or {}).get("approval_id"),
                (state["manifest"] or {}).get("manifest_id"),
            )
            if item
        ),
        project_binding=binding,
    )
    return state


__all__ = [
    "RevisionPublicError",
    "project_draft_state",
    "server_project_binding",
    "submit_revision_request",
    "validate_revision_request",
]
