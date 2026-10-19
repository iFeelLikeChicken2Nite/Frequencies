"""
classicwindow.py - the skinned player.

A frameless 275x116 window drawn entirely from a Winamp skin's
bitmaps. It is a second face on the same app: every control here does
exactly what its counterpart in the main window does, and the two stay
in step because both read the same player.

Two things are worth knowing about how this is drawn.

The window has no system frame, so it has to handle its own dragging -
that is what the title bar is for, and it is why `mousePressEvent`
records an offset.

Everything is drawn at the skin's own 275x116 and then scaled up as one
pixmap. Scaling each sprite as it is drawn would leave seams and blur
the 5x6 bitmap font into mush; scaling once at the end, with smoothing
off, keeps the hard pixel edges these skins were designed for.
"""
import os

from PyQt5.QtCore import Qt, QEvent, QPoint, QRect, QTimer, pyqtSignal
from PyQt5.QtGui import QPainter, QPixmap, QColor, QImage, QPolygonF
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QApplication

import winampskin


def spectrum_window():
    """How many samples the analyzer wants, without importing it early."""
    import spectrum
    return spectrum.WINDOW


def skin_icon():
    """The mark from the skin, as a window icon.

    Shared by the skinned stack and the visualizer so they read as the
    same application in a taskbar, rather than two unrelated windows
    that happen to be open together.
    """
    from PyQt5.QtGui import QIcon
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "freqamp.png")
    # Decoded now, under the image lock (task 54) - QIcon(path) would
    # decode at its first paint, outside it.
    import imagelock
    return imagelock.icon(path) if os.path.exists(path) else QIcon()


class ClassicDock(QWidget):
    """One window holding the player, equalizer and playlist.

    Winamp let you drag its windows into a stack and then move the lot
    by the top one. Reproducing that with three separate top-level
    windows and manual move() calls does not work: on Wayland - which
    Fedora's KDE session uses by default - a client is not permitted to
    position its own top-level windows at all, so every move() was
    silently ignored and nothing ever stuck together.

    So they are not top-level windows. They are child widgets stacked in
    this one, which means they move together because they are one
    window, and no positioning API is involved.

    The dock itself is dragged through startSystemMove(), which asks the
    compositor to move the window rather than telling it where to go.
    That is the only thing that works under Wayland, and it works under
    X11 too.
    """

    closed = pyqtSignal()

    def __init__(self, parent=None):
        # A child widget when docked, a window when not.
        #
        # Three separate top-level windows could never be dragged as a
        # unit: Wayland does not let a client position its own windows,
        # so every move() was ignored. Inside one dock they are simply
        # one window.
        flags = Qt.Widget if parent is not None else (
            Qt.Window | Qt.FramelessWindowHint
        )
        super().__init__(parent, flags)
        self.dock = None
        # What the taskbar shows for the skinned stack.
        self.setWindowTitle("Freqamp")
        self.setWindowIcon(skin_icon())
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._panels = []
        # Set while the stack's contents are being swapped out.
        self.rebuilding = False

    def set_skin(self, skin):
        """The skin's idle pointer for the dock itself (task 32).

        Panels of different widths leave gaps beside the narrower ones,
        and those gaps are the dock. Without this they showed the
        desktop's arrow in the middle of a skinned stack.
        """
        winampskin.set_skin_cursor(self, skin, "normal")

    def add_panel(self, widget):
        """Stack another skin window under the ones already here."""
        if widget in self._panels:
            return
        # Same width as the panels already here (task 97): a window
        # resized sideways while loose would otherwise widen the whole
        # dock and leave bare grey beside the narrower ones.
        if self._panels and hasattr(widget, "fit_width"):
            widget.fit_width(self._panels[0].width() // widget.scale)
        widget.dock = self
        self._panels.append(widget)
        # A panel coming back from being its own window still carries
        # its Qt.Window flags when addWidget reparents it, and the
        # layout's size hint does not count it until those are gone -
        # which normally happens a whole event-loop turn later. Clearing
        # them first makes the hint right immediately, so the refit
        # below sees the true height.
        if widget.isWindow():
            widget.setWindowFlags(Qt.Widget)
        self._reorder()
        widget.show()
        # refit, not just adjustSize: adjustSize does nothing to a
        # window that is already shown, so a panel re-docked into a
        # visible stack did not grow it - the stack stayed one panel
        # tall with two panels in it.
        self.refit()

    def undock(self, widget, at=None):  # noqa: C901
        """Float a panel back out into a window of its own.

        Dragging a stacked window away from the stack should take it
        with you - otherwise the stack is a trap you can get into and
        not out of. The panel becomes a top-level window again and the
        dock shrinks around what is left.
        """
        if widget not in self._panels:
            return False

        # Where it is on screen right now, before it leaves the layout.
        where = widget.mapToGlobal(widget.rect().topLeft())

        self._panels.remove(widget)
        self._layout.removeWidget(widget)
        widget.dock = None
        # Reparented to the dock, not to nothing.
        #
        # Qt.Tool only avoids a taskbar button while the window has an
        # owner: setParent(None) first drops that ownership, so each torn
        # off panel became a top-level window in its own right and the
        # taskbar grew an entry per panel. Passing the dock and the flags
        # together keeps it transient for the stack -- one entry however
        # the panels are arranged, and they stay above it rather than
        # falling behind.
        widget.setParent(self, Qt.Tool | Qt.FramelessWindowHint)

        # Re-assert the size before showing it.
        #
        # Coming out of a layout, the widget's own geometry is whatever
        # the layout last gave it, and setWindowFlags resets the window
        # handle underneath. Without this the new window came up at
        # whatever size it happened to hold - sometimes nothing at all,
        # which is a taskbar entry for a window you cannot see.
        if hasattr(widget, "skin_width"):
            widget.setFixedSize(widget.skin_width * widget.scale,
                                widget.skin_height * widget.scale)
        widget.resize(widget.sizeHint())

        # Ask for the spot the panel is already occupying.
        #
        # Not the cursor: a panel that leaps to the pointer looks like
        # it snapped somewhere, and the pointer is over the title bar
        # anyway. Coming out exactly where it already was means the only
        # visible change is that it is now a separate window.
        #
        # This is a request, not a guarantee. Under Wayland a client may
        # not place its own top-level windows - the compositor decides,
        # and KWin will put it where its placement policy says. There is
        # no client-side way around that; on X11 it lands exactly here.
        target = where if where is not None else at
        if target is not None:
            widget.move(max(0, target.x()), max(0, target.y()))
        widget.show()
        widget.raise_()
        widget.activateWindow()

        # If the button is still down, hand the compositor the drag so
        # the tear-off continues as one motion. Deferred a turn so the
        # new window is actually mapped first - asking the compositor to
        # move a surface it has not shown yet is refused.
        def continue_drag():
            handle = widget.windowHandle()
            if handle is not None and hasattr(handle, "startSystemMove"):
                if QApplication.mouseButtons() & Qt.LeftButton:
                    handle.startSystemMove()
        QTimer.singleShot(0, continue_drag)

        # And once more, later: if the compositor dropped the surface
        # during the handover - which cannot be reproduced without a
        # real seat, so it cannot be ruled out either - re-showing is
        # harmless when the window survived and decisive when it did
        # not.
        def reassert():
            if widget.dock is None and not widget.isVisible():
                widget.show()
                widget.raise_()
        QTimer.singleShot(120, reassert)

        # Shrink to what is left, and stay shrunk - see refit.
        self.refit()
        if not self._panels:
            self.close()
        return True

    def remove_panel(self, widget, unparent=True):
        """Take a panel out of the stack.

        unparent=False keeps it a (hidden) child of the dock: a skin
        change retires the old panels that way, so they are never a
        parentless widget that Python deletes the moment its last
        reference goes (task 45).
        """
        if widget not in self._panels:
            return
        self._panels.remove(widget)
        self._layout.removeWidget(widget)
        if unparent:
            widget.setParent(None)
        widget.dock = None
        self.refit()
        # Empty because it is being rebuilt is not empty because the
        # user closed everything. Closing here would destroy the window
        # the rebuild exists to preserve.
        if not self._panels and not self.rebuilding:
            self.close()

    def wants(self, widget, at):
        """Is this loose panel close enough to snap back on?

        Winamp re-docked when you dropped a window against the stack,
        and having to find a menu to undo an accidental tear-off would
        be a poor trade for the drag working. A generous margin, since
        the compositor has been moving the window and we only find out
        where it ended up.
        """
        if not self._panels:
            return False
        edge = self.frameGeometry()
        margin = 40
        return edge.adjusted(-margin, -margin, margin, margin).contains(at)

    # Where each panel belongs, top to bottom. Winamp stacked in a fixed
    # order and so does this: which window happened to be opened first
    # should not decide where it sits.
    PANEL_ORDER = ("ClassicWindow", "EqualizerWindow", "TagWindow",
                   "PlaylistWindow")

    def _reorder(self):
        """Put the panels back in their canonical order.

        Called on every add. The layout keeps insertion order otherwise,
        so opening the tag window before the equalizer - which happens
        whenever one was already showing and the other was not - left
        them stacked the wrong way round.
        """
        def rank(widget):
            name = type(widget).__name__
            return (self.PANEL_ORDER.index(name)
                    if name in self.PANEL_ORDER else len(self.PANEL_ORDER))

        self._panels.sort(key=rank)
        for index, panel in enumerate(self._panels):
            self._layout.insertWidget(index, panel, alignment=Qt.AlignTop)

    def refit(self):
        # Frozen while the contents are being swapped.
        #
        # A rebuild empties the stack and refills it. Resizing along the
        # way collapses the window to nothing and grows it again, and a
        # compositor that sees a window shrink to a few pixels is
        # entitled to re-place it when it comes back - which is the jump
        # on every skin change. Holding the size until the panels are
        # all back means the window never visibly changes at all.
        if self.rebuilding:
            return

        """Shrink or grow to exactly the panels inside.

        Called whenever a panel changes size - shading one, or resizing
        the playlist. Qt does not do this for us: the panels have fixed
        sizes, so the layout's hint changes but a window that has been
        shown keeps whatever size it was given. That left a shaded
        equalizer sitting above a slab of empty grey.

        activate() first: a widget added to the layout is only picked
        up by sizeHint after the layout has processed the change, which
        normally waits for the event loop. Re-docking then refitting in
        the same breath read the OLD hint and left the stack one panel
        short.
        """
        self._layout.activate()
        self.adjustSize()
        self.resize(self.sizeHint())

    def changeEvent(self, event):
        """Gaining or losing focus relights every title bar at once.

        The panels also hear about it themselves; this is the backstop,
        because a stack with some title bars lit and some dim is exactly
        what it looked like when each panel was left to notice alone.
        """
        if event.type() == QEvent.ActivationChange:
            for panel in self._panels:
                panel.update()
        super().changeEvent(event)

    def begin_move(self):
        """Hand the drag to the window manager.

        Called by whichever panel's title bar was pressed. Returns True
        when the compositor took it, so the caller knows not to fall
        back to moving anything itself.
        """
        handle = self.windowHandle()
        if handle is not None and hasattr(handle, "startSystemMove"):
            return bool(handle.startSystemMove())
        return False

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)


class SnapGroup:
    """Windows that have been dragged together and now move together.

    Winamp's windows snapped to each other's edges and, once snapped,
    the main window towed the others around. Reproducing that is
    mostly bookkeeping: on every drag, work out which windows are
    touching the one being moved and shift them by the same delta.

    Membership is recomputed from geometry rather than remembered, so
    dragging a window away simply leaves the group - there is no
    "detach" to find.
    """

    THRESHOLD = 12          # pixels; how close counts as touching

    def __init__(self):
        self.windows = []

    def add(self, window):
        if window not in self.windows:
            self.windows.append(window)
        window.snap_group = self

    def remove(self, window):
        if window in self.windows:
            self.windows.remove(window)

    def attached_to(self, window):
        """Everything transitively touching `window`."""
        alive = [w for w in self.windows if w is not window and w.isVisible()]
        group = []
        frontier = [window]
        while frontier:
            current = frontier.pop()
            for other in list(alive):
                if self._touching(current, other):
                    alive.remove(other)
                    group.append(other)
                    frontier.append(other)
        return group

    @classmethod
    def _touching(cls, a, b):
        ra, rb = a.frameGeometry(), b.frameGeometry()
        near = cls.THRESHOLD
        # Overlapping on one axis and within the threshold on the other.
        horizontal = (ra.left() < rb.right() + near
                      and rb.left() < ra.right() + near)
        vertical = (ra.top() < rb.bottom() + near
                    and rb.top() < ra.bottom() + near)
        edge_y = (abs(ra.bottom() - rb.top()) <= near
                  or abs(rb.bottom() - ra.top()) <= near)
        edge_x = (abs(ra.right() - rb.left()) <= near
                  or abs(rb.right() - ra.left()) <= near)
        return (horizontal and edge_y) or (vertical and edge_x)

    def snap(self, window):
        """Nudge `window` onto the nearest edge it is close to."""
        rect = window.frameGeometry()
        near = self.THRESHOLD
        for other in self.windows:
            if other is window or not other.isVisible():
                continue
            target = other.frameGeometry()
            dx = dy = 0
            if abs(rect.top() - target.bottom()) <= near:
                dy = target.bottom() - rect.top() + 1
            elif abs(rect.bottom() - target.top()) <= near:
                dy = target.top() - rect.bottom() - 1
            if abs(rect.left() - target.left()) <= near:
                dx = target.left() - rect.left()
            elif abs(rect.left() - target.right()) <= near:
                dx = target.right() - rect.left() + 1
            if dx or dy:
                window.move(rect.left() + dx, rect.top() + dy)
                return True
        return False


class ClassicWindow(QWidget):
    """A Winamp-skinned player window."""

    closed = pyqtSignal()

    def __init__(self, skin, parent=None, scale=2):
        # A child widget when docked, a window when not.
        #
        # Three separate top-level windows could never be dragged as a
        # unit: Wayland does not let a client position its own windows,
        # so every move() was ignored. Inside one dock they are simply
        # one window.
        flags = Qt.Widget if parent is not None else (
            Qt.Window | Qt.FramelessWindowHint
        )
        super().__init__(parent, flags)
        self.dock = None
        self.skin = skin
        self.scale = max(1, int(scale))
        self.setFixedSize(
            winampskin.MAIN_WIDTH * self.scale,
            winampskin.MAIN_HEIGHT * self.scale,
        )
        # Needed for the logo's hover state: without it we only hear
        # about the mouse while a button is held.
        self.setMouseTracking(True)
        # Wear the skin's pointer from the start, not only after the
        # mouse has moved inside (task 32): a player built, or rebuilt by
        # a skin change, under a still pointer showed the desktop's arrow.
        winampskin.set_skin_cursor(self, skin, "normal")
        # What the taskbar shows for the skinned stack.
        self.setWindowTitle("Freqamp")
        self.setWindowIcon(skin_icon())

        # What the window is showing.
        self.title_text = "Frequencies"
        self.state = "stop"          # play | pause | stop | connecting
        self.twenty_four_hour = False
        self.bitrate_text = ""
        self.samplerate_text = ""
        self.playing = False
        self.shuffle = False
        self.repeat = False
        # The repeat button spins once when pressed. It is decoration
        # rather than state - there is nothing to repeat on live radio -
        # but a button that does nothing at all feels broken.
        self._repeat_spin = 0.0
        self._repeat_timer = QTimer(self)
        self._repeat_timer.setInterval(16)
        self._repeat_timer.timeout.connect(self._advance_repeat_spin)
        self.volume = 70
        # Balance: 0 hard left, 0.5 centred, 1 hard right.
        #
        # Tethered to the middle. Stereo balance on a radio is something
        # you nudge and then want back where it was, and a slider that
        # stays where you left it means a permanently lopsided mix
        # nobody notices until they wonder why one speaker is quiet.
        self.balance = 0.5
        self._balance_anim = QTimer(self)
        self._balance_anim.setInterval(16)
        self._balance_anim.timeout.connect(self._recentre_balance)
        self._pressed = None
        self._drag_offset = None
        # Set by the main window once all three exist.
        self.snap_group = None
        self._drag_followers = ()
        self._drag_origin = None
        self._scroll = 0
        self._title_drag_from = None
        self._title_drag_start = 0
        # The logo does nothing but acknowledge you.
        self._logo_hot = False
        self._logo_pressed = False
        # Which clutterbar buttons are latched. Only two of them are
        # states; the others just do something when pressed.
        # Only D and V are states. A opens a link, so latching its lamp
        # would leave it lit for something that already happened.
        self.clutter_state = {"d": False, "v": False}
        self._clutter_hover = None
        self._logo_mask_cache = None
        self._title_hover = None
        self._repeat_cache = None
        self.favourite = False
        self._star_hot = False
        self._star_pressed = False
        self._star_glow = False
        self._star_timer = QTimer(self)
        self._star_timer.setSingleShot(True)
        self._star_timer.timeout.connect(self._end_star_glow)
        # Our own skin gets a solid star; others get a fainter one.
        self.skin_is_base = "frequencies" in (
            getattr(self.skin, "name", "") or ""
        ).lower()
        self._cursor_name = None
        # bars | wave | none - clicking the panel cycles them.
        self.vis_mode = "bars"
        self._vis_samples = None
        self._vis_peaks = [0.0] * winampskin.VIS_BARS
        # The little dashes that hang above each bar and sink back.
        self._vis_caps = [0.0] * winampskin.VIS_BARS
        self.vis_gain = 1.0

        # Windowshade: rolled up to a single 14-pixel strip. Not just
        # the title bar cropped - the skin supplies a different
        # background for it, with a slot for the time.
        self.shaded = False

        # The dial. Winamp's position bar has nothing to seek within on
        # a live stream, so it tunes instead: drag along it and you scan
        # the station list in its current order, as if turning the knob
        # on a receiver.
        self.dial_stations = []      # display names, in list order
        self.dial_index = -1
        self._dial_dragging = False
        self._dial_target = -1
        self._dial_settle = QTimer(self)
        self._dial_settle.setSingleShot(True)
        self._dial_settle.setInterval(650)
        self._dial_settle.timeout.connect(self._settle_dial)

        # Callbacks, set by the main window.
        self.on_play = None
        self.on_pause = None
        self.on_stop = None
        self.on_next = None
        self.on_previous = None
        self.on_eject = None
        self.on_shuffle = None
        self.on_volume = None
        self.on_tune = None          # called with an index, once settled
        self.on_scan = None          # called while scanning, for static
        self.on_balance = None
        self.on_menu = None
        self.on_clutter = None
        self.on_clock_mode = None
        self.on_vis_mode = None
        self.on_close_all = None
        self.on_favourite = None
        self.on_favourite_menu = None
        self.on_history = None
        self.on_logo = None

        # The title scrolls, as it always did.
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(220)
        self._scroll_timer.timeout.connect(self._advance_scroll)
        self._scroll_timer.start()

        # The clock only changes once a minute, but a second is cheap
        # and keeps it honest across a minute boundary.
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self.update)
        self._clock_timer.start()

    # -- state --------------------------------------------------------

    def set_title(self, text):
        if text != self.title_text:
            self.title_text = text or "Frequencies"
            self._scroll = 0
            self.update()

    def set_stream_info(self, bitrate, samplerate):
        """Fill the kbps and kHz boxes from what is actually playing."""
        self.bitrate_text = str(int(bitrate)) if bitrate else ""
        self.samplerate_text = str(int(samplerate)) if samplerate else ""
        self.update()

    def set_playing(self, playing):
        self.playing = bool(playing)
        self.update()

    def set_state(self, state):
        """play, stop, or connecting - what the little glyph shows."""
        self.state = state
        self.update()

    def set_shuffle(self, on):
        self.shuffle = bool(on)
        self.update()

    def set_volume(self, value):
        self.volume = max(0, min(100, int(value)))
        self.update()

    def _advance_scroll(self):
        if len(self.title_text) > winampskin.TITLE_CHARS:
            self._scroll = (self._scroll + 1) % (len(self.title_text) + 4)
            self.update()

    # -- painting -----------------------------------------------------

    def _refit_dock(self):
        if self.dock is not None:
            self.dock.refit()

    def _is_active(self):
        """Ask the window we live in - see classicextras for why."""
        return self.window().isActiveWindow()

    def event(self, event):
        # Repaint on focus change, so the title bar goes lit or dim with
        # the rest of the stack rather than on the clock's next tick.
        if event.type() in (QEvent.WindowActivate, QEvent.WindowDeactivate):
            self.update()
        return super().event(event)

    def toggle_shade(self):
        self.shaded = not self.shaded
        self.setFixedSize(
            winampskin.MAIN_WIDTH * self.scale,
            (winampskin.SHADE_HEIGHT if self.shaded
             else winampskin.MAIN_HEIGHT) * self.scale,
        )
        self._refit_dock()
        self.update()

    def _paint_shade(self):
        canvas = QPixmap(winampskin.MAIN_WIDTH, winampskin.SHADE_HEIGHT)
        canvas.fill(QColor("#000000"))
        painter = QPainter(canvas)

        background = self.skin.shade("main_background")
        if background:
            painter.drawPixmap(0, 0, background)
        else:
            # A skin without a shade background still collapses; its
            # title bar is a reasonable stand-in.
            titlebar = self.skin.titlebar(self._is_active())
            if titlebar:
                painter.drawPixmap(0, 0, titlebar)

        button = self.skin.shade("main_button_selected")
        if button:
            painter.drawPixmap(*winampskin.SHADE_BUTTON_POS, button)

        # A shorter title, inline. No scrolling: at this size the text
        # moving would be more distracting than useful.
        x, y = winampskin.SHADE_TITLE_POS
        for index, character in enumerate(
            self.title_text[:winampskin.SHADE_TITLE_CHARS]
        ):
            glyph = self.skin.char(character)
            if glyph:
                painter.drawPixmap(
                    x + index * winampskin.CHAR_WIDTH, y, glyph
                )

        painter.end()
        self._blit_scaled(canvas)

    def _blit_scaled(self, canvas):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.drawPixmap(self.rect(), canvas)
        painter.end()

    def paintEvent(self, _event):
        if self.shaded:
            self._paint_shade()
            return
        canvas = QPixmap(winampskin.MAIN_WIDTH, winampskin.MAIN_HEIGHT)
        canvas.fill(QColor("#000000"))

        painter = QPainter(canvas)
        background = self.skin.background()
        if background:
            painter.drawPixmap(0, 0, background)

        titlebar = self.skin.titlebar(self._is_active())
        if titlebar:
            painter.drawPixmap(0, 0, titlebar)

        self._draw_title(painter)

        for name in winampskin.BUTTONS:
            sprite = self.skin.button(name, pressed=(self._pressed == name))
            rect = self.skin.button_rect(name)
            if sprite and rect:
                painter.drawPixmap(rect.x(), rect.y(), sprite)

        # Shuffle reflects the app; repeat is drawn in its off state
        # rather than skipped, because a skin's background has a hole
        # where the button belongs and leaving it empty looks broken.
        for name, on in (("shuffle", self.shuffle), ("repeat", self.repeat)):
            sprite = self.skin.toggle(name, on)
            rect = self.skin.toggle_rect(name)
            if not (sprite and rect):
                continue
            if name == "repeat" and self._repeat_spin:
                self._draw_spinning_repeat(painter, rect, sprite)
            else:
                painter.drawPixmap(rect.x(), rect.y(), sprite)

        self._draw_star(painter)
        self._draw_title_hover(painter)
        self._draw_visualizer(painter)
        self._draw_play_state(painter)
        self._draw_clutterbar(painter)
        self._draw_readouts(painter)
        self._draw_logo_feedback(painter)
        self._draw_volume(painter)
        self._draw_balance(painter)
        self._draw_dial(painter)
        self._draw_clock(painter)
        painter.end()

        # One scale at the end, with smoothing off: these are pixel-art
        # bitmaps and a 5x6 font, and any interpolation turns both to
        # porridge.
        self._blit_scaled(canvas)

    def _title_rect(self):
        x, y = winampskin.TITLE_POS
        return QRect(x, y - 2, winampskin.TITLE_CHARS * winampskin.CHAR_WIDTH,
                     winampskin.CHAR_HEIGHT + 4)

    def _logo_rect(self):
        return QRect(*winampskin.LOGO_RECT)

    def _logo_mask(self):
        """Which pixels of the mark are actually the mark.

        A rectangular highlight lit the empty panel around it as well,
        which looked like a grey box appearing rather than a logo
        responding. This finds the pixels that differ from the flat
        panel color and lights only those. Computed once and kept,
        since the artwork does not change.
        """
        if self._logo_mask_cache is not None:
            return self._logo_mask_cache

        background = self.skin.background()
        rect = self._logo_rect()
        mask = []
        if background is not None:
            image = background.toImage()
            # The panel colour, sampled from just left of the mark.
            panel = QColor(image.pixel(max(0, rect.x() - 6),
                                       rect.y() + rect.height() // 2))
            for dy in range(rect.height()):
                for dx in range(rect.width()):
                    x, y = rect.x() + dx, rect.y() + dy
                    if not (0 <= x < image.width() and 0 <= y < image.height()):
                        continue
                    pixel = QColor(image.pixel(x, y))
                    if (abs(pixel.red() - panel.red())
                            + abs(pixel.green() - panel.green())
                            + abs(pixel.blue() - panel.blue())) > 40:
                        mask.append((x, y))
        self._logo_mask_cache = mask
        return mask

    def _logo_hit(self, point):
        """Anywhere on the mark's box counts as being on the mark.

        Pixel-exact hit testing meant hunting for the two-pixel-wide
        arcs to make it light up. The box is the hitbox; the mask only
        decides which pixels get brightened, so the feedback still
        follows the artwork rather than lighting a rectangle.
        """
        return self._logo_rect().contains(point)

    def _draw_logo_feedback(self, painter):
        """Light the mark under the cursor, dim it while pressed.

        It is not a button and does nothing - but a thing that looks
        like one should at least admit it noticed the click.
        """
        if not (self._logo_hot or self._logo_pressed):
            return
        painter.setOpacity(0.45 if self._logo_pressed else 0.25)
        painter.setPen(QColor(255, 255, 255))
        for x, y in self._logo_mask():
            painter.drawPoint(x, y)
        painter.setOpacity(1.0)

    def set_audio(self, samples):
        """The latest PCM, from the same tap the visualizer uses.

        Kept as a rolling buffer rather than just the newest chunk. A
        single read can be shorter than the analysis window, and one
        short chunk on its own would leave most of the window empty -
        so chunks are joined and the last window's worth is what gets
        analysed.
        """
        if not samples:
            return
        buffered = (self._vis_samples or []) + list(samples)
        keep = spectrum_window() * 2
        self._vis_samples = buffered[-keep:]
        if self.vis_mode != "none" and not self.shaded:
            self.update()

    def _vis_rect(self):
        return QRect(*winampskin.VIS_RECT)

    def _cycle_vis(self):
        order = ("bars", "wave", "none")
        self.vis_mode = order[(order.index(self.vis_mode) + 1) % len(order)]
        self.update()
        return self.vis_mode

    def _draw_visualizer(self, painter):
        """Bars or a waveform in the panel beside the clock.

        Drawn in the skin's own readout color so it belongs to whatever
        skin is loaded, and falling back gently rather than snapping to
        zero - a meter that drops instantly reads as broken.
        """
        if self.vis_mode == "none" or self.shaded:
            return
        rect = self._vis_rect()
        colour = QColor(self.skin.color("normal", "#00FF00"))

        # Clipped to the panel. Belt and braces alongside the measured
        # rect: a rounding error in a bar height or a waveform point
        # should never be able to draw on the border again.
        painter.save()
        painter.setClipRect(rect)

        if self.vis_mode == "bars":
            import spectrum
            heights = spectrum.bands(self._vis_samples or [],
                                     winampskin.VIS_BARS, self.vis_gain)
            # Decay, so bars fall back instead of flickering.
            self._vis_peaks = [
                max(new, old * 0.75)
                for new, old in zip(heights, self._vis_peaks)
            ]
            # Caps: hold the highest point each bar has reached, then
            # sink slowly. They fall more slowly than the bars so they
            # hang in the air for a moment, which is what makes the
            # movement readable rather than a flicker.
            self._vis_caps = [
                max(bar, cap - 0.02)
                for bar, cap in zip(self._vis_peaks, self._vis_caps)
            ]

            width = max(1, rect.width() // winampskin.VIS_BARS)
            # White caps, so the falling dashes read as separate from
            # the bars they sit above rather than as their tips.
            cap_colour = QColor(255, 255, 255)
            for index, value in enumerate(self._vis_peaks):
                x = rect.x() + index * width
                bar_width = max(1, width - 1)

                height = int(value * rect.height())
                if height > 0:
                    painter.fillRect(
                        QRect(x, rect.bottom() - height + 1,
                              bar_width, height),
                        colour,
                    )

                cap = self._vis_caps[index]
                if cap > 0.02:
                    cap_y = rect.bottom() - int(cap * rect.height())
                    cap_y = max(rect.y(), min(rect.bottom(), cap_y))
                    # Full width, one pixel tall. Narrowing them was
                    # the wrong axis: a dash should span its bar and be
                    # thin from top to bottom, which is what makes it
                    # read as a mark hanging above rather than a chip
                    # off the bar.
                    painter.fillRect(QRect(x, cap_y, bar_width, 1),
                                     cap_colour)
            painter.restore()
            return

        import spectrum
        points = spectrum.waveform(self._vis_samples or [], rect.width(),
                                   self.vis_gain)
        middle = rect.y() + rect.height() // 2
        painter.setPen(colour)
        previous = None
        for index, value in enumerate(points):
            y = middle - int(value * (rect.height() // 2 - 1))
            y = max(rect.y(), min(rect.bottom(), y))
            x = rect.x() + index
            if previous is not None:
                painter.drawLine(x - 1, previous, x, y)
            previous = y
        painter.restore()

    def _draw_play_state(self, painter):
        """The play / stop glyph beside the visualizer.

        While connecting it shows an ellipsis instead: the stream has
        not started, and claiming it is playing would be a lie told for
        several seconds at a time.
        """
        if self.state == "connecting":
            x, y = winampskin.PLAYSTATE_POS
            for index, character in enumerate("..."):
                glyph = self.skin.char(character)
                if glyph:
                    painter.drawPixmap(
                        x + index * winampskin.CHAR_WIDTH, y + 2, glyph
                    )
            return

        sprite = self.skin.play_state(self.state)
        if sprite:
            painter.drawPixmap(*winampskin.PLAYSTATE_POS, sprite)

    def _draw_clutterbar(self, painter):
        """O A I D V down the left of the visualizer.

        Drawn from titlebar.bmp - it is a sprite, not part of the
        background, which is why nothing appeared here before. The
        hovered entry gets the skin's own lit variant rather than a
        white wash of ours.
        """
        x, y = winampskin.CLUTTER_POS

        base = self.skin.clutterbar()
        if base is None:
            return
        painter.drawPixmap(x, y, base)

        # Hover: the skin carries a lit strip per entry.
        for index, (name, top, bottom, _ind) in enumerate(
            winampskin.CLUTTER_BUTTONS
        ):
            if self._clutter_hover == name:
                lit = self.skin.clutter_hover(index)
                if lit is not None:
                    painter.drawPixmap(x, y, lit)
                break

        # Latched states get their own little marks.
        for name, _top, _bottom, indicator in winampskin.CLUTTER_BUTTONS:
            if indicator is None or not self.clutter_state.get(name):
                continue
            sprite = self.skin.clutter_indicator(indicator)
            if sprite is not None:
                painter.drawPixmap(x, y + indicator[4], sprite)

    def _clutter_rects(self):
        x, y = winampskin.CLUTTER_POS
        width = winampskin.CLUTTER_SIZE[0]
        return {
            name: QRect(x, y + top, width, bottom - top)
            for name, top, bottom, _ind in winampskin.CLUTTER_BUTTONS
        }

    def _clock_rect(self):
        """The time display, which toggles 12/24 hour when clicked."""
        x, y = winampskin.TIME_POS
        return QRect(x - 3, y - 3, winampskin.SECONDS_GAPS[-1] + 12,
                     winampskin.DIGIT_HEIGHT + 6)

    def _draw_readouts(self, painter):
        """The kbps and kHz figures, from the stream itself."""
        for text, pos in ((self.bitrate_text, winampskin.KBPS_POS),
                          (self.samplerate_text, winampskin.KHZ_POS)):
            if not text:
                continue
            x, y = pos
            for index, character in enumerate(text[:3]):
                glyph = self.skin.char(character)
                if glyph:
                    painter.drawPixmap(
                        x + index * winampskin.CHAR_WIDTH, y, glyph
                    )

    def _draw_title(self, painter):
        """The song title, in the skin's own bitmap font."""
        text = self.title_text
        if len(text) > winampskin.TITLE_CHARS:
            padded = text + "   -   "
            start = self._scroll % len(padded)
            text = (padded + padded)[start:start + winampskin.TITLE_CHARS]

        x, y = winampskin.TITLE_POS
        for index, character in enumerate(text[:winampskin.TITLE_CHARS]):
            glyph = self.skin.char(character)
            if glyph:
                painter.drawPixmap(
                    x + index * winampskin.CHAR_WIDTH, y, glyph
                )

    def wheelEvent(self, event):
        """Wheel over the volume, balance or the dial."""
        point = self._skin_point(event)
        up = event.angleDelta().y() > 0

        if self._volume_rect().contains(point):
            self.set_volume(max(0, min(100, self.volume + (5 if up else -5))))
            if self.on_volume:
                self.on_volume(self.volume)
            event.accept()
            return

        if self._balance_rect().contains(point):
            self._balance_anim.stop()
            self.balance = max(0.0, min(1.0,
                                        self.balance + (0.05 if up else -0.05)))
            if self.on_balance:
                self.on_balance(self.balance)
            self._balance_anim.start()      # springs back, as a drag does
            self.update()
            event.accept()
            return

        if self._dial_rect().contains(point) and self.dial_stations:
            # One station per click of the wheel.
            index = max(0, min(len(self.dial_stations) - 1,
                               (self.dial_index if self.dial_index >= 0 else 0)
                               + (-1 if up else 1)))
            self.dial_index = index
            self._dial_target = index
            if self.on_tune:
                self.on_tune(index)
            self.update()
            event.accept()
            return

        super().wheelEvent(event)

    def _advance_repeat_spin(self):
        self._repeat_spin += 18
        if self._repeat_spin >= 360:
            self._repeat_spin = 0.0
            self._repeat_timer.stop()
        self.update()

    def _draw_balance(self, painter):
        x, y = winampskin.BALANCE_POS
        track = self.skin.balance_track(self.balance)
        if track:
            painter.drawPixmap(x, y, track)
        knob = self.skin.balance_knob(self._pressed == "balance")
        if knob:
            travel = winampskin.BALANCE_TRACK_SIZE[0] - knob.width()
            painter.drawPixmap(x + int(travel * self.balance), y + 1, knob)

    def _balance_rect(self):
        x, y = winampskin.BALANCE_POS
        w, h = winampskin.BALANCE_TRACK_SIZE
        return QRect(x, y, w, h)

    def _set_balance_from(self, point):
        rect = self._balance_rect()
        fraction = (point.x() - rect.x()) / max(1, rect.width())
        self.balance = max(0.0, min(1.0, fraction))
        if self.on_balance:
            self.on_balance(self.balance)
        self.update()

    def _recentre_balance(self):
        """Spring the knob back to the middle.

        Eased rather than snapped: a hard jump looks like a bug, while a
        quick glide reads as the control being sprung. Stops itself once
        it is close enough that another step would not be visible.
        """
        gap = 0.5 - self.balance
        if abs(gap) < 0.004:
            self.balance = 0.5
            self._balance_anim.stop()
        else:
            self.balance += gap * 0.25
        if self.on_balance:
            self.on_balance(self.balance)
        self.update()

    def _draw_dial(self, painter):
        """The tuning dial, where the position bar used to be."""
        x, y = winampskin.POSBAR_POS
        track = self.skin.posbar()
        if track:
            painter.drawPixmap(x, y, track)

        if not self.dial_stations:
            return

        knob = self.skin.posbar_knob(pressed=self._dial_dragging)
        if knob is None:
            return

        index = self._dial_target if self._dial_dragging else self.dial_index
        span = max(1, len(self.dial_stations) - 1)
        fraction = max(0, min(span, index)) / span if index >= 0 else 0.0
        travel = winampskin.POSBAR_SIZE[0] - knob.width()
        painter.drawPixmap(x + int(travel * fraction), y, knob)

    def _dial_rect(self):
        x, y = winampskin.POSBAR_POS
        width, height = winampskin.POSBAR_SIZE
        return QRect(x, y, width, height)

    def _dial_index_at(self, point):
        rect = self._dial_rect()
        if not self.dial_stations:
            return -1
        fraction = (point.x() - rect.x()) / max(1, rect.width())
        fraction = max(0.0, min(1.0, fraction))
        return int(round(fraction * (len(self.dial_stations) - 1)))

    def _scan_to(self, index):
        """Move the dial without committing to a station.

        Scanning plays static and shows the name it is passing, the same
        as sweeping past a station on a real receiver. Nothing is tuned
        until the dial stops - either because the mouse was released or
        because it has been held still.
        """
        if index == self._dial_target:
            return
        self._dial_target = index
        if 0 <= index < len(self.dial_stations):
            self.set_title(self.dial_stations[index])
        if self.on_scan:
            self.on_scan()
        self._dial_settle.start()
        self.update()

    def _settle_dial(self):
        """Held still long enough - tune to whatever the dial is on."""
        if self._dial_target < 0:
            return
        self.dial_index = self._dial_target
        if self.on_tune:
            self.on_tune(self._dial_target)

    def set_dial(self, names, index):
        """The stations the dial scans, in the order the list shows."""
        self.dial_stations = list(names)
        if not self._dial_dragging:
            self.dial_index = index
            self._dial_target = index
        self.update()

    def _draw_clock(self, painter):
        """The current time, where the elapsed time used to go.

        A live stream has no meaningful elapsed time - it started before
        you tuned in - so that display was either blank or counting
        something nobody cares about. The clock is the number you
        actually want while a radio is on.
        """
        import time as _time
        now = _time.localtime()
        hour = now.tm_hour
        if not self.twenty_four_hour:
            # 12-hour by default, and midnight reads as 12 rather than
            # 00 - which is what a clock face would show.
            hour = hour % 12 or 12
        digits = [hour // 10, hour % 10,
                  now.tm_min // 10, now.tm_min % 10]

        x, y = winampskin.TIME_POS

        # Cover the colon painted into the background.
        #
        # With a flat fill, which is what this used to do. The panel is
        # not flat - it is a grid of dots - so the fill left a blank
        # rectangle wherever the digits did not cover it. Copying a
        # patch of the panel's own dotted background instead keeps the
        # grid unbroken, and works on any skin because the patch comes
        # from that skin.
        background = self.skin.background()
        if background is not None:
            cx, cy, cw, ch = winampskin.PAINTED_COLON
            sx, sy = winampskin.DOT_SOURCE
            painter.drawPixmap(cx, cy, background, sx, sy, cw, ch)

        for value, gap in zip(digits, winampskin.TIME_GAPS):
            sprite = self.skin.digit(value)
            if sprite is None:
                return          # no numbers.bmp: draw nothing at all
            painter.drawPixmap(x + gap, y, sprite)

        # Seconds, same size as the rest, on the same line.
        #
        # They were two-thirds height and tucked underneath, which read
        # as a subtitle rather than part of the time. Shifting the whole
        # clock left made room for them at full size without running
        # into the play-state glyph on one side or the display on the
        # other.
        for gap, value in zip(winampskin.SECONDS_GAPS,
                              (now.tm_sec // 10, now.tm_sec % 10)):
            sprite = self.skin.digit(value)
            if sprite is None:
                return
            painter.drawPixmap(x + gap, y, sprite)

        # Two colons, matching the digits' own colour.
        colour = QColor(self.skin.color("normal", "#00FF00"))
        for colon_x in winampskin.COLON_XS:
            for dy in (3, 8):
                painter.fillRect(QRect(x + colon_x, y + dy, 2, 2), colour)

    def _draw_volume(self, painter):
        x, y = winampskin.VOLUME_POS
        track = self.skin.volume_track(self.volume / 100.0)
        if track:
            painter.drawPixmap(x, y, track)
        knob = self.skin.volume_knob(self._pressed == "volume")
        if knob:
            travel = winampskin.VOLUME_TRACK_SIZE[0] - knob.width()
            painter.drawPixmap(
                x + int(travel * self.volume / 100.0), y + 1, knob
            )

    # -- input --------------------------------------------------------

    def _skin_point(self, event):
        """Window coordinates back to the skin's 275x116 space."""
        return QPoint(event.x() // self.scale, event.y() // self.scale)

    def mousePressEvent(self, event):
        point = self._skin_point(event)

        # Right-click on the title bar opens the same menu the icon
        # does. This window had no right-click handling at all, so it
        # was the one skinned window whose title bar did nothing.
        if event.button() == Qt.RightButton and point.y() < 14:
            if self.on_menu:
                self.on_menu(self.mapToGlobal(event.pos()))
            return

        # Right-click the star: the skin's own history, not the app's.
        #
        # on_favourite_menu was wired up and never called, so the click
        # fell through to whatever was underneath — and the main window's
        # history page opened behind the skin, which is the one place you
        # were not looking.
        if (event.button() == Qt.RightButton
                and self._star_rect().contains(point)):
            if self.on_favourite_menu:
                self.on_favourite_menu()
            return

        # The shade button is on the title bar in both modes.


        if self.shaded:
            # The title-bar buttons still work when rolled up.
            #
            # Everything went straight to the drag handler here, so the
            # shade button could roll the window up and then never
            # unroll it - the one control that has to work in this state
            # was the one that did not.
            for rect, action in self._title_controls():
                if rect.contains(point):
                    action()
                    return
            self._begin_drag(event)
            return

        for name in winampskin.BUTTONS:
            rect = self.skin.button_rect(name)
            if rect and rect.contains(point):
                self._pressed = name
                self.update()
                return

        rect = self.skin.toggle_rect("shuffle")
        if rect and rect.contains(point):
            self.shuffle = not self.shuffle
            if self.on_shuffle:
                self.on_shuffle(self.shuffle)
            self.update()
            return

        rect = self.skin.toggle_rect("repeat")
        if rect and rect.contains(point):
            self.repeat = not self.repeat
            self._repeat_spin = 0.01      # non-zero starts the spin
            self._repeat_timer.start()
            self.update()
            return

        if self._grab_rects()[0].contains(point):
            self._pressed = "volume"
            self._set_volume_from(point)
            return

        if self._vis_rect().contains(point) and not self.shaded:
            mode = self._cycle_vis()
            if self.on_vis_mode:
                self.on_vis_mode(mode)
            return

        if self._clock_rect().contains(point) and not self.shaded:
            self.twenty_four_hour = not self.twenty_four_hour
            if self.on_clock_mode:
                self.on_clock_mode(self.twenty_four_hour)
            self.update()
            return

        for name, rect in self._clutter_rects().items():
            if rect.contains(point) and not self.shaded:
                if name in self.clutter_state:
                    self.clutter_state[name] = not self.clutter_state[name]
                if self.on_clutter:
                    self.on_clutter(name, self.clutter_state.get(name, False))
                self.update()
                return

        # The title bar's own buttons, left to right: the icon menu,
        # then minimise, shade and close on the right. Checked whether
        # shaded or not - the shade button is the only way back when
        # the window is rolled up, so skipping it there stranded it.
        if point.y() < 14:
            if QRect(*winampskin.TITLEBAR_ICON).contains(point):
                if self.on_menu:
                    self.on_menu(self.mapToGlobal(event.pos()))
                return
            if QRect(*winampskin.TITLEBAR_MINIMIZE).contains(point):
                self.window().showMinimized()
                return
            if QRect(*winampskin.TITLEBAR_SHADE).contains(point):
                self.toggle_shade()
                return
            if QRect(*winampskin.TITLEBAR_CLOSE).contains(point):
                # Closes the whole stack, as Winamp's did.
                #
                # Closing only the player left the equaliser, tags and
                # playlist floating with nothing to control them. The
                # visualizer is its own window and is left alone - it is
                # useful without the skin, and shutting it because a
                # skin closed would be surprising.
                if self.on_close_all:
                    self.on_close_all()
                else:
                    self.close()
                return

        if self._title_rect().contains(point) and not self.shaded:
            # Winamp let you drag the marquee left and right. Pausing
            # the automatic scroll while you do is the point: otherwise
            # it fights you.
            self._title_drag_from = point.x()
            self._title_drag_start = self._scroll
            self._scroll_timer.stop()
            return

        if self._star_rect().contains(point):
            if event.button() == Qt.RightButton:
                # The list of what has been identified, since the star
                # is what marks entries in it.
                if self.on_history:
                    self.on_history()
                return
            self._star_pressed = True
            self.update()
            return

        if self._logo_hit(point):
            self._logo_pressed = True
            self.update()
            QTimer.singleShot(150, self._release_logo)
            return

        if self._grab_rects()[1].contains(point):
            self._pressed = "balance"
            self._balance_anim.stop()
            self._set_balance_from(point)
            return

        if self._dial_rect().contains(point) and self.dial_stations:
            self._dial_dragging = True
            self._scan_to(self._dial_index_at(point))
            return

        # Anything else on the title bar drags the window. Frameless
        # means nothing else will do it for us.
        if point.y() < 14:
            self._begin_drag(event)

    def _begin_drag(self, event):
        """Start a drag.

        Docked, the whole stack is one window and the compositor moves
        it - the only approach that works under Wayland. Undocked, we
        fall back to moving ourselves, which is fine on X11 and is all
        that is possible anyway.

        The player is the stack's anchor: dragging it always moves the
        whole stack, as Winamp's main window did. The equalizer and the
        playlist tear off instead - see _SkinnedWindow._begin_drag.
        """
        if self.dock is not None and self.dock.begin_move():
            self._drag_offset = None
            self._drag_followers = ()
            return

        # A loose window: ask the compositor to move it. Manual move()
        # is ignored under Wayland, so without this an undocked window
        # was visible but could never be dragged again.
        handle = self.windowHandle()
        if (handle is not None and hasattr(handle, "startSystemMove")
                and handle.startSystemMove()):
            self._drag_offset = None
            self._drag_followers = ()
            return

        self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
        self._drag_origin = self.frameGeometry().topLeft()
        self._drag_followers = tuple(
            (w, w.frameGeometry().topLeft() - self._drag_origin)
            for w in (self.snap_group.attached_to(self)
                      if self.snap_group else ())
        )

    def _drag_to(self, top_left):
        self.move(top_left)
        for window, offset in self._drag_followers:
            window.move(top_left + offset)

    def _grab_rects(self):
        """The sliders, padded to a size you can actually hit.

        Thirteen pixels tall is a small target, so they grow upward -
        and only one pixel downward, because the dial sits immediately
        below and stealing rows from it is what made clicks near the
        volume slider start retuning the radio.

        Cursor and click use the same rects, so what the pointer
        promises is what the button does.
        """
        return (self._volume_rect().adjusted(-2, -7, 2, 1),
                self._balance_rect().adjusted(-2, -7, 2, 1))

    def _apply_cursor(self, point):
        """Use the skin's own cursors over the parts that have them."""
        volume, balance = self._grab_rects()

        if volume.contains(point) or balance.contains(point):
            name = "volume"
        elif self._dial_rect().contains(point):
            name = "posbar"
        elif point.y() < 14:
            name = "titlebar"
        elif any(r.contains(point) for r in self._clutter_rects().values()):
            name = "menu"
        else:
            # Everything else gets the skin's idle cursor, if it has one.
            name = "normal"

        # The name alone is not enough to skip: if something changed
        # the cursor since, the skin's never came back (task 32).
        if (name == self._cursor_name
                and self.testAttribute(Qt.WA_SetCursor)):
            # Same pointer - but the window may still not be showing it
            # (a panel built under a resting mouse, task 43).
            winampskin.sync_window_cursor(self)
            return
        self._cursor_name = name
        winampskin.set_skin_cursor(self, self.skin, name)

    def enterEvent(self, event):
        # Coming back in (from another panel, a menu, the desktop): work
        # the cursor out afresh rather than trusting the last answer.
        self._cursor_name = None
        pos = getattr(event, "pos", None)
        if callable(pos):
            p = pos()
            self._apply_cursor(QPoint(p.x() // self.scale,
                                      p.y() // self.scale))
        super().enterEvent(event)

    def _repeat_glyph(self, sprite):
        """The loop symbol alone, lifted off the button's face.

        Cached, and found by color rather than by coordinates: the
        button face is whatever fills its corner, and anything that
        differs from it is the symbol. That way this works on a skin
        whose glyph is a different shape or in a different place.
        """
        if self._repeat_cache is not None:
            return self._repeat_cache

        # Inside the bevel, not the whole sprite.
        #
        # These buttons have a raised border, and sampling its corner as
        # the face made every pixel look like part of the symbol - so
        # the "symbol" came out as the entire button and the whole thing
        # span. The interior is where the symbol actually lives.
        inset = 3
        image = sprite.toImage().convertToFormat(QImage.Format_ARGB32)
        face = QColor(image.pixel(inset + 1, image.height() // 2))
        left, top = image.width(), image.height()
        right = bottom = 0
        for y in range(inset, image.height() - inset):
            for x in range(inset, image.width() - inset):
                pixel = QColor(image.pixel(x, y))
                if (abs(pixel.red() - face.red())
                        + abs(pixel.green() - face.green())
                        + abs(pixel.blue() - face.blue())) < 70:
                    image.setPixelColor(x, y, QColor(0, 0, 0, 0))
                else:
                    left, top = min(left, x), min(top, y)
                    right, bottom = max(right, x), max(bottom, y)

        if right <= left or bottom <= top:
            self._repeat_cache = (None, face, None)
            return self._repeat_cache

        box = QRect(left, top, right - left + 1, bottom - top + 1)
        glyph = QPixmap.fromImage(image).copy(box)
        self._repeat_cache = (glyph, face, box)
        return self._repeat_cache

    def _draw_spinning_repeat(self, painter, rect, sprite):
        """The button still, its symbol turning, nothing behind it."""
        glyph, face, box = self._repeat_glyph(sprite)
        painter.drawPixmap(rect.x(), rect.y(), sprite)
        if glyph is None:
            return

        # Wipe where the symbol sits, so the turning copy has the
        # button's plain face under it rather than a second symbol.
        painter.fillRect(
            QRect(rect.x() + box.x(), rect.y() + box.y(),
                  box.width(), box.height()),
            face,
        )

        painter.save()
        painter.translate(rect.x() + box.x() + box.width() / 2,
                          rect.y() + box.y() + box.height() / 2)
        painter.rotate(self._repeat_spin)
        painter.drawPixmap(-glyph.width() // 2, -glyph.height() // 2, glyph)
        painter.restore()

    def _title_controls(self):
        """[(rect, what it does)] for the title bar's own buttons."""
        return [
            (QRect(*winampskin.TITLEBAR_CLOSE),
             lambda: self.on_close_all() if self.on_close_all
             else self.close()),
            (QRect(*winampskin.TITLEBAR_SHADE), self.toggle_shade),
            (QRect(*winampskin.TITLEBAR_MINIMIZE),
             lambda: self.window().showMinimized()),
        ]

    def _end_star_glow(self):
        self._star_glow = False
        self.update()

    def _release_star(self):
        """On release: toggle, and shine for a second if now set."""
        if not self._star_pressed:
            return
        self._star_pressed = False
        self.favourite = not self.favourite
        if self.favourite:
            self._star_glow = True
            self._star_timer.start(1000)
        if self.on_favourite:
            self.on_favourite(self.favourite)
        self.update()

    def set_favourite(self, on):
        self.favourite = bool(on)
        self.update()

    def _star_rect(self):
        return QRect(*winampskin.STAR_RECT)

    def _star_points(self, rect):
        """A five-pointed star inscribed in `rect`."""
        import math
        from PyQt5.QtCore import QPointF
        cx, cy = rect.x() + rect.width() / 2, rect.y() + rect.height() / 2
        outer = min(rect.width(), rect.height()) / 2 - 1
        inner = outer * 0.42
        points = []
        for step in range(10):
            radius = outer if step % 2 == 0 else inner
            angle = math.radians(-90 + step * 36)
            points.append(QPointF(cx + radius * math.cos(angle),
                                  cy + radius * math.sin(angle)))
        return QPolygonF(points)

    def _draw_star(self, painter):
        """Hollow normally, gold when this station is a favourite.

        Kept fairly transparent on skins other than our own: it is not
        part of Winamp's furniture, so on somebody else's artwork it
        should read as an addition rather than something that belongs
        to the panel it is sitting on.
        """
        rect = self._star_rect()
        star = self._star_points(rect)

        alpha = 90 if not self.skin_is_base else 190
        if self._star_hot or self._star_pressed or self.favourite:
            alpha = 255 if self.favourite else 210

        gold = QColor(255, 200, 60, alpha)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        if self.favourite or self._star_glow:
            # Filled, and briefly brighter just after it is set.
            fill = QColor(255, 225, 130) if self._star_glow else gold
            painter.setBrush(fill)
        else:
            painter.setBrush(Qt.NoBrush)

        pen = QColor(255, 210, 90, max(alpha, 120))
        painter.setPen(pen)
        if self._star_pressed:
            # Pressed: drawn a pixel down and in, as the buttons are.
            painter.translate(1, 1)
        painter.drawPolygon(star)
        painter.restore()

    def _draw_title_hover(self, painter):
        """Light whichever title-bar button the pointer is over.

        The skin has no pressed or hovered artwork for these, so a
        gentle wash is the only way to show they are live at all -
        without it they look painted on.
        """
        if self._title_hover is None:
            return
        painter.setOpacity(0.25)
        painter.fillRect(self._title_hover, QColor(255, 255, 255))
        painter.setOpacity(1.0)

    def _button_centre(self, name):
        """The middle of a transport button, in widget coordinates.

        Menus raised by a button belong under that button, not wherever
        the pointer happened to be inside it.
        """
        rect = self.skin.button_rect(name)
        if rect is None:
            return QPoint(0, 0)
        return QPoint(int((rect.x() + rect.width() // 2) * self.scale),
                      int((rect.y() + rect.height()) * self.scale))

    def _release_logo(self):
        """Back to the ordinary window.

        The mark is where Winamp put its own logo, and in a player that
        has two faces the obvious thing for it to do is show the other
        one. It used to only un-press itself - an earlier edit had also
        pasted the constructor's whole body in here, so every click
        quietly reset the window's state as well.
        """
        self._logo_pressed = False
        self.update()
        if self.on_logo:
            self.on_logo()

    def mouseMoveEvent(self, event):
        point = self._skin_point(event)

        self._apply_cursor(point)

        hot = self._star_rect().contains(point)
        if hot != self._star_hot:
            self._star_hot = hot
            self.update()

        over = None
        for rect, _action in self._title_controls():
            if rect.contains(point):
                over = rect
                break
        if over != self._title_hover:
            self._title_hover = over
            self.update()

        # Hover feedback for the mark and the clutterbar.
        hot = self._logo_hit(point)
        if hot != self._logo_hot:
            self._logo_hot = hot
            self.update()

        over = None
        for name, rect in self._clutter_rects().items():
            if rect.contains(point):
                over = name
                break
        if over != self._clutter_hover:
            self._clutter_hover = over
            self.update()

        if self._title_drag_from is not None:
            # One character per character-width of travel.
            moved = (point.x() - self._title_drag_from) // winampskin.CHAR_WIDTH
            span = max(1, len(self.title_text) + 4)
            self._scroll = (self._title_drag_start - moved) % span
            self.update()
            return

        if self._dial_dragging:
            self._scan_to(self._dial_index_at(self._skin_point(event)))
            return
        if self._pressed == "balance":
            self._set_balance_from(self._skin_point(event))
        elif self._pressed == "volume":
            self._set_volume_from(self._skin_point(event))
        elif self._drag_offset is not None:
            self._drag_to(event.globalPos() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if self._star_pressed:
            self._release_star()
            return
        if self._title_drag_from is not None:
            self._title_drag_from = None
            self._scroll_timer.start()      # hand it back to the marquee
            return

        if self._dial_dragging:
            # Letting go settles immediately rather than waiting out
            # the timer - the user has stopped, which is the whole
            # signal the timer exists to detect.
            self._dial_dragging = False
            self._dial_settle.stop()
            self._settle_dial()
            self.update()
            return

        pressed, self._pressed = self._pressed, None
        if pressed == "balance":
            # Let go and it springs back.
            self._balance_anim.start()
            self.update()
            return
        was_dragging = self._drag_offset is not None
        self._drag_offset = None
        self._drag_followers = ()
        if was_dragging and self.snap_group:
            self.snap_group.snap(self)
        self.update()
        if pressed in (None, "volume"):
            return
        # Only fire if the release happened over the same button, which
        # is how every button everywhere behaves.
        rect = self.skin.button_rect(pressed)
        if not rect or not rect.contains(self._skin_point(event)):
            return

        handler = {
            "play": self.on_play,
            "pause": self.on_pause or self.on_play,
            "stop": self.on_stop,
            "next": self.on_next,
            "previous": self.on_previous,
            "eject": lambda: self.on_eject(
                self.mapToGlobal(self._button_centre("eject"))
            ) if self.on_eject else None,
        }.get(pressed)
        if handler:
            handler()

    def mouseDoubleClickEvent(self, event):
        """Double-click the title bar to roll up or down.

        The way Winamp always did it, and the reason the shade button
        being tiny never mattered.
        """
        if self._skin_point(event).y() < 14:
            self.toggle_shade()

    def _volume_rect(self):
        x, y = winampskin.VOLUME_POS
        w, h = winampskin.VOLUME_TRACK_SIZE
        return QRect(x, y, w, h)

    def _set_volume_from(self, point):
        rect = self._volume_rect()
        fraction = (point.x() - rect.x()) / max(1, rect.width())
        self.volume = max(0, min(100, int(round(fraction * 100))))
        if self.on_volume:
            self.on_volume(self.volume)
        self.update()

    def closeEvent(self, event):
        self._scroll_timer.stop()
        # Leave the stack on the way out.
        #
        # Closing a docked panel only hid it: the layout still held a
        # slot the size of the window that used to be there, so the
        # stack kept its height and the gap showed as bare grey.
        if self.dock is not None:
            self.dock.remove_panel(self)
        self.closed.emit()
        super().closeEvent(event)
