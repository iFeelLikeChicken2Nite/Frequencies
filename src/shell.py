"""Frequencies — application shell and player bar.

Three zones under a persistent bottom bar:

    +--------+---------------------------+-----------+
    | nav    | content stack             | context   |
    | tags   |                           | panel     |
    +--------+---------------------------+-----------+
    |  art / track | transport | actions / volume    |
    +----------------------------------------------+

Run this file directly to see the shell on its own.
"""

import math
import os
import threading
import sys
import time

from PyQt5.QtCore import (QPointF, QVariantAnimation,QEasingCurve, QEvent, QParallelAnimationGroup, QPoint,
                          QPointF, pyqtProperty,
                          QPropertyAnimation, QRect, QRectF, QSize, Qt,
                          QTimer, pyqtSignal)
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QIcon,
                         QLinearGradient,
                         QPainter, QPainterPath, QPen, QPixmap, QPolygonF)
from PyQt5.QtWidgets import (QGraphicsOpacityEffect,QAbstractItemView, QApplication, QButtonGroup,
                             QMenu, QScrollArea, QSplitter, QStyle, QStyleOptionFrame,
                             QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMainWindow,
                             QPushButton, QSizePolicy, QSlider,
                             QStackedWidget, QToolButton, QVBoxLayout,
                             QWidget)

import theme
from jobs import Job
from nowplaying import NowPlaying
from theme import T


# --------------------------------------------------------------------------
# Icons, painted rather than loaded. QtSvg gets stripped by trim_pyqt.py and
# shipping PNGs means shipping them at four scale factors.
# --------------------------------------------------------------------------

# Any glyph can be replaced by dropping a file next to the code, so a
# custom icon needs no edit to this file. Looked up once per name and
# cached, because icon() is called on every repaint of the player bar.
ICON_EXTS = (".svg", ".png")
_icon_files = {}


def _icon_dirs():
    here = os.path.dirname(os.path.abspath(__file__))
    return (os.path.join(here, "icons"),
            os.path.join(here, "..", "icons"),
            os.path.join(os.path.expanduser("~"), ".config", "frequencies",
                         "icons"))


def icon_file(name):
    """A replacement image for this glyph, or "" if there isn't one."""
    if name in _icon_files:
        return _icon_files[name]
    found = ""
    for folder in _icon_dirs():
        for ext in ICON_EXTS:
            path = os.path.join(folder, name + ext)
            if os.path.exists(path):
                found = path
                break
        if found:
            break
    _icon_files[name] = found
    return found


# Each replacement icon file, decoded once (task 54).
#
# The icon cache below is keyed by colour, and the accent colour changes
# with every station's art — so a station change asked for the same file
# in a new colour and decoded it again, on the GUI thread, right while
# the artwork scaler decoded the new cover. That is where the app aborted
# (build 63 log: media-key Next → set_shazam → _from_file → QPixmap(path)).
# Now the file is decoded once, under the image lock, and only re-tinted
# after that. Re-read only if the file changes.
_icon_sources = {}
# How many times an icon file was read from disk this session.
icon_file_reads = 0


def _icon_source(path):
    """The decoded picture in `path` (a QPixmap), or None. GUI thread."""
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        stamp = None
    hit = _icon_sources.get(path)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    import imagelock
    global icon_file_reads
    icon_file_reads += 1        # for tests and the curious (task 58)
    pm = imagelock.pixmap(path)
    found = None if pm.isNull() else pm
    _icon_sources[path] = (stamp, found)
    return found


def _from_file(path, size, color):
    """Load a replacement and tint it to the requested color.

    Tinted rather than drawn as-is so a custom icon still goes accent
    colored when it is active and grey when it is not, like every icon
    beside it. A fully transparent source would come back blank, so an
    image with no alpha is used unchanged.
    """
    source = _icon_source(path)
    if source is None:
        return None
    source = source.scaled(size, size, Qt.KeepAspectRatio,
                           Qt.SmoothTransformation)
    if not source.hasAlphaChannel():
        return QIcon(source)
    out = QPixmap(source.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, source)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(out.rect(), QColor(color))
    p.end()
    return QIcon(out)


# Icons, once each.
#
# icon() was decoding a PNG from disk on every call, and it is called
# from paint handlers — so the GUI thread was running an image decoder
# while the artwork thread was running another one, and Qt's image
# machinery is not re-entrant enough to survive that. It aborted the
# process. Decoding once per name, size and colour removes the race and
# the repeated work together.
_icon_cache = {}
_ICON_CACHE_MAX = 400


def icon(name: str, size: int = 20, color: str = None) -> QIcon:
    color = color or T["text"]
    key = (name, int(size), str(color))
    hit = _icon_cache.get(key)
    if hit is not None:
        return hit
    made = _make_icon(name, size, color)
    if len(_icon_cache) > _ICON_CACHE_MAX:
        _icon_cache.clear()
    _icon_cache[key] = made
    return made


def forget_icons():
    """Drop the tinted icons — the accent colour or the icon folder
    changed.

    Only the tinted copies (task 58). The decoded files stay: emptying
    them too (build 65) meant every icon file was read from disk again
    on the window's thread at each station change, right while the
    artwork scaler decoded the new cover — the window could wait for it
    behind the image lock. A file that changed on disk is still read
    again: _icon_source compares its time stamp.
    """
    _icon_cache.clear()


def _make_icon(name: str, size: int = 20, color: str = None) -> QIcon:
    color = color or T["text"]
    # An embedded image first, then a file, then the drawn version.
    #
    # Embedded because a loose file has to survive packaging and this one
    # did not — the build copied only *.py, so a corrected icon changed
    # nothing on the machine it mattered on.
    try:
        import glyphs
        built_in = glyphs.embedded(name, size, color)
        if built_in is not None:
            return QIcon(built_in)
    except Exception:                           # noqa: BLE001
        pass
    replacement = icon_file(name)
    if replacement:
        custom = _from_file(replacement, size, color)
        if custom is not None:
            return custom
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(color)
    pen = QPen(c, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    u = size / 24.0          # icons are drawn on a 24-unit grid

    def poly(*pts):
        return QPolygonF([QPoint(int(x * u), int(y * u)) for x, y in pts])

    if name == "play":
        p.setBrush(c); p.setPen(Qt.NoPen)
        p.drawPolygon(poly((8, 5), (19, 12), (8, 19)))
    elif name.startswith("playpause"):
        # playpause0 is the triangle, playpause4 the two bars.
        #
        # The two right-hand points converge on the vertical centre at 0
        # and separate to full height at 1 — that convergence is what
        # makes it a triangle. Written as an offset from the top and
        # bottom instead, both points sat at the edges and the "triangle"
        # came out a rectangle.
        tail = name[9:]
        f = (int(tail) / 4.0) if tail.isdigit() else 0.0
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        top, bottom, middle = 5.0, 19.0, 12.0
        spread = (bottom - top) / 2 * f
        tip = 19.0 - 7.5 * f
        p.drawPolygon(poly((7.0, top), (7.0, bottom),
                           (tip, middle + spread), (tip, middle - spread)))
        if f > 0.05:
            x = 13.6
            p.drawPolygon(poly((x, middle - spread), (x + 4.4 * f,
                               middle - spread),
                               (x + 4.4 * f, middle + spread),
                               (x, middle + spread)))
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
    elif name == "pause":
        p.setBrush(c); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(7 * u, 5 * u, 3.5 * u, 14 * u), 1.5, 1.5)
        p.drawRoundedRect(QRectF(13.5 * u, 5 * u, 3.5 * u, 14 * u), 1.5, 1.5)
    elif name == "stop":
        p.setBrush(c); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(6.5 * u, 6.5 * u, 11 * u, 11 * u), 2, 2)
    elif name.startswith("next_shuffle"):
        # Built from the plain Next arrow, so it weighs the same as Prev.
        #
        # The crossing arrows were drawn as thin outlines while Prev is a
        # solid triangle, so side by side one looked bold and the other
        # sketched. These use the same stroke weight as the bar and solid
        # heads the size of the triangle's point.
        tail = name[12:]
        f = (int(tail) / 4.0) if tail.isdigit() else 1.0

        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(19 * u, 5 * u, 2.4 * u, 14 * u), 1, 1)

        if f < 0.999:
            p.setOpacity(1.0 - f)
            p.drawPolygon(poly((7, 5), (17.5, 12), (7, 19)))
            p.setOpacity(1.0)

        if f > 0.001:
            p.setOpacity(f)
            # Two paths crossing, at the arrow's own weight.
            stroke = QPen(c, 1.9 * u)
            stroke.setCapStyle(Qt.RoundCap)
            stroke.setJoinStyle(Qt.RoundJoin)
            p.setBrush(Qt.NoBrush)
            p.setPen(stroke)
            p.drawPolyline(poly((3, 8), (6.5, 8), (12, 16), (14.5, 16)))
            p.drawPolyline(poly((3, 16), (6.5, 16), (8.8, 12.6)))
            p.drawPolyline(poly((10.6, 10.2), (12, 8), (14.5, 8)))
            # Solid heads, in proportion to the strokes rather than to
            # the triangle: matched to the triangle they overwhelmed it.
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            p.drawPolygon(poly((14, 5), (17.5, 8), (14, 11)))
            p.drawPolygon(poly((14, 13), (17.5, 16), (14, 19)))
            p.setOpacity(1.0)
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
    elif name in ("prev", "next"):
        p.setBrush(c); p.setPen(Qt.NoPen)
        if name == "next":
            p.drawPolygon(poly((6, 5), (16, 12), (6, 19)))
            p.drawRoundedRect(QRectF(17 * u, 5 * u, 2.2 * u, 14 * u), 1, 1)
        else:
            p.drawPolygon(poly((18, 5), (8, 12), (18, 19)))
            p.drawRoundedRect(QRectF(4.8 * u, 5 * u, 2.2 * u, 14 * u), 1, 1)
    elif name.startswith("next_shuffle"):
        # next_shuffle0..4: the arrow grows its crossing tail.
        tail = name[12:]
        f = (int(tail) / 4.0) if tail.isdigit() else 1.0
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(poly((6, 5), (16, 12), (6, 19)))
        p.drawRoundedRect(QRectF(16.5 * u, 5 * u, 2.4 * u, 14 * u),
                          1.0 * u, 1.0 * u)
        if f > 0.05:
            p.setPen(QPen(c, 1.6 * u))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(poly((3, 8), (3 + 5 * f, 8),
                                (3 + 9 * f, 16), (3 + 12 * f, 16)))
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
    elif name.startswith("shuffle_"):
        # shuffle_0 (parallel) through shuffle_4 (crossed), so the button
        # can be seen changing its mind rather than swapping pictures.
        tail = name.split("_", 1)[1]
        step = int(tail) if tail.isdigit() else 0
        f = max(0.0, min(1.0, step / 4.0))

        # The two paths start level and swap ends as f goes to 1.
        top_out, bottom_out = 8 + 8 * f, 16 - 8 * f
        p.drawPolyline(poly((3, 8), (8, 8), (15, top_out), (18, top_out)))
        p.drawPolyline(poly((3, 16), (8, 16), (15, bottom_out),
                            (18, bottom_out)))
        p.setBrush(c); p.setPen(Qt.NoPen)
        p.drawPolygon(poly((17, top_out - 3), (21, top_out),
                           (17, top_out + 3)))
        p.drawPolygon(poly((17, bottom_out - 3), (21, bottom_out),
                           (17, bottom_out + 3)))
        p.setBrush(Qt.NoBrush); p.setPen(pen)
    elif name == "shuffle":
        # The same two arrows, crossed. On and off are the same drawing in
        # two states, so the button says what it does rather than needing
        # to be remembered.
        p.drawPolyline(poly((3, 7), (8, 7), (16, 17), (21, 17)))
        p.drawPolyline(poly((3, 17), (8, 17), (11, 13)))
        p.drawPolyline(poly((14, 10), (16, 7), (21, 7)))
        p.setBrush(c); p.setPen(Qt.NoPen)
        p.drawPolygon(poly((19, 4), (23, 7), (19, 10)))
        p.drawPolygon(poly((19, 14), (23, 17), (19, 20)))
    elif name == "star":
        path = QPainterPath()
        pts = [(12, 3.5), (14.6, 9.2), (20.8, 10), (16.3, 14.3),
               (17.5, 20.4), (12, 17.4), (6.5, 20.4), (7.7, 14.3),
               (3.2, 10), (9.4, 9.2)]
        path.addPolygon(poly(*pts))
        path.closeSubpath()
        p.drawPath(path)
    elif name.startswith("star_shine"):
        step = int(name[10:] or 1)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(poly((12, 3.5), (14.6, 9.2), (20.8, 9.9), (16.2, 14.1),
                           (17.5, 20.2), (12, 17.1), (6.5, 20.2),
                           (7.8, 14.1), (3.2, 9.9), (9.4, 9.2)))
        # Rays, thrown further out on each frame and fading as they go.
        ray = QColor(c)
        ray.setAlpha(max(0, 220 - step * 55))
        rp = QPen(ray, 1.6, Qt.SolidLine, Qt.RoundCap)
        p.setPen(rp)
        p.setBrush(Qt.NoBrush)
        import math
        inner = 10.5 + step * 1.1
        outer = inner + 2.4
        for i in range(8):
            a = math.radians(i * 45 + 22)
            p.drawLine(int((12 + math.cos(a) * inner) * u),
                       int((12 + math.sin(a) * inner) * u),
                       int((12 + math.cos(a) * outer) * u),
                       int((12 + math.sin(a) * outer) * u))
    elif name == "star_on":
        path = QPainterPath()
        pts = [(12, 3.5), (14.6, 9.2), (20.8, 10), (16.3, 14.3),
               (17.5, 20.4), (12, 17.4), (6.5, 20.4), (7.7, 14.3),
               (3.2, 10), (9.4, 9.2)]
        path.addPolygon(poly(*pts))
        path.closeSubpath()
        p.fillPath(path, c)
    elif name.startswith("volume_muted"):
        # The cone with a stroke straight through it, top-left to
        # bottom-right — the direction a "no" sign's bar runs. Sitting
        # beside the speaker it read as a stray mark; crossing it is what
        # says the speaker itself is off.
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(poly((4, 9), (8, 9), (12.5, 5), (12.5, 19), (8, 15),
                           (4, 15)))
        p.setBrush(Qt.NoBrush)
        # volume_muted0..4: the stroke is drawn on, top-left first, so
        # muting reads as an action rather than a substitution.
        tail = name[12:]
        f = (int(tail) / 4.0) if tail.isdigit() else 1.0
        if f > 0.02:
            x1, y1 = 3.5, 4.5
            x2 = x1 + (20.5 - x1) * f
            y2 = y1 + (19.5 - y1) * f
            # A gap under the stroke, in the bar's own colour, so the
            # slash separates from the cone instead of merging into one
            # red shape.
            gap = QPen(QColor(T["bar_bg"]), 4.4 * u)
            gap.setCapStyle(Qt.RoundCap)
            p.setPen(gap)
            p.drawLine(int(x1 * u), int(y1 * u), int(x2 * u), int(y2 * u))
            stroke = QPen(c, 2.2 * u)
            stroke.setCapStyle(Qt.RoundCap)
            p.setPen(stroke)
            p.drawLine(int(x1 * u), int(y1 * u), int(x2 * u), int(y2 * u))
        p.setPen(pen)
    elif name.startswith("volume"):
        # volume0..volume3 — the arcs are the level, not decoration.
        waves = 3
        if len(name) > 6 and name[6:].isdigit():
            waves = int(name[6:])
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(poly((4, 9), (8, 9), (12.5, 5), (12.5, 19), (8, 15),
                           (4, 15)))
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
        for i, box in enumerate(((14.5, 9.5, 3, 5), (16.5, 7, 5, 10),
                                 (18.5, 4.5, 7, 15))):
            if i < waves:
                x, y, w, h = box
                p.drawArc(QRectF(x * u - w * u / 2, y * u, w * u, h * u),
                          -70 * 16, 140 * 16)
    elif name.startswith("eq"):
        # Three sliders, spaced across the full width rather than huddled
        # in the middle.
        #
        # "eq" is the resting position; "eq_hover" moves each knob to the
        # far end of its own travel, in the direction it has room for. A
        # caller animates between the two by asking for intermediate
        # names — eq_30, eq_60 — so the movement is a series of cached
        # glyphs rather than a repaint of live geometry.
        try:
            toward = int(name.split("_", 1)[1]) / 100.0 if "_" in name else 0.0
        except ValueError:
            toward = 1.0 if name.endswith("hover") else 0.0
        toward = max(0.0, min(1.0, toward))

        # Where each knob ends up: the outer two go down, the middle one
        # goes up, so the three read as moving against each other rather
        # than sliding together.
        for x, knob, target in ((5, 15, 7), (12, 8, 16), (19, 13, 6)):
            knob = knob + (target - knob) * toward
            p.drawLine(int(x * u), int(4 * u), int(x * u), int(20 * u))
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(
                QRectF((x - 2.6) * u, (knob - 1.4) * u, 5.2 * u, 2.8 * u),
                1.2 * u, 1.2 * u)
            p.setBrush(Qt.NoBrush)
            p.setPen(pen)
    elif name.startswith("timer"):
        # A clock face with a stem on top, which is what a kitchen timer
        # looks like and reads at 22px better than an hourglass does.
        #
        # "timer" is at rest; "timer_40" and the like are frames of the
        # hover: the button on top presses down into the face and the
        # hands sweep round, the way winding one feels.
        import math
        try:
            t = int(name.split("_", 1)[1]) / 100.0 if "_" in name else 0.0
        except ValueError:
            t = 0.0
        t = max(0.0, min(1.0, t))

        # The whole drawing sits this many units higher than it was.
        #
        # It spanned 3.5 to 23 on a 24-unit grid, so its middle was at
        # 13.25 while shuffle, the equaliser and full screen all have
        # theirs at 12 — and in the bar the timer looked like it had
        # slipped down. Lifting everything by the same amount keeps the
        # shape and the hover exactly as they were.
        lift = 1.5

        # The press: all of it in the first half, so it has landed by the
        # time the hands are moving, and it reads as a click rather than
        # as the button sliding.
        # About two pixels at the size it is drawn, which is the least
        # that reads as a click rather than as the icon wobbling.
        press = min(1.0, t * 2.0) * 2.4
        p.drawEllipse(QRectF(4 * u, (7 - lift) * u, 16 * u, 16 * u))
        p.drawLine(QPointF(9 * u, (3.5 + press - lift) * u),
                   QPointF(15 * u, (3.5 + press - lift) * u))
        p.drawLine(QPointF(12 * u, (3.5 + press - lift) * u),
                   QPointF(12 * u, (7 - lift) * u))

        # The hands: a full turn for the long one and a small step for the
        # short, like the minute hand racing the hour hand when a timer
        # is wound. A full turn also ends exactly where it began, so while
        # the pointer rests on the button the face looks ordinary rather
        # than stopped at an odd angle. Real angles and float points —
        # whole-pixel ends made the rotation jitter.
        centre = QPointF(12 * u, (15 - lift) * u)
        for length, start, travel in ((4.0, -90.0, 360.0),
                                      (3.0, 0.0, 30.0)):
            angle = math.radians(start + travel * t)
            p.drawLine(centre, QPointF(
                centre.x() + math.cos(angle) * length * u,
                centre.y() + math.sin(angle) * length * u))
    elif name == "search":
        p.drawEllipse(QRectF(4 * u, 4 * u, 12 * u, 12 * u))
        p.drawLine(int(15 * u), int(15 * u), int(20 * u), int(20 * u))
    elif name == "stations":
        # Arcs opening up and down rather than left and right — the same
        # broadcast mark, turned a quarter.
        p.drawEllipse(QRectF(9.5 * u, 9.5 * u, 5 * u, 5 * u))
        p.drawArc(QRectF(5 * u, 5 * u, 14 * u, 14 * u), 120 * 16, 120 * 16)
        p.drawArc(QRectF(5 * u, 5 * u, 14 * u, 14 * u), 300 * 16, 120 * 16)
        p.drawArc(QRectF(1.5 * u, 1.5 * u, 21 * u, 21 * u), 120 * 16, 120 * 16)
        p.drawArc(QRectF(1.5 * u, 1.5 * u, 21 * u, 21 * u), 300 * 16, 120 * 16)
    elif name == "discover":
        p.drawEllipse(QRectF(3 * u, 3 * u, 18 * u, 18 * u))
        p.setBrush(c); p.setPen(Qt.NoPen)
        p.drawPolygon(poly((15.5, 8.5), (13, 13), (8.5, 15.5), (11, 11)))
    elif name == "history":
        p.drawEllipse(QRectF(3.5 * u, 3.5 * u, 17 * u, 17 * u))
        p.drawPolyline(poly((12, 7), (12, 12), (16, 14.5)))
    elif name == "trash":
        p.drawLine(int(4 * u), int(6.5 * u), int(20 * u), int(6.5 * u))
        p.drawPolyline(poly((9, 6.5), (9, 4), (15, 4), (15, 6.5)))
        p.drawPolyline(poly((6, 6.5), (7, 20), (17, 20), (18, 6.5)))
        p.drawLine(int(10.5 * u), int(10 * u), int(10.5 * u), int(17 * u))
        p.drawLine(int(13.5 * u), int(10 * u), int(13.5 * u), int(17 * u))
    elif name == "info":
        p.drawEllipse(QRectF(4 * u, 4 * u, 16 * u, 16 * u))
        p.drawLine(int(12 * u), int(11 * u), int(12 * u), int(16.5 * u))
        p.setBrush(c); p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(11 * u, 7 * u, 2 * u, 2 * u))
    elif name == "menu":
        for y in (8, 12, 16):
            p.drawLine(int(5 * u), int(y * u), int(19 * u), int(y * u))
    elif name == "expand":
        p.drawPolyline(poly((8, 14), (12, 10), (16, 14)))
    elif name == "shazam":
        # A concentric ring, filled when identification is on.
        p.drawEllipse(QRectF(4 * u, 4 * u, 16 * u, 16 * u))
        p.drawArc(QRectF(8 * u, 8 * u, 8 * u, 8 * u), 30 * 16, 300 * 16)
    elif name.startswith("shazam_fill"):
        # The real mark, twice: once grey, once in the accent, with the
        # second clipped to a circle growing from the middle. Redrawing an
        # approximation of the swirl by hand gave a different shape from
        # the one beside it, which is worse than no animation.
        tail = name[11:]
        f = (int(tail) / 4.0) if tail.isdigit() else 1.0
        base = icon("shazam_on", size, T["text2"]).pixmap(size, size)
        p.drawPixmap(0, 0, base)
        if f > 0.02:
            lit = icon("shazam_on", size, c.name()
                       if hasattr(c, "name") else color).pixmap(size, size)
            radius = size / 2.0 * f * 1.15
            path = QPainterPath()
            path.addEllipse(QRectF(size / 2 - radius, size / 2 - radius,
                                   radius * 2, radius * 2))
            p.setClipPath(path)
            p.drawPixmap(0, 0, lit)
            p.setClipping(False)
    elif name == "shazam_on":
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(4 * u, 4 * u, 16 * u, 16 * u))
        p.setBrush(Qt.NoBrush)
    elif name == "gear":
        # Teeth as short broad blocks around the rim, not spokes radiating
        # from the centre — thin rays at this size read as a sun.
        import math
        cx, cy = 12 * u, 12 * u
        p.save()
        p.setBrush(c)
        for i in range(8):
            p.save()
            p.translate(cx, cy)
            p.rotate(i * 45)
            p.drawRoundedRect(QRectF(-1.9 * u, -10.2 * u, 3.8 * u, 3.6 * u),
                              0.8 * u, 0.8 * u)
            p.restore()
        p.setBrush(Qt.NoBrush)
        pen = p.pen()
        pen.setWidthF(2.2 * u)
        p.setPen(pen)
        p.drawEllipse(QRectF(4.6 * u, 4.6 * u, 14.8 * u, 14.8 * u))
        p.restore()
        p.drawEllipse(QRectF(9.2 * u, 9.2 * u, 5.6 * u, 5.6 * u))
    elif name == "winamp":
        # The lightning bolt, flat and monochrome: the one part of the
        # Winamp mark that still reads at 18px.
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(poly((14.5, 2.5), (7, 13), (11, 13), (9.5, 21.5),
                           (17, 11), (13, 11)))
        p.setBrush(Qt.NoBrush)
    elif name == "visualizer":
        # Four bars of a spectrum analyser, uneven so it reads as levels
        # rather than a bar chart.
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        for x, top in ((4.5, 13), (9, 7), (13.5, 10), (18, 4.5)):
            p.drawRect(QRectF(x * u, top * u, 2.5 * u, (20 - top) * u))
        p.setBrush(Qt.NoBrush)
    elif name == "schedule":
        # A grid with a marked column: the shape of a listings page.
        p.drawRoundedRect(QRectF(3.5 * u, 5 * u, 17 * u, 15 * u),
                          2 * u, 2 * u)
        p.drawLine(int(3.5 * u), int(9.5 * u), int(20.5 * u), int(9.5 * u))
        for y in (13.2, 16.6):
            p.drawLine(int(3.5 * u), int(y * u), int(20.5 * u), int(y * u))
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(6 * u, 10.6 * u, 5.4 * u, 1.9 * u),
                          0.8 * u, 0.8 * u)
        p.drawRoundedRect(QRectF(12.6 * u, 14 * u, 5.4 * u, 1.9 * u),
                          0.8 * u, 0.8 * u)
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
    elif name == "help":
        p.drawEllipse(QRectF(3.5 * u, 3.5 * u, 17 * u, 17 * u))
        p.drawArc(QRectF(8.6 * u, 7.0 * u, 6.8 * u, 6.4 * u),
                  200 * 16, -250 * 16)
        p.drawLine(int(12 * u), int(13.2 * u), int(12 * u), int(15 * u))
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(11 * u, 16.6 * u, 2 * u, 2 * u))
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
    elif name == "tag":
        # A luggage-label: angled body, notched corner, a hole for the
        # string. The stations glyph was standing in for this and said
        # "list", which is what the button above it already says.
        p.drawPolyline(poly((11.5, 3.5), (20.5, 3.5), (20.5, 12.5),
                            (11.5, 21), (3.5, 12.5), (11.5, 3.5)))
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(15.4 * u, 6.4 * u, 3.1 * u, 3.1 * u))
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
    elif name == "back":
        p.drawPolyline(poly((13, 5), (6, 12), (13, 19)))
        p.drawLine(int(6 * u), int(12 * u), int(20 * u), int(12 * u))
    elif name == "folder":
        p.drawPolyline(poly((3, 19), (3, 6), (9, 6), (11, 8.5), (21, 8.5),
                            (21, 19), (3, 19)))
    elif name == "link":
        # Two chain links, angled.
        p.drawArc(QRectF(3 * u, 9 * u, 10 * u, 6 * u), 90 * 16, 180 * 16)
        p.drawArc(QRectF(11 * u, 9 * u, 10 * u, 6 * u), 270 * 16, 180 * 16)
        p.drawLine(int(8 * u), int(12 * u), int(16 * u), int(12 * u))
    elif name == "youtube":
        p.drawRoundedRect(QRectF(3 * u, 6 * u, 18 * u, 12 * u), 3 * u, 3 * u)
        p.setBrush(c)
        p.drawPolygon(poly((10, 9), (16, 12), (10, 15)))
        p.setBrush(Qt.NoBrush)
    elif name == "ibroadcast":
        # Their own mark: a lowercase i beside a heavy B.
        #
        # Drawn as solid shapes rather than as text, because a font-based
        # glyph would come out in whatever typeface the system has and
        # would not match at 20px. The B is built from an outer rounded
        # body with two counters knocked out of it, which is what gives
        # it the two bowls without needing a path with holes.
        p.setPen(Qt.NoPen)
        p.setBrush(c)

        # The i: a dot and a stem, both left of centre.
        p.drawEllipse(QRectF(3.4 * u, 4.0 * u, 4.0 * u, 4.0 * u))
        p.drawRoundedRect(QRectF(3.9 * u, 9.6 * u, 3.0 * u, 10.4 * u),
                          0.5 * u, 0.5 * u)

        # The B: the solid shape united, then the counters subtracted.
        #
        # Odd-even was the wrong tool. It makes *any* overlap a hole, so
        # where the spine met the bowls, and where the two bowls met each
        # other, the fill cancelled — which is what the stray black shapes
        # were. united() and subtracted() say what is actually meant:
        # these three make the letter, those two are the holes in it.
        spine = QPainterPath()
        spine.addRect(QRectF(9.6 * u, 3.6 * u, 3.6 * u, 16.8 * u))
        upper = QPainterPath()
        upper.addRoundedRect(QRectF(9.6 * u, 3.6 * u, 9.4 * u, 8.6 * u),
                             3.3 * u, 3.3 * u)
        lower = QPainterPath()
        lower.addRoundedRect(QRectF(9.6 * u, 11.8 * u, 10.6 * u, 8.6 * u),
                             3.7 * u, 3.7 * u)
        letter = spine.united(upper).united(lower)

        hole_top = QPainterPath()
        hole_top.addRoundedRect(QRectF(13.4 * u, 6.2 * u, 3.6 * u, 3.4 * u),
                                1.2 * u, 1.2 * u)
        hole_bottom = QPainterPath()
        hole_bottom.addRoundedRect(QRectF(13.4 * u, 14.2 * u, 4.2 * u,
                                          3.8 * u), 1.4 * u, 1.4 * u)
        letter = letter.subtracted(hole_top).subtracted(hole_bottom)
        p.drawPath(letter)

        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
    elif name == "shared":
        # A card with a code on it: the shape of the thing being handed
        # over, rather than a QR that would be unreadable at 20px.
        p.drawRoundedRect(QRectF(3 * u, 5 * u, 18 * u, 14 * u), 2 * u, 2 * u)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        for row in range(3):
            for col in range(3):
                if (row + col) % 2 == 0:
                    p.drawRect(QRectF((6.4 + col * 2.6) * u,
                                      (8.2 + row * 2.6) * u,
                                      1.8 * u, 1.8 * u))
        p.drawRect(QRectF(14.4 * u, 13.4 * u, 1.8 * u, 1.8 * u))
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
    elif name == "twitch":
        # The chat bubble with a screen inside it — the shape everyone
        # recognises without being the mark itself.
        #
        # Lost when the Deezer branch was deleted: the removal ran to the
        # *next* branch, and Twitch was the next branch.
        p.drawPolyline(poly((4, 6), (4, 17), (8.5, 17), (11.5, 20.5),
                            (14.5, 17), (20, 17), (20, 6), (4, 6)))
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(9 * u, 9 * u, 1.9 * u, 5 * u), 0.9 * u,
                          0.9 * u)
        p.drawRoundedRect(QRectF(13.5 * u, 9 * u, 1.9 * u, 5 * u), 0.9 * u,
                          0.9 * u)
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
    elif name == "sniffer":
        # A magnifier over a waveform: looking inside a page for audio.
        p.drawEllipse(QRectF(4 * u, 4 * u, 12 * u, 12 * u))
        p.drawLine(int(15 * u), int(15 * u), int(20 * u), int(20 * u))
        p.drawPolyline(poly((7, 10), (8.5, 13), (10, 7.5), (11.5, 12),
                            (13, 10)))
    elif name.startswith("thumb"):
        # thumb_up / thumb_down, hollow, or _on for the filled state.
        down = "down" in name
        filled = name.endswith("_on")
        p.save()
        if down:
            p.translate(24 * u, 24 * u)
            p.rotate(180)
        if filled:
            p.setBrush(c)
        # The cuff, then the hand: one shape so the fill reads as solid.
        p.drawRoundedRect(QRectF(3.5 * u, 10.5 * u, 4.5 * u, 9.5 * u),
                          1.2 * u, 1.2 * u)
        p.drawPolygon(poly((9, 20), (9, 10.5), (13, 3.6), (14.6, 3.6),
                           (15.2, 5.2), (14.2, 9.2), (19.6, 9.2),
                           (20.6, 10.6), (19.2, 19), (17.6, 20)))
        p.setBrush(Qt.NoBrush)
        p.restore()
    elif name.startswith("fullscreen"):
        # fullscreen, or fullscreen2/3 with the corners pushed outward —
        # the frames of the hover animation.
        step = 0
        if len(name) > 10 and name[10:].isdigit():
            step = int(name[10:])
        d = step * 1.1
        p.drawPolyline(poly((9 - d, 4 - d), (4 - d, 4 - d), (4 - d, 9 - d)))
        p.drawPolyline(poly((15 + d, 4 - d), (20 + d, 4 - d), (20 + d, 9 - d)))
        p.drawPolyline(poly((9 - d, 20 + d), (4 - d, 20 + d), (4 - d, 15 + d)))
        p.drawPolyline(poly((15 + d, 20 + d), (20 + d, 20 + d),
                            (20 + d, 15 + d)))
    elif name == "plus":
        p.drawLine(int(12 * u), int(6 * u), int(12 * u), int(18 * u))
        p.drawLine(int(6 * u), int(12 * u), int(18 * u), int(12 * u))
    elif name == "edit":
        # A pencil, point down-left: the body, the line where the tip
        # starts, and the line under it (task 79, the Tags edit button).
        p.drawPolygon(poly((15.5, 4.5), (19.5, 8.5), (9, 19), (5, 19),
                           (5, 15)))
        p.drawLine(int(13 * u), int(7 * u), int(17 * u), int(11 * u))
        p.drawLine(int(12 * u), int(20.5 * u), int(20 * u), int(20.5 * u))
    elif name == "smiley":
        # A face: the chat's emote picker button (task 95).
        p.drawEllipse(QRectF(3.5 * u, 3.5 * u, 17 * u, 17 * u))
        p.save()
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(8.2 * u, 8.6 * u, 2.2 * u, 2.6 * u))
        p.drawEllipse(QRectF(13.6 * u, 8.6 * u, 2.2 * u, 2.6 * u))
        p.restore()
        p.drawArc(QRectF(7.5 * u, 9 * u, 9 * u, 7.5 * u), -20 * 16,
                  -140 * 16)

    p.end()
    return QIcon(pm)


MENU_GROW_MS = 130
MENU_FROM = 0.86        # the fraction it starts at


def open_menu(menu, where):
    """Show a menu the way the artwork does it: growing out of the point
    you clicked, fading in as it goes.

    A menu that simply appears has no relationship to what you clicked —
    it could belong to anything. One that grows from the cursor is
    plainly the answer to the thing under it.

    exec_ runs its own event loop, so the animation is started from a
    zero-millisecond timer and plays inside it.
    """
    # The rounded corners only show if the window behind them is allowed
    # to be see-through. Without this the radius is drawn onto an opaque
    # square popup and the corners stay sharp.
    menu.setAttribute(Qt.WA_TranslucentBackground, True)
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint
                        | Qt.NoDropShadowWindowHint)
    # Wayland ignores window opacity and warns on every call, one per
    # frame of the fade (build 89's log: nine at a time).
    fades = not QGuiApplication.platformName().startswith("wayland")
    if fades:
        menu.setWindowOpacity(0.0)

    def start():
        target = menu.geometry()
        if target.isEmpty():
            if fades:
                menu.setWindowOpacity(1.0)
            return
        small = QRect(
            target.left(), target.top(),
            max(1, int(target.width() * MENU_FROM)),
            max(1, int(target.height() * MENU_FROM)))
        menu.setGeometry(small)

        group = QParallelAnimationGroup(menu)
        grow = QPropertyAnimation(menu, b"geometry", menu)
        grow.setDuration(MENU_GROW_MS)
        grow.setStartValue(small)
        grow.setEndValue(target)
        # Slightly past and back would overshoot the text; out-cubic just
        # decelerates, which is what the artwork does.
        grow.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(grow)

        if fades:
            fade = QPropertyAnimation(menu, b"windowOpacity", menu)
            fade.setDuration(MENU_GROW_MS)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            group.addAnimation(fade)
        group.start(QParallelAnimationGroup.DeleteWhenStopped)

    QTimer.singleShot(0, start)
    return menu.exec_(where)


def with_badge(base, size, colour, badge=None):
    """An icon with a small mark in its corner.

    Drawn on rather than made into another glyph, so any icon can carry
    one and the badge stays the same shape wherever it appears.
    """
    pm = base.pixmap(size, size)
    if not badge:
        return QIcon(pm)

    # Room made for it, rather than sat on top of the glyph.
    #
    # A third of the icon's width, dropped on the bottom-right corner,
    # landed squarely on the logo. The artwork is shrunk and moved up-left
    # so the badge has a corner of its own to sit in.
    r = max(5, size // 3)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    inset = int(r * 0.55)
    p.drawPixmap(0, 0, pm.scaled(size - inset, size - inset,
                                 Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation))
    box = QRectF(size - r - 1, size - r - 1, r, r)

    # A ring of background around it, so the mark reads against whatever
    # part of the icon it lands on.
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(T["bg1"]))
    p.drawEllipse(box.adjusted(-1.5, -1.5, 1.5, 1.5))
    p.setBrush(QColor(badge))
    p.drawEllipse(box)

    # A tick, if there is room for one to read.
    if r >= 7:
        tick = QPen(QColor(T["bg1"]), max(1.2, r * 0.18))
        tick.setCapStyle(Qt.RoundCap)
        tick.setJoinStyle(Qt.RoundJoin)
        p.setPen(tick)
        p.setBrush(Qt.NoBrush)
        left, top, w = box.left(), box.top(), box.width()
        p.drawPolyline(QPolygonF([
            QPointF(left + w * 0.26, top + w * 0.52),
            QPointF(left + w * 0.44, top + w * 0.70),
            QPointF(left + w * 0.76, top + w * 0.30)]))
    p.end()
    return QIcon(out)


def _animate_glyph(button, base, size, steps=(0, 25, 50, 75, 100), ms=28,
                   colour=None):
    """Run a glyph through its frames while the pointer is over it.

    The frames are just icons with different names, so each one is drawn
    once and cached like any other — hovering a button does not mean
    redrawing anything after the first pass.
    """
    from PyQt5.QtCore import QEvent, QObject, QTimer

    class _Hover(QObject):
        def __init__(self):
            super().__init__(button)
            self.at = 0
            self.forward = True
            self.timer = QTimer(self)
            self.timer.setInterval(ms)
            self.timer.timeout.connect(self.step)

        def step(self):
            self.at += 1 if self.forward else -1
            if not 0 <= self.at < len(steps):
                self.at = max(0, min(len(steps) - 1, self.at))
                self.timer.stop()
            frame = steps[self.at]
            # The colour is asked for each frame rather than fixed, so a
            # button that is lit — the sleep timer while it counts down —
            # stays lit through the animation instead of going grey.
            tint = colour() if callable(colour) else T["text2"]
            button._timer_frame = frame
            button.setIcon(icon(f"{base}_{frame}" if frame else base,
                                size, tint))

        def eventFilter(self, _obj, event):
            if event.type() == QEvent.Enter:
                self.forward = True
                self.timer.start()
            elif event.type() == QEvent.Leave:
                self.forward = False
                self.timer.start()
            return False

    watcher = _Hover()
    button.installEventFilter(watcher)
    button._glyph_hover = watcher       # kept alive with the button
    return watcher


def tool_button(name, tip, size=20, checkable=False):
    b = QToolButton()
    # Hover events are delivered regardless of which window is active, so
    # pointing at a control still lights it when the app is in the
    # background — which is where a radio spends most of its time.
    b.setAttribute(Qt.WA_Hover, True)
    b.setIcon(icon(name, size, T["text2"]))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setAutoRaise(True)
    return b


def _unused_paint_new_badge(widget, right_inset=10):
    """A small green pill reading "New!", at the right of a widget."""
    from PyQt5.QtGui import QFont, QFontMetrics

    p = QPainter(widget)
    p.setRenderHint(QPainter.Antialiasing)
    font = QFont(widget.font())
    font.setPixelSize(10)
    font.setBold(True)
    p.setFont(font)
    text = "New!"
    width = QFontMetrics(font).horizontalAdvance(text) + 12
    height = 16
    box = QRectF(widget.width() - width - right_inset,
                 (widget.height() - height) / 2.0, width, height)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#2e9e5b"))
    p.drawRoundedRect(box, height / 2.0, height / 2.0)
    p.setPen(QColor("#ffffff"))
    p.drawText(box, Qt.AlignCenter, text)
    p.end()


class TagPill(QPushButton):
    """A tag chip whose strike-through is drawn across it.

    Excluding a tag is a real change of meaning — from "only these" to
    "anything but these" — and it deserves to be seen happening rather
    than to have already happened.
    """

    STRIKE_MS = 180

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self._struck = 0.0
        self._anim = None

    def set_struck(self, on):
        target = 1.0 if on else 0.0
        if abs(self._struck - target) < 0.01:
            return
        if self._anim is not None:
            self._anim.stop()
        anim = QVariantAnimation(self)
        anim.setDuration(self.STRIKE_MS)
        anim.setStartValue(float(self._struck))
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(self._set_value)
        anim.start()
        self._anim = anim

    def _set_value(self, value):
        self._struck = float(value)
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._struck <= 0.01:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(T["text2"]), 1.4)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        y = self.height() / 2.0
        left = 8.0
        span = (self.width() - 16.0) * self._struck
        p.drawLine(QPointF(left, y), QPointF(left + span, y))
        p.end()


class ClickableLabel(QLabel):
    """A label that reports clicks. The expanded artwork uses it, because
    the thumbnail it came from is no longer on screen to click again."""

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class Ripple(QWidget):
    """An expanding ring over a button, in the manner of a Material ripple.

    The conventions these follow are consistent across Material, iOS and
    Fluent, and my first attempt broke all of them: it stepped the icon
    through six sizes on a linear timer, which reads as the icon twitching
    rather than as the button responding.

    What they share is that the acknowledgement is a shape that leaves the
    control — expanding outward from its centre, decelerating, fading as
    it grows, and gone in under half a second — while the control itself
    stays where it is. The icon gets a small spring instead of a resize:
    up quickly, back slowly.
    """

    DURATION = 560

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # Nothing of its own to paint: an overlay that fills its rect with
        # a background hides the very button it is meant to acknowledge.
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._progress = 0.0
        self.hide()

    def get_progress(self):
        return self._progress

    def set_progress(self, value):
        self._progress = value
        self.update()

    progress = pyqtProperty(float, get_progress, set_progress)

    def fire(self):
        self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()
        anim = QPropertyAnimation(self, b"progress", self)
        anim.setDuration(self.DURATION)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        # Decelerating: fast as it leaves, slow as it dies. A linear ring
        # looks mechanical, and an accelerating one looks like an error.
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(self.hide)
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def paintEvent(self, _e):
        if self._progress <= 0.0 or self._progress >= 1.0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        centre = QPointF(self.width() / 2, self.height() / 2)
        base = min(self.width(), self.height()) / 2

        # Two rings, the second trailing — that trailing edge is what
        # reads as a shimmer rather than a single bubble.
        for lag in (0.0, 0.22):
            t = self._progress - lag
            if t <= 0.0 or t >= 1.0:
                continue
            radius = base * (0.45 + t * 1.15)
            colour = QColor(T["accent"])
            colour.setAlpha(int(150 * (1.0 - t) ** 1.6))
            p.setPen(QPen(colour, max(1.2, base * 0.16 * (1.0 - t))))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(centre, radius, radius)

        # A soft fill for the first third, so the button looks touched
        # rather than merely circled.
        if self._progress < 0.34:
            wash = QColor(T["accent"])
            wash.setAlpha(int(70 * (1.0 - self._progress / 0.34)))
            p.setPen(Qt.NoPen)
            p.setBrush(wash)
            p.drawEllipse(centre, base * 0.95, base * 0.95)
        p.end()


class Hourglass(QWidget):
    """How much of a covered ad is left, as sand.

    A countdown in text is a number to read; an hourglass is a glance.
    It also says what is happening without a caption — something is
    running, and it will finish — which is all the ad cover needs to
    communicate.
    """

    def __init__(self, size=20, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._total = 0.0
        self._left = 0.0
        self._time_left = None
        self._began = 0.0
        self._emptied = None
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)
        self.setVisible(False)

    # How long the empty glass stays up once the ad is over.
    EMPTY_HOLD_MS = 1500

    def start(self, seconds, time_left=None):
        """Run the glass for an ad of `seconds`.

        `time_left` (build 71, task 80): a function giving the seconds
        the player says are left (None once its ad is over). The glass
        follows it, so it empties when the ad really ends. It used to
        count down on its own from the button press, while the player's
        ad clock starts only when the station makes a sound, so the
        glass was out of step. Without one it keeps its own (monotonic)
        clock.
        """
        self._total = max(1.0, float(seconds))
        self._left = self._total
        self._time_left = time_left
        self._began = time.monotonic()
        self._emptied = None
        self._say_time()
        self.setVisible(True)
        self._timer.start()
        self.update()

    def stop(self):
        self._timer.stop()
        self._left = 0.0
        self._time_left = None
        self.setVisible(False)

    def running(self):
        return self._timer.isActive()

    def _tick(self):
        now = time.monotonic()
        if self._emptied is not None:
            # Over: the empty glass stays a moment, then goes.
            if (now - self._emptied) * 1000 >= self.EMPTY_HOLD_MS:
                self.stop()
            return
        if self._time_left is not None:
            try:
                left = self._time_left()
            except Exception:                   # noqa: BLE001
                left = None
        else:
            left = self._total - (now - self._began)
        if left is None or left <= 0:
            self._left = 0.0
            self._emptied = now
            self.setToolTip("Ad over")
            self.update()
            return
        self._left = min(self._total, float(left))
        self._say_time()
        self.update()

    def _say_time(self):
        remaining = int(self._left + 0.5)
        self.setToolTip("Ad playing. Time remaining: "
                        f"{remaining // 60}:{remaining % 60:02d}")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        u = self._size / 24.0
        run = 1.0 - (self._left / self._total if self._total else 1.0)

        frame = QColor(T["text2"])
        pen = QPen(frame, 1.5 * u)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        # The glass: two triangles meeting at the waist, capped top and
        # bottom.
        top, bottom, waist = 4.0 * u, 20.0 * u, 12.0 * u
        left, right = 6.0 * u, 18.0 * u
        p.drawLine(int(left), int(top), int(right), int(top))
        p.drawLine(int(left), int(bottom), int(right), int(bottom))
        p.drawPolyline(QPolygonF([QPointF(left, top),
                                  QPointF(12 * u, waist),
                                  QPointF(left, bottom)]))
        p.drawPolyline(QPolygonF([QPointF(right, top),
                                  QPointF(12 * u, waist),
                                  QPointF(right, bottom)]))

        # The sand: the upper cone empties as the lower one fills, so the
        # two always add up to the same amount.
        #
        # By area, as real sand does (task 85, build 78). The lower pile
        # used to be a triangle the full height of the bulb from the
        # first second, only getting wider, so the bottom looked full
        # long before the ad was over; and the upper cone lost both
        # height and width at once, so it looked a quarter full at half
        # time. Now the upper sand is a cone sitting on the waist and the
        # lower sand a pile rising from the bottom, each holding exactly
        # its share: half the ad gone, half the sand down.
        sand = QColor(T["accent"])
        p.setPen(Qt.NoPen)
        p.setBrush(sand)

        run = max(0.0, min(1.0, run))
        remaining = 1.0 - run
        bulb = waist - top                  # height of each bulb
        half_cap = (right - left) / 2       # half the glass's width
        mid = 12 * u
        if remaining > 0.02:
            # A cone on the waist; its area goes with height squared.
            scale = math.sqrt(remaining)
            y = waist - bulb * scale
            half = half_cap * scale
            p.drawPolygon(QPolygonF([
                QPointF(mid - half, y), QPointF(mid + half, y),
                QPointF(mid, waist)]))
        if run > 0.02:
            # A pile from the bottom up: the bulb narrows toward the
            # waist, so the first sand barely raises it.
            narrow = math.sqrt(remaining)   # width left at the pile's top
            y = bottom - bulb * (1.0 - narrow)
            half = half_cap * narrow
            if narrow > 0.02:
                p.drawPolygon(QPolygonF([
                    QPointF(mid - half, y), QPointF(mid + half, y),
                    QPointF(mid + half_cap, bottom),
                    QPointF(mid - half_cap, bottom)]))
            else:
                p.drawPolygon(QPolygonF([
                    QPointF(mid, waist), QPointF(mid + half_cap, bottom),
                    QPointF(mid - half_cap, bottom)]))
            if remaining > 0.02:
                # The falling stream, from the waist to the pile.
                p.setPen(QPen(sand, 1.2 * u))
                p.drawLine(QPointF(mid, waist), QPointF(mid, y))
        p.end()


class ArtThumb(QLabel):
    """The player bar's artwork. Highlights under the pointer and reports
    a click, so it can be a control rather than decoration."""

    clicked = pyqtSignal()

    def __init__(self, size, parent=None):
        super().__init__(parent)
        self._size = size
        # Height fixed, width animatable: setFixedSize would pin the width
        # too and the slide would have nothing to move.
        self.setFixedHeight(size)
        self.setMinimumWidth(size)
        self.setMaximumWidth(size)
        self._tint = None
        self._art = placeholder_art(size)
        # The expanded view draws from _full, and a 54px placeholder
        # stretched to fill a 300px dock is a blur. Drawn once at a size
        # the dock will never exceed.
        self._full = placeholder_art(self.FULL_SIZE)
        self._hot = False
        self._repaint()

    # Big enough for the dock and the full-screen view to scale down
    # from rather than up: a 54px placeholder stretched across a 300px
    # dock is the blur.
    FULL_SIZE = 512

    def set_placeholder_tint(self, colour, station_name=""):
        """Recolour the empty state to match this station."""
        name = QColor(colour).name() if colour else None
        if name == self._tint and station_name == getattr(
                self, "_mono_name", ""):
            return
        self._tint = name
        self._mono_name = station_name or ""
        if self._showing_placeholder():
            self.set_art(QPixmap())

    def _showing_placeholder(self):
        return bool(getattr(self, "_is_placeholder", True))

    def _square(self, pixmap):
        """Centre-crop to the thumbnail's own size.

        Expanding to fill keeps the aspect, which means a 16:9 cover comes
        back nearly twice as wide as the label — and the overflow is
        clipped against the right edge rather than centred. YouTube
        thumbnails are all 16:9, which is why this only showed up
        sometimes.
        """
        if pixmap is None or pixmap.isNull():
            return pixmap
        size = self._size
        if pixmap.width() == size and pixmap.height() == size:
            return pixmap
        x = max(0, (pixmap.width() - size) // 2)
        y = max(0, (pixmap.height() - size) // 2)
        return pixmap.copy(x, y, min(size, pixmap.width()),
                           min(size, pixmap.height()))

    def set_scaled(self, thumb, full):
        """Both already scaled — only the crop is left."""
        self._full = full
        self._art = self._square(thumb)
        self._repaint()

    def set_art(self, pixmap):
        # Nothing means the placeholder, not nothing.
        #
        # An empty pixmap was stored as-is, so clearing the artwork left
        # a blank square in the bar and an empty dock — which is the
        # "sometimes there is just nothing there" case. The placeholder
        # is what an absent cover looks like.
        if pixmap is None or pixmap.isNull():
            self._is_placeholder = True
            mono = getattr(self, "_mono_name", "")
            self._art = placeholder_art(self._size, self._tint, mono)
            self._full = placeholder_art(self.FULL_SIZE, self._tint, mono)
            self._repaint()
            return

        # The full-size original is kept as well as the thumbnail: the
        # sidebar dock is four times this wide, and rescaling a 48px
        # thumbnail up to fill it is what made the expanded art look like
        # it had been printed on a potato.
        self._is_placeholder = False
        self._full = pixmap
        self._art = self._square(pixmap.scaled(
            self._size, self._size, Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation))
        self._repaint()

    def art(self):
        """The thumbnail, for the bar."""
        return self._art

    def full_art(self):
        """The original, for anything larger than the bar."""
        return self._full

    def _repaint(self):
        pm = rounded(self._art, 5)
        if self._hot:
            # Lifted rather than tinted: a wash over album art fights the
            # art, while a light edge reads as "this does something".
            out = QPixmap(pm.size())
            out.fill(Qt.transparent)
            p = QPainter(out)
            p.setRenderHint(QPainter.Antialiasing)
            p.drawPixmap(0, 0, pm)
            p.setPen(QPen(QColor(255, 255, 255, 150), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(1, 1, pm.width() - 2, pm.height() - 2),
                              5, 5)
            p.end()
            pm = out
        self.setPixmap(pm)

    def enterEvent(self, e):
        self._hot = True
        self._repaint()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hot = False
        self._repaint()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class Marquee(QLabel):
    """A label that scrolls its text when it doesn't fit, Winamp-style.

    The player bar's text used to size itself to its content, so a long
    title pushed the transport buttons sideways. This has a fixed width and
    scrolls instead: the layout stops moving, and you still get to read the
    whole thing.
    """

    LIMIT = 40              # characters before scrolling is worth it
    STEP = 1                # pixels per tick
    INTERVAL = 33           # ~30fps
    PAUSE_MS = 1400         # hold at the start so it can be read
    GAP = 48                # blank between the end and the repeat

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._full = text
        self._offset = 0
        self._direction = 1
        self._paused = 0
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL)
        self._timer.timeout.connect(self._tick)

    def setText(self, text):
        text = text or ""
        if text == self._full:
            return
        self._full = text
        self._offset = 0
        self._direction = 1
        self._paused = self.PAUSE_MS // self.INTERVAL
        super().setText(text)
        self.setToolTip(text if len(text) > self.LIMIT else "")
        self._restart()

    def text(self):
        return self._full

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._restart()

    def sizeHint(self):
        """As wide as the text, up to the cap.

        A fixed width parked the star a full 260px from a two-word title.
        Capping instead of fixing keeps the transport where it is — it is
        centered in the bar, not positioned after this — while letting short
        titles pull the star in beside them.
        """
        hint = super().sizeHint()
        width = self.fontMetrics().horizontalAdvance(self._full) + 2
        return QSize(min(width, self.maximumWidth()), hint.height())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def _overflow(self):
        return max(0, self.fontMetrics().horizontalAdvance(self._full)
                   - self.width())

    def _restart(self):
        # Overflow is the whole test. Requiring a long string as well meant
        # a 30-character title in a narrow bar sat there truncated,
        # which is exactly when scrolling is worth having.
        if self._overflow() > 0:
            self._timer.start()
        else:
            self._timer.stop()
            self._offset = 0
            self.update()

    def _cycle(self):
        """One full lap: the text, then the gap before it comes round."""
        return self.fontMetrics().horizontalAdvance(self._full) + self.GAP

    def _tick(self):
        if self._paused > 0:
            self._paused -= 1
            return
        if self._overflow() <= 0:
            self._timer.stop()
            return
        self._offset += self.STEP
        if self._offset >= self._cycle():
            # Round again rather than back: reversing means reading the
            # title right to left for half of every cycle.
            self._offset = 0
            self._paused = self.PAUSE_MS // self.INTERVAL
        self.update()

    def paintEvent(self, e):
        if not self._timer.isActive() and self._overflow() <= 0:
            super().paintEvent(e)
            return
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        cycle = self._cycle()
        width = self.fontMetrics().horizontalAdvance(self._full) + 8
        # Drawn twice, a lap apart, so the start is already arriving as the
        # end leaves — a continuous loop rather than a jump back.
        for start in (-self._offset, -self._offset + cycle):
            p.drawText(QRect(start, 0, width, self.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, self._full)
        p.end()


class Segmented(QWidget):
    """A row of mutually exclusive pills. Better than tabs for two to four
    choices: it takes one line, reads as a single control, and doesn't go
    ragged when a label is long."""

    changed = pyqtSignal(int)

    def __init__(self, labels, parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(3)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, label in enumerate(labels):
            b = QPushButton(label)
            b.setProperty("segment", True)
            b.setCheckable(True)
            self.group.addButton(b, i)
            lay.addWidget(b)
        self.group.button(0).setChecked(True)
        self.group.idClicked.connect(self.changed.emit)

    def set_index(self, i):
        btn = self.group.button(i)
        if btn:
            btn.setChecked(True)
            self.changed.emit(i)

    def index(self):
        return self.group.checkedId()


class PillSearch(QLineEdit):
    """A search box that holds tag filters as pills to the left of the text.

    A tag chosen in the sidebar shows up here, so there is one place that
    says what the list is currently filtered by. Backspace at the start of
    the text turns the last pill back into plain text — the tag stops being
    a filter and becomes something you are editing, which is what backspace
    means everywhere else.
    """

    pills_changed = pyqtSignal()
    pill_clicked = pyqtSignal(str, bool)     # name, right_button

    GAP = 5

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.addAction(icon("search", 16, T["text3"]), QLineEdit.LeadingPosition)
        self.setClearButtonEnabled(True)
        self._pills = []            # [(name, colour, state)]
        self._base_margin = self.textMargins()
        self._origin = None
        self._hovered = -1
        self.setMouseTracking(True)

    # -- pills -------------------------------------------------------------

    def pills(self):
        return list(self._pills)

    def set_pills(self, pills):
        if pills == self._pills:
            return
        self._pills = list(pills)
        self._relayout()
        self.pills_changed.emit()

    def _pill_text(self, pill):
        name, _colour, state = pill
        return ("−" if state == "exclude" else "") + name

    def _text_origin(self):
        """Where the caret sits with no pills at all.

        Measured from the widget rather than computed: the frame, the
        padding in the stylesheet and the leading search action all shift
        the text start, and every attempt to add those up by hand landed a
        few pixels out. With no pills the caret *is* the answer, so it is
        read once and kept.
        """
        if self._origin is None and not self._pills:
            self._origin = self.cursorRect().left()
        if self._origin is not None:
            return self._origin
        opt = QStyleOptionFrame()
        self.initStyleOption(opt)
        return self.style().subElementRect(
            QStyle.SE_LineEditContents, opt, self).left()

    MIN_PILL = 46               # px: a couple of letters and the ellipsis

    def _pill_rects(self):
        """Where each pill sits, shrinking them all when room runs out.

        Pills used to take whatever width their text wanted, so a narrow
        field pushed the last ones off the end and clipped them against
        the frame — square edges, half a word. Now the whole row is scaled
        to the space available and the text inside is elided, so a pill
        stays a pill whatever it is holding.
        """
        f = QFont(self.font())
        f.setPixelSize(11)
        fm = QFontMetrics(f)
        left = self._text_origin()
        widths = [fm.horizontalAdvance(self._pill_text(p)) + 16
                  for p in self._pills]

        # Leave room for the caret and a word of typing.
        room = max(0, self.width() - left - 90)
        total = sum(widths) + self.GAP * max(0, len(widths) - 1)
        if total > room and widths:
            spare = room - self.GAP * (len(widths) - 1)
            share = max(self.MIN_PILL, spare // len(widths))
            widths = [min(w, share) for w in widths]

        out = []
        for w in widths:
            out.append(QRect(left, (self.height() - 20) // 2, w, 20))
            left += w + self.GAP
        return out

    def _relayout(self):
        rects = self._pill_rects()
        used = (rects[-1].right() + self.GAP - self._text_origin()
                if rects else 0)
        m = self._base_margin
        self.setTextMargins(m.left() + used, m.top(), m.right(), m.bottom())
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # The frame width can change with the style, so the measured origin
        # is thrown away and read again rather than trusted forever.
        if not self._pills:
            self._origin = None
        self._relayout()

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._pills:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i, (pill, rect) in enumerate(zip(self._pills,
                                             self._pill_rects())):
            _name, colour, state = pill
            included = state == "include"
            base = QColor(colour)
            hot = i == self._hovered
            # Same treatment as the sidebar: a filled pill when the tag is
            # required, an outlined one when it is excluded. Both keep the
            # tag's own hue so it reads as the same object in both places.
            fill = theme.tint_color(colour, (96 if included else 30)
                                    + (26 if hot else 0))
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(rect), 9, 9)
            if not included:
                edge = QColor(base)
                edge.setAlpha(255 if hot else 170)
                p.setPen(QPen(edge, 1))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5),
                                  9, 9)
            f = QFont(self.font())
            f.setPixelSize(11)
            f.setStrikeOut(not included)
            p.setFont(f)
            p.setPen(QColor(T["text"]) if included else base)
            # Elided to the pill, not clipped by it: the rounded edge is
            # drawn either way and the text stops before it.
            p.drawText(rect, Qt.AlignCenter,
                       QFontMetrics(f).elidedText(self._pill_text(pill),
                                                  Qt.ElideRight,
                                                  rect.width() - 10))
        p.end()

    def _pill_at(self, pos):
        for i, rect in enumerate(self._pill_rects()):
            if rect.contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, e):
        hit = self._pill_at(e.pos())
        if hit != self._hovered:
            self._hovered = hit
            # Leave the cursor alone entirely.
            #
            # Setting Qt.ArrowCursor does not mean "default" — it means
            # Qt's own arrow, which ignores the cursor theme and comes
            # out flat black. unsetCursor hands the decision back to the
            # desktop, which is what "no special cursor here" means.
            self.unsetCursor()
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self._hovered != -1:
            self._hovered = -1
            self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        """A pill is a control, not text.

        Clicking one reports it so the sidebar chip can be cycled the same
        way — otherwise the two views of the same filter disagree about
        what a click means.
        """
        hit = self._pill_at(e.pos())
        if hit >= 0 and e.button() in (Qt.LeftButton, Qt.RightButton):
            self.pill_clicked.emit(self._pills[hit][0],
                                   e.button() == Qt.RightButton)
            return
        super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        """Right-clicking a pill means "exclude", not "paste".

        Both halves are needed: the press handler stops the click reaching
        the line edit, and this stops Qt raising the standard edit menu
        from the same event.
        """
        if self._pill_at(e.pos()) >= 0:
            e.accept()
            return
        # On the text: the usual edit menu, with the Paste that works
        # on Wayland (editmenu.py, task 20).
        import editmenu
        editmenu.show_line_menu(self, e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.RightButton and self._pill_at(e.pos()) >= 0:
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if (e.key() == Qt.Key_Backspace and self.cursorPosition() == 0
                and not self.hasSelectedText() and self._pills):
            # Removes the filter outright rather than turning it into text
            # to be deleted a second time: the pill is one thing, and one
            # backspace should be enough to be rid of it.
            name = self._pills[-1][0]
            self._pills = self._pills[:-1]
            self._hovered = -1
            self._relayout()
            self.pills_changed.emit()
            return
        super().keyPressEvent(e)


def search_field(placeholder="Stations, artists, or tag:80s"):
    """QLineEdit with the magnifier the QSS already reserves padding for."""
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.addAction(icon("search", 16, T["text3"]), QLineEdit.LeadingPosition)
    e.setClearButtonEnabled(True)
    return e


def placeholder_art(size, seed_color=None, name=""):
    """What an absent cover looks like: the station's own monogram.

    It used to be a pair of quavers drawn on a tinted field. The notes
    are gone — the monogram already says which station this is, in the
    colour the list uses for it, and a second symbol on top only
    competed with it.

    With no name to go on, it is the tinted field alone.
    """
    from stationlist import monogram

    if name:
        return monogram(name, size)

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    if seed_color:
        tint = QColor(seed_color)
        p.setBrush(QColor.fromHsl(
            tint.hslHue() if tint.hslHue() >= 0 else 0,
            max(20, int(tint.hslSaturation() * 0.55)), 38))
    else:
        p.setBrush(QColor(T["bg2"]))
    radius = max(4.0, size * 0.12)
    p.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.end()
    return pm

def _old_placeholder_art(size, seed_color="#3a3a41"):
    pm = QPixmap(size, size)
    pm.fill(QColor(seed_color))
    return pm


def cover(pixmap, width, height, radius=0):
    """Fill a box exactly: expand to cover, centre-crop, then round.

    Scaling with KeepAspectRatioByExpanding alone returns something
    *larger* than the box in one dimension. The label then clips the
    overflow against its own edge — so the picture is cropped off-centre
    and the rounded corners end up outside the visible area, which is why
    the docked artwork had square corners and a shaved edge.
    """
    if pixmap is None or pixmap.isNull() or width <= 0 or height <= 0:
        return pixmap
    scaled = pixmap.scaled(width, height, Qt.KeepAspectRatioByExpanding,
                           Qt.SmoothTransformation)
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, (scaled.height() - height) // 2)
    cropped = scaled.copy(x, y, min(width, scaled.width()),
                          min(height, scaled.height()))
    return rounded(cropped, radius) if radius else cropped


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
# Sidebar
# --------------------------------------------------------------------------

class Sidebar(QWidget):
    # The dock changed size, so whatever is in it needs redrawing at the
    # new one.
    art_resized = pyqtSignal()
    width_changed = pyqtSignal(int)

    """Nav destinations plus the tag list. Collapses to an icon rail."""

    navigated = pyqtSignal(str)
    extra_hidden = pyqtSignal(str)
    classic_requested = pyqtSignal()
    visualizer_requested = pyqtSignal()
    tag_toggled = pyqtSignal(str, bool)
    # The pencil beside "Tags": the tag manager, every tag to add,
    # rename and remove (task 87; until build 79 it opened the playing
    # station's Tags… window).
    tags_edit_requested = pyqtSignal()
    # name, state: "off" | "include" | "exclude"
    tag_state_changed = pyqtSignal(str, str)

    DESTINATIONS = [
        ("stations", "stations", "Saved Frequencies"),
        ("discover", "plus",     "Add Frequency…"),
        ("history",  "history",  "History"),
        ("options",  "gear",     "Settings"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(T["sidebar_w"])
        self._collapsed = False
        self._buttons = {}
        self._tag_buttons = []
        self._tag_states = {}

        lay = QVBoxLayout(self)
        # 6, not 12: the artwork is 64 in a 76 bar, so 12 top and bottom
        # left it 52 to live in and cropped the bottom off it.
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)

        for key, ico, label in self.DESTINATIONS:
            b = QPushButton(f"  {label}")
            b.setProperty("nav", True)
            b.setIcon(icon(ico, 18, T["text2"]))
            b.setIconSize(QSize(18, 18))
            b.setCheckable(True)
            b.setToolTip(label)
            b.clicked.connect(lambda _, k=key: self._select(k))
            b.setAttribute(Qt.WA_Hover, True)
            b.setContextMenuPolicy(Qt.PreventContextMenu)
            b.installEventFilter(self)
            lay.addWidget(b)
            self._buttons[key] = (b, label)

        # Not a destination: it swaps the whole window for the skinned
        # stack rather than changing what this one is showing, so it is not
        # checkable and never appears selected.
        # Everything below the destinations. Each can be hidden from its
        # own right-click menu and brought back from Settings — a rail
        # with six things nobody uses is worse than one with two they do.
        self.extras = {}

        self.classic_btn = QPushButton("  Winamp")
        self.classic_btn.setProperty("nav", True)
        self.classic_btn.setIcon(icon("winamp", 18, T["text2"]))
        self.classic_btn.setIconSize(QSize(18, 18))
        self.classic_btn.setToolTip("Switch to the classic skinned player")
        self.classic_btn.clicked.connect(self.classic_requested.emit)
        lay.addWidget(self.classic_btn)
        self._register_extra("winamp", self.classic_btn, "Winamp")

        self.vis_btn = QPushButton("  Visualizer")
        self.vis_btn.setProperty("nav", True)
        self.vis_btn.setIcon(icon("visualizer", 18, T["text2"]))
        self.vis_btn.setIconSize(QSize(18, 18))
        self.vis_btn.setToolTip("Open the visualizer window")
        self.vis_btn.clicked.connect(self.visualizer_requested.emit)
        lay.addWidget(self.vis_btn)
        self._register_extra("visualizer", self.vis_btn, "Visualizer")

        self.schedule_btn = QPushButton("  Schedule")
        self.schedule_btn.setProperty("nav", True)
        self.schedule_btn.setIcon(icon("schedule", 18, T["text2"]))
        self.schedule_btn.setIconSize(QSize(18, 18))
        self.schedule_btn.setCheckable(True)
        self.schedule_btn.setToolTip("What is on, and when")
        self.schedule_btn.clicked.connect(lambda: self._select("schedule"))
        lay.addWidget(self.schedule_btn)
        self._register_extra("schedule", self.schedule_btn, "Schedule")

        self.help_btn = QPushButton("  Help")
        self.help_btn.setProperty("nav", True)
        self.help_btn.setIcon(icon("help", 18, T["text2"]))
        self.help_btn.setIconSize(QSize(18, 18))
        self.help_btn.setCheckable(True)
        self.help_btn.setToolTip("Dog jokes, for now")
        self.help_btn.clicked.connect(lambda: self._select("help"))
        lay.addWidget(self.help_btn)
        self._register_extra("help", self.help_btn, "Help")


        # The "Tags" heading, with a pencil beside it (task 79). The row
        # is what hides when the rail collapses (_apply_collapse).
        self._tag_header = QWidget()
        head = QHBoxLayout(self._tag_header)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(0)
        self._tag_title = QLabel("Tags")
        self._tag_title.setProperty("role", "section")
        head.addWidget(self._tag_title)
        self.tags_edit_btn = QToolButton()
        self.tags_edit_btn.setObjectName("TagsEdit")
        self.tags_edit_btn.setIcon(icon("edit", 14, T["text3"]))
        self.tags_edit_btn.setIconSize(QSize(14, 14))
        self.tags_edit_btn.setAutoRaise(True)
        self.tags_edit_btn.setCursor(Qt.PointingHandCursor)
        self.tags_edit_btn.setToolTip("Edit tags: add, rename and remove "
                                      "them")
        self.tags_edit_btn.clicked.connect(self.tags_edit_requested.emit)
        # Level with the word: the heading's padding puts its text at the
        # bottom of its box.
        head.addWidget(self.tags_edit_btn, 0, Qt.AlignBottom)
        head.addStretch(1)
        lay.addWidget(self._tag_header)

        # The tags scroll rather than growing without limit: the artwork
        # dock underneath is reserved space, and a long tag list used to
        # push everything below it off the bottom.
        self._tag_scroll = QScrollArea()

        self._tag_scroll.setWidgetResizable(True)
        self._tag_scroll.setFrameShape(QFrame.NoFrame)
        self._tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tag_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        self._tag_box = QVBoxLayout(holder)
        self._tag_box.setSpacing(4)
        # The bar comes and goes; the pills do not move either way.
        #
        # Pinning it on kept the widths steady and left a groove sitting
        # there under a handful of tags, which is furniture for nothing.
        # The pills stay still because the scrolled widget is given a
        # fixed width that already allows for the bar — so whether the
        # bar is up or not, the space left for a pill is the same.
        self._tag_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._tag_box.setContentsMargins(6, 0, 8, 0)
        self._tag_box.addStretch(1)
        self._tag_scroll.setWidget(holder)
        lay.addWidget(self._tag_scroll, 1)

        self.tags_btn = QPushButton("  Tags")
        self.tags_btn.setProperty("nav", True)
        self.tags_btn.setIcon(icon("tag", 18, T["text2"]))
        self.tags_btn.setIconSize(QSize(18, 18))

        self.tags_btn.clicked.connect(self.show_tag_menu)
        self.tags_btn.setVisible(False)
        lay.addWidget(self.tags_btn)

        # Where the artwork goes when it is expanded. Always reserved, so
        # the layout never jumps when it arrives or leaves.
        # Collapsed it takes no height at all, so the tag list gets the
        # room back rather than scrolling past a permanent empty square.
        self.art_dock = ClickableLabel()
        self.art_dock.setObjectName("ArtDock")
        self.art_dock.setAlignment(Qt.AlignCenter)
        self.art_dock.setFixedHeight(0)
        # Nothing behind the picture.
        #
        # The rounded pixmap has transparent corners, so anything opaque
        # under the label fills them back in and the artwork looks square
        # however carefully it was drawn.
        self.art_dock.setAttribute(Qt.WA_TranslucentBackground, True)
        # No frame and no margin, so the contents rectangle is the widget
        # and there is nothing left to clip against.
        self.art_dock.setStyleSheet(
            "background: transparent; border: 0; padding: 0; margin: 0;")
        self.art_dock.setContentsMargins(0, 0, 0, 0)
        self.art_dock.setScaledContents(False)
        # It reports its own resizes, which is the only way to catch the
        # dock opening inside a layout pass with the sidebar unchanged.
        self.art_dock.installEventFilter(self)
        lay.addWidget(self.art_dock)

    # Follows the sidebar rather than the theme constant: dragged wider
    # or narrower, the artwork is still square and still fits.
    ART_DOCK_H = property(
        lambda self: max(96, min(self.width() - 24, 320)))

    def set_art_dock_open(self, open_):
        self.art_dock.setFixedHeight(self.ART_DOCK_H if open_ else 0)

    def set_new_badge(self, count):
        """A green "New!" on Saved Frequencies while imports are unplayed.

        A small label parented to the button, not a replaced paintEvent.
        Assigning paintEvent on an instance looks like an override and is
        not one — Qt dispatches through the class, so the badge was never
        drawn. A child widget is simply painted with its parent.
        """
        button = self._buttons.get("stations", (None,))[0]
        if button is None:
            return
        badge = getattr(button, "_new_badge", None)
        if badge is None:
            badge = QLabel("New!", button)
            badge.setAlignment(Qt.AlignCenter)
            badge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            badge.setStyleSheet(
                "background: #2e9e5b; color: #ffffff; border-radius: 8px;"
                " font-size: 10px; font-weight: 700; padding: 0 6px;")
            button._new_badge = badge
        badge.setFixedHeight(16)
        badge.adjustSize()
        badge.setFixedHeight(16)

        # Right-aligned, and hidden when the rail is collapsed to icons —
        # there is no room beside an icon, and a badge on top of one
        # would hide what the button is.
        badge.move(button.width() - badge.width() - 10,
                   (button.height() - badge.height()) // 2)
        showing = bool(count) and not self._collapsed
        badge.setVisible(showing)
        button._new_count = int(count or 0)

        # The label gives way to it, rather than running underneath.
        #
        # The rail is narrow, so "Saved Frequencies" already fills the
        # button; laid on top, the badge covered the end of the word. The
        # text is shortened to what fits beside it instead.
        full = getattr(button, "_full_label", None)
        if full is None:
            full = button.text().strip()
            button._full_label = full
        if showing:
            from PyQt5.QtGui import QFontMetrics
            room = max(20, badge.x() - 44)
            button.setText("  " + QFontMetrics(button.font()).elidedText(
                full, Qt.ElideRight, room))
        elif not self._collapsed:
            button.setText(f"  {full}")

    def choose_start_page(self):
        """The destination the app opens on.

        A line that belongs in the constructor had drifted into
        set_art_dock_open, which runs every time the artwork folds — so
        collapsing the window threw you back to Saved Frequencies from
        whatever page you were reading.
        """
        self._select("stations")

    # -- tags --------------------------------------------------------------

    def set_tags(self, tags):
        """tags: list of (name, hex_color, count)."""
        # Size the column before the pills go in, so the first layout is
        # already the final one — otherwise the first paint is measured
        # against a width that changes a moment later.
        self._fit_tag_holder()
        # Hidden on purpose before they leave. A pill put in the layout
        # gets a queued "show me" from Qt; if the pills are rebuilt again
        # before that runs (two reloads in one go, as after the Twitch live
        # re-check), the old pill is shown as a window of its own, one per
        # tag (task 148). An explicit hide() cancels that show.
        for b in self._tag_buttons:
            b.hide()
            b.setParent(None)
            b.deleteLater()
        self._tag_buttons.clear()

        for name, colour, count in tags:
            b = TagPill(f"{name}   {count}")
            b.setProperty("pill", True)
            b.tag_name = name
            b.tag_colour = colour
            b.tag_count = count
            # Allowed to shrink, and its text elided to whatever room it
            # has.
            #
            # Without this the pill demands the width of its full label,
            # so the scrolled widget grows wider than the viewport and
            # every pill is clipped against the right edge — worse when
            # the scrollbar appears and takes another fifteen pixels.
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.setAttribute(Qt.WA_Hover, True)
            b.enterEvent = lambda _e, w=b: self._tag_scroll_start(w)
            b.leaveEvent = lambda _e, w=b: self._tag_scroll_stop(w)
            b.setMinimumWidth(40)
            b.full_label = b.text()
            # No tooltip on a tag.
            #
            # The pill already says the tag's name, and a tip explaining
            # left- and right-click appeared every time the pointer
            # crossed the rail on its way to the list.
            b.clicked.connect(lambda _c, n=name: self._cycle_tag(n, "include"))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            # Right-click strikes the tag through and excludes it, which is
            # the other half of building a playlist out of tags: "80s but
            # not the ones I have heard too often".
            b.customContextMenuRequested.connect(
                lambda _p, n=name: self._cycle_tag(n, "exclude"))
            self._tag_box.insertWidget(self._tag_box.count() - 1, b)
            self._tag_buttons.append(b)
            self._paint_tag(b)
        self._tag_data = list(tags)
        self._elide_tags()
        self._apply_collapse()

    def _cycle_tag(self, name, target):
        """Clicking the same way twice turns the tag off again."""
        current = self._tag_states.get(name, "off")
        state = "off" if current == target else target
        self.set_tag_state(name, state)

    def set_tag_state(self, name, state):
        if self._tag_states.get(name, "off") == state:
            return
        if state == "off":
            self._tag_states.pop(name, None)
        else:
            self._tag_states[name] = state
        for b in self._tag_buttons:
            if b.tag_name == name:
                self._paint_tag(b)
        self.tag_state_changed.emit(name, state)
        # Kept so anything still listening for the old include-only signal
        # keeps working.
        self.tag_toggled.emit(name, state == "include")

    def tag_states(self):
        return dict(self._tag_states)

    def _paint_tag(self, b):
        state = self._tag_states.get(b.tag_name, "off")
        colour, name, count = b.tag_colour, b.tag_name, b.tag_count
        # Drawn, not a font flag.
        #
        # setStrikeOut is on or off with nothing in between, so excluding
        # a tag happened between one frame and the next. A line the pill
        # draws itself can be given a length, and a length can be
        # animated.
        if hasattr(b, "set_struck"):
            b.set_struck(state == "exclude")
        else:
            font = b.font()
            font.setStrikeOut(state == "exclude")
            b.setFont(font)
        if state == "include":
            bg, fg = theme.tint(colour, 80), T["text"]
        elif state == "exclude":
            # Dimmed and struck through: present, but plainly working
            # against the list rather than for it.
            bg, fg = theme.tint(colour, 18), T["text3"]
        else:
            bg, fg = theme.tint(colour, 34), colour
        # The hover rule was missing entirely, so the sidebar chips were
        # the one place a tag did not react to the pointer.
        hover = theme.tint(colour, {"include": 108, "exclude": 44}.get(
            state, 62))
        b.setStyleSheet(
            f"QPushButton[pill='true'] {{ background: {bg}; color: {fg};"
            f" text-align: left; padding: 3px 10px; }}"
            f"QPushButton[pill='true']:hover {{ background: {hover}; }}")
        b.setText(f"{name}   {count}")

    # -- collapse ----------------------------------------------------------

    def set_collapsed(self, collapsed: bool):
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        width = T["rail_w"] if collapsed else T["sidebar_w"]
        # Bounds, and then a request — not a fixed width.
        #
        # setFixedWidth pins minimum and maximum together, and a splitter
        # will not move a child it cannot resize. The first collapse took
        # because the splitter had not been given sizes yet; after that
        # its own numbers won, and nothing moved again.
        # The minimum stays at the rail, always.
        #
        # Raising it to the full width raised the window's minimum with
        # it — so once expanded, the window could no longer be made narrow
        # enough to ask for a collapse. It worked once and then the app
        # refused to get small. Only the maximum moves; the width itself
        # is asked for through the splitter.
        self.setMinimumWidth(T["rail_w"])
        self.setMaximumWidth(width)
        self._apply_collapse()
        self.width_changed.emit(width)

    def set_live_badge(self, on):
        """A red "LIVE NOW" on Schedule while a scheduled show is on the
        air (task 131); a small red dot when the rail is collapsed.

        A label inside a layout on the button, so it follows the button's
        width without being moved by hand.
        """
        button = getattr(self, "schedule_btn", None)
        if button is None:
            return
        badge = getattr(button, "_live_badge", None)
        if badge is None:
            badge = QLabel("LIVE NOW", button)
            badge.setAlignment(Qt.AlignCenter)
            badge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            lay = QHBoxLayout(button)
            lay.addStretch(1)
            lay.addWidget(badge)
            button._live_badge = badge
        button._live_on = bool(on)
        lay = button.layout()
        if self._collapsed:
            badge.setText("")
            badge.setFixedSize(10, 10)
            lay.setContentsMargins(0, 6, 6, 0)
            lay.setAlignment(badge, Qt.AlignTop | Qt.AlignRight)
            badge.setStyleSheet("background: #d93030; border-radius: 5px;")
        else:
            badge.setText("LIVE NOW")
            badge.setFixedHeight(16)
            badge.setMinimumWidth(0)
            badge.setMaximumWidth(16777215)
            lay.setContentsMargins(0, 0, 10, 0)
            lay.setAlignment(badge, Qt.AlignVCenter | Qt.AlignRight)
            badge.setStyleSheet(
                "background: #d93030; color: #ffffff; border-radius: 8px;"
                " font-size: 10px; font-weight: 700; padding: 0 6px;")
            badge.adjustSize()
        lay.invalidate()
        badge.setVisible(bool(on))
        button.setToolTip("Live now: a scheduled show is on the air" if on
                          else "What is on, and when")

    def _reposition_new_badge(self):
        """Keep the badge at the button's right edge, or hide it."""
        button = self._buttons.get("stations", (None,))[0]
        if button is not None and getattr(button, "_new_badge", None):
            self.set_new_badge(getattr(button, "_new_count", 0))

    def _apply_collapse(self):
        QTimer.singleShot(0, self._reposition_new_badge)
        live = getattr(getattr(self, "schedule_btn", None), "_live_on", None)
        if live is not None:
            self.set_live_badge(live)
        for key, (b, label) in self._buttons.items():
            b.setText("" if self._collapsed else f"  {label}")
        self.classic_btn.setText("" if self._collapsed else "  Winamp")
        self.vis_btn.setText("" if self._collapsed else "  Visualizer")
        self._tag_header.setVisible(not self._collapsed)
        self._tag_scroll.setVisible(not self._collapsed)
        self.art_dock.setVisible(not self._collapsed)
        # Collapsed, the tags become a button; expanded, the list is back
        # and the button would only duplicate it.
        self.tags_btn.setVisible(self._collapsed)
        for b in self._tag_buttons:
            b.setVisible(not self._collapsed)

    def _register_extra(self, key, button, label):
        """A rail entry that can be hidden, with the menu that hides it."""
        self.extras[key] = (button, label)
        button.setAttribute(Qt.WA_Hover, True)
        button.setContextMenuPolicy(Qt.CustomContextMenu)
        button.customContextMenuRequested.connect(
            lambda pos, k=key, b=button: self._extra_menu(k, b, pos))

    def _extra_menu(self, key, button, pos):
        menu = QMenu(self)
        menu.addAction(f"Hide {self.extras[key][1]}",
                       lambda: self.hide_extra(key))
        menu.addSeparator()
        note = menu.addAction("Bring it back in Settings")
        note.setEnabled(False)
        open_menu(menu, button.mapToGlobal(pos))

    def _tag_menu_exclude(self, menu, pos):
        """Right-clicking an entry excludes that tag outright."""
        act = menu.actionAt(pos)
        name = act.data() if act is not None else None
        if not name:
            return
        state = self.tag_states().get(name, "off")
        self._cycle_tag(name, "off" if state == "exclude" else "exclude")
        menu.close()

    def hide_extra(self, key):
        button = self.extras.get(key, (None, None))[0]
        if button is not None:
            button.setVisible(False)
        self.extra_hidden.emit(key)

    def apply_extras(self, hidden):
        """Which rail entries are switched off, from settings."""
        for key, (button, _label) in self.extras.items():
            button.setVisible(key not in set(hidden or ()))

    SCROLL_MS = 90              # ms per step, as the player bar uses

    def _tag_scroll_start(self, button):
        """Loop a truncated pill's text while the pointer is on it.

        The elision is what keeps the pills inside the panel, but it also
        hides the end of every long tag. Scrolling on hover is how the
        rest of the app solves the same problem, so it is how this one
        does too.
        """
        label = getattr(button, "full_label", "")
        if not label or button.text().rstrip("…") == label:
            return                      # nothing was cut off
        button._scroll_at = 0
        timer = getattr(button, "_scroll_timer", None)
        if timer is None:
            timer = QTimer(button)
            timer.setInterval(self.SCROLL_MS)
            timer.timeout.connect(lambda b=button: self._tag_scroll_step(b))
            button._scroll_timer = timer
        timer.start()

    def _tag_scroll_step(self, button):
        label = getattr(button, "full_label", "")
        room = max(4, len(button.text()))
        # Drawn twice with a gap, so the end runs into the beginning
        # rather than the text bouncing off the edge.
        loop = label + "   ·   "
        button._scroll_at = (button._scroll_at + 1) % len(loop)
        button.setText((loop + loop)[button._scroll_at:
                                     button._scroll_at + room])

    def _tag_scroll_stop(self, button):
        timer = getattr(button, "_scroll_timer", None)
        if timer is not None:
            timer.stop()
        self._elide_tags()

    TAG_BAR_ROOM = 14           # what a scrollbar takes when it appears

    def _fit_tag_holder(self):
        """Hold the tag column to one width, bar or no bar."""
        holder = self._tag_box.parentWidget()
        if holder is None:
            return
        room = max(40, self._tag_scroll.width() - self.TAG_BAR_ROOM)
        if holder.width() != room:
            holder.setFixedWidth(room)

    def _elide_tags_soon(self):
        """Re-measure the pills when the resizing stops."""
        timer = getattr(self, "_elide_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(70)
            timer.timeout.connect(self._elide_tags)
            self._elide_timer = timer
        timer.start()

    def _elide_tags(self):
        """Trim each pill's text to the width it actually has."""
        # Measured from the scroll area, not its viewport.
        #
        # The viewport shrinks when the scrollbar appears, so the pills
        # were elided harder the moment the list got long — the same tag
        # readable with nine tags and cut off with ten. The margin
        # already reserves the bar's width, so the outer width is the
        # honest number and it does not change.
        room = max(40, self._tag_scroll.width() - self.TAG_BAR_ROOM
                   - self._tag_box.contentsMargins().left()
                   - self._tag_box.contentsMargins().right())
        for button in getattr(self, "_tag_buttons", []):
            label = getattr(button, "full_label", button.text())
            metrics = QFontMetrics(button.font())
            button.setText(metrics.elidedText(label, Qt.ElideRight,
                                              room - 18))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.art_dock.height() > 0:
            self.art_dock.setFixedHeight(self.ART_DOCK_H)
            self.art_resized.emit()
        # Re-measuring every pill on every pixel of a drag is the
        # expensive half of a resize: one QFontMetrics elide per tag,
        # times a hundred events a second. Once the drag settles is
        # soon enough — the pills keep their old text until then, which
        # nobody sees at drag speed.
        self._fit_tag_holder()
        self._elide_tags_soon()

    def show_tag_menu(self):
        """The tag list as a menu, for when the rail has no room for one.

        Collapsed, the sidebar is icons and nothing else — but the tags are
        how the library gets navigated, so they have to stay reachable.
        """
        from PyQt5.QtWidgets import QMenu
        menu = QMenu(self)
        states = self.tag_states()
        for name, colour, count in getattr(self, "_tag_data", []):
            state = states.get(name, "off")
            # The same three states the pills have: off, included, and
            # excluded with a minus. Clicking cycles forward, right-click
            # jumps straight to excluded — which is what the pills do, and
            # this menu is standing in for them.
            mark = {"include": "✓  ", "exclude": "−  "}.get(state, "    ")
            act = menu.addAction(f"{mark}{name}   {count}")
            act.setData(name)
            nxt = {"off": "include", "include": "exclude",
                   "exclude": "off"}[state]
            act.triggered.connect(
                lambda _c=False, n=name, s=nxt: self._cycle_tag(n, s))
        menu.setContextMenuPolicy(Qt.CustomContextMenu)
        menu.customContextMenuRequested.connect(
            lambda pos, mn=menu: self._tag_menu_exclude(mn, pos))
        if menu.isEmpty():
            act = menu.addAction("No tags yet")
            act.setEnabled(False)
        # The collapsed rail's stand-in for the pencil beside "Tags".
        menu.addSeparator()
        menu.addAction(icon("edit", 16, T["text2"]), "Edit tags…",
                       self.tags_edit_requested.emit)
        open_menu(menu, self.tags_btn.mapToGlobal(
            self.tags_btn.rect().topRight()))

    def eventFilter(self, obj, event):
        """Two jobs, in one method.

        A class can only have one eventFilter — a second definition
        silently replaces the first, which is how the dock's half of this
        went missing without any error to show for it.

        The dock reports its own resizes, because waiting for the sidebar
        to resize misses the case that matters: the dock changes height
        when it opens, inside a layout pass, with the sidebar unchanged
        throughout.

        And the right button is swallowed on the rail: Qt gives any button
        a pressed appearance for whichever button went down on it, so
        right-clicking a destination lit it up as though it had been
        chosen, for a menu that never appears.
        """
        if obj is getattr(self, "art_dock", None) and \
                event.type() == QEvent.Resize:
            self.art_resized.emit()
        # Asked of the event itself, not assumed from its type.
        #
        # On Wayland a right-click can reach this filter as a
        # context-menu event while still reporting a mouse-button type,
        # and a context-menu event has no button() — so every right-click
        # on the sidebar raised, twice, and filled the log.
        button = getattr(event, "button", None)
        if callable(button) and event.type() in (
                QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                QEvent.MouseButtonDblClick):
            if button() == Qt.RightButton and obj is not None:
                if obj in [b for b, _ in self._buttons.values()]:
                    return True
        return super().eventFilter(obj, event)

    def _select(self, key):
        # Schedule is a page like the others but is not in DESTINATIONS,
        # so its button has to be kept in step by hand.
        if hasattr(self, "schedule_btn"):
            self.schedule_btn.setChecked(key == "schedule")
        if hasattr(self, "help_btn"):
            self.help_btn.setChecked(key == "help")
        for k, (b, _) in self._buttons.items():
            b.setChecked(k == key)
        self.navigated.emit(key)


# --------------------------------------------------------------------------
# Player bar
# --------------------------------------------------------------------------

class PlayerBar(QWidget):
    """The transport. Painted with a lit top edge so the bar reads as its
    own surface however dark the rest of the window is."""

    GLOW = 7                # px the accent bleeds down from the edge

    """Persistent bottom bar: three columns, transport optically centered."""

    play_toggled = pyqtSignal(bool)
    skipped = pyqtSignal(int)          # -1 previous, +1 next
    art_clicked = pyqtSignal()
    fullscreen_requested = pyqtSignal()
    sleep_requested = pyqtSignal()
    station_menu_requested = pyqtSignal(QPoint)
    shazam_toggled = pyqtSignal(bool)
    volume_changed = pyqtSignal(int)
    favourited = pyqtSignal(bool)
    eq_requested = pyqtSignal()
    shuffled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.setFixedHeight(T["bar_h"])
        self._playing = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 16, 10)
        lay.setSpacing(16)

        lay.addWidget(self._build_track(), 1)
        lay.addWidget(self._build_transport(), 0, Qt.AlignCenter)
        lay.addWidget(self._build_actions(), 1)

    # -- left: artwork and what's playing ---------------------------------

    def _build_track(self):
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.setAlignment(Qt.AlignVCenter)

        self.art = ArtThumb(T["art_sm"])
        self.art.setToolTip("Show the artwork larger")
        self.art.clicked.connect(self.art_clicked.emit)
        row.addWidget(self.art)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        # Three lines, most specific first: the song, who made it, and
        # where it came from. Artist and station were sharing one line
        # joined by a dash, which put the least useful part of the readout
        # in the same weight as the most.
        self.song = Marquee("Nothing playing")
        self.song.setProperty("role", "title")
        self.artist = Marquee("")
        self.artist.setProperty("role", "secondary")
        self.meta = Marquee("Pick a station to start")
        self.meta.setProperty("role", "tertiary")
        # A fixed-width block, so the star beside it sits in the same place
        # whatever is playing. The labels scroll inside it rather than
        # resizing it: a control that moves depending on the length of a
        # song title is a control you have to look for every time.
        # Fixed width so the star never moves, but narrow enough that the
        # left group plus the transport plus the right group still fit:
        # at 260 the last control was being clipped off the edge whenever
        # the artwork was in the bar as well.
        width = T.get("nowplaying_w", 260) - 44
        block = QWidget()
        block.setLayout(text)
        block.setFixedWidth(width)
        for l in (self.song, self.artist, self.meta):
            l.setFixedWidth(width)
            text.addWidget(l)

        # A fourth line for things about the app rather than the station —
        # a sign-in, a library fetch, a failed lookup. It used to be the
        # lyrics column's job, which meant an iBroadcast message replaced
        # the lyrics.
        self.notice = QLabel("")
        self.notice.setProperty("role", "notice")
        self.notice.setFixedWidth(width)
        self.notice.setVisible(False)
        text.addWidget(self.notice)

        row.addWidget(block, 0)

        # Right-clicking what is playing gets at the same things the list
        # offers, without going back to find the row.
        #
        # The block as well as the labels inside it: the labels are fixed
        # width and rarely fill their line, so a click landing in the gap
        # beside a short title hit the container and nothing happened. The
        # artist line was missing from this list entirely.
        for widget in (self.art, block, self.song, self.artist, self.meta):
            widget.setContextMenuPolicy(Qt.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget: self.station_menu_requested.emit(
                    w.mapToGlobal(pos)))

        # Songs get favourited, stations don't -- a station you like is one
        # you keep in the list, which is a different gesture entirely.
        self.fav_btn = tool_button("star", "Favorite this song", 20,
                                   checkable=True)
        self.fav_btn.toggled.connect(self._on_fav)
        row.addWidget(self.fav_btn, 0, Qt.AlignVCenter)

        # Beside the star, and inside the same row — so it travels with the
        # song text and the star when the artwork expands, rather than
        # staying put while everything around it moves.
        self.hourglass = Hourglass(20)
        # Space kept whether or not it is showing, so the artwork beside
        # it is not squeezed the moment an ad starts — the row was giving
        # the glass its width by taking it off the picture.
        self.hourglass.setFixedSize(20, 20)
        row.addWidget(self.hourglass, 0, Qt.AlignVCenter)

        row.addStretch(1)
        return w

    # -- centre: transport -------------------------------------------------

    def _build_transport(self):
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.prev_btn = tool_button("prev", "Previous station", 26)
        self.prev_btn.clicked.connect(lambda: self.skipped.emit(-1))

        self.play_btn = QToolButton()
        self.play_btn.setProperty("primary", True)
        self.play_btn.setIcon(icon("play", 20, T["on_accent"]))
        self.play_btn.setIconSize(QSize(20, 20))
        self.play_btn.setToolTip("Play")
        self.play_btn.clicked.connect(self._toggle_play)

        self.next_btn = tool_button("next", "Next station", 26)
        self.next_btn.clicked.connect(lambda: self.skipped.emit(1))

        for b in (self.prev_btn, self.play_btn, self.next_btn):
            row.addWidget(b)
        return w

    def _toggle_play(self):
        """Pressed. Morph, then report.

        This used to set _playing itself and paint the icon at whatever
        step it happened to be on. So the animation never ran from here,
        and worse: the state was already flipped by the time the player
        called back with set_playing, which then saw nothing to change and
        returned. The morph could not run from either direction.
        """
        self.set_playing(not self._playing)
        self.play_toggled.emit(self._playing)

    # -- right: secondary actions -----------------------------------------

    def _build_actions(self):
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addStretch(1)

        # Identification sits beside shuffle because both are "how this
        # station behaves" rather than transport.
        self.shazam_btn = tool_button("shazam", "Identify songs on this "
                                      "station", 22, checkable=True)
        self.shazam_btn.toggled.connect(self._on_shazam)

        self.shuffle_btn = tool_button("shuffle_0", "Shuffle stations", 22,
                                       checkable=True)
        self._shuffle_step = 0
        self.shuffle_btn.toggled.connect(self._on_shuffle)

        self.full_btn = tool_button("fullscreen", "Full screen", 22)
        self.full_btn.clicked.connect(self.fullscreen_requested.emit)
        # The corners move apart as you point at it: the button does what
        # the picture is doing.
        self.full_btn.enterEvent = lambda e: self._expand_full(True)
        self.full_btn.leaveEvent = lambda e: self._expand_full(False)
        self._full_step = 0

        # The sleep timer, between shuffle and the station's settings.
        #
        # It has always been there — a menu entry and a setting — with
        # nothing on screen to say whether it was running. A button that
        # takes the accent while it counts down answers that at a glance.
        self.sleep_btn = tool_button("timer", "Sleep timer", 22)
        self.sleep_btn.clicked.connect(self.sleep_requested.emit)
        self._sleep_on = False
        # More frames than the equaliser: the hands turn through a real
        # angle, and five steps of that looks like ticking, not sweeping.
        _animate_glyph(
            self.sleep_btn, "timer", 22,
            steps=tuple(range(0, 101, 10)), ms=24,
            colour=lambda: T["accent"] if self._sleep_on else T["text2"])

        self.eq_btn = tool_button("eq", "Equalizer for this station", 22)
        _animate_glyph(self.eq_btn, "eq", 22)
        self.eq_btn.clicked.connect(self.eq_requested.emit)

        # Shuffle, then the sleep timer, then the station's settings.
        # It was added to the row before this loop ran, which put it
        # ahead of everything instead of between the two it belongs to.
        for b in (self.shazam_btn, self.shuffle_btn, self.sleep_btn,
                  self.eq_btn, self.full_btn):
            row.addWidget(b)

        row.addSpacing(8)
        self.vol_icon = tool_button("volume3", "Mute", 20)
        self.vol_icon.clicked.connect(self._toggle_mute)
        self._muted = False
        self._premute = None
        row.addWidget(self.vol_icon)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(68)
        self.vol.setFixedWidth(96)
        self.vol.valueChanged.connect(self.volume_changed.emit)
        self.vol.valueChanged.connect(self._paint_volume)
        self._paint_volume(self.vol.value())
        row.addWidget(self.vol)
        return w

    SHINE_FRAMES = 4

    def _shine(self):
        """A few frames of rays, then the plain star.

        Favouriting has no other feedback — the icon is already lit for a
        song you starred earlier — so the moment of pressing it needs its
        own signal.
        """
        self._shine_step = 0
        if getattr(self, "_shine_timer", None) is None:
            self._shine_timer = QTimer(self)
            self._shine_timer.setInterval(55)
            self._shine_timer.timeout.connect(self._shine_tick)
        self._shine_timer.start()
        self._shine_tick()

    def _shine_tick(self):
        self._shine_step += 1
        if self._shine_step > self.SHINE_FRAMES:
            self._shine_timer.stop()
            self.fav_btn.setIcon(icon(
                "star_on" if self.fav_btn.isChecked() else "star", 20,
                T["accent"] if self.fav_btn.isChecked() else T["text2"]))
            return
        self.fav_btn.setIcon(icon(f"star_shine{self._shine_step}", 20,
                                  T["accent"]))

    def _on_fav(self, on):
        self.fav_btn.setIcon(
            icon("star_on" if on else "star", 20,
                 T["accent"] if on else T["text2"]))
        if on:
            self._shine()
        self.favourited.emit(on)

    def set_sleep_active(self, on):
        """Light the timer while it is counting down."""
        self._sleep_on = bool(on)
        self.sleep_btn.setIcon(icon("timer", 22,
                                    T["accent"] if on else T["text2"]))

    def refresh_accent(self):
        """Re-tint the icons that are drawn in the accent color.

        Shuffle was missing from this: it is painted in the accent when
        on, but nothing repainted it, so it kept the color it was lit in
        until the next time it was toggled.
        """
        self._paint_shazam(self.shazam_btn.isChecked())
        on = self.shuffle_btn.isChecked()
        self.shuffle_btn.setIcon(
            icon(f"shuffle_{getattr(self, '_shuffle_step', 4 if on else 0)}",
                 22, T["accent"] if on else T["text2"]))
        self.play_btn.setIcon(
            icon(f"playpause{getattr(self, '_play_step', 0)}", 20,
                 T["on_accent"]))

        # The sleep timer is lit in the accent while it counts down, and
        # like shuffle before it, nothing repainted it — so it kept the
        # colour of whatever station it was started on. Redrawn in the
        # frame it is currently showing, so a mid-hover accent change
        # does not snap it back to the resting pose.
        sleep_btn = getattr(self, "sleep_btn", None)
        if sleep_btn is not None and getattr(self, "_sleep_on", False):
            frame = getattr(sleep_btn, "_timer_frame", 0)
            sleep_btn.setIcon(icon(f"timer_{frame}" if frame else "timer",
                                   22, T["accent"]))
        on = self.shuffle_btn.isChecked()
        self._animate_shuffle(on)
        # Next stays neutral, and at its own size.
        #
        # This line repainted it in the accent at 20px whenever the colour
        # changed, undoing both the neutral tint and the 26px transport
        # size — which is why it "sometimes" had colour: only after an
        # artwork change.
        self.next_btn.setIcon(
            icon(f"next_shuffle{self._next_step}" if self._next_step
                 else "next", 26, T["text2"]))
        if self.fav_btn.isChecked():
            self.fav_btn.setIcon(icon("star_on", 20, T["accent"]))
            self._shine()

    def paintEvent(self, e):
        super().paintEvent(e)
        # A border cannot glow, so this is painted: a hairline of the
        # accent along the top, and a short gradient beneath it fading
        # into the bar. Three flat greys stacked next to each other are
        # hard to tell apart; a lit edge is not.
        p = QPainter(self)
        accent = QColor(T["accent"])
        grad = QLinearGradient(0, 0, 0, self.GLOW)
        top = QColor(accent)
        top.setAlpha(34)
        grad.setColorAt(0.0, top)
        faded = QColor(accent)
        faded.setAlpha(0)
        grad.setColorAt(1.0, faded)
        p.fillRect(0, 0, self.width(), self.GLOW, grad)
        # The hairline keeps the accent's saturation and simply carries
        # less of it: a lit edge, not a light source.
        edge = QColor(accent)
        edge.setAlpha(120)
        p.fillRect(0, 0, self.width(), 1, edge)
        p.end()

    def shazam_pulse(self):
        """A ripple when a song is identified.

        The only sign identification worked is the readout changing, which
        you may not be looking at — so the button that did the work says
        so itself.
        """
        if getattr(self, "_ripple", None) is None:
            self._ripple = Ripple(self.shazam_btn)
        self._ripple.fire()

        # The colour arrives from the middle outward rather than all at
        # once: the mark fills with accent from its centre over the first
        # third of the ripple, which is what "it found something" looks
        # like when the thing that found it is a circular mark.
        self._fill_step = 0
        if getattr(self, "_pulse_timer", None) is None:
            self._pulse_timer = QTimer(self)
            self._pulse_timer.setInterval(40)
            self._pulse_timer.timeout.connect(self._pulse_tick)
        self._pulse_step = 0
        self._pulse_timer.start()

    def _pulse_tick(self):
        self._pulse_step += 1
        steps = 14
        if self._pulse_step >= steps:
            self._pulse_timer.stop()
            self._paint_shazam(self.shazam_btn.isChecked())
            return
        t = self._pulse_step / steps
        if t <= 0.45:
            # Filling: the accent spreads from the centre to the rim.
            self.shazam_btn.setIcon(
                icon(f"shazam_fill{int(t / 0.45 * 4)}", 22, T["accent"]))
            return
        # Settling: eased back from bright to the resting colour.
        eased = 1.0 - (1.0 - (t - 0.45) / 0.55) ** 3
        lift = int(150 - 50 * eased)
        self.shazam_btn.setIcon(
            icon("shazam_on", 22, QColor(T["accent"]).lighter(lift).name()))

    def _animate_next(self, on):
        """The crossing tail grows onto the Next arrow, or retreats."""
        self._next_target = 4 if on else 0
        if getattr(self, "_next_timer", None) is None:
            self._next_step = 0
            self._next_timer = QTimer(self)
            self._next_timer.setInterval(30)
            self._next_timer.timeout.connect(self._next_tick)
        self._next_timer.start()

    def _next_tick(self):
        if self._next_step == self._next_target:
            self._next_timer.stop()
            return
        self._next_step += 1 if self._next_step < self._next_target else -1
        # Neutral either way: the shuffle *button* is what carries the
        # accent to say the mode is on. The Next button is a transport
        # control, and colouring it made two things claim to be the
        # indicator.
        name = ("next" if self._next_step == 0
                else f"next_shuffle{self._next_step}")
        self.next_btn.setIcon(icon(name, 26, T["text2"]))

    def _animate_shuffle(self, on):
        """Walk the arrows across rather than swapping two pictures."""
        self._shuffle_target = 4 if on else 0
        if getattr(self, "_shuffle_timer", None) is None:
            self._shuffle_timer = QTimer(self)
            self._shuffle_timer.setInterval(30)
            self._shuffle_timer.timeout.connect(self._shuffle_tick)
        self._shuffle_timer.start()

    def _shuffle_tick(self):
        target = self._shuffle_target
        if self._shuffle_step == target:
            self._shuffle_timer.stop()
            return
        self._shuffle_step += 1 if self._shuffle_step < target else -1
        on = self.shuffle_btn.isChecked()
        self.shuffle_btn.setIcon(
            icon(f"shuffle_{self._shuffle_step}", 22,
                 T["accent"] if on else T["text2"]))

    def _toggle_mute(self):
        """Silence, and put it back where it was.

        Remembering the level matters: a mute that leaves you at zero is
        not a mute, it is a volume change you have to undo by guessing.
        """
        if self._muted:
            self._muted = False
            self.vol.setValue(self._premute or 60)
        else:
            self._premute = self.vol.value()
            self._muted = True
            self.vol.setValue(0)
        self._paint_volume(self.vol.value())

    def _paint_volume(self, value):
        """Arcs for level: none when silent, three when loud."""
        if value <= 0:
            self._muted = True
            self.vol_icon.setToolTip("Unmute")
            self._animate_mute(True)
            return
        self._muted = False
        waves = 1 if value < 34 else 2 if value < 70 else 3
        self.vol_icon.setToolTip("Mute")
        if getattr(self, "_mute_step", 0) > 0:
            self._animate_mute(False, waves)
        else:
            self.vol_icon.setIcon(icon(f"volume{waves}", 20, T["text2"]))

    def _animate_mute(self, muting, waves=3):
        """Draw the slash on, or take it off again."""
        self._mute_target = 4 if muting else 0
        self._mute_waves = waves
        if getattr(self, "_mute_timer", None) is None:
            self._mute_step = 0
            self._mute_timer = QTimer(self)
            self._mute_timer.setInterval(26)
            self._mute_timer.timeout.connect(self._mute_tick)
        self._mute_timer.start()

    def _mute_tick(self):
        if self._mute_step == self._mute_target:
            self._mute_timer.stop()
            if self._mute_step == 0:
                self.vol_icon.setIcon(
                    icon(f"volume{self._mute_waves}", 20, T["text2"]))
            return
        self._mute_step += 1 if self._mute_step < self._mute_target else -1
        self.vol_icon.setIcon(
            icon(f"volume_muted{self._mute_step}", 20, T["down"]))

    def _expand_full(self, opening):
        self._full_opening = opening
        if getattr(self, "_full_timer", None) is None:
            self._full_timer = QTimer(self)
            self._full_timer.setInterval(28)
            self._full_timer.timeout.connect(self._full_tick)
        self._full_timer.start()

    def _full_tick(self):
        target = 2 if self._full_opening else 0
        if self._full_step == target:
            self._full_timer.stop()
            return
        self._full_step += 1 if self._full_step < target else -1
        name = ("fullscreen" if self._full_step == 0
                else f"fullscreen{self._full_step}")
        self.full_btn.setIcon(icon(name, 22, T["text2"]))

    def _on_shazam(self, on):
        self.shazam_btn.setIcon(
            icon("shazam_on" if on else "shazam", 22,
                 T["accent"] if on else T["text2"]))
        self.shazam_btn.setToolTip(
            "Identifying songs on this station" if on
            else "Identify songs on this station")
        self.shazam_toggled.emit(on)

    def set_shazam(self, on):
        """Reflect the station's own setting without re-emitting."""
        self.shazam_btn.blockSignals(True)
        self.shazam_btn.setChecked(bool(on))
        self.shazam_btn.blockSignals(False)
        self._paint_shazam(bool(on))

    def _paint_shazam(self, on):
        self.shazam_btn.setIcon(
            icon("shazam_on" if on else "shazam", 22,
                 T["accent"] if on else T["text2"]))

    def _on_shuffle(self, on):
        self._animate_shuffle(on)
        # Skip stops meaning "the next one down" when shuffle is on, so the
        # glyph says which it is — but it stays a neutral transport colour
        # either way. The accent is for what is playing, and a permanently
        # lit Next read as a state rather than a button.
        self._animate_next(on)
        self.next_btn.setToolTip("Random station" if on else "Next station")
        self.shuffled.emit(on)

    # -- public ------------------------------------------------------------

    def set_playing(self, playing):
        """Reflect the player, without re-emitting.

        The icon only ever changed from its own click, so starting a
        station from the list left a play triangle sitting over a playing
        stream — the one place the button is wrong is the place you look
        to find out what is happening.
        """
        playing = bool(playing)
        if playing == self._playing:
            return
        self._playing = playing
        self.play_btn.setToolTip("Pause" if playing else "Play")
        self._morph_play(playing)

    MORPH_FRAMES = 4

    def _morph_play(self, playing):
        """Walk the glyph between the triangle and the two bars."""
        self._play_target = self.MORPH_FRAMES if playing else 0
        if getattr(self, "_play_timer", None) is None:
            self._play_step = 0
            self._play_timer = QTimer(self)
            self._play_timer.setInterval(26)
            self._play_timer.timeout.connect(self._play_tick)
        self._play_timer.start()

    def _play_tick(self):
        if self._play_step == self._play_target:
            self._play_timer.stop()
            return
        self._play_step += 1 if self._play_step < self._play_target else -1
        self.play_btn.setIcon(
            icon(f"playpause{self._play_step}", 20, T["on_accent"]))

    def set_now_playing(self, song, artist="", station=None, pixmap=None):
        """song / artist / station, top to bottom.

        `station` defaults to whatever is already on the bottom line, so a
        song change keeps the station it is playing on rather than blanking
        it until the next station change.
        """
        # Clear the star only when the song actually changes.
        #
        # This ran on every readout refresh, and a station re-identifies
        # what is playing every half minute — so favouriting a song
        # un-favourited itself moments later, on screen, while the record
        # on disk stayed correct. The star was never wrong about what was
        # saved; it was being wiped by something that had no business
        # touching it.
        # The star is not this method's business.
        #
        # It used to clear whenever the readout text changed, which is
        # the wrong signal: the text changes when the readout source
        # changes, when a stream sends metadata, when a custom override
        # applies, when YouTube resolves a title — none of which mean a
        # different song. The window clears the star when the song
        # itself changes, and only then.
        self.song.setText(song)
        self.artist.setText(artist or "")
        if station is not None:
            self.meta.setText(station)
        if pixmap:
            self.set_artwork(pixmap)

    NOTICE_MS = 6000

    def set_notice(self, text):
        """A short-lived line about the app, under the station name.

        Clears itself: a message about a sign-in should not still be
        sitting there an hour later.
        """
        self.notice.setText(text or "")
        self.notice.setVisible(bool(text))
        if getattr(self, "_notice_timer", None) is None:
            self._notice_timer = QTimer(self)
            self._notice_timer.setSingleShot(True)
            self._notice_timer.timeout.connect(
                lambda: self.notice.setVisible(False))
        if text:
            self._notice_timer.start(self.NOTICE_MS)
        else:
            self._notice_timer.stop()

    def set_ad_cover(self, seconds, time_left=None):
        """Run the hourglass for a covered ad break, or stop it.
        `time_left`: see Hourglass.start."""
        if seconds and seconds > 0:
            self.hourglass.start(seconds, time_left)
        else:
            self.hourglass.stop()

    def set_station_line(self, station):
        self.meta.setText(station or "")

    def set_artwork(self, pixmap):
        self.art.set_art(pixmap)


class ArtworkScaler(Job):
    """Resize incoming artwork off the GUI thread.

    A cover arrives at whatever size the service sends — often 1200px or
    more — and every place it is shown needs its own smaller copy. Doing
    that scaling inline stopped the interface for as long as it took,
    which on a large image is long enough to feel like the app has hung.

    QPixmap may only be touched on the GUI thread, so the work is done on
    QImage and the cheap conversion happens when the result arrives. The
    dominant color is calculated here too, for the same reason.

    A jobs.Job (daemon thread), not a QThread, so quitting mid-scale
    can't abort the app (build 54, task 14). It is C++ image work rather
    than a download, so quitting gives it a moment to finish
    (WAIT_ON_QUIT) instead of cutting it off inside Qt.
    """

    WAIT_ON_QUIT = True

    ready = pyqtSignal(object, object, object, object)
    # thumb, dock, full, dominant colour
    failed = pyqtSignal()

    def __init__(self, image, thumb_size, dock_size, parent=None,
                 data=None, path=""):
        super().__init__(parent)
        self._image = image
        self._data = data
        self._path = path
        self._bytes = None
        self._buffer = None
        self._thumb = thumb_size
        self._dock = dock_size

    def _decode(self):
        """Turn bytes or a path into an image, here rather than on the GUI
        thread.

        This is the expensive part and it was never moved: the scaling ran
        on a worker while the *decode* stayed where it was, so a large
        JPEG still stopped the window while it was being read. Qt's color
        profile parsing is switched off for the same reason — the log had
        77 "fromIccProfile: failed minimal tag size sanity" warnings, each
        one a broken profile being parsed before being thrown away.
        """
        from PyQt5.QtCore import QBuffer, QByteArray
        from PyQt5.QtGui import QImageReader

        if self._image is not None:
            return self._image
        reader = QImageReader()
        reader.setAutoTransform(False)
        if self._path:
            reader.setFileName(self._path)
        else:
            # Both kept alive on self: a QBuffer wrapping a temporary
            # QByteArray reads freed memory the moment the temporary goes,
            # which is a segfault rather than an error.
            self._bytes = QByteArray(self._data)
            self._buffer = QBuffer(self._bytes)
            self._buffer.open(QBuffer.ReadOnly)
            reader.setDevice(self._buffer)
        image = reader.read()
        if self._buffer is not None:
            self._buffer.close()
        return image if not image.isNull() else None

    # One decode at a time, across every thread.
    #
    # Qt's image reading shares state that is not re-entrant, and the
    # crash log caught the GUI thread and this one inside it together.
    # The lock costs nothing — decoding is already the slow part and
    # there is only ever one cover in flight.
    # Shared with the thumbnail cache — see imagelock.py.
    from imagelock import LOCK as _decode_lock

    def work(self):
        try:
            with self._decode_lock:
                image = self._decode()
        except Exception:                       # noqa: BLE001
            image = None
        if image is None:
            self.send(self.failed)
            return
        try:
            thumb = image.scaled(self._thumb, self._thumb,
                                 Qt.KeepAspectRatioByExpanding,
                                 Qt.SmoothTransformation)
            dock = (image.scaled(self._dock, Qt.KeepAspectRatioByExpanding,
                                 Qt.SmoothTransformation)
                    if self._dock.isValid() and not self._dock.isEmpty()
                    else None)
            # None when the art has no colour of its own (task 31).
            colour = theme.dominant_color(image, fallback=None)
        except Exception:                       # noqa: BLE001
            return
        self.send(self.ready, thumb, dock, image, colour)


# --------------------------------------------------------------------------
# Shell
# --------------------------------------------------------------------------

class Shell(QMainWindow):
    # Emitted when a fetched image could not be decoded, so whatever
    # supplied it can move on to the next candidate.
    art_decode_failed = pyqtSignal()

    """Everything hangs off here. Add real pages with add_page()."""

    COLLAPSE_AT = 1000      # px: sidebar becomes a rail below this
    # One column's worth. Two of these plus the gap is the split layout.
    PANEL_W = 320
    # The sidebar as a rail of icons: below this it has nothing to show.
    RAIL_W = 64
    # The station list must not be squeezed to nothing to make room.
    LIST_MIN = 520
    HIDE_PANEL_AT = 820     # px: right panel folds away below this

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Frequencies")
        self.resize(1180, 760)

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = Sidebar()
        self.pages = QStackedWidget()
        # A stack takes the largest minimum of every page it holds, so the
        # widest one — Discover, with its filter row — was setting the
        # smallest the whole window could ever be. Each page scrolls or
        # elides on its own; none of them needs to veto a resize.
        self.pages.setMinimumWidth(300)
        self.pages.setObjectName("Content")
        self.right = self._build_right_panel()

        # A splitter rather than a fixed row, so the three columns can be
        # dragged against each other. Each keeps a minimum that is the
        # width below which it stops being useful rather than merely
        # narrow — a 40px lyrics column is not a smaller lyrics column, it
        # is a stripe.
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setObjectName("Body")
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(4)
        self.sidebar.setMinimumWidth(self.RAIL_W)
        self.pages.setMinimumWidth(self.LIST_MIN)
        self.right.setMinimumWidth(self.PANEL_W)
        for widget in (self.sidebar, self.pages, self.right):
            self.splitter.addWidget(widget)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        # The sidebar is not a column to be tuned — it has one width for
        # its labels and one for the rail, and anything between shows
        # half a word. Its handle is disabled; the window collapsing it
        # still works, because that is a decision the layout makes rather
        # than a drag.
        handle = self.splitter.handle(1)
        if handle is not None:
            handle.setEnabled(False)
            handle.unsetCursor()
        # Once dragged, the automatic sizing steps aside: a width someone
        # chose should not be overwritten the next time the window moves.
        # Only a real drag counts.
        #
        # setSizes emits splitterMoved too, so every programmatic resize
        # marked the layout as user-adjusted — and the automatic collapse
        # then stood aside permanently. It worked exactly once, which is
        # the shape of a latch that never unlatches.
        self.splitter.splitterMoved.connect(self._splitter_moved)
        # The sidebar asking for a width, since it cannot take one.
        self.sidebar.width_changed.connect(self._resize_sidebar)
        body.addWidget(self.splitter, 1)

        self.player = PlayerBar()

        outer.addLayout(body, 1)
        outer.addWidget(self.player)
        self.setCentralWidget(root)

        self.sidebar.navigated.connect(self._go)
        self._page_index = {}
        self._last_page = "stations"

        self.now_playing = NowPlaying()
        self.now_playing.dismissed.connect(self.close_now_playing)
        self.add_page("now_playing", self.now_playing)
        # Clicks are deliberate; the window resizing is not.
        self.player.art_clicked.connect(lambda: self.toggle_art_dock(True))
        self.sidebar.art_dock.clicked.connect(
            lambda: self.toggle_art_dock(True))
        self.player.fullscreen_requested.connect(self.toggle_now_playing)
        # Expanded is the default: the sidebar space is reserved for it
        # either way, so leaving it empty on launch shows a hole and hides
        # the artwork.
        self._art_expanded = True
        self._art_anim = None
        self._flier = None
        QTimer.singleShot(0, self._art_initial)

    def add_page(self, key: str, widget: QWidget):
        self._page_index[key] = self.pages.addWidget(widget)
        # The sidebar picks its destination before any page exists, so show
        # a page as soon as the one it already selected turns up.
        if key == self._last_page:
            self.pages.setCurrentIndex(self._page_index[key])

    # A fade, and only a fade.
    #
    # Scaling the page up from 80% looked right in the compositor's own
    # settings and wrong here: a window animates against the desktop,
    # while a page animates inside a frame that stays put — so shrinking
    # it just exposed the page underneath around its edges. What looked
    # like a graceful zoom was the old page framing the new one.
    PAGE_MS = 180

    def _go(self, key):
        if key not in self._page_index:
            return
        self._last_page = key
        index = self._page_index[key]
        if index == self.pages.currentIndex():
            return
        self.pages.setCurrentIndex(index)
        self._animate_page(self.pages.widget(index))

    def _animate_page(self, page):
        """Grow the arriving page into place.

        A page that simply appears has no relationship to the click that
        asked for it. One that scales up from the middle, fading, reads
        as the answer to that click — which is what the desktop does with
        its own windows.
        """
        if page is None:
            return
        anim = getattr(self, "_page_anim", None)
        if anim is not None:
            anim.stop()
            # Stopping an animation does not run its "finished" handler,
            # so the page it was fading kept its effect for good — drawn
            # through it forever, half transparent if it was stopped early.
            # Cleared by hand when one fade interrupts another.
            previous = getattr(self, "_page_animating", None)
            if previous is not None and previous is not page:
                try:
                    previous.setGraphicsEffect(None)
                except RuntimeError:
                    pass
        self._page_animating = page

        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)

        group = QParallelAnimationGroup(self)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(self.PAGE_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(fade)

        # The effect is dropped at the end: a page left under a graphics
        # effect repaints through it forever, which costs more than the
        # animation did.
        group.finished.connect(lambda: page.setGraphicsEffect(None))
        self._page_anim = group
        group.start()

    # -- artwork dock ------------------------------------------------------

    ART_MS = 260
    # Light, not soft: enough that the corner is not a point, not so much
    # that the artwork looks like an app icon.
    DOCK_RADIUS = 10

    def _redraw_dock_art(self):
        """Rescale the docked artwork to the space it now has."""
        if not self._art_expanded or self._art_anim is not None:
            return
        dock = self.sidebar.art_dock
        full = self.player.art.full_art()
        if full is None or full.isNull() or dock.height() <= 0:
            return
        # The content area, not the widget.
        #
        # A label's frame and margins make its contents rectangle smaller
        # than the widget, so a pixmap sized to the widget overflows by a
        # pixel or two — and the label clips that overflow, which takes
        # the rounded corners with it.
        box = dock.contentsRect()
        dock.setPixmap(cover(full, box.width(), box.height(),
                             self.DOCK_RADIUS))

    def _art_initial(self):
        """Put the art in the dock without animating it on launch."""
        if not self._art_expanded or self.sidebar._collapsed:
            self._art_expanded = False
            return
        bar_art = self.player.art
        dock = self.sidebar.art_dock
        self.sidebar.set_art_dock_open(True)
        bar_art.setMaximumWidth(0)
        bar_art.setMinimumWidth(0)
        bar_art.setVisible(False)
        box = dock.contentsRect().size()
        dock.setPixmap(cover(bar_art.full_art(), box.width(), box.height(),
                             self.DOCK_RADIUS))
        dock.setToolTip("Put the artwork back")

    def toggle_art_dock(self, manual=False):
        """Move the artwork between the bar and the dock.

        `manual` marks a press of the button rather than the window
        deciding — a deliberate fold stays folded when the window comes
        back, because it was a choice rather than a consequence.
        """
        if manual:
            self._art_was_expanded = False
        """Move the artwork between the player bar and the sidebar dock.

        Not a page change and not a dialog: the art travels to the space
        that is already reserved for it, and the text it was sitting
        beside slides left into where it was. Reversing is the same click.
        """
        if self._art_anim is not None:
            return                          # mid-flight; ignore the click
        if self.sidebar._collapsed:
            return                          # no dock to fly to
        self._art_expanded = not self._art_expanded
        self._animate_art(self._art_expanded)

    def _animate_art(self, expanding):
        bar_art = self.player.art
        dock = self.sidebar.art_dock

        # Open the dock early, close it late.
        #
        # Expanding, the space has to exist before the destination can be
        # measured — that was the old jump. Collapsing, the opposite is
        # true: closing it first meant the *starting* rectangle was read
        # after the dock had already gone, so the artwork appeared at the
        # bottom of the sidebar at full size and then shrank, instead of
        # travelling down. Symmetrical bugs, opposite fixes.
        if expanding:
            self.sidebar.set_art_dock_open(True)
            layout = self.sidebar.layout()
            if layout is not None:
                layout.activate()

        size = QSize(self.sidebar.width() - 32, self.sidebar.ART_DOCK_H)
        art = bar_art.art()

        # A free-floating copy does the travelling, so neither layout has
        # to be fought while it moves.
        flier = QLabel(self)
        flier.setAttribute(Qt.WA_TransparentForMouseEvents)
        flier.setScaledContents(True)
        # The bar thumbnail's *intended* size, not its current one.
        #
        # While the artwork is docked the thumbnail is collapsed to zero
        # width, so reading its size on the way back gave a destination
        # 0px wide — and the flier, which scales its contents, squashed
        # into a vertical sliver instead of shrinking. That is the flicker.
        side = T["art_sm"]
        start = QRect(bar_art.mapTo(self, QPoint(0, 0)), QSize(side, side))
        end = QRect(dock.mapTo(self, QPoint(0, 0)),
                    QSize(dock.width() or size.width(), size.height()))
        if not expanding:
            start, end = end, start
        flier.setPixmap(cover(
            bar_art.full_art(),
            max(start.width(), end.width()),
            max(start.height(), end.height()), self.DOCK_RADIUS))
        flier.setGeometry(start)
        flier.show()
        flier.raise_()
        self._flier = flier

        # Kept in the layout, emptied of its picture.
        #
        # Hiding it took it out of the layout entirely, so the width
        # animation had nothing to push and the text sat still while the
        # artwork descended onto it. Visible but blank, its animated width
        # is what slides the song text across.
        bar_art.setPixmap(QPixmap())
        bar_art.setVisible(True)
        dock.clear()

        group = QParallelAnimationGroup(self)
        travel = QPropertyAnimation(flier, b"geometry", self)
        travel.setDuration(self.ART_MS)
        travel.setStartValue(start)
        travel.setEndValue(end)
        travel.setEasingCurve(QEasingCurve.InOutCubic)
        group.addAnimation(travel)

        # The text follows by the width the artwork leaves behind: animating
        # the thumbnail's own maximum width lets the layout carry the text
        # rather than positioning it by hand.
        # The text and the star travel by the width the artwork gives up,
        # on the same curve and over the same time, so the two halves of
        # the movement read as one gesture rather than two.
        for prop, start, end in ((b"maximumWidth", T["art_sm"], 0),
                                 (b"minimumWidth", T["art_sm"], 0)):
            slide = QPropertyAnimation(bar_art, prop, self)
            slide.setDuration(self.ART_MS)
            slide.setStartValue(start if expanding else end)
            slide.setEndValue(end if expanding else start)
            slide.setEasingCurve(QEasingCurve.InOutCubic)
            group.addAnimation(slide)

        group.finished.connect(lambda: self._art_landed(expanding, size))
        self._art_anim = group
        group.start()

    def _art_landed(self, expanded, size):
        # Collapsing, the dock is given up only now — the flier has
        # finished travelling out of it, so the tag list closing the gap
        # is the last thing that happens rather than the first.
        if not expanded:
            self.sidebar.set_art_dock_open(False)
        flier, self._flier = self._flier, None
        bar_art = self.player.art
        dock = self.sidebar.art_dock
        if expanded:
            # The dock's size now, not the one measured before the
            # animation started.
            #
            # `size` was computed from the sidebar's width at the moment
            # the flight began, and the sidebar can settle to something
            # else while it runs — leaving a 300px picture in a 173px
            # label, which the label then crops. Asking the widget is the
            # only reliable answer.
            box = dock.contentsRect().size()
            dock.setPixmap(cover(bar_art.full_art(),
                                 box.width() or size.width(),
                                 box.height() or size.height(),
                                 self.DOCK_RADIUS))
            dock.setToolTip("Put the artwork back")
            # Redrawn once the flight is properly over.
            #
            # _redraw_dock_art refuses while _art_anim is set — it has to,
            # or a stray resize mid-flight would drop the picture back
            # into the dock early. But that same guard rejected every
            # attempt made from here, because the handle is cleared after
            # this returns. Clearing it first is what lets the redraw run.
            self._art_anim = None
            # Twice, and the second one matters.
            #
            # At zero milliseconds the dock is often still the width it
            # had mid-flight; the sidebar settles a frame or two later and
            # no further resize arrives to trigger a redraw. The delayed
            # pass is what actually lands the picture at the dock's final
            # size.
            QTimer.singleShot(0, self._redraw_dock_art)
            QTimer.singleShot(150, self._redraw_dock_art)
            bar_art.setMaximumWidth(0)
            bar_art.setMinimumWidth(0)
        else:
            dock.clear()
            self.sidebar.set_art_dock_open(False)
            dock.unsetCursor()
            dock.setToolTip("")
            bar_art.setMaximumWidth(T["art_sm"])
            bar_art.setMinimumWidth(T["art_sm"])
            bar_art.setVisible(True)
            # The picture comes back only now that it has arrived.
            bar_art.set_art(bar_art.full_art())
        if flier is not None:
            flier.deleteLater()
        self._art_anim = None

    def toggle_now_playing(self):
        """The same click in and out — clicking the artwork a second time
        was doing nothing, which makes it look broken."""
        if self.in_now_playing():
            self.close_now_playing()
        else:
            self.open_now_playing()

    def open_now_playing(self):
        """The right panel is a smaller version of this view, so it folds
        away rather than showing the same thing twice."""
        self.right.hide()

        # The docked artwork folds too, and stays folded.
        #
        # Full screen shows the cover at the size of the window; the
        # little one in the corner is the same picture again, smaller,
        # competing with it. Whether it was open is remembered so that
        # leaving puts things back exactly as they were.
        self._art_before_full = self._art_expanded
        if self._art_expanded:
            self.toggle_art_dock(manual=True)
        self.pages.setCurrentIndex(self._page_index["now_playing"])

    def close_now_playing(self):
        # The page goes back first, then everything that depends on it.
        #
        # Restoring the artwork dock re-runs the layout check, and that
        # check hides the side panel while full screen is showing. Done in
        # the old order, full screen was still the current page at that
        # moment — so the panel was hidden again straight after being
        # shown, and stayed hidden until a resize ran the check again.
        self.pages.setCurrentIndex(self._page_index.get(self._last_page, 0))
        self.right.setVisible(self.width() >= self.HIDE_PANEL_AT)
        # Catch up on whatever the window did while full screen was open.
        #
        # The resize handling stands aside during full screen, so a window
        # made narrower (or wider) in there came back with the three
        # columns still sized for the old width: the splitter shrank all
        # of them, the sidebar included, and the panel kept two columns
        # it no longer had room for (task 73).
        self._fit_layout(self.width())
        if getattr(self, "_art_before_full", False) and not self._art_expanded:
            self.toggle_art_dock(manual=True)
        self._art_before_full = False
        # And once more after the dock has settled, since its animation
        # can resize the window a frame later.
        QTimer.singleShot(
            0, lambda: self.right.setVisible(
                self.width() >= self.HIDE_PANEL_AT
                and not self.in_now_playing()))

    def in_now_playing(self):
        return (self.pages.currentIndex()
                == self._page_index.get("now_playing", -1))

    def _build_right_panel(self):
        """Lyrics and artist, not a second copy of the artwork.

        Width is a minimum rather than fixed now: the panel can be widened
        far enough to hold two columns, which is what Both uses when there
        is room for it.
        """
        from sidepanel import SidePanel
        panel = SidePanel()
        panel.setMinimumWidth(300)
        panel.setMaximumWidth(760)
        self.panel = panel
        return panel

    def connect_sidebar(self):
        """The dock tells us when it has changed size."""
        # Coalesced, not one redraw per pixel of a drag.
        #
        # Dragging the panel resizes the sidebar continuously, and each
        # step rescaled a 1000px cover — which is what made a drag feel
        # sluggish and the artwork flicker. One redraw after the drag
        # settles is all that is needed.
        self._dock_redraw = QTimer(self)
        self._dock_redraw.setSingleShot(True)
        self._dock_redraw.setInterval(90)
        self._dock_redraw.timeout.connect(self._redraw_dock_art)
        self.sidebar.art_resized.connect(self._dock_redraw.start)

    def attach_accent(self, controller):
        """Hand the shell an AccentController and everything follows the art."""
        self.accent = controller
        controller.set_target(self)
        # The surfaces the accent is actually visible on. They get the
        # small sheet at once; the window follows a moment later.
        controller.set_quick_targets([self.player, self.sidebar, self.panel])
        controller.changed.connect(self._accent_changed)

    def showEvent(self, e):
        super().showEvent(e)
        # Coming back from the skinned player: apply whatever the accent
        # moved to while this window was hidden.
        if getattr(self, "accent", None) is not None:
            self.accent.flush()

    def _splitter_moved(self, *_args):
        if getattr(self, "_moving_splitter", False):
            return
        self._panel_dragged = True

        # Coalesced, not once per pixel of the drag.
        #
        # Every step was rewriting all three column widths and remeasuring
        # every tag pill, and a drag produces a hundred of those a second.
        # Doing it when the pointer pauses is indistinguishable to look at
        # and a fraction of the work.
        keeper = getattr(self, "_hold_timer", None)
        if keeper is None:
            keeper = QTimer(self)
            keeper.setSingleShot(True)
            keeper.setInterval(60)
            keeper.timeout.connect(self._hold_sidebar)
            self._hold_timer = keeper
        keeper.start()
        # Dragging the panel must not cost the sidebar.
        #
        # Its handle is disabled, so it cannot be dragged directly — but
        # pulling the *panel* wider makes the splitter take the shortfall
        # from wherever it can, and the sidebar was fair game. It is put
        # back, out of the list's share.

    def _hold_sidebar(self):
        """Restore the sidebar to the width it is meant to have."""
        want = (T["rail_w"] if self.sidebar._collapsed else T["sidebar_w"])
        sizes = self.splitter.sizes()
        if len(sizes) != 3 or sizes[0] == want:
            return
        spare = sizes[1] + (sizes[0] - want)
        if spare < self.LIST_MIN:
            # Not enough room to be generous: take it from the panel.
            panel = max(self.PANEL_W, sizes[2] - (self.LIST_MIN - spare))
            spare = sum(sizes) - want - panel
            self._set_sizes([want, spare, panel])
            return
        self._set_sizes([want, spare, sizes[2]])

    def _set_sizes(self, sizes):
        """Resize the columns without it counting as a drag."""
        self._moving_splitter = True
        try:
            self.splitter.setSizes(sizes)
        finally:
            self._moving_splitter = False

    def _resize_sidebar(self, width):
        """Give the sidebar the width it just asked for.

        A splitter distributes what it has; a child cannot simply become
        wider. The difference comes off the pages column, which is the
        one thing here that is happy to be any size.
        """
        sizes = self.splitter.sizes()
        if len(sizes) != 3 or sizes[0] == width:
            return
        spare = sizes[1] + (sizes[0] - width)
        if spare < self.LIST_MIN:
            # Take it from the panel instead of refusing.
            #
            # Refusing left the sidebar at the rail width with nothing to
            # widen it later — the flag said "expanded", the maximum said
            # "expanded", and the splitter quietly kept its old numbers
            # forever. Something always has to give; the panel is the one
            # thing here that can.
            needed = self.LIST_MIN - spare
            panel = max(self.PANEL_W, sizes[2] - needed)
            spare = max(self.LIST_MIN, sizes[1] + sizes[2]
                        - panel + (sizes[0] - width))
            self._set_sizes([width, spare, panel])
            return
        self._set_sizes([width, spare, sizes[2]])

    def _accent_changed(self, _colour):
        """The accent moved. Repaint what the stylesheet cannot reach.

        The icon cache is emptied only when the fade lands, not on every
        frame of it.

        A fade runs at thirty frames a second for a second, and clearing
        the cache each time meant every icon in the window was redrawn
        from scratch thirty times per station change — with the cost
        building as more icons had been seen. That is the app getting
        choppier the longer it ran until it stopped answering at all.

        Dropping it once at the end is enough: the cache is keyed by
        colour, so the frames in between simply miss and draw what they
        need, and the final colour is the one worth keeping.
        """
        if getattr(self.accent, "settled", True):
            forget_icons()
        self.player.refresh_accent()
        # Icons drawn as pixmaps have to be told; the stylesheet reaches
        # everything else on its own.
        repaint = getattr(self, "_repaint_jump", None)
        if repaint is not None:
            repaint()
        panel = getattr(self, "panel", None)
        if panel is not None:
            panel.reaccent()
        chat = getattr(self, "_chat_window", None)    # task 92's pop-out
        if chat is not None:
            chat.restyle()
        self.now_playing.lyrics.reaccent()
        for key in self._page_index:
            page = self.pages.widget(self._page_index[key])
            repaint = getattr(page, "reaccent", None)
            if repaint is not None:
                repaint()
            vp = getattr(page, "viewport", None)
            (vp() if callable(vp) else page).update()

    def set_artwork(self, pixmap):
        """One call updates the bar, the full-screen view and the accent.

        The right panel no longer shows album art — it was the same image
        the bar already had. Artwork that fails to load simply doesn't
        arrive here, so the color from the previous song is held rather
        than reset.
        """
        if pixmap is None or pixmap.isNull():
            return
        self._start_scaler(image=pixmap.toImage())

    def clear_artwork(self, tint=None, station_name=""):
        """Put the artwork back to the station's own placeholder.

        set_artwork ignores an empty picture on purpose — artwork that
        fails to load should leave the last one alone rather than blank
        the bar. Clearing is a different intention and needs saying
        outright.
        """
        self.player.art.set_placeholder_tint(tint, station_name)
        self.player.art.set_art(QPixmap())
        if self._art_expanded:
            self._redraw_dock_art()

        # The placeholder's own colour becomes the accent.
        #
        # The accent is taken from the artwork, and a station with no
        # artwork left it on whatever the last station happened to be —
        # so an empty cover wore somebody else's colour. The placeholder
        # is drawn from the station's monogram hue, and that hue is as
        # much "this station's colour" as a cover would have been.
        if (tint and getattr(self, "accent", None)
                and getattr(self.accent, "_source", "artwork") == "artwork"):
            self.accent.set_color(QColor(tint))

    def set_artwork_data(self, data=None, path=""):
        """Artwork as bytes, or as a file on disk.

        Preferred over set_artwork: handing the worker the raw bytes lets
        it do the decoding as well as the scaling. Decoding is the part
        that was stopping the window — a large JPEG with a broken color
        profile takes real time, and it was happening here.
        """
        if not data and not path:
            return
        self._start_scaler(data=data, path=path)

    def _start_scaler(self, image=None, data=None, path=""):
        """The worker does everything; this returns immediately and the
        picture appears a frame or two later."""
        # One at a time. Two songs identified in quick succession would
        # otherwise start two scalers racing to paint the same widgets,
        # and the loser would win.
        running = getattr(self, "_art_scaler", None)
        try:
            busy = running is not None and running.isRunning()
        except RuntimeError:
            # deleteLater has already taken it; the Python name outlives
            # the C++ object.
            busy = False
        if busy:
            self._art_queued = (image, data, path)
            return
        self._art_queued = None

        dock = self.sidebar.art_dock
        worker = ArtworkScaler(
            image, T["art_sm"],
            QSize(dock.width(), self.sidebar.ART_DOCK_H), self,
            data=data, path=path)
        worker.ready.connect(self._artwork_scaled)
        worker.failed.connect(self.art_decode_failed.emit)
        worker.finished.connect(self._scaler_done)
        # A Job deletes itself once it has answered. The reference is for
        # the "one at a time" check above.
        self._art_scaler = worker
        worker.start()

    def _scaler_done(self):
        self._art_scaler = None

    def shutdown_scaler(self):
        """Called on quit: the scaler holds an image, not a socket, so it
        always finishes quickly — but it must be allowed to.
        (`Job.stop_all` usually waited for it already: WAIT_ON_QUIT.)"""
        worker = getattr(self, "_art_scaler", None)
        try:
            if worker is not None and worker.isRunning():
                worker.wait(2000)
        except RuntimeError:
            pass

    def _artwork_scaled(self, thumb, dock_image, full, colour):
        """Back on the GUI thread with everything already the right size."""
        pixmap = QPixmap.fromImage(full)
        self.player.art.set_scaled(QPixmap.fromImage(thumb), pixmap)
        if (self._art_expanded and self._art_anim is None
                and dock_image is not None):
            dock = self.sidebar.art_dock
            # Cropped to the dock, then rounded — not rounded and then
            # cropped by the label.
            #
            # This is the path that runs every time the artwork changes,
            # so it is the one that mattered: the worker scales to fill,
            # which overshoots the label, and rounding an image wider
            # than its frame puts the corners outside the visible area.
            # The other three call sites were fixed; this one was missed.
            box = dock.contentsRect()
            dock.setPixmap(cover(QPixmap.fromImage(dock_image),
                                 box.width(), box.height(),
                                 self.DOCK_RADIUS))
        self.now_playing.set_track(self.player.song.text(),
                                   self.player.meta.text(), "", pixmap)
        if (getattr(self, "accent", None)
                and getattr(self.accent, "_source", "artwork") == "artwork"):
            if colour is not None:
                self.accent.set_color(colour)
            else:
                # White, grey or black art: the desktop's accent, not the
                # amber that a colourless picture used to turn into.
                self.accent.from_desktop()

        queued, self._art_queued = getattr(self, "_art_queued", None), None
        if queued is not None:
            QTimer.singleShot(0, lambda q=queued: self._start_scaler(*q))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = e.size().width()
        # Put the artwork back before the rail collapses, not after: the
        # toggle refuses to run once there is no dock to fly to, so doing
        # this in the other order left the art expanded into a space that
        # had just been taken away.
        # Not while a drag is in flight.
        #
        # The panel handle resizing the sidebar past the threshold made
        # the artwork fold and unfold on alternate frames.
        if getattr(self, "_moving_splitter", False):
            return

        # And not while full screen is open: the window widening again
        # would otherwise bring the small copy back over the large one.
        # close_now_playing runs the same fitting once it is closed, so a
        # window resized during full screen is caught up then (task 73).
        if self.in_now_playing():
            return
        self._fit_layout(w)

    def _fit_layout(self, w):
        """Sidebar rail or not, panel shown or not, one column or two —
        for a window `w` wide. The resize does this; leaving full screen
        does it too."""
        collapsed = w < self.COLLAPSE_AT
        if collapsed and self._art_expanded and self._art_anim is None:
            # Remember that the window took it away, not you.
            #
            # Collapsing folds the artwork back into the bar because there
            # is no dock to hold it. Widening the window again used to
            # leave it folded, so a window resize quietly changed a
            # setting — and only a manual toggle could undo it.
            self._art_was_expanded = True
            self.toggle_art_dock()
        self.sidebar.set_collapsed(collapsed)

        # Put it back only once the rail is a sidebar again.
        #
        # The toggle refuses while the sidebar is collapsed — there is no
        # dock to fly to — so asking before set_collapsed was asking at
        # the one moment it could not be done.
        if (not collapsed and getattr(self, "_art_was_expanded", False)
                and not self._art_expanded and self._art_anim is None):
            self._art_was_expanded = False
            QTimer.singleShot(0, self.toggle_art_dock)
        self.right.setVisible(w >= self.HIDE_PANEL_AT
                              and not self.in_now_playing())
        # The splitter owns the widths once the user has touched it; this
        # only steps the panel between one and two columns when they have
        # not.
        if getattr(self, "_panel_dragged", False):
            return
        # One panel or two, nothing in between.
        #
        # Widening the single panel until it could hold two columns gave a
        # very wide panel with two narrow columns in it. What is wanted is
        # a second panel: the width steps from one to two when the window
        # can spare it, and back again when it cannot, so a column is
        # always the same readable width.
        panel = getattr(self, "panel", None)
        # isVisibleTo, not isVisible.
        #
        # isVisible() is false for every child until the window itself is
        # shown, so a resize during construction read the panel as hidden,
        # collapsed it to nothing, and there was no later event to undo
        # that. isVisibleTo asks what this widget was told to do, which is
        # the question being asked.
        if panel is not None and not self.right.isVisibleTo(self):
            # Hidden, it must also stop reserving width, or the window
            # cannot be made narrow enough to reach the collapsed layout
            # at all — its minimum still includes a panel nobody can see.
            panel.setMaximumWidth(0)
        elif panel is not None:
            room = w - self.sidebar.width() - self.LIST_MIN
            two = room >= self.PANEL_W * 2 + 16
            wanted = self.PANEL_W * 2 + 16 if two else self.PANEL_W
            # Through the splitter, not as a fixed width.
            #
            # setFixedWidth pins the minimum as well, so the panel could
            # never be dragged narrower than whatever the automatic step
            # had last chosen — and the window inherited that as its own
            # floor.
            panel.setMaximumWidth(16777215)
            sizes = self.splitter.sizes()
            if len(sizes) == 3 and sizes[2] != wanted:
                # The three have to add up to what there is.
                #
                # Handing the splitter numbers that total more than its
                # width makes it scale all three down to fit — including
                # the sidebar, which is why widening the panel squeezed
                # the tags. The sidebar keeps its width, the panel gets
                # what it asked for if there is room, and the list takes
                # whatever is left.
                total = sum(sizes)
                # The width the sidebar is *meant* to have, not the one
                # it happens to have while the window is still resizing —
                # measuring mid-flight bakes in a squeeze that then never
                # comes out.
                rail = (T["rail_w"] if self.sidebar._collapsed
                        else T["sidebar_w"])
                # The panel takes what is left over, never more.
                #
                # Giving up and returning was the squeeze: the splitter
                # was left to distribute the space itself, and its way of
                # doing that is to shrink everything proportionally —
                # sidebar included. Clamping the panel and always setting
                # the sizes means the sidebar is never one of the things
                # that gives.
                room = total - rail - self.LIST_MIN
                wanted = max(0, min(wanted, room))
                if wanted < self.PANEL_W:
                    wanted = 0              # no room for a panel at all
                self._set_sizes([rail, total - rail - wanted, wanted])


# --------------------------------------------------------------------------

def demo():
    app = QApplication(sys.argv)
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.stylesheet())
    app.setFont(QFont("Inter", 10))

    shell = Shell()
    shell.attach_accent(theme.AccentController(app))
    shell.sidebar.set_tags([
        ("80s", "#5dcaa5", 41), ("90s", "#7f77dd", 33),
        ("college radio", "#378add", 18), ("contemporary", "#d4537e", 27),
        ("experimental", "#ef9f27", 9), ("local", "#1d9e75", 12),
    ])

    listing = QListWidget()
    listing.setSelectionMode(QAbstractItemView.SingleSelection)
    listing.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    for name, sub in [
        ("WCJK 96.3 Jack FM", "Murfreesboro, TN"),
        ("KRVM 91.9", "Eugene, OR"),
        ("KOOL 96.9 FM", "80s"),
        ("0xfdb.FM", "experimental"),
        ("KHTT 106.9 KHits", "contemporary"),
        ("WAPS 91.3 The Summit", "Akron, OH"),
    ]:
        it = QListWidgetItem(f"{name}\n{sub}")
        it.setSizeHint(QSize(0, T["row_h"]))
        listing.addItem(it)

    page = QWidget()
    pl = QVBoxLayout(page)
    pl.setContentsMargins(14, 14, 8, 8)
    pl.setSpacing(10)
    search = search_field()
    pl.addWidget(search)
    pl.addWidget(listing, 1)
    count = QLabel("325 stations")
    count.setProperty("role", "secondary")
    pl.addWidget(count)

    shell.add_page("stations", page)

    from historypage import HistoryPage
    shell.add_page("history", HistoryPage())
    shell.player.set_now_playing("Need You Tonight", "INXS — Jack FM")
    shell.panel.set_track("Need You Tonight", "INXS")
    shell.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    demo()
