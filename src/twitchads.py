"""Frequencies — what a live Twitch stream is sending while it tunes in.

Debug only (build 64, task 38). A live Twitch channel played static,
silence, static, silence before the stream really started. The guess is
Twitch's pre-roll ad: a player like this one gets the ad's segments
(often silent) at the start of the stream. mpv doesn't say which
segments are ads, but the stream's playlist does, so this reads the
same playlist mpv is playing, every few seconds for the first minute and
a half, and writes one log line each time what it sees changes:

    [twitch] playlist 12.0s: 6 segments, 4 ad (twitch-stitched-ad), last 'Amazon|...'

It never changes what is played. It stops early when another station
starts (the `cancel` event, set by bridge.Playback).
"""

import re
import threading
import time

from jobs import Job

WATCH_SECONDS = 90
EVERY = 3.0

_AD_MARKS = ("stitched-ad", "twitch-ad", "x-tv-twitch-ad", "amazon")


def _log(text):
    try:
        import crashlog
        crashlog.write(text)
    except Exception:                           # noqa: BLE001
        pass


def summarize(text):
    """(summary, variant_url) for one playlist's text.

    A master playlist (a list of qualities) gives back the address of
    its audio-only (or first) variant to read instead.
    """
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
        variant, audio = None, None
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
                url = lines[i + 1]
                variant = variant or url
                if "audio" in line.lower() or "audio_only" in url:
                    audio = audio or url
        return "master playlist", audio or variant
    segments, ads, titles = 0, 0, []
    marks = set()
    next_is_ad = False
    for line in lines:
        low = line.lower()
        if line.startswith("#EXT-X-DATERANGE") and any(
                m in low for m in _AD_MARKS):
            found = re.search(r'CLASS="([^"]+)"', line)
            marks.add(found.group(1) if found else "daterange ad")
        if line.startswith("#EXTINF"):
            title = line.split(",", 1)[1] if "," in line else ""
            titles.append(title)
            next_is_ad = bool(title) and title.lower() != "live"
        elif not line.startswith("#"):
            segments += 1
            if next_is_ad:
                ads += 1
            next_is_ad = False
    summary = f"{segments} segments, {ads} ad"
    if marks:
        summary += f" ({', '.join(sorted(marks))})"
    if titles:
        summary += f", last '{titles[-1][:40]}'"
    if any("discontinuity" in l.lower() for l in lines):
        summary += ", has a break (discontinuity)"
    return summary, None


class TwitchAdWatch(Job):
    """Reads the playlist mpv is playing and logs what is in it."""

    def __init__(self, url, headers, cancel, seconds=WATCH_SECONDS,
                 why=""):
        super().__init__()
        self.url = url
        self.headers = dict(headers or {})
        self.cancel = cancel or threading.Event()
        # Build 79 (task 88): also run for a while after the channel was
        # opened in the browser; `why` goes in front of each line.
        self.seconds = seconds
        self.why = why

    def stop(self):
        self.cancel.set()

    def work(self):
        try:
            import requests
        except Exception:                       # noqa: BLE001
            return
        began = time.monotonic()
        url = self.url
        last = None
        hops = 0
        while not self.cancel.is_set():
            took = time.monotonic() - began
            if took > self.seconds:
                _log(f"[twitch] playlist watch done"
                     f"{f' ({self.why})' if self.why else ''}")
                return
            try:
                reply = requests.get(url, headers=self.headers, timeout=5)
                summary, variant = summarize(reply.text)
                if variant and hops < 2:
                    hops += 1
                    from urllib.parse import urljoin
                    url = urljoin(url, variant)
                    _log(f"[twitch] playlist: master, reading the "
                         f"variant {url[:60]}…")
                    continue
                if reply.status_code != 200:
                    summary = f"HTTP {reply.status_code}"
            except Exception as exc:            # noqa: BLE001
                summary = f"read failed ({type(exc).__name__})"
            if summary != last:
                when = (f"{took:4.1f}s after {self.why}" if self.why
                        else f"{took:4.1f}s")
                _log(f"[twitch] playlist {when}: {summary}")
                last = summary
            self.cancel.wait(EVERY)
