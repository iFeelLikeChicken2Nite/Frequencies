"""
playlists.py - Import stations from (and export them to) other radio
apps and players.

Most radio apps store their station lists in one of a handful of plain
formats, so a single reader per format covers a lot of apps:

  .m3u / .m3u8   VLC, Audacious, Clementine, Kodi, most desktop players
  .pls           Shoutcast/Winamp lineage - RadioSure, Screamer, foobar
  .xspf          VLC, Audacious
  .opml          podcast and radio apps that export an outline list
  rhythmdb.xml   Rhythmbox (its radio entries; read only)
  .json          RadioDroid and anything else built on radio-browser,
                 plus Frequencies' own backup files
  stations.xml   Goodvibes (GTK radio player for Linux)

Everything is decoded to the same shape - a list of {"name", "url",
"favicon"} dicts - so the import dialog can show one checkbox list
whatever the source was.

Detection is by content, not by file extension, because plenty of apps
save a PLS with a .m3u name or hand out extensionless files.
"""
import configparser
import io
import json
import os
import re
import uuid
import xml.etree.ElementTree as ET

import storage


class PlaylistError(Exception):
    pass


FORMATS = {
    "goodvibes": "Goodvibes (stations.xml)",
    "m3u": "M3U / M3U8 (VLC, Audacious, Kodi, most players)",
    "pls": "PLS (Winamp / Shoutcast lineage, RadioSure, foobar2000)",
    "xspf": "XSPF (VLC, Audacious)",
    "opml": "OPML (outline lists from radio and podcast apps)",
    "rhythmbox": "Rhythmbox (rhythmdb.xml)",
    "radiodroid": "JSON (RadioDroid, radio-browser exports)",
    "streamdeck": "Frequencies backup",
    # Read only, from the apps' own files (radioapps.py, build 113).
    "shortwave": "Shortwave (library)",
    "gradio": "Gradio (library)",
    "musicpod": "MusicPod (starred stations)",
    "strawberry": "Strawberry (streams in its playlists)",
    "clementine": "Clementine (Your radio streams)",
    "gnomeradio": "GNOME Radio (your stations)",
}

EXPORT_FORMATS = ("goodvibes", "m3u", "pls", "xspf", "opml", "radiodroid",
                  "streamdeck")

# How many entries the last read_file() left out because they were links
# to web pages (TuneIn and the like), not streams. The import dialog
# mentions it so a short list doesn't look like a bug.
LAST_SKIPPED = 0

_PAGE_HOSTS = ("opml.radiotime.com", "tunein.com", "radiotime.com")

_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                      re.IGNORECASE)


def _is_url(text):
    return bool(_URL_RE.match((text or "").strip()))


# ----------------------------------------------------------------------
# Readers
# ----------------------------------------------------------------------
def _read_m3u(text):
    stations = []
    pending_name = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF"):
            # #EXTINF:-1 tvg-logo="..." ,Station Name
            # The first comma outside quotes: a group-title with several
            # tags ("jazz, smooth") has commas inside its quotes.
            tail = re.sub(r'"[^"]*"', lambda m: " " * len(m.group(0)), line)
            comma = tail.find(",")
            tail = line[comma + 1:] if comma >= 0 else ""
            pending_name = tail.strip()
            logo = re.search(r'tvg-logo="([^"]+)"', line)
            pending_logo = logo.group(1) if logo else ""
            group = re.search(r'group-title="([^"]+)"', line)
            stations.append({"name": pending_name, "url": "",
                             "favicon": pending_logo,
                             "tags": group.group(1) if group else ""})
            continue
        if line.startswith("#"):
            continue
        if stations and not stations[-1]["url"]:
            stations[-1]["url"] = line
        else:
            stations.append({"name": pending_name or line, "url": line, "favicon": ""})
        pending_name = ""
    return [s for s in stations if _is_url(s["url"])]


def _read_pls(text):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise PlaylistError(f"Malformed PLS file: {exc}") from exc

    section = None
    for candidate in parser.sections():
        if candidate.lower() == "playlist":
            section = candidate
            break
    if section is None:
        raise PlaylistError("PLS file has no [playlist] section.")

    items = parser[section]
    entries = {}
    for key, value in items.items():
        match = re.match(r"(file|title)(\d+)$", key.strip(), re.IGNORECASE)
        if not match:
            continue
        field, index = match.group(1).lower(), int(match.group(2))
        entries.setdefault(index, {})[field] = value.strip()

    stations = []
    for index in sorted(entries):
        entry = entries[index]
        url = entry.get("file", "")
        if _is_url(url):
            stations.append({
                "name": entry.get("title") or url,
                "url": url,
                "favicon": "",
            })
    return stations


def _strip_ns(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _read_xspf(text):
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise PlaylistError(f"Malformed XSPF file: {exc}") from exc

    stations = []
    for track in root.iter():
        if _strip_ns(track.tag) != "track":
            continue
        name, url, image, tags = "", "", "", ""
        for child in track:
            field = _strip_ns(child.tag)
            value = (child.text or "").strip()
            if field == "title":
                name = value
            elif field == "location":
                url = value
            elif field == "image":
                image = value
            elif field == "meta" and child.get("rel", "").rstrip("/").endswith("tags"):
                tags = value
        if _is_url(url):
            stations.append({"name": name or url, "url": url,
                             "favicon": image, "tags": tags})
    return stations


def _read_json(data):
    """RadioDroid / radio-browser exports, and our own backups."""
    if isinstance(data, dict):
        if storage.is_backup_format(data.get("format")):
            rows = data.get("stations") or []
        else:
            rows = data.get("stations") or data.get("items") or []
    elif isinstance(data, list):
        rows = data
    else:
        raise PlaylistError("Unrecognized JSON structure.")

    stations = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = (row.get("url_resolved") or row.get("url")
               or row.get("streamUrl") or row.get("stream_url") or "")
        name = (row.get("custom_name") or row.get("name")
                or row.get("title") or url)
        url = url or row.get("urlResolved") or row.get("url-resolved") or ""
        favicon = (row.get("favicon") or row.get("imageUrl")
                   or row.get("favicon-url") or "")
        tags = (row.get("tags") or row.get("genre") or row.get("genres")
                or row.get("category") or "")
        # radio-browser's id (RadioDroid, Tuner); Tuner 1 called it "id".
        rb = row.get("stationuuid") or row.get("id") or ""
        rb = rb if isinstance(rb, str) and _UUID_RE.match(rb) else ""
        if _is_url(url):
            stations.append({"name": name, "url": url, "favicon": favicon,
                             "tags": tags, "rb_uuid": rb,
                             "homepage": row.get("homepage") or ""})
    return stations


def _read_goodvibes(text):
    """Goodvibes stations.xml.

        <Stations>
          <Station>
            <uri>https://example.com/stream</uri>
            <name>Example FM</name>
            <insecure>true</insecure>     <- optional
          </Station>
        </Stations>

    <insecure> means "don't verify this station's TLS certificate".
    We carry the flag through so an export back to Goodvibes is
    lossless, and so those stations keep working here.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise PlaylistError(f"Malformed Goodvibes file: {exc}") from exc

    stations = []
    for node in root.iter():
        if _strip_ns(node.tag) != "station":
            continue
        fields = {}
        for child in node:
            fields[_strip_ns(child.tag)] = (child.text or "").strip()
        url = fields.get("uri", "")
        if not _is_url(url):
            continue
        stations.append({
            "name": fields.get("name") or url,
            "url": url,
            "favicon": "",
            "insecure": fields.get("insecure", "").lower() in ("true", "1", "yes"),
        })
    return stations


def _is_page_url(url):
    """True for a link to a web page rather than a stream (TuneIn etc.)."""
    host = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", (url or "").lower())
    host = host.split("/", 1)[0].split(":", 1)[0]
    return any(host == h or host.endswith("." + h) for h in _PAGE_HOSTS)


def _read_opml(text):
    """OPML outline lists.

        <opml version="2.0"><body>
          <outline text="Jazz">                       <- a folder: becomes a tag
            <outline type="audio" text="Name" URL="https://..."/>
          </outline>
        </body></opml>

    Attribute names differ between apps (URL / url / xmlUrl), so they are
    matched without regard to case. Links to TuneIn pages are not
    streams; they are counted in LAST_SKIPPED and left out.
    """
    global LAST_SKIPPED
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise PlaylistError(f"Malformed OPML file: {exc}") from exc

    stations = []
    skipped = 0

    def walk(node, folders):
        nonlocal skipped
        for child in node:
            if _strip_ns(child.tag) != "outline":
                continue
            attrs = {k.lower(): (v or "").strip() for k, v in child.attrib.items()}
            url = attrs.get("url") or attrs.get("xmlurl") or attrs.get("htmlurl") or ""
            label = attrs.get("text") or attrs.get("title") or attrs.get("name") or ""
            if _is_url(url):
                if _is_page_url(url):
                    skipped += 1
                else:
                    tags = attrs.get("category") or attrs.get("tags") or attrs.get("genre") or ""
                    if not tags and folders:
                        tags = ", ".join(folders)
                    stations.append({
                        "name": label or url, "url": url, "tags": tags,
                        "favicon": attrs.get("image") or attrs.get("logo")
                                   or attrs.get("imageurl") or "",
                    })
            walk(child, folders + [label] if label and not _is_url(url) else folders)

    for body in root.iter():
        if _strip_ns(body.tag) == "body":
            walk(body, [])
            break
    LAST_SKIPPED = skipped
    return stations


def _read_rhythmbox(text):
    """Rhythmbox's rhythmdb.xml: only its internet radio entries.

        <entry type="iradio">
          <title>Name</title><genre>Jazz</genre><location>https://...</location>
        </entry>

    The file also lists every song and podcast, which are skipped.
    """
    stations = []
    try:
        for _event, node in ET.iterparse(io.StringIO(text), events=("end",)):
            if _strip_ns(node.tag) != "entry":
                continue
            if node.get("type") == "iradio":
                fields = {_strip_ns(c.tag): (c.text or "").strip() for c in node}
                url = fields.get("location", "")
                genre = fields.get("genre", "")
                if _is_url(url):
                    stations.append({
                        "name": fields.get("title") or url, "url": url,
                        "favicon": "",
                        "tags": "" if genre.lower() == "unknown" else genre,
                    })
            node.clear()        # the file can hold tens of thousands of songs
    except ET.ParseError as exc:
        raise PlaylistError(f"Malformed Rhythmbox file: {exc}") from exc
    return stations


def sniff_format(text):
    head = text.lstrip()[:400].lower()
    # PLS must be checked before JSON: "[playlist]" also starts with "[",
    # which would otherwise look like a JSON array.
    if "[playlist]" in head:
        return "pls"
    if head.startswith("{") or head.startswith("["):
        return "radiodroid"
    if "<gnome_radio" in head:
        return "gnomeradio"
    # Goodvibes before XSPF: both are XML, but Goodvibes uses <Stations>.
    if "<stations" in head:
        return "goodvibes"
    if "<opml" in head:
        return "opml"
    if "<rhythmdb" in head:
        return "rhythmbox"
    if "<playlist" in head or "xspf" in head:
        return "xspf"
    if head.startswith("#extm3u") or "#extinf" in head:
        return "m3u"
    return "m3u"  # bare URL-per-line lists are valid M3U


def read_file(path):
    """Any supported playlist file -> (format_name, [station dicts]).

    A station from an app that only kept its radio-browser id (Gradio, a
    Shortwave station never opened) comes back with no "url" and an
    "rb_uuid"/"rb_id": radioapps.fill_in() looks those up, off the GUI
    thread.
    """
    import radioapps
    global LAST_SKIPPED
    LAST_SKIPPED = 0
    if radioapps.is_sqlite(path):
        try:
            fmt, stations = radioapps.read_sqlite(path)
        except radioapps.AppFileError as exc:
            raise PlaylistError(str(exc)) from exc
        if not stations:
            raise PlaylistError(f"{FORMATS[fmt]} has no stations in it yet.")
        return fmt, stations
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as exc:
        raise PlaylistError(f"Couldn't open that file: {exc}") from exc

    if not text.strip():
        raise PlaylistError("That file is empty.")

    fmt = sniff_format(text)
    if radioapps.is_clementine_conf(text):
        fmt = "clementine"
        stations = radioapps.read_clementine(path)
        if not stations:
            raise PlaylistError("Clementine's “Your radio streams” is empty.")
        return fmt, stations
    if fmt == "gnomeradio":
        try:
            stations = radioapps.read_gnome_radio(text)
        except radioapps.AppFileError as exc:
            raise PlaylistError(str(exc)) from exc
    elif fmt == "radiodroid":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PlaylistError(f"Invalid JSON: {exc}") from exc
        if isinstance(data, dict) and storage.is_backup_format(data.get("format")):
            fmt = "streamdeck"
        stations = _read_json(data)
    elif fmt == "pls":
        stations = _read_pls(text)
    elif fmt == "goodvibes":
        stations = _read_goodvibes(text)
    elif fmt == "xspf":
        stations = _read_xspf(text)
    elif fmt == "opml":
        stations = _read_opml(text)
    elif fmt == "rhythmbox":
        stations = _read_rhythmbox(text)
    else:
        stations = _read_m3u(text)

    if not stations and LAST_SKIPPED:
        raise PlaylistError(
            "That file only has links to web pages (like TuneIn), not "
            "streams. Open the station's page, copy its stream address and "
            "use Add Frequency… instead.")
    if not stations:
        raise PlaylistError(
            "No stream URLs found in that file. If it came from an app "
            "that isn't supported yet, send the file along and it can be "
            "added."
        )
    return fmt, stations


# -- resolving a playlist URL to the stream inside it -----------------

_RESOLVE_CACHE = {}


def looks_like_playlist(url):
    """True for URLs that point at a playlist FILE rather than audio.

    Deliberately excludes .m3u8, which is an HLS manifest - a streaming
    format players handle natively. Fetching and unwrapping one of those
    would break HLS rather than fix it.
    """
    path = (url or "").split("?", 1)[0].split("#", 1)[0].lower()
    return path.endswith(".pls") or path.endswith(".m3u")


def _first_url_in(text):
    if "#EXT-X-" in text:
        # An HLS manifest that happens to be named .m3u. Leave it alone:
        # the player understands it and the segment URLs inside are not
        # something to play directly.
        return ""
    fmt = sniff_format(text)
    entries = _read_pls(text) if fmt == "pls" else _read_m3u(text)
    for entry in entries:
        candidate = (entry.get("url") or "").strip()
        if _is_url(candidate):
            return candidate
    # Fall back to the first bare URL on any line.
    for line in text.splitlines():
        line = line.strip()
        if _is_url(line):
            return line
    return ""


def resolve_stream_url(url, timeout=8, session=None):
    """The actual audio URL behind a .pls or .m3u, or `url` unchanged.

    A .pls is a text file listing stream URLs, not audio. mpv resolves
    them transparently during playback, which is why stations added from
    a .pls play perfectly - but anything that opens the URL directly
    gets a few hundred bytes of INI text instead of audio. On the
    desktop that surfaced as "captured audio was empty" when
    identifying a song; on Android, where ExoPlayer has no playlist
    support at all, the station simply appeared to be down.

    Fails open: any error returns the original URL, so a station that
    was working never stops working because a resolve attempt failed.
    """
    if not looks_like_playlist(url):
        return url
    if url in _RESOLVE_CACHE:
        return _RESOLVE_CACHE[url]

    resolved = url
    try:
        import requests
        getter = session.get if session is not None else requests.get
        response = getter(url, timeout=timeout)
        response.raise_for_status()
        # Playlists are tiny; anything huge is not one.
        found = _first_url_in(response.text[:64_000])
        if found:
            resolved = found
    except Exception:
        pass

    _RESOLVE_CACHE[url] = resolved
    return resolved


def _clean_tags(value):
    """Whatever the file called them, as a tidy list.

    Exports write tags as a comma-separated string, a list, or a
    "group-title" — all three mean the same thing, and all three were
    being ignored.
    """
    if not value:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,;|]", value)
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        return []
    out = []
    for part in parts:
        tag = str(part).strip().strip("#").lower()
        if tag and tag not in out:
            out.append(tag)
    return out[:12]                     # a sane ceiling for one station


def to_station(entry):
    """Importer output -> a full station record."""
    return dict(
        storage.STATION_DEFAULTS,
        id=str(uuid.uuid4()),
        name=entry.get("name") or entry.get("url"),
        url=entry["url"],
        favicon=entry.get("favicon", ""),
        insecure=bool(entry.get("insecure")),
        rb_uuid=entry.get("rb_uuid") or "",
        homepage=entry.get("homepage") or "",
        # Tags come across.
        #
        # This built a station from scratch and never looked at them, so
        # three hundred stations arrived with their genres stripped —
        # which is most of what makes a list of that size usable.
        tags=_clean_tags(entry.get("tags")),
    )


# ----------------------------------------------------------------------
# Writers
# ----------------------------------------------------------------------
def _name_of(station):
    return station.get("custom_name") or station.get("name") or station.get("url")


def _write_m3u(stations):
    out = io.StringIO()
    out.write("#EXTM3U\n")
    for station in stations:
        favicon = station.get("favicon") or ""
        logo = f' tvg-logo="{favicon}"' if favicon else ""
        # Tags go out as group-title, which is what reads them back in —
        # exporting a tagged library and importing it elsewhere should
        # not quietly lose the tagging.
        tags = ", ".join(station.get("tags") or [])
        group = f' group-title="{tags}"' if tags else ""
        out.write(f"#EXTINF:-1{logo}{group},{_name_of(station)}\n"
                  f"{station['url']}\n")
    return out.getvalue()


def _write_pls(stations):
    out = io.StringIO()
    out.write("[playlist]\n")
    for i, station in enumerate(stations, start=1):
        out.write(f"File{i}={station['url']}\n")
        out.write(f"Title{i}={_name_of(station)}\n")
        out.write(f"Length{i}=-1\n")
    out.write(f"NumberOfEntries={len(stations)}\nVersion=2\n")
    return out.getvalue()


def _xml_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_XSPF_TAGS_REL = "http://frequencies.invalid/ns/tags"


def _write_xspf(stations):
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write('<playlist version="1" xmlns="http://xspf.org/ns/0/">\n')
    out.write("  <title>Frequencies</title>\n  <trackList>\n")
    for station in stations:
        out.write("    <track>\n")
        out.write(f"      <title>{_xml_escape(_name_of(station))}</title>\n")
        out.write(f"      <location>{_xml_escape(station['url'])}</location>\n")
        if station.get("favicon"):
            out.write(f"      <image>{_xml_escape(station['favicon'])}</image>\n")
        tags = ", ".join(station.get("tags") or [])
        if tags:
            out.write(f'      <meta rel="{_XSPF_TAGS_REL}">{_xml_escape(tags)}</meta>\n')
        out.write("    </track>\n")
    out.write("  </trackList>\n</playlist>\n")
    return out.getvalue()


def _write_opml(stations):
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write('<opml version="2.0">\n  <head><title>Frequencies</title></head>\n  <body>\n')
    for station in stations:
        attrs = [("type", "audio"), ("text", _name_of(station)),
                 ("URL", station["url"])]
        tags = ", ".join(station.get("tags") or [])
        if tags:
            attrs.append(("category", tags))
        if station.get("favicon"):
            attrs.append(("image", station["favicon"]))
        body = " ".join(f'{k}="{_xml_escape(v)}"' for k, v in attrs)
        out.write(f"    <outline {body}/>\n")
    out.write("  </body>\n</opml>\n")
    return out.getvalue()


def _write_goodvibes(stations):
    out = io.StringIO()
    out.write("<Stations>\n")
    for station in stations:
        out.write("  <Station>\n")
        out.write(f"    <uri>{_xml_escape(station['url'])}</uri>\n")
        out.write(f"    <name>{_xml_escape(_name_of(station))}</name>\n")
        if station.get("insecure"):
            out.write("    <insecure>true</insecure>\n")
        out.write("  </Station>\n")
    out.write("</Stations>\n")
    return out.getvalue()


def _write_radiodroid(stations):
    rows = [{
        "name": _name_of(station),
        "url": station["url"],
        "url_resolved": station["url"],
        "favicon": station.get("favicon", ""),
        "tags": ",".join(station.get("tags") or []),
        "stationuuid": station.get("rb_uuid", ""),
    } for station in stations]
    return json.dumps(rows, indent=2, ensure_ascii=False)


def write_file(path, stations, fmt):
    """Write the chosen stations out in another app's format."""
    if fmt == "streamdeck":
        storage.export_backup(path, list(stations),
                              storage.load_settings(remember=False), {})
        return
    writer = {
        "goodvibes": _write_goodvibes,
        "m3u": _write_m3u,
        "pls": _write_pls,
        "xspf": _write_xspf,
        "opml": _write_opml,
        "radiodroid": _write_radiodroid,
    }.get(fmt)
    if writer is None:
        raise PlaylistError(f"Don't know how to write '{fmt}' files.")
    text = writer(stations)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as exc:
        raise PlaylistError(f"Couldn't write that file: {exc}") from exc


def default_extension(fmt):
    return {
        "goodvibes": ".xml", "m3u": ".m3u", "pls": ".pls", "xspf": ".xspf",
        "opml": ".opml", "radiodroid": ".json", "streamdeck": ".json",
    }.get(fmt, ".txt")


# ----------------------------------------------------------------------
# Finding other apps' station lists automatically
# ----------------------------------------------------------------------
# Rather than making the user hunt for a config file, look in the places
# these apps actually keep them. Each entry is (app name, format, path
# relative to $HOME or $XDG_CONFIG_HOME). Globs are allowed.
#
# Only apps whose format has a verified reader above are listed. If an
# app you use isn't here, its config file can be added once we know what
# it looks like - the generic M3U/PLS/XSPF/JSON readers already cover a
# lot of them.
# Goodvibes, Rhythmbox, Clementine, Shortwave, Gradio, MusicPod, Tuner,
# Strawberry and GNOME Radio: their places are in radioapps.APPS (build
# 113), which "Send to" uses too.
KNOWN_APPS = [
    ("Audacious", "m3u", "~/.config/audacious/playlists/*.audpl"),
    ("VLC", "xspf", "~/.local/share/vlc/ml.xspf"),
    ("RadioDroid backup", "radiodroid", "~/Downloads/*radiodroid*.json"),
    ("Frequencies", "streamdeck", "~/*frequencies-backup*.json"),
    ("StreamDeck Radio", "streamdeck", "~/*streamdeck-radio-backup*.json"),
]


def discover_app_configs():
    """Look for other apps' station lists in their usual locations.

    Returns a list of (app_name, path, station_count). Files that exist
    but can't be read are skipped silently - this is a convenience
    scan, not a diagnostic.
    """
    import glob

    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )

    import radioapps
    found = []
    seen = set()
    for key in radioapps.APPS:
        for label, path in radioapps.found_files(key):
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            try:
                _detected, stations = read_file(path)
            except Exception:               # noqa: BLE001
                continue
            if stations:
                found.append((label, path, len(stations)))
    for app_name, _fmt, pattern in KNOWN_APPS:
        expanded = pattern.replace("$XDG_CONFIG_HOME", config_home)
        expanded = os.path.expanduser(expanded)
        for path in sorted(glob.glob(expanded)):
            real = os.path.realpath(path)
            if real in seen or not os.path.isfile(real):
                continue
            seen.add(real)
            try:
                _detected, stations = read_file(path)
            except PlaylistError:
                continue
            if stations:
                found.append((app_name, path, len(stations)))
    return found
