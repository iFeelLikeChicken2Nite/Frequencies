"""Frequencies — saved frequencies shared as one zip (build 105, task 130).

A QR code holds one station, and only one that fits in about two
kilobytes. Several at once, or a folder of your own music, go in a zip
instead: `frequencies-share.json` lists every station in the same shape
a code carries (tags, equalizer, artwork, corrections — see
storage.featured_from_station), and a local-files station can bring its
audio along under `audio/<n>/`.

Both directions are plain file work with no Qt in them, so the window
runs them on a Lookup (a zip of a music folder can take a while).
"""

import json
import os
import posixpath
import zipfile

import storage

MANIFEST = "frequencies-share.json"
FORMAT = "frequencies-share"

# Already compressed: deflating them again only costs time.
_STORED = (".mp3", ".ogg", ".opus", ".m4a", ".aac", ".flac", ".wma",
           ".webm", ".mka", ".mp4")


def shareable(station):
    """Whether a station can be shared at all. iBroadcast stations can't:
    their links are signed for one account (task 130b)."""
    station = station or {}
    return not (station.get("ibroadcast_urls")
                or station.get("kind") == "ibroadcast")


def _safe(name):
    text = "".join(c for c in (name or "") if c.isalnum() or c in " -_")
    text = text.strip()[:80].strip()
    # A folder Windows can't make (CON, NUL, COM1…; build 126).
    if text.upper() in storage._WINDOWS_RESERVED:
        text = "_" + text
    return text or "frequency"


def default_path(stations):
    """Where the zip goes: Downloads if there is one, else home, never
    over an existing file."""
    home = os.path.expanduser("~")
    folder = os.path.join(home, "Downloads")
    if not os.path.isdir(folder):
        folder = home
    if len(stations) == 1:
        base = _safe(storage.station_display_name(stations[0]))
    else:
        base = f"Frequencies - {len(stations)} frequencies"
    path = os.path.join(folder, base + ".zip")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({n}).zip")
        n += 1
    return path


def write(stations, path, with_audio=False):
    """Zip `stations` to `path`. Returns how many audio files went in."""
    entries = []
    audio_count = 0
    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for i, station in enumerate(stations):
            entry = storage.featured_from_station(station)
            if storage.is_local_station(station):
                # The folder's path means nothing on another machine.
                entry["url"] = "local://" + _safe(
                    storage.station_display_name(station))
                folder = station.get("local_path") or ""
                if with_audio and os.path.isdir(folder):
                    prefix = f"audio/{i + 1}"
                    for track in storage.scan_local_tracks(folder):
                        rel = os.path.relpath(track, folder)
                        arc = posixpath.join(prefix,
                                             *rel.split(os.sep))
                        kind = (zipfile.ZIP_STORED
                                if track.lower().endswith(_STORED)
                                else zipfile.ZIP_DEFLATED)
                        z.write(track, arc, compress_type=kind)
                        audio_count += 1
                    entry["audio_folder"] = prefix
            entries.append(entry)
        z.writestr(MANIFEST, json.dumps(
            {"format": FORMAT, "version": 1, "stations": entries},
            indent=1))
    os.replace(tmp, path)
    return audio_count


def _open(path):
    """Read a zip without the cp437 codec, which the AppImage's Python
    may not carry: plain zip names are decoded as cp437 unless told
    otherwise, and the import died with "unknown encoding: cp437" (task
    145). Our own names are ASCII or flagged UTF-8, so nothing changes
    (winampskin._open_archive does the same for skins)."""
    return zipfile.ZipFile(path, metadata_encoding="utf-8")


def is_share_zip(path):
    try:
        with _open(path) as z:
            return MANIFEST in z.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def read(path, audio_root):
    """The stations a share zip carries, ready to add.

    A local-files station that came with its audio is unpacked into a
    new folder under `audio_root` and pointed at it; one that came
    without keeps an empty `local_path` for the window to ask about.
    """
    stations = []
    with _open(path) as z:
        data = json.loads(z.read(MANIFEST).decode("utf-8"))
        if data.get("format") != FORMAT:
            raise ValueError("That zip isn't a Frequencies share.")
        names = z.namelist()
        for entry in data.get("stations") or []:
            if not isinstance(entry, dict):
                continue
            prefix = entry.pop("audio_folder", "") or ""
            station = storage.station_from_featured(entry)
            if station.get("kind") == "local":
                station["local_path"] = ""
                if prefix:
                    target = _unpack(z, names, prefix.rstrip("/") + "/",
                                     audio_root,
                                     storage.station_display_name(station))
                    if target:
                        station["local_path"] = target
                        station["url"] = "local://" + target
            stations.append(station)
    return stations


def _unpack(z, names, prefix, audio_root, name):
    """Extract every file under `prefix` into a fresh folder."""
    base = os.path.join(audio_root, _safe(name))
    target, n = base, 2
    while os.path.exists(target):
        target = f"{base} ({n})"
        n += 1
    wrote = 0
    for member in names:
        if not member.startswith(prefix) or member.endswith("/"):
            continue
        rel = posixpath.normpath(member[len(prefix):])
        # Never outside the folder, whatever the zip says.
        if rel.startswith("..") or rel.startswith("/") or ":" in rel:
            continue
        out = os.path.join(target, *rel.split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with z.open(member) as src, open(out, "wb") as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
        wrote += 1
    return target if wrote else ""


def show_in_file_browser(path):
    """Open the folder holding `path`, with the file picked where the
    file manager supports it (the freedesktop FileManager1 call)."""
    from PyQt5.QtCore import QUrl
    from PyQt5.QtGui import QDesktopServices
    try:
        from PyQt5.QtDBus import QDBusConnection, QDBusInterface
        iface = QDBusInterface("org.freedesktop.FileManager1",
                               "/org/freedesktop/FileManager1",
                               "org.freedesktop.FileManager1",
                               QDBusConnection.sessionBus())
        if iface.isValid():
            reply = iface.call("ShowItems",
                               [QUrl.fromLocalFile(path).toString()], "")
            if not reply.errorMessage():
                return
    except Exception:                           # noqa: BLE001
        pass
    QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
