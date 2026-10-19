"""Frequencies — slow network calls that can't crash the app on quit.

The lookups (Twitch, YouTube, iBroadcast sign-in renewal, the Discover
lists) used to be QThreads owned by the window. A QThread that is still
running when its owner is destroyed makes Qt abort the whole app:
"QThread: Destroyed while thread is still running". A Twitch lookup can
take 25 seconds, so quitting in the middle of one crashed.

A Job does the same work on a plain daemon thread instead, and answers
through Qt signals as before (Qt delivers them on the GUI thread). A
daemon thread ends with the program, so nothing has to wait for it.

On quit, `Job.stop_all()` shuts the door: no Job sends anything after
that, so a lookup that finishes late can't reach a window that is being
torn down. A sign-in renewal gets up to 2 s to finish (it may be about
to save a new token); every other lookup is simply let go.

A Job cleans up after itself: once its answer has been delivered it is
deleted on the GUI thread, so finished lookups don't pile up.

To write one: subclass, declare signals, put the work in `work()`, and
send answers with `self.send(self.some_signal, ...)`, never `.emit()`.

Build 54 (task 14) moved every other background thread here too: song
ID, lyrics, artwork, artist info, the station health re-check, the Twitch
live check, the artwork scaler. Two extras for those:

  * `stop()` — called on every running Job at quit. Override it to cut
    the work short (the health re-check's loop ends, a song-ID capture
    stops recording). The default does nothing.
  * `LOOPS = True` — a Job that runs for the whole session (the health
    re-check). It is stopped at quit but not counted as "left running"
    in the quit line of the log.
"""

import threading
import time

from PyQt5 import sip
from PyQt5.QtCore import QObject, pyqtSignal


def _log(text):
    try:
        import crashlog
        crashlog.write("[jobs] " + text)
    except Exception:                           # noqa: BLE001
        pass


class Job(QObject):
    """One slow call on a daemon thread. Same shape as a QThread:
    start(), isRunning(), wait(ms) and a `finished` signal."""

    finished = pyqtSignal()

    # Worth waiting for on quit? Only for work whose result must be kept
    # (a renewed sign-in that is about to be saved). A lookup whose
    # answer nobody will see is just left to end with the app.
    WAIT_ON_QUIT = False
    # Runs for the whole session, not one lookup (see the module notes).
    LOOPS = False

    # Every Job that hasn't been cleaned up yet. Holding them here keeps
    # Python from collecting one mid-lookup, whoever started it.
    _live = set()
    # Taken around every send and by stop_all, so "closed" and "sent"
    # can never happen at the same time.
    _lock = threading.Lock()
    _closed = False

    def __init__(self, parent=None):
        # `parent` is accepted, like a QThread's, and ignored on purpose:
        # a parent deletes its children when it goes, and deleting a Job
        # while its thread is still sending is exactly the crash this
        # module exists to avoid. So nothing owns a Job but itself.
        super().__init__()
        self._thread = None
        # Set when work() has returned, just before `finished` goes out,
        # so a `finished` handler asking isRunning() gets False (the
        # thread itself may live a moment longer).
        self._done = False
        # Qt owns it, not Python: otherwise the daemon thread could drop
        # the last Python reference and delete it from the wrong thread.
        # `_clean_up` frees it, on the GUI thread, once it has answered.
        sip.transferto(self, None)
        # Delete on the GUI thread once the answer is in. deleteLater
        # only takes effect back in the event loop, so the other
        # `finished` handlers still see a live object.
        self.finished.connect(self._clean_up)

    # -- the QThread-like part ---------------------------------------------

    def start(self):
        with Job._lock:
            if Job._closed:
                return              # quitting: nothing new starts
            Job._live.add(self)
        name = type(self).__name__
        self._thread = threading.Thread(target=self._main, name=name,
                                        daemon=True)
        self._thread.start()

    def isRunning(self):
        return (self._thread is not None and not self._done
                and self._thread.is_alive())

    def wait(self, ms=None):
        """Wait for the work to end. True if it has."""
        if self._thread is None:
            return True
        self._thread.join(None if ms is None else ms / 1000.0)
        return not self._thread.is_alive()

    # -- for subclasses ----------------------------------------------------

    def work(self):
        raise NotImplementedError

    def stop(self):
        """Asked to end early (at quit). Runs on the GUI thread; must
        return at once. Override to cut long work short."""

    def send(self, signal, *args):
        """Emit `signal`, unless the app is quitting. True if sent."""
        with Job._lock:
            if Job._closed:
                return False
            try:
                signal.emit(*args)
            except RuntimeError:
                # The Qt side is already gone; nobody left to tell.
                return False
        return True

    # -- inside ------------------------------------------------------------

    def _main(self):
        try:
            self.work()
        except Exception as exc:                # noqa: BLE001
            _log(f"{type(self).__name__} failed: {exc!r}")
        finally:
            self._done = True
            self.send(self.finished)

    def _clean_up(self):
        """Runs on the GUI thread, after the answer was handled."""
        Job._live.discard(self)
        try:
            self.deleteLater()
        except RuntimeError:
            pass

    # -- quitting ----------------------------------------------------------

    @classmethod
    def stop_all(cls, wait_ms=2000):
        """On quit. Nothing is sent after this. Waits up to `wait_ms` in
        total for running Jobs marked WAIT_ON_QUIT, then leaves the rest
        to end with the program. Returns how many were still running."""
        with Job._lock:
            Job._closed = True
            running = [j for j in Job._live if j.isRunning()]
        for job in running:
            try:
                job.stop()
            except Exception as exc:            # noqa: BLE001
                _log(f"{type(job).__name__}.stop failed: {exc!r}")
        deadline = time.monotonic() + wait_ms / 1000.0
        for job in running:
            if job.WAIT_ON_QUIT:
                job._thread.join(max(0.0, deadline - time.monotonic()))
        # Session-long loops are stopped above and end on their own; they
        # are not lookups anyone is waiting on.
        running = [j for j in running if not j.LOOPS]
        left = [j for j in running if j.isRunning()]
        if running:
            _log(f"quit: {len(running) - len(left)} lookup(s) finished, "
                 f"{len(left)} left to end with the app"
                 + (" (" + ", ".join(type(j).__name__ for j in left) + ")"
                    if left else ""))
        return len(left)
