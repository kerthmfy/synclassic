"""Turn banner.html into the README banner (docs/banner.png).

    python docs/banner/render.py              # writes docs/banner.png (2560 x 1280)
    python docs/banner/render.py --scale 1    # 1280 x 640
    python docs/banner/render.py --out ~/Desktop/banner.png

Uses Qt WebEngine, which Synclassic already installs. A window shows the banner for a moment
while it's captured.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
WIDTH, HEIGHT = 1280, 640


def render(source: pathlib.Path, out: pathlib.Path, scale: float = 2.0) -> pathlib.Path:
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    view = QWebEngineView()
    view.setWindowTitle("Rendering the banner…")
    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
    # Render the 1280 x 640 page at `scale` by zooming it into a bigger view, so the PNG is
    # sharp whatever the screen's own pixel density is.
    ratio = view.devicePixelRatioF() or 1.0
    view.setZoomFactor(scale / ratio)
    view.resize(round(WIDTH * scale / ratio), round(HEIGHT * scale / ratio))
    result: dict = {}

    def capture() -> None:
        image = view.grab().toImage()
        if image.width() != round(WIDTH * scale):  # a screen at another density: fit exactly
            from PySide6.QtCore import Qt

            image = image.scaled(round(WIDTH * scale), round(HEIGHT * scale), Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        out.parent.mkdir(parents=True, exist_ok=True)
        result["ok"] = image.save(str(out))
        app.quit()

    def loaded(ok: bool) -> None:
        if not ok:
            result["error"] = f"Couldn't open {source}"
            app.quit()
            return
        # Wait for the web fonts and images to be drawn.
        view.page().runJavaScript("document.fonts.ready.then(() => 1)", 0, lambda _: QTimer.singleShot(600, capture))

    view.loadFinished.connect(loaded)
    view.load(QUrl.fromLocalFile(str(source)))
    view.show()
    QTimer.singleShot(30_000, app.quit)  # never hang
    app.exec()
    if not result.get("ok"):
        raise SystemExit(result.get("error", "The banner couldn't be rendered."))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=pathlib.Path, default=HERE / "banner.html")
    parser.add_argument("--out", type=pathlib.Path, default=HERE.parent / "banner.png")
    parser.add_argument("--scale", type=float, default=2.0, help="2 = sharp on Retina screens (default)")
    args = parser.parse_args()
    path = render(args.source.resolve(), args.out.expanduser().resolve(), args.scale)
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
