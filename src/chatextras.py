"""Frequencies — Twitch chat extras: emotes, BetterTTV, pronouns (task 92).

Build 81 adds three things to the read-only chat of build 77:

  * Twitch's own emotes. Every chat line carries an `emotes` tag that
    says which emote sits where in the text (`25:0-4,12-16/1902:6-10` =
    emote 25 at characters 0–4 and 12–16, emote 1902 at 6–10). The
    picture comes from Twitch's emote server.
  * BetterTTV emotes. BetterTTV keeps a list of emotes for everyone
    (global) and one per channel; a word in a message that matches a
    code on those lists ("catJAM") is shown as that emote. The channel's
    list is asked for by the channel's number (`room-id`), which the chat
    server sends when we join.
  * Pronouns. pr.alejo.io (the "Twitch Chat Pronouns" extension) is asked
    for each person who writes, by their login; PronounDB is asked for
    the ones alejo has nothing for, by their Twitch number. The answer
    ("He/Him") is shown in a small grey tag before the name.

All of it goes over the network on background Jobs, never on the GUI
thread, and is kept for the rest of the run (emote pictures also on
disk, in ~/.cache/frequencies/emotes, for 30 days).

Animated emotes are decoded once, every frame, on the download thread
(Qt's image readers must not run on two threads at once — imagelock),
and played back by one timer for every chat window, all in step.

The sandbox this was written in can't reach Twitch, BetterTTV,
pr.alejo.io or PronounDB: the addresses and the shapes of the answers
are from their public docs and from memory, tested only against fake
servers. The `[chat]` lines in the log say what each one answered.

Build 85 (task 95) adds:

  * Chat badges (broadcaster, moderator, subscriber…) in front of names.
    Every chat line carries a `badges` tag ("moderator/1,subscriber/12");
    the pictures and titles come from Twitch's API with your chat
    sign-in (`/helix/chat/badges/global` and `?broadcaster_id=`), or,
    when you are not signed in, from the API Twitch's own website uses
    (gql.twitch.tv, a guess at its shape). Log: `[chat] badges: …`.
  * Your own Twitch emotes, for the emote picker and the ":" list: the
    chat server names your emote sets (`emote-sets`), Twitch's API turns
    them into emotes (`/helix/chat/emotes/set`, no extra permission).
    Log: `[chat] your emotes: …`.
"""

import hashlib
import os
import queue
import re
import threading
import time
import weakref

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from jobs import Job

EMOTE_PX = 28                   # an emote's height in the chat, like Twitch's
BADGE_PX = 18                   # a badge's height, like Twitch's
USER_AGENT = "Frequencies/1.5 (personal internet radio player)"

TWITCH_EMOTE_URL = ("https://static-cdn.jtvnw.net/emoticons/v2/{id}/"
                    "default/dark/2.0")
BTTV_EMOTE_URL = "https://cdn.betterttv.net/emote/{id}/2x"
BTTV_GLOBAL_URL = "https://api.betterttv.net/3/cached/emotes/global"
BTTV_CHANNEL_URL = "https://api.betterttv.net/3/cached/users/twitch/{id}"
ALEJO_PRONOUNS_URL = "https://api.pronouns.alejo.io/v1/pronouns"
ALEJO_USER_URL = "https://api.pronouns.alejo.io/v1/users/{login}"
PRONOUNDB_URL = "https://pronoundb.org/api/v2/lookup"
HELIX_URL = "https://api.twitch.tv/helix"

CACHE_DAYS = 30
MAX_FRAMES = 240                # an animated emote keeps at most this many
PRONOUN_KEEP_S = 3600           # an answer is good for an hour

# Settings keys (all on unless switched off in the chat's right-click menu).
OPTIONS = {"chat_emotes": True, "chat_animated": True,
           "chat_pronouns": True, "chat_badges": True}

# Chat badges (task 95): "set/version" → (picture address, title).
BADGES = {}                     # Twitch's global badges (kept for the run)
# A channel's own badges (subscriber, bits…) are in its `_Chan` (task 107:
# the side panel and the pop-out can be on different channels).


def badge_list(tag):
    """'moderator/1,subscriber/12' → ['moderator/1', 'subscriber/12']."""
    return [b for b in (tag or "").split(",") if "/" in b]


def badge_info(badge, channel=""):
    """(picture address, title) for "set/version", or None (not known
    yet, or Twitch has no picture for it). `channel`: whose own badges
    come first (without one, any channel's)."""
    chans = shared()._chans
    for c in ([chans[channel.lower()]] if channel.lower() in chans
              else list(chans.values())):
        if badge in c.badges:
            return c.badges[badge]
    return BADGES.get(badge)


def option(name):
    return bool(OPTIONS.get(name, True))


def _log(text):
    try:
        import crashlog
        crashlog.write("[chat] " + text)
    except Exception:                           # noqa: BLE001
        pass


# -- splitting a message into text and emotes -------------------------------

_logged_utf16 = False


def _utf16_to_index(text):
    """Map UTF-16 positions to Python (code point) positions."""
    table, pos = {}, 0
    for i, ch in enumerate(text):
        table[pos] = i
        pos += 2 if ord(ch) > 0xFFFF else 1
    table[pos] = len(text)
    return table


def _fits(text, start, end):
    """A real emote range: inside the text, a word with no spaces."""
    if start < 0 or end < start or end >= len(text):
        return False
    word = text[start:end + 1]
    return bool(word) and not any(c.isspace() for c in word)


def twitch_ranges(tag, text):
    """[(start, end, emote_id)] from a message's `emotes` tag, sorted.

    Twitch counts positions in characters (code points), which is what
    Python counts too. Some programs say it counts UTF-16 units instead
    (an emoji before an emote would then shift it); a range that doesn't
    land on a word is tried that way before it is dropped. A guess until
    a log from the user's PC says otherwise.
    """
    global _logged_utf16
    out = []
    if not tag:
        return out
    utf16 = None
    for part in tag.split("/"):
        emote_id, _, places = part.partition(":")
        if not emote_id or not places:
            continue
        for place in places.split(","):
            a, _, b = place.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                continue
            if not _fits(text, start, end):
                if utf16 is None:
                    utf16 = _utf16_to_index(text)
                s2, e2 = utf16.get(start), utf16.get(end + 1)
                if s2 is None or e2 is None or not _fits(text, s2, e2 - 1):
                    continue
                if not _logged_utf16:
                    _logged_utf16 = True
                    _log("emotes: Twitch counted an emote's place in UTF-16 "
                         "units, not characters (an emoji came before it); "
                         "placed it that way")
                start, end = s2, e2 - 1
            out.append((start, end, emote_id))
    out.sort()
    # Overlaps would mean a broken tag: keep the first of any pair.
    kept, last = [], -1
    for start, end, emote_id in out:
        if start > last:
            kept.append((start, end, emote_id))
            last = end
    return kept


# Twitch emotes seen in chat, name → id (build 82): your own messages
# come with no emote tag, so their emotes are found by name.
LEARNED = {}


def split_message(text, emotes_tag="", bttv=None, twitch=True,
                  known_twitch=None):
    """A message as parts: ("text", s) or ("emote", key, code).

    `key` is "tw:<id>" for a Twitch emote, "bttv:<id>" for BetterTTV.
    Twitch's own emotes come from the tag; BetterTTV's are whole words
    (split on spaces) found in `bttv` (code → id) in the text between.
    `known_twitch` (code → id) is matched by word too, for messages with
    no tag (your own, build 82).
    """
    parts = []
    pieces = []                                 # text between Twitch emotes
    pos = 0
    for start, end, emote_id in (twitch_ranges(emotes_tag, text)
                                 if twitch else []):
        if start > pos:
            pieces.append(text[pos:start])
        code = text[start:end + 1]
        pieces.append(("tw:" + emote_id, code))
        if len(LEARNED) < 5000:
            LEARNED[code] = emote_id
        pos = end + 1
    if pos < len(text):
        pieces.append(text[pos:])
    for piece in pieces:
        if isinstance(piece, tuple):
            parts.append(("emote",) + piece)
            continue
        if not bttv and not known_twitch:
            parts.append(("text", piece))
            continue
        run = []
        for word in re.split(r"(\s+)", piece):
            key = None
            if word and not word.isspace():
                if known_twitch and word in known_twitch:
                    key = "tw:" + known_twitch[word]
                elif bttv and word in bttv:
                    key = "bttv:" + bttv[word]
            if key:
                if run:
                    parts.append(("text", "".join(run)))
                    run = []
                parts.append(("emote", key, word))
            else:
                run.append(word)
        if run:
            parts.append(("text", "".join(run)))
    return [p for p in parts if p[0] == "emote" or p[1]]


def emote_url(key):
    kind, _, emote_id = key.partition(":")
    if kind == "tw":
        return TWITCH_EMOTE_URL.format(id=emote_id)
    if kind == "bttv":
        return BTTV_EMOTE_URL.format(id=emote_id)
    if kind == "bdg":
        got = badge_info(emote_id)
        return got[0] if got else ""
    return ""


def picture_px(key):
    """How tall a picture is drawn: badges are smaller than emotes."""
    return BADGE_PX if key.startswith("bdg:") else EMOTE_PX


# -- the network ----------------------------------------------------------------

def _session():
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _cache_dir():
    import appdirs
    base = appdirs.cache_base()
    path = os.path.join(base, "frequencies", "emotes")
    os.makedirs(path, exist_ok=True)
    return path


def _prune_cache():
    """Drop emote pictures not used for CACHE_DAYS."""
    try:
        folder = _cache_dir()
        cutoff = time.time() - CACHE_DAYS * 86400
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def decode_frames(data, height_px):
    """Every frame of a picture, scaled to `height_px` tall.

    Returns ([QImage], [delay ms]); one frame for a still picture.
    Background threads only, under imagelock (QImageReader.read is
    guarded by imagelock.install; the lock is re-entrant).
    """
    import imagelock
    from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PyQt5.QtGui import QImageReader
    frames, delays = [], []
    with imagelock.held("emote"):
        buf = QBuffer()
        buf.setData(QByteArray(data))
        buf.open(QIODevice.ReadOnly)
        reader = QImageReader(buf)
        reader.setDecideFormatFromContent(True)
        while len(frames) < MAX_FRAMES:
            img = reader.read()
            if img.isNull():
                break
            delay = reader.nextImageDelay()
            if img.height() != height_px:
                img = img.scaledToHeight(height_px, Qt.SmoothTransformation)
            frames.append(img)
            delays.append(max(20, delay or 100))
            if not reader.supportsAnimation() or reader.imageCount() == 1:
                break
            if not reader.canRead():
                break
        buf.close()
    return frames, delays


class _EmoteFetcher(Job):
    """Downloads and decodes emote pictures, one at a time, for the run."""

    LOOPS = True

    got = pyqtSignal(str, list, list)       # key, [QImage], [delay ms]
    failed = pyqtSignal(str, str)           # key, why

    def __init__(self, height_px):
        super().__init__()
        self.height_px = height_px
        self.todo = queue.Queue()
        self.cancel = threading.Event()

    def stop(self):
        self.cancel.set()

    def work(self):
        _prune_cache()
        http = None
        while not self.cancel.is_set():
            try:
                key, url, height_px = self.todo.get(timeout=0.5)
            except queue.Empty:
                continue
            data, why = None, ""
            path = os.path.join(_cache_dir(), hashlib.sha1(
                url.encode("utf-8")).hexdigest())
            try:
                if os.path.exists(path):
                    with open(path, "rb") as handle:
                        data = handle.read()
                    os.utime(path)
            except OSError:
                data = None
            if not data:
                try:
                    http = http or _session()
                    reply = http.get(url, timeout=10)
                    if reply.status_code == 200 and reply.content:
                        data = reply.content
                        try:
                            with open(path + ".part", "wb") as handle:
                                handle.write(data)
                            os.replace(path + ".part", path)
                        except OSError:
                            pass
                    else:
                        why = f"HTTP {reply.status_code}"
                except Exception as exc:        # noqa: BLE001
                    why = f"{type(exc).__name__}: {str(exc)[:80]}"
            if self.cancel.is_set():
                break
            if not data:
                self.send(self.failed, key, why or "no picture")
                continue
            try:
                frames, delays = decode_frames(data,
                                               height_px or self.height_px)
            except Exception as exc:            # noqa: BLE001
                frames, delays = [], []
                why = f"{type(exc).__name__}: {str(exc)[:80]}"
            if frames:
                self.send(self.got, key, frames, delays)
            else:
                self.send(self.failed, key,
                          why or f"could not read the picture "
                                 f"({len(data)} bytes)")


class _PronounFetcher(Job):
    """Asks pr.alejo.io (then PronounDB) for people's pronouns."""

    LOOPS = True

    found = pyqtSignal(dict)                # login → label ("" = none)
    table = pyqtSignal(int, str)            # kinds known, where from

    def __init__(self):
        super().__init__()
        self.todo = queue.Queue()
        self.cancel = threading.Event()
        self.kinds = dict(ALEJO_FALLBACK)
        self.stats = {"asked": 0, "alejo": 0, "pronoundb": 0, "failed": 0}
        self.first_fail = ""

    def stop(self):
        self.cancel.set()

    def work(self):
        http = _session()
        self._load_table(http)
        waiting = {}                        # user id → login, for PronounDB
        last_batch = time.monotonic()
        while not self.cancel.is_set():
            try:
                login, user_id = self.todo.get(timeout=0.4)
            except queue.Empty:
                login = None
            if login:
                label = self._alejo(http, login)
                if label is None and user_id:
                    waiting[user_id] = login
                elif label is not None:
                    self.send(self.found, {login: label})
                else:
                    self.send(self.found, {login: ""})
            if waiting and (len(waiting) >= 50 or login is None
                            or time.monotonic() - last_batch > 2):
                batch, waiting = waiting, {}
                last_batch = time.monotonic()
                self.send(self.found, self._pronoundb(http, batch))

    def _fail(self, text):
        self.stats["failed"] += 1
        if not self.first_fail:
            self.first_fail = text
            _log(f"pronouns: {text} (first failure; more are only counted)")

    def _load_table(self, http):
        try:
            reply = http.get(ALEJO_PRONOUNS_URL, timeout=8)
            data = reply.json() if reply.status_code == 200 else None
            kinds = {}
            if isinstance(data, dict):
                rows = data.values()
            elif isinstance(data, list):        # the old API's shape
                rows = data
            else:
                rows = []
            for row in rows:
                if isinstance(row, dict) and row.get("name"):
                    kinds[row["name"]] = (row.get("subject") or "",
                                          row.get("object") or "",
                                          bool(row.get("singular")))
            if kinds:
                self.kinds = kinds
                _log(f"pronouns: pr.alejo.io's list has {len(kinds)} kinds")
                self.send(self.table, len(kinds), "pr.alejo.io")
                return
            _log(f"pronouns: pr.alejo.io's list came back empty "
                 f"(HTTP {reply.status_code}); using the built-in one")
        except Exception as exc:                # noqa: BLE001
            _log(f"pronouns: could not read pr.alejo.io's list "
                 f"({type(exc).__name__}: {str(exc)[:80]}); using the "
                 f"built-in one")
        self.send(self.table, len(self.kinds), "built in")

    def _label(self, main, alt=None):
        a = self.kinds.get(main)
        if not a:
            return ""
        b = self.kinds.get(alt) if alt else None
        if b:
            return f"{a[0]}/{b[0]}"
        if a[2] or not a[1]:
            return a[0]
        return f"{a[0]}/{a[1]}"

    def _alejo(self, http, login):
        """The label, "" for nobody set, None if alejo has none / failed."""
        self.stats["asked"] += 1
        try:
            reply = http.get(ALEJO_USER_URL.format(login=login), timeout=8)
        except Exception as exc:                # noqa: BLE001
            self._fail(f"pr.alejo.io: {type(exc).__name__}: {str(exc)[:80]}")
            return None
        if reply.status_code == 404:
            return None
        if reply.status_code != 200:
            self._fail(f"pr.alejo.io answered HTTP {reply.status_code}")
            return None
        try:
            data = reply.json()
        except ValueError:
            self._fail("pr.alejo.io's answer wasn't JSON")
            return None
        if isinstance(data, list):              # the old API: [{pronoun_id}]
            data = data[0] if data else {}
        if not isinstance(data, dict):
            return None
        label = self._label(data.get("pronoun_id") or "",
                            data.get("alt_pronoun_id"))
        if label:
            self.stats["alejo"] += 1
            if self.stats["alejo"] <= 3:
                _log(f"pronouns: '{login}' → {label} (pr.alejo.io)")
            return label
        return None

    def _pronoundb(self, http, batch):
        """{login: label} for a batch of {user id: login}."""
        out = {login: "" for login in batch.values()}
        try:
            # Commas as they are: "%2C" may not be read as a list.
            reply = http.get(PRONOUNDB_URL, timeout=8,
                             params="platform=twitch&ids=" + ",".join(batch))
            if reply.status_code != 200:
                self._fail(f"PronounDB answered HTTP {reply.status_code}")
                return out
            data = reply.json()
        except Exception as exc:                # noqa: BLE001
            self._fail(f"PronounDB: {type(exc).__name__}: {str(exc)[:80]}")
            return out
        if not isinstance(data, dict):
            return out
        for user_id, row in data.items():
            login = batch.get(str(user_id))
            sets = ((row or {}).get("sets") or {}).get("en") or []
            label = pronoundb_label(sets)
            if login and label:
                out[login] = label
                self.stats["pronoundb"] += 1
                if self.stats["pronoundb"] <= 3:
                    _log(f"pronouns: '{login}' → {label} (PronounDB)")
        return out


# pr.alejo.io's kinds, in case its list can't be read: name → (subject,
# object, singular).
ALEJO_FALLBACK = {
    "aeaer": ("Ae", "Aer", False), "any": ("Any", "", True),
    "eem": ("E", "Em", False), "faefaer": ("Fae", "Faer", False),
    "hehim": ("He", "Him", False), "heshe": ("He", "She", False),
    "hethem": ("He", "They", False), "itits": ("It", "Its", False),
    "other": ("Other", "", True), "perper": ("Per", "Per", False),
    "sheher": ("She", "Her", False), "shethem": ("She", "They", False),
    "theythem": ("They", "Them", False), "vever": ("Ve", "Ver", False),
    "xexem": ("Xe", "Xem", False), "ziehir": ("Zie", "Hir", False),
}

_PDB = {"he": ("He", "Him"), "she": ("She", "Her"), "it": ("It", "Its"),
        "they": ("They", "Them"), "any": ("Any", ""), "ask": ("Ask me", ""),
        "avoid": ("Use name", ""), "other": ("Other", "")}


def pronoundb_label(sets):
    """PronounDB's ["he", "they"] → "He/They"; ["she"] → "She/Her"."""
    sets = [s for s in sets or [] if s in _PDB]
    if not sets:
        return ""
    if len(sets) == 1:
        subject, obj = _PDB[sets[0]]
        return f"{subject}/{obj}" if obj else subject
    return "/".join(_PDB[s][0] for s in sets[:2])


# -- what the chat windows use (GUI thread) ------------------------------------

class _Chan:
    """What is known about one channel (task 107: the side panel's chat
    and the pop-out's can be on different channels at once)."""

    def __init__(self, channel=""):
        self.channel = channel
        self.room_id = ""                   # its Twitch number
        self.bttv = {}                      # its own BetterTTV emotes
        self.bttv_room = ""
        self.badges = {}                    # its own badges
        self.badge_room = None
        self.mine = []                      # your Twitch emotes, ranked here
        self.mine_keys = {}                 # key → its row
        self.mine_asked = None
        self.rules = []                     # its chat rules
        self.rules_asked = False


_NOBODY = _Chan()


class ChatExtras(QObject):
    """Emote pictures, BetterTTV's lists and pronouns, for every chat view.

    `pixmap(key)` and `pronoun(login)` answer at once
    from what has arrived (and ask for the rest); `emotes_arrived`,
    `pronouns_arrived` and `bttv_changed` say when there is more.
    """

    emotes_arrived = pyqtSignal(list)       # keys
    pronouns_arrived = pyqtSignal(list)     # logins
    bttv_changed = pyqtSignal()
    badges_changed = pyqtSignal()           # task 95
    rules_changed = pyqtSignal(str)         # task 95: channel
    mine_changed = pyqtSignal()             # task 95: your Twitch emotes
    pins_changed = pyqtSignal()             # task 106: the pinned row

    def __init__(self):
        super().__init__()
        self._frames = {}                   # key → ([QPixmap], [ms], total)
        self._asked = set()
        self._failed = {}                   # key → why
        self._fetcher = None
        self._pron = {}                     # login → (label, when)
        self._pron_asked = set()
        self._pron_fetcher = None
        self.bttv_global = {}
        self._bttv_global_asked = False
        self._chans = {}                    # channel → _Chan
        self.pins = []                      # task 106: [{"key", "code"}]
        self.rules_ok = set()               # channels whose rules you OK'd
        self._arrived, self._pron_new = [], []
        self._flush = QTimer(self)
        self._flush.setSingleShot(True)
        self._flush.setInterval(120)
        self._flush.timeout.connect(self._send_arrived)
        self._views = weakref.WeakSet()
        self._anim = QTimer(self)
        self._anim.setInterval(40)
        self._anim.timeout.connect(self._tick)
        self._started = time.monotonic()
        self.stats = {"emotes": 0, "failed": 0, "animated": 0}
        self._dpr = None

    # -- emotes ------------------------------------------------------------

    def dpr(self):
        if self._dpr is None:
            try:
                from PyQt5.QtWidgets import QApplication
                screen = QApplication.primaryScreen()
                self._dpr = max(1.0, float(screen.devicePixelRatio()))
            except Exception:                   # noqa: BLE001
                self._dpr = 1.0
        return self._dpr

    def pixmap(self, key, now_ms=None):
        """The emote's picture now (the current frame), or None for not
        here yet (it is asked for)."""
        got = self._frames.get(key)
        if got is None:
            self._ask(key)
            return None
        frames, delays, total = got
        if len(frames) == 1 or not option("chat_animated"):
            return frames[0]        # "Show animated emotes" off: a still
        return frames[self.frame_index(key, now_ms)]

    def frame_index(self, key, now_ms=None):
        got = self._frames.get(key)
        if not got or len(got[0]) == 1:
            return 0
        frames, delays, total = got
        if now_ms is None:
            now_ms = (time.monotonic() - self._started) * 1000
        t = now_ms % total
        for i, d in enumerate(delays):
            if t < d:
                return i
            t -= d
        return len(frames) - 1

    def animated(self, key):
        got = self._frames.get(key)
        if not option("chat_animated"):
            return False
        return bool(got) and len(got[0]) > 1

    def failed(self, key):
        return key in self._failed

    def _ask(self, key):
        if key in self._asked:
            return
        url = emote_url(key)
        if not url:
            return
        self._asked.add(key)
        fetcher = self._fetcher
        alive = False
        try:
            alive = fetcher is not None and fetcher.isRunning()
        except RuntimeError:
            alive = False
        if not alive:
            fetcher = _EmoteFetcher(round(EMOTE_PX * self.dpr()))
            fetcher.got.connect(self._got)
            fetcher.failed.connect(self._failed_one)
            self._fetcher = fetcher
            fetcher.start()
        fetcher.todo.put((key, url, round(picture_px(key) * self.dpr())))

    def _got(self, key, images, delays):
        from PyQt5.QtGui import QPixmap
        dpr = self.dpr()
        frames = []
        for img in images:
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(dpr)
            frames.append(pm)
        self._frames[key] = (frames, delays, max(1, sum(delays)))
        self.stats["emotes"] += 1
        if len(frames) > 1:
            self.stats["animated"] += 1
        if self.stats["emotes"] == 1:
            _log(f"emotes: first picture arrived ({key}, "
                 f"{frames[0].width()}x{frames[0].height()} px, "
                 f"{len(frames)} frame(s))")
        self._arrived.append(key)
        self._soon()

    def _failed_one(self, key, why):
        self._failed[key] = why
        self.stats["failed"] += 1
        if self.stats["failed"] <= 3:
            _log(f"emotes: no picture for {key} ({why}) - its name is "
                 f"shown instead")
        self._arrived.append(key)
        self._soon()

    def _soon(self):
        if not self._flush.isActive():     # a busy chat can't put it off
            self._flush.start()

    def _send_arrived(self):
        if self._arrived:
            keys, self._arrived = self._arrived, []
            self.emotes_arrived.emit(keys)
        if self._pron_new:
            logins, self._pron_new = self._pron_new, []
            self.pronouns_arrived.emit(logins)

    # -- animation -----------------------------------------------------------

    def watch(self, view):
        """`view._animate(now_ms)` is called on each animation tick while
        it returns True (it has animated emotes on show)."""
        self._views.add(view)
        if not self._anim.isActive():
            self._anim.start()

    def _tick(self):
        now = (time.monotonic() - self._started) * 1000
        busy = False
        for view in list(self._views):
            try:
                busy = view._animate(now) or busy
            except RuntimeError:
                pass
        if not busy:
            self._anim.stop()

    # -- BetterTTV -------------------------------------------------------------

    def chan(self, channel):
        """The channel's `_Chan`, made when first asked for."""
        channel = (channel or "").lower()
        c = self._chans.get(channel)
        if c is None:
            c = self._chans[channel] = _Chan(channel)
        return c

    def peek(self, channel):
        """The channel's `_Chan` if there is one, else an empty one."""
        return self._chans.get((channel or "").lower(), _NOBODY)

    def bttv(self, channel=""):
        """code → emote id, the channel's over the global ones."""
        if not option("chat_emotes"):
            return {}
        own = self.peek(channel).bttv
        if own:
            merged = dict(self.bttv_global)
            merged.update(own)
            return merged
        return self.bttv_global

    def load_bttv(self, room_id, channel):
        """BetterTTV's global list (once a run) and this channel's."""
        if not option("chat_emotes"):
            return
        import workers
        if not self._bttv_global_asked:
            self._bttv_global_asked = True
            job = workers.Lookup(_bttv_global)
            job.done.connect(self._bttv_global_done)
            job.start()
        c = self.chan(channel)
        if room_id and room_id != c.bttv_room:
            c.bttv_room = room_id
            c.bttv = {}
            job = workers.Lookup(lambda: _bttv_channel(room_id))
            job.done.connect(lambda result, error, c=c:
                             self._bttv_channel_done(c, result, error))
            job.start()

    def forget_channel(self, channel):
        """Nothing shows this channel any more: drop what was loaded."""
        c = self._chans.pop((channel or "").lower(), None)
        if c is None:
            return
        if c.bttv:
            self.bttv_changed.emit()
        if c.mine:
            self.mine_changed.emit()

    # -- badges (task 95) ------------------------------------------------------

    def load_badges(self, room_id, channel):
        """Twitch's global badges (once a run) and this channel's own."""
        if not option("chat_badges"):
            return
        want_global = not BADGES
        c = self.chan(channel)
        if room_id == c.badge_room and not want_global:
            return
        import workers
        c.badge_room = room_id
        job = workers.Lookup(
            lambda: _load_badges(room_id, channel, want_global))
        job.done.connect(lambda result, error, c=c:
                         self._badges_done(c, result, error))
        job.start()

    def _badges_done(self, c, result, error):
        channel = c.channel
        here = self._chans.get(channel) is c    # not dropped meanwhile
        if error is not None or not result:
            if here:
                c.badge_room = None         # try again next time
            _log(f"badges: none for '{channel}' ({_why(error)}) - names "
                 f"show without them")
            return
        glob, chan, source = result
        if glob:
            BADGES.update(glob)
        if here:
            c.badges = dict(chan)
        _log(f"badges: {len(glob)} global, {len(chan)} of '{channel}''s "
             f"own, from {source}")
        self.badges_changed.emit()

    # -- chat rules (task 95) ----------------------------------------------------

    def load_rules(self, channel):
        """The channel's chat rules, shown before you first write there."""
        if not channel:
            return
        c = self.chan(channel)
        if c.rules_asked:
            return
        import workers
        c.rules_asked = True
        c.rules = []
        job = workers.Lookup(lambda: _chat_rules(channel))
        job.done.connect(lambda result, error, c=c:
                         self._rules_done(c, result, error))
        job.start()

    def chan_rules(self, channel):
        """The channel's chat rules (what has arrived)."""
        return self.peek(channel).rules

    def _rules_done(self, c, result, error):
        channel = c.channel
        if self._chans.get(channel) is not c:
            return
        if error is not None or not isinstance(result, list):
            _log(f"rules: could not read '{channel}''s chat rules "
                 f"({_why(error)}) - you can chat without them")
            return
        c.rules = result
        _log(f"rules: '{channel}' has {len(result)} chat rule(s)"
             + (" - shown before you first write there"
                if result and channel not in self.rules_ok else ""))
        self.rules_changed.emit(channel)

    def rules_pending(self, channel):
        """True while this channel has rules you haven't OK'd."""
        return bool(channel and self.peek(channel).rules
                    and channel not in self.rules_ok)

    # -- your own Twitch emotes (task 95) ----------------------------------------

    def load_my_emotes(self, emote_sets, room_id, channel):
        """The emotes you can use, from the sets the chat server named."""
        sets = tuple(sorted({x.strip() for x in (emote_sets or "").split(",")
                             if x.strip()}))
        c = self.chan(channel)
        if not sets or (sets, room_id) == c.mine_asked:
            return
        import workers
        c.mine_asked = (sets, room_id)
        job = workers.Lookup(lambda: _my_emotes(sets, room_id))
        job.done.connect(lambda result, error, c=c, n=len(sets):
                         self._mine_done(c, n, result, error))
        job.start()

    def _mine_done(self, c, sets, result, error):
        if self._chans.get(c.channel) is not c:
            return
        if error is not None or not isinstance(result, list):
            c.mine_asked = None
            _log(f"your emotes: could not read them ({_why(error)}) - the "
                 f"picker shows BetterTTV's only")
            return
        c.mine = result
        c.mine_keys = {row["key"]: row for row in result}
        for row in result:
            if len(LEARNED) < 8000:
                LEARNED[row["code"]] = row["key"][3:]
        groups = sorted({row["group"] or "this channel" for row in result})
        _log(f"your emotes: {len(result)} from {sets} emote set(s) "
             f"({', '.join(groups[:6])}{'…' if len(groups) > 6 else ''})")
        self.mine_changed.emit()

    # -- pinned emotes (task 106) and where an emote works (task 108) -----------

    def set_room(self, channel, room_id):
        """The channel's Twitch number, for `why_not`."""
        room_id = str(room_id or "")
        c = self.chan(channel)
        if room_id != c.room_id:
            c.room_id = room_id
            self.mine_changed.emit()            # views repaint their greys

    def set_pins(self, rows):
        """The saved pins, at start-up (no signal)."""
        out, seen = [], set()
        for row in rows or []:
            if isinstance(row, dict) and row.get("key") and row.get("code") \
                    and row["key"] not in seen:
                seen.add(row["key"])
                out.append({"key": str(row["key"]), "code": str(row["code"])})
        self.pins = out[:60]

    def is_pinned(self, key):
        return any(p["key"] == key for p in self.pins)

    def pin(self, key, code):
        if self.is_pinned(key) or len(self.pins) >= 60:
            return
        self.pins.append({"key": key, "code": code})
        _log(f"pinned emote '{code}' ({len(self.pins)} on the bar)")
        self.pins_changed.emit()

    def unpin(self, key):
        kept = [p for p in self.pins if p["key"] != key]
        if len(kept) != len(self.pins):
            self.pins = kept
            _log(f"unpinned an emote ({len(self.pins)} on the bar)")
            self.pins_changed.emit()

    def why_not(self, key, channel=""):
        """"" when you can use this emote in this channel, else the
        reason, in a few words (task 108). What isn't known yet (your
        emotes, BetterTTV's lists, the channel's number) counts as usable.
        Your emotes: a follower or Bits emote only works in the channel
        it belongs to (Twitch's `emote_type`); BetterTTV's channel emotes
        only in their channel."""
        kind, _, emote_id = key.partition(":")
        c = self.peek(channel)
        if kind == "tw":
            if not c.mine_keys:
                return ""
            row = c.mine_keys.get(key)
            if row is None:
                return "You don't have this emote"
            if row.get("restricted") and c.room_id and \
                    row.get("owner") != c.room_id:
                return (f"Only works in {row['group']}'s channel"
                        if row.get("group") else
                        "Only works in its own channel")
            return ""
        if kind == "bttv":
            if not self.bttv_global:
                return ""
            if emote_id in self.bttv_global.values() or \
                    emote_id in c.bttv.values():
                return ""
            return "A BetterTTV emote from another channel"
        return ""

    def emote_groups(self, channel=""):
        """[(title, [(key, code)])] for the emote picker, in Twitch's
        order: this channel's, its BetterTTV ones, your other channels',
        Twitch's global ones, BetterTTV's global ones."""
        out = []
        ranked = {}
        c = self.peek(channel)
        for row in c.mine:
            ranked.setdefault((row["rank"], row["group"]), []).append(
                (row["key"], row["code"]))
        here = [v for (rank, _g), v in ranked.items() if rank == 0]
        if here:
            out.append((f"{channel}'s emotes" if channel else
                        "This channel's emotes",
                        [e for part in here for e in part]))
        if c.bttv:
            out.append(("BetterTTV: " + (channel or "this channel"),
                        sorted((("bttv:" + i, code) for code, i in
                                c.bttv.items()),
                               key=lambda e: e[1].lower())))
        for (rank, group), rows in sorted(
                ranked.items(), key=lambda kv: (kv[0][0], kv[0][1].lower())):
            if rank != 0:
                out.append((group, rows))
        if self.bttv_global:
            out.append(("BetterTTV", sorted(
                (("bttv:" + i, code) for code, i in self.bttv_global.items()
                 if code not in c.bttv),
                key=lambda e: e[1].lower())))
        return out

    def suggestions(self, typed, limit=8, channel=""):
        """Emotes whose name starts with (then holds) `typed`, for the
        ":" list: [(key, code)]."""
        typed = (typed or "").lower()
        if not typed:
            return []
        seen, starts, holds = set(), [], []
        for _title, rows in self.emote_groups(channel):
            for key, code in rows:
                low = code.lower()
                if code in seen:
                    continue
                if low.startswith(typed):
                    seen.add(code)
                    starts.append((key, code))
                elif typed in low:
                    seen.add(code)
                    holds.append((key, code))
        starts.sort(key=lambda e: (len(e[1]), e[1].lower()))
        holds.sort(key=lambda e: (len(e[1]), e[1].lower()))
        return (starts + holds)[:limit]

    def _bttv_global_done(self, result, error):
        if error is not None or not isinstance(result, dict):
            self._bttv_global_asked = False     # try again next channel
            _log(f"BetterTTV: could not read the global emotes "
                 f"({_why(error)})")
            return
        self.bttv_global = result
        _log(f"BetterTTV: {len(result)} global emotes")
        self.bttv_changed.emit()

    def _bttv_channel_done(self, c, result, error):
        channel = c.channel
        if self._chans.get(channel) is not c:
            return
        if error is not None:
            _log(f"BetterTTV: could not read '{channel}''s emotes "
                 f"({_why(error)})")
            return
        if not result:
            _log(f"BetterTTV: '{channel}' has no BetterTTV emotes")
            return
        c.bttv = result
        _log(f"BetterTTV: '{channel}' has {len(result)} emotes of its own")
        self.bttv_changed.emit()

    # -- pronouns --------------------------------------------------------------

    def pronoun(self, login):
        got = self._pron.get(login)
        return got[0] if got else ""

    def want_pronouns(self, items):
        """Ask for the pronouns of everyone in `items` not asked yet."""
        if not option("chat_pronouns"):
            return
        now = time.monotonic()
        fetcher = None
        for item in items:
            login = item.get("login") or ""
            if not login:
                continue
            got = self._pron.get(login)
            if login in self._pron_asked and (
                    got is None or now - got[1] < PRONOUN_KEEP_S):
                continue                    # on its way, or still fresh
            self._pron_asked.add(login)
            if fetcher is None:
                fetcher = self._pronoun_fetcher()
            fetcher.todo.put((login, item.get("user_id") or ""))

    def drop_pending_pronouns(self):
        """A new channel: the old one's queue no longer matters."""
        fetcher = self._pron_fetcher
        if fetcher is None:
            return
        try:
            while True:
                login, _uid = fetcher.todo.get_nowait()
                self._pron_asked.discard(login)
        except (queue.Empty, RuntimeError):
            pass

    def _pronoun_fetcher(self):
        fetcher = self._pron_fetcher
        try:
            if fetcher is not None and fetcher.isRunning():
                return fetcher
        except RuntimeError:
            pass
        fetcher = _PronounFetcher()
        fetcher.found.connect(self._pronouns_found)
        self._pron_fetcher = fetcher
        fetcher.start()
        return fetcher

    def _pronouns_found(self, answers):
        now = time.monotonic()
        for login, label in answers.items():
            self._pron[login] = (label, now)
            if label:
                self._pron_new.append(login)
        if self._pron_new:
            self._soon()

    def summary(self, channel=""):
        """One line for the log when a chat stops."""
        c = self.peek(channel)
        bits = [f"emote pictures: {self.stats['emotes']} "
                f"({self.stats['animated']} animated), "
                f"{self.stats['failed']} failed"]
        bits.append(f"BetterTTV: {len(self.bttv_global)} global, "
                    f"{len(c.bttv)} channel")
        bits.append(f"badges: {len(BADGES)} global, "
                    f"{len(c.badges)} channel; your emotes: "
                    f"{len(c.mine)}")
        f = self._pron_fetcher
        if f is not None:
            try:
                s = f.stats
                bits.append(f"pronouns: {s['asked']} asked, "
                            f"{s['alejo']} from pr.alejo.io, "
                            f"{s['pronoundb']} from PronounDB, "
                            f"{s['failed']} failed")
            except RuntimeError:
                pass
        return "; ".join(bits)


def _why(error):
    if error is None:
        return "nothing came back"
    return f"{type(error).__name__}: {str(error)[:80]}"


def _bttv_rows(rows):
    out = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("code") and row.get("id"):
            out[str(row["code"])] = str(row["id"])
    return out


def _bttv_global():
    reply = _session().get(BTTV_GLOBAL_URL, timeout=10)
    reply.raise_for_status()
    return _bttv_rows(reply.json())


def _bttv_channel(room_id):
    reply = _session().get(BTTV_CHANNEL_URL.format(id=room_id), timeout=10)
    if reply.status_code == 404:
        return {}
    reply.raise_for_status()
    data = reply.json() or {}
    out = _bttv_rows(data.get("sharedEmotes"))
    out.update(_bttv_rows(data.get("channelEmotes")))
    return out


# -- Twitch's API (task 95) ---------------------------------------------------

def _helix_headers():
    """Client ID + your chat sign-in, or None when not signed in.
    Network (the token may be renewed): background threads only."""
    import twitchauth
    cid = twitchauth.client_id()
    creds = twitchauth.chat_credentials() if cid else None
    if not creds:
        return None
    return {"Client-Id": cid, "Authorization": "Bearer " + creds[1]}


def _helix_get(path, params, headers):
    reply = _session().get(HELIX_URL + path, params=params, headers=headers,
                           timeout=10)
    if reply.status_code != 200:
        raise RuntimeError(f"HTTP {reply.status_code} "
                           f"{reply.text[:80].strip()}")
    return (reply.json() or {}).get("data") or []


def _helix_badges(rows):
    out = {}
    for row in rows or []:
        set_id = (row or {}).get("set_id")
        for v in (row or {}).get("versions") or []:
            url = v.get("image_url_2x") or v.get("image_url_1x")
            if set_id and v.get("id") and url:
                out[f"{set_id}/{v['id']}"] = (url, v.get("title") or set_id)
    return out


def _gql_badges(rows):
    out = {}
    for b in rows or []:
        if isinstance(b, dict) and b.get("setID") and b.get("version") \
                and b.get("imageURL"):
            out[f"{b['setID']}/{b['version']}"] = (
                b["imageURL"], b.get("title") or b["setID"])
    return out


# The query Twitch's own website makes for badges: a guess at its shape
# (the sandbox can't reach Twitch). Only used when you are not signed in.
_BADGE_QUERY = ("query($login: String!) { badges { setID version title "
                "imageURL(size: DOUBLE) } user(login: $login) { "
                "broadcastBadges { setID version title "
                "imageURL(size: DOUBLE) } } }")


def _load_badges(room_id, channel, want_global):
    """(global, the channel's, where from)."""
    tried = []
    try:
        headers = _helix_headers()
    except Exception as exc:                    # noqa: BLE001
        headers = None
        tried.append(f"sign-in: {_why(exc)}")
    if headers:
        try:
            glob = _helix_badges(_helix_get(
                "/chat/badges/global", {}, headers)) if want_global else {}
            chan = _helix_badges(_helix_get(
                "/chat/badges", {"broadcaster_id": room_id},
                headers)) if room_id else {}
            return glob, chan, "Twitch's API"
        except Exception as exc:                # noqa: BLE001
            tried.append(f"api: {_why(exc)}")
    else:
        tried.append("api: not signed in")
    try:
        import twitch
        reply = _session().post(
            twitch.GQL_URL, timeout=10,
            headers={"Client-Id": twitch.WEB_CLIENT_ID,
                     "User-Agent": twitch.BROWSER_AGENT},
            json={"query": _BADGE_QUERY, "variables": {"login": channel}})
        if reply.status_code != 200:
            raise RuntimeError(f"HTTP {reply.status_code}")
        body = reply.json()
        if isinstance(body, list):
            body = body[0] if body else {}
        data = (body or {}).get("data") or {}
        glob = _gql_badges(data.get("badges")) if want_global else {}
        chan = _gql_badges((data.get("user") or {}).get("broadcastBadges"))
        if glob or chan:
            return glob, chan, ("Twitch's website API (" + "; ".join(tried)
                                + ")")
        errors = (body or {}).get("errors") or []
        first = errors[0] if errors else "nothing came back"
        tried.append("website: " + str(first.get("message", first)
                                       if isinstance(first, dict)
                                       else first)[:80])
    except Exception as exc:                    # noqa: BLE001
        tried.append(f"website: {_why(exc)}")
    raise RuntimeError("; ".join(tried))


# What Twitch's own website asks for a channel's chat rules: a guess at
# its shape (the public API has no chat rules; the sandbox can't reach
# Twitch).
_RULES_QUERY = ("query($login: String!) { user(login: $login) { "
                "chatSettings { rules } } }")


def _chat_rules(channel):
    import twitch
    reply = _session().post(
        twitch.GQL_URL, timeout=10,
        headers={"Client-Id": twitch.WEB_CLIENT_ID,
                 "User-Agent": twitch.BROWSER_AGENT},
        json={"query": _RULES_QUERY, "variables": {"login": channel}})
    if reply.status_code != 200:
        raise RuntimeError(f"HTTP {reply.status_code}")
    body = reply.json()
    if isinstance(body, list):
        body = body[0] if body else {}
    errors = (body or {}).get("errors") or []
    user = ((body or {}).get("data") or {}).get("user")
    if user is None:
        first = errors[0] if errors else "no such channel"
        raise RuntimeError(str(first.get("message", first)
                               if isinstance(first, dict) else first)[:80])
    rules = ((user.get("chatSettings") or {}).get("rules")) or []
    return [str(r).strip() for r in rules if str(r).strip()][:20]


def _my_emotes(sets, room_id):
    """Your Twitch emotes from your emote sets: [{key, code, group,
    rank}], rank 0 = this channel's, 2 = another channel's, 3 = Twitch's
    global ones."""
    headers = _helix_headers()
    if not headers:
        raise RuntimeError("not signed in")
    rows = []
    for i in range(0, len(sets), 25):
        rows += _helix_get("/chat/emotes/set",
                           [("emote_set_id", x) for x in sets[i:i + 25]],
                           headers)
    plain = ("", "0", "twitch")
    owners = sorted({str(r.get("owner_id") or "") for r in rows} -
                    set(plain) - {room_id})
    names = {}
    if owners:
        try:
            for u in _helix_get("/users", [("id", o) for o in owners[:100]],
                                headers):
                names[str(u.get("id"))] = u.get("display_name") or \
                    u.get("login") or ""
        except Exception as exc:                # noqa: BLE001
            _log(f"your emotes: no channel names ({_why(exc)})")
    out, seen = [], set()
    for r in rows:
        code, eid = r.get("name") or "", str(r.get("id") or "")
        if not code or not eid or r.get("emote_type") == "smilies" or \
                "\\" in code or code in seen:
            continue
        seen.add(code)
        owner = str(r.get("owner_id") or "")
        if room_id and owner == room_id:
            group, rank = "", 0
        elif owner in plain or r.get("emote_type") == "globals":
            group, rank = "Twitch", 3
        else:
            group, rank = names.get(owner) or "Other channels", 2
        out.append({"key": "tw:" + eid, "code": code, "group": group,
                    "rank": rank, "owner": owner,
                    "restricted": r.get("emote_type") in
                    ("follower", "bitstier")})
    return out


_shared = None


def shared():
    """The one ChatExtras of the app (made on first use, GUI thread)."""
    global _shared
    if _shared is None:
        _shared = ChatExtras()
    return _shared
