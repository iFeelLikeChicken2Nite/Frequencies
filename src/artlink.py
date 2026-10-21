"""Frequencies — a link to a station's full picture, for QR codes (build 128).

A QR code holds about 2.9 kB, so the picture in it is a 64 px copy. The
code also carries `art_url`, where the full picture is online, and the
importing app fetches it from there (artupgrade.py checks it against the
copy first, so a dead or changed link can't put the wrong picture on).

Pictures that came from the web keep the address they came from
(storage.set_art_link). A picture somebody chose from their own files has
none, so sharing it uploads it to the developer's picture service: a
small Cloudflare Worker in front of an R2 bucket (cloudflare/art-worker.js
in the source folder). Its address is the "art" entry of photo_pack.json,
set in a dev build's Settings > Lyrics and artists and shipped like the
photo pack address. Not a secret: the Worker takes only small pictures.

The upload is `POST <address>/upload` with the picture as the body; the
answer is {"url": ...}. The same picture always gets the same address
(the Worker names it by its sha256), so sharing twice uploads nothing new.
"""

import os

from PyQt5.QtCore import pyqtSignal

from jobs import Job

AGENT = "Frequencies/1.5"
MAX_UPLOAD = 1_500_000          # bytes; the Worker refuses more than 2 MB
SHRINK_PX = 1200                # a bigger picture is scaled to this first


def service():
    """The picture service's address, or "" when there is none."""
    import photopack
    return photopack._address("art").rstrip("/")


def needs_upload(station):
    """A picture of the user's own that a code can't link to yet."""
    import storage
    path = (station.get("custom_art_path") or "").strip()
    return bool(service() and path and os.path.isfile(path)
                and not station.get("art_from_code")
                and not storage.art_link(station))


def _picture_bytes(path):
    """The file as it is when it is small enough, else a smaller copy."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) <= MAX_UPLOAD:
        return data
    import imagelock
    from PyQt5.QtCore import QBuffer, Qt
    image = imagelock.image(path)
    if image.isNull():
        raise ValueError("not a picture")
    if max(image.width(), image.height()) > SHRINK_PX:
        image = image.scaled(SHRINK_PX, SHRINK_PX, Qt.KeepAspectRatio,
                             Qt.SmoothTransformation)
    fmt = "PNG" if image.hasAlphaChannel() else "JPG"
    for quality in (90, 80, 70):
        buffer = QBuffer()
        buffer.open(QBuffer.ReadWrite)
        image.save(buffer, fmt, -1 if fmt == "PNG" else quality)
        data = bytes(buffer.data())
        if len(data) <= MAX_UPLOAD or fmt == "PNG":
            break
    if len(data) > MAX_UPLOAD:
        raise ValueError("too big")
    return data


class Upload(Job):
    """Put one picture on the picture service, off the GUI thread."""

    # station id, picture path, link ("" on failure), reason
    done = pyqtSignal(str, str, str, str)

    def __init__(self, station, parent=None):
        super().__init__(parent)
        self.station_id = str(station.get("id") or "")
        self.path = station.get("custom_art_path") or ""

    def work(self):
        import crashlog
        import requests
        link, reason = "", ""
        try:
            data = _picture_bytes(self.path)
            answer = requests.post(
                service() + "/upload", data=data, timeout=30,
                headers={"User-Agent": AGENT,
                         "Content-Type": "application/octet-stream"})
            if answer.status_code != 200:
                reason = f"the picture service said {answer.status_code}"
            else:
                link = str(answer.json().get("url") or "")
                if not link.startswith(("https://", "http://")):
                    link, reason = "", "the picture service gave no link"
        except Exception as exc:                # noqa: BLE001
            reason = f"{exc.__class__.__name__}"
        crashlog.write("[art] shared picture "
                     + (f"uploaded: {link}" if link
                        else f"not uploaded ({reason})"))
        self.send(self.done, self.station_id, self.path, link, reason)
