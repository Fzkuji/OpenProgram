"""apply_patch tool — multi-file structured patch."""

from .apply_patch import NAME, SPEC, execute

TOOL = {"spec": SPEC, "execute": execute}

__all__ = ["NAME", "SPEC", "TOOL", "execute"]
