"""Frequencies — is the internet there? (build 151, task 214)

One answer for the whole app. `is_offline()` can be asked from any thread;
`Watch` (owned by mainwindow) keeps it current and says so through
`changed(offline)` on the GUI thread.

How it decides, without flapping:
  - A quick look every few seconds: does the system say it has a network
    (Linux: NetworkManager over D-Bus, when it is there; Windows:
    WinINet's InternetGetConnectedState), and can it open a connection to
    any of a few well-known addresses (TCP to Cloudflare, Google and
    Quad9 DNS on 443/53, then one HTTPS request through the system's
    proxy if those fail)? Each look runs on a jobs.Job, never on the GUI
    thread.
  - The answer only changes after it has held for a while both ways:
    offline after OFF_AFTER seconds of failed looks in a row, online
    after ON_AFTER seconds of good ones. A dropped Wi-Fi for two seconds
    changes nothing.
  - Online, it looks every IDLE_EVERY seconds (cheaper than one stream
    second of audio), and every FAST_EVERY seconds once something seems
    wrong or while offline.

FREQ_OFFLINE=1 forces it offline and FREQ_OFFLINE=0 online (tests).
"""

import os
import socket
import sys
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from jobs import Job

OFF_AFTER = 6           # seconds of failed looks before "offline"
ON_AFTER = 4            # seconds of good looks before "online" again
IDLE_EVERY = 20         # seconds between looks while all is well
FAST_EVERY = 2          # while unsure, or offline

# Anycast DNS resolvers: up nearly everywhere, and a TCP handshake to
# them sends no data anyone has to answer.
PROBES = (("1.1.1.1", 443), ("8.8.8.8", 443), ("9.9.9.9", 443),
          ("1.0.0.1", 53), ("8.8.4.4", 53))
WEB_PROBES = ("https://www.cloudflare.com/cdn-cgi/trace",
              "https://raw.githubusercontent.com/")

_offline = False


def is_offline():
    """True while the app has decided the internet is gone. Any thread."""
    forced = os.environ.get("FREQ_OFFLINE", "")
    if forced in ("0", "1"):
        return forced == "1"
    return _offline


def _log(text):
    try:
        import crashlog
        crashlog.write("[offline] " + text)
    except Exception:                           # noqa: BLE001
        pass


def system_says_online():
    """The system's own opinion: True, False, or None (no opinion).
    Fast; safe on any thread."""
    if sys.platform == "win32":
        try:
            import ctypes
            flags = ctypes.c_ulong(0)
            return bool(ctypes.windll.wininet.InternetGetConnectedState(
                ctypes.byref(flags), 0))
        except Exception:                       # noqa: BLE001
            return None
    if not sys.platform.startswith("linux"):
        return None
    try:
        from PyQt5.QtDBus import QDBusConnection, QDBusInterface
        bus = QDBusConnection.systemBus()
        if not bus.isConnected():
            return None
        nm = QDBusInterface("org.freedesktop.NetworkManager",
                            "/org/freedesktop/NetworkManager",
                            "org.freedesktop.NetworkManager", bus)
        if not nm.isValid():
            return None
        state = nm.property("State")
        if state is None:
            return None
        # NM_STATE: 70 connected global, 60 site, 50 local, 40 connecting,
        # 30 disconnecting, 20 disconnected, 10 asleep, 0 unknown.
        state = int(state)
        if state in (10, 20):
            return False
        if state >= 50:
            return True
        return None
    except Exception:                           # noqa: BLE001
        return None


def reachable(timeout=2.0):
    """Can anything on the internet be reached? Network; not on the GUI
    thread."""
    for host, port in PROBES:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    # A network that only lets traffic out through a proxy.
    try:
        import requests
    except ImportError:
        return False
    for address in WEB_PROBES:
        try:
            answer = requests.head(address, timeout=timeout + 2,
                                   headers={"User-Agent": "Frequencies"})
            if answer.status_code < 500:
                return True
        except Exception:                       # noqa: BLE001
            continue
    return False


class _Look(Job):
    """One look: the system's opinion, then a connection if needed."""

    done = pyqtSignal(bool, str)        # online, how it was decided

    def work(self):
        system = system_says_online()
        if system is False:
            self.send(self.done, False, "the system says it has no network")
            return
        ok = reachable()
        self.send(self.done, ok, "an internet address answered" if ok
                  else "no internet address answered")


class Watch(QObject):
    """Keeps `is_offline()` current; `changed(offline)` when it flips."""

    changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._look)
        self._job = None
        self._streak_since = None       # when looks started disagreeing
        self._why = ""
        self._stopped = False

    def start(self):
        self._timer.start(1500)

    def stop(self):
        self._stopped = True
        self._timer.stop()

    def offline(self):
        return is_offline()

    def _look(self):
        if self._stopped:
            return
        forced = os.environ.get("FREQ_OFFLINE", "")
        if forced in ("0", "1"):
            self._settle(forced == "1", "FREQ_OFFLINE")
            self._timer.start(IDLE_EVERY * 1000)
            return
        if self._job is not None:
            return
        job = _Look()
        job.done.connect(self._looked)
        self._job = job
        job.start()

    def _looked(self, online, why):
        self._job = None
        if self._stopped:
            return
        now = time.monotonic()
        agrees = (not online) == _offline
        if agrees:
            self._streak_since = None
        else:
            if self._streak_since is None:
                self._streak_since = now
                self._why = why
            needed = ON_AFTER if online else OFF_AFTER
            if now - self._streak_since >= needed:
                self._settle(not online, why)
                self._streak_since = None
        unsure = self._streak_since is not None or _offline
        self._timer.start((FAST_EVERY if unsure else IDLE_EVERY) * 1000)

    def _settle(self, offline, why):
        global _offline
        if offline == _offline:
            return
        _offline = offline
        _log(("offline: " if offline else "back online: ") + why)
        self.changed.emit(offline)

    def force(self, offline, why="test"):
        """Set the answer now (tests, and the env override)."""
        self._settle(bool(offline), why)
