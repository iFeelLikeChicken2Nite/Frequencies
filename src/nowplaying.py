"""Frequencies — full-screen now playing.

Click the artwork in the player bar and the station list gives way to this:
artwork at size, live lyrics beside it, and the whole background filled with
a heavily blurred copy of the art so the view takes its color from whatever
is playing.

The blur is done by downscaling and scaling back up with smooth transforms
rather than QGraphicsBlurEffect, which is slow at this size and has to
re-render every paint.
"""

from PyQt5.QtCore import (QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF,
                          QSize, Qt, pyqtProperty, pyqtSignal)
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPainterPath,
                         QPixmap)
from PyQt5.QtWidgets import (QGraphicsBlurEffect, QGraphicsOpacityEffect,
                             QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
                             QVBoxLayout, QWidget)

import theme
from theme import T


def blurred(pm: QPixmap, size: QSize, strength: int = 22) -> QPixmap:
    """Cheap gaussian-ish blur: throw away detail, then let the smooth
    upscale interpolate it back into a wash.

    One downscale-and-upscale left visible blocks, because bilinear
    interpolation between a handful of pixels is a gradient mesh, not a
    blur — you could see the grid. Downscaling much further and then
    stepping back up in stages fixes it: each pass smooths the one before,
    so what arrives is a wash rather than a lattice. Three passes is where
    the blocking stops being visible on a 4K screen.
    """
    if pm.isNull() or size.isEmpty():
        return QPixmap()

    # Aim for roughly a 12px-wide thumbnail whatever the source size, so a
    # small favicon and a 1400px cover blur to the same softness.
    tiny = max(2, min(16, pm.width() // max(1, strength)))
    small = pm.scaled(tiny, max(2, tiny), Qt.IgnoreAspectRatio,
                      Qt.SmoothTransformation)

    out = small
    for factor in (6, 5):
        out = out.scaled(out.width() * factor, out.height() * factor,
                         Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return out.scaled(size, Qt.KeepAspectRatioByExpanding,
                      Qt.SmoothTransformation)


def rounded(pm: QPixmap, radius: int) -> QPixmap:
    out = QPixmap(pm.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(pm.rect()), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


# --------------------------------------------------------------------------
# Lyrics
# --------------------------------------------------------------------------

class LyricLine(QLabel):
    """One line. Inactive lines sit back; the active one comes forward."""

    resync_here = pyqtSignal(object)        # this line's start, in ms

    def __init__(self, text, parent=None):
        self.start_ms = None
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self._active = False
        self._sung = False
        self.refresh()

    # How far out of focus a line goes, per line away from the one being
    # sung, and the most it ever gets.
    # How far out of focus a line goes, by distance from the one being
    # sung. The curve is deliberately not a straight line: the lines
    # either side of the current one stay easy to read, and it falls
    # away harder further out, so the column has a near field and a far
    # field rather than one even smear.
    BLUR_MAX = 9.0
    BLUR_SOFT = 1.6         # how sharply the far field arrives
    BLUR_SPAN = 7.0         # lines from sharp to fully soft

    def set_state(self, active, sung):
        """Weight and colour. The blur is set separately, by position."""
        if (active, sung) == (self._active, self._sung):
            return
        self._active, self._sung = active, sung
        self.refresh()

    BLUR_MS = 260           # how long a change of blur takes

    def set_blur(self, wanted, ms=None):
        """Set this line's blur, in pixels, easing to it over `ms`.

        Both ways are eased (task 47). Going sharp used to drop the
        effect on the spot, so hovering the lyrics snapped the blur off;
        and a fresh effect started at Qt's default radius of 5, so the
        blur coming back jumped to 5 before it eased. Now a new effect
        starts at 0, and the effect is only dropped once the fade to
        sharp has finished.
        """
        wanted = max(0.0, float(wanted))
        if abs(wanted - getattr(self, "_blur", -1)) < 0.05:
            return
        self._blur = wanted

        anim = getattr(self, "_blur_anim", None)
        if anim is not None:
            anim.stop()
            self._blur_anim = None

        effect = self.graphicsEffect()
        if not isinstance(effect, QGraphicsBlurEffect):
            if wanted <= 0.05:
                return                  # already sharp, nothing to fade
            effect = QGraphicsBlurEffect(self)
            effect.setBlurHints(QGraphicsBlurEffect.PerformanceHint)
            effect.setBlurRadius(0.0)
            self.setGraphicsEffect(effect)

        anim = QPropertyAnimation(effect, b"blurRadius", self)
        anim.setDuration(int(self.BLUR_MS if ms is None else ms))
        anim.setStartValue(effect.blurRadius())
        anim.setEndValue(wanted)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(self._blur_settled)
        anim.start()
        self._blur_anim = anim

    def _blur_settled(self):
        """A fade to sharp is over: drop the effect.

        No effect at all rather than a zero-radius one: an effect costs a
        repaint through an offscreen buffer even when it does nothing.
        """
        if getattr(self, "_blur", 0.0) <= 0.05 \
                and isinstance(self.graphicsEffect(), QGraphicsBlurEffect):
            self._blur_anim = None
            self.setGraphicsEffect(None)

    def mouseDoubleClickEvent(self, e):
        """Double-click: the song is here, whatever the clock thinks.

        On a live stream the clock is dead reckoning from whenever the
        song was identified, so it drifts — and there is no way for the
        app to notice. The listener can see it, though, and pointing at
        the line being sung is the most direct way to say so.
        """
        if self.start_ms is not None:
            self.resync_here.emit(self.start_ms)
        super().mouseDoubleClickEvent(e)

    def reaccent(self):
        """Repaint after the accent moves, without changing state."""
        if self._active:
            self.refresh()

    SIZE = 20               # scaled by the lyrics size setting

    def refresh(self):
        f = QFont()
        base = self.SIZE
        # A clear step, not a nudge: the line being sung should be
        # findable without reading any of the others.
        size = int(base * 1.35) if self._active else base
        f.setPixelSize(size)
        f.setWeight(QFont.Bold if self._active else QFont.Normal)
        self.setFont(f)
        if self._active:
            # The accent, not white: the line being sung is the one thing
            # on the panel that is changing, and the colour is already
            # tied to the artwork it is changing under.
            colour = T["accent"]
        elif self._sung:
            # Further down than before.
            #
            # At 70 and 105 the difference between the line being sung and
            # the rest was there but weak, so the panel read as a block of
            # text with one slightly brighter line in it rather than as a
            # song moving through the words. The timed case can afford a
            # much deeper dim, because something is always lit.
            colour = "rgba(255, 255, 255, 48)"     # already gone past
        else:
            colour = "rgba(255, 255, 255, 60)"     # not reached yet
        # Size and weight go in the stylesheet as well as the font.
        #
        # The theme sets font-size on QWidget, which every QLabel inherits,
        # and a stylesheet rule beats setFont every time — so the size
        # chosen here was being discarded and every line came out at the
        # theme's 13px whatever the slider said.
        self.setStyleSheet(
            f"background: transparent; color: {colour};"
            f" font-size: {size}px;"
            f" font-weight: {'bold' if self._active else 'normal'};"
            f" padding: {max(4, size // 3)}px 0;")


class LyricsView(QScrollArea):
    resync_requested = pyqtSignal(object)   # a line's start, in ms

    """Timed or plain lyrics. Feed it milliseconds and it keeps up."""

    def set_text_size(self, pixels):
        """The base size for an inactive line; the live one scales off it."""
        LyricLine.SIZE = max(11, min(40, int(pixels)))
        for _start, widget in self._lines:
            widget.refresh()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Only the area is made transparent here; the scrollbar is left to
        # the theme.
        #
        # This set its own handle in translucent white, which matched the
        # dark theme by coincidence and nothing else: on a light theme it
        # vanished, and on the others it was a different grey from every
        # other scrollbar in the app. The theme already draws them all
        # the same way, so the right amount of styling here is none.
        self.setStyleSheet("QScrollArea { background: transparent; }")
        self.viewport().setStyleSheet("background: transparent;")

        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._lay = QVBoxLayout(self._inner)
        # The last of that bottom margin is the switch band in the panel;
        # in the full-screen view it is simply room to breathe.
        self._lay.setContentsMargins(0, 40, 8, 96)
        self._lay.setSpacing(0)
        # No stretch between the last line and the credit: the caption
        # belongs to the words, and a spring in between pushed it to the
        # bottom of a mostly empty column.
        # A caption that lives at the end of the words rather than on top
        # of the panel, so it scrolls away with them.
        self.footer = QLabel("")
        self.footer.setProperty("role", "tertiary")
        self.footer.setWordWrap(True)
        self.footer.setTextFormat(Qt.RichText)
        self.footer.setOpenExternalLinks(True)
        self.footer.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.footer.setAlignment(Qt.AlignCenter)
        self.footer.setStyleSheet("background: transparent;")
        self.footer.setContentsMargins(0, 18, 0, 6)
        self._lay.addWidget(self.footer)

        self.setWidget(self._inner)

        self._lines = []          # (start_ms or None, LyricLine)
        self._current = -1
        self._inside = False

        # Hover: the blur clears on an enter event and comes back on a
        # leave event. Miss the leave (task 29) and the panel is stuck with
        # no blur at all, so hovering changes nothing. While the pointer is
        # over the words, the window's own mouse moves are watched too, and
        # the first one anywhere else counts as leaving.
        self._watched_window = None

        self._scroll = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._scroll.setDuration(420)
        self._scroll.setEasingCurve(QEasingCurve.OutCubic)

    def clear(self):
        for _, w in self._lines:
            w.hide()                # or Qt's queued show makes it a window (task 148)
            w.setParent(None)
        self._lines.clear()
        self._current = -1

    def _add_line(self, widget):
        """Lines go above the credit, which stays last."""
        self._lay.insertWidget(max(0, self._lay.count() - 1), widget)

    def set_lyrics(self, lines, message=None):
        """The words, all of them, evenly.

        Lines arrive either bare or as (seconds, text). Bare ones are
        drawn evenly — a lyric sheet, which is all a live stream can
        honestly offer. Stamped ones are dimmed until the song reaches
        them and lit as it passes, which is what set_position does.
        """
        self.clear()
        if not lines:
            placeholder = LyricLine(message or "No lyrics found for this song.")
            placeholder.set_state(False, False)
            self._add_line(placeholder)
            self._lines.append((None, placeholder))
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            return

        self._current = None
        self._watch_scroll()
        self._watch_user_scroll()
        for entry in lines:
            start, text = entry if isinstance(entry, (tuple, list)) else (None, entry)
            w = LyricLine(text)
            w.start_ms = start
            w.resync_here.connect(self.resync_requested.emit)
            # Dim until the song reaches it, when there is a timing to
            # reach; evenly weighted when there is not.
            w.set_state(False, start is not None)
            self._add_line(w)
            self._lines.append((start, w))

        # Always scrollable now: nothing moves the view on its own, so the
        # bar is how the rest of the song is reached.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Soften the edges as soon as the lines are laid out. Only a new
        # position did this before, and while paused there is none — so
        # lyrics that arrived (or were redrawn) during a pause had no blur
        # at all, and hovering had nothing to clear (task 29).
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, self.refresh_blur)

    def set_position(self, ms):
        """Light the line the song is on, and keep it in view.

        Only when the lyrics arrived with timings — a plain sheet has
        nothing to be current about, and guessing a position on a live
        stream is what made this a no-op in the first place. A track with
        real stamps and a real clock is a different thing entirely.
        """
        timed = [pair for pair in self._lines if pair[0] is not None]
        if not timed:
            return

        # Milliseconds throughout.
        #
        # The stamps arrive already converted to milliseconds, so
        # dividing the position by a thousand compared a clock in
        # seconds against marks in milliseconds — every mark was in the
        # future and the highlight never left the first line.
        now = max(0.0, float(ms or 0))
        current = None
        for start, widget in timed:
            if start <= now:
                current = widget
            else:
                break
        if current is self._current:
            return
        self._current = current

        # Each line is told how far it is from the one being sung, so it
        # can place itself in or out of focus.
        here = -1
        for start, widget in self._lines:
            widget.set_state(widget is current, start is not None)
        self.refresh_blur()

        # Follow it, but only while nobody is scrolling by hand: dragging
        # back to read an earlier verse and being yanked forward again is
        # worse than not following at all.
        if current is not None and not self._user_scrolling():
            self._glide_to(current)

    GLIDE_MS = 420

    # Where the focus begins and ends, as a share of the panel's height.
    #
    # Blur by position, not by distance from the line being sung. A line
    # two away from the current one is perfectly readable when it is in
    # the middle of the panel; the same line is at the edge when the
    # panel is short. What should go soft is what is running off the top
    # and bottom, which is a fact about the panel rather than about the
    # song.
    FOCUS_BAND = 0.34       # share of the height, either side of centre,
                            # that stays completely sharp
    BLUR_EDGE = 9.0         # blur at the very top and bottom

    def resizeEvent(self, e):
        # The soft band is measured from the panel's own height, so it
        # has to be worked out again whenever that height changes.
        super().resizeEvent(e)
        self.refresh_blur()

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh_blur()

    def hideEvent(self, e):
        # Hidden is not being read: no leave event comes for a view that
        # is swapped away (side panel modes, closing full screen).
        super().hideEvent(e)
        self._set_inside(False)

    def _watch_scroll(self):
        """Recompute the blur whenever the column moves under the panel.

        The effect is anchored to the panel, not to the words, so it has
        to be recalculated on every scroll — otherwise the soft band
        travels with the text instead of staying at the edges.
        """
        bar = self.verticalScrollBar()
        if not getattr(self, "_blur_watching", False):
            bar.valueChanged.connect(lambda _v: self.refresh_blur())
            self._blur_watching = True

    # How long the blur takes to fade out when the pointer comes onto
    # the lyrics, and back in when it leaves (task 47).
    HOVER_FADE_MS = 200

    def refresh_blur(self, ms=None):
        """Sharp through the middle, soft towards the edges.

        Skipped entirely while the pointer is over the panel: reading is
        what the blur gets in the way of, so hovering clears it and
        leaving puts it back — at the same moment the song takes the
        scrolling over again. `ms` is how long each line takes to get
        there (None: the line's usual time).
        """
        height = self.viewport().height()
        if height <= 0:
            return
        centre = height / 2.0
        clear = self.FOCUS_BAND * height
        span = max(1.0, centre - clear)
        reading = getattr(self, "_inside", False)

        top = self.verticalScrollBar().value()

        # An edge with the song in it stays sharp.
        #
        # At the start of a song the panel cannot scroll up far enough to
        # centre the first lines, so the line being sung sits near the
        # top — right where the blur is heaviest. The same happens at the
        # end, at the bottom. Whichever edge the current line has been
        # pushed into is left clear until the song moves away from it.
        sharp_top = sharp_bottom = False
        current = getattr(self, "_current", None)
        # Before any lyrics arrive this is a plain number, not a line.
        if current is not None and hasattr(current, "y"):
            here = current.y() + current.height() / 2.0 - top
            sharp_top = here < centre - clear
            sharp_bottom = here > centre + clear

        for _start, widget in self._lines:
            middle = widget.y() + widget.height() / 2.0 - top
            if middle < -40 or middle > height + 40:
                widget.set_blur(0.0, ms)        # off screen: nothing to blur
                continue
            if reading:
                widget.set_blur(0.0, ms)
                continue
            if (sharp_top and middle < centre) or (
                    sharp_bottom and middle > centre):
                widget.set_blur(0.0, ms)
                continue
            out = max(0.0, abs(middle - centre) - clear)
            widget.set_blur(self.BLUR_EDGE * min(1.0, out / span) ** 1.5, ms)

    def _glide_to(self, widget):
        """Slide the panel so this line sits in the middle.

        ensureWidgetVisible jumps, and it only moves far enough to bring
        a line inside the viewport — which is why the sung line landed
        somewhere vague rather than in the centre. Working out the exact
        offset and animating to it puts the line in one place and keeps
        it there, and the movement is what reads as following along.
        """
        from PyQt5.QtCore import QPropertyAnimation

        bar = self.verticalScrollBar()
        centre = (widget.mapTo(self.widget(), QPoint(0, 0)).y()
                  + widget.height() // 2)
        target = centre - self.viewport().height() // 2
        target = max(bar.minimum(), min(bar.maximum(), target))
        if abs(target - bar.value()) <= 1:
            return

        glide = getattr(self, "_glide", None)
        if glide is not None:
            glide.stop()
        glide = QPropertyAnimation(bar, b"value", self)
        glide.setDuration(self.GLIDE_MS)
        glide.setStartValue(bar.value())
        glide.setEndValue(target)
        glide.setEasingCurve(QEasingCurve.OutCubic)
        glide.start()
        self._glide = glide

    # How long a hand-scrolled panel stays put with the pointer resting
    # on it and nothing happening.
    PIN_SECONDS = 5

    def _user_scrolling(self):
        """Whether the reader has taken over.

        Pinned by scrolling, not by hovering: moving the pointer across
        the panel on the way somewhere else is not an instruction, and
        treating it as one meant the song stopped following for no
        reason anybody could see.

        It lets go when the pointer leaves, and also after a quiet spell
        — a pointer parked over the lyrics while you do something else is
        not someone reading, and the song should carry on without you
        having to move the mouse to prove it.
        """
        bar = self.verticalScrollBar()
        if bar.isSliderDown():
            return True
        if not getattr(self, "_pinned", False):
            return False

        import time as _time
        since = _time.monotonic() - getattr(self, "_pinned_at", 0.0)
        if since > self.PIN_SECONDS:
            self._pinned = False
            return False
        return True

    def enterEvent(self, e):
        super().enterEvent(e)
        self._set_inside(True)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._set_inside(False)

    def eventFilter(self, obj, event):
        """While hovered: a mouse move outside the words is a leave."""
        from PyQt5.QtCore import QEvent
        watched = getattr(self, "_watched_window", None)
        if watched is None or obj is not watched:
            # The scroll area's own filter (it watches its contents).
            return super().eventFilter(obj, event)
        if getattr(self, "_inside", False):
            kind = event.type()
            if kind == QEvent.Leave:
                self._set_inside(False)
            elif kind == QEvent.MouseMove:
                # The whole view, scroll bar included. Only the words
                # counted before, so reaching for the bar brought the blur
                # back and ended the hold, and coming back off the bar
                # never cleared it again (no new enter event for that).
                try:
                    local = self.mapFrom(self.window(), event.pos())
                except (RuntimeError, TypeError):
                    local = None
                if local is None or not self.rect().contains(local) \
                        or not self.isVisible():
                    self._set_inside(False)
        return False

    def _set_inside(self, inside):
        inside = bool(inside)
        if inside == getattr(self, "_inside", False):
            return
        self._inside = inside
        self._watch_window(inside)
        if not inside:
            # Pointer gone: hand the panel back to the song, and let the
            # edges soften again as it resumes.
            self._pin(False)
        # Eased, not snapped (task 47): the edges fade sharp as the
        # pointer arrives and fade soft again as it goes.
        self.refresh_blur(self.HOVER_FADE_MS)

    def _watch_window(self, on):
        """Listen to the window's mouse moves only while hovered."""
        old = self._watched_window
        if old is not None:
            try:
                old.removeEventFilter(self)
            except RuntimeError:
                pass
            self._watched_window = None
        if on:
            window = self.window().windowHandle()
            if window is not None:
                window.installEventFilter(self)
                self._watched_window = window

    def wheelEvent(self, e):
        super().wheelEvent(e)
        if getattr(self, "_inside", False):
            self._pin()

    def mousePressEvent(self, e):
        # Dragging the bar counts as taking over too.
        super().mousePressEvent(e)
        self._pin()

    def scrolled_by_hand(self):
        """The smooth scroller took a wheel turn or a key here (task 35).

        It handles the wheel on the viewport and eats it, and it moves the
        bar with setValue, which fires none of the bar's own signals — so
        neither wheelEvent nor actionTriggered ever saw a wheel scroll,
        and the song pulled the lyrics back at the next line. The
        scroller calls this instead, before it moves anything.

        Stop any glide to the sung line first: it and the wheel would
        both be driving the bar.
        """
        for name in ("_glide", "_scroll"):
            anim = getattr(self, name, None)
            if anim is not None:
                try:
                    anim.stop()
                except RuntimeError:
                    pass
        self._pin()

    def _watch_user_scroll(self):
        """Pin when the reader moves the bar, however they move it.

        wheelEvent here never fires: the smooth-scrolling filter handles
        the wheel on the viewport and consumes it (that path pins through
        scrolled_by_hand). The scrollbar knows when it was moved by a
        person — actionTriggered is only emitted for a gesture, never for
        a value set in code.
        """
        bar = self.verticalScrollBar()
        if not getattr(self, "_user_scroll_watched", False):
            bar.actionTriggered.connect(lambda _a: self._pin())
            bar.sliderPressed.connect(self._pin)
            self._user_scroll_watched = True

    def _pin(self, on=True):
        import time as _time
        self._pinned = bool(on)
        self._pinned_at = _time.monotonic()

    def reaccent(self):
        for _start, widget in self._lines:
            widget.reaccent()

    def _centre(self, widget):
        target = (widget.pos().y()
                  - self.viewport().height() // 2
                  + widget.height() // 2)
        target = max(0, min(target, self.verticalScrollBar().maximum()))
        self._scroll.stop()
        self._scroll.setStartValue(self.verticalScrollBar().value())
        self._scroll.setEndValue(target)
        self._scroll.start()


# --------------------------------------------------------------------------
# The view
# --------------------------------------------------------------------------

class NowPlaying(QWidget):
    """Full-bleed now playing. Lives as a page in the shell's stack, so the
    player bar stays put underneath it."""

    dismissed = pyqtSignal()

    ART_MAX = 420
    ART_MIN = 220
    PHOTO = 132
    PHOTO_MAX = 460         # tallest the full-screen portrait may be

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NowPlaying")
        self._art = QPixmap()
        self._backdrop = QPixmap()
        self._backdrop_stale = False
        self._veil = 150          # how far the backdrop is knocked back

        outer = QHBoxLayout(self)
        outer.setContentsMargins(48, 40, 40, 40)
        outer.setSpacing(48)

        # Art and titles, then the words, then who made them: three
        # columns, most concrete on the left.
        outer.addWidget(self._build_left(), 0)
        self.lyrics = LyricsView()
        outer.addWidget(self.lyrics, 3)
        self._artist_col = self._build_artist()
        outer.addWidget(self._artist_col, 2)

    def _build_left(self):
        col = QWidget()
        col.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.close_btn = QLabel("Back to stations")
        self.close_btn.setStyleSheet(
            "background: transparent; color: rgba(255,255,255,150);"
            "font-size: 12px; padding: 0 0 18px 2px;")
        self.close_btn.mouseReleaseEvent = lambda _e: self.dismissed.emit()
        lay.addWidget(self.close_btn, 0, Qt.AlignLeft)

        self.art_label = QLabel()
        self.art_label.setFixedSize(self.ART_MAX, self.ART_MAX)
        self.art_label.setStyleSheet("background: transparent;")
        lay.addWidget(self.art_label, 0, Qt.AlignLeft)
        lay.addSpacing(26)

        self.song = QLabel("Nothing playing")
        self.song.setWordWrap(True)
        self.song.setStyleSheet(
            f"background: transparent; color: {T['text']}; font-size: 30px;")
        lay.addWidget(self.song)

        self.artist = QLabel("")
        self.artist.setStyleSheet(
            "background: transparent; color: rgba(255,255,255,175);"
            "font-size: 18px; padding-top: 4px;")
        lay.addWidget(self.artist)

        self.station = QLabel("")
        self.station.setStyleSheet(
            "background: transparent; color: rgba(255,255,255,120);"
            "font-size: 13px; padding-top: 14px;")
        lay.addWidget(self.station)

        lay.addStretch(1)
        return col

    def _build_artist(self):
        """Photo and biography, hidden until there is one.

        An empty column is worse than a narrower layout: the lyrics take
        the space back when nothing is known about the artist.
        """
        col = QScrollArea()
        col.setWidgetResizable(True)
        col.setFrameShape(QScrollArea.NoFrame)
        col.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        col.setStyleSheet("QScrollArea { background: transparent; }")
        col.setMinimumWidth(260)
        col.setMaximumWidth(520)

        # Scrolled, so a long biography runs on instead of being cut off
        # at the bottom of the screen.
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 34, 8, 24)
        lay.setSpacing(16)

        self.artist_name = QLabel("")
        self.artist_name.setWordWrap(True)
        self.artist_name.setStyleSheet(
            f"background: transparent; color: {T['text']}; font-size: 22px;")
        lay.addWidget(self.artist_name)

        self.artist_bio = QLabel("")
        self.artist_bio.setWordWrap(True)
        self.artist_bio.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.artist_bio.setStyleSheet(
            "background: transparent; color: rgba(255,255,255,180);"
            "font-size: 16px; line-height: 155%;")
        lay.addWidget(self.artist_bio)

        # Under the words rather than over them, and large: at the foot of
        # a column of prose a photograph reads as who that was about,
        # where above it competes with the album art on the other side.
        self.artist_photo = QLabel()
        self.artist_photo.setStyleSheet("background: transparent;")
        self.artist_photo.setVisible(False)
        lay.addWidget(self.artist_photo, 0, Qt.AlignLeft)
        lay.addStretch(1)

        col.setWidget(body)
        col.setVisible(False)
        return col

    def set_artist(self, name, biography="", photo=None):
        self.artist_name.setText(name or "")
        self.artist_bio.setText(biography or "")
        self._artist_pixmap = photo
        self._lay_artist_photo()
        self._artist_col.setVisible(bool(name and biography))

    def _lay_artist_photo(self):
        """As wide as the column, rounded on all four corners.

        Not a fixed square: the column is the measure, and a portrait kept
        at its own proportions is a photograph rather than a crop.
        """
        photo = getattr(self, "_artist_pixmap", None)
        if photo is None or photo.isNull():
            self.artist_photo.setVisible(False)
            return
        width = max(180, self._artist_col.viewport().width() - 24)
        scaled = photo.scaledToWidth(width, Qt.SmoothTransformation)
        if scaled.height() > self.PHOTO_MAX:
            top = int((scaled.height() - self.PHOTO_MAX) * 0.2)
            scaled = scaled.copy(0, top, width, self.PHOTO_MAX)
        self.artist_photo.setPixmap(rounded(scaled, 14))
        self.artist_photo.setFixedSize(scaled.size())
        self.artist_photo.setVisible(True)

    # -- content ----------------------------------------------------------

    def set_track(self, song, artist, station, art: QPixmap = None):
        self.song.setText(song or "Nothing playing")
        self.artist.setText(artist or "")
        self.station.setText(station or "")
        if art and not art.isNull():
            self._art = art
            self._rebuild_art()

    def _add_line(self, widget):
        """Lines go above the credit, which stays last."""
        self._lay.insertWidget(max(0, self._lay.count() - 1), widget)

    def set_lyrics(self, lines, message=None):
        self.lyrics.set_lyrics(lines, message)

    def set_position(self, ms):
        """Pass the clock down to the lyrics.

        This was stubbed out back when nothing had a position to report
        and the line below was left stranded after the return — so the
        panel got timed lyrics and the full-screen view never did.
        """
        self.lyrics.set_position(ms)

    # -- painting ---------------------------------------------------------

    def _rebuild_art(self):
        """Rescale the cover, and blur the backdrop only if anyone can see
        it.

        The blur is the single most expensive thing a new song causes --
        several scaling passes at window size -- and it was being done on
        every identification whether or not this view was on screen. It
        now waits for the view to appear, which is the difference between
        a hitch on every song and none at all.
        """
        if self._art.isNull():
            return
        if not self.isVisible():
            # Nothing here is visible, so none of it is worth doing yet:
            # both the cover rescale and the blur wait for showEvent.
            self._backdrop_stale = True
            return
        side = self.art_label.width()
        self.art_label.setPixmap(rounded(
            self._art.scaled(side, side, Qt.KeepAspectRatioByExpanding,
                             Qt.SmoothTransformation), 12))
        self._rebuild_backdrop()
        self.update()

    def _rebuild_backdrop(self):
        # No blurred cover any more — the background is drawn live from
        # the accent. See _paint_aurora.
        self._backdrop = QPixmap()
        self._backdrop_stale = False

    # -- the moving background ----------------------------------------------
    #
    # A few soft pools of colour drifting past each other, in shades of the
    # accent. Drawn into a tiny image and stretched to fill the window:
    # stretching a 96-pixel picture across a screen is what blurs it, so
    # the softness costs nothing, and four gradients on a picture that size
    # take a fraction of a millisecond a frame.
    AURORA_W = 96
    AURORA_FPS = 30
    # How fast the pools drift. 1.0 is the original pace; lower is calmer.
    # Slowed at the user's request — the movement was nice but busy.
    AURORA_SPEED = 0.55

    def _aurora_timer(self):
        timer = getattr(self, "_aurora", None)
        if timer is None:
            from PyQt5.QtCore import QElapsedTimer, QTimer
            timer = QTimer(self)
            timer.setInterval(int(1000 / self.AURORA_FPS))
            timer.timeout.connect(self.update)
            self._aurora = timer
            self._aurora_clock = QElapsedTimer()
            self._aurora_clock.start()
        return timer

    def _paint_aurora(self, p):
        import math
        from PyQt5.QtGui import QImage, QRadialGradient

        w = self.AURORA_W
        h = max(1, int(w * self.height() / max(1, self.width())))
        t = self._aurora_clock.elapsed() / 1000.0 * self.AURORA_SPEED

        base = QColor(T["accent"])
        hue = base.hslHue() if base.hslHue() >= 0 else 30
        sat = max(90, base.hslSaturation())

        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(QColor.fromHsl(hue, min(255, sat), 18))
        q = QPainter(img)
        q.setRenderHint(QPainter.Antialiasing)
        q.setPen(Qt.NoPen)
        q.setCompositionMode(QPainter.CompositionMode_Screen)

        # Each pool: its own hue offset, speed and path, so they never line
        # up into a repeating pattern the eye can catch.
        pools = ((0, 0.11, 0.07, 0.62, 0.9),
                 (28, 0.07, 0.13, 0.55, 0.8),
                 (-26, 0.09, 0.05, 0.60, 0.75),
                 (12, 0.05, 0.09, 0.48, 0.7))
        for index, (shift, fx, fy, size, strength) in enumerate(pools):
            phase = index * 1.7
            cx = w * (0.5 + 0.38 * math.sin(t * fx * 2 * math.pi + phase))
            cy = h * (0.5 + 0.38 * math.cos(t * fy * 2 * math.pi + phase * 1.3))
            radius = w * size
            colour = QColor.fromHsl((hue + shift) % 360, min(255, sat),
                                    int(96 * strength))
            gradient = QRadialGradient(cx, cy, radius)
            gradient.setColorAt(0.0, colour)
            fade = QColor(colour)
            fade.setAlpha(0)
            gradient.setColorAt(1.0, fade)
            q.setBrush(gradient)
            q.drawEllipse(QPointF(cx, cy), radius, radius)
        q.end()

        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(self.rect(), img)

    def showEvent(self, e):
        super().showEvent(e)
        self._aurora_timer().start()

    def hideEvent(self, e):
        super().hideEvent(e)
        # Nothing is drawn while nobody can see it.
        timer = getattr(self, "_aurora", None)
        if timer is not None:
            timer.stop()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        side = max(self.ART_MIN, min(self.ART_MAX, int(e.size().width() * 0.32)))
        self.art_label.setFixedSize(side, side)
        self._rebuild_art()
        self._lay_artist_photo()

    def paintEvent(self, e):
        p = QPainter(self)
        self._aurora_timer()
        self._paint_aurora(p)
        # A lighter veil than the old cover needed.
        #
        # The overlay was set dark enough to quiet any album art, bright
        # ones included. This background is drawn dark to begin with, so
        # the same veil flattened it to near black and hid the movement.
        # Lyrics stay legible at this level whatever the accent is.
        p.fillRect(self.rect(), QColor(9, 9, 11, min(self._veil, 90)))
        p.end()
