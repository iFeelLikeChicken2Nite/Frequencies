"""Frequencies — signing in to Twitch chat, so you can write in it (task 92e).

Build 82. Twitch's "device code" sign-in, the one Twitch offers apps that
run on your own computer:

  1. The app asks Twitch for a short code (`DeviceSignIn`).
  2. Your browser opens twitch.tv/activate with that code; you check it
     and press Authorize. Your password never passes through the app.
  3. The app has been asking Twitch every few seconds whether you have;
     once you have, it is handed a token that lets it read and write chat
     as you (scopes `chat:read chat:edit`, nothing else; build 85 adds
     `user:manage:chat_color`, to change your name colour).

The token is kept in `~/.config/frequencies/twitch_chat.json`, readable
only by you (like iBroadcast's), and never written to the log or the
project files. It lasts a few hours and is renewed on its own
(`chat_credentials`); the renewal itself lasts 30 days unused, then you
sign in again. Sign out (`sign_out`) deletes the file and tells Twitch to
forget the token.

Twitch only does this for an app registered with it. The app's Client ID
(public, not a secret) is set in Settings in a dev build — saved to
`~/.config/frequencies/twitch_app.json` — and the build script copies it
into `src/twitch_app.json` (tools/ship_twitch_app.py), so the builds you
share carry it. A build without one can't sign in and says so.

The sandbox this was written in can't reach Twitch: the addresses and the
answers are from Twitch's docs, tested only against a fake server. The
`[chat] sign-in: …` lines in the log say what Twitch answered.
"""

import json
import os
import threading
import time

from PyQt5.QtCore import pyqtSignal

from jobs import Job

DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
REVOKE_URL = "https://id.twitch.tv/oauth2/revoke"
# user:manage:chat_color (build 85, task 95c): change your name colour.
# A sign-in from build 82–84 doesn't have it: sign in again once.
COLOUR_SCOPE = "user:manage:chat_color"
SCOPES = "chat:read chat:edit " + COLOUR_SCOPE
COLOUR_URL = "https://api.twitch.tv/helix/chat/color"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
RENEW_EARLY_S = 300             # renew when less than this is left

APP_FILE = "twitch_app.json"    # the Client ID (dev config, or shipped)
TOKEN_FILE = "twitch_chat.json"

_lock = threading.Lock()        # one renewal at a time, whichever thread


def _log(text):
    try:
        import crashlog
        crashlog.write("[chat] sign-in: " + text)
    except Exception:                           # noqa: BLE001
        pass


def _config_dir():
    import storage
    return os.path.dirname(storage.settings_path())


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# -- the app's Client ID --------------------------------------------------------

def dev_app_path():
    return os.path.join(_config_dir(), APP_FILE)


def shipped_app_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), APP_FILE)


def client_id():
    """The Twitch app's Client ID: a dev build's own (Settings) first,
    else the one this build shipped with; "" for none."""
    import devmode
    if devmode.is_dev():
        own = str(_read(dev_app_path()).get("client_id") or "").strip()
        if own:
            return own
    return str(_read(shipped_app_path()).get("client_id") or "").strip()


def save_client_id(value):
    """From Settings (dev builds). "" removes it."""
    value = (value or "").strip()
    path = dev_app_path()
    try:
        if not value:
            if os.path.exists(path):
                os.remove(path)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump({"client_id": value}, handle)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


# -- the saved sign-in ---------------------------------------------------------

def token_path():
    return os.path.join(_config_dir(), TOKEN_FILE)


def _save(data):
    """Owner-only, written beside and swapped in whole (another thread
    may be reading it)."""
    path = token_path()
    temp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except OSError as exc:
        _log(f"could not save the sign-in ({type(exc).__name__})")


def _load():
    data = _read(token_path())
    if not data.get("access_token") or not data.get("login"):
        return {}
    # Made with another app's Client ID: Twitch won't renew it for this one.
    if data.get("client_id") and data.get("client_id") != client_id():
        return {}
    return data


def _forget():
    try:
        os.remove(token_path())
    except OSError:
        pass


def signed_in_as():
    """The Twitch login saved, or "" (no network)."""
    return str(_load().get("login") or "")


def chat_credentials(force_renew=False):
    """(login, access token) to sign in to chat with, or None.

    Renews the token first when it is close to running out (or when
    `force_renew`, after Twitch refused it). Goes over the network: never
    on the GUI thread (the chat reader calls it).
    """
    with _lock:
        data = _load()
        if not data:
            return None
        left = float(data.get("expires_at") or 0) - time.time()
        if force_renew or left < RENEW_EARLY_S:
            renewed = _renew(data)
            if renewed is None:
                return None
            if renewed:
                data = renewed
        return data["login"], data["access_token"]


def can_change_colour():
    """Your sign-in lets the app change your name colour (no network)."""
    return COLOUR_SCOPE in (_load().get("scopes") or [])


# Twitch's 15 colours anyone may pick (the names its API takes), in the
# order Twitch's own Chat Identity shows them.
NAMED_COLOURS = (
    ("red", "#FF0000"), ("blue", "#0000FF"), ("green", "#008000"),
    ("firebrick", "#B22222"), ("coral", "#FF7F50"),
    ("yellow_green", "#9ACD32"), ("orange_red", "#FF4500"),
    ("sea_green", "#2E8B57"), ("golden_rod", "#DAA520"),
    ("chocolate", "#D2691E"), ("cadet_blue", "#5F9EA0"),
    ("dodger_blue", "#1E90FF"), ("hot_pink", "#FF69B4"),
    ("blue_violet", "#8A2BE2"), ("spring_green", "#00FF7F"))


def set_chat_colour(colour):
    """Change your Twitch name colour: one of NAMED_COLOURS' names, or
    "#RRGGBB" (Twitch only takes that from Prime or Turbo users).
    Network: background threads only. Returns (ok, what to tell you)."""
    import requests
    creds = chat_credentials()
    data = _load()
    if not creds or not data:
        return False, "Sign in to Twitch chat first."
    if not can_change_colour():
        _log("color: this sign-in has no color permission yet")
        return False, "need sign-in"
    try:
        reply = requests.put(COLOUR_URL, timeout=10, params={
            "user_id": data.get("user_id") or "", "color": colour},
            headers={"Client-Id": client_id(),
                     "Authorization": f"Bearer {creds[1]}"})
    except Exception as exc:                    # noqa: BLE001
        _log(f"color: couldn't reach Twitch ({type(exc).__name__})")
        return False, "Couldn't reach Twitch."
    if reply.status_code in (200, 204):
        _log(f"color: changed to {colour}")
        return True, ""
    says = _twitch_says(reply)
    _log(f"color: Twitch said no to {colour} (HTTP {reply.status_code}: "
         f"{says})")
    if reply.status_code == 401:
        return False, "need sign-in"
    if colour.startswith("#"):
        return False, ("Twitch only lets Prime or Turbo users pick any "
                       "color. Pick one of the colors above.")
    return False, f"Twitch said: {says}"


def _renew(data):
    """New tokens, {} to keep the old ones for now (network trouble),
    None when Twitch says the sign-in is over."""
    import requests
    try:
        reply = requests.post(TOKEN_URL, timeout=10, data={
            "client_id": client_id(), "grant_type": "refresh_token",
            "refresh_token": data.get("refresh_token") or ""})
    except Exception as exc:                    # noqa: BLE001
        _log(f"could not renew ({type(exc).__name__}); trying the old "
             f"token")
        return {}
    if reply.status_code in (400, 401):
        _log(f"Twitch ended the sign-in ({_twitch_says(reply)}): "
             f"sign in again")
        _forget()
        return None
    if reply.status_code != 200:
        _log(f"could not renew (HTTP {reply.status_code}); trying the old "
             f"token")
        return {}
    try:
        got = reply.json()
    except ValueError:
        return {}
    if not got.get("access_token"):
        return {}
    data = dict(data)
    data["access_token"] = got["access_token"]
    if got.get("refresh_token"):
        data["refresh_token"] = got["refresh_token"]
    data["expires_at"] = time.time() + float(got.get("expires_in") or 3600)
    _save(data)
    _log(f"renewed for '{data.get('login')}' "
         f"({int(float(got.get('expires_in') or 0) / 60)} min)")
    return data


def _twitch_says(reply):
    try:
        got = reply.json()
        return str(got.get("message") or got.get("error") or
                   f"HTTP {reply.status_code}")[:80]
    except ValueError:
        return f"HTTP {reply.status_code}"


def sign_out():
    """Forget the sign-in now; tell Twitch to forget the token on a
    background Job (it doesn't matter if that fails)."""
    data = _read(token_path())
    _forget()
    _log(f"signed out ('{data['login']}')" if data.get("login")
         else "signed out (no sign-in was saved)")
    token = data.get("access_token")
    cid = data.get("client_id") or client_id()
    if not token or not cid:
        return

    def revoke():
        import requests
        requests.post(REVOKE_URL, timeout=8,
                      data={"client_id": cid, "token": token})

    import workers
    job = workers.Lookup(revoke)
    job.start()


# -- signing in --------------------------------------------------------------------

class DeviceSignIn(Job):
    """One sign-in, from the code to the saved token.

    `code(user_code, url, seconds)`: show the code and open `url`.
    `done(ok, text)`: ok → text is the login; else what went wrong, in
    plain words.
    """

    code = pyqtSignal(str, str, int)
    done = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self.cancel = threading.Event()

    def stop(self):
        self.cancel.set()

    def work(self):
        import requests
        cid = client_id()
        if not cid:
            self.send(self.done, False, "This build has no Twitch app "
                                        "Client ID, so it can't sign in.")
            return
        _log("asking Twitch for a code")
        try:
            reply = requests.post(DEVICE_URL, timeout=10, data={
                "client_id": cid, "scopes": SCOPES})
            got = reply.json() if reply.status_code == 200 else {}
        except Exception as exc:                # noqa: BLE001
            _log(f"no code: {type(exc).__name__}: {str(exc)[:80]}")
            self.send(self.done, False, "Couldn't reach Twitch.")
            return
        device = got.get("device_code")
        if not device:
            why = _twitch_says(reply)
            _log(f"no code: Twitch said {why}")
            self.send(self.done, False, f"Twitch said: {why}")
            return
        url = got.get("verification_uri") or "https://www.twitch.tv/activate"
        lasts = int(got.get("expires_in") or 1800)
        wait = max(1, int(got.get("interval") or 5))
        _log(f"code shown, waiting for Authorize ({lasts // 60} min)")
        self.send(self.code, str(got.get("user_code") or ""), url, lasts)
        ends = time.monotonic() + lasts
        while not self.cancel.wait(wait):
            if time.monotonic() > ends:
                _log("the code ran out before Authorize was pressed")
                self.send(self.done, False, "The code ran out. Try again.")
                return
            try:
                reply = requests.post(TOKEN_URL, timeout=10, data={
                    "client_id": cid, "scopes": SCOPES,
                    "device_code": device, "grant_type": DEVICE_GRANT})
            except Exception:                   # noqa: BLE001
                continue                        # try again next round
            if reply.status_code == 200:
                self._signed_in(reply, cid)
                return
            says = _twitch_says(reply)
            if "pending" in says:
                continue
            if "slow_down" in says:
                wait += 5
                continue
            _log(f"stopped: Twitch said {says}")
            self.send(self.done, False,
                      "Twitch said no (" + says + "). Try again." if
                      "denied" in says else f"Twitch said: {says}")
            return
        _log("cancelled")

    def _signed_in(self, reply, cid):
        import requests
        try:
            got = reply.json()
            token = got["access_token"]
        except (ValueError, KeyError):
            self.send(self.done, False, "Twitch's answer made no sense.")
            return
        login, user_id = "", ""
        try:
            who = requests.get(VALIDATE_URL, timeout=10, headers={
                "Authorization": f"OAuth {token}"}).json()
            login = str(who.get("login") or "")
            user_id = str(who.get("user_id") or "")
        except Exception:                       # noqa: BLE001
            pass
        if not login:
            _log("signed in, but Twitch didn't say who you are")
            self.send(self.done, False,
                      "Twitch didn't say which account this is. Try again.")
            return
        scopes = got.get("scope") or []
        if isinstance(scopes, str):
            scopes = scopes.split()
        _save({"client_id": cid, "login": login, "user_id": user_id,
               "scopes": list(scopes), "access_token": token,
               "refresh_token": got.get("refresh_token") or "",
               "expires_at": time.time()
               + float(got.get("expires_in") or 3600)})
        _log(f"signed in as '{login}'")
        self.send(self.done, True, login)


# -- the window that shows the code ------------------------------------------------

def sign_in_dialog(parent):
    """A small window: the code, a button to open Twitch, what is going
    on. Made here so the Qt widgets are only imported when it is used."""
    from PyQt5.QtCore import Qt, QTimer, QUrl
    from PyQt5.QtGui import QDesktopServices
    from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                                 QVBoxLayout)
    from theme import T

    class SignInDialog(QDialog):
        signed_in = pyqtSignal(str)

        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle("Sign in to Twitch chat")
            self.setAttribute(Qt.WA_DeleteOnClose)
            self._job = None
            self._url = ""
            lay = QVBoxLayout(self)
            lay.setContentsMargins(22, 18, 22, 16)
            lay.setSpacing(10)
            self.step = QLabel("Asking Twitch for a code…")
            self.step.setWordWrap(True)
            lay.addWidget(self.step)
            self.code_label = QLabel("")
            self.code_label.setAlignment(Qt.AlignCenter)
            self.code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.code_label.setStyleSheet(
                f"font-size: 30px; font-weight: 700; letter-spacing: 4px;"
                f" color: {T['accent']}; padding: 8px;")
            self.code_label.setMinimumHeight(64)
            lay.addWidget(self.code_label)
            self.status = QLabel("")
            self.status.setWordWrap(True)
            self.status.setProperty("role", "secondary")
            lay.addWidget(self.status)
            row = QHBoxLayout()
            row.addStretch(1)
            self.open_button = QPushButton("Open Twitch")
            self.open_button.setEnabled(False)
            self.open_button.clicked.connect(self._open)
            row.addWidget(self.open_button)
            self.again = QPushButton("Try again")
            self.again.hide()
            self.again.clicked.connect(self.start)
            row.addWidget(self.again)
            self.close_button = QPushButton("Cancel")
            self.close_button.clicked.connect(self.close)
            row.addWidget(self.close_button)
            lay.addLayout(row)
            self.setMinimumWidth(420)

        def start(self):
            self.again.hide()
            self.code_label.setText("")
            self.step.setText("Asking Twitch for a code…")
            self.status.setText("")
            job = DeviceSignIn()
            job.code.connect(self._got_code)
            job.done.connect(self._done)
            self._job = job
            job.start()

        def _got_code(self, user_code, url, seconds):
            self._url = url
            self.code_label.setText(user_code)
            self.step.setText(
                "Your browser opens Twitch's page. Check that it shows this "
                "code, then press Authorize there.")
            self.status.setText(f"Waiting for you to press Authorize (the "
                                f"code lasts {max(1, seconds // 60)} "
                                f"minutes)…")
            self.open_button.setEnabled(True)
            self.open_button.setDefault(True)
            self._open()

        def _open(self):
            if self._url:
                QDesktopServices.openUrl(QUrl(self._url))

        def _done(self, ok, text):
            self._job = None
            if ok:
                self.step.setText(f"Signed in as {text}.")
                self.status.setText("You can write in Twitch chat now.")
                self.code_label.setText("✓")
                self.open_button.setEnabled(False)
                self.close_button.setText("Close")
                self.signed_in.emit(text)
                QTimer.singleShot(1500, self.close)
                return
            self.code_label.setText("")
            self.step.setText("Not signed in.")
            self.status.setText(text)
            self.open_button.setEnabled(False)
            self.again.show()

        def closeEvent(self, e):
            if self._job is not None:
                try:
                    self._job.stop()
                except RuntimeError:
                    pass
                self._job = None
            super().closeEvent(e)

    return SignInDialog()
