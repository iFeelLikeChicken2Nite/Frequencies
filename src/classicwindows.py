"""Frequencies — two more windows for the skinned stack.

Winamp never had either of these, so there is no original to copy. Both
borrow PlaylistWindow's chrome, which is the one resizable window the skin
format defines: fixed corners, repeating edges, a scrollbar and a row list
already drawn in the skin's own colors.

  HistoryWindow   what has been identified, and what you starred
  LyricsWindow    the words, following the song

Neither knows anything about storage or playback. They render what they
are given and report clicks, exactly as the playlist does.
"""

from PyQt5.QtCore import QPoint, Qt, pyqtSignal
from PyQt5.QtGui import QPainter, QPixmap

from classicextras import PlaylistWindow


class _RenamedList(PlaylistWindow):
    """A skinned list that does not claim to be the playlist.

    The title bar is a bitmap from the skin with the word PLAYLIST drawn
    into it, so a window borrowing this chrome borrows the label too.
    The frame draws the plate with PLATE_LABEL in place of the skin's
    word, in the skin's own plate colours - see winampskin.relabel_plate.

    It used to cover the word with a black band and write the name over
    it in bold green, which made these two title bars the odd ones out
    in every skin.
    """

    LABEL = ""

    def paintEvent(self, _event):
        canvas = QPixmap(self.skin_width, self.skin_height)
        canvas.fill(self.skin.color("normalbg", "#000000"))
        if self.shaded:
            self._paint_shade_bar(canvas, self.LABEL)
            return
        painter = QPainter(canvas)
        self._draw_frame(painter)
        self._draw_rows(painter)
        painter.end()
        self._blit(canvas)


class HistoryWindow(_RenamedList):
    # Nothing to add, remove or reorder in a history — and each row starts
    # with when it was heard, which says more than a running number.
    BUTTONS = False
    NUMBERED = False

    """Identified songs, or just the starred ones.

    Reuses the playlist's list machinery — scrolling, selection, the
    filter line — because it is the same shape of problem and a second
    implementation would drift from it.
    """

    LABEL = "History"
    PLATE_LABEL = "Freqamp History"

    entry_activated = pyqtSignal(int)        # row in the visible list
    scope_toggled = pyqtSignal(bool)         # True when showing favourites
    # (song, artist, station, starred) and where the pointer was.
    menu_requested = pyqtSignal(object, QPoint)

    def __init__(self, skin, scale=2, parent=None, width=275, height=200):
        super().__init__(skin, scale, parent, width, height)
        self.setWindowTitle("History")
        self.favourites_only = False
        self.manual_sort = False        # nothing here is dragged into order
        self._all_entries = []
        self._entries = []
        self.station_activated.connect(self.entry_activated.emit)
        # The filter line filters this window's own rows.
        #
        # It is the playlist's filter line, borrowed, and it emitted into
        # nothing here — typing changed the text in the box and left the
        # list exactly as it was.
        self.search_changed.connect(lambda _t: self._apply_filter())

    def set_entries(self, entries, favourites_only=None):
        """entries: (song, artist, station, starred) in newest-first order."""
        if favourites_only is not None:
            self.favourites_only = bool(favourites_only)
        self._all_entries = list(entries)
        self._apply_filter()

    def _apply_filter(self):
        """Show only the rows that match what is typed in the filter.

        Matches the song, the artist and the station, so typing a station
        name finds everything heard on it. The visible rows are kept in
        step with the entries they came from, so a double-click or a
        right-click on a filtered row acts on the right song.
        """
        needle = (getattr(self, "search_text", "") or "").strip().lower()
        shown = []
        for entry in self._all_entries:
            song, artist, station = entry[0], entry[1], entry[2]
            hay = f"{song} {artist} {station}".lower()
            if not needle or needle in hay:
                shown.append(entry)
        self._entries = shown
        rows = []
        for entry in shown:
            song, artist, _station, starred = entry[:4]
            when = _when(entry[4] if len(entry) > 4 else 0)
            label = f"{artist} - {song}" if artist else song
            # A leading star rather than a column: the row is one line of
            # skin-drawn text, and there is nowhere to put a second field.
            rows.append(f"{when}  " + ("* " if starred else "") + label)
        self.set_stations(rows, -1)

    def entry_at(self, row):
        entries = getattr(self, "_entries", [])
        return entries[row] if 0 <= row < len(entries) else None

    def contextMenuEvent(self, e):
        """Right-click: the same menu as the main window's history."""
        # In skin pixels, like every other click this window handles —
        # the window is drawn at two or three times the skin's size.
        row = self._row_at(self._skin_point(e))
        entry = self.entry_at(row)
        if entry is not None:
            self.menu_requested.emit(entry, e.globalPos())

    def keyPressEvent(self, e):
        # F toggles between everything and favourites, since the window is
        # too narrow for a pair of buttons that would each need a label —
        # but not while the filter has the keyboard, or every word with an
        # "f" in it flipped the view instead of being typed.
        if e.key() == Qt.Key_F and not getattr(self, "_search_focused", False):
            self.favourites_only = not self.favourites_only
            self.scope_toggled.emit(self.favourites_only)
            return
        super().keyPressEvent(e)


def _when(timestamp):
    """A time, with a short date when it was not today.

    Today's songs need only the time; anything older gets a day and month
    in front, so a row from last week is not mistaken for one from this
    morning. Follows the app's 12/24-hour setting.
    """
    import datetime
    import clock

    if not timestamp:
        return ""
    try:
        moment = datetime.datetime.fromtimestamp(float(timestamp))
    except (TypeError, ValueError, OSError):
        return ""
    time_part = clock.fmt(moment)
    if moment.date() == datetime.date.today():
        return time_part
    return f"{moment.day} {moment.strftime('%b')} {time_part}"


class LyricsWindow(_RenamedList):
    # Lines of a song, not tracks in a playlist: no numbers, and centred.
    NUMBERED = False
    CENTRED = True
    SEARCHABLE = False
    BUTTONS = False

    """The words, with the current line marked.

    The skin gives us one text color and one "current row" color, which
    is exactly what a lyric view needs — the playing row is the line being
    sung.
    """

    LABEL = "Lyrics"
    PLATE_LABEL = "Freqamp Lyrics"

    # A line's start, in milliseconds, when it is double-clicked.
    resync_requested = pyqtSignal(int)

    def __init__(self, skin, scale=2, parent=None, width=275, height=180):
        super().__init__(skin, scale, parent, width, height)
        self.setWindowTitle("Lyrics")
        # Double-clicking a line means "the song is here" — the same as in
        # the main window. A playlist row's double-click plays a station;
        # here there is no station, only a moment in the song.
        self.station_activated.connect(self._line_chosen)
        self.manual_sort = False
        self._timed = []
        self._line = -1

    def _line_chosen(self, index):
        """Hand back when this line starts, if the lyrics are timed.

        Untimed lyrics have no positions, so there is nothing to sync to
        and the double-click does nothing.
        """
        if 0 <= index < len(getattr(self, "_timed", [])):
            self.resync_requested.emit(int(self._timed[index][0]))

    def set_lyrics(self, lines, message=None):
        """lines: [(ms, text)] or plain strings. A message replaces them."""
        if not lines:
            self._timed = []
            self._line = -1
            self.set_stations([message or "No lyrics for this track."], -1)
            return
        if isinstance(lines[0], (tuple, list)):
            self._timed = [(int(t), str(s)) for t, s in lines]
            rows = [s for _t, s in self._timed]
        else:
            self._timed = []
            rows = [str(s) for s in lines]
        self._line = -1
        self.set_stations(rows, -1)

    def set_position(self, ms):
        """Mark the line being sung, and keep it in view.

        Unsynced lyrics have no positions, so this does nothing for them
        rather than guessing — a highlight that is wrong is worse than no
        highlight at all.
        """
        if not self._timed:
            return
        line = -1
        for index, (at, _text) in enumerate(self._timed):
            if ms >= at:
                line = index
            else:
                break
        if line == self._line:
            return
        self._line = line
        # set_stations scrolls the current row into view for us.
        self.set_stations([s for _t, s in self._timed], line)
