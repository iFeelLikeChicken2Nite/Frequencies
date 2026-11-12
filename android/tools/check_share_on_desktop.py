"""Read what the phone's core tests wrote (core/build/phone-share.zip and
phone-share-code.txt) with the desktop's own sharezip.read and
storage.station_from_code.

    python3 tools/check_share_on_desktop.py <desktop source>/src
"""
import os
import sys
import tempfile

here = os.path.dirname(os.path.abspath(__file__))
build = os.path.join(here, "..", "core", "build")
sys.path.insert(0, sys.argv[1])
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import sharezip  # noqa: E402
import storage  # noqa: E402

stations = sharezip.read(os.path.join(build, "phone-share.zip"), tempfile.mkdtemp())
names = [storage.station_display_name(s) for s in stations]
print("zip:", names, [s.get("tags") for s in stations],
      ["art" if s.get("custom_art_path") else "no art" for s in stations])
assert names == ["KXCI 91.3", "Two"], names
assert stations[0]["tags"] == ["community", "Jazz"]
assert os.path.isfile(stations[0]["custom_art_path"])

with open(os.path.join(build, "phone-share-code.txt")) as f:
    code = f.read()
station = storage.station_from_code(code)
print("code:", storage.station_display_name(station), station.get("tags"), station.get("play_count"), station.get("gain_db"))
assert storage.station_display_name(station) == "KXCI 91.3"
assert station["play_count"] == 3 and station["gain_db"] == 2.5
print("desktop reads the phone's share zip and code: OK")
