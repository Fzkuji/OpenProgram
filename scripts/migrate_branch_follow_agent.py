"""Put branches that copied their Agent's model back to following the Agent.

The Agent switch used to copy the Agent's model onto the branch, and every
send stored the web search switch; both made a branch ignore later edits to
its Agent. This clears, on every branch of every conversation, a model equal
to the branch Agent's own and a web search switch equal to the Agent's
choice (``branch_config.follow_agent_resets``). A model the person picked
stays.

    python -m scripts.migrate_branch_follow_agent            # dry run: list only
    python -m scripts.migrate_branch_follow_agent --apply    # back up, then write

``--apply`` first writes every touched conversation's settings to a backup
JSON beside the state directory. Running it twice changes nothing the
second time. The evaluation profile is never touched (``--profile`` picks
which data directory the process reads, as every CLI command does).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def plan(db) -> dict:
    from openprogram.runtime.conversation.session_config import _agent_settings
    from openprogram.state.store.session.branch_config import follow_agent_resets
    out = {}
    for row in db.list_sessions(limit=1_000_000, include_archived=True):
        session_id = row.get("id")
        meta = db.get_session(session_id) or {}
        resets = follow_agent_resets(meta, _agent_settings)
        if resets:
            out[session_id] = {"resets": resets, "backup": {
                "branch_refs": meta.get("branch_refs"),
                **{key: meta.get(key) for key in ("provider_override", "model_override", "web_search")},
            }}
    return out


def apply(db, planned: dict) -> None:
    for session_id, entry in planned.items():
        for branch_id, fields in entry["resets"].items():
            db.update_branch_config(session_id, branch_id, {field: None for field in fields})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="back up, then write (default: dry run)")
    args = parser.parse_args(argv)
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.paths import get_state_dir
    db = default_db()
    planned = plan(db)
    for session_id, entry in planned.items():
        print(f"{session_id}: {json.dumps(entry['resets'])}")  # noqa: T201 - CLI output
    print(f"{len(planned)} conversation(s) to change")  # noqa: T201 - CLI output
    if not args.apply or not planned:
        return 0
    backup = Path(get_state_dir()) / f"branch-follow-agent-backup-{int(time.time())}.json"
    backup.write_text(json.dumps({sid: entry["backup"] for sid, entry in planned.items()},
                                 ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"backup: {backup}")  # noqa: T201 - CLI output
    apply(db, planned)
    return 0


if __name__ == "__main__":
    sys.exit(main())
