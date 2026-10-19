"""Frequencies — the Twitch chat's message bar (task 95, build 85).

Laid out like Twitch's own chat bar:

    ┌──────────────────────────────────┐
    │ Send a message              [☺]  │   the box: where you type, and
    └──────────────────────────────────┘   the emote picker button
                             [⚙] [Chat]    chat options, and send

  * `EmotePicker`: a small window of emotes you can use, grouped like
    Twitch's: this channel's, its BetterTTV ones, your other channels',
    Twitch's global ones, BetterTTV's global ones. A search field at the
    top. Clicking one puts its name in the box (the picker stays open
    for more; Esc or a click outside closes it).
  * `SuggestList`: type ":" and two or more letters (no spaces) and a
    small list of matching emotes shows just above the box, without
    taking the keyboard. Up/Down move, Tab or Enter picks, Esc closes,
    and it goes as soon as the word stops looking like ":name".
  * The ⚙ button opens the chat options (the same switches as the
    chat's right-click menu, plus Twitch chat settings).
  * "Chat" sends, like Enter.
  * `PinBar` (task 106, build 87): a thin row of pinned emotes at the very
    bottom of the bar. Right-click an emote (in the chat or the picker) >
    "Pin to the bar"; click a pinned one to put it in the box, right-click
    > "Unpin from the bar". Saved between runs (`chat_pins`).
  * "@" and a few letters (task 103) lists names seen in this chat in the
    same small list as ":"; an emote you can't use in the channel is
    grayed out in the picker, the pinned row and the ":" list (task 108).
  * `RulesCard`: the channel's chat rules with an OK button, shown when
    you first go to write in a channel that has rules (the box stays shut
    until OK, which is remembered); ⚙ > View chat rules shows them again.

Signed out, the box says so and the button reads "Sign in"; a build
with no Twitch app Client ID (a shared build that wasn't set up) says
the chat is read-only and has no button.

Where the emote lists come from: `chatextras` (your Twitch emotes need
the chat sign-in; BetterTTV's need nothing).
"""

import re

from PyQt5.QtCore import QEvent, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QToolButton,
                             QVBoxLayout, QWidget)

from theme import T

CELL = 38                       # one emote's square in the picker
SUGGEST_ROW = 34                # one row of the ":" list
SUGGEST_MAX = 6                 # rows shown at most
SUGGEST_FROM = 2                # letters after ":" before the list shows
PIN_CELL = 32                   # one pinned emote's square
_WORD = re.compile(r"(?:^|\s):([^\s:]+)$")
_AT_WORD = re.compile(r"(?:^|\s)@([^\s@]*)$")


def _log(text):
    try:
        import crashlog
        crashlog.write("[chat] " + text)
    except Exception:                           # noqa: BLE001
        pass


def _picture(extras, key, widget=None):
    """The emote's picture right now (the frame of the moment, for an
    animated one), or None while it downloads. `widget` is asked to
    repaint with each animation tick while it shows one (task 104: the
    pickers drew only the first frame)."""
    pm = extras.pixmap(key)
    if widget is not None and extras.animated(key):
        extras.watch(widget)
    return pm


def popup_spot(anchor, popup, right_edge=True, gap=6):
    """Where a popup window goes: just above `anchor` (below it when
    there is no room above), kept on the screen the anchor is on.

    Task 112: builds 85 to 87 kept the picker inside
    `anchor.screen().availableGeometry()`. On Wayland a window doesn't
    know where it sits on the desktop, so its global coordinates are not
    in the screen's frame; on a second monitor the picker was pushed out
    to that monitor's corner. Now the screen is only used when the
    anchor's own point falls on it (the frames agree); otherwise the
    popup sits right next to the button, unclamped.
    """
    from PyQt5.QtGui import QGuiApplication
    x = anchor.width() - popup.width() if right_edge else 0
    above = anchor.mapToGlobal(QPoint(x, -popup.height() - gap))
    below = anchor.mapToGlobal(QPoint(x, anchor.height() + gap))
    here = anchor.mapToGlobal(anchor.rect().center())
    screen = QGuiApplication.screenAt(here)
    if screen is None or screen is not anchor.screen():
        return above if above.y() >= 0 else below
    area = screen.availableGeometry()
    spot = above if above.y() >= area.top() else below
    spot.setX(max(area.left(),
                  min(spot.x(), area.right() - popup.width() + 1)))
    spot.setY(max(area.top(),
                  min(spot.y(), area.bottom() - popup.height() + 1)))
    return spot


class ChatBar(QWidget):
    """The box you type in, with the emote, options and Chat buttons."""

    send = pyqtSignal(str)
    options_asked = pyqtSignal(QPoint)          # where to open the menu
    sign_in_asked = pyqtSignal()
    typing = pyqtSignal()                       # the text or cursor moved
    colour_asked = pyqtSignal(str, str)         # Twitch's value, #hex

    def __init__(self, parent=None):
        super().__init__(parent)
        import chatextras
        self.extras = chatextras.shared()
        self.channel = ""
        self._mode = "off"
        self.me_name = ""
        self.me_colour = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.box = QFrame()
        self.box.setObjectName("chatBox")
        box = QHBoxLayout(self.box)
        box.setContentsMargins(10, 2, 4, 2)
        box.setSpacing(2)
        # Your name colour, like Twitch's Chat Identity (task 95c).
        self.identity_button = QToolButton()
        self.identity_button.setToolTip("Chat identity: your name color")
        self.identity_button.setCursor(Qt.PointingHandCursor)
        self.identity_button.setFixedSize(28, 28)
        self.identity_button.setIconSize(QSize(18, 18))
        self.identity_button.clicked.connect(self.open_identity)
        box.addWidget(self.identity_button)
        box.addSpacing(4)
        self.entry = QLineEdit()
        self.entry.setMaxLength(500)
        self.entry.setFrame(False)
        self.entry.returnPressed.connect(self._send)
        self.entry.textEdited.connect(lambda _t: self.typing.emit())
        self.entry.cursorPositionChanged.connect(
            lambda *_: self.typing.emit())
        box.addWidget(self.entry, 1)
        self.emote_button = QToolButton()
        self.emote_button.setToolTip("Emotes")
        self.emote_button.setCursor(Qt.PointingHandCursor)
        self.emote_button.setFixedSize(30, 30)
        self.emote_button.setIconSize(QSize(20, 20))
        self.emote_button.clicked.connect(self.open_picker)
        box.addWidget(self.emote_button)
        lay.addWidget(self.box)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addStretch(1)
        self.options_button = QToolButton()
        self.options_button.setToolTip("Chat options")
        self.options_button.setCursor(Qt.PointingHandCursor)
        self.options_button.setFixedSize(32, 32)
        self.options_button.setIconSize(QSize(20, 20))
        self.options_button.clicked.connect(self._options)
        row.addWidget(self.options_button)
        self.chat_button = QPushButton("Chat")
        self.chat_button.setCursor(Qt.PointingHandCursor)
        self.chat_button.setFixedHeight(32)
        self.chat_button.clicked.connect(self._chat_clicked)
        row.addWidget(self.chat_button)
        lay.addLayout(row)
        # Pinned emotes, at the very bottom (task 106).
        self.pinbar = PinBar(self.extras, bar=self)
        self.pinbar.picked.connect(self.insert_emote)
        self.pinbar.hide()
        lay.addWidget(self.pinbar)
        self.extras.pins_changed.connect(self._refresh_pins)

        self.picker = None
        self.identity = None
        self.restyle()

    def restyle(self):
        from shell import icon
        self.box.setStyleSheet(
            f"#chatBox {{ background: {T['bg2']}; border-radius: 8px; }}"
            f"QLineEdit {{ background: transparent; color: {T['text']};"
            f" border: none; padding: 5px 0; font-size: 14px; }}"
            f"QLineEdit:disabled {{ color: {T['text2']}; }}")
        tool = (f"QToolButton {{ background: transparent; border: none;"
                f" border-radius: 6px; }}"
                f"QToolButton:hover {{ background: {T['bg1']}; }}")
        self.emote_button.setStyleSheet(tool)
        self.options_button.setStyleSheet(tool)
        self.identity_button.setStyleSheet(tool)
        self._paint_identity()
        if getattr(self, "identity", None) is not None:
            self.identity.restyle()
        self.emote_button.setIcon(icon("smiley", 20, T["text2"]))
        self.options_button.setIcon(icon("gear", 20, T["text2"]))
        self.chat_button.setStyleSheet(
            f"QPushButton {{ background: {T['accent']};"
            f" color: {T.get('on_accent', '#000')}; border: none;"
            f" border-radius: 8px; padding: 0 16px; font-size: 14px;"
            f" font-weight: 600; }}"
            f"QPushButton:disabled {{ background: {T['bg2']};"
            f" color: {T['text2']}; }}")
        if self.picker is not None:
            self.picker.restyle()

    # -- what the bar shows -----------------------------------------------------

    def set_state(self, writer, signed_in, live, can_sign_in=True):
        """`writer`: joined as you (you can type). `signed_in`: a saved
        sign-in (joining as you is on its way). `live`: a chat is on."""
        if not live:
            mode = "off"
        elif writer:
            mode = "write"
        elif signed_in:
            mode = "joining"
        elif can_sign_in:
            mode = "signed out"
        else:
            mode = "read only"
        self._mode = mode
        self.setVisible(mode != "off")
        writing = mode == "write"
        self.entry.setEnabled(writing)
        self.emote_button.setVisible(writing)
        self.identity_button.setVisible(writing)
        self.chat_button.setVisible(mode != "read only")
        self.chat_button.setEnabled(mode in ("write", "signed out"))
        self._refresh_pins()
        self.chat_button.setText("Sign in" if mode == "signed out"
                                 else "Chat")
        self.chat_button.setToolTip(
            "Sign in to Twitch to write in chat" if mode == "signed out"
            else "Send (Enter)")
        self.entry.setPlaceholderText({
            "write": "Send a message",
            "joining": "Joining the chat…",
            "signed out": "Sign in to write in chat",
            "read only": "Chat is read-only in this build",
        }.get(mode, ""))
        if not writing:
            self.entry.clear()
            if self.picker is not None:
                self.picker.hide()
            if self.identity is not None:
                self.identity.hide()

    def mode(self):
        return self._mode

    def _refresh_pins(self):
        """The pinned row: shown while you can write and have pins."""
        rows = [(p["key"], p["code"]) for p in self.extras.pins]
        self.pinbar.set_rows(rows)
        self.pinbar.setVisible(bool(rows) and self._mode == "write")
        self.pinbar.set_width(max(PIN_CELL, self.width()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.pinbar.isVisible() and \
                self.pinbar.width() != self.width():
            self.pinbar.set_width(max(PIN_CELL, self.width()))

    # -- buttons -------------------------------------------------------------------

    def _send(self):
        text = self.entry.text().strip()
        if text and self._mode == "write":
            self.entry.clear()
            self.send.emit(text)

    def _chat_clicked(self):
        if self._mode == "signed out":
            self.sign_in_asked.emit()
        else:
            self._send()
            self.entry.setFocus()

    def _options(self):
        b = self.options_button
        self.options_asked.emit(b.mapToGlobal(QPoint(0, 0)))

    # -- your name colour (task 95c) -------------------------------------------

    def set_me(self, name, colour):
        self.me_name = name or ""
        self.me_colour = colour or ""
        self._paint_identity()
        if self.identity is not None and self.identity.isVisible():
            self.identity.show_me(self.channel, self.me_name, self.me_colour)

    def _paint_identity(self):
        """The button: a dot in your name colour."""
        from PyQt5.QtGui import QIcon, QPixmap
        import sidepanel
        colour = sidepanel.chat_colour(self.me_colour, self.me_name)
        dpr = 2
        pm = QPixmap(18 * dpr, 18 * dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(T["text2"]), 1.5 * dpr))
        p.setBrush(QColor(colour))
        p.drawEllipse(QRect(2 * dpr, 2 * dpr, 14 * dpr, 14 * dpr))
        p.end()
        pm.setDevicePixelRatio(dpr)
        self.identity_button.setIcon(QIcon(pm))

    def open_identity(self):
        if self.identity is None:
            self.identity = IdentityPopup(self)
            self.identity.chosen.connect(self._colour_chosen)
            self.identity.sign_in_asked.connect(self.sign_in_asked.emit)
        self.identity.show_me(self.channel, self.me_name, self.me_colour)
        self.identity.open_over(self.box)

    def _colour_chosen(self, value, hex_colour):
        self.identity.set_status("Changing your color…")
        self.colour_asked.emit(value, hex_colour)

    def colour_result(self, ok, text):
        """Twitch's answer to a colour change."""
        if self.identity is None:
            return
        if ok:
            self.identity.set_status("Done. It may take a few minutes to "
                                     "show for everyone in chat.")
        elif text == "need sign-in":
            self.identity.set_status(
                "Sign in to chat once more to let Frequencies change your "
                "color (Twitch asks for one more permission).",
                sign_in=True)
        else:
            self.identity.set_status(text)

    def open_picker(self):
        if self.picker is None:
            self.picker = EmotePicker(self)
            self.picker.picked.connect(self.insert_emote)
        self.picker.open_for(self.channel, self.box)

    def insert_emote(self, code):
        """The emote's name into the box at the cursor, with a space
        either side as needed."""
        text = self.entry.text()
        at = self.entry.cursorPosition()
        before, after = text[:at], text[at:]
        if before and not before.endswith(" "):
            before += " "
        add = code + ("" if after.startswith(" ") else " ")
        self.entry.setText(before + add + after)
        self.entry.setCursorPosition(len(before) + len(add))
        self.entry.setFocus()

    # -- ":" suggestions ---------------------------------------------------------

    def typed_word(self):
        """The ":name" being typed right before the cursor, or ""."""
        if self._mode != "write":
            return ""
        before = self.entry.text()[:self.entry.cursorPosition()]
        m = _WORD.search(before)
        if not m or len(m.group(1)) < SUGGEST_FROM:
            return ""
        return m.group(1)

    def replace_word(self, code):
        """Swap the ":name" before the cursor for the emote's name."""
        text = self.entry.text()
        at = self.entry.cursorPosition()
        before = text[:at]
        m = _WORD.search(before)
        if not m:
            return
        start = m.start(1) - 1                  # the ":"
        after = text[at:]
        add = code + ("" if after.startswith(" ") else " ")
        self.entry.setText(text[:start] + add + after)
        self.entry.setCursorPosition(start + len(add))


    # -- "@" names (task 103) -------------------------------------------------

    def typed_mention(self):
        """The name being typed after "@" right before the cursor
        (possibly ""), or None when it isn't one."""
        if self._mode != "write":
            return None
        before = self.entry.text()[:self.entry.cursorPosition()]
        m = _AT_WORD.search(before)
        return m.group(1) if m else None

    def replace_mention(self, name):
        """Swap the "@part" before the cursor for "@name "."""
        text = self.entry.text()
        at = self.entry.cursorPosition()
        before = text[:at]
        m = _AT_WORD.search(before)
        if not m:
            return
        start = m.start(1) - 1                  # the "@"
        after = text[at:]
        add = "@" + name.lstrip("@") + ("" if after.startswith(" ")
                                        else " ")
        self.entry.setText(text[:start] + add + after)
        self.entry.setCursorPosition(start + len(add))


class SuggestList(QWidget):
    """The ":" list: a few emotes whose name matches what you typed.

    A child of the chat (not a window of its own), so it never takes the
    keyboard from the box; the chat view moves it just above the bar.
    """

    picked = pyqtSignal(str)

    def __init__(self, parent):
        super().__init__(parent)
        import chatextras
        self.extras = chatextras.shared()
        self.rows = []                          # [(key, code)]
        self.blocked = {}                       # code → why not
        self.current = 0
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.extras.emotes_arrived.connect(lambda _k: self.update())
        self.hide()

    def show_rows(self, rows, blocked=None):
        """`rows`: [(key, text)], key "" for a name. `blocked`: {text:
        why} for rows that can't be picked here (grayed, task 108)."""
        if rows != self.rows:
            self.current = 0
        self.rows = rows
        self.blocked = blocked or {}
        if not rows:
            self.hide()
            return
        self.setFixedHeight(min(len(rows), SUGGEST_MAX) * SUGGEST_ROW + 8)
        self.show()
        self.raise_()
        self.update()

    def move_by(self, step):
        if self.rows:
            self.current = (self.current + step) % min(len(self.rows),
                                                       SUGGEST_MAX)
            self.update()

    def chosen(self):
        if self.rows and self.current < len(self.rows) and \
                self.rows[self.current][1] not in self.blocked:
            return self.rows[self.current][1]
        return ""

    def _animate(self, now_ms):
        """One animation step (task 104): repaint when an animated emote
        in the list has a new frame; False when nothing moves."""
        if not self.isVisible():
            return False
        frames = tuple(self.extras.frame_index(k, now_ms)
                       for k, _c in self.rows[:SUGGEST_MAX]
                       if k and self.extras.animated(k))
        if not frames:
            return False
        if frames != getattr(self, "_frames_shown", None):
            self._frames_shown = frames
            self.update()
        return True

    def _row_at(self, y):
        i = (y - 4) // SUGGEST_ROW
        if 0 <= i < min(len(self.rows), SUGGEST_MAX):
            return i
        return -1

    def mouseMoveEvent(self, e):
        i = self._row_at(e.pos().y())
        if i >= 0 and i != self.current:
            self.current = i
            self.update()

    def mousePressEvent(self, e):
        i = self._row_at(e.pos().y())
        if i >= 0 and e.button() == Qt.LeftButton and \
                self.rows[i][1] not in self.blocked:
            self.picked.emit(self.rows[i][1])

    def paintEvent(self, e):
        import chatextras
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setPen(QPen(QColor(T["bg2"]), 1))
        p.setBrush(QColor(T["bg1"]))
        p.drawRoundedRect(QRect(0, 0, self.width() - 1, self.height() - 1),
                          8, 8)
        font = QFont(self.font())
        font.setPixelSize(14)
        p.setFont(font)
        side = chatextras.EMOTE_PX
        for i, (key, code) in enumerate(self.rows[:SUGGEST_MAX]):
            top = 4 + i * SUGGEST_ROW
            if i == self.current:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T["bg2"]))
                p.drawRoundedRect(QRect(4, top, self.width() - 8,
                                        SUGGEST_ROW), 6, 6)
            why = self.blocked.get(code, "")
            p.setOpacity(0.35 if why else 1.0)
            pm = _picture(self.extras, key, self) if key else None
            if pm is not None:
                w = pm.width() / max(1.0, pm.devicePixelRatio())
                h = pm.height() / max(1.0, pm.devicePixelRatio())
                scale = min(1.0, side / max(w, h, 1))
                p.drawPixmap(QRect(10, top + (SUGGEST_ROW - int(h * scale))
                                   // 2, int(w * scale), int(h * scale)), pm)
            p.setPen(QColor(T["text"]))
            left = 16 + side if key else 12
            name_w = p.fontMetrics().horizontalAdvance(code)
            p.drawText(QRect(left, top, self.width() - left - 8,
                             SUGGEST_ROW), Qt.AlignVCenter | Qt.AlignLeft,
                       code)
            if why:
                # Why, small, after the name (the row stays grayed).
                p.setOpacity(0.7)
                small = QFont(font)
                small.setPixelSize(11)
                p.setFont(small)
                p.setPen(QColor(T["text2"]))
                room = self.width() - left - name_w - 24
                if room > 40:
                    p.drawText(QRect(left + name_w + 12, top, room,
                                     SUGGEST_ROW),
                               Qt.AlignVCenter | Qt.AlignRight,
                               p.fontMetrics().elidedText(
                                   why, Qt.ElideRight, room))
                p.setFont(font)
            p.setOpacity(1.0)
        p.end()


def pin_menu(owner, extras, key, code, global_pos):
    """The right-click menu on an emote: pin it to the bar, or take it
    off (task 106). Only Twitch's and BetterTTV's emotes (not badges)."""
    if not key.startswith(("tw:", "bttv:")):
        return
    from sidepanel import panel_menu
    menu = panel_menu(owner)
    if extras.is_pinned(key):
        act = menu.addAction("Unpin from the bar")
        act.triggered.connect(lambda: extras.unpin(key))
    else:
        act = menu.addAction("Pin to the bar")
        act.triggered.connect(lambda: extras.pin(key, code))
    menu.exec_(global_pos)


class _EmoteGrid(QWidget):
    """One group's emotes as a grid of squares (painted, not one button
    each: a picker can hold hundreds).

    An emote you can't use in this channel (`extras.why_not`) is grayed
    out, says why on hover and does nothing when clicked (task 108).
    Right-click opens the pin menu (task 106)."""

    picked = pyqtSignal(str)
    channel = ""                                # whose emotes these are

    def __init__(self, rows, extras, parent=None, cell=CELL, marks=True):
        super().__init__(parent)
        self.rows = rows                        # [(key, code)]
        self.extras = extras
        self.cell = cell
        self.marks = marks                      # a dot on pinned ones
        self.hover = -1
        self.cols = 8
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set_width(self, width):
        self.cols = max(1, width // self.cell)
        rows = (len(self.rows) + self.cols - 1) // self.cols
        self.setFixedSize(width, max(1, rows) * self.cell)

    def set_rows(self, rows):
        if rows != self.rows:
            self.rows = rows
            self.hover = -1
            self.set_width(self.width() or self.cell)
            self.update()

    def _at(self, pos):
        c, r = pos.x() // self.cell, pos.y() // self.cell
        if c >= self.cols:
            return -1
        i = r * self.cols + c
        return i if 0 <= i < len(self.rows) else -1

    def keys(self):
        return {k for k, _c in self.rows}

    def _animate(self, now_ms):
        """One animation step (task 104): repaint what shows when an
        animated emote has a new frame; False when nothing on show
        moves (the timer then stops until a paint asks again)."""
        if not self.isVisible():
            return False
        vis = self.visibleRegion().boundingRect()
        if vis.isEmpty():
            return False
        first = max(0, vis.top() // self.cell) * self.cols
        last = (vis.bottom() // self.cell + 1) * self.cols
        frames = tuple(self.extras.frame_index(k, now_ms)
                       for k, _c in self.rows[first:last]
                       if self.extras.animated(k))
        if not frames:
            return False
        if frames != getattr(self, "_frames_shown", None):
            self._frames_shown = frames
            self.update(vis)
        return True

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            i = self._at(e.pos())
            from PyQt5.QtWidgets import QToolTip
            if i >= 0:
                key, code = self.rows[i]
                why = self.extras.why_not(key, self.channel)
                QToolTip.showText(e.globalPos(),
                                  f"{code}: {why}" if why else code, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def mouseMoveEvent(self, e):
        i = self._at(e.pos())
        if i != self.hover:
            self.hover = i
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        i = self._at(e.pos())
        if i < 0:
            return
        key, code = self.rows[i]
        if e.button() == Qt.LeftButton:
            if not self.extras.why_not(key, self.channel):
                self.picked.emit(code)
        elif e.button() == Qt.RightButton:
            pin_menu(self, self.extras, key, code, e.globalPos())

    def paintEvent(self, e):
        import chatextras
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        side = min(chatextras.EMOTE_PX, self.cell - 6)
        cell_px = self.cell
        clip = e.rect()
        for i, (key, code) in enumerate(self.rows):
            x, y = (i % self.cols) * cell_px, (i // self.cols) * cell_px
            cell = QRect(x, y, cell_px, cell_px)
            if not cell.intersects(clip):
                continue
            blocked = bool(self.extras.why_not(key, self.channel))
            if i == self.hover and not blocked:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T["bg2"]))
                p.drawRoundedRect(cell.adjusted(1, 1, -1, -1), 6, 6)
            p.setOpacity(0.3 if blocked else 1.0)
            pm = _picture(self.extras, key, self)
            if pm is None:
                if self.extras.failed(key):
                    p.setPen(QColor(T["text2"]))
                    f = QFont(self.font())
                    f.setPixelSize(9)
                    p.setFont(f)
                    p.drawText(cell, Qt.AlignCenter, code[:6])
                p.setOpacity(1.0)
                continue
            dpr = max(1.0, pm.devicePixelRatio())
            w, h = pm.width() / dpr, pm.height() / dpr
            scale = min(1.0, side / max(w, h, 1))
            w, h = int(w * scale), int(h * scale)
            p.drawPixmap(QRect(x + (cell_px - w) // 2, y + (cell_px - h) // 2,
                               w, h), pm)
            p.setOpacity(1.0)
            if self.marks and self.extras.is_pinned(key):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T["accent"]))
                p.drawEllipse(QRect(x + cell_px - 9, y + 3, 6, 6))
        p.end()


class PinBar(_EmoteGrid):
    """The pinned emotes, small, in pin order, wrapping to more rows when
    there are many (task 106). Click puts one in the box."""

    def __init__(self, extras, parent=None, bar=None):
        super().__init__([], extras, parent, cell=PIN_CELL, marks=False)
        self._bar = bar
        extras.emotes_arrived.connect(lambda _k: self.update())
        extras.mine_changed.connect(self.update)
        extras.bttv_changed.connect(self.update)

    @property
    def channel(self):
        return self._bar.channel if self._bar is not None else ""


class EmotePicker(QFrame):
    """The emote picker: a small window under (or over) the message box."""

    picked = pyqtSignal(str)
    WIDTH = 8 * CELL + 30
    HEIGHT = 380

    def __init__(self, bar):
        super().__init__(bar.window(), Qt.Popup)
        import chatextras
        self.extras = chatextras.shared()
        self.channel = ""
        self.grids = []
        self.setObjectName("emotePicker")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 4, 10)
        lay.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search for emotes")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        lay.addWidget(self.search)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        lay.addWidget(self.scroll, 1)
        self.extras.emotes_arrived.connect(self._arrived)
        self.extras.mine_changed.connect(self._fill_if_open)
        self.extras.bttv_changed.connect(self._fill_if_open)
        self.extras.pins_changed.connect(self._repaint_grids)
        self.restyle()

    def _repaint_grids(self):
        for grid in self.grids:
            grid.update()

    def restyle(self):
        self.setStyleSheet(
            f"#emotePicker {{ background: {T['bg1']};"
            f" border: 1px solid {T['bg2']}; border-radius: 10px; }}"
            f"QLineEdit {{ background: {T['bg2']}; color: {T['text']};"
            f" border: none; border-radius: 6px; padding: 6px 8px;"
            f" font-size: 13px; }}"
            f"QScrollArea, QScrollArea > QWidget > QWidget"
            f" {{ background: transparent; }}"
            f"QLabel {{ color: {T['text2']}; font-size: 12px;"
            f" font-weight: 600; background: transparent; }}")

    def open_for(self, channel, anchor):
        self.channel = channel or ""
        self.search.clear()
        self._fill()
        # Above the box when there is room (the box sits at the bottom),
        # right edges lined up, like Twitch's (task 112: see popup_spot).
        self.move(popup_spot(anchor, self))
        self.show()
        self.search.setFocus()
        groups = self.extras.emote_groups(self.channel)
        _log(f"emote picker opened: {sum(len(r) for _t, r in groups)} "
             f"emotes in {len(groups)} group(s)")

    def _fill_if_open(self):
        if self.isVisible():
            self._fill()

    def _fill(self, *_):
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.grids = []
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        width = self.WIDTH - 30
        groups = self.extras.emote_groups(self.channel)
        wanted = self.search.text().strip().lower()
        if wanted:
            found, seen = [], set()
            for _title, rows in groups:
                for key, code in rows:
                    if wanted in code.lower() and code not in seen:
                        seen.add(code)
                        found.append((key, code))
            found.sort(key=lambda e: (not e[1].lower().startswith(wanted),
                                      e[1].lower()))
            groups = [("Results", found)] if found else []
        if not groups:
            note = QLabel("No emotes match." if wanted else
                          "No emotes yet. Your Twitch emotes show once "
                          "you're signed in and the chat has joined; "
                          "BetterTTV's once its list has arrived.")
            note.setWordWrap(True)
            note.setFixedWidth(width)
            lay.addWidget(note)
        for title, rows in groups:
            label = QLabel(title)
            label.setFixedWidth(width)
            lay.addWidget(label)
            grid = _EmoteGrid(rows, self.extras)
            grid.channel = self.channel
            grid.set_width(width)
            grid.picked.connect(self.picked.emit)
            lay.addWidget(grid)
            self.grids.append(grid)
        lay.addStretch(1)
        page.setFixedWidth(width)
        page.adjustSize()
        self.scroll.setWidget(page)

    def _arrived(self, keys):
        if not self.isVisible():
            return
        keys = set(keys)
        for grid in self.grids:
            if grid.keys() & keys:
                grid.update()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)


class RulesCard(QFrame):
    """The channel's chat rules, with an OK button (task 95).

    Shown over the bottom of the chat, just above the bar, the first
    time you go to write in a channel that has rules (Twitch does the
    same); the box stays shut until you press OK. ⚙ > View chat rules
    shows it again any time.
    """

    accepted = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("rulesCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        self.title = QLabel("Chat Rules")
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        lay.addWidget(self.title)
        lay.addWidget(self.intro)
        lay.addWidget(self.body)
        row = QHBoxLayout()
        row.addStretch(1)
        self.ok = QPushButton("OK")
        self.ok.setCursor(Qt.PointingHandCursor)
        self.ok.setFixedHeight(30)
        self.ok.clicked.connect(self._ok)
        row.addWidget(self.ok)
        lay.addLayout(row)
        self.restyle()
        self.hide()

    def restyle(self):
        self.setStyleSheet(
            f"#rulesCard {{ background: {T['bg1']};"
            f" border: 1px solid {T['bg2']}; border-radius: 10px; }}"
            f"QLabel {{ color: {T['text']}; font-size: 13px;"
            f" background: transparent; }}"
            f"QPushButton {{ background: {T['accent']};"
            f" color: {T.get('on_accent', '#000')}; border: none;"
            f" border-radius: 8px; padding: 0 18px; font-size: 13px;"
            f" font-weight: 600; }}")
        self.title.setStyleSheet("font-size: 15px; font-weight: 700;")

    def show_rules(self, channel, rules):
        import html
        self.intro.setText(f"{channel}'s chat rules:" if channel
                           else "This channel's chat rules:")
        self.body.setText("".join(
            f"<p style='margin:0 0 4px 0;'>{i}. {html.escape(rule)}</p>"
            for i, rule in enumerate(rules, 1)))
        self.show()
        self.raise_()

    def fit(self, width):
        """Height for `width` (word-wrapped labels: CLAUDE.md's sizing
        gotcha)."""
        for w in (self.title, self.intro, self.body, self.ok):
            w.ensurePolished()
        return self.layout().totalHeightForWidth(width)

    def _ok(self):
        self.hide()
        self.accepted.emit()


class IdentityPopup(QFrame):
    """Chat Identity, like Twitch's (task 95c): how your name looks, and
    Twitch's 15 name colours. "More colors…" picks any colour, which
    Twitch only accepts from Prime or Turbo users. Changing which badge
    you wear isn't here: Twitch doesn't let apps do that.
    """

    chosen = pyqtSignal(str, str)               # Twitch's value, #hex
    sign_in_asked = pyqtSignal()
    PER_ROW = 7
    DOT = 30

    def __init__(self, bar):
        super().__init__(bar.window(), Qt.Popup)
        import twitchauth
        self.setObjectName("identityPopup")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(self.PER_ROW * (self.DOT + 10) + 28)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        self.title = QLabel("Chat Identity")
        lay.addWidget(self.title)
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        lay.addWidget(self.intro)
        self.preview = QLabel()
        self.preview.setTextFormat(Qt.RichText)
        lay.addWidget(self.preview)
        self.heading = QLabel("Global Name Color")
        lay.addSpacing(4)
        lay.addWidget(self.heading)
        note = QLabel("Pick a color. It may take a few minutes to show "
                      "in chat.")
        note.setWordWrap(True)
        lay.addWidget(note)
        from PyQt5.QtWidgets import QGridLayout
        grid = QGridLayout()
        grid.setSpacing(10)
        self.dots = {}
        for i, (value, hex_colour) in enumerate(twitchauth.NAMED_COLOURS):
            dot = QToolButton()
            dot.setFixedSize(self.DOT, self.DOT)
            dot.setCursor(Qt.PointingHandCursor)
            dot.setToolTip(value.replace("_", " ").title())
            dot.clicked.connect(lambda _c=False, v=value, h=hex_colour:
                                self.chosen.emit(v, h))
            grid.addWidget(dot, i // self.PER_ROW, i % self.PER_ROW)
            self.dots[hex_colour.upper()] = dot
        lay.addLayout(grid)
        self.more = QPushButton("More colors…")
        self.more.setCursor(Qt.PointingHandCursor)
        self.more.setToolTip("Any color: Twitch only takes these from "
                             "Prime or Turbo users")
        self.more.clicked.connect(self._more)
        row = QHBoxLayout()
        row.addWidget(self.more)
        row.addStretch(1)
        lay.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        lay.addWidget(self.status)
        self.sign_in = QPushButton("Sign in again…")
        self.sign_in.setCursor(Qt.PointingHandCursor)
        self.sign_in.clicked.connect(self._sign_in)
        self.sign_in.hide()
        lay.addWidget(self.sign_in, 0, Qt.AlignLeft)
        self._current = ""
        self.restyle()

    def restyle(self):
        self.setStyleSheet(
            f"#identityPopup {{ background: {T['bg1']};"
            f" border: 1px solid {T['bg2']}; border-radius: 10px; }}"
            f"QLabel {{ color: {T['text2']}; font-size: 12px;"
            f" background: transparent; }}"
            f"QPushButton {{ background: {T['bg2']}; color: {T['text']};"
            f" border: none; border-radius: 6px; padding: 5px 12px;"
            f" font-size: 12px; }}"
            f"QPushButton:hover {{ color: {T['accent']}; }}")
        self.title.setStyleSheet(f"color: {T['text']}; font-size: 15px;"
                                 f" font-weight: 700;")
        self.heading.setStyleSheet(f"color: {T['text']}; font-size: 13px;"
                                   f" font-weight: 700;")
        self._paint_dots()

    def _paint_dots(self):
        for hex_colour, dot in self.dots.items():
            ring = (f"border: 2px solid {T['text']};"
                    if hex_colour == self._current else
                    f"border: 2px solid {T['bg1']};")
            dot.setStyleSheet(
                f"QToolButton {{ background: {hex_colour}; {ring}"
                f" border-radius: {self.DOT // 2}px; }}"
                f"QToolButton:hover {{ border: 2px solid {T['text2']}; }}")

    def show_me(self, channel, name, colour):
        import html
        import sidepanel
        self.intro.setText(f"How your name will appear in chat on "
                           f"{channel}'s channel:" if channel else
                           "How your name will appear in chat:")
        shown = sidepanel.chat_colour(colour, name)
        self.preview.setText(
            f'<span style="color:{shown}; font-size:15px; font-weight:600;">'
            f"{html.escape(name or 'you')}</span>")
        self._current = (colour or "").upper()
        self._paint_dots()

    def set_status(self, text, sign_in=False):
        self.status.setText(text)
        self.status.setVisible(bool(text))
        self.sign_in.setVisible(sign_in)
        self.adjustSize()

    def open_over(self, anchor):
        self.set_status("")
        self.adjustSize()
        self.move(popup_spot(anchor, self, right_edge=False))
        self.show()
        _log("chat identity opened")

    def _more(self):
        from PyQt5.QtWidgets import QColorDialog
        start = QColor(self._current) if self._current else QColor("#9146FF")
        self.hide()                         # a popup under a dialog closes
        got = QColorDialog.getColor(start, self.parentWidget(),
                                    "Any name color (Prime or Turbo)")
        if got.isValid():
            self.chosen.emit(got.name().upper(), got.name().upper())
            self.show()

    def _sign_in(self):
        self.hide()
        self.sign_in_asked.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)
