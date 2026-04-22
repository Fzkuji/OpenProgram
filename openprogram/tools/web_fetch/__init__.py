"""web_fetch tool."""

from .web_fetch import DESCRIPTION, NAME, SPEC, execute

TOOL = {
    "spec": SPEC,
    "execute": execute,
    # No check_fn / requires_env — stdlib-only fast path always works.
    # trafilatura improves extraction quality when installed but the
    # fallback stripper runs fine without it.
    "max_result_size_chars": 80_000,
}

__all__ = ["NAME", "SPEC", "TOOL", "execute", "DESCRIPTION"]
