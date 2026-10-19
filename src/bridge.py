"""Frequencies — the bridge between the redesigned UI and the existing app.

Written against the real `storage.py` and `player.py`.

Two things live here:

``Library``   owns the three mutable objects main.py already owns — the
              stations list, the settings dict, the history list — and turns
              them into the row/entry dicts the new widgets want. `storage`
              is a module of free functions, so there's no object to hang
              this off; Library is that object.

``Playback``  owns the queue. `Player` deliberately has no next/previous:
              it plays one URL and stops. Shuffle, wrap-around, the
              previous-station stack, and local/YouTube resolution all sit
              here.

Nothing in the UI imports `storage` or `player` directly. Everything goes
through these two.
"""

import os
import random
import threading
import time

import storage


# --------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------

class Library:
    def __init__(self, stations, settings, history, health=None):
        self.stations = stations
        self.settings = settings
        self.history = history
        self.health = health if health is not None else storage.load_health()

    @classmethod
    def load(cls):
        """Standalone load, for running the UI without the rest of main.py."""
        settings = storage.load_settings()
        stations = storage.load_stations()
        storage.apply_identify_default(stations, settings)
        storage.apply_station_eq_default(stations, settings)
        return cls(stations, settings, storage.load_history())

    # -- persistence -------------------------------------------------------

    def save_stations(self):
        storage.save_stations(self.stations)

    def save_settings(self):
        storage.save_settings(self.settings)

    def save_history(self):
        storage.save_history(self.history)

    # -- lookups -----------------------------------------------------------

    def station_by_id(self, sid):
        return next((s for s in self.stations if s.get("id") == sid), None)

    def tag_colour(self, tag):
        return storage.tag_color(self.settings, tag)

    # -- conversion --------------------------------------------------------

    def subtitle(self, station):
        """The second line. A stream host told you nothing worth reading at
        a glance, so an ordinary station gets no subtitle at all — only the
        things actually worth saying: that it is down, or that it plays your
        own music rather than a broadcast."""
        entry = storage.health_for(self.health, station.get("id"))
        if entry and entry.get("down"):
            return storage.describe_health(entry)

        kind = station.get("kind", "stream")
        if kind == "local":
            path = station.get("local_path") or ""
            return f"Local · {os.path.basename(path.rstrip('/')) or path}"
        if kind == "youtube":
            return "YouTube playlist"
        return ""

    def logo_for(self, station):
        """What the row tile should show.

        Saved artwork wins whenever there is any, not only when art_source
        says "custom". Picking an image for a station saves it to
        custom_art_path and often leaves art_source alone, so gating on
        the source meant art you had chosen yourself never reached the
        row — the one place it is most useful.

        Otherwise the favicon; and when that is missing, which is most
        directory entries, the station's own site, so the fetcher has
        somewhere to derive an icon from.
        """
        path = (station.get("custom_art_path") or "").strip()
        if path and os.path.exists(path):
            return path
        # No favicons.
        #
        # A favicon is a 32px site icon, and a station that has one
        # usually also sends a real cover in its stream — so taking the
        # favicon meant choosing the worst picture available and then
        # scaling it up. Nothing here is better than something bad: the
        # placeholder is drawn, not fetched, and looks deliberate.
        return ""

    def row(self, station):
        tags = [(t, self.tag_colour(t)) for t in station.get("tags") or []]
        entry = storage.health_for(self.health, station.get("id"))
        return {
            "_down": bool(entry and entry.get("down")),
            "id": station.get("id"),
            "name": storage.station_display_name(station),
            "location": self.subtitle(station),
            "logo": self.logo_for(station),
            "tags": tags,
            "_source": station,
        }

    def rows(self):
        ordered = storage.sort_stations(
            self.stations, self.settings.get("sort_mode", "manual"),
            self.settings.get("sort_desc"))
        return [self.row(s) for s in ordered]

    def sidebar_tags(self):
        counts = {}
        for s in self.stations:
            for t in s.get("tags") or []:
                counts[t.lower()] = counts.get(t.lower(), 0) + 1
        # A spare tag (made in the tag manager, on no station yet) shows
        # with 0 (task 87).
        return [(t, self.tag_colour(t), counts.get(t.lower(), 0))
                for t in storage.library_tags(self.stations, self.settings)]

    def history_entries(self):
        """History rows use `title` and `favorite`; the History page wants
        `song` and `favourite`. The storage key doubles as the tile id, so
        starring a tile lands on the right entry."""
        out = []
        for e in storage.sort_history(self.history):
            out.append({
                "id": storage.entry_key(e),
                "song": e.get("title", ""),
                "artist": e.get("artist", ""),
                "station": e.get("station", ""),
                "at": e.get("at", 0),
                "art": None,               # filled in by the thumbnail cache
                "cover_url": e.get("cover_url", ""),
                # Spelled as storage spells it, so the row and the entry
                # it came from cannot disagree.
                "favorite": storage.is_favorite(e),
                "url": e.get("apple_music_url", ""),
            })
        return out

    def set_favourite(self, key, on, track=None, station_name=""):
        self.history, changed = storage.set_favorite(
            self.history, key, on, track=track, station_name=station_name)
        if changed:
            self.save_history()
        return changed

    def reorder(self, ids):
        """Drag-reorder writes sort_index, and only means anything in manual
        sort — the same rule the old list had."""
        if self.settings.get("sort_mode") != "manual":
            return False
        index = {sid: i for i, sid in enumerate(ids)}
        self.stations.sort(key=lambda s: index.get(s.get("id"), 1 << 30))
        storage.renumber_manual_order(self.stations)
        self.save_stations()
        return True


# --------------------------------------------------------------------------
# Playback
# --------------------------------------------------------------------------

def _buffer_for(station):
    """Seconds to pre-buffer, or None to leave mpv's default alone. An
    enabled setting with no explicit value falls back to the worst connect
    time this station has actually shown."""
    if not station.get("buffer_enabled"):
        return None
    seconds = float(station.get("buffer_seconds") or 0.0)
    if seconds <= 0:
        seconds = float(station.get("connect_worst") or 0.0)
    return seconds if seconds > 0 else None


class Playback:
    """The queue Player doesn't have.

    `visible_ids` returns the ids currently on screen, so Next respects the
    tag filter and the search box — the visible list is the play queue,
    which is what the earlier redesign settled on.
    """

    def __init__(self, player, library, visible_ids=None, on_change=None,
                 on_notice=None, parent=None):
        self.player = player
        self.lib = library
        self.visible_ids = visible_ids or (
            lambda: [s.get("id") for s in library.stations])
        self.on_change = on_change or (lambda station: None)
        # Set by the window: a station's health changed here (marked
        # down), so the list must be redrawn to show it (build 47).
        self.on_health = lambda: None
        # Set by play_another just before it calls play(): the next song
        # of the same station, so no tuning static (build 47).
        self._between_songs = False
        # The YouTube lookup in flight is the next song, not a tune-in.
        self._youtube_quiet = False
        # Set by the window: "this is the song now", for stations that
        # know their own track list rather than waiting for Shazam.
        self.on_track = lambda title, artist, album="", cover="": None
        # Called after next() or previous() moves the station.
        #
        # There are five ways to change station — the buttons, the
        # keyboard, the tray menu, a media key, the skinned player — and
        # anything wired to only one of them is a setting that works
        # sometimes. This is the one place they all pass through.
        self.on_moved = lambda: None
        # Set by the window: a URL to show as this station's artwork.
        self.on_station_art = None
        self.on_notice = on_notice or (lambda text: None)
        self.on_ad_cover = lambda seconds: None
        self.parent = parent            # QObject to own the resolver thread
        self.current = None
        self._back_stack = []
        self._last_local = {}
        self._last_youtube = {}
        self._youtube_worker = None
        # Twitch lookups run on a thread. The sequence number says which
        # request is the current one, so an answer that arrives after you
        # have moved on is ignored instead of started.
        self._twitch_seq = 0
        self._twitch_ready_seq = -1
        self._twitch_station = None
        # Ends the Twitch playlist log (twitchads, build 64) when another
        # station starts or playback stops.
        self._twitch_watch = threading.Event()
        # The playing Twitch stream's (url, headers), for the playlist
        # watch after the channel is opened in the browser (task 88).
        self._twitch_playing = None
        # Same idea for YouTube: stop() bumps it, and the delayed
        # MIN_TUNE_MS re-call checks it before starting the song.
        self._youtube_seq = 0
        self._rng = random.Random()

    # -- playing -----------------------------------------------------------

    def play(self, station):
        # Read once and cleared at once, so it can never leak into the
        # next play() — which may be you pressing a station.
        between = self._between_songs
        self._between_songs = False
        if not station:
            return
        self._twitch_watch.set()
        self._twitch_playing = None
        if self.current and self.current.get("id") != station.get("id"):
            self._back_stack.append(self.current.get("id"))
            self._back_stack = self._back_stack[-20:]

        if station.get("ibroadcast_urls"):
            # Starting the station starts its queue at the top.
            #
            # This reset was sitting in play_another, where it ran before
            # every *next* track — so the queue went to track one, then
            # forward one, and never got past track two. Starting a
            # station is the only time the position should go back.
            station["_ib_at"] = -1
            station["url"] = self._next_ibroadcast_url(station)
            station["_ib_join"] = self._ib_start_fraction(station)
            # Announced at the end, after on_change (task 23). Said here,
            # the station change that follows wiped the track straight
            # away — along with any cover lookup it had started.
            try:
                import crashlog
                shown = station["url"].split("?")[0]
                crashlog.write(
                    f"[ibroadcast] playing {shown} "
                    f"({len(station.get('ibroadcast_urls') or [])} in queue)")
            except Exception:                   # noqa: BLE001
                pass

        settings = self.lib.settings
        self.player.set_stall_timeout(settings.get("stall_timeout", 6))
        self.apply_eq(station)

        # The track is picked once, above.
        #
        # It was picked a second time here. In order that gave the same
        # track, but shuffled it gave a different one — so the log named
        # one song and the player fetched another, and a failure could
        # not be matched to the track that caused it.

        if station.get("kind") == "twitch" and station.get("twitch_channel"):
            self._play_twitch(station)
            return

        if storage.is_youtube_station(station):
            # Static first, then silence the outgoing stream.
            #
            # Resolving takes two network round-trips and the old stream
            # must not run underneath for all of it — but stopping the
            # player also stops the static, so the order matters and stop()
            # is told to leave the noise alone. Started on both sides of
            # the stop, since start() is a no-op when it's already running.
            self._start_youtube(station)
            self.current = station
            storage.record_play(station)
            settings["last_station_id"] = station.get("id")
            self.lib.save_stations()
            self.lib.save_settings()
            self.on_change(station)
            return

        # A short burst of static for a folder of files.
        #
        # A local station starts instantly, which sounds less like tuning
        # in and more like a file opening. Three quarters of a second of
        # noise gives it the same arrival as everything else without
        # delaying anything: the audio starts underneath it and the static
        # fades off the top.
        # Only when you arrive, not between its own tracks.
        #
        # A folder of your own music moving from one song to the next is
        # not tuning anywhere — the static belongs to changing station,
        # and putting it between tracks made a playlist sound like a
        # radio being retuned every three minutes.
        arriving = self.current is not station
        # Local folders only — not YouTube.
        #
        # This faded the static out on a fixed 750ms timer, which is right
        # for a folder: the file is on disk and plays at once. YouTube is
        # also a "custom" station, but its audio has to be found first and
        # takes longer than that — so the static was cut off while the
        # video was still being fetched, leaving silence, and then came
        # back when the video started. YouTube's own path holds the static
        # until the audio is actually there.
        if (arriving and not between and storage.is_custom_station(station)
                and not storage.is_youtube_station(station)
                and not station.get("playlist_url")
                and self.lib.settings.get("noise_between_stations", True)):
            from PyQt5.QtCore import QTimer
            self.player.noise.start()
            QTimer.singleShot(
                750, lambda: self.player.noise.fade_out_and_stop(250))

        url, start_fraction = self._source_for(station)
        if between:
            # The next song starts at the top (task 22).
            #
            # _source_for joins a track partway, which is right when you
            # tune in: the station sounds like it was already on air. But
            # a music folder's next song comes through here too, so every
            # song after the first started somewhere in its middle.
            start_fraction = None
        if station.get("ibroadcast_urls"):
            # Join the first track partway too, not just the ones after
            # it — otherwise the station opens neatly and only starts
            # behaving like a station from song two.
            start_fraction = station.get("_ib_join")
        if not url:
            self.on_notice(
                f"{storage.station_display_name(station)}: no playable audio "
                f"files in {station.get('local_path') or 'that folder'}.")
            return

        ad_seconds, ad_cover = self._ad_cover_for(station)
        # The glass runs for the length of the ad whether or not
        # anything was found to cover it with. The wait is the same either
        # way, and how long it has left is the thing worth knowing —
        # requiring a filler meant it almost never appeared.
        declared = storage.ad_cover_seconds(station)
        if storage.ad_cover_on(station) and declared > 0:
            self.on_ad_cover(declared)

        self.player.play(
            url,
            gain_db=float(station.get("gain_db") or 0.0),
            insecure=bool(station.get("insecure")),
            buffer_seconds=_buffer_for(station),
            crossfade_ms=settings.get("crossfade_ms", 700),
            radio_quality=bool(station.get("radio_quality")),
            start_fraction=start_fraction,
            ad_seconds=ad_seconds,
            ad_cover=ad_cover,
            # No static between two songs of the same station (task 21).
            #
            # play() is also how a music folder moves to its next song,
            # and player.play() starts the static by default — so every
            # song change on a folder had a burst of tuning noise in it.
            use_noise=not between,
        )

        storage.record_play(station)
        settings["last_station_id"] = station.get("id")
        self.lib.save_stations()
        self.lib.save_settings()
        self.current = station
        self.on_change(station)
        if station.get("ibroadcast_urls"):
            self._announce_ibroadcast(station)

    def apply_eq(self, station):
        """Both are pushed every time: the simple one so it's correct if the
        station switches back, and the advanced flag so the player knows
        which of the two to build its filter chain from."""
        self.player.set_equalizer(**storage.station_eq(station))
        self.player.set_advanced_equalizer(
            bool(self.lib.settings.get("eq_advanced")),
            storage.normalize_eq_bands(station.get("eq_bands")))

    def stop(self):
        # Any Twitch lookup still running is now out of date.
        self._twitch_seq += 1
        self._twitch_playing = None
        self._twitch_watch.set()
        # ...and so is any YouTube lookup, including the delayed re-call.
        self._youtube_seq += 1
        if self._youtube_worker is not None:
            try:
                self._youtube_worker.resolved.disconnect()
                self._youtube_worker.failed.disconnect()
            except Exception:           # noqa: BLE001
                pass
            self._youtube_worker = None
        self.player.stop()
        self.on_change(None)

    def _source_for(self, station):
        """(url, start_fraction). Custom stations join a track partway
        through so it sounds like it was already on air."""
        if storage.is_local_station(station):
            track = storage.pick_local_track(
                station.get("local_path"),
                exclude=self._last_local.get(station.get("id")),
                rng=self._rng)
            if not track:
                return None, None
            self._last_local[station.get("id")] = track
            return track, self._rng.uniform(0.05, 0.6)

        return station.get("url"), None

    # -- Twitch stations ---------------------------------------------------

    def _play_twitch(self, station):
        """Look the channel up (on a thread), then play what comes back.

        Twitch stream URLs are signed and expire, so the one stored when
        the station was added is good for minutes rather than days —
        which is why a live channel still refused to connect. It is looked
        up fresh every time, and the lookup is what decides whether the
        channel is live.

        The lookup used to run right here, on the GUI thread. That froze
        the window for its whole length, and worse: the "silence the old
        station in 140 ms" timer set up below could only run once the
        lookup was over — after the new stream had already started — so
        it stopped the stream it was meant to make room for. The channel
        connected and was cut off at once. Now the lookup is on its own
        thread and this method returns straight away.
        """
        import twitchresolver

        self.current = station
        # Counted and remembered like every other kind of station.
        storage.record_play(station)
        self.lib.settings["last_station_id"] = station.get("id")
        self.lib.save_stations()
        self.lib.save_settings()
        self.on_change(station)
        self._twitch_seq += 1
        seq = self._twitch_seq
        self._twitch_station = station

        # Static while it resolves, as with YouTube.
        # Static when you arrive, silence between songs.
        #
        # A live channel is always a fresh tune-in, never "the next one
        # from the same list", so there is no reshuffle case here.
        if self.lib.settings.get("noise_between_stations", True):
            self.player.noise.start()
            self._tune_started = time.monotonic()

            # The old stream keeps playing until the static is audible.
            #
            # Starting the noise means opening a handle and loading a
            # file, which takes a moment — and stopping the stream in the
            # same breath left a hole where neither was playing. That is
            # the silence before the static: not a missing sound, a
            # sound that had not started yet.
            #
            # Only if this request is still the current one and its
            # stream has not started: a stop that lands after the new
            # stream is playing would kill it.
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(140, lambda: self._quiet_old_stream(seq))
        else:
            self._tune_started = 0.0
            self.player.noise.fade_out_and_stop(200)
            self.player.stop(keep_noise=False)

        worker = twitchresolver.TwitchResolver(
            seq, station["twitch_channel"], parent=self.parent)
        worker.done.connect(self._on_twitch_resolved)
        # A jobs.Job keeps itself alive until it has answered, and
        # deletes itself after. Nothing to hold on to here.
        worker.start()
        self.on_notice(f"{station['twitch_channel']}: looking up the stream…")

    def _quiet_old_stream(self, seq):
        """Silence the previous station, unless this tune-in has moved on."""
        if seq != self._twitch_seq or seq == self._twitch_ready_seq:
            return
        self.player.stop(keep_noise=True)

    def _on_twitch_resolved(self, seq, url, reason, headers, art):
        station = self._twitch_station
        # Moved on while it looked: another station, or stop.
        if (seq != self._twitch_seq or station is None
                or self.current is not station):
            return
        channel = station.get("twitch_channel") or ""

        # The channel's own picture becomes the station's artwork, unless
        # a picture has already been chosen by hand.
        if art and not (station.get("custom_art_path") or "").strip():
            station["twitch_art"] = art
            if self.on_station_art is not None:
                self.on_station_art(art)
        try:
            import crashlog
            crashlog.write(f"[twitch] play: url={bool(url)} reason={reason!r}")
        except Exception:                       # noqa: BLE001
            pass

        if not url:
            # Nothing to play. Let go of the static — it was left running
            # here before, so a channel that was offline meant tuning
            # noise forever — and say so in the list.
            self.player.stop(keep_noise=True)
            storage.mark_failed(self.lib.health, station.get("id"),
                                reason or "not live", down_after=1)
            storage.save_health(self.lib.health)
            # Redraw the list so the row turns red now (task 19).
            #
            # Only the health record was changed, and the row's red is
            # read from it when the list is rebuilt — which nothing here
            # asked for. The live watcher would not do it either: it only
            # redraws when a channel's state changes, and by then this
            # had already written "down", so to the watcher nothing had.
            self.on_health()
            self.on_notice(f"{channel}: {reason or 'nothing to play'}")
            self._fade_twitch_static(seq)
            return

        # This request's stream is starting: the 140 ms timer, if it has
        # not fired yet, must leave it alone.
        self._twitch_ready_seq = seq

        ad_seconds, ad_cover = self._ad_cover_for(station)
        declared = storage.ad_cover_seconds(station)
        if storage.ad_cover_on(station) and declared > 0:
            self.on_ad_cover(declared)

        # Debug (task 38): log what the stream's playlist holds while it
        # tunes in — whether the silent start is Twitch's pre-roll ad.
        self._twitch_watch.set()
        self._twitch_watch = threading.Event()
        try:
            import twitchads
            twitchads.TwitchAdWatch(url, headers, self._twitch_watch).start()
        except Exception as exc:                # noqa: BLE001
            self._ad_log(f"[twitch] playlist watch not started: {exc!r}")

        # No video= here: Player.play has no such argument, and passing
        # it raised a TypeError that went nowhere — which is exactly what
        # "adds the station but never plays" looks like. mpv is already
        # started with vid=no, so the video track is never decoded
        # regardless.
        self.player.play(
            url,
            gain_db=float(station.get("gain_db") or 0.0),
            buffer_seconds=_buffer_for(station),
            crossfade_ms=self.lib.settings.get("crossfade_ms", 700),
            radio_quality=bool(station.get("radio_quality")),
            http_headers=headers,
            # A Twitch channel's pre-roll is exactly what the ad cover
            # is for, so it is covered too when the station asks.
            ad_seconds=ad_seconds,
            ad_cover=ad_cover,
            # Task 88: the stream going silent for good (the audio
            # stopped when the channel was opened in the browser) pauses
            # the station, so the play button shows it.
            quiet_stop=True,
        )
        self._twitch_playing = (url, headers)

    def watch_twitch_playlist(self, why, seconds=30):
        """Read the playing Twitch stream's playlist for a while and log
        what it holds (task 88: whether Twitch itself stops sending, or
        sends an ad, after the channel was opened in the browser)."""
        playing = getattr(self, "_twitch_playing", None)
        station = self.current or {}
        if not playing or not (station.get("kind") == "twitch"
                               or station.get("twitch_channel")):
            return
        url, headers = playing
        try:
            import twitchads
            twitchads.TwitchAdWatch(url, headers, self._twitch_watch,
                                    seconds=seconds, why=why).start()
        except Exception as exc:                # noqa: BLE001
            self._ad_log(f"[twitch] playlist watch not started: {exc!r}")

    # The shortest the static may run for an offline channel, and how
    # long it then takes to fade.
    #
    # An offline channel answers in a fraction of a second, so the static
    # began fading almost as soon as it started — before it had properly
    # come up — and sounded cut off rather than faded (task 19).
    TWITCH_MIN_STATIC_MS = 1200
    TWITCH_FADE_MS = 1000

    def _fade_twitch_static(self, seq):
        """Let the static run its minimum, then fade it away."""
        started = getattr(self, "_tune_started", 0.0)
        waited = (time.monotonic() - started) * 1000 if started else 9999
        fade = max(self.TWITCH_FADE_MS,
                   int(self.lib.settings.get("crossfade_ms", 700) or 0))

        def go():
            # Moved on meanwhile: the static now belongs to the next
            # station, and must be left alone.
            if seq != self._twitch_seq:
                return
            self.player.noise.fade_out_and_stop(fade)

        if waited >= self.TWITCH_MIN_STATIC_MS:
            go()
            return
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(int(self.TWITCH_MIN_STATIC_MS - waited), go)

    # How far into a track a station may join it.
    #
    # Capped well short of the end: a station you tune to should sound
    # like it was already playing, not like it is about to change song.
    IB_JOIN_MAX = 0.6

    def _ib_start_fraction(self, station):
        """Where to join a track, as a fraction of its length.

        Only when the station is a shuffle, and only on the track you
        tune in to. A playlist in order is a running order, and every
        track after the first is one you chose to hear — both should
        start where the song starts.
        """
        if not self._shuffles(station):
            return None
        return self._rng.uniform(0.0, self.IB_JOIN_MAX)

    def _announce_ibroadcast(self, station):
        """Say what is playing, from the names stored with the queue.

        The readout drives the artwork lookup as well, so a track with a
        title gets a cover and the station's thumbnail becomes the album
        it is playing — which is what a library station should look like.
        """
        titles = station.get("ibroadcast_titles") or []
        index = station.get("_ib_at", -1)
        if not (0 <= index < len(titles)):
            return
        artist, title, album, cover = (
            list(titles[index]) + ["", "", "", ""])[:4]
        if title:
            # The album travels too: a cover search is far more likely to
            # find the right sleeve with it than without. So does the
            # cover iBroadcast keeps for the track, when it was saved
            # (task 23).
            self.on_track(title, artist, album, cover or "")

    def _shuffles(self, station):
        """One answer for every kind of station with contents."""
        station = station or {}
        if "shuffle_contents" in station:
            return bool(station["shuffle_contents"])
        if "ibroadcast_shuffle" in station:
            return bool(station["ibroadcast_shuffle"])
        return bool(self.lib.settings.get("shuffle_contents", True))

    def _fresh_ibroadcast(self, url, station=None):
        """A saved library URL, signed for right now and at the chosen rate.

        The rate lives in the path — .../128/aa/bb/file.mp3 — so changing
        quality is a matter of rewriting that one segment rather than
        asking the account for new links.
        """
        try:
            import ibroadcast as ib
            # The saved sign-in, as it is right now.
            #
            # This read a setting called "ibroadcast_token" that nothing
            # ever wrote, so it was always empty and every track kept the
            # token it was built with. When that token ran out, the whole
            # station stopped mid-listen. The token file is kept fresh by
            # TokenKeeper; reading it is a file read, safe on this thread.
            token = ib.current_token()
            url = ib.refresh_url(url, token)

            wanted = str((station or {}).get("ibroadcast_bitrate") or "")
            if wanted:
                url = ib.at_bitrate(url, wanted)
            return url
        except Exception:                       # noqa: BLE001
            return url

    def check_url(self, station):
        """The URL the background check should try for this station.

        An iBroadcast station's saved URL carries the token it was last
        played with, which may have run out since — so checking it as
        saved would keep the station marked down however good the
        sign-in is now. Re-signed first; every other station as saved.
        """
        station = station or {}
        url = station.get("url", "")
        if station.get("ibroadcast_urls"):
            return self._fresh_ibroadcast(url, station)
        return url

    def retry_ibroadcast(self, station):
        """The same track again, freshly signed.

        In order, step back one so the track that was refused is the one
        tried again rather than skipped. Shuffled, any track will do.
        """
        if not station or not station.get("ibroadcast_urls"):
            return False
        if not self._shuffles(station):
            station["_ib_at"] = max(-1, station.get("_ib_at", 0) - 1)
        return self.play_another(station)

    def _next_ibroadcast_url(self, station):
        """The next track from a stored iBroadcast queue."""
        urls = station.get("ibroadcast_urls") or []
        if not urls:
            return station.get("url") or ""
        if self._shuffles(station):
            # Keep the index, not just the URL: _announce_ibroadcast reads
            # _ib_at to find the song's name.
            index = self._rng.randrange(len(urls))
            station["_ib_at"] = index
            return self._fresh_ibroadcast(urls[index], station)
        seen = station.get("_ib_at", -1) + 1
        station["_ib_at"] = seen % len(urls)
        return self._fresh_ibroadcast(urls[station["_ib_at"]], station)

    def play_another(self, station):
        """The next track from the same station, chosen at random.

        A YouTube station is a playlist, not a stream: when a video ends
        the station has not finished, it has simply reached the end of one
        song. Random rather than sequential because these are stations —
        you tune to one for the kind of thing it plays, not to hear a
        running order.
        """
        if not station:
            return False
        if storage.is_youtube_station(station):
            # A different entry from the same playlist, at random.
            # Always between songs here, shuffled or not. The static
            # was skipped only when the station shuffled, so a playlist
            # in order got tuning noise after every video (task 21).
            self._start_youtube(station,
                                reshuffle=self._shuffles(station),
                                between_songs=True)
            return True
        if station.get("ibroadcast_urls"):
            # A finished track means the next one, not a dead station.
            url = self._next_ibroadcast_url(station)
            if url:
                # From the beginning.
                #
                # Joining partway is for *tuning in* — it makes a station
                # sound like it was already on air. Once you are listening,
                # the next song starting halfway through is just a song
                # with its first minute missing.
                self.player.play(
                    url,
                    crossfade_ms=self.lib.settings.get("crossfade_ms", 700),
                    gain_db=float(station.get("gain_db") or 0.0),
                    radio_quality=bool(station.get("radio_quality")),
                    # No static between songs.
                    #
                    # play() starts it by default, which is right when
                    # you arrive at a station and wrong between two
                    # tracks of the same one — your own library retuning
                    # itself after every song.
                    use_noise=False,
                )
                self._announce_ibroadcast(station)
                return True

        # Say "handled": the caller moves to another station when this
        # returns falsy, and the next track has already been started here.
        self._between_songs = True
        self.play(station)
        return True

    def _start_youtube(self, station, reshuffle=False, between_songs=False):
        try:
            import crashlog
            crashlog.write(f"[static] -- youtube tune begins "
                           f"(reshuffle={reshuffle}, "
                           f"between songs={between_songs})")
        except Exception:                       # noqa: BLE001
            pass
        """Resolve a video from the playlist and play it.

        `reshuffle` means "another one from the same list" — asked for
        when a track ends, so the resolver picks a different entry rather
        than restarting the one that just finished.
        """
        import os
        import workers

        # Static from the moment it starts trying.
        #
        # The wait is a wait whether it is spent buffering or spent
        # asking YouTube which video to play — and silence during it
        # sounds like nothing happening. The noise runs from here and
        # only stops when music is actually audible.
        # Static when you arrive, silence between songs.
        #
        # Continuing a playlist comes through here too, so the tuning
        # noise was starting at the end of every track — and because the
        # next resolve is cached and instant, the 700ms floor held it
        # there with nothing else to hear.
        #
        # "Between songs" decides it, not "reshuffle": a playlist played
        # in order is not reshuffled, but its next video is still the next
        # song, not a new station (task 21). The answer is remembered so
        # _on_youtube_resolved does not start the static either.
        self._youtube_quiet = bool(between_songs)
        if (not between_songs
                and self.lib.settings.get("noise_between_stations", True)):
            self.player.noise.start()
            self._tune_started = time.monotonic()

            # The old stream keeps playing until the static is audible.
            #
            # Starting the noise means opening a handle and loading a
            # file, which takes a moment — and stopping the stream in the
            # same breath left a hole where neither was playing. That is
            # the silence before the static: not a missing sound, a
            # sound that had not started yet.
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(
                140, lambda: self.player.stop(keep_noise=True))
        else:
            self._tune_started = 0.0
            self.player.noise.fade_out_and_stop(200)
            self.player.stop(keep_noise=False)

        self._youtube_seq += 1          # a delayed re-call of an older one dies
        if self._youtube_worker is not None:
            try:                        # a newer request supersedes this one
                self._youtube_worker.resolved.disconnect()
                self._youtube_worker.failed.disconnect()
            except Exception:           # noqa: BLE001
                pass

        worker = workers.YouTubeResolver(
            station["id"], station.get("playlist_url", ""),
            os.path.dirname(storage.settings_path()),
            # The one that just played is excluded already, which is
            # exactly what "another song from this station" needs.
            exclude_id=self._last_youtube.get(station["id"], ""),
            parent=self.parent)
        worker.resolved.connect(self._on_youtube_resolved)
        worker.failed.connect(self._on_youtube_failed)
        self._youtube_worker = worker
        worker.start()
        self.on_notice(
            f"{storage.station_display_name(station)}: finding a track…")

    # The shortest the static may be heard for.
    #
    # A cached playlist resolves in a few milliseconds, so the noise
    # started and was crossfaded away before it was audible — which is
    # why it only ever seemed to work on the first station, the one with
    # nothing cached. A floor makes the arrival sound the same either
    # way.
    MIN_TUNE_MS = 700


    def _on_youtube_resolved(self, station_id, url, fraction, title,
                             thumbnail=""):
        try:
            import crashlog
            crashlog.write("[static] -- youtube video found, starting audio")
        except Exception:                       # noqa: BLE001
            pass
        if self._youtube_worker is None:
            return                      # Stop was pressed while it resolved
        if not self.current or self.current.get("id") != station_id:
            return                      # the user moved on while it resolved
        station = self.current
        self._last_youtube[station_id] = url.split("?")[0][-24:]

        # If it came back too fast, wait out the rest of the tuning.
        started = getattr(self, "_tune_started", 0.0)
        # No floor when there was no tuning: a track following another
        # should start when it is ready.
        waited = (time.monotonic() - started) * 1000 if started else 9999
        if waited < self.MIN_TUNE_MS:
            from PyQt5.QtCore import QTimer
            seq = self._youtube_seq
            QTimer.singleShot(
                int(self.MIN_TUNE_MS - waited),
                lambda: seq == self._youtube_seq
                and self._on_youtube_resolved(station_id, url, fraction,
                                              title, thumbnail))
            self._tune_started = 0.0
            return

        self.player.play(
            url,
            gain_db=float(station.get("gain_db") or 0.0),
            insecure=bool(station.get("insecure")),
            buffer_seconds=_buffer_for(station),
            crossfade_ms=self.lib.settings.get("crossfade_ms", 700),
            radio_quality=bool(station.get("radio_quality")),
            # The next video starts at the top (task 22). The resolver
            # always picks a join point; only a tune-in should use it.
            start_fraction=None if self._youtube_quiet else fraction,
            # The next song of the same playlist starts without static.
            #
            # player.play() starts the static by default, so even with
            # none during the lookup it came on here, once the video was
            # found — the static between songs in task 21's log.
            use_noise=not self._youtube_quiet,
        )
        # A video's title is the best "now playing" a YouTube station has —
        # there is no ICY metadata to read.
        self.on_resolved(title, thumbnail)

    on_resolved = staticmethod(lambda title, thumbnail: None)

    def _on_youtube_failed(self, station_id, message):
        if self._youtube_worker is None:
            return                      # Stop was pressed; nothing to report
        self.player.noise.fade_out_and_stop(
            self.lib.settings.get("crossfade_ms", 700))
        storage.mark_failed(self.lib.health, station_id, message, down_after=1)
        storage.save_health(self.lib.health)
        self.on_health()
        self.on_notice(message)

    # How many saved frequencies to line up for one ad: the first that
    # makes a sound covers it, the rest are spares (build 64, task 50).
    AD_COVER_TRIES = 3

    def _ad_cover_for(self, station):
        """(seconds, covers) for this station's opening ad.

        `covers` is a list for Player.play(ad_cover=…), tried in order
        until one can be heard. "Cover it with" (Station settings):
          any    — any saved frequency without ads (build 64)
          chosen — the one picked under "Choose frequency…", then any
        Settings saved before build 64 ("music" / "stream") still work.

        Never for a custom station: those have no ads. With nothing to
        cover it with, the static covers the ad.
        """
        if not storage.ad_cover_on(station) or storage.is_custom_station(station):
            return 0.0, None
        seconds = storage.ad_cover_seconds(station)
        if seconds <= 0:
            return 0.0, None

        kind = station.get("ad_filler_kind") or "any"
        chosen_id = station.get("ad_filler_station_id") or ""
        covers = []
        if kind == "stream" and (station.get("ad_filler_url") or "").strip():
            # Before build 64 a stream address could be typed in.
            url = station["ad_filler_url"].strip()
            covers.append({"kind": "stream", "label": url, "path": url,
                           "fraction": None, "insecure": False})
        if chosen_id and kind in ("chosen", "music", "stream"):
            chosen = self.lib.station_by_id(chosen_id)
            cover = self._cover_from(chosen, station) if chosen else None
            if cover:
                covers.append(cover)
            else:
                self._ad_log(f"[ad] the chosen frequency ({chosen_id}) "
                             f"can't cover an ad (gone, down or not "
                             f"playable); trying any other")
        pool = [s for s in self.lib.stations
                if s.get("id") not in (station.get("id"), chosen_id)
                and self.can_cover_ads(s, station)]
        self._rng.shuffle(pool)
        for candidate in pool:
            if len(covers) >= self.AD_COVER_TRIES:
                break
            cover = self._cover_from(candidate, station)
            if cover:
                covers.append(cover)
        # Which "Cover it with" this station has saved, and what that
        # gave (build 71, task 75: a test with one frequency chosen saw
        # three covers and couldn't tell which mode was read).
        names = ", ".join(f"'{c['label']}'" for c in covers) or "none"
        if kind == "chosen" or (chosen_id and kind in ("music", "stream")):
            chosen = self.lib.station_by_id(chosen_id)
            picked = (storage.station_display_name(chosen) if chosen
                      else f"gone ({chosen_id})")
            self._ad_log(f"[ad] cover it with: the frequency you chose, "
                         f"'{picked}' - covers in order: {names} (the "
                         f"others only if it fails)")
        else:
            self._ad_log(f"[ad] cover it with: any frequency without ads "
                         f"- picked at random: {names}")
        if not covers:
            self._ad_log("[ad] no saved frequency can cover the ad "
                         "(none without ads that plays straight away): "
                         "the static covers it")
            # Still covered: Player keeps the static on for the ad.
            covers = [{"kind": "none", "label": "nothing", "path": "",
                       "fraction": None}]
        return seconds, covers

    @staticmethod
    def _ad_log(line):
        try:
            import crashlog
            crashlog.write(line)
        except Exception:                       # noqa: BLE001
            pass

    def can_cover_ads(self, candidate, station=None):
        """Whether `candidate` can cover another station's ad.

        It must play at once: a plain stream or a music folder. Twitch,
        YouTube and iBroadcast need a lookup or a sign-in first. "Without
        ads" means its own "Cover the opening ad" is off. Never the
        station itself (or the same stream), never one marked down.
        Also used by Station settings to fill "Choose frequency…".
        """
        if not candidate:
            return False
        if candidate.get("ad_override"):
            return False
        if station is not None and (
                candidate.get("id") == station.get("id")
                or (candidate.get("url") and
                    candidate.get("url") == station.get("url"))):
            return False
        if (self.lib.health.get(candidate.get("id")) or {}).get("down"):
            return False
        return self._playable_cover(candidate)

    @staticmethod
    def _playable_cover(candidate):
        if storage.is_local_station(candidate):
            return bool(candidate.get("local_path"))
        if storage.is_custom_station(candidate):
            return False                        # YouTube
        if candidate.get("kind") == "twitch" or candidate.get("twitch_channel"):
            return False
        if candidate.get("ibroadcast_urls") or candidate.get("playlist_url"):
            return False
        return bool(candidate.get("url"))

    def _cover_from(self, candidate, station):
        """One cover for Player, or None if this frequency can't give one."""
        if not candidate or not self._playable_cover(candidate):
            return None
        if candidate.get("id") == station.get("id"):
            return None
        label = storage.station_display_name(candidate)
        if storage.is_local_station(candidate):
            track = storage.pick_local_track(candidate.get("local_path"),
                                             rng=self._rng)
            if not track:
                return None
            return {"kind": "music", "label": label, "path": track,
                    "fraction": self._rng.uniform(0.05, 0.6),
                    "insecure": False}
        return {"kind": "stream", "label": label, "path": candidate["url"],
                "fraction": None,
                "insecure": bool(candidate.get("insecure"))}

    # -- queue -------------------------------------------------------------

    def next(self):
        ids = [i for i in self.visible_ids() if i]
        if not ids:
            return
        here = self.current.get("id") if self.current else None
        if self.lib.settings.get("shuffle") and len(ids) > 1:
            pick = self._rng.choice(self._shuffle_pool(ids, here) or
                                    [i for i in ids if i != here] or ids)
        elif ids == [here]:
            pick = here                  # the only one: start it again
        else:
            # In list order, passing over stations marked down (task 81).
            pick = self._step_past_down(ids, here, +1, "Next")
            if pick is None:
                return
        self.play(self.lib.station_by_id(pick))
        self.on_moved()

    def _is_down(self, station_id):
        entry = self.lib.health.get(station_id) or {}
        return bool(entry.get("down"))

    def _step_past_down(self, ids, here, step, button):
        """The next station in `step`'s direction that isn't marked down.

        Shuffle off only (task 81). One lap at most: back at the station
        you started from — or round the whole list, when nothing in it is
        playing — means every other station is down, and then nothing
        moves. A station marked down is looked at again in the background
        every few minutes, and comes back on its own once it answers.
        """
        n = len(ids)
        pos = ids.index(here) if here in ids else (-1 if step > 0 else n)
        passed = []
        for k in range(1, n + 1):
            candidate = ids[(pos + k * step) % n]
            if candidate == here:
                break
            station = self.lib.station_by_id(candidate)
            if station is None:
                continue
            if self._is_down(candidate):
                passed.append(storage.station_display_name(station))
                continue
            if passed:
                self._skip_log(button, passed, station)
            return candidate
        self._skip_log(button, passed, None)
        if here is None:
            self.on_notice(f"Every station is marked down, so {button} "
                           "did nothing.")
        else:
            self.on_notice("Every other station is marked down, so "
                           f"{button} stayed where it was.")
        return None

    @staticmethod
    def _skip_log(button, passed, landed):
        shown = ", ".join(f"'{name}'" for name in passed[:8])
        if len(passed) > 8:
            shown += f" and {len(passed) - 8} more"
        if landed is None:
            line = (f"[skip] {button}: no station up - passed {len(passed)} "
                    f"marked down ({shown}); stayed put")
        else:
            if isinstance(landed, dict):
                landed = f"'{storage.station_display_name(landed)}'"
            line = (f"[skip] {button}: passed {len(passed)} marked down "
                    f"({shown}) -> {landed}")
        try:
            import crashlog
            crashlog.write(line)
        except Exception:                       # noqa: BLE001
            pass

    def checkable(self, station):
        """Whether the background check should try this station.

        An iBroadcast station with no sign-in behind it will always fail,
        and a check that always fails is noise — it marks the station down
        for a reason that has nothing to do with the station.
        """
        station = station or {}
        if station.get("kind") == "twitch":
            # Its own watcher decides this. Fetching a Twitch URL proves
            # nothing: an offline channel answers perfectly well, with a
            # page that is not a stream.
            return False
        if not station.get("ibroadcast_urls"):
            return True
        try:
            import ibroadcast
            return bool(ibroadcast.load_tokens().get("refresh_token"))
        except Exception:                       # noqa: BLE001
            return False

    def _shuffle_pool(self, ids, here):
        """Stations shuffle is allowed to land on.

        Everything except what is known to be broken.

        This used to require proof a station worked — played before, or
        checked and found up — which sounds careful and is wrong. Import
        three hundred stations and none of them has been played, so
        shuffle circled the two or three that had. A station nobody has
        tried yet is not a bad station; it is the entire point of having
        imported it.

        Only stations actually marked down are skipped, and even those
        come back if that is all there is.
        """
        others = [i for i in ids if i != here]
        pool = []
        for station_id in others:
            if self.lib.station_by_id(station_id) is None:
                continue
            health = storage.health_for(self.lib.health, station_id) or {}
            if health.get("down"):
                continue
            pool.append(station_id)
        # Everything marked down is still better than refusing to move.
        return pool or others

    def previous(self):
        """Always the station played before this one, shuffle or not.

        With shuffle off, a station played before that is now marked
        down is passed over for the one before it (task 81).
        """
        skip_down = not self.lib.settings.get("shuffle")
        passed = []
        while self._back_stack:
            station = self.lib.station_by_id(self._back_stack.pop())
            if station and skip_down and self._is_down(station.get("id")):
                passed.append(storage.station_display_name(station))
                continue
            if station:
                if passed:
                    self._skip_log("Previous", passed, station)
                self.current = None      # don't push this one back on
                self.play(station)
                self.on_moved()
                return
        if passed:
            self._skip_log("Previous", passed, "the list's next station")
        self.next()                      # which reports for itself
