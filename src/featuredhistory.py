"""Frequencies — previously featured frequencies (build 151, task 211).

featured.json lives in a public repository (radiobrowser.FEATURED_URL), so
every list it ever held is in that repository's history. `refresh()` asks
GitHub's commits API (no token; once a day at most, one call against the
60-an-hour limit) which versions of the file there are, and reads each
version it hasn't read yet from raw.githubusercontent.com (not counted
against that limit). A version is read once, ever: what it held is folded
into one file, <config>/featured/history.json, and the commit is marked
done.

Every list this install is shown (radiobrowser.featured_stations) is
recorded too (`note_seen`), so the window still has something to show
offline, rate-limited, or if the repository's history is ever rewritten.

A station is one entry, by its stream address: its newest settings, the
first day it was featured and the day it stopped being (the date of the
version that no longer had it; for one only seen here, the last day this
install saw it). Nothing here runs on the GUI thread except `previous()`
and `when_text()`, which only read.
"""

import datetime
import json
import os
import re
import threading
import time

HISTORY_FILE = "history.json"
REFRESH_EVERY = 24 * 60 * 60        # seconds between commit listings
RETRY_AFTER = 60 * 60               # after a failure (offline, rate limit)
MAX_PAGES = 5                       # 500 versions is plenty
AGENT = "Frequencies (radio app)"
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

_lock = threading.Lock()


def _log(text):
    try:
        import crashlog
        crashlog.write("[featured] " + text)
    except Exception:                           # noqa: BLE001
        pass


def source():
    """(owner, repo, branch, path) of featured.json, from FEATURED_URL."""
    import radiobrowser
    found = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/"
                     r"([^/]+)/(.+)$", radiobrowser.FEATURED_URL)
    if not found:
        return None
    return found.groups()


def key_of(item):
    """One station, whatever its name says: its stream address."""
    url = str((item or {}).get("url") or "").strip().lower()
    return url.rstrip("/")


def _path(folder):
    return os.path.join(folder, HISTORY_FILE)


def _empty():
    return {"commits": [], "last_keys": [], "checked_at": 0,
            "failed_at": 0, "stations": {}}


def _read(folder):
    try:
        with open(_path(folder), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("stations"),
                                                    dict):
        return _empty()
    for key, value in _empty().items():
        data.setdefault(key, value)
    return data


def _write(folder, data):
    try:
        os.makedirs(folder, exist_ok=True)
        tmp = _path(folder) + f".{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, _path(folder))
    except OSError as exc:
        _log(f"couldn't save the history ({exc})")


def _items(payload):
    stations = payload if isinstance(payload, list) else (
        payload.get("stations") if isinstance(payload, dict) else None)
    if not isinstance(stations, list):
        return []
    return [s for s in stations if isinstance(s, dict) and key_of(s)]


# -- what this install has seen -----------------------------------------------

def note_seen(folder, stations, day=None):
    """Record today's featured list (raw featured.json entries). Written
    only when something changed, so a cached list costs nothing."""
    if not folder:
        return
    day = day or datetime.date.today().isoformat()
    with _lock:
        data = _read(folder)
        changed = False
        for item in _items(stations):
            key = key_of(item)
            entry = data["stations"].get(key)
            if entry is None:
                entry = data["stations"][key] = {"item": item}
                changed = True
            if entry.get("seen_from", "9999") > day:
                entry["seen_from"] = day
                changed = True
            if entry.get("seen_to", "") < day:
                entry["seen_to"] = day
                entry["item"] = item            # the newest settings
                changed = True
        if changed:
            _write(folder, data)


# -- the repository's history ------------------------------------------------

def _get(url, timeout=15):
    import requests
    return requests.get(url, timeout=timeout,
                        headers={"User-Agent": AGENT,
                                 "Accept": "application/vnd.github+json"})


def _commits(owner, repo, branch, path):
    """Every version of the file, oldest first: [(sha, iso date)]."""
    out = []
    for page in range(1, MAX_PAGES + 1):
        answer = _get(f"{API}/repos/{owner}/{repo}/commits?path={path}"
                      f"&sha={branch}&per_page=100&page={page}")
        if answer.status_code in (403, 429):
            left = answer.headers.get("X-RateLimit-Remaining", "?")
            raise RuntimeError(f"GitHub's limit for now ({answer.status_code}"
                               f", {left} left)")
        answer.raise_for_status()
        batch = answer.json()
        if not isinstance(batch, list):
            break
        for commit in batch:
            info = (commit.get("commit") or {})
            when = ((info.get("committer") or {}).get("date")
                    or (info.get("author") or {}).get("date") or "")
            if commit.get("sha"):
                out.append((commit["sha"], when[:10]))
        if len(batch) < 100:
            break
    out.reverse()
    return out


def refresh(folder, force=False):
    """Read any versions of featured.json not read yet. Network: never on
    the GUI thread. Returns "" when the history is current, else why not
    (shown as a quiet line in the window)."""
    where = source()
    if not folder or where is None:
        return "no featured list address"
    owner, repo, branch, path = where
    now = time.time()
    with _lock:
        data = _read(folder)
    if not force and now - data.get("checked_at", 0) < REFRESH_EVERY:
        return ""
    if not force and now - data.get("failed_at", 0) < RETRY_AFTER:
        return "GitHub couldn't be reached a little while ago"
    try:
        import offline
        if offline.is_offline():
            return "offline"
    except Exception:                           # noqa: BLE001
        pass
    try:
        versions = _commits(owner, repo, branch, path)
    except Exception as exc:                    # noqa: BLE001
        with _lock:
            data = _read(folder)
            data["failed_at"] = now
            _write(folder, data)
        why = str(exc) if isinstance(exc, RuntimeError) \
            else "couldn't reach GitHub"
        _log(f"previously featured: {why}")
        return why
    done = set(data.get("commits") or [])
    new = [(sha, day) for sha, day in versions if sha not in done]
    read = 0
    for sha, day in new:
        try:
            answer = _get(f"{RAW}/{owner}/{repo}/{sha}/{path}", timeout=30)
            answer.raise_for_status()
            payload = answer.json()
        except ValueError:
            payload = None                      # a broken upload: skip it
        except Exception as exc:                # noqa: BLE001
            _log(f"previously featured: version {sha[:7]} not read ({exc})")
            with _lock:
                data = _read(folder)
                data["failed_at"] = now
                _write(folder, data)
            return "couldn't read every older list"
        with _lock:
            data = _read(folder)
            if sha in data["commits"]:
                continue
            if payload is not None:
                _fold(data, _items(payload), day)
            data["commits"].append(sha)
            _write(folder, data)
        read += 1
    with _lock:
        data = _read(folder)
        data["checked_at"] = now
        data["failed_at"] = 0
        _write(folder, data)
    if read:
        _log(f"previously featured: read {read} older list(s), "
             f"{len(data['stations'])} frequencies known")
    return ""


def _fold(data, items, day):
    """One version of the list, in commit order, into the history."""
    keys = []
    for item in items:
        key = key_of(item)
        keys.append(key)
        entry = data["stations"].setdefault(key, {"item": item})
        if entry.get("from", "9999") > day:
            entry["from"] = day
        if day >= entry.get("version_day", ""):
            entry["item"] = item                # the newest settings
            entry["version_day"] = day
        entry["to"] = ""                        # featured as of this one
    for key in data.get("last_keys") or []:
        if key not in keys and key in data["stations"]:
            data["stations"][key]["to"] = day   # replaced by this version
    data["last_keys"] = keys


# -- what the window shows ----------------------------------------------------

def previous(folder, current=()):
    """[(raw item, first day, last day)] of every frequency featured
    before and not featured now, newest first. `current` is today's raw
    featured list (or its stations' addresses)."""
    with _lock:
        data = _read(folder)
    now = {key_of(c) if isinstance(c, dict) else str(c).lower().rstrip("/")
           for c in current or ()}
    out = []
    for key, entry in data["stations"].items():
        if key in now or not isinstance(entry.get("item"), dict):
            continue
        first = min(d for d in (entry.get("from"), entry.get("seen_from"),
                                "9999") if d)
        last = entry.get("to") or entry.get("seen_to") or ""
        if first == "9999":
            first = last
        out.append((entry["item"], first, last))
    out.sort(key=lambda e: (e[2] or e[1], e[1]), reverse=True)
    return out


def _day(text):
    try:
        return datetime.date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def when_text(first, last, today=None):
    """"Sep 3 – Oct 18" for a tile (the year only when it isn't this
    year's), "Sep 3, 2025 – Jan 4, 2026" across years."""
    a, b = _day(first), _day(last)
    year = (today or datetime.date.today()).year

    def short(d, with_year):
        text = f"{d.strftime('%b')} {d.day}"
        return f"{text}, {d.year}" if with_year else text
    if a and b and a != b:
        across = a.year != b.year
        return (f"{short(a, across)} – "
                f"{short(b, across or b.year != year)}")
    one = a or b
    return short(one, one.year != year) if one else "Featured before"
