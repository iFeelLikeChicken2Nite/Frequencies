"""Frequencies — where settings, data, cache and the log live.

One place for the base folders, so Windows (WP1) can differ without
every file asking. Each function returns the base the app's own
"frequencies" folder goes under, exactly as XDG_*_HOME did before:

    Linux    ~/.config  ~/.local/share  ~/.cache  ~/.local/state
             (or $XDG_CONFIG_HOME and friends, as always)
    Windows  %APPDATA%  %LOCALAPPDATA%  %LOCALAPPDATA%  %LOCALAPPDATA%
    macOS    ~/Library/Application Support (config and data)
             ~/Library/Caches  ~/Library/Logs

So settings land in %APPDATA%\\frequencies on Windows, and the Linux
paths are unchanged.
"""

import os
import shutil
import sys
import tempfile
import threading

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
_migration_lock = threading.RLock()


def _home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


def _windows(name):
    return os.environ.get(name) or _home("AppData",
                                         "Roaming" if name == "APPDATA"
                                         else "Local")


def _raise_walk_error(error):
    raise error


def _copy_link(source, destination):
    try:
        os.symlink(os.readlink(source), destination)
    except FileExistsError:
        pass


def _copy_missing(source, destination):
    for root, directories, files in os.walk(source, onerror=_raise_walk_error):
        relative = os.path.relpath(root, source)
        target = destination if relative == "." else os.path.join(destination, relative)
        os.makedirs(target, exist_ok=True)
        for name in directories:
            original = os.path.join(root, name)
            if os.path.islink(original):
                _copy_link(original, os.path.join(target, name))
        directories[:] = [name for name in directories
                          if not os.path.islink(os.path.join(root, name))
                          and not os.path.islink(os.path.join(target, name))
                          and (not os.path.exists(os.path.join(target, name))
                               or os.path.isdir(os.path.join(target, name)))]
        for name in files:
            original = os.path.join(root, name)
            copied = os.path.join(target, name)
            if os.path.islink(original):
                _copy_link(original, copied)
                continue
            temporary = None
            try:
                descriptor, temporary = tempfile.mkstemp(
                    prefix=".frequencies-migrate-", dir=target)
                with os.fdopen(descriptor, "wb") as output, open(
                        original, "rb") as incoming:
                    os.fchmod(output.fileno(), os.stat(original).st_mode & 0o777)
                    shutil.copyfileobj(incoming, output)
                try:
                    os.link(temporary, copied)
                except FileExistsError:
                    pass
            finally:
                if temporary is not None:
                    os.remove(temporary)


def _mac_base(variable, native, legacy, category):
    override = os.environ.get(variable)
    if override:
        return override
    base = _home("Library", native)
    destination = os.path.join(base, "frequencies")
    marker = os.path.join(destination, ".xdg-migrated-" + category)
    with _migration_lock:
        if os.path.exists(marker):
            return base
        source = os.path.join(_home(*legacy), "frequencies")
        if category == "config" and not os.path.isdir(source):
            source = os.path.join(_home(*legacy), "streamdeck-radio")
        if os.path.isdir(source):
            _copy_missing(source, destination)
            try:
                with open(marker, "x", encoding="utf-8"):
                    pass
            except FileExistsError:
                pass
    return base


def config_base():
    if WINDOWS:
        return _windows("APPDATA")
    if MACOS:
        return _mac_base("XDG_CONFIG_HOME", "Application Support", (".config",), "config")
    return os.environ.get("XDG_CONFIG_HOME") or _home(".config")


def data_base():
    if WINDOWS:
        return _windows("LOCALAPPDATA")
    if MACOS:
        return _mac_base("XDG_DATA_HOME", "Application Support", (".local", "share"), "data")
    return os.environ.get("XDG_DATA_HOME") or _home(".local", "share")


def cache_base():
    if WINDOWS:
        return _windows("LOCALAPPDATA")
    if MACOS:
        return _mac_base("XDG_CACHE_HOME", "Caches", (".cache",), "cache")
    return os.environ.get("XDG_CACHE_HOME") or _home(".cache")


def state_base():
    if WINDOWS:
        return _windows("LOCALAPPDATA")
    if MACOS:
        return _mac_base("XDG_STATE_HOME", "Logs", (".local", "state"), "state")
    return os.environ.get("XDG_STATE_HOME") or _home(".local", "state")
