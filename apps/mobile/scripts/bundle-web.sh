#!/usr/bin/env bash
# Build the web UI's static export and put it in the phone app (ios/web).
# The app serves these files itself, so none of the UI crosses the network
# (docs/reference/design/distribution/mobile-app.html).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
web="$here/../web"
if [[ "${1:-}" != "--no-build" ]]; then
  (cd "$web" && npm run build)
fi
rm -rf "$here/ios/web"
mkdir -p "$here/ios/web"
# Source maps stay out of the app.
rsync -a --exclude '*.map' "$web/out/" "$here/ios/web/"
echo "bundled $(du -sh "$here/ios/web" | cut -f1) of web UI into ios/web"
