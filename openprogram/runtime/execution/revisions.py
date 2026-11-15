"""Durable revision drafts, validation reports, approvals, and manifests.

This module is deliberately the only writer for RevisionDraftV1 and
RevisionManifestV1.  A fork can name an already published manifest, never
client-supplied revision JSON or a guessed compatible prefix.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .checkpoints import CheckpointManifest, ExecutionCheckpointStore
from .model import _json, _snapshot_json, _thaw_json
from .store import ExecutionConflict, ExecutionStore
from .store.run_tables import TableHandle


_ARTIFACT_PREFIX = "revision-artifact://sha256/"
_HASH_SIZE = 64
_MAX_ARTIFACT_BYTES = 256 * 1024
_MAX_RATIONALE_BYTES = 4096
_CHANGE_KINDS = frozenset({
    "workflow", "prompt", "tool_contract", "model_policy", "output_schema", "program_artifact",
})
_RELATIONS = frozenset({"preserved", "replaced", "split", "merged", "removed"})
_BINDING_KEYS = frozenset({"project_id", "worktree_id", "root_identity", "source_commit"})
_FRONTIER_KEYS = frozenset({
    "step_id", "action_id", "contract_hash", "branch_path", "input_schema_hash",
    "output_schema_hash", "dependency_hash", "effect_contract_hash",
})
_MAPPING_KEYS = frozenset({
    "old_step_id", "new_step_id", "relation", "old_contract_hash", "new_contract_hash",
})


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _require_hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != _HASH_SIZE:
        raise ExecutionConflict("invalid_revision_schema", f"{field} must be a sha256 hash")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ExecutionConflict("invalid_revision_schema", f"{field} must be a sha256 hash") from exc
    return value


def _require_actor(actor: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(actor, Mapping) or set(actor) != {"subject"}:
        raise ExecutionConflict("invalid_revision_actor", "revision actor must contain only subject")
    subject = actor.get("subject")
    if not isinstance(subject, str) or not subject or len(subject) > 256:
        raise ExecutionConflict("invalid_revision_actor", "revision actor subject is invalid")
    return {"subject": subject}


def _require_binding(binding: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(binding, Mapping) or set(binding) != _BINDING_KEYS:
        raise ExecutionConflict("invalid_project_binding", "project binding has an invalid schema")
    result: dict[str, str] = {}
    for key in _BINDING_KEYS:
        value = binding.get(key)
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise ExecutionConflict("invalid_project_binding", f"project binding {key} is invalid")
        result[key] = value
    _require_hash(result["source_commit"], "project binding source_commit")
    return result


def _require_ref(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith(_ARTIFACT_PREFIX):
        raise ExecutionConflict("invalid_artifact_ref", "revision artifact ref is invalid")
    _require_hash(value[len(_ARTIFACT_PREFIX):], "artifact ref")
    return value


def _strict_artifact(kind: str, content: Mapping[str, Any]) -> dict[str, Any]:
    if kind == "prompt" and isinstance(content, Mapping) and set(content) == {"template_hash", "instructions"}:
        instructions = content["instructions"]
        if (not isinstance(instructions, str) or not instructions.strip()
                or len(instructions.encode("utf-8")) > 16384
                or content["template_hash"] != hashlib.sha256(instructions.encode("utf-8")).hexdigest()):
            raise ExecutionConflict("invalid_artifact", "Agent instructions must have bounded, hash-bound text")
        return dict(content)
    expected = {
        "workflow": {"graph_hash"},
        "prompt": {"template_hash"},
        "tool_contract": {"contract_hash"},
        "model_policy": {"policy_hash"},
        "output_schema": {"schema_hash"},
        "program_artifact": {"entrypoint", "source_hash"},
    }
    if kind not in expected or not isinstance(content, Mapping) or set(content) != expected[kind]:
        raise ExecutionConflict("invalid_artifact", f"{kind} artifact has an invalid schema")
    result = _snapshot_json(content)
    for key, value in result.items():
        if key == "entrypoint":
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ExecutionConflict("invalid_artifact", "program artifact entrypoint is invalid")
        else:
            _require_hash(value, f"artifact {key}")
    return result


@dataclass(frozen=True)
class RevisionArtifact:
    artifact_ref: str
    kind: str
    content_hash: str
    content: Mapping[str, Any]
    created_at: float


@dataclass(frozen=True)
class RevisionDraft:
    draft_id: str
    project_binding: Mapping[str, str]
    source_execution_id: str
    base_revision_id: str
    base_revision_hash: str
    source_checkpoint_id: str
    changes: tuple[Mapping[str, Any], ...]
    frontier_mapping: tuple[Mapping[str, Any], ...]
    requested_by: Mapping[str, str]
    draft_version: int
    status: str
    created_at: float
    updated_at: float
    published_manifest_id: str | None = None


@dataclass(frozen=True)
class RevisionValidation:
    validation_id: str
    draft_id: str
    draft_version: int
    report_hash: str
    report: Mapping[str, Any]
    compatible_checkpoint_id: str
    proof_hash: str
    created_at: float


@dataclass(frozen=True)
class RevisionApproval:
    approval_id: str
    draft_id: str
    draft_version: int
    validation_id: str
    validation_report_hash: str
    project_binding: Mapping[str, str]
    policy_version: str
    status: str
    actor: Mapping[str, str]
    created_at: float


@dataclass(frozen=True)
class RevisionManifest:
    manifest_id: str
    revision_id: str
    source_execution_id: str
    source_checkpoint_id: str
    compatible_checkpoint_id: str
    parent_revision_id: str
    content_hash: str
    manifest: Mapping[str, Any]
    validation_id: str
    approval_id: str
    proof_hash: str
    created_by: Mapping[str, str]
    published_at: float


class RevisionControlService:
    """The canonical state machine for revision editing and publication."""

    def __init__(self, executions: ExecutionStore):
        self.executions = executions
        self.checkpoints = ExecutionCheckpointStore(executions)

    def prepare_agent_instructions(self, execution_id: str, checkpoint_id: str,
                                   instructions: str, rationale: str = "") -> list[dict[str, Any]]:
        """Prepare real instruction artifacts from a validated source checkpoint."""
        source = self.executions.get_execution(execution_id)
        checkpoint = self.checkpoints.get(checkpoint_id)
        if (source is None or checkpoint is None or checkpoint.execution_id != execution_id
                or checkpoint.revision_id != source.revision_id):
            raise ExecutionConflict("invalid_checkpoint", "instruction branch requires its source checkpoint")
        payload = self.executions.get_agent_turn_input(execution_id)
        if not payload or payload.get("kind") != "chat":
            raise ExecutionConflict("unsupported_instruction_branch", "instruction editing requires an Agent chat checkpoint")
        if not isinstance(rationale, str) or len(rationale.encode("utf-8")) > _MAX_RATIONALE_BYTES:
            raise ExecutionConflict("invalid_revision_schema", "revision rationale is invalid")
        program_content = self._agent_program_content(checkpoint)
        prompt_content = _strict_artifact("prompt", {
            "instructions": instructions,
            "template_hash": hashlib.sha256(instructions.encode("utf-8")).hexdigest()
            if isinstance(instructions, str) else "",
        })
        program = self.put_artifact(kind="program_artifact", content=program_content)
        prompt = self.put_artifact(kind="prompt", content=prompt_content)
        base = self.executions.get_revision(source.revision_id)
        if base is None:
            raise ExecutionConflict("base_revision_mismatch", "source revision is unavailable")
        return [
            {"kind": "program_artifact", "target": "agent.program", "before_hash": base.content_hash,
             "after_ref": program.artifact_ref, "rationale": rationale},
            {"kind": "prompt", "target": "agent.additional_instructions", "before_hash": program_content["source_hash"],
             "after_ref": prompt.artifact_ref, "rationale": rationale},
        ]

    def _agent_program_content(self, checkpoint: CheckpointManifest) -> dict[str, str]:
        source = self.executions.get_execution_input(checkpoint.execution_id)
        if source is None or source.entrypoint != "openprogram.runtime.conversation.production_driver:AgentProductionDriver":
            raise ExecutionConflict("unsupported_instruction_branch", "checkpoint has no Agent program")
        return {"entrypoint": source.entrypoint, "source_hash": self._agent_snapshot_hash(checkpoint)}

    def _agent_snapshot_hash(self, checkpoint: CheckpointManifest) -> str:
        """Identify the model, prompt and tools the checkpoint's turn resolved."""
        import hashlib
        from .agent_journal import load_journal
        from .runtime_contract import canonical_json_bytes

        pointer = checkpoint.state_refs.get("agent_journal")
        execution = self.executions.get_execution(checkpoint.execution_id)
        if not isinstance(pointer, Mapping) or execution is None or not execution.session_id:
            raise ExecutionConflict("checkpoint_schema_invalid", "checkpoint names no journal position")
        state = load_journal(execution.session_id, pointer)
        if not state.snapshot:
            raise ExecutionConflict("checkpoint_schema_invalid", "journal has no resolved snapshot")
        return hashlib.sha256(canonical_json_bytes(state.snapshot)).hexdigest()

    def instruction_editor(self, changes: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        if len(changes) != 2 or {item["target"] for item in changes} != {"agent.program", "agent.additional_instructions"}:
            return None
        change = next(item for item in changes if item["target"] == "agent.additional_instructions")
        with self.executions._connect() as connection:
            row = connection.get("revision_artifacts", change["after_ref"])
        if row is None or row["kind"] != "prompt":
            return None
        content = self._artifact(row).content
        _strict_artifact("prompt", content)
        if "instructions" not in content:
            return None
        return {"kind": "agent_instructions", "instructions": content["instructions"],
                "rationale": change["rationale"]}

    def published_instructions(self, revision_id: str) -> tuple[RevisionManifest, str] | None:
        """Read only text bound to the immutable published revision."""
        with self.executions._connect() as connection:
            row = connection.first("revision_manifests", revision_id=revision_id)
            if row is None:
                return None
            manifest = self._manifest(row)
            validation = connection.get("revision_validations", manifest.validation_id)
            draft = self.get_draft(validation["draft_id"]) if validation else None
            editor = self.instruction_editor(draft.changes) if draft else None
            return (manifest, editor["instructions"]) if editor else None

    def instruction_branch(self, connection, execution, checkpoint) -> tuple[RevisionManifest, str] | None:
        published = self.published_instructions(execution.revision_id)
        if published is None or execution.source_checkpoint_id != checkpoint.checkpoint_id:
            return None
        manifest, instructions = published
        if (execution.parent_execution_id != manifest.source_execution_id
                or checkpoint.execution_id != manifest.source_execution_id
                or checkpoint.revision_id != manifest.parent_revision_id):
            raise ExecutionConflict("revision_manifest_source_mismatch", "instruction branch source is invalid")
        self.require_fork_manifest(connection, manifest_id=manifest.manifest_id,
            source_execution_id=manifest.source_execution_id, checkpoint_id=checkpoint.checkpoint_id,
            proof_hash=manifest.proof_hash)
        return manifest, instructions

    def put_artifact(self, *, kind: str, content: Mapping[str, Any]) -> RevisionArtifact:
        value = _strict_artifact(kind, content)
        encoded = _json(value)
        if len(encoded.encode("utf-8")) > _MAX_ARTIFACT_BYTES:
            raise ExecutionConflict("artifact_too_large", "revision artifact exceeds the size limit")
        content_hash = _hash({"kind": kind, "content": value})
        artifact_ref = f"{_ARTIFACT_PREFIX}{content_hash}"
        with self.executions._transaction() as connection:
            row = connection.get("revision_artifacts", artifact_ref)
            if row is None:
                now = time.time()
                connection.insert("revision_artifacts", {
                    "artifact_ref": artifact_ref, "kind": kind, "content_hash": content_hash,
                    "content_json": encoded, "created_at": now,
                })
                return RevisionArtifact(artifact_ref, kind, content_hash, value, now)
            return self._artifact(row)

    def create_draft(
        self,
        *,
        project_binding: Mapping[str, Any],
        source_execution_id: str,
        base_revision_id: str,
        source_checkpoint_id: str,
        changes: Sequence[Mapping[str, Any]],
        frontier_mapping: Sequence[Mapping[str, Any]],
        requested_by: Mapping[str, Any],
    ) -> RevisionDraft:
        binding = _require_binding(project_binding)
        actor = _require_actor(requested_by)
        normalized_changes = self._changes(changes)
        normalized_mapping = self._mapping(frontier_mapping)
        with self.executions._transaction() as connection:
            source = self.executions._require_execution(connection, source_execution_id)
            base = self.executions._get_revision(connection, base_revision_id)
            if base is None or source.revision_id != base_revision_id:
                raise ExecutionConflict("base_revision_mismatch", "draft base revision does not match source execution")
            checkpoint = self.checkpoints._get(connection, source_checkpoint_id)
            if checkpoint is None or checkpoint.execution_id != source_execution_id or checkpoint.revision_id != base_revision_id:
                raise ExecutionConflict("invalid_checkpoint", "draft checkpoint does not belong to source execution")
            self._require_artifacts(connection, normalized_changes)
            now = time.time()
            draft = RevisionDraft(
                draft_id=f"draft_{uuid.uuid4().hex}", project_binding=binding,
                source_execution_id=source_execution_id, base_revision_id=base_revision_id,
                base_revision_hash=base.content_hash, source_checkpoint_id=source_checkpoint_id,
                changes=tuple(normalized_changes), frontier_mapping=tuple(normalized_mapping),
                requested_by=actor, draft_version=1, status="draft", created_at=now, updated_at=now,
            )
            connection.insert("revision_drafts", {
                "draft_id": draft.draft_id, "project_binding_json": _json(binding),
                "source_execution_id": source_execution_id, "base_revision_id": base_revision_id,
                "base_revision_hash": base.content_hash, "source_checkpoint_id": source_checkpoint_id,
                "changes_json": _json(normalized_changes), "frontier_mapping_json": _json(normalized_mapping),
                "requested_by_json": _json(actor), "draft_version": 1, "status": "draft",
                "created_at": now, "updated_at": now, "published_manifest_id": None,
            })
            return draft

    def validate_draft(self, *, draft_id: str, expected_draft_version: int) -> RevisionValidation:
        with self.executions._transaction() as connection:
            draft = self._require_draft(connection, draft_id, expected_draft_version)
            existing = connection.first("revision_validations", draft_id=draft_id, draft_version=expected_draft_version)
            if existing is not None:
                return self._validation(existing)
            self._verify_draft_binding(connection, draft)
            checkpoint, proof = self._compatible_checkpoint_and_proof(connection, draft)
            # Instruction-only branches have already proved the original program and
            # runtime unchanged; they have the same authority as a user steer.
            requires_approval = self.instruction_editor(draft.changes) is None and any(
                change["kind"] in {"tool_contract", "model_policy", "output_schema", "program_artifact"}
                for change in draft.changes
            )
            report = {
                "schema_version": 1,
                "draft_id": draft.draft_id,
                "draft_version": draft.draft_version,
                "project_binding": dict(draft.project_binding),
                "source_execution_id": draft.source_execution_id,
                "source_checkpoint_id": draft.source_checkpoint_id,
                "compatible_checkpoint_id": checkpoint.checkpoint_id,
                "compatible_prefix_proof": proof,
                "requires_approval": requires_approval,
                "approval_status": "pending" if requires_approval else "not_required",
            }
            report_hash = _hash(report)
            proof_hash = _hash(proof)
            now = time.time()
            validation = RevisionValidation(
                validation_id=f"validation_{uuid.uuid4().hex}", draft_id=draft.draft_id,
                draft_version=draft.draft_version, report_hash=report_hash, report=report,
                compatible_checkpoint_id=checkpoint.checkpoint_id, proof_hash=proof_hash, created_at=now,
            )
            connection.insert("revision_validations", {
                "validation_id": validation.validation_id, "draft_id": draft.draft_id,
                "draft_version": draft.draft_version, "report_hash": report_hash, "report_json": _json(report),
                "compatible_checkpoint_id": checkpoint.checkpoint_id, "proof_hash": proof_hash,
                "status": "valid", "created_at": now,
            })
            return validation

    def replace_draft(
        self,
        *, draft_id: str, expected_draft_version: int, changes: Sequence[Mapping[str, Any]],
        frontier_mapping: Sequence[Mapping[str, Any]], actor: Mapping[str, Any],
    ) -> RevisionDraft:
        """Replace a mutable draft version; published drafts are never changed."""
        editor = _require_actor(actor)
        normalized_changes = self._changes(changes)
        normalized_mapping = self._mapping(frontier_mapping)
        with self.executions._transaction() as connection:
            draft = self._require_draft(connection, draft_id, expected_draft_version)
            if editor != draft.requested_by:
                raise ExecutionConflict("draft_authorization_denied", "only the draft author can edit the draft")
            self._require_artifacts(connection, normalized_changes)
            now = time.time()
            next_version = draft.draft_version + 1
            connection.update("revision_drafts", {
                "changes_json": _json(normalized_changes), "frontier_mapping_json": _json(normalized_mapping),
                "draft_version": next_version, "updated_at": now,
            }, draft_id=draft_id)
            return RevisionDraft(
                draft_id=draft.draft_id, project_binding=draft.project_binding,
                source_execution_id=draft.source_execution_id, base_revision_id=draft.base_revision_id,
                base_revision_hash=draft.base_revision_hash, source_checkpoint_id=draft.source_checkpoint_id,
                changes=tuple(normalized_changes), frontier_mapping=tuple(normalized_mapping),
                requested_by=draft.requested_by, draft_version=next_version, status="draft",
                created_at=draft.created_at, updated_at=now,
            )

    def discard_draft(
        self, *, draft_id: str, expected_draft_version: int, actor: Mapping[str, Any]
    ) -> RevisionDraft:
        actor_value = _require_actor(actor)
        with self.executions._transaction() as connection:
            draft = self._require_draft(connection, draft_id, expected_draft_version)
            if actor_value != draft.requested_by:
                raise ExecutionConflict("draft_authorization_denied", "only the draft author can discard the draft")
            now = time.time()
            connection.update("revision_drafts", {"status": "discarded", "updated_at": now}, draft_id=draft_id)
            return RevisionDraft(
                draft_id=draft.draft_id, project_binding=draft.project_binding,
                source_execution_id=draft.source_execution_id, base_revision_id=draft.base_revision_id,
                base_revision_hash=draft.base_revision_hash, source_checkpoint_id=draft.source_checkpoint_id,
                changes=draft.changes, frontier_mapping=draft.frontier_mapping,
                requested_by=draft.requested_by, draft_version=draft.draft_version,
                status="discarded", created_at=draft.created_at, updated_at=now,
            )

    def approve_draft(
        self,
        *, draft_id: str, expected_draft_version: int, validation_id: str,
        actor: Mapping[str, Any], policy_version: str,
    ) -> RevisionApproval:
        approver = _require_actor(actor)
        if not isinstance(policy_version, str) or not policy_version or len(policy_version) > 256:
            raise ExecutionConflict("invalid_policy_version", "policy version is invalid")
        with self.executions._transaction() as connection:
            draft = self._require_draft(connection, draft_id, expected_draft_version)
            validation = self._require_validation(connection, validation_id, draft)
            existing = connection.first("revision_approvals", draft_id=draft_id, draft_version=expected_draft_version)
            if existing is not None:
                approval = self._approval(existing)
                if approval.validation_id != validation_id or approval.actor != approver or approval.policy_version != policy_version:
                    raise ExecutionConflict("approval_collision", "approval already exists with different fields")
                return approval
            requires_approval = bool(validation.report["requires_approval"])
            if requires_approval and approver["subject"] == draft.requested_by["subject"]:
                raise ExecutionConflict("self_approval_forbidden", "draft author cannot approve a sensitive revision")
            now = time.time()
            status = "approved" if requires_approval else "not_required"
            approval = RevisionApproval(
                approval_id=f"approval_{uuid.uuid4().hex}", draft_id=draft_id,
                draft_version=draft.draft_version, validation_id=validation_id,
                validation_report_hash=validation.report_hash, project_binding=draft.project_binding,
                policy_version=policy_version, status=status, actor=approver, created_at=now,
            )
            connection.insert("revision_approvals", {
                "approval_id": approval.approval_id, "draft_id": draft_id, "draft_version": draft.draft_version,
                "validation_id": validation_id, "validation_report_hash": validation.report_hash,
                "project_binding_json": _json(draft.project_binding), "policy_version": policy_version,
                "status": status, "actor_json": _json(approver), "created_at": now,
            })
            return approval

    def publish_draft(
        self,
        *, draft_id: str, expected_draft_version: int, validation_id: str,
        approval_id: str, actor: Mapping[str, Any],
    ) -> RevisionManifest:
        publisher = _require_actor(actor)
        with self.executions._transaction() as connection:
            draft = self._require_draft(connection, draft_id, expected_draft_version)
            validation = self._require_validation(connection, validation_id, draft)
            approval = self._require_approval(connection, approval_id, draft, validation)
            self._verify_draft_binding(connection, draft)
            checkpoint, proof = self._compatible_checkpoint_and_proof(connection, draft)
            if checkpoint.checkpoint_id != validation.compatible_checkpoint_id or _hash(proof) != validation.proof_hash:
                raise ExecutionConflict("validation_stale", "draft validation no longer matches the source frontier")
            existing_id = draft.published_manifest_id
            if existing_id:
                existing = self._get_manifest(connection, existing_id)
                if existing is None:
                    raise ExecutionConflict("manifest_missing", "published draft lost its manifest")
                return existing
            runtime = self._runtime_contract(connection, draft)
            manifest_data = {
                "schema_version": 1,
                "parent_revision_id": draft.base_revision_id,
                "program_artifact_ref": next(change["after_ref"] for change in draft.changes if change["kind"] == "program_artifact"),
                "resolved_runtime_contract_ref": runtime.artifact_ref,
                "project_binding": dict(draft.project_binding),
                "frontier_mapping": list(draft.frontier_mapping),
                "compatible_prefix_proof": proof,
                "validation_report_ref": validation.validation_id,
                "approval_ref": approval.approval_id,
                "created_by": publisher,
            }
            content_hash = _hash(manifest_data)
            revision = self.executions._create_revision_in_transaction(
                connection, manifest={"schema_version": 1, "revision_manifest_hash": content_hash},
                revision_id=f"rev_{content_hash[:32]}", parent_revision_id=draft.base_revision_id,
            )
            now = time.time()
            result = RevisionManifest(
                manifest_id=f"manifest_{uuid.uuid4().hex}", revision_id=revision.revision_id,
                source_execution_id=draft.source_execution_id, source_checkpoint_id=draft.source_checkpoint_id,
                compatible_checkpoint_id=checkpoint.checkpoint_id, parent_revision_id=draft.base_revision_id,
                content_hash=content_hash, manifest=manifest_data, validation_id=validation.validation_id,
                approval_id=approval.approval_id, proof_hash=validation.proof_hash,
                created_by=publisher, published_at=now,
            )
            connection.insert("revision_manifests", {
                "manifest_id": result.manifest_id, "revision_id": result.revision_id,
                "source_execution_id": result.source_execution_id, "source_checkpoint_id": result.source_checkpoint_id,
                "compatible_checkpoint_id": result.compatible_checkpoint_id,
                "parent_revision_id": result.parent_revision_id, "content_hash": result.content_hash,
                "manifest_json": _json(manifest_data), "validation_id": validation.validation_id,
                "approval_id": approval.approval_id, "proof_hash": result.proof_hash,
                "created_by_json": _json(publisher), "published_at": now,
            })
            connection.update("revision_drafts", {
                "status": "published", "published_manifest_id": result.manifest_id, "updated_at": now,
            }, draft_id=draft_id)
            return result

    def get_manifest(self, manifest_id: str) -> RevisionManifest | None:
        with self.executions._connect() as connection:
            return self._get_manifest(connection, manifest_id)

    def get_draft(self, draft_id: str) -> RevisionDraft | None:
        """Read a draft without treating its terminal status as an error."""
        with self.executions._connect() as connection:
            row = connection.get("revision_drafts", draft_id)
            return self._draft(row) if row is not None else None

    def list_drafts_for_execution(self, execution_id: str) -> list[RevisionDraft]:
        """Return all revision drafts rooted at one source execution."""
        with self.executions._connect() as connection:
            rows = connection.rows("revision_drafts", source_execution_id=execution_id, order=("created_at", "draft_id"))
            return [self._draft(row) for row in rows]

    def get_validation(self, validation_id: str) -> RevisionValidation | None:
        with self.executions._connect() as connection:
            row = connection.get("revision_validations", validation_id)
            return self._validation(row) if row is not None else None

    def get_approval(self, approval_id: str) -> RevisionApproval | None:
        with self.executions._connect() as connection:
            row = connection.get("revision_approvals", approval_id)
            return self._approval(row) if row is not None else None

    def draft_state(
        self, draft_id: str
    ) -> tuple[RevisionDraft, RevisionValidation | None, RevisionApproval | None, RevisionManifest | None] | None:
        """Return the canonical objects a debugger needs for one draft.

        This is read-only and deliberately includes terminal drafts.  Public
        callers still authorize the source execution before receiving it.
        """
        with self.executions._connect() as connection:
            row = connection.get("revision_drafts", draft_id)
            if row is None:
                return None
            draft = self._draft(row)
            validation_row = connection.first("revision_validations", draft_id=draft_id, draft_version=draft.draft_version, order="-created_at")
            approval_row = connection.first("revision_approvals", draft_id=draft_id, draft_version=draft.draft_version, order="-created_at")
            manifest = (
                self._get_manifest(connection, draft.published_manifest_id)
                if draft.published_manifest_id else None
            )
            return (
                draft,
                self._validation(validation_row) if validation_row is not None else None,
                self._approval(approval_row) if approval_row is not None else None,
                manifest,
            )

    def require_fork_manifest(
        self, connection: TableHandle, *, manifest_id: str, source_execution_id: str,
        checkpoint_id: str, proof_hash: str,
    ) -> RevisionManifest:
        if not isinstance(manifest_id, str) or not manifest_id.startswith("manifest_"):
            raise ExecutionConflict("revision_manifest_not_found", "revision manifest is not published")
        _require_hash(proof_hash, "proof_hash")
        manifest = self._get_manifest(connection, manifest_id)
        if manifest is None:
            raise ExecutionConflict("revision_manifest_not_found", "revision manifest is not published")
        if manifest.source_execution_id != source_execution_id:
            raise ExecutionConflict("revision_manifest_source_mismatch", "revision manifest belongs to another execution")
        if manifest.compatible_checkpoint_id != checkpoint_id:
            raise ExecutionConflict("revision_manifest_checkpoint_mismatch", "revision manifest checkpoint does not match")
        if manifest.proof_hash != proof_hash:
            raise ExecutionConflict("revision_manifest_proof_mismatch", "revision manifest proof does not match")
        validation_row = connection.get("revision_validations", manifest.validation_id)
        if validation_row is None:
            raise ExecutionConflict("revision_manifest_invalid", "revision manifest validation is missing")
        validation = self._validation(validation_row)
        draft_row = connection.get("revision_drafts", validation.draft_id)
        if draft_row is None:
            raise ExecutionConflict("revision_manifest_invalid", "revision manifest draft is missing")
        draft = self._draft(draft_row)
        if draft.status != "published":
            raise ExecutionConflict("revision_manifest_invalid", "revision manifest draft is not published")
        self._verify_draft_binding(connection, draft)
        checkpoint, proof = self._compatible_checkpoint_and_proof(connection, draft)
        if checkpoint.checkpoint_id != manifest.compatible_checkpoint_id or _hash(proof) != manifest.proof_hash:
            raise ExecutionConflict("revision_manifest_invalid", "revision manifest proof is stale")
        approval = self._require_approval(connection, manifest.approval_id, draft, validation)
        if approval.status not in {"approved", "not_required"}:
            raise ExecutionConflict("approval_required", "revision manifest approval is unavailable")
        if self.executions._get_revision(connection, manifest.revision_id) is None:
            raise ExecutionConflict("revision_manifest_invalid", "revision manifest revision is missing")
        return manifest

    def _changes(self, values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values or len(values) > 128:
            raise ExecutionConflict("invalid_revision_schema", "revision draft requires bounded changes")
        result: list[dict[str, Any]] = []
        program_count = 0
        for value in values:
            if not isinstance(value, Mapping) or set(value) != {"kind", "target", "before_hash", "after_ref", "rationale"}:
                raise ExecutionConflict("invalid_revision_schema", "revision change has an invalid schema")
            kind, target, rationale = value["kind"], value["target"], value["rationale"]
            if kind not in _CHANGE_KINDS or not isinstance(target, str) or not target or len(target) > 512:
                raise ExecutionConflict("invalid_revision_schema", "revision change target is invalid")
            if not isinstance(rationale, str) or len(rationale.encode("utf-8")) > _MAX_RATIONALE_BYTES:
                raise ExecutionConflict("invalid_revision_schema", "revision rationale is invalid")
            result.append({"kind": kind, "target": target, "before_hash": _require_hash(value["before_hash"], "before_hash"), "after_ref": _require_ref(value["after_ref"]), "rationale": rationale})
            program_count += kind == "program_artifact"
        if program_count != 1:
            raise ExecutionConflict("program_artifact_required", "revision requires exactly one program artifact")
        return result

    def _mapping(self, values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or len(values) > 4096:
            raise ExecutionConflict("invalid_frontier_mapping", "frontier mapping is invalid")
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, Mapping) or set(value) != _MAPPING_KEYS:
                raise ExecutionConflict("invalid_frontier_mapping", "frontier mapping entry has an invalid schema")
            old, new, relation = value["old_step_id"], value["new_step_id"], value["relation"]
            if not isinstance(old, str) or not old or old in seen or relation not in _RELATIONS:
                raise ExecutionConflict("invalid_frontier_mapping", "frontier mapping entry is invalid")
            if relation == "removed":
                if new is not None or value["new_contract_hash"] is not None:
                    raise ExecutionConflict("invalid_frontier_mapping", "removed step must not name a replacement")
            elif not isinstance(new, str) or not new or not isinstance(value["new_contract_hash"], str):
                raise ExecutionConflict("invalid_frontier_mapping", "mapped step requires a new contract")
            result.append({"old_step_id": old, "new_step_id": new, "relation": relation,
                           "old_contract_hash": _require_hash(value["old_contract_hash"], "old_contract_hash"),
                           "new_contract_hash": _require_hash(value["new_contract_hash"], "new_contract_hash") if value["new_contract_hash"] is not None else None})
            seen.add(old)
        return result

    def _require_artifacts(self, connection: TableHandle, changes: Sequence[Mapping[str, Any]]) -> None:
        for change in changes:
            artifact = connection.get("revision_artifacts", change["after_ref"])
            if artifact is None or artifact["kind"] != change["kind"]:
                raise ExecutionConflict("artifact_not_found", "revision change artifact is unavailable or has the wrong type")

    def _verify_draft_binding(self, connection: TableHandle, draft: RevisionDraft) -> None:
        source = self.executions._require_execution(connection, draft.source_execution_id)
        base = self.executions._get_revision(connection, draft.base_revision_id)
        checkpoint = self.checkpoints._get(connection, draft.source_checkpoint_id)
        if base is None or base.content_hash != draft.base_revision_hash or source.revision_id != draft.base_revision_id:
            raise ExecutionConflict("base_revision_mismatch", "draft base revision no longer matches source execution")
        if checkpoint is None or checkpoint.execution_id != source.execution_id or checkpoint.revision_id != source.revision_id:
            raise ExecutionConflict("invalid_checkpoint", "draft checkpoint is unavailable")
        if self.executions._first_unresolved_effect(connection, source.execution_id) is not None:
            raise ExecutionConflict("unresolved_effect", "source execution has an unresolved effect")
        self._require_artifacts(connection, draft.changes)

    def _compatible_checkpoint_and_proof(self, connection: TableHandle, draft: RevisionDraft) -> tuple[CheckpointManifest, list[dict[str, Any]]]:
        checkpoint = self.checkpoints._get(connection, draft.source_checkpoint_id)
        assert checkpoint is not None
        if self.instruction_editor(draft.changes) is not None:
            return checkpoint, self._agent_instruction_proof(connection, draft, checkpoint)
        mapping = {item["old_step_id"]: item for item in draft.frontier_mapping}
        while checkpoint is not None:
            proof = self._proof(checkpoint, mapping)
            if proof is not None:
                return checkpoint, proof
            checkpoint = self.checkpoints._get(connection, checkpoint.parent_checkpoint_id) if checkpoint.parent_checkpoint_id else None
        raise ExecutionConflict("compatible_checkpoint_required", "no checkpoint has a proven compatible prefix")

    def _agent_instruction_proof(self, connection, draft, checkpoint) -> list[dict[str, Any]]:
        """Prove the runtime is unchanged and the checkpoint's journal is complete."""
        program = next(item for item in draft.changes if item["target"] == "agent.program")
        prompt = next(item for item in draft.changes if item["target"] == "agent.additional_instructions")
        content = self._agent_program_content(checkpoint)
        row = connection.get("revision_artifacts", program["after_ref"])
        if (program["kind"] != "program_artifact" or prompt["kind"] != "prompt"
                or program["before_hash"] != draft.base_revision_hash
                or prompt["before_hash"] != content["source_hash"]
                or row is None or self._artifact(row).content != content or draft.frontier_mapping):
            raise ExecutionConflict("invalid_instruction_revision", "instructions must preserve the Agent program and runtime")
        proof = [{"kind": "journal_checkpoint", "checkpoint_id": checkpoint.checkpoint_id,
                  "checkpoint_hash": checkpoint.content_hash,
                  "runtime_contract_hash": content["source_hash"], "reuse_decision": "reuse"}]
        return proof

    def _proof(self, checkpoint: CheckpointManifest, mapping: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]] | None:
        frontier = checkpoint.completed_frontier
        if frontier is None:
            raise ExecutionConflict("checkpoint_frontier_required", "revision fork requires a structured completed frontier")
        receipts = {str(item.get("action_id")): item for item in checkpoint.effect_receipts if isinstance(item, Mapping)}
        proof: list[dict[str, Any]] = []
        reusable = True
        for raw in frontier:
            if not isinstance(raw, Mapping) or set(raw) != _FRONTIER_KEYS:
                raise ExecutionConflict("checkpoint_frontier_required", "completed frontier lacks revision proof fields")
            item = _thaw_json(raw)
            if not isinstance(item["step_id"], str) or not isinstance(item["action_id"], str) or not isinstance(item["branch_path"], list) or not item["branch_path"] or not all(isinstance(part, str) and part for part in item["branch_path"]):
                raise ExecutionConflict("checkpoint_frontier_required", "completed frontier identity is invalid")
            for field in _FRONTIER_KEYS - {"step_id", "action_id", "branch_path"}:
                _require_hash(item[field], field)
            receipt = receipts.get(item["action_id"])
            if receipt is None or receipt.get("outcome") not in {"committed", "not_committed", "compensated"}:
                raise ExecutionConflict("checkpoint_effect_proof_required", "completed frontier is missing a terminal effect receipt")
            mapped = mapping.get(item["step_id"])
            same = bool(mapped and mapped["relation"] == "preserved" and mapped["new_step_id"] == item["step_id"] and mapped["old_contract_hash"] == item["contract_hash"] and mapped["new_contract_hash"] == item["contract_hash"])
            reusable = reusable and same
            proof.append({
                "branch_path": item["branch_path"], "old_step_id": item["step_id"],
                "new_step_id": mapped["new_step_id"] if mapped else None,
                "contract_hash": item["contract_hash"], "input_schema_hash": item["input_schema_hash"],
                "output_schema_hash": item["output_schema_hash"], "dependency_hash": item["dependency_hash"],
                "effect_contract_hash": item["effect_contract_hash"],
                "reuse_decision": "reuse" if reusable else "rerun",
            })
        return proof if reusable else None

    def _runtime_contract(self, connection: TableHandle, draft: RevisionDraft) -> RevisionArtifact:
        content = {"artifact_refs": [change["after_ref"] for change in draft.changes]}
        # The generated contract is schema-versioned and derives only from validated refs.
        content_hash = _hash({"kind": "runtime_contract", "content": content})
        ref = f"{_ARTIFACT_PREFIX}{content_hash}"
        row = connection.get("revision_artifacts", ref)
        if row is not None:
            return self._artifact(row)
        now = time.time()
        connection.insert("revision_artifacts", {
            "artifact_ref": ref, "kind": "runtime_contract", "content_hash": content_hash,
            "content_json": _json(content), "created_at": now,
        })
        return RevisionArtifact(ref, "runtime_contract", content_hash, content, now)

    def _require_draft(self, connection: TableHandle, draft_id: str, expected_version: int | None) -> RevisionDraft:
        row = connection.get("revision_drafts", draft_id)
        if row is None:
            raise ExecutionConflict("draft_not_found", "revision draft does not exist")
        draft = self._draft(row)
        if expected_version is not None and draft.draft_version != expected_version:
            raise ExecutionConflict("draft_version_conflict", "revision draft version is stale")
        if draft.status != "draft":
            raise ExecutionConflict("draft_not_editable", "revision draft is already published or discarded")
        return draft

    def _require_validation(self, connection: TableHandle, validation_id: str, draft: RevisionDraft) -> RevisionValidation:
        row = connection.get("revision_validations", validation_id)
        if row is None:
            raise ExecutionConflict("validation_not_found", "revision validation does not exist")
        result = self._validation(row)
        if result.draft_id != draft.draft_id or result.draft_version != draft.draft_version:
            raise ExecutionConflict("validation_stale", "revision validation does not match the draft")
        return result

    def _require_approval(self, connection: TableHandle, approval_id: str, draft: RevisionDraft, validation: RevisionValidation) -> RevisionApproval:
        row = connection.get("revision_approvals", approval_id)
        if row is None:
            raise ExecutionConflict("approval_not_found", "revision approval does not exist")
        result = self._approval(row)
        if result.draft_id != draft.draft_id or result.draft_version != draft.draft_version or result.validation_id != validation.validation_id or result.validation_report_hash != validation.report_hash or result.project_binding != draft.project_binding:
            raise ExecutionConflict("approval_stale", "revision approval does not match the draft validation")
        return result

    @staticmethod
    def _artifact(row: Mapping[str, Any]) -> RevisionArtifact:
        return RevisionArtifact(str(row["artifact_ref"]), str(row["kind"]), str(row["content_hash"]), _snapshot_json(json.loads(row["content_json"])), float(row["created_at"]))

    @staticmethod
    def _draft(row: Mapping[str, Any]) -> RevisionDraft:
        return RevisionDraft(str(row["draft_id"]), _snapshot_json(json.loads(row["project_binding_json"])), str(row["source_execution_id"]), str(row["base_revision_id"]), str(row["base_revision_hash"]), str(row["source_checkpoint_id"]), tuple(_snapshot_json(json.loads(row["changes_json"]))), tuple(_snapshot_json(json.loads(row["frontier_mapping_json"]))), _snapshot_json(json.loads(row["requested_by_json"])), int(row["draft_version"]), str(row["status"]), float(row["created_at"]), float(row["updated_at"]), row["published_manifest_id"])

    @staticmethod
    def _validation(row: Mapping[str, Any]) -> RevisionValidation:
        return RevisionValidation(str(row["validation_id"]), str(row["draft_id"]), int(row["draft_version"]), str(row["report_hash"]), _snapshot_json(json.loads(row["report_json"])), str(row["compatible_checkpoint_id"]), str(row["proof_hash"]), float(row["created_at"]))

    @staticmethod
    def _approval(row: Mapping[str, Any]) -> RevisionApproval:
        return RevisionApproval(str(row["approval_id"]), str(row["draft_id"]), int(row["draft_version"]), str(row["validation_id"]), str(row["validation_report_hash"]), _snapshot_json(json.loads(row["project_binding_json"])), str(row["policy_version"]), str(row["status"]), _snapshot_json(json.loads(row["actor_json"])), float(row["created_at"]))

    @staticmethod
    def _manifest(row: Mapping[str, Any]) -> RevisionManifest:
        return RevisionManifest(str(row["manifest_id"]), str(row["revision_id"]), str(row["source_execution_id"]), str(row["source_checkpoint_id"]), str(row["compatible_checkpoint_id"]), str(row["parent_revision_id"]), str(row["content_hash"]), _snapshot_json(json.loads(row["manifest_json"])), str(row["validation_id"]), str(row["approval_id"]), str(row["proof_hash"]), _snapshot_json(json.loads(row["created_by_json"])), float(row["published_at"]))

    def _get_manifest(self, connection: TableHandle, manifest_id: str) -> RevisionManifest | None:
        row = connection.get("revision_manifests", manifest_id)
        return self._manifest(row) if row is not None else None
