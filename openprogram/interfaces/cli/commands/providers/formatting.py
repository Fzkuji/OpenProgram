"""Terminal prompts and credential summaries for ``openprogram providers``."""
from __future__ import annotations

from openprogram.models.auth.summary import format_duration as _fmt_duration
from openprogram.models.auth.summary import mask_secret as _mask
from openprogram.models.auth.summary import payload_summary as _payload_summary

__all__ = ["_confirm", "_fmt_duration", "_mask", "_payload_summary"]


def _confirm(question: str, *, default: bool) -> bool:
    """Prompt for y/n; Enter accepts ``default``. Returns the bool.

    Accepts y/yes/n/no (case-insensitive). Any other input repeats the
    prompt. EOF/^C propagate — the wizard handles them at the top
    level so each sub-step doesn't have to."""
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{question} {hint} ").strip().lower()
        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  (answer y or n)")

