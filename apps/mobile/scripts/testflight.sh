#!/usr/bin/env bash
# Build the iOS app and upload it to TestFlight (or only export the .ipa
# with --export-only). The archive is left unsigned and the export signs it
# with the team's Apple Distribution certificate, so no registered device
# is needed; App Store Connect numbers each upload's build itself.
# Needs Xcode signed in to the team's account and the app's record in
# App Store Connect (docs/reference/design/distribution/mobile-app.html).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
destination=upload
if [[ "${1:-}" == "--export-only" ]]; then destination=export; shift; fi
out="${TMPDIR:-/tmp}/openprogram-ios"
rm -rf "$out"
mkdir -p "$out"

bash "$here/scripts/bundle-web.sh" "$@"

xcodebuild -project "$here/ios/OpenProgram.xcodeproj" -scheme OpenProgram \
  -configuration Release -destination 'generic/platform=iOS' \
  -archivePath "$out/OpenProgram.xcarchive" CODE_SIGNING_ALLOWED=NO archive -quiet

cat > "$out/export.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>method</key><string>app-store-connect</string>
<key>teamID</key><string>P7U5M4PX5L</string>
<key>signingStyle</key><string>automatic</string>
<key>destination</key><string>$destination</string>
<key>manageAppVersionAndBuildNumber</key><true/>
</dict></plist>
PLIST

xcodebuild -exportArchive -archivePath "$out/OpenProgram.xcarchive" \
  -exportPath "$out/export" -exportOptionsPlist "$out/export.plist" \
  -allowProvisioningUpdates -quiet
if [[ "$destination" == upload ]]; then
  echo "uploaded; the build shows in TestFlight once App Store Connect has processed it"
else
  echo "exported $out/export/OpenProgram.ipa"
fi
