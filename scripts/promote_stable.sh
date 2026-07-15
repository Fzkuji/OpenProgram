#!/usr/bin/env bash
# Promote a verified commit to the stable instance.
#
# Layout this script assumes (see docs in the repo root README):
#   dev    = this repo checkout, run via `openprogram-dev`
#            (profile ~/.openprogram-dev, ports 18200/18209)
#   stable = git worktree ../OpenProgram-stable on branch `stable`,
#            run via `openprogram` (venv ~/.openprogram-stable-env,
#            default profile ~/.openprogram, ports 18100/18109)
#
# Usage:
#   scripts/promote_stable.sh            # promote origin/main
#   scripts/promote_stable.sh <commit>   # promote a specific commit
set -euo pipefail

STABLE_DIR="${OPENPROGRAM_STABLE_DIR:-$HOME/Documents/LLM Agent Harness/OpenProgram-stable}"
STABLE_BIN="$HOME/.openprogram-stable-env/bin/openprogram"
TARGET="${1:-origin/main}"

cd "$STABLE_DIR"
git fetch origin
git merge --ff-only "$TARGET"
git push origin stable

# Rebuild the bundled frontend (no-op ~seconds when nothing changed).
( cd web && npm install --silent && npm run build )

# Prebuild the docs site (gitignored build artifact) so the first /docs visit
# after a promote serves immediately instead of waiting on the auto-rebuild.
"$HOME/.openprogram-stable-env/bin/python" -m tools.docs_site.build

# Snapshot the live data dir's config before the new code touches it,
# so a bad release can be rolled back together with its config.
cp ~/.openprogram/config.json ~/.openprogram/config.json.pre-promote 2>/dev/null || true

"$STABLE_BIN" restart
echo "stable promoted to $(git rev-parse --short HEAD)"
