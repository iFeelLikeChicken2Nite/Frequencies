#!/usr/bin/env python3
"""Copy the developer's disabled visualizer presets into the build (task 119).

Run by build-appimage-fedora.sh before it copies src/ into the AppImage:

  your dev build saved   ~/.config/frequencies/settings.json
                         ("visualizer_disabled": full paths on your PC)
  this writes            src/visualizer_disabled.json
                         ({"presets": ["Some Preset.milk", ...]})

Only the file names are shipped, because the preset folder sits somewhere
else on every machine. Nothing else from settings.json is read. Your list
is empty or missing (a new machine): src/ is left as it is.

Stand-alone on purpose (no app imports): it runs inside the build box.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED = os.path.join(HERE, "src", "visualizer_disabled.json")


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


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main(argv):
    settings = read_json(os.path.join(config_dir(), "settings.json"))
    paths = settings.get("visualizer_disabled") if isinstance(
        settings, dict) else None
    names = sorted({os.path.basename(str(p)) for p in paths or ()
                    if str(p).strip()})
    if not names:
        old = read_json(SHIPPED)
        have = len(old.get("presets") or ()) if isinstance(old, dict) else 0
        print("    no disabled visualizer presets in your dev build; "
              f"shipping the {have} already in src/")
        return 0
    with open(SHIPPED + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"presets": names}, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(SHIPPED + ".tmp", SHIPPED)
    print(f"    disabled visualizer presets shipped: {len(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
