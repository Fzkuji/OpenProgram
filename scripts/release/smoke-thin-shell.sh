#!/usr/bin/env bash
# The first install a thin release performs, end to end, in a temporary HOME:
# build a seed from a small sealed version in one directory, unpack it with the
# shell's own code in another, and check the result the way a later start reads
# it. Then the same through the command-line installer. Starts no App.
#
#   smoke-thin-shell.sh
#
# Needs cc, zstd, git and Node with zstd support. Set PYTHON to choose Python.
set -euo pipefail

repository="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
python="${PYTHON:-python3}"
temp_root="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-thin-smoke.XXXXXX")"
temp_root="$(cd "$temp_root" && pwd -P)"
trap 'rm -rf "$temp_root"' 0

fail() { printf 'smoke-thin-shell: %s\n' "$1" >&2; exit 1; }
for tool in cc zstd git node; do
  command -v "$tool" >/dev/null 2>&1 || fail "$tool is required"
done

build_root="$temp_root/build/state/runtime"
source_repository="$temp_root/source"
export HOME="$temp_root/home"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
unset OPENPROGRAM_PROFILE OPENPROGRAM_STATE_DIR
mkdir -p "$HOME" "$source_repository"

git -C "$source_repository" init --quiet
printf 'seeded\n' > "$source_repository/README"
git -C "$source_repository" add README
git -C "$source_repository" -c user.name=smoke -c user.email=smoke@example.invalid commit --quiet -m 'First'
commit="$(git -C "$source_repository" rev-parse HEAD)"

launcher="$temp_root/openprogram-launcher"
cc -O1 -DOPENPROGRAM_LAUNCHER_EXEC "$repository/scripts/release/runtime-launcher.c" -o "$launcher"

# A base whose Python is a wrapper, and a version that names its build root the
# way a real venv does, so the seed has something to relocate.
version="$(PYTHONPATH="$repository" "$python" - "$build_root" "$commit" <<'PY'
import os
from pathlib import Path
import shlex
import sys

from openprogram.operations.self_update.versions import layout as paths, store
from openprogram.operations.self_update.versions.layout import Layout

root, commit = Path(sys.argv[1]), sys.argv[2]
base = "py3.12-0123456789abcdef"
version = "20261108T000000Z-" + commit[:12]
layout = Layout(root)
python = paths.base_python(layout.base(base))
python.parent.mkdir(parents=True)
python.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n")
python.chmod(0o755)

staging = layout.versions / f".staging-{version}"
site = paths.site_packages(staging, base)
(site / "openprogram").mkdir(parents=True)
(site / "openprogram" / "__init__.py").write_text("")
bundle = paths.frontend_bundle(site)
bundle.mkdir(parents=True)
(bundle / "index.html").write_text("<html></html>\n")
(staging / "desktop").mkdir()
(staging / "desktop" / "main.js").write_text("exports.start = () => Promise.resolve();\n")
binary = staging / "venv" / "bin"
binary.mkdir(parents=True)
(binary / "python").write_text(f'#!/bin/sh\necho "ran {version}"\n')
(binary / "python").chmod(0o755)
(binary / "python3.12").symlink_to(python)
(staging / "venv" / "pyvenv.cfg").write_text(f"home = {python.parent}\n")
store.seal(layout, staging, {
    "id": version, "commit": commit, "base": base, "shell_compat": 1, "built_by": "smoke",
    "built_at": "2026-11-08T00:00:00Z", "components": ["python"], "parent": None, "summary": "Smoke",
})
print(version)
PY
)"

pack() {
  "$python" "$repository/scripts/release/build-seed.py" --root "$build_root" --version "$version" \
    --repository "$source_repository" --launcher "$launcher" --output "$1" --compression "$2" >/dev/null
}

check_install() {
  local root="$1" label="$2"
  test -f "$root/current" || fail "$label: no pointer"
  test ! -e "$root/.seeding" || fail "$label: .seeding left behind"
  test ! -e "$root/.seed-work" || fail "$label: work directory left behind"
  test -z "$(find "$root/versions" -name '._*' -print -quit)" || fail "$label: macOS metadata files were packed"
  grep -qx "version=$version" "$root/current" || fail "$label: pointer names the wrong version"
  grep -qx "last_good=$version" "$root/current" || fail "$label: no last good version"
  grep -qx 'writer=installer' "$root/current" || fail "$label: wrong pointer writer"
  ran="$("$root/identity/openprogram-launcher" --root "$root" -u -m openprogram worker run)"
  test "$ran" = "ran $version" || fail "$label: the launcher did not start the version: $ran"
  test ! -e "$root/unapplied" || fail "$label: the launcher found the seed differs from its seal"
  test "$(wc -l < "$root/boot/$version.attempts" | tr -d ' ')" = 1 || fail "$label: the start was not counted once"
  test "$(git -C "$root/repo" rev-parse local)" = "$commit" || fail "$label: local is wrong"
  test "$(git -C "$root/repo" rev-parse release)" = "$commit" || fail "$label: release is wrong"
  test "$(git -C "$root/repo" rev-parse "refs/openprogram/version/$version")" = "$commit" || fail "$label: version ref is wrong"
  "$root/bin/openprogram" versions list | grep -q "$version" || fail "$label: versions list does not show it"
  PYTHONPATH="$repository" "$python" - "$root" "$version" "$build_root" <<'PY' || fail "$label: the seal or relocation is wrong"
import os
from pathlib import Path
import sys
import zipfile

from openprogram.operations.self_update.versions import store

root, version, built_root = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
directory = root / "versions" / version
assert store.changed_files(directory, "py3.12-0123456789abcdef") == [], "a sealed file changed"
configuration = (directory / "venv/pyvenv.cfg").read_text()
assert built_root not in configuration and str(root) in configuration, configuration
link = os.readlink(directory / "venv/bin/python3.12")
assert link.startswith(f"{root}/"), link
assert (directory / "venv/bin/python3.12").exists(), "the link is broken"
with zipfile.ZipFile(directory / "pristine.zip") as archive:
    assert built_root not in archive.read("venv/pyvenv.cfg").decode()
    assert archive.read("venv/bin/python3.12").decode() == link
PY
}

# --- what the App's shell does ------------------------------------------------
pack "$temp_root/seed" zst
shell_root="$HOME/.openprogram/runtime"
SEED_ROOT="$shell_root" SEED_DIRECTORY="$temp_root/seed" SHELL_SEED="$repository/apps/desktop/shell/seed.js" node -e '
  const { seed } = require(process.env.SHELL_SEED);
  let last = -1;
  seed({
    root: process.env.SEED_ROOT, seedDirectory: process.env.SEED_DIRECTORY,
    onProgress: (value) => { if (value < last) throw new Error("progress went back"); last = value; },
  }).then(() => { if (last !== 100) throw new Error(`progress stopped at ${last}`); })
    .catch((error) => { console.error(error); process.exit(1); });
'
check_install "$shell_root" 'shell seed'

# --- what the command-line installer does -------------------------------------
pack "$temp_root/packed/seed" gz
COPYFILE_DISABLE=1 tar -C "$temp_root/packed" -czf "$temp_root/seed.tar.gz" seed
sha="$("$python" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$temp_root/seed.tar.gz")"
cli_state="$temp_root/cli/state"
OPENPROGRAM_VERSION=0.0.0 OPENPROGRAM_STATE_DIR="$cli_state" OPENPROGRAM_BIN_DIR="$temp_root/cli/bin" \
  OPENPROGRAM_SEED_ARCHIVE="$temp_root/seed.tar.gz" OPENPROGRAM_SEED_SHA256="$sha" \
  sh "$repository/scripts/release/install-release.sh" >/dev/null
check_install "$cli_state/runtime" 'command-line seed'
test "$("$temp_root/cli/bin/openprogram" --version)" = "ran $version" || fail 'the installed command does not start the version'

printf 'thin shell smoke passed\n'
