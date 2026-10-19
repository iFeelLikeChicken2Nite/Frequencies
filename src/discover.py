"""Frequencies — the Discover and Search destinations.

Two pages that were empty sidebar entries until now.

``DiscoverPage``  radio-browser.info, as a page rather than a modal — plus the
                 two hand-made routes, since this is the "get more stations"
                 screen and the directory is only one of three ways in. It
                 reuses
                 dialogs._SearchWorker, _FeaturedWorker and _FilterListWorker
                 verbatim — those are already correct and already off the GUI
                 thread (jobs.Job daemon threads since build 46, so quitting
                 mid-search can't abort the process). What is
                 new is only the presentation: results are painted by the same
                 StationDelegate the library uses, so a station looks the same
                 before and after you add it.

``SearchPage``   one field over your own library, scoped into sections. The
                 old app had search in the toolbar and browse in a dialog;
                 this is the "unify search" idea, with your stations answered
                 instantly from memory and radio-browser offered underneath.

Both emit `station_added`, which FrequenciesWindow turns into a real station
via storage's own normalisation.
"""

from PyQt5.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QFontMetrics
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel,
                             QSizePolicy,
                             QListView, QMenu, QPushButton, QToolButton,
                             QVBoxLayout, QWidget)

import radiobrowser
import storage
from dialogs import (PAGE_SIZE, _FeaturedWorker, _FilterListWorker,
                     _SearchWorker)
from shell import icon, open_menu, search_field, tool_button, with_badge
from theme import T
from stationlist import StationList


# A featured tile at two-thirds size: recognisable, without the strip
# claiming a third of the page.
FEATURED_TILE = 100
# Tall enough for the whole tile.
#
# A tile is the artwork plus a 46px caption, and the strip has a
# horizontal scrollbar eating the bottom of it. At +60 the last few
# pixels of every tile were cut off, which showed as a hover highlight
# that stopped short of the name.
FEATURED_H = FEATURED_TILE + 46 + 34


SOURCE_HINTS = {
    "search": "Search radio-browser: jazz, BBC, lofi…",
    "local": "Choose a folder of your own music…",
    "url": "Paste a stream URL and press Return",
    "youtube": "Paste a YouTube playlist URL and press Return",
    "ibroadcast": "Signing in to iBroadcast…",
    "twitch": "A Twitch channel name or URL, then Return — audio only",
    "shared": "Open pictures of shared station codes",
    "sniffer": "Open a web page and watch what audio it requests…",
}

# These open a window instead of waiting for something to be typed. There
# is nothing to type for a folder, a web page, or an account you have to
# sign into — asking for Return first was a step that led nowhere.
# iBroadcast is not here: its tile opens a menu instead, because there
# are several things you might mean by clicking it. Listing it as an
# action too made a click do both — the menu appeared and the
# add-a-playlist dialog opened behind it.
ACTIONS = ("local", "sniffer", "shared")

# Four and four, in this order.
#
# The top row is the ways a station arrives on its own — an address, a
# search, something handed to you, something found. The bottom row is
# the places you already have an account or a folder at.
SOURCES = (("url", "link", "URL", "Paste a stream URL"),
           ("search", "search", "Search", "Search the directory"),
           ("shared", "shared", "Shared",
            "Pictures of station codes somebody sent you"),
           ("sniffer", "sniffer", "Sniffer", "Find a stream on a web page"),
           ("local", "folder", "Local", "A folder of your own music"),
           ("youtube", "youtube", "YouTube", "A YouTube playlist"),
           ("twitch", "twitch", "Twitch", "A Twitch channel, audio only"),
           ("ibroadcast", "ibroadcast", "iBroadcast", "An iBroadcast stream"))


class SourceRow(QWidget):
    """The six ways a station can arrive, as one row of toggles."""

    chosen = pyqtSignal(str)
    menu_requested = pyqtSignal(str, object)    # key, global position

    def __init__(self, parent=None, big=False):
        super().__init__(parent)
        self._connected = set()
        self._big = big
        self._size = 34 if big else 20
        from PyQt5.QtWidgets import QGridLayout
        row = QGridLayout(self) if big else QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12 if big else 2)
        self._columns = 4
        self._buttons = {}
        self._current = ""
        for key, glyph, label, tip in SOURCES:
            # Labelled, and only as tall as it needs to be: six unlabelled
            # squares make you hover each one to find out what it does,
            # and the space they were taking belongs to the results.
            b = QToolButton()
            b.setIcon(icon(glyph, self._size, T["text2"]))
            b.setIconSize(QSize(self._size, self._size))
            b.setText(label)
            b.setToolTip(tip)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setCheckable(True)
            b.setAutoRaise(True)
            b.setProperty("source", True)
            b.setProperty("big", big)
            # Wide enough for the word underneath. Left to itself the
            # button sizes to the icon and elides the label to "S...h",
            # which defeats the point of labelling them.
            # Measured with the size the stylesheet will actually draw.
            #
            # fontMetrics() reports the widget's own font, which QSS then
            # overrides — so the button was sized for 13px text and drew
            # 11px labels wider than the space reserved, and neighbours
            # ran into each other.
            metric_font = QFont(b.font())
            metric_font.setPixelSize(13 if big else 11)
            width = QFontMetrics(metric_font).horizontalAdvance(label)
            if big:
                # Blocks of one size, laid out five across and wrapping —
                # a single row of nine crowded them together and their
                # borders overlapped the moment the window narrowed.
                b.setMinimumSize(max(120, width + 46), 96)
            else:
                b.setMinimumWidth(width + 40)
            b.setChecked(False)
            b.clicked.connect(lambda _c, k=key: self.choose(k))
            if big:
                index = len(self._buttons)
                row.addWidget(b, index // self._columns,
                              index % self._columns)
                # Never smaller than they were laid out to be: a grid will
                # happily crush its cells to fit, and these overlapped
                # into each other as the window narrowed.
                b.setMinimumSize(b.minimumWidth(), 96)
                b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            else:
                row.addWidget(b)
            # Left click opens it too, for the tiles that have one.
            #
            # A menu you can only reach by right-clicking is a menu most
            # people never find. iBroadcast's tile does more than "start
            # adding", so the plain click asks what you want.
            if key == "ibroadcast":
                b.clicked.connect(
                    lambda _c=False, k=key, w=b: self.menu_requested.emit(
                        k, w.mapToGlobal(w.rect().bottomLeft())))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda pos, k=key, w=b: self.menu_requested.emit(
                    k, w.mapToGlobal(pos)))
            self._buttons[key] = b
        if not big:
            row.addStretch(1)

    def current(self):
        return self._current

    def set_columns(self, columns):
        """Re-lay the cards across a different number of columns."""
        if columns == self._columns:
            return
        self._columns = columns
        layout = self.layout()
        widgets = list(self._buttons.values())
        for widget in widgets:
            layout.removeWidget(widget)
        for index, widget in enumerate(widgets):
            layout.addWidget(widget, index // columns, index % columns)

    def clear_choice(self):
        self._current = ""
        for button in self._buttons.values():
            button.setChecked(False)
        self._paint()

    def set_connected(self, keys):
        """Which services are signed in.

        A row of identical icons cannot say which of them will actually do
        something — a tick in the corner is the difference between "we
        support this" and "this is yours".
        """
        keys = set(keys or ())
        if keys == self._connected:
            return
        self._connected = keys
        self._paint()

    def _paint(self):
        for name, button in self._buttons.items():
            on = name == self._current
            glyph = next(g for k, g, _l, _t in SOURCES if k == name)
            colour = T["accent"] if on else T["text2"]
            base = icon(glyph, self._size, colour)
            button.setIcon(with_badge(
                base, self._size, colour,
                T["playing"] if name in self._connected else None))
            label = next(l for k, _g, l, _t in SOURCES if k == name)
            tip = next(t for k, _g, _l, t in SOURCES if k == name)
            button.setToolTip(f"{tip} · signed in"
                              if name in self._connected else tip)

    def choose(self, key, announce=True):
        # The two that open a picker are actions, not modes: leaving one
        # of them latched would say the bar is for folders when it is not.
        sticky = key not in ACTIONS
        if sticky:
            self._current = key
        for name, button in self._buttons.items():
            button.setChecked(sticky and name == self._current)
        self._paint()
        if announce:
            self.chosen.emit(key)


def _featured_art(entry):
    """Write the entry's embedded artwork out once, and reuse it.

    The tile wants something a pixmap can be built from, and the blob is
    base64 in a JSON file — so it is decoded to the cache directory the
    first time and referenced by path after that.
    """
    import base64
    import hashlib
    import os

    raw = entry.get("settings") if isinstance(entry.get("settings"), dict) \
        else entry
    blob = raw.get("art_data")
    if not blob:
        return ""

    folder = os.path.join(os.path.dirname(storage.settings_path()),
                          "featured", "art")
    name = hashlib.sha1(blob[:512].encode("utf-8", "replace")).hexdigest()[:16]
    path = os.path.join(folder, name + ".png")
    if os.path.exists(path):
        return path
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(blob))
    except Exception:                           # noqa: BLE001
        return ""
    return path


def _row_from_result(result, index):
    """A radio-browser result in the shape the station list paints."""
    bits = [result.get("country") or ""]
    if result.get("bitrate"):
        bits.append(f"{result['bitrate']}k")
    if result.get("codec"):
        bits.append(result["codec"])
    if result.get("distance_km") is not None:
        bits.append(f"{int(result['distance_km'])} km")
    return {
        "id": result.get("uuid") or f"rb{index}",
        "name": result.get("name", "Unnamed"),
        "location": " · ".join(b for b in bits if b),
        "logo": result.get("favicon") or "",
        "tags": [],
        "url": result.get("url", ""),
        "_result": result,
    }


class DiscoverPage(QWidget):
    """radio-browser.info as a first-class destination."""

    station_added = pyqtSignal(dict)        # a normalised station record
    source_requested = pyqtSignal(str, str)
    source_menu = pyqtSignal(str, object)
    share_requested = pyqtSignal(dict)       # key, global position  # source key, the bar's text
    station_removed = pyqtSignal(str)       # url of one to drop
    preview_requested = pyqtSignal(dict)    # play it without adding it

    def __init__(self, near=None, parent=None):
        super().__init__(parent)
        self._near = near
        # Set by the Trending and Random buttons, cleared once used.
        self._browse_mode = ""
        self._page = 0
        self._worker = None
        self._retired = []
        self._results = []
        self._owned = set()
        self._featured_worker = None
        self._filter_worker = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 8, 8)
        lay.setSpacing(10)

        # One bar, six things that can fill it.
        #
        # Every way of getting a station ends with a line of text — a
        # search phrase, a folder, a URL, a playlist link, a page to sniff
        # — so they share the field and the icons say what the field means
        # this time. Six separate boxes would have said the same thing
        # five more times than necessary.
        self.query = search_field("Search radio-browser: jazz, BBC, lofi…")
        self.query.returnPressed.connect(self._submit)
        lay.addWidget(self.query)

        # The source row lives where the results go, until a search is
        # actually running. Six large buttons on an empty page say what
        # this screen is for; a strip of small ones above an empty list
        # says the list is the point and the buttons are a footnote.
        self.sources = SourceRow(big=True)
        self.sources.chosen.connect(self._source_chosen)
        self.sources.menu_requested.connect(self.source_menu.emit)

        self.chooser = QWidget()
        chooser_lay = QVBoxLayout(self.chooser)
        chooser_lay.setContentsMargins(0, 40, 0, 0)
        chooser_lay.setSpacing(18)
        chooser_lay.addStretch(1)
        chooser_lay.addWidget(self.sources, 0, Qt.AlignCenter)
        chooser_lay.addStretch(2)
        # Featured is filled in below, once the strip exists: it lives at
        # the foot of this screen rather than under the search results.
        # It is a standing suggestion for someone who has not decided
        # where to look yet — which is exactly who is on this screen.
        self._chooser_lay = chooser_lay

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.sort = QComboBox()
        for value, label in radiobrowser.SORT_MODES:
            self.sort.addItem(label, value)
        self.sort.currentIndexChanged.connect(self.search)

        # No language filter.
        #
        # It defaulted to English and quietly hid most of what the
        # directory holds, which is the opposite of what a worldwide
        # station list is for. Searching now searches everything.


        # Two ways of asking "show me something", each with a button.
        #
        # They used to be entries in the sort dropdown, which made them
        # look like orderings for a search you had already typed. They
        # are not: they are the search.
        # Back first, then the order, then the two browsing buttons.
        #
        # Removing "Near me" took out everything between its first and
        # last line, and the lines that placed the back button and this
        # dropdown were in between — so the search was left with no way
        # out and no way to sort. Rebuilt here, deliberately, rather than
        # relying on where they used to sit.
        from shell import icon as _icon
        self.back_btn = QPushButton("  Back")
        self.back_btn.setIcon(_icon("back", 16, T["text2"]))
        self.back_btn.clicked.connect(self.show_chooser)
        filters.addWidget(self.back_btn)

        filters.addWidget(self.sort)
        filters.addStretch(1)

        self.trending_btn = QPushButton("Trending")
        self.trending_btn.clicked.connect(
            lambda: self._browse_by("clicktrend"))
        filters.addWidget(self.trending_btn)

        self.random_btn = QPushButton("Random")
        self.random_btn.clicked.connect(lambda: self._browse_by("random"))
        filters.addWidget(self.random_btn)

        self.filter_row = QWidget()
        self.filter_row.setLayout(filters)
        lay.addWidget(self.filter_row)

        # No "Results" heading: what is underneath is plainly the
        # results, and the word was a row of furniture saying so.
        # Kept as an attribute only because older code may still reach
        # for it — but it is parented and never shown.
        #
        # When the heading was removed it was taken out of the layout and
        # left with no parent, while _set_browsing still set it visible
        # on every search. A parentless widget that is shown becomes a
        # window of its own, so every search, page, Trending and Random
        # opened an empty window that had to be closed by hand.
        self.results_head = QLabel("", self)
        self.results_head.setVisible(False)
        lay.addWidget(self.chooser, 1)

        self.list = StationList()
        # Double-click plays it. Adding is the + on the right, so you can
        # listen to something before deciding whether to keep it — which is
        # the whole point of browsing a directory.
        self.list.activated_station.connect(self._preview)
        self.list.action_clicked.connect(self._toggle)
        self.list.context_requested.connect(self._row_menu)
        self.list.delegate.show_actions = True
        lay.addWidget(self.list, 1)

        foot = QHBoxLayout()
        self.status = QLabel("")

        # The waiting indicator, centred over where results will appear.
        from historypage import Ellipsis
        self.searching = Ellipsis()
        self.searching.setVisible(False)
        self.status.setProperty("role", "secondary")
        self.prev_btn = QPushButton("Previous")
        self.next_btn = QPushButton("More")
        self.prev_btn.clicked.connect(lambda: self._turn(-1))
        self.next_btn.clicked.connect(lambda: self._turn(1))
        for b in (self.prev_btn, self.next_btn):
            b.setVisible(False)
        foot.addWidget(self.prev_btn)
        foot.addWidget(self.status, 1)
        foot.addWidget(self.searching, 0, Qt.AlignHCenter)
        foot.addWidget(self.next_btn)
        lay.addLayout(foot)

        # Under the source buttons, on the screen you arrive at.
        #
        # It used to sit beneath the search results, where it competed
        # with them for height and was only visible once you had already
        # chosen a source — by which point a suggestion is beside the
        # point.
        self.featured_head = QLabel("Curated frequencies · changes daily")
        self.featured_head.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.featured_head.setProperty("role", "section")

        # One row that scrolls sideways, not a grid: featured is a strip
        # along the bottom, and wrapping turned it into a second list
        # competing with the results for height.
        self.featured_list = StationList(tile=FEATURED_TILE)
        self.featured_list.set_view_mode(True)          # artwork tiles
        self.featured_list.setWrapping(False)
        self.featured_list.setFlow(QListView.LeftToRight)
        # Centred when the tiles fit, left-aligned when they do not.
        #
        # Centring is only right while there is spare room. Once the
        # strip is scrolling there is no spare room to share, and
        # centring a scrolling list means the first tile starts
        # off-screen — so the alignment follows the width rather than
        # being set once.
        self.featured_list.setResizeMode(QListView.Adjust)
        # No bar when everything fits.
        #
        # The strip is sized to its tiles when there is room, so a bar
        # under a row that cannot scroll is furniture for nothing.
        self.featured_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.featured_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # A wheel turned over a one-row strip should move it.
        #
        # The row scrolls sideways and a mouse wheel scrolls vertically,
        # so the natural gesture did nothing at all — you had to find the
        # bar. The vertical wheel is handed to the horizontal bar.
        self.featured_list.setFixedHeight(FEATURED_H)
        self.featured_list.activated_station.connect(self._preview)
        self.featured_list.clicked_station.connect(self._featured_card)
        self.featured_list.context_requested.connect(self._featured_menu)

        # Empty, not "Loading…".
        #
        # The strip fills in on its own within a second, so the word was
        # on screen just long enough to be noticed and never long enough
        # to be useful — and it stayed visible under stations that had
        # plainly finished loading. It still speaks when there is
        # something to say, which is when nothing came back.
        self.featured_note = QLabel("")
        self.featured_note.setProperty("role", "secondary")

        # All three into the chooser, at the bottom, under the buttons.
        self._chooser_lay.addWidget(self.featured_head)
        # Centred by the layout, not by margins inside the view.
        #
        # setViewportMargins was the wrong tool: an item view lays its
        # items out from the viewport's own origin, so the margin moved
        # the viewport and the tiles came straight back to the left edge.
        # A stretch either side of a strip that is only as wide as its
        # tiles centres it the ordinary way.
        strip = QHBoxLayout()
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(0)
        strip.addStretch(1)
        strip.addWidget(self.featured_list)
        strip.addStretch(1)
        self._chooser_lay.addLayout(strip)
        self.featured_note.setVisible(False)
        self._chooser_lay.addWidget(self.featured_note)

        self._featured = []
        fw = _FeaturedWorker(self._featured_cache(), self)
        fw.ready.connect(self._on_featured)
        fw.start()
        self._featured_worker = fw

        lw = _FilterListWorker(self)
        lw.ready.connect(self._on_filters)
        lw.start()
        self._filter_worker = lw

        # The screen opens as the chooser. Without this the results list
        # and its filters were on screen from the start, empty, under the
        # very buttons that decide whether they are wanted.
        self._set_browsing(False)

    def shutdown(self):
        """Nothing to wait for any more. The search and list lookups are
        jobs.Job daemon threads (build 46): a QThread still running here
        used to abort the app on quit, and a 1.5 s wait each only
        sometimes saved it. mainwindow.shutdown stops all Jobs first."""

    @staticmethod
    def _featured_cache():
        import os
        return os.path.join(
            os.path.dirname(storage.settings_path()), "featured")

    # -- searching ---------------------------------------------------------

    # -- where a station comes from ----------------------------------------

    def show_chooser(self):
        """Back to picking a source: nothing lit, nothing assumed.

        Search used to be selected on arrival, which quietly answered the
        question the page exists to ask.
        """
        self.sources.clear_choice()
        self._set_browsing(False)

    def _set_browsing(self, on):
        """Directory furniture belongs to the directory.

        Sort order only means anything for a radio-browser search —
        shown beside a folder picker it is a control that does nothing.
        """
        self.filter_row.setVisible(on)
        # (the old results heading stays hidden — see where it is made)
        self.list.setVisible(on)
        # Featured belongs to the chooser, so it is shown when the
        # chooser is — the opposite of the results, filters and status.
        for widget in (self.featured_head, self.featured_list,
                       self.featured_note):
            widget.setVisible(not on)
        self.status.setVisible(on)
        self.chooser.setVisible(not on)

        # Paging belongs to the results, so it goes with them.
        #
        # "More" was shown when a full page of results arrived and never
        # hidden again, so it sat at the bottom of the Add Frequency
        # screen afterwards — a button with nothing behind it, because
        # the results it would page through were no longer on screen.
        if not on:
            self.prev_btn.setVisible(False)
            self.next_btn.setVisible(False)
            self.searching.stop()

    COLUMN_W = 132              # a card plus its gap

    def _reflow_sources(self):
        """Always two rows, however wide the window is.

        Eight sources reflowing freely gave three rows, then two, then
        four — a different shape every time the window moved. Two rows of
        four is the arrangement; narrower means two rows of fewer, never
        a third row.
        """
        # Half of them, always.
        #
        # Letting the width decide gave three rows on a narrow window and
        # two on a wide one — the shape of the screen changed as you
        # dragged it. Two rows is the arrangement, so the column count is
        # simply half the sources; the cards have a small enough minimum
        # to fit four across the narrowest the window goes.
        self.sources.set_columns((len(SOURCES) + 1) // 2)

    def _centre_featured(self):
        """Share any spare width evenly on both sides of the tiles.

        Centring is only right while there is room to spare. Once the
        strip is long enough to scroll there is none, and centring a
        scrolling list pushes its first tile off the left edge — so the
        padding is recomputed from the width rather than set once.
        """
        view = getattr(self, "featured_list", None)
        model = view.model() if view is not None else None
        if model is None or not model.rowCount():
            return
        # Measured from the items themselves, not from the tile constant.
        #
        # FEATURED_TILE is the artwork's size, not the width a delegate
        # ends up giving a tile — padding, the name underneath and the
        # spacing all add to it. Guessing that number meant the padding
        # was computed against a width the strip never had, so it was
        # never actually centred.
        first = view.visualRect(model.index(0, 0))
        last = view.visualRect(model.index(model.rowCount() - 1, 0))
        if not first.isValid() or first.width() <= 0:
            return
        if model.rowCount() == 1:
            needed = first.width()
        else:
            # Right edge of the last tile minus the left edge of the
            # first, in the view's own coordinates, plus whatever the
            # scroll has already taken off the front.
            offset = view.horizontalScrollBar().value()
            needed = (last.right() + offset) - (first.left() + offset) + 1
        # Only as wide as it needs to be, so the stretches can share out
        # what is left. Wider than the room available, it fills the room
        # and scrolls, which is when centring stops making sense.
        room = self.width() - 28
        wanted = min(needed + 2, max(120, room))
        if getattr(self, "_featured_width", None) == wanted:
            return
        self._featured_width = wanted
        view.setFixedWidth(wanted)
        view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff if wanted >= needed
            else Qt.ScrollBarAsNeeded)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._centre_featured()
        self._reflow_sources()

    def _featured_menu(self, row, pos):
        """The three things worth doing to a featured station."""
        menu = QMenu(self)
        menu.addAction("Play", lambda: self._preview(row))
        menu.addSeparator()
        if self.is_owned(row):
            menu.addAction("Remove from my stations",
                           lambda: self.station_removed.emit(
                               row.get("url") or ""))
        else:
            menu.addAction("Add to my stations", lambda: self._add(row))
        menu.addAction("Share…", lambda: self.share_requested.emit(
            self._as_station(row)))
        open_menu(menu, pos)

    def _featured_card(self, station, point):
        """A card about the station, closed by clicking anywhere else."""
        from stationcard import StationCard
        # Windows stack rather than replace: two stations side by side is
        # a comparison, which is what this list is for.
        cards = getattr(self, "_cards", [])
        cards = [c for c in cards if c.isVisible()]
        self._cards = cards
        try:
            card = StationCard(station, self)
        except Exception as exc:                # noqa: BLE001
            # A card that cannot be built must say so.
            #
            # This runs from a signal, and an exception in a signal
            # handler is swallowed with nothing on screen — so a station
            # whose entry is missing a field the card expects simply did
            # not open, while its neighbours did. That is the scattered
            # "some of them do nothing" pattern.
            try:
                import crashlog
                crashlog.write(f"[card] could not build one: {exc!r} "
                               f"for {station.get('name')!r}")
            except Exception:                   # noqa: BLE001
                pass
            return
        card.destroyed.connect(
            lambda *_a, c=card: c in self._cards and self._cards.remove(c))
        self._cards.append(card)
        self._card = card
        self._card.show_beside(self.featured_list.viewport(), point)

    def reaccent(self):
        """Repaint the source glyphs after an accent change.

        They are pixmaps drawn once in whatever the accent was at the
        time, so the selected tile kept the old colour until something
        else happened to redraw it.
        """
        self.sources._paint()

    def refresh_connections(self):
        """Ask each service whether it has a usable credential.

        Signing in happens in a dialog somewhere else, so the row cannot
        know on its own; it is asked whenever this page is opened.
        """
        connected = set()
        try:
            import ibroadcast
            if ibroadcast.load_tokens().get("refresh_token"):
                connected.add("ibroadcast")
        except Exception:                       # noqa: BLE001
            pass
        self.sources.set_connected(connected)

    def _source_chosen(self, key):
        """The bar means something different for each of these, so it says
        so — and the two that cannot be typed open their own picker."""
        # iBroadcast's hint depends on whether you are signed in.
        #
        # It always said "Signing in to iBroadcast…", which is only true
        # the first time — for anyone already signed in it described
        # something that was not happening.
        hint = SOURCE_HINTS[key]
        if key == "ibroadcast":
            import ibroadcast as _ib
            signed_in = _ib.signed_in()
            hint = ("Choose a playlist from your iBroadcast library…"
                    if signed_in else "Sign in to iBroadcast to add your library…")
        self.query.setPlaceholderText(hint)
        if key == "search":
            # The directory is the only source with a directory's
            # furniture, so it is the only one that brings it on screen.
            self._set_browsing(True)
            self.query.setFocus()

            # Something to look at on arrival.
            #
            # An empty results area asks you to think of a search term
            # before the directory has shown you anything it holds; the
            # most listened stations are a reasonable answer to "what is
            # out there" and cost one request.
            if not self._results:
                self._browse_by("clickcount")
            return
        if key in ACTIONS:
            self.source_requested.emit(key, "")
            return
        self._set_browsing(False)
        self.query.setFocus()
        self.query.selectAll()

    def _submit(self):
        """Return in the bar, meaning whatever the chosen icon says."""
        text = self.query.text().strip()
        key = self.sources.current()
        if key == "search":
            self.search()
            return
        if not text:
            return
        self.source_requested.emit(key, text)
        self.query.clear()

    def _clear_browse_mode(self):
        self._browse_mode = ""

    def _browse_by(self, mode):
        """Ask the directory for a set, rather than sorting one.

        Empty search text on purpose: both of these are answers to "what
        is out there", and typing something first would narrow them into
        something else.
        """
        self._browse_mode = mode
        self.search()

    def search(self, keep_page=False):
        if self.sources.current() == "search":
            self._set_browsing(True)
        if not keep_page:
            self._page = 0
        query = self.query.text().strip()
        # An empty box with a language chosen means "browse everything that
        # matches", which is how you find stations you couldn't have named.
        # An empty box is fine when a button asked for a set.
        #
        # Trending, Random and the opening view all mean "show me what is
        # out there", which is a search with no words in it. Returning
        # early on empty text made every one of those buttons do nothing.
        if not query and not self._browse_mode:
            return
        # Dots rather than the word.
        #
        # "Searching…" is a sentence that has to be read; three dots
        # filling in turn say the same thing at a glance and stop saying
        # it the moment results land.
        self.status.setText("")
        self.searching.start()
        self.list.set_stations([])

        if self._worker is not None:
            try:
                self._worker.results_ready.disconnect()
                self._worker.error.disconnect()
            except (TypeError, RuntimeError):
                # Already disconnected, or finished and deleted itself.
                pass
            if self._worker.isRunning():
                # Kept only to be counted; a Job keeps itself alive.
                self._retired.append(self._worker)
                self._worker.finished.connect(self._drop_finished)

        worker = _SearchWorker(
            query,
            # A button's mode wins for the one search it asked for, then
            # hands back to the dropdown.
            order=(self._browse_mode or self.sort.currentData()),
            language="",
            # No location is sent, ever. The app has no business
            # knowing where you are to find a radio station.
            near=None,
            offset=self._page * PAGE_SIZE,
            limit=PAGE_SIZE,
        )
        worker.results_ready.connect(self._on_results)
        worker.error.connect(self._on_error)
        self._worker = worker
        worker.start()

    def _drop_finished(self):
        self._retired = [w for w in self._retired if w.isRunning()]

    def _turn(self, direction):
        self._page = max(0, self._page + direction)
        self.search(keep_page=True)

    def _on_results(self, results):
        self._results = results
        rows = [_row_from_result(r, i) for i, r in enumerate(results)]
        # Results arrive long after the library was read, so the +/− state
        # has to be stamped on here as well as in set_owned.
        for row in rows:
            row["_owned"] = (row.get("url") or "").lower() in self._owned
        self.list.set_stations(rows)
        self.prev_btn.setVisible(self._page > 0)
        self.next_btn.setVisible(len(results) >= PAGE_SIZE)
        if not results:
            self.status.setText("No more stations." if self._page
                                else "No stations found.")
            return
        first = self._page * PAGE_SIZE + 1
        self.status.setText(
            f"{first}–{first + len(results) - 1} · double-click to listen, "
            f"+ to keep")
        self.searching.stop()

    def _on_error(self, message):
        self.searching.stop()
        self.status.setText(message)

    def _on_filters(self, countries, languages):
        """Nothing to fill in any more.

        The language list fed a filter that has been removed; the
        callback stays because the worker still reports, and an empty
        handler is cheaper than unpicking the signal.
        """
        return

    def _on_featured(self, stations):
        self._featured = stations or []
        if not self._featured:
            self.featured_note.setText(radiobrowser.FEATURED_EMPTY)
            self.featured_note.setVisible(True)
            self.featured_list.setVisible(False)
            return
        self.featured_note.setVisible(False)
        self.featured_list.set_stations([
            {"id": e.get("id") or f"feat{i}",
             "name": e.get("name", ""),
             "location": e.get("country", ""),
             # The curator's own artwork, which travels inside the entry
             # as data rather than as a URL — a path would mean nothing on
             # anyone else's machine. Only favicon was being read, so the
             # picture that came with the station never reached the tile.
             "logo": _featured_art(e) or e.get("favicon", ""),
             "tags": [(t, "#8a8a92") for t in (e.get("tags") or [])[:2]],
             "url": e.get("url", ""),
             "_featured": e}
            for i, e in enumerate(self._featured)])

    # -- adding ------------------------------------------------------------

    def set_owned(self, urls):
        """Which results are already in the library, so the + becomes a −.
        Matched on URL, since a directory uuid means nothing locally."""
        self._owned = {(u or "").lower() for u in urls if u}
        for model in (self.list.model_, self.featured_list.model_):
            for i in range(model.rowCount()):
                row = model.station_at(i)
                row["_owned"] = (row.get("url") or "").lower() in self._owned
        self.list.viewport().update()
        self.featured_list.viewport().update()

    def _preview(self, row):
        station = self._as_station(row)
        if station.get("url"):
            self.preview_requested.emit(station)
            self.status.setText(
                f"Playing {station.get('name') or station['url']} — "
                f"+ adds it to your stations.")

    def is_owned(self, row):
        """Is this row's station one of mine — asked now, not remembered.

        Rows reach their callers through Qt signals, and a dict passed
        that way is a copy. Its `_owned` flag is a snapshot from the moment
        it was sent: the station card keeps its copy open, so after Add the
        flag still said "not mine" and the minus added again (refused as a
        duplicate) instead of removing. The page's own set is kept current
        by `set_owned` on every reload, so that is what decides.
        """
        url = (row.get("url") or "").lower()
        if url:
            return url in self._owned
        return bool(row.get("_owned"))

    def _toggle(self, row):
        if self.is_owned(row):
            self.station_removed.emit(row.get("url") or "")
        else:
            self._add(row)

    def _row_menu(self, row, pos):
        menu = QMenu(self)
        menu.addAction("Play", lambda: self._preview(row))
        menu.addSeparator()
        if self.is_owned(row):
            menu.addAction("Remove from my stations",
                           lambda: self.station_removed.emit(
                               row.get("url") or ""))
        else:
            menu.addAction("Add to my stations", lambda: self._add(row))
        open_menu(menu, pos)

    def _as_station(self, row):
        featured = row.get("_featured")
        if featured is not None:
            return storage.station_from_featured(featured)
        result = row.get("_result") or {}
        return storage._normalize_station({
            "name": result.get("name", ""),
            "url": result.get("url", ""),
            "rb_uuid": result.get("uuid", ""),
            "favicon": result.get("favicon", ""),
            "homepage": result.get("homepage", ""),
        })

    def _add(self, row):
        # A featured entry carries the curator's tags, EQ and artwork, so it
        # becomes a station wholesale rather than as four strings.
        station = self._as_station(row)
        if not station.get("url"):
            self.status.setText("That entry has no stream URL.")
            return
        # The window has the last word on the status line: it knows whether
        # this was actually added or refused as a duplicate, and setting the
        # text here too would overwrite that.
        self.station_added.emit(station)


class SearchPage(QWidget):
    """One field over the library, with Discover offered underneath.

    Your own stations answer instantly because they are already in memory;
    the directory needs a round trip, so it stays a deliberate second step
    rather than something that fires on every keystroke.
    """

    discover_requested = pyqtSignal(str)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.lib = library

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 8, 8)
        lay.setSpacing(10)

        self.field = search_field("Search your stations")
        self.field.textChanged.connect(self._typed)
        self.field.returnPressed.connect(self._hand_off)
        lay.addWidget(self.field)

        self.heading = QLabel("Your stations")
        self.heading.setProperty("role", "section")
        lay.addWidget(self.heading)

        self.list = StationList()
        lay.addWidget(self.list, 1)

        self.elsewhere = QPushButton("Search radio-browser instead")
        self.elsewhere.clicked.connect(self._hand_off)
        self.elsewhere.setVisible(False)
        lay.addWidget(self.elsewhere)

        # Typing is not searching. A short pause keeps the list from
        # rebuilding on every keystroke across 325 stations.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._run)

    def refresh(self):
        self._rows = self.lib.rows()
        self.list.set_stations(self._rows)
        self._run()

    def _typed(self, _text):
        self._debounce.start()

    def _run(self):
        text = self.field.text().strip()
        found = self.list.filter(text)
        if not text:
            self.heading.setText(f"Your stations · {found}")
            self.elsewhere.setVisible(False)
            return
        self.heading.setText(
            f"Your stations · {found} match" + ("" if found == 1 else "es"))
        # The offer appears when the library came up short, which is exactly
        # when the directory is worth the round trip.
        self.elsewhere.setVisible(found < 3)
        self.elsewhere.setText(f"Search radio-browser for “{text}”")

    def _hand_off(self):
        text = self.field.text().strip()
        if text:
            self.discover_requested.emit(text)
