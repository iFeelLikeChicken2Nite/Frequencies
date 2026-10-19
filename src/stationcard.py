"""Frequencies — the little card that appears over a featured station.

A window rather than a popup: it stays until it is closed, and more than
one can be open at once. At the foot of it sit three icon buttons — Play,
Add and Share — which do the same things as the right-click menu on the
featured strip.

The blurbs are dog jokes for now, in the same spirit as the help page.
"""

from PyQt5.QtCore import QPoint, QPointF, QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                             QScrollArea, QToolButton, QVBoxLayout, QWidget)

from theme import T

# Keyed loosely: whatever a station is called, one of these fits.
# Title, subtitle, body — the three parts the card shows. Dog jokes for
# now; the shape is what matters, so filling these in later is editing a
# list rather than changing the card.
BLURBS = (
    ("Requests only",
     "Talk · listener-driven",
     "A dog walks into a radio station and asks to make a request.\n\n"
     "The host says they don't take requests from dogs. The dog says "
     "fine, he'll sit outside the window and stare. They played it."),

    ("No bath talk",
     "Music · uninterrupted",
     "Why do dogs love this station?\n\n"
     "No talk segments, and nobody ever says the word \"bath\"."),

    ("Third song rule",
     "Eclectic · loud",
     "My dog has strong opinions about this one.\n\n"
     "He howls along to every third song, which is a better hit rate "
     "than most critics."),

    ("AM, obviously",
     "Oldies · gentle",
     "What's a dog's favorite kind of radio?\n\n"
     "AM. He likes anything that comes with a walk beforehand."),

    ("Sixteen hours",
     "Ambient · accidental",
     "This station once played sixteen hours of ambient by mistake.\n\n"
     "The dog slept through all of it and has requested it since."),

    ("Felt in the ribs",
     "Bass · physical",
     "Why did the dog sit so close to the speaker?\n\n"
     "Bass. He says he feels it in his ribs, which is also how he "
     "describes dinner."),

    ("The overnight show",
     "Freeform · nocturnal",
     "A beagle applied to host the overnight show.\n\n"
     "Excellent nose for a good track, no sense of an outro. Every song "
     "ends when he decides it does."),
)


def blurb_for(name):
    """One of the jokes, chosen by name so a station keeps the same one.

    Random would give a different joke every time the same card opened,
    which reads as a bug rather than as variety.
    """
    return BLURBS[sum(ord(c) for c in (name or "?")) % len(BLURBS)]


# Two glyphs the shared icon painter does not have.
#
# Play and Add ("plus") come from shell.icon like every other button. Share
# and Remove were never needed as icons before — the list uses a menu — so
# they are drawn here, on the same 24-unit grid and with the same pen, and
# the card stays a one-file change.
_extra_icons = {}


def _extra_icon(name, size, colour):
    key = (name, int(size), str(colour))
    hit = _extra_icons.get(key)
    if hit is not None:
        return hit
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(colour)
    pen = QPen(c, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    u = size / 24.0

    if name == "minus":
        p.drawLine(QPointF(6 * u, 12 * u), QPointF(18 * u, 12 * u))
    elif name == "share":
        # Three dots joined by two lines: one thing going to two places.
        a, b, d = QPointF(18 * u, 5.5 * u), QPointF(6 * u, 12 * u), \
            QPointF(18 * u, 18.5 * u)
        p.drawLine(b, a)
        p.drawLine(b, d)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        for point in (a, b, d):
            p.drawEllipse(point, 2.9 * u, 2.9 * u)
    p.end()
    made = QIcon(pm)
    _extra_icons[key] = made
    return made


def _icon(name, size, colour):
    """Shared painter for the glyphs it knows, local one for the rest."""
    if name in ("minus", "share"):
        return _extra_icon(name, size, colour)
    from shell import icon
    return icon(name, size, colour)


class StationCard(QWidget):
    """A card about one station, closed by its own close button."""

    WIDTH = 320
    ICON = 20

    def __init__(self, station, parent=None):
        # A window, not a popup.
        #
        # A popup closes the moment you look away from it, which is wrong
        # for something you might want to read, copy from, or leave open
        # beside the list while you try the station. A window stays until
        # it is closed, and more than one can be open at once.
        super().__init__(parent, Qt.Window)
        self.setWindowTitle(
            __import__("storage").station_display_name(station))
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        # The row the card was opened from, kept as it came.
        #
        # The station below is a copy with every field defended; the
        # buttons need the original, because Play, Add and Share are
        # answered by the page that owns the row and it knows the row's
        # own shape (`_featured`, `_owned`).
        self._row = station if isinstance(station, dict) else {}
        # The page this card was opened from: the Add Frequency… page.
        self._page = parent

        import storage
        # Every field defended.
        #
        # These entries come from a directory, and a directory is not
        # tidy: tags can be a string rather than a list, country can be
        # absent, a name can be missing outright. Any one of those raised
        # here and took the whole card with it.
        station = dict(station or {})
        name = storage.station_display_name(station) or "Unknown station"
        blurb_title, blurb_sub, blurb_body = blurb_for(name)
        # Whatever was written for this station wins over the stock text.
        blurb_title = (station.get("card_title") or blurb_title).strip()
        blurb_sub = (station.get("card_subtitle") or blurb_sub).strip()
        blurb_body = (station.get("card_body") or blurb_body).strip()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(0)

        # The words sit in a scroll area of their own.
        #
        # The window is sized to fit them (`_fit`), so normally there is
        # nothing to scroll. But a curator's `card_body` can be any length,
        # and a card taller than the screen would hide its buttons. Then
        # the words scroll and the buttons stay at the foot.
        self._text = QWidget()
        self._text.setObjectName("cardText")
        text = QVBoxLayout(self._text)
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        self._text_lay = text

        self._scroll = QScrollArea()
        self._scroll.setObjectName("cardScroll")
        self._scroll.setWidget(self._text)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.viewport().setObjectName("cardView")
        self._scroll.setStyleSheet(
            "#cardScroll, #cardView, #cardText { background: transparent; }")
        lay.addWidget(self._scroll, 1)

        # Title: the station, always — the card is about this station,
        # not about the note attached to it.
        title = QLabel(name)
        title.setProperty("role", "display")
        title.setWordWrap(True)
        text.addWidget(title)

        # Subtitle: what the station is. Whatever the directory knows
        # comes first, since that is true; the written one fills in when
        # it knows nothing.
        # Tags arrive two ways.
        #
        # A station from the library has plain strings; a row built for
        # the list has (name, colour) pairs, and printing one of those
        # gave a Python tuple on screen — quotes, hash and all.
        tags = station.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        flat = []
        for tag in tags:
            if isinstance(tag, (tuple, list)):
                tag = tag[0] if tag else ""
            if tag:
                flat.append(str(tag))
        tags = flat
        known = " · ".join(str(x) for x in (station.get("country"),
                                            station.get("language"),
                                            ", ".join(tags)) if x)
        subtitle = QLabel(known or blurb_sub)
        subtitle.setProperty("role", "secondary")
        subtitle.setWordWrap(True)
        text.addWidget(subtitle)

        text.addSpacing(10)

        heading = QLabel(blurb_title)
        heading.setProperty("role", "title")
        heading.setWordWrap(True)
        text.addWidget(heading)

        body = QLabel(blurb_body)
        body.setProperty("role", "body")
        body.setWordWrap(True)
        text.addWidget(body)

        # Spare height, if the window is made taller, goes below the
        # words, so they stay together at the top and the buttons at the
        # foot — not spread out down the window.
        text.addStretch(1)

        lay.addSpacing(10)
        lay.addLayout(self._build_buttons())

        self.setFixedWidth(self.WIDTH)
        self._fit()

    # -- the three buttons ---------------------------------------------------

    def _build_buttons(self):
        """Play, Add, Share — icons only, each with a tooltip.

        Left-aligned under the text, in the order you would use them:
        listen first, keep it second, send it on third. Add turns into
        Remove for a station that is already in the library, the same
        swap the + does in the list.
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.play_btn = self._button("play", "Play")
        self.play_btn.clicked.connect(self._play)
        self.add_btn = self._button("plus", "Add to my stations")
        self.add_btn.clicked.connect(self._add_or_remove)
        self.share_btn = self._button("share", "Share…")
        self.share_btn.clicked.connect(self._share)

        for b in (self.play_btn, self.add_btn, self.share_btn):
            row.addWidget(b)
        row.addStretch(1)

        # Nothing to answer them, nothing to press.
        #
        # The card is only ever opened from the Add Frequency… page, but a
        # card built anywhere else should show greyed buttons rather than
        # raise when one is clicked.
        if not self._wired():
            for b in (self.play_btn, self.add_btn, self.share_btn):
                b.setEnabled(False)
        self._sync_add()
        return row

    def _button(self, glyph, tip):
        b = QToolButton()
        b.setAttribute(Qt.WA_Hover, True)
        b.setAutoRaise(True)
        b.setIcon(_icon(glyph, self.ICON, T["text2"]))
        b.setIconSize(QSize(self.ICON, self.ICON))
        b.setToolTip(tip)
        b.setFixedSize(34, 34)
        b.setFocusPolicy(Qt.NoFocus)
        return b

    def _wired(self):
        page = self._page
        return all(hasattr(page, attr) for attr in
                   ("_preview", "_toggle", "_as_station", "share_requested"))

    def _owned(self):
        """Is this station already one of mine?

        Asked of the page every time — it keeps the list of owned
        addresses current — and of the row only when there is no page.
        The row is a copy taken when the card opened, so its own `_owned`
        goes stale the moment Add or Remove is pressed.
        """
        ask = getattr(self._page, "is_owned", None)
        if callable(ask):
            return ask(self._row)
        owned = getattr(self._page, "_owned", None)
        url = (self._row.get("url") or "").lower()
        if owned is not None and url:
            return url in owned
        return bool(self._row.get("_owned"))

    def _sync_add(self):
        """Show Add or Remove, whichever is true now."""
        owned = self._owned()
        self.add_btn.setIcon(_icon("minus" if owned else "plus",
                                   self.ICON, T["text2"]))
        self.add_btn.setToolTip("Remove from my stations" if owned
                                else "Add to my stations")

    def _play(self):
        if self._wired():
            self._page._preview(self._row)

    def _add_or_remove(self):
        """Add or remove, decided by what the icon shows.

        The icon and the action used to ask two different questions: the
        icon asked the page (current), the action read the row's own
        `_owned` (a copy from when the card opened). After Add the icon
        became a minus, but the action still thought "not mine" and tried
        to add again, which the window refused as a duplicate. So the
        minus did nothing (task 25). Now both ask `_owned()`.
        """
        if not self._wired():
            return
        page = self._page
        if self._owned():
            # The window asks "Remove …?" first, as it does everywhere.
            page.station_removed.emit(self._row.get("url") or "")
        else:
            page._add(self._row)
        self._row["_owned"] = self._owned()
        self._sync_add()
        # In case the window answers late, look once more.
        QTimer.singleShot(250, self._sync_add)

    def _share(self):
        if self._wired():
            self._page.share_requested.emit(self._page._as_station(self._row))

    # -- painting and placement ---------------------------------------------

    def paintEvent(self, _e):
        # A plain panel: the window manager draws the frame now, so a
        # rounded rectangle inside it would be a second border.
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T["bg0"]))
        p.end()

    def _fit(self):
        """Open tall enough for every word (task 26).

        `adjustSize` measured the labels before the theme's fonts reached
        them (the body grows from the default size to 15 px when it is
        styled), and a window's own size hint ignores how wrapped text
        gets taller as it gets narrower. So the card opened a line or two
        short and cut the joke off. Here the fonts are applied first, then
        the words are measured at the width they really get.
        """
        self.ensurePolished()
        for child in self.findChildren(QWidget):
            child.ensurePolished()
        lay = self.layout()
        # Forget the last fit, so it isn't counted as "the rest".
        self._scroll.setMinimumHeight(0)
        m = lay.contentsMargins()
        inner = self.WIDTH - m.left() - m.right()
        need = self._text_lay.totalHeightForWidth(inner)
        if need < 0:
            need = self._text_lay.totalSizeHint().height()
        # Never taller than the screen: past that the words scroll.
        screen = QApplication.primaryScreen()
        room = (screen.availableGeometry().height() - 80) if screen else 900
        others = lay.totalMinimumSize().height() \
            - self._scroll.minimumSizeHint().height()
        need = max(40, min(need, room - others))
        self._scroll.setMinimumHeight(need)
        height = lay.totalMinimumSize().height()
        self.setMinimumHeight(height)
        self.resize(self.WIDTH, max(height, self.height()
                                    if self.isVisible() else 0))

    def show_beside(self, widget, point):
        """Open near the tile, kept on screen.

        Placed below-right by default, flipped when that would run off the
        edge — a card half outside the window is worse than one on the
        other side of the pointer.
        """
        from PyQt5.QtWidgets import QApplication

        # Above the row, not below it.
        #
        # Opening downward covered the stations either side of the one
        # clicked — the very things you were comparing it against. The
        # card goes above unless there is no room up there, in which case
        # below is better than off the screen.
        self._fit()
        anchor = widget.mapToGlobal(point)
        screen = QApplication.primaryScreen().availableGeometry()

        top = anchor.y() - self.height() - 14
        if top < screen.top() + 8:
            top = anchor.y() + 24
        left = anchor.x() - self.width() // 2
        left = max(screen.left() + 8,
                   min(left, screen.right() - self.width() - 8))
        self.move(QPoint(left, top))
        self.show()
        self.raise_()
        self.activateWindow()
