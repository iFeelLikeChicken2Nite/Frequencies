"""Frequencies — cropping a picture without leaving the app.

Artwork arrives wrong-shaped constantly: a press shot is landscape, a
scanned sleeve has a border, a search result has a watermark down one
side. Sending someone to an image editor for that is sending them away
from the thing they were doing.

So: a window with the picture, a rectangle you drag, and a Save. The
rectangle is square by default because everywhere this art is used is
square — the thumbnail, the dock, the tile — and a crop that does not
match the frame it lands in is a crop you will have to do again.

The picture as it was before the first crop is kept (task 76): a copy in
<config>/uncropped/, with a note of where the crop was. Cropping again
opens on the whole picture with the last crop marked, and "Undo the crop"
puts the whole picture back. The note remembers the cropped file's time
and size, so a file replaced since (a new download, new artwork chosen)
is not "uncropped" back to an older picture.
"""

import hashlib
import json
import os
import shutil

from PyQt5.QtCore import QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QPainter, QPen, QPixmap)
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout,
                             QLabel, QPushButton, QVBoxLayout, QWidget)

from theme import T

HANDLE = 9                      # px, the grab area at each corner
MIN_SIDE = 24                   # px in image space


class CropCanvas(QWidget):
    """The picture, with a rectangle you can drag and resize."""

    changed = pyqtSignal()

    def __init__(self, image, square=True, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 300)
        self.setMouseTracking(True)
        self._image = image
        self._square = square
        self._scale = 1.0
        self._offset = QPoint(0, 0)
        # In image coordinates, always: a selection stored in screen
        # pixels would move every time the window resized.
        self._sel = QRect()
        self._drag = None
        self._grab_at = QPoint()
        self._sel_at = QRect()
        self.reset()

    # -- geometry ----------------------------------------------------------

    def reset(self):
        """The largest centred square the picture allows."""
        w, h = self._image.width(), self._image.height()
        side = min(w, h)
        if self._square:
            self._sel = QRect((w - side) // 2, (h - side) // 2, side, side)
        else:
            self._sel = QRect(0, 0, w, h)
        self.update()
        self.changed.emit()

    def set_square(self, on):
        self._square = bool(on)
        if on:
            side = min(self._sel.width(), self._sel.height())
            self._sel.setWidth(side)
            self._sel.setHeight(side)
            self._clamp()
        self.update()
        self.changed.emit()

    def selection(self):
        return QRect(self._sel)

    def set_selection(self, rect):
        """Start from a rectangle chosen before (the last crop)."""
        if rect is None or rect.isEmpty():
            return
        self._sel = QRect(rect)
        self._clamp()
        self.update()
        self.changed.emit()

    def result(self):
        """The cropped picture, at its original resolution."""
        return self._image.copy(self._sel.intersected(self._image.rect()))

    def _fit(self):
        """Scale and offset that centre the picture in this widget."""
        if self._image.isNull():
            return 1.0, QPoint(0, 0)
        available = self.size() - QSize(24, 24)
        scale = min(available.width() / self._image.width(),
                    available.height() / self._image.height(), 1.0)
        shown = QSize(int(self._image.width() * scale),
                      int(self._image.height() * scale))
        offset = QPoint((self.width() - shown.width()) // 2,
                        (self.height() - shown.height()) // 2)
        return scale, offset

    def _to_screen(self, rect):
        return QRect(self._offset.x() + int(rect.x() * self._scale),
                     self._offset.y() + int(rect.y() * self._scale),
                     max(1, int(rect.width() * self._scale)),
                     max(1, int(rect.height() * self._scale)))

    def _to_image(self, point):
        if not self._scale:
            return QPoint(0, 0)
        return QPoint(int((point.x() - self._offset.x()) / self._scale),
                      int((point.y() - self._offset.y()) / self._scale))

    def _clamp(self):
        """Keep the selection inside the picture and above a minimum."""
        image = self._image.rect()
        side_w = max(MIN_SIDE, min(self._sel.width(), image.width()))
        side_h = max(MIN_SIDE, min(self._sel.height(), image.height()))
        if self._square:
            side_w = side_h = min(side_w, side_h)
        self._sel.setWidth(side_w)
        self._sel.setHeight(side_h)
        x = max(0, min(self._sel.x(), image.width() - side_w))
        y = max(0, min(self._sel.y(), image.height() - side_h))
        self._sel.moveTo(x, y)

    # -- painting ----------------------------------------------------------

    def paintEvent(self, _e):
        self._scale, self._offset = self._fit()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(T["bg0"]))

        shown = self._to_screen(self._image.rect())
        p.drawImage(shown, self._image)

        box = self._to_screen(self._sel)

        # Everything outside the selection goes dim — the point of the
        # window is to show what you are keeping, and the easiest way to
        # show that is to take the rest away.
        shade = QColor(0, 0, 0, 140)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(shade))
        p.drawRect(QRect(shown.left(), shown.top(),
                         shown.width(), box.top() - shown.top()))
        p.drawRect(QRect(shown.left(), box.bottom() + 1,
                         shown.width(), shown.bottom() - box.bottom()))
        p.drawRect(QRect(shown.left(), box.top(),
                         box.left() - shown.left(), box.height()))
        p.drawRect(QRect(box.right() + 1, box.top(),
                         shown.right() - box.right(), box.height()))

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(T["accent"]), 2))
        p.drawRect(box)

        # Thirds, which is how people actually place a subject.
        thin = QPen(QColor(255, 255, 255, 60), 1)
        p.setPen(thin)
        for i in (1, 2):
            x = box.left() + box.width() * i // 3
            y = box.top() + box.height() * i // 3
            p.drawLine(x, box.top(), x, box.bottom())
            p.drawLine(box.left(), y, box.right(), y)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T["accent"]))
        for corner in (box.topLeft(), box.topRight(),
                       box.bottomLeft(), box.bottomRight()):
            p.drawRect(QRect(corner.x() - HANDLE // 2, corner.y() - HANDLE // 2,
                             HANDLE, HANDLE))
        p.end()

    # -- interaction -------------------------------------------------------

    def _corner_at(self, pos):
        box = self._to_screen(self._sel)
        corners = {"tl": box.topLeft(), "tr": box.topRight(),
                   "bl": box.bottomLeft(), "br": box.bottomRight()}
        for name, point in corners.items():
            if (abs(pos.x() - point.x()) <= HANDLE
                    and abs(pos.y() - point.y()) <= HANDLE):
                return name
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self._grab_at = self._to_image(e.pos())
        self._sel_at = QRect(self._sel)
        corner = self._corner_at(e.pos())
        if corner:
            self._drag = corner
        elif self._to_screen(self._sel).contains(e.pos()):
            self._drag = "move"
        else:
            # Starting outside begins a fresh selection, which is what
            # dragging on an empty area means everywhere else.
            self._drag = "br"
            self._sel = QRect(self._grab_at, QSize(MIN_SIDE, MIN_SIDE))
            self._sel_at = QRect(self._sel)

    def mouseMoveEvent(self, e):
        if self._drag is None:
            # Only the resize cursor is ours to set; anywhere else the
            # desktop's own pointer is the right one.
            if self._corner_at(e.pos()):
                self.setCursor(Qt.SizeFDiagCursor)
            else:
                self.unsetCursor()
            return

        now = self._to_image(e.pos())
        dx, dy = now.x() - self._grab_at.x(), now.y() - self._grab_at.y()

        if self._drag == "move":
            self._sel.moveTo(self._sel_at.x() + dx, self._sel_at.y() + dy)
        else:
            rect = QRect(self._sel_at)
            if "l" in self._drag:
                rect.setLeft(rect.left() + dx)
            else:
                rect.setRight(rect.right() + dx)
            if "t" in self._drag:
                rect.setTop(rect.top() + dy)
            else:
                rect.setBottom(rect.bottom() + dy)
            rect = rect.normalized()
            if self._square:
                # The corner being dragged stays put; the opposite one
                # follows, so a square grows from where your hand is.
                side = max(rect.width(), rect.height())
                if "l" in self._drag:
                    rect.setLeft(rect.right() - side)
                else:
                    rect.setRight(rect.left() + side)
                if "t" in self._drag:
                    rect.setTop(rect.bottom() - side)
                else:
                    rect.setBottom(rect.top() + side)
            self._sel = rect

        self._clamp()
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, _e):
        self._drag = None
        self.changed.emit()


class CropDialog(QDialog):
    """Crop a picture and hand back the result."""

    # Always square.
    #
    # Every frame this artwork lands in is square — the thumbnail, the
    # dock, the tile — so a free crop is a crop that gets cropped again
    # by the app, off-centre, with no say in where. The option was a
    # choice between one good outcome and one bad one.
    def __init__(self, image, title="Crop", square=True, parent=None,
                 recropping=False):
        square = True
        # The window as owner, for the same reason as the artist editor:
        # the artist photo sits in a see-through column, and a dialog
        # parented there opened see-through too.
        super().__init__(parent.window() if parent is not None else None)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 520)
        self._result = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        self.canvas = CropCanvas(image, square=square)
        outer.addWidget(self.canvas, 1)

        row = QHBoxLayout()
        row.setSpacing(10)

        reset = QPushButton("Reset")
        reset.clicked.connect(self.canvas.reset)
        row.addWidget(reset)

        self.size_label = QLabel("")
        self.size_label.setProperty("role", "secondary")
        row.addWidget(self.size_label)
        row.addStretch(1)
        outer.addLayout(row)
        if recropping:
            # The whole picture is back, so say why the frame is not the
            # cropped one any more (task 76).
            again = QLabel("This is the whole picture. Your last crop is "
                           "marked: move it, or make it bigger.")
            again.setProperty("role", "secondary")
            again.setWordWrap(True)
            outer.addWidget(again)

        buttons = QDialogButtonBox(QDialogButtonBox.Save
                                   | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Use this crop")
        for button in buttons.buttons():
            button.setAutoDefault(False)
            button.setDefault(False)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.canvas.changed.connect(self._show_size)
        self._show_size()

    def _show_size(self):
        rect = self.canvas.selection()
        self.size_label.setText(f"{rect.width()} × {rect.height()} px")

    def _accept(self):
        self._result = self.canvas.result()
        self.accept()

    def result_image(self):
        return self._result


def _log(line):
    try:
        import crashlog
        crashlog.write(line)
    except Exception:  # noqa: BLE001
        pass


def _uncropped_dir():
    import storage
    return os.path.join(os.path.dirname(storage.settings_path()),
                        "uncropped")


def _keep_paths(path):
    """(kept original, note) for the picture at `path`."""
    key = hashlib.sha1(os.path.abspath(path).encode("utf-8")
                       ).hexdigest()[:20]
    folder = _uncropped_dir()
    return (os.path.join(folder, key + ".img"),
            os.path.join(folder, key + ".json"))


def _stamp(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    return [info.st_mtime_ns, info.st_size]


def _forget(path):
    for name in _keep_paths(path):
        try:
            os.remove(name)
        except OSError:
            pass


def uncropped(path):
    """The whole picture kept from before `path` was first cropped, and
    the last crop's rectangle in it: (file, QRect or None), or (None, None).

    A kept copy only counts while `path` is still the file the crop wrote.
    Replaced since (a new download, other artwork chosen), the copy is of
    an older picture: it is forgotten, and the new file is the whole one.
    """
    if not path:
        return None, None
    original, note = _keep_paths(path)
    try:
        with open(note, "r", encoding="utf-8") as handle:
            info = json.load(handle)
    except (OSError, ValueError):
        return None, None
    if (info.get("path") != os.path.abspath(path)
            or info.get("stamp") != _stamp(path)
            or not os.path.exists(original)):
        _forget(path)
        return None, None
    rect = info.get("rect") or []
    return original, (QRect(*rect) if len(rect) == 4 else None)


def crop_file(path, parent=None, square=True, title="Crop"):
    """Open a file, crop it, write it back. True if it was changed.

    Written back in place rather than to a new file: the station already
    points at this path, and a crop that leaves the old picture behind
    means choosing the right one again later.

    The whole picture is kept aside the first time (task 76), and every
    later crop is cut from it, so cropping again can widen the frame
    again and "Undo the crop" can put it all back.
    """
    import imagelock
    original, last = uncropped(path)
    image = imagelock.image(original or path)  # under the lock (task 54)
    if image.isNull():
        return False
    dialog = CropDialog(image, title=title, square=square, parent=parent,
                        recropping=original is not None)
    if last is not None:
        dialog.canvas.set_selection(last)
    if not dialog.exec_():
        return False
    out = dialog.result_image()
    if out is None or out.isNull():
        return False
    kept, note = _keep_paths(path)
    if original is None:
        # Kept before the file is touched: a crop that fails to save
        # must not have cost the picture.
        try:
            os.makedirs(os.path.dirname(kept), exist_ok=True)
            shutil.copyfile(path, kept)
        except OSError as exc:
            _log(f"[crop] could not keep the whole picture of "
                 f"{os.path.basename(path)} ({exc}); cropping anyway")
            kept = None
    suffix = os.path.splitext(path)[1].lower()
    fmt = "PNG" if suffix in (".png", "") else "JPEG"
    if not out.save(path, fmt, 92):
        if original is None and kept:
            _forget(path)
        return False
    if kept:
        rect = dialog.canvas.selection()
        try:
            with open(note, "w", encoding="utf-8") as handle:
                json.dump({"path": os.path.abspath(path),
                           "rect": [rect.x(), rect.y(), rect.width(),
                                    rect.height()],
                           "whole": [image.width(), image.height()],
                           "stamp": _stamp(path)}, handle)
        except OSError:
            pass
        _log(f"[crop] {os.path.basename(path)}: cropped to "
             f"{rect.width()}x{rect.height()} at {rect.x()},{rect.y()} of "
             f"the whole {image.width()}x{image.height()} picture "
             f"({'cropped again' if original else 'whole picture kept'})")
    return True


def undo_crop(path):
    """Put the whole picture back over its crop. True if it did."""
    original, _ = uncropped(path)
    if not original:
        return False
    try:
        shutil.copyfile(original, path)
    except OSError as exc:
        _log(f"[crop] could not undo the crop of {os.path.basename(path)}"
             f": {exc}")
        return False
    _forget(path)
    _log(f"[crop] {os.path.basename(path)}: crop undone, whole picture "
         f"back")
    return True


def crop_pixmap(pixmap, parent=None, square=True, title="Crop"):
    """Crop a picture already in memory. Returns a QPixmap, or None."""
    if pixmap is None or pixmap.isNull():
        return None
    dialog = CropDialog(pixmap.toImage(), title=title, square=square,
                        parent=parent)
    if not dialog.exec_():
        return None
    out = dialog.result_image()
    return QPixmap.fromImage(out) if out is not None and not out.isNull() \
        else None
