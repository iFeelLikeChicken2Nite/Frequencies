"""Frequencies — band logos in place of the artist's name (build 141, task 203).

The developer gives an artist a logo (a PNG with transparency) in a dev
build's Edit this artist… > Logo, sets its size and place in a small
editor (LogoEditor), and Publish my photos sends it with the photos: a
pack of its own beside the photo pack (packbuild.logo_pack, base32 twice,
logo-pack.json). Every user's app fetches it at the photo pack's times
(photopack.Keeper.check_logos), whether or not they opted in to the
photos, unless they switch it off: Settings > Lyrics and artists > "Show
band logos in place of plain text" (on by default, no prompt). Builds
before 141 never ask for those files.

Where it shows: the artist photo's band in the side panel (ArtistHeader,
through LogoMark) and Comfort View's artist column (LogoName), in place
of the name, which stays as the tooltip and accessible name. No logo, or
the switch off: the name as text, as before.

The logo is never recolored. When it would be lost against the panel
behind it (a white logo on a light theme), a soft outline of the opposite
tone is drawn around it.
"""

import os
import shutil

from PyQt5.QtCore import (QEasingCurve, QObject, QPointF, QRectF, QSize, Qt,
                          QTimer, QVariantAnimation, pyqtSignal)
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
                             QGridLayout, QLabel, QPushButton, QSizePolicy,
                             QSlider, QVBoxLayout, QWidget)

FIELDS = ("logo", "logo_scale", "logo_x", "logo_y")
LOGO_FOLDER = "band_logos"          # the dev build's copies (config folder)
BOX = 96                            # the name's area, made room for a logo
COMFORT_BOX = 110                   # the same in Comfort View
SCALE_MIN, SCALE_MAX = 0.3, 2.0

_on = [True]


def set_on(on):
    """The Settings switch, read once at start-up and when it changes."""
    _on[0] = bool(on)


def is_on():
    return _on[0]


def _log(text):
    try:
        import crashlog
        crashlog.write("[logo] " + text)
    except Exception:                           # noqa: BLE001
        pass


# -- which logo -----------------------------------------------------------------

def placement(entry):
    """(scale, x, y) from a correction or pack entry, within bounds."""
    def number(name, default, low, high):
        try:
            value = float((entry or {}).get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))
    return (number("logo_scale", 1.0, SCALE_MIN, SCALE_MAX),
            number("logo_x", 0.0, -1.0, 1.0),
            number("logo_y", 0.0, -1.0, 1.0))


def _match(table, artist):
    """This artist's entry in one layer: its own name (or the same name
    spelled differently), or an entry shown under that name ("Show as").
    Never a collaboration's first artist: the logo would hide the others'
    names."""
    import storage
    key, how = storage.find_artist(table, artist)
    if key and how in ("", "spelled differently"):
        return table.get(key) or {}
    loose = storage._loose_artist(artist)
    for entry in table.values():
        if isinstance(entry, dict) and entry.get("name") \
                and storage._loose_artist(entry["name"]) == loose:
            return entry
    return {}


_said = set()


def find(artist):
    """{"path", "scale", "x", "y", "from"} for this artist's logo, or None
    (switched off, none, or its file isn't here). The developer's own
    correction first (dev builds), then the logo pack."""
    artist = " ".join((artist or "").split())
    if not artist or not _on[0]:
        return None
    import storage
    import photopack
    layers = (("yours", storage.load_artist_overrides(), None),
              ("pack", photopack.logo_names(), photopack.logo_entry))
    for source, table, resolve in layers:
        entry = _match(table, artist)
        if not entry.get("logo"):
            continue
        if resolve is not None:
            key = next((k for k, v in table.items() if v is entry), "")
            entry = resolve(key)
        path = str(entry.get("logo") or "").strip()
        if not path or not os.path.isfile(path):
            continue
        scale, x, y = placement(entry)
        if artist.lower() not in _said:
            _said.add(artist.lower())
            _log(f"'{artist}': logo shown ({source}, "
                 f"{os.path.basename(path)}, size {round(scale * 100)}%)")
        return {"path": path, "scale": scale, "x": x, "y": y,
                "from": source}
    return None


def keep_logo(artist, path):
    """Copy a chosen logo into the dev build's own folder, so it stays
    when the source folder it came from is replaced by the next build's.
    Returns the copy's path (or the path as given, if it can't)."""
    import packbuild
    import storage
    path = (path or "").strip()
    if not path or not os.path.isfile(path):
        return path
    folder = os.path.join(os.path.dirname(storage.settings_path()),
                          LOGO_FOLDER)
    if os.path.dirname(os.path.abspath(path)) == folder:
        return path
    target = os.path.join(folder, packbuild.slug(artist) + (
        os.path.splitext(path)[1].lower() or ".png"))
    try:
        os.makedirs(folder, exist_ok=True)
        shutil.copyfile(path, target)
    except OSError:
        return path
    return target


# -- drawing --------------------------------------------------------------------

_images = {}            # path -> (mtime, QImage)
_drawn = {}             # (path, mtime, w, h, tone) -> (QPixmap, halo or None)


def image(path):
    """The logo's picture, decoded once per file version."""
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return QImage()
    have = _images.get(path)
    if have and have[0] == stamp:
        return have[1]
    import imagelock
    img = imagelock.image(path)
    if img.isNull():
        return img
    img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    _images[path] = (stamp, img)
    return img


def _lum(color):
    return (0.2126 * color.redF() + 0.7152 * color.greenF()
            + 0.0722 * color.blueF())


def outline_for(img, tone):
    """The outline's color when the logo would be lost on `tone`, else
    None: when fewer than 40% of its solid pixels stand apart from the
    panel. Dark logos get a light outline, light ones a dark one."""
    small = img.scaled(48, 48, Qt.KeepAspectRatio,
                       Qt.SmoothTransformation).convertToFormat(
        QImage.Format_ARGB32)
    behind = _lum(QColor(tone))
    solid = apart = 0
    total = 0.0
    for y in range(small.height()):
        for x in range(small.width()):
            pixel = QColor.fromRgba(small.pixel(x, y))
            if pixel.alpha() < 128:
                continue
            light = _lum(pixel)
            solid += 1
            total += light
            if abs(light - behind) >= 0.35:
                apart += 1
    if not solid or apart / solid >= 0.4:
        return None
    return QColor(0, 0, 0) if total / solid > 0.5 else QColor(255, 255, 255)


def _with_outline(pm, color, radius=3):
    """`pm` with a soft outline of `color` around its shape."""
    tint = QPixmap(pm.size())
    tint.fill(Qt.transparent)
    p = QPainter(tint)
    p.drawPixmap(0, 0, pm)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(tint.rect(), color)
    p.end()
    out = QPixmap(pm.width() + radius * 2, pm.height() + radius * 2)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setOpacity(0.35)
    for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1),
                   (0, 1), (1, 1)):
        for step in range(1, radius + 1):
            p.drawPixmap(radius + dx * step, radius + dy * step, tint)
    p.setOpacity(1.0)
    p.drawPixmap(radius, radius, pm)
    p.end()
    return out


def place(box, size, scale, x, y, bounds=None):
    """Where the logo goes: fitted into `box` (keeping its shape), times
    `scale`, at the box's bottom left (where the name sits), moved by
    x / y box widths / heights; then kept inside `bounds`, smaller if it
    has to be."""
    if size.width() <= 0 or size.height() <= 0 or box.width() <= 0:
        return QRectF()
    fit = min(box.width() / size.width(), box.height() / size.height())
    w, h = size.width() * fit * scale, size.height() * fit * scale
    left = box.left() + x * box.width()
    top = box.bottom() - h + y * box.height()
    rect = QRectF(left, top, w, h)
    if bounds is None:
        return rect
    shrink = min(1.0, bounds.width() / w, bounds.height() / h)
    if shrink < 1.0:
        rect.setSize(rect.size() * shrink)
    rect.moveLeft(min(max(rect.left(), bounds.left()),
                      bounds.right() - rect.width()))
    rect.moveTop(min(max(rect.top(), bounds.top()),
                     bounds.bottom() - rect.height()))
    return rect


def draw(painter, rect, path, tone):
    """The logo in `rect`, with an outline when `tone` would swallow it."""
    img = image(path)
    if img.isNull() or rect.isEmpty():
        return
    w, h = max(1, round(rect.width())), max(1, round(rect.height()))
    tone = QColor(tone).name()
    key = (path, _images[path][0], w, h, tone)
    have = _drawn.get(key)
    if have is None:
        if len(_drawn) > 24:
            _drawn.clear()
        pm = QPixmap.fromImage(img.scaled(w, h, Qt.IgnoreAspectRatio,
                                          Qt.SmoothTransformation))
        color = outline_for(img, tone)
        have = (pm, _with_outline(pm, color) if color is not None else None)
        _drawn[key] = have
    pm, outlined = have
    if outlined is not None:
        pad = (outlined.width() - pm.width()) / 2
        painter.drawPixmap(QPointF(rect.left() - pad, rect.top() - pad),
                           outlined)
    else:
        painter.drawPixmap(QPointF(rect.left(), rect.top()), pm)


# -- the fade (build 143, task 206) ---------------------------------------------

FADE_MS = 350               # one fade
SLIDE_PX = 16               # how far the logo travels while it fades
SETTLE_MS = 120             # a change must hold this long before it moves


class Fade(QObject):
    """The logo's state: 1 = shown, 0 = gone.

    It follows `want` (a logo exists AND the artist's picture is on screen,
    i.e. the antenna is not). Coming in, it fades up while sliding up;
    going out, it fades while sliding down. A change that flips back
    within SETTLE_MS (a lookup clearing and refilling for the same artist)
    never starts a move, so the logo does not flicker between songs.
    """

    changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self._want = False
        self._key = None
        self._anim = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SETTLE_MS)
        self._timer.timeout.connect(self._go)

    def _step(self, v):
        self.value = float(v)
        self.changed.emit(self.value)

    def _go(self):
        self._stop()
        end = 1.0 if self._want else 0.0
        if abs(self.value - end) < 0.001:
            return
        # A fresh animation each time: one restarted from its end jumps.
        anim = QVariantAnimation(self)
        anim.setDuration(FADE_MS)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(float(self.value))
        anim.setEndValue(end)
        anim.valueChanged.connect(self._step)
        self._anim = anim
        anim.start(QVariantAnimation.DeleteWhenStopped)

    def _stop(self):
        if self._anim is not None:
            try:
                self._anim.valueChanged.disconnect(self._step)
                self._anim.stop()
            except RuntimeError:        # already deleted
                pass
            self._anim = None

    def snap(self, value):
        self._timer.stop()
        self._stop()
        self.value = float(value)
        self.changed.emit(self.value)

    def follow(self, want, key=None, animate=True):
        """`key` names the logo (its file). A different logo starts over."""
        want = bool(want)
        if key != self._key:
            self._key = key
            if self.value > 0 or self._want:
                self.snap(0.0)          # another artist: out, then in
                self._want = False
        if want == self._want:
            return
        self._want = want
        if not animate:
            self.snap(1.0 if want else 0.0)
            return
        self._timer.start()


def header_box(width, height):
    """The logo's area in the artist photo's band: where the name sits,
    taller."""
    box = min(BOX, height * 0.5)
    return QRectF(12, height - 8 - box, max(0, width - 24), box)


class LogoMark(QWidget):
    """Lies over the artist photo's band and draws the logo where the
    name would be. Clicks pass through to the photo (its dev menu)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.info = None
        self.tone = "#000000"
        self.fade = Fade(self)
        self.fade.changed.connect(self._moved)
        self.hide()

    def set_logo(self, info, tone=None, show=True):
        """`show`: the artist's picture is up (no antenna)."""
        self.info = info
        if tone:
            self.tone = tone
        self.fade.follow(info is not None and show,
                         info["path"] if info else None)
        self._moved()

    def _moved(self, *_):
        self.setVisible(self.info is not None and self.fade.value > 0)
        self.update()

    def paintEvent(self, _e):
        if not self.info or self.fade.value <= 0:
            return
        img = image(self.info["path"])
        bounds = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        rect = place(header_box(self.width(), self.height()), img.size(),
                     self.info["scale"], self.info["x"], self.info["y"],
                     bounds)
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setOpacity(self.fade.value)
        p.translate(0, (1.0 - self.fade.value) * SLIDE_PX)
        draw(p, rect, self.info["path"], self.tone)
        p.end()


class LogoName(QWidget):
    """Comfort View's artist name as a logo: as wide as the column lets
    it, its height following from that."""

    TONE = "#101010"            # Comfort View is always dark

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.info = None
        self.name = ""
        self.fade = Fade(self)
        self.fade.changed.connect(lambda *_: self.update())
        policy = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.hide()

    def set_logo(self, info, name="", show=True):
        """`show`: the artist's picture is up. Without it the plain name
        stays here, in the logo's own space, so nothing jumps."""
        self.info = info
        self.name = name or ""
        self.fade.follow(info is not None and show,
                         info["path"] if info else None)
        self.setToolTip(name if info else "")
        self.setAccessibleName(name if info else "")
        self.setVisible(info is not None)
        self.updateGeometry()
        self.update()

    def _rect(self, width):
        if not self.info:
            return QRectF()
        img = image(self.info["path"])
        # Kept inside the column, never taller than twice the box. Up and
        # down mean nothing in a column, so only the size and left-right
        # count here.
        return place(QRectF(0, 0, width, COMFORT_BOX), img.size(),
                     self.info["scale"], self.info["x"], 0,
                     QRectF(0, -COMFORT_BOX, width, 2 * COMFORT_BOX))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        rect = self._rect(w)
        return max(0, round(rect.height())) if not rect.isEmpty() else 0

    def sizeHint(self):
        return QSize(220, self.heightForWidth(220))

    def minimumSizeHint(self):
        return QSize(0, 0)

    def paintEvent(self, _e):
        rect = self._rect(self.width())
        if rect.isEmpty():
            return
        rect.moveTop(0)
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        v = self.fade.value
        if v < 1.0 and self.name:
            font = QFont(self.font())
            font.setPixelSize(22)
            p.setFont(font)
            p.setPen(QColor(255, 255, 255))
            p.setOpacity(1.0 - v)
            p.drawText(QRectF(0, 0, self.width(), self.height()),
                       int(Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap),
                       self.name)
        if v > 0:
            p.setOpacity(v)
            p.translate(0, (1.0 - v) * SLIDE_PX)
            draw(p, rect, self.info["path"], self.TONE)
        p.end()


# -- the dev build's editor -----------------------------------------------------

class _Preview(QWidget):
    """The artist photo's band, drawn as the side panel draws it, with
    the logo on it. Drag to move it, the wheel to size it."""

    moved = pyqtSignal(float, float)
    sized = pyqtSignal(float)

    def __init__(self, path, photo="", parent=None):
        super().__init__(parent)
        self.path = path
        self.photo = QPixmap()
        if photo and os.path.isfile(photo):
            import imagelock
            self.photo = imagelock.pixmap(photo)
        self.scale, self.x, self.y = 1.0, 0.0, 0.0
        self.light = False
        self._drag = None
        self.setMinimumSize(420, 240)
        self.setCursor(Qt.OpenHandCursor)

    def tone(self):
        return "#f3f3f3" if self.light else "#16181d"

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(self.tone()))
        if not self.photo.isNull():
            from sidepanel import faded, rounded
            scaled = self.photo.scaled(self.size(),
                                       Qt.KeepAspectRatioByExpanding,
                                       Qt.SmoothTransformation)
            x = max(0, (scaled.width() - self.width()) // 2)
            y = int(max(0, scaled.height() - self.height()) * 0.22)
            p.drawPixmap(0, 0, faded(rounded(scaled.copy(
                x, y, self.width(), self.height()), 10), self.size(),
                self.tone()))
        p.setPen(QColor(128, 128, 128, 90))
        p.drawRect(header_box(self.width(), self.height()))
        draw(p, self._rect(), self.path, self.tone())
        p.end()

    def _rect(self):
        return place(header_box(self.width(), self.height()),
                     image(self.path).size(), self.scale, self.x, self.y,
                     QRectF(self.rect()).adjusted(4, 4, -4, -4))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = (e.pos(), self.x, self.y)
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        start, x, y = self._drag
        box = header_box(self.width(), self.height())
        delta = e.pos() - start
        self.moved.emit(max(-1.0, min(1.0, x + delta.x() / box.width())),
                        max(-1.0, min(1.0, y + delta.y() / box.height())))

    def mouseReleaseEvent(self, _e):
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, e):
        step = 0.05 if e.angleDelta().y() > 0 else -0.05
        self.sized.emit(max(SCALE_MIN, min(SCALE_MAX, self.scale + step)))


class LogoEditor(QDialog):
    """Size and place a band logo before it is saved, and again later
    (Edit this artist… > Adjust…). What is set here is what users see."""

    def __init__(self, artist, path, place=(1.0, 0.0, 0.0), photo="",
                 parent=None):
        super().__init__(parent.window() if parent is not None else None)
        self.setWindowTitle(f"Logo — {artist}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        note = QLabel("This is how the logo sits in the artist panel, in "
                      "place of the name. Drag it to move it, scroll to "
                      "size it. Comfort View uses the same size.")
        note.setWordWrap(True)
        note.setProperty("role", "secondary")
        lay.addWidget(note)
        self.preview = _Preview(path, photo, self)
        lay.addWidget(self.preview, 1)

        grid = QGridLayout()
        self.sliders = {}
        for row, (name, label, low, high) in enumerate((
                ("scale", "Size", round(SCALE_MIN * 100),
                 round(SCALE_MAX * 100)),
                ("x", "Left – right", -100, 100),
                ("y", "Up – down", -100, 100))):
            slider = QSlider(Qt.Horizontal)
            slider.setRange(low, high)
            value = QLabel()
            value.setMinimumWidth(48)
            slider.valueChanged.connect(self._changed)
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(slider, row, 1)
            grid.addWidget(value, row, 2)
            self.sliders[name] = (slider, value)
        lay.addLayout(grid)
        self.light = QCheckBox("Preview on a light theme")
        self.light.toggled.connect(self._light)
        lay.addWidget(self.light)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Use this")
        reset = QPushButton("Reset")
        buttons.addButton(reset, QDialogButtonBox.ResetRole)
        reset.clicked.connect(lambda: self.set_place(1.0, 0.0, 0.0))
        for button in buttons.buttons():
            button.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.preview.moved.connect(
            lambda x, y: self.set_place(self.preview.scale, x, y))
        self.preview.sized.connect(
            lambda s: self.set_place(s, self.preview.x, self.preview.y))
        self.set_place(*place)
        self.resize(600, 620)

    def set_place(self, scale, x, y):
        for name, value in (("scale", scale), ("x", x), ("y", y)):
            slider = self.sliders[name][0]
            slider.blockSignals(True)
            slider.setValue(round(value * 100))
            slider.blockSignals(False)
        self._changed()

    def _changed(self, *_):
        values = {name: slider.value()
                  for name, (slider, _label) in self.sliders.items()}
        for name, (_slider, label) in self.sliders.items():
            label.setText(f"{values[name]}%" if name == "scale"
                          else str(values[name]))
        self.preview.scale = values["scale"] / 100
        self.preview.x = values["x"] / 100
        self.preview.y = values["y"] / 100
        self.preview.update()

    def _light(self, on):
        self.preview.light = bool(on)
        self.preview.update()

    def place(self):
        """(scale, x, y) as set."""
        return (self.preview.scale, self.preview.x, self.preview.y)
