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
lock_root="$HOME/Library/Caches/OpenProgram"
lock_file="$lock_root/app-package.lock"
lock_owned=0

release_package_lock() {
  if [[ "$lock_owned" == 1 && "$(sed -n '1p' "$lock_file" 2>/dev/null || :)" == "$$" ]]; then
    rm -f "$lock_file" || :
  fi
}

[[ "$(uname -s)" == "Darwin" ]] || {
  printf 'OpenProgram App packaging requires macOS\n' >&2
  exit 1
}
[[ -x "$builder" ]] || {
  printf 'missing electron-builder; run npm install in %s\n' "$desktop_dir" >&2
  exit 1
}

mkdir -p "$desktop_dir/build" "$lock_root"
if ! /usr/bin/shlock -p "$$" -f "$lock_file"; then
  lock_pid="$(sed -n '1p' "$lock_file" 2>/dev/null || :)"
  printf 'another OpenProgram App package is running%s\n' \
    "${lock_pid:+ (pid $lock_pid)}" >&2
  exit 1
fi
lock_owned=1

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-app-package.XXXXXX")"
package_dir="$work_dir/package"

cleanup() {
  local status="$?"
  [[ "$runtime_dir" == "$desktop_dir/build/runtime" ]] || { release_package_lock; exit "$status"; }
  [[ "$python_build_dir" == "$repo_root/build" ]] || { release_package_lock; exit "$status"; }
  [[ "$web_build_dir" == "$repo_root/web/.next" ]] || { release_package_lock; exit "$status"; }
  [[ "$web_output_dir" == "$repo_root/web/out" ]] || { release_package_lock; exit "$status"; }
  [[ "$frontend_stage_dir" == "$repo_root/openprogram/webui/_frontend" ]] || { release_package_lock; exit "$status"; }
  rm -rf "$work_dir" "$runtime_dir" "$python_build_dir" \
    "$web_build_dir" "$web_output_dir" "$frontend_stage_dir" || :
  release_package_lock
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$desktop_dir"
npm run prepare:runtime
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
