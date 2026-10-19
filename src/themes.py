"""Frequencies — color schemes for the shell.

A theme is only the surface and text tokens. Sizes stay put, because a
theme that moves the artwork or the row height is a different layout
wearing a different name, and the accent stays out too — it belongs to
whatever is playing, or to the setting that overrides that.

Each one is a whole ramp rather than a hue swapped into the default.
bg0 through bg3 have to keep their relationship — canvas, panel, hover,
pressed — or hover states stop reading as raised and the player bar stops
reading as a separate surface.
"""

# The keys a theme may set. Anything else is ignored, so a theme file
# cannot quietly change the layout.
KEYS = ("bg0", "bg1", "bg2", "bg3", "line", "row_alt", "bar_bg", "bar_edge",
        "text", "text2", "text3", "down", "playing", "danger", "accent")

THEMES = {
    "midnight": {
        "name": "Midnight",
        "note": "The default. Near-black, faintly blue, dial amber.",
        "tokens": {
            "bg0": "#131315", "bg1": "#1a1a1d", "bg2": "#232327",
            "bg3": "#2b2b30", "line": "#2c2c31", "row_alt": "#1b1b1e",
            "bar_bg": "#2f2f38", "bar_edge": "#464654",
            "text": "#ececee", "text2": "#9a9aa2", "text3": "#6e6e76",
            "down": "#e0574f", "playing": "#4fc3a1", "danger": "#e2564b",
            "accent": "#d99a2b",
        },
    },
    "graphite": {
        "name": "Graphite",
        "note": "Neutral gray, no color cast. For looking at artwork.",
        "tokens": {
            "bg0": "#161616", "bg1": "#1e1e1e", "bg2": "#282828",
            "bg3": "#323232", "line": "#333333", "row_alt": "#1f1f1f",
            "bar_bg": "#343434", "bar_edge": "#4c4c4c",
            "text": "#ededed", "text2": "#9c9c9c", "text3": "#6f6f6f",
            "down": "#df5b52", "playing": "#57c39f", "danger": "#df5b52",
            "accent": "#c8c8c8",
        },
    },
    "oxide": {
        "name": "Oxide",
        "note": "Warm browns and rust — a valve radio in a wooden case.",
        "tokens": {
            "bg0": "#17120f", "bg1": "#1f1815", "bg2": "#2a211c",
            "bg3": "#352a23", "line": "#382c25", "row_alt": "#211a16",
            "bar_bg": "#3a2d25", "bar_edge": "#59453a",
            "text": "#f0e7df", "text2": "#a89588", "text3": "#7a6a5f",
            # Down has to stay separable from the walls, which are already
            # warm: it goes bright coral rather than the usual red.
            "down": "#ef7361", "playing": "#7fbf7a", "danger": "#ef7361",
            "accent": "#e08a3c",
        },
    },
    "cathode": {
        "name": "Cathode",
        "note": "Green on black, in the manner of a terminal.",
        "tokens": {
            "bg0": "#0a0f0b", "bg1": "#101711", "bg2": "#182119",
            "bg3": "#1f2b21", "line": "#1f2d22", "row_alt": "#131b14",
            "bar_bg": "#1d2a1f", "bar_edge": "#31492f",
            "text": "#d8f0d8", "text2": "#83a487", "text3": "#5c745f",
            # Playing cannot be green here; it would vanish into the walls.
            "down": "#e2665c", "playing": "#e8d55f", "danger": "#e2665c",
            "accent": "#5ddc7a",
        },
    },
    "paper": {
        "name": "Paper",
        "note": "Light. Warm white, ink-gray text, for a bright room.",
        "tokens": {
            # The whole ramp inverts: bg0 is the lightest and each step up
            # gets *darker*, so "elevated" still means "further from the
            # canvas". Keeping them ascending would make hover states
            # disappear into the page.
            "bg0": "#f6f4f1", "bg1": "#efece7", "bg2": "#e4e0d9",
            "bg3": "#d8d3ca", "line": "#d5d0c7", "row_alt": "#f1eee9",
            "bar_bg": "#e2ded6", "bar_edge": "#c6c0b4",
            "text": "#1e1c19", "text2": "#5c574f", "text3": "#8a8378",
            "down": "#b23c30", "playing": "#1f7a5c", "danger": "#b23c30",
            "accent": "#a8631a",
        },
    },
}

DEFAULT = "midnight"


def names():
    """(key, label) for a settings dropdown."""
    return [(key, data["name"]) for key, data in THEMES.items()]


def tokens(key):
    """The colors for one theme, or the default's."""
    theme = THEMES.get(key) or THEMES[DEFAULT]
    return dict(theme["tokens"])


def is_light(key):
    """Whether text on this theme is dark on light.

    Anything painting its own overlays needs to know — a scrim of white
    over a white page is not a scrim.
    """
    from PyQt5.QtGui import QColor

    c = QColor(tokens(key).get("bg0", "#131315"))
    return (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000.0 > 140
