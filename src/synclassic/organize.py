"""Folder layouts for downloaded music.

"artist" (default):   Music/<Album artist>/<Album>/<Track>.m4a     each song stored once
"playlist":           <Playlist>/<Artist> - <Title>.m4a          one folder per playlist,
                      plus <Playlist>/<Playlist>.m3u8 in Spotify order

In the playlist layout a song that's in several playlists appears in each folder. On Mac
disks (APFS, Mac OS Extended) these are hard links: one file listed in several folders, so it
takes space only once. On disks without hard links (exFAT, FAT32) it's a copy.
"""

from __future__ import annotations

import os
import pathlib
import shutil

from synclassic.config import Settings
from synclassic.library import Library, LibraryPlaylist, LibraryTrack
from synclassic.playlist_writer import render_m3u8
from synclassic.textnorm import safe_filename

LAYOUTS = ("artist", "playlist")
RESERVED = {"music", "playlists"}  # used by the artist layout


def playlist_folder(music_dir: pathlib.Path, playlist_name: str) -> pathlib.Path:
    name = safe_filename(playlist_name, 100)
    if name.lower() in RESERVED:
        name = f"{name} (playlist)"
    return music_dir / name


def placed_name(item: LibraryTrack, extension: str) -> str:
    track = item.track
    stem = f"{track.artist} - {track.title}" if track.artists else track.title
    return safe_filename(stem, 120) + extension


def link_or_copy(source: pathlib.Path, target: pathlib.Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _remove(path: pathlib.Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def remove_song_file(library: Library, item: LibraryTrack) -> None:
    """Delete a song's file and all its playlist-folder entries."""
    forget_placements(library, item.id)
    if item.file_path:
        path = pathlib.Path(item.file_path)
        _remove(path)
        _remove_empty_dirs(path.parent, path.parents[-1])


def _remove_empty_dirs(start: pathlib.Path, stop: pathlib.Path) -> None:
    folder = start
    while folder != stop and stop in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def first_playlist(library: Library, track_id: int) -> LibraryPlaylist | None:
    row = library.db.execute(
        "SELECT playlist_id FROM playlist_tracks WHERE track_id = ? ORDER BY playlist_id, position LIMIT 1", (track_id,)
    ).fetchone()
    if not row:
        return None
    return next((p for p in library.playlists() if p.id == row["playlist_id"]), None)


def new_download_path(library: Library, item: LibraryTrack, settings: Settings) -> pathlib.Path | None:
    """Where a new download goes in the playlist layout (without extension); None = artist layout."""
    if settings.folder_layout != "playlist":
        return None
    playlist = first_playlist(library, item.id)
    if playlist is None:
        return None
    folder = playlist_folder(settings.music_path, playlist.name)
    return folder / placed_name(item, "")


def forget_placements(library: Library, track_id: int, keep: pathlib.Path | None = None) -> None:
    """Remove a song's extra folder entries (e.g. before a new version is downloaded)."""
    for row in library.placements(track_id=track_id):
        path = pathlib.Path(row["path"])
        if keep is None or path != keep:
            _remove(path)
    library.delete_placements(track_id=track_id)


def organize(library: Library, settings: Settings) -> dict[str, int]:
    """Bring the folders in line with the chosen layout. Safe to run any time."""
    if settings.folder_layout == "playlist":
        return _organize_playlists(library, settings)
    return _organize_artists(library, settings)


def _organize_playlists(library: Library, settings: Settings) -> dict[str, int]:
    music_dir = settings.music_path
    stats = {"placed": 0, "removed": 0}
    wanted: set[tuple[int, int]] = set()
    for playlist in library.playlists():
        folder = playlist_folder(music_dir, playlist.name)
        used: set[str] = set()
        entries = []
        for item in library.playlist_tracks(playlist):
            if not item.has_file or item.status == "local":
                continue
            source = pathlib.Path(item.file_path)  # type: ignore[arg-type]
            key = (item.id, playlist.id)
            if key in wanted:  # the same song twice in one playlist
                existing = library.placement(item.id, playlist.id)
                if existing:
                    entries.append((item, pathlib.Path(existing)))
                continue
            wanted.add(key)
            existing = library.placement(item.id, playlist.id)
            target = pathlib.Path(existing) if existing else None
            if target is not None and (target.parent != folder or not target.exists()):
                if target.exists() and target != source:
                    _remove(target)
                target = None
            if target is None:
                name = placed_name(item, source.suffix)
                candidate, number = folder / name, 2
                while candidate.name.lower() in used or (candidate.exists() and not _same_file(candidate, source)):
                    candidate = folder / f"{pathlib.Path(name).stem} ({number}){source.suffix}"
                    number += 1
                target = candidate
                if not target.exists():
                    link_or_copy(source, target)
                    stats["placed"] += 1
                library.set_placement(item.id, playlist.id, str(target))
            used.add(target.name.lower())
            entries.append((item, target))
        if entries:
            lines = [
                (round((i.track.duration_ms or 0) / 1000) or -1, i.track.display(), path) for i, path in entries
            ]
            m3u8 = folder / f"{safe_filename(playlist.name, 100)}.m3u8"
            text = render_m3u8(playlist.name, lines, folder)
            if not m3u8.exists() or m3u8.read_text(encoding="utf-8", errors="replace") != text:
                m3u8.write_text(text, encoding="utf-8")

    # Entries for songs that left a playlist (or playlists that were removed/renamed).
    for row in library.placements():
        if (row["track_id"], row["playlist_id"]) in wanted:
            continue
        path = pathlib.Path(row["path"])
        item = library.track(row["track_id"])
        if item and item.file_path == str(path):
            # This entry is the song's main file: hand that role to another copy first.
            others = [pathlib.Path(r["path"]) for r in library.placements(track_id=item.id) if r["path"] != row["path"]]
            others = [o for o in others if o.exists()]
            if others:
                library.update_track(item.id, file_path=str(others[0]))
            else:
                continue  # keep the only copy; 'synclassic clean' can delete it
        _remove(path)
        library.delete_placement(row["track_id"], row["playlist_id"])
        stats["removed"] += 1
        _remove_m3u8_if_empty(path.parent)
        _remove_empty_dirs(path.parent, music_dir)

    # A song's main file must be one of its playlist-folder entries (it isn't after a rename,
    # or when switching from the artist layout): point it at an entry, remove the old file.
    for item in library.tracks_in_playlists():
        if not item.has_file or item.status == "local":
            continue
        path = pathlib.Path(item.file_path)  # type: ignore[arg-type]
        copies = [pathlib.Path(r["path"]) for r in library.placements(track_id=item.id)]
        copies = [c for c in copies if c.exists()]
        if copies and path not in copies:
            library.update_track(item.id, file_path=str(copies[0]))
            _remove(path)
            _remove_m3u8_if_empty(path.parent)
            _remove_empty_dirs(path.parent, music_dir)

    # Folders of playlists that were renamed or removed.
    current = {playlist_folder(music_dir, p.name) for p in library.playlists()}
    for folder in music_dir.iterdir() if music_dir.is_dir() else []:
        if folder.is_dir() and folder not in current and folder.name.lower() not in RESERVED:
            for m3u8 in folder.glob("*.m3u8"):
                if _is_ours(m3u8):
                    _remove(m3u8)
            _remove_empty_dirs(folder, music_dir)
    return stats


def _organize_artists(library: Library, settings: Settings) -> dict[str, int]:
    from synclassic.update import target_path  # artist-layout location

    music_dir = settings.music_path
    stats = {"placed": 0, "removed": 0}
    for item in library.tracks_in_playlists():
        if not item.has_file or item.status == "local":
            continue
        path = pathlib.Path(item.file_path)  # type: ignore[arg-type]
        if music_dir / "Music" in path.parents:
            continue
        destination = target_path(music_dir, item).with_suffix(path.suffix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            link_or_copy(path, destination)
        library.update_track(item.id, file_path=str(destination))
    for row in library.placements():
        path = pathlib.Path(row["path"])
        item = library.track(row["track_id"])
        library.delete_placement(row["track_id"], row["playlist_id"])
        if item and item.file_path == str(path):
            continue  # a song no playlist uses any more: keep its only file ('clean' deletes it)
        _remove(path)
        stats["removed"] += 1
        _remove_m3u8_if_empty(path.parent)
        _remove_empty_dirs(path.parent, music_dir)
    return stats


def _same_file(a: pathlib.Path, b: pathlib.Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _remove_m3u8_if_empty(folder: pathlib.Path) -> None:
    """A playlist folder holding only its .m3u8 is empty: remove the .m3u8 so the folder can go."""
    try:
        contents = list(folder.iterdir())
    except OSError:
        return
    if contents and all(p.suffix.lower() == ".m3u8" and _is_ours(p) for p in contents):
        for p in contents:
            _remove(p)


def _is_ours(m3u8: pathlib.Path) -> bool:
    from synclassic.playlist_writer import MARKER

    try:
        return MARKER in m3u8.read_text(encoding="utf-8", errors="replace").splitlines()[:3]
    except OSError:
        return False
