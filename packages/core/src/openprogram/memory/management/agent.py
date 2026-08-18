"""Memory Writer and Manager execution through Claude Code."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # the SDK is only needed to actually run a writer
    from ..agent_runtime import ClaudeCodeAgent
from .config import MemoryConfig
from ..prompts import FEW_SHOT_INSTRUCTIONS, SYSTEM_PROMPT
from .tools import management_tools
from .transaction import TransactionError
from .workspace import MemoryWorkspace
from ..workspace_layout import runtime_dir


def render_conversation(
    turns: list[tuple[str, str]], refs: list[str]
) -> str:
    return "\n".join(
        (
            json.dumps(
                {"ref": ref, "speaker": speaker, "content": text},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )
        for (speaker, text), ref in zip(turns, refs)
    )


def _baseline(workspace: MemoryWorkspace) -> tuple[Any, ...] | None:
    """Snapshot the stage, or None if it cannot be parsed.

    A turn whose edits were rejected leaves the stage rolled back, but a
    malformed file that reached disk another way would make this raise. That
    is a reason to skip committing, never to abort the whole build.
    """
    try:
        return workspace.baseline()
    except Exception:
        workspace._refresh_stage()
        try:
            return workspace.baseline()
        except Exception:
            return None


def _commit_turn(
    workspace: MemoryWorkspace,
    baseline: tuple[Any, ...] | None,
    audit: list[dict[str, Any]],
) -> str | None:
    """Install what the turn staged. Returns the error text if it failed."""
    if baseline is None:
        workspace._refresh_stage()
        return None
    if not workspace.stage_is_dirty():
        return None
    try:
        workspace.commit_edits(*baseline)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        audit.append({
            "tool": "commit", "status": "error", "output": message,
        })
        return message
    audit.append({
        "tool": "commit",
        "status": "ok",
        "count": workspace.last_created_blocks,
        "topic_paths": workspace.last_changed_topics,
    })
    return None


def _repair_guidance(error: str) -> str:
    """What to change, for the validation failures that recur.

    The raw message names the rule that was broken; a writer that has just
    broken it needs the correction instead.
    """
    if "memory source links required" in error or "undefined footnote" in error:
        return (
            "A citation needs its `[^eN]:` definition line in the same file. "
            "Either add the definition or leave the existing `[^e-...]` "
            "citation exactly as it was."
        )
    if "Source Memory is append-only" in error:
        return (
            "Nothing under sources/ may be edited. Write the fact into a "
            "Topic file under topics/ instead."
        )
    return ""


def _record_trajectory(
    memory_dir: str | Path,
    stage: str,
    system_prompt: str,
    prompt: str,
    result: Any = None,
    error: BaseException | None = None,
) -> None:
    """Append one agent run to the runtime directory's agent-history.jsonl.

    What the model was sent, every tool call it made, and what it replied.
    Usage counters say a batch took sixty turns; only this says what those
    turns were doing, and a failed run is the one most worth reading.
    """
    record = {
        "stage": stage,
        "system_prompt": system_prompt,
        "prompt": prompt,
    }
    if error is not None:
        record["error"] = f"{type(error).__name__}: {error}"
        record["turns"] = getattr(error, "turns", [])
    else:
        record.update({
            "turns": result.turns,
            "reply": result.reply or result.text,
            "rounds": result.num_turns,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "stop_reason": result.stop_reason,
        })
    # The runtime directory, not the workspace root: this is a log of how
    # the memory was produced, not memory, and the transaction compares the
    # workspace against the stage to decide what an edit changed.
    path = runtime_dir(memory_dir) / "agent-history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _run_agent(
    memory_dir: str | Path,
    *,
    agent: ClaudeCodeAgent,
    task: str,
    source_sessions: list[dict[str, Any]] | None = None,
    usage_logger: Any | None = None,
    final_output: list[str] | None = None,
    config: MemoryConfig | None = None,
    history_dir: str | Path | None = None,
    stage: str | None = None,
    allowed_new_source_refs: set[str] | None = None,
) -> list[dict[str, Any]]:
    if config is None:
        from .config import load_memory_config

        config = load_memory_config()
    # Verification runs against a throwaway copy of the memory. Its history
    # belongs with the real workspace, or it is deleted with the copy.
    history_dir = memory_dir if history_dir is None else history_dir
    workspace = MemoryWorkspace(
        memory_dir,
        config=config,
        allowed_new_source_refs=allowed_new_source_refs,
    )
    try:
        if source_sessions:
            workspace.archive_sessions(source_sessions)
            workspace._refresh_stage()
        task = f"{task}\n\nCurrent workspace structure:\n{workspace.structure()}"
        audit: list[dict[str, Any]] = []
        system_prompt = (
            f"{SYSTEM_PROMPT}\n{FEW_SHOT_INSTRUCTIONS}"
            if config.few_shot_instructions
            else SYSTEM_PROMPT
        )
        # Edits made through the built-in file tools never pass through the
        # shell tool, so the transaction runs once the turn is over.
        baseline = _baseline(workspace)
        stage = stage or ("write" if source_sessions else "organize")
        try:
            result = agent.run(
                prompt=task,
                system_prompt=system_prompt,
                cwd=workspace.stage_dir,
                tools=management_tools(workspace, audit),
                max_turns=config.max_turns,
                max_budget_usd=config.max_budget_usd,
            )
        except BaseException as exc:
            _record_trajectory(
                history_dir, stage, system_prompt, task, error=exc
            )
            raise
        _record_trajectory(history_dir, stage, system_prompt, task, result)
        if usage_logger is not None:
            usage_logger(result)
        error = _commit_turn(workspace, baseline, audit)
        if error is not None:
            # A rejected turn was rolled back, so the pre-repair stage is the
            # committed state. Snapshot it now: reading it after the repair
            # edits would parse the model's malformed text and raise.
            repair_baseline = _baseline(workspace)
            repair_prompt = (
                "Your edits were rejected and discarded:\n\n"
                f"{error}\n\n"
                + (f"{_repair_guidance(error)}\n\n" if _repair_guidance(error) else "")
                + "The workspace is back to its state before this turn. "
                "Redo the work, fixing what the message reports.\n\n"
                f"{task}"
            )
            try:
                repair = agent.run(
                    prompt=repair_prompt,
                    system_prompt=system_prompt,
                    cwd=workspace.stage_dir,
                    tools=management_tools(workspace, audit),
                    max_turns=config.max_turns,
                    max_budget_usd=config.max_budget_usd,
                )
            except BaseException as exc:
                _record_trajectory(
                    history_dir, f"{stage}-repair", system_prompt,
                    repair_prompt, error=exc,
                )
                raise
            _record_trajectory(
                history_dir, f"{stage}-repair", system_prompt,
                repair_prompt, repair,
            )
            if usage_logger is not None:
                usage_logger(repair)
            repair_error = _commit_turn(workspace, repair_baseline, audit)
            if repair_error is not None:
                # Two rejected turns leave the workspace as it started.
                # Reporting success here is what lets the caller advance
                # its cursor past turns that never reached memory.
                raise TransactionError(
                    "COMMIT_REJECTED",
                    f"repair edits were rejected too: {repair_error}",
                )
        if final_output is not None:
            final_output.append(result.text)
        audit.append({
            "tool": "agent",
            "status": "ok",
            "reason": result.stop_reason or "complete",
            "rounds": result.num_turns,
            "turns": result.turns,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "anthropic_equivalent_cost_usd": (
                result.anthropic_equivalent_cost_usd
            ),
        })
        return audit
    finally:
        shutil.rmtree(workspace.stage_dir, ignore_errors=True)
