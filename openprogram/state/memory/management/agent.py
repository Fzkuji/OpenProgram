"""Memory Writer and Manager execution through Claude Code."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # the SDK is only needed to actually run a writer
    from ..agent_runtime import ClaudeCodeAgent
from .. import chat_refs
from .config import MemoryConfig
from .guidance import repair_guidance
from ..prompts import FEW_SHOT_INSTRUCTIONS, SYSTEM_PROMPT
from .tools import management_tools
from .transaction import TransactionError
from .workspace import MemoryWorkspace
from ..workspace_layout import runtime_dir


def _cite(ref: str) -> str:
    """What the writer pastes into a footnote: the chat link of a message."""
    try:
        return chat_refs.link(ref)
    except ValueError:
        return ref


def render_conversation(
    turns: list[tuple[str, str]], refs: list[str]
) -> str:
    return "\n".join(
        (
            json.dumps(
                {"ref": _cite(ref), "speaker": speaker, "content": text},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )
        for (speaker, text), ref in zip(turns, refs)
    )


def _baseline(workspace: MemoryWorkspace) -> tuple[Any, ...] | None:
    """Snapshot the stage, or None if it cannot be read.

    The stage is read the way memory is read, so a file another tool wrote
    never makes this fail; only an unreadable stage does, and that is a
    reason to skip committing, never to abort the whole build.
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


# How many times a turn whose staged edits fail the checks is sent back to
# fix them in place before the commit decides.
FIX_ROUNDS = 3


def _turn(
    agent: ClaudeCodeAgent,
    workspace: MemoryWorkspace,
    audit: list[dict[str, Any]],
    *,
    prompt: str,
    system_prompt: str,
    config: MemoryConfig,
    history_dir: str | Path,
    stage: str,
    usage_logger: Any | None,
) -> Any:
    """One agent run on the stage, recorded in the agent history."""
    try:
        result = agent.run(
            prompt=prompt,
            system_prompt=system_prompt,
            cwd=workspace.stage_dir,
            tools=management_tools(workspace, audit),
            max_turns=config.max_turns,
            max_budget_usd=config.max_budget_usd,
        )
    except BaseException as exc:
        _record_trajectory(history_dir, stage, system_prompt, prompt, error=exc)
        raise
    _record_trajectory(history_dir, stage, system_prompt, prompt, result)
    if usage_logger is not None:
        usage_logger(result)
    return result


def _fix_in_place(
    workspace: MemoryWorkspace,
    baseline: tuple[Any, ...] | None,
    task: str,
    run_turn: Any,
    stage: str,
) -> None:
    """Send the agent back to its staged edits until they pass the checks.

    The edits stay on the stage, so a fix changes what is wrong instead of
    redoing the work. Gives up after FIX_ROUNDS; the commit then rejects
    what is left and says why.
    """
    for _round in range(FIX_ROUNDS):
        if not workspace.stage_is_dirty():
            return
        problem = workspace.check_edits(baseline)
        if problem is None:
            return
        guidance = repair_guidance(problem)
        run_turn(
            prompt=(
                "Your edits are still staged, but they fail the memory "
                f"checks:\n\n{problem}\n\n"
                + (f"{guidance}\n\n" if guidance else "")
                + "Fix the staged files in place so they pass. Keep what "
                "you wrote and change only what the message reports.\n\n"
                f"The task these edits are for:\n\n{task}"
            ),
            stage=f"{stage}-fix",
        )


def _run_agent(
    memory_dir: str | Path,
    *,
    agent: ClaudeCodeAgent,
    task: str,
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
        if baseline is None:
            # Committing against a tree that cannot be parsed would drop
            # the turn's edits and still report it done.
            raise TransactionError(
                "INVALID_TOPIC_FORMAT",
                "committed Topic files do not parse",
                retryable=True,
            )
        # The Write and Edit tools check each edit against it as it lands.
        workspace.turn_baseline = baseline
        stage = stage or "organize"

        def run_turn(*, prompt: str, stage: str) -> Any:
            return _turn(
                agent, workspace, audit, prompt=prompt,
                system_prompt=system_prompt, config=config,
                history_dir=history_dir, stage=stage,
                usage_logger=usage_logger,
            )

        result = run_turn(prompt=task, stage=stage)
        _fix_in_place(workspace, baseline, task, run_turn, stage)
        error = _commit_turn(workspace, baseline, audit)
        if error is not None:
            # A rejected turn was rolled back, so the pre-repair stage is the
            # committed state. Snapshot it now: reading it after the repair
            # edits would parse the model's malformed text and raise.
            repair_baseline = _baseline(workspace)
            workspace.turn_baseline = repair_baseline
            guidance = repair_guidance(error)
            run_turn(
                prompt=(
                    "Your edits were rejected and discarded:\n\n"
                    f"{error}\n\n"
                    + (f"{guidance}\n\n" if guidance else "")
                    + "The workspace is back to its state before this turn. "
                    "Redo the work, fixing what the message reports.\n\n"
                    f"{task}"
                ),
                stage=f"{stage}-repair",
            )
            _fix_in_place(workspace, repair_baseline, task, run_turn, stage)
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
