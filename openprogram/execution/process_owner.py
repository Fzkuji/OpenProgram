"""Same-host process evidence for startup recovery, separate from fencing."""
import os
import socket
import time

from openprogram.store.file_operations import process_start_identity


ADMISSION_OWNER_GRACE_SECONDS = 30.0


def current_process_owner():
    return {"host": socket.gethostname(), "pid": os.getpid(),
            "start": process_start_identity(),
            "admission_deadline": time.time() + ADMISSION_OWNER_GRACE_SECONDS}


def process_owner_may_be_alive(lease, *, lease_expires_at=None):
    owner = lease.get("process_owner")
    if not isinstance(owner, dict):
        return False  # Legacy records have no process evidence.
    if owner.get("host") != socket.gethostname():
        return True  # A local process query cannot disprove a foreign owner.
    deadline = lease_expires_at if lease_expires_at is not None else owner.get("admission_deadline")
    if isinstance(deadline, (int, float)) and deadline <= time.time():
        return False
    pid = owner.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    actual_start = process_start_identity(pid)
    expected_start = owner.get("start")
    if actual_start is None or expected_start is None:
        return True
    return actual_start == expected_start
