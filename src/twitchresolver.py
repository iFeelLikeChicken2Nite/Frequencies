"""Frequencies — looking up a Twitch channel without freezing the window.

Asking Twitch for a stream means a network round trip through yt-dlp, and
it can take several seconds. That used to happen on the GUI thread, which
had two costs: the whole window froze while it waited, and anything the
GUI had scheduled for "in 140 ms" (like silencing the previous station)
ran only AFTER the wait — which meant it also ran after the new stream had
started, and stopped it. The stream connected and was cut off at once.

So the lookup runs here, on its own thread, like the YouTube one does.
The answer comes back through a signal, and the GUI thread does the
playing.

It is a jobs.Job (a daemon thread), not a QThread: quitting while a
QThread lookup was still running crashed the app (build 46).
"""

from PyQt5.QtCore import pyqtSignal

from jobs import Job


class TwitchResolver(Job):
    """Resolve one channel. Emits once, whatever happens."""

    # sequence number, url, reason, headers, channel picture
    done = pyqtSignal(int, str, str, dict, str)

    def __init__(self, seq, channel, parent=None):
        super().__init__(parent)
        self.seq = seq
        self.channel = channel

    def work(self):
        url, reason, headers, art = "", "", {}, ""
        try:
            import twitch
            url, reason, headers = twitch.resolve(self.channel)
            art = twitch.channel_art(self.channel)
        except Exception as exc:                # noqa: BLE001
            url, reason, headers = "", f"lookup failed ({exc})", {}
        self.send(self.done, self.seq, url or "", reason or "",
                  dict(headers or {}), art or "")
