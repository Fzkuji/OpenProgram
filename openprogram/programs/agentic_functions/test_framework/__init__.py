"""Small readiness checker used by framework tests."""

__test__ = False

import json
import re

from openprogram.agentic_programming.function import agentic_function


_VAGUE_PATTERNS = [
    r"^你看看[。！!]?$",
    r"^看一下[。！!]?$",
    r"^帮我看看[。！!]?$",
    r"^check( it)?$",
    r"^take a look$",
]


def _extract_instruction(task: str) -> str:
    match = re.search(r"Instruction:\s*(.*)\Z", task or "", re.DOTALL)
    if match:
        return match.group(1).strip()
    return (task or "").strip()


def _is_vague(instruction: str) -> bool:
    cleaned = " ".join((instruction or "").split())
    if not cleaned:
        return True
    return any(re.fullmatch(pattern, cleaned, re.IGNORECASE) for pattern in _VAGUE_PATTERNS)


@agentic_function

def test_framework(task: str) -> str:
    """Return JSON readiness for whether an instruction is specific enough."""
    instruction = _extract_instruction(task)
    if _is_vague(instruction):
        return json.dumps(
            {
                "ready": False,
                "question": "请具体说明希望我检查或修改什么，例如预期行为、报错信息或目标变更。",
            },
            ensure_ascii=False,
        )
    return json.dumps({"ready": True}, ensure_ascii=False)


test_framework.__test__ = False
