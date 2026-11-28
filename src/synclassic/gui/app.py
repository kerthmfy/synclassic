"""Start the Synclassic app."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from synclassic.macapp import name_menu_bar_app

    from synclassic import tools

    tools.add_to_path()  # the app's own ffmpeg and deno, or Homebrew's
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

    QTimer.singleShot(400, lambda: _first_run(window))
    return app.exec()


def _first_run(window) -> None:
    """The setup assistant the very first time; afterwards a reminder if the tools are missing,
    and the daily look for a new version."""
    from synclassic import app_update
    from synclassic.config import load_settings
    from synclassic.gui.setup_assistant import SetupAssistant, should_show

    if should_show(window.library):
        SetupAssistant(window).exec()
        window.refresh()
    else:
        _check_tools(window)
    if load_settings().check_updates and app_update.due():
        window.check_for_updates(quiet=True)


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


if __name__ == "__main__":
    sys.exit(main())
