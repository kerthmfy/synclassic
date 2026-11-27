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
        self.db = itunesdb.parse(ipod.database_path.read_bytes())
        self.state = load_state(ipod)
        self.itunes = ipod.control / "iTunes"
        self.counts_file: pathlib.Path | None = self.itunes / "Play Counts"
        self.files_to_delete: list[pathlib.Path] = []
        self.copied: list[pathlib.Path] = []
        self.art_jobs: list[tuple[int, bytes]] = []
        self.deleted_dbids: set[int] = set()
        self._merge_device_changes()
        self.editor = Editor(self.db)

    # --- what the iPod logged since the last write --------------------------------------------

    def _merge_device_changes(self) -> None:
        if self.counts_file is not None and self.counts_file.is_file():
            try:
                playcounts.merge_play_counts(self.db, playcounts.parse_play_counts(self.counts_file.read_bytes()))
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

    def delete_playlist(self, playlist_id: int) -> None:
        self.editable(playlist_id)
        self.editor.delete_playlist(playlist_id)

    def delete_songs(self, song_ids: set[int]) -> int:
        songs = self.songs()
        doomed = [songs[i] for i in song_ids if i in songs]
        self.editor.remove_songs({s.id for s in doomed})
        for song in doomed:
            if song.location:
                self.files_to_delete.append(self.ipod.root / song.relative_path)
            self.deleted_dbids.add(song.dbid)
        return len(doomed)

    def copy_file(self, path: pathlib.Path) -> int:
        """Copy a song file from this computer onto the iPod. Returns its new song id."""
        extension = path.suffix.lower()
        if extension not in SUPPORTED:
            raise ManageError(f"The iPod can't play {extension} files.")
        target = _new_ipod_path(self.ipod, extension, _music_folders(self.ipod))
        tmp = target.with_name(target.name + ".part")
        shutil.copyfile(path, tmp)
        if tmp.stat().st_size != path.stat().st_size:
            tmp.unlink(missing_ok=True)
            raise ManageError(f"Copying {path.name} didn't verify.")
        os.replace(tmp, target)
        self.copied.append(target)
        location = ":" + target.relative_to(self.ipod.root).as_posix().replace("/", ":")
        song_id, dbid = self.editor.add_song(song_from_file(target, location, extension))
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
        for number, ids in enumerate(self.otg, start=1):
            name = f"On-The-Go {datetime.date.today():%Y-%m-%d}" + (f" ({number})" if len(self.otg) > 1 else "")
            self.create(name, ids)
        self.editor.finish()
        links = []
        art_path = self.ipod.control / "Artwork" / "ArtworkDB"
        if (self.art_jobs or self.deleted_dbids) and art_path.is_file():
            try:
                writer = artwork.ArtworkWriter(art_path.parent)
                if writer.usable:
                    mhits = {s.dbid: s.chunk for s in itunesdb.songs(self.db)}
                    links = [(mhits[d], writer.add(d, c), len(c)) for d, c in self.art_jobs if d in mhits]
                    writer.remove_for(self.deleted_dbids)
                    writer.commit()
            except (OSError, itunesdb.DatabaseError) as error:
                self.say(f"Album art was skipped ({error}).")
                links = []
        for mhit, image_id, size in links:
            artwork.link_song(mhit, image_id, size)
        data = itunesdb.serialize(self.db)
        if itunesdb.serialize(itunesdb.parse(data)) != data:
            raise ManageError("The changed database didn't verify, so nothing was changed.")
        _write_atomically(self.ipod.database_path, data)
        for path in self.files_to_delete:
            path.unlink(missing_ok=True)
        if self.counts_file is not None and self.counts_file.is_file():
            self.counts_file.unlink()
        for otg in playcounts.otg_files(self.itunes):
            otg.unlink()
        if self.deleted_dbids:
            self.state["tracks"] = {
                k: v for k, v in self.state["tracks"].items() if int(v["dbid"], 16) not in self.deleted_dbids
            }
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
    )


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


def delete_playlist(ipod: IPod, playlist_id: int, make_backup: bool = True) -> None:
    _run(ipod, make_backup, lambda s: s.delete_playlist(playlist_id))


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
