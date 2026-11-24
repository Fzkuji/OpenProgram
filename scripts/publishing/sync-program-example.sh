#!/usr/bin/env bash
# Copy a user Program's checked commit into examples/applications/<name>, so it
# ships with OpenProgram. The Program itself stays in
# ~/.openprogram/programs/<name>, its own Git repository, where the loader reads
# it; only the commit `openprogram programs check` recorded there is copied, never
# uncommitted edits. Commit the result like any other change:
#
#     scripts/publishing/sync-program-example.sh auto_research
#     git commit -- examples/applications/auto_research
set -euo pipefail

name=${1:?usage: sync-program-example.sh <name>}
src="$HOME/.openprogram/programs/$name"
repo=$(git rev-parse --show-toplevel)
dest="$repo/examples/applications/$name"

commit=$(git -C "$src" rev-parse --verify refs/openprogram/checked) || {
  echo "$src has no checked commit; run: openprogram programs check $src" >&2
  exit 1
}

rm -rf "$dest"
mkdir -p "$dest"
git -C "$src" archive "$commit" | tar -x -C "$dest"
echo "examples/applications/$name <- $name $(git -C "$src" rev-parse --short "$commit")"
