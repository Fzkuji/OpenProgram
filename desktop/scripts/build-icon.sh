#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
desktop_dir="$(cd -- "$script_dir/.." && pwd)"
build_dir="$desktop_dir/build"
source_svg="$build_dir/icon.svg"
master_png="$build_dir/icon.png"
iconset_dir="$build_dir/icon.iconset"
icon_icns="$build_dir/icon.icns"

[[ "$(uname -s)" == "Darwin" ]] || {
  printf 'icon build requires macOS\n' >&2
  exit 1
}
for command_name in iconutil sips; do
  command -v "$command_name" >/dev/null 2>&1 || {
    printf 'missing command: %s\n' "$command_name" >&2
    exit 1
  }
done
[[ -f "$source_svg" ]] || {
  printf 'missing source: %s\n' "$source_svg" >&2
  exit 1
}

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-icon-build.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT
work_master="$work_dir/icon.png"
work_iconset="$work_dir/icon.iconset"
work_icns="$work_dir/icon.icns"
mkdir -p "$work_iconset"

sips -s format png "$source_svg" --out "$work_master" >/dev/null

icon_names=(
  icon_16x16.png
  icon_16x16@2x.png
  icon_32x32.png
  icon_32x32@2x.png
  icon_128x128.png
  icon_128x128@2x.png
  icon_256x256.png
  icon_256x256@2x.png
  icon_512x512.png
  icon_512x512@2x.png
)
icon_sizes=(16 32 32 64 128 256 256 512 512 1024)

for index in "${!icon_names[@]}"; do
  size="${icon_sizes[$index]}"
  sips -z "$size" "$size" "$work_master" \
    --out "$work_iconset/${icon_names[$index]}" >/dev/null
done
iconutil -c icns "$work_iconset" -o "$work_icns"

mkdir -p "$build_dir"
[[ "$iconset_dir" == "$build_dir/icon.iconset" ]] || {
  printf 'refusing to replace unexpected iconset path: %s\n' "$iconset_dir" >&2
  exit 1
}
rm -rf "$iconset_dir"
mv "$work_iconset" "$iconset_dir"
mv -f "$work_master" "$master_png"
mv -f "$work_icns" "$icon_icns"

printf 'generated %s, %s, and %s\n' \
  "$master_png" "$iconset_dir" "$icon_icns"
