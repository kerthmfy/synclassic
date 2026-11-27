"""Start the Synclassic app."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from synclassic.macapp import name_menu_bar_app

    _add_homebrew_to_path()
    name_menu_bar_app()  # must happen before the Qt application starts

    from synclassic.gui import theme
    from synclassic.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Synclassic")
    app.setOrganizationName("Synclassic")
    theme.apply(app)
    from synclassic.gui.icon import app_icon

    app.setWindowIcon(app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


def _add_homebrew_to_path() -> None:
    """Apps opened from Finder don't get Terminal's PATH; ffmpeg and deno usually live here."""
    import os

    if sys.platform != "darwin":
        return
    parts = os.environ.get("PATH", "").split(os.pathsep)
    for folder in ("/usr/local/bin", "/opt/homebrew/bin"):
        if folder not in parts and os.path.isdir(folder):
            parts.insert(0, folder)
    os.environ["PATH"] = os.pathsep.join(parts)


if __name__ == "__main__":
    sys.exit(main())
