"""Writer-capacity artifacts and complete-message packing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .tokenization import TokenCounter


SCHEMA = "scriptorium-writer-capacity-v3"
# The format did not change when the project was renamed, so a calibration
# recorded under the former name still loads.
ACCEPTED_SCHEMAS = (SCHEMA, "nativemem-writer-capacity-v3")


class MessageTooLargeError(ValueError):
    """One complete message cannot fit within the calibrated Writer limit."""


@dataclass(frozen=True)
class WriterCapacity:
    model: str
    writer_protocol_sha256: str
    safe_input_tokens: int
    tokenizer: dict[str, Any]

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        model: str,
        writer_protocol_sha256: str,
    ) -> WriterCapacity:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if value.get("schema") not in ACCEPTED_SCHEMAS:
            raise ValueError("unsupported Writer capacity schema")
        if value.get("model") != model:
            raise ValueError("Writer capacity model does not match")
        if value.get("writer_protocol_sha256") != writer_protocol_sha256:
            raise ValueError("Writer capacity protocol does not match")
        status = value.get("status")
        if status not in (None, "complete"):
            # "inconsistent" means a larger input outperformed a smaller one,
            # so the numbers do not describe capacity and must not be used.
            raise ValueError(
                f"Writer capacity artifact is not usable: status={status}"
            )
        inversions = value.get("capacity_inversions") or []
        if inversions:
            raise ValueError(
                f"Writer capacity artifact reports {len(inversions)} "
                "capacity inversion(s); recalibrate before using it"
            )
        safe_input_tokens = value.get("safe_input_tokens")
        if not isinstance(safe_input_tokens, int) or safe_input_tokens < 1:
            raise ValueError("safe_input_tokens must be a positive integer")
        tokenizer = value.get("tokenizer")
        if not isinstance(tokenizer, dict):
            raise ValueError("Writer capacity tokenizer is missing")
        return cls(
            model=model,
            writer_protocol_sha256=writer_protocol_sha256,
            safe_input_tokens=safe_input_tokens,
            tokenizer=tokenizer,
        )


def pack_complete_messages(
    sessions: list[dict[str, Any]],
    *,
    max_input_tokens: int,
    render_batch: Callable[[list[dict[str, Any]]], str],
    token_counter: TokenCounter,
) -> list[list[dict[str, Any]]]:
    """Fill Writer batches without splitting message text."""
    if max_input_tokens < 1:
        raise ValueError("max_input_tokens must be positive")
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for session in sessions:
        turns = list(session.get("turns", []))
        refs = list(session.get("refs", []))
        if len(turns) != len(refs):
            raise ValueError("session turns and refs must have equal length")
        fragment_index: int | None = None
        for turn, ref in zip(turns, refs):
            while True:
                if fragment_index is None:
                    fragment = {**session, "turns": [turn], "refs": [ref]}
                    candidate = [*current, fragment]
                else:
                    fragment = {
                        **current[fragment_index],
                        "turns": [*current[fragment_index]["turns"], turn],
                        "refs": [*current[fragment_index]["refs"], ref],
                    }
                    candidate = [*current]
                    candidate[fragment_index] = fragment
                if token_counter.count(render_batch(candidate)) <= max_input_tokens:
                    current = candidate
                    fragment_index = len(current) - 1
                    break
                if current:
                    batches.append(current)
                    current = []
                    fragment_index = None
                    continue
                raise MessageTooLargeError(
                    "one complete message exceeds the calibrated Writer input limit"
                )
    if current:
        batches.append(current)
    return batches


def is_passing_level(level: dict[str, Any]) -> bool:
    return (
        level.get("pass_rate") == 1.0
        and level.get("source_coverage_min") == 1.0
        and level.get("fact_coverage_min") == 1.0
    )


def find_capacity_inversions(levels: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Report (failing, passing) pairs where a larger input outperforms a smaller one.

    Writer capacity cannot genuinely improve as the input grows, so such a pair
    means something other than capacity decided the result — a turn budget, a
    flaky trial, or too few trials. Reporting it prevents the largest passing
    level from being read as the answer when smaller levels failed.
    """
    ordered = sorted(levels, key=lambda level: int(level["candidate_tokens"]))
    inversions = []
    for index, level in enumerate(ordered):
        if is_passing_level(level):
            continue
        # Inconclusive levels were cut off, not out-of-capacity.
        if int(level.get("inconclusive_trials", 0) or 0) >= int(level.get("trials", 0) or 0):
            continue
        smaller = int(level["candidate_tokens"])
        for larger in ordered[index + 1:]:
            if is_passing_level(larger):
                inversions.append((smaller, int(larger["candidate_tokens"])))
                break
    return inversions


def select_writer_capacities(levels: list[dict[str, Any]]) -> dict[str, int]:
    """Select the cheapest fully correct level and the largest passing level."""
    passing = [level for level in levels if is_passing_level(level)]
    if not passing:
        raise ValueError("no Writer capacity level completed the fixed workload")
    recommended = min(
        passing,
        key=lambda level: (
            float(level["estimated_cost_usd_mean"]),
            float(level["elapsed_seconds_mean"]),
            int(level["candidate_tokens"]),
        ),
    )
    largest = max(passing, key=lambda level: int(level["candidate_tokens"]))
    return {
        "recommended_input_tokens": int(
            recommended.get(
                "max_batch_input_tokens_min",
                recommended["candidate_tokens"],
            )
        ),
        "recommended_candidate_tokens": int(recommended["candidate_tokens"]),
        "max_tested_passing_input_tokens": int(
            largest.get(
                "max_batch_input_tokens_min",
                largest["candidate_tokens"],
            )
        ),
        "max_passing_candidate_tokens": int(largest["candidate_tokens"]),
    }
