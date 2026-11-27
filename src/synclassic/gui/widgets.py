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
        super().__init__(parent)
        self.top, self.bottom, self.line_at_bottom = top, bottom, line_at_bottom

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        rect = QRectF(self.rect())
        painter.fillRect(rect, _gradient(rect, (0, self.top), (1, self.bottom)))
        painter.setPen(QColor(255, 255, 255, 150))
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
        painter.setPen(QColor("#262626" if enabled else "#8f8f8f"))
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
            painter.setPen(QColor(theme.SELECT_BORDER if active else "#7d8ca3"))
            painter.drawLine(rect.topLeft(), rect.topRight())
        icon_rect = QRectF(rect.left() + 18, rect.center().y() - 8, 16, 16)
        draw_source_icon(painter, icon_rect, index.data(ICON_ROLE) or "playlist")
        text_rect = rect.adjusted(42, 0, -8, 0)
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
            painter.setBrush(QColor(255, 255, 255, 230) if selected else QColor("#8a9bb4"))
            painter.drawRoundedRect(pill, 7, 7)
            painter.setPen(QColor(theme.SELECT_BOTTOM) if selected else QColor("#ffffff"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, str(count))
            text_rect.setRight(pill.left() - 6)
        painter.setFont(font)
        if selected:
            painter.setPen(QColor(0, 0, 0, 90))
            painter.drawText(text_rect.translated(0, 1), Qt.AlignmentFlag.AlignVCenter,
                             QFontMetrics(font).elidedText(index.data(), Qt.TextElideMode.ElideRight, int(text_rect.width())))
        painter.setPen(QColor("#ffffff" if selected else "#000000"))
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


class IPodGraphic(QWidget):
    """A 5.5G iPod (Video) in black, drawn: glossy body, lit screen, click wheel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.name = "iPod"
        self.line = ""
        self.setMinimumSize(170, 280)

    def set_screen(self, name: str, line: str) -> None:
        self.name, self.line = name, line
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = min(self.height() - 20, (self.width() - 20) * 1.66)
        w = h / 1.66
        body = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        # Shadow, chrome edge, black front.
        shadow = QRadialGradient(body.center() + QPointF(0, h * 0.48), w * 0.7)
        shadow.setColorAt(0, QColor(0, 0, 0, 70))
        shadow.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(QRectF(body.left() - 10, body.bottom() - 14, w + 20, 28))
        painter.setBrush(_gradient(body, (0, "#f4f4f4"), (0.5, "#9d9d9d"), (1, "#e6e6e6"), horizontal=True))
        painter.drawRoundedRect(body, w * 0.09, w * 0.09)
        front = body.adjusted(1.6, 1.6, -1.6, -1.6)
        painter.setBrush(_gradient(front, (0, "#3c3c3c"), (0.08, "#1b1b1b"), (1, "#050505")))
        painter.drawRoundedRect(front, w * 0.085, w * 0.085)
        # Screen.
        screen = QRectF(front.left() + w * 0.09, front.top() + w * 0.11, w * 0.80, w * 0.62)
        painter.setPen(QPen(QColor("#000000"), 1.2))
        painter.setBrush(_gradient(screen, (0, "#f6fbff"), (0.18, "#d9ecfb"), (1, "#a9cbe8")))
        painter.drawRect(screen)
        title_bar = QRectF(screen.left(), screen.top(), screen.width(), screen.height() * 0.16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_gradient(title_bar, (0, "#fdfdfd"), (1, "#c4c9cf")))
        painter.drawRect(title_bar)
        font = QFont(self.font())
        font.setPixelSize(max(8, int(screen.height() * 0.1)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#111111"))
        painter.drawText(title_bar, Qt.AlignmentFlag.AlignCenter, self.name)
        font.setPixelSize(max(8, int(screen.height() * 0.1)))
        font.setBold(False)
        painter.setFont(font)
        body_text = screen.adjusted(6, title_bar.height() + 6, -6, -6)
        painter.drawText(body_text, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, self.line)
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
        label_font = QFont(self.font())
        label_font.setPixelSize(max(7, int(radius * 0.16)))
        label_font.setBold(True)
        painter.setFont(label_font)
        painter.setPen(QColor("#c9c9c9"))
        painter.drawText(QRectF(center.x() - radius, center.y() - radius * 0.92, 2 * radius, radius * 0.3),
                         Qt.AlignmentFlag.AlignCenter, "MENU")
        painter.setBrush(QColor("#c9c9c9"))
        painter.setPen(Qt.PenStyle.NoPen)
        self._transport(painter, center, radius)

    @staticmethod
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

    SEGMENTS = (("Audio", "#4f97e8", "#2b6ec9"), ("Other", "#f3c94f", "#d79f12"), ("Free", "#f7f7f7", "#cfcfcf"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.values: list[tuple[str, int]] = []
        self.setMinimumHeight(48)

    def set_values(self, audio: int, other: int, free: int) -> None:
        self.values = [("Audio", audio), ("Other", other), ("Free", free)]
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
            painter.setPen(QColor("#222222"))
            painter.drawText(QPointF(x + 15, bar.bottom() + 17), text)
            x += QFontMetrics(font).horizontalAdvance(text) + 34


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):  # noqa: N802
        self.clicked.emit()

