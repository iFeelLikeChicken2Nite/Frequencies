"""Frequencies — Twitch as a radio source.

Audio only. A great deal of Twitch is somebody talking or playing records,
and for that the video is a waste of a decoder — but there is no audio
endpoint, so the stream is resolved with yt-dlp and mpv is told to leave
the video track alone.

Nothing here touches chat or an account. A channel name goes in, a
playable URL comes out, and it is refreshed when the stream ends and
starts again — Twitch URLs are signed and expire, which is the one thing
that makes this different from an ordinary stream.

`profile(name)` (build 76, task 77) reads the channel's own "About" — its
name, bio and profile picture — for the artist panel.
"""

import html
import re
import subprocess
import threading
import time

CHANNEL = re.compile(r"^[A-Za-z0-9_]{3,25}$")


def available():
    """yt-dlp, which is what resolves the stream."""
    try:
        import importlib.util
        return importlib.util.find_spec("yt_dlp") is not None
    except Exception:                           # noqa: BLE001
        return False


def channel_name(text):
    """Accept a name, a URL, or something pasted from the address bar."""
    text = (text or "").strip()
    if not text:
        return ""
    if "twitch.tv" in text:
        tail = text.split("twitch.tv/", 1)[1]
        text = tail.split("/")[0].split("?")[0]
    text = text.lstrip("@")
    return text if CHANNEL.match(text) else ""


def channel_url(name):
    return f"https://www.twitch.tv/{name}"


# The last picture seen for each channel, by name.
#
# Kept here rather than pushed through resolve's return value, which two
# callers already unpack — and definitely not in the HTTP headers, which
# go to the player.
_channel_art = {}


def channel_art(name):
    """The channel's picture, if a resolve has seen one."""
    return _channel_art.get((name or "").lower(), "")


def resolve(name, timeout=25):
    """(url, reason, headers) for a live channel.

    The headers matter as much as the URL. Twitch signs its manifests and
    checks the referer and origin on the media requests that follow, so a
    URL handed to a player without them connects and then receives
    nothing — which sounds exactly like a stream that is up but silent.

    Returns ("", reason) when there is nothing to play, and the reason
    matters: an extractor error and an offline channel both used to come
    back as an empty string, so every failure was reported as "not live"
    — including the format-selection failures that were the actual
    problem.

    "bestaudio/best" rather than "bestaudio/worst": Twitch's HLS rarely
    publishes an audio-only rendition, so asking for one and falling back
    to *worst* meant asking for a format that often does not exist and
    then taking 160p. mpv is told not to decode video anyway, so the
    honest request is the best stream and let it drop the picture.
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return "", "yt-dlp is not installed", {}

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "bestaudio/best",
        "socket_timeout": timeout,
        # No extractor_args: an empty client_id list was being handed to
        # the Twitch extractor for no reason, and it is the sort of thing
        # that breaks quietly on a yt-dlp update.
    }
    def _log(text):
        try:
            import crashlog
            crashlog.write("[twitch] " + text)
        except Exception:                       # noqa: BLE001
            pass

    _log(f"resolving {name}")
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(channel_url(name), download=False)
    except Exception as exc:                    # noqa: BLE001
        _log(f"{name}: extract failed: {exc!r}")
        return "", f"could not read the channel ({exc})", {}

    if not info:
        return "", "no channel information came back", {}

    # live_status is the current field; is_live is the old one, and a
    # live channel may set either. Absent from both means unknown, which
    # is not the same as offline.
    status = info.get("live_status")
    if status in ("not_live", "was_live", "post_live"):
        _log(f"{name}: live_status={status}")
        return "", "not live right now", {}
    if status is None and info.get("is_live") is False:
        return "", "not live right now", {}

    url = info.get("url") or ""
    headers = dict(info.get("http_headers") or {})
    if not url:
        for fmt in reversed(info.get("formats") or []):
            if fmt.get("url"):
                url = fmt["url"]
                headers = dict(fmt.get("http_headers") or headers)
                break
    if not url:
        return "", "live, but no playable stream was offered", {}
    # Referer and origin are the two Twitch actually checks; a full
    # user-agent from yt-dlp is worth carrying as well.
    headers.setdefault("Referer", "https://www.twitch.tv/")
    headers.setdefault("Origin", "https://www.twitch.tv")

    # The channel's own picture, for the station's artwork.
    #
    # A Twitch station had no art at all: nothing identifies a live
    # stream, so nothing ever went looking. yt-dlp already hands back the
    # channel's thumbnail with everything else, and it is the right image
    # — it is what the channel calls itself.
    picture = (info.get("thumbnail") or "").strip()
    if not picture:
        for candidate in reversed(info.get("thumbnails") or []):
            if isinstance(candidate, dict) and candidate.get("url"):
                picture = candidate["url"]
                break
    if picture:
        _channel_art[name.lower()] = picture
    _log(f"{name}: live, url {url[:70]}… ({len(headers)} headers, "
         f"keys {sorted(headers)})")
    return url, "", headers


def title(name, timeout=15):
    """What the channel is streaming, for the station subtitle."""
    try:
        from yt_dlp import YoutubeDL
        with YoutubeDL({"quiet": True, "no_warnings": True,
                        "skip_download": True,
                        "socket_timeout": timeout}) as ydl:
            info = ydl.extract_info(channel_url(name), download=False)
        return (info or {}).get("title") or name
    except Exception:                           # noqa: BLE001
        return name


def station_for(name, resolved_url=""):
    """A station record for a Twitch channel.

    The channel is kept alongside the playable URL because the playable
    one expires: when the stream drops, the channel is what gets asked
    again.
    """
    return {
        "name": name,
        "kind": "twitch",
        "twitch_channel": name,
        "url": resolved_url or channel_url(name),
        # Audio only, whatever the format turns out to carry.
        "video": False,
        "art_source": "stream",
    }


# -- the channel's own "About" (build 76, task 77) --------------------------
#
# A Twitch station's artist panel was empty: there is no artist, and
# nothing looked the streamer up. Twitch's own website asks its GraphQL
# endpoint for the channel's name, bio and picture with a public client
# id (the same one yt-dlp uses), no account. That is tried first. If it
# is refused, the channel page's own preview tags (og:description,
# og:image — what a link to the channel shows in a chat app) carry the
# bio and the picture too.
#
# Neither could be tried from where this was written (twitch.tv can't be
# reached there), so both are guesses until the log says which worked:
#   [twitch] profile for '<name>': from Twitch's API | from the channel page …
#   [twitch] profile for '<name>': nothing found (api: …; page: …)

GQL_URL = "https://gql.twitch.tv/gql"
WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
BROWSER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# A channel's bio and picture change rarely: asked once per half hour.
PROFILE_KEEP_S = 30 * 60

_profiles = {}                  # name -> (time asked, profile)
_profiles_lock = threading.Lock()

_FULL_QUERY = ("query($login: String!) { user(login: $login) { "
               "login displayName description profileImageURL(width: 300) "
               "followers { totalCount } stream { game { name } } } }")
# Only the three that matter, in case Twitch refuses one of the extras.
_CORE_QUERY = ("query($login: String!) { user(login: $login) { "
               "login displayName description profileImageURL(width: 300) "
               "} }")


def _log(text):
    try:
        import crashlog
        crashlog.write("[twitch] " + text)
    except Exception:                           # noqa: BLE001
        pass


def _from_api(name, timeout):
    """(profile, why) from Twitch's GraphQL endpoint."""
    import requests
    why = ""
    for query in (_FULL_QUERY, _CORE_QUERY):
        reply = requests.post(
            GQL_URL, timeout=timeout,
            headers={"Client-Id": WEB_CLIENT_ID,
                     "User-Agent": BROWSER_AGENT},
            json={"query": query, "variables": {"login": name.lower()}})
        if reply.status_code != 200:
            return {}, f"HTTP {reply.status_code}"
        body = reply.json()
        if isinstance(body, list):
            body = body[0] if body else {}
        user = ((body or {}).get("data") or {}).get("user")
        errors = (body or {}).get("errors") or []
        if user:
            followers = (user.get("followers") or {}).get("totalCount")
            game = (((user.get("stream") or {}).get("game") or {})
                    .get("name") or "")
            return {
                "name": (user.get("displayName") or "").strip(),
                "bio": (user.get("description") or "").strip(),
                "photo": (user.get("profileImageURL") or "").strip(),
                "followers": followers if isinstance(followers, int) else None,
                "game": game.strip(),
            }, why
        if errors:
            why = "; ".join(
                str(e.get("message", e) if isinstance(e, dict) else e)[:80]
                for e in errors[:2])
            why = "full query refused: " + why
            continue                # try again with the plain query
        return {}, "no such channel"
    return {}, why or "nothing came back"


_META = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r'([a-zA-Z:-]+)\s*=\s*"([^"]*)"')
# Twitch's own sentence for a channel with no bio of its own.
_DEFAULT_BIO = "streams live on twitch"


def page_profile(text):
    """A profile from the channel page's HTML (its og: preview tags)."""
    tags = {}
    for tag in _META.findall(text or ""):
        attrs = {k.lower(): v for k, v in _ATTR.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key and "content" in attrs:
            tags.setdefault(key, html.unescape(attrs["content"]).strip())
    title = tags.get("og:title", "")
    if title.endswith(" - Twitch"):
        title = title[:-len(" - Twitch")]
    bio = tags.get("og:description") or tags.get("description") or ""
    if _DEFAULT_BIO in bio.lower():
        bio = ""                    # only Twitch's stock line
    photo = tags.get("og:image", "")
    return {"name": title.strip(), "bio": bio, "photo": photo,
            "followers": None, "game": ""}


def _from_page(name, timeout):
    """(profile, why) from the channel's web page."""
    import requests
    reply = requests.get(channel_url(name), timeout=timeout,
                         headers={"User-Agent": BROWSER_AGENT,
                                  "Accept-Language": "en"})
    if reply.status_code != 200:
        return {}, f"HTTP {reply.status_code}"
    found = page_profile(reply.text)
    if not (found["bio"] or found["photo"]):
        return {}, "no preview tags in the page"
    return found, ""


def profile(name, timeout=10):
    """The channel's name, bio and profile picture, or {}.

    {"name", "bio", "photo" (a link), "followers" (int or None), "game"
    (what it is streaming now, or ""), "url", "source"}. Runs on a
    worker, never on the GUI thread: it is one or two network calls.
    """
    name = (name or "").strip()
    if not CHANNEL.match(name):
        return {}
    key = name.lower()
    with _profiles_lock:
        kept = _profiles.get(key)
    if kept and time.monotonic() - kept[0] < PROFILE_KEEP_S:
        return dict(kept[1])

    found, api_why, page_why = {}, "", ""
    try:
        found, api_why = _from_api(name, timeout)
        source = "Twitch's API"
    except Exception as exc:                    # noqa: BLE001
        api_why = f"{type(exc).__name__}: {str(exc)[:80]}"
    if not found:
        try:
            found, page_why = _from_page(name, timeout)
            source = "the channel page"
        except Exception as exc:                # noqa: BLE001
            page_why = f"{type(exc).__name__}: {str(exc)[:80]}"
    if not found:
        _log(f"profile for '{name}': nothing found (api: {api_why}; "
             f"page: {page_why})")
        return {}
    found["url"] = channel_url(name)
    found["source"] = source
    extras = []
    if found.get("game"):
        extras.append(f"streaming {found['game']}")
    if found.get("followers") is not None:
        extras.append(f"{found['followers']} followers")
    _log(f"profile for '{name}': from {source}"
         f"{' (api: ' + api_why + ')' if api_why else ''} - "
         f"name '{found.get('name') or name}', "
         f"bio {len(found.get('bio') or '')} chars, "
         f"photo {'yes' if found.get('photo') else 'no'}"
         f"{', ' + ', '.join(extras) if extras else ''}")
    with _profiles_lock:
        _profiles[key] = (time.monotonic(), dict(found))
    return found


def profile_subtitle(found):
    """The line under the name: "Music · 12,345 followers"."""
    parts = []
    if (found or {}).get("game"):
        parts.append(found["game"])
    followers = (found or {}).get("followers")
    if isinstance(followers, int):
        parts.append(f"{followers:,} follower{'' if followers == 1 else 's'}")
    return " · ".join(parts)
