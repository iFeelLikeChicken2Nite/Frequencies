"""Frequencies — the global options page.

Everything that isn't per-station. The old app kept these in a menu bar,
which meant a slider living inside a dropdown and three related settings in
three different submenus. As a page they can be grouped by what they're
about, and since build 89 (task 102) each group is a tab:

    Appearance        theme, accent color, interface size, 12-hour times,
                      which extras the sidebar shows; the tray icon
    Sound             loudness, equalizer, audio output, static
    Frequencies       following the playing station, re-checking and
                      skipping stations that are down, show reminders,
                      the sleep timer
    Lyrics and artists  lyrics text size, biography lookups, corrections,
                      the artist photo pack and thumbs-down reports
                      (build 91)
    Accounts          Twitch chat sign-in, iBroadcast
    Library and tools backup, restore, transfer to and from other apps,
                      exporting a station as featured (dev builds only),
                      Create log
    Keyboard          every shortcut

Handlers that open a dialog live here rather than in FrequenciesWindow: the
window is about playing radio, and none of this is.
"""

import os
import time

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame,
                             QHBoxLayout, QInputDialog, QLabel, QMessageBox,
                             QPushButton, QScrollArea, QSlider, QTabWidget,
                             QVBoxLayout, QWidget)

import storage

RECHECK_CHOICES = [(5, "5 minutes"), (15, "15 minutes"), (30, "30 minutes"),
                   (60, "an hour"), (180, "3 hours")]

RATIO_CHOICES = [(0, "Never"), (2, "Every 2 tracks"), (4, "Every 4 tracks"),
                 (8, "Every 8 tracks"), (16, "Every 16 tracks")]


def _section(title, blurb=""):
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    head = QLabel(title)
    head.setProperty("role", "section")
    lay.addWidget(head)
    if blurb:
        note = QLabel(blurb)
        note.setProperty("role", "secondary")
        note.setWordWrap(True)
        lay.addWidget(note)
    return box, lay


def _rule():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setProperty("role", "rule")
    return line


def _row(*widgets):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    for widget in widgets:
        lay.addWidget(widget)
    lay.addStretch(1)
    return w


def _name_from_url(url):
    """A first guess at a station's name, from its address.

    The host with its www and its top-level domain removed reads like a
    name far more often than a path does — "radioparadise" rather than
    "/mp3-128" — and it gives something to edit rather than something to
    replace.
    """
    import urllib.parse
    try:
        host = urllib.parse.urlsplit(url).netloc.split(":")[0]
    except Exception:                           # noqa: BLE001
        return url
    parts = [p for p in host.split(".") if p and p != "www"]
    if len(parts) > 1:
        parts = parts[:-1]                      # drop .com, .fm, .co.uk
    return " ".join(parts).replace("-", " ").title() or url


class OptionsPage(QWidget):
    """Tabs, each of which scrolls, because some of them are a lot of
    settings and a cramped page that hides half of them is how the menu
    bar got into trouble."""

    changed = pyqtSignal(str)           # a setting key that just changed
    library_changed = pyqtSignal()      # stations were added or replaced
    stop_playback = pyqtSignal()        # a restore is about to swap the list
    chat_sign_in = pyqtSignal()         # Twitch chat (build 82, task 92e)
    chat_sign_out = pyqtSignal()

    def __init__(self, library, player, parent=None):
        super().__init__(parent)
        self.lib = library
        self.player = player

        # One scrolling page per tab, made now (empty) so the window can
        # attach smooth scrolling to each; the contents come later.
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self._tab_areas = {}            # tab name -> its QScrollArea
        self._tab_lays = {}             # tab name -> its layout
        for name in self.TABS:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.NoFrame)
            body = QWidget()
            lay = QVBoxLayout(body)
            lay.setContentsMargins(18, 16, 18, 24)
            lay.setSpacing(14)
            area.setWidget(body)
            self._tab_areas[name] = area
            self._tab_lays[name] = lay
            self.tabs.addTab(area, name)
        # No colors here: the selected tab's accent comes from the window's
        # own sheet (and follows the accent), the rest from the theme.
        self.tabs.setStyleSheet(
            "QTabBar::tab { padding: 8px 16px; margin-right: 2px;"
            " background: transparent; border: none;"
            " border-bottom: 2px solid transparent; }"
            "QTabBar::tab:selected { border-top: none; border-left: none;"
            " border-right: none; }")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.tabs)
        self.lay = self._tab_lays[self.TABS[0]]

        # Built in pieces once the app is up, not while it starts (build
        # 69, task 36). Every start-up of the overnight logs had its one
        # long wait inside this page; it was a quarter of the window's
        # whole build, for a page nobody has opened yet. Each piece is
        # one section, with a pause for the window between pieces, and
        # opening Settings before they are done builds the rest at once.
        # Build 89: a piece says which tab it goes into.
        self._pieces = [
            lambda: self._into("Appearance", self._build_look),
            lambda: self._into("Appearance", self._build_sidebar, True),
            lambda: self._into("Appearance", self._build_window, True),
            lambda: self._into("Appearance", self._end),
            lambda: self._into("Sound", self._build_sound),
            lambda: self._into("Sound", self._end),
            lambda: self._into("Frequencies", self._build_stations),
            lambda: self._into("Frequencies", self._end),
            lambda: self._into("Lyrics and artists", self._build_artists),
            lambda: self._into("Lyrics and artists", self._build_photo_pack,
                               True),
            lambda: self._into("Lyrics and artists", self._end),
            lambda: self._into("Accounts", self._build_twitch_chat),
            lambda: self._into("Accounts", self._build_ibroadcast, True),
            lambda: self._into("Accounts", self._end),
            lambda: self._into("Library and tools", self._build_library),
            lambda: self._into("Library and tools", self._build_updates,
                               True),
            lambda: self._into("Library and tools", self._build_diagnostics,
                               True),
            lambda: self._into("Library and tools", self._end),
            lambda: self._into("Keyboard", self._build_keys),
            lambda: self._into("Keyboard", self._end),
        ]
        self._build_ms = 0.0
        QTimer.singleShot(self.BUILD_AFTER_MS, self._build_next)

    TABS = ("Appearance", "Sound", "Frequencies", "Lyrics and artists",
            "Accounts", "Library and tools", "Keyboard")

    def _into(self, tab, build, rule=False):
        """Run one builder with `self.lay` pointing at that tab's page."""
        self.lay = self._tab_lays[tab]
        if rule:
            self.lay.addWidget(_rule())
        build()

    def _end(self):
        self.lay.addStretch(1)

    def scrollers(self):
        """The scroll areas, one per tab (for smooth scrolling)."""
        return [self._tab_areas[name] for name in self.TABS]

    def tab_of(self, widget):
        """The tab name whose page holds `widget`, or None."""
        for name, area in self._tab_areas.items():
            if area.widget().isAncestorOf(widget):
                return name
        return None

    def ensureWidgetVisible(self, widget, xmargin=50, ymargin=50):
        """Switch to the tab `widget` is on and scroll it into view."""
        name = self.tab_of(widget)
        if name is None:
            return
        self.tabs.setCurrentWidget(self._tab_areas[name])
        self._tab_areas[name].ensureWidgetVisible(widget, xmargin, ymargin)

    # A few seconds after start-up: the window is on screen and the last
    # station is tuning in by then.
    BUILD_AFTER_MS = 2500

    def _build_next(self):
        """One more piece of the page, then a pause for the window."""
        if not self._pieces:
            return
        began = time.monotonic()
        self._pieces.pop(0)()
        self._build_ms += (time.monotonic() - began) * 1000
        if self._pieces:
            QTimer.singleShot(0, self._build_next)
        else:
            self._built_log("after start-up")

    def ensure_built(self):
        """Build whatever is left now (the page is about to show)."""
        if not self._pieces:
            return
        began = time.monotonic()
        while self._pieces:
            self._pieces.pop(0)()
        self._build_ms += (time.monotonic() - began) * 1000
        self._built_log("when opened")

    def _built_log(self, when):
        try:
            import crashlog
            crashlog.write(f"[settings] page built {when}, "
                           f"{self._build_ms:.0f} ms in all")
        except Exception:                       # noqa: BLE001
            pass

    def showEvent(self, e):
        self.ensure_built()
        # The outputs again: mpv reports its list a moment after
        # start-up, and devices come and go (task 36).
        self._fill_outputs()
        # Signing in to iBroadcast happens while adding a station, after
        # this page was built (task 166).
        self._refresh_ib()
        # Photos added since (Edit this artist…) count at once (task 194).
        self._refresh_photo_count()
        super().showEvent(e)

    # -- helpers -----------------------------------------------------------

    def _save(self, key, value):
        self.lib.settings[key] = value
        self.lib.save_settings()
        self.changed.emit(key)

    def _check(self, key, label, tip="", default=True, on_change=None):
        box = QCheckBox(label)
        box.setChecked(bool(self.lib.settings.get(key, default)))
        if tip:
            box.setToolTip(tip)

        def toggled(on):
            self._save(key, bool(on))
            if on_change:
                on_change(on)
        box.toggled.connect(toggled)
        return box

    def _choice(self, key, choices, default, on_change=None):
        combo = QComboBox()
        for value, label in choices:
            combo.addItem(label, value)
        index = combo.findData(self.lib.settings.get(key, default))
        combo.setCurrentIndex(max(0, index))

        def picked(_i):
            value = combo.currentData()
            self._save(key, value)
            if on_change:
                on_change(value)
        combo.currentIndexChanged.connect(picked)
        return combo

    # -- sound -------------------------------------------------------------

    def _build_sound(self):
        box, lay = _section(
            "Sound",
            "Applies to every station. Per-station gain and EQ live in that "
            "station's own options.")

        lay.addWidget(self._check(
            "normalize", "Even out loudness between stations",
            "Quiet stations come up, loud ones come down",
            on_change=lambda on: self.player.set_normalize(on)))

        lay.addWidget(self._check(
            "eq_advanced", "Ten-band equalizer",
            "Off gives the three-band bass/mid/treble instead. Applies to "
            "every station; the values themselves are per-station.",
            default=False, on_change=lambda _on: self.changed.emit(
                "eq_advanced")))

        self.output = QComboBox()
        self.output.setMinimumWidth(240)
        self._fill_outputs()
        self.output.currentIndexChanged.connect(self._pick_output)
        lay.addWidget(_row(QLabel("Audio output"), self.output))

        lay.addWidget(self._check(
            "noise_between_stations", "Play static while a station connects",
            "Covers the silence while the stream opens",
            on_change=self._noise_switched))

        self.noise_vol = QSlider(Qt.Horizontal)
        self.noise_vol.setRange(0, 100)
        self.noise_vol.setFixedWidth(180)
        self.noise_vol.setValue(int(self.lib.settings.get("noise_volume", 100)))
        self.noise_label = QLabel(f"{self.noise_vol.value()}%")
        self.noise_label.setProperty("role", "secondary")
        self.noise_vol.valueChanged.connect(self._noise_volume)
        # And on release.
        #
        # valueChanged fires while the handle is down, and the save was
        # skipped then to avoid writing the file on every pixel of a
        # drag. But nothing fired afterwards — so a value reached by
        # dragging, which is how a slider is used, was never written at
        # all. It survived until the app closed and then reverted.
        self.noise_vol.sliderReleased.connect(
            lambda: self._save("noise_volume", int(self.noise_vol.value())))
        self.noise_row = _row(QLabel("Static volume"), self.noise_vol,
                              self.noise_label)
        # Grayed out while the static is off (build 89, task 102).
        self.noise_row.setEnabled(bool(self.lib.settings.get(
            "noise_between_stations", True)))
        lay.addWidget(self.noise_row)

        self.lay.addWidget(box)

    def _noise_switched(self, on):
        self.player.set_noise_enabled(on)
        self.noise_row.setEnabled(bool(on))

    def _build_lyric_size(self, lay):
        """The lyrics text size slider (it sat under Sound until build 89)."""
        self.lyric_size = QSlider(Qt.Horizontal)
        self.lyric_size.setRange(12, 34)
        self.lyric_size.setFixedWidth(180)
        self.lyric_size.setValue(int(self.lib.settings.get("lyric_size", 13)))
        self.lyric_size_label = QLabel(f"{self.lyric_size.value()} px")
        self.lyric_size_label.setProperty("role", "secondary")
        self.lyric_size.valueChanged.connect(self._lyric_size)
        lay.addWidget(_row(QLabel("Lyrics text size"), self.lyric_size,
                           self.lyric_size_label))

    def _lyric_size(self, value):
        self.lyric_size_label.setText(f"{value} px")
        self._save("lyric_size", int(value))
        self.changed.emit("lyric_size")

    def _noise_volume(self, value):
        self.noise_label.setText(f"{value}%")
        self.player.set_noise_volume(value)
        # Written on release rather than on every pixel of the drag.
        if not self.noise_vol.isSliderDown():
            self._save("noise_volume", int(value))

    def _fill_outputs(self):
        """The outputs mpv has reported (never asked for here: task 36).

        Filled again each time Settings is shown, quietly — a change of
        selection here restarts the station.
        """
        output = getattr(self, "output", None)
        if output is None:
            return                              # not built yet
        output.blockSignals(True)
        try:
            output.clear()
            output.addItem("System default", "auto")
            try:
                for name, label in self.player.audio_devices():
                    # audio_devices() leads with "auto" itself: it was
                    # in the list twice.
                    if name != "auto":
                        output.addItem(label, name)
            except Exception:                   # noqa: BLE001
                pass                            # mpv not up yet; default only
            index = output.findData(
                self.lib.settings.get("audio_device", "auto"))
            output.setCurrentIndex(max(0, index))
        finally:
            output.blockSignals(False)

    def _pick_output(self, _i):
        """Switch the device, and put the stream on it.

        mpv binds the output when a file is loaded, so setting the device
        mid-stream changes what the *next* one will use and leaves the
        current one playing where it was. Restarting the station is what
        actually moves the sound.
        """
        device = self.output.currentData()
        self._save("audio_device", device)
        self.player.set_audio_device(device)
        window = self.window()
        playback = getattr(window, "playback", None)
        if playback is not None and playback.current is not None:
            playback.play(playback.current)

    # -- stations ----------------------------------------------------------

    def _build_stations(self):
        box, lay = _section(
            "Frequencies",
            "How frequencies behave when you switch, when they stall, and "
            "when you go to sleep.")

        lay.addWidget(self._check(
            "jump_on_change",
            "Automatically scroll list position to currently playing "
            "frequency",
            default=True))

        lay.addWidget(self._check(
            "health_recheck", "Re-check frequencies that are down",
            "A frequency that comes back gets un-flagged on its own",
            on_change=self._recheck_switched))

        self.recheck_row = _row(
            QLabel("Check every"),
            self._choice("health_recheck_minutes", RECHECK_CHOICES, 30))
        # Grayed out while re-checking is off (build 89, task 102).
        self.recheck_row.setEnabled(bool(self.lib.settings.get(
            "health_recheck", True)))
        lay.addWidget(self.recheck_row)

        lay.addWidget(self._check(
            "auto_skip_broken", "Skip to the next frequency when one stalls"))

        lay.addWidget(self._check(
            "show_reminders", "Tell me when a scheduled show starts",
            "Uses the schedule you have set for each frequency.",
            default=True))

        self._build_sleep(lay)

        self.lay.addWidget(box)

    def _recheck_switched(self, on):
        self.recheck_row.setEnabled(bool(on))
        self.changed.emit("health_recheck")

    # -- artists -----------------------------------------------------------

    def _build_artists(self):
        box, lay = _section("Lyrics and artists")

        self._build_lyric_size(lay)

        # Dev builds only (the user, 2026-10-15): the shipped corrections
        # are the developer's to make; they reach users with the build and
        # the photo pack.
        import devmode
        if devmode.is_dev():
            corrections = QPushButton("Artist corrections…")
            corrections.clicked.connect(self._artist_corrections)
            corrections.setToolTip(
                "Fix a lookup that finds the wrong act — common for short "
                "or shared names")
            lay.addWidget(_row(corrections))

        self.lay.addWidget(box)

    def _artist_corrections(self):
        from dialogs import ArtistOverridesDialog
        ArtistOverridesDialog(self).exec_()

    def _build_photo_pack(self):
        """The developer's artist photo pack, opt-in, and where thumbs-down
        reports go (build 91, tasks 117 and 114)."""
        import devmode
        import photopack
        box, lay = _section(
            "Artist photo pack")
        keeper = photopack.keeper(self.lib.settings)
        has_pack = bool(photopack.pack_address())
        pack = QCheckBox(
            "Download the artist photo pack and keep it updated "
            "(Recommended to turn on)")
        pack.setChecked(bool(self.lib.settings.get(photopack.SETTING)))
        pack.setEnabled(has_pack)
        self.pack_status = QLabel(keeper.text if keeper else "")
        self.pack_status.setProperty("role", "secondary")
        self.pack_status.setWordWrap(True)
        check = QPushButton("Check now")
        check.setEnabled(has_pack and pack.isChecked())

        def toggled(on):
            self._save(photopack.SETTING, bool(on))
            check.setEnabled(has_pack and on)
            if keeper:
                keeper.set_enabled(on)
        pack.toggled.connect(toggled)
        self.pack_check = pack          # Clippy's Yes ticks it (task 143)
        if keeper:
            check.clicked.connect(keeper.check_now)
            keeper.status.connect(self.pack_status.setText)
        lay.addWidget(pack)
        lay.addWidget(_row(self.pack_status, check))
        self.pack_status.parentWidget().layout().setStretch(0, 1)

        if devmode.is_dev():
            from PyQt5.QtWidgets import QLineEdit
            for name, label, hint in (
                    ("pack", "Photo pack address",
                     "https://github.com/<you>/<repo>/releases/latest/"
                     "download/photo-pack.json"),
                    ("reports", "Report address",
                     "https://github.com/<you>/<repo> (blank: the photo "
                     "pack's repository)"),
                    ("art", "Picture link address",
                     "https://<your worker>.workers.dev (shared pictures "
                     "for QR codes)")):
                field = QLineEdit(photopack._address(name))
                field.setPlaceholderText(hint)
                field.setMinimumWidth(420)

                def saved(field=field, name=name):
                    photopack.save_addresses(**{name: field.text()})
                    if name == "pack":
                        has = bool(photopack.pack_address())
                        pack.setEnabled(has)
                        check.setEnabled(has and pack.isChecked())
                        if keeper:
                            keeper._say(keeper._resting())
                field.editingFinished.connect(saved)
                lay.addWidget(_row(QLabel(label), field))
            note = QLabel(
                "Dev build only. None of these addresses is a secret: the "
                "builds you share carry them. Thumbs-down reports are filed "
                "on the report address's repository. Shared pictures that "
                "have no address online are uploaded to the picture link "
                "address, so QR codes can carry a link to them.")
            note.setProperty("role", "secondary")
            note.setWordWrap(True)
            lay.addWidget(note)
            self._build_report_token(lay)
            self._build_publish(lay)
            self._build_publish_update(lay)
        self.lay.addWidget(box)

    def _build_report_token(self, lay):
        """Dev builds: the token that files thumbs-down reports (build 97,
        task 133). Shipped in the builds you share, so it must only be able
        to add issues to the reports repository."""
        import photopack
        from PyQt5.QtWidgets import QLineEdit
        field = QLineEdit(photopack.dev_report_token())
        field.setEchoMode(QLineEdit.Password)
        field.setPlaceholderText("github_pat_… (Issues only, reports "
                                 "repository only)")
        field.setMinimumWidth(420)
        field.editingFinished.connect(
            lambda: photopack.save_report_token(field.text()))
        lay.addWidget(_row(QLabel("Report token"), field))
        test = QPushButton("Send a test report")
        status = QLabel("")
        status.setProperty("role", "secondary")
        status.setWordWrap(True)
        lay.addWidget(_row(test, status))

        def run():
            photopack.save_report_token(field.text())
            status.setText("Sending…")
            test.setEnabled(False)
            self._test_job = photopack.test_report(
                lambda ok, text: (
                    status.setText(("Sent: " if ok else "Failed: ") + text
                                   + ("" if ok else ".")),
                    test.setEnabled(True)))
            if self._test_job is None:
                test.setEnabled(True)
        test.clicked.connect(run)
        note = QLabel(
            "The token the builds you share use to file the reports. "
            "github.com > Settings > Developer settings > Fine-grained "
            "tokens > Generate, only your reports repository, "
            "Issues: Read and write, nothing else. It ships in your next "
            "build; a leaked one can only add issues, and you can revoke it.")
        note.setProperty("role", "secondary")
        note.setWordWrap(True)
        lay.addWidget(note)

    def _build_publish(self, lay):
        """Dev builds: send your photos (base32 twice) and your bio fixes
        to the pack's GitHub release (build 95, ghpublish.py)."""
        import ghpublish
        import photopack
        from PyQt5.QtWidgets import QLineEdit
        field = QLineEdit(ghpublish.token())
        field.setEchoMode(QLineEdit.Password)
        field.setPlaceholderText("github_pat_… (a fine-grained token)")
        field.setMinimumWidth(420)
        field.editingFinished.connect(
            lambda: ghpublish.save_token(field.text()))
        lay.addWidget(_row(QLabel("GitHub token"), field))
        photos = QPushButton("Publish my photos to GitHub")
        bios = QPushButton("Publish my bio fixes to GitHub")
        vis = QPushButton("Publish my visualizer list to GitHub")
        buttons = (photos, bios, vis)
        self.publish_status = QLabel("")
        self.publish_status.setProperty("role", "secondary")
        self.publish_status.setWordWrap(True)
        lay.addWidget(_row(photos, bios))
        lay.addWidget(_row(vis))
        lay.addWidget(self.publish_status)
        # How many of your photos the published pack has (task 194).
        self.photo_count = QLabel("")
        self.photo_count.setProperty("role", "secondary")
        self.photo_count.setWordWrap(True)
        self.photo_count.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.photo_count)
        self._published_pack = None
        self._refresh_photo_count(fetch=True)
        note = QLabel(
            "Photos go up in base32, twice; users who opted in get them at "
            "their next check (Check now, or within a day). Bio fixes reach "
            "every user the same way, no opt-in. The visualizer list (the "
            "presets you turned off) is copied by each user build once, on "
            "its first run. The token: github.com > "
            "Settings > Developer settings > Fine-grained tokens > Generate, "
            "only this repository, Contents: Read and write. It stays on "
            "this PC and is never put in a build.")
        note.setProperty("role", "secondary")
        note.setWordWrap(True)
        lay.addWidget(note)

        def publish(kind):
            ghpublish.save_token(field.text())
            job = getattr(self, "_publish_job", None)
            if job is not None and job.isRunning():
                return
            job = ghpublish.Publish(kind, photopack.pack_address())
            job.progress.connect(self.publish_status.setText)
            job.done.connect(lambda ok, text: self.publish_status.setText(
                ("Done: " if ok else "Failed: ") + text + "."))
            self._publish_job = job
            for button in buttons:
                button.setEnabled(False)
            job.done.connect(lambda *_: [b.setEnabled(True)
                                         for b in buttons])
            if kind == "photos":
                job.done.connect(
                    lambda ok, _text: ok and self._refresh_photo_count(
                        fetch=True))
            job.start()
        photos.clicked.connect(lambda: publish("photos"))
        bios.clicked.connect(lambda: publish("bios"))
        vis.clicked.connect(lambda: publish("visualizer"))

    def _refresh_photo_count(self, fetch=False):
        """Your photos here against the published pack's (task 194).
        The pack's list is read once, and again after a photos publish."""
        label = getattr(self, "photo_count", None)
        if label is None:
            return
        import photopack
        job = getattr(self, "_count_job", None)
        if not fetch or job is not None:
            known = self._published_pack
            label.setText(photopack.photo_count(*known) if known
                          else photopack.photo_count() + (
                              " Checking the published pack…" if job
                              else ""))
            return
        address = photopack.pack_address()
        if not address:
            label.setText(photopack.photo_count(None, "no photo pack "
                                                "address"))
            return
        label.setText(photopack.photo_count() + " Checking the published "
                      "pack…")

        def came(manifest, why):
            self._count_job = None
            self._published_pack = (manifest, why)
            self._refresh_photo_count()
        job = photopack.ManifestFetch(address)
        job.done.connect(came)
        self._count_job = job
        job.start()

    def _build_publish_update(self, lay):
        """Dev builds: put a user build on the releases repository, which
        every user build updates from (build 125, task 177)."""
        import updater
        from PyQt5.QtWidgets import QLineEdit
        field = QLineEdit(updater.token())
        field.setEchoMode(QLineEdit.Password)
        field.setPlaceholderText("github_pat_… (frequencies-releases only)")
        field.setMinimumWidth(420)
        field.editingFinished.connect(
            lambda: updater.save_token(field.text()))
        lay.addWidget(_row(QLabel("Release token"), field))
        button = QPushButton("Publish this build as an update…")
        status = QLabel("")
        status.setProperty("role", "secondary")
        status.setWordWrap(True)
        lay.addWidget(_row(button))
        lay.addWidget(status)
        repo = updater.repository()
        where = f"{repo[0]}/{repo[1]}" if repo else "<you>/frequencies-releases"
        note = QLabel(
            f"Pick the user build you made ({updater.ASSETS[updater.OS]}); "
            f"it goes on {where} and every user build installs it the next "
            "time it's closed. Linux and Windows each publish their own "
            "file to the same release. The token: github.com > Settings > "
            "Developer settings > Fine-grained tokens > Generate, only "
            "that repository, Contents: Read and write. It stays on this "
            "PC and is never put in a build.")
        note.setProperty("role", "secondary")
        note.setWordWrap(True)
        lay.addWidget(note)

        def publish():
            updater.save_token(field.text())
            job = getattr(self, "_release_job", None)
            if job is not None and job.isRunning():
                return
            start = self.lib.settings.get("update_publish_folder") or \
                os.path.dirname(os.environ.get("APPIMAGE", "")) or \
                os.path.expanduser("~")
            path, _ = QFileDialog.getOpenFileName(
                self, "Publish which build?", start,
                f"User build ({updater.ASSETS[updater.OS]});;All files (*)")
            if not path:
                return
            self.lib.settings["update_publish_folder"] = os.path.dirname(path)
            info, problem = updater.build_info(path)
            if problem:
                status.setText(f"Not published: {problem}.")
                return
            asked = QMessageBox.question(
                self, "Publish this build",
                f"Publish build {info.get('build')} for "
                f"{updater.OS_NAMES[updater.OS]} to {where}?\n\nEveryone "
                "using Frequencies on "
                f"{updater.OS_NAMES[updater.OS]} gets it the next time "
                "they open and close the app.")
            if asked != QMessageBox.Yes:
                return
            job = updater.PublishUpdate(path)
            job.progress.connect(status.setText)
            job.done.connect(lambda ok, text: (
                status.setText(("Done: " if ok else "Failed: ") + text + "."),
                button.setEnabled(True)))
            self._release_job = job
            button.setEnabled(False)
            job.start()
        button.clicked.connect(publish)

    # -- library -----------------------------------------------------------

    def _build_library(self):
        box, lay = _section(
            "Library and tools",
            "Your stations, settings, tags and history are one JSON file. A "
            "backup is that file; a restore replaces what you have.")

        backup = QPushButton("Back up to a file…")
        backup.clicked.connect(self.backup)
        restore = QPushButton("Restore from a file…")
        restore.clicked.connect(self.restore)
        lay.addWidget(_row(backup, restore))

        scan = QPushButton("Import from an installed app…")
        scan.clicked.connect(self.import_installed)
        scan.setToolTip("Looks where other radio apps keep their station lists")
        imp = QPushButton("Import from a file…")
        imp.clicked.connect(self.import_file)
        exp = QPushButton("Export to another app…")
        exp.clicked.connect(self.export_file)
        lay.addWidget(_row(scan, imp, exp))

        # Dev builds only (build 89, task 102): it prepares a station to
        # ship as a featured suggestion, which only the developer does.
        import devmode
        if devmode.is_dev():
            featured = QPushButton("Export a station as featured…")
            featured.clicked.connect(self.export_featured)
            featured.setToolTip(
                "Dev build only. Tidy a station up, export it, and it can "
                "ship as one of the featured suggestions")
            lay.addWidget(_row(featured))

        self.lay.addWidget(box)

    def _build_updates(self):
        """Updates (build 125, task 177): checked at every launch; this is
        the button for checking now."""
        import updater
        box, lay = _section(
            "Updates",
            "Frequencies checks for a new build each time it starts, "
            "downloads it in the background and puts it in place when you "
            "close the app. Your settings and frequencies stay as they are.")
        updates = updater.updater()
        status = QLabel(updates.text if updates else "")
        status.setProperty("role", "secondary")
        status.setWordWrap(True)
        check = QPushButton("Check for updates")
        show = QPushButton("Show the download")
        show.setVisible(bool(updates and updates.manual))
        lay.addWidget(_row(check, show))
        lay.addWidget(status)
        if updates is not None:
            check.clicked.connect(lambda: updates.check(quiet=False))
            updates.status.connect(status.setText)
            updates.status.connect(
                lambda _t: show.setVisible(bool(updates.manual)))
            show.clicked.connect(
                lambda: self._open_folder_of(updates.manual))
        self.lay.addWidget(box)

    def _build_diagnostics(self):
        box, lay = _section(
            "Diagnostics")
        create = QPushButton("Create log")
        create.clicked.connect(self.create_log)
        create.setToolTip("Saves the app's log as a text file you can find")
        lay.addWidget(_row(create))
        self.lay.addWidget(box)

    def _make_log(self):
        """Write the log file now; its path, or None after telling the user
        it couldn't be written."""
        import crashlog
        import devmode
        try:
            path = crashlog.create_report("1.5", devmode.is_dev())
        except OSError as exc:
            QMessageBox.warning(self, "Create log",
                                f"Couldn't save the log: {exc}")
            return None
        self._last_log = path
        return path

    def _open_folder_of(self, path):
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices
        QDesktopServices.openUrl(
            QUrl.fromLocalFile(os.path.dirname(path)))

    def create_log(self):
        path = self._make_log()
        if path is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Create log")
        box.setIcon(QMessageBox.Information)
        box.setText("The log is saved.")
        box.setInformativeText(
            f"{path}\n\nIt holds what the app did in this run and the one "
            "before: stations tuned, errors and freezes. It has no "
            "passwords or sign-ins.")
        folder = box.addButton("Open folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec_()
        if box.clickedButton() is folder:
            self._open_folder_of(path)

    def backup(self):
        default = os.path.join(os.path.expanduser("~"),
                               time.strftime("frequencies-backup-%Y%m%d.json"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Back up library", default,
            "JSON files (*.json);;All files (*)")
        if not path:
            return
        try:
            storage.export_backup(
                path, self.lib.stations, self.lib.settings, self.lib.health,
                self.lib.history, storage.load_artist_overrides(),
                storage.load_station_overrides())
        except Exception as exc:                # noqa: BLE001
            QMessageBox.warning(self, "Back up", f"Couldn't write it: {exc}")
            return
        # Say what will not survive the trip.
        #
        # A backup carries settings, not audio. Music on this machine is
        # referred to by a path that will mean nothing elsewhere, and
        # iBroadcast stations are signed by an account. Finding that out
        # on restore, station by station, is worse than being told now.
        notes = []
        local = [s for s in self.lib.stations
                 if storage.is_custom_station(s) and s.get("local_path")]
        if local:
            notes.append(
                f"{len(local)} station(s) play your own music. The files "
                f"are not included — only where they were. Restoring on "
                f"another machine will ask for the folder.")
        ib = [s for s in self.lib.stations if s.get("ibroadcast_urls")]
        if ib:
            notes.append(
                f"{len(ib)} iBroadcast station(s) are included, and will "
                f"stay marked down until that account is signed in.")
        QMessageBox.information(
            self, "Back up",
            f"Saved to {path}" + ("\n\n" + "\n\n".join(notes)
                                  if notes else ""))

    def _relocate_local(self, stations):
        """Ask where the music went.

        A station that plays your own files stores a path, and a path
        from another machine points at nothing. Asking once per folder —
        not once per station — is the difference between a question and
        an interrogation.
        """
        import os as _os
        wanted = [s for s in stations
                  if storage.is_custom_station(s) and s.get("local_path")
                  and not _os.path.isdir(s.get("local_path") or "")]
        if not wanted:
            return

        answered = {}
        for station in wanted:
            old = station.get("local_path") or ""
            if old in answered:
                station["local_path"] = answered[old]
                continue
            name = storage.station_display_name(station)
            if QMessageBox.question(
                    self, "Music folder",
                    f"“{name}” plays music from:\n\n{old}\n\n"
                    f"That folder isn't on this machine. Find it now?",
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                answered[old] = old
                continue
            picked = QFileDialog.getExistingDirectory(
                self, f"Music folder for {name}",
                _os.path.expanduser("~"))
            answered[old] = picked or old
            station["local_path"] = answered[old]

    def _mark_ibroadcast(self, stations, health):
        """An iBroadcast station is only playable with its account.

        Restored onto a machine that is not signed in — or signed into
        somebody else's account — it cannot work, so it arrives marked
        down rather than arriving broken and looking fine.
        """
        try:
            import ibroadcast
            signed_in = bool(ibroadcast.load_tokens().get("refresh_token"))
        except Exception:                       # noqa: BLE001
            signed_in = False
        if signed_in:
            return
        for station in stations:
            if not station.get("ibroadcast_urls"):
                continue
            entry = health.setdefault(str(station.get("id")), {})
            entry["down"] = True
            entry["last_error"] = "not signed in to iBroadcast"

    def restore(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Restore library", os.path.expanduser("~"),
            "JSON files (*.json);;All files (*)")
        if not path:
            return
        # Replacing a library is not something to do by accident.
        if QMessageBox.question(
                self, "Restore",
                "This replaces your stations, tags and settings with the "
                "ones in that file.\n\nCarry on?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            (stations, settings, health, history, overrides,
             station_overrides) = storage.read_backup(path)
        except storage.BackupError as exc:
            QMessageBox.warning(self, "Restore failed", str(exc))
            return

        # Replace or merge, as before. Merge is the one people actually
        # want when moving between two machines they both use.
        box = QMessageBox(self)
        box.setWindowTitle("Restore library")
        box.setText(
            f"This backup holds {len(stations)} station(s).\n\n"
            "Replace your current library, or merge the new stations in?")
        replace_btn = box.addButton("Replace", QMessageBox.AcceptRole)
        merge_btn = box.addButton("Merge", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec_()
        clicked = box.clickedButton()

        self._relocate_local(stations)
        self._mark_ibroadcast(stations, health)

        if clicked is replace_btn:
            self.stop_playback.emit()
            self.lib.stations[:] = stations
            self.lib.settings.clear()
            self.lib.settings.update(settings)
            self.lib.health.clear()
            self.lib.health.update(health)
            # History and overrides come back on a replace, since replace
            # means "make this machine look like that one".
            if history:
                self.lib.history[:] = history
                self.lib.save_history()
            # A backup carries no artist photos (task 194): the ones
            # chosen on this computer stay.
            if overrides:
                storage.save_artist_overrides(
                    storage.keep_own_photos(overrides))
            if station_overrides:
                storage.save_station_overrides(station_overrides)
            message = f"Restored {len(stations)} station(s)."
        elif clicked is merge_btn:
            merged, added = storage.merge_stations(self.lib.stations, stations)
            self.lib.stations[:] = merged
            for station_id, entry in health.items():
                self.lib.health.setdefault(station_id, entry)
            message = f"Merged in {added} new station(s)."
        else:
            return

        self.lib.save_stations()
        self.lib.save_settings()
        storage.save_health(self.lib.health)
        self.library_changed.emit()
        QMessageBox.information(self, "Restore", message)

    def import_installed(self):
        import playlists
        found = playlists.discover_app_configs()
        if not found:
            QMessageBox.information(
                self, "Import",
                "No station lists from other apps turned up in the usual "
                "places.\n\nUse “Import from a file…” to point at one "
                "directly.")
            return
        labels = [f"{app} — {count} station(s)" for app, _path, count in found]
        label, ok = QInputDialog.getItem(
            self, "Import", "Import from which?", labels, 0, False)
        if ok:
            self._import_path(found[labels.index(label)][1])

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import stations", os.path.expanduser("~"),
            "Station lists (*.m3u *.m3u8 *.pls *.xspf *.opml *.json *.xml *.audpl *.zip "
            "*.db *.sqlite *.conf);;"
            "All files (*)")
        if not path:
            return
        # A Frequencies share zip goes to the window, which adds the
        # stations with all their settings (build 105, task 130a).
        if path.lower().endswith(".zip"):
            from PyQt5.QtWidgets import QApplication
            for w in QApplication.topLevelWidgets():
                if hasattr(w, "import_share_zip"):
                    w.import_share_zip(path)
                    return
        self._import_path(path)

    def _import_path(self, path):
        import playlists
        import radioapps
        try:
            _fmt, entries = playlists.read_file(path)
        except Exception as exc:                # noqa: BLE001
            QMessageBox.warning(self, "Import", f"Couldn't read it: {exc}")
            return
        if not radioapps.needs_lookup(entries):
            self._add_imported(entries)
            return
        # Some stations are only a radio-browser id (Gradio, Shortwave):
        # look them up off the GUI thread, then add.
        import workers
        job = workers.Lookup(lambda: radioapps.fill_in(entries))
        job.done.connect(lambda _r, _e: self._add_imported(entries))
        self._import_lookup = job
        job.start()

    def _add_imported(self, entries):
        import playlists
        self._import_lookup = None
        missing = sum(1 for e in entries if not e.get("url"))
        entries = [e for e in entries if e.get("url")]
        known = {(s.get("url") or "").lower() for s in self.lib.stations}
        added = 0
        for entry in entries:
            url = (entry.get("url") or "").strip()
            if not url or url.lower() in known:
                continue
            # playlists owns the entry -> station conversion, including the
            # insecure flag some apps set.
            station = playlists.to_station(entry)
            station["sort_index"] = len(self.lib.stations)
            self.lib.stations.append(station)
            known.add(url.lower())
            added += 1
        if added:
            self.lib.save_stations()
            self.library_changed.emit()
        QMessageBox.information(
            self, "Import",
            f"Added {added} station{'' if added == 1 else 's'}."
            + ("" if added == len(entries)
               else f" {len(entries) - added} were already in your list.")
            + ("" if not playlists.LAST_SKIPPED
               else f" {playlists.LAST_SKIPPED} were links to web pages, "
                    "not streams, and were skipped.")
            + ("" if not missing
               else f" {missing} couldn't be found on radio-browser.info "
                    "(the app only kept their directory number)."))

    def export_file(self):
        import playlists
        import radioapps
        if not self.lib.stations:
            QMessageBox.information(self, "Export", "There's nothing to export.")
            return
        labels = [playlists.FORMATS[f] for f in playlists.EXPORT_FORMATS]
        # Straight into another app's own list (build 113, task 152), for
        # each app whose list is on this computer.
        targets = radioapps.send_targets()
        labels += [f"Send to {label}" for _key, label, _path in targets]
        label, ok = QInputDialog.getItem(
            self, "Export stations", "Save in which app's format?",
            labels, 0, False)
        if not ok:
            return
        index = labels.index(label)
        if index >= len(playlists.EXPORT_FORMATS):
            self._send_to(*targets[index - len(playlists.EXPORT_FORMATS)])
            return
        fmt = playlists.EXPORT_FORMATS[index]
        path, _ = QFileDialog.getSaveFileName(
            self, "Export stations",
            os.path.join(os.path.expanduser("~"),
                         "frequencies" + playlists.default_extension(fmt)))
        if not path:
            return
        try:
            playlists.write_file(path, self.lib.stations, fmt)
        except playlists.PlaylistError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        QMessageBox.information(self, "Export", f"Saved to {path}")

    def _send_to(self, key, label, path):
        """Add frequencies to another app's own list: only adds, keeps a
        backup of its file, and won't while that app is open."""
        import radioapps
        from dialogs import StationPickerDialog
        title = f"Send to {label}"
        if radioapps.is_running(key):
            QMessageBox.information(
                self, title,
                f"{radioapps.app_name(key)} is open. Close it first, then "
                "try again.\n\nIt saves its own list when it closes, which "
                "would undo what Frequencies adds.")
            return
        stations = [s for s in self.lib.stations if radioapps.sendable(s)]
        if not stations:
            QMessageBox.information(
                self, title,
                "None of your frequencies can go to another app: folders "
                "of songs, iBroadcast, YouTube and Twitch frequencies only "
                "play here.")
            return
        picker = StationPickerDialog(
            self, title=title, entries=stations,
            subtitle=(f"Tick the frequencies to add to {radioapps.app_name(key)}. "
                      "Nothing there is removed or changed, and links it "
                      "already has are skipped. A backup of its list is "
                      "saved first."),
            action_label="Send")
        if not picker.exec_():
            return
        chosen = picker.selected()
        if not chosen:
            return
        try:
            added, already, _left, saved = radioapps.send(key, path, chosen)
        except radioapps.AppFileError as exc:
            QMessageBox.warning(self, title, str(exc))
            return
        QMessageBox.information(
            self, title,
            f"Added {added} frequenc{'y' if added == 1 else 'ies'} to "
            f"{radioapps.app_name(key)}."
            + ("" if not already else
               f" {already} {'was' if already == 1 else 'were'} already there.")
            + f"\n\nIts old list was saved as:\n{saved}")

    def export_featured(self):
        from dialogs import StationPickerDialog
        picker = StationPickerDialog(
            self, title="Export as featured", entries=self.lib.stations,
            subtitle=("Tick the stations to write out. Click one to change "
                      "the picture that travels with it — it starts as "
                      "whatever that station already uses."),
            action_label="Export", with_art=True)
        if not picker.exec_():
            return
        chosen = picker.selected()
        if not chosen:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export featured",
            os.path.join(os.path.expanduser("~"), "featured.json"),
            "JSON files (*.json)")
        if not path:
            return
        import json

        # The picture chosen in the picker, not only the one on the
        # station — that is the point of choosing it there.
        entries = []
        for station in chosen:
            art = picker.art_for(station)
            if art and art != (station.get("custom_art_path") or ""):
                station = dict(station, custom_art_path=art,
                               art_source="custom")
            # The words written in the picker travel with it.
            written = {k: v for k, v in picker.card_for(station).items() if v}
            if written:
                station = dict(station, **written)
            entries.append(storage.featured_from_station(station))

        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(entries, handle, indent=2)
        except Exception as exc:                # noqa: BLE001
            QMessageBox.warning(self, "Export", f"Couldn't write it: {exc}")
            return
        QMessageBox.information(
            self, "Export", f"Wrote {len(chosen)} station(s) to {path}")

    # -- system ------------------------------------------------------------

    ZOOM_CHOICES = [(80, "80%"), (90, "90%"), (100, "100%"), (110, "110%"),
                    (125, "125%"), (150, "150%")]

    ACCENT_CHOICES = [("artwork", "From the artwork"),
                      ("desktop", "Match my desktop"),
                      ("fixed", "A color I choose")]

    SLEEP_ACTIONS = [("fade", "Fade to silence"),
                     ("switch", "Switch to another frequency"),
                     ("tag", "Switch to a frequency tagged…")]

    def _build_sleep(self, lay):
        """What happens when the sleep timer runs out."""
        lay.addWidget(_row(
            QLabel("When the sleep timer ends"),
            self._choice("sleep_action", self.SLEEP_ACTIONS, "fade",
                         on_change=self._sleep_action)))

        self.wake_station = QComboBox()
        self.wake_station.setMinimumWidth(220)
        self._fill_wake_stations()
        self.wake_station.currentIndexChanged.connect(self._pick_wake)
        self.wake_row = _row(QLabel("Wake up to"), self.wake_station)
        self.wake_row.setVisible(
            self.lib.settings.get("sleep_action") == "switch")
        lay.addWidget(self.wake_row)

        # A tag rather than one station.
        #
        # "Something from my ambient stations" is how people actually
        # think about what to fall asleep to, and a single named station
        # goes stale — this picks any station carrying the tag, so it
        # stays fresh and survives the station it would have named being
        # removed.
        self.wake_tag = QComboBox()
        self.wake_tag.setMinimumWidth(220)
        self._fill_wake_tags()
        self.wake_tag.currentIndexChanged.connect(self._pick_wake_tag)
        self.wake_tag_row = _row(QLabel("Wake up to anything tagged"),
                                 self.wake_tag)
        self.wake_tag_row.setVisible(
            self.lib.settings.get("sleep_action") == "tag")
        lay.addWidget(self.wake_tag_row)

    def _fill_wake_stations(self):
        self.wake_station.clear()
        chosen = self.lib.settings.get("sleep_wake_station_id", "")

        # Alphabetical, whatever order the library is in.
        #
        # It followed the list's own sort, so with manual ordering the
        # dropdown came out in whatever order the stations had been
        # dragged into — fine for a list you arranged yourself, useless
        # for finding one name among three hundred.
        ordered = sorted(
            self.lib.stations,
            key=lambda s: storage.station_display_name(s).casefold())
        for station in ordered:
            self.wake_station.addItem(
                storage.station_display_name(station), station.get("id"))
        index = self.wake_station.findData(chosen)
        if index >= 0:
            self.wake_station.setCurrentIndex(index)

    def _fill_wake_tags(self):
        """Every tag in use, alphabetically, with how many carry it."""
        self.wake_tag.blockSignals(True)
        self.wake_tag.clear()
        counts = {}
        for station in self.lib.stations:
            for tag in station.get("tags") or []:
                counts[tag] = counts.get(tag, 0) + 1
        for tag in sorted(counts, key=str.casefold):
            self.wake_tag.addItem(f"{tag}  ({counts[tag]})", tag)
        index = self.wake_tag.findData(
            self.lib.settings.get("sleep_wake_tag", ""))
        if index >= 0:
            self.wake_tag.setCurrentIndex(index)
        self.wake_tag.blockSignals(False)

    def _pick_wake_tag(self, _i):
        self._save("sleep_wake_tag", self.wake_tag.currentData())

    def _sleep_action(self, mode):
        self.wake_row.setVisible(mode == "switch")
        self.wake_tag_row.setVisible(mode == "tag")
        if mode == "tag":
            self._fill_wake_tags()

    def _pick_wake(self, _i):
        self._save("sleep_wake_station_id", self.wake_station.currentData())

    IB_CACHE = [(0, "Don't cache"), (256, "256 MB"), (512, "512 MB"),
                (1024, "1 GB"), (4096, "4 GB")]

    def _build_ibroadcast(self):
        """Signed-in state, and what is kept on disk.

        What to play and what quality both belong to the station being
        added, and they are asked there — having them here as well meant
        two places to set the same thing, one of which the add dialog
        ignored.
        """
        from PyQt5.QtWidgets import QPushButton
        import ibroadcast
        import keys as _keys                    # noqa: F401  (import check)

        box, lay = _section(
            "iBroadcast",
            "Your iBroadcast library, for iBroadcast stations.")

        self.ib_status = QLabel("")
        self.ib_status.setProperty("role", "secondary")
        lay.addWidget(self.ib_status)

        lay.addWidget(_row(
            QLabel("Keep played tracks on disk"),
            self._choice("ibroadcast_cache_mb", self.IB_CACHE, 0,
                         on_change=self._ib_cache_changed)))

        self.ib_cache_note = QLabel("")
        self.ib_cache_note.setProperty("role", "secondary")
        self.ib_cache_note.setWordWrap(True)
        lay.addWidget(self.ib_cache_note)
        self._update_ib_cache_note()

        row = QHBoxLayout()
        clear = QPushButton("Empty the cache")
        clear.clicked.connect(self._ib_clear_cache)
        row.addWidget(clear)
        self.ib_out = QPushButton("Sign out")
        self.ib_out.clicked.connect(self._ib_sign_out)
        row.addWidget(self.ib_out)
        row.addStretch(1)
        lay.addLayout(row)
        self.lay.addWidget(box)
        self._refresh_ib()

    def _refresh_ib(self):
        """Sign out is grayed out while nobody is signed in (task 166)."""
        if not hasattr(self, "ib_out"):
            return
        import ibroadcast
        signed_in = bool(ibroadcast.load_tokens().get("refresh_token"))
        self.ib_out.setEnabled(signed_in)
        if signed_in:
            self.ib_status.setText("Signed in.")
        elif self.ib_status.text() != "Signed out.":
            self.ib_status.setText(
                "Not signed in. Add an iBroadcast station to connect.")

    def _ib_cache_dir(self):
        return os.path.join(os.path.dirname(storage.settings_path()),
                            "ibroadcast-cache")

    def _ib_cache_size(self):
        total = 0
        for root, _dirs, files in os.walk(self._ib_cache_dir()):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total

    def _update_ib_cache_note(self):
        used = self._ib_cache_size() / (1024 * 1024)
        limit = int(self.lib.settings.get("ibroadcast_cache_mb", 0))
        if not limit:
            self.ib_cache_note.setText(
                "Nothing is kept: every track is streamed each time.")
            return
        self.ib_cache_note.setText(
            f"Using {used:.0f} MB of {limit} MB. The oldest tracks are "
            f"dropped first once it is full.")

    def _ib_cache_changed(self, _value):
        self._update_ib_cache_note()

    def _ib_clear_cache(self):
        import shutil
        shutil.rmtree(self._ib_cache_dir(), ignore_errors=True)
        self._update_ib_cache_note()

    def _ib_sign_out(self):
        """Forget the tokens, and say so.

        The refresh token is a standing credential, so signing out has to
        actually revoke it rather than only forgetting it locally.
        """
        import ibroadcast
        saved = ibroadcast.load_tokens()
        if saved.get("refresh_token"):
            ibroadcast.revoke(saved["refresh_token"])
        ibroadcast.clear_tokens()
        self.ib_status.setText("Signed out.")
        self.ib_out.setEnabled(False)

    def _build_twitch_chat(self):
        """Signing in to Twitch chat (build 82, task 92e), and in a dev
        build the Twitch app's Client ID that every build signs in with."""
        import devmode
        import twitchauth
        box, lay = _section(
            "Twitch chat",
            "Chat is read only until you sign in. Signing in lets you "
            "write in a Twitch station's chat as yourself.")
        self.chat_status = QLabel("")
        self.chat_status.setProperty("role", "secondary")
        self.chat_status.setWordWrap(True)
        lay.addWidget(self.chat_status)
        self.chat_in = QPushButton("Sign in to chat…")
        self.chat_in.clicked.connect(self.chat_sign_in.emit)
        self.chat_out = QPushButton("Sign out")
        self.chat_out.clicked.connect(self.chat_sign_out.emit)
        lay.addWidget(_row(self.chat_in, self.chat_out))
        if devmode.is_dev():
            from PyQt5.QtWidgets import QLineEdit
            field = QLineEdit(twitchauth.client_id())
            field.setPlaceholderText("Client ID from dev.twitch.tv")
            field.setMinimumWidth(320)

            def saved():
                twitchauth.save_client_id(field.text())
                self.show_chat_login(twitchauth.signed_in_as())
            field.editingFinished.connect(saved)
            lay.addWidget(_row(QLabel("Twitch app Client ID"), field))
            note = QLabel(
                "Dev build only. From dev.twitch.tv > Your Console > "
                "Applications (a Public client). It isn't a secret: the "
                "builds you share carry it, so their users can sign in.")
            note.setProperty("role", "secondary")
            note.setWordWrap(True)
            lay.addWidget(note)
        self.lay.addWidget(box)
        self.show_chat_login(twitchauth.signed_in_as())

    def show_chat_login(self, login):
        """Signed in as `login` ("" = not signed in)."""
        if not hasattr(self, "chat_status"):
            return                      # not built yet: it reads it then
        import twitchauth
        has_app = bool(twitchauth.client_id())
        if login:
            text = f"Signed in as {login}."
        elif has_app:
            text = "Not signed in."
        else:
            import devmode
            text = ("Paste your Twitch app's Client ID below to turn "
                    "signing in on." if devmode.is_dev() else
                    "Signing in isn't set up in this build (no Twitch app "
                    "Client ID).")
        self.chat_status.setText(text)
        self.chat_in.setVisible(not login)
        self.chat_in.setEnabled(has_app)
        self.chat_out.setVisible(bool(login))

    def _build_keys(self):
        """Every shortcut, editable, with a way back to the defaults."""
        from PyQt5.QtWidgets import QKeySequenceEdit, QPushButton
        from PyQt5.QtGui import QKeySequence
        import keys as keymap

        box, lay = _section("Keyboard")
        note = QLabel("Click a shortcut and press the keys you want. "
                      "Clearing one leaves that action without a key.")
        note.setProperty("role", "secondary")
        note.setWordWrap(True)
        lay.addWidget(note)

        self._key_edits = {}
        seen_labels = set()
        for name, _default, label, _group in keymap.ACTIONS:
            edit = QKeySequenceEdit(
                QKeySequence(keymap.binding(self.lib.settings, name)))
            edit.keySequenceChanged.connect(
                lambda seq, n=name: self._key_changed(n, seq))
            self._key_edits[name] = edit
            # The alternates share a label with the action they duplicate,
            # so the second one says so rather than repeating it.
            shown = label if label not in seen_labels else label + " (also)"
            seen_labels.add(label)
            lay.addWidget(_row(QLabel(shown), edit))

        self.key_warning = QLabel("")
        self.key_warning.setProperty("role", "secondary")
        self.key_warning.setWordWrap(True)
        lay.addWidget(self.key_warning)

        reset = QPushButton("Reset all shortcuts to default")
        reset.clicked.connect(self._reset_keys)
        lay.addWidget(reset, 0, Qt.AlignLeft)
        self._check_key_conflicts()
        self.lay.addWidget(box)

    def _key_changed(self, name, sequence):
        import keys as keymap
        keymap.set_binding(self.lib.settings, name,
                           sequence.toString() if sequence else "")
        self.lib.save_settings()
        window = self.window()
        if hasattr(window, "rebind_keys"):
            window.rebind_keys()
        self._check_key_conflicts()

    def _check_key_conflicts(self):
        """Say so rather than silently letting one key win."""
        import keys as keymap
        clashes = keymap.conflicts(self.lib.settings)
        if not clashes:
            self.key_warning.setText("")
            return
        lines = [f"{key} is used by "
                 + " and ".join(sorted(keymap.LABELS.get(a, a)
                                       for a in actions))
                 for key, actions in clashes.items()]
        self.key_warning.setText("Conflicting: " + "; ".join(lines))

    def _reset_keys(self):
        """Every shortcut back to the shipped binding.

        Clearing the stored overrides is what does it: a binding is read
        as "whatever is saved, else the default", so an empty table means
        the defaults everywhere. The fields are refilled from the same
        table so what is on screen matches what is now in force.
        """
        from PyQt5.QtGui import QKeySequence
        import keys as keymap
        self.lib.settings["keys"] = {}
        self.lib.save_settings()
        for name, edit in self._key_edits.items():
            edit.blockSignals(True)
            edit.setKeySequence(QKeySequence(keymap.DEFAULTS.get(name, "")))
            edit.blockSignals(False)
        window = self.window()
        if hasattr(window, "rebind_keys"):
            window.rebind_keys()
        self._check_key_conflicts()

    def _build_look(self):
        box, lay = _section("Look")

        import themes
        lay.addWidget(_row(
            QLabel("Theme"),
            self._choice("theme", themes.names(), themes.DEFAULT,
                         on_change=self._theme_changed)))

        self.accent_pick = QPushButton("Choose color…")
        self.accent_pick.clicked.connect(self._pick_accent)
        lay.addWidget(_row(
            QLabel("Accent color"),
            self._choice("accent_source", self.ACCENT_CHOICES, "artwork",
                         on_change=self._accent_source),
            self.accent_pick))
        self.accent_pick.setVisible(
            self.lib.settings.get("accent_source") == "fixed")

        lay.addWidget(_row(
            QLabel("Interface size"),
            self._choice("ui_zoom", self.ZOOM_CHOICES, 100,
                         on_change=self._zoom_changed),
            QLabel("Ctrl + and Ctrl − change this too")))

        # 12- or 24-hour, for every time the app shows.
        lay.addWidget(self._check(
            "clock_12h", "Show times as 12-hour (1:30 PM)", default=True,
            on_change=self._clock_changed))

        # Clippy's closed tips (task 143): all of them again, from tip 1.
        reset = QPushButton("Reset Clippy's hot tips")
        reset.clicked.connect(self._reset_clippy)
        note = QLabel("Clippy shows every tip again, starting from the first")
        note.setProperty("role", "secondary")
        lay.addWidget(_row(reset, note))

        self.lay.addWidget(box)

    def _reset_clippy(self):
        companion = getattr(self.window(), "companion", None)
        if companion is not None:
            companion.reset()

    def _build_sidebar(self):
        box, lay = _section(
            "Sidebar",
            "Which extras show under Saved Frequencies. Right-click one in "
            "the sidebar to hide it there too.")
        self._extra_boxes = {}
        for key, label in (("winamp", "Winamp"), ("visualizer", "Visualizer"),
                           ("schedule", "Schedule"), ("help", "Help")):
            # Not "box": that is the section widget this layout belongs
            # to, and rebinding it let Python collect the section — taking
            # the layout with it, mid-loop.
            check = QCheckBox(label)
            check.setChecked(key not in
                             set(self.lib.settings.get("hidden_extras") or ()))
            check.toggled.connect(lambda on, k=key: self._toggle_extra(k, on))
            self._extra_boxes[key] = check
            lay.addWidget(check)
        self.lay.addWidget(box)

    def _build_window(self):
        box, lay = _section("Window and tray")
        lay.addWidget(self._check(
            "tray_icon", "Show a tray icon", default=False,
            on_change=self._tray_switched))
        self.tray_close = self._check(
            "minimize_to_tray", "Closing the window minimizes to the tray",
            "Needs \"Show a tray icon\" on. Otherwise closing quits",
            default=False)
        # Grayed out while there is no tray icon (build 89, task 102).
        self.tray_close.setEnabled(bool(self.lib.settings.get(
            "tray_icon", False)))
        lay.addWidget(self.tray_close)
        self.lay.addWidget(box)

    def _tray_switched(self, on):
        self.tray_close.setEnabled(bool(on))
        self.changed.emit("tray_icon")

    def _toggle_extra(self, key, on):
        """Show or hide one of the rail's lower entries."""
        window = self.window()
        if hasattr(window, "show_extra"):
            window.show_extra(key, on)

    def _theme_changed(self, key):
        """Applied at once, so the choice is made by looking at it."""
        window = self.window()
        if hasattr(window, "apply_theme"):
            window.apply_theme(key)

    def _accent_source(self, mode):
        self.accent_pick.setVisible(mode == "fixed")
        window = self.window()
        accent = getattr(window, "accent", None)
        if accent is not None:
            accent.set_source(mode, self.lib.settings.get("accent_fixed"))
        if mode == "fixed" and not self.lib.settings.get("accent_fixed"):
            self._pick_accent()

    def _pick_accent(self):
        from PyQt5.QtWidgets import QColorDialog
        from PyQt5.QtGui import QKeySequence, QColor
        start = QColor(self.lib.settings.get("accent_fixed") or "#d99a2b")
        chosen = QColorDialog.getColor(start, self, "Accent color")
        if not chosen.isValid():
            return
        self._save("accent_fixed", chosen.name())
        window = self.window()
        accent = getattr(window, "accent", None)
        if accent is not None:
            accent.set_source("fixed", chosen.name())

    def _clock_changed(self, on):
        """Switch the clock style and redraw what shows a time."""
        import clock
        clock.set_mode(on)
        window = self.window()
        for name in ("refresh_history", "refresh_schedule"):
            redraw = getattr(window, name, None)
            if callable(redraw):
                redraw()

    def _zoom_changed(self, percent):
        window = self.window()
        if hasattr(window, "set_zoom"):
            window.set_zoom(percent, save=False)

    def _sniffer_name_helper(self):
        """Kept next to open_sniffer so the suggestion logic is findable."""

    def open_sniffer(self):
        """The sniffer runs out of process — it is a whole browser, and
        embedding one in this process is what the helper exists to avoid.
        It hands back a URL, or "" if nothing was picked."""
        import webview
        if not webview.is_available():
            QMessageBox.information(self, "Stream sniffer",
                                    webview.unavailable_message())
            return
        url = webview.sniff_stream()
        if not url:
            return
        known = {(s.get("url") or "").lower() for s in self.lib.stations}
        if url.lower() in known:
            QMessageBox.information(self, "Stream sniffer",
                                    "That stream is already in your list.")
            return
        # Ask what it is called, now.
        #
        # It used to be named after its own URL and you were told to
        # rename it later — so a sniffed station arrived as a line of
        # query string in the list, and later never comes.
        from PyQt5.QtWidgets import QInputDialog
        suggestion = _name_from_url(url)
        name, ok = QInputDialog.getText(
            self, "Stream sniffer", "Name this station:", text=suggestion)
        if not ok:
            return
        name = (name or "").strip() or suggestion

        station = storage._normalize_station({"name": name, "url": url})
        station["sort_index"] = len(self.lib.stations)
        self.lib.stations.append(station)
        self.lib.save_stations()
        self.library_changed.emit()
        QMessageBox.information(self, "Stream sniffer", f"Added {name}.")
