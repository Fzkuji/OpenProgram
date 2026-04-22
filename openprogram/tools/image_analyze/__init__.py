"""image_analyze tool — re-exports TOOL + provider registry."""

from .image_analyze import DESCRIPTION, NAME, SPEC, _tool_check_fn, execute
from .registry import ImageAnalyzeProvider, ImageInput, registry

TOOL = {
    "spec": SPEC,
    "execute": execute,
    "check_fn": _tool_check_fn,
    "max_result_size_chars": 20_000,
}

__all__ = [
    "NAME",
    "SPEC",
    "TOOL",
    "execute",
    "DESCRIPTION",
    "ImageAnalyzeProvider",
    "ImageInput",
    "registry",
]
