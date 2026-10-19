"""Frequencies — station list.

A model plus a painted delegate, replacing the two-lines-crammed-into-a-row
placeholder. Each row carries a logo, the station name, a secondary line of
location and stream detail, its tags as tinted pills, and a live indicator
when it's the station currently playing.

Stations without a logo get a monogram tile colored from a hash of the
name, so the list is scannable before any favicons have been fetched.
"""

import hashlib
import time
import os
import re
import math

from PyQt5.QtCore import (QAbstractListModel, QElapsedTimer, QModelIndex,
                          QPersistentModelIndex, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer,
                          pyqtSignal)
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath,
                         QPen, QPixmap)
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QListView,
                             QStyle, QStyledItemDelegate)

import theme
from theme import T, tint_color

STATION_ROLE = Qt.UserRole + 1


# --------------------------------------------------------------------------
# Logos
# --------------------------------------------------------------------------

MONOGRAM_HUES = [12, 32, 48, 96, 152, 188, 210, 262, 292, 330]


def _short_date(stamp):
    """MM/DD, or MM/DD/YY once it is not this year.

    Never a full timestamp. A column is read as a set rather than one line
    at a time, so every entry has to be the same shape — which is why this
    no longer says "today" or "4d" for the recent ones: a column of dates
    with words scattered through it cannot be scanned.
    """
    try:
        value = float(stamp or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""

    import datetime
    when = datetime.datetime.fromtimestamp(value)
    now = datetime.datetime.now()
    if when.year == now.year:
        return f"{when.month:02d}/{when.day:02d}"
    # The year only when it is not this one: two characters that are the
    # same on every row are two characters wasted.
    return f"{when.month:02d}/{when.day:02d}/{when.year % 100:02d}"


def call_sign(name):
    """A station's call sign, if the name starts with one.

    Four letters is the American standard and three the older one, so a
    leading run of capitals that long is almost always the call sign —
    "KEXP 90.3 Seattle", "WFMU", "CBC Radio One". Anything else is a
    name, and gets initials instead.
    """
    first = (name or "").strip().split()
    if not first:
        return ""
    word = first[0].strip("-—·:")
    if 3 <= len(word) <= 4 and word.isalpha() and word.isupper():
        return word
    return ""


def initials(name, limit=4):
    """Up to four characters for a station with no call sign.

    Letters are taken evenly across the words rather than one from each:
    "Nightwave Plaza" reads as NIPL, where one-per-word and then padding
    the first gave NIGP, which looks like a typo.
    """
    words = [w for w in re.split(r"[^0-9A-Za-z]+", name or "") if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:limit].upper()

    words = words[:limit]
    per = max(1, limit // len(words))
    out = ""
    for word in words:
        out += word[:per]
        if len(out) >= limit:
            break
    return out[:limit].upper()


def station_hue(name):
    """The hue a station is drawn in, from its name.

    Shared with the monogram so a station's colour is one colour, not two
    that happen to be near each other.
    """
    digest = hashlib.md5((name or "?").encode("utf-8")).digest()
    return MONOGRAM_HUES[digest[0] % len(MONOGRAM_HUES)]


def station_color(station):
    """A station's own colour: from its artwork if it has any.

    The cover is what the player bar takes its accent from, so a shared
    code framed in the same colour looks like the same station. Without
    artwork, the name decides — which is what the list already does.
    """
    import os
    from theme import dominant_color

    path = (station or {}).get("custom_art_path") or ""
    if path and os.path.exists(path):
        import imagelock
        image = imagelock.image(path)  # under the image lock (task 54)
        if not image.isNull():
            return dominant_color(image)

    # Lighter than the tile's own fill.
    #
    # The monogram sits behind white letters, so it is dark on purpose;
    # a border has to be visible against a dark dialog instead. Same hue,
    # so it is recognisably the same station's colour.
    colour = QColor()
    colour.setHsl(station_hue(_display_name(station)), 130, 150)
    return colour


def _display_name(station):
    return ((station or {}).get("custom_name")
            or (station or {}).get("name") or "?")


# Finished placeholders, and the glyph's proportions. Both are fixed for
# the life of the process: the drawing never changes, only its size and
# its colours.
_notes_cache = {}
_NOTES_CACHE_MAX = 64
_ink_box = None


def _ink_bounds():
    """Where the glyph's ink sits, as fractions of its square.

    Measured once, on a small render, using QImage's own alpha channel
    rather than a Python loop over every pixel.
    """
    global _ink_box
    if _ink_box is not None:
        return _ink_box

    probe = 128
    layer = _notes_ink(probe, QColor("#ffffff"))
    mask = layer.toImage().convertToFormat(QImage.Format_ARGB32)
    left, top, right, bottom = probe, probe, -1, -1
    for y in range(probe):
        line = mask.constScanLine(y)
        row = bytes(line.asarray(probe * 4)) if hasattr(line, "asarray") \
            else bytes(line)[:probe * 4]
        for x in range(probe):
            if row[x * 4 + 3] > 40:             # alpha, in ARGB32
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y
    if right < left:
        _ink_box = (0.0, 0.0, 1.0, 1.0)
    else:
        _ink_box = (left / probe, top / probe,
                    (right - left + 1) / probe, (bottom - top + 1) / probe)
    return _ink_box


def _notes_ink(side, colour):
    """The pair of quavers alone, on transparency.

    Drawn without its background so the ink can be measured: where the
    shape actually lands is not where its coordinates suggest, because
    stems, beams and rotated heads all overhang differently.
    """
    # An image with a real alpha channel, not a pixmap.
    #
    # A QPixmap lives in the window system's format, and on some
    # platforms that has no alpha to antialias into — so a curve drawn on
    # one comes out stepped, which is the jaggedness on the note heads.
    # ARGB32 premultiplied is what Qt's own rasteriser wants, and the
    # edges come out smooth.
    canvas = QImage(side, side, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setRenderHint(QPainter.HighQualityAntialiasing)

    # Drawn to the proportions of the reference: a solid beam slanting
    # up to the right, two vertical stems, and oval heads tilted the way
    # engraved notation tilts them. The beam is a filled shape rather
    # than a thick line — a line's rounded ends do not sit flush with
    # the stems, which is what made the old one look hand-drawn.
    u = side / 40.0
    p.setPen(Qt.NoPen)
    p.setBrush(colour)

    from PyQt5.QtGui import QPolygonF

    left_x, right_x = 14.0, 31.0      # stem centres
    beam_left_y, beam_right_y = 11.5, 5.0
    beam_depth = 5.0
    stem_w = 2.6
    left_head_y, right_head_y = 28.5, 24.0

    # The beam: four corners, so both edges stay parallel to the slant.
    p.drawPolygon(QPolygonF([
        QPointF((left_x - stem_w / 2) * u, beam_left_y * u),
        QPointF((right_x + stem_w / 2) * u, beam_right_y * u),
        QPointF((right_x + stem_w / 2) * u, (beam_right_y + beam_depth) * u),
        QPointF((left_x - stem_w / 2) * u, (beam_left_y + beam_depth) * u),
    ]))

    # Stems, down from the beam to each head.
    p.drawRect(QRectF((left_x - stem_w / 2) * u, beam_left_y * u,
                      stem_w * u, (left_head_y - beam_left_y) * u))
    p.drawRect(QRectF((right_x - stem_w / 2) * u, beam_right_y * u,
                      stem_w * u, (right_head_y - beam_right_y) * u))

    # Heads: ovals tilted up to the right, hung on the left of each stem
    # as they are in engraving.
    for cx, cy in ((left_x - 4.6, left_head_y), (right_x - 4.6, right_head_y)):
        p.save()
        p.translate(cx * u, cy * u)
        p.rotate(-22)
        p.drawEllipse(QRectF(-5.6 * u, -4.0 * u, 11.2 * u, 8.0 * u))
        p.restore()

    p.end()
    return QPixmap.fromImage(canvas)


def beamed_notes(size=40, ink=None, bg=None) -> QPixmap:
    """A pair of beamed quavers on a grey field, centred by measurement.

    The shape is drawn on its own, its ink is measured, and it is then
    placed by that measurement rather than by where the drawing
    coordinates happen to put it. Eyeballing a glyph's centre is how it
    ends up sitting low and to the left — the numbers know better.
    """
    colour = QColor(ink or "#8a8a93")
    key = (int(size), colour.name(), QColor(bg or "#26262c").name())
    hit = _notes_cache.get(key)
    if hit is not None:
        return hit

    work = max(size * 4, 160)
    layer = _notes_ink(work, colour)

    # The shape's outline, measured once ever.
    #
    # This scanned every pixel of the working image in Python — at 512px
    # that is a 2048 square, four million pixelColor calls, and the stall
    # log caught it holding the window for eight seconds on a station
    # change. The glyph never changes, so its proportions are worked out
    # on the first call and scaled from then on.
    bounds = _ink_bounds()
    ink_rect = QRect(int(bounds[0] * work), int(bounds[1] * work),
                     max(1, int(bounds[2] * work)),
                     max(1, int(bounds[3] * work)))

    tile = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    tile.fill(Qt.transparent)
    p = QPainter(tile)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setRenderHint(QPainter.HighQualityAntialiasing)

    p.setPen(Qt.NoPen)
    p.setBrush(QColor(bg or "#26262c"))
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.14, size * 0.14)

    # Fit the ink to a share of the tile, then centre it on both axes.
    target = size * 0.52
    scale = target / max(ink_rect.width(), ink_rect.height())
    shown = layer.copy(ink_rect).scaled(
        max(1, int(ink_rect.width() * scale)),
        max(1, int(ink_rect.height() * scale)),
        Qt.KeepAspectRatio, Qt.SmoothTransformation)
    # Nudged left, because the eye does not centre on the bounding box.
    #
    # The two heads sit low and left while the beam runs up to the right
    # and carries most of the ink with it, so a shape centred by its
    # outline reads as sitting right of centre. A small shift back puts
    # the visual weight where the middle looks like it is.
    nudge = int(round(size * 0.035))
    p.drawPixmap(int((size - shown.width()) / 2.0) - nudge,
                 int((size - shown.height()) / 2.0), shown)
    p.end()
    pm = QPixmap.fromImage(tile)

    if len(_notes_cache) > _NOTES_CACHE_MAX:
        _notes_cache.clear()
    _notes_cache[key] = pm
    return pm


def monogram(name: str, size: int = 40) -> QPixmap:
    """A stable, distinct tile for a station with no artwork.

    The hue comes from a hash of the name, so a station looks the same on
    every launch and two adjacent stations rarely collide.
    """
    digest = hashlib.md5(name.encode("utf-8")).digest()
    hue = MONOGRAM_HUES[digest[0] % len(MONOGRAM_HUES)]

    bg = QColor()
    bg.setHsl(hue, 90, 62)
    fg = QColor()
    fg.setHsl(hue, 120, 190)

    # The call sign if the name carries one, otherwise up to four
    # initials. Two characters left most of a tile empty, and a call sign
    # is what the station calls itself — "KEXP" is more use than "K9".
    letters = call_sign(name) or initials(name)

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(QRectF(0, 0, size, size), 5, 5)
    f = QFont()
    # Four characters need to be smaller than two did, or they run off
    # the edges of the tile.
    f.setPixelSize(int(size * (0.36 if len(letters) <= 2 else
                               0.28 if len(letters) == 3 else 0.24)))
    f.setWeight(QFont.Medium)
    p.setFont(f)
    p.setPen(fg)
    # All of them, not the first two.
    #
    # The call sign and the initials were computed properly and then
    # sliced back to two characters on the way to the canvas, which is why
    # "KEXP" kept coming out "KE".
    p.drawText(QRect(0, 0, size, size), Qt.AlignCenter, letters)
    p.end()
    return pm


class LogoCache:
    """Keeps decoded logo pixmaps in memory, keyed by station id.

    Hand it a fetcher that takes (station_id, url, done_callback) and does
    the network work off the GUI thread -- your existing workers module
    already does exactly this shape for Shazam thumbnails.
    """

    def __init__(self, size=40, fetcher=None, on_ready=None):
        self.size = size
        self._fetcher = fetcher
        self._on_ready = on_ready
        self._cache = {}
        self._pending = set()
        self._real = set()          # ids whose pixmap is a real logo
        self._tried = {}            # id -> url already asked for
        self._mono = {}             # id -> name its monogram was drawn from

    def forget(self, sid):
        """Drop everything remembered about one station's thumbnail.

        The cache is built to avoid re-reading a file it has already
        seen, which is right until the answer changes. Choosing artwork
        is exactly that moment, and nothing was telling the cache — so a
        station that had been drawn once as a monogram went on being
        drawn as a monogram.
        """
        self._cache.pop(sid, None)
        self._tried.pop(sid, None)
        self._real.discard(sid)
        self._pending.discard(sid)

    def _monogram(self, sid, station):
        name = station.get("name", "?")
        self._mono[sid] = name
        return monogram(name, self.size)

    def redraw_monograms(self):
        """Draw the letter tiles again (brat mode changes their case, task
        48). Real logos are left alone; nothing is fetched again."""
        for sid, name in list(self._mono.items()):
            if sid in self._cache and sid not in self._real:
                self._cache[sid] = monogram(name, self.size)

    def set_fetcher(self, fetcher):
        """Fetchers arrive after the list does, because the thing that owns
        the network lives further up. Anything already showing a monogram
        gets another chance when one turns up — otherwise a station painted
        before this point keeps its monogram forever, which is exactly the
        bug that made station art look broken."""
        self._fetcher = fetcher
        self._tried.clear()

    def get(self, station) -> QPixmap:
        sid = station["id"]
        url = station.get("logo") or ""

        local = bool(url) and not url.startswith(("http://", "https://"))
        # A local file is identified by its path *and* when it changed.
        #
        # Chosen artwork always lands at the same path for a station —
        # station-art/<id>.jpg — so a second picture, or a crop of the
        # first, left the path identical and the cache saw nothing new.
        # The art was on disk and the row went on showing the old one.
        # A file on disk is decided here, before any of the caching
        # below gets a say.
        #
        # That logic is built around network fetches — what has been
        # tried, what is pending, whether a fetcher exists — and a local
        # path kept falling into one of its early returns and handing
        # back the monogram it drew the first time. A file is cheap to
        # check: its path and its modification time say everything, and
        # if they match what was drawn last time the cached pixmap is
        # still right.
        if local:
            try:
                stamp = f"{url}@{os.path.getmtime(url):.0f}"
            except OSError:
                # Pointing at a file that is gone: fall back to the
                # monogram rather than to a blank square.
                if sid not in self._cache:
                    self._cache[sid] = self._monogram(sid, station)
                return self._cache[sid]

            if self._tried.get(sid) == stamp and sid in self._cache:
                return self._cache[sid]

            from PyQt5.QtGui import QPixmapCache
            QPixmapCache.remove(url)
            import imagelock
            pm = imagelock.pixmap(url)     # under the lock (task 54)
            self._tried[sid] = stamp
            self._real.discard(sid)
            if not pm.isNull():
                self._cache[sid] = self._round(pm.scaled(
                    self.size, self.size, Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation))
                self._real.add(sid)
            elif sid not in self._cache:
                self._cache[sid] = self._monogram(sid, station)
            return self._cache[sid]

        stamp = url
        if sid in self._cache and (
                (sid in self._real and self._tried.get(sid) == stamp)
                or self._tried.get(sid) == stamp
                or not url
                or not self._fetcher):
            return self._cache[sid]

        if sid not in self._cache:
            self._cache[sid] = self._monogram(sid, station)

        if url and self._fetcher and sid not in self._pending:
            self._tried[sid] = stamp
            self._pending.add(sid)
            self._fetcher(sid, url, self._store)
        return self._cache[sid]

    def _store(self, sid, data):
        self._pending.discard(sid)
        pm = QPixmap()
        if not data or not pm.loadFromData(data):
            return
        self._real.add(sid)
        self._cache[sid] = self._round(
            pm.scaled(self.size, self.size,
                      Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        if self._on_ready:
            self._on_ready(sid)

    def _round(self, pm):
        out = QPixmap(self.size, self.size)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.size, self.size), 5, 5)
        p.setClipPath(path)
        p.drawPixmap(0, 0, pm)
        p.end()
        return out


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------

class StationModel(QAbstractListModel):
    order_changed = pyqtSignal()
    refreshed = pyqtSignal()        # the rows were replaced wholesale
    rows_arrived = pyqtSignal(int, int)     # first row, how many
    rows_left = pyqtSignal()

    """Stations are plain dicts:

        {"id", "name", "location", "codec", "bitrate",
         "tags": [(name, color)], "logo": url or None}
    """

    def __init__(self, stations=None, parent=None):
        super().__init__(parent)
        self._all = list(stations or [])
        self._rows = list(self._all)

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.isValid():
            return base | Qt.ItemIsDragEnabled
        return base | Qt.ItemIsDropEnabled

    def supportedDropActions(self):
        return Qt.MoveAction

    def moveRows(self, src_parent, src_row, count, dst_parent, dst_row):
        """Manual sort. Reordering a filtered view still moves the station
        to the right place in the full list, so a drag to the top means the
        top even when tags are hiding half the stations."""
        if src_row == dst_row or src_row + count == dst_row:
            return False
        self.beginMoveRows(QModelIndex(), src_row, src_row + count - 1,
                           QModelIndex(), dst_row)
        moving = self._rows[src_row:src_row + count]
        del self._rows[src_row:src_row + count]
        insert = dst_row - count if dst_row > src_row else dst_row
        self._rows[insert:insert] = moving
        self.endMoveRows()
        self._resync_all()
        self.order_changed.emit()
        return True

    def _resync_all(self):
        """Push the visible order back onto the unfiltered list."""
        visible = {s["id"] for s in self._rows}
        it = iter(self._rows)
        self._all = [next(it) if s["id"] in visible else s for s in self._all]

    def order(self):
        return [s["id"] for s in self._all]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        st = self._rows[index.row()]
        if role == STATION_ROLE:
            return st
        if role == Qt.DisplayRole:
            return st.get("name", "")
        # No tooltip.
        #
        # It repeated the name and location already printed on the row,
        # so it added nothing and appeared over the list every time the
        # pointer rested anywhere.
        return None

    def set_stations(self, stations):
        """Take a new set of rows, telling Qt what actually changed.

        A full reset throws the list away and builds it again, which is
        why removing one station made the whole thing blink — and why
        nothing could animate on arrival or departure: a reset carries no
        information about which rows are new and which have gone.

        So the ids are compared. Same ids, same order: the contents are
        swapped in place and only the painting is refreshed. One run
        removed or one run added: Qt is told exactly that, and the view
        animates the rows involved. Anything more tangled than that —
        a re-sort, a filter change — still resets, because describing it
        as a series of moves would cost more than it saves.
        """
        before = [r.get("id") for r in self._rows]
        after = [r.get("id") for r in stations]

        if before == after:
            self._all = list(stations)
            self._rows = list(self._all)
            if self._rows:
                self.dataChanged.emit(self.index(0, 0),
                                      self.index(len(self._rows) - 1, 0))
            return

        removed = self._single_run(before, after)
        if removed is not None:
            start, count = removed
            self.beginRemoveRows(QModelIndex(), start, start + count - 1)
            self._all = list(stations)
            self._rows = list(self._all)
            self.endRemoveRows()
            self.rows_left.emit()
            return

        added = self._single_run(after, before)
        if added is not None:
            start, count = added
            self.beginInsertRows(QModelIndex(), start, start + count - 1)
            self._all = list(stations)
            self._rows = list(self._all)
            self.endInsertRows()
            self.rows_arrived.emit(start, count)
            return

        self.beginResetModel()
        self._all = list(stations)
        self._rows = list(self._all)
        self.endResetModel()
        self.refreshed.emit()

    @staticmethod
    def _single_run(longer, shorter):
        """(start, count) when `shorter` is `longer` with one run cut out.

        Anything else returns None, and the caller falls back to a reset
        — this is deliberately only the simple case, because the simple
        case is the one that happens when a station is added or removed.
        """
        if len(longer) <= len(shorter):
            return None
        count = len(longer) - len(shorter)
        for start in range(len(shorter) + 1):
            if (longer[:start] == shorter[:start]
                    and longer[start + count:] == shorter[start:]):
                return start, count
        return None

    def apply_filter(self, text="", tags=(), exclude=()):
        """Free text, tags to include, and tags that rule a station out.

        Included tags are ORed: picking 80s and 90s asks for stations
        tagged either, not both. Two tags almost never appear on the same
        station, so requiring all of them turned every second selection
        into an empty list — which is the opposite of what adding a tag
        looks like it should do.

        Exclusion still ANDs, because it means "not this, and also not
        that", which is how nobody is surprised by it. Supports tag:name
        and -tag:name in the text.
        """
        text = (text or "").strip().lower()
        tags = set(tags)
        exclude = set(exclude)

        for token in list(text.split()):
            if token.startswith("tag:"):
                tags.add(token[4:])
                text = text.replace(token, "").strip()
            elif token.startswith("-tag:"):
                exclude.add(token[5:])
                text = text.replace(token, "").strip()

        def keep(st):
            names = {t[0].lower() for t in st.get("tags", ())}
            if exclude and {t.lower() for t in exclude} & names:
                return False
            if tags and not {t.lower() for t in tags} & names:
                return False
            if not text:
                return True
            hay = " ".join(str(st.get(k) or "")
                           for k in ("name", "location", "codec")).lower()
            return text in hay

        # Same result as last time means nothing to tell the view.
        #
        # This ran on every reload — every identified song, every health
        # change — and a reset throws the list away and rebuilds it. That
        # is the blink when a station is removed, and it is also why the
        # per-row arrival never showed: a reset has no idea which rows
        # are new.
        wanted = [s for s in self._all if keep(s)]
        if [r.get("id") for r in wanted] == [r.get("id") for r in self._rows]:
            self._rows = wanted
            if self._rows:
                self.dataChanged.emit(self.index(0, 0),
                                      self.index(len(self._rows) - 1, 0))
            # The count still matters, even when nothing moved.
            #
            # The early return added for the unchanged case forgot to
            # give one back, so the caller got None and the search box
            # ended up offering to search "None frequencies".
            return len(self._rows)

        self.beginResetModel()
        self._rows = wanted
        self.endResetModel()
        return len(self._rows)

    def station_at(self, row):
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_of(self, station_id):
        for i, s in enumerate(self._rows):
            if s["id"] == station_id:
                return i
        return -1

    def touch(self, station_id):
        row = self.row_of(station_id)
        if row >= 0:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)


# --------------------------------------------------------------------------
# Delegate
# --------------------------------------------------------------------------

# Which glyph belongs to which kind of station, using the same names the
# Add Frequency tiles use — so a station looks like the thing you added.
KIND_GLYPH = {
    "youtube": "youtube",
    "twitch": "twitch",
    "ibroadcast": "ibroadcast",
    "local": "folder",
    "folder": "folder",
}


class StationDelegate(QStyledItemDelegate):

    COMFORTABLE = 56
    COMPACT = 40

    ACTION_W = 26

    KIND_BADGE = 15

    @staticmethod
    def _kind_of(station):
        """Which glyph this station's kind calls for, or "".

        Worked out from the fields a station carries rather than from a
        "kind" key, because a station saved before that key existed does
        not have one and normalisation drops what it does not know.
        """
        # The row, or the station inside it.
        #
        # A row carries id, name, location, logo and tags — and none of
        # the fields that say what kind of station it is. Those live on
        # the station the row was built from, so asking the row directly
        # always came back empty and no glyph was ever drawn.
        station = station or {}
        station = station.get("_source") or station
        if station.get("ibroadcast_urls"):
            return KIND_GLYPH.get("ibroadcast", "")
        if station.get("twitch_channel"):
            return KIND_GLYPH.get("twitch", "")
        if station.get("playlist_url"):
            return KIND_GLYPH.get("youtube", "")
        if station.get("local_path"):
            return KIND_GLYPH.get("local", "")
        return KIND_GLYPH.get(station.get("kind") or "", "")

    def _draw_new(self, p, right, r):
        """The green "New!" pill. Returns the x it starts at."""
        from PyQt5.QtGui import QFont, QFontMetrics

        font = QFont(self.f_sub)
        font.setPixelSize(10)
        font.setBold(True)
        text = "New!"
        width = QFontMetrics(font).horizontalAdvance(text) + 12
        height = 16
        box = QRectF(right - width, r.center().y() - height / 2.0,
                     width, height)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2e9e5b"))
        p.drawRoundedRect(box, height / 2.0, height / 2.0)
        p.setFont(font)
        p.setPen(QColor("#ffffff"))
        p.drawText(box, Qt.AlignCenter, text)
        p.restore()
        return int(box.left())

    def _draw_kind(self, p, station, x, y, art):
        """A small mark saying where this station comes from.

        Only for the ones that are not ordinary streams: a radio station
        is the default and does not need saying. The glyph is the same
        one on its tile in Add Frequency, so the two read as the same
        thing.
        """
        # Worked out from what the station has, not only from "kind".
        #
        # A station saved before "kind" existed does not carry one, and
        # normalisation drops keys it does not know — so the fields that
        # define the station are the reliable answer.
        name = self._kind_of(station)
        if not name:
            return

        from shell import icon as _icon
        size = self.KIND_BADGE
        left = x + art - size + 2
        top = y + art - size + 2
        # A disc behind it, or a pale glyph vanishes into pale artwork.
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T["bg0"]))
        p.drawEllipse(QRectF(left - 2, top - 2, size + 4, size + 4))
        p.restore()
        p.drawPixmap(left, top,
                     _icon(name, size, T["text2"]).pixmap(size, size))

    def __init__(self, logos: LogoCache, parent=None):
        super().__init__(parent)
        self.logos = logos
        self.row_height = self.COMFORTABLE
        self.playing_id = None
        self.is_playing = False
        self.elapsed = 0.0        # seconds, advanced by the view's timer
        # Set on the Discover list: every row gets a + to add it, or a − if
        # it is already in the library. Off everywhere else, because in your
        # own list the button would mean "remove", which is a menu's job.
        self.show_actions = False
        self.show_columns = False

        self.f_name = QFont()
        self.f_name.setPixelSize(13)
        self.f_sub = QFont()
        self.f_sub.setPixelSize(11)
        self.f_pill = QFont()
        self.f_pill.setPixelSize(10)

    def sizeHint(self, option, index):
        return QSize(0, self.row_height)

    # -- painting ---------------------------------------------------------

    def paint(self, p, option, index):
        st = index.data(STATION_ROLE)
        if not st:
            return

        # Arriving rows rise and fade into place.
        # The view that is painting, not whatever owns the delegate.
        #
        # A delegate's parent is often the page rather than the list, so
        # parent() had no row_progress on it and every row drew as
        # already arrived — the animation ran and nothing showed it.
        # option.widget is the view doing the painting, by definition.
        view = option.widget if option.widget is not None else self.parent()
        progress = 1.0
        if hasattr(view, "row_progress"):
            progress = view.row_progress(index.row())
        if progress <= 0.001:
            return
        if progress < 1.0:
            eased = 1.0 - (1.0 - progress) ** 3
            p.save()
            p.setOpacity(eased)
            p.translate(0, int(view.ROW_RISE * (1.0 - eased)))

        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        r = option.rect.adjusted(6, 1, -6, -1)
        playing = st["id"] == self.playing_id
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)

        down = bool(st.get("_down"))

        # Playing and selected are different states and must look different:
        # the accent tint means "this is what you are hearing", a neutral
        # fill means "this is what your keyboard is on". Underneath both,
        # every other row alternates so the eye can track across a wide row
        # without losing the line.
        accent = QColor(T["accent"])
        live = QColor(T["playing"])
        if playing:
            bg = QColor(live)
            bg.setAlpha(52 if selected else 38)
        elif selected:
            bg = QColor(T["bg3"])
        elif hovered:
            bg = QColor(T["bg2"])
        elif index.row() % 2:
            bg = QColor(T["row_alt"])
        else:
            bg = None

        if bg:
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(r), 6, 6)

        if down:
            # A dead station should be obvious while scanning the list, not
            # something you notice only by reading the second line.
            #
            # A red wash only, with no outline. The outline made a down
            # row look like an error box and fought with the selection
            # highlight; the wash and the red second line say it enough.
            glow = QColor(T["down"])
            glow.setAlpha(28 if not (playing or selected) else 46)
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawRoundedRect(QRectF(r), 6, 6)

        primary = live if playing else QColor(T["text"])
        secondary = QColor(T["down"]) if down else QColor(T["text2"])

        compact = self.row_height <= self.COMPACT
        art = 0 if compact else 40
        x = r.left() + 10

        if art:
            pm = self.logos.get(st)
            top = r.top() + (r.height() - art) // 2
            p.drawPixmap(x, top, pm)
            self._draw_kind(p, st, x, top, art)
            x += art + 12
        else:
            # Compact rows have no artwork to put a badge on, so the mark
            # stands on its own where the artwork would have been. A
            # station's kind is worth knowing at any row height.
            side = self.KIND_BADGE
            if self._kind_of(st):
                top = r.top() + (r.height() - side) // 2
                from shell import icon as _icon
                p.drawPixmap(x, top, _icon(self._kind_of(st), side,
                                           T["text2"]).pixmap(side, side))
                x += side + 10

        # right-hand furniture is measured first so the name can elide
        right = r.right() - 10

        # "New!" for a station imported and not yet played. Placed with
        # the other right-hand furniture so the name elides around it
        # rather than running underneath.
        source = st.get("_source") or st
        if source.get("is_new"):
            right = self._draw_new(p, right, r) - 8

        if self.show_columns:
            right = self._draw_columns(p, right, r, st) - 10
        if self.show_actions:
            right = self._draw_action(p, right, r, st, hovered) - 8
        if playing:
            right = self._draw_live(p, right, r, live) - 10
        if not compact:
            right = self._draw_tags(p, right, r, st.get("tags", ())) - 8

        avail = max(40, right - x)

        fm_name = QFontMetrics(self.f_name)
        fm_sub = QFontMetrics(self.f_sub)
        sub = self._subtitle(st)

        if compact or not sub:
            p.setFont(self.f_name)
            p.setPen(primary)
            p.drawText(QRect(x, r.top(), avail, r.height()),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       fm_name.elidedText(st["name"], Qt.ElideRight, avail))
        else:
            block = fm_name.height() + fm_sub.height() + 2
            top = r.top() + (r.height() - block) // 2
            p.setFont(self.f_name)
            p.setPen(primary)
            p.drawText(QRect(x, top, avail, fm_name.height()),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       fm_name.elidedText(st["name"], Qt.ElideRight, avail))
            p.setFont(self.f_sub)
            p.setPen(secondary)
            p.drawText(QRect(x, top + fm_name.height() + 2, avail, fm_sub.height()),
                       Qt.AlignVCenter | Qt.AlignLeft,
                       fm_sub.elidedText(sub, Qt.ElideRight, avail))

        p.restore()
        if progress < 1.0:
            # Matches the save() taken when the row was still arriving.
            p.restore()

    def action_rect(self, option_rect):
        """Where the +/− sits, so the view can tell a click on it from a
        click on the row."""
        r = option_rect.adjusted(6, 1, -6, -1)
        size = 20
        return QRect(r.right() - 10 - size, r.top() + (r.height() - size) // 2,
                     size, size)

    def _draw_action(self, p, right, r, st, hovered):
        size = 20
        box = QRect(right - size, r.top() + (r.height() - size) // 2,
                    size, size)
        owned = bool(st.get("_owned"))
        colour = QColor(T["text2"] if not hovered else T["text"])
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T["bg3"]) if hovered else QColor(T["bg2"]))
        p.drawRoundedRect(QRectF(box), 5, 5)
        pen = QPen(QColor(T["accent"]) if owned else colour, 1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        cx, cy = box.center().x() + 1, box.center().y() + 1
        p.drawLine(cx - 5, cy, cx + 5, cy)
        if not owned:
            p.drawLine(cx, cy - 5, cx, cy + 5)
        p.setPen(Qt.NoPen)
        return box.left()

    def _subtitle(self, st):
        bits = []
        if st.get("location"):
            bits.append(st["location"])
        if st.get("bitrate"):
            bits.append(f"{st['bitrate']}k")
        if st.get("codec"):
            bits.append(st["codec"])
        return " · ".join(bits)

    def _draw_tags(self, p, right, r, tags):
        """Paint up to two pills, right to left. Returns the new left edge."""
        if not tags:
            return right
        fm = QFontMetrics(self.f_pill)
        p.setFont(self.f_pill)
        for name, colour in list(tags)[:2][::-1]:
            w = fm.width(name) + 18
            h = 18
            box = QRectF(right - w, r.top() + (r.height() - h) / 2.0, w, h)
            c = QColor(colour)
            fill = QColor(c)
            fill.setAlpha(34)
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(box, 9, 9)
            p.setPen(c)
            p.drawText(box, Qt.AlignCenter, name)
            right -= w + 6
        return right

    COLUMNS = (("plays", 46, Qt.AlignRight, "Plays"),
               ("last", 74, Qt.AlignRight, "Last played"),
               ("added", 74, Qt.AlignRight, "Added"))

    def column_boxes(self, right, width_of_row):
        """Where each column sits, so a header can line up with them.

        The header is drawn by a separate widget above the list, and a
        header that does not sit exactly over its column is worse than no
        header at all — so both ask the delegate rather than each doing
        its own arithmetic.
        """
        boxes = []
        for _key, width, align, title in self.COLUMNS:
            boxes.append((title, right - width, width, align))
            right -= width + 8
        return boxes, right

    def _draw_columns(self, p, right, r, st):
        """Play count, last played and date added, right-aligned.

        Fixed widths so the figures line up down the list — the point of a
        column is that you can compare it without reading it, and ragged
        numbers cannot be compared at a glance.
        """
        p.save()
        p.setFont(self.f_sub)
        fm = QFontMetrics(self.f_sub)
        for key, width, align, _title in self.COLUMNS:
            text = self._column_text(key, st)
            box = QRect(right - width, r.top(), width, r.height())
            p.setPen(QColor(T["text3"] if not text else T["text2"]))
            p.drawText(box, align | Qt.AlignVCenter,
                       fm.elidedText(text or "—", Qt.ElideRight, width))
            right -= width + 8
        p.restore()
        return right

    def _column_text(self, key, st):
        source = st.get("_source") or {}
        if key == "plays":
            count = int(source.get("play_count") or 0)
            return str(count) if count else ""
        stamp = source.get("last_played" if key == "last" else "added_at")
        return _short_date(stamp)

    def _draw_live(self, p, right, r, accent):
        """Three bars that move while the stream is running, flat when paused.

        Cheaper and calmer than an animated GIF, and it reads as sound.
        """
        bar_w, gap = 3, 3
        total = bar_w * 3 + gap * 2
        x = right - total
        cy = r.center().y()
        p.setPen(Qt.NoPen)
        p.setBrush(accent)

        # Each bar runs on its own sine, so the three never line up and the
        # motion reads as level meters rather than a repeating loop.
        waves = ((5.1, 0.0, 5.5, 3.0), (6.7, 1.9, 7.5, 4.5), (5.9, 3.7, 6.0, 3.5))
        for i, (speed, offset, span, base) in enumerate(waves):
            if self.is_playing:
                h = base + span * (0.5 + 0.5 * math.sin(
                    self.elapsed * speed + offset))
            else:
                # Paused: bars of different heights, still standing.
                #
                # Three equal dots is what a menu button looks like, and
                # people were clicking it expecting one. Uneven bars sitting
                # still read as a meter at rest, which is what it is.
                h = (7.0, 4.0, 9.0)[i]
            p.drawRoundedRect(
                QRectF(x + i * (bar_w + gap), cy - h / 2.0, bar_w, h), 1.5, 1.5)
        return right - total


class StationGridDelegate(QStyledItemDelegate):
    """Artwork-first tiles, for when you recognize stations by their logo
    faster than by their call sign."""

    TILE_W = 148
    ART = 148
    TEXT = 46

    def __init__(self, logos: LogoCache, parent=None, tile=None):
        super().__init__(parent)
        if tile:
            # A smaller tile for a strip rather than a full grid page.
            self.TILE_W = self.ART = tile
        self.logos = logos
        self.playing_id = None
        self.is_playing = False
        self.elapsed = 0.0

        self.f_name = QFont()
        self.f_name.setPixelSize(12)
        self.f_sub = QFont()
        self.f_sub.setPixelSize(11)
        self.f_pill = QFont()
        self.f_pill.setPixelSize(10)

    def sizeHint(self, option, index):
        return QSize(self.TILE_W, self.ART + self.TEXT)

    def paint(self, p, option, index):
        st = index.data(STATION_ROLE)
        if not st:
            return

        # Arriving rows rise and fade into place.
        # The view that is painting, not whatever owns the delegate.
        #
        # A delegate's parent is often the page rather than the list, so
        # parent() had no row_progress on it and every row drew as
        # already arrived — the animation ran and nothing showed it.
        # option.widget is the view doing the painting, by definition.
        view = option.widget if option.widget is not None else self.parent()
        progress = 1.0
        if hasattr(view, "row_progress"):
            progress = view.row_progress(index.row())
        if progress <= 0.001:
            return
        if progress < 1.0:
            eased = 1.0 - (1.0 - progress) ** 3
            p.save()
            p.setOpacity(eased)
            p.translate(0, int(view.ROW_RISE * (1.0 - eased)))

        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        r = option.rect
        playing = st["id"] == self.playing_id
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        accent = QColor(T["playing"])       # same rule as the list rows

        if playing or selected or hovered:
            bg = QColor(accent)
            if playing:
                bg.setAlpha(52 if selected else 38)
            elif selected:
                bg = QColor(T["bg3"])
            else:
                bg = QColor(T["bg2"])
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            # The same inset on every side.
            #
            # -6 all round grew the highlight *outside* the tile, so in a
            # strip it ran under its neighbours and the gap between two
            # highlighted tiles was smaller than the gap between two
            # plain ones. Drawn inside its own rectangle, every tile has
            # the same padding whatever is next to it.
            # The whole tile, caption included.
            p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1), 10, 10)

        art = self.logos.get(st)
        p.drawPixmap(r.left(), r.top(), art)
        self._kind_badge(p, st, r)

        if playing:
            self._badge(p, r, accent)

        fm_name = QFontMetrics(self.f_name)
        fm_sub = QFontMetrics(self.f_sub)
        y = r.top() + self.ART + 9
        p.setFont(self.f_name)
        p.setPen(accent if playing else QColor(T["text"]))
        p.drawText(QRect(r.left(), y, self.TILE_W, fm_name.height()),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   fm_name.elidedText(st["name"], Qt.ElideRight, self.TILE_W))

        # No tags on a tile.
        #
        # Two pills under a 120px thumbnail is a row of coloured smudges,
        # and the card that opens on a click has room to list them
        # properly. What the tile shows instead is where the station is,
        # which is the other thing you want at a glance.
        line_y = y + fm_name.height() + 3
        sub = st.get("location") or ""
        if sub:
            p.setFont(self.f_sub)
            p.setPen(QColor(T["text2"]))
            p.drawText(QRect(r.left(), line_y, self.TILE_W, fm_sub.height()),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       fm_sub.elidedText(sub, Qt.ElideRight, self.TILE_W))
        p.restore()

    KIND_BADGE = 16

    def _kind_badge(self, p, station, r):
        """The station's kind, in the corner of its thumbnail.

        The same mark the list rows carry. A grid of artwork says even
        less about where a station comes from than a row does, so if
        anything it is needed more here.
        """
        name = StationDelegate._kind_of(station)
        if not name:
            return
        from shell import icon as _icon

        side = self.KIND_BADGE
        left = r.left() + self.ART - side - 4
        top = r.top() + self.ART - side - 4
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T["bg0"]))
        p.drawEllipse(QRectF(left - 3, top - 3, side + 6, side + 6))
        p.restore()
        p.drawPixmap(left, top, _icon(name, side, T["text2"]).pixmap(side,
                                                                    side))

    def _tag_pills(self, p, r, y, tags):
        """As many as fit across the tile, in tag order."""
        p.setFont(self.f_pill)
        fm = QFontMetrics(self.f_pill)
        x = r.left()
        limit = r.left() + self.TILE_W
        for name, colour in tags:
            w = fm.horizontalAdvance(name) + 12
            if x + w > limit:
                if x == r.left():           # one tag too wide: clip it
                    w = limit - x
                else:
                    break
            box = QRectF(x, y, w, fm.height() + 4)
            p.setPen(Qt.NoPen)
            p.setBrush(tint_color(colour, 46))
            p.drawRoundedRect(box, 7, 7)
            p.setPen(QColor(colour))
            p.drawText(box, Qt.AlignCenter,
                       fm.elidedText(name, Qt.ElideRight, int(w) - 8))
            x += w + 4

    def _badge(self, p, r, accent):
        """The live bars sit in a dark chip on the artwork -- a station logo
        can be any color, so the indicator brings its own background."""
        w, h = 34, 24
        box = QRectF(r.left() + self.ART - w - 8,
                     r.top() + self.ART - h - 8, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(10, 10, 12, 200))
        p.drawRoundedRect(box, 7, 7)

        bar_w, gap = 3, 3
        x = box.center().x() - (bar_w * 3 + gap * 2) / 2.0
        cy = box.center().y()
        p.setBrush(accent)
        waves = ((5.1, 0.0, 5.5, 3.0), (6.7, 1.9, 7.5, 4.5), (5.9, 3.7, 6.0, 3.5))
        for i, (speed, offset, span, base) in enumerate(waves):
            if self.is_playing:
                bh = base + span * (0.5 + 0.5 * math.sin(
                    self.elapsed * speed + offset))
            else:
                bh = 3.0
            p.drawRoundedRect(
                QRectF(x + i * (bar_w + gap), cy - bh / 2.0, bar_w, bh), 1.5, 1.5)


# --------------------------------------------------------------------------
# View
# --------------------------------------------------------------------------

class StationList(QListView):
    """Wires the model, delegate and animation together."""

    activated_station = pyqtSignal(dict)
    clicked_station = pyqtSignal(dict, object)   # station, point in viewport
    context_requested = pyqtSignal(dict, QPoint)
    # Several rows picked with Ctrl/Shift+click, right-clicked (build 105).
    context_many = pyqtSignal(list, QPoint)
    action_clicked = pyqtSignal(dict)
    order_changed = pyqtSignal(list)         # station ids, new order

    def __init__(self, logo_fetcher=None, parent=None, tile=None):
        super().__init__(parent)
        self.logos = LogoCache(40, fetcher=logo_fetcher,
                               on_ready=self._logo_ready)
        self.grid_logos = LogoCache(tile or StationGridDelegate.ART,
                                    fetcher=logo_fetcher,
                                    on_ready=self._logo_ready)
        self.model_ = StationModel()
        self.delegate = StationDelegate(self.logos, self)
        self.grid_delegate = StationGridDelegate(self.grid_logos, self,
                                                 tile=tile)
        self._grid = False

        self.setModel(self.model_)
        self.setItemDelegate(self.delegate)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QListView.NoFrame)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

        # Manual sort survives the redesign: drag a station where you want it.
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(True)
        self.setDragEnabled(False)          # opt in via set_manual_sort()
        self._manual = False
        self._move_from = None              # (row, press point) while armed
        self._move_to = None                # drop row while a move is on

        self.doubleClicked.connect(self._activate)
        self._rows_at = 0.0
        self._row_timer = QTimer(self)
        self._row_timer.setInterval(16)
        self._row_timer.timeout.connect(self._row_tick)
        self.model_.refreshed.connect(self._start_row_animation)
        # Rows that genuinely arrived animate; a wholesale refresh does
        # the whole list as before.
        self.model_.rows_arrived.connect(self._rows_arrived)

        self.model_.order_changed.connect(
            lambda: self.order_changed.emit(self.model_.order()))
        self.customContextMenuRequested.connect(self._context)

        # 30fps. Only the one visible playing row is repainted, and the
        # timer stops the moment playback does.
        self._anim = QTimer(self)
        self._anim.setInterval(33)
        self._anim.timeout.connect(self._tick)
        self._clock = QElapsedTimer()

    # -- data -------------------------------------------------------------

    def set_stations(self, stations):
        self.model_.set_stations(stations)

    def filter(self, text="", tags=(), exclude=()):
        return self.model_.apply_filter(text, tags, exclude)

    def set_columns(self, on):
        """Show or hide the play count, last played and added columns."""
        self.delegate.show_columns = bool(on)
        self.viewport().update()

    def set_view_mode(self, grid: bool):
        """List or artwork grid. Dragging is turned off in grid mode: an
        icon view lets you drop a tile anywhere, which is not what a manual
        sort order means."""
        if grid == self._grid:
            return
        # setMovement(Static) clears dragEnabled as a side effect, so the
        # flag has to be captured before anything touches the view.
        if grid:
            self._manual_before_grid = self._manual
        self._grid = grid
        self.delegate.playing_id = self.grid_delegate.playing_id
        self.grid_delegate.playing_id = self.delegate.playing_id

        if grid:
            self.setItemDelegate(self.grid_delegate)
            self.setViewMode(QListView.IconMode)
            self.setResizeMode(QListView.Adjust)
            self.setMovement(QListView.Static)
            self.setWrapping(True)
            self.setSpacing(14)
            self.setGridSize(QSize(self.grid_delegate.TILE_W + 22,
                                   self.grid_delegate.ART
                                   + self.grid_delegate.TEXT + 16))
            self.setUniformItemSizes(True)
            self.set_manual_sort(False)
        else:
            self.setItemDelegate(self.delegate)
            self.setViewMode(QListView.ListMode)
            self.setMovement(QListView.Static)
            self.setWrapping(False)
            self.setSpacing(0)
            self.setGridSize(QSize())
            self.set_manual_sort(getattr(self, "_manual_before_grid", False))
        self.reset()

    def is_grid(self):
        return self._grid

    def _active_delegate(self):
        return self.grid_delegate if self._grid else self.delegate

    def set_manual_sort(self, on: bool):
        """Only allow dragging when the list is in manual order -- dragging
        a row inside an alphabetical sort just snaps back and looks broken.

        The list moves its rows itself (mouse press, move, release) rather
        than through Qt's drag and drop. Since Ctrl+click multi-select
        (build 105) a drag that Qt's drop didn't take turned into a drag
        selection: the rows under the mouse were picked, nothing moved
        (task 146). Under Wayland Qt's drag can end at once, so it is not
        used at all now."""
        self._manual = bool(on)
        self._move_from = self._move_to = None
        self.setDragEnabled(False)

    def set_density(self, compact: bool):
        self.delegate.row_height = (StationDelegate.COMPACT if compact
                                    else StationDelegate.COMFORTABLE)
        if not self._grid:
            self.reset()

    # -- playback state ---------------------------------------------------

    def set_playing(self, station_id, playing=True):
        old = self.delegate.playing_id
        for d in (self.delegate, self.grid_delegate):
            d.playing_id = station_id
            d.is_playing = playing
        for sid in (old, station_id):
            if sid:
                self.model_.touch(sid)
        if playing and station_id:
            if not self._anim.isActive():
                self._clock.start()
                self._anim.start()
        else:
            self._anim.stop()
            self.viewport().update()

    def _tick(self):
        elapsed = self._clock.elapsed() / 1000.0
        self.delegate.elapsed = elapsed
        self.grid_delegate.elapsed = elapsed
        row = self.model_.row_of(self.delegate.playing_id)
        if row < 0:
            return
        rect = self.visualRect(self.model_.index(row))
        if rect.isValid() and rect.intersects(self.viewport().rect()):
            self.viewport().update(rect)

    # How an arriving row behaves.
    #
    # iOS staggers a list in: each row a moment after the one above,
    # rising slightly and fading. It reads as the list assembling rather
    # than replacing itself, which matters most when a filter changes and
    # the whole thing is different.
    ROW_MS = 260                # how long one row takes
    ROW_STAGGER = 22            # ms between neighbouring rows
    ROW_RISE = 10               # px it travels up into place

    def _rows_arrived(self, start, count):
        """Animate the inserted run only, not the whole list."""
        self._arrived = (start, start + count)
        self._start_row_animation()

    def _start_row_animation(self):
        self._arrived = getattr(self, "_arrived", None)
        self._rows_at = time.monotonic()
        if not self._row_timer.isActive():
            self._row_timer.start()

    def _row_tick(self):
        rows = self.model_.rowCount() if self.model_ else 0
        total = self.ROW_MS + self.ROW_STAGGER * min(rows, 24)
        if (time.monotonic() - self._rows_at) * 1000 > total:
            self._row_timer.stop()
            self._arrived = None
        self.viewport().update()

    def row_progress(self, row):
        """0 to 1 for how far this row is into arriving."""
        if not self._row_timer.isActive():
            return 1.0
        arrived = getattr(self, "_arrived", None)
        if arrived is not None:
            # Only the run that was inserted moves; everything else is
            # already where it was and must not flicker.
            first, past = arrived
            if not (first <= row < past):
                return 1.0
            row -= first
        elapsed = (time.monotonic() - self._rows_at) * 1000
        # Only the first screenful is staggered: a four-hundred station
        # list would otherwise take half a minute to finish arriving.
        started = elapsed - self.ROW_STAGGER * min(row, 24)
        if started <= 0:
            return 0.0
        return min(1.0, started / float(self.ROW_MS))

    def _logo_ready(self, sid):
        self.model_.touch(sid)

    # -- interaction ------------------------------------------------------

    def _row_at(self, index):
        """The model's own row dict for an index — never a copy.

        index.data(STATION_ROLE) goes through a QVariant, and PyQt hands
        back a deep copy of the dict, row["_source"] included. So the
        station a menu edited was a copy: Tags… put the tag on it, Save
        wrote the real (untouched) list, and the tag never showed
        (task 39, build 58). Everything the user acts on comes from here.
        """
        if not index.isValid():
            return None
        return self.model_.station_at(index.row())

    def _activate(self, index):
        st = self._row_at(index)
        if st:
            self.activated_station.emit(st)

    def mousePressEvent(self, e):
        """A single click reports the station and where it was clicked.

        Separate from activation, which is a double-click or Return:
        clicking once should be able to mean "tell me about this" without
        also meaning "play it".
        """
        super().mousePressEvent(e)
        if e.button() != Qt.LeftButton:
            return
        index = self.indexAt(e.pos())
        st = self._row_at(index)
        # Manual sort: a plain press on a row may become a move. Ctrl and
        # Shift clicks stay selection clicks.
        self._move_from = self._move_to = None
        if (st and self._manual and not self._grid
                and not e.modifiers() & (Qt.ControlModifier
                                         | Qt.ShiftModifier)):
            self._move_from = (index.row(), e.pos())
        if st:
            self.clicked_station.emit(st, e.pos())

    def mouseMoveEvent(self, e):
        if self._move_from is None or not e.buttons() & Qt.LeftButton:
            super().mouseMoveEvent(e)
            return
        row, start = self._move_from
        if (self._move_to is None
                and (e.pos() - start).manhattanLength()
                < QApplication.startDragDistance()):
            return                          # not a move yet; no drag-select
        # Near the top or bottom edge the list scrolls along.
        bar = self.verticalScrollBar()
        if e.pos().y() < 24:
            bar.setValue(bar.value() - 12)
        elif e.pos().y() > self.viewport().height() - 24:
            bar.setValue(bar.value() + 12)
        self._move_to = self._drop_row(e.pos())
        self.viewport().update()

    def _drop_row(self, pos):
        """The row a move would land before (rowCount = the end)."""
        index = self.indexAt(pos)
        if not index.isValid():
            count = self.model_.rowCount()
            if count and pos.y() < self.visualRect(
                    self.model_.index(0, 0)).top():
                return 0
            return count
        rect = self.visualRect(index)
        return index.row() + (1 if pos.y() > rect.center().y() else 0)

    def _finish_move(self):
        """Move the pressed row there, or every picked row when the press
        was on one of several picked with Ctrl (same steps as Qt's own
        list drop, so non-adjacent picks land together, in order)."""
        row = self._move_from[0]
        target = self._move_to
        self._move_from = self._move_to = None
        self.viewport().update()
        picked = sorted(i.row() for i in self.selectionModel().selectedIndexes())
        rows = picked if row in picked and len(picked) > 1 else [row]
        ids = [self.model_.station_at(r).get("id") for r in rows]
        keep = [QPersistentModelIndex(self.model_.index(r, 0)) for r in rows]
        for kept in keep:
            if target != kept.row() and target != kept.row() + 1:
                self.model_.moveRows(QModelIndex(), kept.row(), 1,
                                     QModelIndex(), target)
            target = kept.row() + 1
        selection = self.selectionModel()
        selection.clearSelection()
        for kept in keep:
            selection.select(self.model_.index(kept.row(), 0),
                             selection.Select)
        try:
            import crashlog
            crashlog.write(f"[sort] moved {len(ids)} by hand to row "
                           f"{keep[0].row() + 1}")
        except Exception:                       # noqa: BLE001
            pass

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._move_to is None:
            return
        count = self.model_.rowCount()
        if not count:
            return
        if self._move_to < count:
            y = self.visualRect(self.model_.index(self._move_to, 0)).top()
        else:
            y = self.visualRect(self.model_.index(count - 1, 0)).bottom()
        p = QPainter(self.viewport())
        p.setPen(QPen(QColor(T["accent"]), 2))
        p.drawLine(4, y, self.viewport().width() - 4, y)
        p.end()

    def set_multi_select(self, on):
        """Ctrl+click and Shift+click pick several rows (Saved
        Frequencies only, build 105, task 130a)."""
        self.setSelectionMode(QAbstractItemView.ExtendedSelection if on
                              else QAbstractItemView.SingleSelection)

    def selected_stations(self):
        rows = sorted(i.row() for i in self.selectionModel().selectedIndexes())
        return [st for st in (self.model_.station_at(r) for r in rows) if st]

    def _context(self, pos):
        index = self.indexAt(pos)
        st = self._row_at(index)
        if not st:
            return
        point = self.viewport().mapToGlobal(pos)
        if (self.selectionMode() == QAbstractItemView.ExtendedSelection
                and self.selectionModel().isSelected(index)):
            picked = self.selected_stations()
            if len(picked) > 1:
                self.context_many.emit(picked, point)
                return
        self.context_requested.emit(st, point)

    def mouseReleaseEvent(self, e):
        """A click on the +/− is the button, not the row.

        Handled here rather than with a real QPushButton per row: the list
        paints itself, and 300 live widgets to show 300 small squares is
        how a list stops scrolling smoothly.
        """
        if e.button() == Qt.LeftButton and self._move_to is not None:
            self._finish_move()
            self.setState(QAbstractItemView.NoState)
            return
        self._move_from = None
        if (e.button() == Qt.LeftButton and not self._grid
                and self.delegate.show_actions):
            index = self.indexAt(e.pos())
            st = self._row_at(index)
            if st is not None:
                rect = self.delegate.action_rect(self.visualRect(index))
                if rect.contains(e.pos()):
                    self.action_clicked.emit(st)
                    return
        super().mouseReleaseEvent(e)
