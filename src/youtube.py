"""
youtube.py - YouTube playlists as custom stations.

A YouTube playlist behaves exactly like a folder of local music here:
pick a random entry, join it partway through, cross-fade the static.
The only real difference is that the "files" have to be resolved over
the network, and the URLs they resolve to expire.

Two separate steps, deliberately:

  1. Listing the playlist. Flat extraction - titles and ids only, no
     per-video requests - which is fast enough to do on the fly and is
     cached to disk so a station doesn't re-list every time it plays.

  2. Resolving one entry to a playable audio URL. This has to happen at
     play time and cannot be cached: the URLs YouTube hands out are
     signed and expire after a few hours, so a cached one would work in
     testing and fail the next morning.

yt-dlp does the work. It is an optional dependency: without it, YouTube
stations simply report that they need it, the same way song
identification reports a missing fingerprinter, rather than failing in
some obscure way.

Worth knowing: YouTube changes its internals often, and yt-dlp is a
moving target that keeps up with them. A station that stops working
usually means yt-dlp needs updating rather than anything here being
broken. This also isn't something YouTube's terms of service
contemplate, which is worth being aware of even for a personal tool.
"""
import json
import os
import re
import time

_YT_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
             "music.youtube.com")

# Playlist listings are stable enough that re-listing on every play is
# pure waste; a day is long enough to be cheap and short enough that
# additions show up.
CACHE_TTL_SECONDS = 24 * 3600


class YouTubeError(Exception):
    pass


def available():
    """True when yt-dlp is importable."""
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False


def version():
    try:
        import yt_dlp
        return getattr(yt_dlp.version, "__version__", "unknown")
    except Exception:
        return ""


def looks_like_playlist(url):
    """A YouTube URL that names a playlist, channel or user."""
    text = (url or "").strip()
    if not text:
        return False
    if not any(host in text for host in _YT_HOSTS):
        return False
    return bool(
        re.search(r"[?&]list=", text)
        or "/playlist" in text
        or "/channel/" in text
        or "/@" in text
        or "/user/" in text
        or "/c/" in text
    )


def _cache_path(cache_dir, url):
    import hashlib
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, "youtube-%s.json" % digest)


def _read_cache(cache_dir, url, ttl=CACHE_TTL_SECONDS):
    path = _cache_path(cache_dir, url)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return None
    if time.time() - float(payload.get("fetched_at", 0)) > ttl:
        return None
    entries = payload.get("entries")
    return entries if isinstance(entries, list) and entries else None


def _write_cache(cache_dir, url, entries):
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(_cache_path(cache_dir, url), "w", encoding="utf-8") as handle:
            json.dump({"fetched_at": time.time(), "entries": entries}, handle)
    except Exception:
        pass


# Playlist url -> its name on YouTube, filled in as playlists are read.
_titles = {}


def playlist_title(url, cache_dir=None):
    """The playlist's own name on YouTube, or "".

    Used to name the station, so a custom station reads "Prog Essentials"
    rather than a playlist id. Filled in by playlist_entries, so this
    costs nothing extra once the playlist has been read once.
    """
    if url in _titles:
        return _titles[url]
    try:
        playlist_entries(url, cache_dir)
    except Exception:  # noqa: BLE001
        return ""
    return _titles.get(url, "")


def playlist_entries(url, cache_dir=None, force=False):
    """[{"id", "title", "duration"}] for a playlist.

    Flat extraction: one request for the whole list rather than one per
    video, which is the difference between a second and a minute on a
    few hundred entries.
    """
    if cache_dir and not force:
        cached = _read_cache(cache_dir, url)
        if cached:
            return cached

    if not available():
        raise YouTubeError(
            "YouTube stations need yt-dlp. Install it with "
            "'pip install --user yt-dlp' and restart."
        )

    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises many types
        raise YouTubeError("Couldn't read that playlist: %s" % exc) from exc

    if not info:
        raise YouTubeError("That playlist came back empty.")

    # The playlist's own name, kept so a station can be called after it.
    _titles[url] = (info.get("title") or "").strip()

    raw = info.get("entries") or []
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        video_id = item.get("id")
        if not video_id:
            continue
        # Private and deleted videos come back with no title; they'd
        # only fail later, so drop them here.
        title = (item.get("title") or "").strip()
        if not title or title.lower() in ("[private video]", "[deleted video]"):
            continue
        entries.append({
            "id": video_id,
            "title": title,
            "duration": float(item.get("duration") or 0.0),
        })

    if not entries:
        raise YouTubeError("No playable videos in that playlist.")

    if cache_dir:
        _write_cache(cache_dir, url, entries)
    return entries


def resolve_audio(video_id):
    """(stream_url, duration_seconds, title, thumbnail) for one video.

    Never cached. YouTube's media URLs are signed and expire within
    hours, so a cached one works in testing and fails the next morning -
    the worst kind of bug to chase.
    """
    if not available():
        raise YouTubeError("YouTube stations need yt-dlp.")

    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        # Audio only, best available. This is a radio player; pulling
        # video streams would waste bandwidth on data never decoded.
        "format": "bestaudio/best",
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                "https://www.youtube.com/watch?v=%s" % video_id, download=False
            )
    except Exception as exc:  # noqa: BLE001
        raise YouTubeError("Couldn't resolve that video: %s" % exc) from exc

    if not info:
        raise YouTubeError("That video isn't available.")

    stream_url = info.get("url")
    if not stream_url:
        # Some extractions only populate the format list.
        for fmt in info.get("formats") or []:
            if fmt.get("acodec") not in (None, "none") and fmt.get("url"):
                stream_url = fmt["url"]
                break
    if not stream_url:
        raise YouTubeError("No audio stream in that video.")

    # The thumbnail doubles as album art for the station. yt-dlp
    # returns a list ordered worst-to-best plus a "thumbnail" pick;
    # prefer its pick and fall back to the largest listed.
    thumbnail = info.get("thumbnail") or ""
    if not thumbnail:
        for candidate in reversed(info.get("thumbnails") or []):
            if candidate.get("url"):
                thumbnail = candidate["url"]
                break

    return (
        stream_url,
        float(info.get("duration") or 0.0),
        info.get("title") or "",
        thumbnail,
    )
