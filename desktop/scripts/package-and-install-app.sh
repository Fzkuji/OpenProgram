#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
desktop_dir="$(cd -- "$script_dir/.." && pwd)"
repo_root="$(cd -- "$desktop_dir/.." && pwd)"
builder="$desktop_dir/node_modules/.bin/electron-builder"
runtime_dir="$desktop_dir/build/runtime"
python_build_dir="$repo_root/build"
web_build_dir="$repo_root/web/.next"
web_output_dir="$repo_root/web/out"
frontend_stage_dir="$repo_root/openprogram/webui/_frontend"
lock_dir="$desktop_dir/build/.app-package.lock"

[[ "$(uname -s)" == "Darwin" ]] || {
  printf 'OpenProgram App packaging requires macOS\n' >&2
  exit 1
}
[[ -x "$builder" ]] || {
  printf 'missing electron-builder; run npm install in %s\n' "$desktop_dir" >&2
  exit 1
}

mkdir -p "$desktop_dir/build"
if ! mkdir "$lock_dir" 2>/dev/null; then
  lock_pid="$(sed -n '1p' "$lock_dir/pid" 2>/dev/null || :)"
  if [[ "$lock_pid" =~ ^[0-9]+$ ]] && kill -0 "$lock_pid" 2>/dev/null; then
    printf 'another OpenProgram App package is running (pid %s)\n' "$lock_pid" >&2
    exit 1
  fi
  rm -rf "$lock_dir"
  mkdir "$lock_dir"
fi
printf '%s\n' "$$" >"$lock_dir/pid"

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-app-package.XXXXXX")"
package_dir="$work_dir/package"

cleanup() {
  local status="$?"
  [[ "$runtime_dir" == "$desktop_dir/build/runtime" ]] || exit "$status"
  [[ "$python_build_dir" == "$repo_root/build" ]] || exit "$status"
  [[ "$web_build_dir" == "$repo_root/web/.next" ]] || exit "$status"
  [[ "$web_output_dir" == "$repo_root/web/out" ]] || exit "$status"
  [[ "$frontend_stage_dir" == "$repo_root/openprogram/webui/_frontend" ]] || exit "$status"
  rm -rf "$work_dir" "$runtime_dir" "$python_build_dir" \
    "$web_build_dir" "$web_output_dir" "$frontend_stage_dir" "$lock_dir"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$desktop_dir"
npm run prepare:runtime
npm run icon:build
npm run icon:check
"$builder" --dir --mac --publish never --config.directories.output="$package_dir"

app_list="$work_dir/apps.txt"
find "$package_dir" -type d -name OpenProgram.app -prune -print >"$app_list"
app_count="$(wc -l <"$app_list" | tr -d ' ')"
[[ "$app_count" == 1 ]] || {
  printf 'expected one OpenProgram.app, found %s\n' "$app_count" >&2
  exit 1
}
built_app="$(sed -n '1p' "$app_list")"
bash "$repo_root/scripts/smoke-packaged-runtime.sh" mac "$package_dir"
env -u DESTDIR bash "$script_dir/install-app.sh" "$built_app"
