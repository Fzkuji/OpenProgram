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
  "$modern_assets_dir/01-orbit.svg"
  "$modern_assets_dir/02-node-blue.svg"
  "$modern_assets_dir/03-node-purple.svg"
  "$modern_assets_dir/04-node-indigo.svg"
)
package_json="$desktop_dir/package.json"
release_workflow="$repository_dir/.github/workflows/release.yml"

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
node_count="$(grep -hEo 'id="op-node-[abc]"' "${modern_symbol_svgs[@]}" | wc -l | tr -d ' ')"
[[ "$node_count" == "3" ]] \
  || fail "Apple icon artwork must contain exactly three brand nodes"
grep -q 'id="op-orbit"' "${modern_symbol_svgs[0]}" \
  || fail "Apple icon artwork must preserve the brand orbit"
grep -q 'r="326"' "${modern_symbol_svgs[0]}" \
  || fail "Apple icon orbit must use the approved larger footprint"
grep -q '<radialGradient id="op-ring-depth" cx="512" cy="512" r="354" gradientUnits="userSpaceOnUse">' "${modern_symbol_svgs[0]}" \
  || fail "Apple icon orbit must use the approved convex cross-section shading"
grep -q 'id="op-orbit-depth"' "${modern_symbol_svgs[0]}" \
  || fail "Apple icon orbit must apply its convex cross-section shading"
grep -q 'stroke="url(#op-ring-depth)"' "${modern_symbol_svgs[0]}" \
  || fail "Apple icon orbit must render the approved convex depth overlay"
if grep -Eqi 'stop-color="(#fff|#ffffff|white)"' "${modern_symbol_svgs[0]}"; then
  fail "Apple icon orbit depth shading must not introduce a white circular halo"
fi
grep -q 'r="140"' "${modern_symbol_svgs[1]}" \
  || fail "Apple icon blue node must use the approved larger footprint"
grep -q 'r="105"' "${modern_symbol_svgs[2]}" \
  || fail "Apple icon purple node must use the approved larger footprint"
grep -q 'r="55"' "${modern_symbol_svgs[3]}" \
  || fail "Apple icon indigo node must use the approved larger footprint"
node_gradient_ids=(op-node-blue op-node-purple op-node-indigo)
for index in 1 2 3; do
  node_svg="${modern_symbol_svgs[$index]}"
  gradient_id="${node_gradient_ids[$((index - 1))]}"
  grep -q "<radialGradient id=\"$gradient_id\" cx=\"35%\" cy=\"28%\" r=\"72%\"" "$node_svg" \
    || fail "Apple icon node must use the approved upper-left convex lighting: ${node_svg##*/}"
  grep -q "fill=\"url(#$gradient_id)\"" "$node_svg" \
    || fail "Apple icon node must use its approved convex gradient: ${node_svg##*/}"
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
if (!icon.fill?.["automatic-gradient"]) {
  throw new Error("AppIcon.icon must delegate its background treatment to Icon Composer");
}
const expectedLayers = [
  "01-orbit.svg",
  "02-node-blue.svg",
  "03-node-purple.svg",
  "04-node-indigo.svg",
];
if (icon.groups?.length !== expectedLayers.length) {
  throw new Error("AppIcon.icon must use four ordered depth groups");
}
for (let index = 0; index < expectedLayers.length; index += 1) {
  if (icon.groups[index]?.specular !== false) {
    throw new Error(`AppIcon.icon depth group ${index + 1} must disable the circular specular halo`);
  }
  const shadow = icon.groups[index]?.shadow;
  if (shadow?.kind !== "neutral" || shadow?.opacity !== 0.42) {
    throw new Error(`AppIcon.icon depth group ${index + 1} must use the approved neutral shadow`);
  }
  const layers = icon.groups[index]?.layers;
  if (layers?.length !== 1 || layers[0]?.["image-name"] !== expectedLayers[index]) {
    throw new Error(`AppIcon.icon depth group ${index + 1} must reference ${expectedLayers[index]}`);
  }
}
if (icon["supported-platforms"]?.squares !== "shared") {
  throw new Error("AppIcon.icon must declare shared square platforms");
}
NODE

grep -q 'runner: macos-26$' "$release_workflow" \
  || fail "arm64 desktop releases must use the macos-26 runner"
grep -q 'runner: macos-15-intel$' "$release_workflow" \
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
