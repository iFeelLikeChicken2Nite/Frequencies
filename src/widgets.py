"""
widgets.py - Custom widgets.

  ElidedLabel      a label that never widens its window, however long
                   the song title is
  LiveLyricsView   synced lyrics with the current line highlighted and
                   scrolled to the middle
  CollapsibleBox   a section that folds away, so all the controls fit
                   without a wall of widgets
  StationItemDelegate / TagChip / draw_tag_pill
                   tags drawn as rounded pills, identically everywhere
  FlowLayout       wraps buttons onto another row rather than shrinking
                   them until their labels are unreadable
"""
from PyQt5.QtCore import (Qt, QSize, QRect, QRectF, QPoint, QTimer,
                          QPropertyAnimation, QEasingCurve, pyqtSignal,
                          QVariant, QUrl)
from PyQt5.QtGui import (QFontMetrics, QColor, QFont, QPainter, QPen,
                         QBrush, QTextDocument, QImage)
from PyQt5.QtWidgets import (
    QTextBrowser,
    QLabel,
    QSizePolicy,
    QListWidget,
    QListWidgetItem,
    QWidget,
    QVBoxLayout,
    QToolButton,
    QFrame,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QStyle,
    QLayout,
)

from jobs import Job


class ElidedLabel(QLabel):
    """A label that shows "A Really Long Song Ti..." instead of forcing
    the window wider.

    Two things are needed for that, and both matter:
      - a horizontal size policy of Ignored, so the label's preferred
        width never feeds back into the layout's minimum, and
      - eliding the text ourselves on every resize, because QLabel only
        elides automatically in a few narrow cases.

    Without this, a station that puts a 200-character "now playing"
    string in its metadata visibly stretches the window.
    """

    def __init__(self, text="", parent=None, lines=2):
        super().__init__(parent)
        self._full_text = text
        self._lines = max(1, lines)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._apply()

    def setText(self, text):
        self._full_text = text or ""
        self.setToolTip(self._full_text if len(self._full_text) > 40 else "")
        self._apply()

    def fullText(self):
        return self._full_text

    def sizeHint(self):
        metrics = QFontMetrics(self.font())
        return QSize(80, metrics.lineSpacing() * self._lines + 4)

    def minimumSizeHint(self):
        return self.sizeHint()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def _apply(self):
        metrics = QFontMetrics(self.font())
        width = max(40, self.width() - 4)
        lines = self._full_text.split("\n")[: self._lines]
        elided = [metrics.elidedText(line, Qt.ElideRight, width) for line in lines]
        super().setText("\n".join(elided))


class LiveLyricsView(QListWidget):
    """Synced lyrics that follow the song, styled after Apple Music.

    What that means in practice, and why each part is here:

      - the active line is bright, bold and slightly larger; lines
        around it are dimmer, and dim further the more distant they are,
        which gives the "focus" falloff rather than a hard on/off
      - the scroll to the next line is *animated* rather than an
        instant jump, so the sheet glides
      - within the active line, words light up progressively as they're
        sung, interpolated across the gap to the next line's timestamp

    Falls back to plain, unsynced lyrics as ordinary rows.
    """

    ACTIVE = QColor("#ffffff")
    NEAR = QColor("#b4b4b4")
    FAR = QColor("#6e6e6e")
    DIM = QColor("#8a8a8a")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timed = []
        self._current = -1
        self._synced = False
        self.setSelectionMode(QListWidget.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWordWrap(True)
        self.setStyleSheet("QListWidget { border: none; }")

        # Smooth scrolling: animate the scrollbar instead of jumping.
        self._pad_rows = 0

        # Scroll by pixel, not by item.
        #
        # QListWidget defaults to per-item scrolling, where the
        # scrollbar's value counts rows rather than pixels. The
        # centring arithmetic here is in pixels, so against a per-item
        # bar it was computing targets in the wrong unit entirely - and
        # a smooth animation is impossible when the smallest step is a
        # whole row.
        self.setVerticalScrollMode(self.ScrollPerPixel)

        self._scroll_anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._scroll_anim.setDuration(420)
        self._scroll_anim.setEasingCurve(QEasingCurve.OutCubic)

    @property
    def is_synced(self):
        return self._synced

    def clear_lyrics(self, message=""):
        self.clear()
        self._timed = []
        self._current = -1
        self._synced = False
        self._pad_rows = 0
        if message:
            item = QListWidgetItem(message)
            item.setForeground(self.DIM)
            item.setTextAlignment(Qt.AlignCenter)
            self.addItem(item)

    def set_plain(self, text):
        """Unsynced lyrics - readable, but nothing to animate against."""
        self.clear()
        self._pad_rows = 0
        self._timed = []
        self._current = -1
        self._synced = False
        for line in (text or "").splitlines():
            item = QListWidgetItem(line)
            item.setForeground(self.DIM)
            self.addItem(item)

    def set_synced(self, timed_lines):
        """timed_lines: [(seconds, text), ...] sorted by time."""
        self.clear()
        self._timed = list(timed_lines)
        self._current = -1
        self._synced = bool(self._timed)
        row_height = self._row_height()
        width = max(1, self.viewport().width())
        for _when, line in self._timed:
            item = QListWidgetItem(line or "\u266a")
            item.setForeground(self.FAR)
            item.setTextAlignment(Qt.AlignCenter)
            item.setSizeHint(QSize(width, row_height))
            self.addItem(item)
        self._pad_ends()

    def _pad_ends(self):
        """Blank rows above and below, so any line can sit centered.

        Without them the scroll simply runs out: the first lines of a
        song stayed pinned to the top and the last to the bottom, since
        there was nothing to scroll past. The padding is half a viewport
        at each end, which is exactly the room needed to bring the first
        and last lines to the middle.
        """
        if not self._timed:
            return
        row_height = max(1, self._row_height())
        rows = max(1, (self.viewport().height() // 2) // row_height)
        self._pad_rows = rows

        width = max(1, self.viewport().width())
        for _ in range(rows):
            top = QListWidgetItem("")
            top.setFlags(Qt.NoItemFlags)
            top.setSizeHint(QSize(width, row_height))
            self.insertItem(0, top)
            bottom = QListWidgetItem("")
            bottom.setFlags(Qt.NoItemFlags)
            bottom.setSizeHint(QSize(width, row_height))
            self.addItem(bottom)

    def _row_height(self):
        base = self.font()
        tall = QFont(base)
        tall.setBold(True)
        tall.setPointSizeF(base.pointSizeF() + 1.0)
        return QFontMetrics(tall).height() + 6

    def update_position(self, seconds):
        """Highlight whichever line is being sung at `seconds`, and
        shade its neighbours by distance."""
        if not self._synced or seconds is None:
            return
        index = self._index_for(seconds)
        if index == self._current:
            return
        self._current = index
        self._restyle()

        if 0 <= index < len(self._timed):
            self._scroll_to(index + self._pad_rows)

    def _restyle(self):
        base = self.font()
        # Every row is sized for the *active* font, whether it is active
        # or not.
        #
        # The active line is bold and a point larger, which changed its
        # height - and so pushed every row below it down and back up
        # again as the highlight moved. The scroll target therefore
        # moved while we were animating towards it, which is what made
        # the sheet jump back and forth on every line change.
        #
        # Reserving the tall height everywhere costs a little vertical
        # space and makes the layout completely static.
        tall = QFont(base)
        tall.setBold(True)
        tall.setPointSizeF(base.pointSizeF() + 1.0)
        row_height = QFontMetrics(tall).height() + 6

        # A size hint with a negative width is invalid, and Qt discards
        # the whole hint rather than just the width - so the height was
        # being ignored and rows kept resizing themselves.
        width = max(1, self.viewport().width())
        for row in range(self.count()):
            item = self.item(row)
            item.setSizeHint(QSize(width, row_height))
            lyric_row = row - self._pad_rows
            distance = (abs(lyric_row - self._current)
                        if self._current >= 0 else 99)
            font = QFont(base)
            if distance == 0:
                item.setForeground(self.ACTIVE)
                font.setBold(True)
                font.setPointSizeF(base.pointSizeF() + 1.0)
            elif distance == 1:
                item.setForeground(self.NEAR)
                font.setBold(False)
            else:
                item.setForeground(self.FAR)
                font.setBold(False)
            item.setFont(font)

    def _scroll_to(self, index):
        """Animate so the active line settles in the middle.

        The target is computed from the row's absolute position - row
        index times row height - rather than from visualItemRect, which
        is measured against the current scroll offset. During an
        animation that offset is mid-flight, so reading it produced a
        target relative to wherever the scroll happened to be at that
        instant, and each new line compounded the error.
        """
        item = self.item(index)
        if item is None:
            return
        bar = self.verticalScrollBar()
        row_height = item.sizeHint().height() or 1
        center = index * row_height + row_height // 2
        target = center - self.viewport().height() // 2
        target = max(bar.minimum(), min(bar.maximum(), target))
        if abs(target - bar.value()) < 2:
            return
        self._scroll_anim.stop()
        self._scroll_anim.setStartValue(bar.value())
        self._scroll_anim.setEndValue(target)
        self._scroll_anim.start()

    def line_progress(self, seconds):
        """How far through the active line we are, 0..1.

        Used for the word-by-word fill; exposed separately so it can be
        tested without a running clock.
        """
        if not self._synced or seconds is None or self._current < 0:
            return 0.0
        start = self._timed[self._current][0]
        if self._current + 1 < len(self._timed):
            end = self._timed[self._current + 1][0]
        else:
            end = start + 4.0
        if end <= start:
            return 1.0
        return max(0.0, min(1.0, (seconds - start) / (end - start)))

    def _index_for(self, seconds):
        """Last line whose timestamp has passed. Linear is fine - even a
        long song is only a few hundred lines."""
        found = -1
        for i, (when, _line) in enumerate(self._timed):
            if when <= seconds:
                found = i
            else:
                break
        return found


class CollapsibleBox(QWidget):
    """A titled section that folds away.

    Used to keep every control available while the window stays compact:
    the sound settings are all still there, just folded by default.
    """

    toggled = pyqtSignal(bool)

    def __init__(self, title="", parent=None, expanded=False):
        super().__init__(parent)

        self.toggle_button = QToolButton()
        self.toggle_button.setStyleSheet("QToolButton { border: none; }")
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.RightArrow)
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.clicked.connect(self._on_toggle)

        self.content = QFrame()
        self.content.setFrameShape(QFrame.NoFrame)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(12, 2, 2, 2)
        self.content.setVisible(expanded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content)

        self._sync_arrow()

    def _on_toggle(self, checked):
        self.content.setVisible(checked)
        self._sync_arrow()
        self.toggled.emit(checked)

    def _sync_arrow(self):
        self.toggle_button.setArrowType(
            Qt.DownArrow if self.toggle_button.isChecked() else Qt.RightArrow
        )

    def addWidget(self, widget):
        self.content_layout.addWidget(widget)

    def addLayout(self, layout):
        self.content_layout.addLayout(layout)

    def setExpanded(self, expanded):
        self.toggle_button.setChecked(bool(expanded))
        self._on_toggle(bool(expanded))

    def isExpanded(self):
        return self.toggle_button.isChecked()


def draw_tag_pill(painter, rect, text, color, font=None, selected=False):
    """Paint one tag as a rounded pill.

    Kept as a free function so the list delegate, the filter chips and
    the dialogs all draw tags identically - a pill should look the same
    everywhere it appears, which was the point of the exercise.
    """
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)

    base = QColor(color)
    fill = QColor(base)
    fill.setAlpha(230 if selected else 52)
    border = QColor(base)
    border.setAlpha(255 if selected else 150)
    text_color = QColor("#101010") if selected else base

    if font is not None:
        painter.setFont(font)

    radius = rect.height() / 2.0
    painter.setPen(QPen(border, 1))
    painter.setBrush(QBrush(fill))
    painter.drawRoundedRect(QRectF(rect), radius, radius)

    painter.setPen(text_color)
    painter.drawText(rect, Qt.AlignCenter, text)
    painter.restore()


def tag_pill_size(text, font, padding=9, height_pad=5):
    metrics = QFontMetrics(font)
    return QSize(metrics.horizontalAdvance(text) + padding * 2,
                 metrics.height() + height_pad)


def pill_stylesheet(color, checked=False, excluded=False):
    """The same pill shape, for widgets that are styled rather than
    painted (the filter chips, buttons in dialogs).

    Excluded reads as struck through and drained of color: an excluded
    tag is not a quieter version of an included one, it's the opposite,
    and it needs to look like it at a glance.
    """
    if excluded:
        return (
            f"border: 1px dashed {color}; border-radius: 9px;"
            f" padding: 2px 9px; background: transparent; color: #8a8a8a;"
            f" text-decoration: line-through;"
        )
    if checked:
        return (
            f"border: 1px solid {color}; border-radius: 9px;"
            f" padding: 2px 9px; background: {color}; color: #101010;"
            f" font-weight: bold;"
        )
    return (
        f"border: 1px solid {color}; border-radius: 9px;"
        f" padding: 2px 9px; background: transparent; color: {color};"
    )


class StationItemDelegate(QStyledItemDelegate):
    """Draws a station row: name, status, and its tags as pills.

    Qt list items can only hold one string, so tags used to be crammed
    into the text as "[jazz, live]". Painting them ourselves means they
    read as pills here exactly like they do in the filter bar.
    """

    TAG_ROLE = Qt.UserRole + 10
    COLOR_ROLE = Qt.UserRole + 11
    DIM_ROLE = Qt.UserRole + 12
    PLAYING_ROLE = Qt.UserRole + 13

    PAD = 6
    GAP = 4

    def _tag_font(self, base):
        font = QFont(base)
        font.setPointSizeF(max(6.5, base.pointSizeF() - 1.5))
        return font

    def paint(self, painter, option, index):
        tags = index.data(self.TAG_ROLE) or []
        colors = index.data(self.COLOR_ROLE) or {}
        dimmed = bool(index.data(self.DIM_ROLE))
        playing = bool(index.data(self.PLAYING_ROLE))

        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        text = style_option.text
        style_option.text = ""
        style_option.widget.style().drawControl(
            QStyle.CE_ItemViewItem, style_option, painter, style_option.widget
        )

        painter.save()
        if dimmed:
            painter.setOpacity(0.4)

        # Mark the station that is actually playing.
        #
        # Selection isn't enough: you can select one row while a
        # different one plays, and in a list of 276 stations there was
        # nothing to say which. A tinted band, an accent bar down the
        # left edge and a bold name make it findable at a glance
        # without shouting.
        row_font = QFont(option.font)
        if playing:
            accent = option.palette.highlight().color()
            band = QColor(accent)
            band.setAlpha(38)
            painter.fillRect(option.rect, band)

            bar = QRect(option.rect.left(), option.rect.top(),
                        3, option.rect.height())
            painter.fillRect(bar, accent)
            row_font.setBold(True)

        metrics = QFontMetrics(row_font)
        tag_font = self._tag_font(row_font)

        # Reserve room on the right for the pills, then elide the name
        # into whatever is left.
        pill_sizes = [tag_pill_size(t, tag_font) for t in tags]
        pills_width = sum(s.width() + self.GAP for s in pill_sizes)

        text_rect = QRect(option.rect)
        text_rect.adjust(self.PAD, 0, -(pills_width + self.PAD), 0)

        color = index.data(Qt.ForegroundRole)
        if isinstance(color, QBrush):
            painter.setPen(color.color())
        elif isinstance(color, QColor):
            painter.setPen(color)
        else:
            painter.setPen(option.palette.text().color())
        painter.drawText(
            text_rect, Qt.AlignVCenter | Qt.AlignLeft,
            metrics.elidedText(text, Qt.ElideRight, max(20, text_rect.width()))
        )

        x = option.rect.right() - self.PAD
        for tag, size in zip(reversed(tags), reversed(pill_sizes)):
            x -= size.width()
            pill_rect = QRect(
                x, option.rect.center().y() - size.height() // 2,
                size.width(), size.height()
            )
            draw_tag_pill(painter, pill_rect, tag,
                          colors.get(tag.lower(), "#8a8a8a"), tag_font)
            x -= self.GAP

        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        tags = index.data(self.TAG_ROLE) or []
        if tags:
            tag_font = self._tag_font(option.font)
            needed = tag_pill_size(tags[0], tag_font).height() + 6
            size.setHeight(max(size.height(), needed))
        return size


class TagChip(QToolButton):
    """A tri-state pill for filtering by tag.

    Left-click includes the tag, and clicking an included tag clears it.
    Right-click excludes it, and right-clicking an excluded tag clears
    it. The prefix says which state you're in without needing color:
    "+" included, "-" excluded, nothing at all for neutral.

    The chip holds no state of its own - it emits what was asked for and
    is told what to display. The search box is the single source of
    truth for the filter, so a chip and a typed query can never drift
    apart.
    """

    NEUTRAL = 0
    INCLUDED = 1
    EXCLUDED = -1

    #: (tag, exclude_requested)
    clicked_with_button = pyqtSignal(str, bool)

    def __init__(self, tag, color, state=0, parent=None, max_chars=None):
        super().__init__(parent)
        self.tag = tag
        self.color = color
        # The rail is a narrow column, so a long tag is shown shortened
        # there. self.tag keeps the real name - that's what gets filtered
        # on - so eliding only ever affects what's drawn.
        self._max_chars = max_chars
        self._state = state
        self.setCheckable(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.set_state(state)

    def state(self):
        return self._state

    def set_state(self, state):
        self._state = state
        prefix = {self.INCLUDED: "+ ", self.EXCLUDED: "- "}.get(state, "")
        shown = self.tag
        if self._max_chars and len(shown) > self._max_chars:
            shown = shown[:self._max_chars - 1].rstrip() + "\u2026"
        self.setText(f"{prefix}{shown}")
        self.setToolTip({
            self.INCLUDED: f'Only showing "{self.tag}". Click to clear, '
                           f"right-click to exclude instead.",
            self.EXCLUDED: f'Hiding "{self.tag}". Right-click to clear, '
                           f"click to show only these instead.",
        }.get(state, f'Click to show only "{self.tag}", '
                     f"right-click to hide it."))
        self.setStyleSheet(
            "QToolButton { %s }" % pill_stylesheet(
                self.color, state == self.INCLUDED, state == self.EXCLUDED
            )
        )

    def mouseReleaseEvent(self, event):
        # Both buttons are handled here rather than through clicked(),
        # because QToolButton only reports left clicks that way and the
        # right-click has to mean something different.
        if event.button() in (Qt.LeftButton, Qt.RightButton) and \
                self.rect().contains(event.pos()):
            self.clicked_with_button.emit(
                self.tag, event.button() == Qt.RightButton
            )
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        # Swallow it: right-click is the exclude gesture here, not a menu.
        event.accept()


class FlowLayout(QLayout):
    """A layout that wraps its widgets onto a new row when they don't
    fit, instead of squeezing them.

    The button rows used to have an Ignored horizontal size policy, so
    narrowing the window shrank the buttons and Qt elided their labels
    to "Rem..." or just "...". Squeezing a button until you can't read
    it is worse than moving it to the next line, so this gives every
    widget its natural width and wraps instead.
    """

    def __init__(self, parent=None, margin=0, spacing=4):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def __del__(self):
        while self.count():
            self.takeAt(0)

    # -- QLayout plumbing ---------------------------------------------
    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(),
                      margins.top() + margins.bottom())
        return size

    # -- the actual wrapping ------------------------------------------
    def _layout(self, rect, test_only):
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(),
                                  -margins.right(), -margins.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + margins.bottom()


class RemoteImageBrowser(QTextBrowser):
    """A QTextBrowser that can actually show <img src="http://...">.

    QTextBrowser resolves image resources itself and has no network
    stack, so a remote src renders as the broken-image glyph - which is
    the little grey page that appeared where the artist photo should
    have been.

    Fetching is done once per URL on a worker thread and cached, so a
    panel that re-renders every few seconds (trivia rotating, say)
    doesn't re-download the same photo each time.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._images = {}
        self._pending = set()
        self._workers = []

    def loadResource(self, resource_type, url):
        if resource_type != QTextDocument.ImageResource:
            return super().loadResource(resource_type, url)

        key = url.toString()
        if not key.startswith(("http://", "https://")):
            return super().loadResource(resource_type, url)

        cached = self._images.get(key)
        if cached is not None:
            return cached

        # Not here yet: start a fetch and draw nothing for now. The
        # re-render when it arrives is what puts the picture in.
        if key not in self._pending:
            self._pending.add(key)
            worker = _ImageFetcher(key, self)
            worker.fetched.connect(self._on_image)
            worker.finished.connect(lambda w=worker: self._workers.remove(w)
                                    if w in self._workers else None)
            self._workers.append(worker)
            worker.start()
        return QVariant()

    def _on_image(self, key, data):
        self._pending.discard(key)
        if not data:
            return
        image = QImage()
        if not image.loadFromData(data):
            return
        self._images[key] = image
        # Re-lay the document out now the resource exists.
        self.document().addResource(
            QTextDocument.ImageResource, QUrl(key), image
        )
        self.setHtml(self.document().toHtml())

    def clear_image_cache(self):
        self._images.clear()


class _ImageFetcher(Job):
    """One image, off the GUI thread.

    A jobs.Job (daemon thread), not a QThread, so closing the window
    mid-download can't crash the app (build 54, task 14)."""

    fetched = pyqtSignal(str, bytes)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self._url = url

    def work(self):
        try:
            import requests
            response = requests.get(
                self._url,
                timeout=10,
                headers={"User-Agent": "Frequencies/1.0"},
            )
            response.raise_for_status()
            data = response.content
        except Exception:  # noqa: BLE001
            data = b""
        self.send(self.fetched, self._url, data)
