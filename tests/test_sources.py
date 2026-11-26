import pathlib

import pytest

from synclassic.sources import SourceError, SourceSpec, read_source, specs_from_input
from synclassic.sources.csv_import import parse_csv
from synclassic.sources.m3u import parse_m3u
from synclassic.sources.spotify_links import is_liked_songs, parse_spotify_ref
from synclassic.sources.spotify_public import entity_to_playlist, parse_embed_html


@pytest.mark.parametrize(
    "text,kind,id_",
    [
        ("https://open.spotify.com/playlist/37i9dQZF1DX4o1oenSJRJd", "playlist", "37i9dQZF1DX4o1oenSJRJd"),
        ("https://open.spotify.com/playlist/37i9dQZF1DX4o1oenSJRJd?si=abc123", "playlist", "37i9dQZF1DX4o1oenSJRJd"),
        ("https://open.spotify.com/intl-es/album/2noRn2Aes5aoNVsU6iWThc", "album", "2noRn2Aes5aoNVsU6iWThc"),
        ("https://open.spotify.com/intl-pt-br/track/4HCOHRCSQelz2GPX8iTamO", "track", "4HCOHRCSQelz2GPX8iTamO"),
        ("https://open.spotify.com/embed/playlist/37i9dQZF1DX4o1oenSJRJd", "playlist", "37i9dQZF1DX4o1oenSJRJd"),
        ("spotify:track:4HCOHRCSQelz2GPX8iTamO", "track", "4HCOHRCSQelz2GPX8iTamO"),
        ("spotify:user:someone:playlist:37i9dQZF1DX4o1oenSJRJd", "playlist", "37i9dQZF1DX4o1oenSJRJd"),
    ],
)
def test_spotify_links(text, kind, id_):
    ref = parse_spotify_ref(text)
    assert ref and ref.kind == kind and ref.id == id_


def test_not_spotify_links():
    assert parse_spotify_ref("https://example.com/playlist/37i9dQZF1DX4o1oenSJRJd") is None
    assert parse_spotify_ref("hello") is None


def test_liked_songs_words():
    assert is_liked_songs("liked")
    assert is_liked_songs("Liked Songs")
    assert is_liked_songs("https://open.spotify.com/collection/tracks")
    assert not is_liked_songs("https://open.spotify.com/playlist/37i9dQZF1DX4o1oenSJRJd")


def test_public_playlist(embed_entity):
    playlist = entity_to_playlist(embed_entity("embed_playlist.json"))
    assert playlist.name == "Today’s Top Hits"
    assert not playlist.truncated
    first = playlist.tracks[0]
    assert first.artists[:3] == ["Justice", "Tame Impala", "SZA"]
    assert first.spotify_id == "4HCOHRCSQelz2GPX8iTamO"
    assert first.duration_ms == 224877
    assert first.album == ""  # the embed doesn't include albums
    assert [t.title for t in playlist.tracks[1:]] == ["Housewife", "Stop The Wedding!"]


def test_public_playlist_truncated_at_100(embed_entity):
    entity = embed_entity("embed_playlist.json")
    entity["trackList"] = entity["trackList"][:1] * 100
    playlist = entity_to_playlist(entity)
    assert playlist.truncated and "100" in playlist.note


def test_public_album(embed_entity):
    album = entity_to_playlist(embed_entity("embed_album.json"))
    assert album.name == "Discovery"
    assert [t.track_number for t in album.tracks] == [1, 2, 3]
    assert all(t.album == "Discovery" and t.album_artist == "Daft Punk" for t in album.tracks)
    assert album.tracks[0].cover_url  # album art comes from the page; the release date doesn't


def test_embed_html_parsing(embed_entity):
    import json

    entity = embed_entity("embed_album.json")
    html = (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": {"state": {"data": {"entity": entity}}}}})
        + "</script></html>"
    )
    assert parse_embed_html(html)["name"] == "Discovery"


def test_m3u_mixed_entries(tmp_path):
    music = tmp_path / "songs"
    music.mkdir()
    (music / "Local Song.mp3").write_bytes(b"fake")
    playlist_file = tmp_path / "Road Trip.m3u8"
    playlist_file.write_text(
        "\n".join(
            [
                "#EXTM3U",
                "#PLAYLIST:Road Trip",
                "#EXTINF:215,Daft Punk - One More Time",
                "songs/Local Song.mp3",
                "#EXTINF:240,Gorillaz - Feel Good Inc.",
                "/does/not/exist/Feel Good Inc.m4a",
                "#EXTINF:200,Tame Impala - The Less I Know the Better",
                "https://open.spotify.com/track/4HCOHRCSQelz2GPX8iTamO",
                "Radiohead - Karma Police",
                "",
            ]
        ),
        encoding="utf-8",
    )
    playlist = parse_m3u(playlist_file)
    assert playlist.name == "Road Trip"
    local, missing, spotify, plain = playlist.tracks
    assert local.local_path == str((music / "Local Song.mp3").resolve())
    assert (local.artists, local.title, local.duration_ms) == (["Daft Punk"], "One More Time", 215000)
    assert missing.local_path is None and missing.title == "Feel Good Inc."
    assert spotify.spotify_id == "4HCOHRCSQelz2GPX8iTamO"
    assert (plain.artists, plain.title) == (["Radiohead"], "Karma Police")


def test_csv_exportify(tmp_path):
    path = tmp_path / "Chill.csv"
    path.write_text(
        "Track URI,Track Name,Artist Name(s),Album Name,Album Artist Name(s),Album Release Date,Disc Number,"
        "Track Number,Track Duration (ms),Explicit,ISRC\n"
        'spotify:track:4HCOHRCSQelz2GPX8iTamO,Song A,"Artist 1,Artist 2",Album A,Artist 1,2020-01-01,1,3,200000,true,USABC1\n'
        "spotify:track:0000000000000000000000,Song B,Artist 3,Album B,Artist 3,2019,1,1,180000,false,\n",
        encoding="utf-8",
    )
    (playlist,) = parse_csv(path)
    assert playlist.name == "Chill"
    a, b = playlist.tracks
    assert a.spotify_id == "4HCOHRCSQelz2GPX8iTamO" and a.artists == ["Artist 1", "Artist 2"]
    assert (a.album, a.track_number, a.duration_ms, a.explicit, a.isrc) == ("Album A", 3, 200000, True, "USABC1")
    assert b.explicit is False


def test_csv_tunemymusic_several_playlists(tmp_path):
    path = tmp_path / "export.csv"
    path.write_text(
        "Track name,Artist name,Album,Playlist name,Type,ISRC,Spotify - id\n"
        "Song A,Artist 1,Album A,Gym,Playlist,,4HCOHRCSQelz2GPX8iTamO\n"
        "Song B,Artist 2,Album B,Sleep,Playlist,,\n"
        "Song C,Artist 3,Album C,Gym,Playlist,,\n",
        encoding="utf-8",
    )
    playlists = parse_csv(path)
    assert [(p.name, [t.title for t in p.tracks]) for p in playlists] == [("Gym", ["Song A", "Song C"]), ("Sleep", ["Song B"])]
    specs = specs_from_input(str(path))
    assert [s.selector for s in specs] == ["Gym", "Sleep"]
    assert read_source(specs[1], None).tracks[0].title == "Song B"


def test_specs_from_input_errors(tmp_path):
    with pytest.raises(SourceError):
        specs_from_input("https://example.com/whatever")
    with pytest.raises(SourceError):
        specs_from_input(str(tmp_path / "missing.m3u8"))
    other = tmp_path / "notes.txt"
    other.write_text("x")
    with pytest.raises(SourceError):
        specs_from_input(str(other))


def test_liked_songs_needs_login():
    with pytest.raises(SourceError, match="login"):
        read_source(SourceSpec("liked", "liked"), None)
