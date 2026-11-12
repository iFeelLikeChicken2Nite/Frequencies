#!/usr/bin/env python3
"""Answers from the desktop's own artist code, for core's ArtistTest.

    python3 -I tools/make_artist_fixtures.py <desktop source>/src core/src/test/resources/artist

Writes a real photo pack (packbuild.photo_pack: the zip in base32 twice,
photo-pack.json, bio-fixes.json) from made-up photos, and answers.json:
find_artist / artist_keys / _loose_artist / _norm_stream / talk_entry /
format_span / looks_musical / _issue_text / _issue_address for a list of
inputs. PyQt5 isn't needed: photopack's Qt names are stubbed.
"""
import json
import os
import sys
import tempfile
import types

src, out = sys.argv[1], sys.argv[2]
sys.path.insert(0, src)

# photopack and jobs import PyQt5.QtCore: stand-ins are enough here.
qt = types.ModuleType("PyQt5")
core = types.ModuleType("PyQt5.QtCore")
class _Any:
    def __init__(self, *a, **k): pass
    def __getattr__(self, name): return _Any()
    def __call__(self, *a, **k): return _Any()
core.QObject = _Any
core.QThread = _Any
core.QTimer = _Any
core.pyqtSignal = lambda *a, **k: _Any()
core.pyqtSlot = lambda *a, **k: (lambda f: f)
qt.QtCore = core
qt.sip = types.ModuleType("PyQt5.sip")
sys.modules["PyQt5.sip"] = qt.sip
sys.modules["PyQt5"] = qt
sys.modules["PyQt5.QtCore"] = core
if "requests" not in sys.modules:
    try:
        import requests  # noqa: F401
    except ImportError:
        sys.modules["requests"] = types.ModuleType("requests")

jobs = types.ModuleType("jobs")
jobs.Job = _Any
sys.modules["jobs"] = jobs
import packbuild
import storage
import photopack
import artistinfo

os.makedirs(out, exist_ok=True)
tmp = tempfile.mkdtemp()


def made(name, data):
    path = os.path.join(tmp, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


# Not real pictures: the pack carries bytes, whatever they are.
dev = {
    "eminem": {"photo": made("eminem.jpg", b"\xff\xd8\xff eminem" * 50),
               "photo_source": "Photo: Somebody", "photo_url": "https://example.com/eminem"},
    "barenaked ladies": {"photo": made("bnl.png", b"\x89PNG bnl" * 70), "bio": "A Canadian band."},
    "dracula": {"wikipedia": "Dracula (band)", "genres": "rock"},
    "beyoncé": {"photo": "https://example.com/b.jpg", "name": "Beyoncé"},
}
talk = {"kbem.jpg": made("kbem.jpg", b"\xff\xd8\xff kbem" * 30)}
data, manifest = packbuild.photo_pack(dev, talk, warn=lambda *_: None)
manifest["made"] = "2026-10-29"
with open(os.path.join(out, packbuild.PACK_NAME), "wb") as f:
    f.write(data)
with open(os.path.join(out, packbuild.MANIFEST), "wb") as f:
    f.write(packbuild.manifest_bytes(manifest))
bios, _count, _digest = packbuild.bio_fixes(dev)
with open(os.path.join(out, packbuild.BIO_NAME), "wb") as f:
    f.write(bios)

credits = ["Eminem", "EMINEM", "Eminem feat. Dido", "Eminem & Rihanna", "Eminem (with Ed Sheeran)",
           "The Barenaked Ladies", "Barenaked Ladies x Someone", "Beyonce", "Beyoncé, Jay-Z",
           "AC/DC", "Nobody Here", "dracula", "  Dracula  ", "Eminem ft Dido", "Eminem vs. Moby",
           "Simon and Garfunkel", "Earth, Wind & Fire",
           # Unicode: Python's \w, \s and \b count these letters and spaces.
           "Björk feat. Ólafur Arnalds", "Sigur Rós", "Röyksopp\u00a0&\u00a0Robyn", "Andréx Bob",
           "Joséand Maria", "Zé x Ana", "Ólafur Arnalds and Nils Frahm", "Mötley Crüe vs.\u00a0Ratt",
           "坂本龍一 feat. David Sylvian", "Café Tacvba (featuring Ely Guerra)", "Ñu\u2003x\u2003Ébano",
           "Ärzte – Live!"]
table = {k: v for k, v in storage.load_bundled_artist_overrides().items()}
table.update({k.lower(): v for k, v in dev.items()})
table["earth wind and fire"] = {}
table["simon & garfunkel"] = {}
talk_entries = {
    "jazz 88.5 kbem": {"talk": True, "stream": "https://kbem-live.streamguys1.com/kbem_mp3;", "photo": "talk_photos/kbem.jpg"},
    "npr news": {"talk": True},
    "plain correction": {"bio": "Typed in."},
}
stations = [
    {"name": "Anything", "url": "http://KBEM-LIVE.streamguys1.com/kbem_mp3/"},
    {"name": "NPR News", "url": "http://other/"},
    {"name": "x", "custom_name": "npr news", "url": "http://other2/"},
    {"name": "Nope", "url": "http://nope/"},
]
answers = {
    "find_artist": {c: list(storage.find_artist(table, c)) for c in credits},
    "artist_keys": {c: [list(k) for k in storage.artist_keys(c)] for c in credits},
    "loose": {c: storage._loose_artist(c) for c in credits},
    "table": sorted(table),
    "norm_stream": {u: storage._norm_stream(u) for u in [
        "https://KBEM-live.streamguys1.com/kbem_mp3;", "http://a.b/c/", "a.B/", "", "HTTP://X.Y/Path/To;/"]},
    "talk_entries": talk_entries,
    "talk_entry": [[s, list(storage.talk_entry(s, talk_entries))] for s in stations],
    "format_span": {r: artistinfo.format_span(r) for r in ["1979-02-15", "1979", "1979-13", "79", "", "2001-07"]},
    "looks_musical": [[d, s, artistinfo.looks_musical(d, s)] for d, s in [
        ("male ruler", "Prince is a title."), ("American musician (1958-2016)", ""),
        ("", "Muse are an English rock band."), ("river in Europe", "A river."), ("", "x" * 500 + " band")]],
}
photopack.time.strftime = lambda fmt, *a: "2026-10-29 12:00" if "%H" in fmt else "2026-10-29"
issues = []
for what in ("bio", "photo", "both", "nonsense"):
    title, body = photopack._issue_text("Dracula", "Biography from Wikipedia", "B" * 250, what, "photo pack, photos/dracula.jpg")
    issues.append({"what": what, "title": title, "body": body,
                   "address": photopack._issue_address("https://github.com/someone/frequencies-reports", title, body)})
answers["issues"] = issues
with open(os.path.join(out, "answers.json"), "w", encoding="utf-8") as f:
    json.dump(answers, f, indent=2, ensure_ascii=False)
print("wrote", out)
