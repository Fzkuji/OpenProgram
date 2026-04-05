"""
Backward-compatible re-exports from agentic.meta_functions.

New code should import from agentic.meta_functions directly:
    from agentic.meta_functions import create, fix, create_skill, create_app
"""

from agentic.meta_functions import create, fix, create_skill, create_app
from agentic.meta_functions._helpers import (
    extract_code as _extract_code,
    validate_code as _validate_code,
    compile_function as _compile_function,
    save_function as _save_function,
    save_skill_template as _save_skill_template,
    find_function as _find_function,
    guess_name as _guess_name,
    get_source as _get_source,
    get_error_log as _get_error_log,
    _make_safe_builtins,
)

__all__ = ["create", "create_app", "fix", "create_skill"]
