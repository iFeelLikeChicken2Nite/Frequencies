#!/usr/bin/env python3
"""Copy the developer's Twitch app Client ID into the build (task 92e).

Run by build-appimage-fedora.sh before it copies src/ into the AppImage:

  your dev build saved   ~/.config/frequencies/twitch_app.json
  this writes            src/twitch_app.json

The Client ID is public (not a secret): every build needs it to sign in
to Twitch chat. Only that one value is copied; your own sign-in
(twitch_chat.json) is never read. No Client ID set yet: src/ is left as
it is.

Stand-alone on purpose (no app imports): it runs inside the build box.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED = os.path.join(HERE, "src", "twitch_app.json")


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return os.path.join(base, "frequencies")


def read_id(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("client_id") or "").strip()


def main(argv):
    dev = read_id(os.path.join(config_dir(), "twitch_app.json"))
    if not dev:
        have = read_id(SHIPPED)
        print("    no Client ID in your dev build's Settings; "
              + ("shipping the one already in src/" if have
                 else "this build can't sign in to Twitch chat"))
        return 0
    with open(SHIPPED, "w", encoding="utf-8") as f:
        json.dump({"client_id": dev}, f)
    print(f"    shipping Client ID {dev[:4]}… (from your dev build)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
