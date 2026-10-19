"""
webview.py - launches the embedded browser, in another process.

Nothing here imports QtWebEngine. That is the whole point.

QtWebEngine starts Chromium inside whatever process imports it and
requires the application to share its OpenGL context, and in the main
window that combination corrupted the interface: lists that would not
repaint, widgets drawn over each other, a minimise and restore needed
to force a redraw. Deferring the import postponed the damage rather
than preventing it, because it happens when the engine initialises.

So the browser runs as a separate program - see webview_helper.py -
and this module only starts it and reads back the one line it prints.
The main process stays a plain Qt application that has never heard of
Chromium.
"""
import logging
import os
import subprocess
import time
import sys

from PyQt5.QtCore import QUrl


# What a stream URL looks like. Kept here as well as in the helper so
# the main process can test a URL without starting a browser.
STREAM_HINTS = (
    ".mp3", ".aac", ".m4a", ".ogg", ".opus", ".flac", ".m3u8", ".mpd",
    "/stream", "/listen", "/live", ";stream", "icecast", "shoutcast",
    "/;", ":8000", ":8080/", "/hls/", "/radio/",
)
STREAM_EXCLUDE = (
    "google-analytics", "doubleclick", "/ads/", "adservice",
    "/beacon", "/pixel", ".gif", ".png", ".jpg", ".css", ".js?",
    "googletagmanager", "/favicon",
)


SKIN_MUSEUM_URL = "https://skins.webamp.org/"


def looks_like_stream(url, content_type=""):
    """Is this URL plausibly the audio a page is playing?"""
    lowered = url.lower()
    if any(bad in lowered for bad in STREAM_EXCLUDE):
        return False
    if content_type:
        kind = content_type.lower()
        if kind.startswith("audio/") or "mpegurl" in kind or "ogg" in kind:
            return True
    return any(hint in lowered for hint in STREAM_HINTS)


def image_search_url(query):
    """Image search for a station name, via Startpage.

    Not DuckDuckGo: its image tab builds the grid in JavaScript and wants
    the GPU path this embedded browser is started without, so it renders
    an empty page here while working perfectly in a normal browser.

    Startpage returns its images as ordinary HTML, and it proxies Google's
    results without carrying an account or a tracking profile — which is
    the same reason to prefer it in a browser.
    """
    return ("https://www.startpage.com/sp/search?cat=images&query="
            + QUrl.toPercentEncoding(query or "").data().decode())


log = logging.getLogger("frequencies.webview")


def _helper_env(mode=None):
    """The environment the browser helper needs.

    Chromium's sandbox cannot start from inside an AppImage - the mount
    is nosuid, so its setuid helper will not run - and when it fails
    QtWebEngine keeps the window but never paints a page, which is the
    blank white panel. Disabling the sandbox is the standard remedy for
    bundled builds; the GPU sandbox goes too, because the same mount
    restriction applies to it.

    Only for this child process. Nothing here changes the environment
    the player itself runs in.
    """
    env = dict(os.environ)
    env.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "")

    # The skin museum draws its grid with WebGL.
    #
    # --disable-gpu takes WebGL with it, and the museum then renders a
    # page that is entirely black: the chrome is there, the grid never
    # appears. SwiftShader gives it a software GL implementation, which
    # is slow for a game and fine for a wall of thumbnails. The picker
    # and the sniffer are ordinary pages and keep the cheaper path.
    gpu_flags = (("--use-gl=swiftshader", "--enable-unsafe-swiftshader",
                  "--ignore-gpu-blocklist", "--in-process-gpu")
                 if mode == "skins"
                 else ("--disable-gpu", "--in-process-gpu"))

    for flag in (
        "--no-sandbox",
        "--disable-gpu-sandbox",
        # Single process, and no GPU.
        #
        # Chromium normally forks a zygote and a GPU process. Inside an
        # AppImage both routinely fail to start - the mount is nosuid
        # and the bundled GL stack is not the host's - and when they do,
        # QtWebEngine shows a window that never paints. Running it all
        # in one process with software rendering is slower and entirely
        # adequate for a picture chooser and a page of skins.
        # Not --single-process: it makes Chromium hard to shut down,
        # and a helper that will not exit hangs whatever is waiting for
        # it. Disabling the GPU is enough to get it painting inside an
        # AppImage.
        *gpu_flags,
        "--disable-features=VizDisplayCompositor",
    ):
        # Compared as whole tokens.
        #
        # A substring test drops --disable-gpu because it appears
        # inside --disable-gpu-sandbox, which is exactly the flag that
        # matters here.
        if flag not in flags.split():
            flags = f"{flags} {flag}".strip()
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = flags

    # Point Chromium at the bundled resources explicitly.
    #
    # It finds them by walking up from the executable, which lands
    # somewhere unhelpful when the executable is a Python interpreter
    # inside an AppImage. A missing resource pak is another way to get
    # a window that never paints.
    try:
        import PyQt5
        base = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5")
        resources = os.path.join(base, "resources")
        if os.path.isdir(resources):
            env.setdefault("QTWEBENGINE_RESOURCES_PATH", resources)
        locales = os.path.join(base, "translations", "qtwebengine_locales")
        if os.path.isdir(locales):
            env.setdefault("QTWEBENGINE_LOCALES_PATH", locales)
        process = os.path.join(base, "libexec", "QtWebEngineProcess")
        if os.path.isfile(process):
            env.setdefault("QTWEBENGINEPROCESS_PATH", process)
    except Exception:  # noqa: BLE001
        pass

    return env


def _helper_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "webview_helper.py")


def is_available():
    """True when the helper can run - i.e. QtWebEngine is installed.

    Checked by asking the helper's own interpreter, so this process
    still never imports it.
    """
    if getattr(sys, "frozen", False):
        # Windows (WP2): the helper is the app itself, started with
        # --webview-helper; QtWebEngine is bundled or it isn't.
        import importlib.util
        return importlib.util.find_spec("PyQt5.QtWebEngineWidgets") is not None
    try:
        result = subprocess.run(
            [sys.executable, "-c",
             "import PyQt5.QtWebEngineWidgets"],
            capture_output=True, timeout=20,
        )
        return result.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def unavailable_message():
    return ("The embedded browser needs QtWebEngine, which isn't installed. "
            "Opening your normal browser instead.")


def _run(mode, *args, timeout=900):
    """Run the helper and return what it printed, or "".

    The parent keeps pumping its own event loop while it waits, so the
    player carries on playing and the window stays alive rather than
    going grey. subprocess.run blocks the calling thread outright, and
    this is called from the GUI thread - so the app froze for as long
    as the browser was open, and forever if the browser failed to exit.

    Output goes to files rather than pipes. A pipe that fills while
    nobody is reading it stops the child dead, and Chromium is chatty.
    """
    import tempfile

    from PyQt5.QtWidgets import QApplication

    out = tempfile.TemporaryFile(mode="w+")
    err = tempfile.TemporaryFile(mode="w+")
    env = _helper_env(mode)
    command = [sys.executable, _helper_path(), mode, *args]
    result_file = None
    if getattr(sys, "frozen", False):
        # Windows (WP2): sys.executable is Frequencies.exe, which runs
        # the helper when started with --webview-helper. A windowed exe
        # may have no stdout, so the answer comes back in a file.
        command = [sys.executable, "--webview-helper", mode, *args]
        handle, result_file = tempfile.mkstemp(prefix="freq-helper-")
        os.close(handle)
        env["FREQ_HELPER_OUT"] = result_file
    try:
        child = subprocess.Popen(
            command, stdout=out, stderr=err, env=env,
        )
    except Exception:  # noqa: BLE001
        return ""

    waited = 0.0
    while child.poll() is None:
        QApplication.processEvents()
        time.sleep(0.03)
        waited += 0.03
        if waited > timeout:
            # Refusing to exit. Kill it rather than hang the app: a
            # browser window that will not close is worse than one that
            # closes rudely.
            log.warning("browser helper did not exit; killing it")
            child.kill()
            break

    try:
        err.seek(0)
        complaints = err.read().strip()
        if complaints:
            log.warning("browser helper said: %s", complaints[:2000])
        if result_file:
            with open(result_file, "r", encoding="utf-8") as f:
                return (f.read() or "").strip()
        out.seek(0)
        return (out.read() or "").strip()
    except Exception:  # noqa: BLE001
        return ""
    finally:
        out.close()
        err.close()
        if result_file:
            try:
                os.remove(result_file)
            except OSError:
                pass

def pick_image(query):
    """Open the image picker; return the chosen URL, or ""."""
    return _run("images", query or "")


def browse_skins(skin_folder):
    """Open the skin museum in the user's own browser.

    Not in the bundled one. The museum is a JavaScript application that
    wants WebGL, and the embedded browser is started without a GPU path —
    so it rendered a blank rectangle and no amount of flags fixed it. A
    real browser has the graphics, the password manager and the download
    folder already.

    Returns the folder skins should end up in, so the caller can say
    where to put them.
    """
    import webbrowser
    webbrowser.open(SKIN_MUSEUM_URL)
    return skin_folder or ""


def sniff_stream():
    """Open the sniffer; return the chosen stream URL, or ""."""
    return _run("sniff")
