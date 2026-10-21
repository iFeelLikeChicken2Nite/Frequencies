"""
lyrics.py - Fetch lyrics for the currently identified song.

Uses LRCLIB (https://lrclib.net), an open, free, no-account lyrics
database with an API meant for exactly this. It returns both plain
lyrics and, when someone has contributed them, time-synced LRC lyrics.

We ask for an exact match first (artist + track), then fall back to
their search endpoint, which is fuzzier and copes with the messy
artist/title strings radio streams and Shazam hand us.
"""
import re

from urllib.parse import quote

import requests

BASE = "https://lrclib.net/api"
USER_AGENT = "Frequencies/1.5 (https://github.com/)"
TIMEOUT = 10


class LyricsError(Exception):
    """A lookup that failed, as opposed to a song with no lyrics.

    `network` distinguishes "couldn't reach lrclib" from "lrclib
    answered with something unusable" - the first is nothing to do with
    the song and there is no point retrying it per-track.
    """

    def __init__(self, message, network=False):
        super().__init__(message)
        self.network = network


LYRICS_OVH = "https://api.lyrics.ovh/v1"


def _fallback_lyrics(artist, title, timeout=8):
    """(result, error_text). Returns (None, None) for a clean miss."""
    """Plain lyrics from lyrics.ovh, when lrclib has nothing.

    Deliberately a different service rather than a different query
    against the same one: lrclib being unreachable - which is exactly
    what happens behind a filter - should not mean no lyrics at all.

    Plain text only, so no live highlight. That is the trade: unsynced
    lyrics you can read beat synced lyrics that don't exist.
    """
    import requests as _requests

    try:
        response = _requests.get(
            f"{LYRICS_OVH}/{quote(artist)}/{quote(title)}",
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        )
        if response.status_code == 404:
            return None, None            # a clean "no such song"
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        _log(f"lyrics.ovh failed: {exc}")
        return None, type(exc).__name__

    text = (payload.get("lyrics") or "").strip()
    if not text:
        return None, None
    return {
        "syncedLyrics": "",
        "plainLyrics": text,
        "instrumental": False,
        "source": "lyrics.ovh",
    }, None


RETRY_WAITS = (1.0, 2.5)       # seconds before the 2nd and 3rd try


def _get(url, headers, params=None, timeout=TIMEOUT):
    """requests.get that quietly tries again on a 5xx, a timeout or a
    dropped connection (a busy server clears in a second or two). A 4xx
    answer is returned at once: retrying it can't change it."""
    import time

    last = None
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            response = requests.get(url, params=params, headers=headers,
                                    timeout=timeout)
            if response.status_code < 500:
                return response
            last = requests.HTTPError(
                f"{response.status_code} Server Error", response=response)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if isinstance(exc, requests.exceptions.SSLError):
                raise
            last = exc
        if attempt < len(RETRY_WAITS):
            time.sleep(RETRY_WAITS[attempt])
    if isinstance(last, requests.HTTPError):
        return last.response     # still 5xx: the caller treats it as busy
    raise last


def _log(text):
    try:
        import crashlog
        crashlog.write(f"[lyrics] {text}")
    except Exception:  # noqa: BLE001
        pass


def _network_error(exc):
    """Turn a requests exception into something worth reading.

    The raw text is a nest of urllib3 wrappers ending in things like
    "record layer failure (_ssl.c:1032)", which told the user nothing
    except that something exploded. What actually matters is which of
    three situations they're in.
    """
    import requests as _requests

    if isinstance(exc, _requests.exceptions.SSLError):
        return LyricsError(
            "Couldn't establish a secure connection to lrclib.net. "
            "Something on the network is interfering with it - the app "
            "itself is fine.",
            network=True,
        )
    if isinstance(exc, _requests.exceptions.Timeout):
        return LyricsError("The lyrics server took too long to answer.",
                           network=True)
    if isinstance(exc, _requests.exceptions.ConnectionError):
        return LyricsError("Couldn't reach the lyrics server (lrclib.net).",
                           network=True)
    _log(f"lookup failed: {exc}")
    return LyricsError("The lyrics service is busy right now. "
                       "Try again in a little while.", network=True)


def _clean(text):
    """Radio metadata is full of noise that stops a lyrics lookup from
    matching: bitrate tags, "(Official Video)", featured-artist lists."""
    if not text:
        return ""
    text = str(text)
    text = re.sub(r"\((?:official|lyric|audio|hd|remaster).*?\)", "", text,
                  flags=re.IGNORECASE)
    text = re.sub(r"\[(?:official|lyric|audio|hd|remaster).*?\]", "", text,
                  flags=re.IGNORECASE)
    text = re.sub(r"\s*[-–]\s*(?:\d{4}\s*)?(?:digital\s+)?(?:re)?master(?:ed)?.*$",
                  "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*[-–]\s*official.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -–—·|")


def parse_synced(lrc_text):
    """LRC -> [(seconds, line)], sorted. Returns [] if it isn't LRC."""
    if not lrc_text:
        return []
    out = []
    for raw in str(lrc_text).splitlines():
        for match in re.finditer(r"\[(\d+):(\d+(?:\.\d+)?)\]", raw):
            minutes, seconds = int(match.group(1)), float(match.group(2))
            line = re.sub(r"\[\d+:\d+(?:\.\d+)?\]", "", raw).strip()
            if line:
                out.append((minutes * 60 + seconds, line))
    return sorted(out, key=lambda pair: pair[0])


def _result_to_dict(item):
    plain = (item.get("plainLyrics") or "").strip()
    synced = (item.get("syncedLyrics") or "").strip()
    if not plain and synced:
        plain = "\n".join(line for _, line in parse_synced(synced))
    if not plain:
        return None
    return {
        "title": item.get("trackName", ""),
        "artist": item.get("artistName", ""),
        "plain": plain,
        "synced": synced,
        "instrumental": bool(item.get("instrumental")),
    }


def fetch(artist, title):
    """Return a lyrics dict, or None when nothing matched.

    Raises LyricsError only when the lookup itself failed, so the caller
    can tell "no lyrics for this song" from "couldn't reach the server".
    """
    artist, title = _clean(artist), _clean(title)
    if not title:
        return None

    primary_error = None
    try:
        found = _fetch_lrclib(artist, title)
        if found:
            return found
    except LyricsError as exc:
        # lrclib unreachable - which is exactly what a network filter
        # looks like. Try the other service before reporting failure.
        primary_error = exc

    fallback, fallback_error = _fallback_lyrics(artist, title)
    if fallback:
        return fallback

    # Details go to the log only; the panel gets one plain sentence.
    _log(f"lrclib: {primary_error or 'no match'}; "
         f"lyrics.ovh: {fallback_error or 'no match'}")
    if primary_error or fallback_error:
        raise LyricsError("The lyrics service is busy right now. "
                          "Try again in a little while.", network=True)
    return None


def _fetch_lrclib(artist, title):
    """The primary source: lrclib, which has synced lyrics."""

    headers = {"User-Agent": USER_AGENT}

    # 1. Exact match.
    try:
        response = _get(f"{BASE}/get", headers,
                        params={"artist_name": artist, "track_name": title})
        if response.status_code == 200:
            parsed = _result_to_dict(response.json())
            if parsed:
                return parsed
        elif response.status_code not in (404,):
            response.raise_for_status()
    except requests.RequestException as exc:
        raise _network_error(exc) from exc
    except ValueError as exc:
        _log(f"unreadable response: {exc}")
        raise LyricsError("The lyrics service is busy right now. "
                          "Try again in a little while.") from exc

    # 2. Fuzzier search.
    query = " ".join(part for part in (artist, title) if part)
    try:
        response = _get(f"{BASE}/search", headers, params={"q": query})
        response.raise_for_status()
        results = response.json()
    except requests.RequestException as exc:
        raise _network_error(exc) from exc
    except ValueError as exc:
        _log(f"unreadable response: {exc}")
        raise LyricsError("The lyrics service is busy right now. "
                          "Try again in a little while.") from exc

    if not isinstance(results, list):
        return None
    for item in results[:5]:
        if isinstance(item, dict):
            parsed = _result_to_dict(item)
            if parsed:
                return parsed
    return None
