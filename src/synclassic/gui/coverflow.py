"""Cover Flow: album covers in a row on black, the chosen one facing you and the others turned
away on either side, with mirror reflections, sliding smoothly when you click, scroll, drag or use
the arrow keys (as in iTunes 7, 2007)."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QHBoxLayout, QScrollBar, QVBoxLayout, QWidget

ANGLE = 62  # how far side covers are turned
REFLECTION = 0.3  # share of the cover height mirrored below it
SIDE_SCALE = 0.72  # side covers sit further back


def _reflected(image: QImage, size: int) -> QPixmap:
    """The cover at `size`, with its fading reflection underneath (cached per cover)."""
    cover = image.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    height = int(size * REFLECTION)
    out = QImage(size, size + height, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.drawImage(0, 0, cover)
    mirror = cover.flipped(Qt.Orientation.Vertical).copy(0, 0, size, height)
    painter.drawImage(0, size, mirror)
    fade = QLinearGradient(0, size, 0, size + height)
    fade.setColorAt(0, QColor(0, 0, 0, 150))
    fade.setColorAt(1, QColor(0, 0, 0, 255))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
    painter.fillRect(QRectF(0, size, size, height), fade)
    painter.end()
    return QPixmap.fromImage(out)


class CoverFlowView(QWidget):
    current_changed = Signal(int)  # index into the items
    activated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(220)
        self.items: list[tuple[str, str]] = []  # (title, artist)
        self.images: dict[int, QImage] = {}
        self.visible: list[int] = []  # indexes shown (search)
        self.position = 0.0  # float index into `visible`, animated
        self.target = 0
        self._cache: dict[tuple[int, int], QPixmap] = {}
        self._placeholder: QImage | None = None
        self._drag_from: tuple[float, float] | None = None
        self.animation = QVariantAnimation(self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._moved)

    # --- data -----------------------------------------------------------------------------------

    def set_items(self, items: list[tuple[str, str]]) -> None:
        self.items = items
        self.images.clear()
        self._cache.clear()
        self.visible = list(range(len(items)))
        self.position, self.target = 0.0, 0
        self.update()

    def set_image(self, index: int, image: QImage) -> None:
        self.images[index] = image
        for key in [k for k in self._cache if k[0] == index]:
            del self._cache[key]
        self.update()

    def filter(self, text: str) -> None:
        words = text.casefold().split()
        self.visible = [i for i, (title, artist) in enumerate(self.items)
                        if not words or all(w in f"{title} {artist}".casefold() for w in words)]
        self.position, self.target = 0.0, 0
        self.update()
        if self.visible:
            self.current_changed.emit(self.visible[0])

    def current(self) -> int | None:
        return self.visible[self.target] if self.visible else None

    def set_current(self, index: int, animate: bool = True) -> None:
        if index in self.visible:
            self.go_to(self.visible.index(index), animate)

    # --- moving ---------------------------------------------------------------------------------

    def go_to(self, slot: int, animate: bool = True) -> None:
        if not self.visible:
            return
        slot = max(0, min(len(self.visible) - 1, slot))
        changed = slot != self.target
        self.target = slot
        self.animation.stop()
        if animate:
            self.animation.setDuration(int(min(900, 220 + 90 * abs(slot - self.position))))
            self.animation.setStartValue(float(self.position))
            self.animation.setEndValue(float(slot))
            self.animation.start()
        else:
            self.position = float(slot)
            self.update()
        if changed:
            self.current_changed.emit(self.visible[slot])

    def _moved(self, value) -> None:
        self.position = float(value)
        self.update()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Left:
            self.go_to(self.target - 1)
        elif event.key() == Qt.Key.Key_Right:
            self.go_to(self.target + 1)
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.current() is not None:
            self.activated.emit(self.current())
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):  # noqa: N802
        delta = event.angleDelta()
        step = delta.x() or -delta.y()
        if step:
            self.go_to(self.target + (1 if step > 0 else -1))

    def mousePressEvent(self, event):  # noqa: N802
        self.setFocus()
        self._drag_from = (event.position().x(), self.position)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_from is None:
            return
        start_x, start_position = self._drag_from
        moved = (start_x - event.position().x()) / max(1.0, self._spacing())
        self.animation.stop()
        self.position = max(0.0, min(len(self.visible) - 1.0, start_position + moved))
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._drag_from is None:
            return
        start_x, _ = self._drag_from
        self._drag_from = None
        if abs(event.position().x() - start_x) < 4:  # a click: go to the cover under the pointer
            slot = self._slot_at(event.position())
            if slot is not None:
                if slot == self.target and self.current() is not None:
                    self.activated.emit(self.current())
                self.go_to(slot)
            return
        self.go_to(round(self.position))

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if self.current() is not None:
            self.activated.emit(self.current())

    # --- drawing --------------------------------------------------------------------------------

    def _size(self) -> int:
        # The cover, its reflection and two lines of text fit the height.
        return int(max(60, min(self.height() * 0.6, self.width() * 0.3)))

    def _spacing(self) -> float:
        return self._size() * 0.2

    def _geometry(self, offset: float) -> tuple[float, float, float]:
        """(x of the cover's center, turn in degrees, scale) for a cover `offset` from the middle."""
        size = self._size()
        center = self.width() / 2
        near = size * 0.78  # distance from the middle to the first side cover
        if abs(offset) <= 1:
            x = center + offset * near
            angle = -offset * ANGLE
            scale = 1 - (1 - SIDE_SCALE) * abs(offset)
        else:
            sign = 1 if offset > 0 else -1
            x = center + sign * (near + (abs(offset) - 1) * self._spacing())
            angle = -sign * ANGLE
            scale = SIDE_SCALE
        return x, angle, scale

    def _slot_at(self, point: QPointF) -> int | None:
        best = None
        for slot in range(len(self.visible)):
            x, _, scale = self._geometry(slot - self.position)
            half = self._size() * scale * (0.5 if slot == self.target else 0.15)
            distance = abs(point.x() - x)
            if distance <= half and (best is None or distance < best[0]):
                best = (distance, slot)
        return best[1] if best else None

    def _pixmap(self, index: int, size: int) -> QPixmap:
        key = (index, size)
        if key not in self._cache:
            image = self.images.get(index)
            if image is None:
                if self._placeholder is None:
                    from synclassic.gui.ipod_views import placeholder_cover

                    self._placeholder = placeholder_cover(256).toImage()
                image = self._placeholder
            if len(self._cache) > 400:
                self._cache.clear()
            self._cache[key] = _reflected(image, size)
        return self._cache[key]

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        background = QLinearGradient(0, 0, 0, self.height())
        background.setColorAt(0, QColor("#000000"))
        background.setColorAt(0.75, QColor("#0b0b0b"))
        background.setColorAt(1, QColor("#1c1c1c"))
        painter.fillRect(self.rect(), background)
        if not self.visible:
            painter.setPen(QColor("#9a9a9a"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No albums")
            return
        size = self._size()
        top = self.height() * 0.05
        spread = int(self.width() / max(1.0, self._spacing())) + 3
        first, last = max(0, int(self.position) - spread), min(len(self.visible) - 1, int(self.position) + spread)
        order = sorted(range(first, last + 1), key=lambda slot: -abs(slot - self.position))  # far ones first
        pixel_size = int(size * self.devicePixelRatioF())
        for slot in order:
            offset = slot - self.position
            x, angle, scale = self._geometry(offset)
            pixmap = self._pixmap(self.visible[slot], pixel_size)
            pixmap.setDevicePixelRatio(self.devicePixelRatioF())
            painter.save()
            transform = QTransform()
            transform.translate(x, top + size)  # every cover stands on the same floor
            transform.rotate(angle, Qt.Axis.YAxis, 3.2 * size)
            transform.scale(scale, scale)
            painter.setTransform(transform)
            painter.drawPixmap(QPointF(-size / 2, -size), pixmap)
            # Covers further from the middle fade into the dark.
            shade = min(1.0, max(0.0, (abs(offset) - 0.5) / 6))
            if shade:
                painter.fillRect(QRectF(-size / 2, -size, size, size * (1 + REFLECTION)),
                                 QColor(0, 0, 0, int(200 * shade)))
            painter.restore()
        # Title and artist of the cover in the middle.
        index = self.visible[min(len(self.visible) - 1, max(0, round(self.position)))]
        title, artist = self.items[index]
        font = QFont(self.font())
        font.setPixelSize(max(11, int(size * 0.075)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        line = size * 0.11
        text_top = min(top + size * (1 + REFLECTION) + 4, self.height() - 2.1 * line)
        painter.drawText(QRectF(0, text_top, self.width(), line), Qt.AlignmentFlag.AlignCenter, title)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor("#a9a9a9"))
        painter.drawText(QRectF(0, text_top + line, self.width(), line), Qt.AlignmentFlag.AlignCenter, artist)


class CoverFlow(QWidget):
    """The view plus the slim scroll bar iTunes had under it."""

    current_changed = Signal(int)
    activated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.view = CoverFlowView()
        layout.addWidget(self.view, 1)
        self.bar = QScrollBar(Qt.Orientation.Horizontal)
        self.bar.setStyleSheet(
            "QScrollBar { background: #1c1c1c; height: 10px; border: none; }"
            "QScrollBar::handle { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #8a8a8a, stop:1 #4a4a4a);"
            " border-radius: 4px; min-width: 30px; }"
            "QScrollBar::add-line, QScrollBar::sub-line { width: 0; }"
            "QScrollBar::add-page, QScrollBar::sub-page { background: #2c2c2c; border-radius: 4px; }")
        self.setStyleSheet("background: #1c1c1c;")
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 2, 0, 6)
        row.addStretch(1)
        row.addWidget(self.bar, 3)
        row.addStretch(1)
        layout.addWidget(holder)
        self.bar.valueChanged.connect(lambda value: value != self.view.target and self.view.go_to(value))
        self.view.current_changed.connect(self._changed)
        self.view.activated.connect(self.activated)

    def _changed(self, index: int) -> None:
        self.bar.blockSignals(True)
        self.bar.setRange(0, max(0, len(self.view.visible) - 1))
        self.bar.setValue(self.view.target)
        self.bar.blockSignals(False)
        self.current_changed.emit(index)

    def set_items(self, items: list[tuple[str, str]]) -> None:
        self.view.set_items(items)
        self.bar.setRange(0, max(0, len(items) - 1))
        self.bar.setValue(0)

    def set_image(self, index: int, image: QImage) -> None:
        self.view.set_image(index, image)

    def filter(self, text: str) -> None:
        self.view.filter(text)
        self.bar.setRange(0, max(0, len(self.view.visible) - 1))

