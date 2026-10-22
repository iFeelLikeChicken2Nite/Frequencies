Frequencies — new shell, ready to build
=======================================

This is your build folder with the redesigned UI merged in. Nothing about
the build harness changed: AppRun, build-appimage-fedora.sh, bundle_deps.py,
trim_pyqt.py, the icons and the static clips are all exactly as you sent
them.

    ./build-appimage-fedora.sh

That is the whole procedure.


Windows (build 122, WP1)
------------------------
On a Windows 10 or 11 PC with Python 3.10 or newer (python.org, or
"winget install -e --id Python.Python.3.12"):

    1. Unzip this folder.
    2. Double-click build-windows.bat (or "build-windows.bat dev" in a
       Command Prompt for a dev build).
    3. Open dist\Frequencies\Frequencies.exe.

The first run downloads libmpv-2.dll (the sound engine) and the Python
packages, so it needs the internet. Settings live in
%APPDATA%\frequencies, the log in %LOCALAPPDATA%\frequencies.


The one structural change
-------------------------
src/main.py is now the new shell.

AppRun execs usr/share/frequencies/main.py, and build-appimage-fedora.sh
copies src/*.py wholesale, so installing the new entry point under that
name means neither file needed editing. Your original window is kept
beside it as src/main_legacy.py — nothing imports either, so swapping the
two names back restores the old UI if you ever want it.

app_icon() was lifted out of the old main.py into the new one, so main.py
no longer imports anything from main_legacy.py. Delete main_legacy.py once
you are happy; it costs a few hundred KB in the AppImage and nothing else.


Replacing an icon
-----------------
Every glyph in the sidebar and player bar is drawn in code, at whatever
size and color it is asked for. To use your own instead, drop a file
named after the glyph into icons/ beside the code, or into
~/.config/frequencies/icons/ to change one without rebuilding:

    icons/shazam.png        identification, off
    icons/shazam_on.png     identification, on

Both of those are already in place, using the Shazam mark. They are the
same file: the tint is what tells the two states apart, so replacing one
means replacing both unless you want them to differ in shape as well.

.svg or .png, any size — it is scaled and tinted to match the icon beside
it, so it still goes accent-colored when active. An image with no alpha
channel is used as-is rather than tinted to a solid block.

The names are the strings in shell.py's icon(): play, pause, stop, next,
next_shuffle, prev, shuffle, star, star_on, eq, volume, search, stations,
discover, history, trash, info, menu, expand, plus, gear, winamp,
visualizer, fullscreen, shazam, shazam_on.

Note that build-appimage-fedora.sh copies icons/*.png, so a .svg
replacement needs adding to that line to survive a build.


New modules (all picked up automatically by cp src/*.py)
--------------------------------------------------------
  bridge.py       Library over storage.py's free functions, and Playback,
                  which owns the station queue Player does not have.
  mainwindow.py   FrequenciesWindow — the real window.
  shell.py        Sidebar, page stack, player bar, icon painter, marquee.
  stationlist.py  The painted station list.
  sidepanel.py    Right panel: lyrics, artist, or both.
  discover.py     radio-browser as a page, with the featured strip.
  optionspage.py  Global settings.
  feeds.py        Shazam timer, lyrics, artwork resolution, station logos.
  desktop.py      MPRIS, tray icon, health monitor, sleep timer.
  classic.py      The Winamp skinned stack and the visualizer.
  ibroadcast.py     iBroadcast streams, with a FLAC/MP3 quality choice.
  stallwatch.py     Logs a stack trace when the interface stops
                    responding. Always on; Settings > Library and tools >
                    "Create log" saves the log as a file.
  keys.py           Shortcut table: bindings, tooltips and Settings.
  glyphs.py         Embedded logo images, for marks that cannot honestly
                    be redrawn from primitives.
  twitchwatch.py    Checks Twitch channels for life, every five minutes.
  artupgrade.py     Replaces a shared thumbnail with the real cover.
  windowfx.py       Dialogs that scale and fade like KWin's own windows.
  lrclib.py         Timed lyrics from LRCLIB, and the LRC parser.
  qrread.py         Reads a station back out of a picture of its code.
  importwindow.py   Progress window for importing several at once.
  healthlog.py      Live log of the background station health checks.
  croptool.py       Crop artwork and artist photos without leaving.
  photopack.py      The opt-in artist photo pack, and thumbs-down reports.
  packbuild.py      Makes the photo pack (base32 twice) and bio-fixes.json.
  ghpublish.py      Dev build: publishes your photos and bio fixes to GitHub.
  sharedialog.py    Share window: link, short link, copy, QR code.
  stationcard.py    The popup card over a featured station.
  helppage.py       Help: questions and answers.
  schedule.py       Listings grid, per-station shows, start reminders.
  themes.py         Five color schemes for the shell.
  services.py       Search a song on Apple Music, Spotify and the rest.
  smoothscroll.py   Inertial scrolling with a springy edge.
  skinbrowser.py    Skin list, live preview, directory and Get more.
  classicwindows.py
                  Two windows Winamp never had: skinned history and
                  lyrics, built on PlaylistWindow's chrome.
  theme.py        Palette, stylesheet, accent-follows-artwork. (replaced)
  nowplaying.py   Comfort View: artwork, lyrics and artist.
  historypage.py  Identified songs.
  tagsdialog.py   The tag editor the station context menu opens.
                  Superseded by your own StationOptionsDialog. Unused;
                  safe to delete.

  probe.py         plays one station through Player alone, printing
                   position and volume every half second.
  probe_shazam.py  walks the identification chain, stopping at the first
                   step that breaks.

Both probes are diagnostics, not part of the app. They add ~10KB and are
worth keeping for the next time audio or identification misbehaves.


About the visualizer
--------------------
It reported missing only because we were running from source. There is no
usable Fedora package — the distro carries projectM 2.x, which is C++-only
— which is exactly why build-appimage-fedora.sh builds projectM v4.1.4
from source and bundles it. The AppImage has always been the only place it
works, and it still will.


Worth checking once it is built
-------------------------------
  * The Winamp stack. Right-clicking any of the four title bars offers
    the same entries, and closing a panel hides it rather than destroying
    it, so the menu can always bring it back. Anything that fails to
    build says so in ~/.local/state/frequencies/frequencies.log with the
    reason rather than vanishing.
  * The visualizer, from the sidebar button or the clutterbar V.
  * Whether the old layout corruption is gone. The central widget now
    fills its own background, and the likeliest explanation for what you
    were seeing is that it never did — so the desktop showed through
    wherever the three panels did not cover.
  * Transfer (Settings > Library and tools) and the stream sniffer. Both are wired
    against the real playlists.py and webview.py APIs but have not been
    exercised end to end.


Building on an immutable system (Bazzite, Silverblue, Kinoite)
--------------------------------------------------------------
The build installs its dependencies with dnf, which an atomic system's
read-only /usr will not allow. Build in a container instead:

    distrobox create --name freqbuild --image fedora:latest
    distrobox enter freqbuild
    cd <this folder>
    ./build-appimage-fedora.sh

The AppImage that comes out runs on the host like any other; only the
build needs the container. `toolbox create` works identically if
distrobox is not installed.

Running the AppImage on an atomic system needs FUSE. If it refuses to
mount, `./Frequencies.AppImage --appimage-extract-and-run` works without
it.
