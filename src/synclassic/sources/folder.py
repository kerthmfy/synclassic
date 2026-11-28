"""Your own music: a folder on your Mac (with everything in it, subfolders too) as a playlist.

Nothing is downloaded and your files are never changed: Synclassic reads their details (title,
artist, album, track number…) and syncs them to the iPod as they are, converting the ones the
iPod can't play (FLAC and others) on the way. Adding or removing files in the folder shows up
at the next Update. The playlist's order is by album artist, album, disc and track.

Details read from the files are remembered (by size and date) in folder-tags.json, so checking a
big folder again is quick.
"""

from __future__ import annotations

import json
import os
import pathlib

from synclassic.convert import AUDIO
from synclassic.models import SourcePlaylist, Track
from synclassic.textnorm import split_artists

SKIP_FOLDERS = {".Trash", ".Trashes", ".Spotlight-V100", ".fseventsd", "iPod_Control", ".rockbox"}


def _cache_path() -> pathlib.Path:
    from synclassic.config import app_dir

    return app_dir() / "folder-tags.json"


def _number(value) -> int | None:
    """'3/12' or '3' -> 3."""
    text = str(value[0] if isinstance(value, list) and value else value or "").split("/")[0].strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def read_tags(path: pathlib.Path) -> dict:
    """The song's details from inside the file (any format mutagen reads)."""
    from mutagen import File as MutagenFile

    details: dict = {}
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        audio = None
    if audio is None:
        return details
    tags = audio.tags or {}

    def first(*names: str) -> str:
        for name in names:
            try:
                values = tags.get(name)
            except (KeyError, ValueError):
                values = None
            if values:
                return str(values[0] if isinstance(values, list) else values).strip()
        return ""

    details["title"] = first("title")
    details["artist"] = first("artist")
    details["album"] = first("album")
    details["album_artist"] = first("albumartist", "album artist", "album_artist")
    details["genre"] = first("genre")
    details["year"] = first("date", "year", "originaldate")[:4]
    details["track"] = _number(first("tracknumber", "track"))
    details["disc"] = _number(first("discnumber", "disc"))
    length = getattr(getattr(audio, "info", None), "length", 0) or 0
    details["duration_ms"] = int(length * 1000) or None
    return details


def _track_from(path: pathlib.Path, details: dict) -> Track:
    title = details.get("title") or ""
    artist = details.get("artist") or ""
    if not title:  # "Artist - Title.flac" or "03 Title.flac"
        stem = path.stem
        if " - " in stem and not artist:
            artist, title = (part.strip() for part in stem.split(" - ", 1))
        else:
            title = stem.lstrip("0123456789 .-_") or stem
    artists = split_artists(artist) or ([artist] if artist else [])
    track = Track(title=title, artists=artists or [details.get("album_artist") or "Unknown Artist"],
                  album=details.get("album") or "", duration_ms=details.get("duration_ms"))
    track.album_artist = details.get("album_artist") or ""
    track.track_number = details.get("track")
    track.disc_number = details.get("disc")
    track.genre = details.get("genre") or ""
    track.release_date = details.get("year") or ""
    track.local_path = str(path)
    return track


def audio_files(folder: pathlib.Path) -> list[pathlib.Path]:
    found = []
    for directory, subfolders, files in os.walk(folder):
        subfolders[:] = sorted(d for d in subfolders if not d.startswith(".") and d not in SKIP_FOLDERS)
        for name in files:
            if name.startswith((".", "._")):
                continue
            path = pathlib.Path(directory) / name
            if path.suffix.lower() in AUDIO:
                found.append(path)
    return found


def parse_folder(folder: str | pathlib.Path, read=read_tags) -> SourcePlaylist:
    folder = pathlib.Path(folder).expanduser().resolve()
    try:
        cache = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    tracks: list[Track] = []
    changed = False
    for path in audio_files(folder):
        try:
            info = path.stat()
        except OSError:
            continue
        stamp = [info.st_size, int(info.st_mtime)]
        entry = cache.get(str(path))
        if entry and entry.get("stamp") == stamp:
            details = entry["details"]
        else:
            details = read(path)
            cache[str(path)] = {"stamp": stamp, "details": details}
            changed = True
        tracks.append(_track_from(path, details))
    if changed:
        try:
            tmp = _cache_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(cache), encoding="utf-8")
            os.replace(tmp, _cache_path())
        except OSError:
            pass

    def order(track: Track):
        artist = (track.album_artist or track.primary_artist).casefold()
        return (artist, (track.album or "").casefold(), track.disc_number or 0, track.track_number or 0,
                track.title.casefold(), track.local_path or "")

    tracks.sort(key=order)
    note = "" if tracks else "No music files found in this folder."
    return SourcePlaylist(name=folder.name, tracks=tracks, note=note)
