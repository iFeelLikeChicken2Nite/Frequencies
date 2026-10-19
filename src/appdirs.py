"""Frequencies — where settings, data, cache and the log live.

One place for the base folders, so Windows (WP1) can differ without
every file asking. Each function returns the base the app's own
"frequencies" folder goes under, exactly as XDG_*_HOME did before:

    Linux    ~/.config  ~/.local/share  ~/.cache  ~/.local/state
             (or $XDG_CONFIG_HOME and friends, as always)
    Windows  %APPDATA%  %LOCALAPPDATA%  %LOCALAPPDATA%  %LOCALAPPDATA%

So settings land in %APPDATA%\\frequencies on Windows, and the Linux
paths are unchanged.
"""

import os
import sys

WINDOWS = sys.platform == "win32"


def _home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


def _windows(name):
    return os.environ.get(name) or _home("AppData",
                                         "Roaming" if name == "APPDATA"
                                         else "Local")


def config_base():
    if WINDOWS:
        return _windows("APPDATA")
    return os.environ.get("XDG_CONFIG_HOME") or _home(".config")


def data_base():
    if WINDOWS:
        return _windows("LOCALAPPDATA")
    return os.environ.get("XDG_DATA_HOME") or _home(".local", "share")


def cache_base():
    if WINDOWS:
        return _windows("LOCALAPPDATA")
    return os.environ.get("XDG_CACHE_HOME") or _home(".cache")


def state_base():
    if WINDOWS:
        return _windows("LOCALAPPDATA")
    return os.environ.get("XDG_STATE_HOME") or _home(".local", "state")
