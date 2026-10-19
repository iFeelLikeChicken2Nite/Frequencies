"""
artistinfo.py - who you're listening to.

Two separate sources, each optional and each failing on its own:

  MusicBrainz + Wikipedia   a short biography and the basic facts

They're in one module because they answer one question - "what is this
artist?" - and because the panel wants them together. But nothing here
depends on anything else: a failed artwork lookup still leaves you with
a biography, and vice versa.

No API keys. MusicBrainz and Wikipedia are both open,
artwork is read from the public album page.

Every network call is blocking - callers run this on a worker thread.
"""
import json
import math
import re
import time
import urllib.parse

import requests

USER_AGENT = "Frequencies/1.5 (personal internet radio player)"
TIMEOUT = 12

_cache = {}
CACHE_TTL = 6 * 3600


def forget_cached_bios():
    """Drop cached biographies so a new correction takes effect at once."""
    for key in [k for k in _cache if k and k[0] == "bio"]:
        _cache.pop(key, None)


def _cached(key, produce):
    """One-shot memo with an expiry.

    Artist facts don't change minute to minute, and the panel re-asks
    every time a song is identified - which on a music station is every
    few minutes. Without this, a single listening session would hammer
    three services for answers that never changed.
    """
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    value = produce()
    _cache[key] = (time.time(), value)
    return value


def _get(url, params=None, headers=None):
    response = requests.get(
        url,
        params=params,
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
    )
    response.raise_for_status()
    return response


# -- biography --------------------------------------------------------

MUSIC_WORDS = (
    "band", "singer", "musician", "rapper", "songwriter", "composer",
    "guitarist", "drummer", "bassist", "vocalist", "producer", "dj",
    "duo", "trio", "quartet", "orchestra", "album", "music", "rock",
    "hip hop", "hip-hop", "pop ", "jazz", "metal", "punk", "rapper",
    "recording artist", "record label", "discography",
)


def looks_musical(description, summary):
    """Is this article about a musician at all?

    Wikipedia will happily hand back the article on princes when asked
    about Prince, and it passes a name check perfectly - the title is
    "Prince" either way. The one-line description settles it: "male
    ruler" against "American musician". The summary is checked too,
    because not every article carries a description.
    """
    haystack = (description + " " + summary[:400]).lower()
    return any(word in haystack for word in MUSIC_WORDS)


MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def format_span(raw):
    """"1979-02-15" -> "February 1979"; "1979" -> "1979".

    Spelled month and four-digit year, no day: for a band's active
    years the exact date is noise, and "February 1979" reads as a
    period where "1979-02-15" reads as a database row.
    """
    if not raw:
        return ""
    parts = str(raw).split("-")
    year = parts[0]
    if len(year) != 4 or not year.isdigit():
        return str(raw)
    if len(parts) < 2 or not parts[1].isdigit():
        return year
    month = int(parts[1])
    if not 1 <= month <= 12:
        return year
    return f"{MONTHS[month - 1]} {year}"


def split_artist_title(text):
    """Guess which half of "A - B" is the artist.

    Stream metadata is a single line with no marking of which part is
    which, and stations disagree: most write "Artist - Title", plenty
    write "Title - Artist". Rather than pick a convention and be wrong
    half the time, both readings are checked against MusicBrainz and
    the one that names a real artist wins.

    Returns (artist, title), or ("", "") when neither reading looks
    like an artist - which is the right answer for a station that puts
    its own name or a programme title in the field.
    """
    if not text:
        return "", ""

    # The separator is a dash with spaces around it. A bare hyphen is
    # left alone: it turns up inside names often enough that splitting
    # on it does more harm than good.
    for separator in (" - ", " \u2013 ", " \u2014 "):
        if separator in text:
            left, right = text.split(separator, 1)
            break
    else:
        return "", ""

    left, right = left.strip(), right.strip()
    if not left or not right:
        return "", ""

    left_score = _artist_confidence(left)
    right_score = _artist_confidence(right)
    if max(left_score, right_score) < 70:
        return "", ""
    return (left, right) if left_score >= right_score else (right, left)


def _artist_confidence(name):
    """How sure MusicBrainz is that this names an artist, 0-100."""
    def look_up():
        try:
            data = _get(
                "https://musicbrainz.org/ws/2/artist",
                params={"query": f'artist:"{name}"', "fmt": "json",
                        "limit": 1},
            ).json()
        except Exception:  # noqa: BLE001
            return 0
        artists = data.get("artists") or []
        if not artists:
            return 0
        best = artists[0]
        # An exact name match matters as much as the score: searching
        # for a song title often returns some band with a similar name
        # at a high score, and that is exactly the confusion this is
        # meant to resolve.
        exact = (best.get("name", "").strip().lower() == name.strip().lower())
        return int(best.get("score", 0)) if exact else 0

    return _cached(("artist-confidence", name.lower()), look_up)


def station_article(station_name, override=None):
    """A Wikipedia article about a station, for talk and news.

    Music stations get a biography of whoever is playing. A talk
    station has no such thing - there is no artist - so the station
    itself is the subject worth reading about.
    """
    if not station_name:
        return None

    override = override or {}
    if override.get("bio"):
        return {
            "name": override.get("name") or station_name,
            "summary": override["bio"], "tags": [], "area": "",
            "years": "", "url": override.get("url", ""),
            "photo": override.get("photo", ""),
        }

    def look_up():
        summary, page, article, photo, description = _wikipedia_summary(
            override.get("wikipedia") or station_name
        )
        if not summary:
            return None
        # Radio stations describe themselves as such; anything else with
        # this name is a coincidence.
        text = f"{description} {summary}".lower()
        if not any(word in text for word in
                   ("radio", "station", "broadcast", "fm", "am ", "network")):
            return None
        return {
            "name": article or station_name,
            "summary": summary,
            "tags": [],
            "area": "",
            "years": "",
            "url": page,
            "photo": photo,
        }

    # Keyed by the article asked for, not only the station: a Wikipedia
    # title typed into Edit this artist… must not get the old answer.
    return _cached(("station", station_name.lower(),
                    (override.get("wikipedia") or "").lower()), look_up)


# Discogs, as a last resort. Their search needs a token, but the
# database endpoints do not - and an artist page carries a profile,
# which is all we want here.
DISCOGS_AGENT = "Frequencies/1.0 +https://github.com/frequencies-radio"


def discogs_biography(artist):
    """An artist profile from Discogs, or None.

    Reached only when MusicBrainz and Wikipedia between them have
    nothing. Discogs covers a great deal that Wikipedia does not -
    session players, small labels, electronic acts with no article -
    which is exactly the gap worth filling.
    """
    if not artist:
        return None

    def look_up():
        try:
            found = _get(
                "https://api.discogs.com/database/search",
                params={"q": artist, "type": "artist", "per_page": 1},
                headers={"User-Agent": DISCOGS_AGENT},
            ).json()
        except Exception:  # noqa: BLE001
            return None

        results = found.get("results") or []
        if not results:
            return None
        best = results[0]
        if best.get("title", "").strip().lower() != artist.strip().lower():
            # Same rule as elsewhere: a near match is usually a
            # different act with a similar name.
            return None

        try:
            page = _get(
                best.get("resource_url", ""),
                headers={"User-Agent": DISCOGS_AGENT},
            ).json()
        except Exception:  # noqa: BLE001
            return None

        profile = (page.get("profile") or "").strip()
        if not profile:
            return None

        # Discogs marks up its text with [b]...[/b] and [a123] artist
        # references. Left in, they read as noise.
        profile = re.sub(r"\[/?[abilu]\]", "", profile)
        profile = re.sub(r"\[[auml]=?\d+\]", "", profile)
        profile = re.sub(r"\[url=[^\]]*\]", "", profile)
        profile = profile.replace("[/url]", "").strip()

        images = page.get("images") or []
        photo = images[0].get("uri", "") if images else ""
        return {
            "name": page.get("name") or artist,
            "summary": profile,
            "tags": [],
            "area": "",
            "years": "",
            "url": page.get("uri", ""),
            "photo": photo,
            "source": "Discogs",
        }

    return _cached(("discogs", artist.lower()), look_up)


def biography(artist, override=None):
    """{"name", "summary", "tags", "area", "years", "url"} or None.

    `override` is a correction from the shipped or user override file:
    an mbid pins the MusicBrainz artist, a wikipedia title forces one
    article, and a bio replaces the text outright for the cases where
    no article is right.
    """
    if not artist:
        return None
    override = override or {}
    # The override is part of the cache key: changing a correction has
    # to take effect without waiting for the cache to expire.
    key = ("bio", artist.lower(), repr(sorted(override.items())))
    return _cached(key, lambda: _biography(artist, override))


def _biography(artist, override=None):
    """MusicBrainz first, Wikipedia alone if it draws a blank.

    MusicBrainz is authoritative about who an artist *is* - area, active
    years, genre tags - but its coverage thins fast outside the
    well-catalogd, and a lot of indie artists simply aren't in it or
    don't clear the confidence threshold. Wikipedia often has a page
    regardless, so a summary with no structured data beats nothing.
    """
    global last_bio_error
    last_bio_error = ""
    problems = []
    override = override or {}

    # A hand-written biography short-circuits everything. This is the
    # last resort for artists no article describes correctly.
    if override.get("bio"):
        return {
            "name": override.get("name") or artist,
            "summary": override["bio"],
            "tags": override.get("tags") or [],
            "area": override.get("area", ""),
            "years": override.get("years", ""),
            "url": override.get("url", ""),
            "photo": override.get("photo", ""),
        }

    # A forced article skips the search entirely.
    forced = override.get("wikipedia")
    if forced:
        summary, page, article, photo, _description = _wikipedia_summary(forced)
        if summary:
            return {
                "name": override.get("name") or article or artist,
                "summary": summary,
                "tags": override.get("tags") or [],
                "area": override.get("area", ""),
                "years": override.get("years", ""),
                "url": page,
                "photo": override.get("photo") or photo,
            }

    try:
        found = _musicbrainz_biography(artist, mbid=override.get("mbid", ""))
    except Exception as exc:  # noqa: BLE001
        # MusicBrainz being unreachable must not stop Wikipedia being
        # tried - which is what used to happen, and why a band as
        # well-known as this one could come back with nothing.
        problems.append("musicbrainz: %s" % type(exc).__name__)
        found = None
    if found:
        return found

    # Disambiguated titles are tried FIRST.
    #
    # A bare name is often a common word with an article of its own -
    # Prince, Muse, Bush, Cream - and Wikipedia answers with that
    # article quite happily. It even passes a name check, since the
    # title really is "Prince". Asking for "Prince (musician)" first
    # avoids the problem; the bare name is the fallback, and is only
    # accepted when the article is recognisably about music.
    summary = page = article = photo = ""
    for candidate in (f"{artist} (band)", f"{artist} (musician)",
                      f"{artist} (singer)", f"{artist} (rapper)", artist):
        try:
            found = _wikipedia_summary(candidate)
        except Exception as exc:  # noqa: BLE001
            problems.append("wikipedia: %s" % type(exc).__name__)
            continue
        got_summary, got_page, got_article, got_photo, got_desc = found
        if not got_summary or not looks_musical(got_desc, got_summary):
            continue                      # right name, wrong subject
        summary, page, article = got_summary, got_page, got_article
        photo = got_photo
        break

    if not summary:
        # Discogs, before giving up entirely.
        #
        # MusicBrainz knows who someone is but often has nothing to
        # read; Wikipedia has an article or it does not. Discogs sits
        # between them - a profile for a great many acts that never got
        # an encyclopaedia entry.
        try:
            fallback = discogs_biography(artist)
        except Exception as exc:  # noqa: BLE001
            problems.append("discogs: %s" % type(exc).__name__)
            fallback = None
        if fallback:
            return fallback

        last_bio_error = "; ".join(problems)
        return None

    # Show the article's own title, not what we searched for - but only
    # when it is recognisably the same act. Wikipedia redirects freely,
    # and "Sting" leading to "Sting (musician)" is fine while "Sting"
    # leading to "Sting (wrestler)" is not something to display as the
    # band name.
    display = artist
    if article:
        bare = article.split("(")[0].strip()
        if bare.lower() == artist.lower():
            display = bare

    return {
        "name": display,
        "area": "",
        "tags": [],
        "years": "",
        "summary": summary,
        "url": page,
        "photo": photo,
        "source": "Wikipedia",
    }


def _musicbrainz_article(mbid):
    """The Wikipedia article MusicBrainz links to for this artist.

    This is the reliable route. Searching Wikipedia by name lands on
    whatever article happens to own that title - a novel, a river, a
    Roman emperor - whereas MusicBrainz records which article is about
    the musician, so following its link cannot pick the wrong one.
    """
    try:
        data = _get(
            "https://musicbrainz.org/ws/2/artist/%s" % mbid,
            params={"fmt": "json", "inc": "url-rels"},
        ).json()
    except Exception:
        return ""

    for relation in data.get("relations") or []:
        target = (relation.get("url") or {}).get("resource", "")
        if relation.get("type") == "wikipedia" and "wikipedia.org" in target:
            return urllib.parse.unquote(target.rsplit("/", 1)[-1])
    # Wikidata as a second hop: most artists have that link even when
    # the direct Wikipedia one is missing.
    for relation in data.get("relations") or []:
        target = (relation.get("url") or {}).get("resource", "")
        if relation.get("type") == "wikidata" and "wikidata.org" in target:
            return _wikidata_article(target.rsplit("/", 1)[-1])
    return ""


def _wikidata_article(entity):
    """The English Wikipedia title for a Wikidata entity."""
    try:
        data = _get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbgetentities", "ids": entity,
                    "props": "sitelinks", "format": "json"},
        ).json()
    except Exception:
        return ""
    sitelinks = (((data.get("entities") or {}).get(entity) or {})
                 .get("sitelinks") or {})
    return (sitelinks.get("enwiki") or {}).get("title", "")


def _musicbrainz_biography(artist, mbid=""):
    try:
        if mbid:
            # An override pinned this to one specific artist.
            data = {"artists": [_get(
                "https://musicbrainz.org/ws/2/artist/%s" % mbid,
                params={"fmt": "json"},
            ).json()]}
            data["artists"][0].setdefault("score", 100)
        else:
            data = _get(
                "https://musicbrainz.org/ws/2/artist",
                params={"query": artist, "fmt": "json", "limit": 1},
            ).json()
    except Exception:
        return None

    matches = data.get("artists") or []
    if not matches:
        return None
    match = matches[0]

    # A weak match is worse than none: showing the wrong artist's
    # biography is actively misleading, where showing nothing is merely
    # unhelpful.
    if int(match.get("score", 0)) < 85:
        return None

    info = {
        "name": match.get("name") or artist,
        "area": (match.get("area") or {}).get("name", ""),
        "tags": [t["name"] for t in (match.get("tags") or [])[:6]],
        "years": "",
        "summary": "",
        "url": "",
    }
    span = match.get("life-span") or {}
    begin = format_span(span.get("begin"))
    end = format_span(span.get("end"))
    if begin:
        info["years"] = f"{begin} - {end}" if end else f"{begin} - present"

    # Five values, not two.
    #
    # This unpacked the old two-value shape after the signature grew,
    # so every MusicBrainz lookup raised ValueError and fell through as
    # "no biography found" - which is why artists as well known as
    # these were coming back empty.
    # Follow MusicBrainz's own link to the article, and only fall back
    # to searching by name when it has none.
    title = _musicbrainz_article(match.get("id", "")) or info["name"]
    summary, page, _article, photo, description = _wikipedia_summary(title)
    # The existing check, which weighs the summary as well as the
    # description - some articles carry no description at all.
    if not looks_musical(description, summary):
        summary, page, photo = "", "", ""
    info["summary"] = summary
    info["url"] = page
    info["photo"] = photo
    return info


def _wikipedia_summary(name):
    """(summary, page_url, article_title).

    The article title comes back too, because it is what should be
    shown. Looking up "Muse (band)" and then displaying that as the
    artist name puts our search syntax on screen; Wikipedia's own title
    for the page is "Muse", which is what the band is actually called.
    """
    try:
        title = urllib.parse.quote(name.replace(" ", "_"))
        data = _get(
            "https://en.wikipedia.org/api/rest_v1/page/summary/%s" % title
        ).json()
    except Exception:
        return "", "", "", "", ""

    # Disambiguation pages are not biographies.
    if data.get("type") == "disambiguation":
        return "", "", "", "", ""
    extract = (data.get("extract") or "").strip()
    page = ((data.get("content_urls") or {}).get("desktop") or {}).get("page", "")
    article = (data.get("title") or "").strip()
    # The article's lead image. Wikipedia hands back both a thumbnail
    # and the original; the thumbnail is the right size for a panel and
    # avoids pulling a multi-megabyte press photo over a phone tether.
    photo = ((data.get("thumbnail") or {}).get("source")
             or (data.get("originalimage") or {}).get("source") or "")
    # Wikipedia's own one-line description - "American musician
    # (1958-2016)" - which is how we tell a singer from a job title.
    description = (data.get("description") or "").strip()
    return extract, page, article, photo, description


# Why the last biography lookup found nothing, when it was a failure
# rather than a genuine absence. A panel that cannot tell the two apart
# reports a network problem as "this band does not exist".
last_bio_error = ""


