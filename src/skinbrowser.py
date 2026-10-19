"""Frequencies — the skin browser.

A list on the left, a preview on the right, and the three things you
actually want at the bottom: point it at a folder, go and get more, close.

The preview is a real skinned player rather than a screenshot: the skin
format has no preview image, and rendering the thing itself is both more
honest and less work than compositing a fake one.
"""

import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout,
                             QLabel, QListWidget, QListWidgetItem,
                             QPushButton, QVBoxLayout, QWidget)

import storage
import winampskin

SKIN_EXTS = (".wsz", ".zip")


class SkinBrowser(QDialog):
    """Pick a skin, see it before committing to it."""

    skin_chosen = pyqtSignal(str)           # path to a .wsz
    eq_fill_changed = pyqtSignal(str, bool)  # skin name, filled

    def __init__(self, parent=None, folder="", current="", eq_fills=None):
        super().__init__(parent)
        self.setWindowTitle("Skins")
        self.setMinimumSize(760, 520)
        self._folder = folder or ""
        self._current = current or ""
        self._eq_fills = dict(eq_fills or {})
        self._preview = None
        self._museum = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)

        body = QHBoxLayout()
        body.setSpacing(16)

        self.list = QListWidget()
        self.list.setMinimumWidth(260)
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        body.addWidget(self.list, 1)

        body.addWidget(self._build_preview(), 1)
        outer.addLayout(body, 1)

        outer.addLayout(self._build_buttons())
        self.reload()

    # -- construction ------------------------------------------------------

    def _build_preview(self):
        side = QWidget()
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # Per skin, not global: some skins draw their own filled bars into
        # the equaliser graph and a second fill on top of that is a mess,
        # while others leave it empty and look broken without one.
        self.blank_eq = QCheckBox("Blank EQ")
        self.blank_eq.setToolTip(
            "Leave this skin's equalizer graph empty rather than filling it")
        self.blank_eq.toggled.connect(self._eq_toggled)
        lay.addWidget(self.blank_eq, 0, Qt.AlignRight)

        self.preview_box = QWidget()
        self.preview_box.setObjectName("SkinPreview")
        self.preview_box.setMinimumHeight(300)
        self.preview_lay = QVBoxLayout(self.preview_box)
        self.preview_lay.setContentsMargins(14, 14, 14, 14)
        self.preview_lay.setAlignment(Qt.AlignCenter)

        self.preview_note = QLabel("Select a skin to preview it.")
        self.preview_note.setProperty("role", "secondary")
        self.preview_note.setAlignment(Qt.AlignCenter)
        self.preview_lay.addWidget(self.preview_note)

        lay.addWidget(self.preview_box, 1)
        return side

    def _build_buttons(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        directory = QPushButton("Set directory…")
        directory.clicked.connect(self.pick_directory)
        row.addWidget(directory)

        self.more_btn = QPushButton("Get more…")
        self.more_btn.setToolTip(
            "Browse the skin museum. Anything you download lands in this "
            "folder and appears in the list.")
        self.more_btn.clicked.connect(self.get_more)
        row.addWidget(self.more_btn)

        row.addStretch(1)
        self.path_label = QLabel("")
        self.path_label.setProperty("role", "secondary")
        row.addWidget(self.path_label)
        row.addStretch(1)

        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        return row

    # -- the list ----------------------------------------------------------

    def folder(self):
        return self._folder

    def reload(self):
        self.list.clear()
        folder = self._folder
        self.path_label.setText(folder or "No folder chosen yet")
        if not folder or not os.path.isdir(folder):
            return
        import winampskin
        winampskin.copy_own_skin(folder)
        names = sorted(f for f in os.listdir(folder)
                       if f.lower().endswith(SKIN_EXTS))
        for name in names:
            item = QListWidgetItem(os.path.splitext(name)[0])
            item.setData(Qt.UserRole, os.path.join(folder, name))
            self.list.addItem(item)
            if os.path.abspath(item.data(Qt.UserRole)) == \
                    os.path.abspath(self._current or ""):
                self.list.setCurrentItem(item)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)

    def pick_directory(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Where your skins are",
            self._folder or os.path.expanduser("~"))
        if folder:
            self._folder = folder
            self.reload()

    def get_more(self):
        """The skin museum, in the browser you already use.

        It used to open in the bundled one, which renders the site blank:
        it is a JavaScript application that wants WebGL and the embedded
        browser has no GPU path. Your own browser has the graphics — and
        the download folder, and the bookmarks.

        What the app can still do is say where the file should end up and
        take it from there when it arrives.
        """
        import webview

        if not self._folder:
            self.pick_directory()
            if not self._folder:
                return
        webview.browse_skins(self._folder)
        self._show_note(
            f"The museum is open in your browser. Save a .wsz into\n"
            f"{self._folder}\nand it appears here — or drag one onto this "
            f"window.")

    def _selected(self, item, _previous=None):
        if item is None:
            return
        path = item.data(Qt.UserRole)
        try:
            skin = winampskin.load_skin(path)
        except Exception as exc:                # noqa: BLE001
            self._show_note(f"This skin can't be read: {exc}")
            return

        self._current = path
        name = getattr(skin, "name", os.path.basename(path))
        self.blank_eq.blockSignals(True)
        self.blank_eq.setChecked(not self._eq_fills.get(name, True))
        self.blank_eq.blockSignals(False)

        # Announced on selection rather than on close: seeing it applied to
        # the real player behind the dialog is the whole point of a
        # preview, and a skin you dislike is one click from being replaced.
        self.skin_chosen.emit(path)
        self._render(skin)

    def _show_note(self, text):
        self._clear_preview()
        self.preview_note.setText(text)
        self.preview_note.setVisible(True)

    def _clear_preview(self):
        if self._preview is not None:
            self._preview.setParent(None)
            self._preview.deleteLater()
            self._preview = None

    def _render(self, skin):
        """The whole stack, not just the player.

        A skin is four graphics, and the equalizer and playlist are where
        most of the character is — judging one from the player alone means
        finding out what you actually picked afterwards.
        """
        from classicwindow import ClassicWindow
        self._clear_preview()

        holder = QWidget(self.preview_box)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)          # the stack is flush, as it docks

        try:
            player = ClassicWindow(skin, parent=holder, scale=1)
        except Exception as exc:                # noqa: BLE001
            holder.deleteLater()
            self._show_note(f"This skin can't be drawn: {exc}")
            return
        player.set_title("Frequencies")
        player.set_playing(False)
        column.addWidget(player, 0, Qt.AlignHCenter)

        panels = []
        try:
            from classicextras import (EqualizerWindow, PlaylistWindow,
                                       TagWindow)
            if getattr(skin, "has_equalizer", lambda: True)():
                panels.append(EqualizerWindow(skin, scale=1, parent=holder))
            panels.append(TagWindow(skin, scale=1, parent=holder))
            playlist = PlaylistWindow(skin, scale=1, parent=holder,
                                      height=140)
            playlist.set_stations(["A station", "Another station",
                                   "One more"], 0)
            panels.append(playlist)
        except Exception:                       # noqa: BLE001
            pass                                # the player alone will do

        for panel in panels:
            column.addWidget(panel, 0, Qt.AlignHCenter)

        # Inert: it is a picture of a player, not a second one competing
        # for the buttons.
        for widget in [player, *panels]:
            widget.setEnabled(False)
            widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            widget.show()

        self.preview_note.setVisible(False)
        self.preview_lay.addWidget(holder, 0, Qt.AlignCenter)
        holder.show()
        self._preview = holder

    def _eq_toggled(self, blank):
        item = self.list.currentItem()
        if item is None:
            return
        try:
            skin = winampskin.load_skin(item.data(Qt.UserRole))
        except Exception:                       # noqa: BLE001
            return
        name = getattr(skin, "name", "")
        self._eq_fills[name] = not blank
        self.eq_fill_changed.emit(name, not blank)
