"""The sound of a song as bars, for the visualizer (View → Visualizer).

The song is decoded once with ffmpeg (mono, 22 kHz) and cut into 30 slices a second; each slice's
frequencies (FFT, numpy) are grouped into bands spaced like hearing is (more for the lows), from
40 Hz to 11 kHz. Levels are 0–255 per band, relative to the song's own loud parts, so quiet and
loud songs both fill the screen. A 4-minute song takes well under a second.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from dataclasses import dataclass

RATE = 22050
FPS = 30
BANDS = 32
WINDOW = 2048


class SpectrumError(RuntimeError):
    pass


@dataclass
class Spectrum:
    fps: int
    bands: int
    levels: bytes  # frames × bands, 0–255

    @property
    def frames(self) -> int:
        return len(self.levels) // self.bands if self.bands else 0

    def at(self, ms: int) -> bytes:
        """The band levels at a moment of the song."""
        if not self.frames:
            return bytes(self.bands)
        frame = min(self.frames - 1, max(0, int(ms * self.fps / 1000)))
        return self.levels[frame * self.bands:(frame + 1) * self.bands]


def decode(path: pathlib.Path, run=subprocess.run) -> bytes:
    if run is subprocess.run and not shutil.which("ffmpeg"):
        raise SpectrumError("The visualizer needs ffmpeg (brew install ffmpeg).")
    result = run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1",
                  "-ar", str(RATE), "-f", "s16le", "-"], capture_output=True)
    if result.returncode != 0 or not result.stdout:
        raise SpectrumError(f"Couldn't read {path.name}.")
    return result.stdout


def band_edges(bands: int = BANDS, low: float = 40.0, high: float = 11000.0) -> list[int]:
    """FFT bin numbers where each band starts (and the last ends): log-spaced, at least one bin each."""
    import math

    hz_per_bin = RATE / WINDOW
    edges = []
    for i in range(bands + 1):
        hz = low * (high / low) ** (i / bands)
        edges.append(max(1, round(hz / hz_per_bin)))
    for i in range(1, len(edges)):
        edges[i] = max(edges[i], edges[i - 1] + 1)
    return [min(e, WINDOW // 2) for e in edges] if math.isfinite(hz_per_bin) else edges


def analyse_samples(pcm: bytes, fps: int = FPS, bands: int = BANDS) -> Spectrum:
    try:
        import numpy as np
    except ImportError:
        raise SpectrumError("The visualizer needs numpy. In Terminal: cd synclassic, then "
                            "source .venv/bin/activate, then pip install -e .") from None

    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    hop = RATE // fps
    if len(samples) < WINDOW:
        samples = np.pad(samples, (0, WINDOW - len(samples)))
    count = 1 + (len(samples) - WINDOW) // hop
    window = np.hanning(WINDOW).astype(np.float32)
    edges = band_edges(bands)
    out = np.zeros((count, bands), dtype=np.float32)
    for start in range(0, count, 512):  # in chunks, to keep memory small
        stop = min(count, start + 512)
        index = (np.arange(start, stop)[:, None] * hop) + np.arange(WINDOW)[None, :]
        magnitude = np.abs(np.fft.rfft(samples[index] * window, axis=1))
        for band in range(bands):
            out[start:stop, band] = magnitude[:, edges[band]:edges[band + 1]].mean(axis=1)
    decibels = 20 * np.log10(out + 1e-6)
    top = np.percentile(decibels, 99.5) if decibels.size else 0
    scaled = np.clip((decibels - (top - 50)) / 50, 0, 1)  # the loudest moments near the top, 50 dB of range
    return Spectrum(fps, bands, (scaled * 255).astype(np.uint8).tobytes())


def analyse(path: pathlib.Path, run=subprocess.run) -> Spectrum:
    return analyse_samples(decode(path, run))
