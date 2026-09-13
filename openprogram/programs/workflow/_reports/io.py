"""Bounded data and local draft IO for report Workflows; no network operations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import uuid

from openprogram.programs.tools.files.write import execute as write_file
from openprogram.worktree.path_resolve import resolve_path


def decode_object(value: str, max_bytes: int = 300_000) -> dict:
    """Read a bounded JSON object, rejecting ambiguous duplicate keys."""
    if not isinstance(value, str) or len(value.encode("utf-8")) > max_bytes:
        raise ValueError("Report input exceeds byte limit")

    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate key: " + key)
            result[key] = item
        return result

    result = json.loads(value, object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise ValueError("Report input must be a JSON object")
    return result


def encode(value) -> str:
    """Serialize Workflow state and model context without evaluating input."""
    return json.dumps(value, ensure_ascii=False, indent=2)


def save_report(
    week: str,
    output_dir: str,
    summary: str,
    reminder: str,
    coverage: dict,
    sources: list,
) -> Path:
    """Save a unique draft directory through the existing checked write tool."""
    if not re.fullmatch(r"\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])", week):
        raise ValueError("Expected week YYYY-Www")
    resolved, _ = resolve_path(output_dir or "reports/group-weekly")
    target = Path(resolved).absolute() / week / uuid.uuid4().hex
    # Do not mkdir before the public file tool has checked write permission.
    payloads = {"summary.md": summary, "sources.json": encode(sources)}
    if reminder:
        payloads["reminder.md"] = reminder
    payloads["coverage.json"] = encode(coverage)
    for name, content in payloads.items():
        outcome = write_file(str(target / name), content)
        if (
            not isinstance(outcome, str)
            or not outcome
            or not outcome.splitlines()[-1].startswith("Wrote ")
        ):
            raise OSError(str(outcome))
    return target


def material_digest(text: str) -> str:
    """Stable source comparison without retaining full message bodies."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def preflight(output_dir: str) -> str:
    """Resolve once and reject inaccessible outputs before collection/model calls."""
    from openprogram.sandbox import validate_write_path

    resolved, _ = resolve_path(output_dir or "reports/group-weekly")
    target = str(Path(resolved).absolute())
    violation = validate_write_path(str(Path(target) / "checkpoint.json"))
    if violation:
        raise OSError("sandbox policy: " + violation)
    return target


def save_checkpoint(state: dict, output_dir: str) -> str:
    """Immutable checkpoint through checked IO; never overwrite a prior run."""
    target = Path(preflight(output_dir)) / "checkpoints" / (uuid.uuid4().hex + ".json")
    payload = encode(state)
    if len(payload.encode()) > 2_000_000:
        raise ValueError("Checkpoint exceeds byte limit")
    outcome = write_file(str(target), payload)
    if (
        not isinstance(outcome, str)
        or not outcome.splitlines()
        or not outcome.splitlines()[-1].startswith("Wrote ")
    ):
        raise OSError(str(outcome))
    return str(target)


def load_checkpoint(path: str) -> dict:
    """Read bounded state using current read policy; snapshots are untrusted input."""
    from openprogram.sandbox import validate_read_path

    violation = validate_read_path(path)
    if violation:
        raise OSError("sandbox policy: " + violation)
    invalidation = str(path) + ".invalidated.json"
    if Path(invalidation).exists():
        violation = validate_read_path(invalidation)
        if violation:
            raise OSError("sandbox policy: " + violation)
        with open(invalidation, "rb") as stream:
            notice = decode_object(stream.read(4097).decode("utf-8"), max_bytes=4096)
        raise ValueError("Checkpoint invalidated; start a fresh report: " + str(notice.get("reason", "Source correction"))[:500])
    with open(path, "rb") as stream:
        raw = stream.read(2_000_001)
    return decode_object(raw.decode("utf-8"), max_bytes=2_000_000)


def iso_week(stamp: str) -> str:
    """Validate a source ISO date before assigning a reporting week."""
    from datetime import date

    value = date.fromisoformat(stamp).isocalendar()
    return f"{value.year}-W{value.week:02d}"
