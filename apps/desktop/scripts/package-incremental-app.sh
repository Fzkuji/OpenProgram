#!/usr/bin/env bash
set -euo pipefail

# Rebuild only the named components inside a copy of the installed App.
# The self-update supervisor clones the installed bundle to --app and runs
# this script from the candidate checkout inside its build sandbox; it signs
# and installs the result. Components: web, docs, tui, python, desktop.

usage() {
  printf 'usage: %s --app /absolute/path/OpenProgram.app --components web,docs,tui,python,desktop\n' "$0" >&2
  exit 2
}
app=""
components=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --app) app="${2:-}"; shift 2 || usage ;;
    --components) components="${2:-}"; shift 2 || usage ;;
    *) usage ;;
  esac
done
[[ "$app" == /* && "$(basename -- "$app")" == "OpenProgram.app" && -d "$app" && ! -L "$app" ]] || usage
[[ "$components" =~ ^(web|docs|tui|python|desktop)(,(web|docs|tui|python|desktop))*$ ]] || usage
has() { [[ ",$components," == *",$1,"* ]]; }

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../../.." && pwd)"
resources="$app/Contents/Resources"
runtime_root="$resources/runtime"
manifest="$runtime_root/runtime-manifest.json"
uv_bin="${OPENPROGRAM_UV_BIN:-$(command -v uv || true)}"

stamp() {
  printf '[incremental %s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

revision="$(git -C "$repo_root" rev-parse --verify HEAD)"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { printf 'candidate revision is unavailable\n' >&2; exit 1; }
[[ -z "$(git -C "$repo_root" status --porcelain --untracked-files=all)" ]] || {
  printf 'candidate checkout has uncommitted changes\n' >&2
  exit 1
}
[[ -f "$manifest" && ! -L "$manifest" ]] || { printf 'App runtime manifest is missing\n' >&2; exit 1; }
python_relative="$(sed -n 's/^[[:space:]]*"python":[[:space:]]*"\([^"]*\)".*/\1/p' "$manifest")"
case "$python_relative" in ''|/*|*..*) printf 'invalid runtime Python path\n' >&2; exit 1 ;; esac
python_bin="$runtime_root/$python_relative"
[[ -x "$python_bin" ]] || { printf 'App runtime Python is missing\n' >&2; exit 1; }
site_packages="$("$python_bin" -I -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
case "$site_packages" in "$runtime_root"/*) ;; *) printf 'App site-packages escapes the runtime\n' >&2; exit 1 ;; esac
frontend="$site_packages/openprogram/interfaces/server/_frontend"
cli_dist="$site_packages/openprogram/interfaces/cli/dist"
[[ -f "$frontend/chat.html" ]] || { printf 'App Web export is missing\n' >&2; exit 1; }

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-incremental.XXXXXX")"
trap 'rm -rf "$work_dir" "$repo_root/build" "$repo_root/apps/web/.next" "$repo_root/apps/web/out" "$repo_root/docs/_site"' EXIT

cd "$repo_root"
if has web || has tui || has desktop || has python; then
  stamp "installing Node dependencies"
  (unset npm_config_workspace npm_config_workspaces; npm ci --ignore-scripts)
fi

if has python; then
  stamp "building the wheel"
  [[ -x "$uv_bin" ]] || { printf 'uv is required for the Python component\n' >&2; exit 1; }
  rm -rf "$repo_root/build"
  "$uv_bin" build --offline --wheel --out-dir "$work_dir/wheel" "$repo_root"
  wheel="$(find "$work_dir/wheel" -maxdepth 1 -type f -name 'openprogram-*.whl' -print -quit)"
  [[ -n "$wheel" ]] || { printf 'OpenProgram wheel was not built\n' >&2; exit 1; }
  # The wheel carries no Web export or TUI bundle here; keep the App's own
  # (rebuilt below when those components changed too).
  mv "$frontend" "$work_dir/frontend"
  [[ ! -d "$cli_dist" ]] || mv "$cli_dist" "$work_dir/cli-dist"
  "$python_bin" -I "$repo_root/scripts/release/remove-stale-openprogram-packages.py" "$python_bin"
  "$uv_bin" pip install --offline --python "$python_bin" --strict \
    --break-system-packages --no-deps --force-reinstall "$wheel"
  rm -rf "$frontend"
  mv "$work_dir/frontend" "$frontend"
  if [[ -d "$work_dir/cli-dist" ]]; then
    rm -rf "$cli_dist"
    mv "$work_dir/cli-dist" "$cli_dist"
  fi
  package_version="$("$python_bin" -I -c 'from importlib.metadata import version; print(version("openprogram"))')"
  uv_version="$("$runtime_root/bin/uv" --version | awk '{print $2}')"
  cp "$repo_root/scripts/release/verify-product-runtime.py" "$runtime_root/bin/verify-product-runtime.py"
  "$python_bin" -I "$runtime_root/bin/verify-product-runtime.py" "$runtime_root" \
    --write --defer-browser --python-relative "$python_relative" \
    --openprogram-version "$package_version" --uv-version "$uv_version"
fi

if has web; then
  stamp "building the Web export"
  rm -rf "$repo_root/apps/web/out" "$repo_root/apps/web/.next" "$repo_root/apps/web/public/document-assets/office"
  NEXT_IGNORE_INCORRECT_LOCKFILE=1 npm run build --workspace apps/web
  [[ -f "$repo_root/apps/web/out/chat.html" ]] || { printf 'Next.js export did not produce chat.html\n' >&2; exit 1; }
  staged="$work_dir/frontend-new"
  mkdir -p "$staged"
  cp -R "$repo_root/apps/web/out/." "$staged/"
  rm -rf "$staged/docs"
  [[ ! -d "$frontend/docs" ]] || mv "$frontend/docs" "$staged/docs"
  rm -rf "$frontend"
  mv "$staged" "$frontend"
fi

if has docs; then
  stamp "building the docs site"
  [[ -x "$uv_bin" ]] || { printf 'uv is required for the docs component\n' >&2; exit 1; }
  "$uv_bin" run --isolated --locked --python 3.12 \
    --with markdown-it-py --with mdit-py-plugins --with pygments \
    python -m scripts.docs_site.build
  [[ -f "$repo_root/docs/_site/index.html" ]] || { printf 'Docs build did not produce index.html\n' >&2; exit 1; }
  rm -rf "$work_dir/docs-new"
  cp -R "$repo_root/docs/_site" "$work_dir/docs-new"
  rm -rf "$frontend/docs"
  mv "$work_dir/docs-new" "$frontend/docs"
fi

if has tui; then
  stamp "building the terminal UI"
  npm run build:standalone --workspace apps/cli
  npm run build:release --workspace apps/cli
  cp "$repo_root/apps/cli/dist/index-standalone.cjs" "$runtime_root/assets/tui/index.cjs.incoming"
  mv -f "$runtime_root/assets/tui/index.cjs.incoming" "$runtime_root/assets/tui/index.cjs"
  mkdir -p "$cli_dist"
  cp "$repo_root"/openprogram/interfaces/cli/dist/*.mjs "$cli_dist/"
fi

if has desktop; then
  stamp "repacking the Desktop archive"
  asar_cli="$repo_root/node_modules/@electron/asar/bin/asar.js"
  [[ -f "$asar_cli" ]] || { printf 'the Electron asar tool is missing\n' >&2; exit 1; }
  desktop_stage="$work_dir/desktop"
  node "$asar_cli" extract "$resources/app.asar" "$desktop_stage"
  "$python_bin" -I "$repo_root/scripts/release/restore-asar-permissions.py" \
    "$resources/app.asar" "$desktop_stage"
  # Copy every file build.files names: a relative file, or a whole folder
  # written "folder/**" (dotfiles included). Never keep a second list here.
  node - "$repo_root/apps/desktop/package.json" <<'NODE' >"$work_dir/desktop-files.txt"
const fs = require("node:fs");
const path = require("node:path");
const desktopRoot = path.dirname(process.argv[2]);
const files = require(process.argv[2]).build.files;
function under(folder) {
  const found = [];
  for (const entry of fs.readdirSync(path.join(desktopRoot, folder), { withFileTypes: true })) {
    const name = `${folder}/${entry.name}`;
    if (entry.isDirectory()) found.push(...under(name));
    else if (entry.isFile()) found.push(name);
  }
  return found.sort();
}
for (const name of files) {
  const folder = typeof name === "string" && name.endsWith("/**") ? name.slice(0, -3) : null;
  const relative = folder ?? name;
  if (typeof relative !== "string" || !relative || relative.startsWith("/") || relative.split("/").includes("..") ||
      /[*?[\]!\\\n\r]/.test(relative)) {
    throw new Error(`unsupported desktop module path: ${name}`);
  }
  if (folder === null) { console.log(name); continue; }
  if (!fs.statSync(path.join(desktopRoot, folder), { throwIfNoEntry: false })?.isDirectory()) {
    throw new Error(`desktop folder listed in build.files is missing: ${name}`);
  }
  for (const found of under(folder)) console.log(found);
}
NODE
  # main/ is replaced whole, so a module removed from it does not linger.
  rm -rf "$desktop_stage/main"
  while IFS= read -r desktop_file; do
    [[ -n "$desktop_file" ]] || continue
    [[ -f "$repo_root/apps/desktop/$desktop_file" ]] || {
      printf 'desktop module listed in build.files is missing: %s\n' "$desktop_file" >&2
      exit 1
    }
    mkdir -p "$(dirname -- "$desktop_stage/$desktop_file")"
    cp "$repo_root/apps/desktop/$desktop_file" "$desktop_stage/$desktop_file"
  done <"$work_dir/desktop-files.txt"
  # The installed asar may still hold modules from before they moved into
  # main/; only the files build.files names stay at the top level.
  for staged_module in "$desktop_stage"/*.js; do
    [[ -f "$staged_module" ]] || continue
    grep -qxF "$(basename -- "$staged_module")" "$work_dir/desktop-files.txt" \
      || rm -f "$staged_module"
  done
  rm -rf "$desktop_stage/tab-transfer" "$desktop_stage/shell-integration"
  node "$asar_cli" pack "$desktop_stage" "$work_dir/app.asar" --unpack-dir node_modules/node-pty
  mv -f "$work_dir/app.asar" "$resources/app.asar"
  if [[ -d "$work_dir/app.asar.unpacked" ]]; then
    rm -rf "$resources/app.asar.unpacked"
    mv "$work_dir/app.asar.unpacked" "$resources/app.asar.unpacked"
  fi
fi

stamp "recording revision $revision"
printf '%s\n' "$revision" >"$site_packages/openprogram/_build_revision.txt"
printf '%s\n' "$revision" >"$resources/openprogram-source-revision"
if has web || has python || has desktop; then
  node "$script_dir/write-reopen-protocol.cjs" --resources "$resources"
fi

stamp "smoke testing the packaged runtime"
OPENPROGRAM_SELF_UPDATE_DEFER_BROWSER="${OPENPROGRAM_SELF_UPDATE_DEFER_BROWSER:-1}" \
OPENPROGRAM_SMOKE_PORT="${OPENPROGRAM_SMOKE_PORT:-}" \
  bash "$repo_root/scripts/release/smoke-packaged-runtime.sh" mac "$(dirname -- "$app")"
printf 'OpenProgram incremental artifact written to %s (%s)\n' "$app" "$components"
