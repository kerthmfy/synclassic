"""Let background tasks use the full-playlist reader, which must run on the main (window) thread."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QThread, Signal

from synclassic.sources.full_reader import read_full_playlist


class FullReaderBridge(QObject):
    _request = Signal(object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._request.connect(self._handle, Qt.ConnectionType.QueuedConnection)

    def read(self, ref, expected_total, name):
        """Callable from any thread; blocks that thread until the reader window is done."""
        if QThread.currentThread() is self.thread():
            return read_full_playlist(ref, expected_total, name, self.window)
        holder = {"done": threading.Event(), "result": None}
        self._request.emit((ref, expected_total, name, holder))
        holder["done"].wait()
        return holder["result"]

    def _handle(self, payload) -> None:
        ref, expected_total, name, holder = payload
        try:
            holder["result"] = read_full_playlist(ref, expected_total, name, self.window)
        finally:
            holder["done"].set()
