"""Frequencies — sync between paired devices, the part without Qt
(task 126, Batch CJ).

The wire protocol is notes/sync-protocol.md; the Android port speaks the
same one, so change that file first and this one to match, never the
other way round.

What lives here:
  * this device's id and name (sync.json) and its pairs (sync-peers.json,
    the pair keys; never in settings.json or a backup);
  * stamps: every station field, shared setting and star carries
    [t ms, device id] so the newest edit wins per item (sync-state.json);
    `observe` finds this device's own edits by comparing fingerprints, so
    nothing else in the app has to know sync exists;
  * `build_state` / `merge` (the state message, section 6 and 7);
  * framing, pairing and the encrypted session (sections 3 to 5), on a
    plain blocking socket. P-256, HKDF-SHA256, HMAC-SHA256 and AES-256-GCM
    all come from the `cryptography` package: nothing homemade.

`sync.py` runs the network and the Settings side on top of this.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import struct
import threading
import time

import storage

PROTOCOL = 1
TCP_PORT = 47614
UDP_PORT = 47615
SERVICE = "_frequencies._tcp.local."
APP_TAG = "frequencies-sync"
MAX_FRAME = 16 * 1024 * 1024
ART_BATCH = 12 * 1024 * 1024
# What this desktop puts in one `art` message it sends (build 177, task
# 231): a few pictures at a time, so a slow phone saves each message
# quickly and what came is kept if the session drops. A single picture
# may still fill a whole ART_BATCH.
ART_SEND_BATCH = 4 * 1024 * 1024
STEP_TIMEOUT = 15.0

CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 10
CODE_LIFETIME = 600
CODE_TRIES = 5
TOMBSTONE_DAYS = 365

SYNC_FILE = "sync.json"
PEERS_FILE = "sync-peers.json"
STATE_FILE = "sync-state.json"

# Station keys that belong to this device (section 6.1). `custom_art_path`
# travels as the made-up "art" field instead; `android_folder` is the
# phone's own folder link.
NEVER_SENT = ("id", "custom_art_path", "connect_worst", "art_checked",
              "android_folder")

# Section 6.2. Every other settings key is never sent and never applied.
SHARED_SETTINGS = (
    "theme", "accent_source", "accent_fixed", "photo_pack", "band_logos",
    "lyrics_on", "artist_on", "clock_12h", "sort_mode", "sort_desc",
    "tag_colors", "spare_tags", "shuffle", "noise_between_stations",
    "noise_volume", "crossfade_ms", "auto_skip_broken",
    "auto_skip_after_tries", "stall_timeout", "health_recheck",
    "health_recheck_minutes", "normalize", "lyric_size",
    "shazam_history_limit", "sleep_minutes", "sleep_fade_seconds",
    # Whether a station's 10-band equalizer values are used (build 167):
    # the values travel with each station, so the switch that turns them
    # on travels too.
    "eq_advanced")
DESKTOP_SETTINGS = ("companion_closed", "show_reminders", "artist_panel",
                    "jump_on_change", "open_in_apps")
SYNC_SETTINGS = SHARED_SETTINGS + DESKTOP_SETTINGS

# Settings that name a station by its id: re-pointed when a station's id
# goes away for its twin's (section 6.1). Device-local, never sent.
STATION_ID_SETTINGS = ("last_station_id", "ad_filler_station_id",
                       "sleep_wake_station_id")

# How often this device looks for paired devices it isn't connected to
# and syncs with them (build 167, task 223): minutes, 0 = only when the
# user presses Sync now. Also once at launch unless 0. Each device keeps
# its own: never sent (section 6.2's lists don't hold it).
INTERVAL_KEY = "sync_interval_minutes"
INTERVAL_DEFAULT = 30
INTERVAL_CHOICES = (5, 15, 30, 60, 120, 0)

# A picture bigger than this is sent as a smaller copy (build 167): one
# art message holds at most ART_BATCH of base64, so a 10 MB photo never
# arrived and the other device waited for it for good.
ART_SEND_MAX = 8 * 1024 * 1024
ART_SHRINK_PX = 2048

# A Clear history on this device (Android has one; the desktop keeps the
# key so a later button only has to set it): seconds, local only.
CLEARED_KEY = "history_cleared_at"

# "What to sync" (build 169, task 225): what this device sends to and
# takes from one paired device. Section 6.4 of the protocol. Every part is
# on unless the user turned it off; a part a later build adds is on too.
WHAT_PARTS = ("stations", "art", "settings", "favorites", "history")
SETTING_GROUPS = (
    ("look", ("theme", "accent_source", "accent_fixed", "clock_12h")),
    ("playback", ("shuffle", "noise_between_stations", "noise_volume",
                  "crossfade_ms", "normalize", "auto_skip_broken",
                  "auto_skip_after_tries", "stall_timeout",
                  "health_recheck", "health_recheck_minutes",
                  "sleep_minutes", "sleep_fade_seconds", "eq_advanced")),
    ("lyrics", ("lyrics_on", "artist_on", "photo_pack", "band_logos",
                "lyric_size", "shazam_history_limit")),
    ("list", ("sort_mode", "sort_desc", "tag_colors", "spare_tags",
              "jump_on_change")),
    ("desktop", ("companion_closed", "show_reminders", "artist_panel",
                 "open_in_apps")),
)
GROUP_OF = {key: group for group, keys in SETTING_GROUPS for key in keys}


def clean_what(raw):
    """A what-to-sync choice with every part and group as a bool
    (missing = on). Pictures need Saved Frequencies: they travel as a
    station field."""
    raw = raw if isinstance(raw, dict) else {}
    groups = raw.get("groups") if isinstance(raw.get("groups"), dict) else {}
    out = {part: raw.get(part) is not False for part in WHAT_PARTS}
    out["groups"] = {g: groups.get(g) is not False for g, _ in SETTING_GROUPS}
    if not out["stations"]:
        out["art"] = False
    if not any(out["groups"].values()):
        out["settings"] = False
    return out


def nothing_what():
    """Sends nothing and takes nothing (a pairing waiting for its choice)."""
    out = {part: False for part in WHAT_PARTS}
    out["groups"] = {g: False for g, _ in SETTING_GROUPS}
    return out


def setting_allowed(key, what):
    if what is None:
        return True
    return bool(what["settings"]) and \
        bool(what["groups"].get(GROUP_OF.get(key, ""), True))


# Untouched values (build 169, task 224). The first time a device dates a
# value and it is still what this platform starts with, its stamp is
# [0, me] ("never edited"), so it loses to any real edit on another
# device: a fresh computer's defaults no longer win over a phone's
# changes just because the computer looked last. Section 7.
#
# Shared keys a fresh desktop doesn't write into settings.json, with the
# values that mean "never set".
_EXTRA_DEFAULTS = {
    "theme": ("midnight",),
    "accent_source": ("artwork",),
    "accent_fixed": (None, ""),
    "photo_pack": (False, None),
    "spare_tags": ([], None),
    "sort_desc": (False, None),
    "companion_closed": ([], None),
    "show_reminders": (True,),
    "open_in_apps": (None, ""),
}
# A default station's own picture travels as `art`; what a fresh profile
# writes for these depends only on that picture, so they're compared too.
_shipped = None
_shipped_lock = threading.Lock()


def setting_is_default(key, value):
    fp = fingerprint(value)
    if key in storage.SETTINGS_DEFAULTS and \
            fp == fingerprint(storage.SETTINGS_DEFAULTS[key]):
        return True
    return any(fp == fingerprint(v) for v in _EXTRA_DEFAULTS.get(key, ()))


def shipped_stations():
    """{normalized address: {field: value}} of the default stations a fresh
    profile starts with (storage._default_stations), as station_fields
    gives them, picture included. Made once, in a scratch folder."""
    global _shipped
    with _shipped_lock:
        if _shipped is not None:
            return _shipped
        out = {}
        path = os.path.join(os.path.dirname(os.path.abspath(storage.__file__)),
                            "default_stations.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, ValueError):
            entries = []
        import shutil
        import tempfile
        scratch = tempfile.mkdtemp(prefix="freq-shipped-")
        try:
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict) or not entry.get("url"):
                    continue
                try:
                    station = storage._normalize_station(
                        storage.station_from_featured(entry, scratch))
                    fields = station_fields(station)
                except Exception as exc:        # noqa: BLE001
                    _log(f"default station {entry.get('name')!r}: {exc!r}")
                    continue
                out[norm_url(station.get("url"))] = fields
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        _shipped = out
        return out


def station_field_is_default(station, field, value):
    """Is this a value nobody chose? A default station (same address as
    one a fresh profile starts with) is compared with how it ships, its
    added_at always counts (it is when the profile was made); any other
    station with STATION_DEFAULTS. name, url and the like never are."""
    fp = fingerprint(value)
    shipped = shipped_stations().get(norm_url(station.get("url")))
    if shipped is not None:
        if field == "added_at":
            return True
        if field in shipped:
            return fp == fingerprint(shipped[field])
    if field == "art":
        return value is None
    return field in storage.STATION_DEFAULTS and \
        fp == fingerprint(storage.STATION_DEFAULTS[field])


def _log(text):
    try:
        import crashlog
        crashlog.write("[sync] " + text)
    except Exception:                           # noqa: BLE001
        pass


def now_ms():
    return int(time.time() * 1000)


def b64(data):
    return base64.b64encode(data).decode("ascii")


def unb64(text):
    return base64.b64decode(str(text or "").encode("ascii"), validate=True)


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def newer(a, b):
    """Is stamp a ([t, dev, …]) newer than stamp b? None is oldest."""
    if not a:
        return False
    if not b:
        return True
    return (int(a[0]), str(a[1])) > (int(b[0]), str(b[1]))


def norm_url(url):
    url = str(url or "").strip().lower()
    return url[:-1] if url.endswith("/") else url


# ----------------------------------------------------------------------
# Files
# ----------------------------------------------------------------------
_file_lock = threading.RLock()


def _path(name):
    return os.path.join(storage._config_dir(), name)


def _read(name, default):
    try:
        with open(_path(name), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def _write(name, payload, private=False):
    with _file_lock:
        path = _path(name)
        tmp = f"{path}.{os.getpid()}-{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1, ensure_ascii=False)
        if private:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, path)


def default_name():
    forced = os.environ.get("FREQ_SYNC_NAME", "").strip()
    if forced:
        return forced
    try:
        name = socket.gethostname().split(".")[0].strip()
    except OSError:
        name = ""
    return name or "Frequencies"


def identity():
    """{"id", "name"} for this install, made on first use."""
    with _file_lock:
        data = _read(SYNC_FILE, {})
        changed = False
        dev = str(data.get("device_id") or "")
        if len(dev) != 32 or any(c not in "0123456789abcdef" for c in dev):
            data["device_id"] = secrets.token_hex(16)
            changed = True
        if not str(data.get("device_name") or "").strip():
            data["device_name"] = default_name()
            changed = True
        if os.environ.get("FREQ_SYNC_NAME") and \
                data["device_name"] != default_name():
            data["device_name"] = default_name()
            changed = True
        if changed:
            _write(SYNC_FILE, data)
        return {"id": data["device_id"], "name": data["device_name"]}


def load_peers():
    """{peer id: {"name", "key" (bytes), "paired_at", "last_sync"}}."""
    out = {}
    peers = _read(PEERS_FILE, {}).get("peers")
    for pid, entry in (peers.items() if isinstance(peers, dict) else []):
        try:
            key = unb64(entry.get("key"))
        except (ValueError, TypeError, AttributeError):
            continue
        if len(key) == 32:
            out[pid] = {"name": str(entry.get("name") or "another device"),
                        "key": key,
                        "paired_at": float(entry.get("paired_at") or 0),
                        "last_sync": float(entry.get("last_sync") or 0)}
            # Build 169 (task 225): what to sync with it, whether that is
            # still to be chosen (paired from here, nothing synced yet),
            # and what it is ("desktop"/"android", from its state).
            if isinstance(entry.get("what"), dict):
                out[pid]["what"] = clean_what(entry["what"])
            if entry.get("ask"):
                out[pid]["ask"] = True
            if entry.get("platform") in ("desktop", "android"):
                out[pid]["platform"] = entry["platform"]
    return out


def save_peers(peers):
    out = {}
    for pid, p in peers.items():
        out[pid] = {"name": p["name"], "key": b64(p["key"]),
                    "paired_at": p.get("paired_at", 0),
                    "last_sync": p.get("last_sync", 0)}
        for extra in ("what", "ask", "platform"):
            if p.get(extra):
                out[pid][extra] = p[extra]
    _write(PEERS_FILE, {"v": 1, "peers": out}, private=True)


def blank_store():
    return {"v": 1, "clock": 0, "st": {}, "set": {}, "fav": {}, "tomb": {},
            "cleared": None, "pending_art": {}, "untouched_fix": 0,
            "filled_fix": 0}


def load_store():
    data = _read(STATE_FILE, {})
    store = blank_store()
    for key, default in store.items():
        value = data.get(key)
        if key == "cleared":
            if isinstance(value, list) and len(value) == 2:
                store[key] = value
        elif isinstance(value, type(default)):
            store[key] = value
    return store


def save_store(store):
    _write(STATE_FILE, store)


# ----------------------------------------------------------------------
# Art: the made-up "art" field
# ----------------------------------------------------------------------
_art_cache = {}


def _sha_of_file(path, st):
    key = (path, st.st_size, st.st_mtime)
    sha = _art_cache.get(key)
    if sha is None:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
        except OSError:
            return None
        sha = h.hexdigest()
        _art_cache[key] = sha
    return sha


def _ext_of(path):
    ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
    return ext if ext.isalnum() and len(ext) <= 5 else "png"


def _smaller_copy(path, sha):
    """A picture too big to send: a copy at most ART_SHRINK_PX on its
    long side, made once and kept in the art folder (named after the
    big file's sha, so it stays the same picture with the same sha for
    as long as the big file doesn't change). Its path, or ""."""
    folder = art_folder()
    for ext in ("jpg", "png"):
        done = os.path.join(folder, f"sync-small-{sha[:16]}.{ext}")
        if os.path.isfile(done):
            return done
    try:
        import imagelock
        from PyQt5.QtCore import Qt
        image = imagelock.image(path)
        if image.isNull():
            return ""
        if max(image.width(), image.height()) > ART_SHRINK_PX:
            image = image.scaled(ART_SHRINK_PX, ART_SHRINK_PX,
                                 Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ext = "png" if image.hasAlphaChannel() else "jpg"
        done = os.path.join(folder, f"sync-small-{sha[:16]}.{ext}")
        with imagelock.LOCK:
            ok = image.save(done + ".tmp", ext.upper(),
                            -1 if ext == "png" else 90)
        if not ok:
            return ""
        os.replace(done + ".tmp", done)
    except Exception as exc:                    # noqa: BLE001
        _log(f"couldn't make a smaller copy of a big picture: {exc!r}")
        return ""
    if os.path.getsize(done) > ART_SEND_MAX:
        return ""
    _log(f"a {os.path.getsize(path) // 1048576} MB picture is sent as a "
         f"{os.path.getsize(done) // 1024} KB copy")
    return done


def art_file(station):
    """(path, sha, ext) of what this device sends as the station's own
    picture, or None. Usually the file at custom_art_path itself; a
    picture over ART_SEND_MAX is sent as a smaller copy."""
    path = (station.get("custom_art_path") or "").strip()
    if not path or not os.path.isfile(path):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    sha = _sha_of_file(path, st)
    if sha is None:
        return None
    if st.st_size > ART_SEND_MAX:
        small = _smaller_copy(path, sha)
        if not small:
            return None
        try:
            small_sha = _sha_of_file(small, os.stat(small))
        except OSError:
            return None
        if small_sha is None:
            return None
        return small, small_sha, _ext_of(small)
    return path, sha, _ext_of(path)


def art_of(station):
    """{"sha", "ext"} of the station's own picture, or None."""
    found = art_file(station)
    if found is None:
        return None
    return {"sha": found[1], "ext": found[2]}


def art_folder():
    folder = os.path.join(storage._config_dir(), "artwork")
    os.makedirs(folder, exist_ok=True)
    return folder


def save_art(sha, ext, data, station_id=""):
    """Write a picture that came from another device; its path. One file
    per station (build 167): two stations with the same picture shared
    one file before, so cropping one cropped the other."""
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError("picture doesn't match its sha")
    ext = str(ext).lower()
    ext = ext if ext.isalnum() and len(ext) <= 5 else "png"
    sid = "".join(c for c in str(station_id) if c.isalnum() or c == "-")[:40]
    name = f"sync-{sid}-{sha[:16]}.{ext}" if sid else f"sync-{sha[:16]}.{ext}"
    path = os.path.join(art_folder(), name)
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)
    return path


def art_index(stations):
    """{sha: path} for every station picture this device can send."""
    out = {}
    for s in stations:
        found = art_file(s)
        if found:
            out[found[1]] = found[0]
    return out


# ----------------------------------------------------------------------
# Stamps (section 7)
# ----------------------------------------------------------------------
def _tick(store):
    t = max(now_ms(), int(store.get("clock") or 0) + 1)
    store["clock"] = t
    return t


def station_fields(station):
    out = {k: v for k, v in station.items()
           if k not in NEVER_SENT and not str(k).startswith("_")}
    out["art"] = art_of(station)
    return out


def song_key(entry):
    return storage.entry_key(entry)


def _favorites_now(history):
    favs = {}
    for e in history:
        key = song_key(e)
        if key == "|":
            continue
        favs[key] = favs.get(key, False) or bool(e.get("favorite"))
    return favs


def repair_first_scan(stations, settings, store, me):
    """Once (build 169, task 224): builds 160-168 dated every value
    [now, me] the first time they looked at it, so a value nobody ever
    changed looked like a fresh edit. A stamp of this device's from that
    first look, on a value that is still the default and unchanged since,
    becomes [0, me]. The first look is this device's oldest stamp (and
    what follows it within two minutes; 160-166 took one millisecond per
    field). True if anything changed."""
    if store.get("untouched_fix"):
        return False
    store["untouched_fix"] = 1
    own = [int(r[0]) for rec in store["st"].values() for r in rec.values()
           if isinstance(r, list) and len(r) >= 3 and r[1] == me
           and int(r[0]) > 0]
    own += [int(r[0]) for r in store["set"].values()
            if isinstance(r, list) and len(r) >= 3 and r[1] == me
            and int(r[0]) > 0]
    if not own:
        return True
    limit = min(own) + 120000 + len(own)
    fixed = 0
    by_id = {str(s.get("id") or ""): s for s in stations}
    for sid, rec in store["st"].items():
        station = by_id.get(sid)
        if station is None:
            continue
        fields = station_fields(station)
        for field, r in rec.items():
            if not (isinstance(r, list) and len(r) >= 3 and r[1] == me
                    and 0 < int(r[0]) <= limit and field in fields):
                continue
            value = fields[field]
            if r[2] == fingerprint(value) and \
                    station_field_is_default(station, field, value):
                rec[field] = [0, me, r[2]]
                fixed += 1
    for key, r in store["set"].items():
        if not (isinstance(r, list) and len(r) >= 3 and r[1] == me
                and 0 < int(r[0]) <= limit and key in settings):
            continue
        if r[2] == fingerprint(settings[key]) and \
                setting_is_default(key, settings[key]):
            store["set"][key] = [0, me, r[2]]
            fixed += 1
    _log(f"untouched values from the first look marked never edited: {fixed}")
    return True


FILLED = -1     # a stamp's t for a value only filled in here (section 7)


def repair_filled_in(stations, store, me):
    """Once (build 171, task 226): in 169, a station that came from
    another device while Station pictures were off had its empty picture
    dated as this device's own edit ([0, me], or [now, me] on a default
    station), so turning pictures back on never brought the picture. A
    station from another device (its address stamp isn't ours) with no
    picture here and a picture stamp of ours gets [-1, me]: never sent,
    and any picture stamp from another device wins. True if run."""
    if store.get("filled_fix"):
        return False
    store["filled_fix"] = 1
    fixed = 0
    by_id = {str(s.get("id") or ""): s for s in stations}
    for sid, rec in store["st"].items():
        station = by_id.get(sid)
        art, url = rec.get("art"), rec.get("url")
        if station is None or not (isinstance(art, list) and len(art) >= 3
                                   and isinstance(url, list)
                                   and len(url) >= 2):
            continue
        if art[1] == me and int(art[0]) >= 0 and url[1] != me \
                and art_of(station) is None:
            rec["art"] = [FILLED, me, fingerprint(None)]
            fixed += 1
    _log(f"pictures dated while they weren't synced, now left to the "
         f"other device: {fixed}")
    return True


def observe(stations, settings, history, store, me):
    """Stamp this device's own edits since the last look. True if any."""
    changed = repair_first_scan(stations, settings, store, me)
    changed = repair_filled_in(stations, store, me) or changed
    # One stamp for the whole look: ticking per field ran the clock
    # ahead of the wall by one millisecond per field.
    t = _tick(store)
    st = store["st"]
    seen = set()
    for s in stations:
        sid = str(s.get("id") or "")
        if not sid:
            continue
        seen.add(sid)
        rec = st.setdefault(sid, {})
        pending = sid in store["pending_art"]
        for field, value in station_fields(s).items():
            if field == "art" and pending:
                continue
            fp = fingerprint(value)
            old = rec.get(field)
            if old is None:
                # First look at it: an untouched value is "never edited".
                untouched = station_field_is_default(s, field, value)
                rec[field] = [0 if untouched else t, me, fp]
                changed = True
            elif old[2] != fp:
                rec[field] = [max(t, int(old[0]) + 1), me, fp]
                changed = True
    for sid in [k for k in st if k not in seen]:
        st.pop(sid, None)
        store["pending_art"].pop(sid, None)
        if sid not in store["tomb"]:
            store["tomb"][sid] = [t, me]
        changed = True

    for key in SYNC_SETTINGS:
        if key not in settings:
            continue
        fp = fingerprint(settings[key])
        old = store["set"].get(key)
        if old is None:
            untouched = setting_is_default(key, settings[key])
            store["set"][key] = [0 if untouched else t, me, fp]
            changed = True
        elif old[2] != fp:
            store["set"][key] = [max(t, int(old[0]) + 1), me, fp]
            changed = True
        elif int(old[0]) == 0 and old[1] == me and \
                not setting_is_default(key, settings[key]):
            # The platform's default changed under an untouched value
            # (shuffle, build 181): it is still what this profile had, so
            # it beats a fresh device's untouched default ([1, me] > [0,
            # …]) but loses to any real edit. Section 7.
            store["set"][key] = [1, me, fp]
            changed = True

    for key, starred in _favorites_now(history).items():
        old = store["fav"].get(key)
        if old is None:
            if starred:
                store["fav"][key] = [starred, t, me]
                changed = True
        elif bool(old[0]) != starred:
            store["fav"][key] = [starred, t, me]
            changed = True

    try:
        cleared = int(float(settings.get(CLEARED_KEY) or 0) * 1000)
    except (TypeError, ValueError):
        cleared = 0
    if cleared and (not store["cleared"]
                    or cleared > int(store["cleared"][0])):
        store["cleared"] = [cleared, me]
        changed = True

    cutoff = now_ms() - TOMBSTONE_DAYS * 86400 * 1000
    for sid in [k for k, v in store["tomb"].items() if int(v[0]) < cutoff]:
        store["tomb"].pop(sid, None)
    return changed


def build_state(stations, settings, history, store, me, platform="desktop",
                what=None):
    """The state message (section 6). Call `observe` first. `what`: the
    what-to-sync choice for the device it goes to (clean_what; None =
    everything); a part that is off is left out (section 6.4)."""
    w = what or clean_what(None)
    out_st = []
    for s in (sorted(stations, key=lambda x: str(x.get("id")))
              if w["stations"] else []):
        sid = str(s.get("id") or "")
        rec = store["st"].get(sid, {})
        fields = {}
        for field, value in station_fields(s).items():
            stamp = rec.get(field)
            if stamp is None or (field == "art" and not w["art"]) \
                    or int(stamp[0]) < 0:
                continue
            if field == "art" and sid in store["pending_art"]:
                value = store["pending_art"][sid]
            fields[field] = [value, int(stamp[0]), stamp[1]]
        out_st.append({"id": sid, "f": fields})
    for sid, stamp in (sorted(store["tomb"].items()) if w["stations"]
                       else []):
        out_st.append({"id": sid, "del": [int(stamp[0]), stamp[1]]})
    out_set = {}
    for key in SYNC_SETTINGS:
        stamp = store["set"].get(key)
        if key in settings and stamp and int(stamp[0]) >= 0 and \
                setting_allowed(key, w):
            out_set[key] = [settings[key], int(stamp[0]), stamp[1]]
    favs = {k: [bool(v[0]), int(v[1]), v[2]] for k, v in store["fav"].items()
            if int(v[1]) >= 0} if w["favorites"] else {}
    rows = []
    for e in (history if w["history"] else []):
        if not (e.get("title") or e.get("artist")):
            continue
        rows.append({"title": e.get("title", ""), "artist": e.get("artist", ""),
                     "station": e.get("station", ""),
                     "at": float(e.get("at") or 0),
                     "apple_music_url": e.get("apple_music_url", ""),
                     "cover_url": e.get("cover_url", "")})
    return {"t": "state", "v": PROTOCOL, "platform": platform,
            "now": now_ms(), "stations": out_st, "settings": out_set,
            "favorites": favs, "history": rows,
            "history_cleared": store["cleared"] if w["history"] else None}


def digest(state):
    """What a state says, without when it was sent."""
    return fingerprint({k: v for k, v in state.items() if k != "now"})


class Merged:
    def __init__(self):
        self.stations = False
        self.settings = set()
        self.history = False
        self.need_art = {}          # sha -> [station ids]
        self.renamed = {}           # old station id -> kept id
        self.removed = set()        # station ids deleted here

    @property
    def changed(self):
        return bool(self.stations or self.settings or self.history)


def _fresh_stamp(store, rec, field):
    """A stamp of this device's that beats the field's current one."""
    t = _tick(store)
    old = rec.get(field)
    if old:
        t = max(t, int(old[0]) + 1)
        store["clock"] = max(int(store.get("clock") or 0), t)
    return t


def _tag_union(first, second):
    out, seen = [], set()
    for tag in list(first or []) + list(second or []):
        if not isinstance(tag, str) or not tag.strip():
            continue
        low = tag.strip().lower()
        if low not in seen:
            seen.add(low)
            out.append(tag)
    return out


def _number(value, cast):
    try:
        return cast(value or 0)
    except (TypeError, ValueError):
        return cast(0)


def _join_twins(station, rec, twins, fields, store, me, out):
    """One station under two ids just became one (section 6.1). Fields
    went to the newest stamp; now play_count and last_played take the
    larger of the two, and tags both lists (the smaller id's first, so
    both devices get the same list). A value that differs from what the
    stamps chose gets a fresh stamp of this device's, so it wins on the
    other device too."""
    mine, remote_id, local_id = twins

    def theirs(field):
        triple = fields.get(field)
        if isinstance(triple, list) and len(triple) >= 3 \
                and _valid_stamp(triple[1:]):
            return True, triple[0]
        return False, None

    joined = {}
    for field, cast in (("play_count", int), ("last_played", float)):
        has, value = theirs(field)
        if has or field in mine:
            joined[field] = max(_number(mine.get(field), cast),
                                _number(value, cast))
    has, value = theirs("tags")
    if has or "tags" in mine:
        own = mine.get("tags") if isinstance(mine.get("tags"), list) else []
        other = value if isinstance(value, list) else []
        joined["tags"] = (_tag_union(own, other) if local_id < remote_id
                          else _tag_union(other, own))
    for field, value in joined.items():
        if station.get(field) == value:
            continue
        station[field] = value
        rec[field] = [_fresh_stamp(store, rec, field), me, fingerprint(value)]
        out.stations = True
    if joined:
        _log(f"twin stations joined: play count {joined.get('play_count')}, "
             f"{len(joined.get('tags') or [])} tags")


# Two default stations whose address the Android port shipped without
# its query string (task 227, build 171): the same station on both
# devices never joined as twins (rule 6.1 compares whole addresses).
# {address without the query: the address the desktop ships}, from the
# shipped list, so only default stations with a query string are in it.
_fixes = None


def address_fixes():
    global _fixes
    if _fixes is None:
        out = {}
        path = os.path.join(os.path.dirname(os.path.abspath(storage.__file__)),
                            "default_stations.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, ValueError):
            entries = []
        for entry in entries if isinstance(entries, list) else []:
            url = str((entry or {}).get("url") or "").strip() \
                if isinstance(entry, dict) else ""
            # Only stream addresses (task 230): the YouTube defaults
            # (build 174) have a query string too, all on one base
            # (".../playlist"); Android 175 leaves them out the same way.
            if "?" in url and url.lower().startswith(("http://",
                                                      "https://")):
                out[norm_url(url.split("?", 1)[0])] = url
        _fixes = out
    return _fixes


def join_old_addresses(stations, settings, store, me):
    """One station under two ids whose addresses differ only by the query
    string of a default station's shipped address (task 227): join them
    as rule 6.1 does (smaller id kept, fields by stamp, play count, last
    played and tags joined, the other id tombstoned) and give the kept one
    the shipped address. Only for those default stations; the general
    twin rule still compares whole addresses."""
    out = Merged()
    fixes = address_fixes()
    if not fixes:
        return out
    groups = {}
    for s in stations:
        url = norm_url(s.get("url"))
        base = norm_url(url.split("?", 1)[0])
        if base in fixes and url in (base, norm_url(fixes[base])):
            groups.setdefault(base, []).append(s)
    for base, group in groups.items():
        if len({norm_url(s.get("url")) for s in group}) < 2:
            continue        # same address twice: rule 6.1's own case
        group.sort(key=lambda s: str(s.get("id")))
        keep = group[0]
        kid = str(keep.get("id"))
        krec = store["st"].setdefault(kid, {})
        for other in group[1:]:
            oid = str(other.get("id"))
            orec = store["st"].get(oid, {})
            before = dict(keep)
            joined = {}
            for field, stamp in orec.items():
                if field in NEVER_SENT or not isinstance(stamp, list) \
                        or len(stamp) < 3 or not newer(stamp, krec.get(field)):
                    continue
                if field == "art":
                    krec["art"] = list(stamp)
                    keep["custom_art_path"] = other.get("custom_art_path", "")
                    store["pending_art"].pop(kid, None)
                    if oid in store["pending_art"]:
                        store["pending_art"][kid] = store["pending_art"][oid]
                    continue
                if field in other:
                    keep[field] = other[field]
                    krec[field] = list(stamp)
            for field in ("play_count", "last_played", "tags"):
                if field in other and isinstance(orec.get(field), list):
                    joined[field] = [other[field]] + list(orec[field][:2])
            _join_twins(keep, krec, (before, oid, kid), joined, store, me, out)
            stations.remove(other)
            store["st"].pop(oid, None)
            store["pending_art"].pop(oid, None)
            store["tomb"][oid] = [_tick(store), me]
            out.renamed[oid] = kid
            out.removed.add(oid)
            out.stations = True
            _log(f"joined {keep.get('name')!r}: its old address without "
                 f"the query string was the same station")
        right = fixes[base]
        if keep.get("url") != right:
            keep["url"] = right
            krec["url"] = [_fresh_stamp(store, krec, "url"), me,
                           fingerprint(right)]
            out.stations = True
    if out.renamed:
        for s in stations:
            if s.get("ad_filler_station_id") in out.renamed:
                s["ad_filler_station_id"] = \
                    out.renamed[s["ad_filler_station_id"]]
        for key in STATION_ID_SETTINGS:
            if settings.get(key) in out.renamed:
                settings[key] = out.renamed[settings[key]]
                out.settings.add(key)
    return out


def _valid_stamp(raw):
    """A stamp another device may send: [t >= 0, device]. [-1, dev] is
    never sent (section 7); one that arrives anyway is ignored."""
    return (isinstance(raw, list) and len(raw) >= 2
            and isinstance(raw[0], (int, float)) and raw[0] >= 0
            and isinstance(raw[1], str))


def merge(stations, settings, history, store, me, state, history_limit=300,
          what=None):
    """Take what's newer in another device's state (section 6 and 7).
    Edits `stations`, `settings` and `history` in place. `what`: this
    device's what-to-sync choice for the sender (None = everything); a
    part that is off is not taken (section 6.4)."""
    out = Merged()
    if not isinstance(state, dict):
        return out
    w = what or clean_what(None)
    try:
        ahead = int(state.get("now") or 0) - now_ms()
        if ahead > 5 * 60 * 1000:
            _log(f"clock: the other device is {ahead // 1000} s ahead")
    except (TypeError, ValueError):
        pass

    remote = [r for r in (state.get("stations") or [] if w["stations"]
                          else [])
              if isinstance(r, dict) and r.get("id")]
    remote_ids = {str(r["id"]) for r in remote}
    by_id = {str(s.get("id")): s for s in stations}

    # Deletes first: a tombstone beats everything, for good.
    gone_urls = {}
    gone_here = {}                  # address -> a station deleted just now
    for r in remote:
        sid = str(r["id"])
        if not _valid_stamp(r.get("del")):
            continue
        stamp = [int(r["del"][0]), r["del"][1]]
        if sid not in store["tomb"] or newer(stamp, store["tomb"][sid]):
            store["tomb"][sid] = stamp
        if sid in by_id:
            gone = by_id.pop(sid)
            stations.remove(gone)
            gone_urls[sid] = norm_url(gone.get("url"))
            gone_here[norm_url(gone.get("url"))] = gone
            out.stations = True
            out.removed.add(sid)
        store["st"].pop(sid, None)
        store["pending_art"].pop(sid, None)

    for r in remote:
        sid = str(r["id"])
        if _valid_stamp(r.get("del")) or sid in store["tomb"]:
            continue
        fields = r.get("f") if isinstance(r.get("f"), dict) else {}
        target = sid
        twins = None
        if sid not in by_id:
            # One station under two ids (section 6.1): keep the smaller.
            url = norm_url((fields.get("url") or [""])[0])
            twin = None
            if url:
                twin = next((s for s in stations
                             if norm_url(s.get("url")) == url
                             and str(s.get("id")) not in remote_ids), None)
            if twin is not None:
                tid = str(twin.get("id"))
                twins = (dict(twin), sid, tid)
                if sid < tid:
                    twin["id"] = sid
                    by_id[sid] = by_id.pop(tid)
                    store["st"][sid] = store["st"].pop(tid, {})
                    if tid in store["pending_art"]:
                        store["pending_art"][sid] = \
                            store["pending_art"].pop(tid)
                    store["tomb"][tid] = [_tick(store), me]
                    out.renamed[tid] = sid
                    out.stations = True
                else:
                    store["tomb"][sid] = [_tick(store), me]
                    target = tid
            else:
                fresh = {"id": sid}
                # Its twin here was deleted by this same state (the other
                # device joined them first): keep this device's own keys,
                # so its picture file isn't lost and asked for again.
                was = gone_here.get(url) if url else None
                for key in NEVER_SENT[1:] + ("local_path",):
                    if was is not None and was.get(key):
                        fresh[key] = was[key]
                stations.append(fresh)
                by_id[sid] = fresh
                store["st"][sid] = {}
                out.stations = True
        station = by_id[target]
        rec = store["st"].setdefault(target, {})
        for field, triple in fields.items():
            if field in NEVER_SENT or not isinstance(triple, list) \
                    or len(triple) < 3 or not _valid_stamp(triple[1:]) \
                    or (field == "art" and not w["art"]):
                continue
            value, stamp = triple[0], [int(triple[1]), triple[2]]
            if not newer(stamp, rec.get(field)):
                continue
            if field == "art":
                rec["art"] = stamp + [fingerprint(value)]
                have = art_of(station)
                if not value:
                    store["pending_art"].pop(target, None)
                    if station.get("custom_art_path"):
                        station["custom_art_path"] = ""
                        out.stations = True
                elif not isinstance(value, dict) or not value.get("sha"):
                    continue
                elif have and have["sha"] == value["sha"]:
                    store["pending_art"].pop(target, None)
                else:
                    store["pending_art"][target] = {
                        "sha": str(value["sha"]),
                        "ext": str(value.get("ext") or "png")}
                    out.need_art.setdefault(str(value["sha"]), []) \
                        .append(target)
                continue
            if field == "local_path":
                mine = (station.get("local_path") or "").strip()
                if mine and os.path.exists(mine) and mine != value:
                    # Both computers keep their own music folder.
                    rec[field] = stamp + [fingerprint(mine)]
                    continue
            station[field] = value
            rec[field] = stamp + [fingerprint(value)]
            out.stations = True
        if "art" not in rec:
            # Its picture didn't come with it (Station pictures are off
            # for this device, or the sender has none to send): this
            # empty picture is nobody's choice. [-1, me] is never sent and
            # loses to any picture stamp, so the picture arrives once it
            # is sent (section 7, "Filled in").
            rec["art"] = [FILLED, me, fingerprint(art_of(station))]
        if twins is not None:
            _join_twins(station, rec, twins, fields, store, me, out)

    # New stations get the keys the rest of the app expects.
    for s in stations:
        if out.stations:
            storage._normalize_station(s)
    # A deleted station whose twin (same address) stays: whatever named
    # the deleted one now names the twin (section 6.1).
    for sid, url in gone_urls.items():
        if not url or sid in out.renamed:
            continue
        twin = next((s for s in stations
                     if norm_url(s.get("url")) == url
                     and str(s.get("id")) != sid), None)
        if twin is not None:
            out.renamed[sid] = str(twin.get("id"))
    if out.renamed:
        for s in stations:
            old = s.get("ad_filler_station_id")
            if old in out.renamed:
                s["ad_filler_station_id"] = out.renamed[old]
        for key in STATION_ID_SETTINGS:
            if settings.get(key) in out.renamed:
                settings[key] = out.renamed[settings[key]]
                out.settings.add(key)

    remote_set = state.get("settings") if isinstance(
        state.get("settings"), dict) else {}
    for key in SYNC_SETTINGS:
        if not setting_allowed(key, w):
            continue
        triple = remote_set.get(key)
        if not isinstance(triple, list) or len(triple) < 3 \
                or not _valid_stamp(triple[1:]):
            continue
        stamp = [int(triple[1]), triple[2]]
        if newer(stamp, store["set"].get(key)):
            store["set"][key] = stamp + [fingerprint(triple[0])]
            if settings.get(key, object()) != triple[0]:
                settings[key] = triple[0]
                out.settings.add(key)

    # Stars, newest per song.
    remote_rows = [e for e in (state.get("history") or [] if w["history"]
                               else [])
                   if isinstance(e, dict)]
    fav_changed = set()
    favs = state.get("favorites") if isinstance(
        state.get("favorites"), dict) and w["favorites"] else {}
    for key, triple in favs.items():
        if not isinstance(triple, list) or len(triple) < 3 \
                or not _valid_stamp(triple[1:]):
            continue
        stamp = [int(triple[1]), triple[2]]
        old = store["fav"].get(key)
        if old is None or newer(stamp, old[1:]):
            store["fav"][key] = [bool(triple[0])] + stamp
            fav_changed.add(key)

    cleared = state.get("history_cleared") if w["history"] else None
    if _valid_stamp(cleared) and newer(cleared, store["cleared"]):
        store["cleared"] = [int(cleared[0]), cleared[1]]

    # History: the union, then the rules of section 6.3.
    def row_id(e):
        try:
            return song_key(e) + "|" + str(int(float(e.get("at") or 0)))
        except (TypeError, ValueError):
            return song_key(e) + "|0"

    before = fingerprint([[row_id(e), bool(e.get("favorite"))]
                          for e in history])
    known = {row_id(e) for e in history}
    merged = list(history)
    for e in remote_rows:
        if not (e.get("title") or e.get("artist")):
            continue
        rid = row_id(e)
        if rid in known:
            continue
        known.add(rid)
        try:
            at = float(e.get("at") or 0)
        except (TypeError, ValueError):
            at = 0.0
        merged.append({"title": str(e.get("title") or ""),
                       "artist": str(e.get("artist") or ""),
                       "station": str(e.get("station") or ""),
                       "at": at,
                       "apple_music_url": str(e.get("apple_music_url") or ""),
                       "cover_url": str(e.get("cover_url") or ""),
                       "favorite": False})
    for e in merged:
        entry = store["fav"].get(song_key(e))
        if entry is not None:
            e["favorite"] = bool(entry[0])
    cut = int(store["cleared"][0]) if store["cleared"] else 0
    merged.sort(key=lambda e: float(e.get("at") or 0), reverse=True)
    kept, starred_seen = [], set()
    for e in merged:
        key = song_key(e)
        if e.get("favorite"):
            if key in starred_seen:
                continue
            starred_seen.add(key)
        elif cut and float(e.get("at") or 0) * 1000 < cut:
            continue
        kept.append(e)
    kept = storage.trim_history(kept, history_limit)
    if fingerprint([[row_id(e), bool(e.get("favorite"))]
                    for e in kept]) != before:
        history[:] = kept
        out.history = True
    return out


# ----------------------------------------------------------------------
# Frames (section 3)
# ----------------------------------------------------------------------
class SyncError(Exception):
    pass


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise SyncError("the other device hung up")
        buf.extend(chunk)
    return bytes(buf)


def send_frame(sock, data):
    if not 0 < len(data) <= MAX_FRAME:
        raise SyncError("frame too big")
    sock.sendall(struct.pack(">I", len(data)) + data)


def recv_frame(sock):
    n = struct.unpack(">I", _recv_exact(sock, 4))[0]
    if not 0 < n <= MAX_FRAME:
        raise SyncError("bad frame length")
    return _recv_exact(sock, n)


def send_json(sock, msg):
    send_frame(sock, json.dumps(msg, separators=(",", ":"),
                                ensure_ascii=False).encode("utf-8"))


def recv_json(sock):
    try:
        msg = json.loads(recv_frame(sock).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SyncError("not a message") from exc
    if not isinstance(msg, dict):
        raise SyncError("not a message")
    return msg


# ----------------------------------------------------------------------
# Crypto (sections 4 and 5)
# ----------------------------------------------------------------------
def crypto_ok():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM is not None
    except Exception:                           # noqa: BLE001
        return False


def hkdf(ikm, salt, info, length=64):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt,
                info=info).derive(ikm)


def new_ec_key():
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.generate_private_key(ec.SECP256R1())


def ec_public_bytes(key):
    from cryptography.hazmat.primitives import serialization
    return key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint)


def ec_shared(key, peer_bytes):
    from cryptography.hazmat.primitives.asymmetric import ec
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),
                                                        peer_bytes)
    return key.exchange(ec.ECDH(), peer)


def pair_keys(z, code, client_pub, server_pub, client_id, server_id):
    """(confirm_key, pair_key), section 4 step 3."""
    info = (b"frequencies-pair v1" + client_pub + server_pub
            + client_id.encode("ascii") + server_id.encode("ascii"))
    okm = hkdf(z, code.encode("ascii"), info)
    return okm[:32], okm[32:]


def session_keys(pair_key, client_nonce, server_nonce):
    """(k_cs, k_sc), section 5 step 3."""
    okm = hkdf(pair_key, client_nonce + server_nonce, b"frequencies-sync v1")
    return okm[:32], okm[32:]


def mac(key, label):
    return hmac.new(key, label.encode("ascii"), hashlib.sha256).digest()


def new_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def show_code(code):
    return code[:5] + "-" + code[5:]


def clean_code(text):
    """A typed code as the protocol wants it, or "" if it can't be one."""
    text = str(text or "").upper().replace("-", "").replace(" ", "")
    text = text.replace("O", "0").replace("I", "1").replace("L", "1")
    if len(text) != CODE_LENGTH or any(c not in CODE_ALPHABET for c in text):
        return ""
    return text


def qr_text(me, name, code, port, ips):
    from urllib.parse import quote
    return (f"frequencies-sync:?v={PROTOCOL}&id={me}&name={quote(name)}"
            f"&code={code}&port={port}&ip={','.join(ips)}")


class Channel:
    """An encrypted session over a socket (section 5 step 4)."""

    def __init__(self, sock, send_key, recv_key, peer_id, peer_name):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.sock = sock
        self._send = AESGCM(send_key)
        self._recv = AESGCM(recv_key)
        self._send_n = 0
        self._recv_n = 0
        self._lock = threading.Lock()
        self.peer_id = peer_id
        self.peer_name = peer_name

    @staticmethod
    def _nonce(n):
        return b"\0\0\0\0" + struct.pack(">Q", n)

    def send(self, msg):
        data = json.dumps(msg, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")
        with self._lock:
            box = self._send.encrypt(self._nonce(self._send_n), data, None)
            self._send_n += 1
            send_frame(self.sock, box)

    def recv(self):
        box = recv_frame(self.sock)
        from cryptography.exceptions import InvalidTag
        try:
            data = self._recv.decrypt(self._nonce(self._recv_n), box, None)
        except InvalidTag as exc:
            raise SyncError("wrong key") from exc
        self._recv_n += 1
        try:
            msg = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise SyncError("not a message") from exc
        if not isinstance(msg, dict):
            raise SyncError("not a message")
        return msg


def _check_hello(msg, want):
    if msg.get("t") != want:
        raise SyncError(f"expected {want}, got {msg.get('t')!r}")
    if msg.get("v") != PROTOCOL:
        _log(f"other device speaks version {msg.get('v')!r}")
        raise SyncError("other version")
    pid = str(msg.get("id") or "")
    if len(pid) != 32 or any(c not in "0123456789abcdef" for c in pid):
        raise SyncError("bad device id")
    return pid, str(msg.get("name") or "another device")[:80]


def client_session(sock, me, name, pair_key, peer_id):
    """Open a sync session to a paired device. Returns a Channel."""
    nonce = secrets.token_bytes(16)
    send_json(sock, {"t": "hello", "v": PROTOCOL, "id": me, "name": name,
                     "nonce": b64(nonce)})
    msg = recv_json(sock)
    pid, pname = _check_hello(msg, "hello")
    if pid != peer_id:
        raise SyncError("a different device answered")
    theirs = unb64(msg.get("nonce"))
    if len(theirs) != 16:
        raise SyncError("bad nonce")
    k_cs, k_sc = session_keys(pair_key, nonce, theirs)
    return Channel(sock, k_cs, k_sc, pid, pname)


def server_session(sock, hello, me, name, peers):
    """Answer a hello. Returns a Channel, or None for an unpaired device
    (which gets nothing at all)."""
    pid, pname = _check_hello(hello, "hello")
    peer = peers.get(pid)
    if peer is None:
        return None
    theirs = unb64(hello.get("nonce"))
    if len(theirs) != 16:
        raise SyncError("bad nonce")
    nonce = secrets.token_bytes(16)
    send_json(sock, {"t": "hello", "v": PROTOCOL, "id": me, "name": name,
                     "nonce": b64(nonce)})
    k_cs, k_sc = session_keys(peer["key"], theirs, nonce)
    return Channel(sock, k_sc, k_cs, pid, pname)


def client_pair(sock, me, name, code):
    """Pair with a device showing `code`. Returns (id, name, pair key)."""
    key = new_ec_key()
    pub = ec_public_bytes(key)
    send_json(sock, {"t": "pair", "v": PROTOCOL, "id": me, "name": name,
                     "pub": b64(pub)})
    msg = recv_json(sock)
    pid, pname = _check_hello(msg, "pair")
    server_pub = unb64(msg.get("pub"))
    confirm, pair_key = pair_keys(ec_shared(key, server_pub), code, pub,
                                  server_pub, me, pid)
    send_json(sock, {"t": "confirm", "mac": b64(mac(confirm, "client"))})
    msg = recv_json(sock)
    if msg.get("t") == "pair-failed":
        raise SyncError("wrong code")
    if msg.get("t") != "confirm" or not hmac.compare_digest(
            unb64(msg.get("mac")), mac(confirm, "server")):
        raise SyncError("the other device didn't prove the code")
    return pid, pname, pair_key


def server_pair(sock, first, me, name, code):
    """Answer a pair request while showing `code`. Returns (id, name, pair
    key); raises SyncError("wrong code") on a wrong code."""
    pid, pname = _check_hello(first, "pair")
    client_pub = unb64(first.get("pub"))
    key = new_ec_key()
    pub = ec_public_bytes(key)
    send_json(sock, {"t": "pair", "v": PROTOCOL, "id": me, "name": name,
                     "pub": b64(pub)})
    confirm, pair_key = pair_keys(ec_shared(key, client_pub), code,
                                  client_pub, pub, pid, me)
    msg = recv_json(sock)
    if msg.get("t") != "confirm" or not hmac.compare_digest(
            unb64(msg.get("mac")), mac(confirm, "client")):
        try:
            send_json(sock, {"t": "pair-failed"})
        except OSError:
            pass
        raise SyncError("wrong code")
    send_json(sock, {"t": "confirm", "mac": b64(mac(confirm, "server"))})
    return pid, pname, pair_key


def local_ipv4s():
    """This device's IPv4 addresses on the local network."""
    ips = set()
    try:
        import ifaddr
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if isinstance(ip.ip, str) and not ip.ip.startswith("127."):
                    ips.add(ip.ip)
    except Exception:                           # noqa: BLE001
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    return sorted(ip for ip in ips if not ip.startswith("169.254."))


def broadcast_addresses():
    out = {"255.255.255.255"}
    try:
        import ifaddr
        import ipaddress
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if isinstance(ip.ip, str) and not ip.ip.startswith("127."):
                    net = ipaddress.IPv4Network(
                        f"{ip.ip}/{ip.network_prefix}", strict=False)
                    out.add(str(net.broadcast_address))
    except Exception:                           # noqa: BLE001
        pass
    return sorted(out)
