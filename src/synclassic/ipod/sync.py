"""Sync Synclassic's playlists to an iPod.

What a sync does:
- Songs already on the iPod are reused (matched by artist, title and length), so nothing is
  copied twice and their play counts stay. Everything else is copied.
- Each Synclassic playlist becomes an iPod playlist in the same order. An existing iPod
  playlist with the same name is taken over (filled with Synclassic's songs).
- Songs Synclassic copied that no playlist uses any more are removed, unless another iPod
  playlist still uses them. Music Synclassic didn't put there is never deleted.
- Play counts, skips and ratings from the iPod are merged in first.

Safety:
- The check (byte-for-byte rebuild) must pass first.
- The database, play counts and Synclassic's state are backed up before every sync.
- New files are copied and verified before the database is written; the new database is read
  back and checked, then swapped in atomically. Old files are deleted only after that.

What Synclassic manages is remembered on the iPod in iPod_Control/Synclassic/state.json.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import random
import re
import shutil
import string
from collections.abc import Callable
from dataclasses import dataclass, field

from synclassic.config import app_dir
from synclassic.ipod import itunesdb, playcounts
from synclassic.ipod.check import check
from synclassic.ipod.device import IPod
from synclassic.ipod.edit import Editor, NewSong
from synclassic.library import Library, LibraryPlaylist, LibraryTrack
from synclassic.podcasts import ipod_sync as podcast_sync
from synclassic.textnorm import core_title, normalize, split_artists

SUPPORTED = {".m4a": "m4a", ".mp3": "mp3", ".wav": "wav", ".aif": "aif", ".aiff": "aif", ".m4b": "m4a", ".aac": "m4a"}
KEEP_BACKUPS = 10
SPACE_MARGIN = 100 * 1024 * 1024

Say = Callable[[str], None]


class SyncError(RuntimeError):
    pass


# --- state kept on the iPod -------------------------------------------------------------------


def state_path(ipod: IPod) -> pathlib.Path:
    return ipod.control / "Synclassic" / "state.json"


def load_state(ipod: IPod) -> dict:
    try:
        state = json.loads(state_path(ipod).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.setdefault("version", 1)
    state.setdefault("tracks", {})  # library track key -> {"dbid": hex, "added": bool}
    state.setdefault("playlists", {})  # library playlist key -> {"id": hex, "name": str}
    return state


def save_state(ipod: IPod, state: dict) -> None:
    path = state_path(ipod)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def playlist_key(playlist: LibraryPlaylist) -> str:
    spec = playlist.spec
    return f"{spec.kind}|{spec.ref}|{spec.selector}"


# --- planning -----------------------------------------------------------------------------------


@dataclass
class PlannedPlaylist:
    library: LibraryPlaylist
    action: str  # "create", "update", "take over" or "wait" (left alone this time)
    ipod_id: int | None
    items: list[LibraryTrack]
    unchanged: bool = False
    missing: int = 0  # songs not on the iPod and not downloaded yet
    reused: int = 0
    copied: int = 0
    ipod_count: int = 0  # songs in the iPod's playlist now (take over / update)


@dataclass
class Plan:
    ipod: IPod
    copy: list[LibraryTrack] = field(default_factory=list)
    reuse: dict[str, int] = field(default_factory=dict)  # library key -> iPod dbid
    playlists: list[PlannedPlaylist] = field(default_factory=list)
    remove_songs: list[itunesdb.SongView] = field(default_factory=list)
    remove_playlists: list[tuple[str, int, str]] = field(default_factory=list)  # (key, id, name)
    skipped: list[tuple[LibraryTrack, str]] = field(default_factory=list)
    not_ready: set[str] = field(default_factory=set)  # songs neither on the iPod nor downloaded
    bytes_to_copy: int = 0
    bytes_to_free: int = 0
    free_bytes: int = 0
    play_counts_pending: bool = False
    art_backfill: list[tuple[int, LibraryTrack]] = field(default_factory=list)  # (dbid, item): added songs without art
    soundcheck: list[tuple[int, LibraryTrack]] = field(default_factory=list)  # (dbid, item): on the iPod, no Sound Check
    to_measure: int = 0  # songs whose loudness isn't known yet
    artwork_ok: bool = False  # the iPod's artwork database can be updated
    podcasts: podcast_sync.PodcastPlan = field(default_factory=podcast_sync.PodcastPlan)
    videos: list[tuple[str, LibraryTrack, pathlib.Path]] = field(default_factory=list)  # music videos to copy
    remove_videos: list[tuple[str, itunesdb.SongView]] = field(default_factory=list)
    convert: dict = field(default_factory=dict)  # library key -> convert.Target (converted while copying)
    chosen_only: bool = False  # only the playlists ticked for this iPod
    bitrate: int = 0  # this iPod's chosen size for new songs (0 = as they are)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.copy or self.remove_songs or self.remove_playlists or self.play_counts_pending
            or any(not p.unchanged and p.action != "wait" for p in self.playlists)
            or (self.artwork_ok and bool(self.art_backfill)) or self.podcasts.has_changes or bool(self.soundcheck)
            or bool(self.videos) or bool(self.remove_videos)
        )

    @property
    def fits(self) -> bool:
        return self.total_to_copy + SPACE_MARGIN <= self.free_bytes + self.total_to_free

    @property
    def only_adds(self) -> bool:
        """Nothing is removed or taken over (Update & Sync then goes ahead without asking)."""
        return (not self.remove_songs and not self.remove_playlists and not self.podcasts.remove
                and not self.remove_videos and all(p.action != "take over" for p in self.playlists))

    @property
    def total_to_copy(self) -> int:
        return self.bytes_to_copy + self.podcasts.bytes_to_copy + sum(p.stat().st_size for _, _, p in self.videos)

    @property
    def total_to_free(self) -> int:
        return self.bytes_to_free + self.podcasts.bytes_to_free + sum(s.size for _, s in self.remove_videos)

    def summary(self) -> list[str]:
        gb = 1000**3
        lines = [f"Sync plan for “{self.ipod.name}”" + (" (the playlists chosen for it)" if self.chosen_only else "") + ":"]
        lines.append(f"  Copy {len(self.copy)} songs ({self.bytes_to_copy / gb:.2f} GB)")
        if self.convert:
            kinds: dict[str, int] = {}
            for target in self.convert.values():
                kinds[target.describe()] = kinds.get(target.describe(), 0) + 1
            lines.append("  Convert while copying: " + ", ".join(f"{n} to {k}" for k, n in sorted(kinds.items())))
        lines.append(f"  Reuse {len(self.reuse)} songs already on the iPod")
        for planned in self.playlists:
            name = planned.library.name
            if planned.action == "wait":
                lines.append(
                    f"  ⏸ “{name}”: left as it is on the iPod ({planned.ipod_count} songs) until all its songs are "
                    f"ready: {planned.missing} not downloaded yet"
                )
                continue
            if planned.unchanged:
                lines.append(f"  · {name}: no changes ({len(planned.items)} songs)")
                continue
            verb = {"create": "Create", "update": "Update", "take over": "Take over iPod playlist"}[planned.action]
            detail = [f"{planned.reused} already on iPod", f"{planned.copied} to copy"]
            if planned.missing:
                detail.append(f"{planned.missing} not downloaded yet")
            line = f"  · {verb} “{name}”: {len(planned.items)} songs ({', '.join(detail)})"
            if planned.action in ("take over", "update") and planned.ipod_count and len(planned.items) < planned.ipod_count:
                line += f"; the iPod's version has {planned.ipod_count}"
            lines.append(line)
        if self.remove_playlists:
            lines.append(f"  Remove {len(self.remove_playlists)} playlists you removed from Synclassic"
                         + (" or didn't choose for this iPod" if self.chosen_only else "") + ": "
                         + ", ".join(name for _, _, name in self.remove_playlists))
        if self.remove_songs:
            lines.append(f"  Remove {len(self.remove_songs)} songs Synclassic added that no playlist uses "
                         f"({self.bytes_to_free / gb:.2f} GB)")
        art = sum(1 for item in self.copy) + len(self.art_backfill)
        if self.artwork_ok and art:
            lines.append(f"  Add album art for up to {art} songs" + (f" ({len(self.art_backfill)} added earlier without art)" if self.art_backfill else ""))
        elif art:
            lines.append("  ⚠ Album art can't be added on this iPod (its artwork database couldn't be read safely)")
        if self.soundcheck:
            lines.append(f"  Sound Check for {len(self.soundcheck)} songs already on the iPod")
        if self.to_measure:
            lines.append(f"  Measure the loudness of {self.to_measure} songs for Sound Check (once; about "
                         f"{max(1, round(self.to_measure * 0.4 / 60))} min)")
        if self.videos:
            lines.append(f"  Copy {len(self.videos)} music video{'s' if len(self.videos) != 1 else ''}")
        if self.remove_videos:
            lines.append(f"  Remove {len(self.remove_videos)} music videos of songs no playlist has any more")
        lines += self.podcasts.summary()
        if self.play_counts_pending:
            lines.append("  Save plays, skips and ratings from the iPod")
        for item, reason in self.skipped[:10]:
            lines.append(f"  ⚠ Skipped {item.track.display()}: {reason}")
        if len(self.skipped) > 10:
            lines.append(f"  ⚠ …and {len(self.skipped) - 10} more skipped")
        lines.append(f"  Free space after sync: {(self.free_bytes + self.total_to_free - self.total_to_copy) / gb:.1f} GB")
        if not self.fits:
            lines.append("  ✗ Not enough space on the iPod.")
            lines += [f"    {hint}" for hint in self.fit_hints()]
        if not self.has_changes:
            lines.append("  Everything is already in sync.")
        return lines

    @property
    def shortfall(self) -> int:
        """How many bytes too few there are (0 if it fits)."""
        return max(0, self.total_to_copy + SPACE_MARGIN - self.free_bytes - self.total_to_free)

    def fit_hints(self) -> list[str]:
        """Ways to make the sync fit."""
        from synclassic import convert

        gb = 1000**3
        hints = [f"About {self.shortfall / gb:.1f} GB too much."]
        for kbps in (256, 192, 128):
            if self.bitrate and kbps >= self.bitrate:
                continue
            target = convert.Target("aac", kbps)
            saved = 0
            for item in self.copy:
                path = pathlib.Path(item.file_path)  # type: ignore[arg-type]
                current = self.convert.get(item.key)
                if current is not None and current.codec == "aac" and current.kbps <= kbps:
                    continue
                now = convert.estimated_size(path, item.track.duration_ms, current)
                saved += max(0, now - convert.estimated_size(path, item.track.duration_ms, target))
            if saved >= self.shortfall:
                hints.append(f"Smaller files ({kbps} kbps) for this iPod would make it fit (saves about {saved / gb:.1f} GB).")
                break
        else:
            hints.append("Even the smallest files (128 kbps) wouldn't be enough on their own.")
        hints.append("Or choose fewer playlists for this iPod (iPod page → Choose Playlists…, "
                     "or synclassic ipod playlists).")
        return hints


def _match_key(artist: str, title: str) -> tuple[str, str]:
    parts = split_artists(artist)
    return normalize(parts[0] if parts else artist), core_title(title)


def songs_on_ipod(items: dict[str, LibraryTrack], db: itunesdb.Chunk, state: dict) -> dict[str, int]:
    """Which library songs are already on the iPod: library key -> the iPod song's dbid.
    Songs Synclassic put there are known by id; others match by artist, title and length."""
    songs = itunesdb.songs(db)
    by_dbid = {s.dbid: s for s in songs}
    by_text: dict[tuple[str, str], list[itunesdb.SongView]] = {}
    for song in songs:
        if song.media_type in (0, 1):
            by_text.setdefault(_match_key(song.artist, song.title), []).append(song)
    found: dict[str, int] = {}
    for key, item in items.items():
        known = state["tracks"].get(key)
        if known and int(known["dbid"], 16) in by_dbid:
            found[key] = int(known["dbid"], 16)
            continue
        duration = item.track.duration_ms
        best = None
        for song in by_text.get(_match_key(item.track.artist, item.track.title), []):
            diff = abs(song.length_ms - duration) if duration and song.length_ms else 0
            if diff <= 4000 and (best is None or diff < best[0]):
                best = (diff, song)
        if best:
            found[key] = best[1].dbid
    return found


def library_keys_on_ipod(library: Library, ipod: IPod) -> set[str]:
    """Keys of the library's songs that are on this iPod (for showing it, and skipping downloads)."""
    db = itunesdb.parse(ipod.database_path.read_bytes())
    items = {item.key: item for item in library.tracks_in_playlists()}
    return set(songs_on_ipod(items, db, load_state(ipod)))


def make_plan(library: Library, ipod: IPod, db: itunesdb.Chunk | None = None) -> Plan:
    from synclassic import convert
    from synclassic.config import load_settings
    from synclassic.ipod import prefs as ipod_prefs

    db = db if db is not None else itunesdb.parse(ipod.database_path.read_bytes())
    state = load_state(ipod)
    plan = Plan(ipod)
    chosen_prefs = ipod_prefs.load(ipod)
    chosen = ipod_prefs.chosen_playlists(library, ipod, chosen_prefs)
    plan.chosen_only, plan.bitrate = not chosen_prefs.all_playlists, chosen_prefs.bitrate
    lossless_to = load_settings().lossless_to
    plan.free_bytes = ipod.disk_usage()[1]
    plan.play_counts_pending = (ipod.control / "iTunes" / "Play Counts").is_file() or bool(
        playcounts.otg_files(ipod.control / "iTunes")
    )

    songs = itunesdb.songs(db)
    by_dbid = {s.dbid: s for s in songs}

    # Every song in a playlist, downloaded or not: a song that's already on the iPod doesn't
    # need a downloaded file to be put in a playlist.
    wanted: dict[str, LibraryTrack] = {}
    for playlist in chosen:
        for item in library.playlist_tracks(playlist):
            wanted.setdefault(item.key, item)

    plan.reuse = songs_on_ipod(wanted, db, state)
    for key, item in wanted.items():
        if key in plan.reuse:
            continue
        if not (item.has_file and item.status in ("downloaded", "review", "local")):
            plan.not_ready.add(key)  # not downloaded yet (or not found on YouTube)
            continue
        path = pathlib.Path(item.file_path)  # type: ignore[arg-type]
        target = convert.target_for(path, item.track.duration_ms, chosen_prefs.bitrate, lossless_to)
        if target is not None and not convert.available():
            if path.suffix.lower() not in SUPPORTED:
                plan.skipped.append((item, f"the iPod can't play {path.suffix} files, and converting needs ffmpeg"))
                plan.not_ready.add(key)
                continue
            target = None  # copied as it is
        size = convert.estimated_size(path, item.track.duration_ms, target)
        if not ipod.fits_file(size):
            plan.skipped.append((item, "it's 4 GB or bigger, more than a Windows-formatted (FAT32) iPod can hold"))
            plan.not_ready.add(key)
            continue
        if target is not None:
            plan.convert[key] = target
        plan.copy.append(item)
        plan.bytes_to_copy += size

    copying = {item.key for item in plan.copy}
    on_ipod = set(plan.reuse) | copying

    existing = {p.chunk.u64(0x1C): p for p in itunesdb.playlists(db, 2) if not p.is_master}
    owned_ids = {int(v["id"], 16) for v in state["playlists"].values()}
    from synclassic.sources.store import COLLECTION_KINDS

    for playlist in chosen:
        if playlist.spec.kind in COLLECTION_KINDS:
            continue  # Find Music albums and songs: on the iPod by album/artist, not as playlists
        key = playlist_key(playlist)
        items = [i for i in library.playlist_tracks(playlist) if i.key in on_ipod]
        known = state["playlists"].get(key)
        ipod_id = int(known["id"], 16) if known and int(known["id"], 16) in existing else None
        action = "update"
        if ipod_id is None:
            same_name = [
                pid for pid, p in existing.items()
                if p.name.casefold() == playlist.name.casefold() and not p.is_smart and not p.is_podcasts
                and pid not in owned_ids
            ]
            if same_name:
                ipod_id, action = same_name[0], "take over"
            else:
                action = "create"
        all_items = library.playlist_tracks(playlist)
        planned = PlannedPlaylist(playlist, action, ipod_id, items)
        planned.missing = sum(1 for i in all_items if i.key in plan.not_ready)
        planned.reused = sum(1 for i in items if i.key in plan.reuse)
        planned.copied = sum(1 for i in items if i.key in copying)
        planned.ipod_count = len(existing[ipod_id].track_ids) if ipod_id is not None else 0
        if action == "take over" and planned.missing:
            # Never replace an iPod playlist with an incomplete one: it would lose songs.
            planned.action = "wait"
        if ipod_id is not None and action == "update" and not copying.intersection(i.key for i in items):
            current = existing[ipod_id]
            dbids = [plan.reuse.get(i.key) for i in items]
            id_by_dbid = {s.dbid: s.id for s in songs}
            planned.unchanged = current.name == playlist.name and current.track_ids == [id_by_dbid.get(d) for d in dbids]
        plan.playlists.append(planned)

    library_keys = {playlist_key(p) for p in chosen if p.spec.kind not in COLLECTION_KINDS}
    for key, value in state["playlists"].items():
        pid = int(value["id"], 16)
        if key not in library_keys and pid in existing:
            plan.remove_playlists.append((key, pid, existing[pid].name))

    # Songs Synclassic added and nothing needs any more (and no other iPod playlist uses).
    kept_ids = set()
    removed_playlist_ids = {pid for _, pid, _ in plan.remove_playlists}
    planned_ids = {p.ipod_id for p in plan.playlists if p.ipod_id is not None and p.action != "wait"}
    for pid, playlist in existing.items():
        if pid not in removed_playlist_ids and pid not in planned_ids:
            kept_ids.update(playlist.track_ids)
    for key, value in state["tracks"].items():
        dbid = int(value["dbid"], 16)
        song = by_dbid.get(dbid)
        if song and value.get("added") and key not in on_ipod and song.id not in kept_ids:
            if dbid not in plan.reuse.values():
                plan.remove_songs.append(song)
                plan.bytes_to_free += song.size
    # Songs Synclassic added earlier that still have no album art (e.g. synced before art support).
    from synclassic.ipod import artwork as art

    art_dir = ipod.control / "Artwork"
    try:
        plan.artwork_ok = (art_dir / "ArtworkDB").is_file() and art.ArtworkWriter(art_dir).usable
    except (OSError, itunesdb.DatabaseError):
        plan.artwork_ok = False
    if plan.artwork_ok:
        for key, value in state["tracks"].items():
            song = by_dbid.get(int(value["dbid"], 16))
            item = wanted.get(key)
            if (value.get("added") and song is not None and item is not None and song.chunk.u8(0xA4) != 1
                    and item.has_file and song.dbid not in {s.dbid for s in plan.remove_songs}):
                plan.art_backfill.append((song.dbid, item))
    # Sound Check: songs from the library on the iPod without a value yet.
    if load_settings().sound_check:
        from synclassic.loudness import LoudnessCache

        cache = LoudnessCache(library)
        removing = {s.dbid for s in plan.remove_songs}
        for key, dbid in plan.reuse.items():
            song, item = by_dbid.get(dbid), wanted.get(key)
            if (song is not None and item is not None and item.has_file and dbid not in removing
                    and song.chunk.u32(0x4C) == 0 and len(song.chunk.header) > 0x50):
                plan.soundcheck.append((dbid, item))
        paths = [pathlib.Path(i.file_path) for _, i in plan.soundcheck] + [pathlib.Path(i.file_path) for i in plan.copy]  # type: ignore[arg-type]
        plan.to_measure = sum(1 for p in paths if not cache.known(p)[0])
    # Music videos of the playlists' songs (when that option is on).
    known_videos = state.setdefault("videos", {})
    if load_settings().music_videos:
        from synclassic.videos import Videos

        videos = Videos(library)
        for key, item in wanted.items():
            entry = known_videos.get(key)
            if entry and int(entry["dbid"], 16) in by_dbid:
                continue
            path = videos.path_for(item.id)
            if path is not None:
                plan.videos.append((key, item, path))
    for key, entry in known_videos.items():
        song = by_dbid.get(int(entry["dbid"], 16))
        if song is not None and key not in wanted:
            plan.remove_videos.append((key, song))
    plan.podcasts = podcast_sync.plan(library, ipod, db, state)
    return plan


# --- applying -----------------------------------------------------------------------------------


def backup_dir(ipod: IPod) -> pathlib.Path:
    serial = ipod.sysinfo.get("pszSerialNumber") or ipod.sysinfo.get("FirewireGuid") or ipod.name
    return app_dir() / "ipod-backups" / "".join(c for c in serial if c.isalnum() or c in "-_")


def backup(ipod: IPod) -> pathlib.Path:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder, number = backup_dir(ipod) / stamp, 2
    while folder.exists():
        folder, number = backup_dir(ipod) / f"{stamp}_{number}", number + 1
    folder.mkdir(parents=True)
    itunes = ipod.control / "iTunes"
    artwork_db = ipod.control / "Artwork" / "ArtworkDB"
    for path in [itunes / "iTunesDB", itunes / "Play Counts", state_path(ipod), artwork_db, *playcounts.otg_files(itunes)]:
        if path.is_file():
            shutil.copy2(path, folder / path.name)
    backups = sorted(p for p in backup_dir(ipod).iterdir() if p.is_dir())
    for old in backups[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)
    return folder


def list_backups(ipod: IPod) -> list[pathlib.Path]:
    folder = backup_dir(ipod)
    return sorted((p for p in folder.iterdir() if p.is_dir()), reverse=True) if folder.is_dir() else []


def restore(ipod: IPod, folder: pathlib.Path) -> None:
    """Put a backed-up database (and play counts/state) back on the iPod."""
    source = folder / "iTunesDB"
    itunesdb.parse(source.read_bytes())  # refuse to restore a damaged backup
    _write_atomically(ipod.database_path, source.read_bytes())
    for name in ("Play Counts",):
        if (folder / name).is_file():
            shutil.copy2(folder / name, ipod.control / "iTunes" / name)
    if (folder / "ArtworkDB").is_file():
        # Pixels are only ever appended to the .ithmb files, so the old ArtworkDB still matches them.
        _write_atomically(ipod.control / "Artwork" / "ArtworkDB", (folder / "ArtworkDB").read_bytes())
    if (folder / "state.json").is_file():
        state_path(ipod).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / "state.json", state_path(ipod))


def _write_atomically(path: pathlib.Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".synclassic-new")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _music_folders(ipod: IPod) -> list[pathlib.Path]:
    music = ipod.control / "Music"
    # "F00".."F49" (Windows-formatted iPods may have them in lowercase).
    folders = sorted(p for p in music.iterdir() if p.is_dir() and re.fullmatch(r"[Ff]\d\d", p.name)) \
        if music.is_dir() else []
    if not folders:
        folders = [music / f"F{n:02d}" for n in range(20)]
        for folder in folders:
            folder.mkdir(parents=True, exist_ok=True)
    return folders


def _new_ipod_path(ipod: IPod, extension: str, folders: list[pathlib.Path]) -> pathlib.Path:
    while True:
        name = "".join(random.choices(string.ascii_uppercase + string.digits, k=4)) + extension
        path = random.choice(folders) / name
        if not path.exists():
            return path


def song_info(path: pathlib.Path, item: LibraryTrack, location: str) -> NewSong:
    from mutagen import File as MutagenFile

    audio = MutagenFile(path)
    info = getattr(audio, "info", None)
    extension = path.suffix.lower()
    filetype = SUPPORTED.get(extension, "m4a")
    length = getattr(info, "length", 0) or (item.track.duration_ms or 0) / 1000
    bitrate = int((getattr(info, "bitrate", 0) or 0) / 1000)
    sample_rate = int(getattr(info, "sample_rate", 0) or 44100)
    vbr = False
    if filetype == "m4a" and str(getattr(info, "codec", "")).lower() == "alac":
        filetype = "alac"
    if filetype == "mp3":
        mode = getattr(info, "bitrate_mode", None)
        vbr = mode is not None and "VBR" in str(mode).upper()
    track = item.track
    year = int(track.year) if track.year and track.year.isdigit() else 0
    from synclassic import lyrics

    return NewSong(
        lyrics=bool(lyrics.read(path)),
        location=location,
        filetype=filetype,
        size=path.stat().st_size,
        length_ms=int(length * 1000),
        bitrate=bitrate,
        sample_rate=sample_rate,
        vbr=vbr,
        title=track.title,
        artist=track.artist,
        album=track.album,
        album_artist=track.album_artist or track.primary_artist,
        genre=track.genre,
        track_number=track.track_number or 0,
        disc_number=track.disc_number or 0,
        year=year,
        explicit=bool(track.explicit),
    )


def apply_plan(
    library: Library,
    plan: Plan,
    say: Say,
    should_stop: Callable[[], bool] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> pathlib.Path:
    """Run the sync. Returns the backup folder."""
    ipod = plan.ipod
    report = check(ipod)
    if not report.safe_to_manage:
        raise SyncError("The iPod check didn't pass, so nothing was changed: " + "; ".join(report.problems))
    if not plan.fits:
        raise SyncError("There isn't enough free space on the iPod for this sync.")

    say("Backing up the iPod's database…")
    saved = backup(ipod)
    raw = ipod.database_path.read_bytes()
    db = itunesdb.parse(raw)
    state = load_state(ipod)
    itunes = ipod.control / "iTunes"

    # 1. Merge what happened on the iPod (by song position, so before anything moves).
    otg_playlists: list[list[int]] = []
    counts_file = itunes / "Play Counts"
    if counts_file.is_file():
        try:
            entries = playcounts.parse_play_counts(counts_file.read_bytes())
            _queue_scrobbles(db, entries, say)
            changed = playcounts.merge_play_counts(db, entries)
            for song in itunesdb.songs(db):  # played podcast episodes lose their blue dot
                if song.media_type in (0x04, 0x06) and song.play_count and len(song.chunk.header) > 0xB2:
                    song.chunk.set_u8(0xB2, 1)
            say(f"Saved plays/ratings for {changed} songs from the iPod.")
        except ValueError:
            say("The iPod's play counts file couldn't be read; it was left alone.")
            counts_file = None  # type: ignore[assignment]
    songs_before = itunesdb.songs(db)
    for otg in playcounts.otg_files(itunes):
        positions = playcounts.parse_otg(otg.read_bytes())
        ids = [songs_before[p].id for p in positions if p < len(songs_before)]
        if ids:
            otg_playlists.append(ids)

    # 2. Copy new songs (verified) before touching the database.
    folders = _music_folders(ipod)
    editor = Editor(db)
    from synclassic.config import load_settings
    from synclassic.loudness import LoudnessCache

    loudness = LoudnessCache(library) if load_settings().sound_check else None
    new_dbids: dict[str, int] = {}
    copied_files: list[pathlib.Path] = []
    from synclassic import convert

    converter = convert.Converter([(item.key, pathlib.Path(item.file_path), plan.convert[item.key], item.track)  # type: ignore[arg-type]
                                   for item in plan.copy if item.key in plan.convert])
    try:
        for number, item in enumerate(plan.copy, start=1):
            if should_stop and should_stop():
                raise SyncError("Stopped before the database was changed. Nothing on the iPod was changed.")
            original = pathlib.Path(item.file_path)  # type: ignore[arg-type]
            source = original
            target_format = plan.convert.get(item.key)
            if target_format is not None:
                say(f"[{number}/{len(plan.copy)}] Converting {item.track.display()} to {target_format.describe()}")
                if progress:
                    progress(number, len(plan.copy), f"Converting {item.track.display()}")
                try:
                    source = converter.get(item.key)
                except convert.ConvertError as error:
                    raise SyncError(f"{error} Nothing on the iPod was changed.") from error
            target = _new_ipod_path(ipod, source.suffix.lower(), folders)
            say(f"[{number}/{len(plan.copy)}] Copying {item.track.display()}")
            if progress:
                progress(number, len(plan.copy), item.track.display())
            tmp = target.with_name(target.name + ".part")
            shutil.copyfile(source, tmp)
            if tmp.stat().st_size != source.stat().st_size:
                raise SyncError(f"Copy of {source.name} didn't verify.")
            os.replace(tmp, target)
            if source is not original:
                source.unlink(missing_ok=True)
                source = original  # loudness and the cover come from your own file
            copied_files.append(target)
            location = ":" + target.relative_to(ipod.root).as_posix().replace("/", ":")
            info = song_info(target, item, location)
            if loudness is not None:
                info.soundcheck = loudness.soundcheck(source)
            _, dbid = editor.add_song(info)
            new_dbids[item.key] = dbid
        new_videos: dict[str, int] = {}
        for number, (key, item, source) in enumerate(plan.videos, start=1):
            if should_stop and should_stop():
                raise SyncError("Stopped before the database was changed. Nothing on the iPod was changed.")
            target = _new_ipod_path(ipod, ".m4v", folders)
            say(f"[{number}/{len(plan.videos)}] Copying music video {item.track.display()}")
            if progress:
                progress(number, len(plan.videos), f"Video: {item.track.display()}")
            tmp = target.with_name(target.name + ".part")
            shutil.copyfile(source, tmp)
            if tmp.stat().st_size != source.stat().st_size:
                raise SyncError(f"Copy of {source.name} didn't verify.")
            os.replace(tmp, target)
            copied_files.append(target)
            location = ":" + target.relative_to(ipod.root).as_posix().replace("/", ":")
            info = song_info(target, item, location)
            info.filetype, info.music_video, info.lyrics = "m4v", True, False
            _, dbid = editor.add_song(info)
            new_videos[key] = dbid
        new_episodes: dict[str, int] = {}
        episode_files: dict[int, pathlib.Path] = {}
        episodes = plan.podcasts.copy
        for number, (podcast, episode) in enumerate(episodes, start=1):
            if should_stop and should_stop():
                raise SyncError("Stopped before the database was changed. Nothing on the iPod was changed.")
            source = pathlib.Path(episode.file_path)  # type: ignore[arg-type]
            target = _new_ipod_path(ipod, source.suffix.lower(), folders)
            say(f"[{number}/{len(episodes)}] Copying podcast {podcast.title}: {episode.title}")
            if progress:
                progress(number, len(episodes), f"{podcast.title}: {episode.title}")
            tmp = target.with_name(target.name + ".part")
            shutil.copyfile(source, tmp)
            if tmp.stat().st_size != source.stat().st_size:
                raise SyncError(f"Copy of {source.name} didn't verify.")
            os.replace(tmp, target)
            copied_files.append(target)
            dbid = podcast_sync.add_episode(editor, podcast, episode, target, ipod.root)
            new_episodes[podcast_sync.episode_key(podcast, episode)] = dbid
            episode_files[dbid] = source
    except BaseException:
        for path in copied_files:  # leave no orphan files behind
            path.unlink(missing_ok=True)
        raise
    finally:
        converter.close()

    # Sound Check for songs that were already on the iPod.
    if loudness is not None and plan.soundcheck:
        mhits = {s.dbid: s.chunk for s in itunesdb.songs(db)}
        for number, (dbid, item) in enumerate(plan.soundcheck, start=1):
            if should_stop and should_stop():
                break  # the rest get it next time
            if progress:
                progress(number, len(plan.soundcheck), f"Sound Check: {item.track.display()}")
            value = loudness.soundcheck(pathlib.Path(item.file_path))  # type: ignore[arg-type]
            if value and dbid in mhits:
                mhits[dbid].set_u32(0x4C, value)
        say(f"Sound Check set for {len(plan.soundcheck)} songs already on the iPod.")

    # 3. Playlists.
    songs_now = {s.dbid: s.id for s in itunesdb.songs(db)}
    dbid_for = {**plan.reuse, **new_dbids}
    for _, pid, name in plan.remove_playlists:
        say(f"Removing playlist “{name}”")
        editor.delete_playlist(pid)
    for planned in plan.playlists:
        if planned.unchanged or planned.action == "wait":
            continue
        pid = planned.ipod_id
        if pid is None:
            pid = editor.create_playlist(planned.library.name)
        else:
            editor.rename_playlist(pid, planned.library.name)
        entries = [(songs_now[dbid_for[i.key]], dbid_for[i.key]) for i in planned.items if i.key in dbid_for]
        editor.set_playlist_songs(pid, entries)
        state["playlists"][playlist_key(planned.library)] = {"id": f"{pid:016x}", "name": planned.library.name}
        say(f"Playlist “{planned.library.name}”: {len(entries)} songs")
    otg_named: list[tuple[str, list[int]]] = []
    for number, ids in enumerate(otg_playlists, start=1):
        otg_name = f"On-The-Go {datetime.date.today():%Y-%m-%d}" + (f" ({number})" if len(otg_playlists) > 1 else "")
        otg_named.append((otg_name, ids))
        pid = editor.create_playlist(otg_name)
        dbid_by_id = {s.id: s.dbid for s in itunesdb.songs(db)}
        editor.set_playlist_songs(pid, [(i, dbid_by_id[i]) for i in ids if i in dbid_by_id])
    for key, _, _ in plan.remove_playlists:
        state["playlists"].pop(key, None)

    # 4. Remove songs nothing needs any more.
    removed = ({song.id for song in plan.remove_songs} | {song.id for song in plan.podcasts.remove}
               | {song.id for _, song in plan.remove_videos})
    editor.remove_songs(removed)
    # Smart playlists Synclassic keeps: their songs refreshed (the iPod keeps them live after).
    from synclassic.ipod import smart

    if smart.chosen(state):
        smart.apply(editor, db, state)
    editor.finish()

    # 5. Album art: written before the music database, which then links to it.
    if plan.artwork_ok:
        _add_artwork(ipod, db, plan, new_dbids, say, episode_files)

    # 6. Write the new database, check it, swap it in.
    data = itunesdb.serialize(db)
    check_db = itunesdb.parse(data)
    if itunesdb.serialize(check_db) != data:
        raise SyncError("The new database didn't verify; nothing was changed. (Copied songs were left in place.)")
    final_songs = itunesdb.songs(check_db)
    expected = len(songs_before) + len(plan.copy) + len(new_episodes) + len(new_videos) - len(removed)
    if len(final_songs) != expected:
        raise SyncError("The new database has the wrong number of songs; nothing was changed.")
    for song in final_songs:
        if (song.dbid in new_dbids.values() or song.dbid in episode_files) and not (ipod.root / song.relative_path).is_file():
            raise SyncError(f"A copied song is missing on the iPod ({song.relative_path}); nothing was changed.")
    say("Writing the iPod's database…")
    from synclassic.ipod import signing

    _write_atomically(ipod.database_path, signing.finish(ipod, data, raw))

    # 7. Clean up: old files, merged play counts, On-The-Go files; remember what we manage.
    for song in [*plan.remove_songs, *plan.podcasts.remove, *(s for _, s in plan.remove_videos)]:
        (ipod.root / song.relative_path).unlink(missing_ok=True)
    if counts_file is not None and counts_file.is_file():
        counts_file.unlink()
    for otg in playcounts.otg_files(itunes):
        otg.unlink()
    if otg_named:
        _otg_to_spotify(library, db, state, otg_named, say)
    for key, dbid in plan.reuse.items():
        previous = state["tracks"].get(key, {})
        state["tracks"][key] = {"dbid": f"{dbid:016x}", "added": bool(previous.get("added") and int(previous["dbid"], 16) == dbid)}
    for key, dbid in new_dbids.items():
        state["tracks"][key] = {"dbid": f"{dbid:016x}", "added": True}
    removed_dbids = {song.dbid for song in plan.remove_songs}
    state["tracks"] = {k: v for k, v in state["tracks"].items() if int(v["dbid"], 16) not in removed_dbids}
    podcast_sync.remember(state, plan.podcasts, new_episodes)
    videos_state = state.setdefault("videos", {})
    for key, _ in plan.remove_videos:
        videos_state.pop(key, None)
    for key, dbid in new_videos.items():
        videos_state[key] = {"dbid": f"{dbid:016x}"}
    state["last_sync"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_state(ipod, state)
    if hasattr(os, "sync"):
        os.sync()
    say("Sync complete.")
    # Songs to keep only on the iPod: now that they're safely there, this Mac's copy can go.
    from synclassic import destinations

    paths = {song.dbid: ipod.root / song.relative_path for song in itunesdb.songs(db) if song.location}
    on_ipod = {key: paths[int(entry["dbid"], 16)] for key, entry in state["tracks"].items()
               if int(entry["dbid"], 16) in paths}
    destinations.release_mac_copies(library, on_ipod, say)
    return saved


def _add_artwork(ipod: IPod, db: itunesdb.Chunk, plan: Plan, new_dbids: dict[str, int], say: Say,
                 episode_files: dict[int, pathlib.Path] | None = None) -> None:
    from synclassic.ipod import artwork as art

    jobs: list[tuple[int, pathlib.Path]] = [
        (new_dbids[item.key], pathlib.Path(item.file_path)) for item in plan.copy if item.key in new_dbids  # type: ignore[arg-type]
    ]
    jobs += [(dbid, pathlib.Path(item.file_path)) for dbid, item in plan.art_backfill]  # type: ignore[arg-type]
    jobs += list((episode_files or {}).items())
    removed_dbids = {song.dbid for song in plan.remove_songs} | {song.dbid for song in plan.podcasts.remove}
    if not jobs and not removed_dbids:
        return
    try:
        writer = art.ArtworkWriter(ipod.control / "Artwork")
        mhits = {s.dbid: s.chunk for s in itunesdb.songs(db)}
        links = []
        for dbid, path in jobs:
            cover = art.embedded_cover(path)
            if not cover and episode_files and dbid in episode_files and (path.parent / "cover.jpg").is_file():
                cover = (path.parent / "cover.jpg").read_bytes()  # the show's artwork
            if cover and dbid in mhits:
                try:
                    links.append((mhits[dbid], writer.add(dbid, cover), len(cover)))
                except Exception as error:  # one unreadable image shouldn't stop the others
                    say(f"  (no album art for {path.name}: {error})")
        writer.remove_for(removed_dbids)
        if links or removed_dbids:
            writer.commit()
        for mhit, image_id, size in links:
            art.link_song(mhit, image_id, size)
        if links:
            say(f"Added album art for {len(links)} songs.")
    except (OSError, itunesdb.DatabaseError) as error:
        say(f"Album art was skipped this time ({error}); the music sync continues.")


def _otg_to_spotify(library, db: itunesdb.Chunk, state: dict, playlists: list[tuple[str, list[int]]], say: Say) -> None:
    """Queue On-The-Go playlists for Spotify (when that's on), with the Spotify ids Synclassic knows."""
    from synclassic import otg_spotify

    try:
        if not otg_spotify.wanted():
            return
        by_id = {s.id: s for s in itunesdb.songs(db)}
        spotify_ids: dict[int, str] = {}
        if library is not None:
            items = {item.key: item for item in library.tracks_in_playlists()}
            for key, entry in state["tracks"].items():
                item = items.get(key)
                if item is not None and item.track.spotify_id:
                    spotify_ids[int(entry["dbid"], 16)] = item.track.spotify_id
        for name, ids in playlists:
            if otg_spotify.capture(name, [by_id[i] for i in ids if i in by_id], spotify_ids):
                say(f"“{name}” will be made on Spotify too.")
    except OSError as error:
        say(f"(Couldn't keep the On-The-Go playlists for Spotify: {error})")


def _queue_scrobbles(db: itunesdb.Chunk, entries, say: Say) -> None:
    """Keep the plays for Last.fm before they're merged (the Play Counts file is deleted after)."""
    from synclassic import scrobble

    try:
        added = scrobble.capture(itunesdb.songs(db), entries)
        loves = scrobble.capture_loves(itunesdb.songs(db), entries)
    except OSError as error:
        say(f"(Couldn't save plays for Last.fm: {error})")
        return
    if added:
        say(f"{added} plays saved for scrobbling to Last.fm.")
    if loves:
        say(f"{loves} rating change{'s' if loves != 1 else ''} saved for Last.fm (5 stars = loved).")


@dataclass
class EjectResult:
    ok: bool
    message: str
    blocked_by: str = ""  # the app keeping the iPod busy, when macOS names one


def _blocker(text: str) -> str:
    """macOS says e.g. "dissented by PID 720 (/System/.../AMPDeviceDiscoveryAgent)"."""
    import re

    found = re.search(r"dissented by PID \d+ \(([^)]*)\)", text)
    return found.group(1).rsplit("/", 1)[-1] if found else ""


def _whole_disk(root: pathlib.Path, run) -> str:
    import plistlib

    result = run(["diskutil", "info", "-plist", str(root)])
    try:
        info = plistlib.loads(result.stdout.encode() if isinstance(result.stdout, str) else result.stdout)
    except Exception:
        return ""
    return info.get("ParentWholeDisk") or info.get("DeviceIdentifier") or ""


def eject_ipod(ipod: IPod, force: bool = False, attempts: int = 4, wait: float = 1.5, run=None,
               sleep=None) -> EjectResult:
    """Eject safely (macOS). macOS's own device agent (AMPDeviceDiscoveryAgent, the Finder's
    iPod/iPhone helper) often looks at an iPod right after it changes and briefly refuses the
    eject, so this tries a few times. `force` unmounts even while something holds it (everything
    Synclassic wrote is flushed to the iPod first)."""
    import subprocess
    import sys
    import time

    if sys.platform != "darwin" and run is None:
        return EjectResult(False, "Eject the iPod from your file manager before unplugging it.")
    run = run or (lambda args: subprocess.run(args, capture_output=True, text=True))
    sleep = sleep or time.sleep
    if hasattr(os, "sync"):
        os.sync()
    disk = _whole_disk(ipod.root, run)
    if force:
        if not disk:
            return EjectResult(False, "Couldn't find the iPod's disk to force it to eject.")
        result = run(["diskutil", "unmountDisk", "force", disk])
        if result.returncode == 0:
            run(["diskutil", "eject", disk])
            return EjectResult(True, "Ejected. You can unplug the iPod.")
        text = (result.stderr or result.stdout or "").strip()
        return EjectResult(False, f"Couldn't eject: {text}", _blocker(text))
    text = ""
    for attempt in range(attempts):
        result = run(["diskutil", "eject", disk or str(ipod.root)])
        if result.returncode == 0:
            return EjectResult(True, "Ejected. You can unplug the iPod.")
        text = (result.stderr or result.stdout or "").strip()
        if attempt + 1 < attempts:
            sleep(wait)
    return EjectResult(False, f"Couldn't eject: {text}", _blocker(text))


def eject(ipod: IPod, force: bool = False) -> str:
    """Eject safely (macOS). Returns a message."""
    result = eject_ipod(ipod, force)
    if not result.ok and result.blocked_by:
        return (f"Couldn't eject: {result.blocked_by} is still using the iPod. Try again in a moment, "
                "or force it: synclassic ipod eject --force")
    return result.message
