"""
dialogs.py - Small modal dialogs: add custom station, rename station,
per-station options, and the radio-browser.info search window.
"""
import json
import os

from PyQt5.QtCore import Qt, QSize, pyqtSignal, QFileSystemWatcher
from PyQt5.QtGui import QBrush, QColor, QPixmap, QIcon
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QDialogButtonBox,
    QSpinBox,
    QButtonGroup,
    QDoubleSpinBox,
    QRadioButton,
    QSlider,
    QGroupBox,
    QTabWidget,
    QWidget,
    QAbstractItemView,
    QPlainTextEdit,
    QFileDialog,
    QInputDialog,
)

import re

import radiobrowser
import storage
from jobs import Job
import workers
import youtube
import thumbs


class AddStationDialog(QDialog):
    """Add a station from a hand-typed name + stream URL."""

    def __init__(self, parent=None, name="", url=""):
        super().__init__(parent)
        self.setWindowTitle("Add custom station")
        self.setMinimumWidth(420)

        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("e.g. My Favorite Station")
        self.url_edit = QLineEdit(url)
        self.url_edit.setPlaceholderText("https://example.com/stream.mp3")

        form = QFormLayout()
        form.addRow("Name:", self.name_edit)
        form.addRow("Stream URL:", self.url_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self.url_edit.text().strip():
            QMessageBox.warning(self, "Missing URL", "Please enter a stream URL.")
            return
        if not self.name_edit.text().strip():
            self.name_edit.setText(self.url_edit.text().strip())
        self.accept()

    def values(self):
        return self.name_edit.text().strip(), self.url_edit.text().strip()


class EditStationDialog(QDialog):
    """Edit an existing station: its display name and its stream URL.

    The URL is editable because stations move - a mount point gets
    renamed, a station switches host, or the directory entry was simply
    wrong. Previously the only remedy was to delete the station and add
    it again, which threw away its tags, level trim, play count and
    everything else attached to it.
    """

    def __init__(self, parent=None, station=None, other_urls=None):
        super().__init__(parent)
        station = station or {}
        self._original_url = station.get("url", "")
        self._other_urls = {u.strip().lower() for u in (other_urls or [])}

        self.setWindowTitle(f"Edit {storage.station_display_name(station)}")
        self.setMinimumWidth(520)

        self.name_edit = QLineEdit(storage.station_display_name(station))
        self.name_edit.setPlaceholderText("What this station is called here")
        self.name_edit.selectAll()

        self.url_edit = QLineEdit(self._original_url)
        self.url_edit.setPlaceholderText("https://example.com/stream.mp3")
        self.url_edit.setToolTip(
            "The stream URL. Changing it keeps everything else about "
            "this station - tags, options, play count, favorites."
        )

        form = QFormLayout()
        form.addRow("Name:", self.name_edit)
        form.addRow("Stream URL:", self.url_edit)

        original = QLabel("")
        if self._original_url:
            original.setText(f"Currently: {self._original_url}")
            original.setWordWrap(True)
            original.setStyleSheet("color: #888;")

        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #d08a3a;")
        self.warning.hide()
        self.url_edit.textChanged.connect(self._check_url)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        if self._original_url:
            layout.addWidget(original)
        layout.addWidget(self.warning)
        layout.addWidget(buttons)

    def _check_url(self, text):
        """Warn as they type, rather than only on OK."""
        url = text.strip()
        if url and not re.match(r"^[a-z][a-z0-9+.\-]*://", url, re.IGNORECASE):
            self.warning.setText(
                "That doesn't look like a URL - it usually starts with "
                "http:// or https://."
            )
            self.warning.show()
        elif url.lower() in self._other_urls:
            self.warning.setText(
                "Another station in your list already uses this URL."
            )
            self.warning.show()
        else:
            self.warning.hide()

    def _on_accept(self):
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL",
                                "A station needs a stream URL.")
            return
        if url.lower() in self._other_urls:
            confirm = QMessageBox.question(
                self, "Duplicate URL",
                "Another station already uses this URL. Save anyway?"
            )
            if confirm != QMessageBox.Yes:
                return
        self.accept()

    def values(self):
        return self.name_edit.text().strip(), self.url_edit.text().strip()

    def url_changed(self):
        return self.url_edit.text().strip() != self._original_url


class _SearchWorker(Job):
    results_ready = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, query, order=None, country="", language="",
                 near=None, mode="search", offset=0, limit=40):
        super().__init__()
        self.offset = offset
        self.limit = limit
        self.query = query
        self.order = order or radiobrowser.DEFAULT_SORT
        self.country = country
        self.language = language
        self.near = near
        self.mode = mode

    def work(self):
        try:
            if self.mode == "new":
                results = radiobrowser.new_stations(
                    country=self.country, language=self.language
                )
            else:
                results = radiobrowser.search_stations(
                    self.query, order=self.order, country=self.country,
                    language=self.language, near=self.near,
                    offset=self.offset, limit=self.limit,
                )
            self.send(self.results_ready, results)
        except radiobrowser.RadioBrowserError as exc:
            self.send(self.error, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.send(self.error, str(exc))


class _FeaturedWorker(Job):
    """Today's featured stations, off the GUI thread."""

    ready = pyqtSignal(list)

    def __init__(self, cache_dir, parent=None):
        super().__init__(parent)
        self._cache_dir = cache_dir

    def work(self):
        try:
            self.send(self.ready,
                      radiobrowser.featured_stations(self._cache_dir))
        except Exception:  # noqa: BLE001
            self.send(self.ready, [])


class _FilterListWorker(Job):
    """Fetch the country and language lists once, off the GUI thread."""

    ready = pyqtSignal(list, list)

    def __init__(self, parent=None):
        super().__init__(parent)

    def work(self):
        try:
            self.send(self.ready, radiobrowser.countries(),
                      radiobrowser.languages())
        except Exception:  # noqa: BLE001
            self.send(self.ready, [], [])


# How many results a page holds. Radio Browser will return far more in
# one go, but a list of hundreds is not browsable and the merge of the
# name and tag searches gets slower the more it has to de-duplicate.
PAGE_SIZE = 40


class SearchDialog(QDialog):
    """Search radio-browser.info and let the user add results."""

    # name, url, uuid, favicon, homepage
    station_added = pyqtSignal(str, str, str, str, str)

    # A featured entry, carrying whatever settings its curator tuned.
    # Separate from station_added because there is a whole station in
    # here, not four strings.
    featured_added = pyqtSignal(dict)

    def __init__(self, parent=None, near=None):
        super().__init__(parent)
        self._near = near
        self.setWindowTitle("Search radio-browser.info")
        self.setMinimumSize(560, 480)
        self._worker = None
        # Searches that are still running but whose results we no longer
        # want. Only kept to be counted: a jobs.Job keeps itself alive.
        self._retired_workers = []
        self._results = []

        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Search by station name, e.g. jazz, BBC, lofi...")
        self.query_edit.returnPressed.connect(self._do_search)

        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._do_search)

        # The search row. This used to hold a Map button too; removing
        # it took the layout with it.
        top = QHBoxLayout()
        top.addWidget(self.query_edit, stretch=1)
        top.addWidget(search_btn)

        # Sort and filter.
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Sort"))
        self.sort_combo = QComboBox()
        for value, label in radiobrowser.SORT_MODES:
            self.sort_combo.addItem(label, value)
        self.sort_combo.currentIndexChanged.connect(self._do_search)
        filters.addWidget(self.sort_combo)

        filters.addWidget(QLabel("Language"))
        self.language_combo = QComboBox()
        self.language_combo.addItem("Any", "")
        self.language_combo.currentIndexChanged.connect(self._do_search)
        filters.addWidget(self.language_combo, stretch=1)

        self.near_check = QCheckBox("Near me")
        self.near_check.setToolTip(
            "Sort results by distance from the home location in Options"
        )
        self.near_check.setEnabled(bool(near))
        if not near:
            self.near_check.setToolTip(
                "Set a home location under Options -> "
                "location to sort by distance"
            )
        self.near_check.toggled.connect(self._do_search)
        filters.addWidget(self.near_check)

        self._page = 0
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888;")

        # Paging, so a search is not capped at one screenful.
        self.pager = QWidget()
        pager_row = QHBoxLayout(self.pager)
        pager_row.setContentsMargins(0, 0, 0, 0)
        self.prev_button = QPushButton("< Previous")
        self.prev_button.clicked.connect(lambda: self._turn_page(-1))
        self.next_button = QPushButton("More >")
        self.next_button.clicked.connect(lambda: self._turn_page(1))
        self.page_label = QLabel("")
        self.page_label.setStyleSheet("color: #888;")
        pager_row.addWidget(self.prev_button)
        pager_row.addWidget(self.page_label)
        pager_row.addStretch(1)
        pager_row.addWidget(self.next_button)
        self.pager.setVisible(False)

        self.results_list = QListWidget()
        self.results_list.itemDoubleClicked.connect(self._add_selected)

        add_btn = QPushButton("Add selected station")
        add_btn.clicked.connect(self._add_selected)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        bottom = QHBoxLayout()
        bottom.addWidget(add_btn)
        bottom.addStretch(1)
        bottom.addWidget(close_btn)

        # Featured stations: a small, permanent panel at the bottom.
        #
        # Fixed height on purpose - it is a suggestion, not the point of
        # the window, and it must never grow enough to push the search
        # results out of the way.
        featured_box = QGroupBox("Featured stations (changes daily!)")
        featured_layout = QVBoxLayout(featured_box)
        featured_layout.setContentsMargins(6, 6, 6, 6)
        self.featured_list = QListWidget()
        self.featured_list.setMaximumHeight(96)
        self.featured_list.itemDoubleClicked.connect(self._add_featured)
        featured_layout.addWidget(self.featured_list)
        self.featured_note = QLabel("Loading...")
        self.featured_note.setWordWrap(True)
        self.featured_note.setStyleSheet("color: #888;")
        featured_layout.addWidget(self.featured_note)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(filters)
        layout.addWidget(self.status_label)
        layout.addWidget(self.pager)
        layout.addWidget(self.results_list, stretch=1)
        layout.addWidget(featured_box)
        layout.addLayout(bottom)

        self._featured_worker = _FeaturedWorker(self._featured_cache(), self)
        self._featured_worker.ready.connect(self._on_featured)
        self._featured_worker.start()

        # A jobs.Job keeps itself alive until it answers, so closing the
        # dialog mid-lookup is safe.
        self._filter_worker = _FilterListWorker(self)
        self._filter_worker.ready.connect(self._on_filters)
        self._filter_worker.start()

    @staticmethod
    def _featured_cache():
        import storage
        return os.path.join(
            os.path.dirname(storage.settings_path()), "featured"
        )

    def _on_featured(self, stations):
        self.featured_list.clear()
        if not stations:
            self.featured_note.setText(radiobrowser.FEATURED_EMPTY)
            return
        self.featured_note.setText("")
        for entry in stations:
            country = f" ({entry['country']})" if entry.get("country") else ""
            item = QListWidgetItem(f"{entry['name']}{country}")
            item.setData(Qt.UserRole, entry)
            self.featured_list.addItem(item)

    def _add_featured(self, item):
        entry = item.data(Qt.UserRole)
        if not entry:
            return
        # The whole entry, so the curator's tags, EQ and artwork come
        # with it rather than just a name and a URL.
        self.featured_added.emit(entry)
        self.status_label.setText(f"Added {entry['name']}.")

    def _open_map(self):
        """Open radio-browser's map in a browser.

        Not embedded: it is a Leaflet page with thousands of markers,
        and a worse copy of it inside the app would help nobody.
        """
        import webbrowser
        webbrowser.open(radiobrowser.MAP_URL)

    def _on_filters(self, _countries, languages):
        for name in languages:
            self.language_combo.addItem(name.title(), name)

    def _do_search(self, keep_page=False):
        """Run the search. A new query starts again at page one."""
        if not keep_page:
            self._page = 0
        query = self.query_edit.text().strip()
        # An empty box is fine now: with a country or language chosen it
        # means "browse everything that matches", which is how you find
        # stations you couldn't have named.
        if not query and not self.language_combo.currentData():
            return
        self.status_label.setText("Searching...")
        self.results_list.clear()

        # If the user hits Search again before the previous one comes
        # back, stop listening to the old thread but keep it alive until
        # it finishes on its own.
        if self._worker is not None:
            try:
                self._worker.results_ready.disconnect()
                self._worker.error.disconnect()
            except (TypeError, RuntimeError):
                pass  # already disconnected, or finished and deleted
            if self._worker.isRunning():
                self._retired_workers.append(self._worker)
                self._worker.finished.connect(self._drop_finished_workers)

        self._worker = _SearchWorker(
            query,
            order=self.sort_combo.currentData(),
            language=self.language_combo.currentData() or "",
            near=self._near if self.near_check.isChecked() else None,
            offset=self._page * PAGE_SIZE,
            limit=PAGE_SIZE,
        )
        self._worker.results_ready.connect(self._on_results)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _turn_page(self, direction):
        """Next or previous page of the same search."""
        page = max(0, self._page + direction)
        if page == self._page:
            return
        self._page = page
        self._do_search(keep_page=True)

    def _update_pager(self, count):
        """Show the pager once there is more than one page to show.

        "More" is enabled on a full page rather than on a known total:
        the API reports no count, so a full page is the only evidence
        that another might exist. A last page exactly PAGE_SIZE long
        will offer a More that turns up empty, which is a smaller sin
        than hiding results that are there.
        """
        self.pager.setVisible(self._page > 0 or count >= PAGE_SIZE)
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(count >= PAGE_SIZE)
        self.page_label.setText(f"Page {self._page + 1}")

    def _on_results(self, results):
        self._results = results
        self.results_list.clear()
        self._update_pager(len(results))
        if not results:
            self.status_label.setText(
                "No more stations." if self._page
                else "No stations found."
            )
            return
        first = self._page * PAGE_SIZE + 1
        self.status_label.setText(
            f"{first}-{first + len(results) - 1} - double-click to add"
        )
        for r in results:
            bitrate = f" - {r['bitrate']}kbps" if r.get("bitrate") else ""
            country = f" ({r['country']})" if r.get("country") else ""
            distance = ""
            if r.get("distance_km") is not None:
                distance = f" - {int(r['distance_km'])} km"
            label = f"{r['name']}{country}{bitrate}{distance}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, r)
            self.results_list.addItem(item)

    def _drop_finished_workers(self):
        self._retired_workers = [
            w for w in self._retired_workers if w.isRunning()
        ]

    def _on_error(self, message):
        self.status_label.setText(f"Search failed: {message}")

    def closeEvent(self, event):
        # The lookups here used to be QThreads, and closing the dialog
        # while one ran aborted the app, so this waited up to 3 s each.
        # They are jobs.Job daemon threads now (build 46): nothing owns
        # them but themselves, so the dialog can simply go.
        super().closeEvent(event)

    def _add_selected(self):
        item = self.results_list.currentItem()
        if not item:
            return
        r = item.data(Qt.UserRole)
        self.station_added.emit(
            r["name"], r["url"], r.get("uuid", ""),
            r.get("favicon", ""), r.get("homepage", ""),
        )
        self.status_label.setText(f"Added \"{r['name']}\".")


def _art_log(text):
    """Every step of choosing artwork, to the log.

    This path has three ways in — a typed path, a downloaded URL, an
    inline data URI — and a silent failure in any of them looks the
    same from outside: nothing happens.
    """
    try:
        import crashlog
        crashlog.write("[art] " + text)
    except Exception:                           # noqa: BLE001
        pass


def _write_station_art(data, url, station_id):
    """Write bytes a worker already fetched, and return the path.

    The download that used to sit in front of this is gone: it ran on
    the GUI thread and froze the window while a slow host thought about
    it. Fetching is a worker's job; this is only the part that has to
    happen where the bytes land.
    """
    import re

    if not data:
        return ""

    # Judged by what it starts with, not by how big it is.
    #
    # A size floor looked like a way to catch spacer GIFs and instead
    # threw away real covers: a flat 300px PNG compresses to under a
    # kilobyte. Magic bytes say whether it is an image; the format list
    # includes WebP, which Qt here cannot decode but the app can still
    # keep and hand to something that can.
    _art_log(f"writing {len(data)} bytes from {url[:70]}")
    raw = bytes(data[:16])
    signatures = (
        b"\x89PNG",                     # PNG
        b"\xff\xd8\xff",               # JPEG
        b"GIF87a", b"GIF89a",           # GIF
        b"BM",                          # BMP
        b"\x00\x00\x01\x00",          # ICO
    )
    looks_like_image = raw.startswith(signatures) or (
        raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")
    if not looks_like_image:
        _art_log(f"rejected: first bytes {raw[:8]!r} are not an image")
        return ""

    try:
        import storage
        folder = os.path.join(os.path.dirname(storage.settings_path()),
                              "station-art")
        os.makedirs(folder, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_-]", "", str(station_id)) or "art"
        suffix = ".png" if url.lower().split("?")[0].endswith(".png") \
            else ".jpg"
        # Re-encoded as PNG whenever Qt can read it.
        #
        # A proxy hands back whatever the source served — often WebP —
        # and naming it .jpg does not change that. If the running Qt has
        # no WebP plugin, the file is on disk, the station points at it,
        # and every attempt to draw it silently fails: art that saves
        # correctly and never appears. Converting once, here, means the
        # file is always something the app can draw.
        from PyQt5.QtGui import QImage
        probe = QImage()
        if probe.loadFromData(data):
            path = os.path.join(folder, safe + ".png")
            if probe.save(path, "PNG"):
                _art_log(f"wrote {path} (converted from {len(data)} bytes)")
                return path

        path = os.path.join(folder, safe + suffix)
        with open(path, "wb") as handle:
            handle.write(data)
        _art_log(f"wrote {path} — Qt could not decode it, saved as-is")
        return path
    except Exception as exc:                    # noqa: BLE001
        _art_log(f"could not write it: {exc!r}")
        return ""


# What a cleared name field means: fall back to the station's own name.
station_name_unset = None


def _rounded_art(pixmap, side):
    """A square picture with rounded corners, filled edge to edge like the
    tiles in Saved Frequencies."""
    from PyQt5.QtCore import QRectF
    from PyQt5.QtGui import QPainter, QPainterPath
    out = QPixmap(side, side)
    out.fill(Qt.transparent)
    if pixmap is None or pixmap.isNull():
        return out
    scaled = pixmap.scaled(side, side, Qt.KeepAspectRatioByExpanding,
                           Qt.SmoothTransformation)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, side, side), 8, 8)
    p.setClipPath(path)
    p.drawPixmap((side - scaled.width()) // 2,
                 (side - scaled.height()) // 2, scaled)
    p.end()
    return out


class StationOptionsDialog(QDialog):
    """Everything that can differ from one station to the next.

    Grouped into tabs because there are now enough of them that a single
    column would be a wall of checkboxes.
    """

    def __init__(self, parent=None, station=None, custom_stations=None,
                 eq_advanced=False, other_stations=None,
                 cover_stations=None, talk=None, art=None):
        super().__init__(parent)
        # The talk-station switch (task 49): True/False in a dev build,
        # None in a user build, where it isn't shown at all.
        self._talk = talk
        # Kept so later actions - searching for artwork, say - can use
        # the station's name without digging it back out of a widget.
        self._station = station or {}
        station = station or {}
        name = (station.get("custom_name") or station.get("name")
                or "station")
        self.setWindowTitle(f"Station settings — {name}")
        self.setMinimumWidth(620)

        # Dressed like the rest of the app rather than like a Qt dialog.
        #
        # It kept the platform's own tab bar and button box while every
        # other surface had been restyled, so opening it was a step out
        # of the app and back into the toolkit. The heading and the
        # rounded card come from the same tokens the Settings page uses.
        from theme import T as _T
        self.setStyleSheet(
            f"QDialog {{ background: {_T['bg0']}; }}"
            f"QTabWidget::pane {{ border: 1px solid {_T['line']};"
            f" border-radius: 10px; background: {_T['bg1']};"
            f" top: -1px; }}"
            f"QTabBar::tab {{ background: transparent; color: {_T['text2']};"
            # 5 px top and bottom, was 8: a little too tall (task 42).
            f" padding: 5px 18px; margin-right: 4px;"
            f" border-top-left-radius: 8px; border-top-right-radius: 8px; }}"
            f"QTabBar::tab:selected {{ background: {_T['bg1']};"
            f" color: {_T['accent']};"
            f" border: 1px solid {_T['line']}; border-bottom: none; }}"
            f"QTabBar::tab:hover:!selected {{ color: {_T['text']}; }}")

        heading = QLabel(name)
        heading.setProperty("role", "display")
        heading.setWordWrap(True)

        # The station's picture beside its name (task 67): the same one
        # Saved Frequencies shows, handed over by the window as `art`
        # (its letter tile when there is no picture). Choosing, finding
        # or cropping artwork on the Display tab redraws it.
        self._art_initial = art if art is not None and not art.isNull() \
            else None
        self.art_preview = QLabel()
        self.art_preview.setFixedSize(self.ART_SIDE, self.ART_SIDE)
        self.art_preview.setVisible(self._art_initial is not None)
        if self._art_initial is not None:
            self.art_preview.setPixmap(
                _rounded_art(self._art_initial, self.ART_SIDE))
        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(self.art_preview, 0, Qt.AlignVCenter)
        top.addWidget(heading, 1, Qt.AlignVCenter)

        tabs = QTabWidget()
        tabs.addTab(self._build_sound_tab(
                station, custom_stations, eq_advanced=eq_advanced,
                other_stations=other_stations,
                cover_stations=cover_stations,
            ), "Sound")
        tabs.addTab(self._build_display_tab(station), "Display")
        tabs.addTab(self._build_identify_tab(station), "Identify")

        buttons = QDialogButtonBox(QDialogButtonBox.Save
                                   | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Save")
        # Return belongs to whichever field has focus, as in the tags
        # dialog: a default button swallows it before the field sees it.
        for button in buttons.buttons():
            button.setAutoDefault(False)
            button.setDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(top)
        layout.addWidget(tabs)
        layout.addWidget(buttons)
        # A path typed or chosen shows straight away.
        self.custom_art.textChanged.connect(self._refresh_art_preview)

    ART_SIDE = 72       # px, the picture beside the station's name

    # -- tabs ---------------------------------------------------------
    def _build_sound_tab(self, station, custom_stations=None,
                         eq_advanced=False, other_stations=None,
                         cover_stations=None):
        page = QWidget()
        layout = QVBoxLayout(page)

        # Persistent level trim
        gain_box = QGroupBox("Level trim for this station")
        gain_layout = QVBoxLayout(gain_box)
        blurb = QLabel(
            "Permanently boost a station that's always too quiet, or duck "
            "one that's always too loud. Saved with the station and "
            "applied every time it plays."
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet("color: #888;")
        gain_layout.addWidget(blurb)

        row = QHBoxLayout()
        self.gain_slider = QSlider(Qt.Horizontal)
        self.gain_slider.setRange(-200, 200)          # tenths of a dB
        self.gain_slider.setValue(int(float(station.get("gain_db", 0.0)) * 10))
        self.gain_value = QLabel()
        self.gain_value.setFixedWidth(70)
        self.gain_slider.valueChanged.connect(self._update_gain_label)
        reset = QPushButton("Reset")
        reset.clicked.connect(lambda: self.gain_slider.setValue(0))
        row.addWidget(QLabel("Duck"))
        row.addWidget(self.gain_slider, stretch=1)
        row.addWidget(QLabel("Boost"))
        row.addWidget(self.gain_value)
        row.addWidget(reset)
        gain_layout.addLayout(row)
        self._update_gain_label(self.gain_slider.value())
        layout.addWidget(gain_box)

        # The EQ is per-station: a bass-shy talk stream and a boomy
        # dance stream want opposite corrections, and one global set of
        # sliders could only ever suit one of them.
        eq_box = QGroupBox("Equalizer for this station")
        eq_layout = QVBoxLayout(eq_box)
        eq_blurb = QLabel(
            "Saved with the station and applied every time it plays. "
            "There's a shortcut to these three next to the transport "
            "controls as well."
        )
        eq_blurb.setWordWrap(True)
        eq_blurb.setStyleSheet("color: #888;")
        eq_layout.addWidget(eq_blurb)

        eq_form = QFormLayout()
        eq_form.setLabelAlignment(Qt.AlignRight)
        self.eq_sliders = {}
        self.eq_values = {}
        for key, label in (("eq_bass", "Bass"), ("eq_mid", "Mid"),
                           ("eq_treble", "Treble")):
            row = QHBoxLayout()
            slider = QSlider(Qt.Horizontal)
            slider.setRange(-12, 12)
            slider.setValue(int(station.get(key, 0) or 0))
            value = QLabel()
            value.setFixedWidth(34)
            value.setText(f"{slider.value():+d} dB")
            slider.valueChanged.connect(
                lambda v, lbl=value: lbl.setText(f"{v:+d} dB")
            )
            row.addWidget(slider, stretch=1)
            row.addWidget(value)
            eq_form.addRow(label, row)
            self.eq_sliders[key] = slider
            self.eq_values[key] = value
        eq_layout.addLayout(eq_form)

        # Advanced: ten bands at the ISO center frequencies a graphic
        # equaliser has used since the 1970s. Vertical sliders in a row,
        # because that is the shape everyone already knows how to read.
        # Which EQ is shown is a global choice, made in the player's EQ
        # control; this dialog just edits whichever one is in use. The
        # values on both sides stay with the station, so switching modes
        # never loses anything.
        advanced = bool(eq_advanced)
        # Kept so values() still reports it, but it is not shown here: the
        # choice applies to every station, and a disabled checkbox in a
        # per-station dialog only raises the question of where the real one
        # is. It lives in Settings > Sound.
        self.eq_advanced_check = QCheckBox("Advanced (10 bands)")
        self.eq_advanced_check.setChecked(advanced)
        self.eq_advanced_check.setEnabled(False)
        self.eq_advanced_check.setVisible(False)
        eq_layout.addWidget(self.eq_advanced_check)

        self.eq_band_panel = QWidget()
        bands_row = QHBoxLayout(self.eq_band_panel)
        bands_row.setContentsMargins(0, 4, 0, 0)
        self.eq_band_sliders = []
        current = storage.normalize_eq_bands(station.get("eq_bands"))
        for hz, gain in zip(storage.EQ_BAND_FREQUENCIES, current):
            column = QVBoxLayout()
            readout = QLabel(f"{gain:+d}")
            readout.setAlignment(Qt.AlignHCenter)
            readout.setStyleSheet("color: #888;")
            slider = QSlider(Qt.Vertical)
            slider.setRange(-12, 12)
            slider.setValue(gain)
            slider.setMinimumHeight(90)
            slider.valueChanged.connect(
                lambda v, lbl=readout: lbl.setText(f"{v:+d}")
            )
            caption = QLabel(storage.eq_band_label(hz))
            caption.setAlignment(Qt.AlignHCenter)
            column.addWidget(readout)
            column.addWidget(slider, alignment=Qt.AlignHCenter)
            column.addWidget(caption)
            bands_row.addLayout(column)
            self.eq_band_sliders.append(slider)
        eq_layout.addWidget(self.eq_band_panel)

        def _sync_eq_mode(advanced):
            self.eq_band_panel.setVisible(advanced)
            for slider in self.eq_sliders.values():
                slider.setEnabled(not advanced)
        self.eq_advanced_check.toggled.connect(_sync_eq_mode)
        _sync_eq_mode(self.eq_advanced_check.isChecked())

        flat = QPushButton("Reset EQ")
        flat.clicked.connect(self._flatten_eq)
        eq_layout.addWidget(flat)
        layout.addWidget(eq_box)

        # Ad override.
        ad_box = QGroupBox("Cover the opening ad")
        ad_layout = QVBoxLayout(ad_box)
        ad_blurb = QLabel(
            "Many stations inject an ad the moment you connect. It is "
            "part of the stream, so it can't be skipped - but it can be "
            "covered. Another of your frequencies plays over it while the "
            "stream runs on silently underneath, then a burst of static "
            "hands over to the station, so it sounds like the dial "
            "settling. Static fills in until the cover can be heard.\n\n"
            "Set the length to however long that station's ad usually "
            "runs. Too short and you'll catch the tail of it; too long and "
            "you'll miss the first few seconds of the show."
        )
        ad_blurb.setWordWrap(True)
        ad_blurb.setStyleSheet("color: #888;")
        ad_layout.addWidget(ad_blurb)

        self.ad_check = QCheckBox("Cover the ad with another frequency")
        self.ad_check.setChecked(storage.ad_cover_on(station))
        ad_layout.addWidget(self.ad_check)

        ad_row = QHBoxLayout()
        ad_row.addWidget(QLabel("Ad break lasts"))
        self.ad_spin = QDoubleSpinBox()
        self.ad_spin.setRange(1.0, 120.0)
        self.ad_spin.setSingleStep(1.0)
        self.ad_spin.setDecimals(0)
        self.ad_spin.setSuffix(" seconds")
        self.ad_spin.setValue(storage.ad_cover_seconds(station) or 15.0)
        ad_row.addWidget(self.ad_spin, stretch=1)
        ad_layout.addLayout(ad_row)

        # What covers the ad (build 64, the user's two choices): any
        # saved frequency without ads, or one picked here. Only ones
        # that play at once can cover: a normal radio station or a
        # music folder (cover_stations, from mainwindow).
        if cover_stations is None:
            cover_stations = [
                s for s in list(custom_stations or [])
                + list(other_stations or [])]
        covers = [c for c in cover_stations
                  if c.get("id") != station.get("id")]

        def _cover_name(candidate):
            return (candidate.get("custom_name") or candidate.get("name")
                    or candidate.get("url") or "?")
        # A to Z, ignoring capitals, every time the dialog opens (build
        # 71, task 71: it was in saved order).
        covers.sort(key=lambda c: _cover_name(c).strip().casefold())

        kind_row = QHBoxLayout()
        kind_row.addWidget(QLabel("Cover it with"))
        self.ad_kind = QComboBox()
        self.ad_kind.addItem("Any frequency without ads", "any")
        self.ad_kind.addItem("Choose frequency…", "chosen")
        kind_row.addWidget(self.ad_kind, stretch=1)
        ad_layout.addLayout(kind_row)

        pick_row = QHBoxLayout()
        self.ad_source_label = QLabel("Frequency")
        pick_row.addWidget(self.ad_source_label)
        self.ad_source = QComboBox()
        for candidate in covers:
            name = _cover_name(candidate)
            if candidate.get("kind") == "local":
                name += "  (music folder)"
            elif candidate.get("ad_override"):
                name += "  (has its own ad)"
            self.ad_source.addItem(name, candidate.get("id", ""))
        if not covers:
            self.ad_source.addItem("No saved frequency can cover an ad", "")
        pick_row.addWidget(self.ad_source, stretch=1)
        ad_layout.addLayout(pick_row)

        # What was saved, including the choices from before build 64
        # ("music" / "stream", or a typed stream address).
        saved_kind = station.get("ad_filler_kind") or "any"
        saved_id = station.get("ad_filler_station_id") or ""
        saved_url = (station.get("ad_filler_url") or "").strip()
        if not saved_id and saved_url:
            for candidate in covers:
                if candidate.get("url") == saved_url:
                    saved_id = candidate.get("id", "")
                    break
        index = self.ad_source.findData(saved_id) if saved_id else -1
        if saved_kind != "any" and index >= 0:
            self.ad_kind.setCurrentIndex(self.ad_kind.findData("chosen"))
            self.ad_source.setCurrentIndex(index)
        else:
            self.ad_kind.setCurrentIndex(self.ad_kind.findData("any"))

        def _sync_ad_kind():
            chosen = self.ad_kind.currentData() == "chosen"
            self.ad_source_label.setVisible(chosen)
            self.ad_source.setVisible(chosen)
            on = self.ad_check.isChecked()
            self.ad_kind.setEnabled(on)
            self.ad_source.setEnabled(on and bool(covers))
        self.ad_kind.currentIndexChanged.connect(lambda _i: _sync_ad_kind())
        self.ad_check.toggled.connect(lambda _on: _sync_ad_kind())
        _sync_ad_kind()

        if not covers:
            warning = QLabel(
                "None of your saved frequencies can cover an ad yet: it "
                "takes a normal radio station or a music folder (Twitch, "
                "YouTube and iBroadcast can't start fast enough). Until "
                "then the static covers the ad."
            )
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #c08a5a;")
            ad_layout.addWidget(warning)

        self.ad_spin.setEnabled(self.ad_check.isChecked())
        self.ad_check.toggled.connect(self.ad_spin.setEnabled)
        layout.addWidget(ad_box)

        # Pre-buffering, grounded in what this station has actually done.
        buffer_box = QGroupBox("Pre-buffering")
        buffer_layout = QVBoxLayout(buffer_box)
        worst = float(station.get("connect_worst", 0.0) or 0.0)
        blurb = QLabel(
            "Read further ahead before starting. Slower to begin, but it "
            "rides out the drop-outs that make some streams unlistenable."
            + (f"\n\nThe longest this station has taken to start is "
               f"{worst:.1f}s." if worst > 0 else
               "\n\nOnce this station has played, its slowest start will be "
               "shown here as a guide.")
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet("color: #888;")
        buffer_layout.addWidget(blurb)

        self.buffer_check = QCheckBox("Pre-buffer this station")
        self.buffer_check.setChecked(bool(station.get("buffer_enabled")))
        buffer_layout.addWidget(self.buffer_check)

        buffer_row = QHBoxLayout()
        buffer_row.addWidget(QLabel("Seconds"))
        self.buffer_spin = QDoubleSpinBox()
        self.buffer_spin.setRange(0.0, 60.0)
        self.buffer_spin.setSingleStep(0.5)
        self.buffer_spin.setDecimals(1)
        seconds = float(station.get("buffer_seconds") or 0.0)
        # Default to the measured worst case rather than an arbitrary
        # number - it is the value the user would have chosen anyway.
        self.buffer_spin.setValue(seconds if seconds > 0 else worst)
        self.buffer_spin.setSpecialValueText("auto (use the measured worst)")
        buffer_row.addWidget(self.buffer_spin, stretch=1)
        buffer_layout.addLayout(buffer_row)

        self.buffer_spin.setEnabled(self.buffer_check.isChecked())
        self.buffer_check.toggled.connect(self.buffer_spin.setEnabled)
        layout.addWidget(buffer_box)


        self.radio_check = QCheckBox("Radio quality")
        self.radio_check.setChecked(bool(station.get("radio_quality")))
        self.radio_check.setToolTip(
            "Band-limit to roughly 250 Hz - 6.5 kHz, lift the presence "
            "band, compress hard and add a little saturation: the sound "
            "of a broadcast chain rather than a clean file."
        )
        layout.addWidget(self.radio_check)

        layout.addStretch(1)
        return page

    def _update_gain_label(self, value):
        self.gain_value.setText(f"{value / 10:+.1f} dB")

    def _flatten_eq(self):
        for slider in getattr(self, "eq_band_sliders", []):
            slider.setValue(0)
        for slider in self.eq_sliders.values():
            slider.setValue(0)

    def _build_display_tab(self, station):
        page = QWidget()
        layout = QVBoxLayout(page)

        # Name and address, here rather than in a dialog of their own.
        #
        # "Edit…" opened a second window with two fields in it, next to a
        # menu entry called "Options…" that had everything else. Two
        # places to change one station is one too many.
        ident = QFormLayout()
        self.name_field = QLineEdit(station.get("custom_name")
                                    or station.get("name") or "")
        self.name_field.setPlaceholderText("What to call this station")
        ident.addRow("Name:", self.name_field)

        self.url_field = QLineEdit(station.get("url") or "")
        self.url_field.setPlaceholderText("Stream address")
        ident.addRow("Stream URL:", self.url_field)
        layout.addLayout(ident)

        note = QLabel(
            "Pick where the artwork and the song text come from. The "
            "automatic modes try Shazam first, then fall back - either "
            "to your own custom text, or to whatever the stream itself "
            "reports, whichever you'd rather see when nothing has been "
            "identified yet."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        layout.addWidget(note)

        form = QFormLayout()

        self.art_source = QComboBox()
        for label, value in (("Automatic (Shazam - custom - stream)", "auto"),
                             ("Custom image", "custom"),
                             ("Shazam cover art", "shazam"),
                             ("Station logo from the stream", "stream")):
            self.art_source.addItem(label, value)
        self._select(self.art_source, station.get("art_source", "auto"))
        form.addRow("Artwork:", self.art_source)

        art_row = QHBoxLayout()
        self.custom_art = QLineEdit(station.get("custom_art_path", ""))
        # What this dialog was handed, on the way in.
        #
        # "Blank when I reopen it" and "the station has the path" cannot
        # both be true of the same dictionary, so one of them is not the
        # dictionary I think it is. This says which.
        _art_log("dialog opened with art=%r source=%r id=%r"
                 % (station.get("custom_art_path"),
                    station.get("art_source"), station.get("id")))
        self.custom_art.setPlaceholderText("Path to an image file")
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_art)
        find = QPushButton("Find...")
        find.setToolTip("Search the web for artwork for this station")
        find.clicked.connect(self._find_art)
        crop = QPushButton("Crop…")
        crop.setToolTip("Trim the picture to the square the app draws it in")
        crop.clicked.connect(self._crop_art)
        art_row.addWidget(self.custom_art, stretch=1)
        art_row.addWidget(browse)
        art_row.addWidget(find)
        art_row.addWidget(crop)
        form.addRow("Custom image:", art_row)

        self.art_fallback = QCheckBox("Fall back to the other sources if empty")
        self.art_fallback.setChecked(bool(station.get("art_fallback", True)))
        form.addRow("", self.art_fallback)

        self.readout_source = QComboBox()
        for label, value in (("Automatic (Shazam - custom - stream)", "auto"),
                             ("Automatic (Shazam - stream - custom)", "auto_stream"),
                             ("Custom text", "custom"),
                             ("Shazam result", "shazam"),
                             ("Whatever the stream reports", "stream")):
            self.readout_source.addItem(label, value)
        self._select(self.readout_source, station.get("readout_source", "auto"))
        form.addRow("Song text:", self.readout_source)

        # Empty means "the station's name" (task 100): show that name.
        self._default_readout = storage.station_display_name(station).strip()
        self.custom_readout = QLineEdit(
            station.get("custom_readout") or self._default_readout)
        self.custom_readout.setPlaceholderText(
            "e.g. The Skippy and Dippy Morning Radio Show")
        form.addRow("Custom text:", self.custom_readout)

        self.readout_fallback = QCheckBox("Fall back to the other sources if empty")
        self.readout_fallback.setChecked(bool(station.get("readout_fallback", True)))
        form.addRow("", self.readout_fallback)

        layout.addLayout(form)
        layout.addStretch(1)
        return page

    def _build_identify_tab(self, station):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.shazam_check = QCheckBox(
            "Automatically identify the song playing (Shazam)"
        )
        self.shazam_check.setChecked(bool(station.get("shazam_auto")))
        layout.addWidget(self.shazam_check)

        form = QFormLayout()
        # Fixed at 30 seconds and no longer shown.
        #
        # It is kept so values() still reports it, but the choice was a
        # dial nobody could set well: shorter wastes bandwidth on a song
        # that has not changed, longer misses short tracks entirely. 30 is
        # the answer for every station worth identifying.
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(30, 900)
        self.interval_spin.setValue(30)
        self.interval_spin.setVisible(False)
        self.interval_spin.setEnabled(self.shazam_check.isChecked())
        layout.addLayout(form)

        self.lyrics_check = QCheckBox("Look up lyrics for identified songs")
        self.lyrics_check.setChecked(bool(station.get("lyrics_enabled", True)))
        layout.addWidget(self.lyrics_check)

        # Dev builds only (task 49). What is set here ships to everyone in
        # the next build; users never see the switch.
        self.talk_check = None
        if self._talk is not None:
            self.talk_check = QCheckBox(
                "Talk radio: keep the station's info in the artist panel")
            self.talk_check.setChecked(bool(self._talk))
            self.talk_check.setToolTip(
                "Songs and song IDs never replace it. Right-click the "
                "artist photo > Edit this artist… to set its Wikipedia "
                "page, bio and photo.")
            layout.addWidget(self.talk_check)
            note = QLabel(
                "Dev build only. Ships to everyone in your next build. "
                "To set its Wikipedia page, bio or photo: play it, then "
                "right-click the artist photo > Edit this artist….")
            note.setWordWrap(True)
            note.setStyleSheet("color: #888;")
            layout.addWidget(note)

        layout.addStretch(1)
        return page

    # -- helpers ------------------------------------------------------
    @staticmethod
    def _select(combo, value):
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _find_art(self):
        """Search the web for station art and take one of the results.

        A station's own favicon is usually a 16-pixel afterthought, and
        most streams carry no art at all - so the practical way to get a
        decent image is to go and look for one.
        """
        import webview
        station = getattr(self, "_station", {}) or {}
        query = (station.get("custom_name") or station.get("name") or "").strip()
        if not query:
            QMessageBox.information(
                self, "Find art", "Give the station a name first."
            )
            return

        if not webview.is_available():
            import webbrowser
            webbrowser.open(webview.image_search_url(query))
            QMessageBox.information(
                self, "Find art", webview.unavailable_message()
            )
            return

        # Runs as a separate process, so Chromium never touches this
        # one's rendering. Blocks until the window is closed, which is
        # what a modal dialog would have done anyway.
        url = webview.pick_image(query)
        _art_log(f"picker returned {len(url or '')} chars: {(url or '')[:90]!r}")
        if url:
            self._use_picked_art(url)
        else:
            # Nothing came back at all — the window was closed without a
            # choice, or the helper could not tell which image was meant.
            QMessageBox.information(
                self, "Find art",
                "No image came back from the picker. Double-click the "
                "picture you want, or use “Use this image” on it.")

    def _offer_crop(self, path):
        """Ask, once, right after a picture arrives.

        This is the moment a crop is actually wanted — the image is new,
        it is usually the wrong shape, and the alternative is noticing
        later and hunting for the button.
        """
        from PyQt5.QtWidgets import QMessageBox
        import croptool

        import imagelock
        image = imagelock.image(path)  # under the image lock (task 54)
        if image.isNull():
            return
        # Straight in, no asking.
        #
        # Choosing a picture from the finder and then being asked whether
        # you would like to crop it is one dialog too many — you have
        # already said which picture you want, and the next thing anyone
        # does is frame it. Cancel in the crop window keeps the picture
        # exactly as downloaded, so the question is answerable there.
        croptool.crop_file(path, parent=self, title="Crop artwork")

    def _crop_art(self):
        """Crop whatever picture the field points at, in place.

        In place because the station already points at this path — a crop
        written beside the original would mean choosing the right file
        again afterwards.
        """
        from PyQt5.QtWidgets import QMessageBox
        import croptool

        path = self.custom_art.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.information(
                self, "Crop",
                "Choose or find a picture first — there is nothing to crop.")
            return
        name = (self._station.get("custom_name")
                or self._station.get("name") or "artwork")
        if croptool.crop_file(path, parent=self, title=f"Crop — {name}"):
            # Qt caches pixmaps by path, and the path has not changed —
            # so the preview would show the picture as it was before the
            # crop unless the cache is told.
            from PyQt5.QtGui import QPixmapCache
            QPixmapCache.clear()
            self._refresh_art_preview()

    def _refresh_art_preview(self, *_args):
        """Redraw the picture beside the name (task 67): the chosen file
        when there is one, else what the station showed when this opened."""
        preview = getattr(self, "art_preview", None)
        if preview is None:
            return
        path = self.custom_art.text().strip()
        picture = None
        if path and os.path.isfile(path):
            # Qt caches pictures by path; a crop keeps the path.
            from PyQt5.QtGui import QPixmapCache
            QPixmapCache.remove(path)
            import imagelock
            picture = imagelock.pixmap(path)
            if picture.isNull():
                picture = None
        if picture is None:
            picture = getattr(self, "_art_initial", None)
        if picture is None:
            preview.setVisible(False)
            return
        preview.setPixmap(_rounded_art(picture, self.ART_SIDE))
        preview.setVisible(True)

    def _use_picked_art(self, url):
        _art_log(f"picked {url[:90]!r}")
        """Save the chosen image and point the field at the file.

        The field is a path to an image, not a URL - the artwork loader
        opens it from disk. Writing a http:// address into it produced a
        station whose custom art silently never appeared, which is what
        made the picker look like it did nothing.
        """
        if not url:
            return
        if url.startswith("file://"):
            self.custom_art.setText(url[7:])
            return
        if not url.startswith(("http://", "https://", "data:")):
            self.custom_art.setText(url)
            return

        # Downloaded on a worker, not here.
        #
        # This was a network call on the GUI thread: the stall log caught
        # the window frozen inside it, and a slow host froze the whole
        # app until the socket gave up. The worker already exists for
        # exactly this, and the dialog carries on while it runs.
        # Inline images never reach the network.
        #
        # Image search results are very often data: URIs — the picture is
        # in the address itself. The old blocking downloader decoded
        # those; when the fetch moved to a worker that handling was lost
        # with it, so every inline result failed silently and nothing was
        # ever saved. This is the case that has no download at all.
        if url.startswith("data:"):
            import base64
            header, _, payload = url.partition(",")
            try:
                data = (base64.b64decode(payload) if "base64" in header
                        else payload.encode("utf-8", "replace"))
            except Exception:                   # noqa: BLE001
                data = b""
            self._art_pending_url = url
            self._art_downloaded("station-art", url, data)
            return

        self._art_pending_url = url
        self.custom_art.setPlaceholderText("Downloading…")

        worker = workers.ArtworkWorker("station-art", url, parent=self)
        worker.loaded.connect(self._art_downloaded)
        worker.failed.connect(self._art_download_failed)
        worker.finished.connect(worker.deleteLater)
        self._art_worker = worker
        worker.start()

    def _art_download_failed(self, _slot, _url):
        _art_log(f"download failed for {_url[:70]}")
        self.custom_art.setPlaceholderText("Path to an image file")
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(
            self, "Artwork",
            "That picture could not be downloaded. Try another one.")

    def _art_downloaded(self, _slot, url, data):
        """Write what the worker fetched, then offer the crop."""
        if url != getattr(self, "_art_pending_url", None):
            _art_log("ignored: a later pick overtook this one")
            return
        self.custom_art.setPlaceholderText("Path to an image file")
        path = _write_station_art(data, url, self._station.get("id", "art"))
        if not path:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Artwork",
                "That image couldn't be saved — it did not come back as a "
                "picture. Try a different one.")
        if path:
            self.custom_art.setText(path)
            self._offer_crop(path)
            # The same path as before doesn't count as a change of text.
            self._refresh_art_preview()
            # The source is left alone.
            #
            # Automatic already prefers a saved picture when there is
            # one, so forcing "custom" here changed a setting nobody
            # asked to change — and it stuck, so a station that later got
            # a better cover from its stream could never show it.
        else:
            QMessageBox.warning(
                self, "Find art",
                "That image couldn't be downloaded. Try another one."
            )

    def _browse_art(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose artwork", "",
            "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;All files (*)"
        )
        if path:
            self.custom_art.setText(path)

    def values(self):
        return {
            "gain_db": round(self.gain_slider.value() / 10.0, 1),
            "eq_advanced": self.eq_advanced_check.isChecked(),
            "eq_bands": [s.value() for s in self.eq_band_sliders],
            "eq_bass": self.eq_sliders["eq_bass"].value(),
            "eq_mid": self.eq_sliders["eq_mid"].value(),
            "eq_treble": self.eq_sliders["eq_treble"].value(),
            "ad_override": self.ad_check.isChecked(),
            "ad_seconds": round(self.ad_spin.value(), 0),
            # "any" / "chosen" since build 64. The typed stream address
            # of older builds is gone; a saved one that matched a
            # frequency became that frequency when the dialog opened.
            "ad_filler_kind": (
                "chosen" if self.ad_kind.currentData() == "chosen"
                and self.ad_source.currentData() else "any"),
            "ad_filler_station_id": (
                self.ad_source.currentData() or ""
                if self.ad_kind.currentData() == "chosen" else ""),
            "ad_filler_url": "",
            "buffer_enabled": self.buffer_check.isChecked(),
            "buffer_seconds": round(self.buffer_spin.value(), 1),
            "radio_quality": self.radio_check.isChecked(),
            # An empty name means "no override", not an empty station.
            "custom_name": (self.name_field.text().strip()
                            or station_name_unset),
            "url": self.url_field.text().strip() or self._station.get("url"),
            "art_source": self.art_source.currentData(),
            "custom_art_path": self.custom_art.text().strip(),
            "art_fallback": self.art_fallback.isChecked(),
            "readout_source": self.readout_source.currentData(),
            # The name itself is kept as "empty", so a rename is followed.
            "custom_readout": (
                "" if self.custom_readout.text().strip()
                == self._default_readout
                else self.custom_readout.text().strip()),
            "readout_fallback": self.readout_fallback.isChecked(),
            "shazam_auto": self.shazam_check.isChecked(),
            "shazam_auto_set": True,
            "shazam_interval": int(self.interval_spin.value()),
            "lyrics_enabled": self.lyrics_check.isChecked(),
            **({"talk_radio": self.talk_check.isChecked()}
               if self.talk_check is not None else {}),
        }


class StationPickerDialog(QDialog):
    """A checkbox list of stations, used by both import and export.

    Import shows what was found in someone else's playlist file; export
    shows your own library. Same widget either way.
    """

    def __init__(self, parent=None, title="Choose stations", entries=None,
                 subtitle="", action_label="OK", with_art=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(620, 520)
        self._entries = list(entries or [])
        self._with_art = bool(with_art)
        # Per station, the picture to write out. Seeded with whatever the
        # station already uses, so exporting without touching anything
        # sends what you are looking at.
        self._art = {}
        self._cards = {}

        layout = QVBoxLayout(self)

        if subtitle:
            note = QLabel(subtitle)
            note.setWordWrap(True)
            note.setStyleSheet("color: #888;")
            layout.addWidget(note)

        # A filter, because a library is not a short list.
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name, URL or tag")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.NoSelection)
        for entry in self._entries:
            name = entry.get("custom_name") or entry.get("name") or entry.get("url")
            item = QListWidgetItem(f"{name}\n{entry.get('url', '')}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, entry)
            self.list_widget.addItem(item)
            if self._with_art:
                self._art[id(entry)] = (entry.get("custom_art_path")
                                        or "").strip()
        layout.addWidget(self.list_widget, stretch=1)

        if self._with_art:
            art_row = QHBoxLayout()
            self.art_label = QLabel("Artwork: the station's own")
            self.art_label.setStyleSheet("color: #888;")
            art_row.addWidget(self.art_label, 1)

            choose = QPushButton("Choose a picture…")
            choose.setToolTip("For whichever station is highlighted")
            choose.clicked.connect(self._choose_art)
            art_row.addWidget(choose)

            clear = QPushButton("Use none")
            clear.clicked.connect(self._clear_art)
            art_row.addWidget(clear)
            layout.addLayout(art_row)

            # The card's own words, editable per station.
            #
            # These are what the popup shows — a heading, a line under
            # the name, and a paragraph. Left blank, the station falls
            # back to the stock text, so this only has to be filled in
            # for the ones worth writing about.
            card = QFormLayout()
            self.card_title = QLineEdit()
            self.card_title.setPlaceholderText("Heading, e.g. Requests only")
            card.addRow("Card heading:", self.card_title)

            self.card_subtitle = QLineEdit()
            self.card_subtitle.setPlaceholderText(
                "One line, e.g. Talk · listener-driven")
            card.addRow("Card subtitle:", self.card_subtitle)

            self.card_body = QPlainTextEdit()
            self.card_body.setPlaceholderText(
                "What you want people to read about this station.")
            self.card_body.setFixedHeight(90)
            card.addRow("Card text:", self.card_body)
            layout.addLayout(card)

            for widget in (self.card_title, self.card_subtitle):
                widget.textEdited.connect(self._store_card)
            self.card_body.textChanged.connect(self._store_card)

            self.list_widget.currentItemChanged.connect(self._show_art)
            self.list_widget.currentItemChanged.connect(self._show_card)

        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: #888;")
        layout.addWidget(self.count_label)
        self.list_widget.itemChanged.connect(self._update_count)
        self._update_count()

        select_row = QHBoxLayout()
        all_btn = QPushButton("Select all")
        none_btn = QPushButton("Select none")
        invert_btn = QPushButton("Invert")
        all_btn.clicked.connect(lambda: self._set_all(Qt.Checked))
        none_btn.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        invert_btn.clicked.connect(self._invert)
        for b in (all_btn, none_btn, invert_btn):
            select_row.addWidget(b)
        select_row.addStretch(1)
        layout.addLayout(select_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(action_label)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, state):
        for row in range(self.list_widget.count()):
            self.list_widget.item(row).setCheckState(state)

    def _invert(self):
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            item.setCheckState(
                Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
            )

    def _update_count(self):
        self.count_label.setText(
            f"{len(self.selected())} of {self.list_widget.count()} selected"
        )

    def _filter(self, text):
        """Hide what does not match, rather than removing it.

        A hidden station keeps its tick, so filtering to find one more
        station to include does not quietly drop everything else.
        """
        needle = (text or "").strip().lower()
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            entry = item.data(Qt.UserRole) or {}
            hay = " ".join(str(x) for x in (
                entry.get("custom_name"), entry.get("name"),
                entry.get("url"), " ".join(entry.get("tags") or []))
                if x).lower()
            item.setHidden(bool(needle) and needle not in hay)

    def _current_entry(self):
        item = self.list_widget.currentItem()
        return (item.data(Qt.UserRole) if item else None), item

    def _show_art(self, *_a):
        entry, _item = self._current_entry()
        if entry is None:
            return
        path = self._art.get(id(entry), "")
        name = os.path.basename(path) if path else "none"
        self.art_label.setText(f"Artwork: {name}")

    def _choose_art(self):
        entry, item = self._current_entry()
        if entry is None:
            QMessageBox.information(
                self, "Artwork",
                "Click a station in the list first, then choose its picture.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Artwork for this station", os.path.expanduser("~"),
            "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp);;All files (*)")
        if path:
            self._art[id(entry)] = path
            self._show_art()

    def _clear_art(self):
        entry, _item = self._current_entry()
        if entry is not None:
            self._art[id(entry)] = ""
            self._show_art()

    def _store_card(self, *_a):
        """Keep what is typed against the station it was typed for."""
        entry, _item = self._current_entry()
        if entry is None:
            return
        self._cards[id(entry)] = {
            "card_title": self.card_title.text().strip(),
            "card_subtitle": self.card_subtitle.text().strip(),
            "card_body": self.card_body.toPlainText().strip(),
        }

    def _show_card(self, *_a):
        entry, _item = self._current_entry()
        if entry is None:
            return
        written = self._cards.get(id(entry)) or {
            "card_title": entry.get("card_title", ""),
            "card_subtitle": entry.get("card_subtitle", ""),
            "card_body": entry.get("card_body", ""),
        }
        # Blocked while filling in, or typing into one station's box
        # would be recorded against the one clicked next.
        for widget in (self.card_title, self.card_subtitle, self.card_body):
            widget.blockSignals(True)
        self.card_title.setText(written.get("card_title", ""))
        self.card_subtitle.setText(written.get("card_subtitle", ""))
        self.card_body.setPlainText(written.get("card_body", ""))
        for widget in (self.card_title, self.card_subtitle, self.card_body):
            widget.blockSignals(False)

    def card_for(self, entry):
        """The words written for this station's card, if any."""
        return self._cards.get(id(entry), {
            "card_title": entry.get("card_title", ""),
            "card_subtitle": entry.get("card_subtitle", ""),
            "card_body": entry.get("card_body", ""),
        })

    def art_for(self, entry):
        """The picture chosen for this station, or its own."""
        return self._art.get(id(entry), (entry.get("custom_art_path") or ""))

    def selected(self):
        return [
            self._entries[row]
            for row in range(self.list_widget.count())
            if self.list_widget.item(row).checkState() == Qt.Checked
        ]


class HistoryDialog(QDialog):
    """Every song the app has identified.

    Starred songs are pinned to the top and marked, and they are exempt
    from both the history cap and the Clear button - the only way to
    lose one is the explicit reset, which asks three times.
    """

    STAR = "\u2605"
    NO_STAR = "\u2606"

    def __init__(self, parent=None, history=None, limit=500):
        super().__init__(parent)
        self.setWindowTitle("Identified songs")
        self.setMinimumSize(660, 540)
        self._history = list(history or [])
        self._cleared = False
        self._favorites_reset = False
        self._favorites_changed = False

        layout = QVBoxLayout(self)

        self.list_widget = QListWidget()
        self.list_widget.setIconSize(QSize(48, 48))
        self.list_widget.itemDoubleClicked.connect(self._open_item)
        self.list_widget.itemClicked.connect(self._maybe_toggle_star)
        layout.addWidget(self.list_widget, stretch=1)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: #888;")
        layout.addWidget(self.info)

        row = QHBoxLayout()
        star_btn = QPushButton("Star / unstar selected")
        star_btn.clicked.connect(self._toggle_selected)
        copy_btn = QPushButton("Copy list")
        copy_btn.clicked.connect(self._copy)
        clear_btn = QPushButton("Clear history")
        clear_btn.setToolTip("Removes unstarred songs only")
        clear_btn.clicked.connect(self._clear)
        reset_btn = QPushButton("Reset favorites...")
        reset_btn.clicked.connect(self._reset_favorites)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        for b in (star_btn, copy_btn, clear_btn, reset_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self._limit = limit
        self._rebuild()

    # -- contents ------------------------------------------------------
    def _rebuild(self):
        self.list_widget.clear()
        ordered = storage.sort_history(self._history)
        for entry in ordered:
            when = storage.format_history_time(entry.get("at"))
            title = entry.get("title", "")
            artist = entry.get("artist", "")
            station = entry.get("station", "")
            starred = storage.is_favorite(entry)
            mark = self.STAR + " " if starred else ""
            label = f"{title} - {artist}" if artist else title
            item = QListWidgetItem(f"{mark}{label}\n{when}   -   {station}")
            item.setData(Qt.UserRole, entry.get("apple_music_url", ""))
            item.setData(Qt.UserRole + 1, storage.entry_key(entry))
            if starred:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setForeground(QBrush(QColor("#f5c542")))

            data = thumbs.load(storage.entry_key(entry))
            if data:
                pixmap = QPixmap()
                if pixmap.loadFromData(data):
                    item.setIcon(QIcon(pixmap))
            self.list_widget.addItem(item)

        starred_count = storage.count_favorites(self._history)
        self.info.setText(
            f"{len(self._history)} song(s); {starred_count} starred and "
            f"pinned above. Unstarred songs are capped at {self._limit}. "
            "Click the left edge of a row to star it, double-click to open "
            f"it in Apple Music. Thumbnail cache: "
            f"{thumbs.human_size(thumbs.total_bytes())}."
        )

    def _entry_for_item(self, item):
        key = item.data(Qt.UserRole + 1)
        for entry in self._history:
            if storage.entry_key(entry) == key:
                return entry
        return None

    # -- actions -------------------------------------------------------
    def _maybe_toggle_star(self, item):
        """Clicking the star column toggles; clicking elsewhere just
        selects, so a stray click can't unstar something."""
        pos = self.list_widget.mapFromGlobal(self.cursor().pos())
        if pos.x() <= self.list_widget.iconSize().width() + 14:
            self._toggle_item(item)

    def _toggle_selected(self):
        item = self.list_widget.currentItem()
        if item is None:
            QMessageBox.information(self, "Favorites", "Select a song first.")
            return
        self._toggle_item(item)

    def _toggle_item(self, item):
        entry = self._entry_for_item(item)
        if entry is None:
            return
        entry["favorite"] = not storage.is_favorite(entry)
        self._favorites_changed = True
        self._rebuild()

    def _open_item(self, item):
        url = item.data(Qt.UserRole)
        if url:
            import webbrowser
            webbrowser.open(url)

    def _copy(self):
        from PyQt5.QtWidgets import QApplication
        lines = []
        for entry in storage.sort_history(self._history):
            artist = entry.get("artist", "")
            title = entry.get("title", "")
            star = "* " if storage.is_favorite(entry) else ""
            lines.append(f"{star}{artist} - {title}" if artist else star + title)
        QApplication.clipboard().setText("\n".join(lines))

    def _clear(self):
        starred = storage.count_favorites(self._history)
        ordinary = len(self._history) - starred
        if not ordinary:
            QMessageBox.information(
                self, "Clear history", "There are no unstarred songs to clear."
            )
            return
        message = f"Delete {ordinary} unstarred song(s)?"
        if starred:
            message += f"\n\nYour {starred} starred song(s) will be kept."
        if QMessageBox.question(self, "Clear history", message) == QMessageBox.Yes:
            self._cleared = True
            self.accept()

    def _reset_favorites(self):
        """Unstar everything - asked three separate ways, because this
        is the one action in the app that destroys something the user
        deliberately marked as permanent."""
        count = storage.count_favorites(self._history)
        if not count:
            QMessageBox.information(
                self, "Reset favorites", "You have no starred songs."
            )
            return

        first = QMessageBox.question(
            self, "Reset favorites",
            f"Remove the star from all {count} favorite song(s)?\n\n"
            "Favorites are otherwise permanent - they survive clearing "
            "the history and are never trimmed automatically."
        )
        if first != QMessageBox.Yes:
            return

        second = QMessageBox.warning(
            self, "Reset favorites - are you sure?",
            f"This cannot be undone. All {count} starred song(s) will "
            "become ordinary history entries, and the older ones will "
            "then be trimmed away by the usual cap.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if second != QMessageBox.Yes:
            return

        typed, ok = QInputDialog.getText(
            self, "Reset favorites - final check",
            f'Type RESET below to unstar all {count} song(s).'
        )
        if not ok or typed.strip().upper() != "RESET":
            QMessageBox.information(
                self, "Reset favorites", "Nothing was changed."
            )
            return

        self._favorites_reset = True
        self.accept()

    # -- results -------------------------------------------------------
    def history(self):
        return self._history

    def was_cleared(self):
        return self._cleared

    def favorites_reset(self):
        return self._favorites_reset

    def favorites_changed(self):
        return self._favorites_changed


class TagEditorDialog(QDialog):
    """Edit one station's tags, and set the color each tag uses."""

    def __init__(self, parent=None, station=None, all_tags=None, settings=None):
        super().__init__(parent)
        station = station or {}
        self._settings = settings if settings is not None else {}
        self.setWindowTitle(f"Tags - {storage.station_display_name(station)}")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)

        note = QLabel(
            "Tags are shared across stations - the color you pick here "
            "is used everywhere that tag appears. Search for them with "
            "tag:name."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        layout.addWidget(note)

        # Existing tags in the library, tickable
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.NoSelection)
        current = {t.lower() for t in (station.get("tags") or [])}
        for tag in (all_tags or []):
            item = QListWidgetItem(tag)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if tag.lower() in current else Qt.Unchecked
            )
            item.setForeground(QBrush(QColor(storage.tag_color(self._settings, tag))))
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, stretch=1)

        # Add a new one
        add_row = QHBoxLayout()
        self.new_tag = QLineEdit()
        self.new_tag.setPlaceholderText("New tag, e.g. jazz")
        self.new_tag.returnPressed.connect(self._add_tag)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_tag)
        add_row.addWidget(self.new_tag, stretch=1)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        # Color for the highlighted tag
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color for the ticked tag below:"))
        self.color_combo = QComboBox()
        for name, color in storage.TAG_COLOR_CHOICES:
            self.color_combo.addItem(name, color)
            index = self.color_combo.count() - 1
            self.color_combo.setItemData(index, QBrush(QColor(color)), Qt.ForegroundRole)
        color_row.addWidget(self.color_combo)
        apply_color = QPushButton("Apply to selected")
        apply_color.clicked.connect(self._apply_color)
        color_row.addWidget(apply_color)
        layout.addLayout(color_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_tag(self):
        name = self.new_tag.text().strip()
        if not name:
            return
        for row in range(self.list_widget.count()):
            if self.list_widget.item(row).text().lower() == name.lower():
                self.list_widget.item(row).setCheckState(Qt.Checked)
                self.new_tag.clear()
                return
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        item.setForeground(QBrush(QColor(storage.tag_color(self._settings, name))))
        self.list_widget.addItem(item)
        self.new_tag.clear()

    def _apply_color(self):
        color = self.color_combo.currentData()
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.checkState() == Qt.Checked:
                storage.set_tag_color(self._settings, item.text(), color)
                item.setForeground(QBrush(QColor(color)))

    def selected_tags(self):
        return [
            self.list_widget.item(row).text()
            for row in range(self.list_widget.count())
            if self.list_widget.item(row).checkState() == Qt.Checked
        ]


class LocalStationDialog(QDialog):
    """Create a "custom station" from a folder of music.

    It behaves like a broadcast rather than a playlist: each time you
    tune in, a random track starts partway through, as though it had
    been on air before you arrived. The same static crossfade covers the
    change, so it sits alongside the real stations rather than feeling
    like a separate media player bolted on.
    """

    def __init__(self, parent=None, station=None):
        super().__init__(parent)
        self.setWindowTitle("Local station")
        self.resize(520, 220)
        station = station or {}

        layout = QVBoxLayout(self)

        # Folder only, here.
        #
        # This dialog used to be the only way in for both, so it carried a
        # YouTube option as well. Add Frequency… has its own YouTube
        # button now, and asking the same question in two places invites
        # the two answers to disagree. The radios are kept, hidden, so the
        # rest of the dialog's logic still has something to read.
        self.source_group = QButtonGroup(self)
        self.local_radio = QRadioButton("Folder of music")
        self.youtube_radio = QRadioButton("YouTube playlist")
        self.local_radio.setChecked(True)
        self.source_group.addButton(self.local_radio)
        self.source_group.addButton(self.youtube_radio)
        for radio in (self.local_radio, self.youtube_radio):
            radio.setVisible(False)

        form = QFormLayout()
        self.name_edit = QLineEdit(station.get("custom_name")
                                   or station.get("name") or "")
        self.name_edit.setPlaceholderText("Station name")
        form.addRow("Name", self.name_edit)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(station.get("local_path", ""))
        self.path_edit.setPlaceholderText("Folder containing music")
        path_row.addWidget(self.path_edit, stretch=1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._choose_folder)
        path_row.addWidget(browse)
        form.addRow("Folder", path_row)

        self.youtube_edit = QLineEdit(station.get("playlist_url", ""))
        self.youtube_edit.setVisible(False)
        layout.addLayout(form)

        # The status label has to exist before anything can update it -
        # _sync_source() calls _update_count(), which writes to it.
        # Building it afterwards raised inside __init__, and a dialog
        # whose constructor throws simply never appears.
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #888;")
        layout.addWidget(self.count_label)

        self.folder_widgets = (self.path_edit, browse)
        self.local_radio.toggled.connect(self._sync_source)
        self.youtube_edit.textChanged.connect(self._update_count)
        self.path_edit.textChanged.connect(self._update_count)
        self._sync_source()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose a music folder", self.path_edit.text() or ""
        )
        if folder:
            self.path_edit.setText(folder)

    def _sync_source(self):
        local = self.local_radio.isChecked()
        for widget in self.folder_widgets:
            widget.setEnabled(local)
        self.youtube_edit.setEnabled(not local)
        self._update_count()

    def _update_count(self):
        """Say what was found, so a wrong folder or URL is obvious before
        the station is created rather than when it won't play."""
        if self.local_radio.isChecked():
            folder = self.path_edit.text().strip()
            if not folder:
                self.count_label.setText("")
                return
            count = len(storage.scan_local_tracks(folder))
            self.count_label.setText(
                f"{count} audio file{'s' if count != 1 else ''} found."
                if count else "No audio files found in that folder."
            )
            return

        url = self.youtube_edit.text().strip()
        if not url:
            self.count_label.setText("")
        elif not youtube.available():
            # Say so up front rather than after the station is made.
            self.count_label.setText(
                "YouTube stations need yt-dlp: pip install --user yt-dlp"
            )
        elif not youtube.looks_like_playlist(url):
            self.count_label.setText(
                "That doesn't look like a playlist or channel URL."
            )
        else:
            self.count_label.setText(
                "Looks right. The playlist is read the first time it plays."
            )

    def values(self):
        local = self.local_radio.isChecked()
        return {
            "name": self.name_edit.text().strip() or "Local station",
            "kind": "local" if local else "youtube",
            "local_path": self.path_edit.text().strip() if local else "",
            "playlist_url": "" if local else self.youtube_edit.text().strip(),
        }


class SkinLockerDialog(QDialog):
    """Every skin in a folder, in one window.

    Winamp called this the skin browser and it earned its keep: skins
    arrive in batches, and picking one from a file dialog each time
    means never actually trying the other forty.

    The list is rebuilt from the folder each time the window opens and
    whenever the folder changes on disk, so deleting a skin outside the
    app doesn't leave a dead entry behind - the list is a view of the
    directory, not a copy of it.
    """

    skin_chosen = pyqtSignal(str)

    eq_fill_changed = pyqtSignal(str, bool)

    def _show_eq_fill(self, *_args):
        """Reflect the selected skin's own setting in the box.

        Also called once the list is filled. Without that the box
        started enabled with nothing selected, so ticking it had no
        skin to apply to and silently did nothing - which is exactly
        what a switch that does not work looks like.
        """
        name = self.selected_skin_name()
        self.eq_fill_check.blockSignals(True)
        self.eq_fill_check.setChecked(bool(self.eq_fills.get(name, False)))
        self.eq_fill_check.setEnabled(bool(name))
        self.eq_fill_check.blockSignals(False)

    def _on_eq_fill_toggled(self, on):
        """Report the change for whichever skin is selected."""
        name = self.selected_skin_name()
        if name:
            self.eq_fills[name] = bool(on)
            self.eq_fill_changed.emit(name, bool(on))

    def selected_skin_name(self):
        item = self.list_widget.currentItem()
        if item is None:
            return ""
        # The stored path, not the label.
        #
        # The label can be a tidied-up display name, while the skin
        # names itself after its file - so keying the setting off the
        # label wrote it under a name nothing would ever look up.
        path = item.data(Qt.UserRole) or item.text()
        return os.path.splitext(os.path.basename(path))[0]

    def __init__(self, parent=None, folder="", current="", eq_fills=None):
        super().__init__(parent)
        self.setWindowTitle("Skins")
        self.resize(460, 460)
        self._folder = folder
        self._current = current

        self.folder_label = QLabel("")
        self.folder_label.setStyleSheet("color: #888;")
        self.folder_label.setWordWrap(True)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._apply_selected)
        self.list_widget.currentItemChanged.connect(self._show_eq_fill)

        choose = QPushButton("Skin folder...")
        choose.clicked.connect(self._choose_folder)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.reload)
        apply_btn = QPushButton("Use skin")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._apply_selected)
        remove = QPushButton("Remove skin")
        remove.setToolTip("Take the skin off and go back to the normal window")
        remove.clicked.connect(self._remove_skin)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(choose)
        top.addWidget(refresh)
        top.addStretch(1)

        bottom = QHBoxLayout()
        # Per-skin: paint over the area behind the three simple-EQ
        # bars. Lives here because it is a property of the skin, not of
        # the app - the right answer differs per artwork.
        self.eq_fill_check = QCheckBox(
            "Blank the equalizer background in simple mode"
        )
        self.eq_fill_check.setToolTip(
            "Hides the gridline dashes behind the three bars. Turn it "
            "off for skins with a picture there."
        )
        self.eq_fills = dict(eq_fills or {})
        self.eq_fill_check.toggled.connect(self._on_eq_fill_toggled)

        bottom.addWidget(remove)
        bottom.addStretch(1)
        bottom.addWidget(apply_btn)
        bottom.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.folder_label)
        layout.addWidget(self.list_widget, stretch=1)
        layout.addWidget(self.eq_fill_check)
        layout.addLayout(bottom)

        # Watch the folder, so the list follows what is actually there.
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda _p: self.reload())
        self.reload()

    def folder(self):
        return self._folder

    def _choose_folder(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Folder containing Winamp skins", self._folder or ""
        )
        if chosen:
            self._folder = chosen
            self.reload()

    def reload(self):
        """Re-read the folder. Cheap, and always right."""
        self.list_widget.clear()
        for path in self._watcher.directories():
            self._watcher.removePath(path)

        if not self._folder or not os.path.isdir(self._folder):
            self.folder_label.setText(
                "No skin folder chosen yet."
            )
            return

        self._watcher.addPath(self._folder)
        self.folder_label.setText(self._folder)

        names = []
        # Case-insensitive, so the list reads the way a person would
        # sort it rather than putting every capitalised name first.
        for entry in sorted(os.listdir(self._folder), key=str.lower):
            if entry.lower().endswith((".wsz", ".zip")):
                names.append(entry)

        if not names:
            item = QListWidgetItem("No .wsz skins in this folder.")
            item.setFlags(Qt.NoItemFlags)
            self.list_widget.addItem(item)
            self._show_eq_fill()
            return

        for entry in names:
            full = os.path.join(self._folder, entry)
            label = os.path.splitext(entry)[0]
            if os.path.abspath(full) == os.path.abspath(self._current or ""):
                label += "   (in use)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, full)
            self.list_widget.addItem(item)
            if os.path.abspath(full) == os.path.abspath(self._current or ""):
                self.list_widget.setCurrentItem(item)

        # Nothing matched, so select something rather than leaving the
        # per-skin switch pointing at no skin.
        if self.list_widget.currentRow() < 0 and self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self._show_eq_fill()

    def _apply_selected(self):
        item = self.list_widget.currentItem()
        path = item.data(Qt.UserRole) if item else None
        if not path:
            return
        self._current = path
        self.skin_chosen.emit(path)
        self.reload()

    def _remove_skin(self):
        self._current = ""
        self.skin_chosen.emit("")
        self.reload()


class ArtistOverridesDialog(QDialog):
    """Correct which article an artist's profile pulls from.

    A maintainer's tool, not a user setting: what is edited here is the
    file that ships with the app, so a correction made once is a
    correction for everyone who installs the next build. It writes to
    the bundled file when that is writable - a source checkout - and
    falls back to the user's own file when it is not, which is what
    happens inside an installed AppImage.
    """

    FIELDS = (
        ("wikipedia", "Wikipedia article",
         "Exact article title, e.g. Dracula (band)"),
        ("mbid", "MusicBrainz ID",
         "Pins the lookup to one specific artist"),
        ("bio", "Replacement text",
         "Used instead of any article. Last resort."),
        ("photo", "Photo URL", "Overrides the article's lead image"),
    )

    def __init__(self, parent=None, artist=""):
        super().__init__(parent)
        self.setWindowTitle("Artist corrections")
        self.resize(760, 520)

        self._overrides = dict(storage.load_bundled_artist_overrides())
        # The user's own corrections are shown too, merged on top, so
        # what is listed is what the app will actually use.
        for key, value in storage.load_artist_overrides().items():
            merged = dict(self._overrides.get(key) or {})
            merged.update(value)
            self._overrides[key] = merged

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Corrections shipped with the app. Each one fixes an artist "
            "whose profile pulls the wrong article."
        ))

        split = QHBoxLayout()
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self._show)
        split.addWidget(self.list, 1)

        form_holder = QWidget()
        form = QFormLayout(form_holder)
        self.edits = {}
        for name, label, hint in self.FIELDS:
            if name == "bio":
                edit = QPlainTextEdit()
                edit.setPlaceholderText(hint)
                edit.setMaximumHeight(120)
            else:
                edit = QLineEdit()
                edit.setPlaceholderText(hint)
            self.edits[name] = edit
            form.addRow(label, edit)
        split.addWidget(form_holder, 2)
        layout.addLayout(split)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #888;")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        for text, slot in (("Add artist...", self._add),
                           ("Remove", self._remove),
                           ("Apply to selected", self._apply)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        save = QPushButton("Save")
        save.clicked.connect(self._save)
        export = QPushButton("Export...")
        export.clicked.connect(self._export)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(export)
        buttons.addWidget(save)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self._reload(select=artist.strip().lower() if artist else "")
        if artist and artist.strip().lower() not in self._overrides:
            # Opened from a profile that was wrong: start the entry.
            self._add(artist)

    # -- the list ------------------------------------------------------

    def _reload(self, select=""):
        self.list.clear()
        for name in sorted(self._overrides):
            self.list.addItem(name)
        if select:
            found = self.list.findItems(select, Qt.MatchExactly)
            if found:
                self.list.setCurrentItem(found[0])

    def _show(self, name):
        entry = self._overrides.get(name) or {}
        for field, edit in self.edits.items():
            value = entry.get(field, "")
            if isinstance(edit, QPlainTextEdit):
                edit.setPlainText(value)
            else:
                edit.setText(value)

    def _current(self):
        item = self.list.currentItem()
        return item.text() if item else ""

    # -- editing -------------------------------------------------------

    def _add(self, artist=""):
        name = artist
        if not name:
            name, ok = QInputDialog.getText(
                self, "Add correction", "Artist name, as the stream reports it:"
            )
            if not ok:
                return
        key = (name or "").strip().lower()
        if not key:
            return
        self._overrides.setdefault(key, {})
        self._reload(select=key)

    def _remove(self):
        name = self._current()
        if not name:
            return
        self._overrides.pop(name, None)
        self._reload()
        self.status.setText(f"Removed {name}. Save to make it stick.")

    def _apply(self):
        """Take what is in the boxes into the entry being edited."""
        name = self._current()
        if not name:
            self.status.setText("Pick an artist first.")
            return
        entry = {}
        for field, edit in self.edits.items():
            value = (edit.toPlainText() if isinstance(edit, QPlainTextEdit)
                     else edit.text()).strip()
            if value:
                entry[field] = value
        self._overrides[name] = entry
        self.status.setText(f"{name}: {', '.join(entry) or 'nothing set'}")

    # -- saving --------------------------------------------------------

    def _payload(self):
        """Only entries that actually set something."""
        return {name: entry for name, entry in sorted(self._overrides.items())
                if entry}

    def _save(self):
        self._apply()
        target = storage.bundled_artist_overrides_path()
        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(self._payload(), f, indent=2, sort_keys=True)
            self.status.setText(f"Saved to {target}")
            return
        except OSError:
            pass

        # Read-only, so this is an installed build rather than a
        # checkout. Save where this machine can, and say so - silently
        # writing somewhere else would look like it had shipped.
        try:
            storage.save_artist_overrides(self._payload())
        except OSError as exc:
            self.status.setText(f"Could not save: {exc}")
            return
        self.status.setText(
            "Saved to your own overrides file - the shipped one is "
            "read-only here. Use Export to get a file to ship."
        )

    def _export(self):
        """Write the file out for dropping into the source tree."""
        self._apply()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export corrections",
            os.path.join(os.path.expanduser("~"), "artist_overrides.json"),
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._payload(), f, indent=2, sort_keys=True)
        except OSError as exc:
            self.status.setText(f"Export failed: {exc}")
            return
        self.status.setText(f"Exported {len(self._payload())} correction(s).")
