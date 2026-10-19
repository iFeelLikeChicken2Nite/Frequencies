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

    app = QApplication(sys.argv)

    import player
    import storage

    settings = storage.load_settings()
    stations = storage.load_stations()
    storage.apply_identify_default(stations, settings)
    storage.apply_station_eq_default(stations, settings)
    storage.lower_old_stall_timeout(settings)
    storage.raise_old_noise_volume(settings)
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
    QGuiApplication.setDesktopFileName("frequencies")
    app.setApplicationName("Frequencies")

    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    # Wraps the current style rather than replacing it, so this only
    # changes the tooltip timings and leaves everything else alone.
    app.setStyle(theme.AppStyle(app.style()))
    # Before the palette is built, so a chosen theme is what the first
    # frame is drawn in rather than something that changes a moment later.
    theme.apply_theme(settings.get("theme", "midnight"))
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
    engine.set_noise_volume(settings.get("noise_volume", 70))
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


if __name__ == "__main__":
    sys.exit(main())
