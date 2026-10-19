"""
classicextras.py - the equalizer and playlist windows.

The two companions to the skinned main window. Both are drawn the same
way: sprites onto a canvas at the skin's native size, then one scale at
the end with smoothing off.

They differ in one important respect. The equalizer is a fixed 275x116,
exactly like the main window. The playlist was the only resizable
window Winamp had, so its skin provides *tiles* rather than a
background - fixed corners with edges that repeat to fill whatever size
the user drags it to. That is why this file has a tiling routine and
`classicwindow.py` does not.
"""
from PyQt5.QtCore import Qt, QEvent, QPoint, QRect, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QPainter, QPixmap, QColor, QFont, QFontMetrics, QImage, QTransform,
)
from PyQt5.QtWidgets import QWidget, QMenu

import winampskin
from classicwindow import SnapGroup  # noqa: F401 - re-exported


class _SkinnedWindow(QWidget):

    # Emitted when a detached panel wants to go back in the stack.
    reattach_requested = pyqtSignal()
    close_requested = pyqtSignal()

    """Shared frameless-window behavior: dragging, scaled painting, and
    windowshade - the collapsed one-line mode every Winamp window had."""

    closed = pyqtSignal()

    def __init__(self, skin, scale, width, height, parent=None):
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
        # Set on press while docked; a few pixels of movement turns it
        # into a tear-off. See _begin_drag.
        self._tear_from = None
        self._home_dock = None
        # Where a loose window was when its title bar was pressed: only
        # a real drag of the title bar may snap it back on (task 44).
        self._title_press_at = None
        self._cursor_name = None
        # Needed for hover cursors: without it we only hear about the
        # mouse while a button is held.
        self.setMouseTracking(True)
        self.skin = skin
        self.scale = max(1, int(scale))
        # Set by classic.TagResizer while the pointer is on the resize
        # edge; the skin cursor waits until it is off it again.
        self._edge_cursor = False
        # Wear the skin's pointer from the start, not only after the
        # mouse has moved inside: a panel built (or rebuilt by a skin
        # change) under a still pointer showed the desktop's arrow.
        winampskin.set_skin_cursor(self, skin, self.IDLE_CURSOR)
        self.skin_width = width
        self.skin_height = height
        self.full_height = height
        self.shaded = False
        self._drag_offset = None
        # Set by the main window once all three exist.
        self.snap_group = None
        self._drag_followers = ()
        self._drag_origin = None
        self.setFixedSize(width * self.scale, height * self.scale)

    def _refit_dock(self):
        if self.dock is not None:
            self.dock.refit()

    def fit_width(self, width):
        """Take the stack's width (task 97).

        A loose panel can be dragged wider or narrower; coming back onto
        the stack it must match the player, as a docked resize already
        does -- otherwise the dock grew to the wide panel and the space
        beside every narrower one showed bare grey. Width only: the
        height (and shaded state) stay as they are.
        """
        width = int(width)
        if width == self.skin_width:
            return
        self.skin_width = width
        self.setFixedSize(self.skin_width * self.scale,
                          self.skin_height * self.scale)
        self.update()

    # The name in the middle of the title bar, for windows that wear the
    # playlist's frame. None keeps the skin's own word, which is right
    # only for the playlist itself - the one the skin was drawn for.
    PLATE_LABEL = None

    def event(self, event):
        """Redraw when the stack gains or loses focus.

        Every title bar has a lit and a dim version, chosen when the
        panel paints. Qt does not repaint a panel just because its
        window changed focus, so each one kept whichever version it last
        happened to draw - the playlist lit, the equalizer dim, side by
        side in the same window.
        """
        if event.type() in (QEvent.WindowActivate, QEvent.WindowDeactivate):
            self.update()
        return super().event(event)

    def _draw_plate_titlebar(self, painter):
        """The playlist's title bar, at this window's width.

        Shared by the playlist and every window that borrows its frame
        - tags, lyrics, history - so all of them are the same bar:
        the same stripes, the same plate, the same lit and dim states,
        with only the name on the plate changing. Each used to draw its
        own, and no two agreed.
        """
        active = self._is_active()
        suffix = "_selected" if active else ""

        def sprite(name):
            return (self.skin.playlist(name + suffix)
                    or self.skin.playlist(name))

        top_tile = sprite("top_tile")
        if top_tile:
            x = 0
            while x < self.skin_width:
                painter.drawPixmap(x, 0, top_tile)
                x += max(1, top_tile.width())

        label = self.PLATE_LABEL
        plate = self.skin.playlist_title(label.upper() if label else None,
                                         active)
        if plate:
            painter.drawPixmap((self.skin_width - plate.width()) // 2, 0,
                               plate)

        top_left = sprite("top_left")
        if top_left:
            painter.drawPixmap(0, 0, top_left)
        top_right = sprite("top_right")
        if top_right:
            painter.drawPixmap(self.skin_width - top_right.width(), 0,
                               top_right)

    def draw_skin_text(self, painter, x, y, text, spacing=None):
        """Write in the skin's own bitmap font, as the main window does.

        Every panel had invented its own: monospace 8px green here,
        sans-serif 8px bold white there. The result was six windows in
        one skin that looked like six skins. The characters in text.bmp
        are what FREQAMP's own title is drawn from, so they are what all
        of them use.

        Returns the width written, so a caller can underline or centre
        against it.
        """
        step = spacing or winampskin.CHAR_WIDTH
        written = 0
        for index, character in enumerate(text):
            glyph = self.skin.char(character)
            if glyph:
                painter.drawPixmap(x + index * step, y, glyph)
            written += step
        return written

    def skin_text_width(self, text, spacing=None):
        return len(text) * (spacing or winampskin.CHAR_WIDTH)

    def _cursor_regions(self, point):
        """Which skin cursor belongs at this point - per window."""
        if point.y() < winampskin.PLAYLIST_TITLE_HEIGHT:
            return "titlebar"
        return "normal"

    # The skin's pointer for the plain parts of this window.
    IDLE_CURSOR = "normal"

    def _apply_cursor(self, point):
        if self._edge_cursor:
            return
        name = self._cursor_regions(point)
        # The name alone is not enough to skip: something else (the tag
        # window's resize edge, a rebuild) may have changed the cursor
        # since, and then the skin's never came back (task 32).
        if (name == self._cursor_name
                and self.testAttribute(Qt.WA_SetCursor)):
            # Same pointer - but the window may still not be showing it
            # (a panel built under a resting mouse, task 43).
            winampskin.sync_window_cursor(self)
            return
        self._cursor_name = name
        winampskin.set_skin_cursor(self, self.skin, name, self.IDLE_CURSOR)

    def enterEvent(self, event):
        # Coming back in (from another panel, a menu, the desktop): work
        # the cursor out afresh rather than trusting the last answer.
        self._cursor_name = None
        pos = getattr(event, "pos", None)
        if callable(pos):
            p = pos()
            self._apply_cursor(QPoint(p.x() // self.scale,
                                      p.y() // self.scale))
        elif not self._edge_cursor:
            winampskin.set_skin_cursor(self, self.skin, self.IDLE_CURSOR,
                                       self.IDLE_CURSOR)
        super().enterEvent(event)

    def _title_right_click(self, event):
        """True when this was a right-click on the title bar.

        Checked before the window's own controls, so the menu opens
        from the title bar of every panel rather than only from the
        drag handler - which several panels never reach, because their
        own press handling returns first.
        """
        point = self._skin_point(event)
        if (event.button() == Qt.RightButton
                and point.y() < winampskin.PLAYLIST_TITLE_HEIGHT):
            self._title_menu(self.mapToGlobal(event.pos()))
            return True
        return False

    def _title_controls(self, point):
        """Close and shade, wherever the window is in its cycle.

        Checked before anything else and in both states. Shaded windows
        used to hand every click straight to the drag handler, so the
        unshade button did nothing - the one control you actually need
        when a window is collapsed to its title bar.
        """
        for rect, action in self._control_rects():
            if rect.contains(point):
                action()
                return True
        return False

    def _control_rects(self):
        """[(rect, what it does)] - overridden per window."""
        return []

    def _is_active(self):
        """Is the window this panel lives in the active one?

        Always ask window(), never self. A docked panel is a child
        widget and is never "the active window" in its own right, so
        asking it directly meant the equalizer and the player drew their
        inactive title bars while the playlist - which did ask window()
        - drew its active one. Three title bars, two different colors.
        """
        return self.window().isActiveWindow()

    def toggle_shade(self):
        self.shaded = not self.shaded
        self.skin_height = (
            winampskin.SHADE_HEIGHT if self.shaded else self.full_height
        )
        self.setFixedSize(
            self.skin_width * self.scale, self.skin_height * self.scale
        )
        # The stack has to close up around the shaded panel, or the
        # space it used to occupy stays behind as bare grey.
        self._refit_dock()
        self.update()

    def _paint_shade_bar(self, canvas, title):
        """One 14-pixel strip: whatever background the skin gives, and a
        short title. Shared by both windows because both collapse the
        same way."""
        painter = QPainter(canvas)
        background = self._shade_background()
        if background:
            # The playlist's shade background is a 25-pixel tile rather
            # than a full-width bitmap, so it repeats.
            x = 0
            while x < self.skin_width:
                painter.drawPixmap(x, 0, background)
                x += max(1, background.width())
        # No label of our own when the skin's strip already carries one.
        #
        # The shade bitmap for both windows has the window's name drawn
        # into it - writing "Equalizer" over the top in green just
        # produced two titles fighting for the same fourteen pixels.
        if background is None:
            self.draw_skin_text(painter, 6, 3, title.upper())
        painter.end()
        self._blit(canvas)

    def _shade_background(self):
        return None

    def _skin_point(self, event):
        return QPoint(event.x() // self.scale, event.y() // self.scale)

    def _blit(self, canvas):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.drawPixmap(self.rect(), canvas)
        painter.end()

    def mousePressEvent(self, event):
        if self._skin_point(event).y() < 14:
            self._begin_drag(event)

    # Filled in by the main window, so a right-click here offers the
    # same things the icon menu does rather than a stub.
    extra_menu = None

    def _title_menu(self, where):
        """Detach, re-attach, and everything the icon menu offers."""
        menu = QMenu(self)
        if self.extra_menu:
            self.extra_menu(menu)
            menu.addSeparator()
        if self.dock is not None:
            menu.addAction(
                "Detach from the stack",
                lambda: self.dock and self.dock.undock(self, where),
            )
        else:
            menu.addAction("Attach to the stack",
                           lambda: self.reattach_requested.emit())
        menu.addSeparator()
        menu.addAction("Close", self.close_requested.emit)
        menu.exec_(where)

    def _begin_drag(self, event):
        """Start a drag.

        Docked, the compositor moves the whole stack; undocked, we move
        ourselves. See ClassicDock for why the difference matters.
        """
        if event.button() == Qt.RightButton:
            # mapToGlobal, not globalPos.
            #
            # Wayland gives a client no global coordinate space, so
            # QMouseEvent.globalPos() there is measured from the window
            # rather than the screen - which put every menu a windowful
            # to the left of where it belonged. mapToGlobal goes through
            # Qt's own translation and is right on both platforms.
            self._title_menu(self.mapToGlobal(event.pos()))
            return

        # Docked: wait and see whether this is a drag.
        #
        # Handing the drag straight to the compositor - which is the
        # only way to move a window under Wayland - means never hearing
        # about the mouse again, so there is no moment at which to
        # notice the panel has been pulled off the stack. Holding off
        # until the pointer has actually moved a few pixels gives us
        # that moment: past the threshold we detach and hand the
        # compositor the newly freed window, so the drag continues
        # without the user doing anything special.
        if self.dock is not None:
            self._tear_from = event.globalPos()
            self._drag_offset = None
            self._drag_followers = ()
            return

        # A loose window: ask the compositor to move it. Manual move()
        # is ignored under Wayland, so without this an undocked window
        # was visible but could never be dragged again.
        self._title_press_at = self.frameGeometry().topLeft()
        handle = self.windowHandle()
        if (handle is not None and hasattr(handle, "startSystemMove")
                and handle.startSystemMove()):
            self._drag_offset = None
            self._drag_followers = ()
            return

        self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
        origin = self.frameGeometry().topLeft()
        self._drag_followers = tuple(
            (w, w.frameGeometry().topLeft() - origin)
            for w in (self.snap_group.attached_to(self)
                      if self.snap_group else ())
        )

    def mouseDoubleClickEvent(self, event):
        """Double-click the title bar to roll up or down."""
        if self._skin_point(event).y() < 14:
            self.toggle_shade()

    def _finish_tear(self, dock, at):
        if dock is not None:
            dock.undock(self, at)

    def _maybe_redock(self, at):
        """Dropped against the stack? Snap back on."""
        if self.dock is not None or self._home_dock is None:
            return
        if self._home_dock.wants(self, at):
            self._home_dock.add_panel(self)

    def mouseMoveEvent(self, event):
        self._apply_cursor(self._skin_point(event))
        if self._tear_from is not None:
            # Nothing happens during the drag. The tear-off waits for
            # the release - see mouseReleaseEvent for why.
            return
        if self._drag_offset is not None:
            top_left = event.globalPos() - self._drag_offset
            self.move(top_left)
            for window, offset in self._drag_followers:
                window.move(top_left + offset)

    def mouseReleaseEvent(self, event):
        if self._tear_from is not None:
            moved = event.globalPos() - self._tear_from
            self._tear_from = None
            # Tear off on release, not mid-drag.
            #
            # Detaching while the button was held meant destroying the
            # surface that owned the pointer grab and mapping a new one
            # under it, and KWin never showed the replacement - a window
            # in the taskbar and nowhere on screen. This survived every
            # ordering fix because the problem is the handover itself.
            # On release there is no grab: the new window comes up the
            # same way every window that works comes up. The cost is
            # that tearing off takes a drag and then a second drag to
            # place it - the price of it existing at all.
            if abs(moved.x()) + abs(moved.y()) >= 20 and self.dock is not None:
                dock = self.dock
                self._home_dock = dock
                at = event.globalPos()
                QTimer.singleShot(0, lambda: self._finish_tear(dock, at))
            return

        # Snap back on only at the end of a title-bar drag that really
        # moved the window. Any click used to be enough: under Wayland a
        # click's "global" position is measured from the window itself,
        # so it nearly always landed "inside" the stack's frame and a
        # click in a torn-off window put it straight back (task 44).
        # Under Wayland a client cannot see where its window was dropped
        # at all, so there re-docking is the title bar menu's
        # "Attach to the stack".
        pressed_at, self._title_press_at = self._title_press_at, None
        if (pressed_at is not None
                and self.frameGeometry().topLeft() != pressed_at):
            self._maybe_redock(event.globalPos())
        was_dragging = self._drag_offset is not None
        self._drag_offset = None
        self._drag_followers = ()
        if was_dragging and self.snap_group:
            self.snap_group.snap(self)

    def closeEvent(self, event):
        # Leave the stack on the way out.
        #
        # Closing a docked panel only hid it: the layout still held a
        # slot the size of the window that used to be there, so the
        # stack kept its height and the gap showed as bare grey.
        if self.dock is not None:
            self.dock.remove_panel(self)
        self.closed.emit()
        super().closeEvent(event)


class EqualizerWindow(_SkinnedWindow):
    close_requested = pyqtSignal()
    IDLE_CURSOR = "eq_normal"

    """The ten-band EQ, in the skin's own clothes.

    Drives the same per-station bands as the main window's advanced EQ,
    so the two are two views of one setting rather than two settings.
    """

    def __init__(self, skin, scale=2, parent=None):
        super().__init__(skin, scale, winampskin.EQ_WIDTH,
                         winampskin.EQ_HEIGHT, parent)
        self.setWindowTitle("Equalizer")
        self.enabled = False
        self.preamp = 0          # -12..12, the station's gain trim
        self.bands = [0] * 10    # -12..12 each
        self._dragging = None
        # Set by the main window from the per-skin setting.
        self.eq_fill = False

        # Which EQ this window is showing, following the global choice.
        #
        # Ten vertical sliders when advanced, three horizontal ones when
        # not - because that is what the two EQs actually are, and a
        # window showing ten sliders for a three-band EQ would be lying
        # about what the controls do.
        self.advanced = False
        self.simple = [0, 0, 0]  # bass, mid, treble
        self._auto_pressed = False

        self.on_change = None    # (enabled, preamp, bands)

    # -- state --------------------------------------------------------

    def set_state(self, enabled, bands, preamp=0, simple=None):
        """`enabled` is the global advanced/simple choice.

        The window shows whichever EQ is in use: ten bands or three.
        `preamp` is the station's own gain trim, which is what the
        preamp slider has always represented here.
        """
        self.advanced = bool(enabled)
        self.enabled = bool(enabled)
        self.bands = list(bands or [0] * 10)[:10]
        self.bands += [0] * (10 - len(self.bands))
        if simple is not None:
            self.simple = list(simple)[:3] + [0] * (3 - len(list(simple)[:3]))
        self.preamp = int(preamp)
        self.update()

    def _emit(self):
        if self.on_change:
            self.on_change(self.enabled, self.preamp, list(self.bands),
                           list(self.simple))

    # -- painting -----------------------------------------------------

    def _shade_background(self):
        # The EQ has no shade bitmap of its own; its title bar is what
        # Winamp showed when it was rolled up.
        return self.skin.eq("titlebar_selected" if self._is_active()
                            else "titlebar")

    def paintEvent(self, _event):
        canvas = QPixmap(self.skin_width, self.skin_height)
        canvas.fill(QColor("#000000"))
        if self.shaded:
            self._paint_shade_bar(canvas, "Equalizer")
            return
        painter = QPainter(canvas)

        background = self.skin.eq("background")
        if background:
            painter.drawPixmap(0, 0, background)

        titlebar = self.skin.eq(
            "titlebar_selected" if self._is_active() else "titlebar"
        )
        if titlebar:
            painter.drawPixmap(0, 0, titlebar)

        # ON reflects whether the EQ is doing anything, not a setting.
        #
        # A lit ON button over a flat curve says the equaliser is on
        # while it is audibly not; the honest reading is "is any band
        # away from zero".
        on_sprite = self.skin.eq(
            "on_selected" if self._bands_active() else "on"
        )
        if on_sprite:
            painter.drawPixmap(*winampskin.EQ_ON_POS, on_sprite)

        # AUTO is drawn pressed while the mouse is down and never lit:
        # there is no auto-loading of per-song presets here, and a
        # button that latches on while doing nothing is a lie. It still
        # depresses, because a control that ignores you feels broken.
        auto = self.skin.eq(
            "auto_selected" if self._auto_pressed else "auto"
        )
        if auto:
            painter.drawPixmap(*winampskin.EQ_AUTO_POS, auto)
        presets = self.skin.eq("presets")
        if presets:
            painter.drawPixmap(*winampskin.EQ_PRESETS_POS, presets)

        self._draw_curve(painter)
        # The cover goes down before anything is drawn on top of it.
        self._clear_band_area(painter)
        self._draw_sliders(painter)
        self._draw_band_labels(painter)

        painter.end()
        self._blit(canvas)

    # The band area, between the preamp panel and the right edge, down
    # to just above the frequency numbers. The gridline dashes live in
    # here, printed into the background bitmap at three heights.
    # Left edge clears the "+12 db" labels, right edge stops before the
    # window's own border, top starts below the response graph.
    BAND_AREA = (68, 37, 198, 62)

    def _clear_band_area(self, painter):
        """Blank behind the three bars, if this skin is set to.

        Off unless asked for, per skin. Whether the patch helps depends
        entirely on the artwork: on a plain panel it hides gridline
        dashes that label nothing in simple mode, and on a skin with a
        picture behind the bands it covers the picture. Only the person
        looking at it can say which, so it is a switch in the skin
        locker rather than a guess made here.
        """
        if self.advanced or not self.eq_fill:
            return

        background = self.skin.eq("background")
        if background is None:
            return
        image = background.toImage()
        if image.width() < 20 or image.height() < 60:
            return

        # Row by row from the panel's plain right edge, so the patch
        # follows a vertical gradient instead of sitting on it.
        rect = QRect(winampskin.EQ_BAND_X0 - 10, winampskin.EQ_BAND_Y - 1,
                     self.skin_width - winampskin.EQ_BAND_X0 - 2, 74)
        edge_x = max(0, min(image.width() - 1, self.skin_width - 8))
        for row in range(rect.y(), rect.y() + rect.height()):
            if 0 <= row < image.height():
                painter.fillRect(QRect(rect.x(), row, rect.width(), 1),
                                 QColor(image.pixel(edge_x, row)))

    def _draw_band_labels(self, painter):
        """Bass / Mid / Treble down the left, in simple mode.

        The frequency numbers along the bottom belong to the ten-band
        EQ; against three bands they were labelling sliders that are not
        there. Names on the left match how the same three appear in the
        main window.
        """
        if self.advanced:
            return

        # White, not the readout green.
        #
        # These are labels on the panel, not values in a display - the
        # green belongs to things that report a number, and using it
        # here made three static words look like live readouts.
        painter.setPen(QColor(255, 255, 255))
        font = QFont("monospace")
        font.setPixelSize(7)
        painter.setFont(font)
        for index, label in enumerate(("BASS", "MID", "TRBL")):
            rect = self._slider_rects().get(f"s{index}")
            if rect is None:
                continue
            # Right of the preamp panel, in the gap before the bars.
            # Right-aligned against the bar, so the gap between a name
            # and the thing it names is the same for all three.
            painter.drawText(
                QRect(rect.x() - 42, rect.y(), 38, rect.height()),
                Qt.AlignVCenter | Qt.AlignRight, label,
            )

    def _draw_curve(self, painter):
        """The little response graph.

        The skin supplies a background and a one-pixel-wide column of
        line colors; Winamp picked a color per height from that column.
        Drawing our own polyline in the skin's own middle color keeps it
        looking native without pretending to be a real FFT.
        """
        graph = self.skin.eq("graph_background")
        x, y = winampskin.EQ_GRAPH_POS
        if graph:
            painter.drawPixmap(x, y, graph)

        colors = self.skin.eq("graph_line_colors")
        color = QColor(0, 255, 0)
        if colors and colors.height() >= 10:
            color = QColor(colors.toImage().pixel(0, 9))

        painter.setPen(color)
        width, height = 113, 19
        values = self.bands if self.advanced else self.simple
        points = []
        for index, gain in enumerate(values):
            span = max(1, len(values) - 1)
            px = x + int(index * (width - 1) / span)
            py = y + int((height - 1) / 2 - (gain / 12.0) * (height - 1) / 2)
            points.append((px, py))
        for i in range(len(points) - 1):
            painter.drawLine(points[i][0], points[i][1],
                             points[i + 1][0], points[i + 1][1])

    def _draw_sliders(self, painter):
        thumb = self.skin.eq(
            "thumb_pressed" if self._dragging is not None else "thumb"
        )
        if not thumb:
            return

        self._draw_grooves(painter)

        # Two thumbs: the upright one for the preamp, and - in simple
        # mode - a turned one for the three horizontal bands.
        upright = thumb
        turned = thumb.transformed(QTransform().rotate(90))

        for name, rect in self._slider_rects().items():
            gain = self._gain_for(name)
            thumb = turned if (isinstance(name, str)
                               and name.startswith("s")) else upright
            if isinstance(name, str) and name.startswith("s"):
                # Horizontal: the thumb travels left to right, with the
                # middle meaning flat.
                travel = rect.width() - thumb.width()
                offset = int(((gain + 12) / 24.0) * travel)
                painter.drawPixmap(rect.x() + offset, rect.y() + 1, thumb)
            else:
                # Vertical: top of the travel is maximum gain, as on the
                # original.
                offset = int(
                    (1 - (gain + 12) / 24.0) * winampskin.EQ_SLIDER_TRAVEL
                )
                painter.drawPixmap(rect.x() + 1, rect.y() + offset, thumb)

    def _bands_active(self):
        """True when any band is off flat - this lights the ON lamp.

        Named apart from the base class's _is_active, which asks whether
        the window has focus. They are different questions, and having
        both under one name meant the title bar drew its focused variant
        according to whether the equalizer was doing anything.
        """
        values = self.bands if self.advanced else self.simple
        return any(values) or bool(self.preamp)

    def _draw_grooves(self, painter):
        """The colored channel each slider sits in.

        The skin carries 28 of these, running green through yellow to
        red, one per position - so picking the frame that matches the
        gain is what makes a raised band glow.

        For the horizontal EQ the same frame is turned a quarter turn
        and stretched, rather than a gradient being drawn from scratch.
        That way any skin's horizontal bars look like that skin: whoever
        made it chose those colors, and morphing their artwork keeps
        their choice instead of replacing it with ours.
        """
        for name, rect in self._slider_rects().items():
            gain = self._gain_for(name)
            index = max(0, min(27, int(round((gain + 12) / 24.0 * 27))))
            groove = self.skin.eq_slider_frame(index)
            if groove is None:
                continue

            # The preamp stays vertical in both modes.
            #
            # It is not one of the bands - it is the level going in -
            # so turning it on its side with them would suggest it is
            # another band, and it would lose the column of colour that
            # says how far it has been pushed.
            if self.advanced or name == "preamp":
                painter.drawPixmap(rect.x(), rect.y(), groove)
            else:
                turned = groove.transformed(QTransform().rotate(90))
                painter.drawPixmap(
                    rect.x(), rect.y(),
                    turned.scaled(rect.width(), rect.height(),
                                  Qt.IgnoreAspectRatio,
                                  Qt.FastTransformation),
                )

    def _gain_for(self, name):
        if name == "preamp":
            return self.preamp
        if isinstance(name, str) and name.startswith("s"):
            return self.simple[int(name[1:])]
        return self.bands[name]

    def _set_gain(self, name, gain):
        if name == "preamp":
            self.preamp = gain
        elif isinstance(name, str) and name.startswith("s"):
            self.simple[int(name[1:])] = gain
        else:
            self.bands[name] = gain

    # -- input --------------------------------------------------------

    # A different kitten every time. The presets button used to load a
    # saved EQ curve; this is what it does now, and a single fixed
    # search would show the same page forever.
    KITTEN_SEARCHES = (
        "kittens", "silly kittens", "tomcat kittens", "sleepy kittens",
        "kittens in boxes", "tabby kittens", "calico kittens",
        "ginger kittens", "tuxedo kittens", "maine coon kittens",
        "ragdoll kittens", "siamese kittens", "bengal kittens",
        "scottish fold kittens", "british shorthair kittens",
        "persian kittens", "sphynx kittens", "norwegian forest kittens",
        "russian blue kittens", "munchkin kittens", "devon rex kittens",
        "kittens with tiny hats", "kittens being dramatic",
        "kittens sitting like people", "kittens loafing",
        "kittens mid yawn", "kittens in teacups", "surprised kittens",
        "grumpy kittens", "kittens wearing bowties", "black kittens",
        "fluffy kittens", "newborn kittens", "kittens playing",
        "kittens with big ears", "barn kittens", "rescue kittens",
        "kittens and puppies", "kitten pile", "kittens looking guilty",
        "kittens on keyboards", "kittens in sinks", "kittens and yarn",
        "orange tabby kittens", "grey kittens", "tortoiseshell kittens",
        "kittens with blue eyes", "kittens sleeping upside down",
        "kittens in shoes", "kittens on laptops", "wet kittens",
        "kittens in hammocks", "kittens hiding", "kittens climbing curtains",
        "kittens stuck in things", "kittens with hiccups",
        "kittens meeting dogs", "abyssinian kittens", "birman kittens",
        "burmese kittens", "chartreux kittens", "cornish rex kittens",
        "himalayan kittens", "korat kittens", "manx kittens",
        "ocicat kittens", "savannah kittens", "selkirk rex kittens",
        "somali kittens", "turkish van kittens", "american curl kittens",
        "japanese bobtail kittens", "lykoi kittens", "nebelung kittens",
        "singapura kittens", "toyger kittens", "farm kittens",
        "bodega kittens", "library kittens", "kittens in a basket",
        "kittens riding roombas", "kittens in bow ties",
        "kittens making biscuits", "screaming kittens",
        "kittens who have seen things", "unimpressed kittens",
        "kittens caught mid-pounce", "kittens with tiny sweaters",
        "kittens in flowerpots", "kittens on windowsills", "bakery kittens",
        "kittens in the snow", "kittens with butterflies", "beach kittens",
        "kittens under blankets", "kittens in drawers", "kittens on shelves",
        "kittens playing piano", "kittens in teapots", "kittens with balloons",
        "kittens wearing scarves", "kittens in mittens", "kittens at windows",
        "kittens chasing shadows", "kittens and houseplants",
        "kittens investigating cameras", "kittens in cardboard castles",
        "kittens sitting in loaves", "kittens with enormous paws",
        "kittens mid-stretch", "kittens upside down asleep",
        "kittens in laundry baskets", "kittens attacking socks",
        "kittens riding shoulders", "kittens in bookshelves",
        "kittens with feather toys", "kittens napping on keyboards",
        "kittens in sunbeams", "kittens meeting mirrors",
        "kittens standing on hind legs", "kittens in tiny beds",
        "kittens with milk moustaches", "kittens in fruit bowls",
    )

    def _kittens(self):
        """What the presets button does now."""
        try:
            import random
            import webbrowser
            import webview
            query = random.choice(self.KITTEN_SEARCHES)
            # new=1 asks for a window rather than a background tab, so
            # the kittens actually arrive in front of you.
            webbrowser.open(webview.image_search_url(query), new=1,
                            autoraise=True)
        except Exception:  # noqa: BLE001
            pass

    def _slider_rects(self):
        """Where the sliders are, for the EQ currently in use.

        Advanced keeps Winamp's own layout: a preamp and ten vertical
        bands. Simple lays its three out horizontally across the same
        space, since three vertical sliders in a row built for ten would
        just look broken.
        """
        rects = {"preamp": QRect(*winampskin.EQ_PREAMP_POS, 14, 63)}
        if self.advanced:
            for index in range(10):
                rects[index] = QRect(
                    winampskin.EQ_BAND_X0 + index * winampskin.EQ_BAND_SPACING,
                    winampskin.EQ_BAND_Y, 14, 63,
                )
            return rects

        # Three horizontal bars, stacked, filling the band area - with
        # room on the left for their labels.
        # Started clear of the preamp's panel.
        #
        # The bars used to begin close enough to the left that they ran
        # over the raised edge of the preamp's own panel. The right end
        # was already where it should be, so only the left moves - the
        # bars get shorter rather than shifting across.
        left = winampskin.EQ_BAND_X0 + 52
        right = winampskin.EQ_BAND_X0 + 9 * winampskin.EQ_BAND_SPACING + 14
        for index in range(3):
            rects[f"s{index}"] = QRect(
                left,
                winampskin.EQ_BAND_Y + 4 + index * 20,
                max(20, right - left), 14,
            )
        return rects

    def _control_rects(self):
        return [
            (QRect(*winampskin.EQ_CLOSE_POS, 9, 9),
             self.close_requested.emit),
            (QRect(*winampskin.EQ_SHADE_POS, 9, 9), self.toggle_shade),
        ]

    def draw_skin_text(self, painter, x, y, text, spacing=None):
        """Write in the skin's own bitmap font, as the main window does.

        Every panel had invented its own: monospace 8px green here,
        sans-serif 8px bold white there. The result was six windows in
        one skin that looked like six skins. The characters in text.bmp
        are what FREQAMP's own title is drawn from, so they are what all
        of them use.

        Returns the width written, so a caller can underline or centre
        against it.
        """
        step = spacing or winampskin.CHAR_WIDTH
        written = 0
        for index, character in enumerate(text):
            glyph = self.skin.char(character)
            if glyph:
                painter.drawPixmap(x + index * step, y, glyph)
            written += step
        return written

    def skin_text_width(self, text, spacing=None):
        return len(text) * (spacing or winampskin.CHAR_WIDTH)

    def _cursor_regions(self, point):
        if QRect(*winampskin.EQ_CLOSE_POS, 9, 9).contains(point):
            return "close"
        if not self.shaded:
            for rect in self._slider_rects().values():
                if rect.adjusted(-2, -3, 2, 3).contains(point):
                    return "eq_slider"
        if point.y() < 14:
            return "eq_title"
        return "eq_normal"

    def mousePressEvent(self, event):
        if self._title_right_click(event):
            return
        point = self._skin_point(event)
        if self._title_controls(point):
            return
        if self.shaded:
            super().mousePressEvent(event)
            return

        # The presets button. Winamp's opened a menu of saved curves;
        # this one opens an image search for kittens, which was a
        # deliberate request and is left exactly as asked.
        presets_rect = QRect(*winampskin.EQ_PRESETS_POS, 44, 12)
        if presets_rect.contains(point):
            self._kittens()
            return

        auto_rect = QRect(*winampskin.EQ_AUTO_POS, 32, 12)
        if auto_rect.contains(point):
            self._auto_pressed = True
            self.update()
            QTimer.singleShot(140, self._release_auto)
            return

        on_rect = QRect(*winampskin.EQ_ON_POS, 26, 12)
        if on_rect.contains(point):
            # Swaps between the three-band and ten-band equalisers.
            #
            # Winamp's ON switched the EQ off entirely, but this one is
            # never off - the bands sit flat instead - so the button had
            # nothing useful to do. Switching between simple and
            # advanced is what people actually want from it here.
            self.advanced = not self.advanced
            self._emit()
            self.update()
            return

        for name, rect in self._slider_rects().items():
            if rect.contains(point):
                self._dragging = name
                self._set_from(point)
                return

        super().mousePressEvent(event)

    def wheelEvent(self, event):
        """Scroll a slider under the pointer.

        A wheel is the obvious way to nudge one of these by a step or
        two, and much easier than dragging a fourteen-pixel thumb.
        """
        point = self._skin_point(event)
        step = 1 if event.angleDelta().y() > 0 else -1
        for name, rect in self._slider_rects().items():
            if rect.contains(point):
                gain = max(-12, min(12, self._gain_for(name) + step))
                self._set_gain(name, gain)
                self._emit()
                self.update()
                event.accept()
                return
        super().wheelEvent(event)

    def _release_auto(self):
        self._auto_pressed = False
        self.update()

    def mouseMoveEvent(self, event):
        if self._tear_from is not None:
            return super().mouseMoveEvent(event)
        if self._dragging is not None:
            self._set_from(self._skin_point(event))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging is not None:
            self._dragging = None
            self._emit()
            self.update()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Double-clicking a slider zeroes it, as it always did; on the
        title bar it rolls the window up instead."""
        point = self._skin_point(event)
        if point.y() < 14:
            self.toggle_shade()
            return
        for name, rect in self._slider_rects().items():
            if rect.contains(point):
                self._set_gain(name, 0)
                self._emit()
                self.update()
                return

    def _set_from(self, point):
        rect = self._slider_rects()[self._dragging]
        name = self._dragging
        if isinstance(name, str) and name.startswith("s"):
            travel = max(1, rect.width() - 11)
            fraction = max(0.0, min(1.0, (point.x() - rect.x()) / travel))
        else:
            travel = winampskin.EQ_SLIDER_TRAVEL
            fraction = 1 - max(0.0, min(1.0, (point.y() - rect.y()) / travel))
        self._set_gain(name, int(round(fraction * 24 - 12)))
        self.update()


class PlaylistWindow(_SkinnedWindow):
    """The station list, in the skin's own clothes.

    Built from tiles because this was the one window Winamp let you
    resize: fixed corners, repeating edges.
    """

    IDLE_CURSOR = "playlist_normal"

    station_activated = pyqtSignal(int)
    # row, where to show it, and the window it came from - the last so
    # the menu can be parented to this surface. See main._skin_menu.
    station_menu = pyqtSignal(int, QPoint, object)
    # What the user has typed into the playlist's own filter line.
    search_changed = pyqtSignal(str)
    # from row, to row - manual reordering by dragging a station.
    rows_reordered = pyqtSignal(int, int)
    # previous | play | pause | stop | next | eject
    transport = pyqtSignal(str)
    selection_changed = pyqtSignal(set)
    close_requested = pyqtSignal()

    # What the bottom buttons ask the app to do. The playlist window
    # knows nothing about stations beyond their names, so every action
    # is a request rather than something done here.
    add_requested = pyqtSignal(str)      # "url" | "browse" | "custom"
    remove_requested = pyqtSignal(str)   # "selected" | "all" | "dead"
    select_requested = pyqtSignal(str)   # "all" | "none" | "invert" | "tags"
    misc_requested = pyqtSignal(str)     # "sort" | "info"
    list_requested = pyqtSignal(str)     # "backup" | "restore"

    def __init__(self, skin, scale=2, parent=None, width=275, height=232):
        super().__init__(skin, scale, width, height, parent)
        self.setWindowTitle("Playlist")
        self.stations = []
        self.current = -1
        self.offset = 0
        self._row_height = 10
        # Which rows the user has picked out, as indices into the
        # visible list. Selection is separate from what is playing:
        # you select a row in order to do something to it.
        self.selected = set()
        self._scroll_dragging = False
        self._resize_from = None
        self._pressed_button = None
        self.search_text = ""
        self._search_focused = False
        self.on_resized = None
        # Reordering is only offered in manual sort mode; any other
        # order is computed, so dragging a row in it would be undone
        # the moment the list was rebuilt.
        self.manual_sort = False
        # Seconds the current station has been playing.
        self.elapsed = 0
        self._drag_row = -1
        self._drop_row = -1

    def set_stations(self, names, current=-1):
        # A different list starts at the top.
        #
        # Filtering leaves the old scroll position behind, so after a
        # search that matches three stations the window could be
        # scrolled past all of them and look empty until you scrolled
        # back up.
        if list(names) != self.stations:
            self.offset = 0
        self.stations = list(names)
        self.current = current
        # Keep the playing station in view without yanking the list
        # around while someone is reading it.
        if 0 <= current < len(names):
            visible = self._visible_rows()
            if not (self.offset <= current < self.offset + visible):
                self.offset = max(0, current - visible // 2)
        self.update()

    # Whether this window has a filter line at all. The lyrics window
    # borrows this list and has nothing to filter — a song's words are
    # read top to bottom, not searched.
    SEARCHABLE = True
    # Whether the bottom bar carries the playlist's buttons.
    BUTTONS = True

    @property
    def BOTTOM_HEIGHT(self):
        """How tall the frame's bottom border is.

        The playlist's is 38: its buttons, transport and time live there.
        A window without them ends in the visualizer's thin border
        instead (task 98a) - the list runs down to it, rather than
        stopping above an empty 38-pixel bar.
        """
        if getattr(self, "BUTTONS", True):
            return winampskin.PLAYLIST_BOTTOM_HEIGHT
        return winampskin.THIN_BOTTOM_HEIGHT

    def _list_top(self):
        """Where the rows begin - below the title and the filter line."""
        if not getattr(self, "SEARCHABLE", True):
            # The rows move up into the space, rather than leaving a gap.
            return winampskin.PLAYLIST_TITLE_HEIGHT + 2
        return (winampskin.PLAYLIST_TITLE_HEIGHT
                + PLAYLIST_SEARCH_HEIGHT + 2)

    def _search_rect(self):
        """Inside the frame, not across it.

        A three-pixel inset put the box over the window's own side
        border and, on the right, under the scrollbar. The list itself
        starts twelve pixels in; the filter line lines up with it.
        """
        left = 12
        right = 20
        return QRect(left, winampskin.PLAYLIST_TITLE_HEIGHT + 2,
                     max(20, self.skin_width - left - right),
                     PLAYLIST_SEARCH_HEIGHT - 1)

    def _visible_rows(self):
        usable = (self.skin_height - self._list_top()
                  - self.BOTTOM_HEIGHT)
        return max(1, usable // self._row_height)

    def _shade_background(self):
        return self.skin.shade("playlist_background")

    def paintEvent(self, _event):
        canvas = QPixmap(self.skin_width, self.skin_height)
        canvas.fill(self.skin.color("normalbg", "#000000"))
        if self.shaded:
            playing = (self.stations[self.current]
                       if 0 <= self.current < len(self.stations) else "Playlist")
            self._paint_shade_bar(canvas, playing)
            return
        painter = QPainter(canvas)
        self._draw_frame(painter)
        self._draw_rows(painter)
        painter.end()
        self._blit(canvas)

    def _draw_frame(self, painter):
        """Corners fixed, edges repeated - the tiling the skin expects.

        All four corners, not just the top-left. The bottom corners
        carry the buttons and the resize grip and the top-right carries
        the close button, so leaving them out made the window look sawn
        off down the right-hand side.
        """
        # The same title bar the tags, lyrics and history windows draw,
        # with the skin's own word left on the plate.
        self._draw_plate_titlebar(painter)

        body_top = winampskin.PLAYLIST_TITLE_HEIGHT
        body_bottom = self.skin_height - self.BOTTOM_HEIGHT

        left = self.skin.playlist("left_tile")
        right = self.skin.playlist("right_tile")
        if left:
            y = body_top
            while y < body_bottom:
                painter.drawPixmap(0, y, left)
                y += left.height()
        if right:
            y = body_top
            while y < body_bottom:
                painter.drawPixmap(self.skin_width - right.width(), y, right)
                y += right.height()

        if not getattr(self, "BUTTONS", True):
            # No buttons: the visualizer's thin border and grip, drawn by
            # the same code (task 98a). The side tiles above may overrun
            # into it; it goes on after them, so it covers that.
            #
            # This used to keep the playlist's whole 38-pixel bottom bar
            # and only crop the buttons off its corners, which left a
            # tall empty bar under every list - with the scroll arrows
            # and the grip floating in it.
            winampskin.paint_thin_bottom(painter, self.skin,
                                         self.skin_width, self.skin_height)
        else:
            bottom = self.skin.playlist("bottom_tile")
            if bottom:
                x = 0
                while x < self.skin_width:
                    painter.drawPixmap(x, body_bottom, bottom)
                    x += bottom.width()

            # The corners go on last so they sit over the repeated tiles.
            bottom_left = self.skin.playlist("bottom_left")
            bottom_right = self.skin.playlist("bottom_right")
            if bottom_left:
                painter.drawPixmap(0, body_bottom, bottom_left)
            if bottom_right:
                painter.drawPixmap(
                    self.skin_width - bottom_right.width(), body_bottom,
                    bottom_right,
                )
            self._draw_transport_time(painter)
        self._draw_drop_marker(painter)
        self._draw_search(painter)
        self._draw_scrollbar(painter)
        self._draw_pressed_button(painter)

    def _draw_search(self, painter):
        """The filter line, drawn in the skin's own list colors.

        Same query string the tag chips edit and the main window reads,
        so typing here and clicking a chip there are the same action.
        """
        if not getattr(self, "SEARCHABLE", True):
            return
        rect = self._search_rect()
        painter.fillRect(rect, QColor(self.skin.playlist_colors.get(
            "normalbg", "#000000")))
        edge = QColor(self.skin.playlist_colors.get("normal", "#00FF00"))
        painter.setPen(edge if self._search_focused else QColor(90, 90, 90))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        # Drawn with a real font, for the same reason the tag chips are.
        text = self.search_text or "Filter..."
        ink = edge if self.search_text else QColor(120, 120, 120)
        font = QFont("sans-serif")
        font.setPixelSize(8)
        painter.setFont(font)
        painter.setPen(ink)
        inner = rect.adjusted(3, 0, -3, 0)
        painter.drawText(inner, Qt.AlignVCenter | Qt.AlignLeft, text)

        if self._search_focused:
            width = QFontMetrics(font).horizontalAdvance(self.search_text)
            caret = min(inner.x() + width + 1, inner.right())
            painter.fillRect(QRect(caret, rect.y() + 2, 1,
                                   PLAYLIST_SEARCH_HEIGHT - 5), edge)

    def _draw_drop_marker(self, painter):
        """A line showing where a dragged station would land."""
        if self._drop_row < 0 or not self.manual_sort:
            return
        top = self._list_top() + (self._drop_row - self.offset) * self._row_height
        if not (self._list_top() <= top <= self.skin_height
                - self.BOTTOM_HEIGHT):
            return
        painter.fillRect(
            QRect(12, top - 1, self.skin_width - 32, 2),
            QColor(self.skin.playlist_colors.get("current", "#FFFFFF")),
        )

    def _transport_rects(self):
        """Where each little bottom-row button sits."""
        rects = {}
        if not getattr(self, "BUTTONS", True):
            # Not drawn, so not clickable: on a thin bottom these
            # positions would fall on the list's last rows.
            return rects
        for name, (dx, dy, width) in winampskin.PLAYLIST_TRANSPORT.items():
            rects[name] = QRect(
                self.skin_width + dx, self.skin_height + dy,
                width, winampskin.PLAYLIST_TRANSPORT_HEIGHT,
            )
        return rects

    def _draw_transport_time(self, painter):
        """The elapsed time, in the skin's own digits.

        Winamp showed the track position here. A stream has no
        position, so this shows how long the station has been playing -
        which is the only elapsed time a radio has, and is what the
        main window's clock shows too.
        """
        if self.shaded:
            return
        minutes, seconds = divmod(max(0, int(self.elapsed)), 60)
        minutes %= 100

        for (dx, dy), value in ((winampskin.PLAYLIST_TIME_MIN, minutes),
                                (winampskin.PLAYLIST_TIME_SEC, seconds)):
            x = self.skin_width + dx
            y = self.skin_height + dy
            # The small text font, stepped by its own glyph width.
            #
            # numbers.bmp is thirteen pixels tall and this strip is
            # eight, so those digits do not fit; the text font does. It
            # is five wide, and stepping by anything else either
            # overlaps them or scatters them.
            for offset, digit in enumerate(f"{value:02d}"):
                glyph = self.skin.char(digit)
                if glyph is None:
                    continue
                # Drawn as the skin supplies it.
                #
                # Recolouring means deciding per pixel which is ink and
                # which is background, and at five by six that guess is
                # wrong often enough to turn digits to mush - the same
                # thing that made the visualizer's title unreadable.
                painter.drawPixmap(x + offset * glyph.width(), y, glyph)

    def _clear_pressed(self):
        self._pressed_button = None
        self.update()

    def _draw_pressed_button(self, painter):
        """Darken whichever bottom button is held down."""
        if not self._pressed_button:
            return
        rect = (self._button_rects().get(self._pressed_button)
                or self._transport_rects().get(self._pressed_button))
        if rect is None:
            return
        painter.setOpacity(0.35)
        painter.fillRect(rect, QColor(0, 0, 0))
        painter.setOpacity(1.0)

    def _draw_scrollbar(self, painter):
        """The handle on the right edge, positioned by scroll offset."""
        handle = self.skin.playlist("scroll_handle")
        if handle is None:
            return
        rect = self._scroll_rect()
        rows = max(1, len(self.stations) - self._visible_rows())
        fraction = min(1.0, max(0.0, self.offset / rows)) if rows else 0.0
        travel = max(0, rect.height() - handle.height())
        painter.drawPixmap(rect.x(), rect.y() + int(travel * fraction), handle)

    def _scroll_rect(self):
        x = self.skin_width + winampskin.PLAYLIST_SCROLL_X
        top = winampskin.PLAYLIST_SCROLL_TOP
        bottom = self.skin_height - (
            winampskin.PLAYLIST_SCROLL_BOTTOM
            if getattr(self, "BUTTONS", True) else self.BOTTOM_HEIGHT)
        return QRect(x, top, 8, max(1, bottom - top))

    def _resize_rect(self):
        """The grip in the bottom-right corner.

        Winamp's playlist was its one resizable window and the grip is
        drawn into the corner sprite already - it just needed to do
        something.
        """
        size = min(20, self.BOTTOM_HEIGHT)
        return QRect(self.skin_width - size, self.skin_height - size,
                     size, size)

    def _close_rect(self):
        dx, y = winampskin.PLAYLIST_CLOSE_POS
        return QRect(self.skin_width + dx, y, 9, 9)

    def _draw_rows(self, painter):
        """Station names, in the colors pledit.txt asked for.

        A real font rather than the 5x6 bitmap one: station names are
        long, arbitrary and full of characters the bitmap font has no
        glyph for, and an unreadable list would be a poor trade for
        period accuracy.
        """
        normal = self.skin.color("normal", "#00FF00")
        current = self.skin.color("current", "#FFFFFF")
        selected_bg = self.skin.color("selectedbg", "#0000C6")

        font = QFont("monospace")
        font.setPixelSize(8)
        painter.setFont(font)

        x = winampskin.PLAYLIST_LEFT_WIDTH + 2
        width = (self.skin_width - x
                 - winampskin.PLAYLIST_RIGHT_WIDTH - 2)
        y = self._list_top()

        for row in range(self._visible_rows()):
            index = self.offset + row
            if index >= len(self.stations):
                break
            top = y + row * self._row_height
            if index in self.selected:
                painter.fillRect(x - 1, top - 1, width, self._row_height,
                                 selected_bg)
            elif index == self.current:
                # The playing row gets the same highlight at half
                # strength, so "selected" and "playing" stay distinct.
                faded = QColor(selected_bg)
                faded.setAlpha(110)
                painter.fillRect(x - 1, top - 1, width, self._row_height,
                                 faded)
            painter.setPen(current if index == self.current else normal)
            painter.drawText(
                QRect(x, top, width, self._row_height),
                # Lyrics sit in the middle, the way a song's words are
                # set; a playlist stays left-aligned so its numbers line
                # up down the edge.
                Qt.AlignVCenter | (Qt.AlignHCenter
                                   if getattr(self, "CENTRED", False)
                                   else Qt.AlignLeft),
                # Numbered only where the number means something.
                #
                # The lyrics and history windows borrow this list, and a
                # track number in front of each line of a song is noise.
                (f"{index + 1}. {self.stations[index]}"
                 if getattr(self, "NUMBERED", True)
                 else self.stations[index]),
            )

    def _button_rects(self):
        """Where each bottom button sits, in skin coordinates.

        Hit regions only - nothing is drawn here. The bottom-left and
        bottom-right corner sprites already have ADD, REM, SEL, MISC and
        LIST printed into them, so drawing our own on top produced the
        doubled, overlapping buttons. Audacious does the same: the
        artwork comes from the corners, and only the clicking is code.
        """
        width, height = winampskin.PLAYLIST_BUTTON_SIZE
        y = self.skin_height - winampskin.PLAYLIST_BUTTON_BOTTOM
        rects = {}
        for name, spec in winampskin.PLAYLIST_BUTTONS.items():
            x = spec["x"]
            if x < 0:                      # measured from the right edge
                x = self.skin_width + x
            rects[name] = QRect(x, y, width, height)
        return rects

    def _menu_for(self, name):
        """The menu a button opens.

        Everything here is a signal rather than an action: this window
        draws a list of names and knows nothing about stations, so the
        app does the work and tells it what changed.
        """
        menu = QMenu(self)

        if name == "add":
            menu.addAction(
                "Add a station by URL...",
                lambda: self.add_requested.emit("url"))
            menu.addAction(
                "Search radio-browser...",
                lambda: self.add_requested.emit("browse"))
            menu.addAction(
                "New custom station...",
                lambda: self.add_requested.emit("custom"))
        elif name == "rem":
            menu.addAction(
                "Remove selected",
                lambda: self.remove_requested.emit("selected"))
            menu.addAction(
                "Remove stations that are down",
                lambda: self.remove_requested.emit("dead"))
            menu.addSeparator()
            menu.addAction(
                "Remove all",
                lambda: self.remove_requested.emit("all"))
        elif name == "sel":
            menu.addAction(
                "Select all", lambda: self.select_requested.emit("all"))
            menu.addAction(
                "Select none", lambda: self.select_requested.emit("none"))
            menu.addAction(
                "Invert selection",
                lambda: self.select_requested.emit("invert"))
            menu.addSeparator()
            # Tags live here because this is the "which of these do I
            # mean" menu, and filtering by tag is the same question.
            menu.addAction(
                "Tags...", lambda: self.select_requested.emit("tags"))
        elif name == "misc":
            menu.addAction(
                "Sort list...", lambda: self.misc_requested.emit("sort"))
            menu.addAction(
                "Station info...", lambda: self.misc_requested.emit("info"))
        elif name == "list":
            menu.addAction(
                "Back up library...",
                lambda: self.list_requested.emit("backup"))
            menu.addAction(
                "Restore from backup...",
                lambda: self.list_requested.emit("restore"))
        return menu

    def _scroll_to(self, point):
        rect = self._scroll_rect()
        fraction = (point.y() - rect.y()) / max(1, rect.height())
        highest = max(0, len(self.stations) - self._visible_rows())
        self.offset = int(round(max(0.0, min(1.0, fraction)) * highest))
        self.update()

    def mouseMoveEvent(self, event):
        if self._drag_row >= 0:
            row = self._row_at(self._skin_point(event))
            if row != self._drop_row:
                self._drop_row = row
                self.update()
            return
        if self._tear_from is not None:
            return super().mouseMoveEvent(event)
        if self._resize_from is not None:
            start, width, height = self._resize_from
            moved = event.globalPos() - start
            # Rounded to whole rows and whole scale units, so the frame
            # tiles keep meeting and rows do not end up half drawn.
            self.resize_skin(width + moved.x() // self.scale,
                             height + moved.y() // self.scale)
            return

        if self._scroll_dragging:
            self._scroll_to(self._skin_point(event))
            return
        super().mouseMoveEvent(event)

    def resize_skin(self, width, height):
        """Change the window's size in skin pixels.

        Docked, the width is fixed to the stack's.

        The player and equalizer are fixed-width, so a wider playlist
        just made the dock wider and left bare grey beside everything
        else. Winamp only ever let the playlist be wider than the main
        window as a separate window, which is exactly the rule here:
        drag it out and it resizes freely, docked it resizes vertically.

        The minimum is enforced here rather than only in the drag
        handler: any caller can resize this, and a window narrower than
        its own frame tiles draws its corners on top of each other.
        """
        if self.dock is not None:
            width = self.skin_width
        self.skin_width = max(winampskin.PLAYLIST_MIN_WIDTH, int(width))
        self.skin_height = max(winampskin.PLAYLIST_MIN_HEIGHT, int(height))
        # Told so it can be remembered for next time.
        if self.on_resized:
            self.on_resized(self.skin_width, self.skin_height)
        self.full_height = self.skin_height
        self.setFixedSize(self.skin_width * self.scale,
                          self.skin_height * self.scale)
        self._refit_dock()
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_row >= 0:
            source, target = self._drag_row, self._drop_row
            self._drag_row = self._drop_row = -1
            self.update()
            if target >= 0 and target != source:
                self.rows_reordered.emit(source, target)
            self._resize_from = None
            self._scroll_dragging = False
            return

        self._resize_from = None
        self._scroll_dragging = False
        super().mouseReleaseEvent(event)

    def set_selection(self, rows):
        self.selected = set(rows)
        self.update()

    def wheelEvent(self, event):
        step = -1 if event.angleDelta().y() > 0 else 1
        highest = max(0, len(self.stations) - self._visible_rows())
        self.offset = max(0, min(highest, self.offset + step * 3))
        self.update()

    def _row_at(self, point):
        """Which visible row a point falls on, or -1.

        Bounded at the bottom of the list, not just the top. Without
        that, a click on the ADD button forty pixels below the last row
        still computed a row number - so every press on the bottom
        buttons was swallowed as a row selection and the buttons
        appeared to do nothing at all.
        """
        top = self._list_top()
        bottom = self.skin_height - self.BOTTOM_HEIGHT
        if not (top <= point.y() < bottom):
            return -1

        row = (point.y() - top) // self._row_height
        # Bounded by the rows actually drawn, not just by the panel.
        #
        # The panel is taller than the rows that fit in it, so the strip
        # under the last one was still inside the list area. With more
        # stations than fit on screen the arithmetic there produced a
        # perfectly valid index - a station well down the list - so
        # clicking empty space selected one, and a double-click played
        # it.
        if row >= self._visible_rows():
            return -1

        index = self.offset + row
        return index if 0 <= index < len(self.stations) else -1

    def keyPressEvent(self, event):
        """Typing goes to the filter line when it has focus."""
        if not self._search_focused:
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape):
            self._search_focused = False
        elif event.key() == Qt.Key_Backspace:
            self.search_text = self.search_text[:-1]
            self.search_changed.emit(self.search_text)
        elif event.text() and event.text().isprintable():
            self.search_text += event.text()
            self.search_changed.emit(self.search_text)
        self.update()

    def set_search_text(self, text):
        """Set from outside - a tag chip clicked in the tag window."""
        if text != self.search_text:
            self.search_text = text or ""
            self.update()

    def _control_rects(self):
        close = self._close_rect()
        return [
            (close, self.close_requested.emit),
            (QRect(close.x() - 10, close.y(), 9, 9), self.toggle_shade),
        ]

    def draw_skin_text(self, painter, x, y, text, spacing=None):
        """Write in the skin's own bitmap font, as the main window does.

        Every panel had invented its own: monospace 8px green here,
        sans-serif 8px bold white there. The result was six windows in
        one skin that looked like six skins. The characters in text.bmp
        are what FREQAMP's own title is drawn from, so they are what all
        of them use.

        Returns the width written, so a caller can underline or centre
        against it.
        """
        step = spacing or winampskin.CHAR_WIDTH
        written = 0
        for index, character in enumerate(text):
            glyph = self.skin.char(character)
            if glyph:
                painter.drawPixmap(x + index * step, y, glyph)
            written += step
        return written

    def skin_text_width(self, text, spacing=None):
        return len(text) * (spacing or winampskin.CHAR_WIDTH)

    def _cursor_regions(self, point):
        if self._close_rect().contains(point):
            return "close"
        if not self.shaded and self._resize_rect().contains(point):
            return "playlist_resize"
        if point.y() < winampskin.PLAYLIST_TITLE_HEIGHT:
            return "playlist_title"
        return "playlist_normal"

    def mousePressEvent(self, event):
        if self._title_right_click(event):
            return
        point = self._skin_point(event)
        if self._title_controls(point):
            return

        if not self.shaded:
            # The little transport row, before the list gets a look:
            # it sits inside the bottom strip, which _row_at already
            # refuses, but the buttons still have to be checked first.
            for name, rect in self._transport_rects().items():
                if rect.contains(point):
                    self._pressed_button = name
                    self.update()
                    self.transport.emit(name)
                    QTimer.singleShot(120, self._clear_pressed)
                    return

            if (getattr(self, "SEARCHABLE", True)
                    and self._search_rect().contains(point)):
                self._search_focused = True
                self.setFocus(Qt.MouseFocusReason)
                self.update()
                return
            if self._search_focused:
                self._search_focused = False
                self.update()

            if self._resize_rect().contains(point):
                self._resize_from = (event.globalPos(),
                                     self.skin_width, self.skin_height)
                return

            if self._scroll_rect().contains(point):
                self._scroll_dragging = True
                self._scroll_to(point)
                return

            row = self._row_at(point)
            if row >= 0:
                if event.button() == Qt.RightButton:
                    # Right-clicking an unselected row selects it first,
                    # so the menu always acts on something visible.
                    if row not in self.selected:
                        self.selected = {row}
                        self.selection_changed.emit(set(self.selected))
                    # Right-click selects the row too.
                    #
                    # Acting on a row the click did not visibly pick is
                    # unnerving - you cannot see what the menu is about
                    # to affect.
                    self.selection = {row}
                    self.update()
                    self.station_menu.emit(
                        row, self.mapToGlobal(event.pos()), self
                    )
                    self.update()
                    return

                if (self.manual_sort
                        and not event.modifiers()
                        and event.button() == Qt.LeftButton):
                    # Remembered, not acted on: a press is only the
                    # start of a drag if the pointer then moves, and
                    # treating every click as one would make selecting
                    # a station impossible.
                    self._drag_row = row

                if event.modifiers() & Qt.ControlModifier:
                    self.selected ^= {row}
                else:
                    self.selected = {row}
                self.selection_changed.emit(set(self.selected))
                self.update()
                return

        if not self.shaded:
            buttons = (self._button_rects().items()
                       if getattr(self, "BUTTONS", True) else ())
            for name, rect in buttons:
                if rect.contains(point):
                    # Show it going down. A button that opens a menu
                    # without ever looking pressed reads as broken even
                    # when it works.
                    self._pressed_button = name
                    self.update()
                    menu = self._menu_for(name)
                    # Opened above the button, as Winamp's did - the
                    # buttons are at the bottom of the window, so a menu
                    # dropping downwards would fall off the screen.
                    below = self.mapToGlobal(
                        QPoint(rect.x() * self.scale,
                               rect.y() * self.scale)
                    )
                    menu.exec_(QPoint(below.x(),
                                      below.y() - menu.sizeHint().height()))
                    self._pressed_button = None
                    self.update()
                    return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        point = self._skin_point(event)
        if self.shaded or point.y() < winampskin.PLAYLIST_TITLE_HEIGHT:
            # On the title bar (or already rolled up) this toggles the
            # shade rather than picking a station.
            if point.y() < 14:
                self.toggle_shade()
            return
        # Through _row_at, not its own arithmetic.
        #
        # This worked out the row itself, from the wrong origin and with
        # no bound at the bottom - so a double-click in the empty frame
        # under the list computed a valid row and started playing it.
        index = self._row_at(point)
        if index >= 0:
            self.station_activated.emit(index)


# ---------------------------------------------------------------------
# The tag window
# ---------------------------------------------------------------------

# The playlist's own filter line, under its title bar.
PLAYLIST_SEARCH_HEIGHT = 12

TAG_ROW_HEIGHT = 14
TAG_PADDING = 5
TAG_GAP = 3
# The frame's side tiles. Chips have to stay inside these or they are
# drawn on top of the window's own border.
TAG_SIDE_INSET = 12
# The bottom edge of the frame, and the height reserved for it.
TAG_BOTTOM = 14


class TagWindow(_SkinnedWindow):
    """The tag chips, in the skin's own frame.

    Sits above the playlist in the stack and works exactly as the rail
    in the main window does: left-click to show only that tag, click
    again to clear it, right-click to hide it. Nothing about filtering
    lives here - it reports clicks and the main window edits the same
    search text the rest of the app reads, so a tag clicked here and a
    query typed there can never disagree.
    """

    IDLE_CURSOR = "playlist_normal"

    tag_clicked = pyqtSignal(str, bool)      # tag, exclude
    clear_requested = pyqtSignal()

    PLATE_LABEL = "Freqamp Tags"

    def __init__(self, skin, scale=1, parent=None, width=275, height=58):
        super().__init__(skin, scale, width, height, parent)
        self.tags = []                       # [(tag, colour)]
        self.states = {}                     # tag -> 1 include, -1 exclude
        self._layout = []                    # [(tag, QRect)]
        self._hover = None
        self._resize_from = None
        self.setMouseTracking(True)

    # -- what the main window feeds it ---------------------------------

    def resize_skin(self, width, height):
        """Change size in skin pixels, as the playlist does."""
        self.skin_width = int(width)
        self.skin_height = max(winampskin.PLAYLIST_TITLE_HEIGHT + 20,
                               int(height))
        self.full_height = self.skin_height
        self.setFixedSize(self.skin_width * self.scale,
                          self.skin_height * self.scale)
        self._refit_dock()
        self.update()

    def set_tags(self, tags):
        """[(tag, "#rrggbb")] in the order they should appear."""
        self.tags = list(tags)
        self.update()

    def set_states(self, states):
        """tag -> included / excluded, from the current query."""
        self.states = dict(states or {})
        self.update()

    # -- geometry ------------------------------------------------------

    def _font(self):
        font = QFont("sans-serif")
        font.setPixelSize(8)
        font.setBold(True)
        return font

    def _text_width(self, tag):
        """Measured with the font it is actually drawn in.

        The skin's 5x6 bitmap font was unreadable at this size once
        recolored - deciding per pixel which was ink and which was
        background got it wrong often enough to turn short words to
        mush. A real font is legible, and legibility matters more on a
        filter control than matching the skin's letterforms.
        """
        return QFontMetrics(self._font()).horizontalAdvance(tag)

    def _lay_out(self):
        """Chips wrapped into rows, like the rail wraps them."""
        placed = []
        x = TAG_SIDE_INSET
        y = winampskin.PLAYLIST_TITLE_HEIGHT + TAG_GAP
        limit = self.skin_width - TAG_SIDE_INSET
        bottom = self.skin_height - TAG_BOTTOM

        for tag, _colour in self.tags:
            width = self._text_width(tag) + TAG_PADDING * 2
            if x + width > limit and x > TAG_SIDE_INSET:
                x = TAG_SIDE_INSET
                y += TAG_ROW_HEIGHT + TAG_GAP
            if y + TAG_ROW_HEIGHT > bottom:
                break                        # no room; the rest are cut
            placed.append((tag, QRect(x, y, width, TAG_ROW_HEIGHT)))
            x += width + TAG_GAP
        self._layout = placed
        return placed

    def height_for_rows(self, rows):
        """The window height that fits `rows` rows of chips."""
        return (winampskin.PLAYLIST_TITLE_HEIGHT + TAG_GAP
                + rows * (TAG_ROW_HEIGHT + TAG_GAP) + TAG_BOTTOM)

    def rows_needed(self):
        """How tall this window wants to be for the tags it has."""
        rows, x = 1, TAG_SIDE_INSET
        limit = self.skin_width - TAG_SIDE_INSET
        for tag, _colour in self.tags:
            width = self._text_width(tag) + TAG_PADDING * 2
            if x + width > limit and x > TAG_SIDE_INSET:
                rows += 1
                x = TAG_SIDE_INSET
            x += width + TAG_GAP
        return rows

    # -- painting ------------------------------------------------------

    def _shade_background(self):
        # Rolled up, the same strip the playlist, lyrics and history
        # windows show - not green letters on black.
        return self.skin.shade("playlist_background")

    def paintEvent(self, _event):
        canvas = QPixmap(self.skin_width, self.skin_height)
        canvas.fill(QColor("#000000"))
        if self.shaded:
            self._paint_shade_bar(canvas, "Tags")
            return

        painter = QPainter(canvas)
        self._paint_frame(painter)
        self._paint_chips(painter)
        painter.end()
        self._blit(canvas)

    def _paint_frame(self, painter):
        """The playlist frame, so it belongs to the stack it sits in."""
        suffix = "_selected" if self._is_active() else ""

        def sprite(name):
            return (self.skin.playlist(name + suffix)
                    or self.skin.playlist(name))

        # The playlist's own title bar, with this window's name on the
        # plate. It used to punch a black hole in the middle and write
        # FREQAMP in the green song-title letters, which matched nothing
        # else in the stack.
        self._draw_plate_titlebar(painter)

        left, right = sprite("left_tile"), sprite("right_tile")
        painter.save()
        painter.setClipRect(QRect(0, winampskin.PLAYLIST_TITLE_HEIGHT,
                                  self.skin_width, self.skin_height))
        y = winampskin.PLAYLIST_TITLE_HEIGHT
        step = (left or right).height() if (left or right) else 29
        limit = self.skin_height - TAG_BOTTOM
        while y < limit:
            if left:
                painter.drawPixmap(0, y, left)
            if right:
                painter.drawPixmap(self.skin_width - right.width(), y, right)
            y += step
        painter.restore()

        # The bottom edge, cropped to what is reserved for it - the
        # playlist's tile is 38 tall because it houses that window's
        # buttons, and drawing all of it here would swallow a row of
        # chips.
        # The bottom edge, and both corners.
        #
        # Only the tiles were drawn, so the window ended in a straight
        # run with no corner pieces - which is what makes it look
        # unfinished next to the playlist. The corners are cropped to
        # the same strip the tiles are, since the playlist's are 38
        # tall to house its buttons and this window has none.
        bottom = sprite("bottom_tile")
        if bottom:
            edge = bottom.copy(0, max(0, bottom.height() - TAG_BOTTOM),
                               bottom.width(),
                               min(TAG_BOTTOM, bottom.height()))
            x = 0
            while x < self.skin_width:
                painter.drawPixmap(x, self.skin_height - edge.height(), edge)
                x += edge.width()

        # Only the outer few pixels of each corner.
        #
        # The playlist's corners carry its buttons and time readout, so
        # taking a full-width slice brought a row of transport controls
        # along with the border. The corner proper is the first handful
        # of columns; that is all this window needs.
        edge_width = 12
        for name, at_left in (("bottom_left", True), ("bottom_right", False)):
            corner = self.skin.playlist(name)
            if corner is None:
                continue
            top = max(0, corner.height() - TAG_BOTTOM)
            height = min(TAG_BOTTOM, corner.height())
            if at_left:
                piece = corner.copy(0, top, edge_width, height)
                painter.drawPixmap(0, self.skin_height - height, piece)
            else:
                piece = corner.copy(corner.width() - edge_width, top,
                                    edge_width, height)
                painter.drawPixmap(self.skin_width - edge_width,
                                   self.skin_height - height, piece)

    def _paint_chips(self, painter):
        """A chip per tag, colored as in the main window."""
        for tag, rect in self._lay_out():
            colour = QColor(dict(self.tags).get(tag) or "#8899aa")
            state = self.states.get(tag.lower(), 0)

            # Included chips are filled, excluded ones struck through,
            # untouched ones outlined - the same three states the rail
            # shows, in a form that survives a 13-pixel-tall chip.
            if state == 1:
                painter.fillRect(rect, colour)
                ink = QColor(0, 0, 0)
            else:
                painter.fillRect(rect, QColor(0, 0, 0, 90))
                ink = colour
            painter.setPen(colour)
            painter.drawRect(rect.adjusted(0, 0, -1, -1))

            if self._hover == tag:
                painter.fillRect(rect, QColor(255, 255, 255, 40))

            painter.setPen(ink)
            painter.setFont(self._font())
            painter.drawText(rect, Qt.AlignCenter, tag)

            if state == -1:
                middle = rect.y() + rect.height() // 2
                painter.setPen(colour)
                painter.drawLine(rect.x() + 2, middle,
                                 rect.right() - 2, middle)

    # -- input ---------------------------------------------------------

    def _tag_at(self, point):
        for tag, rect in self._layout:
            if rect.contains(point):
                return tag
        return None

    def mousePressEvent(self, event):
        if self._title_right_click(event):
            return
        point = self._skin_point(event)
        if self._title_controls(point):
            return

        tag = self._tag_at(point)
        if tag is not None and not self.shaded:
            self.tag_clicked.emit(tag, event.button() == Qt.RightButton)
            return

        if event.button() == Qt.RightButton:
            self._title_menu(self.mapToGlobal(event.pos()))
            return

        if self._grip_rect().contains(point) and not self.shaded:
            self._resize_from = (event.globalPos(), self.skin_height)
            return

        # Dragged by the title bar only.
        #
        # Any press anywhere used to start a drag, so the empty space
        # between chips moved the window and the corner grip did too -
        # there was nowhere in the window that was not a drag handle.
        if point.y() < winampskin.PLAYLIST_TITLE_HEIGHT:
            self._begin_drag(event)

    def _grip_rect(self):
        """The bottom-right corner, matching the drawn grip."""
        return QRect(self.skin_width - TAG_BOTTOM,
                     self.skin_height - TAG_BOTTOM,
                     TAG_BOTTOM, TAG_BOTTOM)

    def mouseMoveEvent(self, event):
        if self._resize_from is not None:
            start, height = self._resize_from
            moved = event.globalPos().y() - start.y()
            # Height only: the width is the stack's, and a tag window
            # wider than the playlist above it would look broken.
            self.resize_skin(self.skin_width,
                             height + moved // self.scale)
            return

        point = self._skin_point(event)
        over = self._tag_at(point)
        if over != self._hover:
            self._hover = over
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_from = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._skin_point(event).y() < winampskin.PLAYLIST_TITLE_HEIGHT:
            self.toggle_shade()

    def _control_rects(self):
        close = QRect(self.skin_width - 11, 3, 9, 9)
        return [
            (close, self.close_requested.emit),
            (QRect(close.x() - 10, close.y(), 9, 9), self.toggle_shade),
        ]

    def draw_skin_text(self, painter, x, y, text, spacing=None):
        """Write in the skin's own bitmap font, as the main window does.

        Every panel had invented its own: monospace 8px green here,
        sans-serif 8px bold white there. The result was six windows in
        one skin that looked like six skins. The characters in text.bmp
        are what FREQAMP's own title is drawn from, so they are what all
        of them use.

        Returns the width written, so a caller can underline or centre
        against it.
        """
        step = spacing or winampskin.CHAR_WIDTH
        written = 0
        for index, character in enumerate(text):
            glyph = self.skin.char(character)
            if glyph:
                painter.drawPixmap(x + index * step, y, glyph)
            written += step
        return written

    def skin_text_width(self, text, spacing=None):
        return len(text) * (spacing or winampskin.CHAR_WIDTH)

    def _cursor_regions(self, point):
        if self._tag_at(point):
            return "normal"
        if point.y() < winampskin.PLAYLIST_TITLE_HEIGHT:
            return "titlebar"
        return "playlist_normal"


def _tinted(glyph, colour):
    """A bitmap-font glyph recolored, keeping its letterforms."""
    image = glyph.toImage().convertToFormat(QImage.Format_ARGB32)
    background = QColor(image.pixel(0, 0))
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = QColor(image.pixel(x, y))
            near = (abs(pixel.red() - background.red())
                    + abs(pixel.green() - background.green())
                    + abs(pixel.blue() - background.blue())) < 60
            image.setPixelColor(x, y, QColor(0, 0, 0, 0) if near else colour)
    return QPixmap.fromImage(image)
