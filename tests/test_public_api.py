"""Public API surface tests."""

from agentic import __all__ as public_api
from agentic import edit, fix
from agentic.meta_functions import fix as meta_fix


def test_auto_trace_package_is_exported_in_public_api():
    """Top-level package should export auto_trace_package alongside auto_trace_module."""
    assert "auto_trace_package" in public_api


def test_fix_is_exported_in_public_api_as_edit_alias():
    """Top-level fix import should remain available as a backward-compatible alias."""
    assert "fix" in public_api
    assert fix is edit
    assert meta_fix is edit
