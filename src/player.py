"""
player.py - Everything that touches libmpv (via python-mpv).

Three separate mpv uses live here:

  Player       the actual radio playback, plus the audio filter chain
  NoisePlayer  a second, tiny mpv instance that plays synthesized radio
               static while a station is connecting, so switching
               stations sounds like tuning a radio instead of silence
  capture_stream_to_wav()
               opens a stream just long enough to write a few seconds of
               mono audio to a WAV, which shazam.py fingerprints

Sound leveling:
  dynaudnorm continuously adapts gain so quiet and loud stations end up
  at a similar perceived loudness without the pumping a plain compressor
  gives you. On top of that there's an optional 3-band EQ, and an
  optional per-station "vocal clarity" chain for talk radio.
"""
import ctypes.util
import locale
import random
import os
import re
import sys
import tempfile
import threading
import time

# When running from the AppImage, AppRun sets STREAMDECK_LIBMPV_PATH to the
# absolute path of the bundled libmpv.so so we don't depend on the host
# system having libmpv registered with ldconfig (ctypes.util.find_library
# does not reliably honor LD_LIBRARY_PATH on its own). Outside the
# AppImage (normal dev run), this is a no-op and the system libmpv is
# found the usual way.
_bundled_libmpv = os.environ.get("STREAMDECK_LIBMPV_PATH")
if _bundled_libmpv and os.path.exists(_bundled_libmpv):
    _orig_find_library = ctypes.util.find_library

    def _patched_find_library(name, _orig=_orig_find_library, _bundled=_bundled_libmpv):
        if name == "mpv":
            return _bundled
        return _orig(name)

    ctypes.util.find_library = _patched_find_library

import mpv

import playlists

MAX_VOLUME = 100


# What the app calls itself to stream servers (task 162).
#
# mpv's own name ("libmpv") is one some servers treat specially: Nightwave
# Plaza sends it to an HLS playlist that never plays, while every other
# player gets the MP3 it asked for. So every handle that fetches a station
# says who it is, as the health check already did. A station's own
# User-Agent header (Twitch) still wins: ffmpeg sends that one instead.
STREAM_AGENT = "Frequencies/1.5"


def system_ca_file():
    """The system's bundle of trusted certificates, wherever it lives.

    mpv's TLS library looks for it where the build machine keeps it.
    Built on Fedora and run on Ubuntu, that is the wrong folder, so no
    https stream could be verified and every one of them failed at once
    (task 162, the Ubuntu VM). Naming the file outright works on all of
    them.
    """
    for path in (os.environ.get("SSL_CERT_FILE"),
                 "/etc/ssl/certs/ca-certificates.crt",       # Debian, Ubuntu, Arch
                 "/etc/pki/tls/certs/ca-bundle.crt",         # Fedora
                 "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
                 "/etc/ssl/ca-bundle.pem",                   # openSUSE
                 "/etc/ssl/cert.pem"):
        if path and os.path.isfile(path):
            return path
    return None


# Every handle that fetches a station: who it is, and what to trust.
_TLS_CA = system_ca_file()
STREAM_OPTS = {"user_agent": STREAM_AGENT,
               **({"tls_ca_file": _TLS_CA} if _TLS_CA else {})}

# Every handle, on Windows: none of mpv's built-in Lua scripts (stats,
# console, menus). The app uses none of them, and there each Lua error
# they catch is a Windows exception that faulthandler writes to the log
# as a "fatal exception" with every thread's stack, dozens per tune.
NO_SCRIPTS = ({"load_stats_overlay": False, "load_console": False,
               "load_auto_profiles": False, "load_select": False,
               "load_commands": False, "load_positioning": False}
              if sys.platform == "win32" else {})


def force_c_numeric_locale():
    """Make sure LC_NUMERIC is "C" before we talk to libmpv.

    libmpv refuses to start under any other numeric locale: mpv_create()
    prints "Non-C locale detected. This is not supported." and returns
    NULL, and the next call straight into libmpv segfaults on the null
    handle.

    python-mpv sets LC_NUMERIC="C" for us, but only once, at `import mpv`
    time -- and Qt undoes it, because constructing a QApplication calls
    setlocale(LC_ALL, "") to pick up the user's system locale. So on any
    machine that isn't already in the "C" locale, the sequence

        import mpv            -> LC_NUMERIC = C
        QApplication(argv)    -> LC_NUMERIC = en_US.UTF-8   (clobbered!)
        mpv.MPV(...)          -> crash

    is guaranteed to crash. Calling this right before creating any mpv
    handle (and again after QApplication is built, in main.py) closes
    that window.
    """
    try:
        if locale.setlocale(locale.LC_NUMERIC) not in ("C", "C.UTF-8", "C.utf8"):
            locale.setlocale(locale.LC_NUMERIC, "C")
    except locale.Error:
        # Some very stripped-down environments can reject even "C";
        # nothing useful to do but continue.
        pass


# Junk that some encoders bolt onto the title: a run of key="value"
# pairs, often malformed, carrying store IDs nobody wants to read.
_TAG_PAIR = re.compile(r"""\s*[\w{}\[\]]+\s*=\s*"[^"]*\"""", re.VERBOSE)
_BARE_KEY = re.compile(r"""\s*[\w{}\[\]]+\s*=\s*\S*""")


def clean_stream_title(raw):
    """A readable song line from whatever the stream sent.

    Some encoders append their whole tag block to the title, so what
    arrives is

        Dracula" artist="TAME IMPALA" {="0" itunesTrackId=" )" amgTrackI...

    which is a title, then a run of key="value" pairs that were never
    meant to be displayed. The useful part is the title and the artist;
    everything else is metadata plumbing.

    Nothing is invented here. If the line has no recognisable structure
    it comes back as it arrived, minus the obvious rubbish.
    """
    if not raw:
        return ""

    text = raw.strip()

    # Pull out artist and title if they are in there as tags, before
    # the tag block is stripped away.
    fields = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', text))
    artist = (fields.get("artist") or "").strip()

    # The title is whatever precedes the first tag pair - that is where
    # these encoders put it - or an explicit title= field.
    head = re.split(r'\s*[\w{}\[\]]+\s*=\s*"', text, maxsplit=1)[0]
    title = (fields.get("title") or head).strip().strip('"').strip()

    if artist and title:
        return f"{artist} - {title}"
    if title and len(title) < len(text):
        return title

    # No tags to speak of: strip any trailing key=value debris and
    # return what is left.
    cleaned = _TAG_PAIR.sub("", text)
    cleaned = _BARE_KEY.sub("", cleaned).strip().strip('"').strip()
    return cleaned or text


class PlayerState:
    STOPPED = "stopped"
    CONNECTING = "connecting"
    PLAYING = "playing"
    ERROR = "error"
    # The stream was fine but the audio device wasn't - kept separate so
    # a busy sound card doesn't get every station flagged as broken.
    NO_AUDIO = "no-audio"


# ----------------------------------------------------------------------
# Filter chain construction
# ----------------------------------------------------------------------
def build_filter_chain(normalize=True, bass=0, mid=0, treble=0,
                      gain_db=0.0, radio_quality=False, bands=None,
                      meter=False, **_retired):
    """Assemble the mpv "af" string from the current sound settings.

    `**_retired` swallows vocal_clarity, enhance and safe_enhance. They
    were removed rather than deprecated — enhance generated audible
    harmonics and vocal clarity was a preset nobody could hear working —
    and accepting the arguments silently means an old caller does not
    crash on its way to being updated.
    """
    """Assemble the mpv "af" string from the current sound settings.

    Kept as a free function so it can be unit-tested without spinning up
    an actual mpv instance.
    """
    filters = []

    if normalize:
        # f=frame length(ms), g=gaussian smoothing window,
        # p=target peak (0.95 leaves a little headroom).
        filters.append("lavfi=[dynaudnorm=f=200:g=15:p=0.95]")

    if radio_quality:
        # Make it sound like it came out of a radio.
        #
        # Four things, in the order a real signal chain does them:
        #
        #   bandpass    - broadcast audio is band-limited. FM runs to
        #                 about 15 kHz, but the receiver, the speaker
        #                 and the transmission all narrow it further;
        #                 250 Hz - 6.5 kHz is the range that reads as
        #                 "radio" without becoming a telephone.
        #   presence    - a lift around 2 kHz, the forward midrange
        #                 broadcast processors are known for.
        #   compression - stations run heavy compression so everything
        #                 sits at the same loudness. This is what makes
        #                 radio sound "flat and loud" more than the EQ.
        #   saturation  - a touch of soft clipping for the analogue
        #                 edge; too much and it just sounds broken.
        # Opened up, and far less squashed.
        #
        # 250 Hz-6.5 kHz with 4:1 compression is a telephone impression,
        # not a radio: the band was narrow enough to hollow out bass
        # guitar and the ratio flattened every dynamic in the music. Real
        # broadcast processing is wideband and heavy on *loudness*, not on
        # gain reduction you can hear pumping.
        #
        #   120 Hz-11 kHz keeps the bottom octave and the cymbals
        #   2:1 at a lower threshold levels without crushing
        #   a slower attack lets transients through before it clamps
        filters.append(
            "lavfi=[highpass=f=120:poles=1,"
            "lowpass=f=11000:poles=1,"
            "equalizer=f=2200:width_type=o:width=1.8:g=2.5,"
            "acompressor=threshold=-14dB:ratio=2:attack=20:release=250:"
            "makeup=1.5,"
            "asoftclip=type=atan:threshold=0.94]"
        )

    if bands and any(bands):
        # Ten one-octave peaking filters at the ISO centers. Takes the
        # place of bass/mid/treble entirely rather than stacking with
        # it: a band boosted by both is a band nobody can reason about.
        import storage as _storage
        parts = []
        for hz, gain in zip(_storage.EQ_BAND_FREQUENCIES,
                            _storage.normalize_eq_bands(bands)):
            if gain:
                parts.append(
                    "equalizer=f=%d:width_type=o:width=1:g=%d" % (hz, gain)
                )
        if parts:
            filters.append("lavfi=[%s]" % ",".join(parts))
    elif bass or mid or treble:
        filters.append(
            "lavfi=[equalizer=f=100:width_type=o:width=2:g={0},"
            "equalizer=f=1000:width_type=o:width=2:g={1},"
            "equalizer=f=8000:width_type=o:width=2:g={2}]".format(bass, mid, treble)
        )

    # The per-station trim goes LAST, after normalization.
    #
    # It used to sit first, on the reasoning that a ducked station
    # should stay ducked. That was exactly backwards: erasing level
    # differences is precisely dynaudnorm's job, so it pulled the gain
    # straight back out again. Measured, a +/-12 dB trim ahead of
    # dynaudnorm moved the actual output by 1.4 dB in total; placed
    # after it, the full +/-12 dB survives.
    if gain_db:
        filters.append("lavfi=[volume={0:+.1f}dB]".format(float(gain_db)))

    # The level meter goes LAST, so it hears what goes out. dynaudnorm
    # holds about 3 s of audio back to look ahead; a meter in front of
    # it heard the sound up to 3 s before you did, and the static faded
    # into silence again (found testing build 64). See LEVEL_FILTER.
    if meter:
        filters.append(LEVEL_FILTER)

    return ",".join(filters)


# ----------------------------------------------------------------------
# Between-station radio static
# ----------------------------------------------------------------------
def static_dir():
    """Where the bundled static recordings live.

    Next to the source inside the AppImage; falls back to a sibling
    directory when running from a checkout.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(here, "static"),
                      os.path.join(os.path.dirname(here), "static")):
        if os.path.isdir(candidate):
            return candidate
    return ""


def _lavfi_escape(path):
    """Paths go inside a filter graph, where ':' and '\\' are syntax."""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _static_log(event):
    """Note what touched the static, and from where.

    The silence in the middle of a YouTube tune has survived three fixes
    that each removed a real cause, so the remaining one is not visible
    from reading the code. This writes every start, stop and fade with
    the two calls that led to it, so a single log shows who cut it.
    """
    try:
        import traceback
        import crashlog
        stack = traceback.extract_stack()[-4:-2]
        where = " <- ".join(f"{os.path.basename(f.filename)}:{f.lineno}"
                            f" {f.name}" for f in reversed(stack))
        crashlog.write(f"[static] {event:<14} {where}")
    except Exception:                           # noqa: BLE001
        pass


class NoisePlayer:
    """Real radio static, played while a station connects.

    This plays actual recordings of a receiver between stations -
    hiss, dial sweeps, carrier flips - rather than anything synthesized.
    All the clips were loudness-matched when they were bundled, so no
    single one jumps out when it comes round.

    Authenticity comes from never playing them the same way twice:

      - two or three clips are mixed together at once, at randomized
        relative levels, the way real interference layers up
      - each starts from a random point in the file, so you never hear
        the same "take" begin twice
      - the whole mix is re-rolled every couple of seconds while it
        plays, which is what makes it sound like a dial being turned
        rather than a loop

    The mixing is done by libavfilter's amovie sources through mpv's
    lavfi input, so it's still one small mpv instance and no decoding
    work of our own.
    """

    RESHUFFLE_MS = (1600, 3200)

    # Clips that work as a continuous bed, versus ones that are events
    # (a flip, a sweep landing). At least one bed always plays; events
    # are layered on top sometimes.
    BEDS = ("hiss", "dial", "tuning", "search")
    EVENTS = ("flip", "sweep1", "sweep2")

    def __init__(self):
        self._mpv = None
        self._volume = 35           # the static's own level, 0-100
        self._master = 100          # the app's volume, as a ceiling
        self._lock = threading.Lock()
        self._active = False
        self._timer = None
        self._clips = self._find_clips()
        self._fade_thread = None
        self._fade_cancel = threading.Event()
        self._fade_target = self._volume
        # The level last asked for (the static's own scale, before the
        # master), so a fade starts from it without asking mpv.
        self._level = float(self._volume)
        # The static's mpv calls run on this, never on the window's
        # thread (build 69, task 61). Made on first use.
        self._queue = None
        # Settings > Audio output, as for the station (task 83).
        self._device = "auto"

    def _find_clips(self):
        directory = static_dir()
        clips = {}
        if not directory:
            return clips
        for name in self.BEDS + self.EVENTS:
            path = os.path.join(directory, name + ".ogg")
            if os.path.exists(path):
                clips[name] = path
        return clips

    @property
    def available(self):
        return bool(self._clips)

    def _duration_guess(self, name):
        """Rough lengths, only used to pick a sane random seek point.
        Overshooting just means starting near the end, which loops."""
        return {"hiss": 7, "flip": 14, "sweep2": 28, "sweep1": 43,
                "search": 72, "tuning": 165, "dial": 207}.get(name, 10)

    def _build(self):
        """One randomized mix of the real recordings."""
        if not self._clips:
            return None

        beds = [n for n in self.BEDS if n in self._clips]
        events = [n for n in self.EVENTS if n in self._clips]
        if not beds:
            beds = list(self._clips)

        chosen = [random.choice(beds)]
        # Usually layer a second bed, and sometimes an event on top.
        if len(beds) > 1 and random.random() < 0.75:
            second = random.choice([b for b in beds if b != chosen[0]])
            chosen.append(second)
        if events and random.random() < 0.45:
            chosen.append(random.choice(events))

        parts, labels, weights = [], [], []
        for i, name in enumerate(chosen):
            seek = round(random.uniform(0, max(1, self._duration_guess(name) - 2)), 2)
            label = f"s{i}"
            # loop=0 means loop forever, so a short clip still fills the
            # whole connect.
            parts.append(
                f"amovie='{_lavfi_escape(self._clips[name])}':loop=0:"
                f"seek_point={seek}[{label}]"
            )
            labels.append(f"[{label}]")
            weights.append("1" if i == 0 else f"{random.uniform(0.35, 0.85):.2f}")

        if len(chosen) == 1:
            graph = parts[0].rsplit("[", 1)[0]
            return "av://lavfi:" + graph

        graph = ";".join(parts) + ";" + "".join(labels)
        graph += (f"amix=inputs={len(chosen)}:duration=first:"
                  f"weights={' '.join(weights)}")
        # amix divides by the input count, so bring the level back up -
        # modestly, then hard-limit. Two loud moments landing together
        # would otherwise clip, which sounds like a fault rather than
        # like radio.
        graph += f",volume={1.0 + 0.28 * (len(chosen) - 1):.2f}"
        graph += ",alimiter=limit=0.92:attack=5:release=50"
        return "av://lavfi:" + graph

    def _put(self, label, fn):
        """Queue an mpv call for the static (task 61).

        Every call to the static's own mpv runs on one thread of its own,
        in order, so the window never waits for it: not for a loadfile,
        and not for the lock a reshuffle used to hold across one (a
        station change could wait for the static to re-roll its mix).
        """
        queue = self._queue
        if queue is None:
            queue = self._queue = MpvQueue("StaticQueue")
        queue.put(label, fn)

    def warm_up(self):
        """Build the mpv instance before it is needed.

        Creating one takes long enough to hear. Because it happened on
        the first call to start(), the static arrived late at exactly
        the moment it was meant to be covering - the gap it exists to
        fill was the gap it was waiting on. On the static's queue since
        build 69, so the window doesn't wait for it either.
        """
        if not self._clips:
            return
        self._put("static warm-up", self._ensure_quietly)

    warm = warm_up

    def _ensure_quietly(self):
        try:
            self._ensure()
        except Exception:                       # noqa: BLE001
            pass            # a missing audio device is not worth a crash

    def _ensure(self):
        """The static's mpv handle. Queue thread only."""
        if self._mpv is None:
            force_c_numeric_locale()
            self._mpv = mpv.MPV(
                vid="no",
                vo="null",
                video=False,
                ytdl=False,
                volume=self._volume,
                idle=True,
                # Names the PulseAudio stream after the app rather than
                # after mpv.
                #
                # A desktop's task manager scrolls the volume of whichever
                # stream belongs to the window under the pointer, and it
                # matches them up by application name. Left as "mpv", the
                # stream belonged to a program with no window, so
                # scrolling the taskbar entry had nothing to act on.
                audio_client_name="Frequencies",
                title="Frequencies",
                # The output chosen in Settings, not the desktop's
                # default (task 83).
                audio_device=self._device,
                **NO_SCRIPTS,
            )
        return self._mpv

    def set_audio_device(self, name):
        """Settings > Audio output, for the static too (task 83).

        Kept for a handle made later, and written to the one there is,
        on the static's own queue.
        """
        self._device = name or "auto"

        def write(device=self._device):
            if self._mpv is not None:
                self._mpv["audio-device"] = device
        if self._clips:
            self._put("static audio-device", write)

    def set_master(self, value):
        """The app's volume, applied on top of the static's own.

        The two are separate settings for a reason — static matched to the
        music is deafening — but turning the app down should turn
        everything down, and the noise was ignoring the slider entirely.
        """
        self._master = max(0, min(MAX_VOLUME, int(value)))
        with self._lock:
            self._set_volume_now(self._level)

    def _effective(self):
        """The static's level scaled by the master, as a fraction of it."""
        return int(self._volume * (self._master / 100.0))

    def set_volume(self, value):
        self._volume = max(0, min(MAX_VOLUME, int(value)))
        with self._lock:
            self._set_volume_now(self._volume)

    def start(self, fade_ms=0):
        _static_log("start")
        """Begin the static, at full level immediately.

        No fade in. The static is what covers a gap, so it has to be
        there the instant the gap opens - fading it up just moves the
        silence rather than filling it. `fade_ms` is accepted and
        ignored so callers can stay symmetrical with
        fade_out_and_stop().

        Calling this while the static is already running does NOT
        re-roll the mix. Skipping through several stations in a row used
        to restart it each time, and because a fade-out may have been
        part-way through, the level dipped and recovered on every press -
        the static appearing to cross-fade into itself. It now simply
        stays running, at full level.
        """
        if not self._clips:
            return
        with self._lock:
            # Any fade in flight is cancelled first, every time.
            #
            # A fade-out leaves the handle at volume zero and then stops
            # it. Starting again reused that handle, and the level it was
            # left at, so the static ran silently from the second station
            # onward — audible the first time and never again.
            self._fade_cancel.set()
            already_running = self._active
            self._active = True
            if already_running:
                # Come straight back to full, without touching the clips
                # that are playing.
                self._set_volume_now(self._volume)
                return
            # The play sets the level itself, whatever the handle was
            # left holding.
            self._play_new(start_volume=self._volume)
        self._schedule_reshuffle()

    def _fade_to(self, target, ms, then_stop=False):
        """Ramp the static's volume over `ms`, on a background thread.

        A hard cut between static and a stream is the one thing that
        gives away that these are separate players - a real receiver
        never switches instantly. Twenty steps is enough to sound
        continuous without spinning the CPU. The thread only paces the
        steps; each one is a write on the static's queue (build 69).
        """
        if self._fade_thread is not None and self._fade_thread.is_alive():
            self._fade_cancel.set()
            self._fade_thread.join(timeout=0.5)
        self._fade_cancel = threading.Event()
        cancel = self._fade_cancel

        start = self._current_volume()
        steps = max(1, int(ms / 35))

        def run():
            for i in range(1, steps + 1):
                value = start + (target - start) * (i / steps)
                with self._lock:
                    if cancel.is_set():
                        return
                    if not self._active and not then_stop:
                        return
                    self._set_volume_now(value, cancel)
                time.sleep(ms / 1000.0 / steps)
            if then_stop:
                self._fade_stop(cancel)

        self._fade_thread = threading.Thread(target=run, daemon=True,
                                             name="StaticFade")
        self._fade_thread.start()

    def _fade_stop(self, cancel):
        """The end of a fade-out: stop, unless start() came in first.

        Checked and done under the lock, so a start() can't slip in
        between "not cancelled" and the stop and be stopped by it.
        """
        with self._lock:
            if cancel.is_set():
                return
            _static_log("stop")
            self._stop_locked()
            # Leave the level where the next start() expects it.
            self._set_volume_now(self._volume)

    def fade_out_and_stop(self, ms):
        """Blend the static away rather than cutting it off."""
        if not self._active:
            # Nothing playing, so nothing to fade or stop. Every song
            # change of an iBroadcast or music folder station came here
            # (the hand-over to the next song) and stopped an idle mpv
            # on the window's thread, with two log lines each time.
            return
        _static_log(f"fade out {ms}ms")
        if not ms:
            self.stop()
            return
        self._fade_to(0, ms, then_stop=True)

    def _current_volume(self):
        """The level last asked for — never read back from mpv, which
        would wait for its lock (build 69)."""
        return float(self._level)

    def _set_volume_now(self, value, cancel=None):
        """Caller holds the lock. Queues the write.

        Every level set here is scaled by the master, so a fade lands at
        the figure the slider allows rather than at the static's own.
        A fade step passes its `cancel`: a step still queued when the
        fade was cancelled (start() came back) is skipped.
        """
        self._level = float(value)
        scaled = max(0, min(100, int(round(
            float(value) * (getattr(self, "_master", 100) / 100.0)))))

        def write():
            if cancel is not None and cancel.is_set():
                return
            if self._mpv is not None:
                self._mpv.volume = scaled
        self._put("static volume", write)

    def _play_new(self, start_volume=None):
        """Caller holds the lock. Queues a new mix."""
        source = self._build()
        if not source:
            return
        # Scaled here as well: a fresh handle was being built with
        # the static's own level, so every restart of the noise — and
        # it restarts on every station change — ignored the slider
        # until something else happened to set the volume again.
        wanted = (self._volume if start_volume is None
                  else float(start_volume))
        self._level = float(wanted)
        scaled = max(0, min(100, int(round(
            wanted * (getattr(self, "_master", 100) / 100.0)))))

        def play():
            try:
                handle = self._ensure()
                handle.volume = scaled
                handle.play(source)
            except Exception:                   # noqa: BLE001
                # Static is a nicety; never let it break playback.
                pass
        self._put("static play", play)

    def _schedule_reshuffle(self):
        if self._timer is not None:
            self._timer.cancel()
        delay = random.uniform(*self.RESHUFFLE_MS) / 1000.0
        self._timer = threading.Timer(delay, self._reshuffle)
        self._timer.daemon = True
        self._timer.start()

    def _reshuffle(self):
        with self._lock:
            if not self._active:
                return
            # Re-roll at full level. The mix changing is meant to sound
            # like a dial moving, not like the static ducking to let
            # another take of itself in - so no fade between mixes.
            #
            # The exception is a fade-out already in progress: match
            # wherever it has got to, so handing over to the stream
            # stays smooth. The lock is only held while the new mix is
            # queued, never across the loadfile itself (task 61).
            fading_out = (self._fade_thread is not None
                          and self._fade_thread.is_alive())
            self._play_new(
                start_volume=self._current_volume() if fading_out
                else self._volume
            )
        self._schedule_reshuffle()

    def stop(self):
        _static_log("stop")
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        self._active = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._queue is None:
            return                  # never started: no handle to stop

        def halt():
            if self._mpv is not None:
                self._mpv.stop()
        self._put("static stop", halt)

    def shutdown(self):
        self.stop()
        self._fade_cancel.set()
        queue = self._queue
        if queue is None:
            return

        def terminate():
            if self._mpv is not None:
                try:
                    self._mpv.terminate()
                except Exception:               # noqa: BLE001
                    pass
                self._mpv = None
        queue.put("static shutdown", terminate)
        # What was queued before (the stop) runs first; then the queue's
        # thread ends. Up to 2 s, as the station's own queue gets.
        queue.wait_idle(2.0)
        queue.close(1.0)


# ----------------------------------------------------------------------
# Capturing audio for song identification
# ----------------------------------------------------------------------
class CaptureCancel:
    """Lets another thread stop a capture_stream_to_wav early (at quit).

    The lock makes "stop the recorder" and "the recorder is finished and
    gone" mutually exclusive: an mpv command sent to a handle that has
    already been terminated is a crash, not an error (build 54).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._encoder = None
        self.cancelled = False

    def attach(self, encoder):
        """The capture has started. False if it was cancelled already."""
        with self._lock:
            if self.cancelled:
                return False
            self._encoder = encoder
            return True

    def detach(self):
        with self._lock:
            self._encoder = None

    def cancel(self):
        """Any thread. Stops the recording if one is running."""
        with self._lock:
            self.cancelled = True
            encoder = self._encoder
            if encoder is not None:
                try:
                    encoder.stop()      # ends playback: the wait returns
                except Exception:       # noqa: BLE001
                    pass


def capture_stream_to_wav(url, seconds=6, out_path=None, start_seconds=None,
                          cancel=None):
    """Record `seconds` of a live stream to a mono 16 kHz WAV.

    Uses a throwaway mpv instance in encode mode with a null audio
    output, so nothing is audible and the user's playback is untouched.
    Returns the path on success, raises RuntimeError otherwise.
    `cancel` (a CaptureCancel) lets another thread stop it early.
    """
    if out_path is None:
        handle, out_path = tempfile.mkstemp(prefix="sdr-capture-", suffix=".wav")
        os.close(handle)
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except OSError:
            pass

    # A .pls or .m3u URL is a text file listing streams, not audio.
    # mpv resolves those transparently when it is playing normally,
    # which is why such stations play perfectly - but this encode-mode
    # instance ends up producing an empty WAV, and identification failed
    # with "captured audio was empty". Resolve it to the real stream
    # first. Falls back to the original URL if anything goes wrong.
    url = playlists.resolve_stream_url(url)

    force_c_numeric_locale()
    encoder = None
    try:
        encoder = mpv.MPV(
            vid="no",
            video=False,
            ytdl=False,
            ao="null",
            o=out_path,
            of="wav",
            oac="pcm_s16le",
            audio_channels="mono",
            audio_samplerate=16000,
            length=seconds,
            cache=True,
            **STREAM_OPTS,
            **NO_SCRIPTS,
            # Where to capture from.
            #
            # A live stream has no meaningful position - you always get
            # "now" - but a custom station is playing a finite track, so
            # capturing from the top would fingerprint the opening of
            # the song no matter how far in the listener actually is.
            # Starting at the current playback position identifies what
            # is being heard.
            **({"start": start_seconds} if start_seconds else {}),
        )
        encoder.play(url)
        # Attached after play(): a stop sent before play() would be lost.
        if cancel is not None and not cancel.attach(encoder):
            raise RuntimeError("cancelled")
        encoder.wait_for_playback()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Couldn't capture audio: {exc}") from exc
    finally:
        if cancel is not None:
            cancel.detach()             # before terminate, under its lock
        if encoder is not None:
            try:
                encoder.terminate()
            except Exception:
                pass

    if cancel is not None and cancel.cancelled:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise RuntimeError("Capture canceled.")

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 4096:
        raise RuntimeError("Captured audio was empty.")
    return out_path


# ----------------------------------------------------------------------
# Hearing whether a stream makes a sound (build 64, tasks 38 and 50)
# ----------------------------------------------------------------------
# A level meter at the end of a filter chain. ffmpeg's astats
# writes the loudness of every chunk of audio into the chunk's metadata,
# and mpv hands the latest one out as the property `af-metadata/<label>`
# (watched, never asked for). It comes before mpv's volume, so a stream
# held at volume 0 under an ad cover still shows how loud it really is.
#
# Why: "the position moved" is not "there is sound". A Twitch channel's
# pre-roll ad reaches a player like this one as silent audio — the
# position runs, nothing is heard — and the static faded over it
# (task 38). The same meter tells whether an ad cover is really playing
# (task 50).
LEVEL_LABEL = "freqlvl"
LEVEL_PROP = "af-metadata/" + LEVEL_LABEL
LEVEL_FILTER = f"@{LEVEL_LABEL}:lavfi=[astats=metadata=1:reset=1]"
# Quieter than this is silence. Real music, even a quiet passage, sits
# far above it; an ad break's digital silence reads -inf.
AUDIBLE_DB = -55.0


def level_db(meta):
    """The loudness (dB) in a level-meter report, or None if unknown.

    -inf (digital silence) comes back as float('-inf').
    """
    if not isinstance(meta, dict):
        return None
    raw = meta.get("lavfi.astats.Overall.RMS_level")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _show_db(value):
    if value is None:
        return "?"
    if value == float("-inf"):
        return "silent"
    return f"{value:.0f} dB"


def _log(line):
    try:
        import crashlog
        crashlog.write(line)
    except Exception:                           # noqa: BLE001
        pass


def _short(text, keep=70):
    text = str(text or "")
    return text if len(text) <= keep else text[:keep] + "…"


class PlaylistEntries:
    """Which of mpv's playlist entries came from the file asked for.

    A .pls or .m3u address is a playlist, not a stream. mpv opens it,
    ends that entry with "redirect" and plays the streams it lists as
    NEW entries, with new ids. The players trusted mpv's reports only for
    the id loadfile handed back, so a playlist station's position never
    counted, and after 10 s it was called down (task 91, build 78: every
    .pls station, e.g. SomaFM's). Now the streams a playlist lists count
    as the file asked for too.

    Written from mpv's event thread, read from the others: the caller
    holds its own lock around every call.
    """

    def __init__(self):
        self._kids = {}             # entry id -> ids of what it listed

    def clear(self):
        self._kids.clear()

    def redirect(self, event):
        """Note a redirect end-file. Returns how many entries it listed."""
        try:
            parent = int(event.data.playlist_entry_id)
            first = int(event.data.playlist_insert_id)
            count = int(event.data.playlist_insert_num_entries)
        except Exception:                       # noqa: BLE001
            return 0
        if parent and first and count > 0:
            # mpv numbers inserted entries one after another.
            self._kids[parent] = list(range(first, first + count))
            return count
        return 0

    def family(self, root):
        """`root` and every entry it led to (playlists can nest)."""
        ids, todo = {root}, [root]
        while todo:
            for kid in self._kids.get(todo.pop(), ()):
                if kid not in ids:
                    ids.add(kid)
                    todo.append(kid)
        return ids

    def place(self, entry):
        """(n, of) for an entry a playlist listed, else None."""
        for kids in self._kids.values():
            if entry in kids:
                return kids.index(entry) + 1, len(kids)
        return None

    def has_next(self, entry):
        """Whether mpv goes on to another listed stream after `entry`."""
        where = self.place(entry)
        return bool(where and where[0] < where[1])


class FillerPlayer:
    """A second audio source, for covering a station's pre-roll ad.

    It has to be separate from the main player: the advert is inside the
    stream, so the stream must keep running - and stay inaudible - while
    something else is heard over it.

    Rebuilt in build 64 (task 50). The old one played one file and never
    said whether anything came out; on the user's PC the whole ad was
    silence. Its mpv was also the one handle in the app still called
    "mpv" in the desktop's volume mixer — the name a station's sound was
    lost under in task 37. Now:

      - it is given a list of candidates (another saved frequency's
        stream, or a track from a music folder) and moves to the next
        one when one fails, ends, or makes no sound within START_TIMEOUT
      - it has its own level meter and only counts as playing once the
        position moves AND it is audible; Player keeps the static on
        until then, and brings it back over any gap
      - it is named "Frequencies" in the mixer, and gets the same sound
        check as the station once it plays
      - every mpv call runs on its own queue thread, never the GUI's

    The GUI thread drives it through poll() from Player.tick.
    """

    START_TIMEOUT = 8.0         # a cover gets this long to make a sound

    def __init__(self):
        self._mpv = None
        self._queue = MpvQueue("FillerQueue")
        self._lock = threading.Lock()
        self._volume = 100
        self._normalize = True
        # Settings > Audio output, as for the station (task 83). Each
        # ad's new handle is made with it.
        self._device = "auto"
        self._gen = 0               # bumped by play/stop; old jobs skip
        self._candidates = []
        self._current = None
        self._status = "idle"       # idle / starting / playing / failed
        self._started_at = None
        self._began_at = None       # when play() was called
        self._first_seen = None
        self._last_pos = None
        self._last_progress = None
        self._audible_since = None
        self._fade_cancel = threading.Event()
        # Bumped by play() only: a stop or fade-out still runs its last
        # mpv stop unless a newer cover has started since (task 74).
        self._plays = 0
        # The stop watch (task 74): when the cover was told to stop
        # (GUI thread) and when the queue really stopped it.
        self._stop_asked = None     # (monotonic, why)
        self._stop_done = None      # monotonic, set on the queue
        self._stop_warned = False
        # Held while the handle is closed, and by the rescue stop (see
        # watch_stop), so the rescue never calls a closed handle.
        self._handle_lock = threading.Lock()
        # What mpv reports, under _lock (mpv's event thread writes).
        self._props = {}
        self._entry = None          # mpv's id for the file asked for
        self._started_entry = None
        self._ended = None          # (reason, error) of that file
        # The streams a .pls / .m3u cover lists count as it (task 91).
        self._listed = PlaylistEntries()

    # -- handle (queue thread only) -------------------------------------
    def _ensure(self):
        if self._mpv is None:
            force_c_numeric_locale()
            handle = mpv.MPV(
                vid="no", vo="null", video=False, ytdl=False,
                audio_display="no", terminal=False,
                cache=True,
                idle=True,
                # Named "Frequencies" in the desktop's mixer, like the
                # station and the static — not "mpv" (task 37).
                audio_client_name="Frequencies",
                # The mixer's second line (PipeWire's media.name). Left
                # alone it is "<cover station> - mpv", which read as a
                # second app (build 71, task 75).
                title="Frequencies (ad cover)",
                **STREAM_OPTS,
                **NO_SCRIPTS,
                # The output chosen in Settings, not the desktop's
                # default (task 83).
                audio_device=self._device,
            )
            handle.event_callback("end-file")(self._on_end_file)
            handle.event_callback("start-file")(self._on_start_file)
            for name in ("time-pos", LEVEL_PROP):
                try:
                    handle.observe_property(name, self._on_prop)
                except Exception:               # noqa: BLE001
                    pass
            self._mpv = handle
        return self._mpv

    def set_audio_device(self, name):
        """Settings > Audio output, for the ad cover too (task 83)."""
        self._device = name or "auto"

        def write(device=self._device):
            with self._handle_lock:
                if self._mpv is not None:
                    self._mpv["audio-device"] = device
        self._queue.put("filler audio-device", write)

    # -- what mpv reports (mpv's event thread) ----------------------------
    def _on_prop(self, name, value):
        with self._lock:
            self._props[name] = value

    def _on_start_file(self, event=None):
        try:
            entry = int(event.data.playlist_entry_id)
        except Exception:                       # noqa: BLE001
            entry = None
        with self._lock:
            self._props.clear()
            self._started_entry = entry

    def _on_end_file(self, event):
        try:
            reason = event.data.reason
            error = event.data.error
        except Exception:                       # noqa: BLE001
            reason, error = None, 0
        try:
            entry = int(event.data.playlist_entry_id) or None
        except Exception:                       # noqa: BLE001
            entry = None
        if reason == 5:                         # a playlist, opened
            with self._lock:
                self._listed.redirect(event)
            return
        # 0 = played to its end, 4 = error. A stop (2) is ours.
        if reason not in (0, 4):
            return
        with self._lock:
            if self._entry is None or (
                    entry and entry not in self._listed.family(self._entry)):
                return
            if reason == 4 and entry and self._listed.has_next(entry):
                # One of a playlist's streams failed: mpv goes on to the
                # next one it lists by itself.
                n, of = self._listed.place(entry)
                _log(f"[ad] cover: stream {n} of {of} in its playlist "
                     f"failed ({error}), mpv tries the next one")
                return
            self._ended = (reason, error)

    def _fresh_props(self):
        """(position, level) of the file asked for, or (None, None)."""
        with self._lock:
            if (self._entry is None or self._started_entry is None
                    or self._started_entry
                    not in self._listed.family(self._entry)):
                return None, None
            pos = self._props.get("time-pos")
            level = level_db(self._props.get(LEVEL_PROP))
            return (float(pos) if isinstance(pos, (int, float)) else None,
                    level)

    # -- control (GUI thread) --------------------------------------------
    def play(self, candidates, volume=100, normalize=True):
        """Try `candidates` in order until one makes a sound.

        Each is a dict: kind ("music" / "stream"), label, path, fraction
        (where to join a track, or None), insecure.
        """
        self._gen += 1
        self._plays += 1
        self._fade_cancel.set()
        self._stop_asked = None
        # A cover with no address ("nothing found") is not tried: the
        # static covers the ad on its own.
        self._candidates = [c for c in (candidates or []) if c.get("path")]
        self._volume = max(0, min(100, int(volume)))
        self._normalize = bool(normalize)
        self._began_at = time.monotonic()
        self._status = "starting"
        self._start_next()

    def _start_next(self):
        if not self._candidates:
            self._current = None
            self._status = "failed"
            _log("[ad] cover: nothing left to try - the static covers "
                 "the rest of the ad")
            self._queue.put("filler stop",
                            self._close_job(self._plays, "nothing left"))
            return
        cand = self._candidates.pop(0)
        self._current = cand
        self._status = "starting"
        self._started_at = time.monotonic()
        self._first_seen = None
        self._last_pos = None
        self._last_progress = None
        self._audible_since = None
        with self._lock:
            self._entry = None
            self._ended = None
            self._listed.clear()
        where = (f"{os.path.basename(cand.get('path') or '')}"
                 if cand.get("kind") == "music" else
                 _short(cand.get("path")))
        _log(f"[ad] cover: trying {cand.get('kind')} "
             f"'{cand.get('label')}' ({where}), "
             f"{len(self._candidates)} more to fall back on")
        gen = self._gen
        volume, normalize = self._volume, self._normalize

        def load():
            if gen != self._gen:
                return
            handle = self._ensure()
            chain = []
            if normalize:
                chain.append("lavfi=[dynaudnorm=f=200:g=15:p=0.95]")
            # Last, after the levelling's 3 s look-ahead (see
            # build_filter_chain).
            chain.append(LEVEL_FILTER)
            try:
                handle["af"] = ",".join(chain)
            except Exception:                   # noqa: BLE001
                try:
                    handle["af"] = ""
                except Exception:               # noqa: BLE001
                    pass
            handle.volume = volume
            handle.mute = False
            fraction = cand.get("fraction")
            try:
                handle["start"] = ("{0:.1f}%".format(
                    max(0.0, min(90.0, float(fraction) * 100.0)))
                    if fraction else "none")
            except Exception:                   # noqa: BLE001
                pass
            try:
                handle["tls-verify"] = not bool(cand.get("insecure"))
            except Exception:                   # noqa: BLE001
                pass
            path = cand.get("path") or ""
            entry = None
            try:
                reply = handle.command(
                    "loadfile", path.encode(mpv.fs_enc), "replace")
                if isinstance(reply, dict):
                    entry = int(reply.get("playlist_entry_id") or 0) or None
            except Exception as exc:            # noqa: BLE001
                with self._lock:
                    self._entry = -1
                    self._ended = (4, repr(exc))
                return
            with self._lock:
                self._entry = entry if entry is not None else -2
                if entry is None:
                    # No id from an old python-mpv: take any start.
                    self._started_entry = self._started_entry or -2
        self._queue.put("filler play", load)

    def poll(self, now=None):
        """Move things along. GUI thread, from Player.tick. No mpv calls."""
        if self._status not in ("starting", "playing"):
            return
        now = now or time.monotonic()
        with self._lock:
            ended = self._ended
        label = (self._current or {}).get("label")
        if ended is not None:
            reason, error = ended
            what = "came to its end" if reason == 0 else f"failed ({error})"
            _log(f"[ad] cover: '{label}' {what} after "
                 f"{now - self._started_at:.1f}s")
            self._start_next()
            return
        pos, level = self._fresh_props()
        if pos is not None:
            if self._last_pos is not None and pos > self._last_pos + 0.05:
                self._last_progress = now
            self._last_pos = pos
        if level is not None and level > AUDIBLE_DB:
            if self._audible_since is None:
                self._audible_since = now
        elif level is not None:
            self._audible_since = None

        if self._status == "starting":
            if now - self._started_at > self.START_TIMEOUT:
                _log(f"[ad] cover: '{label}' made no sound in "
                     f"{self.START_TIMEOUT:.0f}s (position "
                     f"{'none' if pos is None else f'{pos:.1f}'}, level "
                     f"{_show_db(level)})")
                self._start_next()
                return
            if pos is None:
                return
            if self._first_seen is None:
                self._first_seen = (pos, now)
            # The position must move (it only does once the sound output
            # is running — the meter reads the audio up to two seconds
            # before that, while a stream's output opens) and the meter
            # must hear sound.
            moved = pos - self._first_seen[0] >= 0.3
            loud = (level is None or (self._audible_since is not None
                                      and now - self._audible_since >= 0.4))
            if moved and loud:
                self._status = "playing"
                _log(f"[ad] cover: '{label}' audio flowing after "
                     f"{now - self._began_at:.1f}s (level "
                     f"{_show_db(level)})")
                gen = self._gen
                self._queue.put("filler sound check",
                                lambda: self._sound_check(gen))

    def audible(self, now=None):
        """Whether the cover can be heard right now (GUI thread)."""
        if self._status != "playing":
            return False
        now = now or time.monotonic()
        if self._last_progress is None or now - self._last_progress > 1.2:
            return False
        _pos, level = self._fresh_props()
        return level is None or level > AUDIBLE_DB

    def describe(self, now=None):
        """One short phrase for the [ad] trace."""
        now = now or time.monotonic()
        pos, level = self._fresh_props()
        label = (self._current or {}).get("label") or "-"
        status = self._status
        if (status == "idle" and self._stop_asked is not None
                and self._stop_done is None):
            status = "stopping"         # told to stop, not stopped yet
        return (f"cover '{label}' {status}, pos "
                f"{'none' if pos is None else f'{pos:.1f}'}, level "
                f"{_show_db(level)}")

    @property
    def status(self):
        return self._status

    def set_volume(self, value):
        self._volume = max(0, min(100, int(value)))
        if self._status not in ("starting", "playing"):
            return
        gen, volume = self._gen, self._volume

        def write():
            if gen == self._gen and self._mpv is not None:
                self._mpv.volume = volume
        self._queue.put("filler volume", write)

    def _close_job(self, plays, why, report=False):
        """Queue job: stop the cover and close its mpv handle.

        Closing the handle (not only `stop`) is what takes the cover's
        entry out of the desktop's mixer for sure. Build 69 only sent
        mpv `stop`, after a fade that a newer stop or fade could cut
        short, and on the user's PC the cover went on playing after the
        hand-over while the log said `idle` (task 74). Now the stop is
        never skipped unless a newer cover has started on this handle
        (`plays`), mpv is asked whether it really went idle, and the
        handle is closed; a new one is made for the next ad.
        `report`: log what happened (an ad that was covered); quiet for
        the stop at every station change.
        """
        def close():
            if plays != self._plays:
                return                  # a newer cover owns the handle
            handle = self._mpv
            if handle is None:
                self._stop_done = time.monotonic()
                return
            notes = []
            try:
                handle.stop()
            except Exception as exc:            # noqa: BLE001
                notes.append(f"stop failed ({exc!r})")
            # mpv answers `stop` at once and goes idle a moment later.
            idle, waited = None, 0.0
            began = time.monotonic()
            while waited < 2.0:
                try:
                    idle = bool(handle.idle_active)
                except Exception:               # noqa: BLE001
                    idle = None
                    break
                if idle:
                    break
                time.sleep(0.05)
                waited = time.monotonic() - began
            with self._lock:
                self._entry = None
                self._started_entry = None
                self._ended = None
                self._props.clear()
            if plays != self._plays:
                return                  # a play came in meanwhile: keep it
            with self._handle_lock:
                self._mpv = None
                try:
                    handle.terminate()
                    closed = "handle closed (gone from the mixer)"
                except Exception as exc:        # noqa: BLE001
                    closed = f"closing the handle failed ({exc!r})"
            self._stop_done = time.monotonic()
            if not report and idle and not notes:
                return
            state = ("mpv idle" if idle else
                     "mpv NOT idle after 2 s" if idle is False else
                     "mpv state unknown")
            # How long the cover's mixer entry outlived the ask: at the
            # ad's end the static and the station have theirs open, so
            # this is how long the mixer showed three (task 84).
            asked = self._stop_asked
            after = (f", {(self._stop_done - asked[0]) * 1000:.0f} ms "
                     f"after the ask" if asked else "")
            _log(f"[ad] cover stopped ({why}): {state}, {closed}{after}"
                 + (", " + ", ".join(notes) if notes else ""))
        return close

    def fade_out_and_stop(self, ms, why="ad time is up"):
        self._gen += 1
        self._candidates = []
        was = self._status
        self._status = "idle"
        if was not in ("starting", "playing") or not ms:
            self._stop(why, report=was in ("starting", "playing"))
            return
        self._fade_cancel.set()
        self._fade_cancel = threading.Event()
        cancel = self._fade_cancel
        plays = self._plays
        self._ask_stop(why)
        start = self._volume
        steps = max(1, int(ms / 35))
        close = self._close_job(plays, why, report=True)

        def fade():
            # The fade may be cut short (a newer stop); the close is not.
            for i in range(1, steps + 1):
                if cancel.is_set() or plays != self._plays \
                        or self._mpv is None:
                    break
                try:
                    self._mpv.volume = int(round(start * (1 - i / steps)))
                except Exception:               # noqa: BLE001
                    break
                time.sleep(ms / 1000.0 / steps)
            close()
        self._queue.put("filler fade", fade)

    def stop(self):
        self._stop("stopped", report=self._status in ("starting", "playing"))

    def _stop(self, why, report):
        self._gen += 1
        self._fade_cancel.set()
        self._candidates = []
        self._current = None
        self._status = "idle"
        if report:
            self._ask_stop(why)
        self._queue.put("filler stop",
                        self._close_job(self._plays, why, report=report))

    def _ask_stop(self, why):
        self._stop_asked = (time.monotonic(), why)
        self._stop_done = None
        self._stop_warned = False

    # How long a stop may take before the log says so (task 74).
    STOP_LATE = 2.5

    def watch_stop(self, now=None):
        """GUI thread, every tick: log once if a stop asked for has not
        run on the cover's queue after STOP_LATE seconds. No mpv calls."""
        asked = self._stop_asked
        if asked is None or self._stop_warned:
            return
        now = now or time.monotonic()
        if self._stop_done is not None:
            self._stop_asked = None
            return
        if now - asked[0] < self.STOP_LATE:
            return
        self._stop_warned = True
        busy = self._queue.busy_with()
        doing = (f"busy with '{busy[0]}' for {busy[1]:.1f}s, {busy[2]} "
                 f"job(s) waiting" if busy else "not busy")
        _log(f"[ad] the cover has NOT stopped {now - asked[0]:.1f}s after "
             f"'{asked[1]}' - its queue is {doing}; stopping it from a "
             f"thread of its own")
        threading.Thread(target=self._rescue_stop, args=(self._plays,),
                         daemon=True, name="FillerRescue").start()

    def _rescue_stop(self, plays):
        """Stop the cover past its stuck queue (task 74).

        mpv's own calls may be made from any thread; only this class's
        queue keeps them in order. A queue held up by a slow call would
        leave the cover playing, so this thread mutes and stops it
        directly. If mpv itself is not answering, this waits too — on
        a thread nobody waits for.
        """
        began = time.monotonic()
        with self._handle_lock:
            handle = self._mpv
            if handle is None or plays != self._plays:
                _log("[ad] cover rescue: nothing to do (already closed)")
                return
            try:
                handle.mute = True
                handle.stop()
                idle = bool(handle.idle_active)
                what = "mpv idle" if idle else "mpv not idle yet"
            except Exception as exc:            # noqa: BLE001
                what = f"failed ({exc!r})"
        _log(f"[ad] cover rescue: muted and stopped after "
             f"{time.monotonic() - began:.1f}s ({what}); the queue closes "
             f"the handle when it is free")

    def shutdown(self):
        self.stop()
        if not self._queue.close(timeout=2.0):
            return
        try:
            if self._mpv is not None:
                self._mpv.terminate()
        except Exception:                       # noqa: BLE001
            pass
        self._mpv = None

    def _sound_check(self, gen):
        """Queue thread: like Player._sound_check, for the cover."""
        if gen != self._gen or self._mpv is None:
            return
        handle = self._mpv

        def ask(name):
            try:
                return getattr(handle, name.replace("-", "_"))
            except Exception:                   # noqa: BLE001
                return None
        volume, mute = ask("volume"), ask("mute")
        sys_level, sys_mute = ask("ao-volume"), ask("ao-mute")
        fixed = []
        want = self._volume
        if isinstance(volume, (int, float)) and want > 0 \
                and volume < want - 1:
            handle.volume = want
            fixed.append(f"volume {volume:.0f} -> {want}")
        if mute is True:
            handle.mute = False
            fixed.append("mute off")
        if sys_mute is True:
            try:
                handle.ao_mute = False
                fixed.append("system mute off")
            except Exception:                   # noqa: BLE001
                pass
        if isinstance(sys_level, (int, float)) and sys_level < 1:
            try:
                handle.ao_volume = 100
                fixed.append(f"system level {sys_level:.0f}% -> 100%")
            except Exception:                   # noqa: BLE001
                pass
        out = ask("audio-out-params") or {}
        params = (f"{out.get('samplerate', '?')} Hz "
                  f"{out.get('channel-count', '?')}ch"
                  if isinstance(out, dict) and out
                  else "none (nothing reaching the output)")

        def show(v, unit=""):
            if v is None:
                return "?"
            if isinstance(v, bool):
                return "yes" if v else "no"
            if isinstance(v, float):
                return f"{v:.0f}{unit}"
            return f"{v}{unit}"
        line = (f"[ad] cover sound check: volume {show(volume)} "
                f"(want {want}), mute {show(mute)}, system level "
                f"{show(sys_level, '%')}, system mute {show(sys_mute)}, "
                f"output {show(ask('current-ao'))} / "
                f"{show(ask('audio-device'))}, out {params}")
        if fixed:
            line += " — fixed: " + ", ".join(fixed)
        _log(line)


# ----------------------------------------------------------------------
# The radio player
# ----------------------------------------------------------------------
class MpvQueue:
    """Runs mpv writes one after another on a thread of their own.

    Right after a song ends, mpv is busy finishing it and holds its lock
    for 250–550 ms. Any mpv call made in that time waits for the lock —
    and the GUI thread made several straight away, to start the next
    song (`start`, `af`, `stop`, `loadfile`). That was the freeze at
    every track change (task 24): 90 times in one 8-hour log, all at
    `self._mpv["start"] = "none"`.

    So the GUI thread no longer calls mpv for these at all. It puts the
    work here and carries on; this thread does the waiting. The order is
    kept, so a stop queued before a play still runs before it.
    (build 55, task 24)
    """

    # A call that kept mpv's lock this long is noted in the log, so a
    # test run shows the waits moved here instead of freezing the window.
    SLOW = 0.25

    def __init__(self, name="MpvQueue"):
        import collections
        self._jobs = collections.deque()
        self._cv = threading.Condition()
        self._closed = False
        self._busy = False
        self._doing = None          # (label, since) of the job running
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=name)
        self._thread.start()

    def put(self, label, fn):
        """Queue fn() (no arguments). Returns at once."""
        with self._cv:
            if self._closed:
                return
            self._jobs.append((label, fn))
            self._cv.notify_all()

    def _run(self):
        while True:
            with self._cv:
                while not self._jobs and not self._closed:
                    self._cv.wait()
                if self._closed:
                    return
                label, fn = self._jobs.popleft()
                self._busy = True
                began = time.monotonic()
                self._doing = (label, began)
            try:
                fn()
            except Exception as exc:            # noqa: BLE001
                try:
                    import crashlog
                    crashlog.write(f"[player] {label} failed: {exc!r}")
                except Exception:               # noqa: BLE001
                    pass
            took = time.monotonic() - began
            if took >= self.SLOW:
                try:
                    import crashlog
                    crashlog.write(f"[player] mpv kept {label} waiting "
                                   f"{took * 1000:.0f} ms (not the window)")
                except Exception:               # noqa: BLE001
                    pass
            with self._cv:
                self._busy = False
                self._doing = None
                self._cv.notify_all()

    def busy_with(self):
        """(label, seconds, jobs waiting) of the job running now, or None.

        For a log line when something queued is late (build 71, task 74:
        the ad cover's stop).
        """
        with self._cv:
            if self._doing is None:
                return None
            label, since = self._doing
            return label, time.monotonic() - since, len(self._jobs)

    def wait_idle(self, timeout=2.0):
        """Wait until everything queued has run. True if it has."""
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._jobs or self._busy:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cv.wait(left)
        return True

    def close(self, timeout=2.0):
        """Drop what is still queued and let the call in progress finish.

        True when the thread has stopped, so the handle can be destroyed
        without a call still running on it.
        """
        with self._cv:
            self._closed = True
            self._jobs.clear()
            self._cv.notify_all()
        if threading.current_thread() is self._thread:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()


class ClipPlayer:
    """A short sound played once, on an mpv of its own (build 114, task
    154: Winamp's llama the first time the Winamp view opens).

    Its mpv lives and dies on one thread of its own (never the window's),
    is named "Frequencies" in the mixer, uses the output chosen in
    Settings, and is closed as soon as the clip ends.
    """

    LONGEST = 20.0              # seconds a clip may take before it is cut

    def __init__(self):
        self._device = "auto"
        self._done = threading.Event()
        self._thread = None

    def set_audio_device(self, name):
        self._device = name or "auto"

    def play(self, path, volume):
        if self._thread is not None and self._thread.is_alive():
            return                      # one at a time
        self._done.clear()
        self._thread = threading.Thread(
            target=self._play, args=(path, volume), daemon=True,
            name="ClipPlayer")
        self._thread.start()

    def _play(self, path, volume):
        try:
            force_c_numeric_locale()
            handle = mpv.MPV(vid="no", vo="null", video=False, ytdl=False,
                             audio_display="no", terminal=False, idle=True,
                             volume=max(0, min(MAX_VOLUME, int(volume))),
                             audio_client_name="Frequencies",
                             title="Frequencies", audio_device=self._device,
                             **NO_SCRIPTS)
        except Exception as exc:                # noqa: BLE001
            _log(f"[player] clip could not start: {exc!r}")
            return
        try:
            @handle.event_callback("end-file")
            def _ended(_event):
                self._done.set()
            handle.play(path)
            self._done.wait(self.LONGEST)
        finally:
            handle.terminate()

    def shutdown(self):
        self._done.set()
        if self._thread is not None:
            self._thread.join(2.0)


class Player:
    # Read from mpv's own reports (see _on_prop), never asked for on the
    # GUI thread. Positions are only trusted once the file asked for has
    # started (see _fresh).
    WATCHED = ("time-pos", "demuxer-cache-duration", "audio-bitrate",
               "packet-audio-bitrate", "audio-params", "audio-out-params",
               # Build 64 (tasks 38, 50): how loud the stream is, and
               # whether mpv has paused to fill its cache.
               LEVEL_PROP, "paused-for-cache",
               # Task 88: mpv paused by something that isn't the app.
               "pause")

    def __init__(self, on_state=None, on_metadata=None,
                 on_connected=None, on_stalled=None,
                 on_track_finished=None):
        """
        on_state(state, message): called on the mpv event thread whenever
            playback state changes. `message` is a short human-readable
            string (used for error text).
        on_metadata(title): called when the stream provides an updated
            ICY title (song/show name), if available.
        on_connected(url, seconds): audio actually started, and took this
            long. Feeds the per-station buffering suggestion.
        on_stalled(url): audio stopped arriving and hasn't come back.
            The stream is dead even though the connection looks fine.
        """
        self._on_state = on_state or (lambda state, message: None)
        self._on_metadata = on_metadata or (lambda title: None)
        self._on_connected = on_connected
        self._on_stalled = on_stalled
        self._on_track_finished = on_track_finished

        self._radio_quality = False
        self._eq_advanced = False
        self._eq_bands = [0] * 10
        self._ad_seconds = 0.0
        self._ad_deadline = None
        self._ad_state = "none"
        self._connect_started = None
        self._audio_started = False
        self._buffer_target = 0.0
        self._crossfade_ms = 700
        self._stall_timeout = 6
        # Long enough for a slow mount to answer, short enough that two
        # dead stations do not cost most of a minute.
        # Ten seconds: a working stream is audible well inside that, and
        # twenty meant a dead one held the radio silent for a third of a
        # minute before anything moved on.
        self.CONNECT_TIMEOUT = 10
        self._last_progress = None
        self._last_position = None
        # Hand-over bookkeeping for the static (build 47, task 18).
        # _covered: the static is covering this connect.
        # _first_seen: (position, time) of the first position mpv gave.
        # _handover_at: when the static was faded for this stream.
        # _rebuffering: the static came back because the stream stalled
        # straight after the hand-over.
        self._covered = False
        self._first_seen = None
        self._handover_at = None
        self._rebuffering = False
        # Build 64 (task 38): the hand-over also waits for sound.
        # _moved_at: when the position first really moved.
        # _audible_since / _quiet_since: from the level meter.
        self._moved_at = None
        self._audible_since = None
        self._quiet_since = None
        self._handover_why = ""
        # The [tune] / [ad] trace: one line a second while tuning in.
        self._trace_next = 0.0
        self._trace_lines = 0
        self._play_began = None
        # Ad cover (task 50): whether the static is on over the cover.
        self._ad_static_on = False
        # Whether the station's real output is open again after a
        # covered ad (build 71, see _ad_output_back).
        self._ad_output_open = False
        # Task 88: sound that stops on its own after the hand-over.
        # on_went_quiet(why) is called (GUI thread, from tick) so the
        # window can pause for real and the play button shows it.
        # _quiet_stop: this station pauses after QUIET_STOP_S of silence
        # (Twitch, set by play()). _sound_watch: (why, since, until) of
        # the [sound] trace after a link opened in the browser.
        self.on_went_quiet = None
        self._quiet_stop = False
        self._quiet_done = False
        self._quiet_warned = False
        self._paused_since = None
        self._sound_watch = None
        self._sound_watch_next = 0.0
        self._sound_watch_lines = 0

        # Must happen immediately before mpv.MPV(): the very first thing
        # libmpv does is check LC_NUMERIC, and Qt will have changed it.
        force_c_numeric_locale()

        # mpv's own warnings and errors, newest last (task 162). A stream
        # that never starts used to leave only "never started playing" in
        # the log; the reason (a refused connection, a 403, a bad
        # certificate) was mpv's and went nowhere. Kept here, written out
        # when the app gives up. Filled on mpv's event thread: a deque
        # append is safe there and costs nothing.
        import collections
        self._mpv_said = collections.deque(maxlen=8)
        self._mpv = mpv.MPV(
            log_handler=self._mpv_log,
            loglevel="warn",
            **STREAM_OPTS,
            **NO_SCRIPTS,
            # This is a radio player - it never shows video. Forcing the
            # "null" video output (rather than just deselecting the video
            # track) stops mpv from ever probing/initializing a GPU-based
            # video output (EGL/GLX/Wayland/DRM/GBM) at all. Without this,
            # mpv can still try to set up a default GPU-backed VO purely
            # for internal reasons (e.g. OSD/album art) even when nothing
            # is ever displayed, which has caused hard crashes on some
            # driver/compositor combinations.
            vid="no",
            vo="null",
            video=False,
            ytdl=False,
            cache=True,
            demuxer_max_bytes="16MiB",
            demuxer_readahead_secs=8,
            volume=80,
            # Named like the static (NoisePlayer). Left as "mpv", the
            # station was a separate entry in the desktop's volume mixer,
            # with a level and mute of its own that the app's slider
            # cannot reach — the static could be heard and the station
            # not (task 37). One name: one entry, one level.
            audio_client_name="Frequencies",
            # The mixer's second line (PipeWire's media.name) is mpv's
            # title, "<song> - mpv" by default (build 71, task 75).
            title="Frequencies",
        )
        self._normalize = True
        self._eq = {"bass": 0, "mid": 0, "treble": 0}
        self._gain_db = 0.0
        self._current_url = None
        # Last value written to each slow mpv option (see _set_opt).
        self._opt_cache = {}
        # Set by stop(keep_noise=True), so the end-file that follows the
        # stop does not cut the static it was asked to keep.
        self._noise_held = False

        self._base_volume = 80
        # Bumped by every play, stop and new fade: a fade-in step still
        # waiting on the queue checks it and stands down (task 60).
        self._fade_gen = 0

        # Every mpv write goes through here (build 55, task 24).
        self._queue = MpvQueue()
        self._af_written = None         # last filter chain written (queue)

        # What mpv last reported, and whether it is about the file we
        # asked for. All under _prop_lock: written on mpv's event thread,
        # the queue thread and the GUI thread.
        self._prop_lock = threading.Lock()
        self._props = {}
        self._want = False          # something is meant to be playing
        self._pending = 0           # loadfiles queued, not yet sent
        self._entry_id = None       # mpv's id for the file asked for
        self._started_id = None     # id of the last file mpv started
        self._starts = 0            # start-files seen since the last play
        # The streams a .pls / .m3u station lists count as the file
        # asked for (task 91). _next_tries: playlist streams skipped to
        # because the first one made no sound (one per tune-in).
        self._listed = PlaylistEntries()
        self._next_tries = 0

        self._apply_audio_filters()

        self.noise = NoisePlayer()
        # Built now, not at the first station change.
        self.noise.warm_up()
        self.filler = FillerPlayer()
        self.clips = ClipPlayer()
        self._noise_enabled = True

        self._mpv.event_callback("end-file")(self._on_end_file)
        self._mpv.event_callback("file-loaded")(self._on_file_loaded)
        self._mpv.event_callback("start-file")(self._on_start_file)
        self._mpv.observe_property("media-title", self._on_media_title)
        # The file's own tags (a music folder's album, artist, title), for
        # brat mode (task 48). Event thread: main.py hands it to the window
        # through PlayerBus, never straight to Qt.
        self.on_tags = None
        self._mpv.observe_property("metadata", self._on_tags)
        for name in self.WATCHED:
            try:
                self._mpv.observe_property(name, self._on_prop)
            except Exception:                   # noqa: BLE001
                pass
        # The sound outputs, as mpv reports them (build 69, task 36).
        # Settings read this instead of asking mpv on the window's
        # thread, which waited for mpv's lock — the last such call.
        # Kept apart from _props: those are cleared at every new file.
        self._devices = None
        try:
            self._mpv.observe_property("audio-device-list",
                                       self._on_devices)
        except Exception:                       # noqa: BLE001
            pass

    # -- what mpv reports (event thread) -------------------------------
    _POSITIONS = ("time-pos", "demuxer-cache-duration")

    def _on_devices(self, _name, value):
        self._devices = list(value) if value else []

    def _on_prop(self, name, value):
        with self._prop_lock:
            self._props[name] = value

    def _on_start_file(self, event=None):
        try:
            entry = int(event.data.playlist_entry_id)
        except Exception:                       # noqa: BLE001
            entry = None
        with self._prop_lock:
            # What follows is about this file. mpv sends the current value
            # with every report, so nothing from the last file comes after.
            self._props.clear()
            self._started_id = entry
            self._starts += 1

    def _fresh(self):
        """Whether what mpv reports is about the file asked for.

        Called with _prop_lock held. Until the new file has started, the
        reports are still about the old one — a stale position would fade
        the static before the new stream has made a sound (task 18).
        """
        if not self._want or self._pending:
            return False
        if self._entry_id and self._started_id:
            return self._started_id in self._listed.family(self._entry_id)
        return self._starts > 0

    def _reported(self, name):
        """mpv's last report of `name`, or None. Never waits for mpv."""
        with self._prop_lock:
            if not self._fresh():
                return None
            return self._props.get(name)

    # -- filter chain -----------------------------------------------
    def _apply_audio_filters(self, force=False):
        """Work out the filter chain and queue the write.

        `force`: write it even when it is the same as last time. play()
        does, so every new stream gets its chain written right before it
        loads, the way build 54 did it (task 37). Live changes (EQ, gain,
        levelling) skip an unchanged chain.
        """
        def chain_for(safe):
            return build_filter_chain(
                bands=self._eq_bands if self._eq_advanced else None,
                normalize=self._normalize,
                bass=self._eq["bass"],
                mid=self._eq["mid"],
                treble=self._eq["treble"],
                gain_db=self._gain_db,
                radio_quality=self._radio_quality,
                # The fallback ("safe") chain leaves the meter out: if
                # this mpv will not take it, sound matters more than
                # knowing how loud it is. Without it the hand-over goes
                # by the position alone, as before build 64.
                meter=not safe,
            )

        # Built here, from the settings as they are now; written on the
        # queue, and only when it changed. A station change pushed the
        # same chain three times (EQ, advanced EQ, play), each a wait on
        # mpv's lock (build 55, task 24).
        full, safe = chain_for(False), chain_for(True)

        def write():
            if self._af_written == full and not force:
                return
            try:
                self._mpv["af"] = full
                self._af_written = full
                return
            except Exception:
                pass

            # A filter mpv will not accept costs the whole chain, EQ and
            # levelling included, so the fallback retries with the simpler
            # form rather than losing everything. Remembered as done: the
            # full chain would only fail the same way again.
            try:
                self._mpv["af"] = safe
            except Exception:
                try:
                    self._mpv["af"] = ""
                except Exception:
                    pass
            self._af_written = full

        self._queue.put("af", write)

    def set_normalize(self, enabled):
        self._normalize = bool(enabled)
        self._apply_audio_filters()

    def stream_info(self):
        """(kbps, kHz) for what is playing, or (0, 0).

        Read from mpv rather than guessed from the URL: plenty of
        stations advertise one bitrate and serve another, and the whole
        point of showing it is to know what you are actually getting.

        Attribute access, not subscript. In python-mpv `mpv[name]` reads
        an OPTION and `mpv.name` reads a PROPERTY, and these are
        properties - so the subscript form quietly returned nothing for
        every one of them, which is why the boxes stayed empty. Property
        names use underscores where mpv writes hyphens, and a nested
        path like audio-params/samplerate is a dict on the parent.
        """
        def prop(name):
            # From mpv's own reports, not asked for: asking waits for
            # mpv's lock, which froze the window at a track change
            # (build 55, task 24).
            return self._reported(name.replace("_", "-"))

        def kilo(value):
            if not isinstance(value, (int, float)):
                return 0
            return int(value / 1000.0)

        bitrate = kilo(prop("audio_bitrate")) or kilo(prop("packet_audio_bitrate"))

        # audio-params is a dict; audio-out-params is what actually
        # reaches the device, and one is populated when the other is not.
        samplerate = 0
        for name in ("audio_params", "audio_out_params"):
            params = prop(name)
            if isinstance(params, dict):
                rate = params.get("samplerate")
                if isinstance(rate, (int, float)) and rate:
                    samplerate = int(rate / 1000.0)
                    break

        return bitrate, samplerate

    def audio_devices(self):
        """[(id, description)] of what mpv can play through.

        mpv keeps this list itself and reports it (and every change to
        it) as devices come and go, so there is no need to talk to
        PulseAudio or PipeWire directly. "auto" always leads, because letting the system decide
        is the right default and the only thing that survives unplugging
        a USB interface.
        """
        out = [("auto", "System default")]
        # From mpv's own report, never asked for here: this runs on the
        # window's thread (task 36). None until mpv has said, which is
        # a moment after start-up; Settings fills its list again when
        # it is opened.
        entries = self._devices
        if not entries:
            return out
        try:
            return out + self._real_outputs(list(entries))
        except Exception:  # noqa: BLE001
            return out

    @staticmethod
    def _real_outputs(entries):
        """Only the devices your desktop would show you.

        mpv lists every output through every audio system it was built
        with — PipeWire, PulseAudio, ALSA's raw hardware and plug devices,
        JACK, SDL, OpenAL — so one pair of speakers turned up half a dozen
        times and the list ran to twenty-six entries where the system tray
        shows three.

        The desktop's own sound server is the one to trust: PipeWire if
        it is there, otherwise PulseAudio. Only its devices are offered,
        once each, under the names the desktop uses. ALSA is kept only as
        a last resort, for a machine with no sound server at all.
        """
        by_system = {}
        for entry in entries:
            name = entry.get("name") or ""
            if not name or name == "auto" or "/" not in name:
                # A bare system name ("pipewire", "pulse") is that system's
                # own default, which "System default" already covers.
                continue
            system = name.split("/", 1)[0]
            by_system.setdefault(system, []).append(
                (name, (entry.get("description") or name).strip()))

        # Windows (WP1): WASAPI is the system's own, like PipeWire here.
        for preferred in ("pipewire", "pulse", "wasapi", "alsa"):
            devices = by_system.get(preferred)
            if not devices:
                continue
            seen, out = set(), []
            for name, label in devices:
                if preferred == "alsa" and not name.startswith(
                        ("alsa/default:", "alsa/sysdefault:", "alsa/front:")):
                    continue            # ALSA's plug and surround aliases
                if label.lower() in seen:
                    continue
                seen.add(label.lower())
                out.append((name, label))
            if out:
                return out
        return []

    def set_audio_device(self, name):
        """Switch output. mpv reopens the stream on the new device.

        The static and the ad cover follow it (task 83): each has its
        own mpv, and before build 75 only the station's was told, so
        they came out of the desktop's default output.
        """
        def write():
            self._mpv["audio-device"] = name or "auto"
        self._queue.put("audio-device", write)
        self.noise.set_audio_device(name)
        self.filler.set_audio_device(name)
        self.clips.set_audio_device(name)
        _log(f"[player] audio output: {name or 'auto'} "
             f"(station, static and ad cover)")

    def set_equalizer(self, bass=0, mid=0, treble=0):
        self._eq = {"bass": bass, "mid": mid, "treble": treble}
        self._apply_audio_filters()

    def set_advanced_equalizer(self, enabled, bands=None):
        """Ten-band EQ, replacing bass/mid/treble while it is on."""
        self._eq_advanced = bool(enabled)
        if bands is not None:
            import storage as _storage
            self._eq_bands = _storage.normalize_eq_bands(bands)
        self._apply_audio_filters()

    def set_radio_quality(self, enabled):
        """Band-limit and compress, so the station sounds broadcast."""
        self._radio_quality = bool(enabled)
        self._apply_audio_filters()

    def set_gain_db(self, gain_db):
        """Persistent per-station boost or duck, in dB. Applied live."""
        try:
            self._gain_db = max(-20.0, min(20.0, float(gain_db)))
        except (TypeError, ValueError):
            self._gain_db = 0.0
        self._apply_audio_filters()

    # -- between-station static --------------------------------------
    def set_noise_enabled(self, enabled):
        self._noise_enabled = bool(enabled)
        if not self._noise_enabled:
            self.noise.stop()

    def set_noise_volume(self, value):
        self.noise.set_volume(value)

    # -- playback -----------------------------------------------------
    def _set_opt(self, name, value):
        """Set an mpv property only when the value changed.

        Every write takes mpv's lock. Right after a track ends mpv is busy
        with end-file, so the write blocked the GUI thread for ~250 ms.
        Since build 55 it only runs on the queue thread (task 24).
        """
        if name in self._opt_cache and self._opt_cache[name] == value:
            return
        self._mpv[name] = value
        self._opt_cache[name] = value

    def play(self, url, gain_db=0.0, insecure=False,
             buffer_seconds=None, crossfade_ms=700, radio_quality=False,
             start_fraction=None, http_headers=None, use_noise=True,
             ad_seconds=0.0, filler_path=None, filler_fraction=None,
             ad_cover=None, quiet_stop=False):
        """Start `url`. Returns at once: the mpv side runs on the queue.

        `quiet_stop`: pause for real when the stream goes silent for
        QUIET_STOP_S after the hand-over (Twitch, task 88).

        Nothing here waits for mpv (build 55, task 24). The settings are
        worked out here, on the GUI thread, and handed to the queue in
        the order they used to be written: options, filter chain, then
        silence and the new file.
        """
        self._current_url = url
        self._mpv_said.clear()
        with self._prop_lock:
            # Until mpv has started this file, its reports are about the
            # last one.
            self._want = True
            self._pending += 1
            self._starts = 0
            self._listed.clear()
        self._next_tries = 0

        # Headers for hosts that check them.
        #
        # Twitch signs its manifest and then refuses the media requests
        # that follow unless the referer and origin match — the stream
        # connects and delivers nothing, which sounds like silence rather
        # than like an error. Cleared when a station does not need them,
        # so one station's headers cannot leak into the next.
        #
        # A list, not a comma-joined string.
        #
        # mpv's http-header-fields is a list property, and header
        # values contain commas of their own — an Accept line is
        # nothing but commas. Joining them produced one malformed
        # field, the CDN rejected the media requests, and the
        # stream connected and played silence.
        #
        # Only the three that matter are sent. Accept-Language and
        # Sec-Fetch-Mode do nothing here and are more chances to
        # send something malformed.
        headers = []
        if http_headers:
            wanted = ("Referer", "Origin", "User-Agent")
            headers = [f"{k}: {v}" for k, v in http_headers.items()
                       if k in wanted]

        self._connect_started = time.monotonic()
        self._audio_started = False
        self._fade_gen += 1         # an old fade-in step must not land
        self._last_progress = None
        self._last_position = None
        self._first_seen = None
        self._handover_at = None
        self._rebuffering = False
        self._moved_at = None
        self._audible_since = None
        self._quiet_since = None
        self._handover_why = ""
        self._trace_next = 0.0
        self._trace_lines = 0
        self._play_began = time.monotonic()
        self._ad_static_on = False
        self._ad_output_open = False
        self._quiet_stop = bool(quiet_stop)
        self._quiet_done = False
        self._quiet_warned = False
        self._paused_since = None
        self._crossfade_ms = max(0, int(crossfade_ms or 0))

        # Pre-buffering, per station. mpv normally starts as soon as it
        # has enough to decode; a station told to read further ahead
        # takes longer to start but rides out the drop-outs that make
        # some streams unlistenable.
        self._buffer_target = 0.0
        try:
            if buffer_seconds and float(buffer_seconds) > 0:
                self._buffer_target = max(1.0, min(60.0,
                                                   float(buffer_seconds)))
        except (TypeError, ValueError):
            pass
        cache_secs = self._buffer_target or 10
        readahead = self._buffer_target or 1

        # Joining a local track partway through is what makes a folder
        # of music behave like a station: you tune in and something is
        # already playing, rather than every "station" starting at the
        # top of a song. mpv takes a percentage directly, which avoids
        # having to know the file's duration first.
        start = "none"
        try:
            if start_fraction is not None:
                start = "{0:.1f}%".format(
                    max(0.0, min(90.0, float(start_fraction) * 100.0)))
        except (TypeError, ValueError):
            pass

        def options():
            # Never start a station paused. mpv keeps "pause" from one
            # file to the next, so a pause from outside the app (task 88)
            # left every station after it silent at 0.0.
            try:
                if self._mpv.pause:
                    self._mpv.pause = False
                    _log("[playstate] mpv was left paused; unpaused for "
                         "the new station")
            except Exception:                   # noqa: BLE001
                pass
            try:
                self._set_opt("http-header-fields", headers)
            except Exception as exc:            # noqa: BLE001
                try:
                    import crashlog
                    crashlog.write(f"[player] header set failed: {exc!r}")
                except Exception:               # noqa: BLE001
                    pass
            try:
                self._set_opt("cache", "yes")
                self._set_opt("cache-secs", cache_secs)
                self._set_opt("demuxer-readahead-secs", readahead)
            except Exception:
                pass
            # Some stations (Goodvibes marks these "insecure") serve audio
            # behind a certificate that doesn't validate - self-signed, or
            # issued for a different hostname. mpv leaves tls-verify off by
            # default, but set it explicitly per station so the intent is
            # visible and so enabling verification globally later won't
            # silently break these.
            try:
                self._set_opt("tls-verify", not bool(insecure))
            except Exception:
                pass
            # Skipped when unchanged: "none" every song, and this was the
            # write the window froze on at most track changes (task 24).
            try:
                self._set_opt("start", start)
            except Exception:
                pass

        self._queue.put("options", options)
        self._radio_quality = bool(radio_quality)

        # Ad override. The stream still connects and plays immediately -
        # it has to, because the advert is inside it and the only way
        # past an advert is to let it run. What changes is what you
        # hear: the filler covers it, and the hand-over happens when the
        # ad is due to be over.
        self._ad_seconds = max(0.0, float(ad_seconds or 0.0))
        self._ad_deadline = None
        self._ad_state = "none"
        self.filler.stop()

        try:
            self._gain_db = max(-20.0, min(20.0, float(gain_db)))
        except (TypeError, ValueError):
            self._gain_db = 0.0
        self._apply_audio_filters(force=True)
        self._on_state(PlayerState.CONNECTING, "Connecting...")

        # What covers the ad: a list of candidates (build 64). The old
        # single filler_path is still taken, as a list of one.
        if ad_cover is None and filler_path:
            ad_cover = [{"kind": "music", "label": os.path.basename(
                str(filler_path)), "path": filler_path,
                "fraction": filler_fraction}]
        if self._ad_seconds > 0 and ad_cover:
            self._ad_state = "filler"
            # The static runs until the cover can really be heard, and
            # comes back over any gap in it (task 50). It used to fade
            # straight away, so a cover that was slow or silent left
            # nothing at all: the whole ad was silence.
            _log(f"[ad] covering the opening ad: {self._ad_seconds:.0f}s, "
                 f"{len(ad_cover)} cover(s) to try")
            if self._noise_enabled:
                self.noise.start()
                self._ad_static_on = True
            self.filler.play(ad_cover, volume=self._base_volume,
                             normalize=self._normalize)
        elif self._noise_enabled and use_noise:
            # The static keeps playing until audio is genuinely flowing,
            # then the two cross-fade - so a station that takes eight
            # seconds to buffer sounds like a dial being held between
            # frequencies rather than silence.
            #
            # `use_noise` is how a caller says this one is different. A
            # YouTube station has already spent its wait resolving, in
            # silence; starting the static at the moment the song is
            # finally found puts the tuning noise after the tuning.
            self.noise.start()
        # Whether the static is covering this connect. Decides how sure
        # tick() must be that audio is flowing before it fades it.
        self._covered = bool(self._noise_enabled and use_noise
                             and self._ad_state != "filler")

        crossfade = self._crossfade_ms
        # While an ad is covered, the station plays to mpv's null output:
        # it runs in real time (the ad has to play out) but opens nothing
        # in the desktop's mixer, so the mixer shows the cover alone
        # instead of two "Frequencies" entries (build 71, task 75). Its
        # real output opens when the ad time is up, under the static.
        ad_quiet = self._ad_state == "filler"

        def load():
            # The stream starts silent either way; what covers the gap is
            # either the static or, when an ad is being overridden, music.
            try:
                self._mpv.volume = 0
            except Exception:
                pass
            try:
                self._set_opt("ao", "null" if ad_quiet else "")
            except Exception as exc:            # noqa: BLE001
                _log(f"[ad] could not set the station's output: {exc!r}")
            entry, failed = None, None
            try:
                entry = self._loadfile(url)
            except Exception as exc:  # noqa: BLE001 - surface any backend error
                failed = exc
            with self._prop_lock:
                self._entry_id = entry
                self._pending = max(0, self._pending - 1)
            if failed is not None:
                self.noise.fade_out_and_stop(crossfade)
                self.filler.stop()
                self._on_state(PlayerState.ERROR, str(failed))

        self._queue.put("play", load)

    def _loadfile(self, url):
        """loadfile, returning mpv's id for the new entry (or None).

        The id tells this file's start and end apart from the last one's,
        which the queue makes possible to receive out of step.
        """
        try:
            import mpv as _mpv_mod
            name = url.encode(_mpv_mod.fs_enc) if isinstance(url, str) else url
            reply = self._mpv.command("loadfile", name, "replace")
        except AttributeError:
            # An older python-mpv without command() replies.
            self._mpv.play(url)
            return None
        if isinstance(reply, dict):
            try:
                return int(reply.get("playlist_entry_id") or 0) or None
            except (TypeError, ValueError):
                return None
        return None

    def stop(self, keep_noise=False):
        """Stop playback.

        `keep_noise` silences the stream but leaves the static running.
        That is what a YouTube station needs: it has to silence the
        outgoing station before it can start resolving, but the static
        is meant to run unbroken from the button press to the moment the
        resolved audio arrives. Tearing it down and starting it again a
        millisecond later reloads the noise files and leaves an audible
        hole exactly where the whole point was to have none.
        """
        self._current_url = None
        self._audio_started = False
        self._fade_gen += 1         # an old fade-in step must not land
        self._ad_state = "none"
        self._ad_deadline = None
        self.filler.stop()
        # Remembered for _on_end_file.
        #
        # Stopping a playing stream makes mpv send "end-file" a moment
        # later, on its own thread, and that handler stopped the static.
        # So keep_noise kept it for a few milliseconds and then the
        # end-file took it away anyway — static, then silence while the
        # next Twitch channel or YouTube video was looked up. That was
        # the silence in the middle of a tune that survived three fixes.
        self._noise_held = bool(keep_noise)
        self._covered = False
        self._handover_at = None
        self._rebuffering = False
        if not keep_noise:
            self.noise.stop()
        with self._prop_lock:
            self._want = False

        def halt():
            self._mpv.stop()
        # On the queue, like every mpv write: a stop straight after a song
        # ended waited for mpv's lock too (YouTube's next song, task 24).
        self._queue.put("stop", halt)
        self._on_state(PlayerState.STOPPED, "")

    def set_volume(self, value):
        self._base_volume = max(0, min(MAX_VOLUME, int(value)))
        # The static follows the slider too, scaled by its own level.
        try:
            self.noise.set_master(self._base_volume)
        except Exception:
            pass
        # And the ad cover, if one is playing (build 64).
        try:
            self.filler.set_volume(self._base_volume)
        except Exception:                       # noqa: BLE001
            pass
        # Don't yank the volume up mid-connect: the stream is
        # deliberately silent until the cross-fade brings it in.
        if not self._audio_started and self._current_url:
            return
        self._set_stream_volume(self._base_volume)

    def _set_stream_volume(self, value):
        """The stream's volume, set on the queue (after any play queued
        before it, which sets 0 first)."""
        def write():
            self._mpv.volume = value
        self._queue.put("volume", write)

    def _begin_audio(self):
        """Audio started flowing. Hand over, or start the ad clock."""
        """Called the moment real audio starts flowing.

        Fades the stream up while the static fades away, and records how
        long this station took to get here so its buffering setting can
        be based on evidence rather than a guess.
        """
        if self._audio_started:
            return

        # Hold the handover until the requested buffer has actually built
        # up.
        #
        # cache-secs only tells mpv how far ahead it may read; it never
        # delays playback, so a station set to buffer ten seconds still
        # started the moment the first packet decoded and dropped out
        # exactly as before. tick() calls this repeatedly, so returning
        # early simply tries again a moment later — with a ceiling, since
        # a stream that never fills its cache must not be held forever.
        if self._buffer_target > 0 and self._connect_started is not None:
            waited = time.monotonic() - self._connect_started
            if (self.get_buffer_seconds() < self._buffer_target
                    and waited < self._buffer_target + 12):
                return

        self._audio_started = True

        elapsed = 0.0
        if self._connect_started:
            elapsed = max(0.0, time.monotonic() - self._connect_started)
        if self._on_connected is not None:
            try:
                self._on_connected(self._current_url, elapsed)
            except Exception:
                pass

        if self._ad_state == "filler":
            # The stream is live but inaudible, playing its advert to
            # nobody. Start counting from here rather than from the
            # connection attempt: a station that took six seconds to
            # buffer has not yet played six seconds of advert.
            self._ad_deadline = time.monotonic() + self._ad_seconds
            return

        # The static fades, the song does not.
        #
        # Fading both meant the handover had a hole in it: the noise on
        # its way out was already quiet and the music on its way in was
        # quieter still. The stream comes up at full level underneath the
        # noise, and the noise gets out of the way.
        if self._covered:
            self._handover_at = time.monotonic()
            try:
                import crashlog
                crashlog.write(f"[static] hand-over: audio flowing after "
                               f"{elapsed:.1f}s"
                               + (f" ({self._handover_why})"
                                  if self._handover_why else ""))
            except Exception:                   # noqa: BLE001
                pass
        self.noise.fade_out_and_stop(self._crossfade_ms)
        self._fade_stream_in(0)
        self._queue_sound_check()

    # -- sound check (task 37) ---------------------------------------
    # Build 55 played only the static on the user's PC: the stream ran
    # (its position moved) and nothing was heard, whatever the slider or
    # the output device. This could not be reproduced here, so after
    # every hand-over mpv's own view of the stream's sound is read back
    # (on the queue, after the volume write) and written to the log as
    # one line, and anything that would silence it is put right.
    SOUND_CHECK_DELAY = 1.5

    def _queue_sound_check(self):
        url = self._current_url
        want = self._base_volume

        def later():
            if self._current_url != url or not self._audio_started:
                return                  # moved on: not this stream's check
            self._queue.put("sound check",
                            lambda: self._sound_check(want))
        timer = threading.Timer(self.SOUND_CHECK_DELAY, later)
        timer.daemon = True
        timer.start()

    def _sound_check(self, want):
        """Runs on the queue thread: the only place that may ask mpv."""
        def ask(name):
            try:
                return self._mpv[name] if name == "af" else \
                    getattr(self._mpv, name.replace("-", "_"))
            except Exception:           # noqa: BLE001
                return None

        volume, mute = ask("volume"), ask("mute")
        sys_level, sys_mute = ask("ao-volume"), ask("ao-mute")
        fixed = []
        try:
            want = int(self._base_volume)   # the slider may have moved
        except (TypeError, ValueError):
            pass
        if isinstance(volume, (int, float)) and want > 0 \
                and volume < want - 1:
            self._mpv.volume = want
            fixed.append(f"volume {volume:.0f} -> {want}")
        if mute is True:
            self._mpv.mute = False
            fixed.append("mute off")
        # The stream's own level in the desktop's mixer (PipeWire /
        # PulseAudio). The app never sets it; something outside did.
        if sys_mute is True:
            try:
                self._mpv.ao_mute = False
                fixed.append("system mute off")
            except Exception:           # noqa: BLE001
                pass
        if isinstance(sys_level, (int, float)) and sys_level < 1:
            try:
                self._mpv.ao_volume = 100
                fixed.append(f"system level {sys_level:.0f}% -> 100%")
            except Exception:           # noqa: BLE001
                pass

        def show(value, unit=""):
            if value is None:
                return "?"
            if isinstance(value, bool):
                return "yes" if value else "no"
            if isinstance(value, float):
                return f"{value:.0f}{unit}"
            return f"{value}{unit}"

        out = ask("audio-out-params") or {}
        if isinstance(out, dict) and out:
            params = (f"{out.get('samplerate', '?')} Hz "
                      f"{out.get('channel-count', '?')}ch "
                      f"{out.get('format', '?')}")
        else:
            params = "none (nothing reaching the output)"
        filters = ask("af")
        count = len(filters) if isinstance(filters, list) else "?"
        line = (f"[player] sound check: volume {show(volume)} "
                f"(slider {want}), mute {show(mute)}, "
                f"system level {show(sys_level, '%')}, "
                f"system mute {show(sys_mute)}, "
                f"output {show(ask('current-ao'))} / "
                f"{show(ask('audio-device'))}, "
                f"filters {count}, out {params}")
        if fixed:
            line += " — fixed: " + ", ".join(fixed)
        try:
            import crashlog
            crashlog.write(line)
        except Exception:               # noqa: BLE001
            pass

    # How long after the hand-over a stall brings the static back.
    #
    # A Twitch stream often plays a moment and then stops to fill its
    # buffer for several seconds. That is still part of tuning in, so
    # the static covers it instead of silence (task 18).
    REBUFFER_WINDOW = 12.0

    def _audio_is_flowing(self, position, now):
        """Whether a moving position really means audio is playing.

        With the static covering, the first position mpv reports is not
        proof: a Twitch (HLS) stream gives a position as soon as it has
        opened, then can wait seconds for data. Fading the static there
        left silence (task 18). So it needs a second reading that has
        moved on at a sane rate. Without the static there is nothing to
        cover, and the song should start at once.
        """
        if not self._covered:
            return True
        if self._first_seen is None:
            self._first_seen = (position, now)
            return False
        first_pos, first_time = self._first_seen
        advanced = position - first_pos
        if advanced > (now - first_time) * 2.0 + 0.3:
            # A jump, not playback (a live stream seeking to its edge).
            # Measure again from here.
            self._first_seen = (position, now)
            return False
        return advanced >= 0.3

    # Build 64 (task 38): the static stays on while the stream is silent,
    # however long the position has been moving — a Twitch pre-roll ad
    # arrives as silence, and the static faded over it. This long at
    # most: a station that really is quiet still gets handed over.
    SILENT_LIMIT = 45.0
    # After the hand-over, silence this long (within REBUFFER_WINDOW)
    # brings the static back, like a stall does.
    QUIET_GAP = 1.5

    def _level(self):
        """The stream's loudness from the meter (dB), or None."""
        return level_db(self._reported(LEVEL_PROP))

    def _track_level(self, now):
        """Keep _audible_since / _quiet_since up to date (every tick)."""
        level = self._level()
        if level is None:
            self._audible_since = None
            self._quiet_since = None
        elif level > AUDIBLE_DB:
            if self._audible_since is None:
                self._audible_since = now
            self._quiet_since = None
        else:
            if self._quiet_since is None:
                self._quiet_since = now
            self._audible_since = None
        return level

    def _sound_ready(self, now):
        """Whether the covered stream can be heard (or can't be told).

        No meter reading at all: this mpv has no meter, so the position
        alone decides, as before build 64.
        """
        level = self._level()
        if level is None:
            self._handover_why = "no level meter"
            return True
        if (self._audible_since is not None
                and now - self._audible_since >= 0.4):
            self._handover_why = f"sound at {_show_db(level)}"
            return True
        if (self._moved_at is not None
                and now - self._moved_at > self.SILENT_LIMIT):
            self._handover_why = (f"still silent after "
                                  f"{self.SILENT_LIMIT:.0f}s, handing over "
                                  f"anyway")
            return True
        return False

    def _watch_rebuffer(self, moving, now):
        """Bring the static back over a stall or a silence right after
        the hand-over, and fade it away again when sound comes back."""
        if not self._audio_started or self._handover_at is None:
            return
        quiet = (now - self._quiet_since) if self._quiet_since else 0.0
        stalled = (not moving and self._last_progress is not None
                   and now - self._last_progress >= 0.9)
        if self._rebuffering:
            if now - self._handover_at > self.SILENT_LIMIT:
                # Never static for ever: a station that stays silent is
                # handed over anyway.
                self._rebuffering = False
                _log(f"[static] still no sound "
                     f"{self.SILENT_LIMIT:.0f}s after the hand-over, "
                     f"static fades anyway")
                self.noise.fade_out_and_stop(self._crossfade_ms)
                return
            if moving and self._quiet_since is None:
                self._rebuffering = False
                _log(f"[static] stream sounding again "
                     f"({_show_db(self._level())}), static fades")
                self.noise.fade_out_and_stop(self._crossfade_ms)
            return
        if (not self._noise_enabled
                or now - self._handover_at > self.REBUFFER_WINDOW):
            return
        if stalled:
            why = "stalled"
        elif quiet >= self.QUIET_GAP:
            why = f"silent for {quiet:.1f}s"
        else:
            return
        self._rebuffering = True
        _log(f"[static] stream {why} {now - self._handover_at:.1f}s after "
             f"the hand-over, static back on")
        self.noise.start()

    # The trace: what the tune-in looks like, once a second, until the
    # stream has played for this long after the hand-over (task 38: so a
    # log shows what happens in the gaps).
    TRACE_AFTER_HANDOVER = 15.0
    TRACE_MAX_LINES = 120

    def _trace(self, now, position, moving):
        if now < self._trace_next or self._trace_lines >= self.TRACE_MAX_LINES:
            return
        if self._ad_state == "none":
            if not (self._covered or self._rebuffering):
                return
            if (self._handover_at is not None and not self._rebuffering
                    and now - self._handover_at > self.TRACE_AFTER_HANDOVER):
                return
        self._trace_next = now + 1.0
        self._trace_lines += 1
        began = self._play_began or now
        cache = self._reported("demuxer-cache-duration")
        waiting = self._reported("paused-for-cache")
        pos = "none" if position is None else f"{position:.1f}"
        static = "on" if self.noise._active else "off"
        if self._ad_state != "none":
            left = (f"{self._ad_deadline - now:.0f}s"
                    if self._ad_deadline else "not started")
            _log(f"[ad] {now - began:4.1f}s: {self._ad_state}, ad left "
                 f"{left}, {self.filler.describe(now)}; station pos {pos}, "
                 f"level {_show_db(self._level())}; static {static}")
            return
        if not self._audio_started:
            stage = "tuning"
        elif self._rebuffering:
            stage = "static back"
        else:
            stage = "playing"
        _log(f"[tune] {now - began:4.1f}s: {stage}, pos {pos} "
             f"({'moving' if moving else 'still'}), level "
             f"{_show_db(self._level())}, cache "
             f"{'?' if cache is None else f'{float(cache):.1f}s'}, "
             f"waiting for data {'?' if waiting is None else ('yes' if waiting else 'no')}, "
             f"static {static}")

    def _tick_ad(self, now=None):
        """Move the ad cover through cover -> static -> station.

        Called on every tick (build 64): the cover has to be watched
        before the station has made a sound, too.
        """
        if self._ad_state == "none":
            return
        now = now or time.monotonic()

        if self._ad_state == "filler":
            self.filler.poll(now)
            # The static fills any moment the cover can't be heard: while
            # it connects, if it fails, and across a gap (task 50).
            if self._noise_enabled:
                heard = self.filler.audible(now)
                if heard and self._ad_static_on:
                    self._ad_static_on = False
                    self.noise.fade_out_and_stop(self._crossfade_ms)
                elif not heard and not self._ad_static_on:
                    self._ad_static_on = True
                    _log(f"[ad] cover can't be heard "
                         f"({self.filler.describe(now)}), static on")
                    self.noise.start()

        if self._ad_deadline is None:
            return
        remaining = self._ad_deadline - now

        if self._ad_state == "filler" and remaining <= self.AD_OUTPUT_EARLY:
            # Open the station's output a few seconds early, still at
            # volume 0 under the cover: reopening it restarts the filter
            # chain (levelling holds ~3 s back) and the meter, and a
            # hand-over straight after had drop-outs and no level.
            self._ad_output_back()

        if self._ad_state == "filler" and remaining <= 0:
            # Cover out, static in. The static is what sells it: a
            # straight cut from music to a different station sounds like
            # an edit, where a burst of noise sounds like the dial
            # moving.
            _log(f"[ad] ad time is up ({self.filler.describe(now)}): "
                 f"static, then the station")
            self._ad_state = "static"
            # The static starts at full level, then the cover goes in a
            # quick fade under it. A full cross-fade kept the cover's
            # handle open ~0.8 s beside the static's and the station's:
            # three "Frequencies" entries in the mixer at the very end of
            # the ad (task 84, build 78). Without the static the cover
            # keeps the full fade.
            if self._noise_enabled:
                self.noise.start()
                self._ad_static_on = True
                self.filler.fade_out_and_stop(self.AD_COVER_OUT_MS)
            else:
                self.filler.fade_out_and_stop(self._crossfade_ms)
            self._ad_deadline = now + (self._crossfade_ms / 1000.0) + 0.6
            return

        if self._ad_state == "static" and remaining <= 0:
            self._ad_state = "none"
            self._ad_deadline = None
            self._ad_static_on = False
            level = self._level()
            self._fade_stream_in(self._crossfade_ms)
            self._queue_sound_check()
            if (level is not None and level <= AUDIBLE_DB
                    and self._noise_enabled):
                # The ad ran longer than set (a Twitch pre-roll often
                # does) and the station is still silent: the static stays
                # until it sounds, as after any hand-over.
                _log(f"[ad] ad time is up but the station is still "
                     f"silent ({_show_db(level)}): static stays until it "
                     f"sounds")
                self._handover_at = now
                self._rebuffering = True
                return
            _log(f"[ad] hand-over to the station (level "
                 f"{_show_db(level)})")
            self.noise.fade_out_and_stop(self._crossfade_ms)

    def ad_time_left(self):
        """Seconds of the covered ad still to run, for the hourglass
        (GUI thread, no mpv calls). The whole ad while the station is
        still connecting (the ad clock starts when its sound does), 0
        once the ad time is up, None when no covered ad is running.
        (build 71, task 80: the glass kept its own time from the button
        press, so it still had sand when the ad was over.)"""
        if self._ad_state == "none":
            return None
        if self._ad_state == "static":
            return 0.0
        if self._ad_deadline is None:
            return self._ad_seconds
        return max(0.0, self._ad_deadline - time.monotonic())

    # Seconds before the ad's end that the station's output opens.
    AD_OUTPUT_EARLY = 5.0
    # The cover's fade-out under the static at the ad's end (task 84).
    AD_COVER_OUT_MS = 150

    def _ad_output_back(self):
        """Open the station's real sound output again after an ad
        (it played to mpv's null output while covered, see play()).
        Once per tune-in."""
        if self._ad_output_open:
            return
        self._ad_output_open = True

        def write():
            try:
                self._set_opt("ao", "")
            except Exception as exc:            # noqa: BLE001
                _log(f"[ad] could not open the station's output: {exc!r}")
                return
            _log("[ad] station's sound output opened (it was silent to "
                 "the mixer while the ad was covered)")
        self._queue.put("ad output", write)

    def cancel_ad_override(self):
        """Abandon the override and hand straight over to the stream."""
        if self._ad_state == "none":
            return
        _log("[ad] cover cancelled, straight to the station")
        self._ad_state = "none"
        self._ad_deadline = None
        self._ad_static_on = False
        self.filler.fade_out_and_stop(200, why="cancelled")
        self._ad_output_back()
        # The static fades, the song does not.
        #
        # Fading both meant the handover had a hole in it: the noise on
        # its way out was already quiet and the music on its way in was
        # quieter still. The stream comes up at full level underneath the
        # noise, and the noise gets out of the way.
        self.noise.fade_out_and_stop(self._crossfade_ms)
        self._fade_stream_in(0)

    def _fade_stream_in(self, ms):
        """Bring the station's volume up over `ms` (0: at once).

        Each step is a write on the mpv queue, like every other write to
        this handle (task 60): the fade's own thread only keeps time. It
        wrote `self._mpv.volume` itself before build 69 — the one write
        not on the queue — so a step could land after newer writes, and
        a slider moved during the fade was undone by the fade's end.

        Each step aims at the volume as it is when the step runs, so the
        fade ends wherever the slider is by then. A newer play, stop or
        fade bumps `_fade_gen`, and steps from the old one stand down.
        """
        self._fade_gen += 1
        gen = self._fade_gen
        if not ms:
            self._set_stream_volume(self._base_volume)
            return

        steps = max(1, int(ms / 35))

        def step(fraction):
            if gen != self._fade_gen or not self._audio_started:
                return
            self._mpv.volume = int(round(self._base_volume * fraction))

        def run():
            for i in range(1, steps + 1):
                if gen != self._fade_gen or not self._audio_started:
                    return
                self._queue.put("fade in",
                                lambda f=i / steps: step(f))
                time.sleep(ms / 1000.0 / steps)

        thread = threading.Thread(target=run, daemon=True,
                                  name="StationFadeIn")
        thread.start()

    def get_buffer_seconds(self):
        """How far behind the live edge playback currently is.

        mpv buffers ahead of what you're hearing, so the audio coming
        out of the speakers is this many seconds older than the bytes
        arriving from the station. Anything that opens a *second*
        connection to the same stream - transcription, song
        identification - is reading the live edge and is therefore ahead
        of you by roughly this much.
        """
        # From mpv's reports; asking would wait for its lock (task 24).
        try:
            value = self._reported("demuxer-cache-duration")
            if value is None:
                return 0.0
            return max(0.0, min(60.0, float(value)))
        except Exception:
            return 0.0

    def get_volume(self):
        try:
            return int(self._mpv.volume)
        except Exception:
            return 80

    def shutdown(self):
        self.noise.shutdown()
        self.filler.shutdown()
        self.clips.shutdown()
        # Nothing may still be calling mpv when its handle goes: a call
        # on a terminated handle is a crash. What is queued is dropped
        # (terminate stops everything anyway); a call already running
        # gets two seconds, far longer than mpv ever kept one waiting.
        if not self._queue.close(timeout=2.0):
            try:
                import crashlog
                crashlog.write("[player] mpv still busy at quit; "
                               "left for the system to close")
            except Exception:                   # noqa: BLE001
                pass
            return
        try:
            self._mpv.terminate()
        except Exception:
            pass

    # -- mpv event handlers --------------------------------------------
    def _on_file_loaded(self, event=None):
        # Deliberately does NOT stop the static.
        #
        # file-loaded fires when mpv has opened the stream and read
        # enough to know what it is - which can be seconds before any
        # sound reaches the speakers on a slow station. Cutting the
        # static here left a silent gap that felt like the app had
        # frozen. The hand-off now happens in tick(), when playback
        # time actually starts moving.
        self._on_state(PlayerState.PLAYING, "")

    def current_source(self):
        """The URL actually being played.

        For an ordinary station this is the station's own URL, but a
        custom station resolves to a local file or a temporary YouTube
        media URL - and that is what has to be fingerprinted. Reading it
        back from the player means identification never has to know how
        the station picked its source.
        """
        return self._current_url

    def position_seconds(self):
        """Playback position, or None for a live stream.

        From mpv's own reports, not asked for. The lyric clock calls this
        4×/s, and an ask waits for mpv's lock — 450 ms once, at a track
        change (task 24, build 55).
        """
        try:
            value = self._reported("time-pos")
            return float(value) if value else None
        except Exception:
            return None

    def _mpv_log(self, level, component, message):
        # Errors, and warnings about getting the stream (network, the
        # stream layer, the demuxer); not the decoder's per-packet chatter.
        if level != "error" and component not in ("ffmpeg", "stream") \
                and not str(component).startswith("demux"):
            return
        line = f"{level} {component}: {(message or '').strip()}"
        if not self._mpv_said or self._mpv_said[-1] != line:
            self._mpv_said.append(line)

    def _log_mpv_said(self, url):
        """Write what mpv said about `url` since it was asked to play it."""
        said = list(self._mpv_said)
        self._mpv_said.clear()
        for line in said:
            _log(f"[stream] mpv said: {line}")
        if not said:
            _log(f"[stream] mpv said nothing about {url} (no error: it "
                 f"was still connecting)")

    def set_stall_timeout(self, seconds):
        self._stall_timeout = max(2, int(seconds or 6))

    def _try_next_listed(self, now):
        """Before calling a playlist station down, try its next stream.

        A .pls usually lists the same station on several servers (SomaFM
        lists five). When the one mpv picked first makes no sound in
        CONNECT_TIMEOUT, the next one gets the same time, once per
        tune-in, the static still on (task 91). False when there is no
        other stream to try.
        """
        if self._next_tries >= 1:
            return False
        with self._prop_lock:
            started = self._started_id
            fresh = self._fresh()
            where = self._listed.place(started) if fresh else None
            more = bool(where and self._listed.has_next(started))
        if not more:
            return False
        self._next_tries += 1
        _log(f"[tune] playlist stream {where[0]} of {where[1]} made no "
             f"sound in {self.CONNECT_TIMEOUT}s, trying the next one "
             f"before calling the station down")
        self._connect_started = now
        self._moved_at = None
        self._first_seen = None
        self._last_progress = None
        self._last_position = None
        self._audible_since = None
        self._quiet_since = None

        def skip():
            try:
                self._mpv.command("playlist-next", "force")
            except Exception as exc:            # noqa: BLE001
                _log(f"[tune] could not move to the next stream: {exc!r}")
        self._queue.put("playlist-next", skip)
        return True

    # Task 88: after the hand-over, sound that stops with nobody
    # pressing pause. mpv paused by something else: this long, then the
    # station pauses for real. A station that asked for quiet_stop
    # (Twitch): silence from the stream this long.
    PAUSED_STOP_S = 3.0
    QUIET_STOP_S = 30.0

    def _watch_quiet(self, now):
        """Pause for real when the sound has stopped on its own.

        True when it did (tick has nothing more to do)."""
        if (self._quiet_done or not self._audio_started
                or self._handover_at is None or self._ad_state != "none"):
            return False
        if self._reported("pause") is True:
            if self._paused_since is None:
                self._paused_since = now
                _log("[playstate] mpv says it is paused, but nothing in "
                     "the app paused it")
                self._sound_report("mpv paused")
                return False
            if now - self._paused_since >= self.PAUSED_STOP_S:
                return self._went_quiet(
                    f"mpv was paused from outside the app for "
                    f"{now - self._paused_since:.0f}s")
            return False
        if self._paused_since is not None:
            _log("[playstate] mpv is playing again by itself")
            self._paused_since = None
        if not self._quiet_stop or self._rebuffering \
                or self._quiet_since is None:
            return False
        # Counted from the hand-over at the earliest: a stream handed
        # over "still silent after 45s" gets its own 30 s.
        quiet = now - max(self._quiet_since, self._handover_at)
        if quiet >= self.QUIET_STOP_S / 2 and not self._quiet_warned:
            self._quiet_warned = True
            _log(f"[playstate] the stream has been silent for "
                 f"{quiet:.0f}s (level {_show_db(self._level())}); "
                 f"pausing at {self.QUIET_STOP_S:.0f}s if it stays silent")
            self._sound_report("silent stream")
        if quiet >= self.QUIET_STOP_S:
            return self._went_quiet(
                f"the stream sent only silence for {quiet:.0f}s")
        return False

    def _went_quiet(self, why):
        self._quiet_done = True
        _log(f"[playstate] the sound stopped: {why} - pausing, so the "
             f"play button shows it")
        callback = self.on_went_quiet
        if callback is not None:
            try:
                callback(why)
            except Exception as exc:            # noqa: BLE001
                _log(f"[playstate] pausing failed: {exc!r}")
        return True

    # The [sound] trace (task 88): one line a second for this long after
    # a link was opened in the browser, with the output's state every
    # few lines.
    SOUND_WATCH_S = 30.0
    SOUND_REPORT_EVERY = 5

    def watch_sound(self, why, seconds=None):
        """Log what the station's sound does for a while (GUI thread)."""
        now = time.monotonic()
        seconds = self.SOUND_WATCH_S if seconds is None else seconds
        self._sound_watch = (why, now, now + seconds)
        self._sound_watch_next = 0.0
        self._sound_watch_lines = 0
        if not self._current_url:
            _log(f"[sound] {why}: nothing is playing")
            self._sound_watch = None
            return
        stage = "playing" if self._audio_started else "tuning in"
        _log(f"[sound] {why}: the station is {stage}; watching its sound "
             f"for {seconds:.0f}s")

    def _end_sound_watch(self, why):
        if self._sound_watch is not None:
            _log(f"[sound] {self._sound_watch[0]}: {why}, watch ends")
            self._sound_watch = None

    def _trace_sound(self, now, position, moving):
        watch = self._sound_watch
        if watch is None or now < self._sound_watch_next:
            return
        why, since, until = watch
        if now > until:
            self._end_sound_watch(f"{until - since:.0f}s are up")
            return
        self._sound_watch_next = now + 1.0
        pos = "none" if position is None else f"{position:.1f}"
        paused = self._reported("pause")
        waiting = self._reported("paused-for-cache")

        def yes(v):
            # Not reported since the file started = never changed.
            return "yes" if v else "no"
        stage = ("playing" if self._audio_started else "tuning")
        if self._rebuffering:
            stage = "static back"
        _log(f"[sound] {now - since:4.1f}s after {why}: {stage}, pos {pos} "
             f"({'moving' if moving else 'still'}), level "
             f"{_show_db(self._level())}, mpv paused {yes(paused)}, "
             f"waiting for data {yes(waiting)}, static "
             f"{'on' if self.noise._active else 'off'}")
        if self._sound_watch_lines % self.SOUND_REPORT_EVERY == 0:
            self._sound_report(f"{now - since:.0f}s after {why}")
        self._sound_watch_lines += 1

    def _sound_report(self, label):
        """The output's side, read on the queue (never the GUI thread):
        what the desktop's mixer says about the stream."""
        def report():
            def ask(name):
                try:
                    return getattr(self._mpv, name.replace("-", "_"))
                except Exception:               # noqa: BLE001
                    return None

            def show(value, unit=""):
                if value is None:
                    return "?"
                if isinstance(value, bool):
                    return "yes" if value else "no"
                if isinstance(value, float):
                    return f"{value:.0f}{unit}"
                return f"{value}{unit}"
            out = ask("audio-out-params") or {}
            params = (f"{out.get('samplerate', '?')} Hz "
                      f"{out.get('channel-count', '?')}ch"
                      if isinstance(out, dict) and out
                      else "none (nothing reaching the output)")
            _log(f"[sound] output ({label}): volume {show(ask('volume'))}, "
                 f"mute {show(ask('mute'))}, system level "
                 f"{show(ask('ao-volume'), '%')}, system mute "
                 f"{show(ask('ao-mute'))}, output {show(ask('current-ao'))}"
                 f" / {show(ask('audio-device'))}, out {params}")
        self._queue.put("sound report", report)

    def tick(self):
        """Called on a timer by the UI. Watches for two things.

        Whether audio has genuinely started, so the static can be faded
        out at the right moment; and whether it has silently stopped, so
        a dead stream doesn't leave the user staring at a station that
        claims to be playing.
        """
        # A stop asked of the ad cover that never ran is logged, even
        # after the station itself has stopped (task 74).
        self.filler.watch_stop()
        if not self._current_url:
            self._end_sound_watch("the station is stopped")
            return

        # A stream that never starts is a stream that failed.
        #
        # Nothing was watching the connecting state itself: mpv sat
        # waiting on a host that answered and then sent nothing, and the
        # app sat with it, indefinitely. Two dead stations in a row meant
        # two silences with no error and no skip. The stall timeout
        # already covers audio that stops; this covers audio that never
        # starts.
        #
        # Build 64: counted from the last time the position moved, once
        # it has. A stream that plays silence (a Twitch pre-roll ad) is
        # alive, and is no longer failed for not being heard in 10 s.
        now = time.monotonic()
        since = (self._last_progress if self._moved_at is not None
                 and self._last_progress is not None
                 else self._connect_started)
        if (not self._audio_started and since
                and now - since > self.CONNECT_TIMEOUT
                and self._try_next_listed(now)):
            return
        if (not self._audio_started and since
                and now - since > self.CONNECT_TIMEOUT):
            url, self._current_url = self._current_url, ""
            self._connect_started = None
            _log(f"[tune] gave up: the position "
                 f"{'stopped moving' if self._moved_at else 'never moved'} "
                 f"for {self.CONNECT_TIMEOUT}s")
            self.noise.fade_out_and_stop(self._crossfade_ms)
            self.filler.stop()
            self._ad_state = "none"
            self._log_mpv_said(url)
            self._on_state(PlayerState.ERROR,
                           "That stream never started playing.")
            if self._on_stalled is not None:
                try:
                    self._on_stalled(url)
                except Exception:               # noqa: BLE001
                    pass
            return

        # Coerced rather than trusted. time_pos is normally a float or
        # None, but it is read straight off the mpv handle and anything
        # non-numeric coming back would poison _last_position and make
        # every later comparison raise.
        try:
            raw = self._reported("time-pos")
            position = float(raw) if isinstance(raw, (int, float)) else None
        except Exception:
            position = None

        moving = position is not None and (
            self._last_position is None or position > self._last_position + 0.05
        )

        self._track_level(now)
        self._tick_ad(now)
        self._trace(now, position, moving)
        self._trace_sound(now, position, moving)
        if self._watch_quiet(now):
            return

        if moving:
            self._last_position = position
            self._last_progress = now
            if self._moved_at is None:
                self._moved_at = now
            if not self._audio_started:
                # Moving is not enough: it must also be heard (task 38).
                if (self._audio_is_flowing(position, now)
                        and (not self._covered or self._sound_ready(now))):
                    self._begin_audio()
            else:
                self._watch_rebuffer(True, now)
            return

        if position is not None:
            self._last_position = position
        if self._last_progress is None:
            self._last_progress = now
            return
        self._watch_rebuffer(False, now)

        # Only call it dead once audio had actually started; a station
        # still connecting is handled by its own timeout, and the static
        # is already covering it.
        if self._audio_started and (now - self._last_progress) > self._stall_timeout:
            url = self._current_url
            self._last_progress = now
            if self._on_stalled is not None:
                try:
                    self._on_stalled(url)
                except Exception:
                    pass

    # mpv error codes we care about telling apart.
    MPV_ERROR_AO_INIT_FAILED = -14
    MPV_ERROR_VO_INIT_FAILED = -15

    def _on_end_file(self, event):
        try:
            reason = event.data.reason
            error_code = event.data.error
        except Exception:
            reason, error_code = None, 0
        try:
            entry = int(event.data.playlist_entry_id) or None
        except Exception:                       # noqa: BLE001
            entry = None

        if reason == 5:
            # A playlist (.pls / .m3u) opened: mpv plays the streams it
            # lists as new entries. Nothing ended (task 91).
            with self._prop_lock:
                count = self._listed.redirect(event)
                ours = bool(entry and self._entry_id
                            and entry in self._listed.family(self._entry_id))
            if count and ours:
                _log(f"[tune] playlist: {count} stream(s) listed, "
                     f"playing the first")
            return

        with self._prop_lock:
            for name in self._POSITIONS:
                self._props.pop(name, None)
            # The end of an older file. With the queue, a new play can be
            # asked for while the last song ends: that song's end must not
            # count as the new one's (skip a track, or call it broken).
            family = (self._listed.family(self._entry_id)
                      if self._entry_id else set())
            stale = bool(self._pending or (
                entry and self._entry_id and entry not in family))
            # One of a playlist's streams failed, and the playlist lists
            # more: mpv goes on to the next one by itself.
            moves_on = bool(not stale and reason == 4 and entry
                            and self._listed.has_next(entry))
            where = self._listed.place(entry) if moves_on else None
        if stale and reason in (0, 4):
            return
        if moves_on:
            _log(f"[tune] playlist stream {where[0]} of {where[1]} failed "
                 f"(error {error_code}), mpv tries the next one")
            self._connect_started = time.monotonic()
            return

        # Only report as an error if we were actually trying to play
        # something (a manual stop() also triggers end-file/ABORTED).
        if reason == 4:  # MpvEventEndFile.ERROR
            self.noise.stop()
            self.filler.stop()
            self._ad_state = "none"
            if error_code == self.MPV_ERROR_AO_INIT_FAILED:
                # The stream was fine - we couldn't open an audio
                # device. Reporting this as a dead stream would blame
                # the station, mark it down and skip to the next one,
                # which then fails identically, all the way through the
                # library. Distinct state, no retry, no skip.
                self._on_state(
                    PlayerState.NO_AUDIO,
                    "Couldn't open an audio device - is something else "
                    "using it exclusively?"
                )
            elif error_code == self.MPV_ERROR_VO_INIT_FAILED:
                self._on_state(PlayerState.NO_AUDIO,
                               "Couldn't initialize output.")
            else:
                # A moment later: mpv's reason can arrive just after the
                # end-file that it explains, on this same thread.
                threading.Timer(0.5, self._log_mpv_said,
                                (self._current_url,)).start()
                self._on_state(PlayerState.ERROR,
                               "Couldn't connect to that stream.")
        elif reason == 0 and self._current_url is not None:
            # A file played to its end. Live streams never do this, so
            # in practice it means a local station's track finished -
            # the station should roll on to the next one rather than
            # falling silent.
            if self._on_track_finished is not None:
                try:
                    self._on_track_finished()
                    return
                except Exception:
                    pass
            self._on_state(PlayerState.STOPPED, "")
        elif self._current_url is None:
            # Left alone when the stop asked to keep the static.
            if not getattr(self, "_noise_held", False):
                self.noise.stop()
            self._on_state(PlayerState.STOPPED, "")

    def _on_tags(self, name, value):
        callback = getattr(self, "on_tags", None)
        # Only tags of the file asked for: tested, the last folder's song
        # was reported again just after a station change.
        with self._prop_lock:
            fresh = self._fresh()
        if not fresh:
            return
        if callback is not None and isinstance(value, dict) and value:
            try:
                callback(dict(value))
            except Exception:                   # noqa: BLE001
                pass

    def _on_media_title(self, name, value):
        if value:
            cleaned = clean_stream_title(str(value))
            if cleaned:
                self._on_metadata(cleaned)
