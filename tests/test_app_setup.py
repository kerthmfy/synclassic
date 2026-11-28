"""Zero-setup: the app's own tools, the setup assistant, and checking for new versions."""

import os
import pathlib
import sys
from types import SimpleNamespace

import pytest

from synclassic import app_update, config, tools


def test_the_apps_own_tools_come_first(tmp_path, monkeypatch):
    app = tmp_path / "Synclassic.app" / "Contents"
    (app / "MacOS").mkdir(parents=True)
    (app / "Resources" / "bin").mkdir(parents=True)
    ffmpeg = app / "Resources" / "bin" / "ffmpeg"
    ffmpeg.write_text("#!/bin/sh\n")
    ffmpeg.chmod(0o755)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "MacOS" / "Synclassic"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    tools.add_to_path()
    assert os.environ["PATH"].split(os.pathsep)[0] == str(app / "Resources" / "bin")
    assert tools.where("ffmpeg") == "in the app"
    tools.add_to_path()  # twice: no duplicates
    assert os.environ["PATH"].count(str(app / "Resources" / "bin")) == 1
    assert tools.where("surely-not-a-tool") == "missing"
    monkeypatch.setattr(sys, "frozen", False)
    assert tools.bundled_dir() is None


def test_fetch_tools_refuses_a_wrong_download(tmp_path):
    import subprocess

    fake = tmp_path / "fakebin"
    fake.mkdir()
    curl = fake / "curl"  # "downloads" something that isn't the expected file
    curl.write_text('#!/bin/bash\nwhile [ $# -gt 0 ]; do if [ "$1" = "-o" ]; then echo nope > "$2"; fi; shift; done\n')
    curl.chmod(0o755)
    script = pathlib.Path(__file__).parent.parent / "packaging" / "fetch_tools.sh"
    result = subprocess.run(["bash", str(script), "arm64", str(tmp_path / "bin")], capture_output=True, text=True,
                            env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"})
    assert result.returncode != 0 and "Refused ffmpeg-darwin-arm64.gz" in result.stderr
    assert not (tmp_path / "bin" / "ffmpeg").exists()


def response(status, body=None):
    return SimpleNamespace(status_code=status, json=lambda: body or {})


RELEASE = {"tag_name": "v9.1.0", "html_url": "https://github.com/kerthmfy/synclassic/releases/tag/v9.1.0",
           "body": "New things", "assets": [{"name": "Synclassic-9.1.0.dmg",
                                             "browser_download_url": "https://example.invalid/Synclassic-9.1.0.dmg"}]}


def test_versions_compare_like_numbers():
    assert app_update.is_newer("v0.10.0", "0.9.9") and not app_update.is_newer("0.2.0", "0.2.0")
    assert app_update.parse_version("v1.2.3-beta") == (1, 2, 3) and app_update.parse_version("") == (0,)


def test_latest_release_and_the_daily_check(monkeypatch):
    release = app_update.latest(lambda url: response(200, RELEASE))
    assert release.version == "9.1.0" and release.download.endswith(".dmg") and release.notes == "New things"
    assert app_update.latest(lambda url: response(404)) is None  # no releases yet
    with pytest.raises(app_update.UpdateError):
        app_update.latest(lambda url: response(500))

    assert app_update.due(now=1_000_000)
    assert app_update.check_now(lambda url: response(200, RELEASE), now=1_000_000).version == "9.1.0"
    assert not app_update.due(now=1_000_000 + 3600) and app_update.due(now=1_000_000 + 25 * 3600)
    app_update.skip("9.1.0")
    assert app_update.check_now(lambda url: response(200, RELEASE)) is None  # you skipped this one
    old = {**RELEASE, "tag_name": "v0.0.1"}
    assert app_update.check_now(lambda url: response(200, old)) is None
    assert "git pull" in app_update.how_to_get(release)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert "drag it to Applications" in app_update.how_to_get(release)


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_setup_assistant_only_the_first_time(app, tmp_path):
    from synclassic.gui import theme
    from synclassic.gui.setup_assistant import SetupAssistant, should_show
    from synclassic.library import Library
    from synclassic.sources import SourceSpec

    library = Library(config.app_dir() / "library.db")
    assert should_show(library)
    dialog = SetupAssistant()
    for _ in range(3):
        dialog.next.click()
    assert dialog.next.text() == "Start Using Synclassic"
    dialog.folder.setText(str(tmp_path / "Music"))
    dialog.format.setCurrentIndex(1)
    dialog.appearance.setCurrentIndex(dialog.appearance.findData("dark"))
    dialog.updates.setChecked(False)
    dialog.next.click()
    settings = config.load_settings()
    assert (settings.music_dir, settings.format, settings.appearance, settings.check_updates, settings.setup_done) == (
        str(tmp_path / "Music"), "mp3", "dark", False, True)
    assert not should_show(library)
    theme.apply(app, "aqua")

    # Someone who already has playlists never sees it.
    config.save_settings(config.Settings())
    library.add_playlist("Mine", SourceSpec("m3u", "/x.m3u8"))
    assert not should_show(library) and config.load_settings().setup_done


def test_check_for_updates_from_the_window(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from synclassic.gui.main_window import MainWindow

    monkeypatch.setattr(app_update, "latest", lambda get=None: app_update.Release("9.1.0", "page", "dmg", "notes"))
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()) or 0)
    window = MainWindow()
    try:
        window.check_for_updates(quiet=False)
        import time

        deadline = time.monotonic() + 10
        while not shown and time.monotonic() < deadline:
            app.processEvents()
        assert shown and "Synclassic 9.1.0 is out" in shown[0]
    finally:
        window.close()
