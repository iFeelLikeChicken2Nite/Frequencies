#!/usr/bin/env python3
"""Write the phone's theme table from the desktop's src/themes.py.

    python3 tools/export_themes.py <desktop source>/src

The 22 shell themes are never copied by hand: this reads THEMES, KEYS and
DEFAULT out of themes.py (as data, without running it) and writes
core/src/main/resources/com/frequencies/core/themes.json in the desktop's
order. A theme added on the desktop later comes over by running this again.
"is light" is the desktop's own test (themes.is_light: bg0's brightness).
"""

import ast
import json
import pathlib
import sys

OUT = (pathlib.Path(__file__).resolve().parent.parent
       / "core/src/main/resources/com/frequencies/core/themes.json")


def read(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name in ("THEMES", "KEYS", "DEFAULT"):
                found[name] = ast.literal_eval(node.value)
    return found["THEMES"], found["KEYS"], found["DEFAULT"]


def is_light(bg0):
    r, g, b = (int(bg0[i:i + 2], 16) for i in (1, 3, 5))
    return (r * 299 + g * 587 + b * 114) / 1000.0 > 140


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    themes, keys, default = read(pathlib.Path(sys.argv[1]) / "themes.py")
    table = []
    for key, data in themes.items():
        tokens = {k: data["tokens"][k].lower() for k in keys if k in data["tokens"]}
        missing = [k for k in keys if k not in tokens]
        if missing:
            sys.exit(f"{key}: no {', '.join(missing)}")
        table.append({"key": key, "name": data["name"], "note": data.get("note", ""),
                      "light": is_light(tokens["bg0"]), "tokens": tokens})
    OUT.write_text(json.dumps({"source": "desktop src/themes.py", "default": default,
                               "keys": list(keys), "themes": table}, indent=1) + "\n",
                   encoding="utf-8")
    print(f"{len(table)} themes -> {OUT}")


if __name__ == "__main__":
    main()
