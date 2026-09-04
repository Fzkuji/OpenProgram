"""Crash-safe file store for conversational self-update state."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import shutil
import stat
import threading
import time
import uuid
from typing import Any, Iterator, Mapping

from openprogram import _compat as file_lock
from openprogram.store.session.git_session import atomic_write_text

from .types import (
    SCHEMA_VERSION,
    ActiveUpdateError,
    ConcurrentUpdateError,
    CorruptUpdateStateError,
    InvalidTransitionError,
    SelfUpdateError,
    UpdateExistsError,
    UpdateNotFoundError,
    UpdatePhase,
    UpdateRecord,
    UpdateRequest,
    UpdateState,
    VerifierClaim,
    VerifierDispatch,
    can_transition,
    is_terminal,
    _validate_update_id,
)


_process_lock = threading.RLock()


class SelfUpdateStore:
    """Owns ``<state>/self-updates`` and its single active update slot."""

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            from openprogram.paths import get_state_dir

            root = get_state_dir() / "self-updates"
        self.root = Path(root)

    def create(self, request: UpdateRequest, *, verifier_config: Mapping[str, Any] | None = None,
               diagnosis_config: Mapping[str, Any] | None = None,
               source_repair_config: Mapping[str, Any] | None = None) -> UpdateState:
        with self._locked():
            maintenance = self.root / "maintenance.json"
            if maintenance.exists() or maintenance.is_symlink():
                raise ActiveUpdateError("self-update maintenance has not been cleared")
            current = self._load_active_unlocked()
            if current is not None and not is_terminal(current.state.phase):
                raise ActiveUpdateError(
                    f"active update {current.request.update_id} is {current.state.phase.value}"
                )
            target = self._update_dir(request.update_id)
            if target.exists():
                raise UpdateExistsError(f"update {request.update_id} already exists")

            now = time.time()
            created_event = {
                "schema": SCHEMA_VERSION,
                "at": now,
                "type": "created",
                "update_id": request.update_id,
                "phase": UpdatePhase.PREPARING.value,
                "revision": 1,
            }
            state = UpdateState(
                update_id=request.update_id,
                phase=UpdatePhase.PREPARING,
                revision=1,
                updated_at=now,
                last_event=created_event,
            )
            staged = self.root / f".{request.update_id}.{uuid.uuid4().hex}.tmp"
            staged.mkdir(mode=0o700)
            try:
                self._write_json(staged / "request.json", request.to_dict())
                if verifier_config is not None:
                    self._write_json(staged / "verifier-config.json", verifier_config)
                if diagnosis_config is not None:
                    self._write_json(staged / "diagnosis-config.json", diagnosis_config)
                if source_repair_config is not None:
                    self._write_json(staged / "source-repair-config.json", source_repair_config)
                self._write_json(staged / "state.json", state.to_dict())
                self._write_events(staged / "events.jsonl", created_event)
                os.replace(staged, target)
                self._fsync_directory(self.root)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
            self._write_json(
                self.root / "active.json",
                {"schema": SCHEMA_VERSION, "update_id": request.update_id},
            )
            from .diagnosis import cancel_pending
            try:
                cancel_pending(self)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Could not revoke superseded diagnosis")
            from .source_repair import cancel_pending as cancel_repair
            try:
                cancel_repair(self)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Could not revoke superseded source repair")
            return state

    def load(self, update_id: str) -> UpdateRecord:
        with self._locked():
            record = self._load_unlocked(update_id)
            if not is_terminal(record.state.phase):
                active = self._load_active_unlocked()
                if active is None or active.request.update_id != update_id:
                    raise CorruptUpdateStateError(
                        "non-terminal update does not own the active slot"
                    )
            return record

    def load_active(self) -> UpdateRecord | None:
        with self._locked():
            return self._load_active_unlocked()

    def transition(
        self,
        update_id: str,
        target: UpdatePhase,
        *,
        expected_phase: UpdatePhase | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> UpdateState:
        if not isinstance(target, UpdatePhase):
            raise ValueError("target must be an UpdatePhase")
        if expected_phase is not None and not isinstance(expected_phase, UpdatePhase):
            raise ValueError("expected_phase must be an UpdatePhase or None")
        with self._locked():
            record = self._load_unlocked(update_id)
            current = record.state
            if expected_phase is not None and current.phase is not expected_phase:
                raise ConcurrentUpdateError(
                    f"expected {expected_phase.value}, found {current.phase.value}"
                )
            if not can_transition(current.phase, target):
                raise InvalidTransitionError(
                    f"illegal update transition {current.phase.value} -> {target.value}"
                )
            now = time.time()
            event = {
                "schema": SCHEMA_VERSION,
                "at": now,
                "type": "transition",
                "update_id": update_id,
                "from": current.phase.value,
                "phase": target.value,
                "revision": current.revision + 1,
                "detail": dict(detail or {}),
            }
            updated = replace(
                current,
                phase=target,
                revision=current.revision + 1,
                updated_at=now,
                detail=dict(detail or {}),
                last_event=event,
            )
            self._write_json(self._state_path(update_id), updated.to_dict())
            self._write_events(self._events_path(update_id), event)
            if is_terminal(target):
                self._clear_active_unlocked(update_id)
            return updated

    def claim_verifier(
        self,
        update_id: str,
        *,
        owner: str,
        lease_seconds: float,
        now: float | None = None,
    ) -> VerifierClaim:
        if not isinstance(owner, str):
            raise ValueError("owner must be a string")
        owner = owner.strip()
        if not owner or len(owner) > 256:
            raise ValueError("owner is required and must be at most 256 characters")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, (int, float))
            or not math.isfinite(lease_seconds)
            or not 0 < lease_seconds <= 86400
        ):
            raise ValueError("lease_seconds must be finite and between 0 and 86400")
        now = time.time() if now is None else now
        if (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(now)
            or now < 0
        ):
            raise ValueError("now must be a finite non-negative timestamp")
        with self._locked():
            state = self._load_unlocked(update_id).state
            if state.phase is not UpdatePhase.VERIFYING:
                raise InvalidTransitionError(
                    f"verifier can only be claimed in verifying, found {state.phase.value}"
                )
            dispatch = state.dispatch
            job_id = (
                dispatch.job_id
                if dispatch is not None
                else f"self-update:{update_id}:verify:{state.attempt}"
            )
            if dispatch is not None and dispatch.lease_until > now:
                return VerifierClaim(
                    acquired=False,
                    job_id=job_id,
                    generation=dispatch.generation,
                    lease_until=dispatch.lease_until,
                )
            generation = 1 if dispatch is None else dispatch.generation + 1
            lease_until = now + float(lease_seconds)
            claimed = VerifierDispatch(
                job_id=job_id,
                claimed_by=owner,
                lease_until=lease_until,
                generation=generation,
            )
            event = {
                "schema": SCHEMA_VERSION,
                "at": now,
                "type": "verifier_claimed",
                "update_id": update_id,
                "phase": state.phase.value,
                "revision": state.revision + 1,
                "job_id": job_id,
                "owner": owner,
                "generation": generation,
                "lease_until": lease_until,
            }
            updated = replace(
                state,
                revision=state.revision + 1,
                updated_at=now,
                dispatch=claimed,
                last_event=event,
            )
            self._write_json(self._state_path(update_id), updated.to_dict())
            self._write_events(self._events_path(update_id), event)
            return VerifierClaim(True, job_id, generation, lease_until)

    def _update_dir(self, update_id: str) -> Path:
        return self.root / _validate_update_id(update_id)

    def _state_path(self, update_id: str) -> Path:
        return self._update_dir(update_id) / "state.json"

    def _events_path(self, update_id: str) -> Path:
        return self._update_dir(update_id) / "events.jsonl"

    def _load_unlocked(self, update_id: str, *, read_only: bool = False) -> UpdateRecord:
        directory = self._update_dir(update_id)
        if not directory.is_dir():
            raise UpdateNotFoundError(f"update {update_id} does not exist")
        request = UpdateRequest.from_dict(self._read_json(directory / "request.json"))
        state = UpdateState.from_dict(self._read_json(directory / "state.json"))
        if request.update_id != update_id or state.update_id != update_id:
            raise CorruptUpdateStateError("update id does not match its directory")
        record = UpdateRecord(request, state)
        self._reconcile_events_unlocked(record, read_only=read_only)
        return record

    def _load_active_unlocked(self) -> UpdateRecord | None:
        path = self.root / "active.json"
        if not path.exists():
            discovered = self._discover_nonterminal_unlocked()
            if discovered is None:
                return None
            self._write_json(
                path,
                {"schema": SCHEMA_VERSION, "update_id": discovered.request.update_id},
            )
            return discovered
        data = self._read_json(path)
        if set(data) != {"schema", "update_id"} or data.get("schema") != SCHEMA_VERSION:
            raise CorruptUpdateStateError("unsupported or malformed active schema")
        update_id = data.get("update_id")
        if not isinstance(update_id, str):
            raise CorruptUpdateStateError("active update_id must be a string")
        try:
            record = self._load_unlocked(update_id)
        except (ValueError, UpdateNotFoundError) as exc:
            raise CorruptUpdateStateError(
                "active.json points to an invalid or missing update"
            ) from exc
        if is_terminal(record.state.phase):
            self._clear_active_unlocked(update_id)
            return None
        return record

    def _discover_nonterminal_unlocked(self) -> UpdateRecord | None:
        records: list[UpdateRecord] = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                record = self._load_unlocked(directory.name)
            except UpdateNotFoundError:
                continue
            if not is_terminal(record.state.phase):
                records.append(record)
        if len(records) > 1:
            raise CorruptUpdateStateError("multiple non-terminal self-updates exist")
        return records[0] if records else None

    def _reconcile_events_unlocked(self, record: UpdateRecord, *, read_only: bool = False) -> None:
        path = self._events_path(record.request.update_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CorruptUpdateStateError("cannot read events.jsonl") from exc
        lines = raw.splitlines()
        complete_count = len(lines) if raw.endswith("\n") else max(0, len(lines) - 1)
        events: list[dict[str, Any]] = []
        previous_phase: UpdatePhase | None = None
        for line in lines[:complete_count]:
            try:
                event = self._loads_json(line)
            except (json.JSONDecodeError, ValueError) as exc:
                raise CorruptUpdateStateError(
                    "events.jsonl contains invalid JSON"
                ) from exc
            previous_phase = self._validate_event(
                event,
                record.request.update_id,
                len(events) + 1,
                previous_phase=previous_phase,
            )
            events.append(event)

        state = record.state
        if not state.last_event:
            raise CorruptUpdateStateError("state is missing its recovery event")
        last_revision = events[-1]["revision"] if events else 0
        if last_revision == state.revision and events[-1] == dict(state.last_event):
            if complete_count != len(lines):
                raise CorruptUpdateStateError("events.jsonl has trailing data")
            if previous_phase is not state.phase:
                raise CorruptUpdateStateError(
                    "event phase does not match durable state"
                )
            return
        if last_revision != state.revision - 1:
            raise CorruptUpdateStateError("event revisions do not match durable state")
        recovered_phase = self._validate_event(
            state.last_event,
            record.request.update_id,
            state.revision,
            previous_phase=previous_phase,
        )
        if recovered_phase is not state.phase:
            raise CorruptUpdateStateError("recovery event does not match durable state")

        if read_only:
            return

        repaired = "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False)
            + "\n"
            for event in [*events, dict(state.last_event)]
        )
        atomic_write_text(path, repaired)

    @staticmethod
    def _validate_event(
        event: Any,
        update_id: str,
        revision: int,
        *,
        previous_phase: UpdatePhase | None,
    ) -> UpdatePhase:
        if not isinstance(event, dict):
            raise CorruptUpdateStateError("event must be a JSON object")
        event_type = event.get("type")
        expected_fields = {
            "created": {"schema", "at", "type", "update_id", "phase", "revision"},
            "transition": {
                "schema",
                "at",
                "type",
                "update_id",
                "from",
                "phase",
                "revision",
                "detail",
            },
            "verifier_claimed": {
                "schema",
                "at",
                "type",
                "update_id",
                "phase",
                "revision",
                "job_id",
                "owner",
                "generation",
                "lease_until",
            },
        }
        if (
            event.get("schema") != SCHEMA_VERSION
            or event.get("update_id") != update_id
            or event.get("revision") != revision
            or event_type not in expected_fields
            or set(event) != expected_fields[event_type]
        ):
            raise CorruptUpdateStateError("event schema or revision is invalid")
        try:
            phase = UpdatePhase(event["phase"])
            at = event["at"]
            if (
                isinstance(at, bool)
                or not isinstance(at, (int, float))
                or not math.isfinite(at)
                or at < 0
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise CorruptUpdateStateError(
                "event phase or timestamp is invalid"
            ) from exc
        if event_type == "created":
            if revision != 1 or phase is not UpdatePhase.PREPARING or previous_phase:
                raise CorruptUpdateStateError(
                    "created event is not the first preparing state"
                )
        elif event_type == "transition":
            try:
                source = UpdatePhase(event["from"])
            except (TypeError, ValueError) as exc:
                raise CorruptUpdateStateError(
                    "transition source phase is invalid"
                ) from exc
            if (
                previous_phase is None
                or source is not previous_phase
                or not can_transition(source, phase)
                or not isinstance(event["detail"], dict)
            ):
                raise CorruptUpdateStateError("event contains an illegal transition")
        else:
            generation = event["generation"]
            lease_until = event["lease_until"]
            if (
                previous_phase is not UpdatePhase.VERIFYING
                or phase is not UpdatePhase.VERIFYING
                or not isinstance(event["job_id"], str)
                or not event["job_id"]
                or not isinstance(event["owner"], str)
                or not event["owner"]
                or isinstance(generation, bool)
                or not isinstance(generation, int)
                or generation < 1
                or isinstance(lease_until, bool)
                or not isinstance(lease_until, (int, float))
                or not math.isfinite(lease_until)
                or lease_until < 0
            ):
                raise CorruptUpdateStateError("verifier claim event is invalid")
        return phase

    def _clear_active_unlocked(self, update_id: str) -> None:
        path = self.root / "active.json"
        if not path.exists():
            return
        data = self._read_json(path)
        if data.get("update_id") != update_id:
            return
        path.unlink()
        self._fsync_directory(self.root)

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            value = self._loads_json(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise CorruptUpdateStateError(f"cannot read {path.name}") from exc
        if not isinstance(value, dict):
            raise CorruptUpdateStateError(f"{path.name} must contain a JSON object")
        return value

    @staticmethod
    def _loads_json(raw: str) -> Any:
        def reject_constant(value: str) -> None:
            raise ValueError(f"non-standard JSON constant: {value}")

        return json.loads(raw, parse_constant=reject_constant)

    def _write_json(self, path: Path, value: Mapping[str, Any]) -> None:
        atomic_write_text(
            path,
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n",
        )

    def _write_events(self, path: Path, event: Mapping[str, Any]) -> None:
        line = (
            json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False)
            + "\n"
        )
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o600)

    @contextmanager
    def _locked(self, *, read_only: bool = False) -> Iterator[None]:
        if not read_only:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.root, 0o700)
        lock_path = self.root / ".lock"
        with _process_lock:
            flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK
                     if read_only else os.O_RDWR | os.O_CREAT)
            descriptor = os.open(lock_path, flags, 0o600)
            try:
                if read_only:
                    info = os.fstat(descriptor)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                            or stat.S_IMODE(info.st_mode) != 0o600):
                        raise CorruptUpdateStateError("read-only update lock is not a private regular file")
                file_lock.flock(descriptor, file_lock.LOCK_EX)
                yield
            finally:
                file_lock.flock(descriptor, file_lock.LOCK_UN)
                os.close(descriptor)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)


__all__ = ["SelfUpdateStore"]
