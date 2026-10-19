"""Frequencies — iBroadcast.

OAuth 2 with PKCE, a library fetch, and stream URLs built per track.

The client_secret is deliberately not used. A desktop application cannot
keep one: it ships inside the AppImage where anyone can read it, and a
secret everybody has is not a secret. That is what PKCE replaces — the app
proves it started the login by producing the verifier for a challenge it
sent earlier, and nothing confidential has to be stored.

Bitrate is a real choice here rather than a URL flag: the number is a path
segment and the service transcodes to it. "orig" returns the file as it
was uploaded. Free accounts are served 128 whatever is asked for, which is
why the cap is offered as a setting rather than hidden.
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = "38f3f34ca8ac11f1b50eb49691aa2236"
OAUTH = "https://oauth.ibroadcast.com"
API = "https://api.ibroadcast.com/s/JSON/"
LIBRARY = "https://library.ibroadcast.com/"

CLIENT = "frequencies"
VERSION = "1.5"
SCOPES = "user.library:read"

# The loopback redirect. A desktop app has no public address to be sent
# back to, so it listens on localhost for the few seconds a login takes.
REDIRECT_PORT = 8731
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"

# What the streaming server will transcode to. "orig" is the upload.
# Offered qualities. No 192: on a paid account 128, 320 and the original
# all play while 192 does not, so the service does not serve that rate at
# all. Offering it guaranteed a station that would not start.
BITRATES = ("96", "128", "256", "320", "orig")

# Recognised in saved paths even though it is not offered, so an older
# station saved at 192 can still be rewritten to a rate that works.
KNOWN_RATES = BITRATES + ("192",)
BITRATE_LABELS = {
    "96": "96 kbps — smallest",
    "128": "128 kbps",
    "192": "192 kbps",
    "256": "256 kbps",
    "320": "320 kbps",
    "orig": "Original — as uploaded",
}

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Frequencies/" + VERSION


class IBroadcastError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# OAuth
# --------------------------------------------------------------------------

def make_verifier():
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")


def challenge_for(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorize_url(verifier, state):
    return OAUTH + "/authorize?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "state": state,
        "response_type": "code",
        "code_challenge": challenge_for(verifier),
        # "S256", uppercase.
        #
        # RFC 7636 defines exactly two values, "plain" and "S256", and
        # they are case-sensitive. iBroadcast's own documentation writes
        # it lowercase, which their authorization server then rejects with
        # "transform algorithm 's256' not supported".
        "code_challenge_method": "S256",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
    })


def _post_form(path, fields):
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        OAUTH + path, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def exchange_code(code, verifier):
    return _post_form("/token", {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    })


def refresh_token(token):
    return _post_form("/token", {
        "grant_type": "refresh_token",
        "refresh_token": token,
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
    })


def revoke(token):
    try:
        _post_form("/revoke", {"refresh_token": token,
                               "client_id": CLIENT_ID})
    except Exception:                           # noqa: BLE001
        pass                                    # logging out always succeeds


class RedirectCatcher:
    """A one-request web server that catches the code and stops.

    Bound to the loopback address, open only while a login is in progress,
    and it answers exactly one request before shutting down.
    """

    PAGE = (b"<html><body style='font-family:sans-serif;background:#131315;"
            b"color:#ececee;padding:3em'><h2>Signed in.</h2>"
            b"<p>You can close this tab and go back to Frequencies.</p>"
            b"</body></html>")

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer

        self.code = ""
        self.state = ""
        self.error = ""
        catcher = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):               # noqa: N802
                query = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(self.path).query)
                catcher.code = (query.get("code") or [""])[0]
                catcher.state = (query.get("state") or [""])[0]
                catcher.error = (query.get("error") or [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(catcher.PAGE)

            def log_message(self, *_args):
                pass                        # not our logging to do

        self.server = HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)

    def start(self):
        """Begin listening, without waiting for anything."""
        self._thread = threading.Thread(target=self.server.handle_request,
                                        daemon=True)
        self._thread.start()

    def done(self):
        return not self._thread.is_alive()

    def close(self):
        try:
            self.server.server_close()
        except Exception:                       # noqa: BLE001
            pass

    def wait(self, seconds=180):
        """Serve one request, or give up. Blocks — callers with an event
        loop should use start()/done() instead."""
        self.start()
        self._thread.join(seconds)
        self.close()
        return self.code


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def call(token, mode, host=API, **extra):
    """One JSON API request. Raises if the token is no longer good."""
    body = {"client": CLIENT, "version": VERSION,
            "device_name": "Frequencies", "user_agent": USER_AGENT,
            "mode": mode}
    body.update(extra)
    request = urllib.request.Request(
        host, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": USER_AGENT,
                 "Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    if payload.get("authenticated") is False:
        raise IBroadcastError("token expired")
    if payload.get("result") is False:
        raise IBroadcastError(payload.get("message") or "request failed")
    return payload


def status(token):
    """User, settings, and the streaming server address."""
    return call(token, "status")


def library(token):
    """The whole library. A different host, as the docs are explicit about."""
    return call(token, "library", host=LIBRARY)


def set_bitrate_pref(token, bitrate):
    """The account's own preference, which is what free tiers are capped by.

    Every failure is swallowed, not just this module's own. A 403 from
    this call used to escape as an HTTPError and take down the whole
    add-a-station flow — so signing in worked, picking a playlist worked,
    and no station ever appeared. Setting a preference is a nicety; it
    must not be able to stop the thing you actually asked for.
    """
    try:
        return call(token, "setbitratepref", bitrate=int(bitrate))
    except Exception:                           # noqa: BLE001
        return {}


# --------------------------------------------------------------------------
# The library's shape
# --------------------------------------------------------------------------

def rows(section):
    """Turn a column-mapped section into dictionaries.

    Everything but "tags" arrives as arrays with a separate name-to-index
    map, which saves them a lot of bandwidth and costs one function here.
    """
    section = section or {}
    index = section.get("map") or {}
    # Only the entries that really are column numbers.
    #
    # iBroadcast's map is not flat: some names point at a nested map of
    # their own rather than at an index. Comparing one of those with
    # len(values) raised "'<' not supported between instances of 'dict'
    # and 'int'" and took the whole add-a-station flow with it.
    columns = {name: i for name, i in index.items()
               if isinstance(i, int) and not isinstance(i, bool)}
    out = {}
    for key, values in section.items():
        if key == "map" or not isinstance(values, list):
            continue
        out[key] = {name: (values[i] if i < len(values) else None)
                    for name, i in columns.items()}
    return out


def playlists(lib):
    return rows((lib or {}).get("playlists"))


def tracks(lib):
    return rows((lib or {}).get("tracks"))


def artists(lib):
    return rows((lib or {}).get("artists"))


def albums(lib):
    return rows((lib or {}).get("albums"))


def refresh_url(url, token="", ttl=86400):
    """Re-stamp a saved stream URL so it is valid now.

    Every URL is signed with an expiry a day ahead of when it was built.
    A station added yesterday therefore holds links that expired
    overnight — which is why an iBroadcast station played only until the
    next day and had to be added again to work.

    The signature is the OAuth access token, and that token DOES change:
    it runs out and is replaced by a refresh. This used to say otherwise,
    and the caller never passed a token at all — so every track stayed
    signed with the token from the day the station was added, and when
    it ran out the station went down in the middle of listening. Pass
    current_token() and the signature is renewed along with the expiry.
    Returns the URL unchanged if it is not one of ours.
    """
    if not url or "Expires=" not in url:
        return url
    try:
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        query["Expires"] = str(int(time.time()) + int(ttl))
        if token:
            query["Signature"] = token
        return urllib.parse.urlunsplit((
            parts.scheme, parts.netloc, parts.path,
            urllib.parse.urlencode(query), parts.fragment))
    except Exception:                           # noqa: BLE001
        return url


def at_bitrate(url, bitrate):
    """The same stream, asked for at a different quality.

    Every streaming path begins with the rate — /128/aa/bb/file.mp3 —
    so swapping that first segment is the whole change. "orig" is the
    account's own upload, which iBroadcast serves from the same place
    under its own name.
    """
    if not url or not bitrate:
        return url
    known = set(KNOWN_RATES)
    try:
        parts = urllib.parse.urlsplit(url)
        pieces = parts.path.split("/")
        for index, piece in enumerate(pieces):
            if piece in known:
                pieces[index] = str(bitrate)
                return urllib.parse.urlunsplit((
                    parts.scheme, parts.netloc, "/".join(pieces),
                    parts.query, parts.fragment))
        return url
    except Exception:                           # noqa: BLE001
        return url


def name_for(section, key):
    """The name of an artist or album, by its id.

    A track row carries artist_id and album_id, not the names — so
    anything that wants to say who is playing, or to go looking for a
    cover, has to come here first. Without this a track has a title and
    nothing else, which is why iBroadcast stations showed a song with no
    artist and never found artwork: the lookup had half a search term.
    """
    row = (section or {}).get(str(key)) or {}
    return (row.get("name") or "").strip()


# --------------------------------------------------------------------------
# Artwork
# --------------------------------------------------------------------------

# Where the covers live, unless the account's settings name another host.
ARTWORK = "https://artwork.ibroadcast.com"


def artwork_url(settings, track, album_rows=None):
    """The cover iBroadcast holds for one track, or "".

    Your library already has the right sleeve for every track you
    uploaded with one. Searching the internet by name only guesses at it
    (task 23). A track row carries `artwork_id`; the album's row is a
    fallback for tracks that have none of their own.
    """
    track = track or {}
    art = track.get("artwork_id")
    if not art or str(art) == "0":
        album = (album_rows or {}).get(str(track.get("album_id"))) or {}
        art = album.get("artwork_id")
    if not art or str(art) == "0":
        return ""
    server = ((settings or {}).get("artwork_server") or ARTWORK).rstrip("/")
    return f"{server}/artwork/{art}-300"


def file_id_of(url):
    """The file_id a stream URL asks for, or "" (matches a track row)."""
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url or "").query)
        return str((query.get("file_id") or [""])[0])
    except Exception:                           # noqa: BLE001
        return ""


def artwork_by_file(settings, lib):
    """{file_id: cover URL} for a whole library.

    For stations added before covers were saved with their tracks: their
    URLs say which file each one is, and this says which cover goes with
    it.
    """
    album_rows = rows((lib or {}).get("albums"))
    out = {}
    for key, track in rows((lib or {}).get("tracks")).items():
        url = artwork_url(settings, track, album_rows)
        if not url:
            continue
        out[str(track.get("file_id") or key)] = url
        out.setdefault(str(key), url)
    return out


# --------------------------------------------------------------------------
# Playback
# --------------------------------------------------------------------------

def stream_url(settings, track, token, user_id, bitrate="128", cap=False):
    """A playable URL for one track.

    The bitrate is a path segment rather than a query parameter, so asking
    for a different one rewrites the front of the path. A free account is
    served 128 whatever is requested; `cap` makes that explicit rather than
    letting the app claim a quality it will not get.
    """
    server = (settings or {}).get("streaming_server") \
        or "https://streaming.ibroadcast.com"
    # "file" is the track. "path" is only the folder it sits in.
    #
    # The log made this plain: path came back as "musics" and
    # "Albums/Imaginal Disk" — directories, with no filename on the end —
    # so every URL pointed at a folder and nothing played. A track row
    # carries both, and `file` is the one that names the audio.
    track = track or {}
    name = track.get("file") or ""
    folder = track.get("path") or ""
    if name and folder and not name.startswith(("/", folder)):
        name = folder.rstrip("/") + "/" + name.lstrip("/")
    elif not name:
        name = folder
    file_id = track.get("file_id") or track.get("id") or ""
    if not name and not file_id:
        return ""
    if cap and bitrate not in ("96", "128"):
        bitrate = "128"
    if bitrate not in BITRATES:
        bitrate = "128"

    query = urllib.parse.urlencode({
        # An expiry in the future, in seconds.
        #
        # This was time.time() * 1000 — the current moment, in
        # milliseconds. As an expiry that is either "already expired" or a
        # date in the year 57000 depending on how the server reads it, and
        # either way the link does not play. A day ahead is what their own
        # player asks for.
        "Expires": int(time.time()) + 86400,
        "Signature": token,
        "file_id": track.get("file_id") or track.get("id") or "",
        "user_id": user_id,
        "platform": CLIENT,
        "version": VERSION,
    })
    # The path has to be escaped.
    #
    # Track paths are real filenames — "Misc/mini mix vol. 2" — full of
    # spaces, brackets and full stops. Dropped into a URL raw they make a
    # malformed request, which is why the log showed "couldn't connect"
    # against a path that plainly existed. Slashes are kept, because they
    # are the path.
    if name:
        cleaned = name.lstrip("/")
        # The file already carries the bitrate.
        #
        # iBroadcast's `file` looks like "128/11a/dad/255993361" — the
        # quality is the first segment. Prepending another produced
        # "/128/128/11a/..." and a 404 that reads as "couldn't connect".
        # The leading segment is replaced with the one being asked for
        # rather than stacked on top of it.
        head, _, rest = cleaned.partition("/")
        if head in BITRATES and rest:
            cleaned = rest
        safe = urllib.parse.quote(cleaned, safe="/")
        return f"{server.rstrip('/')}/{bitrate}/{safe}?{query}"
    # No path, only an id. Most of a library comes back this way, and
    # returning "" for it threw away seven tracks in eight.
    return f"{server.rstrip('/')}/{bitrate}/{file_id}?{query}"


# --------------------------------------------------------------------------
# Where the tokens live
# --------------------------------------------------------------------------

def token_path():
    import storage
    return os.path.join(os.path.dirname(storage.settings_path()),
                        "ibroadcast.json")


def save_tokens(data):
    """Written with owner-only permissions.

    A refresh token is a standing credential. The keyring is the right home
    and worth doing later; a file nobody else can read is already a great
    deal better than the settings file everything else shares.
    """
    path = token_path()
    # Written beside the real file and swapped in whole.
    #
    # The sign-in is now renewed on a background thread while the window
    # reads it to sign each track. Writing in place let a read land on a
    # half-written file, which parses as nothing — a track signed with no
    # token at all.
    temp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except OSError:
        pass


def load_tokens():
    try:
        with open(token_path(), encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:                           # noqa: BLE001
        return {}


def clear_tokens():
    try:
        os.remove(token_path())
    except OSError:
        pass


def signed_in():
    """Whether a sign-in is saved, without asking the network.

    For wording, not for streaming: a saved token may still need a
    refresh, and valid_token is what does that. This is only so the app
    can say the right thing before anything has been tried.
    """
    try:
        tokens = load_tokens() or {}
    except Exception:                           # noqa: BLE001
        return False
    return bool(tokens.get("access_token") or tokens.get("refresh_token"))


def valid_token():
    """A usable access token, refreshing it if the old one has expired."""
    saved = load_tokens()
    token = saved.get("access_token", "")
    expires = float(saved.get("expires_at") or 0)
    if token and expires > time.time() + 60:
        return token
    if not saved.get("refresh_token"):
        return ""
    try:
        fresh = refresh_token(saved["refresh_token"])
    except Exception:                           # noqa: BLE001
        return ""
    store(fresh)
    return fresh.get("access_token", "")


def current_token():
    """The saved access token, from the file only — never the network.

    For signing a track at the moment it starts, which happens on the GUI
    thread and so must not wait on anything. Keeping it fresh is
    TokenKeeper's job (ibtoken.py); this only reads what it left.
    """
    try:
        return (load_tokens() or {}).get("access_token", "") or ""
    except Exception:                           # noqa: BLE001
        return ""


def needs_refresh(margin=600):
    """Whether the saved access token is gone or about to run out.

    Local only. False when signed out — there is nothing to refresh with.
    False too when the expiry is unknown and a token exists: renewing on
    a guess would mean renewing every minute. A token that fails in use
    is renewed then instead.
    """
    saved = load_tokens() or {}
    if not saved.get("refresh_token"):
        return False
    if not saved.get("access_token"):
        return True
    expires = float(saved.get("expires_at") or 0)
    if not expires:
        return False
    return expires < time.time() + margin


def fresh_token(force=False, margin=600):
    """(token, problem, lifetime) — renewing the sign-in if it needs it.

    Reaches the network, so never call it on the GUI thread: TokenKeeper
    runs it on a thread of its own.

    `force` renews even when the saved token looks fine, for when the
    streaming server has just refused it. `problem` is "" on success,
    "signed out" with nothing to renew with, "rejected" when iBroadcast
    turned the refresh token down (a real sign-in is needed), or a short
    description of a network failure. `lifetime` is how many seconds the
    new token is good for, when the server says, so the log can show it.
    """
    saved = load_tokens() or {}
    token = saved.get("access_token", "")
    expires = float(saved.get("expires_at") or 0)
    if not force and token and expires > time.time() + margin:
        return token, "", 0
    refresh = saved.get("refresh_token")
    if not refresh:
        return "", "signed out", 0
    try:
        payload = refresh_token(refresh)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(200).decode("utf-8", "replace")
        except Exception:                       # noqa: BLE001
            body = ""
        if exc.code in (400, 401, 403):
            return "", f"rejected (HTTP {exc.code} {body.strip()})", 0
        return "", f"HTTP {exc.code}", 0
    except Exception as exc:                    # noqa: BLE001
        return "", str(exc) or exc.__class__.__name__, 0
    if not (payload or {}).get("access_token"):
        said = (payload or {}).get("error") or "no token came back"
        return "", f"rejected ({said})", 0
    store(payload)
    try:
        lifetime = int(float(payload.get("expires_in") or 0))
    except (TypeError, ValueError):
        lifetime = 0
    return payload["access_token"], "", lifetime


def store(payload):
    """Keep the tokens and work out when the access one dies."""
    data = load_tokens()
    data.update({k: v for k, v in payload.items() if v})
    if payload.get("expires_in"):
        data["expires_at"] = time.time() + float(payload["expires_in"])
    save_tokens(data)
    return data
