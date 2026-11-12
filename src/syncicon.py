"""Frequencies — the spinning sync icon (build 167, task 223).

Two arrows chasing each other round a circle. It turns while a sync is
running (`sync.SyncService.busy`) and stands still, or goes away, when it
is done:

  * `attach_button` puts it on Settings' Sync now button;
  * `SyncBadge` is the small one in the player bar of the main window,
    shown only while a sync runs.
"""

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PyQt5.QtWidgets import QToolButton

FRAME_MS = 40
STEP_DEGREES = 12


def pixmap(size, color, angle=0.0, ratio=1.0):
    """The icon at `size` px, turned `angle` degrees clockwise."""
    px = max(1, int(round(size * ratio)))
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    u = size / 24.0
    p.translate(size / 2.0, size / 2.0)
    p.rotate(angle)
    c = QColor(color)
    pen = QPen(c, max(1.4, 1.9 * u), Qt.SolidLine, Qt.RoundCap,
               Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    r = 7.5 * u
    box = QRectF(-r, -r, 2 * r, 2 * r)
    # Qt angles: counter-clockwise from 3 o'clock, in 1/16 degree.
    for start in (20, 200):
        p.drawArc(box, start * 16, 130 * 16)
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    # An arrowhead at the end of each arc, pointing on round.
    for end in (150, 330):
        p.save()
        p.rotate(-end)
        head = QPolygonF([QPointF(r - 3.2 * u, 0.4 * u),
                          QPointF(r + 3.2 * u, 0.4 * u),
                          QPointF(r, -3.6 * u)])
        p.drawPolygon(head)
        p.restore()
    p.end()
    return pm


class _Turner:
    """A timer that turns an angle while asked to."""

    def __init__(self, owner, redraw):
        self.angle = 0.0
        self._redraw = redraw
        self.timer = QTimer(owner)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self._step)

    def run(self, on):
        if on and not self.timer.isActive():
            self.timer.start()
        elif not on and self.timer.isActive():
            self.timer.stop()
            self.angle = 0.0
        self._redraw()

    def _step(self):
        self.angle = (self.angle + STEP_DEGREES) % 360
        self._redraw()


def _ratio(widget):
    try:
        return float(widget.devicePixelRatioF())
    except Exception:                           # noqa: BLE001
        return 1.0


def attach_button(button, svc, color, size=16):
    """Give a push button the sync icon; it turns while `svc` is busy.
    `color` is a callable returning the color to draw in."""
    button.setIconSize(QSize(size, size))

    def redraw():
        button.setIcon(QIcon(pixmap(size, color(), turner.angle,
                                    _ratio(button))))

    turner = _Turner(button, redraw)

    def follow():
        busy = bool(svc is not None and svc.busy)
        turner.run(busy)

    if svc is not None:
        svc.activity.connect(follow)
    follow()
    return follow


class SyncBadge(QToolButton):
    """The main window's small sync icon: only there while a sync runs.
    Clicking it opens Settings at the sync section (`clicked`)."""

    def __init__(self, color, size=18, parent=None):
        super().__init__(parent)
        self._color = color
        self._size = size
        self._svc = None
        self.setAutoRaise(True)
        self.setIconSize(QSize(size, size))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._turner = _Turner(self, self._redraw)
        self.hide()

    def watch(self, svc):
        self._svc = svc
        if svc is not None:
            svc.activity.connect(self._follow)
        self._follow()

    def _follow(self):
        svc = self._svc
        busy = bool(svc is not None and svc.busy)
        if busy:
            self.setToolTip(svc.busy_text())
        self.setVisible(busy)
        self._turner.run(busy)

    def _redraw(self):
        self.setIcon(QIcon(pixmap(self._size, self._color(),
                                  self._turner.angle, _ratio(self))))
