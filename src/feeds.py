"""Frequencies — the identification side, ported onto the new window.

main.py ran four background jobs off one another: the Shazam timer, the
lyrics lookup that a hit triggers, the artwork fetch, and the thumbnail
cache. They are here rather than in `FrequenciesWindow` because they're a
pipeline, not UI: the window only ever receives the finished result.

Everything below is a port of the logic already in main.py, including the
two corrections it carries — the lyric clock anchors on capture time rather
than response time (or the highlight runs ten seconds late), and the Shazam
capture uses `player.current_source()` rather than the station URL (a custom
station's URL is a `local://` marker, not audio).

Station logos reuse the same worker and the same on-disk cache as history
thumbnails, keyed `logo:<station id>`, so a station's favicon is fetched once
ever rather than once per launch.
"""

import hashlib
import os
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap

import lyrics as lyrics_module
import storage
import thumbs
import workers
from jobs import Job

# Wikimedia refuses requests without one. Same text as artistinfo's.
USER_AGENT = "Frequencies/1.5 (personal internet radio player)"


ART_ORDER = ("shazam", "custom", "stream")


def _art_log(text):
    try:
        import crashlog
        crashlog.write("[art] " + text)
    except Exception:                           # noqa: BLE001
        pass
MIN_LOGO_PX = 24


def _twitch_log(text):
    try:
        import crashlog
        crashlog.write("[twitch] " + text)
    except Exception:                           # noqa: BLE001
        pass


def _talk_log(text):
    try:
        import crashlog
        crashlog.write("[talk] " + text)
    except Exception:                           # noqa: BLE001
        pass


def _artist_log(text):
    try:
        import crashlog
        crashlog.write("[artist] " + text)
    except Exception:                           # noqa: BLE001
        pass


def _dev_list():
    """True when a dev build is using its own talk-station list."""
    import devmode
    return devmode.is_dev() and os.path.exists(storage.dev_talk_path())


def _photo_cache_dir():
    import storage
    return os.path.join(os.path.dirname(storage.settings_path()),
                        "artist-photos")


def _photo_cache_path(artist):
    key = hashlib.sha1((artist or "").strip().lower().encode("utf-8")
                       ).hexdigest()[:16]
    return os.path.join(_photo_cache_dir(), key + ".img")


def _cached_photo(artist):
    """A file already fetched for this artist, or "".

    Kept indefinitely: an artist's photograph is not news, and the point
    of caching it is that the same station playing the same act three
    times an evening does not go and ask three times.
    """
    path = _photo_cache_path(artist)
    return path if os.path.exists(path) else ""


def cache_photo(artist, data):
    path = _photo_cache_path(artist)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
    except OSError:
        return ""
    return path

# A hostname is plumbing. Nobody calls it api.deezer.com.
SERVICE_NAMES = {
    "deezer": "Deezer",
    "dzcdn": "Deezer",          # its image CDN, which is where photos come from
    "apple": "Apple Music",
    "mzstatic": "Apple Music",
    "wikipedia": "Wikipedia",
    "wikimedia": "Wikimedia",
    "musicbrainz": "MusicBrainz",
    "coverartarchive": "Cover Art Archive",
    "lrclib": "lrclib",
    "lyrics.ovh": "lyrics.ovh",
    "shazam": "Shazam",
    "twitch": "Twitch",
}


def _credit(prefix, source, url=""):
    """"Biography from Wikipedia", with the name as a link.

    The name of a service is the obvious thing to click when you want to
    check what it said — so it is the link, rather than a separate "read
    more" that repeats the same word.
    """
    name = _service_name(source)
    if not url:
        return f"{prefix} {name}"
    safe = url.replace("&", "&amp;").replace('"', "&quot;")
    return f'{prefix} <a href="{safe}">{name}</a>'


def _service_name(url_or_host):
    """The name people use, not the host it happens to live on."""
    host = url_or_host
    if "//" in host:
        host = host.split("/")[2]
    host = host.lower().strip()
    for fragment, name in SERVICE_NAMES.items():
        if fragment in host:
            return name
    # Nothing recognised: at least drop the www. and the api. prefix.
    for prefix in ("www.", "api.", "cdn.", "images."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def _site_icons(station):
    """Icon URLs derived from the station's own website.

    Tried in descending order of quality: a touch icon is a real square
    logo, while favicon.ico is usually 16 pixels of nothing — but 16
    pixels of the right logo beats a blank tile.
    """
    from urllib.parse import urlsplit

    homepage = (station.get("homepage") or "").strip()
    if not homepage:
        # No site recorded: the stream's own host is a fair guess.
        homepage = station.get("url", "")
    try:
        parts = urlsplit(homepage)
    except Exception:                   # noqa: BLE001
        return []
    if not parts.netloc or parts.scheme not in ("http", "https"):
        return []
    root = f"{parts.scheme}://{parts.netloc}"
    return [f"{root}/apple-touch-icon.png",
            f"{root}/apple-touch-icon-precomposed.png",
            f"{root}/favicon.png",
            f"{root}/favicon.ico"]


def resolve_order(preferred, fallback_enabled, order=ART_ORDER):
    """A per-station preference turned into the list of sources to try."""
    if preferred in ("auto", "auto_stream"):
        return list(order)
    if not fallback_enabled:
        return [preferred]
    return [preferred] + [s for s in order if s != preferred]


def _usable_logo(data):
    """Is this worth showing instead of a monogram?"""
    if not data:
        return False
    image = QImage()
    if not image.loadFromData(data):
        return False
    return image.width() >= MIN_LOGO_PX and image.height() >= MIN_LOGO_PX


class ArtistPhotoWorker(Job):
    """Find a picture of an artist.

    Deezer's public search returns artist photographs directly, with no
    key and no scraping. The DuckDuckGo route this replaces needed a token
    parsed out of an HTML page first, and that page changed shape often
    enough that it mostly returned nothing — which is why the panel stayed
    blank. iTunes is the second try; it has no artist images, but an
    album cover is a better answer than an empty frame.
    """

    found = pyqtSignal(str, str)        # artist, image url
    credited = pyqtSignal(str, str)     # artist, "who|link"

    def __init__(self, artist, parent=None, settings=None):
        super().__init__(parent)
        self.artist = artist
        self.settings = settings or {}
        self.credit = ""
        # URLs already rejected — usually because they turned out to be
        # the album art the panel is already showing.
        self.skip = set()

    def work(self):
        cached = _cached_photo(self.artist)
        if cached:
            # Decoded here, off the window's thread (task 57).
            workers.predecode(cached)
            self.send(self.found, self.artist, cached)
            return
        # Wikidata first: it is the only one of these that stores a
        # photograph *of the artist* as a fact about them, rather than
        # whatever image a music service happens to have on file. The
        # pictures are usually a portrait or a press shot, and they are
        # freely licensed, which the others are not.
        for lookup in (self._wikidata, self._deezer, self._itunes):
            try:
                url = lookup()
            except Exception:                   # noqa: BLE001
                url = ""
            if url in self.skip:
                continue                        # already rejected once
            if url:
                self.send(self.found, self.artist, url)
                return

    def _wikidata(self):
        """P18 — the "image" property — via the public query endpoint."""
        import requests

        search = requests.get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbsearchentities", "search": self.artist,
                    "language": "en", "format": "json", "limit": 5,
                    "type": "item"},
            headers={"User-Agent": USER_AGENT}, timeout=8).json()
        for hit in search.get("search") or []:
            description = (hit.get("description") or "").lower()
            # Only entities that are plainly musical: "Air" is a band, a
            # chemical mixture and a Nicolas Cage film.
            if not any(word in description for word in
                       ("music", "band", "singer", "rapper", "composer",
                        "duo", "dj", "producer", "songwriter")):
                continue
            entity = requests.get(
                f"https://www.wikidata.org/wiki/Special:EntityData/"
                f"{hit['id']}.json",
                headers={"User-Agent": USER_AGENT}, timeout=8).json()
            claims = ((entity.get("entities") or {}).get(hit["id"]) or {}
                      ).get("claims") or {}
            for claim in claims.get("P18") or []:
                name = (((claim.get("mainsnak") or {}).get("datavalue")
                         or {}).get("value") or "")
                if name:
                    # Special:FilePath redirects to the file itself and
                    # takes a width, so the thumbnail comes back already
                    # the right size.
                    from urllib.parse import quote
                    return ("https://commons.wikimedia.org/wiki/"
                            "Special:FilePath/" + quote(name) + "?width=900")
        return ""

    def _deezer(self):
        """Deezer's artist picture: a press shot rather than a live photo.

        The reason this is tried before anything else is what the pictures
        are. An image search returns whatever the web has most of, which
        for a working band is somebody mid-song under a stage light —
        sweating, eyes shut, mouth open. Deezer and Apple carry the
        photograph the artist's own label supplied, which is the one they
        chose to be seen in.
        """
        import requests
        data = requests.get("https://api.deezer.com/search/artist",
                            params={"q": self.artist, "limit": 5},
                            timeout=8).json()
        wanted = self.artist.strip().lower()
        results = data.get("data") or []
        # Prefer an exact name match: searching "Air" otherwise returns
        # whichever act Deezer thinks is most popular this week.
        for item in sorted(results,
                           key=lambda i: (i.get("name", "").lower() != wanted)):
            # Largest first, always. picture_medium is 250px, and a 250px
            # source cropped to fill a 320-wide header is exactly the soft,
            # blocky result that looks like a mistake.
            url = (item.get("picture_xl") or item.get("picture_big") or "")
            # Deezer serves a grey silhouette for artists with no photo;
            # it is always the same file, so it can be recognised by name.
            if url and "1000x1000-000000-80-0-0" not in url:
                return url
        return ""

    def _itunes(self):
        """Apple's artist image, then one of their album covers.

        The artist entity carries a real portrait for most acts; a cover
        is the fallback, and while it is not a photograph of anyone it is
        at least the picture the artist put on their own record.
        """
        import requests
        artist = requests.get(
            "https://itunes.apple.com/search",
            params={"term": self.artist, "entity": "musicArtist",
                    "limit": 1}, timeout=8).json()
        for item in artist.get("results") or []:
            art = (item.get("artworkUrl100") or "").strip()
            if art:
                return art.replace("100x100bb", "600x600bb")

        album = requests.get(
            "https://itunes.apple.com/search",
            params={"term": self.artist, "entity": "album", "limit": 1},
            timeout=8).json()
        for item in album.get("results") or []:
            art = (item.get("artworkUrl100") or "").strip()
            if art:
                # 100px is a thumbnail; the same URL serves any size.
                return art.replace("100x100bb", "600x600bb")
        return ""


class Feeds(QObject):
    def __init__(self, window, library, player, parent=None):
        super().__init__(parent)
        self.win = window
        self.lib = library
        self.player = player

        self._station_id = None
        self._track = None
        self._track_key = None
        self._shazam_worker = None
        self._lyrics_worker = None
        self._artist_worker = None
        self._artist_shown = ""
        self._last_line_ms = 0
        self._photo_worker = None
        self._photo_url = ""
        # url -> the artist it was asked for (task 57)
        self._photo_asked = {}
        self._station_worker = None
        self._station_bio = ""
        self._station_wanted = None     # storage.station_info() asked for
        self._station_shown = None      # what is on screen, for redraws
        self._station_seq = 0
        self._bio_source = ""
        self._photo_source = ""
        self._art_pending = None
        self._artwork_url = ""
        self._art_workers = []
        self._logo_waiting = {}
        self._art_cache = {}            # url -> bytes
        self._logo_queue = {}           # station id -> candidate urls left
        self._stream_art = ""           # a YouTube video thumbnail
        self._art_failed = set()
        # The song a station named itself (iBroadcast), with its cover.
        # Kept apart from _track, which Shazam replaces and clears: a
        # missed identification must not throw away a song the station
        # already told us (task 23).
        self._named = None
        self._cover_seq = 0

        self._shazam_timer = QTimer(self)
        self._shazam_timer.timeout.connect(self.run_shazam)

        # Say so once, at startup, rather than failing quietly every 30
        # seconds. Running from source this is the usual answer: the
        # fingerprinter is bundled into the AppImage, not installed system
        # wide, so identification is simply unavailable until it is.
        import shazam
        if not shazam.AVAILABLE:
            QTimer.singleShot(0, lambda: self.win.notify(
                "Song identification is unavailable: "
                f"{shazam.IMPORT_ERROR or 'shazamio_core missing'}. "
                "Install it with: pip install --user shazamio-core"))

        # No timer.
        #
        # Timed lyrics on a live stream meant guessing a playback position
        # from a Shazam offset and hoping. Every part of that — the
        # anchor, the capture lag, the resync, the timing slider — existed
        # to compensate for a clock the app does not have. Showing the
        # words without pretending to know which one is playing is both
        # honest and, in practice, more useful.


    # -- station changes ---------------------------------------------------

    def station_changed(self, station):
        """Called from FrequenciesWindow whenever playback switches.

        Also for the next song of the same station (a music folder,
        iBroadcast, YouTube) and after its settings are saved: then a
        station's own info that is up already stays up (task 49).
        """
        same = bool(station) and station.get("id") == self._station_id
        self._shazam_timer.stop()
        self._track = None
        self._track_key = None
        self._artist_shown = ""
        self._photo_url = ""         # a photo still coming is stale
        self._station_id = station.get("id") if station else None
        self._stream_art = ""
        self._named = None
        self._cover_seq += 1            # a cover search still out is stale
        if (station is not None and not same
                and storage.is_twitch_station(station)):
            _art_log(f"'{storage.station_display_name(station)}': Twitch "
                     f"station, no song art (task 89) - the cover shows "
                     f"the station's own picture or its letter tile")
        self.refresh_artwork()
        if station is None:
            self._station_bio = ""
            self._station_shown = None
            self.win.set_station_info_mode(None)
            return
        # A station-level article, when one is set. It describes the
        # station rather than a song, so it stays up the whole time you are
        # connected instead of being replaced by each artist in turn.
        # The same station again keeps what it has (task 49): the window
        # puts it straight back after clearing the last song.
        if not (same and self._station_bio
                and self._station_wanted == storage.station_info(station)):
            had = same and self._station_bio
            self._station_bio = ""
            self._show_station_bio(station)
            if had and not self._station_bio:
                # Switched off while playing: the songs' artists from the
                # next song on, and nothing of the station's left up.
                self._station_shown = None
                self.win.on_artist("", "", "")
                self.win.on_artist_photo(None)
                self.win.on_sources(artist="")

        if not station.get("shazam_auto"):
            return
        # Every 30 seconds, for every station. Not a per-station number:
        # see the note in dialogs.py.
        interval = 30
        self._shazam_timer.start(interval * 1000)
        # A short delay: fingerprinting the first second of a stream that
        # hasn't produced audio yet can only fail.
        QTimer.singleShot(2000, self.run_shazam)

    def _is_current(self, station_id):
        return station_id == self._station_id

    # -- identification ----------------------------------------------------

    def run_shazam(self):
        station = self.lib.station_by_id(self._station_id)
        if station is None:
            return
        if self._shazam_worker is not None and self._shazam_worker.isRunning():
            return                      # one at a time

        source = self.player.current_source() or station.get("url")
        # How far behind the live edge you are hearing.
        #
        # The capture opens its own connection and reads the live edge,
        # so Shazam identifies a moment that has not reached the speakers
        # yet — by exactly the amount mpv has buffered. That buffer varies
        # with the station and the network, which is why the lyric drift
        # was inconsistent rather than a fixed amount.
        start_seconds = None
        if storage.is_custom_station(station):
            position = self.player.position_seconds()
            if position and position > 1:
                start_seconds = max(0.0, position - 1.0)

        worker = workers.ShazamWorker(
            station["id"], source, parent=self, start_seconds=start_seconds)
        worker.identified.connect(self._on_identified)
        worker.no_match.connect(self._on_no_match)
        worker.failed.connect(self._on_failed)
        self._shazam_worker = worker
        worker.start()

    def _on_identified(self, station_id, track):
        if not self._is_current(station_id):
            return
        station = self.lib.station_by_id(station_id)
        if self._named and not track.get("cover_url"):
            # Shazam knew the song but had no sleeve: keep the one the
            # station gave us rather than dropping to nothing.
            track = dict(track, cover_url=self._named.get("cover_url", ""))
        self._track = track

        limit = storage.history_limit(self.lib.settings)
        self.lib.history, added = storage.add_history_entry(
            self.lib.history, track,
            storage.station_display_name(station or {}), limit=limit)
        if added:
            self.lib.save_history()

        key = storage.track_key(track.get("artist"), track.get("title"))
        self._cache_thumbnail(key, track.get("cover_url"))

        # How far into the song the sample matched.
        #
        # shazam.py has worked this out all along and nobody read it, so
        # the lyric clock assumed every song had just begun — right only
        # when the identification happened to catch the opening bars.
        self.win.on_song(track.get("title", ""), track.get("artist", ""),
                         offset=track.get("match_offset") or 0.0,
                         album=track.get("album") or "", source="shazam")
        self.win.refresh_history()
        # Shazam's cover is only one candidate; the station may prefer its
        # own art, so go through the same resolution as everything else.
        if storage.is_twitch_station(station) and track.get("cover_url"):
            _art_log(f"cover for {track.get('title', '')!r}: not shown, "
                     f"Twitch station (task 89)")
        self.refresh_artwork()

        self._maybe_fetch_lyrics(station, track)
        self._maybe_fetch_artist(track)

    def _on_no_match(self, station_id):
        """Keep whatever the stream reported — a failed match shouldn't
        throw away information that was working."""
        if self._is_current(station_id):
            # Back to the song the station named, if it named one. This
            # was None, so on an iBroadcast station every missed match
            # threw the song — and its cover — away (task 23).
            self._track = dict(self._named) if self._named else None
            self.refresh_artwork()

    def _on_failed(self, station_id, message):
        """A failure is not a miss, and swallowing it made a broken
        fingerprinter look exactly like a station nobody has ever heard
        of. Say which it was."""
        if self._is_current(station_id):
            self._track = dict(self._named) if self._named else None
            self.refresh_artwork()
        self.win.notify(f"Song identification: {message}")

    # -- lyrics ------------------------------------------------------------

    # How far out an anchor has to be before it is worth moving.
    #
    # Small corrections every thirty seconds would make the highlight
    # twitch; the measurement is not that precise. Under a second is
    # within the noise, so it is left alone.
    def forget_artwork(self):
        """Drop any artwork still in flight for the station that ended.

        A cover fetched for the last song used to arrive after the change
        and install itself over the new station's — the picture caught up
        with a station that was no longer playing.
        """
        self._art_pending = None
        self._artwork_url = ""

    def _maybe_fetch_lyrics(self, station, track):
        # Off in Settings means no lookup at all.
        if not self.lib.settings.get("lyrics_on", True):
            return
        if not station or not station.get("lyrics_enabled", True):
            return
        title = (track.get("title") or "").strip()
        artist = (track.get("artist") or "").strip()
        if not title:
            return
        key = f"{artist}|{title}".lower()
        if key == self._track_key:
            # Same song, identified again — re-anchor instead of returning.
            #
            # This is the fix for lyrics drifting on a long track. Every
            # other player syncs to a playback position it owns; a live
            # stream has none, so the clock here is dead reckoning from
            # one measurement taken up to several minutes ago. Shazam
            # tells us where we are every thirty seconds, and until now
            # that answer was thrown away for a song already playing.
            return                      # already have them
        self._track_key = key

        self.win.on_lyrics([], "")
        self.win.on_busy(lyrics=True)
        worker = workers.LyricsWorker(station["id"], key, artist, title,
                                      parent=self)
        worker.loaded.connect(self._on_lyrics_loaded)
        worker.none_found.connect(self._on_lyrics_none)
        worker.failed.connect(self._on_lyrics_failed)
        self._lyrics_worker = worker
        worker.start()

    def _still_wanted(self, station_id, track_key):
        return self._is_current(station_id) and track_key == self._track_key

    def _on_lyrics_loaded(self, station_id, track_key, result):
        if not self._still_wanted(station_id, track_key):
            return
        raw = (result.get("source") or "lrclib").strip()
        self.win.on_sources(
            lyrics=_credit("Lyrics from", raw, result.get("url")))
        if result.get("instrumental"):
            self.win.on_lyrics([], "(instrumental)")
            return
        # Either shape: LRCLIB's client hands back parsed pairs, the
        # older source hands back raw LRC text. Parsing an already
        # parsed list would throw both away.
        synced = result.get("synced", "")
        if isinstance(synced, (list, tuple)):
            timed = [(float(at), str(text)) for at, text in synced]
        else:
            timed = lyrics_module.parse_synced(synced)
        self._last_line_ms = int(timed[-1][0] * 1000) if timed else 0
        if timed:
            # The new view wants milliseconds; parse_synced returns seconds.
            self.win.on_lyrics([(int(t * 1000), line) for t, line in timed])

        else:
            plain = (result.get("plain") or "").splitlines()
            self.win.on_lyrics(plain or [],
                               None if plain else "No lyrics found.")

    def _on_lyrics_none(self, station_id, track_key):
        if self._still_wanted(station_id, track_key):
            self.win.on_lyrics([], "No lyrics found for this track.")
            self.win.on_sources(lyrics="")

    def _on_lyrics_failed(self, station_id, track_key, message):
        if self._still_wanted(station_id, track_key):
            self.win.on_lyrics([], message or "Lyrics lookup failed.")

    # A line should be on screen slightly before it is sung, not after —
    # but Shazam's offset lands early enough that lines were changing a
    # beat ahead of the vocal. Holding the clock back by a fraction puts
    # the highlight back on the words.
    # A small constant on top of the measured buffer: decoding, the sound
    # server and the speaker itself all add a little, and none of it is
    # visible from here. Adjustable, because it lands differently on
    # different hardware and the only honest test is your ears.

    def _expire_track(self):
        self._track = None
        self._named = None
        self._track_key = None
        self._last_line_ms = 0
        self._artist_shown = ""
        self._photo_url = ""         # a photo still coming is stale
        self.win.on_lyrics([], "")
        if self._station_bio:
            # The station's own article outlives any one song.
            station = self.lib.station_by_id(self._station_id)
            if station is not None:
                self._show_station_bio(station)
        else:
            self.win.on_artist("", "", "")
        self.refresh_artwork()

    # -- artwork and logos -------------------------------------------------

    def _cache_thumbnail(self, key, url):
        if not key or not url or thumbs.has(key):
            return
        worker = workers.ArtworkWorker("thumb:" + key, url, parent=self)
        worker.loaded.connect(self._on_thumb_fetched)
        worker.finished.connect(self._reap)
        self._art_workers.append(worker)
        worker.start()

    def _on_thumb_fetched(self, slot, url, data):
        if slot.startswith("thumb:"):
            key = slot[6:]
            thumbs.save(key, data,
                        limit_bytes=self.lib.settings.get(
                            "thumb_limit_bytes", thumbs.DEFAULT_LIMIT_BYTES))
            if self._track and storage.track_key(
                    self._track.get("artist"),
                    self._track.get("title")) == key:
                self._art_cache[url] = data
                self.refresh_artwork()
            # One cover, one tile.
            #
            # This called refresh_history(), which rebuilt the entire
            # page — every row and tile destroyed and recreated — for each
            # thumbnail that arrived. Covers land one at a time, so twenty
            # of them meant twenty full rebuilds, and the stall log showed
            # them costing up to four seconds each.
            forget_history_art(key)
            self.win.cover_arrived(key, data)
        elif slot.startswith("logo:"):
            sid = slot[5:]
            if not _usable_logo(data):
                # A 16px broken-image glyph is worse than the monogram, and
                # so is a placeholder the host serves for everything — try
                # the next candidate instead of settling for it.
                self._next_logo(sid)
                return
            self._logo_queue.pop(sid, None)
            thumbs.save("logo:" + sid, data)
            done = self._logo_waiting.pop(sid, None)
            if done:
                done(sid, data)

    # -- artist ------------------------------------------------------------

    _talk_passed = ""

    def _show_station_bio(self, station):
        """The station's own info in the artist panel, while it plays.

        A talk station (task 49: switched on in a dev build, shipped to
        users) or a station correction from Settings > Artists > Artist
        corrections. Songs never replace it: `_maybe_fetch_artist` stands
        aside while `_station_bio` is set, and the window puts it back
        after each next song of the same station.
        """
        name = storage.station_display_name(station)
        info = storage.station_info(station)
        self._station_wanted = info
        self._station_shown = None
        self._station_seq += 1
        talk = bool(info.get("talk"))
        if talk:
            import devmode
            self.win.set_station_info_mode("edit" if devmode.is_dev()
                                           else "locked")
            _talk_log(f"'{name}' keeps the station's info in the artist "
                      f"panel (talk radio, matched by "
                      f"{info.get('_matched', '?')}, "
                      f"{'your dev list' if _dev_list() else 'shipped list'})")
        elif info.get("twitch_channel"):
            # The streamer's own words (task 77): nothing to correct or
            # vote on, like a talk station in a user build.
            self.win.set_station_info_mode("locked")
            _twitch_log(f"'{name}': asking Twitch for the streamer's bio "
                        f"and picture (channel {info['twitch_channel']})")
        else:
            self.win.set_station_info_mode(None)
        if not info:
            return
        self._station_bio = name
        self._talk_passed = ""
        self.redraw_station_info()      # the name and dots, for now
        worker = workers.StationInfoWorker(name, info, parent=self)
        seq = self._station_seq
        worker.article_ready.connect(
            lambda n, found, seq=seq: self._on_station_article(n, found, seq))
        self._station_worker = worker
        worker.start()

    def _on_station_article(self, name, info, seq=None):
        if name != self._station_bio:
            return
        if seq is not None and seq != self._station_seq:
            return                      # asked again since (info edited)
        info = info or {}
        wanted = self._station_wanted or {}
        shown = (wanted.get("name") or name).strip()
        if wanted.get("twitch_channel"):
            # The streamer's display name, as Twitch writes it.
            shown = (wanted.get("name") or info.get("name") or name).strip()
        genres = wanted.get("genres", "") or info.get("subtitle", "") or ""
        bio = info.get("summary") or info.get("extract") or ""
        photo = info.get("photo_path") or None
        credit = ""
        if wanted.get("twitch_channel"):
            if info.get("twitch") and info.get("url"):
                credit = _credit("About the streamer from", "twitch",
                                 info["url"])
            _twitch_log(
                f"'{name}': artist panel shows "
                + (("Twitch's profile" if info.get("twitch")
                    else "the name only (nothing came from Twitch)")
                   + (", bio from your correction" if wanted.get("bio")
                      else f", bio {len(bio)} chars" if bio
                      else ", no bio")
                   + (", photo" if photo else ", no photo")))
        elif wanted.get("photo_source"):
            credit = wanted["photo_source"]
            if wanted.get("photo_url"):
                credit = (f'<a href="{wanted["photo_url"]}" '
                          f'style="color:inherit">{credit}</a>')
        elif info.get("url") and not wanted.get("bio"):
            credit = _credit("About the station from", "wikipedia",
                             info.get("url"))
        self._station_shown = (shown, genres, bio, photo, credit)
        if wanted.get("talk"):
            _talk_log(f"'{name}': info shown ("
                      f"{'typed-in bio' if wanted.get('bio') else 'Wikipedia' if bio else 'nothing found, name only'}"
                      f"{', photo' if photo else ', no photo'})")
        self.redraw_station_info()

    def redraw_station_info(self):
        """Put the station's info (back) on screen, if it has some.

        The window clears the artist panel at every song change, the
        next song of the same station too; this puts it straight back
        without asking anyone again. True when it did.
        """
        if not self._station_bio:
            return False
        shown = self._station_shown
        if not shown:
            # Still being looked up: the name, and dots for the rest.
            wanted = self._station_wanted or {}
            self.win.on_artist(wanted.get("name") or self._station_bio,
                               "", "")
            self.win.on_busy(artist=True)
            self.win.set_station_info_mode(self.win._station_info_mode)
            return True
        name, genres, bio, photo, credit = shown
        self.win.panel.set_busy(artist=False)
        self.win._artist_key = name
        self.win.on_artist(name, genres, bio, photo)
        if not photo:
            self.win.on_artist_photo(None)
        self.win.on_sources(artist=credit)
        self.win.set_station_info_mode(
            self.win._station_info_mode)          # pins it on the views
        return True

    def refresh_station_info(self):
        """Look the playing station's info up again (it was just edited)."""
        station = self.lib.station_by_id(self._station_id)
        if station is None:
            return
        self._station_bio = ""
        self._show_station_bio(station)

    def holds_station_info(self):
        """True while the station's own info holds the artist panel."""
        return bool(self._station_bio)

    def _artist_credit(self):
        parts = [p for p in (self._bio_source, self._photo_source) if p]
        return " · ".join(parts)

    def _maybe_fetch_artist(self, track):
        """Who this is, from MusicBrainz and Wikipedia.

        Nothing drove this before — the panel had nowhere to put it. Now
        that the side panel has an artist view it is worth asking, but only
        once per artist: the lookup is slow and the answer doesn't change
        between two songs by the same act.
        """
        if not self.lib.settings.get("artist_lookup", True):
            return
        if self._station_bio:
            # The station's own article holds the panel (task 49).
            artist = (track.get("artist") or "").strip()
            if artist and artist != self._talk_passed:
                self._talk_passed = artist
                _talk_log(f"song by '{artist}' left the station's info "
                          f"in place")
            return
        artist = (track.get("artist") or "").strip()
        if not artist or artist == self._artist_shown:
            return
        self._artist_shown = artist
        self._photo_url = ""             # the last artist's is stale now
        self._bio_source = self._photo_source = ""
        self.win.on_sources(artist="")
        # Clear the previous artist's picture before the new lookup runs.
        #
        # The name changed with the song but the photograph did not, so a
        # panel could show one artist's face over another's name — and
        # under a line saying nothing was found for them.
        self.win.on_artist_photo(None)

        # The name straight away, and dots where the biography will be.
        self.win.on_artist(artist, "", "")
        self.win.on_busy(artist=True)

        worker = workers.ArtistInfoWorker(
            artist, title=track.get("title", ""), parent=self)
        worker.biography_ready.connect(self._on_artist_bio)
        self._artist_worker = worker
        worker.start()

    def _find_artist_photo(self, artist):
        """A picture when the biography sources had none.

        MusicBrainz and Wikipedia only have photographs for artists someone
        has written about, which leaves a lot of electronic and underground
        acts with a blank panel. An image search fills those in. It runs on
        its own thread and simply fails quietly — a missing photo is the
        state we are already in.
        """
        if not artist or not self.lib.settings.get("artist_lookup", True):
            return
        worker = ArtistPhotoWorker(artist, parent=self,
                                   settings=self.lib.settings)
        worker.credited.connect(self._on_photo_credit)
        worker.found.connect(self._on_photo_found)
        worker.finished.connect(self._reap_photo)
        self._photo_worker = worker
        worker.start()

    def _reap_photo(self):
        self._photo_worker = None

    def _retry_photo(self, artist):
        """Ask again, skipping whatever matched the album art."""
        worker = ArtistPhotoWorker(artist, parent=self,
                                   settings=self.lib.settings)
        worker.skip = set(self._art_failed)
        worker.found.connect(self._on_photo_found)
        worker.credited.connect(self._on_photo_credit)
        worker.finished.connect(self._reap)
        self._photo_worker = worker
        worker.start()

    def _on_photo_credit(self, artist, credit):
        """Record who took the picture, for the panel's source line."""
        if artist != self._artist_shown or not credit:
            return
        who, _, link = credit.partition("|")
        self._photo_source = _credit("photo by", who, link)
        self.win.on_sources(artist=self._bio_source or self._photo_source)

    def _on_photo_found(self, artist, url):
        if artist != self._artist_shown or not url:
            return
        # Not the same picture as the album art.
        #
        # Both can resolve to the same place — iTunes hands back a cover
        # when it has no artist portrait, and the cover is often what the
        # station is already showing. Two identical squares in one panel
        # look like a bug, so the next source is tried instead.
        if url and url == getattr(self, "_artwork_url", ""):
            self._art_failed.add(url)
            worker = getattr(self, "_photo_worker", None)
            if worker is not None:
                self._retry_photo(artist)
            return
        if self.holds_station_info():
            return                      # the station's own info holds
        # A cache hit is a path, not a URL, and needs no fetching.
        if not url.startswith(("http://", "https://")):
            # Decoded on the worker already (task 57); this only turns
            # it into a pixmap. Falls back to a locked decode.
            pixmap = workers.pixmap_for(url)
            if not pixmap.isNull():
                # The file goes with the picture, or "Crop this photo…"
                # has nothing to work on (task 46).
                self.win.panel.set_photo_path(url)
                self.win.panel.set_photo(pixmap)
            return
        self._photo_url = url
        self._photo_asked[url] = artist
        while len(self._photo_asked) > 20:      # oldest out
            self._photo_asked.pop(next(iter(self._photo_asked)))
        self._photo_source = _credit("photo from", url, url)
        self.win.on_sources(artist=self._artist_credit())
        worker = workers.ArtworkWorker("photo", url, parent=self)
        worker.loaded.connect(self._on_photo_loaded)
        worker.finished.connect(self._reap)
        self._art_workers.append(worker)
        worker.start()

    MIN_PHOTO_PX = 400

    def _on_photo_loaded(self, _slot, url, data):
        """A downloaded artist photo: decode and save it off the window's
        thread, then show it — if it is still the one wanted (task 57).

        Before build 69 this didn't check which photo had come back. A
        download that finished after the song had moved on to another
        artist was shown under the new artist's name AND saved as the
        new artist's photo, so the wrong face came back every time that
        artist played (and on a talk station it covered the station's
        own photo). It was also decoded on the window's thread.

        Now it is saved under the artist it was asked for (a late photo
        is still the right face for that artist), and only shown when
        it is the photo the panel is still waiting for.
        """
        artist = self._photo_asked.pop(url, "")
        if not artist or not data:
            return
        small = self.MIN_PHOTO_PX

        def work():
            image = QImage()
            if not image.loadFromData(data):    # under the image lock
                return None
            path = cache_photo(artist, data)
            if path:
                workers._note_origin(path, url)
                workers.remember_decoded(path, image)
            return path, min(image.width(), image.height()) >= small

        job = workers.Lookup(work)
        job.done.connect(lambda result, error:
                         self._on_photo_ready(artist, url, result, error))
        job.start()

    def _on_photo_ready(self, artist, url, result, error):
        if error is not None or not result:
            return
        path, big_enough = result
        if (url != self._photo_url or artist != self._artist_shown
                or self.holds_station_info()):
            now = ("the station's own info" if self.holds_station_info()
                   else f"'{self._artist_shown}'" if self._artist_shown
                   else "nobody")
            _artist_log(f"late photo for '{artist}' not shown (the panel "
                        f"is on {now} now); kept for '{artist}'")
            return
        # Too small to crop into a wide header without softening, so it is
        # not used: an empty frame with the tower in it is better than a
        # blurred face.
        if not big_enough or not path:
            return
        pixmap = workers.pixmap_for(path)
        if pixmap.isNull():
            return
        # The cached file is what "Crop this photo…" rewrites. It was never
        # passed on, so the item stayed disabled for every photo that was
        # found rather than typed in (task 46).
        self.win.panel.set_photo_path(path)
        self.win.panel.set_photo(pixmap)

    def _on_artist_bio(self, artist, info):
        if artist != self._artist_shown:
            return                      # a newer song won the race
        info = info or {}
        genres = info.get("genres") or info.get("tags") or ""
        if isinstance(genres, (list, tuple)):
            genres = " · ".join(str(g) for g in genres[:6])
        # Where the words came from. artistinfo tries MusicBrainz first
        # and falls back to Wikipedia, and it says which answered.
        where = (info.get("source") or "").strip()
        self._bio_source = (_credit("Biography from", where,
                                    info.get("url")) if where else "")
        self.win.on_sources(artist=self._artist_credit())
        # Your corrections win over what was found, field by field.
        #
        # Only the biography was corrected, and that deep inside the
        # lookup. The name, the genres and the photograph all came
        # straight from the result — so the moment a lookup finished it
        # repainted the panel with the originals, and a saved correction
        # seemed to undo itself.
        import storage as _storage
        written = _storage.artist_override(artist) or {}
        shown = (written.get("name") or artist).strip()
        genres = written.get("genres") or genres
        chosen = (written.get("photo") or "").strip()
        photo = (chosen if chosen and os.path.exists(chosen)
                 else info.get("photo_path") or None)

        self.win._artist_key = artist
        self.win.on_artist(
            shown, genres,
            written.get("bio") or info.get("summary") or info.get("extract")
            or info.get("bio") or "",
            photo)
        pin = getattr(self.win, "_pin_artist_key", None)
        if callable(pin):
            pin()

        credit = (written.get("photo_source") or "").strip()
        if credit:
            link = (written.get("photo_url") or "").strip()
            if link:
                credit = (f'<a href="{link}" style="color:inherit">'
                          f'{credit}</a>')
            self.win.on_sources(artist=credit)

        if not photo:
            self._find_artist_photo(artist)

    # -- artwork resolution -------------------------------------------------

    def _art_candidates(self, source, station):
        """Every (kind, value) worth trying for this source, in order.

        A source can offer more than one candidate, which is what makes
        "stream" actually produce artwork: most directory entries carry no
        favicon at all, so with only that to go on the source looked broken
        when it was merely empty-handed.
        """
        if source == "custom":
            path = (station.get("custom_art_path") or "").strip()
            return [("path", path)] if path and os.path.exists(path) else []

        twitch = storage.is_twitch_station(station)
        if source == "shazam":
            # No song art on a Twitch station (task 89): an identified
            # song's cover never shows there.
            if twitch:
                return []
            url = (self._track or {}).get("cover_url", "")
            return [("url", url)] if url else []

        if source == "stream":
            out = []
            # A Twitch channel's own picture (bridge, from yt-dlp), so a
            # refresh after an identified song keeps it (task 89).
            twitch_art = (station.get("twitch_art") or "").strip()
            if twitch and twitch_art:
                out.append(("url", twitch_art))
            if self._stream_art:        # a YouTube video's own thumbnail
                out.append(("url", self._stream_art))
            favicon = (station.get("favicon") or "").strip()
            if favicon:
                out.append(("url", favicon))
            out.extend(("url", u) for u in _site_icons(station))
            return out

        return []

    def refresh_artist(self, name):
        """Show a correction now, then look the rest up behind it.

        Waiting for MusicBrainz and Wikipedia to answer before showing
        text that was typed by hand is a wait for nothing — the answer is
        already known. Anything the correction supplies is put on screen
        immediately; the lookup still runs, for the fields it did not
        fill in.
        """
        import storage as _storage

        name = (name or "").strip()
        if not name:
            return
        written = _storage.artist_override(name) or {}

        shown = (written.get("name") or name).strip()
        photo = (written.get("photo") or "").strip()
        if written.get("bio") or photo or written.get("genres"):
            # "genres" — this read "subtitle", a name the editor never
            # writes, so it redrew the panel with no genres at all a moment
            # after the window had shown the ones you typed.
            self.win.on_artist(shown, written.get("genres", "") or "",
                               written.get("bio", "") or "",
                               photo or None)
            if photo:
                self.win.panel.set_photo_path(photo)

        # And the rest, which may still add tags, an area or a picture.
        self._artist_shown = ""
        self._photo_url = ""         # a photo still coming is stale
        self._maybe_fetch_artist({"artist": name, "title": ""})

    def want_station_art(self, url):
        """Show a picture the station itself pointed at.

        A Twitch channel has no songs to identify and so never reaches
        the ordinary artwork path — but it does have a picture of its
        own, and that is what the station should look like.
        """
        if not url or url == self._artwork_url:
            return
        data = self._art_cache.get(url)
        if data is not None:
            self._artwork_url = url
            self.win.set_artwork_data(data=data)
            return
        self._fetch_art(url)

    def want_artwork(self, artist, title, album="", cover=""):
        """Show the cover for a track the station named itself.

        iBroadcast knows what it is playing, so it never needs Shazam.
        `cover` is the sleeve iBroadcast keeps for the track. Without one
        (a station added before covers were saved, or a track uploaded
        with no art) the cover is looked up by name instead.

        The same track can be announced more than once — again when it
        finishes connecting, again when a station's covers are filled
        in. That is not a new song: nothing is reset, and a cover that
        has turned up since is added (task 23).
        """
        artist = (artist or "").strip()
        title = (title or "").strip()
        album = (album or "").strip()
        cover = (cover or "").strip()
        if not title:
            return
        same = (self._named is not None
                and (self._named["artist"], self._named["title"],
                     self._named["album"]) == (artist, title, album))
        if same:
            if cover and cover != self._named.get("cover_url"):
                self._named["cover_url"] = cover
                if self._track is None or self._track.get("title") == title:
                    self._track = dict(self._named)
                self.refresh_artwork()
            return

        self._cover_seq += 1            # a search for the last song is stale
        self._named = {"artist": artist, "title": title, "album": album,
                       "cover_url": cover}
        self._track = dict(self._named)
        self._artwork_url = ""
        self._art_pending = None
        if cover and cover not in self._art_failed:
            self.refresh_artwork()
        else:
            self._search_cover()
        if artist:
            # Both take the track, not the artist name.
            self._maybe_fetch_artist(self._track)
            self._maybe_fetch_lyrics(
                self.lib.station_by_id(self._station_id) or {}, self._track)

    def _search_cover(self):
        """Look the named song's cover up by name, off the GUI thread.

        Apple's search, as used for artist photos: the album when we know
        it, the song otherwise. Only taken when the artist matches, so a
        song that shares its name with someone else's doesn't wear their
        sleeve. Nothing found means the station's placeholder, not the
        last song's cover.
        """
        named = self._named
        if not named or not named.get("artist") or named.get("searched"):
            self._settle_artwork()
            return
        named["searched"] = True        # once per song, never in a loop
        self._cover_seq += 1
        seq = self._cover_seq
        artist, title, album = named["artist"], named["title"], named["album"]

        def search():
            import requests
            wanted = artist.lower()
            tries = []
            if album:
                tries.append(("album", f"{artist} {album}"))
            tries.append(("song", f"{artist} {title}"))
            for entity, term in tries:
                found = requests.get(
                    "https://itunes.apple.com/search",
                    params={"term": term, "entity": entity, "limit": 5},
                    headers={"User-Agent": USER_AGENT},
                    timeout=8).json()
                for item in found.get("results") or []:
                    name = (item.get("artistName") or "").lower()
                    if not name or (wanted not in name
                                    and name not in wanted):
                        continue
                    art = (item.get("artworkUrl100") or "").strip()
                    if art:
                        return art.replace("100x100bb", "600x600bb")
            return ""

        job = workers.Lookup(search)
        job.done.connect(
            lambda url, error: self._cover_found(seq, url, error))
        job.start()

    def _cover_found(self, seq, url, error):
        if seq != self._cover_seq or not self._named:
            return                      # the song or station has moved on
        title = self._named.get("title", "")
        if url:
            _art_log(f"cover for {title!r}: found by search")
            self._named["cover_url"] = url
            if self._track is None or self._track.get("title") == title:
                self._track = dict(self._named)
            self._settle_artwork()
            return
        _art_log(f"cover for {title!r}: none found"
                 + (f" ({error})" if error else ""))
        self._settle_artwork()

    def _settle_artwork(self):
        """Nothing more is coming for this song: if no source has a
        picture, show the station's placeholder rather than leave the
        previous song's cover up."""
        if self.refresh_artwork():
            return
        station = self.lib.station_by_id(self._station_id)
        if station is None:
            return
        try:
            import stationlist
            self.win.clear_artwork(stationlist.station_color(station),
                                   storage.station_display_name(station))
        except Exception:                       # noqa: BLE001
            pass

    def refresh_artwork(self):
        """Recompute the artwork from whatever each source currently has.

        Walks the station's preferred order, then the rest if fallback is
        on. A URL that isn't cached yet starts a fetch and returns — the
        fetch calls back here when it lands.
        """
        station = self.lib.station_by_id(self._station_id)
        if station is None:
            return False
        for source in resolve_order(station.get("art_source", "auto"),
                                    station.get("art_fallback", True)):
            for kind, value in self._art_candidates(source, station):
                if kind == "path":
                    # Handed over as a path, not a decoded pixmap: the
                    # worker reads and decodes it off the GUI thread.
                    if os.path.exists(value):
                        self._art_pending = (source, value)
                        self._artwork_url = value
                        self.win.set_artwork_data(path=value)
                        return True
                    continue
                if value in self._art_failed:
                    continue
                data = self._art_cache.get(value)
                if data is not None:
                    # The bytes go straight to the worker. Decoding here
                    # was the freeze: loadFromData on a large image blocks
                    # the window for as long as it takes.
                    self._art_pending = (source, value)
                    self._artwork_url = value
                    self.win.set_artwork_data(data=data)
                    return True
                self._fetch_art(value)
                return True             # the fetch will call us back
        return False                    # no source has a picture

    def _fetch_art(self, url):
        if not url or url in self._art_cache or url in self._art_failed:
            return
        worker = workers.ArtworkWorker("art", url, parent=self)
        worker.loaded.connect(self._on_art_loaded)
        worker.failed.connect(self._on_art_failed)
        worker.finished.connect(self._reap)
        self._art_workers.append(worker)
        worker.start()

    def _on_art_loaded(self, slot, url, data):
        self._art_cache[url] = data
        self.refresh_artwork()

    def art_decode_failed(self):
        """The worker could not read the last image we handed it.

        Marked as failed and the search resumes, which is what the inline
        decode used to do with a `continue`.
        """
        pending = getattr(self, "_art_pending", None)
        self._art_pending = None
        if pending:
            self._art_failed.add(pending[1])
            if self._named and pending[1] == self._named.get("cover_url"):
                # The song's own cover can't be read: search by name.
                self._named["cover_url"] = ""
                if self._track and self._track.get("cover_url") == pending[1]:
                    self._track["cover_url"] = ""
                self._search_cover()
                return
        self.refresh_artwork()

    def _on_art_failed(self, slot, url):
        # Remembered, so the next refresh moves past it instead of
        # re-requesting a 404 every time anything changes.
        self._art_failed.add(url)
        if self._named and url == self._named.get("cover_url"):
            # iBroadcast's own cover wouldn't load: look it up by name.
            _art_log(f"cover for {self._named.get('title')!r}: "
                     f"library cover failed, searching")
            self._named["cover_url"] = ""
            if self._track and self._track.get("cover_url") == url:
                self._track["cover_url"] = ""
            self._search_cover()
            return
        self.refresh_artwork()

    def fetch_artwork(self, url):
        """A YouTube video's thumbnail. It becomes the "stream" candidate
        rather than being pushed straight to the panel, so a station that
        prefers custom art still gets custom art."""
        if not url:
            return
        self._stream_art = url
        worker = workers.ArtworkWorker("stream", url, parent=self)
        worker.loaded.connect(self._on_stream_art)
        worker.finished.connect(self._reap)
        self._art_workers.append(worker)
        worker.start()

    def _on_stream_art(self, slot, url, data):
        self._art_cache[url] = data
        self.refresh_artwork()

    def _reap(self):
        self._art_workers = [w for w in self._art_workers if w.isRunning()]

    def shutdown(self):
        """On quit. Nothing to wait for any more: every worker here is a
        jobs.Job (a daemon thread), and `Job.stop_all` in
        mainwindow.shutdown has already stopped them sending anything
        (build 54, task 14). Waiting up to 4 s for each download made
        quitting slow, and one still going after that crashed the app."""
        self._shazam_timer.stop()

    def logo_fetcher(self, station_id, url, done):
        """Handed to StationList. Cached logos come straight back; the rest
        go through the same worker as cover art.

        A station with no favicon still has a website, and a website nearly
        always has an icon — so this tries the same derived candidates the
        artwork panel uses rather than giving up on the majority of the
        directory.
        """
        cached = thumbs.load("logo:" + str(station_id))
        if cached:
            done(station_id, cached)
            return
        if station_id in self._logo_waiting:
            return

        station = self.lib.station_by_id(station_id) or {}
        candidates = []
        if url and url.startswith(("http://", "https://")):
            candidates.append(url)
        for derived in _site_icons(station):
            if derived not in candidates:
                candidates.append(derived)
        if not candidates:
            return

        self._logo_waiting[station_id] = done
        self._logo_queue[station_id] = candidates
        self._next_logo(station_id)

    def _next_logo(self, station_id):
        """Try the next candidate, or give up and leave the monogram."""
        queue = self._logo_queue.get(station_id) or []
        if not queue:
            self._logo_queue.pop(station_id, None)
            self._logo_waiting.pop(station_id, None)
            return
        url = queue.pop(0)
        worker = workers.ArtworkWorker("logo:" + str(station_id), url,
                                       parent=self)
        worker.loaded.connect(self._on_thumb_fetched)
        worker.failed.connect(
            lambda _slot, _url, sid=station_id: self._next_logo(sid))
        worker.finished.connect(self._reap)
        self._art_workers.append(worker)
        worker.start()


_history_art_cache = {}
_HISTORY_ART_MAX = 400


def history_art(entry):
    """Fill a history entry's `art` from the thumbnail cache.

    Memoised, because this is called for every entry on every refresh —
    and a refresh happens on every identified song. Two hundred entries
    meant two hundred file reads and two hundred JPEG decodes on the GUI
    thread, which the stall log caught at 2.3 seconds.

    A miss is remembered too: a song with no thumbnail was the expensive
    case, since it re-checked the disk every time to find nothing.
    """
    key = entry.get("id", "")
    if key in _history_art_cache:
        return _history_art_cache[key]

    data = thumbs.load(key)
    pixmap = None
    if data:
        candidate = QPixmap()
        if candidate.loadFromData(data):
            pixmap = candidate

    if len(_history_art_cache) > _HISTORY_ART_MAX:
        _history_art_cache.clear()
    _history_art_cache[key] = pixmap
    return pixmap


def forget_history_art(key=""):
    """Drop a cached thumbnail, or all of them, when one is fetched."""
    if key:
        _history_art_cache.pop(key, None)
    else:
        _history_art_cache.clear()
