"""Frequencies — Settings > Library and tools > Sync with another device,
and the Pair a device… window (task 126, Batch CJ). The work is in
sync.py; this is only what the user sees."""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QVBoxLayout,
                             QWidget)

import sync
import sync_core

# Sync automatically (build 167, task 223): the interval's choices.
INTERVAL_LABELS = {5: "Every 5 minutes", 15: "Every 15 minutes",
                   30: "Every 30 minutes", 60: "Every hour",
                   120: "Every 2 hours",
                   0: "Off (only when I press Sync now)"}


def build_section(section, row):
    """The section's widget. `section` and `row` are optionspage's own
    _section and _row helpers, so it looks like its neighbors."""
    box, lay = section(
        "Sync with another device",
        "Keep your frequencies (with their pictures and settings), tags, "
        "stars, History and look the same on your other computers and your "
        "phone. Devices sync when Frequencies opens, every so often while "
        "both are open on the same network, and when you press Sync now; "
        "nothing goes through the internet.")
    svc = sync.service()
    status = QLabel()
    status.setWordWrap(True)
    lay.addWidget(status)
    # What went wrong in the last Sync now, in plain words (build 167).
    problem = QLabel()
    problem.setWordWrap(True)
    problem.hide()
    lay.addWidget(problem)
    now = QPushButton("Sync now")
    now.setToolTip("Look for your paired devices on this network and sync "
                   "with them right away")
    try:
        import syncicon
        from theme import T
        syncicon.attach_button(now, svc, lambda: T["text2"])
    except Exception:                           # noqa: BLE001
        pass
    every = QComboBox()
    for minutes in sync_core.INTERVAL_CHOICES:
        every.addItem(INTERVAL_LABELS[minutes], minutes)
    every.setToolTip("How often this device looks for your other devices "
                     "and syncs with them (and once when Frequencies "
                     "opens). This setting is this device's own.")
    every_label = QLabel("Sync automatically")
    lay.addWidget(row(now, every_label, every))
    # The same "What to sync" choice Sync now asks (build 169, task 225).
    choose = QPushButton("Choose what to sync…")
    choose.setToolTip("Pick what this device keeps the same with each "
                      "paired device: frequencies, pictures, settings, "
                      "stars and History")
    lay.addWidget(row(choose))
    peers_box = QWidget()
    peers_lay = QVBoxLayout(peers_box)
    peers_lay.setContentsMargins(0, 0, 0, 0)
    peers_lay.setSpacing(4)
    lay.addWidget(peers_box)
    pair = QPushButton("Pair a device…")
    me = QLabel()
    me.setProperty("role", "secondary")
    lay.addWidget(row(pair, me))

    def refresh():
        if svc is None:
            status.setText("Sync isn't available in this build.")
            pair.setEnabled(False)
            now.setEnabled(False)
            every.setEnabled(False)
            choose.setEnabled(False)
            return
        status.setText(svc.summary())
        busy = svc.busy
        now.setText("Syncing…" if busy else "Sync now")
        now.setEnabled(svc.ok and bool(svc.peers) and not busy)
        choose.setEnabled(svc.ok and bool(svc.peers))
        problem.setText(svc.error)
        problem.setVisible(bool(svc.error))
        index = every.findData(svc.interval_minutes())
        if index >= 0 and index != every.currentIndex():
            every.blockSignals(True)
            every.setCurrentIndex(index)
            every.blockSignals(False)
        me.setText(f"This device: {svc.name}")
        pair.setEnabled(svc.ok)
        while peers_lay.count():
            item = peers_lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().setParent(None)
        for pid, name, text in svc.rows():
            label = QLabel(f"<b>{_esc(name)}</b> · {_esc(text)}")
            label.setTextFormat(Qt.RichText)
            forget = QPushButton("Forget this device")
            forget.setToolTip(
                f"Stop syncing with {name}. Nothing is deleted on either "
                "device.")
            forget.clicked.connect(lambda _c=False, p=pid: svc.forget(p))
            peers_lay.addWidget(row(label, forget))

    if svc is not None:
        svc.status.connect(refresh)
        svc.activity.connect(refresh)
        def sync_now():
            # Sync now asks what to sync first; Sync in the window goes.
            if svc.peers and svc.ok:
                FrequensyncDialog(box.window(), sync_after=True).exec_()
            else:
                svc.sync_now()
        now.clicked.connect(sync_now)
        choose.clicked.connect(
            lambda: FrequensyncDialog(box.window()).exec_())
        every.currentIndexChanged.connect(
            lambda i: svc.set_interval(every.itemData(i)))
        # The "… ago" text gets older by itself.
        timer = QTimer(box)
        timer.setInterval(30000)
        timer.timeout.connect(refresh)
        timer.start()
    pair.clicked.connect(lambda: PairDialog(box.window()).exec_())
    refresh()
    box.sync_now_button = now
    box.sync_interval = every
    box.sync_problem = problem
    box.sync_choose = choose
    return box


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


class PairDialog(QDialog):
    """Shows this device's code (and its QR for a phone) and takes a code
    from another device. One of the two devices shows, the other types."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pair a device")
        self.svc = sync.service()
        outer = QVBoxLayout(self)
        outer.setSpacing(12)
        code, qr = self.svc.open_pairing()

        cols = QHBoxLayout()
        cols.setSpacing(24)
        left = QVBoxLayout()
        head = QLabel("On the other device, type this code")
        head.setProperty("role", "section")
        left.addWidget(head)
        big = QLabel(code)
        big.setTextInteractionFlags(Qt.TextSelectableByMouse)
        font = big.font()
        font.setPointSize(font.pointSize() + 10)
        font.setBold(True)
        font.setLetterSpacing(font.AbsoluteSpacing, 3)
        big.setFont(font)
        left.addWidget(big)
        hint = QLabel("The code works for 10 minutes. On your phone, go to "
                      "Settings > Sync with another device > Pair a device… "
                      "and scan the QR code.")
        hint.setProperty("role", "secondary")
        hint.setWordWrap(True)
        left.addWidget(hint)
        left.addStretch(1)
        cols.addLayout(left, 1)
        pic = QLabel()
        try:
            from sharedialog import qr_pixmap
            pixmap = qr_pixmap(qr, size=200)
        except Exception:                       # noqa: BLE001
            pixmap = None
        if pixmap is not None:
            pic.setPixmap(pixmap)
        cols.addWidget(pic, 0, Qt.AlignTop)
        outer.addLayout(cols)

        other = QLabel("Or type a code that another device is showing")
        other.setProperty("role", "section")
        outer.addWidget(other)
        line = QHBoxLayout()
        self.field = QLineEdit()
        self.field.setPlaceholderText("ABCDE-12345")
        self.field.setMaxLength(14)
        self.go = QPushButton("Pair")
        self.go.setDefault(True)
        line.addWidget(self.field, 1)
        line.addWidget(self.go)
        outer.addLayout(line)
        self.note = QLabel(" ")
        self.note.setWordWrap(True)
        outer.addWidget(self.note)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close)
        outer.addLayout(bottom)

        self.go.clicked.connect(self._pair)
        self.field.returnPressed.connect(self._pair)
        self.svc.pairing_done.connect(self._done)
        self.svc.pairing_failed.connect(self._failed)
        self.svc.choose_needed.connect(self._choose)
        self._chosen_for = ""
        self.setMinimumWidth(520)

    def _pair(self):
        self.go.setEnabled(False)
        self.note.setText("Looking for that device…")
        self.svc.pair_with(self.field.text())

    def _done(self, name):
        self.note.setText(f"Paired with {name}. Syncing now.")
        self.go.setEnabled(False)
        QTimer.singleShot(1500, self._close_after_pairing)

    def _choose(self, pid):
        # This device typed the code, so it starts the sync: What to sync
        # comes up first (build 169, task 225).
        self._chosen_for = pid
        name = self.svc.peers.get(pid, {}).get("name", "the other device")
        self.note.setText(f"Paired with {name}. Choose what to sync.")

    def _close_after_pairing(self):
        pid, parent = self._chosen_for, self.parent()
        self.accept()
        if pid:
            FrequensyncDialog(parent, peers=[pid], sync_after=True,
                              first=True).exec_()

    def _failed(self, message):
        self.note.setText(message)
        self.go.setEnabled(True)

    def done(self, result):
        try:
            self.svc.pairing_done.disconnect(self._done)
            self.svc.pairing_failed.disconnect(self._failed)
            self.svc.choose_needed.disconnect(self._choose)
        except (TypeError, RuntimeError):
            pass
        self.svc.close_pairing()
        super().done(result)


# What to sync (build 169, task 225). The user's name for the window.
PARTS = (
    ("stations", "Saved Frequencies",
     "Your stations and their settings: names, tags, equalizer, gain, "
     "ad cover and the rest"),
    ("art", "Station pictures", "The picture of each station"),
    ("settings", "Settings", "The settings ticked below"),
    ("favorites", "Favorites", "Starred songs"),
    ("history", "History", "Songs heard"),
)
GROUPS = (
    ("look", "Look", "Theme, accent color, 12-hour clock"),
    ("playback", "Playback",
     "Static, crossfade, normalize, skips and checks, sleep timer, "
     "shuffle, 10-band EQ"),
    ("lyrics", "Lyrics and artists",
     "Lyrics, artist info, photo pack, band logos, lyric size, songs "
     "kept"),
    ("list", "Saved Frequencies list",
     "Sort, tag colors and spare tags, jump to playing"),
    ("desktop", "Desktop only",
     "Clippy tips, reminders, artist panel, open links in apps"),
)


class FrequensyncDialog(QDialog):
    """What to sync with each paired device. Asked before the first sync
    with a device paired from here, and on Sync now; Settings' "Choose
    what to sync…" opens it too. The choice is kept per device; automatic
    syncs use it without asking."""

    def __init__(self, parent=None, peers=None, sync_after=False,
                 first=False):
        super().__init__(parent)
        self.setWindowTitle("Frequensync")
        self.svc = sync.service()
        self.sync_after = sync_after
        ids = [p for p in (peers or [pid for pid, _n, _t in self.svc.rows()])
               if p in self.svc.peers]
        self.ids = ids
        self.choices = {pid: self.svc.saved_what(pid) for pid in ids}
        self.current = ids[0] if ids else ""
        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        head = QLabel()
        head.setProperty("role", "section")
        outer.addWidget(head)
        self.head = head
        self.pick = QComboBox()
        for pid in ids:
            self.pick.addItem(self.svc.peers[pid]["name"], pid)
        if len(ids) > 1:
            outer.addWidget(self.pick)

        self.boxes = {}
        self.group_boxes = {}
        for key, label, tip in PARTS:
            box = QCheckBox(label)
            box.setToolTip(tip)
            self.boxes[key] = box
            if key == "art":
                outer.addWidget(_indent(box))
            else:
                outer.addWidget(box)
            if key == "settings":
                for gkey, glabel, gtip in GROUPS:
                    g = QCheckBox(glabel)
                    g.setToolTip(gtip)
                    self.group_boxes[gkey] = g
                    hint = QLabel(gtip)
                    hint.setProperty("role", "secondary")
                    hint.setWordWrap(True)
                    holder = QWidget()
                    hl = QVBoxLayout(holder)
                    hl.setContentsMargins(0, 0, 0, 0)
                    hl.setSpacing(0)
                    hl.addWidget(g)
                    hl.addWidget(_indent(hint, 26))
                    outer.addWidget(_indent(holder))
                    g.holder = holder
        note = QLabel("Anything you uncheck isn't sent to or taken from "
                      "that device, and stays as it is on each one.")
        note.setProperty("role", "secondary")
        note.setWordWrap(True)
        outer.addWidget(note)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        cancel = QPushButton("Not now" if first else "Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Sync" if sync_after else "Save")
        ok.setDefault(True)
        ok.clicked.connect(self._ok)
        bottom.addWidget(cancel)
        bottom.addWidget(ok)
        outer.addLayout(bottom)
        self.ok_button = ok

        self._busy = False
        self._desktop_shown = True
        self.boxes["stations"].toggled.connect(self._stations_toggled)
        self.boxes["settings"].clicked.connect(self._settings_clicked)
        for g in self.group_boxes.values():
            g.toggled.connect(self._group_toggled)
        self.pick.currentIndexChanged.connect(self._picked)
        self._show(self.current)
        self.setMinimumWidth(500)

    # -- the choice on screen <-> self.choices ---------------------------

    def _show(self, pid):
        self.current = pid
        what = self.choices.get(pid) or sync_core.clean_what(None)
        name = self.svc.peers.get(pid, {}).get("name", "your other device")
        self.head.setText(f"What to sync with {name}")
        # Desktop only settings: only between desktops (a phone neither
        # sends nor takes them). Not known yet counts as a desktop.
        platform = self.svc.peers.get(pid, {}).get("platform", "desktop")
        self._desktop_shown = platform == "desktop"
        self.group_boxes["desktop"].holder.setVisible(self._desktop_shown)
        self._busy = True
        for key in sync_core.WHAT_PARTS:
            if key != "settings":
                self.boxes[key].setChecked(bool(what[key]))
        for gkey, g in self.group_boxes.items():
            g.setChecked(bool(what["settings"] and what["groups"][gkey]))
        self._busy = False
        self._stations_toggled(self.boxes["stations"].isChecked())
        self._sync_parent()

    def _read(self):
        old = self.choices.get(self.current) or sync_core.clean_what(None)
        what = {key: self.boxes[key].isChecked()
                for key in sync_core.WHAT_PARTS if key != "settings"}
        groups = {k: g.isChecked() for k, g in self.group_boxes.items()}
        if not self._desktop_shown:
            groups["desktop"] = old["groups"].get("desktop", True)
        what["groups"] = groups
        what["settings"] = any(g.isChecked() for g in self._shown_groups())
        return sync_core.clean_what(what)

    def _picked(self, index):
        if self.current:
            self.choices[self.current] = self._read()
        self._show(self.pick.itemData(index))

    def _stations_toggled(self, on):
        # Pictures travel with each frequency, so they need it.
        self.boxes["art"].setEnabled(on)
        if not on:
            self._busy = True
            self.boxes["art"].setChecked(False)
            self._busy = False
        elif not self._busy and not self.boxes["art"].isChecked():
            self.boxes["art"].setChecked(True)

    def _shown_groups(self):
        return [g for k, g in self.group_boxes.items()
                if k != "desktop" or self._desktop_shown]

    def _sync_parent(self):
        shown = self._shown_groups()
        # Checked while any group is (the groups say which).
        box = self.boxes["settings"]
        box.blockSignals(True)
        box.setChecked(any(g.isChecked() for g in shown))
        box.blockSignals(False)

    def _settings_clicked(self, _checked=False):
        on = self.boxes["settings"].isChecked()
        self._busy = True
        for g in self._shown_groups():
            g.setChecked(on)
        self._busy = False
        self._sync_parent()

    def _group_toggled(self, _on):
        if not self._busy:
            self._sync_parent()

    def _ok(self):
        if self.current:
            self.choices[self.current] = self._read()
        self.svc.set_what(self.choices, sync=self.sync_after)
        self.accept()


def _indent(widget, px=22):
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(px, 0, 0, 0)
    lay.addWidget(widget)
    return holder
