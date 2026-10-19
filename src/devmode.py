"""Frequencies — is this a dev build?

The developer builds the app for other people. Some settings are the
developer's to make and everyone else's to receive: the talk-station
switch (task 49) is the first. A dev build shows them; a user build
does not, and gets what the developer set, shipped inside the app.

One check, in one place, so every later dev-only thing asks the same
question the same way:

    import devmode
    if devmode.is_dev():
        ...

What counts as a dev build:
  - an AppImage built with `./build-appimage-fedora.sh --dev` (the build
    writes a `dev-build` file next to the code);
  - running straight from the source folder (`python3 src/main.py`).
A normal `./build-appimage-fedora.sh` is a user build.

`FREQ_DEV=1` / `FREQ_DEV=0` in the environment forces it either way
(for tests).
"""

import os

MARKER = "dev-build"

_answer = None          # (is_dev, why), worked out once


def _here():
    return os.path.dirname(os.path.abspath(__file__))


def _work_out():
    forced = os.environ.get("FREQ_DEV", "").strip()
    if forced in ("1", "yes", "true"):
        return True, "FREQ_DEV=1"
    if forced in ("0", "no", "false"):
        return False, "FREQ_DEV=0"
    if os.path.exists(os.path.join(_here(), MARKER)):
        return True, "built with --dev"
    inside_appimage = (bool(os.environ.get("APPIMAGE"))
                       or "/.mount_" in _here())
    if inside_appimage:
        return False, "AppImage"
    return True, "running from source"


def is_dev():
    """True in a dev build. Cheap: worked out once per run."""
    global _answer
    if _answer is None:
        _answer = _work_out()
    return _answer[0]


def describe():
    """'dev build (built with --dev)' / 'user build (AppImage)'."""
    is_dev()
    kind = "dev build" if _answer[0] else "user build"
    return f"{kind} ({_answer[1]})"


def reset():
    """Forget the answer (tests change FREQ_DEV between runs)."""
    global _answer
    _answer = None
