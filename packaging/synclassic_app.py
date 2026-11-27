"""Entry point of the standalone Synclassic.app (built with PyInstaller).

Opens the app; with --cli, runs the command line instead (used by the background updates)."""

import sys


def run() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from synclassic.cli import main

        return main(sys.argv[2:])
    from synclassic.gui.app import main

    return main()


if __name__ == "__main__":
    sys.exit(run())
