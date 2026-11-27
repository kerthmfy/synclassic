"""The play queue and Mac-play scrobbling (with a stand-in for the audio device)."""

import os
import pathlib

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtMultimedia")

from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402

from synclassic import scrobble  # noqa: E402
from synclassic.gui.player import Player, PlayItem, scrobble_threshold_ms  # noqa: E402


class FakeBackend:
    def __init__(self):
        self.source = None
        self.state = QMediaPlayer.PlaybackState.StoppedState
        self.pos = 0
        self.length = 200_000

    def setSource(self, url):  # noqa: N802
        self.source = url.toLocalFile()
        self.pos = 0

    def play(self):
        self.state = QMediaPlayer.PlaybackState.PlayingState

    def pause(self):
        self.state = QMediaPlayer.PlaybackState.PausedState

    def stop(self):
        self.state = QMediaPlayer.PlaybackState.StoppedState

    def playbackState(self):  # noqa: N802
        return self.state

    def position(self):
        return self.pos

    def setPosition(self, ms):  # noqa: N802
        self.pos = ms

    def duration(self):
        return self.length


def items(n=4, length=200_000):
    return [PlayItem(pathlib.Path(f"/music/{i}.m4a"), f"Song {i}", "Artist", "Album", "Artist", length, ("lib", i))
            for i in range(n)]


@pytest.fixture
def player():
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    return Player(backend=FakeBackend())


def test_queue_next_previous_and_repeat(player):
    changed = []
    player.track_changed.connect(lambda item: changed.append(item.title if item else None))
    player.play_list(items(), 1)
    assert player.current.title == "Song 1" and player.is_playing
    player.next()
    player.next()
    assert player.current.title == "Song 3"
    player.next()  # end of the list, no repeat
    assert player.current is None and changed[-1] is None
    player.play_list(items(), 0)
    player.backend.pos = 10_000
    player.previous()  # more than 3 s in: restart the song
    assert player.current.title == "Song 0" and player.backend.pos == 0
    player.cycle_repeat()  # all
    player.play_list(items(), 3)
    player.next()
    assert player.current.title == "Song 0"
    player.cycle_repeat()  # one
    player._status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert player.current.title == "Song 0"
    player.toggle()
    assert not player.is_playing


def test_shuffle_keeps_the_chosen_song_first(player):
    player.shuffle = True
    player.play_list(items(10), 6)
    assert player.current.title == "Song 6" and sorted(player.order) == list(range(10))
    player.set_shuffle(False)
    assert player.current.title == "Song 6" and player.order == list(range(10))


def test_a_play_counts_after_half_the_song(player):
    assert scrobble_threshold_ms(200_000) == 100_000
    assert scrobble_threshold_ms(600_000) == 240_000
    assert scrobble_threshold_ms(20_000) is None
    player.play_list(items(1), 0)
    for ms in range(1000, 99_001, 1000):
        player._position(ms)
    assert scrobble.waiting() == 0
    player._position(100_000)
    player._position(101_000)
    queue = scrobble.load_queue()
    assert len(queue) == 1 and queue[0]["track"] == "Song 0" and queue[0]["duration"] == 200


def test_skipping_ahead_and_short_songs_dont_count(player):
    player.play_list(items(1), 0)
    player.seek(150_000)
    player._position(150_000)
    player._position(151_000)
    assert scrobble.waiting() == 0  # jumping ahead isn't listening
    short = items(1, length=20_000)
    player.backend.length = 20_000
    player.play_list(short, 0)
    for ms in range(1000, 20_001, 1000):
        player._position(ms)
    assert scrobble.waiting() == 0
