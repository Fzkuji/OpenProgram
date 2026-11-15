#!/usr/bin/env bash
# Always-on weekly report: reads the state directory and the peers' latest
# commits. Starts nothing, spends nothing and changes nothing.
#   always_on_weekly.sh [--state-dir DIR] [--no-peers]
#   always_on_weekly.sh --print-task    the scheduled task to create once
set -euo pipefail

repository="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# A worktree has no virtualenv of its own; the main checkout's serves them all.
main_checkout="$(dirname "$(git -C "$repository" rev-parse --path-format=absolute --git-common-dir)")"
python="${OPENPROGRAM_PYTHON:-$main_checkout/.venv/bin/python}"
peers=(--peers "$repository/scripts/testing/always_on_peers.json")
arguments=()

while [ $# -gt 0 ]; do
  case "$1" in
    --no-peers) peers=() ;;
    --print-task)
      cat <<TASK
Create with the cron tool, from one dedicated conversation of the Main Agent on the stable instance:
  action: create, title: Always-on weekly check, cron: 0 9 * * 1, cwd: $main_checkout
  prompt:
Run the always-on weekly check, as the Weekly routine section of docs/reference/design/runtime/always-on-agents.html says.
Run scripts/testing/always_on_weekly.sh and read the peers listed on that page from each baseline in scripts/testing/always_on_peers.json to the latest commit.
Then, in your own worktree, change only that page in both languages and the peer baseline file.
Do not run scripts/testing/always_on_weekly_checks.sh, do not deploy, promote or build a batch.
End with one verdict in plain words: what is done, what is left, what needs a yes.
TASK
      exit 0 ;;
    *) arguments+=("$1") ;;
  esac
  shift
done

cd "$repository"
PYTHONPATH="$repository" exec "$python" scripts/testing/always_on_weekly_report.py \
  ${peers[@]+"${peers[@]}"} ${arguments[@]+"${arguments[@]}"}
