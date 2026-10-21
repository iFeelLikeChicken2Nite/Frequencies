"""Frequencies — updates itself (build 125, task 177; folds in task 135).

Where builds live: the GitHub repository `<you>/frequencies-releases`,
where <you> is the owner of the photo pack's repository (the address every
build already carries). Each release is tagged `build-N` and holds the
Linux AppImage and/or the Windows installer, and the newest release holds
`latest.json`:

    {"linux":   {"build": 125, "url": …, "size": …, "sha256": …},
     "windows": {"build": 125, "url": …, "size": …, "sha256": …},
     "android": {"build": 125, "url": …, "size": …, "sha256": …}}

An OS is offered an update only when its own entry is newer than this
build (version.BUILD), so a release may carry one OS for a while. The
desktop reads only its own entry; "android" (task 184) is the Android
app's (Frequencies.apk on the same build-N release, one build counter
for the project, versionCode = N), kept as it is when the desktop
publishes.

User builds check at every launch, in the background (no switch; the
user's wish 2026-10-19: "check for updates once every session"), and
with Settings > Library and tools > Check for updates. A newer build is downloaded quietly and checked against its sha256; it is
put in place when the app really quits (main.py, after the window is
gone), never when it only hides in the tray:

  Linux    the new AppImage is downloaded next to the running one
           ($APPIMAGE) and renamed over it at quit (one atomic rename; a
           crash or kill first leaves the old one, and the download is
           used again next time).
  Windows  Frequencies-Setup.exe is downloaded to
           %LOCALAPPDATA%\\frequencies\\updates (by the app, not a browser,
           so no Mark of the Web) and started at quit, detached:
           /VERYSILENT /SUPPRESSMSGBOXES /CLOSEAPPLICATIONS
           /NORESTARTAPPLICATIONS /UPDATE. /UPDATE makes the installer
           wait for every Frequencies.exe to let go of the
           "FrequenciesRunning" mutex and skip its "launch now" step.
           Not when Windows is shutting down or signing out
           (session_ending): then at the next normal quit. If the installer
           didn't finish (Windows killed it), the next launch runs it again
           before the window opens (/RELAUNCH starts the app after it).

The next launch of the new build says "Updated to build N" once, with
the what's-new lines (whats_new.json) of the builds in between.

Dev builds never download or install: their check only says a newer
build exists. Publishing is the dev build's "Publish this build as an
update" (PublishUpdate), with its own token in release_token.json.

Tests: FREQ_UPDATE_URL points the check at another latest.json;
FREQ_UPDATE_REPO ("owner/name") at another repository for publishing.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

import version
from jobs import Job

WINDOWS = sys.platform == "win32"
OS = "windows" if WINDOWS else "linux"
OS_NAMES = {"linux": "Linux", "windows": "Windows", "android": "Android"}
ASSETS = {"linux": "Frequencies-x86_64.AppImage",
          "windows": "Frequencies-Setup.exe"}
MANIFEST = "latest.json"
REPO_NAME = "frequencies-releases"
TOKEN_FILE = "release_token.json"
MUTEX = "FrequenciesRunning"
FIRST_CHECK_MS = 15000          # after launch, once the window is settled
CHECKED = "update_checked_at"   # settings: last successful check
LAST_RUN = "update_last_build"  # settings: the build that ran last time
TRIES = 2                       # Windows: installer runs at launch, at most
SHIPPED = ("talk stations", "Twitch Client ID", "report token",
           "disabled presets")

_session_ending = False


def _log(text):
    try:
        import crashlog
        crashlog.write("[update] " + text)
    except Exception:                           # noqa: BLE001
        pass


# -- where things are ----------------------------------------------------------

def repository():
    """(owner, name) of the releases repository, or None."""
    forced = os.environ.get("FREQ_UPDATE_REPO", "").strip()
    if "/" in forced:
        owner, name = forced.split("/", 1)
        return owner, name
    import photopack
    found = re.match(r"https://github\.com/([^/\s]+)/",
                     photopack.pack_address() or "")
    return (found.group(1), REPO_NAME) if found else None


def manifest_url():
    forced = os.environ.get("FREQ_UPDATE_URL", "").strip()
    if forced:
        return forced
    repo = repository()
    if repo is None:
        return ""
    return (f"https://github.com/{repo[0]}/{repo[1]}/releases/latest/"
            f"download/{MANIFEST}")


def folder():
    import appdirs
    path = os.path.join(appdirs.data_base(), "frequencies", "updates")
    os.makedirs(path, exist_ok=True)
    return path


def _pending_path():
    return os.path.join(folder(), "pending.json")


def pending():
    try:
        with open(_pending_path(), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def this_copy():
    """The file this copy runs from: the installed or unpacked exe on
    Windows, the AppImage on Linux."""
    if WINDOWS:
        return os.path.abspath(sys.executable)
    return os.environ.get("APPIMAGE", "")


def ours():
    """pending(), when this copy saved it. The updates folder is shared
    by every copy on the PC, so a test or unpacked copy's download must
    never be installed over the installed one (Windows test, build 125).
    A note with no "app" (an early build 125 saved it) counts as ours."""
    info = pending()
    here = this_copy()
    return info if info and info.get("app", here) == here else {}


def _save_pending(data):
    path = _pending_path()
    if not data:
        if os.path.exists(path):
            os.remove(path)
        return
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(path + ".tmp", path)


def installed_copy():
    """Windows: is this the installed app (an uninstaller beside it)?"""
    if not getattr(sys, "frozen", False):
        return False
    here = os.path.dirname(os.path.abspath(sys.executable))
    return os.path.exists(os.path.join(here, "unins000.exe"))


def cant_replace():
    """Why this copy can't be replaced in place, or ""."""
    if WINDOWS:
        if not installed_copy():
            return ("this copy isn't installed (updates install themselves "
                    "only in a copy installed with Frequencies-Setup.exe)")
        return ""
    image = os.environ.get("APPIMAGE", "")
    if not image or not os.path.isfile(image):
        return "it isn't running from an AppImage"
    if "appimagelauncherfs" in image:
        return ("AppImageLauncher looks after this AppImage, so Frequencies "
                "leaves the file alone")
    if not os.access(os.path.dirname(image), os.W_OK):
        return ("the folder it's in can't be written ("
                + os.path.dirname(image) + ")")
    return ""


def download_path(build, entry):
    """Where build `build` is downloaded to."""
    if not WINDOWS and not cant_replace():
        image = os.environ["APPIMAGE"]
        # Beside the running AppImage, so the swap is one rename on the
        # same filesystem.
        return os.path.join(os.path.dirname(image),
                            f".{os.path.basename(image)}.update-{build}")
    name = ASSETS[OS] if WINDOWS else f"Frequencies-build{build}.AppImage"
    return os.path.join(folder(), name)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for piece in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(piece)
    return digest.hexdigest()


def _verified(info):
    """The pending download, checked, or None."""
    path = info.get("file") or ""
    try:
        if os.path.getsize(path) != int(info.get("size") or -1):
            return None
        if sha256_of(path) != info.get("sha256"):
            return None
    except (OSError, ValueError):
        return None
    return path


# -- what's new ----------------------------------------------------------------

def whats_new(since, upto):
    """The lines of every build after `since`, up to `upto`."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(here, "whats_new.json"),
                  encoding="utf-8") as handle:
            notes = json.load(handle)
    except (OSError, ValueError):
        return []
    lines = []
    for build in sorted((int(b) for b in notes if str(b).isdigit()),
                        reverse=True):
        if since < build <= upto:
            lines += [str(line) for line in notes.get(str(build)) or []]
    return lines


# -- launch and quit (main.py) -------------------------------------------------

def session_ending(*_args):
    """Windows is shutting down or signing out (Qt's commitDataRequest):
    the installer would be killed half-way, so it waits for the next
    normal quit."""
    global _session_ending
    _session_ending = True
    _log("the session is ending: the update waits for the next quit")


def _dev():
    import devmode
    return devmode.is_dev()


def before_window():
    """At launch, before any window. Tidies a finished update away, and on
    Windows runs an installer that didn't finish last time (so the app
    never runs from a half-updated copy). True = the installer is
    running and this launch should end."""
    info = ours()
    if not info:
        return False
    build = int(info.get("build") or 0)
    if build <= version.BUILD or info.get("os") != OS:
        # Done (this is that build or newer), or not for this OS.
        path = info.get("file") or ""
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        _save_pending({})
        if build == version.BUILD:
            _log(f"build {build} is in place (update from build "
                 f"{info.get('from')})")
        return False
    if not WINDOWS or _dev() or not info.get("started"):
        return False
    tries = int(info.get("tries") or 0)
    if tries >= TRIES or cant_replace():
        _log(f"build {build}'s installer didn't finish {tries} times; "
             f"giving up on this download")
        _save_pending({})
        return False
    path = _verified(info)
    if not path:
        _save_pending({})
        return False
    info["tries"] = tries + 1
    _save_pending(info)
    _log(f"build {build}'s installer didn't finish last time: running it "
         f"again before the window opens (try {tries + 1})")
    return _start_installer(path, relaunch=True)


def apply_on_quit():
    """At the very end of a real quit. Puts a downloaded build in place."""
    info = ours()
    if not info or _dev() or info.get("os") != OS:
        return
    build = int(info.get("build") or 0)
    if build <= version.BUILD:
        return
    if _session_ending:
        _log(f"build {build} not installed: Windows is shutting down")
        return
    why = cant_replace()
    if why:
        _log(f"build {build} is downloaded but not put in place: {why}")
        return
    path = _verified(info)
    if not path:
        _log(f"build {build}'s download didn't check out; it will be "
             f"downloaded again")
        _save_pending({})
        return
    if WINDOWS:
        info["started"] = True
        _save_pending(info)
        _start_installer(path, relaunch=False)
        return
    image = os.environ["APPIMAGE"]
    try:
        mode = os.stat(image).st_mode & 0o7777
        os.chmod(path, mode | 0o111)
        os.replace(path, image)
    except OSError as exc:
        _log(f"build {build} could not replace {image}: {exc}")
        return
    info["file"] = ""
    _save_pending(info)
    _log(f"build {build} put in place: {image}")


def _start_installer(path, relaunch):
    args = [path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS",
            "/NORESTARTAPPLICATIONS", "/NORESTART", "/UPDATE"]
    if relaunch:
        args.append("/RELAUNCH")
    detached, group, breakaway = 0x8, 0x200, 0x01000000
    for flags in (detached | group | breakaway, detached | group):
        try:
            subprocess.Popen(args, creationflags=flags, close_fds=True,
                             cwd=os.path.dirname(path))
            _log("installer started: " + " ".join(
                [os.path.basename(path)] + args[1:]))
            return True
        except OSError as exc:
            error = exc
    _log(f"the installer wouldn't start: {error}")
    return False


# -- background work -----------------------------------------------------------

class Check(Job):
    """Read latest.json. done(entry or {}, problem or "")."""

    done = pyqtSignal(dict, str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def work(self):
        import requests
        try:
            answer = requests.get(self.url, timeout=20, headers={
                "User-Agent": "Frequencies (radio app)",
                "Cache-Control": "no-cache"})
            if answer.status_code == 404:
                self.send(self.done, {}, "")        # nothing published yet
                return
            answer.raise_for_status()
            data = answer.json()
            entry = data.get(OS) if isinstance(data, dict) else None
            if isinstance(entry, dict):
                entry = dict(entry, _others={
                    k: v.get("build") for k, v in data.items()
                    if isinstance(v, dict)})
            self.send(self.done, entry or {}, "")
        except Exception as exc:                # noqa: BLE001
            self.send(self.done, {}, _plain(exc))


class Download(Job):
    """Fetch one build and check it. progress(percent), done(path or "",
    problem or "")."""

    progress = pyqtSignal(int)
    done = pyqtSignal(str, str)

    def __init__(self, entry, path):
        super().__init__()
        self.entry = entry
        self.path = path
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def work(self):
        import requests
        part = self.path + ".part"
        try:
            if os.path.exists(self.path) and _verified(
                    {"file": self.path, **self.entry}):
                self.send(self.done, self.path, "")     # downloaded before
                return
            size = int(self.entry.get("size") or 0)
            got, last = 0, -1
            digest = hashlib.sha256()
            with requests.get(self.entry["url"], stream=True, timeout=30,
                              headers={"User-Agent":
                                       "Frequencies (radio app)"}) as answer:
                answer.raise_for_status()
                with open(part, "wb") as handle:
                    for piece in answer.iter_content(256 * 1024):
                        if self._stop.is_set():
                            raise InterruptedError("stopped at quit")
                        handle.write(piece)
                        digest.update(piece)
                        got += len(piece)
                        percent = got * 100 // max(size, 1)
                        if percent != last:
                            last = percent
                            self.send(self.progress, min(percent, 100))
            if got != size or digest.hexdigest() != self.entry["sha256"]:
                raise ValueError("the download didn't match its checksum")
            os.replace(part, self.path)
            self.send(self.done, self.path, "")
        except Exception as exc:                # noqa: BLE001
            try:
                os.remove(part)
            except OSError:
                pass
            self.send(self.done, "", _plain(exc))


def _plain(exc):
    try:
        import requests
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return "couldn't reach GitHub (offline?)"
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return f"GitHub answered {exc.response.status_code}"
    except ImportError:
        pass
    return str(exc) or type(exc).__name__


class Updater(QObject):
    """The window's one updater. `status(text)` for Settings; `ready(text)`
    once, when an update is waiting for the quit."""

    status = pyqtSignal(str)
    ready = pyqtSignal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.text = ""
        self.manual = ""        # a download the user installs by hand
        self._job = None
        self._quiet = True
        info = ours()
        if info.get("os") == OS and int(info.get("build") or 0) \
                > version.BUILD and not _dev():
            self._waiting(int(info["build"]), info.get("file") or "")
        else:
            self._say(self._resting())

    # -- at launch

    def start(self):
        _log(f"build {version.BUILD} ({OS}, "
             f"{'dev' if _dev() else 'user'} build)")
        if _dev() or self.manual or ours().get("os") == OS:
            return
        # Once every session (the user, 2026-10-19).
        QTimer.singleShot(FIRST_CHECK_MS, lambda: self.check(quiet=True))

    def updated_note(self):
        """(build, lines) once, on the first launch of a newer build."""
        last = int(self.settings.get(LAST_RUN) or 0)
        if last == version.BUILD:
            return None
        self.settings[LAST_RUN] = version.BUILD
        self._save()
        if not last or last > version.BUILD:
            return None
        _log(f"updated from build {last} to {version.BUILD}")
        return version.BUILD, whats_new(last, version.BUILD)

    # -- checking

    def check(self, quiet=False):
        if self._job is not None and self._job.isRunning():
            return
        url = manifest_url()
        if not url:
            self._say("This build has no update address (it comes with "
                      "the photo pack address).")
            return
        self._quiet = quiet
        if not quiet:
            self._say("Checking…")
        job = Check(url)
        job.done.connect(self._checked)
        self._job = job
        job.start()

    def _checked(self, entry, problem):
        self._job = None
        if problem:
            _log(f"check failed: {problem}")
            self._say(self._resting() if self._quiet
                      else f"Couldn't check: {problem}.")
            return
        self.settings[CHECKED] = time.time()
        self._save()
        build = int(entry.get("build") or 0)
        if build <= version.BUILD:
            _log(f"up to date (newest {OS} build: {build or 'none'})")
            self._say(f"Up to date (build {version.BUILD}).")
            return
        _log(f"build {build} is out ({OS})")
        if _dev():
            self._say(f"Build {build} is out. This is a dev build, so it "
                      "doesn't install user builds over itself.")
            return
        self._download(build, entry)

    # -- downloading

    def _download(self, build, entry):
        path = download_path(build, entry)
        self._say(f"Downloading build {build}…")
        job = Download(entry, path)
        job.progress.connect(lambda p: self._say(
            f"Downloading build {build}… {p}%"))
        job.done.connect(lambda got, problem: self._downloaded(
            build, entry, got, problem))
        self._job = job
        job.start()

    def _downloaded(self, build, entry, path, problem):
        self._job = None
        if problem:
            _log(f"download of build {build} failed: {problem}")
            self._say(self._resting() if self._quiet
                      else f"Couldn't download build {build}: {problem}.")
            return
        _save_pending({"build": build, "os": OS, "file": path,
                       "app": this_copy(),
                       "size": entry.get("size"),
                       "sha256": entry.get("sha256"),
                       "from": version.BUILD})
        _log(f"build {build} downloaded and checked: {path}")
        self._waiting(build, path)
        self.ready.emit(self.text)

    def _waiting(self, build, path):
        why = cant_replace()
        if why:
            self.manual = path
            self._say(f"Build {build} is downloaded, but Frequencies can't "
                      f"put it in place itself: {why}. Use Show the "
                      "download to install it yourself.")
        else:
            self._say("Update ready: applies when you close Frequencies.")

    # -- small parts

    def _resting(self):
        return f"This is build {version.BUILD}."

    def _say(self, text):
        self.text = text
        self.status.emit(text)

    def _save(self):
        try:
            import storage
            storage.save_settings(self.settings)
        except Exception:                       # noqa: BLE001
            pass


_updater = None


def updater(settings=None, parent=None):
    """The app's one Updater (made by the window at start-up)."""
    global _updater
    if _updater is None and settings is not None:
        _updater = Updater(settings, parent)
    return _updater


# -- publishing (dev builds) ---------------------------------------------------

def token_path():
    import storage
    return os.path.join(os.path.dirname(storage.settings_path()), TOKEN_FILE)


def token():
    try:
        with open(token_path(), encoding="utf-8") as handle:
            return str(json.load(handle).get("token") or "").strip()
    except (OSError, ValueError, AttributeError):
        return ""


def save_token(value):
    value = (value or "").strip()
    path = token_path()
    if not value:
        if os.path.exists(path):
            os.remove(path)
        return
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"token": value}, handle)
    os.replace(path + ".tmp", path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def build_info(path):
    """The build script's note about a file it made (<file>.json), checked
    against the file: (info, problem or "")."""
    try:
        with open(path + ".json", encoding="utf-8") as handle:
            info = json.load(handle)
    except (OSError, ValueError):
        return {}, ("the build script didn't leave its note beside this "
                    f"file ({os.path.basename(path)}.json), so it can't be "
                    "checked. Build it again with the build script")
    if info.get("dev"):
        return info, "that's a dev build; publish the user build"
    if info.get("os") != OS:
        return info, (f"that's a {OS_NAMES.get(info.get('os'), '?')} build; "
                      f"publish it from {OS_NAMES.get(info.get('os'), '?')}")
    missing = [name for name in SHIPPED
               if not (info.get("shipped") or {}).get(name)]
    if missing:
        return info, ("it was built without your " + ", ".join(missing)
                      + ", so updated users would lose them. Set them in "
                      "this dev build's Settings and build again")
    try:
        same = (os.path.getsize(path) == info.get("size")
                and sha256_of(path) == info.get("sha256"))
    except OSError as exc:
        return info, f"it can't be read ({exc})"
    if not same:
        return info, "the file changed after it was built; build it again"
    return info, ""


def heads_up(manifest, build):
    """". Heads up: …" naming every OS in latest.json that is behind
    `build` (task 184), or "". Linux and Windows count even when they
    have nothing published yet; Android once it has an entry."""
    behind = []
    for name in OS_NAMES:
        if name == OS:
            continue
        entry = manifest.get(name)
        if not isinstance(entry, dict) and name == "android":
            continue
        have = int((entry or {}).get("build") or 0)
        if have < build:
            behind.append(f"{OS_NAMES[name]} on {have or 'nothing yet'}")
    if not behind:
        return ""
    return (f". Heads up: {OS_NAMES[OS]} is on {build}, "
            + ", ".join(behind))


class PublishUpdate(Job):
    """Put one user build on the releases repository: its `build-N`
    release (made if new), then latest.json. progress(text), done(ok,
    text)."""

    progress = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    API = "https://api.github.com"
    UPLOADS = "https://uploads.github.com"

    def __init__(self, path):
        super().__init__()
        self.path = path
        self._token = token()

    def _headers(self, extra=None):
        headers = {"Authorization": "Bearer " + self._token,
                   "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "Frequencies (radio app)"}
        headers.update(extra or {})
        return headers

    def work(self):
        try:
            ok, text = True, self._publish()
        except Exception as exc:                # noqa: BLE001
            ok, text = False, self._why(exc)
        _log(f"publish: {'done' if ok else 'failed'} - {text}")
        self.send(self.done, ok, text)

    def _why(self, exc):
        import requests
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            code = exc.response.status_code
            if code in (401, 403):
                return ("GitHub refused the release token (it needs "
                        "Contents: Read and write on the releases "
                        "repository)")
            if code == 404:
                owner, name = repository() or ("?", REPO_NAME)
                return (f"GitHub can't find {owner}/{name} (make it first, "
                        "and check the token is for it)")
            import ghpublish
            said = ghpublish._message(exc.response)
            return f"GitHub answered {code}" + (f": {said}" if said else "")
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return "couldn't reach GitHub (offline?)"
        return str(exc) or type(exc).__name__

    def _publish(self):
        import requests
        repo = repository()
        if repo is None:
            raise RuntimeError("this dev build has no photo pack address, "
                               "so it doesn't know your GitHub account")
        if not self._token:
            raise RuntimeError("paste your release token first")
        self.send(self.progress, "Checking the build…")
        info, problem = build_info(self.path)
        if problem:
            raise RuntimeError(f"not published: {problem}")
        build = int(info["build"])
        owner, name = repo
        base = f"{self.API}/repos/{owner}/{name}"
        self.send(self.progress, "Reading what's published…")
        manifest, latest = self._manifest(base)
        have = int((manifest.get(OS) or {}).get("build") or 0)
        if have >= build:
            raise RuntimeError(
                f"not published: {OS_NAMES[OS]} build {have} is already "
                f"out (bump src/version.py for a new build)")
        release = self._release(base, build)
        asset = ASSETS[OS]
        self._replace(base, owner, name, release, asset,
                      open(self.path, "rb").read())
        manifest[OS] = {
            "build": build,
            "url": (f"https://github.com/{owner}/{name}/releases/download/"
                    f"build-{build}/{asset}"),
            "size": info["size"], "sha256": info["sha256"]}
        data = json.dumps(manifest, indent=2).encode()
        self.send(self.progress, f"Uploading {MANIFEST}…")
        targets = [release]
        if latest and latest.get("id") != release.get("id") \
                and _tag_build(latest) > build:
            targets.append(latest)      # a newer release is "latest"
        for target in targets:
            self._replace(base, owner, name, target, MANIFEST, data)
        text = (f"published {OS_NAMES[OS]} build {build} to "
                f"{owner}/{name}")
        return text + heads_up(manifest, build)

    def _manifest(self, base):
        """(latest.json as a dict, the latest release or None)."""
        import requests
        answer = requests.get(base + "/releases/latest",
                              headers=self._headers(), timeout=20)
        if answer.status_code == 404:
            return {}, None
        answer.raise_for_status()
        latest = answer.json()
        for asset in latest.get("assets") or []:
            if asset.get("name") == MANIFEST:
                got = requests.get(
                    asset["url"], timeout=20,
                    headers=self._headers(
                        {"Accept": "application/octet-stream"}))
                got.raise_for_status()
                data = got.json()
                return (data if isinstance(data, dict) else {}), latest
        return {}, latest

    def _release(self, base, build):
        import requests
        tag = f"build-{build}"
        answer = requests.get(f"{base}/releases/tags/{tag}",
                              headers=self._headers(), timeout=20)
        if answer.status_code == 200:
            return answer.json()
        lines = whats_new(build - 1, build)
        new = {"tag_name": tag, "name": f"Build {build}",
               "body": "\n".join("- " + line for line in lines)
               or f"Frequencies build {build}.",
               "make_latest": "true"}
        answer = requests.post(base + "/releases", headers=self._headers(),
                               timeout=20, json=new)
        if answer.status_code == 422 and "empty" in answer.text.lower():
            import base64
            readme = requests.put(
                base + "/contents/README.md", headers=self._headers(),
                timeout=20,
                json={"message": "Start the releases repository",
                      "content": base64.b64encode(
                          b"# Frequencies releases\n\nBuilds of the "
                          b"Frequencies radio app. The app updates itself "
                          b"from here.\n").decode()})
            readme.raise_for_status()
            answer = requests.post(base + "/releases",
                                   headers=self._headers(), timeout=20,
                                   json=new)
        answer.raise_for_status()
        _log(f"made release {tag}")
        return answer.json()

    def _replace(self, base, owner, name, release, filename, data):
        import requests
        import ghpublish
        listed = requests.get(f"{base}/releases/{release['id']}/assets",
                              headers=self._headers(), timeout=20,
                              params={"per_page": 100})
        listed.raise_for_status()
        for asset in listed.json():
            if asset.get("name") == filename:
                requests.delete(f"{base}/releases/assets/{asset['id']}",
                                headers=self._headers(),
                                timeout=20).raise_for_status()

        def report(at, total):
            self.send(self.progress, f"Uploading {filename}… "
                      f"{at * 100 // max(total, 1)}%")
        url = (f"{self.UPLOADS}/repos/{owner}/{name}/releases/"
               f"{release['id']}/assets?name={filename}")
        sent = requests.post(
            url, data=ghpublish._Upload(data, report), timeout=1800,
            headers=self._headers({
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(data))}))
        sent.raise_for_status()


def _tag_build(release):
    found = re.match(r"build-(\d+)$", (release or {}).get("tag_name") or "")
    return int(found.group(1)) if found else 0
