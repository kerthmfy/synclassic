"""The Now Playing panel: karaoke lyrics or the visualizer for the song playing in Synclassic.

Both follow the song by its position; between the player's position updates they keep counting
with the clock, so the highlight and the bars move smoothly.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QStackedLayout, QWidget

from synclassic.gui import theme


class Clock:
    """The song's position now, from the last position the player gave and the time since."""

    def __init__(self):
        self.ms, self.at, self.playing = 0, time.monotonic(), False

    def set(self, ms: int) -> None:
        self.ms, self.at = ms, time.monotonic()

    def set_playing(self, playing: bool) -> None:
        self.ms, self.at, self.playing = self.now(), time.monotonic(), playing

    def now(self) -> int:
        return self.ms + (int((time.monotonic() - self.at) * 1000) if self.playing else 0)


def average_colour(image: QImage | None) -> QColor:
    """The cover's overall colour, darkened, for the background."""
    if image is None or image.isNull():
        return QColor("#1b1e24")
    small = image.scaled(8, 8, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    r = g = b = 0
    for x in range(8):
        for y in range(8):
            colour = small.pixelColor(x, y)
            r, g, b = r + colour.red(), g + colour.green(), b + colour.blue()
    colour = QColor(r // 64, g // 64, b // 64)
    hue, saturation, value, _ = colour.getHsv()
    return QColor.fromHsv(max(0, hue), min(200, saturation), min(value, 95))


class _Backdrop(QWidget):
    """Shared: the song's title and cover at the top, over a dark background tinted by the cover."""

    def __init__(self, clock: Clock, parent=None):
        super().__init__(parent)
        self.clock = clock
        self.title = self.artist = ""
        self.cover: QImage | None = None
        self.tint = average_colour(None)

    def set_song(self, title: str, artist: str, cover: QImage | None) -> None:
        self.title, self.artist, self.cover = title, artist, cover
        self.tint = average_colour(cover)
        self.update()

    def paint_backdrop(self, painter: QPainter) -> None:
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, self.tint)
        gradient.setColorAt(1, QColor("#08090b"))
        painter.fillRect(self.rect(), gradient)
        x = 20
        if self.cover is not None and not self.cover.isNull():
            painter.drawImage(QRectF(20, 18, 56, 56), self.cover)
            painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
            painter.drawRect(QRectF(20, 18, 56, 56))
            x = 90
        font = QFont(self.font())
        font.setPixelSize(16)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        width = max(10, self.width() - x - 20)
        painter.drawText(QRectF(x, 24, width, 22), Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(font).elidedText(self.title, Qt.TextElideMode.ElideRight, width))
        font.setBold(False)
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 170))
        painter.drawText(QRectF(x, 48, width, 20), Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(font).elidedText(self.artist, Qt.TextElideMode.ElideRight, width))


class LyricsView(_Backdrop):
    seek_requested = Signal(int)

    TOP = 96  # below the song's title

    def __init__(self, clock: Clock, parent=None):
        super().__init__(clock, parent)
        self.lyrics = None  # synced_lyrics.Lyrics
        self.message = ""
        self.offset = 0.0  # how far the lines are scrolled
        self.manual_until = 0.0  # scrolled by hand: follow the song again after this
        self._layout: list[tuple[float, float, str]] = []  # (y, height, text) per line, for this width
        self._layout_width = -1
        self.setMouseTracking(True)

    def set_lyrics(self, lyrics, message: str = "") -> None:
        self.lyrics, self.message = lyrics, message
        self._layout_width = -1
        self.offset = 0.0
        self.manual_until = 0.0
        self.update()

    def _lines(self) -> list[str]:
        if self.lyrics is None:
            return []
        if self.lyrics.timed:
            return [text or "♪" for _, text in self.lyrics.lines]
        return self.lyrics.plain.splitlines()

    def _fonts(self) -> tuple[QFont, QFont]:
        normal = QFont(self.font())
        normal.setPixelSize(20)
        normal.setBold(True)
        big = QFont(normal)
        big.setPixelSize(26)
        return normal, big

    def _ensure_layout(self) -> None:
        if self._layout_width == self.width():
            return
        normal, big = self._fonts()
        metrics = QFontMetrics(big)  # room for the biggest size, so lines don't jump
        width = max(100, self.width() - 80)
        y = 0.0
        self._layout = []
        for text in self._lines():
            height = metrics.boundingRect(0, 0, width, 10_000, Qt.TextFlag.TextWordWrap, text or " ").height() + 14
            self._layout.append((y, float(height), text))
            y += height
        self._layout_width = self.width()

    def current(self) -> int:
        if self.lyrics is None or not self.lyrics.timed:
            return -1
        return self.lyrics.line_at(self.clock.now())

    def tick(self) -> None:
        self._ensure_layout()
        line = self.current()
        if line >= 0 and time.monotonic() > self.manual_until:
            y, height, _ = self._layout[line]
            target = y + height / 2 - (self.height() - self.TOP) * 0.4
            self.offset += (max(0.0, target) - self.offset) * 0.18
        self.update()

    def wheelEvent(self, event):  # noqa: N802
        self.offset = max(0.0, self.offset - event.angleDelta().y() / 2)
        self.manual_until = time.monotonic() + 4
        self.update()

    def mousePressEvent(self, event):  # noqa: N802
        if self.lyrics is None or not self.lyrics.timed:
            return
        self._ensure_layout()
        y = event.position().y() - self.TOP + self.offset
        for index, (top, height, _) in enumerate(self._layout):
            if top <= y < top + height:
                self.manual_until = 0.0
                self.seek_requested.emit(self.lyrics.lines[index][0])
                return

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.paint_backdrop(painter)
        if not self._lines():
            font = QFont(self.font())
            font.setPixelSize(16)
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255, 150))
            painter.drawText(QRectF(40, self.TOP, self.width() - 80, self.height() - self.TOP - 40),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             self.message or "No lyrics for this song.")
            return
        self._ensure_layout()
        normal, big = self._fonts()
        current = self.current()
        timed = self.lyrics is not None and self.lyrics.timed
        painter.setClipRect(QRectF(0, self.TOP - 10, self.width(), self.height() - self.TOP + 10))
        for index, (y, height, text) in enumerate(self._layout):
            top = self.TOP + y - self.offset
            if top + height < self.TOP - 40 or top > self.height():
                continue
            fade = max(0.0, min(1.0, (top - self.TOP + 10) / 60))  # lines scrolling up under the title fade away
            if timed and index == current:
                painter.setFont(big)
                painter.setPen(QColor(255, 255, 255, int(255 * max(fade, 0.6))))
            else:
                painter.setFont(normal)
                alpha = 150 if not timed else (95 if index < current else 125)
                painter.setPen(QColor(255, 255, 255, int(alpha * fade)))
            painter.drawText(QRectF(40, top, self.width() - 80, height), Qt.AlignmentFlag.AlignHCenter |
                             Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, text)
        painter.setClipping(False)
        small = QFont(self.font())
        small.setPixelSize(10)
        painter.setFont(small)
        painter.setPen(QColor(255, 255, 255, 90))
        note = "Lyrics from LRCLIB" if self.lyrics is not None and self.lyrics.source in ("LRCLIB", "cache") else ""
        if not timed:
            note = (note + " · " if note else "") + "not timed for this song"
        painter.drawText(QRectF(0, self.height() - 22, self.width() - 14, 16), Qt.AlignmentFlag.AlignRight, note)


class Visualizer(_Backdrop):
    """Bars for the song's frequencies, with falling peaks and a reflection."""

    def __init__(self, clock: Clock, parent=None):
        super().__init__(clock, parent)
        self.spectrum = None
        self.message = ""
        self.shown: list[float] = []
        self.peaks: list[float] = []
        self.started = time.monotonic()

    def set_spectrum(self, spectrum, message: str = "") -> None:
        self.spectrum, self.message = spectrum, message
        bands = spectrum.bands if spectrum is not None else 32
        self.shown, self.peaks = [0.0] * bands, [0.0] * bands
        self.started = time.monotonic()
        self.update()

    def tick(self) -> None:
        if self.spectrum is not None:
            levels = self.spectrum.at(self.clock.now()) if self.clock.playing else bytes(self.spectrum.bands)
            for band, level in enumerate(levels):
                value = level / 255
                # Up quickly, down gently; peaks hang a moment and fall.
                self.shown[band] += (value - self.shown[band]) * (0.6 if value > self.shown[band] else 0.18)
                self.peaks[band] = max(self.shown[band], self.peaks[band] - 0.012)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor("#05070d"))
        gradient.setColorAt(1, self.tint.darker(160))
        painter.fillRect(self.rect(), gradient)
        if time.monotonic() - self.started < 6:  # the song's name for a few seconds, then just the bars
            self.paint_title(painter)
        if self.spectrum is None:
            font = QFont(self.font())
            font.setPixelSize(16)
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255, 150))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message or "Play a song to see it.")
            return
        bands = len(self.shown)
        floor = self.height() * 0.72
        room = floor - 100
        gap = 4
        width = (self.width() - 80 - gap * (bands - 1)) / bands
        accent_top, accent_bottom = QColor(theme.c("accent_light")), QColor(theme.c("accent_bottom"))
        if theme.current_name == "graphite":
            accent_top, accent_bottom = QColor("#d4dbe6"), QColor("#7f8ca1")
        for band in range(bands):
            x = 40 + band * (width + gap)
            height = max(2.0, self.shown[band] * room)
            bar = QRectF(x, floor - height, width, height)
            fill = QLinearGradient(bar.topLeft(), bar.bottomLeft())
            fill.setColorAt(0, accent_top)
            fill.setColorAt(1, accent_bottom)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(bar, 2, 2)
            # Reflection on the "floor".
            mirror = QRectF(x, floor + 3, width, height * 0.35)
            fade = QLinearGradient(mirror.topLeft(), mirror.bottomLeft())
            colour = QColor(accent_bottom)
            colour.setAlpha(90)
            fade.setColorAt(0, colour)
            colour.setAlpha(0)
            fade.setColorAt(1, colour)
            painter.setBrush(fade)
            painter.drawRect(mirror)
            # Peak cap.
            peak_y = floor - max(2.0, self.peaks[band] * room) - 5
            painter.setBrush(QColor(255, 255, 255, 220))
            painter.drawRoundedRect(QRectF(x, peak_y, width, 3), 1.5, 1.5)

    def paint_title(self, painter: QPainter) -> None:
        """The song's cover and name, fading out after a few seconds."""
        if not self.title:
            return
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, 6 - (time.monotonic() - self.started))))
        x = 20
        if self.cover is not None and not self.cover.isNull():
            painter.drawImage(QRectF(20, 18, 56, 56), self.cover)
            x = 90
        font = QFont(self.font())
        font.setPixelSize(16)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QPointF(x, 40), self.title)
        font.setBold(False)
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 170))
        painter.drawText(QPointF(x, 62), self.artist)
        painter.restore()


class NowPlayingPanel(QWidget):
    """Lyrics or the visualizer, filling the space of the song list."""

    seek_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clock = Clock()
        self.lyrics_view = LyricsView(self.clock)
        self.visualizer = Visualizer(self.clock)
        self.lyrics_view.seek_requested.connect(self.seek_requested)
        layout = QStackedLayout(self)
        layout.addWidget(self.lyrics_view)
        layout.addWidget(self.visualizer)
        self.pages = layout
        self.mode = "lyrics"
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)

    def show_mode(self, mode: str) -> None:
        self.mode = mode
        self.pages.setCurrentWidget(self.lyrics_view if mode == "lyrics" else self.visualizer)
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def set_song(self, title: str, artist: str, cover: QImage | None) -> None:
        self.lyrics_view.set_song(title, artist, cover)
        self.visualizer.set_song(title, artist, cover)

    def set_position(self, ms: int) -> None:
        self.clock.set(ms)

    def set_playing(self, playing: bool) -> None:
        self.clock.set_playing(playing)

    def _tick(self) -> None:
        (self.lyrics_view if self.mode == "lyrics" else self.visualizer).tick()
