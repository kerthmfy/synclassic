"""Sound Check: how loud each song is, so the iPod can play them all at a similar volume.

Loudness is measured with ffmpeg (EBU R128, the measure behind ReplayGain 2.0) and kept in the
library database, keyed by file, so each file is measured once. The iPod's Sound Check value
is derived from it: 1000 means "as is", lower plays louder, higher plays quieter
(value = 1000 x 10^(-gain/10), the formula libgpod and iTunes use).
"""

from __future__ import annotations

import math
import pathlib
import re
import shutil
import subprocess
from collections.abc import Callable

REFERENCE_LUFS = -18.0  # ReplayGain 2.0's target loudness
MAX_BOOST_DB = 12.0  # never lift a quiet song more than this (it would also lift its noise)
MAX_CUT_DB = 18.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS loudness (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    lufs REAL                    -- NULL: couldn't be measured (e.g. silence)
);
"""

_INTEGRATED = re.compile(r"Integrated loudness:\s*I:\s*(-?[\d.]+|-inf)\s*LUFS", re.S)


class LoudnessError(RuntimeError):
    pass


def measure(path: pathlib.Path, run: Callable = subprocess.run) -> float | None:
    """Integrated loudness of a song in LUFS (None for silence)."""
    if not shutil.which("ffmpeg") and run is subprocess.run:
        raise LoudnessError("ffmpeg isn't installed (brew install ffmpeg).")
    result = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-vn",
                  "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                 capture_output=True, text=True, timeout=600)
    found = _INTEGRATED.findall(result.stderr or "")
    if not found:
        raise LoudnessError(f"Couldn't measure {path.name}: {(result.stderr or '').strip().splitlines()[-1:]}")
    value = found[-1]
    if value == "-inf":
        return None
    lufs = float(value)
    return None if lufs < -70 else lufs


def gain_db(lufs: float) -> float:
    """How much to turn a song up (positive) or down (negative) to reach the reference."""
    return max(-MAX_CUT_DB, min(MAX_BOOST_DB, REFERENCE_LUFS - lufs))


def soundcheck(lufs: float | None) -> int:
    """The iPod's Sound Check value for a song of this loudness (0 = unknown, ignored)."""
    if lufs is None:
        return 0
    return max(1, min(0xFFFFFFFF, round(1000 * math.pow(10, -gain_db(lufs) / 10))))


class LoudnessCache:
    """Measured loudness, remembered in the library database."""

    def __init__(self, library, measure_file: Callable[[pathlib.Path], float | None] | None = None):
        self.db = library.db
        self.db.executescript(SCHEMA)
        self.measure_file = measure_file or (lambda path: measure(path))

    def known(self, path: pathlib.Path) -> tuple[bool, float | None]:
        try:
            stat = path.stat()
        except OSError:
            return False, None
        row = self.db.execute("SELECT size, mtime, lufs FROM loudness WHERE path = ?", (str(path),)).fetchone()
        if row and row["size"] == stat.st_size and abs(row["mtime"] - stat.st_mtime) < 1:
            return True, row["lufs"]
        return False, None

    def get(self, path: pathlib.Path) -> float | None:
        """The song's loudness, measuring it if it isn't known yet."""
        found, lufs = self.known(path)
        if found:
            return lufs
        lufs = self.measure_file(path)
        stat = path.stat()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO loudness (path, size, mtime, lufs) VALUES (?, ?, ?, ?)",
                            (str(path), stat.st_size, stat.st_mtime, lufs))
        return lufs

    def soundcheck(self, path: pathlib.Path) -> int:
        try:
            return soundcheck(self.get(path))
        except (LoudnessError, OSError, subprocess.SubprocessError):
            return 0
