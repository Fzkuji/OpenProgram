"""Owner recovery of a self-update stuck in maintenance, run in a local terminal.

``openprogram self-update status|repair`` and the saved recovery script
(``recover.sh repair``, through :mod:`..delivery.bootstrap`) both run this
flow. Repair shows the plan, requires the owner to type a confirmation phrase
in an interactive terminal, then resumes the update supervisor and waits for a
verified recovery. Nothing here starts a chat turn or an LLM.
"""
from __future__ import annotations

import json
import sys
import time


def run_owner_recovery(update_id: str, *, verb: str = "repair", as_json: bool = False) -> int:
    """Print the recovery status (``verb="status"``) or run owner repair; return an exit code."""
    from . import owner_repair as repair
    from ..delivery.launcher import launch_supervisor
    from ..verification.verification_channel import _digest
    try:
        if verb == "status":
            value = repair.status(update_id)
            print(json.dumps(value, ensure_ascii=False, indent=None if as_json else 2))
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError("owner repair requires an interactive local terminal; confirmation cannot be bypassed")
        current = repair.status(update_id)
        if not current["maintenance"]:
            raise ValueError("this update has no maintenance to recover")
        pending = current["repair_id"] and not current["repair_cleanup_error"] and current["repair_deadline"] > time.time() and (
            current["repair_result"] is None or current["repair_result"]["status"] == "recovered")
        if not pending:
            plan = repair.preview_repair(update_id)
            digest = _digest(plan)
            print(json.dumps({key: plan[key] for key in ("update_id", "phase", "action", "target_revision")}, indent=2))
            print(f"Plan SHA-256: {digest}\nThis may restore the App and restart the default worker. Authorization expires in 10 minutes.")
            phrase = f"repair {update_id} {digest[:12]}"
            if input(f"Type '{phrase}' to confirm: ") != phrase:
                raise ValueError("owner repair was not confirmed")
            request = repair.approve_repair(update_id, digest)
            deadline = request["deadline"]
        else:
            deadline = current["repair_deadline"]
        launch_supervisor(update_id, resume=True)
        monotonic_deadline = time.monotonic() + max(0, deadline - time.time())
        while time.monotonic() < monotonic_deadline and time.time() < deadline:
            current = repair.status(update_id)
            if current["repair_cleanup_error"]:
                raise ValueError("owner repair cleanup failed; inspect and confirm a new attempt")
            result = current["repair_result"]
            if result is not None:
                if result["status"] == "failed":
                    raise ValueError("owner repair failed: " + result["error"])
                if not current["maintenance"]:
                    print(json.dumps(current, indent=2))
                    return 0
            time.sleep(0.2)
        raise ValueError("owner repair timed out; maintenance remains until verified recovery")
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Self-update recovery: {str(exc) or type(exc).__name__}", file=sys.stderr)
        return 1
