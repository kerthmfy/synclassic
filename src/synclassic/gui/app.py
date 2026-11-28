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
    hints = app.styleHints()
    if hasattr(hints, "colorSchemeChanged"):  # "Automatic" follows macOS switching to dark and back

        def follow(*_):
            from synclassic.config import load_settings

            if load_settings().appearance == "auto":
                theme.apply(app, "auto")

        hints.colorSchemeChanged.connect(follow)
    from synclassic.gui.icon import app_icon

    app.setWindowIcon(app_icon())
    window = MainWindow()
    window.show()
    from PySide6.QtCore import QTimer

    QTimer.singleShot(800, lambda: _check_tools(window))
    return app.exec()


def _check_tools(window) -> None:
    """ffmpeg and deno come from Homebrew (also for the standalone app): say how to get them."""
    from PySide6.QtWidgets import QMessageBox

    from synclassic import downloader

    problems = downloader.check_tools()
    if problems:
        QMessageBox.information(
            window, "One more thing",
            "Synclassic needs two free tools to download music:\n\n" + "\n".join(problems)
            + "\n\nAll at once, in Terminal: brew install ffmpeg deno\n(Homebrew: https://brew.sh)")


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
