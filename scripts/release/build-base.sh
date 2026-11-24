#!/usr/bin/env bash
set -euo pipefail

# The one recipe for the dependency layers: Python packages, Playwright and Git.
#
#   build-base.sh --dest DIR [--clone-from BASE]
#     A complete base for a version store. Layers whose pins are unchanged are
#     cloned from BASE; only a changed Python pin runs `uv python install`.
#   build-base.sh --embedded-runtime DIR --python BIN
#     Only the package, browser and Git layers, into a complete runtime.
#     build-product-runtime.sh calls this.

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
dest=""
clone_from=""
embedded_runtime=""
python_bin=""
while test "$#" -gt 0; do
  case "$1" in
    --dest) dest="$2"; shift 2 ;;
    --clone-from) clone_from="$2"; shift 2 ;;
    --embedded-runtime) embedded_runtime="$2"; shift 2 ;;
    --python) python_bin="$2"; shift 2 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

uv_bin="${OPENPROGRAM_UV_BIN:-$(command -v uv || true)}"
if test -z "$uv_bin" || ! test -x "$uv_bin"; then
  printf 'missing build command: uv\n' >&2
  exit 1
fi

product_config="$repo_root/scripts/release/product-runtime.json"
read_pin() { sed -n "s/^  \"$1\": \"\(.*\)\",*$/\1/p" "$product_config" | head -n 1; }

clone_tree() {
  if test "$(uname -s)" = Darwin; then
    cp -c -R -p "$1" "$2" 2>/dev/null || { rm -rf "$2"; cp -R -p "$1" "$2"; }
  else
    cp --reflink=auto -R -p "$1" "$2"
  fi
}

# Writes the locked requirements, then installs them. `sync` also removes what
# a cloned Python layer brought along and the new lock no longer lists.
# Wheels are picked for the oldest system the release supports, never for this
# runner, and only as wheels: runtime-wheels.py names the few packages a leg
# builds from source against a static library it prepared (OPENSSL_DIR).
install_packages() {
  local verb="$1" python="$2" requirements="$3" target_args=()
  "$uv_bin" export --project "$repo_root" --frozen --no-dev \
    --extra all --extra search --no-emit-project \
    --output-file "$requirements" >/dev/null
  while IFS= read -r argument; do target_args+=("$argument"); done < <(
    "$python" -I "$repo_root/scripts/release/runtime-wheels.py" uv-args)
  test "${#target_args[@]}" -gt 0 || { printf 'no wheel target for this machine\n' >&2; exit 1; }
  # `uv pip sync` takes the file as an argument and refuses --requirements;
  # `uv pip install` needs --requirements, since its arguments are packages.
  local source=(--requirements "$requirements")
  test "$verb" = sync && source=("$requirements")
  MACOSX_DEPLOYMENT_TARGET="$(read_pin macosMinimum)" \
    "$uv_bin" pip "$verb" --python "$python" --strict --break-system-packages \
      --require-hashes "${target_args[@]}" "${source[@]}"
}

# Seal each browser bundle once here. Packaging leaves this tree untouched
# (signIgnore), so every later build ships these exact bytes.
install_browsers() {
  local python="$1" browsers="$2" shell=()
  # Every launch asks for the full browser first, and on macOS it always
  # starts, so the headless shell (190 MB) would ship unused. Linux keeps it:
  # it needs fewer system libraries and is what starts on a bare server.
  test "$(uname -s)" != Darwin || shell=(--no-shell)
  PLAYWRIGHT_BROWSERS_PATH="$browsers" "$python" -m playwright install chromium ${shell[@]+"${shell[@]}"}
  if test "$(uname -s)" = Darwin; then
    find "$browsers" -type d -name '*.app' -prune -print0 |
      while IFS= read -r -d '' browser_app; do
        codesign --force --deep --sign - --timestamp=none "$browser_app"
        codesign --verify --deep --strict "$browser_app"
      done
  fi
}

# A pinned Git, so a new machine needs none installed and nothing reaches the
# macOS /usr/bin/git shim and its Command Line Tools dialog. The Linux builds
# need glibc 2.34 (Ubuntu 22.04 has 2.35, Debian 12 has 2.36) and libz.so.1;
# only Git over HTTP(S) also needs the system's libcurl.so.4. It is the Git for
# the Python it ships beside, which an emulated build (an x86_64 shell under
# Rosetta) does not share with this machine.
install_git() {
  local target="$1" python="$2" packaged git_pin git_platform git_asset git_sha git_archive actual_sha
  packaged="$("$python" -I "$repo_root/scripts/release/runtime-wheels.py" target)"
  case "$packaged" in
    macos-arm64) git_platform=macOS-arm64 ;;
    macos-x86_64) git_platform=macOS-x64 ;;
    linux-x86_64) git_platform=ubuntu-x64 ;;
    linux-arm64) git_platform=ubuntu-arm64 ;;
    *)
      printf 'no pinned Git is available for %s\n' "$packaged" >&2
      exit 1
      ;;
  esac
  git_pin="$(read_pin git)"
  git_asset="$(read_pin gitAsset)-$git_platform.tar.gz"
  git_sha="$(sed -n "s/^    \"$git_platform\": \"\(.*\)\",*$/\1/p" "$product_config")"
  test -n "$git_sha" || { printf 'no checksum is pinned for %s\n' "$git_asset" >&2; exit 1; }
  git_archive="$(dirname -- "$target")/.git-archive.tar.gz"
  curl --fail --silent --show-error --location --output "$git_archive" \
    "https://github.com/desktop/dugite-native/releases/download/$git_pin/$git_asset"
  if command -v sha256sum >/dev/null 2>&1; then
    actual_sha="$(sha256sum "$git_archive" | awk '{print $1}')"
  else
    actual_sha="$(shasum -a 256 "$git_archive" | awk '{print $1}')"
  fi
  test "$actual_sha" = "$git_sha" || {
    printf 'Git archive checksum mismatch: %s\n' "$git_asset" >&2
    exit 1
  }
  mkdir -p "$target"
  tar -xzf "$git_archive" -C "$target"
  rm -f "$git_archive"
}

if test -n "$embedded_runtime"; then
  test -n "$python_bin" || { printf -- '--embedded-runtime needs --python\n' >&2; exit 2; }
  install_packages install "$python_bin" "$embedded_runtime/product-requirements.txt"
  install_browsers "$python_bin" "$embedded_runtime/assets/playwright"
  install_git "$embedded_runtime/git" "$python_bin"
  exit 0
fi

test -n "$dest" || { printf -- 'give --dest or --embedded-runtime\n' >&2; exit 2; }
test ! -e "$dest" || { printf 'base staging already exists: %s\n' "$dest" >&2; exit 1; }
pin_of() { # base.json field of the base being cloned from
  test -n "$clone_from" && test -f "$clone_from/base.json" || return 0
  sed -n "s/^  \"$1\": \"\(.*\)\",*$/\1/p" "$clone_from/base.json" | head -n 1
}
python_pin="$(read_pin python)"
uv_pin="$(read_pin uv)"
playwright_pin="$(awk '/^name = "playwright"$/ { getline; gsub(/version = |"/, ""); print; exit }' "$repo_root/uv.lock")"
actual_uv="$("$uv_bin" --version | awk '{print $2}')"
test "$actual_uv" = "$uv_pin" || {
  printf 'uv version mismatch: expected %s, got %s\n' "$uv_pin" "$actual_uv" >&2
  exit 1
}
# The base carries node as one file, so it must run with nothing beside it. A
# node linked against a shared libnode (Homebrew's) does not, and is passed over.
runs_alone() {
  local probe status=0
  probe="$(mktemp -d)"
  cp "$1" "$probe/node" && "$probe/node" --version >/dev/null 2>&1 || status=1
  rm -rf "$probe"
  return "$status"
}
node_bin=""
found_node=""
while IFS= read -r candidate; do
  found_node="${found_node:-$candidate}"
  if runs_alone "$candidate"; then node_bin="$candidate"; break; fi
done < <(type -ap node || true)
test -n "$found_node" || { printf 'missing build command: node\n' >&2; exit 1; }
test -n "$node_bin" || {
  printf 'no node on PATH runs on its own (first found: %s); install an official Node build\n' "$found_node" >&2
  exit 1
}
# npm from the same installation as that node, run by it.
node_dir="$(dirname "$node_bin")"
npm_run() { PATH="$node_dir:$PATH" npm "$@"; }
node_pin="$("$node_bin" --version)"
npm_pin="$(npm_run --version)"

mkdir -p "$dest/assets" "$dest/bin"

if test -n "$(pin_of python)" && test "$(pin_of python)" = "$python_pin"; then
  clone_tree "$clone_from/python" "$dest/python"
  python_verb=sync
else
  install_dir="$dest/.python-install"
  UV_PYTHON_INSTALL_DIR="$install_dir" "$uv_bin" python install "$python_pin" \
    --install-dir "$install_dir" --no-bin
  found="$(UV_PYTHON_INSTALL_DIR="$install_dir" "$uv_bin" python find --managed-python "$python_pin")"
  python_home="$(cd -P -- "$(dirname -- "$found")/.." && pwd)"
  mv "$python_home" "$dest/python"
  rm -rf "$install_dir"
  python_verb=install
fi
base_python="$dest/python/bin/python3"
test -x "$base_python" || { printf 'base Python is missing: %s\n' "$base_python" >&2; exit 1; }
ln -s ../python/bin/python3 "$dest/bin/python"
ln -s ../python/bin/python3 "$dest/bin/python3"

install_packages "$python_verb" "$base_python" "$dest/product-requirements.txt"

# The docs build needs one package the product does not. It is taken from the
# same lock, with its hashes, and installed without dependencies.
docs_all="$dest/.docs-all.txt"
docs_only="$dest/docs-requirements.txt"
"$uv_bin" export --project "$repo_root" --frozen --no-dev --extra dev --no-emit-project \
  --output-file "$docs_all" >/dev/null
awk '/^mdit-py-plugins==/ { keep = 1; print; next }
     keep && /^[[:space:]]+--hash/ { print; next }
     { keep = 0 }' "$docs_all" > "$docs_only"
rm -f "$docs_all"
"$uv_bin" pip install --python "$base_python" --break-system-packages --no-deps \
  --require-hashes --requirements "$docs_only"

if test -n "$(pin_of playwright)" && test "$(pin_of playwright)" = "$playwright_pin"; then
  clone_tree "$clone_from/assets/playwright" "$dest/assets/playwright"
else
  install_browsers "$base_python" "$dest/assets/playwright"
fi

if test -n "$(pin_of node)" && test "$(pin_of node)" = "$node_pin"; then
  clone_tree "$clone_from/bin/node" "$dest/bin/node"
else
  cp "$node_bin" "$dest/bin/node"
fi
mkdir -p "$dest/lib/node_modules"
if test -n "$(pin_of npm)" && test "$(pin_of npm)" = "$npm_pin"; then
  clone_tree "$clone_from/lib/node_modules/npm" "$dest/lib/node_modules/npm"
else
  cp -R "$(npm_run root -g)/npm" "$dest/lib/node_modules/npm"
fi
ln -s ../lib/node_modules/npm/bin/npm-cli.js "$dest/bin/npm"
if test -n "$(pin_of uv)" && test "$(pin_of uv)" = "$uv_pin"; then
  clone_tree "$clone_from/bin/uv" "$dest/bin/uv"
else
  cp "$uv_bin" "$dest/bin/uv"
fi
git_pin="$(read_pin git)"
# A base built before Linux bundled Git records the pin but has no Git to clone.
if test -n "$(pin_of git)" && test "$(pin_of git)" = "$git_pin" && test -d "$clone_from/git"; then
  clone_tree "$clone_from/git" "$dest/git"
else
  install_git "$dest/git" "$base_python"
fi

cp "$product_config" "$dest/product-runtime.json"
cp "$repo_root/uv.lock" "$dest/product-uv.lock"
cp "$repo_root/scripts/release/verify-product-runtime.py" "$dest/bin/verify-product-runtime.py"
cp "$repo_root/scripts/release/smoke-ink-tui-pty.py" "$dest/bin/smoke-ink-tui-pty.py"
chmod 0755 "$dest/bin/node" "$dest/bin/uv" "$dest/bin/verify-product-runtime.py" "$dest/bin/smoke-ink-tui-pty.py"
# One Node in the base: Playwright's driver runs on bin/node.
"$base_python" -I "$repo_root/scripts/release/share-runtime-node.py" "$dest"

cat > "$dest/base.json" <<JSON
{
  "schema": "1",
  "python": "$python_pin",
  "uv": "$uv_pin",
  "playwright": "$playwright_pin",
  "node": "$node_pin",
  "npm": "$npm_pin",
  "git": "$git_pin"
}
JSON
printf 'prepared base at %s\n' "$dest"
