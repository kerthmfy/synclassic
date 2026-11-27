"""Turn banner.html into the README banner (docs/banner.png).

    python docs/banner/render.py              # writes docs/banner.png (2560 x 1280)
    python docs/banner/render.py --scale 1    # 1280 x 640
    python docs/banner/render.py --out ~/Desktop/banner.png

Two ways, tried in this order (it says which one it used):
1. A Chrome-family browser you have (Google Chrome, Chromium, Brave, Microsoft Edge), run
   invisibly ("headless") to take a picture of the page: exactly what a browser shows.
2. Qt WebEngine + Qt PDF (part of Synclassic's PySide6): the page is printed to a one-page PDF,
   which is drawn into the PNG. Needs no browser.
Neither saves a blank picture; if both fail, both reasons are printed.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
WIDTH, HEIGHT = 1280, 640  # CSS pixels; the PDF page is the same size in points x 0.75

MAC_BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
COMMAND_BROWSERS = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser",
                    "microsoft-edge"]


class RenderError(RuntimeError):
    pass


def find_browser() -> str | None:
    chosen = os.environ.get("SYNCLASSIC_BROWSER")
    if chosen:
        return chosen
    for path in MAC_BROWSERS + [str(pathlib.Path.home() / p.lstrip("/")) for p in MAC_BROWSERS]:
        if os.path.exists(path):
            return path
    for name in COMMAND_BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def check_picture(path: pathlib.Path, scale: float) -> None:
    """The PNG has the right size and isn't one flat colour."""
    from PIL import Image

    if not path.is_file():
        raise RenderError("no picture was written")
    with Image.open(path) as picture:
        expected = (round(WIDTH * scale), round(HEIGHT * scale))
        if picture.size != expected:
            raise RenderError(f"the picture is {picture.size[0]}x{picture.size[1]}, not {expected[0]}x{expected[1]}")
        low, high = picture.convert("L").resize((64, 32)).getextrema()
        if low == high:
            raise RenderError("the picture came out blank")


def render_with_browser(source: pathlib.Path, out: pathlib.Path, scale: float, browser: str) -> pathlib.Path:
    with tempfile.TemporaryDirectory(prefix="synclassic-banner-") as tmp:
        shot = pathlib.Path(tmp) / "banner.png"
        command = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                   "--no-default-browser-check", "--allow-file-access-from-files",
                   f"--user-data-dir={pathlib.Path(tmp) / 'profile'}",  # never touches your own browser profile
                   f"--window-size={WIDTH},{HEIGHT}", f"--force-device-scale-factor={scale:g}",
                   "--virtual-time-budget=4000",  # time for fonts, images and the background script
                   f"--screenshot={shot}", source.as_uri()]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            command.insert(1, "--no-sandbox")  # Chrome refuses to run as root otherwise (servers, containers)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RenderError(f"couldn't run {pathlib.Path(browser).name}: {error}") from error
        if result.returncode != 0 and not shot.is_file():
            detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no details"]
            raise RenderError(f"{pathlib.Path(browser).name} failed: {detail[0]}")
        check_picture(shot, scale)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(shot), out)
    return out


def render_with_qt(source: pathlib.Path, out: pathlib.Path, scale: float = 2.0) -> pathlib.Path:
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
        raise RenderError(result.get("error", "the page couldn't be printed"))
    check_picture(out, scale)
    return out


def render(source: pathlib.Path, out: pathlib.Path, scale: float = 2.0, method: str = "auto") -> tuple[pathlib.Path, str]:
    """Returns (picture, how it was made)."""
    problems = []
    if method in ("auto", "browser"):
        browser = find_browser()
        if browser:
            try:
                return render_with_browser(source, out, scale, browser), pathlib.Path(browser).name
            except RenderError as error:
                problems.append(f"browser: {error}")
        else:
            problems.append("browser: no Chrome, Chromium, Brave or Edge found")
    if method in ("auto", "qt"):
        try:
            return render_with_qt(source, out, scale), "Qt WebEngine + Qt PDF"
        except (RenderError, ImportError) as error:
            problems.append(f"Qt: {error}")
    raise RenderError("The banner couldn't be made.\n  " + "\n  ".join(problems))


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
    parser.add_argument("--method", choices=["auto", "browser", "qt"], default="auto")
    args = parser.parse_args()
    try:
        path, how = render(args.source.resolve(), args.out.expanduser().resolve(), args.scale, args.method)
    except RenderError as error:
        raise SystemExit(str(error)) from None
    print(f"Saved {path} (made with {how})")


if __name__ == "__main__":
    main()
