"""One lock for decoding images, shared by every thread that does it.

Qt's image readers are not safe to run on two threads at once. The
artwork scaler decodes in the background while a dozen places on the
main thread decode thumbnails, logos, photos and covers — and when two
overlapped, the reader deadlocked on its own internal mutex and the app
aborted. The log caught it twice in one session.

The lock is put on the decode itself, not on each caller: install()
wraps QImage.loadFromData and QPixmap.loadFromData so every call in the
app takes it, including ones written later. It is re-entrant, so code
that already holds it (the scaler) can call through without deadlocking
on itself.

Build 65 (task 54): reading a picture straight from a FILE never took
the lock. `QPixmap(path)` / `QImage(path)` decode in the constructor,
and a constructor can't be wrapped like a method — so the app aborted
again, in `QPixmap(path)` inside `shell._from_file` (an icon loaded at a
station change from the media keys) while the artwork scaler was
decoding a cover. Now:
- install() also wraps `QImage.load`, `QPixmap.load` and
  `QImageReader.read` (the other ways in);
- file reads go through `pixmap(path)`, `image(path)` and `icon(path)`
  here, never `QPixmap(path)` / `QImage(path)` / `QIcon(path)` directly.
  A `QIcon(path)` is worse still: it decodes later, inside a paint.
A wait of over WAIT_LOG_MS for the lock is logged as `[image] …`, so a
log shows when the GUI thread queued behind another decode (where it
used to crash).
"""

import threading
import time

LOCK = threading.RLock()
_installed = False

# A GUI-thread wait longer than this goes in the log.
WAIT_LOG_MS = 150
# At most this many wait lines per run — enough to see a pattern.
_WAIT_LOG_MAX = 40
_wait_logged = 0


def _log(line):
    try:
        import crashlog
        crashlog.write(line)
    except Exception:  # noqa: BLE001
        pass


class _Held:
    """`with LOCK`, timing the wait for it."""

    def __init__(self, what):
        self.what = what

    def __enter__(self):
        global _wait_logged
        started = time.monotonic()
        LOCK.acquire()
        waited = (time.monotonic() - started) * 1000
        if waited >= WAIT_LOG_MS and _wait_logged < _WAIT_LOG_MAX:
            _wait_logged += 1
            _log(f"[image] waited {waited:.0f} ms for another picture to "
                 f"finish decoding ({self.what}, "
                 f"{threading.current_thread().name})")
        return self

    def __exit__(self, *exc):
        LOCK.release()
        return False


def held(what="decode"):
    """The lock as a context manager that logs a long wait."""
    return _Held(what)


def install():
    global _installed
    if _installed:
        return
    from PyQt5.QtGui import QImage, QPixmap, QImageReader

    def wrap(cls, name):
        original = getattr(cls, name)

        def guarded(self, *args, _original=original, **kwargs):
            with LOCK:
                return _original(self, *args, **kwargs)

        setattr(cls, name, guarded)

    for cls in (QImage, QPixmap):
        wrap(cls, "loadFromData")
        wrap(cls, "load")
    wrap(QImageReader, "read")
    _installed = True


def _short(path):
    import os
    return os.path.basename(str(path)) or str(path)


def image(path):
    """A QImage of the file at `path`, decoded under the lock. Any thread."""
    from PyQt5.QtGui import QImage
    with held(_short(path)):
        return QImage(path)


def pixmap(path):
    """A QPixmap of the file at `path`, decoded under the lock. GUI thread."""
    from PyQt5.QtGui import QPixmap
    with held(_short(path)):
        return QPixmap(path)


def icon(path):
    """A QIcon of the file at `path`, decoded now (under the lock).

    `QIcon(path)` would decode later, at its first paint, outside any
    lock — the same crash, just harder to see.
    """
    from PyQt5.QtGui import QIcon
    pm = pixmap(path)
    return QIcon(pm) if not pm.isNull() else QIcon()
