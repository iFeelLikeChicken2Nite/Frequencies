"""Frequencies — scrolling that carries.

Qt scrolls in steps: three notches of the wheel moves three chunks, each
one instantly. It is precise and it feels like machinery. A trackpad on a
Mac feels like moving a sheet of paper — it keeps going when you let go,
and when it reaches the end it stretches slightly and springs back rather
than simply stopping dead.

This does both, for any scroll area:

  * a wheel notch adds velocity rather than jumping the position, and the
    view eases toward its target
  * scrolling past either end pulls the content a little further and
    releases it back, so the edge is something you feel rather than a wall

The overscroll is drawn by moving the viewport itself, because the scroll
bar cannot hold a value outside its own range.
"""

from PyQt5.QtCore import QEvent, QObject, QTimer, Qt


class SmoothScroll(QObject):
    """Install on a scroll area to give it inertia and a springy edge."""

    INTERVAL = 16               # ms between steps, about 60fps
    EASE = 0.22                 # how much of the remaining gap to close
    STEP = 110                  # px per wheel notch
    # None at all. A list that gives when you reach the end is a phone
    # gesture; on a desktop, where the wheel keeps turning after the
    # content has run out, it reads as the view coming loose.
    MAX_OVERSHOOT = 0           # px the content may be pulled past the end
    SPRING = 1.0
    STOP_AT = 0.6               # px; below this the animation is over

    def __init__(self, area, parent=None):
        super().__init__(parent or area)
        self.area = area
        self.bar = area.verticalScrollBar()
        self._target = float(self.bar.value())
        self._over = 0.0        # px past the end, positive either way
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL)
        self._timer.timeout.connect(self._step)
        area.viewport().installEventFilter(self)
        area.installEventFilter(self)

        # Dragging the bar is the user setting the position directly.
        #
        # Without this the animation kept easing toward whatever target the
        # last wheel notch left behind, so letting go of the handle snapped
        # the list straight back to where it had been.
        self.bar.sliderPressed.connect(self._grabbed)
        self.bar.sliderMoved.connect(self._dragged)
        self.bar.sliderReleased.connect(self._dragged)
        self.bar.rangeChanged.connect(lambda *_a: self._sync())

        # A view that wants to know when a person scrolled it (the lyrics
        # hold still while you read, task 35) has a scrolled_by_hand().
        # The bar's own signals can't say: this moves it with setValue.
        self._by_hand = getattr(area, "scrolled_by_hand", None)
        self._set_to = None         # the value this last put on the bar

    # -- input -------------------------------------------------------------

    # How far the keys move, in rows or screens.
    KEYS = {
        Qt.Key_PageDown: ("page", 1), Qt.Key_PageUp: ("page", -1),
        Qt.Key_Home: ("end", -1), Qt.Key_End: ("end", 1),
        Qt.Key_Down: ("line", 1), Qt.Key_Up: ("line", -1),
    }

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            return self._key(event)
        if event.type() != QEvent.Wheel:
            return False
        if event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier):
            return False        # zoom and horizontal scroll are not ours

        delta = event.angleDelta().y()
        if not delta:
            return False

        # A sideways list takes a vertical wheel sideways.
        #
        # This drove the vertical bar only, so on the curated strip —
        # which scrolls horizontally and has no vertical range — every
        # notch was swallowed and nothing moved.
        if self.bar.maximum() <= 0:
            across = getattr(self.area, "horizontalScrollBar", None)
            across = across() if across else None
            if across is not None and across.maximum() > 0:
                across.setValue(across.value()
                                - int(delta / 120.0 * self.STEP))
                return True
            return False

        self._taking_over()
        notches = delta / 120.0
        self._target -= notches * self.STEP

        low, high = self.bar.minimum(), self.bar.maximum()
        if self._target < low:
            # Past the top: the excess becomes stretch, not position.
            self._over = max(-self.MAX_OVERSHOOT,
                             self._over + (self._target - low) * 0.16)
            self._target = low
        elif self._target > high:
            self._over = min(self.MAX_OVERSHOOT,
                             self._over + (self._target - high) * 0.16)
            self._target = high

        self._run()
        return True

    def _key(self, event):
        """Page Up, Home and the rest, eased rather than jumped.

        Qt moves the scrollbar in one step for these, which in a view
        that otherwise glides is a jolt — and the animation then fought
        it, because its target was still wherever the wheel left it.
        """
        move = self.KEYS.get(event.key())
        if move is None or event.modifiers() & ~Qt.KeypadModifier:
            return False
        kind, direction = move
        self._taking_over()
        low, high = self.bar.minimum(), self.bar.maximum()

        if kind == "end":
            self._target = float(high if direction > 0 else low)
        elif kind == "page":
            self._target += direction * self.bar.pageStep()
        else:
            self._target += direction * self.bar.singleStep() * 3

        self._target = max(low, min(high, self._target))
        self._run()
        return True

    def _taking_over(self):
        """A wheel turn or a key: tell the view, and start from where the
        bar really is.

        The target used to be wherever this scroller last left it. When
        something else had moved the bar since (the lyrics gliding to the
        sung line), a wheel notch measured from that old spot, so the
        view leapt back to it instead of moving one notch (task 35).
        """
        if callable(self._by_hand):
            try:
                self._by_hand()
            except Exception:                   # noqa: BLE001
                pass
        if not self._timer.isActive():
            self._sync()

    def _run(self):
        """Start the animation, if it isn't running already."""
        if not self._timer.isActive():
            self._set_to = None
            self._timer.start()

    def _grabbed(self):
        """A hand on the handle stops the animation dead."""
        self._timer.stop()
        self._set_to = None
        self._over = 0.0
        self._last_shift = 0.0
        self._sync()

    def _dragged(self, *_args):
        self._sync()

    def _sync(self):
        self._target = float(self.bar.value())

    def sync_after_jump(self):
        """Accept a position set by something other than this scroller."""
        self._timer.stop()
        self._set_to = None
        self._over = 0.0
        self._last_shift = 0.0
        QTimer.singleShot(0, self._sync)

    # Quicker than a wheel flick, still eased: a jump is a request to be
    # somewhere else, not a request to watch the journey.
    JUMP_EASE = 0.34

    def glide_to(self, value):
        """Animate to a position rather than snapping to it."""
        low, high = self.bar.minimum(), self.bar.maximum()
        self._target = float(max(low, min(high, value)))
        self._over = 0.0
        self._ease = self.JUMP_EASE
        self._run()

    # -- animation ---------------------------------------------------------

    def _step(self):
        value = float(self.bar.value())
        if self._set_to is not None and int(value) != self._set_to:
            # Something else moved the bar since the last step (the
            # lyrics gliding to the sung line): it wins. Carrying on
            # meant two animations pulling the bar between them, and
            # whichever stopped last decided where the view ended up.
            self._timer.stop()
            self._set_to = None
            self._over = 0.0
            self._ease = None
            self._sync()
            return
        gap = self._target - value
        ease = getattr(self, "_ease", None) or self.EASE
        if abs(gap) > self.STOP_AT:
            self.bar.setValue(int(round(value + gap * ease)))
        else:
            self.bar.setValue(int(round(self._target)))
        self._set_to = self.bar.value()

        if abs(self._over) > 0.5:
            self._over *= (1.0 - self.SPRING)
        else:
            self._over = 0.0
        self._apply_overshoot()

        if abs(self._target - self.bar.value()) <= self.STOP_AT \
                and self._over == 0.0:
            self._timer.stop()
            self._set_to = None
            self._ease = None

    def _apply_overshoot(self):
        """Shift the viewport contents, since the bar cannot go past its
        own range."""
        widget = self.area.widget() if hasattr(self.area, "widget") else None
        if widget is None:
            return
        try:
            widget.move(widget.x(), widget.y() - int(self._shift()))
        except Exception:                       # noqa: BLE001
            pass

    def _shift(self):
        """How far the content is currently displaced, and by how much it
        changed since last frame."""
        previous = getattr(self, "_last_shift", 0.0)
        now = self._over
        self._last_shift = now
        return now - previous


def attach(*areas):
    """Give every one of these scroll areas the same behavior."""
    return [SmoothScroll(area) for area in areas if area is not None]
