#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
web_dir="$repo_root/apps/web"
source_dir="$web_dir/out"
next_build_dir="$web_dir/.next"
target_dir="$repo_root/openprogram/interfaces/server/_frontend"
docs_source_dir="$repo_root/docs/_site"
docs_target_dir="$target_dir/docs"

# --node-modules DIR is a local build from a verified toolchain: node_modules
# is cloned from DIR (never linked), `npm ci` is skipped, and the docs build
# uses OPENPROGRAM_DOCS_PYTHON, whose environment already has the dependencies.
node_modules_source=""
if test "${1:-}" = --node-modules; then
  node_modules_source="$2"
  test -d "$node_modules_source/node_modules" || {
    printf 'toolchain has no node_modules: %s\n' "$node_modules_source" >&2
    exit 1
  }
  shift 2
fi

clone_tree() {
  if test "$(uname -s)" = Darwin; then
    cp -c -R -p "$1" "$2" 2>/dev/null || { rm -rf "$2"; cp -R -p "$1" "$2"; }
  else
    cp --reflink=auto -R -p "$1" "$2"
  fi
}

command -v npm >/dev/null 2>&1 || {
  printf 'npm is required to stage release Web assets\n' >&2
  exit 1
}

# Office is installed separately after user consent; exclude stale staged modules.
rm -rf "$web_dir/public/document-assets/office"

(
  cd "$repo_root"
  unset npm_config_workspace npm_config_workspaces
  if test -n "$node_modules_source"; then
    (cd "$node_modules_source" && find . -type d -name node_modules -prune -print) |
      while IFS= read -r relative; do
        target="$repo_root/$relative"
        rm -rf "$target"
        mkdir -p "$(dirname "$target")"
        clone_tree "$node_modules_source/$relative" "$target"
      done
  else
    npm ci --ignore-scripts
  fi
  rm -rf "$source_dir" "$next_build_dir"
  NEXT_IGNORE_INCORRECT_LOCKFILE=1 npm run build --workspace apps/web
  npm run build:standalone --workspace apps/cli
  npm run build:release --workspace apps/cli
)

test -f "$source_dir/index.html" || {
  printf 'Next.js export did not produce %s/index.html\n' "$source_dir" >&2
  exit 1
}
test -f "$repo_root/apps/cli/dist/index-standalone.cjs" || {
  printf 'CLI build did not produce apps/cli/dist/index-standalone.cjs\n' >&2
  exit 1
}

if test -n "$node_modules_source"; then
  test -n "${OPENPROGRAM_DOCS_PYTHON:-}" || {
    printf -- '--node-modules needs OPENPROGRAM_DOCS_PYTHON\n' >&2
    exit 1
  }
  (cd "$repo_root" && "$OPENPROGRAM_DOCS_PYTHON" -m scripts.docs_site.build)
else
  uv_bin="$(command -v uv || true)"
  test -n "$uv_bin" || {
    printf 'uv is required to stage release documentation\n' >&2
    exit 1
  }
  (
    cd "$repo_root"
    # torch 2.2.2 Intel wheels are cp311/cp312 only; hosted runners default to 3.13+.
    "$uv_bin" run --isolated --locked --python 3.12 \
      --with markdown-it-py --with mdit-py-plugins --with pygments \
      python -m scripts.docs_site.build
  )
fi
test -f "$docs_source_dir/index.html" || {
  printf 'Docs build did not produce %s/index.html\n' "$docs_source_dir" >&2
  exit 1
}

rm -rf "$target_dir"
mkdir -p "$target_dir"
cp -R "$source_dir/." "$target_dir/"
mkdir -p "$docs_target_dir"
cp -R "$docs_source_dir/." "$docs_target_dir/"
if test -n "$node_modules_source"; then
  # A version carries its terminal UI inside the bundle it is staged with.
  mkdir -p "$target_dir/tui"
  cp "$repo_root/apps/cli/dist/index-standalone.cjs" "$target_dir/tui/index.cjs"
fi
python3 - "$target_dir/chat.html" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(f"staged chat.html is missing: {path}")
html = path.read_text(encoding="utf-8")
if 'aria-label="Authenticating"' in html:
    raise SystemExit(f"staged chat.html still ships Authenticating: {path}")
start = html.lower().find("<body")
body = html[start:] if start >= 0 else html
match = re.search(r"<script[\s>]", body, flags=re.I)
paint = body[: match.start()] if match else body
if 'id="sidebar"' not in paint:
    raise SystemExit(f"staged chat.html first-paint lacks id=\"sidebar\": {path}")
PY
printf 'staged release Web and docs assets in %s\n' "$target_dir"
