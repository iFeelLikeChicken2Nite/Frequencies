"""Frequencies — History.

Everything Shazam identified, as a grid of covers rather than a list of
text. It is the closest thing radio has to a library: you cannot browse a
station's catalog, but you can browse what you actually heard.

Favorites are pinned into their own section at the top, as they were in
the old history list. Everything else groups by day.
"""

from datetime import date, datetime, timedelta

import math

from PyQt5.QtCore import QDateTime, QRectF, QTimer, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PyQt5.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLayout,
                             QScrollArea, QSizePolicy, QToolButton,
                             QVBoxLayout, QWidget)

import theme
from shell import Segmented, icon, rounded, search_field
from stationlist import monogram
import storage
from theme import T


# --------------------------------------------------------------------------
# Flow layout: tiles wrap to whatever width the window gives them
# --------------------------------------------------------------------------

class FlowLayout(QLayout):
    GAP = 16                    # the default spacing, named so callers can
                                # size a row of tiles without guessing


    def __init__(self, parent=None, margin=0, spacing=GAP):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Horizontal)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), test=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, test=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect, test):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            if line and x + w > area.right() + 1:
                x = area.x()
                y += line + self.spacing()
                line = 0
            if not test:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x += w + self.spacing()
            line = max(line, h)
        return y + line - rect.y() + m.bottom()


class FlowContainer(QWidget):
    """A widget that actually reports the height its flow layout needs.

    QVBoxLayout ignores heightForWidth unless the size policy opts in, and
    without it every section claims one row and the tiles wrap wrongly.
    """

    def __init__(self, spacing=18, parent=None):
        super().__init__(parent)
        self.flow = FlowLayout(self, margin=0, spacing=spacing)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def add(self, widget):
        self.flow.addWidget(widget)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.flow.heightForWidth(width)

    def sizeHint(self):
        w = self.width() or 600
        return QSize(w, self.flow.heightForWidth(w))

    def resizeEvent(self, e):
        # Only the flow layout's business. The toolbar wrapping that used
        # to be here belonged to HistoryPage and threw on every resize,
        # because a FlowContainer has no toolbar rows to move things
        # between — see the AttributeError in the log.
        super().resizeEvent(e)
        self.setMinimumHeight(self.flow.heightForWidth(e.size().width()))
        self.updateGeometry()


# --------------------------------------------------------------------------
# Tile
# --------------------------------------------------------------------------

class SongTile(QWidget):
    """One identified song. The star only appears on hover unless it is
    already set, so a wall of covers stays a wall of covers."""

    ART = 148

    favourited = pyqtSignal(str, bool)
    opened = pyqtSignal(dict)
    menu_requested = pyqtSignal(dict, QPoint)

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.setFixedWidth(self.ART)
        self.setAttribute(Qt.WA_Hover, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        holder = QWidget()
        holder.setFixedSize(self.ART, self.ART)
        self.art = QLabel(holder)
        self.art.setGeometry(0, 0, self.ART, self.ART)
        art = entry.get("art")
        if art and not art.isNull():
            art = art.scaled(self.ART, self.ART, Qt.KeepAspectRatioByExpanding,
                             Qt.SmoothTransformation)
        else:
            art = monogram(entry.get("song") or entry.get("station", "?"),
                           self.ART)
        self.art.setPixmap(rounded(art, 8))

        self.star = QToolButton(holder)
        self._forward_menu(self.star, entry)
        self.star.setCheckable(True)
        self.star.setChecked(bool(bool(entry.get("favorite"))))
        self.star.setGeometry(self.ART - 32, self.ART - 32, 26, 26)
        self.star.setToolTip("Favorite")
        self.star.setStyleSheet(
            "QToolButton { background: rgba(10,10,12,170); border: none;"
            " border-radius: 13px; }"
            "QToolButton:hover { background: rgba(10,10,12,220); }")
        self.star.toggled.connect(self._on_star)
        self._paint_star()
        self.star.setVisible(True)
        lay.addWidget(holder)

        self.title = QLabel(self._elide(entry.get("song") or "Unidentified"))
        self.title.setStyleSheet(
            f"color: {T['text']}; font-size: 12px; padding-top: 8px;")
        lay.addWidget(self.title)

        sub = entry.get("artist") or entry.get("station", "")
        self.sub = QLabel(self._elide(sub))
        self.sub.setStyleSheet(f"color: {T['text2']}; font-size: 11px;")
        lay.addWidget(self.sub)

        self.setToolTip("\n".join(x for x in [
            entry.get("song"), entry.get("artist"), entry.get("station"),
            self._when(entry.get("at"))] if x))

    def _elide(self, text):
        fm = QFontMetrics(QFont("", 9))
        return fm.elidedText(text or "", Qt.ElideRight, self.ART)

    @staticmethod
    def _when(ts):
        if not ts:
            return ""
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%d %B %Y at %H:%M")

    def _forward_menu(self, child, entry):
        _forward_menu_to(self, child, entry)

    def _paint_star(self):
        on = self.star.isChecked()
        self.star.setIcon(icon("star_on" if on else "star", 15,
                               T["accent"] if on else T["text"]))
        self.star.setIconSize(QSize(15, 15))

    def set_favourite(self, on):
        """Set the star from outside, without reporting it back.

        A change that came from the data should not be echoed to the code
        that made it — that is how a toggle ends up fighting itself.
        """
        if bool(on) == self.star.isChecked():
            return
        self.star.blockSignals(True)
        self.star.setChecked(bool(on))
        self.star.blockSignals(False)
        self._paint_star()
        self.star.setVisible(True)

    def _on_star(self, on):
        self._paint_star()
        self.entry["favorite"] = on
        self.star.setVisible(True)
        # The song's key, not the row's id.
        #
        # Favourites are matched by artist and title — an id matched
        # nothing, so the star was written to a record that did not
        # exist and the next refresh read the real entry back unstarred.
        # That is the star undoing itself a few seconds later.
        self.favourited.emit(storage.entry_key(self.entry), on)

    # No show-and-hide on hover.
    #
    # Toggling the star's visibility changed the row's layout under the
    # pointer: the star appeared, everything to its left shifted, and the
    # columns looked like they were overlapping as the mouse moved down
    # the list. The star is simply always there now.

    def mouseDoubleClickEvent(self, e):
        self.opened.emit(self.entry)


class SongRow(QWidget):
    """A history entry as a row. Once you are past the favorites, what you
    want is the run of what played -- time, song, artist, station -- and a
    column of covers only slows that down."""

    HEIGHT = 38

    favourited = pyqtSignal(str, bool)
    opened = pyqtSignal(dict)
    menu_requested = pyqtSignal(dict, QPoint)

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self._hover = False
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(14)

        when = QLabel(self._clock(entry.get("at")))
        when.setFixedWidth(46)
        when.setStyleSheet(f"color: {T['text3']}; font-size: 11px;")
        lay.addWidget(when)

        song = _ElidedLabel(entry.get("song") or "Unidentified", Qt.ElideRight)
        song.setStyleSheet(f"color: {T['text']}; font-size: 13px;")
        song.setToolTip(entry.get("song") or "")
        lay.addWidget(song, 4)

        artist = _ElidedLabel(entry.get("artist") or "", Qt.ElideRight)
        artist.setStyleSheet(f"color: {T['text2']}; font-size: 12px;")
        artist.setToolTip(entry.get("artist") or "")
        lay.addWidget(artist, 3)

        # Elided, not allowed to grow.
        #
        # A QLabel takes as much room as its text wants, so one long
        # station name pushed the artist column aside and the row looked
        # like its columns were sliding over each other. Every column now
        # keeps its share whatever is written in it, and text too long
        # for the space is cut with an ellipsis.
        station = _ElidedLabel(entry.get("station") or "", Qt.ElideRight)
        station.setStyleSheet(f"color: {T['text3']}; font-size: 11px;")
        station.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        station.setToolTip(entry.get("station") or "")
        lay.addWidget(station, 3)

        # Parented now: shown before it is laid out, a parentless button
        # flashes up as a window of its own on Windows.
        self.star = QToolButton(self)
        self._forward_menu(self.star, entry)
        self.star.setCheckable(True)
        self.star.setChecked(bool(bool(entry.get("favorite"))))
        self.star.setAutoRaise(True)
        self.star.setToolTip("Favorite")
        self.star.setIconSize(QSize(15, 15))
        self.star.toggled.connect(self._on_star)
        self._paint_star()
        # Always there, always the same width.
        #
        # It used to appear only when hovered or already set, so a star
        # arriving under the pointer pushed the station name aside — and
        # the columns shuffled as the mouse moved down the list. A
        # permanent slot costs one column of space and nothing moves.
        self.star.setVisible(True)
        self.star.setFixedWidth(26)
        lay.addWidget(self.star)

    @staticmethod
    def _clock(ts):
        import clock
        return clock.fmt(ts) if ts else ""

    def _forward_menu(self, child, entry):
        _forward_menu_to(self, child, entry)

    def _paint_star(self):
        on = self.star.isChecked()
        # Grey when it is not a favourite, so the slot reads as a
        # control rather than as an empty gap.
        self.star.setIcon(icon("star_on" if on else "star", 15,
                               T["accent"] if on else T["text3"]))

    def set_favourite(self, on):
        """From the data, not from a click — so it is not echoed back."""
        if bool(on) == self.star.isChecked():
            return
        self.star.blockSignals(True)
        self.star.setChecked(bool(on))
        self.star.blockSignals(False)
        self._paint_star()
        self.star.setVisible(True)

    def set_cover(self, art):
        """A thumbnail that has just arrived.

        One tile repainting itself, rather than the whole page being torn
        down and rebuilt — which is what every arriving thumbnail used to
        cause, one rebuild each.
        """
        if art is None or art.isNull():
            return
        self.entry["art"] = art
        scaled = art.scaled(self.ART, self.ART,
                            Qt.KeepAspectRatioByExpanding,
                            Qt.SmoothTransformation)
        self.art.setPixmap(rounded(scaled, 8))

    def _on_star(self, on):
        self._paint_star()
        self.entry["favorite"] = on
        self.star.setVisible(True)
        # The song's key, not the row's id.
        #
        # Favourites are matched by artist and title — an id matched
        # nothing, so the star was written to a record that did not
        # exist and the next refresh read the real entry back unstarred.
        # That is the star undoing itself a few seconds later.
        self.favourited.emit(storage.entry_key(self.entry), on)

    def enterEvent(self, e):
        # The highlight moves; the layout does not.
        #
        # Showing the star on hover shifted everything left of it, which
        # is what made the columns look like they were sliding over each
        # other as the pointer went down the list.
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.opened.emit(self.entry)

    def paintEvent(self, e):
        striped = getattr(self, "_striped", False)
        if not (self._hover or striped):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        # The hover, when there is one, replaces the stripe rather than
        # sitting on it — one shape per row, whatever state it is in.
        p.setBrush(QColor(T["bg2"] if self._hover else T["row_alt"]))
        p.drawRoundedRect(self.rect().adjusted(0, 1, 0, -1), 6, 6)


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------

class _ElidedLabel(QLabel):
    """A label that shrinks its text rather than its column.

    QLabel's own minimum is as wide as its text, so a long one wins every
    argument with the layout around it. This one asks for nothing and
    trims what it draws to whatever it is given.
    """

    def __init__(self, text="", mode=Qt.ElideRight, parent=None):
        super().__init__(text, parent)
        self._full = text or ""
        self._mode = mode
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setText(self, text):
        self._full = text or ""
        super().setText(self._full)
        self._trim()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._trim()

    def _trim(self):
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full, self._mode,
                                           max(10, self.width())))


def _forward_menu_to(row, child, entry):
    """Let a child hand its right-click back to the row.

    A button consumes the click before the row sees it, and the star
    sits at the end of every row — so right-clicking a favourited song
    did nothing, because the obvious place to aim is the very thing that
    shows it is favourited.
    """
    child.setContextMenuPolicy(Qt.CustomContextMenu)
    child.customContextMenuRequested.connect(
        lambda pos, c=child, r=row, e=entry: r.menu_requested.emit(
            e, c.mapToGlobal(pos)))


def _install_menu(widget, entry):
    """Right-click anywhere on a row or tile — including its star.

    A child widget consumes the click before the row sees it, and the
    star sits at the end of every row. Right-clicking a favourited song
    therefore did nothing, because the obvious place to aim is the thing
    that shows it is favourited.
    """
    widget.setContextMenuPolicy(Qt.CustomContextMenu)
    widget.customContextMenuRequested.connect(
        lambda pos, w=widget, e=entry: w.menu_requested.emit(
            e, w.mapToGlobal(pos)))

    for child in widget.findChildren(QWidget):
        child.setContextMenuPolicy(Qt.CustomContextMenu)
        child.customContextMenuRequested.connect(
            lambda pos, c=child, w=widget, e=entry: w.menu_requested.emit(
                e, c.mapToGlobal(pos)))


class Ellipsis(QWidget):
    """Three dots filling in turn, over a solid panel."""

    DOTS = 3
    # Finer steps, and a curve rather than a raw sine.
    #
    # 60ms is sixteen frames a second, which reads as stepping rather
    # than moving. 16ms is the screen's own rate; the cycle is kept the
    # same length by counting more of them. The brightness is shaped
    # like KWin's Scale — ease out, so it arrives softly instead of
    # sweeping past at constant speed.
    INTERVAL = 16
    PERIOD = 98

    def __init__(self, parent=None):
        super().__init__(parent)
        self._step = 0
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL)
        self._timer.timeout.connect(self._tick)
        self.setVisible(False)

    def start(self):
        self.setVisible(True)
        self._step = 0
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.setVisible(False)

    def _tick(self):
        self._step = (self._step + 1) % self.PERIOD
        self.update()

    def paintEvent(self, _e):
        from PyQt5.QtGui import QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(T["bg0"]))
        p.setPen(Qt.NoPen)
        base, gap = 6.0, 24.0
        left = self.width() / 2 - gap
        for i in range(self.DOTS):
            phase = (self._step / float(self.PERIOD)) - i * 0.18
            raw = 0.5 + 0.5 * math.sin(phase * 2.0 * math.pi)
            # Smoothstep, not a two-sided curve.
            #
            # Easing each half separately put a jump at the middle — the
            # value leapt from 0.50 to 0.88 as it crossed. This one eases
            # both ends with a single continuous expression, so the pulse
            # settles at the top and bottom and has no seam anywhere.
            glow = raw * raw * (3.0 - 2.0 * raw)
            colour = QColor(T["text2"])
            colour.setAlpha(int(45 + 200 * glow ** 1.6))
            p.setBrush(colour)
            radius = base * (0.85 + 0.25 * glow)
            p.drawEllipse(QRectF(left + i * gap - radius,
                                 self.height() / 2 - radius,
                                 radius * 2, radius * 2))
        p.end()


class HistoryPage(QWidget):
    menu_requested = pyqtSignal(dict, QPoint)


    favourited = pyqtSignal(str, bool)
    opened = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_toolbar())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # A minimum wide enough for one tile and no more.
        #
        # The page inherited its width from its widest section, so with the
        # panels open the tiles simply carried on underneath them instead
        # of wrapping. Letting the page shrink to a single column is what
        # makes the flow layout do its job.
        # Three tiles across, always.
        #
        # One tile was enough to make the flow layout work, but a history
        # of favourites one column wide is a list with pictures — the
        # point of the wall is comparing them, and three is the fewest
        # that still reads as a wall.
        # Three tiles, their gaps and the scrollbar — measured rather than
        # guessed, because at 64px of slack the third tile wrapped by a
        # couple of pixels at the narrowest the window would go.
        self.scroll.setMinimumWidth(
            SongTile.ART * 3 + FlowLayout.GAP * 2 + 32 + 16)
        self._inner = QWidget()
        self._inner.setMinimumWidth(
            SongTile.ART * 3 + FlowLayout.GAP * 2 + 32)
        self._body = QVBoxLayout(self._inner)
        self._body.setContentsMargins(16, 4, 16, 24)
        self._body.setSpacing(0)
        self.scroll.setWidget(self._inner)
        outer.addWidget(self.scroll, 1)

    def _build_toolbar(self):
        bar = QWidget()
        # Two rows when there is not room for one.
        #
        # A horizontal layout has no idea it is out of space: it keeps
        # handing out negative widths and the widgets end up on top of one
        # another. The second row is only used when the first cannot fit.
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(16, 14, 16, 10)
        lay.setSpacing(8)
        self._toolbar_rows = lay

        # The search box does the work here, so it gets the room: capped
        # at 300 it was the same width as a dropdown holding one word.
        self.search = search_field("Songs, artists, stations")
        # A floor rather than a demand: below this the toolbar wraps
        # instead of the controls sliding over each other.
        self.search.setMinimumWidth(150)
        self.search.textChanged.connect(lambda _t: self._rebuild())
        self._row_one = QHBoxLayout()
        self._row_one.setSpacing(10)
        self._row_two = QHBoxLayout()
        self._row_two.setSpacing(10)
        lay.addLayout(self._row_one)
        lay.addLayout(self._row_two)
        self._row_one.addWidget(self.search, 1)

        self.scope = Segmented(["Everything", "Favorites"])
        self.scope.changed.connect(lambda _i: self._rebuild())
        self._row_one.addWidget(self.scope)

        self.station = QComboBox()
        self.station.addItem("All stations")
        # Long station names would otherwise stretch this to half the bar;
        # the popup is free to be as wide as it likes.
        self.station.setFixedWidth(150)
        self.station.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLength)
        self.station.view().setMinimumWidth(260)
        self.station.currentIndexChanged.connect(lambda _i: self._rebuild())
        self._row_one.addWidget(self.station)

        self.count = QLabel("")
        self.count.setStyleSheet(f"color: {T['text2']}; font-size: 11px;")
        self._row_one.addWidget(self.count)
        return bar

    # -- data --------------------------------------------------------------

    def set_entries(self, entries):
        """New history. Rebuilt now only if anyone can see it.

        This page is rebuilt from every identified song and from every
        thumbnail that lands — and rebuilding means destroying and
        recreating a widget per row. Doing that for a page on another tab
        cost seconds of frozen interface for something nobody was looking
        at. Marked instead, and rebuilt when it is next shown.
        """
        """entries: [{id, song, artist, station, at (epoch seconds),
                      art (QPixmap|None), favourite, url}]"""
        self._entries = sorted(entries, key=lambda e: e.get("at", 0),
                               reverse=True)

        current = self.station.currentText()
        stations = sorted({e.get("station", "") for e in self._entries if
                           e.get("station")})
        self.station.blockSignals(True)
        self.station.clear()
        self.station.addItem("All stations")
        self.station.addItems(stations)
        i = self.station.findText(current)
        self.station.setCurrentIndex(max(0, i))
        self.station.blockSignals(False)
        if self.isVisible():
            self._rebuild()
        else:
            self._dirty = True

    def cover_arrived(self, track_key, art):
        """Hand a newly fetched cover to the tiles that want it.

        Matched on the artist/title key, which is what a history entry is
        identified by — the same song can appear more than once, and all
        of them should show the picture. The page is not rebuilt: a
        thumbnail changes a picture, not the list.
        """
        found = False
        for widget in self.findChildren(SongTile):
            entry = getattr(widget, "entry", None)
            if entry and storage.entry_key(entry) == track_key:
                widget.set_cover(art)
                found = True
        # Remembered either way, so a later rebuild already has it.
        for entry in self._entries:
            if storage.entry_key(entry) == track_key:
                entry["art"] = art
        return found

    def showEvent(self, e):
        super().showEvent(e)
        if getattr(self, "_dirty", False):
            self._dirty = False
            self._rebuild()

    def _matches(self, e):
        if self.scope.index() == 1 and not bool(e.get("favorite")):
            return False
        chosen = self.station.currentText()
        if self.station.currentIndex() > 0 and e.get("station") != chosen:
            return False
        text = self.search.text().strip().lower()
        if not text:
            return True
        hay = " ".join(str(e.get(k) or "") for k in
                       ("song", "artist", "station")).lower()
        return text in hay

    # -- rendering ---------------------------------------------------------

    def _signature(self):
        """What the visible list is, cheaply — which songs and whether
        they are starred. Anything else that changes does not change which
        widgets are needed."""
        return tuple((storage.entry_key(e), bool(bool(e.get("favorite"))))
                     for e in self._entries if self._matches(e))

    def _mark_rows(self):
        """Same rows, possibly different stars."""
        state = {storage.entry_key(e): bool(bool(e.get("favorite")))
                 for e in self._entries}
        for widget in self.findChildren(SongRow) + self.findChildren(SongTile):
            entry = getattr(widget, "entry", None)
            key = storage.entry_key(entry) if entry else None
            if key in state:
                widget.set_favourite(state[key])

    def _clear(self):
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w:
                w.hide()            # or Qt's queued show makes it a window (task 148)
                w.setParent(None)

    def show_loading(self):
        """A plain panel with an ellipsis while the list is built.

        Rebuilding a long history takes long enough to see, and a page
        half-populating itself looks like a fault. One solid surface and
        three dots says "working" without pretending to be finished.
        """
        if getattr(self, "_loading", None) is None:
            self._loading = Ellipsis(self)
            self._loading.setAttribute(Qt.WA_StyledBackground, True)
            self._loading.setStyleSheet(f"background: {T['bg0']};")
        self._loading.setGeometry(self.scroll.geometry())
        self._loading.raise_()
        self._loading.start()

    def hide_loading(self):
        if getattr(self, "_loading", None) is not None:
            self._loading.stop()

    def _rebuild(self):
        # The rows that are already right are left alone.
        #
        # A rebuild used to destroy every row and construct it again, so a
        # history of two hundred songs meant two hundred widgets built on
        # the GUI thread for a list that had gained one entry at the top.
        signature = self._signature()
        if signature == getattr(self, "_built", None):
            self._mark_rows()
            return
        self._built = signature
        self._clear()
        rows = [e for e in self._entries if self._matches(e)]
        self.count.setText(f"{len(rows)} song{'' if len(rows) == 1 else 's'}")

        if not rows:
            empty = QLabel("Nothing here yet. Songs land here as they are "
                           "identified.")
            empty.setStyleSheet(
                f"color: {T['text2']}; font-size: 13px; padding: 40px 4px;")
            self._body.addWidget(empty)
            self._body.addStretch(1)
            return

        # Favourites keep their pinned section, but only when they would
        # otherwise be scattered through the days.
        favourites = [e for e in rows if bool(e.get("favorite"))]
        if favourites and self.scope.index() == 0:
            self._section("Favorites", favourites, tiles=True)
            rows = [e for e in rows if not bool(e.get("favorite"))]
        elif self.scope.index() == 1:
            self._section("Favorites", rows, tiles=True)
            rows = []

        groups = {}
        for e in rows:
            groups.setdefault(self._day_label(e.get("at", 0)), []).append(e)
        for label, items in groups.items():
            self._section(label, items, tiles=False)

        self._body.addStretch(1)

    WRAP_AT = 720

    def _lay_toolbar(self):
        """Move the filters onto their own row when the bar gets narrow."""
        wrapped = self.width() < self.WRAP_AT
        if wrapped == getattr(self, "_wrapped", None):
            return
        self._wrapped = wrapped
        source = self._row_one if wrapped else self._row_two
        target = self._row_two if wrapped else self._row_one
        for widget in (self.scope, self.station):
            source.removeWidget(widget)
            target.addWidget(widget)
        if wrapped:
            target.addStretch(1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._lay_toolbar()
        # The flow layout only rewraps when something asks it to.
        for holder in self.findChildren(FlowContainer):
            holder.setMinimumHeight(
                holder.flow.heightForWidth(self.scroll.viewport().width() - 32))
            holder.updateGeometry()

    def _section(self, title, entries, tiles):
        head = QLabel(title)
        head.setStyleSheet(
            f"color: {T['text3']}; font-size: 11px; padding: 18px 2px 8px 2px;")
        self._body.addWidget(head)

        if tiles:
            holder = FlowContainer()
            for e in entries:
                w = SongTile(e)
                # The menu, which tiles never had.
                #
                # Favourites are drawn as tiles and everything else as
                # rows, and only the rows were given a right-click menu —
                # so the songs you cared most about were the only ones you
                # could not act on. The earlier fix forwarded the star's
                # click to its parent, which was right, but the tile it
                # forwarded to was connected to nothing.
                _install_menu(w, e)
                w.menu_requested.connect(self.menu_requested.emit)
                w.favourited.connect(self._on_fav)
                w.opened.connect(self.opened.emit)
                holder.add(w)
            self._body.addWidget(holder)
            return

        holder = QWidget()
        rows = QVBoxLayout(holder)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(0)
        for index, e in enumerate(entries):
            w = SongRow(e)
            _install_menu(w, e)
            w.menu_requested.connect(self.menu_requested.emit)
            # Alternating, the way the station list is: a long history is
            # four columns of small text, and a stripe is what lets the eye
            # carry a row across it.
            #
            # Painted by the row, not set as a stylesheet. A bare
            # "background:" rule on the row is inherited by every label
            # and button inside it, so each column drew its own box of the
            # stripe colour — boxes that sat on top of the hover highlight
            # and looked like the columns were overlapping.
            w._striped = bool(index % 2)
            w.favourited.connect(self._on_fav)
            w.opened.connect(self.opened.emit)
            rows.addWidget(w)
        self._body.addWidget(holder)

    def _on_fav(self, entry_id, on):
        for e in self._entries:
            if e["id"] == entry_id:
                e["favorite"] = on
                break
        self.favourited.emit(entry_id, on)

    @staticmethod
    def _day_label(ts):
        if not ts:
            return "Earlier"
        d = datetime.fromtimestamp(ts).date()
        today = date.today()
        if d == today:
            return "Today"
        if d == today - timedelta(days=1):
            return "Yesterday"
        if (today - d).days < 7:
            return d.strftime("%A")
        if d.year == today.year:
            return d.strftime("%d %B")
        return d.strftime("%d %B %Y")
