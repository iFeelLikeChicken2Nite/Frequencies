"""Frequencies — timed lyrics, from LRCLIB.

Plain lyrics tell you what the words are. Timed lyrics tell you where you
are in them, which is the difference between a page of text and singing
along.

LRCLIB is the source: free, no key, no account, and it answers with the
LRC format — a plain list of lines each stamped with the moment it
starts. Musixmatch and NetEase both carry timed lyrics too, but both want
credentials and neither wants to be used this way; LRCLIB exists for
exactly this and asks only that clients identify themselves.

What comes back is a list of (seconds, text). Nothing here decides how to
display it — that belongs to the panel, which knows how much room it has.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# lrcmux first, LRCLIB behind it.
#
# lrcmux is an aggregator by the author of Chorus: it asks several
# providers at once — LRCLIB, Musixmatch, NetEase and others — picks the
# best answer and caches it. It speaks the same requests and returns the
# same JSON as LRCLIB, which is why apps integrate it by changing a base
# URL and nothing else.
#
# So: ask the aggregator, and if it is down or has nothing, ask LRCLIB
# directly. One provider being unreachable should not mean no lyrics.
# lrcmux's LRCLIB-compatible endpoints, then LRCLIB itself.
#
# lrcmux has its own richer endpoint at /get — word-level timings, cover
# art, a sync level — but the compatibility routes are the ones whose
# parameters and response shape are written down: track_name,
# artist_name, album_name, duration in, an LRCLIB-shaped record out. Same
# aggregation across Genius, KuGou, LRCLIB and YouTube Music behind it,
# without guessing at field names.
#
# Worth revisiting: /get would give per-word highlighting, which the
# panel could use one day.
HOSTS = ("https://api.lrcmux.dev/compat/lrclib/api",
         "https://lrclib.net/api")
BASE = HOSTS[0]
USER_AGENT = "Frequencies/1.5 (https://github.com/iFeelLikeChicken2Nite)"

# [mm:ss.xx] or [mm:ss:xx], one or more per line — a line repeated in a
# song is stamped several times and shares its text.
_STAMP = re.compile(r"\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]")


# Being a good guest.
#
# These are free services with no key and no account, and the polite way
# to use one is to ask rarely and remember the answer. Three things do
# that here: results are cached, requests are spaced, and a run of
# failures stops the asking for a while rather than hammering a service
# that is already struggling.
CACHE_SECONDS = 6 * 3600        # a song's lyrics do not change
MISS_SECONDS = 30 * 60          # "not found" might be added later

# lrcmux allows 60 requests a minute and says cache hits are free. A
# second between requests keeps this app to at most 60 in that window
# even if it never stops asking, so the ceiling cannot be reached by
# accident.
MIN_GAP = 1.0                   # seconds between outbound requests
REQUESTS_PER_MINUTE = 60
COOLDOWN = 5 * 60               # quiet period after repeated failures
FAILURES_BEFORE_COOLDOWN = 3

_cache = {}
_cache_lock = threading.Lock()
_gate = threading.Lock()
_last_call = 0.0
_failures = 0
_quiet_until = 0.0


def _cached(key):
    with _cache_lock:
        entry = _cache.get(key)
        if not entry:
            return None
        value, until = entry
        if time.time() > until:
            _cache.pop(key, None)
            return None
        return value


def _remember(key, value):
    keep = CACHE_SECONDS if value.get("synced") or value.get("plain") \
        else MISS_SECONDS
    with _cache_lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = (value, time.time() + keep)


def _wait_turn():
    """Space the requests out, and honour a cooldown.

    Returns False when the service is being rested, so the caller can
    give up without a network call at all.
    """
    global _last_call
    with _gate:
        if time.time() < _quiet_until:
            return False
        gap = time.time() - _last_call
        if gap < MIN_GAP:
            time.sleep(MIN_GAP - gap)
        _last_call = time.time()
    return True


def _note_result(ok):
    """Count failures, and rest after a few in a row."""
    global _failures, _quiet_until
    with _gate:
        if ok:
            _failures = 0
            return
        _failures += 1
        if _failures >= FAILURES_BEFORE_COOLDOWN:
            _quiet_until = time.time() + COOLDOWN
            _failures = 0


def parse_lrc(text):
    """LRC text -> [(seconds, line)], in order.

    Blank lines are kept when they are stamped: a pause between verses is
    part of the timing, and dropping it makes the next line arrive early.
    Metadata tags like [ar:] are not timestamps and are skipped.
    """
    out = []
    for raw in (text or "").splitlines():
        stamps = list(_STAMP.finditer(raw))
        if not stamps:
            continue
        words = raw[stamps[-1].end():].strip()
        for stamp in stamps:
            minutes = int(stamp.group(1))
            seconds = int(stamp.group(2))
            fraction = stamp.group(3) or "0"
            # Two digits means hundredths, three means thousandths.
            divisor = 1000.0 if len(fraction) == 3 else 100.0
            at = minutes * 60 + seconds + int(fraction) / divisor
            out.append((at, words))
    out.sort(key=lambda pair: pair[0])
    return out


def _request(path, params, timeout=10, host=None):
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    request = urllib.request.Request(
        f"{host or BASE}/{path}?{query}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            # Told to wait, so wait — for as long as it asked, not for
            # however long this code felt like.
            _rest(exc.headers.get("Retry-After"))
        raise


def _rest(retry_after):
    """Honour a 429's Retry-After."""
    global _quiet_until
    try:
        seconds = float(str(retry_after).strip())
    except (TypeError, ValueError):
        seconds = 60.0
    with _gate:
        _quiet_until = max(_quiet_until,
                           time.time() + max(1.0, min(seconds, 600.0)))


def _ask(path, params, timeout):
    """Each host in turn, until one answers with something.

    An empty answer counts as an answer for the host that gave it, but
    not for the search: a provider that simply does not have the song
    should hand over to the next one.
    """
    # Always in the listed order — lrcmux first, every time.
    #
    # This used to move whichever host answered last to the front, left
    # over from when the right lrcmux address was still being worked out.
    # With the addresses settled it could only do harm: one song lrcmux
    # happened not to have, answered by LRCLIB instead, and LRCLIB was
    # asked first for the rest of the session. The aggregator is the
    # better source; it gets asked first on every lookup, and LRCLIB is
    # only ever the fallback.
    for host in HOSTS:
        try:
            found = _request(path, params, timeout, host=host)
        except Exception:                       # noqa: BLE001
            continue
        if found:
            return found, host
    return None, ""


def fetch(artist, title, album="", duration=0, timeout=10):
    """Timed and plain lyrics for one track.

    Returns {"synced": [(seconds, line)], "plain": str, "source": str} —
    empty fields rather than None, so a caller never has to check which
    kind it got before looking.

    The exact endpoint is tried first because it matches on duration and
    so cannot hand back a different recording of the same song; search is
    the fallback for when the duration is unknown, which is most of the
    time on a radio stream.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return {"synced": [], "plain": "", "source": ""}

    # Asked once per song, not once per station change.
    #
    # The same track turns up again — flipping back to a station, a
    # channel that repeats it an hour later — and without this every one
    # of those is another request for an answer already known.
    key = f"{artist}|{title}|{int(duration or 0)}".lower()
    hit = _cached(key)
    if hit is not None:
        return dict(hit, source=hit.get("source", "") + " (cached)"
                    if hit.get("source") else "")

    if not _wait_turn():
        return {"synced": [], "plain": "", "source": ""}

    found = None
    host = ""
    if duration:
        found, host = _ask("get", {
            "artist_name": artist, "track_name": title,
            "album_name": album, "duration": int(duration),
        }, timeout)

    if not found:
        results, host = _ask("search", {
            "artist_name": artist, "track_name": title,
        }, timeout)
        if isinstance(results, list) and results:
            # Whichever match actually has timings, in preference to the
            # first: a search often returns several versions and only
            # some of them are stamped.
            found = next((r for r in results if r.get("syncedLyrics")),
                         results[0])

    if not isinstance(found, dict):
        _note_result(False)
        # A miss is remembered too, so a song nobody has lyrics for is
        # not looked up again every time it plays.
        _remember(key, {"synced": [], "plain": "", "source": ""})
        return {"synced": [], "plain": "", "source": ""}

    _note_result(True)
    result = {
        "synced": parse_lrc(found.get("syncedLyrics") or ""),
        "plain": (found.get("plainLyrics") or "").strip(),
        "source": "lrcmux" if "lrcmux" in host else "LRCLIB",
    }
    _remember(key, result)
    return result


def line_at(lines, seconds):
    """Which line is current at this moment, as an index, or -1.

    A plain scan: a song has a few dozen lines, and the position moves
    every tick, so anything cleverer would be optimising the wrong end.
    """
    if not lines:
        return -1
    found = -1
    for index, (at, _text) in enumerate(lines):
        if at <= seconds:
            found = index
        else:
            break
    return found
