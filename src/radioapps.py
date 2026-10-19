"""
radioapps.py - Other Linux radio apps' own station lists (build 113,
task 152).

Two halves:

  Reading   Shortwave (and its old name, Gradio), MusicPod and Strawberry
            keep SQLite databases; Clementine keeps its "Your radio
            streams" in Clementine.conf; GNOME Radio keeps the stations
            you add in ~/.gnome-radio/gnome-radio.xml. playlists.read_file
            hands those files here. (Goodvibes, Rhythmbox and Tuner are
            plain XML/JSON and are read in playlists.py.)

  Send to   Adds frequencies straight into Goodvibes, Rhythmbox,
            Clementine, Shortwave, MusicPod or Tuner. It only ever adds
            (a link already there is skipped), refuses while that app is
            running (they all write their list back when they close, which
            would undo ours), and copies the app's file to
            "<file>.frequencies-backup-<date>" before writing.

Each format was checked against the app's own source code, not memory:
Goodvibes 0.8.4 (src/core/gv-station-list.c), Shortwave 5.1.0
(data/database/migrations, src/api/station_metadata.rs), Gradio 7.x
(src/gradio-library.vala), MusicPod main (lib/radio/persistence/tables.dart,
lib/third_party/database_module.dart), Clementine 1.4.1
(src/internet/internetradio/savedradio.cpp), Strawberry 1.2.27
(data/schema/schema.sql, src/core/database.cpp), Rhythmbox master
(rhythmdb/rhythmdb-tree.c, plugins/iradio), Tuner main
(src/Services/StarStore.vala, src/Models/Station.vala), GNOME Radio
(src/gnome-radio.c, src/gnome-radio-station.c). No real file from any of
these apps was available, so the files they write were made by hand from
that source.
"""
import glob
import json
import os
import re
import shutil
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

import crashlog


class AppFileError(Exception):
    pass


HOME = os.path.expanduser("~")


def _data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share")


def _config_home():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")


# app key -> (name shown, program names to look for while running,
#             Flatpak ids, places its own list lives, can we send to it)
# Places are tried in order; "$DATA" / "$CONFIG" are the XDG folders.
APPS = {
    "goodvibes": ("Goodvibes", ("goodvibes",), ("io.gitlab.Goodvibes",), (
        "$DATA/goodvibes/stations.xml",
        "~/.var/app/io.gitlab.Goodvibes/data/goodvibes/stations.xml",
        # Before 0.5 Goodvibes kept it with its settings.
        "$CONFIG/goodvibes/stations.xml",
        "~/.var/app/io.gitlab.Goodvibes/config/goodvibes/stations.xml",
    ), True),
    "rhythmbox": ("Rhythmbox", ("rhythmbox",), ("org.gnome.Rhythmbox3",), (
        "$DATA/rhythmbox/rhythmdb.xml",
        "~/.var/app/org.gnome.Rhythmbox3/data/rhythmbox/rhythmdb.xml",
    ), True),
    "clementine": ("Clementine", ("clementine",),
                   ("org.clementine_player.Clementine",), (
        "$CONFIG/Clementine/Clementine.conf",
        "~/.var/app/org.clementine_player.Clementine/config/Clementine/Clementine.conf",
    ), True),
    "shortwave": ("Shortwave", ("shortwave",), ("de.haeckerfelix.Shortwave",), (
        "$DATA/Shortwave/Shortwave.db",
        "~/.var/app/de.haeckerfelix.Shortwave/data/Shortwave/Shortwave.db",
    ), True),
    "musicpod": ("MusicPod", ("musicpod", "org.feichtmeier.Musicpod"),
                 ("org.feichtmeier.Musicpod",), (
        "$DATA/org.feichtmeier.Musicpod/org.feichtmeier.Musicpod.sqlite",
        "$DATA/musicpod/org.feichtmeier.Musicpod.sqlite",
        "~/.var/app/org.feichtmeier.Musicpod/data/org.feichtmeier.Musicpod/org.feichtmeier.Musicpod.sqlite",
        "~/snap/musicpod/current/.local/share/org.feichtmeier.Musicpod/org.feichtmeier.Musicpod.sqlite",
    ), True),
    "tuner": ("Tuner", ("io.github.tuner_labs.tuner", "com.github.louis77.tuner"),
              ("io.github.tuner_labs.tuner", "com.github.louis77.tuner"), (
        "$DATA/io.github.tuner_labs.tuner/starred.json",
        "~/.var/app/io.github.tuner_labs.tuner/data/io.github.tuner_labs.tuner/starred.json",
        # Tuner 1.x, before the project moved.
        "$DATA/com.github.louis77.tuner/favorites.json",
        "~/.var/app/com.github.louis77.tuner/data/com.github.louis77.tuner/favorites.json",
    ), True),
    "gradio": ("Gradio", ("gradio",), ("de.haeckerfelix.gradio",), (
        "$DATA/gradio/gradio.db",
        "~/.var/app/de.haeckerfelix.gradio/data/gradio/gradio.db",
    ), False),
    "strawberry": ("Strawberry", ("strawberry",),
                   ("org.strawberrymusicplayer.strawberry",), (
        "$DATA/strawberry/strawberry/strawberry.db",
        "~/.var/app/org.strawberrymusicplayer.strawberry/data/strawberry/strawberry/strawberry.db",
    ), False),
    "gnomeradio": ("GNOME Radio", ("gnome-radio",), (), (
        "~/.gnome-radio/gnome-radio.xml",
    ), False),
}


def app_name(key):
    return APPS[key][0]


def _expand(pattern):
    path = pattern.replace("$DATA", _data_home()).replace("$CONFIG", _config_home())
    return os.path.expanduser(path)


def _where(path):
    """' (Flatpak)' / ' (Snap)' for a copy that isn't the usual install."""
    if "/.var/app/" in path:
        return " (Flatpak)"
    if "/snap/" in path:
        return " (Snap)"
    return ""


def found_files(key):
    """[(label, path)] for each copy of the app's list on this computer."""
    name = APPS[key][0]
    out, seen = [], set()
    for pattern in APPS[key][3]:
        for path in sorted(glob.glob(_expand(pattern))):
            real = os.path.realpath(path)
            if real in seen or not os.path.isfile(real):
                continue
            # Tuner 2 reads starred.json and ignores an old favorites.json
            # left beside it.
            if (path.endswith("favorites.json") and os.path.exists(
                    os.path.join(os.path.dirname(path), "starred.json"))):
                continue
            seen.add(real)
            out.append((name + _where(path), path))
    return out


# ----------------------------------------------------------------------
# Is the app running?
# ----------------------------------------------------------------------
def is_running(key):
    """True while the app (or its Flatpak) is running for this user.

    Every one of these apps writes its whole list back when it closes, so
    a station added under a running app would vanish at its next save.
    """
    names = set(APPS[key][1])
    flatpaks = APPS[key][2]
    if not os.path.isdir("/proc"):
        return False                    # Windows (WP1): no /proc to look in
    me = os.getpid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if not raw:
            continue
        args = raw.split(b"\0")
        exe = os.path.basename(args[0].decode("utf-8", "replace"))
        if exe in names:
            return True
        if flatpaks and exe in ("flatpak", "bwrap", "flatpak-spawn"):
            text = raw.decode("utf-8", "replace")
            if any(fid in text for fid in flatpaks):
                return True
    return False


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------
def is_sqlite(path):
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _open_copy(path):
    """A private copy of a database, opened read-only.

    Reading the app's own file directly could meet a lock while it runs
    (and must never change it); a copy, with its -wal beside it, reads the
    same rows without touching theirs.
    """
    import sqlite3
    tmp = tempfile.mkdtemp(prefix="frequencies-import-")
    target = os.path.join(tmp, "copy.db")
    shutil.copy2(path, target)
    for extra in ("-wal", "-shm"):
        if os.path.exists(path + extra):
            shutil.copy2(path + extra, target + extra)
    con = sqlite3.connect(target)
    return con, tmp


def _tables(con):
    return {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(con, table):
    return [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]


def read_sqlite(path):
    """(format, entries) for Shortwave, Gradio, MusicPod or Strawberry."""
    try:
        con, tmp = _open_copy(path)
    except Exception as exc:                # noqa: BLE001
        raise AppFileError(f"Couldn't open that database: {exc}") from exc
    try:
        tables = _tables(con)
        if "library" in tables and "is_local" in _columns(con, "library"):
            return "shortwave", _read_shortwave(con)
        if "library" in tables and "collections" in tables:
            return "gradio", _read_gradio(con)
        if "starred_station_table" in tables:
            return "musicpod", _read_musicpod(con)
        if "playlist_items" in tables and "playlists" in tables:
            return "strawberry", _read_strawberry(con)
        raise AppFileError(
            "That database isn't one this app knows (Shortwave, Gradio, "
            "MusicPod or Strawberry).")
    except AppFileError:
        raise
    except Exception as exc:                # noqa: BLE001
        raise AppFileError(f"Couldn't read that database: {exc}") from exc
    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _read_shortwave(con):
    """Shortwave's library: uuid, is_local, data (the station as JSON).

    A directory station's uuid is its radio-browser.info id; a station
    added by hand has a made-up one and its data is required. A directory
    station with no data yet is looked up by fill_in().
    """
    out = []
    for uid, is_local, data in con.execute(
            "SELECT uuid, is_local, data FROM library"):
        meta = {}
        if data:
            try:
                meta = json.loads(data) or {}
            except ValueError:
                meta = {}
        url = meta.get("url_resolved") or meta.get("url") or ""
        entry = {"name": (meta.get("name") or "").strip() or url,
                 "url": url, "favicon": meta.get("favicon") or "",
                 "tags": meta.get("tags") or "",
                 "homepage": meta.get("homepage") or ""}
        if not is_local:
            entry["rb_uuid"] = uid
        out.append(entry)
    return out


def _read_gradio(con):
    """Gradio (Shortwave's old name): only radio-browser station numbers,
    with the collection names, which become tags."""
    names = {}
    try:
        for cid, cname in con.execute(
                "SELECT collection_id, collection_name FROM collections"):
            names[cid] = cname or ""
    except Exception:                       # noqa: BLE001
        pass
    tags = {}
    for sid, cid in con.execute("SELECT station_id, collection_id FROM library"):
        tags.setdefault(str(sid), [])
        if names.get(cid):
            tags[str(sid)].append(names[cid])
    return [{"name": "", "url": "", "favicon": "", "rb_id": sid,
             "tags": ", ".join(t)} for sid, t in tags.items()]


def _read_musicpod(con):
    """MusicPod's starred stations (its SQLite database since 2025)."""
    cols = set(_columns(con, "starred_station_table"))
    want = [c for c in ("uuid", "name", "url", "url_resolved", "favicon",
                        "tags", "homepage") if c in cols]
    out = []
    for row in con.execute(f"SELECT {', '.join(want)} FROM starred_station_table"):
        r = dict(zip(want, row))
        url = r.get("url_resolved") or r.get("url") or ""
        out.append({"name": (r.get("name") or "").strip() or url, "url": url,
                    "favicon": r.get("favicon") or "",
                    "tags": r.get("tags") or "",
                    "homepage": r.get("homepage") or "",
                    "rb_uuid": r.get("uuid") or ""})
    return out


def _read_strawberry(con):
    """The internet streams in Strawberry's saved playlists."""
    out, seen = [], set()
    for title, artist, url in con.execute(
            "SELECT title, artist, url FROM playlist_items "
            "WHERE url LIKE 'http%' ORDER BY playlist, ROWID"):
        url = (url or "").strip()
        if not url or url.lower() in seen:
            continue
        seen.add(url.lower())
        out.append({"name": (title or artist or "").strip() or url,
                    "url": url, "favicon": "", "tags": ""})
    return out


def is_clementine_conf(text):
    return "[SavedRadio]" in text


def read_clementine(path):
    """Clementine's "Your radio streams": [SavedRadio] streams\\N\\url..."""
    from PyQt5.QtCore import QSettings
    s = QSettings(path, QSettings.IniFormat)
    s.beginGroup("SavedRadio")
    out = []
    for i in range(s.beginReadArray("streams")):
        s.setArrayIndex(i)
        url = _text(s.value("url"))
        if url:
            out.append({"name": _text(s.value("name")) or url, "url": url,
                        "favicon": _text(s.value("url_logo")), "tags": ""})
    s.endArray()
    s.endGroup()
    return out


def _text(value):
    """A QSettings value as text: QUrls come back as QUrl objects."""
    if value is None:
        return ""
    if hasattr(value, "toString"):
        value = value.toString()
    return str(value).strip()


def read_gnome_radio(text):
    """GNOME Radio's own file: <gnome_radio><station name=…><stream uri=…/>"""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise AppFileError(f"Malformed GNOME Radio file: {exc}") from exc
    out = []
    for node in root.iter("station"):
        url = ""
        for sub in node:
            if sub.tag == "stream" and sub.get("uri"):
                url = sub.get("uri").strip()
                break
        if not url:
            uri = node.find("uri")
            url = (uri.text or "").strip() if uri is not None else ""
        if url:
            out.append({"name": (node.get("name") or "").strip() or url,
                        "url": url, "favicon": "", "tags": ""})
    return out


def needs_lookup(entries):
    return [e for e in entries if not e.get("url")
            and (e.get("rb_uuid") or e.get("rb_id"))]


def fill_in(entries):
    """Ask radio-browser.info for stations known only by their id.

    Network: never call this on the GUI thread. Entries it can't find stay
    without a URL (and are left out by the import).
    """
    import radiobrowser
    pending = needs_lookup(entries)
    by_uuid = [e for e in pending if e.get("rb_uuid")]
    found = 0
    for i in range(0, len(by_uuid), 100):
        chunk = by_uuid[i:i + 100]
        try:
            items = radiobrowser._request(
                "/json/stations/byuuid",
                {"uuids": ",".join(e["rb_uuid"] for e in chunk)})
        except Exception as exc:            # noqa: BLE001
            crashlog.write(f"[apps] radio-browser lookup failed: {exc}")
            break
        known = {it.get("stationuuid"): it for it in items or []}
        for e in chunk:
            it = known.get(e["rb_uuid"])
            if it:
                _take(e, it)
                found += 1
    # Gradio's old station numbers. radio-browser's API may no longer know
    # them (not checked: the sandbox can't reach it); whatever isn't found
    # is reported as not found.
    for e in (e for e in pending if e.get("rb_id")):
        try:
            items = radiobrowser._request(f"/json/stations/byid/{e['rb_id']}", {})
        except Exception:                   # noqa: BLE001
            continue
        if items:
            _take(e, items[0])
            found += 1
    crashlog.write(f"[apps] looked up {len(pending)} station(s) on "
                   f"radio-browser.info, found {found}")
    return entries


def _take(entry, item):
    entry["url"] = item.get("url_resolved") or item.get("url") or ""
    entry["name"] = entry.get("name") or (item.get("name") or "").strip()
    entry["favicon"] = entry.get("favicon") or item.get("favicon") or ""
    entry["tags"] = entry.get("tags") or item.get("tags") or ""
    entry["rb_uuid"] = item.get("stationuuid") or entry.get("rb_uuid", "")


# ----------------------------------------------------------------------
# Send to
# ----------------------------------------------------------------------
def sendable(station):
    """Only internet streams make sense in another app: not a folder of
    songs, iBroadcast, YouTube or Twitch (their links only work here)."""
    url = (station.get("url") or "").strip().lower()
    if not url.startswith(("http://", "https://")):
        return False
    if station.get("kind") in ("local", "youtube", "twitch", "ibroadcast"):
        return False
    return not (station.get("ibroadcast_urls") or station.get("twitch_channel"))


def send_targets():
    """[(app key, label, path)] for every app list on this computer we can
    add to, in the order of APPS."""
    out = []
    for key, spec in APPS.items():
        if spec[4]:
            out.extend((key, label, path) for label, path in found_files(key))
    return out


def _norm(url):
    return (url or "").strip().rstrip("/").lower()


def _name(station):
    return (station.get("custom_name") or station.get("name")
            or station.get("url") or "").strip()


def _tags(station):
    return [t for t in (station.get("tags") or []) if t]


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def backup(path):
    """Copy the app's file (and a database's -wal/-shm) aside first."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = f"{path}.frequencies-backup-{stamp}"
    shutil.copy2(path, target)
    for extra in ("-wal", "-shm"):
        if os.path.exists(path + extra):
            shutil.copy2(path + extra, target + extra)
    return target


def _write_text(path, text):
    # Write through a symlink to the real file, and swap it in whole.
    real = os.path.realpath(path)
    tmp = real + ".frequencies-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    shutil.copymode(real, tmp)
    os.replace(tmp, real)


def send(key, path, stations):
    """Add the stations to the app's list at `path`.

    Returns (added, already_there, left_out, backup_path). Raises
    AppFileError with words for the user when it won't or can't.
    """
    if is_running(key):
        raise AppFileError(f"{app_name(key)} is open. Close it first (it "
                           "saves its own list when it closes, which would "
                           "undo this), then try again.")
    wanted = [s for s in stations if sendable(s)]
    left_out = len(stations) - len(wanted)
    if not wanted:
        raise AppFileError("None of those can go to another app: folders of "
                           "songs, iBroadcast, YouTube and Twitch frequencies "
                           "only play here.")
    writer = {"goodvibes": _send_goodvibes, "rhythmbox": _send_rhythmbox,
              "clementine": _send_clementine, "shortwave": _send_shortwave,
              "musicpod": _send_musicpod, "tuner": _send_tuner}[key]
    saved = backup(path)
    try:
        added, already = writer(path, wanted)
    except AppFileError:
        raise
    except Exception as exc:                # noqa: BLE001
        raise AppFileError(f"Couldn't add them to {app_name(key)}: {exc}. "
                           f"Its list was not changed.") from exc
    crashlog.write(f"[apps] sent to {app_name(key)} ({path}): {added} added, "
                   f"{already} already there, {left_out} can't go; "
                   f"backup {saved}")
    return added, already, left_out, saved


def _new_only(stations, have):
    """The stations whose link isn't in `have` (a set of _norm urls)."""
    out = []
    for s in stations:
        n = _norm(s.get("url"))
        if n and n not in have:
            have.add(n)
            out.append(s)
    return out


def _insert_before(text, closing, blocks, empty_re, opening):
    idx = text.rfind(closing)
    if idx >= 0:
        return text[:idx] + blocks + text[idx:]
    m = re.search(empty_re, text)
    if m:
        return text[:m.start()] + opening + "\n" + blocks + closing + text[m.end():]
    raise AppFileError(f"Its file doesn't end in {closing}; left alone.")


def _send_goodvibes(path, stations):
    """stations.xml: <Stations><Station><uri/><name/>[<insecure/>]…"""
    import playlists
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        text = "<Stations>\n</Stations>"
    before = playlists._read_goodvibes(text)
    have = {_norm(e["url"]) for e in before}
    new = _new_only(stations, have)
    blocks = "".join(
        "  <Station>\n"
        f"    <uri>{_esc(s['url'])}</uri>\n"
        f"    <name>{_esc(_name(s))}</name>\n"
        + ("    <insecure>true</insecure>\n" if s.get("insecure") else "")
        + "  </Station>\n" for s in new)
    if new:
        out = _insert_before(text, "</Stations>", blocks, r"<Stations\s*/>",
                             "<Stations>")
        if len(playlists._read_goodvibes(out)) != len(before) + len(new):
            raise AppFileError("the new file didn't read back right")
        _write_text(path, out)
    return len(new), len(stations) - len(new)


def _send_rhythmbox(path, stations):
    """rhythmdb.xml: an <entry type="iradio"> per station, as Rhythmbox's
    own "New Internet Radio Station" makes (title, genre, location)."""
    import playlists
    with open(path, encoding="utf-8") as f:
        text = f.read()
    before = playlists._read_rhythmbox(text)
    have = {_norm(e["url"]) for e in before}
    new = _new_only(stations, have)
    blocks = ""
    for s in new:
        tags = _tags(s)
        blocks += ('  <entry type="iradio">\n'
                   f"    <title>{_esc(_name(s))}</title>\n"
                   + (f"    <genre>{_esc(tags[0])}</genre>\n" if tags else "")
                   + "    <artist></artist>\n    <album></album>\n"
                   f"    <location>{_esc(s['url'])}</location>\n"
                   "    <date>0</date>\n"
                   "    <media-type>application/octet-stream</media-type>\n"
                   "  </entry>\n")
    if new:
        out = _insert_before(text, "</rhythmdb>", blocks, r"<rhythmdb[^>]*/>",
                             '<rhythmdb version="2.0">')
        if len(playlists._read_rhythmbox(out)) != len(before) + len(new):
            raise AppFileError("the new file didn't read back right")
        _write_text(path, out)
    return len(new), len(stations) - len(new)


def _send_clementine(path, stations):
    """Through Qt's own settings reader/writer, which is what Clementine
    uses (its links are stored as QUrl values, @Variant(...) in the file)."""
    from PyQt5.QtCore import QSettings, QUrl
    s = QSettings(os.path.realpath(path), QSettings.IniFormat)
    s.beginGroup("SavedRadio")
    rows = []
    for i in range(s.beginReadArray("streams")):
        s.setArrayIndex(i)
        rows.append((s.value("url"), s.value("name"), s.value("url_logo")))
    s.endArray()
    have = {_norm(_text(r[0])) for r in rows}
    new = _new_only(stations, have)
    if new:
        s.beginWriteArray("streams", len(rows) + len(new))
        for i, (url, name, logo) in enumerate(rows):
            s.setArrayIndex(i)
            s.setValue("url", url)
            s.setValue("name", name)
            s.setValue("url_logo", logo if logo is not None else QUrl())
        for i, st in enumerate(new, start=len(rows)):
            s.setArrayIndex(i)
            s.setValue("url", QUrl(st["url"]))
            s.setValue("name", _name(st))
            s.setValue("url_logo", QUrl(st.get("favicon") or ""))
        s.endArray()
    s.endGroup()
    s.sync()
    if s.status() != QSettings.NoError:
        raise AppFileError("Qt couldn't write Clementine.conf")
    return len(new), len(stations) - len(new)


# Every field of Shortwave's StationMetadata: it has no defaults, so a
# station whose JSON lacks one is dropped when Shortwave loads.
_SW_TEXT = ("changeuuid", "stationuuid", "name", "url", "url_resolved",
            "homepage", "favicon", "tags", "country", "countrycode", "state",
            "language", "languagecodes", "codec")
_SW_INT = ("votes", "bitrate", "hls", "lastcheckok", "clickcount",
           "clicktrend", "ssl_error")
_SW_NULL = ("lastchangetime", "lastchangetime_iso8601", "lastchecktime",
            "lastchecktime_iso8601", "lastcheckoktime",
            "lastcheckoktime_iso8601", "lastlocalchecktime", "clicktimestamp",
            "clicktimestamp_iso8601", "geo_lat", "geo_long")


def _sw_metadata(station, uid):
    meta = {k: "" for k in _SW_TEXT}
    meta.update({k: 0 for k in _SW_INT})
    meta.update({k: None for k in _SW_NULL})
    meta.update(stationuuid=uid, name=_name(station), url=station["url"],
                url_resolved=station["url"],
                homepage=station.get("homepage") or "",
                favicon=station.get("favicon") or "",
                tags=",".join(_tags(station)), has_extended_info=False)
    return meta


def _send_shortwave(path, stations):
    import sqlite3
    con = sqlite3.connect(os.path.realpath(path), timeout=3)
    try:
        cols = _columns(con, "library")
        if "is_local" not in cols or "data" not in cols:
            raise AppFileError("This Shortwave library is from a version "
                               "older than 2021; open it in a newer Shortwave "
                               "once, close it, and try again.")
        have, uuids = set(), set()
        for uid, data in con.execute("SELECT uuid, data FROM library"):
            uuids.add(uid)
            try:
                meta = json.loads(data or "{}") or {}
            except ValueError:
                meta = {}
            for key in ("url", "url_resolved"):
                if meta.get(key):
                    have.add(_norm(meta[key]))
        new = [s for s in _new_only(stations, have)
               if not (s.get("rb_uuid") and s["rb_uuid"] in uuids)]
        with con:
            for s in new:
                rb = (s.get("rb_uuid") or "").strip()
                uid = rb or str(uuid.uuid4())
                con.execute("INSERT INTO library (uuid, is_local, data) "
                            "VALUES (?, ?, ?)",
                            (uid, 0 if rb else 1,
                             json.dumps(_sw_metadata(s, uid))))
                uuids.add(uid)
    finally:
        con.close()
    return len(new), len(stations) - len(new)


def _send_musicpod(path, stations):
    import sqlite3
    con = sqlite3.connect(os.path.realpath(path), timeout=3)
    try:
        cols = set(_columns(con, "starred_station_table"))
        if not cols:
            raise AppFileError("MusicPod's database has no starred stations "
                               "table yet; star one station in MusicPod "
                               "once, close it, and try again.")
        full = {"name", "url", "url_resolved"} <= cols
        have, uuids = set(), set()
        pick = [c for c in ("uuid", "url", "url_resolved") if c in cols]
        for row in con.execute(f"SELECT {', '.join(pick)} FROM starred_station_table"):
            r = dict(zip(pick, row))
            uuids.add(r.get("uuid"))
            have.update(_norm(r.get(k)) for k in ("url", "url_resolved") if r.get(k))
        new = [s for s in _new_only(stations, have)
               if not (s.get("rb_uuid") and s["rb_uuid"] in uuids)
               # An older MusicPod keeps only the radio-browser id.
               and (full or s.get("rb_uuid"))]
        with con:
            for s in new:
                values = {"uuid": (s.get("rb_uuid") or "").strip() or str(uuid.uuid4())}
                if full:
                    values.update(name=_name(s), url=s["url"],
                                  url_resolved=s["url"])
                    for key, val in (("favicon", s.get("favicon") or ""),
                                     ("homepage", s.get("homepage") or ""),
                                     ("tags", ",".join(_tags(s)))):
                        if key in cols:
                            values[key] = val
                keys = list(values)
                con.execute(
                    f"INSERT OR IGNORE INTO starred_station_table "
                    f"({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
                    [values[k] for k in keys])
    finally:
        con.close()
    return len(new), len(stations) - len(new)


# What Tuner 2 reads for each station (Station.basic), radio-browser's names.
_TUNER_FIELDS = {k: "" for k in (
    "changeuuid", "stationuuid", "name", "url", "url_resolved", "homepage",
    "favicon", "tags", "country", "countrycode", "iso_3166_2", "state",
    "language", "languagecodes", "lastchangetime", "lastchangetime_iso8601",
    "codec", "lastchecktime", "lastchecktime_iso8601", "lastcheckoktime",
    "lastcheckoktime_iso8601", "lastlocalchecktime",
    "lastlocalchecktime_iso8601", "clicktimestamp", "clicktimestamp_iso8601",
    "geo_lat", "geo_long")}
_TUNER_FIELDS.update({k: 0 for k in (
    "votes", "bitrate", "hls", "lastcheckok", "clickcount", "clicktrend",
    "ssl_error")})
_TUNER_FIELDS.update(has_extended_info=False, starred=True)


def _tuner_row(station, template):
    uid = (station.get("rb_uuid") or "").strip() or str(uuid.uuid4())
    mine = {"stationuuid": uid, "id": uid, "name": _name(station),
            "title": _name(station), "url": station["url"],
            "url_resolved": station["url"], "urlResolved": station["url"],
            "url-resolved": station["url"],
            "favicon": station.get("favicon") or "",
            "favicon-url": station.get("favicon") or "",
            "homepage": station.get("homepage") or "",
            "tags": ",".join(_tags(station)), "starred": True}
    # Copy the shape of a station already in the file (Tuner 1 and 2 name
    # things differently); a file with none gets Tuner 2's shape.
    if not template:
        template = _TUNER_FIELDS
    row = {}
    for key, sample in template.items():
        if key in mine:
            row[key] = mine[key]
        elif isinstance(sample, bool):
            row[key] = False
        elif isinstance(sample, (int, float)):
            row[key] = 0
        elif isinstance(sample, str):
            row[key] = ""
        else:
            row[key] = None
    return row


def _send_tuner(path, stations):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.strip():
        data = json.loads(text)
    else:                       # Tuner makes an empty file on first start
        data = {"app": "io.github.tuner_labs.tuner",
                "file": os.path.basename(path), "schema": "2.0",
                "stations": [], "searches": []}
    rows = data.get("stations") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise AppFileError("Tuner's file isn't a list of stations; left alone.")
    have = set()
    for r in rows:
        if isinstance(r, dict):
            for key in ("url", "url_resolved", "urlResolved", "url-resolved"):
                if r.get(key):
                    have.add(_norm(r[key]))
    new = _new_only(stations, have)
    template = next((r for r in rows if isinstance(r, dict)), None)
    rows.extend(_tuner_row(s, template) for s in new)
    if new:
        _write_text(path, json.dumps(data, indent=2, ensure_ascii=False))
    return len(new), len(stations) - len(new)
