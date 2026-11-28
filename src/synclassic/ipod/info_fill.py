"""Fill In Genres & Years: songs on this Mac and on the iPod that are missing them.

On this Mac, Synclassic's songs get the info in its library and inside their files. On the iPod
only its library is changed (its Genres menu and song info), not the files. Synclassic's own
songs reuse what was found for their Mac copy.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from dataclasses import dataclass

import requests

from synclassic import song_info, tagging
from synclassic.ipod import itunesdb
from synclassic.ipod.device import IPod


@dataclass
class InfoResult:
    updated: int = 0
    not_found: int = 0
    failed: int = 0
    stopped: bool = False

    def summary(self) -> str:
        parts = [f"genre/year for {self.updated} song{'s' if self.updated != 1 else ''}"]
        if self.not_found:
            parts.append(f"{self.not_found} not found")
        if self.failed:
            parts.append(f"{self.failed} couldn't be checked")
        return ("Stopped: " if self.stopped else "") + ", ".join(parts) + "."


def library_missing(library) -> list:
    lookups = song_info.InfoLookups(library)
    return [item for item in library.tracks_in_playlists()
            if not (item.track.genre and item.track.year) and not lookups.recently_missing(item.track.artist, item.track.title)]


def ipod_missing(ipod: IPod, library=None) -> list[itunesdb.SongView]:
    db = itunesdb.parse(ipod.database_path.read_bytes())
    lookups = song_info.InfoLookups(library) if library is not None else None
    return [s for s in itunesdb.songs(db)
            if s.media_type in (0, 1) and (not s.genre or not s.year) and s.title
            and not (lookups is not None and lookups.recently_missing(s.artist, s.title))]


def fill_library(library, finder: song_info.InfoFinder | None = None, progress=None,
                 should_stop: Callable[[], bool] | None = None, say: Callable[[str], None] = lambda s: None) -> InfoResult:
    finder = finder or song_info.InfoFinder()
    lookups = song_info.InfoLookups(library)
    result = InfoResult()
    items = library_missing(library)
    for number, item in enumerate(items, start=1):
        if should_stop and should_stop():
            result.stopped = True
            break
        if progress:
            progress(number, len(items), item.track.display())
        track = item.track
        try:
            changed = song_info.complete(track, finder, lookups)
        except requests.RequestException as error:
            say(f"  {track.display()}: {error}")
            result.failed += 1
            continue
        if not changed:
            result.not_found += 1
            continue
        library.update_track(item.id, track=track)
        if item.editable_file:
            try:
                tagging.update_info(pathlib.Path(item.file_path), track.genre, track.year)  # type: ignore[arg-type]
            except Exception as error:
                say(f"  couldn't update {pathlib.Path(item.file_path).name}: {error}")  # type: ignore[arg-type]
        result.updated += 1
    return result


def fill_ipod(ipod: IPod, library=None, finder: song_info.InfoFinder | None = None, make_backup: bool = True,
              progress=None, should_stop: Callable[[], bool] | None = None,
              say: Callable[[str], None] = lambda s: None) -> InfoResult:
    from synclassic.ipod.manage import EditSession
    from synclassic.ipod.sync import load_state

    finder = finder or song_info.InfoFinder()
    lookups = song_info.InfoLookups(library) if library is not None else None
    mac_track = {}
    if library is not None:
        by_key = {item.key: item.track for item in library.tracks_in_playlists()}
        for key, entry in load_state(ipod)["tracks"].items():
            if key in by_key:
                mac_track[int(entry["dbid"], 16)] = by_key[key]

    result = InfoResult()
    changes: dict[int, dict] = {}  # song id -> fields
    songs = ipod_missing(ipod, library)
    for number, song in enumerate(songs, start=1):
        if should_stop and should_stop():
            result.stopped = True
            break
        if progress:
            progress(number, len(songs), f"{song.artist} - {song.title}")
        genre, year = "", 0
        known = mac_track.get(song.dbid)
        if known is not None and (known.genre or known.year):
            genre, year = known.genre, int(known.year or 0)
        else:
            try:
                info = finder.find(song.artist, song.title, round(song.length_ms / 1000))
            except requests.RequestException as error:
                say(f"  {song.title}: {error}")
                result.failed += 1
                continue
            if lookups is not None:
                lookups.remember(song.artist, song.title, info is not None)
            if info is not None:
                genre, year = info.genre, info.year
        fields = {}
        if genre and not song.genre:
            fields["genre"] = genre
        if year and not song.year:
            fields["year"] = year
        if fields:
            changes[song.id] = fields
            result.updated += 1
        else:
            result.not_found += 1
    if changes:
        session = EditSession(ipod, say, make_backup)
        for song_id, fields in changes.items():
            session.editor.update_song(song_id, fields)
        session.commit()
    return result
