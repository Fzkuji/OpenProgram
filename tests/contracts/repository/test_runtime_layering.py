"""The runtime packages depend on each other in one direction.

``openprogram.runtime`` is split by concept:

* ``agent`` — one model call: the ``Agent`` class, ``agent()``, and the
  tool loop (the code of Agentic Programming).
* ``context`` — the ``Context`` class and what the model sees.
* ``permissions`` — what a tool call may do, and who decides.
* ``execution`` — the pause, cancel and recovery control plane.
* ``events`` — the event bus.
* ``conversation`` — chat sessions and turns, built on the Agent.
* ``jobs`` — background runs.
* ``agent_registry`` — saved Agent configurations.
* ``scheduler``, ``proactive``, ``commands`` — features built on the above.

The lower packages must not reach up into the features built on them,
``execution`` does not depend on the model-call core, and ``context``,
``permissions`` and ``events`` do not either. ``execution`` does not know
about Programs either: the engines built on it register hooks for the
tools they run (a chat Goal's settlement, for one). The package now named
``openprogram.runtime.agent`` once mixed all of these; no module imports
one of its former submodules that moved elsewhere, nor the call core's
former name ``openprogram.runtime.agentic_programming``.

The scan reads every module under ``openprogram/`` as source, so it also sees
imports inside functions and string imports through
``importlib.import_module`` and ``__import__``. Imports that break a rule
today are listed in ``ALLOWED``; the test fails on a new one and on an entry
that no longer occurs, so the list only shrinks.
"""
from __future__ import annotations

import ast
from pathlib import Path

_RUNTIME_ENTRYPOINT_PREFIXES = (
    # The runtime packages, before they were grouped under openprogram.runtime.
    *(
        (f"openprogram.{old}", f"openprogram.runtime.{new}")
        for old, new in (
            ("agent", "agent"),
            ("agentic_programming", "agentic_programming"),
            ("execution", "execution"),
            ("context", "context"),
            ("commands", "commands"),
            ("events", "events"),
            ("proactive", "proactive"),
            ("surfaces.scheduler", "scheduler"),
        )
    ),
    # The modules of the former openprogram.runtime.agent package, which was
    # split by concept. Its questions and process_runner modules kept their
    # paths; the call core then took the package name.
    *(
        (f"openprogram.runtime.agent.{old}", new)
        for old, new in (
            ("agent_loop", "openprogram.runtime.agent.loop"),
            ("agent", "openprogram.runtime.agent.loop.driver"),
            ("session", "openprogram.runtime.agent.loop.session"),
            ("types", "openprogram.runtime.agent.loop.types"),
            ("messages", "openprogram.runtime.agent.loop.messages"),
            ("retry", "openprogram.runtime.agent.loop.retry"),
            ("failure_policy", "openprogram.runtime.agent.loop.failure_policy"),
            ("provider_lifecycle", "openprogram.runtime.agent.loop.provider_lifecycle"),
            ("turn_runtime", "openprogram.runtime.conversation.turn_runtime"),
            ("dispatcher", "openprogram.runtime.conversation.turn_runtime"),
            ("chat", "openprogram.runtime.conversation.chat"),
            ("session_config", "openprogram.runtime.conversation.session_config"),
            ("session_model", "openprogram.runtime.conversation.session_model"),
            ("inbox", "openprogram.runtime.conversation.inbox"),
            ("open_work", "openprogram.runtime.conversation.open_work"),
            ("owner_turn", "openprogram.runtime.conversation.owner_turn"),
            ("sub_agent_run", "openprogram.runtime.conversation.sub_agent_run"),
            ("_rewind", "openprogram.runtime.conversation.rewind"),
            ("internals._event_parsing", "openprogram.runtime.conversation.event_parsing"),
            ("internals._merge", "openprogram.runtime.conversation.merge"),
            ("internals._model_tools", "openprogram.runtime.conversation.model_tools"),
            ("internals._revert", "openprogram.runtime.conversation.revert"),
            ("internals._turn_lifecycle", "openprogram.runtime.conversation.turn_lifecycle"),
            ("internals._workdir", "openprogram.runtime.conversation.workdir"),
            ("history_ownership", "openprogram.runtime.conversation.history_ownership"),
            ("surface_context", "openprogram.runtime.conversation.surface_context"),
            ("turn_request_context", "openprogram.runtime.conversation.turn_request_context"),
            ("plan_mode", "openprogram.runtime.conversation.plan_mode"),
            ("always_on", "openprogram.runtime.conversation.always_on"),
            ("production_driver", "openprogram.runtime.conversation.production_driver"),
            ("continuation", "openprogram.runtime.conversation.checkpoint"),
            ("run_control", "openprogram.runtime.conversation.run_control"),
            ("management.gating", "openprogram.extensions.gating"),
            ("management", "openprogram.runtime.agent_registry"),
            ("job", "openprogram.runtime.jobs"),
            ("resource_governance", "openprogram.runtime.jobs.resource_governance"),
            ("permissions", "openprogram.runtime.permissions"),
            ("authority", "openprogram.runtime.permissions.authority"),
            ("attended", "openprogram.runtime.permissions.attended"),
            ("session_db", "openprogram.runtime.context.session_db"),
            ("exec", "openprogram.host.processes.command"),
        )
    ),
    # The call core, before it took the name openprogram.runtime.agent. Three
    # of its modules were renamed so no current module reuses the name of a
    # submodule listed above.
    ("openprogram.runtime.agentic_programming.agent", "openprogram.runtime.agent.agent_function"),
    ("openprogram.runtime.agentic_programming.continuation", "openprogram.runtime.agent.durable_steps"),
    ("openprogram.runtime.agentic_programming.session", "openprogram.runtime.agent.follow_up_session"),
    ("openprogram.runtime.agentic_programming", "openprogram.runtime.agent"),
)

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "openprogram"
RUNTIME = "openprogram.runtime"

# Old module paths, from the execution store's rename table.
REMOVED = tuple(
    old for old, _ in _RUNTIME_ENTRYPOINT_PREFIXES if old.startswith(RUNTIME + ".")
)

FEATURES = tuple(
    f"{RUNTIME}.{name}"
    for name in ("conversation", "jobs", "agent_registry", "scheduler", "proactive", "commands")
)
CALL_CORE = (f"{RUNTIME}.agent",)
PROGRAMS = ("openprogram.programs",)

# (importing package, packages it must not import)
RULES = (
    (f"{RUNTIME}.agent", FEATURES),
    (f"{RUNTIME}.context", FEATURES + CALL_CORE),
    (f"{RUNTIME}.permissions", FEATURES + CALL_CORE),
    (f"{RUNTIME}.execution", FEATURES + CALL_CORE),
    # execution imports programs
    (f"{RUNTIME}.execution", PROGRAMS),
    (f"{RUNTIME}.events", FEATURES + CALL_CORE),
)

# (file, imported module) pairs that break a rule today. Remove an entry
# when its import goes away; never add one.
ALLOWED: frozenset[tuple[str, str]] = frozenset({
})


def _within(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


def _module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported_modules(path: Path) -> set[str]:
    """Every module ``path`` imports, resolved to absolute names."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = _module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                source = ".".join(base + ([node.module] if node.module else []))
            else:
                source = node.module or ""
            found.add(source)
            # ``from openprogram.runtime import conversation`` imports a package.
            found.update(f"{source}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            callee = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else ""
            )
            first = node.args[0]
            if callee in {"import_module", "__import__"} and isinstance(first, ast.Constant):
                if isinstance(first.value, str):
                    found.add(first.value)
    return found


def _existing(names: set[str], modules: set[str]) -> set[str]:
    """Keep the names that are modules (drop ``package.attribute`` guesses)."""
    return {name for name in names if name in modules}


def _scan() -> tuple[dict[tuple[str, str], str], list[str]]:
    paths = [p for p in sorted(PACKAGE.rglob("*.py")) if "__pycache__" not in p.parts]
    modules = {_module_name(p) for p in paths}
    violations: dict[tuple[str, str], str] = {}
    removed: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        importer = _module_name(path)
        raw = _imported_modules(path)
        removed += [f"{relative}: {name}" for name in sorted(raw) if _within(name, REMOVED)]
        imported = _existing(raw, modules)
        for package, forbidden in RULES:
            if not _within(importer, (package,)):
                continue
            for name in sorted(imported):
                if _within(name, forbidden):
                    violations[(relative, name)] = package
    return violations, removed


def test_no_module_imports_a_removed_runtime_package():
    _, removed = _scan()
    assert removed == []


def test_runtime_and_state_do_not_import_the_server():
    offenders = sorted(
        f"{_module_name(path)} imports {name}"
        for top in ("runtime", "state")
        for path in (PACKAGE / top).rglob("*.py")
        for name in _imported_modules(path)
        if _within(name, ("openprogram.interfaces.server",))
    )
    assert offenders == []


def test_runtime_packages_import_in_one_direction():
    violations, _ = _scan()
    new = sorted(pair for pair in violations if pair not in ALLOWED)
    stale = sorted(pair for pair in ALLOWED if pair not in violations)
    assert new == [], "new layering violations (fix the import; do not allow it):\n" + "\n".join(
        f"  {file}: {name}  [{violations[(file, name)]}]" for file, name in new
    )
    assert stale == [], "these allowed imports are gone; delete them from ALLOWED:\n" + "\n".join(
        f"  {pair!r}," for pair in stale
    )
