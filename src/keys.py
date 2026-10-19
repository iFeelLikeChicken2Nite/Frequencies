"""Frequencies — keyboard shortcuts.

One table, used three ways: to build the real QShortcuts, to write the
hint into every tooltip, and to fill the list in Settings. Kept in one
place because the alternative is a tooltip that promises a key nothing
listens for, which is worse than no tooltip.

A binding the user has changed lives in settings under "keys", keyed by
the action name. Anything not overridden uses the default here.
"""

from PyQt5.QtGui import QKeySequence

# action, default binding, what it does, where it appears
ACTIONS = (
    ("favorite", "Alt+B", "Favorite the song playing", "player"),
    ("history", "Ctrl+H", "Open History", "player"),
    ("previous", "Ctrl+Left", "Previous station", "player"),
    ("previous_alt", "Ctrl+Up", "Previous station", "player"),
    ("play_pause", "Space", "Play or pause", "player"),
    ("next", "Ctrl+Right", "Next station", "player"),
    ("next_alt", "Ctrl+Down", "Next station", "player"),
    ("identify", "Ctrl+I", "Identify songs on this station", "player"),
    ("shuffle", "Ctrl+S", "Shuffle stations", "player"),
    ("station_settings", "Alt+J", "Station settings", "player"),
    ("fullscreen", "F11", "Full screen", "player"),
    ("mute", "Ctrl+M", "Mute", "player"),
    ("search", "Ctrl+F", "Search stations", "page"),
    ("search_alt", "Ctrl+L", "Search stations", "page"),
    ("page_stations", "Ctrl+1", "Saved Frequencies", "page"),
    ("page_discover", "Ctrl+2", "Add Frequency", "page"),
    ("page_history", "Ctrl+3", "History", "page"),
    ("page_schedule", "Ctrl+4", "Schedule", "page"),
    ("page_help", "Ctrl+5", "Help", "page"),
    ("page_options", "Ctrl+6", "Settings", "page"),
)

DEFAULTS = {name: key for name, key, _label, _group in ACTIONS}
LABELS = {name: label for name, _key, label, _group in ACTIONS}


def binding(settings, name):
    """The key for one action, honoring an override."""
    keys = (settings or {}).get("keys") or {}
    return keys.get(name) or DEFAULTS.get(name, "")


def pretty(settings, name):
    """The binding as the platform writes it — "Ctrl+B" or "⌘B"."""
    key = binding(settings, name)
    if not key:
        return ""
    return QKeySequence(key).toString(QKeySequence.NativeText)


def tip(settings, text, *names):
    """A tooltip with its shortcuts appended in parentheses.

    Built from the same table the shortcuts are, so the two cannot drift:
    rebinding a key changes what the tooltip claims, without anybody
    remembering to edit a string.
    """
    shown = [pretty(settings, n) for n in names]
    shown = [s for s in shown if s]
    if not shown:
        return text
    return f"{text} ({'/'.join(shown)})"


def set_binding(settings, name, key):
    """Record an override, or clear it back to the default."""
    keys = dict((settings or {}).get("keys") or {})
    if not key or key == DEFAULTS.get(name):
        keys.pop(name, None)
    else:
        keys[name] = key
    settings["keys"] = keys
    return keys


def conflicts(settings):
    """Any binding used by more than one action.

    The alternates are deliberate duplicates — Ctrl+Left and Ctrl+Up both
    mean previous — so only actions that do *different* things count as a
    clash.
    """
    seen, clashes = {}, {}
    for name, _key, _label, _group in ACTIONS:
        base = name.replace("_alt", "")
        key = binding(settings, name)
        if not key:
            continue
        if key in seen and seen[key] != base:
            clashes.setdefault(key, {seen[key]}).add(base)
        else:
            seen[key] = base
    return clashes
