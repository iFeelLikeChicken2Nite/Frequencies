#!/usr/bin/env python3
"""Fetch projectM 4 for the Windows build (WP2).

Run by build-windows.ps1 with the build's own Python. projectM has no
Windows download of its own, so this takes MSYS2's packages (the
mingw64 builds: projectM, GLEW and the MinGW runtime it needs) and keeps
only their DLLs:

    build-windows\\projectm\\libprojectM-4-4.dll   (+ the DLLs it needs)

then loads it once to prove nothing is missing. Also fetches the
"cream of the crop" preset pack, as the Linux build does:

    build-windows\\presets\\*.milk

Each part is skipped when it is already there. Nothing here is fatal:
without it the visualizer just says projectM is missing.
"""

import ctypes
import io
import os
import re
import shutil
import sys
import tarfile
import zipfile
from urllib.parse import quote

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(HERE, "build-windows")
DLLS = os.path.join(WORK, "projectm")
PRESETS = os.path.join(WORK, "presets")
REPO = "https://repo.msys2.org/mingw/mingw64/"
# MSYS2's names. gcc-libs is only a meta package now: the MinGW runtime
# DLLs are in libgcc and libstdc++.
PACKAGES = ("libprojectm", "glew", "libgcc", "libstdc++", "libwinpthread")
PRESET_ZIP = ("https://github.com/projectM-visualizer/presets-cream-of-the-"
              "crop/archive/refs/heads/master.zip")
LIBRARY = "libprojectM-4-4.dll"      # MSYS2 adds the soversion


def _version_key(version):
    return [int(n) for n in re.findall(r"\d+", version)]


def _newest(listing, name):
    # The listing's links are URL-encoded (libstdc++ is libstdc%2B%2B).
    pattern = re.compile(r'href="(mingw-w64-x86_64-' + re.escape(quote(name))
                         + r'(?:-git)?-(\d[^"]*?)-any\.pkg\.tar\.zst)"')
    found = pattern.findall(listing)
    if not found:
        return None
    return max(found, key=lambda item: _version_key(item[1]))[0]


def _unpack_dlls(data):
    import zstandard
    raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data))
    count = 0
    with tarfile.open(fileobj=raw, mode="r|") as archive:
        for member in archive:
            if (member.isfile() and member.name.startswith("mingw64/bin/")
                    and member.name.lower().endswith(".dll")):
                target = os.path.join(DLLS, os.path.basename(member.name))
                with open(target, "wb") as out:
                    shutil.copyfileobj(archive.extractfile(member), out)
                count += 1
    return count


def fetch_projectm():
    library = os.path.join(DLLS, LIBRARY)
    if not os.path.exists(library):
        os.makedirs(DLLS, exist_ok=True)
        listing = requests.get(REPO, timeout=60).text
        for name in PACKAGES:
            package = _newest(listing, name)
            if package is None:
                print(f"    no {name} package in MSYS2's list")
                continue
            print(f"    downloading {package}")
            data = requests.get(REPO + package, timeout=300).content
            print(f"    {_unpack_dlls(data)} DLL(s) from it")
    if not os.path.exists(library):
        print(f"    {LIBRARY} wasn't in the download")
        return False
    os.add_dll_directory(DLLS)
    try:
        loaded = ctypes.CDLL(library)
        loaded.projectm_create
    except (OSError, AttributeError) as error:
        print(f"    {LIBRARY} doesn't load: {error}")
        return False
    print(f"    {LIBRARY} loads")
    return True


def fetch_presets():
    if os.path.isdir(PRESETS) and any(
            n.lower().endswith(".milk") for n in os.listdir(PRESETS)):
        print(f"    {len(os.listdir(PRESETS))} presets already here")
        return
    print("    downloading the preset pack")
    data = requests.get(PRESET_ZIP, timeout=600).content
    os.makedirs(PRESETS, exist_ok=True)
    count = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if name.lower().endswith(".milk"):
                target = os.path.join(PRESETS, os.path.basename(name))
                with open(target, "wb") as out:
                    out.write(archive.read(name))
                count += 1
    print(f"    bundled {count} presets")


def main():
    try:
        fetch_projectm()
    except Exception as error:             # noqa: BLE001
        print(f"    projectM download failed: {error}")
    try:
        fetch_presets()
    except Exception as error:             # noqa: BLE001
        print(f"    preset download failed: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
