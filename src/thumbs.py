"""
thumbs.py - A small on-disk cache of cover art for the song history.

The history can hold thousands of songs, and re-downloading a cover
every time the window opens would be slow and rude to the servers
hosting them. So each cover is downsized and kept locally.

Two decisions worth stating:

  * They are stored small (128 px, JPEG). The history shows them at
    thumbnail size, so keeping the full-resolution art would waste
    space to no visible benefit - a typical thumbnail lands around
    5-10 KB rather than several hundred.
  * The cache is capped at 1 GB and evicted least-recently-used. At
    ~8 KB each that is room for well over a hundred thousand songs, so
    in practice the cap never bites; it exists so a long-running
    install cannot quietly fill a disk.
"""
import os
import re
import time

DEFAULT_LIMIT_BYTES = 1024 * 1024 * 1024      # 1 GB
THUMB_PX = 128
JPEG_QUALITY = 80

# Only sweep occasionally - stat-ing every file on every save would be
# silly when a save adds ~8 KB to a 1 GB budget.
SWEEP_EVERY = 200
_saves_since_sweep = 0


_cache_dir = None


def cache_dir():
    """Where thumbnails live. Worked out once, not on every lookup.

    This asked the filesystem to create the directory every single time,
    and it is called once per history entry — so identifying a song on a
    station with a long history meant hundreds of makedirs calls in a
    row, on the GUI thread. The path does not change while the app is
    running.
    """
    global _cache_dir
    if _cache_dir is not None:
        return _cache_dir
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    path = os.path.join(base, "frequencies", "thumbs")
    os.makedirs(path, exist_ok=True)
    _cache_dir = path
    return path


def _filename(key):
    """One file per song key. Hashed so odd characters in titles can't
    escape the directory or blow past filename length limits."""
    import hashlib
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return digest + ".jpg"


def path_for(key):
    return os.path.join(cache_dir(), _filename(key))


def has(key):
    return os.path.exists(path_for(key))


def load(key):
    """Returns raw bytes, or None. Touches the file so the LRU sweep
    knows it's still wanted."""
    path = path_for(key)
    if not os.path.exists(path):
        return None
    try:
        os.utime(path, None)
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def save(key, image_bytes, limit_bytes=DEFAULT_LIMIT_BYTES):
    """Downscale and store a cover. Returns the path, or None if the
    image couldn't be decoded.

    Needs Qt for the scaling, which is always available here - this is
    only ever called from the GUI process.
    """
    global _saves_since_sweep
    if not image_bytes:
        return None

    from PyQt5.QtGui import QImage

    # Under the same lock as the artwork scaler's thread. Decoding here
    # while it decoded there is what aborted the app.
    from imagelock import LOCK
    image = QImage()
    with LOCK:
        loaded = image.loadFromData(image_bytes)
    if not loaded:
        return None
    if image.width() > THUMB_PX or image.height() > THUMB_PX:
        from PyQt5.QtCore import Qt
        image = image.scaled(THUMB_PX, THUMB_PX,
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)

    path = path_for(key)
    tmp = path + ".tmp"
    if not image.save(tmp, "JPG", JPEG_QUALITY):
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None
    try:
        os.replace(tmp, path)
    except OSError:
        return None

    _saves_since_sweep += 1
    if _saves_since_sweep >= SWEEP_EVERY:
        _saves_since_sweep = 0
        enforce_limit(limit_bytes)
    return path


def total_bytes():
    total = 0
    try:
        for name in os.listdir(cache_dir()):
            try:
                total += os.path.getsize(os.path.join(cache_dir(), name))
            except OSError:
                continue
    except OSError:
        return 0
    return total


def enforce_limit(limit_bytes=DEFAULT_LIMIT_BYTES):
    """Evict least-recently-used thumbnails until the cache fits.

    Returns how many files were removed.
    """
    directory = cache_dir()
    try:
        names = os.listdir(directory)
    except OSError:
        return 0

    entries = []
    total = 0
    for name in names:
        full = os.path.join(directory, name)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        entries.append((stat.st_atime, stat.st_size, full))
        total += stat.st_size

    if total <= limit_bytes:
        return 0

    entries.sort()          # oldest access first
    removed = 0
    for _atime, size, full in entries:
        if total <= limit_bytes:
            break
        try:
            os.remove(full)
        except OSError:
            continue
        total -= size
        removed += 1
    return removed


def clear():
    """Wipe the cache. The covers can always be fetched again, so this
    is safe in a way that clearing favorites is not."""
    directory = cache_dir()
    removed = 0
    try:
        for name in os.listdir(directory):
            try:
                os.remove(os.path.join(directory, name))
                removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed


def human_size(num_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} GB"
