"""
radiobrowser.py - Minimal client for the radio-browser.info API.

The API is served by a rotating pool of mirrors. The recommended way to
pick one is to resolve the DNS name "all.api.radio-browser.info" to get
the list of currently active servers, then use any of them as the base
URL. We fall back to a small static list of known mirrors if DNS
resolution fails (e.g. no network at startup).
"""
import socket
import time

import requests

APP_USER_AGENT = "Frequencies/1.0 (AppImage; +https://github.com/)"

FALLBACK_HOSTS = [
    "de1.api.radio-browser.info",
    "de2.api.radio-browser.info",
    "at1.api.radio-browser.info",
    "nl1.api.radio-browser.info",
]

_cached_base_url = None


def _resolve_base_url():
    global _cached_base_url
    if _cached_base_url:
        return _cached_base_url

    hosts = []
    try:
        infos = socket.getaddrinfo(
            "all.api.radio-browser.info", 80, proto=socket.IPPROTO_TCP
        )
        ips = sorted({info[4][0] for info in infos})
        for ip in ips:
            try:
                hostname, _, _ = socket.gethostbyaddr(ip)
                hosts.append(hostname)
            except socket.herror:
                continue
    except socket.gaierror:
        pass

    hosts.extend(FALLBACK_HOSTS)

    for host in hosts:
        base = f"https://{host}"
        try:
            r = requests.get(
                f"{base}/json/stats",
                headers={"User-Agent": APP_USER_AGENT},
                timeout=4,
            )
            if r.ok:
                _cached_base_url = base
                return base
        except requests.RequestException:
            continue

    # last resort: just return the first fallback and let the caller's
    # request fail with a clear error the UI can show
    _cached_base_url = f"https://{FALLBACK_HOSTS[0]}"
    return _cached_base_url


class RadioBrowserError(Exception):
    pass


# The public map. Opened in a browser rather than embedded: it is a
# Leaflet page with thousands of markers, and reimplementing it inside
# the app would be a worse version of something that already exists.
MAP_URL = "https://www.radio-browser.info/map"

# What "sort by" offers.
#
# Geo, codec and tag are gone. Tags belong in the search itself - you
# should be able to type "jazz" and find jazz stations without choosing
# a mode first - and sorting by codec is a technical detail nobody
# browses by. Clicks is the default because popularity is the most
# useful prior when you know nothing else about a station.
# Trending and Random have buttons of their own now.
#
# Both are ways of asking "show me something", not ways of ordering a
# search you have already typed — burying them in a sort dropdown made
# them look like the latter. They are still valid sort values, so the
# buttons pass them straight through.
SORT_MODES = (
    ("clickcount", "Most listened"),
    ("votes", "Most voted"),
    ("name", "Name"),
)
DEFAULT_SORT = "clickcount"


def _station_from(item):
    return {
        "uuid": item.get("stationuuid", ""),
        "name": item.get("name", "").strip() or "Unnamed station",
        "url": item.get("url_resolved") or item.get("url") or "",
        "country": item.get("country", ""),
        "countrycode": item.get("countrycode", ""),
        "language": item.get("language", ""),
        "tags": item.get("tags", ""),
        "favicon": item.get("favicon", ""),
        # Kept because most stations have no favicon at all, and their
        # site almost always has an icon we can derive one from.
        "homepage": item.get("homepage", ""),
        "codec": item.get("codec", ""),
        "bitrate": item.get("bitrate", 0),
        "votes": item.get("votes", 0),
        "clickcount": item.get("clickcount", 0),
        "lat": item.get("geo_lat"),
        "lon": item.get("geo_long"),
    }


def _request(path, params, attempts=3):
    """One call to the directory, retried on a dropped connection.

    The mirrors close idle connections and throttle bursts, so a request
    made a moment after the last one — pressing "More", say — can come
    back as a reset rather than an answer. That is not a failure worth
    showing anyone: it means try again, on a fresh connection.

    Each attempt resolves the base URL again, so a mirror that has gone
    unhealthy is not retried forever.
    """
    last = None
    for attempt in range(max(1, attempts)):
        base = _resolve_base_url()
        try:
            r = requests.get(
                f"{base}{path}",
                params=params,
                headers={"User-Agent": APP_USER_AGENT,
                         # Asking for a fresh connection each time, since
                         # a pooled one is what gets reset.
                         "Connection": "close"},
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.4 * (attempt + 1))
                continue
    raise RadioBrowserError(_plain_error(last)) from last


def _plain_error(exc):
    """A sentence, not a socket trace.

    "Connection aborted, ConnectionResetError(104, ...)" tells the
    reader nothing they can act on.
    """
    text = str(exc or "")
    if "reset" in text.lower() or "aborted" in text.lower():
        return "The station directory dropped the connection. Try again."
    if "timed out" in text.lower() or "timeout" in text.lower():
        return "The station directory did not answer in time."
    return "Could not reach the station directory."


def _haversine(lat1, lon1, lat2, lon2):
    import math
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lon / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(min(1.0, a)))


def search_stations(query, limit=40, order=DEFAULT_SORT, reverse=True,
                    country="", language="", near=None, offset=0):
    """Free-text search across names AND tags.

    Two requests, merged. The API matches `name` against the name only,
    so a search for "jazz" would miss every station that is tagged jazz
    but not named it - which is most of them. Searching both and
    merging is what makes tags "factored into search" rather than a
    separate mode the user has to know to pick.

    `near` is (lat, lon): results that carry coordinates are sorted by
    distance from it. Sorting locally rather than asking the API to do
    it avoids depending on query parameters whose availability varies
    between mirrors.
    """
    common = {
        # Fetch past the offset, then slice.
        #
        # The two searches are merged and de-duplicated here, so asking
        # the API for exactly one page would leave holes once the
        # overlap between them is removed. Requesting the whole span up
        # to the end of the wanted page and slicing after the merge is
        # what makes page two actually follow page one.
        "limit": offset + limit,
        "offset": 0,
        "order": order,
        "reverse": "true" if reverse else "false",
        "hidebroken": "true",
    }
    if country:
        common["country"] = country

    results = []
    seen = set()

    # Every spelling of the chosen language.
    #
    # Merging "rus" and the Cyrillic spelling into one Russian entry is
    # only half the job: the directory still files stations under each
    # original spelling, so asking for one of them would quietly find a
    # fraction of what is there.
    language_terms = language_spellings(language) if language else [""]
    # The chosen spelling first, so the primary request is the one the
    # caller asked for and the alternates are genuinely extra.
    if language:
        canonical = canonical_language(language)
        language_terms.sort(key=lambda term: (term != language,
                                              term != canonical))

    searches = []
    if query.strip():
        searches.append({"name": query})
        searches.append({"tag": query})
    else:
        # No text: browse by whatever filters are set.
        searches.append({})

    # One request per search shape per language spelling.
    requests_to_make = [
        {**common, **extra, **({"language": term} if term else {})}
        for extra in searches
        for term in language_terms
    ]

    for params in requests_to_make:
        try:
            data = _request("/json/stations/search", params)
        except RadioBrowserError:
            if not results:
                raise
            continue
        for item in data:
            station = _station_from(item)
            if not station["url"] or station["uuid"] in seen:
                continue
            seen.add(station["uuid"])
            results.append(station)

    if near:
        def distance(station):
            try:
                return _haversine(near[0], near[1],
                                  float(station["lat"]), float(station["lon"]))
            except (TypeError, ValueError):
                return float("inf")
        for station in results:
            d = distance(station)
            station["distance_km"] = None if d == float("inf") else d
        results.sort(key=lambda s: (s["distance_km"] is None,
                                    s["distance_km"] or 0.0))

    return results[offset:offset + limit]


def new_stations(limit=40, country="", language=""):
    """Recently added, changed or played - one "New" list.

    "Lately played" and "lately changed" were two menu entries that
    answered nearly the same question. Merged, ordered by whichever
    signal is fresher, they make one list worth glancing at.
    """
    common = {
        "limit": max(10, limit // 2),
        "reverse": "true",
        "hidebroken": "true",
    }
    if country:
        common["country"] = country
    if language:
        common["language"] = language

    results = []
    seen = set()
    for order in ("changetimestamp", "clicktimestamp"):
        try:
            data = _request("/json/stations/search",
                            {**common, "order": order})
        except RadioBrowserError:
            continue
        for item in data:
            station = _station_from(item)
            if not station["url"] or station["uuid"] in seen:
                continue
            seen.add(station["uuid"])
            results.append(station)
    return results[:limit]


# Where the featured list lives. A plain JSON file in a repo: no server
# to run, no API to keep up, and editing it is a commit.
FEATURED_URL = (
    "https://raw.githubusercontent.com/"
    "iFeelLikeChicken2Nite/featured/main/featured.json"
)

# Shown when there is nothing to show.
FEATURED_EMPTY = (
    "more like uh, uncurated frequencies am i right? "
    "(there seems to be no curated frequency data today, sorry!)"
)


def featured_stations(cache_dir=None, force=False):
    """Today's featured stations, fetched once a day.

    Cached by date rather than by age: the list changes at most daily,
    so a stamp of "which day's list is this" is simpler than a timer and
    survives restarts. A failed fetch falls back to the cached copy -
    yesterday's picks beat an empty panel - and only an empty cache
    produces nothing.
    """
    import datetime
    import json
    import os

    today = datetime.date.today().isoformat()
    path = None
    if cache_dir:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            path = os.path.join(cache_dir, "featured.json")
        except Exception:  # noqa: BLE001
            path = None

    cached = None
    if path and os.path.exists(path):
        try:
            with open(path) as handle:
                cached = json.load(handle)
        except Exception:  # noqa: BLE001
            cached = None
        if not force and cached and cached.get("date") == today:
            return [_featured_entry(item) for item in cached.get("stations", [])]

    try:
        response = requests.get(
            FEATURED_URL,
            timeout=8,
            headers={"User-Agent": APP_USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001
        # Yesterday's list is better than none.
        if cached:
            return [_featured_entry(item) for item in cached.get("stations", [])]
        return []

    stations = payload if isinstance(payload, list) else payload.get("stations", [])
    if not isinstance(stations, list):
        return []

    if path:
        try:
            with open(path, "w") as handle:
                json.dump({"date": today, "stations": stations}, handle)
        except Exception:  # noqa: BLE001
            pass

    return [_featured_entry(item) for item in stations]


def _featured_entry(item):
    """A featured entry: how to show it, plus everything to import.

    The list is hand-curated rather than scraped from the directory, so
    an entry can carry a whole station - tags, EQ, artwork, the lot -
    and not just a name and a URL. `settings` holds that verbatim; the
    importer decides which of it is safe to keep.
    """
    entry = _station_from(item)
    entry["settings"] = item if isinstance(item, dict) else {}
    return entry


def countries():
    """[(name, code)] for the country filter."""
    try:
        data = _request("/json/countries", {"hidebroken": "true"})
    except RadioBrowserError:
        return []
    out = []
    for item in data:
        name = (item.get("name") or "").strip()
        if name and item.get("stationcount", 0) > 0:
            out.append((name, item.get("iso_3166_1") or name))
    out.sort(key=lambda pair: pair[0].lower())
    return out


# The languages the filter offers.
#
# radio-browser's list is free text typed by whoever submitted each
# station, so one language arrives under dozens of spellings: its own
# name, an English name, another language's name for it, and a long
# tail of typos - "Engilsh", "Englidh", "Englisg", "Engli". A table of
# known aliases cannot keep up with that, and every one it misses shows
# up as another entry in the list.
#
# So the list is fixed here instead, and anything the directory sends
# is matched onto it. What does not match is not shown.
CANONICAL_LANGUAGES = (
    "afrikaans", "akan", "albanian", "arabic", "azerbaijani", "bangla",
    "belarusian", "bosnian", "bulgarian", "cantonese", "catalan",
    "chinese", "croatian", "czech", "danish", "dutch", "english",
    "estonian", "filipino", "finnish", "flemish", "french", "galician",
    "georgian", "german", "german (swiss)", "greek", "hebrew", "hindi",
    "hokkien", "hungarian", "indonesian", "italian", "japanese",
    "kannada", "kazakh", "korean", "kurdish", "latvian", "lithuanian",
    "luganda", "macedonian", "malay", "malayalam", "mandarin",
    "mongolian", "montenegrin", "norwegian", "persian", "polish",
    "portuguese", "português (brasil)", "punjabi", "romanian",
    "russian", "serbian", "sinhala", "slovak", "slovenian", "spanish",
    "swahili", "swedish", "tagalog", "tamil", "telugu", "thai",
    "turkish", "ukrainian", "urdu", "vietnamese", "welsh",
)

# Spellings fuzzy matching will never get: a different language's word
# for it, a country standing in for a language, a native name in
# another script.
LANGUAGE_ALIASES = {
    "engels": "english", "anglais": "english", "ingles": "english",
    "inglés": "english", "en": "english", "en gb": "english",
    "en us": "english", "eng": "english",
    "duits": "german", "deutsch": "german", "allemand": "german",
    "de": "german", "deutsch fränkisch": "german", "low german": "german",
    "plattdeutsch": "german", "swiss german": "german (swiss)",
    "schweizerdeutsch": "german (swiss)",
    "francais": "french", "francaise": "french", "français": "french",
    "fr": "french",
    "castellano": "spanish", "castellano. español": "spanish",
    "español": "spanish", "espanol": "spanish", "es": "spanish",
    "espanish": "spanish",
    "português": "portuguese", "portugues": "portuguese",
    "pt": "portuguese",
    "pt-br": "português (brasil)", "pt br": "português (brasil)",
    "brazilian portuguese": "português (brasil)",
    "brasil": "português (brasil)",
    "rus": "russian", "русский": "russian", "язык: русский": "russian",
    "ру": "russian",
    "türkçe": "turkish", "turkce": "turkish",
    "romania": "romanian", "română": "romanian", "limba română": "romanian",
    "bahasa indonesia": "indonesian", "bahasa": "indonesian",
    "montenegro": "montenegrin",
    "nederlands": "dutch", "vlaams": "flemish",
    "italiano": "italian", "polski": "polish", "svenska": "swedish",
    "suomi": "finnish", "dansk": "danish", "norsk": "norwegian",
    "eesti": "estonian", "ελληνικά": "greek", "日本語": "japanese",
    "中文": "chinese", "한국어": "korean", "עברית": "hebrew",
    "українська": "ukrainian", "ukranian": "ukrainian",
}

# What each canonical name has actually been seen as, filled in by
# languages(). A search has to ask for every spelling, because the
# directory files stations under whichever one was typed.
_language_spellings = {}


def _normalise_language(name):
    """Lowercase, unaccented, punctuation-free, for matching."""
    import unicodedata

    text = unicodedata.normalize("NFKD", (name or "").strip().lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    for character in "._-()[]/,":
        text = text.replace(character, " ")
    return " ".join(text.split())


def canonical_language(name):
    """The canonical language for a directory spelling, or "".

    Exact match, then a known alias, then a close spelling. The last of
    those is what catches the long tail of typos without needing every
    one listed: "Englidh" and "Englisg" are both a character or two from
    "english" and nothing else.
    """
    import difflib

    key = _normalise_language(name)
    if not key:
        return ""

    if key in CANONICAL_LANGUAGES:
        return key
    if key in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[key]

    # Aliases and canonical names, compared without accents so the
    # table can be written the natural way.
    targets = {}
    for canonical in CANONICAL_LANGUAGES:
        targets[_normalise_language(canonical)] = canonical
    for spelling, canonical in LANGUAGE_ALIASES.items():
        targets[_normalise_language(spelling)] = canonical

    if key in targets:
        return targets[key]

    close = difflib.get_close_matches(key, list(targets), n=1, cutoff=0.82)
    if close:
        return targets[close[0]]

    # A compound like "English/Tagalog" or "English - Twi": take the
    # first part that is recognisable rather than discarding it.
    for part in key.replace("/", " ").split():
        if part in targets:
            return targets[part]
    return ""


def language_spellings(label):
    """Every directory spelling seen for this language."""
    label = canonical_language(label) or label
    spellings = set(_language_spellings.get(label) or ())
    spellings.add(label)
    for spelling, target in LANGUAGE_ALIASES.items():
        if target == label:
            spellings.add(spelling)
    return sorted(spellings)


def languages():
    """The languages to offer, from what the directory actually has.

    Only names that match one of CANONICAL_LANGUAGES are shown. The
    directory's own spellings are remembered so a search can ask for
    all of them.
    """
    try:
        data = _request("/json/languages", {"hidebroken": "true"})
    except RadioBrowserError:
        return []

    _language_spellings.clear()
    for item in data:
        name = (item.get("name") or "").strip()
        if not name or item.get("stationcount", 0) <= 0:
            continue
        canonical = canonical_language(name)
        if not canonical:
            continue                     # not a language we offer
        _language_spellings.setdefault(canonical, set()).add(name)

    return sorted(_language_spellings, key=str.lower)

def register_click(station_uuid):
    """Best-effort ping to radio-browser.info that a station was played.
    Helps keep their stats useful; failures are ignored."""
    if not station_uuid:
        return
    base = _resolve_base_url()
    try:
        requests.get(
            f"{base}/json/url/{station_uuid}",
            headers={"User-Agent": APP_USER_AGENT},
            timeout=4,
        )
    except requests.RequestException:
        pass


def geocode(place):
    """(latitude, longitude, label) for a place name, or None.

    OpenStreetMap's Nominatim, which needs no key and asks only that
    callers identify themselves. Used once when someone sets their
    location, not per search.
    """
    if not place:
        return None
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": place, "format": "json", "limit": 1},
            headers={"User-Agent": APP_USER_AGENT},
            timeout=15,
        )
        results = response.json()
    except Exception:  # noqa: BLE001
        return None
    if not results:
        return None
    first = results[0]
    try:
        return (float(first["lat"]), float(first["lon"]),
                first.get("display_name", place))
    except (KeyError, TypeError, ValueError):
        return None
