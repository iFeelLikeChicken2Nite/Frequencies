#!/usr/bin/env python3
"""Copy the developer's talk stations into the build (task 49).

Run by build-appimage-fedora.sh before it copies src/ into the AppImage,
so nobody edits JSON by hand:

  your dev build saved   ~/.config/frequencies/dev_talk_stations.json
                         ~/.config/frequencies/talk_photos/*
  this writes            src/station_overrides.json   (talk entries only)
                         src/talk_photos/*            (their photos)

Only entries with the talk switch on are shipped. Nothing else from your
config folder is read - your own artist corrections stay yours. Entries in
src/station_overrides.json that are not talk stations are left as they
are. No dev list yet (a new machine): src/ is left as it is.

Stand-alone on purpose (no app imports): it runs inside the build box.
"""

import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "src")
SHIPPED = os.path.join(SRC, "station_overrides.json")
PHOTOS = "talk_photos"


def config_dir():
    if os.environ.get("FREQUENCIES_DEV_CONFIG"):
        # build-windows.ps1's copy (WP3): the Store's Python reads its own
        # private AppData, not the folder the dev build writes.
        return os.environ["FREQUENCIES_DEV_CONFIG"]
    if sys.platform == "win32":
        # Windows (WP3): the dev build's Settings, as appdirs.py has it.
        base = os.environ.get("APPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "frequencies")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return os.path.join(base, "frequencies")


def read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def main(argv):
    dev_path = os.environ.get("FREQ_DEV_TALK_FILE") or os.path.join(
        config_dir(), "dev_talk_stations.json")
    shipped = read(SHIPPED) or {}
    dev = read(dev_path)
    if dev is None:
        talk = [k for k, v in shipped.items()
                if isinstance(v, dict) and v.get("talk")]
        print(f"    no dev talk list ({dev_path}); shipping the "
              f"{len(talk)} talk station(s) already in src/")
        return 0

    out = {k: v for k, v in shipped.items()
           if isinstance(v, dict) and "talk" not in v}
    photo_dir = os.path.join(SRC, PHOTOS)
    wanted_photos = set()
    names = []
    for key, entry in sorted(dev.items()):
        if not isinstance(entry, dict) or not entry.get("talk"):
            continue
        entry = dict(entry)
        photo = (entry.get("photo") or "").strip()
        if photo and not photo.startswith(("http://", "https://")):
            source = photo if os.path.isabs(photo) else os.path.join(
                config_dir(), PHOTOS, os.path.basename(photo))
            if not os.path.exists(source):
                # Copied over from another PC (WP3: a Linux path on
                # Windows), so it sits in this config folder's photos.
                source = os.path.join(config_dir(), PHOTOS,
                                      os.path.basename(photo))
            if not os.path.exists(source):
                # Already shipped from an earlier build?
                source = os.path.join(photo_dir, os.path.basename(photo))
            if os.path.exists(source):
                base = os.path.basename(source)
                if os.path.isabs(photo):
                    slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
                    base = (slug or "station") + os.path.splitext(base)[1]
                os.makedirs(photo_dir, exist_ok=True)
                target = os.path.join(photo_dir, base)
                if os.path.abspath(source) != os.path.abspath(target):
                    shutil.copyfile(source, target)
                wanted_photos.add(base)
                entry["photo"] = PHOTOS + "/" + base
            else:
                print(f"!!  photo for '{key}' not found ({photo}); "
                      f"shipping it without one")
                entry.pop("photo", None)
        out[key] = entry
        names.append(key)

    # Photos of stations no longer shipped go.
    if os.path.isdir(photo_dir):
        for name in os.listdir(photo_dir):
            if name not in wanted_photos:
                os.remove(os.path.join(photo_dir, name))

    with open(SHIPPED + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(SHIPPED + ".tmp", SHIPPED)
    print(f"    talk stations shipped: {len(names)}"
          + (f" ({', '.join(names)})" if names else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
