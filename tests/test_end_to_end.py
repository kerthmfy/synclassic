"""Add -> download -> tag -> M3U8 -> update with changes -> pick by hand.

YouTube is replaced by a fake search and a fake downloader that writes a real (tiny) audio
file with ffmpeg, so everything after the network is exercised for real.
"""

import pathlib

import pytest
from mutagen.mp4 import MP4

from synclassic import config, downloader, playlist_writer, tagging
from synclassic.library import Library
from synclassic.matcher import Matcher
from synclassic.models import SourcePlaylist, Track
from synclassic.picker import pick_interactively
from synclassic.sources import SourceSpec
from synclassic.update import apply_source, download_missing
from tests.conftest import make_audio


def yt(video_id, title, artist, seconds, album=""):
    return {"videoId": video_id, "title": title, "artists": [{"name": artist}], "album": {"name": album} if album else None,
            "duration_seconds": seconds, "resultType": "song",
            "thumbnails": [{"url": f"https://lh3.googleusercontent.com/{video_id}=w60-h60", "width": 60}]}


CATALOG = [
    yt("onemoretime", "One More Time", "Daft Punk", 320, "Discovery"),
    yt("aerodynamic", "Aerodynamic", "Daft Punk", 212, "Discovery"),
    yt("karmapolice", "Karma Police", "Radiohead", 264, "OK Computer"),
    yt("karmalive00", "Karma Police (Live at Glastonbury)", "Radiohead", 290, ""),
    yt("oddcover000", "Strange Song (Piano Cover)", "Someone Else", 100, ""),
]


def search(query, kind):
    words = set(query.lower().split())
    return [r for r in CATALOG if words & set(r["title"].lower().split())]


@pytest.fixture
def fake_youtube(monkeypatch):
    downloads = []

    def fake_download(video_id, destination, settings):
        path = destination.with_suffix(f".{settings.format}")
        path.parent.mkdir(parents=True, exist_ok=True)
        make_audio(path)
        downloads.append(video_id)
        return downloader.DownloadResult(path, "mp4a.40.2", 128.0, converted=False)

    monkeypatch.setattr(downloader, "download", fake_download)
    monkeypatch.setattr(downloader, "check_tools", lambda: [])
    monkeypatch.setattr(tagging, "fetch_cover", lambda url: b"\xff\xd8\xff\xe0fakejpeg" if url else None)
    return downloads


def test_full_flow(tmp_path, fake_youtube):
    settings = config.load_settings()
    library = Library(config.app_dir() / "library.db")
    spec = SourceSpec("spotify", "https://open.spotify.com/playlist/37i9dQZF1DX4o1oenSJRJd")
    playlist = library.add_playlist("My Mix", spec)

    # A no-login link: no album names, which should be filled in from YouTube Music.
    source = SourcePlaylist("My Mix", [
        Track("One More Time", ["Daft Punk"], duration_ms=320_357, spotify_id="0DiWol3AO6WpXZgp0goxAV"),
        Track("Karma Police", ["Radiohead"], duration_ms=264_066, spotify_id="63OQupATfueTdZMWTxW03A"),
        Track("Strange Song", ["Nobody"], duration_ms=200_000, spotify_id="1111111111111111111111"),
    ])
    apply_source(library, playlist, source)
    report = download_missing(library, settings, lambda s: None, matcher=Matcher(search))
    assert (report.downloaded, report.unmatched) == (2, 1)
    assert fake_youtube == ["onemoretime", "karmapolice"]  # the live version was not chosen

    items = library.playlist_tracks(playlist)
    first = items[0]
    assert first.status == "downloaded" and first.has_file
    assert pathlib.Path(first.file_path).relative_to(settings.music_path) == pathlib.Path("Music/Daft Punk/Discovery/One More Time.m4a")
    tags = MP4(first.file_path).tags
    assert tags["\xa9nam"] == ["One More Time"] and tags["\xa9alb"] == ["Discovery"] and tags["aART"] == ["Daft Punk"]
    assert bytes(tags["----:com.synclassic:spotify_id"][0]) == b"0DiWol3AO6WpXZgp0goxAV"
    assert "covr" in tags

    written = playlist_writer.write_all(library, settings.music_path)
    text = written[0].read_text(encoding="utf-8")
    assert text.splitlines()[:3] == ["#EXTM3U", "#PLAYLIST:My Mix", "#SYNCLASSIC"]
    assert "#EXTINF:320,Daft Punk - One More Time\n../Music/Daft Punk/Discovery/One More Time.m4a" in text
    assert "Strange Song" not in text  # not downloaded, so not listed

    # Running again downloads nothing new.
    fake_youtube.clear()
    download_missing(library, settings, lambda s: None, matcher=Matcher(search))
    assert fake_youtube == []

    # The playlist changes on Spotify: one song removed, one added, order changed.
    changed = SourcePlaylist("My Mix", [
        Track("Aerodynamic", ["Daft Punk"], duration_ms=212_546, spotify_id="2222222222222222222222"),
        Track("One More Time", ["Daft Punk"], duration_ms=320_357, spotify_id="0DiWol3AO6WpXZgp0goxAV"),
    ])
    refresh = apply_source(library, playlist, changed)
    assert refresh.added == ["Daft Punk - Aerodynamic"]
    assert sorted(refresh.removed) == ["Nobody - Strange Song", "Radiohead - Karma Police"]
    download_missing(library, settings, lambda s: None, matcher=Matcher(search))
    assert fake_youtube == ["aerodynamic"]
    playlist_writer.write_all(library, settings.music_path)
    lines = [l for l in written[0].read_text(encoding="utf-8").splitlines() if not l.startswith("#")]
    assert [pathlib.Path(l).stem for l in lines] == ["Aerodynamic", "One More Time"]
    assert [o.track.title for o in library.orphan_tracks()] == ["Karma Police", "Strange Song"]


def test_pick_by_hand_with_preview(tmp_path, fake_youtube, monkeypatch):
    settings = config.load_settings()
    library = Library(config.app_dir() / "library.db")
    playlist = library.add_playlist("Rock", SourceSpec("m3u", str(tmp_path / "x.m3u8")))
    apply_source(library, playlist, SourcePlaylist("Rock", [Track("Karma Police", ["Radiohead"], duration_ms=290_000)]))
    item = library.playlist_tracks(playlist)[0]

    previews = []
    monkeypatch.setattr("synclassic.picker.preview", lambda video_id, settings, wait: previews.append(video_id) or "played")
    answers = iter(["?", "p 2", "s karma police live", "2"])
    lines = []
    outcome = pick_interactively(library, item, settings, Matcher(search), ask=lambda prompt: next(answers), say=lines.append)

    assert outcome == "picked"
    assert previews  # listened before choosing
    picked = library.track(item.id)
    assert picked.manual and picked.status == "downloaded" and picked.has_file
    assert picked.video_id == fake_youtube[-1]
    assert any("p 1-8" in line for line in lines)  # help was shown

    # A song you picked by hand is never re-matched automatically.
    fake_youtube.clear()
    download_missing(library, settings, lambda s: None, matcher=Matcher(lambda q, k: []))
    assert fake_youtube == []


def test_pick_by_pasting_a_link(tmp_path, fake_youtube):
    settings = config.load_settings()
    library = Library(config.app_dir() / "library.db")
    playlist = library.add_playlist("X", SourceSpec("m3u", str(tmp_path / "x.m3u8")))
    apply_source(library, playlist, SourcePlaylist("X", [Track("Obscure B-side", ["Band"], duration_ms=100_000)]))
    item = library.playlist_tracks(playlist)[0]
    answers = iter(["u https://music.youtube.com/watch?v=dQw4w9WgXcQ&si=xyz"])
    outcome = pick_interactively(library, item, settings, Matcher(lambda q, k: []), ask=lambda p: next(answers), say=lambda s: None)
    assert outcome == "picked" and fake_youtube == ["dQw4w9WgXcQ"]


def test_local_files_from_m3u_are_not_downloaded(tmp_path, fake_youtube):
    settings = config.load_settings()
    library = Library(config.app_dir() / "library.db")
    song = make_audio(tmp_path / "mine.m4a")
    playlist = library.add_playlist("Local", SourceSpec("m3u", str(tmp_path / "x.m3u8")))
    apply_source(library, playlist, SourcePlaylist("Local", [Track("Mine", ["Me"], local_path=str(song))]))
    download_missing(library, settings, lambda s: None, matcher=Matcher(search))
    assert fake_youtube == []
    text = playlist_writer.write_all(library, settings.music_path)[0].read_text(encoding="utf-8")
    assert text.rstrip().endswith("mine.m4a")


def test_mp3_tags(tmp_path):
    from mutagen.id3 import ID3

    path = make_audio(tmp_path / "x.mp3")
    tagging.tag_file(path, Track("Title", ["A", "B"], album="Album", track_number=4, spotify_id="S" * 22), b"\xff\xd8\xff", "vid")
    tags = ID3(path)
    assert tags.version[:2] == (2, 3)
    assert str(tags["TIT2"]) == "Title" and str(tags["TPE1"]) == "A, B" and str(tags["TRCK"]) == "4"
    assert "APIC:Cover" in tags
