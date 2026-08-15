#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
web_dir="$repo_root/web"
source_dir="$web_dir/out"
next_build_dir="$web_dir/.next"
target_dir="$repo_root/openprogram/webui/_frontend"

command -v npm >/dev/null 2>&1 || {
  printf 'npm is required to stage release Web assets\n' >&2
  exit 1
}

npm ci --prefix "$web_dir"
rm -rf "$source_dir" "$next_build_dir"
npm run build --prefix "$web_dir"
test -f "$source_dir/index.html" || {
  printf 'Next.js export did not produce %s/index.html\n' "$source_dir" >&2
  exit 1
}

rm -rf "$target_dir"
mkdir -p "$target_dir"
cp -R "$source_dir/." "$target_dir/"
printf 'staged release Web assets in %s\n' "$target_dir"
