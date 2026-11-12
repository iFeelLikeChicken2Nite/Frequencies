"""icyrelay.py - play stations whose server answers "ICY 200 OK" (task 220).

Old SHOUTcast servers (version 1) answer a request with the status line
"ICY 200 OK" instead of "HTTP/1.0 200 OK". Players used to take it; newer
FFmpeg ("http: Malformed HTTP status line", the Linux build) and mpv's
libcurl backend ("Unsupported protocol", the Windows build) refuse it.
So such a station never played, and its song checks recorded nothing:
"Captured audio was empty". Two thirds of Radio Caprice's channels
(the 320k ones on ports like :9003) are on servers like that.

This is a tiny relay on 127.0.0.1: mpv asks it for the station, it asks
the station, puts "HTTP/1.0 200 OK" in place of "ICY 200 OK" and passes
everything else through untouched (headers, audio, the song titles in the
stream). Only stations that really answer "ICY" go through it; every other
address is handed to mpv exactly as before.

route(url) is the one call: the address to give mpv. It costs one quick
look at the server the first time an http:// address is played, kept for
the session. It never raises and fails open: anything unexpected gives
back the address unchanged.
"""
import socket
import socketserver
import threading
import urllib.parse

USER_AGENT = "Frequencies/1.5"

# The quick look: how long to wait for the server's first line.
LOOK_CONNECT_S = 3.0
LOOK_READ_S = 3.0
# The relay gives up on a station that sends nothing for this long
# (mpv has its own, shorter, network timeout and reconnects).
STALL_S = 30.0

_lock = threading.Lock()
_known = {}          # address -> True (ICY) / False (anything else)
_routes = {}         # token -> address
_server = None
_port = None
_said = set()


def _log(line):
    try:
        import crashlog
        crashlog.write(line)
    except Exception:                           # noqa: BLE001
        pass


def _short(url):
    return (url or "").split("?")[0]


def _plain_http(url):
    """http:// to somewhere other than this computer (SHOUTcast v1 has
    no TLS; https and local addresses are never relayed)."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return (parts.scheme.lower() == "http" and bool(host)
            and host not in ("localhost", "::1")
            and not host.startswith("127."))


def _request(parts, extra=()):
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    lines = [f"GET {path} HTTP/1.0", f"Host: {parts.netloc}"]
    lines += list(extra)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", "replace")


def answers_icy(url):
    """Whether the server answers this address with "ICY ...". Cached;
    None (not cached) when the server couldn't be asked."""
    with _lock:
        if url in _known:
            return _known[url]
    parts = urllib.parse.urlsplit(url)
    try:
        sock = socket.create_connection((parts.hostname, parts.port or 80),
                                        timeout=LOOK_CONNECT_S)
    except (OSError, ValueError):
        return None
    try:
        sock.settimeout(LOOK_READ_S)
        sock.sendall(_request(parts, (f"User-Agent: {USER_AGENT}",
                                      "Icy-MetaData: 1")))
        got = b""
        while b"\n" not in got and len(got) < 256:
            chunk = sock.recv(256)
            if not chunk:
                break
            got += chunk
    except OSError:
        return None
    finally:
        sock.close()
    if not got:
        return None
    icy = got.startswith(b"ICY ")
    with _lock:
        _known[url] = icy
    return icy


def remember(url):
    """Note an address as an ICY server (the station check found it)."""
    if _plain_http(url):
        with _lock:
            _known[url] = True


def route(url):
    """The address to give mpv for `url`: the relay's for an ICY server
    (or a .pls/.m3u that lists one), otherwise `url` itself."""
    try:
        if not isinstance(url, str) or not url.lower().startswith("http"):
            return url
        target = url
        import playlists
        if playlists.looks_like_playlist(url):
            target = playlists.resolve_stream_url(url)
        if not _plain_http(target) or not answers_icy(target):
            return url
        relayed = _relay_url(target)
        if relayed is None:
            return url
        if target not in _said:
            _said.add(target)
            _log(f"[stream] {_short(target)} answers 'ICY 200 OK' (an old "
                 f"SHOUTcast server); playing it through the app's relay")
        return relayed
    except Exception as exc:                    # noqa: BLE001
        _log(f"[stream] relay check failed, playing directly: {exc!r}")
        return url


def _relay_url(target):
    port = _ensure_server()
    if port is None:
        return None
    with _lock:
        for token, address in _routes.items():
            if address == target:
                break
        else:
            token = f"s{len(_routes) + 1}"
            _routes[token] = target
    # The station's own path after the token, so mpv's fallback title
    # (the address's last part, shown until the stream names a song) is
    # what it was without the relay: "latinjazz", not "s1".
    parts = urllib.parse.urlsplit(target)
    tail = parts.path.lstrip("/") + (f"?{parts.query}" if parts.query else "")
    return f"http://127.0.0.1:{port}/{token}/{tail}"


def _ensure_server():
    global _server, _port
    with _lock:
        if _server is not None:
            return _port
        try:
            server = _Server(("127.0.0.1", 0), _Handler)
        except OSError as exc:
            _log(f"[stream] the relay could not start: {exc!r}")
            return None
        _server, _port = server, server.server_address[1]
    threading.Thread(target=server.serve_forever, name="IcyRelay",
                     daemon=True).start()
    return _port


def shutdown():
    global _server
    with _lock:
        server, _server = _server, None
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except Exception:                       # noqa: BLE001
            pass


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


# Headers from mpv passed on to the station. Host and Connection are the
# relay's own; Range makes no sense on a live stream.
_PASSED = ("user-agent", "icy-metadata", "accept", "referer", "cookie")


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        client.settimeout(10)
        head = b""
        try:
            while b"\r\n\r\n" not in head and len(head) < 16384:
                chunk = client.recv(4096)
                if not chunk:
                    return
                head += chunk
        except OSError:
            return
        lines = head.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
        try:
            token = lines[0].split()[1].lstrip("/").split("/", 1)[0]
        except IndexError:
            return
        with _lock:
            target = _routes.get(token)
        if target is None:
            client.sendall(b"HTTP/1.0 404 Not Found\r\n\r\n")
            return
        extra = [line for line in lines[1:]
                 if line.split(":", 1)[0].strip().lower() in _PASSED]
        parts = urllib.parse.urlsplit(target)
        try:
            upstream = socket.create_connection(
                (parts.hostname, parts.port or 80), timeout=15)
        except OSError:
            client.sendall(b"HTTP/1.0 502 Bad Gateway\r\n\r\n")
            return
        try:
            upstream.settimeout(STALL_S)
            upstream.sendall(_request(parts, extra))
            first = b""
            while b"\n" not in first and len(first) < 4096:
                chunk = upstream.recv(4096)
                if not chunk:
                    break
                first += chunk
            if first.startswith(b"ICY "):
                first = b"HTTP/1.0 " + first[4:]
            client.sendall(first)
            while True:
                chunk = upstream.recv(16384)
                if not chunk:
                    break
                client.sendall(chunk)
        except OSError:
            pass                    # mpv hung up (station change) or a stall
        finally:
            upstream.close()
