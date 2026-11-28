"""Plain-word errors, and Report a Problem without secrets."""

import errno
import os
import sqlite3
import time

import pytest
import requests

from synclassic import cli, config, diagnostics, errors, lastfm
from synclassic.sources import spotify_auth


@pytest.mark.parametrize("error, words", [
    (OSError(errno.ENOSPC, "No space", "/Volumes/Kerth's iPod/iPod_Control/Music/F01/AB.m4a"),
     "no space left on “Kerth's iPod” (the iPod or drive)"),
    (OSError(errno.EROFS, "Read-only", "/Volumes/IPOD/x"), "can only be read"),
    (PermissionError(errno.EACCES, "Denied", "/Volumes/IPOD/x"), "didn't let Synclassic change"),
    (OSError(errno.EIO, "I/O error", "/Volumes/IPOD/x"), "stopped responding"),
    (FileNotFoundError(errno.ENOENT, "No such file", "/Volumes/IPOD/iPod_Control/iTunes/iTunesDB"),
     "Was the iPod unplugged?"),
    (FileNotFoundError(errno.ENOENT, "No such file", "ffmpeg"), "brew install ffmpeg deno"),
    (sqlite3.OperationalError("database is locked"), "library is busy"),
    (RuntimeError("Something specific"), "Something specific"),
])
def test_errors_in_plain_words(error, words):
    assert words in errors.friendly(error)


def test_network_errors_name_the_site():
    error = requests.ConnectionError("boom", request=requests.Request("GET", "https://lrclib.net/api/get").prepare())
    assert errors.friendly(error).startswith("Couldn't reach lrclib.net")
    timeout = requests.Timeout("slow", request=requests.Request("GET", "https://api.spotify.com/v1/me").prepare())
    assert "api.spotify.com took too long" in errors.friendly(timeout)


def test_the_report_has_what_helps_and_no_secrets(monkeypatch):
    spotify_auth.save_token({"access_token": "BQ-SECRET-TOKEN", "refresh_token": "REFRESH-SECRET",
                             "expires_at": time.time() + 3600, "client_id": "CLIENTSECRET",
                             "scope": "playlist-read-private playlist-modify-private"})
    lastfm.save_credentials({"session_key": "LASTFM-SESSION-SECRET", "user": "kerth"})
    settings = config.Settings(spotify_client_id="CLIENTSECRET")
    config.save_settings(settings)
    try:
        raise OSError(errno.ENOSPC, "No space", os.path.expanduser("~/Music/x.m4a"))
    except OSError as error:
        message = errors.remember(error)
    assert "no space left on ~/Music/x.m4a" in message
    activity = "Logged in with token=ABC123SECRET for someone@example.com\nCopying song 3"
    report = diagnostics.collect("Sync stopped at song 12", activity)
    for secret in ("BQ-SECRET-TOKEN", "REFRESH-SECRET", "CLIENTSECRET", "LASTFM-SESSION-SECRET", "ABC123SECRET",
                   "someone@example.com", "kerth"):
        assert secret not in report, secret
    for part in ("== What happened ==", "Sync stopped at song 12", "== Versions ==", "Synclassic ", "== Tools ==",
                 "spotify_client_id = set", "Spotify: logged in", "Last.fm: connected", "No iPod connected",
                 "== Last error ==", "no space left", "Copying song 3"):
        assert part in report, part
    assert str(os.path.expanduser("~")) not in report or os.path.expanduser("~") == "/"
    link = diagnostics.issue_link("It crashed\nmore")
    assert link.startswith("https://github.com/kerthmfy/synclassic/issues/new?title=It+crashed")


def test_terminal_report_and_errors(tmp_path, capsys, monkeypatch):
    out = tmp_path / "report.txt"
    assert cli.main(["report", "--out", str(out), "nothing", "syncs"]) == 0
    assert "nothing syncs" in out.read_text() and "Saved the report" in capsys.readouterr().out

    def broken(args):
        raise OSError(errno.EIO, "I/O error", "/Volumes/IPOD/x")

    monkeypatch.setattr(cli, "cmd_report", broken)
    assert cli.main(["report"]) == 1
    printed = capsys.readouterr().out
    assert "stopped responding" in printed and "synclassic report --out" in printed
    assert "stopped responding" in errors.last()["message"]


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_report_a_problem_window(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from synclassic.gui.main_window import MainWindow
    from synclassic.gui.report_dialog import ReportDialog

    window = MainWindow()
    try:
        window._job_failed("The disk stopped responding.")  # doesn't block the window
        boxes = [w for w in app.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()]
        assert boxes and boxes[-1].text() == "The disk stopped responding."
        assert any(b.text() == "Report a Problem…" for b in boxes[-1].buttons())
        boxes[-1].close()
        dialog = ReportDialog("Copying song 3\ntoken=SECRET1", window)
        deadline = time.monotonic() + 15
        while "== Versions ==" not in dialog.report.toPlainText() and time.monotonic() < deadline:
            app.processEvents()
        dialog.description.setPlainText("It stopped")
        text = dialog._text()
        assert "It stopped" in text and "Copying song 3" in text and "SECRET1" not in text
        dialog.copy()
        assert app.clipboard().text() == text
        dialog.reject()
    finally:
        window.close()


def test_terminal_typing_is_put_back_to_normal_before_a_question(monkeypatch):
    import pty
    import termios

    leader, follower = pty.openpty()
    stream = os.fdopen(follower, "r")
    attrs = termios.tcgetattr(follower)
    attrs[0] &= ~termios.ICRNL  # what the browser left behind: Return shows as ^M
    attrs[3] &= ~(termios.ICANON | termios.ECHO)
    termios.tcsetattr(follower, termios.TCSANOW, attrs)
    monkeypatch.setattr("sys.stdin", stream)
    monkeypatch.setattr("builtins.input", lambda question: "y\r")
    assert cli.ask("Sync now? [y/N] ") == "y"
    fixed = termios.tcgetattr(follower)
    assert fixed[0] & termios.ICRNL and fixed[3] & termios.ICANON and fixed[3] & termios.ECHO
    stream.close()
    os.close(leader)
