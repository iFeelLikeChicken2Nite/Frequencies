"""
storage.py - Persistence for the station list, settings and per-stream
health history.

Everything lives in the user's config directory (XDG_CONFIG_HOME or
~/.config/frequencies), so it survives between runs of the
AppImage:

    stations.json   the station list, including custom names and the
                    per-station options (shazam / vocal clarity)
    settings.json   global settings (volume, EQ, shuffle, noise...)
    health.json     per-station reachability history, so a station that
                    was down yesterday still shows as down today

There is also backup/restore, which packs all three into a single
portable .json file.
"""
import base64
import json
import os
import random
import shutil
import time
import uuid

APP_DIR_NAME = "frequencies"
# The app used to be called StreamDeck Radio. Anyone upgrading has their
# stations, settings, health and history under the old directory, so the
# first run under the new name moves them across rather than silently
# starting from an empty library.
LEGACY_APP_DIR_NAME = "streamdeck-radio"
STATIONS_FILE = "stations.json"
SETTINGS_FILE = "settings.json"
HEALTH_FILE = "health.json"

BACKUP_FORMAT = "frequencies-backup"
# Backups written by the old name are still valid input.
LEGACY_BACKUP_FORMATS = ("streamdeck-radio-backup",)
BACKUP_VERSION = 2


def is_backup_format(value):
    return value == BACKUP_FORMAT or value in LEGACY_BACKUP_FORMATS


def _config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    path = os.path.join(base, APP_DIR_NAME)
    if not os.path.isdir(path):
        legacy = os.path.join(base, LEGACY_APP_DIR_NAME)
        if os.path.isdir(legacy):
            try:
                # Copy rather than move, so an older build still works if
                # the user goes back to it.
                shutil.copytree(legacy, path)
                return path
            except OSError:
                pass
    os.makedirs(path, exist_ok=True)
    return path


def stations_path():
    return os.path.join(_config_dir(), STATIONS_FILE)


def settings_path():
    return os.path.join(_config_dir(), SETTINGS_FILE)


def bundled_visualizer_disabled():
    """File names of presets the developer disabled (task 119), shipped
    in visualizer_disabled.json next to this file."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "visualizer_disabled.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    names = data.get("presets") if isinstance(data, dict) else None
    return [str(n) for n in names or () if str(n).strip()]


def seed_visualizer_disabled(settings, presets):
    """First time only: start a fresh install with the developer's disabled
    presets. `presets` is projectm.list_presets() ([(name, path)]). A list
    the user already has, or one they changed later, is never touched.
    Returns True when `settings` changed (the caller saves)."""
    if settings.get("visualizer_disabled_seeded"):
        return False
    if not presets:
        return False  # no presets found yet: try again next time
    settings["visualizer_disabled_seeded"] = True
    if settings.get("visualizer_disabled"):
        return True
    names = settings.get("visualizer_disabled_names")
    shipped = set(bundled_visualizer_disabled() if names is None else names)
    settings["visualizer_disabled"] = sorted(
        path for _name, path in presets if os.path.basename(path) in shipped)
    return True


def take_visualizer_list(settings, names):
    """The developer's published list arrived (build 106, task 142): a
    user build fetches it until it gets it once. It becomes this install's
    list unless the user already made the list their own. The next
    visualizer opened applies it (seed_visualizer_disabled). Returns True
    when the list changed."""
    settings["visualizer_list_fetched"] = True
    if not settings.get("visualizer_disabled_mine") and settings.get(
            "visualizer_disabled_seeded"):
        # An install from before build 106: a list that is no longer the
        # shipped one was changed by the user.
        have = {os.path.basename(str(p)) for p in
                settings.get("visualizer_disabled") or ()}
        if have != set(bundled_visualizer_disabled()):
            settings["visualizer_disabled_mine"] = True
    if settings.get("visualizer_disabled_mine"):
        return False
    settings["visualizer_disabled_names"] = sorted(
        {str(n) for n in names if str(n).strip()})
    settings["visualizer_disabled"] = []
    settings["visualizer_disabled_seeded"] = False
    return True


def health_path():
    return os.path.join(_config_dir(), HEALTH_FILE)


# ----------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------
# Per-station options. Anything not present in a station dict is filled
# in from here on load, so old station files keep working.
# Two sound options that were removed.
#
# Left ticked on an existing station they would keep being passed to the
# filter chain, which no longer builds them — so they are cleared on load
# rather than left as settings nothing reads.
# Cleared on load: settings that used to exist and no longer do.
#
# custom_song and custom_artist came from a by-hand readout override that
# has been taken out — left on a station they would sit in the file
# forever, meaning nothing.
_RETIRED_STATION_KEYS = ("vocal_clarity", "enhance_quality", "safe_enhance",
                         "custom_song", "custom_artist")

# Not a setting anyone chooses: set when a station's artwork arrived as a
# code's thumbnail, cleared once the real cover has been found.
_KEPT_STATION_KEYS = ("art_from_code",)


STATION_DEFAULTS = {
    "custom_name": None,
    "favicon": "",
    # The station's own website, used to find artwork when the
    # directory has no favicon for it - which is the common case.
    "homepage": "",
    "rb_uuid": "",
    # Goodvibes' <insecure> flag: skip TLS certificate verification for
    # this station. Carried through import/export so a round trip is
    # lossless, and honoured at playback time.
    "insecure": False,
    # Identification is on by default now, every 30 seconds.
    # Imported and not yet played — shown with a "New!" badge.
    "is_new": False,
    "shazam_auto": True,
    # Set once the user has chosen for themselves, so nothing chooses
    # again on their behalf.
    "shazam_auto_set": False,
    "shazam_interval": 30,
    # The 3-band EQ is per-station now: a bass-shy talk stream and a
    # boomy dance stream want opposite corrections, and one global
    # setting could only ever suit one of them.
    "eq_bass": 0,
    "eq_mid": 0,
    "eq_treble": 0,
    # Ten-band values live per station; which EQ is shown is global.
    "eq_bands": [0] * 10,

    # Advanced EQ.
    #
    # Ten bands at the ISO center frequencies a graphic equaliser has
    # used since the 1970s - 31 Hz to 16 kHz, one octave apart. When
    # `eq_advanced` is off the simple bass/mid/treble above is used
    # instead; the two are never combined, because a band boosted twice
    # is a band nobody can reason about.
    # Pre-buffering. Off by default; when enabled, the player is told to
    # read this many seconds ahead before starting. `connect_worst` is
    # learned automatically - the longest this station has ever taken to
    # produce audio - so the suggested value is grounded in what this
    # stream actually does rather than a guess.
    "buffer_enabled": False,
    "buffer_seconds": 0.0,
    "connect_worst": 0.0,

    # A "custom station": a folder of local music played the way a real
    # broadcast behaves - a random track, joined partway through, as
    # though it had been on air before you tuned in.
    "kind": "stream",          # "stream", "local" or "youtube"
    "local_path": "",
    # For a YouTube custom station: the playlist / channel URL.
    "playlist_url": "",

    # Band-limit and compress so the station sounds like it came out of
    # a receiver rather than a file.
    "radio_quality": False,

    # Try to restore what a low bitrate threw away, by synthesising
    # harmonics from what survived. Off by default: on a good stream it
    # is unnecessary, and on a bad one it is a matter of taste.

    # Ad override.
    #
    # Many stations inject an advert at the moment you connect. It is
    # part of the stream, so it cannot be skipped - but it can be
    # covered: play something else over the top for as long as the ad
    # lasts, while the real stream runs on silently underneath, then
    # hand over with a burst of static so it sounds like the dial
    # settling rather than a splice.
    # None = never set (build 83, task 93): a Twitch channel then covers
    # its opening ad for 30 s by default (ad_cover_on / ad_cover_seconds);
    # True / False is the user's own choice and always wins.
    "ad_override": None,
    "ad_seconds": 15.0,
    # The frequency chosen to cover the ad ("Choose frequency…").
    "ad_filler_station_id": "",

    # What covers the advert (build 64): "any" = any saved frequency
    # without ads (a normal radio station or a music folder), "chosen" =
    # the one in ad_filler_station_id. Older builds saved "music" /
    # "stream"; bridge._ad_cover_for still reads those.
    # A cover that has to connect first (a stream) is bridged by the
    # static; one that fails is swapped for the next (Player.FillerPlayer).
    "ad_filler_kind": "any",
    "ad_filler_url": "",
    # Persistent per-station level trim, in dB. Negative ducks a station
    # that is always too loud, positive boosts a quiet one. This is
    # separate from the global volume and survives restarts.
    "gain_db": 0.0,
    # Which source wins for the artwork and for the song readout, and
    # whether to fall back to the others when the preferred one has
    # nothing. "auto" means: custom, then shazam, then stream.
    "art_source": "auto",
    "custom_art_path": "",
    "art_fallback": True,
    "readout_source": "auto",
    "custom_readout": "",
    "readout_fallback": True,
    # Plays its own contents in a random order, for the stations that
    # have contents: a folder, a playlist, a library.
    "shuffle_contents": True,
    "lyrics_enabled": True,
    # Sorting / statistics
    "added_at": 0.0,
    "last_played": 0.0,
    "play_count": 0,
    "sort_index": 0,
    # Free-form labels. Colors live in settings["tag_colors"] so the
    # same tag looks the same on every station.
    "tags": [],
}

# Numeric per-station fields and their sane ranges.
_STATION_RANGES = {
    "gain_db": (-20.0, 20.0),
    "eq_bass": (-12, 12),
    "eq_mid": (-12, 12),
    "eq_treble": (-12, 12),
    "buffer_seconds": (0.0, 60.0),
    "connect_worst": (0.0, 300.0),
    "shazam_interval": (30, 900),
    "play_count": (0, 10 ** 9),
    "sort_index": (0, 10 ** 9),
}

ART_SOURCES = ("auto", "custom", "shazam", "stream")
# "auto" and "auto_stream" differ only in which they prefer when Shazam
# has nothing: your own label, or whatever the stream is reporting.
READOUT_SOURCES = ("auto", "auto_stream", "custom", "shazam", "stream")


SORT_MODES = ("manual", "alphabetical", "added", "played", "play_count")

SETTINGS_DEFAULTS = {
    # 13, not 20. Lyrics sit in a narrow column beside a biography; at
    # 20 a single line wrapped three times and four lines filled the
    # panel.
    "lyric_size": 13,
    "volume": 80,
    "normalize": True,
    "eq_bass": 0,
    "eq_mid": 0,
    "eq_treble": 0,

    # Advanced EQ.
    #
    # Ten bands at the ISO center frequencies a graphic equaliser has
    # used since the 1970s - 31 Hz to 16 kHz, one octave apart. When
    # `eq_advanced` is off the simple bass/mid/treble above is used
    # instead; the two are never combined, because a band boosted twice
    # is a band nobody can reason about.
    # Pre-buffering. Off by default; when enabled, the player is told to
    # read this many seconds ahead before starting. `connect_worst` is
    # learned automatically - the longest this station has ever taken to
    # produce audio - so the suggested value is grounded in what this
    # stream actually does rather than a guess.
    "buffer_enabled": False,
    "buffer_seconds": 0.0,
    "connect_worst": 0.0,

    # A "custom station": a folder of local music played the way a real
    # broadcast behaves - a random track, joined partway through, as
    # though it had been on air before you tuned in.
    "kind": "stream",          # "stream", "local" or "youtube"
    "local_path": "",
    # For a YouTube custom station: the playlist / channel URL.
    "playlist_url": "",

    # Band-limit and compress so the station sounds like it came out of
    # a receiver rather than a file.
    "radio_quality": False,

    # Try to restore what a low bitrate threw away, by synthesising
    # harmonics from what survived. Off by default: on a good stream it
    # is unnecessary, and on a bad one it is a matter of taste.

    # Ad override.
    #
    # Many stations inject an advert at the moment you connect. It is
    # part of the stream, so it cannot be skipped - but it can be
    # covered: play something else over the top for as long as the ad
    # lasts, while the real stream runs on silently underneath, then
    # hand over with a burst of static so it sounds like the dial
    # settling rather than a splice.
    "ad_override": False,
    "ad_seconds": 15.0,
    # The frequency chosen to cover the ad ("Choose frequency…").
    "ad_filler_station_id": "",

    # What covers the advert (build 64): "any" = any saved frequency
    # without ads (a normal radio station or a music folder), "chosen" =
    # the one in ad_filler_station_id. Older builds saved "music" /
    # "stream"; bridge._ad_cover_for still reads those.
    # A cover that has to connect first (a stream) is bridged by the
    # static; one that fails is swapped for the next (Player.FillerPlayer).
    "ad_filler_kind": "any",
    "ad_filler_url": "",
    "shuffle": False,
    "noise_between_stations": True,
    # Stations play at the main volume (80 by default) while the static
    # sat at 35, so it was ~7 dB down on top of the recordings being
    # quiet in the first place. 70 puts it in the same ballpark as the
    # programme material without burying it.
    "noise_volume": 70,
    "last_station_id": None,
    "auto_skip_broken": True,
    "auto_skip_after_tries": 3,
    "sort_mode": "manual",
    "tag_colors": {},
    # One-time migration marker: when identification became a default,
    # existing stations were switched on to match. Without this the new
    # default would only ever apply to newly added stations.
    "identify_default_applied": False,
    # One-time nudge for anyone still on the old, too-quiet default.
    "noise_volume_raised": False,

    # Re-check stations that failed, in the background, so a station
    # that was down an hour ago isn't skipped forever - and isn't
    # dropped into the middle of a listening session unverified either.
    # On by default: a station that comes back should un-flag itself
    # without anyone going looking for the setting.
    "health_recheck": True,
    # The GUI stall log, on by default — see stallwatch.py.
    "stall_watch": True,
    # Whole features, off entirely rather than per station.
    "lyrics_on": True,
    "artist_on": True,
    "health_recheck_minutes": 30,

    # How long the static takes to blend into and out of the stream.
    "crossfade_ms": 700,

    # A stream that goes silent and stays silent is dead, however
    # healthy the connection looks. Seconds of no progress before the
    # app gives up and moves on.
    "stall_timeout": 6,
    # One-time migration marker: the stall timeout was lowered from 8s
    # to 6s. A saved settings file would otherwise keep the old value
    # forever, since the default only applies to keys it doesn't have.
    "stall_timeout_lowered": False,

    # How often shuffle should land on one of your own local stations
    # rather than a stream, as a percentage. 0 leaves selection purely
    # proportional to how many of each you have.
    "local_station_ratio": 0,

    # Sleep timer. `sleep_minutes` is what the menu last used, so
    # starting it again doesn't mean re-picking. `sleep_fade` fades the
    # audio out over the last stretch rather than cutting, which is the
    # whole point of falling asleep to it.
    "sleep_minutes": 30,
    "sleep_fade_seconds": 20,

    # Artist profile panel.
    "artist_panel": True,
    # for. Empty disables the tour section entirely.
    # (latitude, longitude) used to sort tour dates nearest first.
    "home_label": "",
    # Animated album art from Apple Music, where it exists.

    # System tray.
    #
    # `tray_icon` puts an icon in the notification area; `minimize_to_tray`
    # makes closing the window hide it there instead of quitting, which
    # only makes sense while the icon exists to get it back from.
    "tray_icon": False,
    "minimize_to_tray": False,

    # Which EQ the controls show. Global, because it is a choice about
    # the interface rather than about a station - ten sliders for one
    # station and three for the next would be nonsense. The VALUES stay
    # per-station, so each keeps both its simple and its ten-band
    # settings and switching back finds them intact.
    "eq_advanced": False,

    # Output device, as mpv names it. "auto" means the system default,
    # which is what should happen unless the user says otherwise.
    "audio_device": "auto",

    # Classic Winamp skin. Empty means the normal interface.
    "skin_path": "",
    # Where the user keeps their skins, so the locker opens there.
    "skin_folder": "",
    # Change skin whenever a station starts playing.
    "skin_shuffle": False,
    # Which skinned windows come up with the player, and how the clock
    # in it is written.
    "skin_show_eq": True,
    "skin_show_playlist": True,
    "skin_show_tags": True,
    # How tall the skinned playlist was left. Clamped on load, so a
    # value saved on a big screen cannot open a window taller than a
    # small one.
    "skin_playlist_height": 232,
    # Per skin: whether the simple equalizer paints over the background
    # behind its three bars. Some skins put a picture there and the
    # patch ruins it; others have gridline dashes that only look like
    # damage. Keyed by skin name, since only the person looking at it
    # can say which is which.
    "skin_eq_fill": {},
    "clock_24_hour": False,
    # The projectM visualizer. Off by default: it needs an OpenGL
    # context inside the skinned window, which is exactly the kind of
    # thing that has broken this skin before.
    "visualizer": False,
    # Tint the interface from the current artwork.
    "accent_from_art": True,
    # Which device the visualizer listens to. Empty means the system
    # default, which on a machine with several outputs may be a silent
    # one.
    "visualizer_source": "",
    # bars | wave | none, for the panel beside the clock.
    "skin_vis_mode": "bars",
    # How hard the analyser drives what it hears. The capture is a
    # monitor of the output and so is post-volume: at a normal listening
    # level the samples sit well below full scale.
    # Wide open by default.
    #
    # A visualiser that barely moves looks broken rather than subtle, and
    # the first thing anyone does is reach for the sensitivity. 16 is the
    # ceiling the canvas allows.
    "visualizer_gain": 16.0,
    # Follow the station when Next or Previous moves it.
    "jump_on_change": False,
    # Presets switched off in the browser, by full path.
    "visualizer_disabled": [],
    # True once the developer's shipped disabled list was offered to this
    # install (task 119), so what you turn back on later stays on.
    "visualizer_disabled_seeded": False,
    # Build 106 (task 142): the developer's published list was fetched
    # once (user builds); its file names; the user changed the list.
    "visualizer_list_fetched": False,
    "visualizer_disabled_names": None,
    "visualizer_disabled_mine": False,

    # Station list as a grid of artwork rather than rows of names.
    "station_thumbnails": False,
    "lyrics_panel_hidden": False,
    "artist_panel_hidden": False,

    # Where "near me" means, for sorting radio-browser results by
    # distance. Nothing else uses it.
    "home_lat": 0.0,
    "home_lon": 0.0,

    # Halves of the side panel the user has folded away. Both show by
    # default; remembered so a fold survives a restart.
    "skin_scale": 2,
    # One-time migration marker: the EQ used to be a single global
    # setting. When it became per-station, whatever was set globally
    # was copied onto every station so nobody's sound changed.
    "eq_moved_to_stations": False,
    # Ordinary songs kept in history. Favourites are never counted
    # against this — see trim_history.
    "shazam_history_limit": 300,
    # Show times as 1:30 PM rather than 13:30, everywhere.
    "clock_12h": False,
    "thumbnail_cache_mb": 1024,
}

# How many identified songs we keep. The list is capped so the file
# stays small and the history window stays fast to open no matter how
# long the app has been running.
HISTORY_HARD_CAP = 5000


def _default_stations():
    return [
        dict(STATION_DEFAULTS, id=str(uuid.uuid4()),
             name="SomaFM - Groove Salad",
             url="https://ice1.somafm.com/groovesalad-128-mp3"),
        dict(STATION_DEFAULTS, id=str(uuid.uuid4()),
             name="SomaFM - Drone Zone",
             url="https://ice1.somafm.com/dronezone-128-mp3"),
        dict(STATION_DEFAULTS, id=str(uuid.uuid4()),
             name="BBC Radio 1",
             url="http://stream.live.vc.bbcmedia.co.uk/bbc_radio_one"),
    ]


def _clamp(value, low, high, fallback):
    try:
        return max(low, min(high, type(low)(value)))
    except (TypeError, ValueError):
        return fallback


def _normalize_station(s):
    """Fill in any missing keys so the rest of the app can assume they
    exist, whatever version of the file wrote them."""
    for key, default in STATION_DEFAULTS.items():
        if key not in s or s[key] is None and default is not None:
            s[key] = default() if callable(default) else default
    # Cleared on load, so a station saved with either of them ticked
    # stops asking for a filter the player no longer builds.
    for retired in _RETIRED_STATION_KEYS:
        s.pop(retired, None)
    if not s.get("id"):
        s["id"] = str(uuid.uuid4())
    if not s.get("added_at"):
        s["added_at"] = time.time()

    for key, (low, high) in _STATION_RANGES.items():
        s[key] = _clamp(s.get(key), low, high, STATION_DEFAULTS[key])

    for key in ("is_new", "shazam_auto", "shazam_auto_set", "art_fallback",
                "readout_fallback", "lyrics_enabled", "insecure"):
        s[key] = bool(s.get(key))

    if s.get("art_source") not in ART_SOURCES:
        s["art_source"] = "auto"
    if s.get("readout_source") not in READOUT_SOURCES:
        s["readout_source"] = "auto"
    if not isinstance(s.get("tags"), list):
        s["tags"] = []
    else:
        # Normalized once here so comparisons elsewhere can be plain
        # equality: trimmed, de-duplicated, order preserved.
        seen = set()
        clean = []
        for tag in s["tags"]:
            name = str(tag).strip()
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                clean.append(name)
        s["tags"] = clean

    # Settings from removed features (the old local ad-break handling
    # and, later, transcription) are dropped rather than carried
    # forward, so a station file written by an older build doesn't keep
    # dead keys forever.
    for gone in ("ad_handling", "ad_patterns", "preroll_mute_secs",
                "transcribe"):
        s.pop(gone, None)
    return s


def _atomic_write_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ----------------------------------------------------------------------
# Stations
# ----------------------------------------------------------------------
def load_stations():
    path = stations_path()
    if not os.path.exists(path):
        stations = _default_stations()
        save_stations(stations)
        return stations
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [_normalize_station(s) for s in data if isinstance(s, dict)]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def save_stations(stations):
    _atomic_write_json(stations_path(), stations)


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
def load_settings():
    settings = dict(SETTINGS_DEFAULTS)
    path = settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                settings.update(data)
        except (json.JSONDecodeError, OSError):
            pass
    # Volume used to allow up to 130; the app is capped at 100 now, so
    # clamp anything left over from an older settings file.
    try:
        settings["volume"] = max(0, min(100, int(settings.get("volume", 80))))
    except (TypeError, ValueError):
        settings["volume"] = 80
    return settings


def save_settings(settings):
    _atomic_write_json(settings_path(), settings)


# ----------------------------------------------------------------------
# Health / reachability
# ----------------------------------------------------------------------
# health[station_id] = {
#   "last_ok":     epoch seconds of the last successful connection, or None
#   "last_check":  epoch seconds of the last attempt
#   "fail_count":  consecutive failures
#   "down":        sticky flag - stays True until we reach it again
#   "last_error":  short human-readable reason
# }
def _blank_health():
    return {
        "last_ok": None,
        "last_check": None,
        "fail_count": 0,
        "down": False,
        "last_error": "",
    }


def load_health():
    path = health_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {k: dict(_blank_health(), **v)
                        for k, v in data.items() if isinstance(v, dict)}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_health(health):
    _atomic_write_json(health_path(), health)


def health_for(health, station_id):
    if station_id not in health:
        health[station_id] = _blank_health()
    return health[station_id]


def mark_ok(health, station_id):
    entry = health_for(health, station_id)
    now = time.time()
    entry["last_ok"] = now
    entry["last_check"] = now
    entry["fail_count"] = 0
    entry["down"] = False
    entry["last_error"] = ""
    return entry


def mark_failed(health, station_id, reason="", down_after=1):
    entry = health_for(health, station_id)
    entry["last_check"] = time.time()
    entry["fail_count"] = int(entry.get("fail_count", 0)) + 1
    entry["last_error"] = reason or "unreachable"
    if entry["fail_count"] >= down_after:
        entry["down"] = True
    return entry


def humanize_age(seconds):
    """'3m', '5h', '2d' - compact enough to sit in a list row."""
    if seconds is None:
        return "never"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def describe_health(entry):
    """Short status string for a station row, or '' when it's fine."""
    if not entry or not entry.get("down"):
        return ""
    last_ok = entry.get("last_ok")
    if last_ok is None:
        return "never reached"
    return f"down {humanize_age(time.time() - last_ok)}"


# ----------------------------------------------------------------------
# Backup / restore
# ----------------------------------------------------------------------
def export_backup(path, stations, settings, health, history=None,
                  artist_overrides=None, station_overrides=None):
    """Write everything into one portable file.

    Everything means everything: stations with their tags and per-station
    equalizer curves, settings (which is where tag colors and display
    preferences live), health, listening history, artist overrides, and
    the custom artwork itself rather than a path to it.

    Artwork is embedded because a path is only meaningful on the machine
    that wrote it - restoring on another computer would have produced
    stations pointing at pictures that were not there.
    """
    payload = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": time.time(),
        "stations": stations,
        "settings": settings,
        "health": health,
        "history": history or [],
        "artist_overrides": artist_overrides or {},
        # Corrections for stations that play no music, where the
        # article is about the station rather than an artist.
        "station_overrides": station_overrides or {},
        "artwork": _collect_artwork(stations),
    }
    _atomic_write_json(path, payload)


def _collect_artwork(stations):
    """{station id: {"name": ..., "data": base64}} for custom art."""
    art = {}
    for station in stations or []:
        source = (station.get("custom_art_path") or "").strip()
        if not source or not os.path.isfile(source):
            continue
        try:
            with open(source, "rb") as f:
                blob = base64.b64encode(f.read()).decode("ascii")
        except OSError:
            continue
        art[str(station.get("id", ""))] = {
            "name": os.path.basename(source),
            "data": blob,
        }
    return art


def restore_artwork(artwork, stations):
    """Write embedded art back to disk and repoint the stations at it.

    Returns the stations, edited in place. A station whose artwork is
    missing from the backup keeps whatever path it had, which may well
    still be valid on the machine doing the restore.
    """
    if not artwork:
        return stations
    folder = os.path.join(_config_dir(), "artwork")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return stations

    by_id = {str(s.get("id", "")): s for s in stations or []}
    for station_id, entry in (artwork or {}).items():
        station = by_id.get(str(station_id))
        if station is None or not isinstance(entry, dict):
            continue
        try:
            blob = base64.b64decode(entry.get("data", ""))
        except Exception:  # noqa: BLE001
            continue
        name = os.path.basename(entry.get("name") or f"{station_id}.png")
        target = os.path.join(folder, f"{station_id}-{name}")
        try:
            with open(target, "wb") as f:
                f.write(blob)
        except OSError:
            continue
        station["custom_art_path"] = target
    return stations


class BackupError(Exception):
    pass


def read_backup(path):
    """Load and validate a backup file.

    Returns (stations, settings, health, history, artist_overrides).
    Older backups simply have no history or overrides in them and come
    back with empty ones, so a file written before this change still
    restores.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        raise BackupError(f"Couldn't open that file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BackupError(f"That isn't a valid backup file: {exc}") from exc

    if not isinstance(data, dict) or not is_backup_format(data.get("format")):
        raise BackupError("That file isn't a Frequencies backup.")

    raw_stations = data.get("stations")
    if not isinstance(raw_stations, list):
        raise BackupError("The backup has no station list in it.")

    stations = [_normalize_station(dict(s))
                for s in raw_stations if isinstance(s, dict) and s.get("url")]

    # Artwork rides along inside the backup; write it out and point the
    # stations at the copies before anything else looks at them.
    stations = restore_artwork(data.get("artwork"), stations)

    settings = dict(SETTINGS_DEFAULTS)
    if isinstance(data.get("settings"), dict):
        settings.update(data["settings"])
    try:
        settings["volume"] = max(0, min(100, int(settings.get("volume", 80))))
    except (TypeError, ValueError):
        settings["volume"] = 80

    health = {}
    if isinstance(data.get("health"), dict):
        health = {k: dict(_blank_health(), **v)
                  for k, v in data["health"].items() if isinstance(v, dict)}

    history = data.get("history")
    if not isinstance(history, list):
        history = []
    overrides = data.get("artist_overrides")
    if not isinstance(overrides, dict):
        overrides = {}
    stations_overrides = data.get("station_overrides")
    if not isinstance(stations_overrides, dict):
        stations_overrides = {}

    return (stations, settings, health, history, overrides,
            stations_overrides)


def merge_stations(existing, incoming):
    """Add incoming stations that aren't already present (matched on
    URL), keeping the existing entries untouched. Returns (merged,
    added_count)."""
    by_url = {s.get("url"): s for s in existing}
    merged = list(existing)
    added = 0
    for s in incoming:
        if s.get("url") not in by_url:
            station = dict(s)
            station["id"] = str(uuid.uuid4())
            merged.append(station)
            added += 1
    return merged, added


# ----------------------------------------------------------------------
# Identified-song history
# ----------------------------------------------------------------------
# Every Shazam hit is appended here. The list is capped (newest first)
# so the file can't grow without bound - an always-on station
# identifying every 90 seconds would otherwise add ~1000 rows a day.
HISTORY_FILE = "history.json"


def artist_overrides_path():
    return os.path.join(_config_dir(), "artist_overrides.json")


def bundled_artist_overrides_path():
    """The corrections that ship with the app.

    Shipped rather than user-editable on purpose: these are fixes the
    maintainer makes once - pinning "Dracula" to the band rather than
    the novel - and pushes to everyone in the next build. Users get the
    benefit without having to know the problem existed.
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "artist_overrides.json")


def load_bundled_artist_overrides():
    try:
        with open(bundled_artist_overrides_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).strip().lower(): v for k, v in data.items()
            if isinstance(v, dict)}


def station_overrides_path():
    return os.path.join(_config_dir(), "station_overrides.json")


def bundled_station_overrides_path():
    """Station corrections that ship with the app, like artists'."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "station_overrides.json")


def _read_overrides(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).strip().lower(): v for k, v in data.items()
            if isinstance(v, dict)}


def load_station_overrides():
    return _read_overrides(station_overrides_path())


def load_bundled_station_overrides():
    return _read_overrides(bundled_station_overrides_path())


def save_station_overrides(overrides):
    _atomic_write_json(station_overrides_path(), overrides or {})


def station_override(station_name):
    """The correction for this station's article, if there is one.

    Shipped talk-station entries (task 49) are not corrections: they are
    looked up by `station_info`, through the stream address, and only
    while the developer has them switched on.
    """
    key = (station_name or "").strip().lower()
    if not key:
        return {}
    bundled = load_bundled_station_overrides().get(key) or {}
    merged = {} if "talk" in bundled else dict(bundled)
    merged.update(load_station_overrides().get(key) or {})
    return merged


# -- talk stations (task 49) ------------------------------------------------
#
# A talk station keeps its own info (Wikipedia article, or a bio and photo
# typed in) in the artist panel the whole time it plays. The developer
# switches it on in a dev build (Station settings > Identify); it reaches
# users in the next build:
#
#   dev build  -> <config>/dev_talk_stations.json  (+ <config>/talk_photos/)
#   build      -> tools/ship_talk_stations.py copies the talk entries into
#                 src/station_overrides.json (+ src/talk_photos/)
#   user build -> reads them from the shipped station_overrides.json
#
# Entries are keyed by lowercase station name and carry the stream address
# ("stream"), which is matched first: users rename stations.

DEV_TALK_FILE = "dev_talk_stations.json"
TALK_PHOTOS = "talk_photos"


def dev_talk_path():
    return os.path.join(_config_dir(), DEV_TALK_FILE)


def _norm_stream(url):
    """A stream address in one spelling: scheme dropped, host lowercase,
    no trailing slash or semicolon (old Shoutcast links end in ';')."""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" in url:
        url = url.split("://", 1)[1]
    host, sep, rest = url.partition("/")
    url = host.lower() + sep + rest
    return url.rstrip("/;").rstrip("/")


def talk_entries():
    """Every talk-station entry this build uses, switched on or not.

    A dev build uses its own list once it has one (the developer's
    switches); until then, and in every user build, the shipped list.
    """
    import devmode
    if devmode.is_dev() and os.path.exists(dev_talk_path()):
        return _read_overrides(dev_talk_path())
    return {k: v for k, v in load_bundled_station_overrides().items()
            if "talk" in v}


def talk_entry(station, entries=None):
    """(key, entry, how) for this station, or ("", None, "").

    Matched by stream address first, then by name (the station's own
    name, then the one you gave it).
    """
    station = station or {}
    entries = talk_entries() if entries is None else entries
    url = _norm_stream(station.get("url"))
    if url:
        for key, entry in entries.items():
            if _norm_stream(entry.get("stream")) == url:
                return key, entry, "stream address"
    for name in (station.get("custom_name"), station.get("name")):
        key = (name or "").strip().lower()
        if key and key in entries:
            return key, entries[key], "name"
    return "", None, ""


def is_talk_station(station):
    _key, entry, _how = talk_entry(station)
    return bool(entry and entry.get("talk"))


def talk_photo_path(photo):
    """A talk station's photo as a file on this machine, or "".

    Saved as "talk_photos/<file>": the developer's copy lives in the
    config folder, the shipped copy next to the code.
    """
    photo = (photo or "").strip()
    if not photo or photo.startswith(("http://", "https://")):
        return ""
    if os.path.isabs(photo):
        return photo if os.path.exists(photo) else ""
    base = os.path.basename(photo)
    import devmode
    places = [os.path.join(_config_dir(), TALK_PHOTOS, base)] \
        if devmode.is_dev() else []
    places.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               TALK_PHOTOS, base))
    for path in places:
        if os.path.exists(path):
            return path
    # Builds from 91 on ship no photos: they come with the photo pack
    # (task 117), for those who opted in.
    try:
        import photopack
        return photopack.talk_photo(base)
    except Exception:                           # noqa: BLE001
        return ""


def station_info(station):
    """What the artist panel shows for this station, or {}.

    {"talk": True, ...} for a talk station (shipped or the developer's
    own), with the user's own station correction on top - theirs wins.
    Otherwise a station correction as before (Settings > Artists): it
    also holds the panel while the station plays.

    A Twitch station gives {"twitch_channel": <channel>, ...} (build 76,
    task 77): the streamer's own name, bio and picture are looked up
    from Twitch, with a station correction on top.
    """
    station = station or {}
    name = station_display_name(station)
    own = load_station_overrides().get((name or "").strip().lower()) or {}
    key, entry, how = talk_entry(station)
    if entry and entry.get("talk"):
        merged = dict(entry)
        merged.update(own)
        merged["talk"] = True
        merged["_matched"] = how
        merged["_key"] = key
        return merged
    correction = station_override(name)
    channel = (station.get("twitch_channel") or "").strip()
    if channel and station.get("kind") == "twitch":
        merged = {"twitch_channel": channel}
        merged.update(correction)
        return merged
    return correction


def save_talk_entry(station, talk=None, fields=None):
    """Change a station's talk entry in the developer's own list.

    `talk` True/False switches it; `fields` (name, genres, wikipedia, bio,
    photo, photo_source, photo_url) replaces its info - blank ones are
    dropped. Switching off keeps the info, so switching on again brings
    it back. Dev builds only; returns the entry saved.
    """
    station = station or {}
    entries = dict(talk_entries())        # starts from the shipped list
    key, entry, _how = talk_entry(station, entries)
    entry = dict(entry or {})
    if key:
        entries.pop(key, None)
    key = station_display_name(station).strip().lower() or key
    if talk is not None:
        entry["talk"] = bool(talk)
    entry.setdefault("talk", False)
    entry["stream"] = station.get("url") or entry.get("stream", "")
    if fields is not None:
        for field in ("name", "genres", "wikipedia", "bio", "photo",
                      "photo_source", "photo_url"):
            entry.pop(field, None)
            value = (fields.get(field) or "").strip()
            if value:
                entry[field] = value
    entries[key] = entry
    _atomic_write_json(dev_talk_path(), dict(sorted(entries.items())))
    return entry


def keep_talk_photo(station, path):
    """Copy a chosen photo into the developer's talk_photos folder.

    Returns "talk_photos/<file>" - the form that ships - or the path as
    given when it can't be copied (a link, a missing file).
    """
    path = (path or "").strip()
    if not path or path.startswith(("http://", "https://")) \
            or not os.path.isabs(path) or not os.path.exists(path):
        return path
    folder = os.path.join(_config_dir(), TALK_PHOTOS)
    if os.path.dirname(os.path.abspath(path)) == folder:
        return TALK_PHOTOS + "/" + os.path.basename(path)
    import re
    slug = re.sub(r"[^a-z0-9]+", "-",
                  station_display_name(station).lower()).strip("-") or "station"
    ext = os.path.splitext(path)[1].lower() or ".jpg"
    try:
        os.makedirs(folder, exist_ok=True)
        shutil.copyfile(path, os.path.join(folder, slug + ext))
    except OSError:
        return path
    return TALK_PHOTOS + "/" + slug + ext


def artist_override(artist):
    """The correction for this artist, if there is one.

    The user's own file wins over the shipped one, so someone who has
    fixed something locally does not have it undone by an update that
    disagrees with them.
    """
    key = (artist or "").strip().lower()
    if not key:
        return {}
    merged = dict(load_bundled_artist_overrides().get(key) or {})
    # The developer's photo pack (build 91, task 117), for those who
    # opted in: newer than the build's corrections, and the only place
    # their photos come from. The user's own still win.
    # Since build 95 the developer's bio fixes come apart from the photos,
    # to every user (photopack.BioUpdate); the pack's photo on top.
    try:
        import photopack
        merged.update(photopack.bio_entry(key))
        merged.update(photopack.artist_entry(key))
    except Exception:                           # noqa: BLE001
        pass
    merged.update(load_artist_overrides().get(key) or {})
    return merged


def load_artist_overrides():
    """artist name (lowercased) -> what to look up instead.

    Each entry is {"mbid": "...", "wikipedia": "...", "bio": "..."} with
    every field optional: an MBID pins the lookup to one specific
    artist, a wikipedia title forces one article, and a bio replaces the
    text outright for cases where no article is right.
    """
    try:
        with open(artist_overrides_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).strip().lower(): v for k, v in data.items()
            if isinstance(v, dict)}


def save_artist_overrides(overrides):
    _atomic_write_json(artist_overrides_path(), overrides or {})


def history_path():
    return os.path.join(_config_dir(), HISTORY_FILE)


def load_history():
    path = history_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [e for e in data if isinstance(e, dict)]
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_history(history):
    _atomic_write_json(history_path(), history)


def history_limit(settings):
    """How many ordinary songs to keep, at most 300.

    Every history row is a widget, and the page rebuilds on each
    identification — so a long history is paid for every few minutes,
    not once. Three hundred is several days of listening, which is as far
    back as anyone scrolls. Favourites sit outside the count entirely.
    """
    try:
        limit = int(settings.get("shazam_history_limit", 300))
    except (TypeError, ValueError):
        limit = 300
    return max(10, min(HISTORY_HARD_CAP, 300, limit))


def track_key(artist, title):
    """Stable identity for a song, used for favorites and the
    thumbnail cache. Case- and whitespace-insensitive so the same song
    from two stations doesn't become two entries."""
    return f"{(artist or '').strip().lower()}|{(title or '').strip().lower()}"


def entry_key(entry):
    """The song's identity: artist and title.

    Read from "title" or "song", because history is carried in both
    shapes — saved entries say "title", the rows the history page is
    given say "song". Reading only "title" made every star on that page
    produce a key like "artist|" that matched nothing, so clicking a star
    changed nothing and the redraw put it straight back.
    """
    return track_key(entry.get("artist"),
                     entry.get("title") or entry.get("song"))


def is_favorite(entry):
    return bool(entry.get("favorite"))


def add_history_entry(history, track, station_name, limit=300):
    """Prepend a hit, skipping consecutive duplicates, and trim to the
    cap. Returns (history, added?) so the caller can avoid a pointless
    disk write when nothing changed."""
    title = (track.get("title") or "").strip()
    artist = (track.get("artist") or "").strip()
    if not title and not artist:
        return history, False

    # The same song identified twice in a row on the same station isn't
    # a new event - it's the same play.
    if history:
        last = history[0]
        if (last.get("title") == title and last.get("artist") == artist
                and last.get("station") == station_name):
            return history, False

    # If this song is already a favorite somewhere in the list, keep
    # that entry (and its star) and just refresh when it was heard,
    # rather than adding a second row for the same track.
    key = track_key(artist, title)
    for existing in history:
        if is_favorite(existing) and entry_key(existing) == key:
            existing["at"] = time.time()
            existing["station"] = station_name
            history.remove(existing)
            history.insert(0, existing)
            return history, True

    history.insert(0, {
        "title": title,
        "artist": artist,
        "station": station_name,
        "at": time.time(),
        "apple_music_url": track.get("apple_music_url", ""),
        "cover_url": track.get("cover_url", ""),
        "favorite": False,
    })
    return trim_history(history, limit), True


def format_history_time(when):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(when)))
    except (TypeError, ValueError):
        return "?"


# ----------------------------------------------------------------------
# Sorting
# ----------------------------------------------------------------------
SORT_LABELS = {
    "manual": "Manual (drag to reorder)",
    "alphabetical": "Alphabetical",
    "added": "Recently added",
    "played": "Recently played",
    "play_count": "Most played",
}


# Everything a video title carries that a readout should not.
_TITLE_NOISE = (
    r"\(\s*(?:official|hd|4k|hq)?\s*(?:music\s*)?video[^)]*\)",
    r"\[\s*(?:official|hd|4k|hq)?\s*(?:music\s*)?video[^\]]*\]",
    r"\(\s*(?:official\s*)?(?:audio|lyrics?|lyric\s*video|visualizer|"
    r"visualiser|full\s*album|remaster(?:ed)?[^)]*)\s*\)",
    r"\[\s*(?:official\s*)?(?:audio|lyrics?|lyric\s*video|visualizer|"
    r"visualiser|full\s*album|remaster(?:ed)?[^\]]*)\s*\]",
    r"\(\s*(?:19|20)\d\d\s*(?:remaster(?:ed)?)?\s*\)",
    r"\bofficial\s+(?:music\s+)?video\b",
    r"\bhd\b|\b4k\b|\bhq\b",
    r"\|\s*[^|]*\b(?:records|recordings|music|tv|channel)\b[^|]*$",
)


def clean_video_title(title):
    """A video title, reduced to (artist, song).

    Uploaders put everything in there — "ARTIST - Song (Official Music
    Video) [HD] | Label TV" — and showing that raw is why the readout
    looked like a filename. What is wanted is the two things the rest of
    the app already understands.

    Returns ("", title) when there is no separator to split on, because a
    title with no dash is more likely to be the song than the artist.
    """
    import re

    text = (title or "").strip()
    if not text:
        return "", ""
    for pattern in _TITLE_NOISE:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    # Whatever the removals left behind: empty brackets, doubled spaces,
    # a trailing separator with nothing after it.
    text = re.sub(r"[\(\[]\s*[\)\]]", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -–—|·•").strip()

    # The first dash that has spaces around it: "AC/DC - Back in Black"
    # must not split on the slash, and "Post-Punk Band - Song" must not
    # split on the hyphen inside the name.
    parts = re.split(r"\s+[-–—]\s+", text, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0].strip(), parts[1].strip()
    return "", text


def station_display_name(station):
    return (station.get("custom_name") or station.get("name")
            or station.get("url") or "")


def sort_stations(stations, mode, descending=None):
    """Return the station list in the requested order.

    "manual" keeps whatever order the user dragged them into, which is
    stored per-station as sort_index so it survives a restart. Every
    other mode is derived, and leaves the underlying order untouched -
    switching back to manual restores the arrangement.
    """
    # `descending` of None means each mode's own default, which is what
    # they were already doing: A-Z for names, newest and most-played first
    # for the rest.
    if mode == "alphabetical":
        return sorted(stations, key=lambda s: station_display_name(s).lower(),
                      reverse=bool(descending))
    reverse = True if descending is None else bool(descending)
    if mode == "added":
        return sorted(stations, key=lambda s: s.get("added_at", 0),
                      reverse=reverse)
    if mode == "played":
        return sorted(stations, key=lambda s: s.get("last_played", 0),
                      reverse=reverse)
    if mode == "play_count":
        return sorted(
            stations,
            key=lambda s: (s.get("play_count", 0), s.get("last_played", 0)),
            reverse=reverse,
        )
    # manual: the drag order is the order, and has no direction to flip
    return sorted(stations, key=lambda s: s.get("sort_index", 0))


def renumber_manual_order(stations):
    """Rewrite sort_index to match the current list order."""
    for i, station in enumerate(stations):
        station["sort_index"] = i
    return stations


# -- query tokenizing -------------------------------------------------
#
# The filter query is whitespace-separated, which quietly broke every tag
# containing a space. `tag:hi energy` split into `tag:hi` and `energy`,
# so the filter matched the wrong stations AND the "is this tag already
# in the query?" test never found it - clicking the chip a second time
# appended a duplicate instead of clearing it.
#
# Quoting fixes both: `tag:"hi energy"` survives as one token.


def _tokens(query):
    """Split on whitespace, except inside double quotes."""
    out, current, in_quotes = [], "", False
    for ch in query or "":
        if ch == '"':
            in_quotes = not in_quotes
            current += ch
        elif ch.isspace() and not in_quotes:
            if current:
                out.append(current)
                current = ""
        else:
            current += ch
    if current:
        out.append(current)
    return out


def _unquote(text):
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1], True
    return text, False


def _split_token(token):
    """(negated, is_tag, value, quoted) for one token.

    Accepts both `tag:"two words"` and `"tag:two words"`, because a
    person typing by hand could reasonably produce either.
    """
    negated = False
    text = token
    if len(text) > 1 and text.startswith("-"):
        negated, text = True, text[1:]

    text, outer_quoted = _unquote(text)
    if text.lower().startswith("tag:"):
        value, inner_quoted = _unquote(text[4:])
        return negated, True, value, (outer_quoted or inner_quoted)
    return negated, False, text, outer_quoted


def _quote_tag(tag):
    """Tags are always quoted when written by a chip, which makes the
    match exact. A hand-typed `tag:rock` stays a substring search, so
    partial typing still works."""
    return '"%s"' % tag.replace('"', "")


def matches_search(station, query):
    """Case-insensitive match over the name, the original name, the URL
    and the station's tags.

    A "tag:" prefix restricts the match to tags, so "tag:jazz" finds
    stations labeled jazz without also matching every station with
    "jazz" in its name.

    Included tags are ORed with each other: picking "hi energy" shows
    hi-energy stations, and adding "talk" widens the list to both rather
    than narrowing it to stations that are somehow simultaneously
    high-energy and talk radio. Tags in this app describe what a station
    IS, and almost nothing is two things at once, so ANDing them mostly
    produced an empty list. Everything else still narrows: free text and
    negations are ANDed on top, so `tag:rock tag:jazz -tag:live` means
    "rock or jazz, but nothing live".

    A leading "-" negates a term: "-tag:talk" means "everything except
    talk", and the two combine, so "tag:rock -tag:live" is rock without
    the live recordings.

    Quoting a tag makes it an exact match: `tag:"hi energy"` finds that
    tag and not, say, "hi energy remix". Unquoted stays a substring
    search so partial typing still narrows things down. The chips always
    quote, which is why clicking "energy" no longer also pulls in
    "hi energy".
    """
    if not query:
        return True
    tokens = _tokens(query)
    if not tokens:
        return True

    tags = [t.lower() for t in (station.get("tags") or [])]
    text_fields = (
        (station.get("custom_name") or "").lower(),
        (station.get("name") or "").lower(),
        (station.get("url") or "").lower(),
    )

    # Included tags are collected and tested as a group; everything
    # else is tested one term at a time.
    included_tags = []

    for token in tokens:
        negated, is_tag, value, quoted = _split_token(token)
        value = value.lower()
        if not value:
            continue                          # "tag:" alone constrains nothing

        if is_tag:
            result = (value in tags) if quoted else any(value in t for t in tags)
            if not negated:
                included_tags.append(result)
                continue                      # decided below, as a group
        else:
            result = (any(value in f for f in text_fields)
                      or any(value in t for t in tags))
        if result == negated:
            return False

    # Any one included tag is enough.
    if included_tags and not any(included_tags):
        return False
    return True


# -- the tag filter query, as read and written by the chips -----------
# Three states per tag, and the search box is the only place they live:
#   0   not mentioned
#  +1   included - only these stations show
#  -1   excluded - these are hidden
TAG_INCLUDED = 1
TAG_EXCLUDED = -1
TAG_NEUTRAL = 0


def tag_filter_states(query):
    """Map of tag -> TAG_INCLUDED / TAG_EXCLUDED for a query string."""
    states = {}
    for token in _tokens(query):
        negated, is_tag, value, _quoted = _split_token(token)
        if is_tag and value:
            states[value.lower()] = TAG_EXCLUDED if negated else TAG_INCLUDED
    return states


def tag_filter_state(query, tag):
    return tag_filter_states(query).get(tag.lower(), TAG_NEUTRAL)


def set_tag_filter(query, tag, state):
    """Return the query with `tag` forced to `state`.

    Rewrites rather than appends, so a tag can never end up both
    included and excluded - nor listed twice, which is what used to
    happen to any tag with a space in it.
    """
    wanted = tag.lower()
    kept = []
    for token in _tokens(query):
        _negated, is_tag, value, _quoted = _split_token(token)
        if is_tag and value.lower() == wanted:
            continue
        kept.append(token)
    if state == TAG_INCLUDED:
        kept.append("tag:%s" % _quote_tag(tag))
    elif state == TAG_EXCLUDED:
        kept.append("-tag:%s" % _quote_tag(tag))
    return " ".join(kept)


def cycle_tag_filter(query, tag, exclude=False):
    """What a click does. Left-click walks neutral <-> included,
    right-click walks neutral <-> excluded, and either one clears a tag
    that is currently in the other state - so a second click always
    undoes the first."""
    wanted = TAG_EXCLUDED if exclude else TAG_INCLUDED
    current = tag_filter_state(query, tag)
    return set_tag_filter(query, tag, TAG_NEUTRAL if current == wanted else wanted)


def clear_tag_filters(query):
    """Drop every tag: term but leave a typed text search alone."""
    kept = []
    for token in _tokens(query):
        _negated, is_tag, _value, _quoted = _split_token(token)
        if not is_tag:
            kept.append(token)
    return " ".join(kept)


def record_play(station):
    """Bump the play statistics used by the 'played' sort modes."""
    station["last_played"] = time.time()
    try:
        station["play_count"] = int(station.get("play_count", 0)) + 1
    except (TypeError, ValueError):
        station["play_count"] = 1
    return station


def apply_identify_default(stations, settings):
    """One-time switch-on of automatic identification for stations that
    were added before it became the default. Returns True if anything
    changed."""
    if settings.get("identify_default_applied"):
        return False
    for station in stations:
        # Never override a choice somebody made.
        if station.get("shazam_auto_set"):
            continue
        station["shazam_auto"] = True
        if int(station.get("shazam_interval", 30)) > 30:
            station["shazam_interval"] = 30
    settings["identify_default_applied"] = True
    return True


AUDIO_EXTENSIONS = (
    ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac",
    ".wav", ".wma", ".mp4", ".alac", ".aiff", ".aif",
)


EQ_BAND_FREQUENCIES = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)


def eq_band_label(hz):
    """"31" / "1k" / "16k" - short enough to sit under a slider."""
    return f"{hz // 1000}k" if hz >= 1000 else str(hz)


def normalize_eq_bands(values):
    """Exactly ten gains, each within range.

    Defensive because this comes back from JSON, and a backup written by
    an older version has no bands at all.
    """
    out = list(values or [])[:10]
    out += [0] * (10 - len(out))
    cleaned = []
    for value in out:
        try:
            cleaned.append(max(-12, min(12, int(round(float(value))))))
        except (TypeError, ValueError):
            cleaned.append(0)
    return cleaned


# Fields a featured entry must never dictate. Everything else in
# STATION_DEFAULTS is fair game.
#
#   id          would collide with a station already in the library
#   local_path  a path on the curator's machine, meaningless here
#   playlist_url and kind are allowed: a featured YouTube station is a
#               perfectly reasonable thing to publish
#   sort_index  where it lands in YOUR list is yours to decide
#   connect_worst / health are measurements, not settings
# Dropped on the way out: identifiers and measurements that mean nothing
# on another machine. custom_art_path goes because the path itself is
# useless elsewhere - the picture travels instead, embedded, the same
# way a backup carries it.
_FEATURED_SKIP = ("id", "local_path", "sort_index", "connect_worst",
                  "custom_art_path")

# name and url are not in STATION_DEFAULTS - they are supplied when a
# station is created rather than defaulted - so a whitelist built from
# that dict alone silently drops them.
# Per-station settings that have no entry in STATION_DEFAULTS but are
# still part of what somebody tuned. Without them a shared station
# arrives having lost the very corrections the sharer made.
# The card's own words travel with a featured station: they are written
# about that station, and a station without them falls back to the stock
# text as before.
_FEATURED_CORE = ("name", "url", "artist_lookup", "playlist_url", "kind",
                  "card_title", "card_subtitle", "card_body",
                  "twitch_channel", "shows")


def station_from_featured(entry):
    """Build a station from a featured entry, keeping its settings.

    A featured station is one somebody has already tuned - tags written,
    EQ set, artwork chosen - and the point is that the work travels. So
    every field the curator set is taken, filtered to the ones this
    version understands and the ones that make sense on another
    machine.

    Unknown keys are dropped rather than kept: a list published by a
    newer version must not smuggle fields this one will not honour but
    will faithfully save forever.
    """
    import uuid as _uuid

    # Accept both shapes.
    #
    # featured_from_station writes the fields at the top level, but this
    # only ever looked under "settings" - so a round trip through the
    # two lost every tag, every EQ curve and every rename, silently.
    # Older lists that do nest under "settings" still work.
    entry = entry or {}
    raw = entry.get("settings") if isinstance(entry.get("settings"), dict) \
        else entry
    station = dict(STATION_DEFAULTS)
    station["id"] = str(_uuid.uuid4())

    allowed = set(STATION_DEFAULTS) | set(_FEATURED_CORE)
    for key, value in raw.items():
        if key in _FEATURED_SKIP or key not in allowed:
            continue
        station[key] = value

    # Unpack any artwork that came with it.
    blob = raw.get("art_data")
    if blob:
        path = _write_featured_art(station["id"], raw.get("art_name"), blob)
        if path:
            station["custom_art_path"] = path
            # A code's artwork is a thumbnail by necessity. Marked as
            # such so the app can go and find the real one later —
            # nothing else knows the difference by looking.
            if raw.get("art_name") == "shared.jpg":
                station["art_from_code"] = True

    # The display fields, which the entry carries at the top level
    # whether or not it carries a full settings block.
    for key in ("name", "url", "favicon", "homepage"):
        if not station.get(key) and entry.get(key):
            station[key] = entry[key]
    if entry.get("uuid") and not station.get("rb_uuid"):
        station["rb_uuid"] = entry["uuid"]

    # The two fields that would break things if a published list got
    # them wrong.
    station["eq_bands"] = normalize_eq_bands(station.get("eq_bands"))
    if not isinstance(station.get("tags"), list):
        station["tags"] = []
    station["tags"] = [str(t).strip() for t in station["tags"] if str(t).strip()]
    return station


# A whole station, small enough to be a QR code.
#
# There is nowhere to upload a file to, and asking someone to run a
# server to pass a radio station to a friend is absurd. A QR code holds
# about 2.9 kB, which is enough for everything that makes a station
# yours — tags, equalizer, corrections — as long as the artwork is left
# out. Artwork is the one part that is genuinely large and the one part
# the receiving app can fetch again on its own.
CODE_PREFIX = "FREQ1:"
CODE_LIMIT = 2800                       # chars, just inside a QR's limit


def code_thumbnail(data, side=64, quality=50):
    """Artwork shrunk until it fits in a code.

    A cover is a 1000px PNG and a QR holds about 2.9 kB, so the picture
    has to become small rather than be left out — 64px of JPEG is about
    1.5 kB, which leaves room for the station itself. It is a thumbnail,
    and it is a great deal better than a grey square at the other end.
    """
    from PyQt5.QtCore import QBuffer, Qt
    from PyQt5.QtGui import QImage

    if not data:
        return b""
    image = QImage()
    if isinstance(data, str):
        import base64 as _b64
        try:
            data = _b64.b64decode(data)
        except Exception:                       # noqa: BLE001
            return b""
    if not image.loadFromData(data):
        return b""

    for px, q in ((side, quality), (56, 45), (48, 40)):
        small = image.scaled(px, px, Qt.KeepAspectRatioByExpanding,
                             Qt.SmoothTransformation)
        buffer = QBuffer()
        buffer.open(QBuffer.ReadWrite)
        if not small.save(buffer, "JPEG", q):
            return b""
        raw = bytes(buffer.data())
        if len(raw) <= 1800:
            return raw
    return raw


def station_to_code(station, keep_artwork=True):
    """A station as one compact line of text.

    Returns (code, shrunk_artwork). The artwork travels as a thumbnail:
    at full size it is twenty times what a QR can hold, and a station
    that arrives without its cover does not look like the station that
    was sent.
    """
    import base64
    import json
    import zlib

    entry = featured_from_station(station)

    def encode(payload):
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        packed = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode()
        return CODE_PREFIX + packed

    art = entry.get("art_data")
    if keep_artwork and art:
        # Smaller and smaller thumbnails until the code fits. A 64 px
        # one alone could take the code past what a QR holds, and the
        # share window then had no code to show (build 105, task 130e).
        for side, quality in ((64, 50), (56, 45), (48, 40), (40, 35),
                              (32, 30)):
            thumb = code_thumbnail(art, side, quality)
            if not thumb:
                break
            shrunk = dict(entry)
            shrunk["art_data"] = base64.b64encode(thumb).decode()
            shrunk["art_name"] = "shared.jpg"
            code = encode(shrunk)
            if len(code) <= CODE_LIMIT:
                return code, True

    # No artwork, and if it still doesn't fit, the card's long texts
    # and schedule go, then everything but what makes it play and sound
    # the same. A code that is too long is no code at all.
    slim = {k: v for k, v in entry.items()
            if k not in ("art_data", "art_name")}
    code = encode(slim)
    if len(code) > CODE_LIMIT:
        slim = {k: v for k, v in slim.items()
                if k not in ("card_body", "card_subtitle", "card_title",
                             "shows")}
        code = encode(slim)
    if len(code) > CODE_LIMIT:
        keep = ("name", "url", "custom_name", "kind", "playlist_url",
                "twitch_channel", "tags", "eq_bass", "eq_mid", "eq_treble",
                "eq_bands", "gain_db", "insecure", "favicon", "homepage")
        code = encode({k: v for k, v in slim.items() if k in keep})
    return code, False


def station_from_code(text):
    """The station a code describes, or None.

    Anything unreadable returns None rather than raising: this is fed
    from a clipboard and a camera, and both produce nonsense regularly.
    """
    import base64
    import json
    import zlib

    text = (text or "").strip()
    if not text.startswith(CODE_PREFIX):
        return None
    try:
        packed = text[len(CODE_PREFIX):].strip()
        raw = zlib.decompress(base64.urlsafe_b64decode(packed))
        entry = json.loads(raw.decode("utf-8"))
    except Exception:                           # noqa: BLE001
        return None
    if not isinstance(entry, dict) or not entry.get("url"):
        return None
    return station_from_featured(entry)


def featured_from_station(station):
    """The other direction: a station as a featured entry.

    What a curator exports after tidying one up. Measurements and the
    local id are left out for the same reasons the importer ignores
    them.
    """
    out = {}
    allowed = set(STATION_DEFAULTS) | set(_FEATURED_CORE)
    for key, value in station.items():
        if key in _FEATURED_SKIP or key not in allowed:
            continue
        if value == STATION_DEFAULTS.get(key):
            continue                    # defaults are noise in a file
        out[key] = value
    # Both names travel, and neither is overwritten by the other: the
    # station's real name is what the directory calls it, custom_name is
    # what the curator renamed it to, and collapsing them here would
    # lose whichever one lost.
    out["name"] = station.get("name", "")
    out["url"] = station.get("url", "")

    # The artwork itself, not a path to it. Chosen artwork is part of
    # the work a curator did on a station, so it has to travel with it.
    source = (station.get("custom_art_path") or "").strip()
    if source and os.path.isfile(source):
        try:
            with open(source, "rb") as f:
                out["art_data"] = base64.b64encode(f.read()).decode("ascii")
            out["art_name"] = os.path.basename(source)
        except OSError:
            pass
    return out


def _write_featured_art(station_id, name, blob):
    """Save embedded artwork and return where it landed, or ""."""
    try:
        data = base64.b64decode(blob)
    except Exception:  # noqa: BLE001
        return ""
    folder = os.path.join(_config_dir(), "artwork")
    try:
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(
            folder, f"{station_id}-{os.path.basename(name or 'cover.png')}"
        )
        with open(target, "wb") as f:
            f.write(data)
    except OSError:
        return ""
    return target


def is_local_station(station):
    return (station or {}).get("kind") == "local"


def is_youtube_station(station):
    return (station or {}).get("kind") == "youtube"


def is_twitch_station(station):
    """A Twitch channel (build 79: one test for the places that ask)."""
    station = station or {}
    return (station.get("kind") == "twitch"
            or bool(station.get("twitch_channel")))


TWITCH_AD_SECONDS = 30.0


def ad_cover_on(station):
    """Whether this station covers its opening ad (build 83, task 93).

    The user's own choice (True / False) wins; a Twitch channel they never
    set is on.
    """
    station = station or {}
    mine = station.get("ad_override")
    if mine is None:
        return is_twitch_station(station)
    return bool(mine)


def ad_cover_seconds(station):
    """Length of the ad cover: 30 s for a Twitch channel never set."""
    station = station or {}
    if station.get("ad_override") is None and is_twitch_station(station):
        return TWITCH_AD_SECONDS
    return float(station.get("ad_seconds") or 0.0)


def is_custom_station(station):
    """Either kind of custom station - local files or a YouTube list.

    Both behave the same way: a random track, joined partway through,
    with the static covering the change. Only where the audio comes
    from differs.
    """
    return (station or {}).get("kind") in ("local", "youtube")


def scan_local_tracks(folder, limit=20000):
    """Every audio file under `folder`, sorted for a stable order.

    Sorted rather than left in directory order so that "the same
    station" means the same library between launches; the randomness
    comes from picking within it, not from the filesystem.
    """
    if not folder or not os.path.isdir(folder):
        return []
    found = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if name.lower().endswith(AUDIO_EXTENSIONS):
                found.append(os.path.join(root, name))
                if len(found) >= limit:
                    return sorted(found)
    return sorted(found)


def pick_local_track(folder, exclude=None, rng=None):
    """A random track, avoiding an immediate repeat where possible."""
    tracks = scan_local_tracks(folder)
    if not tracks:
        return None
    chooser = rng or random
    if exclude and len(tracks) > 1:
        candidates = [t for t in tracks if t != exclude] or tracks
    else:
        candidates = tracks
    return chooser.choice(candidates)


def lower_old_stall_timeout(settings):
    """Bring an existing install onto the shorter 6s stall timeout.

    Only touches a value that is exactly the old default, so anyone who
    has deliberately set their own keeps it. Returns True if anything
    changed.
    """
    if settings.get("stall_timeout_lowered"):
        return False
    if int(settings.get("stall_timeout", 6) or 6) == 8:
        settings["stall_timeout"] = 6
    settings["stall_timeout_lowered"] = True
    return True


def apply_station_eq_default(stations, settings):
    """One-time move of the old global EQ onto every station.

    The EQ used to be one set of three sliders for the whole app. Making
    it per-station would otherwise silently flatten it for everyone, so
    the old global values are stamped onto each station once. Returns
    True if anything changed.
    """
    if settings.get("eq_moved_to_stations"):
        return False
    bass = int(settings.get("eq_bass", 0) or 0)
    mid = int(settings.get("eq_mid", 0) or 0)
    treble = int(settings.get("eq_treble", 0) or 0)
    if bass or mid or treble:
        for station in stations:
            station["eq_bass"] = bass
            station["eq_mid"] = mid
            station["eq_treble"] = treble
    settings["eq_moved_to_stations"] = True
    return True


def station_eq(station):
    """The three EQ values for a station, clamped, as a plain dict."""
    def band(key):
        try:
            return max(-12, min(12, int(station.get(key, 0) or 0)))
        except (TypeError, ValueError):
            return 0
    return {"bass": band("eq_bass"), "mid": band("eq_mid"),
            "treble": band("eq_treble")}


# ----------------------------------------------------------------------
# Tags
# ----------------------------------------------------------------------
# A palette that stays legible on both light and dark list rows. Named
# so the color picker can show "Teal" instead of a hex code - nobody
# thinks in hex when picking a color for a tag.
TAG_COLOR_CHOICES = [
    ("Red", "#d96c6c"),
    ("Orange", "#d9914e"),
    ("Amber", "#d9b54e"),
    ("Yellow", "#c9c04a"),
    ("Lime", "#a3c94a"),
    ("Green", "#6fbf73"),
    ("Teal", "#4ebf9e"),
    ("Cyan", "#4ea3c9"),
    ("Blue", "#5a8fd9"),
    ("Indigo", "#7b7bd9"),
    ("Purple", "#a86fd9"),
    ("Magenta", "#c96fbf"),
    ("Pink", "#d96f9a"),
    ("Brown", "#a67c52"),
    ("Slate", "#6c7a89"),
    ("Gray", "#8a8a8a"),
]

# Kept as a flat hex list too - this is what automatic assignment and
# any older code that just wants "the Nth color" uses.
TAG_PALETTE = [hex_code for _name, hex_code in TAG_COLOR_CHOICES]

# hex (lowercased) -> human name, for showing a name next to a color
# that was only ever stored as hex (e.g. from an older save file).
TAG_COLOR_NAMES = {hex_code.lower(): name for name, hex_code in TAG_COLOR_CHOICES}


def tag_color_name(hex_code):
    """Best-effort human name for a color. Falls back to the hex
    itself for a color outside the named palette (still possible if a
    future version of the app, or a hand-edited settings file, stores
    something else)."""
    return TAG_COLOR_NAMES.get((hex_code or "").strip().lower(), hex_code)


def all_tags(stations):
    """Every tag in use, in a stable order (alphabetical, case
    preserved from first appearance)."""
    seen = {}
    for station in stations:
        for tag in station.get("tags") or []:
            seen.setdefault(tag.lower(), tag)
    return [seen[key] for key in sorted(seen)]


def eq_fill_for_skin(settings, skin_name):
    """Should this skin's simple EQ blank the area behind the bars?"""
    if not skin_name:
        return False
    return bool((settings.get("skin_eq_fill") or {}).get(skin_name, False))


def set_eq_fill_for_skin(settings, skin_name, on):
    if not skin_name:
        return
    fills = settings.setdefault("skin_eq_fill", {})
    fills[skin_name] = bool(on)


# The Tags… dialog had its own palette until build 58 (task 34). A tag
# coloured there keeps its look, moved to the app's matching colour.
OLD_DIALOG_COLORS = {
    "#e2564b": "#d96c6c",   # Red
    "#e07a5a": "#d9914e",   # Coral -> Orange
    "#d99a2b": "#d9b54e",   # Amber
    "#c9b23f": "#c9c04a",   # Gold -> Yellow
    "#9aae4a": "#a3c94a",   # Olive -> Lime
    "#5dbb6a": "#6fbf73",   # Green
    "#4fc39a": "#4ebf9e",   # Mint -> Teal
    "#3fb5b5": "#4ebf9e",   # Teal
    "#43a6d4": "#4ea3c9",   # Cyan
    "#5a8ce0": "#5a8fd9",   # Blue
    "#7f77dd": "#7b7bd9",   # Indigo
    "#a06ddb": "#a86fd9",   # Violet -> Purple
    "#c761c7": "#c96fbf",   # Magenta
    "#dd6494": "#d96f9a",   # Pink
    "#d4537e": "#d96f9a",   # Rose -> Pink
    "#8a93a6": "#6c7a89",   # Slate
}


def tag_color(settings, tag, assign=True):
    """Color for a tag, assigning a new one from the palette the first
    time we're asked. Lookup is case-insensitive so "Jazz" and "jazz"
    can't drift apart visually."""
    colors = settings.setdefault("tag_colors", {})
    key = tag.strip().lower()
    if key in colors:
        moved = OLD_DIALOG_COLORS.get(str(colors[key]).strip().lower())
        if moved:
            colors[key] = moved
        return colors[key]
    if not assign:
        return TAG_PALETTE[0]
    color = TAG_PALETTE[len(colors) % len(TAG_PALETTE)]
    colors[key] = color
    return color


def set_tag_color(settings, tag, color):
    settings.setdefault("tag_colors", {})[tag.strip().lower()] = color


def rename_tag_color(settings, old, new):
    """Move a tag's colour to its new name (colours are kept by name).
    Leaves the colour alone when the new name already had one of its own
    (a rename onto an existing tag merges the two)."""
    colors = settings.setdefault("tag_colors", {})
    old_key, new_key = old.strip().lower(), new.strip().lower()
    if not new_key or old_key == new_key:
        return
    colour = colors.pop(old_key, None)
    if colour is not None and new_key not in colors:
        colors[new_key] = colour


def rename_tag(stations, old, new):
    """Rename a tag everywhere it's used. Returns how many stations
    changed."""
    old_key, new_name = old.strip().lower(), new.strip()
    if not new_name:
        return 0
    changed = 0
    for station in stations:
        tags = station.get("tags") or []
        updated = []
        hit = False
        for tag in tags:
            if tag.lower() == old_key:
                hit = True
                if new_name.lower() not in [u.lower() for u in updated]:
                    updated.append(new_name)
            else:
                updated.append(tag)
        if hit:
            station["tags"] = updated
            changed += 1
    return changed


def remove_tag(stations, tag):
    """Strip a tag from every station. Returns how many changed."""
    key = tag.strip().lower()
    changed = 0
    for station in stations:
        tags = station.get("tags") or []
        kept = [t for t in tags if t.lower() != key]
        if len(kept) != len(tags):
            station["tags"] = kept
            changed += 1
    return changed


def spare_tags(settings):
    """Tags made in the tag manager that no station has (task 87).

    A tag used to exist only while a station carried it. The tag manager
    can make one before any station has it, so those are kept here, in
    settings["spare_tags"], until the tag manager (or the Tags… bin)
    removes them."""
    tags = settings.get("spare_tags")
    if not isinstance(tags, list):
        return []
    return [str(t).strip() for t in tags if str(t).strip()]


def set_spare_tags(settings, names):
    """A new list each time: the settings defaults are copied shallowly,
    so a list is never changed in place."""
    seen, kept = set(), []
    for name in names:
        name = str(name).strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            kept.append(name)
    settings["spare_tags"] = kept


def rename_spare_tag(settings, old, new):
    old_key = old.strip().lower()
    set_spare_tags(settings, [new if t.lower() == old_key else t
                              for t in spare_tags(settings)])


def forget_spare_tag(settings, tag):
    key = tag.strip().lower()
    set_spare_tags(settings, [t for t in spare_tags(settings)
                              if t.lower() != key])


def library_tags(stations, settings):
    """Every tag: the ones stations carry plus the spare ones, in
    all_tags' order (alphabetical, a station's spelling first)."""
    seen = {}
    for tag in all_tags(stations) + spare_tags(settings):
        seen.setdefault(tag.lower(), tag)
    return [seen[key] for key in sorted(seen)]


def rename_tags(stations, settings, renamed):
    """Several renames at once, {old: new}, as if all happened together.

    One after another, a pair like {"Rock": "Old", "Pop": "Rock"} gave
    Pop's stations "Old" as well: the second rename found the first one's
    result. Every old name goes to a stand-in first, then each stand-in
    to its new name. Colours move with their names (rename_tag_color);
    two old names that become one keep the first one's colour."""
    stand_ins = {}
    for i, old in enumerate(renamed):
        stand_in = "\x00rename%d" % i
        stand_ins[stand_in] = renamed[old]
        rename_tag(stations, old, stand_in)
        rename_tag_color(settings, old, stand_in)
        rename_spare_tag(settings, old, stand_in)
    for stand_in, new in stand_ins.items():
        rename_tag(stations, stand_in, new)
        rename_tag_color(settings, stand_in, new)
        rename_spare_tag(settings, stand_in, new)


# ----------------------------------------------------------------------
# Favorites
# ----------------------------------------------------------------------
# A starred song is a deliberate act, so it is treated as permanent:
# it is exempt from the history cap, survives "clear history", and can
# only be lost by explicitly resetting favorites.
def trim_history(history, limit):
    """Trim to the cap, but never at a favorite's expense.

    The limit applies to ordinary entries only. Starring a thousand
    songs will keep a thousand rows, which is what starring them meant.
    """
    kept = []
    ordinary = 0
    for entry in history:
        if is_favorite(entry):
            kept.append(entry)
            continue
        if ordinary < limit:
            kept.append(entry)
            ordinary += 1
    return kept


def sort_history(history):
    """Favorites first (newest first within each group), then the rest.

    Pinning is a display concern, so it happens here rather than by
    reordering the stored list.
    """
    favorites = [e for e in history if is_favorite(e)]
    rest = [e for e in history if not is_favorite(e)]
    favorites.sort(key=lambda e: e.get("at", 0), reverse=True)
    rest.sort(key=lambda e: e.get("at", 0), reverse=True)
    return favorites + rest


def set_favorite(history, key, favorite, track=None, station_name=""):
    """Star or unstar a song by key.

    If it isn't in the history yet - starring the song currently
    playing, before it has been logged - `track` lets us create the
    entry on the spot.
    """
    for entry in history:
        if entry_key(entry) == key:
            entry["favorite"] = bool(favorite)
            return history, True

    if favorite and track:
        history.insert(0, {
            "title": (track.get("title") or "").strip(),
            "artist": (track.get("artist") or "").strip(),
            "station": station_name,
            "at": time.time(),
            "apple_music_url": track.get("apple_music_url", ""),
            "cover_url": track.get("cover_url", ""),
            "favorite": True,
        })
        return history, True
    return history, False


def favorite_keys(history):
    return {entry_key(e) for e in history if is_favorite(e)}


def count_favorites(history):
    return sum(1 for e in history if is_favorite(e))


def clear_history(history):
    """"Clear" means clear the ordinary entries. Favorites stay -
    losing a starred song to a button labeled "clear history" would be
    a nasty surprise."""
    return [e for e in history if is_favorite(e)]


def reset_favorites(history):
    """Unstar everything. Only ever called after an explicit,
    repeatedly-confirmed request."""
    for entry in history:
        entry["favorite"] = False
    return history


def raise_old_noise_volume(settings):
    """Move anyone still sitting on the old static level up to the new
    default, once.

    The bundled static was re-levelled upward, but a saved settings file
    keeps whatever slider position it had - so an existing install would
    otherwise never see the change. Only the untouched old default (35)
    is moved; anything the user actually chose is left alone.
    """
    if settings.get("noise_volume_raised"):
        return False
    settings["noise_volume_raised"] = True
    if int(settings.get("noise_volume", 35)) == 35:
        settings["noise_volume"] = SETTINGS_DEFAULTS["noise_volume"]
        return True
    return False
