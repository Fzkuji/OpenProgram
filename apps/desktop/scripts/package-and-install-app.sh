#!/usr/bin/env bash
set -euo pipefail

output_app=""
if [[ "${1:-}" == "--output" ]]; then
  output_app="${2:-}"
  [[ $# == 2 && "$output_app" == /* && "$(basename -- "$output_app")" == "OpenProgram.app" ]] || {
    printf 'usage: %s [--output /absolute/path/OpenProgram.app]\n' "$0" >&2
    exit 2
  }
elif [[ $# != 0 ]]; then
  printf 'usage: %s [--output /absolute/path/OpenProgram.app]\n' "$0" >&2
  exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
desktop_dir="$(cd -- "$script_dir/.." && pwd)"
repo_root="$(cd -- "$desktop_dir/../.." && pwd)"
runtime_dir="$desktop_dir/build/runtime"
python_build_dir="$repo_root/build"
web_build_dir="$repo_root/apps/web/.next"
web_output_dir="$repo_root/apps/web/out"
frontend_stage_dir="$repo_root/apps/server/openprogram_server/_webui/_frontend"
lock_root="$HOME/Library/Caches/OpenProgram"
lock_file="$lock_root/app-package.lock"
lock_owned=0

acquire_pid_lock() {
  local path="$1"
  if [[ -x /usr/bin/shlock ]]; then
    /usr/bin/shlock -p "$$" -f "$path"
  else
    (set -o noclobber; printf '%s\n' "$$" > "$path") 2>/dev/null
  fi
}

release_package_lock() {
  if [[ "$lock_owned" == 1 && "$(sed -n '1p' "$lock_file" 2>/dev/null || :)" == "$$" ]]; then
    rm -f "$lock_file" || :
  fi
}

[[ "$(uname -s)" == "Darwin" ]] || {
  printf 'OpenProgram App packaging requires macOS\n' >&2
  exit 1
}
mkdir -p "$desktop_dir/build" "$lock_root"
if ! acquire_pid_lock "$lock_file"; then
  lock_pid="$(sed -n '1p' "$lock_file" 2>/dev/null || :)"
  printf 'another OpenProgram App package is running%s\n' \
    "${lock_pid:+ (pid $lock_pid)}" >&2
  exit 1
fi
lock_owned=1
trap release_package_lock EXIT

command -v npm >/dev/null 2>&1 || {
  printf 'missing npm; install Node.js and run npm install in %s\n' "$repo_root" >&2
  exit 1
}

if [[ -n "$output_app" ]]; then
  output_app="$(node - "$output_app" <<'NODE'
const fs = require("fs");
const path = require("path");
let current = path.resolve(process.argv[2]);
const missing = [];
while (!fs.existsSync(current)) {
  missing.unshift(path.basename(current));
  current = path.dirname(current);
}
process.stdout.write(path.join(fs.realpathSync(current), ...missing));
NODE
)"
  case "$output_app" in
    "/Applications/OpenProgram.app"|\
    "$runtime_dir"|"$runtime_dir"/*|\
    "$python_build_dir"|"$python_build_dir"/*|\
    "$web_build_dir"|"$web_build_dir"/*|\
    "$web_output_dir"|"$web_output_dir"/*|\
    "$frontend_stage_dir"|"$frontend_stage_dir"/*)
      printf 'build output is managed by installation or package cleanup: %s\n' \
        "$output_app" >&2
      exit 2
      ;;
  esac
fi

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-app-package.XXXXXX")"
package_dir="$work_dir/package"

cleanup() {
  local status="$?"
  [[ "$runtime_dir" == "$desktop_dir/build/runtime" ]] || { release_package_lock; exit "$status"; }
  [[ "$python_build_dir" == "$repo_root/build" ]] || { release_package_lock; exit "$status"; }
  [[ "$web_build_dir" == "$repo_root/apps/web/.next" ]] || { release_package_lock; exit "$status"; }
  [[ "$web_output_dir" == "$repo_root/apps/web/out" ]] || { release_package_lock; exit "$status"; }
  [[ "$frontend_stage_dir" == "$repo_root/apps/server/openprogram_server/_webui/_frontend" ]] || { release_package_lock; exit "$status"; }
  rm -rf "$work_dir" "$runtime_dir" "$python_build_dir" \
    "$web_build_dir" "$web_output_dir" "$frontend_stage_dir" || :
  release_package_lock
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$repo_root"
npm run prepare:runtime --workspace apps/desktop
npm run icon:check --workspace apps/desktop
npm exec --workspace apps/desktop -- electron-builder \
  --dir --mac --publish never --config.directories.output="$package_dir"

app_list="$work_dir/apps.txt"
find "$package_dir" -type d -name OpenProgram.app -prune -print >"$app_list"
app_count="$(wc -l <"$app_list" | tr -d ' ')"
[[ "$app_count" == 1 ]] || {
  printf 'expected one OpenProgram.app, found %s\n' "$app_count" >&2
  exit 1
}
built_app="$(sed -n '1p' "$app_list")"
bash "$repo_root/scripts/release/smoke-packaged-runtime.sh" mac "$package_dir"
if [[ -n "$output_app" ]]; then
  [[ ! -e "$output_app" && ! -L "$output_app" ]] || {
    printf 'build output already exists: %s\n' "$output_app" >&2
    exit 1
  }
  mkdir -p "$(dirname -- "$output_app")"
  if ! ditto "$built_app" "$output_app"; then
    rm -rf "$output_app" || :
    printf 'failed to copy OpenProgram App artifact to %s\n' "$output_app" >&2
    exit 1
  fi
  printf 'OpenProgram App artifact written to %s\n' "$output_app"
else
  env -u DESTDIR bash "$script_dir/install-app.sh" "$built_app"
fi
