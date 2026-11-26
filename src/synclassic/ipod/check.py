"""A read-only health check of an iPod: what's on it, and whether Synclassic can manage it safely.

Nothing on the iPod is changed.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

from synclassic.ipod import itunesdb
from synclassic.ipod.device import IPod

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".m4p", ".aac", ".wav", ".aif", ".aiff", ".mp4", ".m4v", ".mov", ".alac"}


@dataclass
class CheckReport:
    ipod: IPod
    database_size: int = 0
    songs: int = 0
    videos: int = 0
    podcasts: int = 0
    other_media: int = 0
    playlists: list[tuple[str, int, bool]] = field(default_factory=list)  # (name, songs, smart)
    podcast_playlists: int = 0
    missing_files: list[str] = field(default_factory=list)
    unlisted_files: int = 0
    round_trip_ok: bool = False
    hashed_database: bool = False
    play_counts_file: bool = False
    artwork_db: bool = False
    sections: list[int] = field(default_factory=list)
    index_types: list[int] = field(default_factory=list)
    jump_tables: dict[int, bool] = field(default_factory=dict)
    write_test: str = ""  # "" = not run, "ok", or what went wrong
    problems: list[str] = field(default_factory=list)

    @property
    def safe_to_manage(self) -> bool:
        return self.round_trip_ok and not self.hashed_database and not self.problems


def check(ipod: IPod) -> CheckReport:
    report = CheckReport(ipod)
    path = ipod.database_path
    if not path.is_file():
        report.problems.append("There's no music database on this iPod yet (it may be new or freshly restored).")
        return report
    raw = path.read_bytes()
    report.database_size = len(raw)
    try:
        db = itunesdb.parse(raw)
    except itunesdb.DatabaseError as error:
        report.problems.append(f"The music database couldn't be read: {error}")
        return report

    report.round_trip_ok = itunesdb.serialize(db) == raw
    if not report.round_trip_ok:
        report.problems.append("Synclassic couldn't rebuild the database exactly, so it won't write to this iPod yet.")
    # Later iPods (Classic 6G+, nano 3G+) need a signed database; the 5/5.5G doesn't.
    report.hashed_database = db.u16(0x30) != 0 or any(db.header[0x58:0x6C])
    if report.hashed_database:
        report.problems.append("This iPod's database is signed (a newer iPod model); Synclassic only writes to 5G/5.5G iPods.")
    report.sections = [mhsd.u32(0x0C) for mhsd in db.find(b"mhsd")]

    songs = itunesdb.songs(db)
    for song in songs:
        media = song.media_type
        # Media type bits: 0x01 audio, 0x02 movie, 0x04 podcast, 0x08 audiobook, 0x20 music video, 0x40 TV show.
        if media in (0, 1):
            report.songs += 1
        elif media & 0x04:
            report.podcasts += 1
        elif media & (0x02 | 0x20 | 0x40):
            report.videos += 1
        else:
            report.other_media += 1
        if song.location:
            if not (ipod.root / song.relative_path).is_file():
                report.missing_files.append(song.relative_path)

    for playlist in itunesdb.playlists(db, 2):
        if not playlist.is_master:
            report.playlists.append((playlist.name, len(playlist.track_ids), playlist.is_smart))
    report.podcast_playlists = sum(1 for p in itunesdb.playlists(db, 3) if not p.is_master)

    listed = {song.relative_path.lower() for song in songs if song.location}
    music = ipod.control / "Music"
    if music.is_dir():
        for file in music.rglob("*"):
            if file.suffix.lower() in AUDIO_EXTENSIONS and file.is_file():
                relative = file.relative_to(ipod.root).as_posix().lower()
                if relative not in listed:
                    report.unlisted_files += 1

    masters = [p for p in itunesdb.playlists(db, 2) if p.is_master]
    if masters:
        from synclassic.ipod import library_index

        report.index_types = library_index.index_sort_types(masters[0].chunk)[0]
        report.jump_tables = library_index.jump_tables_match(
            masters[0].chunk, masters[0].track_ids, {s.id: s for s in songs}
        )
    if report.round_trip_ok and not report.hashed_database:
        report.write_test = write_self_test(raw)
        if report.write_test != "ok":
            report.problems.append(f"The in-memory write test failed: {report.write_test}")

    report.play_counts_file = (ipod.control / "iTunes" / "Play Counts").is_file()
    report.artwork_db = (ipod.control / "Artwork" / "ArtworkDB").is_file()
    return report


def write_self_test(raw: bytes) -> str:
    """On a copy in memory: add a song and a playlist, rebuild indexes, write and re-read.

    Returns "ok" or a description of the problem. The iPod is never touched.
    """
    import struct

    from synclassic.ipod.edit import Editor, NewSong

    try:
        db = itunesdb.parse(raw)
        before = itunesdb.songs(db)
        editor = Editor(db)
        new_id, dbid = editor.add_song(NewSong(
            location=":iPod_Control:Music:F00:TEST.m4a", filetype="m4a", size=1000, length_ms=200000,
            bitrate=256, sample_rate=44100, vbr=False, title="Synclassic Test", artist="Synclassic",
            album="Test Album", album_artist="Synclassic",
        ))
        pid = editor.create_playlist("Synclassic Test Playlist")
        editor.set_playlist_songs(pid, [(new_id, dbid)] + [(s.id, s.dbid) for s in before[:2]])
        editor.finish()
        data = itunesdb.serialize(db)
        again = itunesdb.parse(data)
        if itunesdb.serialize(again) != data:
            return "the edited database didn't read back identically"
        songs = itunesdb.songs(again)
        if len(songs) != len(before) + 1 or songs[-1].title != "Synclassic Test":
            return "the test song wasn't stored correctly"
        for kind in (2, 3):
            if itunesdb.section(again, kind) is None:
                continue
            lists = itunesdb.playlists(again, kind)
            test = [p for p in lists if p.name == "Synclassic Test Playlist"]
            if len(test) != 1 or test[0].track_ids[0] != new_id:
                return f"the test playlist wasn't stored correctly in section {kind}"
            master = next(p for p in lists if p.is_master)
            if new_id not in master.track_ids:
                return "the test song is missing from the main library"
            for child in master.chunk.children:
                if child.tag == b"mhod" and child.mhod_type == 52:
                    count = struct.unpack_from("<I", child.body, 4)[0]
                    positions = struct.unpack_from(f"<{count}I", child.body, 48)
                    if sorted(positions) != list(range(len(master.track_ids))):
                        return "a menu index didn't cover every song after the change"
        return "ok"
    except Exception as error:  # report, never crash the check
        return f"{error.__class__.__name__}: {error}"


def format_report(report: CheckReport) -> list[str]:
    ipod = report.ipod
    total, free = ipod.disk_usage()
    gb = 1000**3
    lines = [
        f"iPod “{ipod.name}” at {ipod.root}",
        f"  Model:    {ipod.model}" + (f" · firmware {ipod.firmware}" if ipod.firmware else ""),
        f"  Storage:  {total / gb:.1f} GB, {free / gb:.1f} GB free",
    ]
    if ipod.is_supported_model is False:
        lines.append("  ⚠ Synclassic is built for the 5th/5.5th generation iPod (Video).")
    if report.database_size:
        kinds = [f"{report.songs} songs"]
        if report.videos:
            kinds.append(f"{report.videos} videos")
        if report.podcasts:
            kinds.append(f"{report.podcasts} podcast episodes")
        if report.other_media:
            kinds.append(f"{report.other_media} other")
        lines.append(f"  Library:  {', '.join(kinds)}")
        smart = sum(1 for _, _, is_smart in report.playlists if is_smart)
        lines.append(
            f"  Playlists: {len(report.playlists)}" + (f" ({smart} smart)" if smart else "")
            + (f", plus {report.podcast_playlists} podcast" if report.podcast_playlists else "")
        )
        for name, count, is_smart in report.playlists[:15]:
            lines.append(f"      • {name} ({count} songs{', smart' if is_smart else ''})")
        if len(report.playlists) > 15:
            lines.append(f"      …and {len(report.playlists) - 15} more")
        lines.append(f"  Database: {report.database_size / 1024:.0f} KB, sections {report.sections}")
        if report.missing_files:
            lines.append(f"  ⚠ {len(report.missing_files)} songs are listed but their file is missing on the iPod")
        if report.unlisted_files:
            lines.append(f"  · {report.unlisted_files} music files on the iPod aren't in the database (leftovers; they use space)")
        names = {3: "songs", 4: "albums", 5: "artists", 7: "genres", 0x12: "composers", 0x1D: "shows",
                 0x1E: "seasons", 0x1F: "episodes", 0x23: "album artists", 0x24: "album groups"}
        if report.index_types:
            lines.append("  · Menu indexes: " + ", ".join(names.get(t, str(t)) for t in report.index_types))
        if report.jump_tables:
            good = sum(report.jump_tables.values())
            line = f"  · Letter-jump tables reproduced: {good} of {len(report.jump_tables)}"
            differ = [names.get(t, str(t)) for t, ok in report.jump_tables.items() if not ok]
            if differ:
                line += f" (differ: {', '.join(differ)}; existing songs keep the iPod's letters)"
            lines.append(line)
        lines.append(f"  · Play counts waiting to be read: {'yes' if report.play_counts_file else 'no'}")
        lines.append(f"  · Album artwork database: {'yes' if report.artwork_db else 'no'}")
        lines.append("")
        lines.append(
            "  ✓ Synclassic can rebuild this database byte-for-byte."
            if report.round_trip_ok
            else "  ✗ Synclassic could NOT rebuild this database exactly."
        )
        if report.write_test == "ok":
            lines.append("  ✓ Write test passed (a test song and playlist, added in memory only).")
    for problem in report.problems:
        lines.append(f"  ✗ {problem}")
    if report.safe_to_manage:
        lines.append("  ✓ Safe for Synclassic to manage. (Nothing was changed on your iPod.)")
    return lines


def song_paths(ipod: IPod) -> list[pathlib.Path]:
    db = itunesdb.parse(ipod.database_path.read_bytes())
    return [ipod.root / s.relative_path for s in itunesdb.songs(db) if s.location]
