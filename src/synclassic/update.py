"""The "Update" step: re-read sources, download what's new, rewrite playlists."""

from __future__ import annotations

import pathlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from synclassic import downloader, tagging
from synclassic.config import Settings
from synclassic.library import Library, LibraryPlaylist, LibraryTrack, now
from synclassic.matcher import Candidate, Matcher, MatchResult
from synclassic.models import SourcePlaylist
from synclassic.sources import SourceError, read_source
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


def refresh_playlist(library: Library, playlist: LibraryPlaylist, client: SpotifyClient | None) -> RefreshReport:
    try:
        source = read_source(playlist.spec, client)
    except SourceError as error:
        return RefreshReport(playlist.name, error=str(error))
    return apply_source(library, playlist, source)


def apply_source(library: Library, playlist: LibraryPlaylist, source: SourcePlaylist) -> RefreshReport:
    """Store the playlist's current songs (in source order) and report what changed."""
    report = RefreshReport(playlist.name)
    before = library.playlist_track_ids(playlist)
    ids = [library.upsert_track(track) for track in source.tracks]
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


def needs_work(item: LibraryTrack, retry_failed: bool) -> bool:
    if item.status == "local":
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
    if not track.cover_url and cand.thumbnail:
        track.cover_url = cand.thumbnail
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
        message = str(error)
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


def save_candidate(
    library: Library, item: LibraryTrack, result: MatchResult, settings: Settings, say: Say, manual: bool = False
) -> str:
    """Download and tag the chosen recording. `manual` means you picked it yourself."""
    cand = result.candidate
    assert cand is not None
    notes = "chosen by you" if manual else "; ".join(result.notes)
    _enrich(item, cand)
    destination = target_path(settings.music_path, item)
    try:
        download = downloader.download(cand.video_id, destination, settings)
    except downloader.DownloadError as error:
        library.update_track(
            item.id, track=item.track, status="failed", video_id=cand.video_id, match_score=result.score,
            match_notes=notes, error=str(error),
        )
        say(f"    download failed: {error}")
        return "failed"

    cover = tagging.fetch_cover(item.track.cover_url)
    if cover is None and cand.thumbnail and cand.thumbnail != item.track.cover_url:
        cover = tagging.fetch_cover(cand.thumbnail)
    try:
        tagging.tag_file(download.path, item.track, cover, cand.video_id)
    except Exception as error:  # a tagging problem shouldn't lose the download
        say(f"    warning: couldn't write tags: {error}")

    # Replace an older copy in another format/location.
    if item.file_path and pathlib.Path(item.file_path) != download.path and pathlib.Path(item.file_path).is_file():
        pathlib.Path(item.file_path).unlink()

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
) -> DownloadReport:
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
    if not work:
        return report
    problems = downloader.check_tools()
    if problems:
        report.stopped_early = "\n".join(problems)
        return report

    failures_in_a_row = 0
    for number, item in enumerate(work, start=1):
        say(f"[{number}/{len(work)}] {item.track.display()}")
        status = process_track(library, item, settings, matcher, say)
        if status == "downloaded":
            report.downloaded += 1
        elif status == "review":
            report.review += 1
        elif status == "unmatched":
            report.unmatched += 1
        else:
            report.failed += 1
        failures_in_a_row = failures_in_a_row + 1 if status == "failed" else 0
        if failures_in_a_row >= 5:
            report.stopped_early = (
                "Stopped after 5 failures in a row. YouTube may be rate-limiting; try again later "
                "with 'synclassic update --retry-failed'."
            )
            break
        if number < len(work) and status in ("downloaded", "review", "failed"):
            time.sleep(settings.download_pause)
    return report
