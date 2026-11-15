"""Write-time verification: check that what was just written can be found.

A write can land and still be useless: the fact went into a file nobody
would look in, or under a heading that hides it. With
``memory.writer.verify`` on, every batch the writer commits is followed by
a self-check:

1. probe — the model picks one concrete fact from the batch and states a
   question, the answer the conversation supports, and the turns it rests on;
2. retrieve — a fresh run answers the question from a throwaway copy of
   memory, so a retrieval that edits nothing real cannot disturb it;
3. judge — the model decides whether that answer matches the expected one;
4. repair — when it does not, a run reorganizes the real workspace with the
   question, the expected answer and the failed retrieval in hand, and the
   retrieval and judgement run once more.

Verification never fails the write it follows. Its outcome is appended to
``verification.jsonl`` in the runtime directory, and the counts show under
the writer's status.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openprogram.state.store.session.atomic_file import atomic_write_text

from ..prompts import VERIFY_JUDGE, VERIFY_PROBE, VERIFY_REPAIR, VERIFY_RETRIEVE
from ..workspace_layout import RUNTIME_DIR_NAMES, TEMPORARY_PREFIX, runtime_dir
from .agent import _record_trajectory, _run_agent, render_conversation
from .config import MemoryConfig
from .retrying import STRUCTURED_OUTPUT_ATTEMPTS

logger = logging.getLogger(__name__)

LOG_FILE = "verification.jsonl"
OUTCOMES = ("passed", "repaired", "unrepaired", "error")
# The log is for reading back what verification found, not an archive; the
# oldest entries go once it holds this many.
LOG_LIMIT = 500

_PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "expected_answer": {"type": "string"},
        "refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["question", "expected_answer", "refs"],
    "additionalProperties": False,
}

_SUPPORTED_SCHEMA = {
    "type": "object",
    "properties": {"supported": {"type": "boolean"}},
    "required": ["supported"],
    "additionalProperties": False,
}
_ANSWER = re.compile(r"<answer>(.*?)</answer>", re.DOTALL | re.IGNORECASE)


def _structured(
    agent: Any,
    *,
    prompt: str,
    schema: dict[str, Any],
    memory_dir: Path,
    config: MemoryConfig,
    stage: str,
) -> dict[str, Any]:
    system_prompt = "Return the requested structured result."
    # A model reached through a gateway returns structured output only most
    # of the time; one miss is retried before verification gives up.
    for attempt in range(STRUCTURED_OUTPUT_ATTEMPTS):
        label = stage if attempt == 0 else f"{stage}-retry{attempt}"
        try:
            result = agent.run(
                prompt=prompt,
                system_prompt=system_prompt,
                cwd=memory_dir,
                tools=[],
                max_turns=config.max_turns,
                max_budget_usd=config.max_budget_usd,
                output_schema=schema,
            )
        except BaseException as exc:
            _record_trajectory(memory_dir, label, system_prompt, prompt, error=exc)
            raise
        _record_trajectory(memory_dir, label, system_prompt, prompt, result)
        if isinstance(result.structured_output, dict):
            return result.structured_output
    raise ValueError(
        "verification did not return structured output after "
        f"{STRUCTURED_OUTPUT_ATTEMPTS} attempts"
    )


def _retrieve(
    memory_dir: Path, *, agent: Any, question: str, config: MemoryConfig,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix=f"{TEMPORARY_PREFIX}verify-read-"
    ) as temporary:
        copied = Path(temporary) / "memory"
        # The runtime directory holds logs and indexes, not memory, and can
        # be the largest thing in the workspace.
        shutil.copytree(
            memory_dir, copied, ignore=shutil.ignore_patterns(*RUNTIME_DIR_NAMES),
        )
        final: list[str] = []
        audit = _run_agent(
            copied,
            agent=agent,
            task=VERIFY_RETRIEVE.format(question=question),
            final_output=final,
            config=config,
            history_dir=memory_dir,
            stage="verify-retrieve",
        )
    text = final[-1] if final else ""
    match = _ANSWER.search(text)
    answer = (match.group(1) if match else text).strip()
    return {"answer": answer, "trace": audit}


def _supported(
    memory_dir: Path,
    *,
    agent: Any,
    question: str,
    expected_answer: str,
    retrieved_answer: str,
    config: MemoryConfig,
) -> bool:
    value = _structured(
        agent,
        prompt=VERIFY_JUDGE.format(
            question=question,
            expected_answer=expected_answer,
            retrieved_answer=retrieved_answer,
        ),
        schema=_SUPPORTED_SCHEMA,
        memory_dir=memory_dir,
        config=config,
        stage="verify-judge",
    )
    if not isinstance(value.get("supported"), bool):
        raise ValueError("verification judgement must return supported boolean")
    return value["supported"]


def _changed_files(audit: list[dict[str, Any]]) -> list[str]:
    return sorted({
        path
        for entry in audit
        if entry.get("tool") == "commit" and entry.get("status") == "ok"
        for path in entry.get("topic_paths", [])
    })


def verify_batch(
    memory_dir: str | Path,
    *,
    agent: Any,
    observation_date: str,
    turns: list[tuple[str, str]],
    refs: list[str],
    config: MemoryConfig | None = None,
) -> dict[str, Any]:
    """Probe, retrieve, judge, and repair once. Raises on a model failure."""
    from .config import load_memory_config

    root = Path(memory_dir).resolve()
    config = config or load_memory_config()
    probe = _structured(
        agent,
        prompt=VERIFY_PROBE.format(
            observation_date=observation_date,
            conversation=render_conversation(turns, refs),
        ),
        schema=_PROBE_SCHEMA,
        memory_dir=root,
        config=config,
        stage="verify-probe",
    )
    question = str(probe.get("question", "")).strip()
    expected = str(probe.get("expected_answer", "")).strip()
    probe_refs = [str(ref) for ref in probe.get("refs", [])]
    if (
        not question or not expected or not probe_refs
        or any(ref not in refs for ref in probe_refs)
    ):
        raise ValueError("verification probe is incomplete or cites another batch")

    def check() -> dict[str, Any]:
        found = _retrieve(root, agent=agent, question=question, config=config)
        found["supported"] = _supported(
            root, agent=agent, question=question, expected_answer=expected,
            retrieved_answer=found["answer"], config=config,
        )
        return found

    initial = check()
    report: dict[str, Any] = {
        "question": question,
        "expected_answer": expected,
        "refs": probe_refs,
        "initial_answer": initial["answer"],
        "changed_files": [],
    }
    if initial["supported"]:
        return {**report, "outcome": "passed"}
    repair = _run_agent(
        root,
        agent=agent,
        task=VERIFY_REPAIR.format(
            question=question,
            expected_answer=expected,
            refs=", ".join(probe_refs),
            retrieved_answer=initial["answer"],
            trace=json.dumps(initial["trace"], ensure_ascii=False, indent=2),
        ),
        config=config,
        stage="verify-repair",
    )
    after = check()
    return {
        **report,
        "outcome": "repaired" if after["supported"] else "unrepaired",
        "final_answer": after["answer"],
        "changed_files": _changed_files(repair),
    }


def verify_after_write(
    memory_dir: str | Path,
    *,
    agent: Any,
    observation_date: str,
    turns: list[tuple[str, str]],
    refs: list[str],
) -> list[str]:
    """Verify a committed batch; never raises. Returns files a repair changed."""
    root = Path(memory_dir).resolve()
    try:
        report = verify_batch(
            root, agent=agent, observation_date=observation_date,
            turns=turns, refs=refs,
        )
    except Exception as exc:  # noqa: BLE001 - verification cannot fail a write
        logger.info("memory verification did not finish: %s", exc)
        report = {"outcome": "error", "error": f"{type(exc).__name__}: {exc}"}
    _append(root, report)
    return list(report.get("changed_files") or [])


def _log_path(root: Path) -> Path:
    return runtime_dir(root) / LOG_FILE


def _append(root: Path, report: dict[str, Any]) -> None:
    entry = {"at": datetime.now(timezone.utc).isoformat(), **report}
    path = _log_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        lines.append(json.dumps(entry, ensure_ascii=False))
        atomic_write_text(path, "\n".join(lines[-LOG_LIMIT:]) + "\n")
    except OSError as exc:
        logger.debug("memory verification result was not saved: %s", exc)


def status(memory_dir: str | Path) -> dict[str, Any]:
    """Counts by outcome and the latest outcome, from the log."""
    counts = {outcome: 0 for outcome in OUTCOMES}
    last = None
    try:
        lines = _log_path(Path(memory_dir)).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        outcome = entry.get("outcome") if isinstance(entry, dict) else None
        if outcome in counts:
            counts[outcome] += 1
            last = {"at": entry.get("at"), "outcome": outcome}
    return {"last": last, **counts}
