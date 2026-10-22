"""Frequencies — entry point.

This IS main.py in a packaged build: the AppImage runs main.py, so the new
shell is installed under that name and the old window is kept beside it as
main_legacy.py for reference. Nothing imports either, so the swap is just
the filename.

One ordering point: `Player.__init__` calls `force_c_numeric_locale()`
immediately before `mpv.MPV()`, because Qt has already changed LC_NUMERIC by
then. So the QApplication is built first and the Player second, exactly as
before. Don't reverse it.
"""

import os
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def app_icon():
    """The Frequencies icon, at every size we ship.

    Built from the individual PNGs rather than one file so the desktop can
    pick the right size — a 256px icon scaled down for a tray entry looks
    noticeably worse than the 16px one.

    Searched in several places because the layout differs between running
    from a checkout and running from the AppImage, where the code lives in
    usr/share/frequencies. Lifted out of the old main.py so this file has
    no reason to import it.
    """
    from PyQt5.QtCore import QSize
    from PyQt5.QtGui import QIcon

    here = os.path.dirname(os.path.abspath(__file__))
    roots = (os.path.join(here, "icons"),
             os.path.join(here, "..", "icons"),
             here,
             os.path.join(here, ".."))

    icon = QIcon()
    for root in roots:
        found = False
        for size in (16, 24, 32, 48, 64, 128, 1):
            path = os.path.join(root, f"frequencies_{size}.png")
            if os.path.exists(path):
                icon.addFile(path, QSize(size, size))
                found = True
        if found:
            return icon

    # A single-size fallback, then give up gracefully: an empty QIcon means
    # an invisible tray entry, so the caller checks isNull().
    for root in roots:
        path = os.path.join(root, "frequencies.png")
        if os.path.exists(path):
            icon.addFile(path)
            return icon
    return icon


# How to install the libraries a system is most likely to lack.
INSTALL_HINTS = {
    "libOpenGL.so.0": ("libopengl0", "libglvnd-opengl", "libglvnd",
                       "libOpenGL0"),
    "libGLdispatch.so.0": ("libglvnd0", "libglvnd", "libglvnd",
                           "libglvnd"),
    "libGL.so.1": ("libgl1", "libglvnd-glx", "libglvnd", "libGL1"),
    "libEGL.so.1": ("libegl1", "libglvnd-egl", "libglvnd", "libEGL1"),
    "libjack.so.0": ("libjack-jackd2-0", "pipewire-jack-audio-connection-kit-libs",
                     "pipewire-jack", "libjack0"),
    "libpipewire-0.3.so.0": ("libpipewire-0.3-0t64", "pipewire-libs",
                             "libpipewire", "libpipewire-0_3-0"),
    "libasound.so.2": ("libasound2t64", "alsa-lib", "alsa-lib",
                       "libasound2"),
    "libmpv.so.2": ("libmpv2", "mpv-libs", "mpv", "libmpv2"),
}


def windows_dll_folders():
    """Windows (WP1): let python-mpv find libmpv-2.dll beside the app.

    python-mpv looks for it on PATH. The packaged app keeps it next to
    Frequencies.exe (or in its _internal folder); from source, in src/.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    folders = [getattr(sys, "_MEIPASS", None),
               os.path.dirname(sys.executable), here,
               os.path.join(here, "src")]
    for folder in folders:
        if folder and os.path.isdir(folder):
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
            try:
                os.add_dll_directory(folder)
            except (AttributeError, OSError):
                pass


def hold_running_mutex():
    """Windows (build 125): every Frequencies.exe holds this named mutex
    while it runs, so an update's installer can wait for all of them,
    the browser helper too, to end (frequencies.iss, /UPDATE)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hold_running_mutex.handle = ctypes.windll.kernel32.CreateMutexW(
            None, False, "FrequenciesRunning")
    except (AttributeError, OSError):
        pass


def _log_other_copies():
    """Says so in the log when another copy of Frequencies is already
    running (task 205): two copies share one settings file. Linux only."""
    if not os.path.isdir("/proc"):
        return
    import crashlog
    me = os.getpid()
    others = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == me:
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as f:
                parts = f.read().split(b"\0")
        except OSError:
            continue
        if any(p.endswith(b"/main.py") and b"frequencies" in p.lower()
               for p in parts) and b"--webview-helper" not in parts:
            others.append(name)
    if others:
        crashlog.write("[settings] another copy of Frequencies is running "
                       f"(pid {', '.join(others)}); both use the same "
                       "settings file")


def missing_library_window(error):
    """A plain window naming the missing library and its package."""
    import re
    from PyQt5.QtWidgets import QMessageBox
    found = re.search(r"(lib[\w.+-]+\.so[\d.]*)", error)
    name = found.group(1) if found else "a system library"
    lines = [f"Frequencies can't start because {name} is missing "
             "from this computer."]
    hint = INSTALL_HINTS.get(name)
    if sys.platform == "win32":
        lines = ["Frequencies can't start because its sound engine "
                 "(libmpv-2.dll) didn't load.", "",
                 "Put libmpv-2.dll back in the Frequencies folder, next "
                 "to Frequencies.exe, then open Frequencies again."]
    elif hint:
        apt, dnf, pacman, zypper = hint
        lines += ["", "Install it, then open Frequencies again:",
                  f"Ubuntu, Debian, Mint, Pop!_OS:  sudo apt install {apt}",
                  f"Fedora:  sudo dnf install {dnf}",
                  f"Arch, Manjaro:  sudo pacman -S {pacman}",
                  f"openSUSE:  sudo zypper install {zypper}"]
    else:
        lines += ["", "Install the package that provides it with your "
                  "system's software manager, then open Frequencies again."]
    lines += ["", f"Details: {error}"]
    box = QMessageBox(QMessageBox.Critical, "Frequencies", "\n".join(lines))
    box.setTextInteractionFlags(Qt.TextSelectableByMouse)
    # Wide enough that each install command stays on one line.
    box.setStyleSheet("QLabel#qt_msgbox_label { min-width: 560px; }")
    box.exec_()


def main():
    # Before anything Qt: a qFatal() prints to a stderr nobody sees when the
    # app is launched from a desktop icon, and crashlog is what catches it.
    # Before anything decodes an image — see imagelock.py.
    import imagelock
    imagelock.install()
    # brat mode (task 48): lowers painted text and window titles while a
    # song from brat plays. Wraps QPainter.drawText, so before any window.
    import brat
    brat.install()

    import crashlog
    crashlog.install(version="1.5")
    # Dev or user build (task 49): dev-only things ask devmode.is_dev().
    import devmode
    crashlog.write("[dev] " + devmode.describe())

    if sys.platform == "win32":
        windows_dll_folders()
        hold_running_mutex()

    if sys.platform.startswith("linux"):
        # X11: WM_CLASS is ("frequencies", "Frequencies"), matching the
        # launcher entry's StartupWMClass (task 189).
        os.environ.setdefault("RESOURCE_NAME", "frequencies")
    app = QApplication(sys.argv)
    # Updates (build 125, task 177): an installer that didn't finish last
    # time runs again before any window (Windows), and Windows shutting
    # down or signing out leaves the update for the next quit.
    import updater
    if updater.before_window():
        return 0
    app.commitDataRequest.connect(updater.session_ending)
    if os.environ.get("FREQ_FALLBACK_LIBS"):
        crashlog.write("[startup] using bundled copies of: "
                       + os.environ["FREQ_FALLBACK_LIBS"])
    if os.environ.get("FREQ_GLIBC"):
        # Task 185: the app runs on the glibc it carries.
        crashlog.write("[startup] glibc: " + os.environ["FREQ_GLIBC"])
    if "/frequencies/tls/" in os.environ.get("GNUTLS_SYSTEM_PRIORITY_FILE", ""):
        crashlog.write("[startup] using bundled TLS settings (no "
                       "/etc/crypto-policies on this system)")

    try:
        import player
    except OSError as exc:
        # libmpv (or something it needs) wouldn't load (task 160): say
        # which library, and how to get it, instead of a traceback.
        crashlog.write(f"[startup] could not load the audio engine: {exc}")
        missing_library_window(str(exc))
        return 1
    import storage

    settings = storage.load_settings()
    _log_other_copies()
    stations = storage.load_stations()
    storage.apply_identify_default(stations, settings)
    storage.apply_station_eq_default(stations, settings)
    storage.lower_old_stall_timeout(settings)
    storage.raise_old_noise_volume(settings)
    storage.apply_new_defaults_120(settings)
    # A theme that was removed (Graphite, Oxide, Cathode: build 130), or
    # one from a newer build, shows as the default, without saving that
    # over the choice (task 205).
    import themes
    shown_theme = settings.get("theme", "midnight")
    if shown_theme not in themes.THEMES:
        shown_theme = themes.DEFAULT
    history = storage.load_history()

    # ---- NEW: theme -------------------------------------------------------
    import theme
    from bridge import Library
    from mainwindow import FrequenciesWindow

    # Wayland has no per-window icon protocol: a taskbar matches a window to
    # a .desktop file by app_id and takes the icon from there, so naming the
    # desktop entry is what makes the icon appear at all — including in the
    # tray, which otherwise warns that no icon is set.
    from PyQt5.QtGui import QGuiApplication
    import desktopentry
    QGuiApplication.setDesktopFileName(desktopentry.APP_ID)
    app.setApplicationName(desktopentry.WM_CLASS)
    # A launcher entry so the dock shows the icon, not a gear (task 189).
    desktopentry.install(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "icons"))

    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    # Wraps the current style rather than replacing it, so this only
    # changes the tooltip timings and leaves everything else alone.
    app.setStyle(theme.AppStyle(app.style()))
    # Before the palette is built, so a chosen theme is what the first
    # frame is drawn in rather than something that changes a moment later.
    theme.apply_theme(shown_theme)
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.stylesheet())

    # No window animations of our own.
    #
    # The compositor already animates windows, and does it with the
    # user's own settings — ours could only ever be a second opinion
    # fighting the first. Removed rather than made optional: a setting
    # for "animate twice" is not a setting anyone wants.
    app.setFont(QFont("Inter", 10))

    # Right-click > Paste in text fields: reads the clipboard when the
    # menu opens, because on Wayland it is gone by the time Qt's own
    # Paste looks (task 20).
    import editmenu
    editmenu.install(app)

    library = Library(stations, settings, history)

    # ---- NEW: the window, and the player it drives ------------------------
    #
    # Chicken and egg: Player wants its callbacks at construction time, and
    # the window wants a Player. A one-element holder breaks the cycle, and
    # a callback arriving before the window exists is simply dropped.
    holder = {}

    def dispatch(name):
        def fire(*args):
            win = holder.get("win")
            if win is not None:
                getattr(win, name)(*args)
        return fire

    engine = player.Player(
        on_state=dispatch("on_state"),
        on_metadata=dispatch("on_metadata"),
        on_connected=dispatch("on_connected"),
        on_stalled=dispatch("on_stalled"),
        # A track that plays to its end is not a dead station.
        #
        # Without this the end of a YouTube video looked like a stream
        # that had stopped, so the stall logic moved on to the next
        # station instead of the next song in the playlist.
        on_track_finished=dispatch("on_track_finished"),
    )
    # A file's own tags (album, artist, title) for brat mode (task 48).
    engine.on_tags = dispatch("on_tags")
    engine.set_normalize(settings.get("normalize", True))
    engine.set_noise_enabled(settings.get("noise_between_stations", True))
    engine.set_noise_volume(settings.get("noise_volume", 100))
    engine.set_stall_timeout(settings.get("stall_timeout", 6))
    engine.set_audio_device(settings.get("audio_device", "auto"))

    # Says what blocked the interface, when something does. Created
    # before the window, because the window is the thing that stalls.
    import stallwatch
    stall_watch = stallwatch.start(settings)    # noqa: F841
    # Names any button or label shown as a window of its own (task 101).
    crashlog.install_stray_watch(app)

    win = FrequenciesWindow(library, engine)
    holder["win"] = win

    # ---- NEW: identification, lyrics, artwork, station logos --------------
    from feeds import Feeds
    feeds = Feeds(win, library, engine)
    win.attach_feeds(feeds)

    # ---- NEW: MPRIS, tray icon, health monitor, sleep timer ---------------
    from desktop import Desktop
    integration = Desktop(win, library, engine, icon=icon)
    integration.start()
    win.on_desktop(integration)

    last = library.station_by_id(settings.get("last_station_id"))
    if last is not None:
        win.stations_page.list.set_playing(last.get("id"), False)

    win.show()
    try:
        return app.exec_()
    finally:
        # Order matters: stop the threads that might still be talking to the
        # window, then the window, then mpv.
        win.shutdown()
        integration.shutdown()
        engine.shutdown()
        # Then get rid of the window while Qt is still here.
        #
        # Left alone, PyQt deletes it on its way out, in no set order —
        # often after the QApplication is already gone, and a window
        # destroyed after its application crashed the app on quit about
        # half the time (a segfault in PyQt's exit clean-up, build 46).
        from PyQt5 import sip
        try:
            sip.delete(win)
        except RuntimeError:
            pass
        # Last: a downloaded build goes in place only on a real quit
        # (hiding to the tray never gets here).
        updater.apply_on_quit()


if __name__ == "__main__":
    if getattr(sys, "frozen", False) and sys.argv[1:2] == ["--webview-helper"]:
        # Windows (WP2): the in-app browser runs as a second copy of the
        # exe (there is no separate Python to run webview_helper.py).
        del sys.argv[1]
        hold_running_mutex()
        import webview_helper
        sys.exit(webview_helper.main())
    sys.exit(main())
