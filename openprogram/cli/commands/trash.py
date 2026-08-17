"""List and restore recoverable local deletion records."""
from __future__ import annotations

import sys


_SCOPE = (
    "Coverage: only local child-process deletions captured while a session and "
    "turn were bound; cron jobs, background tasks, remote backends, and bypassed "
    "deletion APIs are not included."
)


def _cmd_trash_list() -> int:
    from openprogram.sandbox.recoverable_delete import list_deleted

    records = list_deleted()
    if not records:
        print("No recoverable deletion records.")
    else:
        print("id  status  kind  run  original_path")
        for entry in records:
            print(
                f"{entry['id']}  {entry['status']}  {entry.get('kind', 'unknown')}  "
                f"{entry['session']}/{entry['turn']}  {entry.get('original_path', '')}"
            )
    print(_SCOPE)
    return 0


def _cmd_trash_restore(entry_id: str) -> int:
    from openprogram.sandbox.recoverable_delete import restore_deleted_anywhere

    try:
        destination = restore_deleted_anywhere(entry_id)
    except KeyError:
        print(f"Trash entry not found: {entry_id}", file=sys.stderr)
        print(_SCOPE, file=sys.stderr)
        return 1
    except FileExistsError as exc:
        print(f"Refusing to overwrite existing path: {exc.filename}", file=sys.stderr)
        print(_SCOPE, file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"Trash entry is no longer available: {entry_id}", file=sys.stderr)
        print(_SCOPE, file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as exc:
        print(f"Failed to restore trash entry {entry_id}: {exc}", file=sys.stderr)
        print(_SCOPE, file=sys.stderr)
        return 1
    print(f"Restored {entry_id} to {destination}")
    print(_SCOPE)
    return 0


__all__ = ["_cmd_trash_list", "_cmd_trash_restore"]
