"""Frequencies — the classic skinned view.

The Winamp stack, opened from the sidebar and swapped for the main window
rather than shown alongside it. `main.py` drove it through fifteen or so
handler methods scattered across MainWindow; this collects the ones that
matter into one object with a `Playback` behind it.

The stack comes up whole — player, equalizer, playlist — because Winamp
remembered which windows were open and opening only the player left the
other two to be found in a menu, which is not how anyone used it.

Handlers deliberately match the main window's, so the skinned player, the
media keys and the ordinary view can never disagree about what is playing.
"""

import os
import sys

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication

import storage
import winampskin
from classicwindow import ClassicDock, ClassicWindow

TAG_MIN_WIDTH = 180         # skin pixels; narrower and a chip cannot fit
TAG_MAX_WIDTH = 900

TITLE_ROTATE_MS = 8000
INFO_POLL_MS = 2000
RETIRE_RETRY_MS = 250


# -- finished windows ------------------------------------------------------
#
# A skin change (or closing the stack) is nearly always asked for from a
# menu or dialog that a panel itself opened: right-click the title bar,
# pick "Random skin now". At that moment the old player's mousePressEvent
# is still running underneath the menu's event loop. Deleting the player
# there - deleteLater runs inside that nested loop, and dropping the last
# Python reference deletes it on the spot - left Qt finishing the click
# on a freed widget (it sends the right-click's context-menu event to it
# after the handler returns). That was the crash to desktop on every
# skin change (task 45; reproduced under a Wayland compositor).
#
# So finished windows are only hidden here, kept alive, and deleted once
# nothing of theirs is on the stack and no menu or dialog is open.

_retired = []


def retire(widget):
    """Hide a finished skin window; delete it once it is safe (task 45)."""
    if widget is None:
        return
    try:
        widget.hide()
        widget.blockSignals(True)
    except RuntimeError:
        return                          # already gone
    _retired.append(widget)
    QTimer.singleShot(0, _bury)


def _belongs_to(obj, widget):
    """Is obj the widget, or anything inside it (menus and dialogs too)?"""
    while obj is not None:
        if obj is widget:
            return True
        try:
            obj = obj.parent()
        except RuntimeError:
            return False
    return False


def _in_use(widget):
    """Could deleting widget now pull it out from under running code?"""
    app = QApplication.instance()
    if app is None:
        return False
    # A menu or dialog still open: its event loop may sit on top of the
    # widget's own click handler, even when no Python frame shows it.
    if app.activePopupWidget() is not None \
            or app.activeModalWidget() is not None:
        return True
    frame = sys._getframe(1)
    while frame is not None:
        try:
            owner = frame.f_locals.get("self")
        except Exception:                       # noqa: BLE001
            owner = None
        if isinstance(owner, QObject) and _belongs_to(owner, widget):
            return True
        frame = frame.f_back
    return False


def _bury():
    from PyQt5 import sip
    waiting = []
    while _retired:
        widget = _retired.pop()
        if sip.isdeleted(widget):
            continue
        if _in_use(widget):
            waiting.append(widget)
            continue
        widget.deleteLater()
    _retired.extend(waiting)
    if waiting:
        QTimer.singleShot(RETIRE_RETRY_MS, _bury)


class TagResizer(QObject):
    """Lets the tag window be resized horizontally once it is undocked.

    Docked, its width belongs to the stack — the player and equalizer are
    fixed-width, so a wider tag window would leave bare grey beside them.
    Floating, there is nothing to line up with, and a wider window means
    fewer rows of chips.

    The height is never dragged: it is recomputed from the rows the chips
    need at the new width, so resizing can never cut a tag off.
    """

    EDGE = 6                    # grab zone, in real pixels

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.w = window
        self._from = None
        self._start_width = 0

    def _floating(self):
        return getattr(self.w, "dock", None) is None

    def _on_edge(self, pos):
        return self.w.width() - pos.x() <= self.EDGE

    def _off_edge(self):
        if getattr(self.w, "_edge_cursor", False):
            self.w._edge_cursor = False
            self.w._cursor_name = None

    def eventFilter(self, obj, event):
        if obj is not self.w:
            return False
        if not self._floating():
            self._off_edge()
            return False

        if event.type() == QEvent.MouseMove and self._from is None:
            if self._on_edge(event.pos()):
                self.w._edge_cursor = True
                self.w.setCursor(Qt.SizeHorCursor)
            else:
                # Hand the pointer back to the window, which puts the
                # skin's cursor on again as this same move reaches it.
                # This used to unsetCursor() on every move, and the window
                # then thought its skin cursor was still on, so a floating
                # Tags window showed the desktop's arrow (task 32).
                self._off_edge()
            return False
        if event.type() == QEvent.Leave and self._from is None:
            self._off_edge()
            return False

        if (event.type() == QEvent.MouseButtonPress
                and event.button() == Qt.LeftButton
                and self._on_edge(event.pos())):
            self._from = event.globalPos().x()
            self._start_width = self.w.skin_width
            return True             # don't let it start a window drag

        if event.type() == QEvent.MouseMove and self._from is not None:
            delta = (event.globalPos().x() - self._from) // max(
                1, self.w.scale)
            self._resize(self._start_width + delta)
            return True

        if event.type() == QEvent.MouseButtonRelease and self._from is not None:
            self._from = None
            return True

        return False

    def _resize(self, width):
        width = max(TAG_MIN_WIDTH, min(TAG_MAX_WIDTH, int(width)))
        if width == self.w.skin_width:
            return
        # Width first: rows_needed measures against skin_width, so the row
        # count has to be asked for after the new width is in place.
        self.w.resize_skin(width, self.w.skin_height)
        self.w.resize_skin(width,
                           self.w.height_for_rows(self.w.rows_needed()))


class ClassicSession(QObject):
    """Owns the skinned stack for as long as it is open."""

    closed = pyqtSignal()               # the user wants the main view back
    # The audio tap reads on its own thread; the samples cross to the GUI
    # thread through this before they reach the player (which repaints).
    _tap_samples = pyqtSignal(object)

    def __init__(self, window, library, playback, parent=None):
        super().__init__(parent or window)
        self.win = window
        self.lib = library
        self.playback = playback
        self.dock = None
        self.player = None
        self.equaliser = None
        self.playlist = None
        self.tags = None
        self.history = None
        self.lyrics = None
        self._visualizer = None
        self._tap = None
        self._skin = None
        self._titles = []
        self._title_index = 0
        self._list_stamp = None
        self._scanning = False

        self._rotate = QTimer(self)
        self._rotate.setInterval(TITLE_ROTATE_MS)
        self._rotate.timeout.connect(self._advance_title)

        self._info = QTimer(self)
        self._info.setInterval(INFO_POLL_MS)
        self._info.timeout.connect(self._poll_info)

        self._tap_samples.connect(self._feed_player)

    # -- opening -----------------------------------------------------------

    def _log(self, text):
        """Why the classic player did or did not open.

        It is one button with several ways to fail — a missing file, a
        skin that will not parse, a bundled copy that was not packaged —
        and they all looked the same from outside.
        """
        try:
            import crashlog
            crashlog.write("[classic] " + text)
        except Exception:                       # noqa: BLE001
            pass

    def _log_skin(self, skin):
        """Which pointers the skin brings, once per skin put on (task 43).

        "The plain arrow shows" cannot be told apart from "this skin has
        no pointer for that part" without it.
        """
        if skin is None:
            return
        try:
            import crashlog
            crashlog.write(f"[skin] {skin.name}: {skin.describe_cursors()}")
        except Exception:                       # noqa: BLE001
            pass

    def load_skin(self):
        """The configured skin, or the bundled one.

        A missing skin file is not a reason to refuse: falling back means
        the button always does something, and a default skin is what a
        fresh install has anyway.
        """
        path = (self.lib.settings.get("skin_path") or "").strip()
        if path and os.path.exists(path):
            try:
                skin = winampskin.load_skin(path)
                self._log(f"loaded chosen skin {path}")
                return skin
            except Exception as exc:    # noqa: BLE001
                self._log(f"chosen skin {path} failed: {exc!r}")
        elif path:
            self._log(f"chosen skin is missing: {path}")
        # The bundled one, wherever it ended up.
        #
        # An AppImage lays the tree out differently from a source
        # checkout, so a single hard-coded path is one packaging change
        # away from "no skin installed".
        for folder in winampskin.bundled_skin_folders():
            if not os.path.isdir(folder):
                continue
            # The Frequencies skin (FreqAmp) first: it is the default
            # since build 120 (task 168); sorted alone put base.wsz first.
            names = sorted(os.listdir(folder),
                           key=lambda n: (n != winampskin.OWN_SKIN, n))
            for name in names:
                if not name.lower().endswith((".wsz", ".zip")):
                    continue
                candidate = os.path.join(folder, name)
                try:
                    skin = winampskin.load_skin(candidate)
                    self._log(f"loaded bundled skin {candidate}")
                    return skin
                except Exception as exc:        # noqa: BLE001
                    self._log(f"bundled {candidate} failed: {exc!r}")
        self._log("no skin found in any bundled location")
        return None

    def open(self):
        if self.player is not None:
            return True
        if self.lib.settings.get("skin_random"):
            self._pick_random_skin()
        self._skin = self.load_skin()
        if self._skin is None:
            return False
        self._log_skin(self._skin)

        scale = int(self.lib.settings.get("skin_scale", 1))
        self.dock = ClassicDock()
        self.dock.closed.connect(self._on_dock_closed)
        self.dock.set_skin(self._skin)

        player = ClassicWindow(self._skin, parent=self.dock, scale=scale)
        self._wire_player(player)
        self.player = player
        self.dock.add_panel(player)

        self._open_extras(scale)

        # After the panels exist, so they are fed before being seen.
        self.refresh()
        self._start_tap()
        self._rotate.start()
        self._info.start()
        self.dock.show()
        self.dock.raise_()
        self.player.show()
        QTimer.singleShot(0, self._refresh_pointer)
        return True

    def _open_extras(self, scale):
        """The rest of the stack: equalizer, tags, then playlist.

        All three are built whatever their skin_show_* setting says, and
        then hidden if it says so. Building only the ones set to show left
        the menu with nothing to toggle -- getattr returned None and the
        entry did nothing -- so a setting turned off in some earlier
        session could never be turned back on. Which is exactly what
        "they don't load in" looks like from the outside.
        """
        try:
            from classicextras import (EqualizerWindow, PlaylistWindow,
                                       TagWindow)
        except ImportError as exc:
            self._report(f"The skinned panels are missing from this build "
                         f"({exc}).")
            return

        self._build("equaliser", lambda: self._make_eq(EqualizerWindow, scale))
        self._build("tags", lambda: self._make_tags(TagWindow, scale))
        self._build("playlist",
                    lambda: self._make_playlist(PlaylistWindow, scale))
        # Off unless asked for: Winamp's stack was three windows, and
        # opening five on a fresh install buries the player.
        self._build("lyrics", lambda: self._make_lyrics(scale))
        self._build("history", lambda: self._make_history(scale))

    PANEL_SETTINGS = {"equaliser": "skin_show_eq",
                      "tags": "skin_show_tags",
                      "playlist": "skin_show_playlist",
                      "history": "skin_show_history",
                      "lyrics": "skin_show_lyrics"}

    def _build(self, which, factory):
        """Build one panel, keeping a failure to itself.

        One panel that cannot be built must not take the other two with
        it, and it must say which one it was -- a silent return here is
        indistinguishable from the panel simply never appearing.
        """
        if getattr(self, which) is not None:
            return getattr(self, which)
        try:
            panel = factory()
        except Exception as exc:                # noqa: BLE001
            self._report(f"The {which} panel could not be built: "
                         f"{type(exc).__name__}: {exc}")
            return None
        if panel is None:
            return None
        setattr(self, which, panel)
        self._wire_panel(panel)
        self.dock.add_panel(panel)          # this shows it and refits
        panel.setVisible(bool(self.lib.settings.get(
            self.PANEL_SETTINGS[which], which not in ("lyrics", "history"))))
        return panel

    def _report(self, message):
        """To the log and, if the main window is reachable, to the panel."""
        try:
            import crashlog
            crashlog.write("[classic] " + message)
        except Exception:                       # noqa: BLE001
            pass
        print("Frequencies:", message, file=sys.stderr)

    def _make_eq(self, EqualizerWindow, scale):
        # A skin with no eqmain.bmp has no equaliser to draw.
        if not getattr(self._skin, "has_equalizer", lambda: True)():
            self._report("This skin has no eqmain.bmp, so no equalizer.")
            return None
        eq = EqualizerWindow(self._skin, scale=scale, parent=self.dock)
        eq.eq_fill = storage.eq_fill_for_skin(
            self.lib.settings, getattr(self._skin, "name", ""))
        eq.on_change = self._on_eq_changed
        return eq

    def _make_tags(self, TagWindow, scale):
        # The same scale as the rest of the stack. It was one smaller, so
        # at double size the tag window came out half as wide as the
        # panels around it, with a title bar half their height.
        tags = TagWindow(self._skin, scale=scale, parent=self.dock)
        tags.tag_clicked.connect(self._on_tag_clicked)
        # Undocked, it can be dragged wider or narrower; the height follows
        # so every chip stays visible at whatever width you choose.
        tags.installEventFilter(TagResizer(tags, self))
        return tags

    def _make_lyrics(self, scale):
        from classicwindows import LyricsWindow
        w = LyricsWindow(self._skin, scale=scale, parent=self.dock)
        w.set_lyrics([], "Lyrics appear here once a song is identified.")
        # The same resync as the main window, so both views move together.
        w.resync_requested.connect(self.win._resync_lyrics)
        return w

    def _make_history(self, scale):
        from classicwindows import HistoryWindow
        w = HistoryWindow(self._skin, scale=scale, parent=self.dock)
        w.entry_activated.connect(self._history_activated)
        w.scope_toggled.connect(lambda _f: self.refresh_history())
        w.menu_requested.connect(self._history_menu)
        return w

    def _history_menu(self, entry, where):
        """The main window's history menu, for a row in the skinned list.

        Found by song rather than rebuilt here, so the two windows offer
        exactly the same choices and cannot drift apart.
        """
        song, artist = entry[0], entry[1]
        key = storage.track_key(artist, song)
        match = next((e for e in self.lib.history_entries()
                      if storage.entry_key(e) == key), None)
        if match is None:
            return
        self.win._history_menu(match, where)
        # A favourite changed from the menu should show here straight away.
        self.refresh_history()

    def _history_activated(self, row):
        """Starring from the skinned list writes to the same history the
        main window shows — one record, two views of it."""
        entry = self.history.entry_at(row) if self.history else None
        if not entry:
            return
        song, artist, station, starred = entry[:4]
        self.lib.set_favourite(
            storage.track_key(artist, song), not starred,
            track={"title": song, "artist": artist}, station_name=station)
        self.win.refresh_history()
        self.refresh_history()

    def refresh_history(self):
        if self.history is None:
            return
        entries = self.lib.history_entries()
        if self.history.favourites_only:
            entries = [e for e in entries if storage.is_favorite(e)]
        self.history.set_entries(
            [(e["song"], e["artist"], e["station"], storage.is_favorite(e),
              e.get("at", 0))
             for e in entries[:200]])

    def set_lyrics(self, lines, message=None):
        if self.lyrics is not None:
            self.lyrics.set_lyrics(lines, message)

    def set_position(self, ms):
        if self.lyrics is not None:
            self.lyrics.set_position(ms)

    def _make_playlist(self, PlaylistWindow, scale):
        playlist = PlaylistWindow(
            self._skin, scale=scale, parent=self.dock,
            height=int(self.lib.settings.get("skin_playlist_height", 232)))
        playlist.manual_sort = (
            self.lib.settings.get("sort_mode", "manual") == "manual")
        playlist.station_activated.connect(self._play_index)
        # The filter bar along the bottom of the playlist. Its keystrokes
        # were going nowhere: the signal exists and nothing was connected
        # to it, so typing in it changed the caption and not the list.
        playlist.station_menu.connect(self._playlist_menu)
        playlist.search_changed.connect(self._on_playlist_search)
        playlist.set_search_text(self.win.stations_page.search.text())
        if hasattr(playlist, "rows_reordered"):
            playlist.rows_reordered.connect(self._on_playlist_reorder)
        playlist.on_resized = lambda _w, h: self._save(
            "skin_playlist_height", int(h))
        return playlist

    def _wire_panel(self, panel):
        """Every skinned panel can be torn off and put back, and every one
        of them right-clicks to the same entries.

        The player and the extras take their menu differently: the player
        calls on_menu(global_pos) and builds nothing itself, while the
        extras build their own detach/attach/close menu and call
        extra_menu(menu) to have the shared entries added to it. Setting
        on_menu on an extras panel does nothing at all, which is why
        right-clicking those three offered only Close.
        """
        if hasattr(panel, "reattach_requested"):
            panel.reattach_requested.connect(
                lambda w=panel: self.dock.add_panel(w))
        if hasattr(panel, "close_requested"):
            panel.close_requested.connect(lambda w=panel: self._hide(w))
        panel.extra_menu = self.fill_menu

    def _hide(self, panel):
        """Closing a panel hides it and remembers that, rather than
        destroying it — the menu has to be able to bring it back."""
        panel.setVisible(False)
        for which, key in self.PANEL_SETTINGS.items():
            if getattr(self, which, None) is panel:
                self._save(key, False)
        if self.dock is not None:
            self.dock.refit()

    def fill_menu(self, menu):
        """The entries every skinned title bar offers."""
        menu.addAction("Show the main window", self._back_to_main)
        menu.addSeparator()
        menu.addAction("Equalizer", lambda: self._toggle_panel("equaliser"))
        menu.addAction("Playlist", lambda: self._toggle_panel("playlist"))
        menu.addAction("Tags", lambda: self._toggle_panel("tags"))
        menu.addAction("Lyrics", lambda: self._toggle_panel("lyrics"))
        menu.addAction("History", lambda: self._toggle_panel("history"))
        menu.addAction("Visualizer", self.toggle_visualizer)
        menu.addSeparator()
        menu.addAction("Skins…", self._open_skin_locker)
        act = menu.addAction("Random skin each launch")
        act.setCheckable(True)
        act.setChecked(bool(self.lib.settings.get("skin_random")))
        act.triggered.connect(self._toggle_random_skin)
        menu.addAction("Random skin now", self.random_skin)

    def _toggle_panel(self, which):
        """Show or hide a panel, building it if it was never built."""
        panel = getattr(self, which, None)
        if panel is None:
            scale = int(self.lib.settings.get("skin_scale", 1))
            from classicextras import (EqualizerWindow, PlaylistWindow,
                                       TagWindow)
            factory = {
                "equaliser": lambda: self._make_eq(EqualizerWindow, scale),
                "tags": lambda: self._make_tags(TagWindow, scale),
                "playlist": lambda: self._make_playlist(PlaylistWindow,
                                                        scale),
                "lyrics": lambda: self._make_lyrics(scale),
                "history": lambda: self._make_history(scale),
            }[which]
            panel = self._build(which, factory)
            if panel is None:
                return
            self.refresh()
            panel.setVisible(True)
            self._save(self.PANEL_SETTINGS[which], True)
            return
        panel.setVisible(not panel.isVisible())
        self._save(self.PANEL_SETTINGS[which], panel.isVisible())

    def visible_rows(self):
        """The filtered list, as the main window has it.

        One list, two faces: the playlist shows exactly what the station
        list shows, and Next walks the same order, so a tag chosen in
        either place means the same thing everywhere.
        """
        model = self.win.stations_page.list.model_
        return [model.station_at(i) for i in range(model.rowCount())]

    def _play_index(self, index):
        self._scanning = False
        rows = self.visible_rows()
        if 0 <= index < len(rows):
            self.playback.play(rows[index]["_source"])

    def _on_eq_changed(self, enabled, preamp, bands, simple=None):
        """The skinned EQ edits the playing station's bands — the same
        setting the main window's sliders write, not a parallel one."""
        station = self.playback.current
        if station is None:
            return
        self._save("eq_advanced", bool(enabled))
        station["eq_bands"] = storage.normalize_eq_bands(bands)
        if simple is not None and len(simple) >= 3:
            station["eq_bass"] = int(simple[0])
            station["eq_mid"] = int(simple[1])
            station["eq_treble"] = int(simple[2])
        station["gain_db"] = max(-20.0, min(20.0, float(preamp)))
        self.lib.save_stations()
        self.playback.apply_eq(station)
        self.playback.player.set_gain_db(station["gain_db"])

    def _playlist_menu(self, index, where, _window):
        """The same entries the main list offers, on the skinned rows."""
        rows = self.visible_rows()
        if not (0 <= index < len(rows)):
            return
        row = rows[index]
        station = row["_source"]
        menu = self._menu(self.playlist)
        menu.addAction("Play", lambda: self.playback.play(station))
        menu.addSeparator()
        menu.addAction("Edit…", lambda: self.win.edit_station(station))
        menu.addAction("Tags…", lambda: self.win.tag_station(row))
        menu.addAction("Options…", lambda: self.win.station_options(row))
        menu.addSeparator()
        menu.addAction("Remove", lambda: (self.win.remove_station(station),
                                          self.refresh_playlist()))
        from shell import open_menu
        open_menu(menu, where)

    def _on_playlist_search(self, text):
        """One search box, two faces — the same field the station list
        filters on, so what you type here and what you type there mean the
        same thing."""
        self.win.stations_page.search.setText(text or "")
        self.refresh_playlist()

    def _on_playlist_reorder(self, source, target):
        """Dragging a row only means something in manual order."""
        if self.lib.settings.get("sort_mode", "manual") != "manual":
            return
        rows = self.visible_rows()
        if not (0 <= source < len(rows)) or not (0 <= target < len(rows)):
            return
        ids = [r["id"] for r in rows]
        ids.insert(target, ids.pop(source))
        self.lib.reorder(ids)
        self.win.reload()
        self.refresh_playlist()

    def _on_tag_clicked(self, name, exclude=False):
        """Filter the playlist, in place.

        Left-click requires the tag, right-click excludes it, and clicking
        the same way twice clears it — the same rule as the sidebar pills,
        so the two views can never mean different things by the same chip.

        It used to hand the tag to the main window and switch to it, which
        threw you out of the skinned player to look at a result you asked
        for from inside it. The filter is shared state; only the view
        needed to stay put.
        """
        target = "exclude" if exclude else "include"
        current = self.win.sidebar.tag_states().get(name, "off")
        self.win.sidebar.set_tag_state(
            name, "off" if current == target else target)
        # The main window updates itself off that signal; these two are the
        # skinned half of the same picture.
        self.refresh_tags()
        self.refresh_playlist()

    def refresh_eq(self):
        if self.equaliser is None:
            return
        station = self.playback.current or {}
        self.equaliser.set_state(
            bool(self.lib.settings.get("eq_advanced")),
            storage.normalize_eq_bands(station.get("eq_bands")),
            int(station.get("gain_db", 0) or 0),
            simple=[int(station.get("eq_bass", 0) or 0),
                    int(station.get("eq_mid", 0) or 0),
                    int(station.get("eq_treble", 0) or 0)])

    def refresh_tags(self):
        """The same tags and states the rail shows.

        set_tags wants (name, color) pairs, not bare names — handing it
        strings gave it nothing it could draw.
        """
        if self.tags is None:
            return
        self.tags.set_tags([(tag, self.lib.tag_colour(tag))
                            for tag in storage.library_tags(
                                self.lib.stations, self.lib.settings)])
        # set_states wants storage's numeric constants keyed by lowercase
        # name -- 1 include, -1 exclude. It was getting the sidebar's own
        # "include"/"exclude" strings, which the painter compares against
        # numbers, so no chip ever showed as filtered.
        states = {}
        for name, state in self.win.sidebar.tag_states().items():
            states[name.lower()] = (storage.TAG_INCLUDED if state == "include"
                                    else storage.TAG_EXCLUDED)
        self.tags.set_states(states)
        # Grow to the rows the chips actually need, so every tag is visible
        # without scrolling.
        #
        # setFixedHeight was wrong twice over: these windows measure
        # themselves in skin pixels, not screen pixels, so at scale 2 the
        # comparison was against a number twice the size and the window was
        # resized on every refresh -- and a fixed height fights the layout
        # the dock uses to stack panels, which is what was bringing the
        # whole thing down. resize_skin is the API for this, and the dock
        # has to be told to refit afterwards or it keeps its old height
        # with a taller panel inside it.
        wanted = self.tags.height_for_rows(self.tags.rows_needed())
        if wanted and wanted != self.tags.skin_height:
            self.tags.resize_skin(self.tags.skin_width, wanted)
            if self.dock is not None and self.tags.dock is not None:
                self.dock.refit()

    def refresh_playlist(self):
        if self.playlist is None:
            return
        rows = self.visible_rows()
        current = -1
        if self.playback.current is not None:
            playing = self.playback.current.get("id")
            current = next((i for i, r in enumerate(rows)
                            if r["id"] == playing), -1)
        self.playlist.set_stations([r["name"] for r in rows], current)

    def _wire_player(self, w):
        w.on_play = self._play
        # Pause stops, deliberately: pausing a live stream holds a buffer
        # that is stale the moment you resume, and most servers drop the
        # connection anyway.
        w.on_pause = self.playback.stop
        w.on_stop = self.playback.stop
        w.on_next = self.playback.next
        w.on_previous = self.playback.previous
        w.on_volume = self._set_volume
        w.on_shuffle = self._set_shuffle
        w.on_favourite = lambda on: self.win.player.fav_btn.setChecked(on)
        w.on_favourite_menu = lambda: self._toggle_panel("history")
        w.on_history = lambda: self._back_to_main("history")
        # The logo goes back to the ordinary window, and the close button
        # closes the application — it is the only window on screen while
        # this is up, so leaving the app running invisibly would surprise.
        w.on_logo = self._back_to_main
        w.on_close_all = self._quit
        w.on_eject = lambda: self.win.add_station_menu()
        w.on_tune = self._tune
        # Static while the dial moves, silence underneath it.
        #
        # The window already does the hard part — it only calls on_tune
        # once the dial has settled, either on release or after being held
        # still — but on_scan was never connected, so sweeping the dial
        # was silent and the station you were passing kept playing.
        w.on_scan = self._scan
        w.on_menu = self.titlebar_menu
        w.on_clutter = self._clutter
        w.clutter_state["d"] = int(
            self.lib.settings.get("skin_scale", 1)) > 1
        w.closed.connect(self._on_dock_closed)
        w.set_volume(int(self.lib.settings.get("volume", 80)))
        w.set_shuffle(bool(self.lib.settings.get("shuffle", False)))
        w.twenty_four_hour = bool(self.lib.settings.get("clock_24_hour"))
        w.vis_mode = self.lib.settings.get("skin_vis_mode", "bars")
        w.vis_gain = float(self.lib.settings.get("visualizer_gain", 1.0))

    # -- menus -------------------------------------------------------------

    def _menu(self, near=None):
        """A QMenu parented to the skinned window it belongs to.

        Wayland places a popup relative to its PARENT surface, not the
        screen. A menu built with the main window as parent is positioned
        against a window that isn't even visible, which is why it landed
        hundreds of pixels away — or, since the main window is hidden here,
        did not appear at all.
        """
        from PyQt5.QtWidgets import QMenu
        return QMenu(near or self.player or self.dock)

    def titlebar_menu(self, at=None):
        """Right-click on the player, or the icon in its title bar."""
        menu = self._menu()
        # No "Close the skinned player" at the bottom (task 65): it ran the
        # same _back_to_main as "Show the main window" at the top.
        self.fill_menu(menu)
        from shell import open_menu
        open_menu(menu, at or self._anchor())

    def _anchor(self):
        window = self.player or self.dock
        if window is None:
            from PyQt5.QtGui import QCursor
            return QCursor.pos()
        return window.mapToGlobal(window.rect().center())

    def _open_skin_locker(self):
        """The skin folder, as a list you can flip through.

        It reports through signals rather than a return value, and it
        remembers the folder itself — reading an attribute off the dialog
        after exec_() picked up nothing, which is why changing the folder
        appeared to do nothing at all.
        """
        # The dock, not the player: picking a skin rebuilds the player,
        # and a dialog parented to it would go with it (task 45).
        self.open_skin_locker(self.dock or self.win)

    def open_skin_locker(self, parent=None):
        from skinbrowser import SkinBrowser
        settings = self.lib.settings

        folder = settings.get("skin_folder", "")
        if not folder and settings.get("skin_path"):
            folder = os.path.dirname(settings["skin_path"])

        dlg = SkinBrowser(
            parent or self.win, folder=folder,
            current=settings.get("skin_path", ""),
            eq_fills=settings.get("skin_eq_fill") or {})
        dlg.skin_chosen.connect(self._on_skin_chosen)
        dlg.eq_fill_changed.connect(self._on_eq_fill_changed)
        dlg.exec_()

        # Remember where they keep them, so it opens there next time.
        if dlg.folder() != settings.get("skin_folder", ""):
            self._save("skin_folder", dlg.folder())

    def _on_skin_chosen(self, path):
        if not path:
            return
        try:
            skin = winampskin.load_skin(path)
        except winampskin.SkinError as exc:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self.player or self.win, "Skin", str(exc))
            return
        self._skin = skin
        self._save("skin_path", path)
        self._log(f"skin changed to {path}")
        self._log_skin(skin)
        # Applying a skin rebuilds every window in the stack, so this only
        # reopens when there is a stack to reopen — choosing one from the
        # main window just records it for next time.
        if self.player is not None:
            self.reopen()

    def _on_eq_fill_changed(self, skin_name, on):
        storage.set_eq_fill_for_skin(self.lib.settings, skin_name, on)
        self.lib.save_settings()
        if (self.equaliser is not None
                and getattr(self._skin, "name", "") == skin_name):
            self.equaliser.eq_fill = bool(on)
            self.equaliser.update()

    def _toggle_random_skin(self, on):
        self._save("skin_random", bool(on))
        if on:
            self.random_skin()

    def _skin_folder(self):
        folder = (self.lib.settings.get("skin_folder") or "").strip()
        if not folder and self.lib.settings.get("skin_path"):
            folder = os.path.dirname(self.lib.settings["skin_path"])
        winampskin.copy_own_skin(folder)
        return folder

    def _pick_random_skin(self):
        """Choose before the stack is built, so launch does not open one
        skin and immediately rebuild it as another."""
        import random
        folder = self._skin_folder()
        if not folder or not os.path.isdir(folder):
            return
        skins = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                 if f.lower().endswith((".wsz", ".zip"))]
        if skins:
            self.lib.settings["skin_path"] = random.choice(skins)
            self.lib.save_settings()

    def random_skin(self):
        """Another skin from the same folder, never the one already on."""
        import random
        folder = self._skin_folder()
        if not folder or not os.path.isdir(folder):
            self._report("No skin folder to choose from — open Skins… once "
                         "so it knows where they are.")
            return
        current = os.path.abspath(self.lib.settings.get("skin_path", ""))
        skins = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                 if f.lower().endswith((".wsz", ".zip"))]
        skins = [s for s in skins if os.path.abspath(s) != current]
        if not skins:
            return
        self._on_skin_chosen(random.choice(skins))

    def _clutter(self, name, active, at=None):
        """The O A I D V strip down the left of the visualiser."""
        import webbrowser
        if name == "o":
            self.titlebar_menu(at)
        elif name == "a":
            track = getattr(self.win.feeds, "_track", None) or {}
            url = (track.get("apple_music_url") or "").strip()
            if url:
                webbrowser.open(url, new=1, autoraise=True)
        elif name == "i":
            station = self.playback.current
            if station is not None:
                self.win.station_options(self.lib.row(station))
        elif name == "d":
            self.lib.settings["skin_scale"] = 2 if active else 1
            self.lib.save_settings()
            self.reopen()
        elif name == "v":
            self.toggle_visualizer()

    def reopen(self):
        """Reskin and rescale without replacing the dock.

        The dock is the only top-level window here, and under Wayland a
        client cannot position its own top-level windows — every move()
        is silently ignored. That is why the panels became children of one
        dock in the first place. So a skin change must not destroy the
        dock: closing it and opening a new one hands the compositor a new
        window, which it centres, and nothing we can call will move it
        back.

        Only the panels inside are rebuilt. The window itself never goes
        away, so it never moves.
        """
        if self.dock is None:
            self.open()
            return

        scale = int(self.lib.settings.get("skin_scale", 1))
        self.dock.set_skin(self._skin)
        showing = {which: (getattr(self, which) is not None
                           and getattr(self, which).isVisible())
                   for which in self.PANEL_SETTINGS}

        # Out of the dock's list as well as out of the dock. Only
        # unparenting them left the dock holding the deleted panels, and
        # the next focus change tried to repaint them ("wrapped C/C++
        # object ... has been deleted" in the log after a skin change).
        self.dock.rebuilding = True
        try:
            # Retired, not deleted: this usually runs from a menu the old
            # player opened, with its click handler still underneath
            # (task 45). retire() deletes them once that has finished.
            for which in list(self.PANEL_SETTINGS) + ["player"]:
                panel = getattr(self, which, None)
                if panel is not None:
                    panel.hide()
                    self.dock.remove_panel(panel, unparent=False)
                    retire(panel)
                    setattr(self, which, None)

            player = ClassicWindow(self._skin, parent=self.dock, scale=scale)
            self._wire_player(player)
            self.player = player
            self.dock.add_panel(player)
            player.show()

            self._open_extras(scale)
            for which, was_open in showing.items():
                panel = getattr(self, which, None)
                if panel is not None:
                    panel.setVisible(was_open)
        finally:
            # The dock holds its size through the swap and fits the new
            # panels once, at the end (see ClassicDock.refit).
            self.dock.rebuilding = False
        self.dock.refit()

        self._list_stamp = None
        self._scanning = False
        self.refresh()
        self._refresh_pointer()

    def _refresh_pointer(self):
        """The new panels' pointer for where the mouse already is (task 43).

        A panel built under a resting mouse wears its idle pointer and
        gets no mouse event until the mouse moves, so after a skin change
        the title bar under the pointer showed the wrong one.
        """
        from PyQt5.QtCore import QPoint
        from PyQt5.QtGui import QCursor
        for panel in (self.player, self.equaliser, self.tags,
                      self.playlist, self.lyrics, self.history):
            if panel is None or not panel.isVisible():
                continue
            try:
                local = panel.mapFromGlobal(QCursor.pos())
                if not panel.rect().contains(local):
                    continue
                scale = max(1, int(getattr(panel, "scale", 1)))
                panel._cursor_name = None
                panel._apply_cursor(QPoint(local.x() // scale,
                                           local.y() // scale))
            except (AttributeError, RuntimeError):
                pass

    # -- visualiser ---------------------------------------------------------

    def visualizer_window(self):
        """This session's visualiser window, if it is open.

        Since build 68 a closed visualiser deletes itself, so a name kept
        for it can point at a window that is gone: ask through here.
        """
        from PyQt5 import sip
        existing = getattr(self, "_visualizer", None)
        if existing is None or sip.isdeleted(existing) \
                or getattr(existing, "_closed", not existing.isVisible()):
            return None
        return existing

    def toggle_visualizer(self):
        """Opens a window either way. If projectM is unavailable the window
        says so rather than a status message claiming it is on while
        nothing appears anywhere."""
        existing = self.visualizer_window()
        if existing is not None and (existing.isMinimized()
                                     or not existing.isVisible()):
            # Brought back, not closed: a minimised visualiser is out of
            # sight, and the button is how you would look for it. (On
            # Wayland the app isn't told it was minimised, so there the
            # button closes it, as before.)
            self._log_vis(f"bringing window #{existing.number} back "
                          "from minimised")
            existing.showNormal()
            existing.raise_()
            existing.activateWindow()
            return
        if existing is not None:
            self._log_vis(f"closing window #{existing.number}")
            existing.close()
            self._visualizer = None
            self._save("visualizer", False)
            return
        from visualizer import VisualizerWindow
        settings = self.lib.settings
        try:
            import projectm
            import storage
            if storage.seed_visualizer_disabled(
                    settings, projectm.list_presets()):
                self.lib.save_settings()
        except Exception as e:
            self._log_vis(f"shipped disabled list not applied: {e}")
        self._visualizer = VisualizerWindow(
            skin=self._skin,
            scale=int(settings.get("skin_scale", 1)),
            source=settings.get("visualizer_source") or None,
            gain=float(settings.get("visualizer_gain", 1.0)),
            disabled=settings.get("visualizer_disabled") or (),
        )
        self._visualizer.source_changed.connect(
            lambda name: self._save("visualizer_source", name))
        self._visualizer.gain_changed.connect(self._set_vis_gain)
        self._visualizer.disabled_changed.connect(self._vis_disabled)
        self._visualizer.show()
        self._visualizer.raise_()
        self._save("visualizer", True)

    def _log_vis(self, text):
        try:
            import crashlog
            crashlog.write("[vis] " + text)
        except Exception:                       # noqa: BLE001
            pass

    def _vis_disabled(self, paths):
        """The user turned a preset on or off: the list is theirs now, and
        the developer's published list never replaces it (task 142)."""
        self.lib.settings["visualizer_disabled_mine"] = True
        self._save("visualizer_disabled", list(paths))

    def _save(self, key, value):
        self.lib.settings[key] = value
        self.lib.save_settings()

    def _set_vis_gain(self, gain):
        """One sensitivity for both: the analyzer beside the clock reads the
        same audio, so a level set in one place should hold in the other."""
        self._save("visualizer_gain", float(gain))
        if self.player is not None:
            self.player.vis_gain = float(gain)
            self.player.update()

    def _start_tap(self):
        """Feed the skin's analyzer from the output monitor."""
        if self._tap is not None:
            return
        try:
            import audiotap
        except ImportError:
            return

        def deliver(samples):
            # On the tap's thread: never touch a widget here. The player
            # may be swapped (skin change) at any moment on the GUI side.
            self._tap_samples.emit(samples)

        try:
            tap = audiotap.AudioTap(
                deliver, self.lib.settings.get("visualizer_source") or None)
            # start() reports whether the capture command exists at all —
            # a tap that never started must not be kept, or stop() later
            # tries to close a process that was never opened.
            self._tap = tap if tap.start() else None
        except Exception:               # noqa: BLE001
            self._tap = None

    def _feed_player(self, samples):
        window = self.player
        if window is not None:
            window.set_audio(samples)

    def _stop_tap(self):
        if self._tap is not None:
            try:
                self._tap.stop()
            except Exception:           # noqa: BLE001
                pass
            self._tap = None

    # -- handlers ----------------------------------------------------------

    def _play(self):
        station = self.playback.current or self.lib.station_by_id(
            self.lib.settings.get("last_station_id"))
        if station is not None:
            self.playback.play(station)

    def _set_volume(self, value):
        self.win.player.vol.setValue(int(value))

    def _set_shuffle(self, on):
        self.lib.settings["shuffle"] = bool(on)
        self.lib.save_settings()
        self.win.player.shuffle_btn.setChecked(bool(on))

    def _scan(self):
        """Sweeping past a station: noise, and nothing else."""
        if self._scanning:
            return
        self._scanning = True
        if self.lib.settings.get("noise_between_stations", True):
            self.player.noise.start()
        self.player.stop(keep_noise=True)

    def _tune(self, index):
        """The dial scans the visible list, in the order it is shown.

        on_tune reports the position the knob settled on, not a
        direction — treating it as one meant the bar never moved and the
        station never changed, which looked like a missing seek bar.
        """
        self._scanning = False
        rows = self.visible_rows()
        if 0 <= index < len(rows):
            self.playback.play(rows[index]["_source"])

    # -- state -------------------------------------------------------------

    def _title_parts(self):
        station = self.playback.current
        if station is None:
            return ["Frequencies"]
        parts = [storage.station_display_name(station)]
        song = self.win.player.song.text()
        if song and song != "Nothing playing":
            parts.insert(0, song)
        return parts

    def refresh(self):
        if self.player is None:
            return
        parts = self._title_parts()
        if parts != self._titles:
            # Reset whenever what is playing changes, so a new song is
            # announced rather than waiting for the rotation to come round.
            self._titles = parts
            self._title_index = 0
        self.player.set_title(parts[self._title_index % len(parts)])
        self.player.set_playing(self.playback.current is not None)
        self.player.set_favourite(self.win.player.fav_btn.isChecked())
        self.refresh_eq()
        self.refresh_tags()
        self.refresh_dial()
        # 8. Not on every refresh.
        #
        # refresh() runs on the title rotation and on every song, and
        # rebuilding these two means sorting the whole library and reading
        # a thumbnail off disk per history row. That is the stutter after
        # a station change. They are rebuilt when their data changes
        # instead — which is what set_stations was already checking for.
        if self._list_stamp != self._library_stamp():
            self._list_stamp = self._library_stamp()
            self.refresh_playlist()
            self.refresh_history()
        else:
            self._mark_playing()

    def _library_stamp(self):
        """Cheap check for "has the visible list changed".""" 
        rows = self.visible_rows()
        return (len(rows), rows[0]["id"] if rows else "",
                rows[-1]["id"] if rows else "", len(self.lib.history))

    def _mark_playing(self):
        """Move the highlight without rebuilding the rows."""
        if self.playlist is None:
            return
        rows = self.visible_rows()
        playing = (self.playback.current or {}).get("id")
        index = next((i for i, r in enumerate(rows) if r["id"] == playing), -1)
        if index != getattr(self.playlist, "current", -1):
            self.playlist.set_stations([r["name"] for r in rows], index)

    def refresh_dial(self):
        """The position bar, as an FM dial across the visible stations."""
        if self.player is None:
            return
        rows = self.visible_rows()
        playing = (self.playback.current or {}).get("id")
        index = next((i for i, r in enumerate(rows) if r["id"] == playing), -1)
        self.player.set_dial([r["name"] for r in rows], index)

    def _advance_title(self):
        if len(self._titles) > 1:
            self._title_index += 1
        self.refresh()

    def _poll_info(self):
        """mpv doesn't know the bitrate until it has decoded a little, so
        reading once when playback starts always gave zero."""
        if self.player is None:
            return
        try:
            bitrate, samplerate = self.playback.player.stream_info()
        except Exception:               # noqa: BLE001
            return
        self.player.set_stream_info(bitrate, samplerate)

    # -- closing -----------------------------------------------------------

    def _back_to_main(self, page=None):
        self.close()
        if page:
            self.win.sidebar._select(page)
        self.win.showNormal()
        self.win.raise_()

    def _quit(self):
        from PyQt5.QtWidgets import QApplication
        self.close()
        QApplication.instance().quit()

    def _on_dock_closed(self):
        # However the stack went away, the main window has to come back —
        # otherwise the application is running with nothing on screen.
        if self.player is not None:
            self._back_to_main()

    def close(self, back=True):
        self._rotate.stop()
        self._info.stop()
        self._stop_tap()
        window = self.visualizer_window()
        if window is not None:
            window.close()
        self._visualizer = None
        # Retired rather than deleted on the spot: "Show the main window"
        # comes from the player's own menu,
        # with its click still being handled underneath (task 45).
        for panel in (self.history, self.lyrics, self.playlist, self.tags,
                      self.equaliser, self.player):
            retire(panel)
        retire(self.dock)
        self.player = self.equaliser = self.playlist = None
        self.tags = self.history = self.lyrics = self.dock = None
        if back:
            self.closed.emit()
