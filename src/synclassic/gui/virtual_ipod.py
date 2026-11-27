"""The Virtual iPod: the iPod drawing on the iPod page, working like a 5.5G.

Its menus come from the real iPod's library (Music > Playlists / Artists / Albums / Songs /
Genres / Audiobooks, Podcasts, Shuffle Songs, Now Playing). Circle the click wheel with the
mouse (or scroll, or use the arrow keys) to move, click the center button (or Return) to choose,
MENU (or Esc) to go back, and the wheel's play/pause, previous and next buttons control playback.
Songs play through the app's player.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen

from synclassic.gui.widgets import IPodGraphic, ipod_geometry, paint_ipod

STEP_DEGREES = 18  # turning the wheel this far moves one row
VISIBLE_ROWS = 6


@dataclass
class Item:
    label: str
    open: Callable[[], "Menu"] | None = None  # a submenu
    play: tuple[list[int], int] | None = None  # (song ids, which one) to start playing
    action: Callable[[], None] | None = None
    arrow: bool = True


@dataclass
class Menu:
    title: str
    items: list[Item] = field(default_factory=list)
    selected: int = 0
    top: int = 0  # first row shown


class VirtualIPod(IPodGraphic):
    play_requested = Signal(list, int)  # song ids, start index
    toggle_requested = Signal()
    next_requested = Signal()
    previous_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(False)
        self.contents = None
        self.stack: list[Menu] = []
        self.now_playing = False  # showing the Now Playing screen
        self.current = None  # PlayItem being played
        self.position, self.duration, self.playing = 0, 0, False
        self.queue_info = (0, 0)  # (number, total) in the playing list
        self.cover_for: Callable[[object], QImage | None] | None = None
        self._cover_cache: tuple[object, QImage | None] | None = None
        self._turn: float | None = None  # last angle while circling the wheel
        self._turned = 0.0
        self._pressed_at: QPointF | None = None
        self.tick: Callable[[], None] | None = None
        self.setToolTip("Circle the click wheel (or scroll) to move, click the center to choose, MENU to go back.")

    # --- data -----------------------------------------------------------------------------------

    def set_contents(self, contents) -> None:
        self.contents = contents
        self.stack = [self._main_menu()] if contents is not None else []
        self.now_playing = False
        self.update()

    def set_now_playing(self, item, number: int = 0, total: int = 0) -> None:
        self.current = item
        self.queue_info = (number, total)
        if item is None:
            self.now_playing = False
        self._cover_cache = None
        self.update()

    def set_position(self, position: int, duration: int) -> None:
        self.position, self.duration = position, duration
        if self.now_playing:
            self.update()

    def set_playing(self, playing: bool) -> None:
        self.playing = playing
        self.update()

    # --- menus ----------------------------------------------------------------------------------

    def _songs(self, kind: str = "song"):
        return [s for s in self.contents.songs.values() if s.kind == kind]

    def _song_list(self, title: str, ids: list[int]) -> Menu:
        songs = self.contents.songs
        return Menu(title, [Item(songs[i].title or "(no title)", play=(ids, n), arrow=False) for n, i in enumerate(ids)])

    def _main_menu(self) -> Menu:
        items = [Item("Music", open=self._music_menu)]
        if self._songs("video"):
            items.append(Item("Videos", open=lambda: self._song_list("Videos", self._sorted(self._songs("video")))))
        if self._songs("podcast"):
            items.append(Item("Podcasts", open=self._podcasts_menu))
        items.append(Item("Shuffle Songs", action=self._shuffle, arrow=False))
        items.append(Item("Now Playing", action=self._show_now_playing))
        return Menu(self.contents.name or "iPod", items)

    def _sorted(self, songs) -> list[int]:
        return [s.id for s in sorted(songs, key=lambda s: s.title.casefold())]

    def _music_menu(self) -> Menu:
        items = [Item("Playlists", open=self._playlists_menu), Item("Artists", open=self._artists_menu),
                 Item("Albums", open=self._albums_menu), Item("Songs", open=lambda: self._song_list(
                     "Songs", self._sorted(self._songs()))), Item("Genres", open=self._genres_menu)]
        if self._songs("audiobook"):
            items.append(Item("Audiobooks", open=lambda: self._song_list("Audiobooks", self._sorted(self._songs("audiobook")))))
        return Menu("Music", items)

    def _playlists_menu(self) -> Menu:
        songs = self.contents.songs
        items = []
        for playlist in self.contents.playlists:
            ids = [i for i in playlist.song_ids if i in songs]
            items.append(Item(playlist.name, open=lambda p=playlist.name, ids=ids: self._song_list(p, ids)))
        return Menu("Playlists", items)

    def _album_order(self, ids: list[int]) -> list[int]:
        songs = self.contents.songs
        return sorted(ids, key=lambda i: (songs[i].album.casefold(), songs[i].disc_number, songs[i].track_number,
                                          songs[i].title.casefold()))

    def _artists_menu(self) -> Menu:
        items = []
        for artist, ids in self.contents.grouped("artist").items():
            items.append(Item(artist, open=lambda a=artist, ids=ids: self._artist_menu(a, ids)))
        return Menu("Artists", items)

    def _artist_menu(self, artist: str, ids: list[int]) -> Menu:
        songs = self.contents.songs
        albums: dict[str, list[int]] = {}
        for i in self._album_order(ids):
            albums.setdefault(songs[i].album or "Unknown Album", []).append(i)
        items = [Item("All", open=lambda: self._song_list(artist, self._album_order(ids)))]
        items += [Item(album, open=lambda a=album, album_ids=album_ids: self._song_list(a, album_ids))
                  for album, album_ids in albums.items()]
        return Menu(artist, items)

    def _albums_menu(self) -> Menu:
        albums = sorted(self.contents.albums(), key=lambda a: a.title.casefold())
        return Menu("Albums", [Item(a.title, open=lambda a=a: self._song_list(a.title, a.song_ids)) for a in albums])

    def _genres_menu(self) -> Menu:
        songs = self.contents.songs
        items = []
        for genre, ids in self.contents.grouped("genre").items():
            ordered = sorted(ids, key=lambda i: (songs[i].artist.casefold(), songs[i].album.casefold(),
                                                 songs[i].track_number))
            items.append(Item(genre, open=lambda g=genre, ids=ordered: self._song_list(g, ids)))
        return Menu("Genres", items)

    def _podcasts_menu(self) -> Menu:
        shows: dict[str, list] = {}
        for song in self._songs("podcast"):
            shows.setdefault(song.album or "Podcast", []).append(song)
        items = []
        for show, episodes in sorted(shows.items(), key=lambda kv: kv[0].casefold()):
            ids = [s.id for s in sorted(episodes, key=lambda s: s.added or 0, reverse=True)]
            items.append(Item(show, open=lambda show=show, ids=ids: self._song_list(show, ids)))
        return Menu("Podcasts", items)

    def _shuffle(self) -> None:
        ids = [s.id for s in self._songs()]
        if ids:
            random.shuffle(ids)
            self.play_requested.emit(ids, 0)
            self.now_playing = True
            self.update()

    def _show_now_playing(self) -> None:
        if self.current is not None:
            self.now_playing = True
            self.update()

    # --- using it -------------------------------------------------------------------------------

    def scroll(self, steps: int) -> None:
        if self.now_playing or not self.stack:
            return
        menu = self.stack[-1]
        new = max(0, min(len(menu.items) - 1, menu.selected + steps))
        if new == menu.selected:
            return
        menu.selected = new
        if menu.selected < menu.top:
            menu.top = menu.selected
        elif menu.selected >= menu.top + VISIBLE_ROWS:
            menu.top = menu.selected - VISIBLE_ROWS + 1
        if self.tick:
            self.tick()
        self.update()

    def select(self) -> None:
        if self.now_playing:
            return
        if not self.stack or not self.stack[-1].items:
            return
        item = self.stack[-1].items[self.stack[-1].selected]
        if item.open:
            self.stack.append(item.open())
        elif item.play:
            ids, index = item.play
            self.play_requested.emit(ids, index)
            self.now_playing = True
        elif item.action:
            item.action()
        self.update()

    def back(self) -> None:
        if self.now_playing:
            self.now_playing = False
        elif len(self.stack) > 1:
            self.stack.pop()
        self.update()

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Down:
            self.scroll(1)
        elif key == Qt.Key.Key_Up:
            self.scroll(-1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) or (key == Qt.Key.Key_Right and not self.now_playing):
            self.select()
        elif key == Qt.Key.Key_Right:
            self.next_requested.emit()
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_Backspace, Qt.Key.Key_Left):
            self.back()
        elif key == Qt.Key.Key_Space:
            self.toggle_requested.emit()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):  # noqa: N802
        delta = event.angleDelta().y()
        if delta:
            self.scroll(-1 if delta > 0 else 1)

    def _wheel(self) -> tuple[QPointF, float, float]:
        _, center, radius, inner = ipod_geometry(QRectF(self.rect()).adjusted(10, 10, -10, -10))
        return center, radius, inner

    def mousePressEvent(self, event):  # noqa: N802
        self.setFocus()
        center, radius, inner = self._wheel()
        point = event.position()
        distance = math.hypot(point.x() - center.x(), point.y() - center.y())
        self._pressed_at = point
        self._turn = math.degrees(math.atan2(point.y() - center.y(), point.x() - center.x())) \
            if inner < distance <= radius else None
        self._turned = 0.0

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._turn is None:
            return
        center, _, _ = self._wheel()
        point = event.position()
        angle = math.degrees(math.atan2(point.y() - center.y(), point.x() - center.x()))
        change = (angle - self._turn + 180) % 360 - 180  # clockwise is positive (y grows down)
        self._turn = angle
        self._turned += change
        while abs(self._turned) >= STEP_DEGREES:
            step = 1 if self._turned > 0 else -1
            self._turned -= step * STEP_DEGREES
            self.scroll(step)

    def mouseReleaseEvent(self, event):  # noqa: N802
        start, self._pressed_at = self._pressed_at, None
        if start is None or (event.position() - start).manhattanLength() > 6:
            self._turn = None
            return
        self._turn = None
        center, radius, inner = self._wheel()
        dx, dy = start.x() - center.x(), start.y() - center.y()
        distance = math.hypot(dx, dy)
        if distance <= inner:
            self.select()
        elif distance <= radius:
            angle = math.degrees(math.atan2(dy, dx))  # 0 = right, 90 = bottom
            if -135 <= angle < -45:
                self.back()  # MENU
            elif 45 <= angle < 135:
                self.toggle_requested.emit()
            elif -45 <= angle < 45:
                self.next_requested.emit()
            else:
                self.previous_requested.emit()

    # --- drawing --------------------------------------------------------------------------------

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        if self.sync_progress is not None:
            display = self._sync_screen
        elif self.contents is None:
            display = None
        else:
            display = self._now_playing_screen if self.now_playing and self.current is not None else self._menu_screen
        paint_ipod(painter, QRectF(self.rect()).adjusted(10, 10, -10, -10), self.name, self.line, self.font(),
                   display=display)

    def _font(self, size: float, bold: bool = False) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(max(7, int(size)))
        font.setBold(bold)
        return font

    def _title_bar(self, painter: QPainter, screen: QRectF, title: str) -> QRectF:
        bar = QRectF(screen.left() + 1, screen.top() + 1, screen.width() - 2, screen.height() * 0.15)
        gradient = QLinearGradient(bar.topLeft(), bar.bottomLeft())
        gradient.setColorAt(0, QColor("#fbfbfb"))
        gradient.setColorAt(1, QColor("#c9ced6"))
        painter.fillRect(bar, gradient)
        painter.setPen(QPen(QColor("#8e959f"), 1))
        painter.drawLine(bar.bottomLeft(), bar.bottomRight())
        painter.setFont(self._font(bar.height() * 0.55, True))
        painter.setPen(QColor("#111111"))
        text = painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(bar.width() * 0.62))
        painter.drawText(bar, Qt.AlignmentFlag.AlignCenter, text)
        if self.playing:  # the little play symbol on the left
            path = QPainterPath()
            x, y, t = bar.left() + bar.height() * 0.55, bar.center().y(), bar.height() * 0.2
            path.moveTo(x - t * 0.8, y - t)
            path.lineTo(x + t, y)
            path.lineTo(x - t * 0.8, y + t)
            path.closeSubpath()
            painter.fillPath(path, QColor("#2b2b2b"))
        battery = QRectF(bar.right() - bar.height() * 1.4, bar.center().y() - bar.height() * 0.2,
                         bar.height() * 0.9, bar.height() * 0.4)
        painter.setPen(QPen(QColor("#3a3a3a"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(battery, 1.5, 1.5)
        fill = QLinearGradient(battery.topLeft(), battery.bottomLeft())
        fill.setColorAt(0, QColor("#bff07a"))
        fill.setColorAt(1, QColor("#4aa31b"))
        painter.fillRect(battery.adjusted(1.5, 1.5, -battery.width() * 0.2, -1.5), fill)
        return bar

    def _menu_screen(self, painter: QPainter, screen: QRectF) -> None:
        painter.save()
        painter.setClipRect(screen.adjusted(1, 1, -1, -1))
        painter.fillRect(screen, QColor("#ffffff"))
        menu = self.stack[-1]
        bar = self._title_bar(painter, screen, menu.title)
        row_height = (screen.bottom() - bar.bottom()) / VISIBLE_ROWS
        scrollbar = len(menu.items) > VISIBLE_ROWS
        width = screen.width() - (row_height * 0.35 if scrollbar else 0)
        painter.setFont(self._font(row_height * 0.5, True))
        for slot, index in enumerate(range(menu.top, min(len(menu.items), menu.top + VISIBLE_ROWS))):
            item = menu.items[index]
            row = QRectF(screen.left() + 1, bar.bottom() + slot * row_height, width - 1, row_height)
            selected = index == menu.selected
            if selected:
                gradient = QLinearGradient(row.topLeft(), row.bottomLeft())
                gradient.setColorAt(0, QColor("#7fb8f3"))
                gradient.setColorAt(0.5, QColor("#3c8ae6"))
                gradient.setColorAt(0.51, QColor("#2a7ae0"))
                gradient.setColorAt(1, QColor("#3f93ee"))
                painter.fillRect(row, gradient)
            painter.setPen(QColor("#ffffff" if selected else "#111111"))
            text = painter.fontMetrics().elidedText(item.label, Qt.TextElideMode.ElideRight,
                                                    int(row.width() - row_height * 1.1))
            painter.drawText(row.adjusted(row_height * 0.3, 0, 0, 0),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            if item.arrow:
                painter.setPen(QPen(QColor("#ffffff" if selected else "#7a7a7a"), max(1.2, row_height * 0.08),
                                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                x, y, k = row.right() - row_height * 0.45, row.center().y(), row_height * 0.13
                painter.drawLine(QPointF(x - k, y - k * 1.6), QPointF(x + k * 0.4, y))
                painter.drawLine(QPointF(x + k * 0.4, y), QPointF(x - k, y + k * 1.6))
        if scrollbar:
            track = QRectF(screen.right() - row_height * 0.35, bar.bottom() + 2, row_height * 0.3,
                           screen.bottom() - bar.bottom() - 4)
            painter.setPen(QPen(QColor("#9a9a9a"), 1))
            painter.setBrush(QColor("#f2f2f2"))
            painter.drawRect(track)
            share = VISIBLE_ROWS / len(menu.items)
            thumb = QRectF(track.left(), track.top() + track.height() * menu.top / len(menu.items), track.width(),
                           max(4.0, track.height() * share))
            painter.setBrush(QColor("#6f8fb8"))
            painter.drawRect(thumb)
        if not menu.items:
            painter.setPen(QColor("#777777"))
            painter.drawText(screen.adjusted(0, bar.height(), 0, 0), Qt.AlignmentFlag.AlignCenter, "Empty")
        painter.restore()

    def _now_playing_screen(self, painter: QPainter, screen: QRectF) -> None:
        painter.save()
        painter.setClipRect(screen.adjusted(1, 1, -1, -1))
        background = QLinearGradient(screen.topLeft(), screen.bottomLeft())
        background.setColorAt(0, QColor("#ffffff"))
        background.setColorAt(1, QColor("#dfe6ee"))
        painter.fillRect(screen, background)
        bar = self._title_bar(painter, screen, "Now Playing")
        item = self.current
        number, total = self.queue_info
        body = QRectF(screen.left() + 6, bar.bottom() + 4, screen.width() - 12, screen.bottom() - bar.bottom() - 8)
        painter.setPen(QColor("#333333"))
        painter.setFont(self._font(body.height() * 0.1))
        if total:
            painter.drawText(QRectF(body.left(), body.top(), body.width(), body.height() * 0.14),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{number} of {total}")
        art = QRectF(body.left(), body.top() + body.height() * 0.17, body.height() * 0.5, body.height() * 0.5)
        cover = self._cover()
        if cover is not None and not cover.isNull():
            painter.drawImage(art, cover)
        else:
            painter.fillRect(art, QColor("#c9d2dd"))
            painter.setPen(QColor("#ffffff"))
            painter.setFont(self._font(art.height() * 0.55))
            painter.drawText(art, Qt.AlignmentFlag.AlignCenter, "♪")
        text_left = art.right() + 6
        lines = [(item.title, True), (item.artist, False), (item.album, False)]
        for n, (text, bold) in enumerate(lines):
            painter.setFont(self._font(body.height() * 0.11, bold))
            painter.setPen(QColor("#111111" if bold else "#444444"))
            box = QRectF(text_left, art.top() + n * art.height() / 3, body.right() - text_left, art.height() / 3)
            painter.drawText(box, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(text or "", Qt.TextElideMode.ElideRight, int(box.width())))
        progress = QRectF(body.left() + body.width() * 0.04, body.bottom() - body.height() * 0.2,
                          body.width() * 0.92, max(4.0, body.height() * 0.07))
        painter.setPen(QPen(QColor("#6d6d6d"), 1))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRect(progress)
        if self.duration:
            done = QRectF(progress.left(), progress.top(), progress.width() * min(1.0, self.position / self.duration),
                          progress.height())
            fill = QLinearGradient(done.topLeft(), done.bottomLeft())
            fill.setColorAt(0, QColor("#8cc0f7"))
            fill.setColorAt(1, QColor("#2a76db"))
            painter.fillRect(done, fill)

        def clock(ms: int) -> str:
            seconds = max(0, round(ms / 1000))
            return f"{seconds // 60}:{seconds % 60:02d}"

        painter.setFont(self._font(body.height() * 0.09))
        painter.setPen(QColor("#333333"))
        times = QRectF(progress.left(), progress.bottom() + 1, progress.width(), body.height() * 0.12)
        painter.drawText(times, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, clock(self.position))
        painter.drawText(times, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         "-" + clock(max(0, self.duration - self.position)))
        painter.restore()

    def _cover(self) -> QImage | None:
        if self._cover_cache is not None and self._cover_cache[0] is self.current:
            return self._cover_cache[1]
        image = self.cover_for(self.current) if self.cover_for and self.current is not None else None
        self._cover_cache = (self.current, image)
        return image
