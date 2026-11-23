#!/usr/bin/env bash
# Builds the install packages into dist/:
#   AITokenTracker-Claude-<version>-chrome.zip   Chrome, Edge, Brave, Arc (Load unpacked or Chrome Web Store upload)
#   AITokenTracker-Claude-<version>-firefox.zip  Firefox (Load Temporary Add-on, or addons.mozilla.org upload)
set -euo pipefail
cd "$(dirname "$0")"

VERSION=$(python3 -c "import json;print(json.load(open('manifest.json'))['version'])")
FILES=(i18n.js background.js content.js hook.js shared.js tokenizer.js styles.css popup.html popup.js popup.css icons)
rm -rf dist && mkdir -p dist/tmp

build() {
  local variant="$1" manifest="$2" out="dist/AITokenTracker-Claude-${VERSION}-$1.zip"
  rm -rf dist/tmp/* && cp -r "${FILES[@]}" dist/tmp/ && cp "$manifest" dist/tmp/manifest.json
  (cd dist/tmp && zip -qr "../$(basename "$out")" .)
  echo "built $out"
}

build chrome manifest.json
build firefox manifest_firefox.json
rm -rf dist/tmp
