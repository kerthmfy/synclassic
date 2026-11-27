"""Songs already on the iPod: shown, optionally not downloaded; using your own file."""

import pathlib

from synclassic import config, update
from synclassic.ipod import sync
from synclassic.library import Library
from synclassic.models import SourcePlaylist, Track
from synclassic.sources import SourceSpec
from tests.conftest import make_audio
from tests.test_ipod_sync import ipod, library_with, read_db, song  # noqa: F401


def test_library_songs_on_the_ipod_are_found(tmp_path, ipod):
    from synclassic.ipod import itunesdb

    existing = itunesdb.songs(read_db(ipod))[0]
    library = library_with(tmp_path, {"Mix": [song(1), Track(existing.title, [existing.artist], duration_ms=existing.length_ms,
                                                              spotify_id="e" * 22)]})
    keys = {i.track.title: i.key for i in library.tracks_in_playlists()}
    assert sync.library_keys_on_ipod(library, ipod) == {keys[existing.title]}  # matched by name and length
    sync.apply_plan(library, sync.make_plan(library, ipod), lambda s: None)
    assert sync.library_keys_on_ipod(library, ipod) == set(keys.values())  # and the synced one by id


def test_songs_on_the_ipod_are_not_downloaded_when_asked(tmp_path, ipod, monkeypatch):
    from synclassic.ipod import itunesdb

    existing = itunesdb.songs(read_db(ipod))[0]
    library = Library(config.app_dir() / "library.db")
    playlist = library.add_playlist("Mix", SourceSpec("spotify", "https://open.spotify.com/playlist/" + "8" * 22))
    update.apply_source(library, playlist, SourcePlaylist("Mix", [
        Track(existing.title, [existing.artist], duration_ms=existing.length_ms, spotify_id="e" * 22)]))
    settings = config.load_settings()
    assert update.keys_on_connected_ipod(library, settings) is None  # off by default
    settings.skip_on_ipod = True
    monkeypatch.setenv("SYNCLASSIC_IPOD", str(ipod.root))
    skip = update.keys_on_connected_ipod(library, settings)
    monkeypatch.setattr(update, "process_track", lambda *a, **k: (_ for _ in ()).throw(AssertionError("downloaded")))
    report = update.download_missing(library, settings, lambda s: None, skip_keys=skip)
    assert report.on_ipod == 1 and report.downloaded == 0
    plan = sync.make_plan(library, ipod)
    assert plan.copy == [] and len(plan.playlists[0].items) == 1  # synced from the iPod's copy


def test_use_your_own_file(tmp_path):
    library = Library(config.app_dir() / "library.db")
    playlist = library.add_playlist("Mix", SourceSpec("spotify", "https://open.spotify.com/playlist/" + "9" * 22))
    update.apply_source(library, playlist, SourcePlaylist("Mix", [
        Track("Theme (Instrumental)", ["Composer"], album="Score", duration_ms=1000, spotify_id="i" * 22)]))
    item = library.tracks_in_playlists()[0]
    library.update_track(item.id, status="unmatched")
    mine = make_audio(tmp_path / "my rip.m4a", seconds=1)
    saved = update.use_own_file(library, library.track(item.id), mine, config.load_settings())
    item = library.track(item.id)
    assert (item.status, item.manual, item.file_path) == ("downloaded", True, str(saved))
    assert saved.is_file() and mine.is_file()  # copied, not moved
    from mutagen.mp4 import MP4

    assert MP4(saved).tags["\xa9nam"] == ["Theme (Instrumental)"]
    assert pathlib.Path(saved).parent.name == "Score"
