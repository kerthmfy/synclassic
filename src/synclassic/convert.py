"""Converting songs while they're synced (your files on the Mac are never changed).

Two reasons a song is converted on its way to the iPod:
- The iPod can't play it: FLAC and other lossless files become Apple Lossless (the same sound;
  setting lossless_to = alac, the default) or 256 kbps AAC (lossless_to = aac). Ogg, Opus and
  other lossy files become 256 kbps AAC.
- You chose smaller files for that iPod (256, 192 or 128 kbps AAC), so more fits: every song
  bigger than that is converted.

Conversion uses ffmpeg (Apple's own AAC encoder on a Mac when ffmpeg has it). The converted
copy gets the song's details, cover and lyrics, is copied to the iPod, then deleted.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

# What Apple's iPod firmware plays (by file extension).
APPLE_PLAYABLE = {".m4a", ".mp3", ".wav", ".aif", ".aiff", ".m4b", ".aac"}
LOSSLESS = {".flac", ".wav", ".aif", ".aiff", ".ape", ".wv", ".alac", ".caf", ".tta"}
AUDIO = APPLE_PLAYABLE | LOSSLESS | {".ogg", ".oga", ".opus", ".wma", ".mpc", ".webm", ".mka"}
LOSSLESS_KBPS = 700  # a typical CD-quality lossless file; anything above this is lossless


class ConvertError(RuntimeError):
    pass


@dataclass(frozen=True)
class Target:
    codec: str  # "aac" or "alac"
    kbps: int = 0

    def describe(self) -> str:
        return "Apple Lossless" if self.codec == "alac" else f"{self.kbps} kbps AAC"


def file_kbps(path: pathlib.Path, duration_ms: int | None) -> int:
    """The file's average bitrate from its size and length (0 if the length isn't known)."""
    if not duration_ms:
        try:
            from mutagen import File as MutagenFile

            duration_ms = int((MutagenFile(path).info.length or 0) * 1000)  # type: ignore[union-attr]
        except Exception:
            return 0
    if not duration_ms:
        return 0
    return round(path.stat().st_size * 8 / duration_ms)  # bits per millisecond = kbps


def target_for(path: pathlib.Path, duration_ms: int | None, bitrate: int = 0, lossless_to: str = "alac",
               apple: bool = True) -> Target | None:
    """How this song has to be converted for the iPod (None: copied as it is).

    `bitrate`: the iPod's chosen size (0 = as they are). `apple`: Apple's firmware (False for
    Rockbox, which plays nearly everything)."""
    extension = path.suffix.lower()
    playable = extension in APPLE_PLAYABLE or not apple
    if bitrate:
        kbps = file_kbps(path, duration_ms)
        if kbps == 0 or kbps > bitrate * 1.15:  # bigger than the chosen size (allowing for the cover)
            return Target("aac", bitrate)
        if playable:
            return None
    if playable:
        return None
    if extension in LOSSLESS or file_kbps(path, duration_ms) >= LOSSLESS_KBPS:
        return Target("alac") if lossless_to == "alac" else Target("aac", 256)
    return Target("aac", 256)


def estimated_size(path: pathlib.Path, duration_ms: int | None, target: Target | None) -> int:
    """About how big the song will be on the iPod."""
    size = path.stat().st_size
    if target is None:
        return size
    if target.codec == "alac":
        return round(size * 1.08) if path.suffix.lower() in LOSSLESS else size  # FLAC → ALAC is a bit bigger
    if not duration_ms:
        try:
            from mutagen import File as MutagenFile

            duration_ms = int((MutagenFile(path).info.length or 0) * 1000)  # type: ignore[union-attr]
        except Exception:
            duration_ms = 0
    if not duration_ms:
        return size
    return min(size, round(duration_ms * target.kbps / 8 * 1.03) + 150_000)  # audio + room for the cover


def available() -> bool:
    return shutil.which("ffmpeg") is not None


_ENCODERS: set[str] | None = None


def _encoders(run=subprocess.run) -> set[str]:
    global _ENCODERS
    if _ENCODERS is None:
        try:
            result = run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30)
            _ENCODERS = {line.split()[1] for line in result.stdout.splitlines() if len(line.split()) > 1}
        except (OSError, subprocess.SubprocessError):
            _ENCODERS = set()
    return _ENCODERS


def command(source: pathlib.Path, out: pathlib.Path, target: Target, sample_rate: int = 44100,
            encoders: set[str] | None = None) -> list[str]:
    """The ffmpeg command for one song."""
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-map", "0:a:0", "-vn", "-map_metadata", "-1"]
    if sample_rate > 48000 or sample_rate < 32000:
        args += ["-ar", "44100"]  # the iPod plays 32–48 kHz
    if target.codec == "alac":
        args += ["-c:a", "alac", "-sample_fmt", "s16p"]  # 16-bit: what the iPod's Apple Lossless decoder plays
    else:
        encoder = "aac_at" if "aac_at" in (encoders if encoders is not None else _encoders()) else "aac"
        args += ["-c:a", encoder, "-b:a", f"{target.kbps}k"]
    args += ["-ac", "2", "-movflags", "+faststart", "-f", "ipod", str(out)]
    return args


def convert(source: pathlib.Path, target: Target, track, out_dir: pathlib.Path, run=subprocess.run) -> pathlib.Path:
    """Make the converted copy (an .m4a in `out_dir`) with the song's details, cover and lyrics."""
    from synclassic import lyrics, tagging
    from synclassic.ipod.artwork import embedded_cover

    if run is subprocess.run and not available():
        raise ConvertError("Converting songs needs ffmpeg (brew install ffmpeg).")
    try:
        from mutagen import File as MutagenFile

        sample_rate = int(getattr(MutagenFile(source).info, "sample_rate", 0) or 44100)  # type: ignore[union-attr]
    except Exception:
        sample_rate = 44100
    out_dir.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(suffix=".m4a", dir=out_dir)
    os.close(handle)
    out = pathlib.Path(name)
    result = run(command(source, out, target, sample_rate), capture_output=True, text=True)
    if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        detail = (result.stderr or "").strip().splitlines()[-1:] or ["no details"]
        raise ConvertError(f"Couldn't convert {source.name}: {detail[0]}")
    cover = embedded_cover(source)
    try:
        tagging.tag_file(out, track, cover)
        text = lyrics.read(source)
        if text:
            lyrics.write(out, text)
    except Exception:
        pass  # the song still plays; the iPod gets its details from the database anyway
    return out


class Converter:
    """Converts songs a few at a time in the background, handing them over in order.

        with Converter(jobs) as converter:      # jobs: [(key, source, target, track)]
            path = converter.get(key)           # waits for that one; you delete it after use
    """

    def __init__(self, jobs, workers: int | None = None, out_dir: pathlib.Path | None = None, run=subprocess.run):
        self._tmp = tempfile.TemporaryDirectory(prefix="synclassic-convert-") if out_dir is None else None
        self.out_dir = out_dir or pathlib.Path(self._tmp.name)  # type: ignore[union-attr]
        self.jobs = list(jobs)
        self.run = run
        self.pool = ThreadPoolExecutor(max_workers=workers or max(1, min(4, (os.cpu_count() or 2) - 1)))
        self.futures: dict[str, Future] = {}
        self._next = 0
        self._ahead = (workers or 4) * 2

    def _fill(self) -> None:
        while self._next < len(self.jobs) and sum(1 for f in self.futures.values() if not f.done()) < self._ahead:
            key, source, target, track = self.jobs[self._next]
            self.futures[key] = self.pool.submit(convert, source, target, track, self.out_dir, self.run)
            self._next += 1

    def get(self, key: str) -> pathlib.Path:
        self._fill()
        if key not in self.futures:
            for job in self.jobs:
                if job[0] == key:
                    self.futures[key] = self.pool.submit(convert, job[1], job[2], job[3], self.out_dir, self.run)
                    break
            else:
                raise KeyError(key)
        path = self.futures.pop(key).result()
        self._fill()
        return path

    def close(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)
        for future in self.futures.values():
            if future.done() and not future.cancelled() and future.exception() is None:
                future.result().unlink(missing_ok=True)
        if self._tmp is not None:
            self._tmp.cleanup()

    def __enter__(self) -> Converter:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
