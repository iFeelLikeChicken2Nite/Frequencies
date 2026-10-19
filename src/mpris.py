"""
mpris.py - MPRIS2 D-Bus interface, so desktop media keys work.

KDE (and GNOME, and most Linux desktops) route the Play/Pause, Next and
Previous keys to whichever application owns an
org.mpris.MediaPlayer2.* name on the session bus. Implementing that
interface is the whole job - there is no separate "media key" API to
hook, and grabbing the raw keys would fight with the desktop instead of
cooperating with it.

Implemented with Qt's own D-Bus binding rather than dbus-python, so
nothing extra has to be bundled and there's no second event loop to
reconcile with Qt's.

Two adaptors are exported on /org/mpris/MediaPlayer2:
  org.mpris.MediaPlayer2         - app-level (Raise, Quit, Identity)
  org.mpris.MediaPlayer2.Player  - transport controls and metadata

If the session bus isn't reachable (no desktop session, a stripped
container), everything here degrades to a no-op and the app carries on.
"""
from PyQt5.QtCore import (
    QObject, Q_CLASSINFO, pyqtSlot, pyqtProperty, QVariant, pyqtSignal,
)

try:
    from PyQt5.QtDBus import (
        QDBusConnection, QDBusAbstractAdaptor, QDBusMessage, QDBusVariant,
    )
    AVAILABLE = True
    IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001
    QDBusAbstractAdaptor = QObject      # so the class body below still parses
    QDBusConnection = QDBusMessage = QDBusVariant = None
    AVAILABLE = False
    IMPORT_ERROR = str(exc)

# The type of a slot's last argument that receives the D-Bus call itself,
# so the log can say who asked (task 88).
_CALL = QDBusMessage if AVAILABLE else object


def _log(text):
    try:
        import crashlog
        crashlog.write(text)
    except Exception:                           # noqa: BLE001
        pass

BUS_NAME = "org.mpris.MediaPlayer2.frequencies"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPS_IFACE = "org.freedesktop.DBus.Properties"


class RootAdaptor(QDBusAbstractAdaptor):
    """org.mpris.MediaPlayer2 - the application-level bits."""

    Q_CLASSINFO("D-Bus Interface", ROOT_IFACE)
    Q_CLASSINFO("D-Bus Introspection",
        '  <interface name="org.mpris.MediaPlayer2">\n'
        '    <method name="Raise"/>\n'
        '    <method name="Quit"/>\n'
        '    <property name="CanRaise" type="b" access="read"/>\n'
        '    <property name="CanQuit" type="b" access="read"/>\n'
        '    <property name="Identity" type="s" access="read"/>\n'
        '    <property name="DesktopEntry" type="s" access="read"/>\n'
        '    <property name="HasTrackList" type="b" access="read"/>\n'
        '    <property name="SupportedUriSchemes" type="as" access="read"/>\n'
        '    <property name="SupportedMimeTypes" type="as" access="read"/>\n'
        '  </interface>\n')

    def __init__(self, parent, bridge):
        super().__init__(parent)
        self._bridge = bridge

    @pyqtSlot()
    def Raise(self):
        self._bridge.raise_requested.emit()

    @pyqtSlot()
    def Quit(self):
        self._bridge.quit_requested.emit()

    @pyqtProperty(bool)
    def CanRaise(self):
        return True

    @pyqtProperty(bool)
    def CanQuit(self):
        return True

    @pyqtProperty(str)
    def Identity(self):
        return "Frequencies"

    @pyqtProperty(str)
    def DesktopEntry(self):
        return "frequencies"

    @pyqtProperty(bool)
    def HasTrackList(self):
        return False

    @pyqtProperty("QStringList")
    def SupportedUriSchemes(self):
        return ["http", "https"]

    @pyqtProperty("QStringList")
    def SupportedMimeTypes(self):
        return ["audio/mpeg", "audio/aac", "audio/ogg", "audio/x-mpegurl"]


class PlayerAdaptor(QDBusAbstractAdaptor):
    """org.mpris.MediaPlayer2.Player - what the media keys actually hit."""

    Q_CLASSINFO("D-Bus Interface", PLAYER_IFACE)
    Q_CLASSINFO("D-Bus Introspection",
        '  <interface name="org.mpris.MediaPlayer2.Player">\n'
        '    <method name="PlayPause"/>\n'
        '    <method name="Play"/>\n'
        '    <method name="Pause"/>\n'
        '    <method name="Stop"/>\n'
        '    <method name="Next"/>\n'
        '    <method name="Previous"/>\n'
        '    <property name="PlaybackStatus" type="s" access="read"/>\n'
        '    <property name="Metadata" type="a{sv}" access="read"/>\n'
        '    <property name="Volume" type="d" access="readwrite"/>\n'
        '    <property name="CanGoNext" type="b" access="read"/>\n'
        '    <property name="CanGoPrevious" type="b" access="read"/>\n'
        '    <property name="CanPlay" type="b" access="read"/>\n'
        '    <property name="CanPause" type="b" access="read"/>\n'
        '    <property name="CanControl" type="b" access="read"/>\n'
        '    <property name="CanSeek" type="b" access="read"/>\n'
        '  </interface>\n')

    def __init__(self, parent, bridge):
        super().__init__(parent)
        self._bridge = bridge

    # -- transport -----------------------------------------------------
    # Each takes the D-Bus call as its last argument (Qt hands it over
    # without it being part of the method), so the log can name the
    # program that asked: a media key, the panel's player widget, or
    # another app pausing everyone else (task 88).
    @pyqtSlot(_CALL)
    def PlayPause(self, call=None):
        self._bridge.note_caller("PlayPause", call)
        self._bridge.play_pause_requested.emit()

    @pyqtSlot(_CALL)
    def Play(self, call=None):
        self._bridge.note_caller("Play", call)
        self._bridge.play_requested.emit()

    @pyqtSlot(_CALL)
    def Pause(self, call=None):
        # Live radio can't pause meaningfully - there's nothing buffered
        # to resume from - so Pause stops. Reporting CanPause=True keeps
        # the desktop's play/pause key working as a single toggle.
        self._bridge.note_caller("Pause", call)
        self._bridge.stop_requested.emit()

    @pyqtSlot(_CALL)
    def Stop(self, call=None):
        self._bridge.note_caller("Stop", call)
        self._bridge.stop_requested.emit()

    @pyqtSlot()
    def Next(self):
        self._bridge.next_requested.emit()

    @pyqtSlot()
    def Previous(self):
        self._bridge.previous_requested.emit()

    # -- properties ----------------------------------------------------
    @pyqtProperty(str)
    def PlaybackStatus(self):
        return self._bridge.playback_status

    @pyqtProperty("QVariantMap")
    def Metadata(self):
        return self._bridge.metadata

    @pyqtProperty(float)
    def Volume(self):
        return self._bridge.volume / 100.0

    @Volume.setter
    def Volume(self, value):
        try:
            self._bridge.volume_change_requested.emit(
                int(max(0.0, min(1.0, float(value))) * 100)
            )
        except (TypeError, ValueError):
            pass

    @pyqtProperty(bool)
    def CanGoNext(self):
        return True

    @pyqtProperty(bool)
    def CanGoPrevious(self):
        return True

    @pyqtProperty(bool)
    def CanPlay(self):
        return True

    @pyqtProperty(bool)
    def CanPause(self):
        return True

    @pyqtProperty(bool)
    def CanControl(self):
        return True

    @pyqtProperty(bool)
    def CanSeek(self):
        return False


class MprisBridge(QObject):
    """Owns the D-Bus registration and relays key presses as Qt signals.

    MainWindow connects to the *_requested signals and calls
    update_now_playing() / update_status() when things change.
    """

    play_pause_requested = pyqtSignal()
    play_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    next_requested = pyqtSignal()
    previous_requested = pyqtSignal()
    raise_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    volume_change_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.playback_status = "Stopped"
        self.metadata = {}
        self.volume = 80
        self.registered = False
        self._error = ""

        self._root = None
        self._player = None

    def register(self):
        """Claim the bus name. Returns True on success; a failure here is
        never fatal - the app just won't respond to media keys."""
        if not AVAILABLE:
            self._error = IMPORT_ERROR or "QtDBus unavailable"
            return False
        try:
            bus = QDBusConnection.sessionBus()
            if not bus.isConnected():
                self._error = "No session bus"
                return False
            # Adaptors must outlive registration, so keep references.
            self._root = RootAdaptor(self, self)
            self._player = PlayerAdaptor(self, self)
            if not bus.registerObject(OBJECT_PATH, self):
                self._error = "Couldn't register the MPRIS object"
                return False
            if not bus.registerService(BUS_NAME):
                self._error = "Couldn't claim " + BUS_NAME
                return False
            self.registered = True
            return True
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            return False

    def unregister(self):
        if not self.registered or not AVAILABLE:
            return
        try:
            bus = QDBusConnection.sessionBus()
            bus.unregisterService(BUS_NAME)
            bus.unregisterObject(OBJECT_PATH)
        except Exception:
            pass
        self.registered = False

    @property
    def error(self):
        return self._error

    def note_caller(self, method, call):
        """Log which program asked for `method` (task 88).

        The caller's process is asked of the bus without waiting: the
        answer is logged when it comes, a moment later.
        """
        sender = ""
        try:
            sender = call.service() if call is not None else ""
        except Exception:                       # noqa: BLE001
            sender = ""
        if not sender or not AVAILABLE:
            _log(f"[mpris] {method} asked by the desktop (caller unknown)")
            return
        try:
            from PyQt5.QtDBus import QDBusPendingCallWatcher
            ask = QDBusMessage.createMethodCall(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "GetConnectionUnixProcessID")
            ask.setArguments([sender])
            pending = QDBusConnection.sessionBus().asyncCall(ask, 2000)
            watcher = QDBusPendingCallWatcher(pending, self)
            watcher.finished.connect(
                lambda w, m=method, s=sender: self._caller_known(w, m, s))
        except Exception as exc:                # noqa: BLE001
            _log(f"[mpris] {method} asked by {sender} ({exc!r})")

    def _caller_known(self, watcher, method, sender):
        who = sender
        try:
            from PyQt5.QtDBus import QDBusPendingReply
            reply = QDBusPendingReply(watcher)
            if not reply.isError():
                pid = int(reply.argumentAt(0))
                who = f"{_process_name(pid)} (pid {pid}, {sender})"
        except Exception:                       # noqa: BLE001
            pass
        finally:
            watcher.deleteLater()
        _log(f"[mpris] {method} asked by {who}")

    # -- state pushed in from the app ----------------------------------
    def update_status(self, status):
        """status: 'Playing' | 'Paused' | 'Stopped'"""
        if status == self.playback_status:
            return
        self.playback_status = status
        self._emit_properties_changed({"PlaybackStatus": status})

    def update_volume(self, volume):
        self.volume = int(volume)
        self._emit_properties_changed({"Volume": self.volume / 100.0})

    def update_now_playing(self, station_name="", title="", artist="", art_url=""):
        """Build the MPRIS metadata dict the desktop popup reads."""
        metadata = {
            "mpris:trackid": "/org/mpris/MediaPlayer2/frequencies/current",
            "xesam:title": title or station_name or "Radio",
            "xesam:album": station_name or "",
        }
        if artist:
            metadata["xesam:artist"] = [artist]
        if art_url:
            metadata["mpris:artUrl"] = art_url
        self.metadata = metadata
        self._emit_properties_changed({"Metadata": metadata})

    def _emit_properties_changed(self, changed):
        """MPRIS clients listen for PropertiesChanged rather than polling."""
        if not self.registered or not AVAILABLE:
            return
        try:
            message = QDBusMessage.createSignal(
                OBJECT_PATH, PROPS_IFACE, "PropertiesChanged"
            )
            message.setArguments([PLAYER_IFACE, changed, []])
            QDBusConnection.sessionBus().send(message)
        except Exception:
            pass


def _process_name(pid):
    """A program's name from its pid ("plasmashell", "firefox")."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            first = f.read().split(b"\0")[0].decode(errors="replace")
        name = first.rsplit("/", 1)[-1]
        if name:
            return name
    except Exception:                           # noqa: BLE001
        pass
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip() or "?"
    except Exception:                           # noqa: BLE001
        return "?"
