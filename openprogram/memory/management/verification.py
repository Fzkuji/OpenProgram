"""Write-time retrieval verification and repair through Claude Code."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .agent import _record_trajectory, _run_agent, render_conversation
from .config import MemoryConfig
from ..prompts import (
    VERIFICATION_PROBE_TASK,
    VERIFICATION_REPAIR_TASK,
    VERIFICATION_RETRIEVAL_TASK,
)
from .retrying import STRUCTURED_OUTPUT_ATTEMPTS
from ..workspace_layout import TEMPORARY_PREFIX

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


def _structured(
    agent: Any,
    *,
    prompt: str,
    schema: dict[str, Any],
    cwd: str | Path,
    usage_logger: Any | None,
    config: MemoryConfig,
    memory_dir: str | Path | None = None,
    stage: str = "verify",
) -> dict[str, Any]:
    memory_dir = cwd if memory_dir is None else memory_dir
    system_prompt = "Return the requested structured result."
    # Models reached through a gateway return structured output only most of
    # the time. A single miss used to abort the whole build, discarding memory
    # that had already been written correctly, so retry before giving up.
    for attempt in range(STRUCTURED_OUTPUT_ATTEMPTS):
        # Every attempt is recorded, including the ones that came back
        # unstructured: a retry that keeps missing is the thing worth reading.
        label = stage if attempt == 0 else f"{stage}-retry{attempt}"
        try:
            result = agent.run(
                prompt=prompt,
                system_prompt=system_prompt,
                cwd=cwd,
                tools=[],
                max_turns=config.max_turns,
                max_budget_usd=config.max_budget_usd,
                output_schema=schema,
            )
        except BaseException as exc:
            _record_trajectory(
                memory_dir, label, system_prompt, prompt, error=exc
            )
            raise
        _record_trajectory(memory_dir, label, system_prompt, prompt, result)
        if usage_logger is not None:
            usage_logger(result)
        if isinstance(result.structured_output, dict):
            return result.structured_output
    raise ValueError(
        "verification did not return structured output after "
        f"{STRUCTURED_OUTPUT_ATTEMPTS} attempts"
    )


def _verification_retrieve(
    memory_dir: str | Path,
    *,
    agent: Any,
    question: str,
    usage_logger: Any | None,
    config: MemoryConfig,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix=f"{TEMPORARY_PREFIX}verify-read-"
    ) as temporary:
        copied = Path(temporary) / "memory"
        shutil.copytree(Path(memory_dir), copied)
        final: list[str] = []
        audit = _run_agent(
            copied,
            agent=agent,
            task=VERIFICATION_RETRIEVAL_TASK.format(question=question),
            usage_logger=usage_logger,
            final_output=final,
            config=config,
            history_dir=memory_dir,
            stage="verify-retrieve",
        )
    text = final[-1] if final else ""
    match = re.search(r"<answer>(.*?)</answer>", text, re.DOTALL | re.IGNORECASE)
    answer = (match.group(1) if match else text).strip()
    return {"question": question, "answer": answer, "trace": audit}


def _verification_answer_supported(
    *,
    agent: Any,
    question: str,
    expected_answer: str,
    retrieved_answer: str,
    cwd: str | Path,
    usage_logger: Any | None,
    config: MemoryConfig,
) -> bool:
    value = _structured(
        agent,
        prompt=(
            "Decide whether the retrieved answer correctly answers the question "
            "according to the expected source-grounded answer.\n\n"
            f"Question: {question}\n"
            f"Expected answer: {expected_answer}\n"
            f"Retrieved answer: {retrieved_answer}"
        ),
        schema=_SUPPORTED_SCHEMA,
        cwd=cwd,
        usage_logger=usage_logger,
        config=config,
        memory_dir=cwd,
        stage="verify-answer-supported",
    )
    if not isinstance(value.get("supported"), bool):
        raise ValueError(
            "verification answer check must return supported boolean"
        )
    return value["supported"]


def verify_session(
    memory_dir: str | Path,
    *,
    agent: Any,
    observation_date: str,
    turns: list[tuple[str, str]],
    refs: list[str],
    usage_logger: Any | None = None,
    config: MemoryConfig | None = None,
) -> dict[str, Any]:
    config = config or MemoryConfig()
    probe = _structured(
        agent,
        prompt=VERIFICATION_PROBE_TASK.format(
            observation_date=observation_date,
            conversation=render_conversation(turns, refs),
        ),
        schema=_PROBE_SCHEMA,
        cwd=memory_dir,
        usage_logger=usage_logger,
        config=config,
        memory_dir=memory_dir,
        stage="verify-probe",
    )
    question = str(probe.get("question", "")).strip()
    expected_answer = str(probe.get("expected_answer", "")).strip()
    probe_refs = [str(ref) for ref in probe.get("refs", [])]
    if (
        not question
        or not expected_answer
        or not probe_refs
        or any(ref not in refs for ref in probe_refs)
    ):
        raise ValueError(
            "verification probe is incomplete or cites another session"
        )
    probe = {
        "question": question,
        "expected_answer": expected_answer,
        "refs": probe_refs,
    }

    initial = _verification_retrieve(
        memory_dir,
        agent=agent,
        question=question,
        usage_logger=usage_logger,
        config=config,
    )
    initial["supported"] = _verification_answer_supported(
        agent=agent,
        question=question,
        expected_answer=expected_answer,
        retrieved_answer=initial["answer"],
        cwd=memory_dir,
        usage_logger=usage_logger,
        config=config,
    )
    if initial["supported"]:
        return {
            "probe": probe,
            "initial": initial,
            "repaired": False,
            "repair_trace": [],
            "post_repair": None,
        }

    repair_trace = _run_agent(
        memory_dir,
        agent=agent,
        task=VERIFICATION_REPAIR_TASK.format(
            question=question,
            expected_answer=expected_answer,
            refs=", ".join(probe_refs),
            retrieved_answer=initial["answer"],
            trace=json.dumps(initial["trace"], ensure_ascii=False, indent=2),
        ),
        usage_logger=usage_logger,
        config=config,
        stage="verify-repair",
    )
    post_repair = _verification_retrieve(
        memory_dir,
        agent=agent,
        question=question,
        usage_logger=usage_logger,
        config=config,
    )
    post_repair["supported"] = _verification_answer_supported(
        agent=agent,
        question=question,
        expected_answer=expected_answer,
        retrieved_answer=post_repair["answer"],
        cwd=memory_dir,
        usage_logger=usage_logger,
        config=config,
    )
    return {
        "probe": probe,
        "initial": initial,
        "repaired": True,
        "repair_trace": repair_trace,
        "post_repair": post_repair,
    }
