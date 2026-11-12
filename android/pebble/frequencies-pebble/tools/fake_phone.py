#!/usr/bin/env python3
"""A fake Frequencies phone for testing the watchapp in the Pebble emulator.

Talks to the emulator through pypkjs (the same websocket `pebble` uses) and answers the watchapp
like the Android app does, with canned data. Run it with pebble-tool's own Python:

    ~/.local/share/uv/tools/pebble-tool/bin/python tools/fake_phone.py [ws://localhost:PORT]

Scenarios are switched over a small control port (127.0.0.1:7777), one line each:
    scene playing | unsaved | ad | nothing | stuck | grown_off
    saved N         (Saved Frequencies holds N rows, like a big library)
    dead            (stop answering)      alive  (answer again)
    push            (send the Now playing state again)
The protocol is in notes/pebble-protocol.md and must match src/c/freq.h and core/.../Pebble.kt.
"""
import json, os, socket, struct, sys, threading, time, queue, uuid as uuidlib

from libpebble2.communication import PebbleConnection
from libpebble2.communication.transports.websocket import WebsocketTransport
from libpebble2.services.appmessage import AppMessageService, Uint8, Uint16, Uint32, Int32, CString, ByteArray
from PIL import Image, ImageDraw, ImageFont

APP_UUID = uuidlib.UUID("45e9b1e5-580c-414a-a559-e7a31f438d51")
HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = json.load(open(os.path.join(HERE, "..", "package.json")))["pebble"]["messageKeys"]
K = lambda name: KEYS[name]

(CMD_HELLO, CMD_BYE, CMD_PLAY_PAUSE, CMD_NEXT, CMD_PREV, CMD_SCAN_TO, CMD_PLAY_ITEM, CMD_PLAY_RANDOM,
 CMD_STAR, CMD_SHUFFLE, CMD_SONG_ID, CMD_THUMB_UP, CMD_THUMB_DOWN, CMD_THUMB_UNDO, CMD_THUMB_SEND_NOW,
 CMD_SLEEP_SET, CMD_SLEEP_ACTION, CMD_GET_LIST, CMD_SET_TAG, CMD_FAVORITE, CMD_GET_BIO, CMD_LYRICS,
 CMD_LYRIC_HOLD, CMD_LYRIC_STEP, CMD_LYRIC_SYNC, CMD_SEARCH, CMD_LISTEN, CMD_SAVE, CMD_OPEN_SERVICE,
 CMD_SORT, CMD_PREF, CMD_FIND_MORE, CMD_GET_FEATURED, CMD_VOLUME, CMD_STATION) = range(1, 36)
(LIST_SAVED, LIST_TAGS, LIST_FAVORITES, LIST_HISTORY, LIST_SEARCH, LIST_FEATURED, LIST_PREV_FEATURED,
 LIST_THEMES, LIST_PICK_TAG, LIST_COVERS) = range(1, 11)
COVERS = [dict(name="Groove Salad"), dict(name="Radio Paradise", flags=0), dict(name="KEXP")]
# station settings (STATION_SET): flags saved|ad offered|ad on|buffer on, gain +3 dB, ad 15 s, buffer auto, worst 4.5 s, eq
ST = [0x01 | 0x02 | 0x04 | 0x10, 46, 15, 0, 9, 14, 12, 10] + [12] * 10
ART_COVER, ART_LOGO, ART_PHOTO, ART_FEATURED = 1, 2, 3, 4
RF_PLAYING, RF_DOWN, RF_FAVORITE, RF_SAVED, RF_HEADING, RF_CURRENT = 1, 2, 4, 8, 0x40, 0x80

# ---- pictures: 64 colors with ordered dithering, logos as 1-bit shapes (MSB first) ----
BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]


def to_gcolor8(img):
    # Floyd-Steinberg, serpentine, like the phone since build 191
    img = img.convert("RGB")
    w, h = img.size
    px = img.load()
    err = [[[0.0] * 3 for _ in range(w + 2)] for _ in range(2)]
    out = bytearray(w * h)
    for y in range(h):
        cur, nxt = err[y & 1], err[(y + 1) & 1]
        for e in nxt:
            e[0] = e[1] = e[2] = 0.0
        xs = range(w) if y % 2 == 0 else range(w - 1, -1, -1)
        f = 1 if y % 2 == 0 else -1
        for x in xs:
            q = []
            for c in range(3):
                v = px[x, y][c] + cur[x + 1][c]
                n = max(0, min(3, int(round(v / 85.0))))
                e = v - n * 85
                q.append(n)
                if 0 <= x + f < w: cur[x + 1 + f][c] += e * 7 / 16
                if 0 <= x - f < w: nxt[x + 1 - f][c] += e * 3 / 16
                nxt[x + 1][c] += e * 5 / 16
                if 0 <= x + f < w: nxt[x + 1 + f][c] += e / 16
            out[y * w + x] = 0xC0 | (q[0] << 4) | (q[1] << 2) | q[2]
    return bytes(out)


def to_mask(img):
    img = img.convert("L")
    w, h = img.size
    px = img.load()
    stride = (w + 7) // 8
    out = bytearray(stride * h)
    for y in range(h):
        for x in range(w):
            if px[x, y] >= 128:
                out[y * stride + x // 8] |= 0x80 >> (x % 8)
    return bytes(out)


def sunset(size):
    im = Image.new("RGB", (size, size), (85, 0, 85))
    d = ImageDraw.Draw(im)
    cx, cy, r = size // 2, size * 11 // 20, size * 3 // 10
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 170, 0))
    for i in range(4):
        y = cy - r // 3 + i * (r // 4)
        d.rectangle([cx - r, y, cx + r, y + max(1, size // 40)], fill=(85, 0, 85))
    d.rectangle([0, cy + r // 2, size, size], fill=(255, 0, 170))
    for i in range(-3, 4):
        d.line([cx, cy + r // 2, cx + i * size // 3, size], fill=(85, 0, 85))
    return im


def waves(size, bg=(0, 0, 85)):
    im = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(im)
    import math
    for k, col in enumerate([(0, 170, 170), (85, 255, 255), (0, 170, 170)]):
        pts = [(x, size * (3 + 2 * k) // 10 + int(6 * math.sin(x / size * 6.28 + k))) for x in range(size)]
        d.line(pts, fill=col, width=max(2, size // 16))
    return im


def portrait(size):
    im = Image.new("RGB", (size, size), (85, 0, 85))
    d = ImageDraw.Draw(im)
    d.ellipse([size // 2, -size // 4, size * 5 // 4, size // 2], fill=(170, 85, 170))
    d.rectangle([0, size // 2, size, size], fill=(0, 85, 170))
    d.ellipse([size // 4, size // 5, size * 3 // 5, size * 11 // 20], fill=(0, 0, 0))
    d.ellipse([size // 8, size * 3 // 5, size * 7 // 8, size * 3 // 2], fill=(0, 0, 0))
    return im


def grid(size):
    im = Image.new("RGB", (size, size), (0, 0, 0))
    d = ImageDraw.Draw(im)
    for i in range(0, size, size // 6):
        d.line([i, size // 2, size // 2 + (i - size // 2) * 2, size], fill=(255, 0, 255))
    for j in range(size // 2, size, size // 10):
        d.line([0, j, size, j], fill=(255, 0, 255))
    d.ellipse([size // 3, size // 8, size * 2 // 3, size * 11 // 24], fill=(255, 255, 0))
    return im


def logo(text, w=170, h=28):
    im = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.load_default(size=h - 6)
    except TypeError:
        f = ImageFont.load_default()
    d.text((1, 1), text, fill=255, font=f, stroke_width=1, stroke_fill=255)
    # crop to the drawn width
    box = im.getbbox() or (0, 0, w, h)
    return im.crop(box)


# ---- packing ----
def pack_rows(rows):
    out = bytearray()
    for r in rows:
        name = r["name"].encode()[:43]
        sub = r.get("sub", "").encode()[:51]
        tags = r.get("tags", [])[:4]
        out += struct.pack("<HIBBiB", r["index"], r.get("id", r["index"] + 1), r.get("flags", 0), r.get("color", 0),
                           r.get("time", 0), len(tags))
        out += bytes(tags)
        out += bytes([len(name)]) + name + bytes([len(sub)]) + sub
    return bytes(out)


def pack_tags(tags):
    out = bytearray([len(tags)])
    for t in tags:
        n = t["name"].encode()[:23]
        out += struct.pack("<BBHB", t["color"], t["state"], t["count"], len(n)) + n
    return bytes(out)


OUTRUN = bytes([0xC0, 0xFF, 0xFB, 0xE6, 0xF8, 0xDF, 0xF1, 0xC1, 0xDD])
NOW = int(time.time())
SAVED = [
    dict(name="Nightwave Plaza", tags=[0, 1], flags=RF_PLAYING),
    dict(name="SomaFM Groove Salad", tags=[1]),
    dict(name="Radio Paradise", tags=[2, 1, 0]),
    dict(name="KEXP 90.3", tags=[3]),
    dict(name="Lofi Lagoon", tags=[0]),
    dict(name="Dublab", flags=RF_DOWN, time=NOW - 3 * 3600),
    dict(name="NTS 1", tags=[2]),
    dict(name="WFMU", tags=[3, 2]),
]
TAGS = [dict(name="Synthwave", color=0xF3, state=1, count=4), dict(name="Chill", color=0xCF, state=0, count=6),
        dict(name="Eclectic", color=0xF8, state=2, count=3), dict(name="Indie", color=0xDD, state=0, count=2)]
FAVS = [dict(name="Today", flags=RF_HEADING), dict(name="Night Drive", sub="Neon Static · Nightwave Plaza", flags=RF_FAVORITE, time=NOW - 600),
        dict(name="Tidal Lines", sub="Lofi Lagoon · Lofi Lagoon", flags=RF_FAVORITE, time=NOW - 3000),
        dict(name="Yesterday", flags=RF_HEADING), dict(name="Glass Coast", sub="Mirage Club · SomaFM Groove Salad", flags=RF_FAVORITE, time=NOW - 90000)]
HISTORY = [dict(name="Today", flags=RF_HEADING), dict(name="Night Drive", sub="Neon Static · Nightwave Plaza", flags=RF_FAVORITE, time=NOW - 600),
           dict(name="Paper Moon", sub="Velvet Hours · Nightwave Plaza", time=NOW - 900),
           dict(name="Low Tide", sub="Harbor Lights · Radio Paradise", time=NOW - 1800)]
SEARCH = [dict(name="Midnight Ave FM", sub="US · synthwave · 128 kbps", flags=RF_SAVED), dict(name="Retro Rewind", sub="UK · 80s · 192 kbps"),
          dict(name="Vapor Radio", sub="JP · vaporwave · 128 kbps", flags=RF_PLAYING), dict(name="Chillhop Cafe", sub="NL · lofi · 96 kbps")]
FEATURED = [dict(name="Lofi Lagoon"), dict(name="Midnight Ave", flags=RF_SAVED), dict(name="Starfield"), dict(name="Gridline")]
PREV_FEATURED = [dict(name="Last week", flags=RF_HEADING), dict(name="Coastal FM", sub="US · surf rock · 128 kbps"),
                 dict(name="Analog Dreams", sub="DE · synth · 192 kbps", flags=RF_SAVED)]
THEMES = [dict(name="Outrun", flags=RF_CURRENT), dict(name="Amber CRT"), dict(name="Teletext"), dict(name="OG")]
BIO = ("Neon Static is a synthwave duo from Tucson, Arizona, formed in 2016 by two late-night radio DJs. Their second "
       "album, Night Drive, mixes analog synths with field recordings of desert highways. They tour every summer and "
       "still host a weekly show on community radio, where most of their songs are played first.")
LYRICS = ["chasing a signal through the rain", "your voice came in on ninety-nine point one",
          "and the whole town lit up again", "we drove until the static sang", "a song nobody else could hear"]


class Phone:
    def __init__(self, url):
        self.pebble = PebbleConnection(WebsocketTransport(url))
        self.pebble.connect()
        self.pebble.run_async()
        self.am = AppMessageService(self.pebble)
        self.am.register_handler("appmessage", self.on_message)
        self.am.register_handler("ack", self.on_ack)
        self.am.register_handler("nack", self.on_nack)
        self.q = queue.Queue()
        self.acked = threading.Event()
        self.alive = True
        self.scene("playing")
        threading.Thread(target=self.sender, daemon=True).start()

    # ---- state ----
    def scene(self, name):
        self.s = dict(station="Nightwave Plaza", song="Night Drive", artist="Neon Static", state=1, flags=0x01 | 0x20 | 0x40 | 0x80 | 0x08 | 0x10,
                      sleep_left=25 * 60, sleep_action=0, ad_left=0, pos=0, count=len(SAVED), lyric=1, lyric_state=1, cover=True,
                      vol=9)
        if name == "unsaved":
            self.s.update(station="Vapor Radio", flags=0x40 | 0x08 | 0x10, sleep_left=0)
        elif name == "ad":
            self.s.update(ad_left=15, song="Covering an ad", artist="Music's back in 15s", flags=0x20 | 0x40)
        elif name == "nothing":
            self.s.update(station="", song="", artist="", state=0, flags=0, sleep_left=0, cover=False)
        elif name == "stuck":
            self.s.update(lyric_state=2)
        self.name = name

    # ---- sending ----
    def send(self, d):
        self.q.put(d)

    def sender(self):
        while True:
            d = self.q.get()
            for attempt in range(4):
                self.acked.clear()
                self.ok = False
                self.am.send_message(APP_UUID, d)
                self.acked.wait(3)
                if self.ok:
                    break
                time.sleep(0.2)

    def on_ack(self, txid, uuid):
        self.ok = True
        self.acked.set()

    def on_nack(self, txid, uuid):
        self.ok = False
        self.acked.set()

    def now_dict(self):
        s = self.s
        return {K("STATION"): CString(s["station"]), K("SONG"): CString(s["song"]), K("ARTIST"): CString(s["artist"]),
                K("STATE"): Uint8(s["state"]), K("FLAGS"): Uint16(s["flags"]), K("SLEEP_LEFT"): Int32(s["sleep_left"]),
                K("SLEEP_ACTION"): Uint8(s["sleep_action"]), K("AD_LEFT"): Uint16(s["ad_left"]), K("POS"): Uint16(s["pos"]),
                K("COUNT"): Uint16(s["count"])}

    def push_now(self, all_=False):
        d = self.now_dict()
        if all_:
            d.update({K("PROTOCOL"): Uint8(1), K("THEME"): ByteArray(OUTRUN), K("PREFS"): ByteArray(bytes([1, 0, 0, 1, 0, 7])),
                      K("THEME_NAME"): CString("Outrun"), K("SORT"): Uint8(0), K("TAGS"): ByteArray(pack_tags(TAGS))})
        self.send(d)
        if self.s["cover"]:
            self.art(ART_COVER, int(time.time() * 10) & 0xFFFFFF, sunset(144))
            if not os.environ.get("FP_NOLOGO"): self.art(ART_LOGO, 2, logo("NEON STATIC"), mask=True)
        else:
            self.send({K("ART_KIND"): Uint8(ART_COVER), K("ART_ID"): Uint32(0), K("ART_TOTAL"): Uint32(0), K("ART_OFFSET"): Uint32(0)})

    def art(self, kind, art_id, img, mask=False, slot=0):
        data = to_mask(img) if mask else to_gcolor8(img)
        w, h = img.size
        for off in range(0, len(data), 2000):
            d = {K("ART_OFFSET"): Uint32(off), K("ART_DATA"): ByteArray(data[off:off + 2000]), K("ART_ID"): Uint32(art_id)}
            if off == 0:
                d.update({K("ART_KIND"): Uint8(kind), K("ART_SLOT"): Uint8(slot),
                          K("ART_W"): Uint16(w), K("ART_H"): Uint16(h), K("ART_TOTAL"): Uint32(len(data))})
            self.send(d)

    def send_list(self, kind, start, rows):
        rows = [dict(r, index=i) for i, r in enumerate(rows)]
        window = rows[start:start + 24]
        # rows go in messages of up to ~1500 bytes
        chunk = []
        def flush(last):
            d = {K("LIST_KIND"): Uint8(kind), K("LIST_TOTAL"): Uint16(len(rows)), K("LIST_START"): Uint16(start)}
            if chunk:
                d[K("LIST_ROWS")] = ByteArray(pack_rows(chunk))
            self.send(d)
        for r in window:
            chunk.append(r)
            if len(pack_rows(chunk)) > 1500:
                chunk.pop()
                flush(False)
                chunk = [r]
        flush(True)

    def lyrics(self):
        i = self.s["lyric"]
        self.send({K("LYRIC_PREV"): CString(LYRICS[i - 1] if i > 0 else ""), K("LYRIC_CUR"): CString(LYRICS[i]),
                   K("LYRIC_NEXT"): CString(LYRICS[i + 1] if i + 1 < len(LYRICS) else ""), K("LYRIC_STATE"): Uint8(self.s["lyric_state"])})

    # ---- receiving ----
    def on_message(self, txid, uuid, d):
        if uuid != APP_UUID or not self.alive:
            return
        cmd = d.get(K("CMD"))
        arg = d.get(K("ARG"), 0)
        arg2 = d.get(K("ARG2"), 0)
        text = d.get(K("TEXT"))
        if isinstance(text, bytes):
            text = text.rstrip(b"\0").decode("utf-8", "replace")
        print("watch:", cmd, arg, arg2, text or "", flush=True)
        s = self.s
        if cmd == CMD_HELLO:
            self.push_now(all_=True)
        elif cmd == CMD_PLAY_PAUSE:
            s["state"] = 2 if s["state"] == 1 else 1
            self.send(self.now_dict())
        elif cmd in (CMD_NEXT, CMD_PREV, CMD_SCAN_TO, CMD_PLAY_ITEM, CMD_PLAY_RANDOM, CMD_LISTEN):
            if cmd == CMD_PLAY_RANDOM and not s["station"]:
                self.scene("playing")
                self.push_now()
                return
            s["pos"] = (s["pos"] + (1 if cmd == CMD_NEXT else -1)) % len(SAVED) if cmd in (CMD_NEXT, CMD_PREV) else (arg if cmd == CMD_SCAN_TO else s["pos"])
            if cmd != CMD_LISTEN:
                s["station"] = SAVED[s["pos"]]["name"]
            s["state"] = 1
            self.send(self.now_dict())
        elif cmd == CMD_STAR:
            s["flags"] ^= 0x01
            self.send(self.now_dict())
        elif cmd == CMD_SHUFFLE:
            s["flags"] ^= 0x02
            self.send(self.now_dict())
        elif cmd == CMD_SONG_ID:
            s["flags"] ^= 0x04
            self.send(self.now_dict())
        elif cmd == CMD_SAVE and arg == 0:
            s["flags"] |= 0x20
            self.send(self.now_dict())
        elif cmd == CMD_SLEEP_SET:
            s["sleep_left"] = arg * 60
            self.send(self.now_dict())
        elif cmd == CMD_GET_LIST:
            rows = {LIST_SAVED: SAVED, LIST_FAVORITES: FAVS, LIST_HISTORY: HISTORY, LIST_PREV_FEATURED: PREV_FEATURED,
                    LIST_THEMES: THEMES, LIST_COVERS: COVERS}.get(arg)
            if arg in (LIST_TAGS, LIST_PICK_TAG):
                self.send({K("TAGS"): ByteArray(pack_tags(TAGS))})
            elif rows is not None:
                self.send_list(arg, arg2, rows)
        elif cmd == CMD_VOLUME:
            if arg >= 0:
                s["vol"] = max(0, min(15, arg))
            self.send({K("VOLUME"): ByteArray(bytes([s["vol"], 15]))})
        elif cmd == CMD_STATION:
            f, v = arg, arg2
            if f == 0: ST[1] = v + 40
            elif f in (1, 5, 7):
                bit = {1: 0x04, 5: 0x10, 7: 0x20}[f]
                ST[0] = (ST[0] | bit) if v else (ST[0] & ~bit)
            elif f == 2: ST[2] = v
            elif f == 3: ST[0] &= ~0x08
            elif f == 4: ST[0] |= 0x08; s["cover_name"] = next((c["name"] for i, c in enumerate(COVERS) if i + 1 == v), "")
            elif f == 6: ST[3] = v
            elif f == 8: ST[5:18] = [12] * 13
            elif 10 <= f < 13: ST[5 + f - 10] = v + 12
            elif 20 <= f < 30: ST[8 + f - 20] = v + 12
            self.send({K("STATION_SET"): ByteArray(bytes(ST)), K("STATION_COVER"): CString(s.get("cover_name", ""))})
        elif cmd == CMD_SEARCH:
            self.send_list(LIST_SEARCH, 0, SEARCH if arg != 0 else SEARCH[:2])
        elif cmd == CMD_GET_FEATURED:
            self.send_list(LIST_FEATURED, 0, FEATURED)
            for i, img in enumerate([sunset(80), waves(80), grid(80), waves(80, (85, 0, 0))]):
                self.art(ART_FEATURED, 100 + i, img, slot=i)
        elif cmd == CMD_SET_TAG:
            if arg < len(TAGS):
                TAGS[arg]["state"] = arg2
            self.send({K("TAGS"): ByteArray(pack_tags(TAGS))})
        elif cmd == CMD_GET_BIO:
            self.send({K("BIO_NAME"): CString("Neon Static"), K("BIO_FROM"): CString("Wikipedia"), K("BIO_TEXT"): CString(BIO),
                       K("VERDICT"): Uint8(0)})
            self.art(ART_PHOTO, 3, portrait(116))
        elif cmd == CMD_THUMB_DOWN:
            self.send({K("VERDICT"): Uint8(2), K("THUMBS_LEFT"): Uint8(15), K("THUMBS_WHAT"): Uint8(arg)})
        elif cmd == CMD_THUMB_UNDO:
            self.send({K("VERDICT"): Uint8(0), K("THUMBS_LEFT"): Uint8(0)})
        elif cmd == CMD_LYRICS and arg:
            self.lyrics()
        elif cmd == CMD_LYRIC_STEP:
            s["lyric"] = max(0, min(len(LYRICS) - 1, s["lyric"] + (1 if arg > 0 else -1)))
            self.lyrics()
        elif cmd == CMD_LYRIC_HOLD:
            s["lyric_state"] = 2 if arg else 1
            self.lyrics()
        elif cmd == CMD_LYRIC_SYNC:
            s["lyric_state"] = 1
            self.lyrics()


def control(phone):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 7777))
    srv.listen(1)
    while True:
        c, _ = srv.accept()
        line = c.makefile().readline().strip()
        parts = line.split()
        if parts and parts[0] == "scene":
            phone.scene(parts[1])
            phone.push_now()
        elif parts and parts[0] == "saved":
            n = int(parts[1])
            names = ["Nightwave Plaza", "Groove Salad", "Radio Paradise", "KEXP", "Lofi Lagoon", "NTS", "WFMU", "Dublab", "Fip", "Kiosk"]
            SAVED[:] = [dict(name="%s %d" % (names[i % len(names)], i + 1), tags=[i % 4]) for i in range(n)]
            SAVED[0]["flags"] = RF_PLAYING
            phone.s["count"] = n
            phone.push_now()
        elif line == "unsaved":
            phone.s["flags"] &= ~0x20
            phone.send(phone.now_dict())
        elif line == "nostar":
            phone.s["flags"] ^= 0x100
            phone.send(phone.now_dict())
        elif line in ("eq10", "eq3"):
            ST[0] = (ST[0] | 0x40) if line == "eq10" else (ST[0] & ~0x40)
            ST[8:18] = [12, 15, 17, 14, 12, 10, 9, 11, 13, 16] if line == "eq10" else ST[8:18]
            phone.send({K("STATION_SET"): ByteArray(bytes(ST)), K("STATION_COVER"): CString(phone.s.get("cover_name", ""))})
        elif line == "dead":
            phone.alive = False
        elif line == "alive":
            phone.alive = True
        elif line == "push":
            phone.push_now()
        elif line == "lyrics":
            phone.lyrics()
        c.sendall(b"ok\n")
        c.close()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else None
    if not url:
        info = json.load(open("/tmp/pb-emulator.json"))
        plat = os.environ.get("PLATFORM", "emery")
        ver = sorted(info[plat].keys())[-1]
        url = "ws://localhost:%d/" % info[plat][ver]["pypkjs"]["port"]
    phone = Phone(url)
    print("fake phone ready on", url, flush=True)
    control(phone)
