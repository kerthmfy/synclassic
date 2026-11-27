"""Make a double-clickable Synclassic.app (macOS).

The app is a small launcher: it starts the Synclassic installed in this Python environment, so
updates (git pull) apply without rebuilding it. It also adds Homebrew's folders to PATH,
because apps opened from Finder don't get the Terminal's PATH (ffmpeg and deno live there).
"""

from __future__ import annotations

import os
import pathlib
import plistlib
import shutil
import subprocess
import sys
import tempfile

from synclassic import __version__

BUNDLE_ID = "io.github.kerthmfy.synclassic"
ICONSET_SIZES = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]

LAUNCHER = """#!/bin/bash
# Synclassic launcher (made by 'synclassic install-app').
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
mkdir -p "$HOME/Library/Logs"
exec "{python}" -m synclassic.gui.app "$@" >>"$HOME/Library/Logs/Synclassic.log" 2>&1
"""


def default_destination() -> pathlib.Path:
    return pathlib.Path.home() / "Applications"


def _render_iconset(folder: pathlib.Path) -> None:
    from PySide6.QtGui import QGuiApplication

    from synclassic.gui.icon import render_icon

    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # needed to draw
    folder.mkdir(parents=True, exist_ok=True)
    for size, scale in ICONSET_SIZES:
        name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
        render_icon(size * scale).save(str(folder / name))


def build_app(destination: pathlib.Path, python: str | None = None) -> pathlib.Path:
    """Create (or replace) Synclassic.app in `destination`. Returns its path."""
    python = python or sys.executable
    app = destination / "Synclassic.app"
    contents = app / "Contents"
    if app.exists():
        shutil.rmtree(app)
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()

    launcher = contents / "MacOS" / "Synclassic"
    launcher.write_text(LAUNCHER.format(python=python), encoding="utf-8")
    launcher.chmod(0o755)

    icon_file = "Synclassic.icns"
    with tempfile.TemporaryDirectory() as tmp:
        iconset = pathlib.Path(tmp) / "Synclassic.iconset"
        _render_iconset(iconset)
        if shutil.which("iconutil"):
            subprocess.run(
                ["iconutil", "-c", "icns", str(iconset), "-o", str(contents / "Resources" / icon_file)], check=True
            )
        else:  # not on a Mac: keep a PNG so the bundle is still complete
            icon_file = "Synclassic.png"
            shutil.copy(iconset / "icon_512x512@2x.png", contents / "Resources" / icon_file)

    info = {
        "CFBundleName": "Synclassic",
        "CFBundleDisplayName": "Synclassic",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "Synclassic",
        "CFBundleIconFile": icon_file,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.music",
    }
    with open(contents / "Info.plist", "wb") as handle:
        plistlib.dump(info, handle)

    # Tell Finder/Launch Services about the new app (refreshes its icon).
    lsregister = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
                  "/Support/lsregister")
    if os.path.exists(lsregister):
        subprocess.run([lsregister, "-f", str(app)], check=False)
    os.utime(app, None)
    return app


def name_menu_bar_app() -> None:
    """On macOS, show 'Synclassic' (not 'Python') as the app name in the menu bar."""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle  # from pyobjc-framework-Cocoa
    except ImportError:
        return
    bundle = NSBundle.mainBundle()
    for info in (bundle.localizedInfoDictionary(), bundle.infoDictionary()):
        if info is not None:
            info["CFBundleName"] = "Synclassic"
