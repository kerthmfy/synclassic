"""Genius-style playlists from Last.fm's similar tracks."""

import pytest

from synclassic import config, update
from synclassic.library import Library
from synclassic.sources import genius


class FakeLastfm:
    def __init__(self, items):
        self.items, self.calls = items, []

    def call(self, method, params, post=False, signed=True):
        self.calls.append((method, params, signed))
        return {"similartracks": {"track": self.items}}


def item(name, artist, duration=200):
    return {"name": name, "artist": {"name": artist}, "duration": duration, "match": 0.5}


def test_build_varies_artists_and_starts_with_the_seed():
    items = [item(f"Song {n}", "Same Band") for n in range(5)] + [item(f"Other {n}", f"Band {n}") for n in range(30)]
    client = FakeLastfm(items)
    playlist = genius.build("Daft Punk", "One More Time", client)
    assert playlist.name == "Genius: One More Time" and len(playlist.tracks) == genius.SIZE
    assert (playlist.tracks[0].title, playlist.tracks[0].artist) == ("One More Time", "Daft Punk")
    assert sum(1 for t in playlist.tracks if t.artist == "Same Band") == genius.PER_ARTIST
    assert client.calls[0][0] == "track.getSimilar" and client.calls[0][2] is False  # no signature needed


def test_unknown_song():
    with pytest.raises(genius.GeniusError, match="doesn't know enough"):
        genius.build("Nobody", "Nothing", FakeLastfm([]))


def test_make_and_refresh(monkeypatch):
    library = Library(config.app_dir() / "library.db")
    client = FakeLastfm([item("Digital Love", "Daft Punk"), item("Midnight City", "M83")])
    playlist = genius.make(library, "Daft Punk", "One More Time", client)
    assert playlist.spec.kind == genius.KIND and len(library.playlist_track_ids(playlist)) == 3
    assert playlist.spec.describe().startswith("Genius: songs like Daft Punk - One More Time")
    real = genius.similar
    monkeypatch.setattr(genius, "similar", lambda artist, title, client=None, limit=60: real(
        artist, title, FakeLastfm([item("Nightcall", "Kavinsky")])))
    report = update.refresh_playlist(library, playlist, None)
    assert report.total == 2 and report.added == ["Kavinsky - Nightcall"]
