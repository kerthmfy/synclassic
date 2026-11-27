"""Turn banner.html into the README banner (docs/banner.png).

    python docs/banner/render.py              # writes docs/banner.png (2560 x 1280)
    python docs/banner/render.py --scale 1    # 1280 x 640
    python docs/banner/render.py --out ~/Desktop/banner.png

Uses Qt WebEngine and Qt PDF, which come with Synclassic's PySide6. The page is printed to a
one-page PDF and that PDF is drawn into the PNG, so no window or graphics card is involved
(capturing a window comes out blank on Macs, where web pages are drawn on the GPU).
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
WIDTH, HEIGHT = 1280, 640  # CSS pixels; the PDF page is the same size in points x 0.75


def render(source: pathlib.Path, out: pathlib.Path, scale: float = 2.0) -> pathlib.Path:
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--log-level=3")  # quiet Chromium's own logging
    from PySide6.QtCore import QMarginsF, QSize, QSizeF, QTimer, QUrl
    from PySide6.QtGui import QPageLayout, QPageSize
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    page = QWebEnginePage()
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
    result: dict = {}

    def loaded(ok: bool) -> None:
        if not ok:
            result["error"] = f"Couldn't open {source}"
            app.quit()
            return
        # Wait for the fonts, then give images and the background script a moment.
        page.runJavaScript("document.fonts.ready.then(() => 1)", 0, lambda _: QTimer.singleShot(500, print_pdf))

    def print_pdf() -> None:
        size = QPageSize(QSizeF(WIDTH * 0.75, HEIGHT * 0.75), QPageSize.Unit.Point, "banner",
                         QPageSize.SizeMatchPolicy.ExactMatch)
        layout = QPageLayout(size, QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0))
        page.printToPdf(lambda data: draw(bytes(data)), layout)

    def draw(data: bytes) -> None:
        try:
            if not data:
                result["error"] = "The page couldn't be printed."
                return
            with tempfile.TemporaryDirectory() as tmp:
                pdf_path = pathlib.Path(tmp) / "banner.pdf"
                pdf_path.write_bytes(data)
                document = QPdfDocument()
                document.load(str(pdf_path))
                if document.pageCount() < 1:
                    result["error"] = "The printed page couldn't be read."
                    return
                if document.pageCount() > 1:
                    print("Note: the banner spills onto a second page; only the first is used. "
                          "Keep everything inside 1280 x 640.")
                image = document.render(0, QSize(round(WIDTH * scale), round(HEIGHT * scale)))
                document.close()
            if image.isNull() or _blank(image):
                result["error"] = "The banner came out blank."
                return
            out.parent.mkdir(parents=True, exist_ok=True)
            result["ok"] = image.save(str(out))
        finally:
            app.quit()

    page.loadFinished.connect(loaded)
    page.load(QUrl.fromLocalFile(str(source)))
    QTimer.singleShot(60_000, app.quit)  # never hang
    app.exec()
    if not result.get("ok"):
        raise SystemExit(result.get("error", "The banner couldn't be rendered."))
    return out


def _blank(image) -> bool:
    """True when every sampled pixel is the same colour."""
    small = image.scaled(64, 32)
    first = small.pixel(0, 0)
    return all(small.pixel(x, y) == first for x in range(small.width()) for y in range(small.height()))


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
