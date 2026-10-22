"""Frequencies — the right-hand panel.

It used to be a second copy of the artwork with the song title under it,
which the player bar and the full-screen view both already showed. So it
said nothing new while taking 300px of width.

Now it holds the two things that only exist here: the lyrics, and who the
artist is. Three buttons along the bottom choose between them.

The Both mode adapts rather than insisting: given enough width it splits
into two columns side by side, and when there isn't room it swaps between
them on a timer instead of squeezing two unreadable columns into one. And
because the artist's photo is the fastest way to recognize who you are
listening to, it stays pinned at the top of the panel in every mode — with
the lyrics under it when lyrics are what you asked for.
"""

import math
import os

from PyQt5.QtCore import (QEasingCurve, QEvent, QParallelAnimationGroup, QPoint, QRect,
                          QPropertyAnimation, QSize, Qt, QTimer, pyqtSignal)
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QWidget
from PyQt5.QtGui import QBrush, QColor, QFont, QFontMetrics, QPixmap
from PyQt5.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QStackedWidget,
                             QTextBrowser, QToolTip, QVBoxLayout, QWidget)

import devmode
from nowplaying import LyricsView, rounded
from shell import icon
from theme import T

# Below this, two columns are narrower than a readable line of lyrics, so
# Both alternates instead of splitting.
# Two columns need about this much to be worth reading. Tuned against the
# width the shell actually gives the panel on a maximised window rather
# than a number picked in the abstract.
SPLIT_AT = 520

# The strip at the foot of the panel that Lyrics / Artist / Both occupies.
# Reserved once, here, so every column ends above it rather than each item
# inside them padding itself clear separately.
SWITCH_BAND = 74
SWAP_EVERY = 14000              # ms per side when alternating

# How often the ring around Both is redrawn while it fills.
RING_MS = 40
PHOTO = 92


def log(line):
    try:
        import crashlog
        crashlog.write(line)
    except Exception:  # noqa: BLE001
        pass


def panel_menu(owner):
    """A right-click menu for something in the side panel.

    Parented to the window, never to the widget that asked (task 46).
    The artist column's viewport carries a bare `background: transparent`
    (so the photo can fade into it), and a bare rule reaches every child —
    a menu made as a child of the photo in the two-column layout came up
    see-through. Same cause as the dialogs in task 16 (build 51).
    """
    from PyQt5.QtWidgets import QMenu
    import theme
    menu = QMenu(owner.window())
    # The highlight wears the accent in use now, not the starting amber
    # (task 105): the window's sheet is only written at start-up.
    sheet = theme.current_overlay()
    if sheet:
        menu.setStyleSheet(sheet)
    return menu


class RingButton(QPushButton):
    """A button with a ring that fills clockwise from twelve.

    It shows the one thing the Both mode otherwise hides: that a timer
    is running, and how much of it is left. Drawn outside the label so
    the text is untouched.
    """

    WIDTH = 2.0
    RADIUS = 6.0                # matches QPushButton[segment] in theme.py

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.progress = lambda: 0.0
        self.tail = lambda: 0.0
        self.active = lambda: False

    def _outline(self, inset):
        """The button's own border as a path, from twelve, clockwise.

        drawArc draws an ellipse, so the ring bulged out of the button's
        corners and pinched at its sides — it was tracing a circle over a
        rounded rectangle. Walking the real outline means the light
        follows the edge whatever shape the button is.
        """
        from PyQt5.QtCore import QPointF as _P
        from PyQt5.QtGui import QPainterPath

        left, top = inset, inset
        right = self.width() - inset
        bottom = self.height() - inset
        # The button's own corner radius, not a stadium.
        #
        # height/2 made the ends semicircular, so the ring was a different
        # shape from the thing it was tracing — fatter at the sides and
        # standing off the corners. The stylesheet says 6px; the ring
        # says 6px.
        radius = max(0.0, min(self.RADIUS - inset, (right - left) / 2.0,
                              (bottom - top) / 2.0))
        middle = (left + right) / 2.0

        path = QPainterPath(_P(middle, top))
        path.lineTo(right - radius, top)
        path.quadTo(_P(right, top), _P(right, top + radius))
        path.lineTo(right, bottom - radius)
        path.quadTo(_P(right, bottom), _P(right - radius, bottom))
        path.lineTo(left + radius, bottom)
        path.quadTo(_P(left, bottom), _P(left, bottom - radius))
        path.lineTo(left, top + radius)
        path.quadTo(_P(left, top), _P(left + radius, top))
        path.lineTo(middle, top)
        return path

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.active():
            return
        try:
            head = float(self.progress())
            tail_len = float(self.tail())
        except Exception:                       # noqa: BLE001
            return

        # A segment travelling round, drawn in one or two pieces.
        #
        # The head is where the line ends and the tail is a fixed
        # distance behind it. Most of the time that is a single stretch
        # of border. When the segment straddles twelve o'clock the two
        # ends are on opposite sides of the path's start, so it is drawn
        # as two stretches — one running up to the seam, one continuing
        # from it. Drawing it as a single span across the seam is what
        # produced the overlap: the path doubles back on itself.
        head = head % 1.0
        tail = (head - tail_len) % 1.0
        if tail < head:
            spans = [(tail, head)]
        else:
            spans = [(tail, 1.0), (0.0, head)]

        from PyQt5.QtGui import QPainter, QPainterPath, QPen

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(T["accent"]), self.WIDTH)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)

        outline = self._outline(self.WIDTH / 2.0 + 0.5)

        # Sampled along the path rather than clipped: a clip would cut
        # the stroke square, and the point of the ring is a line with two
        # moving ends.
        for begin, finish in spans:
            length = finish - begin
            if length <= 0.001:
                continue
            steps = max(6, int(140 * length))
            drawn = QPainterPath(outline.pointAtPercent(begin))
            for i in range(1, steps + 1):
                drawn.lineTo(outline.pointAtPercent(begin + length * i / steps))
            p.drawPath(drawn)
        p.end()


class SwitchScrim(QWidget):
    """The backing behind Lyrics / Artist / Both.

    Not a plate: a rounded panel floating over a column reads as a
    separate object sitting on top of the text. What is wanted is for the
    column to simply become unreadable as it approaches the buttons — so
    this is a gradient of the panel's own color, solid along the bottom
    and gone by the top, with no edge anywhere.
    """

    def paintEvent(self, _e):
        from PyQt5.QtCore import QRectF
        from PyQt5.QtGui import QColor, QLinearGradient, QPainter

        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        for stop, alpha in ((0.0, 0), (0.45, 150), (0.75, 226), (1.0, 255)):
            colour = QColor(T["bg0"])
            colour.setAlpha(alpha)
            grad.setColorAt(stop, colour)
        p.fillRect(QRectF(0, 0, self.width(), self.height()), grad)
        p.end()


# Block properties on each chat line (task 92): whose it is, and whether
# its pronoun tag is in.
_LOGIN_PROP = 0x100000 + 921
_PILL_PROP = 0x100000 + 922
_NAME_PROP = 0x100000 + 923     # the sender's display name (task 103)


class _ChatText(QTextBrowser):
    """The chat's text. Emote pictures are asked for by name
    ("emote:tw:25") and come from chatextras (build 81, task 92)."""

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        self.codes = {}         # emote key -> its name, for the tooltip

    def viewportEvent(self, e):
        # An emote's name when the mouse rests on it (task 94).
        if e.type() == QEvent.ToolTip:
            code = self._emote_at(e.pos())
            if code:
                QToolTip.showText(e.globalPos(), code, self.viewport())
            else:
                QToolTip.hideText()
                e.ignore()
            return True
        return super().viewportEvent(e)

    def _emote_at(self, pos):
        key = self._emote_key_at(pos)
        return self.codes.get(key, "") if key else ""

    def _emote_key_at(self, pos):
        from PyQt5.QtGui import QTextCursor
        here = self.cursorForPosition(pos)
        for step in (QTextCursor.Left, QTextCursor.Right):
            start = QTextCursor(here)
            if not start.movePosition(step):
                continue
            lo = min(here.position(), start.position())
            probe = QTextCursor(here)
            probe.setPosition(lo)
            probe.setPosition(lo + 1, QTextCursor.KeepAnchor)
            fmt = probe.charFormat()
            if not fmt.isImageFormat():
                continue
            rect_a = self.cursorRect(here)
            rect_b = self.cursorRect(start)
            left = min(rect_a.x(), rect_b.x())
            right = max(rect_a.x(), rect_b.x())
            if left <= pos.x() <= right and \
                    abs(rect_a.y() - pos.y()) < 40:
                return fmt.toImageFormat().name()[6:]
        return ""

    def _line_at(self, pos):
        """(login, display name) of the message under `pos`, or ("", "")."""
        block = self.cursorForPosition(pos).block()
        fmt = block.blockFormat()
        return (fmt.property(_LOGIN_PROP) or "",
                fmt.property(_NAME_PROP) or "")

    def loadResource(self, kind, url):
        name = url.toString()
        if name.startswith("emote:"):
            return self._view._emote_resource(name[6:])
        return super().loadResource(kind, url)

    def contextMenuEvent(self, e):
        login, name = self._line_at(e.pos())
        key = self._emote_key_at(e.pos())
        self._view._menu(e.globalPos(), login, name,
                         (key, self.codes.get(key, "")) if key else None)


class ChatView(QWidget):
    """A Twitch channel's chat, read only (build 77, task 78).

    Newest at the bottom; each line is the sender's name in their Twitch
    colour, then the message. It follows new messages while you are at
    the bottom; scroll up and it holds still, with "New messages ↓" to
    jump back. Keeps the last KEEP messages. A message a moderator
    removes disappears.

    Build 81 (task 92): Twitch's and BetterTTV's emotes as pictures
    (animated ones move), the sender's pronouns in a grey tag before the
    name, and a pop-out button (`pop_out`) that opens the same chat in a
    window of its own. Right-click: Copy, Pop out chat, and
    switches for emotes, animated emotes and pronouns (`option_toggled`).

    Build 85 (task 95): chat badges before names, and Twitch's chat bar
    under the chat (`chatbar.ChatBar`: emote picker, ⚙ chat options,
    Chat), with the ":" list (`chatbar.SuggestList`) floating above it.
    `set_live(on, channel)` shows or hides the bar.
    """

    KEEP = 200
    SIZE = 15                   # px, a little under the lyrics' 20

    pop_out = pyqtSignal()
    option_toggled = pyqtSignal(str, bool)
    send_asked = pyqtSignal(str)        # build 82: a message you typed
    sign_in_asked = pyqtSignal()
    sign_out_asked = pyqtSignal()
    settings_asked = pyqtSignal()       # task 95: Settings > Twitch chat
    rules_accepted = pyqtSignal(str)    # task 95: you OK'd a channel's rules
    colour_asked = pyqtSignal(str, str)  # task 95c: Twitch's value, #hex

    def __init__(self, parent=None, can_pop=True):
        super().__init__(parent)
        from collections import deque
        import chatextras

        self._items = deque(maxlen=self.KEEP)
        self._status = ""
        self._used = set()          # emote keys in the text now
        self._animated = set()
        self._frame_shown = {}
        self._to_lay_out = set()
        self.can_pop = can_pop
        self.extras = chatextras.shared()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 0, 8, 0)
        lay.setSpacing(0)
        self.text = _ChatText(self)
        self.text.setFrameShape(QTextBrowser.NoFrame)
        self.text.setOpenExternalLinks(True)
        self.text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text.document().setMaximumBlockCount(self.KEEP)
        self.text.document().setDocumentMargin(2)
        # Nothing here is ever undone; an undo list would only grow.
        self.text.document().setUndoRedoEnabled(False)
        self.text.viewport().setAutoFillBackground(False)
        lay.addWidget(self.text, 1)
        # Where you type (build 82, task 92e), in Twitch's chat bar with
        # the emote picker, chat options and Chat buttons (task 95).
        import chatbar
        self._writer = ""
        self._signed_in = ""
        self._live = False
        self.bar = chatbar.ChatBar(self)
        self.entry = self.bar.entry
        self.bar.send.connect(self.send_asked.emit)
        self.bar.colour_asked.connect(self.colour_asked.emit)
        self.bar.sign_in_asked.connect(self.sign_in_asked.emit)
        self.bar.options_asked.connect(self._options_menu)
        self.bar.typing.connect(self._suggest)
        self.entry.installEventFilter(self)
        self.bar.hide()
        lay.addSpacing(6)
        lay.addWidget(self.bar)
        # The ":" list floats just above the bar (task 95).
        self.suggest = chatbar.SuggestList(self)
        self.suggest.picked.connect(self._take_suggestion)
        # The channel's chat rules, before you first write (task 95).
        self.rules_card = chatbar.RulesCard(self)
        self.rules_card.accepted.connect(self._rules_ok)
        self.bar.chat_button.clicked.connect(
            lambda: self._rules_pending() and self.show_rules())
        self.restyle()

        # Floats over the bottom of the chat, above the switch band.
        self.jump = QPushButton("New messages ↓", self)
        self.jump.setProperty("segment", True)
        self.jump.setCursor(Qt.PointingHandCursor)
        self.jump.clicked.connect(self.to_bottom)
        self.jump.hide()
        # Top right: open the chat in a window of its own (task 92).
        self.pop = QPushButton("⧉", self)
        self.pop.setToolTip("Pop out chat: open it in a window of its own "
                            "too")
        self.pop.setCursor(Qt.PointingHandCursor)
        self.pop.setFixedSize(26, 26)
        self.pop.clicked.connect(self.pop_out.emit)
        self.pop.setVisible(can_pop)
        self.restyle()
        self.text.verticalScrollBar().valueChanged.connect(self._scrolled)

        # Pictures that arrive are laid out together, a moment later;
        # pronouns and BetterTTV's lists redraw the lines.
        self._relayout = QTimer(self)
        self._relayout.setSingleShot(True)
        self._relayout.setInterval(150)
        self._relayout.timeout.connect(self._do_relayout)
        self._redraw = QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(400)
        self._redraw.timeout.connect(self._rebuild)
        self.extras.emotes_arrived.connect(self._emotes_arrived)
        self.extras.pronouns_arrived.connect(self._pronouns_arrived)
        self.extras.bttv_changed.connect(self._redraw_soon)
        self.extras.badges_changed.connect(self._redraw_soon)
        self.extras.rules_changed.connect(lambda _c: self._update_bar())

    def restyle(self):
        self.text.setStyleSheet(
            f"QTextBrowser {{ background: transparent; border: none;"
            f" color: {T['text']}; font-size: {self.SIZE}px; }}")
        jump = getattr(self, "jump", None)
        if jump is not None:
            jump.setStyleSheet(
                f"QPushButton {{ background: {T['accent']};"
                f" color: {T.get('on_accent', '#000')}; border: none;"
                f" border-radius: 12px; padding: 4px 14px;"
                f" font-size: 12px; }}")
        bar = getattr(self, "bar", None)
        if bar is not None:
            bar.restyle()
        card = getattr(self, "rules_card", None)
        if card is not None:
            card.restyle()
        pop = getattr(self, "pop", None)
        if pop is not None:
            pop.setStyleSheet(
                f"QPushButton {{ background: {T['bg2']}; color: {T['text2']};"
                f" border: none; border-radius: 13px; font-size: 14px;"
                f" padding: 0; }}"
                f"QPushButton:hover {{ color: {T['accent']}; }}")

    # -- what comes in ------------------------------------------------------

    def clear(self):
        self._items.clear()
        self._status = ""
        self._forget_pictures()
        self.text.clear()
        self.jump.hide()

    def load(self, items, status=""):
        """Start from another view's lines (the pop-out window)."""
        self._items.clear()
        self._items.extend(items)
        self._status = status or ""
        self._rebuild()
        self.to_bottom()

    def set_status(self, text):
        """"Connecting to chat…" and the like, shown while there is
        nothing else to read; "" once joined."""
        self._status = text or ""
        if not self._items:
            self._rebuild()

    def add_messages(self, items):
        if not items:
            return
        bar = self.text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        if not self._items:
            self.text.clear()           # drop the status line
            self._forget_pictures()
        self._items.extend(items)
        self._append(items)
        if at_bottom:
            self.to_bottom()
        else:
            self._place_jump()
            self.jump.show()
            self.jump.raise_()

    def add_note(self, text):
        """A grey line from Twitch or the app ("Your message was not
        sent…"), in among the messages."""
        if text:
            self.add_messages([{"system": True, "text": text, "login": "",
                                "id": ""}])

    def set_writer(self, login, signed_in=None):
        """You can type as `login`; "" = read only for now. `signed_in`
        is the saved sign-in, for the bar and the right-click menu."""
        self._writer = login or ""
        if signed_in is not None:
            self._signed_in = signed_in or ""
        self._update_bar()

    def set_live(self, on, channel=""):
        """A chat is on (the bar shows) or not (task 95)."""
        self._live = bool(on)
        self.bar.channel = channel or ""
        self._update_bar()

    def _update_bar(self):
        import twitchauth
        self.bar.set_state(self._writer, self._signed_in, self._live,
                           can_sign_in=bool(twitchauth.client_id()))
        if self._writer:
            self.entry.setPlaceholderText(f"Send a message as {self._writer}")
        # Rules not OK'd yet: the box shows them instead of taking text.
        pending = self._rules_pending()
        self.entry.setReadOnly(pending)
        if not pending and self.rules_card.isVisible() and \
                not getattr(self, "_rules_viewing", False):
            self.rules_card.hide()
        if not self._writer:
            self.rules_card.hide()
        self.suggest.hide()
        self._place_jump()

    # -- chat rules (task 95) -----------------------------------------------

    def _rules_pending(self):
        return bool(self._writer and self._live and
                    self.extras.rules_pending(self.bar.channel))

    def show_rules(self, viewing=False):
        """The rules card: before your first message (`viewing` False),
        or from ⚙ > View chat rules."""
        rules = self.extras.chan_rules(self.bar.channel)
        if not rules:
            return
        self._rules_viewing = viewing
        self.suggest.hide()
        self.rules_card.show_rules(self.bar.channel, rules)
        self._place_rules()
        if not viewing:
            import crashlog
            crashlog.write(f"[chat] rules: shown for '{self.bar.channel}' "
                           f"before writing ({len(rules)})")

    def _place_rules(self):
        width = max(120, self.bar.width())
        h = min(self.rules_card.fit(width),
                max(80, self.bar.geometry().top() - 8))
        self.rules_card.setGeometry(self.bar.x(),
                                    max(0, self.bar.geometry().top() - h - 4),
                                    width, h)
        self.rules_card.raise_()

    def _rules_ok(self):
        viewing = getattr(self, "_rules_viewing", False)
        self._rules_viewing = False
        if not viewing and self._rules_pending():
            self.rules_accepted.emit(self.bar.channel)
        self.entry.setFocus()

    # -- the ":" list (task 95) ---------------------------------------------

    def _suggest(self):
        self._suggest_names = False
        mention = self.bar.typed_mention()
        if mention is not None:
            self._suggest_names = True
            rows = [("", "@" + n) for n in self._names_like(mention)]
            blocked = {}
            word = "@" + mention
        else:
            word = self.bar.typed_word()
            rows = self.extras.suggestions(word, channel=self.bar.channel) \
                if word else []
            # Emotes you can use here first; the others stay, grayed.
            here = self.bar.channel
            rows.sort(key=lambda r: bool(self.extras.why_not(r[0], here)))
            blocked = {c: w for k, c in rows
                       for w in [self.extras.why_not(k, here)] if w}
        was = self.suggest.isVisible()
        self.suggest.show_rows(rows, blocked)
        if rows:
            self._place_suggest()
            if not was:
                self._suggest_shown = getattr(self, "_suggest_shown", 0) + 1
                if self._suggest_shown <= 3:
                    import crashlog
                    crashlog.write(f"[chat] suggestion list shown for "
                                   f"'{word}' ({len(rows)} match(es))")

    def _names_like(self, typed, limit=6):
        """Names seen in this chat (newest talker first) that start with,
        then hold, what was typed after "@" (task 103). Not you; the
        channel's own name too, if it hasn't spoken."""
        typed = typed.lower()
        me = {(self._writer or "").lower(), self.bar.me_name.lower()} - {""}
        seen, names = set(), []
        for item in reversed(self._items):
            login = (item.get("login") or "").lower()
            if not login or item.get("system") or login in me or \
                    login in seen:
                continue
            seen.add(login)
            names.append(item.get("name") or login)
        host = self.bar.channel
        if host and host.lower() not in seen and host.lower() not in me:
            names.append(host)
        starts = [n for n in names if n.lower().startswith(typed)]
        holds = [n for n in names if typed in n.lower() and n not in starts]
        return (starts + holds)[:limit]

    def mention(self, name):
        """"@name " into the message box (task 103)."""
        if self.bar.mode() != "write" or not name:
            return
        if self._rules_pending():
            self.show_rules()
            return
        self.bar.insert_emote("@" + name)


    def _place_suggest(self):
        top = self.bar.geometry().top()
        h = self.suggest.height()
        self.suggest.setGeometry(self.bar.x(), max(0, top - h - 4),
                                 self.bar.width(), h)
        self.suggest.raise_()

    def _take_suggestion(self, code):
        if getattr(self, "_suggest_names", False):
            self.bar.replace_mention(code)
        else:
            self.bar.replace_word(code)
        self.suggest.hide()
        self.entry.setFocus()

    def eventFilter(self, obj, e):
        if obj is self.entry and self._rules_pending() and e.type() in (
                QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.FocusIn):
            if not self.rules_card.isVisible():
                self.show_rules()
            if e.type() != QEvent.FocusIn:
                return True
        if obj is self.entry and e.type() == QEvent.KeyPress and \
                self.suggest.isVisible():
            key = e.key()
            if key == Qt.Key_Up:
                self.suggest.move_by(-1)
                return True
            if key == Qt.Key_Down:
                self.suggest.move_by(1)
                return True
            if key in (Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter):
                code = self.suggest.chosen()
                if code:
                    self._take_suggestion(code)
                    return True
                if key == Qt.Key_Tab:           # a grayed one: nothing
                    return True
            if key == Qt.Key_Escape:
                self.suggest.hide()
                return True
        if obj is self.entry and e.type() == QEvent.FocusOut:
            self.suggest.hide()
        return super().eventFilter(obj, e)

    def remove(self, kind, key):
        """A moderator removed a message, a person's lines, or all."""
        before = len(self._items)
        if kind == "all":
            self._items.clear()
        elif kind == "message":
            kept = [i for i in self._items if i.get("id") != key]
            self._items.clear()
            self._items.extend(kept)
        elif kind == "user":
            kept = [i for i in self._items if i.get("login") != key]
            self._items.clear()
            self._items.extend(kept)
        if len(self._items) != before:
            self._rebuild()

    def _rebuild(self):
        self._redraw.stop()
        bar = self.text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        from_bottom = bar.maximum() - bar.value()
        self._forget_pictures()
        if not self._items:
            self.text.setHtml(
                f'<p style="color: {T["text2"]};">'
                f"{_esc(self._status)}</p>" if self._status else "")
            return
        self.text.clear()
        self._append(self._items)
        if at_bottom:
            self.to_bottom()
        else:
            bar.setValue(max(0, bar.maximum() - from_bottom))

    def _append(self, items):
        """One block per message, so the document's block cap keeps
        exactly the newest KEEP."""
        from PyQt5.QtGui import QTextBlockFormat, QTextCursor
        spaced = QTextBlockFormat()
        spaced.setBottomMargin(5)
        spaced.setLineHeight(125, QTextBlockFormat.ProportionalHeight)
        cursor = QTextCursor(self.text.document())
        cursor.movePosition(QTextCursor.End)
        # One edit: the chat is laid out once for the whole batch, not
        # once per line (build 81).
        cursor.beginEditBlock()
        for item in items:
            # Whose line it is, and whether it has its pronoun tag, so a
            # pronoun that arrives later goes into that line alone.
            spaced.setProperty(_LOGIN_PROP, item.get("login") or "")
            spaced.setProperty(_NAME_PROP, item.get("name") or
                               item.get("login") or "")
            spaced.setProperty(_PILL_PROP, bool(self._pill(item)))
            if self._mentions_me(item):             # task 103
                shade = QColor(T["accent"])
                shade.setAlpha(46)
                spaced.setBackground(QBrush(shade))
            else:
                spaced.clearBackground()
            if not self.text.document().isEmpty():
                cursor.insertBlock(spaced)
            cursor.insertHtml(self._line(item))
            cursor.mergeBlockFormat(spaced)     # the first one too
        cursor.endEditBlock()
        self.extras.want_pronouns(items)        # asks only the new ones

    def _mentions_me(self, item):
        """Does this line say "@you"? (Not your own lines.)"""
        if item.get("system") or item.get("mine"):
            return False
        text = (item.get("text") or "").lower()
        if "@" not in text:
            return False
        import re
        for name in {(self._writer or "").lower(), self.bar.me_name.lower()}:
            if name and re.search(r"(?<![\w])@" + re.escape(name) +
                                  r"(?![\w])", text):
                return True
        return False

    # -- emote pictures (task 92) ---------------------------------------------

    def _forget_pictures(self):
        self._used.clear()
        self._animated.clear()
        self._frame_shown.clear()

    def _emote_resource(self, key):
        """What the text shows for an emote: its picture, or an empty
        square of the same height while it downloads."""
        from PyQt5.QtGui import QPixmap
        pm = self.extras.pixmap(key)
        if pm is None:
            import chatextras
            dpr = self.extras.dpr()
            side = round(chatextras.picture_px(key) * dpr)
            pm = QPixmap(side, side)
            pm.fill(Qt.transparent)
            pm.setDevicePixelRatio(dpr)
            return pm
        if self.extras.animated(key) and key not in self._animated:
            self._animated.add(key)
            self.extras.watch(self)
        return pm

    def _emotes_arrived(self, keys):
        mine = self._used.intersection(keys)
        if not mine:
            return
        if any(self.extras.failed(k) for k in mine):
            self._redraw_soon()         # those show their names instead
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QTextDocument
        doc = self.text.document()
        for key in mine:
            pm = self.extras.pixmap(key)
            if pm is None:
                continue
            doc.addResource(QTextDocument.ImageResource,
                            QUrl("emote:" + key), pm)
            if self.extras.animated(key):
                self._animated.add(key)
                self.extras.watch(self)
        self._to_lay_out.update(mine)
        if not self._relayout.isActive():
            self._relayout.start()

    def _redraw_soon(self):
        # Not restarted while it runs: a busy chat would put it off for
        # ever.
        if not self._redraw.isActive():
            self._redraw.start()

    def _do_relayout(self):
        """Lines with emotes that arrived take their real width. Only
        those lines are laid out again: the whole chat took ~100 ms."""
        names = {"emote:" + k for k in self._to_lay_out}
        self._to_lay_out.clear()
        bar = self.text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        doc = self.text.document()
        block = doc.firstBlock()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fmt = it.fragment().charFormat()
                if fmt.isImageFormat() and \
                        fmt.toImageFormat().name() in names:
                    doc.markContentsDirty(block.position(), block.length())
                    break
                it += 1
            block = block.next()
        if at_bottom:
            self.to_bottom()

    def _animate(self, now_ms):
        """One step of the animated emotes; False when there is nothing
        to move (hidden, or none on show)."""
        if not self._animated or not self.isVisible():
            return False
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QTextDocument
        doc = self.text.document()
        changed = False
        for key in self._animated:
            i = self.extras.frame_index(key, now_ms)
            if self._frame_shown.get(key) != i:
                self._frame_shown[key] = i
                doc.addResource(QTextDocument.ImageResource,
                                QUrl("emote:" + key),
                                self.extras.pixmap(key, now_ms))
                changed = True
        if changed:
            self.text.viewport().update()
        return True

    def showEvent(self, e):
        super().showEvent(e)
        if self._animated:
            self.extras.watch(self)     # the timer stops while hidden

    def _pronouns_arrived(self, logins):
        """Pronoun tags go into the lines of the people they are for,
        in place (redrawing the whole chat took ~150 ms)."""
        import chatextras
        if not chatextras.option("chat_pronouns"):
            return
        wanted = set(logins)
        if not any(i.get("login") in wanted for i in self._items):
            return
        from PyQt5.QtGui import QTextBlockFormat, QTextCursor
        bar = self.text.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        from_bottom = bar.maximum() - bar.value()
        doc = self.text.document()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        mark = QTextBlockFormat()
        mark.setProperty(_PILL_PROP, True)
        block = doc.firstBlock()
        while block.isValid():
            fmt = block.blockFormat()
            login = fmt.property(_LOGIN_PROP)
            if login in wanted and not fmt.property(_PILL_PROP):
                pill = self._pill({"login": login})
                if pill:
                    cursor.setPosition(block.position())
                    cursor.insertHtml(pill)
                    cursor.mergeBlockFormat(mark)
            block = block.next()
        cursor.endEditBlock()
        if at_bottom:
            self.to_bottom()
        else:
            bar.setValue(max(0, bar.maximum() - from_bottom))

    # -- right-click ----------------------------------------------------------

    SWITCHES = (("chat_badges", "Show badges"),
                ("chat_emotes", "Show emotes"),
                ("chat_animated", "Show animated emotes"),
                ("chat_pronouns", "Show pronouns"))

    def _menu(self, global_pos, login="", name="", emote=None):
        menu = panel_menu(self)
        if login and name:                      # task 103
            me = {(self._writer or "").lower(), self.bar.me_name.lower()}
            if login.lower() not in me:
                mention = menu.addAction(f"Mention @{name}")
                mention.setEnabled(self.bar.mode() == "write")
                mention.triggered.connect(lambda: self.mention(name))
        if emote and emote[0].startswith(("tw:", "bttv:")):     # task 106
            pinned = self.extras.is_pinned(emote[0])
            pin = menu.addAction("Unpin from the bar" if pinned
                                 else "Pin to the bar")
            pin.triggered.connect(
                lambda: self.extras.unpin(emote[0]) if pinned
                else self.extras.pin(emote[0], emote[1]))
        if menu.actions():
            menu.addSeparator()
        copy = menu.addAction("Copy")
        copy.setEnabled(self.text.textCursor().hasSelection())
        copy.triggered.connect(self.text.copy)
        if self.can_pop:
            menu.addSeparator()
            menu.addAction("Pop out chat").triggered.connect(
                self.pop_out.emit)
        menu.addSeparator()
        # Sign-out is in Settings > Twitch chat only (task 96).
        if not self._signed_in:
            menu.addAction("Sign in to chat…").triggered.connect(
                self.sign_in_asked.emit)
            menu.addSeparator()
        self._add_switches(menu)
        menu.exec_(global_pos)

    def _add_switches(self, menu):
        import chatextras
        for key, label in self.SWITCHES:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(chatextras.option(key))
            act.toggled.connect(
                lambda on, key=key: self.option_toggled.emit(key, on))

    def _options_menu(self, button_pos):
        """The bar's ⚙ button: chat options (task 95), opening upwards
        from the button like Twitch's."""
        menu = panel_menu(self)
        title = menu.addAction("Chat options")
        title.setEnabled(False)
        menu.addSeparator()
        self._add_switches(menu)
        menu.addSeparator()
        if self.can_pop:
            menu.addAction("Pop out chat").triggered.connect(
                self.pop_out.emit)
        if not self._signed_in:
            menu.addAction("Sign in to chat…").triggered.connect(
                self.sign_in_asked.emit)
        rules = menu.addAction("View chat rules")
        rules.setEnabled(bool(self.extras.chan_rules(self.bar.channel)))
        rules.triggered.connect(lambda: self.show_rules(viewing=True))
        menu.addAction("Twitch chat settings…").triggered.connect(
            self.settings_asked.emit)
        size = menu.sizeHint()
        menu.exec_(QPoint(button_pos.x() + self.bar.options_button.width()
                          - size.width(), button_pos.y() - size.height() - 4))

    # -- scrolling ------------------------------------------------------------

    def to_bottom(self):
        bar = self.text.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.jump.hide()

    def _scrolled(self, value):
        bar = self.text.verticalScrollBar()
        if value >= bar.maximum() - 4:
            self.jump.hide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_jump()
        if self.suggest.isVisible():
            self._place_suggest()
        if self.rules_card.isVisible():
            self._place_rules()
        self.pop.move(max(0, self.width() - self.pop.width() - 14), 4)
        self.pop.raise_()

    def _place_jump(self):
        self.jump.adjustSize()
        w = self.jump.width()
        bottom = self.text.geometry().bottom() if self.bar.isVisible() \
            else self.height()
        self.jump.move(max(0, (self.width() - w) // 2),
                       max(0, bottom - self.jump.height() - 8))

    # -- one line -------------------------------------------------------------

    def _line(self, item):
        import chatextras
        if item.get("system"):
            return (f'<span style="color:{T["text2"]}; font-style:italic;">'
                    f'{_esc(item.get("text", ""))}</span>')
        name = _esc(item.get("name") or item.get("login") or "?")
        colour = chat_colour(item.get("colour", ""),
                             item.get("login") or item.get("name") or "")
        text = self._rich_text(item)
        pronoun = self._pill(item)
        badges = self._badges(item)
        if item.get("action"):
            return (f'{pronoun}{badges}<span style="color:{colour}; '
                    f'font-style:italic;">{name} {text}</span>')
        return (f'{pronoun}{badges}<span style="color:{colour}; '
                f'font-weight:600;">{name}</span>'
                f'<span style="color:{T["text"]};">: {text}</span>')

    def _badges(self, item):
        """The sender's chat badges as small pictures (task 95). Only the
        ones Twitch has a picture for; their title on hover."""
        import chatextras
        if not chatextras.option("chat_badges") or not item.get("badges"):
            return ""
        out = []
        for badge in chatextras.badge_list(item["badges"])[:4]:
            info = chatextras.badge_info(badge, self.bar.channel)
            if not info:
                continue
            key = "bdg:" + badge
            if self.extras.failed(key):
                continue
            self._used.add(key)
            self.text.codes[key] = info[1]
            out.append(f'<img src="emote:{_esc(key)}" '
                       f'height="{chatextras.BADGE_PX}" align="middle" '
                       f'alt="">&nbsp;')
        return "".join(out)

    def _pill(self, item):
        """The sender's pronouns as a small grey tag, or ""."""
        import chatextras
        if not chatextras.option("chat_pronouns"):
            return ""
        label = self.extras.pronoun(item.get("login") or "")
        if not label:
            return ""
        return (f'<span style="color:{T["text2"]};'
                f' background-color:{T["bg2"]};'
                f' font-size:12px; font-weight:normal; font-style:normal;">'
                f"&nbsp;{_esc(label)}&nbsp;</span>&nbsp;")

    def _rich_text(self, item):
        """The message as HTML, emotes as pictures (task 92)."""
        import chatextras
        raw = item.get("text", "")
        twitch = chatextras.option("chat_emotes")
        bttv = self.extras.bttv(self.bar.channel)
        # Your own lines have no emote tag: Twitch emotes by name.
        known = chatextras.LEARNED if (twitch and item.get("mine")) else None
        if not (twitch and item.get("emotes")) and not bttv and not known:
            return _linkify(_esc(raw))
        out = []
        for part in chatextras.split_message(raw, item.get("emotes", ""),
                                             bttv, twitch=twitch,
                                             known_twitch=known):
            if part[0] == "text":
                out.append(_linkify(_esc(part[1])))
                continue
            key, code = part[1], part[2]
            if self.extras.failed(key):
                out.append(_esc(code))
                continue
            self._used.add(key)
            self.text.codes[key] = code
            out.append(f'<img src="emote:{_esc(key)}" '
                       f'height="{chatextras.EMOTE_PX}" align="middle" '
                       f'alt="{_esc(code)}">')
        return "".join(out)


class ChatWindow(QWidget):
    """The chat in a window of its own (build 81, task 92).

    "Pop out chat" opens it; the side panel keeps its own chat as well,
    and both are fed the same messages. It stays open across station
    changes (empty, with a line saying so, while no Twitch station
    plays). Closing it only hides it; the main window deletes it at quit.
    It doesn't keep the app running on its own (WA_QuitOnClose off, like
    the visualiser's windows, task 69).
    """

    closed = pyqtSignal()

    def __init__(self):
        super().__init__(None, Qt.Window)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("chatWindow")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 8)
        lay.setSpacing(0)
        self.chat = ChatView(self, can_pop=False)
        lay.addWidget(self.chat, 1)
        self.restyle()
        self.resize(380, 640)
        self.set_channel("")

    def restyle(self):
        self.setStyleSheet(f"#chatWindow {{ background: {T['bg0']}; }}")
        self.chat.restyle()

    def set_channel(self, channel):
        self.setWindowTitle(f"Chat — {channel}" if channel else "Chat")

    def closeEvent(self, e):
        self.closed.emit()
        super().closeEvent(e)


# Twitch's own colours for people who never picked one.
_TWITCH_DEFAULTS = ("#FF0000", "#0000FF", "#008000", "#B22222", "#FF7F50",
                    "#9ACD32", "#FF4500", "#2E8B57", "#DAA520", "#D2691E",
                    "#5F9EA0", "#1E90FF", "#FF69B4", "#8A2BE2", "#00FF7F")


def chat_colour(colour, login):
    """The sender's colour, made readable on the dark panel."""
    c = QColor(colour) if colour else QColor()
    if not c.isValid():
        c = QColor(_TWITCH_DEFAULTS[sum(map(ord, login or "?"))
                                    % len(_TWITCH_DEFAULTS)])
    h, sat, light, _a = c.getHsl()
    if light < 140:                     # dark blue on near-black: lift it
        c.setHsl(max(h, 0), sat, 140 + light // 4)
    return c.name()


def _esc(text):
    import html
    return html.escape(text or "", quote=True)


def _linkify(escaped):
    import re
    return re.sub(r"(https?://[^\s<]+)",
                  lambda m: f'<a href="{m.group(1)}" style="color:{T["text2"]};">'
                            f"{m.group(1)}</a>", escaped)


class LyricsColumn(QWidget):
    """The lyrics, with a line underneath saying where they came from.

    A thin wrapper rather than a change to LyricsView: the full-screen
    view uses that class too and does not want a caption.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, SWITCH_BAND)
        lay.setSpacing(4)
        self.busy = Ellipsis()
        self.busy.setContentsMargins(0, 10, 0, 0)
        lay.addWidget(self.busy)
        self.view = LyricsView()
        self.view.setContentsMargins(14, 0, 8, 0)
        lay.addWidget(self.view, 1)
        # A Twitch station's chat takes the lyrics' place (task 78).
        self.chat = ChatView()
        self.chat.hide()
        lay.addWidget(self.chat, 1)
        self.chat_on = False
        # Inside the scrolled content, at the end of it. A credit pinned to
        # the panel is on screen for the whole song; put it after the last
        # line and it is there when you go looking and not before.
        self.source = self.view.footer

    def show_chat(self, on):
        """Chat in place of the lyrics (True), or the lyrics back."""
        self.chat_on = bool(on)
        self.view.setVisible(not self.chat_on)
        self.chat.setVisible(self.chat_on)
        if self.chat_on:
            self.busy.stop()

    def set_lyrics(self, lines, message=None):
        self.view.set_lyrics(lines, message)

    def set_position(self, ms):
        self.view.set_position(ms)

    def resync_signal(self):
        """Emitted when a line is double-clicked, with its start."""
        return self.view.resync_requested

    def reaccent(self):
        self.view.reaccent()
        self.chat.restyle()

    def set_sources(self, text=""):
        # Rich text, so a credit can carry a link.
        self.source.setTextFormat(Qt.RichText)
        self.source.setOpenExternalLinks(True)
        self.source.setText(text or "")


def _thumb(glyph, tip):
    from shell import tool_button
    button = tool_button(glyph, tip, 16, checkable=True)
    button.setProperty("thumb", True)
    return button


class ArtistView(QScrollArea):
    verdict_given = pyqtSignal(str)             # "up" or "down"
    artist_corrected = pyqtSignal(str)          # name whose override changed

    """Photo, name, the tags they're filed under, and the biography."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # No vertical bar either: the biography is sized to fit, so a bar
        # would never appear — but the space reserved for it left a strip
        # of column showing down the side of the photo.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; }")

        body = QWidget()
        self.viewport().setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        # No side margins on the column itself: the photo runs to both
        # edges the way it does in the single-panel layout, and the text
        # under it is inset instead. The bottom margin is the band the
        # switch occupies — reserved here once, rather than every item in
        # the column padding itself clear of it separately.
        lay.setContentsMargins(0, 0, 0, SWITCH_BAND)
        lay.setSpacing(10)

        # Its own header, used only when the panel has split in two: the
        # photo belongs to the artist column, not above both of them, or
        # the lyrics end up under a picture that has nothing to do with
        # what they are.
        self.header = ArtistHeader()
        self.header.setVisible(False)
        lay.addWidget(self.header)

        # No name here: the panel header above already carries it, and
        # printing it twice wasted the width the biography needs.
        self.genres = QLabel("")
        self.genres.setProperty("role", "secondary")
        self.genres.setWordWrap(True)

        # Big text, as the sketch has it: this is the one place a
        # biography is meant to be read rather than glanced at.
        # Empty until there is something. The dots say it is working.
        self.bio = QLabel("")
        # Right-click the words, or the picture, to correct either.
        #
        # Both point at the same artist, so both open the same editor —
        # having to remember which half of the panel accepts a
        # correction would be an arbitrary thing to know.
        # Dev builds only: a user build has no menu here at all (task 200).
        if devmode.is_dev():
            self.bio.setContextMenuPolicy(Qt.CustomContextMenu)
            self.bio.customContextMenuRequested.connect(self._artist_menu)
        else:
            self.bio.setContextMenuPolicy(Qt.NoContextMenu)
        self.bio.setProperty("role", "body")
        self.bio.setMinimumWidth(180)
        self.bio.setWordWrap(True)
        self.bio.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.bio.setTextFormat(Qt.RichText)
        self.bio.setOpenExternalLinks(True)

        # Only the words are inset; the picture is not.
        for widget in (self.genres, self.bio):
            widget.setContentsMargins(14, 0, 12, 0)
        self.busy = Ellipsis()
        lay.addWidget(self.busy)
        lay.addWidget(self.genres)
        # The biography takes the slack. A stretch under it would push it
        # to the top and leave the space it was just sized to fill empty.
        lay.addWidget(self.bio, 1)

        # Whose words these are, and whose picture.
        #
        # A biography scraped from one place and a photograph found in
        # another shouldn't both read as things the app simply knows.
        self.source = QLabel("")
        self.source.setProperty("role", "tertiary")
        self.source.setWordWrap(True)
        # Clear of the Lyrics/Artist/Both plate, which floats over the
        # bottom of the column and was sitting on top of this.
        self.source.setContentsMargins(14, 0, 12, 4)
        lay.addWidget(self.source)

        # A lookup keyed on a name gets the wrong act often enough that
        # there has to be a way to say so — short names, tribute bands,
        # two acts sharing one name.
        # "Is this right?" is the way to fix it; the thumbs are the way to
        # say so without doing the work, which is most of the time.
        verdict = QHBoxLayout()
        verdict.setContentsMargins(14, 0, 12, 6)
        self._verdict_row = verdict
        verdict.setSpacing(8)

        # A label, not a button.
        #
        # It was a link: underlined, highlighted, and it opened a window
        # nobody asked for. It is a question introducing the two buttons
        # beside it, and a question is not a control.
        self.wrong = QLabel("Is this right?")
        self.wrong.setProperty("role", "secondary")
        self.wrong.setTextInteractionFlags(Qt.NoTextInteraction)
        verdict.addWidget(self.wrong)

        self.thumb_up = _thumb("thumb_up", "")
        self.thumb_down = _thumb("thumb_down", "")
        self.thumb_up.toggled.connect(
            lambda on: self._verdict(self.thumb_up, self.thumb_down, on))
        self.thumb_down.toggled.connect(
            lambda on: self._verdict(self.thumb_down, self.thumb_up, on))
        verdict.addWidget(self.thumb_up)
        verdict.addWidget(self.thumb_down)
        verdict.addStretch(1)
        lay.addLayout(verdict)

        self.setWidget(body)

    GOOD = "#4fc3a1"
    BAD = "#e0574f"

    def _verdict(self, pressed, other, on):
        """One or the other, never both.

        A verdict is a single answer, so pressing one releases the other
        rather than leaving two contradictory marks lit.
        """
        if on and other.isChecked():
            other.blockSignals(True)
            other.setChecked(False)
            other.blockSignals(False)
        self._paint_thumbs()
        if on:
            self.verdict_given.emit(
                "down" if pressed is self.thumb_down else "up")

    def _paint_thumbs(self):
        for button, glyph, colour in (
                (self.thumb_up, "thumb_up", self.GOOD),
                (self.thumb_down, "thumb_down", self.BAD)):
            on = button.isChecked()
            button.setIcon(icon(glyph + ("_on" if on else ""), 16,
                                colour if on else T["text3"]))

    def clear_verdict(self):
        """A new artist has not been judged yet."""
        for button in (self.thumb_up, self.thumb_down):
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)
        self._paint_thumbs()

    def set_header(self, name, photo, visible):
        """Shown whenever there is an artist, photograph or not.

        Requiring a photo meant an artist with no picture got no header at
        all — and the tower exists precisely so that case still has
        something in it.
        """
        self.header.set_name(name)
        self.header.set_photo(photo)
        self.header.setVisible(bool(visible) and bool(name))
        # Remember what is on screen, so the crop action has something to
        # work on and something to put back.
        self.header._name = name
        self.header._split = bool(visible)

    # No practical ceiling: the point is to fill the column, and a short
    # biography in a tall panel legitimately wants to be large.
    # 26, not 44.
    #
    # The fit grew the text until it filled the column, so a two-sentence
    # biography came out in headline type — technically "fitting", and
    # unreadable next to lyrics at 20. A ceiling means a short biography
    # simply leaves space at the bottom, which is what space is for.
    BIO_MIN, BIO_MAX = 13, 26

    def _fit_bio(self):
        """Size the biography to fill its column.

        A two-line paragraph set at the same size as a twelve-line one
        leaves most of the panel empty. Choosing the largest size that
        still fits means a short biography reads as a statement and a long
        one still fits without scrolling.
        """
        text = self.bio.text()
        if not text:
            return
        width = max(120, self.viewport().width() - 24)
        # Everything the biography is not: the photo, the genre line, the
        # credit at the bottom, and the spacing between them.
        # Generous, deliberately. The measurement is of a rectangle the
        # text would occupy, and real wrapping rounds up — leaving it
        # tight put the last line under the credit.
        taken = (self.header.height() + self.genres.sizeHint().height()
                 + self.source.sizeHint().height() + 64)
        height = max(80, self.viewport().height() - taken)

        font = QFont(self.bio.font())
        best = self.BIO_MIN
        for size in range(self.BIO_MIN, self.BIO_MAX + 1):
            font.setPixelSize(size)
            needed = QFontMetrics(font).boundingRect(
                0, 0, width, 10000, Qt.TextWordWrap, text).height()
            if needed <= height:
                best = size
            else:
                break
        # Set through the stylesheet, not setFont.
        #
        # A QSS font-size beats anything setFont does, and role="body"
        # sets one — so every size this measured was discarded the moment
        # it was applied, which is why the text never grew.
        font.setPixelSize(best)
        self.bio.setFont(font)
        self.bio.setStyleSheet(f"font-size: {best}px; background: transparent;")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.header.fit_to(self.viewport().height())
        self._refit_soon()

    def _refit_soon(self):
        """Measure after the layout has settled.

        Sizing the text during a resize measures against the geometry the
        widgets had a moment ago — the header has not been re-laid out
        yet, so the space left for the biography looks smaller than it
        turns out to be and the text is set too small.
        """
        QTimer.singleShot(0, self._fit_bio)

    def set_sources(self, text=""):
        # Rich text, so a credit can carry a link.
        self.source.setTextFormat(Qt.RichText)
        self.source.setOpenExternalLinks(True)
        self.source.setText(text or "")

    def _show_verdict(self, on):
        """The thumbs and "Is this right?" belong to a biography - an
        artist's, not a talk station's own info (task 49)."""
        if getattr(self, "_station_editor", None) is not None \
                or getattr(self, "_station_locked", False):
            on = False
        for widget in (self.wrong, self.thumb_up, self.thumb_down):
            widget.setVisible(bool(on))

    def _artist_menu(self, pos):
        """Right-clicking the biography opens the correction editor.

        ArtistHeader carries its own copy for the photograph: they are
        separate widgets in separate classes, and each holds the handler
        for what it shows rather than reaching into the other.
        """
        if not devmode.is_dev() \
                or getattr(self, "_station_locked", False):
            return          # a talk station's info ships with the app
        menu = panel_menu(self)
        edit = menu.addAction("Edit this artist…")
        edit.setEnabled(bool((getattr(self, "_name", "") or "").strip()))
        chosen = menu.exec_(self.bio.mapToGlobal(pos))
        menu.deleteLater()
        if chosen is edit:
            self._edit_artist()

    def _edit_artist(self):
        if not devmode.is_dev():
            return
        from artistedit import ArtistEditDialog

        # The artist as identified, not as displayed.
        #
        # A correction can rename the artist on screen ("Show as"), and
        # this used to take whatever name was showing. So the second edit
        # was filed under the display name: the editor opened empty, the
        # save went to a new entry, and the next identification brought
        # the old text straight back.
        # A talk station in a dev build: the station's info, not an
        # artist (task 49).
        editor = getattr(self, "_station_editor", None)
        if editor is not None:
            editor()
            return
        if getattr(self, "_station_locked", False):
            return
        name = (getattr(self, "_edit_key", "") or getattr(self, "_name", "")
                or "").strip()
        if not name:
            return
        if ArtistEditDialog(name, self).exec_():
            self.artist_corrected.emit(name)

    def set_artist(self, name, genres="", biography=""):
        # Kept so the correction menu knows who is being shown.
        self._name = (name or "").strip()
        self.genres.setText(genres or "")
        # Empty rather than an apology: "no biography found" before a
        # song has even been identified reads as a failure, when in fact
        # nothing has been looked for yet.
        self.bio.setText(biography or "")
        self._show_verdict(bool(biography))
        self._refit_soon()


class Ellipsis(QWidget):
    """Three dots that fill in turn, for a lookup in progress.

    No words: "Looking up…" is the same sentence in both columns and says
    nothing the movement does not. Dots are read as waiting in any
    language and take a line rather than a paragraph.
    """

    DOTS = 3
    # Short frames and a counted cycle, so the dots breathe rather than
    # blink — the same shape of animation as the antenna's waves.
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
        self.setFixedHeight(18)
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
        from PyQt5.QtCore import QRectF
        from PyQt5.QtGui import QColor, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r, gap = 3.0, 11.0
        for i in range(self.DOTS):
            # Each dot on its own sine, a third of a cycle apart, so the
            # brightness travels along the row instead of stepping.
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
            radius = r * (0.85 + 0.25 * glow)
            p.drawEllipse(QRectF(14 + i * gap - (radius - r),
                                 self.height() / 2 - radius,
                                 radius * 2, radius * 2))
        p.end()


class ArtistHeader(QWidget):
    # The photograph's menu edits the same correction as the biography's,
    # so it needs the same signal — it was emitting one that only the
    # other class had, and every use of "Edit this artist…" from the
    # picture raised instead of saving.
    artist_corrected = pyqtSignal(str)
    # The file that was just cropped, so the panel can show the new
    # picture in both headers (single column and split), not only in the
    # one that was right-clicked.
    photo_cropped = pyqtSignal(str)

    """A wide photo with the artist's name laid along its bottom."""

    # The photo is the fastest thing to read in the panel, so it gets
    # room. Sized as a fraction of the panel rather than a constant, so a
    # tall window gives it more rather than leaving a gap under it.
    HEIGHT = 300
    MIN_HEIGHT = 190

    def __init__(self, parent=None, tone=None):
        super().__init__(parent)
        self.setFixedHeight(0)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._tone = tone or T["bg1"]

        self.photo = QLabel(self)
        self.photo.setScaledContents(False)
        self.photo.setAlignment(Qt.AlignCenter)
        # Right-click the picture to fix it.
        #
        # Press shots are landscape and the panel shows a band across the
        # top, so the crop it arrives with is frequently the wrong part of
        # the picture — somebody's elbow rather than their face.
        if devmode.is_dev():
            self.photo.setContextMenuPolicy(Qt.CustomContextMenu)
            self.photo.customContextMenuRequested.connect(self._photo_menu)
        else:
            self.photo.setContextMenuPolicy(Qt.NoContextMenu)

        # Stands in when there is no photograph.
        self.tower = TowerMark(self)
        self.tower.setVisible(False)

        self.name = QLabel("", self)
        self.name.setWordWrap(True)
        self.name.setProperty("role", "display")
        self.name.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        # The band's logo in place of the name (build 141, task 203).
        from bandlogo import LogoMark
        self.logo = LogoMark(self)
        self._art = QPixmap()

    def set_tone(self, colour):
        """The color to dissolve into — whichever column this sits in.

        Re-read on every layout as well as set here, because a theme
        change rewrites the token without anyone telling the header.
        """
        self._tone = colour
        self._layout()

    def set_photo_path(self, path):
        """Where this picture came from, for the crop action."""
        self._photo_path = path or ""

    def _photo_menu(self, pos):
        """What can be done to the artist's picture."""
        path = getattr(self, "_photo_path", "")
        if not devmode.is_dev() \
                or getattr(self, "_station_locked", False):
            return          # a talk station's info ships with the app
        menu = panel_menu(self)
        crop = menu.addAction("Crop this photo…")
        crop.setEnabled(bool(path) and os.path.exists(path))
        # The whole photo is kept from before the first crop (task 76).
        import croptool
        undo = menu.addAction("Undo the crop")
        undo.setEnabled(bool(croptool.uncropped(path)[0]))
        menu.addSeparator()
        edit = menu.addAction("Edit this artist…")
        log(f"[artist] photo menu: crop {'on' if crop.isEnabled() else 'off'}"
            f", undo {'on' if undo.isEnabled() else 'off'}"
            f" ({'file ' + os.path.basename(path) if path else 'no file'})")
        # Acted on after the menu has closed, not from inside it: the crop
        # window is modal, and opening it inside the menu's own loop left
        # two loops nested on Wayland.
        chosen = menu.exec_(self.photo.mapToGlobal(pos))
        menu.deleteLater()
        if chosen is crop:
            self._crop_photo()
        elif chosen is undo:
            self._undo_crop()
        elif chosen is edit:
            self._edit_artist()

    def _edit_artist(self):
        if not devmode.is_dev():
            return
        """Open the correction for whoever is being shown."""
        from artistedit import ArtistEditDialog

        # The artist as identified, not as displayed.
        #
        # A correction can rename the artist on screen ("Show as"), and
        # this used to take whatever name was showing. So the second edit
        # was filed under the display name: the editor opened empty, the
        # save went to a new entry, and the next identification brought
        # the old text straight back.
        # A talk station in a dev build: the station's info, not an
        # artist (task 49).
        editor = getattr(self, "_station_editor", None)
        if editor is not None:
            editor()
            return
        if getattr(self, "_station_locked", False):
            return
        name = (getattr(self, "_edit_key", "") or getattr(self, "_name", "")
                or "").strip()
        if not name:
            return
        dialog = ArtistEditDialog(name, self)
        if dialog.exec_():
            self.artist_corrected.emit(name)

    def _artist_menu(self, pos):
        """Right-clicking the biography offers the same correction."""
        if not devmode.is_dev() \
                or getattr(self, "_station_locked", False):
            return          # a talk station's info ships with the app
        menu = panel_menu(self)
        edit = menu.addAction("Edit this artist…")
        edit.setEnabled(bool((getattr(self, "_name", "") or "").strip()))
        chosen = menu.exec_(self.bio.mapToGlobal(pos))
        menu.deleteLater()
        if chosen is edit:
            self._edit_artist()

    def _crop_photo(self):
        """Crop the photo where it lives, then show the result.

        The crop is square, like every crop in the app (task 52: the user
        is happy with that). The whole photo is kept aside, so a second
        crop starts from all of it and "Undo the crop" brings it back
        (task 76).
        """
        if not devmode.is_dev():
            return
        import croptool
        from PyQt5.QtGui import QPixmapCache

        path = getattr(self, "_photo_path", "")
        if not path or not os.path.exists(path):
            return
        name = getattr(self, "_name", "") or "artist"
        if croptool.crop_file(path, parent=self,
                              title=f"Crop — {name}"):
            # Qt caches by path and the path has not changed, so without
            # this the panel would redraw the picture as it was before.
            QPixmapCache.clear()
            log(f"[artist] photo cropped: {os.path.basename(path)}")
            import imagelock
            self.set_photo(imagelock.pixmap(path))
            self.photo_cropped.emit(path)

    def _undo_crop(self):
        """The whole photo back, as it was before the first crop."""
        if not devmode.is_dev():
            return
        import croptool
        from PyQt5.QtGui import QPixmapCache

        path = getattr(self, "_photo_path", "")
        if not path or not croptool.undo_crop(path):
            return
        QPixmapCache.clear()
        log(f"[artist] photo crop undone: {os.path.basename(path)}")
        import imagelock
        self.set_photo(imagelock.pixmap(path))
        self.photo_cropped.emit(path)

    def set_photo(self, pixmap):
        self._art = pixmap if pixmap is not None else QPixmap()
        # The header keeps its height either way: with no photograph the
        # tower stands there instead, so the panel does not jump every
        # time a lookup fails.
        self.setFixedHeight(self.HEIGHT if (not self._art.isNull()
                                            or self.name.text())
                            else 0)
        self._layout()

    def set_name(self, text):
        self.name.setText(text or "")
        self.refresh_logo(layout=False)
        if not text and self._art.isNull():
            self.setFixedHeight(0)
        elif self._art.isNull():
            self.setFixedHeight(self.HEIGHT)
        self._layout()

    def refresh_logo(self, layout=True):
        """The logo for the name shown, or the name as text (the switch,
        a new logo pack, a correction saved)."""
        import bandlogo
        text = self.name.text()
        info = bandlogo.find(text)
        self.logo.set_logo(info, self._tone or T["bg1"])
        self.name.setVisible(info is None)
        tip = text if info is not None else ""
        for widget in (self, self.photo, self.tower):
            widget.setToolTip(tip)
        self.logo.setAccessibleName(tip)
        if layout:
            self._layout()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Cheap now, smooth when it settles.
        #
        # _layout rescales the photograph with SmoothTransformation, and
        # that is a real cost on a 2000px press shot — run on every pixel
        # of a drag it is the reason resizing the panel crawled. The fast
        # scale keeps something on screen while the pointer moves; the
        # smooth one follows a moment later.
        self._layout(fast=True)
        timer = getattr(self, "_settle", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(80)
            timer.timeout.connect(self._layout)
            self._settle = timer
        timer.start()

    def fit_to(self, available):
        """Take a share of the column, within reason."""
        if self._art.isNull():
            return
        wanted = max(self.MIN_HEIGHT, min(self.HEIGHT, int(available * 0.42)))
        if wanted != self.height():
            self.setFixedHeight(wanted)

    def _layout(self, fast=False):
        if self.width() <= 0:
            return
        if self._art.isNull():
            self.photo.setVisible(False)
            self.tower.setVisible(bool(self.name.text()))
            self.tower.setGeometry(0, 0, self.width(),
                                   max(0, self.height() - 40))
            self.name.setGeometry(12, self.height() - 62,
                                  max(0, self.width() - 24), 54)
            self.name.raise_()
            self._lay_logo()
            return
        self.tower.setVisible(False)
        self.photo.setVisible(True)
        self.photo.setGeometry(0, 0, self.width(), self.height())
        # Cropped to fill rather than letterboxed: a portrait with bars
        # down each side looks like a mistake.
        #
        # Cropped *before* rounding, too. Expanding to fill gives a pixmap
        # larger than the widget, so rounding it and then drawing it at
        # 0,0 put two of the rounded corners outside the visible area —
        # which is why one side looked round and the other square.
        scaled = self._art.scaled(
            self.size(), Qt.KeepAspectRatioByExpanding,
            Qt.FastTransformation if fast else Qt.SmoothTransformation)
        x = max(0, (scaled.width() - self.width()) // 2)
        # Not the middle vertically: a portrait cropped to a wide band from
        # its centre is a photograph of somebody's chest. Faces sit in the
        # upper third, so the crop starts nearer the top.
        overflow = max(0, scaled.height() - self.height())
        y = int(overflow * 0.22)
        cropped = scaled.copy(x, y, self.width(), self.height())
        # The tone is taken from whatever is actually behind the header,
        # so it dissolves into the column it is in rather than into the
        # colour that column used to be.
        parent = self.parentWidget()
        behind = parent.palette().color(parent.backgroundRole()).name() \
            if parent is not None else self._tone
        tone = self._tone if self._tone else behind
        self.photo.setPixmap(faded(rounded(cropped, 10), self.size(), tone))
        self.name.setGeometry(12, self.height() - 62,
                              max(0, self.width() - 24), 54)
        self.name.raise_()
        self._lay_logo(tone)

    def _lay_logo(self, tone=None):
        self.logo.tone = tone or self._tone or T["bg1"]
        self.logo.setGeometry(0, 0, self.width(), self.height())
        self.logo.raise_()
        self.logo.update()


# The artist photo's fade: where it begins (share of the height from the
# top) and how solid it is at points along the way (0 = clear, 255 =
# solid). Build 51 was 0.22 and (110, 200, 245).
FADE_FROM = 0.34
FADE_STOPS = ((0.45, 75), (0.70, 165), (0.88, 230))


def faded(pixmap, size, into=None):
    """Dissolve the bottom of the photo into the panel behind it.

    Not a fade to black: black over a dark panel still reads as a band
    across the picture. Fading into the panel's own color makes the
    photograph end rather than stop, and the name sits in the part that
    has already become background.
    """
    from PyQt5.QtGui import QLinearGradient, QPainter, QColor
    tone = QColor(into or T["bg1"])
    out = QPixmap(size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, pixmap)
    # Starts higher and ends solid: at 40% the picture was still mostly
    # itself where the panel began, so the two met at an edge instead of
    # one becoming the other.
    #
    # Lighter since build 52 (task 28: "too strong"). It used to start 22%
    # down with stops 110 / 200 / 245, which covered half the photo. Now
    # it starts lower and each stop is weaker; the bottom edge is still
    # solid, so the name stays readable and the photo still ends softly.
    grad = QLinearGradient(0, size.height() * FADE_FROM, 0, size.height())
    clear = QColor(tone)
    clear.setAlpha(0)
    grad.setColorAt(0.0, clear)
    for stop, alpha in FADE_STOPS:
        step = QColor(tone)
        step.setAlpha(alpha)
        grad.setColorAt(stop, step)
    grad.setColorAt(1.0, QColor(tone))
    p.fillRect(0, 0, size.width(), size.height(), grad)
    p.end()
    return out


class TowerMark(QWidget):
    """A transmitter with a signal coming off it, for when there is no
    photograph.

    Something has to occupy the space or the panel looks broken, and a grey
    rectangle says the app failed. A mast that is plainly still
    broadcasting says the picture is missing, not the station.

    The waves travel outward on a loop rather than blinking: a signal that
    moves reads as waiting, and one that flashes reads as an error.
    """

    ARCS = 4
    # Smooth rather than stepped, so the frames are short and the cycle
    # is counted in them.
    INTERVAL = 70               # ms per frame
    PERIOD = 22                 # frames for one full breath

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, e):
        super().showEvent(e)
        self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def _tick(self):
        self._frame = (self._frame + 1) % self.PERIOD
        self.update()

    def paintEvent(self, _e):
        from PyQt5.QtCore import QRectF
        from PyQt5.QtGui import QColor, QPainter, QPen

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return

        # Sized off the shorter side so the mast keeps its proportions in
        # a wide header or a narrow one.
        unit = min(w / 2.4, h / 2.6)
        cx = w / 2
        top = h * 0.30              # the dish
        base = h * 0.90
        half = unit * 0.62          # half the width of the feet

        line = QColor(T["text3"])
        pen = QPen(line, max(1.4, unit * 0.035))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        # The dish, and the short neck under it.
        r = unit * 0.16
        p.drawEllipse(QRectF(cx - r, top - r, r * 2, r * 2))
        neck = top + r
        p.drawLine(int(cx), int(neck), int(cx), int(neck + unit * 0.10))

        # Two legs splaying to the feet, and the ground line.
        legs_top = neck + unit * 0.10
        p.drawLine(int(cx - half), int(base), int(cx - unit * 0.05),
                   int(legs_top))
        p.drawLine(int(cx + half), int(base), int(cx + unit * 0.05),
                   int(legs_top))
        p.drawLine(int(cx - half * 1.35), int(base), int(cx + half * 1.35),
                   int(base))

        # The lattice: horizontals at even intervals, and an X in each bay.
        rungs = 5
        points = []
        for i in range(rungs + 1):
            f = i / rungs
            y = legs_top + (base - legs_top) * f
            dx = (unit * 0.05) + (half - unit * 0.05) * f
            points.append((y, dx))
        for y, dx in points[1:]:
            p.drawLine(int(cx - dx), int(y), int(cx + dx), int(y))
        for (y1, d1), (y2, d2) in zip(points, points[1:]):
            p.drawLine(int(cx - d1), int(y1), int(cx + d2), int(y2))
            p.drawLine(int(cx + d1), int(y1), int(cx - d2), int(y2))

        # The signal: fixed arcs, lit in turn.
        #
        # Travelling outward made the mast look like it was transmitting
        # something in particular. Arcs that stay put and take it in turns
        # to brighten read as steady activity, which is what waiting for a
        # picture actually is. Centred on the horizontal and mirrored, so
        # the two sides are one signal rather than two.
        # Each arc rides its own sine, offset a quarter-turn from its
        # neighbour, so brightness travels along them instead of jumping.
        # Stepping the highlight from one to the next was legible but
        # abrupt — four states, changing all at once.
        for i in range(self.ARCS):
            phase = (self._frame / float(self.PERIOD)) - i * 0.22
            glow = 0.5 + 0.5 * math.sin(phase * 2.0 * math.pi)
            radius = unit * (0.30 + i * 0.22)
            wave = QColor(line)
            wave.setAlpha(int(60 + 175 * glow ** 1.6))
            p.setPen(QPen(wave, max(1.3, unit * (0.032 + 0.022 * glow))))
            box = QRectF(cx - radius, top - radius, radius * 2, radius * 2)
            p.drawArc(box, -45 * 16, 90 * 16)       # right
            p.drawArc(box, 135 * 16, 90 * 16)       # left
        p.end()


class SidePanel(QWidget):
    """Lyrics, artist, or both."""

    LYRICS, ARTIST, BOTH = "lyrics", "artist", "both"

    mode_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RightPanel")
        # A QWidget subclass with a styled objectName paints nothing unless
        # it is told to — the stylesheet rule matches, but a plain QWidget
        # doesn't draw its own background.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._mode = self.BOTH
        self._showing = self.LYRICS      # which side is up when alternating
        self._artist_enabled = True
        self._reflowing = False
        self._have = {self.LYRICS: False, self.ARTIST: False}
        self._anim = None
        self._name = ""
        self._photo = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 8, 10)
        outer.setSpacing(10)

        # The photo takes the whole top of the panel and the name sits on
        # it, along the bottom edge — a caption on a picture rather than a
        # thumbnail with a line of text beside it. Pinned above everything
        # in every mode: it is the fastest way to know who you are
        # listening to.
        self.header = ArtistHeader()
        outer.addWidget(self.header)
        self.photo = self.header.photo
        self.heading = self.header.name

        self.lyrics = LyricsColumn()
        self.artist = ArtistView()
        for header in (self.header, self.artist.header):
            header.photo_cropped.connect(self._photo_cropped)
        for view in (self.lyrics, self.artist):
            view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Alternating tones, the way the rows of a list alternate: two
        # columns of identical grey butted together read as one wide
        # column with a gap in it.
        self.lyrics.setAttribute(Qt.WA_StyledBackground, True)
        self.artist.setAttribute(Qt.WA_StyledBackground, True)
        # Far enough apart to see at a glance. row_alt is a whisper — it
        # is tuned for stripes a row apart, where the eye has an edge to
        # follow. Two columns need a step you can name.
        self.restyle()

        # One widget per arrangement, so switching is a page change rather
        # than a reparent — reparenting a scroll area mid-flight loses its
        # position, which is very noticeable in lyrics.
        self.stack = QStackedWidget()
        self.single = QWidget()
        single_lay = QVBoxLayout(self.single)
        single_lay.setContentsMargins(0, 0, 0, 0)

        self.split = QWidget()
        self.split_lay = QHBoxLayout(self.split)
        self.split_lay.setContentsMargins(0, 0, 0, 0)
        self.split_lay.setSpacing(0)

        self.stack.addWidget(self.single)
        self.stack.addWidget(self.split)
        outer.addWidget(self.stack, 1)

        # Floated over the bottom of the columns rather than given a strip
        # of its own: three small words do not need a shelf, and the strip
        # was eating height the text could use.
        self._switch_holder = self._build_switch()
        self._switch_holder.setParent(self)
        self._switch_holder.raise_()

        self._swap = QTimer(self)
        self._swap.setInterval(SWAP_EVERY)
        self._swap.timeout.connect(self._alternate)

        # The ring that shows how long is left.
        #
        # Both swaps on a timer, and a timer with nothing on screen is a
        # panel that changes for no visible reason. The ring starts at
        # twelve and goes round clockwise; when it closes, the sides
        # swap.
        self._ring_at = 0.0
        self._ring_tail = 0.0
        self._ring_erasing = False
        self._ring = QTimer(self)
        self._ring.setInterval(RING_MS)
        self._ring.timeout.connect(self._ring_tick)

        self._apply()

    # -- the switch --------------------------------------------------------

    def _build_switch(self):
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addStretch(1)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = {}
        # Both in the middle, because it is the middle position: lyrics
        # only, lyrics and artist, artist only. The order should read as a
        # scale rather than as a list.
        for mode, label in ((self.LYRICS, "Lyrics"),
                            (self.BOTH, "Both"),
                            (self.ARTIST, "Artist")):
            b = RingButton(label) if mode == self.BOTH else QPushButton(label)
            if mode == self.BOTH:
                b.progress = lambda: self._ring_at
                b.tail = lambda: self.RING_LENGTH
                b.active = lambda: self._ring.isActive()
            b.setProperty("segment", True)
            b.setCheckable(True)
            b.setChecked(mode == self._mode)
            b.clicked.connect(lambda _c, m=mode: self.set_mode(m))
            self._group.addButton(b)
            self._buttons[mode] = b
            row.addWidget(b)
        row.addStretch(1)
        row.setContentsMargins(0, 2, 0, 2)
        # The scrim, which is what SwitchScrim was written for.
        #
        # It had been defined and then never used: a later edit replaced
        # it with a flat rounded plate and left the class sitting there.
        # The gradient is the point — the column fades out as it reaches
        # the buttons rather than ending at an edge.
        holder = SwitchScrim()
        holder.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        holder.setLayout(row)
        self._switch = holder
        return holder

    def mode(self):
        return self._mode

    def set_mode(self, mode):
        if mode not in (self.LYRICS, self.ARTIST, self.BOTH):
            return
        # Entering or leaving Both re-lays the panel out; the other two
        # swap one column's content for another's.
        self._reflowing = self.BOTH in (mode, self._mode) and mode != self._mode
        self._mode = mode
        self._buttons[mode].setChecked(True)
        self._apply()
        self.mode_changed.emit(mode)

    # -- arrangement -------------------------------------------------------

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_switch()
        # Not while hidden: the arrangement is chosen again when it shows.
        #
        # Full screen hides the panel, and the splitter then hands it the
        # one-column width. Qt holds that resize back until something in
        # the window is drawn through an effect (the blurred lyric lines,
        # a page fade) and delivers it then with the panel's redraw
        # switched off. Switching the columns inside that left them
        # unable to redraw for good: back from full screen the panel was
        # blank, and squeezing the window brought back only the buttons
        # (task 73).
        if self.isHidden():
            self._apply_on_show = True
        else:
            self._apply()

    def showEvent(self, e):
        super().showEvent(e)
        if getattr(self, "_apply_on_show", False):
            self._apply_on_show = False
            self._apply()
        self._redraw_back()

    def _redraw_back(self):
        """Switch the columns' redraw back on if Qt left it off (task 73).

        QStackedLayout turns its parent's redraw off while it changes page
        and on again after; if the panel's own redraw is off at that
        moment (Qt delivering a held-back resize), the "on again" does
        nothing and the columns stay frozen. Done a moment later, once the
        panel's redraw is back.
        """
        if not self.updatesEnabled():
            QTimer.singleShot(0, self._redraw_back)
            return
        stuck = [w for w in (self.stack, self.split, self.single)
                 if not w.updatesEnabled()
                 and not w.testAttribute(Qt.WA_ForceUpdatesDisabled)]
        if not stuck:
            return
        self.stack.setUpdatesEnabled(True)
        for widget in stuck:
            widget.setUpdatesEnabled(True)
        log(f"[panel] the side panel's columns had stopped redrawing "
            f"({len(stuck)} part(s)) - put back (task 73)")

    def _place_switch(self):
        """Full width, along the bottom.

        Spanning the panel rather than sitting in the middle of it: the
        fade has to cover both columns or one of them ends in legible text
        under the buttons and the other does not.
        """
        holder = getattr(self, "_switch_holder", None)
        if holder is None:
            return
        holder.setGeometry(0, self.height() - SWITCH_BAND,
                           self.width(), SWITCH_BAND)
        holder.raise_()

    def set_artist_enabled(self, on):
        """Hide the artist half entirely when lookups are switched off.

        A panel that says "no biography found" for every track, because
        the app was told not to look, is worse than not offering the
        choice: it reads as a failure rather than a setting.
        """
        self._artist_enabled = bool(on)
        for mode in (self.ARTIST, self.BOTH):
            button = self._buttons.get(mode)
            if button is not None:
                button.setEnabled(self._artist_enabled)
                button.setVisible(self._artist_enabled)
        if not self._artist_enabled and self._mode != self.LYRICS:
            self.set_mode(self.LYRICS)
        else:
            self._apply()

    def restyle(self):
        """Re-read the tokens after a theme change.

        These two backgrounds are set on the widgets rather than in the
        stylesheet, so nothing repaints them when the palette changes —
        which left the columns wearing the old theme's colors while
        everything around them changed.
        """
        self.lyrics.setStyleSheet(f"background: {T['bg0']};")
        self.lyrics.chat.restyle()
        self.artist.setStyleSheet(f"background: {T['bg2']};")
        self.artist.header.set_tone(T["bg2"])
        self.header.set_tone(T["bg1"])
        self.artist._paint_thumbs()

    def can_split(self):
        """Enough room for two readable columns."""
        return self.width() >= SPLIT_AT

    def _can_split(self):
        # Split whenever there is room, whatever the mode says: with two
        # columns on screen there is nothing left for the mode to choose
        # between. Never, though, with the artist half switched off —
        # there is no second column to show.
        return self.can_split() and getattr(self, "_artist_enabled", True)

    def _apply(self, animate=False):
        """Choose the arrangement for the current mode and width."""
        split = self._can_split()
        # The three buttons choose between lyrics and artist. Split, both
        # are up, so the choice has already been made for you.
        self._switch.setVisible(not split)
        # Keep the thumbs clear of the switch.
        #
        # "Is this right?" sits at the end of the biography, and the
        # three buttons float over the bottom of the panel — so on a
        # short biography they landed on top of each other. The row is
        # given the band's height to clear when the buttons are there,
        # and none when they are not.
        row = getattr(self, "_verdict_row", None)
        if row is not None:
            row.setContentsMargins(14, 0, 12, 6 if split else SWITCH_BAND)
        # The photo belongs to the artist, not to the panel.
        #
        # It was shown above whichever column was up, so choosing Lyrics
        # still gave you a photograph of the artist over the words — the
        # one view where you asked not to have it.
        showing_artist = (self._showing == self.ARTIST
                          if self._mode == self.BOTH else
                          self._mode == self.ARTIST)
        self.header.setVisible(not split and bool(self._name)
                               and self._artist_enabled
                               and showing_artist)
        self.artist.set_header(self._name, self._photo, split)
        if split:
            self._swap.stop()
            self._ring.stop()
            self._take(self.split_lay, self.lyrics)
            self._take(self.split_lay, self.artist)
            self.lyrics.setVisible(True)
            self.artist.setVisible(True)
            self.stack.setCurrentWidget(self.split)
            self._redraw_back()
            return

        wanted = self._showing if self._mode == self.BOTH else self._mode
        other = self.artist if wanted == self.LYRICS else self.lyrics
        chosen = self.lyrics if wanted == self.LYRICS else self.artist

        layout = self.single.layout()
        was_showing = other.isVisible() and other.parent() is self.single

        # The picture of "before" has to be taken before anything moves.
        #
        # The swap happens two lines down, so a snapshot taken inside the
        # animation would already show the new arrangement — two
        # identical pictures, and a cross-fade between them that looks
        # like nothing at all.
        # Swapped outright, with nothing in front of it.
        #
        # Every animation here fought the same fact: the two halves are
        # different heights and the header sits outside the column, so
        # anything measured before the change is the wrong size after
        # it. Cross-fading showed both at once, and a grey dip only hid
        # that at the cost of a flash on every switch. Changing the
        # panel and leaving it alone is honest and instant.
        self._take(layout, chosen)
        chosen.setVisible(True)
        self.stack.setCurrentWidget(self.single)
        self._redraw_back()
        # Hidden, but kept inside the panel.
        #
        # It was detached with setParent(None), leaving the column with no
        # window at all. Anything that column then opened — the photo
        # cropper, the artist editor, its right-click menu — was a dialog
        # hanging off a widget that belonged to no window. On Wayland such
        # a dialog draws transparent and does not take clicks, which is
        # why those broke after full screen (the round trip is what
        # triggers the swap) and came back only once a resize put the
        # column back in a layout.
        if other.parent() is not self:
            other.setParent(self)
        other.setVisible(False)

        if self._mode == self.BOTH:
            self._swap.start()
            self._restart_ring()
        else:
            self._swap.stop()
            self._ring.stop()

    @staticmethod
    def _take(layout, widget):
        if widget.parent() is not layout.parentWidget():
            layout.addWidget(widget)
        elif layout.indexOf(widget) < 0:
            layout.addWidget(widget)

    def set_available(self, lyrics, artist):
        """What each side actually has to show.

        Both mode swapped on a timer regardless, so a track with lyrics
        and no biography spent half its time showing an empty panel. With
        only one side populated it now stays on that side.
        """
        # The chat counts as something to show (task 78).
        lyrics = lyrics or self.lyrics.chat_on
        self._have = {self.LYRICS: bool(lyrics), self.ARTIST: bool(artist)}
        if self._mode == self.BOTH and not self._can_split():
            wanted = self._only_side()
            if wanted and wanted != self._showing:
                self._showing = wanted
                self._apply()
            elif wanted:
                self._swap.stop()
                self._ring.stop()
            elif not self._swap.isActive():
                self._swap.start()
                self._restart_ring()

    def _only_side(self):
        """The one side worth showing, or None when both or neither are."""
        have = getattr(self, "_have", {})
        if have.get(self.LYRICS) and not have.get(self.ARTIST):
            return self.LYRICS
        if have.get(self.ARTIST) and not have.get(self.LYRICS):
            return self.ARTIST
        return None

    # -- the switch between the two, as one movement --------------------

    SWAP_MS = 420

    def _ring_tick(self):
        self._ring_at += RING_MS / float(SWAP_EVERY)
        if self._ring_at >= 1.0:
            # A lap is the wait: the sides change as the head comes home,
            # and the phase carries its remainder into the next lap so
            # the line does not stutter at the join.
            self._ring_at -= 1.0
        button = self._buttons.get(self.BOTH)
        if button is not None:
            button.update()

    def _restart_ring(self):
        """Back to twelve o'clock.

        Leaving Both and returning restarts the wait, so the ring has to
        restart with it — a ring showing three quarters gone when the
        timer has just begun is worse than no ring.
        """
        self._ring_at = 0.0
        self._ring_tail = 0.0
        self._ring_erasing = False
        if self._mode == self.BOTH and not self._can_split():
            self._ring.start()
        else:
            # Stopped means gone, not frozen at whatever it had reached.
            # A ring standing still says the timer is stuck; an absent
            # one says there is no timer, which is the truth.
            self._ring.stop()
        button = self._buttons.get(self.BOTH)
        if button is not None:
            button.update()

    def _alternate(self):
        only = self._only_side()
        if only:
            self._swap.stop()
            self._ring.stop()
            if only != self._showing:
                self._showing = only
                self._apply(animate=True)
            return
        self._showing = (self.ARTIST if self._showing == self.LYRICS
                         else self.LYRICS)
        self._apply(animate=True)

    # -- content -----------------------------------------------------------

    def set_track(self, song, artist):
        # A talk station's photo band keeps the station's name: songs
        # never replace its info (task 49).
        if not (getattr(self.header, "_station_editor", None) is not None
                or getattr(self.header, "_station_locked", False)):
            self._name = artist or song or ""
            self.header.set_name(self._name)
        self.heading.setToolTip(
            f"{song} — {artist}" if song and artist else (song or artist))

    def set_lyrics(self, lines, message=None):
        self.lyrics.set_lyrics(lines, message)

    def set_chat(self, on):
        """A Twitch station's chat where the lyrics go (task 78): the
        Lyrics button reads "Chat" while it is up."""
        on = bool(on)
        if on == self.lyrics.chat_on:
            return
        self.lyrics.show_chat(on)
        button = self._buttons.get(self.LYRICS)
        if button is not None:
            button.setText("Chat" if on else "Lyrics")
        if not on:
            self.lyrics.chat.clear()
        self.set_available(on, self._have.get(self.ARTIST))

    def set_position(self, ms):
        self.lyrics.set_position(ms)

    def reaccent(self):
        """The live line follows the accent, so it repaints with it."""
        self.lyrics.reaccent()

    def verdict_signal(self):
        return self.artist.verdict_given

    def clear_verdict(self):
        self.artist.clear_verdict()

    def set_busy(self, lyrics=None, artist=None):
        """Show the dots while a lookup is out."""
        if lyrics and self.lyrics.chat_on:
            lyrics = None               # the chat is up, not the lyrics
        if lyrics is not None:
            (self.lyrics.busy.start if lyrics else self.lyrics.busy.stop)()
        if artist is not None:
            (self.artist.busy.start if artist else self.artist.busy.stop)()

    def set_sources(self, lyrics="", artist=""):
        """Credit lines for each column."""
        self.lyrics.set_sources(lyrics)
        self.artist.set_sources(artist)

    def _show_verdict(self, on):
        """The thumbs and "Is this right?" belong to a biography - an
        artist's, not a talk station's own info (task 49)."""
        if getattr(self, "_station_editor", None) is not None \
                or getattr(self, "_station_locked", False):
            on = False
        for widget in (self.wrong, self.thumb_up, self.thumb_down):
            widget.setVisible(bool(on))

    def set_artist(self, name, genres="", biography=""):
        if name:
            self._name = name
            self.header.set_name(name)
        self.artist.set_artist(name, genres, biography)

    def artist_corrected_signal(self):
        """Emitted after an artist's correction is edited.

        Lives on the panel because the panel is what the window talks
        to; the views that raise it are an implementation detail.

        The photograph and the biography are separate widgets with a
        signal each, so the header's is forwarded through the column's —
        the window connects to one thing either way.
        """
        # There are two headers in play — the column builds one and the
        # panel builds another — and either can raise it.
        if not getattr(self, "_header_relayed", False):
            for header in (getattr(self.artist, "header", None),
                           getattr(self, "header", None)):
                signal = getattr(header, "artist_corrected", None)
                if signal is not None:
                    signal.connect(self.artist.artist_corrected.emit)
            self._header_relayed = True
        return self.artist.artist_corrected

    def set_photo_path(self, path):
        """Hand the source down to whatever is showing the picture.

        Both headers: the one above the panel and the artist column's own
        (two-column layout). Only the first was told, so in two columns
        "Crop this photo…" never had a file (task 46).
        """
        self._photo_path = path or ""
        self.header.set_photo_path(path)
        self.artist.header.set_photo_path(path)

    def set_photo(self, pixmap):
        """The artist's own photo, not the album art — album art is already
        in the bar and behind the full-screen view."""
        self._photo = pixmap
        if pixmap is None or pixmap.isNull():
            # No picture, no file to crop — and never last artist's file.
            self.set_photo_path("")
        self.header.set_photo(pixmap)
        self._apply()

    def _photo_cropped(self, path):
        """Show the cropped file everywhere the photo is shown."""
        if path and path == getattr(self, "_photo_path", ""):
            import imagelock
            self.set_photo(imagelock.pixmap(path))

    def clear(self):
        self.clear_verdict()
        self.artist._show_verdict(False)
        self._name = ""
        self._photo = None
        self.set_photo_path("")
        self.header.set_name("")
        self.header.set_photo(None)
        self.set_artist("", "", "")
        # Nothing to say yet, so nothing is said.
        #
        # A blank panel carrying two sentences of explanation and a row of
        # buttons for judging a biography that does not exist is three
        # things asking to be read before anything has happened. The dots
        # already mean "waiting"; the words repeated it.
        self.lyrics.set_lyrics([], "")
