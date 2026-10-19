"""
webview_helper.py - the embedded browser, in its own process.

Run as a script, not imported. It prints its result to stdout and exits:

    python webview_helper.py images "Jazz FM"      -> an image URL
    python webview_helper.py sniff                 -> a stream URL

Why a separate process at all:

QtWebEngine starts Chromium inside whatever process imports it, and it
requires the application to share its OpenGL context. In the main
window that combination corrupted the interface - lists that would not
repaint, widgets drawn over each other, a minimise/restore needed to
force a redraw. Deferring the import helped and did not cure it,
because the damage is done the moment the engine initialises.

Out here it can do as it likes. The main process never imports
QtWebEngine, never sets the GL attribute, and cannot be affected by it.
The cost is a second Python interpreter for as long as the window is
open, which for a window the user explicitly asked for is cheap.
"""
import os
import sys

from PyQt5.QtCore import Qt, QCoreApplication, QUrl, QTimer

# Must precede the QApplication.
QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)

from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem,
)
from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt5.QtWebEngineCore import (  # noqa: E402
    QWebEngineUrlRequestInterceptor, QWebEngineUrlRequestInfo,
)


# Kept in step with webview.py, which holds the same tables for the
# main process to use without importing any of this.
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

SELECT_SCRIPT = r"""
(function () {
    if (window.__freqPicker) { return ""; }
    window.__freqPicker = true;
    window.__freqLast = "";

    function fromLink(node) {
        for (var i = 0; i < 6 && node; i++) {
            if (node.tagName === "A" && node.href) {
                try {
                    var url = new URL(node.href, location.href);
                    var keys = ["u", "du", "imgurl", "media"];
                    for (var k = 0; k < keys.length; k++) {
                        var found = url.searchParams.get(keys[k]);
                        if (found && /^https?:/.test(found)) { return found; }
                    }
                } catch (e) { }
            }
            node = node.parentElement;
        }
        return "";
    }

    function fromImage(img) {
        if (img.srcset) {
            var best = "", width = 0;
            img.srcset.split(",").forEach(function (part) {
                var bits = part.trim().split(/\s+/);
                var w = parseInt(bits[1] || "0", 10) || 0;
                if (bits[0] && w >= width) { width = w; best = bits[0]; }
            });
            if (best) { return best; }
        }
        return img.getAttribute("data-src") || img.src || "";
    }

    document.addEventListener("click", function (event) {
        var node = event.target;
        for (var i = 0; i < 5 && node; i++) {
            if (node.tagName === "IMG") {
                window.__freqLast = fromLink(node) || fromImage(node) || "";
                node.style.outline = "4px solid #4ea";
                return;
            }
            node = node.parentElement;
        }
    }, true);
    return "";
})();
"""


def looks_like_stream(url, content_type=""):
    lowered = url.lower()
    if any(bad in lowered for bad in STREAM_EXCLUDE):
        return False
    if content_type:
        kind = content_type.lower()
        if kind.startswith("audio/") or "mpegurl" in kind or "ogg" in kind:
            return True
    return any(hint in lowered for hint in STREAM_HINTS)


class _Base(QDialog):
    def __init__(self, title, url):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(1000, 760)
        self.result_url = ""

        # A view that follows "open in new tab" links in place.
        #
        # Search results are target=_blank. A plain QWebEngineView has
        # nowhere to put a new window, so createWindow returns nothing and
        # the click is silently dropped — which is exactly "the links
        # don't work". Navigating in the same view is what a single-window
        # picker wants anyway.
        class _InPlaceView(QWebEngineView):
            """Follows "open in new tab" links in this same window.

            Returning `self` from createWindow looks like the obvious
            answer and crashes: Qt is asking for somewhere *else* to put
            a page, and handing back the view it asked from re-enters the
            page it is in the middle of creating.

            A throwaway view is given instead. It never shows anything —
            it exists to catch the URL Qt puts into it, which is then
            loaded here. Kept on the instance, because a view garbage
            collected mid-navigation takes the process with it.
            """

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._catchers = []

            def createWindow(self, _kind):
                catcher = QWebEngineView()
                catcher.setAttribute(Qt.WA_DontShowOnScreen, True)

                def follow(url):
                    _log(f"following new-tab link {url.toString()[:90]}")
                    self.setUrl(url)
                    catcher.deleteLater()
                    if catcher in self._catchers:
                        self._catchers.remove(catcher)

                catcher.urlChanged.connect(follow)
                self._catchers.append(catcher)
                return catcher

        self.view = _InPlaceView()
        self._first_url = url
        self._retried = False
        self._arrived = False
        self.view.loadFinished.connect(self._on_load)

        # Blank page first, real page a moment later.
        #
        # On the first run of a fresh process Chromium is still setting
        # itself up - profile, cache, GPU decision - when a navigation
        # issued from the constructor goes out. It reports the load as
        # successful and shows nothing, which is why closing the window
        # and opening it again worked: the second process found the
        # groundwork already done. Navigating to about:blank gives it
        # something harmless to finish starting on.
        self.view.setUrl(QUrl("about:blank"))
        self._navigated = False
        # The real navigation waits for the window to be on screen -
        # see showEvent. The timer is only a backstop for the case
        # where showEvent never arrives.
        QTimer.singleShot(1200, self._go)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #888;")

        self.buttons = QHBoxLayout()
        close = QPushButton("Cancel")
        close.clicked.connect(self.reject)
        self.buttons.addStretch(1)
        self.buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.view, stretch=1)
        layout.addWidget(self.status)
        layout.addLayout(self.buttons)

    def _report(self, message):
        """Straight to stderr, which the parent now logs."""
        sys.stderr.write(f"{message}\n")
        sys.stderr.flush()

    def showEvent(self, event):
        """Load once the window is actually on screen.

        Chromium will not paint into a surface that has not been shown,
        and a navigation issued before then completes into nothing -
        reported as a success, displaying blank. Waiting for the window
        to appear is the difference between the first open working and
        needing a second one.
        """
        super().showEvent(event)
        QTimer.singleShot(50, self._go)

    def _go(self):
        """Navigate to the page this window is actually for."""
        if self._navigated:
            return
        self._navigated = True
        self.view.setUrl(QUrl(self._first_url))
        # A backstop, in case that load also lands blank: reload once
        # if nothing has reported success by then.
        QTimer.singleShot(2500, self._check_arrived)

    def _check_arrived(self):
        if self._arrived or self._retried:
            return
        self._retried = True
        self._report("no load reported; reloading once")
        self.view.setUrl(QUrl(self._first_url))

    def _on_load(self, ok):
        url = self.view.url().toString()
        self._report(f"load finished ok={ok} url={url}")
        if ok and url and not url.startswith("about:"):
            self._arrived = True
            return
        if ok or self._retried:
            return
        self._retried = True
        QTimer.singleShot(400, lambda: self.view.setUrl(QUrl(self._first_url)))

    def add_button(self, text, handler):
        button = QPushButton(text)
        button.setDefault(True)
        button.clicked.connect(handler)
        self.buttons.insertWidget(0, button)


class ImagePicker(_Base):
    def __init__(self, query):
        # Startpage, not DuckDuckGo.
        #
        # This is the copy that matters: the browser runs as a separate
        # process, so the URL the parent builds is never the one loaded.
        # DuckDuckGo's image tab builds its grid in JavaScript and wants
        # the GPU path this helper starts without, so it came up empty.
        url = ("https://www.startpage.com/sp/search?cat=images&query="
               + QUrl.toPercentEncoding(query).data().decode())
        super().__init__(f"Find art for {query}", url)
        self.status.setText(
            "Click an image in the results, then Use this image."
        )
        self.add_button("Use this image", self._use)

        # Injected again whenever the page moves, and into every frame.
        #
        # It only ran on loadFinished in the main frame, so following a
        # result to another page — or a result that lives in an iframe —
        # left nothing listening for the click. The button then found no
        # selection and the whole thing silently produced no image.
        self.view.loadFinished.connect(self._inject)
        self.view.urlChanged.connect(lambda _u: self._inject(True))

    def _inject(self, _ok=True):
        try:
            page = self.view.page()
            page.runJavaScript(SELECT_SCRIPT)
            for frame in ("document.querySelectorAll('iframe')",):
                page.runJavaScript(
                    f"Array.from({frame}).length", lambda n: _log(
                        f"page has {n} iframes"))
        except Exception as exc:                # noqa: BLE001
            _log(f"could not inject the picker script: {exc!r}")

    def _use(self):
        _log("use this image pressed")
        self.view.page().runJavaScript(
            'window.__freqLast || "";', self._picked
        )

    def _picked(self, url):
        _log(f"selection is {len(url or '')} chars: {(url or '')[:90]!r}")
        if not url:
            self.status.setText(
                "Nothing selected yet - click an image in the results first."
            )
            return
        self.result_url = url
        self.accept()


class StreamSniffer(_Base):
    def __init__(self):
        super().__init__("Stream sniffer",
                         "https://www.startpage.com/")
        self.status.setText(
            "Open a station's page and press play. Anything that looks "
            "like audio will appear here."
        )
        self.found = []
        self.results = QListWidget()
        self.results.setMaximumHeight(150)
        self.layout().insertWidget(1, self.results)
        self.add_button("Use this stream", self._use)
        # Double-clicking a row takes it, which is what a list of links
        # invites you to do.
        self.results.itemDoubleClicked.connect(self._chosen)

        outer = self

        class Interceptor(QWebEngineUrlRequestInterceptor):
            def interceptRequest(self, info):
                try:
                    url = info.requestUrl().toString()
                    media = (info.resourceType()
                             == QWebEngineUrlRequestInfo.ResourceTypeMedia)
                    if media or looks_like_stream(url):
                        # Noted, never blocked: pressing play is what
                        # produces the answer.
                        outer.note(url, media)
                except Exception:  # noqa: BLE001
                    pass

        self.view.loadFinished.connect(
            lambda ok: _log(f"sniffer load finished ok={ok} "
                            f"url={self.view.url().toString()[:90]}"))
        self.view.page().profile().downloadRequested.connect(
            lambda item: _log(f"download requested {item.url().toString()[:80]}"))
        self._interceptor = Interceptor()
        self.view.page().profile().setUrlRequestInterceptor(self._interceptor)

    def _chosen(self, item):
        self.result_url = item.data(Qt.UserRole)
        self.accept()

    def _open_externally(self):
        import webbrowser
        webbrowser.open(SKIN_MUSEUM_URL)
        _log("museum handed to the system browser")

    def _check_blank(self):
        """Say so if the page drew nothing.

        A blank window with no explanation is indistinguishable from a
        broken app. Asking the page how much text it has is a blunt
        test, and a blunt test that is right is worth more than no test.
        """
        def seen(length):
            _log(f"museum body length {length}")
            if isinstance(length, int) and length < 200:
                self.status.setText(
                    "The museum needs graphics support this window does "
                    "not have. Use “Open in my browser” — skins downloaded "
                    "there still work; move them to your skins folder, or "
                    "open them from Settings.")

        try:
            self.view.page().runJavaScript(
                "document.body ? document.body.innerText.length : 0", seen)
        except Exception:                       # noqa: BLE001
            pass

    def note(self, url, media):
        if url in self.found:
            return
        _log(f"candidate ({'audio' if media else 'maybe'}) {url[:100]}")
        self.found.append(url)
        item = QListWidgetItem(("audio  " if media else "maybe  ") + url)
        item.setData(Qt.UserRole, url)
        if media:
            self.results.insertItem(0, item)
            self.results.setCurrentRow(0)
        else:
            self.results.addItem(item)
        self.status.setText(f"{len(self.found)} candidate(s) so far.")

    def _use(self):
        item = self.results.currentItem()
        if item is None and self.results.count():
            # Nothing highlighted: take the top one, which is the audio
            # candidate the interceptor put there. Refusing to act when
            # there is exactly one sensible answer is how this looked
            # broken — the button did nothing and said little.
            item = self.results.item(0)
        url = item.data(Qt.UserRole) if item else ""
        if not url:
            self.status.setText(
                "No stream seen yet — press play on the page first.")
            return
        self.result_url = url
        self.accept()


SKIN_MUSEUM_URL = "https://skins.webamp.org/"


class SkinMuseum(_Base):
    """The Winamp Skin Museum, in a window, with downloads captured.

    Just the site: it already has search, thumbnails and previews, and
    reimplementing that against an API nobody promised would be worse
    in every way. What the app adds is where the files land - a .wsz
    clicked here goes straight to the skin folder instead of into
    Downloads for you to move by hand.
    """

    def __init__(self, skin_folder):
        super().__init__("Winamp Skin Museum", SKIN_MUSEUM_URL)
        self.skin_folder = skin_folder or os.path.expanduser("~")

        # A way out when the page will not draw.
        #
        # The museum is a JavaScript application that wants WebGL, and an
        # embedded browser started without a GPU path renders it as a
        # blank rectangle. There is nothing to fix in the page — the
        # answer is the browser that already works, and the downloads
        # still land in the skin folder because that is the browser's
        # own setting.
        self.add_button("Open in my browser", self._open_externally)
        QTimer.singleShot(6000, self._check_blank)
        self.resize(1100, 760)
        self.status.setText(f"Skins download to {self.skin_folder}")

        profile = self.view.page().profile()
        profile.downloadRequested.connect(self._download)

    def _download(self, item):
        """Send skins to the skin folder, accepted without prompting."""
        name = os.path.basename(item.path()) or "skin.wsz"
        try:
            os.makedirs(self.skin_folder, exist_ok=True)
        except OSError:
            item.cancel()
            self.status.setText(f"Couldn't write to {self.skin_folder}")
            return

        target = os.path.join(self.skin_folder, name)
        # Never overwrite: two skins can share a name, and silently
        # replacing one the user already has would be worse than a
        # slightly ugly filename.
        stem, extension = os.path.splitext(target)
        counter = 2
        while os.path.exists(target):
            target = f"{stem} ({counter}){extension}"
            counter += 1

        item.setPath(target)
        item.accept()
        item.finished.connect(
            lambda: self.status.setText(f"Saved {os.path.basename(target)}")
        )
        self.status.setText(f"Downloading {name}...")


def _log(text):
    """The helper is a separate process, so its failures are invisible.

    A crash here looks like the window closing by itself — which is what
    "the sniffer just closes" is. Writing to the same log as the app puts
    the reason somewhere findable.
    """
    try:
        import crashlog
        crashlog.write("[helper] " + text)
    except Exception:                           # noqa: BLE001
        pass


def main():
    if len(sys.argv) < 2:
        return 2
    mode = sys.argv[1]

    # A crash handler, so the helper stops disappearing quietly.
    #
    # It runs as its own process with no console attached, so an
    # unhandled exception closed the window and left nothing behind.
    # Qt's own warnings go the same way — a WebEngine sandbox refusal or
    # a missing codec is exactly the sort of thing that ends a page load
    # without a word.
    try:
        import crashlog
        crashlog.install("helper")
    except Exception:                           # noqa: BLE001
        pass

    def _hook(kind, value, tb):
        import traceback
        _log("crashed: " + "".join(
            traceback.format_exception(kind, value, tb))[-800:])
        sys.__excepthook__(kind, value, tb)

    sys.excepthook = _hook
    _log(f"started in {mode!r} mode")

    app = QApplication(sys.argv[:1])  # noqa: F841 - owns the event loop

    if mode == "images":
        dialog = ImagePicker(sys.argv[2] if len(sys.argv) > 2 else "")
    elif mode == "sniff":
        dialog = StreamSniffer()
    elif mode == "skins":
        dialog = SkinMuseum(sys.argv[2] if len(sys.argv) > 2 else "")
    else:
        return 2

    if mode == "skins":
        # Nothing to report back: the skins are already on disk.
        dialog.exec_()
        return 0

    if dialog.exec_() and dialog.result_url:
        # The only thing on stdout, so the caller can just read it.
        sys.stdout.write(dialog.result_url)
        sys.stdout.flush()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
