"""Read the phone's backup (core/build/phone-backup.json, written by
BackupTest) with the desktop's own storage.read_backup, and compare it with
the desktop backup it started from.

    python3 tools/check_backup_on_desktop.py <desktop source folder>/src
"""
import base64
import json
import os
import sys
import tempfile

src = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, src)
import storage  # noqa: E402

phone = os.path.join(here, "..", "core", "build", "phone-backup.json")
start = os.path.join(here, "..", "core", "src", "test", "resources", "desktop", "desktop-backup.json")
stations, settings, health, history, artists, talk = storage.read_backup(phone)
before = json.load(open(start, encoding="utf-8"))
art = before["artwork"]
problems = 0
for old, new in zip(before["stations"], stations):
    for key in set(old) | set(new):
        if key != "custom_art_path" and old.get(key) != new.get(key):
            problems += 1
            print("differs:", old.get("name"), key, old.get(key), new.get(key))
    with open(new["custom_art_path"], "rb") as f:
        if f.read() != base64.b64decode(art[old["id"]]["data"]):
            problems += 1
            print("art differs:", old.get("name"))
    if not new["custom_art_path"].startswith(os.environ["XDG_CONFIG_HOME"]):
        problems += 1
        print("art path not rewritten:", new["custom_art_path"])
for name, got in (("settings", settings), ("history", history), ("artist_overrides", artists)):
    if before[name] != got:
        problems += 1
        print("differs:", name)
print(f"{len(stations)} stations read by the desktop, {problems} problems")
sys.exit(1 if problems else 0)
