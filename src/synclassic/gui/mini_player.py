"""The small ways to control playback: a music note in the macOS menu bar (the current song, Play/
Pause, Next, Previous) and the Mini Player, a little always-on-top window like iTunes had."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QMenu, QSystemTrayIcon, QWidget

from synclassic.gui import theme
from synclassic.gui.widgets import GlossyButton, GradientBar, LcdDisplay


def note_icon() -> QIcon:
    """A black music note; on macOS it's used as a template, so it follows the menu bar's colours."""
    pixmap = QPixmap(36, 36)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#000000"))
    painter.drawEllipse(QPointF(5.2, 13.6), 3.1, 2.4)
    painter.drawEllipse(QPointF(12.7, 11.8), 3.1, 2.4)
    beam = QPainterPath()
    beam.moveTo(7.6, 13.4)
    beam.lineTo(7.6, 4.6)
    beam.lineTo(15.1, 2.6)
    beam.lineTo(15.1, 11.6)
    beam.lineTo(13.9, 11.6)
    beam.lineTo(13.9, 5.4)
    beam.lineTo(8.8, 6.8)
    beam.lineTo(8.8, 13.4)
    beam.closeSubpath()
    painter.drawPath(beam)
    painter.end()
    icon = QIcon(pixmap)
    icon.setIsMask(True)
    return icon


class MenuBarPlayer:
    """The menu bar item. `window` is the main window (its actions and player are used)."""

    def __init__(self, window):
        self.window = window
        self.tray = QSystemTrayIcon(note_icon(), window)
        self.tray.setToolTip("Synclassic")
        menu = QMenu()
        self.now = QAction("Not playing", menu)
        self.now.setEnabled(False)
        self.line = QAction("", menu)
        self.line.setEnabled(False)
        menu.addAction(self.now)
        menu.addAction(self.line)
        menu.addSeparator()
        menu.addAction(window.play_action)
        menu.addAction(window.next_action)
        menu.addAction(window.previous_action)
        menu.addSeparator()
        mini = QAction("Mini Player", menu)
        mini.triggered.connect(window.show_mini_player)
        menu.addAction(mini)
        show = QAction("Show Synclassic", menu)
        show.triggered.connect(lambda: (window.showNormal(), window.raise_(), window.activateWindow()))
        menu.addAction(show)
        self.menu = menu
        self.tray.setContextMenu(menu)
        window.player.track_changed.connect(self.update)

    def show(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def hide(self) -> None:
        self.tray.hide()

    def update(self, item) -> None:
        if item is None:
            self.now.setText("Not playing")
            self.line.setVisible(False)
            self.tray.setToolTip("Synclassic")
            return
        self.now.setText(item.title)
        self.line.setText(item.line)
        self.line.setVisible(bool(item.line))
        self.tray.setToolTip(f"{item.title} — {item.line}" if item.line else item.title)


class MiniPlayer(QWidget):
    """A small always-on-top window: back / play / next, the display with the seek bar, volume."""

    def __init__(self, window):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.window_ = window
        self.setWindowTitle("Synclassic")
        self.setFixedHeight(78)
        self.setMinimumWidth(460)
        bar = GradientBar(theme.TOOLBAR_TOP, theme.TOOLBAR_BOTTOM, line_at_bottom=False)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(bar)
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(4)
        for symbol, action, size in (("prev", window.previous_action, 26), ("play", window.play_action, 32),
                                     ("next", window.next_action, 26)):
            button = GlossyButton(symbol, "", size)
            button.clicked.connect(action.trigger)
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            if symbol == "play":
                self.play_button = button
        self.lcd = LcdDisplay()
        self.lcd.setMinimumWidth(300)
        self.lcd.seek.sliderReleased.connect(lambda: window.player.seek(self.lcd.seek.value()))
        row.addWidget(self.lcd, 1)
        player = window.player
        player.track_changed.connect(self._track)
        player.position_changed.connect(lambda position, duration: self.lcd.set_times(position, duration))
        player.playing_changed.connect(self._playing)
        self._track(player.current)
        self._playing(player.is_playing)

    def _track(self, item) -> None:
        if item is None:
            self.lcd.hide_now_playing()
            self.lcd.primary.setText("Synclassic")
            self.lcd.secondary.setText("Not playing")
        else:
            self.lcd.show_now_playing(item.title, item.line)

    def _playing(self, playing: bool) -> None:
        self.play_button.symbol = "pause" if playing else "play"
        self.play_button.update()

