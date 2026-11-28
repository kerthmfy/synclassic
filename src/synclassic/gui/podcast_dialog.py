"""Find a podcast (Apple's podcast directory) or paste its feed address, and subscribe."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from synclassic.gui.workers import mark_closed, run_in_background
from synclassic.podcasts import feed as feeds


def _looks_like_feed(text: str) -> bool:
    return text.strip().lower().startswith(("http://", "https://", "feed://"))


class SubscribeDialog(QDialog):
    """After exec(): `feed_urls` holds the feeds to subscribe to."""

    def __init__(self, parent=None, search=feeds.search):
        super().__init__(parent)
        self.setWindowTitle("Subscribe to a Podcast")
        self.resize(560, 460)
        self.feed_urls: list[str] = []
        self._search = search
        self._generation = 0
        layout = QVBoxLayout(self)

        intro = QLabel("Search for a podcast by name, or paste the address of its feed (RSS).")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setProperty("search", True)
        self.query.setPlaceholderText("Podcast name or feed address")
        self.query.returnPressed.connect(self.run_search)
        row.addWidget(self.query, 1)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.run_search)
        row.addWidget(self.search_button)
        layout.addLayout(row)

        self.results = QListWidget()
        self.results.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.results.setAlternatingRowColors(True)
        self.results.itemDoubleClicked.connect(lambda *_: self._accept_selection())
        self.results.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.results, 1)
        self.status = QLabel("")
        self.status.setProperty("role", "dim")
        layout.addWidget(self.status)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.subscribe_button = self.buttons.addButton("Subscribe", QDialogButtonBox.ButtonRole.AcceptRole)
        self.subscribe_button.setDefault(False)
        self.subscribe_button.setAutoDefault(False)
        self.subscribe_button.clicked.connect(self._accept_selection)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.query.textChanged.connect(self._update_buttons)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.subscribe_button.setEnabled(bool(self.results.selectedItems()) or _looks_like_feed(self.query.text()))

    def run_search(self) -> None:
        text = self.query.text().strip()
        if not text:
            return
        if _looks_like_feed(text):
            self._accept_selection()
            return
        self._generation += 1
        generation = self._generation
        self.status.setText("Searching…")
        self.search_button.setEnabled(False)

        def work(task):
            return self._search(text)

        def done(results):
            if generation != self._generation:
                return
            self.show_results(results)

        def failed(error):
            self.search_button.setEnabled(True)
            self.status.setText(f"Couldn't search: {error}")

        run_in_background(self, work, done, failed)

    def show_results(self, results: list[feeds.SearchResult]) -> None:
        self.search_button.setEnabled(True)
        self.results.clear()
        for result in results:
            detail = " · ".join(part for part in (result.author, result.genre,
                                                  f"{result.episodes} episodes" if result.episodes else "") if part)
            item = QListWidgetItem(f"{result.title}\n{detail}")
            item.setData(Qt.ItemDataRole.UserRole, result.feed_url)
            item.setToolTip(result.feed_url)
            self.results.addItem(item)
        self.status.setText(f"{len(results)} found" if results else "Nothing found. Try other words, or paste the feed address.")
        if results:
            self.results.setCurrentRow(0)
        self._update_buttons()

    def _accept_selection(self) -> None:
        urls = [item.data(Qt.ItemDataRole.UserRole) for item in self.results.selectedItems()]
        text = self.query.text().strip()
        if not urls and _looks_like_feed(text):
            urls = ["https://" + text[len("feed://"):] if text.lower().startswith("feed://") else text]
        if urls:
            self.feed_urls = urls
            self.accept()

    def done(self, result: int) -> None:
        mark_closed(self)
        super().done(result)

