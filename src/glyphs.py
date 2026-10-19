"""Frequencies — glyphs that are images rather than drawings.

Some marks cannot honestly be redrawn from rectangles and arcs. The
iBroadcast logo is one: every attempt to approximate it came out lopsided
in a different way, and the answer to "why is the B asymmetrical" is that
a hand-built B is asymmetrical unless it is drawn by someone drawing a
typeface.

So the real thing is embedded here as base64 rather than shipped as a
loose file. A file in src/icons/ works too, and takes precedence, but it
has to survive packaging — and the build was copying only *.py, which is
exactly how a corrected icon can appear to change nothing at all.
"""

import base64

from PyQt5.QtGui import QImage, QPixmap

IBROADCAST_PNG = (
    "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"
    "AABJRU5ErkJggg=="
)

_cache = {}


def embedded(name, size, colour=None):
    """A QPixmap for an embedded glyph, or None if there isn't one.

    The image is a white mark on transparency, so tinting is a matter of
    keeping the alpha and replacing the colour — which is what lets one
    file serve the accent, the rail grey and the disabled tone.
    """
    blob = {"ibroadcast": IBROADCAST_PNG}.get(name)
    if not blob:
        return None

    key = (name, size, colour)
    if key in _cache:
        return _cache[key]

    image = QImage()
    if not image.loadFromData(base64.b64decode(blob)):
        return None
    image = image.convertToFormat(QImage.Format_ARGB32)
    if colour:
        # Tinted by compositing, not by walking the pixels.
        #
        # This looped over every pixel in Python, reading and writing one
        # at a time — a few thousand calls per icon. During an accent
        # fade the colour changes on every frame, so the cache misses and
        # every icon on screen paid that cost thirty times a second,
        # which is what made the window go choppy.
        #
        # SourceIn keeps the source's colour and the destination's alpha,
        # which is exactly what the loop was working out by hand, and Qt
        # does it in one pass of compiled code.
        from PyQt5.QtGui import QColor, QPainter

        p = QPainter(image)
        p.setCompositionMode(QPainter.CompositionMode_SourceIn)
        p.fillRect(image.rect(), QColor(colour))
        p.end()
    pixmap = QPixmap.fromImage(
        image.scaled(size, size, 1, 1))       # KeepAspectRatio, Smooth
    _cache[key] = pixmap
    return pixmap
