"""Frequencies — making the photo pack and the bio fixes file (build 95).

Used two ways, so it imports nothing but the standard library:
  - tools/make_photo_pack.py, at build time (inside the build box);
  - the dev build's "Publish my photos / bio fixes to GitHub" buttons
    (ghpublish.py), which upload straight to the pack's release.

Two files go up, beside photo-pack.json:
  frequencies-photo-pack.b32  the photos (artists' and talk stations'),
                              a zip written in base32 twice (the user's
                              choice: nothing on GitHub looks like a photo)
  bio-fixes.json              the text fixes (bio, name, genres, MusicBrainz
                              id, Wikipedia title); every user's app fetches
                              it, no opt-in (photopack.BioUpdate)

Since build 141 (task 203) the band logos go up beside them, as a pack of
their own made the same way (frequencies-logo-pack.b32 + logo-pack.json):
builds before 141 never ask for those files, and every user's app gets
them (on by default), not only those who opted in to the photos.
"""

import base64
import hashlib
import io
import json
import os
import re
import time
import zipfile

PACK_NAME = "frequencies-photo-pack.b32"
MANIFEST = "photo-pack.json"
BIO_NAME = "bio-fixes.json"

TEXT_FIELDS = ("name", "genres", "mbid", "wikipedia", "bio")
PHOTO_FIELDS = ("photo", "photo_source", "photo_url")
LOGO_NAME = "frequencies-logo-pack.b32"
LOGO_MANIFEST = "logo-pack.json"
# A logo's file and how the developer placed it (bandlogo.py).
LOGO_FIELDS = ("logo", "logo_scale", "logo_x", "logo_y")
STAMP = (1980, 1, 1, 0, 0, 0)       # same bytes for the same pack


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "artist"


def text_fixes(dev):
    """{artist: {text fields}} from the developer's corrections."""
    out = {}
    for key, entry in (dev or {}).items():
        if not isinstance(entry, dict):
            continue
        text = {k: str(entry[k]).strip() for k in TEXT_FIELDS
                if str(entry.get(k) or "").strip()}
        if text:
            out[key.strip().lower()] = text
    return out


def bio_fixes(dev):
    """(bytes of bio-fixes.json, number of artists, checksum of the fixes).
    The checksum leaves out the date, so unchanged fixes keep it."""
    fixes = text_fixes(dev)
    body = json.dumps(fixes, indent=2, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    data = json.dumps({"format": 1, "made": time.strftime("%Y-%m-%d"),
                       "sha256": digest, "artists": fixes},
                      indent=2, ensure_ascii=False, sort_keys=True)
    return data.encode("utf-8") + b"\n", len(fixes), digest


def missing_photos(dev):
    """Artists whose photo is a file this computer doesn't have (WP3: a
    Linux path in corrections copied to Windows). A pack made here would
    drop their photos, so it must not replace the published one."""
    missing = []
    for key, entry in sorted((dev or {}).items()):
        photo = entry.get("photo") if isinstance(entry, dict) else None
        if isinstance(photo, str) and photo.strip() \
                and not photo.strip().startswith(("http://", "https://")) \
                and not os.path.isfile(photo.strip()):
            missing.append(key)
    return missing


def missing_logos(dev):
    """Artists whose logo is a file this computer doesn't have."""
    return [key for key, entry in sorted((dev or {}).items())
            if isinstance(entry, dict)
            and isinstance(entry.get("logo"), str) and entry["logo"].strip()
            and not os.path.isfile(entry["logo"].strip())]


def _encoded(files):
    """The zip of `files` (name -> bytes), base32 twice."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as pack:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, STAMP)
            info.external_attr = 0o644 << 16
            info.compress_type = (zipfile.ZIP_DEFLATED
                                  if name.endswith(".json")
                                  else zipfile.ZIP_STORED)
            pack.writestr(info, files[name])
    return base64.b32encode(base64.b32encode(buffer.getvalue()))


def logo_pack(dev, warn=print):
    """(encoded pack, manifest) of the band logos, or (None, None) when
    there are none (build 141, task 203). Each entry keeps the size and
    place the developer gave it, and the "Show as" name."""
    files, artists = {}, {}
    for key, entry in sorted((dev or {}).items()):
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("logo") or "").strip()
        if not path:
            continue
        key = key.strip().lower()
        if not os.path.isfile(path):
            warn(f"!!  logo for '{key}' not found ({path}); packed "
                 f"without it")
            continue
        name = "logos/" + slug(key) + (os.path.splitext(path)[1].lower()
                                       or ".png")
        with open(path, "rb") as f:
            files[name] = f.read()
        out = {"logo": name}
        for field in ("logo_scale", "logo_x", "logo_y"):
            try:
                out[field] = round(float(entry[field]), 3)
            except (KeyError, TypeError, ValueError):
                pass
        if str(entry.get("name") or "").strip():
            out["name"] = str(entry["name"]).strip()
        artists[key] = out
    if not files:
        return None, None
    files["pack.json"] = json.dumps(
        {"format": 1, "artists": artists}, indent=2, ensure_ascii=False,
        sort_keys=True).encode("utf-8")
    data = _encoded(files)
    manifest = {"format": 1, "file": LOGO_NAME, "encoding": "base32x2",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "made": time.strftime("%Y-%m-%d"),
                "logos": len(artists), "artists": len(artists),
                "names": sorted(artists)}
    return data, manifest


def photo_pack(dev, talk_photos, warn=print):
    """(encoded pack, manifest) or (None, None) when there are no photos.

    `dev` is the developer's artist corrections (photo = a file or a link);
    `talk_photos` maps a talk station photo's file name to its path.
    """
    files = {}                          # name in the zip -> bytes
    artists = {}
    for key, entry in sorted((dev or {}).items()):
        if not isinstance(entry, dict):
            continue
        key = key.strip().lower()
        entry = {k: str(entry[k]).strip() for k in PHOTO_FIELDS
                 if isinstance(entry.get(k), str) and entry[k].strip()}
        photo = entry.get("photo", "")
        if photo and not photo.startswith(("http://", "https://")):
            if os.path.isfile(photo):
                ext = os.path.splitext(photo)[1].lower() or ".jpg"
                name = "photos/" + slug(key) + ext
                with open(photo, "rb") as f:
                    files[name] = f.read()
                entry["photo"] = name
            else:
                warn(f"!!  photo for '{key}' not found ({photo}); "
                     f"packed without one")
                entry.pop("photo", None)
        if entry.get("photo"):
            artists[key] = entry
    talk = 0
    for name, path in sorted((talk_photos or {}).items()):
        if os.path.isfile(path):
            with open(path, "rb") as f:
                files["talk_photos/" + name] = f.read()
            talk += 1
    photos = len(files)
    if not photos:
        return None, None
    files["pack.json"] = json.dumps(
        {"format": 1, "artists": artists}, indent=2, ensure_ascii=False,
        sort_keys=True).encode("utf-8")
    data = _encoded(files)
    manifest = {"format": 1, "file": PACK_NAME, "encoding": "base32x2",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "made": time.strftime("%Y-%m-%d"),
                "photos": photos, "talk_photos": talk,
                "artists": len(artists),
                # Which ones, for the dev build's "not published yet"
                # (task 194).
                "names": sorted(artists)}
    return data, manifest


def manifest_bytes(manifest):
    return (json.dumps(manifest, indent=2, ensure_ascii=False,
                       sort_keys=True) + "\n").encode("utf-8")
