"""What a directory walk of the file tools (grep, glob) leaves out.

The host's read policy decides what no walk may enter; a check-in, which
nobody watches, also keeps off the dangerous paths ``permissions.file_safety``
names, which a walk from a parent directory would otherwise reach.
"""
from __future__ import annotations


def read_denier():
    """The host's deny test for a directory walk, plus a check-in's dangerous
    paths, which a walk from a parent directory would otherwise reach."""
    from openprogram.host.sandbox import read_denier as policy_denier
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.runtime.permissions.file_safety import dangerous_path, turn_dirs
    from openprogram.runtime.permissions.identity import check_in

    denied = policy_denier()
    request = get_turn_request()
    if request is None or not check_in(request):
        return denied
    dirs = turn_dirs(request)
    return lambda target: bool(denied is not None and denied(target)) or dangerous_path(target, dirs, unwatched_read=True) is not None
