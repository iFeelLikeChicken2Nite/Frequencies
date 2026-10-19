"""Frequencies — importing several shared stations at once.

One picture is one notification. Twelve are not: a stream of toasts,
each replacing the last, tells you nothing about the ones that failed.
So a batch gets a window with a line per file, filled in as each one is
read, and a count at the top.

The reading itself is done by the caller's function, one file per tick
of the event loop. That keeps the window responsive — decoding a large
photograph takes long enough to notice — without the complication of a
worker thread touching the station list.
"""

import os

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QHeaderView, QLabel,
                             QProgressBar, QTableWidget, QTableWidgetItem,
                             QVBoxLayout)

from theme import T


class ImportWindow(QDialog):
    """A line per file, and the result of each."""

    finished_all = pyqtSignal(int)          # how many were added

    def __init__(self, paths, importer, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Importing shared frequencies")
        self.resize(620, 420)
        self._paths = list(paths)
        self._importer = importer
        self._next = 0
        self._added = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        self.summary = QLabel("")
        outer.addWidget(self.summary)

        self.bar = QProgressBar()
        self.bar.setRange(0, len(self._paths))
        self.bar.setTextVisible(False)
        outer.addWidget(self.bar)

        self.table = QTableWidget(len(self._paths), 2)
        self.table.setHorizontalHeaderLabels(["File", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        head.setSectionResizeMode(1, QHeaderView.Stretch)
        for row, path in enumerate(self._paths):
            self.table.setItem(row, 0, QTableWidgetItem(os.path.basename(path)))
            self.table.setItem(row, 1, QTableWidgetItem("Waiting"))
        outer.addWidget(self.table, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.Close).setEnabled(False)
        outer.addWidget(self.buttons)

        self._update_summary()

    def start(self):
        QTimer.singleShot(0, self._step)

    def _step(self):
        """Read one file, report it, then yield before the next.

        One per turn of the event loop, so the window repaints between
        files and the progress is something you can watch rather than a
        frozen window followed by a finished one.
        """
        if self._next >= len(self._paths):
            self._done()
            return

        row = self._next
        self.table.item(row, 1).setText("Reading…")
        self.table.scrollToItem(self.table.item(row, 0))

        try:
            ok, message = self._importer(self._paths[row])
        except Exception:                       # noqa: BLE001
            ok, message = False, "That picture could not be read."

        status = self.table.item(row, 1)
        if ok:
            self._added += 1
            status.setText(f"Added — {message}")
            status.setForeground(Qt.darkGreen)
        else:
            status.setText(message)
            status.setForeground(Qt.red)

        self._next += 1
        self.bar.setValue(self._next)
        self._update_summary()
        QTimer.singleShot(0, self._step)

    def _update_summary(self):
        total = len(self._paths)
        if self._next < total:
            self.summary.setText(f"Reading {self._next + 1} of {total}")
        else:
            failed = total - self._added
            text = f"{self._added} added"
            if failed:
                text += f" · {failed} not imported"
            self.summary.setText(text)

    def _done(self):
        self.buttons.button(QDialogButtonBox.Close).setEnabled(True)
        self.finished_all.emit(self._added)
