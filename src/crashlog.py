"""
crashlog.py - Capture why the app died.

A Qt qFatal() prints its reason to stderr and then calls abort(). When
the app is launched from a desktop icon there is no terminal, so that
message is lost and all that survives is a core dump full of Qt frames
with no explanation - which is exactly as useful as it sounds.

So we install three catchers up front:

  * a Qt message handler, which writes every warning/critical/fatal Qt
    emits into the log file *before* Qt aborts
  * sys.excepthook, for Python exceptions that escape
  * faulthandler, which dumps the Python stack on a hard signal

The log lives next to the config so it is easy to find and attach:

    ~/.local/state/frequencies/frequencies.log
"""
import atexit
import datetime
import faulthandler
import os
import sys
import threading
import traceback

APP_DIR_NAME = "frequencies"
LOG_NAME = "frequencies.log"
MAX_BYTES = 512 * 1024

_log_path = None
_handle = None
_lock = threading.RLock()


def log_dir():
    import appdirs
    base = appdirs.state_base()
    path = os.path.join(base, APP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def log_path():
    return os.path.join(log_dir(), LOG_NAME)


def write(line):
    """Append one line, flushed immediately - a crash is coming.

    Flushed, not fsync'd. A flush hands the line to the system, and the
    system keeps it even if the app dies the next moment — that is all a
    crash log needs. fsync also waits for the disk itself, and on the
    user's PC that took 300 ms on the GUI thread mid-song and 4.3 s at
    quit (stall log, 2026-10-01; build 55, task 24). Only a power cut
    can lose a flushed line.
    """
    global _handle
    if _handle is None:
        return
    try:
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        # One line at a time: several threads write here.
        with _lock:
            _handle.write(f"{stamp}  {line}\n")
            _handle.flush()
    except Exception:
        pass


def report_dir():
    """Where "Create log" puts its file: Documents, else the home folder.
    Somewhere the user already looks, not the hidden state folder."""
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    return docs if os.path.isdir(docs) else home


def create_report(version="", dev=False):
    """Write one text file holding this run's log and the one before it,
    and return its path. Raises OSError if it can't be written.

    The log never holds passwords or sign-in tokens (those are kept out of
    it on purpose), so the file is safe to hand to someone helping.
    """
    try:
        if _handle is not None:
            _handle.flush()
    except Exception:                           # noqa: BLE001
        pass
    path = os.path.join(report_dir(), datetime.datetime.now().strftime(
        "frequencies-log-%Y%m%d-%H%M%S.txt"))
    parts = []
    for name in (log_path() + ".1", log_path()):
        try:
            with open(name, "r", encoding="utf-8", errors="replace") as f:
                parts.append((os.path.basename(name), f.read()))
        except OSError:
            continue
    with open(path, "w", encoding="utf-8") as out:
        out.write(f"Frequencies {version}"
                  f"{' (dev build)' if dev else ''} log\n")
        out.write("Made " + datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S") + "\n")
        out.write(f"Python {sys.version.split()[0]}  |  {sys.platform}\n")
        for label, text in parts:
            out.write(f"\n{'=' * 20} {label} {'=' * 20}\n")
            out.write(text)
    return path


_STRAY_LOGGED = 0


def install_stray_watch(app):
    """Log any button, label or field shown as a window of its own.

    One that is shown with no parent is a little window flashing up in the
    corner (task 101: the tag manager's delete buttons did that, one per
    tag). The line names the widget and the stack that showed it; at most
    20 per run.
    """
    from PyQt5.QtCore import QEvent, QObject, Qt
    from PyQt5.QtWidgets import (QAbstractButton, QCheckBox, QComboBox,
                                 QLabel, QLineEdit)
    kinds = (QAbstractButton, QLabel, QLineEdit, QCheckBox, QComboBox)

    class StrayWatch(QObject):
        def eventFilter(self, obj, event):
            global _STRAY_LOGGED
            try:
                if (event.type() == QEvent.Show and _STRAY_LOGGED < 20
                        and isinstance(obj, kinds) and obj.isWindow()
                        and obj.windowType() == Qt.Window):
                    _STRAY_LOGGED += 1
                    text = str(obj.text())[:40] if hasattr(obj, "text") else ""
                    where = "".join(traceback.format_stack(limit=8)[:-2])
                    write(f"[window] a {type(obj).__name__} "
                          f"'{obj.objectName()}' {text!r} was shown with no "
                          f"parent ({obj.width()}x{obj.height()}); shown "
                          f"from:\n{where}")
            except Exception:               # noqa: BLE001
                pass
            return False

    watch = StrayWatch(app)
    app.installEventFilter(watch)
    return watch


def _rotate():
    try:
        path = log_path()
        if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass


def _qt_handler(mode, context, message):
    """Called by Qt for every message it emits."""
    try:
        from PyQt5.QtCore import QtMsgType
        names = {
            QtMsgType.QtDebugMsg: "qt-debug",
            QtMsgType.QtInfoMsg: "qt-info",
            QtMsgType.QtWarningMsg: "qt-warning",
            QtMsgType.QtCriticalMsg: "qt-CRITICAL",
            QtMsgType.QtFatalMsg: "qt-FATAL",
        }
        label = names.get(mode, "qt")
    except Exception:
        label = "qt"

    where = ""
    try:
        if context is not None and context.file:
            where = f"  ({context.file}:{context.line})"
    except Exception:
        pass

    write(f"[{label}] {message}{where}")

    # A fatal is about to abort(); record the Python side too, since
    # that's what actually tells us which call did it.
    if label == "qt-FATAL":
        write("[qt-FATAL] Python stack at the point of the fatal:")
        for line in traceback.format_stack():
            for part in line.rstrip().splitlines():
                write("    " + part)

    # Keep the normal stderr behavior as well.
    try:
        sys.__stderr__.write(f"{label}: {message}\n")
        sys.__stderr__.flush()
    except Exception:
        pass


def _excepthook(exc_type, exc_value, exc_tb):
    write("[python] Uncaught exception:")
    for line in traceback.format_exception(exc_type, exc_value, exc_tb):
        for part in line.rstrip().splitlines():
            write("    " + part)
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def install(version="", extra=None):
    """Start logging. Safe to call once, early, before any Qt objects."""
    global _handle, _log_path
    try:
        _rotate()
        _log_path = log_path()
        _handle = open(_log_path, "a", encoding="utf-8")
    except OSError:
        _handle = None
        return None

    write("=" * 60)
    write(f"Frequencies {version} starting")
    write(f"python {sys.version.split()[0]}  |  pid {os.getpid()}")
    for key in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "QT_QPA_PLATFORM",
                "QT_QPA_PLATFORMTHEME", "QT_PLUGIN_PATH", "QT_STYLE_OVERRIDE",
                "WAYLAND_DISPLAY", "DISPLAY", "LC_ALL", "LANG"):
        value = os.environ.get(key)
        if value:
            write(f"env {key}={value}")
    if extra:
        for line in extra:
            write(line)

    try:
        faulthandler.enable(file=_handle, all_threads=True)
    except Exception:
        pass

    sys.excepthook = _excepthook

    try:
        from PyQt5.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_qt_handler)
    except Exception:
        pass

    # Route the standard logging module into this file too, and to
    # stderr, so diagnostics from modules that use logging (the
    # visualizer, projectM) end up somewhere findable instead of
    # vanishing. Running from a terminal then shows them live.
    try:
        import logging

        class _CrashlogHandler(logging.Handler):
            def emit(self, record):
                try:
                    write(f"{record.name}: {record.getMessage()}")
                    if record.exc_info:
                        write(traceback.format_exception(*record.exc_info))
                except Exception:       # noqa: BLE001
                    pass

        root = logging.getLogger("frequencies")
        root.setLevel(logging.INFO)
        root.addHandler(_CrashlogHandler())
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        root.addHandler(stream)
    except Exception:                   # noqa: BLE001
        pass

    atexit.register(lambda: write("clean exit"))
    return _log_path
