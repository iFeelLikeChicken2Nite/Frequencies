"""Frequencies — keeping the iBroadcast sign-in fresh, off the GUI thread.

Every iBroadcast track is fetched with the account's access token in its
URL, and that token runs out. Nothing renewed it: the token saved on the
day a station was added was used for every track after, so the station
played happily until the token expired and then every track was refused —
"couldn't connect", then marked down, in the middle of listening.

TokenKeeper renews it before that happens. Once a minute it looks at the
saved expiry (a file read, nothing more) and, when less than ten minutes
are left, renews it on a thread of its own. Renewing reaches the network,
and a network call on the GUI thread freezes the window — see CLAUDE.md.

The window can also ask for a renewal right now, when a track has just
been refused: `refresh(force=True)`. Either way the answer comes back
through `refreshed`, on the GUI thread.
"""

import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from jobs import Job


def _log(text):
    try:
        import crashlog
        crashlog.write("[ibroadcast] " + text)
    except Exception:                           # noqa: BLE001
        pass


class _Renew(Job):
    """One renewal. Emits once, whatever happens."""

    # new token ("" if none), problem ("" if fine), lifetime in seconds
    done = pyqtSignal(str, str, int)

    # A renewal may be about to save a new sign-in: give it up to 2 s
    # on quit (as the old QThread wait did) rather than cut it off.
    WAIT_ON_QUIT = True

    def __init__(self, force, margin, parent=None):
        super().__init__(parent)
        self.force = force
        self.margin = margin

    def work(self):
        try:
            import ibroadcast
            token, problem, lifetime = ibroadcast.fresh_token(
                force=self.force, margin=self.margin)
        except Exception as exc:                # noqa: BLE001
            token, problem, lifetime = "", f"renewal failed ({exc})", 0
        self.send(self.done, token or "", problem or "", int(lifetime or 0))


class TokenKeeper(QObject):
    """Renews the iBroadcast sign-in ahead of time, and on request."""

    # new token ("" on failure), problem ("" when it worked)
    refreshed = pyqtSignal(str, str)

    CHECK_MS = 60 * 1000        # how often the saved expiry is looked at
    AHEAD = 10 * 60             # renew when less than this is left
    # After a renewal, automatic ones wait at least this long. A server
    # that hands out very short tokens, or a clock that is wrong, must
    # not turn the keeper into a request every minute.
    COOLDOWN = 5 * 60

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._last = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self.CHECK_MS)
        self._timer.timeout.connect(self.check)
        self._timer.start()
        # Soon after launch too: a computer that slept overnight wakes
        # with a token that has already run out.
        QTimer.singleShot(3000, self.check)

    def busy(self):
        return self._worker is not None and self._worker.isRunning()

    def check(self):
        """Renew if the saved token is close to running out."""
        if time.monotonic() - self._last < self.COOLDOWN:
            return
        try:
            import ibroadcast
            due = ibroadcast.needs_refresh(self.AHEAD)
        except Exception:                       # noqa: BLE001
            due = False
        if due:
            self.refresh()

    def refresh(self, force=False):
        """Start a renewal. If one is already running, its answer will do."""
        if self.busy():
            return
        self._last = time.monotonic()
        worker = _Renew(force, self.AHEAD, self)
        worker.done.connect(self._done)
        self._worker = worker
        worker.start()

    def _done(self, token, problem, lifetime):
        if token:
            _log("sign-in renewed" + (f", good for {lifetime // 60} min"
                                      if lifetime else ""))
        else:
            _log(f"sign-in renewal failed: {problem}")
        self.refreshed.emit(token, problem)

    def stop(self):
        # A renewal still running is waited for (briefly) by
        # jobs.Job.stop_all on quit, with every other lookup.
        self._timer.stop()
