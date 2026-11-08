#!/usr/bin/env python3
"""Leave a note beside a finished build (build 125, task 177).

Run by build-appimage-fedora.sh and build-windows.ps1 last:

    python3 tools/build_info.py <the AppImage or Setup.exe> [--dev]

writes <file>.json: the build number (src/version.py), platform and architecture,
dev or user build, its size and sha256, and whether your dev picks were
shipped into it (talk stations, Twitch Client ID, disabled presets; no
report token since build 151). The dev build's "Publish this build as an
update" reads it and refuses a dev build, a changed file, or a build missing those picks, so
an update never takes them away from the people who installed it.

Nothing secret goes in the note. Stand-alone on purpose: it runs inside
the build box.
"""

import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.environ.get("FREQUENCIES_SOURCE_DIR") or os.path.join(HERE, "src")


def read(name):
    try:
        with open(os.path.join(SRC, name), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def shipped():
    """Which of the tools/ship_*.py found something to ship (no report
    token since build 151)."""
    talk = read("station_overrides.json") or {}
    twitch = read("twitch_app.json") or {}
    presets = read("visualizer_disabled.json") or {}
    return {
        "talk stations": bool(talk),
        "Twitch Client ID": bool(str(twitch.get("client_id") or "").strip()),
        "disabled presets": bool(presets.get("presets")),
    }


def main(argv):
    if len(argv) < 2:
        print("usage: build_info.py <file> [--dev]")
        return 2
    path = argv[1]
    with open(os.path.join(SRC, "version.py"), encoding="utf-8") as f:
        build = int(re.search(r"^BUILD\s*=\s*(\d+)", f.read(), re.M)
                    .group(1))
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for piece in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(piece)
    picks = shipped()
    macos = re.fullmatch(r"frequencies-macos-(arm64|x86_64)(-dev)?\.dmg",
                         os.path.basename(path), re.IGNORECASE)
    if path.lower().endswith(".dmg") and not macos:
        print("macOS DMG must be named Frequencies-macOS-arm64.dmg or "
              "Frequencies-macOS-x86_64.dmg")
        return 2
    arch = macos.group(1).lower() if macos else ""
    if macos and macos.group(2) and "--dev" not in argv:
        print("a -dev DMG must be marked with --dev")
        return 2
    info = {"build": build,
            "os": ("macos_" + arch if arch else
                   "windows" if path.lower().endswith(".exe") else "linux"),
            "dev": "--dev" in argv,
            "file": os.path.basename(path),
            "size": os.path.getsize(path),
            "sha256": digest.hexdigest(),
            "made": time.strftime("%Y-%m-%d %H:%M"),
            "shipped": picks}
    if arch:
        info["arch"] = arch
    with open(path + ".json", "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    print(f"    build {build} ({'dev' if info['dev'] else 'user'} build)")
    missing = [name for name, ok in picks.items() if not ok]
    if missing and not info["dev"]:
        print("    !! not shipped: " + ", ".join(missing)
              + " - the Publish button will refuse this build")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
