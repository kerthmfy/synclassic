import os
import plistlib

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtGui")


def test_install_app_makes_a_complete_bundle(tmp_path):
    from synclassic.cli import main

    assert main(["install-app", "--dest", str(tmp_path)]) == 0
    app = tmp_path / "Synclassic.app" / "Contents"
    info = plistlib.loads((app / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "Synclassic" and info["CFBundleExecutable"] == "Synclassic"
    assert (app / "Resources" / info["CFBundleIconFile"]).is_file()
    launcher = app / "MacOS" / "Synclassic"
    assert os.access(launcher, os.X_OK)
    script = launcher.read_text()
    assert "/opt/homebrew/bin" in script and "-m synclassic.gui.app" in script and "Synclassic.log" in script


def test_icon_renders_at_every_size():
    from PySide6.QtGui import QGuiApplication

    from synclassic.gui.icon import render_icon

    _app = QGuiApplication.instance() or QGuiApplication([])
    for size in (16, 32, 128, 512, 1024):
        image = render_icon(size)
        assert image.width() == size and image.pixelColor(size // 2, size // 2).alpha() > 0
