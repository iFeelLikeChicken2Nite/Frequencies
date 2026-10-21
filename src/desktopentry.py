"""A launcher entry for the AppImage (build 131, task 189).

A Linux dock or taskbar matches a window to a .desktop file by app id, and
takes the icon from there. An AppImage nobody installed has no such file,
so Ubuntu showed a gear. On launch this quietly writes (or refreshes) a
user entry, ~/.local/share/applications/frequencies.desktop, that points
at the running AppImage, and the icons under ~/.local/share/icons.

One app id everywhere: APP_ID is the Wayland app id (Qt's desktop file
name), the entry's file name, and, capitalized, the X11 WM_CLASS class
(StartupWMClass). Skipped when AppImageLauncher or a system install
already provides an entry, and when not running from an AppImage.
"""
import glob
import os
import shutil
import subprocess
import sys

import crashlog

APP_ID = "frequencies"
WM_CLASS = "Frequencies"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")


def _provided_elsewhere(mine):
    """A system install or AppImageLauncher already has an entry."""
    dirs = (os.environ.get("XDG_DATA_DIRS")
            or "/usr/local/share:/usr/share").split(":")
    for base in dirs + ["/var/lib/flatpak/exports/share"]:
        if os.path.exists(os.path.join(base, "applications",
                                       APP_ID + ".desktop")):
            return True
    apps = os.path.join(_data_home(), "applications")
    for path in glob.glob(os.path.join(apps, "appimagekit_*.desktop")):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                if "frequencies" in handle.read().lower():
                    return True
        except OSError:
            pass
    return False


def _quote(path):
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') \
        .replace("`", "\\`").replace("$", "\\$") + '"'


def entry_text(appimage):
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Frequencies",
        "GenericName=Internet Radio",
        "Comment=Listen to internet radio stations",
        "Exec=" + _quote(appimage),
        "Icon=" + APP_ID,
        "Categories=AudioVideo;Audio;Player;",
        "Terminal=false",
        "StartupNotify=true",
        "StartupWMClass=" + WM_CLASS,
        "",
    ])


def _write_if_changed(path, data, binary=False):
    try:
        with open(path, "rb" if binary else "r",
                  **({} if binary else {"encoding": "utf-8"})) as handle:
            if handle.read() == data:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if binary else "w",
              **({} if binary else {"encoding": "utf-8"})) as handle:
        handle.write(data)
    return True


def install(icon_dir):
    """Write or refresh the entry. Never raises: a missing launcher entry
    only costs the icon."""
    try:
        appimage = os.environ.get("APPIMAGE", "")
        if not sys.platform.startswith("linux") or not appimage \
                or not os.path.isfile(appimage):
            return
        appimage = os.path.abspath(appimage)
        if _provided_elsewhere(appimage):
            crashlog.write("[desktop] a system or AppImageLauncher entry "
                           "exists; not writing one")
            return
        changed = False
        home = _data_home()
        for size in SIZES:
            src = os.path.join(icon_dir, f"frequencies_{size}.png")
            if not os.path.isfile(src):
                continue
            with open(src, "rb") as handle:
                data = handle.read()
            changed |= _write_if_changed(os.path.join(
                home, "icons", "hicolor", f"{size}x{size}", "apps",
                APP_ID + ".png"), data, binary=True)
        changed |= _write_if_changed(
            os.path.join(home, "applications", APP_ID + ".desktop"),
            entry_text(appimage))
        if changed:
            crashlog.write(f"[desktop] launcher entry written for {appimage}")
            tool = shutil.which("gtk-update-icon-cache")
            if tool:
                subprocess.run([tool, "-q", "-f", "-t", os.path.join(
                    home, "icons", "hicolor")], timeout=10,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tool = shutil.which("update-desktop-database")
            if tool:
                subprocess.run([tool, "-q", os.path.join(
                    home, "applications")], timeout=10,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:                    # noqa: BLE001
        crashlog.write(f"[desktop] launcher entry not written: {exc!r}")
