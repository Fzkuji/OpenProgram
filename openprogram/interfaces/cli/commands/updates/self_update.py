"""``openprogram self-update`` — owner-only recovery without entering a chat turn or starting an LLM."""
from __future__ import annotations


def _cmd_self_update(args) -> int:
    from openprogram.operations.self_update.repair.owner_terminal import run_owner_recovery

    return run_owner_recovery(
        args.update_id, verb=args.self_update_verb, as_json=bool(getattr(args, "json", False)))
