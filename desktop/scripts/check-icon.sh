#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
desktop_dir="$(cd -- "$script_dir/.." && pwd)"
build_dir="$desktop_dir/build"
source_svg="$build_dir/icon.svg"
master_png="$build_dir/icon.png"
iconset_dir="$build_dir/icon.iconset"
icon_icns="$build_dir/icon.icns"
package_json="$desktop_dir/package.json"

fail() {
  printf 'icon check failed: %s\n' "$*" >&2
  exit 1
}

for command_name in iconutil node sips xcrun; do
  command -v "$command_name" >/dev/null 2>&1 || fail "missing command: $command_name"
done

[[ -f "$source_svg" ]] || fail "missing maintained source: build/icon.svg"
[[ -f "$master_png" ]] || fail "missing master PNG: build/icon.png"
[[ -d "$iconset_dir" ]] || fail "missing iconset: build/icon.iconset"
[[ -f "$icon_icns" ]] || fail "missing packaged icon: build/icon.icns"

grep -q 'viewBox="0 0 1024 1024"' "$source_svg" \
  || fail "icon.svg must use a 1024 x 1024 viewBox"
grep -q 'id="op-macos-icon-mask"' "$source_svg" \
  || fail "icon.svg must define the legacy macOS icon mask"
grep -q 'clip-path="url(#op-macos-icon-clip)"' "$source_svg" \
  || fail "icon.svg artwork must use the legacy macOS icon mask"
grep -q 'x="100" y="100" width="824" height="824" rx="185"' "$source_svg" \
  || fail "icon.svg mask must use the 824 px body and 185 px corner radius"
for background_color in '#29374D' '#19243A' '#101622'; do
  grep -q "stop-color=\"$background_color\"" "$source_svg" \
    || fail "icon.svg must preserve the approved deep-blue background"
done
node_count="$(grep -Eo 'id="op-node-[abc]"' "$source_svg" | wc -l | tr -d ' ')"
[[ "$node_count" == "3" ]] || fail "icon.svg must contain exactly three brand nodes"
if grep -Eq '<radialGradient id="op-node-' "$source_svg"; then
  fail "brand nodes must not use spherical radial shading"
fi
if grep -Eq '<text|<image|\{|\}' "$source_svg"; then
  fail "icon.svg must not contain text, braces, or embedded raster images"
fi

node - "$package_json" <<'NODE'
const fs = require("fs");
const pkg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
if (pkg.build?.mac?.icon !== "build/icon.icns") {
  throw new Error("build.mac.icon must be build/icon.icns");
}
if (pkg.scripts?.["icon:build"] !== "bash scripts/build-icon.sh") {
  throw new Error("icon:build must invoke scripts/build-icon.sh");
}
if (pkg.scripts?.["icon:check"] !== "bash scripts/check-icon.sh") {
  throw new Error("icon:check must invoke scripts/check-icon.sh");
}
NODE

image_size() {
  local image_path="$1"
  local width height
  width="$(sips -g pixelWidth "$image_path" 2>/dev/null | awk '/pixelWidth:/{print $2}')"
  height="$(sips -g pixelHeight "$image_path" 2>/dev/null | awk '/pixelHeight:/{print $2}')"
  printf '%sx%s' "$width" "$height"
}

[[ "$(image_size "$master_png")" == "1024x1024" ]] \
  || fail "icon.png must be 1024 x 1024"

# A legacy ICNS is displayed as-is by Electron/macOS 15. Keep the visible body
# on the 824 px macOS template bounds and leave the canvas corners transparent.
xcrun swift - "$master_png" <<'SWIFT'
import AppKit
import Foundation

let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path) else {
    fputs("icon check failed: cannot load icon.png\n", stderr)
    exit(1)
}
var rect = NSRect(origin: .zero, size: image.size)
guard let source = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
    fputs("icon check failed: cannot decode icon.png\n", stderr)
    exit(1)
}
let width = source.width
let height = source.height
var bytes = [UInt8](repeating: 0, count: width * height * 4)
let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue)
bytes.withUnsafeMutableBytes { raw in
    let context = CGContext(
        data: raw.baseAddress,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: width * 4,
        space: colorSpace,
        bitmapInfo: bitmapInfo.rawValue
    )!
    context.draw(source, in: CGRect(x: 0, y: 0, width: width, height: height))
}
func alphaBounds(threshold: UInt8) -> (Int, Int, Int, Int) {
    var minX = width
    var minY = height
    var maxX = -1
    var maxY = -1
    for y in 0..<height {
        for x in 0..<width where bytes[(y * width + x) * 4 + 3] >= threshold {
            minX = min(minX, x)
            minY = min(minY, y)
            maxX = max(maxX, x)
            maxY = max(maxY, y)
        }
    }
    return (minX, minY, maxX, maxY)
}

let solid = alphaBounds(threshold: 128)
if !(99...101).contains(solid.0) || !(99...101).contains(solid.1) ||
   !(922...924).contains(solid.2) || !(922...924).contains(solid.3) {
    fputs("icon check failed: opaque icon body must use the 100...923 template bounds\n", stderr)
    exit(1)
}
let topBodyPixels = (0..<width).filter {
    bytes[(100 * width + $0) * 4 + 3] >= 128
}
if !(265...280).contains(topBodyPixels.first ?? -1) ||
   !(743...758).contains(topBodyPixels.last ?? -1) ||
   !(470...490).contains(topBodyPixels.count) {
    fputs("icon check failed: top corner profile does not match the macOS legacy mask\n", stderr)
    exit(1)
}
let cornerAlpha = [
    bytes[3],
    bytes[(width - 1) * 4 + 3],
    bytes[((height - 1) * width) * 4 + 3],
    bytes[((height * width) - 1) * 4 + 3],
]
if cornerAlpha.max()! > 8 {
    fputs("icon check failed: legacy ICNS canvas corners must be transparent\n", stderr)
    exit(1)
}
let visible = alphaBounds(threshold: 8)
print("icon alpha bounds: visible \(visible.0),\(visible.1) ... \(visible.2),\(visible.3); body \(solid.0),\(solid.1) ... \(solid.2),\(solid.3)")
SWIFT

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

check_iconset() {
  local directory="$1"
  local label="$2"
  local png_count
  png_count="$(find "$directory" -maxdepth 1 -type f -name '*.png' | wc -l | tr -d ' ')"
  [[ "$png_count" == "10" ]] || fail "$label must contain exactly 10 PNG representations"
  for index in "${!icon_names[@]}"; do
    local icon_path="$directory/${icon_names[$index]}"
    local expected_size="${icon_sizes[$index]}x${icon_sizes[$index]}"
    [[ -f "$icon_path" ]] || fail "$label is missing ${icon_names[$index]}"
    [[ "$(image_size "$icon_path")" == "$expected_size" ]] \
      || fail "$label/${icon_names[$index]} must be $expected_size"
  done
}

check_iconset "$iconset_dir" "build/icon.iconset"

audit_dir="$(mktemp -d "${TMPDIR:-/tmp}/openprogram-icon-check.XXXXXX")"
trap 'rm -rf "$audit_dir"' EXIT
roundtrip_dir="$audit_dir/roundtrip.iconset"
iconutil -c iconset "$icon_icns" -o "$roundtrip_dir"
check_iconset "$roundtrip_dir" "icon.icns round trip"

for size in 16 32 48 64; do
  sips -z "$size" "$size" "$master_png" \
    --out "$audit_dir/expected-$size.png" >/dev/null
done
sips -z 48 48 "$roundtrip_dir/icon_32x32@2x.png" \
  --out "$audit_dir/roundtrip-48.png" >/dev/null

# Decode into premultiplied sRGB pixels before comparing. This ignores hidden
# RGB values in fully transparent pixels while still detecting the malformed
# low-resolution ICNS entries produced by the old automatic conversion.
OPENPROGRAM_ICON_PROFILE="$iconset_dir/icon_256x256.png" xcrun swift - 8.0 \
  16-source "$audit_dir/expected-16.png" "$iconset_dir/icon_16x16.png" \
  16-icns "$audit_dir/expected-16.png" "$roundtrip_dir/icon_16x16.png" \
  32-source "$audit_dir/expected-32.png" "$iconset_dir/icon_32x32.png" \
  32-source-retina "$audit_dir/expected-32.png" "$iconset_dir/icon_16x16@2x.png" \
  32-icns "$audit_dir/expected-32.png" "$roundtrip_dir/icon_32x32.png" \
  32-icns-retina "$audit_dir/expected-32.png" "$roundtrip_dir/icon_16x16@2x.png" \
  48-icns "$audit_dir/expected-48.png" "$audit_dir/roundtrip-48.png" \
  64-source "$audit_dir/expected-64.png" "$iconset_dir/icon_32x32@2x.png" \
  64-icns "$audit_dir/expected-64.png" "$roundtrip_dir/icon_32x32@2x.png" <<'SWIFT'
import AppKit
import Foundation

func decodedRGBA(_ path: String) throws -> (width: Int, height: Int, bytes: [UInt8]) {
    guard let image = NSImage(contentsOfFile: path) else {
        throw NSError(domain: "IconCheck", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "cannot load \(path)"])
    }
    var rect = NSRect(origin: .zero, size: image.size)
    guard let source = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
        throw NSError(domain: "IconCheck", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "cannot decode \(path)"])
    }
    let width = source.width
    let height = source.height
    var bytes = [UInt8](repeating: 0, count: width * height * 4)
    let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
    let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue)
    bytes.withUnsafeMutableBytes { raw in
        let context = CGContext(
            data: raw.baseAddress,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width * 4,
            space: colorSpace,
            bitmapInfo: bitmapInfo.rawValue
        )!
        context.interpolationQuality = .none
        context.draw(source, in: CGRect(x: 0, y: 0, width: width, height: height))
    }
    return (width, height, bytes)
}

let arguments = CommandLine.arguments
let threshold = Double(arguments[1])!
for index in stride(from: 2, to: arguments.count, by: 3) {
    let label = arguments[index]
    let expected = try decodedRGBA(arguments[index + 1])
    let actual = try decodedRGBA(arguments[index + 2])
    guard expected.width == actual.width, expected.height == actual.height else {
        fputs("icon check failed: \(label) dimensions differ\n", stderr)
        exit(1)
    }
    let difference = zip(expected.bytes, actual.bytes).reduce(0) {
        $0 + abs(Int($1.0) - Int($1.1))
    }
    let meanDifference = Double(difference) / Double(expected.bytes.count)
    print(String(format: "%@: mean pixel difference %.3f", label, meanDifference))
    if meanDifference > threshold {
        fputs("icon check failed: \(label) is visually corrupted\n", stderr)
        exit(1)
    }
}

let profilePath = ProcessInfo.processInfo.environment["OPENPROGRAM_ICON_PROFILE"]!
let profile = try decodedRGBA(profilePath)
guard profile.width == 256, profile.height == 256 else {
    fputs("icon check failed: contour profile must be 256 x 256\n", stderr)
    exit(1)
}
func solidSpan(y: Int) -> (first: Int, last: Int, count: Int) {
    let pixels = (0..<profile.width).filter {
        profile.bytes[(y * profile.width + $0) * 4 + 3] >= 128
    }
    return (pixels.first ?? -1, pixels.last ?? -1, pixels.count)
}
let solidArea = stride(from: 3, to: profile.bytes.count, by: 4).reduce(0) {
    $0 + (profile.bytes[$1] >= 128 ? 1 : 0)
}
let expectedSpans = [
    (25, 67...70, 185...188, 118...124),
    (30, 49...51, 204...207, 154...159),
    (40, 36...38, 217...219, 180...184),
    (70, 25...25, 230...230, 206...206),
]
if !(40_480...40_650).contains(solidArea) {
    fputs("icon check failed: 256 px legacy mask has the wrong solid area\n", stderr)
    exit(1)
}
for (y, firstRange, lastRange, countRange) in expectedSpans {
    let span = solidSpan(y: y)
    if !firstRange.contains(span.first) || !lastRange.contains(span.last) ||
       !countRange.contains(span.count) {
        fputs("icon check failed: 256 px legacy mask contour differs at y=\(y)\n", stderr)
        exit(1)
    }
}
print("256-mask: solid area \(solidArea); contour checks passed")
SWIFT

printf 'icon checks passed\n'
