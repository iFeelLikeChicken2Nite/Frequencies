"""Frequencies — tags.

The old dialog did two jobs in one plain list: tick which tags apply to this
station, and set the color of whichever tag was ticked. That coupling meant
you could only recolor a tag you were also assigning.

Here they are separate. A click on the row (or its name) assigns. The
swatch recolors, always. A double-click on the name renames it in place, so
renaming a tag no longer needs its own dialog, and each row carries the
count of stations it would affect.
"""

from PyQt5.QtCore import QElapsedTimer, QEvent, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (QAction, QApplication, QCheckBox, QDialog,
                             QDialogButtonBox,
                             QHBoxLayout, QLabel, QLineEdit, QMenu,
                             QMessageBox, QPushButton, QScrollArea,
                             QToolButton, QVBoxLayout, QWidget)

import storage
import theme
from shell import icon, search_field
from theme import T

# Named, not hex. "Amber" is something you can ask for; #d9b54e is not.
#
# One list for the whole app (task 34, build 58). The dialog used to have
# its own 16 shades, slightly off from the ones the rest of the app gives
# tags, so every existing tag's button said "Custom" and picking a colour
# here switched it to a shade nothing else used.
PALETTE = list(storage.TAG_COLOR_CHOICES)


def swatch(colour, size=14, ring=None):
    pm = QPixmap(size + 4, size + 4)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(colour))
    p.drawEllipse(2, 2, size, size)
    if ring:
        p.setBrush(Qt.NoBrush)
        p.setPen(QColor(ring))
        p.drawEllipse(0, 0, size + 3, size + 3)
    p.end()
    return pm


def colour_name(value):
    for name, hexval in PALETTE:
        if hexval.lower() == str(value).lower():
            return name
    return "Custom"


class TagRow(QWidget):
    """One tag, with the whole row as its hit area.

    The checkbox is a 16px indicator at the left of a row that is two
    hundred wide and looks clickable all the way across. Aiming at the
    indicator is the only thing that worked, which reads as "applying
    tags doesn't work" rather than as "you missed".
    """


    changed = pyqtSignal()
    deleted = pyqtSignal(object)

    def __init__(self, tag, parent=None, ticks=True):
        super().__init__(parent)
        self.tag = dict(tag)          # {name, colour, count, assigned}
        self.original = dict(tag)
        self._hover = False
        # ticks=False is the tag manager's row (task 87): no box to tick,
        # since no station is being tagged. A click on the name renames
        # it, and the rename and bin buttons are always showing.
        self.ticks = ticks
        # The tag manager checks a new name before it is taken (a name
        # another tag already has is a merge, and asks first):
        # rename_check(row, text) -> True to take it.
        self.rename_check = None
        self._edit_clock = QElapsedTimer()
        self.setFixedHeight(40)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(10)

        self.check = QCheckBox()
        self.check.setChecked(bool(tag.get("assigned")))
        self.check.setToolTip("Apply this tag to the station")
        self.check.toggled.connect(self._on_check)
        lay.addWidget(self.check)
        self.check.setVisible(ticks)

        # The name field is the pill: no separate preview to fall out of
        # sync with what you typed.
        # A pill should hug its text, so the field is sized to the name
        # rather than padded out to a uniform box.
        #
        # The pill is the biggest, most obvious thing on the row, so a
        # click on it has to apply the tag. As a plain line edit it ate
        # the click (it just took focus for typing), which made Save look
        # like it did nothing (task 17). So it is read-only until you
        # double-click it, and only then becomes a rename field.
        self.name = QLineEdit(tag["name"])
        self.name.editingFinished.connect(self._on_rename)
        self.name.textChanged.connect(lambda _t: self._fit_name())
        self.name.installEventFilter(self)
        self._editing = True
        self._stop_editing()
        lay.addWidget(self.name)
        self._fit_name()

        self.count = QLabel(self._count_text())
        self.count.setStyleSheet(f"color: {T['text3']}; font-size: 11px;")
        lay.addWidget(self.count)

        lay.addStretch(1)

        self.colour_btn = QToolButton()
        self.colour_btn.setPopupMode(QToolButton.InstantPopup)
        self.colour_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.colour_btn.setMenu(self._colour_menu())
        self.colour_btn.setStyleSheet(
            f"QToolButton {{ padding: 4px 8px; color: {T['text2']};"
            f" border-radius: {T['radius']}; }}"
            f"QToolButton::menu-indicator {{ image: none; }}")
        lay.addWidget(self.colour_btn)

        self.rename_btn = None
        if not ticks:
            self.rename_btn = QToolButton()
            self.rename_btn.setIcon(icon("edit", 16, T["text3"]))
            self.rename_btn.setIconSize(QSize(16, 16))
            self.rename_btn.setAutoRaise(True)
            self.rename_btn.setCursor(Qt.PointingHandCursor)
            self.rename_btn.setToolTip("Rename this tag")
            self.rename_btn.clicked.connect(self._start_editing)
            lay.addWidget(self.rename_btn)

        self.delete_btn = QToolButton()
        self.delete_btn.setIcon(icon("trash", 16, T["text3"]))
        self.delete_btn.setIconSize(QSize(16, 16))
        self.delete_btn.setAutoRaise(True)
        self.delete_btn.setToolTip("Delete this tag everywhere")
        self.delete_btn.clicked.connect(lambda: self.deleted.emit(self))
        lay.addWidget(self.delete_btn)
        # After addWidget, never before (build 92, task 101): until the
        # layout gives it a parent the button has none, and setVisible(True)
        # on a parentless widget shows it as a window of its own. The tag
        # manager did that once per tag, so a stack of little windows
        # flashed up in the corner and vanished.
        self.delete_btn.setVisible(not ticks)

        self._restyle()

    def _fit_name(self):
        w = QFontMetrics(self.name.font()).width(self.name.text()) + 34
        self.name.setFixedWidth(max(64, min(w, 260)))

    # -- appearance --------------------------------------------------------

    def _count_text(self):
        n = self.tag.get("count", 0)
        return f"{n} station{'' if n == 1 else 's'}"

    def _restyle(self):
        c = self.tag["colour"]
        self.name.setStyleSheet(
            f"QLineEdit {{ background: {theme.tint(c, 34)}; color: {c};"
            f" border: 1px solid transparent; border-radius: 10px;"
            f" padding: 3px 12px; font-size: 12px; }}"
            f"QLineEdit:focus {{ border-color: {c}; background: {T['bg2']};"
            f" color: {T['text']}; }}")
        self.colour_btn.setIcon(QIcon(swatch(c)))
        self.colour_btn.setText(colour_name(c))

    def _colour_menu(self):
        m = QMenu(self)
        for name, hexval in PALETTE:
            act = QAction(QIcon(swatch(hexval)), name, m)
            act.triggered.connect(lambda _c, v=hexval: self._set_colour(v))
            m.addAction(act)
        return m

    # -- edits -------------------------------------------------------------

    def _set_colour(self, value):
        self.tag["colour"] = value
        self._restyle()
        self.changed.emit()

    def _on_rename(self):
        # Once per rename. The field can say "finished" twice (Return,
        # then losing focus when the rename ends or a question box opens);
        # the second one finds the rename already over.
        if not self._editing:
            return
        text = self.name.text().strip()
        self._stop_editing()
        if not text:
            self.name.setText(self.tag["name"])
        elif text != self.tag["name"]:
            if self.rename_check is not None and not self.rename_check(
                    self, text):
                return
            self.tag["name"] = text
            self.name.setText(text)
            self.changed.emit()

    # -- the name pill: click applies, double-click renames ----------------

    def _start_editing(self):
        if self._editing:
            return
        self._editing = True
        self.name.setReadOnly(False)
        self.name.setFocusPolicy(Qt.StrongFocus)
        self.name.setCursor(Qt.IBeamCursor)
        self.name.setFocus(Qt.MouseFocusReason)
        self.name.selectAll()
        self._edit_clock.start()

    def _stop_editing(self):
        if not self._editing:
            return
        self._editing = False
        self.name.deselect()
        self.name.setReadOnly(True)
        self.name.setFocusPolicy(Qt.NoFocus)
        self.name.setCursor(Qt.PointingHandCursor)
        self.name.setToolTip("Click to apply this tag · double-click to "
                             "rename it" if self.ticks else
                             "Click to rename this tag")
        if self.name.hasFocus():
            self.name.clearFocus()

    def eventFilter(self, obj, e):
        if (obj is self.name and self._editing
                and e.type() == QEvent.KeyPress
                and e.key() == Qt.Key_Escape):
            # Escape while renaming puts the old name back and ends the
            # rename; the Tags… window stays open (task 64). Return ends
            # the rename through editingFinished, and the dialog swallows
            # the key after that (TagsDialog.keyPressEvent).
            self.name.setText(self.tag["name"])
            self._stop_editing()
            return True
        if (obj is self.name and self._editing and not self.ticks
                and e.type() == QEvent.MouseButtonDblClick
                and self._edit_clock.isValid()
                and self._edit_clock.elapsed()
                < QApplication.doubleClickInterval()):
            # The tag manager starts a rename on the first click, so the
            # second click of a double-click lands in the open field.
            # Keep the whole name selected rather than picking one word.
            self.name.selectAll()
            return True
        if obj is self.name and not self._editing and not self.ticks:
            # The tag manager: nothing to tick, so a click on the name
            # renames it.
            t = e.type()
            if t in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                if e.button() == Qt.LeftButton:
                    self._start_editing()
                return True
            if t in (QEvent.MouseButtonRelease, QEvent.MouseMove):
                return True
        if obj is self.name and not self._editing:
            t = e.type()
            if t == QEvent.MouseButtonDblClick:
                if e.button() == Qt.LeftButton:
                    # The first click of the pair already flipped the
                    # tick. A double-click means "rename", not "apply",
                    # so put the tick back the way it was.
                    self.check.setChecked(not self.check.isChecked())
                    self._start_editing()
                return True
            if t == QEvent.MouseButtonPress:
                if e.button() == Qt.LeftButton:
                    self.check.setChecked(not self.check.isChecked())
                return True
            if t in (QEvent.MouseButtonRelease, QEvent.MouseMove):
                # No text selection while it is a button.
                return True
        return super().eventFilter(obj, e)

    def mousePressEvent(self, e):
        """Anywhere on the row toggles it."""
        if e.button() == Qt.LeftButton:
            # Not on the buttons at the right — those have their own jobs.
            # The count label passes its clicks up to here; it is part of
            # the row, not a dead spot in the middle of it.
            child = self.childAt(e.pos())
            if child is None or child in (self.name, self.count,
                                          self.check, self):
                if self.ticks:
                    self.check.setChecked(not self.check.isChecked())
                elif not self._editing:
                    # The tag manager: the row is the tag, and a click
                    # on it renames it, like a click on its name.
                    self._start_editing()
                e.accept()
                return
        super().mousePressEvent(e)

    def _on_check(self, on):
        self.tag["assigned"] = on
        self.changed.emit()

    def matches(self, text):
        return not text or text in self.tag["name"].lower()

    def enterEvent(self, e):
        self._hover = True
        self.delete_btn.setVisible(True)
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        # The tag manager keeps its bin showing: removing is one of its
        # three jobs, not something to find by hovering.
        self.delete_btn.setVisible(not self.ticks)
        self.update()

    def paintEvent(self, e):
        if not self._hover:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T["bg1"]))
        p.drawRoundedRect(self.rect().adjusted(0, 2, 0, -2), 6, 6)


class TagsDialog(QDialog):
    """tags: [{name, color, count, assigned}] for the station being edited."""

    # TagManagerDialog (task 87) is this window without the station.
    TICKS = True
    NOTE = ("Click a tag to put it on this station; click again "
            "to take it off. Typing a tag's name ticks it too. "
            "Double-click a tag to rename it. "
            "Tags are shared across every station, so renaming or "
            "recoloring one here changes it everywhere.")

    def _title(self, station_name):
        return f"Tags — {station_name}"

    def __init__(self, station_name, tags, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self._title(station_name))
        self.resize(560, 520)
        self._rows = []
        self._deleted = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(18, 16, 18, 8)
        hl.setSpacing(10)

        note = QLabel(self.NOTE)
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {T['text2']}; font-size: 11px;")
        hl.addWidget(note)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.new_tag = search_field("Find a tag, or type a new one")
        self.new_tag.textChanged.connect(self._filter)
        self.new_tag.returnPressed.connect(self._add)
        self.new_tag.setFocus()
        add = QPushButton("Add tag")
        add.setAutoDefault(False)
        add.setDefault(False)
        add.clicked.connect(self._add)
        add_row.addWidget(self.new_tag, 1)
        add_row.addWidget(add)
        hl.addLayout(add_row)

        # What Save will do with the name in the field (task 63). Typing a
        # tag's exact name ticks its box, so the list shows what Save is
        # about to apply; this line says it in words.
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {T['text2']}; font-size: 11px;")
        self.hint.hide()
        hl.addWidget(self.hint)
        outer.addWidget(head)

        # The row whose box the typed name ticked, and whether the tick
        # was ours (so it comes off again if the name changes). A click
        # on that row by hand makes the tick the user's own.
        self._typed_row = None
        self._typed_ticked = False
        self._ticking = False

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(8, 0, 8, 12)
        self.list.setSpacing(0)
        self.list.addStretch(1)
        self.scroll.setWidget(inner)
        outer.addWidget(self.scroll, 1)

        for tag in sorted(tags, key=lambda t: t["name"].lower()):
            self._add_row(tag)

        self.empty = QLabel("No tag by that name. Press Save or Add tag "
                            "to make it.")
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(
            f"color: {T['text2']}; font-size: 12px; padding: 20px 12px;")
        self.empty.hide()
        self.list.insertWidget(self.list.count() - 1, self.empty)

        buttons = QDialogButtonBox(QDialogButtonBox.Save
                                   | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Save")
        # Return belongs to the text field, not to Save.
        #
        # A QDialogButtonBox makes its accept button the dialog default,
        # and Qt gives the default button priority over a line edit's
        # returnPressed — so typing a tag and pressing Enter saved and
        # closed the dialog without ever adding it. Which is exactly what
        # "cannot add new tags" looks like.
        for button in buttons.buttons():
            button.setAutoDefault(False)
            button.setDefault(False)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        foot = QWidget()
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(18, 12, 18, 14)
        fl.addWidget(buttons)
        foot.setStyleSheet(f"border-top: 1px solid {T['line']};")
        outer.addWidget(foot)

    def keyPressEvent(self, e):
        """Enter never closes Tags… (task 64).

        The field it was typed in has already acted on it: the top field
        adds the tag (returnPressed), a rename field ends the rename
        (editingFinished). Then the key went on up to the dialog, and a
        QDialogButtonBox makes Save the default button when it is shown,
        even after setDefault(False) — so Enter also pressed Save and the
        window closed. Save is pressed with the mouse (or Tab, Space)."""
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            e.accept()
            return
        super().keyPressEvent(e)

    # -- rows --------------------------------------------------------------

    def _add_row(self, tag):
        row = TagRow(tag, ticks=self.TICKS)
        row.deleted.connect(self._remove_row)
        row.check.toggled.connect(lambda _on, r=row: self._row_toggled(r))
        self.list.insertWidget(self.list.count() - 1, row)
        self._rows.append(row)
        return row

    def _remove_row(self, row):
        n = row.tag.get("count", 0)
        if n:
            ok = QMessageBox.question(
                self, "Delete tag",
                f"Remove “{row.tag['name']}” from {n} "
                f"station{'' if n == 1 else 's'}?",
                QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel)
            if ok != QMessageBox.Yes:
                return
        # A tag made in this window has no old name to delete (None, which
        # the caller's remove_tag couldn't take).
        if row.original.get("name") is not None:
            self._deleted.append(row.original["name"])
        self._deleted.extend(getattr(row, "merged", []))
        self._rows.remove(row)
        if row is self._typed_row:
            self._typed_row = None
            self._typed_ticked = False
        row.hide()                  # or Qt's queued show makes it a window (task 148)
        row.setParent(None)

    def _add(self):
        name = self.new_tag.text().strip()
        if not name:
            return
        if any(r.tag["name"].lower() == name.lower() for r in self._rows):
            for r in self._rows:
                if r.tag["name"].lower() == name.lower():
                    r.check.setChecked(True)
            # Ticked for good: clearing the field mustn't take it off.
            self._typed_row = None
            self._typed_ticked = False
            self.new_tag.clear()
            self._filter("")
            return
        used = {r.tag["colour"] for r in self._rows}
        colour = next((h for _, h in PALETTE if h not in used), PALETTE[0][1])
        row = self._add_row({"name": name, "colour": colour, "count": 0,
                             "assigned": True})
        row.original = {"name": None}       # marks it as newly created
        self.new_tag.clear()
        self._filter("")

    def _save(self):
        """Save, counting a tag still sitting in the search field.

        Typing a tag's name and pressing Save reads as "put this tag on"
        (task 17). A tag's exact name is already ticked in the list by
        the time Save is pressed (task 63: Save used to apply it while
        its box sat unticked, so the window didn't show what Save would
        do) — and if it was unticked by hand after that, it stays off.
        A name that matches no tag at all is a new tag, made and ticked
        here. Part of a name ("ja" while you look for Jazz) is only a
        search, and is left alone.
        """
        text = self.new_tag.text().strip()
        if text:
            low = text.lower()
            if not any(r.matches(low) for r in self._rows):
                self._add()
        self.accept()

    def _filter(self, text):
        typed = text.strip()
        text = typed.lower()
        visible = 0
        for r in self._rows:
            show = r.matches(text)
            r.setVisible(show)
            visible += show
        if text and visible == 0:
            self.empty.setText(self._empty_text(typed))
        self.empty.setVisible(bool(text) and visible == 0)
        self._follow_typed(text)

    def _empty_text(self, typed):
        return (f"No tag called “{typed}” yet. Press Save or Add tag to "
                "make it and put it on this station.")

    # -- the typed name ticks its tag (task 63) ----------------------------

    def _tick(self, row, on):
        self._ticking = True
        try:
            row.check.setChecked(on)
        finally:
            self._ticking = False

    def _row_toggled(self, row):
        if not self._ticking and row is self._typed_row:
            # Clicked by hand: the tick (or no tick) is the user's now,
            # and Save keeps it as it is.
            self._typed_ticked = False
        self._show_hint()

    def _follow_typed(self, low):
        match = None
        if low:
            match = next((r for r in self._rows
                          if r.tag["name"].lower() == low), None)
        old = self._typed_row
        if old is not None and old is not match:
            if self._typed_ticked and old in self._rows:
                self._tick(old, False)
            self._typed_row = None
            self._typed_ticked = False
        if match is not None and match is not old:
            self._typed_row = match
            self._typed_ticked = not match.check.isChecked()
            if self._typed_ticked:
                self._tick(match, True)
        self._show_hint()

    def _show_hint(self):
        row = self._typed_row
        if row is None:
            self.hint.hide()
            return
        name = row.tag["name"]
        if row.check.isChecked():
            self.hint.setText(f"“{name}” is ticked. Save puts it on this "
                              "station.")
        else:
            self.hint.setText(f"“{name}” is not ticked. Save leaves it off "
                              "this station.")
        self.hint.show()

    # -- result ------------------------------------------------------------

    def result_changes(self):
        """What to write back: the station's tags, plus edits to the shared
        tag list. Renames are keyed on the old name so the caller can update
        every station that used it."""
        assigned, renamed, recoloured, created = [], {}, {}, []
        for r in self._rows:
            tag, was = r.tag, r.original
            if tag.get("assigned"):
                assigned.append(tag["name"])
            if was.get("name") is None:
                created.append({"name": tag["name"], "colour": tag["colour"]})
                continue
            if tag["name"] != was["name"]:
                renamed[was["name"]] = tag["name"]
            if tag["colour"] != was["colour"]:
                recoloured[tag["name"]] = tag["colour"]
        return {"assigned": assigned, "renamed": renamed,
                "recoloured": recoloured, "created": created,
                "deleted": list(self._deleted)}


class TagManagerDialog(TagsDialog):
    """Every tag, to add, rename, recolour and remove (task 87).

    The Tags… window without a station: no boxes to tick. The pencil
    beside "Tags" on the left (and "Edit tags…" on the collapsed rail)
    opens it. A click on a tag (or its pencil) renames it; giving it a
    name another tag already has merges the two, after asking.

    tags: [{name, colour, count, ids}] — ids is the set of station ids
    that carry the tag, so a merge can count its stations without
    counting a station that has both twice.
    """

    TICKS = False
    NOTE = ("All your tags. Click a tag (or its pencil) to rename it, "
            "pick its color with the color button, and remove it with "
            "the trash button. Type a new name at the top and press Add tag to make "
            "one. A change here applies to every station that has the tag. "
            "Nothing is changed until you press Save.")

    def _title(self, _station_name):
        return "Edit tags"

    def __init__(self, tags, parent=None):
        super().__init__(None, tags, parent)
        self.new_tag.setPlaceholderText("Find a tag, or type a new one")

    def _add_row(self, tag):
        tag = dict(tag, assigned=False)
        tag["ids"] = set(tag.get("ids") or ())
        row = super()._add_row(tag)
        # Old names of tags merged into this one; the caller renames them
        # to this one's name.
        row.merged = []
        row.rename_check = self._check_rename
        return row

    # -- add ---------------------------------------------------------------

    def _add(self):
        name = self.new_tag.text().strip()
        if not name:
            return
        if self._named(name) is not None:
            # Already a tag: the field keeps the name, the list shows that
            # tag, and the line under the field says so.
            self._show_hint()
            return
        used = {r.tag["colour"] for r in self._rows}
        colour = next((h for _, h in PALETTE if h not in used), PALETTE[0][1])
        row = self._add_row({"name": name, "colour": colour, "count": 0})
        row.original = {"name": None}       # marks it as newly created
        self.new_tag.clear()
        self._filter("")
        # After the clear above, which hides the line.
        self.hint.setText(f"“{name}” added. Press Save to keep it.")
        self.hint.show()

    def _named(self, name, other_than=None):
        low = name.strip().lower()
        return next((r for r in self._rows
                     if r is not other_than and r.tag["name"].lower() == low),
                    None)

    def _empty_text(self, typed):
        return (f"No tag called “{typed}” yet. Press Add tag (or Save) to "
                "make it.")

    # -- the typed name: nothing to tick, only a note ----------------------

    def _follow_typed(self, low):
        self._typed_row = self._named(low) if low else None
        self._typed_ticked = False
        self._show_hint()

    def _row_toggled(self, row):
        pass

    def _show_hint(self):
        row = self._typed_row
        if row is None:
            self.hint.hide()
            return
        self.hint.setText(f"“{row.tag['name']}” is already a tag.")
        self.hint.show()

    # -- rename onto another tag's name = merge ----------------------------

    def _check_rename(self, row, text):
        other = self._named(text, other_than=row)
        if other is None:
            return True
        both = row.tag["ids"] | other.tag["ids"]
        n = len(row.tag["ids"] - other.tag["ids"])
        moved = (f" {n} more station{'' if n == 1 else 's'} will have "
                 f"“{other.tag['name']}”." if n else "")
        ok = QMessageBox.question(
            self, "Merge tags",
            f"There is already a tag called “{other.tag['name']}”. "
            f"Merge “{row.tag['name']}” into it?{moved}",
            QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel)
        if ok != QMessageBox.Yes:
            row.name.setText(row.tag["name"])
            return False
        if row.original.get("name") is not None:
            other.merged.append(row.original["name"])
        other.merged.extend(row.merged)
        other.tag["ids"] = both
        other.tag["count"] = len(both)
        other.count.setText(other._count_text())
        self._rows.remove(row)
        if row is self._typed_row:
            self._typed_row = None
        # Not deleted on the spot: this runs inside the row's own
        # "editing finished".
        row.hide()
        self.list.removeWidget(row)
        row.deleteLater()
        return False

    # -- result ------------------------------------------------------------

    def result_changes(self):
        """renamed: {old name: new name} (merges included), deleted: old
        names, created: new names, colours: {name: colour} for every tag
        kept, tags: every tag kept, by its new name."""
        renamed, created, colours, kept = {}, [], {}, []
        for r in self._rows:
            name, was = r.tag["name"], r.original.get("name")
            kept.append(name)
            colours[name] = r.tag["colour"]
            if was is None:
                created.append(name)
            elif was != name:
                renamed[was] = name
            for old in r.merged:
                renamed[old] = name
        return {"renamed": renamed, "deleted": list(self._deleted),
                "created": created, "colours": colours, "tags": kept}
