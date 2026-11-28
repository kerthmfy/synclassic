"""The "Update" step: re-read sources, download what's new, rewrite playlists."""

from __future__ import annotations

import pathlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from synclassic import artwork, downloader, tagging
from synclassic.config import Settings
from synclassic.library import Library, LibraryPlaylist, LibraryTrack, now
from synclassic.matcher import Candidate, Matcher, MatchResult
from synclassic.models import SourcePlaylist
from synclassic.sources import FullReader, SourceError, read_source, specs_from_input, spotify_public
from synclassic.sources.spotify_api import SpotifyClient
from synclassic.textnorm import safe_filename

Say = Callable[[str], None]


@dataclass
class RefreshReport:
    name: str
    total: int = 0
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    note: str = ""
    error: str = ""


@dataclass
class DownloadReport:
    downloaded: int = 0
    review: int = 0
    unmatched: int = 0
    failed: int = 0
    stopped_early: str = ""
    on_ipod: int = 0  # not downloaded: already on the iPod


def keys_on_connected_ipod(library: Library, settings: Settings) -> set[str] | None:
    """With "skip songs already on the iPod" on and an iPod connected: the songs it has."""
    if not settings.skip_on_ipod:
        return None
    from synclassic.ipod.device import find_ipods
    from synclassic.ipod.sync import library_keys_on_ipod

    try:
        ipods = find_ipods()
        return library_keys_on_ipod(library, ipods[0]) if ipods else None
    except Exception:  # an unreadable iPod just means nothing is skipped
        return None


def refresh_playlist(
    library: Library, playlist: LibraryPlaylist, client: SpotifyClient | None, full_reader: FullReader | None = None
) -> RefreshReport:
    if playlist.spec.kind in ("store-songs", "mine"):  # songs you picked one by one: nothing to check
        report = RefreshReport(playlist.name)
        report.total = len(library.playlist_track_ids(playlist))
        return report
    try:
        source = read_source(playlist.spec, client, full_reader)
    except SourceError as error:
        return RefreshReport(playlist.name, error=str(error))
    return apply_source(library, playlist, source)


def apply_source(library: Library, playlist: LibraryPlaylist, source: SourcePlaylist) -> RefreshReport:
    """Store the playlist's current songs (in source order) and report what changed."""
    report = RefreshReport(playlist.name)
    before = library.playlist_track_ids(playlist)
    ids = library.with_your_changes(playlist, [library.upsert_track(track) for track in source.tracks])
    library.set_playlist_tracks(playlist, ids, source.note)
    report.total = len(ids)
    report.note = source.note
    old, new = set(before), set(ids)
    for track_id in [i for i in ids if i not in old]:
        item = library.track(track_id)
        report.added.append(item.track.display() if item else str(track_id))
    for track_id in [i for i in before if i not in new]:
        item = library.track(track_id)
        report.removed.append(item.track.display() if item else str(track_id))
    return report


def add_inputs(
    library: Library,
    inputs: list[str],
    name: str | None,
    client: SpotifyClient | None,
    say: Say,
    full_reader: FullReader | None = None,
) -> list[LibraryPlaylist]:
    """Add playlists from links/files/"liked". Already-added sources are returned as they are."""
    added: list[LibraryPlaylist] = []
    for text in inputs:
        try:
            specs = specs_from_input(text)
        except SourceError as error:
            say(f"Skipped '{text}': {error}")
            continue
        for spec in specs:
            existing = library.playlist_by_spec(spec)
            if existing:
                say(f"Already added as '{existing.name}'.")
                added.append(existing)
                continue
            try:
                source = read_source(spec, client, full_reader)
            except SourceError as error:
                say(f"Couldn't read {spec.describe()}: {error}")
                continue
            wanted = name if (name and len(inputs) == 1 and len(specs) == 1) else source.name
            playlist = library.add_playlist(wanted, spec)
            apply_source(library, playlist, source)
            say(f"Added '{playlist.name}' ({len(source.tracks)} songs)")
            if source.note:
                say(f"  Note: {source.note}")
            added.append(playlist)
    return added


def needs_work(item: LibraryTrack, retry_failed: bool) -> bool:
    if item.status in ("local", "ipod"):  # your own file, or kept only on the iPod
        return False
    if item.status == "pending":
        return True
    if item.status in ("downloaded", "review"):
        return not item.has_file  # file was deleted or moved: get it again
    if item.status in ("failed", "unmatched"):
        return retry_failed
    return False


def target_path(music_dir: pathlib.Path, item: LibraryTrack) -> pathlib.Path:
    track = item.track
    artist = safe_filename(track.album_artist or track.primary_artist or "Unknown Artist")
    album = safe_filename(track.album or "Singles")
    prefix = ""
    if track.track_number and track.album:
        prefix = f"{track.disc_number}-" if (track.disc_number or 1) > 1 else ""
        prefix += f"{track.track_number:02d} "
    stem = safe_filename(f"{prefix}{track.title}", 100)
    path = music_dir / "Music" / artist / album / stem
    # Two different songs with the same name: keep both.
    if any(path.with_suffix(ext).exists() for ext in (".m4a", ".mp3")) and not (
        item.file_path and pathlib.Path(item.file_path).with_suffix("") == path
    ):
        path = path.with_name(f"{stem} [{item.id}]")
    return path


def _enrich(item: LibraryTrack, cand: Candidate) -> None:
    """Fill in what the source didn't tell us (e.g. album name for no-login links)."""
    track = item.track
    if not track.album and cand.album:
        track.album = cand.album
    if not track.album_artist and track.album:
        track.album_artist = track.primary_artist
    if not track.artists and cand.artists:
        track.artists = list(cand.artists)
    if not track.duration_ms and cand.duration_s:
        track.duration_ms = cand.duration_s * 1000


def process_track(
    library: Library, item: LibraryTrack, settings: Settings, matcher: Matcher, say: Say
) -> str:
    """Match (if needed), download and tag one song. Returns its new status."""
    if item.manual and item.video_id:
        result = MatchResult(Candidate(item.video_id, "", []), 1.0, "matched", ["chosen by you"])
        return save_candidate(library, item, result, settings, say, manual=True)
    try:
        result = matcher.match(item.track)
    except Exception as error:  # network trouble, YouTube changes...
        from synclassic import errors

        message = errors.friendly(error)
        if isinstance(error, ValueError) and "Expecting value" in message:
            message = "YouTube Music sent back an empty answer (it may be blocking or rate-limiting this network)"
        library.update_track(item.id, status="failed", error=f"Search failed: {message}")
        say(f"    search failed: {message}")
        return "failed"
    if result.status == "unmatched" or not result.candidate:
        library.update_track(
            item.id, status="unmatched", match_score=result.score, match_notes="; ".join(result.notes), error=None
        )
        say(f"    no confident match (best score {result.score:.2f}); pick one with 'synclassic review'")
        return "unmatched"
    return save_candidate(library, item, result, settings, say)


def _cover_candidates(item: LibraryTrack, cand: Candidate):
    """Cover links, best first: Spotify's album art (640 px), then YouTube Music's."""
    track = item.track
    if track.cover_url:
        yield track.cover_url
    elif track.spotify_id:
        # No-login links don't include song covers; Spotify's page for the song does.
        url = spotify_public.track_cover_url(track.spotify_id)
        if url:
            track.cover_url = url
            yield url
    if cand.thumbnail and cand.kind == "song":
        yield cand.thumbnail
    if cand.thumbnail and cand.kind == "video":
        yield cand.thumbnail  # a video still: only if nothing better exists


def save_candidate(
    library: Library, item: LibraryTrack, result: MatchResult, settings: Settings, say: Say, manual: bool = False
) -> str:
    """Download and tag the chosen recording. `manual` means you picked it yourself."""
    cand = result.candidate
    assert cand is not None
    notes = "chosen by you" if manual else "; ".join(result.notes)
    _enrich(item, cand)
    from synclassic import organize

    playlist_destination = organize.new_download_path(library, item, settings)
    destination = playlist_destination or target_path(settings.music_path, item)
    if playlist_destination is not None and any(
        destination.with_suffix(ext).exists() for ext in (".m4a", ".mp3")
    ) and not (item.file_path and pathlib.Path(item.file_path).with_suffix("") == destination):
        destination = destination.with_name(f"{destination.name} [{item.id}]")
    try:
        download = downloader.download(cand.video_id, destination, settings)
    except downloader.DownloadError as error:
        library.update_track(
            item.id, track=item.track, status="failed", video_id=cand.video_id, match_score=result.score,
            match_notes=notes, error=str(error),
        )
        say(f"    download failed: {error}")
        return "failed"

    if settings.song_info and not (item.track.genre and item.track.year):
        from synclassic import song_info

        try:
            song_info.complete(item.track, song_info.InfoFinder(), song_info.InfoLookups(library))
        except Exception as error:  # a bonus; never lose the download over it
            say(f"    (no genre/year this time: {error})")

    cover = artwork.best_cover(_cover_candidates(item, cand), fetch=tagging.fetch_cover)
    try:
        tagging.tag_file(download.path, item.track, cover, cand.video_id)
    except Exception as error:  # a tagging problem shouldn't lose the download
        say(f"    warning: couldn't write tags: {error}")

    # Replace an older copy in another format/location, and its playlist-folder entries
    # (they'd still show the old recording).
    organize.forget_placements(library, item.id, keep=download.path)
    if item.file_path and pathlib.Path(item.file_path) != download.path and pathlib.Path(item.file_path).is_file():
        pathlib.Path(item.file_path).unlink()
    if playlist_destination is not None:
        first = organize.first_playlist(library, item.id)
        if first:
            library.set_placement(item.id, first.id, str(download.path))

    if settings.sound_check:  # measured now, so syncing doesn't have to
        from synclassic.loudness import LoudnessCache, LoudnessError

        try:
            LoudnessCache(library).get(download.path)
        except (LoudnessError, OSError) as error:
            say(f"    (couldn't measure loudness: {error})")

    if settings.lyrics:
        from synclassic import lyrics

        try:
            outcome = lyrics.fetch_and_write(download.path, item.track.artist, item.track.title, item.track.album or "",
                                             round((item.track.duration_ms or 0) / 1000), lyrics.LyricsFinder(),
                                             lyrics.LyricsLookups(library))
            if outcome == "found":
                say("    lyrics added")
        except Exception as error:  # lyrics are a bonus; never lose the download over them
            say(f"    (no lyrics this time: {error})")

    status = "review" if (result.status == "review" and not manual) else "downloaded"
    library.update_track(
        item.id, track=item.track, status=status, video_id=cand.video_id, match_score=result.score,
        match_notes=notes, file_path=str(download.path), error=None, downloaded_at=now(), manual=manual,
    )
    detail = "copied as-is" if not download.converted else f"converted from {download.source_codec or 'source'}"
    flag = "  [check with 'synclassic review']" if status == "review" else ""
    say(f"    saved ({detail}, match {result.score:.2f}){flag}")
    return status


def download_missing(
    library: Library,
    settings: Settings,
    say: Say,
    matcher: Matcher | None = None,
    retry_failed: bool = False,
    only: list[LibraryPlaylist] | None = None,
    progress: Callable[[int, int, LibraryTrack], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    skip_keys: set[str] | None = None,
) -> DownloadReport:
    """Match and download every song that needs it (except songs in `skip_keys`: already on the iPod).

    `progress(number, total, item)` is called before each song and once more with
    number == total + 1 at the end; `should_stop()` is checked between songs.
    """
    matcher = matcher or Matcher()
    report = DownloadReport()
    if only is not None:
        seen: set[int] = set()
        items = []
        for playlist in only:
            for item in library.playlist_tracks(playlist):
                if item.id not in seen:
                    seen.add(item.id)
                    items.append(item)
    else:
        items = library.tracks_in_playlists()
    work = [item for item in items if needs_work(item, retry_failed)]
    if skip_keys:
        report.on_ipod = sum(1 for item in work if item.key in skip_keys)
        work = [item for item in work if item.key not in skip_keys]
    if not work:
        return report
    problems = downloader.check_tools()
    if problems:
        report.stopped_early = "\n".join(problems)
        return report

    workers = max(1, min(MAX_PARALLEL, int(getattr(settings, "parallel_downloads", 1) or 1)))
    if workers > 1 and len(work) > 1:
        _download_in_parallel(library, work, settings, say, matcher, report, workers, progress, should_stop)
    else:
        failures_in_a_row = 0
        for number, item in enumerate(work, start=1):
            if should_stop and should_stop():
                report.stopped_early = STOPPED
                break
            if progress:
                progress(number, len(work), item)
            say(f"[{number}/{len(work)}] {item.track.display()}")
            status = process_track(library, item, settings, matcher, say)
            _count(report, status)
            failures_in_a_row = failures_in_a_row + 1 if status == "failed" else 0
            if failures_in_a_row >= 5:
                report.stopped_early = TOO_MANY_FAILURES
                break
            if number < len(work) and status in ("downloaded", "review", "failed"):
                _pause(settings.download_pause, should_stop)
    if progress and work:
        progress(len(work) + 1, len(work), work[-1])
    return report


MAX_PARALLEL = 3
STOPPED = "Stopped. Update again to continue where you left off."
TOO_MANY_FAILURES = ("Stopped after 5 failures in a row. YouTube may be rate-limiting; try again later "
                     "with 'synclassic update --retry-failed'.")


def _count(report: DownloadReport, status: str) -> None:
    if status == "downloaded":
        report.downloaded += 1
    elif status == "review":
        report.review += 1
    elif status == "unmatched":
        report.unmatched += 1
    else:
        report.failed += 1


def _download_in_parallel(library: Library, work: list[LibraryTrack], settings: Settings, say: Say, matcher,
                          report: DownloadReport, workers: int, progress, should_stop) -> None:
    """A few songs at a time (setting parallel_downloads). Each worker has its own connection to
    the library; each pauses between its own songs as usual."""
    import threading

    lock = threading.Lock()
    songs = iter(enumerate(work, start=1))
    streak = [0]
    problems: list[BaseException] = []

    def stop_now() -> bool:
        return bool(report.stopped_early) or bool(should_stop and should_stop())

    def worker() -> None:
        own = Library(library.path)
        try:
            while True:
                with lock:
                    if stop_now():
                        if should_stop and should_stop() and not report.stopped_early:
                            report.stopped_early = STOPPED
                        return
                    try:
                        number, item = next(songs)
                    except StopIteration:
                        return
                if progress:
                    progress(number, len(work), item)
                say(f"[{number}/{len(work)}] {item.track.display()}")
                current = own.track(item.id) or item
                status = process_track(own, current, settings, matcher, say)
                with lock:
                    _count(report, status)
                    streak[0] = streak[0] + 1 if status == "failed" else 0
                    if streak[0] >= 5 and not report.stopped_early:
                        report.stopped_early = TOO_MANY_FAILURES
                if status in ("downloaded", "review", "failed"):
                    _pause(settings.download_pause, stop_now)
        except BaseException as error:  # raised once all workers are done (the others stop first)
            with lock:
                problems.append(error)
                report.stopped_early = report.stopped_early or STOPPED
        finally:
            own.close()

    threads = [threading.Thread(target=worker, name=f"download-{n}", daemon=True) for n in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if problems:
        report.stopped_early = "" if report.stopped_early == STOPPED else report.stopped_early
        raise problems[0]


def _pause(seconds: float, should_stop: Callable[[], bool] | None) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            return
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


OWN_FILE_TYPES = (".m4a", ".mp3", ".aac", ".wav", ".aif", ".aiff")


def use_own_file(library: Library, item: LibraryTrack, source: pathlib.Path, settings: Settings) -> pathlib.Path:
    """Use a file you have (e.g. a song YouTube doesn't have) for a song: it's copied into the
    music folder, given the song's info and cover, and marked as your pick."""
    import shutil

    suffix = source.suffix.lower()
    if suffix not in OWN_FILE_TYPES:
        raise ValueError(f"The iPod can't play {suffix} files.")
    destination = target_path(settings.music_path, item).with_suffix(suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)
    if suffix in (".m4a", ".mp3"):
        cover = artwork.best_cover([item.track.cover_url], fetch=tagging.fetch_cover) if item.track.cover_url else None
        cover = cover or _cover_inside(destination)
        tagging.tag_file(destination, item.track, cover)
    if item.file_path and pathlib.Path(item.file_path) != destination and pathlib.Path(item.file_path).is_file():
        pathlib.Path(item.file_path).unlink()
    library.update_track(item.id, status="downloaded", file_path=str(destination), video_id=None, manual=True,
                         match_score=None, match_notes="your own file", error=None, downloaded_at=now())
    return destination


def _cover_inside(path: pathlib.Path) -> bytes | None:
    from synclassic.ipod.artwork import embedded_cover

    return embedded_cover(path)


def finalize(library: Library, settings: Settings | None = None) -> None:
    """After any change: arrange folders for the chosen layout and rewrite the playlist files."""
    from synclassic import organize, playlist_writer
    from synclassic.config import load_settings

    settings = settings or load_settings()
    organize.organize(library, settings)
    playlist_writer.write_all(library, settings.music_path)
