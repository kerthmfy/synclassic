"""Little sounds: the chime when a sync finishes (made here, so no sound files are shipped)."""

from __future__ import annotations

import math
import pathlib
import struct
import wave

from synclassic.config import app_dir

RATE = 44100


def chime_file() -> pathlib.Path:
    """A soft two-note chime (a fifth up, bell-like), written once as a .wav."""
    path = app_dir() / "sounds" / "sync-done.wav"
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    notes = [(0.0, 1318.5), (0.11, 1975.5)]  # E6 then B6
    length = 1.2
    frames = []
    for i in range(int(RATE * length)):
        t = i / RATE
        value = 0.0
        for start, frequency in notes:
            if t >= start:
                age = t - start
                envelope = min(1.0, age / 0.004) * math.exp(-age * 4.2)
                value += envelope * (math.sin(2 * math.pi * frequency * age)
                                     + 0.25 * math.sin(2 * math.pi * frequency * 2 * age) * math.exp(-age * 6))
        frames.append(struct.pack("<h", int(max(-1.0, min(1.0, value * 0.32)) * 32767)))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b"".join(frames))
    return path


_effects = []


def play_chime() -> None:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect
    except ImportError:
        return
    effect = QSoundEffect()
    effect.setSource(QUrl.fromLocalFile(str(chime_file())))
    effect.setVolume(0.6)
    effect.play()
    _effects.append(effect)  # kept alive while it plays
    del _effects[:-3]


def tick_file() -> pathlib.Path:
    """The click wheel's tick: a tiny, soft click."""
    path = app_dir() / "sounds" / "tick.wav"
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    for i in range(int(RATE * 0.012)):
        t = i / RATE
        value = math.exp(-t * 900) * (math.sin(2 * math.pi * 3200 * t) * 0.6 + math.sin(2 * math.pi * 1100 * t) * 0.4)
        frames.append(struct.pack("<h", int(value * 0.5 * 32767)))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b"".join(frames))
    return path


_tick = []


def play_tick() -> None:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect
    except ImportError:
        return
    if not _tick:
        effect = QSoundEffect()
        effect.setSource(QUrl.fromLocalFile(str(tick_file())))
        effect.setVolume(0.35)
        _tick.append(effect)
    _tick[0].play()
