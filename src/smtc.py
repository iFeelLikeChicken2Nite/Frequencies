"""Frequencies — Windows media controls (WP3), in place of MPRIS.

Windows' System Media Transport Controls: the keyboard's media keys
(play/pause, next, previous, stop) and the media flyout that shows the
frequency, the song and its art. The same signals and update_* calls as
mpris.MprisBridge, so desktop.py wires both the same way.

Uses PyWinRT (the winrt-* packages build-windows.ps1 installs). A desktop
app without a UWP window gets its controls from a MediaPlayer that plays
nothing: its own command handling is turned off, so only the buttons this
app enables show and every press comes here. Never fatal: without the
packages, or on an older Windows, the keys just don't reach the app.
"""

from PyQt5.QtCore import QObject, pyqtSignal


def _log(text):
    try:
        import crashlog
        crashlog.write(text)
    except Exception:                           # noqa: BLE001
        pass


class SmtcBridge(QObject):
    """Relays Windows media key presses as Qt signals (from Windows'
    thread: Qt queues them onto the app's own)."""

    play_pause_requested = pyqtSignal()
    play_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    next_requested = pyqtSignal()
    previous_requested = pyqtSignal()
    # Unused on Windows; here so desktop.py connects both bridges alike.
    raise_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    volume_change_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.playback_status = "Stopped"
        self.registered = False
        self._error = ""
        self._player = None
        self._controls = None
        self._token = None
        self._art = None
        self._art_url = ""

    @property
    def error(self):
        return self._error

    def register(self):
        try:
            from winrt.windows.media import MediaPlaybackStatus
            from winrt.windows.media.playback import MediaPlayer
            self._player = MediaPlayer()
            self._player.command_manager.is_enabled = False
            controls = self._player.system_media_transport_controls
            controls.is_enabled = True
            controls.is_play_enabled = True
            controls.is_pause_enabled = True
            controls.is_stop_enabled = True
            controls.is_next_enabled = True
            controls.is_previous_enabled = True
            self._token = controls.add_button_pressed(self._pressed)
            controls.playback_status = MediaPlaybackStatus.STOPPED
            self._controls = controls
            self.registered = True
            _log("[smtc] Windows media controls on (media keys, flyout)")
            return True
        except Exception as exc:                # noqa: BLE001
            self._error = repr(exc)
            _log(f"[smtc] Windows media controls unavailable: {exc!r}")
            return False

    def unregister(self):
        if not self.registered:
            return
        try:
            self._controls.remove_button_pressed(self._token)
            self._controls.display_updater.clear_all()
            self._controls.is_enabled = False
            self._player.close()
        except Exception:                       # noqa: BLE001
            pass
        self.registered = False
        self._controls = self._player = None

    # -- presses, on Windows' thread ----------------------------------------

    def _pressed(self, _sender, args):
        try:
            from winrt.windows.media import (
                SystemMediaTransportControlsButton as Button)
            button = args.button
            signal = {
                Button.PLAY: self.play_requested,
                Button.PAUSE: self.stop_requested,
                Button.STOP: self.stop_requested,
                Button.NEXT: self.next_requested,
                Button.PREVIOUS: self.previous_requested,
            }.get(button)
            _log(f"[smtc] {getattr(button, 'name', button)} pressed")
            if signal is not None:
                signal.emit()
        except Exception as exc:                # noqa: BLE001
            _log(f"[smtc] press not handled: {exc!r}")

    # -- state pushed in from the app ---------------------------------------

    def update_status(self, status):
        """status: 'Playing' | 'Paused' | 'Stopped'"""
        self.playback_status = status
        if not self.registered:
            return
        try:
            from winrt.windows.media import MediaPlaybackStatus as S
            self._controls.playback_status = {
                "Playing": S.PLAYING, "Paused": S.PAUSED,
            }.get(status, S.STOPPED)
        except Exception as exc:                # noqa: BLE001
            _log(f"[smtc] status not set: {exc!r}")

    def update_volume(self, volume):
        pass                    # Windows' flyout has no app volume

    def update_now_playing(self, station_name="", title="", artist="",
                           art_url=""):
        if not self.registered:
            return
        try:
            from winrt.windows.media import MediaPlaybackType
            updater = self._controls.display_updater
            updater.type = MediaPlaybackType.MUSIC
            music = updater.music_properties
            music.title = title or station_name or "Radio"
            music.artist = artist or ""
            music.album_title = station_name or ""
            try:
                updater.thumbnail = self._thumbnail(art_url)
            except Exception as exc:            # noqa: BLE001
                _log(f"[smtc] art not shown: {exc!r}")
            updater.update()
        except Exception as exc:                # noqa: BLE001
            _log(f"[smtc] now playing not shown: {exc!r}")

    def _thumbnail(self, art_url):
        """Song art from the web; anything else shows no picture."""
        if art_url == self._art_url:
            return self._art
        self._art_url, self._art = art_url, None
        if art_url.startswith(("http://", "https://")):
            from winrt.windows.foundation import Uri
            from winrt.windows.storage.streams import (
                RandomAccessStreamReference)
            self._art = RandomAccessStreamReference.create_from_uri(
                Uri(art_url))
        return self._art
