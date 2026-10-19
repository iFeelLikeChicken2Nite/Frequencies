"""Frequencies — desktop integration for the new window.

Four things main.py attached to the old MainWindow, repointed at
`FrequenciesWindow` and its `Playback`:

    MPRIS         media keys, and the desktop's now-playing popup
    Tray icon     optional minimise/close to tray
    HealthMonitor background re-checks of stations flagged down
    Sleep timer   fade out and stop after N minutes

All four are optional. A missing session bus, a desktop with no tray, a
disabled setting — each degrades to nothing rather than failing.

    from desktop import Desktop
    integration = Desktop(win, library, engine)
    integration.start()
"""

import sys

import crashlog
import healthlog
import storage
import workers

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QMenu, QSystemTrayIcon


class Desktop(QObject):
    def __init__(self, window, library, player, icon=None, parent=None):
        super().__init__(parent or window)
        self.win = window
        self.lib = library
        # Accept the Playback or the Player itself: both are handed around
        # and only one of them can change the volume.
        self.player = getattr(player, "player", player)
        self.icon = icon or QIcon()
        self.mpris = None
        self.tray = None
        self.monitor = None

        self._sleep_timer = QTimer(self)
        self._sleep_timer.setSingleShot(True)
        self._sleep_timer.timeout.connect(self._sleep_now)

    def start(self):
        # Each on its own: one failing must not stop the rest (the launch
        # health check is last and was the one that went missing, task 147).
        for step in (self._start_mpris, self._start_tray,
                     self._start_health, self._start_launch_check):
            try:
                step()
            except Exception as exc:            # noqa: BLE001
                crashlog.write(f"[desktop] {step.__name__} failed: {exc!r}")

    def reconfigure(self):
        """Options changed something that was decided at startup."""
        if self.monitor is not None:
            self.monitor.set_enabled(
                bool(self.lib.settings.get("health_recheck")))
            self.monitor.set_interval(
                int(self.lib.settings.get("health_recheck_minutes", 30)) * 60)
        elif self.lib.settings.get("health_recheck"):
            self._start_health()

        wants_tray = bool(self.lib.settings.get("tray_icon"))
        if wants_tray and self.tray is None:
            self._start_tray()
        elif not wants_tray and self.tray is not None:
            self.tray.hide()
            self.tray = None

    def shutdown(self):
        """Safe to call more than once."""
        if self.mpris is not None:
            self.mpris.unregister()
            self.mpris = None
        if self.monitor is not None:
            # A jobs.Job: its loop ends on its own, nothing to wait for
            # (a probe can take seconds to time out) (build 54).
            self.monitor.stop()
            self.monitor = None

    # -- MPRIS -------------------------------------------------------------

    def _start_mpris(self):
        if sys.platform == "win32":
            # Windows' media controls in place of MPRIS (WP3).
            import smtc
            bridge = smtc.SmtcBridge(self)
        else:
            import mpris
            bridge = mpris.MprisBridge(self)
        if not bridge.register():
            return                      # no session bus; media keys just won't
        self.mpris = bridge
        pb = self.win.playback

        bridge.play_pause_requested.connect(self._play_pause)
        bridge.play_requested.connect(lambda: self.win._on_play_toggled(True))
        # Pause and Stop go the way the play button does, so the bar
        # settles as paused (task 88), not only the sound.
        bridge.stop_requested.connect(
            lambda: self.win._on_play_toggled(False))
        bridge.next_requested.connect(pb.next)
        bridge.previous_requested.connect(pb.previous)
        bridge.raise_requested.connect(self._raise)
        bridge.quit_requested.connect(QApplication.instance().quit)
        bridge.volume_change_requested.connect(self.win._on_volume)
        bridge.update_volume(self.lib.settings.get("volume", 80))

    def _play_pause(self):
        # The play button's state, not playback.current: stopping keeps
        # the station current (it is what play starts again), so after a
        # pause this read "playing" and the key paused again for ever.
        self.win._on_play_toggled(not self.win.player._playing)

    def _raise(self):
        """Wayland has no activation protocol Qt can use here, so
        activateWindow() only produces a warning. Showing and raising is
        what actually works; the compositor decides the rest."""
        from PyQt5.QtGui import QGuiApplication
        # Hidden in the tray (task 82): show() brings it back as it was,
        # maximised or not. showNormal() would un-maximise it.
        if self.win.isMinimized():
            self.win.showNormal()
        else:
            self.win.show()
        self.win.raise_()
        if not QGuiApplication.platformName().startswith("wayland"):
            self.win.activateWindow()

    def push_mpris(self):
        """Call after a station change or an identified song."""
        if self.mpris is None:
            return
        station = self.win.playback.current
        if station is None:
            self.mpris.update_status("Stopped")
            self.mpris.update_now_playing()
            return
        track = getattr(self.win.feeds, "_track", None) or {}
        name = storage.station_display_name(station)
        # The play button says whether it plays: a paused station is
        # still current (task 88).
        self.mpris.update_status(
            "Playing" if self.win.player._playing else "Stopped")
        # No song art on a Twitch station (task 89).
        art = ("" if storage.is_twitch_station(station)
               else track.get("cover_url", ""))
        self.mpris.update_now_playing(
            station_name=name,
            title=track.get("title") or name,
            artist=track.get("artist", ""),
            art_url=art,
        )

    # -- tray --------------------------------------------------------------

    def _start_tray(self):
        if not self.lib.settings.get("tray_icon"):
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self.icon.isNull():
            # An empty QIcon means an invisible tray entry — worse than no
            # tray at all, and Qt warns about it.
            return
        tray = QSystemTrayIcon(self.icon, self)
        menu = QMenu()
        menu.addAction("Show", self._raise)
        menu.addAction("Play / Stop", self._play_pause)
        menu.addAction("Next", self.win.playback.next)
        menu.addSeparator()
        menu.addAction("Quit", QApplication.instance().quit)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self._raise()
            if reason == QSystemTrayIcon.Trigger else None)
        tray.setToolTip("Frequencies")
        tray.show()
        self.tray = tray

    def toast(self, title, text):
        """A desktop notification, through the tray icon.

        Qt routes this to whatever the desktop actually uses, so there is
        no dependency on a notification library — and if there is no tray,
        there is quietly no notification, which is the right outcome for
        something optional.
        """
        if self.tray is None:
            return
        try:
            self.tray.showMessage(title, text, self.icon, 6000)
        except Exception:                       # noqa: BLE001
            pass

    def update_tray_label(self):
        if self.tray is None:
            return
        station = self.win.playback.current
        self.tray.setToolTip(
            storage.station_display_name(station) if station
            else "Frequencies")

    # -- health ------------------------------------------------------------

    def _start_health(self):
        if not self.lib.settings.get("health_recheck"):
            return
        interval = int(self.lib.settings.get("health_recheck_minutes", 30)) * 60
        monitor = workers.HealthMonitor(interval=interval, parent=self)
        monitor.result.connect(self._health_result)
        monitor.set_targets(self._down_targets())
        monitor.start()
        self.monitor = monitor

    def _start_launch_check(self):
        """Always on, no setting (task 140): every launch probes every
        saved stream in the background, a few seconds after start."""
        playback = getattr(getattr(self, "win", None), "playback", None)
        checkable = getattr(playback, "checkable", None)
        check_url = getattr(playback, "check_url", None)
        targets = []
        for station in self.lib.stations:
            if storage.is_local_station(station) \
                    or storage.is_youtube_station(station):
                continue
            if checkable is not None and not checkable(station):
                continue
            url = (check_url(station) if check_url is not None
                   else station.get("url", ""))
            if url.startswith("http"):
                targets.append((station["id"], url))
        if not targets:
            crashlog.write("[health] launch check: no saved streams to check")
            return
        self._launch_reload = QTimer(self)
        self._launch_reload.setSingleShot(True)
        self._launch_reload.timeout.connect(self._launch_redraw)
        job = workers.LaunchCheck(targets)
        job.result.connect(self._launch_result)
        job.finished.connect(self._launch_redraw)
        job.start()

    def _launch_result(self, station_id, ok, reason):
        station = self.lib.station_by_id(station_id) or {}
        healthlog.note(storage.station_display_name(station), ok, reason)
        if ok:
            storage.mark_ok(self.lib.health, station_id)
        else:
            storage.mark_failed(self.lib.health, station_id, reason)
        # Redraw the rows a second after the last result, not on each.
        self._launch_reload.start(1000)

    def _launch_redraw(self):
        storage.save_health(self.lib.health)
        self.refresh_health_targets()
        self.win.reload()

    def _down_targets(self):
        out = []
        for station in self.lib.stations:
            # Skip what cannot pass for reasons of its own.
            #
            # A signed-out iBroadcast station fails every time, which
            # would keep it marked down for the wrong reason and make the
            # recheck pointless traffic.
            playback = getattr(getattr(self, "win", None), "playback", None)
            checkable = getattr(playback, "checkable", None)
            if checkable is not None and not checkable(station):
                continue
            entry = storage.health_for(self.lib.health, station.get("id"))
            if entry and entry.get("down"):
                # Through Playback, so an iBroadcast station is checked
                # with today's sign-in rather than the one it was saved
                # with — which may be the very reason it went down.
                check_url = getattr(playback, "check_url", None)
                url = (check_url(station) if check_url is not None
                       else station.get("url", ""))
                out.append((station["id"], url))
        return out

    def _health_result(self, station_id, ok, reason):
        # Note it before acting on it, so the window shows the check
        # itself rather than only its consequences.
        station = self.lib.station_by_id(station_id) or {}
        healthlog.note(storage.station_display_name(station), ok, reason)

        if ok:
            storage.mark_ok(self.lib.health, station_id)
        else:
            storage.mark_failed(self.lib.health, station_id, reason)
        storage.save_health(self.lib.health)
        if self.monitor is not None:
            self.monitor.set_targets(self._down_targets())
        # The subtitle line carries the down state, so the rows need redrawing.
        self.win.reload()

    def refresh_health_targets(self):
        if self.monitor is not None:
            self.monitor.set_targets(self._down_targets())

    # -- sleep timer -------------------------------------------------------

    def start_sleep_timer(self, minutes=None):
        minutes = int(minutes or self.lib.settings.get("sleep_minutes", 30))
        self._sleep_timer.start(max(1, minutes) * 60 * 1000)

    def cancel_sleep_timer(self):
        self._sleep_timer.stop()

    @property
    def sleep_active(self):
        return self._sleep_timer.isActive()

    def _station_for_tag(self, tag):
        """Any working station carrying this tag, chosen at random.

        Random so the same tag does not always mean the same station, and
        skipping ones currently marked down — falling asleep to a station
        that will not connect is waking up to silence.
        """
        import random

        tag = (tag or "").strip()
        if not tag:
            return None
        playing = (getattr(getattr(self, "win", None), "playback", None))
        current = getattr(playing, "current", None) or {}
        candidates = []
        for station in self.lib.stations:
            if tag not in (station.get("tags") or []):
                continue
            entry = storage.health_for(self.lib.health, station.get("id"))
            if (entry and entry.get("down")
                    and storage.skipped_when_down(station)):
                continue
            candidates.append(station)
        # Not the one already playing, if there is any alternative.
        others = [s for s in candidates if s.get("id") != current.get("id")]
        pool = others or candidates
        return random.choice(pool) if pool else None

    def _sleep_now(self):
        """Fade out, or hand over to something quieter.

        A sleep timer that stops dead wakes people, so the default fades.
        But silence is not the only useful ending: switching to a station
        chosen for the small hours means the radio is still on when you
        surface, which is what a clock radio always did.
        """
        action = self.lib.settings.get("sleep_action")
        wake = self.lib.settings.get("sleep_wake_station_id") or ""
        if action == "switch" and wake:
            station = self.lib.station_by_id(wake)
            if station is not None:
                self._fade_into(station)
                return

        if action == "tag":
            station = self._station_for_tag(
                self.lib.settings.get("sleep_wake_tag") or "")
            if station is not None:
                self._fade_into(station)
                return
            # No station carries it any more: fading out is the safe
            # ending, rather than leaving the radio on at full level.
        seconds = int(self.lib.settings.get("sleep_fade_seconds", 20))
        steps = max(1, seconds * 4)
        start = int(self.lib.settings.get("volume", 80))

        state = {"i": 0}
        fade = QTimer(self)

        def step():
            state["i"] += 1
            level = int(start * (1 - state["i"] / steps))
            self.player.set_volume(max(0, level))
            if state["i"] >= steps:
                fade.stop()
                self.win.playback.stop()
                self.player.set_volume(start)   # ready for next time

        fade.timeout.connect(step)
        fade.start(250)
        self._fade_timer = fade

    def _fade_into(self, station):
        """Down on the old station, over to the new one, back up.

        Cutting straight across at full volume would be louder than what
        you fell asleep to, which defeats the point.
        """
        seconds = max(4, int(self.lib.settings.get("sleep_fade_seconds", 20)))
        steps = max(1, seconds * 4)
        start = int(self.lib.settings.get("volume", 80))
        state = {"i": 0, "switched": False}
        fade = QTimer(self)

        def step():
            state["i"] += 1
            half = steps // 2
            if state["i"] <= half:
                self.player.set_volume(
                    max(0, int(start * (1 - state["i"] / half))))
                return
            if not state["switched"]:
                state["switched"] = True
                self.win.playback.play(station)
                self.win.notify(
                    f"Sleep timer: switched to "
                    f"{storage.station_display_name(station)}.")
                return
            climb = (state["i"] - half) / max(1, steps - half)
            self.player.set_volume(min(start, int(start * climb)))
            if state["i"] >= steps:
                fade.stop()
                self.player.set_volume(start)

        fade.timeout.connect(step)
        fade.start(250)
        self._fade_timer = fade
