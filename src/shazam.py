"""
shazam.py - Identify what's currently playing on a stream.

How it works:
  1. player.capture_stream_to_wav() records a few seconds of the live
     stream to a small mono WAV (done with libmpv, which we already
     ship - no ffmpeg binary needed).
  2. shazamio_core (a small Rust extension) turns that WAV into a Shazam
     audio "signature". All the DSP happens locally.
  3. We POST the signature to Shazam's lookup endpoint and parse the
     result into a flat dict.

Only shazamio_core is bundled, not the full shazamio package - we make
the HTTP call with `requests` (already a dependency) instead of pulling
in aiohttp, pydantic, pydub and numpy for one POST. That keeps roughly
50 MB out of the AppImage.

Note this uses Shazam's own app endpoint, which is not a documented
public API. It works, but it is not something Apple offers or supports,
so treat it as best-effort: if it ever stops responding, the rest of the
app carries on unaffected.
"""
import asyncio
import json
import uuid

import urllib.parse

import requests

try:
    from shazamio_core import Recognizer
    AVAILABLE = True
    IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001 - any import problem disables the feature
    Recognizer = None
    AVAILABLE = False
    IMPORT_ERROR = str(exc)

ENDPOINT = (
    "https://amp.shazam.com/discovery/v5/en/US/android/-/tag/"
    "{uuid_1}/{uuid_2}"
    "?sync=true&webv3=true&sampling=true&connected="
    "&shazamapiversion=v3&sharehub=true&hubv5minorversion=v5.1"
    "&hidelb=true&video=v3"
)

HEADERS = {
    "User-Agent": (
        "Dalvik/2.1.0 (Linux; U; Android 13; Pixel 7 Build/TQ3A.230805.001)"
    ),
    "Content-Language": "en_US",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

TIMEOUT = 15


class ShazamError(Exception):
    pass


def _make_signature(wav_path):
    """Local DSP: WAV file -> Shazam signature object.

    recognize_path() is a pyo3-asyncio coroutine: it binds to whichever
    event loop is *running* at the moment it's called, so it has to be
    awaited from inside a coroutine. Calling it first and handing the
    result to run_until_complete() raises "no running event loop".
    """
    async def _run():
        recognizer = Recognizer()
        return await recognizer.recognize_path(value=wav_path)

    return asyncio.run(_run())


def _walk_strings(node):
    """Yield every string anywhere in a nested dict/list."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _walk_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_strings(value)


def _normalize_apple_url(url):
    """Turn Shazam's Apple link into one that opens the web player.

    Shazam hands back geo-redirect links - geo.music.apple.com - and
    tags them with app=music plus affiliate parameters. Those are what
    send you to a store listing instead of the song: the geo host
    redirects to whatever storefront it thinks you want, and app=music
    asks the platform to hand off to the native app, which on a phone
    without Apple Music installed means the Play Store.

    Stripping both leaves a plain music.apple.com URL, which plays in
    the browser and still hands off to the app when there is one.
    """
    if not url:
        return ""

    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc
    # geo.music.apple.com -> music.apple.com
    if host.startswith("geo."):
        host = host[4:]
    if not host.endswith("music.apple.com"):
        return url

    dropped = {"app", "at", "ct", "itscg", "itsct", "ls", "uo", "mt"}
    kept = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query)
        if key not in dropped
    ]
    return urllib.parse.urlunsplit((
        "https", host, parsed.path,
        urllib.parse.urlencode(kept), "",
    ))


def _apple_music_url(payload, title, artist):
    """Find a real Apple Music link in the response if there is one,
    otherwise fall back to an Apple Music search for the track."""
    best = ""
    for text in _walk_strings(payload):
        if not text.startswith("http") or "music.apple.com" not in text:
            continue
        # The HOST has to be Apple's, not merely a mention of it.
        #
        # Shazam's payload carries a Play Store link for the Apple Music
        # app, and it has "music.apple.com" inside it as a referrer
        # parameter - so a substring test picked the store page and sent
        # the user there instead of to the song.
        host = urllib.parse.urlsplit(text).netloc.lower()
        if not (host == "music.apple.com" or host.endswith(".music.apple.com")):
            continue
        cleaned = _normalize_apple_url(text)
        # A link to the song itself beats a link to the album or artist.
        if "i=" in cleaned or "/song/" in cleaned:
            return cleaned
        best = best or cleaned
    if best:
        return best

    terms = " ".join(part for part in (artist, title) if part).strip()
    if terms:
        query = requests.utils.quote(terms)
        return f"https://music.apple.com/us/search?term={query}"
    return ""


def _cover_art_url(track):
    images = track.get("images") or {}
    for key in ("coverarthq", "coverart", "background"):
        if images.get(key):
            return images[key]
    share = track.get("share") or {}
    return share.get("image", "") or ""


def _match_offset(payload):
    """How far into the song the sample matched, in seconds.

    This is what makes live lyrics possible on a radio stream: a stream
    has no track position of its own, but Shazam tells us where in the
    song our six-second sample landed. Position now = offset + (now -
    when we recorded). Returns None when the field is absent.
    """
    matches = payload.get("matches")
    if isinstance(matches, list) and matches:
        first = matches[0]
        if isinstance(first, dict):
            for key in ("offset", "timeSkew", "startoffset"):
                value = first.get(key)
                if isinstance(value, (int, float)) and key == "offset":
                    return float(value)
    return None


def _parse(payload):
    """Flatten Shazam's response into what the UI needs, or None if the
    lookup didn't match anything."""
    track = payload.get("track")
    if not isinstance(track, dict):
        return None

    title = (track.get("title") or "").strip()
    artist = (track.get("subtitle") or "").strip()
    if not title and not artist:
        return None

    return {
        "title": title,
        "artist": artist,
        "cover_url": _cover_art_url(track),
        "apple_music_url": _apple_music_url(payload, title, artist),
        "shazam_url": track.get("url", ""),
        # Seconds into the song at the moment we sampled it - used to
        # line synced lyrics up with what's actually playing.
        "match_offset": _match_offset(payload),
        "release_date": _release_date(track),
        # brat mode (task 48) needs the album.
        "album": _album(track),
    }


def _album(track):
    """The album, from the same metadata rows as the release date."""
    for section in (track.get("sections") or []):
        for row in (section.get("metadata") or []):
            if (row.get("title") or "").strip().lower() == "album":
                value = (row.get("text") or "").strip()
                if value:
                    return value
    return ""


def _release_date(track):
    """When the song came out, from Shazam's metadata block.

    Shazam returns this as free-form key/value pairs in the "Released"
    row rather than a structured field, so it can be a year or a full
    date depending on the track. Taken as given: reformatting a value
    whose format isn't guaranteed risks turning "1979" into nonsense.
    """
    sections = (track.get("sections") or [])
    for section in sections:
        for row in (section.get("metadata") or []):
            if (row.get("title") or "").strip().lower() in (
                "released", "release date"
            ):
                value = (row.get("text") or "").strip()
                if value:
                    return value
    # Some responses carry it at the top level instead.
    return (track.get("releasedate") or "").strip()


def identify(wav_path):
    """WAV file -> track dict, or None when nothing matched.

    Raises ShazamError if the lookup itself failed (network, bad
    response), so the caller can tell "no match" apart from "couldn't
    ask".
    """
    if not AVAILABLE:
        raise ShazamError(
            "Song identification isn't available in this build "
            f"({IMPORT_ERROR or 'shazamio_core missing'})."
        )

    try:
        signature = _make_signature(wav_path)
    except Exception as exc:  # noqa: BLE001
        raise ShazamError(f"Couldn't fingerprint the audio: {exc}") from exc

    body = {
        "timezone": "America/Phoenix",
        "signature": {
            "uri": signature.signature.uri,
            "samplems": signature.signature.samples,
        },
        "timestamp": signature.timestamp,
        "context": {},
        "geolocation": {},
    }

    url = ENDPOINT.format(
        uuid_1=str(uuid.uuid4()).upper(),
        uuid_2=str(uuid.uuid4()).upper(),
    )

    try:
        response = requests.post(
            url, headers=HEADERS, data=json.dumps(body), timeout=TIMEOUT
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise ShazamError(f"Lookup failed: {exc}") from exc
    except ValueError as exc:
        raise ShazamError(f"Unreadable response from Shazam: {exc}") from exc

    return _parse(payload)
