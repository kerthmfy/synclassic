"""Rockbox mode: syncing to an iPod that runs Rockbox (the open-source firmware) instead of
Apple's.

Rockbox doesn't read Apple's music database. It plays files from folders and reads .m3u8
playlists, so a Rockbox sync:
- copies songs to Music/<Artist>/<Album>/<NN Title>.<ext> (readable names, safe on FAT32),
- puts the album's cover next to them as cover.jpg (what Rockbox shows),
- writes each playlist as Playlists/<Name>.m3u8, in order,
- removes only the files and playlists it added, when nothing needs them any more.
Rockbox plays FLAC, so files are copied as they are (unless you chose smaller files for this
iPod). What Synclassic added is remembered in
.synclassic/state.json on the iPod. After a sync, Rockbox's database updates by itself if
"Auto Update" is on (Settings → General → Database), or with "Update Now".

An iPod with both Rockbox and Apple's firmware (dual boot) is synced for Apple's unless you
choose Rockbox (the choice is kept on the iPod).
"""

from __future__ import annotations

import datetime
import io
import json
import os
import pathlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field

from synclassic.library import Library, LibraryTrack
from synclassic.textnorm import safe_filename

APPLE, ROCKBOX = "apple", "rockbox"
FOLDER = ".synclassic"
SPACE_MARGIN = 100 * 1024 * 1024

Say = Callable[[str], None]


def is_rockbox(root: pathlib.Path) -> bool:
    return (root / ".rockbox").is_dir()


def mode(ipod) -> str:
    """Which firmware this iPod is synced for."""
    if not is_rockbox(ipod.root):
        return APPLE
    if not ipod.database_path.is_file():
        return ROCKBOX  # Rockbox only
    from synclassic.ipod import prefs

    return ROCKBOX if prefs.load(ipod).mode == ROCKBOX else APPLE


def set_mode(ipod, value: str) -> None:
    from synclassic.ipod import prefs

    prefs.update(ipod, mode=value)


def _state_path(root: pathlib.Path) -> pathlib.Path:
    return root / FOLDER / "state.json"


def load_state(root: pathlib.Path) -> dict:
    try:
        state = json.loads(_state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.setdefault("files", {})  # library track key -> relative path on the iPod
    state.setdefault("playlists", {})  # library playlist key -> relative path of its .m3u8
    return state


def save_state(root: pathlib.Path, state: dict) -> None:
    path = _state_path(root)
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _name(text: str, fallback: str, limit: int = 80) -> str:
    """A file or folder name that's safe on FAT32 (no trailing dots or spaces either)."""
    name = safe_filename(text or fallback, limit).strip().rstrip(". ") or fallback
    return name


def song_path(item: LibraryTrack, extension: str) -> str:
    track = item.track
    artist = _name(track.album_artist or track.primary_artist, "Unknown Artist")
    album = _name(track.album, "Singles")
    number = f"{track.track_number:02d} " if track.track_number and track.album else ""
    return f"Music/{artist}/{album}/{_name(number + track.title, 'Untitled', 100)}{extension}"


@dataclass
class RockboxPlan:
    ipod: object
    copy: list[tuple[LibraryTrack, str]] = field(default_factory=list)  # (song, relative path)
    keep: dict[str, str] = field(default_factory=dict)  # track key -> relative path already there
    playlists: dict[str, tuple[str, str]] = field(default_factory=dict)  # key -> (relative path, contents)
    changed_playlists: list[str] = field(default_factory=list)
    remove_files: list[str] = field(default_factory=list)
    remove_playlists: list[str] = field(default_factory=list)
    skipped: list[tuple[LibraryTrack, str]] = field(default_factory=list)
    convert: dict = field(default_factory=dict)  # track key -> convert.Target (smaller copies)
    bytes_to_copy: int = 0
    bytes_to_free: int = 0
    free_bytes: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.copy or self.changed_playlists or self.remove_files or self.remove_playlists)

    @property
    def only_adds(self) -> bool:
        return not self.remove_files and not self.remove_playlists

    @property
    def fits(self) -> bool:
        return self.bytes_to_copy + SPACE_MARGIN <= self.free_bytes + self.bytes_to_free

    def summary(self) -> list[str]:
        gb = 1000**3
        lines = [f"Rockbox sync for “{self.ipod.name}”:",
                 f"  Copy {len(self.copy)} songs ({self.bytes_to_copy / gb:.2f} GB)",
                 f"  {len(self.keep)} songs already there"]
        if self.convert:
            lines.append(f"  Convert {len(self.convert)} songs to {next(iter(self.convert.values())).describe()} while copying")
        if self.changed_playlists:
            lines.append(f"  Write {len(self.changed_playlists)} playlists: " + ", ".join(
                pathlib.Path(self.playlists[k][0]).stem for k in self.changed_playlists[:8]))
        if self.remove_playlists:
            lines.append(f"  Remove {len(self.remove_playlists)} playlists you removed from Synclassic")
        if self.remove_files:
            lines.append(f"  Remove {len(self.remove_files)} songs Synclassic added that no playlist uses "
                         f"({self.bytes_to_free / gb:.2f} GB)")
        for item, reason in self.skipped[:10]:
            lines.append(f"  ⚠ Skipped {item.track.display()}: {reason}")
        lines.append(f"  Free space after sync: {(self.free_bytes + self.bytes_to_free - self.bytes_to_copy) / gb:.1f} GB")
        if not self.fits:
            lines.append("  ✗ Not enough space on the iPod.")
        if not self.has_changes:
            lines.append("  Everything is already in sync.")
        return lines


def _playlist_text(items: list[tuple[LibraryTrack, str]]) -> str:
    lines = ["#EXTM3U"]
    for item, relative in items:
        seconds = round((item.track.duration_ms or 0) / 1000) or -1
        lines.append(f"#EXTINF:{seconds},{item.track.display()}")
        lines.append("/" + relative)
    return "\n".join(lines) + "\n"


def make_plan(library: Library, ipod, playlists=None) -> RockboxPlan:
    """What a Rockbox sync would do (nothing is changed). `playlists`: which library playlists
    (default: the ones chosen for this iPod)."""
    from synclassic import convert
    from synclassic.ipod import prefs as ipod_prefs
    from synclassic.ipod.sync import playlist_key
    from synclassic.sources.store import COLLECTION_KINDS

    root = ipod.root
    state = load_state(root)
    plan = RockboxPlan(ipod)
    plan.free_bytes = ipod.disk_usage()[1]
    chosen_prefs = ipod_prefs.load(ipod)
    chosen = playlists if playlists is not None else ipod_prefs.chosen_playlists(library, ipod, chosen_prefs)
    taken: set[str] = {p.casefold() for p in state["files"].values()}
    placed: dict[str, str] = {}  # track key -> relative path (this sync)
    for playlist in chosen:
        entries = []
        for item in library.playlist_tracks(playlist):
            if item.key not in placed:
                known = state["files"].get(item.key)
                if known and (root / known).is_file():
                    placed[item.key] = known
                    plan.keep[item.key] = known
                elif item.has_file:
                    source = pathlib.Path(item.file_path)  # type: ignore[arg-type]
                    target = convert.target_for(source, item.track.duration_ms, chosen_prefs.bitrate, apple=False)
                    if target is not None and not convert.available():
                        target = None
                    size = convert.estimated_size(source, item.track.duration_ms, target)
                    if not ipod.fits_file(size):
                        plan.skipped.append((item, "it's 4 GB or bigger (FAT32 can't hold it)"))
                        continue
                    extension = ".m4a" if target is not None else source.suffix.lower()
                    relative = song_path(item, extension)
                    stem, number = relative.rsplit(".", 1)[0], 2
                    while relative.casefold() in taken or (root / relative).exists():
                        relative = f"{stem} ({number}){extension}"
                        number += 1
                    taken.add(relative.casefold())
                    placed[item.key] = relative
                    plan.copy.append((item, relative))
                    if target is not None:
                        plan.convert[item.key] = target
                    plan.bytes_to_copy += size
                else:
                    continue  # not downloaded yet
            entries.append((item, placed[item.key]))
        if playlist.spec.kind in COLLECTION_KINDS:
            continue  # albums and songs from Find Music: on the iPod, not as playlists
        key = playlist_key(playlist)
        relative = state["playlists"].get(key) or f"Playlists/{_name(playlist.name, 'Playlist')}.m3u8"
        text = _playlist_text(entries)
        plan.playlists[key] = (relative, text)
        try:
            current = (root / relative).read_text(encoding="utf-8")
        except OSError:
            current = None
        if current != text:
            plan.changed_playlists.append(key)
    for key, relative in state["files"].items():
        if key not in placed and (root / relative).is_file():
            plan.remove_files.append(relative)
            plan.bytes_to_free += (root / relative).stat().st_size
    for key, relative in state["playlists"].items():
        if key not in plan.playlists and (root / relative).is_file():
            plan.remove_playlists.append(relative)
    return plan


def _cover_jpeg(source: pathlib.Path) -> bytes | None:
    """The song's cover as a baseline JPEG (Rockbox can't show progressive ones), 500 px."""
    from PIL import Image

    from synclassic.ipod.artwork import embedded_cover

    data = embedded_cover(source)
    if not data:
        return None
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image.thumbnail((500, 500))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=88, progressive=False, optimize=False)
        return out.getvalue()
    except Exception:
        return None


def apply(library: Library, plan: RockboxPlan, say: Say, should_stop=None, progress=None) -> None:
    root = plan.ipod.root
    if not plan.fits:
        from synclassic.ipod.sync import SyncError

        raise SyncError("There isn't enough free space on the iPod for this sync.")
    state = load_state(root)
    from synclassic import convert

    with convert.Converter([(item.key, pathlib.Path(item.file_path), plan.convert[item.key], item.track)  # type: ignore[arg-type]
                            for item, _ in plan.copy if item.key in plan.convert]) as converter:
        for number, (item, relative) in enumerate(plan.copy, start=1):
            if should_stop and should_stop():
                say("Stopped. Sync again to copy the rest.")
                break
            original = pathlib.Path(item.file_path)  # type: ignore[arg-type]
            source = original
            if item.key in plan.convert:
                say(f"[{number}/{len(plan.copy)}] Converting {item.track.display()}")
                source = converter.get(item.key)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            say(f"[{number}/{len(plan.copy)}] Copying {item.track.display()}")
            if progress:
                progress(number, len(plan.copy), item.track.display())
            partial = target.with_name(target.name + ".part")
            shutil.copyfile(source, partial)
            if partial.stat().st_size != source.stat().st_size:
                partial.unlink(missing_ok=True)
                raise OSError(f"Copy of {source.name} didn't verify.")
            os.replace(partial, target)
            if source is not original:
                source.unlink(missing_ok=True)
            state["files"][item.key] = relative
            cover = target.parent / "cover.jpg"
            if not cover.exists():
                data = _cover_jpeg(original)
                if data:
                    cover.write_bytes(data)
            save_state(root, state)  # after each song: a stopped sync knows what it copied
    for key, (relative, text) in plan.playlists.items():
        if key in plan.changed_playlists:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".part")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, target)
        state["playlists"][key] = relative
    for relative in plan.remove_playlists:
        (root / relative).unlink(missing_ok=True)
    state["playlists"] = {k: v for k, v in state["playlists"].items() if v not in plan.remove_playlists}
    for relative in plan.remove_files:
        path = root / relative
        path.unlink(missing_ok=True)
        _remove_empty_folders(path.parent, root / "Music")
    state["files"] = {k: v for k, v in state["files"].items() if v not in plan.remove_files}
    state["last_sync"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_state(root, state)
    if hasattr(os, "sync"):
        os.sync()
    say("Rockbox sync complete. On the iPod: Settings → General → Database → Update Now (or turn on Auto Update).")


def _remove_empty_folders(folder: pathlib.Path, stop: pathlib.Path) -> None:
    """Remove album and artist folders a removal left empty (a folder with only its cover.jpg counts as empty)."""
    while folder != stop and stop in folder.parents:
        leftovers = [p for p in folder.iterdir() if p.name not in ("cover.jpg",) and not p.name.startswith("._")]
        if leftovers:
            return
        for p in folder.iterdir():
            p.unlink(missing_ok=True)
        folder.rmdir()
        folder = folder.parent
