#!/usr/bin/env bash
set -euo pipefail

# Build everything a release's seed holds from this checkout: a base, one
# sealed version, the launcher, then the seed itself twice, once for the App
# (.tar.zst) and once for the command-line installers (.tar.gz).
#
#   build-release-seed.sh OUTPUT_DIR
#     → OUTPUT_DIR/seed/            copied into the App as Resources/seed
#     → OUTPUT_DIR/cli/seed/        packed by the release as the -seed- asset
#
# OPENPROGRAM_RELEASE_SIGN_IDENTITY signs the base's macOS binaries.
# Needs Git history, so a CI checkout must set fetch-depth 0, plus uv, zstd and
# a C compiler. Runs under Git Bash on Windows.

# Git Bash paths mean nothing to the native programs below.
native_directory() { (cd "$1" && { pwd -W 2>/dev/null || pwd; }); }

output="$(mkdir -p "$1" && native_directory "$1")"
repo_root="$(native_directory "$(dirname -- "${BASH_SOURCE[0]}")/../..")"
# A build moves every node_modules it finds under the checkout into the store.
case "$output/" in
  "$repo_root"/*)
    printf 'Choose an output directory outside this checkout: %s is inside %s.\n' "$output" "$repo_root" >&2
    exit 1
    ;;
esac
python="${OPENPROGRAM_LOCAL_PYTHON:-}"
if test -z "$python"; then
  # Windows' python3 can be a Store stub that runs nothing.
  for candidate in python3 python; do
    if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
      python="$candidate"
      break
    fi
  done
fi
test -n "$python" || {
  printf 'Python 3.11 or newer is required to build a seed; install it, or name it in OPENPROGRAM_LOCAL_PYTHON.\n' >&2
  exit 1
}
command -v zstd >/dev/null 2>&1 || {
  printf 'zstd is required to build a seed; install it and run this again.\n' >&2
  exit 1
}
store="$output/store/runtime"
mkdir -p "$store"

version="$(
  PYTHONPATH="$repo_root" "$python" - "$store" "$repo_root" <<'PY' | tr -d '\r'
import subprocess
import sys
from pathlib import Path

from openprogram.operations.self_update.versions.build import BuildRequest, build_version
from openprogram.operations.self_update.versions.layout import Layout

store, repository = map(Path, sys.argv[1:])
commit = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
print(build_version(Layout(store), BuildRequest(
    update_id="release", commit=commit, worktree=repository, built_by="release",
)).version)
PY
)"
base="$("$python" -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["base"])' "$store/versions/$version/version.json" | tr -d '\r')"
base_python="$store/bases/$base/python/bin/python3"
test -x "$base_python" || base_python="$store/bases/$base/python/python.exe"
PYTHONPATH="$repo_root" "$python" "$repo_root/scripts/release/build-launcher.py" \
  --python "$base_python" --output "$output/launcher" \
  --icon "$repo_root/apps/desktop/build/icon.icns"
case "$(uname -s)" in
  Darwin)
    launcher=("--launcher" "$output/launcher/OpenProgram.app/Contents/MacOS/OpenProgram"
              "--helper" "$output/launcher/OpenProgram.app")
    ;;
  MINGW*|MSYS*|CYGWIN*) launcher=("--launcher" "$output/launcher/openprogram-launcher.exe") ;;
  *) launcher=("--launcher" "$output/launcher/openprogram-launcher") ;;
esac
signing=()
if test -n "${OPENPROGRAM_RELEASE_SIGN_IDENTITY:-}"; then
  signing=("--sign-identity" "$OPENPROGRAM_RELEASE_SIGN_IDENTITY")
fi
"$python" "$repo_root/scripts/release/build-seed.py" --root "$store" --version "$version" \
  --repository "$repo_root" "${launcher[@]}" "${signing[@]+"${signing[@]}"}" \
  --output "$output/seed" --compression zst
"$python" "$repo_root/scripts/release/build-seed.py" --root "$store" --version "$version" \
  --repository "$repo_root" "${launcher[@]}" \
  --output "$output/cli/seed" --compression gz
