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
        if check.get("entry") == "ui:main" and "interaction" in check:
            keys.add("interaction")
        if set(check) != keys:
            raise ValueError("unsupported verification check")
        if (not isinstance(check["id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", check["id"])
                or check["id"] in ids
                or not isinstance(check["assertion_id"], str) or check["assertion_id"] not in expected
                or check["assertion_id"] in assertions
                or not isinstance(check["entry"], str) or check["entry"] not in OBSERVATION_ENTRIES | NATIVE_ENTRIES | {"ui:main"}
                or type(check["timeout_seconds"]) is not int or not 1 <= check["timeout_seconds"] <= 60
                or type(check["max_output_bytes"]) is not int
                or not 1 <= check["max_output_bytes"] <= (1572864 if check["entry"] == "ui:main" else 262144)):
            raise ValueError("invalid verification check identity, entry or budget")
        if "interaction" in check:
            interaction = check["interaction"]
            scroll = (isinstance(interaction, dict) and set(interaction) == {"kind", "delta_y"}
                      and interaction["kind"] == "scroll" and type(interaction["delta_y"]) is int
                      and interaction["delta_y"] != 0 and -1200 <= interaction["delta_y"] <= 1200)
            view = (isinstance(interaction, dict) and set(interaction) == {"kind", "target"}
                    and interaction["kind"] == "view" and interaction["target"] in ("session", "dag"))
            fixture = (isinstance(interaction, dict) and set(interaction) == {
                "kind", "object_id", "action", "initial_title", "title", "cleanup"}
                and interaction["kind"] == "test_object" and interaction["action"] == "rename"
                and interaction["cleanup"] == "restore-and-remove"
                and isinstance(interaction["object_id"], str)
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", interaction["object_id"])
                and all(isinstance(interaction[key], str) and 1 <= len(interaction[key]) <= 120
                        and interaction[key].strip() == interaction[key]
                        and not any(ord(c) < 32 for c in interaction[key]) for key in ("initial_title", "title"))
                and interaction["initial_title"] != interaction["title"])
            if not (scroll or view or fixture):
                raise ValueError("UI interaction requires an approved bounded scroll, perspective or isolated rename")
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


def required_ui_protocol(checks) -> int:
    """Capability floor for an already validated frozen plan."""
    return max((4 if check.get("interaction", {}).get("kind") == "test_object" else
                3 if check.get("interaction", {}).get("kind") == "view" else
                2 if "interaction" in check else 1 for check in checks), default=1)


def resolve_check(plan, check_id) -> dict:
    if isinstance(check_id, str):
        for check in plan["checks"]:
            if check["id"] == check_id:
                return check
    raise ValueError("check_id is not in the frozen verification plan")
