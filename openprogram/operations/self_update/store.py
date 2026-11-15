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

from openprogram.host._platform import filesystem
from openprogram.host._platform import locking
from openprogram.state.store.session.atomic_file import atomic_write_text

from .types import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    VERIFIER_PHASES,
    ActiveUpdateError,
    ConcurrentUpdateError,
    CorruptUpdateStateError,
    InvalidTransitionError,
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
    known_keys,
    parse_phase,
    _validate_update_id,
)


_process_lock = threading.RLock()
READ_SCAN_LIMIT = 10_000
RETIRED_NOTE = "started under the previous update system"

# Staged payloads a finished update no longer needs: the built App, the
# controller's copy of the runtime, and the build's HOME and TMPDIR.
PAYLOAD_DIRECTORIES = ("artifact", "controller", "build-home", "build-tmp")
PAYLOADS_KEPT = 2
PAYLOAD_MIN_AGE_SECONDS = 7 * 86400
PRUNABLE_PHASES = frozenset({UpdatePhase.SUCCEEDED, UpdatePhase.ABORTED, UpdatePhase.ROLLED_BACK})


def _rebound(value: Any, previous: str, commit: str) -> Any:
    """``value`` with every mention of the commit ``previous`` changed to ``commit``."""
    if isinstance(value, dict):
        return {key: _rebound(item, previous, commit) for key, item in value.items()}
    if isinstance(value, list):
        return [_rebound(item, previous, commit) for item in value]
    return commit if value == previous else value


class SelfUpdateStore:
    """Owns ``<state>/self-updates`` and its single active update slot."""

    def __init__(self, root: Path | None = None) -> None:
        if root is None:
            from openprogram.state.paths import get_state_dir

            root = get_state_dir() / "self-updates"
        self.root = Path(root)

    def create(self, request: UpdateRequest, *, verifier_config: Mapping[str, Any] | None = None,
               diagnosis_config: Mapping[str, Any] | None = None,
               source_repair_config: Mapping[str, Any] | None = None,
               iteration_config: Mapping[str, Any] | None = None,
               continuation_config: Mapping[str, Any] | None = None) -> UpdateState:
        with self._locked():
            return self._create_unlocked(request, verifier_config=verifier_config,
                diagnosis_config=diagnosis_config, source_repair_config=source_repair_config,
                iteration_config=iteration_config, continuation_config=continuation_config)

    def _maintenance_in_force_unlocked(self) -> bool:
        """Whether a marker holds the slot. One a finished switch left behind does not."""
        marker = self.root / "maintenance.json"
        if not marker.exists() and not marker.is_symlink():
            return False
        try:
            value = self._read_json(marker)
            finished = "deadline" in value and is_terminal(self._load_unlocked(value["update_id"]).state.phase)
        except (SelfUpdateError, KeyError, OSError, ValueError):
            return True
        if not finished:
            return True
        marker.unlink()
        self._fsync_directory(self.root)
        return False

    def _create_unlocked(self, request, *, verifier_config=None, diagnosis_config=None,
                         source_repair_config=None, iteration_config=None, continuation_config=None):
        """Caller holds the process/file lock, including child reservation."""
        if self._maintenance_in_force_unlocked():
            raise ActiveUpdateError("self-update maintenance has not been cleared")
        current = self._load_active_unlocked()
        if current is not None and not is_terminal(current.state.phase):
            raise ActiveUpdateError(
                f"active update {current.request.update_id} is {current.state.phase.value}"
            )
        target = self._update_dir(request.update_id)
        if target.exists():
            raise UpdateExistsError(f"update {request.update_id} already exists")
        if iteration_config is None or iteration_config["parent_id"] is None:
            from .repair.next_candidate import supersede
            supersede(self)

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
            attempt=iteration_config["attempt"] if iteration_config else 1,
            phase=UpdatePhase.PREPARING,
            revision=1,
            updated_at=now,
            last_event=created_event,
        )
        staged = self.root / f".{request.update_id}.{uuid.uuid4().hex}.tmp"
        staged.mkdir(mode=0o700)
        try:
            self._write_json(staged / "request.json", request.to_dict())
            if continuation_config is not None:
                self._write_json(staged / "continuation-config.json", continuation_config)
            if verifier_config is not None:
                self._write_json(staged / "verifier-config.json", verifier_config)
            if diagnosis_config is not None:
                self._write_json(staged / "diagnosis-config.json", diagnosis_config)
            if source_repair_config is not None:
                self._write_json(staged / "source-repair-config.json", source_repair_config)
            if iteration_config is not None:
                self._write_json(staged / "iteration-root.json", iteration_config)
            self._write_json(staged / "state.json", state.to_dict())
            self._write_events(staged / "events.jsonl", created_event)
            filesystem.replace_file(staged, target)
            self._fsync_directory(self.root)
        finally:
            if staged.exists():
                shutil.rmtree(staged)
        self._write_json(
            self.root / "active.json",
            {"schema": SCHEMA_VERSION, "update_id": request.update_id},
        )
        from .repair.diagnosis import cancel_pending
        try:
            cancel_pending(self)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Could not revoke superseded diagnosis")
        from .repair.source_repair import cancel_pending as cancel_repair
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
            return self._transition_unlocked(update_id, target, expected_phase=expected_phase, detail=detail)

    def _transition_unlocked(self, update_id, target, *, expected_phase=None, detail=None):
        """Caller holds the process/file lock."""
        record = self._load_unlocked(update_id)
        current = record.state
        if is_candidate_worker(record):
            raise InvalidTransitionError("the candidate's worker reports and never moves its own update")
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

    def record_detail(self, update_id: str, phase: UpdatePhase, *, detail: Mapping[str, Any]) -> UpdateState:
        """Replace the details of a phase without leaving it.

        Only ``trial`` uses this: its verifier needs a receipt from the
        trial's worker, which does not exist when the phase begins.
        """
        if phase is not UpdatePhase.TRIAL:
            raise ValueError("only the trial phase records detail in place")
        with self._locked():
            current = self._load_unlocked(update_id).state
            if current.phase is not phase:
                raise ConcurrentUpdateError(f"expected {phase.value}, found {current.phase.value}")
            now = time.time()
            event = {
                "schema": SCHEMA_VERSION,
                "at": now,
                "type": "detail_recorded",
                "update_id": update_id,
                "phase": phase.value,
                "revision": current.revision + 1,
                "detail": dict(detail),
            }
            updated = replace(current, revision=current.revision + 1, updated_at=now, detail=dict(detail), last_event=event)
            self._write_json(self._state_path(update_id), updated.to_dict())
            self._write_events(self._events_path(update_id), event)
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
            if state.phase not in VERIFIER_PHASES:
                raise InvalidTransitionError(
                    f"verifier can only be claimed in trial or verifying, found {state.phase.value}"
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

    def prune_payloads(self, *, keep: int = PAYLOADS_KEPT,
                       min_age_seconds: float = PAYLOAD_MIN_AGE_SECONDS,
                       now: float | None = None) -> list[Path]:
        """Delete the staged payloads of old finished updates; keep their records.

        Every update stages a full App artifact and a copy of the runtime
        (several GB together) under its directory. Once the update has
        reached SUCCEEDED, ABORTED or ROLLED_BACK and nothing still points
        at it, those payloads are dead weight while request, state, events
        and the evidence files stay as the durable history. The newest
        ``keep`` finished updates and anything younger than
        ``min_age_seconds`` keep their payloads so a fresh outcome can still
        be inspected; NEEDS_MANUAL_RECOVERY retains the whole transaction.
        Returns the directories removed.
        """
        now = time.time() if now is None else now
        removed: list[Path] = []
        with self._locked():
            referenced = self._referenced_update_ids_unlocked()
            finished: list[UpdateRecord] = []
            for directory in sorted(self.root.iterdir()):
                if not directory.is_dir() or directory.name.startswith("."):
                    continue
                try:
                    record = self._load_unlocked(directory.name)
                except (UpdateNotFoundError, CorruptUpdateStateError, ValueError):
                    continue
                if record.state.phase in PRUNABLE_PHASES and record.request.update_id not in referenced:
                    finished.append(record)
            finished.sort(key=lambda record: record.state.updated_at, reverse=True)
            for record in finished[keep:]:
                if now - record.state.updated_at < min_age_seconds:
                    continue
                directory = self._update_dir(record.request.update_id)
                if (directory / "owner-repair.json").exists():
                    continue
                for name in PAYLOAD_DIRECTORIES:
                    payload = directory / name
                    if payload.is_symlink() or not payload.is_dir():
                        continue
                    shutil.rmtree(payload, ignore_errors=True)
                    if not payload.exists():
                        removed.append(payload)
        return removed

    def _referenced_update_ids_unlocked(self) -> set[str]:
        """Update ids some pointer file in the root still names."""
        referenced: set[str] = set()
        for name in ("active.json", "maintenance.json", "diagnosis-pending.json",
                     "iteration-pending.json", "source-repair-pending.json"):
            path = self.root / name
            if not path.exists() and not path.is_symlink():
                continue
            try:
                update_id = self._read_json(path).get("update_id")
            except CorruptUpdateStateError:
                continue
            if isinstance(update_id, str):
                referenced.add(update_id)
        return referenced

    # The queue. A request that finds the slot held waits here in order, with
    # no update directory, because at most one update may be non-terminal.

    def _queue_path(self) -> Path:
        return self.root / "queue.json"

    def _read_queue_unlocked(self) -> list[dict[str, Any]]:
        path = self._queue_path()
        if not path.exists() and not path.is_symlink():
            return []
        data = known_keys(self._read_json(path), {"schema", "protocol", "entries"})
        entries = data.get("entries")
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise CorruptUpdateStateError("malformed update queue")
        return entries

    def _write_queue_unlocked(self, entries: list[dict[str, Any]]) -> None:
        if entries:
            self._write_json(self._queue_path(), {"schema": 1, "protocol": PROTOCOL_VERSION, "entries": entries})
        else:
            self._queue_path().unlink(missing_ok=True)

    def enqueue(self, request: UpdateRequest, configs: Mapping[str, Any]) -> int:
        """Save a validated request to run after the current one; its queue position."""
        with self._locked():
            entries = self._read_queue_unlocked()
            entries.append({"request": request.to_dict(), "configs": dict(configs), "queued_at": time.time()})
            self._write_queue_unlocked(entries)
            return len(entries)

    def queued(self, *, read_only: bool = False) -> list[dict[str, Any]]:
        """The waiting requests, oldest first."""
        if not self.root.exists():
            return []
        with self._locked(read_only=read_only):
            return [
                {"update_id": entry["request"]["update_id"], "session_id": entry["request"]["session_id"],
                 "goal": entry["request"]["acceptance"]["goal"], "queued_at": entry.get("queued_at"),
                 "position": index + 1}
                for index, entry in enumerate(self._read_queue_unlocked())
            ]

    def remove_queued(self, update_id: str) -> bool:
        with self._locked():
            entries = self._read_queue_unlocked()
            remaining = [entry for entry in entries if entry["request"]["update_id"] != update_id]
            if len(remaining) == len(entries):
                return False
            self._write_queue_unlocked(remaining)
            return True

    def take_queued(self, *, durable=lambda request: True, local_tip=None) -> UpdateState | None:
        """Give the slot to the head of the queue; ``None`` when it stays empty.

        A queued request's origin turn has ended, so it starts at ``building``,
        or at ``trial`` when it names a retained version and has nothing to
        build. ``local_tip`` then says what ``local`` is now, because the update
        ahead of it may have moved it. ``durable`` says whether that turn was
        committed; one that was not is dropped, as a prepared update is when its
        turn never commits.
        """
        with self._locked():
            if self._load_active_unlocked() is not None or self._maintenance_in_force_unlocked():
                return None
            while True:
                entries = self._read_queue_unlocked()
                if not entries:
                    return None
                head = entries[0]
                request = UpdateRequest.from_dict(head["request"])
                if not durable(request):
                    self._write_queue_unlocked(entries[1:])
                    continue
                self._create_unlocked(request, **head["configs"])
                self._write_queue_unlocked(entries[1:])
                detail: dict[str, Any] = {"turn_released": True, "queued": True}
                if request.candidate_version and local_tip is not None:
                    detail["base_sha"] = local_tip()
                return self._transition_unlocked(
                    request.update_id,
                    UpdatePhase.TRIAL if request.candidate_version else UpdatePhase.BUILDING,
                    expected_phase=UpdatePhase.PREPARING, detail=detail,
                )

    def rebind_candidate(self, update_id: str, commit: str) -> UpdateRecord:
        """A rebase gave the change a new commit; everything checked from here names it.

        The frozen configurations name the commit too, and the request holds
        their digests. Each step can be repeated, so a controller that dies
        halfway through does this again.
        """
        from .repair.diagnosis import config_evidence as diagnosis_evidence
        from .repair.source_repair import config_evidence as repair_evidence
        from .verification.verifier_config import config_evidence as verifier_evidence

        with self._locked():
            directory = self._update_dir(update_id)
            raw = self._read_json(directory / "request.json")
            previous = raw["candidate_sha"]
            UpdateRequest.from_dict({**raw, "candidate_sha": commit})  # Refuses a malformed commit.
            evidence = list(raw.get("pre_update_evidence") or ())
            for name, digest in (("verifier-config.json", verifier_evidence), ("diagnosis-config.json", diagnosis_evidence),
                                 ("source-repair-config.json", repair_evidence)):
                path = directory / name
                if not path.is_file():
                    continue
                config = _rebound(self._read_json(path), previous, commit)
                self._write_json(path, config)
                current = digest(config)
                kind = current.partition(":")[0]
                evidence = [current if item.partition(":")[0] == kind else item for item in evidence]
            self._write_json(directory / "request.json", {**raw, "candidate_sha": commit, "pre_update_evidence": evidence})
            return self._load_unlocked(update_id)

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
        if read_only:
            self._private_directory(directory)
        raw_request = self._read_json(directory / "request.json", read_only=read_only)
        request = UpdateRequest.from_dict(raw_request)
        state = UpdateState.from_dict(self._read_json(directory / "state.json", read_only=read_only))
        if request.update_id != update_id or state.update_id != update_id:
            raise CorruptUpdateStateError("update id does not match its directory")
        record = UpdateRecord(request, state)
        self._reconcile_events_unlocked(record, read_only=read_only)
        if raw_request.get("schema") == 1 and self._retires_unlocked(record):
            record = self._retire_unlocked(record, read_only=read_only)
        return record

    @staticmethod
    def _retires_unlocked(record: UpdateRecord) -> bool:
        """Whether an unfinished schema 1 update ends now.

        A dev_stable update keeps going: its controller is a frozen private
        copy that finishes the update with the code it started with.
        """
        from .control.dev_stable import owns

        return not is_terminal(record.state.phase) and not owns(record)

    def _retire_unlocked(self, record: UpdateRecord, *, read_only: bool) -> UpdateRecord:
        """End an update whose controller belongs to the previous update system."""
        update_id = record.request.update_id
        now = time.time()
        event = {
            "schema": SCHEMA_VERSION,
            "at": now,
            "type": "retired",
            "update_id": update_id,
            "from": record.state.phase.value,
            "phase": UpdatePhase.ABORTED.value,
            "revision": record.state.revision + 1,
            "detail": {"note": RETIRED_NOTE},
        }
        state = replace(
            record.state, phase=UpdatePhase.ABORTED, revision=record.state.revision + 1,
            updated_at=now, detail={**record.state.detail, "note": RETIRED_NOTE}, last_event=event,
        )
        if not read_only:
            self._write_json(self._state_path(update_id), state.to_dict())
            self._write_events(self._events_path(update_id), event)
            self._clear_active_unlocked(update_id)
            marker = self.root / "maintenance.json"
            try:
                owner = self._read_json(marker).get("update_id") if marker.exists() else None
            except CorruptUpdateStateError:
                owner = None
            if owner == update_id:
                marker.unlink()
                self._fsync_directory(self.root)
        return UpdateRecord(record.request, state)

    def _load_active_unlocked(self, *, read_only: bool = False) -> UpdateRecord | None:
        path = self.root / "active.json"
        if not path.exists() and not path.is_symlink():
            discovered = self._discover_nonterminal_unlocked(read_only=read_only)
            if discovered is None:
                return None
            if not read_only:
                self._write_json(
                    path,
                    {"schema": SCHEMA_VERSION, "update_id": discovered.request.update_id},
                )
            return discovered
        data = self._read_json(path, read_only=read_only)
        if set(data) != {"schema", "update_id"} or data.get("schema") not in (1, SCHEMA_VERSION):
            raise CorruptUpdateStateError("unsupported or malformed active schema")
        update_id = data.get("update_id")
        if not isinstance(update_id, str):
            raise CorruptUpdateStateError("active update_id must be a string")
        try:
            record = self._load_unlocked(update_id, read_only=read_only)
        except (ValueError, UpdateNotFoundError) as exc:
            raise CorruptUpdateStateError(
                "active.json points to an invalid or missing update"
            ) from exc
        if is_terminal(record.state.phase):
            if not read_only:
                self._clear_active_unlocked(update_id)
            return None
        return record

    def _discover_nonterminal_unlocked(self, *, read_only: bool = False) -> UpdateRecord | None:
        records: list[UpdateRecord] = []
        entries = self.root.iterdir() if read_only else sorted(self.root.iterdir())
        for index, directory in enumerate(entries):
            if read_only and index >= READ_SCAN_LIMIT:
                raise ConcurrentUpdateError("self-update history exceeds scan limit")
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                record = self._load_unlocked(directory.name, read_only=read_only)
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
            raw = (self._read_private_text(path, limit=8_388_608) if read_only
                   else path.read_text(encoding="utf-8"))
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
            "detail_recorded": {"schema", "at", "type", "update_id", "phase", "revision", "detail"},
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
            "retired": {
                "schema",
                "at",
                "type",
                "update_id",
                "from",
                "phase",
                "revision",
                "detail",
            },
        }
        if (
            event.get("schema") not in (1, SCHEMA_VERSION)
            or event.get("update_id") != update_id
            or event.get("revision") != revision
            or event_type not in expected_fields
            or set(event) != expected_fields[event_type]
        ):
            raise CorruptUpdateStateError("event schema or revision is invalid")
        try:
            phase = parse_phase(event["phase"])
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
        elif event_type == "detail_recorded":
            if (
                phase is not UpdatePhase.TRIAL
                or previous_phase is not UpdatePhase.TRIAL
                or not isinstance(event["detail"], dict)
            ):
                raise CorruptUpdateStateError("detail event is invalid")
        elif event_type == "retired":
            if phase is not UpdatePhase.ABORTED or previous_phase is None or not isinstance(event["detail"], dict):
                raise CorruptUpdateStateError("event contains an illegal retirement")
        elif event_type == "transition":
            try:
                source = parse_phase(event["from"])
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
                previous_phase not in VERIFIER_PHASES
                or phase is not previous_phase
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

    @staticmethod
    def _private_directory(path: Path) -> None:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or not filesystem.user_private_metadata(info):
            raise CorruptUpdateStateError("self-update directory is not private")

    @staticmethod
    def _read_private_text(path: Path, *, limit: int = 2_097_152) -> str:
        try:
            return filesystem.read_user_state_bytes(path, limit=limit).decode("utf-8")
        except ValueError as exc:
            raise CorruptUpdateStateError("invalid private self-update file") from exc

    def _read_json(self, path: Path, *, read_only: bool = False) -> dict[str, Any]:
        try:
            value = self._loads_json(self._read_private_text(path) if read_only
                                     else path.read_text(encoding="utf-8"))
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
        else:
            self._private_directory(self.root)
        lock_path = self.root / ".lock"
        if not _process_lock.acquire(timeout=0.25 if read_only else -1):
            raise ConcurrentUpdateError("self-update state is busy")
        try:
            flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                     if read_only else os.O_RDWR | os.O_CREAT)
            descriptor = os.open(lock_path, flags, 0o600)
            acquired = False
            try:
                if read_only:
                    info = os.fstat(descriptor)
                    if (not stat.S_ISREG(info.st_mode)
                            or not filesystem.user_private_metadata(info, exact_mode=0o600)
                            or not filesystem.user_private_metadata(lock_path.lstat(), exact_mode=0o600)):
                        raise CorruptUpdateStateError("read-only update lock is not a private regular file")
                try:
                    locking.flock(descriptor, locking.LOCK_EX | (locking.LOCK_NB if read_only else 0))
                    acquired = True
                except BlockingIOError as exc:
                    raise ConcurrentUpdateError("self-update state is busy") from exc
                yield
            finally:
                if acquired:
                    locking.flock(descriptor, locking.LOCK_UN)
                os.close(descriptor)
        finally:
            _process_lock.release()

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


def is_candidate_worker(record: UpdateRecord) -> bool:
    """Whether this process is the worker of the version this update switches to.

    Activation and rollback belong to the old version's controller, so a broken
    candidate cannot mark its own switch as good. The controller runs from
    ``controller_version``, which is never the candidate.
    """
    if record.state.phase not in (UpdatePhase.ACTIVATING, UpdatePhase.VERIFYING):
        return False
    candidate = record.request.candidate_version or record.state.detail.get("candidate_version")
    if not candidate:
        return False
    from .versions.layout import current_runtime

    runtime = current_runtime()
    return runtime is not None and runtime.version == candidate


__all__ = ["SelfUpdateStore", "is_candidate_worker"]
