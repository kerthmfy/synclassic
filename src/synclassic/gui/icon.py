"""The Synclassic app icon: a black iPod (Video) with a glossy Aqua-blue sync badge (the logo),
on the white macOS rounded tile, as designed in Icon Composer (resources/Synclassic.icon).

The .icon file is what macOS 26 shows (with its glass effects) when install-app can compile it;
this drawing is the same design for the window icon and older systems."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

from synclassic.gui.widgets import paint_ipod


def render_icon(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_icon(painter, size)
    painter.end()
    return image


def save_svg(path, size: int = 512, tile: bool = False) -> None:
    """The logo (or, with `tile`, the whole app icon) as a vector SVG."""
    from PySide6.QtCore import QRect, QSize
    from PySide6.QtSvg import QSvgGenerator

    generator = QSvgGenerator()
    generator.setFileName(str(path))
    generator.setSize(QSize(size, size))
    generator.setViewBox(QRect(0, 0, size, size))
    generator.setTitle("Synclassic")
    generator.setDescription("The Synclassic app icon")
    painter = QPainter(generator)
    (paint_icon if tile else paint_logo)(painter, size)
    painter.end()


# The macOS icon grid (on a 1024 canvas): an 824 tile, and the logo at Icon Composer's 172 %
# of its 512 artboard.
TILE, LOGO = 824 / 1024, 880 / 1024


def tile_path(s: float) -> QPainterPath:
    side = s * TILE
    rect = QRectF((s - side) / 2, (s - side) / 2 - s * 0.006, side, side)
    path = QPainterPath()
    path.addRoundedRect(rect, side * 0.225, side * 0.225)
    return path


def paint_icon(painter: QPainter, size: int) -> None:
    """The whole app icon: shadow, white tile, the logo inside it, a soft top light."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    path = tile_path(s)
    rect = path.boundingRect()
    for spread, alpha in ((0.03, 7), (0.018, 11), (0.008, 16)):  # soft, neutral shadow
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, alpha))
        shadow = QPainterPath()
        grown = rect.adjusted(-s * spread * 0.4, s * spread * 0.2, s * spread * 0.4, s * spread * 1.4)
        shadow.addRoundedRect(grown, grown.width() * 0.23, grown.width() * 0.23)
        painter.drawPath(shadow)
    fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    fill.setColorAt(0, QColor("#ffffff"))
    fill.setColorAt(1, QColor("#f5f5f5"))
    painter.setBrush(fill)
    painter.drawPath(path)
    painter.save()
    painter.setClipPath(path)
    offset = (s - s * LOGO) / 2
    painter.translate(offset, offset)
    paint_logo(painter, int(round(s * LOGO)))
    painter.restore()
    light = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    light.setColorAt(0, QColor(255, 255, 255, 230))
    light.setColorAt(0.5, QColor(255, 255, 255, 0))
    light.setColorAt(1, QColor(0, 0, 0, 30))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(light, max(1.0, s * 0.004)))
    painter.drawPath(path)


def paint_logo(painter: QPainter, size: int) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    s = float(size)

    # The iPod, tilted a little, with its own soft shadow.
    painter.save()
    painter.translate(s * 0.42, s * 0.47)
    painter.rotate(-9)
    paint_ipod(painter, QRectF(-s * 0.27, -s * 0.44, s * 0.54, s * 0.88), "", "", QFont(), shadow=size >= 64)
    painter.restore()

    # The sync badge: drop shadow, white ring, blue gel, gloss, white arrows.
    c = QPointF(s * 0.71, s * 0.72)
    r = s * 0.215
    shadow = QRadialGradient(c + QPointF(0, s * 0.025), r * 1.25)
    shadow.setColorAt(0.75, QColor(0, 0, 0, 90))
    shadow.setColorAt(1, QColor(0, 0, 0, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(shadow)
    painter.drawEllipse(c + QPointF(0, s * 0.02), r * 1.25, r * 1.25)
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(c, r * 1.08, r * 1.08)
    gel = QLinearGradient(c - QPointF(0, r), c + QPointF(0, r))
    gel.setColorAt(0, QColor("#7cc0ff"))
    gel.setColorAt(0.5, QColor("#2f82e6"))
    gel.setColorAt(1, QColor("#1c5fc0"))
    painter.setPen(QPen(QColor("#174f9e"), max(1.0, s * 0.006)))
    painter.setBrush(gel)
    painter.drawEllipse(c, r, r)
    glow = QRadialGradient(c + QPointF(0, r * 0.75), r * 0.9)
    glow.setColorAt(0, QColor(150, 220, 255, 170))
    glow.setColorAt(1, QColor(150, 220, 255, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(c, r, r)
    gloss = QRectF(c.x() - r * 0.72, c.y() - r * 0.94, r * 1.44, r * 0.9)
    shine = QLinearGradient(gloss.topLeft(), gloss.bottomLeft())
    shine.setColorAt(0, QColor(255, 255, 255, 230))
    shine.setColorAt(1, QColor(255, 255, 255, 25))
    painter.setBrush(shine)
    painter.drawEllipse(gloss)

    arrow = r * 0.5
    pen = QPen(QColor("#ffffff"), max(1.5, r * 0.16), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    arc = QRectF(c.x() - arrow, c.y() - arrow, 2 * arrow, 2 * arrow)
    painter.drawArc(arc, 25 * 16, 130 * 16)
    painter.drawArc(arc, 205 * 16, 130 * 16)
    for degrees in (25, 205):
        angle = math.radians(degrees)
        tip = QPointF(c.x() + math.cos(angle) * arrow, c.y() - math.sin(angle) * arrow)
        tangent = angle + math.pi / 2
        back = QPointF(math.cos(tangent), -math.sin(tangent))
        side = QPointF(math.cos(angle), -math.sin(angle))
        head = arrow * 0.62
        path = QPainterPath()
        path.moveTo(tip - back * head * 0.55)
        path.lineTo(tip + back * head * 0.55 + side * head * 0.6)
        path.lineTo(tip + back * head * 0.55 - side * head * 0.6)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawPath(path)


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icon.addPixmap(QPixmap.fromImage(render_icon(size)))
    return icon
