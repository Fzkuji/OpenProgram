#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
desktop_dir="$(cd -- "$script_dir/.." && pwd)"
repository_dir="$(cd -- "$desktop_dir/../.." && pwd)"
modern_icon_dir="$desktop_dir/build/AppIcon.icon"
modern_icon_json="$modern_icon_dir/icon.json"
packaged_icon="$desktop_dir/build/icon.icns"
modern_assets_dir="$modern_icon_dir/Assets"
modern_symbol_svgs=(
  "$modern_assets_dir/01-cell.svg"
  "$modern_assets_dir/02-disc-indigo.svg"
  "$modern_assets_dir/03-disc-violet.svg"
  "$modern_assets_dir/04-disc-sky.svg"
)
package_json="$desktop_dir/package.json"
release_workflow="$repository_dir/scripts/release/release-matrix.py"

fail() {
  printf 'icon check failed: %s\n' "$*" >&2
  exit 1
}

for command_name in node sips; do
  command -v "$command_name" >/dev/null 2>&1 \
    || fail "missing command: $command_name"
done

[[ -f "$modern_icon_json" ]] \
  || fail "missing Apple icon source: build/AppIcon.icon/icon.json"
[[ -f "$packaged_icon" ]] \
  || fail "missing packaged macOS icon: build/icon.icns"
for modern_symbol_svg in "${modern_symbol_svgs[@]}"; do
  [[ -f "$modern_symbol_svg" ]] \
    || fail "missing Apple icon artwork: ${modern_symbol_svg#"$desktop_dir/"}"
done
[[ -f "$release_workflow" ]] || fail "missing release workflow"

for modern_symbol_svg in "${modern_symbol_svgs[@]}"; do
  grep -q 'viewBox="0 0 1024 1024"' "$modern_symbol_svg" \
    || fail "Apple icon artwork must use a 1024 x 1024 viewBox: ${modern_symbol_svg##*/}"
done
disc_count="$(grep -hEo 'id="op-disc-[abc]"' "${modern_symbol_svgs[@]}" | wc -l | tr -d ' ')"
[[ "$disc_count" == "3" ]] \
  || fail "Apple icon artwork must contain exactly three brand discs"
grep -q 'id="op-cell"' "${modern_symbol_svgs[0]}" \
  || fail "Apple icon artwork must preserve the brand cell"
grep -q 'fill="url(#op-cell-fill)"' "${modern_symbol_svgs[0]}" \
  || fail "Apple icon cell must use its lavender window fill"
disc_colors=("#4F46E5" "#8B5CF6" "#38BDF8")
for index in 1 2 3; do
  disc_svg="${modern_symbol_svgs[$index]}"
  grep -q "fill=\"${disc_colors[$((index - 1))]}\"" "$disc_svg" \
    || fail "Apple icon disc must stay a flat approved colour: ${disc_svg##*/}"
  if grep -q 'Gradient' "$disc_svg"; then
    fail "Apple icon discs must stay flat, without sphere lighting: ${disc_svg##*/}"
  fi
done

if grep -Eqi 'squircle|rounded|clipPath|mask|filter|shadow|sheen|rim|<rect' "${modern_symbol_svgs[@]}"; then
  fail "Apple icon artwork must not pre-draw the outer shape or system effects"
fi
if grep -Eq '<text|<image|\{|\}' "${modern_symbol_svgs[@]}"; then
  fail "Apple icon artwork must remain vector-only"
fi

node - "$package_json" "$modern_icon_json" <<'NODE'
const fs = require("fs");
const pkg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const icon = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

if (pkg.build?.mac?.icon !== "build/icon.icns") {
  throw new Error("build.mac.icon must use the approved flat macOS icon");
}
if (pkg.scripts?.["icon:check"] !== "bash scripts/check-icon.sh") {
  throw new Error("icon:check must invoke scripts/check-icon.sh");
}
if (pkg.scripts?.["icon:build"] !== undefined) {
  throw new Error("the removed hand-drawn icon build must not return");
}
if (icon.fill?.["linear-gradient"]?.length !== 2) {
  throw new Error("AppIcon.icon must fill the tile with the indigo linear gradient");
}
// Icon Composer lists groups front to back.
const expectedLayers = [
  "04-disc-sky.svg",
  "03-disc-violet.svg",
  "02-disc-indigo.svg",
  "01-cell.svg",
];
if (icon.groups?.length !== expectedLayers.length) {
  throw new Error("AppIcon.icon must use four ordered depth groups");
}
for (let index = 0; index < expectedLayers.length; index += 1) {
  const group = icon.groups[index];
  if (group?.specular !== false) {
    throw new Error(`AppIcon.icon depth group ${index + 1} must disable the specular halo`);
  }
  if (group?.translucency?.enabled !== false) {
    throw new Error(`AppIcon.icon depth group ${index + 1} must stay opaque`);
  }
  if (group?.shadow?.kind !== "neutral") {
    throw new Error(`AppIcon.icon depth group ${index + 1} must use a neutral shadow`);
  }
  const layers = group?.layers;
  if (layers?.length !== 1 || layers[0]?.["image-name"] !== expectedLayers[index] || layers[0]?.glass !== false) {
    throw new Error(`AppIcon.icon depth group ${index + 1} must reference ${expectedLayers[index]} without glass`);
  }
}
if (icon["supported-platforms"]?.squares !== "shared") {
  throw new Error("AppIcon.icon must declare shared square platforms");
}
NODE

grep -q '"macos-26"' "$release_workflow" \
  || fail "arm64 desktop releases must use the macos-26 runner"
grep -q '"macos-15-intel"' "$release_workflow" \
  || fail "x86_64 desktop releases must use the macos-15-intel runner"

if [[ "${OPENPROGRAM_SELF_UPDATE_DEFER_ICON_RENDER:-}" != 1 ]]; then
  audit_dir="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-icon-check.XXXXXX")"
  trap 'rm -rf "$audit_dir"' EXIT
  for modern_symbol_svg in "${modern_symbol_svgs[@]}"; do
    rendered="$audit_dir/${modern_symbol_svg##*/}.png"
    sips -s format png "$modern_symbol_svg" --out "$rendered" >/dev/null
    dimensions="$(sips -g pixelWidth -g pixelHeight "$rendered" 2>/dev/null | awk '/pixelWidth:/ {w=$2} /pixelHeight:/ {h=$2} END {print w "x" h}')"
    [[ "$dimensions" == "1024x1024" ]] \
      || fail "Apple icon artwork must render at 1024 x 1024: ${modern_symbol_svg##*/}"
    alpha="$(sips -g hasAlpha "$rendered" 2>/dev/null | awk '/hasAlpha:/ {print $2}')"
    [[ "$alpha" == "yes" ]] \
      || fail "Apple icon artwork must keep a transparent canvas: ${modern_symbol_svg##*/}"
  done
fi

printf 'Apple icon source checks passed\n'
