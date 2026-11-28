"""Karaoke lyrics and the visualizer."""

import os
import pathlib
import subprocess
import time
from types import SimpleNamespace

import pytest

from synclassic import lyrics, spectrum, synced_lyrics
from tests.conftest import make_audio

LRC = "[ti:Song]\n[offset:+100]\n[00:01.00]First line\n[00:05.50][01:05.50]Chorus\n[00:09.123]Last\nno time here\n"


def test_lrc_lines_times_and_offset():
    lines = synced_lyrics.parse_lrc(LRC)
    assert lines == [(900, "First line"), (5400, "Chorus"), (9023, "Last"), (65400, "Chorus")]
    lyric = synced_lyrics.Lyrics(lines)
    assert [lyric.line_at(ms) for ms in (0, 900, 5000, 9000, 9100, 70_000)] == [-1, 0, 0, 1, 2, 3]
    assert synced_lyrics.plain_of(LRC) == "First line\nChorus\nLast\nno time here"


def test_where_lyrics_come_from(tmp_path):
    song = make_audio(tmp_path / "song.m4a")
    calls = []

    class Finder:
        def __init__(self, found=None, offline=False):
            self.found, self.offline = found, offline

        def find(self, artist, title, album="", duration_s=0):
            calls.append(title)
            if self.offline:
                raise OSError("offline")
            return self.found

    # LRCLIB's timed lyrics, then remembered: the second time nothing is asked.
    found = lyrics.Found("First line\nChorus", synced=LRC)
    result = synced_lyrics.find(song, "Artist", "Song", finder=Finder(found))
    assert result.timed and result.source == "LRCLIB" and calls == ["Song"]
    again = synced_lyrics.find(song, "Artist", "Song", finder=Finder(offline=True))
    assert again.source == "cache" and again.lines == result.lines and calls == ["Song"]

    # Nothing timed: plain lyrics from LRCLIB, and LRCLIB isn't asked again for a month.
    assert synced_lyrics.find(song, "Artist", "Other", finder=Finder(lyrics.Found("Just words"))).plain == "Just words"
    assert synced_lyrics.find(song, "Artist", "Other", finder=Finder(lyrics.Found("x"))) is None
    assert calls == ["Song", "Other"]

    # Offline: the lyrics inside the file; timed ones inside the file win over everything.
    lyrics.write(song, "Words in the file")
    plain = synced_lyrics.find(song, "Artist", "Third", finder=Finder(offline=True))
    assert plain.plain == "Words in the file" and not plain.timed and plain.source == "file"
    lyrics.write(song, LRC)
    assert synced_lyrics.find(song, "Artist", "Song", finder=Finder(offline=True)).source == "file"


def test_fetching_lyrics_while_downloading_keeps_the_timed_ones(tmp_path):
    song = make_audio(tmp_path / "song.m4a")
    finder = SimpleNamespace(find=lambda *a: lyrics.Found("First line", synced=LRC))
    assert lyrics.fetch_and_write(song, "Artist", "Song", "", 10, finder) == "found"
    assert synced_lyrics.cached("Artist", "Song")[0] == LRC


def test_spectrum_puts_low_and_high_sounds_in_the_right_bands(tmp_path):
    path = tmp_path / "two.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=100:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=6000:duration=1", "-filter_complex", "[0][1]concat=n=2:v=0:a=1",
                    "-c:a", "aac", str(path)], check=True)
    result = spectrum.analyse(path)
    assert result.fps == 30 and result.bands == 32 and 55 <= result.frames <= 62
    low, high = result.at(500), result.at(1500)
    assert max(range(32), key=lambda b: low[b]) < 8 and max(range(32), key=lambda b: high[b]) > 24
    assert result.at(-50) == result.at(0) and len(result.at(10**9)) == 32
    with pytest.raises(spectrum.SpectrumError):
        spectrum.decode(tmp_path / "missing.m4a")


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_lyrics_follow_the_song_and_a_click_jumps_there(app):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    from synclassic.gui.now_playing import NowPlayingPanel

    panel = NowPlayingPanel()
    panel.resize(600, 400)
    panel.lyrics_view.set_lyrics(synced_lyrics.Lyrics(synced_lyrics.parse_lrc(LRC), "", "LRCLIB"))
    panel.show_mode("lyrics")
    panel.set_position(6000)
    assert panel.lyrics_view.current() == 1
    panel.set_playing(True)
    time.sleep(0.05)
    assert panel.clock.now() >= 6040  # keeps counting between the player's updates
    panel.set_playing(False)
    jumps = []
    panel.seek_requested.connect(jumps.append)
    view = panel.lyrics_view
    view._ensure_layout()
    y = view.TOP + view._layout[2][0] - view.offset + 5
    view.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(300, y), QPointF(300, y),
                                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert jumps == [9023]
    for _ in range(5):
        view.tick()
    view.grab()  # paints without trouble
    panel.visualizer.set_spectrum(spectrum.Spectrum(30, 4, bytes([255, 0, 128, 64]) * 30))
    panel.set_playing(True)
    panel.visualizer.tick()
    assert panel.visualizer.shown[0] > panel.visualizer.shown[1]
    panel.visualizer.grab()
    panel.stop()


def test_lyrics_and_visualizer_from_the_window(app, monkeypatch, tmp_path):
    from synclassic.gui.main_window import MainWindow
    from synclassic.gui.player import PlayItem

    song = make_audio(tmp_path / "song.m4a", seconds=2)
    lyrics.write(song, LRC)
    window = MainWindow()
    try:
        item = PlayItem(path=song, title="Song", artist="Artist", length_ms=2000)
        monkeypatch.setattr(type(window.player), "current", property(lambda self: item))
        window.karaoke_action.trigger()
        assert window.content.currentWidget() is window.now_playing and window.karaoke_button.isChecked()
        deadline = time.monotonic() + 10
        while window.now_playing.lyrics_view.lyrics is None and time.monotonic() < deadline:
            app.processEvents()
        assert window.now_playing.lyrics_view.lyrics.timed
        window.visualizer_action.trigger()
        assert window.now_playing.mode == "visualizer" and not window.karaoke_action.isChecked()
        while window.now_playing.visualizer.spectrum is None and time.monotonic() < deadline:
            app.processEvents()
        assert window.now_playing.visualizer.spectrum is not None
        window.visualizer_action.trigger()  # again: back to the list
        assert window.content.currentWidget() is window.stack and not window.visualizer_button.isChecked()
    finally:
        window.close()
