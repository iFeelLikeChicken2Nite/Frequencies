"""Clippy, the companion (build 107, task 116; the user's script, build 108,
task 143).

He stands next to whatever a tip is about, gestures at it, and says his
piece in a yellow bubble. The X in the bubble's corner closes that tip for
good (the ids go in settings["companion_closed"]); nothing else counts as
done. Settings > Appearance > "Reset Clippy's hot tips" clears them.

What he says always matches what is on screen: every tip has a place (the
widget it is about). The newest tip asked for whose place is on screen is
the one shown. A newer tip takes over from an older one; when its place
goes away (another page, the Winamp view closed) or it is closed, the
older one comes back the next time its own place is showing.

He blocks clicks (nothing under him or his bubble gets them) and can be
dragged anywhere in the window; the bubble goes with him.

The art is Clippy's sprite sheet from github.com/felixrieseberg/clippy
(src/companion/: map.png, 124 x 93 frames, and animations.json); see
CREDITS.txt there.
"""

import json
import os
import random
import time

from PyQt5.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,
                             QToolButton, QWidget)

import crashlog

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "companion")
FRAME_W, FRAME_H = 124, 93
SETTING = "companion_closed"
OPEN_SETTING = "companion_open_seconds"   # time the app has been open
# Tip 1's "Too bad!" is said once ever (task 172); after that the No
# answer is the same words without it, until "Reset Clippy's hot tips".
TOO_BAD = "Too bad! "
TOO_BAD_SETTING = "companion_too_bad_said"
GAP = 4.0             # "a few seconds" between one tip closing and the next
PLAYED = 5.0          # "playing their first frequency for a few seconds"
SHARE_AT = 20 * 60    # tip 6, "about 20 minutes" in (plus a little, random)
SIDEBAR_AT = 60 * 60  # tip 9, "about an hour"
PHOTOS_AT = 60 * 60   # the photo pack question, "after an hour"

GO_ON = ("Click Add Frequency… to find a station, paste a stream link, or "
         "play your own music.")

# The user's script (notes/clippy-script.md), in its order. id → (number,
# words, how he stands by the place). Typos fixed only where the script's
# note says so.
TIPS = {
    "welcome": ("1", "Hi, it looks like you want to listen to the radio! "
                     "Would you like some help with that?", "center"),
    # Asked 2026-10-19 (task 163): after tip 1's answer, beside the Search
    # button once the Add Frequency page is showing.
    "search": ("1.5", "Try clicking the Search button, the big button with "
                      "the magnifying glass, and searching \"electro\"!",
               "beside"),
    "added": ("2", "Nice pick! It's in Saved Frequencies now. Double-click "
                   "it to tune in, or right-click it for tags, sharing and "
                   "more.", "beside"),
    "queue": ("2.5", "Your saved frequencies displayed here are also your "
                     "queue. Add tags to your frequencies to quickly and "
                     "easily choose what you want to hear! For example, if "
                     "you have tags like \"Funky dance tunes\" and \"New age "
                     "music to conk out to,\" and you want to conk out, you "
                     "can exclude all your Funky Dance Tunes, but keep your "
                     "regular, non tagged stations in the dynamic playlist "
                     "as well.", "middle"),
    "shazam": ("2.75", "Frequencies has built in Shazam support! The button "
                       "works per station. You wouldn't want to shazam those "
                       "boring talk radio streams!", "beside"),
    "favorite": ("2.8", "Click the star button to mark that song as a "
                        "favorite! Right click it to see the history screen, "
                        "or go there from the panel on the left. Everything "
                        "you listen to gets remembered, but your favorites "
                        "will stand out!", "beside"),
    "options": ("2.9", "You can change all sorts of things about your saved "
                       "frequency by clicking this options button in the "
                       "bottom panel! Change anything from the station's own "
                       "equalizer settings, hide its forced ad when you "
                       "connect to the stream (if the stream has that), "
                       "pre-buffer it, change its art, and more!", "beside"),
    "tags": ("3", "Tags are like little folders for your stations. Left "
                  "click a tag on the left of the screen to show only those "
                  "frequencies, right click to exclude them, and the pencil "
                  "edits the list.", "beside"),
    "winamp": ("4", "Welcome! The Winamp view is one click away whenever you "
                    "miss those classic skins. To go back, click on the "
                    "Frequencies icon to the right of the repeat button in "
                    "the main FreqAmp window, or right click on the titlebar "
                    "and choose \"Show the main window\"", "winamp"),
    "visualizer": ("5", "Ooh, pretty! In the visualizer, press Space for the "
                        "next preset, R for a random one, D to disable the "
                        "current visualizer preset and go to the next, L for "
                        "the whole list and F11 for full screen.", "beside"),
    "share": ("6", "Sharing is caring! To share a frequency, right click and "
                   "choose share. You can scan the QR code with the mobile "
                   "app, or save it as a photo to share it on desktop. To "
                   "send several at once, ctrl + click a few stations, then "
                   "right-click and choose Share these frequencies. Tip: "
                   "using the QR code to share them will carry over the "
                   "station's enhanced rich data, as well as its tags!",
              "inside"),
    "chat": ("7", "This station is live on Twitch, so its chat shows up right "
                  "here. Sign in under Settings > Accounts to join the "
                  "conversation.", "inside"),
    "schedule": ("8", "This is the Schedule. Double-click any empty space in "
                      "a show's row to create a show, or right-click it and "
                      "choose Edit show… to change it. You can use this to "
                      "get notified when it's showtime for your favorite "
                      "radio blocks!", "inside"),
    "sidebar": ("9", "You can remove features from the sidebar you don't "
                     "need by right clicking on them. If you want them back, "
                     "check the settings page!", "beside"),
    "down": ("10", "Oh no! It looks like this frequency is down at the "
                   "moment! Down streams will be excluded from the queue, but "
                   "you can always try them again manually. Frequencies will "
                   "periodically check to see which streams marked as Down "
                   "are back up, and add them back into your queue "
                   "automatically.", "row"),
    # Asked 2026-10-19 (after the script): offer the developer's photo pack.
    "photos": ("11", "Would you like to download the developer's artist "
                     "photo pack? The photos are hand curated, and it's "
                     "highly recommended for a good time!", "center"),
    # Asked 2026-10-19 (task 150): the first Twitch frequency added.
    "twitch": ("12", "Twitch streams have forced 30-second ads, so by "
                     "default, Frequencies will cover them. You can change "
                     "that in the station options.", "row"),
    # Asked 2026-10-19 (task 173): every time a frequency is added.
    "art": ("14", "Frequencies can find the right station art for this "
                  "frequency! Open its station settings and click \"Find\" "
                  "on the Display tab.", "row"),
    # Asked 2026-10-19 (task 155): the first time lyrics are found.
    "lyrics": ("13", "If the lyrics are ever out of sync, double-click the "
                     "current lyric line to sync them up again.", "inside"),
}

# Tips that come back each time they are asked for (task 173): closing
# one ends that showing only.
REPEATING = {"art"}

# Tips that ask Yes or No, and what he says after each answer.
ASKS = {
    "welcome": ("Great! " + GO_ON, TOO_BAD + GO_ON),
    "photos": ("Great! The artist photo pack is downloading now. You can "
               "turn it off any time in Settings > Lyrics and artists.",
               "No problem! You can turn it on any time in Settings > "
               "Lyrics and artists."),
}

_SHEET = None
_ANIMS = None


def _load():
    global _SHEET, _ANIMS
    if _ANIMS is None:
        try:
            with open(os.path.join(HERE, "animations.json")) as f:
                _ANIMS = {a["Name"]: a["Frames"] for a in json.load(f)}
            _SHEET = QPixmap(os.path.join(HERE, "map.png"))
        except (OSError, ValueError, KeyError) as exc:
            crashlog.write(f"[companion] art missing: {exc}")
            _ANIMS = {}
    return bool(_ANIMS) and _SHEET is not None and not _SHEET.isNull()


class _Grab:
    """Mouse handling shared by Clippy and his bubble: every click stops
    here (nothing underneath gets it), and a press-and-drag moves him."""

    def mousePressEvent(self, e):
        e.accept()
        if e.button() == Qt.LeftButton:
            self.companion.drag_start(e.globalPos())

    def mouseMoveEvent(self, e):
        e.accept()
        if e.buttons() & Qt.LeftButton:
            self.companion.drag_move(e.globalPos())

    def mouseReleaseEvent(self, e):
        e.accept()
        self.companion.drag_end()

    def mouseDoubleClickEvent(self, e):
        e.accept()

    def wheelEvent(self, e):
        e.accept()


class Sprite(_Grab, QWidget):
    """Plays one of Clippy's animations at a time, frame by frame."""

    def __init__(self, parent, companion):
        super().__init__(parent)
        self.companion = companion
        self.setFixedSize(FRAME_W, FRAME_H)
        self.setCursor(Qt.OpenHandCursor)
        self._frames = []
        self._i = 0
        self._then = None
        self._cell = (0, 0)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._step)

    def play(self, name, then=None):
        frames = (_ANIMS or {}).get(name) or []
        self._timer.stop()
        self._frames, self._i, self._then = frames, 0, then
        self._step()

    def stop(self):
        self._timer.stop()
        self._frames, self._then = [], None

    def _step(self):
        if self._i >= len(self._frames):
            then, self._then = self._then, None
            if then is not None:
                then()
            return
        frame = self._frames[self._i]
        self._i += 1
        at = frame.get("ImagesOffsets")     # None: a blank frame
        self._cell = (at["Column"], at["Row"]) if at else None
        self.update()
        self._timer.start(max(16, int(frame["Duration"])))

    def paintEvent(self, _e):
        if _SHEET is None or self._cell is None:
            return
        col, row = self._cell
        p = QPainter(self)
        p.drawPixmap(0, 0, _SHEET,
                     col * FRAME_W, row * FRAME_H, FRAME_W, FRAME_H)


class Bubble(_Grab, QWidget):
    """Clippy's yellow speech bubble, with an X in the top-right corner
    and, for the first tip, Yes and No."""

    PAD = 12

    def __init__(self, parent, companion):
        super().__init__(parent)
        self.companion = companion
        self.W = 250
        self.text = QLabel(self)
        self.text.setWordWrap(True)
        self.text.setStyleSheet("color: #000; background: transparent;"
                                "font-size: 13px;")
        self.close_btn = QToolButton(self)
        self.close_btn.setText("✕")
        self.close_btn.setToolTip("Close this tip")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setStyleSheet(
            "QToolButton { color: #000; background: transparent;"
            " border: none; font-size: 12px; padding: 0 2px; }"
            "QToolButton:hover { color: #c00; }")
        self.close_btn.clicked.connect(companion.close_tip)
        self.answers = QWidget(self)
        row = QHBoxLayout(self.answers)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addStretch(1)
        for label, value in (("Yes", "yes"), ("No", "no")):
            b = QPushButton(label, self.answers)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton { color: #000; background: #f3f3e0;"
                " border: 1px solid #000; border-radius: 3px;"
                " padding: 3px 16px; font-size: 13px; }"
                "QPushButton:hover { background: #fff; }")
            b.clicked.connect(lambda _c, v=value: companion.answer(v))
            row.addWidget(b)
        self.answers.hide()
        self.tail = "down"              # "down", "up" or "left"
        self.tail_at = self.W - 60
        self._h = 40

    def set_text(self, text, ask=False):
        # The long tips get a wider bubble, so they don't run tall.
        self.W = 330 if len(text) > 200 else 250
        self.text.setText(text)
        inner = self.W - 2 * self.PAD - 16
        self.text.setFixedWidth(inner)
        h = self.text.heightForWidth(inner)
        self.text.setFixedHeight(h)
        self.answers.setVisible(ask)
        if ask:
            self.answers.setFixedWidth(inner + 16)
            self.answers.adjustSize()
            h += self.answers.sizeHint().height() + 10
        self._h = h + 2 * self.PAD
        self._lay_out()

    def _box(self):
        """The rounded part; the tail takes 12 px on its side."""
        left = 12 if self.tail == "left" else 0
        top = 12 if self.tail == "up" else 0
        return QRect(left, top, self.W, self._h)

    def _lay_out(self):
        side = self.tail == "left"
        self.setFixedSize(self.W + (12 if side else 0),
                          self._h + (0 if side else 12))
        box = self._box()
        self.text.move(box.x() + self.PAD, box.y() + self.PAD)
        self.answers.move(box.x() + self.PAD,
                          box.y() + self.PAD + self.text.height() + 10)
        self.close_btn.adjustSize()
        self.close_btn.move(box.right() - self.close_btn.width() - 4,
                            box.y() + 3)

    def set_tail(self, side, at):
        self.tail, self.tail_at = side, at
        self._lay_out()
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = self._box().adjusted(1, 1, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(box.x(), box.y(), box.width(), box.height(), 8, 8)
        tail = QPainterPath()
        if self.tail == "left":
            y = max(14, min(box.bottom() - 24, self.tail_at))
            tail.moveTo(box.left() + 1, y - 8)
            tail.lineTo(1, y + 4)
            tail.lineTo(box.left() + 1, y + 8)
        else:
            x = max(20, min(box.right() - 30, self.tail_at))
            edge = box.bottom() - 1 if self.tail == "down" else box.top() + 1
            tip = self.height() - 1 if self.tail == "down" else 1
            tail.moveTo(x - 8, edge)
            tail.lineTo(x + 6, tip)
            tail.lineTo(x + 10, edge)
        tail.closeSubpath()
        path = path.united(tail)
        p.setPen(QPen(QColor("#000"), 1))
        p.setBrush(QColor("#ffffcc"))
        p.drawPath(path)


class _Float(QWidget):
    """Where he stands in the Winamp view (tip 4): a small see-through
    window beside the skinned stack, since the main window is hidden then.
    A tooltip-type window, so Wayland places it next to the stack too."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)


class Companion:
    """The main window's Clippy.

    `tip(id)` asks for a tip (a closed one never shows again, so callers
    don't keep track). `targets` maps tip id → a function giving the
    widget the tip is about (or (widget, QRect) for part of one, or None
    when it isn't on screen now). The timed tips (2.5–2.9, 6, 9, 10) are
    asked for here, once a second.
    """

    def __init__(self, window, settings, save, targets=None,
                 is_playing=None, winamp=None, photos=None):
        self.window = window
        self.settings = settings
        self.save = save
        self.targets = dict(targets or {})
        self.is_playing = is_playing or (lambda: False)
        self.winamp = winamp or (lambda: None)
        # (offer it now?, turn it on): the photo pack question.
        self.photos = photos or (lambda: False, lambda: None)
        self.wanted = []                # asked for, not closed; newest last
        self.current = None             # the id showing
        self.ok = True                  # until the art fails to load
        self.answered = {}              # id → "yes" / "no", this run
        self.moved = {}                 # id → where he was dragged to
        self.last_close = -1e9          # time.monotonic() of the last ✕
        self.play_since = None
        self.open_s = int(settings.get(OPEN_SETTING) or 0)
        self.share_at = SHARE_AT + random.randint(0, 180)
        self.float = _Float()
        self.sprite = Sprite(window, self)
        self.sprite.hide()
        self.bubble = Bubble(window, self)
        self.bubble.hide()
        self._drag = None
        self._idle = QTimer(window)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self._fidget)
        self._tick = QTimer(window)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._each_second)
        self._tick.start()
        window.installEventFilter(_Watch(window, self))

    def closed(self):
        return set(self.settings.get(SETTING) or [])

    # -- asking ------------------------------------------------------------

    def tip(self, tip_id, target=None):
        if not self.ok or tip_id not in TIPS or tip_id in self.closed():
            return
        if target is not None and tip_id not in self.targets:
            self.targets[tip_id] = lambda t=target: t
        if tip_id in self.wanted:
            self.wanted.remove(tip_id)
        self.wanted.append(tip_id)
        self.update()

    def reset(self):
        """Settings > "Reset Clippy's hot tips": every tip again, from 1."""
        self.settings[SETTING] = []
        self.settings[OPEN_SETTING] = 0
        self.settings[TOO_BAD_SETTING] = False
        self.save()
        self.open_s = 0
        self.answered = {}
        self.moved = {}
        self.last_close = -1e9
        self.wanted = []
        self._hide()
        crashlog.write("[companion] tips reset; all show again from tip 1")
        self.tip("welcome")

    def _each_second(self):
        closed = self.closed()
        if len(closed) >= len(TIPS):
            return
        now = time.monotonic()
        showing_app = self.window.isVisible() or self._winamp_open()
        if showing_app and not {"share", "sidebar", "photos"} <= closed:
            self.open_s += 1
            if self.open_s % 300 == 0:
                self.settings[OPEN_SETTING] = self.open_s
                self.save()
        playing = bool(self.is_playing())
        if not playing:
            self.play_since = None
        elif self.play_since is None:
            self.play_since = now
        # The timed tips wait for a quiet moment: nothing showing and a few
        # seconds since the last one closed. They never cut in.
        quiet = self.current is None and now - self.last_close >= GAP
        if quiet:
            for tip_id, due in (
                    ("search", "welcome" in closed),
                    ("queue", "added" in closed),
                    ("shazam", "queue" in closed and playing
                     and now - self.play_since >= PLAYED),
                    ("favorite", "shazam" in closed),
                    ("options", "favorite" in closed),
                    ("share", self.open_s >= self.share_at),
                    ("photos", self.open_s >= PHOTOS_AT
                     and self.photos[0]()),
                    ("sidebar", self.open_s >= SIDEBAR_AT),
                    ("down", self._resolve("down") is not None)):
                if due and tip_id not in closed and tip_id not in self.wanted:
                    self.wanted.append(tip_id)
                    break
        self.update()

    # -- which tip, where --------------------------------------------------

    def _winamp_open(self):
        dock = self._dock()
        return dock is not None and dock.isVisible()

    def _dock(self):
        try:
            return self.winamp()
        except Exception:                       # noqa: BLE001
            return None

    def _resolve(self, tip_id):
        """The tip's place in window coordinates, or None if it isn't on
        screen now."""
        if TIPS[tip_id][2] == "winamp":
            return QRect() if self._winamp_open() else None
        if not self.window.isVisible():
            return None
        if TIPS[tip_id][2] == "center":
            return self.window.rect()
        find = self.targets.get(tip_id)
        try:
            found = find() if find is not None else None
            if found is None:
                return None
            widget, rect = (found if isinstance(found, tuple)
                            else (found, found.rect()))
            if not widget.isVisible():
                return None
            return QRect(widget.mapTo(self.window, rect.topLeft()),
                         rect.size())
        except RuntimeError:                    # the widget is gone
            return None

    def update(self):
        """Show the newest tip asked for whose place is on screen."""
        if not self.ok:
            return
        closed = self.closed()
        self.wanted = [t for t in self.wanted if t not in closed]
        pick = place = None
        for tip_id in reversed(self.wanted):
            place = self._resolve(tip_id)
            if place is not None:
                pick = tip_id
                break
        if pick is None:
            if self.current is not None:
                self._hide()
            return
        if pick == self.current:
            if self._drag is None:
                self._put(place)
            return
        if not _load():
            self.ok, self.wanted = False, []
            return
        if self.current is not None:
            crashlog.write(f"[companion] tip {self.current!r} waits; "
                           f"{pick!r} is what's on screen now")
            self._hide()
        self._show(pick, place)

    def _show(self, tip_id, place):
        self.current = tip_id
        crashlog.write(f"[companion] tip {tip_id!r} ({TIPS[tip_id][0]})")
        host = self.float if TIPS[tip_id][2] == "winamp" else self.window
        for part in (self.sprite, self.bubble):
            if part.parentWidget() is not host:
                part.setParent(host)
        if tip_id in self.answered:
            self.bubble.set_text(self._answer_text(tip_id))
        else:
            self.bubble.set_text(TIPS[tip_id][1], ask=tip_id in ASKS)
        self._put(place)
        if host is self.float:
            self.float.show()
        self.sprite.show()
        self.sprite.raise_()
        self.sprite.play("Show", self._say)

    def _hide(self):
        self.current = None
        self._drag = None
        self._idle.stop()
        self.sprite.stop()
        self.sprite.hide()
        self.bubble.hide()
        self.float.hide()

    def _say(self):
        if self.current is None:
            return
        self.bubble.show()
        self.bubble.raise_()
        self.sprite.play("Explain", self._point)

    def _point(self):
        if self.current is None:
            return
        self.sprite.play(self._gesture(), self._rest)

    def _rest(self):
        if self.current is None:
            return
        self.sprite.play("RestPose")
        self._idle.start(random.randint(5000, 9000))

    def _fidget(self):
        if self.current is None or not self.sprite.isVisible():
            return
        names = [n for n in (_ANIMS or {})
                 if n.startswith("Idle") and n != "IdleSnooze"]
        self.sprite.play(random.choice(names), self._rest)

    def answer(self, value):
        """Yes or No: the follow-up line, then the ✕ as usual."""
        tip_id = self.current
        if tip_id not in ASKS:
            return
        self.answered[tip_id] = value
        crashlog.write(f"[companion] tip {tip_id!r}: {value}")
        if tip_id == "photos" and value == "yes":
            try:
                self.photos[1]()
            except Exception as exc:            # noqa: BLE001
                crashlog.write(f"[companion] photo pack didn't turn on: "
                               f"{exc!r}")
        self.bubble.set_text(self._answer_text(tip_id))
        self.update_bubble()

    def _answer_text(self, tip_id):
        yes, no = ASKS[tip_id]
        if self.answered.get(tip_id) == "yes":
            return yes
        if no.startswith(TOO_BAD):
            if self.settings.get(TOO_BAD_SETTING):
                return no[len(TOO_BAD):]
            self.settings[TOO_BAD_SETTING] = True
            self.save()
        return no

    def update_bubble(self):
        if self.current is not None:
            self._put(self._resolve(self.current) or QRect())

    def close_tip(self):
        if self.current is None:
            return
        tip_id = self.current
        if tip_id in REPEATING:
            if tip_id in self.wanted:
                self.wanted.remove(tip_id)
        else:
            self.settings[SETTING] = sorted(self.closed() | {tip_id})
            self.save()
        crashlog.write(f"[companion] tip {tip_id!r} closed")
        self.current = None
        self.last_close = time.monotonic()
        self._idle.stop()
        self.bubble.hide()
        self.sprite.play("GoodBye", self._gone)

    def done(self, tip_id):
        """The user did what the tip says: it never needs to show."""
        if tip_id in self.closed():
            return
        if self.current == tip_id:
            self.close_tip()
            return
        self.settings[SETTING] = sorted(self.closed() | {tip_id})
        self.save()
        if tip_id in self.wanted:
            self.wanted.remove(tip_id)
        crashlog.write(f"[companion] tip {tip_id!r} done before it showed")

    def _gone(self):
        if self.current is not None:
            return
        self.sprite.stop()
        self.sprite.hide()
        self.float.hide()
        QTimer.singleShot(800, self.update)

    # -- dragging ------------------------------------------------------------

    def drag_start(self, at):
        if self.current is None:
            return
        on_float = TIPS[self.current][2] == "winamp"
        moving = self.float if on_float else self.sprite
        self._drag = (at, moving.pos())
        self.sprite.setCursor(Qt.ClosedHandCursor)

    def drag_move(self, at):
        if self._drag is None or self.current is None:
            return
        start, was = self._drag
        if TIPS[self.current][2] == "winamp":
            self.float.move(was + (at - start))
            return
        pos = was + (at - start)
        win = self.window.rect()
        pos.setX(max(0, min(win.right() - FRAME_W, pos.x())))
        pos.setY(max(0, min(win.bottom() - FRAME_H, pos.y())))
        self.moved[self.current] = pos
        self.sprite.move(pos)
        self._bubble_by(pos, QRect(pos, self.sprite.size()), True)

    def drag_end(self):
        if self._drag is not None and self.current in self.moved:
            crashlog.write(f"[companion] dragged to {self.moved[self.current].x()},"
                           f"{self.moved[self.current].y()}")
        self._drag = None
        self.sprite.setCursor(Qt.OpenHandCursor)

    # -- where he stands -----------------------------------------------------

    def _put(self, r):
        if self.current is None:
            return
        mode = TIPS[self.current][2]
        if mode == "winamp":
            self._put_float()
            return
        win = self.window.rect()
        sw, sh = FRAME_W, FRAME_H
        if self.current in self.moved:
            pos = self.moved[self.current]
            x = max(0, min(win.right() - sw, pos.x()))
            y = max(0, min(win.bottom() - sh, pos.y()))
            self.sprite.move(x, y)
            self._bubble_by(QPoint(x, y), QRect(x, y, sw, sh), True)
            return
        if mode in ("center", "middle"):
            x, y = r.center().x() - sw // 2, r.center().y() - sh // 2 + 40
        elif mode == "inside":
            x, y = r.right() - sw - 24, r.bottom() - sh - 16
        elif mode == "row":
            x, y = r.right() - sw - 24, r.center().y() - sh // 2
        elif r.center().y() + sh // 2 > win.bottom() - 8:
            # A button in the bottom bar: stand on top of it, not over
            # its neighbors.
            x, y = r.center().x() - sw // 2, r.top() - sh + 6
        elif r.right() + sw + 8 <= win.right():
            x, y = r.right() + 8, r.center().y() - sh // 2
        else:
            x, y = r.left() - sw - 8, r.center().y() - sh // 2
        x = max(8, min(win.right() - sw - 8, x))
        y = max(8, min(win.bottom() - sh - 8, y))
        self.sprite.move(x, y)
        self._bubble_by(QPoint(x, y), r, mode != "beside")

    def _bubble_by(self, at, r, may_cover):
        """The bubble over his head, unless that would cover what he is
        pointing at (or run off the top): then beside him, or below."""
        win = self.window.rect()
        x, y = at.x(), at.y()
        sw, sh = FRAME_W, FRAME_H
        self.bubble.set_tail("down", 0)
        bw, bh = self.bubble.width(), self.bubble.height()
        bx = max(8, min(win.right() - bw - 8, x + sw - bw + 30))
        above = QRect(bx, y - bh + 4, bw, bh)
        if above.top() >= 4 and (may_cover or not above.intersects(r)):
            self.bubble.move(above.topLeft())
            self.bubble.set_tail("down", x + sw // 2 - bx)
            return
        self.bubble.set_tail("left", 0)
        bw, bh = self.bubble.width(), self.bubble.height()
        if x + sw + bw <= win.right() - 4:
            by = max(4, min(win.bottom() - bh - 4, y + sh // 2 - 30))
            self.bubble.move(x + sw - 4, by)
            self.bubble.set_tail("left", y + sh // 2 - by - 8)
            return
        self.bubble.set_tail("up", 0)
        bw = self.bubble.width()
        bx = max(8, min(win.right() - bw - 8, x + sw - bw + 30))
        self.bubble.move(bx, y + sh - 4)
        self.bubble.set_tail("up", x + sw // 2 - bx)

    def _put_float(self):
        """Beside the skinned stack, Clippy below his bubble."""
        self.bubble.set_tail("down", 0)
        bw, bh = self.bubble.width(), self.bubble.height()
        w, h = max(bw, FRAME_W) + 8, bh + FRAME_H
        self.float.setFixedSize(w, h)
        self.bubble.move(0, 0)
        self.sprite.move(20, bh - 4)
        self.bubble.set_tail("down", 20 + FRAME_W // 2)
        dock = self._dock()
        if dock is None or "winamp" in self.moved:
            return
        where = dock.frameGeometry()
        x, y = where.right() + 12, where.top()
        screen = QApplication.screenAt(where.center())
        if screen is not None:
            room = screen.availableGeometry()
            if x + w > room.right():
                x = where.left() - w - 12
            x = max(room.left(), min(room.right() - w, x))
            y = max(room.top(), min(room.bottom() - h, y))
        if self.float.windowHandle() is None:
            self.float.create()
        handle = self.float.windowHandle()
        if handle is not None and dock.windowHandle() is not None:
            handle.setTransientParent(dock.windowHandle())
        self.float.move(x, y)

    def _gesture(self):
        if self.current is None or TIPS[self.current][2] != "beside":
            return "GetAttention"
        r = self._resolve(self.current)
        if r is None:
            return "GetAttention"
        here = self.sprite.geometry().center()
        dx = r.center().x() - here.x()
        dy = r.center().y() - here.y()
        if abs(dx) >= abs(dy):
            return "GestureLeft" if dx < 0 else "GestureRight"
        return "GestureUp" if dy < 0 else "GestureDown"


class _Watch(QObject):
    """Keeps him beside his place when the window changes, and checks what
    is on screen when the window comes back from the Winamp view."""

    def __init__(self, window, companion):
        super().__init__(window)
        self.companion = companion

    def eventFilter(self, obj, event):
        if obj is self.companion.window:
            kind = event.type()
            if kind == QEvent.Resize:
                self.companion.update_bubble()
            elif kind in (QEvent.Show, QEvent.Hide):
                QTimer.singleShot(600, self.companion.update)
        return False
