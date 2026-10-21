"""Frequencies — color schemes for the shell.

A theme is only the surface and text tokens. Sizes stay put, because a
theme that moves the artwork or the row height is a different layout
wearing a different name, and the accent stays out too — it belongs to
whatever is playing, or to the setting that overrides that.

Each one is a whole ramp rather than a hue swapped into the default.
bg0 through bg3 have to keep their relationship — canvas, panel, hover,
pressed — or hover states stop reading as raised and the player bar stops
reading as a separate surface.

A light theme inverts the ramp: bg0 is the lightest and each step up gets
darker, so "elevated" still means "further from the canvas" (Flashbang,
Cassette). Build 130 removed Graphite, Oxide and Cathode; a saved choice
that is not here any more falls back to the default (main.py).
"""

# The keys a theme may set. Anything else is ignored, so a theme file
# cannot quietly change the layout.
KEYS = ("bg0", "bg1", "bg2", "bg3", "line", "row_alt", "bar_bg", "bar_edge",
        "text", "text2", "text3", "down", "playing", "danger", "accent")

THEMES = {
    "midnight": {
        # Named "OG" since build 130; the key stays "midnight" so a saved
        # choice (and the default) keep working.
        "name": "OG",
        "note": "The original and the default. Near-black, faintly blue, dial amber.",
        "tokens": {
            "bg0": "#131315", "bg1": "#1a1a1d", "bg2": "#232327",
            "bg3": "#2b2b30", "line": "#2c2c31", "row_alt": "#1b1b1e",
            "bar_bg": "#2f2f38", "bar_edge": "#464654",
            "text": "#ececee", "text2": "#9a9aa2", "text3": "#6e6e76",
            "down": "#e0574f", "playing": "#4fc3a1", "danger": "#e2564b",
            "accent": "#d99a2b",
        },
    },
    "midnight_black": {
        "name": "Midnight",
        "note": "High contrast: pure black, bright white text, brighter dividers and a yellow accent.",
        "tokens": {
            "bg0": "#000000", "bg1": "#0b0b0b", "bg2": "#1a1a1a",
            "bg3": "#2a2a2a", "line": "#6a6a6a", "row_alt": "#080808",
            "bar_bg": "#1a1a1a", "bar_edge": "#8c8c8c", "text": "#ffffff",
            "text2": "#d4d4d4", "text3": "#a8a8a8", "down": "#ff6b5e",
            "playing": "#4dff9d", "danger": "#ff6b5e", "accent": "#ffe14d",
        },
    },
    "paper": {
        "name": "Flashbang",
        "note": "Light. Warm white, ink-gray text. Named Paper until build 130; you have been warned.",
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
    "deepsea": {
        "name": "Deep Sea",
        "note": "Dark navy with a teal glow, like a sonar screen on a ship's radio.",
        "tokens": {
            "bg0": "#0a1520", "bg1": "#0f1d2b", "bg2": "#16293a",
            "bg3": "#1d3548", "line": "#1c3144", "row_alt": "#0d1926",
            "bar_bg": "#173044", "bar_edge": "#2b506b", "text": "#e3f0f7",
            "text2": "#8fadc0", "text3": "#6a8799", "down": "#ee6a60",
            "playing": "#6fd6a0", "danger": "#ee6a60", "accent": "#35b7d4",
        },
    },
    "velvet": {
        "name": "Velvet",
        "note": "Dark wine and plum, a late-night lounge with a rose-gold dial.",
        "tokens": {
            "bg0": "#170d14", "bg1": "#20121c", "bg2": "#2d1a28",
            "bg3": "#3a2234", "line": "#3a2335", "row_alt": "#1c1018",
            "bar_bg": "#3b2236", "bar_edge": "#5d3a54", "text": "#f4e6ee",
            "text2": "#b394a6", "text3": "#8a6c7e", "down": "#f0705f",
            "playing": "#78cfa4", "danger": "#f0705f", "accent": "#e0a08c",
        },
    },
    "cassette": {
        "name": "Cassette",
        "note": "Light cream-and-tan, a 70s tape deck with a red record button.",
        "tokens": {
            "bg0": "#efe5cd", "bg1": "#e6dabd", "bg2": "#dacda9",
            "bg3": "#cdbf97", "line": "#cdbf99", "row_alt": "#eaded2",
            "bar_bg": "#d8caa4", "bar_edge": "#b4a67b", "text": "#2a2217",
            "text2": "#5d5036", "text3": "#7f7253", "down": "#a8322a",
            "playing": "#1f6b3f", "danger": "#a8322a", "accent": "#b02e20",
        },
    },
    "patina": {
        "name": "Patina",
        "note": "Dark verdigris teal, like old copper, with a bright copper dial.",
        "tokens": {
            "bg0": "#0a1718", "bg1": "#102426", "bg2": "#173539",
            "bg3": "#1e4348", "line": "#1d4046", "row_alt": "#0d1e20",
            "bar_bg": "#1b4146", "bar_edge": "#31696f", "text": "#e6f1ef",
            "text2": "#92b5b2", "text3": "#6b908e", "down": "#f06f5c",
            "playing": "#8fdc9a", "danger": "#f06f5c", "accent": "#e8895a",
        },
    },
    "cobalt": {
        "name": "Cobalt",
        "note": "Dark royal blue, richer and bluer than Deep Sea, with a gold dial.",
        "tokens": {
            "bg0": "#08112b", "bg1": "#0d1a40", "bg2": "#142558",
            "bg3": "#1b306e", "line": "#1a2f6a", "row_alt": "#0b1535",
            "bar_bg": "#18306c", "bar_edge": "#2f52a8", "text": "#e8eeff",
            "text2": "#9aaedd", "text3": "#7489bb", "down": "#ff7466",
            "playing": "#6fe0b4", "danger": "#ff7466", "accent": "#f2c14e",
        },
    },
    "ember": {
        "name": "Ember",
        "note": "Near-black with a red glow, like coals, with a hot orange dial.",
        "tokens": {
            "bg0": "#130b0b", "bg1": "#1c1010", "bg2": "#2a1716",
            "bg3": "#371e1c", "line": "#3a1f1d", "row_alt": "#170d0d",
            "bar_bg": "#3a1f1c", "bar_edge": "#62332e", "text": "#f5e8e4",
            "text2": "#b39892", "text3": "#876d68", "down": "#ff8a7a",
            "playing": "#6fd6a8", "danger": "#ff8a7a", "accent": "#ff6a3d",
        },
    },
    "amber": {
        "name": "Amber CRT",
        "note": "An old amber monitor: black-brown glass, text in warm amber phosphor.",
        "tokens": {
            "bg0": "#130d03", "bg1": "#1b1305", "bg2": "#291c07",
            "bg3": "#372609", "line": "#3b2909", "row_alt": "#170f04",
            "bar_bg": "#34240a", "bar_edge": "#5e4210", "text": "#ffd98a",
            "text2": "#c99c4c", "text3": "#98752f", "down": "#ff6e50",
            "playing": "#b9f06b", "danger": "#ff6e50", "accent": "#ffb000",
        },
    },
    "grape": {
        "name": "Grape Soda",
        "note": "Fizzy dark grape with an acid-lime dial.",
        "tokens": {
            "bg0": "#14092a", "bg1": "#1d0f3a", "bg2": "#2b1858",
            "bg3": "#382070", "line": "#37206e", "row_alt": "#180c30",
            "bar_bg": "#361f6c", "bar_edge": "#5f3bb8", "text": "#f3ebff",
            "text2": "#b8a4e6", "text3": "#8f7bc2", "down": "#ff7390",
            "playing": "#4fe8d4", "danger": "#ff7390", "accent": "#b8f23c",
        },
    },
    "blueprint": {
        "name": "Blueprint",
        "note": "Drafting-table blue with pale chalk lines and a yellow pencil dial.",
        "tokens": {
            "bg0": "#0c2f5a", "bg1": "#103a6c", "bg2": "#17487f",
            "bg3": "#1f5693", "line": "#2b66a6", "row_alt": "#0e335f",
            "bar_bg": "#164a82", "bar_edge": "#3d7ec2", "text": "#f1f7ff",
            "text2": "#b3cfee", "text3": "#8bb0d8", "down": "#ff8a78",
            "playing": "#7ff0c4", "danger": "#ff8a78", "accent": "#ffd23f",
        },
    },
    "steam": {
        "name": "Steam Classic",
        "note": "The original Steam client: olive-green panels with a yellow-gold dial.",
        "tokens": {
            "bg0": "#3e4637", "bg1": "#4c5844", "bg2": "#586651",
            "bg3": "#657560", "line": "#6b7a62", "row_alt": "#434b3b",
            "bar_bg": "#4c5844", "bar_edge": "#889180", "text": "#f4f4ea",
            "text2": "#ddd070", "text3": "#b4bca9", "down": "#ff8a70",
            "playing": "#a6e86c", "danger": "#ff8a70", "accent": "#c4b550",
        },
    },
    "aubergine": {
        "name": "Aubergine",
        "note": "Ubuntu today: deep aubergine with the orange dial.",
        "tokens": {
            "bg0": "#1d0016", "bg1": "#2c001e", "bg2": "#3d0b2c",
            "bg3": "#4d1739", "line": "#4c1a3a", "row_alt": "#240019",
            "bar_bg": "#4a1a38", "bar_edge": "#7a3a6a", "text": "#f8f0f4",
            "text2": "#c9a9bf", "text3": "#9d7c93", "down": "#ff7b6b",
            "playing": "#8ae0b0", "danger": "#ff7b6b", "accent": "#e95420",
        },
    },
    "outrun": {
        "name": "Outrun",
        "note": "80s: violet-black road, magenta haze, a sunset-orange dial and cyan.",
        "tokens": {
            "bg0": "#14061f", "bg1": "#1f0a33", "bg2": "#321150",
            "bg3": "#44186c", "line": "#4a1b73", "row_alt": "#190829",
            "bar_bg": "#3f1466", "bar_edge": "#8a2fc9", "text": "#ffe9f7",
            "text2": "#d49be0", "text3": "#a56fb8", "down": "#ff4d6d",
            "playing": "#2de2e6", "danger": "#ff4d6d", "accent": "#ff9d2e",
        },
    },
    "tron": {
        "name": "Tron",
        "note": "80s: black grid with cyan light lines and an orange spark.",
        "tokens": {
            "bg0": "#02080c", "bg1": "#051119", "bg2": "#0a1f2c",
            "bg3": "#102c3d", "line": "#0f4a63", "row_alt": "#040d14",
            "bar_bg": "#0a2535", "bar_edge": "#18a0c0", "text": "#dff9ff",
            "text2": "#7fc7d9", "text3": "#5496a9", "down": "#ff4d4d",
            "playing": "#ff9a1f", "danger": "#ff4d4d", "accent": "#00e5ff",
        },
    },
    "c64": {
        "name": "Commodore 64",
        "note": "80s: the home computer's blue screen, light-blue text, a light-green dial.",
        "tokens": {
            "bg0": "#2c2268", "bg1": "#352879", "bg2": "#41348f",
            "bg3": "#4d3fa3", "line": "#5547a8", "row_alt": "#30266f",
            "bar_bg": "#40348e", "bar_edge": "#6c5eb5", "text": "#d7d0ff",
            "text2": "#a79ce6", "text3": "#9a8ee6", "down": "#ff8b7e",
            "playing": "#e6e47a", "danger": "#ff8b7e", "accent": "#9ad284",
        },
    },
    "dmg": {
        "name": "DMG",
        "note": "The 80s handheld, as the user named it: four shades of green, nothing else.",
        "tokens": {
            "bg0": "#0f380f", "bg1": "#164416", "bg2": "#1f5420",
            "bg3": "#2a642a", "line": "#306230", "row_alt": "#123c12",
            "bar_bg": "#245a24", "bar_edge": "#4a8a2a", "text": "#9bbc0f",
            "text2": "#8fb00f", "text3": "#789a14", "down": "#ff9f7a",
            "playing": "#e6ffb0", "danger": "#ff9f7a", "accent": "#cfe58a",
        },
    },
    "rose": {
        "name": "Rose",
        "note": "Windows 98's Rose scheme: dusty mauve-rose with cream text, a salmon-pink dial.",
        "tokens": {
            "bg0": "#4e2f3b", "bg1": "#5c3947", "bg2": "#6b4555",
            "bg3": "#7a5163", "line": "#8c6174", "row_alt": "#533340",
            "bar_bg": "#6f4254", "bar_edge": "#b98a9c", "text": "#fff1f3",
            "text2": "#f2cdd5", "text3": "#d8a9b6", "down": "#ffa07a",
            "playing": "#b6f0c8", "danger": "#ffa07a", "accent": "#ffb3c1",
        },
    },
    "oxford": {
        "name": "Oxford Rajah",
        "note": "Pebble palette: Oxford and Duke blues with a Rajah peach dial.",
        "tokens": {
            "bg0": "#00002e", "bg1": "#000055", "bg2": "#0a0a78",
            "bg3": "#1818a0", "line": "#2a2a9a", "row_alt": "#000040",
            "bar_bg": "#0a0a78", "bar_edge": "#5555ff", "text": "#ffffff",
            "text2": "#aaaaff", "text3": "#8c8cdd", "down": "#ff5555",
            "playing": "#55ffaa", "danger": "#ff5555", "accent": "#ffaa55",
        },
    },
    "imperial": {
        "name": "Imperial Aqua",
        "note": "Pebble palette: Imperial Purple and magenta with a Medium Aquamarine dial.",
        "tokens": {
            "bg0": "#2e002e", "bg1": "#550055", "bg2": "#780a78",
            "bg3": "#a018a0", "line": "#a020a0", "row_alt": "#400040",
            "bar_bg": "#780a78", "bar_edge": "#ff55ff", "text": "#ffffff",
            "text2": "#ffaaff", "text3": "#dd88dd", "down": "#ff5555",
            "playing": "#ffff55", "danger": "#ff5555", "accent": "#55ffaa",
        },
    },
    "teletext": {
        "name": "Teletext",
        "note": "Ceefax-style: black, blue blocks and the pure cyan, yellow and green of 80s TV text.",
        "tokens": {
            "bg0": "#000000", "bg1": "#00002e", "bg2": "#00005a",
            "bg3": "#0000a0", "line": "#0000d0", "row_alt": "#00001a",
            "bar_bg": "#00005a", "bar_edge": "#00ffff", "text": "#ffffff",
            "text2": "#00ffff", "text3": "#00cccc", "down": "#ff3030",
            "playing": "#00ff00", "danger": "#ff3030", "accent": "#ffff00",
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
