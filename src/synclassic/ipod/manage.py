"""Change what's on the iPod directly: playlists and songs (the app's iPod management).

Every change runs as one session with the same safety as a sync:
- the check (byte-for-byte rebuild, no signed database) must pass;
- the database is backed up (the app does this once per session, not for every click);
- plays/skips/ratings and On-The-Go playlists the iPod logged are merged first;
- the new database is verified, then swapped in atomically; files are deleted only after.

Smart playlists (rules decide their songs) and playlists Synclassic keeps in sync from Spotify
are not edited here: Synclassic's next sync would overwrite such edits.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field

from synclassic.ipod import artwork, itunesdb, playcounts
from synclassic.ipod.check import check
from synclassic.ipod.device import IPod
from synclassic.ipod.edit import Editor, NewSong
from synclassic.ipod.sync import (
    SUPPORTED,
    _queue_scrobbles,
    _match_key,
    _music_folders,
    _new_ipod_path,
    _write_atomically,
    backup,
    load_state,
    save_state,
)

Say = Callable[[str], None]


class ManageError(RuntimeError):
    pass


def _quiet(text: str) -> None:
    pass


class EditSession:
    def __init__(self, ipod: IPod, say: Say = _quiet, make_backup: bool = True):
        self.ipod = ipod
        self.say = say
        report = check(ipod)
        if not report.safe_to_manage:
            raise ManageError("The iPod check didn't pass, so nothing was changed: " + "; ".join(report.problems))
        if make_backup:
            backup(ipod)
        self.original = ipod.database_path.read_bytes()
        self.db = itunesdb.parse(self.original)
        self.state = load_state(ipod)
        self.itunes = ipod.control / "iTunes"
        self.counts_file: pathlib.Path | None = self.itunes / "Play Counts"
        self.files_to_delete: list[pathlib.Path] = []
        self.copied: list[pathlib.Path] = []
        self.art_jobs: list[tuple[int, bytes]] = []
        self.art_replaced: set[int] = set()  # songs whose old cover gives way to a new one
        self.deleted_dbids: set[int] = set()
        self.state_changed = False
        self._merge_device_changes()
        self.editor = Editor(self.db)

    # --- what the iPod logged since the last write --------------------------------------------

    def _merge_device_changes(self) -> None:
        if self.counts_file is not None and self.counts_file.is_file():
            try:
                entries = playcounts.parse_play_counts(self.counts_file.read_bytes())
                _queue_scrobbles(self.db, entries, self.say)
                playcounts.merge_play_counts(self.db, entries)
            except ValueError:
                self.counts_file = None  # unreadable: leave it alone
        songs = itunesdb.songs(self.db)
        self.otg = []
        for otg in playcounts.otg_files(self.itunes):
            ids = [songs[p].id for p in playcounts.parse_otg(otg.read_bytes()) if p < len(songs)]
            if ids:
                self.otg.append(ids)

    # --- lookups ------------------------------------------------------------------------------

    def songs(self) -> dict[int, itunesdb.SongView]:
        return {s.id: s for s in itunesdb.songs(self.db)}

    def playlist(self, playlist_id: int) -> itunesdb.PlaylistView:
        for view in itunesdb.playlists(self.db, 2):
            if view.chunk.u64(0x1C) == playlist_id and not view.is_master:
                return view
        raise ManageError("That playlist is no longer on the iPod.")

    def managed_ids(self) -> set[int]:
        return {int(v["id"], 16) for v in self.state["playlists"].values()}

    def editable(self, playlist_id: int) -> itunesdb.PlaylistView:
        view = self.playlist(playlist_id)
        if view.is_smart:
            raise ManageError(f"“{view.name}” is a smart playlist: its songs come from its rules.")
        if view.is_podcasts:
            raise ManageError("The Podcasts playlist is kept by the iPod's podcast sync: subscribe or unsubscribe in "
                              "Synclassic's Podcasts instead.")
        if playlist_id in self.managed_ids():
            raise ManageError(
                f"“{view.name}” is kept in sync from Spotify by Synclassic, so changes here would be replaced "
                "at the next sync. Change it in Spotify (or remove it from Synclassic) instead."
            )
        return view

    # --- edits ----------------------------------------------------------------------------------

    def set_songs(self, playlist_id: int, song_ids: list[int]) -> None:
        self.editable(playlist_id)
        songs = self.songs()
        self.editor.set_playlist_songs(playlist_id, [(i, songs[i].dbid) for i in song_ids if i in songs])

    def create(self, name: str, song_ids: list[int]) -> int:
        playlist_id = self.editor.create_playlist(name.strip() or "untitled playlist")
        songs = self.songs()
        self.editor.set_playlist_songs(playlist_id, [(i, songs[i].dbid) for i in song_ids if i in songs])
        return playlist_id

    def rename(self, playlist_id: int, name: str) -> None:
        self.editable(playlist_id)
        self.editor.rename_playlist(playlist_id, name.strip())

    def delete_playlist(self, playlist_id: int, synced_too: bool = False) -> None:
        """Delete a playlist. `synced_too` also allows one Synclassic syncs from Spotify (it is then
        forgotten, so a sync won't touch it again unless it's still in Synclassic's playlists)."""
        view = self.playlist(playlist_id)
        if not (synced_too and not view.is_smart and playlist_id in self.managed_ids()):
            self.editable(playlist_id)
        self.editor.delete_playlist(playlist_id)
        before = len(self.state["playlists"])
        self.state["playlists"] = {
            key: value for key, value in self.state["playlists"].items() if int(value["id"], 16) != playlist_id
        }
        self.state_changed = self.state_changed or len(self.state["playlists"]) != before

    def delete_songs(self, song_ids: set[int]) -> int:
        songs = self.songs()
        doomed = [songs[i] for i in song_ids if i in songs]
        self.editor.remove_songs({s.id for s in doomed})
        for song in doomed:
            if song.location:
                self.files_to_delete.append(self.ipod.root / song.relative_path)
            self.deleted_dbids.add(song.dbid)
        return len(doomed)

    def list_file_on_ipod(self, path: pathlib.Path) -> int:
        """Add a song file that's already on the iPod (but not in its library). Returns its song id."""
        extension = path.suffix.lower()
        if extension not in SUPPORTED:
            raise ManageError(f"The iPod can't play {extension} files.")
        location = ":" + path.relative_to(self.ipod.root).as_posix().replace("/", ":")
        song_id, dbid = self.editor.add_song(song_from_file(path, location, extension))
        cover = artwork.embedded_cover(path)
        if cover:
            self.art_jobs.append((dbid, cover))
        return song_id

    def set_cover(self, dbid: int, cover: bytes) -> None:
        """Give a song a new cover (replacing any it has)."""
        self.art_jobs.append((dbid, cover))
        self.art_replaced.add(dbid)

    def edit_songs(self, song_ids: list[int], changes: dict[str, str | int]) -> int:
        for song_id in song_ids:
            self.editor.update_song(song_id, changes)
        return len(song_ids)

    def copy_file(self, path: pathlib.Path, audiobook: bool = False) -> int:
        """Copy a song file from this computer onto the iPod (as an audiobook, if asked). Returns its new song id."""
        extension = path.suffix.lower()
        if extension not in SUPPORTED:
            raise ManageError(f"The iPod can't play {extension} files.")
        if not self.ipod.fits_file(path.stat().st_size):
            raise ManageError(f"{path.name} is 4 GB or bigger, more than a Windows-formatted (FAT32) iPod can hold.")
        target = _new_ipod_path(self.ipod, extension, _music_folders(self.ipod))
        tmp = target.with_name(target.name + ".part")
        shutil.copyfile(path, tmp)
        if tmp.stat().st_size != path.stat().st_size:
            tmp.unlink(missing_ok=True)
            raise ManageError(f"Copying {path.name} didn't verify.")
        os.replace(tmp, target)
        self.copied.append(target)
        location = ":" + target.relative_to(self.ipod.root).as_posix().replace("/", ":")
        info = song_from_file(target, location, extension)
        if audiobook:
            info.audiobook = True
            if extension == ".m4b":
                info.filetype = "m4b"
        song_id, dbid = self.editor.add_song(info)
        cover = artwork.embedded_cover(target)
        if cover:
            self.art_jobs.append((dbid, cover))
        return song_id

    # --- finishing -----------------------------------------------------------------------------

    def commit(self) -> None:
        try:
            self._commit()
        except BaseException:
            for path in self.copied:  # a failed change leaves no new files behind
                path.unlink(missing_ok=True)
            raise

    def _commit(self) -> None:
        self.otg_named = []
        for number, ids in enumerate(self.otg, start=1):
            name = f"On-The-Go {datetime.date.today():%Y-%m-%d}" + (f" ({number})" if len(self.otg) > 1 else "")
            self.create(name, ids)
            self.otg_named.append((name, ids))
        self.editor.finish()
        links = []
        art_path = self.ipod.control / "Artwork" / "ArtworkDB"
        if (self.art_jobs or self.deleted_dbids) and art_path.is_file():
            # Old images of replaced covers go (the new ones are only added at commit).
            try:
                writer = artwork.ArtworkWriter(art_path.parent)
                if writer.usable:
                    mhits = {s.dbid: s.chunk for s in itunesdb.songs(self.db)}
                    links = [(mhits[d], writer.add(d, c), len(c)) for d, c in self.art_jobs if d in mhits]
                    writer.remove_for(self.deleted_dbids | self.art_replaced)
                    writer.commit()
            except (OSError, itunesdb.DatabaseError) as error:
                self.say(f"Album art was skipped ({error}).")
                links = []
        for mhit, image_id, size in links:
            artwork.link_song(mhit, image_id, size)
        data = itunesdb.serialize(self.db)
        if itunesdb.serialize(itunesdb.parse(data)) != data:
            raise ManageError("The changed database didn't verify, so nothing was changed.")
        from synclassic.ipod import signing

        _write_atomically(self.ipod.database_path, signing.finish(self.ipod, data, self.original))
        for path in self.files_to_delete:
            path.unlink(missing_ok=True)
        if self.counts_file is not None and self.counts_file.is_file():
            self.counts_file.unlink()
        for otg in playcounts.otg_files(self.itunes):
            otg.unlink()
        if getattr(self, "otg_named", None):
            from synclassic.ipod.sync import _otg_to_spotify

            _otg_to_spotify(None, self.db, self.state, self.otg_named, self.say)
        if self.deleted_dbids:
            self.state["tracks"] = {
                k: v for k, v in self.state["tracks"].items() if int(v["dbid"], 16) not in self.deleted_dbids
            }
            if "covers" in self.state:
                self.state["covers"] = {k: v for k, v in self.state["covers"].items()
                                        if int(k, 16) not in self.deleted_dbids}
            self.state_changed = True
        if self.state_changed:
            save_state(self.ipod, self.state)
        if hasattr(os, "sync"):
            os.sync()


def song_from_file(path: pathlib.Path, location: str, extension: str) -> NewSong:
    """Song info for the iPod, from the file's own tags."""
    from mutagen import File as MutagenFile

    audio = MutagenFile(path, easy=True)
    tags = getattr(audio, "tags", None) or {}
    info = getattr(audio, "info", None)

    def tag(name: str) -> str:
        value = tags.get(name) if hasattr(tags, "get") else None
        return str(value[0]) if value else ""

    def number(name: str) -> int:
        text = tag(name).split("/")[0]
        return int(text) if text.isdigit() else 0

    year = tag("date")[:4]
    return NewSong(
        location=location,
        filetype=SUPPORTED[extension],
        size=path.stat().st_size,
        length_ms=int((getattr(info, "length", 0) or 0) * 1000),
        bitrate=int((getattr(info, "bitrate", 0) or 0) / 1000),
        sample_rate=int(getattr(info, "sample_rate", 0) or 44100),
        vbr=False,
        title=tag("title") or path.stem,
        artist=tag("artist"),
        album=tag("album"),
        album_artist=tag("albumartist"),
        genre=tag("genre"),
        track_number=number("tracknumber"),
        disc_number=number("discnumber"),
        year=int(year) if year.isdigit() else 0,
        lyrics=bool(_lyrics_inside(path)),
    )


def _lyrics_inside(path: pathlib.Path) -> str | None:
    from synclassic import lyrics

    return lyrics.read(path)


# --- one-call helpers used by the app ------------------------------------------------------------


def _run(ipod: IPod, make_backup: bool, action, say: Say = _quiet):
    session = EditSession(ipod, say, make_backup)
    result = action(session)
    session.commit()
    return result


def create_playlist(ipod: IPod, name: str, song_ids: list[int], make_backup: bool = True) -> int:
    return _run(ipod, make_backup, lambda s: s.create(name, song_ids))


def rename_playlist(ipod: IPod, playlist_id: int, name: str, make_backup: bool = True) -> None:
    _run(ipod, make_backup, lambda s: s.rename(playlist_id, name))


def delete_playlist(ipod: IPod, playlist_id: int, make_backup: bool = True, synced_too: bool = False) -> None:
    _run(ipod, make_backup, lambda s: s.delete_playlist(playlist_id, synced_too))


def set_playlist_songs(ipod: IPod, playlist_id: int, song_ids: list[int], make_backup: bool = True) -> None:
    _run(ipod, make_backup, lambda s: s.set_songs(playlist_id, song_ids))


def add_to_playlist(ipod: IPod, playlist_id: int, song_ids: list[int], make_backup: bool = True) -> int:
    """Append songs (skipping ones already in it). Returns how many were added."""
    def action(session: EditSession) -> int:
        current = session.editable(playlist_id).track_ids
        new = [i for i in dict.fromkeys(song_ids) if i not in set(current)]
        session.set_songs(playlist_id, current + new)
        return len(new)

    return _run(ipod, make_backup, action)


def delete_songs(ipod: IPod, song_ids: set[int], make_backup: bool = True) -> int:
    return _run(ipod, make_backup, lambda s: s.delete_songs(song_ids))


def edit_songs(ipod: IPod, song_ids: list[int], changes: dict[str, str | int], make_backup: bool = True) -> int:
    """Change song info (only the given fields) for one or more songs."""
    return _run(ipod, make_backup, lambda s: s.edit_songs(song_ids, changes))


# --- clean up --------------------------------------------------------------------------------------

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".m4p", ".aac", ".wav", ".aif", ".aiff", ".mp4", ".m4v", ".mov"}


@dataclass
class CleanupReport:
    leftovers: list[pathlib.Path] = field(default_factory=list)  # files the library doesn't list
    leftover_bytes: int = 0
    missing: list[tuple[int, str]] = field(default_factory=list)  # (song id, "Artist - Title") without a file
    duplicates: list["Duplicate"] = field(default_factory=list)

    @property
    def duplicate_bytes(self) -> int:
        return sum(s.size for group in self.duplicates for s in group.extra)


@dataclass
class Duplicate:
    keep: itunesdb.SongView  # the best copy
    extra: list[itunesdb.SongView]  # the others

    @property
    def name(self) -> str:
        return f"{self.keep.artist} - {self.keep.title}" if self.keep.artist else self.keep.title


def _best_copy_key(song: itunesdb.SongView) -> tuple:
    mhit = song.chunk
    return (mhit.u32(0x38), mhit.u8(0xA4) == 1, song.play_count, song.rating, -song.id)


def find_duplicates(songs: list[itunesdb.SongView]) -> list[Duplicate]:
    """The same song more than once (same artist, title and length within 3 s); music only."""
    from synclassic.textnorm import core_title, normalize

    groups: dict[tuple[str, str], list[itunesdb.SongView]] = {}
    for song in songs:
        if song.media_type in (0, 1) and song.title and song.location:
            groups.setdefault((normalize(song.artist), core_title(song.title)), []).append(song)
    found = []
    for candidates in groups.values():
        while len(candidates) > 1:
            first = candidates[0]
            same = [s for s in candidates if abs(s.length_ms - first.length_ms) <= 3000]
            candidates = [s for s in candidates if s not in same]
            if len(same) > 1:
                same.sort(key=_best_copy_key, reverse=True)
                found.append(Duplicate(same[0], same[1:]))
    found.sort(key=lambda d: d.name.casefold())
    return found


def find_cleanup(ipod: IPod) -> CleanupReport:
    db = itunesdb.parse(ipod.database_path.read_bytes())
    songs = itunesdb.songs(db)
    listed = {s.relative_path.lower() for s in songs if s.location}
    report = CleanupReport()
    music = ipod.control / "Music"
    if music.is_dir():
        for path in sorted(music.rglob("*")):
            if path.suffix.lower() in AUDIO_EXTENSIONS and path.is_file() and not path.name.startswith("._"):
                if path.relative_to(ipod.root).as_posix().lower() not in listed:
                    report.leftovers.append(path)
                    report.leftover_bytes += path.stat().st_size
    for song in songs:
        if song.location and not (ipod.root / song.relative_path).is_file():
            report.missing.append((song.id, f"{song.artist} - {song.title}" if song.artist else song.title))
    report.duplicates = find_duplicates([s for s in songs if (ipod.root / s.relative_path).is_file()] if songs else [])
    return report


AUDIOBOOK_TYPES = (".m4b", ".m4a", ".mp3", ".aac")


def add_audiobooks(ipod: IPod, paths: list[pathlib.Path], make_backup: bool = True, say: Say = _quiet) -> int:
    """Copy audiobooks to the iPod: listed under Music > Audiobooks, remembering where you
    stopped and left out of shuffle. Chapters inside .m4b files work as on iTunes. Returns how many."""

    def action(session: EditSession) -> int:
        for path in paths:
            say(f"Copying {path.name}")
            session.copy_file(path, audiobook=True)
        return len(paths)

    return _run(ipod, make_backup, action, say)


def remove_duplicates(ipod: IPod, groups: list[Duplicate], make_backup: bool = True) -> int:
    """Delete the extra copies. Playlists that had an extra copy get the kept one instead, so no
    playlist loses a song. Returns how many copies were deleted."""

    def action(session: EditSession) -> int:
        replace = {extra.id: group.keep for group in groups for extra in group.extra}
        dbid_for = {extra.dbid: group.keep.dbid for group in groups for extra in group.extra}
        for view in itunesdb.playlists(session.db, 2):
            if view.is_master or view.is_smart or view.is_podcasts or not set(view.track_ids) & set(replace):
                continue
            entries, seen = [], set()
            songs = session.songs()
            for song_id in view.track_ids:
                target = replace.get(song_id)
                song_id = target.id if target is not None else song_id
                if song_id in seen and target is not None:
                    continue  # both copies were in this playlist: keep one entry
                seen.add(song_id)
                if song_id in songs:
                    entries.append((song_id, songs[song_id].dbid))
            session.editor.set_playlist_songs(view.chunk.u64(0x1C), entries)
        # Synclassic's own records point at the kept copy, so syncs don't copy the song again.
        for key, entry in session.state["tracks"].items():
            dbid = int(entry["dbid"], 16)
            if dbid in dbid_for:
                session.state["tracks"][key] = {"dbid": f"{dbid_for[dbid]:016x}", "added": False}
                session.state_changed = True
        return session.delete_songs(set(replace))

    return _run(ipod, make_backup, action)


def add_leftovers(ipod: IPod, paths: list[pathlib.Path], make_backup: bool = True, say: Say = _quiet) -> int:
    """Put leftover song files back into the iPod's library (using their own tags)."""
    def action(session: EditSession) -> int:
        added = 0
        for path in paths:
            try:
                session.list_file_on_ipod(path)
                added += 1
            except (ManageError, OSError, ValueError) as error:
                say(f"Skipped {path.name}: {error}")
        return added

    return _run(ipod, make_backup, action, say)


def delete_leftovers(ipod: IPod, paths: list[pathlib.Path]) -> int:
    """Delete leftover files. Re-checks each one against the library first (never deletes a listed song)."""
    current = {p.resolve() for p in find_cleanup(ipod).leftovers}
    deleted = 0
    for path in paths:
        if path.resolve() in current:
            path.unlink(missing_ok=True)
            deleted += 1
    return deleted


def remove_missing(ipod: IPod, song_ids: list[int], make_backup: bool = True) -> int:
    """Remove library entries whose file is gone."""
    return _run(ipod, make_backup, lambda s: s.delete_songs(set(song_ids)))


# --- backups -------------------------------------------------------------------------------------


@dataclass
class BackupInfo:
    folder: pathlib.Path
    when: str
    songs: int | None
    size: int
    has_artwork: bool


def list_backups(ipod: IPod) -> list[BackupInfo]:
    from synclassic.ipod.sync import list_backups as folders

    result = []
    for folder in folders(ipod):
        database = folder / "iTunesDB"
        if not database.is_file():
            continue
        try:
            songs = len(itunesdb.songs(itunesdb.parse(database.read_bytes())))
        except (OSError, itunesdb.DatabaseError):
            songs = None
        name = folder.name
        when = f"{name[:10]} {name[11:19].replace('-', ':')}" if len(name) >= 19 else name
        result.append(BackupInfo(folder, when, songs, database.stat().st_size, (folder / "ArtworkDB").is_file()))
    return result


def restore_backup(ipod: IPod, folder: pathlib.Path) -> pathlib.Path:
    """Put a backup back on the iPod. The current state is backed up first, so this can be undone.
    Returns that new backup's folder."""
    import tempfile

    from synclassic.ipod.sync import restore

    # Copy the chosen backup aside first: making the new backup can rotate the oldest one away.
    with tempfile.TemporaryDirectory(prefix="synclassic-restore-") as tmp:
        chosen = pathlib.Path(tmp) / "backup"
        shutil.copytree(folder, chosen)
        itunesdb.parse((chosen / "iTunesDB").read_bytes())  # refuse a damaged backup before changing anything
        current = backup(ipod)
        restore(ipod, chosen)
    return current


@dataclass
class ImportResult:
    playlist_id: int = 0
    matched: int = 0
    copied: int = 0
    missing: list[str] = field(default_factory=list)


def import_m3u(ipod: IPod, path: pathlib.Path, name: str | None = None, make_backup: bool = True,
               say: Say = _quiet) -> ImportResult:
    """Make an iPod playlist from an .m3u/.m3u8: songs already on the iPod are used, other song
    files on this computer are copied over, the rest are listed as missing."""
    from synclassic.sources.m3u import parse_m3u

    source = parse_m3u(path)
    result = ImportResult()

    def action(session: EditSession) -> ImportResult:
        by_text: dict[tuple[str, str], list[itunesdb.SongView]] = {}
        for song in session.songs().values():
            by_text.setdefault(_match_key(song.artist, song.title), []).append(song)
        ids = []
        for track in source.tracks:
            candidates = by_text.get(_match_key(track.artist, track.title), [])
            best = min(candidates, key=lambda s: abs(s.length_ms - (track.duration_ms or s.length_ms)), default=None)
            if best is not None and (not track.duration_ms or abs(best.length_ms - track.duration_ms) <= 4000):
                ids.append(best.id)
                result.matched += 1
            elif track.local_path:
                try:
                    ids.append(session.copy_file(pathlib.Path(track.local_path)))
                    result.copied += 1
                    say(f"Copied {pathlib.Path(track.local_path).name}")
                except ManageError as error:
                    result.missing.append(f"{track.display()} ({error})")
            else:
                result.missing.append(track.display())
        result.playlist_id = session.create(name or source.name, ids)
        return result

    return _run(ipod, make_backup, action, say)


def export_m3u(ipod: IPod, playlist_id: int, destination: pathlib.Path) -> int:
    """Write an iPod playlist as .m3u8 (pointing at the files on the iPod). Returns songs written."""
    db = itunesdb.parse(ipod.database_path.read_bytes())
    songs = {s.id: s for s in itunesdb.songs(db)}
    view = next((p for p in itunesdb.playlists(db, 2) if p.chunk.u64(0x1C) == playlist_id), None)
    if view is None:
        raise ManageError("That playlist is no longer on the iPod.")
    lines = ["#EXTM3U", f"#PLAYLIST:{view.name}"]
    count = 0
    for song_id in view.track_ids:
        song = songs.get(song_id)
        if song is None:
            continue
        display = f"{song.artist} - {song.title}" if song.artist else song.title
        lines.append(f"#EXTINF:{round(song.length_ms / 1000)},{display}")
        lines.append(str(ipod.root / song.relative_path))
        count += 1
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count
