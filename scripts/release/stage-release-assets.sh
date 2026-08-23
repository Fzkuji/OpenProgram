#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
web_dir="$repo_root/apps/web"
source_dir="$web_dir/out"
next_build_dir="$web_dir/.next"
target_dir="$repo_root/apps/server/openprogram_server/_webui/_frontend"
legacy_target_dir="$repo_root/openprogram/webui/_frontend"
docs_source_dir="$repo_root/docs/_site"
docs_target_dir="$target_dir/docs"

command -v npm >/dev/null 2>&1 || {
  printf 'npm is required to stage release Web assets\n' >&2
  exit 1
}

(
  cd "$repo_root"
  unset npm_config_workspace npm_config_workspaces
  npm ci --ignore-scripts
  rm -rf "$source_dir" "$next_build_dir"
  NEXT_IGNORE_INCORRECT_LOCKFILE=1 npm run build --workspace apps/web
)
test -f "$source_dir/index.html" || {
  printf 'Next.js export did not produce %s/index.html\n' "$source_dir" >&2
  exit 1
}

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
test -f "$docs_source_dir/index.html" || {
  printf 'Docs build did not produce %s/index.html\n' "$docs_source_dir" >&2
  exit 1
}

rm -rf "$target_dir" "$legacy_target_dir"
mkdir -p "$target_dir"
cp -R "$source_dir/." "$target_dir/"
mkdir -p "$docs_target_dir"
cp -R "$docs_source_dir/." "$docs_target_dir/"
printf 'staged release Web and docs assets in %s\n' "$target_dir"
