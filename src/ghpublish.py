"""Frequencies — the dev build's "Publish … to GitHub" buttons (build 95).

Settings > Lyrics and artists, dev builds only. Each button makes its file
the way the build does (packbuild.py) and puts it on the pack's GitHub
release, replacing the one there:

  Publish my photos to GitHub     frequencies-photo-pack.b32 (base32 twice),
                                  then photo-pack.json - opted-in users'
                                  apps pick it up at their next check;
                                  and the band logos the same way
                                  (frequencies-logo-pack.b32 +
                                  logo-pack.json, build 141), which every
                                  user's app picks up unless switched off
  Publish my bio fixes to GitHub  bio-fixes.json - every user's app picks
                                  it up, no opt-in
  Publish my visualizer list      visualizer-list.json - the presets you
    to GitHub                     turned off; a user build takes it once,
                                  on its first run (build 106, task 142)

The repository comes from the Photo pack address
(https://github.com/<you>/<repo>/releases/latest/download/photo-pack.json).
The release is the repository's latest one; with none yet, one called
"Photo pack" (tag photo-pack) is made.

Signing in to GitHub: a fine-grained personal access token, only for that
repository, with "Contents: Read and write". It is kept in
~/.config/frequencies/github_token.json (readable only by you), never
shipped (no build tool reads it) and never logged.
"""

import json
import os
import re
import urllib.parse

from PyQt5.QtCore import pyqtSignal

from jobs import Job

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
TOKEN_FILE = "github_token.json"
TAG = "photo-pack"


def _log(text):
    try:
        import crashlog
        crashlog.write("[publish] " + text)
    except Exception:                           # noqa: BLE001
        pass


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
    os.chmod(path, 0o600)


def repository(address):
    """(owner, repo) from a github.com address, or None."""
    found = re.match(r"https://github\.com/([^/\s]+)/([^/\s#?]+)",
                     address or "")
    return (found.group(1), found.group(2)) if found else None


def gather(kind):
    """The files to send for "photos" or "bios": [(name, bytes)], and a
    line saying what they hold. Reads the dev build's own corrections and
    talk stations."""
    import packbuild
    import storage
    dev = storage.load_artist_overrides()
    if kind == "visualizer":
        import photopack
        data, count = photopack.visualizer_list_bytes()
        return [(photopack.VIS_NAME, data)], f"{count} presets turned off"
    if kind == "bios":
        data, count, _digest = packbuild.bio_fixes(dev)
        return [(packbuild.BIO_NAME, data)], f"{count} artists' fixes"
    missing = packbuild.missing_photos(dev)
    if missing:
        raise RuntimeError(
            f"{len(missing)} artist photos aren't on this computer (such as "
            f"{missing[0]}'s), so publishing from here would take them out "
            f"of the photo pack. Publish it from the computer that has them")
    missing = packbuild.missing_logos(dev)
    if missing:
        raise RuntimeError(
            f"{len(missing)} band logos aren't on this computer (such as "
            f"{missing[0]}'s), so publishing from here would take them out. "
            f"Publish from the computer that has them")
    talk = {}
    for key, entry in storage.talk_entries().items():
        photo = (entry.get("photo") or "").strip()
        if not entry.get("talk") or not photo \
                or photo.startswith(("http://", "https://")):
            continue
        path = storage.talk_photo_path(photo)
        if not path:
            continue
        if os.path.isabs(photo):
            # The name tools/ship_talk_stations.py gives it in the build.
            name = packbuild.slug(key) + os.path.splitext(photo)[1]
        else:
            name = os.path.basename(photo)
        talk[name] = path
    data, manifest = packbuild.photo_pack(dev, talk, warn=_log)
    files, what = [], []
    if data is not None:
        what.append(f"{manifest['photos'] - manifest['talk_photos']} artist "
                    f"photos, {manifest['talk_photos']} talk station photos, "
                    f"{len(data) // 1024} KB")
        # The pack first: a manifest must never point at a file not
        # there yet.
        files += [(packbuild.PACK_NAME, data),
                  (packbuild.MANIFEST, packbuild.manifest_bytes(manifest))]
    # The band logos, a pack of their own beside it (build 141).
    logos, logo_manifest = packbuild.logo_pack(dev, warn=_log)
    if logos is not None:
        what.append(f"{logo_manifest['logos']} band logos")
        files += [(packbuild.LOGO_NAME, logos),
                  (packbuild.LOGO_MANIFEST,
                   packbuild.manifest_bytes(logo_manifest))]
    return files, ", ".join(what) or "no photos"


class _Upload:
    """A body for requests that says how far it has got."""

    def __init__(self, data, report):
        self.data = data
        self.at = 0
        self.report = report

    def __len__(self):
        return len(self.data)

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self.data) - self.at
        size = min(size, 256 * 1024)
        piece = self.data[self.at:self.at + size]
        self.at += len(piece)
        self.report(self.at, len(self.data))
        return piece


class Publish(Job):
    """Send "photos" or "bios" to the pack's GitHub release. Answers
    progress(text) as it goes and done(ok, text) at the end."""

    progress = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, kind, address, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.address = address
        self._token = token()

    def work(self):
        try:
            ok, text = True, self._publish()
        except Exception as exc:                # noqa: BLE001
            ok, text = False, _why(exc)
            response = getattr(exc, "response", None)
            if response is not None:
                _log(f"{self.kind}: {response.request.method} "
                     f"{response.url.split('?')[0]} -> "
                     f"{response.status_code}")
        _log(f"{self.kind}: {'done' if ok else 'failed'} - {text}")
        self.send(self.done, ok, text)

    def _headers(self, extra=None):
        headers = {"Authorization": "Bearer " + self._token,
                   "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "Frequencies (radio app)"}
        headers.update(extra or {})
        return headers

    def _publish(self):
        repo = repository(self.address)
        if repo is None:
            raise RuntimeError("the Photo pack address isn't a GitHub "
                               "address")
        if not self._token:
            raise RuntimeError("paste your GitHub token first")
        self.send(self.progress, "Getting your files ready…")
        files, what = gather(self.kind)
        if not files:
            raise RuntimeError("there are no photos or logos to publish "
                               "(Edit this artist… > photo or logo)")
        owner, name = repo
        base = f"{API}/repos/{owner}/{name}"
        self.send(self.progress, "Finding the release on GitHub…")
        release = self._release(base, owner, name)
        for filename, data in files:
            self._clear(base, release["id"], filename,
                        release.get("assets"))

            def report(at, total, filename=filename):
                self.send(self.progress, f"Uploading {filename}… "
                          f"{at * 100 // max(total, 1)}%")
            sent = self._upload(owner, name, release["id"], filename, data,
                                report)
            if sent.status_code == 422:
                # GitHub still has one by that name (a half-finished
                # upload is listed only on the release's own asset list).
                self._clear(base, release["id"], filename, None)
                sent = self._upload(owner, name, release["id"], filename,
                                    data, report)
            sent.raise_for_status()
        return f"published ({what}) to {owner}/{name}"

    def _release(self, base, owner, name):
        """The release the "latest" download links point at: the newest
        full release; else the photo-pack one (made a full release if it
        was a draft or pre-release); else a new one. An empty repository
        gets a README first - GitHub can't make a release without a
        commit (it answers 422 "Repository is empty")."""
        import requests
        answer = requests.get(base + "/releases/latest",
                              headers=self._headers(), timeout=20)
        if answer.status_code == 200:
            return answer.json()
        answer = requests.get(f"{base}/releases/tags/{TAG}",
                              headers=self._headers(), timeout=20)
        if answer.status_code == 200:
            release = answer.json()
            if release.get("draft") or release.get("prerelease"):
                fixed = requests.patch(
                    f"{base}/releases/{release['id']}",
                    headers=self._headers(), timeout=20,
                    json={"draft": False, "prerelease": False,
                          "make_latest": "true"})
                fixed.raise_for_status()
                _log(f"release '{TAG}' made a full release (it was a "
                     f"draft or pre-release)")
                release = fixed.json()
            return release
        new = {"tag_name": TAG, "name": "Photo pack",
               "body": "Artist photos and bio fixes for Frequencies."}
        answer = requests.post(base + "/releases", headers=self._headers(),
                               timeout=20, json=new)
        if answer.status_code == 422 and "empty" in _message(answer).lower():
            import base64
            self.send(self.progress, "The repository is empty; adding a "
                      "README first…")
            readme = requests.put(
                base + "/contents/README.md", headers=self._headers(),
                timeout=20,
                json={"message": "Start the photo pack repository",
                      "content": base64.b64encode(
                          b"# Photo pack\n\nArtist photos and bio fixes "
                          b"for the Frequencies radio app.\n").decode()})
            readme.raise_for_status()
            _log(f"{owner}/{name} was empty: added README.md")
            answer = requests.post(base + "/releases",
                                   headers=self._headers(), timeout=20,
                                   json=new)
        answer.raise_for_status()
        _log(f"made release '{TAG}' on {owner}/{name}")
        return answer.json()

    def _clear(self, base, release_id, filename, assets):
        """Delete the release's file of this name, if it has one."""
        import requests
        if assets is None:
            listed = requests.get(f"{base}/releases/{release_id}/assets",
                                  headers=self._headers(), timeout=20,
                                  params={"per_page": 100})
            listed.raise_for_status()
            assets = listed.json()
        for asset in assets or []:
            if asset.get("name") == filename:
                gone = requests.delete(
                    f"{base}/releases/assets/{asset['id']}",
                    headers=self._headers(), timeout=20)
                gone.raise_for_status()

    def _upload(self, owner, name, release_id, filename, data, report):
        import requests
        url = (f"{UPLOADS}/repos/{owner}/{name}/releases/{release_id}/"
               f"assets?" + urllib.parse.urlencode({"name": filename}))
        return requests.post(
            url, data=_Upload(data, report), timeout=300,
            headers=self._headers({
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(data))}))


def _message(response):
    """GitHub's own reason, from its answer's JSON."""
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    parts = [str(data.get("message") or "")]
    for error in data.get("errors") or []:
        if isinstance(error, dict):
            parts.append(str(error.get("message") or error.get("code")
                             or ""))
        else:
            parts.append(str(error))
    return "; ".join(p for p in parts if p)


def _why(exc):
    try:
        import requests
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            code = exc.response.status_code
            if code in (401, 403):
                return ("GitHub refused the token (it needs Contents: Read "
                        "and write on this repository)")
            if code == 404:
                return ("GitHub can't find the repository (check the Photo "
                        "pack address, and that the token is for it)")
            said = _message(exc.response)
            return f"GitHub answered {code}" + (f": {said}" if said else "")
        if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
            return "couldn't reach GitHub (offline?)"
    except ImportError:
        pass
    text = str(exc).strip() or type(exc).__name__
    return text if len(text) < 160 else text[:157] + "…"
