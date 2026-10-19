"""Frequencies — every web link the app opens (build 79, task 88).

Links in the app's labels (a streamer's "About the streamer from
Twitch", a Wikipedia credit, a link in the Twitch chat) are opened by Qt
itself, through QDesktopServices. A handler for http and https sees
each one first: it logs it and tells the window, then hands the link to
the desktop as before. The window watches the station's sound for a
while after (task 88: the sound stopped when a Twitch channel was opened
in the browser, and nothing in the log said why).

Links opened with Python's `webbrowser` (sign-ins, image searches) don't
pass through here.
"""

from PyQt5.QtCore import QObject, QUrl, pyqtSlot
from PyQt5.QtGui import QDesktopServices

SCHEMES = ("http", "https")


def _log(text):
    try:
        import crashlog
        crashlog.write(text)
    except Exception:                           # noqa: BLE001
        pass


class LinkWatch(QObject):
    """Sees every http(s) link Qt opens. `on_open(url)` runs first."""

    def __init__(self, on_open, parent=None):
        super().__init__(parent)
        self._on_open = on_open
        for scheme in SCHEMES:
            QDesktopServices.setUrlHandler(scheme, self, "open_link")

    @pyqtSlot(QUrl)
    def open_link(self, url):
        text = url.toString()
        shown = text if len(text) <= 80 else text[:80] + "…"
        _log(f"[link] opening {shown} in the browser")
        try:
            self._on_open(text)
        except Exception as exc:                # noqa: BLE001
            _log(f"[link] watching the sound failed: {exc!r}")
        # Asked again from inside the handler, Qt skips the handlers and
        # gives the link to the desktop (its own guard against a loop).
        if not QDesktopServices.openUrl(url):
            _log("[link] the desktop could not open it")

    def uninstall(self):
        for scheme in SCHEMES:
            QDesktopServices.unsetUrlHandler(scheme)
