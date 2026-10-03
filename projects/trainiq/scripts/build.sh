#!/usr/bin/env bash
# scripts/build.sh — Feature 0.1
#
# Must be run on macOS, with PyInstaller and a valid Apple Developer ID
# installed/configured. This script cannot be executed in the development
# sandbox this code was written in — see the Epic 0 completion report.
#
# R-ARCH-01 (Milestone 4 §10) — consistent code signing across every build,
# starting with this one, is what keeps macOS treating TrainIQ as the same
# application across rebuilds, which is a prerequisite for Keychain-stored
# credentials (Feature 0.2) surviving an app update without re-prompting.

set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "error: this script must be run on macOS." >&2
  exit 1
fi

: "${TRAINIQ_SIGNING_IDENTITY:?Set TRAINIQ_SIGNING_IDENTITY to your Apple Developer ID (e.g. 'Developer ID Application: Your Name (TEAMID)')}"

pip install --quiet pyinstaller

pyinstaller trainiq.spec --noconfirm

echo "Signing TrainIQ.app with identity: $TRAINIQ_SIGNING_IDENTITY"
codesign --deep --force --options runtime \
  --sign "$TRAINIQ_SIGNING_IDENTITY" \
  dist/TrainIQ.app

echo "Verifying signature..."
codesign --verify --deep --strict --verbose=2 dist/TrainIQ.app

echo "Done. dist/TrainIQ.app is ready."
echo ""
echo "R-ARCH-02 reminder: manually double-click-launch dist/TrainIQ.app on a"
echo "clean machine before treating this build as verified — PyInstaller has"
echo "a documented, recurring issue class where windowed macOS .app bundles"
echo "silently quit on launch (Milestone 4 §9). This is exactly the DoD for"
echo "Feature 0.1 and it is NOT satisfied by this script exiting cleanly."
