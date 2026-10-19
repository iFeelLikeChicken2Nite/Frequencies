"""brat mode: while a song from Charli XCX's album "brat" plays, the whole
app shows its text in lowercase (task 48, build 66).

Which songs: only the original album, named exactly "brat" (any case),
and only by Charli XCX. The deluxe and remix albums ("Brat and it's the
same but…", "Brat and it's completely different…") don't count.

Where the album comes from, best first:
  "station"  the song's own metadata — iBroadcast's album field, a music
             folder file's album tag;
  "shazam"   the album Shazam names for the song it identified;
  "title"    nothing names an album (a radio stream's "artist - title",
             a YouTube video): the song counts when the artist is Charli
             XCX alone and the title is one of brat's 15 tracks, as named
             on the album (no "featuring", no remix).
For the same song, a weaker source never overrules a stronger one.

How: display only, nothing saved changes.
  - The application font gets QFont.AllLowercase (for windows made
    later), and so does every existing widget's own font (Qt doesn't
    pass the change down past stylesheet-fixed containers). Labels,
    buttons, menus, fields, lists, rich text, tooltips and dialogs all
    follow it, stylesheet fonts too; their other font settings stay.
  - Between two songs of one station the look holds until the new
    song is known (BratMode.next_song, GRACE_MS), so no capitals flash.
  - Text the app paints itself goes through QPainter.drawText, which
    install() wraps: while the mode is on, the string is lowered (and a
    painter font set to ALL CAPS is put back to normal case first).
  - Window titles (install() wraps QWidget.setWindowTitle): shown in
    lowercase, the real title kept in the "_brat_title" property and put
    back when the mode ends.
The Winamp skins' bitmap fonts are pictures, so they stay as they are.

Log: `[brat] lowercase on (album 'brat') — <artist> – <title>, from …`
and `[brat] lowercase off (…)`.
"""

import re

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

ALBUM = "brat"
ARTIST = "charli xcx"

# The original album, in order. Compared after _norm().
TRACKS = (
    "360",
    "Club classics",
    "Sympathy is a knife",
    "I might say something stupid",
    "Talk talk",
    "Von dutch",
    "Everything is romantic",
    "Rewind",
    "So I",
    "Girl, so confusing",
    "Apple",
    "B2b",
    "Mean girls",
    "I think about it all the time",
    "365",
)

# Read by the painter and title wrappers. One flag for the whole app.
ON = False

RANK = {"title": 0, "shazam": 1, "station": 2}
SOURCE_NAME = {"title": "the song's name", "shazam": "Shazam",
               "station": "the station's own song info"}

_installed = False
_TITLE_PROP = "_brat_title"


def _norm(text):
    """Lowercase, punctuation and repeated spaces gone: "Girl, so
    confusing" and "girl so confusing" are the same name."""
    text = (text or "").casefold().replace("&", " and ")
    return re.sub(r"[^\w]+", " ", text).strip()


# "(Explicit)", "[Clean]", " - Explicit": a store's label, not a
# different song.
_LABEL = re.compile(r"\s*(?:[\(\[]\s*(?:explicit|clean)\s*[\)\]]"
                    r"|-\s*(?:explicit|clean))\s*$", re.IGNORECASE)

# A music file with no tags is named by its file name.
_FILE = re.compile(r"\.(?:mp3|flac|m4a|aac|ogg|oga|opus|wav|wma|alac)$",
                   re.IGNORECASE)

_TRACKS = {_norm(t) for t in TRACKS}


def is_charli(artist):
    return ARTIST in _norm(artist)


def album_is_brat(artist, album):
    """The album named, by the artist named: exactly brat, by Charli."""
    return _norm(album) == ALBUM and is_charli(artist)


def title_is_brat(artist, title):
    """No album known: Charli XCX alone, and one of brat's own titles."""
    if _norm(artist) != ARTIST:
        return False
    title = _FILE.sub("", (title or "").strip())
    return _norm(_LABEL.sub("", title)) in _TRACKS


def split_stream_title(text):
    """'Artist - Title' as a stream sends it → (artist, title), or None."""
    text = (text or "").strip()
    for sep in (" - ", " – ", " — "):
        if sep in text:
            artist, title = text.split(sep, 1)
            if artist.strip() and title.strip():
                return artist.strip(), title.strip()
    return None


# -- the look ---------------------------------------------------------------

def install():
    """Wrap QPainter.drawText and QWidget.setWindowTitle. Once, early in
    main(), before any window exists. Costs one flag check per call while
    the mode is off."""
    global _installed
    if _installed:
        return
    from PyQt5.QtGui import QFont, QPainter
    from PyQt5.QtWidgets import QWidget

    draw = QPainter.drawText

    def draw_text(painter, *args, _draw=draw):
        if not ON:
            return _draw(painter, *args)
        args = tuple(a.lower() if isinstance(a, str) else a for a in args)
        font = painter.font()
        if font.capitalization() in (QFont.AllUppercase, QFont.SmallCaps,
                                     QFont.Capitalize):
            # Column headers are drawn in ALL CAPS on purpose.
            plain = QFont(font)
            plain.setCapitalization(QFont.MixedCase)
            painter.setFont(plain)
            try:
                return _draw(painter, *args)
            finally:
                painter.setFont(font)
        return _draw(painter, *args)

    QPainter.drawText = draw_text

    set_title = QWidget.setWindowTitle

    def set_window_title(widget, title, _set=set_title):
        title = title or ""
        try:
            widget.setProperty(_TITLE_PROP, title)
        except Exception:                       # noqa: BLE001
            pass
        return _set(widget, title.lower() if ON else title)

    QWidget.setWindowTitle = set_window_title
    QWidget._brat_plain_title = set_title
    _installed = True


def apply(on):
    """Switch the whole app's text to lowercase, or back. GUI thread."""
    global ON
    ON = bool(on)
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication, QWidget

    app = QApplication.instance()
    if app is None:
        return
    target = QFont.AllLowercase if ON else QFont.MixedCase
    font = app.font()
    font.setCapitalization(target)
    app.setFont(font)       # windows and widgets made from now on
    # And every widget that exists. Qt passes an app font change down to
    # children only where nothing has fixed their font, and the
    # stylesheet does fix some: tested, containers (the page stack, the
    # splitter) kept the lowercase font after the mode ended, and a new
    # page built inside them would have inherited it. So each widget gets
    # the case set on its own font — its other font settings stay as
    # they are (the copy keeps which attributes it had set).
    for w in app.allWidgets():
        f = w.font()
        if f.capitalization() != target:
            f.setCapitalization(target)
            w.setFont(f)

    plain = getattr(QWidget, "_brat_plain_title", QWidget.setWindowTitle)
    for w in app.topLevelWidgets():
        try:
            real = w.property(_TITLE_PROP)
            if real is None:
                real = w.windowTitle()
                w.setProperty(_TITLE_PROP, real)
            plain(w, real.lower() if ON else real)
        except Exception:                       # noqa: BLE001
            pass
    # Painted text (lists, pills, headers) only changes when repainted.
    for w in app.allWidgets():
        w.update()


# -- when ---------------------------------------------------------------------

class BratMode(QObject):
    """Follows the song the app shows and switches the look.

    mainwindow calls song() wherever a song is shown (Shazam, iBroadcast,
    YouTube, a music folder's tags), stream_title() for a stream's own
    "artist - title" while nothing better is known, and clear() when the
    station changes or playback stops.
    """

    changed = pyqtSignal(bool)

    # The next song on the same station (a music folder, iBroadcast, a
    # YouTube playlist): how long the look waits for that song's info
    # before it gives up and goes back to capitals. Without it, two brat
    # songs in a row flashed capitals in between.
    GRACE_MS = 6000

    def __init__(self, log=None, parent=None):
        super().__init__(parent)
        self.on = False
        self._key = None
        self._rank = -1
        self._log = log or (lambda text: None)
        self._grace = QTimer(self)
        self._grace.setSingleShot(True)
        self._grace.timeout.connect(lambda: self._set(
            False, "next song, nothing says it is from brat"))

    def song(self, artist, title, album=None, source="title"):
        artist = (artist or "").strip()
        title = (title or "").strip()
        if not artist and not title:
            return
        key = (_norm(artist), _norm(title))
        rank = RANK.get(source, 0)
        album = (album or "").strip()
        named = f"{artist} – {title}"
        if not album:
            if key == self._key:
                return              # same song: keep what is known
            rank = 0
            on = title_is_brat(artist, title)
            if on:
                reason = "album unknown, a brat track by name"
            elif is_charli(artist):
                reason = "album unknown, not a brat track"
            else:
                reason = "not Charli XCX"
            detail = f"{named}, from the song's name"
        else:
            if key == self._key and rank < self._rank:
                return              # a weaker source than the one we have
            on = album_is_brat(artist, album)
            reason = f"album '{album}'"
            if not on and _norm(album) == ALBUM:
                reason += ", not Charli XCX"
            detail = f"{named}, from {SOURCE_NAME.get(source, source)}"
        self._key, self._rank = key, rank
        self._set(on, reason, detail)

    def stream_title(self, text):
        """A stream's own title, used only while it is what the app shows.
        Anything that isn't "artist - title" (station slogans, a show's
        name) leaves the mode as it is."""
        parts = split_stream_title(text)
        if parts:
            self.song(parts[0], parts[1], None, "title")

    def tags(self, tags):
        """A music folder file's tags (mpv's `metadata`): artist, title,
        album. Only when the file names an album."""
        if not isinstance(tags, dict):
            return
        low = {str(k).casefold(): v for k, v in tags.items()}
        album = low.get("album")
        if not album:
            return
        artist = low.get("artist") or low.get("album_artist") or ""
        title = low.get("title") or ""
        self.song(str(artist), str(title), str(album), "station")

    def next_song(self):
        """Same station, next song: forget the last song, but keep the
        look until the new one is known (or GRACE_MS runs out)."""
        self._key, self._rank = None, -1
        if self.on:
            self._grace.start(self.GRACE_MS)

    def clear(self, why):
        self._key, self._rank = None, -1
        self._set(False, why)

    def _set(self, on, reason, detail=""):
        self._grace.stop()          # the new song is known
        if on == self.on:
            return
        self.on = on
        line = f"[brat] lowercase {'on' if on else 'off'} ({reason})"
        if detail:
            line += f" — {detail}"
        self._log(line)
        apply(on)
        self.changed.emit(on)
