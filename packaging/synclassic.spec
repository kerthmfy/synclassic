# PyInstaller recipe for Synclassic.app (build on a Mac: packaging/build_mac_app.sh).
# -*- mode: python -*-
import os
import pathlib

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

from synclassic import __version__

root = pathlib.Path(SPECPATH).parent  # noqa: F821 (provided by PyInstaller)
icon = os.environ.get("SYNCLASSIC_ICNS", str(root / "build" / "Synclassic.icns"))

datas = collect_data_files("synclassic") + collect_data_files("ytmusicapi") + collect_data_files("certifi")
hiddenimports = (collect_submodules("synclassic") + collect_submodules("yt_dlp") + collect_submodules("mutagen")
                 + ["PySide6.QtMultimedia", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore", "PySide6.QtPdf",
                    "PySide6.QtSvg"])

a = Analysis(  # noqa: F821
    [str(root / "packaging" / "synclassic_app.py")],
    pathex=[str(root / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Synclassic", console=False,  # noqa: F821
          target_arch=os.environ.get("SYNCLASSIC_ARCH") or None)
coll = COLLECT(exe, a.binaries, a.datas, name="Synclassic")  # noqa: F821
app = BUNDLE(  # noqa: F821
    coll,
    name="Synclassic.app",
    icon=icon if os.path.exists(icon) else None,
    bundle_identifier="io.github.kerthmfy.synclassic",
    info_plist={
        "CFBundleName": "Synclassic",
        "CFBundleDisplayName": "Synclassic",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "CFBundleIconName": "Synclassic",  # the glass icon in Assets.car (added by the build script)
        "LSApplicationCategoryType": "public.app-category.music",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSContactsUsageDescription": "Synclassic copies your contacts to the iPod when you ask it to.",
        "NSAppleEventsUsageDescription": "Synclassic reads your contacts and notes (when you ask) to put them on the iPod.",
    },
)
