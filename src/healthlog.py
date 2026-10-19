"""Frequencies — watching the health checker work.

Stations get marked down in the background, and until now the only
evidence was a station quietly acquiring a flag. That is fine when it is
right and impossible to argue with when it is wrong: there was no way to
see what was checked, when, or what the answer was.

This is that window. It is a log, not a control — nothing here changes
what the checker does. It fills while the app runs and empties when the
app closes, because a record of which stations answered an hour ago is
not worth a file on disk.
"""

import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QHeaderView,
                             QLabel, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout)

from theme import T

MAX_ROWS = 500

# Every check since launch, kept here so the window shows them whenever it
# is opened. It used to be filled only while open, so opening it after the
# launch check had run said "Nothing checked yet" (task 147).
_HISTORY = []
_WINDOW = None


def note(station_name, ok, reason=""):
    """One check: kept, and shown in the window if it is open."""
    _HISTORY.append((time.time(), station_name, ok, reason))
    del _HISTORY[:-MAX_ROWS]
    if _WINDOW is not None:
        try:
            _WINDOW.record(station_name, ok, reason)
        except RuntimeError:                    # window already deleted
            pass


class HealthLogWindow(QDialog):
    """Every background check, newest first."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Station health checks")
        self.resize(720, 460)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        top = QHBoxLayout()
        self.summary = QLabel("Nothing checked yet.")
        self.summary.setProperty("role", "secondary")
        top.addWidget(self.summary, 1)

        # Failures are what anyone opens this for; everything else is
        # background noise once you have seen it works.
        self.only_failures = QCheckBox("Only what failed")
        self.only_failures.toggled.connect(self._refilter)
        top.addWidget(self.only_failures)

        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        top.addWidget(clear)
        outer.addLayout(top)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Time", "Station", "Result", "Reason"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.Stretch)
        head.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(3, QHeaderView.Stretch)
        outer.addWidget(self.table, 1)

        self._checked = 0
        self._failed = 0
        for when, name, ok, reason in _HISTORY:
            self.record(name, ok, reason, when)
        global _WINDOW
        _WINDOW = self

    def record(self, station_name, ok, reason="", when=None):
        """One check, as it happens.

        Newest at the top: the reason to have this open is to watch what
        is happening now, and scrolling to the bottom to find it would be
        the wrong way round.
        """
        self._checked += 1
        if not ok:
            self._failed += 1

        self.table.insertRow(0)
        import clock
        cells = (clock.fmt(when or time.time(), seconds=True),
                 station_name or "(unnamed)",
                 "answered" if ok else "no answer",
                 reason or "")
        for column, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if not ok:
                item.setForeground(Qt.red if column == 2 else item.foreground())
            self.table.setItem(0, column, item)

        # Bounded, because this runs for as long as the app does.
        while self.table.rowCount() > MAX_ROWS:
            self.table.removeRow(self.table.rowCount() - 1)

        self._update_summary()
        self._refilter()

    def _update_summary(self):
        self.summary.setText(
            f"{self._checked} checked · {self._failed} with no answer")

    def _refilter(self, *_a):
        only_bad = self.only_failures.isChecked()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 2)
            failed = item is not None and item.text() != "answered"
            self.table.setRowHidden(row, only_bad and not failed)

    def clear(self):
        del _HISTORY[:]
        self.table.setRowCount(0)
        self._checked = self._failed = 0
        self.summary.setText("Nothing checked yet.")
