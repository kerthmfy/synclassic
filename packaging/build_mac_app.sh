#!/bin/bash
# Build Synclassic.app and Synclassic.dmg on a Mac (Apple silicon or Intel).
#   packaging/build_mac_app.sh
# Needs: Python 3.11+ (python3). Optional: Xcode (for the glass icon on macOS 26).
#
# ffmpeg and deno go inside the app (packaging/fetch_tools.sh, exact versions with checked
# fingerprints), so it works without Homebrew. SYNCLASSIC_NO_TOOLS=1 leaves them out; if the
# download fails, the app is built without them and uses Homebrew's instead.
#
# Signing: without anything set, the app gets an "ad-hoc" signature (first open: right-click →
# Open). With an Apple Developer ID ($99/year) it's signed properly, and notarized by Apple so it
# opens like any app:
#   SYNCLASSIC_SIGN_ID="Developer ID Application: Your Name (TEAMID)"   (in your keychain)
#   and for notarizing either SYNCLASSIC_NOTARY_PROFILE=<name> (made once with
#   xcrun notarytool store-credentials), or APPLE_ID + APPLE_TEAM_ID + APPLE_APP_PASSWORD.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
BUILD="$ROOT/build"
rm -rf "$BUILD" "$ROOT/dist"
mkdir -p "$BUILD"

echo "==> A clean Python environment for the build"
python3 -m venv "$BUILD/venv"
source "$BUILD/venv/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet . "pyinstaller>=6.10"

echo "==> The icon (.icns, and the glass Assets.car when Xcode is there)"
python - <<'PY'
import os, pathlib, subprocess, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
app = QApplication([])
from synclassic import macapp
build = pathlib.Path("build")
with tempfile.TemporaryDirectory() as tmp:
    iconset = pathlib.Path(tmp) / "Synclassic.iconset"
    macapp._render_iconset(iconset)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(build / "Synclassic.icns")], check=True)
(build / "resources").mkdir(exist_ok=True)
print("glass icon:", "yes" if macapp._compile_glass_icon(build / "resources") else "no (Xcode not found)")
PY

echo "==> Synclassic.app"
pyinstaller --noconfirm --clean --distpath "$ROOT/dist" --workpath "$BUILD/pyinstaller" packaging/synclassic.spec
APP="$ROOT/dist/Synclassic.app"
if [ -f "$BUILD/resources/Assets.car" ]; then
  cp "$BUILD/resources/Assets.car" "$APP/Contents/Resources/Assets.car"
fi

if [ "${SYNCLASSIC_NO_TOOLS:-0}" != "1" ]; then
  echo "==> ffmpeg and deno inside the app"
  if ! packaging/fetch_tools.sh "${SYNCLASSIC_ARCH:-$(uname -m)}" "$APP/Contents/Resources/bin"; then
    echo "    (couldn't get them: the app will use Homebrew's ffmpeg and deno instead)"
    rm -rf "$APP/Contents/Resources/bin"
  fi
fi

echo "==> Signing"
if [ -n "${SYNCLASSIC_SIGN_ID:-}" ]; then
  # Inside out: every program and library first, then the app, all with the hardened runtime.
  find "$APP/Contents" -type f \( -perm -u+x -o -name "*.dylib" -o -name "*.so" \) -print0 |
    while IFS= read -r -d '' file; do
      if file "$file" | grep -q "Mach-O"; then
        codesign --force --timestamp --options runtime --entitlements packaging/entitlements.plist \
          --sign "$SYNCLASSIC_SIGN_ID" "$file"
      fi
    done
  codesign --force --timestamp --options runtime --entitlements packaging/entitlements.plist \
    --sign "$SYNCLASSIC_SIGN_ID" "$APP"
  codesign --verify --deep --strict "$APP"
  echo "    signed as $SYNCLASSIC_SIGN_ID"
else
  # Ad-hoc signature, so it opens on Apple silicon (first time: right-click → Open).
  codesign --force --deep --sign - "$APP"
  echo "    ad-hoc (set SYNCLASSIC_SIGN_ID to sign with your Developer ID)"
fi

echo "==> Synclassic.dmg"
STAGE="$BUILD/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
VERSION="$(python -c 'import synclassic; print(synclassic.__version__)')"
DMG="$ROOT/dist/Synclassic-$VERSION.dmg"
hdiutil create -volname "Synclassic" -srcfolder "$STAGE" -ov -format UDZO "$DMG"

if [ -n "${SYNCLASSIC_SIGN_ID:-}" ]; then
  codesign --force --timestamp --sign "$SYNCLASSIC_SIGN_ID" "$DMG"
  NOTARY=()
  if [ -n "${SYNCLASSIC_NOTARY_PROFILE:-}" ]; then
    NOTARY=(--keychain-profile "$SYNCLASSIC_NOTARY_PROFILE")
  elif [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ]; then
    NOTARY=(--apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_PASSWORD")
  fi
  if [ ${#NOTARY[@]} -gt 0 ]; then
    echo "==> Notarizing (Apple checks the app; a few minutes)"
    xcrun notarytool submit "$DMG" "${NOTARY[@]}" --wait
    xcrun stapler staple "$DMG"
    echo "    notarized: it opens without warnings"
  else
    echo "    (not notarized: set SYNCLASSIC_NOTARY_PROFILE, or APPLE_ID, APPLE_TEAM_ID and APPLE_APP_PASSWORD)"
  fi
fi
echo "Done: dist/Synclassic.app and $DMG"
