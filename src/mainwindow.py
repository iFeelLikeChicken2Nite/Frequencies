"""Frequencies — the real main window.

`Shell` is scaffolding: sidebar, page stack, right panel, player bar, and no
knowledge of radio. `FrequenciesWindow` fills it with your library and wires
every signal to `Library` and `Playback`.

From main.py::

    lib  = Library(stations, settings, history)
    win  = FrequenciesWindow(lib, engine)      # engine is your Player
    win.show()

`Player` reports through the callbacks you hand its constructor, not through
signals, so main.py forwards them in:

    engine = player.Player(
        on_state=win.on_state,
        on_metadata=win.on_metadata,
        on_connected=win.on_connected,
        on_stalled=win.on_stalled,
        on_track_finished=win.on_track_finished)

and the Shazam / lyrics side calls `win.on_song(...)`, `win.on_lyrics(...)`,
`win.on_position(ms)` as results arrive.
"""

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMenu,
                             QMessageBox,
                             QPushButton, QTabWidget, QVBoxLayout, QWidget)

import os

import feeds
import time

import storage
import theme
from bridge import Playback
from historypage import HistoryPage
from shell import PillSearch, Shell, icon, open_menu, tool_button
from theme import T
from stationlist import StationList


class PlayerBus(QObject):
    """mpv calls back on its own event thread. Qt widgets must only be
    touched on the GUI thread, so every callback becomes a signal — Qt
    queues cross-thread emissions automatically, which is what makes this
    safe. A crash with nothing but Qt frames in the trace is what happens
    without it."""

    state = pyqtSignal(object, str)
    metadata = pyqtSignal(str)
    connected = pyqtSignal(str, float)
    stalled = pyqtSignal(str)
    track_finished = pyqtSignal()
    tags = pyqtSignal(object)           # a file's own tags (brat mode)


class ColumnHeader(QWidget):
    """Names the detail columns, lined up with them exactly.

    It asks the delegate where the columns are rather than working it out
    again — a header a few pixels off its column reads as a mistake, and
    two copies of the same arithmetic drift apart the first time one of
    them changes.

    Tags sit last because they are the one column with no fixed width:
    everything else is a number or a date and can be measured, while a
    tag list is as long as it is.
    """

    HEIGHT = 22

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view
        self.setFixedHeight(self.HEIGHT)

    def paintEvent(self, _e):
        delegate = getattr(self.view, "delegate", None)
        if delegate is None or not getattr(delegate, "show_columns", False):
            return

        from PyQt5.QtCore import QRect as _Rect
        from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QPainter,
                                 QPen)

        p = QPainter(self)
        font = QFont()
        font.setPixelSize(10)
        font.setCapitalization(QFont.AllUppercase)
        p.setFont(font)
        metrics = QFontMetrics(font)

        right = self.width() - 10
        if getattr(delegate, "show_actions", False):
            right -= delegate.ACTION_W + 8

        boxes, left_edge = delegate.column_boxes(right, self.width())
        p.setPen(QColor(T["text3"]))
        for title, x, width, align in boxes:
            p.drawText(_Rect(x, 0, width, self.HEIGHT),
                       align | Qt.AlignVCenter,
                       metrics.elidedText(title, Qt.ElideRight, width))

        # Tags last, in whatever room is left before the columns.
        tag_room = max(0, left_edge - 60)
        if tag_room > 30:
            p.drawText(_Rect(left_edge - tag_room - 8, 0, tag_room,
                             self.HEIGHT),
                       Qt.AlignRight | Qt.AlignVCenter, "Tags")

        p.setPen(QPen(QColor(T["line"]), 1))
        p.drawLine(0, self.HEIGHT - 1, self.width(), self.HEIGHT - 1)
        p.end()


class StationsPage(QWidget):
    """Search field over the painted list, count underneath."""

    def __init__(self, logo_fetcher=None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 8, 8)
        lay.setSpacing(10)

        # Adding lives on Add Frequency…, which is a whole destination for
        # it; a second button here was the same door twice. What this list
        # does need is a way to say what order it is in.
        head = QHBoxLayout()
        head.setSpacing(8)
        self.search = PillSearch("Search your frequencies, or tag:80s")
        # No tooltip: the menu it opens names everything in it.
        self.sort_btn = tool_button("menu", "", 18)
        head.addWidget(self.search, 1)
        head.addWidget(self.sort_btn)

        self.list = StationList(logo_fetcher=logo_fetcher)

        # Floats over the list rather than taking a row of its own, and
        # only appears when the playing station is somewhere you cannot
        # see — a permanent button for a thing that is usually already on
        # screen is just clutter.
        self.jump = QPushButton("  Jump to playing")
        self.jump.setProperty("floating", True)
        self.jump.setIcon(icon("stations", 14, T["accent"]))
        self._jump_accent = T["accent"]
        self.jump.setParent(self.list)
        self.jump.setVisible(False)

        # Task 131: the same idea for scheduled shows on the air now. Each
        # pill reads "<frequency> - <show>" (task 134) and selects that
        # frequency without playing it. At most two of them, then a third,
        # "More live shows", that opens the Schedule (task 137).
        self.live_pills = []
        for _n in range(2):
            self.live_pills.append(self._make_live_pill(""))
        self.live_more = self._make_live_pill("More live shows")

        # A strip naming the detail columns, shown only with them.
        self.columns_header = ColumnHeader(self.list)
        self.columns_header.setVisible(False)

        lay.addLayout(head)
        lay.addWidget(self.columns_header)
        lay.addWidget(self.list, 1)

    def _make_live_pill(self, text):
        pill = QPushButton("  " + text if text else "")
        pill.setProperty("floating", True)
        pill.setIcon(icon("schedule", 14, "#d93030"))
        pill.setParent(self.list)
        pill.setVisible(False)
        return pill


class FrequenciesWindow(Shell):
    _live_ids = []          # frequencies with a show on the air (task 131)
    _live_shows = []        # [(station, show)] on the air now

    def __init__(self, library, player, logo_fetcher=None, accent=True):
        super().__init__()
        self.lib = library
        # brat mode (task 48): a song from Charli XCX's brat makes the
        # whole app lowercase while it plays. brat.py decides and switches.
        # First, so nothing that reports a song can arrive before it.
        import brat
        import crashlog
        self.brat = brat.BratMode(log=crashlog.write, parent=self)
        self.brat.changed.connect(self._brat_changed)
        self._rows = []
        self._active_tags = set()
        self._song = None
        self._connecting = False
        self._pending_meta = ""            # (title, artist) currently identified

        if accent:
            self.attach_accent(theme.AccentController(QApplication.instance()))

        # The central widget is a plain QWidget, and the stylesheet only
        # paints the named surfaces — so any sliver the three panels don't
        # cover has no background at all and shows whatever is behind the
        # window. Filling it from the palette costs nothing and removes a
        # whole class of "corruption" that is really just transparency.
        self.centralWidget().setAutoFillBackground(True)

        self.stations_page = StationsPage(logo_fetcher=logo_fetcher)
        self.history_page = HistoryPage()
        self.add_page("stations", self.stations_page)
        self.add_page("history", self.history_page)

        from discover import DiscoverPage
        # home_lat/home_lon default to 0.0 rather than None, and (0, 0) is a
        # point in the Atlantic — treat an unset location as unset.
        lat = float(library.settings.get("home_lat") or 0.0)
        lon = float(library.settings.get("home_lon") or 0.0)
        self.discover_page = DiscoverPage(
            near=(lat, lon) if (lat or lon) else None)
        self.add_page("discover", self.discover_page)

        from optionspage import OptionsPage
        self.options_page = OptionsPage(library, player)
        from helppage import HelpPage
        self.help_page = HelpPage()
        self.add_page("help", self.help_page)

        from schedule import SchedulePage
        self.schedule_page = SchedulePage(self.lib)
        self.schedule_page.play_station.connect(self._play_by_id)
        # The same artwork the station list shows, not a second copy.
        self.schedule_page.grid.set_logos(self.stations_page.list.logos)
        self.add_page("schedule", self.schedule_page)

        self.add_page("options", self.options_page)

        # Everything the accent shows up on. Settings, Add Frequency… and
        # History were not in the list, so their checkboxes, pills and
        # segmented buttons kept whatever colour they had at startup.
        if getattr(self, "accent", None) is not None:
            # Dialogs open later than any restyle, so they are caught as
            # they show rather than repainted in advance.
            self._dialog_accent = theme.DialogAccent(self.accent, self)
            QApplication.instance().installEventFilter(self._dialog_accent)
            # On screen while music plays, so these change with the art.
            self.accent.set_quick_targets(
                [self.player, self.sidebar, self.panel])
            # Everything else waits until it is looked at.
            self.accent.set_visible_page(lambda: self.pages.currentWidget())
            # The schedule was missing from this list, so everything on it
            # drawn in the accent — its checkbox, its selected show — kept
            # the colour from when the app started.
            self.accent.set_lazy_targets([
                self.options_page, self.discover_page, self.history_page,
                self.stations_page, self.now_playing, self.schedule_page])

        self.playback = Playback(
            player, library,
            visible_ids=self.visible_ids,
            on_change=self._station_changed,
            on_notice=self.notify,
            parent=self)
        self.playback.on_ad_cover = self.ad_cover_started
        # An iBroadcast station knows what it is playing without being
        # told by an identification, so it says so directly.
        self.playback.on_track = self._ibroadcast_track
        self.playback.on_moved = self._after_skip
        self.playback.on_station_art = self._show_station_art
        self.playback.on_resolved = self._youtube_resolved
        # A station Playback marked down (an offline Twitch channel, a
        # YouTube playlist with nothing playable) turns red at once.
        self.playback.on_health = self.reload

        # Keeps the iBroadcast sign-in from running out mid-listen.
        #
        # Every library track is fetched with the account's token in its
        # URL, and nothing ever renewed it — so a station played until the
        # token expired and then every track was refused.
        import ibtoken
        self._ib_keeper = ibtoken.TokenKeeper(self)
        self._ib_keeper.refreshed.connect(self._ib_token_refreshed)
        # The station waiting on a renewal, if any; and the stations that
        # have had their one renew-and-retry since they last connected.
        self._ib_resign_for = None
        self._ib_resigned_ids = set()

        # Ctrl +/-/0, as a browser does it. Qt scales a stylesheet's px
        # units by the application font's DPI, so changing the base point
        # size moves everything together rather than only the text.
        from PyQt5.QtWidgets import QShortcut
        from PyQt5.QtGui import QKeySequence
        # Not "keys": that is the shortcut table's module name, and
        # shadowing it here is the sort of thing that works until someone
        # uses the module three lines later.
        for combo, delta in ((("Ctrl++", "Ctrl+="), 1), (("Ctrl+-",), -1)):
            for key in combo:
                QShortcut(QKeySequence(key), self,
                          lambda d=delta: self.zoom_by(d))
        QShortcut(QKeySequence("Ctrl+0"), self, lambda: self.set_zoom(100))
        QTimer.singleShot(0, lambda: self.set_zoom(
            int(self.lib.settings.get("ui_zoom", 100)), save=False))

        # Scrolling that carries, everywhere there is something to scroll.
        import smoothscroll
        self._scrollers = smoothscroll.attach(
            self.stations_page.list, self.discover_page.list,
            self.discover_page.featured_list, self.history_page.scroll,
            *self.options_page.scrollers(), self.panel.lyrics.view,
            self.panel.artist,
            self.now_playing.lyrics, self.now_playing._artist_col,
            self.sidebar._tag_scroll)

        # Watches for scheduled shows starting.
        import schedule as schedule_module
        self._reminders = schedule_module.Reminders(
            self.lib, self._show_starting)
        # The red LIVE NOW badges and the jump button (task 131). A show
        # still never starts by itself.
        self._live_ids = []
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(30_000)
        self._live_timer.timeout.connect(self._refresh_live)
        self._live_timer.start()
        self.schedule_page.changed.connect(self._refresh_live)
        QTimer.singleShot(0, self._refresh_live)

        self.bus = PlayerBus()
        self.bus.state.connect(self._handle_state)
        self.bus.metadata.connect(self._handle_metadata)
        self.bus.connected.connect(self._handle_connected)
        self.bus.stalled.connect(self._handle_stalled)
        self.bus.track_finished.connect(self._handle_track_finished)
        self.bus.tags.connect(self._handle_tags)

        self.feeds = None
        self.desktop = None

        # The clock style is set before any page draws a time.
        import clock
        clock.set_mode(bool(library.settings.get("clock_12h")))
        self._classic = None
        self._lyrics = []
        self._bio = ""
        self._lyrics_source = ""
        self._artist_source = ""
        self._vis_session = None
        # Dragging is off by default in StationList, and nothing turned it
        # on — so manual order could never actually be rearranged.
        self.stations_page.list.set_view_mode(
            bool(library.settings.get("station_grid")))
        self.stations_page.list.set_columns(
            bool(library.settings.get("station_columns")))
        # The header follows the setting at launch too, not only when the
        # menu item is used.
        self.stations_page.columns_header.setVisible(
            bool(library.settings.get("station_columns")))
        self.stations_page.list.set_manual_sort(
            not library.settings.get("station_grid")
            and library.settings.get("sort_mode", "manual") == "manual")

        # Drives the static hand-off and the dead-stream watchdog.
        #
        # Nothing else calls player.tick(), and without it the player never
        # notices that audio has started — so the static that covers a
        # connection runs forever and the stream never fades in underneath
        # it. Half a second catches the moment audio starts without the
        # cross-fade feeling late.
        self._playback_timer = QTimer(self)
        self._playback_timer.setInterval(500)
        self._playback_timer.timeout.connect(player.tick)
        self._playback_timer.start()

        # Task 88: sound that stops on its own pauses for real, so the
        # play button follows it; links opened in the browser are logged
        # with what the station's sound does next.
        player.on_went_quiet = self._sound_stopped
        import links
        self._link_watch = links.LinkWatch(self._link_opened, self)

        # The static's handle is built now rather than when it is first
        # wanted — see NoisePlayer.warm. Deferred a moment so it does not
        # sit in front of the window appearing.
        warm = getattr(getattr(player, "noise", None), "warm", None)
        if callable(warm):
            QTimer.singleShot(400, warm)

        # The clock the timed lyrics follow.
        #
        # Nothing was calling on_position at all, so the panel had stamps
        # and no idea what time it was — which is why lyrics arrived and
        # then just sat there. Four times a second is finer than any line
        # needs and costs nothing.
        self._lyric_clock = QTimer(self)
        self._lyric_clock.setInterval(250)
        self._lyric_clock.timeout.connect(self._tick_lyrics)
        self._lyric_clock.start()

        # How long each station has actually been listened to. Play counts
        # say how often you started something, which is a different thing:
        # a station you skip after ten seconds scores the same as one you
        # leave on all evening.
        self._listen_timer = QTimer(self)
        self._listen_timer.setInterval(15000)
        self._listen_timer.timeout.connect(self._log_listening)
        self._listen_timer.start()
        self._listening_since = None

        # Keeps the "down 3h" ages in the subtitle line honest.
        self._age_timer = QTimer(self)
        self._age_timer.timeout.connect(self._refresh_ages)
        self._age_timer.start(60000)
        self._wire()
        self.reload()
        # The bar's slider ships at a hardcoded 68 and knew nothing about the
        # saved volume, so what you saw and what you heard disagreed. Sync it
        # without emitting, then apply it once.
        # Shuffle is saved on every toggle and was never read back, so it
        # silently reverted to off on each launch.
        self.player.shuffle_btn.blockSignals(True)
        self.player.shuffle_btn.setChecked(
            bool(self.lib.settings.get("shuffle", False)))
        self.player.shuffle_btn.blockSignals(False)
        self.player._on_shuffle(self.player.shuffle_btn.isChecked())

        if getattr(self, "accent", None) is not None:
            self.accent.set_source(
                self.lib.settings.get("accent_source", "artwork"),
                self.lib.settings.get("accent_fixed"))

        # Artist biographies are always on (build 100, task 138).
        self.lib.settings["artist_lookup"] = True
        self.panel.set_artist_enabled(self.artist_lookup_on())

        size = int(self.lib.settings.get("lyric_size", 13))
        self.panel.lyrics.view.set_text_size(size)
        self.now_playing.lyrics.set_text_size(size)

        volume = int(self.lib.settings.get("volume", 80))
        self.player.vol.blockSignals(True)
        self.player.vol.setValue(volume)
        self.player.vol.blockSignals(False)
        player.set_volume(volume)

        # Clippy (build 107, task 116; the user's script, build 108, task
        # 143): each tip's place is the widget below; see companion.py.
        import companion
        self.companion = companion.Companion(
            self, self.lib.settings, self.lib.save_settings,
            targets={
                "search": lambda: self.discover_page.sources._buttons.get(
                    "search"),
                "results": lambda: self.discover_page.list,
                "added": lambda: self.sidebar._buttons["stations"][0],
                "queue": lambda: self.stations_page.list,
                "shazam": lambda: self.player.shazam_btn,
                "favorite": lambda: self.player.fav_btn,
                "options": lambda: self.player.eq_btn,
                "tags": lambda: self.sidebar._tag_title,
                "visualizer": lambda: self.sidebar.vis_btn,
                "share": lambda: self.stations_page.list,
                "chat": lambda: (self.panel if getattr(
                    self, "_chat_channel", "") else None),
                "schedule": lambda: self.schedule_page,
                "sidebar": lambda: self.sidebar,
                "down": self._clippy_down_row,
                "twitch": self._clippy_twitch_row,
                "art": lambda: self._clippy_row(
                    getattr(self, "_clippy_art_id", None)),
                "lyrics": lambda: (self.panel.lyrics
                                   if self.panel.lyrics.view.isVisible()
                                   else None),
            },
            is_playing=lambda: bool(getattr(self.player, "_playing", False)),
            winamp=lambda: getattr(self._classic, "dock", None),
            photos=(self._clippy_offer_photos, self._clippy_photos_on))
        self.companion.tip("welcome")
        # Tip 1.5 is done once Search is clicked (task 163); if it was
        # showing, he stays and moves on to tip 1.75 about the results
        # (task 196).
        self.discover_page.sources.chosen.connect(self._clippy_search_clicked)

    def _clippy_search_clicked(self, key):
        if key != "search":
            return
        if self.companion.current == "search":
            self.companion.move_on("search", "results")
        else:
            self.companion.done("search")

    def _clippy_offer_photos(self):
        """Clippy's photo pack question: only while it is off and there
        is a pack to get."""
        import photopack
        return (not self.lib.settings.get(photopack.SETTING)
                and bool(photopack.pack_address()))

    def _clippy_photos_on(self):
        """Clippy's Yes: the same as ticking the Settings switch."""
        import photopack
        box = getattr(getattr(self, "options_page", None), "pack_check",
                      None)
        if box is not None:
            box.setChecked(True)        # saves and starts it, as a click
            return
        self.lib.settings[photopack.SETTING] = True
        self.lib.save_settings()
        keeper = photopack.keeper(self.lib.settings)
        if keeper is not None:
            keeper.set_enabled(True)

    def _clippy_twitch_row(self):
        """Tip 12's place: the Twitch frequency just added, in Saved
        Frequencies (task 150)."""
        return self._clippy_row(getattr(self, "_clippy_twitch_id", None))

    def _clippy_row(self, station_id):
        """A saved frequency's row, for a tip beside it (tips 12 and 14)."""
        view = self.stations_page.list
        if not station_id or not view.isVisible():
            return None
        row = view.model_.row_of(station_id)
        if row is None or row < 0:
            return None
        rect = view.visualRect(view.model_.index(row, 0))
        if not rect.intersects(view.viewport().rect()):
            return None
        return view.viewport(), rect

    def _clippy_down_row(self):
        """Tip 10's place: the first stream on screen in Saved Frequencies
        that the health check marked down (not Twitch, YouTube or local
        files)."""
        view = self.stations_page.list
        if not view.isVisible():
            return None
        port = view.viewport()
        model = view.model_
        for row in range(model.rowCount()):
            st = model.station_at(row) or {}
            if not (self.lib.health.get(st.get("id")) or {}).get("down"):
                continue
            source = st.get("_source") or st
            if (source.get("kind") == "twitch"
                    or storage.is_local_station(source)
                    or storage.is_youtube_station(source)
                    or not str(source.get("url", "")).startswith("http")):
                continue
            rect = view.visualRect(model.index(row, 0))
            if rect.intersects(port.rect()):
                return port, rect
        return None

    # -- wiring ------------------------------------------------------------

    def _wire(self):
        sp = self.stations_page
        sp.search.textChanged.connect(lambda _: self._filter())
        sp.sort_btn.clicked.connect(lambda: self._sort_menu(sp.sort_btn))
        sp.jump.clicked.connect(self.jump_to_playing)
        for pill in sp.live_pills:
            pill.clicked.connect(
                lambda _c=False, b=pill: self.jump_to_live(
                    b.property("station_id")))
        sp.live_more.clicked.connect(lambda: self.sidebar._select("schedule"))
        sp.list.verticalScrollBar().valueChanged.connect(
            lambda _v: self._place_jump())
        sp.list.installEventFilter(self)
        sp.list.activated_station.connect(
            lambda row: self.playback.play(row.get("_source")))
        sp.list.context_requested.connect(self._station_menu)
        sp.list.set_multi_select(True)
        sp.list.context_many.connect(self._many_menu)
        sp.list.order_changed.connect(self.lib.reorder)

        self.sidebar.tag_state_changed.connect(self._tag_state_changed)
        sp.search.pills_changed.connect(self._pills_changed)
        sp.search.pill_clicked.connect(self._pill_clicked)
        self.sidebar.navigated.connect(self._page_entered)
        self.sidebar.classic_requested.connect(self.open_classic)
        self.sidebar.visualizer_requested.connect(self.open_visualizer)
        self.sidebar.tags_edit_requested.connect(self._edit_tags_from_sidebar)

        self.discover_page.station_added.connect(self.add_station)
        self.discover_page.source_requested.connect(self._add_from_source)
        self.discover_page.source_menu.connect(self._source_menu)
        self.discover_page.share_requested.connect(self.share_station)
        # A picture the worker could not read: the feed moves to the next
        # candidate rather than leaving the previous song's cover up.
        self.art_decode_failed.connect(
            lambda: self.feeds.art_decode_failed() if self.feeds else None)
        self.discover_page.station_removed.connect(self.remove_by_url)
        self.discover_page.preview_requested.connect(self.preview_station)
        self.options_page.library_changed.connect(self.reload)
        self.options_page.stop_playback.connect(self.playback.stop)
        self.options_page.changed.connect(self._setting_changed)
        self.options_page.chat_sign_in.connect(self._chat_sign_in)
        self.options_page.chat_sign_out.connect(self._chat_sign_out)

        p = self.player
        p.play_toggled.connect(self._on_play_toggled)
        p.sleep_requested.connect(lambda: self._sleep_menu(p.sleep_btn))
        p.skipped.connect(self._on_skip)
        p.volume_changed.connect(self._on_volume)
        p.shuffled.connect(self._on_shuffle)
        p.favourited.connect(self._on_favourite)
        p.fav_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        p.fav_btn.customContextMenuRequested.connect(
            lambda _pos: self.sidebar._select("history"))
        p.fav_btn.setToolTip("Favorite this song · right-click for history")
        p.eq_requested.connect(self.open_eq)
        p.shazam_toggled.connect(self._shazam_toggled)
        p.station_menu_requested.connect(self._now_playing_menu)

        # Twitch channels are only up while somebody is streaming, and
        # nothing else would ever notice one going live.
        # A shared thumbnail is a placeholder for the real cover.
        import artupgrade
        self._art_upgrade = artupgrade.ArtUpgrade(self.lib, self)
        self._art_upgrade.changed.connect(self.reload)
        QTimer.singleShot(4000, self._art_upgrade.sweep)

        import twitchwatch
        self._twitch_watch = twitchwatch.TwitchWatch(self.lib, self)
        self._twitch_watch.changed.connect(self.reload)
        self._twitch_watch.start()

        self.sidebar.choose_start_page()
        # Unplayed stations from last time keep their badge across a
        # restart, so the sidebar has to say so from the first frame.
        QTimer.singleShot(0, self._refresh_new_badge)
        self._install_shortcuts()
        self.connect_sidebar()
        # So the tooltip is right before anything has been played.
        self._refresh_fullscreen_state()
        self.sidebar.extra_hidden.connect(self._hide_extra)
        self.sidebar.apply_extras(self.lib.settings.get("hidden_extras") or [])
        self.history_page.menu_requested.connect(self._history_menu)
        # "Is this right?" is a question, not a control — there is
        # nothing to connect. The thumbs beside it are the answer.
        self.panel.lyrics.resync_signal().connect(self._resync_lyrics)
        # Twitch chat's pop-out button and right-click switches (task 92).
        self._load_chat_options()
        self._hook_chat_view(self.panel.lyrics.chat)
        self.panel.artist_corrected_signal().connect(self._artist_corrected)
        self.now_playing.lyrics.resync_requested.connect(self._resync_lyrics)
        self.panel.verdict_signal().connect(self._artist_verdict)
        # The artist photo pack (build 91, task 117): opt-in, kept up to
        # date in the background; a new pack redraws the artist panel.
        import photopack
        photopack.keeper(self.lib.settings).changed.connect(
            self._photo_pack_changed)
        # Updates (build 125, task 177): checked at every launch, applied
        # when the app quits; a new build says so once.
        import updater
        updates = updater.updater(self.lib.settings, self)
        updates.ready.connect(self.notify)
        updates.start()
        QTimer.singleShot(3000, self._updated_note)
        self.history_page.favourited.connect(
            lambda key, on: (self.lib.set_favourite(key, on),
                             self.refresh_history()))


    def _updated_note(self):
        """"Updated to build N" and what's new, once (build 125)."""
        import updater
        note = updater.updater().updated_note()
        if note is None:
            return
        build, lines = note
        box = QMessageBox(self)
        box.setWindowTitle("Frequencies")
        box.setIcon(QMessageBox.Information)
        box.setText(f"Updated to build {build}")
        if lines:
            box.setInformativeText("What's new:\n" + "\n".join(
                "\u2022 " + line for line in lines))
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.open()

    # -- data --------------------------------------------------------------

    def _mark_new_stations(self):
        """Flag any station that was not here last time as new.

        Done here, once, rather than at each way in. There are already
        several — the search results, a folder, YouTube, Twitch,
        iBroadcast, a pasted URL, the sniffer, an imported playlist, a
        shared code — and marking each separately meant the badge worked
        for whichever one had been remembered. Every add ends in a
        reload, so every add is seen here.

        Two cases are left alone. The first reload after launch only
        records what is there: the library you open is not new. And a
        restore, where none of the old stations survive, is a library
        being put back rather than stations being added — flagging three
        hundred of them would make the badge meaningless.
        """
        current = {s.get("id") for s in self.lib.stations if s.get("id")}
        known = getattr(self, "_known_station_ids", None)
        self._known_station_ids = current
        if known is None:
            return
        arrived = current - known
        if not arrived:
            return
        if known and not (known & current):
            return                              # a restore, not an add
        changed = False
        for station in self.lib.stations:
            if station.get("id") in arrived and not station.get("is_new"):
                station["is_new"] = True
                changed = True
        if changed:
            self.lib.save_stations()
            self._refresh_new_badge()

    def reload(self):
        """Re-read the library. Call after anything edits stations or tags."""
        self._mark_new_stations()
        self._rows = self.lib.rows()
        if getattr(self, "discover_page", None) is not None:
            self.discover_page.set_owned(
                [s.get("url") or "" for s in self.lib.stations])
        self.stations_page.list.set_stations(self._rows)
        self.sidebar.set_tags(self.lib.sidebar_tags())
        self.refresh_history()
        self._filter()

    def cover_arrived(self, track_key, data):
        """A freshly fetched cover, given to the one tile that wants it."""
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        self.history_page.cover_arrived(track_key, pixmap)

    def refresh_history(self):
        entries = self.lib.history_entries()
        for e in entries:
            e["art"] = feeds.history_art(e)
        if self.history_page.isVisible() and len(entries) > 60:
            self.history_page.show_loading()
            QTimer.singleShot(0, lambda: (
                self.history_page.set_entries(entries),
                self.history_page.hide_loading()))
            return
        self.history_page.set_entries(entries)

    def eventFilter(self, obj, event):
        """Re-centre the jump button when the list is resized."""
        if (event.type() == QEvent.Resize
                and obj is self.stations_page.list):
            # After the layout has settled, not during it: measured mid
            # resize the list still reports its old width and the button
            # lands where it used to be centred.
            QTimer.singleShot(0, self._place_jump)
        return super().eventFilter(obj, event)

    def _repaint_jump(self):
        """Re-tint the jump button when the accent moves.

        Its icon is a pixmap drawn once in whatever the accent was at the
        time, so unlike everything driven by the stylesheet it needed
        telling.
        """
        page = getattr(self, "stations_page", None)
        if page is None or not hasattr(page, "jump"):
            return
        page.jump.setIcon(icon("stations", 14, T["accent"]))

        # The hover colours are set on the button itself, from the current
        # accent, every time the accent moves.
        #
        # They came from a rule in the application's sheet, and that sheet
        # is written once, at launch. The accent is then carried by small
        # overlay sheets on each page — but this button floats over the
        # list as its own child, and none of those overlays reached it. So
        # its hover stayed the colour the app opened with, whatever was
        # playing since.
        page.jump.setStyleSheet(
            f"QPushButton:hover {{ background: {T['bg3']};"
            f" color: {T['accent']}; border: 1px solid {T['accent']}; }}")

    def _refresh_live(self):
        """Which scheduled shows are on the air now: the badge on the
        Schedule button, the schedule's own rows, the jump pills."""
        import schedule as schedule_module
        live = schedule_module.live_now(self.lib.stations)
        self._live_shows = live
        self._live_ids = [station.get("id") for station, _show in live]
        self.sidebar.set_live_badge(bool(live))
        self.schedule_page.grid.update()
        self._place_jump()

    def _live_targets(self):
        """[(station_id, label, row, below)] for every live frequency that
        is out of sight in the list."""
        page = self.stations_page
        model = page.list.model_
        port = page.list.viewport().rect()
        found = []
        for station, show in getattr(self, "_live_shows", []):
            station_id = station.get("id")
            row = model.row_of(station_id)
            if row < 0:
                continue
            rect = page.list.visualRect(model.index(row, 0))
            if rect.isValid() and not port.intersects(rect):
                label = storage.station_display_name(station)
                name = (show.get("name") or "").strip()
                if name:
                    label = f"{label} - {name}"
                found.append((station_id, label, row,
                              rect.top() >= port.height()))
        return found

    def _place_live(self):
        """Up to two "<frequency> - <show>" pills for live shows out of
        sight, then "More live shows" when there are further ones. Each sits
        above or below the list, whichever way it will take you."""
        page = self.stations_page
        targets = self._live_targets()
        pills = []
        for pill, (station_id, label, _row, below) in zip(
                page.live_pills, targets[:2]):
            room = max(80, page.list.viewport().width() - 60)
            pill.setProperty("station_id", station_id)
            pill.setToolTip(label)
            pill.setText("  " + pill.fontMetrics().elidedText(
                label, Qt.ElideRight, room - 40))
            pills.append((pill, below))
        if len(targets) > 2:
            pills.append((page.live_more, targets[2][3]))
        shown = {id(p) for p, _b in pills}
        for pill in (*page.live_pills, page.live_more):
            if id(pill) not in shown:
                pill.setVisible(False)
        # Stack on each side, clear of "Jump to playing" on the same side.
        used = {False: 14, True: 14}
        if page.jump.isVisible():
            jump_below = page.jump.y() > page.list.viewport().height() // 2
            used[jump_below] += page.jump.height() + 8
        for pill, below in pills:
            size = pill.sizeHint()
            height = page.list.viewport().height()
            y = height - size.height() - used[below] if below else used[below]
            used[below] += size.height() + 8
            pill.setGeometry(
                max(0, (page.list.viewport().width() - size.width()) // 2), y,
                size.width(), size.height())
            pill.setVisible(True)
            pill.raise_()

    def _place_jump(self):
        self._place_jump_playing()
        self._place_live()

    def _place_jump_playing(self):
        """Show the button only when the playing row is off screen."""
        page = self.stations_page
        model = page.list.model_
        playing = (self.playback.current or {}).get("id")
        row = model.row_of(playing) if playing else -1
        if row < 0:
            page.jump.setVisible(False)
            return
        rect = page.list.visualRect(model.index(row, 0))
        visible = rect.isValid() and page.list.viewport().rect().intersects(rect)
        page.jump.setVisible(not visible)
        if not visible:
            size = page.jump.sizeHint()
            # Below if the station is further down the list, above if it is
            # further up: the button points the way it will take you.
            below = rect.top() >= page.list.viewport().height()
            y = (page.list.viewport().height() - size.height() - 14 if below
                 else 14)
            # Centred on the list as it is now, not as it was: the list
            # changes width whenever the sidebar collapses or a panel
            # appears, and the button has to follow.
            page.jump.setGeometry(
                max(0, (page.list.viewport().width() - size.width()) // 2), y,
                size.width(), size.height())
            page.jump.raise_()

    def jump_to_playing(self):
        """Scroll to the playing row and stay there.

        The smooth scroller keeps its own target and eases toward it, so a
        scrollTo underneath it was corrected back to wherever the wheel had
        last been aimed — which is why this snapped straight back.
        """
        # No sync_after_jump here.
        #
        # It defers its work to the next turn of the event loop, so it
        # landed *after* glide_to and reset the target to where the list
        # had been put back to — cancelling the journey a moment after it
        # started. glide_to already takes the scroller over cleanly.
        model = self.stations_page.list.model_
        playing = (self.playback.current or {}).get("id")
        row = model.row_of(playing) if playing else -1
        if row >= 0:
            self._glide_to_row(row)
        self._place_jump()

    def jump_to_live(self, station_id=None):
        """Scroll to the frequency of a show that is on the air and select
        it. It does not play it (task 131)."""
        targets = self._live_targets()
        for target_id, _label, row, _below in targets:
            if station_id is None or target_id == station_id:
                view = self.stations_page.list
                index = view.model_.index(row, 0)
                self._glide_to_row(row)
                view.setCurrentIndex(index)
                break
        self._place_jump()

    def _glide_to_row(self, row):
        model = self.stations_page.list.model_
        view = self.stations_page.list
        bar = view.verticalScrollBar()
        # Let the view work out where the row is, then animate to it.
        #
        # visualRect returns an empty rectangle for a row outside the
        # viewport — which is every row this button exists for — so
        # computing the destination from it gave zero every time. The
        # view knows how to find the row; it is only the travelling
        # that needs doing differently.
        here = bar.value()
        # The model the row index came from, not view.model(): the
        # list wraps its model, and an index made from the wrapper
        # means nothing to scrollTo, which silently did nothing.
        view.scrollTo(model.index(row, 0), view.PositionAtCenter)
        target = bar.value()
        moved = False
        if target != here:
            for scroller in getattr(self, "_scrollers", []):
                if getattr(scroller, "area", None) is view:
                    bar.setValue(here)      # back, to travel there
                    scroller.glide_to(target)
                    moved = True
        if not moved:
            bar.setValue(target)

    def _update_search_placeholder(self):
        """The field says how to reach it as well as what it searches."""
        import keys
        n, total = getattr(self, "_search_counts", (0, 0))
        combo = "/".join(x for x in (keys.pretty(self.lib.settings, "search"),
                                     keys.pretty(self.lib.settings,
                                                 "search_alt")) if x)
        lead = f"{combo} to search " if combo else "Search "
        self.stations_page.search.setPlaceholderText(
            f"{lead}{n} frequencies, or tag:80s" if n == total
            else f"{lead}{n} frequencies")

    def _promote_tag_text(self):
        """Turn typed tag: syntax into a pill on Return.

        The text form already filters — it has to, since it is what the
        pills are made of — but leaving it as text means the tag is
        invisible once you look away from the field. Entering it makes it
        a pill, which is where every other tag in the app lives.
        """
        field = self.stations_page.search
        words = field.text().split()
        kept, changed = [], False
        for word in words:
            lowered = word.lower()
            if lowered.startswith("tag:") and len(word) > 4:
                self.sidebar.set_tag_state(word[4:], "include")
                changed = True
            elif lowered.startswith("-tag:") and len(word) > 5:
                # The minus is the difference between "only these" and
                # "anything but these", and it survives the promotion.
                self.sidebar.set_tag_state(word[5:], "exclude")
                changed = True
            else:
                kept.append(word)
        if changed:
            field.setText(" ".join(kept))
            self._filter()

    def _filter(self):
        states = self.sidebar.tag_states()
        include = {n for n, s in states.items() if s == "include"}
        exclude = {n for n, s in states.items() if s == "exclude"}
        n = self.stations_page.list.filter(
            self.stations_page.search.text(), include, exclude)
        total = len(self._rows)
        # The count lives in the search box rather than on a line of its
        # own under the list: it is a fact about what you are searching,
        # and it was costing a whole row to say so.
        # The number that matters is what you are searching, which after
        # a filter is the filtered set. "325 frequencies — 7 shown" made
        # you do the subtraction to find out you were searching seven.
        self._search_counts = (n, total)
        self._update_search_placeholder()

    SLEEP_CHOICES = (15, 30, 45, 60, 90, 120)

    def _sleep_menu(self, anchor):
        """How long to keep playing, or stop counting.

        The durations are the ones people actually pick — a field to type
        minutes into is a question where a short list is an answer.
        """
        from PyQt5.QtWidgets import QMenu

        desk = getattr(self, "desktop", None)
        running = bool(getattr(desk, "sleep_active", False))

        menu = QMenu(self)
        for minutes in self.SLEEP_CHOICES:
            label = (f"{minutes} minutes" if minutes < 60
                     else f"{minutes // 60} hour"
                     + ("s" if minutes >= 120 else "")
                     + (f" {minutes % 60} minutes" if minutes % 60 else ""))
            act = menu.addAction(label)
            act.triggered.connect(
                lambda _c, n=minutes: self._start_sleep(n))

        if running:
            menu.addSeparator()
            stop = menu.addAction("Cancel the sleep timer")
            stop.triggered.connect(self._cancel_sleep)

        open_menu(menu, anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _start_sleep(self, minutes):
        desk = getattr(self, "desktop", None)
        if desk is None:
            return
        self.lib.settings["sleep_minutes"] = int(minutes)
        self.lib.save_settings()
        desk.start_sleep_timer(minutes)
        self.player.set_sleep_active(True)
        self.notify(f"Sleeping in {minutes} minutes.", transient=True)

    def _cancel_sleep(self):
        desk = getattr(self, "desktop", None)
        if desk is not None:
            desk.cancel_sleep_timer()
        self.player.set_sleep_active(False)
        self.notify("Sleep timer canceled.", transient=True)

    def refresh_schedule(self):
        """Redraw the schedule — its ruler and editors show times."""
        page = getattr(self, "schedule_page", None)
        if page is not None:
            page.reload()
            page.grid.update()
            self._refresh_live()

    def open_health_log(self):
        """A window showing every background check as it happens."""
        from healthlog import HealthLogWindow

        if getattr(self, "health_log", None) is None:
            self.health_log = HealthLogWindow(self)
        self.health_log.show()
        self.health_log.raise_()
        self.health_log.activateWindow()

    def open_classic(self):
        """Swap this window for the skinned stack."""
        from classic import ClassicSession
        self._drop_visualizer()
        if self._classic is None:
            self._classic = ClassicSession(self, self.lib, self.playback)
            self._classic.closed.connect(self._classic_closed)
        if self._classic.open():
            self.hide()
            self.companion.tip("winamp")
            self.companion.tip_if_idle("winampeq")
            self._llama_once()
            return

        # Only now the locker, and only as a way to fix it.
        #
        # The button's job is to open the player; the locker is what you
        # need when there is nothing to open it with. Reaching it every
        # time made the button look like it opened the wrong thing.
        self._classic.open_skin_locker(self)
        if self._classic.open():
            self.hide()
            self.companion.tip("winamp")
            self.companion.tip_if_idle("winampeq")
            self._llama_once()
            return
        self._classic = None
        self.notify("No skin chosen, so the classic player has "
                    "nothing to draw itself with.", transient=True)

    LLAMA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "sounds", "winamp-llama.mp3")

    def _llama_once(self):
        """Winamp's own greeting the first time the Winamp view opens
        (build 114, task 154). Only into silence: with a station or song
        playing it waits for a later opening, and once played it never
        plays again."""
        import crashlog
        if self.lib.settings.get("winamp_llama_played"):
            return
        if self.player._playing or not os.path.exists(self.LLAMA):
            crashlog.write("[winamp] llama clip not played: "
                           + ("something is playing" if self.player._playing
                              else "the clip is missing"))
            return
        self.lib.settings["winamp_llama_played"] = True
        self.lib.save_settings()
        crashlog.write("[winamp] first Winamp view: playing the llama clip")
        engine = self.playback.player
        engine.clips.play(self.LLAMA, engine._base_volume)

    def open_visualizer(self):
        """The visualizer stands on its own — it does not need the skinned
        stack up, so opening it here builds the session without showing it
        if one isn't already running.

        One session, kept (build 68, task 56). Each press used to build a
        new one and start its audio capture, so the button never closed
        the visualiser — a second press opened a second window — and
        every press left one more capture running. The session is only
        a holder for the skin and the window here; its own capture feeds
        the Winamp player's analyser, which isn't open, so it isn't
        started.
        """
        from classic import ClassicSession
        if self._classic is not None:
            session, how = self._classic, "the Winamp view's session"
        else:
            session = self._vis_session
            if session is None or session.player is not None:
                session = ClassicSession(self, self.lib, self.playback)
                how = "a new session"
            else:
                how = "the same session as before"
            if session.visualizer_window() is None:
                # Opening: the skin may have changed since last time.
                session._skin = session.load_skin()
        self._vis_session = session
        import crashlog
        crashlog.write(f"[vis] Visualizer button, using {how}")
        try:
            session.toggle_visualizer()
        except ImportError as exc:
            self.notify(f"The visualizer isn't available in this build "
                        f"({exc}).")
            return
        self.companion.tip("visualizer", self.sidebar.vis_btn)

    def _drop_visualizer(self):
        """It is parented to whichever view opened it, and its audio tap
        belongs to that session, so it goes when the view does rather than
        floating over the other one."""
        for holder in (self._classic, self._vis_session):
            if holder is None:
                continue
            try:
                window = holder.visualizer_window()
                if window is not None:
                    window.close()
            except Exception:                   # noqa: BLE001
                pass
            holder._visualizer = None

    def _classic_closed(self):
        self._drop_visualizer()
        self._classic = None
        self.show()

    def shutdown(self):
        """Called on quit, before the QApplication goes away."""
        self._log_listening(final=True)
        self._listen_timer.stop()
        # First, before anything is torn down: from here no lookup
        # (Twitch, YouTube, iBroadcast renewal, Discover lists) sends
        # anything back. A running sign-in renewal gets up to 2 s to
        # finish; the rest are daemon threads and end with the app
        # (build 46).
        import jobs
        jobs.Job.stop_all(wait_ms=2000)
        self.shutdown_scaler()
        links = getattr(self, "_link_watch", None)
        if links is not None:
            links.uninstall()
        watch = getattr(self, "_twitch_watch", None)
        if watch is not None:
            watch.stop()
        upgrade = getattr(self, "_art_upgrade", None)
        if upgrade is not None:
            upgrade.stop()
        keeper = getattr(self, "_ib_keeper", None)
        if keeper is not None:
            keeper.stop()
        self._ib_login_cancel()
        # Every visualiser window, closed and deleted while Qt is still
        # here (build 68): one opened from the sidebar has no Winamp view
        # to close it, and was left for Python's own clean-up. Before the
        # Winamp view closes its own, so the log says how many were still
        # open at quit (`[vis] quit: closing N visualiser window(s)`,
        # task 69).
        try:
            import visualizer
            visualizer.close_all("quit")
        except ImportError:
            pass
        if self._classic is not None:
            self._classic.close()
        self._drop_visualizer()
        self._close_chat_window()
        self.discover_page.shutdown()
        if self.feeds is not None:
            self.feeds.shutdown()
        self._playback_timer.stop()
        self._age_timer.stop()

    def _setting_changed(self, key):
        if key == "theme":
            self.apply_theme(self.lib.settings.get("theme", "midnight"))
            return
        if key == "artist_lookup":
            self.panel.set_artist_enabled(self.artist_lookup_on())
            return
        if key == "lyric_size":
            size = int(self.lib.settings.get("lyric_size", 13))
            self.panel.lyrics.view.set_text_size(size)
            self.now_playing.lyrics.set_text_size(size)
            return
        """A few settings have to reach something other than the settings
        file: the health monitor and the tray are built at startup."""
        if key == "eq_advanced" and self.playback.current is not None:
            # Switching between three and ten bands has to reach the player
            # or the change only takes effect on the next station.
            self.playback.apply_eq(self.playback.current)
            return
        if key in ("health_recheck", "health_recheck_minutes", "tray_icon") \
                and self.desktop is not None:
            self.desktop.reconfigure()

    def _page_entered(self, key):
        """Focus whatever the destination is for."""
        # A page catches up with the accent as it appears, which nobody
        # can see happening because the page is arriving anyway.
        if getattr(self, "accent", None) is not None:
            self.accent.refresh_lazy(self.pages.currentWidget())
        # What Clippy says follows the page (task 143).
        if getattr(self, "companion", None) is not None:
            QTimer.singleShot(0, self.companion.update)
        if key == "discover":
            # Signing in happens elsewhere and the badge has to catch up.
            self.discover_page.refresh_connections()
            self.discover_page.query.setFocus()
            # Clippy's tip 1 told them to come here: straight on to tip
            # 1.5, no leave animation (task 186).
            self.companion.move_on("welcome", "search")
        elif key == "schedule":
            self.companion.tip("schedule", self.schedule_page)

    # -- adding stations ---------------------------------------------------

    def add_station_menu(self, anchor=None):
        """One entry for both kinds: a URL you already have, or music you
        own. They end up as the same thing — a row in the same list."""
        menu = self._menu()
        menu.addAction("From a stream URL…", self.add_from_url)
        menu.addAction("From a shared code…", self.add_from_code)
        menu.addAction("From your own music…", self.add_custom_station)
        # No point offering the directory to someone already standing in it.
        if self.pages.currentIndex() != self._page_index.get("discover"):
            menu.addSeparator()
            menu.addAction("Browse radio-browser…",
                           lambda: self.sidebar._select("discover"))
        point = (anchor.mapToGlobal(anchor.rect().bottomLeft())
                 if anchor is not None else self.cursor().pos())
        open_menu(menu, point)

    def save_station_code(self, station):
        """Write the station's QR straight to a file.

        The share window is the place to look at it; this is for when you
        already know you want the picture — printing it, putting it in a
        message, sticking it on a poster.
        """
        import stationlist
        from PyQt5.QtWidgets import QFileDialog
        from sharedialog import qr_pixmap

        name = storage.station_display_name(station)
        code, _shrunk = storage.station_to_code(station)
        pixmap = qr_pixmap(code, 520, name,
                           stationlist.station_color(station))
        if pixmap is None:
            self.notify("This frequency has too much on it for a QR code.",
                        transient=True)
            return
        safe = "".join(c for c in name if c.isalnum() or c in " -_").strip()
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save the code", f"{safe or 'station'}.png",
            "PNG image (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        self.notify("Saved." if pixmap.save(path, "PNG")
                    else "Couldn't write that file.", transient=True)

    def add_shared(self):
        """Stations somebody sent, as pictures of their codes.

        A file picker straight away, rather than a question first: a
        shared station is a QR image now, so there is only one way in.
        Several can be chosen at once — people share a handful at a time.
        """
        import os
        import qrread
        from PyQt5.QtWidgets import QFileDialog, QMessageBox

        if not qrread.available():
            QMessageBox.information(
                self, "Shared frequencies",
                "This build cannot read QR codes — the decoder is missing.")
            return

        paths, _filter = QFileDialog.getOpenFileNames(
            self, "Open shared frequencies", os.path.expanduser("~"),
            "Shared frequencies (*.png *.jpg *.jpeg *.webp *.bmp *.zip);;"
            "All files (*)")
        if not paths:
            return
        # A share zip carries several at once (build 105).
        for zipped in [p for p in paths if p.lower().endswith(".zip")]:
            self.import_share_zip(zipped)
        paths = [p for p in paths if not p.lower().endswith(".zip")]
        if not paths:
            return

        if len(paths) == 1:
            self._import_one(paths[0])
        else:
            from importwindow import ImportWindow
            window = ImportWindow(paths, self._import_station, self)
            window.finished_all.connect(
                lambda n: n and (self.reload(), self._refresh_new_badge()))
            window.show()
            window.start()

    def _import_station(self, path):
        """One picture in, one station added — or a reason why not.

        Returns (ok, message). Kept separate from the window so a single
        import and a batch go through exactly the same steps.
        """
        import qrread

        try:
            station = qrread.read_station(path)
        except qrread.QRError as exc:
            return False, str(exc)
        except Exception:                       # noqa: BLE001
            return False, "That picture could not be read."

        url = (station.get("url") or "").strip().lower()
        if url and any((s.get("url") or "").strip().lower() == url
                       for s in self.lib.stations):
            return False, "Already in your frequencies."

        station["is_new"] = True
        station = storage._normalize_station(station)
        self.lib.stations.append(station)
        self.lib.save_stations()
        self._find_full_art([station])
        return True, storage.station_display_name(station)

    def _find_full_art(self, stations):
        """Stations that arrived with a thumbnail of their art (a QR code,
        a code, a share zip): find the same picture at full size, in the
        background (build 126, task 178)."""
        upgrade = getattr(self, "_art_upgrade", None)
        if upgrade is not None:
            for station in stations:
                upgrade.queue(station, force=True)

    def _refresh_new_badge(self):
        """Show "New!" on Saved Frequencies while anything is unplayed."""
        count = sum(1 for s in self.lib.stations if s.get("is_new"))
        setter = getattr(self.sidebar, "set_new_badge", None)
        if setter is not None:
            setter(count)

    def _import_one(self, path):
        ok, message = self._import_station(path)
        if ok:
            self.reload()
            self._refresh_new_badge()
            self.notify(f"Added {message}.", transient=True)
        else:
            self.notify(message, transient=True)

    def add_from_code(self):
        """Rebuild a station somebody sent as a code.

        The other half of the share window. A code carries the station
        rather than pointing at it, so this needs no network and nothing
        hosted anywhere.
        """
        from PyQt5.QtWidgets import QInputDialog
        text, ok = QInputDialog.getMultiLineText(
            self, "Add from a code",
            "Paste the station code (it starts with FREQ1:):", "")
        if not ok:
            return
        station = storage.station_from_code(text)
        if station is None:
            self.notify("That isn't a station code.", transient=True)
            return
        station = storage._normalize_station(station)
        # Its cover came through as a thumbnail; add_station goes and
        # finds the real one.
        self.add_station(station)

    def add_from_url(self):
        from dialogs import AddStationDialog
        d = AddStationDialog(self)
        if not d.exec_():
            return
        name, url = d.values()
        self.add_station(storage._normalize_station(
            {"name": name or url, "url": url}))

    def add_custom_station(self):
        """A folder of music or a YouTube playlist, played like a broadcast.

        Neither has a real stream URL, but the rest of the app keys off one,
        so they get a stable local:// or youtube:// marker instead.
        """
        from dialogs import LocalStationDialog
        d = LocalStationDialog(self)
        if not d.exec_():
            return
        values = d.values()
        # values() substitutes "Custom station" for an empty name, so it can
        # never tell us the user left it blank. Ask the field itself.
        named = bool(d.name_edit.text().strip())

        if values["kind"] == "youtube":
            playlist = values["playlist_url"]
            if not playlist:
                self.notify("No playlist URL given.")
                return
            import youtube
            if not youtube.available():
                self.notify("YouTube stations need yt-dlp: "
                            "pip install --user yt-dlp")
                return
            source_url = "youtube://" + playlist
            if not named:
                # A station called "PLx7f2…" tells you nothing, and the
                # playlist already has a perfectly good name on YouTube.
                # Asking for it goes over the network, so it runs off the
                # GUI thread and the add finishes when it answers.
                self.notify("Looking up the playlist…")

                def named_it(title, _error):
                    values["name"] = title or "YouTube"
                    self._finish_custom_station(values, source_url)
                self._look_up(lambda: youtube.playlist_title(playlist),
                              named_it)
                return
            self._finish_custom_station(values, source_url)
        else:
            folder = values["local_path"]
            if not folder:
                self.notify("No folder chosen.")
                return
            tracks = storage.scan_local_tracks(folder)
            if not tracks:
                self.notify("That folder has no audio files in it.")
                return
            self._finish_custom_station(values, "local://" + folder,
                                        len(tracks))

    def _finish_custom_station(self, values, source_url, track_count=0):
        """The second half of add_custom_station, once the name is known."""
        station = storage._normalize_station({
            "name": values["name"],
            "kind": values["kind"],
            "local_path": values["local_path"],
            "playlist_url": values["playlist_url"],
            # A YouTube station has no favicon but always has a video
            # thumbnail, so "stream" art is the useful default for it.
            "art_source": "stream" if values["kind"] == "youtube" else "auto",
            "url": source_url,
        })
        self.add_station(station)
        if values["kind"] == "local":
            self.notify(f"Added {values['name']} — {track_count} tracks.")
        else:
            self.notify(f"Added {values['name']}. The playlist is read the "
                        f"first time it plays.")

    def _add_from_source(self, key, text):
        """One handler for the six icons on Add Frequency…

        Each one ends in a station record; what differs is only where the
        text came from and what it means.
        """
        if key == "local":
            self.add_custom_station()
            return
        if key == "sniffer":
            self.options_page.open_sniffer()
            return
        if key == "url":
            self.add_station(storage._normalize_station(
                {"name": text, "url": text}))
            return
        if key == "youtube":
            import youtube
            if not youtube.available():
                self.notify("YouTube stations need yt-dlp: "
                            "pip install --user yt-dlp")
                return
            if not youtube.looks_like_playlist(text):
                self.notify("That doesn't look like a YouTube playlist link.", transient=True)
                return
            # The playlist's name comes from YouTube, over the network —
            # off the GUI thread, and the station is added when it answers.
            self.notify("Looking up the playlist…")

            def named_it(title, _error):
                self.add_station(storage._normalize_station({
                    "name": title or "YouTube",
                    "kind": "youtube", "playlist_url": text,
                    "art_source": "stream", "url": "youtube://" + text}))
            self._look_up(lambda: youtube.playlist_title(text), named_it)
            return
        if key == "shared":
            self.add_shared()
            return
        if key == "twitch":
            self.add_twitch(text)
            return
        if key == "ibroadcast":
            self.add_ibroadcast(text)

    def add_twitch(self, text):
        """A Twitch channel as an audio-only station."""
        import twitch
        if not twitch.available():
            self.notify("Twitch channels need yt-dlp: "
                        "pip install --user yt-dlp", transient=True)
            return
        name = twitch.channel_name(text)
        if not name:
            self.notify("That doesn't look like a Twitch channel.", transient=True)
            return

        # twitch.resolve can take up to 25 s, so it runs off the GUI
        # thread and the station is added when it answers.
        self.notify(f"Looking up {name}…", transient=True)
        self._look_up(lambda: twitch.resolve(name),
                      lambda result, error: self._twitch_looked_up(
                          name, result, error))

    def _twitch_looked_up(self, name, result, error):
        """add_twitch, once Twitch has answered."""
        import twitch
        if error is not None:
            url, reason = "", f"lookup failed ({error})"
        else:
            url, reason, _headers = result
        if not url:
            # Say which kind of nothing it is.
            #
            # Offline is not an error — the station is worth keeping until
            # they go live — but a yt-dlp failure is, and reporting both
            # as "not live" sent people looking at the wrong thing.
            self.notify(f"{name}: {reason or 'nothing to play'} — added "
                        f"anyway.", transient=True)
        self.add_station(storage._normalize_station(
            twitch.station_for(name, url)))

    def _look_up(self, fn, then):
        """Run fn() off the GUI thread, then then(result, error) on it.

        For the network calls behind Add Frequency…, which used to freeze
        the window for seconds. `error` is the exception fn() raised, or
        None. The Lookup keeps itself alive until it answers and deletes
        itself after (jobs.Job).
        """
        import workers
        job = workers.Lookup(fn, self)

        def answered(result, error):
            try:
                then(result, error)
            except Exception as exc:            # noqa: BLE001
                # An exception escaping a signal handler can take the
                # whole app down; log it and say so instead.
                import traceback
                try:
                    import crashlog
                    crashlog.write("[lookup] " + traceback.format_exc())
                except Exception:               # noqa: BLE001
                    pass
                self.notify(f"That failed: {exc}", transient=True)
        job.done.connect(answered)
        job.start()

    def _ib_fail(self, text):
        """Say it on screen as well as in the log.

        Every dead end in this flow used to be a silent return or a line
        in a file nobody has open, which is why "nothing appears" was the
        only symptom anyone ever saw.
        """
        self._ib_log("stopped: " + text)
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(self, "iBroadcast", text)

    def _ib_log(self, text):
        """Every step of adding an iBroadcast station, to the log.

        This flow crosses two network calls, a dialog and a station
        write, and when it goes wrong the only symptom is that nothing
        appears — which is the least informative failure there is.
        """
        try:
            import crashlog
            crashlog.write("[ibroadcast] " + text)
        except Exception:                       # noqa: BLE001
            pass

    def _source_menu(self, key, where):
        """Right-click on a source tile.

        Only iBroadcast has anything to offer so far: it is the one
        source you are signed in to, and signing out had no home in the
        interface at all — it was in Settings, several screens from the
        tile that says you are connected.
        """
        if key == "twitch":
            self._twitch_tile_menu(where)
            return
        if key != "ibroadcast":
            return
        import ibroadcast as ib

        menu = self._menu()
        signed_in = bool(ib.load_tokens().get("refresh_token"))

        # Run the choice after the menu has gone.
        #
        # A menu action fires inside the menu's own exec_ loop, while it
        # still holds the mouse grab — so a dialog opened from here comes
        # up behind it and is dismissed by the same click that chose the
        # item. That is why "Add a playlist…" appeared to do nothing at
        # all. A zero millisecond timer runs it once the menu has closed.
        def later(action):
            def run():
                try:
                    action()
                except Exception as exc:        # noqa: BLE001
                    # Nothing swallows an exception like a signal handler.
                    #
                    # A menu action that raises is reported to stderr, and
                    # an AppImage has no terminal to report to — so the
                    # click did nothing, said nothing, and logged nothing.
                    import traceback
                    self._ib_log("action crashed: " + traceback.format_exc())
                    self._ib_fail(f"That failed: {exc}")
            return lambda: QTimer.singleShot(0, run)

        pick = menu.addAction("Add a playlist…")
        pick.triggered.connect(later(self.add_ibroadcast))
        pick.setEnabled(signed_in)

        menu.addSeparator()
        if signed_in:
            out = menu.addAction("Sign out of iBroadcast")
            out.triggered.connect(later(self._ibroadcast_sign_out))
        else:
            # Something to press, not a greyed-out statement of fact.
            sign_in = menu.addAction("Sign in to iBroadcast…")
            sign_in.triggered.connect(later(self._ibroadcast_login))

        opts = menu.addAction("Settings…")
        opts.triggered.connect(later(self._open_ibroadcast_settings))

        open_menu(menu, where)

    def _twitch_tile_menu(self, where):
        """Right-click on the Twitch tile (task 99): chat sign-in or
        sign-out, and Settings > Twitch chat. Same look as iBroadcast's."""
        import twitchauth
        menu = self._menu()
        login = twitchauth.signed_in_as()

        # Run the choice after the menu has gone (see _source_menu).
        def later(action):
            return lambda: QTimer.singleShot(0, action)

        if login:
            out = menu.addAction(f"Sign out of Twitch chat ({login})")
            out.triggered.connect(later(self._chat_sign_out))
        else:
            sign_in = menu.addAction("Sign in to Twitch chat…")
            sign_in.triggered.connect(later(self._chat_sign_in))
        opts = menu.addAction("Twitch settings…")
        opts.triggered.connect(later(self._open_twitch_settings))
        open_menu(menu, where)

    def _open_twitch_settings(self):
        """Settings, on the Accounts tab at the Twitch chat section."""
        self.sidebar._select("options")
        page = self.options_page
        page.ensure_built()
        target = getattr(page, "chat_status", None)
        if target is not None:
            QTimer.singleShot(0, lambda: page.ensureWidgetVisible(target, 0,
                                                                  120))

    def add_ibroadcast_library(self):
        """Everything in the account, shuffled, without the picker.

        No longer in the menu — "Everything" is the first entry
        in the picker, so a second way in was two names for one thing.
        Kept because the keyboard and the tests still call it.
        """
        self.add_ibroadcast(whole_library=True)

    def _open_ibroadcast_settings(self):
        """Settings, on the Accounts tab at the iBroadcast section."""
        self.sidebar._select("options")
        page = self.options_page
        page.ensure_built()
        target = getattr(page, "ib_status", None)
        if target is not None:
            QTimer.singleShot(0, lambda: page.ensureWidgetVisible(target, 0,
                                                                  120))

    def _ibroadcast_sign_out(self):
        """Forget the tokens, and revoke them.

        A refresh token is a standing credential, so forgetting it here
        while it still works elsewhere is not signing out.
        """
        import ibroadcast as ib
        saved = ib.load_tokens()
        try:
            if saved.get("refresh_token"):
                ib.revoke(saved["refresh_token"])
        except Exception:                       # noqa: BLE001
            pass
        # Remember whose account it was, so signing back into the same
        # one restores the stations rather than leaving them marked dead.
        self.lib.settings["ibroadcast_last_user"] = str(
            (saved.get("user_id") or self.lib.settings.get(
                "ibroadcast_user") or ""))
        ib.clear_tokens()
        self._set_ibroadcast_health(False)
        self.discover_page.refresh_connections()
        self.notify("Signed out of iBroadcast.", transient=True)

    def _set_ibroadcast_health(self, signed_in):
        """Mark every iBroadcast station down, or bring them back.

        Their URLs are signed by the account. Without a sign-in they
        cannot play, so leaving them looking healthy means the background
        check keeps trying them and the list keeps lying about them.
        """
        touched = 0
        for station in self.lib.stations:
            if not station.get("ibroadcast_urls"):
                continue
            entry = storage.health_for(self.lib.health, station.get("id"))
            if signed_in:
                storage.mark_ok(self.lib.health, station.get("id"))
            else:
                entry["down"] = True
                entry["last_error"] = "signed out of iBroadcast"
            touched += 1
        if touched:
            storage.save_health(self.lib.health)
            self.reload()

    def _ibroadcast_custom(self):
        """A stream URL from iBroadcast, kept as a station.

        For the links their web player hands out — a shared playlist, a
        direct file — which the playlist picker knows nothing about.
        """
        from PyQt5.QtWidgets import QInputDialog
        url, ok = QInputDialog.getText(
            self, "iBroadcast stream",
            "Paste an iBroadcast stream URL:")
        url = (url or "").strip()
        if not ok or not url:
            return
        if not url.lower().startswith(("http://", "https://")):
            self.notify("That doesn't look like a URL.", transient=True)
            return
        name, ok = QInputDialog.getText(
            self, "iBroadcast stream", "Name it:", text="iBroadcast")
        self.add_station(storage._normalize_station({
            "name": (name or "iBroadcast").strip(),
            "url": url,
            "kind": "ibroadcast",
        }))
        self.notify("Added.", transient=True)

    def add_ibroadcast(self, url="", station=None, whole_library=False):
        """Sign in if needed, then turn a playlist into a station.

        `whole_library` skips the picker entirely — the answer is already
        known, so asking would be a dialog with one obvious button.
        """
        import ibroadcast as ib

        # Three steps, each waiting on the network: check the sign-in
        # (valid_token may renew it), then read the account and the
        # library. They run off the GUI thread — done inline they froze
        # the window for seconds — and each step starts the next when it
        # answers. One add at a time: a second click while it reads
        # would open two pickers.
        if getattr(self, "_ib_busy", False):
            self.notify("Still reading your iBroadcast library…",
                        transient=True)
            return
        self._ib_busy = True
        self._ib_log("starting")
        self.notify("Looking up your iBroadcast sign-in…", transient=True)
        self._look_up(ib.valid_token,
                      lambda token, error: self._ib_token_checked(
                          token, error, whole_library))

    def _ib_token_checked(self, token, error, whole_library):
        """add_ibroadcast, step 2: signed in? Then read the library."""
        import ibroadcast as ib
        try:
            if error is not None:
                # Refreshing the token reaches the network, and the
                # network can refuse. Unwrapped, that ended the whole flow
                # silently.
                self._ib_busy = False
                self._ib_fail(
                    f"Couldn't check your iBroadcast sign-in: {error}")
                return
            self._ib_log(f"token: {bool(token)}")
            if token:
                # Back in: whatever was marked down for being signed out
                # is playable again.
                self._set_ibroadcast_health(True)
            if not token:
                # The sign-in answers later (task 59): the window keeps
                # running while the browser is out.
                self._ibroadcast_login(
                    lambda fresh: self._ib_signed_in(fresh, whole_library))
                return
            self._ib_read_library(token, whole_library)
        except Exception:
            self._ib_busy = False
            raise

    def _ib_signed_in(self, token, whole_library):
        """add_ibroadcast, after a sign-in it had to wait for."""
        if not token:
            self._ib_busy = False
            self._ib_fail("Sign-in did not complete.")
            return
        try:
            self._ib_read_library(token, whole_library)
        except Exception:
            self._ib_busy = False
            raise

    def _ib_read_library(self, token, whole_library):
        """add_ibroadcast, step 2b: read the account and the library."""
        import ibroadcast as ib
        self.notify("Reading your iBroadcast library…", transient=True)

        def read():
            info = ib.status(token)
            lib = ib.library(token).get("library") or {}
            return info, lib
        self._look_up(read, lambda result, err: self._ib_library_read(
            result, err, token, whole_library))

    def _ib_library_read(self, result, error, token, whole_library):
        """add_ibroadcast, step 3: pick a playlist and add the station."""
        import ibroadcast as ib
        from PyQt5.QtWidgets import (QComboBox, QCheckBox, QDialog,
                                     QDialogButtonBox, QFormLayout, QLabel,
                                     QVBoxLayout)
        self._ib_busy = False
        if error is not None:
            self._ib_fail(f"Couldn't read your library: {error}")
            return
        info, lib = result
        info = info or {}

        settings = info.get("settings") or {}
        user_id = str((info.get("user") or {}).get("id")
                      or (info.get("user") or {}).get("user_id") or "")
        lists = ib.playlists(lib)
        self._ib_log(f"playlists: {len(lists)} | tracks: "
                     f"{len(ib.tracks(lib))} | user: {user_id!r} | "
                     f"server: {(settings or {}).get('streaming_server')!r}")
        if not lists and not whole_library:
            # Having no playlists is not a reason to refuse the library.
            #
            # "Add my whole library" does not need a playlist to exist, but
            # this check ran before the picker either way — so an account
            # with no playlists could not add anything at all.
            self._ib_fail("No playlists came back from iBroadcast.")
            return
        if whole_library and not ib.tracks(lib):
            self._ib_fail("No tracks came back from iBroadcast.")
            return

        d = QDialog(self)
        d.setWindowTitle("iBroadcast playlist")
        d.setMinimumWidth(460)
        outer = QVBoxLayout(d)
        blurb = QLabel("A playlist becomes a station: it plays through, in "
                       "order, like anything else in your list.")
        blurb.setWordWrap(True)
        blurb.setProperty("role", "secondary")
        outer.addWidget(blurb)

        form = QFormLayout()
        picker = QComboBox()
        # The whole library, first and by name.
        #
        # It was reachable only as a setting nobody would think to look
        # for, so the obvious thing — "play everything I have, shuffled"
        # — had no obvious way to ask for it. It belongs at the top of
        # the same list the playlists are in.
        picker.addItem("Everything", "__library__")
        picker.insertSeparator(1)

        # Two of iBroadcast's own lists are left out.
        #
        # "Most recent uploads" and "Recently played" are views of what
        # you did lately, not collections — their contents change under
        # you, so a station built from one plays something different
        # every time for reasons nothing in this app can explain.
        skipped = {"most recent uploads", "recently played"}
        for pid, row in sorted(lists.items(),
                               key=lambda kv: (kv[1].get("name") or "").lower()):
            name = row.get("name") or f"Playlist {pid}"
            if name.strip().lower() in skipped:
                continue
            picker.addItem(name, pid)
        form.addRow("Playlist", picker)

        # A name, because "Night" is a playlist and this is a station —
        # and you may well want more than one station from the same
        # playlist, shuffled and not.
        from PyQt5.QtWidgets import QLineEdit
        name_field = QLineEdit()
        name_field.setPlaceholderText("Name this station")
        form.addRow("Name", name_field)

        # Quality, which is the cap.
        #
        # There used to be a separate "cap at 128" tick beside this, and
        # the two said the same thing twice: asking for 128 *is* capping
        # at 128. A free account is served 128 whatever is requested, so
        # the dropdown is both the request and the ceiling.
        quality = QComboBox()
        for value in ib.BITRATES:
            quality.addItem(ib.BITRATE_LABELS[value], value)
        wanted = str(self.lib.settings.get("ibroadcast_bitrate", "128"))
        index = quality.findData(wanted)
        quality.setCurrentIndex(index if index >= 0 else 1)
        quality.setToolTip("Free accounts are served 128 kbps whatever is "
                           "asked for.")
        form.addRow("Quality", quality)

        shuffled = QCheckBox("Shuffle it")
        shuffled.setToolTip("Tune in partway through a random track, and "
                            "keep picking at random — a station rather "
                            "than a running order.")
        form.addRow("", shuffled)

        def suggest(index):
            """Fill the name with the playlist's, unless it was typed in."""
            if getattr(name_field, "_typed", False):
                return
            chosen = picker.itemText(index)
            name_field.setText("iBroadcast" if picker.itemData(index)
                               == "__library__" else chosen)
            if picker.itemData(index) == "__library__":
                shuffled.setChecked(True)

        name_field.textEdited.connect(
            lambda _t: setattr(name_field, "_typed", True))
        picker.currentIndexChanged.connect(suggest)
        suggest(picker.currentIndex())

        outer.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(d.accept)
        buttons.rejected.connect(d.reject)
        outer.addWidget(buttons)
        # Asked for the whole library outright: no dialog to show.
        if whole_library:
            picker.setCurrentIndex(0)           # "Everything"
        elif not d.exec_():
            return

        bitrate = quality.currentData() or "128"
        self.lib.settings["ibroadcast_bitrate"] = bitrate
        self.lib.save_settings()
        # The account preference matters as well as the URL: it is what the
        # service falls back to for anything that does not ask.


        playlist_id = picker.currentData()
        by_id = ib.tracks(lib)
        if playlist_id == "__library__":
            # Every track there is, shuffled.
            import random
            track_ids = list(by_id.keys())
            random.shuffle(track_ids)
            row = {"name": "iBroadcast"}
        else:
            row = lists.get(playlist_id) or {}
            track_ids = [str(t) for t in (row.get("tracks") or [])]
        # The name tables, once, rather than per track.
        by_artist = ib.artists(lib)
        by_album = ib.albums(lib)

        urls = []
        titles = []
        for track_id in track_ids:
            track = by_id.get(track_id)
            if not track:
                continue
            made = ib.stream_url(settings, track, token, user_id,
                                 bitrate, False)
            if made:
                urls.append(made)
                # The name alongside the URL, so the player can say what
                # is on and the artwork lookup has something to search
                # for. Without it an iBroadcast station plays music with
                # no title and no cover.
                titles.append([
                    ib.name_for(by_artist, track.get("artist_id")),
                    track.get("title") or "",
                    ib.name_for(by_album, track.get("album_id")),
                    # The cover iBroadcast keeps for it (task 23).
                    ib.artwork_url(settings, track, by_album),
                ])
        # A sample of what was skipped, with values — names only told me
        # which fields exist, not which of them are empty.
        if len(urls) < len(track_ids):
            skipped = next(
                (by_id[t] for t in track_ids
                 if by_id.get(t) and not ib.stream_url(
                     settings, by_id[t], token, user_id, bitrate,
                     False)), None)
            if skipped:
                self._ib_log("skipped track: " + ", ".join(
                    f"{k}={skipped.get(k)!r}"
                    for k in ("file", "path", "file_id", "title", "trashed")))
        self._ib_log(f"chose {playlist_id!r}: {len(track_ids)} ids, "
                     f"{len(urls)} playable")
        if not urls:
            self._ib_fail(
                "None of those tracks produced a playable URL. The "
                "streaming server or the account's file list came back "
                "empty.")
            return

        fresh = storage._normalize_station({
            "name": (name_field.text().strip()
                     or row.get("name") or "iBroadcast"),
            "url": urls[0],
        })
        # The whole queue, not just its first track.
        #
        # Only urls[0] was kept, so a playlist was a station that played
        # one song and stopped — and "my whole library" would have been
        # the same one song. The list is what makes it a station.
        fresh["ibroadcast_urls"] = urls
        fresh["ibroadcast_titles"] = titles
        # Covers came with the titles, so no backfill is needed later.
        fresh["ibroadcast_art_checked"] = True
        fresh["kind"] = "ibroadcast"
        fresh["ibroadcast_playlist"] = playlist_id
        fresh["ibroadcast_shuffle"] = (shuffled.isChecked()
                                       or playlist_id == "__library__")
        fresh["ibroadcast_bitrate"] = bitrate
        fresh["ibroadcast_cap"] = False
        self.add_station(fresh)

    # How long the browser gets to come back with the sign-in.
    IB_LOGIN_SECONDS = 120

    def _ibroadcast_login(self, then=None):
        """Open the browser, catch the code, exchange it for tokens.

        Answers later: `then(token)` runs on the window's thread when it
        is over ("" if it failed or was abandoned). Nothing waits here
        (task 59). Before build 69 this waited for the browser in a loop
        of processEvents() for up to two minutes, so everything else ran
        inside the sign-in — quitting during it shut the app down from
        inside the loop, which then carried on with a closed window, and
        a second sign-in started a second loop. Then the code was traded
        for tokens over the network on the window's thread. Now a timer
        looks in on the browser's answer every tenth of a second and the
        exchange runs on a Lookup.
        """
        import ibroadcast as ib
        import secrets
        import time
        import webbrowser
        from PyQt5.QtWidgets import QMessageBox

        then = then or (lambda _token: None)
        waiting = getattr(self, "_ib_login", None)
        if waiting is not None:
            # One sign-in at a time; a second asker waits for the same one.
            waiting["then"].append(then)
            self.notify("Still waiting for iBroadcast in your browser…",
                        transient=True)
            return

        verifier = ib.make_verifier()
        state = secrets.token_urlsafe(16)
        try:
            catcher = ib.RedirectCatcher()
        except OSError as exc:
            QMessageBox.warning(
                self, "iBroadcast",
                f"Couldn't listen on {ib.REDIRECT_URI} ({exc}).\n\n"
                f"Something else is using that port.")
            then("")
            return

        webbrowser.open(ib.authorize_url(verifier, state), new=1,
                        autoraise=True)
        self.notify("Waiting for iBroadcast in your browser…", transient=True)
        self._ib_log("sign-in: waiting for the browser")

        catcher.start()
        timer = QTimer(self)
        timer.setInterval(100)
        self._ib_login = {
            "catcher": catcher, "timer": timer, "then": [then],
            "verifier": verifier, "state": state,
            "deadline": time.monotonic() + self.IB_LOGIN_SECONDS,
        }
        timer.timeout.connect(self._ib_login_tick)
        timer.start()

    def _ib_login_tick(self):
        """Has the browser come back (or the time run out)?"""
        import time
        login = getattr(self, "_ib_login", None)
        if login is None:
            return
        catcher = login["catcher"]
        if not catcher.done() and time.monotonic() < login["deadline"]:
            return
        login["timer"].stop()
        login["timer"].deleteLater()
        catcher.close()
        code = catcher.code
        if not code or catcher.state != login["state"]:
            # A mismatched state means the reply is not the one we asked
            # for, which is the whole reason for sending one.
            self._ib_log("sign-in: " + ("no answer from the browser"
                                        if not code else "state mismatch"))
            self.notify("iBroadcast sign-in was canceled."
                        if not code else "iBroadcast sign-in did not match.")
            self._ib_login_over("")
            return

        import ibroadcast as ib
        verifier = login["verifier"]
        self._ib_log("sign-in: code received, exchanging it")

        def exchange():
            ib.store(ib.exchange_code(code, verifier))
            return ib.load_tokens().get("access_token", "")
        self._look_up(exchange, self._ib_exchanged)

    def _ib_exchanged(self, token, error):
        if error is not None or not token:
            self._ib_log(f"sign-in: exchange failed: {error!r}")
            self.notify(f"iBroadcast sign-in failed: {error}", transient=True)
            self._ib_login_over("")
            return
        self._ib_log("sign-in: done")
        self.notify("Signed in to iBroadcast.", transient=True)
        self.discover_page.refresh_connections()
        self._ib_login_over(token)

    def _ib_login_over(self, token):
        login = getattr(self, "_ib_login", None)
        self._ib_login = None
        for then in (login or {}).get("then", []):
            try:
                then(token)
            except Exception:                   # noqa: BLE001
                import traceback
                self._ib_log("sign-in follow-up crashed: "
                             + traceback.format_exc())

    def _ib_login_cancel(self):
        """On quit: stop waiting for the browser."""
        login = getattr(self, "_ib_login", None)
        self._ib_login = None
        if login is not None:
            login["timer"].stop()
            login["catcher"].close()

    def add_station(self, station):
        """A station arriving from Discover. It is already normalized, so
        this only has to place it and persist."""
        existing = {(s.get("url") or "").lower() for s in self.lib.stations}
        if (station.get("url") or "").lower() in existing:
            self._added_message(
                f"{storage.station_display_name(station)} is already in "
                f"your list.")
            return
        station["sort_index"] = len(self.lib.stations)
        self.lib.stations.append(station)
        self.lib.save_stations()
        self.reload()
        self._find_full_art([station])
        self._added_message(
            f"Added {storage.station_display_name(station)} to your stations.")
        # Tip 14 (task 173) every time, asked first so tips 2 and 12,
        # the newer asks, show before it.
        self._clippy_art_id = station.get("id")
        self.companion.tip("art")
        self.companion.tip("added", self.sidebar._buttons["stations"][0])
        if station.get("kind") == "twitch":
            self._clippy_twitch_id = station.get("id")
            self.companion.tip("twitch")

    def _added_message(self, text):
        """Discover has its own status line; everywhere else uses the panel,
        so a station added from the menu doesn't report into a hidden page."""
        if self.pages.currentIndex() == self._page_index.get("discover"):
            self.discover_page.status.setText(text)
        else:
            self.notify(text)

    # Ordered as you would reach for them: the one you arranged yourself,
    # then the one that never changes, then the three that follow use.
    SORT_MODES = ("manual", "alphabetical", "played", "play_count", "added")

    def _menu(self):
        """A menu wearing the current accent.

        The application sheet is only rewritten at startup now, so a menu
        built later would otherwise carry whatever color it was then.
        """
        menu = QMenu(self)
        if getattr(self, "accent", None) is not None:
            menu.setStyleSheet(self.accent.overlay())
        return menu

    def _sort_menu(self, anchor):
        """How the list looks, then how it is ordered."""
        menu = self._menu()

        columns = menu.addAction("Show details")
        columns.setCheckable(True)
        columns.setChecked(bool(self.lib.settings.get("station_columns")))
        columns.setToolTip("Play count, when it was last played, and when "
                           "it was added")
        columns.triggered.connect(self._toggle_columns)
        menu.addSeparator()

        grid = bool(self.lib.settings.get("station_grid"))
        for label, wants_grid in (("List", False), ("Thumbnails", True)):
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(grid == wants_grid)
            act.triggered.connect(lambda _c, g=wants_grid: self._set_view(g))
        menu.addSeparator()

        # One entry, between the view and the sort modes: which way the
        # sort runs. An arrow rather than two radio buttons, because there
        # are only ever two answers and one of them is already showing.
        current = self.lib.settings.get("sort_mode", "manual")
        if current != "manual":
            descending = self.lib.settings.get("sort_desc")
            if descending is None:
                descending = current != "alphabetical"
            direction = menu.addAction(
                ("↓  " if descending else "↑  ")
                + ("Descending" if descending else "Ascending"))
            direction.setToolTip("Which end of the list to start from")
            direction.triggered.connect(
                lambda _c, d=descending: self._set_sort_direction(not d))
            menu.addSeparator()

        for mode in self.SORT_MODES:
            act = menu.addAction(storage.SORT_LABELS[mode])
            act.setCheckable(True)
            act.setChecked(mode == current)
            act.triggered.connect(lambda _c, mo=mode: self._set_sort(mo))
        menu.addSeparator()
        # A window onto the background checker.
        #
        # Stations get marked down without anyone watching, so the only
        # evidence was a station quietly acquiring a flag. This says what
        # was checked, when, and what came back.
        checks = menu.addAction("Station health checks…")
        checks.triggered.connect(self.open_health_log)

        open_menu(menu, anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _toggle_columns(self, on):
        self.lib.settings["station_columns"] = bool(on)
        self.lib.save_settings()
        self.stations_page.list.set_columns(on)
        self.stations_page.columns_header.setVisible(bool(on))
        self.stations_page.columns_header.update()

    def _set_sort_direction(self, descending):
        """Flip the sort. Manual has no direction — it is the drag order."""
        self.lib.settings["sort_desc"] = bool(descending)
        self.lib.save_settings()
        self.reload()

    def _set_view(self, grid):
        """List rows or artwork tiles. Dragging is off in the grid — an
        icon view lets you drop a tile anywhere, which is not what a manual
        sort order means."""
        if grid == bool(self.lib.settings.get("station_grid")):
            return
        self.lib.settings["station_grid"] = bool(grid)
        self.lib.save_settings()
        self.stations_page.list.set_view_mode(grid)
        if not grid:
            self.stations_page.list.set_manual_sort(
                self.lib.settings.get("sort_mode", "manual") == "manual")

    def _set_sort(self, mode):
        if mode == self.lib.settings.get("sort_mode"):
            return
        self.lib.settings["sort_mode"] = mode
        self.lib.save_settings()
        # setDragEnabled alone lets a row be picked up and gives it nowhere
        # to land, because dropping needs acceptDrops on the view *and* its
        # viewport. set_manual_sort does all three.
        self.stations_page.list.set_manual_sort(mode == "manual")
        self.reload()
        if mode != "manual":
            self.notify(f"Sorted by {storage.SORT_LABELS[mode].lower()}. "
                        f"Switch to manual to drag stations around.")

    def _tag_state_changed(self, name, state):
        self._sync_pills()
        self._filter()

    def _sync_pills(self):
        states = self.sidebar.tag_states()
        self.stations_page.search.set_pills(
            [(n, self.lib.tag_colour(n), s) for n, s in states.items()])

    def _pill_clicked(self, name, right):
        """A pill and its sidebar chip are the same control in two places,
        so clicking either cycles the same way: left requires, right
        excludes, and the same click again clears it."""
        self.sidebar._cycle_tag(name, "exclude" if right else "include")

    def _pills_changed(self):
        """A pill removed in the search box is a tag turned off in the
        sidebar: one filter, two places to see it."""
        kept = {n for n, _c, _s in self.stations_page.search.pills()}
        for name in list(self.sidebar.tag_states()):
            if name not in kept:
                self.sidebar.set_tag_state(name, "off")
        self._filter()

    ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 70, 180, 10

    def zoom_by(self, direction):
        self.set_zoom(int(self.lib.settings.get("ui_zoom", 100))
                      + direction * self.ZOOM_STEP)

    def set_zoom(self, percent, save=True):
        percent = max(self.ZOOM_MIN, min(self.ZOOM_MAX, int(percent)))
        app = QApplication.instance()
        base = getattr(app, "_base_point_size", None)
        if base is None:
            base = app.font().pointSizeF()
            if base <= 0:
                base = 10.0
            app._base_point_size = base
        font = app.font()
        font.setPointSizeF(base * percent / 100.0)
        app.setFont(font)

        # The sheet is what sets the sizes people see, so it is the sheet
        # that has to change. Setting the font alone did nothing: every
        # rule names its own pixel size and a stylesheet beats a font.
        import theme as _theme
        if abs(_theme.ZOOM - percent / 100.0) > 0.001:
            _theme.ZOOM = percent / 100.0
            # The page sheets only carry the accent's colours, never a
            # size, so replacing the application's sheet reaches all text.
            app.setStyleSheet(_theme.stylesheet())
        if save and percent != self.lib.settings.get("ui_zoom", 100):
            self.lib.settings["ui_zoom"] = percent
            self.lib.save_settings()
        if save:
            self.notify(f"Zoom {percent}%")

    def _log_listening(self, final=False):
        """Add the time since the last tick to the playing station.

        Written in 15-second instalments rather than on stop, so a crash or
        a kill loses at most a quarter of a minute rather than the whole
        session.
        """
        import time
        station = self.playback.current
        now = time.time()
        started, self._listening_since = self._listening_since, (
            None if final or station is None else now)
        if station is None or started is None:
            return
        seconds = max(0.0, now - started)
        if seconds < 1:
            return
        station["listen_seconds"] = round(
            float(station.get("listen_seconds") or 0.0) + seconds, 1)
        station["last_listened"] = now
        self.lib.save_stations()

    def _refresh_ages(self):
        """Repaint the rows without rebuilding the list — a rebuild would
        drop the selection and the scroll position every minute."""
        model = self.stations_page.list.model_
        for i in range(model.rowCount()):
            row = model.station_at(i)
            row["location"] = self.lib.subtitle(row["_source"])
        self.stations_page.list.viewport().update()

    def visible_ids(self):
        """The filtered list is the play queue."""
        model = self.stations_page.list.model_
        return [model.station_at(i)["id"] for i in range(model.rowCount())]

    # -- playback ----------------------------------------------------------

    TITLE_CHARS = 28            # what a taskbar entry shows before eliding
    TITLE_STEP = 320            # ms per character

    def _update_title(self):
        """The taskbar entry names the station, and only the station.

        Not the song: the entry is how you find this window among several,
        and a name that changes every three minutes is no use for that.

        A long name scrolls rather than being cut off, in the same looping
        way the player bar does it — the taskbar elides in the middle,
        which is where the distinguishing part of a station name usually
        is.
        """
        station = self.playback.current
        name = (storage.station_display_name(station)
                if station is not None else "Frequencies")
        self._title_text = name
        self._title_pos = 0

        # Scrolls only when the name will not fit.
        #
        # This used a fixed character count, so a name longer than that
        # scrolled even in a wide window with plenty of titlebar to show
        # it whole — movement for no reason. The room is measured from the
        # window's own width now; the name only moves when it genuinely
        # would be cut off.
        if self._title_fits(name):
            self._stop_title_scroll()
            self.setWindowTitle(name)
            return

        if getattr(self, "_title_timer", None) is None:
            self._title_timer = QTimer(self)
            self._title_timer.setInterval(self.TITLE_STEP)
            self._title_timer.timeout.connect(self._title_tick)
        self._title_timer.start()
        self._title_tick()

    # What the window manager draws beside the title: the app icon on one
    # side and the minimise, maximise and close buttons on the other, plus
    # breathing room. Generous on purpose — scrolling a name that would
    # have fitted is better than one that runs under the buttons.
    TITLE_CHROME = 200

    def _title_room(self):
        """Characters of title the titlebar can show at this width."""
        from PyQt5.QtGui import QFontMetrics
        from PyQt5.QtWidgets import QApplication

        metrics = QFontMetrics(QApplication.font())
        per_char = max(1, metrics.averageCharWidth())
        return max(8, (self.width() - self.TITLE_CHROME) // per_char)

    def _title_fits(self, name):
        from PyQt5.QtGui import QFontMetrics
        from PyQt5.QtWidgets import QApplication

        needed = QFontMetrics(QApplication.font()).horizontalAdvance(name)
        return needed <= self.width() - self.TITLE_CHROME

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Whether the name fits depends on the width, so a resize can
        # start or stop the scrolling. Deferred a moment so a drag does
        # not re-measure on every pixel.
        timer = getattr(self, "_title_refit", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(150)
            timer.timeout.connect(self._refit_title)
            self._title_refit = timer
        timer.start()

    def _refit_title(self):
        name = getattr(self, "_title_text", "")
        if not name:
            return
        running = getattr(self, "_title_timer", None)
        scrolling = running is not None and running.isActive()
        if self._title_fits(name):
            if scrolling:
                self._stop_title_scroll()
            self.setWindowTitle(name)
        elif not scrolling:
            self._update_title()

    def _stop_title_scroll(self):
        timer = getattr(self, "_title_timer", None)
        if timer is not None:
            timer.stop()

    def _title_tick(self):
        # Drawn twice with a separator, so the end runs into the start
        # rather than the text bouncing off the edge.
        loop = self._title_text + "   ·   "
        self._title_pos = (self._title_pos + 1) % len(loop)
        room = self._title_room()
        window = (loop + loop)[self._title_pos:self._title_pos + room]
        self.setWindowTitle(window)

    def _station_changed(self, station):
        """Playback tells us what it just started — or that it stopped.

        None means stopped, and everything below reads fields off a
        station. One line that did not guard against it raised inside
        stop(), which aborted the rest of stopping — so pausing left the
        song, the artist and the artwork exactly as they were. Guarding
        once here is better than guarding at each use.
        """
        station = station or {}

        # A renewal still on its way belongs to what was playing before.
        # Tuning in (or stopping) starts afresh, with its one retry back.
        self._ib_resign_for = None
        self._ib_resigned_ids.clear()

        # Played once, no longer new.
        if station.get("is_new"):
            station["is_new"] = False
            held = self.lib.station_by_id(station.get("id"))
            if held is not None:
                held["is_new"] = False
            self.lib.save_stations()
            self._refresh_new_badge()
        # Whatever was being covered is not being covered any more —
        # unless it is this station's own ad, which was started a
        # moment ago by the very call that led here. Clearing
        # unconditionally stopped the glass the instant it appeared.
        if not storage.ad_cover_on(station):
            self.player.set_ad_cover(0)
        # Anything the last station announced belongs to the last station.
        #
        # The held title was only cleared when it was used, so switching
        # away mid-connect left it in the pocket — and the next station to
        # finish connecting showed the previous one's metadata.
        self._pending_meta = ""
        self._song = None
        # brat mode belongs to the song that was playing (task 48). The
        # same station again is its next song (a music folder, iBroadcast
        # and YouTube go through here for each song): the look waits for
        # that song's info instead of flashing capitals in between.
        sid = station.get("id")
        if station and sid == getattr(self, "_brat_station_id", None):
            self.brat.next_song()
        else:
            self.brat.clear("station changed" if station else "stopped")
        self._brat_station_id = sid if station else None
        # And the picture.
        #
        # Artwork was left on screen until the new station produced some
        # of its own, so a station with none at all kept showing the last
        # song Shazam identified — minutes after it stopped playing.
        # The station's own colour, so an empty cover still looks like
        # this station rather than like every other empty one.
        import stationlist
        self.clear_artwork(stationlist.station_color(station),
                           storage.station_display_name(station))
        if self.feeds is not None:
            self.feeds.forget_artwork()

        # A station's own artwork, straight away.
        #
        # Chosen art only reached the bar when Settings was saved while
        # that station happened to be playing. Tuning to it afterwards
        # showed the placeholder until something else fetched a cover —
        # so art that was saved correctly looked like art that had not
        # been saved at all.
        art = (station.get("custom_art_path") or "").strip()
        if art and os.path.exists(art):
            from PyQt5.QtGui import QPixmapCache
            QPixmapCache.remove(art)
            self.set_artwork_data(path=art)

        import time
        # Bank the time on the station that is ending before switching.
        self._log_listening(final=True)
        self._listening_since = time.time() if station is not None else None
        if self.feeds is not None:
            self.feeds.station_changed(station)
        self._twitch_chat_for(station)
        self._update_title()
        if self.desktop is not None:
            self.desktop.push_mpris()
            self.desktop.update_tray_label()
        sid = station.get("id") if station else None
        # bool(station): it is {} when stopped (see the top), and
        # "is not None" showed playing for a moment after every stop.
        self.stations_page.list.set_playing(sid, bool(station))
        self.player.set_playing(bool(station))
        self._place_jump()
        if station:
            # No song yet on a fresh station: the station is the headline
            # until something is identified, and moves down a line when it
            # is.
            self.player.set_now_playing(
                storage.station_display_name(station), "",
                self.lib.subtitle(station))
            self.player.set_shazam(station.get("shazam_auto", True))
            # Per station, so switching a talk station's lookups off takes
            # the tower and the "connecting" line with it rather than
            # leaving a placeholder for something that will never arrive.
            self.panel.set_artist_enabled(self.artist_lookup_on())
            self.panel.clear()
            self._refresh_fullscreen_state()
            self._clear_track_info()
            # A talk station's own info (task 49) outlives the song that
            # just ended: the same station's next song comes through here
            # too, and must not wipe it.
            if self.feeds is not None:
                self.feeds.redraw_station_info()

    def _clear_track_info(self):
        """Lyrics and biography belong to the song that has just ended, so
        they go with it rather than sitting under the next one."""
        self._lyrics = []
        self._bio = ""
        self._lyric_nudge = 0.0
        # The artist belongs to the song that just ended, name and picture
        # both: leaving either up over the next station is worse than an
        # empty panel.
        self.panel.set_track("", "")
        self.panel.set_artist("", "", "")
        self.panel.set_photo(None)
        self.panel.clear_verdict()
        self.now_playing.set_lyrics([], "")
        # Last, once the old track's lyrics and biography are actually
        # gone: called before this, it reported on the station that just
        # ended.
        self._refresh_fullscreen_state()
        self.now_playing.set_track("", "", "", None)
        self.now_playing.set_artist("", "", None)
        self.on_sources(lyrics="", artist="")

    def ad_cover_started(self, seconds):
        """Run the hourglass while something else covers the pre-roll.

        The glass follows the player's own ad clock (build 71, task 80),
        so it runs out when the ad really ends, not on a timer of its own.
        """
        self.player.set_ad_cover(
            seconds, time_left=self.playback.player.ad_time_left)

    def notify(self, text, transient=False):
        """One-line status, in the lyrics column.

        Only for things about what is playing. Progress from a sign-in,
        a library fetch or an ad countdown is about the app, not about
        the station, and putting it here replaced the lyrics with a
        sentence about iBroadcast — which is not what that column is for.
        Those callers pass transient=True and get the player bar instead.
        """
        if not text:
            return
        # Never the lyrics panel.
        #
        # A notice written there replaced the words with a sentence about
        # the app — the panel exists for the song, and a message in it
        # looks like the lyrics have gone wrong. The bar already has a
        # line for this and is where every other notice appears.
        self.player.set_notice(text)

    def _youtube_resolved(self, title, thumbnail):
        """A YouTube station has no ICY metadata, so the video title is the
        readout and its thumbnail is the artwork.

        The title is split into artist and song first. Uploaders put
        everything in there — the label, the resolution, "Official Music
        Video" — and showing that raw is why the readout looked like a
        filename rather than a song.
        """
        station = self.playback.current or {}
        try:
            import crashlog
            crashlog.write(f"[youtube] video title: {title!r}")
        except Exception:                       # noqa: BLE001
            pass
        if title:
            artist, song = storage.clean_video_title(title)
            self._song = (song, artist)
            self.brat.song(artist, song, None, "title")     # task 48
            # The video title is the identified song here, and the
            # station's preference still decides whether to show it.
            wanted = self._readout_choice(shazam=(song, artist)) or (
                song, artist)
            self.player.set_now_playing(
                wanted[0], wanted[1], storage.station_display_name(station))
            self.panel.set_track(song, artist)
        if thumbnail and self.feeds is not None:
            self.feeds.fetch_artwork(thumbnail)

    def _after_skip(self):
        """Follow the station in the list, if that is wanted."""
        if self.lib.settings.get("jump_on_change", True):
            self.jump_to_playing()

    def _on_skip(self, direction):
        # No _after_skip here: playback reports it, so every route —
        # media keys, tray, skinned player — behaves the same.
        (self.playback.next if direction > 0 else self.playback.previous)()

    def _on_play_toggled(self, on):
        if on:
            station = (self.playback.current
                       or self.lib.station_by_id(
                           self.lib.settings.get("last_station_id")))
            self.playback.play(station)
        else:
            # For radio, stop and pause are the same gesture — mpv drops the
            # connection rather than buffering a live stream nobody is
            # listening to.
            # Noted before stopping, not after.
            #
            # stop() clears what is current, so reading it afterwards
            # gives nothing — and nothing has no artwork and no name.
            station = self.playback.current or self.lib.station_by_id(
                self.lib.settings.get("last_station_id")) or {}
            try:                        # task 111: who asked for the pause
                import crashlog
                import traceback
                where = " <- ".join(
                    f"{os.path.basename(f.filename)}:{f.name}"
                    for f in reversed(traceback.extract_stack(limit=6)[:-1]))
                crashlog.write(f"[playstate] pause asked via {where}")
            except Exception:           # noqa: BLE001
                pass
            self.playback.stop()
            # Applied after the stop has finished being handled.
            #
            # Stopping raises a state change of its own, and that handler
            # blanks the readout and puts the placeholder up — running
            # first and being overwritten a moment later is why this
            # looked like it did nothing at all.
            QTimer.singleShot(0, lambda s=station: self._settle_paused(s))

    def _settle_paused(self, station):
        """What a paused station should look like.

        The song, the artist and the stream's own metadata all describe
        something that is no longer happening, so they go. The station
        name stays, because that is what pressing play would start again
        — and so does its artwork, because a paused station is still that
        station.
        """
        self._song = None
        self._pending_meta = ""

        # Pausing does not un-favourite anything.
        #
        # Clearing the readout counts as a song change, which clears the
        # star — but the record on disk still says favourite, so the two
        # disagreed until the next identification. The song stopping is
        # not a change of mind about it.
        starred = self.player.fav_btn.isChecked()
        self.player.set_now_playing(
            "", "", storage.station_display_name(station or {}))
        if starred:
            self.player.fav_btn.blockSignals(True)
            self.player.fav_btn.setChecked(True)
            self.player.fav_btn.blockSignals(False)
            self.player._on_fav(True)

        art = ((station or {}).get("custom_art_path") or "").strip()
        if art and os.path.exists(art):
            self.set_artwork_data(path=art)

    def _on_volume(self, value):
        self.playback.player.set_volume(value)
        self.lib.settings["volume"] = int(value)
        self.lib.save_settings()

    def _on_shuffle(self, on):
        self.lib.settings["shuffle"] = bool(on)
        self.lib.save_settings()

    def _on_favourite(self, on):
        """The star favorites the song, never the station."""
        if not self._song:
            return
        title, artist = self._song
        key = storage.track_key(artist, title)
        station = self.playback.current
        self.lib.set_favourite(
            key, on, track={"title": title, "artist": artist},
            station_name=storage.station_display_name(station or {}))
        self.refresh_history()

    def _shazam_toggled(self, on):
        """The glyph is a per-station switch, not a global one — some
        stations are worth identifying and talk radio never is."""
        station = self.playback.current
        if station is None:
            return
        station["shazam_auto"] = bool(on)
        # Marked as a deliberate choice, and written through by id.
        #
        # Two things could undo this. The one-time "switch identification
        # on for everyone" pass has no way of telling a station that was
        # never configured from one you switched off on purpose, so it
        # turned yours back on. And the station being played is not
        # always the object the library holds, so the change could land
        # on a copy and never reach disk.
        station["shazam_auto_set"] = True
        held = self.lib.station_by_id(station.get("id"))
        if held is not None and held is not station:
            held["shazam_auto"] = bool(on)
            held["shazam_auto_set"] = True
        self.lib.save_stations()
        if self.feeds is not None:
            self.feeds.station_changed(station)

    def attach_scrolling(self, *areas):
        """Anything built later — a dialog, a page that appears once —
        gets the same feel as the rest."""
        import smoothscroll
        self._scrollers.extend(smoothscroll.attach(*areas))

    def apply_theme(self, key):
        """Repaint everything in a new scheme.

        The full sheet goes to the application here rather than the
        accent-only overlay: a theme changes every surface, so there is
        nothing to be gained by being clever, and it happens once when
        somebody chooses it rather than on every song.
        """
        theme.apply_theme(key)
        app = QApplication.instance()
        app.setPalette(theme.palette())
        app.setStyleSheet(theme.stylesheet())
        # The overlays are stale against the new base sheet, and the
        # painted widgets need telling to redraw.
        for widget in getattr(self.accent, "_quick", []):
            widget.setStyleSheet("")
        if getattr(self, "accent", None) is not None:
            self.accent.set_source(
                self.lib.settings.get("accent_source", "artwork"),
                self.lib.settings.get("accent_fixed"))
        # Widgets that keep colours of their own have to be asked; a new
        # stylesheet does not reach a background set in code.
        self.panel.restyle()
        self.help_page.restyle()
        # An empty cover is drawn in the theme's own surface color, so it
        # is drawn again (build 130).
        art = self.player.art
        if art._showing_placeholder() and not art._tint:
            art.set_art(QPixmap())
            if getattr(self, "_art_expanded", False):
                self._redraw_dock_art()
        if self._chat_window is not None:
            self._chat_window.restyle()
        self.player.refresh_accent()
        self._repaint_jump()
        for widget in app.allWidgets():
            widget.update()

    def _play_by_id(self, station_id):
        station = self.lib.station_by_id(station_id)
        if station is not None:
            self.playback.play(station)

    def _show_starting(self, station, show):
        """A scheduled programme is beginning.

        Told rather than tuned to: the schedule says what is on, and
        deciding to listen is still yours to make.
        """
        name = storage.station_display_name(station)
        text = f"{show.get('name', 'A show')} is starting on {name}"
        self.notify(text)
        if self.desktop is not None and hasattr(self.desktop, "toast"):
            self.desktop.toast("Frequencies", text)

    def _hide_extra(self, key):
        """Remember a rail entry the user has switched off."""
        hidden = set(self.lib.settings.get("hidden_extras") or [])
        hidden.add(key)
        self.lib.settings["hidden_extras"] = sorted(hidden)
        self.lib.save_settings()
        # Standing on a page that has just been hidden is a dead end.
        if self.pages.currentIndex() == self._page_index.get(key):
            self.sidebar._select("stations")

    def show_extra(self, key, on):
        hidden = set(self.lib.settings.get("hidden_extras") or [])
        hidden.discard(key) if on else hidden.add(key)
        self.lib.settings["hidden_extras"] = sorted(hidden)
        self.lib.save_settings()
        self.sidebar.apply_extras(hidden)

    def _install_shortcuts(self):
        """Bind every action in the table, and label the controls to match.

        Rebuilt rather than patched when a binding changes: a QShortcut
        holds its key, so the honest way to change one is to throw the old
        one away.
        """
        from PyQt5.QtWidgets import QShortcut
        from PyQt5.QtGui import QKeySequence
        import keys

        for shortcut in getattr(self, "_shortcuts", []):
            shortcut.setParent(None)
        self._shortcuts = []

        actions = {
            "favorite": lambda: self.player.fav_btn.click(),
            "history": lambda: self.sidebar._select("history"),
            "previous": lambda: self.playback.previous(),
            "previous_alt": lambda: self.playback.previous(),
            "play_pause": lambda: self.player.play_btn.click(),
            "next": lambda: self.playback.next(),
            "next_alt": lambda: self.playback.next(),
            "identify": lambda: self.player.shazam_btn.click(),
            "shuffle": lambda: self.player.shuffle_btn.click(),
            "station_settings": self._settings_for_current,
            "fullscreen": lambda: self.player.full_btn.click(),
            "mute": lambda: self.player.vol_icon.click(),
            "search": lambda: self._focus_search(),
            "search_alt": lambda: self._focus_search(),
            "page_stations": lambda: self.sidebar._select("stations"),
            "page_discover": lambda: self.sidebar._select("discover"),
            "page_history": lambda: self.sidebar._select("history"),
            "page_schedule": lambda: self.sidebar._select("schedule"),
            "page_help": lambda: self.sidebar._select("help"),
            "page_options": lambda: self.sidebar._select("options"),
        }
        for name, handler in actions.items():
            sequence = keys.binding(self.lib.settings, name)
            if not sequence:
                continue
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)
        self._label_shortcuts()

    def _refresh_fullscreen_state(self):
        """Say why full screen would be empty, rather than opening empty.

        The view is lyrics on one side and the artist on the other. With
        neither, it is a large dark rectangle with the artwork in it —
        which looks like the app failed rather than like there is nothing
        to show. The button stays clickable, because the artwork alone is
        a legitimate thing to want full screen; the tooltip is what says
        what you will get.
        """
        import keys
        has_lyrics = bool(self._lyrics)
        has_artist = bool(self._bio)
        both_off = (not self.lib.settings.get("lyrics_on", True)
                    and not self.lib.settings.get("artist_on", True))
        button = getattr(self.player, "full_btn", None)
        if button is None:
            return

        # Always available.
        #
        # It was greyed out when lyrics and artist information were both
        # switched off, on the grounds there would be nothing to show. But
        # full screen is the artwork, the song and the moving background
        # as much as it is words — and wanting just that is a perfectly
        # good reason to open it.
        button.setEnabled(True)
        if both_off:
            button.setToolTip("Full screen")
            return

        if has_lyrics or has_artist:
            text = "Full screen"
        elif self.playback.current is None:
            text = "Full screen — nothing is playing yet"
        elif not self.lib.settings.get("artist_lookup", True) \
                and not (self.playback.current or {}).get("lyrics_enabled",
                                                          True):
            text = ("Full screen — lyrics and artist lookups are both off "
                    "for this station, so there would be nothing to show "
                    "but the artwork")
        else:
            text = ("Full screen — no lyrics or artist information yet, so "
                    "it would show the artwork alone")
        button.setToolTip(keys.tip(self.lib.settings, text, "fullscreen"))

    def _label_shortcuts(self):
        """Put each binding into the tooltip of the control it drives."""
        import keys
        s = self.lib.settings
        pairs = (
            (self.player.fav_btn,
             keys.tip(s, "Click to favorite", "favorite") + ", "
             + keys.tip(s, "right click for history", "history")),
            (self.player.prev_btn, keys.tip(s, "Previous station",
                                            "previous", "previous_alt")),
            (self.player.play_btn, keys.tip(s, "Play or pause", "play_pause")),
            (self.player.next_btn, keys.tip(s, "Next station",
                                            "next", "next_alt")),
            (self.player.shazam_btn,
             keys.tip(s, "Identify songs on this station", "identify")),
            (self.player.shuffle_btn, keys.tip(s, "Shuffle stations",
                                               "shuffle")),
            (self.player.eq_btn, keys.tip(s, "Station settings",
                                          "station_settings")),
            # full_btn is left out here: its tooltip says what full
            # screen would contain, which _refresh_fullscreen_state keeps
            # current. Setting a plain label here would overwrite that
            # every time a key was rebound.
            (self.player.vol_icon, keys.tip(
                s, "Unmute" if getattr(self.player, "_muted", False)
                else "Mute", "mute")),
        )
        for widget, text in pairs:
            if widget is not None:
                widget.setToolTip(text)

        for key, name in (("stations", "page_stations"),
                          ("discover", "page_discover"),
                          ("history", "page_history"),
                          ("options", "page_options")):
            entry = self.sidebar._buttons.get(key)
            if entry:
                button, label = entry
                button.setToolTip(keys.tip(s, label, name))
        for attr, name, label in (("schedule_btn", "page_schedule",
                                   "What is on, and when"),
                                  ("help_btn", "page_help",
                                   "Questions and answers")):
            button = getattr(self.sidebar, attr, None)
            if button is not None:
                button.setToolTip(keys.tip(s, label, name))
        self._update_search_placeholder()

    def _focus_search(self):
        """Whatever search belongs to the page in view."""
        page = self.pages.currentWidget()
        field = getattr(page, "search", None) or getattr(page, "query", None)
        if field is None:
            self.sidebar._select("stations")
            field = self.stations_page.search
        field.setFocus()
        field.selectAll()

    def _settings_for_current(self):
        """Alt+J: the settings for whatever is playing.

        It used to open the two-field rename box, which is not what
        "station settings" means to anyone.
        """
        station = self.playback.current
        if station is not None:
            self.station_options(self.lib.row(station))

    def rebind_keys(self):
        """Called from Settings when a binding changes."""
        self._install_shortcuts()

    def _artist_verdict(self, verdict):
        """A thumb, and a pause before a thumbs-down is believed.

        Fifteen seconds because the first press is often a mis-click or a
        change of mind: someone taps down, looks again, and taps up. Only
        what is still selected when the timer expires gets written.
        """
        self._pending_verdict = (verdict, self.panel.heading.text().strip())
        self._pending_what = ""
        if verdict == "down":
            # Build 98, task 136: which part is wrong. Cancel takes the
            # thumb back and reports nothing.
            self._pending_what = self._ask_what_wrong(self._pending_verdict[1])
            if not self._pending_what:
                self._pending_verdict = ("", "")
                if getattr(self, "_verdict_timer", None) is not None:
                    self._verdict_timer.stop()
                self.panel.clear_verdict()
                return
        if getattr(self, "_verdict_timer", None) is None:
            self._verdict_timer = QTimer(self)
            self._verdict_timer.setSingleShot(True)
            self._verdict_timer.setInterval(15000)
            self._verdict_timer.timeout.connect(self._store_verdict)
        if verdict == "down":
            self._verdict_timer.start()
        else:
            self._verdict_timer.stop()

    def _ask_what_wrong(self, artist):
        """Bio, Photo or Both? Returns "bio", "photo", "both" or "" when
        canceled."""
        from PyQt5.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle("What's wrong?")
        box.setText(f"What is wrong about “{artist}”?")
        buttons = {box.addButton(text, QMessageBox.AcceptRole): key
                   for text, key in (("Bio", "bio"), ("Photo", "photo"),
                                     ("Both", "both"))}
        cancel = box.addButton("Cancel", QMessageBox.RejectRole)
        box.setEscapeButton(cancel)
        box.exec_()
        return buttons.get(box.clickedButton(), "")

    def _store_verdict(self):
        """Record that this artist's lookup was wrong."""
        verdict, artist = getattr(self, "_pending_verdict", ("", ""))
        if verdict != "down" or not artist:
            return
        if artist != self.panel.heading.text().strip():
            return              # the song moved on; the verdict was not kept
        reports = self.lib.settings.get("artist_reports") or {}
        entry = reports.get(artist.lower()) or {}
        entry["wrong"] = int(entry.get("wrong", 0)) + 1
        entry["source"] = self._artist_source
        what = getattr(self, "_pending_what", "") or "both"
        entry["what"] = what
        reports[artist.lower()] = entry
        self.lib.settings["artist_reports"] = reports
        # And to the developer (build 91, task 114; a GitHub issue filed by
        # the app since task 133), so the bio can be fixed for everyone and go
        # out with the next photo pack.
        import photopack
        import re
        source = re.sub(r"<[^>]+>", "", self._artist_source or "").strip()
        bio = self.panel.artist.bio.text().strip()
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QDesktopServices
        photo = ""
        if what in ("photo", "both"):
            photo = photopack.photo_link(
                artist, getattr(self.panel, "_photo_path", ""))
        photopack.report(
            self.lib.settings, artist, source, bio,
            lambda address: QDesktopServices.openUrl(QUrl(address)),
            what, photo)
        self.lib.save_settings()

    def _photo_pack_changed(self):
        """A new photo pack (or none): show the artist on screen again
        with what it brings."""
        artist = (getattr(self, "_artist_key", "")
                  or self.panel.heading.text()).strip()
        if self.feeds is not None and self.feeds.holds_station_info():
            self.feeds.refresh_station_info()   # a talk station's photo
            return
        if artist:
            self._artist_corrected(artist)

    def _artist_wrong(self):
        """Correct a wrong artist lookup, locally and upstream.

        storage keeps per-artist overrides already — that is what fixes it
        here, permanently, for every future play. MusicBrainz is where the
        data came from, so its edit page is the honest place to send a
        correction that would help anyone else; the app cannot submit on
        your behalf without your account.
        """
        from PyQt5.QtWidgets import QInputDialog, QMessageBox
        artist = self.panel.heading.text().strip()
        if not artist:
            return

        # An override pins the lookup rather than renaming the artist: a
        # MusicBrainz id identifies exactly one act, which is the only
        # thing that reliably fixes two bands sharing a name.
        current = storage.artist_override(artist) or {}
        text, ok = QInputDialog.getText(
            self, "Is this right?",
            f"Point “{artist}” at the right act.\n\n"
            f"Paste a MusicBrainz artist id or a Wikipedia article title.\n"
            f"Leave it empty to clear the correction.",
            text=current.get("mbid") or current.get("wikipedia") or "")
        if not ok:
            return

        value = text.strip()
        overrides = storage.load_artist_overrides()
        key = artist.lower()
        if not value:
            overrides.pop(key, None)
        elif value.count("-") == 4 and len(value) >= 32:
            overrides[key] = {"mbid": value}     # looks like a UUID
        else:
            overrides[key] = {"wikipedia": value}
        storage.save_artist_overrides(overrides)

        if self.feeds is not None:
            self.feeds._artist_shown = ""
            track = getattr(self.feeds, "_track", None)
            if track:
                self.feeds._maybe_fetch_artist(track)
        if QMessageBox.question(
                self, "Is this right?",
                "Saved for this machine.\n\nOpen MusicBrainz to correct it "
                "for everyone? It needs an account there.",
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            import webbrowser
            import urllib.parse
            webbrowser.open(
                "https://musicbrainz.org/search?type=artist&query="
                + urllib.parse.quote(artist), new=1, autoraise=True)

    def _history_menu(self, entry, pos):
        """Where else this song can be heard.

        Search links rather than an integration: no accounts, nothing to
        break when a service changes its terms, and it works for every
        song in the list whether or not it is on any given service.
        """
        import services
        song = entry.get("song", "")
        artist = entry.get("artist", "")
        if not song and not artist:
            return
        menu = self._menu()
        # No title at the top.
        #
        # It repeated the song you had just right-clicked, greyed out,
        # and pushed every real action one line further away.
        for name, _template, _apps in services.SERVICES:
            menu.addAction(
                f"Search {name}",
                lambda _c=False, n=name: services.open_search(
                    self, self.lib.settings, n, song, artist,
                    on_saved=self.lib.save_settings))
        menu.addSeparator()
        starred = bool(storage.is_favorite(entry))
        menu.addAction("Unfavorite" if starred else "Favorite",
                       lambda: (self.lib.set_favourite(
                           storage.entry_key(entry), not starred,
                           track={"title": entry.get("title", ""),
                                  "artist": entry.get("artist", "")},
                           station_name=entry.get("station", "")),
                           self.refresh_history()))
        open_menu(menu, pos)

    @staticmethod
    def can_shuffle_contents(station):
        """Whether this station has contents to put in an order at all.

        A live stream plays what it plays. A folder, a playlist and a
        library each hold a list, and a list can be shuffled.
        """
        station = station or {}
        return bool(station.get("ibroadcast_urls")
                    or station.get("playlist_url")
                    or station.get("local_path"))

    def shuffles_contents(self, station):
        station = station or {}
        if "shuffle_contents" in station:
            return bool(station["shuffle_contents"])
        # iBroadcast had its own flag before this existed; it still wins
        # for stations saved with it.
        if "ibroadcast_shuffle" in station:
            return bool(station["ibroadcast_shuffle"])
        return bool(self.lib.settings.get("shuffle_contents", True))

    def _ibroadcast_resign(self, why=""):
        """A library track was refused: renew the sign-in, then try again.

        The usual reason a track is refused a minute after the last one
        played is that the sign-in ran out. Marking the station down for
        that blamed the station for something a renewal fixes in a
        second — so the first refusal renews and retries, and only a
        refusal with a fresh sign-in counts against the station.

        Renewing reaches the network, so it happens on the keeper's
        thread; the retry follows in _ib_token_refreshed.

        Returns True when a retry is on its way (or already was), so the
        caller does not also count this as the station failing.
        """
        station = self.playback.current or {}
        if not station.get("ibroadcast_urls"):
            return False
        if self._ib_resign_for is not None:
            return True                 # the same failure, reported again
        sid = station.get("id")
        if sid in self._ib_resigned_ids:
            return False                # a fresh sign-in did not help
        self._ib_resigned_ids.add(sid)
        self._ib_resign_for = station
        self._ib_retry_at = time.monotonic()
        try:
            shown = (self.playback.player.current_source() or "")
        except Exception:                       # noqa: BLE001
            shown = ""
        # Without the query: it holds the token.
        self._ib_log(f"track refused ({why or 'no reason given'}): "
                     f"{shown.split('?')[0] or 'unknown track'} — renewing "
                     f"the sign-in and trying again")
        self._ib_keeper.refresh(force=True)
        return True

    def _ib_token_refreshed(self, token, problem):
        """A renewal came back. If a station was waiting on it, go on."""
        station, self._ib_resign_for = self._ib_resign_for, None
        if station is None or self.playback.current is not station:
            return                  # a routine renewal, or you moved on
        if token:
            self._ib_retry_at = time.monotonic()
            self.playback.retry_ibroadcast(station)
            return

        # No fresh sign-in to be had. Now it is fair to say so.
        signed_out = problem.startswith(("rejected", "signed out"))
        reason = ("iBroadcast sign-in expired" if signed_out
                  else "couldn't reach iBroadcast")
        storage.mark_failed(self.lib.health, station.get("id"),
                            reason=reason, down_after=1)
        storage.save_health(self.lib.health)
        self.reload()
        self.notify("iBroadcast needs you to sign in again." if signed_out
                    else "Couldn't reach iBroadcast to renew the sign-in.",
                    transient=True)
        # Never skipped away from by itself (task 156): it stays, so
        # signing in again picks up where it was.
        if (self.lib.settings.get("auto_skip_broken", True)
                and storage.skipped_when_down(station)):
            self.playback.next()

    def _ibroadcast_fall_back(self):
        """Retry a library track at 128 if a higher rate was refused.

        Returns True when a retry was started, so the caller does not
        also count this as the station failing.
        """
        station = self.playback.current or {}
        if not station.get("ibroadcast_urls"):
            return False
        rate = str(station.get("ibroadcast_bitrate") or "128")
        if rate == "128":
            return False                # already at the safe rate

        refused = sorted(set(station.get("ibroadcast_rates_refused") or [])
                         | {rate})
        station["ibroadcast_bitrate"] = "128"
        station["ibroadcast_rates_refused"] = refused
        held = self.lib.station_by_id(station.get("id"))
        if held is not None and held is not station:
            held["ibroadcast_bitrate"] = "128"
            held["ibroadcast_rates_refused"] = refused
        self.lib.save_stations()

        shown = "the original file" if rate == "orig" else f"{rate} kbps"
        # Said as what happened, not as a verdict on the account — a
        # rate that fails is not evidence of which plan anyone is on.
        self.notify(f"{shown.capitalize()} didn't play — "
                    f"switched to 128 kbps.", transient=True)
        self._ib_retry_at = time.monotonic()
        self.playback.play_another(station)
        return True

    def set_ib_bitrate(self, station, rate):
        """Which quality this library station asks for.

        The saved URLs carry the rate in their path, so they are rebuilt
        on the next track rather than rewritten here — the station knows
        how to sign its own links.
        """
        station["ibroadcast_bitrate"] = str(rate)
        # Picking it again is a fresh try, so its old mark goes. If it
        # fails once more it will simply be marked again.
        refused = [r for r in (station.get("ibroadcast_rates_refused") or [])
                   if r != str(rate)]
        station["ibroadcast_rates_refused"] = refused
        held = self.lib.station_by_id(station.get("id"))
        if held is not None and held is not station:
            held["ibroadcast_bitrate"] = str(rate)
            held["ibroadcast_rates_refused"] = refused
        self.lib.save_stations()
        shown = "original" if rate == "orig" else f"{rate} kbps"
        self.notify(
            f"{storage.station_display_name(station)}: {shown} from now on.",
            transient=True)

    def set_shuffle_contents(self, station, on):
        station["shuffle_contents"] = bool(on)
        station["ibroadcast_shuffle"] = bool(on)
        self.lib.save_stations()
        self.notify(
            f"{storage.station_display_name(station)}: "
            + ("shuffled." if on else "in order."), transient=True)

    def share_station(self, station):
        """Write a station out as a file somebody else can open.

        The same format the featured list uses, which already carries
        everything that makes a station yours — the tags, the equalizer,
        the artwork as data rather than a path, the readout and artwork
        preferences, the ad settings. That is the point of sharing one:
        the work you did on it travels, not just the URL.
        """
        import sharezip
        if not sharezip.shareable(station):
            self.notify("iBroadcast frequencies can't be shared.",
                        transient=True)
            return
        # A folder of your own music has no stream to put in a QR code:
        # it goes out as a zip, and can take its audio along (task 130c).
        if storage.is_local_station(station):
            self.share_as_zip([station])
            return
        name = storage.station_display_name(station)
        # The link first, the file second.
        #
        # Most sharing is "send someone this station", which is a link in
        # a message — and a QR code when they are sitting opposite you.
        # The file is for carrying the work: tags, equalizer, artwork.
        from sharedialog import ShareDialog
        code, dropped = storage.station_to_code(station)
        import stationlist
        dialog = ShareDialog(name, station.get("url") or "", code, dropped,
                             stationlist.station_color(station), self)
        self._link_shared_picture(station, dialog)
        # The code, as an image, is the file now. There is no second
        # format to save in — a picture of the code opens on any
        # machine and carries everything the station needs.
        dialog.exec_()

    def _link_shared_picture(self, station, dialog):
        """A picture of your own goes to the picture service, so the code
        can carry a link to it at full size (build 128, artlink.py)."""
        import artlink
        if not artlink.needs_upload(station):
            return
        job = artlink.Upload(station, self)

        def uploaded(station_id, path, link, _reason):
            held = self.lib.station_by_id(station_id) or station
            if link and (held.get("custom_art_path") or "") == path:
                storage.set_art_link(held, link, path)
                self.lib.save_stations()
                code, _dropped = storage.station_to_code(held)
                said = ""
            else:
                code = ""
                said = ("Couldn't upload the picture, so the code carries "
                        "a small copy.")
            try:
                dialog.set_code(code, said)
            except RuntimeError:            # the window was closed
                pass
        job.done.connect(uploaded)
        dialog.wait_for_link()
        job.start()

    def share_as_zip(self, stations):
        """Zip saved frequencies with their rich data and show the zip in
        the file browser (build 105, task 130a/c). iBroadcast ones stay
        out; a local-files one asks whether its audio goes too."""
        from PyQt5.QtWidgets import QMessageBox
        import sharezip

        picked = [self._held_station({"_source": s.get("_source") or s})
                  for s in stations]
        left_out = [s for s in picked if not sharezip.shareable(s)]
        picked = [s for s in picked if sharezip.shareable(s)]
        if not picked:
            self.notify("iBroadcast frequencies can't be shared.",
                        transient=True)
            return
        with_audio = False
        local = [s for s in picked if storage.is_local_station(s)]
        if local:
            names = ", ".join(storage.station_display_name(s)
                              for s in local[:3])
            if len(local) > 3:
                names += ", …"
            answer = QMessageBox.question(
                self, "Share",
                f"Include the audio files too? ({names})\n\n"
                "Without them, the other person picks a folder of their "
                "own music for it.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.No)
            if answer == QMessageBox.Cancel:
                return
            with_audio = answer == QMessageBox.Yes
        path = sharezip.default_path(picked)
        self.notify("Making the zip…", transient=True)

        def done(count, error):
            if error is not None:
                self.notify(f"Couldn't make the zip: {error}",
                            transient=True)
                return
            text = (f"Saved {os.path.basename(path)}"
                    + (f" with {count} audio files" if count else "") + ".")
            if left_out:
                text += " iBroadcast frequencies were left out."
            self.notify(text, transient=True)
            sharezip.show_in_file_browser(path)

        self._look_up(lambda: sharezip.write(picked, path, with_audio), done)

    def import_share_zip(self, path):
        """Bring back every frequency in a share zip (build 105)."""
        import sharezip
        audio_root = os.path.join(os.path.expanduser("~"), "Music",
                                  "Frequencies")

        def done(stations, error):
            if error is not None:
                self.notify(f"Couldn't read that zip: {error}",
                            transient=True)
                return
            self._add_from_zip(stations or [])

        self._look_up(lambda: sharezip.read(path, audio_root), done)

    def _add_from_zip(self, stations):
        from PyQt5.QtWidgets import QFileDialog
        known = {(s.get("url") or "").strip().lower()
                 for s in self.lib.stations}
        added = []
        for station in stations:
            if storage.is_local_station(station) and \
                    not station.get("local_path"):
                name = storage.station_display_name(station)
                folder = QFileDialog.getExistingDirectory(
                    self, f"Choose a music folder for {name}",
                    os.path.expanduser("~"))
                if not folder:
                    continue
                station["local_path"] = folder
                station["url"] = "local://" + folder
            url = (station.get("url") or "").strip().lower()
            if not url or url in known:
                continue
            known.add(url)
            station["is_new"] = True
            station = storage._normalize_station(station)
            station["sort_index"] = len(self.lib.stations)
            self.lib.stations.append(station)
            added.append(station)
        if added:
            self.lib.save_stations()
            self.reload()
            self._refresh_new_badge()
            self._find_full_art(added)
        skipped = len(stations) - len(added)
        self.notify(f"Added {len(added)} frequenc"
                    + ("y" if len(added) == 1 else "ies")
                    + (f" ({skipped} already in your list or skipped)."
                       if skipped else "."), transient=True)

    def _many_menu(self, rows, pos):
        """Several saved frequencies picked: sharing them is the one
        thing to do (task 130a)."""
        m = self._menu()
        m.addAction("Share these frequencies",
                    lambda: self.share_as_zip(rows))
        open_menu(m, pos)

    def _now_playing_menu(self, pos):
        """No Play here: it is already playing, which is why it is in the
        bar at all. Artwork gets its own section, because this is the
        artwork you are looking at."""
        station = self.playback.current
        if station is None:
            return
        self._station_menu(self.lib.row(station), pos, playable=False,
                           artwork=True)

    def open_eq(self):
        """The shortcut beside the transport opens the same dialog on its
        Sound tab, rather than being a second EQ that can disagree with the
        one in Options."""
        station = self.playback.current
        if station is None:
            self.notify("Nothing is playing.")
            return
        self.station_options(self.lib.row(station), tab=0)

    # -- context menu ------------------------------------------------------

    ART_SOURCES = (("Automatic", "auto"), ("Shazam", "shazam"),
                   ("Custom", "custom"))

    def _station_menu(self, row, pos, playable=True, artwork=False):
        station = self._held_station(row)
        m = self._menu()
        if playable:
            m.addAction("Play", lambda: self.playback.play(station))
            m.addSeparator()
        if artwork:
            current = station.get("art_source", "auto")
            for label, value in self.ART_SOURCES:
                act = m.addAction(label)
                act.setCheckable(True)
                act.setChecked(current == value)
                act.triggered.connect(
                    lambda _c, s=station, v=value: self._set_art_source(s, v))
            m.addAction("Choose…", lambda: self._choose_art(station))
            act = m.addAction("Clear")
            act.setEnabled(bool(station.get("custom_art_path")))
            act.triggered.connect(lambda: self._clear_art(station))
            m.addSeparator()

        m.addAction("Tags…", lambda: self.tag_station(row))
        m.addAction("Options…", lambda: self.station_options(row))

        if station.get("ibroadcast_urls"):
            # Quality and shuffle, where the station's own tracks are.
            #
            # Both were only reachable while adding the station, so
            # changing your mind afterwards meant adding it again.
            quality = m.addMenu("Stream quality")
            current = str(station.get("ibroadcast_bitrate") or "128")
            refused = set(station.get("ibroadcast_rates_refused") or [])

            # No 192.
            #
            # On a paid account 128, 320 and the original file all play
            # and 192 does not, so it is not a plan limit — the service
            # simply does not serve that rate. Offering it only guaranteed
            # a failure.
            for rate in ("96", "128", "256", "320", "orig"):
                label = "Original" if rate == "orig" else f"{rate} kbps"
                # Marked one at a time, only where it was actually turned
                # down. Assuming a refusal meant "and everything above it"
                # was wrong: rates are not a ladder, and that rule would
                # have flagged 320 and Original, which work.
                if rate in refused:
                    label += " — didn't play"
                act = quality.addAction(label)
                act.setCheckable(True)
                act.setChecked(rate == current)
                act.triggered.connect(
                    lambda _c, s=station, r=rate: self.set_ib_bitrate(s, r))

        if self.can_shuffle_contents(station):
            shuffle_it = m.addAction("Shuffle its contents")
            shuffle_it.setCheckable(True)
            shuffle_it.setChecked(self.shuffles_contents(station))
            shuffle_it.toggled.connect(
                lambda on, s=station: self.set_shuffle_contents(s, on))

        m.addSeparator()
        # No sharing for iBroadcast; a local-files one shares as a zip,
        # so it has no code to save (build 105, task 130b/c).
        import sharezip
        if sharezip.shareable(station):
            m.addAction("Share station…",
                        lambda: self.share_station(station))
            if not storage.is_local_station(station):
                m.addAction("Save its code as an image…",
                            lambda: self.save_station_code(station))
            m.addSeparator()
        m.addAction("Remove", lambda: self.remove_station(station))
        open_menu(m, pos)

    def preview_station(self, station):
        """Play something from the directory without keeping it.

        It is not in the library, so nothing is saved and the row is not
        marked playing — but everything else works, which is what makes it
        possible to judge a station before adding it.
        """
        known = self.lib.station_by_id(station.get("id"))
        by_url = next((s for s in self.lib.stations
                       if (s.get("url") or "").lower()
                       == (station.get("url") or "").lower()), None)
        self.playback.play(known or by_url or station)

    def remove_by_url(self, url):
        url = (url or "").lower()
        station = next((s for s in self.lib.stations
                        if (s.get("url") or "").lower() == url), None)
        if station is not None:
            self.remove_station(station)

    def _set_art_source(self, station, value):
        """Where this station's artwork comes from. Choosing Custom with no
        file yet goes straight to the picker, rather than silently doing
        nothing until you find Choose."""
        station["art_source"] = value
        self.lib.save_stations()
        if value == "custom" and not station.get("custom_art_path"):
            self._choose_art(station)
            return
        if self.feeds is not None:
            self.feeds.refresh_artwork()
        self.reload()

    def _choose_art(self, station):
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose artwork", os.path.expanduser("~"),
            "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;All files (*)")
        if not path:
            return
        station["custom_art_path"] = path
        station["art_source"] = "custom"
        self.lib.save_stations()
        if self.feeds is not None:
            self.feeds.refresh_artwork()
        self.reload()

    def _clear_art(self, station):
        """Forget the chosen file and fall back to whatever else there is —
        the file itself is left alone."""
        station["custom_art_path"] = ""
        if station.get("art_source") == "custom":
            station["art_source"] = "auto"
        self.lib.save_stations()
        if self.feeds is not None:
            self.feeds.refresh_artwork()
        self.reload()

    def remove_station(self, station):
        """Ask first.

        Removing takes the tags, the EQ, the artwork and the listening
        history with it, and there is no undo — so a mis-aimed click on a
        context menu should not be able to do it.
        """
        from PyQt5.QtWidgets import QMessageBox
        name = storage.station_display_name(station)
        box = QMessageBox(self)
        box.setWindowTitle("Remove station")
        box.setIcon(QMessageBox.Question)
        box.setText(f"Remove “{name}”?")
        box.setInformativeText(
            "Its tags, equalizer settings and artwork go with it. "
            "This cannot be undone.")
        remove = box.addButton("Remove", QMessageBox.DestructiveRole)
        box.addButton("Keep", QMessageBox.RejectRole)
        box.setDefaultButton(remove)
        box.exec_()
        if box.clickedButton() is not remove:
            return
        self._remove_station_now(station)

    def _remove_station_now(self, station):
        self.lib.stations[:] = [s for s in self.lib.stations
                                if s.get("id") != station.get("id")]
        self.lib.save_stations()
        self.reload()

    def edit_station(self, station):
        """dialogs.EditStationDialog covers name and URL. It warns about a
        duplicate URL, so it wants every other station's URL."""
        from dialogs import EditStationDialog
        # Edit the stored station, not a copy.
        #
        # Anything played straight from Add Frequency… without being added
        # is a standalone dict, and renaming that changed something the
        # library has never heard of — the dialog reopened showing the old
        # name because the old name was the only one that existed.
        stored = self.lib.station_by_id(station.get("id"))
        if stored is None:
            url = (station.get("url") or "").lower()
            stored = next((s for s in self.lib.stations
                           if (s.get("url") or "").lower() == url), None)
        if stored is None:
            self.notify("That station isn't in your list yet — add it "
                        "first, then it can be renamed.")
            return
        station = stored
        others = [s.get("url", "") for s in self.lib.stations
                  if s.get("id") != station.get("id")]
        d = EditStationDialog(self, station=station, other_urls=others)
        if d.exec_() != d.Accepted:
            return
        name, url = d.values()
        # "" rather than None: display_name falls back on the directory
        # name when this is empty, and None would be written to the file
        # as a null that the loader has to cope with.
        station["custom_name"] = name if name != station.get("name") else ""
        station["url"] = url
        self.lib.save_stations()
        # A changed URL invalidates what we learned about the old one.
        if d.url_changed():
            station["connect_worst"] = 0.0
            if self.playback.current is station:
                self.playback.play(station)
        self.reload()
        if self.playback.current is station:
            self.player.set_now_playing(
                self.player.song.text(), self.player.artist.text(),
                storage.station_display_name(station))

    def tag_station(self, row):
        """The redesigned dialog does two jobs the old one had tangled:
        assigning tags to this station, and editing the shared tag list.
        Renames, recolors and deletions apply across the whole library."""
        from tagsdialog import TagsDialog
        import crashlog
        station = self._held_station(row)
        assigned = {t.lower() for t in (station.get("tags") or [])}
        tags = [dict(name=n, colour=self.lib.tag_colour(n),
                     count=sum(1 for s in self.lib.stations
                               if n.lower() in {t.lower()
                                                for t in (s.get("tags") or [])}),
                     assigned=n.lower() in assigned)
                for n in storage.library_tags(self.lib.stations,
                                              self.lib.settings)]

        d = TagsDialog(row["name"], tags, self)
        if not d.exec_():
            crashlog.write("[tags] dialog cancelled for %r"
                           % storage.station_display_name(station))
            return
        changes = d.result_changes()
        crashlog.write(
            "[tags] save for %r (id %s, in library: %s): ticked %s, "
            "renamed %s, recoloured %s, new %s, deleted %s"
            % (storage.station_display_name(station), station.get("id"),
               "yes" if self.lib.station_by_id(station.get("id"))
               is station else "NO",
               changes["assigned"], changes["renamed"],
               changes["recoloured"],
               [m["name"] for m in changes["created"]],
               changes["deleted"]))

        # Order matters: rename before anything keyed on the new name, and
        # delete before assigning, so a tag removed from the library can't
        # come back on this station.
        # The colour goes with the name (rename_tags moves it). Colours
        # are kept by name, so without that the new name had none and was
        # handed the next free one from the palette — red turned green
        # (task 40).
        storage.rename_tags(self.lib.stations, self.lib.settings,
                            changes["renamed"])
        for gone in changes["deleted"]:
            storage.remove_tag(self.lib.stations, gone)
            storage.forget_spare_tag(self.lib.settings, gone)
        for name, colour in changes["recoloured"].items():
            storage.set_tag_color(self.lib.settings, name, colour)
        for made in changes["created"]:
            storage.set_tag_color(self.lib.settings, made["name"],
                                  made["colour"])

        dropped = {g.lower() for g in changes["deleted"]}
        station["tags"] = [t for t in changes["assigned"]
                           if t.lower() not in dropped]

        self.lib.save_stations()
        self.lib.save_settings()
        crashlog.write("[tags] %r now has %s"
                       % (storage.station_display_name(station),
                          station["tags"] or "no tags"))
        self.reload()
        self.companion.tip("tags", self.sidebar._tag_title)

    def _edit_tags_from_sidebar(self):
        """The pencil beside "Tags" on the left, and "Edit tags…" on the
        collapsed rail: the tag manager, every tag to add, rename,
        recolour or remove (task 87). It used to open the playing
        station's Tags… window (task 79); that stays on a station's
        right-click menu."""
        self.manage_tags()

    def manage_tags(self):
        """The tag manager (task 87). Changes apply to every station
        that has the tag, all at once on Save."""
        from tagsdialog import TagManagerDialog
        import crashlog
        holders = {}
        for s in self.lib.stations:
            for t in {t.lower() for t in (s.get("tags") or [])}:
                holders.setdefault(t, set()).add(s.get("id"))
        tags = []
        for n in storage.library_tags(self.lib.stations, self.lib.settings):
            ids = holders.get(n.lower(), set())
            tags.append(dict(name=n, colour=self.lib.tag_colour(n),
                             count=len(ids), ids=ids))
        crashlog.write("[tags] tag manager opened: %d tag(s)" % len(tags))
        self.companion.tip("tags", self.sidebar._tag_title)
        d = TagManagerDialog(tags, self)
        if not d.exec_():
            crashlog.write("[tags] tag manager cancelled")
            return
        changes = d.result_changes()
        crashlog.write(
            "[tags] tag manager save: renamed %s, new %s, deleted %s"
            % (changes["renamed"], changes["created"], changes["deleted"]))

        # The filter on the left follows a renamed tag, and lets go of a
        # removed one: a filter on a name no tag has any more would hide
        # every station with no pill left to clear it.
        states = self.sidebar.tag_states()
        storage.rename_tags(self.lib.stations, self.lib.settings,
                            changes["renamed"])
        for gone in changes["deleted"]:
            storage.remove_tag(self.lib.stations, gone)
        for name, colour in changes["colours"].items():
            storage.set_tag_color(self.lib.settings, name, colour)
        # The manager's list is the whole list now: a kept tag no station
        # has stays as a spare one (a new tag, or one whose stations all
        # dropped it), a removed one doesn't.
        used = {t.lower() for t in storage.all_tags(self.lib.stations)}
        storage.set_spare_tags(self.lib.settings,
                               [n for n in changes["tags"]
                                if n.lower() not in used])

        self.lib.save_stations()
        self.lib.save_settings()
        crashlog.write("[tags] tag manager: %d tag(s) now: %s"
                       % (len(changes["tags"]), changes["tags"]))
        self.reload()

        low_renamed = {o.lower(): n for o, n in changes["renamed"].items()}
        dropped = {g.lower() for g in changes["deleted"]}
        for name, state in states.items():
            new = low_renamed.get(name.lower())
            if name.lower() in dropped:
                self.sidebar.set_tag_state(name, "off")
            elif new is not None and new != name:
                self.sidebar.set_tag_state(name, "off")
                self.sidebar.set_tag_state(new, state)

    def _held_station(self, row):
        """The library's own dict for a row's station.

        A row's "_source" is meant to be that dict, but a row that went
        through the list model comes back as a deep copy (task 39). Edits
        made to a copy are lost on save, so look the station up by id;
        the row's own copy is only a fallback (a station not in the
        library, such as a directory preview)."""
        source = row.get("_source") or row
        sid = source.get("id")
        held = self.lib.station_by_id(sid) if sid is not None else None
        return held if held is not None else source

    def station_options(self, row, tab=None):
        """Your own StationOptionsDialog, not the redesigned stand-in.

        This dialog — ad override, ten-band EQ, art and readout sources, buffering
        — and none of it round-trips to the station record.
        """
        from dialogs import StationOptionsDialog
        station = self._held_station(row)
        d = StationOptionsDialog(
            self, station,
            # Only local custom stations can supply filler audio: a YouTube
            # one needs two round-trips, and the point is immediacy.
            custom_stations=[s for s in self.lib.stations
                             if storage.is_local_station(s)
                             and s.get("local_path")],
            eq_advanced=bool(self.lib.settings.get("eq_advanced")),
            # Real streams only — a custom station has no live URL to join
            # while an ad runs.
            other_stations=[s for s in self.lib.stations
                            if not storage.is_custom_station(s)
                            and s.get("url")],
            # "Choose frequency…" under Cover it with (build 64): every
            # saved frequency that can start at once — a normal radio
            # station or a music folder.
            cover_stations=[s for s in self.lib.stations
                            if self.playback._playable_cover(s)],
            # The talk-station switch, dev builds only (task 49).
            talk=self._talk_switch_state(station),
            # Its picture at the top, as Saved Frequencies shows it
            # (task 67).
            art=self._station_art(station),
        )
        if tab is not None:
            tabs = d.findChild(QTabWidget)
            if tabs is not None:
                tabs.setCurrentIndex(tab)
        # Clippy's first-time tip about the ten-band switch (task 197).
        self.companion.open_dialog(d)
        self.companion.tip("stationeq")
        if not d.exec_():
            return

        values = d.values()
        # eq_advanced is a global choice about which EQ is shown, not a
        # property of this station, so it goes to settings.
        self.lib.settings["eq_advanced"] = bool(values.pop("eq_advanced"))
        # Not a station field: it goes to the developer's talk list,
        # which ships (task 49). Saved after the rest, so a new name or
        # stream address is what the entry is filed under.
        talk = values.pop("talk_radio", None)
        talk_was = self._talk_switch_state(station)
        station.update(values)

        # And the library's own copy of this station.
        #
        # The dialog is handed row["_source"], which is *meant* to be the
        # very dict the library holds — but the log showed a station
        # saving its artwork and the same id reopening empty seconds
        # later, so for at least some rows it is not. Writing through the
        # id as well costs nothing and cannot be wrong.
        held = self.lib.station_by_id(station.get("id"))
        if held is not None and held is not station:
            held.update(values)

        self.lib.save_stations()
        self.lib.save_settings()
        if talk is not None and (talk != talk_was
                                 or storage.talk_entry(station)[1]):
            # Saved again when on, too: a renamed station or a new stream
            # address moves with it.
            storage.save_talk_entry(station, talk=talk)
            if talk != talk_was:
                import crashlog
                crashlog.write(
                    f"[talk] switch {'on' if talk else 'off'} for "
                    f"'{storage.station_display_name(station)}' (dev "
                    f"build, saved to {storage.dev_talk_path()})")

        # The thumbnail cache has to be told the answer changed.
        for view in (self.stations_page.list, self.discover_page.list,
                     getattr(self.discover_page, "featured_list", None)):
            logos = getattr(view, "logos", None)
            if logos is not None:
                logos.forget(station.get("id"))

        if self.playback.current is station:
            self.apply_live(station)
            # The talk switch may have changed what the panel is for.
            self.panel.set_artist_enabled(self.artist_lookup_on())
        self.reload()
        self.notify(
            f"Options saved for {storage.station_display_name(station)}.")

    def _station_art(self, station):
        """The station's picture as the Saved Frequencies list draws it
        (its letter tile when it has none), or None."""
        view = getattr(getattr(self, "stations_page", None), "list", None)
        cache = (getattr(view, "grid_logos", None)
                 or getattr(view, "logos", None))
        if cache is None or not station or not station.get("id"):
            return None
        try:
            return cache.get(self.lib.row(station))
        except Exception as exc:                # noqa: BLE001
            import crashlog
            crashlog.write(f"[station] no picture for Station settings: "
                           f"{exc}")
            return None

    def _talk_switch_state(self, station):
        """True/False for the switch in a dev build, None in a user build
        (no switch shown)."""
        import devmode
        if not devmode.is_dev():
            return None
        return storage.is_talk_station(station)

    def apply_live(self, station):
        """Push a station's sound settings to the running stream, so a
        change takes effect without reconnecting."""
        p = self.playback.player
        p.set_gain_db(float(station.get("gain_db") or 0.0))
        p.set_radio_quality(bool(station.get("radio_quality")))
        self.playback.apply_eq(station)

        # The picture too, not only the sound.
        #
        # Choosing artwork and pressing Save changed the file on disk and
        # the row in the list, and left the bar showing whatever was
        # there before — the one place you were looking at when you chose
        # it.
        path = (station.get("custom_art_path") or "").strip()
        if path and os.path.exists(path):
            from PyQt5.QtGui import QPixmapCache
            QPixmapCache.remove(path)
            self.set_artwork_data(path=path)
        if self.feeds is not None:
            # The identify interval may have changed, or been switched off.
            self.feeds.station_changed(station)

    def on_desktop(self, integration):
        """MPRIS and the tray want telling when the station or song
        changes."""
        self.desktop = integration
        # The button reflects a timer that may already be running — a
        # sleep started from the tray is the same timer.
        self.player.set_sleep_active(
            bool(getattr(integration, "sleep_active", False)))

    def closeEvent(self, event):
        """Closing the window: hide it in the tray, or quit (task 82).

        Settings > System's "Closing the window minimises to the tray"
        hides it — but only while a tray icon is showing, or there would
        be nothing on screen to bring it back with (or to quit from).
        The tray icon's Show (or a click on it) brings it back, its Quit
        ends the app. When the desktop is logging out, the window always
        closes: a window that refuses to close stops the logout.
        """
        from PyQt5.QtWidgets import QApplication
        tray = getattr(self.desktop, "tray", None)
        wants = bool(self.lib.settings.get("minimize_to_tray", False))
        app = QApplication.instance()
        ending = bool(app is not None and app.isSavingSession())
        if wants and not ending and tray is not None and tray.isVisible():
            event.ignore()
            self.hide()
            self._tray_log("closing the window hid it in the tray "
                           "(the tray icon's Show brings it back, Quit ends "
                           "the app)")
            if not getattr(self, "_told_about_tray", False):
                self._told_about_tray = True
                if self.desktop is not None:
                    self.desktop.toast(
                        "Frequencies",
                        "Still running in the tray. Click its icon to bring "
                        "the window back, or right-click it to quit.")
            return
        why = ("the desktop is logging out" if ending
               else "the tray switch is off" if not wants
               else "no tray icon is showing - Settings > System: Show a tray "
                    "icon")
        self._tray_log(f"closing the window quits ({why})")
        super().closeEvent(event)

    @staticmethod
    def _tray_log(line):
        try:
            import crashlog
            crashlog.write(f"[tray] {line}")
        except Exception:                       # noqa: BLE001
            pass

    def attach_feeds(self, feeds_obj):
        """Identification, lyrics and artwork. Set after construction so the
        station list can take its logo fetcher from the same object."""
        self.feeds = feeds_obj
        self.stations_page.list.logos.set_fetcher(feeds_obj.logo_fetcher)
        self.discover_page.list.logos.set_fetcher(feeds_obj.logo_fetcher)
        self.discover_page.featured_list.grid_logos.set_fetcher(
            feeds_obj.logo_fetcher)
        self.stations_page.list.viewport().update()

    # -- callbacks from Player ---------------------------------------------
    #
    # These four are what you hand Player's constructor. Each one only
    # emits; the _handle_* methods below do the work, on the GUI thread.

    def on_state(self, state, message):
        self.bus.state.emit(state, message or "")

    def on_metadata(self, title):
        self.bus.metadata.emit(title or "")

    def on_tags(self, tags):
        self.bus.tags.emit(tags)

    def on_connected(self, url, seconds):
        self.bus.connected.emit(url or "", float(seconds or 0))

    def on_stalled(self, url):
        self.bus.stalled.emit(url or "")

    # -- and the GUI-thread half -------------------------------------------

    def _handle_state(self, state, message):
        from player import PlayerState
        if state == PlayerState.NO_AUDIO:
            # The stream was fine and the sound card wasn't — worth saying
            # plainly, since it looks identical to a dead station otherwise.
            self.notify(message or "The audio device is unavailable.")
        elif state == PlayerState.ERROR:
            # A library track refused because the sign-in ran out.
            #
            # Renewed and tried again before anything else — including
            # the quality fallback below, which would otherwise drop a
            # 320 station to 128 for a reason that had nothing to do
            # with the rate.
            if self._ibroadcast_resign(message):
                return
            # A library station asked for a quality it cannot have.
            #
            # iBroadcast serves each account only the rates its plan
            # allows, and nothing here knows which plan that is — so a
            # station set to 320, or to the original file, on an account
            # that only gets 128 was refused, and looked exactly like a
            # dead station. The first refusal drops it to 128 and tries
            # the same track again; the choice is remembered so the rest
            # of the queue does not fail the same way.
            if self._ibroadcast_fall_back():
                return
            self.notify(message or "The stream failed.")
            # A failure to connect counts against the station.
            #
            # Only stalls were being recorded — a stream that answered
            # and then went quiet. A stream that refuses outright, which
            # is the ordinary way a dead station behaves, was reported to
            # you and forgotten, so it never accumulated failures and was
            # never marked down however many times you tried it.
            station = self.playback.current or {}
            if station.get("id"):
                self._log_stream_failure(station.get("url", ""),
                                         message or "error")
                # Down on the first failure you saw yourself.
                #
                # Three strikes suits the background checker, where one
                # network blip should not condemn a station. But pressing
                # play and hearing nothing is evidence enough — waiting
                # for two more tries meant tuning to a dead station three
                # times before the list admitted it.
                storage.mark_failed(self.lib.health, station["id"],
                                    reason=message or "could not connect",
                                    down_after=1)
                storage.save_health(self.lib.health)
                self.reload()
        if state == PlayerState.PLAYING:
            # Re-assert the slider once audio is flowing.
            #
            # set_volume is ignored while a station is still connecting, so
            # a change made during the static was remembered but never
            # applied — the bar showed one level and the stream played at
            # whichever one it had before.
            self.playback.player.set_volume(int(self.player.vol.value()))
        # Connecting: the station's own name with an ellipsis, rather than
        # whatever the stream happens to be announcing.
        # Anything but playing, on an iBroadcast station, is worth a line.
        if state in (PlayerState.ERROR, PlayerState.STOPPED) and \
                (self.playback.current or {}).get("ibroadcast_urls"):
            self._log_stream_failure(
                (self.playback.current or {}).get("url", ""),
                f"{getattr(state, 'name', state)}: {message or 'no message'}")

        was_connecting = getattr(self, "_connecting", False)
        self._connecting = state == PlayerState.CONNECTING
        if self._connecting:
            name = storage.station_display_name(self.playback.current or {})
            self.player.set_now_playing(f"{name}…", "", "Connecting…")
        if state == PlayerState.PLAYING and (
                self.playback.current or {}).get("ibroadcast_titles"):
            # Say it again now the connecting line has gone.
            #
            # The name was set while the station was still connecting, and
            # the connecting readout — the station's own name with an
            # ellipsis — replaced it. The track outlives the connection.
            self.playback._announce_ibroadcast(self.playback.current)

        elif was_connecting and state == PlayerState.PLAYING:
            # Now it is worth showing whatever arrived while we waited.
            held, self._pending_meta = getattr(self, "_pending_meta", ""), ""
            name = storage.station_display_name(self.playback.current or {})
            # Through the station's preference, like every other readout.
            #
            # This path chose for itself — identified song, then whatever
            # the stream sent — and never asked. So a station set to
            # "Custom text" showed its custom text only until it finished
            # connecting, and then this replaced it.
            wanted = self._readout_choice(shazam=self._song, stream=held)
            if wanted:
                self.player.set_now_playing(wanted[0], wanted[1], name)
                if wanted[0] == held:
                    self.panel.set_track(held, "")
            else:
                self.player.set_now_playing(name, "", name)

        playing = state in (PlayerState.PLAYING, PlayerState.CONNECTING)
        sid = (self.playback.current or {}).get("id")
        self.stations_page.list.set_playing(sid, playing)
        self.player.set_playing(playing)
        # The desktop's media controls say the same as the button (they
        # kept saying "Playing" after a pause, task 88).
        if self.desktop is not None:
            self.desktop.push_mpris()
        if message:
            self.notify(message)

    def _sound_stopped(self, why):
        """The station's sound stopped with nobody pressing pause.

        Task 88: on a Twitch station, opening the channel in the browser
        stopped the sound and the play button went on saying it played.
        So: pause for real, exactly as the button does — the button,
        the list, the tray and the desktop's media controls all say
        paused, and play tunes in again. Player.tick decides when (mpv
        paused by something else, or a Twitch stream silent for 30 s)."""
        if not self.player._playing:
            return                      # already shows paused
        station = self.playback.current or {}
        name = storage.station_display_name(station)
        try:
            import crashlog
            crashlog.write(f"[playstate] '{name}': paused because {why}; "
                           f"the play button shows paused, play tunes in "
                           f"again")
        except Exception:                       # noqa: BLE001
            pass
        self._on_play_toggled(False)
        self.notify(f"{name}: the sound stopped, so it is paused. "
                    f"Press play to tune in again.", transient=True)

    def _link_opened(self, url):
        """A link went to the browser (any link the app shows).

        While a station plays, its sound is watched for a while and
        logged ([sound] lines), and a Twitch station's playlist is read
        again — so a log shows what stops when the channel opens in the
        browser (task 88): the stream, the sound output, or a pause asked
        for by another program ([mpris] lines)."""
        if not self.player._playing or not self.playback.current:
            return
        from urllib.parse import urlsplit
        host = (urlsplit(url).hostname or "a link").replace("www.", "")
        why = f"opening {host} in the browser"
        self.playback.player.watch_sound(why)
        self.playback.watch_twitch_playlist(why)

    def _readout_choice(self, shazam=None, stream=None):
        """(text, artist) for the song line, per the station's preference.

        Returns the artist as well as the text. It used to return a title
        alone, and every caller passed an empty artist with it — so when
        the identified song won, which in Automatic it always does, the
        artist was blanked on each stream-metadata refresh, a few seconds
        after it first appeared. The biography, looked up separately, went
        on naming them, which is how the two came to disagree.

        `shazam` is (title, artist); `stream` is the stream's own text.
        """
        station = self.playback.current or {}
        mode = station.get("readout_source", "auto")
        # No custom text of its own: the station's name (task 100).
        custom = ((station.get("custom_readout") or "").strip()
                  or storage.station_display_name(station).strip())
        song = shazam if (shazam and shazam[0]) else None
        found = (song[0], song[1] or "") if song else None
        from_stream = (stream, "") if stream else None
        written = (custom, "") if custom else None

        if mode == "custom":
            order = (written,)
        elif mode == "shazam":
            order = (found,)
        elif mode == "stream":
            order = (from_stream,)
        elif mode == "auto_stream":
            order = (found, from_stream, written)
        else:
            order = (found, written, from_stream)
        for choice in order:
            if choice:
                return choice
        return None

    def _handle_metadata(self, title):
        """ICY title. Only used when nothing better has arrived, since the
        readout order is Shazam, then custom, then stream.

        Held back until audio is flowing. What a stream sends in its first
        second is usually not a song — a mount point, an ad slug, a line of
        the station's own branding — and showing it makes the first thing
        you see after pressing play look like a mistake.
        """
        # A YouTube station never takes its name from the stream.
        #
        # A googlevideo URL carries its own metadata — mime types, codec
        # names, whatever the container happens to say — and that is
        # where "webm&" came from. The video's real title is already
        # known by the time it plays, so anything the stream offers is
        # noise by comparison.
        station = self.playback.current or {}
        if storage.is_youtube_station(station) or station.get("playlist_url"):
            return

        if self._connecting:
            self._pending_meta = title or ""
            return

        # The station's own preference decides whether this is used at
        # all, and what beats it.
        wanted = self._readout_choice(shazam=self._song, stream=title)
        mode = station.get("readout_source", "auto")
        if mode in ("custom", "shazam") or (wanted and wanted[0] != title):
            if wanted:
                self.player.set_now_playing(
                    wanted[0], wanted[1],
                    storage.station_display_name(station))
            return

        if not self._song and title:
            self.player.set_now_playing(
                title, "", storage.station_display_name(
                    self.playback.current or {}))
            self.panel.set_track(title, "")
            # The stream's own "artist - title" is the song shown, so
            # brat mode follows it until something better is known.
            self.brat.stream_title(title)

    def _handle_tags(self, tags):
        """A file's own tags: a music folder's album, for brat mode."""
        if self.playback.current is None:
            return
        self.brat.tags(tags)

    def _brat_changed(self, on):
        """brat mode turned on or off (task 48). The app font and window
        titles are already switched (brat.apply); this redraws the text
        that was painted into pictures and kept: the station tiles'
        letters, the empty-cover placeholder, and History's tiles."""
        for view in (self.stations_page.list, self.discover_page.list,
                     self.discover_page.featured_list):
            for name in ("logos", "grid_logos"):
                cache = getattr(view, name, None)
                if cache is not None and hasattr(cache, "redraw_monograms"):
                    cache.redraw_monograms()
            view.viewport().update()
        # The empty-cover placeholder is the station's letters too.
        art = getattr(self.player, "art", None)
        if art is not None and art._showing_placeholder():
            art.set_art(QPixmap())
            if getattr(self, "_art_expanded", False):
                self._redraw_dock_art()
        self.refresh_history()

    def on_track_finished(self):
        """Audio ran out — reported on mpv's own thread.

        Only emits, like the four callbacks above. Handling it here
        directly reloaded the list and changed the player bar from mpv's
        thread, which Qt does not allow: that is the kind of crash that
        leaves nothing but Qt frames in the trace.
        """
        self.bus.track_finished.emit()

    def _handle_track_finished(self):
        """Audio ran out. What that means depends on the station.

        Three different things end here, and treating them the same was
        why a Twitch stream going offline left silence:

          * a playlist has more in it — play the next thing
          * a live channel that ends has ended; there is nothing more to
            play, so move to another station rather than sit in silence
          * a plain stream running out is a stream that dropped
        """
        station = self.playback.current or {}

        if (storage.is_youtube_station(station)
                or station.get("ibroadcast_urls")
                or station.get("kind") in ("ibroadcast",)):
            self.playback.play_another(station)
            return

        if station.get("kind") == "twitch" or station.get("twitch_channel"):
            # The broadcast is over. Mark it down so the list says so,
            # and move on.
            storage.mark_failed(self.lib.health, station.get("id"),
                                "stream ended", down_after=1)
            storage.save_health(self.lib.health)
            self.reload()
            self.notify(f"{storage.station_display_name(station)} ended.",
                        transient=True)
            self.playback.next()
            return

        if storage.is_custom_station(station):
            # A folder that has run out of files, or a single file that
            # finished. Another track if there is one, another station if
            # there is not.
            if not self.playback.play_another(station):
                self.playback.next()
            return

        from player import PlayerState
        self._handle_state(PlayerState.STOPPED, "")

    def _handle_connected(self, url, seconds):
        # Connecting to it is the best evidence there is that it is up.
        #
        # Only the background monitor cleared this, so a station marked
        # down stayed marked down however many times you played it —
        # which is exactly backwards, since you have better evidence than
        # the monitor does.
        current = self.playback.current
        if current is not None and current.get("id"):
            # Connected, so its renew-and-retry is available again.
            self._ib_resigned_ids.discard(current["id"])
            entry = storage.health_for(self.lib.health, current["id"])
            if entry.get("down") or entry.get("fail_count"):
                storage.mark_ok(self.lib.health, current["id"])
                storage.save_health(self.lib.health)
                self.reload()

        station = self.playback.current
        if station and seconds > (station.get("connect_worst") or 0):
            station["connect_worst"] = seconds
            self.lib.save_stations()

    def _log_stream_failure(self, url, why):
        """What mpv could not do with a URL, in the log.

        A stream that connects and delivers nothing looks identical to one
        that was never reachable. The URL without its query — the query
        holds the signature — plus mpv's own reason is enough to tell
        them apart.
        """
        try:
            import crashlog
            crashlog.write(f"[stream] {why}: {(url or '').split('?')[0]}")
        except Exception:                       # noqa: BLE001
            pass

    def _handle_stalled(self, url):
        # Waiting on a renewed iBroadcast sign-in: the retry is coming,
        # and skipping to another station now would abandon it.
        if self._ib_resign_for is not None:
            return
        # A stream that never starts is reported twice — as an error and
        # as a stall. If the error already started a retry at a lower
        # quality, this one must not skip away from it.
        if self._ibroadcast_fall_back():
            return
        if getattr(self, "_ib_retry_at", 0.0) > time.monotonic() - 3.0:
            return
        self._log_stream_failure(url, "stalled")
        storage.mark_failed(self.lib.health,
                            (self.playback.current or {}).get("id"),
                            reason="no audio", down_after=1)
        storage.save_health(self.lib.health)
        if (self.lib.settings.get("auto_skip_broken", True)
                and storage.skipped_when_down(self.playback.current)):
            self.playback.next()

    # -- callbacks from Shazam / lyrics -------------------------------------

    def _show_station_art(self, url):
        """Artwork a station supplied itself, rather than a song's cover."""
        if self.feeds is not None and url:
            try:
                self.feeds.want_station_art(url)
            except Exception:                   # noqa: BLE001
                pass

    def _ibroadcast_track(self, title, artist, album="", cover=""):
        """A track the station named itself, rather than one identified.

        The readout is set the same way, and the artwork lookup is asked
        for directly — nothing else will ask on this station's behalf,
        because no identification ever happens for a library it already
        knows the answer for. `cover` is the sleeve iBroadcast keeps for
        the track, when the station has it saved (task 23).
        """
        self.on_song(title, artist, album=album, source="station")
        if self.feeds is not None and (artist or album or title):
            try:
                self.feeds.want_artwork(artist, title, album, cover)
            except Exception:                   # noqa: BLE001
                pass
        if not cover:
            self._ib_backfill_art(self.playback.current)

    def _ib_backfill_art(self, station):
        """Fill in the covers of a station added before they were saved.

        Stations added up to build 48 kept each track's name but not its
        cover. The library says which cover goes with which file, so it
        is read once, off the GUI thread, and the covers are saved with
        the station. Once per station: tracks with no cover in your
        library stay without one, and searching for them is feeds' job.
        """
        if not station or not station.get("ibroadcast_titles"):
            return
        if station.get("ibroadcast_art_checked"):
            return
        sid = station.get("id")
        asked = getattr(self, "_ib_art_asked", set())
        self._ib_art_asked = asked
        if sid in asked:
            return
        asked.add(sid)
        import ibroadcast as ib

        def read():
            token = ib.current_token()
            if not token:
                raise ib.IBroadcastError("not signed in")
            info = ib.status(token)
            lib = ib.library(token).get("library") or {}
            return ib.artwork_by_file(info.get("settings") or {}, lib)

        self._look_up(read, lambda found, error: self._ib_art_read(
            sid, found, error))

    def _ib_art_read(self, sid, found, error):
        import ibroadcast as ib
        if error is not None:
            self._ib_log(f"covers: couldn't read the library: {error}")
            return
        station = self.lib.station_by_id(sid)
        if station is None:
            return
        found = found or {}
        titles = station.get("ibroadcast_titles") or []
        urls = station.get("ibroadcast_urls") or []
        filled = 0
        for index, url in enumerate(urls[:len(titles)]):
            row = (list(titles[index]) + ["", "", "", ""])[:4]
            cover = found.get(ib.file_id_of(url), "")
            if cover:
                filled += 1
            row[3] = cover or row[3]
            titles[index] = row
        station["ibroadcast_titles"] = titles
        station["ibroadcast_art_checked"] = True
        self.lib.save_stations()
        self._ib_log(f"covers: {filled} of {len(titles)} tracks have one")
        # The track playing now gets its cover straight away.
        current = self.playback.current
        if current is not None and current.get("id") == sid:
            if current is not station:
                current["ibroadcast_titles"] = titles
                current["ibroadcast_art_checked"] = True
            self.playback._announce_ibroadcast(current)

    def on_song(self, title, artist, art=None, offset=0.0, album=None,
                source="title"):
        # When this song was seen to start, for the lyric clock.
        #
        # A live stream has no position of its own — mpv only knows how
        # long it has been connected, which says nothing about where the
        # song began. So the clock runs from the moment the song was
        # identified. `offset` is how far in it already was, when the
        # identification can say; zero drifts by at most one
        # identification interval, which is the honest worst case.
        # Only when the song actually changes.
        #
        # A station re-identifies what is playing every half minute or so,
        # and the readout is refreshed on stream metadata besides — so
        # this ran repeatedly during one song and each time it moved the
        # clock back to zero. The highlight lit the first line, walked a
        # few, and jumped home again, over and over. The same song
        # carrying on is not a new start.
        if (title, artist) != getattr(self, "_song", None):
            self._song_started = time.monotonic()
            self._song_offset = float(offset or 0.0)
            # A hand resync belongs to the song it was made on.
            self._lyric_nudge = 0.0

        # A new song is the one thing that clears the star.
        #
        # Judged on what was identified, not on what is written in the
        # bar — the two are different whenever a readout preference or a
        # stream's own metadata is in play.
        if (title, artist) != self._song:
            self._show_star_for(title, artist)

        self.player.shazam_pulse()
        self._song = (title, artist)
        # brat mode follows the song shown (task 48). `album` when the
        # source names one; `source` says how sure it is (brat.RANK).
        self.brat.song(artist, title, album, source)
        station = storage.station_display_name(self.playback.current or {})

        # Custom text outranks an identification when the station says so.
        wanted = self._readout_choice(shazam=(title, artist), stream=None)
        if wanted:
            self.player.set_now_playing(wanted[0], wanted[1], station)
        else:
            self.player.set_now_playing(title, artist, station)
        self.panel.set_track(title, artist)
        self._update_title()
        if self._classic is not None:
            self._classic.refresh()
        if art is not None:
            pm = art
            if not isinstance(art, QPixmap):
                pm = QPixmap()
                pm.loadFromData(art)
            self.set_artwork(pm)
        self.refresh_history()
        if self.desktop is not None:
            self.desktop.push_mpris()

    def on_sources(self, lyrics=None, artist=None):
        """Credit lines under each panel column."""
        if lyrics is not None:
            self._lyrics_source = lyrics
        if artist is not None:
            self._artist_source = artist
        self.panel.set_sources(self._lyrics_source, self._artist_source)

    def on_busy(self, lyrics=None, artist=None):
        self.panel.set_busy(lyrics, artist)

    def on_lyrics(self, lines, message=None):
        self._lyrics = list(lines or [])
        self.panel.set_busy(lyrics=False)
        if self._classic is not None:
            self._classic.set_lyrics(lines, message)
        self.now_playing.set_lyrics(lines, message)
        self.panel.set_lyrics(lines, message)
        self.panel.set_available(self._lyrics, self._bio)
        if self._lyrics:
            # Clippy's tip 13, the first time lyrics are found (task 155).
            self.companion.tip("lyrics")
        self._refresh_fullscreen_state()

    def _show_star_for(self, title, artist):
        """Set the star to whether this song is actually a favourite.

        It used to be cleared on a song change — but only when there had
        been a previous song. After changing station or pausing there was
        none, so the next song kept the star lit from before. Asking the
        history instead answers every case: a new song is unstarred, and a
        song you starred last week is starred again when it comes round.
        """
        key = storage.track_key(artist, title)
        starred = any(storage.is_favorite(e) and storage.entry_key(e) == key
                      for e in self.lib.history)
        button = self.player.fav_btn
        if button.isChecked() == starred:
            return
        button.blockSignals(True)
        button.setChecked(starred)
        button.blockSignals(False)
        self.player._on_fav(starred)

    def _pin_artist_key(self):
        """Tell both artist views which name corrections are filed under."""
        key = getattr(self, "_artist_key", "")
        for view in (getattr(self.panel, "artist", None),
                     getattr(getattr(self.panel, "artist", None), "header", None),
                     getattr(self.panel, "header", None)):
            if view is not None:
                view._edit_key = key

    def _artist_corrected(self, name):
        """Put a saved correction on screen at once.

        Applied here rather than through the lookup: the text was typed
        by hand, so there is nothing to wait for, and the lookup is not
        always running — on a build without the identification pieces it
        does not exist at all, and the correction would silently do
        nothing.

        The lookup is still nudged afterwards, for the fields the
        correction left blank.
        """
        name = (name or "").strip()
        if not name:
            return
        written = storage.artist_override(name) or {}
        shown = (written.get("name") or name).strip()
        self._artist_key = name
        photo = (written.get("photo") or "").strip()

        if written.get("bio") or photo or written.get("genres"):
            if photo and os.path.exists(photo):
                from PyQt5.QtGui import QPixmapCache
                QPixmapCache.remove(photo)
                self.panel.set_photo_path(photo)
            self.on_artist(shown, written.get("genres", "") or "",
                           written.get("bio", "") or "",
                           photo if photo and os.path.exists(photo) else None)
            self._pin_artist_key()

            # The credit under the picture, and where it points.
            credit = (written.get("photo_source") or "").strip()
            link = (written.get("photo_url") or "").strip()
            if credit:
                if link:
                    credit = (f'<a href="{link}" style="color:inherit">'
                              f'{credit}</a>')
                self.on_sources(artist=credit)

        if self.feeds is not None:
            try:
                self.feeds.refresh_artist(name)
            except Exception:                   # noqa: BLE001
                pass

    def _resync_lyrics(self, start_ms):
        """Put the clock where the listener says the song is.

        A live stream has no position of its own, so the clock counts
        from the moment the song was identified — and if it was already
        half over by then, everything runs early by that much. Nothing in
        the app can tell. Double-clicking the line being sung says where
        we actually are, and the clock is moved to match.
        """
        try:
            where = float(start_ms or 0) / 1000.0
        except (TypeError, ValueError):
            return
        # A song that knows its own position (iBroadcast, a music folder,
        # YouTube) took its clock from mpv on the very next tick, so the
        # dead-reckoning clock set here was thrown away a quarter of a
        # second later and the double-click did nothing (task 30). Those
        # keep their real position and carry the difference as a nudge.
        real = self._real_position()
        if real is not None:
            self._lyric_nudge = where - real
        else:
            self._song_started = time.monotonic()
            self._song_offset = max(0.0, where)
        # Resynced by hand, so the panel follows again straight away
        # rather than staying where it was scrolled to.
        for view in (self.panel.lyrics.view, self.now_playing.lyrics):
            if hasattr(view, "_pin"):
                view._pin(False)
        if self._lyrics_paused():
            # Paused: the clock is held, so show the chosen line directly.
            self.on_position(int(where * 1000))
        else:
            self._tick_lyrics()
        self.notify("Lyrics resynced to here.", transient=True)

    def _lyrics_paused(self):
        """Paused or stopped, by the play button the listener sees.

        Not playback.current: stopping leaves the station in place (it is
        what pressing play starts again).
        """
        return not getattr(self.player, "_playing", True)

    def _real_position(self):
        """mpv's own position for a song that has one, else None.

        A live stream has none worth using (only how long it has been
        connected), and nothing does while stopped.
        """
        station = self.playback.current or {}
        live = not (station.get("ibroadcast_urls")
                    or station.get("playlist_url")
                    or station.get("local_path"))
        if live or self._lyrics_paused():
            return None
        try:
            return self.playback.player.position_seconds()
        except Exception:                       # noqa: BLE001
            return None

    def _tick_lyrics(self):
        """Where we are in the song, by whichever means there is.

        A file or a playlist entry knows its own position and that is the
        end of it. A live stream does not: it only knows how long it has
        been connected, which has nothing to do with where the song
        started. For those, the clock runs from the moment the song was
        identified — dead reckoning, and the only honest option.
        """
        # Paused (for radio, stopped): the words hold still.
        #
        # The dead-reckoning clock kept running with nothing playing, so
        # the lyrics went on scrolling through a pause — and a folder or
        # iBroadcast song, whose real position vanished on stop, jumped
        # to a guessed one. Nothing is being sung; nothing should move.
        if self._lyrics_paused():
            return

        seconds = self._real_position()
        if seconds is not None:
            seconds += getattr(self, "_lyric_nudge", 0.0)
        else:
            started = getattr(self, "_song_started", 0.0)
            if not started:
                return
            seconds = (time.monotonic() - started
                       + getattr(self, "_song_offset", 0.0))

        self.on_position(int(max(0.0, seconds) * 1000))

    def on_position(self, ms):
        if self._classic is not None:
            self._classic.set_position(ms)
        self.now_playing.set_position(ms)
        self.panel.set_position(ms)

    def has_fullscreen_content(self):
        """Lyrics or a biography. Without either, full screen is a large
        empty rectangle with the artwork already visible behind it."""
        return bool(self._lyrics) or bool(self._bio)

    def toggle_now_playing(self):
        """Open or close full screen, whatever there is to show in it.

        It used to refuse when there were no lyrics or artist information.
        But full screen is the artwork, the song and the moving background
        too, and wanting only that is a good reason to open it — so it
        always opens, and fills in whatever arrives afterwards.
        """
        if self.in_now_playing():
            self.close_now_playing()
            return
        self.open_now_playing()

    def lyrics_on(self):
        """Whether lyrics are wanted at all."""
        if not self.lib.settings.get("lyrics_on", True):
            return False
        station = self.playback.current or {}
        return bool(station.get("lyrics_enabled", True))

    def artist_lookup_on(self):
        """Whether biographies are wanted for the station playing.

        A per-station override wins over the global setting, so a talk
        station can be switched off on its own — and a station the dev has
        set up with an override still shows what it was given.
        """
        # The global switch wins outright: off means off everywhere, not
        # off unless a station disagrees.
        if not self.lib.settings.get("artist_on", True):
            return False
        station = self.playback.current or {}
        # A talk station shows its own info whatever its lookups say
        # (task 49): that switch is about songs, and it has none.
        if station and storage.is_talk_station(station):
            return True
        if "artist_lookup" in station:
            return bool(station.get("artist_lookup"))
        return bool(self.lib.settings.get("artist_lookup", True))

    # -- Twitch chat (task 78) ---------------------------------------------

    _chat_reader = None
    _chat_channel = ""
    _chat_seq = 0
    # The pop-out's own connection (task 107): only while it is on another
    # channel than the side panel's. Its seq is negative, so it never
    # clashes with `_chat_seq`; a reader handed over from the side panel
    # keeps the (positive) seq it was made with.
    _pop_channel = ""
    _pop_reader = None
    _pop_cancel = None
    _pop_seq = 0
    _pop_n = 0
    _mode_before_chat = None

    def _pop_follows(self):
        """The pop-out is open and shows the side panel's chat."""
        window = self._chat_window
        return (window is not None and window.isVisible()
                and self._pop_reader is None)

    def _main_views(self):
        """The views fed by the side panel's reader."""
        views = [self.panel.lyrics.chat]
        if self._pop_follows():
            views.append(self._chat_window.chat)
        return views

    def _views_for(self, seq):
        """The views a reader's answers go to (by its seq)."""
        if seq == self._chat_seq:
            return self._main_views()
        window = self._chat_window
        if seq and seq == self._pop_seq and window is not None \
                and window.isVisible():
            return [window.chat]
        return []

    def _channel_for(self, seq):
        if seq == self._chat_seq:
            return self._chat_channel
        return self._pop_channel if seq and seq == self._pop_seq else ""

    def _reader_for(self, seq):
        if seq == self._chat_seq:
            return self._chat_reader
        return self._pop_reader if seq and seq == self._pop_seq else None

    def _twitch_chat_for(self, station, keep=False):
        """A Twitch station's chat in the lyrics' place, read only.

        Started when a Twitch station starts, kept through anything that
        re-announces the same channel, stopped when anything else plays
        or playback stops (build 77).
        """
        channel = ""
        if (station or {}).get("kind") == "twitch":
            channel = (station.get("twitch_channel") or "").strip().lower()
        if channel and channel == self._chat_channel \
                and self._chat_reader is not None and not keep:
            return
        self._release_to_pop(keep)
        self._stop_chat()
        window = self._chat_window
        following = self._pop_follows()
        if not channel:
            self.panel.set_chat(False)
            if self._mode_before_chat is not None:      # task 109
                back = self._mode_before_chat
                if back != self.panel.LYRICS and \
                        not self.panel._artist_enabled:
                    back = self.panel.LYRICS
                self.panel.set_mode(back)
                self._mode_before_chat = None
            for view in self._main_views():
                view.set_writer("")
                view.set_live(False)
            if following:
                window.set_channel("")
                self._pop_channel = ""
                window.chat.clear()
                window.chat.set_status(self.NO_CHAT)
            return
        import threading
        import crashlog
        self._chat_seq += 1
        self._chat_channel = channel
        self._chat_cancel = threading.Event()
        if not keep:                    # a new sign-in keeps the lines
            self.panel.lyrics.chat.clear()
        self.panel.set_chat(True)
        if not keep:
            # A Twitch station opens the panel on Chat (task 109); the
            # tab it was on comes back at the next station that isn't
            # Twitch. A tab picked by hand holds while you stay: this
            # runs only when the channel changes.
            if self._mode_before_chat is None:
                self._mode_before_chat = self.panel.mode()
            self.panel.set_mode(self.panel.LYRICS)
            self.companion.tip("chat", self.panel)
        if window is not None and window.isVisible():
            if self._pop_reader is not None \
                    and self._pop_channel == channel:
                self._stop_pop_reader()     # the side panel takes it over
                following = True
            elif following and not self._pop_channel:
                self._pop_channel = channel  # an empty pop-out adopts it
            if following:
                window.set_channel(self._pop_channel)
                if not keep:
                    window.chat.clear()
        import twitchauth
        saved = twitchauth.signed_in_as()
        for view in self._main_views():
            view.set_writer("", saved)  # you can type once joined
            view.set_live(True, channel)
        self._chat_reader = self._new_reader(self._chat_seq, channel,
                                             self._chat_cancel)
        crashlog.write(f"[chat] '{channel}': chat shown where the lyrics "
                       f"go (Twitch station)")
        self._chat_reader.start()

    def _new_reader(self, seq, channel, cancel):
        import twitchchat
        reader = twitchchat.ChatReader(seq, channel, cancel)
        reader.messages.connect(self._chat_messages)
        reader.removed.connect(self._chat_removed)
        reader.status.connect(self._chat_status)
        reader.room.connect(self._chat_room)
        reader.auth.connect(self._chat_auth)
        reader.note.connect(self._chat_note)
        reader.me_changed.connect(self._chat_me)
        return reader

    def _release_to_pop(self, keep):
        """The side panel is leaving its channel while the pop-out shows
        it: the pop-out keeps that channel (task 107), and keeps this
        very connection too, so no lines are missed."""
        if keep or not self._pop_follows() or not self._chat_channel \
                or self._pop_channel != self._chat_channel \
                or self._chat_reader is None:
            return
        import crashlog
        self._pop_reader, self._pop_cancel = (self._chat_reader,
                                              self._chat_cancel)
        self._pop_seq = self._chat_seq
        self._chat_reader = self._chat_cancel = None
        crashlog.write(f"[chat] pop-out stays on '{self._pop_channel}' "
                       f"while the side panel moves on")

    def _start_pop_reader(self, channel, keep=False):
        """The pop-out's own connection to `channel` (task 107)."""
        import threading
        import crashlog
        import twitchauth
        self._stop_pop_reader()
        window = self._chat_window
        self._pop_n += 1
        self._pop_seq = -self._pop_n
        self._pop_channel = channel
        self._pop_cancel = threading.Event()
        if window is not None:
            window.set_channel(channel)
            if not keep:
                window.chat.clear()
            window.chat.set_writer("", twitchauth.signed_in_as())
            window.chat.set_live(True, channel)
        self._pop_reader = self._new_reader(self._pop_seq, channel,
                                            self._pop_cancel)
        crashlog.write(f"[chat] pop-out has its own connection to "
                       f"'{channel}'")
        self._pop_reader.start()

    def _stop_pop_reader(self):
        cancel = self._pop_cancel
        if cancel is not None:
            cancel.set()
        channel = self._pop_channel
        had = self._pop_reader is not None
        self._pop_cancel = self._pop_reader = None
        self._pop_seq = 0
        if had and channel and channel != self._chat_channel:
            import chatextras
            chatextras.shared().forget_channel(channel)

    def _stop_chat(self):
        cancel = getattr(self, "_chat_cancel", None)
        if cancel is not None:
            cancel.set()
        if self._chat_channel:
            import chatextras
            import crashlog
            extras = chatextras.shared()
            crashlog.write(f"[chat] '{self._chat_channel}': extras - "
                           f"{extras.summary(self._chat_channel)}")
            if self._chat_channel != self._pop_channel:
                extras.forget_channel(self._chat_channel)
            extras.drop_pending_pronouns()
        self._chat_cancel = None
        self._chat_reader = None
        self._chat_channel = ""
        self._chat_seq += 1             # answers still on their way: stale

    def _chat_views(self):
        """Every chat on show: the side panel's, and the pop-out's."""
        views = [self.panel.lyrics.chat]
        if self._chat_window is not None and self._chat_window.isVisible():
            views.append(self._chat_window.chat)
        return views

    def _chat_messages(self, seq, items):
        for view in self._views_for(seq):
            view.add_messages(items)

    def _chat_removed(self, seq, kind, key):
        for view in self._views_for(seq):
            view.remove(kind, key)

    def _chat_status(self, seq, text):
        for view in self._views_for(seq):
            view.set_status(text)

    def _chat_auth(self, seq, login):
        """Joined: as you (the box to type in shows) or anonymously."""
        views = self._views_for(seq)
        if not views:
            return
        import twitchauth
        saved = twitchauth.signed_in_as()
        for view in views:
            view.set_writer(login, saved)
        if seq == self._chat_seq:
            self._chat_login = login
        page = getattr(self, "options_page", None)
        if page is not None:
            page.show_chat_login(saved)     # it may have just ended

    def _chat_note(self, seq, text):
        for view in self._views_for(seq):
            view.add_note(text)

    def _chat_send(self, text, view=None):
        window = self._chat_window
        pop = (view is not None and window is not None
               and view is window.chat)
        reader = self._pop_reader if pop and self._pop_reader is not None \
            else self._chat_reader
        if reader is not None:
            reader.say(text)

    # -- signing in to chat (build 82, task 92e) ----------------------------

    _chat_login = ""
    _sign_in_window = None

    def _chat_sign_in(self):
        """Twitch's device-code sign-in, in a small window."""
        import crashlog
        import devmode
        import twitchauth
        from PyQt5.QtWidgets import QMessageBox
        if not twitchauth.client_id():
            crashlog.write("[chat] sign-in: no Twitch app Client ID in "
                           "this build")
            QMessageBox.information(
                self, "Sign in to Twitch chat",
                "Paste your Twitch app's Client ID in Settings > Twitch "
                "chat first." if devmode.is_dev() else
                "Signing in to Twitch chat isn't set up in this build.")
            return
        window = self._sign_in_window
        if window is not None:
            try:
                window.raise_()
                window.activateWindow()
                return
            except RuntimeError:
                pass
        window = twitchauth.sign_in_dialog(self.window())
        window.signed_in.connect(self._chat_signed_in)
        window.destroyed.connect(self._sign_in_closed)
        self._sign_in_window = window
        window.show()
        window.start()

    def _sign_in_closed(self, *_):
        self._sign_in_window = None

    def _chat_signed_in(self, login):
        self._chat_account_changed(login)

    def _chat_sign_out(self):
        import twitchauth
        twitchauth.sign_out()
        self._chat_account_changed("")

    def _chat_account_changed(self, login):
        """Signed in or out: the open chat reconnects as the new you
        (keeping its lines), and Settings says so."""
        for view in self._chat_views():
            view.set_writer("", login)
        page = getattr(self, "options_page", None)
        if page is not None and hasattr(page, "show_chat_login"):
            page.show_chat_login(login)
        if self._chat_channel and self.playback.current:
            self._twitch_chat_for(self.playback.current, keep=True)
        if self._pop_reader is not None:
            self._start_pop_reader(self._pop_channel, keep=True)

    def _chat_room(self, seq, room_id):
        """The channel's Twitch number: BetterTTV's emotes for it, its
        chat badges, and your emotes ranked with this channel's first."""
        channel = self._channel_for(seq)
        if channel:
            import chatextras
            extras = chatextras.shared()
            extras.set_room(channel, room_id)
            extras.load_bttv(room_id, channel)
            extras.load_badges(room_id, channel)
            extras.load_rules(channel)
            reader = self._reader_for(seq)
            if reader is not None and reader.login:
                extras.load_my_emotes(dict(reader.me).get("emote-sets", ""),
                                      room_id, channel)

    def _chat_me(self, seq, me):
        """What Twitch said about you: your emote sets (task 95)."""
        channel = self._channel_for(seq)
        if not channel:
            return
        import chatextras
        for view in self._views_for(seq):
            view.bar.set_me(me.get("display-name", ""), me.get("color", ""))
        reader = self._reader_for(seq)
        room_id = getattr(reader, "room_id", "") if reader else ""
        chatextras.shared().load_my_emotes(me.get("emote-sets", ""),
                                           room_id, channel)

    # -- chat extras: switches and the pop-out window (task 92) -------------

    _chat_window = None
    NO_CHAT = "No Twitch station is playing. Its chat shows here when one is."

    def _load_chat_options(self):
        import chatextras
        for key, default in chatextras.OPTIONS.items():
            chatextras.OPTIONS[key] = bool(
                self.lib.settings.get(key, default))
        saved = self.lib.settings.get("chat_rules_ok") or []
        if isinstance(saved, list):
            chatextras.shared().rules_ok.update(str(c) for c in saved)
        # Pinned emotes (task 106), saved whenever they change.
        extras = chatextras.shared()
        extras.set_pins(self.lib.settings.get("chat_pins"))
        extras.pins_changed.connect(self._save_chat_pins)

    def _save_chat_pins(self):
        import chatextras
        self.lib.settings["chat_pins"] = list(chatextras.shared().pins)
        self.lib.save_settings()

    def _hook_chat_view(self, view):
        view.pop_out.connect(self._pop_out_chat)
        view.option_toggled.connect(self._chat_option)
        view.send_asked.connect(
            lambda text, v=view: self._chat_send(text, v))
        view.sign_in_asked.connect(self._chat_sign_in)
        view.sign_out_asked.connect(self._chat_sign_out)
        view.settings_asked.connect(
            lambda: QTimer.singleShot(0, self._open_twitch_settings))
        view.rules_accepted.connect(self._chat_rules_ok)
        view.colour_asked.connect(self._chat_colour)

    def _chat_colour(self, value, hex_colour):
        """Change your Twitch name colour (task 95c), off the GUI thread."""
        import crashlog
        import twitchauth
        # Task 111 (the stream paused after a pick): nothing here touches
        # the player, so the log says whether it was playing before and
        # after, and who asked if it paused (`[playstate] pause asked`).
        crashlog.write(f"[chat] color: asked for '{value}' (station "
                       f"playing: {bool(self.player._playing)})")
        self._look_up(lambda: twitchauth.set_chat_colour(value),
                      lambda result, error, h=hex_colour:
                      self._chat_colour_done(h, result, error))

    def _chat_colour_done(self, hex_colour, result, error):
        ok, text = result if (error is None and result) else (
            False, "Couldn't reach Twitch.")
        import crashlog
        crashlog.write(f"[chat] color: answer {'ok' if ok else 'no'} "
                       f"(station playing: {bool(self.player._playing)})")
        reader = self._chat_reader
        if ok and reader is not None:
            reader.me["color"] = hex_colour     # your next lines use it
        for view in self._chat_views():
            if ok:
                me = dict(reader.me) if reader is not None else {}
                view.bar.set_me(me.get("display-name", "") or
                                view.bar.me_name, hex_colour)
            view.bar.colour_result(ok, text)

    def _chat_rules_ok(self, channel):
        """You pressed OK on a channel's chat rules (task 95): saved, so
        they don't come up again there."""
        import chatextras
        import crashlog
        extras = chatextras.shared()
        extras.rules_ok.add(channel)
        self.lib.settings["chat_rules_ok"] = sorted(extras.rules_ok)[-500:]
        self.lib.save_settings()
        crashlog.write(f"[chat] rules: OK for '{channel}', you can write")
        for view in self._chat_views():
            view._update_bar()

    def _chat_option(self, key, on):
        """Show emotes / BetterTTV emotes / pronouns, from the chat's
        right-click menu. Saved; every chat on show redraws."""
        import chatextras
        import crashlog
        chatextras.OPTIONS[key] = bool(on)
        self.lib.settings[key] = bool(on)
        self.lib.save_settings()
        crashlog.write(f"[chat] {key} {'on' if on else 'off'}")
        for reader, channel in ((self._chat_reader, self._chat_channel),
                                (self._pop_reader, self._pop_channel)):
            if reader is None:
                continue
            if key == "chat_emotes" and on:
                chatextras.shared().load_bttv(
                    getattr(reader, "room_id", ""), channel)
            if key == "chat_badges" and on:
                chatextras.shared().load_badges(
                    getattr(reader, "room_id", ""), channel)
        for view in self._chat_views():
            if key == "chat_pronouns" and on:
                chatextras.shared().want_pronouns(list(view._items))
            view._rebuild()

    def _pop_out_chat(self):
        """Open the chat in a window of its own as well (task 92)."""
        import crashlog
        import sidepanel
        window = self._chat_window
        if window is None:
            window = sidepanel.ChatWindow()
            self._hook_chat_view(window.chat)
            window.closed.connect(self._chat_window_closed)
            saved = self.lib.settings.get("chat_window_geometry") or ""
            if saved:
                from PyQt5.QtCore import QByteArray
                try:
                    window.restoreGeometry(
                        QByteArray.fromBase64(saved.encode("ascii")))
                except Exception:                   # noqa: BLE001
                    pass
            self._chat_window = window
        source = self.panel.lyrics.chat
        was_open = window.isVisible()
        # The pop-out keeps the channel it was opened on while the side
        # panel changes frequencies (task 107); asking again from a
        # chat on another channel moves it there.
        if not was_open or (self._chat_channel and
                            self._pop_channel != self._chat_channel):
            self._stop_pop_reader()
            self._pop_channel = self._chat_channel
            window.set_channel(self._chat_channel)
            if self._chat_channel:
                window.chat.set_live(True, self._chat_channel)
                window.chat.load(list(source._items), source._status)
                window.chat.set_writer(source._writer, source._signed_in)
            else:
                window.chat.set_live(False)
                window.chat.load([], self.NO_CHAT)
        window.show()
        window.raise_()
        window.activateWindow()
        if not was_open:
            crashlog.write(f"[chat] pop-out window opened "
                           f"('{self._chat_channel or 'no Twitch station'}', "
                           f"{len(source._items)} messages copied over)")

    def _chat_window_closed(self):
        import crashlog
        window = self._chat_window
        if window is None:
            return
        self.lib.settings["chat_window_geometry"] = bytes(
            window.saveGeometry().toBase64()).decode("ascii")
        self.lib.save_settings()
        self._stop_pop_reader()
        self._pop_channel = ""
        crashlog.write("[chat] pop-out window closed")

    def _close_chat_window(self):
        """At quit: closed and deleted while Qt is still here (a
        top-level window left for PyQt's own clean-up can crash it)."""
        window = self._chat_window
        self._chat_window = None
        self._stop_pop_reader()
        if window is None:
            return
        try:
            if window.isVisible():
                window.close()
            from PyQt5 import sip
            sip.delete(window)
        except RuntimeError:
            pass

    # -- talk stations (task 49) ------------------------------------------

    _station_info_mode = None

    def set_station_info_mode(self, mode):
        """What the artist panel's menus do while a station's own info
        holds it: None (an artist, as usual), "edit" (a dev build: Edit
        this artist… edits the station's info) or "locked" (a user build:
        nothing to edit, the info ships with the app)."""
        self._station_info_mode = mode
        editor = self.edit_station_info if mode == "edit" else None
        for view in (getattr(self.panel, "artist", None),
                     getattr(getattr(self.panel, "artist", None), "header",
                             None),
                     getattr(self.panel, "header", None)):
            if view is not None:
                view._station_editor = editor
                view._station_locked = mode == "locked"
                if mode and hasattr(view, "_show_verdict"):
                    view._show_verdict(False)

    def edit_station_info(self):
        """Edit this artist… on a talk station, in a dev build.

        Edits the station's own info (Wikipedia page, bio, photo) in the
        developer's talk list, which ships in the next build.
        """
        import devmode
        station = self.playback.current
        if not station or not devmode.is_dev():
            return
        from artistedit import ArtistEditDialog
        dialog = ArtistEditDialog(storage.station_display_name(station),
                                  self.panel, station=station)
        if dialog.exec_() and self.feeds is not None:
            self.feeds.refresh_station_info()
            self.notify("Saved. It ships to everyone in your next build.",
                        transient=True)

    def on_artist_photo(self, photo):
        """Set or clear the artist picture on its own."""
        pixmap = None
        if photo is not None:
            import workers
            pixmap = (photo if isinstance(photo, QPixmap)
                      else workers.pixmap_for(photo))
            if isinstance(photo, str):
                # The file goes with it, for "Crop this photo…" (task 46).
                self.panel.set_photo_path(photo)
        self.panel.set_photo(pixmap)

    def on_artist(self, name, genres="", biography="", photo=None):
        """From workers.ArtistInfoWorker. The photo comes back as a local
        path because Qt's rich text has no network stack."""
        self._bio = biography if biography and biography != "Looking up…" else ""
        # The lookup has answered, whatever it said.
        if biography or genres:
            self.panel.set_busy(artist=False)
        self.panel.set_artist(name, genres, biography)
        # Decoded once, and usually already decoded by the lookup's own
        # thread (workers.pixmap_for). It was read from disk twice here,
        # on the GUI thread: 430–490 ms (task 24, build 55).
        pixmap = None
        if photo:
            import workers
            pixmap = (photo if isinstance(photo, QPixmap)
                      else workers.pixmap_for(photo))
        self.now_playing.set_artist(name, self._bio, pixmap)
        self.panel.set_available(self._lyrics, self._bio)
        self._refresh_fullscreen_state()
        if photo:
            # The path travels with the picture, so the crop action knows
            # which file to rewrite.
            if isinstance(photo, str):
                self.panel.set_photo_path(photo)
            self.panel.set_photo(pixmap)
