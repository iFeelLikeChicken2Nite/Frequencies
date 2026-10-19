"""Frequencies — the share window.

A station is shared two ways and they are not the same thing. The file
carries everything you did to it: tags, equalizer, artwork, corrections.
The URL carries only where the audio is — but it fits in a message, and
a QR code fits on a screen somebody else is pointing a phone at.

Shortening goes through is.gd, which is a request to someone else's
server, so it happens when the button is pressed and never on its own.
"""

import json
import urllib.parse
import urllib.request

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import (QColor, QGuiApplication, QImage, QPainter,
                         QPainterPath, QPen, QPixmap)
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QVBoxLayout)

from theme import T

USER_AGENT = "Frequencies/1.5"


def qr_pixmap(text, size=180, label="", accent=None):
    """The code as a QR, with a mark in the middle and a titled frame.

    Both decorations are allowed by the format rather than smuggled past
    it. A QR carries error correction — a small patch in the centre is
    within what it can lose and still be read — and the frame sits
    entirely outside the code, where there is nothing to damage.

    The centre patch is kept small deliberately. At the correction level
    a payload this size allows, a large logo is the difference between a
    code that scans and a code that scans on some phones.
    """
    if not text:
        return None
    try:
        import segno
    except ImportError:
        return None

    # Level M first; a code past M's 2331 bytes drops to L (2953).
    # Only M was tried, so a code between the two came back empty and
    # the window said "QR needs the segno package" (build 105, task 130e).
    code = None
    for level in ("m", "l"):
        try:
            code = segno.make(text, error=level)
            break
        except Exception:                       # noqa: BLE001
            continue
    if code is None:
        return None
    matrix = [list(row) for row in code.matrix]

    count = len(matrix)
    if not count:
        return None

    quiet = 2
    frame = 22 if label else 0
    total = count + quiet * 2
    scale = max(2, (size - frame * 2) // total)
    inner = total * scale
    side = inner + frame * 2

    image = QImage(side, side, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)

    # The code itself, on white — a QR on a tinted background is a QR
    # some scanners refuse.
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(QRectF(frame, frame, inner, inner), 6, 6)
    p.setBrush(QColor("#101012"))
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                p.drawRect(frame + (x + quiet) * scale,
                           frame + (y + quiet) * scale, scale, scale)

    # The mark: a clear disc with three arcs in it, like the tile.
    patch = max(scale * 3, int(inner * 0.15))
    patch -= patch % 2
    cx = frame + inner / 2.0
    box = QRectF(cx - patch / 2.0, cx - patch / 2.0, patch, patch)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(box.adjusted(-2, -2, 2, 2), 4, 4)

    pen = QPen(QColor("#101012"), max(1.5, patch * 0.075))
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    for i in range(3):
        r = patch * (0.16 + i * 0.15)
        arc = QRectF(cx - r, cx - r, r * 2, r * 2)
        p.drawArc(arc, -55 * 16, 110 * 16)      # right
        p.drawArc(arc, 125 * 16, 110 * 16)      # left
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#101012"))
    dot = max(2.0, patch * 0.07)
    p.drawEllipse(QRectF(cx - dot, cx - dot, dot * 2, dot * 2))

    if label:
        _draw_frame(p, side, frame, label, accent)
    p.end()
    return QPixmap.fromImage(image)


def _draw_frame(p, side, frame, label, accent=None):
    """A border with the station's name set into the bottom of it.

    Drawn in the station's own colour — the one its artwork gives the
    player bar, or the one its name gives its tile. A code for a station
    should look like that station and not like every other code.
    """
    from PyQt5.QtGui import QFont, QFontMetrics

    font = QFont()
    font.setPixelSize(max(9, frame - 10))
    font.setBold(True)
    metrics = QFontMetrics(font)
    # The whole name, however long.
    #
    # It was elided to whatever the frame had spare, so a station with a
    # real name — "Nightwave Plaza Ambient Stream" — came out cut off on
    # the one image people scan from across a room. The font shrinks
    # instead, down to a floor where it is still legible.
    text = label
    width = metrics.horizontalAdvance(text) + 14
    while width > side - frame * 2 and font.pixelSize() > 7:
        font.setPixelSize(font.pixelSize() - 1)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 14
    if width > side - frame * 2:
        text = metrics.elidedText(label, Qt.ElideRight, side - frame * 2 - 14)
        width = metrics.horizontalAdvance(text) + 14

    colour = QColor(accent) if accent else QColor(T["text2"])
    line = QPen(colour, 2)
    p.setPen(line)
    p.setBrush(Qt.NoBrush)
    inset = frame / 2.0
    box = QRectF(inset, inset, side - frame, side - frame)

    # A rounded rectangle with a piece taken out of the bottom for the
    # name — drawn as one path so the corners are curves rather than four
    # lines meeting at a point.
    gap_left = box.center().x() - width / 2.0
    gap_right = box.center().x() + width / 2.0
    radius = min(14.0, box.width() / 8.0)

    path = QPainterPath()
    path.moveTo(gap_right, box.bottom())
    path.lineTo(box.right() - radius, box.bottom())
    path.quadTo(box.right(), box.bottom(), box.right(),
                box.bottom() - radius)
    path.lineTo(box.right(), box.top() + radius)
    path.quadTo(box.right(), box.top(), box.right() - radius, box.top())
    path.lineTo(box.left() + radius, box.top())
    path.quadTo(box.left(), box.top(), box.left(), box.top() + radius)
    path.lineTo(box.left(), box.bottom() - radius)
    path.quadTo(box.left(), box.bottom(), box.left() + radius,
                box.bottom())
    path.lineTo(gap_left, box.bottom())
    p.drawPath(path)

    p.setFont(font)
    p.setPen(colour)
    p.drawText(QRectF(gap_left, box.bottom() - frame / 2.0,
                      width, frame),
               Qt.AlignCenter, text)


class ShareDialog(QDialog):
    """The link, a short one, a copy button and a code to point a phone at."""

    save_requested = pyqtSignal()

    def __init__(self, name, url, code="", dropped=False, accent=None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Share — {name}")
        self.setMinimumWidth(520)
        self._url = url
        self._code = code or url

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)

        heading = QLabel(name)
        heading.setProperty("role", "display")
        heading.setWordWrap(True)
        outer.addWidget(heading)

        note = QLabel(
            "The code carries the whole station — artwork, tags, "
            "equalizer, corrections — so scanning it in Frequencies "
            "rebuilds it as you have it. The plain URL is only where the "
            "audio is.")
        note.setProperty("role", "secondary")
        note.setWordWrap(True)
        outer.addWidget(note)

        # The code in the middle, Save and Copy right under it, and the
        # stream URL at the bottom. The thumbnail note and the code as
        # text went (build 112, task 151: the user's marked-up
        # screenshot); the picture is what gets shared.
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignCenter)
        code = qr_pixmap(self._code, 360, name, accent)
        if code is None:
            self.qr.setText("This frequency has too much on it for a QR "
                            "code.")
            self.qr.setProperty("role", "secondary")
        else:
            self.qr.setPixmap(code)
        outer.addWidget(self.qr, 0, Qt.AlignHCenter)

        code_row = QHBoxLayout()
        code_row.setSpacing(8)
        code_row.addStretch(1)
        self.save_image_btn = QPushButton("Save the code…")
        self.save_image_btn.clicked.connect(self._save_image)
        self.save_image_btn.setEnabled(code is not None)
        code_row.addWidget(self.save_image_btn)
        self.copy_image_btn = QPushButton("Copy to clipboard")
        self.copy_image_btn.clicked.connect(self._copy_image)
        self.copy_image_btn.setEnabled(code is not None)
        code_row.addWidget(self.copy_image_btn)
        code_row.addStretch(1)
        outer.addLayout(code_row)

        # Only says something after a button is pressed.
        self.status = QLabel("")
        self.status.setProperty("role", "secondary")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)
        self.status.hide()
        outer.addWidget(self.status)

        outer.addSpacing(6)
        outer.addWidget(QLabel("Stream URL"))
        self.full = QLineEdit(url)
        self.full.setReadOnly(True)
        self.full.setCursorPosition(0)
        full_row = QHBoxLayout()
        full_row.setSpacing(8)
        full_row.addWidget(self.full, 1)
        self.copy_full = QPushButton("Copy")
        self.copy_full.clicked.connect(lambda: self._copy(self.full))
        full_row.addWidget(self.copy_full)
        outer.addLayout(full_row)


        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        for button in buttons.buttons():
            button.setAutoDefault(False)
            button.setDefault(False)
        outer.addWidget(buttons)

    # -- actions -----------------------------------------------------------

    def _save_image(self):
        """Write the QR out as a picture, to send or to print."""
        from PyQt5.QtWidgets import QFileDialog

        pixmap = self.qr.pixmap()
        if pixmap is None or pixmap.isNull():
            self._say("There is no code to save.")
            return
        safe = "".join(c for c in self.windowTitle().split("—")[-1]
                       if c.isalnum() or c in " -_").strip()
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save the code", f"{safe or 'station'}.png",
            "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        self._say("Saved." if pixmap.save(path, "PNG")
                  else "Couldn't write that file.")

    def _say(self, text):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _copy_image(self):
        """Put the QR picture itself on the clipboard, to paste anywhere."""
        pixmap = self.qr.pixmap()
        if pixmap is None or pixmap.isNull():
            self._say("There is no code to copy.")
            return
        QGuiApplication.clipboard().setPixmap(pixmap)
        self._say("Copied the code picture.")

    def _copy(self, field):
        text = field.text().strip()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self._say(f"Copied: {text}")
