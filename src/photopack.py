"""Frequencies — the artist photo pack, and thumbs-down reports (build 91).

The photos the developer picks for artists ("Edit this artist…") and for
talk stations are not free to share, so no build carries them (task 117).
They travel in a pack of their own that a user opts in to:

  build      tools/make_photo_pack.py writes photo-pack/ next to the
             AppImage: frequencies-photo-pack.b32 (the zip in base32,
             twice, since build 93) and photo-pack.json
             (its version, size and checksum). The developer uploads both
             to the place the pack address points at (a GitHub release).
  user       Settings > Lyrics and artists > "Download the artist photo
             pack and keep it updated". The app reads photo-pack.json a
             minute after start and once a day, downloads the zip only
             when its checksum changed, checks it, and swaps it in
             (~/.local/share/frequencies/photo_pack/). Opting out deletes
             it.
  app        storage.artist_override() puts the pack's entry between the
             shipped corrections and the user's own (the user's still
             win); storage.talk_photo_path() looks in the pack too.

The developer's bio corrections travel apart from the photos since build
95: bio-fixes.json on the same release, which every user's app fetches
a minute after start and daily, no opt-in (BioUpdate). The dev build's
"Publish my photos / bio fixes to GitHub" buttons (ghpublish.py) put both
there. A thumbs down (always on since build 95) in the artist panel files an
issue on the developer's reports repository (task 132; no Discord): the
artist's name, where the bio came from and its first 200 characters.
Nothing about the user. Since build 97 (task 133) the app files it itself
with a token limited to Issues on that one repository (report_token.json,
shipped by tools/ship_report_token.py); no token or a failed send falls
back to build 93's pre-filled issue in the browser. One per artist a day.

Addresses: a dev build's own `~/.config/frequencies/photo_pack.json`
(Settings fields) first, else the shipped `src/photo_pack.json`, which
tools/make_photo_pack.py writes at build time. Neither is a
secret; a build without one says so in Settings.
"""

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import zipfile

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from jobs import Job

ADDRESS_FILE = "photo_pack.json"
PACK_DIR = "photo_pack"
PACK_INDEX = "pack.json"            # inside the zip and the unpacked folder
INSTALLED = "installed.json"        # what was unpacked, and from which zip
REPORT_TOKEN_FILE = "report_token.json"   # dev: Settings; shipped: src/
SETTING = "photo_pack"              # the opt-in (settings.json)
AGENT = "Frequencies (radio app)"

CHECK_AFTER_MS = 60 * 1000
CHECK_EVERY_MS = 24 * 60 * 60 * 1000
RETRY_MS = 60 * 60 * 1000           # after a failure


def _log(text):
    try:
        import crashlog
        crashlog.write("[pack] " + text)
    except Exception:                           # noqa: BLE001
        pass


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# -- addresses ----------------------------------------------------------------

def _config_dir():
    import storage
    return os.path.dirname(storage.settings_path())


def dev_address_path():
    return os.path.join(_config_dir(), ADDRESS_FILE)


def shipped_address_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ADDRESS_FILE)


def _address(name):
    import devmode
    if devmode.is_dev():
        own = str(_read(dev_address_path()).get(name) or "").strip()
        if own:
            return own
    return str(_read(shipped_address_path()).get(name) or "").strip()


def pack_address():
    """Where photo-pack.json is, or ""."""
    return _address("pack")


def save_addresses(pack=None, reports=None, art=None):
    """From Settings (dev builds). None leaves a field as it is. art: the
    picture service for QR codes (build 128, artlink.py)."""
    data = _read(dev_address_path())
    for name, value in (("pack", pack), ("reports", reports), ("art", art)):
        if value is None:
            continue
        value = value.strip()
        if value:
            data[name] = value
        else:
            data.pop(name, None)
    path = dev_address_path()
    if not data:
        if os.path.exists(path):
            os.remove(path)
        return
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(path + ".tmp", path)


# -- the unpacked pack --------------------------------------------------------

def pack_dir():
    import appdirs
    base = appdirs.data_base()
    return os.path.join(base, "frequencies", PACK_DIR)


_cache = {"stamp": None, "artists": {}}
_cache_lock = threading.Lock()


def _index():
    """The pack's artist entries, read again only when the file changes."""
    path = os.path.join(pack_dir(), PACK_INDEX)
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        stamp = None
    with _cache_lock:
        if stamp != _cache["stamp"]:
            data = _read(path) if stamp else {}
            artists = data.get("artists") if isinstance(
                data.get("artists"), dict) else {}
            _cache["artists"] = {str(k).strip().lower(): v
                                 for k, v in artists.items()
                                 if isinstance(v, dict)}
            _cache["stamp"] = stamp
        return _cache["artists"]


def artist_entry(key):
    """The pack's entry for this artist (lowercase key), photo as a full
    path on this machine; {} when the pack has none or isn't here."""
    entry = _index().get((key or "").strip().lower())
    if not entry:
        return {}
    entry = dict(entry)
    photo = (entry.get("photo") or "").strip()
    if photo and not photo.startswith(("http://", "https://")):
        path = _inside(photo)
        if path and os.path.exists(path):
            entry["photo"] = path
        else:
            entry.pop("photo", None)
    return entry


def pack_names():
    """The pack's artist entries by lowercase name ({} with no pack)."""
    return _index()


def bio_names():
    """The developer's bio fixes by lowercase name."""
    return _bios()["artists"]


_explained = set()


def explain_photo(settings, artist, shown_photo=""):
    """One "[photos]" line per artist a session (task 190): which pack
    photo a credit got when it isn't the exact name, or why it got none.
    `shown_photo` is the file the panel is about to show (or "")."""
    import storage
    artist = " ".join((artist or "").split())
    if not artist or artist.lower() in _explained:
        return
    _explained.add(artist.lower())
    key, how = storage.find_artist(_index(), artist)
    entry = artist_entry(key) if key else {}
    own_key, _own_how = storage.find_artist(storage.load_artist_overrides(),
                                            artist)
    own = (storage.load_artist_overrides().get(own_key) or {}).get("photo")
    # Corrections copied from another computer, or a deleted file
    # (task 194): it used to hide the pack's photo without a word.
    lost = own if storage.missing_photo(own) else ""
    if lost:
        own = ""
        lost = (f"your correction's photo isn't on this computer "
                f"({lost})")
    import devmode
    if entry.get("photo"):
        if lost:
            _photos(f"'{artist}': {lost}; the pack's photo is shown instead")
        elif how:
            _photos(f"'{artist}': the pack's photo of '{key}' ({how})")
        return
    if own:
        if devmode.is_dev() and installed():
            _photos(f"'{artist}': your own photo is shown; the photo pack on "
                    f"this computer (made {installed().get('made') or '?'}) "
                    f"has none for it - Publish my photos so users get it")
        return
    if not pack_address():
        why = "this build has no photo pack address"
    elif not (settings or {}).get(SETTING):
        why = "the photo pack is off (Settings > Lyrics and artists)"
    elif not installed():
        why = "the photo pack isn't downloaded yet"
    elif key:
        why = (f"the pack lists '{key}' but its photo file is missing "
               f"(made {installed().get('made') or '?'})")
    else:
        have = installed()
        why = (f"not in the photo pack (made {have.get('made') or '?'}, "
               f"{have.get('artists', 0)} artists)")
    _photos(f"'{artist}': no pack photo - {why}"
            + (f"; {lost}" if lost else "")
            + ("; a found photo is shown instead" if shown_photo else "")
            # The dev build: a found photo isn't one of yours (task 194).
            + ("; no photo of yours for it here either (Edit this "
               "artist… > Choose…)" if devmode.is_dev() else ""))


def _photos(text):
    try:
        import crashlog
        crashlog.write("[photos] " + text)
    except Exception:                           # noqa: BLE001
        pass


def talk_photo(base):
    """A talk station's photo from the pack, or ""."""
    path = _inside("talk_photos/" + os.path.basename(base or ""))
    return path if path and os.path.exists(path) else ""


def _inside(relative):
    """`relative` under the pack folder, or "" if it would leave it."""
    root = os.path.realpath(pack_dir())
    path = os.path.realpath(os.path.join(root, relative))
    return path if path.startswith(root + os.sep) else ""


# -- the developer's bio fixes (build 95) ---------------------------------------
#
# bio-fixes.json sits on the same release as the photo pack. Every user's
# app fetches it (no opt-in: it is text, and the user asked for it to
# reach everyone automatically) and keeps it beside the pack folder, not in
# it, so opting out of the photos keeps the fixes.

BIO_FILE = "bio_fixes.json"


def bio_path():
    return os.path.join(os.path.dirname(pack_dir()), BIO_FILE)


_bio_cache = {"stamp": None, "artists": {}, "info": {}}


def _bios():
    try:
        stamp = os.stat(bio_path()).st_mtime_ns
    except OSError:
        stamp = None
    with _cache_lock:
        if stamp != _bio_cache["stamp"]:
            data = _read(bio_path()) if stamp else {}
            artists = data.get("artists") if isinstance(
                data.get("artists"), dict) else {}
            _bio_cache["artists"] = {str(k).strip().lower(): v
                                     for k, v in artists.items()
                                     if isinstance(v, dict)}
            _bio_cache["info"] = {"sha256": data.get("sha256", ""),
                                  "made": data.get("made", ""),
                                  "artists": len(_bio_cache["artists"])}
            _bio_cache["stamp"] = stamp
        return _bio_cache


def bio_entry(key):
    """The developer's published text fix for this artist, or {}."""
    entry = _bios()["artists"].get((key or "").strip().lower())
    return {k: v for k, v in (entry or {}).items()
            if isinstance(v, str) and k != "photo"}


def bio_info():
    """{"sha256", "made", "artists"} of the fixes on this machine."""
    return dict(_bios()["info"]) if os.path.exists(bio_path()) else {}


def bio_address():
    address = pack_address()
    return urllib.parse.urljoin(address, "bio-fixes.json") if address else ""


class BioUpdate(Job):
    """Fetch bio-fixes.json; keep it if it changed. done(state, text):
    "current", "updated", "none" (nothing published yet) or "failed"."""

    done = pyqtSignal(str, str)

    def __init__(self, address, parent=None):
        super().__init__(parent)
        self.address = address

    def work(self):
        try:
            state, text = self._update()
        except Exception as exc:                # noqa: BLE001
            _log(f"bio fixes failed: {exc}")
            state, text = "failed", _why(exc)
        _log(f"bio fixes {state}: {text}")
        self.send(self.done, state, text)

    def _update(self):
        import requests
        answer = requests.get(self.address, timeout=20,
                              headers={"User-Agent": AGENT})
        if answer.status_code == 404:
            return "none", "none published yet"
        answer.raise_for_status()
        data = answer.json()
        if not isinstance(data, dict) or not isinstance(
                data.get("artists"), dict):
            return "failed", "the bio fixes file isn't one"
        count = len(data["artists"])
        if data.get("sha256") and data.get("sha256") == bio_info().get(
                "sha256"):
            return "current", f"{count} artists"
        path = bio_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        os.replace(path + ".tmp", path)
        return "updated", f"{count} artists, made {data.get('made', '?')}"


# -- the developer's visualizer list (build 106, task 142) -------------------

VIS_NAME = "visualizer-list.json"
VIS_FIRST_MS = 5 * 1000


def visualizer_list_bytes():
    """The dev build's turned-off presets, by file name (the preset folder
    is somewhere else on every PC), as the file to publish."""
    import storage
    settings = _read(storage.settings_path())
    names = sorted({os.path.basename(str(p)) for p in
                    settings.get("visualizer_disabled") or ()
                    if str(p).strip()})
    data = {"presets": names, "made": time.strftime("%Y-%m-%d %H:%M")}
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"), len(names)


def visualizer_address():
    address = pack_address()
    return urllib.parse.urljoin(address, VIS_NAME) if address else ""


class VisualizerListFetch(Job):
    """Fetch visualizer-list.json once. done(names, text): names is the
    list, or None when it didn't come (offline, nothing published)."""

    done = pyqtSignal(object, str)

    def __init__(self, address, parent=None):
        super().__init__(parent)
        self.address = address

    def work(self):
        names, text = None, ""
        try:
            import requests
            answer = requests.get(self.address, timeout=20,
                                  headers={"User-Agent": AGENT})
            if answer.status_code == 404:
                text = "none published yet"
            else:
                answer.raise_for_status()
                data = answer.json()
                found = data.get("presets") if isinstance(data, dict) \
                    else None
                if isinstance(found, list):
                    names = [str(n) for n in found]
                    text = (f"{len(names)} presets turned off, made "
                            f"{data.get('made') or '?'}")
                else:
                    text = "the file isn't a visualizer list"
        except Exception as exc:                # noqa: BLE001
            text = _why(exc)
        _log(f"visualizer list: {'got it' if names is not None else 'not now'}"
             f" - {text}")
        self.send(self.done, names, text)


def installed():
    """What is unpacked: {"sha256", "made", "photos", "artists"} or {}."""
    return _read(os.path.join(pack_dir(), INSTALLED))


def remove():
    """Delete the pack (opting out). True if there was one."""
    folder = pack_dir()
    if not os.path.isdir(folder):
        return False
    shutil.rmtree(folder, ignore_errors=True)
    with _cache_lock:
        _cache["stamp"] = None
        _cache["artists"] = {}
    _log("pack removed (opted out)")
    return True


# -- checking and downloading -------------------------------------------------

class PackUpdate(Job):
    """Read photo-pack.json; download, check and unpack the zip if it
    changed. Answers with progress(percent) while downloading and
    done(state, text) at the end: state is "current", "updated" or
    "failed"."""

    progress = pyqtSignal(int)
    unpacking = pyqtSignal()
    done = pyqtSignal(str, str)

    def __init__(self, address, parent=None):
        super().__init__(parent)
        self.address = address
        self._cancel = threading.Event()

    def stop(self):
        self._cancel.set()

    def work(self):
        try:
            state, text = self._update()
        except Exception as exc:                # noqa: BLE001
            _log(f"failed: {exc}")
            state, text = "failed", _why(exc)
        _log(f"{state}: {text}")
        self.send(self.done, state, text)

    def _update(self):
        import requests
        _log(f"checking {self.address}")
        answer = requests.get(self.address, timeout=20,
                              headers={"User-Agent": AGENT})
        answer.raise_for_status()
        manifest = answer.json()
        if not isinstance(manifest, dict) or not manifest.get("sha256"):
            return "failed", "the pack address doesn't point at a pack"
        have = installed()
        if have.get("sha256") == manifest["sha256"]:
            return "current", _summary(have)

        url = urllib.parse.urljoin(
            self.address, manifest.get("url") or manifest.get("file")
            or "frequencies-photo-pack.b32")
        size = int(manifest.get("size") or 0)
        _log(f"update available ({manifest.get('made', '?')}, "
             f"{size // 1024} KB); downloading {url}")
        self.send(self.progress, 0)
        folder = pack_dir()
        os.makedirs(os.path.dirname(folder), exist_ok=True)
        part = folder + ".download"
        digest = hashlib.sha256()
        got = 0
        last = -1
        with requests.get(url, timeout=30, stream=True,
                          headers={"User-Agent": AGENT}) as response:
            response.raise_for_status()
            size = size or int(response.headers.get("Content-Length") or 0)
            with open(part, "wb") as handle:
                for chunk in response.iter_content(64 * 1024):
                    if self._cancel.is_set():
                        raise RuntimeError("stopped (the app is closing)")
                    handle.write(chunk)
                    digest.update(chunk)
                    got += len(chunk)
                    percent = min(99, got * 100 // size) if size else 0
                    if percent != last:
                        last = percent
                        self.send(self.progress, percent)
        if digest.hexdigest() != manifest["sha256"]:
            os.remove(part)
            return "failed", ("the download didn't match the pack's "
                              "checksum (an old or broken upload?)")
        if manifest.get("encoding") == "base32x2":
            # Stored as base32 twice on GitHub (build 93): undone here,
            # so what is unpacked on this machine is the plain zip.
            self.send(self.unpacking)
            plain = folder + ".zip"
            _decode_base32_twice(part, plain, self._cancel)
            os.remove(part)
            part = plain
        info = _unpack(part, folder, manifest)
        os.remove(part)
        return "updated", _summary(info)


class ManifestFetch(Job):
    """Read the published photo-pack.json (a few hundred bytes), for the
    dev build's count next to Publish my photos (task 194).
    done(manifest or None, why)."""

    done = pyqtSignal(object, str)

    def __init__(self, address, parent=None):
        super().__init__(parent)
        self.address = address

    def work(self):
        try:
            import requests
            answer = requests.get(self.address, timeout=20,
                                  headers={"User-Agent": AGENT})
            if answer.status_code == 404:
                self.send(self.done, {}, "")
                return
            answer.raise_for_status()
            manifest = answer.json()
            if not isinstance(manifest, dict) or not manifest.get("sha256"):
                raise ValueError("not a pack")
        except Exception as exc:                # noqa: BLE001
            self.send(self.done, None, _why(exc))
            return
        self.send(self.done, manifest, "")


def photo_count(manifest=None, why=""):
    """"N artists have photos here, the pack has M", and which of yours
    aren't published yet (task 194). `manifest` is the published
    photo-pack.json ({} when none is published, None when it couldn't be
    read: `why`)."""
    import storage
    dev = storage.load_artist_overrides()
    here, lost = {}, set()
    for key, entry in dev.items():
        photo = entry.get("photo")
        if not isinstance(photo, str) or not photo.strip():
            continue
        if storage.missing_photo(photo):
            lost.add(key)
        else:
            here[key] = (entry.get("name") or key).strip()
    line = (f"{len(here)} artist{'' if len(here) == 1 else 's'} "
            f"{'has a photo' if len(here) == 1 else 'have photos'} here")
    if lost:
        line += f" ({len(lost)} more whose photo file is missing)"
    if manifest is None:
        return line + (f"; couldn't check the published pack ({why})."
                       if why else ".")
    if not manifest:
        return line + "; no photo pack is published yet."
    line += (f", the published pack has {manifest.get('artists', '?')} "
             f"(made {manifest.get('made') or '?'}).")
    names = manifest.get("names")
    if not isinstance(names, list):
        # Packs from before build 135 don't list their artists; the copy
        # downloaded on this computer does, when it's the same pack.
        if installed().get("sha256") != manifest.get("sha256"):
            return line + (" Publish my photos once to see which of "
                           "yours are new.")
        names = list(pack_names())
    published = {str(n).strip().lower() for n in names}
    new = sorted(here[k] for k in here if k not in published)
    gone = len(published - set(here) - lost)
    if new:
        shown = ", ".join(new[:8]) + (f" and {len(new) - 8} more"
                                      if len(new) > 8 else "")
        line += f" Not published yet: {shown}."
    else:
        line += " All of yours are published."
    if gone:
        line += (f" {gone} in the pack aren't in your corrections here, "
                 f"so publishing from here would take them out.")
    return line


def _decode_base32_chunks(chunks):
    """Decode base32 piece by piece: whole 8-character groups at a time,
    so a big pack never has to sit in memory at once."""
    rest = b""
    for chunk in chunks:
        rest += chunk.translate(None, b" \r\n\t")
        whole = len(rest) - len(rest) % 8
        if whole:
            yield _b32(rest[:whole])
            rest = rest[whole:]
    if rest:
        raise ValueError("the pack's encoding is cut short")


# base32's letters as the digits int() reads in base 32. Python's own
# base64.b32decode is written in Python: ~0.6 s a megabyte, holding the
# interpreter; int() does the same in C.
_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_B32 = bytes.maketrans(_ALPHABET, b"0123456789abcdefghijklmnopqrstuv")


def _b32(block):
    import base64
    if b"=" in block or len(block) < 8:
        return base64.b32decode(block)          # the last few groups
    if block.translate(None, _ALPHABET):
        raise ValueError("the pack's encoding has stray characters")
    return int(block.translate(_B32), 32).to_bytes(len(block) * 5 // 8,
                                                   "big")


def _decode_base32_twice(source, target, cancel=None):
    def read():
        with open(source, "rb") as handle:
            while True:
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("stopped (the app is closing)")
                block = handle.read(1024 * 1024)
                if not block:
                    return
                yield block
    with open(target, "wb") as out:
        for piece in _decode_base32_chunks(_decode_base32_chunks(read())):
            out.write(piece)


def _unpack(zip_path, folder, manifest):
    """Unpack beside the old pack, then swap the two folders."""
    fresh = folder + ".new"
    shutil.rmtree(fresh, ignore_errors=True)
    os.makedirs(fresh)
    root = os.path.realpath(fresh)
    photos = 0
    # UTF-8 names: the AppImage's Python may lack cp437 (task 145).
    with zipfile.ZipFile(zip_path, metadata_encoding="utf-8") as pack:
        for member in pack.infolist():
            if member.is_dir():
                continue
            target = os.path.realpath(os.path.join(fresh, member.filename))
            if not target.startswith(root + os.sep):
                continue                        # never outside the folder
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with pack.open(member) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            if not member.filename.endswith(".json"):
                photos += 1
    index = _read(os.path.join(fresh, PACK_INDEX))
    info = {"sha256": manifest["sha256"],
            "made": manifest.get("made", ""),
            "photos": photos,
            "artists": len(index.get("artists") or {}),
            "installed": time.strftime("%Y-%m-%d %H:%M")}
    with open(os.path.join(fresh, INSTALLED), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    old = folder + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(folder):
        os.rename(folder, old)
    os.rename(fresh, folder)
    shutil.rmtree(old, ignore_errors=True)
    with _cache_lock:
        _cache["stamp"] = None
    return info


def _summary(info):
    made = info.get("made") or "?"
    return (f"{info.get('photos', 0)} photos, "
            f"{info.get('artists', 0)} artists, made {made}")


def _why(exc):
    """A failure in a few plain words (the full one goes to the log)."""
    try:
        import requests
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            code = exc.response.status_code
            return ("the pack isn't at its address (404)" if code == 404
                    else f"the server answered {code}")
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return "couldn't reach the pack's address (offline?)"
        if isinstance(exc, ValueError):
            return "the pack address doesn't point at a pack"
    except ImportError:
        pass
    text = str(exc).strip() or type(exc).__name__
    return text if len(text) < 160 else text[:157] + "…"


class Keeper(QObject):
    """Keeps the pack up to date while the app runs: one check a minute
    after start, then one a day (an hour after a failure). `status` is
    the line Settings shows; `changed` means a new pack was unpacked or
    the pack was removed (the artist panel redraws)."""

    status = pyqtSignal(str)
    bio_status = pyqtSignal(str)
    changed = pyqtSignal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.text = ""
        self.bio_text = ""
        self._job = None
        self._bio_job = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.check_now)
        # The bio fixes, for everyone (build 95): their own timer, so a
        # user who never opts in to the photos still gets them.
        self._bio_timer = QTimer(self)
        self._bio_timer.setSingleShot(True)
        self._bio_timer.timeout.connect(self.check_bios)
        self._say(self._resting())
        self._say_bios(self._bio_resting())
        if self.enabled():
            self._timer.start(CHECK_AFTER_MS)
        if pack_address():
            self._bio_timer.start(CHECK_AFTER_MS)
        # The developer's visualizer list (task 142): user builds only,
        # until it has come once. A failure waits for the next launch.
        import devmode
        self._vis_job = None
        if not devmode.is_dev() and not settings.get(
                "visualizer_list_fetched") and visualizer_address():
            QTimer.singleShot(VIS_FIRST_MS, self.fetch_visualizer_list)

    def fetch_visualizer_list(self):
        if self._vis_job is not None or self.settings.get(
                "visualizer_list_fetched"):
            return
        job = VisualizerListFetch(visualizer_address())
        job.done.connect(self._visualizer_list_came)
        self._vis_job = job
        job.start()

    def _visualizer_list_came(self, names, _text):
        if names is None:
            return
        import storage
        took = storage.take_visualizer_list(self.settings, names)
        storage.save_settings(self.settings)
        _log("visualizer list: " + (
            f"applied ({len(names)} presets off from the next visualizer "
            f"opened)" if took else "kept yours (you changed the list)"))

    def _bio_resting(self):
        if not pack_address():
            return ""
        have = bio_info()
        if have:
            return (f"Bio fixes from the developer: {have['artists']} "
                    f"artists, made {have.get('made') or '?'}.")
        return "Bio fixes from the developer: none yet."

    def _say_bios(self, text):
        self.bio_text = text
        self.bio_status.emit(text)

    def check_bios(self):
        if not pack_address():
            return
        if self._bio_job is not None and self._bio_job.isRunning():
            return
        self._bio_timer.stop()
        job = BioUpdate(bio_address())
        job.done.connect(self._bios_done)
        self._bio_job = job
        job.start()

    def _bios_done(self, state, text):
        self._bio_job = None
        if state == "failed":
            self._say_bios(f"Bio fixes: couldn't check ({text}). Trying "
                           f"again in an hour.")
            self._bio_timer.start(RETRY_MS)
            return
        self._say_bios(self._bio_resting())
        if state == "updated":
            self.changed.emit()
        self._bio_timer.start(CHECK_EVERY_MS)

    def enabled(self):
        return bool(self.settings.get(SETTING)) and bool(pack_address())

    def _resting(self):
        if not pack_address():
            return "This build has no photo pack address."
        have = installed()
        if not self.settings.get(SETTING):
            return "Off." + (" The pack is still here." if have else "")
        if have:
            return self._lines("Up to date")
        return "Not downloaded yet."

    def _lines(self, state):
        """Last updated / Up to date or Update available / Last downloaded
        (build 100, task 138)."""
        have = installed()
        return (f"Last updated: {have.get('made') or '?'}\n{state}\n"
                f"Last downloaded: {have.get('installed') or '?'}")

    def _say(self, text):
        self.text = text
        self.status.emit(text)

    def set_enabled(self, on):
        """The Settings switch (already saved)."""
        if on:
            _log("opted in")
            self.check_now()
            return
        self._timer.stop()
        if self._job is not None:
            self._job.stop()
        if remove():
            self.changed.emit()
        self._say("Off. The pack was deleted.")

    def check_now(self):
        self.check_bios()
        if not self.enabled():
            self._say(self._resting())
            return
        if self._job is not None and self._job.isRunning():
            return
        self._timer.stop()
        self._say("Checking for a new pack…")
        job = PackUpdate(pack_address())
        job.progress.connect(self._progress)
        job.unpacking.connect(lambda: self._say("Unpacking…"))
        job.done.connect(self._done)
        self._job = job
        job.start()

    def _progress(self, percent):
        if percent == 0:
            self._say(self._lines("Update available, downloading…"))
        else:
            self._say(f"Downloading… {percent}%")

    def _done(self, state, text):
        self._job = None
        if not self.settings.get(SETTING):
            # Switched off while it ran: what it unpacked goes too.
            if remove():
                self.changed.emit()
            self._say(self._resting())
            return
        if state == "failed":
            self._say(f"Failed: {text}. Trying again in an hour.")
            self._timer.start(RETRY_MS)
            return
        self._say(self._lines("Up to date"))
        if state == "updated":
            self.changed.emit()
        self._timer.start(CHECK_EVERY_MS)


_keeper = None


def keeper(settings=None):
    """The app's one Keeper (made by the window at start-up)."""
    global _keeper
    if _keeper is None and settings is not None:
        _keeper = Keeper(settings)
    return _keeper


# -- thumbs-down reports ------------------------------------------------------

def report_address():
    """The developer's GitHub repository (https://github.com/<you>/<repo>)
    that thumbs-down issues are opened on, or "". A dev build's Report
    address first, then the shipped one, else the repository the photo pack
    comes from."""
    for address in (_address("reports"), pack_address()):
        found = re.match(r"https://github\.com/([^/\s]+)/([^/\s#?]+)",
                         address or "")
        if found:
            return f"https://github.com/{found.group(1)}/{found.group(2)}"
    return ""


def _config_file(name):
    return os.path.join(_config_dir(), name)


def report_token():
    """The token that files thumbs-down issues (build 97, task 133): a
    fine-grained GitHub token limited to Issues on the reports repository,
    nothing else. A dev build's own (Settings) first, else the one the
    build shipped (src/report_token.json, written by
    tools/ship_report_token.py). Never logged, never in the project."""
    paths = [shipped_report_token_path()]
    import devmode
    if devmode.is_dev():
        paths.insert(0, _config_file(REPORT_TOKEN_FILE))
    for path in paths:
        found = str(_read(path).get("token") or "").strip()
        if found:
            return found
    return ""


def shipped_report_token_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        REPORT_TOKEN_FILE)


def dev_report_token():
    return str(_read(_config_file(REPORT_TOKEN_FILE)).get("token") or "")


def save_report_token(value):
    """From Settings (dev builds). Readable only by you."""
    value = (value or "").strip()
    path = _config_file(REPORT_TOKEN_FILE)
    if not value:
        if os.path.exists(path):
            os.remove(path)
        return
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"token": value}, handle)
    os.replace(path + ".tmp", path)
    os.chmod(path, 0o600)


WRONG_THINGS = {"bio": "Bio", "photo": "Photo", "both": "Bio and photo"}


def _issue_text(artist, source, bio, what="both", photo=""):
    """The issue's title and body. `what` is "bio", "photo" or "both"
    (build 98, task 136); `photo` says where the photo shown came from."""
    what = what if what in WRONG_THINGS else "both"
    lines = [f"Artist: {artist}",
             f"What is wrong: {WRONG_THINGS[what]}"]
    if what in ("bio", "both"):
        lines += [f"Bio from: {source or 'unknown'}",
                  f"Wrong info (first 200 characters):\n> {(bio or '')[:200]}"]
    if what in ("photo", "both"):
        lines.append(f"Photo shown: {photo or 'none shown'}")
    lines.append(f"({time.strftime('%Y-%m-%d %H:%M')})")
    return (f"Wrong artist info ({WRONG_THINGS[what].lower()}): {artist}",
            "\n".join(lines))


# -- where a shown photo came from (build 98, task 136) -----------------------
#
# Downloaded photos are cached under a hash or the artist's name, so the
# address they came from is written down beside the pack when they arrive.

ORIGINS_FILE = "photo_origins.json"
_origins_cache = {"data": None}
_origins_lock = threading.Lock()


def _origins_path():
    return os.path.join(os.path.dirname(pack_dir()), ORIGINS_FILE)


def note_photo_origin(path, url):
    """Remember that the cached file `path` was downloaded from `url`."""
    if not path or not url or not url.startswith(("http://", "https://")):
        return
    key = os.path.realpath(path)
    with _origins_lock:
        if _origins_cache["data"] is None:
            data = _read(_origins_path())
            _origins_cache["data"] = data if isinstance(data, dict) else {}
        data = _origins_cache["data"]
        if data.get(key) == url:
            return
        data.pop(key, None)
        data[key] = url
        while len(data) > 500:                  # oldest out
            data.pop(next(iter(data)))
        try:
            path = _origins_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.replace(path + ".tmp", path)
        except OSError:
            pass


def photo_link(artist, path):
    """Words for an issue: where the photo in the panel (file `path`) came
    from. A download's address, the pack's file name, the developer's
    photo link, or an honest "unknown"."""
    if not path:
        return ""
    real = os.path.realpath(path)
    with _origins_lock:
        if _origins_cache["data"] is None:
            data = _read(_origins_path())
            _origins_cache["data"] = data if isinstance(data, dict) else {}
        url = _origins_cache["data"].get(real, "")
    if url:
        return url
    try:
        import storage
        entry = storage.artist_override(artist) or {}
    except Exception:                           # noqa: BLE001
        entry = {}
    link = (entry.get("photo_url") or "").strip()
    root = os.path.realpath(pack_dir())
    if real.startswith(root + os.sep):
        name = real[len(root) + 1:]
        return f"photo pack, {name}" + (f" ({link})" if link else "")
    if link:
        return link
    return f"unknown (a local file, {os.path.basename(path)})"


def _issue_address(repo, title, body):
    query = urllib.parse.urlencode({"title": title, "body": body},
                                   quote_via=urllib.parse.quote)
    return f"{repo}/issues/new?{query}"


def _due(settings, artist):
    """One report per artist per day (task 133). Marks it as sent."""
    today = time.strftime("%Y-%m-%d")
    sent = settings.get("report_sent") or {}
    sent = {k: v for k, v in sent.items() if v == today}   # only today's
    key = artist.lower()
    settings["report_sent"] = sent
    if key in sent:
        return False
    sent[key] = today
    return True


class ReportSend(Job):
    """File one issue on the reports repository with the shipped token.
    Answers done(ok, text): the issue's address, or why it failed."""

    done = pyqtSignal(bool, str)

    def __init__(self, repo, title, body, token, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.title = title
        self.body = body
        self._token = token

    def work(self):
        try:
            ok, text = True, self._file()
        except Exception as exc:                # noqa: BLE001
            ok, text = False, self._why(exc)
        self.send(self.done, ok, text)

    def _file(self):
        import requests
        owner, name = self.repo.rsplit("/", 2)[-2:]
        answer = requests.post(
            f"https://api.github.com/repos/{owner}/{name}/issues",
            json={"title": self.title, "body": self.body},
            headers={"Authorization": "Bearer " + self._token,
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": AGENT},
            timeout=15)
        answer.raise_for_status()
        return str(answer.json().get("html_url") or "")

    @staticmethod
    def _why(exc):
        try:
            import requests
            if isinstance(exc, requests.HTTPError) \
                    and exc.response is not None:
                code = exc.response.status_code
                if code in (401, 403):
                    return ("GitHub refused the token (it needs Issues: "
                            "Read and write on the reports repository)")
                if code in (404, 410):
                    return ("GitHub can't find the reports repository (or "
                            "the token isn't for it)")
                return f"GitHub answered {code}"
            if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
                return "couldn't reach GitHub (offline?)"
        except ImportError:
            pass
        return str(exc) or exc.__class__.__name__


def report(settings, artist, source, bio, open_url, what="both", photo=""):
    """A thumbs down (always on since build 95). Since build 97 the app
    files the issue itself with the shipped token, no browser and no
    Submit; if it can't (no token, offline, refused), it falls back to
    build 93's pre-filled issue, which `open_url(address)` opens for the
    user to submit. One report per artist per day. Needs the GUI thread
    (the answer comes back there)."""
    repo = report_address()
    if not repo:
        _log(f"thumbs down for '{artist}' not reported: this build has no "
             f"report address")
        return
    if not _due(settings, artist):
        _log(f"thumbs down for '{artist}' already reported today")
        return
    title, body = _issue_text(artist, source, bio, what, photo)
    token = report_token()
    if not token:
        _log(f"thumbs down for '{artist}': no report token, opening the "
             f"browser")
        open_url(_issue_address(repo, title, body))
        return
    job = ReportSend(repo, title, body, token)

    def done(ok, text):
        if ok:
            _log(f"thumbs down for '{artist}' filed: {text}")
            return
        _log(f"thumbs down for '{artist}' not filed ({text}), opening the "
             f"browser")
        open_url(_issue_address(repo, title, body))
    job.done.connect(done)
    job.start()


def test_report(done):
    """Dev build: file a test issue now. done(ok, text) on the GUI thread."""
    repo = report_address()
    token = report_token()
    if not repo:
        done(False, "set the Report address first")
        return None
    if not token:
        done(False, "paste the Report token first")
        return None
    job = ReportSend(repo, "Test report from Frequencies",
                     "Sent by the dev build's \"Send a test report\" "
                     "button. You can close this issue.", token)
    job.done.connect(done)
    job.start()
    return job
