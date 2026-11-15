"""Which process owns an execution.

One process per profile holds ``worker.lock`` and runs that profile's Jobs and
chat turns. A scheduled task runs its turn in a child process of its own, and
an interface started on its own (ACP, MCP) runs the turns it admits, so a turn
can belong to a process other than the lock holder. Such a process writes its
pid and start time into the lease; the lock holder's turns need neither, since
a new lock holder means the old one no longer runs anything.
"""
import os
import uuid


_instance: tuple[int, str] | None = None
_start_token: tuple[int, str | None] | None = None


def _this_instance() -> str:
    """Per process, also across a fork."""
    global _instance
    if _instance is None or _instance[0] != os.getpid():
        _instance = (os.getpid(), uuid.uuid4().hex)
    return _instance[1]


def _this_start_token() -> str | None:
    global _start_token
    if _start_token is None or _start_token[0] != os.getpid():
        from openprogram.host._platform.processes import process_start_token

        _start_token = (os.getpid(), process_start_token(os.getpid()))
    return _start_token[1]


def identified_process():
    """This process, named so that another process can tell whether it still runs."""
    return {"instance": _this_instance(), "pid": os.getpid(), "started": _this_start_token()}


def current_process_owner():
    from openprogram.state.store.session.session_log import is_main_writer

    owner = {"instance": _this_instance()}
    if not is_main_writer():
        owner.update(pid=os.getpid(), started=_this_start_token())
    return owner


def owned_by_another_process(lease) -> bool:
    """The turn runs, or ran, in a process other than this one."""
    owner = lease.get("process_owner")
    return isinstance(owner, dict) and owner.get("instance") != _this_instance()


def process_owner_may_be_alive(lease):
    owner = lease.get("process_owner")
    if not isinstance(owner, dict):
        return False
    if owner.get("instance") == _this_instance():
        return True
    pid, started = owner.get("pid"), owner.get("started")
    if not isinstance(pid, int) or not started or pid == os.getpid():
        return False
    from openprogram.host._platform.processes import process_start_token

    return process_start_token(pid) == started
