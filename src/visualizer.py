"""The projectM visualizer window.

A plain top-level window holding a QOpenGLWindow, deliberately NOT part
of the skinned stack. An OpenGL surface inside a frameless child window
is the exact shape of thing that produced the disappearing-window bugs,
so this keeps its own window and its own context where the compositor
can see it.

Without an audio tap projectM renders its preset against silence, which
still animates - just gently. player.py is where a real PCM feed would
come from; see projectm.py.
"""

import logging
import random
import sys
import threading

from PyQt5.QtCore import QEvent, QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor, QCursor, QKeySequence, QOpenGLWindow, QPainter,
    QPixmap, QSurfaceFormat,
)
from PyQt5.QtWidgets import (
    QAction, QActionGroup, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QSlider, QVBoxLayout, QWidget, QWidgetAction,
)

from PyQt5 import sip

import audiotap
import projectm
import winampskin

log = logging.getLogger("frequencies.visualizer")


def _vlog(text):
    """One `[vis] …` line in the app's log (build 68, tasks 33/55/56).

    The visualiser only runs in the built AppImage, so these lines are
    how a test on the real PC shows what happened: each window's number,
    when it closes, and how many visualiser windows and audio captures
    are still running afterwards.
    """
    try:
        import crashlog
        crashlog.write("[vis] " + text)
    except Exception:                           # noqa: BLE001
        pass


# What the visualiser can listen to, listed off the window's thread
# (build 69, task 61). The right-click menu ran `pactl list short
# sources` on the window's thread each time it opened (up to a 5 s
# timeout). Now the list is made on a Job — when a window opens, and
# again each time the menu opens so a new device shows up — and the menu
# shows the last list it has. None: not listed yet.
_sources = None
_sources_listing = False
_sources_waiters = []


def list_sources_later(then=None):
    """Make the list again in the background; then() when it is in."""
    global _sources_listing
    if then is not None:
        _sources_waiters.append(then)
    if _sources_listing:
        return
    _sources_listing = True
    import workers
    job = workers.Lookup(audiotap.list_sources)
    job.done.connect(_sources_listed)
    job.start()


def _sources_listed(result, error):
    global _sources, _sources_listing
    _sources_listing = False
    if error is None and result is not None:
        _sources = list(result)
    elif _sources is None:
        _sources = []
    waiters = list(_sources_waiters)
    _sources_waiters.clear()
    for then in waiters:
        try:
            then()
        except Exception:                       # noqa: BLE001
            pass


# Every visualiser window not yet closed, oldest first. Kept so quitting
# can close them all properly, and so the log can count them.
_open_windows = []
# Every one not yet deleted (a closed one waits for the event loop).
_alive = {}
_window_count = 0


def windows_open():
    return len(_open_windows)


def close_all(why="quit"):
    """Close every visualiser window and delete it now (build 68).

    Called by the main window on quit, while Qt is still fully there: a
    GL window left for Python's own clean-up is deleted after the
    application, in no set order.
    """
    windows = [w for w in _open_windows if not sip.isdeleted(w)]
    if windows:
        _vlog(f"{why}: closing {len(windows)} visualiser window(s)")
    for window in windows:
        try:
            window.close()
        except RuntimeError:
            pass
    del _open_windows[:]
    # Closed ones still waiting to be deleted: after the event loop has
    # ended nothing else will, so now, while Qt is still here.
    for window in list(_alive.values()):
        if not sip.isdeleted(window):
            try:
                sip.delete(window)
            except RuntimeError:
                pass
    _alive.clear()


_quit_hooked = False


def _hook_last_window():
    """Log when closing the last app window quits the app (task 69).

    Build 69 and older: the visualiser counted as an app window, so
    closing the main window with it open did not quit. This line shows,
    on the real PC, that closing the main window now quits with the
    visualiser still up (Qt then closes it: `window #N closed` follows).
    """
    global _quit_hooked
    if _quit_hooked:
        return
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return
    _quit_hooked = True
    app.lastWindowClosed.connect(
        lambda: _vlog(f"the app's last window closed, quitting — "
                      f"{_counts()} (they close with the app)"))


def _forget(number):
    _alive.pop(number, None)
    _vlog(f"window #{number} deleted (after closing)")


def _counts():
    return (f"visualiser windows open: {windows_open()}, "
            f"audio taps running: {audiotap.running_count()}")

# How long a preset stays up when shuffling, and the frame rates on offer.
SHUFFLE_SECONDS = 30
FRAME_RATES = (30, 60, 120)
DEFAULT_FPS = 60

# Presets are listed in submenus of this many, because a flat menu of a
# few hundred is not a menu, it is a wall.
MENU_CHUNK = 25


# The frame is drawn from the skin's playlist sprites.
#
# Winamp's own generic-window frame lives in gen.bmp, but plenty of
# skins - this one included - ship a gen.bmp containing only the title
# font, with the frame pieces left blank. The playlist frame is present
# in every skin, is the right visual language, and already matches the
# rest of the stack.
FRAME_TOP = 20
FRAME_SIDE = 12
# The playlist's bottom tile is 38 tall because it houses that window's
# buttons. This window has none, so only the bottom edge of that sprite
# is used - the part that is actually the border. Reserving 14 while
# drawing all 38 is what put 24 pixels of frame underneath the GL child,
# where it showed as a bar flickering whenever the surface repainted.
FRAME_BOTTOM = winampskin.THIN_BOTTOM_HEIGHT


class VisualizerWindow(QWidget):
    """Holds the GL window, or an explanation of why it cannot.

    Skinned rather than decorated by the desktop, so it belongs with the
    rest of the stack instead of looking like a stray dialog.
    """

    # Passed up so the chosen source and gain can be saved.
    source_changed = pyqtSignal(str)
    gain_changed = pyqtSignal(float)
    disabled_changed = pyqtSignal(list)

    def __init__(self, skin=None, scale=1, source=None, gain=1.0,
                 disabled=(), parent=None):
        super().__init__(parent)
        global _window_count
        _window_count += 1
        self.number = _window_count
        self._closed = False
        self._release_logged = False
        self._menu_logged = False
        # Deleted by Qt itself, after it closes, never by Python (build 68,
        # task 33). Owned by Python, a top-level window was deleted
        # whenever its last Python name went — possibly in the middle of
        # Qt painting it, which is a crash in Qt's own code. Now close()
        # stops projectM and the audio first, and Qt deletes the window
        # at a safe moment (the event loop), with its GL window inside.
        self.setAttribute(Qt.WA_DeleteOnClose)
        # Not a reason to keep the app running (build 70, task 69). Qt
        # quits when the last window is closed, and this one counted: with
        # the visualiser open, closing the main window left the app
        # running, the visualiser on screen and nothing to quit it with,
        # until it was closed by hand. Now closing the main window quits,
        # and quitting closes this (mainwindow.shutdown → close_all).
        self.setAttribute(Qt.WA_QuitOnClose, False)
        if parent is None:
            sip.transferto(self, None)
        self.skin = skin
        self._source = source
        self._gain = float(gain)
        self._disabled = set(disabled or ())
        # Ready by the first right-click (task 61).
        list_sources_later()
        self.scale = max(1, int(scale))
        # What the taskbar shows.
        self.setWindowTitle("Visualizer")
        from classicwindow import skin_icon
        self.setWindowIcon(skin_icon())
        self._drag_from = None
        self._resize_from = None
        self._browser = None
        self.canvas = None
        self.container = None

        if self.skin is not None:
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.resize(640, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        usable, message = projectm.availability()
        gl_ok, gl_message = opengl_available()
        log.info("visualizer opening: projectM usable=%s, opengl ok=%s",
                 usable, gl_ok)
        if not usable:
            log.warning("projectM unavailable: %s", message)
        if not gl_ok:
            log.warning("opengl unavailable: %s", gl_message)

        if usable and gl_ok:
            self.canvas = _ProjectMCanvas(self.number)
            self.canvas.source = self._source
            self.canvas.failed.connect(self._show_failure)
            self.canvas.menu_requested.connect(self._show_menu)
            self.canvas.source_changed.connect(self.source_changed)
            self.canvas.gain_changed.connect(self.gain_changed)
            self.canvas.gain = self._gain
            self.canvas.disabled = set(self._disabled)
            self.canvas.disabled_changed.connect(self.disabled_changed)
            self.canvas.fullscreen_requested.connect(self.toggle_fullscreen)
            self.canvas.key_pressed.connect(self._handle_key)
            # A QOpenGLWindow is a window, so it needs a container to
            # live inside a layout.
            self.container = QWidget.createWindowContainer(self.canvas, self)
            # Placed by hand in resizeEvent, not by the layout.
            #
            # A native child window is positioned by the compositor, and
            # a layout margin scaled by the zoom factor rounded to a
            # size a pixel or two taller than the space left for it - so
            # the GL surface overhung the bottom frame, which is the
            # broken border and the bar that flickers there when it
            # repaints. Explicit geometry leaves no rounding to differ.
            if self.skin is None:
                layout.addWidget(self.container)
            # Right-click anywhere for the options.
            self.setContextMenuPolicy(Qt.CustomContextMenu)
            self.customContextMenuRequested.connect(self._show_menu)
        else:
            # No GL widget at all when a context is impossible.
            #
            # A QOpenGLWidget that cannot get a context never paints -
            # the window shows whatever was behind it and smears when
            # dragged, with nowhere to put an explanation. Better to
            # never create one and show the reason instead.
            self.canvas = None
            label = QLabel(message if not usable else gl_message, self)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("padding: 24px;")
            layout.addWidget(label)

        _open_windows.append(self)
        _alive[self.number] = self
        _hook_last_window()
        number = self.number
        self.destroyed.connect(lambda *_a: _forget(number))
        _vlog(f"window #{self.number} opened"
              f"{'' if self.canvas is not None else ' (no picture: ' + ('projectM' if not usable else 'OpenGL') + ' unavailable)'}"
              f" — {_counts()}")

    def changeEvent(self, event):
        """Repaint the frame when coming back from minimised.

        This window draws its own decoration, and a restore does not
        always ask for that to be redrawn - so it came back with the
        left and top edges missing. Asking explicitly, once the restore
        has settled, puts them back.
        """
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange and not self.isMinimized():
            QTimer.singleShot(0, self._repaint_frame)

    def _repaint_frame(self):
        self._place_container()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_container()

    def _place_container(self):
        container = getattr(self, "container", None)
        if container is None or self.skin is None:
            return
        if self.isFullScreen():
            container.setGeometry(0, 0, self.width(), self.height())
            return
        left = FRAME_SIDE * self.scale
        top = FRAME_TOP * self.scale
        width = max(1, self.width() - left * 2)
        height = max(1, self.height() - top - FRAME_BOTTOM * self.scale)
        container.setGeometry(left, top, width, height)
        # And the GL window itself.
        #
        # A window inside a container does not always follow it, and
        # when it does not projectM keeps rendering at the size it
        # started with while the frame around it grows.
        if self.canvas is not None:
            self.canvas.resize(width, height)

    # -- the skinned frame ---------------------------------------------

    def paintEvent(self, _event):
        if self.skin is None or self.isFullScreen():
            return
        canvas = QPixmap(max(1, self.width() // self.scale),
                         max(1, self.height() // self.scale))
        canvas.fill(QColor("#000000"))
        painter = QPainter(canvas)
        self._paint_frame(painter, canvas.width(), canvas.height())
        painter.end()
        target = QPainter(self)
        target.drawPixmap(0, 0, canvas.scaled(self.width(), self.height()))
        target.end()

    def _paint_frame(self, painter, width, height):
        """The playlist frame, stretched to this window."""
        active = self.isActiveWindow()
        suffix = "_selected" if active else ""

        def sprite(name):
            return (self.skin.playlist(name + suffix)
                    or self.skin.playlist(name))

        top_tile = sprite("top_tile")
        if top_tile:
            x = 0
            while x < width:
                painter.drawPixmap(x, 0, top_tile)
                x += top_tile.width()

        # The title strip, with its own name over the playlist's.
        # The same relabeled plate the tags, lyrics and history windows
        # wear, so the name is in the same letters, size and ink as
        # theirs (task 98b). It used to be painted over with a band and
        # VISUALIZER written in an 8px system font - bigger, blurrier
        # and a different face from every other title bar.
        title = self.skin.playlist_title("FREQAMP VISUALIZER", active)
        if title:
            painter.drawPixmap((width - title.width()) // 2, 0, title)

        for name, x in (("top_left", 0),
                        ("top_right", width - 25)):
            piece = sprite(name)
            if piece:
                painter.drawPixmap(x, 0, piece)

        # The sides, clipped so they stop at the bottom strip.
        #
        # A 29-pixel tile drawn from the last position before the limit
        # overshoots by up to 28 pixels, which is what left the bottom
        # border looking broken - side tiles painted over it.
        left, right = sprite("left_tile"), sprite("right_tile")
        bottom_edge = height - FRAME_BOTTOM
        painter.save()
        painter.setClipRect(QRect(0, FRAME_TOP, width,
                                  max(0, bottom_edge - FRAME_TOP)))
        y = FRAME_TOP
        step = (left or right).height() if (left or right) else 29
        while y < bottom_edge:
            if left:
                painter.drawPixmap(0, y, left)
            if right:
                painter.drawPixmap(width - right.width(), y, right)
            y += step
        painter.restore()

        # Plain bottom tiles only.
        #
        # The playlist's bottom corners have ADD/REM/SEL/MISC and LIST
        # printed into them, which would be nonsense on a visualizer -
        # so the run of tiles goes right across instead.
        # Shared with the lyrics and history windows (task 98a).
        winampskin.paint_thin_bottom(painter, self.skin, width, height,
                                     sprite("bottom_tile"))
        self._paint_minimize(painter, width)

    def _paint_minimize(self, painter, width):
        """A minimize mark over the shade button's artwork.

        The frame comes from the playlist, whose second button rolls the
        window up - so the sprite there shows a shade icon for a button
        that minimizes. Covering it with a bar along the bottom, which
        is what a minimize control looks like everywhere else, stops the
        picture contradicting the action.
        """
        rect = self._minimize_rect()
        strip = self.skin.playlist("top_tile")
        if strip is not None:
            image = strip.toImage()
            painter.fillRect(rect, QColor(image.pixel(2, 10)))
        painter.fillRect(
            QRect(rect.x() + 1, rect.bottom() - 2, rect.width() - 2, 2),
            QColor(210, 210, 220),
        )

    def _close_rect(self):
        return QRect((self.width() // self.scale) - 11, 3, 9, 9)

    def _minimize_rect(self):
        """Beside close, where the playlist puts its shade button.

        Minimize rather than shade: a window whose whole purpose is a
        picture has nothing useful to show rolled up to its title bar,
        whereas getting it out of the way without closing it is
        something you actually want.
        """
        close = self._close_rect()
        return QRect(close.x() - 10, close.y(), 9, 9)

    def _grip_rect(self):
        """The bottom-right corner, where the skin draws its grip."""
        return QRect((self.width() // self.scale) - FRAME_BOTTOM,
                     (self.height() // self.scale) - FRAME_BOTTOM,
                     FRAME_BOTTOM, FRAME_BOTTOM)

    def mousePressEvent(self, event):
        if self.skin is None:
            return super().mousePressEvent(event)
        point = QPoint(event.x() // self.scale, event.y() // self.scale)
        if self._close_rect().contains(point):
            self.close()
            return

        if self._minimize_rect().contains(point):
            # Deferred out of the mouse event.
            #
            # Minimizing from the taskbar works and minimizing from here
            # did not, and the only difference is that this happens
            # inside the window's own press handler while it still holds
            # the mouse. Changing window state there leaves the frame
            # half torn down. Letting the event finish first makes the
            # two paths identical.
            QTimer.singleShot(0, self.showMinimized)
            return

        if self._grip_rect().contains(point):
            self._resize_from = (event.globalPos(), self.width(),
                                 self.height())
            return
        if event.y() < FRAME_TOP * self.scale:
            # Hand the drag to the compositor, as the skinned stack does.
            #
            # The return value matters. startSystemMove can refuse - no
            # window manager, or a compositor that will not do it - and
            # returning regardless meant the drag was neither handed over
            # nor handled here. What movement there was came from stray
            # events, which is a window that resists the mouse.
            handle = self.windowHandle()
            if (handle is not None and hasattr(handle, "startSystemMove")
                    and handle.startSystemMove()):
                return
            self._drag_from = event.globalPos() - self.frameGeometry().topLeft()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_from is not None:
            # Ask the compositor to do it.
            #
            # Resizing by hand from mouse-move events means every frame
            # waits for a full repaint of the GL child before the next
            # move is processed, which is the lag. The compositor drives
            # its own resize loop and the window keeps up.
            handle = self.windowHandle()
            if (handle is not None and hasattr(handle, "startSystemResize")
                    and handle.startSystemResize(Qt.BottomEdge | Qt.RightEdge)):
                self._resize_from = None
                return

            start, width, height = self._resize_from
            moved = event.globalPos() - start
            # A floor big enough that the frame tiles still meet and the
            # title strip is not wider than the window holding it.
            self.resize(max(200 * self.scale, width + moved.x()),
                        max(120 * self.scale, height + moved.y()))
            return
        if self._drag_from is not None:
            self.move(event.globalPos() - self._drag_from)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # Only the drag and the resize end here (build 68, task 55).
        #
        # This also used to forget the picture, its holder and the preset
        # list on every release on the frame — one click on the title bar
        # or the grip, and closing no longer stopped projectM or the
        # audio capture, the menu and keys did nothing and resizing left
        # the picture behind. The GL window kept drawing, with nothing
        # left that could stop it.
        self._drag_from = None
        self._resize_from = None
        if not self._release_logged and self.skin is not None:
            self._release_logged = True
            _vlog(f"window #{self.number}: frame clicked — picture "
                  f"{'still attached' if self.canvas is not None else 'not there'}"
                  f", menu and keys still work")
        super().mouseReleaseEvent(event)

    def _show_failure(self, reason):
        """Replace the blank canvas with the reason it is blank."""
        _vlog(f"window #{self.number}: projectM could not start: "
              f"{str(reason).splitlines()[0] if reason else '?'}")
        if self.canvas is not None:
            self.canvas.shutdown()
        container = getattr(self, "container", None)
        if container is not None:
            container.hide()
            self.layout().removeWidget(container)
            container.deleteLater()
            self.container = None
        self.canvas = None
        label = QLabel(f"The visualizer could not start.\n\n{reason}", self)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("padding: 24px;")
        self.layout().addWidget(label)

    # -- the options menu ---------------------------------------------

    def _show_menu(self, _pos=None):
        """Open the options menu at the pointer.

        exec_ on a menu raised from a native GL window left it stuck
        open: the window keeps the mouse, so the click that should
        dismiss the menu never reaches it. popup() with an explicit
        grab hands input to the menu instead, and it closes on a click
        anywhere as a menu should.
        """
        if self.canvas is None:
            return
        menu = self._build_menu()
        menu.setAttribute(Qt.WA_DeleteOnClose)
        menu.popup(QCursor.pos())
        # Not on Wayland (build 70, task 70). There Qt sends every pointer
        # event over another surface to the window holding the grab, at
        # (-1, -1): the submenus (Audio source, Frame rate…) opened, but
        # their entries never lit up or took a click — "as if my mouse
        # wasn't hovering". Reproduced on a real Wayland desktop (sway,
        # pointer input from a virtual mouse). Wayland's own popup grab
        # closes the menu on a click in another program, and a click on
        # the picture closes it in _ProjectMCanvas.mousePressEvent, so
        # the grab is only kept on X11.
        from PyQt5.QtGui import QGuiApplication
        wayland = QGuiApplication.platformName().startswith("wayland")
        if not wayland:
            menu.grabMouse()
            menu.grabKeyboard()
            menu.aboutToHide.connect(menu.releaseMouse)
            menu.aboutToHide.connect(menu.releaseKeyboard)
        if not self._menu_logged:
            self._menu_logged = True
            _vlog(f"window #{self.number}: menu opened "
                  f"({'Wayland, no mouse grab' if wayland else ('Windows' if sys.platform == 'win32' else 'X11') + ', mouse grab'})"
                  f", audio sources listed: "
                  f"{'not yet' if _sources is None else len(_sources)}")

    def _build_menu(self):
        canvas = self.canvas
        menu = QMenu(self)

        current = canvas.current_preset
        if current:
            name = next((n for n, path in canvas.presets if path == current),
                        "this preset")
            off = current in canvas.disabled
            action = QAction(
                f"{'Re-enable' if off else 'Disable'}  {name[:40]}", menu)
            action.triggered.connect(
                lambda _c, p=current, o=off: canvas.set_disabled(p, not o)
            )
            menu.addAction(action)
            menu.addSeparator()

        menu.addAction("Next preset", canvas.next_preset)
        menu.addAction("Previous preset", canvas.previous_preset)
        menu.addAction("Random preset", canvas.random_preset)
        menu.addSeparator()

        shuffle = QAction("Shuffle every 30 seconds", menu, checkable=True)
        shuffle.setChecked(canvas.shuffling)
        shuffle.toggled.connect(canvas.set_shuffling)
        menu.addAction(shuffle)

        rate_menu = menu.addMenu("Frame rate")
        rates = QActionGroup(rate_menu)
        for fps in FRAME_RATES:
            action = QAction(f"{fps} fps", rate_menu, checkable=True)
            action.setChecked(canvas.fps == fps)
            action.triggered.connect(
                lambda _checked, value=fps: canvas.set_fps(value)
            )
            rates.addAction(action)
            rate_menu.addAction(action)

        menu.addSeparator()
        self._add_preset_menu(menu, canvas)

        # Say plainly whether it can hear anything. Without this, a
        # visualizer drifting along to silence looks broken with no
        # indication of why.
        menu.addSeparator()
        self._add_sensitivity(menu, canvas)
        self._add_source_menu(menu, canvas)

        # Only the failure is worth a line.
        #
        # "Listening to the audio output" told you the thing was working
        # while you were watching it work — a disabled entry taking up a
        # row to confirm what the bars already say.
        if not canvas.listening:
            status = menu.addAction("Not hearing audio - see Help")
            status.setEnabled(False)
            help_menu = menu.addMenu("Help")
            reason = help_menu.addAction(audiotap.availability()[1])
            reason.setEnabled(False)

        menu.addSeparator()
        full = QAction("Fullscreen", menu, checkable=True)
        full.setChecked(self.isFullScreen())
        full.setShortcut(QKeySequence("F11"))
        full.triggered.connect(self.toggle_fullscreen)
        menu.addAction(full)
        keys = menu.addMenu("Keyboard shortcuts")
        for combination, what in self.SHORTCUTS:
            entry = keys.addAction(f"{combination:<12} {what}")
            entry.setEnabled(False)

        menu.addAction("Close", self.close)
        return menu

    def toggle_fullscreen(self):
        """F11, Alt+Enter, or a double-click.

        The skinned frame is dropped while full screen - a title bar and
        a resize grip are furniture for a window, and there is no window
        to speak of once it fills the display.
        """
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self._place_container()
        self.update()

    # Every shortcut in one table, so the menu can list exactly what
    # the keys do rather than a description that drifts from the code.
    SHORTCUTS = (
        ("F11", "Fullscreen"),
        ("Alt+Enter", "Fullscreen"),
        ("Esc", "Leave fullscreen"),
        ("Space", "Next preset"),
        ("Backspace", "Previous preset"),
        ("R", "Random preset"),
        ("S", "Shuffle on/off"),
        ("D", "Disable this preset"),
        ("L", "Preset list"),
        ("Up / Down", "Sensitivity"),
    )

    def _handle_key(self, key, modifiers):
        canvas = self.canvas
        if key == Qt.Key_F11:
            self.toggle_fullscreen()
        elif key in (Qt.Key_Return, Qt.Key_Enter) and modifiers & Qt.AltModifier:
            self.toggle_fullscreen()
        elif key == Qt.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
        elif canvas is None:
            return
        elif key == Qt.Key_Space:
            canvas.next_preset()
        elif key == Qt.Key_Backspace:
            canvas.previous_preset()
        elif key == Qt.Key_R:
            canvas.random_preset()
        elif key == Qt.Key_S:
            canvas.set_shuffling(not canvas.shuffling)
        elif key == Qt.Key_D and canvas.current_preset:
            canvas.set_disabled(canvas.current_preset,
                                canvas.current_preset not in canvas.disabled)
        elif key == Qt.Key_L:
            self._open_preset_browser()
        elif key == Qt.Key_Up:
            canvas.set_gain(min(16.0, canvas.gain + 0.5))
        elif key == Qt.Key_Down:
            canvas.set_gain(max(0.1, canvas.gain - 0.5))

    def keyPressEvent(self, event):
        self._handle_key(event.key(), int(event.modifiers()))

    def mouseDoubleClickEvent(self, event):
        self.toggle_fullscreen()
        super().mouseDoubleClickEvent(event)

    def _add_sensitivity(self, menu, canvas):
        """A gain slider, live while you drag it.

        Needed because the capture is a monitor of the output, which is
        post-volume: at a comfortable listening level the samples are a
        long way below full scale, so an honest meter looks half asleep.
        This scales what the analyzer sees without touching what plays.
        """
        holder = QWidget(menu)
        row = QHBoxLayout(holder)
        row.setContentsMargins(12, 4, 12, 4)

        label = QLabel(f"Sensitivity  {canvas.gain:.1f}x", holder)
        label.setMinimumWidth(96)
        row.addWidget(label)

        slider = QSlider(Qt.Horizontal, holder)
        # Tenths, so the slider can carry fractions without a spin box.
        slider.setRange(10, 160)
        slider.setValue(int(canvas.gain * 10))
        slider.setMinimumWidth(160)

        def changed(value):
            gain = value / 10.0
            label.setText(f"Sensitivity  {gain:.1f}x")
            canvas.set_gain(gain)

        slider.valueChanged.connect(changed)
        row.addWidget(slider)

        action = QWidgetAction(menu)
        action.setDefaultWidget(holder)
        menu.addAction(action)

    def _add_source_menu(self, menu, canvas):
        """Which device the visualizer listens to.

        The default is whatever the system calls its default source,
        which on a machine with several outputs may well be a silent
        one - an HDMI monitor nothing is playing through, say. Being
        able to pick the right output's loopback is the difference
        between a visualizer that moves and one that does not.
        """
        source_menu = menu.addMenu("Audio source")
        filled = [_sources]
        self._fill_source_menu(source_menu, canvas, _sources)

        def arrived():
            # Only if the menu is still there and the list changed.
            if sip.isdeleted(source_menu) or sip.isdeleted(canvas):
                return
            if _sources == filled[0]:
                return
            filled[0] = _sources
            source_menu.clear()
            self._fill_source_menu(source_menu, canvas, _sources)
        list_sources_later(arrived)

    def _fill_source_menu(self, source_menu, canvas, sources):
        """The entries of "Audio source", from the last list made."""
        if sources is None:
            waiting = source_menu.addAction("Looking for sources…")
            waiting.setEnabled(False)
            return
        if not sources:
            none_found = source_menu.addAction("No sources found")
            none_found.setEnabled(False)
            return

        group = QActionGroup(source_menu)
        default = QAction("System default", source_menu, checkable=True)
        default.setChecked(canvas.source is None)
        default.triggered.connect(lambda: canvas.set_source(None))
        group.addAction(default)
        source_menu.addAction(default)
        source_menu.addSeparator()

        for pretty, name in sources:
            action = QAction(pretty, source_menu, checkable=True)
            action.setChecked(canvas.source == name)
            action.triggered.connect(
                lambda _checked, n=name: canvas.set_source(n)
            )
            group.addAction(action)
            source_menu.addAction(action)

    def _add_preset_menu(self, menu, canvas):
        """One entry that opens the browser.

        The list used to be submenus of twenty-five, which for four
        thousand presets is a hundred and seventy submenus - navigable
        in principle and useless in practice. A window with a search box
        is the right shape for a list that long.
        """
        count = len(canvas.presets)
        if not count:
            action = menu.addAction("No presets installed")
            action.setEnabled(False)
            return
        menu.addAction(f"Presets ({count})...", self._open_preset_browser)

    def _open_preset_browser(self):
        if self.canvas is None:
            return
        if self._browser is None:
            self._browser = PresetBrowser(self.canvas, self)
        self._browser.show()
        self._browser.raise_()
        self._browser.activateWindow()

    def closeEvent(self, event):
        if self._closed:
            super().closeEvent(event)
            return
        self._closed = True
        if self.canvas is not None:
            self.canvas.shutdown()
        if self._browser is not None:
            self._browser.close()
        if self in _open_windows:
            _open_windows.remove(self)
        _vlog(f"window #{self.number} closed: "
              f"{'projectM and its audio tap stopped' if self.canvas is not None else 'nothing was running'}"
              f" — {_counts()}")
        super().closeEvent(event)


def opengl_available():
    """(usable, message) - can this system give us a 3.3 core context?

    Tested on a throwaway QOffscreenSurface rather than by creating the
    real widget, because a QOpenGLWidget with no context is worse than
    useless: it never paints, so there is nothing to put an error
    message on.
    """
    from PyQt5.QtGui import QOffscreenSurface, QOpenGLContext

    surface = QOffscreenSurface()
    surface.setFormat(projectm_surface_format())
    surface.create()

    context = QOpenGLContext()
    context.setFormat(projectm_surface_format())
    created = context.create()
    log.info("probe context created=%s", created)
    if not created:
        return False, (
            "This system could not provide an OpenGL context.\n\n"
            "Inside an AppImage this usually means Qt's GL integration "
            "plugins are missing. Outside one, check the graphics "
            "driver with:  glxinfo | grep \"OpenGL core\""
        )

    got = context.format()
    log.info("probe context is OpenGL %d.%d %s",
             got.majorVersion(), got.minorVersion(),
             "core" if got.profile() == QSurfaceFormat.CoreProfile
             else "compatibility")
    if (got.majorVersion(), got.minorVersion()) < (3, 3):
        return False, (
            f"The best OpenGL context available is "
            f"{got.majorVersion()}.{got.minorVersion()}, "
            "but projectM needs 3.3 or better."
        )
    return True, "OpenGL is available."


def projectm_surface_format():
    """A context projectM will accept.

    Qt hands out an OpenGL 2.1 compatibility context by default, and
    projectM 4 needs 3.3 core - its shaders are GLSL 330. Given a 2.1
    context projectm_create() simply returns NULL, which is what an
    empty white window meant: the widget existed, projectM never
    started, and paintGL had nothing to draw.
    """
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
    return fmt


class PresetBrowser(QWidget):
    """A searchable list of presets.

    Selecting one loads it immediately - there is no OK button, because
    the whole point is to hear and see the change while browsing.
    """

    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.setWindowTitle("Visualizer presets")
        self.setWindowFlags(Qt.Window)
        self.resize(420, 520)

        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search presets...")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.list = QListWidget(self)
        self.list.currentRowChanged.connect(self._chose)
        self.list.itemChanged.connect(self._toggled)
        layout.addWidget(self.list)

        hint = QLabel("Tick to enable. Unticked presets are skipped.", self)
        layout.addWidget(hint)

        self._shown = []
        self._filter("")

    def _filter(self, text):
        needle = text.strip().lower()
        self._shown = [
            (name, path) for name, path in self.canvas.presets
            if not needle or needle in name.lower()
        ]
        # Blocked while refilling: setting the rows would otherwise fire
        # currentRowChanged and load whatever happened to land first.
        self.list.blockSignals(True)
        self.list.clear()
        current = self.canvas.current_preset
        for row, (name, path) in enumerate(self._shown):
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Unchecked if path in self.canvas.disabled else Qt.Checked
            )
            self.list.addItem(item)
            if path == current:
                self.list.setCurrentRow(row)
        self.list.blockSignals(False)

    def _toggled(self, item):
        row = self.list.row(item)
        if 0 <= row < len(self._shown):
            self.canvas.set_disabled(self._shown[row][1],
                                     item.checkState() == Qt.Unchecked)

    def _chose(self, row):
        if 0 <= row < len(self._shown):
            self.canvas.load_preset(self._shown[row][1])


class _ProjectMCanvas(QOpenGLWindow):
    """projectM rendering into a real OpenGL window.

    A QOpenGLWindow, not a QOpenGLWidget, and that distinction is the
    whole reason this took so long to see. A QOpenGLWidget renders into
    an FBO of its own and composites it; projectM's render call draws to
    framebuffer 0. So every frame went to a buffer the widget never
    displayed - projectM reported success, the log said "first frame
    rendered", and the window stayed empty.

    projectM did add an explicit render-to-FBO call for exactly this
    problem, but it is not in any released version yet. A QOpenGLWindow
    genuinely owns framebuffer 0, so the plain call lands where it
    should and no unreleased code is needed.
    """

    # Emitted when projectM cannot start, with the reason.
    failed = pyqtSignal(str)
    # Right-click, forwarded up. A QOpenGLWindow is a native window, so
    # clicks on it never reach the widget behind it - setting a context
    # menu policy on the container did nothing at all.
    menu_requested = pyqtSignal()
    # So the chosen source and gain can be remembered between runs.
    source_changed = pyqtSignal(str)
    gain_changed = pyqtSignal(float)
    disabled_changed = pyqtSignal(list)
    fullscreen_requested = pyqtSignal()
    key_pressed = pyqtSignal(int, int)

    def __init__(self, number=0):
        super().__init__()
        self.setFormat(projectm_surface_format())
        self.number = number
        # Set once shutdown() has run: nothing is drawn after that.
        self._closed = False
        self._expose_logged = False
        self._update_logged = False
        self._pm = None
        self._tap = None
        # Samples arrive on the capture thread and are consumed on the
        # GL thread. One slot, newest wins: if rendering falls behind,
        # showing the latest audio is better than working through a
        # backlog the music has already moved past.
        self._pending = None
        self._pending_lock = threading.Lock()
        self.presets = projectm.list_presets()
        self.current_preset = None
        self.fps = DEFAULT_FPS
        self.shuffling = True
        self.listening = False
        self.source = None
        self.gain = 1.0
        # Presets the user has switched off. Kept as full paths so the
        # set survives presets being added or removed.
        self.disabled = set()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)

        self._shuffle_timer = QTimer(self)
        self._shuffle_timer.setInterval(SHUFFLE_SECONDS * 1000)
        self._shuffle_timer.timeout.connect(self.random_preset)

        # A backstop for the case where initializeGL never runs at all.
        #
        # If the driver cannot give us a 3.3 core context, Qt fails to
        # create one and simply never calls initializeGL - so nothing
        # raises, nothing is reported, and the window sits there blank.
        # Checking shortly after showing catches that.
        self._started = False
        self._logged_frame = False
        self._logged_paint_error = False
        self._clear_fns = None
        QTimer.singleShot(1500, self._check_started)

    def _check_started(self):
        if self._closed or self._started or self._pm is not None:
            return
        context = self.context()
        if context is None or not context.isValid():
            self.failed.emit(
                "Qt could not create an OpenGL 3.3 core context.\n\n"
                "This usually means the graphics driver does not offer "
                "one - check with:  glxinfo | grep \"OpenGL core\""
            )

    def mousePressEvent(self, event):
        # A click on the picture closes an open menu first (build 70,
        # task 70). This is a native window, not a widget, so Qt's own
        # "click outside closes the menu" never sees the click, and on
        # Wayland the desktop hands clicks on the app's own windows to
        # the app. The menu used to grab the mouse for this, which broke
        # its submenus on Wayland (see VisualizerWindow._show_menu).
        from PyQt5.QtWidgets import QApplication
        if QApplication.activePopupWidget() is not None:
            for _ in range(10):
                popup = QApplication.activePopupWidget()
                if popup is None:
                    break
                popup.close()
            return
        if event.button() == Qt.RightButton:
            self.menu_requested.emit()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Forwarded up. The GL window is native, so a double-click on it
        # never reaches the widget behind - which is why double-clicking
        # the visualizer did nothing.
        self.fullscreen_requested.emit()

    def keyPressEvent(self, event):
        # Same reason: the GL window has focus, so it sees the keys.
        self.key_pressed.emit(event.key(), int(event.modifiers()))

    # -- GL lifecycle --------------------------------------------------

    def exposeEvent(self, event):
        """Not drawn once closed (build 68, task 33).

        The crash in task 33 was Qt drawing this window for the desktop
        (an expose, sent when the window is moved, resized or shown)
        while its OpenGL context was gone. Once shutdown() has run there
        is nothing to draw, so the expose is let go.
        """
        if self._closed:
            if not self._expose_logged:
                self._expose_logged = True
                _vlog(f"window #{self.number}: the desktop asked to redraw "
                      "it after it was closed — skipped")
            return
        super().exposeEvent(event)

    def event(self, event):
        """No drawing at all once closed (build 70, task 68).

        The expose guard above was not enough: closing from the Winamp
        view's V button, the desktop still delivered an update request
        (its "draw your next frame now") that was asked for before the
        close. Qt's own handler for it starts a frame on the closed
        window's OpenGL context — the segfault in
        `QOpenGLContext::isValid` ← `QOpenGLWindowPrivate::beginPaint` ←
        `QPaintDeviceWindow::event`. Reproduced here by sending one
        update request to a just-closed window: build 69 crashed at once.
        """
        if self._closed and event.type() in (QEvent.UpdateRequest,
                                             QEvent.Paint):
            if not self._update_logged:
                self._update_logged = True
                _vlog(f"window #{self.number}: a frame was asked for after "
                      "it was closed — skipped (the task 68 crash)")
            return True
        return super().event(event)

    def initializeGL(self):
        # Built here, not in __init__: projectM needs a current GL
        # context to exist before it will start, and there isn't one
        # until Qt calls this.
        surface = self.format()
        log.info("initializeGL: widget context is OpenGL %d.%d %s, size %dx%d",
                 surface.majorVersion(), surface.minorVersion(),
                 "core" if surface.profile() == QSurfaceFormat.CoreProfile
                 else "compatibility",
                 self.width(), self.height())
        try:
            self._pm = projectm.ProjectM(self.width(), self.height())
            log.info("projectM started, %d presets available",
                     len(self.presets))
        except Exception as error:      # noqa: BLE001
            log.exception("projectM failed to start")
            self._pm = None
            # Say what happened, and what context it was given. Failing
            # silently left a blank white window with no way to tell
            # whether it was broken or just quiet.
            self.failed.emit(
                f"{error}\n\nThe context Qt provided was OpenGL "
                f"{surface.majorVersion()}.{surface.minorVersion()} "
                f"{'core' if surface.profile() == QSurfaceFormat.CoreProfile else 'compatibility'}"
                f".\nprojectM 4 needs 3.3 core or better."
            )
            return
        self._started = True
        if self.presets:
            self.random_preset()
        self.set_fps(self.fps)
        if self.shuffling:
            self._shuffle_timer.start()

        # Start listening. If no capture tool is installed this simply
        # does not start, and projectM renders against silence as before.
        self._start_tap()

    def _start_tap(self):
        if self._closed:
            return
        if self._tap is not None:
            self._tap.stop()
        self._tap = audiotap.AudioTap(self._collect_samples, self.source)
        if not self._tap.start():
            self._tap = None
        self.listening = self._tap is not None
        log.info("audio tap on %s: %s",
                 self.source or "(system default)",
                 "listening" if self.listening else "not started")
        _vlog(f"window #{self.number}: audio tap "
              f"{'started' if self.listening else 'not started'} on "
              f"{self.source or 'the system default'} — {_counts()}")

    def set_gain(self, gain):
        """How hard the incoming audio is driven, for projectM too."""
        self.gain = max(0.1, float(gain))
        self.gain_changed.emit(self.gain)

    def set_source(self, name):
        """Listen to a different device."""
        self.source = name
        self.source_changed.emit(name or "")
        _vlog(f"window #{self.number}: Audio source chosen: "
              f"{name or 'System default'}")
        self._start_tap()

    def _collect_samples(self, samples):
        """Called on the capture thread - store and return."""
        with self._pending_lock:
            self._pending = samples

    def resizeGL(self, width, height):
        # Scaled to real pixels.
        #
        # On a HiDPI screen the width Qt reports here is in logical
        # units while the framebuffer is larger, so projectM was being
        # told a size that did not match what it was drawing into.
        ratio = self.devicePixelRatio() or 1
        if self._pm is not None:
            self._pm.resize(int(width * ratio), int(height * ratio))

    def _clear(self):
        """Paint the buffer black before projectM draws.

        PyQt5 has no QOpenGLContext.functions() - that is the C++ API,
        and calling it raised on the first line of every frame, so
        render() was never reached and the window stayed blank. PyQt5
        offers versionFunctions() instead, which can still return None
        if the bindings for this exact profile were not built, hence
        the guard.
        """
        if self._clear_fns is False:
            return
        if self._clear_fns is None:
            try:
                functions = self.context().versionFunctions()
                if functions is not None:
                    functions.initializeOpenGLFunctions()
                self._clear_fns = functions or False
            except Exception:           # noqa: BLE001
                self._clear_fns = False
                return
        if self._clear_fns:
            self._clear_fns.glClearColor(0.0, 0.0, 0.0, 1.0)
            self._clear_fns.glClear(0x00004000 | 0x00000100)

    def paintGL(self):
        # Nothing in here may raise.
        #
        # An exception in paintGL is printed once per frame - sixty
        # times a second - and stops anything after it running, which
        # is how one wrong API call turned into a blank window and a
        # wall of identical tracebacks.
        try:
            if self._closed:
                return
            self._clear()

            if self._pm is None:
                return

            with self._pending_lock:
                samples, self._pending = self._pending, None
            if samples:
                if self.gain != 1.0:
                    import spectrum
                    samples = spectrum.apply_gain(samples, self.gain)
                self._pm.add_audio(samples)

            # Into the widget's own framebuffer, not the window's.
            # Framebuffer 0 for a QOpenGLWindow, which is what
            # projectM draws to anyway; the argument is kept so a
            # future projectM with the FBO API can be handed a target.
            self._pm.render(self.defaultFramebufferObject())
            if not self._logged_frame:
                self._logged_frame = True
                log.info("first frame rendered into fbo %d",
                         self.defaultFramebufferObject())
        except Exception:               # noqa: BLE001
            if not self._logged_paint_error:
                self._logged_paint_error = True
                log.exception("painting the visualizer failed")
                self._timer.stop()

    # -- what the menu drives ------------------------------------------

    def set_fps(self, fps):
        """60 by default - smooth enough that motion reads as motion."""
        self.fps = int(fps)
        self._timer.setInterval(max(1, round(1000 / self.fps)))
        self._timer.start()

    def set_shuffling(self, on):
        self.shuffling = bool(on)
        if self.shuffling:
            self._shuffle_timer.start()
        else:
            self._shuffle_timer.stop()

    def load_preset(self, path):
        if self._pm is None or self._closed:
            return
        self.makeCurrent()
        try:
            self._pm.load_preset(path)
            self.current_preset = path
        except Exception:               # noqa: BLE001
            pass
        self.doneCurrent()

    def enabled_presets(self):
        """Everything not switched off, falling back to everything.

        If every preset is disabled the list is ignored rather than
        leaving nothing to show - an empty visualizer is a worse
        outcome than honouring a setting literally.
        """
        live = [item for item in self.presets if item[1] not in self.disabled]
        return live or self.presets

    def set_disabled(self, path, off):
        self.disabled = set(self.disabled)
        if off:
            self.disabled.add(path)
        else:
            self.disabled.discard(path)
        self.disabled_changed.emit(sorted(self.disabled))
        # Move on if the one showing was just switched off.
        if off and path == self.current_preset:
            self.random_preset()

    def _step(self, delta):
        if not self.presets:
            return
        paths = [path for _name, path in self.enabled_presets()]
        try:
            index = paths.index(self.current_preset)
        except ValueError:
            index = 0
        self.load_preset(paths[(index + delta) % len(paths)])

    def next_preset(self):
        self._step(1)

    def previous_preset(self):
        self._step(-1)

    def random_preset(self):
        live = self.enabled_presets()
        if live:
            self.load_preset(random.choice(live)[1])

    def shutdown(self):
        """Stop drawing, listening and projectM. Safe to call twice."""
        self._closed = True
        self._timer.stop()
        self._shuffle_timer.stop()
        if self._tap is not None:
            self._tap.stop()
            self._tap = None
        self.listening = False
        if self._pm is not None:
            self.makeCurrent()
            self._pm.close()
            self._pm = None
            self.doneCurrent()
