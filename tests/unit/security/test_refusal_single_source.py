"""Every refusal the model reads comes from ``runtime/permissions/refusal.py``.

The scan walks the code on the tool path as source. It fails on a ``[denied]``
or ``[tool refused]`` literal, a ``"denied": True`` result, a ``sandbox
policy:`` message, a ``raise PermissionError(``, and on building ``Refusal`` or
``Refused`` anywhere else. ``ALLOWED`` names the exceptions and why each is not
a refusal of a tool call. A stale entry fails too, so the list only shrinks.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCANNED = (
    "openprogram/programs",
    "openprogram/runtime/permissions",
    "openprogram/runtime/agent/loop",
    "openprogram/extensions/mcp/server",
)
SOURCE = "openprogram/runtime/permissions/refusal.py"
_BROKER = "openprogram/programs/tools/computer_use/computer_use/code_mode/sandbox/broker.py"
_BROKER_WHY = "the GUI broker's own lifecycle: a revoked handle or a stale attempt, not a decision about the call"

# (file, finding) -> why it is not a refusal.
ALLOWED: dict[tuple[str, str], str] = {
    ("openprogram/programs/tools/agents/send_message/send_message/send_message.py", "[send_message refused]"):
        "argument error: the target is the caller's own turn",
    (_BROKER, "raise PermissionError('GUI broker revoked')"): _BROKER_WHY,
    (_BROKER, "raise PermissionError('unknown GUI resource or denied method')"): _BROKER_WHY,
    (_BROKER, "raise PermissionError('GUI operation cancelled or revoked')"): _BROKER_WHY,
    (_BROKER, "raise PermissionError('GUI resource revoked')"): _BROKER_WHY,
    (_BROKER, "raise PermissionError('GUI execution attempt is stale or admission is closed')"): _BROKER_WHY,
    ("openprogram/programs/_host/ui/catalog.py", "raise PermissionError('applications cannot write into .git')"):
        "validates an application's own storage path; the model does not call it",
    ("openprogram/programs/_host/framework/interface.py",
     "raise PermissionError('interface_requires_unrestricted_owner_connection')"):
        "websocket admission for a window, not a tool call",
    ("openprogram/programs/_host/ui/runner.py", "raise PermissionError(f'application requires {capability}')"):
        "a Program calling outside its declared capabilities, reported to that Program",
    ("openprogram/programs/_host/registry.py",
     "raise PermissionError(f'refusing to import unrecorded agentic source: {abs_path}')"):
        "validates a request path on hot reload; the model does not reach it",
    ("openprogram/programs/applications/browser/pages.py",
     "raise PermissionError(str(command.get('rejection_code') or 'command_rejected'))"):
        "a page command the browser rejected, relayed to its caller",
    ("openprogram/extensions/plugins/loader.py", "raise PermissionError(p.error)"):
        "plugin load state recorded on the plugin; refuses no tool call",
}

_REFUSED_TAG = re.compile(r"\[\w+ refused\]")


def _files():
    for scanned in SCANNED:
        yield from sorted((ROOT / scanned).rglob("*.py"))
    yield ROOT / "openprogram/extensions/plugins/loader.py"


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _literal_findings(node: ast.AST, docs: set[int]) -> list[str]:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or id(node) in docs:
        return []
    found = []
    if "[denied]" in node.value:
        found.append("[denied]")
    found.extend(_REFUSED_TAG.findall(node.value))
    if "sandbox policy:" in node.value:
        found.append("sandbox policy:")
    return found


def _findings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docs = _docstrings(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        found.extend(_literal_findings(node, docs))
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == "denied"
                        and isinstance(value, ast.Constant) and value.value is True):
                    found.append('"denied": True')
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "denied" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                    found.append("denied=True")
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in {"Refused", "Refusal", "Retry"}:
                found.append(f"{name}(...)")
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            if getattr(node.exc.func, "id", "") == "PermissionError":
                found.append("raise " + ast.unparse(node.exc))
    return found


def test_refusals_have_one_source():
    seen: set[tuple[str, str]] = set()
    unexpected = []
    for path in _files():
        relative = path.relative_to(ROOT).as_posix()
        if relative == SOURCE:
            continue
        for finding in _findings(path):
            entry = (relative, finding)
            seen.add(entry)
            if entry not in ALLOWED:
                unexpected.append(entry)
    assert not unexpected, f"a refusal must come from {SOURCE}: {unexpected}"
    assert not set(ALLOWED) - seen, f"stale allowlist entries: {set(ALLOWED) - seen}"


def test_every_refusal_reads_the_same_way():
    from openprogram.runtime.permissions.refusal import MCP_GATE_REFUSALS, Refusal

    item = Refusal("rule", "RULE_DENY", "bash", 'your deny rule "bash" matches', "Permission rules in this project")
    assert item.text == ('[blocked by rule] bash did not run: your deny rule "bash" matches. '
                         "To change this: Permission rules in this project.")
    for gate in MCP_GATE_REFUSALS.values():
        assert re.fullmatch(r"\[blocked by \w+\] .+ did not run: .+\. To change this: .+\.", gate.text)
