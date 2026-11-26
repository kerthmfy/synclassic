"""The 'one folder per playlist' layout and switching between layouts."""

import os
import pathlib

from synclassic import config
from synclassic.library import Library
from synclassic.matcher import Matcher
from synclassic.models import SourcePlaylist, Track
from synclassic.sources import SourceSpec
from synclassic.update import apply_source, download_missing, finalize
from tests.test_end_to_end import fake_youtube, search  # noqa: F401  (fixture)

ONE = Track("One More Time", ["Daft Punk"], duration_ms=320_000, spotify_id="a" * 22)
KARMA = Track("Karma Police", ["Radiohead"], duration_ms=264_000, spotify_id="b" * 22)
AERO = Track("Aerodynamic", ["Daft Punk"], duration_ms=212_000, spotify_id="c" * 22)


def setup(layout="playlist"):
    settings = config.load_settings()
    settings.folder_layout = layout
    config.save_settings(settings)
    library = Library(config.app_dir() / "library.db")
    gym = library.add_playlist("Gym", SourceSpec("spotify", "https://open.spotify.com/playlist/" + "1" * 22))
    run = library.add_playlist("Run", SourceSpec("spotify", "https://open.spotify.com/playlist/" + "2" * 22))
    apply_source(library, gym, SourcePlaylist("Gym", [ONE, KARMA]))
    apply_source(library, run, SourcePlaylist("Run", [KARMA, AERO]))
    download_missing(library, settings, lambda s: None, matcher=Matcher(search))
    finalize(library, settings)
    return library, settings, gym, run


def files(folder: pathlib.Path):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_playlist_folders_share_songs_without_using_space_twice(fake_youtube):  # noqa: F811
    library, settings, gym, run = setup()
    music = settings.music_path
    assert files(music / "Gym") == ["Daft Punk - One More Time.m4a", "Gym.m3u8", "Radiohead - Karma Police.m4a"]
    assert files(music / "Run") == ["Daft Punk - Aerodynamic.m4a", "Radiohead - Karma Police.m4a", "Run.m3u8"]
    assert os.path.samefile(music / "Gym" / "Radiohead - Karma Police.m4a", music / "Run" / "Radiohead - Karma Police.m4a")
    assert fake_youtube.count("karmapolice") == 1  # downloaded once
    m3u8 = (music / "Run" / "Run.m3u8").read_text(encoding="utf-8")
    assert m3u8.index("Radiohead - Karma Police.m4a") < m3u8.index("Daft Punk - Aerodynamic.m4a")  # Spotify order
    assert not (music / "Music").exists()


def test_song_leaving_a_playlist_leaves_its_folder(fake_youtube):  # noqa: F811
    library, settings, gym, run = setup()
    music = settings.music_path
    apply_source(library, gym, SourcePlaylist("Gym", [ONE]))  # Karma Police removed from Gym
    finalize(library, settings)
    assert files(music / "Gym") == ["Daft Punk - One More Time.m4a", "Gym.m3u8"]
    assert (music / "Run" / "Radiohead - Karma Police.m4a").is_file()  # still in Run
    karma = next(i for i in library.playlist_tracks(run) if i.track.title == "Karma Police")
    assert karma.has_file


def test_renaming_and_removing_playlists(fake_youtube):  # noqa: F811
    library, settings, gym, run = setup()
    music = settings.music_path
    library.rename_playlist(gym, "Leg Day")
    finalize(library, settings)
    assert not (music / "Gym").exists()
    assert files(music / "Leg Day") == ["Daft Punk - One More Time.m4a", "Leg Day.m3u8", "Radiohead - Karma Police.m4a"]
    library.remove_playlist(run)
    finalize(library, settings)
    # Like the artist layout, removing a playlist keeps songs nothing else uses until 'clean'.
    assert files(music / "Run") == ["Daft Punk - Aerodynamic.m4a"]
    assert len([i for i in library.tracks_in_playlists() if i.has_file]) == 2

    from synclassic.cli import main

    assert main(["clean", "--yes"]) == 0
    assert not (music / "Run").exists()
    assert files(music / "Leg Day") == ["Daft Punk - One More Time.m4a", "Leg Day.m3u8", "Radiohead - Karma Police.m4a"]


def test_switching_layouts_moves_files_both_ways(fake_youtube):  # noqa: F811
    library, settings, gym, run = setup()
    music = settings.music_path
    settings.folder_layout = "artist"
    finalize(library, settings)
    assert files(music / "Music") == [
        "Daft Punk/Discovery/Aerodynamic.m4a",  # album names came from YouTube Music
        "Daft Punk/Discovery/One More Time.m4a",
        "Radiohead/OK Computer/Karma Police.m4a",
    ]
    assert not (music / "Gym").exists() and not (music / "Run").exists()
    assert all(i.has_file for i in library.tracks_in_playlists())

    settings.folder_layout = "playlist"
    finalize(library, settings)
    assert not (music / "Music").exists()
    assert "Radiohead - Karma Police.m4a" in files(music / "Gym")
    assert all(i.has_file for i in library.tracks_in_playlists())


def test_repicking_a_song_updates_every_folder(fake_youtube):  # noqa: F811
    from synclassic.matcher import Candidate, MatchResult
    from synclassic.update import save_candidate

    library, settings, gym, run = setup()
    music = settings.music_path
    karma = next(i for i in library.playlist_tracks(gym) if i.track.title == "Karma Police")
    save_candidate(library, karma, MatchResult(Candidate("karmalive00", "Karma Police (Live)", ["Radiohead"]), 1.0, "matched"),
                   settings, lambda s: None, manual=True)
    finalize(library, settings)
    gym_copy, run_copy = music / "Gym" / "Radiohead - Karma Police.m4a", music / "Run" / "Radiohead - Karma Police.m4a"
    assert gym_copy.is_file() and run_copy.is_file() and os.path.samefile(gym_copy, run_copy)
    from mutagen.mp4 import MP4

    for copy in (gym_copy, run_copy):  # the new recording, in every folder
        assert bytes(MP4(copy).tags["----:com.synclassic:youtube_id"][0]) == b"karmalive00"
