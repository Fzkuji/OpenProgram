"""Validate owner-approved checks without granting model-selected execution."""
from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
import re


def validate_plan(value, request) -> dict:
    from .system_probe import OBSERVATION_ENTRIES
    from .native_checks import NATIVE_ENTRIES

    if (not isinstance(value, dict) or set(value) != {"schema", "checks"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or not isinstance(value["checks"], list) or not 1 <= len(value["checks"]) <= 32):
        raise ValueError("invalid verification plan")
    ids, assertions = set(), set()
    expected = {f"acceptance-{n}" for n in range(1, len(request.assertions) + 1)}
    for check in value["checks"]:
        if not isinstance(check, dict):
            raise ValueError("unsupported verification check")
        keys = {"id", "assertion_id", "entry", "timeout_seconds", "max_output_bytes"}
        if check.get("entry") == "test:python":
            keys.add("argv")
        if set(check) != keys:
            raise ValueError("unsupported verification check")
        if (not isinstance(check["id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", check["id"])
                or check["id"] in ids
                or not isinstance(check["assertion_id"], str) or check["assertion_id"] not in expected
                or check["assertion_id"] in assertions
                or not isinstance(check["entry"], str) or check["entry"] not in OBSERVATION_ENTRIES | NATIVE_ENTRIES
                or type(check["timeout_seconds"]) is not int or not 1 <= check["timeout_seconds"] <= 60
                or type(check["max_output_bytes"]) is not int or not 1 <= check["max_output_bytes"] <= 262144):
            raise ValueError("invalid verification check identity, entry or budget")
        if check["entry"] == "test:python":
            argv = check["argv"]
            if (not isinstance(argv, list) or not 1 <= len(argv) <= 32
                    or any(not isinstance(arg, str) or len(arg) > 4096 or "\0" in arg for arg in argv)
                    or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,507}\.py", argv[0])
                    or ".." in PurePosixPath(argv[0]).parts or str(PurePosixPath(argv[0])) != argv[0]):
                raise ValueError("candidate test requires a relative Python script and bounded argv")
        ids.add(check["id"])
        assertions.add(check["assertion_id"])
    if assertions != expected:
        raise ValueError("verification plan must cover every assertion exactly once")
    return deepcopy(value)


def resolve_check(plan, check_id) -> dict:
    if isinstance(check_id, str):
        for check in plan["checks"]:
            if check["id"] == check_id:
                return check
    raise ValueError("check_id is not in the frozen verification plan")
