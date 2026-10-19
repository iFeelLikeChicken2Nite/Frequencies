#!/usr/bin/env python3
"""
Frequencies - a small internet radio player.

Features:
 - A list of stream URLs; double-click one to play it
 - Play / Stop / Next controls, plus a Shuffle toggle that changes what
   Next does. Previous always retraces the stations you actually played,
   shuffle or not.
 - Search radio-browser.info and add stations from results
 - Add custom stream URLs by hand, and rename stations
 - Global loudness normalization so stations don't jump wildly in volume,
   and a per-station 3-band EQ
 - Tag filtering that is the queue: click a tag to show only it,
   right-click to hide it, and whatever is left in the list is exactly
   what Next, Previous and shuffle move through
 - Per-station vocal clarity for talk broadcasts
 - Per-station automatic song identification, shown side by side with
   whatever the stream itself reports, with cover art for both and a
   link to the track on Apple Music
 - Radio static between stations while one is connecting
 - Sticky "this station is down" flags with background re-checking, and
   automatic skip to the next station when one won't connect
 - Backup / restore of the whole library, renames and settings
"""
import os
import shutil
import subprocess
import random
import sys
import time
import urllib.parse
import uuid
import webbrowser

from PyQt5.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QEvent, QSize, QPoint,
)
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QInputDialog,
    QSplitter,
    QLineEdit,
    QToolButton,

    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLabel,
    QSlider,
    QFormLayout,
    QMenu,
    QCheckBox,
    QSystemTrayIcon,
    QMessageBox,
    QStatusBar,
    QAbstractItemView,
    QFileDialog,
    QAction,
    QActionGroup,
    QTextBrowser,
    QWidgetAction,
    QScrollArea,
    QFrame,
    QSizePolicy,
)
from PyQt5.QtGui import QFont, QPixmap, QColor, QBrush, QIcon, QCursor

import crashlog
import thumbs
import mpris
import playlists
import lyrics as lyrics_module
import radiobrowser
import storage
import winampskin
from classicwindow import ClassicWindow, ClassicDock as SkinDock
from classicextras import EqualizerWindow, PlaylistWindow, TagWindow
import youtube
import artistinfo
import workers
from player import Player, PlayerState, force_c_numeric_locale
from widgets import (
    ElidedLabel, LiveLyricsView,
    StationItemDelegate, TagChip, FlowLayout,
)
from html import escape as _escape
from dialogs import (
    AddStationDialog,
    SkinLockerDialog,
    EditStationDialog,
    SearchDialog,
    StationOptionsDialog,
    StationPickerDialog,
    HistoryDialog,
    TagEditorDialog,
    LocalStationDialog,
)

APP_TITLE = "Frequencies"

ART_SIZE = 96
ART_SIZE_EXPANDED = 208
DOWN_COLOR = QColor("#c05a5a")


def app_icon():
    """The Frequencies icon, at every size we ship.

    Built from the individual PNGs rather than one file so the desktop
    can pick the right size - a 256px icon scaled down to 16px for a
    tray entry looks noticeably worse than the 16px one.

    Searched in several places because the layout differs between
    running from a checkout and running from the AppImage, where the
    code lives in usr/share/frequencies.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    roots = (
        os.path.join(here, "icons"),          # AppImage: beside the code
        os.path.join(here, "..", "icons"),    # checkout: repo root
        here,
        os.path.join(here, ".."),
    )

    icon = QIcon()
    for root in roots:
        found = False
        for size in (16, 24, 32, 48, 64, 128, 1):
            path = os.path.join(root, f"frequencies_{size}.png")
            if os.path.exists(path):
                icon.addFile(path, QSize(size, size))
                found = True
        if found:
            return icon

    # A single-size fallback, then give up gracefully: an empty QIcon
    # means an invisible tray entry, so the caller checks isNull().
    for root in roots:
        path = os.path.join(root, "frequencies.png")
        if os.path.exists(path):
            icon.addFile(path)
            return icon
    return icon


# Extensions mpv reports when it is describing the mount point rather
# than a song.
_STREAM_SUFFIXES = (
    ".mp3", ".aac", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".m3u8",
    ".mp4", ".webm", ".ts",
)


def _looks_like_stream_path(title, station=None):
    """Is this the stream's filename rather than a song?

    Before any real metadata arrives, mpv hands back whatever the URL's
    last path component is - "wbrh-mp3", "KSANFMAAC.aac" - which reads
    as a song title to everything downstream. Two tells: it matches part
    of the station's own URL, or it looks like a bare filename with no
    spaces in it.
    """
    text = (title or "").strip()
    if not text:
        return False

    lowered = text.lower()
    if lowered.endswith(_STREAM_SUFFIXES):
        return True

    if station:
        url = (station.get("url") or "").lower()
        if url:
            # The last path segment, with separators normalised: mpv
            # reports "wbrh-mp3" for a mount called "wbrh.mp3".
            tail = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
            flat = lowered.replace("-", "").replace("_", "").replace(".", "")
            if flat and flat == tail.replace("-", "").replace("_", "").replace(".", ""):
                return True

    # Deliberately no "single hyphenated word" rule.
    #
    # It caught "wbrh-mp3" but also "Weather-Update", which is a real
    # thing a station broadcasts. Matching the URL or an audio
    # extension is evidence; a hyphen on its own is not.
    return False


def _host_of(url):
    """The host part of a URL, for naming things after it."""
    import urllib.parse
    try:
        return urllib.parse.urlsplit(url).netloc
    except Exception:  # noqa: BLE001
        return ""


def display_name(station):
    """What to call this station on screen.

    The URL is the last resort, and deliberately not used for custom
    stations: theirs is a local:// or youtube:// marker, and showing
    half of one is worse than showing nothing.
    """
    name = station.get("custom_name") or station.get("name")
    if name:
        return name
    url = station.get("url") or ""
    if url.startswith(("local://", "youtube://")):
        return "Custom station"
    return url


class NowPlayingPanel(QFrame):
    """One artwork + one song readout.

    Which source each of them uses is a per-station setting, resolved by
    MainWindow: the panel is told what to show, it doesn't decide.
    """

    #: Left-click on the artwork - the "expand" gesture.
    art_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)

        self._art_size = ART_SIZE
        self.on_art_changed = None
        self._art_pixmap = None

        self.art_label = QLabel()
        self.art_label.setFixedSize(ART_SIZE, ART_SIZE)
        self.art_label.setAlignment(Qt.AlignCenter)
        self.art_label.setStyleSheet(
            "border: 1px solid #444; border-radius: 4px; color: #666;"
        )
        self.art_label.setText("no art")
        self.art_label.setCursor(Qt.PointingHandCursor)
        self.art_label.installEventFilter(self)

        # ElidedLabel, not QLabel: a station that stuffs a 200-character
        # string into its metadata must not be able to widen the window.
        self.text_label = ElidedLabel("-", lines=2)
        self.text_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        font = QFont()
        font.setPointSize(font.pointSize() + 1)
        self.text_label.setFont(font)

        # Star sits right beside the title, not off in the controls
        # somewhere - it's about this song, so it belongs next to it.
        self.favorite_btn = QToolButton()
        self.favorite_btn.setText("\u2606")          # hollow star
        self.favorite_btn.setCheckable(True)
        self.favorite_btn.setToolTip(
            "Star this song - starred songs pin to the top of the history "
            "and are never cleared"
        )
        self.favorite_btn.setStyleSheet(
            "QToolButton { border: none; font-size: 17px; color: #888; }"
            "QToolButton:checked { color: #f5c542; }"
        )
        self.favorite_btn.setEnabled(False)

        # Tells the user where what they're looking at came from, so
        # "why is it showing that?" is answerable at a glance.
        self.source_label = QLabel("")
        self.source_label.setStyleSheet("color: #888;")

        # When the song came out, when Shazam knows.
        self.release_label = QLabel("")
        self.release_label.setStyleSheet("color: #888;")
        self.release_label.hide()

        self.link_label = QLabel("")
        self.link_label.setOpenExternalLinks(False)
        self.link_label.setWordWrap(True)
        self.link_label.linkActivated.connect(self._open_link)
        self.link_label.hide()

        title_row = QHBoxLayout()
        title_row.addWidget(self.text_label, stretch=1)
        title_row.addWidget(self.favorite_btn)

        text_column = QVBoxLayout()
        text_column.addLayout(title_row)
        text_column.addWidget(self.source_label)
        text_column.addWidget(self.release_label)
        text_column.addWidget(self.link_label)

        body = QHBoxLayout()
        body.addWidget(self.art_label)
        body.addLayout(text_column, stretch=1)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.addLayout(body)

    def eventFilter(self, obj, event):
        # Left-click expands, right-click still opens the artwork menu -
        # so the two gestures don't fight over the same widget.
        if obj is self.art_label and event.type() == QEvent.MouseButtonRelease \
                and event.button() == Qt.LeftButton:
            self.art_clicked.emit()
            return True
        return super().eventFilter(obj, event)

    def set_art_size(self, px):
        """Grow or shrink the artwork, rescaling whatever is loaded."""
        self._art_size = int(px)
        self.art_label.setFixedSize(self._art_size, self._art_size)
        if self._art_pixmap is not None:
            self._rescale()

    def art_size(self):
        return self._art_size

    def _open_link(self, url):
        webbrowser.open(url)

    def set_text(self, text, source=""):
        self.text_label.setText(text or "-")
        self._raw_text = text or ""
        self.source_label.setText(f"via {source}" if source else "")

    def raw_text(self):
        return getattr(self, "_raw_text", "")

    def set_release_date(self, value):
        """When the song came out, under the title.

        Hidden rather than shown empty: a permanently blank line under
        every unidentified track is worse than no line at all.
        """
        self.release_label.setText(f"Released {value}" if value else "")
        self.release_label.setVisible(bool(value))

    def set_link(self, text, url, extra=()):
        """The main link, plus any others beside it.

        `extra` is [(text, url)] - a second and third place to read
        about what is playing, shown on the same line.
        """
        parts = []
        if url:
            parts.append(f'<a href="{url}">{text}</a>')
        for label, target in extra or ():
            if target:
                parts.append(f'<a href="{target}">{label}</a>')
        if parts:
            self.link_label.setText("  &middot;  ".join(parts))
            self.link_label.show()
        else:
            self.link_label.clear()
            self.link_label.hide()

    def set_art_bytes(self, data, source=""):
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self._set_pixmap(pixmap)
            return True
        return False

    def set_art_path(self, path):
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self._set_pixmap(pixmap)
            return True
        return False

    def _set_pixmap(self, pixmap):
        # Keep the original: rescaling an already-scaled pixmap when the
        # art is expanded would show a blurry upscale of a thumbnail.
        self._art_pixmap = pixmap
        self._rescale()
        if self.on_art_changed:
            self.on_art_changed(pixmap)

    def _rescale(self):
        self.art_label.setPixmap(
            self._art_pixmap.scaled(self._art_size, self._art_size,
                                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def clear_art(self):
        self._art_pixmap = None
        self.art_label.clear()
        self.art_label.setText("no art")
        if self.on_art_changed:
            self.on_art_changed(None)

    def reset(self):
        self.set_release_date("")
        self.set_text("-", "")
        self.set_link("", "")
        self.clear_art()


def aoty_search_url(artist, album):
    """A link to Album of the Year's own search for this record.

    A link rather than fetched data, deliberately. Reading a rating out
    of the site means finding its page for the album, and that needs a
    search - which albumoftheyear.org's robots.txt does not allow
    automated clients to do. Their own community library says as much.
    Sending you to the page instead is the same information, one click
    further away, and does not depend on scraping a site that has asked
    not to be scraped.
    """
    terms = " ".join(part for part in (artist, album) if part).strip()
    if not terms:
        return ""
    return ("https://www.albumoftheyear.org/search/?q="
            + urllib.parse.quote(terms))


class MainWindow(QMainWindow):
    # Bridges from the mpv event thread to the Qt GUI thread.
    state_changed = pyqtSignal(str, str)
    metadata_changed = pyqtSignal(str)
    # Audio actually started (url, seconds it took), and audio stopped
    # arriving and didn't come back.
    stream_connected = pyqtSignal(str, float)
    stream_stalled = pyqtSignal(str)
    # A local station's track played to its end.
    track_finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        icon = app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(880, 600)
        # A long "now playing" string must never be able to grow the
        # window. Widgets that could report a huge sizeHint are set to
        # Ignored/elided individually; this caps the whole thing as a
        # backstop so the layout can always shrink back down.
        self.setMinimumSize(560, 400)

        # Set before anything can emit a signal. Restoring the saved
        # volume fires valueChanged, whose handler talks to MPRIS - and
        # the bridge isn't built until the end of __init__. With the
        # attribute missing that raised AttributeError inside a Qt slot,
        # which PyQt turns into an abort(): the app died at launch for
        # anyone whose saved volume wasn't exactly the default. A plain
        # None plus the guards below is the whole fix.
        self.mpris = None

        self.stations = storage.load_stations()
        self.settings = storage.load_settings()
        self.health = storage.load_health()
        self.history = storage.load_history()

        # Identification became a default; switch it on for stations
        # added before that (once, tracked in settings).
        if storage.apply_identify_default(self.stations, self.settings):
            storage.save_stations(self.stations)
            storage.save_settings(self.settings)

        # The static recordings were re-levelled louder; nudge anyone
        # still on the old slider position so they actually hear it.
        if storage.raise_old_noise_volume(self.settings):
            storage.save_settings(self.settings)

        # The stall timeout was shortened; carry existing installs over.
        if storage.lower_old_stall_timeout(self.settings):
            storage.save_settings(self.settings)

        # The EQ became per-station; carry the old global setting over
        # so nobody's sound quietly goes flat.
        if storage.apply_station_eq_default(self.stations, self.settings):
            storage.save_stations(self.stations)
            storage.save_settings(self.settings)

        self._visible = list(self.stations)
        self._lyrics_anchor = None
        self.current_index = -1
        self.is_playing = False
        self._classic_state = "stop"

        # Where Prev goes. Playback history, not list order: with
        # shuffle on, "the one before this" is only knowable by
        # remembering it, and the user expects Prev to retrace their
        # steps either way.
        self._back_stack = []
        # Last track played per local station, so the next pick avoids
        # an immediate repeat.
        self._last_local_track = {}
        self._last_youtube_video = {}
        self._sleep_timer = None
        self._sleep_deadline = None
        self._sleep_restore_volume = None
        self._artist_shown = ""
        self._artist_info = {}
        self._artist_worker = None
        # A YouTube video's thumbnail, used as "stream" artwork.
        self._stream_art = ""

        # Trivia cycles slowly: fast enough to notice, slow enough to
        # finish reading a sentence before it changes.
        self._favicon_cache = {}
        self._classic_info_timer = None
        self._visualizer = None
        self._skin_tap = None
        self._marquee_cycle = []
        self._marquee_index = 0
        self._marquee_timer = None
        self._playing_since = None
        self._accent = None
        # Set by the sidebar; None means show everything.
        self._nav_filter = None
        self._classic_tags = None
        self._tray = None
        self._tray_now_playing = None
        self._really_quitting = False
        self._skin = None
        self._classic = None
        self._classic_eq = None
        self._classic_playlist = None
        self._classic_group = None
        self._youtube_worker = None
        self._suppress_back_push = False

        # Which view sits in each dynamic panel, and how wide the window
        # was last time we decided how many panels fit.
        self._layout_tier = None
        self._art_expanded = False

        # Auto-skip bookkeeping
        self._connect_attempts = 0
        self._skip_chain = 0          # stations skipped without success
        self._suppress_auto_skip = False

        # Background workers we must keep references to
        self._shazam_worker = None
        self._art_workers = []
        self._checks = []
        self._lyrics_worker = None

        # What each source currently reports for the playing station.
        # The panel shows whichever one the station's settings pick.
        self._stream_text = ""
        self._shazam_track = None
        self._art_cache = {}          # url -> raw image bytes
        self._current_track_key = ""


        self.player = Player(
            on_state=lambda s, m: self.state_changed.emit(s, m),
            on_metadata=lambda t: self.metadata_changed.emit(t),
            on_connected=lambda url, secs: self.stream_connected.emit(url, secs),
            on_stalled=lambda url: self.stream_stalled.emit(url),
            on_track_finished=lambda: self.track_finished.emit(),
        )
        self.state_changed.connect(self._on_player_state)
        self.metadata_changed.connect(self._on_metadata)
        self.stream_connected.connect(self._on_stream_connected)
        self.stream_stalled.connect(self._on_stream_stalled)
        self.track_finished.connect(self._on_track_finished)

        self._build_ui()
        self._build_menu()
        self._rebuild_tag_bars()
        self._reload_list()
        self._apply_loaded_settings()
        self._apply_layout_tier(force=True)

        # Periodic song identification for the playing station
        self._shazam_timer = QTimer(self)
        self._shazam_timer.timeout.connect(self._run_shazam)

        # Drives the static hand-off and the dead-stream watchdog. Half
        # a second is frequent enough to catch the moment audio starts
        # without the cross-fade feeling late.
        self._playback_timer = QTimer(self)
        self._playback_timer.setInterval(500)
        self._playback_timer.timeout.connect(self.player.tick)
        self._playback_timer.start()

        # Background re-checking of stations flagged down
        self.health_monitor = workers.HealthMonitor(interval=300, parent=self)
        self.health_monitor.result.connect(self._on_health_result)
        self._refresh_monitor_targets()
        self.health_monitor.start()
        self._apply_health_recheck_setting()

        # Keep the "down 3h" ages in the list fresh
        self._age_timer = QTimer(self)
        self._age_timer.timeout.connect(self._refresh_row_labels)
        self._age_timer.start(60000)

        # Drives the live lyric highlight.
        self._lyrics_timer = QTimer(self)
        self._lyrics_timer.timeout.connect(self._tick_lyrics)
        self._lyrics_timer.start(250)

        # Desktop media keys (KDE, GNOME, ...) via MPRIS.
        self.mpris = mpris.MprisBridge(self)
        if self.mpris.register():
            self.mpris.play_pause_requested.connect(self._mpris_play_pause)
            self.mpris.play_requested.connect(self.play_current_or_selected)
            self.mpris.stop_requested.connect(self.stop_playback)
            self.mpris.next_requested.connect(self.play_next)
            self.mpris.previous_requested.connect(self.play_previous)
            self.mpris.raise_requested.connect(self._raise_window)
            self.mpris.quit_requested.connect(self.close)
            self.mpris.volume_change_requested.connect(
                self.volume_slider.setValue
            )
        elif self.mpris.error:
            # Not fatal - the app just won't answer media keys.
            print(f"MPRIS unavailable: {self.mpris.error}")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        """Three slots, of which only the middle one is always there.

        Narrow, the window is a single column: search, tags, the station
        list, and the player at the bottom. Widen it and a second
        dynamic panel appears beside the list (lyrics by default);
        widen it further and the tags move out into a rail of their own.
        Nothing is hidden that you can't get back to - a panel that
        doesn't fit is still reachable through the view menu.
        """
        # Built before the panels: it's one of the views they mount, so
        # it has to exist before either slot is filled.
        self.artist_view = QTextBrowser()
        self.artist_view.setOpenExternalLinks(True)
        self.artist_view.setHtml(
            "<p style='color:#888'>Who you're listening to appears here "
            "once a song has been identified.</p>"
        )

        self.lyrics_view = LiveLyricsView()
        self.lyrics_view.clear_lyrics(
            "Lyrics appear here once a song has been identified."
        )
        self.lyrics_view.setMinimumHeight(120)

        self.splitter = QSplitter(Qt.Horizontal)

        self.tag_rail = self._build_tag_rail()
        self.main_column = self._build_main_column()
        # Both views exist all the time; which slot each one is mounted
        # in is what changes. One widget per view, moved rather than
        # duplicated, so the lyric highlight only ever ticks one of them.
        # Lyrics and the artist profile share one panel, divided.
        #
        # They used to be two panels you could swap views between, which
        # was more choice than the question deserves - nobody wants the
        # station list in the right-hand slot. One panel, two halves,
        # each of which can be folded away with a click.
        self.side_panel = QSplitter(Qt.Vertical)
        self.side_panel.addWidget(self.lyrics_view)
        self.side_panel.addWidget(self.artist_view)
        self.side_panel.setStretchFactor(0, 3)
        self.side_panel.setStretchFactor(1, 2)
        self.side_panel.setChildrenCollapsible(False)

        self.panel_b = self._build_side_panel()

        self.splitter.addWidget(self.tag_rail)
        self.splitter.addWidget(self.main_column)
        self.splitter.addWidget(self.panel_b)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 3)
        self.splitter.setSizes([170, 520, 420])
        self.splitter.setChildrenCollapsible(False)

        # Three zones above, one player bar across the bottom.
        #
        # The player used to live inside the middle column, which put
        # the thing you look at most in a third of the window and left
        # it competing with the station list for width. Across the
        # bottom it gets the full width and the list gets its height
        # back - which is the shape every music player has settled on
        # for the same reason.
        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(self.splitter, stretch=1)

        self.player_bar = self._build_now_playing()
        self.player_bar.setObjectName("PlayerBar")
        shell_layout.addWidget(self.player_bar)
        self.setCentralWidget(shell)


        self._views = {"stations": self.stations_page}
        self._apply_panel_views()

        self.setStatusBar(QStatusBar())
        self.statusBar().setSizeGripEnabled(True)
        self.statusBar().setMinimumWidth(1)

    # -- far left: the tag rail ----------------------------------------
    def _build_tag_rail(self):
        """The same chips as the bar above the list, stacked in a column
        for when there's width to spare."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 2, 4)
        layout.setSpacing(4)

        # Navigation above the tags.
        #
        # Each destination is a view of the same station list rather
        # than a separate page: Favourites is the list filtered, Heard
        # is it sorted by when you last played something. Building five
        # real pages would mean five copies of a list that already
        # knows how to be all of these.
        self.nav_buttons = {}
        for key, label in (("stations", "Stations"),
                           ("search", "Search"),
                           ("discover", "Discover"),
                           ("heard", "Heard"),
                           ("favourites", "Favorites")):
            button = QPushButton(label)
            button.setProperty("nav", True)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _c, k=key: self._navigate(k))
            layout.addWidget(button)
            self.nav_buttons[key] = button
        self.nav_buttons["stations"].setChecked(True)

        layout.addSpacing(10)

        heading = QLabel("TAGS")
        font = QFont()
        font.setBold(True)
        font.setPointSize(max(7, font.pointSize() - 1))
        heading.setFont(font)
        heading.setProperty("role", "section")
        layout.addWidget(heading)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        self.tag_rail_layout = QVBoxLayout(inner)
        self.tag_rail_layout.setContentsMargins(0, 0, 0, 0)
        self.tag_rail_layout.setSpacing(3)
        self.tag_rail_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)

        self.clear_tags_btn = QPushButton("Clear filters")
        self.clear_tags_btn.clicked.connect(self._clear_tag_filters)
        layout.addWidget(self.clear_tags_btn)
        panel.setMinimumWidth(160)
        panel.setMaximumWidth(230)
        return panel

    # -- a dynamic panel is just a slot something gets mounted into ----
    def _build_dynamic_slot(self, layout_attr, slot_key):
        """A panel plus its own menu for choosing what goes in it.

        Every slot carries the same menu rather than one menu governing
        all of them: with three panels and four views, "which of these
        am I changing?" has to be answerable by where you clicked.
        """
        slot = QWidget()
        outer = QVBoxLayout(slot)
        outer.setContentsMargins(2, 4, 4, 4)
        outer.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("")
        title.setStyleSheet("color: #888;")
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(layout, stretch=1)

        setattr(self, layout_attr, layout)
        setattr(self, f"{layout_attr}_title", title)
        return slot




    def _open_stream_sniffer(self):
        """Browse to a station's site and capture what it plays.

        For stations that publish no stream URL - just a page with a
        play button. The URL exists, it is simply never written down;
        pressing play makes the page fetch it, and this watches for it.
        """
        import webview
        if not webview.is_available():
            QMessageBox.information(
                self, "Stream sniffer",
                "This needs QtWebEngine, which isn't installed in this "
                "build."
            )
            return

        url = webview.sniff_stream()
        if url:
            self._add_sniffed_stream(url)

    def _add_sniffed_stream(self, url):
        """Turn a captured stream into a station."""
        if not url:
            return
        name, ok = QInputDialog.getText(
            self, "New station", "Name this station:",
            text=_host_of(url) or "Sniffed station",
        )
        if not ok or not name.strip():
            return

        station = dict(storage.STATION_DEFAULTS)
        station.update({
            "id": str(uuid.uuid4()),
            "name": name.strip(),
            "url": url,
        })
        self.stations.append(station)
        storage.save_stations(self.stations)
        self._reload_list()
        self.notify(f"Added {name.strip()}.", 5)

    def _build_panels_menu(self):
        """Which halves of the side panel are showing.

        The same two toggles as the buttons on the panel itself, put on
        the menu bar so they are still reachable when the panel has been
        folded away entirely.
        """
        self.panels_menu.clear()
        for label, key, toggle in (
            ("Lyrics", "lyrics", self.lyrics_toggle),
            ("Artist profile", "artist", self.artist_toggle),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(toggle.isChecked())
            action.toggled.connect(lambda on, t=toggle: t.setChecked(on))
            self.panels_menu.addAction(action)

    SLOTS = ("a", "b")

    def _build_side_panel(self):
        """Lyrics above, artist profile below, each foldable.

        Deliberately not swappable. The two panels used to offer a menu
        of which view to show, and every combination except this one was
        either useless or a way to lose the station list. What was
        actually wanted is being able to fold one away, which is a
        button rather than a menu.
        """
        panel = QWidget()
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(2, 4, 4, 4)
        outer.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.lyrics_toggle = QToolButton()
        self.lyrics_toggle.setText("Lyrics")
        self.lyrics_toggle.setCheckable(True)
        self.lyrics_toggle.setChecked(True)
        self.lyrics_toggle.toggled.connect(
            lambda on: self._set_half_visible("lyrics", on)
        )
        self.artist_toggle = QToolButton()
        self.artist_toggle.setText("Artist")
        self.artist_toggle.setCheckable(True)
        self.artist_toggle.setChecked(True)
        self.artist_toggle.toggled.connect(
            lambda on: self._set_half_visible("artist", on)
        )
        header.addWidget(self.lyrics_toggle)
        header.addWidget(self.artist_toggle)
        header.addStretch(1)
        outer.addLayout(header)
        outer.addWidget(self.side_panel, stretch=1)
        return panel

    def _set_half_visible(self, which, visible):
        """Fold one half of the side panel away."""
        widget = self.lyrics_view if which == "lyrics" else self.artist_view
        widget.setVisible(bool(visible))
        self.settings[f"{which}_panel_hidden"] = not bool(visible)
        storage.save_settings(self.settings)

        # With both folded, the panel itself is just a strip of buttons.
        #
        # isHidden, not isVisible: a widget inside a window that has not
        # been shown yet reports "not visible" whatever its own state,
        # so isVisible here folded the panel away during startup and it
        # never came back.
        both_gone = self.lyrics_view.isHidden() and self.artist_view.isHidden()
        self.side_panel.setVisible(not both_gone)

    def _apply_panel_views(self):
        """Mount the station list in the main column.

        Mounted once and left alone.

        This used to empty the layout and setParent(None) on whatever
        was in it before adding the list back. setParent(None) makes a
        widget a top-level window for the moment before it is re-parented
        - the compositor is asked for a surface and then told to throw it
        away - and doing that during startup is what left the main window
        a grey rectangle with its contents drawn over each other.

        There is only one mountable view now, so the shuffling had
        nothing to shuffle. It just adds the list if it is not already
        there.
        """
        if self.stations_page.parent() is not self.panel_a:
            self.panel_a_layout.addWidget(self.stations_page, stretch=1)
        self.stations_page.setVisible(True)

    # -- middle: search, tags, the list, the player --------------------
    def _navigate(self, key):
        """Switch what the station list is showing.

        Search and Discover are actions rather than destinations - one
        puts the cursor in the search box, the other opens the
        directory - so neither stays lit afterwards.
        """
        for name, button in self.nav_buttons.items():
            button.setChecked(name == key)

        if key == "search":
            self.nav_buttons["search"].setChecked(False)
            self.nav_buttons["stations"].setChecked(True)
            self.search_box.setFocus(Qt.ShortcutFocusReason)
            self.search_box.selectAll()
            return

        if key == "discover":
            self.nav_buttons["discover"].setChecked(False)
            self.nav_buttons["stations"].setChecked(True)
            self._open_search()
            return

        if key == "favourites":
            # Stations holding a song you have starred.
            starred = {
                (entry.get("station") or "").strip().lower()
                for entry in (self.history or [])
                if entry.get("favorite")
            }
            self._nav_filter = (
                lambda station: display_name(station).strip().lower() in starred
            )
        elif key == "heard":
            self._nav_filter = lambda station: bool(station.get("last_played"))
        else:
            self._nav_filter = None

        self._reload_list()

    def _build_main_column(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 2, 4)
        layout.setSpacing(4)

        # Search + the panel/sort menu
        top_row = QHBoxLayout()
        top_row.setSpacing(4)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search stations...")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._on_search_changed)
        top_row.addWidget(self.search_box, stretch=1)

        self.panel_menu_btn = QToolButton()
        self.panel_menu_btn.setText("\u2261")
        self.panel_menu_btn.setToolTip(
            "Sort the list, and choose what this panel shows"
        )
        self.panel_menu_btn.setPopupMode(QToolButton.InstantPopup)
        self.panel_menu_btn.setMinimumHeight(28)
        self.panel_menu_btn.setMenu(self._build_panel_menu())
        top_row.addWidget(self.panel_menu_btn)
        layout.addLayout(top_row)

        # Tag chips, wrapped. Hidden when the rail is showing them.
        self.tag_bar = FlowLayout(spacing=3)
        self.tag_bar_wrap = QWidget()
        self.tag_bar_wrap.setLayout(self.tag_bar)
        self.tag_bar_wrap.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        layout.addWidget(self.tag_bar_wrap)

        # The station list, packaged as a view so it can be mounted in
        # either dynamic slot.
        self.stations_page = self._build_stations_page()

        # Slot A: whatever view is selected, the station list by default.
        # Kept on self, like the other two, so the Panels menu can see
        # all three - it was a local, so the main panel never appeared.
        panel_a = self.panel_a = self._build_dynamic_slot("panel_a_layout", "a")
        self.panel_a_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel_a, stretch=1)

        return panel

    def _build_stations_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.itemDoubleClicked.connect(self._on_item_activated)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self.list_widget.setToolTip("Double-click a station to play it")
        self.list_widget.setTextElideMode(Qt.ElideRight)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.list_widget.model().rowsMoved.connect(self._on_rows_moved)
        self.station_delegate = StationItemDelegate(self.list_widget)
        self.list_widget.setItemDelegate(self.station_delegate)
        layout.addWidget(self.list_widget, stretch=1)

        # The count is the honest read-out of what will actually play,
        # now that the visible list *is* the queue.
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #888;")
        layout.addWidget(self.count_label)
        return page

    def _build_now_playing(self):
        box = QWidget()
        root = QVBoxLayout(box)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self.now_playing_label = ElidedLabel("Not playing", lines=1)
        bold = QFont()
        bold.setBold(True)
        self.now_playing_label.setFont(bold)
        root.addWidget(self.now_playing_label)

        self.panel = NowPlayingPanel()
        self.panel.on_art_changed = self._accent_from_art
        self.panel.art_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.panel.art_label.customContextMenuRequested.connect(self._art_menu)
        self.panel.art_label.setToolTip(
            "Click to enlarge, right-click to change the artwork"
        )
        self.panel.art_clicked.connect(self._toggle_art_size)
        self.favorite_btn = self.panel.favorite_btn
        self.favorite_btn.toggled.connect(self._on_favorite_toggled)
        root.addWidget(self.panel)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Show:"))
        self.source_combo = QComboBox()
        for label, value in (("Automatic", "auto"), ("Custom", "custom"),
                             ("Shazam", "shazam"), ("Stream", "stream")):
            self.source_combo.addItem(label, value)
        self.source_combo.setToolTip(
            "Which source the artwork and song text prefer for this station"
        )
        self.source_combo.currentIndexChanged.connect(self._on_source_combo)
        source_row.addWidget(self.source_combo, stretch=1)

        # One row, three columns: what is playing on the left, the
        # transport in the middle, everything else on the right.
        #
        # Stacked vertically this was a quarter of the window tall for
        # information that fits on one line. A bar reads left to right
        # the way the eye already moves.
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(14)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(1)
        left.addWidget(self.now_playing_label)
        left.addLayout(source_row)
        left_box = QWidget()
        left_box.setLayout(left)
        left_box.setMinimumWidth(220)

        # The song title gets the width, not the "Show:" row.
        #
        # With the panel at its minimum the title elided to a couple of
        # characters - it is the one thing in this bar people actually
        # read, so it takes the space and the source picker makes do.
        bar.addWidget(self.panel, stretch=3)
        bar.addWidget(left_box, stretch=1)
        bar.addWidget(self._build_controls(), stretch=0)

        root.addLayout(bar)
        return box

    def _build_controls(self):
        """Transport, volume, and a shortcut into this station's EQ.

        The old folded "sound leveling, EQ and static" section is gone:
        the EQ moved into the station's own options (where a per-station
        setting belongs), and levelling and static are global switches
        that live in the Options menu.
        """
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        # A single row, not a flow.
        #
        # FlowLayout wraps when it runs out of width, which in a column
        # meant the transport folded into a little grid of buttons. In
        # a bar there is width to spare and wrapping just looks broken.
        transport = QHBoxLayout()
        transport.setSpacing(4)

        def tool(text, tip, slot, checkable=False):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.setCheckable(checkable)
            button.setMinimumHeight(30)
            # Fixed, not Ignored: a transport glyph that has been
            # squeezed into an ellipsis is useless.
            button.setMinimumWidth(max(34, button.sizeHint().width()))
            button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            (button.toggled if checkable else button.clicked).connect(slot)
            return button

        self.prev_btn = tool("\u23ee", "Back to the station you were on last",
                             self.play_previous)
        self.play_btn = tool("\u25b6", "Play", self.play_current_or_selected)
        self.stop_btn = tool("\u25a0", "Stop", self.stop_playback)
        self.next_btn = tool("\u23ed", "Next station", self.play_next)
        self.shuffle_btn = tool("\U0001f500",
                                "Shuffle: Next jumps at random. Prev always "
                                "retraces what you actually played.",
                                self._on_shuffle_toggled, checkable=True)
        self.shazam_btn = tool("\u266a",
                               "Identify songs automatically on this station",
                               self._on_shazam_toggled, checkable=True)

        # EQ is per-station now, so this opens the playing station's
        # three bands right here rather than a global set.
        self.eq_btn = QToolButton()
        self.eq_btn.setText("EQ")
        self.eq_btn.setMinimumHeight(30)
        self.eq_btn.setMinimumWidth(max(34, self.eq_btn.sizeHint().width()))
        self.eq_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.eq_btn.setPopupMode(QToolButton.InstantPopup)
        self.eq_btn.setMenu(self._build_eq_menu())

        # Editing the playing station shouldn't mean finding it in the
        # list first - most of the time the station you want to change
        # is the one you are listening to.
        self.edit_btn = QToolButton()
        self.edit_btn.setText("Edit")
        self.edit_btn.setToolTip("Station options for what's playing")
        self.edit_btn.setMinimumHeight(30)
        self.edit_btn.setMinimumWidth(max(34, self.edit_btn.sizeHint().width()))
        self.edit_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.edit_btn.clicked.connect(self._edit_playing_station)

        for button in (self.prev_btn, self.play_btn, self.stop_btn,
                       self.next_btn, self.shuffle_btn, self.shazam_btn,
                       self.eq_btn, self.edit_btn):
            transport.addWidget(button)
        outer.addLayout(transport)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(4)
        vol_row.addWidget(QLabel("Vol"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(self.volume_slider, stretch=1)
        self.volume_value_label = QLabel("80")
        self.volume_value_label.setFixedWidth(26)
        vol_row.addWidget(self.volume_value_label)
        outer.addLayout(vol_row)
        return box

    # -- the per-station EQ popup ---------------------------------------
    def _build_eq_menu(self):
        menu = QMenu(self)
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.eq_station_label = QLabel("")
        self.eq_station_label.setStyleSheet("color: #888;")
        form.addRow(self.eq_station_label)

        # Simple: three bands.
        self.eq_simple_rows = []
        self.eq_sliders = {}
        for band, label in (("bass", "Bass"), ("mid", "Mid"),
                            ("treble", "Treble")):
            slider = QSlider(Qt.Horizontal)
            slider.setRange(-12, 12)
            slider.setMinimumWidth(180)
            slider.valueChanged.connect(self._on_station_eq_changed)
            self.eq_sliders[band] = slider
            row_label = QLabel(label)
            form.addRow(row_label, slider)
            self.eq_simple_rows.append((row_label, slider))

        # Advanced: ten, in place of the three rather than beside them.
        #
        # The two never show together: they are two ways of saying the
        # same thing, and a panel offering both invites you to set them
        # against each other.
        self.eq_advanced_panel = QWidget()
        bands_row = QHBoxLayout(self.eq_advanced_panel)
        bands_row.setContentsMargins(0, 0, 0, 0)
        self.eq_control_bands = []
        for hz in storage.EQ_BAND_FREQUENCIES:
            column = QVBoxLayout()
            readout = QLabel("0")
            readout.setAlignment(Qt.AlignHCenter)
            readout.setStyleSheet("color: #888;")
            slider = QSlider(Qt.Vertical)
            slider.setRange(-12, 12)
            slider.setMinimumHeight(80)
            slider.valueChanged.connect(
                lambda v, lbl=readout: lbl.setText(f"{v:+d}")
            )
            slider.valueChanged.connect(self._on_station_eq_changed)
            caption = QLabel(storage.eq_band_label(hz))
            caption.setAlignment(Qt.AlignHCenter)
            column.addWidget(readout)
            column.addWidget(slider, alignment=Qt.AlignHCenter)
            column.addWidget(caption)
            bands_row.addLayout(column)
            self.eq_control_bands.append(slider)
        form.addRow(self.eq_advanced_panel)

        self.eq_mode_check = QCheckBox("Advanced (10 bands)")
        self.eq_mode_check.setToolTip(
            "Applies to every station; each station keeps its own values"
        )
        self.eq_mode_check.toggled.connect(self._on_eq_mode_toggled)
        form.addRow("", self.eq_mode_check)

        reset = QPushButton("Flat")
        reset.clicked.connect(self._reset_station_eq)
        form.addRow("", reset)

        action = QWidgetAction(menu)
        action.setDefaultWidget(page)
        menu.addAction(action)
        menu.aboutToShow.connect(self._sync_eq_menu)
        return menu

    def _edit_playing_station(self):
        """Options for the playing station, falling back to the selected
        one when nothing is playing yet."""
        index = self.current_index
        if not (0 <= index < len(self.stations)):
            station = self._selected_station_or_none()
            index = self._index_of_id(station["id"]) if station else -1
        if index < 0:
            self.notify("No station to edit.", 4)
            return
        self._station_options(index)

    def _on_eq_mode_toggled(self, advanced):
        """Global: the choice of EQ is about the interface, not a
        station. The values on each side stay per-station."""
        self.settings["eq_advanced"] = bool(advanced)
        storage.save_settings(self.settings)
        self._apply_eq_mode()
        station = self._current_station()
        if station:
            self._apply_station_eq(station)

    def _apply_eq_mode(self):
        advanced = bool(self.settings.get("eq_advanced"))
        self.eq_advanced_panel.setVisible(advanced)
        for label, slider in self.eq_simple_rows:
            label.setVisible(not advanced)
            slider.setVisible(not advanced)

    def _sync_eq_menu(self):
        station = self._current_station() or self._selected_station_or_none()
        self.eq_mode_check.setChecked(bool(self.settings.get("eq_advanced")))
        self._apply_eq_mode()
        if station:
            bands = storage.normalize_eq_bands(station.get("eq_bands"))
            for slider, value in zip(self.eq_control_bands, bands):
                slider.blockSignals(True)
                slider.setValue(value)
                slider.blockSignals(False)
        self.eq_station_label.setText(
            f"EQ for {display_name(station)}" if station
            else "Select a station first"
        )
        values = storage.station_eq(station) if station else \
            {"bass": 0, "mid": 0, "treble": 0}
        for band, slider in self.eq_sliders.items():
            slider.blockSignals(True)
            slider.setValue(values[band])
            slider.setEnabled(station is not None)
            slider.blockSignals(False)

    def _eq_target(self):
        """Which station the EQ popup edits: the playing one if there is
        one, otherwise whatever is selected in the list."""
        return self._current_station() or self._selected_station_or_none()

    def _selected_station_or_none(self):
        item = self.list_widget.currentItem()
        if item is None:
            return None
        idx = self._index_of_id(item.data(Qt.UserRole))
        return self.stations[idx] if idx >= 0 else None

    def _on_station_eq_changed(self, _value):
        station = self._eq_target()
        if station is None:
            return
        station["eq_bands"] = [s.value() for s in self.eq_control_bands]
        station["eq_bass"] = self.eq_sliders["bass"].value()
        station["eq_mid"] = self.eq_sliders["mid"].value()
        station["eq_treble"] = self.eq_sliders["treble"].value()
        storage.save_stations(self.stations)
        # Only touch the player if this is what's actually playing.
        if station is self._current_station():
            self.player.set_equalizer(**storage.station_eq(station))

    def _reset_station_eq(self):
        for slider in self.eq_sliders.values():
            slider.setValue(0)

    def _apply_station_eq(self, station):
        """Push whichever EQ this station uses.

        Both are set every time: the simple one so it's correct if the
        station switches back, and the advanced flag so the player knows
        which of the two to build its filter chain from.
        """
        self.player.set_equalizer(**storage.station_eq(station))
        self.player.set_advanced_equalizer(
            bool(self.settings.get("eq_advanced")),
            storage.normalize_eq_bands(station.get("eq_bands")),
        )

    def _build_panel_menu(self):
        """The hamburger next to the search box: how the list is sorted.

        It used to offer to put the lyrics or the artist profile here
        instead of the stations, which was a good way to lose the list.
        Those live in their own panel now and are folded away with a
        button rather than swapped for something else.
        """
        menu = QMenu(self)

        menu.addSection("Sort stations by")
        self._sort_group = QActionGroup(self)
        self._sort_group.setExclusive(True)
        self._sort_actions = {}
        for mode in storage.SORT_MODES:
            action = QAction(storage.SORT_LABELS[mode], self)
            action.setCheckable(True)
            action.triggered.connect(lambda _c, m=mode: self._set_sort_mode(m))
            self._sort_group.addAction(action)
            self._sort_actions[mode] = action
            menu.addAction(action)

        menu.aboutToShow.connect(self._sync_panel_menu)
        return menu

    def _sync_panel_menu(self):
        mode = self.settings.get("sort_mode", "manual")
        for key, action in self._sort_actions.items():
            action.setChecked(key == mode)

    def _build_menu(self):
        menu = self.menuBar().addMenu("&Library")
        # Kept so the transfer submenu can be hung off it later.
        self.library_menu = menu

        add_action = QAction("&Add a station...", self)
        add_action.setShortcut("Ctrl+N")
        add_action.triggered.connect(self._add_station_menu)
        menu.addAction(add_action)

        location_action = QAction("Set my &location...", self)
        location_action.setToolTip(
            "Used by \"Near me\" when searching for stations"
        )
        location_action.triggered.connect(self._set_home_location)
        menu.addAction(location_action)

        browse_action = QAction("&Browse radio-browser.info...", self)
        browse_action.setShortcut("Ctrl+B")
        browse_action.triggered.connect(self._open_search)
        menu.addAction(browse_action)

        menu.addSeparator()

        backup_action = QAction("Back &up to file...", self)
        backup_action.triggered.connect(self._backup)
        menu.addAction(backup_action)

        restore_action = QAction("&Restore from file...", self)
        restore_action.triggered.connect(self._restore)
        menu.addAction(restore_action)

        menu.addSeparator()

        identify_action = QAction("Identify song &now", self)
        identify_action.setShortcut("Ctrl+I")
        identify_action.triggered.connect(lambda: self._run_shazam(manual=True))
        menu.addAction(identify_action)

        menu.addSeparator()
        history_action = QAction("Identified &songs...", self)
        history_action.setShortcut("Ctrl+H")
        history_action.triggered.connect(self._show_history)
        menu.addAction(history_action)

        # -- Options: the settings that really are global ---------------
        options = self.menuBar().addMenu("&Options")

        self.normalize_action = QAction("Auto-&normalize loudness", self)
        self.normalize_action.setCheckable(True)
        self.normalize_action.setToolTip(
            "Even out the volume differences between stations"
        )
        self.normalize_action.toggled.connect(self._on_normalize_toggled)
        options.addAction(self.normalize_action)

        options.addSeparator()

        self.noise_action = QAction("&Static between stations", self)
        self.noise_action.setCheckable(True)
        self.noise_action.toggled.connect(self._on_noise_toggled)
        options.addAction(self.noise_action)

        # A slider inside the menu, so the static's level sits next to
        # its on/off switch instead of in a dialog of its own.
        noise_menu = options.addMenu("Static &volume")
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(8, 4, 8, 4)
        self.noise_slider = QSlider(Qt.Horizontal)
        self.noise_slider.setRange(0, 100)
        self.noise_slider.setMinimumWidth(160)
        self.noise_slider.valueChanged.connect(self._on_noise_volume_changed)
        self.noise_value_label = QLabel("70")
        self.noise_value_label.setFixedWidth(26)
        row.addWidget(QLabel("Quiet"))
        row.addWidget(self.noise_slider, stretch=1)
        row.addWidget(self.noise_value_label)
        slider_action = QWidgetAction(noise_menu)
        slider_action.setDefaultWidget(holder)
        noise_menu.addAction(slider_action)

        # How often shuffle should land on a local station.
        ratio_menu = options.addMenu("Custom station &frequency")
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(8, 4, 8, 4)
        self.ratio_slider = QSlider(Qt.Horizontal)
        self.ratio_slider.setRange(0, 100)
        self.ratio_slider.setMinimumWidth(170)
        self.ratio_slider.setToolTip(
            "How often shuffle picks one of your local stations. At 0 it "
            "is simply proportional to how many you have."
        )
        self.ratio_slider.valueChanged.connect(self._on_local_ratio_changed)
        self.ratio_label = QLabel("off")
        self.ratio_label.setFixedWidth(48)
        row.addWidget(QLabel("Local"))
        row.addWidget(self.ratio_slider, stretch=1)
        row.addWidget(self.ratio_label)
        ratio_action = QWidgetAction(ratio_menu)
        ratio_action.setDefaultWidget(holder)
        ratio_menu.addAction(ratio_action)

        options.addSeparator()

        # Re-check stations that failed, in the background.
        self.recheck_action = QAction("Re-&check down stations", self)
        self.recheck_action.setCheckable(True)
        self.recheck_action.setToolTip(
            "Periodically retest stations that failed, so ones that come "
            "back stop being skipped"
        )
        self.recheck_action.toggled.connect(self._on_recheck_toggled)
        options.addAction(self.recheck_action)

        recheck_menu = options.addMenu("Re-check &every")
        self.recheck_group = QActionGroup(self)
        self.recheck_group.setExclusive(True)
        self.recheck_actions = {}
        for minutes, label in ((5, "5 minutes"), (15, "15 minutes"),
                               (30, "30 minutes"), (60, "1 hour"),
                               (180, "3 hours")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _c, m=minutes: self._on_recheck_interval(m)
            )
            self.recheck_group.addAction(action)
            self.recheck_actions[minutes] = action
            recheck_menu.addAction(action)
        recheck_menu.aboutToShow.connect(self._sync_recheck_menu)

        options.addSeparator()

        self.artist_panel_action = QAction("Artist &profile lookups", self)
        self.artist_panel_action.setCheckable(True)
        self.artist_panel_action.setToolTip(
            "Look up a biography for whoever is playing"
        )
        self.artist_panel_action.toggled.connect(self._on_artist_panel_toggled)
        options.addAction(self.artist_panel_action)

        skin_menu = options.addMenu("Classic &skin")
        locker_action = QAction("Skins...", self)
        locker_action.setToolTip("Every skin in your skin folder")
        locker_action.triggered.connect(self._open_skin_locker)
        skin_menu.addAction(locker_action)

        self.skin_show_action = QAction("Show the skinned player", self)
        self.skin_show_action.setCheckable(True)
        self.skin_show_action.toggled.connect(self._toggle_classic_window)
        skin_menu.addAction(self.skin_show_action)

        self.skin_eq_action = QAction("Show the skinned equalizer", self)
        self.skin_eq_action.setCheckable(True)
        self.skin_eq_action.toggled.connect(self._toggle_classic_eq)
        skin_menu.addAction(self.skin_eq_action)

        self.skin_playlist_action = QAction("Show the skinned playlist", self)
        self.skin_playlist_action.setCheckable(True)
        self.skin_playlist_action.toggled.connect(self._toggle_classic_playlist)
        skin_menu.addAction(self.skin_playlist_action)

        self.accent_action = QAction("Tint the interface from album art", self)
        self.accent_action.setCheckable(True)
        self.accent_action.setToolTip(
            "Pick the highlight color from whatever is playing"
        )
        self.accent_action.toggled.connect(self._on_accent_toggled)
        options.addAction(self.accent_action)

        visualizer_action = QAction("&Visualizer", self)
        visualizer_action.setToolTip(
            "Open the visualizer. It does not need the skinned player."
        )
        visualizer_action.triggered.connect(self._toggle_visualizer)
        options.addAction(visualizer_action)

        corrections_action = QAction("Artist corrections...", self)
        corrections_action.setToolTip(
            "Fix which article an artist's profile pulls from."
        )
        corrections_action.triggered.connect(self._edit_artist_overrides)
        options.addAction(corrections_action)

        museum_action = QAction("Browse the skin museum...", self)
        museum_action.setToolTip(
            "Open skins.webamp.org. Anything downloaded goes straight "
            "into your skin folder."
        )
        museum_action.triggered.connect(self._browse_skin_museum)
        skin_menu.addAction(museum_action)

        # One entry that opens the lot, with the individual toggles
        # still below it. Opening the stack took four clicks otherwise.
        whole_stack = QAction("Open the skinned player", self)
        whole_stack.setToolTip(
            "Opens the player, equalizer, tags and playlist together."
        )
        whole_stack.triggered.connect(self._open_whole_stack)
        skin_menu.addAction(whole_stack)
        skin_menu.addSeparator()

        self.skin_shuffle_action = QAction("Shuffle skin on each track", self)
        self.skin_shuffle_action.setCheckable(True)
        self.skin_shuffle_action.setToolTip(
            "Pick a different skin from the skin folder whenever a "
            "station starts playing."
        )
        self.skin_shuffle_action.toggled.connect(
            lambda on: self._save_setting("skin_shuffle", bool(on))
        )
        skin_menu.addAction(self.skin_shuffle_action)

        self.skin_tags_action = QAction("Show the skinned tags", self)
        self.skin_tags_action.setCheckable(True)
        self.skin_tags_action.toggled.connect(self._toggle_classic_tags)
        skin_menu.addAction(self.skin_tags_action)

        skin_menu.addSeparator()
        self.skin_scale_group = QActionGroup(self)
        self.skin_scale_actions = {}
        # No 3x. The skin is 275 wide, so triple size is a 825-pixel
        # window of scaled-up pixel art - past the point where it looks
        # like the skin rather than a magnified screenshot of it.
        for factor in (1, 2):
            action = QAction(f"{factor}x size", self)
            action.setCheckable(True)
            action.triggered.connect(lambda _c, f=factor: self._set_skin_scale(f))
            self.skin_scale_group.addAction(action)
            self.skin_scale_actions[factor] = action
            skin_menu.addAction(action)
        skin_menu.addSeparator()
        skin_menu.aboutToShow.connect(self._sync_skin_menu)

        self.thumbs_action = QAction("Station &thumbnails", self)
        self.thumbs_action.setCheckable(True)
        self.thumbs_action.setToolTip(
            "Show the station list as artwork instead of names"
        )
        self.thumbs_action.toggled.connect(self._on_thumbnails_toggled)
        options.addAction(self.thumbs_action)

        export_featured = QAction("Export as &featured...", self)
        export_featured.setToolTip(
            "Write the selected stations out as a featured list, "
            "settings and all"
        )
        export_featured.triggered.connect(self._export_featured)
        options.addAction(export_featured)

        sniff_action = QAction("&Stream sniffer...", self)
        sniff_action.setToolTip(
            "Find the stream behind a station's web player"
        )
        sniff_action.triggered.connect(self._open_stream_sniffer)
        options.addAction(sniff_action)

        self.panels_menu = options.addMenu("&Panels")
        self.panels_menu.aboutToShow.connect(self._build_panels_menu)

        self.output_menu = options.addMenu("Audio &output")
        self.output_menu.aboutToShow.connect(self._build_output_menu)

        self.tray_action = QAction("Show in system &tray", self)
        self.tray_action.setCheckable(True)
        self.tray_action.toggled.connect(self._on_tray_toggled)
        options.addAction(self.tray_action)

        self.minimize_action = QAction("&Close to tray", self)
        self.minimize_action.setCheckable(True)
        self.minimize_action.setToolTip(
            "Closing the window hides it instead of quitting"
        )
        self.minimize_action.toggled.connect(self._on_minimize_toggled)
        options.addAction(self.minimize_action)



        options.addSeparator()

        # Sleep timer.
        sleep_menu = options.addMenu("&Sleep timer")
        self.sleep_actions = {}
        for minutes, label in ((15, "15 minutes"), (30, "30 minutes"),
                               (45, "45 minutes"), (60, "1 hour"),
                               (90, "1.5 hours"), (120, "2 hours")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.triggered.connect(lambda _c, m=minutes: self._start_sleep(m))
            self.sleep_actions[minutes] = action
            sleep_menu.addAction(action)
        sleep_menu.addSeparator()
        self.sleep_cancel_action = QAction("Cancel sleep timer", self)
        self.sleep_cancel_action.triggered.connect(self._cancel_sleep)
        sleep_menu.addAction(self.sleep_cancel_action)
        sleep_menu.aboutToShow.connect(self._sync_sleep_menu)

        options.addSeparator()
        station_options = QAction("&Station options...", self)
        station_options.setShortcut("Ctrl+E")
        station_options.setToolTip(
            "Per-station sound, EQ, artwork and identification"
        )
        station_options.triggered.connect(self._options_for_current)
        options.addAction(station_options)

        tags_action = QAction("Edit this station's &tags...", self)
        tags_action.triggered.connect(self._tags_for_current)
        options.addAction(tags_action)

        # Import / export with other radio apps
        # A submenu of Library rather than a menu of its own.
        #
        # Importing and exporting stations is library work, and a
        # top-level menu holding three entries was more menu bar than
        # the feature deserved.
        transfer = self.library_menu.addMenu("&Transfer")
        scan_action = QAction("Import from an &installed app...", self)
        scan_action.triggered.connect(self._import_from_installed)
        transfer.addAction(scan_action)

        import_action = QAction("Import from a &file...", self)
        import_action.triggered.connect(self._import_playlist)
        transfer.addAction(import_action)

        export_action = QAction("&Export to another app...", self)
        export_action.triggered.connect(self._export_playlist)
        transfer.addAction(export_action)



    def _options_for_current(self):
        """Options for what's playing, or failing that what's selected."""
        station = self._eq_target()
        if station is None:
            self.notify("Select a station first.", 4)
            return
        self._station_options(self._index_of_id(station["id"]))

    def _tags_for_current(self):
        station = self._eq_target()
        if station is None:
            self.notify("Select a station first.", 4)
            return
        self._edit_tags(self._index_of_id(station["id"]))

    def _set_sort_mode(self, mode):
        if self._classic_playlist is not None:
            self._classic_playlist.manual_sort = (mode == "manual")
        if mode == self.settings.get("sort_mode"):
            return
        self.settings["sort_mode"] = mode
        storage.save_settings(self.settings)
        self._reload_list()
        if mode != "manual":
            self.notify(f"Sorted by {storage.SORT_LABELS[mode].lower()}. "
                "Switch to Manual to drag stations around.", 5)

    # -- responsive layout ---------------------------------------------
    def _layout_tier_for(self, width):
        if width >= 1080:
            return "wide"
        if width >= 780:
            return "medium"
        return "narrow"

    def _apply_layout_tier(self, force=False):
        """Decide how many panels fit, and where the tags live.

        Only acts when the tier actually changes: showing and hiding
        splitter children fires more resize events, and reacting to
        those would loop.
        """
        tier = self._layout_tier_for(self.width())
        if tier == self._layout_tier and not force:
            return
        self._layout_tier = tier

        self.tag_rail.setVisible(tier == "wide")
        self.panel_b.setVisible(tier in ("wide", "medium"))
        # The chips appear in exactly one place at a time.
        self.tag_bar_wrap.setVisible(tier != "wide")
        self._apply_panel_views()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_layout_tier()

    def _toggle_art_size(self):
        """Click the artwork to trade list space for a bigger picture."""
        self._art_expanded = not self._art_expanded
        self.panel.set_art_size(
            ART_SIZE_EXPANDED if self._art_expanded else ART_SIZE
        )

    # ------------------------------------------------------------------
    # Station list
    # ------------------------------------------------------------------
    def _row_label(self, station):
        # Tags are painted as pills by the delegate, not squeezed into
        # the text.
        name = display_name(station)
        status = storage.describe_health(self.health.get(station["id"]))
        if status:
            return f"⚠ {name}  -  {status}"
        return name

    def _reload_list(self):
        """Rebuild the visible rows from the current sort + search.

        self.stations is the canonical list; self._visible holds the
        stations actually shown, in display order, so row indices can be
        mapped back without guessing.
        """
        selected_id = None
        current = self.list_widget.currentItem()
        if current is not None:
            selected_id = current.data(Qt.UserRole)

        mode = self.settings.get("sort_mode", "manual")
        ordered = storage.sort_stations(self.stations, mode)
        query = self.search_box.text() if hasattr(self, "search_box") else ""
        self._visible = [s for s in ordered if storage.matches_search(s, query)]
        # The sidebar's destination narrows it further. Applied after
        # the search so the two combine rather than override: searching
        # inside Favourites searches favourites.
        if self._nav_filter is not None:
            self._visible = [s for s in self._visible if self._nav_filter(s)]

        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        thumbnails = bool(self.settings.get("station_thumbnails"))
        for station in self._visible:
            # In grid mode the name alone is the caption; the row's
            # usual extra detail has nowhere to go under a 96px tile.
            label = (display_name(station) if thumbnails
                     else self._row_label(station))
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, station["id"])
            if thumbnails:
                icon = self.station_thumbnail(station)
                if icon is not None:
                    item.setIcon(icon)
                item.setTextAlignment(Qt.AlignHCenter | Qt.AlignBottom)
            self._style_row(item, station)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

        # Dragging only makes sense when the order is the user's own.
        manual = mode == "manual" and not query.strip()
        self.list_widget.setDragDropMode(
            QAbstractItemView.InternalMove if manual
            else QAbstractItemView.NoDragDrop
        )
        self.list_widget.setDefaultDropAction(
            Qt.MoveAction if manual else Qt.IgnoreAction
        )

        if selected_id is not None:
            item = self._item_for_id(selected_id)
            if item is not None:
                self.list_widget.setCurrentItem(item)

        total = len(self.stations)
        shown = len(self._visible)
        self.count_label.setText(
            f"{shown} of {total} stations" if shown != total
            else f"{total} stations"
        )

    def _style_row(self, item, station):
        tags = station.get("tags") or []
        item.setData(StationItemDelegate.TAG_ROLE, tags)
        item.setData(
            StationItemDelegate.PLAYING_ROLE,
            0 <= self.current_index < len(self.stations)
            and self.stations[self.current_index]["id"] == station["id"],
        )
        item.setData(
            StationItemDelegate.COLOR_ROLE,
            {t.lower(): storage.tag_color(self.settings, t) for t in tags},
        )
        entry = self.health.get(station["id"])
        if entry and entry.get("down"):
            item.setForeground(QBrush(DOWN_COLOR))
            last_ok = entry.get("last_ok")
            when = (
                "never successfully reached"
                if last_ok is None
                else f"last reached {storage.humanize_age(time.time() - last_ok)} ago"
            )
            reason = entry.get("last_error") or "unreachable"
            item.setToolTip(f"{when} ({reason})")
        else:
            # Color the row by its first tag, so the library reads at a
            # glance without needing a separate column.
            tags = station.get("tags") or []
            if tags:
                item.setForeground(
                    QBrush(QColor(storage.tag_color(self.settings, tags[0])))
                )
            else:
                item.setData(Qt.ForegroundRole, None)
            # Stream URL deliberately left out of the tooltip - it's not
            # something the user asked to see just from hovering.
            item.setToolTip(f"Tags: {', '.join(tags)}" if tags else "")

    def _refresh_row_labels(self):
        """Update the 'down 3h' text in place without rebuilding the
        list (which would lose the selection)."""
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            idx = self._index_of_id(item.data(Qt.UserRole))
            if idx < 0:
                continue
            station = self.stations[idx]
            item.setText(self._row_label(station))
            self._style_row(item, station)

    def _on_search_changed(self, _text):
        # Keep the skinned windows in step: the playlist's own filter
        # line and the tag chips are views onto this one string.
        if self._classic_playlist is not None:
            self._classic_playlist.set_search_text(self.search_box.text())
        self._refresh_classic_tags()
        self._reload_list()
        self._sync_tag_chips()
        # And the rows themselves.
        #
        # _reload_list rebuilds the main list from the filter but says
        # nothing to the skinned one, so the skinned playlist kept
        # showing every station while the main view narrowed.
        self._refresh_classic_playlist()

    def _on_classic_transport(self, what):
        """The playlist's own little transport row.

        The same handlers the main window's buttons use, so the two
        cannot drift apart. Pause stops, for the reason it does
        everywhere else here: a paused stream resumes stale.
        """
        actions = {
            "previous": self.play_previous,
            "play": self.play_current_or_selected,
            "pause": self.stop_playback,
            "stop": self.stop_playback,
            "next": self.play_next,
            "eject": lambda: self._classic_eject_menu(
                self._classic_playlist.mapToGlobal(QPoint(0, 0))
                if self._classic_playlist else None
            ),
        }
        action = actions.get(what)
        if action:
            action()

    def _on_classic_reorder(self, source, target):
        """A station dragged to a new place in the skinned playlist.

        Moves it in self.stations, which is the canonical order, then
        rebuilds both views. Only reachable in manual sort mode - any
        other order is computed and would put the station straight back
        where it was.
        """
        visible = getattr(self, "_visible", None) or self.stations
        if not (0 <= source < len(visible) and 0 <= target < len(visible)):
            return

        moving = self._index_of_id(visible[source]["id"])
        landing = self._index_of_id(visible[target]["id"])
        if moving < 0 or landing < 0 or moving == landing:
            return

        station = self.stations.pop(moving)
        self.stations.insert(landing, station)
        storage.save_stations(self.stations)
        self._reload_list()
        self._refresh_classic_playlist()

    def _on_rows_moved(self, *_args):
        """The user dragged a station. Persist the new manual order."""
        if self.settings.get("sort_mode") != "manual":
            return
        order = []
        for row in range(self.list_widget.count()):
            station_id = self.list_widget.item(row).data(Qt.UserRole)
            index = self._index_of_id(station_id)
            if index >= 0:
                order.append(self.stations[index])
        # Anything filtered out of view keeps its relative place at the end.
        seen = {s["id"] for s in order}
        order.extend(s for s in self.stations if s["id"] not in seen)
        storage.renumber_manual_order(order)
        storage.save_stations(self.stations)
        self.notify("Order saved.", 2)

    def _index_of_id(self, station_id):
        for i, s in enumerate(self.stations):
            if s["id"] == station_id:
                return i
        return -1

    def _item_for_id(self, station_id):
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(Qt.UserRole) == station_id:
                return item
        return None

    def _add_station(self, name, url, station_uuid="", favicon="",
                     homepage=""):
        station = dict(
            storage.STATION_DEFAULTS,
            id=str(uuid.uuid4()),
            name=name or url,
            url=url,
            favicon=favicon,
            homepage=homepage,
            rb_uuid=station_uuid,
        )
        self.stations.append(station)
        storage.save_stations(self.stations)
        self._reload_list()

    def _add_featured_station(self, entry):
        """Import a featured station with its settings intact.

        A featured entry is a station somebody has already tidied up -
        named, tagged, EQ'd, artwork chosen - and the point of the list
        is that the work travels rather than being redone by everyone
        who adds it.
        """
        station = storage.station_from_featured(entry)
        if not station.get("url"):
            self.notify("That featured entry has no stream URL.", 5)
            return
        if any(existing.get("url") == station["url"]
               for existing in self.stations):
            self.notify(f"{display_name(station)} is already in your list.", 5)
            return

        self.stations.append(station)
        storage.save_stations(self.stations)
        self._reload_list()
        self.notify(f"Added {display_name(station)} with its settings.", 5)

    def _export_featured(self):
        """Write the selected stations out as a featured list.

        This is the other half: tidy a station up here, export it, and
        commit the file. What the importer reads is exactly what this
        writes.
        """
        import json

        chosen = [
            self.stations[self._index_of_id(item.data(Qt.UserRole))]
            for item in self.list_widget.selectedItems()
            if self._index_of_id(item.data(Qt.UserRole)) >= 0
        ]
        if not chosen:
            station = self._current_station()
            if station is None:
                self.notify("Select the stations to feature first.", 5)
                return
            chosen = [station]

        path, _ = QFileDialog.getSaveFileName(
            self, "Export featured stations", "featured.json",
            "JSON (*.json)",
        )
        if not path:
            return

        payload = {
            "stations": [storage.featured_from_station(st) for st in chosen]
        }
        try:
            with open(path, "w") as handle:
                json.dump(payload, handle, indent=2)
        except OSError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        self.notify(f"Exported {len(chosen)} station(s) to {os.path.basename(path)}.",
            6000,
        )

    def _add_station_menu(self):
        """One entry for both kinds of station.

        There were two: one for a stream URL and one for a folder of
        local music. They are both "add a station", they both end in the
        same list, and having them apart meant knowing in advance which
        menu item you wanted. Asked once, here, instead.
        """
        menu = QMenu(self)
        menu.addAction("From a stream URL...", self._add_custom_station)
        menu.addAction("From a folder of music...", self._add_local_station)
        menu.addAction("Search radio-browser...", self._open_search)
        menu.exec_(QCursor.pos())

    def _add_local_station(self):
        """Create a station backed by a folder of local music."""
        dlg = LocalStationDialog(self)
        if not dlg.exec_():
            return
        values = dlg.values()

        if values["kind"] == "youtube":
            url = values["playlist_url"]
            if not url:
                self.notify("No playlist URL given.", 4)
                return
            if not youtube.available():
                self.notify("YouTube stations need yt-dlp: pip install --user yt-dlp", 9)
                return
            source_url = "youtube://" + url

            # Name it after the playlist when the user didn't name it.
            # A station called "PLx7f2..." tells you nothing, and the
            # playlist already has a perfectly good name on YouTube.
            if not values["name"].strip():
                self.notify("Reading the playlist...", 0)
                QApplication.processEvents()
                title = youtube.playlist_title(url)
                if title:
                    values["name"] = title
                self.statusBar().clearMessage()
        else:
            if not values["local_path"]:
                self.notify("No folder chosen.", 4)
                return
            if not storage.scan_local_tracks(values["local_path"]):
                self.notify("That folder has no audio files in it.", 5)
                return
            source_url = "local://" + values["local_path"]

        station = dict(storage.STATION_DEFAULTS)
        station.update({
            "id": str(uuid.uuid4()),
            "name": values["name"],
            "kind": values["kind"],
            "local_path": values["local_path"],
            "playlist_url": values["playlist_url"],
            # A YouTube station has no favicon but always has a video
            # thumbnail, so "stream" art is the useful default for it.
            "art_source": "stream" if values["kind"] == "youtube" else "auto",
            # There is no real URL, but the rest of the app keys off it,
            # so give it a stable unique one rather than an empty string.
            "url": source_url,
        })
        self.stations.append(station)
        storage.save_stations(self.stations)
        self._rebuild_tag_bars()
        self._reload_list()
        if values["kind"] == "youtube":
            self.notify(f"Added {values['name']}. The playlist is read the first "
                f"time it plays.", 6)
        else:
            count = len(storage.scan_local_tracks(values["local_path"]))
            self.notify(f"Added {values['name']} - {count} tracks.", 5)

    def _add_custom_station(self):
        dlg = AddStationDialog(self)
        if dlg.exec_():
            name, url = dlg.values()
            self._add_station(name, url)
            self.notify(f'Added "{name}"', 4)

    def _set_home_location(self):
        """Ask where "near me" means.

        Stored as coordinates, looked up once from a place name. The
        setting existed and was read by the search, but nothing ever
        wrote it - so "Near me" was permanently greyed out with no way
        to enable it.
        """
        current = self.settings.get("home_label", "")
        place, ok = QInputDialog.getText(
            self, "Your location",
            "Town or city, for finding stations near you:", text=current,
        )
        if not ok:
            return
        place = place.strip()
        if not place:
            for key, value in (("home_lat", 0.0), ("home_lon", 0.0),
                               ("home_label", "")):
                self.settings[key] = value
            storage.save_settings(self.settings)
            self.notify("Location cleared.", 4)
            return

        found = radiobrowser.geocode(place)
        if not found:
            QMessageBox.information(
                self, "Location", f"Couldn't find {place}."
            )
            return
        latitude, longitude, label = found
        self.settings["home_lat"] = latitude
        self.settings["home_lon"] = longitude
        self.settings["home_label"] = label
        storage.save_settings(self.settings)
        self.notify(f"Location set to {label}", 5)

    def _open_search(self):
        near = None
        if self.settings.get("home_lat") or self.settings.get("home_lon"):
            near = (float(self.settings["home_lat"]),
                    float(self.settings["home_lon"]))
        dlg = SearchDialog(self, near=near)
        dlg.featured_added.connect(self._add_featured_station)
        dlg.station_added.connect(
            lambda name, url, uid, favicon, homepage="":
                self._add_station(name, url, uid, favicon, homepage)
        )
        dlg.exec_()

    def _selected_station(self, action_name):
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.information(self, action_name, "Select a station first.")
            return None, None
        idx = self._index_of_id(item.data(Qt.UserRole))
        if idx < 0:
            return None, None
        return idx, item

    def _rename_selected(self):
        idx, item = self._selected_station("Edit station")
        if idx is None:
            return
        self._rename_item(item)

    def _rename_item(self, item):
        idx = self._index_of_id(item.data(Qt.UserRole))
        if idx < 0:
            return
        station = self.stations[idx]
        others = [s.get("url", "") for s in self.stations if s["id"] != station["id"]]

        dlg = EditStationDialog(self, station=station, other_urls=others)
        if not dlg.exec_():
            return

        new_name, new_url = dlg.values()
        url_changed = dlg.url_changed()

        if new_name:
            station["custom_name"] = new_name

        if url_changed and new_url:
            station["url"] = new_url
            # The old health record described a different endpoint, so
            # carrying "down for 3 days" across to a URL we have never
            # tried would be misleading. Start it clean.
            self.health.pop(station["id"], None)
            storage.save_health(self.health)
            self._refresh_monitor_targets()

        storage.save_stations(self.stations)
        item.setText(self._row_label(station))
        self._style_row(item, station)

        if idx == self.current_index:
            self._update_now_playing_label()
            if url_changed:
                # Reconnect so the change takes effect now rather than
                # the next time this station comes round.
                self.notify(f"URL changed - reconnecting to {display_name(station)}...", 5)
                self._play_index(idx)
        elif url_changed:
            self.notify(f"URL updated for {display_name(station)}.", 5)

    def _edit_tags_selected(self):
        idx, item = self._selected_station("Tags")
        if idx is None:
            return
        self._edit_tags(idx)

    def _edit_tags(self, idx):
        station = self.stations[idx]
        dlg = TagEditorDialog(
            self, station=station,
            all_tags=storage.all_tags(self.stations),
            settings=self.settings,
        )
        if not dlg.exec_():
            return
        station["tags"] = dlg.selected_tags()
        storage.save_stations(self.stations)
        storage.save_settings(self.settings)
        self._rebuild_tag_bars()
        self._reload_list()
        self.notify(f"Tags for {display_name(station)}: "
            f"{', '.join(station['tags']) or 'none'}", 4)

    def _rebuild_tag_bars(self):
        """One chip per tag in the library, in both the wrapped bar and
        the rail. Only one of the two is visible at a time, but keeping
        both populated means switching between them is instant and can't
        show a stale filter."""
        for layout in (self.tag_bar, self.tag_rail_layout):
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

        tags = storage.all_tags(self.stations)
        states = storage.tag_filter_states(self.search_box.text())
        # The skinned tag window is another view of the same thing.
        self._refresh_classic_tags()
        if not tags:
            return

        for tag in tags:
            state = states.get(tag.lower(), storage.TAG_NEUTRAL)
            # Every tag, in both views.
            #
            # The bar used to stop after twelve chips and the rail cut
            # names at fourteen characters, so a long tag was either
            # missing outright or shortened past recognition. Both wrap,
            # so there is room for all of them; a tag you cannot see is
            # a filter you cannot use.
            for layout, limit, max_chars in ((self.tag_bar, None, None),
                                             (self.tag_rail_layout, None, None)):
                if limit is not None and layout.count() >= limit:
                    continue
                chip = TagChip(tag, storage.tag_color(self.settings, tag),
                               state, max_chars=max_chars)
                chip.setToolTip(chip.toolTip())
                chip.clicked_with_button.connect(self._on_tag_clicked)
                layout.addWidget(chip)

    def _sync_tag_chips(self):
        """Push the query's tag states back onto every chip."""
        states = storage.tag_filter_states(self.search_box.text())
        for layout in (self.tag_bar, self.tag_rail_layout):
            for i in range(layout.count()):
                widget = layout.itemAt(i).widget()
                if isinstance(widget, TagChip):
                    widget.set_state(
                        states.get(widget.tag.lower(), storage.TAG_NEUTRAL)
                    )

    def _on_tag_clicked(self, tag, exclude):
        """Left-click includes (and clicking again clears it), right-click
        excludes. The search box is what actually changes - the chips are
        a view onto it, so a typed query and a clicked one can never
        disagree."""
        self.search_box.setText(
            storage.cycle_tag_filter(self.search_box.text(), tag, exclude)
        )
        state = storage.tag_filter_state(self.search_box.text(), tag)
        if state == storage.TAG_INCLUDED:
            message = f'Showing only "{tag}".'
        elif state == storage.TAG_EXCLUDED:
            message = f'Hiding "{tag}".'
        else:
            message = f'Filter on "{tag}" cleared.'
        self.notify(message + "  Next, Prev and shuffle follow what's in the list.", 4)

    def _clear_tag_filters(self):
        """Drop every tag: term but leave a typed text search alone.

        Delegates to storage rather than splitting on whitespace here: a
        tag containing a space is one quoted token, and naive splitting
        would tear it in half.
        """
        self.search_box.setText(
            storage.clear_tag_filters(self.search_box.text())
        )

    def _station_options_selected(self):
        idx, item = self._selected_station("Station options")
        if idx is None:
            return
        self._station_options(idx)

    def _station_options(self, idx):
        station = self.stations[idx]
        dlg = StationOptionsDialog(
            self, station,
            # Only local custom stations can supply filler audio - a
            # YouTube one needs two network round-trips to resolve, and
            # the point is to have something playing immediately.
            custom_stations=[
                st for st in self.stations
                if storage.is_local_station(st) and st.get("local_path")
            ],
            eq_advanced=bool(self.settings.get("eq_advanced")),
            # Real streams only: a custom station has no live URL to
            # join while an advert runs.
            other_stations=[
                st for st in self.stations
                if not storage.is_custom_station(st) and st.get("url")
            ],
        )
        if not dlg.exec_():
            return
        station.update(dlg.values())
        storage.save_stations(self.stations)
        if idx == self.current_index:
            # Apply immediately to what's playing.
            self.player.set_vocal_clarity(station["vocal_clarity"])
            self.player.set_gain_db(station["gain_db"])
            self.player.set_radio_quality(station.get("radio_quality", False))
            self.player.set_enhance(station.get("enhance_quality", False))
            self._apply_station_eq(station)
            self._sync_source_combo(station)
            self._sync_station_toggles(station)
            self._refresh_display()
            self._sync_shazam_timer()
        self.notify(f'Options saved for "{display_name(station)}"', 4)

    def _remove_selected(self):
        idx, item = self._selected_station("Remove")
        if idx is None:
            return
        name = display_name(self.stations[idx])
        confirm = QMessageBox.question(
            self, "Remove station", f'Remove "{name}" from your list?'
        )
        if confirm != QMessageBox.Yes:
            return
        station_id = self.stations[idx]["id"]
        if idx == self.current_index:
            self.stop_playback()
            self.current_index = -1
        self.health.pop(station_id, None)
        storage.save_health(self.health)
        del self.stations[idx]
        storage.save_stations(self.stations)
        self._reload_list()
        self._refresh_monitor_targets()

    def _show_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        play_action = menu.addAction("Play")
        rename_action = menu.addAction("Edit name and URL...")
        tags_action = menu.addAction("Tags...")
        options_action = menu.addAction("Station options...")
        check_action = menu.addAction("Check if reachable")
        menu.addSeparator()
        remove_action = menu.addAction("Remove")
        action = menu.exec_(self.list_widget.mapToGlobal(pos))
        if action == play_action:
            self._on_item_activated(item)
        elif action == rename_action:
            self._rename_item(item)
        elif action == tags_action:
            idx = self._index_of_id(item.data(Qt.UserRole))
            if idx >= 0:
                self._edit_tags(idx)
        elif action == options_action:
            idx = self._index_of_id(item.data(Qt.UserRole))
            if idx >= 0:
                self._station_options(idx)
        elif action == check_action:
            self.list_widget.setCurrentItem(item)
            self._check_selected()
        elif action == remove_action:
            self.list_widget.setCurrentItem(item)
            self._remove_selected()

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    def _on_item_activated(self, item):
        idx = self._index_of_id(item.data(Qt.UserRole))
        if idx >= 0:
            self._skip_chain = 0
            self._play_index(idx)

    def _play_index(self, idx):
        if not (0 <= idx < len(self.stations)):
            return
        station = self.stations[idx]

        # A deliberate change of station is the moment to change skin.
        # Everything that starts a station comes through here.
        self._shuffle_skin()

        # Remember where we came from so Prev can retrace it. Pushed
        # here rather than in play_next/play_previous so that starting a
        # station any way at all - double-click, Next, MPRIS - is
        # recorded, and only Prev itself is exempt.
        if not self._suppress_back_push and 0 <= self.current_index < len(self.stations):
            leaving = self.stations[self.current_index]["id"]
            if leaving != station["id"]:
                self._back_stack.append(leaving)
                del self._back_stack[:-50]

        self.current_index = idx
        self._connect_attempts = 0
        # Select by station id, not by index: with any sort or filter
        # active the row number and the index into self.stations are
        # different, and using the index selected (and scrolled to) the
        # wrong station.
        self._select_station(station["id"])

        self._stream_text = ""
        self._shazam_track = None
        self._current_track_key = ""
        self.panel.reset()
        self._sync_favorite_button()
        self._reset_lyrics_state()
        self.now_playing_label.setText(f"Loading: {display_name(station)}")

        self._sync_source_combo(station)
        self._sync_station_toggles(station)

        storage.record_play(station)
        storage.save_stations(self.stations)
        # Deliberately no _reload_list() here. Under the "recently
        # played" / "most played" sorts, playing a station changes its
        # own sort key, so rebuilding the list would yank the row out
        # from under the click that started it. The order refreshes on
        # the next reload instead.

        # The EQ belongs to the station, so it has to be set before the
        # stream starts rather than left over from whatever played last.
        self._apply_station_eq(station)
        self.player.set_stall_timeout(self.settings.get("stall_timeout", 6))

        if storage.is_youtube_station(station):
            # Static first, then silence the outgoing station.
            #
            # Resolving takes two network round-trips, and the previous
            # stream must not carry on underneath for the whole of it -
            # but stopping the player also stops the static, so the
            # order matters and stop() is told to leave the noise alone.
            # Started before the stop, so there is never a moment with
            # neither playing.
            wants_noise = self.settings.get("noise_between_stations", True)
            if wants_noise:
                self.player.noise.start()
            self.player.stop(keep_noise=True)
            # Started again after the stop as well as before it.
            #
            # start() is a no-op when the static is already running, so
            # this costs nothing - but it guarantees the static is
            # going once the player has finished tearing the old stream
            # down, whatever that teardown touched on the way.
            if wants_noise:
                self.player.noise.start()
            self._start_youtube_resolve(station)
            self._refresh_display()
            self._mark_playing_row()
            self.settings["last_station_id"] = station["id"]
            storage.save_settings(self.settings)
            return

        url, start_fraction = self._resolve_playback(station)
        if url is None:
            self.notify(f"{display_name(station)}: no playable audio files in "
                f"{station.get('local_path') or 'that folder'}.", 6)
            return
        self.player.play(
            url,
            vocal_clarity=station.get("vocal_clarity"),
            gain_db=station.get("gain_db", 0.0),
            insecure=station.get("insecure", False),
            buffer_seconds=self._buffer_for(station),
            crossfade_ms=self.settings.get("crossfade_ms", 700),
            radio_quality=station.get("radio_quality", False),
            enhance=station.get("enhance_quality", False),
            start_fraction=start_fraction,
            **self._ad_override_args(station),
        )
        self._refresh_display()
        self._mark_playing_row()
        self._update_tray_label()
        self._refresh_classic()
        self._refresh_classic_eq()
        self._refresh_classic_playlist()

        self.settings["last_station_id"] = station["id"]
        storage.save_settings(self.settings)

    def _select_station(self, station_id):
        """Select and scroll to a station by id, without disturbing the
        order or firing a rebuild."""
        item = self._item_for_id(station_id)
        if item is None:
            return
        self.list_widget.blockSignals(True)
        self.list_widget.setCurrentItem(item)
        self.list_widget.scrollToItem(item, QAbstractItemView.EnsureVisible)
        self.list_widget.blockSignals(False)

    def play_current_or_selected(self):
        if self.is_playing:
            return
        self._skip_chain = 0
        if self.current_index >= 0:
            self._play_index(self.current_index)
            return
        # Nothing has played yet.
        #
        # With shuffle on, starting from the top of the list every time
        # is the one thing shuffle is supposed to prevent - so the first
        # press picks at random from whatever the filter allows, exactly
        # as Next would.
        if self.settings.get("shuffle") and self.stations:
            index = self._next_index(1)
            if index >= 0:
                self._play_index(index)
                return

        item = self.list_widget.currentItem()
        if item:
            self._on_item_activated(item)
        elif self.stations:
            self._play_index(0)
        else:
            self.notify("Add a station first.", 4)

    def stop_playback(self):
        self._suppress_auto_skip = True
        self._shazam_timer.stop()
        self.player.stop()

    def _ordered_ids(self, skip_bad=False):
        """Station ids in the order they're shown, which is what Next
        and Prev should follow - if you've sorted alphabetically or
        filtered to a tag, "next" means the next one you can see.

        With `skip_bad`, stations known to be down and not re-tested
        since are left out. Automatic selection shouldn't land you on a
        stream that failed an hour ago and has never been checked; a
        deliberate click still plays whatever you clicked.
        """
        visible = getattr(self, "_visible", None) or self.stations
        ids = [s["id"] for s in visible]
        if not skip_bad:
            return ids
        good = [i for i in ids if not self._is_unverified_down(i)]
        # If everything is flagged, fall back to the full list rather
        # than refusing to advance at all.
        return good or ids

    def _next_index(self, direction=1, skip_bad=True, allow_custom=True):
        """Where Next should go: honouring shuffle, the current sort and
        whatever the filter has left in the list, wrapping at both ends.

        Note this is only Next's business now. Prev has its own path -
        see play_previous - because "the one before this" and "the one
        above this in the list" stopped being the same thing the moment
        shuffle existed.
        """
        ids = self._ordered_ids(skip_bad=skip_bad)
        if not ids:
            return -1

        current_id = None
        if 0 <= self.current_index < len(self.stations):
            current_id = self.stations[self.current_index]["id"]

        if self.settings.get("shuffle") and len(ids) > 1:
            choices = [i for i in ids if i != current_id]
            return self._index_of_id(
                self._weighted_choice(choices, allow_custom=allow_custom)
            )

        if current_id not in ids:
            # Playing something that's filtered out (or nothing yet):
            # start at the appropriate end of the visible list.
            return self._index_of_id(ids[0] if direction > 0 else ids[-1])

        position = ids.index(current_id)
        # The modulo is the wrap: past the last station comes the first,
        # and before the first comes the last.
        return self._index_of_id(ids[(position + direction) % len(ids)])

    def play_next(self, allow_custom=True):
        idx = self._next_index(1, allow_custom=allow_custom)
        if idx >= 0:
            self._skip_chain = 0
            self._play_index(idx)

    def play_previous(self):
        """Always the station you were actually on before this one.

        Shuffle deliberately does not affect this. Under a random Next,
        walking back up the list order takes you somewhere you have
        never been, which is not what "previous" means to anyone - so
        Prev pops the playback history instead. Only when that history
        is empty does it fall back to the station above this one in the
        list, which is the sensible reading of Prev on a fresh start.
        """
        while self._back_stack:
            station_id = self._back_stack.pop()
            idx = self._index_of_id(station_id)
            # A station removed since it was played is skipped over
            # rather than dead-ending Prev.
            if idx < 0 or idx == self.current_index:
                continue
            self._skip_chain = 0
            self._suppress_back_push = True
            try:
                self._play_index(idx)
            finally:
                self._suppress_back_push = False
            return

        idx = self._next_index(-1)
        if idx >= 0:
            self._skip_chain = 0
            self._play_index(idx)

    def _sync_station_toggles(self, station):
        """Mirror the per-station option onto the toolbar toggle."""
        self.shazam_btn.blockSignals(True)
        self.shazam_btn.setChecked(bool(station and station.get("shazam_auto")))
        self.shazam_btn.setEnabled(station is not None)
        self.shazam_btn.blockSignals(False)

    def _on_shazam_toggled(self, checked):
        """Toolbar toggle -> the station's own setting, and vice versa."""
        station = self._current_station()
        if station is None or bool(station.get("shazam_auto")) == bool(checked):
            return
        station["shazam_auto"] = bool(checked)
        storage.save_stations(self.stations)
        self._sync_shazam_timer()
        self.notify(f"Song identification {'on' if checked else 'off'} for "
            f"{display_name(station)}.", 3)

    def _on_shuffle_toggled(self, checked):
        self.settings["shuffle"] = bool(checked)
        storage.save_settings(self.settings)
        # The Next button advertises what it will actually do. Prev's
        # label never changes, because its behavior never does.
        self.next_btn.setText("\U0001f500\u23ed" if checked else "\u23ed")
        self.next_btn.setToolTip(
            "Jump to a random station in the list" if checked
            else "Go to the next station in the list"
        )

    # ------------------------------------------------------------------
    # Player state
    # ------------------------------------------------------------------
    def _mpris_ready(self):
        """True once the bridge exists and registered. Called from slots
        that can fire during construction, before it does."""
        return self.mpris is not None and self.mpris.registered

    def _mpris_play_pause(self):
        """The Play/Pause key is one button, so it toggles."""
        if self.is_playing:
            self.stop_playback()
        else:
            self.play_current_or_selected()

    def _raise_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _push_mpris(self):
        """Tell the desktop what's playing, for its media popup."""
        if not self._mpris_ready():
            return
        station = self._current_station()
        if station is None:
            self.mpris.update_status("Stopped")
            self.mpris.update_now_playing()
            return
        track = self._shazam_track or {}
        self.mpris.update_status("Playing" if self.is_playing else "Stopped")
        self.mpris.update_now_playing(
            station_name=display_name(station),
            title=track.get("title") or self.panel.raw_text().split("\n")[0]
            or display_name(station),
            artist=track.get("artist", ""),
            art_url=track.get("cover_url", ""),
        )

    def _on_player_state(self, state, message):
        self.is_playing = state == PlayerState.PLAYING
        # What the skinned window's little glyph should show. Connecting
        # gets an ellipsis rather than a play arrow: the stream has not
        # started, and claiming otherwise is a lie told for as long as
        # the connection takes.
        if state == PlayerState.PLAYING and not getattr(self, "is_playing", False):
            self._playing_since = time.monotonic()
        elif state == PlayerState.STOPPED:
            self._playing_since = None

        self._classic_state = (
            "connecting" if state == PlayerState.CONNECTING
            else "play" if state == PlayerState.PLAYING
            else "stop"
        )
        self._refresh_classic_state()
        self._push_mpris()
        self._update_now_playing_label(state, message)

        if state == PlayerState.PLAYING:
            self._connect_attempts = 0
            self._skip_chain = 0
            if self.current_index >= 0:
                station = self.stations[self.current_index]
                storage.mark_ok(self.health, station["id"])
                storage.save_health(self.health)
                self._refresh_row_labels()
                self._refresh_monitor_targets()
            self._sync_shazam_timer()

        elif state == PlayerState.ERROR:
            self._shazam_timer.stop()
            self.notify(message or "Playback error", 6)
            self._handle_connect_failure(message)

        elif state == PlayerState.STOPPED:
            self._shazam_timer.stop()

        self._suppress_auto_skip = False

    def _handle_connect_failure(self, message):
        """Retry a few times, then flag the station and move on."""
        if self._suppress_auto_skip or self.current_index < 0:
            return

        station = self.stations[self.current_index]
        tries = int(self.settings.get("auto_skip_after_tries", 3))
        self._connect_attempts += 1

        storage.mark_failed(
            self.health, station["id"], reason=message, down_after=tries
        )
        storage.save_health(self.health)
        self._refresh_row_labels()
        self._refresh_monitor_targets()

        if self._connect_attempts < tries:
            self.notify(f"{display_name(station)}: attempt {self._connect_attempts} of "
                f"{tries} failed, retrying...", 4)
            QTimer.singleShot(1500, self._retry_current)
            return

        if not self.settings.get("auto_skip_broken", True):
            return

        # Give up on this one. Don't loop forever if everything is down.
        self._skip_chain += 1
        if self._skip_chain >= max(1, len(self.stations)):
            self.notify("No reachable stations found - stopped.", 8)
            self._skip_chain = 0
            return

        nxt = self._next_index(1)
        if nxt >= 0 and nxt != self.current_index:
            self.notify(f"{display_name(station)} is unreachable - skipping to the "
                f"next station.", 6)
            skipped = self._skip_chain
            self._play_index(nxt)
            self._skip_chain = skipped

    def _retry_current(self):
        if self.current_index < 0:
            return
        station = self.stations[self.current_index]
        # The EQ belongs to the station, so it has to be set before the
        # stream starts rather than left over from whatever played last.
        self._apply_station_eq(station)
        self.player.set_stall_timeout(self.settings.get("stall_timeout", 6))

        if storage.is_youtube_station(station):
            # Static first, then silence the outgoing station.
            #
            # Resolving takes two network round-trips, and the previous
            # stream must not carry on underneath for the whole of it -
            # but stopping the player also stops the static, so the
            # order matters and stop() is told to leave the noise alone.
            # Started before the stop, so there is never a moment with
            # neither playing.
            wants_noise = self.settings.get("noise_between_stations", True)
            if wants_noise:
                self.player.noise.start()
            self.player.stop(keep_noise=True)
            # Started again after the stop as well as before it.
            #
            # start() is a no-op when the static is already running, so
            # this costs nothing - but it guarantees the static is
            # going once the player has finished tearing the old stream
            # down, whatever that teardown touched on the way.
            if wants_noise:
                self.player.noise.start()
            self._start_youtube_resolve(station)
            self._refresh_display()
            self._mark_playing_row()
            self.settings["last_station_id"] = station["id"]
            storage.save_settings(self.settings)
            return

        url, start_fraction = self._resolve_playback(station)
        if url is None:
            self.notify(f"{display_name(station)}: no playable audio files in "
                f"{station.get('local_path') or 'that folder'}.", 6)
            return
        self.player.play(
            url,
            vocal_clarity=station.get("vocal_clarity"),
            gain_db=station.get("gain_db", 0.0),
            insecure=station.get("insecure", False),
            buffer_seconds=self._buffer_for(station),
            crossfade_ms=self.settings.get("crossfade_ms", 700),
            radio_quality=station.get("radio_quality", False),
            enhance=station.get("enhance_quality", False),
            start_fraction=start_fraction,
            **self._ad_override_args(station),
        )

    def _refresh_tray(self):
        self._update_tray_label()

    def _mark_playing_row(self):
        """Move the "now playing" highlight without rebuilding the list.

        A full _reload_list() here would lose the scroll position and
        any in-progress selection every time the station changed.
        """
        current_id = None
        if 0 <= self.current_index < len(self.stations):
            current_id = self.stations[self.current_index]["id"]
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            item.setData(
                StationItemDelegate.PLAYING_ROLE,
                item.data(Qt.UserRole) == current_id,
            )
        self.list_widget.viewport().update()

    def _update_now_playing_label(self, state=None, message=""):
        if not (0 <= self.current_index < len(self.stations)):
            self.now_playing_label.setText("Not playing")
            return
        name = display_name(self.stations[self.current_index])
        if state == PlayerState.CONNECTING:
            self.now_playing_label.setText(f"Connecting: {name}")
        elif state == PlayerState.PLAYING:
            self.now_playing_label.setText(f"▶ Playing: {name}")
        elif state == PlayerState.ERROR:
            self.now_playing_label.setText(f"⚠ {name} - {message}")
        elif state == PlayerState.STOPPED:
            self.now_playing_label.setText(f"■ Stopped: {name}")
        else:
            self.now_playing_label.setText(name)

    def _weighted_choice(self, ids, allow_custom=True):
        """Pick a station for shuffle, honouring the local/stream ratio.

        With the ratio at 0 (the default) selection is uniform, so a
        library of 200 streams and 2 local stations lands on a local one
        about 1% of the time. Set it to 30 and roughly three in ten
        shuffles pick one of your own stations regardless of how
        outnumbered they are - which is the point, since local stations
        are few by nature but you may want to hear them often.
        """
        ratio = int(self.settings.get("local_station_ratio", 0) or 0)
        by_id = {s["id"]: s for s in self.stations}
        local = [i for i in ids if storage.is_custom_station(by_id.get(i))]
        streams = [i for i in ids if i not in local]

        # Two things stop the ratio drifting upward in practice.
        #
        # Never twice running: custom stations are few, so landing on
        # one right after another is far more noticeable than the same
        # thing happening among two hundred streams - and it is what
        # makes a 7% setting feel like 20%.
        if not allow_custom or self._just_played_custom():
            if streams:
                return random.choice(streams)

        # Only meaningful when there is something of each to choose.
        if ratio <= 0 or not local or not streams:
            return random.choice(ids)
        pool = local if random.random() < (ratio / 100.0) else streams
        return random.choice(pool)

    def _just_played_custom(self):
        if not (0 <= self.current_index < len(self.stations)):
            return False
        return storage.is_custom_station(self.stations[self.current_index])

    def _ad_override_args(self, station):
        """Arguments that cover this station's pre-roll ad, if asked.

        Never applied to a custom station: those have no adverts, and
        covering one with music from another would be absurd.
        """
        if not station.get("ad_override") or storage.is_custom_station(station):
            return {}
        seconds = float(station.get("ad_seconds", 0) or 0)
        if seconds <= 0:
            return {}
        if station.get("ad_filler_kind") == "stream":
            url = self._pick_filler_stream(station)
            if not url:
                return {}
            # A live stream has no meaningful start position - you join
            # it wherever it happens to be.
            return {"ad_seconds": seconds, "filler_path": url,
                    "filler_fraction": None}

        path, fraction = self._pick_filler(station)
        if not path:
            # Nothing to cover it with. Play the station normally rather
            # than pretending; the static alone would just be a longer
            # silence with noise over it.
            return {}
        return {"ad_seconds": seconds, "filler_path": path,
                "filler_fraction": fraction}

    def _pick_filler_stream(self, station):
        """Another station's stream, to play while this one's ad runs.

        Deliberately never this station: opening a second connection to
        the same stream would just start the same advert again. A
        nominated station is used when set, otherwise any other working
        stream - excluding custom stations, which have no live URL.
        """
        wanted = station.get("ad_filler_url", "").strip()
        if wanted:
            return wanted

        nominated = station.get("ad_filler_station_id", "")
        if nominated:
            for candidate in self.stations:
                if candidate["id"] == nominated and candidate.get("url"):
                    if not storage.is_custom_station(candidate):
                        return candidate["url"]

        candidates = [
            st for st in self.stations
            if st["id"] != station["id"]
            and not storage.is_custom_station(st)
            and st.get("url")
            and not self.health.get(st["id"], {}).get("down")
        ]
        if not candidates:
            return ""
        return random.choice(candidates)["url"]

    def _pick_filler(self, station):
        """(path, start_fraction) of something to play over the advert.

        Comes from a custom station - the user's own music - because
        that is the only audio the app can be sure it may play. A
        specific station can be nominated, otherwise any custom station
        with local files will do.

        YouTube stations are not used as fillers: resolving one takes
        two network round-trips, and the whole point here is to have
        something playing immediately.
        """
        wanted = station.get("ad_filler_station_id", "")
        candidates = [
            st for st in self.stations
            if storage.is_local_station(st) and st.get("local_path")
        ]
        if wanted:
            candidates = [st for st in candidates if st["id"] == wanted] or candidates
        if not candidates:
            return None, None

        source = random.choice(candidates)
        track = storage.pick_local_track(
            source.get("local_path", ""),
            exclude=self._last_local_track.get(source["id"]),
        )
        if not track:
            return None, None
        self._last_local_track[source["id"]] = track
        return track, random.uniform(0.0, 0.6)

    def _resolve_playback(self, station):
        """(url, start_fraction) for a station.

        A local station resolves to a random track from its folder,
        joined at a random point in its first two-thirds - so you land
        mid-song the way you would tuning into a broadcast, but not so
        near the end that it changes track immediately.
        """
        if not storage.is_local_station(station):
            return station["url"], None

        previous = self._last_local_track.get(station["id"])
        track = storage.pick_local_track(station.get("local_path", ""),
                                         exclude=previous)
        if not track:
            return None, None
        self._last_local_track[station["id"]] = track
        return track, random.uniform(0.0, 0.66)

    @staticmethod
    def _buffer_for(station):
        """Seconds to pre-buffer, or None to leave mpv's default alone.

        An enabled setting with no explicit value falls back to the
        worst connect time this station has actually shown - which is
        the number the user would have picked anyway, without having to
        watch a stopwatch.
        """
        if not station.get("buffer_enabled"):
            return None
        seconds = float(station.get("buffer_seconds") or 0.0)
        if seconds <= 0:
            seconds = float(station.get("connect_worst") or 0.0)
        return seconds if seconds > 0 else None

    def _health_flow_refresh(self):
        """Keep whatever the list is reading from in step with health."""
        if hasattr(self, "_health_cache"):
            self._health_cache = dict(self.health)

    def _start_youtube_resolve(self, station):
        """Kick off resolution for a YouTube custom station."""
        if self._youtube_worker is not None:
            # A newer request supersedes one still running.
            try:
                self._youtube_worker.resolved.disconnect()
                self._youtube_worker.failed.disconnect()
            except Exception:
                pass

        worker = workers.YouTubeResolver(
            station["id"],
            station.get("playlist_url", ""),
            os.path.dirname(storage.settings_path()),
            exclude_id=self._last_youtube_video.get(station["id"], ""),
            parent=self,
        )
        worker.resolved.connect(self._on_youtube_resolved)
        worker.failed.connect(self._on_youtube_failed)
        self._youtube_worker = worker
        worker.start()
        self.notify(f"{display_name(station)}: finding a track...", 4)

    def _on_youtube_resolved(self, station_id, url, fraction, title,
                             thumbnail=""):
        idx = self._index_of_id(station_id)
        # The user may have moved on while this was in flight.
        if idx < 0 or idx != self.current_index:
            return
        station = self.stations[idx]
        # Remember the video so the next pick avoids an immediate repeat.
        self._last_youtube_video[station_id] = url.split("?")[0][-24:]
        if title:
            # The video's own title is the best "now playing" a YouTube
            # station has - there is no ICY metadata to read, and it is
            # what the favorite button will capture too.
            self._stream_text = title
        self._stream_art = thumbnail or ""
        self._refresh_display()
        self._update_tray_label()
        self._refresh_classic()
        self.player.play(
            url,
            vocal_clarity=station.get("vocal_clarity"),
            gain_db=station.get("gain_db", 0.0),
            insecure=station.get("insecure", False),
            buffer_seconds=self._buffer_for(station),
            crossfade_ms=self.settings.get("crossfade_ms", 700),
            radio_quality=station.get("radio_quality", False),
            enhance=station.get("enhance_quality", False),
            start_fraction=fraction,
        )

    def _on_youtube_failed(self, station_id, message):
        idx = self._index_of_id(station_id)
        if idx < 0:
            return
        station = self.stations[idx]
        self.player.noise.fade_out_and_stop(
            self.settings.get("crossfade_ms", 700)
        )
        storage.mark_failed(self.health, station_id, message, down_after=1)
        storage.save_health(self.health)
        self._reload_list()
        self.notify(f"{display_name(station)}: {message}", 8)

    def _on_track_finished(self):
        """A local station's track ended - queue up the next one.

        Replayed through _play_index so the whole station treatment
        applies again: static crossfade, a fresh random track, and a
        fresh random entry point.
        """
        if not (0 <= self.current_index < len(self.stations)):
            return
        station = self.stations[self.current_index]
        if not storage.is_custom_station(station):
            return
        self._play_index(self.current_index)

    # -- classic Winamp skin ---------------------------------------------

    def _choose_skin(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a Winamp skin", "",
            "Winamp skins (*.wsz *.zip);;All files (*)",
        )
        if not path:
            return
        try:
            skin = winampskin.load_skin(path)
        except winampskin.SkinError as exc:
            QMessageBox.warning(self, "Skin", str(exc))
            return

        self._skin = skin
        self.settings["skin_path"] = path
        storage.save_settings(self.settings)
        self.notify(f"Loaded skin: {skin.name}", 5)
        self.skin_show_action.setChecked(True)

    def _open_skin_locker(self):
        """The skin folder, as a list you can flip through."""
        folder = self.settings.get("skin_folder", "")
        if not folder and self.settings.get("skin_path"):
            folder = os.path.dirname(self.settings["skin_path"])

        dlg = SkinLockerDialog(
            self, folder=folder, current=self.settings.get("skin_path", ""),
            eq_fills=self.settings.get("skin_eq_fill") or {},
        )
        dlg.eq_fill_changed.connect(self._on_eq_fill_changed)
        dlg.skin_chosen.connect(self._on_skin_chosen)
        dlg.exec_()

        # Remember where they keep them, so it opens there next time.
        if dlg.folder() != self.settings.get("skin_folder", ""):
            self.settings["skin_folder"] = dlg.folder()
            storage.save_settings(self.settings)

    def _on_eq_fill_changed(self, skin_name, on):
        storage.set_eq_fill_for_skin(self.settings, skin_name, on)
        storage.save_settings(self.settings)
        if (self._classic_eq is not None
                and getattr(self._skin, "name", "") == skin_name):
            self._classic_eq.eq_fill = bool(on)
            self._classic_eq.update()

    def _on_skin_chosen(self, path):
        if not path:
            self._clear_skin()
            return
        try:
            skin = winampskin.load_skin(path)
        except winampskin.SkinError as exc:
            QMessageBox.warning(self, "Skin", str(exc))
            return

        self._skin = skin
        self.settings["skin_path"] = path
        storage.save_settings(self.settings)
        self.skin_show_action.setEnabled(True)
        # No message for a skin change.
        #
        # With shuffle on this fires on every track, and a notification
        # that says only what you can already see is noise.

        self._rebuild_classic_stack()

    def _clear_skin(self):
        self._close_classic_window()
        self._skin = None
        self.settings["skin_path"] = ""
        storage.save_settings(self.settings)
        self.skin_show_action.setChecked(False)

    def _set_skin_scale(self, factor):
        """Change the skin's size - all three windows, not just one.

        Rebuilt rather than resized: each window is a fixed size derived
        from the scale, and every hit target with it.

        The equalizer and playlist have to be rebuilt too. Rescaling
        only the player left them at the old size inside a stack sized
        for the new one, which is the mismatched windows with grey
        beside them - and it looked like double size "not going back",
        because going back rescaled just as partially.
        """
        self.settings["skin_scale"] = int(factor)
        storage.save_settings(self.settings)
        if self._classic is None:
            return

        self._rebuild_classic_stack()

    def _edit_artist_overrides(self, artist=""):
        """Open the corrections editor, prefilled where it makes sense.

        Passed the artist currently showing, so fixing a wrong profile
        starts on the entry for that artist rather than an empty list
        you then have to search.
        """
        if not isinstance(artist, str):
            artist = ""
        if not artist:
            track = self._shazam_track or {}
            artist = (track.get("artist") or "").strip()

        import artistinfo
        from dialogs import ArtistOverridesDialog

        dialog = ArtistOverridesDialog(self, artist=artist)
        dialog.exec_()
        # Corrections change what a lookup returns, so anything already
        # fetched has to go - otherwise the fix appears to do nothing
        # until the cache expires.
        artistinfo.forget_cached_bios()
        self._refresh_display()

    def _browse_skin_museum(self):
        """The skin museum, with downloads pointed at the skin folder."""
        import webview

        folder = self.settings.get("skin_folder", "")
        if not folder:
            folder = QFileDialog.getExistingDirectory(
                self, "Where should downloaded skins go?",
                os.path.expanduser("~"),
            )
            if not folder:
                return
            self._save_setting("skin_folder", folder)

        if not webview.is_available():
            QMessageBox.information(
                self, "Skin museum",
                "This needs QtWebEngine, which isn't installed in this "
                "build.",
            )
            return

        # Run in a thread so the player keeps playing while you browse.
        #
        # The helper is a separate process either way, but waiting on it
        # from the GUI thread would freeze this window for as long as
        # the museum is open.
        worker = QThread(self)
        worker.run = lambda: webview.browse_skins(folder)
        worker.finished.connect(lambda: self._after_museum(folder))
        worker.start()
        self._museum_worker = worker

    def _after_museum(self, folder):
        """Offer whatever turned up, once the window is closed."""
        count = len(self._available_skins())
        self.notify(f"{count} skin(s) in {folder}", 8)

    def _open_whole_stack(self):
        """Player, equalizer, tags and playlist, in one go.

        Each panel is opened by calling its toggle directly rather than
        by checking its menu action. setChecked does nothing when the
        action is already checked, and after a panel has been closed by
        hand its action can still be checked - so the tick said open
        and nothing appeared.
        """
        for key in ("skin_show_eq", "skin_show_tags", "skin_show_playlist"):
            self.settings[key] = True
        storage.save_settings(self.settings)

        if self._classic is None:
            self.skin_show_action.setChecked(True)

        for window, toggle, action in (
            (self._classic_eq, self._toggle_classic_eq,
             self.skin_eq_action),
            (self._classic_tags, self._toggle_classic_tags,
             self.skin_tags_action),
            (self._classic_playlist, self._toggle_classic_playlist,
             self.skin_playlist_action),
        ):
            if window is None:
                toggle(True)
            action.blockSignals(True)
            action.setChecked(True)
            action.blockSignals(False)

        if self._classic_group is not None:
            self._classic_group.refit()

    def _available_skins(self):
        """Every .wsz in the skin folder, plus the one in use."""
        paths = []
        folder = self.settings.get("skin_folder", "")
        if folder and os.path.isdir(folder):
            # Case-insensitive, so "Zebra" does not sort above "apple".
            for name in sorted(os.listdir(folder), key=str.lower):
                if name.lower().endswith((".wsz", ".zip")):
                    paths.append(os.path.join(folder, name))
        current = self.settings.get("skin_path", "")
        if current and current not in paths and os.path.exists(current):
            paths.append(current)
        return paths

    def _shuffle_skin(self):
        """Pick a different skin, if shuffling is on and there is one.

        Called when you press play or next, not when a stream connects.
        Connecting happens on retries, on stream hiccups and on the
        app's own reconnects, none of which you asked for - so tying
        the skin to it meant the window rebuilding at moments that felt
        random.

        It also excludes the skin already showing: picking at random
        from the whole set means landing on the current one now and
        then, and a shuffle that visibly does nothing looks broken.
        """
        if not self.settings.get("skin_shuffle"):
            return
        choices = [path for path in self._available_skins()
                   if path != self.settings.get("skin_path", "")]
        if not choices:
            return
        self._on_skin_chosen(random.choice(choices))

    def _rebuild_classic_stack(self):
        """Close and reopen the whole stack, keeping what was open.

        Shared by changing scale and changing skin: both need every
        window rebuilt, and both got it wrong in their own way when they
        each did it themselves.
        """
        if self._classic is None:
            return

        # The dock window is kept, and only its contents replaced.
        #
        # Under Wayland a client cannot position its own top-level
        # window - move() is ignored, by design - so a rebuilt stack is
        # placed wherever the compositor decides, and no amount of
        # restoring coordinates afterwards can override that. The only
        # way to stay put is to never close the window in the first
        # place. The panels inside it are swapped instead.
        self._rebuilding = True
        if self._classic_group is not None:
            self._classic_group.rebuilding = True

        # What is open is decided by the windows themselves, not the
        # menu actions.
        #
        # Closing a panel makes its action fire setChecked(False), so
        # reading the actions after the first rebuild reported nothing
        # open - and the equaliser and playlist never came back. The
        # windows are the truth here.
        had_eq = self._classic_eq is not None
        had_playlist = self._classic_playlist is not None
        had_tags = self._classic_tags is not None

        # Toggling the actions off and on rebuilds each panel at the
        # new scale, through the same path the menu uses.
        # Unchecked as well as closed.
        #
        # _open_classic_window reopens the stack with setChecked(True),
        # which does nothing when the action is still checked - so a
        # panel closed directly here never came back. Clearing the
        # actions first means the reopen actually fires.
        for action in (self.skin_eq_action, self.skin_tags_action,
                       self.skin_playlist_action):
            action.blockSignals(True)
            action.setChecked(False)
            action.blockSignals(False)

        self._toggle_classic_eq(False)
        self._toggle_classic_tags(False)
        self._toggle_classic_playlist(False)
        self._close_classic_window()

        # Record what should come back, then let _open_classic_window
        # restore it. Opening the panels here as well made a second set
        # of each - the player already reopens the stack from these
        # settings, so doing it twice means two equalisers.
        self.settings["skin_show_eq"] = had_eq
        self.settings["skin_show_playlist"] = had_playlist
        # The tag window was left out of this, so it never came back
        # after a change of scale.
        self.settings["skin_show_tags"] = had_tags
        storage.save_settings(self.settings)

        self._open_classic_window()

        if self._classic_group is not None:
            self._classic_group.refit()
        self._rebuilding = False
        if self._classic_group is not None:
            self._classic_group.rebuilding = False
            self._classic_group.refit()

    def _sync_skin_menu(self):
        current = int(self.settings.get("skin_scale", 2))
        for factor, action in self.skin_scale_actions.items():
            action.setChecked(factor == current)
        self.skin_show_action.setEnabled(self._skin is not None)

    def _toggle_classic_window(self, show):
        if show:
            self._open_classic_window()
            return

        # Closing the player closes the stack.
        #
        # It used to close only the player, leaving the equaliser, tags
        # and playlist on screen with nothing driving them. The
        # visualizer is deliberately not touched: it has its own window
        # and its own menu entry, and works with the skin closed.
        for action in (self.skin_eq_action, self.skin_tags_action,
                       self.skin_playlist_action):
            action.setChecked(False)
        self._close_classic_window()

    def _open_classic_window(self):
        if self._skin is None or self._classic is not None:
            return
        window = ClassicWindow(
            self._skin, parent=self._classic_dock(),
            scale=int(self.settings.get("skin_scale", 2)),
        )
        # The same handlers MPRIS uses, so the skinned window, the
        # media keys and the main window can never disagree.
        window.on_play = self.play_current_or_selected
        # Pause stops, deliberately.
        #
        # Pausing a live stream holds a buffer that is stale the moment
        # you resume, and most servers drop the connection anyway - so
        # "pause" that appears to work and then plays five minutes of
        # yesterday is worse than a stop that says what it did.
        window.on_pause = self.stop_playback
        window.on_menu = self._classic_titlebar_menu
        # Closing the skinned player quits.
        #
        # It is the only window on screen while it is up, so its close
        # button is the application's close button - leaving the app
        # running invisibly would be a surprise.
        window.on_close_all = self._quit_from_skin
        window.on_logo = self._back_to_main_view
        # The star is the same favourite toggle the main window shows.
        window.on_favourite = lambda on: self.favorite_btn.setChecked(on)
        window.on_history = self._show_history
        window.set_favourite(self.favorite_btn.isChecked())
        window.on_clutter = self._classic_clutter
        window.on_clock_mode = lambda on: self._save_setting(
            "clock_24_hour", on
        )
        window.on_vis_mode = lambda mode: self._save_setting(
            "skin_vis_mode", mode
        )
        window.vis_mode = self.settings.get("skin_vis_mode", "bars")
        window.vis_gain = float(self.settings.get("visualizer_gain", 1.0))
        # Saved on click but never read back, so the clock reverted to
        # 12-hour every launch however it had been left.
        window.twenty_four_hour = bool(self.settings.get("clock_24_hour"))
        # Light D when already at double size.
        #
        # Rescaling rebuilds this window, and the fresh one started with
        # every clutterbar lamp off - so D read as "not doubled" while
        # the stack plainly was, and pressing it asked for double size
        # again instead of going back.
        window.clutter_state["d"] = (
            int(self.settings.get("skin_scale", 2)) > 1
        )
        window.on_stop = self.stop_playback
        window.on_next = self.play_next
        window.on_previous = self.play_previous
        # Eject opened a file dialog in Winamp; here it opens the three
        # ways of getting a new station, which is the same idea.
        window.on_eject = self._classic_eject_menu
        window.on_shuffle = self._on_shuffle_toggled
        window.on_tune = self._classic_tune
        window.on_scan = self._classic_scan
        window.on_volume = self.volume_slider.setValue
        window.closed.connect(self._on_classic_closed)
        window.set_volume(self.volume_slider.value())
        window.set_shuffle(bool(self.settings.get("shuffle", False)))
        self._classic = window

        # The readouts have to be polled.
        #
        # mpv does not know the bitrate until it has decoded a little,
        # so reading once when playback starts always gave zero - which
        # is why the boxes stayed empty. Two seconds is often enough,
        # and cheap enough to keep doing.
        # The analyser needs the same audio the visualizer uses.
        self._start_skin_tap()

        if self._classic_info_timer is None:
            self._classic_info_timer = QTimer(self)
            self._classic_info_timer.setInterval(2000)
            self._classic_info_timer.timeout.connect(self._poll_stream_info)
        self._classic_info_timer.start()

        if self._marquee_timer is None:
            self._marquee_timer = QTimer(self)
            self._marquee_timer.setInterval(8000)
            self._marquee_timer.timeout.connect(self._advance_marquee)
        self._marquee_timer.start()

        self._classic_dock().add_panel(window)
        self._refresh_classic()
        self._refresh_classic_eq()
        self._refresh_classic_playlist()
        window.show()

        # The stack comes up whole.
        #
        # Winamp remembered which windows were open; opening only the
        # player left the equaliser and playlist to be found in a menu,
        # which is not how anyone used it.
        if self.settings.get("skin_show_eq", True):
            self.skin_eq_action.setChecked(True)
        # Tags before the playlist, so the dock stacks them in that
        # order - the tag window is designed to sit above it.
        if self.settings.get("skin_show_tags", True):
            self.skin_tags_action.setChecked(True)
        if self.settings.get("skin_show_playlist", True):
            self.skin_playlist_action.setChecked(True)

        # One window or the other, never both.
        #
        # They are two faces of the same player, and having both on
        # screen means two sets of controls for one thing - which is
        # confusing to look at and confusing to keep in step. The main
        # window steps aside while the skin is up.
        self._classic_dock().show()
        self.hide()

    def _classic_dock(self):
        """The single window the skinned panels live in.

        Created lazily because they are opened independently and in any
        order - whichever appears first brings the dock into being. It
        closes itself when the last panel leaves.
        """
        if self._classic_group is None:
            dock = SkinDock()
            dock.closed.connect(self._on_dock_closed)
            self._classic_group = dock
        return self._classic_group

    def _on_dock_closed(self):
        self._classic_group = None
        self._classic = None
        self._classic_eq = None
        self._classic_playlist = None
        for action in (self.skin_show_action, self.skin_eq_action,
                       self.skin_playlist_action):
            action.setChecked(False)

    def _toggle_classic_eq(self, show):
        self._save_setting("skin_show_eq", bool(show))
        if not show:
            if self._classic_eq is not None:
                window, self._classic_eq = self._classic_eq, None
                window.closed.disconnect()
                if self._classic_group is not None:
                    self._classic_group.remove_panel(window)
                window.close()
            return
        if self._skin is None or not self._skin.has_equalizer():
            self.notify("That skin has no eqmain.bmp, so it has no equalizer.", 6)
            self.skin_eq_action.setChecked(False)
            return
        window = EqualizerWindow(
            self._skin, parent=self._classic_dock(),
            scale=int(self.settings.get("skin_scale", 2)),
        )
        window.eq_fill = storage.eq_fill_for_skin(
            self.settings, getattr(self._skin, "name", "")
        )
        window.on_change = self._on_classic_eq_changed
        window.close_requested.connect(
            lambda: self.skin_eq_action.setChecked(False)
        )
        window.reattach_requested.connect(
            lambda w=window: self._classic_dock().add_panel(w)
        )
        window.extra_menu = self._fill_skin_menu
        window.extra_menu = self._fill_skin_menu
        window.extra_menu = self._fill_skin_menu
        window.closed.connect(
            lambda: self.skin_eq_action.setChecked(False)
        )
        self._classic_eq = window
        self._classic_dock().add_panel(window)
        self._refresh_classic_eq()
        window.show()
        # One window or the other, never both.
        #
        # They are two faces of the same player, and having both on
        # screen means two sets of controls for one thing - which is
        # confusing to look at and confusing to keep in step. The main
        # window steps aside while the skin is up.
        self._classic_dock().show()
        self.hide()

    def _on_classic_eq_changed(self, enabled, preamp, bands, simple=None):
        """The skinned EQ edits the playing station's advanced bands.

        Same setting as the main window's ten sliders, not a parallel
        one - the skin is a different face on the app, not a different
        app.
        """
        if not (0 <= self.current_index < len(self.stations)):
            return
        station = self.stations[self.current_index]
        self.settings["eq_advanced"] = bool(enabled)
        storage.save_settings(self.settings)
        station["eq_bands"] = storage.normalize_eq_bands(bands)
        if simple is not None and len(simple) >= 3:
            station["eq_bass"], station["eq_mid"], station["eq_treble"] = (
                int(simple[0]), int(simple[1]), int(simple[2])
            )
        # The preamp is the station's gain trim - the one thing in this
        # window that is not a band.
        station["gain_db"] = max(-20.0, min(20.0, float(preamp)))
        storage.save_stations(self.stations)
        self._apply_station_eq(station)
        self.player.set_gain_db(station["gain_db"])

    def _refresh_classic_eq(self):
        if self._classic_eq is None:
            return
        if 0 <= self.current_index < len(self.stations):
            station = self.stations[self.current_index]
            self._classic_eq.set_state(
                bool(self.settings.get("eq_advanced")),
                storage.normalize_eq_bands(station.get("eq_bands")),
                int(station.get("gain_db", 0) or 0),
                simple=[
                    int(station.get("eq_bass", 0) or 0),
                    int(station.get("eq_mid", 0) or 0),
                    int(station.get("eq_treble", 0) or 0),
                ],
            )

    def _toggle_classic_tags(self, show):
        """The skinned tag window, above the playlist in the stack."""
        self._save_setting("skin_show_tags", bool(show))
        if not show:
            if self._classic_tags is not None:
                window, self._classic_tags = self._classic_tags, None
                window.close_requested.disconnect()
                if self._classic_group is not None:
                    self._classic_group.remove_panel(window)
                window.close()
            return
        if self._skin is None or self._classic_tags is not None:
            return

        window = TagWindow(
            self._skin, scale=int(self.settings.get("skin_scale", 2)),
            parent=self._classic_dock(),
        )
        window.tag_clicked.connect(self._on_tag_clicked)
        window.close_requested.connect(
            lambda: self.skin_tags_action.setChecked(False)
        )
        window.reattach_requested.connect(
            lambda w=window: self._classic_dock().add_panel(w)
        )
        self._classic_tags = window
        self._classic_dock().add_panel(window)
        self._refresh_classic_tags()

    def _refresh_classic_tags(self):
        """Feed it the same tags and states the rail shows."""
        if self._classic_tags is None:
            return
        tags = [(tag, storage.tag_color(self.settings, tag))
                for tag in storage.all_tags(self.stations)]
        self._classic_tags.set_tags(tags)
        self._classic_tags.set_states(
            storage.tag_filter_states(self.search_box.text())
        )

        # Grow to the rows the chips actually need, rather than sitting
        # at a fixed height with empty space under one row of tags or
        # cutting off the rest when there are many.
        window = self._classic_tags
        # Asked of the window itself rather than recomputed here.
        #
        # This used 16 pixels a row against a layout that steps 17, and
        # left no room for the bottom border - so with enough tags the
        # last row fell outside the window and simply vanished.
        wanted = window.height_for_rows(window.rows_needed())
        if wanted != window.skin_height:
            window.resize_skin(window.skin_width, wanted)
            if self._classic_group is not None:
                self._classic_group.refit()

    def _toggle_classic_playlist(self, show):
        self._save_setting("skin_show_playlist", bool(show))
        if not show:
            if self._classic_playlist is not None:
                window, self._classic_playlist = self._classic_playlist, None
                window.closed.disconnect()
                if self._classic_group is not None:
                    self._classic_group.remove_panel(window)
                window.close()
            return
        if self._skin is None or not self._skin.has_playlist():
            self.notify("That skin has no pledit.bmp, so it has no playlist window.", 6)
            self.skin_playlist_action.setChecked(False)
            return
        # Between a list too short to read and one taller than most
        # screens. The saved value is clamped rather than trusted.
        height = max(140, min(560, int(
            self.settings.get("skin_playlist_height", 232)
        )))
        window = PlaylistWindow(
            self._skin, parent=self._classic_dock(),
            height=height,
            scale=int(self.settings.get("skin_scale", 2)),
        )
        window.manual_sort = (
            self.settings.get("sort_mode", "manual") == "manual"
        )
        window.rows_reordered.connect(self._on_classic_reorder)
        window.transport.connect(self._on_classic_transport)
        window.on_resized = lambda _w, h: self._save_setting(
            "skin_playlist_height", int(h)
        )
        window.station_activated.connect(self._on_classic_playlist_pick)
        window.station_menu.connect(self._on_classic_playlist_menu)
        window.search_changed.connect(self.search_box.setText)
        window.set_search_text(self.search_box.text())
        window.close_requested.connect(
            lambda: self.skin_playlist_action.setChecked(False)
        )
        window.reattach_requested.connect(
            lambda w=window: self._classic_dock().add_panel(w)
        )
        self._wire_classic_playlist(window)
        window.closed.connect(
            lambda: self.skin_playlist_action.setChecked(False)
        )
        self._classic_playlist = window
        self._classic_dock().add_panel(window)
        self._refresh_classic_playlist()
        window.show()
        # One window or the other, never both.
        #
        # They are two faces of the same player, and having both on
        # screen means two sets of controls for one thing - which is
        # confusing to look at and confusing to keep in step. The main
        # window steps aside while the skin is up.
        self._classic_dock().show()
        self.hide()

    def _on_classic_playlist_pick(self, row):
        """Rows are positions in the visible list, as everywhere else."""
        visible = getattr(self, "_visible", None) or self.stations
        if 0 <= row < len(visible):
            index = self._index_of_id(visible[row]["id"])
            if index >= 0:
                self._play_index(index)

    def _refresh_classic_playlist(self):
        if self._classic_playlist is None:
            return
        visible = getattr(self, "_visible", None) or self.stations
        current = -1
        if 0 <= self.current_index < len(self.stations):
            playing_id = self.stations[self.current_index]["id"]
            current = next(
                (i for i, st in enumerate(visible) if st["id"] == playing_id),
                -1,
            )
        self._classic_playlist.set_stations(
            [display_name(st) for st in visible], current
        )

    def _close_classic_window(self):
        if self._classic is None:
            return
        self._stop_skin_tap()
        if self._marquee_timer is not None:
            self._marquee_timer.stop()
        window, self._classic = self._classic, None
        window.closed.disconnect()
        if self._classic_group is not None:
            self._classic_group.remove_panel(window)
        window.close()

    def _on_classic_closed(self):
        self._classic = None
        self.skin_show_action.setChecked(False)
        # Never leave the user with no window at all.
        if not self.isVisible():
            self._restore_from_tray()

    def _back_to_main_view(self):
        """Swap the skinned player for the ordinary window."""
        self.skin_show_action.setChecked(False)
        self._restore_from_tray()
        self.raise_()
        self.activateWindow()

    def _show_main_from_classic(self):
        self._restore_from_tray()

    def _toggle_visualizer(self):
        """The V button: open or close the visualizer window.

        It opens a window either way. If projectM is unavailable the
        window says so instead of rendering - which is better than a
        status message claiming the visualizer is "on" while nothing
        appears anywhere, as it did before.
        """
        existing = getattr(self, "_visualizer", None)
        if existing is not None and existing.isVisible():
            existing.close()
            self._visualizer = None
            self._save_setting("visualizer", False)
            return

        from visualizer import VisualizerWindow

        # Skinned to match the stack, at the same scale.
        self._visualizer = VisualizerWindow(
            skin=self._skin,
            scale=int(self.settings.get("skin_scale", 2)),
            source=self.settings.get("visualizer_source") or None,
            gain=float(self.settings.get("visualizer_gain", 1.0)),
            disabled=self.settings.get("visualizer_disabled") or (),
        )
        self._visualizer.source_changed.connect(
            lambda name: self._save_setting("visualizer_source", name)
        )
        # One sensitivity for both: the analyser beside the clock reads
        # the same audio, so a level set in one place should hold in the
        # other rather than needing setting twice.
        self._visualizer.gain_changed.connect(self._set_vis_gain)
        self._visualizer.disabled_changed.connect(
            lambda paths: self._save_setting("visualizer_disabled",
                                             list(paths))
        )
        self._visualizer.show()
        self._visualizer.raise_()
        self._save_setting("visualizer", True)

    def _skin_menu(self, near=None):
        """A QMenu parented to whichever skinned window it belongs to.

        Wayland positions a popup relative to its PARENT surface, not
        the screen. These menus were built with QMenu(self) - self being
        the main window - so however carefully the coordinates were
        worked out, the compositor placed them relative to a different
        window entirely. That is the menus appearing hundreds of pixels
        to the left.
        """
        parent = self
        if near is not None:
            parent = near
        elif self._classic is not None:
            parent = self._classic
        return QMenu(parent)

    def _menu_anchor(self, at=None):
        """Where a menu opened from the skinned window should appear.

        `at` is the point the caller was clicked, already in global
        coordinates via the window's own mapToGlobal. Without it this
        used the window's centre, which is why menus appeared away from
        the thing that opened them - above and to one side, always by
        the same amount.
        """
        if at is not None:
            return at
        window = self._classic
        if window is not None:
            return window.mapToGlobal(QPoint(0, window.height()))
        return QCursor.pos()

    def _on_accent_toggled(self, on):
        self._save_setting("accent_from_art", bool(on))
        if not on:
            # Back to the default amber rather than leaving whatever
            # the last track happened to set.
            import theme
            self._accent = None
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet(theme.stylesheet())
        else:
            self._accent_from_art(getattr(self.panel, "_art_pixmap", None))

    def _accent_from_art(self, pixmap):
        """Retint the interface from whatever is playing.

        Restyling the whole application is not free, so this only runs
        when the color actually changes - most tracks by the same
        artist land on the same accent, and restyling on every artwork
        would flicker the window for no visible gain.
        """
        if not self.settings.get("accent_from_art", True):
            return
        try:
            import theme
        except Exception:  # noqa: BLE001
            return

        if pixmap is None or pixmap.isNull():
            colour = theme.T["accent"]
        else:
            colour = theme.dominant_color(pixmap.toImage()).name()

        if colour == getattr(self, "_accent", None):
            return
        self._accent = colour
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.stylesheet(colour))

    def notify(self, text, seconds=5):
        """Say something where the user will actually see it.

        The desktop's own notification service first, through
        notify-send. The tray balloon only works when a tray icon
        exists, which it does not unless minimise-to-tray is on - so
        messages were quietly going nowhere whenever the skinned
        player was up and that setting was off.
        """
        if not text:
            return

        # The status bar, when there is one on screen to read.
        if self.isVisible() and not self.isMinimized():
            self.statusBar().showMessage(text, seconds * 1000)
            return

        if self._send_desktop_notification(text, seconds):
            return

        tray = getattr(self, "_tray", None)
        if tray is not None and tray.isVisible():
            icon = app_icon()
            if icon.isNull():
                tray.showMessage(APP_TITLE, text,
                                 QSystemTrayIcon.Information, seconds * 1000)
            else:
                tray.showMessage(APP_TITLE, text, icon, seconds * 1000)
            return

        self.statusBar().showMessage(text, seconds * 1000)

    def _send_desktop_notification(self, text, seconds):
        """Hand it to the desktop. True if that worked."""
        if shutil.which("notify-send") is None:
            return False
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "freqamp.png")
        command = [
            "notify-send",
            "--app-name", APP_TITLE,
            "--expire-time", str(max(1, seconds) * 1000),
        ]
        if os.path.exists(icon):
            command += ["--icon", icon]
        command += [APP_TITLE, text]
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            return False
        return True

    def _save_setting(self, key, value):
        self.settings[key] = value
        storage.save_settings(self.settings)

    def _fill_skin_menu(self, menu):
        """The common entries, shared by every skinned title bar."""
        menu.addAction("Show the main window", self._show_main_from_classic)
        menu.addSeparator()
        menu.addAction("Equalizer",
                       lambda: self.skin_eq_action.setChecked(
                           not self.skin_eq_action.isChecked()))
        menu.addAction("Playlist",
                       lambda: self.skin_playlist_action.setChecked(
                           not self.skin_playlist_action.isChecked()))
        menu.addAction("Tags",
                       lambda: self.skin_tags_action.setChecked(
                           not self.skin_tags_action.isChecked()))
        menu.addAction("Visualizer", self._toggle_visualizer)
        menu.addSeparator()
        menu.addAction("Skins...", self._open_skin_locker)
        menu.addAction("Options...", self._show_main_from_classic)

    def _classic_titlebar_menu(self, at=None):
        """The menu behind the icon in the skin's title bar."""
        menu = self._skin_menu()
        menu.addAction("Show the main window", self._show_main_from_classic)
        menu.addSeparator()
        menu.addAction("Equalizer",
                       lambda: self.skin_eq_action.setChecked(
                           not self.skin_eq_action.isChecked()))
        menu.addAction("Playlist",
                       lambda: self.skin_playlist_action.setChecked(
                           not self.skin_playlist_action.isChecked()))
        menu.addAction("Tags",
                       lambda: self.skin_tags_action.setChecked(
                           not self.skin_tags_action.isChecked()))
        menu.addSeparator()
        menu.addAction("Skins...", self._open_skin_locker)
        menu.addAction("Options...", self._show_main_from_classic)
        menu.addSeparator()
        menu.addAction("Close the skinned player",
                       lambda: self.skin_show_action.setChecked(False))
        menu.exec_(self._menu_anchor(at))

    def _classic_clutter(self, name, active, at=None):
        """The O A I D V strip down the left of the visualizer."""
        if name == "o":
            self._classic_titlebar_menu()
        elif name == "a":
            # Apple Music for whatever is playing.
            #
            # This was always-on-top, which Wayland gives a client no
            # way to ask for - so the button either did nothing or lied.
            # A link that works is better than a toggle that cannot.
            track = self._shazam_track or {}
            url = (track.get("apple_music_url") or "").strip()
            if url:
                webbrowser.open(url, new=1, autoraise=True)
            else:
                self.notify("No Apple Music link for this track yet.", 4)
        elif name == "i":
            # Winamp's file-info box. The nearest thing here is the
            # station's own options, which is where its details live.
            station = self._current_station()
            if station is not None:
                index = self._index_of_id(station["id"])
                if index >= 0:
                    self._station_options(index)
        elif name == "d":
            self._set_skin_scale(2 if active else 1)
        elif name == "v":
            self._toggle_visualizer()

    def _set_vis_gain(self, gain):
        self._save_setting("visualizer_gain", float(gain))
        if self._classic is not None:
            self._classic.vis_gain = float(gain)
            self._classic.update()

    def _start_skin_tap(self):
        """Feed the main window's analyzer from the output monitor."""
        if self._skin_tap is not None:
            return
        import audiotap

        def deliver(samples):
            # Straight onto the window: set_audio only stores and asks
            # for a repaint, both of which are cheap and safe enough
            # from the capture thread.
            window = self._classic
            if window is not None:
                window.set_audio(samples)

        tap = audiotap.AudioTap(
            deliver, self.settings.get("visualizer_source") or None
        )
        self._skin_tap = tap if tap.start() else None

    def _stop_skin_tap(self):
        if self._skin_tap is not None:
            self._skin_tap.stop()
            self._skin_tap = None

    def _advance_marquee(self):
        """Alternate between the station and the song.

        Winamp scrolled one long line; two short ones read better on a
        bar this narrow, and it means the station is never permanently
        pushed off the end by a long title.
        """
        if self._classic is None or len(self._marquee_cycle) < 2:
            return
        self._marquee_index += 1
        self._classic.set_title(
            self._marquee_cycle[self._marquee_index % len(self._marquee_cycle)]
        )

    def _poll_stream_info(self):
        if self._classic_playlist is not None:
            started = getattr(self, "_playing_since", None)
            self._classic_playlist.elapsed = (
                int(time.monotonic() - started) if started else 0
            )
            self._classic_playlist.update()

        if self._classic is None:
            return
        bitrate, samplerate = self.player.stream_info()
        self._classic.set_stream_info(bitrate, samplerate)

    def _classic_tune(self, index):
        """The dial stopped on a station: play it."""
        visible = getattr(self, "_visible", None) or self.stations
        if 0 <= index < len(visible):
            position = self._index_of_id(visible[index]["id"])
            if position >= 0:
                self._play_index(position)

    def _classic_scan(self):
        """Static while the dial is moving.

        A receiver hisses between stations, and the dial should sound
        like one - so the noise starts on the first movement and the
        stream is silenced under it until the dial settles.
        """
        if not self.settings.get("noise_between_stations", True):
            return
        self.player.noise.start()
        self.player.stop(keep_noise=True)

    def _classic_eject_menu(self, at=None):
        """The eject button: three ways to add a station."""
        menu = QMenu(self)
        menu.addAction("Add a station...", self._add_station_menu)
        menu.addAction("Search radio-browser...", self._open_search)
        menu.addSeparator()
        menu.addAction("Show the main window", self._show_main_from_classic)
        menu.exec_(self._menu_anchor(at))

    def _wire_classic_playlist(self, window):
        """Hook the skinned playlist's buttons up to the real actions."""
        window.add_requested.connect(self._classic_add)
        window.remove_requested.connect(self._classic_remove)
        window.select_requested.connect(self._classic_select)
        window.misc_requested.connect(self._classic_misc)
        window.list_requested.connect(self._classic_list)

    def _classic_add(self, what):
        if what == "url":
            self._add_custom_station()
        elif what == "browse":
            self._open_search()
        else:
            self._add_local_station()

    def _classic_remove(self, what):
        if what == "all":
            if QMessageBox.question(
                self, "Remove all",
                f"Remove all {len(self.stations)} stations?",
            ) != QMessageBox.Yes:
                return
            self.stations = []
        elif what == "dead":
            before = len(self.stations)
            self.stations = [
                st for st in self.stations
                if not self.health.get(st["id"], {}).get("down")
            ]
            self.notify(f"Removed {before - len(self.stations)} down station(s).", 5)
        else:
            station = self._current_station() or self._selected_station_or_none()
            if station is None:
                return
            self.stations = [st for st in self.stations
                             if st["id"] != station["id"]]

        storage.save_stations(self.stations)
        self._reload_list()
        self._refresh_classic_playlist()

    def _on_classic_playlist_menu(self, row, where, source=None):
        """Right-click on a playlist row: act on that station."""
        visible = getattr(self, "_visible", None) or self.stations
        if not (0 <= row < len(visible)):
            return
        station = visible[row]
        index = self._index_of_id(station["id"])
        if index < 0:
            return

        menu = self._skin_menu(source)
        menu.addAction("Play", lambda: self._play_index(index))
        menu.addAction("Station options...",
                       lambda: self._station_options(index))
        menu.addAction("Tags...", lambda: self._edit_tags(index))
        menu.addSeparator()
        menu.addAction("Remove", lambda: self._remove_station_at(index))
        menu.exec_(where)

    def _remove_station_at(self, index):
        if not (0 <= index < len(self.stations)):
            return
        name = display_name(self.stations[index])
        del self.stations[index]
        storage.save_stations(self.stations)
        self._reload_list()
        self._refresh_classic_playlist()
        self.notify(f"Removed {name}.", 5)

    def _classic_select(self, what):
        if what == "tags":
            self._edit_tags_selected()
            return
        if what == "all":
            self.list_widget.selectAll()
        elif what == "none":
            self.list_widget.clearSelection()
        else:
            for row in range(self.list_widget.count()):
                item = self.list_widget.item(row)
                item.setSelected(not item.isSelected())

    def _classic_misc(self, what, at=None):
        if what == "sort":
            # The same sort choices as the main window's hamburger.
            menu = self._skin_menu(self._classic_playlist)
            for mode in storage.SORT_MODES:
                menu.addAction(
                    storage.SORT_LABELS[mode],
                    lambda m=mode: self._set_sort_mode(m),
                )
            menu.exec_(self._menu_anchor(at))
        else:
            station = self._current_station() or self._selected_station_or_none()
            if station is not None:
                index = self._index_of_id(station["id"])
                if index >= 0:
                    self._station_options(index)

    def _classic_list(self, what):
        if what == "backup":
            self._backup()
        else:
            self._restore()

    def _marquee_parts(self):
        """[station, what is playing] - whichever of those we know.

        Any source counts. Shazam is preferred because it names the
        artist as well, but a title the stream reported itself is real
        information and belongs in the bar; without it a station that
        publishes its own metadata showed nothing but its own name.
        """
        if not (0 <= self.current_index < len(self.stations)):
            return ["Frequencies"]

        station = display_name(self.stations[self.current_index])
        track = self._shazam_track or {}
        artist = (track.get("artist") or "").strip()
        title = (track.get("title") or "").strip()

        if artist and title:
            song = f"{artist} - {title}"
        elif title:
            song = title
        else:
            song = (self._stream_text or "").strip()

        return [station] + ([song] if song else [])

    def _refresh_classic(self):
        """Keep the skinned window in step with the real one."""
        if self._classic is None:
            return

        parts = self._marquee_parts()
        if parts != self._marquee_cycle:
            # Reset to the station whenever what is playing changes, so
            # a new song is announced rather than appearing whenever the
            # rotation next comes round.
            self._marquee_cycle = parts
            self._marquee_index = 0

        self._classic.set_title(parts[self._marquee_index % len(parts)])

        self._classic.set_playing(bool(getattr(self, "is_playing", False)))
        self._refresh_classic_state()
        bitrate, samplerate = self.player.stream_info()
        self._classic.set_stream_info(bitrate, samplerate)

        # The dial scans the visible list, in the order it is shown.
        visible = getattr(self, "_visible", None) or self.stations
        current = -1
        if 0 <= self.current_index < len(self.stations):
            playing_id = self.stations[self.current_index]["id"]
            current = next(
                (i for i, st in enumerate(visible) if st["id"] == playing_id),
                -1,
            )
        self._classic.set_dial(
            [display_name(st) for st in visible], current
        )
        self._classic.set_volume(self.volume_slider.value())
        self._classic.set_shuffle(bool(self.settings.get("shuffle", False)))

    def _on_thumbnails_toggled(self, on):
        self.settings["station_thumbnails"] = bool(on)
        storage.save_settings(self.settings)
        self._apply_thumbnail_mode()


    def _apply_thumbnail_mode(self):
        """Grid of artwork, or rows of names.

        Only a station's OWN art counts here - a custom image or the
        station's favicon. Deliberately not the last thing Shazam
        identified: that is a picture of one song that happened to be
        playing, and using it as the station's face would mean the grid
        rearranged itself into a different set of album covers every few
        minutes.
        """
        on = bool(self.settings.get("station_thumbnails"))
        if on:
            self.list_widget.setViewMode(QListWidget.IconMode)
            self.list_widget.setIconSize(QSize(96, 96))
            self.list_widget.setGridSize(QSize(112, 1))
            self.list_widget.setResizeMode(QListWidget.Adjust)
            self.list_widget.setWordWrap(True)
            self.list_widget.setSpacing(4)
        else:
            self.list_widget.setViewMode(QListWidget.ListMode)
            self.list_widget.setIconSize(QSize(0, 0))
            self.list_widget.setGridSize(QSize())
            self.list_widget.setSpacing(0)
        self._reload_list()

    def station_thumbnail(self, station):
        """The station's own artwork, or None.

        Custom image first, then the favicon that came with the station.
        Nothing else: see _apply_thumbnail_mode for why the Shazam cover
        is excluded.
        """
        path = (station.get("custom_art_path") or "").strip()
        if path and os.path.exists(path):
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                return QIcon(pixmap)

        favicon = (station.get("favicon") or "").strip()
        if favicon:
            cached = self._favicon_cache.get(favicon)
            if cached is not None:
                return cached
        return None

    def _build_output_menu(self):
        """Rebuilt each time it opens.

        Devices appear and disappear - headphones, a USB interface, a
        Bluetooth speaker - so a list built once at startup is wrong by
        the time anyone looks at it.
        """
        self.output_menu.clear()
        group = QActionGroup(self)
        group.setExclusive(True)
        current = self.settings.get("audio_device", "auto")

        for name, description in self.player.audio_devices():
            action = QAction(description, self)
            action.setCheckable(True)
            action.setChecked(name == current)
            action.triggered.connect(
                lambda _c, n=name: self._set_audio_device(n)
            )
            group.addAction(action)
            self.output_menu.addAction(action)

    def _set_audio_device(self, name):
        self.settings["audio_device"] = name
        storage.save_settings(self.settings)
        self.player.set_audio_device(name)
        self.notify(f"Audio output: {name}", 4)

    # -- system tray ----------------------------------------------------

    def _build_tray(self):
        """The notification-area icon and its menu.

        Built once and kept, shown or hidden by the setting. The menu is
        deliberately short: what's playing, transport, and quit. Anyone
        who wants more opens the window.
        """
        if self._tray is not None:
            return self._tray

        tray = QSystemTrayIcon(self)
        # The app's own icon, explicitly. windowIcon() is empty unless
        # something set it, and an empty tray icon is an invisible tray
        # entry rather than a missing one - which is a far more
        # confusing thing to be given.
        icon = app_icon()
        if icon.isNull():
            icon = self.windowIcon()
        if icon.isNull():
            icon = QIcon.fromTheme("audio-x-generic")
        tray.setIcon(icon)
        tray.setToolTip(APP_TITLE)

        menu = QMenu()
        self._tray_now_playing = menu.addAction("Not playing")
        self._tray_now_playing.setEnabled(False)
        menu.addSeparator()

        self._tray_play = menu.addAction("Play / Pause")
        # The same handler MPRIS and the skinned window use, so every
        # face of the app agrees on what "play" means.
        self._tray_play.triggered.connect(self.play_current_or_selected)
        menu.addAction("Next station").triggered.connect(self.play_next)
        menu.addAction("Previous station").triggered.connect(self.play_previous)
        menu.addSeparator()
        menu.addAction("Show window").triggered.connect(self._restore_from_tray)
        menu.addAction("Quit").triggered.connect(self._quit_from_tray)

        tray.setContextMenu(menu)
        # Left-click (or middle-click on some desktops) toggles the
        # window, which is what every other tray app does.
        tray.activated.connect(self._on_tray_activated)
        self._tray = tray
        return tray

    def _apply_tray_setting(self):
        wanted = bool(self.settings.get("tray_icon", False))
        if wanted and not QSystemTrayIcon.isSystemTrayAvailable():
            self.notify("This desktop has no system tray available.", 6)
            return
        if wanted:
            self._build_tray().show()
        elif self._tray is not None:
            self._tray.hide()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.MiddleClick):
            if self.isVisible():
                self.hide()
            else:
                self._restore_from_tray()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_skin(self):
        """The skinned player's close button: shut everything down."""
        self._really_quitting = True
        if self._visualizer is not None:
            self._visualizer.close()
        self.skin_show_action.setChecked(False)
        QApplication.instance().quit()

    def _quit_from_tray(self):
        """Actually quit, rather than hiding again.

        closeEvent hides the window when minimize-to-tray is on, so the
        tray's own Quit needs a way to say "no, really" - otherwise the
        app becomes impossible to exit from the tray, which is a
        genuinely infuriating bug to ship.
        """
        self._really_quitting = True
        self.close()

    def _update_tray_label(self):
        if self._tray is None:
            return
        if 0 <= self.current_index < len(self.stations):
            name = display_name(self.stations[self.current_index])
            song = (self._stream_text or "").strip()
            label = f"{name} - {song}" if song else name
        else:
            label = "Not playing"
        self._tray_now_playing.setText(label[:80])
        self._tray.setToolTip(f"{APP_TITLE}\n{label}"[:120])

    def _on_tray_toggled(self, enabled):
        self.settings["tray_icon"] = bool(enabled)
        storage.save_settings(self.settings)
        self._apply_tray_setting()
        # Closing to a tray that isn't there would strand the window.
        self.minimize_action.setEnabled(bool(enabled))
        if not enabled and self.settings.get("minimize_to_tray"):
            self.minimize_action.setChecked(False)

    def _on_minimize_toggled(self, enabled):
        self.settings["minimize_to_tray"] = bool(enabled)
        storage.save_settings(self.settings)

    def _on_artist_panel_toggled(self, enabled):
        self.settings["artist_panel"] = bool(enabled)
        storage.save_settings(self.settings)

    def _refresh_artist_panel(self, track):
        """Look up whoever is playing, if the panel is switched on."""
        if not self.settings.get("artist_panel", True):
            return
        artist = (track or {}).get("artist", "").strip()

        # No identification? Read the stream's own metadata.
        #
        # Shazam is not always listening and not always right, but a
        # station that publishes "Artist - Title" has already told us
        # who is playing. Which half is the artist is worked out in
        # artistinfo, by asking MusicBrainz rather than assuming an
        # order stations do not agree on.
        if not artist:
            artist, _title = artistinfo.split_artist_title(
                (self._stream_text or "").strip()
            )

        # Still nothing, so there may be no artist at all - a talk or
        # news station. Read about the station instead.
        if not artist:
            self._show_station_article()
            return

        if artist == self._artist_shown:
            return

        self._artist_shown = artist
        # bio None = still looking; {} = looked and found nothing.
        self._artist_info = {"artist": artist, "bio": None}
        self._render_artist_panel()


        worker = workers.ArtistInfoWorker(
            artist,
            title=(track or {}).get("title", ""),
            parent=self,
        )
        worker.biography_ready.connect(self._on_artist_bio)
        self._artist_worker = worker
        worker.start()

    def _show_station_article(self):
        """For stations with no song: the station's own article."""
        station = self._current_station()
        if station is None:
            return
        name = display_name(station)
        if name == self._artist_shown:
            return
        self._artist_shown = name
        self._artist_info = {"artist": name, "bio": None}
        self._render_artist_panel()

        worker = workers.StationInfoWorker(name, parent=self)
        worker.article_ready.connect(self._on_artist_bio)
        self._artist_worker = worker
        worker.start()

    def _on_artist_bio(self, artist, info):
        if artist != self._artist_shown:
            return
        # An empty dict means "looked, found nothing" - distinct from
        # None, which still means "still looking".
        self._artist_info["bio"] = info if info else {}
        self._render_artist_panel()


    def _render_artist_panel(self):
        info = self._artist_info or {}
        artist = info.get("artist", "")
        if not artist:
            return

        bio = info.get("bio") or {}
        parts = []

        # The article's lead image, from the copy on disk.
        #
        # A remote src cannot work here: Qt resolves image resources
        # itself and has no network stack, so http:// renders as the
        # broken-image glyph. The worker downloads it and hands over a
        # path, which Qt can actually open.
        photo = bio.get("photo_path") or ""
        if photo and os.path.exists(photo):
            parts.append(
                "<p><img src='file://%s' width='190'></p>" % _escape(photo)
            )

        parts.append(
            "<h3 style='margin-bottom:2px'>%s</h3>"
            % _escape(bio.get("name") or artist)
        )

        subtitle = " &middot; ".join(
            _escape(x) for x in (bio.get("area"), bio.get("years")) if x
        )
        if subtitle:
            parts.append("<p style='color:#888;margin-top:0'>%s</p>" % subtitle)
        if bio.get("tags"):
            parts.append(
                "<p style='color:#7aa'>%s</p>"
                % " &middot; ".join(_escape(t) for t in bio["tags"])
            )
        if bio.get("summary"):
            parts.append("<p>%s</p>" % _escape(bio["summary"]))
            if bio.get("url"):
                parts.append(
                    "<p><a href='%s'>Read more on Wikipedia</a></p>"
                    % _escape(bio["url"])
                )
        elif bio is not None:
            import artistinfo
            reason = getattr(artistinfo, "last_bio_error", "")
            if reason:
                # A lookup that failed is not the same as an artist
                # nobody has written about, and the two need different
                # things from whoever is reading.
                parts.append(
                    "<p style='color:#c88'>Couldn't reach the lookup "
                    "services.</p><p style='color:#888'>%s</p>"
                    % _escape(reason)
                )
            else:
                parts.append(
                    "<p style='color:#888'>No biography found for this "
                    "artist.</p>"
                )
        else:
            parts.append("<p style='color:#888'>Looking up...</p>")

        self.artist_view.setHtml("".join(parts))




    def _on_stream_connected(self, url, seconds):
        """Audio started. Remember how long it took for this station.

        `connect_worst` is the evidence behind the buffering suggestion:
        rather than asking the user to guess a number, the station's own
        worst observed connect time is offered.
        """
        idx = next((i for i, st in enumerate(self.stations)
                    if st.get("url") == url), -1)
        if idx < 0:
            return
        station = self.stations[idx]
        worst = float(station.get("connect_worst", 0.0) or 0.0)
        if seconds > worst:
            station["connect_worst"] = round(min(300.0, seconds), 1)
            storage.save_stations(self.stations)

    def _on_stream_stalled(self, url):
        """The stream went quiet and stayed quiet.

        The connection is still open and mpv still thinks it is playing,
        so nothing else notices - but no audio has arrived for a while,
        which for live radio means the station has died. Flag it and
        move on; the static covers the change, as it does for any other
        station switch.
        """
        idx = next((i for i, st in enumerate(self.stations)
                    if st.get("url") == url), -1)
        if idx < 0 or idx != self.current_index:
            return
        station = self.stations[idx]
        # down_after=1: this isn't a failed connection attempt, it's a
        # stream that was working and stopped. One is enough.
        storage.mark_failed(self.health, station["id"],
                            "Stream went silent", down_after=1)
        storage.save_health(self.health)
        self._health_flow_refresh()
        self._reload_list()
        self._refresh_monitor_targets()
        self.notify(f"{display_name(station)} went silent - moving on.", 5)
        # A recovery hop, not a choice.
        #
        # A run of dead stations used to roll the local/stream dice on
        # every hop, so a handful of failures in a row would reliably
        # land on a custom station. Recovering from a failure should be
        # looking for a working stream, not an excuse to play music.
        self.play_next(allow_custom=False)

    def _on_metadata(self, title):
        """ICY metadata from the stream itself.

        Ignored for YouTube stations. Their "stream" is a Googlevideo
        URL with no ICY metadata, and mpv reports fragments of the query
        string instead - which is where "webm&rqh=1&gir=yes&clen=..."
        was coming from. The video's own title, set when the track was
        resolved, is the right answer and must not be overwritten by it.
        """
        station = self._current_station()
        if station is not None and storage.is_youtube_station(station):
            return
        if _looks_like_stream_path(title, station):
            # mpv reports the mount point's filename before any real
            # metadata arrives, so the song line briefly read
            # "wbrh-mp3" or "KSANFMAAC.aac". That is the URL, not a
            # song, and showing it is worse than showing nothing.
            return
        self._stream_text = title or ""
        self._refresh_display()
        # The artist panel follows the stream's own metadata too.
        #
        # It was only ever refreshed from the Shazam handler, so on a
        # station Shazam had not identified the fallback never ran -
        # the parsing worked, nothing called it.
        self._refresh_artist_panel(self._shazam_track or {})
        # The marquee builds its text from this too, and nothing was
        # telling it - so stream-reported titles only ever reached the
        # skinned window if something else happened to refresh it.
        self._refresh_classic()

    # ------------------------------------------------------------------
    # Choosing what to show (art + song text)
    # ------------------------------------------------------------------
    # Both the artwork and the readout follow the same rule: try the
    # station's preferred source, and if that has nothing and fallback
    # is on, walk the remaining sources in a fixed order.
    # Artwork prefers Shazam's cover art, then a custom image, then the
    # station logo. The readout keeps custom first, because custom text
    # is an explicit label the user typed for this station.
    _ART_ORDER = ("shazam", "custom", "stream")
    # Two automatic orders for the song text. Both lead with Shazam,
    # since an identified track is the most specific thing we have; they
    # differ in the fallback when it hasn't matched anything yet.
    _READOUT_ORDER = ("shazam", "custom", "stream")
    _READOUT_ORDER_STREAM_FIRST = ("shazam", "stream", "custom")

    def _current_station(self):
        if 0 <= self.current_index < len(self.stations):
            return self.stations[self.current_index]
        return None

    def _resolve_order(self, preferred, fallback_enabled, order):
        """Turn a per-station preference into the list of sources to try.

        The automatic modes are just a fixed order. An explicit source
        is tried first, then - if fallback is on - the others in the
        order's own sequence.
        """
        if preferred in ("auto", "auto_stream"):
            return list(order)
        if not fallback_enabled:
            return [preferred]
        rest = [s for s in order if s != preferred]
        return [preferred] + rest

    def _readout_from(self, source, station):
        if source == "custom":
            return station.get("custom_readout", "").strip()
        if source == "shazam":
            track = self._shazam_track or {}
            title, artist = track.get("title", ""), track.get("artist", "")
            if not title and not artist:
                return ""
            return f"{title}\n{artist}" if artist else title
        if source == "stream":
            return (self._stream_text or "").strip()
        return ""

    def _art_source_value(self, source, station):
        """First candidate only. Kept for callers that want one."""
        candidates = self._art_source_values(source, station)
        return candidates[0] if candidates else (None, None)

    def _art_source_values(self, source, station):
        """Every (kind, value) worth trying for this source, in order.

        A source can offer more than one candidate, which is what makes
        "stream" actually produce artwork. Most directory entries carry
        no favicon - the field is simply empty for the majority of
        stations - so with only that to go on this source looked broken
        when it was merely empty-handed. The station's own site nearly
        always has an icon, and those are worth deriving.
        """
        if source == "custom":
            path = station.get("custom_art_path", "").strip()
            if path and os.path.exists(path):
                return [("path", path)]
            return []

        if source == "shazam":
            url = (self._shazam_track or {}).get("cover_url", "")
            return [("url", url)] if url else []

        if source == "stream":
            out = []
            # A YouTube station's art is the video's own thumbnail.
            if self._stream_art:
                out.append(("url", self._stream_art))
            favicon = (station.get("favicon") or "").strip()
            if favicon:
                out.append(("url", favicon))
            out.extend(("url", url) for url in self._site_icons(station))
            return out

        return []

    def _site_icons(self, station):
        """Icon URLs derived from the station's own website.

        Tried in descending order of quality: a touch icon is a real
        square logo, while favicon.ico is usually 16 pixels of nothing -
        but 16 pixels of the right logo beats a blank panel.
        """
        import urllib.parse

        homepage = (station.get("homepage") or "").strip()
        if not homepage:
            # No site recorded: the stream's own host is a fair guess.
            homepage = station.get("url", "")
        try:
            parts = urllib.parse.urlsplit(homepage)
        except Exception:  # noqa: BLE001
            return []
        if not parts.netloc or parts.scheme not in ("http", "https"):
            return []

        root = f"{parts.scheme}://{parts.netloc}"
        return [
            f"{root}/apple-touch-icon.png",
            f"{root}/apple-touch-icon-precomposed.png",
            f"{root}/favicon.png",
            f"{root}/favicon.ico",
        ]

    _SOURCE_LABELS = {"custom": "custom", "shazam": "Shazam", "stream": "stream"}

    def _refresh_display(self):
        """Recompute the readout and the artwork from whatever each
        source currently has."""
        station = self._current_station()
        if station is None:
            self.panel.reset()
            return

        # --- song text ---
        text, used = "", ""
        readout_source = station.get("readout_source", "auto")
        readout_order = (self._READOUT_ORDER_STREAM_FIRST
                         if readout_source == "auto_stream"
                         else self._READOUT_ORDER)
        for source in self._resolve_order(
            readout_source,
            station.get("readout_fallback", True),
            readout_order,
        ):
            candidate = self._readout_from(source, station)
            if candidate:
                text, used = candidate, self._SOURCE_LABELS[source]
                break

        # Nothing identified yet: say which station it is.
        #
        # A bare "-" while a stream connects tells the user nothing, and
        # the station's name is both true and useful - it is what they
        # just pressed. The source label is left blank, since this is
        # not a reading of the stream.
        if not text:
            text, used = display_name(station), ""

        self.panel.set_text(text or "-", used)

        # Apple Music link only makes sense when Shazam found something.
        track = self._shazam_track or {}
        self.panel.set_link(
            "Open in Apple Music", track.get("apple_music_url", ""),
            extra=[("Album of the Year",
                    aoty_search_url(track.get("artist", ""),
                                    track.get("album", "")
                                    or track.get("title", "")))],
        )

        # When the song came out, if Shazam said.
        self.panel.set_release_date(track.get("release_date", ""))

        # --- artwork ---
        for source in self._resolve_order(
            station.get("art_source", "auto"),
            station.get("art_fallback", True),
            self._ART_ORDER,
        ):
            for kind, value in self._art_source_values(source, station):
                if kind == "path":
                    if self.panel.set_art_path(value):
                        return
                elif kind == "url":
                    data = self._art_cache.get(value)
                    if data is not None:
                        if self.panel.set_art_bytes(data):
                            return
                        continue        # that one failed; try the next
                    self._load_art(value)
                    return              # the fetch will call us again
        self.panel.clear_art()

    def _load_art(self, url):
        if not url or url in self._art_cache:
            return
        worker = workers.ArtworkWorker("art", url, parent=self)
        worker.loaded.connect(self._on_art_loaded)
        worker.failed.connect(self._on_art_failed)
        worker.finished.connect(self._reap_art_workers)
        self._art_workers.append(worker)
        worker.start()

    def _reap_art_workers(self):
        self._art_workers = [w for w in self._art_workers if w.isRunning()]

    def _on_art_loaded(self, slot, url, data):
        self._art_cache[url] = data
        self._trim_art_cache()
        self._refresh_display()

    def _on_art_failed(self, slot, url):
        self._art_cache[url] = b""      # remember the miss, don't retry
        self._trim_art_cache()
        self._refresh_display()

    def _trim_art_cache(self, keep=40):
        """Cover art accumulates over a long session; keep it bounded."""
        if len(self._art_cache) > keep:
            for key in list(self._art_cache)[:-keep]:
                del self._art_cache[key]

    def _on_source_combo(self, _index):
        # Falls back to the selected station.
        #
        # This used to do nothing at all unless something was playing,
        # so changing "Show" while browsing looked broken - the setting
        # was never written and the panel never redrew.
        station = self._current_station() or self._selected_station_or_none()
        if station is None:
            return
        value = self.source_combo.currentData()
        if value == station.get("art_source") and value == station.get("readout_source"):
            return
        station["art_source"] = value
        station["readout_source"] = value
        storage.save_stations(self.stations)
        self._refresh_display()

    def _sync_source_combo(self, station):
        # The quick switch offers the four broad choices; the second
        # automatic order is a Display-tab refinement, so it shows here
        # simply as "Automatic".
        index = self.source_combo.findData(station.get("art_source", "auto"))
        self.source_combo.blockSignals(True)
        self.source_combo.setCurrentIndex(index if index >= 0 else 0)
        self.source_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Artwork context menu
    # ------------------------------------------------------------------
    def _art_menu(self, pos):
        station = self._current_station()
        if station is None:
            return

        menu = QMenu(self)
        current = station.get("art_source", "auto")

        source_actions = {}
        for label, value in (("Automatic", "auto"),
                             ("Shazam cover art", "shazam"),
                             ("Custom image", "custom"),
                             ("Station logo from the stream", "stream")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(current == value)
            source_actions[action] = value

        menu.addSeparator()
        choose_action = menu.addAction("Choose an image file...")
        clear_action = menu.addAction("Clear the custom image")
        clear_action.setEnabled(bool(station.get("custom_art_path")))

        chosen = menu.exec_(self.panel.art_label.mapToGlobal(pos))
        if chosen is None:
            return

        if chosen in source_actions:
            station["art_source"] = source_actions[chosen]
            storage.save_stations(self.stations)
            self._sync_source_combo(station)
            self._refresh_display()
        elif chosen is choose_action:
            self._pick_art_file(station)
        elif chosen is clear_action:
            station["custom_art_path"] = ""
            storage.save_stations(self.stations)
            self._refresh_display()

    def _pick_art_file(self, station):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose artwork", os.path.expanduser("~"),
            "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;All files (*)"
        )
        if not path:
            return
        station["custom_art_path"] = path
        station["art_source"] = "custom"
        storage.save_stations(self.stations)
        self._sync_source_combo(station)
        self._refresh_display()

    # ------------------------------------------------------------------
    # Lyrics
    # ------------------------------------------------------------------
    def _lyrics_position(self):
        """Where we think we are in the current song, in seconds.

        A radio stream has no position of its own. Shazam tells us how
        far into the track our sample landed, so position now = that
        offset + however long ago we sampled it.
        """
        if self._lyrics_anchor is None:
            return None
        offset, at = self._lyrics_anchor
        return offset + (time.time() - at)

    def _tick_lyrics(self):
        if self.lyrics_view.is_synced:
            self.lyrics_view.update_position(self._lyrics_position())

    def _maybe_fetch_lyrics(self, station, track):
        if not station.get("lyrics_enabled", True):
            return
        title = (track.get("title") or "").strip()
        artist = (track.get("artist") or "").strip()
        if not title:
            return
        key = f"{artist}|{title}".lower()
        if key == self._current_track_key:
            return                      # already have (or are fetching) these
        self._current_track_key = key

        # Anchor the lyric clock.
        #
        # Shazam's offset says where in the song our *sample* matched -
        # and that sample was taken before the lookup ran. Anchoring at
        # response time therefore ran the lyrics late by the whole
        # round trip: six seconds of recording plus however long the
        # network took, so ten seconds behind was normal.
        #
        # Anchoring on the capture time instead makes the arithmetic
        # right: position = offset + (now - captured_at).
        offset = track.get("match_offset")
        captured_at = track.get("captured_at")
        self._lyrics_anchor = (
            float(offset) if isinstance(offset, (int, float)) else 0.0,
            float(captured_at) if isinstance(captured_at, (int, float))
            else time.time(),
        )

        self.lyrics_view.clear_lyrics("Looking up lyrics...")
        worker = workers.LyricsWorker(station["id"], key, artist, title, parent=self)
        worker.loaded.connect(self._on_lyrics_loaded)
        worker.none_found.connect(self._on_lyrics_none)
        worker.failed.connect(self._on_lyrics_failed)
        self._lyrics_worker = worker
        worker.start()

    def _lyrics_still_wanted(self, station_id, track_key):
        return self._is_current(station_id) and track_key == self._current_track_key

    def _on_lyrics_loaded(self, station_id, track_key, result):
        if not self._lyrics_still_wanted(station_id, track_key):
            return
        if result.get("instrumental"):
            self.lyrics_view.clear_lyrics("(instrumental)")
            return

        # Prefer synced lyrics - that's what makes the live highlight
        # possible. Plain text is the fallback.
        timed = lyrics_module.parse_synced(result.get("synced", ""))
        if timed:
            self.lyrics_view.set_synced(timed)
            self._tick_lyrics()
        else:
            self.lyrics_view.set_plain(result.get("plain", ""))

    def _on_lyrics_none(self, station_id, track_key):
        if self._lyrics_still_wanted(station_id, track_key):
            self.lyrics_view.clear_lyrics("No lyrics found for this track.")

    def _on_lyrics_failed(self, station_id, track_key, message):
        """Show the reason, not the stack trace that produced it.

        The message now arrives pre-phrased by lyrics.py - "couldn't
        reach the lyrics server", say - instead of a wall of urllib3
        wrappers ending in "record layer failure (_ssl.c:1032)". That
        raw text made a network problem look like an application bug.
        """
        if self._lyrics_still_wanted(station_id, track_key):
            self.lyrics_view.clear_lyrics(message)

    def _reset_lyrics_state(self):
        """Called whenever a new station starts, so nothing from the
        last one lingers.

        The artist profile matters as much as the lyrics here: leaving
        the previous band's biography on screen while a
        different station plays is worse than an empty panel, because it
        reads as current information.
        """
        self._lyrics_anchor = None
        self._current_track_key = ""
        self.lyrics_view.clear_lyrics("")

        # A YouTube thumbnail belongs to one video, not the next.
        self._stream_art = ""


        self._artist_shown = ""
        self._artist_info = {}
        if self._artist_worker is not None and self._artist_worker.isRunning():
            # A lookup already in flight would otherwise land after the
            # switch and repopulate the panel with the old artist.
            try:
                self._artist_worker.biography_ready.disconnect()
            except TypeError:
                pass
        self.artist_view.setHtml(
            "<p style='color:#888'>Who you're listening to appears here "
            "once a song has been identified.</p>"
        )

    # ------------------------------------------------------------------
    # Song identification
    # ------------------------------------------------------------------
    def _sync_shazam_timer(self):
        """Start/stop the periodic identification for whatever is
        playing now, according to that station's own settings."""
        self._shazam_timer.stop()
        station = self._current_station()
        if station is None or not station.get("shazam_auto"):
            return
        interval = max(30, int(station.get("shazam_interval", 90)))
        self._shazam_timer.start(interval * 1000)
        self._run_shazam()

    def _run_shazam(self, manual=False):
        station = self._current_station()
        if station is None:
            if manual:
                self.notify("Nothing is playing.", 4)
            return
        if self._shazam_worker is not None and self._shazam_worker.isRunning():
            return  # one at a time

        # Fingerprint what is actually playing, not the station's URL.
        #
        # A custom station's URL is a "local://" or "youtube://" marker,
        # not audio - identification against it could never work. The
        # player knows the real source, so ask it.
        source = self.player.current_source() or station["url"]
        start_seconds = None
        if storage.is_custom_station(station):
            position = self.player.position_seconds()
            if position and position > 1:
                # Back off a little: capturing from exactly "now" races
                # the playhead and can catch the very edge of a track.
                start_seconds = max(0.0, position - 1.0)

        worker = workers.ShazamWorker(
            station["id"], source, parent=self, start_seconds=start_seconds
        )
        worker.identified.connect(self._on_shazam_identified)
        worker.no_match.connect(self._on_shazam_no_match)
        worker.failed.connect(self._on_shazam_failed)
        self._shazam_worker = worker
        worker.start()

    def _is_current(self, station_id):
        station = self._current_station()
        return station is not None and station["id"] == station_id

    def _on_shazam_identified(self, station_id, track):
        if not self._is_current(station_id):
            return
        station = self.stations[self.current_index]
        self._shazam_track = track

        # The skinned window builds its title from this, and nothing was
        # telling it the track had changed - so an identified song only
        # reached the marquee if something else happened to refresh it.
        self._refresh_classic()

        # Save it to the history list (capped, newest first).
        limit = storage.history_limit(self.settings)
        self.history, added = storage.add_history_entry(
            self.history, track, display_name(station), limit=limit
        )
        if added:
            storage.save_history(self.history)

        key = storage.track_key(track.get("artist"), track.get("title"))
        self._cache_thumbnail(key, track.get("cover_url"))

        self._refresh_display()
        self._sync_favorite_button()
        self._maybe_fetch_lyrics(station, track)
        self._refresh_artist_panel(track)
        self._push_mpris()

    def _refresh_classic_state(self):
        """Keep the play-state glyph honest."""
        if self._classic is None:
            return
        self._classic.set_state(self._classic_state)

    def _on_shazam_no_match(self, station_id):
        if not self._is_current(station_id):
            return
        # Keep whatever the stream reports rather than blanking the
        # readout - a failed match shouldn't lose working information.
        self._shazam_track = None
        self._refresh_display()
        self._sync_favorite_button()

    def _on_shazam_failed(self, station_id, message):
        if self._is_current(station_id):
            self._shazam_track = None
            self._refresh_display()
        self.notify(f"Song identification: {message}", 6)

    def _current_track_identity(self):
        """What's playing, identified or not.

        Shazam is the best answer but not the only one. Plenty of
        stations put "Artist - Title" in their ICY metadata, and even a
        bare stream title is worth being able to star - it's a record of
        something you liked hearing. Falling back means the button is
        useful on talk stations, obscure tracks and anything Shazam
        can't place, instead of being permanently greyed out.
        """
        track = dict(self._shazam_track or {})
        title = (track.get("title") or "").strip()
        artist = (track.get("artist") or "").strip()
        if title or artist:
            return storage.track_key(artist, title), track

        # Fall back to whatever the stream is announcing.
        stream = (self._stream_text or "").strip()
        if stream:
            artist, title = self._split_stream_title(stream)
            track = {"title": title, "artist": artist,
                     "cover_url": "", "apple_url": "", "shazam_url": "",
                     "unidentified": True}
            return storage.track_key(artist, title), track

        return None, None

    @staticmethod
    def _split_stream_title(text):
        """ICY titles are conventionally "Artist - Title"."""
        for separator in (" - ", " \u2013 ", " | "):
            if separator in text:
                left, right = text.split(separator, 1)
                left, right = left.strip(), right.strip()
                if left and right:
                    return left, right
        return "", text.strip()

    def _sync_favorite_button(self):
        """Reflect whether the song playing right now is starred."""
        key, _track = self._current_track_identity()
        self.favorite_btn.blockSignals(True)
        if key is None:
            self.favorite_btn.setChecked(False)
            self.favorite_btn.setEnabled(False)
            self.favorite_btn.setText("\u2606")
        else:
            starred = key in storage.favorite_keys(self.history)
            self.favorite_btn.setEnabled(True)
            self.favorite_btn.setChecked(starred)
            self.favorite_btn.setText("\u2605" if starred else "\u2606")
        self.favorite_btn.blockSignals(False)

    def _on_favorite_toggled(self, checked):
        key, track = self._current_track_identity()
        if key is None:
            return
        station = self._current_station()
        self.history, changed = storage.set_favorite(
            self.history, key, checked, track=track,
            station_name=display_name(station) if station else "",
        )
        if changed:
            storage.save_history(self.history)
        self.favorite_btn.setText("\u2605" if checked else "\u2606")
        name = track.get("title") or track.get("artist") or "This song"
        self.notify(f"{name} {'starred' if checked else 'unstarred'}.", 4)

    def _cache_thumbnail(self, key, url):
        """Keep a small local copy of the cover so the history window
        doesn't have to re-download everything each time."""
        if not key or not url or thumbs.has(key):
            return
        data = self._art_cache.get(url)
        if data:
            thumbs.save(key, data, limit_bytes=self._thumb_limit())
            return
        # Not downloaded yet - fetch it in the background purely for the
        # cache. The display path fetches independently.
        worker = workers.ArtworkWorker("thumb:" + key, url, parent=self)
        worker.loaded.connect(self._on_thumb_fetched)
        worker.finished.connect(self._reap_art_workers)
        self._art_workers.append(worker)
        worker.start()

    def _on_thumb_fetched(self, slot, url, data):
        if not slot.startswith("thumb:"):
            return
        thumbs.save(slot[6:], data, limit_bytes=self._thumb_limit())

    def _thumb_limit(self):
        try:
            mb = int(self.settings.get("thumbnail_cache_mb", 1024))
        except (TypeError, ValueError):
            mb = 1024
        return max(16, mb) * 1024 * 1024

    def _show_history(self):
        limit = storage.history_limit(self.settings)
        dlg = HistoryDialog(self, self.history, limit=limit)
        dlg.exec_()

        if dlg.favorites_changed():
            self.history = dlg.history()
            storage.save_history(self.history)
            self._sync_favorite_button()

        if dlg.was_cleared():
            kept = storage.count_favorites(self.history)
            self.history = storage.clear_history(self.history)
            storage.save_history(self.history)
            self.notify("Song history cleared."
                + (f" {kept} starred song(s) kept." if kept else ""), 6)
        if dlg.favorites_reset():
            self.history = storage.reset_favorites(self.history)
            storage.save_history(self.history)
            self._sync_favorite_button()
            self.notify("All favorites reset.", 6)

    # ------------------------------------------------------------------
    # Import / export with other radio apps
    # ------------------------------------------------------------------
    def _import_from_installed(self):
        """Look for other radio apps' station lists where they normally
        live, so the user doesn't have to go find the file."""
        found = playlists.discover_app_configs()
        if not found:
            QMessageBox.information(
                self, "Import",
                "No station lists from other apps turned up in the usual "
                "places.\n\nUse \"Import from a file...\" to point at one "
                "directly."
            )
            return

        labels = [f"{app} - {count} station(s)  ({path})"
                  for app, path, count in found]
        label, ok = QInputDialog.getItem(
            self, "Import from an installed app",
            "Found these station lists:", labels, 0, False
        )
        if not ok:
            return
        self._import_path(found[labels.index(label)][1])

    def _import_playlist(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import stations", os.path.expanduser("~"),
            "Station lists (*.m3u *.m3u8 *.pls *.xspf *.json *.xml *.audpl);;"
            "All files (*)"
        )
        if not path:
            return
        self._import_path(path)

    def _import_path(self, path):
        try:
            fmt, entries = playlists.read_file(path)
        except playlists.PlaylistError as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return

        known = {s.get("url") for s in self.stations}
        fresh = [e for e in entries if e.get("url") not in known]
        duplicates = len(entries) - len(fresh)

        subtitle = (f"Read {len(entries)} station(s) as "
                    f"{playlists.FORMATS.get(fmt, fmt)}.")
        if duplicates:
            subtitle += f" {duplicates} already in your list were left out."
        if not fresh:
            QMessageBox.information(
                self, "Import", subtitle + "\n\nNothing new to add."
            )
            return

        dlg = StationPickerDialog(
            self, title="Import stations", entries=fresh,
            subtitle=subtitle + " Tick the ones you want.",
            action_label="Import",
        )
        if not dlg.exec_():
            return
        chosen = dlg.selected()
        if not chosen:
            return
        for entry in chosen:
            self.stations.append(playlists.to_station(entry))
        storage.save_stations(self.stations)
        self._reload_list()
        self.notify(f"Imported {len(chosen)} station(s).", 8)

    def _export_playlist(self):
        if not self.stations:
            QMessageBox.information(self, "Export", "There's nothing to export.")
            return

        labels = [playlists.FORMATS[f] for f in playlists.EXPORT_FORMATS]
        label, ok = QInputDialog.getItem(
            self, "Export stations", "Save in which app's format?",
            labels, 0, False
        )
        if not ok:
            return
        fmt = playlists.EXPORT_FORMATS[labels.index(label)]

        dlg = StationPickerDialog(
            self, title="Export stations", entries=self.stations,
            subtitle="Tick the stations to include.",
            action_label="Export",
        )
        if not dlg.exec_():
            return
        chosen = dlg.selected()
        if not chosen:
            return

        default = os.path.join(
            os.path.expanduser("~"),
            "frequencies-stations" + playlists.default_extension(fmt),
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export stations", default, "All files (*)"
        )
        if not path:
            return
        try:
            playlists.write_file(path, chosen, fmt)
        except playlists.PlaylistError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.notify(f"Exported {len(chosen)} station(s) to {path}", 8)

    def _show_formats(self):
        lines = "\n".join(f"  -  {desc}" for desc in playlists.FORMATS.values())
        QMessageBox.information(
            self, "Supported formats",
            "Stations can be imported from and exported to:\n\n" + lines +
            "\n\nFormat is detected from the file's contents, not its "
            "extension, so a file saved with the wrong suffix still works."
        )

    # ------------------------------------------------------------------
    # Health / reachability
    # ------------------------------------------------------------------
    def _refresh_monitor_targets(self):
        """Only stations currently flagged down get background probes."""
        targets = [
            (s["id"], s["url"])
            for s in self.stations
            if self.health.get(s["id"], {}).get("down")
        ]
        self.health_monitor.set_targets(targets)

    def _apply_health_recheck_setting(self):
        # Called from _apply_loaded_settings, which runs before the
        # monitor thread is built. Nothing to configure yet; __init__
        # calls this again once it exists.
        if not hasattr(self, "health_monitor"):
            return

        """Turn the background re-checking on or off, at the chosen rate.

        A station that failed once is flagged down forever until
        something proves otherwise, which is why it's worth re-testing
        them periodically - stations come back. Off by default because
        it's network traffic the user didn't ask for.
        """
        enabled = bool(self.settings.get("health_recheck", False))
        minutes = max(1, int(self.settings.get("health_recheck_minutes", 30)))
        try:
            self.health_monitor.set_interval(minutes * 60)
        except AttributeError:
            pass
        self.health_monitor.set_enabled(enabled)
        if enabled:
            self._refresh_monitor_targets()

    def _is_unverified_down(self, station_id):
        """True when a station is flagged down and hasn't been re-tested.

        Next and shuffle skip these rather than dropping a known-bad
        stream into the middle of a listening session. The user can
        still play one deliberately - it's only automatic selection
        that avoids them.
        """
        entry = self.health.get(station_id) or {}
        if not entry.get("down"):
            return False
        checked = entry.get("last_check")
        if not checked:
            return True
        # A check older than the recheck interval doesn't count as
        # current evidence.
        window = max(1, int(self.settings.get("health_recheck_minutes", 30))) * 60
        return (time.time() - float(checked)) > window

    def _check_selected(self):
        idx, item = self._selected_station("Check station")
        if idx is None:
            return
        station = self.stations[idx]
        self.notify(f"Checking {display_name(station)}...", 4)
        check = workers.StreamCheck(station["id"], station["url"], parent=self)
        check.finished_check.connect(self._on_health_result)
        check.finished.connect(self._reap_checks)
        self._checks.append(check)
        check.start()

    def _reap_checks(self):
        self._checks = [c for c in self._checks if c.isRunning()]

    def _on_health_result(self, station_id, ok, reason):
        idx = self._index_of_id(station_id)
        if idx < 0:
            return
        station = self.stations[idx]
        was_down = self.health.get(station_id, {}).get("down", False)

        if ok:
            storage.mark_ok(self.health, station_id)
            if was_down:
                self.notify(f"{display_name(station)} is back.", 6)
        else:
            storage.mark_failed(
                self.health, station_id, reason=reason,
                down_after=int(self.settings.get("auto_skip_after_tries", 3)),
            )

        storage.save_health(self.health)
        self._refresh_row_labels()
        self._refresh_monitor_targets()

    # ------------------------------------------------------------------
    # Backup / restore
    # ------------------------------------------------------------------
    def _backup(self):
        default = os.path.join(
            os.path.expanduser("~"),
            time.strftime("frequencies-backup-%Y%m%d.json"),
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Back up library", default, "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            storage.export_backup(
                path, self.stations, self.settings, self.health,
                history=self.history,
                artist_overrides=storage.load_artist_overrides(),
                station_overrides=storage.load_station_overrides(),
            )
        except OSError as exc:
            QMessageBox.warning(self, "Backup failed", str(exc))
            return
        self.notify(f"Backed up {len(self.stations)} station(s) to {path}", 8)

    def _restore(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Restore library", os.path.expanduser("~"),
            "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            (stations, settings, health, history, overrides,
             station_overrides) = storage.read_backup(path)
        except storage.BackupError as exc:
            QMessageBox.warning(self, "Restore failed", str(exc))
            return

        box = QMessageBox(self)
        box.setWindowTitle("Restore library")
        box.setText(
            f"This backup holds {len(stations)} station(s).\n\n"
            "Replace your current library, or merge the new stations into it?"
        )
        replace_btn = box.addButton("Replace", QMessageBox.AcceptRole)
        merge_btn = box.addButton("Merge", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec_()

        clicked = box.clickedButton()
        if clicked is replace_btn:
            self.stop_playback()
            self.current_index = -1
            self.stations = stations
            self.settings = settings
            self.health = health
            # History and artist overrides come back on a replace, since
            # replace means "make this machine look like that one".
            if history:
                self.history = history
                storage.save_history(self.history)
            if overrides:
                storage.save_artist_overrides(overrides)
            if station_overrides:
                storage.save_station_overrides(station_overrides)
            message = f"Restored {len(stations)} station(s)."
        elif clicked is merge_btn:
            self.stations, added = storage.merge_stations(self.stations, stations)
            for station_id, entry in health.items():
                self.health.setdefault(station_id, entry)
            message = f"Merged in {added} new station(s)."
        else:
            return

        storage.save_stations(self.stations)
        storage.save_settings(self.settings)
        storage.save_health(self.health)
        self._reload_list()
        self._apply_loaded_settings()
        self._refresh_monitor_targets()
        self.notify(message, 8)

    # ------------------------------------------------------------------
    # Volume / EQ / noise
    # ------------------------------------------------------------------
    def _on_volume_changed(self, value):
        self.volume_value_label.setText(str(value))
        self.player.set_volume(value)
        if self._mpris_ready():
            self.mpris.update_volume(value)
        self.settings["volume"] = value
        storage.save_settings(self.settings)

    # -- sleep timer ----------------------------------------------------

    def _start_sleep(self, minutes):
        """Stop playback in `minutes`, fading out at the end.

        The fade is the point: waking up to an abrupt silence defeats
        the purpose. The audio is brought down over the last stretch and
        the volume restored afterwards, so the next station plays at the
        level you set rather than at whatever the fade left behind.
        """
        self._cancel_sleep(quiet=True)
        self.settings["sleep_minutes"] = int(minutes)
        storage.save_settings(self.settings)

        self._sleep_deadline = time.monotonic() + minutes * 60
        self._sleep_timer = QTimer(self)
        self._sleep_timer.setInterval(1000)
        self._sleep_timer.timeout.connect(self._tick_sleep)
        self._sleep_timer.start()
        self.notify(f"Sleep timer set for {minutes} minutes.", 5)

    def _cancel_sleep(self, quiet=False):
        if self._sleep_timer is not None:
            self._sleep_timer.stop()
            self._sleep_timer = None
        self._sleep_deadline = None
        if self._sleep_restore_volume is not None:
            self.volume_slider.setValue(self._sleep_restore_volume)
            self._sleep_restore_volume = None
        if not quiet:
            self.notify("Sleep timer canceled.", 4)

    def _tick_sleep(self):
        if self._sleep_deadline is None:
            return
        remaining = self._sleep_deadline - time.monotonic()
        fade = max(1, int(self.settings.get("sleep_fade_seconds", 20)))

        if remaining <= 0:
            volume = self._sleep_restore_volume
            self._cancel_sleep(quiet=True)
            self.stop_playback()
            if volume is not None:
                self.volume_slider.setValue(volume)
            self.notify("Sleep timer: stopped playback.", 8)
            return

        if remaining <= fade:
            if self._sleep_restore_volume is None:
                self._sleep_restore_volume = self.volume_slider.value()
            target = int(self._sleep_restore_volume * (remaining / fade))
            self.volume_slider.setValue(max(0, target))
        elif int(remaining) % 60 == 0:
            self.notify(f"Sleep timer: {int(remaining // 60)} minutes left.", 3)

    def _sync_sleep_menu(self):
        active = self._sleep_deadline is not None
        current = int(self.settings.get("sleep_minutes", 30))
        for minutes, action in self.sleep_actions.items():
            action.setChecked(active and minutes == current)
        self.sleep_cancel_action.setEnabled(active)
        if active:
            left = max(0, int(self._sleep_deadline - time.monotonic()))
            self.sleep_cancel_action.setText(
                f"Cancel sleep timer ({left // 60}m {left % 60}s left)"
            )
        else:
            self.sleep_cancel_action.setText("Cancel sleep timer")

    def _on_local_ratio_changed(self, value):
        self.ratio_label.setText("off" if value == 0 else f"{value}%")
        self.settings["local_station_ratio"] = int(value)
        storage.save_settings(self.settings)

    def _on_recheck_toggled(self, enabled):
        self.settings["health_recheck"] = bool(enabled)
        storage.save_settings(self.settings)
        self._apply_health_recheck_setting()
        self.notify("Down stations will be re-checked in the background."
            if enabled else
            "Background re-checking off.", 4)

    def _on_recheck_interval(self, minutes):
        self.settings["health_recheck_minutes"] = int(minutes)
        storage.save_settings(self.settings)
        self._apply_health_recheck_setting()

    def _sync_recheck_menu(self):
        current = int(self.settings.get("health_recheck_minutes", 30))
        for minutes, action in self.recheck_actions.items():
            action.setChecked(minutes == current)

    def _on_normalize_toggled(self, enabled):
        self.player.set_normalize(bool(enabled))
        self.settings["normalize"] = bool(enabled)
        storage.save_settings(self.settings)

    def _on_noise_toggled(self, enabled):
        self.player.set_noise_enabled(bool(enabled))
        self.settings["noise_between_stations"] = bool(enabled)
        storage.save_settings(self.settings)

    def _on_noise_volume_changed(self, value):
        self.noise_value_label.setText(str(value))
        self.player.set_noise_volume(value)
        self.settings["noise_volume"] = value
        storage.save_settings(self.settings)

    # ------------------------------------------------------------------
    def _apply_loaded_settings(self):
        s = self.settings
        self.volume_slider.setValue(int(s.get("volume", 80)))

        for action, key, default in (
            (self.normalize_action, "normalize", True),
            (self.noise_action, "noise_between_stations", True),
            (self.skin_shuffle_action, "skin_shuffle", False),
            (self.accent_action, "accent_from_art", True),
        ):
            action.blockSignals(True)
            action.setChecked(bool(s.get(key, default)))
            action.blockSignals(False)
        # Blocked above so nothing was written back; push the loaded
        # values into the player explicitly.
        self.player.set_normalize(bool(s.get("normalize", True)))
        self.player.set_noise_enabled(bool(s.get("noise_between_stations", True)))

        noise_volume = int(s.get("noise_volume", 70))
        self.noise_slider.blockSignals(True)
        self.noise_slider.setValue(noise_volume)
        self.noise_slider.blockSignals(False)
        self.noise_value_label.setText(str(noise_volume))
        self.player.set_noise_volume(noise_volume)

        ratio = int(s.get("local_station_ratio", 0))
        self.ratio_slider.blockSignals(True)
        self.ratio_slider.setValue(ratio)
        self.ratio_slider.blockSignals(False)
        self.ratio_label.setText("off" if ratio == 0 else f"{ratio}%")

        for action, key, default in (
            (self.artist_panel_action, "artist_panel", True),
            (self.tray_action, "tray_icon", False),
            (self.minimize_action, "minimize_to_tray", False),
        ):
            action.blockSignals(True)
            action.setChecked(bool(s.get(key, default)))
            action.blockSignals(False)

        self.recheck_action.blockSignals(True)
        self.recheck_action.setChecked(bool(s.get("health_recheck", False)))
        self.recheck_action.blockSignals(False)
        self._apply_health_recheck_setting()
        self._apply_tray_setting()
        self.minimize_action.setEnabled(bool(s.get("tray_icon", False)))
        self.player.set_audio_device(s.get("audio_device", "auto"))
        # Both halves on unless the user folded one away.
        self.lyrics_toggle.setChecked(not s.get("lyrics_panel_hidden", False))
        self.artist_toggle.setChecked(not s.get("artist_panel_hidden", False))

        self.thumbs_action.setChecked(bool(s.get("station_thumbnails")))
        self._apply_thumbnail_mode()


        # A skin chosen last time is reloaded, but the skinned window is
        # not reopened automatically - being greeted by a window you
        # don't recognize is a poor way to start.
        skin_path = s.get("skin_path", "")
        if skin_path and os.path.exists(skin_path):
            try:
                self._skin = winampskin.load_skin(skin_path)
            except winampskin.SkinError:
                self._skin = None
        self.skin_show_action.setEnabled(self._skin is not None)

        self.shuffle_btn.setChecked(bool(s.get("shuffle", False)))
        self._on_shuffle_toggled(self.shuffle_btn.isChecked())

        last_id = s.get("last_station_id")
        if last_id:
            idx = self._index_of_id(last_id)
            if idx >= 0:
                self._select_station(last_id)
                self.current_index = idx
                self._update_now_playing_label()
                # The EQ of the station we'd resume is the right starting
                # point, not a flat chain.
                self._apply_station_eq(self.stations[idx])
                # And its artwork, so the panel is not blank until
                # something plays. The station is already the one shown
                # as selected; showing its picture too just makes the
                # window agree with itself.
                self._refresh_display()

    def shutdown_workers(self):
        """Stop every background thread and save state.

        Idempotent, and wired to BOTH closeEvent and the application's
        aboutToQuit signal. A QThread that is still running when its
        owner is destroyed makes Qt abort the process, so this must not
        depend on the window being closed the tidy way.
        """
        if getattr(self, "_shut_down", False):
            return
        self._shut_down = True

        self._shazam_timer.stop()
        self._age_timer.stop()
        self._lyrics_timer.stop()
        if self.mpris is not None:
            self.mpris.unregister()

        self.health_monitor.stop()
        # Generous window: the loop checks the stop flag every 100 ms and
        # the only long operation is a probe with a 5 second read timeout.
        # Note there is no terminate() here - killing a thread in the
        # middle of a socket read is a good way to corrupt state or trip
        # an abort, and we have nothing to gain from it while quitting.
        self.health_monitor.wait(9000)

        extra = [self._shazam_worker, self._lyrics_worker]
        for worker in extra + self._art_workers + self._checks:
            if worker is not None and worker.isRunning():
                worker.wait(4000)

        storage.save_stations(self.stations)
        storage.save_settings(self.settings)
        storage.save_health(self.health)
        storage.save_history(self.history)
        self.player.shutdown()

        # Everything is saved. If a background thread is *still* going -
        # wedged in a socket read, say - do not let Python tear it down
        # underneath Qt: destroying a running QThread makes Qt call
        # abort(), which turns a slow network call at quit time into a
        # crash report. Leaving through _exit is the quiet, honest way
        # out once the state is safely on disk.
        stragglers = [w for w in ([self.health_monitor] + extra
                                  + self._art_workers + self._checks)
                      if w is not None and w.isRunning()]
        if stragglers:
            crashlog.write(
                "quitting with %d background thread(s) still running: %s"
                % (len(stragglers), ", ".join(type(w).__name__ for w in stragglers))
            )
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            os._exit(0)

    def closeEvent(self, event):
        # The skinned stack goes with it.
        #
        # Those are separate top-level windows, so closing the main one
        # left them on screen with nothing behind them - the app was
        # gone but its player was still there.
        try:
            self.skin_show_action.setChecked(False)
            if self._visualizer is not None:
                self._visualizer.close()
        except Exception:  # noqa: BLE001
            pass

        # Close to tray, when asked and when there is a tray to close to.
        #
        # Guarded on the icon actually being visible: hiding the window
        # with nowhere to restore it from would look exactly like the
        # app crashing.
        if (self.settings.get("minimize_to_tray")
                and not self._really_quitting
                and self._tray is not None
                and self._tray.isVisible()):
            event.ignore()
            self.hide()
            # Pass our own icon rather than QSystemTrayIcon.Information.
            # Several desktops render the balloon by swapping the tray
            # entry's icon for the message icon and never swap it back,
            # so the app's icon turned into a generic "i" the moment it
            # was minimised.
            icon = app_icon()
            if icon.isNull():
                self._tray.showMessage(
                    APP_TITLE,
                    "Still playing. Click the tray icon to bring the "
                    "window back.",
                    QSystemTrayIcon.Information,
                    4000,
                )
            else:
                self._tray.showMessage(
                    APP_TITLE,
                    "Still playing. Click the tray icon to bring the "
                    "window back.",
                    icon,
                    4000,
                )
            # And set it back explicitly, in case the desktop swapped it
            # anyway.
            if not icon.isNull():
                self._tray.setIcon(icon)
            return

        self.shutdown_workers()
        super().closeEvent(event)


APP_VERSION = "1.5"


def _already_running():
    """True when another copy has the lock. Leaves it held if not.

    A lock file rather than a socket: it survives a crash without
    leaving a port bound, and the PID inside lets a stale one be told
    apart from a live one. Two copies fighting over the same settings
    and station list is the kind of thing that loses edits.
    """
    import errno

    path = os.path.join(storage._config_dir(), "frequencies.lock")
    try:
        handle = open(path, "a+")
        handle.seek(0)
    except OSError:
        return False                     # cannot lock; better to run

    try:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            return True
        return False
    except Exception:  # noqa: BLE001
        return False

    handle.truncate(0)
    handle.write(str(os.getpid()))
    handle.flush()
    # Deliberately never closed: the lock lives as long as the process.
    globals()["_instance_lock"] = handle
    return False


def main():
    # Logging first, so a Qt fatal during startup still lands in the log.
    log = crashlog.install(version=APP_VERSION)

    if _already_running():
        sys.stderr.write("Frequencies is already running.\n")
        return 0

    # Before the QApplication: QtWebEngine needs a shared GL context and
    # will not start if the application was built without one.

    app = QApplication(sys.argv)

    # The design overhaul: one set of tokens, one stylesheet.
    #
    # The palette goes on as well as the stylesheet - Qt paints scroll
    # viewports, dialog backgrounds and disabled text from the palette
    # rather than from QSS, and without it those keep the light default
    # and end up dark-on-dark.
    try:
        import theme
        app.setPalette(theme.palette())
        app.setStyleSheet(theme.stylesheet())
    except Exception:  # noqa: BLE001
        pass
    # Set on the application, not just per window.
    #
    # A taskbar groups by application and takes its icon from there, so
    # setting it on individual windows alone left the entries showing
    # the generic default.
    try:
        from PyQt5.QtGui import QGuiApplication, QIcon

        # Tell the compositor which desktop entry this is.
        #
        # Wayland has no per-window icon protocol: a taskbar matches a
        # window to a .desktop file by app_id and takes the icon from
        # there, so setWindowIcon alone does nothing. Naming the entry
        # is what makes the icon appear at all - and it is why the
        # skinned player and the visualizer necessarily share one icon
        # with the app rather than having their own.
        QGuiApplication.setDesktopFileName("frequencies")

        # The application's icon is Frequencies' own.
        #
        # It was the skin's transmitter mark, which meant the ordinary
        # window wore the skinned player's badge. Wayland has one icon
        # per application and takes it from the desktop entry, so this
        # is the app's identity rather than any one window's.
        icon = app_icon()
        if not icon.isNull():
            app.setWindowIcon(icon)
    except Exception:  # noqa: BLE001
        pass
    # Constructing a QApplication calls setlocale(LC_ALL, "") on Linux,
    # which overwrites the LC_NUMERIC="C" that python-mpv set at import
    # time. libmpv treats a non-C LC_NUMERIC as fatal, so put it back
    # before anything creates a Player. See player.py for the details.
    force_c_numeric_locale()
    app.setApplicationName(APP_TITLE)
    app.setDesktopFileName("frequencies")
    if log:
        print(f"Frequencies log: {log}")
    win = MainWindow()
    # Covers quit paths that never deliver a closeEvent (session logout,
    # Ctrl-Q, app.quit() from anywhere).
    app.aboutToQuit.connect(win.shutdown_workers)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
