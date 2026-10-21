"""Frequencies — replacing a shared thumbnail with the real artwork.

A station that arrives by QR code brings its cover as a tiny JPEG (64 px
or less), because that is what fits. It looks right in the list and poor
anywhere else, and the full-size original is sitting on the internet
where it came from.

So: when a station arrives that way, go and look for that same picture
at full size (build 126, task 178). In the background, a couple of
stations at a time, without asking. Candidates come from the station's
own favicon and website, the radio directory (radio-browser) and
TuneIn's station search, all over plain HTTP so Linux and Windows find
the same things and no browser window ever opens. (The image search
behind the Display tab's Find… button, Startpage, answers a plain request
with a bot check page; Bing's answers it with unrelated filler. Only a
real browser gets results from either, so they are left to Find….)
Each candidate is compared with the thumbnail (colors, light and dark
areas and edges on a small grid, on Qt alone), and only a picture that
clearly is the same one, and bigger, replaces it. A different-looking
logo never does; then the thumbnail stays.
"""

import html
import os
import re
import time
from urllib.parse import quote, urljoin, urlsplit

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from jobs import Job

# What marks a station as carrying a shared thumbnail.
FLAG = "art_from_code"
# When the last search that ran properly found nothing (time.time()).
CHECKED = "art_checked"
# A search that found nothing is tried again after this long.
RETRY_DAYS = 30
# Searches running at once.
MAX_RUNNING = 2

# A thumbnail this size or smaller is worth replacing (a code's is 64 px
# at most; a share zip can carry one that came from a code).
SMALL_PX = 128
# A replacement must be at least this many times the thumbnail's size,
# and at least FLOOR_PX on its short side.
BIGGER_BY = 2
FLOOR_PX = 120
# Good enough to stop looking.
ENOUGH_PX = 400
# Saved no bigger than this on its long side.
SAVE_MAX_PX = 1200

# How alike two pictures must be (see "comparing pictures" below).
COLOR_LIMIT = 20
SHAPE_LIMIT = 0.92
EDGE_LIMIT = 0.85
ASPECT_LIMIT = 0.12
# A thumbnail flatter than this has nothing to match by.
DETAIL_MIN = 4

MAX_CANDIDATES = 24
MAX_BYTES = 6 * 1024 * 1024
AGENT = "Frequencies/1.5"
BROWSER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                 "Gecko/20100101 Firefox/128.0")
TUNEIN_SEARCH = "https://opml.radiotime.com/Search.ashx?render=json&query="


def _log(text):
    try:
        import crashlog
        crashlog.write("[art] " + text)
    except Exception:                           # noqa: BLE001
        pass


# -- comparing pictures -------------------------------------------------
#
# Both pictures are brought to the thumbnail's own shape and then to a
# small grid (half the thumbnail's short side, 12-32 cells), and three
# things must agree: the colors (mean 0-255 difference), the light and
# dark areas (correlation), and the edges (correlation of the steps
# between neighbors), which is what tells two look-alike text logos
# apart. Calibrated on photos and on look-alike text logos shrunk the
# way a code shrinks them (build 126): every same picture with any
# detail matched; different ones only did between near-identical text
# logos made to be confusing.

def _flattened(image, background):
    """The picture on a solid background, as the JPEG in a code has it."""
    from PyQt5.QtGui import QImage, QPainter

    out = QImage(image.size(), QImage.Format_RGB32)
    out.fill(background)
    painter = QPainter(out)
    painter.drawImage(0, 0, image)
    painter.end()
    return out


def _cells(image, grid, width, height):
    """(rgb values, gray values, edge steps) of `image` on the grid."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QImage, qBlue, qGray, qGreen, qRed

    small = image.convertToFormat(QImage.Format_RGB32).scaled(
        width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    small = small.scaled(grid, grid, Qt.IgnoreAspectRatio,
                         Qt.SmoothTransformation)
    rgb, gray = [], []
    for y in range(grid):
        for x in range(grid):
            p = small.pixel(x, y)
            rgb.extend((qRed(p), qGreen(p), qBlue(p)))
            gray.append(qGray(p))
    edges = [gray[y * grid + x + 1] - gray[y * grid + x]
             for y in range(grid) for x in range(grid - 1)]
    edges += [gray[(y + 1) * grid + x] - gray[y * grid + x]
              for y in range(grid - 1) for x in range(grid)]
    return rgb, gray, edges


def _correlation(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    sa = sum((v - ma) ** 2 for v in a) ** 0.5
    sb = sum((v - mb) ** 2 for v in b) ** 0.5
    if sa < 1e-6 or sb < 1e-6:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def detail(thumb):
    """How much there is to match by: the thumbnail's gray spread.

    A transparent logo can arrive as a flat black square (the code's JPEG
    has no transparency); nothing can be matched to that.
    """
    _rgb, gray, _edges = _cells(thumb, 16, thumb.width(), thumb.height())
    mean = sum(gray) / len(gray)
    return (sum((v - mean) ** 2 for v in gray) / len(gray)) ** 0.5


def compare(thumb, image):
    """(color difference, light/dark correlation, edge correlation,
    aspect difference) of `image` against `thumb`, at its best
    background: a transparent logo became a JPEG on whatever was under
    it, black, white, or the raw values Qt keeps under transparency."""
    from PyQt5.QtCore import Qt

    width, height = thumb.width(), thumb.height()
    grid = max(12, min(32, min(width, height) // 2))
    mine = _cells(thumb, grid, width, height)
    tries = [image]
    if image.hasAlphaChannel():
        tries += [_flattened(image, Qt.black), _flattened(image, Qt.white)]
    best = None
    for candidate in tries:
        rgb, gray, edges = _cells(candidate, grid, width, height)
        color = sum(abs(x - y) for x, y in zip(mine[0], rgb)) / len(rgb)
        d = (color, _correlation(mine[1], gray),
             _correlation(mine[2], edges))
        if best is None or d[1] + d[2] - d[0] / 50 > \
                best[1] + best[2] - best[0] / 50:
            best = d
    a = width / max(1, height)
    b = image.width() / max(1, image.height())
    return best + (abs(a - b) / max(a, b),)


def is_same(d):
    color, shape, edges, aspect = d
    return color <= COLOR_LIMIT and shape >= SHAPE_LIMIT \
        and edges >= EDGE_LIMIT and aspect <= ASPECT_LIMIT


# -- where candidates come from ----------------------------------------

def _get(url, agent=AGENT, limit=MAX_BYTES, timeout=10):
    import requests

    response = requests.get(url, timeout=timeout, stream=True,
                            headers={"User-Agent": agent})
    response.raise_for_status()
    data = b""
    for chunk in response.iter_content(65536):
        data += chunk
        if len(data) > limit:
            response.close()
            raise ValueError("too big")
    return data


def page_images(page, base):
    """The pictures a website names for itself: og:image, touch icons."""
    out = []
    patterns = (
        r"[<]meta[^>]+(?:property|name)=[\"'](?:og:image|twitter:image)"
        r"(?::src)?[\"'][^>]*>",
        r"[<]link[^>]+rel=[\"'][^\"']*(?:apple-touch-icon|icon)[^\"']*[\"']"
        r"[^>]*>",
    )
    for pattern in patterns:
        for tag in re.findall(pattern, page, re.I):
            m = re.search(r"(?:content|href)=[\"']([^\"']+)[\"']", tag, re.I)
            if m:
                out.append(urljoin(base, html.unescape(m.group(1).strip())))
    return out


def tunein_logos(data, name, limit=3):
    """Logo addresses from a TuneIn search answer, biggest size first."""
    rows = [r for r in (data or {}).get("body") or []
            if isinstance(r, dict) and r.get("image")
            and r.get("type", "audio") == "audio"]
    wanted = (name or "").lower()
    # Same name first, then whatever TuneIn ranked highest.
    rows.sort(key=lambda r: wanted not in (r.get("text") or "").lower())
    out = []
    for row in rows[:limit]:
        image = row["image"]
        if "/logoq." in image:
            out += [image.replace("/logoq.", f"/logo{size}.")
                    for size in ("g", "d")]
        out.append(image)
    return out


def candidates(station, name, log):
    """Addresses worth downloading for this station, best guesses first.

    Returns (urls, searched): `searched` is False when nothing at all
    could be asked (offline), so the station is tried again next launch.
    """
    urls, asked = [], 0
    favicon = (station.get("favicon") or "").strip()
    if favicon.startswith("http"):
        urls.append(favicon)

    # The radio directory, by stream address first: a name can belong to
    # a dozen stations, an address to one.
    stream = (station.get("url") or "").strip()
    try:
        import radiobrowser
        rows = []
        if stream.startswith("http"):
            try:
                rows = [radiobrowser._station_from(item) for item in
                        radiobrowser._request("/json/stations/byurl",
                                              {"url": stream})]
                asked += 1
            except Exception:                   # noqa: BLE001
                rows = []
        same = [r for r in rows if r.get("favicon")]
        if not same and name:
            rows = radiobrowser.search_stations(name, limit=8)
            asked += 1
        for row in same or rows:
            icon = (row.get("favicon") or "").strip()
            if icon:
                urls.append(icon)
            if not station.get("homepage") and row.get("homepage"):
                station = dict(station, homepage=row["homepage"])
    except Exception as exc:                    # noqa: BLE001
        log.append(f"directory: {exc.__class__.__name__}")

    # The station's own website.
    homepage = (station.get("homepage") or "").strip()
    if homepage.startswith("http"):
        try:
            page = _get(homepage, BROWSER_AGENT, 1024 * 1024)
            asked += 1
            urls.extend(page_images(page.decode("utf-8", "replace"),
                                    homepage))
        except Exception as exc:                # noqa: BLE001
            log.append(f"homepage: {exc.__class__.__name__}")
    try:
        import feeds
        urls.extend(feeds._site_icons(station)[:2])
    except Exception:                           # noqa: BLE001
        pass

    # TuneIn's station search: its logos come in sizes, and the biggest
    # (logog, 600 px) is the one asked for first.
    if name:
        try:
            import json
            data = json.loads(_get(TUNEIN_SEARCH + quote(name), AGENT,
                                   1 << 20).decode("utf-8", "replace"))
            asked += 1
            urls.extend(tunein_logos(data, name))
        except Exception as exc:                # noqa: BLE001
            log.append(f"TuneIn: {exc.__class__.__name__}")

    seen, out = set(), []
    for url in urls:
        if url.startswith("http") and url not in seen:
            seen.add(url)
            out.append(url)
    return out[:MAX_CANDIDATES], asked > 0


# -- the search ----------------------------------------------------------

class _UpgradeWorker(Job):
    """Find the full-size picture for one station, off the GUI thread.

    A jobs.Job (daemon thread), not a QThread, so quitting mid-search
    can't crash the app (build 54, task 14)."""

    # station id, result dict (see work()).
    done = pyqtSignal(str, object)

    def __init__(self, station, thumb_path, parent=None):
        super().__init__(parent)
        self.station = dict(station)
        self.thumb_path = thumb_path

    def work(self):
        result = {"status": "error", "thumb": "", "looked": 0, "notes": []}
        try:
            self._search(result)
        except Exception as exc:                # noqa: BLE001
            result["notes"].append(f"{exc.__class__.__name__}: {exc}")
        self.send(self.done, self.station.get("id", ""), result)

    def _search(self, result):
        import imagelock
        import storage
        from PyQt5.QtGui import QImage

        thumb = imagelock.image(self.thumb_path)
        if thumb.isNull():
            result["status"] = "unreadable"
            return
        tw, th = thumb.width(), thumb.height()
        result["thumb"] = f"{tw}x{th}"
        flat = detail(thumb) < DETAIL_MIN
        # The sender's own link first (build 128): the exact picture,
        # checked against the thumbnail so a dead or changed link can't
        # put the wrong one on. A plain thumbnail can only be checked
        # for its shape.
        link = (self.station.get("art_url") or "").strip()
        if link.startswith("http"):
            try:
                image = QImage()
                if image.loadFromData(_get(link)):
                    d = compare(thumb, image)
                    bigger = min(image.width(), image.height()) > max(tw, th)
                    if bigger and (is_same(d) or flat and d[3] <= ASPECT_LIMIT):
                        result.update(
                            status="found", image=image, url=link,
                            via="the code's link",
                            size=f"{image.width()}x{image.height()}",
                            match=f"color {d[0]:.0f}, shape {d[1]:.2f}, "
                                  f"edges {d[2]:.2f}")
                        return
                    result["notes"].append("the code's link showed another picture")
                else:
                    result["notes"].append("the code's link gave no picture")
            except Exception as exc:            # noqa: BLE001
                result["notes"].append(f"the code's link: {exc.__class__.__name__}")
        if flat:
            result["status"] = "flat"
            return
        need = max(FLOOR_PX, BIGGER_BY * min(tw, th))

        # The directory's name for it, not a nickname given to it.
        name = (self.station.get("name") or "").strip() or \
            storage.station_display_name(self.station)
        urls, searched = candidates(self.station, name, result["notes"])
        best = None
        for url in urls:
            try:
                data = _get(url)
            except Exception:                   # noqa: BLE001
                continue
            image = QImage()
            if not image.loadFromData(data):    # under the image lock
                continue
            result["looked"] += 1
            if min(image.width(), image.height()) < need:
                continue
            d = compare(thumb, image)
            if not is_same(d):
                continue
            size = min(image.width(), image.height())
            if best is None or size > best[0]:
                best = (size, image, url, d)
            if size >= ENOUGH_PX:
                break
        if best is None:
            result["status"] = "none" if searched else "offline"
            return
        _size, image, url, d = best
        result.update(status="found", image=image, url=url,
                      size=f"{image.width()}x{image.height()}",
                      match=f"color {d[0]:.0f}, shape {d[1]:.2f}, "
                            f"edges {d[2]:.2f}")


def _art_folder():
    import storage
    return os.path.join(os.path.dirname(storage.settings_path()),
                        "station-art")


def save_picture(image, station_id):
    """Write the found picture under a new name and return its path.

    Always a new name: on Windows a file another handle has open (the
    list's picture, the media flyout) can't be overwritten.
    """
    import hashlib
    from PyQt5.QtCore import QBuffer, Qt

    if max(image.width(), image.height()) > SAVE_MAX_PX:
        image = image.scaled(SAVE_MAX_PX, SAVE_MAX_PX, Qt.KeepAspectRatio,
                             Qt.SmoothTransformation)
    fmt = "PNG" if image.hasAlphaChannel() else "JPG"
    buffer = QBuffer()
    buffer.open(QBuffer.ReadWrite)
    if not image.save(buffer, fmt, -1 if fmt == "PNG" else 92):
        return ""
    data = bytes(buffer.data())
    digest = hashlib.sha1(data).hexdigest()[:10]
    folder = _art_folder()
    path = os.path.join(folder, f"{station_id}-{digest}.{fmt.lower()}")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    except OSError:
        return ""
    return path


def carries_thumbnail(station):
    """Whether this station still shows the small copy it arrived with:
    the file the import wrote (storage._write_featured_art), named after
    the station. A picture the user chose since is never replaced."""
    import storage

    station = station or {}
    path = station.get("custom_art_path") or ""
    if not station.get(FLAG) or not path or not os.path.isfile(path):
        return False
    folder = os.path.join(storage._config_dir(), "artwork")
    return os.path.dirname(os.path.abspath(path)) == \
        os.path.abspath(folder) and \
        os.path.basename(path).startswith(station.get("id") or "?")


class ArtUpgrade(QObject):
    """Watches for stations with shared thumbnails and improves them."""

    changed = pyqtSignal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.lib = library
        self._waiting = []                      # station ids
        self._running = {}                      # station id -> (job, path)
        self._tried = set()

    def queue(self, station, force=False):
        """Look for the full-size picture of this station, soon.

        `force`: a station that just arrived, searched even if an earlier
        copy of it found nothing lately.
        """
        if not station or not carries_thumbnail(station):
            return
        station_id = station.get("id")
        if not station_id or station_id in self._tried:
            return
        checked = station.get(CHECKED) or 0
        if not force and checked and \
                time.time() - checked < RETRY_DAYS * 86400:
            return
        self._tried.add(station_id)
        self._waiting.append(station_id)
        # Not this instant: the list is being rebuilt and looked at.
        QTimer.singleShot(2500, self._next)

    def sweep(self):
        """Anything still carrying a shared thumbnail, at launch."""
        for station in list(self.lib.stations):
            self.queue(station)

    def _next(self):
        while self._waiting and len(self._running) < MAX_RUNNING:
            station_id = self._waiting.pop(0)
            station = self.lib.station_by_id(station_id)
            if station is None or not carries_thumbnail(station):
                continue
            path = station.get("custom_art_path")
            worker = _UpgradeWorker(station, path, self)
            worker.done.connect(self._answered)
            self._running[station_id] = (worker, path)
            worker.start()

    def _answered(self, station_id, result):
        import storage

        _worker, thumb_path = self._running.pop(station_id, (None, ""))
        QTimer.singleShot(0, self._next)
        station = self.lib.station_by_id(station_id)
        if station is None:
            return
        name = storage.station_display_name(station)
        status = result.get("status")
        notes = "; ".join(result.get("notes") or [])
        notes = f" ({notes})" if notes else ""
        # Art the user chose while this ran is theirs, never replaced.
        if station.get("custom_art_path") != thumb_path or \
                not station.get(FLAG):
            station.pop(FLAG, None)
            self.lib.save_stations()
            _log(f"'{name}': kept the picture you chose")
            return
        if status == "found":
            path = save_picture(result["image"], station_id)
            if not path:
                _log(f"'{name}': found full-size art but couldn't save it")
                return
            station["custom_art_path"] = path
            station.pop(FLAG, None)
            station.pop(CHECKED, None)
            storage.set_art_link(station, result.get("url", ""), path)
            self.lib.save_stations()
            # The small copy goes if the import wrote it (it is named
            # after the station) and nothing holds it open (Windows).
            try:
                if os.path.basename(thumb_path).startswith(station_id):
                    os.remove(thumb_path)
            except OSError:
                pass
            host = urlsplit(result.get("url", "")).netloc
            if result.get("via"):
                host = f"{result['via']} ({host})"
            _log(f"'{name}': matched and applied full-size art "
                 f"{result.get('size')} from {host} (the code's copy was "
                 f"{result.get('thumb')}; {result.get('match')})")
            self.changed.emit()
            return
        if status == "none":
            station[CHECKED] = time.time()
            self.lib.save_stations()
            _log(f"'{name}': kept the code's {result.get('thumb')} copy, "
                 f"no online picture matched "
                 f"({result.get('looked', 0)} looked at){notes}")
        elif status == "offline":
            _log(f"'{name}': kept the code's copy, couldn't search "
                 f"(no connection?){notes}")
        elif status == "flat":
            station[CHECKED] = time.time() + 10 * 365 * 86400
            self.lib.save_stations()
            _log(f"'{name}': kept the code's {result.get('thumb')} copy, "
                 "it is too plain to match against")
        else:
            _log(f"'{name}': kept the code's copy ({status}){notes}")

    def stop(self):
        """On quit. Nothing to wait for: `Job.stop_all` has already
        stopped the searches answering, and they end with the app."""
        self._waiting.clear()
        self._running.clear()
