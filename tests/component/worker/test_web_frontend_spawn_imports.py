from __future__ import annotations

import types
from pathlib import Path

SOURCE = Path("openprogram/worker/web.py")
SPAWN_HELPER = "no_window_creation_flags"


def _compile_module() -> types.CodeType:
    return compile(SOURCE.read_text(encoding="utf-8"), str(SOURCE), "exec")


def _find_code(code: types.CodeType, name: str) -> types.CodeType:
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            if const.co_name == name:
                return const
            found = _find_code(const, name)
            if found is not None:
                return found
    return None


def _resolution(func_name: str, symbol: str) -> str:
    """How `symbol` resolves inside `func_name`: local, closure, or global."""
    code = _find_code(_compile_module(), func_name)
    assert code is not None, f"{func_name} not found in {SOURCE}"
    if symbol in code.co_varnames:
        return "local"
    if symbol in code.co_freevars:
        return "closure"
    if symbol in code.co_names:
        return "global"
    return "unreferenced"


def test_every_spawn_site_imports_the_creationflags_helper() -> None:
    """Each function calling no_window_creation_flags must import it itself.

    web.py imports the helper inside _ensure_built, which is function-local and
    invisible to the other spawn sites. start_web_frontend and _loop both called
    it while resolving it as a module global that web.py never defines, so
    spawning the frontend raised NameError. Both call sites wrap Popen in
    `except OSError`, which does not catch NameError, so it propagated to the
    caller instead of being reported as a spawn failure.
    """
    for func_name in ("_ensure_built", "start_web_frontend", "_loop"):
        assert _resolution(func_name, SPAWN_HELPER) in {"local", "closure"}, (
            f"{func_name} resolves {SPAWN_HELPER} as a module global, but "
            f"{SOURCE} defines no such global — calling it raises NameError"
        )
