"""Execution persistence: state blobs."""
from __future__ import annotations

import hashlib

import json


import sqlite3

import time



from contextlib import closing



from typing import Any, Mapping



from ..model import TERMINAL_EXECUTION_STATUSES


from .shared import (
    ExecutionConflict,
    ExecutionStoreError,
    MAX_AGENT_STATE_BLOB_BYTES,
    _STATE_HASH_LENGTH,
    _STATE_REF_PREFIX,
)

class StateBlobsOperations:
    def put_state_blob(
        self,
        execution_id: str,
        payload: bytes | str,
        *,
        media_type: str = "application/json",
        schema_version: int = 1,
    ) -> dict[str, Any]:
        """Persist one execution-owned immutable state blob."""
        with self._transaction() as connection:
            return self._put_state_blob_in_transaction(
                connection,
                execution_id=execution_id,
                payload=payload,
                media_type=media_type,
                schema_version=schema_version,
            )


    def get_state_blob(self, execution_id: str, ref: str) -> dict[str, Any] | None:
        self._validate_state_ref(ref)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT ref, sha256, payload, byte_length, media_type, schema_version "
                "FROM execution_state_blobs WHERE execution_id = ? AND ref = ?",
                (execution_id, ref),
            ).fetchone()
        if row is None:
            return None
        payload = bytes(row["payload"])
        digest = hashlib.sha256(payload).hexdigest()
        if digest != row["sha256"] or len(payload) != int(row["byte_length"]):
            raise ExecutionStoreError(
                "state_blob_corrupt", "stored state blob failed integrity validation"
            )
        return {
            "ref": str(row["ref"]), "sha256": digest,
            "byte_length": len(payload), "media_type": str(row["media_type"]),
            "schema_version": int(row["schema_version"]), "payload": payload,
        }


    def gc_state_blobs(self, execution_id: str) -> int:
        """Remove only terminal, unreferenced state owned by this execution."""
        with self._transaction() as connection:
            execution = self._require_execution(connection, execution_id)
            if execution.status not in TERMINAL_EXECUTION_STATUSES:
                raise ExecutionConflict("state_gc_not_terminal", "state blobs remain while execution is nonterminal")
            referenced: set[str] = set()
            for row in connection.execute(
                "SELECT state_refs_json, effect_receipts_json FROM checkpoints WHERE execution_id = ?",
                (execution_id,),
            ):
                for raw in (row["state_refs_json"], row["effect_receipts_json"]):
                    self._collect_state_refs(json.loads(raw), referenced)
            self._expand_state_blob_refs(connection, execution_id, referenced)
            for row in connection.execute(
                "SELECT receipt_json FROM effects WHERE execution_id = ?", (execution_id,),
            ):
                self._collect_state_refs(json.loads(row["receipt_json"]), referenced)
            self._expand_state_blob_refs(connection, execution_id, referenced)
            if referenced:
                placeholders = ",".join("?" for _ in referenced)
                result = connection.execute(
                    f"DELETE FROM execution_state_blobs WHERE execution_id = ? AND ref NOT IN ({placeholders})",
                    (execution_id, *sorted(referenced)),
                )
            else:
                result = connection.execute(
                    "DELETE FROM execution_state_blobs WHERE execution_id = ?", (execution_id,)
                )
            return int(result.rowcount)


    @classmethod
    def _collect_state_refs(cls, value: Any, refs: set[str]) -> None:
        # AgentCheckpointV1 uses bare refs for well-known durable fields;
        # effect receipts use ``receipt_ref``.  Treat every valid-looking
        # execstate string as a reference, regardless of surrounding shape.
        if isinstance(value, str) and value.startswith(_STATE_REF_PREFIX):
            cls._validate_state_ref(value)
            refs.add(value)
            return
        if isinstance(value, Mapping):
            ref = value.get("ref")
            if isinstance(ref, str) and ref.startswith(_STATE_REF_PREFIX):
                cls._validate_state_ref(ref)
                refs.add(ref)
            for item in value.values():
                cls._collect_state_refs(item, refs)
        elif isinstance(value, list):
            for item in value:
                cls._collect_state_refs(item, refs)


    @classmethod
    def _expand_state_blob_refs(
        cls, connection: sqlite3.Connection, execution_id: str, refs: set[str],
    ) -> None:
        """Expand the child refs of the one versioned Agent checkpoint schema.

        A checkpoint manifest stores the Agent payload as one immutable blob;
        that payload stores descriptors for message deltas, snapshots, and
        receipts.  GC must resolve that schema before deciding which blobs are
        unreachable.  Other JSON state blobs remain opaque by design.
        """
        pending = list(refs)
        visited: set[str] = set()
        while pending:
            ref = pending.pop()
            if ref in visited:
                continue
            visited.add(ref)
            row = connection.execute(
                "SELECT payload, media_type, schema_version FROM execution_state_blobs "
                "WHERE execution_id = ? AND ref = ?",
                (execution_id, ref),
            ).fetchone()
            if row is None or row["media_type"] != "application/json" or int(row["schema_version"]) != 1:
                continue
            try:
                value = json.loads(bytes(row["payload"]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(value, Mapping) or value.get("schema_version") != 1:
                continue
            if not all(key in value for key in ("safe_point", "frontier", "turn", "current_decision", "state_refs")):
                continue
            before = len(refs)
            state_refs = value.get("state_refs")
            if isinstance(state_refs, Mapping):
                cls._collect_state_refs(state_refs, refs)
            if len(refs) > before:
                pending.extend(refs - visited)


    def get_agent_wait(self, execution_id: str, kind: str) -> None:
        """Agent P0 does not persist waits; this explicit query stays empty."""
        self.get_execution(execution_id)
        return None


    @staticmethod
    def _validate_state_ref(ref: str) -> None:
        if (
            not isinstance(ref, str)
            or not ref.startswith(_STATE_REF_PREFIX)
            or len(ref) != len(_STATE_REF_PREFIX) + _STATE_HASH_LENGTH
            or any(char not in "0123456789abcdef" for char in ref[len(_STATE_REF_PREFIX):])
        ):
            raise ExecutionConflict(
                "state_ref_invalid", "state ref must be an execstate sha256 reference"
            )


    def _put_state_blob_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        execution_id: str,
        payload: bytes | str,
        media_type: str,
        schema_version: int,
    ) -> dict[str, Any]:
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if not isinstance(payload, bytes):
            raise ExecutionConflict("state_ref_invalid", "state blob payload must be bytes or UTF-8 text")
        if len(payload) > MAX_AGENT_STATE_BLOB_BYTES:
            raise ExecutionConflict("state_blob_too_large", "Agent state blob exceeds the size limit")
        if not media_type or not isinstance(media_type, str) or type(schema_version) is not int or schema_version < 1:
            raise ExecutionConflict("state_ref_invalid", "state blob media type and schema version are required")
        self._require_execution(connection, execution_id)
        digest = hashlib.sha256(payload).hexdigest()
        ref = f"{_STATE_REF_PREFIX}{digest}"
        row = connection.execute(
            "SELECT sha256, payload, byte_length, media_type, schema_version "
            "FROM execution_state_blobs WHERE execution_id = ? AND ref = ?",
            (execution_id, ref),
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO execution_state_blobs "
                "(execution_id, ref, sha256, payload, byte_length, media_type, schema_version, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (execution_id, ref, digest, payload, len(payload), media_type, schema_version, time.time()),
            )
        elif (
            row["sha256"] != digest or bytes(row["payload"]) != payload
            or int(row["byte_length"]) != len(payload)
            or row["media_type"] != media_type or int(row["schema_version"]) != schema_version
        ):
            raise ExecutionConflict("state_ref_invalid", "state blob reference collides with different content")
        return {
            "ref": ref, "sha256": digest, "byte_length": len(payload),
            "media_type": media_type, "schema_version": schema_version,
        }

