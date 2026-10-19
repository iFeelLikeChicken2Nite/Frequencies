"""Frequencies — replacing a shared thumbnail with the real artwork.

A station that arrives by QR code brings its cover as a 64px JPEG,
because that is what fits. It looks right in the list and poor anywhere
else, and the full-size original is sitting on the internet where it came
from.

So: when a station arrives that way, go and look for the proper one. In
the background, without asking, and without touching the thumbnail unless
something better is actually found — a station whose artwork flickers
between two pictures on a slow connection is worse than one with a small
picture.

Matched on the stream URL first. Two stations can share a name; almost
none share an address.
"""

import os

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from jobs import Job

# Below this, the shared thumbnail is not worth replacing with it.
MIN_BETTER_PX = 200

# What marks a station as carrying a shared thumbnail.
FLAG = "art_from_code"


class _UpgradeWorker(Job):
    """Find a better cover for one station, off the GUI thread.

    A jobs.Job (daemon thread), not a QThread, so quitting mid-search
    can't crash the app (build 54, task 14)."""

    found = pyqtSignal(str, bytes)              # station id, image bytes

    def __init__(self, station_id, name, url, parent=None):
        super().__init__(parent)
        self.station_id = station_id
        self.name = name
        self.url = url

    def work(self):
        try:
            data = self._search()
        except Exception:                       # noqa: BLE001
            data = b""
        if data:
            self.send(self.found, self.station_id, data)

    def _search(self):
        import radiobrowser
        import requests

        candidates = []
        # By URL first: a name can belong to a dozen stations, an address
        # to one.
        for query in (self.url, self.name):
            if not query:
                continue
            try:
                rows = radiobrowser.search_stations(query, limit=8)
            except Exception:                   # noqa: BLE001
                continue
            for row in rows:
                icon = (row.get("favicon") or "").strip()
                if not icon:
                    continue
                same = (row.get("url") or "").lower() == (self.url or "").lower()
                candidates.append((0 if same else 1, icon))
            if candidates:
                break

        for _rank, icon in sorted(candidates):
            try:
                response = requests.get(
                    icon, timeout=10,
                    headers={"User-Agent": "Frequencies/1.5"})
                response.raise_for_status()
                data = response.content
            except Exception:                   # noqa: BLE001
                continue
            if self._big_enough(data):
                return data
        return b""

    @staticmethod
    def _big_enough(data):
        """Only an actual improvement counts.

        A favicon is often 32px, which is worse than the thumbnail it
        would replace — so the size is checked before anything is kept.
        """
        from PyQt5.QtGui import QImage

        if not data or len(data) > 4 * 1024 * 1024:
            return False
        image = QImage()
        if not image.loadFromData(data):
            return False
        return min(image.width(), image.height()) >= MIN_BETTER_PX


class ArtUpgrade(QObject):
    """Watches for stations with shared thumbnails and improves them."""

    changed = pyqtSignal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.lib = library
        self._workers = []
        self._tried = set()

    def queue(self, station, delay_ms=2500):
        """Look for better art for this station, shortly.

        Delayed because this runs the moment a station is added, which is
        the moment the list is rebuilding and the user is looking at it.
        """
        if not station or not station.get(FLAG):
            return
        station_id = station.get("id")
        if not station_id or station_id in self._tried:
            return
        self._tried.add(station_id)
        QTimer.singleShot(delay_ms, lambda: self._start(station))

    def sweep(self):
        """Anything still carrying a shared thumbnail, at launch."""
        for index, station in enumerate(self.lib.stations):
            if station.get(FLAG):
                self.queue(station, delay_ms=4000 + index * 1200)

    def _start(self, station):
        import storage

        worker = _UpgradeWorker(station.get("id", ""),
                                storage.station_display_name(station),
                                station.get("url", ""), self)
        worker.found.connect(self._replace)
        worker.finished.connect(lambda w=worker: self._reap(w))
        self._workers.append(worker)
        worker.start()

    def _reap(self, worker):
        # The Job deletes itself once it has answered.
        if worker in self._workers:
            self._workers.remove(worker)

    def _replace(self, station_id, data):
        """Write the better picture and point the station at it."""
        import storage

        station = self.lib.station_by_id(station_id)
        if station is None:
            return
        folder = os.path.join(os.path.dirname(storage.settings_path()),
                              "station-art")
        try:
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"{station_id}-full.png")
            from PyQt5.QtGui import QImage
            image = QImage()
            if not image.loadFromData(data) or not image.save(path, "PNG"):
                return
        except OSError:
            return

        station["custom_art_path"] = path
        station["art_source"] = "custom"
        # The flag goes: this is the real cover now, and looking again
        # every launch would be pointless traffic.
        station.pop(FLAG, None)
        self.lib.save_stations()
        self.changed.emit()

    def stop(self):
        """On quit. Nothing to wait for: `Job.stop_all` has already
        stopped the searches answering, and they end with the app."""
        self._workers.clear()
