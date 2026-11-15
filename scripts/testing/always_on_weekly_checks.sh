#!/usr/bin/env bash
# Always-on weekly checks. These start processes, so they run in the session
# that starts a batch and never from the weekly scheduled task.
#   always_on_weekly_checks.sh [--state-dir DIR] [--live]
# The tests run first, then a tick rehearsal on a credential-free copy of the
# state. --live also runs the smoke test, which spends model cost.
set -euo pipefail

repository="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# A worktree has no virtualenv of its own; the main checkout's serves them all.
main_checkout="$(dirname "$(git -C "$repository" rev-parse --path-format=absolute --git-common-dir)")"
python="${OPENPROGRAM_PYTHON:-$main_checkout/.venv/bin/python}"
state_dir="$HOME/.openprogram"
live=false

while [ $# -gt 0 ]; do
  case "$1" in
    --state-dir) state_dir="$2"; shift ;;
    --live) live=true ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

# Without the key pytest skips the smoke test and exits 0, which would read as a pass.
if [ "$live" = true ] && [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "--live needs OPENROUTER_API_KEY" >&2
  exit 2
fi

cd "$repository"
PYTHONPATH="$repository" "$python" -m pytest -q \
  tests/unit/agent/test_always_on.py \
  tests/component/runtime/test_always_on_end_to_end.py \
  tests/component/runtime/test_always_on_crash_matrix.py \
  tests/component/scheduler/test_scheduled_run_endings.py \
  tests/integration/execution/test_scheduled_run_processes.py

home="$(mktemp -d "${TMPDIR:-/tmp}/always-on-rehearsal.XXXXXX")"
trap 'rm -rf "$home"' EXIT
mkdir -p "$home/.openprogram"
# An allowlist, so a new credential file cannot slip in. Not copied: auth/,
# keys, executions.db*, channels/, mcp_tokens/, mcp_servers.json, config.json.
for entry in agents agents.json projects always_on session_aliases.json; do
  [ -e "$state_dir/$entry" ] || continue
  rsync -a --exclude 'auth/' --exclude '*.lock' --exclude '*.pid' --exclude '*.port' \
    --exclude '*token*' --exclude '*secret*' --exclude '*credential*' --exclude '.env' \
    "$state_dir/$entry" "$home/.openprogram/"
done

env -i HOME="$home" PYTHONPATH="$repository" "$python" - <<'PYTHON'
import json
import os
import threading
import time

from openprogram.interfaces import channels
from openprogram.runtime.conversation.always_on import ticker
from openprogram.state.paths import get_state_dir
from openprogram.state.store.session.session_state_file import read_files
from scripts.testing.always_on_weekly_report import STATE_NAME, conversation_directories


def may_fire(directory, now):
    """Read from the copy and not from ticker code, so a ticker that fires too
    much is caught. A check-in cut off by a restart may set its next wake to now."""
    state = json.loads(read_files(directory).get(STATE_NAME) or "{}")
    next_wake = state.get("next_wake_at")
    return bool(state.get("check_in")) or (isinstance(next_wake, (int, float)) and next_wake <= now)


built = []
original_build = channels.build_channel


def build_channel(*arguments, **keywords):
    built.append(arguments)
    return original_build(*arguments, **keywords)


channels.build_channel = build_channel
now = time.time()
due = {directory.name for directory in conversation_directories(get_state_dir()) if may_fire(directory, now)}
results = []
worker = threading.Thread(target=lambda: results.extend(ticker.tick(now)), daemon=True)
worker.start()
worker.join(300)
unexpected = sorted(str(row.get("session_id")) for row in results if row.get("session_id") not in due)
print(f"rehearsal: {len(due)} due, {len(results)} fired, {len(unexpected)} unexpected, {len(built)} channel adapters", flush=True)
if worker.is_alive():
    print("rehearsal: the tick did not finish", flush=True)
failed = worker.is_alive() or bool(unexpected) or bool(built)
# Turn threads may still be running; nothing in this copy is worth waiting for.
os._exit(1 if failed else 0)
PYTHON

if [ "$live" = true ]; then
  PYTHONPATH="$repository" "$python" -m pytest -q -m live tests/live/always_on/test_smoke.py
fi
