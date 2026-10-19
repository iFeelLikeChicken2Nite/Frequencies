"""Frequencies — the stream schedule.

A listings grid, in the manner of a television guide: one row per station,
time running left to right, a line where "now" is. Radio has always had
schedules and streams almost never publish them in any machine-readable
way, so these are yours — typed in once, or carried in with a station that
was exported with them.

Shows live on the station record under "shows", which means they travel
with an export, a backup and a featured entry without anything else having
to know about them. Each is:

    {"name": str, "days": [0-6], "start": "HH:MM", "end": "HH:MM",
     "notify": bool}

Days are Monday=0, matching datetime.weekday(), because every comparison
here ends up against one of those.
"""

import datetime

from PyQt5.QtCore import QPoint, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QScrollArea, QTimeEdit, QVBoxLayout, QWidget)

import storage
from theme import T

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def shows_for(station):
    return list(station.get("shows") or [])


def parse_time(text):
    """"HH:MM" to minutes past midnight, or None."""
    try:
        hours, minutes = str(text).split(":")
        return int(hours) * 60 + int(minutes)
    except Exception:                           # noqa: BLE001
        return None


def show_bounds(show):
    """(start, end) in minutes. An end before the start runs past midnight."""
    start = parse_time(show.get("start")) or 0
    end = parse_time(show.get("end"))
    if end is None:
        end = start + 60
    if end <= start:
        end += 24 * 60
    return start, end


def playing_now(station, when=None):
    """The show on this station at this moment, or None."""
    when = when or datetime.datetime.now()
    minute = when.hour * 60 + when.minute
    today = when.weekday()
    for show in shows_for(station):
        if today not in (show.get("days") or []):
            continue
        start, end = show_bounds(show)
        if start <= minute < end:
            return show
        # A show that began yesterday and is still running.
        if end > 24 * 60 and minute < end - 24 * 60:
            if (today - 1) % 7 in (show.get("days") or []):
                return show
    return None


def live_now(stations, when=None):
    """[(station, show)] for every scheduled show on the air right now."""
    found = []
    for station in stations:
        show = playing_now(station, when)
        if show is not None:
            found.append((station, show))
    return found


LIVE_RED = "#d93030"


def paint_live_pill(p, x, y):
    """A red "LIVE NOW" pill with its top-left at (x, y); its width."""
    font = QFont()
    font.setPixelSize(9)
    font.setBold(True)
    p.setFont(font)
    width = QFontMetrics(font).horizontalAdvance("LIVE NOW") + 12
    box = QRectF(x, y, width, 14)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(LIVE_RED))
    p.drawRoundedRect(box, 7, 7)
    p.setPen(QColor("#ffffff"))
    p.drawText(box, Qt.AlignCenter, "LIVE NOW")
    return width


class ShowDialog(QDialog):
    """One programme: what it is, when it is on, and whether to be told."""

    def __init__(self, parent=None, show=None, station_name=""):
        super().__init__(parent)
        self.setWindowTitle("Scheduled show")
        self.setMinimumWidth(420)
        show = show or {}

        outer = QVBoxLayout(self)
        if station_name:
            head = QLabel(station_name)
            head.setProperty("role", "secondary")
            outer.addWidget(head)

        form = QFormLayout()
        self.name = QLineEdit(show.get("name", ""))
        self.name.setPlaceholderText("Show name")
        form.addRow("Show name", self.name)

        self.start = QTimeEdit()
        import clock
        # Shown in the chosen style; still saved as 24-hour below.
        self.start.setDisplayFormat(clock.qt_format())
        self.end = QTimeEdit()
        self.end.setDisplayFormat(clock.qt_format())
        for widget, key, fallback in ((self.start, "start", "09:00"),
                                      (self.end, "end", "10:00")):
            minutes = parse_time(show.get(key) or fallback) or 0
            widget.setTime(widget.time().fromString(
                f"{minutes // 60:02d}:{minutes % 60:02d}", "HH:mm"))
        times = QHBoxLayout()
        times.addWidget(self.start)
        times.addWidget(QLabel("to"))
        times.addWidget(self.end)
        times.addStretch(1)
        form.addRow("Time", times)

        days = QHBoxLayout()
        self.days = []
        chosen = set(show.get("days") or list(range(7)))
        for index, label in enumerate(DAY_NAMES):
            box = QCheckBox(label)
            box.setChecked(index in chosen)
            days.addWidget(box)
            self.days.append(box)
        form.addRow("Days", days)

        self.notify = QCheckBox("Tell me when it starts")
        self.notify.setChecked(bool(show.get("notify", True)))
        form.addRow("", self.notify)
        outer.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def values(self):
        return {
            "name": self.name.text().strip() or "Untitled",
            "start": self.start.time().toString("HH:mm"),
            "end": self.end.time().toString("HH:mm"),
            "days": [i for i, box in enumerate(self.days) if box.isChecked()],
            "notify": self.notify.isChecked(),
        }


class Grid(QWidget):
    show_menu_requested = pyqtSignal(str, object, QPoint)
    """The listings themselves: stations down, hours across."""

    ROW_H = 62
    HEAD_H = 30
    NAME_W = 170
    HOUR_W = 150                # px per hour
    HOURS = 24

    show_activated = pyqtSignal(str, object)    # station id, show or None
    station_activated = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []                         # [(station, [shows])]
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self._hover = None

        # The "now" line has to move on its own or it is just a decoration
        # that was true when the page opened.
        self._tick = QTimer(self)
        self._tick.setInterval(30_000)
        self._tick.timeout.connect(self.update)
        self._tick.start()

    def set_logos(self, cache):
        """The shared logo cache, so the schedule shows the same artwork
        the station list does rather than fetching a second copy."""
        self._logos = cache
        self.update()

    def set_rows(self, rows):
        self._rows = list(rows)
        self.setMinimumWidth(self.NAME_W + self.HOUR_W * self.HOURS)
        self.setMinimumHeight(self.HEAD_H + self.ROW_H * max(1, len(rows)))
        self.updateGeometry()
        self.update()

    # -- geometry ----------------------------------------------------------

    def _scroll_x(self):
        """How far the view has been scrolled, in widget coordinates."""
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "horizontalScrollBar"):
            parent = parent.parentWidget()
        if parent is None:
            return 0
        return parent.horizontalScrollBar().value()

    def _logo_for(self, station):
        """This station's artwork, from the cache the list already fills."""
        logos = getattr(self, "_logos", None)
        if logos is None:
            return None
        try:
            return logos.get(station)
        except Exception:                       # noqa: BLE001
            return None

    def _x_for(self, minutes):
        return self.NAME_W + minutes / 60.0 * self.HOUR_W

    def _row_at(self, y):
        index = int((y - self.HEAD_H) // self.ROW_H)
        return index if 0 <= index < len(self._rows) else -1

    def _in_names(self, point):
        """On the pinned name column. It is drawn at the scroll offset, so
        once the grid is scrolled (it opens on the current hour) the name
        sits far to the right of x = NAME_W (task 144)."""
        return point.x() - self._scroll_x() < self.NAME_W

    def _show_at(self, point):
        row = self._row_at(point.y())
        if row < 0 or self._in_names(point):
            return row, None
        minutes = (point.x() - self.NAME_W) / self.HOUR_W * 60
        for show in self._rows[row][1]:
            start, end = show_bounds(show)
            if start <= minutes < end:
                return row, show
        return row, None

    # -- input -------------------------------------------------------------

    def mouseMoveEvent(self, e):
        hit = self._show_at(e.pos())
        if hit != self._hover:
            self._hover = hit
            self.update()
        super().mouseMoveEvent(e)

    def contextMenuEvent(self, e):
        """Right-click a show to delete it.

        Deleting used to mean opening the show and clearing it by hand,
        which is a strange way to throw something away.
        """
        row, show = self._show_at(e.pos())
        if row < 0 or show is None or self._in_names(e.pos()):
            return
        station = self._rows[row][0]
        self.show_menu_requested.emit(station.get("id", ""), show,
                                      e.globalPos())

    def mouseDoubleClickEvent(self, e):
        row, show = self._show_at(e.pos())
        if row < 0:
            return
        station = self._rows[row][0]
        # The logo, name or a show's listing plays that frequency (task
        # 137); an empty slot still adds a show.
        if self._in_names(e.pos()) or show is not None:
            self.station_activated.emit(station.get("id", ""))
        else:
            self.show_activated.emit(station.get("id", ""), show)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(T["bg0"]))

        now = datetime.datetime.now()
        minute_now = now.hour * 60 + now.minute
        today = now.weekday()

        self._paint_hours(p)
        for index, (station, shows) in enumerate(self._rows):
            top = self.HEAD_H + index * self.ROW_H
            self._paint_row(p, index, station, shows, top, today, minute_now)

        # Now, as a line the full height — the one thing you look for.
        x = self._x_for(minute_now)
        p.setPen(QPen(QColor(T["accent"]), 2))
        p.drawLine(int(x), self.HEAD_H, int(x), self.height())
        p.setBrush(QColor(T["accent"]))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(x - 4, self.HEAD_H - 4, 8, 8))
        p.end()

    def _paint_hours(self, p):
        font = QFont()
        font.setPixelSize(11)
        p.setFont(font)
        for hour in range(self.HOURS + 1):
            x = self._x_for(hour * 60)
            p.setPen(QPen(QColor(T["line"]), 1))
            p.drawLine(int(x), 0, int(x), self.height())
            if hour < self.HOURS:
                p.setPen(QColor(T["text3"]))
                p.drawText(QRect(int(x) + 6, 0, self.HOUR_W - 8, self.HEAD_H),
                           Qt.AlignVCenter | Qt.AlignLeft,
                           __import__("clock").fmt_hour(hour))
        p.setPen(QPen(QColor(T["line"]), 1))
        p.drawLine(0, self.HEAD_H, self.width(), self.HEAD_H)

    def _paint_row(self, p, index, station, shows, top, today, minute_now):
        # Alternating, as the station list is, so the eye can carry a row
        # across a very wide grid.
        if index % 2:
            p.fillRect(QRect(0, top, self.width(), self.ROW_H),
                       QColor(T["row_alt"]))

        # The name column is pinned to the left edge of the *view*, not of
        # the grid — it is drawn at the scroll offset, so it stays put
        # while the hours slide past it. Without that it scrolled away
        # with everything else and a wide schedule became anonymous rows.
        off = self._scroll_x()
        column = QRect(off, top, self.NAME_W, self.ROW_H)
        p.fillRect(column, QColor(T["bg1"]))

        art = self._logo_for(station)
        text_x = off + 12
        if art is not None and not art.isNull():
            side = self.ROW_H - 18
            p.drawPixmap(off + 10, top + 9, art.scaled(
                side, side, Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation))
            text_x = off + 10 + side + 10

        font = QFont()
        font.setPixelSize(12)
        p.setFont(font)
        p.setPen(QColor(T["text"]))
        name = storage.station_display_name(station)
        room = max(20, self.NAME_W - (text_x - off) - 12)
        # A row with a show on the air carries the red badge (task 131),
        # in the pinned column so it stays in view while the hours scroll.
        row_live = playing_now(station) is not None
        p.drawText(QRect(text_x, top, room,
                         self.ROW_H - (14 if row_live else 0)),
                   Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(font).elidedText(name, Qt.ElideRight, room))
        if row_live:
            paint_live_pill(p, text_x, top + self.ROW_H - 22)
        # A hairline down its right edge, so the pinned column reads as a
        # column rather than as rows that happen to start there.
        p.setPen(QPen(QColor(T["line"]), 1))
        p.drawLine(off + self.NAME_W, top,
                   off + self.NAME_W, top + self.ROW_H)
        p.setPen(QPen(QColor(T["line"]), 1))
        p.drawLine(0, top + self.ROW_H, self.width(), top + self.ROW_H)

        # Shows are kept to the right of the station column.
        #
        # The column is pinned — it stays put while the grid scrolls
        # sideways — but the shows are drawn after it, so scrolling slid
        # them straight over the station names. Clipped to where the
        # timeline actually starts, they pass underneath instead.
        p.save()
        p.setClipRect(QRectF(off + self.NAME_W, top,
                             max(0, self.width() - off - self.NAME_W),
                             self.ROW_H))
        for show in shows:
            if today not in (show.get("days") or []):
                continue
            start, end = show_bounds(show)
            left, right = self._x_for(start), self._x_for(min(end, 24 * 60))
            box = QRectF(left + 1, top + 4, max(6.0, right - left - 2),
                         self.ROW_H - 9)
            live = start <= minute_now < end
            hovered = self._hover == (index, show)

            fill = QColor(T["accent"] if live else T["bg2"])
            fill.setAlpha(70 if live else 255)
            if hovered:
                fill = QColor(T["bg3"])
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(box, 5, 5)
            if live:
                p.setPen(QPen(QColor(T["accent"]), 1))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)

            if box.width() < 26:
                continue
            title = QFont()
            title.setPixelSize(12)
            title.setBold(live)
            p.setFont(title)
            p.setPen(QColor(T["accent"] if live else T["text"]))
            # The badge follows the title when the box has room for both.
            name_w = QFontMetrics(title).horizontalAdvance(
                show.get("name", ""))
            badge = live and box.width() - 14 >= name_w + 76
            inner = box.adjusted(8, 4, -6, -box.height() / 2)
            p.drawText(inner, Qt.AlignVCenter | Qt.AlignLeft,
                       QFontMetrics(title).elidedText(
                           show.get("name", ""), Qt.ElideRight,
                           int(inner.width())))
            small = QFont()
            small.setPixelSize(10)
            p.setFont(small)
            p.setPen(QColor(T["text3"]))
            p.drawText(box.adjusted(8, box.height() / 2, -6, -4),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       f"{_shown_time(show.get('start', ''))} – "
                       f"{_shown_time(show.get('end', ''))}")
            if badge:
                paint_live_pill(p, box.left() + 8 + name_w + 8,
                                box.top() + 9)
        p.restore()


def _shown_time(text):
    """A stored "HH:mm" in the clock style the user chose."""
    import datetime
    import clock
    try:
        hours, minutes = (int(x) for x in str(text).split(":")[:2])
        return clock.fmt(datetime.datetime(2000, 1, 1, hours % 24, minutes))
    except (TypeError, ValueError):
        return str(text or "")


class SchedulePage(QWidget):
    """The grid, plus the controls that fill it."""

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if obj is getattr(self, "scroll", None) and \
                event.type() == QEvent.Resize:
            QTimer.singleShot(0, self._fit_vertical)
        return False

    def _fit_vertical(self):
        """A vertical bar only when the rows really do not fit.

        The grid is a whole day wide, so it nearly always has a
        horizontal bar, and that bar takes its height out of the view.
        With a handful of stations that was enough to push the last row a
        few pixels out of sight and bring up a vertical bar that scrolled
        almost nothing. Judged against the full height instead: if every
        row fits in the space the scroll area has, there is no vertical
        bar, and the area is made tall enough to show them all.
        """
        scroll = getattr(self, "scroll", None)
        if scroll is None:
            return
        # The grid sets its own minimum from the row count; that is the
        # height every row needs.
        needed = max(self.grid.minimumHeight(), self.grid.height())
        hbar = scroll.horizontalScrollBar()
        hbar_room = hbar.sizeHint().height() if hbar.maximum() > 0 else 0
        fits = needed + hbar_room <= scroll.height() + 1
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff if fits else Qt.ScrollBarAsNeeded)
        if fits:
            scroll.verticalScrollBar().setValue(0)

    play_station = pyqtSignal(str)
    changed = pyqtSignal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.lib = library

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 14, 16, 10)
        row.setSpacing(10)

        self.day = QComboBox()
        for index, label in enumerate(("Today", *DAY_NAMES)):
            self.day.addItem(label, index - 1)
        self.day.currentIndexChanged.connect(lambda _i: self.reload())
        row.addWidget(QLabel("Showing"))
        row.addWidget(self.day)

        self.only_scheduled = QCheckBox("Only stations with shows")
        # Off by default.
        #
        # On, the page opens empty for anyone who has not written a
        # schedule yet — which is everyone, at first — and an empty grid
        # reads as broken rather than as filtered.
        self.only_scheduled.setChecked(False)
        self.only_scheduled.toggled.connect(lambda _o: self.reload())
        row.addWidget(self.only_scheduled)
        row.addStretch(1)

        self.hint = QLabel("Double-click a slot to add a show, or a show to "
                           "play it. Right-click a show to edit it.")
        self.hint.setProperty("role", "secondary")
        row.addWidget(self.hint)
        outer.addWidget(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.grid = Grid()
        self.grid.show_activated.connect(self._edit)
        self.grid.show_menu_requested.connect(self._show_menu)
        self.grid.station_activated.connect(self.play_station.emit)
        self.scroll.setWidget(self.grid)
        self.scroll.installEventFilter(self)
        # The pinned column is drawn relative to the scroll position, so
        # the grid has to repaint as that changes.
        self.scroll.horizontalScrollBar().valueChanged.connect(
            lambda _v: self.grid.update())
        outer.addWidget(self.scroll, 1)

        self.reload()

    def reload(self):
        QTimer.singleShot(0, self._fit_vertical)
        return self._reload()

    def _reload(self):
        rows = []
        for station in self.lib.stations:
            shows = shows_for(station)
            if self.only_scheduled.isChecked() and not shows:
                continue
            rows.append((station, shows))
        self.grid.set_rows(rows)
        # After the layout has run: the scroll bar has no range until the
        # grid has been given its size, and setting a value against an
        # empty range does nothing at all.
        QTimer.singleShot(0, self._scroll_to_now)

    def _scroll_to_now(self):
        """Open on the current hour rather than on midnight."""
        now = datetime.datetime.now()
        x = self.grid._x_for(now.hour * 60) - Grid.NAME_W - 60
        self.scroll.horizontalScrollBar().setValue(max(0, int(x)))

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._scroll_to_now)

    def _show_menu(self, station_id, show, where):
        from PyQt5.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction("Edit show…",
                       lambda: self._edit(station_id, show))
        menu.addSeparator()
        menu.addAction("Delete show",
                       lambda: self._delete(station_id, show))
        menu.exec_(where)

    def _delete(self, station_id, show):
        """Take one show off a station's schedule."""
        station = self.lib.station_by_id(station_id)
        if station is None:
            return
        shows = [s for s in shows_for(station) if s != show]
        station["shows"] = shows
        self.lib.save_stations()
        self.reload()
        self.changed.emit()

    def _edit(self, station_id, show):
        station = self.lib.station_by_id(station_id)
        if station is None:
            return
        dialog = ShowDialog(self, show,
                            storage.station_display_name(station))
        if not dialog.exec_():
            return
        shows = shows_for(station)
        if show in shows:
            shows[shows.index(show)] = dialog.values()
        else:
            shows.append(dialog.values())
        station["shows"] = shows
        self.lib.save_stations()
        self.reload()
        self.changed.emit()


class Reminders:
    """Watches the clock and says when a scheduled show is starting.

    Checked once a minute against what has already been announced, so a
    show is mentioned at its start and not again — and never for a start
    that happened before the app was opened.
    """

    def __init__(self, library, on_show):
        self.lib = library
        self.on_show = on_show
        self._announced = set()
        self._timer = QTimer()
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self.check)
        self._timer.start()
        # Nothing from before launch: opening the app at 10:15 should not
        # produce a notice about a programme that began at ten.
        self.check(announce=False)

    def check(self, announce=True):
        if not self.lib.settings.get("show_reminders", True):
            return
        now = datetime.datetime.now()
        minute = now.hour * 60 + now.minute
        today = now.weekday()
        for station in self.lib.stations:
            for show in shows_for(station):
                if not show.get("notify", True):
                    continue
                if today not in (show.get("days") or []):
                    continue
                start, _end = show_bounds(show)
                # A minute either side, because a timer that fires every
                # sixty seconds will not land exactly on the minute.
                if abs(minute - start) > 1:
                    continue
                key = (station.get("id"), show.get("name"), start, today)
                if key in self._announced:
                    continue
                self._announced.add(key)
                if announce:
                    self.on_show(station, show)
