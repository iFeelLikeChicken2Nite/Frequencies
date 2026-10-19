"""Design tokens and stylesheet generation for Frequencies.

The whole app is themed from one dict. Change a token here and every
widget follows. The accent is swappable at runtime so it can be pulled
from the current album art.
"""

from PyQt5.QtCore import QEvent, QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import QProxyStyle, QStyle
from PyQt5.QtGui import QColor, QImage, QPalette, QPixmap

# --- tokens ---------------------------------------------------------------
# Warm-neutral darks rather than pure black: the app is a receiver, and the
# default accent is the amber of a lit dial rather than a UI blue.

T = {
    "bg0":      "#131315",   # window canvas
    "bg1":      "#1a1a1d",   # panels: sidebar, list, player bar
    "bg2":      "#232327",   # hover / elevated
    "bg3":      "#2b2b30",   # pressed / selected-but-inactive
    "line":     "#2c2c31",   # hairline
    "row_alt":  "#1b1b1e",   # every other station row: enough to track a
                             # line across, not enough to read as stripes
    # Clearly lighter than everything else, with a lit edge along the top.
    #
    # #24242a was only three steps from the sidebar and read as the same
    # surface; a control bar has to announce itself, so this is far enough
    # up to be unmistakable and the top border does the rest.
    "bar_bg":   "#2f2f38",
    "bar_edge": "#464654",
    "down":     "#e0574f",   # a station that is not answering
    "playing":  "#4fc3a1",   # the row you are hearing. Deliberately not the
                             # accent: artwork can push the accent into red,
                             # and a red playing row is indistinguishable
                             # from a red down row.
    "text":     "#ececee",
    "text2":    "#9a9aa2",   # secondary
    "text3":    "#6e6e76",   # hints, disabled
    "accent":   "#d99a2b",   # replaced at runtime from artwork
    "on_accent": "#17130a",
    "danger":   "#e2564b",

    "radius_sm": "4px",
    "radius":    "6px",
    "radius_lg": "10px",

    "row_h":     44,
    "sidebar_w": 208,
    "rail_w":    60,
    "bar_h":     76,
    # Almost the full height of the bar: the artwork is the one thing in
    # there worth looking at, and at 48 it sat in a moat of padding.
    # 58, not 64: the hover highlight is drawn as a 2px edge inside the
    # pixmap, and at 64 in a 76px bar with 6px margins there was nothing
    # left for it — the bottom edge fell outside the bar.
    # 54: at 58 in a 76px bar with 6px margins the rounded corners were
    # touching the edge and the bottom pair were clipped by a pixel.
    "art_sm":    54,
}

FONT_STACK = '"Inter", "SF Pro Text", "Cantarell", "Noto Sans", sans-serif'


def apply_theme(key):
    """Swap the surface and text colors for one of the named schemes.

    Mutates T in place rather than rebuilding it, because painted widgets
    read the tokens directly at paint time — handing them a new dictionary
    would leave every one of them looking at the old one.
    """
    import themes

    T.update(themes.tokens(key))
    T["on_accent"] = on_accent_for(T["accent"])
    T["light"] = themes.is_light(key)
    return T


def tint(hex_color: str, alpha: int) -> str:
    """rgba() string for a token color at the given alpha (0-255).

    For stylesheets. Painting code wants tint_color() instead — QColor
    cannot parse an rgba() string and silently returns opaque black, which
    is how a colored pill ended up drawn in black.
    """
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def tint_color(hex_color: str, alpha: int) -> QColor:
    """The same tint as a QColor, for QPainter."""
    c = QColor(hex_color)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    """Mix two colors through hue rather than through grey.

    Interpolating red and blue in RGB passes through a muddy purple-grey;
    going round the hue circle keeps every intermediate color as vivid as
    the two ends, which is what a light changing looks like.
    """
    ah, as_, al, _ = a.getHsl()
    bh, bs, bl, _ = b.getHsl()
    if ah < 0:
        ah = bh
    if bh < 0:
        bh = ah
    if ah < 0 and bh < 0:
        ah = bh = 0
    # The short way round: 350 to 10 is twenty degrees, not three hundred.
    delta = ((bh - ah + 540) % 360) - 180
    out = QColor()
    out.setHsl(int((ah + delta * t) % 360),
               int(as_ + (bs - as_) * t),
               int(al + (bl - al) * t))
    return out


def readable_accent(color: QColor) -> QColor:
    """Clamp an arbitrary artwork color into something usable as UI accent."""
    h, s, l, _ = color.getHsl()
    if h < 0:
        h = 40
    s = max(90, min(s, 200))
    l = max(120, min(l, 175))
    out = QColor()
    out.setHsl(h, s, l)
    return out


# The app's own accent, before any artwork has moved it. T["accent"] is
# rewritten as the colour changes, so this keeps the starting amber.
DEFAULT_ACCENT = T["accent"]

# How colourful a pixel must be to count, in RGB terms (brightest channel
# minus darkest). HSL saturation alone let near-white pixels through: a
# cream of (235, 225, 210) is "saturated" by HSL's measure, and the
# accent came out as a washed-out beige for a cover that is white.
MIN_CHROMA = 28


def dominant_color(image: QImage, fallback=DEFAULT_ACCENT):
    """Pick a vivid representative color from album art.

    Averaging gives mud, so this buckets a 24x24 downscale by hue and
    scores buckets on population x saturation.

    Art with no usable colour at all (white, grey, black, or only a
    speck of colour) returns `fallback`. Pass fallback=None to get None
    back instead, so the caller can choose (the accent falls back to the
    desktop's own accent colour, task 31).
    """
    if image is None or image.isNull():
        return QColor(fallback) if fallback is not None else None

    small = image.scaled(24, 24).convertToFormat(QImage.Format_RGB32)
    buckets = {}
    for y in range(small.height()):
        for x in range(small.width()):
            c = QColor(small.pixel(x, y))
            h, s, l, _ = c.getHsl()
            if s < 40 or l < 35 or l > 225:
                continue          # skip greys, near-black, near-white
            r, g, b = c.red(), c.green(), c.blue()
            if max(r, g, b) - min(r, g, b) < MIN_CHROMA:
                continue          # pale tints of white or grey
            key = h // 20
            pop, sat_sum, br, bg, bb = buckets.get(key, (0, 0, 0, 0, 0))
            buckets[key] = (pop + 1, sat_sum + s, br + r, bg + g, bb + b)

    # A few stray pixels (JPEG noise, a tiny logo) are not the art's
    # colour. At least 1% of the picture has to agree.
    need = max(4, (small.width() * small.height()) // 100)
    buckets = {k: v for k, v in buckets.items() if v[0] >= need}
    if not buckets:
        return QColor(fallback) if fallback is not None else None

    key = max(buckets, key=lambda k: buckets[k][0] * buckets[k][1])
    pop, _, r, g, b = buckets[key]
    return readable_accent(QColor(r // pop, g // pop, b // pop))


def palette() -> QPalette:
    """Qt paints plenty of surfaces from the palette rather than the
    stylesheet -- scroll area viewports, dialog backgrounds, disabled text.
    Without this they fall back to the light default and dark text on dark
    backgrounds goes invisible."""
    p = QPalette()
    p.setColor(QPalette.Window, QColor(T["bg0"]))
    p.setColor(QPalette.WindowText, QColor(T["text"]))
    p.setColor(QPalette.Base, QColor(T["bg1"]))
    p.setColor(QPalette.AlternateBase, QColor(T["bg2"]))
    p.setColor(QPalette.Text, QColor(T["text"]))
    p.setColor(QPalette.Button, QColor(T["bg2"]))
    p.setColor(QPalette.ButtonText, QColor(T["text"]))
    p.setColor(QPalette.ToolTipBase, QColor(T["bg3"]))
    p.setColor(QPalette.ToolTipText, QColor(T["text"]))
    p.setColor(QPalette.PlaceholderText, QColor(T["text3"]))
    p.setColor(QPalette.Highlight, QColor(T["accent"]))
    p.setColor(QPalette.HighlightedText, QColor(T["on_accent"]))
    p.setColor(QPalette.Link, QColor(T["accent"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(T["text3"]))
    return p


# --- stylesheet -----------------------------------------------------------

ACCENT_RULES = """
QPushButton[nav="true"]:checked {{ background: {accent_tint}; color: {accent}; }}
QListView::item:selected {{ background: {accent_tint}; color: {accent}; }}
QLineEdit:focus {{ border: 1px solid {accent}; }}
/* The six ways to add a station: label under icon, quiet until chosen. */
QToolButton[source="true"] {{
    color: {text2};
    font-size: 11px;
    /* Room between the words: at 10px of padding the labels of adjacent
       buttons touched and read as one string. */
    padding: 4px 14px 3px 14px;
    border-radius: 6px;
}}
QToolButton[source="true"]:hover   {{ background: {bg2}; color: {text}; }}
QToolButton[source="true"]:checked {{ background: {accent_tint}; color: {accent}; }}
/* The chooser: the same buttons, sized to be the thing on the page. */
QToolButton[source="true"][big="true"] {{
    font-size: 13px;
    padding: 18px 16px 14px 16px;
    border-radius: 12px;
    /* Every block carries its own edge, so they read as a set of cards
       whether or not one is chosen — and the edge is what lights up. */
    border: 1px solid {line};
    background: {bg1};
}}
QToolButton[source="true"][big="true"]:hover {{
    border: 1px solid {accent};
    background: {bg2};
    color: {text};
}}
QToolButton[source="true"][big="true"]:checked {{
    border: 1px solid {accent};
    background: {accent_tint};
    color: {accent};
}}

QToolButton[primary="true"] {{ background: {accent}; }}
QToolButton[primary="true"]:hover   {{ background: {accent_hi}; }}
QToolButton[primary="true"]:pressed {{ background: {accent_lo}; }}
QPushButton:default  {{ background: {accent}; color: {on_accent}; border: none; }}
QPushButton:default:hover {{ background: {accent_tint_hi}; }}
QPushButton[segment="true"]:checked {{ background: {accent_tint}; color: {accent}; }}
/* The indicators themselves, not just their hover state.
   Only the hover rule mentioned the accent, so a ticked box kept whatever
   color it was built with and stayed amber after the accent moved. */
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{
    background: {accent}; border: 1px solid {accent};
}}
QRadioButton::indicator:checked {{
    background: {accent}; border: 4px solid {bg1};
}}
QComboBox:focus, QComboBox:on {{ border-color: {accent}; }}
/* The search field, which was the one input still outlined in grey when
   it had focus. */
QLineEdit:focus {{ border: 1px solid {accent}; }}
QLineEdit[role="search"]:focus {{ border: 1px solid {accent}; }}
QTabBar::tab:selected {{ color: {accent}; border-bottom: 2px solid {accent}; }}
QProgressBar::chunk {{ background: {accent}; }}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {accent}; }}
QSlider::sub-page:horizontal {{ background: {accent}; }}
QSlider::handle:horizontal:hover {{ background: {accent}; }}
QMenu::item:selected {{ background: {accent_tint}; color: {accent}; }}
/* Selected text. Only the full sheet set this, and the full sheet is
   written once at startup — so every text field kept the starting amber
   whatever the accent moved to (task 20). */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
    selection-background-color: {accent};
    selection-color: {on_accent};
}}
"""


def accent_overlay(accent: str) -> str:
    """Just the rules that mention the accent.

    Restyling costs time in proportion to how many widgets have to be
    repolished -- around 26ms for the whole window against 3ms for the
    player bar -- so the surfaces you are looking at get this small sheet
    at once, and the whole window catches up later.
    """
    t = _tokens(accent)
    return ACCENT_RULES.format(**t)


_ACTIVE = []                # the app's AccentController (set when made)


def current_overlay() -> str:
    """The accent-only sheet for the accent in use now, or "" before an
    AccentController exists. For a menu built anywhere (task 105): the
    full sheet is written once at startup, so a menu that only inherits
    from the window shows the starting amber."""
    if not _ACTIVE:
        return ""
    try:
        return _ACTIVE[0].overlay()
    except Exception:                           # noqa: BLE001
        return ""


def on_accent_for(colour) -> str:
    """Black or white, whichever can be read on this color.

    HSL lightness calls a saturated yellow-green mid-range when the eye
    calls it dazzling, so the play glyph stayed white on a button nobody
    could look at. Perceived brightness weights green heavily, which is
    what actually matters here.
    """
    c = QColor(colour)
    brightness = (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000.0
    return "#14110b" if brightness > 150 else "#ffffff"


def _tokens(accent: str = None) -> dict:
    t = dict(T)
    if accent:
        t["accent"] = accent
        t["on_accent"] = on_accent_for(accent)
    t["accent_tint"] = tint(t["accent"], 38)
    t["accent_tint_hi"] = tint(t["accent"], 60)
    t["accent_hi"] = QColor(t["accent"]).lighter(122).name()
    t["accent_lo"] = QColor(t["accent"]).darker(112).name()
    t["font"] = FONT_STACK
    return t


# Interface size, as a multiplier on every font size the sheet sets.
#
# The zoom used to change the application's font, which the sheet then
# overrode — every rule here names a pixel size, and a stylesheet always
# beats setFont. So choosing 120% changed nothing anyone could see. The
# sizes are scaled where they are written instead.
ZOOM = 1.0


def _scaled(sheet):
    import re

    if abs(ZOOM - 1.0) < 0.001:
        return sheet
    return re.sub(
        r"font-size:\s*(\d+(?:\.\d+)?)px",
        lambda m: f"font-size: {max(7, round(float(m.group(1)) * ZOOM))}px",
        sheet)


def stylesheet(accent: str = None) -> str:
    return _scaled(_stylesheet(accent))


def _stylesheet(accent: str = None) -> str:
    # The play button is filled with the accent, so hovering it has to go
    # brighter. accent_tint_hi mixes toward the background, which on a dark
    # theme reads as the button dimming when you point at it — backwards.
    t = _tokens(accent)

    return """
/* Only the named surfaces paint a background. A blanket QWidget rule makes
   every child label inside a panel repaint the page color, which shows up
   as pale bands behind text. */
QWidget {{
    color: {text};
    font-family: {font};
    font-size: 13px;
}}
QMainWindow, QDialog {{ background: {bg0}; }}
QLabel, QToolButton, QCheckBox, QRadioButton {{ background: transparent; }}

/* --- panels ---------------------------------------------------------- */
#Sidebar, #RightPanel {{ background: {bg1}; border: none; }}
/* The reserved artwork space. Empty it is a faint well, so the sidebar
   does not look like it has a hole in it before anything is playing. */
#ArtDock {{ background: {bg0}; border-radius: 8px; margin: 4px 8px 2px 8px; }}
/* The skin browser's preview well: a skin is drawn for a dark desktop, so
   it needs something behind it that is not the dialog's own panel grey. */
#SkinPreview {{ background: {bg0}; border-radius: 8px; }}
#Content   {{ background: {bg0}; }}
#PlayerBar {{ background: {bar_bg}; border-top: 1px solid {bar_edge}; }}

QLabel[role="secondary"] {{ color: {text2}; font-size: 11px; }}
QLabel[role="section"]   {{ color: {text3}; font-size: 11px; padding: 14px 14px 6px 14px; }}
QLabel[role="title"]     {{ color: {text}; font-size: 14px; }}
/* The third line of the player bar: the station. Quieter than the artist
   above it, because it changes least often and you already chose it. */
QLabel[role="tertiary"]  {{ color: {text3}; font-size: 10px; }}
/* Source links: the accent, like every other link in the app. They were
   the one place still painted in a fixed grey. */
QLabel[role="tertiary"] a {{ color: {accent}; text-decoration: underline; }}
QLabel[role="secondary"] a {{ color: {accent}; text-decoration: underline; }}
/* Something happening now, rather than a description of what is on. */
QLabel[role="notice"]    {{ color: {accent}; font-size: 10px; }}

/* Floats over a list: raised, rounded, and quiet until pointed at. */
/* Reads as a link rather than a control: an aside, not an action the
   layout is built around. */
QPushButton[link="true"] {{
    background: transparent;
    border: none;
    color: {text3};
    font-size: 10px;
    text-decoration: underline;
    padding: 0;
}}
QPushButton[link="true"]:hover {{ color: {accent}; }}

QPushButton[floating="true"] {{
    background: {bg3};
    color: {text};
    border: 1px solid {line};
    border-radius: 15px;
    padding: 6px 16px 6px 12px;
    font-size: 12px;
}}
/* Opaque, because it floats over the list.
   The hover used the accent at 38/255 alpha, which is fine on a solid
   panel and not on a button sitting over scrolling rows: the text
   underneath showed straight through, so it read as amber over some
   rows and as nothing at all over others. A solid fill and a visible
   border make it a button wherever it happens to be. */
/* Hover takes the accent, and stays opaque.
   Opaque because it floats over scrolling rows — a translucent fill let
   the text underneath show through, so it read as amber over some rows
   and as nothing at all over others. */
QPushButton[floating="true"]:hover {{
    background: {bg3};
    color: {accent};
    border: 1px solid {accent};
}}
/* The artist biography: meant to be read, so it is set at reading size
   rather than the caption size everything else in a side panel uses. */
QLabel[role="body"]      {{ color: {text2}; font-size: 15px; }}
QLabel[role="display"]   {{ color: {text}; font-size: 22px; }}

/* --- sidebar nav ----------------------------------------------------- */
QPushButton[nav="true"] {{
    background: transparent;
    border: none;
    border-radius: {radius};
    color: {text2};
    padding: 0 10px;
    text-align: left;
    min-height: 34px;
}}
QPushButton[nav="true"]:hover   {{ background: {bg2}; color: {text}; }}
QPushButton[nav="true"]:checked {{ background: {accent_tint}; color: {accent}; }}

/* --- tag pills: hue kept, saturation dropped to a tint ---------------- */
QPushButton[pill="true"] {{
    border: none;
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 11px;
    min-height: 20px;
}}

/* --- station list ---------------------------------------------------- */
QListView, QListWidget {{
    background: {bg0};
    border: none;
    outline: 0;
}}
QListView::item {{
    height: {row_h}px;
    border-radius: {radius};
    padding: 0 10px;
    margin: 0 6px;
}}
QListView::item:hover    {{ background: {bg2}; }}
QListView::item:selected {{ background: {accent_tint}; color: {accent}; }}

/* --- inputs ---------------------------------------------------------- */
QLineEdit {{
    background: {bg2};
    border: 1px solid transparent;
    border-radius: {radius};
    padding: 7px 10px 7px 30px;
    selection-background-color: {accent};
    selection-color: {on_accent};
}}
QLineEdit:focus {{ border: 1px solid {accent}; }}

QComboBox {{
    background: {bg2};
    border: 1px solid {line};
    border-radius: {radius};
    padding: 5px 10px;
    min-height: 24px;
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {bg2};
    border: 1px solid {line};
    border-radius: {radius};
    selection-background-color: {accent_tint};
    padding: 4px;
}}

/* --- transport ------------------------------------------------------- */
QToolButton {{
    background: transparent;
    border: none;
    border-radius: {radius};
    padding: 6px;
}}
QToolButton:hover   {{ background: {bg2}; }}
QToolButton:pressed {{ background: {bg3}; }}
/* The six ways to add a station: label under icon, quiet until chosen. */
QToolButton[source="true"] {{
    color: {text2};
    font-size: 11px;
    /* Room between the words: at 10px of padding the labels of adjacent
       buttons touched and read as one string. */
    padding: 4px 14px 3px 14px;
    border-radius: 6px;
}}
QToolButton[source="true"]:hover   {{ background: {bg2}; color: {text}; }}
QToolButton[source="true"]:checked {{ background: {accent_tint}; color: {accent}; }}
/* The chooser: the same buttons, sized to be the thing on the page. */
QToolButton[source="true"][big="true"] {{
    font-size: 13px;
    padding: 18px 16px 14px 16px;
    border-radius: 12px;
    /* Every block carries its own edge, so they read as a set of cards
       whether or not one is chosen — and the edge is what lights up. */
    border: 1px solid {line};
    background: {bg1};
}}
QToolButton[source="true"][big="true"]:hover {{
    border: 1px solid {accent};
    background: {bg2};
    color: {text};
}}
QToolButton[source="true"][big="true"]:checked {{
    border: 1px solid {accent};
    background: {accent_tint};
    color: {accent};
}}

QToolButton[primary="true"] {{
    background: {accent};
    border-radius: 19px;
    min-width: 38px;
    min-height: 38px;
}}
QToolButton[primary="true"]:hover   {{ background: {accent_hi}; }}
QToolButton[primary="true"]:pressed {{ background: {accent_lo}; }}

/* --- buttons ---------------------------------------------------------- */
QPushButton {{
    background: {bg2};
    border: 1px solid {line};
    border-radius: {radius};
    padding: 6px 14px;
    min-height: 20px;
    color: {text};
}}
QPushButton:hover    {{ background: {bg3}; border-color: {text3}; }}
QPushButton:pressed  {{ background: {bg1}; }}
QPushButton:disabled {{ color: {text3}; background: {bg1}; border-color: {line}; }}
QPushButton:default  {{ background: {accent}; color: {on_accent}; border: none; }}
QPushButton:default:hover {{ background: {accent_tint_hi}; }}

#Segmented {{
    background: {bg1};
    border-radius: 9px;
}}
QPushButton[segment="true"] {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 6px 16px;
    color: {text2};
    min-height: 20px;
}}
QPushButton[segment="true"]:hover   {{ color: {text}; background: {bg2}; }}
QPushButton[segment="true"]:checked {{ background: {accent_tint}; color: {accent}; }}

/* --- checkboxes and radios -------------------------------------------- */
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {text3};
    background: {bg2};
}}
QCheckBox::indicator {{ border-radius: {radius_sm}; }}
QRadioButton::indicator {{ border-radius: 9px; }}
/* The indicators themselves, not just their hover state.
   Only the hover rule mentioned the accent, so a ticked box kept whatever
   color it was built with and stayed amber after the accent moved. */
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{
    background: {accent}; border: 1px solid {accent};
}}
QRadioButton::indicator:checked {{
    background: {accent}; border: 4px solid {bg1};
}}
QComboBox:focus, QComboBox:on {{ border-color: {accent}; }}
/* The search field, which was the one input still outlined in grey when
   it had focus. */
QLineEdit:focus {{ border: 1px solid {accent}; }}
QLineEdit[role="search"]:focus {{ border: 1px solid {accent}; }}
QTabBar::tab:selected {{ color: {accent}; border-bottom: 2px solid {accent}; }}
QProgressBar::chunk {{ background: {accent}; }}
QCheckBox::indicator:checked {{
    background: {accent};
    border-color: {accent};
    image: none;
}}
QRadioButton::indicator:checked {{
    width: 6px;
    height: 6px;
    background: {bg2};
    border: 5px solid {accent};
    border-radius: 8px;
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {line};
    background: {bg1};
}}
QCheckBox:disabled, QRadioButton:disabled {{ color: {text3}; }}

/* --- spin boxes -------------------------------------------------------- */
QSpinBox, QDoubleSpinBox {{
    background: {bg2};
    border: 1px solid {line};
    border-radius: {radius};
    padding: 5px 8px;
    min-width: 84px;
    min-height: 22px;
    selection-background-color: {accent};
    selection-color: {on_accent};
}}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {accent}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {text3}; background: {bg1}; }}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 16px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {text2};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    width: 0; height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {text2};
}}

/* --- sliders --------------------------------------------------------- */
/* The unfilled half is a hairline, not a grey bar.
   height on the groove is not enough on its own: sub-page and add-page are
   laid out against the groove's CONTENT rect, and with no vertical margin
   they fill the widget's full height and paint that hairline color as a
   15px block behind everything. Margins are what actually thin the track,
   so all three get them and the groove keeps only the color. */
QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{
    height: 2px;
    background: {line};
    border-radius: 1px;
}}
QSlider::sub-page:horizontal {{
    height: 2px;
    background: {accent};
    border-radius: 1px;
}}
/* Transparent, not a color.
   add-page ignores height and margin and paints the full height of the
   widget, which is the grey block that appeared behind every slider. The
   groove is already the right 2px, so letting it show through is both
   correct and one less thing to draw. */
QSlider::add-page:horizontal {{ background: transparent; }}
QSlider::handle:horizontal {{
    background: {text};
    width: 11px;
    height: 11px;
    /* Pulls the handle back out over the now-inset track. */
    margin: -5px 0;
    border-radius: 5px;
}}
QSlider::handle:horizontal:hover {{ background: {accent}; }}
/* Sub-control first, pseudo-state second.
   Written the other way round -- QSlider:disabled::sub-page -- Qt applies
   the background to the whole QSlider instead of to the sub-control, and
   it does so whether or not the slider is disabled. That is the grey block
   that sat behind every slider in the app. */
QSlider::sub-page:horizontal:disabled {{ background: {line}; }}

QSlider::groove:vertical {{
    width: 2px; background: {line}; border-radius: 1px;
}}
QSlider::sub-page:vertical {{ background: transparent; }}
QSlider::add-page:vertical {{
    width: 2px; background: {accent}; border-radius: 1px;
}}
QSlider::handle:vertical {{
    background: {text};
    width: 11px;
    height: 11px;
    margin: 0 -5px;
    border-radius: 5px;
}}

/* --- scrollbars: thin, no arrows, fade into the panel ---------------- */
/* A wide, invisible track with a narrow rounded handle inside it.
   The bar has to be easy to grab and quiet to look at, and those pull in
   opposite directions — so the hit area is 13px and only 5 of them are
   ever painted. */
QScrollBar:vertical {{ background: transparent; width: 15px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {bg3};
    /* Inset inside the wide track, so the grab area is the full 13px and
       the painted handle stays a rounded sliver.
       The radius has to be half the painted width or the corners are
       clipped square: at 5px wide with a 3px radius the arcs overlapped
       and Qt drew a rectangle. */
    /* 7px wide with a 3.5px radius: the radius is exactly half the
       width, which is what makes the ends true semicircles rather than
       rounded corners on a rectangle. */
    margin: 3px 4px;
    width: 7px;
    border-radius: 3px;
    min-height: 36px;
}}
QScrollBar::handle:vertical:hover {{ background: {text2}; }}
QScrollBar:horizontal {{ background: transparent; height: 15px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {bg3};
    margin: 4px 3px;
    height: 7px;
    border-radius: 3px;
    min-width: 36px;
}}
/* The horizontal handle lights up too.
   It had no hover rule at all, so the curated strip's bar was the one
   scrollbar in the app that never responded to the pointer. */
QScrollBar::handle:horizontal:hover {{ background: {text2}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
/* The track either side of the handle paints nothing, so the rounded
   handle is the only thing on it. */
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* The splitter grips: invisible until pointed at, then a hairline. */
QSplitter#Body::handle {{ background: transparent; }}
QSplitter#Body::handle:hover {{ background: {accent_tint}; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* --- menus ----------------------------------------------------------- */
QMenu {{
    background: {bg2};
    border: 1px solid {line};
    border-radius: {radius_lg};
    padding: 5px;
}}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: {radius_sm}; }}
QMenu::item:selected {{ background: {accent_tint}; color: {accent}; }}
/* A disabled item looks disabled. The palette gives disabled text the
   same colour as normal text, so an item that could not be clicked looked
   exactly like one that could ("Crop this photo…", task 46). */
QMenu::item:disabled {{ color: {text3}; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 5px 8px; }}

/* Small and quiet. A tooltip inherits the application font, which the
   zoom setting scales, so at 125% they were arriving at body size —
   larger than the label they were explaining. Fixed in px so they stay
   an aside whatever the interface is set to. */
QToolTip {{
    background: {bg2};
    color: {text2};
    border: 1px solid {line};
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 11px;
}}
""".format(**t)


class DialogAccent(QObject):
    """Paint the accent onto dialogs as they open.

    The main window is restyled directly, but a dialog is a separate
    top-level window built long after the application sheet was last
    written — so it would open wearing whatever color was current at
    startup. Catching its show is cheaper than repolishing everything on
    every artwork change.
    """

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

    def eventFilter(self, obj, event):
        from PyQt5.QtWidgets import QDialog
        if event.type() == QEvent.Show and isinstance(obj, QDialog):
            try:
                obj.setStyleSheet(self.sheet_for(obj))
            except Exception:                   # noqa: BLE001
                pass
        return False

    def sheet_for(self, dialog):
        """The dialog's own sheet, with the accent added after it.

        This used to replace the dialog's sheet with the accent rules,
        which threw away anything the dialog set for itself (Station
        settings styles its own tabs). The first sheet seen is kept and
        the overlay goes on the end each time.

        A solid background goes first, as a floor. A dialog parented
        inside a see-through panel inherits that panel's "background:
        transparent", and on Wayland that is a window you can see the
        desktop through (task 16). The dialog's own rules still win.
        """
        own = dialog.property("own_sheet")
        if own is None:
            own = dialog.styleSheet() or ""
            dialog.setProperty("own_sheet", own)
        floor = f"QDialog {{ background: {T['bg0']}; }}\n"
        return floor + own + "\n" + self.controller.overlay()


class AppStyle(QProxyStyle):
    """Qt's default tooltip delay is 700ms, which on a bar of small icons
    means one appears every time the pointer crosses the row on its way
    somewhere else. Long enough to require intent, short enough to still
    be an answer when you want one."""

    WAKE_UP = 1100          # ms hovering before a tooltip appears
    FALL_ASLEEP = 200       # ms before moving on counts as a new hover

    def styleHint(self, hint, option=None, widget=None, data=None):
        if hint == QStyle.SH_ToolTip_WakeUpDelay:
            return self.WAKE_UP
        if hint == QStyle.SH_ToolTip_FallAsleepDelay:
            return self.FALL_ASLEEP
        return super().styleHint(hint, option, widget, data)


# --- live accent ----------------------------------------------------------

def _parse_kde_colour(value):
    """"r,g,b" (what Plasma writes) or "#rrggbb" -> QColor, else None."""
    value = (value or "").strip()
    if value.startswith("#"):
        colour = QColor(value)
        return colour if colour.isValid() else None
    parts = value.split(",")
    if len(parts) >= 3:
        try:
            rgb = [max(0, min(255, int(p.strip()))) for p in parts[:3]]
        except ValueError:
            return None
        return QColor(*rgb)
    return None


def kde_accent():
    """KDE's accent colour, or None when there isn't one to read.

    Read straight from kdeglobals rather than through a Qt palette role:
    Plasma exposes it as General/AccentColor in "r,g,b", and the palette
    only reflects it once a theme has been rebuilt around it (and this
    app replaces the palette with its own anyway).

    With "use the colour scheme's accent" chosen in System Settings there
    is no AccentColor line. The scheme's own accent is then its selection
    colour, and failing that the focus decoration colour.
    """
    import os
    path = os.path.join(
        os.environ.get("XDG_CONFIG_HOME")
        or os.path.join(os.path.expanduser("~"), ".config"), "kdeglobals")
    found = {}
    wanted = {("General", "AccentColor"),
              ("Colors:Selection", "BackgroundNormal"),
              ("Colors:Window", "DecorationFocus")}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            section = ""
            for line in handle:
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1]
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if (section, key) in wanted and (section, key) not in found:
                    colour = _parse_kde_colour(value)
                    if colour is not None:
                        found[(section, key)] = colour
    except Exception:                           # noqa: BLE001
        return None
    for place in (("General", "AccentColor"),
                  ("Colors:Selection", "BackgroundNormal"),
                  ("Colors:Window", "DecorationFocus")):
        if place in found:
            return found[place]
    return None


def desktop_accent(fallback=None):
    """KDE's accent color if it has one, else `fallback` (or the current
    accent)."""
    colour = kde_accent()
    if colour is not None:
        return colour
    return QColor(fallback or T["accent"])


class AccentController(QObject):
    """Keeps the whole app's accent tied to whatever artwork is on screen.

    Artwork changes are a cut, not a fade.

    The fade was thirteen restyles of the whole application in half a
    second, and setStyleSheet on the QApplication reparses the sheet and
    repolishes every widget -- around 36ms each with a full station list
    loaded. Batching it to the final step helped, but the intermediate
    repaints still competed with scrolling and input for the same thread,
    and half a second of that is plainly worse than an instant change
    nobody has time to notice.

    A station change on its own still does nothing -- the old color is
    held until real artwork actually arrives, so the UI never flashes back
    to a default between songs.
    """

    changed = pyqtSignal(QColor)

    MIN_SHIFT = 14          # ignore changes smaller than this, in RGB terms

    APPLY_DELAY = 60        # ms to coalesce a burst of artwork updates
    FULL_DELAY = 900        # ms before the whole window is repolished

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._app = app
        _ACTIVE[:] = [self]
        self._current = QColor(T["accent"])
        self._to = QColor(self._current)
        self._styled = self._current.name()
        self._source = "artwork"
        self._fixed = None
        self._target = None         # the widget to restyle, if not the app
        self._quick = []            # small subtrees that update immediately
        self._lazy = []             # pages, restyled when next shown
        self._pending_targets = []
        self._pending_overlay = ""
        self._lazy_stale = set()
        self._pending = None
        self._full_pending = None

        # The whole-window restyle is the expensive half, and nothing about
        # it is urgent once the visible surfaces already carry the colour.
        self._full_timer = QTimer(self)
        self._full_timer.setSingleShot(True)
        self._full_timer.setInterval(self.FULL_DELAY)
        self._full_timer.timeout.connect(self._apply_full)

        # Artwork resolution can settle on two or three images in quick
        # succession, and each one used to mean a full restyle. One timer
        # turns a burst into a single apply.
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(self.APPLY_DELAY)
        self._apply_timer.timeout.connect(self._apply_pending)

    def color(self) -> QColor:
        return QColor(self._current)

    def set_source(self, mode, fixed=None):
        """"artwork", "desktop" or "fixed".

        Artwork is the default because it is the only one that says
        anything — but a color that changes every three minutes is not
        for everyone, and a desktop that already has an accent should be
        allowed to win.
        """
        self._source = mode or "artwork"
        self._fixed = QColor(fixed) if fixed else None
        if self._source == "desktop":
            self.set_color(desktop_accent(), animate=False)
        elif self._source == "fixed" and self._fixed is not None:
            self.set_color(self._fixed, animate=False)

    def from_artwork(self, source, animate=True):
        # Ignored unless the accent is meant to follow the art.
        if getattr(self, "_source", "artwork") != "artwork":
            return
        """Pass a QPixmap or QImage. Nulls are ignored on purpose."""
        if source is None:
            return
        if isinstance(source, QPixmap):
            if source.isNull():
                return
            source = source.toImage()
        if source.isNull():
            return
        colour = dominant_color(source, fallback=None)
        if colour is None:
            self.from_desktop(animate)
            return
        self.set_color(colour, animate)

    def from_desktop(self, animate=True):
        """The art has no colour of its own (white, grey, black): use the
        desktop's accent instead of amber (task 31). Without a KDE accent
        the app's default amber stays the answer."""
        if getattr(self, "_source", "artwork") != "artwork":
            return
        self.set_color(desktop_accent(DEFAULT_ACCENT), animate)

    FADE_MS = 1000              # how long a colour change takes
    FADE_STEP = 33              # ms between frames, about 30fps
    SHEET_EVERY = 5             # frames between stylesheet rewrites

    def set_color(self, colour: QColor, animate=True):
        """Travel to the new color rather than jumping to it.

        A second, at thirty frames, so the interface changes color the
        way a room does when a light is swapped rather than the way a
        slide does. Instant is still available for the cases that need it
        — choosing a fixed color in Settings, or matching the desktop —
        because those are answers to a question the user just asked and
        should not appear to lag behind them.
        """
        colour = readable_accent(QColor(colour))
        if self._distance(colour, self._to) < self.MIN_SHIFT:
            return
        self._to = colour
        if not animate:
            self._stop_fade()
            self._commit(colour)
            return

        self._from = QColor(self._current)
        self._fade_frame = 0
        if getattr(self, "_fade_timer", None) is None:
            self._fade_timer = QTimer(self)
            self._fade_timer.setInterval(self.FADE_STEP)
            self._fade_timer.timeout.connect(self._fade_tick)
        self._fade_timer.start()

    def _stop_fade(self):
        timer = getattr(self, "_fade_timer", None)
        if timer is not None:
            timer.stop()

    def _fade_tick(self):
        frames = max(1, self.FADE_MS // self.FADE_STEP)
        self._fade_frame += 1
        done = self._fade_frame >= frames
        t = 1.0 if done else self._fade_frame / frames
        # Ease out: most of the travel early, settling at the end. A
        # linear fade reads as a wipe.
        eased = 1.0 - (1.0 - t) ** 3
        step = _blend(self._from, self._to, eased)

        # The stylesheet is the expensive part — about eleven
        # milliseconds — so it is rewritten a few times across the second
        # rather than thirty. The painted widgets read the token directly
        # and repaint every frame, which is what the eye is following.
        self._commit(step, restyle=done
                     or self._fade_frame % self.SHEET_EVERY == 0,
                     final=done)
        if done:
            self._fade_timer.stop()

    def reset(self):
        """Back to the app default -- for when nothing is playing at all."""
        self.set_color(QColor(T["accent"]))

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _distance(a: QColor, b: QColor) -> int:
        return (abs(a.red() - b.red()) + abs(a.green() - b.green())
                + abs(a.blue() - b.blue()))

    def set_quick_targets(self, widgets):
        """Widgets to restyle immediately with the accent-only sheet.

        Keep this list to what is on screen while music plays. Restyling
        costs time in proportion to the widgets underneath, and a page
        nobody is looking at can be brought up to date when they look at
        it — see set_lazy_targets.
        """
        self._quick = [w for w in widgets if w is not None]

    def set_lazy_targets(self, widgets):
        """Pages to restyle when they are next shown, not now."""
        self._lazy = [w for w in widgets if w is not None]
        self._lazy_stale = set()

    def set_visible_page(self, getter):
        """How to ask which page is on screen.

        A page that is being looked at when the colour changes has to be
        restyled now; marking it stale only helps the pages you cannot
        see. Refreshed at the end of a fade rather than on every frame,
        because a full page restyle costs real time.
        """
        self._visible_page = getter

    def refresh_lazy(self, widget):
        """Bring one page up to date, if the accent moved while it was
        hidden."""
        if widget is None or id(widget) not in getattr(self, "_lazy_stale",
                                                       set()):
            return
        self._lazy_stale.discard(id(widget))
        widget.setStyleSheet(accent_overlay(self._styled))

    def set_target(self, widget):
        """Restyle this widget instead of the whole application.

        setStyleSheet on the QApplication repolishes every widget in every
        top-level window -- including the skinned Winamp stack, which uses
        no stylesheet at all and gains nothing from being walked. Scoping
        it to the main window measured at half the cost, and the base sheet
        applied to the application at startup still covers anything
        parented elsewhere.
        """
        self._target = widget

    def _commit(self, colour: QColor, restyle=True, final=True):
        """Move the accent. The restyle itself is deferred and coalesced.

        Painted widgets read the token directly, so those update now and
        repaint on this frame; only the stylesheet waits.
        """
        self._current = colour
        name = colour.name()
        if name == self._styled:
            return
        T["accent"] = name
        T["on_accent"] = on_accent_for(colour)
        self._styled = name
        # Only the last frame of a fade says so.
        #
        # `restyle` is true on several frames — the sheet is rewritten a
        # few times across the second — so using it here still meant
        # doing the expensive work six times per fade instead of once.
        self.settled = bool(final)
        self.changed.emit(QColor(colour))

        # Mid-fade frames repaint but do not restyle; the sheet catches up
        # on the frames that ask for it and always on the last one.
        if not restyle:
            return
        self._pending = name
        self._apply_timer.start()

    def _apply_pending(self):
        name, self._pending = self._pending, None
        if name is None:
            return
        target = self._target
        if target is not None and not target.isVisible():
            # Hidden means the skinned player is up. Restyling a window
            # nobody can see is pure cost, so it waits for flush().
            self._pending = name
            return

        if self._quick:
            # Only the small subtrees, and never the whole window.
            #
            # A full repolish is around 26ms whenever it happens, and
            # deferring it only moved the stall rather than removing it —
            # a stutter 900ms after the artwork changed is still a
            # stutter. The surfaces that show the accent are all in this
            # list, and menus are given the sheet as they are built, so
            # nothing is left wearing the old colour.
            # One widget per turn of the event loop, not all three at once.
            #
            # Every image operation in an artwork change now totals about
            # a tenth of a millisecond; the whole of the remaining cost is
            # Qt re-parsing the sheet and repolishing, and no amount of
            # graphics acceleration touches that — it is the style engine
            # walking widgets on the CPU. What it is sensitive to is how
            # much lands in one frame. Split across frames, each step is
            # two to five milliseconds and nothing is ever late.
            self._pending_targets = [w for w in self._quick
                                     if w is not None and w.isVisibleTo(
                                         w.window())]
            self._pending_overlay = accent_overlay(name)
            self._restyle_next()
            # The rest are marked and left alone. Restyling every page on
            # every artwork change was 37ms of repolishing, most of it for
            # pages that were not on screen — which is the stall.
            self._lazy_stale = {id(w) for w in getattr(self, "_lazy", [])}
            # The page being looked at cannot wait to be revisited.
            #
            # This has to happen here rather than in set_color: the stale
            # set is filled on this deferred pass, so a refresh asked for
            # earlier found nothing marked and did nothing.
            getter = getattr(self, "_visible_page", None)
            if getter is not None:
                try:
                    self.refresh_lazy(getter())
                except Exception:               # noqa: BLE001
                    pass
        else:
            (target or self._app).setStyleSheet(stylesheet(name))

    def _restyle_next(self):
        """Restyle one widget, then hand the loop back before the next."""
        if not getattr(self, "_pending_targets", None):
            return
        widget = self._pending_targets.pop(0)
        try:
            widget.setStyleSheet(self._pending_overlay)
        except RuntimeError:
            pass                                # gone since it was queued
        if self._pending_targets:
            QTimer.singleShot(0, self._restyle_next)

    def _apply_full(self):
        name, self._full_pending = self._full_pending, None
        if name is None:
            return
        target = self._target
        if target is not None and not target.isVisible():
            self._full_pending = name
            return
        (target or self._app).setStyleSheet(stylesheet(name))
        # The overlay would otherwise sit on top of the full sheet and win
        # every specificity tie, which makes later changes look ignored.
        for widget in self._quick:
            widget.setStyleSheet("")

    def overlay(self):
        """The accent-only sheet, for a widget built after the last
        change — a menu, or a dialog."""
        return accent_overlay(self._styled)

    def flush(self):
        """Apply a deferred restyle — call when the target becomes visible
        again."""
        if self._pending is not None:
            self._apply_pending()
        elif self._full_pending is not None:
            self._apply_full()
