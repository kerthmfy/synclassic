"""The look: Mac OS X Tiger/Leopard-era Aqua, as in iTunes 7 (2006, the year of the 5.5G iPod).

Always light, like the original (it ignores the system dark mode). Lucida Grande was the Mac
system font then and still ships with macOS; other systems fall back to similar fonts.
"""

from __future__ import annotations

import pathlib

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

RESOURCES = pathlib.Path(__file__).parent / "resources"


def _res(name: str) -> str:
    return (RESOURCES / name).as_posix()


FONT_FAMILIES = ["Lucida Grande", "Lucida Sans Unicode", "Helvetica Neue", "Helvetica", "DejaVu Sans", "Arial"]

# Palette (sampled from the period's interface conventions).
WINDOW = "#e8e8e8"
TEXT = "#1a1a1a"
SOURCE_LIST = "#d6dde5"
SOURCE_HEADER = "#6b7889"
SELECT_TOP, SELECT_BOTTOM, SELECT_BORDER = "#6c9de0", "#2f66c1", "#2a58a4"
SELECT_INACTIVE_TOP, SELECT_INACTIVE_BOTTOM = "#adbacb", "#8d9cb3"
ROW_ALT = "#edf3fe"
GRID = "#d9d9d9"
TOOLBAR_TOP, TOOLBAR_BOTTOM, TOOLBAR_LINE = "#e3e3e3", "#b4b4b4", "#666666"
BOTTOMBAR_TOP, BOTTOMBAR_BOTTOM = "#d5d5d5", "#a9a9a9"
LCD_TOP, LCD_BOTTOM, LCD_BORDER, LCD_TEXT = "#f6f8ef", "#dde2cf", "#767676", "#2b2b2b"
AQUA_TOP, AQUA_MID, AQUA_BOTTOM, AQUA_BORDER = "#d2e8ff", "#6eb0f4", "#3a8ae6", "#2c5c9c"
STATUS_COLORS = {
    "ready": ("#5cb85c", "#3d8b3d"),
    "your pick": ("#5cb85c", "#3d8b3d"),
    "mac file": ("#6fa8ea", "#3b78c9"),
    "check": ("#f5b544", "#d48a0b"),
    "waiting": ("#b5b5b5", "#8c8c8c"),
    "not found": ("#ee6a6a", "#c63a3a"),
    "failed": ("#ee6a6a", "#c63a3a"),
    "file missing": ("#ee6a6a", "#c63a3a"),
}


def font_family() -> str:
    available = set(QFontDatabase.families())
    return next((family for family in FONT_FAMILIES if family in available), QFont().defaultFamily())


STYLESHEET = f"""
* {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {WINDOW}; }}
QToolTip {{ background: #ffffca; color: #000; border: 1px solid #8a8a8a; padding: 2px 4px; }}

/* ---- push buttons: white "gel" pills; the default button is Aqua blue ---- */
QPushButton {{
    min-height: 18px; padding: 2px 14px; border-radius: 10px;
    border: 1px solid #8c8c8c;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #ffffff, stop:0.49 #f1f1f1, stop:0.5 #e2e2e2, stop:1 #f4f4f4);
}}
QPushButton:hover {{ border-color: #6f6f6f; }}
QPushButton:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #c9c9c9, stop:0.49 #d8d8d8, stop:0.5 #cbcbcb, stop:1 #e3e3e3);
}}
QPushButton:default, QPushButton[aqua="true"] {{
    color: #000; border: 1px solid {AQUA_BORDER};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {AQUA_TOP}, stop:0.49 #8fc4f7, stop:0.5 {AQUA_MID}, stop:1 #a9d6ff);
}}
QPushButton:default:pressed, QPushButton[aqua="true"]:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #9ccaf5, stop:0.49 #5d9be0, stop:0.5 {AQUA_BOTTOM}, stop:1 #7fbdf7);
}}
QPushButton:disabled {{ color: #9a9a9a; border-color: #b8b8b8; background: #efefef; }}

/* ---- text fields ---- */
QLineEdit, QPlainTextEdit, QDoubleSpinBox, QSpinBox {{
    background: #ffffff; border: 1px solid #8f8f8f; border-top-color: #6a6a6a;
    padding: 2px 4px; selection-background-color: #b5d5ff; selection-color: #000;
}}
QLineEdit[search="true"] {{ border-radius: 10px; padding-left: 20px; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 2px solid #7eaef0; padding: 1px 3px; }}
QLineEdit[search="true"]:focus {{ padding-left: 19px; }}

/* ---- pop-up menus (combo boxes) ---- */
QComboBox {{
    min-height: 18px; padding: 1px 22px 1px 10px; border-radius: 5px; border: 1px solid #8c8c8c;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:0.5 #ececec, stop:1 #f7f7f7);
}}
QComboBox::drop-down {{
    width: 18px; border: none; border-left: 1px solid {AQUA_BORDER};
    border-top-right-radius: 5px; border-bottom-right-radius: 5px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {AQUA_TOP}, stop:0.5 {AQUA_MID}, stop:1 #a9d6ff);
}}
QComboBox::down-arrow {{ image: url({_res("updown.svg")}); width: 9px; height: 13px; }}
QComboBox QAbstractItemView {{ background: #ffffff; selection-background-color: {SELECT_BOTTOM}; selection-color: #fff; }}

/* ---- menus ---- */
QMenu {{ background: rgba(255, 255, 255, 245); border: 1px solid #a5a5a5; padding: 4px 0; }}
QMenu::item {{ padding: 3px 22px; }}
QMenu::item:selected {{
    color: #fff; background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {SELECT_TOP}, stop:1 {SELECT_BOTTOM});
}}
QMenu::item:disabled {{ color: #a3a3a3; }}
QMenu::item:disabled:selected {{ color: #a3a3a3; background: transparent; }}
QMenu::separator {{ height: 1px; background: #d0d0d0; margin: 4px 0; }}

/* ---- lists and tables ---- */
QTreeView, QListView, QTableView {{
    background: #ffffff; alternate-background-color: {ROW_ALT}; border: 1px solid #9a9a9a;
    selection-background-color: {SELECT_BOTTOM}; selection-color: #ffffff; outline: 0;
}}
QTreeView::item {{ padding: 1px 2px; border-right: 1px solid {GRID}; }}
QTreeView::item:selected {{
    color: #ffffff; border-right: 1px solid #5b84c8;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {SELECT_TOP}, stop:1 {SELECT_BOTTOM});
}}
QTreeView::item:selected:!active {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {SELECT_INACTIVE_TOP}, stop:1 {SELECT_INACTIVE_BOTTOM});
}}
QHeaderView::section {{
    padding: 2px 6px; border: none; border-right: 1px solid #b9b9b9; border-bottom: 1px solid #8d8d8d;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:0.5 #f1f1f1, stop:0.51 #e4e4e4, stop:1 #eeeeee);
    font-weight: normal;
}}
QHeaderView::section:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #c5daf5, stop:0.5 #9cc0ee, stop:0.51 #86b1ea, stop:1 #b5d2f6);
}}

/* ---- Aqua scroll bars: blue gel thumb in a pinstriped trough ---- */
QScrollBar:vertical {{ width: 15px; margin: 0; border-left: 1px solid #b3b3b3;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d8d8d8, stop:0.5 #f2f2f2, stop:1 #e0e0e0); }}
QScrollBar:horizontal {{ height: 15px; margin: 0; border-top: 1px solid #b3b3b3;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d8d8d8, stop:0.5 #f2f2f2, stop:1 #e0e0e0); }}
QScrollBar::handle:vertical {{
    min-height: 28px; margin: 1px 1px 1px 2px; border-radius: 6px; border: 1px solid {AQUA_BORDER};
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #a9d2fb, stop:0.35 #5aa0ec, stop:0.65 {AQUA_BOTTOM}, stop:1 #8ec4f8);
}}
QScrollBar::handle:horizontal {{
    min-width: 28px; margin: 2px 1px 1px 1px; border-radius: 6px; border: 1px solid {AQUA_BORDER};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #a9d2fb, stop:0.35 #5aa0ec, stop:0.65 {AQUA_BOTTOM}, stop:1 #8ec4f8);
}}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- progress bars: Aqua gel ---- */
QProgressBar {{ border: 1px solid #7d7d7d; border-radius: 5px; background: #f3f3f3; text-align: center; height: 10px; }}
QProgressBar::chunk {{ border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #cfe6ff, stop:0.5 #5fa6f0, stop:0.51 {AQUA_BOTTOM}, stop:1 #9fd0ff); }}

/* ---- tabs: Aqua segmented control ---- */
QTabWidget::pane {{ border: 1px solid #a8a8a8; border-radius: 4px; top: -11px; padding-top: 14px; background: #ececec; }}
QTabWidget::tab-bar {{ alignment: center; }}
QTabBar::tab {{
    padding: 3px 14px; border: 1px solid #8c8c8c; border-left: none; min-height: 16px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:0.5 #ececec, stop:1 #f7f7f7);
}}
QTabBar::tab:first {{ border-left: 1px solid #8c8c8c; border-top-left-radius: 10px; border-bottom-left-radius: 10px; }}
QTabBar::tab:last {{ border-top-right-radius: 10px; border-bottom-right-radius: 10px; }}
QTabBar::tab:selected {{ border-color: {AQUA_BORDER};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {AQUA_TOP}, stop:0.49 #8fc4f7, stop:0.5 {AQUA_MID}, stop:1 #a9d6ff); }}

/* ---- group boxes: inset panels ---- */
QGroupBox {{ border: 1px solid #b5b5b5; border-radius: 5px; margin-top: 18px; padding: 10px 8px 8px 8px;
    background: rgba(0, 0, 0, 10); }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; top: 2px; font-weight: bold; }}

/* ---- check boxes ---- */
QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid #7f7f7f; border-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #dedede); }}
QCheckBox::indicator:checked {{ border-color: {AQUA_BORDER}; image: url({_res("check.svg")});
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {AQUA_TOP}, stop:0.5 {AQUA_MID}, stop:1 #a9d6ff); }}

/* ---- number boxes: Aqua little arrows ---- */
QAbstractSpinBox {{ padding-right: 18px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border; width: 15px; border-left: 1px solid #8f8f8f;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:0.5 #ececec, stop:1 #f7f7f7);
}}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-bottom: 1px solid #b5b5b5; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
QAbstractSpinBox::up-arrow {{ image: url({_res("up.svg")}); width: 8px; height: 5px; }}
QAbstractSpinBox::down-arrow {{ image: url({_res("down.svg")}); width: 8px; height: 5px; }}

QSplitter::handle {{ background: #a3a3a3; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
"""


def apply(app: QApplication) -> None:
    """Apply the Aqua look to the whole app."""
    app.setStyle("Fusion")  # a neutral base so every control can be restyled the same on any OS
    # Ask the OS for light window frames too (the title bar stays dark in macOS dark mode otherwise).
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt 6.8+
        from PySide6.QtCore import Qt

        hints.setColorScheme(Qt.ColorScheme.Light)
    font = QFont(font_family())
    font.setPixelSize(12)
    app.setFont(font)
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, WINDOW), (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, "#ffffff"), (QPalette.ColorRole.AlternateBase, ROW_ALT),
        (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.Button, "#ececec"),
        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, SELECT_BOTTOM),
        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, "#ffffca"),
        (QPalette.ColorRole.ToolTipText, "#000000"), (QPalette.ColorRole.PlaceholderText, "#8a8a8a"),
        (QPalette.ColorRole.Link, "#1f5fbf"),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)
