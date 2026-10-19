"""Frequencies — looking a song up somewhere else.

The history knows what was playing; the obvious next question is "where
can I hear that properly". These are search URLs rather than an
integration: no accounts, no API keys, nothing to break when a service
changes its terms.

Opening a link can hand the request to a native app if one is installed —
Cider for Apple Music, the Spotify client, and so on — which is faster and
keeps you out of a browser. That is a decision about your machine rather
than about this app, so it is asked once and remembered.
"""

import shutil
import subprocess
import urllib.parse

from PyQt5.QtWidgets import QCheckBox, QMessageBox

# name, search URL template, and the desktop apps that can take it
SERVICES = (
    ("Apple Music", "https://music.apple.com/us/search?term={q}",
     ("cider", "Cider", "apple-music")),
    ("YouTube Music", "https://music.youtube.com/search?q={q}",
     ("youtube-music", "th-ch.youtube-music")),
    ("Spotify", "https://open.spotify.com/search/{q}",
     ("spotify",)),
    ("Deezer", "https://www.deezer.com/search/{q}",
     ("deezer",)),
    ("Tidal", "https://listen.tidal.com/search?q={q}",
     ("tidal", "tidal-hifi")),
)


def query_for(song, artist):
    return urllib.parse.quote(" ".join(p for p in (artist, song) if p))


def installed_app(candidates):
    """The first of these that is actually on the system, or ""."""
    for name in candidates:
        if shutil.which(name):
            return name
        # Flatpaks are not on PATH; ask flatpak whether it has one.
        if "." in name and shutil.which("flatpak"):
            try:
                out = subprocess.run(
                    ["flatpak", "info", name], capture_output=True,
                    timeout=4, text=True)
                if out.returncode == 0:
                    return f"flatpak:{name}"
            except Exception:                   # noqa: BLE001
                pass
    return ""


def ask_permission(parent, settings, app_name, service):
    """Once, unless they tick the box.

    Launching another program is not something to do quietly the first
    time, and not something to ask about every time either.
    """
    if settings.get("open_in_apps") == "always":
        return True
    if settings.get("open_in_apps") == "never":
        return False

    box = QMessageBox(parent)
    box.setWindowTitle("Open in " + service)
    box.setText(f"{service} is installed on this machine.\n\n"
                f"Open the search there instead of in a browser?")
    box.setIcon(QMessageBox.Question)
    remember = QCheckBox("Remember this choice")
    box.setCheckBox(remember)
    box.addButton("Use the app", QMessageBox.YesRole)
    browser = box.addButton("Use the browser", QMessageBox.NoRole)
    box.exec_()

    use_app = box.clickedButton() is not browser
    if remember.isChecked():
        settings["open_in_apps"] = "always" if use_app else "never"
    return use_app


def open_search(parent, settings, service, song, artist, on_saved=None):
    """Search this song on one service, in the app or the browser."""
    url = ""
    apps = ()
    for name, template, candidates in SERVICES:
        if name == service:
            url = template.format(q=query_for(song, artist))
            apps = candidates
            break
    if not url:
        return

    app = installed_app(apps)
    if app and ask_permission(parent, settings, app, service):
        if on_saved:
            on_saved()
        try:
            if app.startswith("flatpak:"):
                subprocess.Popen(["flatpak", "run", app[8:], url])
            else:
                subprocess.Popen([app, url])
            return
        except Exception:                       # noqa: BLE001
            pass                                # fall through to the browser
    elif on_saved:
        on_saved()

    import webbrowser
    webbrowser.open(url, new=1, autoraise=True)
