"""Choosing between the 100-song reader and the full-playlist reader."""

from synclassic.models import SourcePlaylist, Track
from synclassic.sources import SourceSpec, read_source, spotify_page, spotify_public
from synclassic.sources.spotify_page import PageInfo

LINK = "https://open.spotify.com/playlist/37i9dQZF1DX4o1oenSJRJd"


def songs(n, album=""):
    return [Track(f"Song {i}", ["Artist"], album=album, spotify_id=f"{i:022d}") for i in range(n)]


def setup(monkeypatch, embed_count, page_total, page_tracks=()):
    embed = SourcePlaylist("Big One", songs(embed_count), truncated=embed_count >= 100)
    monkeypatch.setattr(spotify_public, "read_public", lambda ref: embed)
    monkeypatch.setattr(spotify_page, "fetch_page_info", lambda ref: PageInfo("Big One", page_total, list(page_tracks)))


def test_big_playlist_uses_full_reader(monkeypatch):
    setup(monkeypatch, 100, 250)
    calls = []

    def reader(ref, total, name):
        calls.append((ref.id, total, name))
        return SourcePlaylist("ignored", songs(250))

    result = read_source(SourceSpec("spotify", LINK), None, reader)
    assert calls == [("37i9dQZF1DX4o1oenSJRJd", 250, "Big One")]
    assert len(result.tracks) == 250 and result.name == "Big One"


def test_small_playlist_skips_full_reader_and_gets_album_names(monkeypatch):
    rich = [Track("Song 0", ["Artist"], album="The Album", spotify_id=f"{0:022d}", cover_url="https://i.scdn.co/x", track_number=4)]
    setup(monkeypatch, 40, 40, rich)
    result = read_source(SourceSpec("spotify", LINK), None, lambda *a: (_ for _ in ()).throw(AssertionError("not needed")))
    assert len(result.tracks) == 40 and not result.truncated and result.note == ""
    assert (result.tracks[0].album, result.tracks[0].track_number) == ("The Album", 4)


def test_full_reader_failure_keeps_100_with_a_clear_note(monkeypatch):
    setup(monkeypatch, 100, 250)
    result = read_source(SourceSpec("spotify", LINK), None, lambda *a: None)
    assert len(result.tracks) == 100 and result.truncated
    assert "100 of 250" in result.note


def test_without_full_reader_the_note_explains_how_to_get_everything(monkeypatch):
    setup(monkeypatch, 100, 250)
    result = read_source(SourceSpec("spotify", LINK), None)
    assert "100 of 250" in result.note and "full-playlist reader" in result.note
