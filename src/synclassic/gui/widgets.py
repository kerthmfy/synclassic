"""Hand-drawn Aqua-era pieces: toolbar, round glossy buttons, the iTunes-style LCD, the source
list, status pills, the iPod illustration and its capacity bar."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QTreeWidget,
    QAbstractButton,
    QApplication,
    QStyleOptionViewItem,
    QLabel,
    QProgressBar,
    QStyledItemDelegate,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from synclassic.gui import theme


def _gradient(rect: QRectF, *stops: tuple[float, str | QColor], horizontal: bool = False) -> QLinearGradient:
    gradient = QLinearGradient(rect.topLeft(), rect.topRight() if horizontal else rect.bottomLeft())
    for position, color in stops:
        gradient.setColorAt(position, color if isinstance(color, QColor) else QColor(color))
    return gradient


# --- the window's top bar ------------------------------------------------------------------------


class GradientBar(QWidget):
    """The graphite gradient behind the toolbar (top) or the bottom bar."""

    def __init__(self, top: str, bottom: str, line_at_bottom: bool, parent=None):
        """`top`, `bottom`: palette names (e.g. "toolbar_top") or colours."""
        super().__init__(parent)
        self.top, self.bottom, self.line_at_bottom = top, bottom, line_at_bottom

    @staticmethod
    def _colour(value: str) -> str:
        return value if value.startswith("#") else theme.c(value)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        rect = QRectF(self.rect())
        painter.fillRect(rect, _gradient(rect, (0, self._colour(self.top)), (1, self._colour(self.bottom))))
        painter.setPen(QColor(255, 255, 255, 40 if theme.is_dark() else 150))
        painter.drawLine(0, 0 if self.line_at_bottom else 1, self.width(), 0 if self.line_at_bottom else 1)
        painter.setPen(QColor(theme.TOOLBAR_LINE))
        y = self.height() - 1 if self.line_at_bottom else 0
        painter.drawLine(0, y, self.width(), y)


# --- round glossy toolbar buttons ------------------------------------------------------------------


class GlossyButton(QAbstractButton):
    """A round graphite button with a glass highlight and a drawn symbol, label underneath."""

    def __init__(self, symbol: str, label: str, diameter: int = 34, parent=None):
        super().__init__(parent)
        self.symbol, self.label, self.diameter = symbol, label, diameter
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label)
        font = QFont(self.font())
        font.setPixelSize(10)
        self.label_font = font

    def sizeHint(self) -> QSize:  # noqa: N802
        width = max(self.diameter + 8, QFontMetrics(self.label_font).horizontalAdvance(self.label) + 8)
        return QSize(width, self.diameter + 18)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self.diameter
        x = (self.width() - d) / 2
        circle = QRectF(x, 2, d, d)
        enabled = self.isEnabled()
        pressed = self.isDown()
        # Outer ring and body.
        painter.setPen(QPen(QColor("#4d4d4d" if enabled else "#9c9c9c"), 1))
        if pressed:
            body = _gradient(circle, (0, "#8e8e8e"), (0.5, "#a9a9a9"), (1, "#d2d2d2"))
        else:
            body = _gradient(circle, (0, "#fdfdfd"), (0.5, "#d9d9d9"), (0.51, "#c7c7c7"), (1, "#f2f2f2"))
        painter.setBrush(body)
        painter.drawEllipse(circle)
        # Glass highlight on the top half.
        gloss = QRectF(circle.x() + d * 0.16, circle.y() + 2, d * 0.68, d * 0.42)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_gradient(gloss, (0, QColor(255, 255, 255, 235)), (1, QColor(255, 255, 255, 20))))
        painter.drawEllipse(gloss)
        # Symbol.
        color = QColor("#2e2e2e" if enabled else "#a3a3a3")
        self._draw_symbol(painter, circle.center(), d, color)
        # Label.
        painter.setFont(self.label_font)
        painter.setPen(QColor(theme.c("text") if enabled else theme.c("disabled_text")))
        painter.drawText(QRectF(0, d + 3, self.width(), 14), Qt.AlignmentFlag.AlignHCenter, self.label)

    def _draw_symbol(self, painter: QPainter, c: QPointF, d: float, color: QColor) -> None:
        s = d * 0.26
        pen = QPen(color, max(2.0, d / 13), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.symbol == "plus":
            painter.drawLine(QPointF(c.x() - s, c.y()), QPointF(c.x() + s, c.y()))
            painter.drawLine(QPointF(c.x(), c.y() - s), QPointF(c.x(), c.y() + s))
        elif self.symbol == "stop":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(c.x() - s * 0.8, c.y() - s * 0.8, s * 1.6, s * 1.6), 2, 2)
        elif self.symbol in ("refresh", "sync"):
            arc = QRectF(c.x() - s, c.y() - s, 2 * s, 2 * s)
            if self.symbol == "refresh":
                painter.drawArc(arc, 60 * 16, 280 * 16)
                self._arrow_head(painter, c, s, 60, color)
            else:
                painter.drawArc(arc, 20 * 16, 140 * 16)
                painter.drawArc(arc, 200 * 16, 140 * 16)
                self._arrow_head(painter, c, s, 20, color)
                self._arrow_head(painter, c, s, 200, color)
        elif self.symbol in ("play", "pause", "prev", "next"):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)

            def triangle(x: float, direction: int, size: float) -> None:
                path = QPainterPath()
                path.moveTo(x, c.y() - size)
                path.lineTo(x + direction * size * 1.5, c.y())
                path.lineTo(x, c.y() + size)
                path.closeSubpath()
                painter.drawPath(path)

            if self.symbol == "play":
                triangle(c.x() - s * 0.55, 1, s * 0.95)
            elif self.symbol == "pause":
                for x in (c.x() - s * 0.6, c.x() + s * 0.2):
                    painter.drawRoundedRect(QRectF(x, c.y() - s * 0.85, s * 0.4, s * 1.7), 1, 1)
            else:
                direction = 1 if self.symbol == "next" else -1
                size = s * 0.62
                start = c.x() - direction * size * 1.35
                triangle(start, direction, size)
                triangle(start + direction * size * 1.3, direction, size)
                bar_x = start + direction * size * 2.75 - (size * 0.3 if direction < 0 else 0)
                painter.drawRect(QRectF(bar_x if direction > 0 else start - size * 0.35, c.y() - size, size * 0.3, 2 * size))
        elif self.symbol == "gear":
            painter.drawEllipse(c, s * 0.45, s * 0.45)
            for n in range(8):
                angle = math.radians(n * 45)
                painter.drawLine(QPointF(c.x() + math.cos(angle) * s * 0.75, c.y() + math.sin(angle) * s * 0.75),
                                 QPointF(c.x() + math.cos(angle) * s * 1.05, c.y() + math.sin(angle) * s * 1.05))
        elif self.symbol == "eject":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            path = QPainterPath()
            path.moveTo(c.x(), c.y() - s)
            path.lineTo(c.x() + s, c.y() + s * 0.15)
            path.lineTo(c.x() - s, c.y() + s * 0.15)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawRect(QRectF(c.x() - s, c.y() + s * 0.45, 2 * s, s * 0.4))

    @staticmethod
    def _arrow_head(painter: QPainter, c: QPointF, s: float, degrees: float, color: QColor) -> None:
        angle = math.radians(degrees)
        tip = QPointF(c.x() + math.cos(angle) * s, c.y() - math.sin(angle) * s)
        tangent = angle + math.pi / 2  # counter-clockwise direction
        back = QPointF(math.cos(tangent), -math.sin(tangent))
        side = QPointF(math.cos(angle), -math.sin(angle))
        size = s * 0.55
        path = QPainterPath()
        path.moveTo(tip + back * size * 0.6)
        path.lineTo(tip - back * size * 0.5 + side * size * 0.55)
        path.lineTo(tip - back * size * 0.5 - side * size * 0.55)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(path)


# --- the LCD display ---------------------------------------------------------------------------


class LcdDisplay(QWidget):
    """The iTunes-style status panel: two lines of text and a thin progress bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 50)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 5, 16, 5)
        layout.setSpacing(1)
        self.primary = QLabel("Synclassic")
        self.primary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setBold(True)
        self.primary.setFont(font)
        self.secondary = QLabel("")
        self.secondary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        small = QFont(self.font())
        small.setPixelSize(11)
        self.secondary.setFont(small)
        for label in (self.primary, self.secondary):
            label.setStyleSheet(f"color: {theme.LCD_TEXT}; background: transparent;")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(7)
        self.bar.setStyleSheet(
            "QProgressBar { border: 1px solid #5e5e5e; border-radius: 3px; background: rgba(255,255,255,120); }"
            "QProgressBar::chunk { border-radius: 2px; background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 #6b6b6b, stop:1 #2e2e2e); }"
        )
        self.bar.hide()
        layout.addWidget(self.primary)
        layout.addWidget(self.secondary)
        layout.addWidget(self.bar)

        # Now playing: elapsed | position (drag to seek) | remaining.
        from PySide6.QtWidgets import QHBoxLayout, QSlider

        self.seek_row = QWidget()
        self.seek_row.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self.seek_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.elapsed = QLabel("0:00")
        self.remaining = QLabel("-0:00")
        for label in (self.elapsed, self.remaining):
            label.setFont(small)
            label.setStyleSheet(f"color: {theme.LCD_TEXT}; background: transparent;")
            label.setFixedWidth(42)
        self.remaining.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setFixedHeight(12)
        self.seek.setStyleSheet(
            "QSlider::groove:horizontal { height: 5px; border: 1px solid #5e5e5e; border-radius: 3px;"
            " background: rgba(255,255,255,120); }"
            "QSlider::sub-page:horizontal { border-radius: 3px; background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 #6b6b6b, stop:1 #2e2e2e); }"
            "QSlider::handle:horizontal { width: 9px; height: 9px; margin: -3px 0; border-radius: 4px;"
            " border: 1px solid #3a3a3a; background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:1 #9a9a9a); }"
        )
        row.addWidget(self.elapsed)
        row.addWidget(self.seek, 1)
        row.addWidget(self.remaining)
        self.seek_row.hide()
        layout.addWidget(self.seek_row)

    def show_now_playing(self, title: str, line: str) -> None:
        self.primary.setText(title)
        self.secondary.setText(line)
        self.bar.hide()
        self.seek_row.show()

    def hide_now_playing(self) -> None:
        self.seek_row.hide()

    def set_times(self, position_ms: int, duration_ms: int) -> None:
        def fmt(ms: int) -> str:
            seconds = max(0, round(ms / 1000))
            return f"{seconds // 60}:{seconds % 60:02d}"

        self.elapsed.setText(fmt(position_ms))
        self.remaining.setText("-" + fmt(max(0, duration_ms - position_ms)))
        if not self.seek.isSliderDown():
            self.seek.setRange(0, max(1, duration_ms))
            self.seek.setValue(position_ms)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # Soft outer highlight, then the bezel and the lit panel.
        painter.setPen(QPen(QColor(255, 255, 255, 140), 1))
        painter.drawRoundedRect(rect.adjusted(0, 1, 0, 0), 7, 7)
        painter.setPen(QPen(QColor(theme.LCD_BORDER), 1))
        painter.setBrush(_gradient(rect, (0, theme.LCD_TOP), (1, theme.LCD_BOTTOM)))
        painter.drawRoundedRect(rect.adjusted(0, 0, 0, -1), 7, 7)
        painter.setPen(QPen(QColor(0, 0, 0, 35), 1))
        painter.drawLine(QPointF(rect.left() + 6, rect.top() + 1.5), QPointF(rect.right() - 6, rect.top() + 1.5))


# --- source list -----------------------------------------------------------------------------------

SECTION_ROLE = Qt.ItemDataRole.UserRole + 1  # True for "LIBRARY"/"PLAYLISTS"/"DEVICES" headings
ICON_ROLE = Qt.ItemDataRole.UserRole + 2  # "playlist", "attention", "ipod"
COUNT_ROLE = Qt.ItemDataRole.UserRole + 3  # small grey text on the right
DEPTH_ROLE = Qt.ItemDataRole.UserRole + 4  # 1 = indented under the iPod


class SourceListDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):  # noqa: N802
        return QSize(option.rect.width(), 22 if index.data(SECTION_ROLE) else 24)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect)
        if index.data(SECTION_ROLE):
            font = QFont(option.font)
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255, 160))
            painter.drawText(rect.adjusted(9, 7, 0, 1), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             index.data().upper())
            painter.setPen(QColor(theme.SOURCE_HEADER))
            painter.drawText(rect.adjusted(9, 6, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             index.data().upper())
            painter.restore()
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            active = bool(option.state & QStyle.StateFlag.State_Active)
            top, bottom = (theme.SELECT_TOP, theme.SELECT_BOTTOM) if active else (theme.SELECT_INACTIVE_TOP, theme.SELECT_INACTIVE_BOTTOM)
            painter.fillRect(rect, _gradient(rect, (0, top), (1, bottom)))
            painter.setPen(QColor(theme.SELECT_BORDER if active else theme.c("select_inactive_border")))
            painter.drawLine(rect.topLeft(), rect.topRight())
        indent = 16 * (index.data(DEPTH_ROLE) or 0)
        if index.data(ICON_ROLE) in ("ipod", "syncing"):  # disclosure triangle: the iPod's contents are below
            triangle = QPainterPath()
            x, y = rect.left() + 7, rect.center().y()
            triangle.moveTo(x, y - 3)
            triangle.lineTo(x + 7, y - 3)
            triangle.lineTo(x + 3.5, y + 3)
            triangle.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ffffff" if selected else theme.c("source_header")))
            painter.drawPath(triangle)
        icon_rect = QRectF(rect.left() + 18 + indent, rect.center().y() - 8, 16, 16)
        draw_source_icon(painter, icon_rect, index.data(ICON_ROLE) or "playlist")
        text_rect = rect.adjusted(42 + indent, 0, -8, 0)
        count = index.data(COUNT_ROLE)
        font = QFont(option.font)
        font.setPixelSize(12)
        font.setBold(selected)
        if count:
            small = QFont(font)
            small.setPixelSize(10)
            small.setBold(True)
            painter.setFont(small)
            width = QFontMetrics(small).horizontalAdvance(str(count)) + 12
            pill = QRectF(rect.right() - width - 8, rect.center().y() - 7, width, 14)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 230) if selected else QColor(theme.c("source_badge")))
            painter.drawRoundedRect(pill, 7, 7)
            painter.setPen(QColor(theme.SELECT_BOTTOM) if selected else QColor("#ffffff"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, str(count))
            text_rect.setRight(pill.left() - 6)
        painter.setFont(font)
        if selected:
            painter.setPen(QColor(0, 0, 0, 90))
            painter.drawText(text_rect.translated(0, 1), Qt.AlignmentFlag.AlignVCenter,
                             QFontMetrics(font).elidedText(index.data(), Qt.TextElideMode.ElideRight, int(text_rect.width())))
        painter.setPen(QColor("#ffffff" if selected else theme.c("source_text")))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(font).elidedText(index.data(), Qt.TextElideMode.ElideRight, int(text_rect.width())))
        painter.restore()


def draw_source_icon(painter: QPainter, r: QRectF, kind: str) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if kind == "ipod":
        painter.setPen(QPen(QColor("#5a5a5a"), 0.8))
        painter.setBrush(_gradient(r, (0, "#4a4a4a"), (1, "#0c0c0c")))
        body = QRectF(r.center().x() - 5, r.top(), 10, 16)
        painter.drawRoundedRect(body, 2, 2)
        painter.setBrush(_gradient(r, (0, "#bfe3ff"), (1, "#5d9ee6")))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(QRectF(body.left() + 1.6, body.top() + 1.6, 6.8, 5))
        painter.setBrush(QColor("#9a9a9a"))
        painter.drawEllipse(QPointF(body.center().x(), body.bottom() - 4.2), 3, 3)
    elif kind == "attention":
        path = QPainterPath()
        path.moveTo(r.center().x(), r.top() + 1)
        path.lineTo(r.right(), r.bottom() - 1)
        path.lineTo(r.left(), r.bottom() - 1)
        path.closeSubpath()
        painter.setPen(QPen(QColor("#a66a00"), 0.8))
        painter.setBrush(_gradient(r, (0, "#ffe08a"), (1, "#f0a30a")))
        painter.drawPath(path)
        painter.setPen(QPen(QColor("#5a3a00"), 1.6))
        painter.drawLine(QPointF(r.center().x(), r.top() + 6), QPointF(r.center().x(), r.bottom() - 6))
        painter.drawPoint(QPointF(r.center().x(), r.bottom() - 3.5))
    elif kind in ("music", "podcasts"):
        painter.setPen(QPen(QColor("#2a5ea8"), 0.8))
        painter.setBrush(_gradient(r, (0, "#8fc2ff"), (1, "#2f6fc9")))
        painter.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        painter.setPen(QPen(QColor("#ffffff"), 1.4))
        if kind == "music":
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(QPointF(r.left() + 6, r.bottom() - 4.5), 2, 1.6)
            painter.drawLine(QPointF(r.left() + 7.8, r.bottom() - 4.5), QPointF(r.left() + 7.8, r.top() + 3.5))
            painter.drawLine(QPointF(r.left() + 7.8, r.top() + 3.5), QPointF(r.right() - 3.5, r.top() + 5))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            c = r.center()
            painter.drawEllipse(c, 1.6, 1.6)
            painter.drawArc(QRectF(c.x() - 4.5, c.y() - 4.5, 9, 9), 30 * 16, 120 * 16)
            painter.drawArc(QRectF(c.x() - 4.5, c.y() - 4.5, 9, 9), 210 * 16, 120 * 16)
    elif kind == "syncing":  # the iPod while syncing: orange disc, turning arrows
        disc = r.adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor("#b35400"), 0.8))
        painter.setBrush(_gradient(disc, (0, "#ffc06a"), (1, "#e8710a")))
        painter.drawEllipse(disc)
        draw_sync_arrows(painter, disc.center(), disc.width() * 0.27, QColor("#ffffff"), SPIN["angle"], 1.6)
    elif kind == "search":
        lens = QRectF(r.left() + 1.5, r.top() + 1.5, 9.5, 9.5)
        painter.setPen(QPen(QColor("#2a5ea8"), 1.6))
        painter.setBrush(_gradient(lens, (0, "#e3f0ff"), (1, "#9cc6f5")))
        painter.drawEllipse(lens)
        painter.setPen(QPen(QColor("#3a3a3a"), 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(lens.right() - 1.2, lens.bottom() - 1.2), QPointF(r.right() - 1.5, r.bottom() - 1.5))
    elif kind == "stats":
        for i, height in enumerate((6, 11, 8)):
            column = QRectF(r.left() + 1.5 + i * 4.6, r.bottom() - 1 - height, 3.6, height)
            painter.setPen(QPen(QColor("#2a5ea8"), 0.7))
            painter.setBrush(_gradient(column, (0, "#8fc2ff"), (1, "#2f6fc9")))
            painter.drawRect(column)
    elif kind == "albums":
        for offset, color in ((3, "#9aa9bd"), (0, "#5b7aa6")):
            square = QRectF(r.left() + 1 + offset, r.top() + 1 + (3 - offset), 11, 11)
            painter.setPen(QPen(QColor("#33496b"), 0.8))
            painter.setBrush(_gradient(square, (0, QColor(color).lighter(140).name()), (1, color)))
            painter.drawRect(square)
    elif kind == "artists":
        painter.setPen(QPen(QColor("#3e5b85"), 0.8))
        painter.setBrush(_gradient(r, (0, "#c5d8f2"), (1, "#7a9bca")))
        painter.drawEllipse(QPointF(r.center().x(), r.top() + 5), 3.6, 3.6)
        path = QPainterPath()
        path.moveTo(r.left() + 2, r.bottom() - 1)
        path.cubicTo(r.left() + 2, r.top() + 8, r.right() - 2, r.top() + 8, r.right() - 2, r.bottom() - 1)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "genres":
        painter.setPen(QPen(QColor("#6b4a1f"), 0.8))
        painter.setBrush(_gradient(r, (0, "#f3d08a"), (1, "#c98a2b")))
        painter.drawEllipse(QRectF(r.left() + 1.5, r.top() + 6, 9, 9))
        painter.setPen(QPen(QColor("#6b4a1f"), 1.6))
        painter.drawLine(QPointF(r.left() + 8.5, r.top() + 8), QPointF(r.right() - 1.5, r.top() + 1.5))
    elif kind == "smart":
        sheet = r.adjusted(1, 0, -1, 0)
        painter.setPen(QPen(QColor("#6a4f92"), 0.8))
        painter.setBrush(_gradient(sheet, (0, "#efe6fb"), (1, "#b9a3d8")))
        painter.drawRoundedRect(sheet, 2, 2)
        painter.setPen(QPen(QColor("#4b2f73"), 1.3))
        c = sheet.center()
        for n in range(6):
            angle = math.radians(n * 60)
            painter.drawLine(QPointF(c.x() + math.cos(angle) * 2, c.y() + math.sin(angle) * 2),
                             QPointF(c.x() + math.cos(angle) * 4.2, c.y() + math.sin(angle) * 4.2))
    else:  # playlist: a little blue-grey sheet with a note
        sheet = r.adjusted(1, 0, -1, 0)
        painter.setPen(QPen(QColor("#56708f"), 0.8))
        painter.setBrush(_gradient(sheet, (0, "#e9f1fb"), (1, "#a9bcd4")))
        painter.drawRoundedRect(sheet, 2, 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2f4d73"))
        painter.drawEllipse(QPointF(sheet.left() + 5, sheet.bottom() - 4), 2.4, 2)
        painter.drawRect(QRectF(sheet.left() + 6.6, sheet.top() + 3, 1.2, sheet.height() - 7))
        painter.drawRect(QRectF(sheet.left() + 6.6, sheet.top() + 3, 4, 1.6))
    painter.restore()


# --- status pills in the song list -----------------------------------------------------------------


class StatusPillDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index) -> None:
        # Row background/selection as usual, but without the text; then the pill.
        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        background.text = ""
        style = background.widget.style() if background.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, background.widget)
        text = index.data() or ""
        colors = theme.STATUS_COLORS.get(text.lower().replace("ready (your pick)", "your pick"), ("#b5b5b5", "#8c8c8c"))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(option.font)
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        width = QFontMetrics(font).horizontalAdvance(text) + 16
        rect = QRectF(option.rect.left() + 4, option.rect.center().y() - 7.5, width, 15)
        painter.setPen(QPen(QColor(colors[1]), 1))
        painter.setBrush(_gradient(rect, (0, QColor(colors[0]).lighter(125).name()), (0.5, colors[0]), (1, colors[1])))
        painter.drawRoundedRect(rect, 7.5, 7.5)
        painter.setPen(QColor(0, 0, 0, 60))
        painter.drawText(rect.translated(0, -1), Qt.AlignmentFlag.AlignCenter, text)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option, index):  # noqa: N802
        size = super().sizeHint(option, index)
        return QSize(size.width() + 24, max(size.height(), 19))


# --- the iPod illustration ---------------------------------------------------------------------


SPIN = {"angle": 0.0}  # the turning sync arrows (source list, iPod screen) share one angle


def draw_sync_arrows(painter: QPainter, center: QPointF, radius: float, color: QColor, angle: float = 0.0,
                     width: float | None = None) -> None:
    """Two curved arrows chasing each other in a circle (the iTunes sync symbol)."""
    import math

    painter.save()
    painter.translate(center)
    painter.rotate(angle)
    pen = QPen(color, width or max(1.2, radius * 0.32), Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    arc = QRectF(-radius, -radius, 2 * radius, 2 * radius)
    for start in (20, 200):
        painter.drawArc(arc, start * 16, 120 * 16)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    head = radius * 0.55
    for degrees in (20, 200):
        a = math.radians(degrees)
        tip = QPointF(math.cos(a) * radius, -math.sin(a) * radius)
        back = QPointF(math.sin(a), math.cos(a))  # along the arc, backwards
        out = QPointF(math.cos(a), -math.sin(a))
        path = QPainterPath()
        path.moveTo(tip - back * head * 0.6)
        path.lineTo(tip + back * head * 0.5 + out * head * 0.62)
        path.lineTo(tip + back * head * 0.5 - out * head * 0.62)
        path.closeSubpath()
        painter.drawPath(path)
    painter.restore()


class IPodGraphic(QWidget):
    """A 5.5G iPod (Video) in black, drawn: glossy body, lit screen, click wheel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.name = "iPod"
        self.line = ""
        self.sync_progress: float | None = None  # syncing: its screen says "Do not disconnect."
        self.setMinimumSize(170, 280)

    def set_screen(self, name: str, line: str) -> None:
        self.name, self.line = name, line
        self.update()

    def set_syncing(self, progress: float | None) -> None:
        self.sync_progress = progress
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        display = None if self.sync_progress is None else self._sync_screen
        paint_ipod(painter, QRectF(self.rect()).adjusted(10, 10, -10, -10), self.name, self.line, self.font(),
                   display=display)

    def _sync_screen(self, painter: QPainter, screen: QRectF) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_gradient(screen, (0, "#ffffff"), (1, "#e4e9ef")))
        painter.drawRect(screen.adjusted(1, 1, -1, -1))
        radius = screen.height() * 0.2
        center = QPointF(screen.center().x(), screen.top() + screen.height() * 0.36)
        painter.setBrush(_gradient(QRectF(center.x() - radius * 1.35, center.y() - radius * 1.35, radius * 2.7,
                                          radius * 2.7), (0, "#ffb347"), (1, "#e8710a")))
        painter.drawEllipse(center, radius * 1.35, radius * 1.35)
        draw_sync_arrows(painter, center, radius * 0.82, QColor("#ffffff"), SPIN["angle"])
        font = QFont(self.font())
        font.setPixelSize(max(9, int(screen.height() * 0.11)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#1d1d1d"))
        painter.drawText(QRectF(screen.left(), screen.top() + screen.height() * 0.62, screen.width(),
                                screen.height() * 0.14), Qt.AlignmentFlag.AlignCenter, "Do not disconnect.")
        bar = QRectF(screen.left() + screen.width() * 0.14, screen.top() + screen.height() * 0.82,
                     screen.width() * 0.72, max(4.0, screen.height() * 0.06))
        painter.setPen(QPen(QColor("#6d6d6d"), 1))
        painter.setBrush(QColor("#f7f7f7"))
        painter.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
        done = QRectF(bar.left(), bar.top(), bar.width() * max(0.0, min(1.0, self.sync_progress or 0)), bar.height())
        if done.width() > 1:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_gradient(done, (0, "#8cc0f7"), (0.5, "#3f8ae6"), (1, "#2a76db")))
            painter.drawRoundedRect(done, bar.height() / 2, bar.height() / 2)
        painter.restore()


def ipod_geometry(area: QRectF) -> tuple[QRectF, QPointF, float, float]:
    """Where paint_ipod puts the screen and the click wheel: (screen, wheel center, wheel radius,
    center button radius)."""
    h = min(area.height(), area.width() * 1.66)
    w = h / 1.66
    body = QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)
    edge = max(1.6, w * 0.012)
    front = body.adjusted(edge, edge, -edge, -edge)
    screen = QRectF(front.left() + w * 0.09, front.top() + w * 0.11, w * 0.80, w * 0.62)
    radius = w * 0.31
    center = QPointF(front.center().x(), front.bottom() - w * 0.06 - radius)
    return screen, center, radius, radius * 0.38


def paint_ipod(painter: QPainter, area: QRectF, name: str, line: str, base_font: QFont, shadow: bool = True,
               display=None) -> None:
    """Draw a black 5.5G iPod (Video), as large as fits in `area`. `display(painter, screen rect)`
    draws its screen instead of the name and line."""
    """Draw a black 5.5G iPod (Video), as large as fits in `area`."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    h = min(area.height(), area.width() * 1.66)
    w = h / 1.66
    body = QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)
    # Shadow, chrome edge, black front.
    painter.setPen(Qt.PenStyle.NoPen)
    if shadow:
        glow = QRadialGradient(body.center() + QPointF(0, h * 0.48), w * 0.7)
        glow.setColorAt(0, QColor(0, 0, 0, 70))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(body.left() - w * 0.08, body.bottom() - h * 0.05, w * 1.16, h * 0.1))
    painter.setBrush(_gradient(body, (0, "#f4f4f4"), (0.5, "#9d9d9d"), (1, "#e6e6e6"), horizontal=True))
    painter.drawRoundedRect(body, w * 0.09, w * 0.09)
    edge = max(1.6, w * 0.012)
    front = body.adjusted(edge, edge, -edge, -edge)
    painter.setBrush(_gradient(front, (0, "#3c3c3c"), (0.08, "#1b1b1b"), (1, "#050505")))
    painter.drawRoundedRect(front, w * 0.085, w * 0.085)
    # Screen.
    screen = QRectF(front.left() + w * 0.09, front.top() + w * 0.11, w * 0.80, w * 0.62)
    painter.setPen(QPen(QColor("#000000"), max(1.2, w * 0.008)))
    painter.setBrush(_gradient(screen, (0, "#f6fbff"), (0.18, "#d9ecfb"), (1, "#a9cbe8")))
    painter.drawRect(screen)
    if display is not None:
        display(painter, screen)
    else:
        title_bar = QRectF(screen.left(), screen.top(), screen.width(), screen.height() * 0.16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_gradient(title_bar, (0, "#fdfdfd"), (1, "#c4c9cf")))
        painter.drawRect(title_bar)
        font = QFont(base_font)
        font.setPixelSize(max(8, int(screen.height() * 0.1)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#111111"))
        if name:
            painter.drawText(title_bar, Qt.AlignmentFlag.AlignCenter, name)
        font.setPixelSize(max(8, int(screen.height() * 0.1)))
        font.setBold(False)
        painter.setFont(font)
        if line:
            body_text = screen.adjusted(6, title_bar.height() + 6, -6, -6)
            painter.drawText(body_text, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, line)
    # Glass: a soft highlight over the upper half of the front.
    upper = QRectF(front.left(), front.top(), front.width(), front.height() * 0.5)
    glass = QPainterPath()
    glass.addRoundedRect(upper, w * 0.085, w * 0.085)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_gradient(upper, (0, QColor(255, 255, 255, 46)), (0.6, QColor(255, 255, 255, 10)),
                               (1, QColor(255, 255, 255, 0))))
    painter.drawPath(glass)
    # Click wheel.
    radius = w * 0.31
    center = QPointF(front.center().x(), front.bottom() - w * 0.06 - radius)
    wheel = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
    painter.setPen(QPen(QColor("#3b3b3b"), 1))
    painter.setBrush(_gradient(wheel, (0, "#2a2a2a"), (1, "#151515")))
    painter.drawEllipse(wheel)
    inner = radius * 0.38
    painter.setBrush(_gradient(wheel, (0, "#1d1d1d"), (1, "#2c2c2c")))
    painter.drawEllipse(center, inner, inner)
    if radius >= 20:
        label_font = QFont(base_font)
        label_font.setPixelSize(max(7, int(radius * 0.16)))
        label_font.setBold(True)
        painter.setFont(label_font)
        painter.setPen(QColor("#c9c9c9"))
        painter.drawText(QRectF(center.x() - radius, center.y() - radius * 0.92, 2 * radius, radius * 0.3),
                         Qt.AlignmentFlag.AlignCenter, "MENU")
    painter.setBrush(QColor("#c9c9c9"))
    painter.setPen(Qt.PenStyle.NoPen)
    _transport(painter, center, radius)
    painter.restore()


def _transport(painter: QPainter, c: QPointF, r: float) -> None:
    t = r * 0.07

    def triangle(x: float, y: float, direction: int) -> None:
        path = QPainterPath()
        path.moveTo(x, y - t)
        path.lineTo(x + direction * t * 1.3, y)
        path.lineTo(x, y + t)
        path.closeSubpath()
        painter.drawPath(path)

    for direction, x in ((1, c.x() + r * 0.62), (-1, c.x() - r * 0.62)):
        triangle(x - direction * t * 0.9, c.y(), direction)
        triangle(x + direction * t * 0.4, c.y(), direction)
        painter.drawRect(QRectF(x + direction * t * 1.7 - (t * 0.35 if direction > 0 else 0), c.y() - t, t * 0.35, 2 * t))
    y = c.y() + r * 0.7
    triangle(c.x() - t * 1.6, y, 1)
    painter.drawRect(QRectF(c.x() + t * 0.3, y - t, t * 0.4, 2 * t))
    painter.drawRect(QRectF(c.x() + t * 1.0, y - t, t * 0.4, 2 * t))


# --- capacity bar ----------------------------------------------------------------------------------


class CapacityBar(QWidget):
    """iTunes-style storage bar: coloured glossy segments with a legend underneath."""

    SEGMENTS = (("Audio", "#4f97e8", "#2b6ec9"), ("Video", "#a77de0", "#7a4fc0"), ("Podcasts", "#7cc45a", "#4e9a2e"),
                ("Audiobooks", "#f08a4b", "#cf5f1c"), ("Other", "#f3c94f", "#d79f12"), ("Free", "#f7f7f7", "#cfcfcf"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.values: list[tuple[str, int]] = []
        self.setMinimumHeight(48)

    def set_values(self, audio: int, other: int, free: int, video: int = 0, podcasts: int = 0,
                   audiobooks: int = 0) -> None:
        values = [("Audio", audio), ("Video", video), ("Podcasts", podcasts), ("Audiobooks", audiobooks),
                  ("Other", other), ("Free", free)]
        # Empty kinds are left out of the bar and its legend (Audio, Other and Free always show).
        self.values = [(name, value) for name, value in values if value or name in ("Audio", "Other", "Free")]
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(10, 4, self.width() - 20, 20)
        total = sum(v for _, v in self.values) or 1
        path = QPainterPath()
        path.addRoundedRect(bar, 10, 10)
        painter.setClipPath(path)
        x = bar.left()
        colors = {name: (top, bottom) for name, top, bottom in self.SEGMENTS}
        colors["Free"] = (theme.c("free_top"), theme.c("free_bottom"))
        for name, value in self.values:
            width = bar.width() * value / total
            segment = QRectF(x, bar.top(), width, bar.height())
            top, bottom = colors[name]
            painter.fillRect(segment, _gradient(segment, (0, QColor(top).lighter(135).name()), (0.5, top), (0.51, bottom), (1, top)))
            x += width
        painter.fillRect(QRectF(bar.left(), bar.top(), bar.width(), bar.height() / 2),
                         QColor(255, 255, 255, 60))
        painter.setClipping(False)
        painter.setPen(QPen(QColor("#6d6d6d"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(bar, 10, 10)
        # Legend.
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        x = bar.left() + 4
        for name, value in self.values:
            top, bottom = colors[name]
            dot = QRectF(x, bar.bottom() + 8, 10, 10)
            painter.setPen(QPen(QColor(bottom), 1))
            painter.setBrush(_gradient(dot, (0, top), (1, bottom)))
            painter.drawEllipse(dot)
            text = f"{name}  {value / 1000**3:.1f} GB"
            painter.setPen(QColor(theme.c("text")))
            painter.drawText(QPointF(x + 15, bar.bottom() + 17), text)
            x += QFontMetrics(font).horizontalAdvance(text) + 34


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):  # noqa: N802
        self.clicked.emit()



class SourceList(QTreeWidget):
    """The source list. Songs dragged onto an item emit `songs_dropped(item key, song ids)`;
    `accepts_drop(key)` decides which items take them (set by the window)."""

    songs_dropped = Signal(object, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.accepts_drop = lambda key: False

    def _target(self, event):
        from synclassic.gui.ipod_views import song_ids_from

        item = self.itemAt(event.position().toPoint())
        if item is None or not song_ids_from(event.mimeData()):
            return None, []
        key = item.data(0, Qt.ItemDataRole.UserRole)
        return (item, song_ids_from(event.mimeData())) if self.accepts_drop(key) else (None, [])

    def dragEnterEvent(self, event):  # noqa: N802
        from synclassic.gui.ipod_views import SONG_MIME

        if event.mimeData().hasFormat(SONG_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802
        item, _ = self._target(event)
        if item is not None:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):  # noqa: N802
        item, ids = self._target(event)
        if item is None:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.songs_dropped.emit(item.data(0, Qt.ItemDataRole.UserRole), ids)


def speaker_pixmap(waves: int, size: int = 14, color: str = "#3a3a3a"):
    """A small speaker icon (0-2 sound waves), for the volume slider and the playing song."""
    from PySide6.QtGui import QPixmap

    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    s = float(size)
    body = QPainterPath()
    body.moveTo(s * 0.08, s * 0.38)
    body.lineTo(s * 0.28, s * 0.38)
    body.lineTo(s * 0.52, s * 0.16)
    body.lineTo(s * 0.52, s * 0.84)
    body.lineTo(s * 0.28, s * 0.62)
    body.lineTo(s * 0.08, s * 0.62)
    body.closeSubpath()
    painter.drawPath(body)
    painter.setPen(QPen(QColor(color), max(1.0, s * 0.08), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for n in range(waves):
        r = s * (0.18 + 0.16 * n)
        painter.drawArc(QRectF(s * 0.52 - r, s * 0.5 - r, 2 * r, 2 * r), -45 * 16, 90 * 16)
    painter.end()
    return pixmap


def speaker_icon():
    """Speaker for the playing song: blue normally, white on a selected row."""
    from PySide6.QtGui import QIcon

    icon = QIcon()
    icon.addPixmap(speaker_pixmap(2, 12, "#2f66c1"), QIcon.Mode.Normal)
    icon.addPixmap(speaker_pixmap(2, 12, "#ffffff"), QIcon.Mode.Selected)
    return icon


def control_pixmap(kind: str, size: int = 16, color: str = "#2e2e2e"):
    """Drawn shuffle / repeat / repeat-one icons for the bottom bar."""
    from PySide6.QtGui import QPixmap

    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    pen = QPen(QColor(color), s * 0.11, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

    def head(x: float, y: float, direction: int) -> None:
        path = QPainterPath()
        path.moveTo(x, y - s * 0.16)
        path.lineTo(x + direction * s * 0.2, y)
        path.lineTo(x, y + s * 0.16)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPath(path)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    painter.setPen(pen)
    if kind == "lyrics":  # a speech bubble with lines of words
        bubble = QPainterPath()
        bubble.addRoundedRect(QRectF(s * 0.1, s * 0.16, s * 0.8, s * 0.56), s * 0.14, s * 0.14)
        bubble.moveTo(s * 0.3, s * 0.72)
        bubble.lineTo(s * 0.24, s * 0.9)
        bubble.lineTo(s * 0.46, s * 0.72)
        painter.drawPath(bubble)
        thin = QPen(pen)
        thin.setWidthF(s * 0.08)
        painter.setPen(thin)
        for y, right in ((0.34, 0.72), (0.5, 0.6)):
            painter.drawLine(QPointF(s * 0.26, s * y), QPointF(s * right, s * y))
        painter.end()
        return pixmap
    if kind == "visualizer":  # bars
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        for index, height in enumerate((0.35, 0.7, 0.5, 0.85, 0.45)):
            painter.drawRoundedRect(QRectF(s * (0.1 + index * 0.17), s * (0.9 - height), s * 0.12, s * height),
                                    s * 0.03, s * 0.03)
        painter.end()
        return pixmap
    if kind == "shuffle":
        for y0, y1 in ((0.3, 0.7), (0.7, 0.3)):
            path = QPainterPath()
            path.moveTo(s * 0.1, s * y0)
            path.cubicTo(s * 0.45, s * y0, s * 0.45, s * y1, s * 0.72, s * y1)
            painter.drawPath(path)
            head(s * 0.72, s * y1, 1)
    else:
        rect = QRectF(s * 0.14, s * 0.28, s * 0.72, s * 0.44)
        path = QPainterPath()
        path.moveTo(rect.left() + s * 0.2, rect.top())
        path.lineTo(rect.right() - s * 0.12, rect.top())
        path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + s * 0.12)
        path.lineTo(rect.right(), rect.bottom() - s * 0.12)
        path.quadTo(rect.right(), rect.bottom(), rect.right() - s * 0.12, rect.bottom())
        path.lineTo(rect.left() + s * 0.12, rect.bottom())
        path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - s * 0.12)
        path.lineTo(rect.left(), rect.top() + s * 0.12)
        painter.drawPath(path)
        head(rect.left() + s * 0.2, rect.top(), 1)
        if kind == "repeat_one":
            font = QFont()
            font.setPixelSize(int(s * 0.42))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "1")
    painter.end()
    return pixmap
