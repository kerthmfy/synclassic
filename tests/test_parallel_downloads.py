"""Several songs downloading at once."""

import threading
import time

from synclassic import config, update
from synclassic.library import Library
from synclassic.models import SourcePlaylist, Track
from synclassic.sources import SourceSpec


def library_of(n):
    library = Library(config.app_dir() / "library.db")
    playlist = library.add_playlist("Mix", SourceSpec("spotify", "https://open.spotify.com/playlist/" + "1" * 22))
    update.apply_source(library, playlist, SourcePlaylist("Mix", [Track(f"Song {i}", ["A"], spotify_id=f"{i:022d}")
                                                                   for i in range(n)]))
    return library


def fake_process(monkeypatch, outcome=lambda item: "downloaded", seconds=0.05):
    running, most, done, threads = [0], [0], [], set()
    lock = threading.Lock()

    def process_track(library, item, settings, matcher, say):
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
            threads.add(threading.get_ident())
        time.sleep(seconds)
        status = outcome(item)
        library.update_track(item.id, status=status)  # each worker writes through its own connection
        with lock:
            running[0] -= 1
            done.append(item.track.title)
        return status

    monkeypatch.setattr(update, "process_track", process_track)
    monkeypatch.setattr(update.downloader, "check_tools", lambda: [])
    return most, done, threads


def test_three_at_a_time(monkeypatch):
    library = library_of(9)
    most, done, threads = fake_process(monkeypatch)
    settings = config.Settings(parallel_downloads=3, download_pause=0)
    seen = []
    report = update.download_missing(library, settings, lambda s: None, matcher=object(),
                                     progress=lambda n, total, item: seen.append(n))
    assert report.downloaded == 9 and sorted(done) == sorted(f"Song {i}" for i in range(9))
    assert most[0] == 3 and len(threads) == 3
    assert sorted(seen[:-1]) == list(range(1, 10)) and seen[-1] == 10  # and the "finished" call last
    assert {i.status for i in library.tracks_in_playlists()} == {"downloaded"}


def test_one_at_a_time_when_set_to_1(monkeypatch):
    library = library_of(4)
    most, done, _ = fake_process(monkeypatch)
    update.download_missing(library, config.Settings(parallel_downloads=1, download_pause=0), lambda s: None, matcher=object())
    assert most[0] == 1 and done == [f"Song {i}" for i in range(4)]  # in order


def test_stops_after_five_failures_in_a_row_and_when_asked(monkeypatch):
    library = library_of(20)
    _, done, _ = fake_process(monkeypatch, outcome=lambda item: "failed", seconds=0.01)
    report = update.download_missing(library, config.Settings(parallel_downloads=2, download_pause=0), lambda s: None,
                                     matcher=object())
    assert "5 failures in a row" in report.stopped_early and len(done) < 10

    library2 = library_of(30)
    _, done2, _ = fake_process(monkeypatch, seconds=0.02)
    report = update.download_missing(library2, config.Settings(parallel_downloads=3, download_pause=0), lambda s: None,
                                     matcher=object(), should_stop=lambda: len(done2) >= 4)
    assert report.stopped_early.startswith("Stopped.") and len(done2) < 10


def test_a_crash_in_one_worker_is_raised(monkeypatch):
    import pytest

    library = library_of(6)

    def outcome(item):
        if item.track.title == "Song 2":
            raise RuntimeError("boom")
        return "downloaded"

    fake_process(monkeypatch, outcome=outcome, seconds=0.01)
    with pytest.raises(RuntimeError, match="boom"):
        update.download_missing(library, config.Settings(parallel_downloads=2, download_pause=0), lambda s: None,
                                matcher=object())
