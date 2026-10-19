"""Frequencies — keeping track of which Twitch channels are live.

A Twitch station is up only while somebody is streaming, which is not a
property of the URL and cannot be discovered by fetching it: an offline
channel still answers, with a page. So it needs asking properly, and it
needs asking again — a channel that was offline when the app started may
be live twenty minutes later, and nothing else will notice.

The check runs on a worker, never on the GUI thread: resolving a channel
means a network round trip through yt-dlp, and doing that for a dozen
stations at launch would freeze the window for the length of all of them.
"""

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from jobs import Job

# Five minutes, as asked. Long enough not to be rude to Twitch, short
# enough that a channel going live is noticed within one song.
INTERVAL_MS = 5 * 60 * 1000

# A gap between channels inside one sweep, so twelve stations are not
# twelve simultaneous requests.
STAGGER_MS = 1500


class _LiveWorker(Job):
    """Ask about one channel, off the GUI thread.

    A jobs.Job (daemon thread), not a QThread: quitting while one was
    still asking Twitch could crash the app (build 54, task 14)."""

    answered = pyqtSignal(str, bool, str)       # station id, live, reason

    def __init__(self, station_id, channel, parent=None):
        super().__init__(parent)
        self.station_id = station_id
        self.channel = channel

    def work(self):
        try:
            import twitch
            url, reason, _headers = twitch.resolve(self.channel, timeout=12)
        except Exception as exc:                # noqa: BLE001
            self.send(self.answered, self.station_id, False, str(exc))
            return
        self.send(self.answered, self.station_id, bool(url), reason)


class TwitchWatch(QObject):
    """Checks every Twitch station at launch, then every five minutes."""

    changed = pyqtSignal()                      # something's state moved

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.lib = library
        self._workers = []
        self._timer = QTimer(self)
        self._timer.setInterval(INTERVAL_MS)
        self._timer.timeout.connect(self.sweep)

    def start(self, delay_ms=4000):
        """Begin. The first sweep waits a few seconds.

        Launch is the busiest moment the app has — the window is being
        built, artwork is being fetched, the library is being read. A
        handful of network calls on top of that is how a launch comes to
        feel slow, and nothing here is urgent.
        """
        self._timer.start()
        QTimer.singleShot(delay_ms, self.sweep)

    def stop(self):
        """On quit. The checks are Jobs: `Job.stop_all` has already
        stopped them answering, and they end with the app."""
        self._timer.stop()
        self._workers.clear()

    def stations(self):
        return [s for s in self.lib.stations
                if s.get("kind") == "twitch" and s.get("twitch_channel")]

    def sweep(self):
        """Ask about each channel, spaced out."""
        for index, station in enumerate(self.stations()):
            QTimer.singleShot(index * STAGGER_MS,
                              lambda s=station: self.check(s))

    def check(self, station):
        worker = _LiveWorker(station.get("id", ""),
                             station.get("twitch_channel", ""), self)
        worker.answered.connect(self._answer)
        worker.finished.connect(lambda w=worker: self._reap(w))
        self._workers.append(worker)
        worker.start()

    def _reap(self, worker):
        # The Job deletes itself once it has answered.
        if worker in self._workers:
            self._workers.remove(worker)

    def _answer(self, station_id, live, reason):
        """Record it, and only tell anyone if it actually changed.

        A station that was down and is still down does not need the list
        rebuilt, and a sweep that reported every time would rebuild it
        every five minutes for nothing.
        """
        import storage

        entry = storage.health_for(self.lib.health, station_id)
        was_down = bool(entry.get("down"))
        if live:
            storage.mark_ok(self.lib.health, station_id)
        else:
            entry["down"] = True
            entry["last_error"] = reason or "not live"
        if was_down != (not live):
            storage.save_health(self.lib.health)
            self.changed.emit()
