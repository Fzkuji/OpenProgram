#!/usr/bin/env bash
set -euo pipefail

# Kept as a compatibility entry point. Local development no longer uses a
# second stable worktree or a second Python environment.
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if test "$#" -ne 0; then
  printf 'promote_stable no longer accepts a separate commit; checkout the desired commit first\n' >&2
  exit 2
fi

exec "$repo_root/scripts/refresh-local-app.sh"
