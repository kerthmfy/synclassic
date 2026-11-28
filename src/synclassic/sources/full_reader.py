"""Read every song of a public Spotify playlist, however long, without logging in.

Spotify's embed only shares 100 songs. This opens Spotify's real web page in a small built-in
browser window, scrolls down the song list automatically, and records each row while it's on
screen (Spotify only draws about 30 rows at a time). Rows include the song's Spotify ID, title,
artists, album, length and cover (upgraded from the 64 px row thumbnail to 640 px).

It depends on Spotify's page layout, so it can stop working when Spotify redesigns the page;
Synclassic then falls back to the first 100 songs and says so.
"""

from __future__ import annotations

import json
import time

from synclassic.artwork import upgrade_cover_url
from synclassic.models import SourcePlaylist, Track
from synclassic.sources.spotify_links import SpotifyRef

# Runs inside the Spotify page. Collects the visible rows of the playlist's own song list
# (not "Recommended") and scrolls the list's scroll area down by most of a screen.
CAPTURE_JS = r"""
(() => {
  const grid = document.querySelector('main [data-testid="playlist-tracklist"]');
  if (!grid) return JSON.stringify({ready: false});
  const rows = {};
  for (const row of grid.querySelectorAll('[role="row"][aria-rowindex]')) {
    const link = row.querySelector('a[href*="/track/"]');
    if (!link) continue;
    const id = (link.getAttribute('href').match(/track\/([A-Za-z0-9]{22})/) || [])[1];
    if (!id) continue;
    const leafTexts = [...row.querySelectorAll('div, span')]
      .filter(e => e.childElementCount === 0).map(e => (e.innerText || '').trim());
    const albumLink = row.querySelector('a[href*="/album/"]');
    const image = row.querySelector('img');
    rows[row.getAttribute('aria-rowindex')] = {
      id: id,
      title: (link.innerText || link.textContent || '').trim(),
      artists: [...row.querySelectorAll('a[href*="/artist/"]')].map(a => (a.innerText || '').trim()).filter(Boolean),
      album: albumLink ? (albumLink.innerText || '').trim() : '',
      album_id: albumLink ? ((albumLink.getAttribute('href').match(/album\/([A-Za-z0-9]{22})/) || [])[1] || '') : '',
      time: leafTexts.find(t => /^\d{1,2}:\d{2}(:\d{2})?$/.test(t)) || '',
      explicit: !!row.querySelector('[aria-label="Explicit"], [title="Explicit"]'),
      image: image ? (image.currentSrc || image.src || '') : '',
    };
  }
  let scroller = grid;
  while (scroller && !(scroller.scrollHeight > scroller.clientHeight + 50 &&
         /(auto|scroll)/.test(getComputedStyle(scroller).overflowY))) {
    scroller = scroller.parentElement;
  }
  let atBottom = true;
  if (scroller) {
    atBottom = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4;
    if (__SCROLL__) scroller.scrollTop = scroller.scrollTop + Math.floor(scroller.clientHeight * 0.8);
  }
  const heading = document.querySelector('main h1');
  return JSON.stringify({
    ready: true,
    rowcount: parseInt(grid.getAttribute('aria-rowcount') || '0', 10),
    name: heading ? heading.innerText.trim() : '',
    atBottom: atBottom,
    rows: rows,
  });
})()
"""


def parse_time(text: str) -> int | None:
    parts = [int(p) for p in text.split(":") if p.isdigit()] if text else []
    if len(parts) not in (2, 3):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds * 1000


def rows_to_tracks(rows: dict[str, dict]) -> list[Track]:
    """Captured rows (keyed by their position on the page) -> songs in playlist order."""
    tracks = []
    for key in sorted(rows, key=lambda k: int(k)):
        row = rows[key]
        if not row.get("id") or not row.get("title"):
            continue
        tracks.append(
            Track(
                title=row["title"],
                artists=list(row.get("artists") or []),
                album=row.get("album") or "",
                duration_ms=parse_time(row.get("time") or ""),
                spotify_id=row["id"],
                explicit=True if row.get("explicit") else None,
                cover_url=upgrade_cover_url(row.get("image") or None),
            )
        )
    return tracks


def read_full_playlist(ref: SpotifyRef, expected_total: int | None = None, name: str = "", parent=None) -> SourcePlaylist | None:
    """Show the reader window and return the whole playlist, or None if it failed/was cancelled.

    Must be called on the main (GUI) thread. Works from the terminal too: a Qt application is
    created if there isn't one.
    """
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return None
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        return None
    app = QApplication.instance() or QApplication([])
    del app  # only needed to exist
    reader = _ReaderWindow(ref, expected_total, name, parent)
    return reader.run()


def _ReaderWindow(ref, expected_total, name, parent):  # noqa: N802 (built lazily so Qt is optional)
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

    class Reader(QDialog):
        TICK_MS = 600
        LOAD_TIMEOUT_S = 45
        STALL_LIMIT = 20  # ticks at the bottom without new rows

        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle("Reading playlist from Spotify")
            self.resize(560, 520)
            self.rows: dict[str, dict] = {}
            self.rowcount = 0
            self.name = name
            self.stalls = 0
            self.started = time.monotonic()
            self.result: SourcePlaylist | None = None
            self.loop = QEventLoop(self)
            self.busy = False

            layout = QVBoxLayout(self)
            self.label = QLabel(f"Reading “{name or 'playlist'}” from Spotify's web page…")
            self.label.setWordWrap(True)
            layout.addWidget(self.label)
            self.progress = QProgressBar()
            self.progress.setRange(0, 0)
            layout.addWidget(self.progress)
            # An off-the-record profile: nothing from this page is kept on disk. The profile is
            # owned by the page so Qt deletes the page first (the other order can crash).
            self.profile = QWebEngineProfile()
            self.page = QWebEnginePage(self.profile, self)
            self.profile.setParent(self.page)
            self.view = QWebEngineView(self)
            self.view.setPage(self.page)
            self.view.setZoomFactor(0.5)  # more rows per screen = fewer scroll steps
            layout.addWidget(self.view, 1)
            buttons = QHBoxLayout()
            buttons.addStretch(1)
            cancel = QPushButton("Cancel")
            cancel.clicked.connect(self.reject)
            buttons.addWidget(cancel)
            layout.addLayout(buttons)

            self.timer = QTimer(self)
            self.timer.setInterval(self.TICK_MS)
            self.timer.timeout.connect(self.tick)
            self.rejected.connect(lambda: self.finish(None))

        def run(self) -> SourcePlaylist | None:
            self.show()
            self.view.load(QUrl(ref.url))
            self.timer.start()
            self.loop.exec()
            self.timer.stop()
            self.page.triggerAction(QWebEnginePage.WebAction.Stop)
            self.hide()
            self.deleteLater()
            return self.result

        def tick(self) -> None:
            if self.busy:
                return
            if not self.rows and time.monotonic() - self.started > self.LOAD_TIMEOUT_S:
                self.finish(None)
                return
            self.busy = True
            self.page.runJavaScript(CAPTURE_JS.replace("__SCROLL__", "true"), 0, self.captured)

        def captured(self, raw) -> None:
            self.busy = False
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                data = {}
            if not data.get("ready"):
                return
            self.rowcount = data.get("rowcount") or self.rowcount
            self.name = self.name or data.get("name") or ""
            before = len(self.rows)
            self.rows.update(data.get("rows") or {})
            # aria-rowcount includes the header row.
            total = expected_total or (self.rowcount - 1 if self.rowcount > 1 else 0)
            if total:
                self.progress.setRange(0, total)
                self.progress.setValue(min(len(self.rows), total))
            self.label.setText(
                f"Reading “{self.name or 'playlist'}” from Spotify's web page… "
                f"{len(self.rows)}" + (f" of {total}" if total else "") + " songs"
            )
            if total and len(self.rows) >= total:
                self.finish(self.build(total))
                return
            if len(self.rows) == before and data.get("atBottom"):
                self.stalls += 1
                if self.stalls >= self.STALL_LIMIT:
                    self.finish(self.build(total))
            else:
                self.stalls = 0

        def build(self, total: int) -> SourcePlaylist | None:
            tracks = rows_to_tracks(self.rows)
            if not tracks:
                return None
            missing = total - len(tracks) if total else 0
            note = ""
            if missing > 0:
                note = f"Spotify's page showed {len(tracks)} of {total} songs; {missing} couldn't be read."
            return SourcePlaylist(name=self.name or "Spotify playlist", tracks=tracks, truncated=missing > 0, note=note)

        def finish(self, result: SourcePlaylist | None) -> None:
            if self.loop.isRunning():
                self.result = result
                self.loop.quit()

    return Reader()


def available() -> bool:
    """True when the built-in browser (Qt WebEngine) is installed."""
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        return False
    return True


def terminal_reader(ref: SpotifyRef, expected_total: int | None, name: str) -> SourcePlaylist | None:
    """For the `synclassic` command: same reader, shown as its own small window."""
    count = f"all {expected_total} songs" if expected_total else "every song"
    print(f"Reading {count} of “{name}” from Spotify's web page (a small window opens)…", flush=True)
    try:
        return read_full_playlist(ref, expected_total, name)
    finally:
        from synclassic.cli import sane_terminal

        sane_terminal()  # the browser can leave Terminal's typing in a raw mode
