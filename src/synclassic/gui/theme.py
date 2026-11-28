"""The look: Mac OS X Tiger/Leopard-era Aqua, as in iTunes 7 (2006, the year of the 5.5G iPod).

Three appearances (Settings → Appearance, or `synclassic config appearance …`):
- "aqua": the original, light with blue gel controls (the default);
- "graphite": the same in Apple's grey Graphite colours;
- "dark": Midnight, a dark version for night-time;
- "auto": Midnight when macOS is in Dark Mode, Aqua otherwise.

Every colour comes from the palette below; widgets that draw themselves read it when they
paint, and the rest is one style sheet, so switching applies to the open window right away.
Lucida Grande was the Mac system font then and still ships with macOS; other systems fall back
to similar fonts.
"""

from __future__ import annotations

import pathlib

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

RESOURCES = pathlib.Path(__file__).parent / "resources"


def _res(name: str) -> str:
    return (RESOURCES / name).as_posix()


FONT_FAMILIES = ["Lucida Grande", "Lucida Sans Unicode", "Helvetica Neue", "Helvetica", "DejaVu Sans", "Arial"]
APPEARANCES = {"aqua": "Aqua", "graphite": "Graphite", "dark": "Midnight (dark)", "auto": "Automatic (follow macOS)"}

AQUA = {
    "window": "#e8e8e8", "text": "#1a1a1a", "dim": "#6b6b6b", "faint": "#8a8a8a",
    "base": "#ffffff", "base_alt": "#edf3fe", "grid": "#d9d9d9", "border": "#9a9a9a",
    "field_border": "#8f8f8f", "field_border_top": "#6a6a6a", "focus": "#7eaef0",
    "source_list": "#d6dde5", "source_header": "#6b7889", "source_text": "#000000", "source_badge": "#8a9bb4",
    "select_top": "#6c9de0", "select_bottom": "#2f66c1", "select_border": "#2a58a4",
    "select_inactive_top": "#adbacb", "select_inactive_bottom": "#8d9cb3", "select_inactive_border": "#7d8ca3",
    "toolbar_top": "#e3e3e3", "toolbar_bottom": "#b4b4b4", "toolbar_line": "#666666",
    "toolbar_shine": "rgba(255, 255, 255, 150)",
    "strip_top": "#f4f4f4", "strip_bottom": "#dcdcdc",
    "bottombar_top": "#d5d5d5", "bottombar_bottom": "#a9a9a9",
    "lcd_top": "#f6f8ef", "lcd_bottom": "#dde2cf", "lcd_border": "#767676", "lcd_text": "#2b2b2b",
    "accent_top": "#d2e8ff", "accent_upper": "#8fc4f7", "accent_mid": "#6eb0f4", "accent_bottom": "#3a8ae6",
    "accent_end": "#a9d6ff", "accent_border": "#2c5c9c", "accent_light": "#a9d2fb", "accent_mid2": "#5aa0ec",
    "accent_press_top": "#9ccaf5", "accent_press_mid": "#5d9be0", "accent_press_end": "#7fbdf7",
    "button_top": "#ffffff", "button_upper": "#f1f1f1", "button_lower": "#e2e2e2", "button_bottom": "#f4f4f4",
    "button_border": "#8c8c8c", "button_hover": "#6f6f6f",
    "button_press_top": "#c9c9c9", "button_press_upper": "#d8d8d8", "button_press_lower": "#cbcbcb",
    "button_press_bottom": "#e3e3e3",
    "disabled_text": "#9a9a9a", "disabled_border": "#b8b8b8", "disabled_bg": "#efefef",
    "menu_bg": "rgba(255, 255, 255, 245)", "menu_border": "#a5a5a5", "separator": "#d0d0d0",
    "header_top": "#ffffff", "header_upper": "#f1f1f1", "header_lower": "#e4e4e4", "header_bottom": "#eeeeee",
    "header_line": "#b9b9b9", "header_bottom_line": "#8d8d8d",
    "trough_edge": "#d8d8d8", "trough_mid": "#f2f2f2", "trough_end": "#e0e0e0", "trough_border": "#b3b3b3",
    "progress_bg": "#f3f3f3", "progress_border": "#7d7d7d",
    "tab_pane": "#ececec", "group_bg": "rgba(0, 0, 0, 10)", "group_border": "#b5b5b5",
    "check_top": "#ffffff", "check_bottom": "#dedede", "check_border": "#7f7f7f",
    "tooltip_bg": "#ffffca", "tooltip_text": "#000000", "tooltip_border": "#8a8a8a",
    "note_bg": "#fff6d5", "note_text": "#6b4b00", "note_border": "#e3cf8a",
    "page_top": "#fbfbfb", "page_bottom": "#e6e6e6",
    "panel": "#ffffff", "panel_border": "#b9b9b9", "panel_soft": "rgba(255, 255, 255, 150)", "panel_soft_border": "#c9c9c9",
    "art_title_top": "#eef1f5", "art_title_bottom": "#c9d1db", "art_title_text": "#3b3b3b", "art_title_border": "#9aa5b3",
    "good": "#2e8b2e", "bad": "#c63a3a", "link": "#1f5fbf", "placeholder": "#8a8a8a", "selection_text": "#b5d5ff",
    "free_top": "#f7f7f7", "free_bottom": "#cfcfcf", "splitter": "#a3a3a3",
    "slider_groove": "#f4f4f4", "slider_fill": "#8d8d8d", "slider_border": "#6e6e6e",
    "toggle_checked_text": "#1f5fbf", "toggle_checked_top": "#c9dcf5", "toggle_checked_bottom": "#a8c4ea",
    "grid_top": "#ffffff", "grid_bottom": "#eef1f5", "icon": "#2e2e2e",
}

GRAPHITE = {
    **AQUA,
    "base_alt": "#f0f2f5", "source_list": "#dcdfe3", "source_header": "#6f7783", "source_badge": "#9aa3b0",
    "select_top": "#9ba6b6", "select_bottom": "#6c7a90", "select_border": "#5b687c",
    "select_inactive_top": "#bcc2cb", "select_inactive_bottom": "#a2a9b4", "select_inactive_border": "#8f96a1",
    "accent_top": "#eef1f5", "accent_upper": "#c9d0da", "accent_mid": "#a9b3c1", "accent_bottom": "#8592a5",
    "accent_end": "#d3d9e2", "accent_border": "#5f6a7b", "accent_light": "#d0d6df", "accent_mid2": "#a2acbb",
    "accent_press_top": "#c5ccd6", "accent_press_mid": "#8f9aab", "accent_press_end": "#b8c0cc",
    "focus": "#a3adbb", "link": "#3d5a80", "selection_text": "#cdd5e0",
    "toggle_checked_text": "#3d5a80", "toggle_checked_top": "#dde2e9", "toggle_checked_bottom": "#c3cad4",
}

MIDNIGHT = {
    **AQUA,
    "window": "#2b2b2d", "text": "#e8e8e8", "dim": "#a3a3a3", "faint": "#7f7f7f",
    "base": "#1e1e20", "base_alt": "#252a32", "grid": "#38383b", "border": "#4a4a4d",
    "field_border": "#5a5a5d", "field_border_top": "#444447", "focus": "#4f8fe0",
    "source_list": "#24272c", "source_header": "#8d99ab", "source_text": "#e2e2e2", "source_badge": "#4c5869",
    "select_top": "#4f80c6", "select_bottom": "#2858a6", "select_border": "#204a8e",
    "select_inactive_top": "#4a5261", "select_inactive_bottom": "#3b4250", "select_inactive_border": "#323844",
    "toolbar_top": "#4b4b4e", "toolbar_bottom": "#2d2d30", "toolbar_line": "#0f0f10",
    "toolbar_shine": "rgba(255, 255, 255, 40)",
    "strip_top": "#3a3a3d", "strip_bottom": "#2e2e31",
    "bottombar_top": "#3c3c3f", "bottombar_bottom": "#28282b",
    "lcd_top": "#3d4339", "lcd_bottom": "#2b3029", "lcd_border": "#161616", "lcd_text": "#e8efd6",
    "button_top": "#5c5c5f", "button_upper": "#4e4e51", "button_lower": "#424245", "button_bottom": "#4b4b4e",
    "button_border": "#1d1d1f", "button_hover": "#0c0c0d",
    "button_press_top": "#3a3a3d", "button_press_upper": "#353538", "button_press_lower": "#2f2f32",
    "button_press_bottom": "#3a3a3d",
    "disabled_text": "#6f6f72", "disabled_border": "#3d3d40", "disabled_bg": "#333336",
    "menu_bg": "rgba(42, 42, 44, 245)", "menu_border": "#5a5a5d", "separator": "#48484b",
    "header_top": "#434346", "header_upper": "#3a3a3d", "header_lower": "#323235", "header_bottom": "#38383b",
    "header_line": "#4a4a4d", "header_bottom_line": "#1a1a1c",
    "trough_edge": "#262629", "trough_mid": "#303033", "trough_end": "#28282b", "trough_border": "#1a1a1c",
    "progress_bg": "#1e1e20", "progress_border": "#111112",
    "tab_pane": "#303033", "group_bg": "rgba(255, 255, 255, 8)", "group_border": "#4a4a4d",
    "check_top": "#55555a", "check_bottom": "#3c3c40", "check_border": "#1d1d1f",
    "tooltip_bg": "#3c3c3f", "tooltip_text": "#eeeeee", "tooltip_border": "#5a5a5d",
    "note_bg": "#463c1d", "note_text": "#f2dc9c", "note_border": "#6b5a26",
    "page_top": "#303033", "page_bottom": "#242427",
    "panel": "#1e1e20", "panel_border": "#46464a", "panel_soft": "rgba(0, 0, 0, 70)", "panel_soft_border": "#46464a",
    "art_title_top": "#3b3e43", "art_title_bottom": "#2c2f33", "art_title_text": "#dddddd", "art_title_border": "#55585e",
    "good": "#5dc85d", "bad": "#ff6b6b", "link": "#7ab4ff", "placeholder": "#7a7a7d", "selection_text": "#2c5a99",
    "free_top": "#4a4a4d", "free_bottom": "#38383b", "splitter": "#111112",
    "slider_groove": "#1e1e20", "slider_fill": "#9a9a9d", "slider_border": "#111112",
    "toggle_checked_text": "#9cc8ff", "toggle_checked_top": "#2f4b73", "toggle_checked_bottom": "#243a5a",
    "grid_top": "#1e1e20", "grid_bottom": "#242629", "icon": "#dadada",
}

PALETTES = {"aqua": AQUA, "graphite": GRAPHITE, "dark": MIDNIGHT}
STATUS_COLORS = {
    "ready": ("#5cb85c", "#3d8b3d"),
    "your pick": ("#5cb85c", "#3d8b3d"),
    "mac file": ("#6fa8ea", "#3b78c9"),
    "check": ("#f5b544", "#d48a0b"),
    "waiting": ("#b5b5b5", "#8c8c8c"),
    "not found": ("#ee6a6a", "#c63a3a"),
    "failed": ("#ee6a6a", "#c63a3a"),
    "file missing": ("#ee6a6a", "#c63a3a"),
    "on ipod": ("#6fa8ea", "#3b78c9"),
    "ipod only": ("#8f8fe8", "#5b5bc4"),
    # podcast episodes
    "new": ("#4f93e8", "#2b67c2"),
    "played": ("#b5b5b5", "#8c8c8c"),
    "get": ("#a7b4c6", "#7d8ca3"),
    "downloading": ("#6fa8ea", "#3b78c9"),
}

current_name = "aqua"
C: dict[str, str] = dict(AQUA)  # the palette in use


def c(name: str) -> str:
    """A colour from the palette in use."""
    return C[name]


def is_dark() -> bool:
    return current_name == "dark"


def _set_palette(name: str) -> None:
    """Make `name` the palette in use (and the old module-level names follow it)."""
    global current_name
    current_name = name if name in PALETTES else "aqua"
    C.clear()
    C.update(PALETTES[current_name])
    globals().update({
        "WINDOW": C["window"], "TEXT": C["text"], "SOURCE_LIST": C["source_list"], "SOURCE_HEADER": C["source_header"],
        "SELECT_TOP": C["select_top"], "SELECT_BOTTOM": C["select_bottom"], "SELECT_BORDER": C["select_border"],
        "SELECT_INACTIVE_TOP": C["select_inactive_top"], "SELECT_INACTIVE_BOTTOM": C["select_inactive_bottom"],
        "ROW_ALT": C["base_alt"], "GRID": C["grid"],
        "TOOLBAR_TOP": C["toolbar_top"], "TOOLBAR_BOTTOM": C["toolbar_bottom"], "TOOLBAR_LINE": C["toolbar_line"],
        "BOTTOMBAR_TOP": C["bottombar_top"], "BOTTOMBAR_BOTTOM": C["bottombar_bottom"],
        "LCD_TOP": C["lcd_top"], "LCD_BOTTOM": C["lcd_bottom"], "LCD_BORDER": C["lcd_border"], "LCD_TEXT": C["lcd_text"],
        "AQUA_TOP": C["accent_top"], "AQUA_MID": C["accent_mid"], "AQUA_BOTTOM": C["accent_bottom"],
        "AQUA_BORDER": C["accent_border"],
    })


def resolve(name: str, system_dark: bool = False) -> str:
    """The palette for an appearance setting ("auto" follows the system)."""
    if name == "auto":
        return "dark" if system_dark else "aqua"
    return name if name in PALETTES else "aqua"


def font_family() -> str:
    available = set(QFontDatabase.families())
    return next((family for family in FONT_FAMILIES if family in available), QFont().defaultFamily())


def stylesheet() -> str:
    k = C
    return f"""
* {{ color: {k['text']}; }}
QMainWindow, QDialog {{ background: {k['window']}; }}
QToolTip {{ background: {k['tooltip_bg']}; color: {k['tooltip_text']}; border: 1px solid {k['tooltip_border']}; padding: 2px 4px; }}

/* ---- push buttons: "gel" pills; the default button is Aqua blue ---- */
QPushButton {{
    min-height: 18px; padding: 2px 14px; border-radius: 10px;
    border: 1px solid {k['button_border']};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {k['button_top']}, stop:0.49 {k['button_upper']}, stop:0.5 {k['button_lower']}, stop:1 {k['button_bottom']});
}}
QPushButton:hover {{ border-color: {k['button_hover']}; }}
QPushButton:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {k['button_press_top']}, stop:0.49 {k['button_press_upper']}, stop:0.5 {k['button_press_lower']},
        stop:1 {k['button_press_bottom']});
}}
QPushButton:default, QPushButton[aqua="true"] {{
    color: #000; border: 1px solid {k['accent_border']};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {k['accent_top']}, stop:0.49 {k['accent_upper']}, stop:0.5 {k['accent_mid']}, stop:1 {k['accent_end']});
}}
QPushButton:default:pressed, QPushButton[aqua="true"]:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {k['accent_press_top']}, stop:0.49 {k['accent_press_mid']}, stop:0.5 {k['accent_bottom']},
        stop:1 {k['accent_press_end']});
}}
QPushButton:disabled {{ color: {k['disabled_text']}; border-color: {k['disabled_border']}; background: {k['disabled_bg']}; }}
QPushButton:checked {{
    color: #ffffff; border-color: {k['select_border']};
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {k['accent_upper']}, stop:0.5 {k['accent_bottom']},
        stop:0.51 {k['select_bottom']}, stop:1 {k['accent_mid2']});
}}

/* ---- text fields ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QDoubleSpinBox, QSpinBox {{
    background: {k['base']}; border: 1px solid {k['field_border']}; border-top-color: {k['field_border_top']};
    padding: 2px 4px; selection-background-color: {k['selection_text']}; selection-color: {k['text']};
}}
QLineEdit[search="true"] {{ border-radius: 10px; padding-left: 20px; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 2px solid {k['focus']}; padding: 1px 3px; }}
QLineEdit[search="true"]:focus {{ padding-left: 19px; }}

/* ---- pop-up menus (combo boxes) ---- */
QComboBox {{
    min-height: 18px; padding: 1px 22px 1px 10px; border-radius: 5px; border: 1px solid {k['button_border']};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['button_top']}, stop:0.5 {k['button_lower']},
        stop:1 {k['button_bottom']});
}}
QComboBox::drop-down {{
    width: 18px; border: none; border-left: 1px solid {k['accent_border']};
    border-top-right-radius: 5px; border-bottom-right-radius: 5px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['accent_top']}, stop:0.5 {k['accent_mid']},
        stop:1 {k['accent_end']});
}}
QComboBox::down-arrow {{ image: url({_res("updown.svg")}); width: 9px; height: 13px; }}
QComboBox QAbstractItemView {{ background: {k['base']}; selection-background-color: {k['select_bottom']};
    selection-color: #fff; }}

/* ---- menus ---- */
QMenu {{ background: {k['menu_bg']}; border: 1px solid {k['menu_border']}; padding: 4px 0; }}
QMenu::item {{ padding: 3px 22px; }}
QMenu::item:selected {{
    color: #fff; background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['select_top']}, stop:1 {k['select_bottom']});
}}
QMenu::item:disabled {{ color: {k['disabled_text']}; }}
QMenu::item:disabled:selected {{ color: {k['disabled_text']}; background: transparent; }}
QMenu::separator {{ height: 1px; background: {k['separator']}; margin: 4px 0; }}

/* ---- lists and tables ---- */
QTreeView, QListView, QTableView {{
    background: {k['base']}; alternate-background-color: {k['base_alt']}; border: 1px solid {k['border']};
    selection-background-color: {k['select_bottom']}; selection-color: #ffffff; outline: 0;
}}
QTreeView::item {{ padding: 1px 2px; border-right: 1px solid {k['grid']}; }}
QTableView::item {{ border-right: 1px solid {k['grid']}; }}
QTreeView::item:selected {{
    color: #ffffff; border-right: 1px solid {k['select_border']};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['select_top']}, stop:1 {k['select_bottom']});
}}
QTreeView::item:selected:!active {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['select_inactive_top']},
        stop:1 {k['select_inactive_bottom']});
}}
QHeaderView::section, QLabel[role="listHeader"] {{
    padding: 2px 6px; border: none; border-right: 1px solid {k['header_line']};
    border-bottom: 1px solid {k['header_bottom_line']};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['header_top']}, stop:0.5 {k['header_upper']},
        stop:0.51 {k['header_lower']}, stop:1 {k['header_bottom']});
    font-weight: normal;
}}
QHeaderView::section:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['accent_top']}, stop:0.5 {k['accent_upper']},
        stop:0.51 {k['accent_mid']}, stop:1 {k['accent_end']});
}}

/* ---- Aqua scroll bars: gel thumb in a pinstriped trough ---- */
QScrollBar:vertical {{ width: 15px; margin: 0; border-left: 1px solid {k['trough_border']};
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {k['trough_edge']}, stop:0.5 {k['trough_mid']},
        stop:1 {k['trough_end']}); }}
QScrollBar:horizontal {{ height: 15px; margin: 0; border-top: 1px solid {k['trough_border']};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['trough_edge']}, stop:0.5 {k['trough_mid']},
        stop:1 {k['trough_end']}); }}
QScrollBar::handle:vertical {{
    min-height: 28px; margin: 1px 1px 1px 2px; border-radius: 6px; border: 1px solid {k['accent_border']};
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {k['accent_light']}, stop:0.35 {k['accent_mid2']},
        stop:0.65 {k['accent_bottom']}, stop:1 {k['accent_upper']});
}}
QScrollBar::handle:horizontal {{
    min-width: 28px; margin: 2px 1px 1px 1px; border-radius: 6px; border: 1px solid {k['accent_border']};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['accent_light']}, stop:0.35 {k['accent_mid2']},
        stop:0.65 {k['accent_bottom']}, stop:1 {k['accent_upper']});
}}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- progress bars: Aqua gel ---- */
QProgressBar {{ border: 1px solid {k['progress_border']}; border-radius: 5px; background: {k['progress_bg']};
    text-align: center; height: 10px; }}
QProgressBar::chunk {{ border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['accent_top']}, stop:0.5 {k['accent_mid2']},
        stop:0.51 {k['accent_bottom']}, stop:1 {k['accent_end']}); }}

/* ---- tabs: Aqua segmented control ---- */
QTabWidget::pane {{ border: 1px solid {k['group_border']}; border-radius: 4px; top: -11px; padding-top: 14px;
    background: {k['tab_pane']}; }}
QTabWidget::tab-bar {{ alignment: center; }}
QTabBar::tab {{
    padding: 3px 14px; border: 1px solid {k['button_border']}; border-left: none; min-height: 16px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['button_top']}, stop:0.5 {k['button_lower']},
        stop:1 {k['button_bottom']});
}}
QTabBar::tab:first {{ border-left: 1px solid {k['button_border']}; border-top-left-radius: 10px;
    border-bottom-left-radius: 10px; }}
QTabBar::tab:last {{ border-top-right-radius: 10px; border-bottom-right-radius: 10px; }}
QTabBar::tab:selected {{ color: #000; border-color: {k['accent_border']};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['accent_top']}, stop:0.49 {k['accent_upper']},
        stop:0.5 {k['accent_mid']}, stop:1 {k['accent_end']}); }}

/* ---- group boxes: inset panels ---- */
QGroupBox {{ border: 1px solid {k['group_border']}; border-radius: 5px; margin-top: 18px; padding: 10px 8px 8px 8px;
    background: {k['group_bg']}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; top: 2px; font-weight: bold; }}

/* ---- check boxes and radio buttons ---- */
QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid {k['check_border']}; border-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['check_top']}, stop:1 {k['check_bottom']}); }}
QCheckBox::indicator:checked {{ border-color: {k['accent_border']}; image: url({_res("check.svg")});
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['accent_top']}, stop:0.5 {k['accent_mid']},
        stop:1 {k['accent_end']}); }}

/* ---- number boxes: Aqua little arrows ---- */
QAbstractSpinBox {{ padding-right: 18px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border; width: 15px; border-left: 1px solid {k['field_border']};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {k['button_top']}, stop:0.5 {k['button_lower']},
        stop:1 {k['button_bottom']});
}}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-bottom: 1px solid {k['group_border']}; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
QAbstractSpinBox::up-arrow {{ image: url({_res("up.svg")}); width: 8px; height: 5px; }}
QAbstractSpinBox::down-arrow {{ image: url({_res("down.svg")}); width: 8px; height: 5px; }}

QSplitter::handle {{ background: {k['splitter']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

/* ---- named parts of the main window ---- */
QLabel[role="dim"] {{ color: {k['dim']}; background: transparent; }}
QLabel[role="faint"] {{ color: {k['faint']}; background: transparent; }}
#sourceList {{ background: {k['source_list']}; border: none; }}
#sourceColumn {{ background: {k['source_list']}; }}
#artworkTitle {{ color: {k['art_title_text']}; font-size: 11px; border-top: 1px solid {k['art_title_border']};
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {k['art_title_top']}, stop:1 {k['art_title_bottom']});
    padding: 2px 6px; }}
#note {{ background: {k['note_bg']}; color: {k['note_text']}; border-bottom: 1px solid {k['note_border']};
    padding: 4px 12px; }}
#log {{ font-family: Monaco, Menlo, monospace; font-size: 11px; border: none; border-top: 1px solid {k['splitter']}; }}
#welcomePage, StatsPage {{ background: {k['base']}; }}
#welcomeTitle {{ font-size: 20px; font-weight: bold; color: {k['dim']}; }}
#welcomeText {{ color: {k['faint']}; }}
#ipodPage {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {k['page_top']}, stop:1 {k['page_bottom']}); }}
#summary {{ background: {k['panel']}; border: 1px solid {k['panel_border']}; border-radius: 6px; }}
#details {{ background: {k['panel_soft']}; border: 1px solid {k['panel_soft_border']}; border-radius: 6px; }}
#ipodTech {{ background: transparent; color: {k['dim']}; font-size: 11px; }}
#volume::groove:horizontal {{ height: 4px; border: 1px solid {k['slider_border']}; border-radius: 2px;
    background: {k['slider_groove']}; }}
#volume::sub-page:horizontal {{ border-radius: 2px; background: {k['slider_fill']}; }}
#volume::handle:horizontal {{ width: 12px; margin: -5px 0; border-radius: 6px; border: 1px solid #5b5b5b;
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:1 #b9b9b9); }}
QPushButton[role="bottomButton"] {{ border-radius: 3px; padding: 0; font-weight: bold; border: 1px solid {k['slider_border']};
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {k['button_top']}, stop:0.5 {k['button_lower']},
        stop:0.51 {k['button_press_lower']}, stop:1 {k['button_bottom']}); }}
QPushButton[role="bottomButton"]:pressed {{ background: {k['button_press_top']}; }}
QPushButton[role="bottomButton"]:checked {{ color: {k['toggle_checked_text']};
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {k['toggle_checked_top']},
        stop:1 {k['toggle_checked_bottom']}); }}
#summaryLabel {{ background: transparent; color: {k['text']}; font-size: 11px; }}
#albumGrid {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {k['grid_top']}, stop:1 {k['grid_bottom']}); }}
"""


STYLESHEET = stylesheet()


def system_is_dark(app: QApplication | None = None) -> bool:
    app = app or QApplication.instance()
    hints = app.styleHints() if app else None
    if hints is not None and hasattr(hints, "colorScheme"):  # Qt 6.5+
        from PySide6.QtCore import Qt

        return hints.colorScheme() == Qt.ColorScheme.Dark
    return False


def apply(app: QApplication, appearance: str | None = None) -> str:
    """Apply the look to the whole app. Returns the palette used ("aqua", "graphite" or "dark")."""
    global STYLESHEET
    if appearance is None:
        from synclassic.config import load_settings

        appearance = load_settings().appearance
    hints = app.styleHints()
    system_dark = False
    if appearance == "auto":
        if hasattr(hints, "unsetColorScheme"):  # Qt 6.8+: let macOS decide again
            hints.unsetColorScheme()
        system_dark = system_is_dark(app)
    _set_palette(resolve(appearance, system_dark))
    app.setStyle("Fusion")  # a neutral base so every control can be restyled the same on any OS
    if appearance != "auto" and hasattr(hints, "setColorScheme"):  # window frames to match the look
        from PySide6.QtCore import Qt

        hints.setColorScheme(Qt.ColorScheme.Dark if is_dark() else Qt.ColorScheme.Light)
    font = QFont(font_family())
    font.setPixelSize(12)
    app.setFont(font)
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, C["window"]), (QPalette.ColorRole.WindowText, C["text"]),
        (QPalette.ColorRole.Base, C["base"]), (QPalette.ColorRole.AlternateBase, C["base_alt"]),
        (QPalette.ColorRole.Text, C["text"]), (QPalette.ColorRole.Button, C["button_lower"]),
        (QPalette.ColorRole.ButtonText, C["text"]), (QPalette.ColorRole.Highlight, C["select_bottom"]),
        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, C["tooltip_bg"]),
        (QPalette.ColorRole.ToolTipText, C["tooltip_text"]), (QPalette.ColorRole.PlaceholderText, C["placeholder"]),
        (QPalette.ColorRole.Link, C["link"]), (QPalette.ColorRole.BrightText, C["text"]),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    STYLESHEET = stylesheet()
    app.setStyleSheet(STYLESHEET)
    from PySide6.QtWidgets import QWidget

    for widget in app.topLevelWidgets():  # self-drawn parts pick up the new colours
        if hasattr(widget, "theme_changed"):
            widget.theme_changed()
        widget.update()
        for child in widget.findChildren(QWidget):
            child.update()
    return current_name


_set_palette("aqua")
