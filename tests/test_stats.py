"""Listening stats from the play history and the iPod's play counts."""

import time
from types import SimpleNamespace

from synclassic import scrobble, stats


def test_history_records_app_and_ipod_plays():
    now = time.time()
    scrobble.add_play("Daft Punk", "One More Time", "Discovery", "Daft Punk", 320, int(now - 3600))
    scrobble.add_play("Daft Punk", "Digital Love", "Discovery", "Daft Punk", 301, int(now - 7200))
    scrobble.add_play("M83", "Midnight City", "Hurry Up", "M83", 243, int(now - 40 * 86400))  # too old for 30 days
    week = stats.from_history(7)
    assert week.plays == 2 and week.artists == [("Daft Punk", 2)] and week.albums == [("Discovery", "Daft Punk", 2)]
    assert week.seconds == 621
    assert stats.from_history(60).plays == 3


def test_ipod_all_time():
    songs = [SimpleNamespace(title="A", artist="X", album="One", album_artist="X", genre="Pop", length_ms=200_000,
                             plays=5, kind="song"),
             SimpleNamespace(title="B", artist="Y", album="Two", album_artist="Y", genre="Rock", length_ms=100_000,
                             plays=9, kind="song"),
             SimpleNamespace(title="Show", artist="Pod", album="Pod", album_artist="", genre="", length_ms=1,
                             plays=50, kind="podcast")]
    data = stats.from_ipod(songs)
    assert data.songs == [("B", "Y", 9), ("A", "X", 5)] and data.plays == 14  # podcasts left out
    assert data.genres == [("Rock", 9), ("Pop", 5)] and data.seconds == 9 * 100 + 5 * 200
