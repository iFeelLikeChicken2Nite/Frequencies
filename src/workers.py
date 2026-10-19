"""
workers.py - Background threads.

Anything that touches the network runs off the GUI thread and reports
back through Qt signals, which are delivered on the GUI thread. Nothing
in here touches a widget directly.

  HealthMonitor    periodically re-checks stations that are marked down,
                   so a station that comes back gets un-flagged without
                   the user doing anything
  StreamCheck      one-shot reachability probe
  ShazamWorker     capture a few seconds of the live stream, fingerprint
                   it, look it up
  ArtworkWorker    download a cover image
  Lookup           run one slow call (Twitch, YouTube, iBroadcast) while
                   a station is being added, and hand the answer back
  YouTubeResolver  pick and resolve a YouTube track

Every one of them is a jobs.Job (a daemon thread), so quitting while
one runs can't crash the app (Lookup and YouTubeResolver since build 46,
the rest since build 54). Answers go out with `self.send(...)`, which
stays quiet once the app is quitting.
"""
import os
import time
import threading
import storage

import requests
from PyQt5.QtCore import pyqtSignal

from jobs import Job

import player
import artistinfo
import shazam

_log = player._log

USER_AGENT = "Frequencies/1.5"

# How much of a stream we pull before calling it reachable. We only need
# to know that bytes are flowing.
PROBE_BYTES = 2048
# (connect, read). Kept short so a stalled server can't hold the
# background thread open long enough to matter at shutdown - a dead
# Icecast mount often accepts the connection and then says nothing.
PROBE_TIMEOUT = (3, 5)


def probe_stream(url, timeout=PROBE_TIMEOUT):
    """Try to actually receive audio bytes from a stream URL.

    Returns (ok, reason). A HEAD request isn't enough - plenty of
    Icecast/Shoutcast servers answer HEAD fine while the mount point is
    dead, so we open the stream and wait for real bytes.
    """
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Icy-MetaData": "1"},
            timeout=timeout,
            stream=True,
        )
    except requests.RequestException as exc:
        return False, type(exc).__name__

    try:
        if response.status_code >= 400:
            return False, f"HTTP {response.status_code}"
        for chunk in response.iter_content(chunk_size=PROBE_BYTES):
            if chunk:
                return True, ""
        return False, "no audio data"
    except requests.RequestException as exc:
        return False, type(exc).__name__
    finally:
        response.close()


class StreamCheck(Job):
    """One-shot probe of a single station."""

    finished_check = pyqtSignal(str, bool, str)  # station_id, ok, reason

    def __init__(self, station_id, url, parent=None):
        super().__init__(parent)
        self.station_id = station_id
        self.url = url

    def work(self):
        ok, reason = probe_stream(self.url)
        self.send(self.finished_check, self.station_id, ok, reason)


class HealthMonitor(Job):
    """Re-checks stations that are currently flagged as down.

    Runs forever on its own thread, waking up every `interval` seconds.
    Only stations already marked down get re-probed, so this stays quiet
    - a healthy library generates no network traffic at all.

    A jobs.Job that loops for the whole session (LOOPS): at quit
    `Job.stop_all` calls stop(), and the daemon thread ends on its own —
    nothing waits for a probe that is still connecting (build 54).
    """

    LOOPS = True
    result = pyqtSignal(str, bool, str)  # station_id, ok, reason

    def __init__(self, interval=300, parent=None):
        super().__init__(parent)
        self.interval = interval
        self._targets = []          # list of (station_id, url)
        self._stopping = False
        self._probing = False
        self._enabled = True

    def set_targets(self, targets):
        """Replace the watch list. Called from the GUI thread; assigning
        a new list is atomic enough for our purposes."""
        self._targets = list(targets)

    def set_interval(self, seconds):
        """Change how often the sweep runs, live."""
        self.interval = max(30, int(seconds))

    def set_enabled(self, enabled):
        """Pause or resume without tearing the thread down.

        Stopping a thread and starting a new one on a settings toggle
        invites races; an idle flag is simpler and the thread costs
        nothing while it sleeps.
        """
        self._enabled = bool(enabled)

    def stop(self):
        self._stopping = True

    @property
    def probing(self):
        return self._probing

    def work(self):
        # Don't hammer anything the moment the app opens.
        waited = 0
        while not self._stopping:
            if not getattr(self, "_enabled", True):
                waited = 0
                time.sleep(0.5)
                continue
            if waited >= self.interval:
                waited = 0
                for station_id, url in list(self._targets):
                    if self._stopping:
                        return
                    self._probing = True
                    try:
                        ok, reason = probe_stream(url)
                    finally:
                        self._probing = False
                    if self._stopping:
                        return
                    self.send(self.result, station_id, ok, reason)
            # Sleep in short slices so stop() is noticed promptly rather
            # than up to a second later.
            for _ in range(10):
                if self._stopping:
                    return
                time.sleep(0.1)
            waited += 1


class LaunchCheck(Job):
    """One sweep over every saved stream at start-up (build 100, task 140).

    Four probes at a time, one result per station. If the first eight all
    fail the machine is most likely offline, so nothing is reported (it
    would turn every row red for no reason of the stations' own).
    """

    result = pyqtSignal(str, bool, str)  # station_id, ok, reason
    OFFLINE_AFTER = 8

    def __init__(self, targets, delay=8.0, parent=None):
        super().__init__(parent)
        self._targets = list(targets)
        self._delay = delay
        self._stopping = False

    def stop(self):
        self._stopping = True

    def work(self):
        from concurrent.futures import ThreadPoolExecutor
        # Let the window settle and the first station start first.
        waited = 0.0
        while waited < self._delay:
            if self._stopping:
                return
            time.sleep(0.2)
            waited += 0.2
        held = []                   # results kept back until one succeeds
        seen_ok = False
        fails = 0
        tally = {True: 0, False: 0}
        _log(f"[health] launch check: started, {len(self._targets)} saved "
             f"stream(s) to check")

        def probe(item):
            if self._stopping:
                return item[0], None, ""
            try:
                ok, reason = probe_stream(item[1])
            except Exception as exc:            # noqa: BLE001
                # One odd answer must not end the whole sweep.
                ok, reason = False, type(exc).__name__
            return item[0], ok, reason

        with ThreadPoolExecutor(max_workers=4) as pool:
            for station_id, ok, reason in pool.map(probe, self._targets):
                if self._stopping or ok is None:
                    _log("[health] launch check: stopped (quitting)")
                    return
                tally[bool(ok)] += 1
                if not seen_ok:
                    if ok:
                        seen_ok = True
                        for item in held:
                            self.send(self.result, *item)
                        held = []
                        self.send(self.result, station_id, ok, reason)
                        continue
                    fails += 1
                    held.append((station_id, ok, reason))
                    if fails >= self.OFFLINE_AFTER and len(held) >= fails:
                        # Still nothing working: assume offline, stop.
                        self._stopping = True
                        _log(f"[health] launch check: ended, the first "
                             f"{fails} streams all failed - looks offline, "
                             f"nothing marked")
                        return
                    continue
                self.send(self.result, station_id, ok, reason)
        _log(f"[health] launch check: ended, {tally[True] + tally[False]} "
             f"checked, {tally[True]} answered, {tally[False]} no answer")


class ShazamWorker(Job):
    """Capture -> fingerprint -> look up, for one station."""

    # station_id, track. The track dict carries "captured_at": the wall
    # clock when the six-second sample STARTED, which is the moment
    # Shazam's match offset actually refers to.
    identified = pyqtSignal(str, dict)
    no_match = pyqtSignal(str)           # station_id
    failed = pyqtSignal(str, str)        # station_id, message

    # Quitting stops the recording (stop() below); this is how long the
    # app waits for it to wind down, within Job.stop_all's 2 s.
    WAIT_ON_QUIT = True

    def __init__(self, station_id, url, seconds=6, parent=None,
                 start_seconds=None):
        super().__init__(parent)
        # Lets stop() end the recording early. A recording mpv still
        # running when the app exits is not something to leave to luck.
        self._cancel = player.CaptureCancel()
        self.station_id = station_id
        self.url = url
        self.seconds = seconds
        # Where in the source to capture. None for live streams, which
        # have no position; a custom station passes its current one so
        # the fingerprint matches what is actually being heard rather
        # than the start of the track.
        self.start_seconds = start_seconds

    def stop(self):
        """At quit: stop recording now (the 6 s capture would outlast
        the wait), and skip the lookup."""
        self._cancel.cancel()

    def work(self):
        wav_path = None
        try:
            started = time.time()
            wav_path = player.capture_stream_to_wav(
                self.url, seconds=self.seconds,
                start_seconds=self.start_seconds,
                cancel=self._cancel,
            )
            if self._cancel.cancelled:
                return                  # quitting: no lookup
            # When the captured audio actually began, worked back from the
            # end.
            #
            # Stamping the time before the call looked right but included
            # however long it took to open a second connection to the
            # station — a fresh TCP and HTTP round trip, anywhere from a
            # fraction of a second to several. That varies per song, which
            # is why a lyric offset that fitted one track was wrong on the
            # next. The end of the capture is a fixed distance from the
            # start of the audio: exactly the number of seconds recorded.
            # Both ends of the sample, so the anchor can be chosen later.
            #
            # Shazam's offset aligns the sample to the track, but which
            # end of the sample it refers to is not documented anywhere
            # authoritative — and the difference is the whole capture
            # length, six seconds, which is a very visible lyric error.
            # Recording both lets it be a setting rather than a guess.
            capture_ended = time.time()
            captured_at = max(started, capture_ended - self.seconds)
            track = shazam.identify(wav_path)
        except shazam.ShazamError as exc:
            self.send(self.failed, self.station_id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.send(self.failed, self.station_id, str(exc))
            return
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

        if track:
            # Stamp when the sample was taken, not when the answer
            # arrived: Shazam's match offset describes a point in the
            # audio we captured, and by the time the lookup returns
            # several seconds of song have already gone past.
            track["captured_at"] = captured_at
            track["capture_ended"] = capture_ended
            self.send(self.identified, self.station_id, track)
        else:
            self.send(self.no_match, self.station_id)


class ArtworkWorker(Job):
    """Download an image (station favicon or Shazam cover art)."""

    loaded = pyqtSignal(str, str, bytes)  # slot_name, url, image bytes
    failed = pyqtSignal(str, str)         # slot_name, url

    MAX_BYTES = 4 * 1024 * 1024

    def __init__(self, slot, url, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.url = url

    TOTAL_SECONDS = 15

    def work(self):
        """Fetch the image, with a limit on the whole thing.

        timeout=10 bounds each socket read, not the download — a server
        that sends a chunked response slowly can hold the connection open
        far longer than that, which is how two of these were still reading
        when the app was closed and took it down with a Qt fatal.

        Streamed with a deadline and a byte cap, so the worker always
        finishes: either with an image, or without one.
        """
        import time as _time

        deadline = _time.monotonic() + self.TOTAL_SECONDS
        try:
            response = requests.get(
                self.url, headers={"User-Agent": USER_AGENT},
                timeout=(5, 10), stream=True)
            response.raise_for_status()
            chunks = []
            total = 0
            for chunk in response.iter_content(64 * 1024):
                chunks.append(chunk)
                total += len(chunk)
                if total > self.MAX_BYTES or _time.monotonic() > deadline:
                    break
            response.close()
            data = b"".join(chunks)
        except requests.RequestException:
            self.send(self.failed, self.slot, self.url)
            return
        if not data or len(data) > self.MAX_BYTES:
            self.send(self.failed, self.slot, self.url)
            return
        self.send(self.loaded, self.slot, self.url, data)


class LyricsWorker(Job):
    """Look up lyrics for an identified track."""

    loaded = pyqtSignal(str, str, dict)   # station_id, track_key, lyrics
    none_found = pyqtSignal(str, str)     # station_id, track_key
    failed = pyqtSignal(str, str, str)    # station_id, track_key, message

    def __init__(self, station_id, track_key, artist, title, parent=None,
                 duration=0):
        super().__init__(parent)
        self.station_id = station_id
        self.track_key = track_key
        self.artist = artist
        self.title = title
        # Only used to pin the right recording when it is known; a radio
        # stream rarely knows how long the song is.
        self.duration = duration

    def work(self):
        import lyrics as lyrics_mod

        # Timed lyrics first, plain as the fallback.
        #
        # LRCLIB answers with both when it has them, so one lookup can
        # serve either — and a track it does not know falls through to
        # the existing source rather than showing nothing.
        timed = {}
        try:
            import lrclib
            timed = lrclib.fetch(self.artist, self.title,
                                 duration=self.duration)
        except Exception:                       # noqa: BLE001
            timed = {}

        if timed.get("synced"):
            self.send(self.loaded, self.station_id, self.track_key, {
                "synced": timed["synced"],
                "lines": [text for _at, text in timed["synced"]],
                "text": timed.get("plain", ""),
                "source": timed.get("source", "LRCLIB"),
            })
            return

        # Untimed lyrics from lrcmux still beat the older source.
        #
        # Only timed results were kept here, so a song lrcmux had as plain
        # text was thrown away and looked up again somewhere else — the
        # preferred source losing to the fallback over a missing clock.
        if timed.get("plain"):
            self.send(self.loaded, self.station_id, self.track_key, {
                "synced": [],
                "plain": timed["plain"],
                "lines": timed["plain"].splitlines(),
                "text": timed["plain"],
                "source": timed.get("source", "LRCLIB"),
            })
            return

        try:
            result = lyrics_mod.fetch(self.artist, self.title)
        except lyrics_mod.LyricsError as exc:
            self.send(self.failed, self.station_id, self.track_key, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            # Anything lyrics.py didn't already phrase for a human.
            # Still keep it short: the panel is two lines of text, not a
            # place for a stack trace.
            self.send(
                self.failed, self.station_id, self.track_key,
                f"Couldn't fetch lyrics ({type(exc).__name__}).",
            )
            return
        if result:
            self.send(self.loaded, self.station_id, self.track_key, result)
        else:
            self.send(self.none_found, self.station_id, self.track_key)


class Lookup(Job):
    """One slow call, off the GUI thread, answered on the GUI thread.

    Used by Add Frequency…: asking Twitch whether a channel is live,
    reading a YouTube playlist's name, or reading an iBroadcast library
    all go over the network and can take seconds. Done inline, the whole
    window froze while they ran.

    A jobs.Job: a daemon thread, not a QThread, so quitting mid-lookup
    can't crash the app. The answer comes back through `done`, which Qt
    delivers on the GUI thread.
    """

    # result (None on error), error (an exception, or None)
    done = pyqtSignal(object, object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def work(self):
        result, error = None, None
        try:
            result = self._fn()
        except Exception as exc:                # noqa: BLE001
            error = exc
        self.send(self.done, result, error)


class YouTubeResolver(Job):
    """Turn a YouTube station into a playable URL, off the GUI thread.

    Two network round-trips - listing the playlist (usually cached) and
    resolving one entry - either of which can take seconds. Doing that
    inline would freeze the window at exactly the moment the user
    expects the station to change, so the static covers it while this
    runs.
    """

    # station_id, stream url, start fraction, title, thumbnail
    resolved = pyqtSignal(str, str, float, str, str)
    failed = pyqtSignal(str, str)                 # station_id, message

    def __init__(self, station_id, playlist_url, cache_dir,
                 exclude_id="", parent=None):
        super().__init__(parent)
        self.station_id = station_id
        self.playlist_url = playlist_url
        self.cache_dir = cache_dir
        self.exclude_id = exclude_id

    def work(self):
        import random as _random
        import youtube as yt_mod
        try:
            entries = yt_mod.playlist_entries(self.playlist_url, self.cache_dir)
        except Exception as exc:  # noqa: BLE001
            self.send(self.failed, self.station_id, str(exc))
            return

        candidates = [e for e in entries if e.get("id") != self.exclude_id]
        if not candidates:
            candidates = entries
        entry = _random.choice(candidates)

        try:
            url, duration, title, thumbnail = yt_mod.resolve_audio(entry["id"])
        except Exception as exc:  # noqa: BLE001
            self.send(self.failed, self.station_id, str(exc))
            return

        # Join partway through, as a local custom station does. Only the
        # first two-thirds, so it doesn't change track immediately.
        fraction = _random.uniform(0.0, 0.66) if duration > 30 else 0.0
        self.send(
            self.resolved, self.station_id, url, fraction,
            title or entry.get("title", ""), thumbnail or "",
        )


# Pictures decoded off the GUI thread, waiting to be shown (build 55).
_decoded = {}
_decoded_lock = threading.Lock()


def predecode(path):
    """Read and decode an image file now, on this (worker) thread.

    QImage, unlike QPixmap, may be made on any thread. pixmap_for() then
    turns it into a pixmap on the GUI thread, which is quick.
    """
    try:
        import os
        if not path or not os.path.exists(path):
            return
        stamp = _file_stamp(path)
        # Under the image lock: this runs on a worker thread, beside
        # the artwork scaler (task 54).
        import imagelock
        image = imagelock.image(path)
        if image.isNull():
            return
        with _decoded_lock:
            _decoded[path] = (stamp, image)
            while len(_decoded) > 4:            # oldest out
                _decoded.pop(next(iter(_decoded)))
    except Exception:  # noqa: BLE001
        pass


def remember_decoded(path, image):
    """Keep a QImage already decoded (on a worker) for pixmap_for(path).

    For a picture that arrived as bytes and was decoded from those, so
    the file just written needn't be decoded a second time (task 57).
    """
    try:
        if image is None or image.isNull():
            return
        stamp = _file_stamp(path)
        with _decoded_lock:
            _decoded[path] = (stamp, image)
            while len(_decoded) > 4:            # oldest out
                _decoded.pop(next(iter(_decoded)))
    except Exception:  # noqa: BLE001
        pass


def pixmap_for(path):
    """A QPixmap of the file at `path`. GUI thread only.

    Uses the picture predecode() made, when there is one, instead of
    decoding the file here.
    """
    from PyQt5.QtGui import QPixmap
    with _decoded_lock:
        stamp, image = _decoded.get(path, (None, None))
    # Not if the file changed since (Crop this photo… rewrites it).
    if image is not None and stamp is not None and stamp == _file_stamp(path):
        pixmap = QPixmap.fromImage(image)
        if not pixmap.isNull():
            return pixmap
    import imagelock
    return imagelock.pixmap(path)


def _file_stamp(path):
    """(modified time, size) of a file, or None."""
    try:
        import os
        info = os.stat(path)
        return (info.st_mtime_ns, info.st_size)
    except OSError:
        return None


def _note_origin(path, url):
    """Write down where a cached photo came from, for thumbs-down reports
    (build 98, task 136). Never allowed to break the lookup."""
    try:
        import photopack
        photopack.note_photo_origin(path, url)
    except Exception:  # noqa: BLE001
        pass


def _cache_image(url):
    """Download an image once and return its path on disk, or "".

    Kept beside the settings so it survives a restart; named after a
    hash of the URL so the same photo is never fetched twice.
    """
    import hashlib
    import os

    try:
        import storage
        folder = os.path.join(
            os.path.dirname(storage.settings_path()), "artist-art"
        )
        os.makedirs(folder, exist_ok=True)

        name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
        suffix = ".png" if url.lower().endswith(".png") else ".jpg"
        path = os.path.join(folder, name + suffix)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            _note_origin(path, url)
            return path

        import requests
        response = requests.get(
            url, timeout=12, headers={"User-Agent": "Frequencies/1.0"}
        )
        response.raise_for_status()
        if not response.content:
            return ""
        with open(path, "wb") as handle:
            handle.write(response.content)
        _note_origin(path, url)
        return path
    except Exception:  # noqa: BLE001
        return ""


class ArtistInfoWorker(Job):
    """Biography and animated art for one artist.

    Each part is emitted as it arrives rather than waiting for all
    both, so a slow artwork scrape doesn't hold up a biography that
    is already in hand.
    """

    biography_ready = pyqtSignal(str, dict)    # artist, info

    def __init__(self, artist, title="",
                 want_motion=False, parent=None):
        super().__init__(parent)
        self.artist = artist
        self.title = title

    def work(self):
        import artistinfo

        info = None
        try:
            info = artistinfo.biography(
                self.artist, override=storage.artist_override(self.artist)
            )
        except Exception:  # noqa: BLE001
            info = None
        # Always emitted, even empty.
        #
        # Emitting only on success left the panel stuck on "Looking
        # up..." forever whenever nothing was found - which for a
        # lookup that legitimately finds nothing is the worst possible
        # outcome, because it looks identical to a hang.
        # Fetch the photo here, on this thread, and hand over a local
        # path.
        #
        # Qt's rich text widgets resolve image resources themselves and
        # have no network stack, so a remote src can only ever render as
        # the broken-image glyph. Downloading it first and pointing the
        # markup at a file:// path is what actually puts a picture on
        # screen.
        if info and info.get("photo"):
            local = _cache_image(info["photo"])
            info["photo_path"] = local or ""
        # Decoded here too, so the window only has to show it. Decoding a
        # big photo on the GUI thread froze it for 430–490 ms (task 24,
        # build 55). Your own chosen photo wins in feeds, so it is
        # decoded as well.
        try:
            chosen = (storage.artist_override(self.artist) or {}).get("photo")
        except Exception:  # noqa: BLE001
            chosen = None
        for path in (chosen, (info or {}).get("photo_path")):
            if path:
                predecode(path)

        self.send(self.biography_ready, self.artist, info or {})

        # No biography means no artist worth illustrating.
        #
        # If neither MusicBrainz nor Wikipedia has heard of them, an
        # Apple Music page scrape is very unlikely to find animated art
        # for the same act - and it is the slowest of the three lookups,
        # so skipping it saves the panel a pointless wait.
        if not info:
            return


class StationInfoWorker(Job):
    """A Wikipedia article about a station, off the GUI thread.

    For stations that play no music - news, talk, a single presenter -
    where an artist biography is the wrong question entirely.

    `override` is what storage.station_info() gave for the station (a talk
    station's entry, or a station correction). The photo comes back as a
    local file in "photo_path", decoded already (build 67, task 49): the
    article's picture is a link, and the panel can only show a file.
    """

    article_ready = pyqtSignal(str, dict)

    def __init__(self, station, override=None, parent=None):
        super().__init__(parent)
        self.station = station
        self.override = dict(override if override is not None
                             else storage.station_override(station))

    def work(self):
        if self.override.get("twitch_channel"):
            info = self._twitch_profile()
        else:
            try:
                info = artistinfo.station_article(
                    self.station, override=self.override)
            except Exception:  # noqa: BLE001
                info = None
        info = dict(info or {})
        photo = (self.override.get("photo") or info.get("photo") or "")
        path = ""
        if photo.startswith(("http://", "https://")):
            path = _cache_image(photo)
        elif photo:
            path = storage.talk_photo_path(photo)
        if path:
            info["photo_path"] = path
            predecode(path)
        self.send(self.article_ready, self.station, info)

    def _twitch_profile(self):
        """A Twitch station: the streamer's own "About" (build 76, task
        77) — name, bio, picture, and what they are streaming. A bio
        typed into a station correction wins over Twitch's."""
        try:
            import twitch
            found = twitch.profile(self.override["twitch_channel"])
        except Exception:  # noqa: BLE001
            found = {}
        info = {
            "name": found.get("name", ""),
            "summary": self.override.get("bio") or found.get("bio", ""),
            "subtitle": twitch.profile_subtitle(found) if found else "",
            "photo": found.get("photo", ""),
            "url": found.get("url", ""),
            "twitch": bool(found),
        }
        return info
