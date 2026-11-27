"""Sound Check for songs on the iPod that Synclassic didn't add (from iTunes, other apps).

Synclassic's own songs get their value at sync. These are measured from their file on the iPod
and their value is written into the iPod's library (the files themselves aren't changed).
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable

from synclassic.ipod import itunesdb
from synclassic.ipod.device import IPod
from synclassic.loudness import LoudnessError, measure, soundcheck


def missing(ipod: IPod) -> list[itunesdb.SongView]:
    """Music on the iPod without a Sound Check value."""
    db = itunesdb.parse(ipod.database_path.read_bytes())
    return [s for s in itunesdb.songs(db)
            if s.media_type in (0, 1) and s.location and len(s.chunk.header) > 0x50 and s.chunk.u32(0x4C) == 0]


def apply(ipod: IPod, make_backup: bool = True, say: Callable[[str], None] = lambda s: None,
          progress=None, should_stop: Callable[[], bool] | None = None,
          measure_file: Callable[[pathlib.Path], float | None] = measure) -> int:
    """Measure and set Sound Check for those songs. Returns how many got a value. Stopping
    keeps what was measured so far."""
    from synclassic.ipod.manage import EditSession

    songs = missing(ipod)
    values: dict[int, int] = {}
    for number, song in enumerate(songs, start=1):
        if should_stop and should_stop():
            break
        if progress:
            progress(number, len(songs), f"{song.artist} - {song.title}")
        try:
            value = soundcheck(measure_file(ipod.root / song.relative_path))
        except (LoudnessError, OSError) as error:
            say(f"  {song.title}: {error}")
            continue
        if value:
            values[song.dbid] = value
    if not values:
        return 0
    session = EditSession(ipod, say, make_backup)
    for song in itunesdb.songs(session.db):
        if song.dbid in values:
            song.chunk.set_u32(0x4C, values[song.dbid])
    session.commit()
    return len(values)
