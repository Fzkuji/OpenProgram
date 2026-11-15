#!/usr/bin/env bash
set -euo pipefail

# Build this checkout into the development instance: a runtime under
# ~/.openprogram-dev/runtime, served by the `dev` profile's worker on port
# 18200, and on macOS the "OpenProgram Dev" App over it
# (scripts/install-dev-app.sh). The stable App, its runtime and its worker are never
# touched, so this is safe to run from a conversation in the stable App.
# See openprogram/operations/worker/dev_instance.py for how the two instances relate.

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
state_dir="$HOME/.openprogram-dev"
runtime="$state_dir/runtime"
port=18200
uv_bin="${OPENPROGRAM_UV_BIN:-$(command -v uv || true)}"

stamp() {
  printf '[dev refresh %s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

test -n "$uv_bin" && test -x "$uv_bin" || {
  printf 'uv is required to refresh the development instance\n' >&2
  exit 1
}
if test -n "$(git -C "$repo_root" status --porcelain --untracked-files=all)"; then
  printf 'refusing to deploy uncommitted source changes; commit them first\n' >&2
  exit 1
fi
revision="$(git -C "$repo_root" rev-parse HEAD)"
# Deploys run while the stable App is in use; the build yields the CPU to it.
renice -n 10 -p $$ >/dev/null 2>&1 || true

# One deploy at a time; a second one waits instead of interleaving installs.
mkdir -p "$state_dir"
lock_dir="$state_dir/.refresh.lock"
until mkdir "$lock_dir" 2>/dev/null; do
  if ! kill -0 "$(cat "$lock_dir/pid" 2>/dev/null || echo 0)" 2>/dev/null; then
    rm -rf "$lock_dir"
    continue
  fi
  sleep 1
done
printf '%s\n' "$$" > "$lock_dir/pid"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-dev-wheel.XXXXXX")"
trap 'rm -rf "$work_dir" "$lock_dir"' EXIT

# Remember the source checkout (not this worktree) for `openprogram dev`.
common_dir="$(cd "$repo_root" && cd "$(git rev-parse --git-common-dir)" && pwd)"
mkdir -p "$HOME/.openprogram"
printf '%s\n' "$(dirname "$common_dir")" > "$HOME/.openprogram/source-repo"

# The commits in a deploy come from every session, so docs problems are
# reported and never stop it; a page that fails the format builds as a visible
# error page. Git hooks are unversioned, so every deploy syncs them.
"$repo_root/scripts/git-hooks/install.sh" || stamp "could not sync the git hooks"
docs_python="$(dirname "$common_dir")/.venv/bin/python"
last_revision="$(cat "$runtime/source-revision" 2>/dev/null || true)"
if test -x "$docs_python" && test -n "$last_revision" && test "$last_revision" != "$revision" \
  && git -C "$repo_root" cat-file -e "$last_revision^{commit}" 2>/dev/null; then
  (cd "$repo_root" && "$docs_python" -B -m scripts.docs_site.check --base "$last_revision" --report-only) \
    || stamp "the docs check could not run; continuing"
else
  stamp "docs check skipped: no venv or no earlier deploy to compare with"
fi
export OPENPROGRAM_DOCS_FORMAT=quarantine

stamp "building Web assets and wheel from ${revision:0:10}"
rm -rf "$repo_root/build"
"$repo_root/scripts/release/stage-release-assets.sh"
"$uv_bin" build --wheel --out-dir "$work_dir" "$repo_root"
wheel="$(find "$work_dir" -maxdepth 1 -type f -name 'openprogram-*.whl' -print -quit)"
test -n "$wheel" || { printf 'OpenProgram wheel was not built\n' >&2; exit 1; }

stamp "wheel built"

# A full CPython inside the runtime, like the App's: the macOS helper below
# embeds its libpython, which a virtualenv does not carry. An older venv
# layout is replaced.
if test -f "$runtime/pyvenv.cfg"; then
  OPENPROGRAM_PROFILE=dev "$runtime/bin/python" -I -B -m openprogram worker stop >/dev/null 2>&1 || true
  rm -rf "$runtime"
fi
py="$(find "$runtime/python" -path '*/bin/python3.12' -type f 2>/dev/null | head -1 || true)"
if test -z "$py"; then
  "$uv_bin" python install 3.12 --install-dir "$runtime/python" --no-bin >/dev/null
  py="$(find "$runtime/python" -path '*/bin/python3.12' -type f | head -1)"
fi
test -x "$py" || { printf 'the development runtime has no Python\n' >&2; exit 1; }

# The same locked dependency set a release installs.
requirements="$work_dir/requirements.txt"
"$uv_bin" export --project "$repo_root" --frozen --no-dev \
  --extra all --extra search --no-emit-project --output-file "$requirements" >/dev/null
"$uv_bin" pip install --python "$py" --break-system-packages --require-hashes \
  --requirements "$requirements"
"$uv_bin" pip install --python "$py" --break-system-packages --no-deps --reinstall \
  "$wheel"
# Programs this runtime no longer ships; an earlier refresh may have installed them.
"$uv_bin" pip uninstall --python "$py" --break-system-packages \
  gui-agent-harness research-agent-harness >/dev/null 2>&1 || true
stamp "runtime installed"

# The worker runs as its own named macOS application ("OpenProgram Dev"), so
# Screen Recording, Accessibility and other permissions are granted to it
# separately from the stable App's runtime. The manifest makes
# `openprogram worker` launch through that helper.
helper_name="OpenProgram Dev"
manifest_python="${py#"$runtime/"}"
worker_python="$manifest_python"
if test "$(uname -s)" = Darwin; then
  "$py" -I "$repo_root/scripts/release/build-macos-runtime-app.py" "$runtime" \
    --python "$py" --icon "$repo_root/apps/desktop/build/icon.icns" \
    --name "$helper_name" --identifier ai.openprogram.runtime.dev >/dev/null
  worker_python="$helper_name.app/Contents/MacOS/$helper_name"
fi
"$py" -I - "$runtime/runtime-manifest.json" "$manifest_python" "$worker_python" <<'PYTHON'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump({"python": sys.argv[2], "worker_python": sys.argv[3]}, stream, indent=2)
PYTHON
mkdir -p "$runtime/bin"
ln -sf "../$manifest_python" "$runtime/bin/python"

dev() {
  OPENPROGRAM_PROFILE=dev "$py" -I -B -m openprogram "$@"
}
dev ports --port "$port" >/dev/null
OPENPROGRAM_PROFILE=dev "$py" -I -B -c \
  'from openprogram.state.program_directory import migrate_user_directory; migrate_user_directory()'

# A login service, so the development instance comes back after a reboot or
# a crash; `worker install` also restarts it on the new code.
stamp "restarting the dev worker"
if test "$(uname -s)" = Darwin || test "$(uname -s)" = Linux; then
  dev worker install
else
  dev worker stop >/dev/null 2>&1 || true
  dev worker start
fi
deadline=$((SECONDS + 300))
until curl -fsS --connect-timeout 1 --max-time 2 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; do
  if test "$SECONDS" -ge "$deadline"; then
    printf 'dev worker did not become healthy on port %s; logs: %s/worker.log\n' "$port" "$state_dir" >&2
    exit 1
  fi
  sleep 0.5
done

printf '%s\n' "$revision" > "$runtime/source-revision"

# Keep this build for `openprogram dev promote`: the stable App installs the
# same commit and reuses the wheel and TUI bundle instead of rebuilding them.
artifacts="$state_dir/artifacts"
rm -rf "$artifacts.incoming"
mkdir -p "$artifacts.incoming"
cp "$wheel" "$repo_root/apps/cli/dist/index-standalone.cjs" "$artifacts.incoming/"
printf '%s\n' "$revision" > "$artifacts.incoming/revision"
rm -rf "$artifacts"
mv "$artifacts.incoming" "$artifacts"
# The development App ("OpenProgram Dev") is this commit's Desktop shell over
# the runtime above.
if test "$(uname -s)" = Darwin; then
  "$repo_root/scripts/install-dev-app.sh"
fi
printf 'refreshed the development instance (http://127.0.0.1:%s) from %s\n' "$port" "$revision"
