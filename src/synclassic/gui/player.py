"""Play music in the app: a play queue (shuffle, repeat, next/previous) on Qt's media player.

Plays heard here are scrobbled like Last.fm's own players do: once a song over 30 seconds has
played for half its length or 4 minutes, whichever comes first.
"""

from __future__ import annotations

import pathlib
import random
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QUrl, Signal


@dataclass
class PlayItem:
    path: pathlib.Path
    title: str
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    length_ms: int = 0
    key: tuple = ()  # ("lib", track id) or ("ipod", song id): which row shows the speaker
    cover: tuple | None = None  # where to find a cover on the iPod (browse.Contents.covers value)
    podcast: bool = False  # a podcast episode: not scrobbled; marked played when it ends
    cover_file: pathlib.Path | None = None  # a cover image next to the file (a show's artwork)

    @property
    def line(self) -> str:
        return " — ".join(part for part in (self.artist, self.album) if part)


def scrobble_threshold_ms(length_ms: int) -> int | None:
    """How long a song must play to count as a play (None: too short to ever count)."""
    if length_ms < 30_000:
        return None
    return min(length_ms // 2, 240_000)


class Player(QObject):
    track_changed = Signal(object)  # PlayItem or None
    position_changed = Signal(int, int)  # position, duration (ms)
    playing_changed = Signal(bool)
    error = Signal(str)
    finished_item = Signal(object)  # PlayItem played to the end
    counted = Signal(object)  # PlayItem heard long enough to count as a play (queued for Last.fm)

    REPEAT_MODES = ("off", "all", "one")

    def __init__(self, parent=None, backend=None):
        super().__init__(parent)
        self.queue: list[PlayItem] = []
        self.order: list[int] = []
        self.position = -1  # index into order
        self.shuffle = False
        self.repeat = "off"
        self._started_at = 0.0
        self._heard_ms = 0
        self._last_pos = 0
        self._counted = False
        self.backend = backend or self._make_backend()

    def _make_backend(self):
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        media = QMediaPlayer(self)
        audio = QAudioOutput(self)
        audio.setVolume(0.8)
        media.setAudioOutput(audio)
        media._audio = audio  # keep it alive
        media.positionChanged.connect(self._position)
        media.mediaStatusChanged.connect(self._status)
        media.playbackStateChanged.connect(lambda state: self.playing_changed.emit(self.is_playing))
        media.errorOccurred.connect(lambda *_: self._failed(media.errorString()))
        return media

    # --- state --------------------------------------------------------------------------------

    @property
    def current(self) -> PlayItem | None:
        if 0 <= self.position < len(self.order):
            return self.queue[self.order[self.position]]
        return None

    @property
    def is_playing(self) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer

        return self.backend.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    # --- controls -----------------------------------------------------------------------------

    def play_list(self, items: list[PlayItem], start: int = 0) -> None:
        if not items:
            return
        self._finish_current()
        self.queue = list(items)
        self.order = list(range(len(items)))
        if self.shuffle:
            rest = [i for i in self.order if i != start]
            random.shuffle(rest)
            self.order = [start] + rest
            self.position = 0
        else:
            self.position = start
        self._load()

    def toggle(self) -> None:
        if self.current is None:
            return
        if self.is_playing:
            self.backend.pause()
        else:
            self.backend.play()

    def next(self) -> None:
        if self.current is None:
            return
        self._finish_current()
        if self.position + 1 < len(self.order):
            self.position += 1
        elif self.repeat == "all":
            self.position = 0
        else:
            self.stop()
            return
        self._load()

    def previous(self) -> None:
        if self.current is None:
            return
        if self.backend.position() > 3000 or self.position == 0:
            self.backend.setPosition(0)  # like iTunes: first restart the song
            return
        self._finish_current()
        self.position -= 1
        self._load()

    def stop(self) -> None:
        self._finish_current()
        self.backend.stop()
        self.position = -1
        self.track_changed.emit(None)

    def seek(self, ms: int) -> None:
        self.backend.setPosition(ms)
        self._last_pos = ms  # a jump isn't "listening"

    def set_volume(self, percent: int) -> None:
        audio = getattr(self.backend, "_audio", None)
        if audio is not None:
            audio.setVolume(max(0, min(100, percent)) / 100)

    def set_shuffle(self, on: bool) -> None:
        self.shuffle = on
        if self.current is None or not self.order:
            return
        playing = self.order[self.position]
        if on:
            rest = [i for i in range(len(self.queue)) if i != playing]
            random.shuffle(rest)
            self.order = [playing] + rest
            self.position = 0
        else:
            self.order = list(range(len(self.queue)))
            self.position = playing

    def cycle_repeat(self) -> str:
        self.repeat = self.REPEAT_MODES[(self.REPEAT_MODES.index(self.repeat) + 1) % 3]
        return self.repeat

    def stop_if_from(self, folder: pathlib.Path) -> None:
        """Stop when playing a file inside `folder` (e.g. an iPod about to be ejected)."""
        item = self.current
        if item is not None and (item.path == folder or folder in item.path.parents):
            self.stop()

    # --- internals ----------------------------------------------------------------------------

    def _load(self) -> None:
        item = self.current
        if item is None:
            return
        self._started_at = time.time()
        self._heard_ms = 0
        self._last_pos = 0
        self._counted = False
        self.backend.setSource(QUrl.fromLocalFile(str(item.path)))
        self.backend.play()
        self.track_changed.emit(item)

    def _position(self, ms: int) -> None:
        step = ms - self._last_pos
        if 0 < step < 2000:  # normal playback (not a seek)
            self._heard_ms += step
        self._last_pos = ms
        item = self.current
        duration = self.backend.duration() or (item.length_ms if item else 0)
        self.position_changed.emit(ms, duration)
        self._maybe_count()

    def _maybe_count(self) -> None:
        item = self.current
        if item is None or self._counted:
            return
        threshold = scrobble_threshold_ms(self.backend.duration() or item.length_ms)
        if threshold is not None and self._heard_ms >= threshold:
            self._counted = True
            if item.podcast:
                return
            from synclassic import scrobble

            try:
                if scrobble.add_play(item.artist, item.title, item.album, item.album_artist,
                                     round((self.backend.duration() or item.length_ms) / 1000), int(self._started_at)):
                    self.counted.emit(item)
            except OSError:
                pass

    def _finish_current(self) -> None:
        self._maybe_count()

    def _status(self, status) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._maybe_count()
            if self.current is not None:
                self.finished_item.emit(self.current)
            if self.repeat == "one":
                self._load()
            else:
                self.next()

    def _failed(self, message: str) -> None:
        item = self.current
        self.error.emit(f"Couldn't play {item.title if item else 'this song'}: {message}")
        if item is not None and self.position + 1 < len(self.order):
            self.next()
