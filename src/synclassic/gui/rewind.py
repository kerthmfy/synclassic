"""iPod Rewind: your listening recap as a picture to share (1080 x 1350, portrait).

Top songs on an iPod-style menu screen, top artists with bars, your top album's cover, total
plays and hours. Drawn here with Qt (no web page), so it looks the same everywhere.
"""

from __future__ import annotations

import pathlib

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from synclassic import stats

WIDTH, HEIGHT = 1080, 1350


def _font(size: int, bold: bool = False) -> QFont:
    from synclassic.gui.theme import font_family

    font = QFont(font_family())
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def _elide(painter: QPainter, text: str, width: float) -> str:
    return painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(width))


def render(data: stats.Stats, heading: str, cover: QImage | None = None, icon: QImage | None = None) -> QImage:
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # Background: deep blue night with a soft Aqua glow and pinstripes.
    sky = QLinearGradient(0, 0, 0, HEIGHT)
    sky.setColorAt(0, QColor("#0d1b33"))
    sky.setColorAt(1, QColor("#050a14"))
    painter.fillRect(image.rect(), sky)
    glow = QRadialGradient(QPointF(WIDTH * 0.8, HEIGHT * 0.12), WIDTH * 0.7)
    glow.setColorAt(0, QColor(64, 140, 235, 120))
    glow.setColorAt(1, QColor(64, 140, 235, 0))
    painter.fillRect(image.rect(), glow)
    painter.setPen(QColor(255, 255, 255, 10))
    for y in range(0, HEIGHT, 4):
        painter.drawLine(0, y, WIDTH, y)

    margin = 72
    # Heading.
    x = margin
    if icon is not None:
        painter.drawImage(QRectF(margin - 6, 64, 96, 96), icon)
        x = margin + 108
    painter.setPen(QColor("#9cc6f5"))
    painter.setFont(_font(30, True))
    painter.drawText(QPointF(x, 100), "iPod Rewind")
    painter.setPen(QColor("#ffffff"))
    painter.setFont(_font(56, True))
    painter.drawText(QPointF(x, 160), _elide(painter, heading, WIDTH - x - margin))

    # Big numbers.
    top = 230
    hours = f"{data.hours:,.0f}" if data.hours >= 10 else f"{data.hours:.1f}"
    for i, (value, label) in enumerate(((f"{data.plays:,}", "plays"), (hours, "hours of music"),
                                        (f"{len(data.artists)}", "artists" if len(data.artists) < 25 else "artists+"))):
        left = margin + i * (WIDTH - 2 * margin) / 3
        painter.setPen(QColor("#ffffff"))
        painter.setFont(_font(64, True))
        painter.drawText(QPointF(left, top + 60), value)
        painter.setPen(QColor("#8fa6c4"))
        painter.setFont(_font(24))
        painter.drawText(QPointF(left, top + 96), label)

    # Top songs on an iPod screen.
    screen = QRectF(margin, 380, WIDTH - 2 * margin, 470)
    frame = QPainterPath()
    frame.addRoundedRect(screen.adjusted(-14, -14, 14, 14), 26, 26)
    painter.fillPath(frame, QColor("#141414"))
    painter.setPen(QPen(QColor("#000000"), 3))
    painter.setBrush(QColor("#ffffff"))
    painter.drawRect(screen)
    bar = QRectF(screen.left(), screen.top(), screen.width(), 62)
    title_bar = QLinearGradient(bar.topLeft(), bar.bottomLeft())
    title_bar.setColorAt(0, QColor("#fbfbfb"))
    title_bar.setColorAt(1, QColor("#c9ced6"))
    painter.fillRect(bar.adjusted(1.5, 1.5, -1.5, 0), title_bar)
    painter.setPen(QColor("#111111"))
    painter.setFont(_font(30, True))
    painter.drawText(bar, Qt.AlignmentFlag.AlignCenter, "Top Songs")
    row_height = (screen.height() - bar.height()) / 5
    for i, (title, artist, plays) in enumerate(data.songs[:5]):
        row = QRectF(screen.left() + 1.5, bar.bottom() + i * row_height, screen.width() - 3, row_height)
        if i == 0:
            highlight = QLinearGradient(row.topLeft(), row.bottomLeft())
            highlight.setColorAt(0, QColor("#7fb8f3"))
            highlight.setColorAt(0.5, QColor("#3c8ae6"))
            highlight.setColorAt(0.51, QColor("#2a7ae0"))
            highlight.setColorAt(1, QColor("#3f93ee"))
            painter.fillRect(row, highlight)
        painter.setPen(QColor("#ffffff" if i == 0 else "#111111"))
        painter.setFont(_font(31, True))
        painter.drawText(QPointF(row.left() + 26, row.top() + row_height * 0.47),
                         _elide(painter, f"{i + 1}. {title}", row.width() - 210))
        painter.setFont(_font(24))
        painter.setPen(QColor("#e8f1ff" if i == 0 else "#666666"))
        painter.drawText(QPointF(row.left() + 26 + painter.fontMetrics().horizontalAdvance("1. ") * 1.2,
                                 row.top() + row_height * 0.82), _elide(painter, artist, row.width() - 240))
        painter.setFont(_font(26, True))
        painter.drawText(QRectF(row.right() - 200, row.top(), 176, row_height),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{plays} plays")
    if not data.songs:
        painter.setPen(QColor("#777777"))
        painter.setFont(_font(28))
        painter.drawText(screen.adjusted(0, bar.height(), 0, 0), Qt.AlignmentFlag.AlignCenter, "No plays yet")

    # Top artists (bars) and the top album (cover).
    section = 905
    painter.setPen(QColor("#9cc6f5"))
    painter.setFont(_font(28, True))
    painter.drawText(QPointF(margin, section), "Top Artists")
    most = max((p for _, p in data.artists[:5]), default=1)
    column = (WIDTH - 2 * margin) * 0.58
    for i, (artist, plays) in enumerate(data.artists[:5]):
        y = section + 28 + i * 66
        painter.setPen(QColor("#ffffff"))
        painter.setFont(_font(26, True))
        painter.drawText(QPointF(margin, y + 26), _elide(painter, artist, column - 80))
        width = max(8.0, (column - 10) * plays / most)
        bar_rect = QRectF(margin, y + 38, width, 14)
        fill = QLinearGradient(bar_rect.topLeft(), bar_rect.topRight())
        fill.setColorAt(0, QColor("#2a76db"))
        fill.setColorAt(1, QColor("#8cc0f7"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(bar_rect, 7, 7)
        painter.setPen(QColor("#8fa6c4"))
        painter.setFont(_font(20))
        painter.drawText(QPointF(margin + width + 10, y + 51), str(plays))
    album_left = margin + column + 40
    painter.setPen(QColor("#9cc6f5"))
    painter.setFont(_font(28, True))
    painter.drawText(QPointF(album_left, section), "Top Album")
    size = min(WIDTH - margin - album_left, HEIGHT - (section + 26) - 160)  # room for its name and the footer
    art = QRectF(album_left, section + 26, size, size)
    if cover is not None and not cover.isNull():
        painter.drawImage(art, cover)
    else:
        placeholder = QLinearGradient(art.topLeft(), art.bottomRight())
        placeholder.setColorAt(0, QColor("#3a4c66"))
        placeholder.setColorAt(1, QColor("#1b2536"))
        painter.fillRect(art, placeholder)
        painter.setPen(QColor(255, 255, 255, 120))
        painter.setFont(_font(int(size * 0.4)))
        painter.drawText(art, Qt.AlignmentFlag.AlignCenter, "♪")
    if data.albums:
        album, artist, _ = data.albums[0]
        painter.setPen(QColor("#ffffff"))
        painter.setFont(_font(24, True))
        painter.drawText(QPointF(album_left, art.bottom() + 34), _elide(painter, album, size))
        painter.setPen(QColor("#8fa6c4"))
        painter.setFont(_font(20))
        painter.drawText(QPointF(album_left, art.bottom() + 62), _elide(painter, artist, size))

    painter.setPen(QColor(255, 255, 255, 110))
    painter.setFont(_font(20))
    painter.drawText(QRectF(0, HEIGHT - 60, WIDTH, 30), Qt.AlignmentFlag.AlignCenter, "Made with Synclassic")
    painter.end()
    return image


def album_cover(library, album: str, artist: str) -> QImage | None:
    """The cover inside one of the album's songs on this Mac, if any."""
    from synclassic.ipod.artwork import embedded_cover

    for item in library.tracks_in_playlists():
        if item.track.album == album and item.has_file:
            data = embedded_cover(pathlib.Path(item.file_path))  # type: ignore[arg-type]
            if data:
                image = QImage.fromData(data)
                if not image.isNull():
                    return image
    return None


class RewindDialog(QDialog):
    def __init__(self, library, ipod_songs=None, parent=None):
        import datetime

        super().__init__(parent)
        self.library = library
        self.ipod_songs = ipod_songs
        self.image: QImage | None = None
        self.setWindowTitle("iPod Rewind")
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Recap of:"))
        self.period = QComboBox()
        year = datetime.date.today().year
        self.period.addItem(f"This year ({year})", ("year", year))
        self.period.addItem(f"Last year ({year - 1})", ("year", year - 1))
        self.period.addItem("Last 30 days", ("days", 30))
        if ipod_songs:
            self.period.addItem("All time on my iPod", ("ipod", 0))
        self.period.currentIndexChanged.connect(self.make)
        row.addWidget(self.period)
        row.addStretch(1)
        layout.addLayout(row)
        self.preview = QLabel()
        self.preview.setFixedSize(432, 540)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.preview)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setImage(self.image) if self.image else None)
        save = QPushButton("Save Image…")
        save.setProperty("aqua", True)
        save.clicked.connect(self.save)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for button in (copy, save, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        # A year with no history yet: start with the iPod's all-time counts.
        if ipod_songs and not stats.for_year(year).plays:
            self.period.setCurrentIndex(self.period.count() - 1)
        self.make()

    def data(self) -> tuple[stats.Stats, str]:
        kind, value = self.period.currentData()
        if kind == "year":
            return stats.for_year(value), f"{value} in music"
        if kind == "days":
            return stats.from_history(value), "My last 30 days"
        return stats.from_ipod(self.ipod_songs or []), "My iPod, all time"

    def make(self) -> None:
        from PySide6.QtGui import QPixmap

        from synclassic.gui.icon import render_icon

        data, heading = self.data()
        cover = album_cover(self.library, data.albums[0][0], data.albums[0][1]) if data.albums else None
        self.image = render(data, heading, cover, render_icon(192))
        self.preview.setPixmap(QPixmap.fromImage(self.image).scaled(
            self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def save(self) -> None:
        if self.image is None:
            return
        _, heading = self.data()
        default = pathlib.Path.home() / "Desktop" / f"iPod Rewind - {heading}.png"
        path, _ = QFileDialog.getSaveFileName(self, "Save iPod Rewind", str(default), "PNG image (*.png)")
        if path:
            self.image.save(path)
