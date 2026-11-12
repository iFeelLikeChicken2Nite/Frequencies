#!/usr/bin/env python3
"""Put what the desktop build ships into the APK (survey section 7).

    python tools/ship_assets.py <desktop source folder> [--config <dev config folder>]

Runs the desktop's own ship tools, as build-windows.ps1 does, against a
copy of your dev build's picks (FREQUENCIES_DEV_CONFIG), and copies their
outputs into app/src/main/assets/shipped/:

  artist_overrides.json   your text fixes          (tools/make_photo_pack.py)
  photo_pack.json         pack, report-service and picture-link addresses
                                                   (make_photo_pack.py)
  station_overrides.json  talk stations            (tools/ship_talk_stations.py)
  twitch_app.json         the Twitch Client ID     (tools/ship_twitch_app.py)

These are plain public data and belong in the public source (F-Droid
builds from it; nothing from the dev picks reaches its build otherwise).
Commit them after running this. No token of any kind ships (desktop build
151: thumbs-down reports go through your Cloudflare report Worker).
Photos never ship: talk station photos come with the photo pack. Every
token file is deleted from the copy before any tool runs, and this stops
if a token-looking text turns up in what it would ship.

The dev picks: --config, else FREQUENCIES_DEV_CONFIG, else the desktop's
own folder (%APPDATA%\\frequencies on Windows, ~/.config/frequencies).
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(HERE, "app", "src", "main", "assets", "shipped")
SECRETS = ("github_token.json", "release_token.json", "report_token.json")
OUTPUTS = ("artist_overrides.json", "photo_pack.json", "station_overrides.json",
           "twitch_app.json")
TOOLS = ("ship_talk_stations.py", "ship_twitch_app.py", "make_photo_pack.py")
TOKEN = re.compile(r"(github_pat_|ghp_|gho_|ghs_|ghu_)[A-Za-z0-9_]{10,}")


def default_config():
    if os.environ.get("FREQUENCIES_DEV_CONFIG"):
        return os.environ["FREQUENCIES_DEV_CONFIG"]
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "frequencies")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "frequencies")


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    desktop = os.path.abspath(argv[0])
    config = default_config()
    if "--config" in argv:
        config = argv[argv.index("--config") + 1]
    if not os.path.isfile(os.path.join(desktop, "tools", "make_photo_pack.py")):
        print(f"!!  {desktop} isn't the desktop's source folder (no tools/make_photo_pack.py)")
        return 1
    print(f"==> dev picks from {config}" + ("" if os.path.isdir(config) else " (not there: nothing of yours ships)"))

    work = tempfile.mkdtemp(prefix="freq-ship-")
    try:
        picks = os.path.join(work, "config")
        if os.path.isdir(config):
            shutil.copytree(config, picks, ignore=shutil.ignore_patterns(
                "artist-photos", "artist-art", "artwork", "*.log", "*.img"))
        else:
            os.makedirs(picks)
        for secret in SECRETS:
            path = os.path.join(picks, secret)
            if os.path.exists(path):
                os.remove(path)
        # A copy of the desktop's tools and the src files they write, so
        # your desktop folder isn't touched.
        tree = os.path.join(work, "desktop")
        os.makedirs(os.path.join(tree, "src"))
        shutil.copytree(os.path.join(desktop, "tools"), os.path.join(tree, "tools"))
        for name in OUTPUTS + ("packbuild.py",):
            source = os.path.join(desktop, "src", name)
            if os.path.isfile(source):
                shutil.copyfile(source, os.path.join(tree, "src", name))
        talk = os.path.join(desktop, "src", "talk_photos")
        if os.path.isdir(talk):
            shutil.copytree(talk, os.path.join(tree, "src", "talk_photos"))
        env = dict(os.environ, FREQUENCIES_DEV_CONFIG=picks)
        for tool in TOOLS:
            print(f"==> {tool}")
            done = subprocess.run([sys.executable, os.path.join(tree, "tools", tool)], env=env, cwd=tree)
            if done.returncode != 0:
                print(f"!!  {tool} failed")
                return 1

        os.makedirs(ASSETS, exist_ok=True)
        shipped = []
        for name in OUTPUTS:
            source = os.path.join(tree, "src", name)
            target = os.path.join(ASSETS, name)
            if not os.path.isfile(source):
                if os.path.exists(target):
                    os.remove(target)
                continue
            with open(source, encoding="utf-8") as f:
                text = f.read()
            if TOKEN.search(text):
                print(f"!!  {name} holds something that looks like a GitHub token; nothing was shipped")
                return 1
            with open(target, "w", encoding="utf-8") as f:
                f.write(text)
            shipped.append(name)
        for name in os.listdir(ASSETS):
            if name not in OUTPUTS:
                print(f"!!  removing {name} from assets/shipped (not something the build ships)")
                path = os.path.join(ASSETS, name)
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        print("==> shipped in the APK: " + (", ".join(shipped) if shipped else "nothing"))
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
