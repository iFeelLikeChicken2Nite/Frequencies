#!/usr/bin/env python3
"""Copy the developer's report token into the build (task 133).

Run by build-appimage-fedora.sh before it copies src/ into the AppImage
(and by build-windows.ps1, from %APPDATA%\\frequencies, on Windows):

  your dev build saved   ~/.config/frequencies/report_token.json
  this writes            src/report_token.json

The token files a thumbs-down report as a GitHub issue. It must be a
fine-grained token limited to Issues (Read and write) on the reports
repository, nothing else: it travels inside the builds you share, so
anyone can pull it out, and the worst it can do is add issues there. You
can revoke it and ship a new one. It is never printed or logged. No token
set yet: src/ is left as it is.

Stand-alone on purpose (no app imports): it runs inside the build box.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED = os.path.join(HERE, "src", "report_token.json")


def config_dir():
    if os.environ.get("FREQUENCIES_DEV_CONFIG"):
        # build-windows.ps1's copy (WP3): the Store's Python reads its own
        # private AppData, not the folder the dev build writes.
        return os.environ["FREQUENCIES_DEV_CONFIG"]
    if sys.platform == "win32":
        # Windows (WP2): the dev build's Settings, as appdirs.py has it.
        base = os.environ.get("APPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "frequencies")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return os.path.join(base, "frequencies")


def read_token(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("token") or "").strip()


def main(argv):
    dev = read_token(os.path.join(config_dir(), "report_token.json"))
    if not dev:
        have = read_token(SHIPPED)
        print("    no report token in your dev build's Settings; "
              + ("shipping the one already in src/" if have
                 else "this build will open thumbs-down reports in the "
                      "browser"))
        return 0
    with open(SHIPPED, "w", encoding="utf-8") as f:
        json.dump({"token": dev}, f)
    print("    shipping the report token from your dev build")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
