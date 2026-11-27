"""Background updates (LaunchAgent), the shared update lock, and sync when the iPod is plugged in."""

import plistlib
from types import SimpleNamespace

from synclassic import background, cli


def test_enable_writes_a_launch_agent_and_disable_removes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    message = background.enable(6, run=run)
    assert "every 6 hours" in message
    agent = plistlib.loads(background.agent_path().read_bytes())
    assert agent["Label"] == background.LABEL and agent["StartInterval"] == 6 * 3600
    assert agent["ProgramArguments"][-3:] == ["synclassic.cli", "update", "--background"]
    assert "/opt/homebrew/bin" in agent["EnvironmentVariables"]["PATH"]
    assert [c[1] for c in calls] == ["bootout", "bootstrap"]
    assert background.current_hours() == 6
    background.disable(run=run)
    assert not background.agent_path().exists() and background.current_hours() is None


def test_only_one_update_at_a_time():
    with background.update_lock() as first:
        with background.update_lock() as second:
            assert first and not second
    with background.update_lock() as again:
        assert again


def test_cli_update_skips_while_another_runs(capsys):
    with background.update_lock():
        assert cli.main(["update"]) == 0
    assert "Another update is running" in capsys.readouterr().out


def test_plugging_in_the_ipod_syncs_when_asked(monkeypatch, tmp_path):
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from synclassic import config
    from synclassic.gui.main_window import MainWindow
    from tests.test_ipod_read import fake_ipod, load

    app = QApplication.instance() or QApplication([])
    settings = config.load_settings()
    settings.sync_on_connect = True
    config.save_settings(settings)
    window = MainWindow()
    synced = []
    monkeypatch.setattr(window, "update_and_sync", lambda: synced.append(True))
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(fake_ipod(tmp_path, load("captured-album-index-36.b64"))))
    window._detect_ipod()
    deadline = time.monotonic() + 5
    while not synced and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)
    assert synced == [True]
    window._detect_ipod()  # still connected: not again
    window.close()


def test_standalone_app_runs_its_own_command_line(monkeypatch):
    import runpy
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Synclassic.app/Contents/MacOS/Synclassic")
    assert background.agent_plist(6)["ProgramArguments"] == [
        "/Applications/Synclassic.app/Contents/MacOS/Synclassic", "--cli", "update", "--background"]
    monkeypatch.setattr(sys, "argv", ["Synclassic", "--cli", "background", "status"])
    entry = runpy.run_path("packaging/synclassic_app.py")
    assert entry["run"]() == 0
