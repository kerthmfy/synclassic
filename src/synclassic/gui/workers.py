"""Run slow work (Spotify, YouTube, downloads) off the main thread so the window stays responsive."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal


class Task(QObject):
    """Handed to the background function so it can report back and check for Stop."""

    message = Signal(str)
    progress = Signal(int, int, str)  # number, total, text
    changed = Signal()  # the library changed; views should refresh

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def should_stop(self) -> bool:
        return self._stop.is_set()

    def say(self, text: str) -> None:
        self.message.emit(text)


class Worker(QThread):
    """Runs `function(task)` in a background thread.

    `done(result)` or `failed(message)` is emitted on the main thread when it ends.
    """

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, function: Callable[[Task], Any], parent: QObject | None = None):
        super().__init__(parent)
        self.function = function
        self.task = Task()

    def run(self) -> None:
        try:
            result = self.function(self.task)
        except Exception as error:  # report anything to the user instead of crashing
            traceback.print_exc()
            self.failed.emit(str(error) or error.__class__.__name__)
        else:
            self.done.emit(result)


def run_in_background(
    owner: QObject,
    function: Callable[[Task], Any],
    on_done: Callable[[Any], None] | None = None,
    on_failed: Callable[[str], None] | None = None,
) -> Worker:
    """Start `function` in the background.

    The thread is not owned by `owner`, so closing a window never kills a running thread.
    Results arriving after `owner` was closed/deleted are ignored.
    """
    worker = Worker(function)
    alive = {"yes": True}
    owner.destroyed.connect(lambda *_: alive.update(yes=False))

    def guarded(callback):
        def call(value):
            if alive["yes"] and not getattr(owner, "_synclassic_closed", False):
                callback(value)

        return call

    if on_done:
        worker.done.connect(guarded(on_done))
    if on_failed:
        worker.failed.connect(guarded(on_failed))
    _RUNNING.add(worker)
    worker.finished.connect(lambda: _RUNNING.discard(worker))
    worker.finished.connect(worker.deleteLater)
    worker.start()
    return worker


# Keeps threads referenced until they finish.
_RUNNING: set[Worker] = set()


def mark_closed(owner: QObject) -> None:
    """Ignore any results still on their way to `owner`."""
    owner._synclassic_closed = True
