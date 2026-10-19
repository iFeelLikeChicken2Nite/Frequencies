"""Frequencies — windows that open and close the way the desktop's do.

KWin's Scale effect grows a window from 80% while fading it in, and
reverses that on close: 250ms out, 200ms back, starting and ending at
0.80. Those are the numbers from the compositor's own settings page, and
matching them is the point — a dialog that animates differently from
every other window on screen is the one that looks wrong.

The compositor already does this for windows it manages. What it cannot
reach is anything frameless or marked as a popup, and an app that mixes
both ends up with some windows animating and some appearing instantly.
Doing it here covers all of them the same way.

Installed once, on the application:

    import windowfx
    windowfx.install(app)

after which every dialog the app opens is animated, with no change at
the call sites.
"""

from PyQt5.QtCore import (QEasingCurve, QEvent, QObject,
                          QVariantAnimation, QParallelAnimationGroup,
                          QPropertyAnimation, QRect, QTimer, Qt)
from PyQt5.QtWidgets import (QApplication, QDialog, QGraphicsOpacityEffect,
                             QWidget)

# KWin's defaults, as shown in System Settings → Desktop Effects → Scale.
OPEN_MS = 250
CLOSE_MS = 200
OPEN_FROM = 0.80
CLOSE_TO = 0.80

# Windows that should never be animated: menus and tooltips have their
# own behaviour, and a window being dragged is not opening.
SKIP_FLAGS = (Qt.ToolTip, Qt.SplashScreen)


def _scaled(rect, factor):
    """The same rectangle, smaller, about its own centre."""
    width = max(1, int(rect.width() * factor))
    height = max(1, int(rect.height() * factor))
    return QRect(rect.center().x() - width // 2,
                 rect.center().y() - height // 2,
                 width, height)


def _content_of(window):
    """The widget the animation can actually move.

    On Wayland a client does not control its own window: it cannot place
    it, cannot resize it mid-map, and windowOpacity does nothing — the
    log says so outright, "this plugin does not support setting window
    opacity". So animating the frame produced a dialog that appeared at
    full size and then twitched.

    What a client *can* animate is everything inside the frame. Insetting
    the layout and fading the contents reads as the window growing into
    place, and works the same on X11 and Wayland.
    """
    layout = window.layout()
    return layout if layout is not None else None


def animate_open(window, duration=OPEN_MS, start=OPEN_FROM):
    """Grow a window's contents into place as it appears."""
    layout = _content_of(window)
    if window is None or layout is None:
        return None

    kept = layout.contentsMargins()
    inset_x = int(window.width() * (1 - start) / 2)
    inset_y = int(window.height() * (1 - start) / 2)

    effect = QGraphicsOpacityEffect(window)
    window.setGraphicsEffect(effect)

    group = QParallelAnimationGroup(window)

    fade = QPropertyAnimation(effect, b"opacity", window)
    fade.setDuration(duration)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setEasingCurve(QEasingCurve.OutCubic)
    group.addAnimation(fade)

    squeeze = QVariantAnimation(window)
    squeeze.setDuration(duration)
    squeeze.setStartValue(1.0)
    squeeze.setEndValue(0.0)
    squeeze.setEasingCurve(QEasingCurve.OutCubic)

    def step(value):
        try:
            layout.setContentsMargins(
                kept.left() + int(inset_x * value),
                kept.top() + int(inset_y * value),
                kept.right() + int(inset_x * value),
                kept.bottom() + int(inset_y * value))
        except RuntimeError:
            pass

    squeeze.valueChanged.connect(step)
    group.addAnimation(squeeze)

    def restore():
        try:
            layout.setContentsMargins(kept)
            window.setGraphicsEffect(None)
        except RuntimeError:
            pass

    group.finished.connect(restore)
    step(1.0)
    effect.setOpacity(0.0)
    group.start(QParallelAnimationGroup.DeleteWhenStopped)
    return group


def _old_animate_open(window, duration=OPEN_MS, start=OPEN_FROM):
    """The frame-based version, kept for reference on X11-only setups."""
    if window is None or not window.isWindow():
        return None

    end = QRect(window.geometry())
    if end.isEmpty():
        return None

    # A minimum size would clamp the small starting geometry.
    #
    # Most of these dialogs set one, and Qt refuses to make a window
    # smaller than it — so setGeometry did nothing and the window simply
    # appeared. The minimum is lifted for the length of the animation and
    # put back at the end, where it matters.
    was_min = window.minimumSize()
    window.setMinimumSize(0, 0)

    group = QParallelAnimationGroup(window)

    grow = QPropertyAnimation(window, b"geometry", window)
    grow.setDuration(duration)
    grow.setStartValue(_scaled(end, start))
    grow.setEndValue(end)
    grow.setEasingCurve(QEasingCurve.OutCubic)
    group.addAnimation(grow)

    fade = QPropertyAnimation(window, b"windowOpacity", window)
    fade.setDuration(duration)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setEasingCurve(QEasingCurve.OutCubic)
    group.addAnimation(fade)

    def restore():
        try:
            window.setMinimumSize(was_min)
            window.setGeometry(end)
        except RuntimeError:
            pass

    group.finished.connect(restore)
    window.setGeometry(_scaled(end, start))
    window.setWindowOpacity(0.0)
    group.start(QParallelAnimationGroup.DeleteWhenStopped)
    return group


def animate_close(window, then=None, duration=CLOSE_MS, end_at=CLOSE_TO):
    """Shrink a window's contents away, then do `then`.

    The mirror of animate_open, and for the same reason: the frame is
    the compositor's to move, the contents are ours.
    """
    layout = _content_of(window)
    if window is None or layout is None:
        if then:
            then()
        return None

    kept = layout.contentsMargins()
    inset_x = int(window.width() * (1 - end_at) / 2)
    inset_y = int(window.height() * (1 - end_at) / 2)

    effect = QGraphicsOpacityEffect(window)
    window.setGraphicsEffect(effect)

    group = QParallelAnimationGroup(window)

    fade = QPropertyAnimation(effect, b"opacity", window)
    fade.setDuration(duration)
    fade.setStartValue(1.0)
    fade.setEndValue(0.0)
    fade.setEasingCurve(QEasingCurve.InCubic)
    group.addAnimation(fade)

    squeeze = QVariantAnimation(window)
    squeeze.setDuration(duration)
    squeeze.setStartValue(0.0)
    squeeze.setEndValue(1.0)
    squeeze.setEasingCurve(QEasingCurve.InCubic)

    def step(value):
        try:
            layout.setContentsMargins(
                kept.left() + int(inset_x * value),
                kept.top() + int(inset_y * value),
                kept.right() + int(inset_x * value),
                kept.bottom() + int(inset_y * value))
        except RuntimeError:
            pass

    squeeze.valueChanged.connect(step)
    group.addAnimation(squeeze)

    def done():
        try:
            layout.setContentsMargins(kept)
            window.setGraphicsEffect(None)
        except RuntimeError:
            pass
        if then:
            then()

    group.finished.connect(done)
    group.start(QParallelAnimationGroup.DeleteWhenStopped)
    return group


class _Watcher(QObject):
    """Animates every window the application shows.

    An event filter rather than a base class, because the app opens
    dialogs from a dozen places and half of them are Qt's own —
    QFileDialog, QMessageBox, QInputDialog. Subclassing would reach none
    of those.
    """

    def eventFilter(self, obj, event):
        if not isinstance(obj, QWidget):
            return super().eventFilter(obj, event)

        if event.type() == QEvent.Show and self._wanted(obj):
            # After the show has been processed: animating during it
            # sets a geometry the window manager then overrides with
            # its own placement.
            QTimer.singleShot(0, lambda w=obj: self._open(w))

        elif event.type() == QEvent.Close and self._wanted(obj):
            # The window has to outlive its own animation.
            #
            # So the first close is refused, the shrink runs, and the
            # window closes itself when it finishes — marked, so the
            # second close goes straight through instead of starting
            # another animation.
            if obj.property("fx_closing"):
                return super().eventFilter(obj, event)
            obj.setProperty("fx_closing", True)
            event.ignore()
            animate_close(obj, then=lambda w=obj: self._finish(w))
            return True

        return super().eventFilter(obj, event)

    @staticmethod
    def _finish(widget):
        try:
            widget.setWindowOpacity(1.0)
            widget.hide()
            # Ready to animate again next time it is opened.
            widget.setProperty("fx_closing", False)
        except RuntimeError:
            pass                # already gone

    @staticmethod
    def _wanted(widget):
        if not widget.isWindow() or widget.property("no_window_fx"):
            return False
        kind = widget.windowType()
        if kind in SKIP_FLAGS or kind == Qt.Popup:
            return False
        # The main window is the desktop's to animate; it is a real
        # window with a frame, and KWin already has it.
        return isinstance(widget, QDialog)

    @staticmethod
    def _open(widget):
        try:
            if not widget.isVisible():
                return
            animate_open(widget)
            _Watcher._wrap_done(widget)
        except RuntimeError:
            pass                # closed again before the timer fired

    @staticmethod
    def _wrap_done(dialog):
        """Make accept() and reject() animate before they hide.

        A filter cannot stop a hide — setVisible(False) delivers the
        event and carries on regardless of what the filter returns, which
        is why catching Hide did nothing. done() is the one place every
        dialog result goes through: accept, reject, and the buttons that
        call them. Wrapping it is the only hook that covers all of them.
        """
        if getattr(dialog, "_fx_wrapped", False):
            return
        dialog._fx_wrapped = True
        original = dialog.done

        def done(result):
            if dialog.property("fx_closing") or not dialog.isVisible():
                original(result)
                return
            dialog.setProperty("fx_closing", True)
            animate_close(dialog, then=lambda: _Watcher._finish_done(
                dialog, original, result))

        dialog.done = done

    @staticmethod
    def _finish_done(dialog, original, result):
        try:
            dialog.setWindowOpacity(1.0)
            dialog.setProperty("fx_closing", False)
            original(result)
        except RuntimeError:
            pass


_watcher = None


def install(app=None):
    """Start animating this application's dialogs."""
    global _watcher
    if _watcher is not None:
        return _watcher
    app = app or QApplication.instance()
    if app is None:
        return None
    _watcher = _Watcher(app)
    app.installEventFilter(_watcher)
    return _watcher
