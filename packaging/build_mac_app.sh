#!/bin/bash
# Build Synclassic.app and Synclassic.dmg on a Mac (Apple silicon or Intel).
#   packaging/build_mac_app.sh
# Needs: Python 3.11+ (python3). Optional: Xcode (for the glass icon on macOS 26).
# The app still uses Homebrew's ffmpeg and deno (brew install ffmpeg deno); it says so if they're missing.
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
# Ad-hoc signature, so it opens on Apple silicon (first time: right-click → Open).
codesign --force --deep --sign - "$APP"

echo "==> Synclassic.dmg"
STAGE="$BUILD/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
VERSION="$(python -c 'import synclassic; print(synclassic.__version__)')"
hdiutil create -volname "Synclassic" -srcfolder "$STAGE" -ov -format UDZO "$ROOT/dist/Synclassic-$VERSION.dmg"
echo "Done: dist/Synclassic.app and dist/Synclassic-$VERSION.dmg"
