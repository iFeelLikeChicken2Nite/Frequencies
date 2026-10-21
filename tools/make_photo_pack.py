#!/usr/bin/env python3
"""Make the artist photo pack, apart from the app (build 91, task 117).

Run by build-appimage-fedora.sh after tools/ship_talk_stations.py:

  your dev build saved   ~/.config/frequencies/artist_overrides.json
                         (what "Edit this artist…" wrote: bio, name,
                         genres, photo, photo credit…)
                         ~/.config/frequencies/photo_pack.json
                         (the pack and report addresses, Settings)
  ship_talk_stations     src/talk_photos/* (talk station photos)

  this writes            src/artist_overrides.json  your text fixes (bio,
                                                    name, genres, mbid,
                                                    Wikipedia) - no photos
                         src/photo_pack.json        the addresses (pack,
                                                    reports, art)
                         photo-pack/frequencies-photo-pack.b32
                         photo-pack/photo-pack.json
                         photo-pack/bio-fixes.json  your text fixes, which
                                                    every user's app fetches
                                                    (build 95)

The uploaded pack is the zip written out in base32, twice (the user's
choice, 2026-10-15): nobody who opens it on GitHub sees a photo, and no
image search finds them. It's an encoding, not a lock - the app undoes it
after the download - and it makes the file about 2.6 times bigger.

The build ships no photo files. Upload the files in photo-pack/ to where
the pack address points (the same GitHub release each time, as release
assets with those names) - or, since build 95, press "Publish my photos to
GitHub" / "Publish my bio fixes to GitHub" in the dev build's Settings,
which send the same files there itself. The zip is made the same way each time,
so an unchanged pack keeps its checksum and nobody downloads it again.

Stand-alone on purpose: it runs inside the build box. The one app file
it uses, src/packbuild.py, needs nothing but Python.
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "src")
SHIPPED_FIXES = os.path.join(SRC, "artist_overrides.json")
SHIPPED_ADDRESS = os.path.join(SRC, "photo_pack.json")
TALK_PHOTOS = os.path.join(SRC, "talk_photos")
OUT = os.path.join(HERE, "photo-pack")
ZIP_NAME = "frequencies-photo-pack.b32"   # the zip, base32 twice (build 93)
MANIFEST = "photo-pack.json"

TEXT_FIELDS = ("name", "genres", "mbid", "wikipedia", "bio")
PHOTO_FIELDS = ("photo", "photo_source", "photo_url")


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


def write_json(path, data):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    os.replace(path + ".tmp", path)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "artist"


def ship_addresses():
    own = read(os.path.join(config_dir(), "photo_pack.json")) or {}
    own = {k: str(own.get(k) or "").strip()
           for k in ("pack", "reports", "art")}
    own = {k: v for k, v in own.items() if v}
    if not own:
        have = read(SHIPPED_ADDRESS) or {}
        print("    no pack address in your dev build's Settings; "
              + ("shipping the one already in src/" if have.get("pack")
                 else "this build can't download the photo pack"))
        return
    write_json(SHIPPED_ADDRESS, own)
    print("    pack address: " + own.get("pack", "(none)"))
    print("    report address: " + ("set" if own.get("reports")
                                    else "(none: thumbs down uses the pack address's repository)"))
    print("    picture link address: " + (own.get("art") or "(none: shared "
                                          "pictures of your own travel as thumbnails)"))


def ship_text(dev):
    """Your text fixes into the build; photos never."""
    shipped = read(SHIPPED_FIXES) or {}
    out = {}
    for key, value in shipped.items():
        if isinstance(value, dict):
            value = {k: v for k, v in value.items()
                     if k not in PHOTO_FIELDS}
            if value:
                out[key] = value
        else:
            out[key] = value                # "_comment"
    count = 0
    for key, entry in dev.items():
        if not isinstance(entry, dict):
            continue
        text = {k: str(entry[k]).strip() for k in TEXT_FIELDS
                if str(entry.get(k) or "").strip()}
        if text:
            out[key.strip().lower()] = text
            count += 1
    write_json(SHIPPED_FIXES, out)
    print(f"    artist text fixes shipped in the build: {count} from your "
          f"dev build")


def make_pack(dev):
    """photo-pack/: the photos (base32 twice) with photo-pack.json, and
    bio-fixes.json - the same files the dev build's Publish buttons send
    to GitHub (packbuild.py makes them both ways)."""
    sys.path.insert(0, SRC)
    import packbuild
    talk = {}
    if os.path.isdir(TALK_PHOTOS):
        talk = {name: os.path.join(TALK_PHOTOS, name)
                for name in os.listdir(TALK_PHOTOS)}
    os.makedirs(OUT, exist_ok=True)
    old_zip = os.path.join(OUT, "frequencies-photo-pack.zip")
    if os.path.exists(old_zip):
        os.remove(old_zip)          # build 91's plain zip, not uploaded now

    bios, count, digest = packbuild.bio_fixes(dev)
    bio_path = os.path.join(OUT, packbuild.BIO_NAME)
    same_bios = (read(bio_path) or {}).get("sha256") == digest
    if not same_bios:
        with open(bio_path, "wb") as f:
            f.write(bios)
    print(f"    bio fixes: {count} artist(s)"
          + (" - unchanged" if same_bios else
             f" - NEW: upload {packbuild.BIO_NAME} (or press Publish my "
             f"bio fixes to GitHub in your dev build)"))

    missing = packbuild.missing_photos(dev)
    data, manifest = packbuild.photo_pack(dev, talk)
    if data is None:
        print("    no photos for a photo pack yet (no artist or talk "
              "station photos in your dev build)")
        return
    if missing:
        # WP3: corrections copied from another PC point at photos that
        # stayed there. A pack made here would drop them: none is written.
        print(f"!!  {len(missing)} artist photos aren't on this computer, "
              f"so no photo pack is made here (uploading one would take "
              f"them out of the published pack). Publish photos from the "
              f"computer that has them.")
        return
    old = read(os.path.join(OUT, MANIFEST)) or {}
    same = old.get("sha256") == manifest["sha256"]
    if same:
        manifest["made"] = old.get("made", manifest["made"])
    with open(os.path.join(OUT, ZIP_NAME), "wb") as f:
        f.write(data)
    with open(os.path.join(OUT, MANIFEST), "wb") as f:
        f.write(packbuild.manifest_bytes(manifest))
    print(f"    photo pack: {manifest['photos'] - manifest['talk_photos']} "
          f"artist photo(s), {manifest['talk_photos']} talk station "
          f"photo(s), {len(data) // 1024} KB")
    print("    " + ("unchanged since the last build (no need to upload it "
                    "again)" if same else
                    "NEW: upload " + ZIP_NAME + " and " + MANIFEST
                    + " (or press Publish my photos to GitHub in your dev "
                    "build)"))


def main(argv):
    ship_addresses()
    dev = read(os.environ.get("FREQ_DEV_ARTIST_FILE") or os.path.join(
        config_dir(), "artist_overrides.json")) or {}
    if dev:
        ship_text(dev)
    else:
        print("    no artist fixes in your dev build; the build's text "
              "fixes are left as they are")
    make_pack(dev)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
